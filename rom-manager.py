#! /usr/bin/env python3

import argparse
import logging
from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme
from rich.highlighter import RegexHighlighter

from rom_manager.sync import configure_sync_parser
from rom_manager.rename import configure_rename_parser
from rom_manager.hash import configure_hash_parser



class HashHighlighter(RegexHighlighter):
    base_style = "log."
    highlights = [
        r"(?P<sha1>[a-z0-9]{40})",
        r"(?P<quoted>\"[^\"]+\")"
    ]


def main():
    parser = argparse.ArgumentParser(description="Useful utilities for managing a ROM collection.")
    logging_group = parser.add_argument_group(title='Logging Settings')
    logging_group.add_argument('-l', '--log-file',
                                help='File to output the logs to.')
    logging_group.add_argument('-v', '--verbose',
                                action="store_true",
                                help="Enables verbose logging which includes additional details about why or why not actions were taken.")


    subparsers = parser.add_subparsers()

    sync_parser = subparsers.add_parser('sync', help='Synchronize ROMs between a source and destination location')
    configure_sync_parser(sync_parser)

    rename_parser = subparsers.add_parser('rename', help='Renames ROM files to match the name from a no-intro or redump dat file.')
    configure_rename_parser(rename_parser)

    hash_parser = subparsers.add_parser('hash', help='Creates cached .sha1 files for ROMs in a given directory')
    configure_hash_parser(hash_parser)

    args = parser.parse_args()

    theme = Theme({
        'log.sha1': 'orange1',
        'log.quoted': 'magenta'
    })
    console = Console(log_path=False, theme=theme)

    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt='%Y-%m-%d %H:%M:%S')
    file_handler = logging.FileHandler(args.log_file, 'w')
    file_handler.setFormatter(file_formatter)

    

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(message)s',
        handlers = [
            file_handler,
            RichHandler(level=logging.WARNING, console=console, show_path=False, highlighter=HashHighlighter())
        ]
    )

    args.action(console, args)

if __name__ == '__main__':
    main()