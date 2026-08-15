#! /usr/bin/env python3

import argparse
import pathlib
import sys
import os
import shutil
import humanfriendly
import traceback
from enum import StrEnum
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from dataclasses import dataclass
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from roms import ParseError, Location, Metadata, Profile, ProfileRomFolderConfig, normalize_unicode

DEBUG = False


class DotFilesMode(StrEnum):
    IGNORE = "ignore"
    DELETE_FROM_DEST = "delete-from-dest"
    COPY_FROM_SRC = "copy-from-src"
    BOTH = "both"

    def should_delete(self):
        return self == DotFilesMode.BOTH or self == DotFilesMode.DELETE_FROM_DEST

    def should_copy(self):
        return self == DotFilesMode.BOTH or self == DotFilesMode.COPY_FROM_SRC


@dataclass(frozen=True)
class CopyTask:
    source: pathlib.Path
    source_stat: os.stat_result
    dest: pathlib.Path
    dest_stat: os.stat_result | None

    def __str__(self):
        return f"Copying {self.source.name} -> {self.dest.parent}"

    @property
    def source_size(self) -> int:
        return self.source_stat.st_size

    @property
    def dest_size(self) -> int:
        return 0 if self.dest_stat is None else self.dest_stat.st_size

    def is_copy_required(self) -> bool:
        if not self.dest.exists() or self.dest_stat is None:
            return True

        return self.dest_stat.st_size != self.source_stat.st_size \
            or self.dest_stat.st_mtime < self.source_stat.st_mtime


def main():
    parser = argparse.ArgumentParser(
        description='Sync ROMs between directories')
    parser.add_argument('-o', '--overwrite',
                        action='store_true',
                        help='Force overwrite existing files at the destination.')
    parser.add_argument('-c', '--parallel-copies',
                        default=5,
                        type=int,
                        help='Number file copies to perform in parallel.')
    parser.add_argument('-D', '--dry-run',
                        action='store_true',
                        help='Runs in dry run mode. No copies or deletes will take place and a summary of ' +
                             'the planned actions will be printed.')
    parser.add_argument('--ignore-disk-space-check',
                        action='store_true',
                        help="Ignores the disk-space check and forces the copy to happen.")
    parser.add_argument('-d', '--sync-delete',
                        action='store_true',
                        help="Delete files from the destination that are not in the source.")
    parser.add_argument('-m', '--dot-files-mode',
                        choices=list(DotFilesMode),
                        metavar='MODE',
                        type=DotFilesMode,
                        default=DotFilesMode.IGNORE,
                        help='Control how dot files are handles. Valid choices: %(choices)s. Default: ignore')
    parser.add_argument('source',
                        help='Source directory where the roms exist. ' +
                             'Expects a metadata.yml file to exist in this directory that defines the rom folder layout.')
    parser.add_argument('destination',
                        help='Destination directory where the roms will be copied to.')

    profile_group = parser.add_mutually_exclusive_group(required=True)
    profile_group.add_argument('-p', '--profile',
                               help='Use the profile specified by the YAML file in the profiles directory in the source path.')
    profile_group.add_argument('-f', '--profile-path',
                               help='An explicit path to the profile YAML file.')

    args = parser.parse_args()

    source_path = pathlib.Path(args.source)
    if not source_path.exists() or not source_path.is_dir():
        print(
            f"Error: Source path '{source_path}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)

    metadata_file = source_path / "metadata.yml"
    if not metadata_file.exists():
        print(
            f"Error: Metadata file '{metadata_file}' does not exist in the source directory.", file=sys.stderr)
        sys.exit(1)

    dest_path = pathlib.Path(args.destination)
    if not dest_path.exists() or not dest_path.is_dir():
        print(
            f"Error: Destination path '{dest_path}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)

    if source_path.samefile(dest_path):
        print(f"Error: Source and destination paths can not be the same.",
              file=sys.stderr)
        sys.exit(1)

    try:
        metadata = read_metadata(metadata_file)
    except ParseError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"  in {e.location}", file=sys.stderr)
        sys.exit(1)
    except YAMLError as e:
        print(f"Error {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        if DEBUG:
            traceback.print_exc()
        sys.exit(1)

    if args.profile_path:
        profile_path = pathlib.Path(args.profile_path)
    else:
        profile_path = source_path / 'profiles' / pathlib.Path(args.profile).with_suffix('.yml')

    try:
        profile = read_profile(profile_path, metadata)
    except ParseError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(f"  in {e.location}", file=sys.stderr)
        sys.exit(1)
    except YAMLError as e:
        print(f"Error {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        if DEBUG:
            traceback.print_exc()
        sys.exit(1)

    sync_roms(source_path,
              dest_path,
              profile,
              args.parallel_copies,
              args.overwrite,
              args.sync_delete,
              args.dot_files_mode,
              args.dry_run,
              args.ignore_disk_space_check)


def read_metadata(metadata_path: pathlib.Path) -> Metadata:
    if not metadata_path.exists():
        raise ValueError(f"Metadata file '{metadata_path}' does not exist.")

    yaml = YAML(typ='rt')
    data = yaml.load(metadata_path)
    location = Location(None, metadata_path, data.lc.line+1)
    return Metadata.from_yaml(data, location)


def read_profile(profile_path: pathlib.Path, metadata: Metadata) -> Profile:
    if not profile_path.exists():
        raise ValueError(f"Profile '{profile_path}' does not exist.")

    yaml = YAML(typ='rt')
    data = yaml.load(profile_path)
    location = Location(None, profile_path, data.lc.line+1)

    return Profile.from_yaml(data, location, metadata)


def sync_roms(source_path: pathlib.Path,
              dest_path: pathlib.Path,
              profile: Profile,
              threads: int,
              overwrite: bool,
              sync_delete: bool,
              dot_files_mode: DotFilesMode,
              dry_run: bool,
              ignore_disk_space_check: bool):
    copy_tasks = scan_source_files(source_path, dest_path, profile.rom_folders, dot_files_mode)
    (files_to_delete, dirs_to_delete) = scan_for_files_to_delete(
        profile, copy_tasks, sync_delete, dot_files_mode)

    # filter out copy tasks where the destination file exists, is the same size,
    # and the source file was not modified since the destination was last copied.
    # The overwrite flag will override this behavior.
    to_copy = set(
        task for task in copy_tasks if overwrite or task.is_copy_required())

    total_size = sum(task.source_size for task in to_copy)
    extra_space = total_size \
        - sum(task.dest_size for task in to_copy) \
        - sum(file.stat().st_size for file in files_to_delete)

    if not ignore_disk_space_check:
        check_disk_space(dest_path, extra_space)

    if dry_run:
        print_dry_run_summary(
            files_to_delete, dirs_to_delete, to_copy, extra_space)
    else:
        run_sync_delete(files_to_delete, dirs_to_delete)
        run_copy_sync(to_copy, threads, total_size)


def scan_source_files(source_path: pathlib.Path,
                      dest_path: pathlib.Path,
                      profile_rom_folders: list[ProfileRomFolderConfig],
                      dot_files_mode: DotFilesMode) -> set[CopyTask]:
    copy_tasks = set()
    for rom_folder_config in profile_rom_folders:
        scan_dir = source_path / rom_folder_config.rom_folder.path
        if not scan_dir.exists() or not scan_dir.is_dir():
            print(
                f"Warning: Source rom folder '{scan_dir}' does not exist or is not a directory. Skipping.")
            continue

        for glob_pattern in rom_folder_config.rom_folder.includes:
            for file in scan_dir.glob(glob_pattern):
                if not dot_files_mode.should_copy() and file.name.startswith("."):
                    continue

                normalized_file = normalize_path(file)
                if not normalized_file.is_file() or not is_interested_rom(normalized_file, rom_folder_config):
                    continue

                stat_result = file.stat()

                if rom_folder_config.flatten or rom_folder_config.folder_per_game:
                    relative_path = normalized_file.name
                else:
                    relative_path = normalized_file.relative_to(scan_dir)

                if rom_folder_config.folder_per_game.enabled:
                    folder_name = rom_folder_config.folder_per_game.extract_game_name(
                        normalized_file)
                    dest = rom_folder_config.resolve_destination(dest_path) / folder_name / relative_path
                else:
                    dest = rom_folder_config.resolve_destination(dest_path) / relative_path

                dest_stat = None
                if dest.exists():
                    dest_stat = dest.stat()

                copy_tasks.add(
                    CopyTask(normalized_file, stat_result, dest, dest_stat))

    return copy_tasks


def normalize_path(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(normalize_unicode(str(path)))


def scan_for_files_to_delete(profile: Profile,
                             copy_tasks: set[CopyTask],
                             sync_delete: bool,
                             dot_files_mode: DotFilesMode) -> tuple[set[pathlib.Path], set[pathlib.Path]]:
    if not sync_delete:
        return (set(), set())

    expected_dst_paths = set(
        task.dest for task in copy_tasks if task.dest.exists())
    scanned_dests = set()
    files_to_delete = set()
    dirs_to_delete = set()

    for include in profile.rom_folders:
        if include.destination in scanned_dests:
            continue

        if not include.destination.exists():
            continue

        nesting_level = 0
        for root, dirnames, filenames in include.destination.walk():

            for name in filenames:
                file = root / name

                if not file.is_file():
                    continue

                # ignore hidden files
                if not dot_files_mode.should_delete() and file.name.startswith('.'):
                    continue

                normalized_file = normalize_path(file)

                if any(exclude.matches(normalized_file) for exclude in profile.delete_excludes):
                    continue

                if normalized_file not in expected_dst_paths:
                    files_to_delete.add(normalized_file)
                    if nesting_level >= 1:
                        dirs_to_delete.add(normalized_file.parent)

            if not include.folder_per_game or nesting_level >= 1:
                dirnames.clear()
            elif include.folder_per_game and nesting_level == 0:
                # find empty top-level directories
                for dir in dirnames:
                    dir_path = root / dir
                    if not any(dir_path.iterdir()):
                        dirs_to_delete.add(dir_path)

            nesting_level += 1

        scanned_dests.add(include.destination)

    return (files_to_delete, dirs_to_delete)


def is_interested_rom(file: pathlib.Path, rom_folder_config: ProfileRomFolderConfig) -> bool:
    if any(file.match(exclude) for exclude in rom_folder_config.rom_folder.excludes):
        return False

    if not all(not exclude.matches(file) for exclude in rom_folder_config.excludes):
        return False

    return not rom_folder_config.includes or any(include.matches(file) for include in rom_folder_config.includes)


def check_disk_space(dest_path: pathlib.Path, extra_space: int):
    _, _, free = shutil.disk_usage(dest_path)
    if extra_space > 0 and free < extra_space:
        print(f"Insufficient free space on destination path {dest_path}.  " +
              f"Free space {humanfriendly.format_size(free, binary=True)}, " +
              f"required space {humanfriendly.format_size(extra_space, binary=True)}")
        sys.exit(1)


def print_dry_run_summary(files_to_delete: set[pathlib.Path],
                          dirs_to_delete: set[pathlib.Path],
                          files_to_copy: set[CopyTask],
                          extra_space: int):
    if not files_to_delete:
        print("DRY RUN: No files to delete.")
    else:
        for file in sorted(files_to_delete):
            print(f"DRY RUN: Deleting {file}")

    if not dirs_to_delete:
        print("DRY RUN: No directories to delete.")
    else:
        for dir in sorted(dirs_to_delete):
            print(f"DRY RUN: Deleting directory {dir} (if empty)")

    if not files_to_copy:
        print("DRY RUN: No files to copy.")
    else:
        for task in sorted(files_to_copy, key=lambda task: task.source):
            print(f"DRY RUN: {task}.")

    if extra_space > 0:
        print(
            f"DRY RUN: Operation will use {humanfriendly.format_size(extra_space, binary=True)} of storage.")
    elif extra_space < 0:
        print(
            f"DRY RUN: Operation will free {humanfriendly.format_size(-extra_space, binary=True)} of storage.")


def run_sync_delete(files_to_delete: set[pathlib.Path], dirs_to_delete: set[pathlib.Path]):
    for file in files_to_delete:
        print(f"Deleting {file}")
        try:
            file.unlink()
        except Exception as e:
            print(f"Error: Failed to delete {file}: {e}")

    for dir in dirs_to_delete:
        if any(dir.iterdir()):
            continue

        print(f"Deleting directory {dir}")
        try:
            dir.rmdir()
        except Exception as e:
            print(f"Error: Failed to delete directory {dir}: {e}")


def run_copy_sync(copy_tasks: set[CopyTask], threads: int, total_size: int):
    if len(copy_tasks) == 0:
        return

    with tqdm(
        total=len(copy_tasks),
        desc="File Progress",
        unit='file',
        position=0,
        dynamic_ncols=True
    ) as file_progress, tqdm(
        total=total_size,
        desc="Bytes Progress",
        unit='B',
        unit_scale=True,
        unit_divisor=1024,
        position=1,
        dynamic_ncols=True
    ) as bytes_progress:
        if threads == 1:
            for task in copy_tasks:
                copy_file_with_progress(task, 1, file_progress, bytes_progress)
        else:
            row_pool = Queue()
            for i in range(threads):
                row_pool.put(i+1)

            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = [
                    executor.submit(copy_file_with_progress_thread,
                                    task, row_pool, file_progress, bytes_progress)
                    for task in copy_tasks
                ]

                for future in futures:
                    future.result()


def copy_file_with_progress_thread(task: CopyTask,
                                   row_pool: Queue,
                                   file_progress: tqdm,
                                   bytes_progress: tqdm,
                                   chunk_size: int = 1024*1024):
    position = row_pool.get()
    try:
        copy_file_with_progress(
            task, position, file_progress, bytes_progress, chunk_size)
    finally:
        row_pool.put(position)


def copy_file_with_progress(task: CopyTask,
                            position: int,
                            file_progress: tqdm,
                            bytes_progress: tqdm,
                            chunk_size: int = 1024*1024):
    try:
        with tqdm(total=0, bar_format=f"{task}", dynamic_ncols=True, position=2*position, leave=False) as top_line, \
            tqdm(total=task.source_size,
                 unit='B',
                 unit_scale=True,
                 unit_divisor=1024,
                 desc=f"-> Progress",
                 position=(2*position)+1,
                 dynamic_ncols=True,
                 leave=False) as pbar:
            try:
                task.dest.parent.mkdir(parents=True, exist_ok=True)
                with open(task.source, 'rb') as fsrc, open(task.dest, 'wb') as fdest:
                    while True:
                        chunk = fsrc.read(chunk_size)
                        if not chunk:
                            break
                        fdest.write(chunk)
                        pbar.update(len(chunk))
                        bytes_progress.update(len(chunk))
            except Exception as e:
                tqdm.write(
                    f"Error: Failed to copy {task.source.name} to {task.dest.parent}: {e}")
    finally:
        file_progress.update(1)


if __name__ == "__main__":
    main()
