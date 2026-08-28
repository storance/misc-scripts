import pathlib
import hashlib
import logging
from .progress import ProgressWrapper

SHA1_EXT = '.sha1'

def sha1_hash_file(file: pathlib.Path,
                   progress: ProgressWrapper,
                   use_cache: bool = True,
                   force_regenerate: bool = False,
                   chunk_size=4*1024) -> str | None:
    logging.debug("Starting hashing of \"%s\"", file)
    total_size = file.stat().st_size

    progress.start(visible=True)
    
    sha1_file = file.with_name(file.name + SHA1_EXT)
    if sha1_file.exists() and not force_regenerate and use_cache:
        progress.advance(total_size)
        progress.stop(visible=False)
        sha1 = _read_sha1_file(sha1_file)
        logging.debug("Hashed \"%s\" to SHA1 %s using cached value.", file, sha1)
        return sha1

    try:
        digest = hashlib.sha1()
        with open(file, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                progress.advance(len(chunk))

        sha1 = digest.hexdigest().casefold()
        if use_cache: 
            with open(sha1_file, 'w') as f:
                f.write(sha1)

        progress.stop(visible=False)

        logging.debug("Hashed \"%s\" to SHA1 %s.", file, sha1)
        return sha1
    except Exception as e:
        progress.stop()
        progress.update(failed=True)
        logging.error("Failed to hash file \"%s\": %s", file, str(e))
        return None

def remove_sha1_cache(file: pathlib.Path):
    sha1_file = file.with_name(file.name + SHA1_EXT)
    if sha1_file.exists():
        sha1_file.unlink()

def _read_sha1_file(file: pathlib.Path) -> str:
    with open(file, "r") as f:
        sha1 = f.read().strip().casefold()
        return sha1

def rename_file(old: pathlib.Path, new: pathlib.Path):
    """Rename file that is aware of the cached sha1 files and will rename those files as well. """
    old.rename(new)

    sha1_old = old.with_name(old.name + SHA1_EXT)
    if sha1_old.exists():
        sha1_new = new.with_name(new.name + SHA1_EXT)
        sha1_old.rename(sha1_new)
