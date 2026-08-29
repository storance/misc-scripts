import argparse
import pathlib
import logging
import sys
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.live import Live

from . import ctr, ntr
from .progress import TrimProgressTracker
from .. import copy_file
from ..progress import ProgressWrapper

DEFAULT_CHUNK_SIZE = 64 * 1024


@dataclass
class TrimTask:
    input_file: pathlib.Path
    output_file: pathlib.Path


def configure_trim_parser(parser: argparse.ArgumentParser):
    parser.add_argument('-u', '--untrim',
                        action='store_true',
                        help='Untrimms the rom files instead')
    parser.add_argument('-o', '--output-directory',
                        type=pathlib.Path,
                        help='Output directory to write the trimmed/untrimmed files. Defaults to the same directory as input_path.')
    parser.add_argument('-t', '--threads',
                        default=3,
                        type=int,
                        help='Number of threads to spawn for trimming files in parallel.')
    parser.add_argument('-b', '--buffer-size',
                        type=int,
                        default=DEFAULT_CHUNK_SIZE,
                        help="How large of a buffer to use when copying files in kilobytes.")
    parser.add_argument('-r', '--recursive',
                        action="store_true",
                        help="Recursively search for rom files from the input_path if it's a directory.")

    parser.add_argument('input_path',
                        type=pathlib.Path,
                        help='Input file or directory. If a directory is provided, all .3ds, .cci, and .nds files will be processed.')

    parser.set_defaults(action=trim_roms, log_file='trim.log')


def trim_roms(console: Console, args: argparse.Namespace):
    progress_tracker = TrimProgressTracker(console, not args.untrim)

    with Live(progress_tracker.progress_group(), console=console):
        if not args.input_path.exists():
            logging.error("Input path \"%s\" does not exist.", args.input_path)
            sys.exit(1)

        if args.output_directory is not None:
            output_directory = args.output_directory

            if not output_directory.exists() or not output_directory.is_dir():
                logging.error("Output directory \"%s\" does not exist or is not directory.", output_directory)
                sys.exit(1)
        elif args.input_path.is_file():
            output_directory = args.input_path.parent
        else:
            output_directory = args.input_path

        try:
            files_to_trim = _scan_for_roms(progress_tracker, args.input_path,
                                           output_directory, args.recursive, not args.untrim)
        except Exception as e:
            progress_tracker.fail_scan()
            logging.error("Failed to scan \"%s\": %s",  args.input_path, str(e))
            sys.exit(1)

        if len(files_to_trim) == 0:
            logging.info("No 3DS or NDS roms found to trim.")
            return

        try:
            _trim_files(progress_tracker, files_to_trim,
                        not args.untrim, args.threads, args.buffer_size)
        except Exception as e:
            progress_tracker.fail_trim()
            logging.error("Failed to trim files: %s", str(e))
            sys.exit(1)
        progress_tracker.stop()


def _scan_for_roms(progress_tracker: TrimProgressTracker,
                   input_path: pathlib.Path,
                   output_directory: pathlib.Path,
                   recursive: bool,
                   trim: bool) -> list[TrimTask]:
    progress_tracker.start_scan()
    if input_path.is_file():
        progress_tracker.complete_scan()
        return [TrimTask(input_path, _get_output_file(output_directory, input_path, trim))]

    results = []
    glob_pattern = "**" if recursive else "*"
    for file in input_path.glob(glob_pattern):
        if file.suffix.casefold() in ['.3ds', 'cci']:
            if not ctr.is_valid_game(file):
                logging.debug("Skipping \"%s\" as it's not a valid 3ds game file.", file)
                continue

            if trim and not ctr.is_trim_needed(file):
                logging.debug("Skipping \"%s\" as it's already trimmed.", file)
            elif not trim and not ctr.is_untrim_needed(file):
                logging.debug("Skipping \"%s\" as it's already untrimmed.", file)
            else:
                output_file = _get_output_file(output_directory, file, trim)
                if not output_file.exists():
                    results.append(TrimTask(file, output_file))
                else:
                    logging.debug("Skipping \"%s\" as the output file already exists.", file)
        elif file.suffix.casefold() == '.nds':
            if not ntr.is_valid_game(file):
                logging.debug("Skipping \"%s\" as it's not a valid nds game file.", file)
                continue

            if trim and not ntr.is_trim_needed(file):
                logging.debug("Skipping \"%s\" as it's already trimmed.", file)
            elif not trim and not ntr.is_untrim_needed(file):
                logging.debug("Skipping \"%s\" as it's already untrimmed.", file)
            else:
                output_file = _get_output_file(output_directory, file, trim)
                if not output_file.exists():
                    results.append(TrimTask(file, output_file))
                else:
                    logging.debug("Skipping \"%s\" as the output file already exists.", file)
        else:
            logging.debug("Skipping \"%s\" as it's not a 3ds or nds rom file.", file)

    progress_tracker.complete_scan()

    return results


def _get_output_file(output_directory: pathlib.Path, input_file: pathlib.Path, trim: bool) -> pathlib.Path:
    if trim:
        return output_directory / (input_file.with_suffix(".trimmed" + input_file.suffix)).name
    else:
        all_suffixes = ''.join(input_file.suffixes)
        return output_directory / (input_file.name.removesuffix(all_suffixes) + input_file.suffix)


def _trim_files(progress_tracker: TrimProgressTracker,
                files: list[TrimTask],
                trim: bool,
                thread_count: int,
                buffer_size: int):
    progress_tracker.start_trim(len(files))

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures = []
        for task in files:
            file_size = task.input_file.stat().st_size
            copy_progress = progress_tracker.add_copy_file_task(task.input_file, file_size)

            futures.append(executor.submit(_trim_rom_file,
                                           copy_progress,
                                           task.input_file,
                                           task.output_file,
                                           trim,
                                           buffer_size))

        for future in as_completed(futures):
            future.result()
            progress_tracker.advance_trim()

    progress_tracker.stop_trim()


def _trim_rom_file(copy_progress: ProgressWrapper,
                   input_file: pathlib.Path,
                   output_file: pathlib.Path,
                   trim: bool,
                   buffer_size: int):
    if not copy_file(copy_progress, input_file, output_file, buffer_size):
        return

    try:
        if input_file.suffix.casefold() in ['.3ds', '.cci']:
            if trim:
                logging.info("Trimming \"%s\".", output_file)
                ctr.trim_file(output_file)
            else:
                logging.info("Untrimming \"%s\".", output_file)
                ctr.untrim_file(output_file)
        elif input_file.suffix.casefold() == '.nds':
            if trim:
                logging.info("Trimming \"%s\".", output_file)
                ntr.trim_file(output_file)
            else:
                logging.info("Untrimming \"%s\".", output_file)
                ntr.untrim_file(output_file)
    except Exception as e:
        logging.error("Failed to %s \"%s\": %s", "trim" if trim else "untrim", input_file, str(e))
