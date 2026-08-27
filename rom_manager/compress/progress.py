import pathlib

from typing import Generator
from rich.console import Console, group
from rich.progress import Progress
from ..progress import ProgressWrapper, create_spinner_step_progress, create_bar_step_progress, create_file_subtask_progress


class CompressProgressTracker:
    def __init__(self, console: Console):
        width = 15
        self.scan_overall_progress = create_spinner_step_progress(console, "Scanning", "green", width)
        self.compress_overall_progress = create_bar_step_progress(console, "Compress Files", "slate_blue3", width)
        self.copy_files_progress = create_file_subtask_progress(console)

    @group()
    def progress_group(self) -> Generator[Progress, None, None]:
        yield self.scan_overall_progress.progress
        yield self.compress_overall_progress.progress
        yield self.copy_files_progress

    def add_copy_task(self, file: pathlib.Path, file_size: int) -> ProgressWrapper:
        task_id = self.copy_files_progress.add_task("copy",
                                                    start=False,
                                                    visible=False,
                                                    total=file_size,
                                                    filename=file.name,
                                                    filename_prefix='Copying: ')
        return ProgressWrapper(self.copy_files_progress, task_id)

    def stop(self):
        self.scan_overall_progress.progress.stop()
        self.compress_overall_progress.progress.stop()
        self.copy_files_progress.stop()
