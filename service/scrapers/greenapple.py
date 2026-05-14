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


def _extract_detail(row, label: str) -> str | None:
    for item in row.select("div.event-list__details--item"):
        lbl = item.find("span", class_="event-list__details--label")
        if lbl and label in lbl.get_text():
            # remove the label span then return remaining text
            lbl.extract()
            return item.get_text(separator=" ", strip=True)
    return None


def scrape(url: str) -> list[RawEvent]:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    events = []
    for row in soup.select("div.views-row"):
        title_tag = row.find("h3", class_="event-list__title")
        if not title_tag:
            continue

        link = title_tag.find("a")
        if not link:
            continue

        title = link.get_text(strip=True)
        event_url = urljoin(BASE_URL, link["href"])

        date_str = _extract_detail(row, "Date:")
        time_str = _extract_detail(row, "Time:")

        description_tag = row.find("div", class_="event-list__body")
        description = description_tag.get_text(strip=True) if description_tag else None

        location_tag = row.find("address")
        location = location_tag.get_text(separator=", ", strip=True) if location_tag else None

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
            description=description,
        ))

    return events
