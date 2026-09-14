import pathlib
import logging

from typing import Generator
from dataclasses import dataclass
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from .common import HashFileSource, OverwriteCheck, DotFilesMode, SrcDestPair
from .grouping import Grouping, create_groupers
from .progress import SyncProgressTracker
from .. import Profile, ProfileOutput, ProfileSource, sha1_hash_file, SHA1_EXT

__all__ = ['Plan', 'SrcDestPair', 'DotFilesMode', 'create_plan']


@dataclass
class Plan:
    delete_file_tasks: list[pathlib.Path]
    delete_dir_tasks: list[pathlib.Path]
    copy_tasks: list[SrcDestPair]
    rename_tasks: list[SrcDestPair]

    def empty(self):
        return not self.copy_tasks and not self.rename_tasks and \
            not self.delete_dir_tasks and not self.delete_file_tasks


def create_plan(progress_tracker: SyncProgressTracker,
                src_path: pathlib.Path,
                dst_path: pathlib.Path,
                profile: Profile,
                dot_files_mode: DotFilesMode,
                overwrite_check: OverwriteCheck,
                delete: bool,
                thread_count: int) -> Plan:
    progress_tracker.start_plan()

    copy_candidates_by_folder = _scan_sources(src_path, dst_path, profile, dot_files_mode)
    if delete or overwrite_check == OverwriteCheck.HASH:
        destination_files = _scan_destinations(dst_path, profile)
    else:
        destination_files = {}

    rename_tasks = []
    hashes = {}
    if overwrite_check == OverwriteCheck.HASH:
        hashes = _hash_files(progress_tracker, thread_count, dot_files_mode,
                             copy_candidates_by_folder, destination_files)
        rename_tasks = _find_rename_targets(copy_candidates_by_folder, destination_files, hashes)

    delete_file_tasks = []
    delete_dir_tasks = []
    if delete:
        delete_file_tasks, delete_dir_tasks = _filter_files_to_delete(dst_path,
                                                                      dot_files_mode,
                                                                      profile,
                                                                      copy_candidates_by_folder,
                                                                      rename_tasks,
                                                                      destination_files)

    copy_tasks = list(_filter_copy_tasks(copy_candidates_by_folder, rename_tasks, overwrite_check, hashes))

    progress_tracker.complete_plan()

    return Plan(delete_file_tasks, delete_dir_tasks, copy_tasks, rename_tasks)


def _scan_sources(src_path: pathlib.Path,
                  dst_path: pathlib.Path,
                  profile: Profile,
                  dot_files_mode: DotFilesMode) -> dict[pathlib.Path, list[SrcDestPair]]:
    src_dst_pairs: dict[pathlib.Path, list[SrcDestPair]] = {}
    for output in profile.outputs:
        src_dst_pairs[output.path] = []

        grouper = create_groupers(output.grouping) if output.grouping else None
        results = []
        for source in output.sources:
            _scan_profile_source(src_path, source, dot_files_mode, results)

        results = _filter_duplicate_entries(results)

        folder_dst_path = dst_path / output.path
        if grouper:
            for group in grouper.create_group(src_path, results):
                if isinstance(group, Grouping):
                    src_dst_pairs[output.path].extend(group.to_src_dst_pairs(folder_dst_path))
                else:
                    src_dst_pairs[output.path].append(SrcDestPair(group, folder_dst_path / group.name))
        else:
            for result in results:
                src_dst_pairs[output.path].append(SrcDestPair(result, folder_dst_path / result.name))

        src_dst_pairs[output.path] = sorted(src_dst_pairs[output.path], key=lambda p: p.src.name)

    return src_dst_pairs


def _scan_profile_source(src_path: pathlib.Path,
                         source: ProfileSource,
                         dot_files_mode: DotFilesMode,
                         results: list[pathlib.Path]):
    scan_dir = src_path / source.rom_set.path
    logging.debug("Scanning rom set \"%s\" in folder \"%s\" for roms with extensions: %s",
                  scan_dir,
                  source.rom_set.name,
                  ', '.join(source.rom_set.extensions))
    if not scan_dir.exists() or not scan_dir.is_dir():
        logging.warning(f"Source rom folder \"{scan_dir}\" does not exist or is not a directory. Skipping.")
        return

    glob_pattern = "**" if source.rom_set.recursive else "*"
    for file in scan_dir.glob(glob_pattern):
        relative_path = file.relative_to(scan_dir)
        if not file.is_file():
            continue

        if not source.rom_set.is_included(file):
            logging.debug("Skipping file \"%s\" in rom set \"%s\" as it does not end with a desired extension.",
                          relative_path, source.rom_set.name)
            continue

        if not dot_files_mode.should_copy() and file.name.startswith("."):
            logging.debug("Skipping file \"%s\" in rom set \"%s\" as copying dot files is disabled.",
                          relative_path, source.rom_set.name)
            continue

        relative_path = file.relative_to(scan_dir)
        if source.rom_set.is_excluded(relative_path):
            logging.debug("Skipping file \"%s\" in rom set \"%s\" as it matches the exclude pattern of the rom set.",
                          relative_path, source.rom_set.name)
            continue

        if source.is_excluded(relative_path):
            logging.debug("Skipping file \"%s\" in rom set \"%s\" as it matches an exclude pattern of the profile source.",
                          relative_path, source.rom_set.name)
            continue

        if not source.is_included(relative_path):
            logging.debug("Skipping file \"%s\" in rom set \"%s\" as does not match any include pattern of the profile source.",
                          relative_path, source.rom_set.name)
            continue

        logging.debug("Found \"%s\" from scanning rom set \"%s\"", file, source.rom_set.name)
        results.append(file)


def _filter_duplicate_entries(files: list[pathlib.Path]) -> list[pathlib.Path]:
    unique_names = set()
    results = []
    for file in files:
        if file.name not in unique_names:
            unique_names.add(file.name)
            results.append(file)
        else:
            logging.debug('Skipping \"%s\" since it was duplicated by another rom set', file)

    return results


def _scan_destinations(dst_path: pathlib.Path,
                       profile: Profile) -> dict[pathlib.Path, list[pathlib.Path]]:
    results = {}
    for output in profile.outputs:
        results[output.path] = _scan_destination(dst_path, output)

    return results


def _scan_destination(dst_path: pathlib.Path,
                      output: ProfileOutput) -> list[pathlib.Path]:

    scan_dir = dst_path / output.path
    results = []
    for root_path, _, filenames in scan_dir.walk():
        for file in filenames:
            results.append(root_path / file)
    return sorted(results)


def _hash_files(progress_tracker: SyncProgressTracker,
                thread_count: int,
                dot_files_mode: DotFilesMode,
                copy_candidates: dict[pathlib.Path, list[SrcDestPair]],
                dst_files: dict[pathlib.Path, list[pathlib.Path]]) -> dict[pathlib.Path, str]:
    src_files_to_hash = set()
    dst_files_to_hash = set()
    for copy_task in _iter_copy_candidates(copy_candidates):
        src_files_to_hash.add(copy_task.src)
        if copy_task.dst.exists():
            dst_files_to_hash.add(copy_task.dst)

    for file in _iter_destination_files(dst_files):
        # if we're not copying dot files then we don't need to hash them
        if file.name.startswith('.') and not dot_files_mode.should_copy():
            continue

        # ignore the MacOS metadata files that start with "._" and .sha1 files
        if not file.name.startswith("._") and not file.suffix == SHA1_EXT:
            dst_files_to_hash.add(file)

    if len(dst_files_to_hash) == 0:
        logging.debug("No files found that need to be hashed in the destination folder.")
        return {}

    progress_tracker.start_hash(len(src_files_to_hash) + len(dst_files_to_hash))

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
            progress_tracker.advance_hash()
            file = futures_to_path[future]
            sha1 = future.result()

            hashes[file] = sha1

    progress_tracker.stop_hash()
    return hashes


def _find_rename_targets(copy_candidates: dict[pathlib.Path, list[SrcDestPair]],
                         dst_files: dict[pathlib.Path, list[pathlib.Path]],
                         hashes_by_path: dict[pathlib.Path, str]) -> list[SrcDestPair]:
    """Find ROM files that can be renamed by checking if their sha1 hash matches and they are in the same directory."""
    results = []

    paths_by_hash = defaultdict(lambda: defaultdict(list))
    for folder, files in dst_files.items():
        for file in files:
            sha1 = hashes_by_path.get(file)
            if sha1 is None:
                continue

            paths_by_hash[folder][sha1].append(file)

    for folder, copy_tasks in copy_candidates.items():
        for copy_task in copy_tasks:
            if copy_task.dst.exists():
                logging.debug(
                    "Destination file \"%s\" already exists. Will not attempt to find a rename target.", copy_task.dst)
                continue

            sha1 = hashes_by_path.get(copy_task.src)
            if sha1 is None:
                continue

            candidate_renames = paths_by_hash[folder][sha1]
            if candidate_renames:
                rename_src = candidate_renames[0]
                logging.debug("Found rename candidate \"%s\" for \"%s\": SHA1 hash %s matches.",
                              rename_src, copy_task.dst, sha1)
                results.append(SrcDestPair(rename_src, copy_task.dst))
            else:
                logging.debug("No rename candidate found for \"%s\": No files found with matching SHA1 hash %s.",
                              copy_task.dst, sha1)

    return results


def _filter_copy_tasks(copy_candidates: dict[pathlib.Path, list[SrcDestPair]],
                       rename_tasks: list[SrcDestPair],
                       overwrite_check: OverwriteCheck,
                       hashes_by_path: dict[pathlib.Path, str]) -> Generator[SrcDestPair, None, None]:
    """
        Filter out copy task candidates that don't need to be run because they are either
        <ol>
        <li>Part of the rename tasks</li>
        <li>The destination already exists and has not been modified</li>
        </ol>
    """

    # build the set of destination paths for the rename tasks
    # any copy task whose destination is in this set can be filtered out
    rename_task_dests = set(pair.dst for pair in rename_tasks)

    for copy_tasks in copy_candidates.values():
        for copy_task in copy_tasks:
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
                            dot_files_mode: DotFilesMode,
                            profile: Profile,
                            copy_candidates: dict[pathlib.Path, list[SrcDestPair]],
                            rename_tasks: list[SrcDestPair],
                            dst_files: dict[pathlib.Path, list[pathlib.Path]]) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """
        Finds file to delete in the destination directory by looking at the destination file scan 
        and finding files that:
        <ol>
        <li>Are not in the set of files to copy over</li>
        <li>Are not in the set of files to be renamed</li>
        </ol>

        Any directories that has all it's files deleted will also be marked for deletion.
    """
    copy_task_dests = set(copy_task.dst for copy_task in _iter_copy_candidates(copy_candidates))
    rename_tasks_by_src = {pair.src: pair.dst for pair in rename_tasks}

    files_to_delete = []
    keep_dirs = set()

    for folder, files in dst_files.items():
        profile_output = profile.outputs_by_path[folder]
        for file in files:
            if file.name.startswith('.') and not dot_files_mode.should_delete():
                logging.debug("Ignoring file \"%s\" for deletion since deleting dot files is disabled.", file)
                keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))
                continue

            relative_path = file.relative_to(dst_root_path)
            if profile_output.is_delete_excluded(relative_path) or profile.is_delete_excluded(relative_path):
                logging.debug("Ignoring file \"%s\" for deletion since it's explicitly excluded.", file)
                keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))
                continue

            if file.suffix == SHA1_EXT:
                hashed_file = file.with_name(file.stem)
                if not hashed_file.exists():
                    logging.debug("Marking file \"%s\" for deletion since it's an orphaned SHA1 hash file.", file)
                    files_to_delete.append(file)
                elif hashed_file in files_to_delete:
                    logging.debug("Marking file \"%s\" for deletion since \"%s\" is marked for deletion.",
                                  file, hashed_file.name)
                    files_to_delete.append(file)
                else:
                    rename_dst = rename_tasks_by_src.get(hashed_file)
                    if rename_dst is None or rename_dst.parent == hashed_file.parent:
                        logging.debug("Marking dir \"%s\" to keep since file \"%s\" is not marked for deletion.",
                                      hashed_file.parent, file.name)
                        keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))
                continue

            rename_dst = rename_tasks_by_src.get(file)
            if rename_dst is not None:
                if file.parent == rename_dst.parent:
                    logging.debug("Marking dir \"%s\" to keep since file \"%s\" is being renamed but staying in this directory.",
                                  file.parent, file.name)
                    keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))
                else:
                    logging.debug("Marking dir \"%s\" to keep since file \"%s\" is being renamed into this directory.",
                                  rename_dst.parent, file.name)
                    keep_dirs.update(_list_dirs_from_path(rename_dst.parent, dst_root_path))
            elif file not in copy_task_dests:
                logging.debug("Marking file \"%s\" for deletion as it does not exist in a source rom folder.", file)
                files_to_delete.append(file)
            else:
                logging.debug("Marking dir \"%s\" to keep since file \"%s\" is not marked for deletion.",
                              file.parent, file.name)
                keep_dirs.update(_list_dirs_from_path(file.parent, dst_root_path))

    dirs_to_delete = set()
    for file in files_to_delete:
        for dir in _list_dirs_from_path(file.parent, dst_root_path):
            if dir not in keep_dirs:
                logging.debug(
                    "Marking directory \"%s\" for deletion as all it's children are marked for deletion or rename.", dir)
                dirs_to_delete.add(dst_root_path / dir)

    # check for dirs we shouldn't keep because all their files have been renamed out of the dir
    for src, dst in rename_tasks_by_src.items():
        if src.parent != dst.parent:
            for dir in _list_dirs_from_path(src.parent, dst_root_path):
                if dir not in keep_dirs:
                    logging.debug(
                        "Marking directory \"%s\" for deletion as all it's children are marked for deletion or rename.", dir)
                    dirs_to_delete.add(dst_root_path / dir)

    # reverse sorting the dirs ends up listing the long paths first which allows us to delete subdirs first
    return (files_to_delete, sorted(dirs_to_delete, reverse=True))


def _iter_copy_candidates(copy_candidates: dict[pathlib.Path, list[SrcDestPair]]) -> Generator[SrcDestPair, None, None]:
    for copy_tasks in copy_candidates.values():
        for copy_task in copy_tasks:
            yield copy_task


def _iter_destination_files(destination_files: dict[pathlib.Path, list[pathlib.Path]]) -> Generator[pathlib.Path, None, None]:
    for files in destination_files.values():
        for file in files:
            yield file


def _list_dirs_from_path(path: pathlib.Path, from_dir: pathlib.Path) -> Generator[pathlib.Path, None, None]:
    """
        Lists all directories from a given path.  For example /foo/bar/baz from /foo with return: bar, bar/baz
    """
    relative_path = path.relative_to(from_dir)

    current_path = pathlib.Path()
    for part in relative_path.parts:
        current_path = current_path / part
        yield current_path
