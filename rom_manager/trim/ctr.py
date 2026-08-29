import pathlib
import struct
import io

from typing import BinaryIO
from ..common import is_power_of_2

# References
# https://www.3dbrew.org/wiki/NCSD
# https://github.com/d0k3/GodMode9

MEDIA_UNIT = 0x200
PAD_BYTE = b"\xFF"
MAX_PARTITIONS = 8
PAD_BUFFER = PAD_BYTE * (64 * 1024)


def is_valid_game(file: pathlib.Path):
    with open(file, "rb") as f:
        return _is_valid_game_fobj(f)


def _is_valid_game_fobj(fileobj: BinaryIO) -> bool:
    fileobj.seek(0x100)
    return fileobj.read(4) == b"NCSD"


def is_trim_needed(file: pathlib.Path) -> bool:
    with open(file, "rb") as f:
        f.seek(0, io.SEEK_END)
        actual_size = f.tell()

        if not _is_valid_game_fobj(f):
            raise OSError("Not a 3DS game file")

        trimmed_size = _get_trimmed_size_fobj(f)

        return trimmed_size < actual_size


def is_untrim_needed(file: pathlib.Path) -> bool:
    with open(file, "rb") as f:
        f.seek(0, io.SEEK_END)
        actual_size = f.tell()

        if not _is_valid_game_fobj(f):
            raise OSError("Not a 3DS game file")

        image_size = _get_image_size(f)

        return actual_size < image_size


def _get_trimmed_size_fobj(fileobj: BinaryIO) -> int:
    # jump to the start of the partition table
    fileobj.seek(0x120)

    partition_end_mu = 0
    for i in range(MAX_PARTITIONS):
        (offset, size_mu) = struct.unpack('<II', fileobj.read(8))
        partition_end_mu = max(partition_end_mu, offset + size_mu)

    return partition_end_mu * MEDIA_UNIT


def _get_image_size(fileobj: BinaryIO) -> int:
    fileobj.seek(0x104)
    image_size = struct.unpack('<I', fileobj.read(4))[0] * MEDIA_UNIT
    if not is_power_of_2(image_size):
        raise OSError("Image size in NCSD header is not a power of 2.")

    return image_size


def trim_file(file: pathlib.Path) -> bool:
    with open(file, "r+b") as f:
        f.seek(0, io.SEEK_END)
        file_size = f.tell()

        if not _is_valid_game_fobj(f):
            raise OSError("Not a valid NCSD file")

        trimmed_size = _get_trimmed_size_fobj(f)

        if trimmed_size > file_size:
            raise OSError(f"Calculated trimmed size {trimmed_size} is larger than the actual file size {file_size}")

        if trimmed_size == file_size:
            # we're already trimmed
            return False

        f.truncate(trimmed_size)

        return True


def untrim_file(file: pathlib.Path) -> bool:
    with open(file, "r+b") as f:
        f.seek(0, io.SEEK_END)
        file_size = f.tell()

        if not _is_valid_game_fobj(f):
            raise OSError("Not a valid NCSD file")

        image_size = _get_image_size(f)
        # check if we're already untrimmed
        if file_size == image_size:
            return False

        required = image_size - file_size
        if required < 0:
            raise OSError("File is currently larger than the NCSD header image size")

        while required > 0:
            f.seek(0, io.SEEK_END)
            if required > len(PAD_BUFFER):
                written = f.write(PAD_BUFFER)
            else:
                written = f.write(PAD_BUFFER[:required])
            required -= written
        return True
