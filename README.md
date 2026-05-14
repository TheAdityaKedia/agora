# Agora

Agora is an event aggregator that collects happenings from around your city into a single calendar.

It monitors a curated list of event sources — bookstore websites, dance venues, community organizations — and keeps a unified, searchable calendar up to date. Events are automatically tagged by type, making it easy to filter by what you're in the mood for: a bachata social, an author talk, a free poetry reading.

You can also submit events directly by forwarding an email or sending a screenshot of a flyer. Agora will parse it and add it to the calendar.

## What it does

- Scrapes event listings from configured websites on a regular schedule
- Monitors a Gmail inbox for forwarded event emails and flyer images
- Extracts structured event data using AI
- Auto-tags events by category (dance, lecture, free, paid, etc.)
- Exposes a REST API for querying events by date range, tag, or source
- Serves a calendar UI for browsing what's on

## Structure

```
agora/
├── service/      # Python backend — scraping, email monitoring, API
├── cdk/          # AWS CDK infrastructure
└── frontend/     # Calendar UI (React)
```

## Running locally

```bash
cd service
cp .env.example .env   # fill in credentials
docker compose up
```

## Deploying to AWS

```bash
cd cdk
pip install -r requirements.txt
cdk deploy
```
