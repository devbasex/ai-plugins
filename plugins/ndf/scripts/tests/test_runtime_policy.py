"""ランタイムの宣言（`.ndf/runtimes.json`。#1598）の読み取り・参加者の絞り込み・起動の前の確かめ・組の集計。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = ROOT / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import assignment  # noqa: E402
import run_metrics  # noqa: E402
import runtime_policy  # noqa: E402

EXTERNAL_AI = ROOT / "skills" / "external-ai" / "scripts" / "external-ai.py"
LAUNCH = LIB / "launch-cli.sh"


def _declare(root: pathlib.Path, body) -> pathlib.Path:
    d = root / ".ndf"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "runtimes.json"
    f.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return f


class _Probe:
    """認証確認の差し替え。呼ばれた名前を控え、`fail` の者だけを通さない。"""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.called: list[str] = []

    def __call__(self, names):
        self.called.extend(names)
        return {n: {"command": "x", "ok": n not in self.fail, "detail": "未認証" if n in self.fail else ""} for n in names}, False


# ---------- 宣言の読み取り（I1 / I2 / AC10） ----------


def test_load_returns_none_without_declaration(tmp_path):
    assert runtime_policy.load(tmp_path) is None


def test_load_reads_allowed_and_review_seats(tmp_path):
    f = _declare(tmp_path, {"allowed": ["claude", "codex"], "review_seats": ["claude", "claude-2"]})
    p = runtime_policy.load(tmp_path)
    assert p.allowed == ("claude", "codex")
    assert p.review_seats == ("claude", "claude-2")
    assert p.to_state() == {"path": str(f.resolve()), "allowed": ["claude", "codex"], "review_seats": ["claude", "claude-2"]}


@pytest.mark.parametrize(
    ("body", "what"),
    [
        ("{not json", "JSON として読めない"),
        ({"allowed": []}, "allowed が空"),
        ({"allowed": ["gemini"]}, "知らないランタイム: gemini"),
        ({"allowd": ["claude"]}, "知らないキー: allowd"),
        ({"allowed": ["claude", "claude"]}, "重なって"),
        ({"allowed": ["claude"], "review_seats": ["claude", "codex"]}, "allowed に無い: codex"),
        ({"allowed": ["claude"], "review_seats": ["claude", "claude"]}, "同じ席の名前"),
        ({"allowed": ["claude"], "review_seats": ["claude"]}, "2 つ"),
        ([], "オブジェクトでない"),
    ],
)
def test_broken_declaration_raises_with_the_broken_part(tmp_path, body, what):
    f = _declare(tmp_path, body)
    with pytest.raises(runtime_policy.RuntimePolicyError) as e:
        runtime_policy.load(tmp_path)
    assert what in str(e.value) and str(f.resolve()) in str(e.value)


def test_require_message_has_path_and_allowed(tmp_path):
    f = _declare(tmp_path, {"allowed": ["claude"]})
    p = runtime_policy.load(tmp_path)
    p.require(["claude", "claude-2", None], "x")
    with pytest.raises(runtime_policy.RuntimePolicyError) as e:
        p.require(["codex"], "--include")
    msg = str(e.value)
    assert "codex は宣言の外" in msg and str(f.resolve()) in msg and "allowed: claude" in msg


# ---------- 参加者の決め方（AC1〜AC7 の選び方） ----------


def test_without_policy_participants_are_unchanged():
    probe = _Probe()
    r = assignment.resolve_participants(assignment.default_pool("claude"), host="claude", probe=probe)
    assert r.available == ["claude", "codex", "kiro"] and r.policy is None
    assert r.to_state()["policy"] is None


def test_claude_only_probes_claude_only_and_seats_are_claude_and_claude2(tmp_path):
    _declare(tmp_path, {"allowed": ["claude"]})
    policy = runtime_policy.load(tmp_path)
    probe = _Probe()
    r = assignment.resolve_participants(assignment.default_pool("claude"), host="claude", probe=probe, policy=policy)
    assert probe.called == ["claude"]
    assert r.pool == ["claude"] and r.available == ["claude"]
    for round_no in (1, 2, 3):
        assert assignment.review_seats(round_no, r.available, []) == ["claude", "claude-2"]


@pytest.mark.parametrize("kwargs", [{"include": ["codex"]}, {"only": "codex"}])
def test_outside_include_or_only_stops_before_probe(tmp_path, kwargs):
    _declare(tmp_path, {"allowed": ["claude"]})
    policy = runtime_policy.load(tmp_path)
    probe = _Probe()
    with pytest.raises(runtime_policy.RuntimePolicyError, match="codex は宣言の外"):
        assignment.resolve_participants(assignment.default_pool("claude"), host="claude", probe=probe, policy=policy, **kwargs)
    assert probe.called == []


def test_host_outside_policy_is_not_in_pool(tmp_path):
    _declare(tmp_path, {"allowed": ["claude", "codex"]})
    r = assignment.resolve_participants(
        assignment.default_pool("kiro"), host="kiro", probe=_Probe(), policy=runtime_policy.load(tmp_path)
    )
    assert "kiro" not in r.pool and r.available == ["claude", "codex"]


def test_pinned_seats_are_used_every_round(tmp_path):
    _declare(tmp_path, {"allowed": ["claude", "codex", "kiro"], "review_seats": ["claude", "claude-2"]})
    policy = runtime_policy.load(tmp_path)
    r = assignment.resolve_participants(assignment.default_pool("claude"), host="claude", probe=_Probe(), policy=policy)
    for round_no in (1, 2, 3):
        assert assignment.review_seats(round_no, r.available, [], pinned=policy.review_seats) == ["claude", "claude-2"]


def test_pinned_seats_fall_back_when_runtime_unavailable():
    assert assignment.review_seats(1, ["codex", "kiro"], [], pinned=["claude", "claude-2"]) == ["codex", "kiro"]


def test_failed_auth_fills_with_same_runtime_not_outside(tmp_path):
    _declare(tmp_path, {"allowed": ["claude", "codex"]})
    policy = runtime_policy.load(tmp_path)
    probe = _Probe(fail={"codex"})
    r = assignment.resolve_participants(assignment.default_pool("claude"), host="claude", probe=probe, policy=policy)
    assert set(probe.called) <= {"claude", "codex"}
    assert assignment.review_seats(1, r.available, []) == ["claude", "claude-2"]


def test_no_participant_inside_policy_stops(tmp_path):
    _declare(tmp_path, {"allowed": ["agy"]})
    with pytest.raises(assignment.AssignmentError, match="宣言の中に参加者がいません"):
        assignment.resolve_participants(
            assignment.default_pool("claude"), host="claude", probe=_Probe(), policy=runtime_policy.load(tmp_path)
        )


# ---------- 起動の前の確かめ（I5 / AC8） ----------


def _stub_bin(tmp_path: pathlib.Path, name: str) -> tuple[dict, pathlib.Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    marker = tmp_path / f"{name}-started"
    stub = bin_dir / name
    stub.write_text(f'#!/bin/sh\ntouch "{marker}"\necho ok\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    env.pop("NDF_SKIP_AUTH_CHECK", None)
    return env, marker


@pytest.mark.parametrize("body", [{"allowed": ["claude"]}, "{broken"])
def test_launch_cli_does_not_start_outside_runtime(tmp_path, body):
    work = tmp_path / "work"
    work.mkdir()
    _declare(work, body)
    prompt = tmp_path / "p.md"
    prompt.write_text("prompt\n", encoding="utf-8")
    env, marker = _stub_bin(tmp_path, "codex")
    p = subprocess.run(
        [str(LAUNCH), "codex", str(work), str(prompt), str(tmp_path / "out" / "x"), "", "", "30"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p.returncode == 3, p.stderr
    assert not marker.exists()
    assert "runtimes.json" in p.stderr


def _git_repo(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.mark.parametrize("cmd", ["check", "run"])
def test_external_ai_stops_with_policy_before_which_and_auth(tmp_path, cmd):
    repo = _git_repo(tmp_path / "repo")
    _declare(repo, {"allowed": ["codex"]})
    env, marker = _stub_bin(tmp_path, "claude")
    prompt = tmp_path / "p.md"
    prompt.write_text("prompt\n", encoding="utf-8")
    args = [sys.executable, str(EXTERNAL_AI), cmd, "claude"]
    if cmd == "run":
        args += ["--prompt-file", str(prompt), "--output-file", str(tmp_path / "o.md"), "--workdir", str(repo)]
    p = subprocess.run(args, env=env, capture_output=True, text=True, cwd=repo, timeout=60)
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 3
    assert out["metrics"]["outcome"] == "policy"
    assert "claude は宣言の外" in out["metrics"]["reason"]
    assert not marker.exists()


# ---------- 組の集計（AC14 / AC15 / I7） ----------


def _summary(base: pathlib.Path, n: int, rounds: list[dict]) -> None:
    (base / "o--r").mkdir(parents=True, exist_ok=True)
    row = {"schema": 1, "kind": "cross-review", "started_at": "2026-10-01T00:00:00+00:00", "final": "approved", "rounds": rounds}
    (base / "o--r" / f"s{n}.json").write_text(json.dumps(row), encoding="utf-8")


def test_summary_copies_seats(tmp_path):
    seats = [
        {"seat": "claude", "runtime": "claude", "model": "opus", "partner": "claude-2"},
        {"seat": "claude-2", "runtime": "claude", "model": None, "partner": "claude"},
    ]
    st = {"rounds": [{"round": 1, "started_at": "2026-10-01T00:00:00+00:00", "seats": seats}, {"round": 2}]}
    rows = run_metrics.round_spans(st)
    assert rows[0]["seats"] == seats and "seats" not in rows[1]


def test_aggregate_by_pair_counts_pairs_and_unknown(tmp_path):
    def seat(rt, name=None):
        return {"seat": name or rt, "runtime": rt, "model": None, "partner": None}

    _summary(tmp_path, 1, [{"round": 1, "seats": [seat("codex"), seat("claude")]}, {"round": 2, "seats": [seat("claude"), seat("claude", "claude-2")]}])
    _summary(tmp_path, 2, [{"round": 1, "seats": [seat("claude"), seat("codex")]}, {"round": 2}])
    args = run_metrics.build_parser().parse_args(["aggregate", "--kind", "cross-review", "--by", "pair", "--dir", str(tmp_path)])
    text = run_metrics.aggregate_table(tmp_path, args)
    lines = [ln for ln in text.splitlines() if ln.startswith("|")]
    cells = {ln.split("|")[1].strip(): [c.strip() for c in ln.split("|")[2:4]] for ln in lines[2:]}
    assert cells == {"claude+codex": ["2", "2"], "claude+claude": ["1", "1"], "不明": ["1", "1"]}
    assert lines[2].split("|")[1].strip() == "claude+codex"


def test_pair_name_single_seat():
    assert run_metrics.pair_name({"seats": [{"seat": "codex", "runtime": "codex"}]}) == "codex"


# ---------- supervise（AC9） ----------


def _engine(tmp_path, steps):
    sys.path.insert(0, str(ROOT / "scripts"))
    from supervise_lib.engine import Engine

    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    plan = {"作業場所": str(work), "steps": steps}
    return Engine(plan, tmp_path / "state"), work


@pytest.mark.parametrize(
    "step",
    [
        {"id": "w", "type": "work", "kind": "実装", "prompt": "x", "runtime": "kiro"},
        {"id": "w", "type": "work", "kind": "実装", "prompt": "x"},
    ],
)
def test_supervise_stops_before_any_step_for_outside_worker(tmp_path, step):
    eng, work = _engine(tmp_path, [{"id": "first", "type": "run", "cmd": "true"}, step])
    _declare(work, {"allowed": ["codex"]})
    stopped = eng.check_runtimes()
    assert stopped is not None and stopped[0] == "止まった"
    assert "ステップ w" in stopped[1] and "宣言の外" in stopped[1]


def test_supervise_passes_inside_and_without_declaration(tmp_path):
    eng, work = _engine(tmp_path, [{"id": "w", "type": "work", "kind": "実装", "prompt": "x", "runtime": "codex"}])
    assert eng.check_runtimes() is None
    _declare(work, {"allowed": ["codex"]})
    assert eng.check_runtimes() is None


def test_supervise_stops_on_broken_declaration(tmp_path):
    eng, work = _engine(tmp_path, [{"id": "w", "type": "work", "kind": "実装", "prompt": "x"}])
    _declare(work, {"allowed": []})
    stopped = eng.check_runtimes()
    assert stopped is not None and "allowed が空" in stopped[1]

