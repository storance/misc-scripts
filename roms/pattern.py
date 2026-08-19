import re
import fnmatch
import pathlib
from typing import Any
from .common import ParseError, Location, YamlType, extract_key, extract_key_and_location, enumerate_seq, validate_type, compile_regex
from enum import StrEnum
from dataclasses import dataclass


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

        value = path.name if self.filename_only else str(path)
        if not self.case_sensitive:
            value = value.casefold()

        if self.type == PatternType.EXACT:
            return value == self.pattern
        if self.type == PatternType.PREFIX:
            return value.startswith(self.pattern)
        if self.type == PatternType.SUFFIX:
            return value.endswith(self.pattern)
        if self.type == PatternType.REGEX:
            return self.compiled_pattern.match(value) is not None

        raise ValueError(f"Unsupported pattern type \"{self.type}\"")
