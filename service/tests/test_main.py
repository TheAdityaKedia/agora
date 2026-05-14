from pathlib import Path
from unittest.mock import patch
import tempfile

from main import load_sources


def test_load_sources_returns_list(tmp_path):
    sources_file = tmp_path / "sources.txt"
    sources_file.write_text("https://greenapplebooks.com/events\n")
    with patch("main.SOURCES_FILE", sources_file):
        sources = load_sources()
    assert sources == ["https://greenapplebooks.com/events"]


def test_load_sources_ignores_blank_lines(tmp_path):
    sources_file = tmp_path / "sources.txt"
    sources_file.write_text("\nhttps://greenapplebooks.com/events\n\n")
    with patch("main.SOURCES_FILE", sources_file):
        sources = load_sources()
    assert len(sources) == 1


def test_load_sources_multiple(tmp_path):
    sources_file = tmp_path / "sources.txt"
    sources_file.write_text("https://a.com\nhttps://b.com\n")
    with patch("main.SOURCES_FILE", sources_file):
        sources = load_sources()
    assert len(sources) == 2
