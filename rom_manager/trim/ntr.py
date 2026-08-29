import pathlib
import struct
import io

from dataclasses import dataclass
from typing import BinaryIO

NDS_LOGO_CRC = 0xCF56
RSA_MAGIC = 0x6361
PAD_BYTE = b"\xFF"
PAD_BUFFER = PAD_BYTE * (64 * 1024)

# References
# https://problemkaputt.de/gbatek-ds-cartridge-header.htm
# https://problemkaputt.de/gbatek-dsi-cartridge-header.htm
# https://github.com/d0k3/GodMode9


@dataclass
class RomInfo:
    total_size: int
    used_size: int
    actual_size: int
    logo: bytes
    logo_crc: int
    is_dsi: bool


CRC16_TABVAL = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
                0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


# see: https://github.com/TASVideos/desmume/blob/master/desmume/src/bios.cpp#L1070tions
def crc16(data: bytes):
    crc = 0xFFFF

    for i in range(0, len(data), 2):
        cur = struct.unpack('<H', data[i:i+2])[0]
        for j in range(4):
            tab_val = CRC16_TABVAL[crc & 0xF]
            crc = crc >> 4
            crc = crc ^ tab_val

            tab_val = CRC16_TABVAL[(cur >> (4*j)) & 0xF]
            crc = crc ^ tab_val

    return crc


def is_valid_game(file: pathlib.Path):
    with open(file, "rb") as f:
        rom_info = _get_rom_info(f)
        return _is_valid_game_fobj(rom_info)


def _is_valid_game_fobj(rom_info: RomInfo) -> bool:
    if rom_info.used_size > rom_info.actual_size:
        return False

    if rom_info.logo_crc != NDS_LOGO_CRC:
        return False

    actual_logo_crc = crc16(rom_info.logo)
    return actual_logo_crc != NDS_LOGO_CRC


def is_trim_needed(file: pathlib.Path) -> bool:
    with open(file, "rb") as f:
        rom_info = _get_rom_info(f)

        if not _is_valid_game_fobj(rom_info):
            raise OSError("Not a NDS game file")

        trimmed_size = _get_trimmed_size_fobj(f, rom_info)

        return trimmed_size < rom_info.actual_size


def is_untrim_needed(file: pathlib.Path) -> bool:
    with open(file, "rb") as f:
        rom_info = _get_rom_info(f)

        if not _is_valid_game_fobj(rom_info):
            raise OSError("Not a NDS game file")

        return rom_info.actual_size < rom_info.total_size


def _get_rom_info(fileobj: BinaryIO) -> RomInfo:
    fileobj.seek(0x12)
    unit_code = struct.unpack('B', fileobj.read(1))[0]

    fileobj.seek(0x14)
    capacity_flag = struct.unpack('B', fileobj.read(1))[0]
    # Formula: 2^(17 + X) bytes
    cartridge_size = 2 ** (17 + capacity_flag)

    fileobj.seek(0x0C0)
    logo = fileobj.read(156)
    logo_crc = struct.unpack('<H', fileobj.read(2))[0]

    if unit_code != 0:
        # DSi game so we need to look in the DSi extended header
        fileobj.seek(0x210)
        used_size = struct.unpack('<I', fileobj.read(4))[0]
    else:
        fileobj.seek(0x80)
        used_size = struct.unpack('<I', fileobj.read(4))[0]

    fileobj.seek(0, io.SEEK_END)
    actual_size = fileobj.tell()

    return RomInfo(cartridge_size, used_size, actual_size, logo, logo_crc, unit_code != 0)


def _get_trimmed_size_fobj(fileobj: BinaryIO, rom_info: RomInfo) -> int:
    if rom_info.actual_size == rom_info.used_size:
        return rom_info.used_size

    # check for the magic number RSA_MAGIC which indicates there is a download play RSA key after the rom
    # so add 0x88 bytes to account for that
    fileobj.seek(rom_info.used_size)
    magic_short = struct.unpack('<H', fileobj.read(2))[0]
    if magic_short == RSA_MAGIC:
        return rom_info.used_size + 0x88

    return rom_info.used_size


def trim_file(file: pathlib.Path) -> bool:
    with open(file, "r+b") as f:
        rom_info = _get_rom_info(f)

        if not _is_valid_game_fobj(rom_info):
            raise OSError("Not a NDS game file")

        trimmed_size = _get_trimmed_size_fobj(f, rom_info)

        if trimmed_size > rom_info.actual_size:
            raise OSError(
                f"Calculated trimmed size {trimmed_size} is larger than the actual file size {rom_info.actual_size}")

        if trimmed_size == rom_info.actual_size:
            # we're already trimmed
            return False

        f.truncate(trimmed_size)
        return True


def untrim_file(file: pathlib.Path) -> bool:
    with open(file, "r+b") as f:
        rom_info = _get_rom_info(f)

        if not _is_valid_game_fobj(rom_info):
            raise OSError("Not a NDS game file")

        # check if we're already untrimmed
        if rom_info.actual_size == rom_info.total_size:
            return False

        required = rom_info.total_size - rom_info.actual_size
        if required < 0:
            raise OSError("File is currently larger than the TWL header rom size")

        while required > 0:
            f.seek(0, io.SEEK_END)
            if required > len(PAD_BUFFER):
                written = f.write(PAD_BUFFER)
            else:
                written = f.write(PAD_BUFFER[:required])
            required -= written
        return True
