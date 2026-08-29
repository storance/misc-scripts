from typing import Generator
from rich.console import Console, group
from rich.progress import Progress
from ..progress import create_spinner_step_progress, create_bar_step_progress


class CompressProgressTracker:
    def __init__(self, console: Console):
        width = 15
        self.scan_overall_progress = create_spinner_step_progress(console, "Scanning", "green", width)
        self.compress_overall_progress = create_bar_step_progress(console, "Compress Files", "slate_blue3", width)

    def start_scan(self):
        self.scan_overall_progress.start(visible=True)
        self.compress_overall_progress.update(visible=True)

    def complete_scan(self):
        self.scan_overall_progress.advance()
        self.scan_overall_progress.stop()

    def fail_scan(self):
        self.scan_overall_progress.stop()
        self.scan_overall_progress.update(failed=True)

    def start_compress(self, total: int):
        self.compress_overall_progress.start(visible=True, total=total)

    def advance_compress(self):
        self.compress_overall_progress.advance()

    def stop_compress(self):
        self.compress_overall_progress.stop()

    def fail_compress(self):
        self.compress_overall_progress.stop()
        self.compress_overall_progress.update(failed=True)
    

    @group()
    def progress_group(self) -> Generator[Progress, None, None]:
        yield self.scan_overall_progress.progress
        yield self.compress_overall_progress.progress

    def stop(self):
        self.scan_overall_progress.progress.stop()
        self.compress_overall_progress.progress.stop()
