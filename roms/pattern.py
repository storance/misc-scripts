import re
import fnmatch
import pathlib
from typing import Any
from .common import ParseError, Location, YamlType, extract_key, extract_key_and_location, enumerate_seq, validate_type
from enum import StrEnum
from dataclasses import dataclass, field


class MatchType(StrEnum):
    PREFIX = 'prefix'
    SUFFIX = 'suffix'
    GLOB = 'glob'
    REGEX = 'regex'
    EXACT = 'exact'


@dataclass(frozen=True)
class Pattern:
    type: MatchType
    pattern: str
    case_sensitive: bool
    compiled_pattern: re.Pattern = field(init=False)

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location) -> Pattern:
        validate_type(yaml_value, [YamlType.STRING,
                      YamlType.MAPPING], location)

        if isinstance(yaml_value, str):
            try:
                return Pattern(MatchType.GLOB, yaml_value, False)
            except re.error as e:
                raise ParseError(f"Invalid pattern for {location.field}: {e.msg}.", location)

        pattern, pattern_loc = extract_key_and_location(
            yaml_value, 'pattern', location, expected_types=YamlType.STRING, required=True)

        raw_type, type_loc = extract_key_and_location(yaml_value, 'type', location, default=str(
            MatchType.GLOB), expected_types=YamlType.STRING)
        try:
            type = MatchType(yaml_value.get('type', raw_type))
        except ValueError:
            raise ParseError(
                f"Invalid value '{raw_type} for field {type_loc.field}. Valid values are: {', '.join(list(MatchType))}", type_loc)

        case_sensitive = extract_key(
            yaml_value, 'case_sensitive', location, default=False, expected_types=YamlType.BOOL)
        try:
            return Pattern(type, pattern, case_sensitive)
        except re.error as e:
            raise ParseError(f"Invalid pattern for {pattern_loc.field}: {e.msg}.", pattern_loc)

    @staticmethod
    def from_yaml_list(yaml_values: list[Any], location: Location) -> list[Pattern]:
        return [Pattern.from_yaml(pattern, loc) for pattern, loc in enumerate_seq(yaml_values, location)]

    def __post_init__(self):
        re_flags = re.NOFLAG if self.case_sensitive else re.IGNORECASE
        compiled_pattern = None
        if self.type == MatchType.REGEX:
            compiled_pattern = re.compile(self.pattern, re_flags)
        elif self.type == MatchType.GLOB:
            compiled_pattern = re.compile(
                fnmatch.translate(self.pattern), re_flags)
        elif self.type == MatchType.PREFIX:
            compiled_pattern = re.compile(
                '^' + re.escape(self.pattern) + '.*$', re_flags)
        elif self.type == MatchType.SUFFIX:
            compiled_pattern = re.compile(
                '^.*?' + re.escape(self.pattern) + '$', re_flags)
        elif self.type == MatchType.EXACT:
            # a bit overkill to make this a regex but it simplifies the matches method
            compiled_pattern = re.compile(
                '^' + re.escape(self.pattern) + '$', re_flags)
        else:
            raise ValueError(f"Unsupported match type: {self.type}")

        object.__setattr__(self, "compiled_pattern", compiled_pattern)

    def matches(self, path: pathlib.Path) -> bool:
        return self.compiled_pattern.match(path.name) is not None
