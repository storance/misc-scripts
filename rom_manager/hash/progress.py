import pathlib

from typing import Generator
from rich.console import Console, group
from rich.progress import Progress
from ..progress import ProgressWrapper, create_spinner_step_progress, create_bar_step_progress, create_file_subtask_progress


class HashProgressTracker:
    def __init__(self, console: Console):
        width = 13
        self.scan_overall_progress = create_spinner_step_progress(console, "Scanning", "green", width)
        self.hash_overall_progress = create_bar_step_progress(console, "Hash Files", "blue", width)
        self.hash_files_progress = create_file_subtask_progress(console)

    @group()
    def progress_group(self) -> Generator[Progress, None, None]:
        yield self.scan_overall_progress.progress
        yield self.hash_overall_progress.progress
        yield self.hash_files_progress

    def add_hash_file_task(self, file: pathlib.Path, file_size: int) -> ProgressWrapper:
        task_id = self.hash_files_progress.add_task("hash",
                                                    start=False,
                                                    visible=False,
                                                    total=file_size,
                                                    filename=file.name)
        return ProgressWrapper(self.hash_files_progress, task_id)

    def start_scan(self):
        self.scan_overall_progress.start(visible=True)
        self.hash_overall_progress.update(visible=True)

    def complete_scan(self):
        self.scan_overall_progress.advance()
        self.scan_overall_progress.stop()

    def fail_scan(self):
        self.scan_overall_progress.stop()
        self.scan_overall_progress.update(failed=True)

    def start_hash(self, total: int):
        self.hash_overall_progress.start(visible=True, total=total)

    def advance_hash(self):
        self.hash_overall_progress.advance()

    def stop_hash(self):
        self.hash_overall_progress.stop()

    def fail_hash(self):
        self.hash_overall_progress.stop()
        self.hash_overall_progress.update(failed=True)

    def stop(self):
        self.scan_overall_progress.progress.stop()
        self.hash_overall_progress.progress.stop()
        self.hash_files_progress.stop()
