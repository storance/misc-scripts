import pathlib
import re
import math
import copy
import logging
from itertools import batched
from dataclasses import dataclass, field
from typing import Sequence
from .common import SrcDestPair
from .. import GroupByRegionConfig, GroupByLangConfig, GroupByGameConfig, GroupByPrefixConfig, GroupingConfig, \
    GroupType, MultiRegionMode, RomFile, WORLD_REGION, EUROPE_REGION, ASIA_REGION

NON_ALNUM_PATTERN = re.compile('[^a-zA-Z0-9]+')


@dataclass
class Grouping:
    name: str
    children: list[Grouping | pathlib.Path] = field(default_factory=list)

    def to_src_dst_pairs(self, parent: pathlib.Path) -> list[SrcDestPair]:
        path = parent / self.name
        pairs = []

        for child in self.children:
            if isinstance(child, Grouping):
                pairs.extend(child.to_src_dst_pairs(path))
            else:
                pairs.append(SrcDestPair(child, path / child.name))

        return pairs


class Grouper:
    def __init__(self, next_grouper: Grouper | None):
        self.next_grouper = next_grouper

    def create_group(self, src_path: pathlib.Path, files: list[pathlib.Path]) -> list[Grouping | pathlib.Path]:
        groups_by_name = {}
        children = []
        for file in files:
            rom_file = RomFile.parse_from_name(file.relative_to(src_path))
            groups = self.get_groups(rom_file)

            logging.debug("Grouper %s: Grouping \"%s\" into groups [%s]",
                          self.__class__.__name__, file, ','.join(groups))

            for group in groups:
                if group not in groups_by_name:
                    grouping = Grouping(group)
                    groups_by_name[group] = grouping
                    children.append(grouping)

                groups_by_name[group].children.append(file)

        for child_group in children:
            if self.next_grouper is not None:
                child_group.children = self.next_grouper.create_group(src_path, child_group.children)
            else:
                child_group.children = sorted(child_group.children)

        children = sorted(children, key=lambda x: x.name)
        return self.post_process(children)

    def get_groups(self, game: RomFile) -> list[str]:
        raise NotImplemented

    def post_process(self, children: list[Grouping | pathlib.Path]) -> list[Grouping | pathlib.Path]:
        raise NotImplemented


class GroupByGame(Grouper):
    def __init__(self, config: GroupByGameConfig, next_grouper: Grouper):
        super().__init__(next_grouper)
        self.config = config

    def get_groups(self, game: RomFile) -> list[str]:
        for name, patterns in self.config.custom_mapping.items():
            if any(pattern.matches(game.file) for pattern in patterns):
                return [name]

        return [game.to_game_name(not self.config.strip_regions, not self.config.strip_langs)]

    def post_process(self, children: list[Grouping | pathlib.Path]) -> list[Grouping | pathlib.Path]:
        if self.config.flatten_single_rom and len(children) == 1:
            return _flatten_children(children)
        return children


class GroupByLang(Grouper):
    def __init__(self, config: GroupByLangConfig, next_grouper: Grouper):
        super().__init__(next_grouper)
        self.config = config

    def get_groups(self, game: RomFile) -> list[str]:
        if self.config.single_per_rom:
            langs = sorted(game.langs, key=lambda l: self.config.prefer_ranks.get(l, float('inf')))
            return [langs[0]]
        else:
            return list(game.langs)

    def post_process(self, children: list[Grouping | pathlib.Path]) -> list[Grouping | pathlib.Path]:
        if self.config.flatten_single_lang and len(children) == 1:
            logging.debug("Grouper %s: Collapsing single lang %s", self.__class__.__name__, children[0].name)
            return _flatten_children(children)
        return children


class GroupByRegion(Grouper):
    def __init__(self, config: GroupByRegionConfig, next_grouper: Grouper):
        super().__init__(next_grouper)
        self.config = config

    def get_groups(self, game: RomFile) -> list[str]:
        regions = set()
        for region in game.regions:
            if region == WORLD_REGION and self.config.world_mode == MultiRegionMode.EXPAND_TO_COUNTRIES:
                regions.update(region.children)
            elif region == EUROPE_REGION and self.config.europe_mode == MultiRegionMode.EXPAND_TO_COUNTRIES:
                regions.update(region.children)
            elif region == ASIA_REGION and self.config.asia_mode == MultiRegionMode.EXPAND_TO_COUNTRIES:
                regions.update(region.children)
            elif self.config.world_mode == MultiRegionMode.COLLAPSE_TO_GROUPED and WORLD_REGION.is_member(region):
                regions.add(WORLD_REGION)
            elif self.config.europe_mode == MultiRegionMode.COLLAPSE_TO_GROUPED and EUROPE_REGION.is_member(region):
                regions.add(EUROPE_REGION)
            elif self.config.asia_mode == MultiRegionMode.COLLAPSE_TO_GROUPED and ASIA_REGION.is_member(region):
                regions.add(ASIA_REGION)
            else:
                regions.add(region)

        if self.config.single_per_rom:
            sorted_regions = sorted(game.regions, key=lambda l: self.config.prefer_ranks.get(l, float('inf')))
            return [sorted_regions[0].display_name(self.config.use_short_names)]
        else:
            return [region.display_name(self.config.use_short_names) for region in regions]

    def post_process(self, children: list[Grouping | pathlib.Path]) -> list[Grouping | pathlib.Path]:
        if self.config.flatten_single_region and len(children) == 1:
            logging.debug("Grouper %s: Collapsing single region %s", self.__class__.__name__, children[0].name)
            return _flatten_children(children)
        return children


class GroupByPrefix(Grouper):
    def __init__(self, config: GroupByPrefixConfig, next_grouper: Grouper):
        super().__init__(next_grouper)
        self.config = config

    def get_groups(self, game: RomFile) -> list[str]:
        # replace non-alphanumeric english characters with # and uppercase the string
        prefix = NON_ALNUM_PATTERN.sub('#', game.file.name[:self.config.length]).upper()

        return [prefix]

    def post_process(self, children: list[Grouping | pathlib.Path]) -> list[Grouping | pathlib.Path]:
        new_children = self._split_large_prefix_buckets(children)
        new_children = self._collapse_prefixes_into_ranges(new_children)

        if len(new_children) == 1 and self.config.flatten_single_prefix:
            logging.debug("Grouper %s: Collapsing single prefix %s", self.__class__.__name__, children[0].name)
            return _flatten_children(new_children)

        return new_children

    def _split_large_prefix_buckets(self, children: list[Grouping | pathlib.Path]) -> list[Grouping | pathlib.Path]:
        if self.config.limit is not None:
            new_children = []
            # split a any prefix groups that exceed the configured limit
            for child in children:
                if not isinstance(child, Grouping):
                    raise ValueError(f'Unexpected child in letter groupings list: {child}')

                if len(child.children) > self.config.limit:
                    split_count = math.ceil(len(child.children) / self.config.limit)
                    chunk_size = math.ceil(len(child.children) / split_count)
                    digits_count = len(str(split_count))

                    logging.debug("Grouper %s: Splitting prefix %s to %d buckets",
                                  self.__class__.__name__, child.name, split_count)

                    for idx, batch in enumerate(batched(child.children, chunk_size), start=1):
                        new_name = f"{child.name}{idx:{digits_count}d}"
                        new_children.append(Grouping(new_name, list(batch)))
                else:
                    new_children.append(child)
            return new_children
        else:
            return children

    def _collapse_prefixes_into_ranges(self, children: list[Grouping | pathlib.Path]) -> list[Grouping | pathlib.Path]:
        if self.config.collapse_prefixes and self.config.limit is not None:
            collapsed_children = []

            current_bucket = None
            for child in children:
                if not isinstance(child, Grouping):
                    raise ValueError(f'Unexpected child in letter groupings list: {child}')

                if current_bucket is None:
                    current_bucket = _RangeBucket(self.config.limit, child)
                elif not current_bucket.add(child):
                    logging.debug("Grouper %s: Collapsing range %s to %s", self.__class__.__name__,
                                  current_bucket.start_prefix, current_bucket.end_prefix)
                    collapsed_children.append(current_bucket.to_group())
                    current_bucket = _RangeBucket(self.config.limit, child)

            if current_bucket is not None:
                logging.debug("Grouper %s: Collapsing range %s to %s", self.__class__.__name__,
                              current_bucket.start_prefix, current_bucket.end_prefix)
                collapsed_children.append(current_bucket.to_group())

            return collapsed_children
        else:
            return children


class _RangeBucket:
    start_prefix: str
    end_prefix: str
    children: list[Grouping | pathlib.Path]

    def __init__(self, max_children: int, initial: Grouping):
        if initial.name is None:
            raise ValueError("Missing name for grouping")
        self.max_children = max_children
        self.start_prefix = initial.name
        self.end_prefix = initial.name
        self.children = copy.copy(initial.children)

    def add(self, group: Grouping) -> bool:
        if len(self.children) + len(group.children) >= self.max_children:
            return False

        if group.name is None:
            raise ValueError("Missing name for grouping")

        self.children.extend(group.children)
        self.end_prefix = group.name
        return True

    def to_group(self) -> Grouping:
        if self.start_prefix == self.end_prefix:
            name = self.start_prefix
        else:
            name = f"{self.start_prefix}-{self.end_prefix}"

        return Grouping(name, self.children)


def _flatten_children(children: Sequence[Grouping | pathlib.Path]) -> list[Grouping | pathlib.Path]:
    new_children = []
    for child in children:
        if isinstance(child, Grouping):
            new_children.extend(child.children)
        else:
            new_children.append(child)

    return new_children


def create_groupers(config: GroupingConfig) -> Grouper | None:
    grouper = None

    for group_type in reversed(config.group_by):
        grouper = _create_grouper(group_type, grouper, config)

    return grouper


def _create_grouper(group_type: GroupType, next_grouper: Grouper | None, config: GroupingConfig) -> Grouper:
    if group_type == GroupType.GAME:
        return GroupByGame(config.by_game, next_grouper)  # type: ignore parser ensures this is not None
    elif group_type == GroupType.REGION:
        return GroupByRegion(config.by_region, next_grouper)  # type: ignore parser ensures this is not None
    elif group_type == GroupType.LANG:
        return GroupByLang(config.by_lang, next_grouper)  # type: ignore parser ensures this is not None
    elif group_type == GroupType.PREFIX:
        return GroupByPrefix(config.by_prefix, next_grouper)  # type: ignore parser ensures this is not None
    else:
        raise ValueError(f"Unknown group type: {group_type}")
