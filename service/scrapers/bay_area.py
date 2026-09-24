"""Shared Bay Area location filter.

Some sources are statewide or national (a touring dance company, a statewide
Luma calendar), so we keep only events whose location names a Bay Area
city/region — Agora is an SF Bay aggregator. Titles rarely encode the city, so
match on the ``location`` string.
"""
import re

_BAY_AREA_RE = re.compile(
    r"\b(san francisco|sf|oakland|berkeley|san jose|bay area|alameda|emeryville"
    r"|richmond|marin|sausalito|daly city|south san francisco|fremont|hayward"
    r"|palo alto|mountain view|sunnyvale|santa clara|cupertino|redwood city"
    r"|menlo park|san mateo|burlingame|los gatos|milpitas|union city|san rafael"
    r"|novato|petaluma|santa rosa|sonoma|napa|vallejo|walnut creek|concord"
    r"|pleasanton|livermore|dublin|el cerrito|albany|san leandro|pacifica"
    r"|corte madera|mill valley|larkspur|stanford|moraga|orinda|san bruno)\b",
    re.IGNORECASE,
)


def is_bay_area(location: str | None) -> bool:
    return bool(location and _BAY_AREA_RE.search(location))
