import re
import pathlib
from ruamel.yaml import YAML
from dataclasses import dataclass, field
from typing import Any
from .pattern import Pattern
from .common import ParseError, Location, YamlType, extract_key, extract_key_and_location, \
    enumerate_seq, enumerate_mapping, validate_type, compile_regex
from .metadata import Metadata, RomSet


DEFAULT_GAME_NAME_EXTRACTOR = re.compile(r'^(.+?)(?:\s*\(.+\)\s*)*\..+$', re.IGNORECASE)
DEFAULT_REPLACEMENT = r'\1'


@dataclass
class Profile:
    root_folder: pathlib.Path | None
    rom_sets: list[ProfileRomSetConfig]
    delete_excludes: list[Pattern]
    _interested_exts: set[str] | None = field(default=None, init=False)

    @classmethod
    def load_from_file(cls, file: pathlib.Path, metadata: Metadata) -> Profile:
        yaml_parser = YAML(typ='rt')
        data = yaml_parser.load(file)
        location = Location(None, file, data.lc.line+1)

        return cls.from_yaml(data, location, metadata)

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location, metadata: Metadata) -> Profile:
        validate_type(yaml_value, YamlType.MAPPING, location)

        root_folder = extract_key(yaml_value, 'root_folder', location, expected_types=YamlType.STRING)
        if root_folder is not None:
            root_folder = pathlib.Path(root_folder)

        raw_rom_folders, rom_folders_loc = extract_key_and_location(yaml_value, 'rom_folders', location,
                                                                    required=True,
                                                                    expected_types=YamlType.SEQ)
        rom_folders = [ProfileRomSetConfig.from_yaml(rom_folder, loc, root_folder, metadata)
                       for rom_folder, loc in enumerate_seq(raw_rom_folders, rom_folders_loc)]

        delete_excludes, delete_excludes_loc = extract_key_and_location(yaml_value, 'delete_excludes', location,
                                                                        default=[])

        return Profile(root_folder, rom_folders, Pattern.from_yaml_list(delete_excludes, delete_excludes_loc))

    @property
    def interested_exts(self) -> set[str]:
        if self._interested_exts is None:
            self._interested_exts = set()
            for rfc in self.rom_sets:
                self._interested_exts.update(rfc.rom_set.extensions)

        return self._interested_exts

    def is_interested_ext(self, file: pathlib.Path) -> bool:
        return any(file.name.endswith(ext) for ext in self.interested_exts)

    def is_include_for_delete(self, file: pathlib.Path) -> bool:
        if not self.is_interested_ext(file):
            return False

        return not any(exclude.matches(file) for exclude in self.delete_excludes)


@dataclass(frozen=True)
class ProfileRomSetConfig:
    rom_set: RomSet
    destination: pathlib.Path
    includes: list[Pattern]
    excludes: list[Pattern]
    folder_per_game: FolderPerGameConfig
    flatten: bool

    @staticmethod
    def from_yaml(yaml_value: dict,
                  location: Location,
                  root_folder: pathlib.Path | None,
                  metadata: Metadata) -> ProfileRomSetConfig:
        validate_type(yaml_value, YamlType.MAPPING, location)

        name, name_loc = extract_key_and_location(yaml_value, 'name', location,
                                                  required=True,
                                                  expected_types=YamlType.STRING)
        rom_set = metadata.find_rom_set(name)
        if rom_set is None:
            raise ParseError(
                f"A rom set with the name '{name}' does not exist in the metadata.yml", name_loc)

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

        return ProfileRomSetConfig(rom_set,
                                   _build_destination(rom_set, root_folder, destination),
                                   Pattern.from_yaml_list(includes, includes_loc),
                                   Pattern.from_yaml_list(excludes, excludes_loc),
                                   fpg_config,
                                   flatten)

    def get_relative_destination(self, src_relative_path: pathlib.Path) -> pathlib.Path:
        if self.flatten or self.folder_per_game.enabled:
            src_relative_path = pathlib.Path(src_relative_path.name)

        if self.folder_per_game.enabled:
            folder_name = self.folder_per_game.extract_game_name(src_relative_path)
            return self.destination / folder_name / src_relative_path
        else:
            return self.destination / src_relative_path

    def is_excluded(self, relative_path: pathlib.Path) -> bool:
        return any(exclude.matches(relative_path) for exclude in self.excludes)

    def is_included(self, relative_path: pathlib.Path) -> bool:
        if not self.includes:
            return True

        return any(include.matches(relative_path) for include in self.includes)


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
    case_sensitive: bool = False

    @staticmethod
    def from_yaml(yaml_value: Any, location: Location) -> GameNameExtractorConfig:
        validate_type(yaml_value, [YamlType.STRING, YamlType.MAPPING], location)

        if isinstance(yaml_value, str):
            return GameNameExtractorConfig(compile_regex(yaml_value, location, False))

        pattern, pattern_loc = extract_key_and_location(yaml_value, 'pattern', location,
                                                        default=DEFAULT_GAME_NAME_EXTRACTOR.pattern,
                                                        expected_types=YamlType.STRING)
        replacement = extract_key(yaml_value, 'replacement', location,
                                  default=DEFAULT_REPLACEMENT,
                                  expected_types=YamlType.STRING)
        case_sensitive = extract_key(yaml_value, 'case_sensitive', location,
                                     default=False,
                                     expected_types=YamlType.BOOL)
        compiled_pattern = compile_regex(pattern, pattern_loc, case_sensitive)

        return GameNameExtractorConfig(compiled_pattern, replacement, case_sensitive)

    def extract(self, path: pathlib.Path) -> str:
        return self.pattern.sub(self.replacement, path.name)


def _build_destination(rom_folder: RomSet, root_folder: pathlib.Path | None, destination: str | None) -> pathlib.Path:
    if destination is None:
        destination = rom_folder.path

    if destination.startswith('/'):
        return pathlib.Path(destination[1:])
    elif root_folder is None:
        return pathlib.Path(destination)
    else:
        return root_folder / pathlib.Path(destination)
