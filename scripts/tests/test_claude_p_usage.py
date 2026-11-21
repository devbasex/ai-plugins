"""計測のスクリプトの費用の物差し（#1316）を、模した帳簿・計画・会話・リポジトリで確かめる。

- `scripts/lib/release_map.py`: 載った版への寄せ方と版の PR 数（受け入れ条件 5・6）
- `scripts/measure/claude-p-usage.py`: B を帳簿から読む（受け入れ条件 1〜4）
- `scripts/token-usage.py --by release`: 同じ寄せ方で同じ版の PR 数を出す（受け入れ条件 5）
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))
import release_map  # noqa: E402
from test_token_usage import U_COST, _assistant, _jsonl, build  # noqa: E402

TU = HERE.parent / "token-usage.py"
CPU = HERE.parent / "measure" / "claude-p-usage.py"

TAGS = (  # 版・打った時刻。接尾辞付きのタグは寄せ先にしない
    ("10.17.1", "2026-09-09T12:00:00Z"),
    ("10.18.1-dev.1", "2026-09-10T00:10:00Z"),
    ("10.18.0-rc.1", "2026-09-10T00:20:00Z"),
    ("10.18.0", "2026-09-10T12:00:00Z"),
    ("10.18.1", "2026-09-11T12:00:00Z"),
)
PRS = [
    {
        "number": 7,
        "title": "feat: #1 a",
        "headRefName": "feat/issue-1",
        "mergedAt": "2026-09-10T01:00:00Z",
        "createdAt": "",
        "state": "MERGED",
    },
    {"number": 8, "title": "fix: #2 b", "headRefName": "fix/b", "mergedAt": "2026-09-09T11:00:00Z", "createdAt": "", "state": "MERGED"},
    {
        "number": 9,
        "title": "release",
        "headRefName": "release/ndf-10.18.0",
        "mergedAt": "2026-09-10T11:00:00Z",
        "createdAt": "",
        "state": "MERGED",
    },
    {"number": 20, "title": "feat: #3 c", "headRefName": "feat/c", "mergedAt": "2026-09-11T01:00:00Z", "createdAt": "", "state": "MERGED"},
    {"number": 21, "title": "feat: #4 d", "headRefName": "feat/d", "mergedAt": None, "createdAt": "", "state": "OPEN"},
]
CHANGELOG = "# Changelog\n\n## [ndf 10.18.1]\n\n- x (#20)\n\n## [ndf 10.18.0]\n\n- y (#8)\n\n## [ndf 10.17.1]\n\n- z\n"
A_SID = "aaaaaaaa-0000-0000-0000-000000000001"


def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    if date:
        env |= {"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "git@github.com:acme/secret-repo.git")
    (repo / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    _git(repo, "add", ".")
    for i, (v, at) in enumerate(TAGS):
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"c{i}", date=at)
        _git(repo, "tag", f"ndf--v{v}")
    return repo


def rmap(repo: Path) -> release_map.ReleaseMap:
    return release_map.ReleaseMap.from_repo(repo, PRS)


def ts(s: str) -> float:
    return release_map.ts(s)


# ---------- release_map ----------


def test_releases_include_other_series_and_skip_suffixed_tags(tmp_path):
    rm = rmap(make_repo(tmp_path))
    assert [r.version for r in rm.versions] == ["10.17.1", "10.18.0", "10.18.1"]
    assert rm.changelog == {20: "10.18.1", 8: "10.18.0"}
    # 開発版のタグ（00:10）より後でも、寄せ先は次の正式版
    assert rm.version_at(ts("2026-09-10T00:15:00Z")) == "10.18.0"


def test_place_order_is_changelog_then_merged_at_then_time(tmp_path):
    rm = rmap(make_repo(tmp_path))
    assert rm.place([8], None) == release_map.Placement(8, "10.18.0", "changelog")  # マージの時刻なら 10.17.1
    assert rm.place([7], None) == release_map.Placement(7, "10.18.0", "merged_at")
    pl = rm.place([21], ts("2026-09-11T02:00:00Z"))
    assert (pl.pr, pl.version, pl.by, pl.reason) == (21, "10.18.1", "time_only", "pr_unmerged")
    assert rm.place([], ts("2026-09-12T00:00:00Z")).version is None


def test_prs_of_counts_merged_non_release_prs_only(tmp_path):
    rm = rmap(make_repo(tmp_path))
    assert rm.prs_of("10.18.0") == {7, 8}
    assert rm.prs_of("10.18.1") == {20}
    assert rm.prs_of("10.17.1") == set()


# ---------- claude-p-usage.py ----------


def load_cpu():
    spec = importlib.util.spec_from_file_location("claude_p_usage", CPU)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def usage(w5: int, w1h: int) -> dict:
    return {
        "input_tokens": 10,
        "output_tokens": 100,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": w5 + w1h,
        "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1h},
    }


def lrow(at: str, plan: str, kind: str, model: str = "claude-opus-5", sid: str | None = None, w5: int = 0, w1h: int = 200) -> dict:
    return {
        "at": at,
        "ndf_version": "10.18.0-dev.3",
        "source": "supervise",
        "plan": plan,
        "step": "s",
        "kind": kind,
        "model": model,
        "usage": usage(w5, w1h),
        "model_usage": None,
        "cost_usd": 0.5,
        "turns": 4,
        "seconds": 30.0,
        "session_id": sid,
    }


REPORT = """## フェーズの報告

- フェーズ: 実装
- 課題: #1
- Pull Request: 無し
- 記録: `{record}`
- LLM の使用量: 入力 {inp} / cache read 100 / cache write 50 / 出力 20 / $0.10
"""


def build_cpu(tmp: Path) -> dict:
    repo = make_repo(tmp)
    sv = tmp / "sv"
    projects = tmp / "projects"
    usage_root = tmp / "usage"
    out = tmp / "out" / "cpu"
    out.parent.mkdir(parents=True)
    (out.parent / "prs.json").write_text(json.dumps(PRS), encoding="utf-8")
    # 計画: impl（JSON に フェーズ と 課題）・full だけの計画（会話が A にある）・full の会話が A に無い計画
    p1, p2, p3 = sv / "r-1" / "4-impl-1.json", sv / "r-1" / "5-check.json", sv / "r-2" / "1-design-3.json"
    for p, meta in ((p1, {"フェーズ": "実装", "課題": [1]}), (p2, {"フェーズ": "検査", "課題": [1]}), (p3, {"課題": [3]})):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (sv / "r-2" / "1-design-3-state").mkdir()
    (sv / "r-2" / "1-design-3-state" / "report.md").write_text(
        "- Pull Request: https://github.com/acme/secret-repo/pull/20\n", encoding="utf-8"
    )
    ledger = [
        lrow("2026-09-10T00:05:00Z", str(p1), "work", w5=30, w1h=0),
        lrow("2026-09-10T00:06:00Z", str(p1), "judge", model="claude-fable-5-1", w5=0, w1h=200),
        lrow("2026-09-10T00:07:00Z", str(p1), "work", w5=5, w1h=7),
        lrow("2026-09-10T00:08:00Z", str(p2), "full", sid=A_SID),  # A で数える
        lrow("2026-09-10T00:09:00Z", str(p3), "full", sid="not-in-a"),  # 会話が無いので B で数える
        lrow("2026-09-10T00:10:00Z", "", "mvv"),  # 計画の外
    ]
    _jsonl(usage_root / "acme__secret-repo.jsonl", ledger)
    # A: full のステップの会話
    wt = cpu_mod.project_dir_name(repo / ".worktrees") + "-feat-issue-1"
    conv = [dict(_assistant(8, "a1", usage(0, 200)), cwd=str(repo / ".worktrees" / "feat/issue-1"))]
    _jsonl(projects / wt / f"{A_SID}.jsonl", conv)
    # 帳簿の無い期間（00:05 より前）と、ある期間の報告
    for name, mtime, inp in (("0-impl-old", "2026-09-09T20:00:00Z", 111), ("9-impl-new", "2026-09-10T00:30:00Z", 222)):
        st = sv / "r-9" / f"{name}-state"
        st.mkdir(parents=True)
        (st / "report.md").write_text(REPORT.format(record=str(st), inp=inp), encoding="utf-8")
        os.utime(st / "report.md", (ts(mtime), ts(mtime)))
    return {"repo": repo, "sv": sv, "projects": projects, "usage": usage_root, "out": out, "ledger": ledger, "plans": (p1, p2, p3)}


cpu_mod = load_cpu()


def run_cpu(env: dict, *args: str) -> dict:
    argv = [
        "--repo",
        str(env["repo"]),
        "--usage-root",
        str(env["usage"]),
        "--sv-root",
        str(env["sv"]),
        "--projects",
        str(env["projects"]),
        "--out",
        str(env["out"]),
        *args,
    ]
    assert cpu_mod.main(argv) == 0
    return json.loads(Path(str(env["out"]) + ".json").read_text(encoding="utf-8"))


def test_b_comes_from_ledger_with_models_and_cache_split(tmp_path):
    env = build_cpu(tmp_path)
    r = run_cpu(env)
    B = {b["plan"]: b for b in r["B"]}
    # 3: 帳簿の plan ごとに 1 行（full だけの計画も）
    assert set(B) == {"r-1/4-impl-1", "r-1/5-check", "r-2/1-design-3"}
    assert all(b["source"] == "ledger" for b in B.values())
    # 1: モデルは帳簿の model（呼び出しの多いもの）
    assert B["r-1/4-impl-1"]["model"] == "claude-opus-5"
    assert B["r-2/1-design-3"]["model"] == "claude-opus-5"
    # 2: 5 分 / 1 時間は帳簿の和。A にある full は費用に入れず件数だけ
    impl = B["r-1/4-impl-1"]
    assert (impl["cache_write_5m"], impl["cache_write_1h"], impl["calls"], impl["turns"]) == (35, 207, 3, 12)
    check = B["r-1/5-check"]
    assert (check["calls"], check["full_calls"], check["cost_low"]) == (0, 1, 0)
    assert (B["r-2/1-design-3"]["calls"], B["r-2/1-design-3"]["full_calls"]) == (1, 0)
    # 計画の外は別の 1 まとまり
    assert r["B_outside"]["calls"] == 1 and r["B_outside"]["plan"] == "（計画の外）"
    # 種類は計画の JSON のフェーズ → 計画名
    assert (impl["kind"], check["kind"], B["r-2/1-design-3"]["kind"]) == ("impl", "check", "design")
    # PR: JSON → report.md → 課題のマージ済みの PR
    assert (impl["pr"], impl["version"], impl["by"]) == (7, "10.18.0", "merged_at")
    assert (B["r-2/1-design-3"]["pr"], B["r-2/1-design-3"]["version"], B["r-2/1-design-3"]["by"]) == (20, "10.18.1", "changelog")
    # 帳簿のある範囲では報告を読まない
    assert r["meta"]["ledger_since"] == "2026-09-10T00:05:00Z" and r["meta"]["report_until"] is None
    assert r["meta"]["since"] == "2026-09-10T00:05:00Z"


def test_a_and_b_do_not_count_one_call_twice(tmp_path):
    env = build_cpu(tmp_path)
    r = run_cpu(env)
    [a] = r["A"]
    [v] = [x for x in r["by_version"] if x["version"] == "10.18.0"]
    b_cost = sum(b["cost_low"] for b in r["B"] if b["version"] == "10.18.0")
    assert a["in_B"] is False
    assert v["cost_total_low"] == round(a["cost"] + b_cost + r["B_outside"]["cost_low"])
    assert "session" not in a  # 会話の ID を出力に写さない


def test_before_ledger_only_reports_are_read(tmp_path):
    env = build_cpu(tmp_path)
    r = run_cpu(env, "--since", "2026-09-09T13:00:00Z")
    reports = [b for b in r["B"] if b["source"] == "report"]
    assert [(b["plan"], b["input"], b["model"]) for b in reports] == [("r-9/0-impl-old", 111, "不明")]
    assert r["meta"]["report_until"] == "2026-09-10T00:05:00Z"
    md = Path(str(env["out"]) + ".md").read_text(encoding="utf-8")
    assert "2026-09-10T00:05:00Z" in md


def test_without_ledger_since_is_required(tmp_path):
    env = build_cpu(tmp_path)
    (env["usage"] / "acme__secret-repo.jsonl").unlink()
    with pytest.raises(SystemExit):
        run_cpu(env)
    r = run_cpu(env, "--since", "2026-09-09T13:00:00Z")
    assert {b["plan"] for b in r["B"]} == {"r-9/0-impl-old", "r-9/9-impl-new"}
    assert r["meta"]["ledger_since"] is None


def test_version_rows_count_release_prs_and_mark_partial(tmp_path):
    env = build_cpu(tmp_path)
    r = run_cpu(env)
    rows = {x["version"]: x for x in r["by_version"]}
    assert set(rows) == {"10.18.0", "10.18.1"}
    assert (rows["10.18.0"]["release_prs"], rows["10.18.0"]["partial"]) == (2, True)
    assert (rows["10.18.1"]["release_prs"], rows["10.18.1"]["partial"]) == (1, False)
    assert rows["10.18.0"]["prs_seen"] == 1


# ---------- token-usage.py の軸 release ----------


def run_tu(roots: dict, *args: str) -> dict:
    p = subprocess.run(
        [
            sys.executable,
            str(TU),
            "--claude-root",
            str(roots["claude"]),
            "--codex-root",
            str(roots["codex"]),
            "--kiro-root",
            str(roots["kiro"]),
            "--usage-root",
            str(roots["claude"].parent / "no-usage"),
            "--format",
            "json",
            *args,
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_token_usage_release_axis_matches_claude_p_usage(tmp_path):
    env = build_cpu(tmp_path / "c")
    roots = build(tmp_path / "t")  # 会話は acme/secret-repo の PR 7 を作る
    prs = env["out"].parent / "prs.json"
    r = run_tu(roots, "--by", "release,version", "--repo", str(env["repo"]), "--prs-json", str(prs))
    [row] = r["per_pr"]
    assert (row["release"], row["version"], row["release_by"]) == ("10.18.0", "10.16.0", {"merged_at": 1})
    assert row["conductor_cost"] == 4 * U_COST
    c = run_cpu(env)
    [v] = [x for x in c["by_version"] if x["version"] == "10.18.0"]
    assert row["release_prs"] == v["release_prs"] == len(rmap(env["repo"]).prs_of("10.18.0"))


def test_token_usage_default_axes_do_not_read_pr_list(tmp_path):
    roots = build(tmp_path)
    missing = tmp_path / "nowhere" / "prs.json"
    # 解決できない --repo は終了コード 2 なので、ディレクトリの要らない slug で対象を決めても PR の一覧を読まないことを見る
    r = run_tu(roots, "--repo", "acme/not-a-repo", "--prs-json", str(missing))
    assert "release_prs" not in r["per_pr"][0]
    assert not missing.exists()


# ---------- read_reports の現状固定（I-002） ----------


def _report(record: str, inp: int, read: int = 100, out: int = 20, write: int = 50) -> str:
    return (
        "## フェーズの報告\n\n- 課題: #1\n"
        f"- 記録: `{record}`\n"
        f"- LLM の使用量: 入力 {inp} / cache read {read} / cache write {write} / 出力 {out} / $0.10\n"
    )


def _tool_result_line(at: str, text: str, typ: str = "user") -> dict:
    return {"type": typ, "timestamp": at, "message": {"content": [{"type": "tool_result", "content": text}]}}


def test_read_reports_characterization(tmp_path):
    """read_reports の今の振る舞いを固定する（report.md と会話の tool_result の両方、重複と期間の扱い）。"""
    sv, projects = tmp_path / "sv", tmp_path / "projects"
    cpu_mod.configure(tmp_path / "repo", sv, projects, "seat-")
    try:
        # 1) report.md: 新しい置き場・古い plans/ の置き場・期間の外・LLM を使わない報告
        def put(rel: str, text: str, at: str) -> None:
            p = sv / rel / "report.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            os.utime(p, (ts(at), ts(at)))

        a = sv / "r-1" / "2-impl-5-state"
        put("r-1/2-impl-5-state", _report(str(a), 10), "2026-09-10T01:00:00Z")
        b = sv / "r-2" / "plans" / "3-check-state"
        put("r-2/plans/3-check-state", _report(str(b), 7), "2026-09-10T02:00:00Z")
        old = sv / "r-3" / "1-design-state"
        put("r-3/1-design-state", _report(str(old), 9), "2026-09-01T00:00:00Z")  # since より前
        zero = sv / "r-4" / "1-release-state"
        put("r-4/1-release-state", _report(str(zero), 0, read=0, out=0, write=0), "2026-09-10T03:00:00Z")
        # 2) 会話: 同じ記録で数値が伸びた報告（最後だけ残る）・記録が計画名に合わない報告・type が user でない行・期間の外
        c = sv / "r-5" / "7-review-state"
        lines = [
            _tool_result_line("2026-09-10T04:00:00Z", _report(str(c), 1, read=10, out=1)),
            _tool_result_line("2026-09-10T05:00:00Z", _report(str(c), 1, read=500, out=5)),
            _tool_result_line("2026-09-10T06:00:00Z", _report("/elsewhere/x", 3)),
            _tool_result_line("2026-09-10T06:30:00Z", _report(str(sv / "r-6" / "1-impl-state"), 4), typ="assistant"),
            _tool_result_line("2026-09-12T00:00:00Z", _report(str(sv / "r-7" / "1-impl-state"), 6)),
            # report.md と同じ報告が会話にもある。早い方の時刻が残る
            _tool_result_line("2026-09-10T00:30:00Z", _report(str(a), 10)),
        ]
        f = projects / "p" / "s.jsonl"
        f.parent.mkdir(parents=True)
        f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\nnot json LLM の使用量\n", encoding="utf-8")

        got = cpu_mod.read_reports(ts("2026-09-05T00:00:00Z"), ts("2026-09-11T00:00:00Z"))
    finally:
        cpu_mod.configure(Path.home(), Path.home() / ".local/state/ndf/sv", Path.home() / ".claude/projects", "")

    assert [(r["plan"], r["kind"], r["input"], r["cache_read"], r["output"], cpu_mod.iso(r["at"])) for r in got] == [
        ("r-1/2-impl-5", "impl", 10, 100, 20, "2026-09-10T00:30:00Z"),
        ("r-2/3-check", "check", 7, 100, 20, "2026-09-10T02:00:00Z"),
        ("r-5/7-review", "review", 1, 500, 5, "2026-09-10T05:00:00Z"),
        ("不明", "不明", 3, 100, 20, "2026-09-10T06:00:00Z"),
    ]
    r = got[0]
    assert (r["source"], r["model"], r["calls"], r["cache_write_5m"], r["cache_write_1h"], r["full_calls"], r["run_version"]) == (
        "report",
        "不明",
        None,
        None,
        None,
        None,
        "-",
    )
    low = round(10 + 100 * cpu_mod.tu.read_rate("claude-opus-5-5") + 50 * cpu_mod.tu.WEIGHTS["w5"] + 20 * cpu_mod.tu.WEIGHTS["out"], 1)
    assert r["cost_low"] == low
    assert r["cost_high"] == round(low + 50 * (cpu_mod.tu.WEIGHTS["w1h"] - cpu_mod.tu.WEIGHTS["w5"]), 1)
    assert (r["issues"], r["pr_field"], r["usd"]) == ([1], [], 0.1)


# ---------- 副命令 launches・plan-cost（#3412 の U1・U2） ----------
# 時刻の形は 2026-11-21 に確かめた実データを写す（progress.jsonl と使用量ログの at は UTC・秒まで・+00:00）

SINCE = "2026-11-18T00:00:00+00:00"
REPO = "devbasex/ai-plugins"
UNTIL = "2026-11-19T00:00:00+00:00"


def _dt(s: str):
    return cpu_mod.parse_aware(s)


def _step(at: str, step: str, typ: str = "work", **extra) -> dict:
    return {"kind": "step", "at": at, "step": step, "type": typ, "exit": 0, "cost": 3.23, "models": {}, **extra}


def _lid(run_id: str, step: str, attempt: int) -> str:
    """見本の起動 ID（書き手は起動ごとに一意な値を書く。見本では実行・ステップ・試行から作る）。"""
    return f"{run_id}/{step}/{attempt}"


def _cli(at: str, usage_value="known", **meta) -> dict:
    u = {"input_tokens": 10, "output_tokens": 2, "cache_creation_input_tokens": 4, "cache_read_input_tokens": 100}
    if "launch_id" not in meta and all(k in meta for k in ("run_id", "step", "attempt")):
        meta["launch_id"] = _lid(meta["run_id"], meta["step"], meta["attempt"])
    return {"at": at, "caller": "claude-p", **meta, "usage": u if usage_value == "known" else usage_value}


def _launch(at: str, step: str, attempt: int, run_id: str = "r-1", runtime: str = "claude", launch_id: str | None = None) -> dict:
    lid = launch_id or _lid(run_id, step, attempt)
    return {"kind": "launch", "at": at, "launch_id": lid, "run_id": run_id, "step": step, "attempt": attempt, "runtime": runtime}


def _own(state: Path, slug: str | None = REPO) -> None:
    """状態ディレクトリの親をリポジトリにし、origin を `slug` にする（None なら origin を持たない）。"""
    repo = state.parent
    if not (repo / ".git").exists():
        _git(repo, "init", "-q")
        if slug:
            _git(repo, "remote", "add", "origin", f"https://github.com/{slug}.git")


def _progress(state: Path, rows: list[dict], slug: str | None = REPO) -> Path:
    f = state / "sprint-x" / "1-impl-state" / "progress.jsonl"
    _jsonl(f, rows)
    _own(state, slug)
    return f.parent.resolve()


def _launch_env(tmp_path: Path, usage_rows: list[dict] | None):
    state = tmp_path / ".ndf"
    _progress(
        state,
        [
            {"kind": "run", "at": "2026-11-18T03:41:38+00:00", "from": None, "launches": 1},
            _launch("2026-11-18T03:49:00+00:00", "impl", 1),
            _launch("2026-11-18T03:49:30+00:00", "impl", 2),  # full の再開: 同じステップの中の 2 回目の起動
            _step("2026-11-18T03:50:00+00:00", "impl"),
            _step("2026-11-18T03:55:00+00:00", "pr", "pr"),
            _launch("2026-11-18T03:56:00+00:00", "pr", 1),  # PR の判断からの起動（step の型を問わない）
            _launch("2026-11-18T04:05:00+00:00", "review", 1, runtime="codex"),  # 他のランタイムは数えない
            _step("2026-11-18T04:06:00+00:00", "review"),
        ],
    )
    log = tmp_path / "usage" / "usage.jsonl"
    if usage_rows is not None:
        _jsonl(log, usage_rows)
    return state, log


def test_count_launches_matches_keys_two_of_three(tmp_path):
    rows = [
        _cli("2026-11-18T03:50:30+00:00", run_id="r-1", step="impl", attempt=1),
        _cli("2026-11-18T04:10:30+00:00", run_id="r-1", step="pr", attempt=1),
        _cli("2026-11-18T04:11:00+00:00", run_id="r-1", step="impl", attempt=9),  # 母数に無い鍵
        {"at": "2026-11-18T04:00:30+00:00", "caller": "subagent", "agent_id": "a", "usage": {}},  # 別の書き手
    ]
    state, log = _launch_env(tmp_path, rows)
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["launched"], got["recorded"], got["reason"]) == (3, 2, None)
    assert got["value"] == pytest.approx(2 / 3)


def test_count_launches_old_runner_steps_are_unmeasurable(tmp_path):
    """kind: launch を書かない実行の work・judge は、起動したかも試行の数も分からないので母数にせず計測不能にする。"""
    state = tmp_path / ".ndf"
    _progress(
        state,
        [
            {"kind": "run", "at": "2026-11-18T01:00:00+00:00", "from": None},
            _step("2026-11-18T01:00:01+00:00", "impl", run_id="r-1", attempt=3),
            _step("2026-11-18T01:10:00+00:00", "judge", "judge"),
            {"kind": "run", "at": "2026-11-18T02:00:00+00:00", "from": None, "launches": 1},
            _launch("2026-11-18T02:00:01+00:00", "impl", 1, run_id="r-2"),
            _step("2026-11-18T02:00:02+00:00", "impl"),  # 起動の行を持つ実行のステップの行は区別できる
        ],
    )
    log = tmp_path / "usage.jsonl"
    _jsonl(log, [_cli("2026-11-18T01:00:10+00:00", run_id="r-1", step="impl", attempt=3)])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["launched"], got["unidentified_steps"]) == (None, 1, 2) and got["reason"]


def test_count_launches_period_across_writer_introduction_is_unmeasurable(tmp_path):
    """11.0.13 の runner は kind: run も kind: launch も書かず event: route_chosen だけを書く。書き手が入った後の実行の
    起動がすべて照合できても、入る前の実行の期間の内の行があれば、その起動が母数から落ちるので計測不能にする。"""
    state = tmp_path / ".ndf"
    chosen = {"event": "route_chosen", "run_id": "run-old", "rule": "R03", "chooser": "script", "from": "a", "to": "b"}
    _progress(
        state,
        [
            {**chosen, "at": "2026-11-17T23:00:00Z"},  # 期間の前の選択の記録は数えない
            {**chosen, "at": "2026-11-18T01:00:00Z"},
            {"kind": "run", "at": "2026-11-18T02:00:00+00:00", "run_id": "run-new", "launches": 1},
            _launch("2026-11-18T02:00:01+00:00", "impl", 1, run_id="run-new"),
            {**chosen, "run_id": "run-new", "at": "2026-11-18T02:10:00Z"},  # 対応した実行の選択の記録は数えない
        ],
    )
    log = tmp_path / "usage.jsonl"
    rows = [_cli("2026-11-18T02:05:00+00:00", run_id="run-new", step="impl", attempt=1, repo=REPO, session_id="s", model="m")]
    _jsonl(log, rows)
    got = cpu_mod.measure_both([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)
    assert (got["u2"]["value"], got["u2"]["launched"], got["u2"]["recorded"], got["u2"]["unidentified_steps"]) == (None, 1, 1, 1)
    assert "route_chosen" in got["u2"]["reason"]
    assert got["plan_cost"]["complete"] is False and "route_chosen" in got["plan_cost"]["reason"]
    # 書き手が入った後だけの期間なら測れる
    got = cpu_mod.count_launches([state], log, _dt("2026-11-18T02:00:00+00:00"), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["unidentified_steps"]) == (1.0, 0)


def test_count_launches_same_run_in_two_clones_counts_both(tmp_path):
    """同じ HEAD・同じプランを 2 つの clone で初回に動かすと run_id・step・attempt が同じになる。起動ごとに一意な
    launch_id で照合するので、2 つの起動を 1 回に潰さず、使用量の行も両方を採る。"""
    roots = []
    for name in ("one", "two"):
        state = tmp_path / name / ".ndf"
        _progress(
            state,
            [
                {"kind": "run", "at": "2026-11-18T01:00:00+00:00", "run_id": "r-1", "launches": 1},
                _launch("2026-11-18T01:00:01+00:00", "impl", 1, launch_id=f"L-{name}"),
            ],
        )
        roots.append(state)
    log = tmp_path / "usage.jsonl"
    rows = [
        _cli(
            "2026-11-18T01:05:00+00:00", launch_id=f"L-{n}", run_id="r-1", step="impl", attempt=1, repo=REPO, session_id=f"s-{n}", model="m"
        )
        for n in ("one", "two")
    ]
    _jsonl(log, rows)
    got = cpu_mod.measure_both(roots, log, {}, _dt(SINCE), _dt(UNTIL), REPO)
    assert (got["u2"]["launched"], got["u2"]["recorded"], got["plan_cost"]["rows"]) == (2, 2, 2)


def test_collect_launches_reads_a_file_once_under_nested_roots(tmp_path):
    """リポジトリの根と、その下の <プラン>.state を両方渡しても、同じ progress.jsonl を 2 度数えない。"""
    state = tmp_path / ".ndf"
    f = _progress(state, [{"kind": "run", "at": "2026-11-18T01:00:00+00:00", "from": None}, _step("2026-11-18T01:00:01+00:00", "impl")])
    got = cpu_mod.collect_launches([state, f], _dt(SINCE), _dt(UNTIL), REPO)
    assert got["unidentified"] == 1


def test_count_launches_without_launches_is_null(tmp_path):
    state = tmp_path / ".ndf"
    _progress(
        state,
        [{"kind": "run", "at": "2026-11-18T01:00:00+00:00", "from": None, "launches": 1}, _step("2026-11-18T01:00:01+00:00", "pr", "pr")],
    )
    log = tmp_path / "usage.jsonl"
    _jsonl(log, [_cli("2026-11-18T01:00:10+00:00", run_id="x", step="impl", attempt=1)])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert got["value"] is None and got["launched"] == 0 and got["reason"]


def test_count_launches_rows_without_keys_is_null_with_reason(tmp_path):
    state, log = _launch_env(tmp_path, [_cli("2026-11-18T03:50:30+00:00"), _cli("2026-11-18T04:10:30+00:00", step="impl")])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert got["value"] is None and got["launched"] == 3 and got["reason"]
    missing = cpu_mod.count_launches([state], tmp_path / "none.jsonl", _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert missing["value"] is None and missing["reason"]


def test_count_launches_launch_rows_without_keys_are_not_dropped(tmp_path):
    """有効な起動の行に、鍵の欠けた claude の起動の行が混ざっても、欠けた行を母数から黙って落とさない。"""
    state = tmp_path / ".ndf"
    _progress(
        state,
        [
            {"kind": "run", "at": "2026-11-18T01:00:00+00:00", "from": None, "launches": 1},
            _launch("2026-11-18T01:00:01+00:00", "impl", 1),
            {"kind": "launch", "at": "2026-11-18T01:10:00+00:00", "run_id": "r-1", "step": "judge", "runtime": "claude"},
            {"kind": "launch", "at": "2026-11-18T01:20:00+00:00", "run_id": "r-1", "step": "pr", "attempt": 1},  # runtime が無い
            {"kind": "launch", "at": "2026-11-17T01:20:00+00:00", "run_id": "r-1", "step": "x"},  # 期間の外は数えない
            {"kind": "launch", "at": "2026-11-18T01:30:00+00:00", "step": "review", "runtime": "codex"},  # 他のランタイムは数えない
        ],
    )
    log = tmp_path / "usage.jsonl"
    _jsonl(log, [_cli("2026-11-18T01:05:00+00:00", run_id="r-1", step="impl", attempt=1, repo=REPO)])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["launched"], got["unidentified_launches"]) == (None, 1, 2) and got["reason"]


def test_count_launches_mixed_keyless_usage_rows_are_null(tmp_path):
    """鍵付きの行に鍵の無い行が混ざれば、鍵の無い行がどの起動の分か決められないので計測不能にする。
    別のリポジトリの行と、期間の始まりより前に書かれた行は判定に入れない。"""
    keyed = [
        _cli("2026-11-18T03:50:30+00:00", run_id="r-1", step="impl", attempt=1),
        _cli("2026-11-18T03:51:00+00:00", run_id="r-1", step="impl", attempt=2),
        _cli("2026-11-18T04:10:30+00:00", run_id="r-1", step="pr", attempt=1),
    ]
    state, log = _launch_env(tmp_path, [*keyed, _cli("2026-11-18T04:20:00+00:00", repo=REPO)])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["recorded"], got["keyless"]) == (None, 3, 1) and "鍵" in got["reason"]
    _jsonl(log, [*keyed, _cli("2026-11-18T04:20:00+00:00", repo="other/repo"), _cli("2026-11-17T23:00:00+00:00", repo=REPO)])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["keyless"], got["other_repo"], got["reason"]) == (1.0, 0, 1, None)


def test_count_launches_matches_usage_recorded_after_until(tmp_path):
    """母数は起動の時刻で決め、使用量の行は鍵で照合する。終わりの直前に起動し、終わりの後に記録された起動も記録済み。"""
    state = tmp_path / ".ndf"
    _progress(
        state,
        [{"kind": "run", "at": "2026-11-18T23:00:00+00:00", "from": None, "launches": 1}, _launch("2026-11-18T23:59:00+00:00", "impl", 1)],
    )
    log = tmp_path / "usage.jsonl"
    _jsonl(log, [_cli("2026-11-19T00:03:00+00:00", run_id="r-1", step="impl", attempt=1, repo=REPO)])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["launched"], got["recorded"]) == (1.0, 1, 1)


def test_count_launches_half_open_until(tmp_path):
    state = tmp_path / ".ndf"
    _progress(
        state,
        [
            {"kind": "run", "at": "2026-11-18T23:00:00+00:00", "from": None, "launches": 1},
            _launch("2026-11-18T23:59:59+00:00", "impl", 1),  # 数える
            _launch("2026-11-19T00:00:00+00:00", "judge", 1),  # until ちょうどは数えない
        ],
    )
    log = tmp_path / "usage.jsonl"
    _jsonl(log, [])
    # +09:00 で渡しても同じ瞬間として比べる
    got = cpu_mod.count_launches([state], log, _dt("2026-11-18T09:00:00+09:00"), _dt("2026-11-19T09:00:00+09:00"), REPO)["u2"]
    assert got["launched"] == 1


def _keyed_rows():
    return [
        _cli("2026-11-18T03:50:30+00:00", run_id="r-1", step="impl", attempt=1, repo=REPO),
        _cli("2026-11-18T03:51:00+00:00", run_id="r-1", step="impl", attempt=2, repo=REPO),
        _cli("2026-11-18T04:10:30+00:00", run_id="r-1", step="pr", attempt=1, repo=REPO),
    ]


def test_count_launches_state_of_other_repo_is_unmeasurable(tmp_path, monkeypatch, capsys):
    """母数の状態ディレクトリは対象リポジトリに属すると確かめる。別のリポジトリのものと、属するリポジトリを決められない
    ものは読まずに計測不能にする（別のリポジトリの起動を分母にしない）。既定（カレントのリポジトリの .ndf）も同じ。"""
    state, log = _launch_env(tmp_path / "mine", _keyed_rows())
    assert cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]["value"] == 1.0
    other = tmp_path / "theirs" / ".ndf"
    _progress(
        other,
        [
            {"kind": "run", "at": "2026-11-18T01:00:00+00:00", "from": None, "launches": 1},
            _launch("2026-11-18T01:00:01+00:00", "x", 1, run_id="r-9"),
        ],
        slug="acme/other",
    )
    got = cpu_mod.count_launches([state, other], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["launched"]) == (None, 3) and "acme/other" in got["reason"]
    loose = tmp_path / "loose" / ".ndf"
    _progress(loose, [{"kind": "run", "at": "2026-11-18T01:00:00+00:00", "from": None, "launches": 1}], slug=None)
    got = cpu_mod.count_launches([loose], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert got["value"] is None and got["foreign_state"]
    monkeypatch.chdir(other.parent)
    assert cpu_mod.main(["launches", "--since", SINCE, "--until", UNTIL, "--usage-log", str(log), "--repo", REPO]) == 0
    out = json.loads(capsys.readouterr().out)["u2"]
    assert out["value"] is None and "acme/other" in out["reason"]


def test_count_launches_keyless_rows_filtered_by_launched_at(tmp_path):
    """鍵の無い行は、launched_at が読めれば起動の時刻で期間に絞る。期間の後の起動と分かる行は判定に入れない。"""
    state, log = _launch_env(
        tmp_path, [*_keyed_rows(), _cli("2026-11-19T02:00:00+00:00", repo=REPO, launched_at="2026-11-19T01:59:00+00:00")]
    )
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["keyless"]) == (1.0, 0)
    _jsonl(log, [*_keyed_rows(), _cli("2026-11-19T00:02:00+00:00", repo=REPO, launched_at="2026-11-18T23:59:00+00:00")])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["keyless"]) == (None, 1)


def test_count_launches_usage_of_unread_launch_is_unmeasurable(tmp_path):
    """期間の内に起動した対象リポジトリの行なのに母数に無ければ、読んだ状態ディレクトリが足りないので計測不能。"""
    extra = _cli("2026-11-18T05:00:00+00:00", run_id="r-7", step="impl", attempt=1, repo=REPO, launched_at="2026-11-18T04:59:00+00:00")
    state, log = _launch_env(tmp_path, [*_keyed_rows(), extra])
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["orphan"]) == (None, 1) and "--state" in got["reason"]


def test_count_launches_unmatched_row_uses_launched_at_only(tmp_path):
    """照合できない行は launched_at を読めればその時刻だけで期間を決める。期間の前に起動して期間の内に記録された行だけでは、
    書き手の無い期間を測れたことにしない。"""
    state, log = _launch_env(
        tmp_path, [_cli("2026-11-18T00:10:00+00:00", run_id="r-0", step="x", attempt=1, launched_at="2026-11-17T23:50:00+00:00")]
    )
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert got["value"] is None and "書き手" in got["reason"]


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://github.com/devbasex/ai-plugins.git", REPO),
        ("https://github.com/devbasex/ai-plugins", REPO),
        ("https://x-access-token:t@github.com/devbasex/ai-plugins.git", REPO),
        ("git@github.com:devbasex/ai-plugins.git", REPO),
        ("ssh://git@github.com/devbasex/ai-plugins.git", REPO),
        ("https://gitlab.com/devbasex/ai-plugins.git", None),
        ("git@gitlab.com:devbasex/ai-plugins.git", None),
        ("https://github.com.evil.example/devbasex/ai-plugins.git", None),
        ("/srv/mirror/devbasex/ai-plugins.git", None),
    ],
)
def test_github_slug_checks_host(url, slug):
    assert cpu_mod.github_slug(url) == slug


def test_count_launches_state_on_other_host_is_unmeasurable(tmp_path):
    """末尾が対象リポジトリと同じでも、GitHub 以外のホストの origin の状態ディレクトリは読まない。"""
    state, log = _launch_env(tmp_path, _keyed_rows())
    _git(state.parent, "remote", "set-url", "origin", "https://gitlab.com/devbasex/ai-plugins.git")
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["launched"]) == (None, 0) and got["foreign_state"]


def test_count_launches_reads_origin_after_insteadof(tmp_path):
    """短縮形の origin（`gh:o/r.git`）は insteadOf で GitHub へ展開した先で対象リポジトリと認める。"""
    state, log = _launch_env(tmp_path, _keyed_rows())
    repo = state.parent
    _git(repo, "config", "url.https://github.com/.insteadOf", "gh:")
    _git(repo, "remote", "set-url", "origin", f"gh:{REPO}.git")
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert got["launched"] == 3 and not got["foreign_state"]


def test_count_launches_github_url_rewritten_to_other_host_is_unmeasurable(tmp_path):
    """GitHub の URL を別ホストへ書き換える設定では、書き換えた先が GitHub でないので読まない。"""
    state, log = _launch_env(tmp_path, _keyed_rows())
    _git(state.parent, "config", "url.https://gitlab.com/.insteadOf", "https://github.com/")
    got = cpu_mod.count_launches([state], log, _dt(SINCE), _dt(UNTIL), REPO)["u2"]
    assert (got["value"], got["launched"]) == (None, 0) and got["foreign_state"]


def _plan_env(tmp_path: Path, usage_rows: list[dict], steps=("a", "b", "c")):
    """起動 3 回（鍵 r-1 の a・b・c、試行 1）の状態ディレクトリと使用量ログ。"""
    state = tmp_path / ".ndf"
    rows = [{"kind": "run", "at": "2026-11-18T00:30:00+00:00", "from": None, "launches": 1}]
    rows += [_launch(f"2026-11-18T0{i + 1}:00:00+00:00", st, 1) for i, st in enumerate(steps)]
    _progress(state, rows)
    log = tmp_path / "usage.jsonl"
    _jsonl(log, usage_rows)
    return state, log


def _row(step: str, at: str = "2026-11-18T05:00:00+00:00", **meta) -> dict:
    meta.setdefault("model", "claude-opus-5-5")
    return _cli(at, run_id="r-1", step=step, attempt=1, **meta)


def test_plan_cost_skips_counted_sessions(tmp_path):
    rows = [
        _row("a", session_id="s-1", repo=REPO),
        _row("b", session_id="s-2", repo=REPO),
        _row("c", session_id="s-3", repo="DevBaseX/AI-Plugins"),
    ]
    state, log = _plan_env(tmp_path, rows)
    got = cpu_mod.plan_cost([state], log, {"s-2": 0.0}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    one = cpu_mod.row_cost(rows[0])
    assert one == pytest.approx(
        10 + 4 * cpu_mod.tu.WEIGHTS["w5"] + 2 * cpu_mod.tu.WEIGHTS["out"] + 100 * cpu_mod.tu.read_rate("claude-opus-5-5")
    )
    assert (got["rows"], got["skipped_counted"], got["missing"], got["complete"], got["reason"]) == (2, 1, 0, True, None)
    assert got["value"] == pytest.approx(2 * one, abs=0.01)


def test_plan_cost_counts_by_launch_time_not_record_time(tmp_path):
    """期間は起動の時刻で決める。終わりの後に記録された期間の起動の行は足し、期間の外の起動の行は足さない。"""
    rows = [
        _row("a", session_id="s-1", repo=REPO),
        _row("b", "2026-11-19T00:05:00+00:00", session_id="s-2", repo=REPO),  # 終わりの後に記録
        _row("c", session_id="s-3", repo=REPO),
        _cli(
            "2026-11-18T01:00:00+00:00",
            run_id="r-0",
            step="a",
            attempt=1,
            session_id="s-0",
            repo=REPO,
            launched_at="2026-11-17T23:00:00+00:00",
        ),
    ]
    state, log = _plan_env(tmp_path, rows)
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["rows"], got["complete"]) == (3, True)


def test_plan_cost_writer_introduced_mid_period_is_incomplete(tmp_path):
    """使用量の書き手が入る前の起動に行が無ければ、対象リポジトリの行が他にあっても complete: false。"""
    state, log = _plan_env(tmp_path, [_row("c", session_id="s-3", repo=REPO)])
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["rows"], got["launched"], got["missing"], got["complete"]) == (1, 3, 2, False) and "2 件" in got["reason"]


def test_plan_cost_adds_only_target_repo(tmp_path):
    """同じ鍵の行でも別のリポジトリの行は足さない。帰属を確かめられない行は足さずに不完全とする。"""
    rows = [_row("a", session_id="s-1", repo=REPO), _row("b", session_id="s-2", repo=REPO), _row("c", session_id="s-3", repo=REPO)]
    state, log = _plan_env(tmp_path, [*rows, _row("a", session_id="s-9", repo="other/repo")])
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["rows"], got["other_repo"], got["complete"]) == (3, 1, True)
    _jsonl(log, [_row("a", session_id="s-1", repo="other/repo")])
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["value"], got["other_repo"], got["complete"]) == (0, 1, False) and got["reason"]
    _jsonl(log, [*rows[:2], _row("c", session_id="s-3")])
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["rows"], got["complete"]) == (2, False) and "repo" in got["reason"]


def test_plan_cost_without_rows_is_incomplete(tmp_path):
    state, log = _plan_env(tmp_path, [{"at": "2026-11-18T01:00:00+00:00", "caller": "subagent", "agent_id": "a", "usage": {}}])
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["value"], got["rows"], got["complete"]) == (0, 0, False) and got["reason"]
    missing = cpu_mod.plan_cost([state], tmp_path / "none.jsonl", {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert missing["complete"] is False and missing["reason"]
    empty = cpu_mod.plan_cost([tmp_path / "nowhere"], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert empty["complete"] is False and "母数" in empty["reason"]


def test_plan_cost_rows_without_session_are_not_added(tmp_path):
    rows = [_row("a", session_id="s-1", repo=REPO), _row("b", repo=REPO), _row("c", session_id="s-3", repo=REPO)]
    state, log = _plan_env(tmp_path, rows)
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["rows"], got["complete"]) == (2, False) and "session_id" in got["reason"]
    assert got["value"] == pytest.approx(2 * cpu_mod.row_cost(rows[0]), abs=0.01)


def test_plan_cost_unknown_usage_of_counted_session_is_incomplete(tmp_path):
    """会話の ID の一覧にある起動でも、usage が unknown なら加算の除外より先に下限として扱う（complete: false）。"""
    rows = [
        _row("a", session_id="s-1", repo=REPO),
        _row("b", session_id="s-2", repo=REPO),
        _cli("2026-11-18T05:00:00+00:00", "unknown", run_id="r-1", step="c", attempt=1, session_id="s-3", repo=REPO),
    ]
    state, log = _plan_env(tmp_path, rows)
    got = cpu_mod.plan_cost([state], log, {"s-3": 0.0}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["rows"], got["skipped_counted"], got["complete"]) == (2, 0, False) and "unknown" in got["reason"]


def test_plan_cost_foreign_state_is_incomplete(tmp_path):
    rows = [_row("a", session_id="s-1", repo=REPO), _row("b", session_id="s-2", repo=REPO), _row("c", session_id="s-3", repo=REPO)]
    state = tmp_path / "other" / ".ndf"
    _progress(
        state,
        [{"kind": "run", "at": "2026-11-18T00:30:00+00:00", "from": None, "launches": 1}, _launch("2026-11-18T01:00:00+00:00", "a", 1)],
        slug="other/repo",
    )
    log = tmp_path / "usage.jsonl"
    _jsonl(log, rows)
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert got["complete"] is False and "other/repo" in got["reason"]


def test_plan_cost_counted_session_across_until_adds_the_rest(tmp_path):
    """期間の内に起動して終わりの後に記録を終えた会話は、会話側が期間の内の応答だけを U1 に入れているので、
    起動全体の費用から会話側で計上した費用を引いた残りを足す。期間の内に終えた会話は足さない。"""
    rows = [
        _row("a", session_id="s-1", repo=REPO),
        _row("b", "2026-11-19T00:30:00+00:00", session_id="s-2", repo=REPO),  # 終わりの後に記録（境をまたぐ）
        _row("c", session_id="s-3", repo=REPO),
    ]
    state, log = _plan_env(tmp_path, rows)
    one = cpu_mod.row_cost(rows[0])
    got = cpu_mod.plan_cost([state], log, {"s-1": one, "s-2": one / 4}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["rows"], got["skipped_counted"], got["boundary_counted"], got["complete"]) == (1, 1, 1, True)
    assert got["value"] == pytest.approx(one + one * 3 / 4, abs=0.01)
    # 会話側の値が起動全体を超えても負にしない
    got = cpu_mod.plan_cost([state], log, {"s-2": one * 2}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert got["value"] == pytest.approx(2 * one, abs=0.01)


def test_plan_cost_rows_without_model_are_not_added(tmp_path):
    """cache read の倍率はモデルで 4 倍まで違う（Fable 5.2 は 0.025、既定は 0.1）。model の無い行は既定の倍率で
    足さず complete: false にする。"""
    rows = [
        _row("a", session_id="s-1", repo=REPO),
        _row("b", session_id="s-2", repo=REPO, model=None),
        _row("c", session_id="s-3", repo=REPO),
    ]
    state, log = _plan_env(tmp_path, rows)
    got = cpu_mod.plan_cost([state], log, {}, _dt(SINCE), _dt(UNTIL), REPO)["plan_cost"]
    assert (got["rows"], got["complete"]) == (2, False) and "model" in got["reason"]
    assert got["value"] == pytest.approx(2 * cpu_mod.row_cost(rows[0]), abs=0.01)


@pytest.mark.parametrize("text", ["s-1\n", "s-1\tabc\n", "\t1.0\n"])
def test_plan_cost_cli_rejects_counted_without_cost(tmp_path, capsys, text):
    state, log = _plan_env(tmp_path, [])
    counted = tmp_path / "sessions.txt"
    counted.write_text(text, encoding="utf-8")
    argv = ["plan-cost", "--since", SINCE, "--until", UNTIL, "--repo", REPO, "--usage-log", str(log), "--state", str(state)]
    assert cpu_mod.main([*argv, "--counted", str(counted)]) == 2
    assert "形" in capsys.readouterr().err


def test_plan_cost_cli_prints_json_and_reads_counted(tmp_path, capsys):
    state, log = _plan_env(tmp_path, [_row(st, session_id=f"s-{i}", repo=REPO) for i, st in enumerate("abc", 1)])
    counted = tmp_path / "sessions.txt"
    counted.write_text("s-1\t1.0\n\ns-3\t2.5\n", encoding="utf-8")
    argv = ["plan-cost", "--since", "2026-11-18T09:00:00+09:00", "--until", "2026-11-19T09:00:00+09:00", "--repo", REPO]
    assert cpu_mod.main([*argv, "--counted", str(counted), "--usage-log", str(log), "--state", str(state)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["since"], out["until"]) == ("2026-11-18T09:00:00+09:00", "2026-11-19T09:00:00+09:00")
    assert (out["plan_cost"]["rows"], out["plan_cost"]["skipped_counted"]) == (1, 2)


def test_measure_cli_reads_once_and_matches_both_subcommands(tmp_path, capsys, monkeypatch):
    """measure は母数の収集と照合を 1 回だけ行い、launches・plan-cost と同じ値を 1 つの JSON で返す。"""
    state, log = _plan_env(tmp_path, [_row(st, session_id=f"s-{i}", repo=REPO) for i, st in enumerate("ab", 1)])
    counted = tmp_path / "sessions.txt"
    counted.write_text("s-1\t1.0\n", encoding="utf-8")
    argv = ["--since", SINCE, "--until", UNTIL, "--repo", REPO, "--usage-log", str(log), "--state", str(state)]
    calls = {"collect": 0, "match": 0}
    collect, match = cpu_mod.collect_launches, cpu_mod.match_usage

    def counting(name, fn):
        def wrapped(*a, **k):
            calls[name] += 1
            return fn(*a, **k)

        return wrapped

    monkeypatch.setattr(cpu_mod, "collect_launches", counting("collect", collect))
    monkeypatch.setattr(cpu_mod, "match_usage", counting("match", match))
    assert cpu_mod.main(["measure", *argv, "--counted", str(counted)]) == 0
    both = json.loads(capsys.readouterr().out)
    assert calls == {"collect": 1, "match": 1}
    assert cpu_mod.main(["launches", *argv]) == 0
    assert both["u2"] == json.loads(capsys.readouterr().out)["u2"]
    assert cpu_mod.main(["plan-cost", *argv, "--counted", str(counted)]) == 0
    assert both["plan_cost"] == json.loads(capsys.readouterr().out)["plan_cost"]
    assert (both["u2"]["recorded"], both["plan_cost"]["rows"], both["plan_cost"]["skipped_counted"]) == (2, 1, 1)


def test_launches_cli_default_usage_log_follows_ndf_usage_dir(tmp_path, capsys, monkeypatch):
    state, log = _launch_env(tmp_path, [_cli("2026-11-18T03:50:30+00:00", run_id="r-1", step="impl", attempt=1)])
    monkeypatch.setenv("NDF_USAGE_DIR", str(log.parent))
    assert cpu_mod.main(["launches", "--since", SINCE, "--until", UNTIL, "--state", str(state), "--repo", REPO]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["u2"]["launched"], out["u2"]["recorded"]) == (3, 1)


def test_default_usage_log_rule():
    assert cpu_mod.default_usage_log({"NDF_USAGE_DIR": "/u"}) == Path("/u/usage.jsonl")
    assert cpu_mod.default_usage_log({"NDF_STATE_HOME": "/n", "XDG_STATE_HOME": "/x"}) == Path("/n/ndf/usage/usage.jsonl")
    assert cpu_mod.default_usage_log({"XDG_STATE_HOME": "/x"}) == Path("/x/ndf/usage/usage.jsonl")
    assert cpu_mod.default_usage_log({}) == Path.home() / ".local/state/ndf/usage/usage.jsonl"


@pytest.mark.parametrize("cmd", ["launches", "plan-cost", "measure"])
@pytest.mark.parametrize("bad", ["2026-11-18T00:00:00", "2026-11-18", "yesterday"])
def test_measure_since_without_offset_exits_2(tmp_path, capsys, cmd, bad):
    extra = (["--counted", str(tmp_path / "c.txt")] if cmd != "launches" else []) + ["--repo", "devbasex/ai-plugins"]
    with pytest.raises(SystemExit) as e:
        cpu_mod.main([cmd, "--since", bad, "--until", UNTIL, *extra])
    assert e.value.code == 2
    assert bad in capsys.readouterr().err
