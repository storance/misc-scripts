import pathlib
from typing import Any
from .common import ParseError, YamlType, Location, extract_key, extract_key_and_location, enumerate_seq, validate_type
from .pattern import Pattern
from dataclasses import dataclass


@dataclass
class Metadata:
    roms: list[RomFolder]

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location) -> Metadata:
        roms, roms_loc = extract_key_and_location(
            yaml_value, 'roms', location, required=True)
        return Metadata(RomFolder.from_yaml_list(roms, roms_loc))

    def find_rom_folder(self, name: str) -> RomFolder | None:
        for rom_folder in self.roms:
            if rom_folder.name == name:
                return rom_folder

        return None


@dataclass
class RomFolder:
    path: str
    name: str
    include_subfolders: bool
    extensions: list[str]
    excludes: list[Pattern]

    @staticmethod
    def from_yaml(yaml_value: dict, location: Location) -> RomFolder:
        path = extract_key(yaml_value, 'path', location,
                           required=True, expected_types=YamlType.STRING)
        name = extract_key(yaml_value, 'name', location,
                           required=True, expected_types=YamlType.STRING)
        include_subfolders = extract_key(yaml_value, 'include_subfolders', location,
                                         default=False, expected_types=YamlType.BOOL)
        extensions = extract_key(yaml_value, 'extensions', location,
                                 required=True, expected_types=YamlType.SEQ)
        excludes, excludes_loc = extract_key_and_location(yaml_value, 'excludes', location,
                                                          default=[], expected_types=YamlType.SEQ)

        return RomFolder(path,
                         name,
                         include_subfolders,
                         extensions,
                         Pattern.from_yaml_list(excludes, excludes_loc))

    @staticmethod
    def from_yaml_list(yaml_values: list, location: Location) -> list[RomFolder]:
        validate_type(yaml_values, YamlType.SEQ, location)

        existing_names: dict[str, Location] = {}
        rom_folders = []

        for folder_value, folder_loc in enumerate_seq(yaml_values, location):
            rom_folder = RomFolder.from_yaml(folder_value, folder_loc)

            if rom_folder.name in existing_names:
                raise ParseError(
                    f"A rom folder with the name '{rom_folder.name}' was already defined at {existing_names[rom_folder.name]}.", folder_loc)

            existing_names[rom_folder.name] = folder_loc
            rom_folders.append(rom_folder)

        return rom_folders

    def is_excluded(self, relative_path: pathlib.Path) -> bool:
        return any(exclude.matches(relative_path) for exclude in self.excludes)
