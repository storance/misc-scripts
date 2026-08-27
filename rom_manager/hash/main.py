import argparse
import pathlib
from rich.console import Console

def configure_hash_parser(parser: argparse.ArgumentParser):
    parser.add_argument('-r', '--recursive',
                        action="store_true",
                        help="Recursively hash files in sub directories.")
    parser.add_argument('-e', '--ext',
                        required=True,
                        nargs="+",
                        help="Filter to only include the specified file extension.")
    parser.add_argument('-o', '--overwrite',
                        action="store_true",
                        help="Overwrite any existing .sha1 file.")
    parser.add_argument('input_directory',
                        type=pathlib.Path,
                        help='Directory to scan and create sha1 hashes.')
    parser.set_defaults(action=hash_roms, log_file='hash.log')

def hash_roms(console: Console, args: argparse.Namespace):
    pass
    