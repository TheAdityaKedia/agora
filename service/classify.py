"""LLM event classifier — two axes (type + topic) + cost, via Bedrock.

`classify_show(title, source, description)` returns a validated Classification.
The Bedrock call is isolated and the client is injectable so tests never hit
the network. Model output is always run through the taxonomy validators, so a
slightly-off response (unknown leaf, invalid topic, duplicate path) is coerced
rather than trusted.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import taxonomy
from classifications import Classification

# Primary: global cross-region Haiku 4.5 inference profile. Fallback: the
# direct foundation-model id (same model, no cross-region routing) for when the
# inference profile is unavailable.
PRIMARY_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
FALLBACK_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"

_VALID_COSTS = {"free", "paid", "unknown"}
_DATA_DIR = Path(__file__).parent / "data"
_DESC_CAP = 1500


@lru_cache(maxsize=None)
def _source_profiles() -> dict[str, str]:
    path = _DATA_DIR / "source_profiles.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("profiles", {})


def source_profile(source: str) -> str | None:
    return _source_profiles().get(source)


_SYSTEM = (
    "You classify San Francisco Bay Area event listings on two independent axes "
    "and return ONLY a JSON object — no prose, no markdown fences.\n\n"
    'Schema: {"types": [["top","sub"], ...], "topics": ["slug", ...], '
    '"cost": "free|paid|unknown"}\n\n'
    "TYPE = the event FORMAT (what you physically do). Rules: return 1-2 type "
    "paths, most relevant first; always at least one. Go one level deep only "
    "when the sub-format clearly applies; else give just the top level. Never "
    "repeat a path. A film/movie showing is [\"screening\"] (+ topic film/"
    "documentary), never a performance, even when the description is sparse.\n\n"
    "TOPIC = what the event is ABOUT / the interest it serves. Return 1-3 topic "
    "slugs from the TOPIC list, most relevant first. Topics cut across formats "
    "(a poetry reading, open mic, and workshop all get \"poetry\"). Choose only "
    "listed slugs; omit rather than invent. Assign at least one topic whenever "
    "the subject is identifiable — leave topics empty ONLY when no listed slug "
    "genuinely fits. Guidance for the broad slugs: use \"music\" for a concert, "
    "DJ set, open mic, or jam whose genre is unclear or mixed (prefer a specific "
    "genre like \"jazz\"/\"electronic\" when it's clear); \"games\" for board/"
    "tabletop/hobby game nights (not trivia/bingo, which have their own slugs); "
    "\"language\" for language-exchange / conversation-practice groups; "
    "\"club-night\" for DJ sets, dance parties, and club nights (add the music "
    "genre too when clear, e.g. [\"club-night\",\"electronic\"]).\n\n"
    "Use the VENUE profile as a strong prior, especially when the description "
    "is short or empty: a screening at a repertory cinema is [\"screening\"]+"
    "[\"film\"]; a show at a jazz hall is [\"performance\"]+[\"jazz\"]; a reading "
    "at a bookstore is [\"talk\",\"reading\"]+[\"books-authors\"].\n\n"
    'cost: "free" / "paid" / "unknown" (unknown when the listing does not say).'
)

_FEWSHOT = (
    "Examples:\n"
    "Title: Branford Marsalis Quartet\n"
    "Venue: SFJAZZ Center — Jazz concert hall; live jazz concerts.\n"
    "Desc: The saxophonist's acclaimed quartet performs.\n"
    '{"types":[["performance"]],"topics":["jazz"],"cost":"paid"}\n\n'
    "Title: Poetry Open Mic\n"
    "Venue: Bird & Beckett — Glen Park bookstore; jazz series and poetry readings.\n"
    "Desc: Sign up to read your own work, or just listen.\n"
    '{"types":[["social"],["performance"]],"topics":["poetry","spoken-word"],"cost":"free"}\n\n'
    "Title: Wild Surf Writers: Where Water Women Write\n"
    "Venue: Black Bird Bookstore — neighborhood bookstore; readings, writing circles.\n"
    "Desc: A women's guided two-hour writing practice; open to all women-identifying people. $15.\n"
    '{"types":[["workshop"]],"topics":["writing","women","poetry"],"cost":"paid"}\n\n'
    "Title: Resident Evil\n"
    "Venue: Balboa Theatre — repertory/independent movie theater; film screenings.\n"
    "Desc:\n"
    '{"types":[["screening"]],"topics":["film"],"cost":"unknown"}\n\n'
    "Title: Excelsior Reads Book Club\n"
    "Venue: San Francisco Public Library — free library programs; talks, book clubs.\n"
    "Desc: Monthly discussion of this month's selection. All welcome.\n"
    '{"types":[["social","book-club"]],"topics":["books-authors"],"cost":"free"}\n'
)


def build_prompt(title: str, source: str, description: str | None) -> tuple[str, str]:
    """Return (system, user) prompt strings. Pure — no network."""
    profile = source_profile(source)
    venue = f"{source} — {profile}" if profile else source
    desc = (description or "").strip()[:_DESC_CAP]
    user = (
        taxonomy.render_for_prompt()
        + "\n\n" + _FEWSHOT
        + "\nClassify:\n"
        + f"Title: {title}\n"
        + f"Venue: {venue}\n"
        + f"Desc: {desc}\n\nJSON:"
    )
    return _SYSTEM, user


def parse_classification(text: str) -> dict:
    """Extract + validate the model's JSON into {types, topics, cost}. Always
    returns a dict; invalid/garbage yields empty types (caller decides what to
    do with an untagged show)."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {"types": [], "topics": [], "cost": "unknown"}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"types": [], "topics": [], "cost": "unknown"}

    types: list[list[str]] = []
    for path in obj.get("types") or []:
        valid = taxonomy.validate_type(path)
        if valid and valid not in types:  # coerce + dedupe
            types.append(valid)
    topics = taxonomy.validate_topics(obj.get("topics"))
    cost = obj.get("cost")
    if cost not in _VALID_COSTS:
        cost = "unknown"
    return {"types": types, "topics": topics, "cost": cost}


def _converse(client, model_id: str, system: str, user: str) -> str:
    resp = client.converse(
        modelId=model_id,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": 250, "temperature": 0.0},
    )
    return resp["output"]["message"]["content"][0]["text"]


def make_client():
    """Build a Bedrock runtime client with adaptive retries (throttling)."""
    import boto3
    from botocore.config import Config
    return boto3.client(
        "bedrock-runtime",
        config=Config(retries={"max_attempts": 6, "mode": "adaptive"}),
    )


# Unambiguous title keywords → topics the LLM must never miss. The model is
# inconsistent at emitting these even when the title says so literally (e.g. it
# tagged only ~30% of "Trivia Night at X" events with `trivia`), so we guarantee
# them deterministically. Substring match, case-insensitive; each mapped topic
# is validated against the taxonomy before use.
_KEYWORD_TOPICS = {
    "trivia": "trivia", "quiz": "trivia",
    "karaoke": "karaoke",
    "bingo": "bingo",
    "drag": "drag",
}


# Mutually-exclusive "pub game" topics the model confuses (it tags bingo nights
# as trivia, etc.). When the title names one of these, it is authoritative — the
# model's OTHER game-topic guesses are dropped. (drag is not in this set — a
# "Drag Bingo" is legitimately both.)
_GAME_TOPICS = {"trivia", "karaoke", "bingo"}


def keyword_topics(title: str) -> list[str]:
    """Topics guaranteed by unambiguous words in the title (validated)."""
    low = (title or "").lower()
    found: list[str] = []
    for kw, topic in _KEYWORD_TOPICS.items():
        if kw in low and topic not in found:
            found.append(topic)
    return taxonomy.validate_topics(found)


def _merge_topics(model_topics: list[str], title: str) -> list[str]:
    """Merge model topics with title-keyword topics. Keyword topics are always
    added; and if the title names a specific pub game, the model's other
    game-topic guesses (the confusable trivia/karaoke/bingo set) are removed."""
    kw = keyword_topics(title)
    kw_games = {t for t in kw if t in _GAME_TOPICS}
    topics = list(model_topics)
    if kw_games:
        topics = [t for t in topics if t not in _GAME_TOPICS or t in kw_games]
    for t in kw:
        if t not in topics:
            topics.append(t)
    return topics


def classify_show(
    title: str,
    source: str,
    description: str | None,
    *,
    client=None,
    models: tuple[str, ...] = (PRIMARY_MODEL, FALLBACK_MODEL),
) -> Classification:
    """Classify one show. Tries each model in order until one succeeds; the
    surviving model id is recorded on the Classification. Title-keyword topics
    (trivia/karaoke/bingo/drag) are always merged in, since the model misses
    them even when the title is explicit."""
    if client is None:
        client = make_client()
    system, user = build_prompt(title, source, description)

    last_err: Exception | None = None
    for model_id in models:
        try:
            text = _converse(client, model_id, system, user)
        except Exception as e:  # try the next model
            last_err = e
            continue
        parsed = parse_classification(text)
        topics = _merge_topics(parsed["topics"], title)
        return Classification(
            title=title,
            source=source,
            types=parsed["types"],
            topics=topics,
            cost=parsed["cost"],
            model=model_id,
            taxonomy_version=taxonomy.CURRENT_TAXONOMY_VERSION,
            classified_at=datetime.now(timezone.utc).isoformat(),
        )
    raise RuntimeError(f"all models failed for {source!r}/{title!r}: {last_err}")


def select_shows(rows) -> list[dict]:
    """Collapse (source, title, description) rows into distinct shows, keeping
    the richest (longest) description per (source, title). Pure."""
    best: dict[tuple[str, str], dict] = {}
    for source, title, description in rows:
        desc = description or ""
        key = (source, title)
        if key not in best or len(desc) > len(best[key]["description"]):
            best[key] = {"source": source, "title": title, "description": desc}
    return list(best.values())


def _is_fresh(entry: Classification) -> bool:
    """A cache entry is a hit if it was made under the current taxonomy."""
    return entry.taxonomy_version == taxonomy.CURRENT_TAXONOMY_VERSION


def classify_new_shows(
    shows: list[dict],
    cache,
    *,
    classifier=classify_show,
    client=None,
    log=print,
) -> tuple[int, int]:
    """Classify shows not already covered by a fresh cache entry. Returns
    (classified, cached). Saves the cache once at the end. Steady state (no new
    shows, same taxonomy) makes zero LLM calls."""
    classified = cached = 0
    total = len(shows)
    for i, show in enumerate(shows, 1):
        existing = cache.get(show["source"], show["title"])
        if existing is not None and _is_fresh(existing):
            cached += 1
            continue
        entry = classifier(show["title"], show["source"], show.get("description"),
                           client=client)
        cache.put(entry)
        classified += 1
        if log and classified % 25 == 0:
            log(f"[classify] {classified} classified ({i}/{total} seen)")
    cache.save()
    if log:
        log(f"[classify] done: {classified} classified, {cached} cached")
    return classified, cached
