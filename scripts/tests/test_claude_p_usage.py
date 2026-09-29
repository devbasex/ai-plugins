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
    r = run_tu(roots, "--repo", str(tmp_path / "not-a-repo"), "--prs-json", str(missing))
    assert "release_prs" not in r["per_pr"][0]
    assert not missing.exists()
