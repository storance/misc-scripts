#! /usr/bin/env python3

import argparse
import pathlib
import sys
import os
import unicodedata
import shutil
import humanfriendly
import re
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from enum import StrEnum
from dataclasses import dataclass, field
from dataclass_wizard.mixins.yaml import YAMLWizard

class CopyReason(StrEnum):
    NONE = "none"
    OVERWRITE = "overwrite enabled"
    DOES_NOT_EXIST = "dest doesn't exist"
    SIZE_MISMATCH = "src and dest sizes don't match"
    SOURCE_MODIFIED = "src more recently modified"

@dataclass
class Metadata(YAMLWizard):
    roms: list[RomFolder]

@dataclass
class RomFolder(YAMLWizard):
    path: str
    name: str
    extensions: list[str]
    collections: dict[str, list[str]] = field(default_factory=dict)

@dataclass
class RomSetConfig(YAMLWizard):
    includes: list[IncludeConfig]
    root_folder: str | None = None
    delete_excludes: list[str] = field(default_factory=list)


@dataclass
class IncludeConfig(YAMLWizard):
    name: str
    destination: str | None = None
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    folder_per_game: bool = False

@dataclass
class ResolvedRomSetConfig:
    includes: list[ResolvedIncludeConfig]
    delete_excludes: list[str] = field(default_factory=list)

    @staticmethod
    def convert(set_config: RomSetConfig, dest_path: pathlib.Path, metadata: Metadata) -> ResolvedRomSetConfig:
        delete_excludes = [normalize_unicode(exclude) for exclude in set_config.delete_excludes]
        includes = []
        for include_config in set_config.includes:
            rom_folder = lookup_rom_folder(include_config.name, metadata)
            includes.append(ResolvedIncludeConfig.convert(include_config, dest_path, set_config.root_folder, rom_folder))

        return ResolvedRomSetConfig(includes, delete_excludes)

def lookup_rom_folder(rom_folder_name: str, metadata: Metadata) -> RomFolder:
    for rom_folder in metadata.roms:
        if rom_folder.name == rom_folder_name:
            return rom_folder

    raise ValueError(f"The rom folder '{rom_folder_name}' was not found in the metadata.yml.")


@dataclass
class ResolvedIncludeConfig:
    rom_folder: RomFolder
    destination: pathlib.Path
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    folder_per_game: bool = False

    @staticmethod
    def convert(include_config: IncludeConfig,
                dest_path: pathlib.Path,
                root_folder: str | None,
                rom_folder: RomFolder) -> ResolvedIncludeConfig:
        destination = resolve_destination(rom_folder, dest_path, root_folder, include_config.destination)
        includes = [normalize_unicode(include) for include in include_config.includes]
        excludes = [normalize_unicode(exclude) for exclude in include_config.excludes]

        return ResolvedIncludeConfig(
            rom_folder=rom_folder,
            destination=destination,
            includes=includes,
            excludes=excludes,
            folder_per_game=include_config.folder_per_game
        )

@dataclass
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


def main():
    parser = argparse.ArgumentParser(description='Sync ROMs between directories')
    parser.add_argument('-s', '--set-config',
                        help='Use a predefined set configuration.  By default, looks in the destination ' +
                             'folder for a file named this with a .yaml extension.')
    parser.add_argument('-o', '--overwrite',
                        action='store_true',
                        help='Force overwrite existing files at the destination.')
    parser.add_argument('-p', '--parallel-copies',
                        default=5,
                        type=int,
                        help='Number file copies to perform in parallel.')
    parser.add_argument('-D', '--dry-run',
                        action='store_true',
                        help='Runs in dry run mode. No copies or deletes will take place and a summary of ' + 
                             'the planned actions will be printed.')
    parser.add_argument('-f', '--force',
                        action='store_true',
                        help="Ignores the disk-space check and forces the copy to happen")
    parser.add_argument('-d', '--sync-delete',
                        action='store_true',
                        help="Delete files from the destination that are not in the source")
    parser.add_argument('-H', '--delete-hidden',
                        action='store_true',
                        help="Also deletes files that start with a period (.) which are hidden files on nix platforms.")
    parser.add_argument('source',
                        help='Source directory where the rom folders are located. ' +
                             'Expects a metadata.yml file to exist in this directory.')
    parser.add_argument('destination',
                        help='Destination directory where the roms will be synced to.')
    args = parser.parse_args()

    source_path = pathlib.Path(args.source)
    if not source_path.exists() or not source_path.is_dir():
        print(f"Error: Source path '{source_path}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)

    metadata_file = source_path / "metadata.yml"
    if not metadata_file.exists():
        print(f"Error: Metadata file '{metadata_file}' does not exist in the source directory.", file=sys.stderr)
        sys.exit(1)

    dest_path = pathlib.Path(args.destination)
    if not dest_path.exists() or not dest_path.is_dir():
        print(f"Error: Destination path '{dest_path}' does not exist or is not a directory.", file=sys.stderr)
        sys.exit(1)

    if source_path.samefile(dest_path):
        print(f"Error: Source and destination paths can not be the same.", file=sys.stderr)
        sys.exit(1)

    metadata_result = Metadata.from_yaml_file(metadata_file)
    if isinstance(metadata_result, list):
        print(f"Error: Metadata file '{metadata_file}' must contain a single metadata object.", file=sys.stderr)
        sys.exit(1)

    metadata: Metadata = metadata_result

    if args.set_config:
        set_config_path = source_path / pathlib.Path(args.set_config).with_suffix('.yml')
        if not set_config_path.exists():
            print(f"Error: Set config file {args.set_config} does not exist at {set_config_path}.", file=sys.stderr)
            sys.exit(1)


        set_config_result = RomSetConfig.from_yaml_file(set_config_path)
        if isinstance(set_config_result, list):
            print(f"Error: Set config file '{metadata_file}' must contain a single object.", file=sys.stderr)
            sys.exit(1)
        set_config: RomSetConfig = set_config_result

        try:
            resolved_set_config = ResolvedRomSetConfig.convert(set_config, dest_path, metadata)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        includes = []
        for rom_folder in metadata.roms:
            includes.append(ResolvedIncludeConfig(rom_folder, resolve_destination(rom_folder, dest_path, None, None)))
        resolved_set_config = ResolvedRomSetConfig(includes)

    sync_roms(source_path, dest_path, resolved_set_config, args.parallel_copies,
              args.overwrite, args.sync_delete, args.delete_hidden, args.dry_run, args.force)


def resolve_destination(rom_folder: RomFolder,
                        dest_path: pathlib.Path,
                        root_folder:str | None,
                        destination: str | None) -> pathlib.Path:

    if destination is None:
        if root_folder is None or root_folder == '': 
            return normalize_path(dest_path / rom_folder.path)
        else:
            return normalize_path(dest_path / root_folder / rom_folder.path)
    elif destination in ['', '.']:
        if root_folder is None or root_folder == '': 
            return normalize_path(dest_path)
        else:
            return normalize_path(dest_path / root_folder)
    else:
        if destination.startswith('/'):
            return normalize_path(dest_path / destination[1:])
        elif root_folder is None or root_folder == '':
            return normalize_path(dest_path / destination)
        else:
            return normalize_path(dest_path / root_folder / destination)
        

def normalize_path(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(normalize_unicode(str(path))).resolve()

def normalize_unicode(text: str) -> str:
    return unicodedata.normalize('NFC', text)

def sync_roms(source_path: pathlib.Path,
              dest_path: pathlib.Path,
              rom_set_config: ResolvedRomSetConfig,
              threads: int,
              overwrite: bool,
              sync_delete: bool,
              delete_hidden: bool,
              dry_run: bool,
              force: bool):
    copy_tasks = scan_source_files(source_path, rom_set_config.includes)
    (files_to_delete, dirs_to_delete) = scan_for_files_to_delete(rom_set_config, copy_tasks, sync_delete, delete_hidden)

    # filter out copy tasks where the destination file exists, is the same size,
    # and the source file was not modified since the destination was last copied.
    # The overwrite flag will override this behavior.
    to_copy = [task for task in copy_tasks if should_copy(task, overwrite)]

    total_size = sum(task.source_size for task in to_copy)
    extra_space = total_size \
        - sum(task.dest_size for task in to_copy) \
        - sum(file.stat().st_size for file in files_to_delete)

    _, _, free = shutil.disk_usage(dest_path)
    if not force and extra_space > 0 and free < extra_space:
        print(f"Insufficient free space on destination path {dest_path}.  " +
               f"Free space {humanfriendly.format_size(free, binary=True)}, " +
               f"required space {humanfriendly.format_size(extra_space, binary=True)}")
        sys.exit(1)

    if dry_run:
        for file in sorted(files_to_delete):
            print(f"DRY RUN: Deleting {file}")

        for dir in sorted(dirs_to_delete):
            print(f"DRY RUN: Deleting directory {dir} (if empty)")
        
        for task in sorted(to_copy, key=lambda task: task.source):
            reason = get_copy_reason(task, overwrite)
            print (f"DRY RUN: {task}. Reason: {reason}")

        if extra_space > 0:
            print(f"DRY RUN: Operation will use {humanfriendly.format_size(extra_space, binary=True)} of additional storage")
        elif extra_space < 0:
            print(f"DRY RUN: Operation will free {humanfriendly.format_size(-extra_space, binary=True)} of additional storage")
    else:
        row_pool = Queue()
        for i in range(threads):
            row_pool.put(i+1)

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

        if len(to_copy) == 0:
            return

        with tqdm(
            total=len(to_copy),
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
            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = [
                    executor.submit(copy_file_with_progress, task, row_pool, file_progress, bytes_progress) 
                    for task in to_copy
                ]

                for future in futures:
                    future.result()


def scan_source_files(source_path: pathlib.Path, include_roms: list[ResolvedIncludeConfig]) -> list[CopyTask]:
    source_files = []
    for include in include_roms:
        scan_dir = source_path / include.rom_folder.path
        if not scan_dir.exists() or not scan_dir.is_dir():
            print(f"Warning: Source rom folder '{scan_dir}' does not exist or is not a directory. Skipping.")
            continue

        for file in scan_dir.iterdir():
            normalized_file = normalize_path(file)
            if not normalized_file.is_file() or not is_interested_rom(normalized_file, include):
                continue

            stat_result = file.stat()

            if include.folder_per_game:
                dest = include.destination / get_folder_name(include.rom_folder, normalized_file) / normalized_file.name
            else:
                dest = include.destination / normalized_file.name

            dest_stat = None
            if dest.exists():
                dest_stat = dest.stat()

            source_files.append(CopyTask(normalized_file,
                                         stat_result,
                                         dest, 
                                         dest_stat))

    return source_files

def scan_for_files_to_delete(rom_set_config: ResolvedRomSetConfig,
                             copy_tasks: list[CopyTask],
                             sync_delete: bool,
                             delete_hidden: bool) -> tuple[set[pathlib.Path], set[pathlib.Path]]:
    if not sync_delete:
        return (set(), set())

    expected_dst_paths = set(task.dest for task in copy_tasks if task.dest.exists())
    scanned_dests = set()
    files_to_delete = set()
    dirs_to_delete = set()

    for include in rom_set_config.includes:
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
                if not delete_hidden and file.name.startswith('.'):
                    continue

                normalized_file = normalize_path(file)

                if any(normalized_file.match(exclude) for exclude in rom_set_config.delete_excludes):
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

def is_interested_rom(file: pathlib.Path, include_config: ResolvedIncludeConfig) -> bool:
    if not any(file.name.lower().endswith(ext.lower()) for ext in include_config.rom_folder.extensions):
        return False

    if not all(not file.match(exclude) for exclude in include_config.excludes):
        return False

    return not include_config.includes or any(file.match(include) for include in include_config.includes)

def get_folder_name(rom_folder: RomFolder, source_file: pathlib.Path) -> str:
    if rom_folder.collections:
        for collection, patterns in rom_folder.collections.items():
            if any(source_file.match(pattern) for pattern in patterns):
                return collection

    no_ext = source_file.stem
    first_paren = no_ext.find('(')
    if first_paren == -1:
        return no_ext
    else:
        return no_ext[:first_paren].strip()


def should_copy(task: CopyTask, overwrite: bool) -> bool:
    return get_copy_reason(task, overwrite) != CopyReason.NONE

def get_copy_reason(task: CopyTask, overwrite: bool) -> CopyReason:
    if task.dest_stat is None:
        return CopyReason.DOES_NOT_EXIST
    
    if overwrite:
        return CopyReason.OVERWRITE

    if task.dest_size != task.source_size:
        return CopyReason.SIZE_MISMATCH

    if task.dest_stat.st_mtime < task.source_stat.st_mtime:
        return CopyReason.SOURCE_MODIFIED

    return CopyReason.NONE

def copy_file_with_progress(task: CopyTask,
                            row_pool: Queue,
                            file_progress: tqdm,
                            bytes_progress: tqdm,
                            chunk_size: int = 1024*1024):
    position = row_pool.get()

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
                tqdm.write(f"Error: Failed to copy {task.source.name} to {task.dest.parent}: {e}")
    finally:
        row_pool.put(position)
        file_progress.update(1)


if __name__ == "__main__":
    main()
