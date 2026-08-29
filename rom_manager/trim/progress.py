import pathlib
from typing import Generator
from rich.console import Console, group
from rich.progress import Progress
from ..progress import ProgressWrapper, create_spinner_step_progress, create_bar_step_progress, create_file_subtask_progress


class TrimProgressTracker:
    def __init__(self, console: Console, trim: bool):
        width = 13
        trim_desc = 'Trim Files' if trim else 'Untrim Files'

        self.scan_overall_progress = create_spinner_step_progress(console, "Scanning", "green", width)
        self.trim_overall_progress = create_bar_step_progress(console, trim_desc, "slate_blue3", width)
        self.copy_files_progress = create_file_subtask_progress(console)

    def add_copy_file_task(self, file: pathlib.Path, file_size: int) -> ProgressWrapper:
        task_id = self.copy_files_progress.add_task("copy",
                                                    start=False,
                                                    visible=False,
                                                    total=file_size,
                                                    filename=file.name)
        return ProgressWrapper(self.copy_files_progress, task_id)

    def start_scan(self):
        self.scan_overall_progress.start(visible=True)
        self.trim_overall_progress.update(visible=True)

    def complete_scan(self):
        self.scan_overall_progress.advance()
        self.scan_overall_progress.stop()

    def fail_scan(self):
        self.scan_overall_progress.stop()
        self.scan_overall_progress.update(failed=True)

    def start_trim(self, total: int):
        self.trim_overall_progress.start(visible=True, total=total)

    def advance_trim(self):
        self.trim_overall_progress.advance()

    def stop_trim(self):
        self.trim_overall_progress.stop()

    def fail_trim(self):
        self.trim_overall_progress.stop()
        self.trim_overall_progress.update(failed=True)

    @group()
    def progress_group(self) -> Generator[Progress, None, None]:
        yield self.scan_overall_progress.progress
        yield self.trim_overall_progress.progress

    def stop(self):
        self.scan_overall_progress.progress.stop()
        self.trim_overall_progress.progress.stop()
