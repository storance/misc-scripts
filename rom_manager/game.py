import re
import langcodes
from dataclasses import dataclass

NAME_PARSER = re.compile(r'^(.*?)\s+\((.+?)\)(?:\s*\((.+?)\))?.*$')

@dataclass
class Region:
    name: str
    long_name: str
    default_lang: str

@dataclass
class Game:
    full_name: str
    title: str
    regions: list[Region]
    langs: list[str]

    @staticmethod
    def parse_from_name(name: str) -> Game:
        match = NAME_PARSER.match(name)
        if match is None:
            raise ValueError(f'File name "{name}" does not match no-intro or redump naming conventions.')

        title = match.group(1)
        raw_regions = match.group(2).split(',')
        regions = [lookup_region(region.strip()) for region in raw_regions]

        if match.group(3) is not None:
            langs = [lang.strip() for lang in match.group(3).split(',')]
            if not all(_is_valid_lang(lang) for lang in langs):
                langs = []
        else:
            langs = []

        return Game(name, title, regions, langs)

def _is_valid_lang(lang_code:str) -> bool:
    return len(lang_code) == 2 \
        and lang_code[0].isupper() \
        and lang_code[1].islower() \
        and langcodes.Language(lang_code).is_valid()


UNKNOWN_REGION=Region('UNK', 'Unknown', 'En')

REGIONS = [
    # North America
    Region('USA', 'USA', 'En'),
    Region('CAN', 'Canada', 'En'),
    Region('MEX', 'Mexico', 'Es'),

    # South America
    Region('BRA', 'Brazil', 'Pt'),

    #Europe
    Region('FR', 'France', 'Fr'),
    Region('GER', 'Germany', 'De'),
    Region('ITA', 'Italy', 'It'),
    Region('UK', 'United Kingdom', 'En'),
    Region('SWE', 'Sweden', 'Sv'),
    Region('RUS', 'Russia', 'Ru'),
    Region('SPA', 'Spain', 'Es'),
    Region('POR', 'Portugal', 'Pt'),
    Region('HOL', 'Netherlands', 'Nl'),
    Region('GRE', 'Greece', 'El'),
    Region('FYN', 'Finland', 'Fi'),
    Region('DAN', 'Denmark', 'Da'),
    Region('NOR', 'Norway', 'No'),
    Region('BEL', 'Belgium', 'Fr'),
    Region('CHE', 'Switzerland', 'En'),
    Region('AUT', 'Austria', 'De'),
    Region('SCA', 'Scandinavia', 'En'),
    Region('IRL', 'Ireland', 'En'),
    
    # Asian Regions
    Region('JPN', 'Japan', 'Ja'),
    Region('KOR', 'Korea', 'Ko'),
    Region('CHN', 'China', 'Zh'),
    Region('TAI', 'Taiwan', 'Zh'),
    Region('HK', 'Hong Kong', 'Zh'),
    Region('UAE', 'United Arab Emirates', 'En'),
    Region('TUR', 'Turkey', 'Tr'),
    Region('IND', 'India', 'En'),
    
    # Oceania
    Region('AUS', 'Australia', 'En'),
    Region('NZ', 'New Zealand', 'En'),

    # Grouped Regions
    Region('EUR', 'Europe', 'En'),
    Region('ASI', 'Asia', 'Zh'),
    Region('WORLD', 'World', 'En'),
    Region('LATAM', 'Latin America', 'Es'),
    UNKNOWN_REGION
]

def lookup_region(name: str) -> Region:
    for region in REGIONS:
        if region.name == name or region.long_name == name:
            return region

    raise ValueError(f"Region '{name}' is not supported")