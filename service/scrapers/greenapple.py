from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://greenapplebooks.com"


@dataclass
class RawEvent:
    title: str
    start_time: datetime
    location: str | None
    url: str
    description: str | None


def _parse_datetime(date_str: str, time_str: str) -> datetime | None:
    # date_str: "Mon, 5/4/2026"  time_str: "7:00pm"
    try:
        date_part = date_str.split(", ", 1)[-1].strip()  # "5/4/2026"
        return datetime.strptime(f"{date_part} {time_str.strip()}", "%m/%d/%Y %I:%M%p")
    except ValueError:
        return None


def _extract_field(row, label: str) -> str | None:
    for p in row.find_all("p"):
        text = p.get_text(strip=True)
        if text.startswith(label):
            return text[len(label):].strip()
    return None


def scrape(url: str) -> list[RawEvent]:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    events = []
    for row in soup.select("div.views-row"):
        title_tag = row.find("h3")
        if not title_tag:
            continue

        link = title_tag.find("a")
        if not link:
            continue

        title = link.get_text(strip=True)
        event_url = urljoin(BASE_URL, link["href"])

        date_str = _extract_field(row, "Date:")
        time_str = _extract_field(row, "Time:")
        location = _extract_field(row, "Place:")

        if not date_str or not time_str:
            continue

        start_time = _parse_datetime(date_str, time_str)
        if not start_time:
            continue

        events.append(RawEvent(
            title=title,
            start_time=start_time,
            location=location,
            url=event_url,
            description=None,
        ))

    return events
