import pathlib
from ruamel.yaml import YAML
from dataclasses import dataclass
from typing import Any
from .common import ParseError, YamlType, Location, extract_key, extract_key_and_location, enumerate_seq, validate_type
from .pattern import Filter
from .regions import lookup_region
from .rom import RomFile

METADATA_DIR = pathlib.Path(".metadata")
PROFILES_DIR = METADATA_DIR / "profiles"
DATS_DIR = METADATA_DIR / "dats"


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
    includes: Filter
    excludes: Filter
    dat_files: list[str]
    is_regional_set: bool = False

    @staticmethod
    def from_yaml(yaml_value: dict, location: Location) -> list[RomSet]:
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
        dat_files = extract_key(yaml_value, 'dat_files', location,
                                default=[],
                                expected_types=YamlType.SEQ)
        includes, includes_loc = extract_key_and_location(yaml_value, 'includes', location,
                                                          default={},
                                                          expected_types=YamlType.MAPPING)
        excludes, excludes_loc = extract_key_and_location(yaml_value, 'excludes', location,
                                                          default={},
                                                          expected_types=YamlType.MAPPING)

        regional_sets, regional_sets_loc = extract_key_and_location(yaml_value, 'regional_sets', location,
                                                                    default=[],
                                                                    expected_types=YamlType.SEQ)

        include_filter = Filter.from_yaml(includes, includes_loc)
        exclude_filter = Filter.from_yaml(excludes, excludes_loc)
        if regional_sets:
            if include_filter.regions or include_filter.langs:
                raise ParseError("Regional sets can't be used with includes regions or langs", regional_sets_loc)
            if exclude_filter.regions or exclude_filter.langs:
                raise ParseError("Regional sets can't be used with excludes regions or langs", regional_sets_loc)

        rom_sets = [RomSet(path, name, group, recursive, extensions, include_filter, exclude_filter, dat_files)]
        for region_name, loc in enumerate_seq(regional_sets, regional_sets_loc):
            region = lookup_region(region_name)
            if region is None:
                raise ParseError(f"Unknown or unsupported region \"{name}\".", loc)
            new_include_filter = Filter(include_filter.patterns, [region], [])
            rom_sets.append(RomSet(path, f"{name}_{region.name.casefold()}", group, recursive, extensions,
                            new_include_filter, exclude_filter, dat_files, True))

        return rom_sets

    @staticmethod
    def from_yaml_list(yaml_values: list, location: Location) -> list[RomSet]:
        validate_type(yaml_values, YamlType.SEQ, location)

        existing_names: dict[str, Location] = {}
        rom_folders = []

        for folder_value, folder_loc in enumerate_seq(yaml_values, location):
            rom_sets = RomSet.from_yaml(folder_value, folder_loc)
            for rom_set in rom_sets:
                if rom_set.name in existing_names:
                    raise ParseError(
                        f"A rom folder with the name '{rom_set.name}' was already defined at {existing_names[rom_set.name]}.", folder_loc)

                existing_names[rom_set.name] = folder_loc
                rom_folders.append(rom_set)

        return rom_folders

    def is_included(self, rom_file: RomFile) -> bool:
        return self.is_included_ext(rom_file.file) and self.includes.match_all(rom_file)

    def is_included_ext(self, file: pathlib.Path) -> bool:
        name = file.name.casefold()
        return any(name.endswith(ext) for ext in self.extensions)

    def is_excluded(self, rom_file: RomFile) -> bool:
        return self.excludes.match_any(rom_file)


def _parse_extensions(yaml_values: list, location: Location) -> list[str]:
    if len(yaml_values) == 0:
        raise ParseError("At least one extension must be specified.", location)

    exts = []
    for ext, ext_loc in enumerate_seq(yaml_values, location):
        if not ext:
            raise ParseError(f"Empty or null extensions are not allowed", ext_loc)

        if ext[0] != '.':
            raise ParseError(f"Extension \"{ext}\" does not start with a leading dot (.)", ext_loc)

        exts.append(ext.casefold())

    return exts


def get_metadata_file_path(source_dir: pathlib.Path) -> pathlib.Path:
    return source_dir / METADATA_DIR / "metadata.yml"


def get_profile_file_path(source_dir: pathlib.Path, profile_name: str) -> pathlib.Path:
    return source_dir / PROFILES_DIR / pathlib.Path(profile_name).with_suffix('.yml')


def get_dat_file_path(source_dir: pathlib.Path, dat_file: str) -> pathlib.Path:
    return source_dir / DATS_DIR / pathlib.Path(dat_file)
