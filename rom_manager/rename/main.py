import sys
import argparse
import pathlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.live import Live

from .dat import load_dat_files
from .progress import RenameProgressTracker
from .tasks import build_rename_tasks
from .common import CueFile, RenameTarget, TargetRomSet
from .. import Metadata, ParseError, sha1_hash_file, list_bin_files_from_cue


def configure_rename_parser(parser: argparse.ArgumentParser):
    parser.add_argument('-d', '--dat-file',
                        required=True,
                        nargs="+",
                        type=pathlib.Path,
                        help='Location of the dat files containing rom hashes and filenames.')
    parser.add_argument('-s', '--sync-group',
                        action="store_true",
                        help='Synchronizes the rename across all rom sets in the same group.')
    parser.add_argument('-D', '--dry-run',
                        action="store_true",
                        help="Run in dry-run mode.")
    parser.add_argument('-t', '--threads',
                        type=int,
                        default=3,
                        help='Number of threads to use to hash files in parallel.')
    parser.add_argument('-r', '--rom-sets', nargs="+", required=True, help='The name of the rom sets to rename.')
    parser.add_argument('input_directory',
                        type=pathlib.Path,
                        help='The directory where the roms exist. ' +
                        'Expects a metadata.yml file to exist in this directory that defines the rom folder layout.')
    parser.set_defaults(action=rename_roms, log_file='rename.log')


def rename_roms(console: Console, args: argparse.Namespace):
    progress_tracker = RenameProgressTracker(console)

    with Live(progress_tracker.progress_group(), console=console):
        if not args.input_directory.exists() or not args.input_directory.is_dir():
            logging.error("Input directory path \"%s\" does not exist or is not a directory.", args.input_directory)
            sys.exit(1)

        metadata_file = args.input_directory / "metadata.yml"
        if not metadata_file.exists():
            logging.error("metadata.yml does not exist in \"%s\".", args.input_directory)
            sys.exit(1)

        games_by_hash = load_dat_files(args.dat_file)

        try:
            metadata = Metadata.load_from_file(metadata_file)
        except ParseError as e:
            logging.error("%s\n  in %s", e, e.location)
            sys.exit(1)
        except Exception as e:
            logging.error("Failed to read \"%s\": %s", metadata_file, str(e))
            sys.exit(1)

        rom_sets = _get_target_rom_sets(args.rom_sets, metadata, args.sync_group)

        try:
            scan_result = _scan_for_roms(progress_tracker, args.input_directory, rom_sets)
        except Exception as e:
            progress_tracker.fail_scan()
            logging.error("Failed to scan \"%s\" input path: %s", args.input_directory, str(e))
            sys.exit(1)

        files_to_hash = []
        for rom_file in scan_result:
            if rom_file.is_single_file():
                files_to_hash.append(rom_file.as_single_file())
            elif rom_file.is_cue_file():
                cue_file = rom_file.as_cue_file()
                files_to_hash.extend(cue_file.bin_files)

        if len(files_to_hash) == 0:
            logging.info("No rom files found.")
            return

        try:
            hashes_by_path = _hash_files(progress_tracker, args.threads, files_to_hash)
        except Exception as e:
            progress_tracker.fail_hash()
            logging.error("Failed to hash files: %s", str(e))
            sys.exit(1)
        tasks = build_rename_tasks(args.input_directory, scan_result, games_by_hash, hashes_by_path)

        if not tasks:
            logging.info("No rom files found to rename.")
            return

        if args.dry_run:
            for task in tasks:
                task.dry_run()
        else:
            try:
                progress_tracker.start_rename(len(tasks))
                for task in tasks:
                    task.execute()
                    progress_tracker.advance_rename()
                progress_tracker.stop_rename()
            except Exception as e:
                progress_tracker.fail_rename()
                logging.error("Failed to rename files: %s", str(e))
                sys.exit(1)

        progress_tracker.stop()


def _get_target_rom_sets(rom_set_names: list[str], metadata: Metadata, sync_groups: bool) -> list[TargetRomSet]:
    target_rom_sets = []
    for rom_set_name in rom_set_names:
        rom_set = metadata.find_rom_set(rom_set_name)
        if rom_set is None:
            logging.error("Rom set \"%s\" not found in metadata.yml.", rom_set_name)
            sys.exit(1)

        sync_rom_sets = []
        if sync_groups and rom_set.group is not None:
            sync_rom_sets = [rs for rs in metadata.find_group(rom_set.group) if rs != rom_set]

        target_rom_sets.append(TargetRomSet(rom_set, sync_rom_sets))

    return target_rom_sets


def _scan_for_roms(progress_tracker: RenameProgressTracker,
                   input_directory: pathlib.Path,
                   roms_sets: list[TargetRomSet]) -> list[RenameTarget]:

    progress_tracker.start_scan()

    results = []
    for rom_set in roms_sets:
        glob_pattern = "**" if rom_set.primary_rom_set.recursive else "*"
        scan_dir = input_directory / rom_set.primary_rom_set.path
        for file in scan_dir.glob(glob_pattern):
            relative_path = file.relative_to(input_directory)
            file_name = file.name.casefold()

            if not rom_set.is_included(relative_path):
                logging.debug("Skipping file \"%s\" as it does not end with a desired extension.", file)
                continue

            if rom_set.is_excluded(relative_path):
                logging.debug("Skipping file \"%s\" as it matches the exclude pattern of the rom set.", file)
                continue

            sync_files = rom_set.get_files_to_sync(input_directory, file)
            for sync_file in sync_files:
                logging.debug("Found file \"%s\" to sync rename with \"%s\".", sync_file, file)

            if file_name.endswith('.cue'):
                bin_files = [file.parent / name for name in list_bin_files_from_cue(file)]
                logging.debug("Scan found cue file \"%s\" with %d bin files.", file, len(bin_files))
                results.append(RenameTarget(CueFile(file, bin_files), sync_files))
            else:
                logging.debug("Scan found rom file \"%s\".", file)
                results.append(RenameTarget(file, sync_files))

    progress_tracker.complete_scan()

    return results


def _hash_files(progress_tracker: RenameProgressTracker,
                thread_count: int,
                files: list[pathlib.Path]) -> dict[pathlib.Path, str]:
    progress_tracker.start_hash(len(files))

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures_to_path = {}
        for file in files:
            file_progress = progress_tracker.add_hash_file_task(file, file.stat().st_size)
            future = executor.submit(sha1_hash_file, file, file_progress)
            futures_to_path[future] = file

        hashes = {}
        for future in as_completed(futures_to_path):
            progress_tracker.hash_overall_progress.advance()
            file = futures_to_path[future]
            sha1 = future.result()

            hashes[file] = sha1

    progress_tracker.stop_hash()
    return hashes
