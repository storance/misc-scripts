import pathlib
import logging
from dataclasses import dataclass

from .dat import GameRomPair
from .cue import rename_bin_files_in_cue
from .. import Game, Rom, rename_file


@dataclass
class CueFile:
    cue_file: pathlib.Path
    bin_files: list[pathlib.Path]


@dataclass(frozen=True)
class SyncFolder:
    path: pathlib.Path
    ext: str


class RenameTask:
    def execute(self):
        pass

    def dry_run(self):
        pass


class RenameSingleRomTask(RenameTask):
    def __init__(self, old_file: pathlib.Path, new_file: pathlib.Path):
        self.old_file = old_file
        self.new_file = new_file

    def execute(self):
        logging.info("Renaming file \"%s\" to \"%s\".", self.old_file, self.new_file.name)
        try:
            rename_file(self.old_file, self.new_file)
        except OSError as e:
            logging.exception("Failed to rename file \"%s\" to \"%s\": %s.", self.old_file, self.new_file.name, str(e))

    def dry_run(self):
        logging.info("DRY RUN: Renaming file \"%s\" to \"%s\".", self.old_file, self.new_file.name)


class RenameCueTask(RenameTask):
    def __init__(self,
                 old_cue_file: pathlib.Path,
                 new_cue_file: pathlib.Path,
                 bin_file_renames: dict[pathlib.Path, pathlib.Path]):
        self.old_cue_file = old_cue_file
        self.new_cue_file = new_cue_file
        self.bin_file_renames = bin_file_renames

    def execute(self):
        if self.old_cue_file.name != self.new_cue_file.name:
            try:
                logging.info("Renaming cue file \"%s\" to \"%s\".", self.old_cue_file, self.new_cue_file.name)
                rename_file(self.old_cue_file, self.new_cue_file)
            except OSError as e:
                logging.exception("Failed to rename cue file \"%s\" to \"%s\": %s.",
                                  self.old_cue_file, self.new_cue_file.name, str(e))

        for old_file, new_file in self.bin_file_renames.items():
            try:
                logging.info("Renaming bin file \"%s\" to \"%s\".", old_file, new_file.name)
                rename_file(old_file, new_file)
            except OSError as e:
                logging.exception("Failed to rename bin file \"%s\" to \"%s\": %s.",
                                  old_file, new_file.name, str(e))

        if self.bin_file_renames:
            logging.info("Updating cue file \"%s\" with renamed bin files.", self.new_cue_file)
            relative_rename_mapping = {
                str(old.relative_to(self.old_cue_file.parent)): str(new.relative_to(self.old_cue_file.parent))
                for old, new in self.bin_file_renames.items()
            }
            try:
                rename_bin_files_in_cue(self.new_cue_file, relative_rename_mapping)
            except Exception as e:
                logging.exception("Failed to update cue file \"%s\": %s", self.new_cue_file, str(e))

    def dry_run(self):
        if self.old_cue_file.name != self.new_cue_file.name:
            logging.info("DRY RUN: Renaming cue file \"%s\" to \"%s\".", self.old_cue_file, self.new_cue_file.name)

        if self.bin_file_renames:
            for old_file, new_file in self.bin_file_renames.items():
                logging.info("DRY RUN: Renaming bin file \"%s\" to \"%s\".", old_file, new_file)

            logging.info("DRY RUN: Updating cue file \"%s\" with renamed bin files.", self.new_cue_file)


def build_rename_tasks(rom_files: list[pathlib.Path | CueFile],
                       games_by_hash: dict[str, list[GameRomPair]],
                       hashes_by_path: dict[pathlib.Path, str],
                       sync_folders: list[SyncFolder]) -> list[RenameTask]:
    results = []
    for rom_file in rom_files:
        tasks = None
        if isinstance(rom_file, CueFile):
            tasks = _build_cue_rename_task(rom_file, games_by_hash, hashes_by_path, sync_folders)
        elif isinstance(rom_file, pathlib.Path):
            tasks = _build_single_rename_task(rom_file, games_by_hash, hashes_by_path, sync_folders)

        if tasks is not None:
            results.extend(tasks)

    return results


def _build_single_rename_task(file: pathlib.Path,
                              games_by_hash: dict[str, list[GameRomPair]],
                              hashes_by_path: dict[pathlib.Path, str],
                              sync_folders: list[SyncFolder]) -> list[RenameTask] | None:
    sha1 = hashes_by_path.get(file)

    if sha1 is None:
        logging.debug("Skipping \"%s\" as a sha1 was not calculated for it.", file)
        return None

    if sha1 not in games_by_hash:
        logging.error("No dat entry found for file \"%s\" with sha1 hash: %s.", file, sha1)
        return None

    game_and_roms = games_by_hash[sha1]
    if len(game_and_roms) > 1:
        logging.warning("Multiple dat entries found for file \"%s\" with sha1 hash %s. Skipping...", file, sha1)
        return None

    rom = game_and_roms[0].rom
    if file.name == rom.name:
        logging.debug("Skipping file \"%s\" as it already matches dat file entry.", file)
        return None

    new_name = file.with_name(rom.name)
    tasks: list[RenameTask] = [RenameSingleRomTask(file, new_name)]
    tasks.extend(_sync_rename(file.name, new_name.name, sync_folders))
    return tasks


def _build_cue_rename_task(rom: CueFile,
                           games_by_hash: dict[str, list[GameRomPair]],
                           hashes_by_path: dict[pathlib.Path, str],
                           sync_folders: list[SyncFolder]):
    bin_file_hashes = {
        bin_file: hashes_by_path.get(bin_file)
        for bin_file in rom.bin_files
    }

    game = None
    for bin_file, sha1 in bin_file_hashes.items():
        if sha1 is None:
            logging.debug("Skipping \"%s\" as a sha1 was not calculated for it.", bin_file)
            continue

        if sha1 not in games_by_hash:
            logging.error("No dat entry found for file \"%s\" with sha1 hash: %s.", bin_file, sha1)
            return None

        games = games_by_hash[sha1]
        # The audio track bin files can be the same across the regional variants of a game, but there is at least one
        # bin file that is unique to that regional variant.  That unique mapping is the one we want.
        if len(games) == 1:
            game = games[0].game
            break

    if game is None:
        logging.warning("Multiple dat entries found for all bin files found in \"%s\". Skipping...", rom.cue_file)
        return None

    rename_mapping = {}
    for bin_file, sha1 in bin_file_hashes.items():
        if sha1 is None:
            continue

        dat_rom = _find_rom(sha1, game)
        if dat_rom is None:
            logging.error("Bin file \"%s\" with sha1 hash %s does not match any rom entry for \"%s\".",
                          bin_file, sha1, game.name)
            return None

        if bin_file.name == dat_rom.name:
            logging.debug("Skipping bin file \"%s\" as it already matches dat file entry.", bin_file)
            continue

        rename_mapping[bin_file] = bin_file.with_name(dat_rom.name)

    new_cue_file = rom.cue_file.with_name(game.name + '.cue')
    if len(rename_mapping) == 0 and new_cue_file.name == rom.cue_file.name:
        logging.debug(
            "Skipping file \"%s\" as it already matches dat file entry and none of it's bin files were renamed.", rom.cue_file)
        return None

    tasks: list[RenameTask] = [
        RenameCueTask(rom.cue_file, new_cue_file, rename_mapping)
    ]
    tasks.extend(_sync_rename(rom.cue_file.name, new_cue_file.name, sync_folders))
    return tasks


def _find_rom(sha1: str, game: Game) -> Rom | None:
    for rom in game.roms:
        if rom.sha1 == sha1:
            return rom

    return None


def _sync_rename(old_name: str, new_name: str, sync_folders: list[SyncFolder]):
    results = []
    for sync_folder in sync_folders:
        sync_file = (sync_folder.path / old_name).with_suffix(sync_folder.ext)
        if not sync_file.exists():
            continue

        new_sync_file = sync_file.with_name(new_name).with_suffix(sync_folder.ext)
        results.append(RenameSingleRomTask(sync_file, new_sync_file))

    return results
