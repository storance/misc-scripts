import sys
import argparse
import pathlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.live import Live

from .. import Metadata, Profile, ParseError, copy_file, rename_file
from .progress import SyncProgressTracker
from .plan import Plan, create_plan
from .common import OverwriteCheck, DotFilesMode

__all__ = ['configure_sync_parser', 'sync_roms']


DEFAULT_CHUNK_SIZE = 64 * 1024
RANDOM_SUFFIX_LEN = 6


def configure_sync_parser(parser: argparse.ArgumentParser):
    profile_group = parser.add_mutually_exclusive_group(required=True)
    profile_group.add_argument('-p', '--profile',
                               help='Use the profile specified by the YAML file in the profiles directory in the source path.')
    profile_group.add_argument('-f', '--profile-path',
                               help='An explicit path to the profile YAML file.')

    parser.add_argument('-D', '--dry-run',
                        action='store_true',
                        help='Runs in dry run mode.')
    parser.add_argument('-o', '--overwrite-check',
                        choices=list(OverwriteCheck),
                        type=OverwriteCheck,
                        default=OverwriteCheck.SIZE,
                        help='Which check to use when determining if a destination file should be overwritten. Valid choices: %(choices)s')
    parser.add_argument('-t', '--threads',
                        default=3,
                        type=int,
                        help='Number of threads to spawn for copying and hashing files in parallel.')
    parser.add_argument('-d', '--delete',
                        action='store_true',
                        help="Delete files from the destination that are not present in the source.")
    parser.add_argument('-m', '--dot-files-mode',
                        choices=list(DotFilesMode),
                        metavar='MODE',
                        type=DotFilesMode,
                        default=DotFilesMode.IGNORE,
                        help='Control how dot files are handles. Valid choices: %(choices)s')
    parser.add_argument('-b', '--buffer-size',
                        type=int,
                        default=DEFAULT_CHUNK_SIZE,
                        help="How large of a buffer to use when copying files in kilobytes.")
    parser.add_argument('source',
                        help='Source directory where the roms exist. ' +
                        'Expects a metadata.yml file to exist in this directory that defines the rom folder layout.')
    parser.add_argument('destination',
                        help='Destination directory where the roms will be copied to.')
    parser.set_defaults(action=sync_roms, log_file='sync.log')


def sync_roms(console: Console, args: argparse.Namespace):
    progress_tracker = SyncProgressTracker(console)

    with Live(progress_tracker.progress_group(), console=console):
        source_path = pathlib.Path(args.source)
        if not source_path.exists() or not source_path.is_dir():
            logging.error("Source path \"%s\" does not exist or is not a directory.", source_path)
            sys.exit(1)

        metadata_file = source_path / "metadata.yml"
        if not metadata_file.exists():
            logging.error("metadata.yml does not exist in \"%s\".", source_path)
            sys.exit(1)

        dest_path = pathlib.Path(args.destination)
        if not dest_path.exists() or not dest_path.is_dir():
            logging.error("Destination path \"%s\" does not exist or is not a directory.", dest_path)
            sys.exit(1)

        if source_path.samefile(dest_path):
            logging.error("Source and destination paths can not be the same.")
            sys.exit(1)

        try:
            metadata = Metadata.load_from_file(metadata_file)
        except ParseError as e:
            logging.error("%s\n  in %s", e, e.location)
            sys.exit(1)
        except Exception as e:
            logging.error("Failed to read \"%s\": %s", metadata_file, str(e))
            sys.exit(1)

        if args.profile_path:
            profile_path = pathlib.Path(args.profile_path)
            if not profile_path.exists():
                logging.error("Profile path \"%s\" does not exist.", profile_path)
                sys.exit(1)
        else:
            profile_path = source_path / 'profiles' / pathlib.Path(args.profile).with_suffix('.yml')
            if not profile_path.exists():
                logging.error("Profile \"%s\" does not exist in \"%s/profiles\".", args.profile, source_path)
                sys.exit(1)

        try:
            profile = Profile.load_from_file(profile_path, metadata)
        except ParseError as e:
            logging.error("%s\n  in %s", e, e.location)
            sys.exit(1)
        except Exception as e:
            logging.error("Failed to read \"%s\": %s", profile_path, str(e))
            sys.exit(1)

        try:
            plan = create_plan(
                progress_tracker,
                source_path,
                dest_path,
                profile,
                args.dot_files_mode,
                args.overwrite_check,
                args.delete,
                args.threads
            )
        except Exception as e:
            progress_tracker.fail_plan()
            logging.error("Failed to plan sync for \"%s\": %s", source_path, str(e))
            sys.exit(1)

        if plan.empty():
            logging.info("Nothing to do. Exiting.")
        elif args.dry_run:
            _print_dry_run_plan(plan)
        else:
            _execute_plan(progress_tracker, plan, args.buffer_size, args.threads)

        progress_tracker.stop()


def _print_dry_run_plan(plan: Plan):
    for file in plan.delete_file_tasks:
        logging.info("DRY RUN: Deleting file \"%s\".", file)

    for dir in plan.delete_dir_tasks:
        logging.info("DRY RUN: Deleting directory \"%s\".", dir)

    for copy_task in plan.copy_tasks:
        logging.info("DRY RUN: Copying \"%s\" to \"%s\".", copy_task.src, copy_task.dst)

    for rename_task in plan.rename_tasks:
        logging.info("DRY RUN: Renaming \"%s\" to \"%s\".", rename_task.src, rename_task.dst)


def _execute_plan(progress_tracker: SyncProgressTracker,
                  plan: Plan,
                  buffer_size: int,
                  thread_count: int):
    total_to_delete = len(plan.delete_dir_tasks) + len(plan.delete_file_tasks)

    progress_tracker.execute_plan(total_to_delete, len(plan.rename_tasks), len(plan.copy_tasks))

    if plan.delete_dir_tasks or plan.delete_file_tasks:
        progress_tracker.start_delete()
        try:
            for file in plan.delete_file_tasks:
                logging.info("Deleting file \"%s\".", file)
                try:
                    file.unlink()
                except OSError as e:
                    logging.error("Failed to delete file \"%s\": %s.", file, str(e))
                progress_tracker.advance_delete()

            for dir in plan.delete_dir_tasks:
                logging.info("Deleting directory \"%s\".", dir)
                try:
                    dir.rmdir()
                except OSError as e:
                    logging.error("Failed to delete directory \"%s\": %s.", dir, str(e))
                progress_tracker.advance_delete()
        except Exception as e:
            progress_tracker.fail_delete()
            logging.error("Failed to delete files: %s", str(e))
            sys.exit(1)

        progress_tracker.stop_delete()

    if plan.rename_tasks:
        progress_tracker.start_rename()
        try:
            for rename_task in plan.rename_tasks:
                logging.info("Renaming \"%s\" to \"%s\".", rename_task.src, rename_task.dst)
                try:
                    rename_file(rename_task.src, rename_task.dst)
                except OSError as e:
                    logging.error("Failed to rename file \"%s\": %s.", rename_task.src, str(e))
                progress_tracker.advance_rename()
        except Exception as e:
            progress_tracker.fail_delete()
            logging.error("Failed to rename files: %s", str(e))
            sys.exit(1)
        progress_tracker.stop_rename()

    if plan.copy_tasks:
        progress_tracker.start_copy()
        try:
            with ThreadPoolExecutor(max_workers=thread_count) as executor:
                futures = []

                for copy_task in plan.copy_tasks:
                    file_size = copy_task.src.stat().st_size
                    file_progress = progress_tracker.add_copy_file_task(copy_task.src, file_size)
                    futures.append(executor.submit(copy_file, file_progress, copy_task.src, copy_task.dst, buffer_size))

                for future in as_completed(futures):
                    future.result()
                    progress_tracker.advance_copy()
        except Exception as e:
            progress_tracker.fail_delete()
            logging.error("Failed to copy files: %s", str(e))
            sys.exit(1)
        progress_tracker.stop_copy()
