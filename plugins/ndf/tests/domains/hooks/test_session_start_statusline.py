"""SessionStart が statusline を整える（`ndf statusline ensure` と同じ処理）。Claude Code だけで、排他を持たれていれば待たない。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ndf.domains.relay import signals
from ndf.domains.statusline import settings

from ._hooks import call_entry, sample


def _settings() -> Path:
    return settings.paths(os.environ).settings


def _body() -> Path:
    return settings.paths(os.environ).body


def test_claude_session_start_sets_the_ndf_statusline_and_places_the_body(tmp_path):
    assert call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path)) is None
    line = json.loads(_settings().read_text(encoding="utf-8"))["statusLine"]
    assert line["command"] == settings.NDF_COMMAND
    assert _body().read_bytes() == settings.BODY_SOURCE.read_bytes()


def test_claude_session_start_refreshes_a_stale_body(tmp_path):
    _settings().parent.mkdir(parents=True, exist_ok=True)
    _settings().write_text(json.dumps({"statusLine": {"type": "command", "command": settings.NDF_COMMAND}}), encoding="utf-8")
    _body().parent.mkdir(parents=True, exist_ok=True)
    _body().write_text(f"# {settings.MANAGED_MARK}\necho old\n", encoding="utf-8")
    call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path))
    assert _body().read_bytes() == settings.BODY_SOURCE.read_bytes()


def test_codex_session_start_leaves_claude_settings_alone(tmp_path):
    call_entry("codex", "SessionStart", sample("codex", "SessionStart", tmp_path))
    assert not _settings().exists() and not _body().exists()


def test_session_start_skips_without_waiting_when_the_lock_is_held(tmp_path):
    p = settings.paths(os.environ)
    p.lock.parent.mkdir(parents=True, exist_ok=True)
    with signals.held(p.lock, 0) as got:
        assert got
        assert call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path)) is None
    assert not _settings().exists()
