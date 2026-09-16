import pathlib
from enum import StrEnum
from ruamel.yaml import YAML
from dataclasses import dataclass, field
from typing import Any
from .pattern import Pattern, Filter
from .common import ParseError, Location, YamlType, extract_key, extract_key_and_location, \
    enumerate_seq, enumerate_mapping, validate_type
from .metadata import Metadata, RomSet
from .regions import Region, lookup_region
from .rom import RomFile


@dataclass
class Profile:
    outputs: list[ProfileOutput]
    delete_excludes: list[Pattern]
    outputs_by_path: dict[pathlib.Path, ProfileOutput] = field(init=False)

    def __post_init__(self):
        self.outputs_by_path = {o.path: o for o in self.outputs}

    @classmethod
    def load_from_file(cls, file: pathlib.Path, metadata: Metadata) -> Profile:
        yaml_parser = YAML(typ='rt')
        data = yaml_parser.load(file)
        location = Location(None, file, data.lc.line+1)

        return cls.from_yaml(data, location, metadata)

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, metadata: Metadata) -> Profile:
        validate_type(yaml_value, YamlType.MAPPING, location)

        outputs, outputs_loc = extract_key_and_location(yaml_value, 'outputs', location,
                                                        required=True,
                                                        expected_types=YamlType.SEQ)
        delete_excludes, delete_excludes_loc = extract_key_and_location(
            yaml_value, 'delete_excludes', location, default=[], expected_types=YamlType.SEQ)

        return Profile(
            ProfileOutput.from_yaml_list(outputs, outputs_loc, metadata),
            Pattern.from_yaml_list(delete_excludes, delete_excludes_loc))

    def is_delete_excluded(self, relative_path: pathlib.Path) -> bool:
        return any(exclude.matches(relative_path) for exclude in self.delete_excludes)


@dataclass
class ProfileOutput:
    path: pathlib.Path
    grouping: GroupingConfig | None
    flatten: bool
    sources: list[ProfileSource]
    delete_excludes: list[Pattern]

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, metadata: Metadata) -> ProfileOutput:
        validate_type(yaml_value, YamlType.MAPPING, location)

        path = extract_key(yaml_value, 'path', location, required=True, expected_types=YamlType.STRING)
        grouping, grouping_loc = extract_key_and_location(
            yaml_value, 'grouping', location, expected_types=YamlType.MAPPING)
        flatten = extract_key(yaml_value, 'flatten', location,
                              default=False,
                              expected_types=YamlType.BOOL)
        sources, sources_loc = extract_key_and_location(yaml_value, 'sources', location,
                                                        required=True,
                                                        expected_types=[YamlType.STRING, YamlType.SEQ])
        delete_excludes, delete_excludes_loc = extract_key_and_location(
            yaml_value, 'delete_excludes', location, default=[], expected_types=YamlType.SEQ)

        grouping_config = GroupingConfig.from_yaml(grouping, grouping_loc)
        if grouping_config.is_enabled():
            flatten = True

        return ProfileOutput(
            pathlib.Path(path),
            grouping_config,
            flatten,
            ProfileSource.from_yaml_list(sources, sources_loc, metadata),
            Pattern.from_yaml_list(delete_excludes, delete_excludes_loc)
        )

    @classmethod
    def from_yaml_list(cls, yaml_values: list[Any], location: Location, metadata: Metadata) -> list[ProfileOutput]:
        output_paths = {}
        results = []
        for value, loc in enumerate_seq(yaml_values, location):
            profile_output = cls.from_yaml(value, loc, metadata)
            existing_loc = output_paths.get(profile_output.path)
            if existing_loc is not None:
                raise ParseError(
                    f"Duplicate output path \"{profile_output.path}\". Already defined at {existing_loc.to_compact_str()}:", loc)
            else:
                output_paths[profile_output.path] = loc
                results.append(profile_output)

        return results

    def is_delete_excluded(self, relative_path: pathlib.Path) -> bool:
        return any(exclude.matches(relative_path) for exclude in self.delete_excludes)


@dataclass(frozen=True)
class ProfileSource:
    rom_set: RomSet
    includes: Filter = field(default_factory=Filter)
    excludes: Filter = field(default_factory=Filter)

    @staticmethod
    def from_yaml(yaml_value: Any,
                  location: Location,
                  metadata: Metadata) -> ProfileSource:
        validate_type(yaml_value, [YamlType.MAPPING, YamlType.STRING], location)

        if isinstance(yaml_value, str):
            rom_set = metadata.find_rom_set(yaml_value)
            if rom_set is None:
                raise ParseError(f"Rom set \"{yaml_value}\" does not exist in the metadata.yml", location)

            return ProfileSource(rom_set)

        rom_set_name, rom_set_loc = extract_key_and_location(yaml_value, 'rom_set', location,
                                                             required=True,
                                                             expected_types=YamlType.STRING)
        rom_set = metadata.find_rom_set(rom_set_name)
        if rom_set is None:
            raise ParseError(f"Rom set \"{yaml_value}\" does not exist in the metadata.yml", rom_set_loc)

        includes, includes_loc = extract_key_and_location(yaml_value, 'includes', location,
                                                          default={},
                                                          expected_types=YamlType.MAPPING)
        excludes, excludes_loc = extract_key_and_location(yaml_value, 'excludes', location,
                                                          default={},
                                                          expected_types=YamlType.MAPPING)

        return ProfileSource(rom_set,
                             Filter.from_yaml(includes, includes_loc),
                             Filter.from_yaml(excludes, excludes_loc))

    @classmethod
    def from_yaml_list(cls, yaml_values: Any, location: Location, metadata: Metadata) -> list[ProfileSource]:
        validate_type(yaml_values, [YamlType.SEQ, YamlType.STRING], location)

        if isinstance(yaml_values, str):
            return [ProfileSource.from_yaml(yaml_values, location, metadata)]
        return [cls.from_yaml(value, loc, metadata) for value, loc in enumerate_seq(yaml_values, location)]

    def is_excluded(self, rom_file: RomFile) -> bool:
        return self.excludes.match_any(rom_file)

    def is_included(self, rom_file: RomFile) -> bool:
        return self.includes.match_all(rom_file)


class GroupType(StrEnum):
    GAME = 'game'
    LANG = 'lang'
    REGION = 'region'
    PREFIX = 'prefix'


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
    flatten_single_rom: bool = False

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
        flatten_single_rom = extract_key(yaml_value, 'flatten_single_rom',
                                         location, default=False, expected_types=YamlType.BOOL)

        custom_mapping_raw, custom_mapping_loc = extract_key_and_location(
            yaml_value, 'custom_mappings', location, default={}, expected_types=YamlType.MAPPING)

        custom_mapping = {
            key: Pattern.from_yaml_list(value, key_loc)
            for key, value, key_loc in enumerate_mapping(custom_mapping_raw, custom_mapping_loc)
        }
        return GroupByGameConfig(strip_regions, strip_langs, custom_mapping, flatten_single_rom)


@dataclass
class GroupByLangConfig:
    single_per_rom: bool = False
    prefer: list[str] = field(default_factory=list)
    flatten_single_lang: bool = False

    prefer_ranks: dict[str, int] = field(init=False)

    def __post_init__(self):
        self.prefer_ranks = {
            lang: rank for rank, lang in enumerate(self.prefer)
        }

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, group_by: list[GroupType]) -> GroupByLangConfig | None:
        if yaml_value is None:
            if GroupType.LANG in group_by:
                return GroupByLangConfig()
            else:
                return None

        validate_type(yaml_value, YamlType.MAPPING, location)
        flatten_single_lang = extract_key(yaml_value, 'flatten_single_lang', location,
                                          default=False, expected_types=YamlType.BOOL)
        single_per_rom = extract_key(yaml_value, 'single_per_rom', location,
                                     default=False, expected_types=YamlType.BOOL)
        prefer, prefer_loc = extract_key_and_location(
            yaml_value, 'prefer', location, default=[], expected_types=YamlType.SEQ)

        if single_per_rom and len(prefer) == 0:
            raise ParseError('You must specify a prefer list of languages when single_per_rom is enabled', prefer_loc)
        return GroupByLangConfig(single_per_rom, prefer, flatten_single_lang)


class MultiRegionMode(StrEnum):
    PRESERVE = 'preserve'
    EXPAND_TO_COUNTRIES = 'expand-to-countries'
    COLLAPSE_TO_GROUPED = 'collapse-to-grouped'

    @staticmethod
    def from_yaml(yaml_value: str, location: Location) -> MultiRegionMode:
        try:
            return MultiRegionMode(yaml_value)
        except ValueError:
            raise ParseError(f"Unknown region handling '{yaml_value}", location)


@dataclass
class GroupByRegionConfig:
    single_per_rom: bool = False
    prefer: list[Region] = field(default_factory=list)
    flatten_single_region: bool = False
    use_short_names: bool = False
    world_mode: MultiRegionMode = MultiRegionMode.PRESERVE
    europe_mode: MultiRegionMode = MultiRegionMode.PRESERVE
    asia_mode: MultiRegionMode = MultiRegionMode.PRESERVE
    prefer_ranks: dict[Region, int] = field(init=False)

    def __post_init__(self):
        self.prefer_ranks = {
            lang: rank for rank, lang in enumerate(self.prefer)
        }

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, group_by: list[GroupType]) -> GroupByRegionConfig | None:
        if yaml_value is None:
            if GroupType.REGION in group_by:
                return GroupByRegionConfig()
            else:
                return None

        validate_type(yaml_value, YamlType.MAPPING, location)
        flatten_single_region = extract_key(yaml_value, 'flatten_single_region', location,
                                            default=False, expected_types=YamlType.BOOL)
        single_per_rom = extract_key(yaml_value, 'single_per_rom', location,
                                     default=False, expected_types=YamlType.BOOL)
        use_short_names = extract_key(yaml_value, 'use_short_names', location,
                                      default=False, expected_types=YamlType.BOOL)
        world_mode = MultiRegionMode.from_yaml(*extract_key_and_location(
            yaml_value, 'world_mode', location, default=MultiRegionMode.PRESERVE))
        europe_mode = MultiRegionMode.from_yaml(*extract_key_and_location(
            yaml_value, 'europe_mode', location, default=MultiRegionMode.PRESERVE))
        asia_mode = MultiRegionMode.from_yaml(*extract_key_and_location(
            yaml_value, 'asia_mode', location, default=MultiRegionMode.PRESERVE))
        raw_prefer, prefer_loc = extract_key_and_location(
            yaml_value, 'prefer', location, default=[], expected_types=YamlType.SEQ)

        prefer = []
        for region_name in raw_prefer:
            region = lookup_region(region_name)
            if region is None:
                raise ParseError(f"Unsupported or invalid region \"{region_name}\"", prefer_loc)
            prefer.append(region)

        if single_per_rom and len(prefer) == 0:
            raise ParseError('You must specify a prefer list of regions when single_per_rom is enabled', prefer_loc)
        return GroupByRegionConfig(single_per_rom, prefer, flatten_single_region, use_short_names, world_mode, europe_mode, asia_mode)


@dataclass
class GroupByPrefixConfig:
    length: int = 1
    limit: int | None = None
    collapse_prefixes: bool = False
    flatten_single_prefix: bool = False

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, group_by: list[GroupType]) -> GroupByPrefixConfig | None:
        if yaml_value is None:
            if GroupType.PREFIX in group_by:
                return GroupByPrefixConfig()
            else:
                return None

        validate_type(yaml_value, YamlType.MAPPING, location)
        length = extract_key(yaml_value, 'length', location, default=1, expected_types=YamlType.INT)
        limit = extract_key(yaml_value, 'limit', location, expected_types=YamlType.INT)
        collapse_prefixes = extract_key(yaml_value, 'collapse_prefixes', location,
                                        default=False, expected_types=YamlType.BOOL)
        flatten_single_prefix = extract_key(yaml_value, 'flatten_single_prefix', location,
                                            default=False, expected_types=YamlType.BOOL)

        if limit is None and collapse_prefixes:
            raise ParseError("A limit must be specified when collapse prefixes is enabled", location)

        return GroupByPrefixConfig(length, limit, collapse_prefixes, flatten_single_prefix)
