#! /usr/bin/env python3

import argparse
import pathlib
import sys
import os
import unicodedata
import shutil
import humanfriendly
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from enum import StrEnum
from dataclasses import dataclass
from dataclass_wizard.mixins.yaml import YAMLWizard

class CopyReason(StrEnum):
    NONE = "none"
    OVERWRITE = "overwrite enabled"
    DOES_NOT_EXIST = "dest doesn't exist"
    SIZE_MISMATCH = "src and dest sizes don't match"
    SOURCE_MODIFIED = "src more recently modified"


@dataclass
class RomFolder(YAMLWizard):
    path: str
    name: str
    extension: list[str]


@dataclass
class NamedSetInclude(YAMLWizard):
    name: str
    destination: str | None = None
    includes: list[str] | None = None
    excludes: list[str] | None = None


@dataclass
class NamedSet(YAMLWizard):
    includes: list[NamedSetInclude]
    delete_excludes: list[str] | None = None

@dataclass
class Metadata(YAMLWizard):
    roms: list[RomFolder]
    named_sets: dict[str, NamedSet]


@dataclass
class RomIncludeConfig:
    rom_folder: RomFolder
    destination: pathlib.Path
    includes: list[str]
    excludes: list[str]

@dataclass
class IncludeConfig:
    delete_exclude: list[str]
    include_roms: list[RomIncludeConfig]

    def add_include(self, rom_folder: RomFolder, destination: pathlib.Path, includes=[], excludes=[]):
        self.include_roms.append(RomIncludeConfig(rom_folder, destination, includes, excludes))

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
    parser = argparse.ArgumentParser(
        description="Sync ROMs between directories")
    parser.add_argument("-s", "--named-set",
                        help="Use one of the predefined sets of rom folders to sync in the metadata file.")
    parser.add_argument('-o', '--overwrite',
                        action="store_true",
                        help="Force overwrite existing files at the destination")
    parser.add_argument('-t', '--threads',
                        default=5,
                        type=int,
                        help="Number of parallel copy threads to use")
    parser.add_argument("--dry-run",
                        action="store_true",
                        help="Runs in dry run mode which will print out the actions that will be taken without performing them")
    parser.add_argument("-f", "--force",
                        action="store_true",
                        help="Ignores the disk-space check and forces the copy to happen")


    delete_group = parser.add_argument_group("Delete Destination")
    parser.add_argument("-d", "--sync-delete",
                            action="store_true",
                            help="Delete files from the destination that are not in the source")
    delete_group.add_argument("-X", "--delete-exclude",
                              action="append",
                              help="Glob pattern to exclude when deleting files that exist in the destination but not the source")

    include_group = parser.add_argument_group("Include Roms")
    include_group.add_argument("-i", "--include",
                               action="append",
                               help="Include only specified rom folders (by name).")
    include_group.add_argument("-e", "--exclude",
                               action="append",
                               help="Exclude specified rom folders (by name).")
    include_group.add_argument("-m", "--destination-mapping",
                               nargs=2,
                               metavar=("NAME", "DESTINATION"),
                               action="append",
                               help="Maps a rom folder to a different destination folder name.")
    

    parser.add_argument("source",
                        help="Source directory where the rom folders are located. Expects a metadata.yml file to exist in this directory.")
    parser.add_argument("destination",
                        help="Destination directory where the roms will be synced to.")
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
        print(
            f"Error: Metadata file '{metadata_file}' must contain a single metadata object.", file=sys.stderr)
        sys.exit(1)

    metadata: Metadata = metadata_result

    include_config = IncludeConfig(delete_exclude=[], include_roms=[])
    if args.named_set:
        if args.named_set not in metadata.named_sets:
            print(
                f"Error: Named set '{args.named_set}' not found in the metadata.yml.", file=sys.stderr)
            sys.exit(1)
        named_set = metadata.named_sets[args.named_set]
        include_config.delete_exclude = [normalize_unicode(exclude) for exclude in named_set.delete_excludes or []]
        for ns_include in named_set.includes:
            try:
                rom_folder = lookup_rom_folder(ns_include.name, metadata)
                destination = resolve_destination(
                    rom_folder, dest_path, ns_include.destination)
                includes = [normalize_unicode(include) for include in ns_include.includes or []]
                excludes = [normalize_unicode(exclude) for exclude in ns_include.excludes or []]
                include_config.add_include(rom_folder, destination, includes, excludes)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        include_config.delete_exclude = args.delete_exclude
        destination_mapping = {name: dest for (name, dest) in args.destination_mapping}
        for rom_folder in metadata.roms:
            if args.include and rom_folder.name not in args.include:
                continue
            if args.exclude and rom_folder.name in args.exclude:
                continue
            destination = resolve_destination(
                rom_folder, dest_path, destination_mapping.get(rom_folder.name))
            include_config.add_include(rom_folder, destination)

    sync_roms(source_path, dest_path, include_config, args.threads,
              args.overwrite, args.sync_delete, args.dry_run, args.force)


def lookup_rom_folder(rom_folder_name: str, metadata: Metadata) -> RomFolder:
    for rom_folder in metadata.roms:
        if rom_folder.name == rom_folder_name:
            return rom_folder

    raise ValueError(f"The rom folder '{rom_folder_name}' was not found.")


def resolve_destination(rom_folder: RomFolder, dest_path: pathlib.Path, destination: str | None) -> pathlib.Path:
    if destination is None:
        return normalize_path(dest_path / pathlib.Path(rom_folder.path))
    elif destination in ['', '.']:
        return normalize_path(dest_path.resolve())
    else:
        return normalize_path(dest_path / destination)

def normalize_path(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(normalize_unicode(str(path))).resolve()

def normalize_unicode(text: str) -> str:
    return unicodedata.normalize('NFC', text)

def sync_roms(source_path: pathlib.Path,
              dest_path: pathlib.Path,
              include_config: IncludeConfig,
              threads: int,
              overwrite: bool,
              sync_delete: bool,
              dry_run: bool,
              force: bool):
    copy_tasks = scan_source_files(source_path, include_config.include_roms)
    to_delete = scan_for_files_to_delete(include_config, copy_tasks, sync_delete)

    # filter out copy tasks where the destination file exists, is the same size,
    # and the source file was not modified since the destination was last copied.
    # The overwrite flag will override this behavior.
    to_copy = [task for task in copy_tasks if should_copy(task, overwrite)]

    total_size = sum(task.source_size for task in to_copy)
    extra_space = total_size \
        - sum(task.dest_size for task in to_copy) \
        - sum(file.stat().st_size for file in to_delete)

    _, _, free = shutil.disk_usage(dest_path)
    if not force and extra_space > 0 and free < extra_space:
        print(f"Insufficient free space on destination path {dest_path}.  Free space {humanfriendly.format_size(free, binary=True)}, required space {humanfriendly.format_size(extra_space, binary=True)}")
        sys.exit(1)

    if dry_run:
        for file in sorted(to_delete):
            print(f"DRY RUN: Deleting {file}")
        
        for task in sorted(to_copy, key=lambda task: task.source):
            reason = get_copy_reason(task, overwrite)
            print (f"DRY RUN: {task}. Reason: {reason}")

        if extra_space > 0:
            print(f"DRY RUN: Operation will use {humanfriendly.format_size(extra_space, binary=True)} of additional storage")
        elif extra_space < 0:
            print(f"DRY RUN: Operation will free {humanfriendly.format_size(-extra_space, binary=True)} of additional storage")
    elif len(to_copy) > 0:
        row_pool = Queue()
        for i in range(threads):
            row_pool.put(i+1)

        for file in to_delete:
            print(f"Deleting {file}")
            try:
                file.unlink()
            except Exception as e:
                print(f"Error: Failed to delete {file}: {e}")

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


def scan_source_files(source_path: pathlib.Path, include_roms: list[RomIncludeConfig]) -> list[CopyTask]:
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

            dest = include.destination / file.name
            dest_stat = None
            if dest.exists():
                dest_stat = dest.stat()

            source_files.append(CopyTask(file,
                                         stat_result,
                                         dest, 
                                         dest_stat))

    return source_files

def scan_for_files_to_delete(include_config: IncludeConfig, copy_tasks: list[CopyTask], sync_delete: bool) -> set[pathlib.Path]:
    if not sync_delete:
        return set()

    actual_dst_paths = set()
    expected_dst_paths = set(task.dest for task in copy_tasks if task.dest.exists())
    unique_dests = set(mapping.destination for mapping in include_config.include_roms)
    for scan_dir in unique_dests:
        if not scan_dir.exists():
            continue

        for file in scan_dir.iterdir():
            if not file.is_file():
                continue

            # ignore hidden files
            if file.name.startswith('.'):
                continue

            normalized_file = normalize_path(file)

            if any(normalized_file.match(exclude) for exclude in include_config.delete_exclude):
                continue
            actual_dst_paths.add(normalized_file)

    return actual_dst_paths - expected_dst_paths

def is_interested_rom(file: pathlib.Path, rom_include_config: RomIncludeConfig) -> bool:
    for include in rom_include_config.includes:
        if not file.match(include):
            return False

    for exclude in rom_include_config.excludes:
        if file.match(exclude):
            return False

    for ext in rom_include_config.rom_folder.extension:
        if file.name.lower().endswith(ext.lower()):
            return True

    return False

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
