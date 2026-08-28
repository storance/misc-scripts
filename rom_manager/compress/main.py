import argparse
import os
import io
import sys
import pathlib
import subprocess
import logging
from typing import Any
from enum import StrEnum
from rich.console import Console
from rich.live import Live

from .progress import CompressProgressTracker


class CompressionFormat(StrEnum):
    CHD = 'chd'
    CSO = 'cso'
    RVZ = 'rvz'
    CISO = 'ciso'
    DECISO = 'deciso'
    WUX = 'wux'
    WBFS = 'wbfs'


class CHDFormat(StrEnum):
    CD = 'createcd'
    DVD = 'createdvd'


IS_WINDOWS = os.name == 'nt'
DEFAULT_CHDMAN_PATH = 'chdman.exe' if IS_WINDOWS else 'chdman'
DEFAULT_NKIT_PATH = 'nkit.exe' if IS_WINDOWS else 'nkit'
CHUNK_SIZE = 64 * 1024


def configure_compress_parser(parser: argparse.ArgumentParser):
    parser.add_argument('--chdman-path',
                        type=pathlib.Path,
                        default=DEFAULT_CHDMAN_PATH,
                        help='Path to the chdman executable')
    parser.add_argument('--nkit-path',
                        type=pathlib.Path,
                        default=DEFAULT_NKIT_PATH,
                        help='Path to the nkit V2 cli executable')
    parser.add_argument('-f', '--format',
                        choices=list(CompressionFormat),
                        type=CompressionFormat,
                        default=CompressionFormat.CHD,
                        help='Output format for compressed files.  Valid values are %(choices)s')
    parser.add_argument('-o', '--output-directory',
                        type=pathlib.Path,
                        help='Output directory for compressed files. Defaults to input directory.')
    parser.add_argument('-c', '--force-cd',
                        action="store_true",
                        help="Forces creating a cd based chd file.  Ignored when the format is not chd.")
    parser.add_argument('-k', '--keys',
                        type=pathlib.Path,
                        help='Path to WiiU or PS3 keys zip file if converting one of those systems roms.')
    parser.add_argument('input_directory',
                        type=pathlib.Path,
                        help='Directory containing ISO and BIN/CUE files to compress')
    parser.set_defaults(action=compress_roms, log_file='compress.log')


def compress_roms(console: Console, args: argparse.Namespace):
    progress_tracker = CompressProgressTracker(console)

    with Live(progress_tracker.progress_group(), console=console):
        if not args.input_directory.exists() or not args.input_directory.is_dir():
            logging.error("Input directory \"%s\" does not exist or is not a directory.", args.input_directory)
            sys.exit(1)

        if args.output_directory is None:
            output_directory = args.input_directory
        else:
            output_directory = args.output_directory

            if not output_directory.exists():
                logging.error("Output directory \"%s\" does not exist or is not a directory.", args.input_directory)
                sys.exit(1)

        files_to_compress = _scan_files(progress_tracker, args.input_directory, output_directory, args.format)
        if not files_to_compress:
            logging.info("No ROM files found that need to be compressed.")
            return

        progress_tracker.compress_overall_progress.start(visible=True, total=len(files_to_compress))

        for file in files_to_compress:
            if args.format == CompressionFormat.CHD:
                run_chdman(args.chdman_path, file, output_directory, args.force_cd)
            else:
                run_nkit(args.nkit_path, file, output_directory, args.format, args.keys)

            progress_tracker.compress_overall_progress.advance()

        progress_tracker.compress_overall_progress.stop()
        progress_tracker.stop()


def _scan_files(progress_tracker: CompressProgressTracker,
                input_directory: pathlib.Path,
                output_directory: pathlib.Path,
                format: CompressionFormat) -> list[pathlib.Path]:
    progress_tracker.scan_overall_progress.start(visible=True)
    results = []
    for child in input_directory.iterdir():
        if not child.is_file() or child.name[0] == '.':
            continue

        if not _is_supported_extension(child, format):
            logging.debug("Skipping \"%s\" since it's not a supported extension for format %s.", child, format)
            continue

        _append_if_needed(child, output_directory, format, results)

    progress_tracker.scan_overall_progress.advance()
    progress_tracker.scan_overall_progress.stop()

    return results


def _is_supported_extension(path: pathlib.Path, format: CompressionFormat) -> bool:
    suffix = path.suffix.casefold()

    if format == CompressionFormat.CHD:
        return suffix in ['.cue', '.gdi', '.iso']
    elif format == CompressionFormat.WUX:
        return suffix == '.wud'
    else:
        return suffix == '.iso'


def _append_if_needed(file: pathlib.Path,
                      output_directory: pathlib.Path,
                      format: CompressionFormat,
                      results: list[pathlib.Path]):
    output_file = _get_output_filename(file, output_directory, format)
    if not output_file.exists():
        results.append(file)
    else:
        logging.debug("Skipping \"%s\" because a compressed version already exists.", file)


def _get_output_filename(file: pathlib.Path, output_directory: pathlib.Path, format: CompressionFormat) -> pathlib.Path:
    if format == CompressionFormat.CHD:
        return output_directory / file.with_suffix('.chd').name
    elif format == CompressionFormat.RVZ:
        return output_directory / file.with_suffix('.rvz').name
    elif format == CompressionFormat.CSO:
        return output_directory / file.with_suffix('.cso').name
    elif format == CompressionFormat.CISO:
        return output_directory / file.with_suffix('.ciso').name
    elif format == CompressionFormat.DECISO:
        return output_directory / file.name
    elif format == CompressionFormat.WBFS:
        return output_directory / file.with_suffix('.wbfs').name
    elif format == CompressionFormat.WUX:
        return output_directory / file.with_suffix('.wux').name
    else:
        raise ValueError(f"Unknown compression format {format}")


def run_chdman(chdman_path: pathlib.Path,
               input_file: pathlib.Path,
               output_directory: pathlib.Path,
               force_cd: bool):
    output_file = _get_output_filename(input_file, output_directory, CompressionFormat.CHD)
    logging.info("Compressing \"%s\" to \"%s\".", input_file, output_file)
    if force_cd or input_file.suffix.casefold() in ['.bin', '.cue']:
        format = 'createcd'
    else:
        format = 'createdvd'
    args = [str(chdman_path), format, '-i', str(input_file), '-o', str(output_file)]
    execute_process(input_file, args, True)


def run_nkit(nkit_path: pathlib.Path,
             input_file: pathlib.Path,
             output_directory: pathlib.Path,
             format: CompressionFormat,
             keys: pathlib.Path | None):
    output_file = _get_output_filename(input_file, output_directory, CompressionFormat.CSO)

    logging.info("Compressing \"%s\" to \"%s\".", input_file, output_file)
    args = [str(nkit_path), '-task', 'convert', '-in', str(input_file),
            '-out', str(output_directory), '-convert', str(format)]
    if keys is not None:
        args.extend(['-keys', str(keys)])
    execute_process(input_file, args, True)


def execute_process(input_file: pathlib.Path, args: list[Any], check: bool = True, input: str | None = None):
    logging.debug("Executing command: %s", ' '.join(args))
    result = subprocess.run(args,
                            text=True,
                            input=input,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)

    if check and result.returncode != 0:
        logging.error("Failed to compress \"%s\".  Check log file for error details.", input_file)
        logging.error("Command output: %s", result.stdout)
    else:
        logging.debug("Command output: %s", result.stdout)
