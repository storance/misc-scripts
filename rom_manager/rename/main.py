import sys
import argparse
import pathlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.live import Live

from .dat import load_dat_files
from .progress import RenameProgressTracker
from .tasks import CueFile, SyncFolder, build_rename_tasks
from .. import sha1_hash_file, list_bin_files_from_cue


def configure_rename_parser(parser: argparse.ArgumentParser):
    parser.add_argument('-d', '--dat-file',
                        required=True,
                        nargs="+",
                        type=pathlib.Path,
                        help='Location of the dat files containing rom hashes and filenames.')
    parser.add_argument('-e', '--extension',
                        required=True,
                        nargs="+",
                        help='Extension of the rom files too look at.  For example: iso, chd, cue.  For bin/cue files use cue as the extension.')
    parser.add_argument('-s', '--sync-folder',
                        default=[],
                        nargs=2,
                        metavar=('PATH', 'EXT'),
                        action='append',
                        help='Syncs the filenames in the folder with the given extension. This is useful when renaming .iso files and you want to keep the .chd file in sync, for example.')
    parser.add_argument('-D', '--dry-run',
                        action="store_true",
                        help="Run in dry-run mode.")
    parser.add_argument('-r', '--recursive',
                        action='store_true',
                        help='Recursively search sub-directories for rom files to rename.')
    parser.add_argument('-t', '--threads',
                        type=int,
                        default=3,
                        help='Number of threads to use to hash files in parallel.')
    parser.add_argument('input_directory',
                        help='Input directory containing the roms to rename.')
    parser.set_defaults(action=rename_roms, log_file='rename.log')


def rename_roms(console: Console, args: argparse.Namespace):
    progress_tracker = RenameProgressTracker(console)

    with Live(progress_tracker.progress_group(), console=console):
        input_directory = pathlib.Path(args.input_directory)
        if not input_directory.exists() or not input_directory.is_dir():
            logging.error("Input directory path \"%s\" does not exist or is not a directory.", input_directory)
            sys.exit(1)

        games_by_hash = load_dat_files(args.dat_file)

        sync_folders = [SyncFolder(pathlib.Path(path), _normalize_ext(ext)) for path, ext in args.sync_folder]
        for sync_folder in sync_folders:
            if sync_folder.ext == '.cue':
                logging.error("Error: Syncing cue files is not supported.")
                sys.exit(1)

        file_suffixes = [_normalize_ext(ext) for ext in args.extension]
        try:
            scan_result = _scan_for_roms(progress_tracker, input_directory, args.recursive, file_suffixes)
        except Exception as e:
            progress_tracker.fail_scan()
            logging.error("Failed to scan \"%s\" input path: %s", input_directory, str(e))
            sys.exit(1)

        files_to_hash = []
        for rom_file in scan_result:
            if isinstance(rom_file, pathlib.Path):
                files_to_hash.append(rom_file)
            elif isinstance(rom_file, CueFile):
                files_to_hash.extend(rom_file.bin_files)

        if len(files_to_hash) == 0:
            logging.info("No rom files found.")
            return

        try:
            hashes_by_path = _hash_files(progress_tracker, args.threads, files_to_hash)
        except Exception as e:
            progress_tracker.fail_hash()
            logging.error("Failed to hash files: %s", str(e))
            sys.exit(1)
        tasks = build_rename_tasks(input_directory, scan_result, games_by_hash, hashes_by_path, sync_folders)

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

def _normalize_ext(ext: str) -> str:
    ext = ext.casefold()
    return ext if ext[0] == '.' else f".{ext}"


def _scan_for_roms(progress_tracker: RenameProgressTracker,
                   input_directory: pathlib.Path,
                   recursive: bool,
                   extensions: list[str]) -> list[pathlib.Path | CueFile]:

    progress_tracker.start_scan()

    bin_files = []
    bin_files_from_cue = set()

    results = []
    glob_pattern = "**" if recursive else "*"
    for file in input_directory.glob(glob_pattern):
        file_name = file.name.casefold()

        if not any(file_name.endswith(ext) for ext in extensions):
            logging.debug("Skipping \"%s\" since it doesn't end with any of the specified extensions.", file)
            continue

        # throw bin files in a separate list for now to make sure they are not part of a cue file
        # Some systems use .bin as their rom extension that is different from a bin/cue file
        if file_name.endswith('.bin') and '.cue' in extensions:
            bin_files.append(file)
            continue

        if file_name.endswith('.cue'):
            bin_files = [file.parent / name for name in list_bin_files_from_cue(file)]
            logging.debug("Scan found cue file \"%s\" with %d bin files.", file, len(bin_files))
            bin_files_from_cue.update(bin_files)
            results.append(CueFile(file, bin_files))
        else:
            logging.debug("Scan found rom file \"%s\".", file)
            results.append(file)

    for bin_file in bin_files:
        if bin_file not in bin_files_from_cue:
            logging.debug("Scan found bin file \"%s\" not referenced by a cue file.", bin_file)
            results.append(bin_file)
        else:
            logging.debug("Skipping bin file \"%s\" since it was referenced by a cue file.", bin_file)

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
