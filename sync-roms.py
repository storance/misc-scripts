#! /usr/bin/env python3

import argparse
import pathlib
import sys
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
    destination: str|None = None

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

def main():
    parser = argparse.ArgumentParser(description="Sync ROMs between directories")
    parser.add_argument("-D", "--dry-run", action="store_true", help="Perform a dry run without making changes")
    parser.add_argument("-d", "--sync-delete", action="store_true", help="Delete files from the destination that are not in the source")
    parser.add_argument("-s", "--named-set", help="Use one of the predefined sets of rom folders to sync in the metadata file.")

    manual_include_group = parser.add_argument_group("Manual Inclusion")
    manual_include_group.add_argument("-i", "--include", action="append", help="Include only specified rom folders (by name).")
    manual_include_group.add_argument("-e", "--exclude", action="append", help="Exclude specified rom folders (by name).")
    manual_include_group.add_argument("-m", "--destination-mapping", nargs=2, metavar=("NAME", "DESTINATION"), action="append", help="Maps a rom folder to a different destination folder name.")

    parser.add_argument("source", help="Source directory where the rom folders are located. Expect a metadata.yml file to exist in this directory.")
    parser.add_argument("destination", help="Destination directory where the roms will be synced to.")
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

    metadata = Metadata.from_yaml_file(metadata_file)

    include_mapping = None
    if args.named_set:
        if args.named_set not in metadata.named_sets:
            print(f"Error: Named set '{args.named_set}' not found in the metadata.yml.", file=sys.stderr)
            sys.exit(1)

        include_mapping = []
        for include in metadata.named_sets[args.named_set]:
            try:
                rom_folder = lookup_rom_folder(include.name, metadata)
                destination = resolve_destination(rom_folder, dest_path, include.destination)   
                include_mapping.append(IncludeMapping(rom_folder=rom_folder, destination=destination))
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        destination_mapping = {name: dest for (name, dest) in args.destination_mapping or []}

        include_mapping = []
        for rom_folder in metadata.roms:
            if args.include and rom_folder.name not in args.include:
                continue
            if args.exclude and rom_folder.name in args.exclude:
                continue
            destination = resolve_destination(rom_folder, dest_path, destination_mapping.get(rom_folder.name))
            include_mapping.append(IncludeMapping(rom_folder=rom_folder, destination=destination))

    sync_roms(source_path, dest_path, include_mapping, args.dry_run, args.sync_delete)

def lookup_rom_folder(rom_folder_name: str, metadata: Metadata) -> RomFolder:
    for rom_folder in metadata.roms:
        if rom_folder.name == rom_folder_name:
            return rom_folder
    
    raise ValueError(f"The rom folder '{rom_folder_name}' was not found.")

def resolve_destination(rom_folder: RomFolder, dest_path: pathlib.Path, destination: str|None) -> pathlib.Path:
    if destination is None:
        return (dest_path / pathlib.Path(rom_folder.path)).resolve()
    else:
        return (dest_path / destination).resolve()

def sync_roms(source_path: pathlib.Path, dest_path: pathlib.Path, include_mapping: list[IncludeMapping], dry_run: bool, sync_delete: bool):
    source_files = []

    for mapping in include_mapping:
        scan_dir = source_path / mapping.rom_folder.path
        print (f"Scanning {scan_dir}")
        if not scan_dir.exists() or not scan_dir.is_dir():
            print(f"Warning: Source rom folder '{scan_dir}' does not exist or is not a directory. Skipping.", file=sys.stderr)
            continue

        for file in scan_dir.iterdir():
            if not file.is_file():
                continue

            if is_interested_rom(file, mapping):
                source_files.append(CopyTask(source=file, destination=mapping.destination / file.name))

    to_delete = set()
    if sync_delete:
        unique_dests = set(mapping.destination for mapping in include_mapping)
        for scan_dir in unique_dests:
            if not scan_dir.exists():
                continue

            for file in scan_dir.iterdir():
                if not file.is_file():
                    continue

                source_exists = any(task.destination.exists() and task.destination.samefile(file) for task in source_files)
                if source_exists is None:
                    to_delete.append(file.resolve())

    for copy_task in source_files:
        if not copy_task.destination.exists():
            print(f"Copying {copy_task.source.relative_to(source_path)} to {copy_task.destination.relative_to(dest_path)}")
            if not dry_run:
                copy_task.destination.parent.mkdir(parents=True, exist_ok=True)
                copy_file_with_progress(copy_task.source, copy_task.destination)

    for file in to_delete:
        print(f"Deleting destination file {file}")
        if not dry_run:
            file.unlink()

    
def is_interested_rom(file: pathlib.Path, mapping: IncludeMapping) -> bool:
    for ext in mapping.rom_folder.extension:
        if file.name.lower().endswith(ext.lower()):
            return True
    
    return False

def copy_file_with_progress(src: pathlib.Path, dest: pathlib.Path, chunk_size: int = 1024*1024):
    file_size = src.stat().st_size
    
    with tqdm(total=file_size, unit='B', unit_scale=True, unit_divisor=1024, desc="Copying") as pbar:
        with open(src, 'rb') as fsrc:
            with open(dest, 'wb') as fdest:
                while True:
                    chunk = fsrc.read(chunk_size)
                    if not chunk:
                        break
                    fdest.write(chunk)
                    pbar.update(len(chunk))

if __name__ == "__main__":
    main()