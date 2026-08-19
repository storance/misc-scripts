#! /usr/bin/env python3

import argparse
import os
import io
import sys
import pathlib
import subprocess
from typing import IO, Any
from enum import StrEnum


class CompressionFormat(StrEnum):
    CHD = 'chd'
    CHD_CD = 'chd-cd'
    CSO = 'cso'
    NKIT = 'nkit'
    RVZ = 'rvz'


class CHDFormat(StrEnum):
    CD = 'createcd'
    DVD = 'createdvd'


IS_WINDOWS = os.name == 'nt'
DEFAULT_CHDMAN_PATH = 'chdman.exe' if IS_WINDOWS else 'chdman'
DEFAULT_DOLPHIN_TOOL_PATH = 'DolphinTool.exe' if IS_WINDOWS else 'dolphin-tool'
DEFAULT_MAXCSO_PATH = 'maxcso.exe' if IS_WINDOWS else 'maxcso'


def main():

    parser = argparse.ArgumentParser(description='Compress iso or bin/cue files to various formats.',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
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
    parser.add_argument('--dotnet-path',
                        type=pathlib.Path,
                        default='dotnet',
                        help='Path to the dotnet executable.  Only used for running the NKit tools on unix based systems.')
    parser.add_argument('-f', '--format',
                        choices=list(CompressionFormat),
                        type=CompressionFormat,
                        default=CompressionFormat.CHD,
                        help='Output format for compressed files.  Valid values are %(choices)s')
    parser.add_argument('-l', '--tool-log',
                        default='tool.log',
                        help='Log file where the output of the conversion tools will be logged')
    parser.add_argument('-o', '--output-directory',
                        help='Output directory for compressed files. Defaults to input directory.')
    parser.add_argument('input_directory', help='Directory containing ISO and BIN/CUE files to compress')
    args = parser.parse_args()

    input_directory = pathlib.Path(args.input_directory)

    if not input_directory.exists() or not input_directory.is_dir():
        print(f"Error: {args.input_directory} is not a valid directory.", file=sys.stderr)
        return 1

    if args.output_directory is None:
        output_directory = input_directory
    else:
        output_directory = pathlib.Path(args.output_directory)

        if not output_directory.exists():
            print(f"Error: {args.output_directory} is not a valid directory.", file=sys.stderr)
            return 1

    with open(args.tools_log, 'w') as log_file:
        for child in input_directory.iterdir():
            if not child.is_file():
                continue

            if args.format in [CompressionFormat.CHD, CompressionFormat.CHD_CD] and is_cd_based_image(child):
                run_chdman(args.chdman_path, child, output_directory, log_file, CHDFormat.CD)
            elif args.format == CompressionFormat.CHD and is_regular_iso(child):
                run_chdman(args.chdman_path, child, output_directory, log_file, CHDFormat.DVD)
            elif args.format == CompressionFormat.CHD_CD and is_regular_iso(child):
                run_chdman(args.chdman_path, child, output_directory, log_file, CHDFormat.CD)
            elif args.format == CompressionFormat.CSO and is_regular_iso(child):
                run_maxcso(args.maxcso_path, child, output_directory, log_file)
            elif args.format == CompressionFormat.NKIT and is_regular_iso(child):
                run_converttonkit(args.nkit_path, args.dotnet_path, child, output_directory, log_file)
            elif args.format == CompressionFormat.RVZ and is_regular_iso(child):
                run_dolphintool(args.dolphintool_path, child, output_directory, log_file)

    return 0


def is_cd_based_image(path: pathlib.Path) -> bool:
    return path.suffix.casefold() in ['.cue', '.gdi']


def is_regular_iso(path: pathlib.Path) -> bool:
    return path.suffix.casefold() == '.iso' and not path.name.casefold().endswith('.nkit.iso')


def run_chdman(chdman_path: pathlib.Path,
               input_file: pathlib.Path,
               output_directory: pathlib.Path,
               log_file: IO[Any],
               format: CHDFormat = CHDFormat.DVD):
    output_file = output_directory / (input_file.with_suffix('.chd').name)
    if output_file.exists():
        print(f"Skipping {output_file.name}: chd output file already exists.")
        return

    try:
        print(f"Compressing {input_file.name} to {output_file}...")
        args = [str(chdman_path), str(format), '-i', str(input_file), '-o', str(output_file)]
        print(f"Running: {' '.join(args)}", file=log_file)
        subprocess.run(args,
                       check=True,
                       stdout=log_file,
                       stderr=log_file)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {input_file.name}. Check log file for error details.", file=sys.stderr)


def run_maxcso(maxcso_path: pathlib.Path,
               iso_path: pathlib.Path,
               output_directory: pathlib.Path,
               log_file: IO[Any]):
    output_file = output_directory / (iso_path.with_suffix('.cso').name)
    if output_file.exists():
        print(f"Skipping {output_file.name}: cso output file already exists.")
        return

    try:
        print(f"Compressing {iso_path.name} to {output_file}...")
        args = [str(maxcso_path), str(iso_path), '-o', str(output_file)]
        print(f"Running: {' '.join(args)}", file=log_file)
        subprocess.run(args,
                       check=True,
                       stdout=log_file,
                       stderr=log_file)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {iso_path.name}: {e}", file=sys.stderr)


def run_converttonkit(nkit_path: pathlib.Path,
                      dotnet_path: pathlib.Path,
                      iso_path: pathlib.Path,
                      output_directory: pathlib.Path,
                      log_file: IO[Any]):
    output_name = iso_path.with_suffix('.nkit.iso').name
    output_file = output_directory / output_name
    if output_file.exists():
        print(f"Skipping {output_file.name}: nkit output file already exists.")
        return

    try:
        print(f"Compressing {iso_path.name} to {output_file}...")
        if IS_WINDOWS:
            args = [str(nkit_path / 'ConvertToNKit.exe'), str(iso_path)]
        else:
            args = [str(dotnet_path), str(nkit_path / 'NKit.dll'), 'ConvertToNKit', str(iso_path)]

        print(f"Running: {' '.join(args)}", file=log_file)
        subprocess.run(args,
                       input='\n',
                       text=True,
                       check=False,
                       stdout=log_file,
                       stderr=log_file)

        wii_path = pathlib.Path(nkit_path).parent / 'Processed' / 'Wii' / output_file.name
        if wii_path.exists():
            wii_path.rename(output_file)

        gc_path = pathlib.Path(nkit_path).parent / 'Processed' / 'GameCube' / output_file.name
        if gc_path.exists():
            gc_path.rename(output_file)

        print(f"Error: Failed to find the output file for {iso_path.name} after nkit compression.", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {iso_path.name}. Check log file for error details.", file=sys.stderr)


def run_dolphintool(dolphintool_path: pathlib.Path,
                    iso_path: pathlib.Path,
                    output_directory: pathlib.Path,
                    log_file: IO[Any]):
    output_file = output_directory / (iso_path.with_suffix('.rvz').name)
    if output_file.exists():
        print(f"Skipping {output_file.name}: rvz output file already exists.")
        return

    try:
        print(f"Compressing {iso_path.name} to {output_file}...")
        args = [str(dolphintool_path), 'convert', '-i', str(iso_path), '-o',
                str(output_file), '-f', 'rvz', '-b', '131072', '-c', 'zstd', '-l', '5']
        print(f"Running: {' '.join(args)}", file=log_file)
        subprocess.run(args,
                       check=True,
                       stdout=log_file,
                       stderr=log_file)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to compress {iso_path.name}. Check log file for error details.", file=sys.stderr)


if __name__ == '__main__':
    sys.exit(main())
