import pathlib
from dataclasses import dataclass
from ..profiles import GroupType, GroupByRegionConfig, GroupByLangConfig, GroupByPrefixConfig
from ..game import Game

@dataclass(frozen=True)
class CopyRom:
    src: pathlib.Path
    name: str

@dataclass(frozen=True)
class Grouping:
    name: str
    type: GroupType
    children: list[Grouping|CopyRom]

class Grouper:
    def get_groups(self, game: Game) -> list[str]:
        raise NotImplemented

    def collapse(self, group: Grouping) -> Grouping:
        raise NotImplemented

class GroupByRegion(Grouper):
    def __init__(self, config: GroupByRegionConfig):
        self.config = config