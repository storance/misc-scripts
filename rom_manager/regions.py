from dataclasses import dataclass, field

__all__ = [
    "Region",
    "REGIONS_BY_NAME",
    "REGIONS_BY_LONG_NAME",
    "UNKNOWN_REGION",
    "USA_REGION",
    "CANADA_REGION",
    "MEXICO_REGION",
    "BRAZIL_REGION",
    "FRANCE_REGION",
    "GERMANY_REGION",
    "ITALY_REGION",
    "UNITED_KINGDOM_REGION",
    "SWEDEN_REGION",
    "RUSSIA_REGION",
    "SPAIN_REGION",
    "PORTUGAL_REGION",
    "NETHERLANDS_REGION",
    "GREECE_REGION",
    "FINLAND_REGION",
    "DENMARK_REGION",
    "NORWAY_REGION",
    "BELGIUM_REGION",
    "SWITZERLAND_REGION",
    "AUSTRIA_REGION",
    "IRELAND_REGION",
    "TURKEY_REGION",
    "SCANDINAVIA_REGION",
    "JAPAN_REGION",
    "KOREA_REGION",
    "CHINA_REGION",
    "TAIWAN_REGION",
    "HONG_KONG_REGION",
    "UAE_REGION",
    "INDIA_REGION",
    "AUSTRALIA_REGION",
    "NEW_ZEALAND_REGION",
    "EUROPE_REGION",
    "ASIA_REGION",
    "WORLD_REGION",
    "lookup_region"
]

REGIONS_BY_NAME = {}
REGIONS_BY_LONG_NAME = {}

@dataclass(frozen=True)
class Region:
    name: str
    long_name: str
    default_lang: str
    children: list[Region] = field(default_factory=list, hash=False)

    def __post_init__(self):
        global REGIONS_BY_NAME, REGIONS_BY_LONG_NAME
        REGIONS_BY_NAME[self.name] = self
        REGIONS_BY_LONG_NAME[self.long_name] = self

    def is_member(self, region: Region) -> bool:
        if not self.children:
            return False

        if region in self.children:
            return True

        return any(child.is_member(region) for child in self.children)

    def display_name(self, use_short_name: bool = False) -> str:
        return self.name if use_short_name else self.long_name

UNKNOWN_REGION = Region('UNK', 'Unknown', 'En')
USA_REGION = Region('USA', 'USA', 'En')
CANADA_REGION = Region('CAN', 'Canada', 'En')
MEXICO_REGION = Region('MEX', 'Mexico', 'Es')
BRAZIL_REGION = Region('BRA', 'Brazil', 'Pt')
FRANCE_REGION = Region('FRA', 'France', 'Fr')
GERMANY_REGION = Region('GER', 'Germany', 'De')
ITALY_REGION = Region('ITA', 'Italy', 'It')
UNITED_KINGDOM_REGION = Region('UK', 'United Kingdom', 'En')
SWEDEN_REGION = Region('SWE', 'Sweden', 'Sv')
RUSSIA_REGION = Region('RUS', 'Russia', 'Ru')
SPAIN_REGION = Region('SPA', 'Spain', 'Es')
PORTUGAL_REGION = Region('POR', 'Portugal', 'Pt')
NETHERLANDS_REGION = Region('HOL', 'Netherlands', 'Nl')
GREECE_REGION = Region('GRE', 'Greece', 'El')
FINLAND_REGION = Region('FYN', 'Finland', 'Fi')
DENMARK_REGION = Region('DAN', 'Denmark', 'Da')
NORWAY_REGION = Region('NOR', 'Norway', 'No')
BELGIUM_REGION = Region('BEL', 'Belgium', 'Fr')
SWITZERLAND_REGION = Region('CHE', 'Switzerland', 'En')
AUSTRIA_REGION = Region('AUT', 'Austria', 'De')
IRELAND_REGION = Region('IRL', 'Ireland', 'En')
TURKEY_REGION = Region('TUR', 'Turkey', 'Tr')
SCANDINAVIA_REGION = Region('SCA', 'Scandinavia', 'En', [
    SWEDEN_REGION,
    NORWAY_REGION,
    DENMARK_REGION
])

JAPAN_REGION = Region('JPN', 'Japan', 'Ja')
KOREA_REGION = Region('KOR', 'Korea', 'Ko')
CHINA_REGION = Region('CHN', 'China', 'Zh')
TAIWAN_REGION = Region('TAI', 'Taiwan', 'Zh')
HONG_KONG_REGION = Region('HK', 'Hong Kong', 'Zh')
UAE_REGION = Region('UAE', 'United Arab Emirates', 'En')
INDIA_REGION = Region('IND', 'India', 'En')
AUSTRALIA_REGION = Region('AUS', 'Australia', 'En')
NEW_ZEALAND_REGION = Region('NZ', 'New Zealand', 'En')

EUROPE_REGION = Region('EUR', 'Europe', 'En', [
    FRANCE_REGION,
    GERMANY_REGION,
    BELGIUM_REGION,
    SWITZERLAND_REGION,
    AUSTRIA_REGION,
    UNITED_KINGDOM_REGION,
    IRELAND_REGION,
    RUSSIA_REGION,
    SPAIN_REGION,
    ITALY_REGION,
    PORTUGAL_REGION,
    GREECE_REGION,
    NETHERLANDS_REGION,
    SWEDEN_REGION,
    FINLAND_REGION,
    DENMARK_REGION,
    NORWAY_REGION,
    TURKEY_REGION
])
ASIA_REGION = Region('ASI', 'Asia', 'En', [
    KOREA_REGION,
    CHINA_REGION,
    TAIWAN_REGION,
    HONG_KONG_REGION,
    UAE_REGION,
    INDIA_REGION
])
WORLD_REGION = Region('WORLD', 'World', 'En', [
    USA_REGION,
    EUROPE_REGION,
    JAPAN_REGION
])


def lookup_region(name: str) -> Region|None:
    if name in REGIONS_BY_NAME:
        return REGIONS_BY_NAME[name]
    if name in REGIONS_BY_LONG_NAME:
        return REGIONS_BY_LONG_NAME[name]

    return None