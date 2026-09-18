import pathlib
from dataclasses import dataclass
import xml.etree.ElementTree as ET

__all__ = [ 'DatFile', 'DatHeader', 'DatGame', 'DatRom', 'load_rom_dat']

@dataclass(frozen=True)
class DatFile:
    header: DatHeader|None
    games: list[DatGame]

@dataclass(frozen=True)
class DatHeader:
    name: str
    description: str
    version: str
    author: str

@dataclass(frozen=True)
class DatGame:
    name: str
    id: str|None
    description: str|None
    roms: list[DatRom]

@dataclass(frozen=True)
class DatRom:
    name: str
    size: int|None
    crc: str|None
    md5: str|None
    sha1: str|None
    sha256: str|None

def load_rom_dat(file: pathlib.Path) -> DatFile:
    tree = ET.parse(file)
    root = tree.getroot()

    header = _parser_header(root.find('header'))
    games = []

    for game in root.findall('game'):
        games.append(_parse_game(game))

    return DatFile(header, games)

def _parser_header(header_element: ET.Element[str] | None) -> DatHeader | None:
    if header_element is None:
        return None

    return DatHeader(
        _find_text_required(header_element, 'name'),
        _find_text_required(header_element, 'description'),
        _find_text_required(header_element, 'version'),
        _find_text_required(header_element, 'author'))

def _parse_game(game_element: ET.Element[str]) -> DatGame:
    name = game_element.get('name')
    id = game_element.get('id')

    if name is None:
        raise ValueError("Missing name attribute on a game element")

    description = game_element.findtext('description')
    roms = []
    for rom in game_element.findall('rom'):
        roms.append(_parse_rom(rom))

    return DatGame(name, id, description, roms)

def _parse_rom(rom_element: ET.Element[str]) -> DatRom:
    name = rom_element.get('name')
    size = rom_element.get('size')
    crc = rom_element.get('crc')
    md5 = _normalize_hash(rom_element.get('md5'))
    sha1 = _normalize_hash(rom_element.get('sha1'))
    sha256 = _normalize_hash(rom_element.get('sha256'))

    if name is None:
        raise ValueError("Missing name element on rom")

    return DatRom(name, int(size) if size is not None else None, crc, md5, sha1, sha256)

def _find_text_required(element: ET.Element[str], tag: str) -> str:
    value = element.findtext(tag)
    if value is None:
        raise ValueError(f"Missing required tag {tag} on element {element.tag}")

    return value

def _normalize_hash(hash: str|None) -> str|None:
    return hash.casefold() if hash is not None else None