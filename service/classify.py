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
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import taxonomy
from classifications import Classification

# Haiku 5.5 through its global cross-region inference profile, then the
# US-only one (same model, routed within US regions). Call Haiku through an
# inference profile, not its bare foundation-model id.
PRIMARY_MODEL = "global.anthropic.claude-haiku-5-5"
FALLBACK_MODEL = "us.anthropic.claude-haiku-5-5"
# Haiku 4.5 is the fallback while the account has no Haiku 5.5 capacity
# (its 5.5 quotas are 0 until AWS raises them) and for any 5.5 outage.
HAIKU_45 = ("global.anthropic.claude-haiku-4-5-20251001-v1:0",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0")
MODELS = (PRIMARY_MODEL, FALLBACK_MODEL) + HAIKU_45

# Haiku 5.5 thinks by default and rejects `temperature`. Low effort keeps a
# one-line JSON answer quick; thinking counts toward maxTokens, so leave room.
MODEL_FIELDS = {"output_config": {"effort": "low"}}
MAX_TOKENS = 1024


def is_55(model_id: str) -> bool:
    """A 5.5 model: no `temperature`, and it may think before answering."""
    return "-5-5" in model_id


def inference_config(model_id: str, max_tokens: int) -> dict:
    """Converse `inferenceConfig` for this model: 5.5 rejects `temperature`;
    4.5 takes 0 for repeatable tags."""
    return {"maxTokens": max_tokens} if is_55(model_id) else {"maxTokens": max_tokens, "temperature": 0.0}


# Models Bedrock refused with AccessDeniedException in this process (no access,
# or a quota of 0). Not retried until the next run: otherwise every one of a
# run's calls would first spend a round trip on each refusing model.
_unavailable: set[str] = set()


def available(models) -> tuple[str, ...]:
    """`models` minus those refused earlier in this run; all of them if every
    one was refused (so the caller still reports the real errors)."""
    left = tuple(m for m in models if m not in _unavailable)
    return left or tuple(models)


def note_failure(model_id: str, error: Exception) -> None:
    if type(error).__name__ == "AccessDeniedException" and model_id not in _unavailable:
        _unavailable.add(model_id)
        print(f"[bedrock] {model_id} refused ({str(error)[:120]}); using the fallbacks for the rest of this run",
              flush=True)

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
    "listed slugs; omit rather than invent. Assign a topic only when a listed "
    "slug genuinely names the subject. If none does, return an empty topics "
    "list — NEVER force the nearest slug onto an unlisted subject (a chess club "
    "is not \"latino\", a pet meetup is not \"wellness\"). Guidance for the "
    "broad slugs: use \"music\" for a concert, DJ set, open mic, or jam whose "
    "genre is unclear or mixed (prefer a specific genre like \"jazz\"/\"blues\"/"
    "\"soul-funk\"/\"electronic\" when it's clear — blues/soul/R&B/funk are NOT "
    "\"jazz\"); \"games\" for board/"
    "tabletop/hobby game nights (not trivia/bingo, which have their own slugs); "
    "\"language\" for language-exchange / conversation-practice groups (the "
    "language practiced is the subject — use \"language\", not a community "
    "slug); \"family-kids\" for events aimed at children, teens, or families "
    "(storytime, teen programs, family days) IN ADDITION to the subject slug; "
    "\"education\" for instructional / learning events (test prep, tutoring, "
    "citizenship, personal finance, job skills) — an arts or craft class keeps "
    "its own subject instead; \"dance-party\" for a party built around a dance "
    "floor (pair with \"club-night\" when it's a DJ'd bar/club night); "
    "\"club-night\" — ALWAYS tag this when the event features a DJ, open decks, "
    "a dance floor / dance party, or a bar/club night built around recorded or "
    "electronic music, EVEN IF it also has another element (an open-mic-plus-DJ "
    "night gets [\"club-night\",\"spoken-word\"]); add the genre too when clear "
    "(e.g. [\"club-night\",\"electronic\"]). Do NOT use club-night for seated "
    "concerts, book/launch parties, gallery receptions, or comedy nights that "
    "merely happen at a bar.\n\n"
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
    '{"types":[["social","book-club"]],"topics":["books-authors"],"cost":"free"}\n\n'
    "Title: Tony Lindsay and Future Perfect Band\n"
    "Venue: Biscuits & Blues — Union Square SF blues & jazz supper club; live blues, soul, and R&B concerts.\n"
    "Desc: Experience world-class blues, dinner, and drinks — all in one place. $25.\n"
    '{"types":[["performance"]],"topics":["blues"],"cost":"paid"}\n\n'
    "Title: Girl Dance at The Stud\n"
    "Venue: SF Bar Guide — directory of recurring SF bar nights at a named venue.\n"
    "Desc: Femme forward dance & pop party at The Stud, FREE admission. First Friday of the month.\n"
    '{"types":[["social","party-club"]],"topics":["dance-party","club-night","lgbtq"],"cost":"free"}\n\n'
    "Title: Saturday Morning Story Time!\n"
    "Venue: Noe Valley Books — neighborhood bookstore; readings and community events.\n"
    "Desc: Bring your little ones for picture books and songs.\n"
    '{"types":[["social"]],"topics":["family-kids","books-authors"],"cost":"free"}\n'
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
        inferenceConfig=inference_config(model_id, MAX_TOKENS if is_55(model_id) else 250),
        **({"additionalModelRequestFields": MODEL_FIELDS} if is_55(model_id) else {}),
    )
    # The reply can open with a reasoning block; the answer is the text block.
    for block in resp["output"]["message"]["content"]:
        if "text" in block:
            return block["text"]
    raise RuntimeError(f"no text in reply (stopReason {resp.get('stopReason')})")


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
    models: tuple[str, ...] = MODELS,
) -> Classification:
    """Classify one show. Tries each model in order until one succeeds; the
    surviving model id is recorded on the Classification. Title-keyword topics
    (trivia/karaoke/bingo/drag) are always merged in, since the model misses
    them even when the title is explicit."""
    if client is None:
        client = make_client()
    system, user = build_prompt(title, source, description)

    errors: list[str] = []
    for model_id in available(models):
        try:
            text = _converse(client, model_id, system, user)
        except Exception as e:  # try the next model
            note_failure(model_id, e)
            errors.append(f"{model_id}: {type(e).__name__}: {e}")
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
    # Name every model's error — the primary's is usually the real cause.
    raise RuntimeError(f"all models failed for {source!r}/{title!r}: " + " | ".join(errors))


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


# Concurrent model calls, and how long one run may spend tagging. A taxonomy
# bump re-tags the whole catalog (~2,300 shows): one at a time that outran
# the merge job's 30-minute timeout and, since the cache saved only at the
# end, lost everything. Within the budget a run tags what it can and saves;
# the next run picks up the rest (stale tags keep showing until then).
WORKERS = 8
TIME_BUDGET_S = 15 * 60
# Stop early when the model is unreachable (no credentials, wrong region).
GIVE_UP_AFTER_FAILURES = 20
# Bedrock's "slow down" answers. botocore retries them quietly (adaptive
# mode), so without a count a throttled run just looks slow.
THROTTLE_CODES = {"ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"}


class ThrottleCounter:
    """Counts throttled Bedrock attempts (each one is retried by botocore).

    Hooks the client's `needs-retry` event, which botocore emits after every
    attempt; the handler only looks, it never changes the retry decision."""

    def __init__(self):
        self.count = 0
        self._lock = threading.Lock()

    def attach(self, client) -> "ThrottleCounter":
        events = getattr(getattr(client, "meta", None), "events", None)
        if events is not None:
            events.register("needs-retry.bedrock-runtime", self._on_attempt)
        return self

    def _on_attempt(self, response=None, **kwargs):
        if is_throttle(response):
            with self._lock:
                self.count += 1
        return None  # leave the retry decision to botocore


def is_throttle(response) -> bool:
    """`response` is botocore's (http_response, parsed) for an attempt."""
    if not response:
        return False
    http, parsed = response
    code = (parsed or {}).get("Error", {}).get("Code")
    return code in THROTTLE_CODES or getattr(http, "status_code", None) == 429


def classify_new_shows(
    shows: list[dict],
    cache,
    *,
    classifier=classify_show,
    client=None,
    log=print,
    workers: int = WORKERS,
    time_budget_s: float = TIME_BUDGET_S,
    clock=time.monotonic,
    stats: dict | None = None,
) -> tuple[int, int]:
    """Classify shows not already covered by a fresh cache entry. Returns
    (classified, cached). Calls run `workers` at a time; no new call starts
    after `time_budget_s`. A show whose models all fail is skipped (retried
    next run). Saves the cache once at the end, whatever happened. Steady
    state (no new shows, same taxonomy) makes zero LLM calls. `stats`, if
    given, is filled with the run's numbers (for the run report)."""
    todo, cached = [], 0
    for show in shows:
        existing = cache.get(show["source"], show["title"])
        if existing is not None and _is_fresh(existing):
            cached += 1
        else:
            todo.append(show)
    if todo and client is None and classifier is classify_show:
        client = make_client()  # one shared client (thread-safe), not one per call
    throttles = ThrottleCounter().attach(client)
    started = clock()

    deadline = clock() + time_budget_s
    classified = failed = 0
    queue = iter(todo)
    in_flight = set()

    def unreachable():
        return classified == 0 and failed >= GIVE_UP_AFTER_FAILURES

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        def fill():
            while len(in_flight) < 2 * max(1, workers) and clock() < deadline and not unreachable():
                show = next(queue, None)
                if show is None:
                    return
                in_flight.add(pool.submit(classifier, show["title"], show["source"],
                                          show.get("description"), client=client))
        fill()
        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for f in done:
                in_flight.discard(f)
                try:
                    cache.put(f.result())
                except Exception as e:  # skip it; the next run retries
                    failed += 1
                    if log and failed <= 3:
                        log(f"[classify] failed: {e}")
                    continue
                classified += 1
                if log and classified % 100 == 0:
                    log(f"[classify] {classified}/{len(todo)} classified, "
                        f"{throttles.count} throttled retries so far")
            fill()
    cache.save()
    left = len(todo) - classified - failed
    seconds = round(clock() - started)
    if stats is not None:
        stats.update(classified=classified, cached=cached, failed=failed, left=left,
                     throttled=throttles.count, seconds=seconds)
    if log:
        rate = f" ({classified / seconds:.1f}/s)" if seconds and classified else ""
        log(f"[classify] done in {seconds}s: {classified} classified{rate}, {cached} cached, "
            f"{failed} failed, {throttles.count} throttled retries"
            + (f", {left} left for the next run (time budget)" if left and not unreachable() else ""))
    if unreachable():
        raise RuntimeError(f"model unreachable: first {failed} calls failed")
    return classified, cached
