from pathlib import Path

DATA = Path(__file__).parent.parent / "data"


def _entries():
    lines = (DATA / "local_only_sources.txt").read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def test_each_local_only_entry_matches_exactly_one_source():
    """scripts/scrape-to-neon.sh --blocked passes these as --sources substrings;
    a stale entry would silently skip a source, a loose one would pull in extras
    (plain substring match: 'sfpl' also matches sfplayhouse)."""
    urls = [u.strip() for u in (DATA / "sources.txt").read_text().splitlines() if u.strip()]
    entries = _entries()
    assert entries
    for entry in entries:
        assert len([u for u in urls if entry in u]) == 1, entry
