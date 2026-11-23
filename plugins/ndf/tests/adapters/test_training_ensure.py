"""バックアップが無いか期限切れなら、起動の口がその場で学習の設定を確かめて記録すること（#3907）。

10 系から上げた利用者はバックアップが無い状態から始まる。以前は起動を断るだけで、利用者は原因が分からなかった。
"""

from __future__ import annotations

import json
import time

import pytest

from ndf.adapters import claude, training

NOW = time.time()
EXPIRED = "2000-01-01T00:00:00+00:00"


def _reads(answer, seen: list | None = None):
    """読み手の代わり。HTTP を打たず `answer`（True・False・None）を返す。"""

    def read(runtime, env, now):
        if seen is not None:
            seen.append(runtime)
        return training.Reading(runtime, answer, "テストの読み手")

    return read


def _backup(entry) -> None:
    training.cache_path().write_text(json.dumps({} if entry is None else {"codex": entry}), encoding="utf-8")


@pytest.mark.parametrize(
    ("entry", "stale"),
    [
        (None, True),
        ({"training": False, "reason": "x", "checked_at": EXPIRED}, True),
        ({"training": True, "reason": "x", "checked_at": training._stamp(NOW)}, False),
        ({"training": None, "reason": "HTTP 500", "checked_at": training._stamp(NOW)}, False),
        ({"training": None, "reason": "HTTP 500", "checked_at": None}, False),
    ],
)
def test_verdict_marks_only_a_missing_or_expired_backup_as_stale(entry, stale):
    _backup(entry)

    got = training.verdict("codex", NOW)

    assert got.why is not None and got.stale is stale


@pytest.mark.parametrize("entry", [None, {"training": False, "reason": "x", "checked_at": EXPIRED}])
def test_ensure_checks_on_the_spot_and_records_when_backup_is_missing_or_expired(entry):
    _backup(entry)
    seen: list[str] = []

    assert training.ensure("codex", now=NOW, read=_reads(False, seen)) is None

    assert seen == ["codex"]
    assert json.loads(training.cache_path().read_text(encoding="utf-8"))["codex"]["training"] is False
    assert training.refusal("codex", NOW) is None


@pytest.mark.parametrize(("answer", "said"), [(True, "学習に使う"), (None, "テストの読み手")])
def test_ensure_still_refuses_when_the_check_says_training_or_cannot_tell(answer, said):
    _backup(None)

    why = training.ensure("codex", now=NOW, read=_reads(answer))

    assert why is not None and "その場で確かめた" in why and said in why and training.CHECK_COMMAND in why


def test_ensure_does_not_recheck_a_fresh_refusal():
    _backup({"training": True, "reason": "x", "checked_at": training._stamp(NOW)})
    seen: list[str] = []

    why = training.ensure("codex", now=NOW, read=_reads(False, seen))

    assert seen == [] and "学習に使う" in why


def test_ensure_passes_runtimes_without_a_check():
    _backup(None)

    assert training.ensure("kiro", now=NOW, read=_reads(True)) is None


def test_call_claude_refuses_when_the_check_on_the_spot_cannot_tell(marker_launch, monkeypatch):
    training.cache_path().unlink()
    monkeypatch.setattr(training, "_read_now", _reads(None))
    marker, argv, _ = marker_launch("claude")

    got = claude.call_claude(argv, "q", 30)

    assert got.failure is claude.Failure.REFUSED and "その場で確かめた" in got.error and training.CHECK_COMMAND in got.error
    assert not marker.exists()
