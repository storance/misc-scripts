import argparse
import os
import sys
import pathlib
import subprocess
import logging
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any
from enum import StrEnum
from rich.console import Console
from rich.live import Live

from .progress import CompressProgressTracker
from .. import Metadata, RomSet, ParseError, replace_suffix


class CompressionFormat(StrEnum):
    CHD = 'chd'
    CSO = 'cso'
    RVZ = 'rvz'
    CISO = 'ciso'
    WUX = 'wux'
    WBFS = 'wbfs'

    def supported_exts(self) -> list[str]:
        if self == CompressionFormat.CHD:
            return ['.cue', '.gdi', '.iso']
        elif self == CompressionFormat.WUX:
            return ['.wud']
        else:
            return ['.iso']

    def output_ext(self) -> str:
        if self == CompressionFormat.CHD:
            return '.chd'
        elif self == CompressionFormat.WUX:
            return '.wux'
        elif self == CompressionFormat.RVZ:
            return '.rvz'
        elif self == CompressionFormat.CSO:
            return '.cso'
        elif self == CompressionFormat.CISO:
            return '.ciso'
        elif self == CompressionFormat.WBFS:
            return '.wbfs'
        else:
            raise ValueError(f"Unknown compression format {self}")


@dataclass
class RomSetPair:
    input_rom_set: RomSet
    output_rom_set: RomSet


@dataclass
class CompressPair:
    input_file: pathlib.Path
    output_file: pathlib.Path


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
    parser.add_argument('-r', '--rom-set',
                        required=True,
                        nargs=2,
                        action='append',
                        metavar=('INPUT_ROM_SET', 'OUTPUT_ROM_SET'),
                        help='Pairs of input and output rom sets to compress. Roms will be scanned from the input rom set then compressed and stored in the output rom set.')
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

        metadata_file = args.input_directory / "metadata.yml"
        if not metadata_file.exists():
            logging.error("metadata.yml does not exist in \"%s\".", args.input_directory)
            sys.exit(1)

        try:
            metadata = Metadata.load_from_file(metadata_file)
        except ParseError as e:
            logging.error("%s\n  in %s", e, e.location)
            sys.exit(1)
        except Exception as e:
            logging.error("Failed to read \"%s\": %s", metadata_file, str(e))
            sys.exit(1)

        rom_set_pairs = _get_rom_set_pairs(args.rom_set, metadata, args.format)

        try:
            files_to_compress = _scan_files(progress_tracker, args.input_directory, rom_set_pairs, args.format)
        except Exception as e:
            progress_tracker.fail_scan()
            logging.error("Failed to scan \"%s\": %s",  args.input_directory, str(e))
            sys.exit(1)

        if not files_to_compress:
            logging.info("No ROM files found that need to be compressed.")
            return

        try:
            _compress_files(progress_tracker,
                            files_to_compress,
                            args.chdman_path,
                            args.nkit_path,
                            args.format)
        except Exception as e:
            progress_tracker.fail_scan()
            logging.error("Failed to compress files: %s", str(e))
            sys.exit(1)

        progress_tracker.stop()


def _get_rom_set_pairs(rom_sets: list[tuple[str, str]], metadata: Metadata, format: CompressionFormat) -> list[RomSetPair]:
    rom_set_pairs = []
    for input_rom_set_name, output_rom_set_name in rom_sets:
        input_rom_set = metadata.find_rom_set(input_rom_set_name)
        if input_rom_set is None:
            logging.error("Input rom set \"%s\" does not exist in metadata.yml.", input_rom_set_name)
            sys.exit(1)

        output_rom_set = metadata.find_rom_set(output_rom_set_name)
        if output_rom_set is None:
            logging.error("Output rom set \"%s\" does not exist in metadata.yml.", output_rom_set_name)
            sys.exit(1)

        if not any(ext in format.supported_exts() for ext in input_rom_set.extensions):
            logging.error("Input rom set \"%s\" does not contain supported extensions for format %s.",
                          input_rom_set_name, format)
            sys.exit(1)
        if format.output_ext() not in output_rom_set.extensions:
            logging.error("Output rom set \"%s\" does not contain the output extension %s for format %s.",
                          output_rom_set_name, format.output_ext(), format)
            sys.exit(1)

        rom_set_pairs.append(RomSetPair(input_rom_set, output_rom_set))
    return rom_set_pairs


def _scan_files(progress_tracker: CompressProgressTracker,
                input_directory: pathlib.Path,
                rom_set_pairs: list[RomSetPair],
                format: CompressionFormat) -> list[CompressPair]:
    progress_tracker.start_scan()
    results = []
    for rom_set_pair in rom_set_pairs:
        glob_pattern = "**" if rom_set_pair.input_rom_set.recursive else "*"

        scan_dir = input_directory / rom_set_pair.input_rom_set.path
        for file in scan_dir.glob(glob_pattern):
            if not file.is_file() or file.name[0] == '.':
                continue

            if not _is_supported_extension(file, format):
                logging.debug("Skipping \"%s\" since it's not a supported extension for format %s.", file, format)
                continue

            output_file = _get_output_filename(file, input_directory, rom_set_pair, format)
            if not output_file.exists():
                results.append(CompressPair(file, output_file))
            else:
                logging.debug("Skipping \"%s\" because a compressed version already exists.", file)

    progress_tracker.complete_scan()

    return results


def _is_supported_extension(path: pathlib.Path, format: CompressionFormat) -> bool:
    return path.suffix.casefold() in format.supported_exts()


def _get_output_filename(file: pathlib.Path,
                         input_directory: pathlib.Path,
                         rom_set_pair: RomSetPair,
                         format: CompressionFormat) -> pathlib.Path:
    input_rom_set_path = input_directory / rom_set_pair.input_rom_set.path
    output_rom_set_path = input_directory / rom_set_pair.output_rom_set.path
    relative_path = file.relative_to(input_rom_set_path)

    return replace_suffix(output_rom_set_path / relative_path, format.output_ext())


def _compress_files(progress_tracker: CompressProgressTracker,
                    files_to_compress: list[CompressPair],
                    chdman_path: pathlib.Path,
                    nkit_path: pathlib.Path,
                    format: CompressionFormat):
    progress_tracker.start_compress(len(files_to_compress))

    for file in files_to_compress:
        if format == CompressionFormat.CHD:
            run_chdman(chdman_path, file)
        else:
            keys_zip = _create_keys_zip(format, file.input_file)
            try:
                run_nkit(nkit_path, file, format, keys_zip)
            finally:
                if keys_zip is not None and keys_zip.exists():
                    keys_zip.unlink()

        progress_tracker.compress_overall_progress.advance()

    progress_tracker.stop_compress()


def _create_keys_zip(format: CompressionFormat, input_file: pathlib.Path) -> pathlib.Path | None:
    if format == CompressionFormat.WUX:
        key_file = input_file.with_suffix('.key')
        (fd, path) = tempfile.mkstemp(suffix='.zip')
        logging.debug("Creating WiiU keys zip file at \"%s\" for key file \"%s\".", path, key_file)

        with os.fdopen(fd, 'wb') as tmp_file, zipfile.ZipFile(tmp_file, 'w') as keys_zip:
            keys_zip.write(key_file, arcname=key_file.name)
        return pathlib.Path(path)
    else:
        return None


def run_chdman(chdman_path: pathlib.Path,
               compress_file: CompressPair):
    logging.info("Compressing \"%s\" to \"%s\".", compress_file.input_file, compress_file.output_file)
    if compress_file.input_file.suffix.casefold() in ['.gdi', '.cue']:
        format = 'createcd'
    else:
        format = 'createdvd'
    args = [str(chdman_path), format, '-i', str(compress_file.input_file), '-o', str(compress_file.output_file)]
    _execute_process(compress_file.input_file, args, True)


def run_nkit(nkit_path: pathlib.Path,
             compress_file: CompressPair,
             format: CompressionFormat,
             keys: pathlib.Path | None):
    logging.info("Compressing \"%s\" to \"%s\".", compress_file.input_file, compress_file.output_file)
    args = [str(nkit_path), '-task', 'convert', '-in', str(compress_file.input_file),
            '-out', str(compress_file.output_file.parent), '-convert', str(format)]
    if keys is not None:
        args.extend(['-keys', str(keys)])
    _execute_process(compress_file.input_file, args, True)


def _execute_process(input_file: pathlib.Path, args: list[Any], check: bool = True, input: str | None = None):
    logging.debug("Executing command: %s", ' '.join(args))
    result = subprocess.run(args,
                            text=True,
                            input=input,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)

    if check and result.returncode != 0:
        logging.error("Failed to compress \"%s\".", input_file)
        logging.error("Command output: %s", result.stdout)
    else:
        logging.debug("Command output: %s", result.stdout)
