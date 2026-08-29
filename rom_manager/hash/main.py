import sys
import argparse
import pathlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.live import Live

from .progress import HashProgressTracker
from .. import sha1_hash_file


def configure_hash_parser(parser: argparse.ArgumentParser):
    parser.add_argument('-r', '--recursive',
                        action="store_true",
                        help="Recursively hash files in sub directories.")
    parser.add_argument('-e', '--extension',
                        required=True,
                        nargs="+",
                        help="Filter to only include the specified file extension.")
    parser.add_argument('-o', '--overwrite',
                        action="store_true",
                        help="Overwrite any existing .sha1 file.")
    parser.add_argument('-t', '--thread',
                        type=int,
                        default=3,
                        help="Number of threads to use for hashing files.")
    parser.add_argument('input_directory',
                        type=pathlib.Path,
                        help='Directory to scan and create sha1 hashes.')
    parser.set_defaults(action=hash_roms, log_file='hash.log')


def hash_roms(console: Console, args: argparse.Namespace):
    progress_tracker = HashProgressTracker(console)
    
    with Live(progress_tracker.progress_group(), console=console):
        input_directory = pathlib.Path(args.input_directory)
        if not input_directory.exists() or not input_directory.is_dir():
            logging.error("Input directory path \"%s\" does not exist or is not a directory.", input_directory)
            sys.exit(1)

        extensions = [_normalize_ext(ext) for ext in args.extension]
        try:
            rom_files = _scan_for_roms(progress_tracker, input_directory, args.recursive, extensions)
        except Exception as e:
            progress_tracker.fail_scan()
            logging.error("Failed to scan \"%s\" input path: %s", input_directory, str(e))
            sys.exit(1)

        if len(rom_files) == 0:
            logging.info("No rom files found.")
            return

        try:
            _hash_files(progress_tracker, args.threads, args.overwrite, rom_files)
        except Exception as e:
            progress_tracker.fail_hash()
            logging.error("Failed to hash files: %s", str(e))
            sys.exit(1)
        progress_tracker.stop()

def _normalize_ext(ext: str) -> str:
    ext = ext.casefold()
    return ext if ext[0] == '.' else f".{ext}"

def _scan_for_roms(progress_tracker: HashProgressTracker,
                   input_directory: pathlib.Path,
                   recursive: bool,
                   extensions: list[str]) -> list[pathlib.Path]:

    progress_tracker.start_scan()

    results = []
    glob_pattern = "**" if recursive else "*"
    for file in input_directory.glob(glob_pattern):
        file_name = file.name.casefold()

        if not any(file_name.endswith(ext) for ext in extensions):
            logging.debug("Skipping \"%s\" since it doesn't end with any of the specified extensions.", file)
            continue

        logging.debug("Scan found rom file \"%s\".", file)
        results.append(file)
    
    progress_tracker.complete_scan()

    return results

def _hash_files(progress_tracker: HashProgressTracker,
                thread_count: int,
                overwrite: bool,
                files: list[pathlib.Path]):
    progress_tracker.start_hash(len(files))

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = []
        for file in files:
            file_progress = progress_tracker.add_hash_file_task(file, file.stat().st_size)
            futures.append(executor.submit(sha1_hash_file, file, file_progress, force_regenerate=overwrite))

        for future in as_completed(futures):
            future.result()
            progress_tracker.advance_hash()

    progress_tracker.stop_hash()