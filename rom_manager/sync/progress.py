import pathlib

from typing import Generator
from rich.console import Console, group
from rich.progress import Progress
from .common import HashFileSource
from ..progress import ProgressWrapper, create_spinner_step_progress, create_bar_step_progress, create_file_subtask_progress


class SyncProgressTracker:
    def __init__(self, console: Console):
        width = 13
        self.plan_overall_progress = create_spinner_step_progress(console, "Planning", "green", width)

        self.hash_overall_progress = create_bar_step_progress(console, "Hash Files", "blue", width)
        self.hash_files_progress = create_file_subtask_progress(console)

        self.delete_overall_progress = create_bar_step_progress(console, "Delete Files", "red", width)

        self.rename_overall_progress = create_bar_step_progress(console, "Rename Files", "orange_red1", width)

        self.copy_overall_progress = create_bar_step_progress(console, "Copy Files", "cyan", width)
        self.copy_files_progress = create_file_subtask_progress(console)

    @group()
    def progress_group(self) -> Generator[Progress, None, None]:
        yield self.plan_overall_progress.progress

        yield self.hash_overall_progress.progress
        yield self.hash_files_progress

        yield self.delete_overall_progress.progress

        yield self.rename_overall_progress.progress

        yield self.copy_overall_progress.progress
        yield self.copy_files_progress

    def add_hash_file_task(self,
                           source: HashFileSource,
                           file: pathlib.Path,
                           file_size: int) -> ProgressWrapper:
        if source == HashFileSource.SOURCE_FILE:
            prefix = "Source: "
        else:
            prefix = "Dest:   "

        task_id = self.hash_files_progress.add_task("hash",
                                                    start=False,
                                                    visible=False,
                                                    total=file_size,
                                                    filename=file.name,
                                                    filename_prefix=prefix)
        return ProgressWrapper(self.hash_files_progress, task_id)

    def add_copy_file_task(self, file: pathlib.Path, file_size: int) -> ProgressWrapper:
        task_id = self.copy_files_progress.add_task("copy",
                                                    start=False,
                                                    visible=False,
                                                    total=file_size,
                                                    filename=file.name)
        return ProgressWrapper(self.copy_files_progress, task_id)

    def start_plan(self):
        self.plan_overall_progress.start(visible=True)

    def complete_plan(self):
        self.plan_overall_progress.advance()
        self.plan_overall_progress.stop()

    def fail_plan(self):
        self.plan_overall_progress.stop()
        self.plan_overall_progress.update(failed=True)

    def start_hash(self, total: int):
        self.hash_overall_progress.start(visible=True, total=total)

    def advance_hash(self):
        self.hash_overall_progress.advance()

    def stop_hash(self):
        self.hash_overall_progress.stop()

    def fail_hash(self):
        self.hash_overall_progress.stop()
        self.hash_overall_progress.update(failed=True)

    def execute_plan(self, total_delete_tasks, total_rename_tasks, total_copy_tasks):
        if total_delete_tasks:
            self.delete_overall_progress.update(visible=True, total=total_delete_tasks)

        if total_rename_tasks:
            self.rename_overall_progress.update(visible=True, total=total_rename_tasks)

        if total_copy_tasks:
            self.copy_overall_progress.update(visible=True, total=total_copy_tasks)

    def start_delete(self):
        self.delete_overall_progress.start()

    def advance_delete(self):
        self.delete_overall_progress.advance()

    def stop_delete(self):
        self.delete_overall_progress.stop()

    def fail_delete(self):
        self.delete_overall_progress.stop()
        self.delete_overall_progress.update(failed=True)

    def start_rename(self):
        self.rename_overall_progress.start()

    def advance_rename(self):
        self.rename_overall_progress.advance()

    def stop_rename(self):
        self.rename_overall_progress.stop()

    def fail_rename(self):
        self.rename_overall_progress.stop()
        self.rename_overall_progress.update(failed=True)

    def start_copy(self):
        self.copy_overall_progress.start()

    def advance_copy(self):
        self.copy_overall_progress.advance()

    def stop_copy(self):
        self.copy_overall_progress.stop()

    def fail_copy(self):
        self.copy_overall_progress.stop()
        self.copy_overall_progress.update(failed=True)

    def stop(self):
        self.plan_overall_progress.progress.stop()
        self.hash_overall_progress.progress.stop()
        self.hash_files_progress.stop()
        self.delete_overall_progress.progress.stop()
        self.rename_overall_progress.progress.stop()
        self.copy_overall_progress.progress.stop()
        self.copy_files_progress.stop()
