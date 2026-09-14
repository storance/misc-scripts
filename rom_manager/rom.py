import re
import pathlib
import langcodes
from dataclasses import dataclass
from .common import get_stem
from .regions import Region, UNKNOWN_REGION, lookup_region

_NAME_PARSER = re.compile(r'^(.*?)(?:\s+\((.+?)\))(?:\s+\((.+?)\))?.*$')

__all__ = ['RomFile']

@dataclass
class RomFile:
    file: pathlib.Path
    title: str
    raw_regions: list[str]
    regions: set[Region]
    raw_langs: list[str]
    langs: set[str]

    @staticmethod
    def parse_from_name(file: pathlib.Path) -> RomFile:
        """Parses details about a rom file that is using No-Intro or Redump naming conventions."""
        name = get_stem(file)
        match = _NAME_PARSER.match(name)
        if match is None:
            raise ValueError(f"Could not parse the game \"{name}\"")

        title = match.group(1)
        if match.group(2) is not None:
            raw_regions = match.group(2).split(',')
            regions = set()
            for region_name in raw_regions:
                region = lookup_region(region_name.strip()) 
                if region is not None:
                    regions.add(region)
        else:
            raw_regions = []
            regions = {UNKNOWN_REGION}

        if match.group(3) is not None:
            raw_langs = [lang.strip() for lang in match.group(3).split(',')]
            if not all(_is_valid_lang(lang) for lang in raw_langs):
                langs = set()
            else:
                langs = set(raw_langs)
        else:
            raw_langs = []
            if len(regions) == 1:
                langs = {next(iter(regions)).default_lang}
            else:
                langs = {UNKNOWN_REGION.default_lang}

        return RomFile(file, title, raw_regions, regions, raw_langs, langs)

    def to_game_name(self, include_regions: bool, include_langs: bool) -> str:
        name = self.title

        if include_regions and self.raw_regions:
            name = f"{name} ({', '.join(self.raw_regions)})"

        if include_langs and self.langs:
            name = f"{name} ({', '.join(self.langs)})"

        return name

def _is_valid_lang(lang_code:str) -> bool:
    return len(lang_code) == 2 \
        and lang_code[0].isupper() \
        and lang_code[1].islower() \
        and langcodes.Language(lang_code).is_valid()
