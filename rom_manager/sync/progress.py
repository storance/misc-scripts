import pathlib

from typing import Generator
from rich.console import Console, group
from rich.table import Column
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    DownloadColumn,
    MofNCompleteColumn,
    TaskProgressColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    TimeElapsedColumn,
)
from .common import HashFileSource
from ..progress import ProgressWrapper, create_spinner_step_progress, create_bar_step_progress, create_file_subtask_progress

class SyncProgressTracker:
    def __init__(self, console: Console):
        width=13
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

    def stop(self):
        self.plan_overall_progress.progress.stop()
        self.hash_overall_progress.progress.stop()
        self.hash_files_progress.stop()
        self.delete_overall_progress.progress.stop()
        self.rename_overall_progress.progress.stop()
        self.copy_overall_progress.progress.stop()
        self.copy_files_progress.stop()
