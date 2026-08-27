from dataclasses import dataclass
from rich.text import Text
from rich.table import Table, Column
from rich.console import Console
from rich.progress import (
    Progress,
    TaskID,
    Task,
    ProgressColumn,
    TextColumn,
    BarColumn,
    SpinnerColumn,
    TextColumn,
    DownloadColumn,
    MofNCompleteColumn,
    TaskProgressColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    TimeElapsedColumn,
)

class ProgressWrapper:
    def __init__(self, progress: Progress, task_id: TaskID):
        self.progress = progress
        self.task_id = task_id

    def start(self, visible: bool | None = None, total: int| None = None):
        self.progress.start_task(self.task_id)
        if visible is not None or total is not None:
            self.progress.update(self.task_id, visible=visible, total=total)

    def stop(self, visible: bool | None = None):
        self.progress.stop_task(self.task_id)
        if visible is not None:
            self.progress.update(self.task_id, visible=visible)

    def advance(self, progress: float = 1):
        self.progress.advance(self.task_id, progress)

    def update(self, *args, **kwargs):
        self.progress.update(self.task_id, *args, **kwargs)

    def log(self, *args, **kwargs):
        self.progress.console.log(*args, **kwargs)

@dataclass
class HideText:
    not_started: str = ""
    finished: str = ""
    unknown_total: str = ""

class ConditionalColumn(ProgressColumn):
    """Conditionally hides a ProgressColumn based on whether the task is started, finished, or has an unknown total."""
    def __init__(self,
                 wrapped_column: ProgressColumn,
                 hidden_text: str | HideText = "",
                 hide_not_started: bool = True,
                 hide_finished: bool = True,
                 hide_unknown_total: bool = False):
        super().__init__(wrapped_column.get_table_column()) 

        self.wrapped_column = wrapped_column
        if isinstance(hidden_text, str):
            self.hidden_text = HideText(hidden_text, hidden_text, hidden_text)
        else:
            self.hidden_text = hidden_text
        self.hide_not_started = hide_not_started
        self.hide_finished = hide_finished
        self.hide_unknown_total = hide_unknown_total

    def render(self, task: Task):
        if not task.started and self.hide_not_started:
            return Text.from_markup(self.hidden_text.not_started)
        
        if task.finished and self.hide_finished:
            return Text.from_markup(self.hidden_text.finished)
        
        if task.total is None and self.hide_unknown_total:
            return Text.from_markup(self.hidden_text.unknown_total)            
        
        return self.wrapped_column.render(task)

class JoinColumns(ProgressColumn):
    """Joins multiple ProgressColumns together without the default padding in-between."""
    def __init__(self, *columns: ProgressColumn, table_column: Column | None = None):
        super().__init__(table_column=table_column) 
        self.columns = columns

    def render(self, task: Task):
        header_columns = (
            (
                _column.get_table_column().copy()
            )
            for _column in self.columns
        )

        table = Table.grid(*header_columns)
        table.add_row(*(
            col.render(task) for col in self.columns
        ))

        return table


class FileNameColumn(ProgressColumn):
    def __init__(self, indent:int = 0, table_column: Column|None = None) -> None:
        super().__init__(table_column=table_column) 
        self.indent = indent

    def render(self, task: Task) -> Text:
        filename = task.fields['filename']
        prefix = task.fields.get("filename_prefix", "")
        failed = task.fields.get("failed", False)
        indent = ' ' * self.indent

        if failed:
            return Text.from_markup(f"{indent}[red]:x:{prefix}{filename}")
        else:
            return Text.from_markup(f"{indent}[magenta]{prefix}{filename}")


def create_spinner_step_progress(console: Console, description: str, color: str, width: int = 15) -> ProgressWrapper:
    progress = Progress(
        ConditionalColumn(SpinnerColumn(), hidden_text=HideText(" ", "[green]✓", " ")),
        TextColumn("{task.description}", style=color, table_column=Column(width=width)),
        ConditionalColumn(TimeElapsedColumn(), hide_finished=False),
        console=console
    )
    task_id = progress.add_task(description, start=False, visible=False, total=1)

    return ProgressWrapper(progress, task_id)


def create_bar_step_progress(console: Console, description: str, color: str, width: int = 15) -> ProgressWrapper:
    progress = Progress(
        ConditionalColumn(SpinnerColumn(), hidden_text=HideText(" ", "[green]✓", " ")),
        TextColumn("{task.description}", style=color, table_column=Column(width=width)),
        ConditionalColumn(TimeElapsedColumn(), hide_finished=False),
        ConditionalColumn(BarColumn()),
        TaskProgressColumn(),
        ConditionalColumn(MofNCompleteColumn(), hide_finished=False),
        console=console
    )
    task_id = progress.add_task(description, start=False, visible=False, total=None)

    return ProgressWrapper(progress, task_id)


def create_file_subtask_progress(console: Console) -> Progress:
    return Progress(
        FileNameColumn(indent=4),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        JoinColumns(
            TextColumn("["),
            TimeElapsedColumn(),
            TextColumn("<"),
            TimeRemainingColumn(),
            TextColumn(", "),
            TransferSpeedColumn(),
            TextColumn("]")),
        console=console
    )