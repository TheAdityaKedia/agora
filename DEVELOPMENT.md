# Agora — Development Notes

## Long-term vision

Agora becomes the go-to place to find out what's happening in your city. A small group of people who care about a particular kind of cultural life — dance, literature, music — can point Agora at the places they already follow and get one unified calendar in return. No more checking five websites, no more missing events.

In the long run: any website can be a source, events flow in from scrapers, email, images, and manual entry, every event is tagged automatically, and the calendar is shareable.

## Phases

**Phase 1 — Two sources, database, API**
Two scrapers (Green Apple Books, City Lights Books), one PostgreSQL database, one cron schedule, and a REST API. Prove the full pipeline end to end: fetch page → parse events → store → serve via API.

**Phase 2 — More sources, strategy pattern**
Introduce the scraper strategy abstraction so adding a new source is a two-step process: write a strategy, register a URL. Add sources as needed. Each source gets its own strategy; the scheduler doesn't care which one it's calling.

**Phase 3 — Email ingestion**
Monitor a Gmail inbox. Forward an email or a flyer screenshot to the monitored address and it gets parsed and added to the calendar. This is also how login-gated sources (Facebook groups, etc.) feed into Agora.

**Phase 4 — AI tagging**
Send each new event's description to AWS Bedrock. Get back a set of tags (dance, zouk, lecture, free, paid, etc.). Store tags. Expose tag filtering on the API.

**Phase 5 — Frontend**
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

Next: Phase 1 — scraper for Green Apple Books and City Lights Books, PostgreSQL schema, and REST API.
