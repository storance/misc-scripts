import pathlib
import logging
import sys
from dataclasses import dataclass
from .. import load_rom_dat, Game, Rom

@dataclass(frozen=True)
class GameRomPair:
    game: Game
    rom: Rom


def load_dat_files(dat_files: list[pathlib.Path]) -> dict[str, list[GameRomPair]]:
    games_by_sha1 = {}
    for file in dat_files:
        if not file.exists():
            logging.error("Dat file \"%s\" does not exist.", file)
            sys.exit(1)

        logging.info(f"Loading dat file \"{file}\".")
        try:
            dat = load_rom_dat(file)
            for game in dat.games:
                for rom in game.roms:
                    if rom.sha1 is None:
                        logging.error("Missing sha1 hash for rom %s in dat file \"%s\".", rom.name, file)
                        sys.exit(1)

                    if rom.sha1 not in games_by_sha1:
                        games_by_sha1[rom.sha1] = []

                    games_by_sha1[rom.sha1].append(GameRomPair(game, rom))
        except Exception as e:
            logging.exception("Failed to load dat file \"%s\": %s", file, str(e))
            sys.exit(1)

    return games_by_sha1
