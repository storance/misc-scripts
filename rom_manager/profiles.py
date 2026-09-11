import pathlib
from enum import StrEnum
from ruamel.yaml import YAML
from dataclasses import dataclass, field
from typing import Any
from .pattern import Pattern
from .common import ParseError, Location, YamlType, extract_key, extract_key_and_location, \
    enumerate_seq, enumerate_mapping, validate_type
from .metadata import Metadata, RomSet


@dataclass
class Profile:
    folders: list[ProfileFolder]

    @classmethod
    def load_from_file(cls, file: pathlib.Path, metadata: Metadata) -> Profile:
        yaml_parser = YAML(typ='rt')
        data = yaml_parser.load(file)
        location = Location(None, file, data.lc.line+1)

        return cls.from_yaml(data, location, metadata)

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, metadata: Metadata) -> Profile:
        validate_type(yaml_value, YamlType.MAPPING, location)

        folders, folders_loc = extract_key_and_location(yaml_value, 'folders', location,
                                                        required=True,
                                                        expected_types=YamlType.SEQ)

        return Profile(ProfileFolder.from_yaml_list(folders, folders_loc, metadata))


@dataclass
class ProfileFolder:
    path: pathlib.Path
    grouping: GroupingConfig | None
    rom_sets: list[ProfileRomSet]
    delete_excludes: list[Pattern]

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, metadata: Metadata) -> ProfileFolder:
        validate_type(yaml_value, YamlType.MAPPING, location)

        path = extract_key(yaml_value, 'path', location, required=True, expected_types=YamlType.STRING)
        grouping, grouping_loc = extract_key_and_location(
            yaml_value, 'grouping', location, expected_types=YamlType.MAPPING)
        rom_sets, rom_sets_loc = extract_key_and_location(yaml_value, 'rom_sets', location, expected_types=YamlType.SEQ)
        delete_excludes, delete_excludes_loc = extract_key_and_location(
            yaml_value, 'delete_excludes', location, expected_types=YamlType.SEQ)

        return ProfileFolder(
            pathlib.Path(path),
            GroupingConfig.from_yaml(grouping, grouping_loc),
            ProfileRomSet.from_yaml_list(rom_sets, rom_sets_loc, metadata),
            Pattern.from_yaml_list(delete_excludes, delete_excludes_loc)
        )

    @classmethod
    def from_yaml_list(cls, yaml_values: list[Any], location: Location, metadata: Metadata) -> list[ProfileFolder]:
        return [cls.from_yaml(value, loc, metadata) for value, loc in enumerate_seq(yaml_values, location)]


@dataclass(frozen=True)
class ProfileRomSet:
    rom_set: RomSet
    includes: list[Pattern]
    excludes: list[Pattern]

    @staticmethod
    def from_yaml(yaml_value: Any,
                  location: Location,
                  metadata: Metadata) -> ProfileRomSet:
        validate_type(yaml_value, YamlType.MAPPING, location)
        name, name_loc = extract_key_and_location(yaml_value, 'name', location,
                                                  required=True,
                                                  expected_types=YamlType.STRING)
        rom_set = metadata.find_rom_set(name)
        if rom_set is None:
            raise ParseError(f"A rom set with the name '{name}' does not exist in the metadata.yml", name_loc)

        includes, includes_loc = extract_key_and_location(yaml_value, 'includes', location,
                                                          default=[],
                                                          expected_types=YamlType.SEQ)
        excludes, excludes_loc = extract_key_and_location(yaml_value, 'excludes', location,
                                                          default=[],
                                                          expected_types=YamlType.SEQ)

        return ProfileRomSet(rom_set,
                             Pattern.from_yaml_list(includes, includes_loc),
                             Pattern.from_yaml_list(excludes, excludes_loc))

    @classmethod
    def from_yaml_list(cls, yaml_values: list[Any], location: Location, metadata: Metadata) -> list[ProfileRomSet]:
        return [cls.from_yaml(value, loc, metadata) for value, loc in enumerate_seq(yaml_values, location)]

    def is_excluded(self, relative_path: pathlib.Path) -> bool:
        return any(exclude.matches(relative_path) for exclude in self.excludes)

    def is_included(self, relative_path: pathlib.Path) -> bool:
        if not self.includes:
            return True

        return any(include.matches(relative_path) for include in self.includes)


class GroupType(StrEnum):
    GAME = 'game'
    LANG = 'lang'
    REGION = 'region'
    LETTER = 'letter'


@dataclass
class GroupingConfig:
    group_by: list[GroupType]
    by_game: GroupByGameConfig | None
    by_region: GroupByRegionConfig | None
    by_lang: GroupByLangConfig | None
    by_prefix: GroupByPrefixConfig | None

    @staticmethod
    def disabled() -> GroupingConfig:
        return GroupingConfig([], None, None, None, None)

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location,) -> GroupingConfig:
        if yaml_value is None:
            return GroupingConfig.disabled()

        validate_type(yaml_value, YamlType.MAPPING, location)
        by_raw, by_loc = extract_key_and_location(yaml_value, 'by', location,
                                                  default=[],
                                                  expected_types=YamlType.SEQ)
        group_by = []
        for value in by_raw:
            try:
                group_by.append(GroupType(value))
            except ValueError:
                raise ParseError(f"Invalid group-by type: {value}", by_loc)

        by_game, by_game_loc = extract_key_and_location(yaml_value, 'by_game', location,
                                                        expected_types=YamlType.MAPPING)
        by_region, by_region_loc = extract_key_and_location(yaml_value, 'by_region', location,
                                                            expected_types=YamlType.MAPPING)
        by_lang, by_lang_loc = extract_key_and_location(yaml_value, 'by_lang', location,
                                                        expected_types=YamlType.MAPPING)
        by_prefix, by_prefix_loc = extract_key_and_location(yaml_value, 'by_prefix', location,
                                                            expected_types=YamlType.MAPPING)

        return GroupingConfig(
            group_by,
            GroupByGameConfig.from_yaml(by_game, by_game_loc, group_by),
            GroupByRegionConfig.from_yaml(by_region, by_region_loc, group_by),
            GroupByLangConfig.from_yaml(by_lang, by_lang_loc, group_by),
            GroupByPrefixConfig.from_yaml(by_prefix, by_prefix_loc, group_by)
        )

    def is_enabled(self):
        return len(self.group_by) > 0


@dataclass
class GroupByGameConfig:
    strip_regions: bool = False
    strip_langs: bool = False
    custom_mapping: dict[str, list[Pattern]] = field(default_factory=dict)
    disable_if_single_rom: bool = False

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, group_by: list[GroupType]) -> GroupByGameConfig | None:
        if yaml_value is None:
            if GroupType.GAME in group_by:
                return GroupByGameConfig()
            else:
                return None

        validate_type(yaml_value, YamlType.MAPPING, location)
        strip_regions = extract_key(yaml_value, 'strip_regions', location, default=False, expected_types=YamlType.BOOL)
        strip_langs = extract_key(yaml_value, 'strip_langs', location, default=False, expected_types=YamlType.BOOL)
        disable_if_single_rom = extract_key(yaml_value, 'disable_if_single_rom',
                                            location, default=False, expected_types=YamlType.BOOL)

        custom_mapping_raw, custom_mapping_loc = extract_key_and_location(
            yaml_value, 'custom_mappings', location, default={}, expected_types=YamlType.MAPPING)

        custom_mapping = {
            key: Pattern.from_yaml_list(value, key_loc)
            for key, value, key_loc in enumerate_mapping(custom_mapping_raw, custom_mapping_loc)
        }
        return GroupByGameConfig(strip_regions, strip_langs, custom_mapping, disable_if_single_rom)


@dataclass
class GroupByLangConfig:
    one_lang_per_rom: bool = False
    lang_ordering: list[str] = field(default_factory=list)
    disable_if_single_lang: bool = False

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, group_by: list[GroupType]) -> GroupByLangConfig | None:
        if yaml_value is None:
            if GroupType.LANG in group_by:
                return GroupByLangConfig()
            else:
                return None

        validate_type(yaml_value, YamlType.MAPPING, location)
        disable_if_single_lang = extract_key(yaml_value, 'disable_if_single_lang', location,
                                             default=False, expected_types=YamlType.BOOL)
        one_lang_per_rom = extract_key(yaml_value, 'one_lang_per_rom', location,
                                       default=False, expected_types=YamlType.BOOL)
        lang_ordering = extract_key(yaml_value, 'lang_ordering', location, default=[], expected_types=YamlType.SEQ)
        return GroupByLangConfig(one_lang_per_rom, lang_ordering, disable_if_single_lang)


@dataclass
class GroupByRegionConfig:
    one_region_per_rom: bool = False
    region_ordering: list[str] = field(default_factory=list)
    disable_if_single_region: bool = False

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, group_by: list[GroupType]) -> GroupByRegionConfig | None:
        if yaml_value is None:
            if GroupType.REGION in group_by:
                return GroupByRegionConfig()
            else:
                return None

        validate_type(yaml_value, YamlType.MAPPING, location)
        disable_if_single_region = extract_key(yaml_value, 'disable_if_single_region', location,
                                               default=False, expected_types=YamlType.BOOL)
        one_region_per_rom = extract_key(yaml_value, 'one_region_per_rom', location,
                                         default=False, expected_types=YamlType.BOOL)
        region_ordering = extract_key(yaml_value, 'region_ordering', location, default=[], expected_types=YamlType.SEQ)
        return GroupByRegionConfig(one_region_per_rom, region_ordering, disable_if_single_region)


@dataclass
class GroupByPrefixConfig:
    length: int = 1
    limit: int | None = None
    create_ranges: bool = False
    disable_if_single_range: bool = False

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, group_by: list[GroupType]) -> GroupByPrefixConfig | None:
        if yaml_value is None:
            if GroupType.LETTER in group_by:
                return GroupByPrefixConfig()
            else:
                return None

        validate_type(yaml_value, YamlType.MAPPING, location)
        length = extract_key(yaml_value, 'length', location, default=1, expected_types=YamlType.INT)
        limit = extract_key(yaml_value, 'limit', location, expected_types=YamlType.INT)
        create_ranges = extract_key(yaml_value, 'create_ranges', location, default=False, expected_types=YamlType.BOOL)
        disable_if_single_range = extract_key(yaml_value, 'disable_if_single_range', location,
                                              default=False, expected_types=YamlType.BOOL)
        return GroupByPrefixConfig(length, limit, create_ranges, disable_if_single_range)
