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
    CHD_CD = 'chd-cd'
    CSO = 'cso'
    NKIT = 'nkit'
    RVZ = 'rvz'
    N3DS_TRIM = '3ds-trim'


class CHDFormat(StrEnum):
    CD = 'createcd'
    DVD = 'createdvd'


IS_WINDOWS = os.name == 'nt'
DEFAULT_CHDMAN_PATH = 'chdman.exe' if IS_WINDOWS else 'chdman'
DEFAULT_DOLPHIN_TOOL_PATH = 'DolphinTool.exe' if IS_WINDOWS else 'dolphin-tool'
DEFAULT_MAXCSO_PATH = 'maxcso.exe' if IS_WINDOWS else 'maxcso'
DEFAULT_3DSTOOL_PATH = '3dstool.exe' if IS_WINDOWS else '3dstool'
CHUNK_SIZE = 1024 * 1024


def configure_compress_parser(parser: argparse.ArgumentParser):
    parser.add_argument('--chdman-path',
                        type=pathlib.Path,
                        default=DEFAULT_CHDMAN_PATH,
                        help='Path to the chdman executable')
    parser.add_argument('--maxcso-path',
                        type=pathlib.Path,
                        default=DEFAULT_MAXCSO_PATH,
                        help='Path to the maxcso executable')
    parser.add_argument('--dolphintool-path',
                        type=pathlib.Path,
                        default=DEFAULT_DOLPHIN_TOOL_PATH,
                        help='Path to the dolphin-tool executable')
    parser.add_argument('--nkit-path',
                        type=pathlib.Path,
                        default='NKit',
                        help='Path to the NKit tool directory.')
    parser.add_argument('--3dstool-path',
                        dest='n3dstool_path',
                        type=pathlib.Path,
                        default=DEFAULT_3DSTOOL_PATH,
                        help='Path to 3dstool executable.')
    parser.add_argument('--dotnet-path',
                        type=pathlib.Path,
                        default='dotnet',
                        help='Path to the dotnet executable.  Only used for running the NKit tools on unix based systems.')
    parser.add_argument('-f', '--format',
                        choices=list(CompressionFormat),
                        type=CompressionFormat,
                        default=CompressionFormat.CHD,
                        help='Output format for compressed files.  Valid values are %(choices)s')
    parser.add_argument('-o', '--output-directory',
                        type=pathlib.Path,
                        help='Output directory for compressed files. Defaults to input directory.')
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
            logging.info("No ROM files found that need to be compressed")

        progress_tracker.compress_overall_progress.start(visible=True, total=len(files_to_compress))

        for file in files_to_compress:
            if args.format in [CompressionFormat.CHD, CompressionFormat.CHD_CD] and _is_cd_based_image(file):
                run_chdman(args.chdman_path, file, output_directory, CHDFormat.CD)
            elif args.format == CompressionFormat.CHD and _is_regular_iso(file):
                run_chdman(args.chdman_path, file, output_directory, CHDFormat.DVD)
            elif args.format == CompressionFormat.CHD_CD and _is_regular_iso(file):
                run_chdman(args.chdman_path, file, output_directory, CHDFormat.CD)
            elif args.format == CompressionFormat.CSO and _is_regular_iso(file):
                run_maxcso(args.maxcso_path, file, output_directory)
            elif args.format == CompressionFormat.NKIT and _is_regular_iso(file):
                run_converttonkit(args.nkit_path, args.dotnet_path, file, output_directory)
            elif args.format == CompressionFormat.RVZ and _is_regular_iso(file):
                run_dolphintool(args.dolphintool_path, file, output_directory)
            elif args.format == CompressionFormat.N3DS_TRIM and _is_3ds_rom(file):
                run_3dstool(progress_tracker, args.n3dstool_path, file, output_directory)

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

        if format == CompressionFormat.N3DS_TRIM:
            if _is_3ds_rom(child):
                _append_if_needed(child, output_directory, format, results)
            else:
                logging.debug("Skipping \"%s\" because it is not a 3DS ROM.", child)
        elif _is_regular_iso(child):
            _append_if_needed(child, output_directory, format, results)
        elif _is_cd_based_image(child) and format in [CompressionFormat.CHD, CompressionFormat.CHD_CD]:
            _append_if_needed(child, output_directory, format, results)
        else:
            logging.debug("Skipping \"%s\" because it is not an iso, cue, or gdi file.", child)

    progress_tracker.scan_overall_progress.advance()
    progress_tracker.scan_overall_progress.stop()

    return results


def _is_cd_based_image(path: pathlib.Path) -> bool:
    return path.suffix.casefold() in ['.cue', '.gdi']


def _is_regular_iso(path: pathlib.Path) -> bool:
    return path.suffix.casefold() == '.iso' and not path.name.casefold().endswith('.nkit.iso')


def _is_3ds_rom(path: pathlib.Path) -> bool:
    name = path.name.casefold()

    return (name.endswith('.3ds') and not name.endswith('.trimmed.3ds')) or \
        (name.endswith('.cci') and not name.endswith('.trimmed.cci'))


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
    if format == CompressionFormat.CHD or format == CompressionFormat.CHD_CD:
        return output_directory / file.with_suffix('.chd').name
    elif format == CompressionFormat.RVZ:
        return output_directory / file.with_suffix('.cvz').name
    elif format == CompressionFormat.CSO:
        return output_directory / file.with_suffix('.cso').name
    elif format == CompressionFormat.NKIT:
        return output_directory / file.with_suffix('.nkit.iso').name
    elif format == CompressionFormat.N3DS_TRIM:
        return output_directory / file.with_suffix('.trimmed' + file.suffix).name
    else:
        raise ValueError(f"Unknown compression format {format}")


def run_chdman(chdman_path: pathlib.Path,
               input_file: pathlib.Path,
               output_directory: pathlib.Path,
               format: CHDFormat = CHDFormat.DVD):
    output_file = _get_output_filename(input_file, output_directory, CompressionFormat.CHD)
    logging.info("Compressing \"%s\" to \"%s\".", input_file, output_file)
    args = [str(chdman_path), str(format), '-i', str(input_file), '-o', str(output_file)]
    execute_process(input_file, args, True)


def run_maxcso(maxcso_path: pathlib.Path,
               input_file: pathlib.Path,
               output_directory: pathlib.Path):
    output_file = _get_output_filename(input_file, output_directory, CompressionFormat.CSO)

    logging.info("Compressing \"%s\" to \"%s\".", input_file, output_file)
    args = [str(maxcso_path), str(input_file), '-o', str(output_file)]
    execute_process(input_file, args, True)


def run_converttonkit(nkit_path: pathlib.Path,
                      dotnet_path: pathlib.Path,
                      input_file: pathlib.Path,
                      output_directory: pathlib.Path):
    output_file = _get_output_filename(input_file, output_directory, CompressionFormat.NKIT)

    logging.info("Compressing \"%s\" to \"%s\".", input_file, output_file)
    if IS_WINDOWS:
        args = [str(nkit_path / 'ConvertToNKit.exe'), str(input_file)]
    else:
        args = [str(dotnet_path), str(nkit_path / 'NKit.dll'), 'ConvertToNKit', str(input_file)]

    execute_process(input_file, args, False, '\n')
    wii_path = pathlib.Path(nkit_path).parent / 'Processed' / 'Wii' / output_file.name
    if wii_path.exists():
        wii_path.rename(output_file)

    gc_path = pathlib.Path(nkit_path).parent / 'Processed' / 'GameCube' / output_file.name
    if gc_path.exists():
        gc_path.rename(output_file)

    logging.error("Failed to find the output file for \"%s\" after nkit compression.", input_file)


def run_dolphintool(dolphintool_path: pathlib.Path,
                    input_file: pathlib.Path,
                    output_directory: pathlib.Path):

    output_file = _get_output_filename(input_file, output_directory, CompressionFormat.RVZ)
    logging.info("Compressing \"%s\" to \"%s\".", input_file, output_file)
    args = [str(dolphintool_path), 'convert', '-i', str(input_file), '-o',
            str(output_file), '-f', 'rvz', '-b', '131072', '-c', 'zstd', '-l', '5']
    execute_process(input_file, args, True)


def run_3dstool(
        progress_tracker: CompressProgressTracker,
        n3dstool_path: pathlib.Path,
        input_file: pathlib.Path,
        output_directory: pathlib.Path):
    output_file = _get_output_filename(input_file, output_directory, CompressionFormat.N3DS_TRIM)

    # 3dstool works in place, so we need to copy the original to the output dir first
    copy_file(progress_tracker, input_file, output_file)

    logging.info("Trimming \"%s\" to \"%s\".", input_file, output_file)
    args = [str(n3dstool_path), '--trim', '--verbose', '--file', str(output_file)]
    execute_process(input_file, args, True)


def copy_file(progress_tracker: CompressProgressTracker, src: pathlib.Path, dest: pathlib.Path):
    total_size = src.stat().st_size

    progress = progress_tracker.add_copy_task(src, total_size)
    progress.start(visible=True)

    logging.info("Copying \"%s\" to \"%s\".", src, dest)
    try:
        with open(src, 'rb') as fsrc, open(dest, 'wb') as fdest:
            while True:
                chunk = fsrc.read(CHUNK_SIZE)
                if not chunk:
                    break
                fdest.write(chunk)
                progress.advance(len(chunk))

        progress.stop(visible=False)
    except Exception as e:
        logging.error("Failed to copy file \"%s\" to \"%s\": {e}", src, dest, str(e))
        progress.update(failed=True)
        progress.stop()


def execute_process(input_file: pathlib.Path, args: list[Any], check: bool = True, input: str | None = None):
    logging.info("Executing command: %s", ' '.join(args))
    result = subprocess.run(args,
                            text=True,
                            input=input,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)

    logging.info("Command output: %s", result.stdout)
    if check and result.returncode != 0:
        logging.error("Failed to compress \"%s\".  Check log file for error details.", input_file)
