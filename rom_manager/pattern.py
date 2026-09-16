import re
import pathlib
from typing import Any
from .common import ParseError, Location, YamlType, extract_key, extract_key_and_location, enumerate_seq, validate_type, compile_regex, normalize_unicode
from enum import StrEnum
from dataclasses import dataclass, field
from .regions import Region, lookup_region
from .rom import RomFile, is_valid_lang


class PatternType(StrEnum):
    PREFIX = 'prefix'
    SUFFIX = 'suffix'
    GLOB = 'glob'
    REGEX = 'regex'
    EXACT = 'exact'


@dataclass(frozen=True)
class Pattern:
    type: PatternType
    pattern: str
    compiled_pattern: re.Pattern | None = None
    filename_only: bool = True
    case_sensitive: bool = False

    def __post_init__(self):
        if self.type == PatternType.REGEX and self.compiled_pattern is None:
            raise ValueError("Missing compiled_pattern for regex pattern type")

        if not self.case_sensitive and self.type not in [PatternType.REGEX, PatternType.GLOB]:
            object.__setattr__(self, 'pattern', self.pattern.casefold())

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location) -> Pattern:
        validate_type(yaml_value, [YamlType.STRING, YamlType.MAPPING], location)

        if isinstance(yaml_value, str):
            return Pattern(PatternType.GLOB, yaml_value)

        pattern, pattern_loc = extract_key_and_location(yaml_value, 'pattern', location,
                                                        required=True,
                                                        expected_types=YamlType.STRING)

        raw_type, type_loc = extract_key_and_location(yaml_value, 'type', location,
                                                      default=str(PatternType.GLOB),
                                                      expected_types=YamlType.STRING)
        try:
            type = PatternType(raw_type)
        except ValueError:
            raise ParseError(
                f"Invalid pattern type '{raw_type} for field {type_loc.field}. Valid values are: {', '.join(list(PatternType))}", type_loc)

        case_sensitive = extract_key(yaml_value, 'case_sensitive', location,
                                     default=False,
                                     expected_types=YamlType.BOOL)

        filename_only = extract_key(yaml_value, 'filename_only', location,
                                    default=True,
                                    expected_types=YamlType.BOOL)

        compiled_pattern = None
        if type == PatternType.REGEX:
            compiled_pattern = compile_regex(pattern, pattern_loc)

        return Pattern(type, pattern, compiled_pattern, case_sensitive, filename_only)

    @staticmethod
    def from_yaml_list(yaml_values: list[Any], location: Location) -> list[Pattern]:
        return [Pattern.from_yaml(pattern, loc) for pattern, loc in enumerate_seq(yaml_values, location)]

    def matches(self, path: pathlib.Path) -> bool:
        if self.type == PatternType.GLOB and self.filename_only:
            return path.match(self.pattern, case_sensitive=self.case_sensitive)
        if self.type == PatternType.GLOB and not self.filename_only:
            return path.full_match(self.pattern, case_sensitive=self.case_sensitive)

        value = normalize_unicode(path.name if self.filename_only else str(path))
        if not self.case_sensitive:
            value = value.casefold()

        if self.type == PatternType.EXACT:
            return value == self.pattern
        if self.type == PatternType.PREFIX:
            return value.startswith(self.pattern)
        if self.type == PatternType.SUFFIX:
            return value.endswith(self.pattern)
        if self.type == PatternType.REGEX:
            return self.compiled_pattern.match(value) is not None # type: ignore should not be None for REGEX type

        raise ValueError(f"Unsupported pattern type \"{self.type}\"")

@dataclass(frozen=True)
class Filter:
    patterns: list[Pattern] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    langs: list[str] = field(default_factory=list)

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location) -> Filter:
        validate_type(yaml_value, YamlType.MAPPING, location)

        patterns, patterns_loc = extract_key_and_location(yaml_value, 'patterns', location,
                                                          default=[],
                                                          expected_types=YamlType.SEQ)
        raw_regions, regions_loc = extract_key_and_location(yaml_value, 'regions', location,
                                                            default=[],
                                                            expected_types=YamlType.SEQ)
        langs, langs_loc = extract_key_and_location(yaml_value, 'langs', location,
                                                    default=[],
                                                    expected_types=YamlType.SEQ)

        regions = []
        for region_name, region_name_loc in enumerate_seq(raw_regions, regions_loc):
            region = lookup_region(region_name)
            if region is None:
                raise ParseError(f"Unrecognized region \"{region_name}\".", region_name_loc)

            regions.append(region)

        for lang, lang_loc in enumerate_seq(langs, langs_loc):
            if not is_valid_lang(lang):
                raise ParseError(
                    f"Unrecognized language \"{lang}\".  Must be a two letter ISO 639 language code where the first letter is uppercase and the second is lowercase.", lang_loc)

        return Filter(
            Pattern.from_yaml_list(patterns, patterns_loc),
            regions,
            langs)

    def is_empty(self) -> bool:
        return not self.patterns and not self.regions and not self.langs

    def match_any(self, rom_file: RomFile) -> bool:
        if any(pattern.matches(rom_file.file) for pattern in self.patterns):
            return True

        if any(region in rom_file.regions for region in self.regions):
            return True

        return any(lang in rom_file.langs for lang in self.langs)

    def match_all(self, rom_file: RomFile) -> bool:
        if self.patterns and not any(pattern.matches(rom_file.file) for pattern in self.patterns):
            return False

        if self.regions and not any(region in rom_file.regions for region in self.regions):
            return False

        return not self.langs or any(lang in rom_file.langs for lang in self.langs)
