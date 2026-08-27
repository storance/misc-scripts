import re
import sys
import string
import random
import pathlib
import datetime
import unicodedata
from typing import Any
from enum import StrEnum
from collections.abc import Generator
from dataclasses import dataclass
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from rich.console import Console

class ParseError(Exception):
    def __init__(self, message: str, location: Location):
        super().__init__(message)
        self.location = location


@dataclass
class Location:
    field: str | None
    file: pathlib.Path
    line: int | None

    def __str__(self):
        if self.line is None:
            return f"\"{self.file}\""

        return f"\"{self.file}\", line {self.line}"

    def child_key(self, key: str, line: int | None) -> Location:
        if self.field is None:
            new_field = key
        else:
            new_field = f"{self.field}.{key}"

        return Location(new_field, self.file, line)

    def child_index(self, idx: int, line: int | None) -> Location:
        if self.field is None:
            new_field = f"[{idx}]"
        else:
            new_field = f"{self.field}[{idx}]"

        return Location(new_field, self.file, line)


def extract_location_for_key(mapping: dict, key: str, parent: Location) -> Location:
    if isinstance(mapping, CommentedMap):
        if key not in mapping:
            line = parent.line
        else:
            line = mapping.lc.key(key)[0]+1

        return parent.child_key(key, line)

    return parent.child_key(key, None)


def extract_location_for_index(l: list, idx: int, parent: Location) -> Location:
    if isinstance(l, CommentedSeq):
        return parent.child_index(idx, l.lc.item(idx)[0]+1)

    return parent.child_index(idx, None)


def extract_key(*args, **kwargs) -> Any:
    return extract_key_and_location(*args, **kwargs)[0]


def extract_key_and_location(mapping: dict,
                             key: str,
                             parent: Location,
                             default: Any = None,
                             expected_types: YamlType | list[YamlType] | None = None,
                             none_to_default=True,
                             required=False) -> tuple[Any, Location]:
    if required and key not in mapping:
        raise ParseError(
            f"Missing required '{key}' field in {parent.field if parent.field is not None else "the root"}.", parent)

    key_location = extract_location_for_key(mapping, key, parent)

    value = mapping.get(key, default)
    if none_to_default and value is None and default is not None:
        value = default

    if required and value is None:
        raise ParseError(
            f"A non-null value is required for {key_location.field}.", key_location)

    if expected_types is not None and not (value is None and not required):
        validate_type(value, expected_types, key_location)

    return (value, key_location)


def enumerate_seq(seq: list, parent: Location) -> Generator[tuple[Any, Location], None, None]:
    for idx, value in enumerate(seq):
        location = extract_location_for_index(seq, idx, parent)

        yield (value, location)


def enumerate_mapping(mapping: dict, parent: Location) -> Generator[tuple[str, Any, Location], None, None]:
    for key, value in mapping.items():
        location = extract_location_for_key(mapping, key, parent)

        yield (key, value, location)


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize('NFC', text)


def compile_regex(pattern: str, location: Location, case_sensitive: bool = False) -> re.Pattern:
    re_flags = re.NOFLAG if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern, re_flags)
    except re.error as e:
        raise ParseError(
            f"Invalid regular expression for field {location.field}: {e.msg}.", location)


class YamlType(StrEnum):
    NULL = 'null'
    BOOL = 'boolean'
    INT = 'integer'
    FLOAT = 'real number'
    STRING = 'string'
    BINARY = 'binary'
    SEQ = 'sequence'
    MAPPING = 'mapping'
    DATE = 'date'
    DATETIME = 'date and time'


def extract_type(value: Any) -> YamlType:
    if value is None:
        return YamlType.NULL
    elif isinstance(value, bool):
        return YamlType.BOOL
    elif isinstance(value, int):
        return YamlType.INT
    elif isinstance(value, float):
        return YamlType.FLOAT
    elif isinstance(value, str):
        return YamlType.STRING
    elif isinstance(value, bytes):
        return YamlType.BINARY
    elif isinstance(value, list):
        return YamlType.SEQ
    elif isinstance(value, dict):
        return YamlType.MAPPING
    elif isinstance(value, datetime.date):
        return YamlType.DATE
    elif isinstance(value, datetime.datetime):
        return YamlType.DATETIME
    else:
        raise ValueError(f"Unknown Type: f{type(value)}")


def validate_type(value: Any, expected_types: YamlType | list[YamlType], location: Location):
    if isinstance(expected_types, YamlType):
        expected_types = [expected_types]

    actual_type = extract_type(value)
    if actual_type not in expected_types:
        if len(expected_types) == 1:
            raise ParseError(
                f"Invalid type {actual_type} for {location.field}.  Expected type to {expected_types[0]}.", location)
        else:
            raise ParseError(
                f"Invalid type {actual_type} for {location.field}.  Expected type to be one of: {', '.join(expected_types)}.", location)

def read_yaml_file(console: Console, file: pathlib.Path) -> tuple[dict, Location]:
    try:
        yaml_parser = YAML(typ='rt')
        data = yaml_parser.load(file)
        if isinstance(data, CommentedMap):
            location = Location(None, file, data.lc.line+1)
        else:
            location = Location(None, file, None)

        return (data, location)
    except ParseError as e:
        console.log(f"[red]ERROR[/red] {e}\n  in {e.location}")
        sys.exit(1)
    except Exception as e:
        console.log(f"[red]ERROR[/red] Failed to read [magenta]\"{file}\"[/magenta]: {e}")
        sys.exit(1)

def generate_random_string(size: int):
    return ''.join(random.choices(string.ascii_lowercase, k=size))