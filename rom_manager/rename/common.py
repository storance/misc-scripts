import pathlib
from dataclasses import dataclass, field

from .. import RomSet, replace_suffix

@dataclass
class TargetRomSet:
    primary_rom_set: RomSet
    sync_rom_sets: list[RomSet]
    scan_extensions: list[str] = field(init=False, default_factory=list)

    def __post_init__(self):
        # for bin/cue files, we only want to scan for the cue file, not the bin files
        if '.cue' in self.primary_rom_set.extensions:
            self.scan_extensions = [ext for ext in self.primary_rom_set.extensions if ext != '.bin']
        else:
            self.scan_extensions = self.primary_rom_set.extensions

    def get_files_to_sync(self, input_directory: pathlib.Path, rename_target: pathlib.Path) -> list[pathlib.Path]:
        rom_set_path = input_directory / self.primary_rom_set.path
        # path relative to the rom set path, so we can replace the suffix and append it to the sync rom set path
        target_relative_path = rename_target.relative_to(rom_set_path)

        sync_files = []
        for sync_rom_set in self.sync_rom_sets:
            for ext in sync_rom_set.extensions:
                sync_file = input_directory / sync_rom_set.path / replace_suffix(target_relative_path, ext)
                if sync_file.exists():
                    sync_files.append(sync_file)
        return sync_files

    def is_included(self, relative_path: pathlib.Path) -> bool:
        name = relative_path.name.casefold()

        return any(name.endswith(ext) for ext in self.scan_extensions)

    def is_excluded(self, relative_path: pathlib.Path) -> bool:
        return self.primary_rom_set.is_excluded(relative_path)
    

@dataclass
class CueFile:
    cue_file: pathlib.Path
    bin_files: list[pathlib.Path]

@dataclass
class RenameTarget:
    file: pathlib.Path|CueFile
    sync_files: list[pathlib.Path]

    def is_single_file(self) -> bool:
        return isinstance(self.file, pathlib.Path)
    
    def is_cue_file(self) -> bool:
        return isinstance(self.file, CueFile)

    def as_single_file(self)-> pathlib.Path:
        if not self.is_single_file():
            raise ValueError("RenameTarget does not represent a single file.")
        return self.file # type: ignore the self.is_single_file() check ensures that self.file is a pathlib.Path

    def as_cue_file(self) -> CueFile:
        if not self.is_cue_file():
            raise ValueError("RenameTarget does not represent a cue file.")
        return self.file # type: ignore the self.is_cue_file() check ensures that self.file is a CueFile