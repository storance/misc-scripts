import pathlib
import logging

from typing import Generator
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from .common import HashFileSource, OverwriteCheck, DotFilesMode
from .progress import SyncProgressTracker
from .. import Profile, sha1_hash_file, SHA1_EXT

__all__ = ['Plan', 'SrcDestPair', 'FileDetails', 'DotFilesMode', 'create_plan']


@dataclass
class Plan:
    delete_file_tasks: list[pathlib.Path]
    delete_dir_tasks: list[pathlib.Path]
    copy_tasks: list[SrcDestPair]
    rename_tasks: list[SrcDestPair]


@dataclass(frozen=True)
class SrcDestPair:
    src: pathlib.Path
    dst: pathlib.Path


@dataclass
class FileDetails:
    path: pathlib.Path
    size: int | None
    modified_time: float | None
    sha1_hash: str | None


def create_plan(progress_tracker: SyncProgressTracker,
                src_path: pathlib.Path,
                dst_path: pathlib.Path,
                profile: Profile,
                dot_files_mode: DotFilesMode,
                overwrite_check: OverwriteCheck,
                delete: bool,
                thread_count: int) -> Plan:
    progress_tracker.plan_overall_progress.start(visible=True)

    copy_candidates = _scan_source(src_path, dst_path, profile, dot_files_mode)
    destination_files = _scan_dir(dst_path)

    rename_tasks = []
    hashes = {}
    if overwrite_check == OverwriteCheck.HASH:
        hashes = _hash_files(progress_tracker, profile, thread_count, copy_candidates, destination_files)
        rename_tasks = _find_rename_targets(copy_candidates, destination_files, hashes)

    delete_file_tasks = []
    delete_dir_tasks = []
    if delete:
        delete_file_tasks, delete_dir_tasks = _filter_files_to_delete(dst_path,
                                                                      profile,
                                                                      dot_files_mode,
                                                                      copy_candidates,
                                                                      rename_tasks,
                                                                      destination_files)

    copy_tasks = list(_filter_copy_tasks(copy_candidates, rename_tasks, overwrite_check, hashes))

    progress_tracker.plan_overall_progress.advance()
    progress_tracker.plan_overall_progress.stop()

    return Plan(delete_file_tasks, delete_dir_tasks, copy_tasks, rename_tasks)


def _scan_source(src_path: pathlib.Path,
                 dst_path: pathlib.Path,
                 profile: Profile,
                 dot_files_mode: DotFilesMode) -> list[SrcDestPair]:
    copy_tasks = set()
    for rom_folder_config in profile.rom_folders:
        scan_dir = src_path / rom_folder_config.rom_folder.path
        logging.debug("Using rom folder %s to scan \"%s\" for roms with extensions: %s",
                      rom_folder_config.rom_folder.name,
                      scan_dir,
                      ', '.join(rom_folder_config.rom_folder.extensions))
        if not scan_dir.exists() or not scan_dir.is_dir():
            logging.warning(f"Source rom folder \"{scan_dir}\" does not exist or is not a directory. Skipping.")
            continue

        glob_pattern = "**" if rom_folder_config.rom_folder.include_subfolders else "*"

        for file in scan_dir.glob(glob_pattern):
            if not file.is_file():
                continue

            if not any(file.name.endswith(ext) for ext in rom_folder_config.rom_folder.extensions):
                logging.debug("Skipping file \"%s\" as it does not end with a desired extension.", file)
                continue

            if not dot_files_mode.should_copy() and file.name.startswith("."):
                logging.debug("Skipping file \"%s\" as copying dot files is disabled.", file)
                continue

            relative_path = file.relative_to(scan_dir)
            if rom_folder_config.rom_folder.is_excluded(relative_path):
                logging.debug("Skipping file \"%s\" as it matches the exclude pattern of the rom folder.", file)
                continue

            if rom_folder_config.is_excluded(relative_path):
                logging.debug("Skipping file \"%s\" as it matches an exclude pattern of the profile.", file)
                continue

            if not rom_folder_config.is_included(relative_path):
                logging.debug("Skipping file \"%s\" as does not match any include pattern of the profile.", file)
                continue

            rom_dst_path = (dst_path / rom_folder_config.get_relative_destination(relative_path)).resolve()
            if not rom_dst_path.is_relative_to(dst_path):
                raise ValueError(
                    f"Rom destination path \"{rom_dst_path}\" is not relative to the configured destination folder \"{dst_path}\"")

            logging.debug("Including \"%s\" for possible copying to \"%s\".", file, rom_dst_path)
            copy_tasks.add(SrcDestPair(file, rom_dst_path))

    return sorted(list(copy_tasks), key=lambda t: t.src)


def _scan_dir(scan_dir: pathlib.Path) -> list[pathlib.Path]:
    results = []

    for root_path, _, filenames in scan_dir.walk():
        for file in filenames:
            results.append(root_path / file)

    return sorted(results)


def _hash_files(progress_tracker: SyncProgressTracker,
                profile: Profile,
                thread_count: int,
                copy_candidates: list[SrcDestPair],
                dst_files: list[pathlib.Path]) -> dict[pathlib.Path, str]:
    src_files_to_hash = set(copy_task.src for copy_task in copy_candidates)
    dst_files_to_hash = set(copy_task.dst for copy_task in copy_candidates if copy_task.dst.exists())
    dst_files_to_hash.update(_filter_dest_files_to_hash(profile, dst_files))

    if len(dst_files_to_hash) == 0:
        return {}

    progress_tracker.hash_overall_progress.start(visible=True, total=len(src_files_to_hash) + len(dst_files_to_hash))

    with ThreadPoolExecutor(max_workers=thread_count) as executor:
        futures_to_path = {}
        for file in src_files_to_hash:
            file_progress = progress_tracker.add_hash_file_task(HashFileSource.SOURCE_FILE, file, file.stat().st_size)
            future = executor.submit(sha1_hash_file, file, file_progress)
            futures_to_path[future] = file

        for file in dst_files_to_hash:
            file_progress = progress_tracker.add_hash_file_task(HashFileSource.DEST_FILE, file, file.stat().st_size)
            future = executor.submit(sha1_hash_file, file, file_progress)
            futures_to_path[future] = file

        hashes = {}
        for future in as_completed(futures_to_path):
            progress_tracker.hash_overall_progress.advance()
            file = futures_to_path[future]
            sha1 = future.result()

            hashes[file] = sha1

    progress_tracker.hash_overall_progress.stop()
    return hashes


def _filter_dest_files_to_hash(profile: Profile, dst_files: list[pathlib.Path]) -> Generator[pathlib.Path, None, None]:
    """Filter destination files to only include those that match any of the extensions in the profile's rom folders. """
    for file in dst_files:
        if profile.is_interested_ext(file):
            yield file


def _find_rename_targets(copy_candidates: list[SrcDestPair],
                         dst_files: list[pathlib.Path],
                         hashes_by_path: dict[pathlib.Path, str]) -> list[SrcDestPair]:
    """Find ROM files that can be renamed by checking if their sha1 hash matches and they are in the same directory."""
    results = []

    paths_by_hash = {}
    for path in dst_files:
        sha1 = hashes_by_path.get(path)
        if sha1 is None:
            continue

        if sha1 not in paths_by_hash:
            paths_by_hash[sha1] = []

        paths_by_hash[sha1].append(path)

    for copy_task in copy_candidates:
        if copy_task.dst.exists():
            logging.debug("Destination file \"%s\" already exists. Will not attempt to find a rename target.", copy_task.dst)
            continue

        sha1 = hashes_by_path.get(copy_task.src)
        if sha1 is None:
            continue

        candidate_renames = paths_by_hash.get(sha1, [])
        # find the first matching file that is in the same directory as the desired destination path
        rename_src = next((c for c in candidate_renames if c.parent.samefile(copy_task.dst.parent)), None)
        if rename_src is not None:
            logging.debug("Found rename candidate \"%s\" for \"%s\": SHA1 hash %s matches.",
                          rename_src, copy_task.dst, sha1)
            results.append(SrcDestPair(rename_src, copy_task.dst))
        else:
            logging.debug("No rename candidate found for \"%s\": No files found with matching SHA1 hash %s.",
                          copy_task.dst, sha1)

    return results


def _filter_copy_tasks(copy_candidates: list[SrcDestPair],
                       rename_tasks: list[SrcDestPair],
                       overwrite_check: OverwriteCheck,
                       hashes_by_path: dict[pathlib.Path, str]) -> Generator[SrcDestPair, None, None]:
    """
        Filter out copy task candidates that don't need to be run because they are either
        1). Part of the rename tasks
        2). The destination already exists and has not been modified
    """

    # build the set of destination paths for the rename tasks
    # any copy task whose destination is in this set can be filtered out
    rename_task_dests = set(pair.dst for pair in rename_tasks)

    for copy_task in copy_candidates:
        if overwrite_check == OverwriteCheck.ALWAYS:
            logging.debug("Adding copy \"%s\" -> \"%s\" as task: Overwrite mode is always.",
                          copy_task.src, copy_task.dst)
            yield copy_task
        elif copy_task.dst.exists():
            # check if the src and dst files are different
            # if we have sha1 hashes, use those to check if the files are different
            # otherwise, use the file size and modification times
            if overwrite_check == OverwriteCheck.HASH:
                src_hash = hashes_by_path.get(copy_task.src)
                dst_hash = hashes_by_path.get(copy_task.dst)

                if src_hash != dst_hash:
                    logging.debug("Adding copy \"%s\" -> \"%s\" as task: sha1 %s does not match %s.",
                                  copy_task.src, copy_task.dst, src_hash, dst_hash)
                    yield copy_task
                else:
                    logging.debug("Skipping copy \"%s\" -> \"%s\": sha1 hashes (%s) match.",
                                  copy_task.src, copy_task.dst, src_hash)
            elif overwrite_check in [OverwriteCheck.SIZE_OR_TIME, OverwriteCheck.SIZE]:
                src_stat = copy_task.src.stat()
                dst_stat = copy_task.dst.stat()

                if src_stat.st_size != dst_stat.st_size:
                    logging.debug("Adding copy \"%s\" -> \"%s\" as task: file size %d does not match %d.",
                                  copy_task.src, copy_task.dst, src_stat.st_size, dst_stat.st_size)
                    yield copy_task
                elif overwrite_check == OverwriteCheck.SIZE_OR_TIME:
                    if src_stat.st_mtime > dst_stat.st_mtime:
                        logging.debug("Adding copy \"%s\" -> \"%s\" as task: source was modified more recently.",
                                      copy_task.src, copy_task.dst)
                        yield copy_task
                    else:
                        logging.debug("Skipping copy \"%s\" -> \"%s\": source was not modified more recently.",
                                      copy_task.src, copy_task.dst, src_stat.st_size)
                else:
                    logging.debug("Skipping copy \"%s\" -> \"%s\": file sizes (%d) match.",
                                  copy_task.src, copy_task.dst, src_stat.st_size)
            else:
                logging.debug("Skipping copy \"%s\" -> \"%s\": destination exists and overwrite check is never.",
                              copy_task.src, copy_task.dst)
        elif copy_task.dst not in rename_task_dests:
            logging.debug("Adding copy \"%s\" -> \"%s\" as task: destination does not exist.",
                          copy_task.src, copy_task.dst)
            yield copy_task


def _filter_files_to_delete(dst_root_path: pathlib.Path,
                            profile: Profile,
                            dot_files_mode: DotFilesMode,
                            copy_candidates: list[SrcDestPair],
                            rename_tasks: list[SrcDestPair],
                            dst_files: list[pathlib.Path]) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    copy_task_dests = set(pair.dst for pair in copy_candidates)
    rename_task_srcs = set(pair.src for pair in rename_tasks)

    files_to_delete = []
    keep_dirs = set()

    for file in dst_files:
        if file.name.startswith('.') and not dot_files_mode.should_delete():
            logging.debug(
                "Ignoring file \"%s\" for deletion check since deleting dot files is disabled.", file)
            keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))
            continue

        if file.suffix == SHA1_EXT:
            hashed_file = file.with_name(file.stem)
            if not hashed_file.exists() or hashed_file in files_to_delete:
                logging.debug("Adding file \"%s\" to delete tasks as it's an orphaned SHA1 hash file.", file)
                files_to_delete.append(file)
            else:
                keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))
            continue

        if not profile.is_include_for_delete(file):
            logging.debug(
                "Ignoring file \"%s\" for deletion checks since it's not an interested extension or explicitly excluded.", file)
            keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))
            continue

        if file not in rename_task_srcs and file not in copy_task_dests:
            logging.debug("Adding file \"%s\" to delete tasks as it does not exist in a source rom folder.", file)
            files_to_delete.append(file)

        keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))

    dirs_to_delete = set()
    for file in files_to_delete:
        for dir in _list_dirs_from_path(file.parent, dst_root_path):
            if dir not in keep_dirs:
                logging.debug(
                    "Adding directory \"%s\" to delete tasks as all it's children are marked for deletion.", dir)
                dirs_to_delete.add(dir)

    # reverse sorting the dirs ends up listing the long paths first which allows us to delete subdirs first
    return (files_to_delete, sorted(dirs_to_delete, reverse=True))


def _list_dirs_from_path(path: pathlib.Path, from_dir: pathlib.Path) -> Generator[pathlib.Path, None, None]:
    """
        Lists all directories from a given path.  For example /foo/bar/baz from /foo with return: bar, bar/baz
    """
    relative_path = path.relative_to(from_dir)

    current_path = pathlib.Path()
    for part in relative_path.parts:
        current_path = current_path / part
        yield current_path
