# Agora — Development Notes

## Long-term vision

Agora becomes the go-to place to find out what's happening in your city. A small group of people who care about a particular kind of cultural life — dance, literature, music — can point Agora at the places they already follow and get one unified calendar in return. No more checking five websites, no more missing events.

In the long run: any website can be a source, events flow in from scrapers, email, images, and manual entry, every event is tagged automatically, and the calendar is shareable.

## Phases

**Phase 1 — Green Apple Books, database, API**
One scraper (Green Apple Books) using `requests` + BeautifulSoup (server-rendered HTML). PostgreSQL database, REST API (FastAPI). Prove the pipeline end to end: fetch page → parse events → store → serve via API.

**Phase 2 — Playwright + City Lights + Black Bird SF**
Introduce Playwright for JS-rendered and WAF-protected sites. Add City Lights Books (WordPress + Sucuri) and Black Bird SF (Shopify).

**Phase 3 — More sources**
Strategy pattern. Each source gets its own scraper class. Add sources as needed; the scheduler doesn't care which one it's calling.

**Phase 4 — Email ingestion**
Monitor a Gmail inbox. Forward an email or a flyer screenshot to the monitored address and it gets parsed and added to the calendar. This is also how login-gated sources (Facebook groups, etc.) feed into Agora.

**Phase 5 — AI tagging**
Send each new event's description to AWS Bedrock. Get back a set of tags (dance, zouk, lecture, free, paid, etc.). Store tags. Expose tag filtering on the API.

**Phase 6 — Frontend**
A React calendar (month/week/day views) backed by the API. Tag filter sidebar. Event detail view.

CDK infrastructure is added incrementally alongside each phase — no big-bang infra build.

## Design principles

- **Start minimal.** Add infrastructure only when the simpler thing breaks.
- **Strategies, not conditionals.** Each source gets its own class; the pipeline doesn't care which one it's calling.
- **AI at the edges.** Bedrock handles unstructured input. Structured sources are parsed directly.
- **Portable.** Everything containerized, all infra as code, no manual console steps. Moving to a new AWS account means `cdk deploy`.

## Current status

**Phase 0 — Design** ✓

Repo initialized. Vision and phases agreed. README and development notes written.

**Phase 1 — In progress**

Next: write `service/data/sources.txt`, scaffold service skeleton, build Green Apple scraper.
