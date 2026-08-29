import pathlib
import hashlib
import logging
from .progress import ProgressWrapper
from .common import generate_random_string

SHA1_EXT = '.sha1'
RANDOM_SUFFIX_LEN = 6


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
        logging.exception("Failed to hash file \"%s\": %s", file, str(e))
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


def copy_file(progress: ProgressWrapper,
              src_path: pathlib.Path,
              dst_path: pathlib.Path,
              chunk_size: int,
              remove_sha1: bool = True) -> bool:
    logging.info("Copying \"%s\" to \"%s\".", src_path, dst_path)
    progress.start(visible=True)

    tmp_suffix = generate_random_string(RANDOM_SUFFIX_LEN)
    tmp_file = dst_path.with_name(f"{dst_path.name}.{tmp_suffix}")

    try:
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        with open(src_path, 'rb') as fsrc, open(tmp_file, 'wb') as fdest:
            while True:
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdest.write(chunk)
                progress.advance(len(chunk))

        tmp_file.rename(dst_path)
        if remove_sha1:
            remove_sha1_cache(dst_path)

        progress.stop(visible=False)
        return True
    except Exception as e:
        logging.error("Failed to copy file \"%s\" to \"%s\": {e}", src_path, dst_path, str(e))
        progress.stop()
        progress.update(failed=True)

        delete_quietly(tmp_file)
        return False


def delete_quietly(file: pathlib.Path):
    try:
        file.unlink(missing_ok=True)
    except OSError as e:
        logging.error("Failed to cleanup temp file \"%s\" after failed copy: %s", file, str(e))
