"""使える者の解決（`assignment.resolve_participants` / `refactor_pool`）のテスト（#727）。

確認（`probe`）はスタブで、呼び出しの引数を記録する。環境変数の読み取りは
`auth.probe_auth` の責務なので、飛ばしは `probe` が `(…, True)` を返す形で確かめる。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ASSIGNMENT = Path(__file__).resolve().parents[1] / "lib" / "assignment.py"


@pytest.fixture(scope="module")
def assignment():
    spec = importlib.util.spec_from_file_location("ndf_lib_assignment_participants", ASSIGNMENT)
    mod = importlib.util.module_from_spec(spec)
    # `@dataclass` は `sys.modules[cls.__module__]` を見るため、登録してから実行する
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _probe(failing: dict[str, str] | None = None, *, skipped: bool = False):
    """確認のスタブ。`failing` の名前は理由つきで通らない。呼び出しを `calls` に記録する。"""
    failing = failing or {}
    calls: list[list[str]] = []

    def probe(names):
        calls.append(list(names))
        if skipped:
            return {}, True
        results = {
            n: {"command": f"{n} probe", "ok": n not in failing, "detail": failing.get(n, "")}
            for n in names
        }
        return results, False

    probe.calls = calls
    return probe


# ---------- 母集合の既定 ----------

@pytest.mark.parametrize("host,expected", [
    ("claude", ["claude", "codex", "kiro"]),
    ("codex", ["codex", "kiro"]),
    ("agy", ["codex", "agy", "kiro"]),
    ("kiro", ["codex", "kiro"]),
])
def test_refactor_pool_is_defaults_plus_host_in_fixed_order(assignment, host, expected):
    assert assignment.refactor_pool(host) == expected


def test_refactor_pool_rejects_a_non_host(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.refactor_pool("gemini")


def test_default_refactor_runtimes(assignment):
    assert assignment.DEFAULT_REFACTOR_RUNTIMES == ("codex", "kiro")


# ---------- AC1 / AC2: 通らない者を外す・全員を要する ----------

def test_one_failing_participant_is_moved_to_unavailable(assignment):
    """AC1: 母集合 3 者のうち 1 者が通らないと、残り 2 者が母集合の順で使える者になる。"""
    probe = _probe({"agy": "コマンドが見つかりません"})

    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude", probe=probe,
    )

    assert p.available == ["codex", "kiro"]
    assert p.unavailable == {"agy": "コマンドが見つかりません"}
    assert p.probe_skipped is False
    assert p.require_all is False
    assert p.pool == ["codex", "agy", "kiro"]
    assert p.included == [] and p.excluded == []


def test_require_all_fails_with_the_missing_name_and_reason(assignment):
    """AC2: `require_all` で欠けがあれば `AssignmentError`。名前と理由を含む。"""
    probe = _probe({"agy": "コマンドが見つかりません"})

    with pytest.raises(assignment.AssignmentError) as exc:
        assignment.resolve_participants(
            ["codex", "agy", "kiro"], host="claude", probe=probe, require_all=True,
        )

    assert "agy" in str(exc.value)
    assert "コマンドが見つかりません" in str(exc.value)
    assert "認証されていない CLI があります" in str(exc.value)


# ---------- AC3: 確認の相手は exclude を除き include を含む ----------

def test_probe_is_called_once_with_included_but_not_excluded(assignment):
    probe = _probe()

    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude",
        include=["claude"], exclude=["agy"], probe=probe,
    )

    assert probe.calls == [["claude", "codex", "kiro"]]
    assert p.available == ["claude", "codex", "kiro"]
    assert p.included == ["claude"]
    assert p.excluded == ["agy"]


def test_only_narrows_the_probe_to_that_one(assignment):
    probe = _probe()

    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude", only="kiro", probe=probe,
    )

    assert probe.calls == [["kiro"]]
    assert p.available == ["kiro"]
    assert p.pool == ["codex", "agy", "kiro"]


# ---------- AC4: 名前の矛盾 ----------

@pytest.mark.parametrize("kwargs", [
    dict(include=["agy"], exclude=["agy"]),
    dict(include=["gemini"]),
    dict(exclude=["gemini"]),
    dict(only="agy", exclude=["agy"]),
    dict(only="gemini"),
])
def test_conflicting_names_raise_before_probing(assignment, kwargs):
    probe = _probe()

    with pytest.raises(assignment.AssignmentError):
        assignment.resolve_participants(
            ["codex", "agy", "kiro"], host="claude", probe=probe, **kwargs,
        )

    assert probe.calls == []


def test_excluding_a_name_outside_the_pool_is_ignored(assignment):
    """#786 の決定 2: 母集合（既定 ∪ include）に無い者の除外は止めずに無視し、別に残す。"""
    probe = _probe()

    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude", exclude=["claude"], probe=probe,
    )

    assert p.excluded == []
    assert p.ignored_exclude == ["claude"]
    assert p.available == ["codex", "agy", "kiro"]
    assert probe.calls == [["codex", "agy", "kiro"]]


def test_ignored_and_real_exclusions_are_kept_apart(assignment):
    p = assignment.resolve_participants(
        ["claude", "codex", "kiro"], host="claude", exclude=["agy", "kiro"], probe=_probe(),
    )

    assert p.excluded == ["kiro"]
    assert p.ignored_exclude == ["agy"]
    assert p.available == ["claude", "codex"]


def test_only_names_a_runtime_outside_the_pool(assignment):
    """#542 #786 の決定 12: `--only` の名前は母集合に無くても参加者になる。記録は included に書かない。"""
    probe = _probe()

    p = assignment.resolve_participants(
        ["claude", "codex", "kiro"], host="claude", only="agy", probe=probe,
    )

    assert probe.calls == [["agy"]]
    assert p.available == ["agy"]
    assert p.included == []


def test_excluding_an_included_host_is_a_conflict_not_out_of_pool(assignment):
    """include でホストを足したうえで exclude すると、重なりとして弾く（母集合には入る）。"""
    with pytest.raises(assignment.AssignmentError):
        assignment.resolve_participants(
            ["codex", "agy", "kiro"], host="claude",
            include=["claude"], exclude=["claude"], probe=_probe(),
        )


# ---------- AC5: 飛ばし ----------

def test_skipped_probe_marks_everyone_available(assignment):
    probe = _probe(skipped=True)

    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude", probe=probe,
    )

    assert p.available == ["codex", "agy", "kiro"]
    assert p.unavailable == {}
    assert p.probe_skipped is True
    assert probe.calls == [["codex", "agy", "kiro"]]


def test_skipped_probe_satisfies_require_all(assignment):
    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude", probe=_probe(skipped=True), require_all=True,
    )
    assert p.available == ["codex", "agy", "kiro"]
    assert p.require_all is True


# ---------- 記録の形 ----------

def test_to_state_has_the_eight_keys_without_fallback(assignment):
    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude",
        include=["claude"], exclude=["agy"],
        probe=_probe({"kiro": "1 秒で応答しませんでした"}),
    )

    assert p.to_state() == {
        "pool": ["codex", "agy", "kiro"],
        "included": ["claude"],
        "excluded": ["agy"],
        "ignored_exclude": [],
        "available": ["claude", "codex"],
        "unavailable": {"kiro": "1 秒で応答しませんでした"},
        "probe_skipped": False,
        "require_all": False,
    }


def test_included_and_excluded_are_kept_in_fixed_order(assignment):
    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude",
        include=["claude"], exclude=["kiro", "agy"], probe=_probe(),
    )
    assert p.excluded == ["agy", "kiro"]
    assert p.available == ["claude", "codex"]


def test_excluding_all_pool_members_leaves_empty_available(assignment):
    """母集合の全員を exclude に指定した下限境界の振る舞い（R2-002）。"""
    probe = _probe()
    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"],
        host="claude",
        exclude=["codex", "agy", "kiro"],
        probe=probe,
    )

    assert probe.calls == [[]]
    assert p.available == []
    assert p.unavailable == {}
    assert p.excluded == ["codex", "agy", "kiro"]



def test_participant_missing_from_probe_results_is_unavailable_with_empty_reason(assignment):
    """確認の結果に名前が無い者は、理由が空の `unavailable` になる（R2-001 の現状固定）。

    明示的に `ok=False` を返す経路とは別の分岐である。結果がある成功者だけが残る。
    """
    def probe(names):
        return {"codex": {"command": "codex probe", "ok": True, "detail": ""}}, False

    p = assignment.resolve_participants(
        ["codex", "agy", "kiro"], host="claude", probe=probe,
    )

    assert p.available == ["codex"]
    assert p.unavailable == {"agy": "", "kiro": ""}
    assert p.probe_skipped is False
