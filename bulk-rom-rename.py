#! /usr/bin/env python3

from roms.dat import load_rom_dat, Game, Rom
import pathlib
import hashlib
import argparse
import re
import os
import sys

from dataclasses import dataclass

CUE_FILE_PATTERN = re.compile(r'(FILE\s+")([^"]+\.bin)("\s+BINARY)', re.IGNORECASE)
SHA1_EXT = '.sha1'


@dataclass(frozen=True)
class GameRomPair:
    game: Game
    rom: Rom


@dataclass(frozen=True)
class SyncFolder:
    path: pathlib.Path
    ext: str


def main():
    parser = argparse.ArgumentParser(
        description="Bulk renames ROM files to match names in Redump or No-Intro databases.")
    parser.add_argument('-d', '--dat-file',
                        type=pathlib.Path,
                        action='append',
                        help='Location of the dat file containing rom hashes and filenames.')
    parser.add_argument('-e', '--extension',
                        default='iso',
                        help='Extension of the rom files too look at.  For example: iso, chd, cue.  For bin/cue files use cue as the extension.')
    parser.add_argument('-s', '--sync-folder',
                        default=[],
                        nargs=2,
                        metavar=('PATH', 'EXT'),
                        action='append',
                        help='Syncs the filenames in the folder with the given extension. This is useful when renaming .iso files and you want to keep the .chd file in sync, for example.')
    parser.add_argument('-D', '--dry-run',
                        action="store_true",
                        help="Run in dry-run mode.")
    parser.add_argument('input_directory',
                        help='Input directory containing the roms to rename.')

    args = parser.parse_args()

    input_directory = pathlib.Path(args.input_directory)
    if not input_directory.exists():
        print(f"Input directory \"{args.input_directory}\" does not exist.", file=sys.stderr)
        sys.exit(1)

    print("Loading dat files...")
    try:
        games_by_sha1 = load_dat_files(args.dat_file)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    sync_folders = [SyncFolder(pathlib.Path(path), normalize_ext(ext)) for path, ext in args.sync_folder]
    for sync_folder in sync_folders:
        if sync_folder.ext == '.cue':
            print("Error: Syncing cue files is not supported.", file=sys.stderr)
            sys.exit(1)

    file_suffix = normalize_ext(args.extension)
    print(f"Scanning \"{input_directory}\" for files with ext {file_suffix}")
    for file in input_directory.iterdir():
        if not file.is_file() or file.suffix != file_suffix or file.name[0] == '.':
            continue

        if file_suffix == '.cue':
            handle_cue_file(file, sync_folders, games_by_sha1, args.dry_run)
        else:
            handle_single_file_rom(file, sync_folders, games_by_sha1, args.dry_run)


def normalize_ext(ext: str) -> str:
    return ext if ext[0] == '.' else f".{ext}"


def handle_single_file_rom(file: pathlib.Path,
                           sync_folders: list[SyncFolder],
                           games_by_sha1: dict[str, list[GameRomPair]],
                           dry_run: bool):
    sha1 = calc_sha1_hash(file)
    if sha1 not in games_by_sha1:
        print(f"Error: No dat entry found for file \"{file.name}\" with sha1 hash: {sha1}", file=sys.stderr)
        return

    game_and_roms = games_by_sha1[sha1]
    if len(game_and_roms) > 1:
        print(
            f"Warning: Multiple dat entries found for file \"{file.name}\" with sha1 hash {sha1}. Skipping...", file=sys.stderr)
        return
    game_and_rom = game_and_roms[0]

    if file.name == game_and_rom.rom.name:
        return

    new_name = file.with_name(game_and_rom.rom.name)
    print(f"Renaming \"{file}\" -> \"{new_name.name}\"")
    if not dry_run:
        rename_file(file, new_name)

    sync_rename(file.name, new_name.name, sync_folders, dry_run)


def sync_rename(old_name: str, new_name: str, sync_folders: list[SyncFolder], dry_run: bool):
    for sync_folder in sync_folders:
        sync_file = (sync_folder.path / old_name).with_suffix(sync_folder.ext)
        if not sync_file.exists():
            continue

        new_sync_file = sync_file.with_name(new_name).with_suffix(sync_folder.ext)
        print(f"\tSyncing \"{sync_file}\" -> \"{new_sync_file.name}\"")
        if not dry_run:
            rename_file(sync_file, new_sync_file)


def load_dat_files(dat_files: list[pathlib.Path]) -> dict[str, list[GameRomPair]]:
    games_by_sha1 = {}
    for file in dat_files:
        if not file.exists():
            raise ValueError("Dat file \"{file}\" does not exist.")

        dat = load_rom_dat(file)
        for game in dat.games:
            for rom in game.roms:
                if rom.sha1 is None:
                    raise ValueError(f"Missing sha1 hash for rom {rom.name} in dat {file}")

                if rom.sha1 not in games_by_sha1:
                    games_by_sha1[rom.sha1] = []

                games_by_sha1[rom.sha1].append(GameRomPair(game, rom))

    return games_by_sha1


def calc_sha1_hash(file: pathlib.Path) -> str:
    cached_hash_file = file.with_name(file.name + SHA1_EXT)
    if cached_hash_file.exists():
        with open(cached_hash_file, "r") as f:
            sha1 = f.read().strip().casefold()
            return sha1

    with open(file, "rb") as f:
        digest = hashlib.file_digest(f, "sha1")
    sha1 = digest.hexdigest().casefold()

    with open(cached_hash_file, 'w') as f:
        f.write(sha1)

    return sha1


def rename_file(old: pathlib.Path, new: pathlib.Path):
    try:
        old.rename(new)
    except OSError as e:
        print(f"Error renaming file \"{old}\": {e}")
        return

    sha1_old = old.with_name(old.name + SHA1_EXT)
    if sha1_old.exists():
        sha1_new = new.with_name(new.name + SHA1_EXT)
        try:
            sha1_old.rename(sha1_new)
        except OSError as e:
            print(f"Error renaming sha1 hash file \"{sha1_old}\": {e}")


def handle_cue_file(file: pathlib.Path,
                    sync_folders: list[SyncFolder],
                    games_by_sha1: dict[str, list[GameRomPair]],
                    dry_run: bool):
    bin_files = extract_files_from_cue(file)
    bin_file_hashes = {}
    for bin_file in bin_files:
        bin_file_hashes[bin_file] = calc_sha1_hash(file.parent / bin_file)

    game = None
    for bin_file, sha1 in bin_file_hashes.items():
        if sha1 not in games_by_sha1:
            print(f"Error: No dat entry found for file \"{file.name}\" with sha1 hash: {sha1}", file=sys.stderr)
            return

        games = games_by_sha1[sha1]
        if len(games) == 1:
            game = games[0].game
            break

    if game is None:
        print(f"Warning: Multiple dat entries found for all bin files in \"{file.name}\". Skipping", file=sys.stderr)
        return

    rename_mapping = {}
    for bin_file, sha1 in bin_file_hashes.items():
        bin_file_path = file.parent / bin_file
        rom = find_rom(sha1, game)

        if rom is None:
            print(
                f"Error: Bin file \"{bin_file}\" with sha1 hash {sha1} does not match any rom entry for {game.name}", file=sys.stderr)
            return

        if bin_file_path.name == rom.name:
            continue

        rename_mapping[bin_file_path] = bin_file_path.with_name(rom.name)

    new_cue_file = file.with_name(game.name + '.cue')
    if len(rename_mapping) == 0 and new_cue_file.name == file.name:
        return

    print(f"Renaming cue file \"{file}\" -> {new_cue_file.name}")
    if not dry_run:
        rename_file(file, new_cue_file)

    for old_bin_file, new_bin_file in rename_mapping.items():
        print(f"\tRenaming bin file \"{old_bin_file}\" -> {new_bin_file.name}")
        if not dry_run:
            rename_file(old_bin_file, new_bin_file)

    if len(rename_mapping) > 0:
        print(f"\tUpdating cue file \"{new_cue_file}\"")
        if not dry_run:
            relative_rename_mapping = {
                str(old.relative_to(file.parent)): str(new.relative_to(file.parent))
                for old, new in rename_mapping.items()
            }
            update_files_in_cue(new_cue_file, relative_rename_mapping)

    sync_rename(file.name, new_cue_file.name, sync_folders, dry_run)


def extract_files_from_cue(cue_file: pathlib.Path) -> list[str]:
    files = []
    with open(cue_file, 'r') as f:
        for line in f:
            match = CUE_FILE_PATTERN.match(line)
            if match is not None:
                files.append(match.group(2))
    return files


def find_rom(sha1: str, game: Game) -> Rom | None:
    for rom in game.roms:
        if rom.sha1 == sha1:
            return rom

    return None


def update_files_in_cue(cue_file: pathlib.Path, file_renames: dict[str, str]):
    def do_rename(match):
        file = match.group(2)
        if file in file_renames:
            return f"{match.group(1)}{file_renames[file]}{match.group(3)}"
        else:
            return match.group(0)

    updated_lines = []
    with open(cue_file, 'r') as f:
        for line in f:
            updated_lines.append(CUE_FILE_PATTERN.sub(do_rename, line))

    with open(cue_file, 'w') as f:
        for line in updated_lines:
            f.write(line)


if __name__ == '__main__':
    main()
