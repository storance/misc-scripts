import sys
import argparse
import pathlib
import logging
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.live import Live

from .dat import GameRomPair, load_dat_files
from .progress import VerifyProgressTracker
from .. import sha1_hash_file, list_bin_files_from_cue


@dataclass
class CueFile:
    cue_file: pathlib.Path
    bin_files: list[pathlib.Path]


def configure_verify_parser(parser: argparse.ArgumentParser):
    parser.add_argument('-d', '--dat-file',
                        required=True,
                        nargs="+",
                        type=pathlib.Path,
                        help='Location of the dat files containing rom hashes and filenames.')
    parser.add_argument('-e', '--extension',
                        required=True,
                        nargs="+",
                        help='Extension of the rom files too look at.  For example: iso, chd, cue.  For bin/cue files use cue as the extension.')
    parser.add_argument('-r', '--recursive',
                        action='store_true',
                        help='Recursively search sub-directories for rom files to rename.')
    parser.add_argument('-t', '--threads',
                        type=int,
                        default=3,
                        help='Number of threads to use to hash files in parallel.')
    parser.add_argument('input_directory',
                        help='Input directory containing the roms to rename.')
    parser.set_defaults(action=verify_roms, log_file='verify.log')


def verify_roms(console: Console, args: argparse.Namespace):
    progress_tracker = VerifyProgressTracker(console)

    with Live(progress_tracker.progress_group(), console=console):
        input_directory = pathlib.Path(args.input_directory)
        if not input_directory.exists() or not input_directory.is_dir():
            logging.error("Input directory path \"%s\" does not exist or is not a directory.", input_directory)
            sys.exit(1)

        games_by_hash = load_dat_files(args.dat_file)

        file_suffixes = [_normalize_ext(ext) for ext in args.extension]
        try:
            scan_result = _scan_for_roms(progress_tracker, input_directory, args.recursive, file_suffixes)
        except Exception as e:
            progress_tracker.fail_scan()
            logging.error("Failed to scan \"%s\": %s", input_directory, str(e))
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

        try:
            if not _verify_rom_files(progress_tracker, scan_result, games_by_hash, hashes_by_path):
                progress_tracker.fail_verify()
        except Exception as e:
            progress_tracker.fail_hash()
            logging.error("Failed to verify files: %s", str(e))
            sys.exit(1)

        progress_tracker.stop()


def _normalize_ext(ext: str) -> str:
    ext = ext.casefold()
    return ext if ext[0] == '.' else f".{ext}"


def _scan_for_roms(progress_tracker: VerifyProgressTracker,
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


def _hash_files(progress_tracker: VerifyProgressTracker,
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


def _verify_rom_files(progress_tracker: VerifyProgressTracker,
                      rom_files: list[pathlib.Path | CueFile],
                      games_by_hash: dict[str, list[GameRomPair]],
                      hashes_by_path: dict[pathlib.Path, str]) -> bool:
    progress_tracker.start_verify(len(rom_files))

    all_verified = True
    for rom_file in rom_files:
        if isinstance(rom_file, CueFile):
            all_verified = all_verified and _verify_cue_file(rom_file, games_by_hash, hashes_by_path)
        elif isinstance(rom_file, pathlib.Path):
            all_verified = all_verified and _verify_single_rim(rom_file, games_by_hash, hashes_by_path)

        progress_tracker.advance_verify()
    progress_tracker.stop_verify()

    return all_verified


def _verify_single_rim(file: pathlib.Path,
                       games_by_hash: dict[str, list[GameRomPair]],
                       hashes_by_path: dict[pathlib.Path, str]) -> bool:
    sha1 = hashes_by_path.get(file)

    if sha1 is None:
        logging.debug("Skipping \"%s\" as a sha1 was not calculated for it.", file)
        return True

    if sha1 not in games_by_hash:
        logging.error("File \"%s\" with sha1 hash %s does not match any game.", file, sha1)
        return False

    game_and_roms = games_by_hash[sha1]
    logging.debug("File \"%s\" with sha1 hash %s matches game %s", file, sha1, game_and_roms[0].game.name)
    return True


def _verify_cue_file(rom: CueFile,
                     games_by_hash: dict[str, list[GameRomPair]],
                     hashes_by_path: dict[pathlib.Path, str]) -> bool:
    bin_file_hashes = {
        bin_file: hashes_by_path.get(bin_file)
        for bin_file in rom.bin_files
    }

    game = None
    for bin_file, sha1 in bin_file_hashes.items():
        if sha1 is None:
            logging.debug("Skipping \"%s\" as a sha1 was not calculated for it.", bin_file)
            continue

        if sha1 not in games_by_hash:
            logging.error("File \"%s\" with sha1 hash %s does not match any game.", bin_file, sha1)
            return False

        games = games_by_hash[sha1]
        if len(games) == 1:
            game = games[0].game
            break
        elif game is None:
            game = games[0].game

    logging.debug("File \"%s\" with sha1 hash %s matches game %s.",
                 rom.cue_file, "unknown" if game is None else game.name)

    return True
