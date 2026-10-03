#!/usr/bin/env python3
"""Build a scratch copy of the site from a manifest, for previews and timing.

Takes an existing events.json (default: the committed frontend/events.json),
re-joins the committed venue files the way the exporter does (coordinates,
neighbourhoods), splits descriptions into descriptions.json if the manifest
still carries them inline, and copies the page beside it. Writes OUTSIDE the
repo; never touches frontend/events.json (data ships from CI).

    service/.venv/bin/python scripts/preview_site.py /tmp/agora-preview
    service/.venv/bin/python scripts/preview_site.py /tmp/agora-old --keep-venues \\
        --page <(git show origin/main:frontend/index.html)
    python3 -m http.server -d /tmp/agora-preview 8000
    # then open http://localhost:8000/?beta=1 (the map is in private beta)

--keep-venues keeps the manifest's own `venues` block (no re-join): the
"before" side of a comparison.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service"))

from exporters.json_export import (  # noqa: E402
    DESCRIPTIONS_FILE, _load_venues, neighborhoods_block, split_descriptions, venues_block)
from places.store import DATA_DIR  # noqa: E402


def build(out: Path, manifest: Path, page: Path, keep_venues: bool = False) -> dict:
    data = json.loads(manifest.read_text())
    events = data["events"]
    descriptions = split_descriptions(events) if any("description" in e for e in events) else None
    if not keep_venues:
        data["neighborhoods"] = neighborhoods_block()
        data["venues"] = venues_block(_load_venues(DATA_DIR), events)
    out.mkdir(parents=True, exist_ok=True)
    (out / "events.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    if descriptions is not None:
        (out / DESCRIPTIONS_FILE).write_text(json.dumps(descriptions, indent=0, ensure_ascii=False))
    elif (manifest.parent / DESCRIPTIONS_FILE).exists():
        shutil.copy(manifest.parent / DESCRIPTIONS_FILE, out / DESCRIPTIONS_FILE)
    (out / "index.html").write_bytes(page.read_bytes())
    for name in ("canvas-client.js", "canvas.html"):
        shutil.copy(ROOT / "frontend" / name, out / name)
    shutil.rmtree(out / "vendor", ignore_errors=True)
    shutil.copytree(ROOT / "frontend" / "vendor", out / "vendor")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--manifest", type=Path, default=ROOT / "frontend" / "events.json")
    ap.add_argument("--page", type=Path, default=ROOT / "frontend" / "index.html")
    ap.add_argument("--keep-venues", action="store_true")
    args = ap.parse_args()
    if args.out.resolve().is_relative_to(ROOT):
        sys.exit("write the preview outside the repo")
    data = build(args.out, args.manifest, args.page, args.keep_venues)
    pins = sum(1 for v in data["venues"].values() if "lat" in v)
    print(f"{len(data['events'])} events, {len(data['venues'])} venues ({pins} with pins) -> {args.out}")


if __name__ == "__main__":
    main()
