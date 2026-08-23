import pathlib
import hashlib
from collections.abc import Callable

SHA1_EXT = '.sha1'

def sha1_hash_file(file: pathlib.Path,
                   progress_callback: Callable[[int], None] = lambda c: None,
                   use_cache: bool = True,
                   force_regenerate: bool = False,
                   chunk_size=4*1024):
    total_size = file.stat().st_size
    
    sha1_file = file.with_name(file.name + SHA1_EXT)
    if sha1_file.exists() and not force_regenerate and use_cache:
        progress_callback(total_size)
        return _read_sha1_file(sha1_file)

    digest = hashlib.sha1()
    with open(file, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            progress_callback(len(chunk))

    if use_cache:
        sha1 = digest.hexdigest().casefold()
        with open(sha1_file, 'w') as f:
            f.write(sha1)

def remove_cached_sha1(file: pathlib.Path):
    sha1_file = file.with_name(file.name + SHA1_EXT)
    if sha1_file.exists():
        sha1_file.unlink()

def _read_sha1_file(file: pathlib.Path) -> str:
    with open(file, "r") as f:
        sha1 = f.read().strip().casefold()
        return sha1