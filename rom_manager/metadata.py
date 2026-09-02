import pathlib
from ruamel.yaml import YAML
from dataclasses import dataclass
from typing import Any
from .common import ParseError, YamlType, Location, extract_key, extract_key_and_location, enumerate_seq, validate_type
from .pattern import Pattern


@dataclass
class Metadata:
    roms: list[RomSet]

    @classmethod
    def load_from_file(cls, file: pathlib.Path) -> Metadata:
        yaml_parser = YAML(typ='rt')
        data = yaml_parser.load(file)
        location = Location(None, file, data.lc.line+1)

        return cls.from_yaml(data, location)

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location) -> Metadata:
        roms, roms_loc = extract_key_and_location(
            yaml_value, 'roms', location, required=True)
        return Metadata(RomSet.from_yaml_list(roms, roms_loc))

    def find_rom_set(self, name: str) -> RomSet | None:
        for rom_set in self.roms:
            if rom_set.name == name:
                return rom_set

        return None

    def find_group(self, group_name: str) -> list[RomSet]:
        return [rom_set for rom_set in self.roms if rom_set.group == group_name]


@dataclass
class RomSet:
    path: str
    name: str
    group: str | None
    recursive: bool
    extensions: list[str]
    excludes: list[Pattern]

    @staticmethod
    def from_yaml(yaml_value: dict, location: Location) -> RomSet:
        path = extract_key(yaml_value, 'path', location,
                           required=True, expected_types=YamlType.STRING)
        name = extract_key(yaml_value, 'name', location,
                           required=True, expected_types=YamlType.STRING)
        group = extract_key(yaml_value, 'group', location,
                            expected_types=YamlType.STRING)
        recursive = extract_key(yaml_value, 'recursive', location,
                                default=False, expected_types=YamlType.BOOL)
        extensions = _parse_extensions(*extract_key_and_location(yaml_value, 'extensions', location,
                                                                 required=True, expected_types=YamlType.SEQ))
        excludes, excludes_loc = extract_key_and_location(yaml_value, 'excludes', location,
                                                          default=[], expected_types=YamlType.SEQ)

        return RomSet(path,
                      name,
                      group,
                      recursive,
                      extensions,
                      Pattern.from_yaml_list(excludes, excludes_loc))

    @staticmethod
    def from_yaml_list(yaml_values: list, location: Location) -> list[RomSet]:
        validate_type(yaml_values, YamlType.SEQ, location)

        existing_names: dict[str, Location] = {}
        rom_folders = []

        for folder_value, folder_loc in enumerate_seq(yaml_values, location):
            rom_folder = RomSet.from_yaml(folder_value, folder_loc)

            if rom_folder.name in existing_names:
                raise ParseError(
                    f"A rom folder with the name '{rom_folder.name}' was already defined at {existing_names[rom_folder.name]}.", folder_loc)

            existing_names[rom_folder.name] = folder_loc
            rom_folders.append(rom_folder)

        return rom_folders

    def is_included(self, relative_path: pathlib.Path) -> bool:
        name = relative_path.name.casefold()

        return any(name.endswith(ext) for ext in self.extensions)

    def is_excluded(self, relative_path: pathlib.Path) -> bool:
        return any(exclude.matches(relative_path) for exclude in self.excludes)


def _parse_extensions(yaml_values: list, location: Location) -> list[str]:
    if len(yaml_values) == 0:
        raise ParseError("At least one extension must be specified.", location)

    exts = []

    for ext, ext_loc in enumerate_seq(yaml_values, location):
        if not ext:
            raise ParseError(f"Empty or null extensions are not allowed", ext_loc)

        if ext[0] != '.':
            raise ParseError(f"Extension {ext} does not start with a leading dot (.)", ext_loc)

        exts.append(ext.casefold())

    return exts
