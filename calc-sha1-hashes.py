#! /usr/bin/env python3

import hashlib
import pathlib
import argparse
import sys
from tqdm import tqdm

CHUNK_SIZE = 64 * 1024
SHA1_EXT = '.sha1'


def main():
    parser = argparse.ArgumentParser(description='Calculates the sha1 hashes of all files in a directory.')
    parser.add_argument('-r', '--recursive', action="store_true", help="Recursively hash files in sub directories.")
    parser.add_argument('-e', '--ext', action="append", default=[],
                        help="Filter to only include the specified file extension.")
    parser.add_argument('-o', '--overwrite', action="store_true", help="Overwrite any existing .sha1 file.")
    parser.add_argument('input_directory', type=pathlib.Path, help='Directory to scan and create sha1 hashes.')

    args = parser.parse_args()

    glob_pattern = "**" if args.recursive else "*"

    if not args.input_directory.exists():
        print(f"Input directory \"{args.input_directory}\" does not exist.", file=sys.stderr)
        sys.exit(1)

    if not args.input_directory.is_dir():
        print(f"Input directory \"{args.input_directory}\" is not a directory.", file=sys.stderr)
        sys.exit(1)

    exts = [normalize_ext(ext) for ext in args.ext]

    for file in args.input_directory.glob(glob_pattern):
        if not file.is_file():
            continue

        if file.suffix == SHA1_EXT or file.name[0] == '.':
            continue

        if len(exts) > 0 and not any(file.name.endswith(ext) for ext in exts):
            continue

        hash_file(file, args.overwrite)


def hash_file(file: pathlib.Path, overwrite: bool):
    sha1_file = file.with_name(file.name + SHA1_EXT)
    if sha1_file.exists() and not overwrite:
        tqdm.write(f"SHA1 hash file exists for \"{file}\". Skipping...")
        return

    total_size = file.stat().st_size

    with tqdm(total=total_size,
              unit='B',
              unit_scale=True,
              unit_divisor=1024,
              desc=f"-> Progress",
              dynamic_ncols=True,
              leave=False) as pbar:
        tqdm.write(f"Hashing \"{file}\"...")
        try:
            digest = hashlib.sha1()
            with open(file, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
                    pbar.update(len(chunk))

            sha1 = digest.hexdigest().casefold()
            with open(sha1_file, 'w') as f:
                f.write(sha1)

            tqdm.write(f"\tSHA1: {sha1}")
        except Exception as e:
            tqdm.write(f"\tError: {e}")


def normalize_ext(ext: str) -> str:
    return ext if ext[0] == '.' else f".{ext}"


if __name__ == '__main__':
    main()
