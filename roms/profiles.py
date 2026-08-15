import re
import pathlib
from typing import Any
from .pattern import Pattern
from .common import ParseError, Location, YamlType, extract_key, extract_key_and_location, enumerate_seq, enumerate_mapping, validate_type, normalize_unicode
from .metadata import Metadata, RomFolder
from dataclasses import dataclass


DEFAULT_GAME_NAME_EXTRACTOR = re.compile(r'^(.+?)(?:\s*\(.+\)\s*)*\..+$')
DEFAULT_REPLACEMENT = r'\1'


@dataclass
class Profile:
    root_folder: pathlib.Path | None
    rom_folders: list[ProfileRomFolderConfig]
    delete_excludes: list[Pattern]

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, metadata: Metadata) -> Profile:
        validate_type(yaml_value, YamlType.MAPPING, location)

        root_folder = extract_key(yaml_value, 'root_folder', location, expected_types=YamlType.STRING)
        if root_folder is not None:
            root_folder = pathlib.Path(normalize_unicode(root_folder))

        raw_rom_folders, rom_folders_loc = extract_key_and_location(yaml_value, 'rom_folders', location,
                                                                required=True,
                                                                expected_types=YamlType.SEQ)
        rom_folders = [ProfileRomFolderConfig.from_yaml(rom_folder, loc, root_folder, metadata)
                       for rom_folder, loc in enumerate_seq(raw_rom_folders, rom_folders_loc)]

        delete_excludes, delete_excludes_loc = extract_key_and_location(yaml_value, 'delete_excludes', location,
                                                                        default=[])

        return Profile(root_folder, rom_folders, Pattern.from_yaml_list(delete_excludes, delete_excludes_loc))


@dataclass(frozen=True)
class ProfileRomFolderConfig:
    rom_folder: RomFolder
    destination: pathlib.Path
    includes: list[Pattern]
    excludes: list[Pattern]
    folder_per_game: FolderPerGameConfig
    flatten: bool

    @staticmethod
    def from_yaml(yaml_value: dict,
                  location: Location,
                  root_folder: pathlib.Path | None,
                  metadata: Metadata) -> ProfileRomFolderConfig:
        validate_type(yaml_value, YamlType.MAPPING, location)

        name, name_loc = extract_key_and_location(yaml_value, 'name', location,
                                                  required=True,
                                                  expected_types=YamlType.STRING)
        rom_folder = metadata.find_rom_folder(name)
        if rom_folder is None:
            raise ParseError(
                f"A rom folder with the name '{name}' does not exist in the metadata.yml", name_loc)

        destination = extract_key(yaml_value, 'destination', location, expected_types=YamlType.STRING)
        includes, includes_loc = extract_key_and_location(yaml_value, 'includes', location,
                                                          default=[],
                                                          expected_types=YamlType.SEQ)
        excludes, excludes_loc = extract_key_and_location(yaml_value, 'excludes', location,
                                                          default=[],
                                                          expected_types=YamlType.SEQ)

        fpg_config, fpg_loc = extract_key_and_location(yaml_value, 'folder_per_game', location)
        if fpg_config is None:
            fpg_config = FolderPerGameConfig.disabled()
        else:
            fpg_config = FolderPerGameConfig.from_yaml(fpg_config, fpg_loc)

        flatten = extract_key(yaml_value, 'flatten', location, default=False, expected_types=YamlType.BOOL)

        return ProfileRomFolderConfig(rom_folder,
                                      _build_destination(rom_folder, root_folder, destination),
                                      Pattern.from_yaml_list(includes, includes_loc),
                                      Pattern.from_yaml_list(excludes, excludes_loc),
                                      fpg_config,
                                      flatten)

    def resolve_destination(self, dest_path: pathlib.Path) -> pathlib.Path:
        resolved_dest = (dest_path / self.destination).resolve()

        if not resolved_dest.is_relative_to(dest_path):
            raise ValueError(f"Destination path {resolved_dest} is not a relative path to the destination folder")

        return resolved_dest


@dataclass
class FolderPerGameConfig:
    enabled: bool
    game_name_extractor: GameNameExtractorConfig
    overrides: dict[str, list[Pattern]]

    @staticmethod
    def disabled() -> FolderPerGameConfig:
        return FolderPerGameConfig(False, GameNameExtractorConfig(), {})

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location) -> FolderPerGameConfig:
        validate_type(yaml_value, [YamlType.BOOL, YamlType.MAPPING], location)

        if isinstance(yaml_value, bool):
            return FolderPerGameConfig(yaml_value, GameNameExtractorConfig(), {})

        enabled = extract_key(yaml_value, 'enabled', location, required=True, expected_types=YamlType.BOOL)
        raw_gne, gne_loc = extract_key_and_location(yaml_value, 'game_name_extractor', location)
        if raw_gne is None:
            gne = GameNameExtractorConfig()
        else:
            gne = GameNameExtractorConfig.from_yaml(raw_gne, gne_loc)

        raw_overrides, overrides_loc = extract_key_and_location(yaml_value, 'overrides', location,
                                                                default={},
                                                                expected_types=YamlType.MAPPING)

        overrides = {key: Pattern.from_yaml_list(value, key_loc)
                     for key, value, key_loc in enumerate_mapping(raw_overrides, overrides_loc)}
        return FolderPerGameConfig(enabled, gne, overrides)

    def extract_game_name(self, path: pathlib.Path) -> str:
        if not self.enabled:
            raise ValueError("Folder per game is not enabled.")

        for (name, matchers) in self.overrides.items():
            if any(matcher.matches(path) for matcher in matchers):
                return name

        return self.game_name_extractor.extract(path)


@dataclass
class GameNameExtractorConfig:
    pattern: re.Pattern = DEFAULT_GAME_NAME_EXTRACTOR
    replacement: str = DEFAULT_REPLACEMENT

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location) -> GameNameExtractorConfig:
        validate_type(yaml_value, [YamlType.STRING,
                      YamlType.MAPPING], location)

        if isinstance(yaml_value, str):
            return GameNameExtractorConfig(_compile_regex(yaml_value, location))

        pattern, pattern_loc = extract_key_and_location(yaml_value, 'pattern', location,
                                                        default=DEFAULT_GAME_NAME_EXTRACTOR.pattern,
                                                        expected_types=YamlType.STRING)
        replacement = extract_key(yaml_value, 'replacement', location,
                                  default=DEFAULT_REPLACEMENT,
                                  expected_types=YamlType.STRING)
        compiled_pattern = _compile_regex(pattern, pattern_loc)

        return GameNameExtractorConfig(compiled_pattern, replacement)

    def extract(self, path: pathlib.Path) -> str:
        return self.pattern.sub(self.replacement, path.name)


def _build_destination(rom_folder: RomFolder, root_folder: pathlib.Path | None, destination: str | None) -> pathlib.Path:
    if destination is None:
        destination = rom_folder.path
    else:
        destination = normalize_unicode(destination)
    
    if destination.startswith('/'):
        return pathlib.Path(destination[1:])
    elif root_folder is None:
        return pathlib.Path(destination)
    else:
        return root_folder / pathlib.Path(destination)


def _compile_regex(pattern: str, location: Location) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error as e:
        raise ParseError(
            f"Invalid regular expression for field {location.field}: {e.msg}.", location)
