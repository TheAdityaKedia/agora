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
    "TOPIC = what the event is ABOUT / the interest it serves. Return 0-3 topic "
    "slugs from the TOPIC list, most relevant first. Topics cut across formats "
    "(a poetry reading, open mic, and workshop all get \"poetry\"). Choose only "
    "listed slugs; omit rather than invent.\n\n"
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


def classify_show(
    title: str,
    source: str,
    description: str | None,
    *,
    client=None,
    models: tuple[str, ...] = (PRIMARY_MODEL, FALLBACK_MODEL),
) -> Classification:
    """Classify one show. Tries each model in order until one succeeds; the
    surviving model id is recorded on the Classification."""
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
        return Classification(
            title=title,
            source=source,
            types=parsed["types"],
            topics=parsed["topics"],
            cost=parsed["cost"],
            model=model_id,
            taxonomy_version=taxonomy.CURRENT_TAXONOMY_VERSION,
            classified_at=datetime.now(timezone.utc).isoformat(),
        )
    raise RuntimeError(f"all models failed for {source!r}/{title!r}: {last_err}")
