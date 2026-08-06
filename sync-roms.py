#! /usr/bin/env python3

import argparse
import pathlib
import sys
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
from enum import StrEnum
from dataclasses import dataclass
from dataclass_wizard.mixins.yaml import YAMLWizard


class Type(StrEnum):
    ROM = "rom"
    DLC = "dlc"
    PATCH = "patch"


@dataclass
class RomFolder(YAMLWizard):
    path: str
    name: str
    type: Type
    extension: list[str]


@dataclass
class NamedSetInclude(YAMLWizard):
    name: str
    destination: str | None = None


@dataclass
class Metadata(YAMLWizard):
    roms: list[RomFolder]
    named_sets: dict[str, list[NamedSetInclude]]


@dataclass
class IncludeMapping:
    rom_folder: RomFolder
    destination: pathlib.Path


@dataclass
class CopyTask:
    source: pathlib.Path
    destination: pathlib.Path

    def __str__(self):
        return f"Copying {self.source.name} -> {self.destination.parent}"


def main():
    parser = argparse.ArgumentParser(
        description="Sync ROMs between directories")
    parser.add_argument("-d", "--sync-delete",
                        action="store_true",
                        help="Delete files from the destination that are not in the source")
    parser.add_argument("-s", "--named-set",
                        help="Use one of the predefined sets of rom folders to sync in the metadata file.")
    parser.add_argument('-o', '--overwrite',
                        action="store_true",
                        help="Overwrite existing files at the destination")
    parser.add_argument('-t', '--threads',
                        default=5,
                        type=int,
                        help="Number of parallel copy threads to use")
    parser.add_argument("--dry-run",
                        action="store_true",
                        help="Runs in dry run mode")

    manual_include_group = parser.add_argument_group("Manual Inclusion")
    manual_include_group.add_argument("-i", "--include",
                                      action="append",
                                      help="Include only specified rom folders (by name).")
    manual_include_group.add_argument("-e", "--exclude",
                                      action="append",
                                      help="Exclude specified rom folders (by name).")
    manual_include_group.add_argument("-m", "--destination-mapping",
                                      nargs=2,
                                      metavar=("NAME", "DESTINATION"),
                                      action="append",
                                      help="Maps a rom folder to a different destination folder name.")

    parser.add_argument("source",
                        help="Source directory where the rom folders are located. Expect a metadata.yml file to exist in this directory.")
    parser.add_argument("destination",
                        help="Destination directory where the roms will be synced to.")
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
        print(f"Error: Source and destination paths can not be the same.", file=sys.stderr)
        sys.exit(1)

    metadata = Metadata.from_yaml_file(metadata_file)

    include_mapping = None
    if args.named_set:
        if args.named_set not in metadata.named_sets:
            print(
                f"Error: Named set '{args.named_set}' not found in the metadata.yml.", file=sys.stderr)
            sys.exit(1)

        include_mapping = []
        for include in metadata.named_sets[args.named_set]:
            try:
                rom_folder = lookup_rom_folder(include.name, metadata)
                destination = resolve_destination(
                    rom_folder, dest_path, include.destination)
                include_mapping.append(IncludeMapping(
                    rom_folder=rom_folder, destination=destination))
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        destination_mapping = {name: dest for (
            name, dest) in args.destination_mapping or []}

        include_mapping = []
        for rom_folder in metadata.roms:
            if args.include and rom_folder.name not in args.include:
                continue
            if args.exclude and rom_folder.name in args.exclude:
                continue
            destination = resolve_destination(
                rom_folder, dest_path, destination_mapping.get(rom_folder.name))
            include_mapping.append(IncludeMapping(
                rom_folder=rom_folder, destination=destination))

    sync_roms(source_path, include_mapping, args.threads,
              args.overwrite, args.sync_delete, args.dry_run)


def lookup_rom_folder(rom_folder_name: str, metadata: Metadata) -> RomFolder:
    for rom_folder in metadata.roms:
        if rom_folder.name == rom_folder_name:
            return rom_folder

    raise ValueError(f"The rom folder '{rom_folder_name}' was not found.")


def resolve_destination(rom_folder: RomFolder, dest_path: pathlib.Path, destination: str | None) -> pathlib.Path:
    if destination is None:
        return (dest_path / pathlib.Path(rom_folder.path)).resolve()
    else:
        return (dest_path / destination).resolve()


def sync_roms(source_path: pathlib.Path,
              include_mapping: list[IncludeMapping],
              threads: int,
              overwrite: bool,
              sync_delete: bool,
              dry_run: bool):
    source_files = []

    for mapping in include_mapping:
        scan_dir = source_path / mapping.rom_folder.path
        if not scan_dir.exists() or not scan_dir.is_dir():
            print(f"Warning: Source rom folder '{scan_dir}' does not exist or is not a directory. Skipping.")
            continue

        for file in scan_dir.iterdir():
            if not file.is_file():
                continue

            if is_interested_rom(file, mapping):
                source_files.append(
                    CopyTask(source=file, destination=mapping.destination / file.name))

    to_delete = set()
    # if the sync delete flag was specified, find all files in the destination directories that do not exist
    # in a source directory. This will also handle the case where multiple source directories are mapped to the same
    # destination
    if sync_delete:
        unique_dests = set(mapping.destination for mapping in include_mapping)
        for scan_dir in unique_dests:
            if not scan_dir.exists():
                continue

            for file in scan_dir.iterdir():
                if not file.is_file():
                    continue

                if not any(task.destination.exists() and task.destination.samefile(file) for task in source_files):
                    to_delete.add(file.resolve())

    # filter out existing files unless the overwrite flag was set
    to_copy = [
        task for task in source_files if overwrite or not task.destination.exists()]

    if dry_run:
        for task in to_copy:
            print (f"DRY RUN: Copying {task.source} -> {task.destination}")
    elif len(to_copy) > 0:
        row_pool = Queue()
        for i in range(threads):
            row_pool.put(i)

        with tqdm(
            total=len(to_copy),
            desc="Overall Progress",
            unit='file',
            position=2*threads,
            dynamic_ncols=True,
            leave=True
        ) as total_progress:
            with ThreadPoolExecutor(max_workers=threads) as executor:
                futures = [
                    executor.submit(copy_file_with_progress, task, row_pool, total_progress) 
                    for task in to_copy
                ]

                for future in futures:
                    future.result()

    for file in to_delete:
        if dry_run:
            print(f"DRY RUN: Deleting {file}")
        else:
            print(f"Deleting {file}")
            file.unlink()


def is_interested_rom(file: pathlib.Path, mapping: IncludeMapping) -> bool:
    for ext in mapping.rom_folder.extension:
        if file.name.lower().endswith(ext.lower()):
            return True

    return False


def copy_file_with_progress(task: CopyTask,
                            row_pool: Queue,
                            total_progress: tqdm,
                            chunk_size: int = 1024*1024):
    position = row_pool.get()

    try:
        file_size = task.source.stat().st_size

        with tqdm(total=0, bar_format=f"{task}", dynamic_ncols=True, position=2*position, leave=False) as top_line, \
             tqdm(total=file_size,
                  unit='B',
                  unit_scale=True,
                  unit_divisor=1024,
                  desc=f"-> Progress",
                  position=(2*position)+1,
                  dynamic_ncols=True,
                  leave=False) as pbar:
            try:
                task.destination.parent.mkdir(parents=True, exist_ok=True)
                with open(task.source, 'rb') as fsrc, open(task.destination, 'wb') as fdest:
                    while True:
                        chunk = fsrc.read(chunk_size)
                        if not chunk:
                            break
                        fdest.write(chunk)
                        pbar.update(len(chunk))
            except Exception as e:
                tqdm.write(f"Error: Failed to copy {task.source} ro {task.destination}: {e}")
    finally:
        row_pool.put(position)
        total_progress.update(1)


if __name__ == "__main__":
    main()
