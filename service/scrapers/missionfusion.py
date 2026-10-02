"""Mission Fusion (SF fusion partner dance) — via its Facebook Page.

A thin wrapper around scrapers/facebook.py. Mission Fusion runs a class + social
dance on the first and third Saturdays at St. Gregory of Nyssa (Potrero Hill).
Facebook is where each night is posted with its DJs and teacher;
missionfusion.com only lists bare dates.
"""
from scrapers import facebook
from scrapers.base import RawEvent


SOURCE = "facebook.com"
NAME = "Mission Fusion"
PAGE = "MissionFusion"
CALENDAR_URL = f"https://www.facebook.com/{PAGE}/events"


def matches(url: str) -> bool:
    return "facebook.com/missionfusion" in url.lower()


def scrape(url: str = CALENDAR_URL) -> list[RawEvent]:
    return facebook.scrape_page(PAGE)
