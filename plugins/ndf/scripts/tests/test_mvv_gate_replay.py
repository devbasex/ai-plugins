"""MVV 判定を変更の前と後の指示文で判定し直して数える実験版（実験版の置き場の `mvv-gate-replay.py`、#2912 の F4）。

gh と claude は偽物で差し替える。偽の claude は、渡された指示文に今の指示文だけが持つ語があれば follow を、
無ければ unknown を返す（指示文の差だけが判定を変える形）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "experimental" / "mvv-gate-replay.py"
sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import validate_result  # noqa: E402

NEW_ONLY = "起票者の推定"  # 今の指示文にあり、変更の前の指示文に無い語
FOLLOW = '{"verdict": "follow", "reasons": ["Value 2"], "boundary": []}'
UNKNOWN = '{"verdict": "unknown", "reasons": ["Value 2"], "boundary": []}'
BOUNDARY = '{"verdict": "follow", "reasons": ["C7"], "boundary": ["C7: .ndf/ を書き換える"]}'


def row(pr: int, verdict: str, reasons: list[str], boundary: list[str] | None = None, at: str = "2026-11-08T00:00:00+00:00", gate="design"):
    return {"at": at, "gate": gate, "pr": [pr], "verdict": verdict, "reasons": reasons, "boundary": boundary or []}


LOG = [
    row(1, "unknown", ["既定値を設計承認ゲートで利用者が決めるとしている"], at="2026-11-07T00:00:00+00:00"),
    row(1, "unknown", ["範囲を承認ゲート 1 で人が決める事項としている"]),  # PR 1 の最後の行
    row(2, "unknown", ["材料が足りない"]),  # 委ねた判断を挙げない
    row(3, "not_follow", ["選択が利用者に委ねられている"]),
    row(4, "follow", ["Value 1"], ["C7: .ndf/ を変える"]),  # 対照
    row(5, "unknown", ["利用者が決める"], ["P1"]),  # boundary が空でない → 対照
    row(6, "unknown", ["利用者が決める"], gate="release"),  # 設計承認ゲートでない
    row(7, "unknown", ["利用者が決める"], at="2026-11-10T00:00:01+00:00"),  # 期間の外
]


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "r"
    gate = root / "plugins/ndf/scripts/mvv-gate.py"
    gate.parent.mkdir(parents=True)
    gate.write_text('SYSTEM = """変更の前の指示文"""\n', encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    log = tmp_path / "mvv-gate.jsonl"
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in LOG) + "読めない行\n", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    info = {"title": "設計", "body": "本文", "headRefOid": "abc123", "changedFiles": 1}
    (tmp_path / "info.json").write_text(json.dumps(info, ensure_ascii=False))
    files = [{"filename": "issues/issue-1-design.md", "status": "added", "additions": 3, "deletions": 0}]
    (tmp_path / "files.json").write_text(json.dumps(files))
    (tmp_path / "doc.md").write_text("# 設計\n\n## 決定の記録\n", encoding="utf-8")
    gh = bindir / "gh"
    gh.write_text(
        f'#!/bin/sh\ncase "$*" in *pulls/*/files*) cat {tmp_path / "files.json"}; exit 0;; esac\n'
        f'if [ "$1" = api ]; then cat {tmp_path / "doc.md"}; exit 0; fi\ncat {tmp_path / "info.json"}\n'
    )
    gh.chmod(0o755)
    outs = {}
    for name, text in (("follow", FOLLOW), ("unknown", UNKNOWN), ("boundary", BOUNDARY)):
        outs[name] = tmp_path / f"{name}.json"
        outs[name].write_text(json.dumps({"result": text, "total_cost_usd": 0.25, "duration_ms": 10}, ensure_ascii=False))
    claude = tmp_path / "claude"
    claude.write_text(
        f"#!/bin/sh\necho x >> {tmp_path / 'calls.txt'}\ncat > /dev/null\n"
        f'case "$*" in *{NEW_ONLY}*) ;; *) cat {outs["unknown"]}; exit 0;; esac\n'
        f"if [ -f {tmp_path / 'boundary.flag'} ]; then cat {outs['boundary']}; else cat {outs['follow']}; fi\n"
    )
    claude.chmod(0o755)
    return {"root": root, "log": log, "tmp": tmp_path, "bin": bindir, "claude": claude}


def run(m, *extra):
    out = m["tmp"] / "out" / "replay.json"
    environ = {"PATH": f"{m['bin']}:/usr/bin:/bin", "HOME": str(m["tmp"]), "NDF_MVV_CLAUDE": str(m["claude"])}
    args = [sys.executable, str(SCRIPT), "run", "--before", "HEAD", "--out", str(out), "--log", str(m["log"])]
    p = subprocess.run([*args, "--root", str(m["root"]), "--repo", "o/r", *extra], capture_output=True, text=True, env=environ)
    res = json.loads(p.stdout.splitlines()[-1]) if p.stdout.strip() else None
    assert res is None or validate_result(res, p.returncode) == [], (res, p.stderr)
    return p.returncode, res, out


def calls(m) -> int:
    f = m["tmp"] / "calls.txt"
    return len(f.read_text().splitlines()) if f.exists() else 0


def test_select_only_picks_target_and_control_without_judging(env):
    code, res, out = run(env, "--select-only")
    assert code == 0, res
    kinds = {(it["kind"], it["pr"][0]) for it in res["items"]}
    assert kinds == {("target", 1), ("target", 3), ("control", 4), ("control", 5)}
    assert calls(env) == 0
    assert json.loads(out.read_text()) == res


def test_run_counts_before_and_after_rates(env):
    code, res, out = run(env)
    assert code == 0, res
    t = res["metrics"]["target"]
    assert t["prs"] == 2
    assert t["before"] == {"passed": 0, "rate": 0.0, "unreadable": 0}
    assert t["after"] == {"passed": 2, "rate": 1.0, "unreadable": 0}
    assert t["recorded_mismatch"] == 1  # PR 3 は記録が not_follow、変更の前の判定が unknown
    assert res["metrics"]["control"]["after"]["passed"] == 2
    assert calls(env) == 2 * 2 + 2  # 対象は前と後の 2 回、対照は後の 1 回
    assert res["metrics"]["cost_usd"] == pytest.approx(0.25 * 6)
    assert json.loads(out.read_text()) == res


def test_control_with_boundary_is_not_counted_as_passed(env):
    (env["tmp"] / "boundary.flag").write_text("x")
    code, res, _ = run(env)
    assert code == 0, res
    assert res["metrics"]["control"]["after"]["passed"] == 0
    assert res["metrics"]["target"]["after"]["passed"] == 0


def test_unreadable_judgements_stay_in_the_denominator(env):
    env["claude"].write_text("#!/bin/sh\ncat > /dev/null\necho 'not json'\n")
    code, res, _ = run(env)
    assert code == 0, res
    assert res["metrics"]["target"]["after"] == {"passed": 0, "rate": 0.0, "unreadable": 2}


def test_the_record_and_the_sprint_state_are_not_written(env):
    """I6: MVV 判定の記録とスプリントの状態へ書かない（Claude の使用量の帳簿は起動の共有処理が書く）。"""
    before = env["log"].read_text()
    code, res, out = run(env)
    assert code == 0, res
    assert env["log"].read_text() == before
    written = {p.name for p in env["tmp"].rglob("*") if p.is_file()}
    assert "state.json" not in written
    assert [p for p in env["tmp"].rglob("mvv-gate.jsonl")] == [env["log"]]


def test_an_unreadable_revision_returns_2(env):
    code, res, _ = run(env, "--before", "no-such-rev")
    assert code == 2


def test_no_rows_in_the_period_returns_3(env):
    code, res, _ = run(env, "--until", "2026-01-01T00:00:00Z")
    assert code == 3
