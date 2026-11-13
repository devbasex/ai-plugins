"""旧名の呼び方（#1407 の AC4・AC5・AC7・I1〜I4・I6）。旧名の受け付けをやめる課題は、このファイルを「断る」テストへ書き換える。

- 旧名の入口（`new mission`・`--mission`・`--kind mission`・旧名のスクリプト）は新しい名前と同じ結果を返し、案内を 1 行出す
- `MODE` が `refuse` なら、新しい名前を示して終了コード 2 で止まり、何も書かない
- 改名の前のプランのキー・記録のキー・ブランチの頭・記録の行の見出しを読み、旧名のファイルを書き換えない
- 旧名の文字列は表（`lib/legacy_names.py`）と旧名の入口とこのファイルの外に無い
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import legacy_names as ln  # noqa: E402
import pr_mode  # noqa: E402
import project_mvv_signals as pms  # noqa: E402

PY = sys.executable
SUPERVISE = SCRIPTS / "supervise.py"
OLD_SCRIPTS = ("mission-state.py", "mission-close.py", "bundle-close.py")


def run(script: Path, *args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(script), *map(str, args)], capture_output=True, text=True, cwd=cwd)


def snapshot(d: Path) -> dict[str, str]:
    return {str(p.relative_to(d)): p.read_text() for p in sorted(d.rglob("*")) if p.is_file()}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def notices(stderr: str) -> list[str]:
    return [x for x in stderr.splitlines() if "改名した" in x]


# ---------------------------------------------------------------- 入口の引数（AC4・I1）


def test_new_check_with_mission_is_the_same_as_with_sprint(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"name": "m", "issues": [11], "gates": []}))
    out = tmp_path / "check.json"
    base = ["new", "check", "--since-last", "--id", "m-3", "--worktree", str(tmp_path), "--out", str(out)]
    new = run(SUPERVISE, *base, "--sprint", state)
    assert new.returncode == 0, new.stdout + new.stderr
    plan = out.read_text()
    out.unlink()
    old = run(SUPERVISE, *base, "--mission", state)
    assert (old.returncode, old.stdout, out.read_text()) == (new.returncode, new.stdout, plan)
    assert len(notices(old.stderr)) == 1 and "--sprint" in notices(old.stderr)[0]


@pytest.mark.parametrize(
    ("entry", "argv", "want"),
    [
        ("mvv-gate.py", ["check", "--mission", "s.json", "--gate", "design"], ["check", "--sprint", "s.json", "--gate", "design"]),
        ("mvv-gate.py", ["check", "--mission=s.json"], ["check", "--sprint=s.json"]),
        ("project-mvv.py", ["vet", "--body", "b.md", "--kind", "mission"], ["vet", "--body", "b.md", "--kind", "sprint"]),
        ("project-mvv.py", ["vet", "--kind=mission"], ["vet", "--kind=sprint"]),
        ("project-mvv.py", ["context", "--mission", "s.json"], ["context", "--sprint", "s.json"]),
        ("supervise.py", ["new", "mission", "--name", "mission"], ["new", "sprint", "--name", "mission"]),
    ],
)
def test_rewrite_argv_replaces_old_names_and_tells_once_each(entry, argv, want, capsys):
    assert ln.rewrite_argv(entry, argv) == want
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_rewrite_argv_leaves_new_names_and_values_alone(capsys):
    argv = ["vet", "--body", "mission", "--kind", "sprint"]
    assert ln.rewrite_argv("project-mvv.py", argv) == argv
    assert ln.rewrite_argv("unknown.py", ["--mission"]) == ["--mission"]
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------- 旧名のスクリプト（AC4・AC5）


def test_old_state_script_gives_the_same_result_with_one_notice(tmp_path):
    state = tmp_path / "sprint-state.json"
    assert run(SCRIPTS / "sprint-state.py", "init", state, "--name", "m").returncode == 0
    new = run(SCRIPTS / "sprint-state.py", "status", state)
    old = run(SCRIPTS / "mission-state.py", "status", state)
    assert (old.returncode, old.stdout) == (new.returncode, new.stdout) and new.stdout.startswith("スプリント: m")
    assert notices(old.stderr) == [ln.rename_notice("mission-state.py", "sprint-state.py")]


@pytest.mark.parametrize("old", ["mission-close.py", "bundle-close.py"])
def test_old_close_scripts_return_the_exit_code_of_the_new_one(old):
    new = run(SCRIPTS / "sprint-close.py", "--record-pr", "0")  # --issues が無い: 2
    got = run(SCRIPTS / old, "--record-pr", "0")
    # 実行ごとに変わる実行の識別子（run_id。#2337）を除いて比べる
    strip = lambda out: {k: v for k, v in json.loads(out).items() if k != "run_id"}  # noqa: E731
    assert (got.returncode, strip(got.stdout)) == (new.returncode, strip(new.stdout)) and new.returncode == 2
    assert notices(got.stderr) == [ln.rename_notice(old, "sprint-close.py")]


def test_old_scripts_are_thin_entries_to_the_table():
    for name in OLD_SCRIPTS:
        text = (SCRIPTS / name).read_text()
        assert "legacy_names.forward(__file__)" in text and len(text.splitlines()) < 20
        assert ln.SCRIPTS[name] in ("sprint-state.py", "sprint-close.py")


# ---------------------------------------------------------------- 受け付けをやめた後（AC7・I6）


def test_refuse_does_not_run_the_new_script(tmp_path, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(ln.os, "execv", lambda *a: ran.append(a))
    with pytest.raises(SystemExit) as e:
        ln.forward(str(SCRIPTS / "mission-state.py"), mode="refuse")
    assert e.value.code == 2 and ran == []
    assert "sprint-state.py" in capsys.readouterr().err


# ---------------------------------------------------------------- 改名の前のプランと記録（AC5・I3）


def test_old_plan_key_is_read_without_rewriting_the_plan(tmp_path):
    plan_file = tmp_path / "3-impl.json"
    plan_file.write_text(json.dumps({"ミッション状態": "/x/state.json"}, ensure_ascii=False))
    before = sha(plan_file)
    plan = json.loads(plan_file.read_text())
    assert ln.read_key(plan, "スプリント状態") == "/x/state.json"
    assert ln.read_key({"スプリント状態": "/new", "ミッション状態": "/old"}, "スプリント状態") == "/new"
    assert ln.read_key({}, "スプリント状態") is None and sha(plan_file) == before


def test_old_state_key_in_a_plan_gives_the_sprint_mvv(tmp_path):
    from supervise_lib.state import RunState

    mvv = tmp_path / "mvv.md"
    mvv.write_text("## Mission\n速く\n")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"mvv": {"path": str(mvv), "sha256": sha(mvv)}}))
    before = sha(state)
    rs = RunState(tmp_path / "sv", {"ミッション状態": str(state)})
    assert rs.sprint_mvv_of({"ミッション状態": str(state)}) == "## Mission\n速く\n"
    assert sha(state) == before


def test_old_record_key_counts_as_the_same_sprint(tmp_path):
    log = tmp_path / "mvv-gate.jsonl"
    state = tmp_path / "s.json"
    old_row = {"at": "2026-09-28T00:00:00Z", "gate": "design", "mission": str(state), "pr": [1], "verdict": "not_follow"}
    pms.append_jsonl(log, old_row)
    before = sha(log)
    assert pms.last_mvv_verdict({}, str(state), "設計承認ゲート", log, 1) == "not_follow"
    assert sha(log) == before
    pms.append_jsonl(log, {"at": "2026-09-28T00:01:00Z", "gate": "design", "sprint": str(state), "pr": [1], "verdict": "follow"})
    assert pms.last_mvv_verdict({}, str(state), "設計承認ゲート", log, 1) == "follow"


def test_old_record_labels_are_read(tmp_path):
    spec = importlib.util.spec_from_file_location("sprint_close_legacy", SCRIPTS / "sprint-close.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for label in ln.record_labels():
        out = mod.parse_record(f"## リリース記録\n\n段階: 本番\n{label}PR #7 / PR #8\n")
        assert out["sprint_prs"] == [7, 8]


# ---------------------------------------------------------------- ブランチの頭（AC4・I4）


def test_pr_target_reads_both_prefixes():
    assert pr_mode.pr_target("sprint/x") == pr_mode.pr_target("mission/x") == "sprint"
    assert pr_mode.pr_target("develop") == "base"
    assert not pr_mode.needs_review("mission/x")


# ---------------------------------------------------------------- 旧名は表の外に無い（I2）


def old_names() -> set[str]:
    names = {" ".join(old) for rows in ln.ARGS.values() for old, _ in rows}
    names |= set(ln.SCRIPTS)
    names |= set(ln.KEYS.values()) - {"mission"}  # キーの mission は MVV の Mission の候補のキーと同じ文字列（I5）
    names |= set(ln.BRANCH_PREFIXES.values())
    names |= {x for xs in ln.RECORD_LABELS.values() for x in xs}
    return names


def test_old_names_live_only_in_the_table():
    allowed = {SCRIPTS / "lib" / "legacy_names.py", Path(__file__).resolve(), *(SCRIPTS / n for n in OLD_SCRIPTS)}
    hits = []
    for p in sorted(SCRIPTS.rglob("*")):
        if p.suffix not in (".py", ".sh") or p.resolve() in allowed or "experimental" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        hits += [f"{p.relative_to(SCRIPTS)}: {n}" for n in old_names() if re.search(re.escape(n), text)]
    assert hits == []


def test_old_skill_name_is_forwarded_with_a_notice(capsys):
    """#1429 の決定 20: 旧名の Skill は新しい名前へ引き、案内を 1 行出す。旧名でない名前はそのまま返す。"""
    assert ln.skill_name("issue-upkeep") == "backlog-refinement"
    assert "backlog-refinement" in capsys.readouterr().err
    assert ln.skill_name("worktree") == "worktree"
    assert capsys.readouterr().err == ""
    with pytest.raises(SystemExit) as e:
        ln.skill_name("issue-upkeep", mode="refuse")
    assert e.value.code == 2
