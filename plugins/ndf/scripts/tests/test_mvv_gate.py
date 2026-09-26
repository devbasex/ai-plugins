"""mvv-gate.py: 関門 1・2 の材料が MVV に従うかの判定と、関門を省く条件（#1078）。

claude は NDF_MVV_CLAUDE で、gh は PATH の先頭の偽物で差し替える。差し替えた claude は呼ばれるたびに
calls.txt へ 1 行を足す（機械のチェックで止めるときに LLM を呼ばないことを数で見る）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "mvv-gate.py"
sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import validate_result  # noqa: E402

FOLLOW = '{"verdict": "follow", "reasons": ["Value 1 に沿う"], "boundary": []}'
MVV = "## Mission\n速くする\n\n## Vision\n止まらない\n\n## Value\n1. スクリプトで判定する\n"


def fake_claude(tmp_path: Path, text: str) -> str:
    out = tmp_path / "out.json"
    out.write_text(json.dumps({"result": text, "total_cost_usd": 0.01, "duration_ms": 1500}))
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\necho x >> {tmp_path / 'calls.txt'}\ncat > {tmp_path / 'prompt.txt'}\ncat {out}\n")
    fake.chmod(0o755)
    return str(fake)


def fake_gh(tmp_path: Path, files: list[str]) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    info = {"title": "変更", "body": "本文", "files": [{"path": f, "additions": 1, "deletions": 0} for f in files]}
    gh = bindir / "gh"
    gh.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(info, ensure_ascii=False)}\nEOF\n")
    gh.chmod(0o755)
    return str(bindir)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def mission(tmp_path):
    """MVV を写して利用者が承認したミッションの状態（mission-state.py の形）。"""
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "pace.json").write_text(json.dumps({
        "version": 1, "fast": {"enabled": True, "verify": "true"}, "areas": [],
        "boundary_paths": ["lib/auth.py", ".github/workflows/**"]}))
    mvv = tmp_path / "state" / "mvv.md"
    mvv.parent.mkdir()
    mvv.write_text(MVV)
    state = tmp_path / "state" / "mission-state.json"
    state.write_text(json.dumps({
        "name": "m", "milestone": "26", "issues": [1], "versions": {"dev": "", "prod": ""}, "plans": [], "done": [],
        "goal_template": "", "pace": "fast", "mvv": {"path": str(mvv), "sha256": sha(MVV)},
        "gates": [{"name": "MVV", "what": "MVV を承認", "at": "2026-09-25T00:00:00+00:00", "sha256": sha(MVV)}]},
        ensure_ascii=False))
    material = tmp_path / "approval.md"
    material.write_text("# 配布\n")
    return {"root": root, "state": state, "mvv": mvv, "material": material, "log": tmp_path / "log.jsonl",
            "tmp": tmp_path}


def run(m: dict, text: str, *extra: str, files: list[str] | None = None) -> tuple[int, dict, list[dict]]:
    tmp = m["tmp"]
    env = {"PATH": f"{fake_gh(tmp, files or ['app/x.py'])}:/usr/bin:/bin", "HOME": str(tmp),
           "NDF_MVV_CLAUDE": fake_claude(tmp, text)}
    args = [sys.executable, str(SCRIPT), "check", "--mission", str(m["state"]), "--gate", "release",
            "--material", str(m["material"]), "--log", str(m["log"]), "--root", str(m["root"]), *extra]
    p = subprocess.run(args, capture_output=True, text=True, env=env, cwd=m["root"])
    out = json.loads(p.stdout.splitlines()[-1])
    assert validate_result(out, p.returncode) == [], (out, p.returncode, p.stderr)
    rows = [json.loads(ln) for ln in m["log"].read_text().splitlines()] if m["log"].exists() else []
    return p.returncode, out, rows


def llm_calls(m: dict) -> int:
    f = m["tmp"] / "calls.txt"
    return len(f.read_text().splitlines()) if f.exists() else 0


def gates(m: dict) -> dict:
    return {g["name"]: g for g in json.loads(m["state"].read_text())["gates"]}


def test_follow_passes_the_gate_and_is_recorded(mission):
    code, out, rows = run(mission, FOLLOW, "--pr", "5")
    assert (code, out["status"]) == (0, "ok")
    assert len(rows) == 1 and rows[0]["passed"] is True and rows[0]["reasons"] == ["Value 1 に沿う"]
    g = gates(mission)["関門 2"]
    assert (g["by"], g["verdict"], g["reasons"], g["log"]) == ("mvv", "follow", ["Value 1 に沿う"],
                                                              str(mission["log"]))
    assert "速くする" in (mission["tmp"] / "prompt.txt").read_text()


def test_design_gate_is_recorded_as_gate_1(mission):
    tmp = mission["tmp"]
    env = {"PATH": f"{fake_gh(tmp, ['issues/x.md'])}:/usr/bin:/bin", "HOME": str(tmp),
           "NDF_MVV_CLAUDE": fake_claude(tmp, FOLLOW)}
    p = subprocess.run([sys.executable, str(SCRIPT), "check", "--mission", str(mission["state"]), "--gate", "design",
                        "--pr", "7", "--log", str(mission["log"]), "--root", str(mission["root"]),
                        "--note", str(tmp / "note.md")], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert gates(mission)["関門 1"]["by"] == "mvv"
    assert "follow" in (tmp / "note.md").read_text()


@pytest.mark.parametrize("text", [
    '{"verdict": "not_follow", "reasons": ["Value 2"], "boundary": []}',
    '{"verdict": "unknown", "reasons": [], "boundary": []}',
    '{"verdict": "follow", "reasons": [], "boundary": ["他のリポジトリへの公開"]}',
    "判定できませんでした",
])
def test_anything_but_a_clean_follow_asks_the_user(mission, text):
    code, out, rows = run(mission, text)
    assert (code, out["status"]) == (10, "gate")
    assert rows[-1]["passed"] is False
    assert "関門 2" not in gates(mission)


def test_without_the_approval_the_llm_is_not_called(mission):
    st = json.loads(mission["state"].read_text())
    st["gates"] = []
    mission["state"].write_text(json.dumps(st))
    code, out, _ = run(mission, FOLLOW)
    assert (code, llm_calls(mission)) == (10, 0)


def test_a_changed_mvv_after_the_approval_goes_back_to_the_user(mission):
    mission["mvv"].write_text(MVV + "\n4. LLM が足した行\n")
    code, out, _ = run(mission, FOLLOW)
    assert (code, llm_calls(mission)) == (10, 0)
    assert "ハッシュ" in out["summary"]


@pytest.mark.parametrize("mode", ["operation", "documentation"])
def test_modes_outside_fast_never_skip_the_gate(mission, mode):
    code, out, _ = run(mission, FOLLOW, "--mode", mode)
    assert (code, llm_calls(mission)) == (10, 0)


@pytest.mark.parametrize("path", ["lib/auth.py", ".github/workflows/ci.yml"])
def test_boundary_paths_never_skip_the_gate(mission, path):
    code, out, rows = run(mission, FOLLOW, "--pr", "5", files=["app/x.py", path])
    assert (code, llm_calls(mission)) == (10, 0)
    assert path in out["summary"]
    assert rows[-1]["verdict"] == "machine"


def test_a_missing_material_goes_back_to_the_user(mission):
    mission["material"].unlink()
    code, out, _ = run(mission, FOLLOW)
    assert (code, llm_calls(mission)) == (10, 0)


DESIGN_DOC = "# 設計\n\nドメインモデルの節\n"


def fake_gh_with_design(tmp_path: Path, files: list[str], api_fails: bool = False) -> str:
    """`gh pr view` は PR の情報を、`gh api .../contents/...` は設計文書の中身を返す偽物。呼び出しを gh-calls.txt へ残す。"""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    info = {"title": "設計", "body": "本文", "headRefOid": "abc123",
            "files": [{"path": f, "additions": 1, "deletions": 0} for f in files]}
    (tmp_path / "info.json").write_text(json.dumps(info, ensure_ascii=False))
    (tmp_path / "doc.md").write_text(DESIGN_DOC)
    api = "echo 'HTTP 404' >&2; exit 1" if api_fails else f"cat {tmp_path / 'doc.md'}"
    gh = bindir / "gh"
    gh.write_text(f"#!/bin/sh\necho \"$*\" >> {tmp_path / 'gh-calls.txt'}\n"
                  f"if [ \"$1\" = api ]; then {api}; exit 0; fi\ncat {tmp_path / 'info.json'}\n")
    gh.chmod(0o755)
    return str(bindir)


def run_design(m: dict, files: list[str], gate: str = "design", api_fails: bool = False) -> tuple[int, dict, list[dict]]:
    tmp = m["tmp"]
    env = {"PATH": f"{fake_gh_with_design(tmp, files, api_fails)}:/usr/bin:/bin", "HOME": str(tmp),
           "NDF_MVV_CLAUDE": fake_claude(tmp, FOLLOW)}
    p = subprocess.run([sys.executable, str(SCRIPT), "check", "--mission", str(m["state"]), "--gate", gate,
                        "--pr", "7", "--log", str(m["log"]), "--root", str(m["root"]), "--repo", "o/r"],
                       capture_output=True, text=True, env=env, cwd=m["root"])
    out = json.loads(p.stdout.splitlines()[-1])
    assert validate_result(out, p.returncode) == [], (out, p.returncode, p.stderr)
    rows = [json.loads(ln) for ln in m["log"].read_text().splitlines()] if m["log"].exists() else []
    return p.returncode, out, rows


def test_design_gate_passes_the_design_doc_of_the_pr_as_material(mission):
    code, out, rows = run_design(mission, ["issues/x-design.md", "app/x.py"])
    assert code == 0, out
    assert "ドメインモデルの節" in (mission["tmp"] / "prompt.txt").read_text()
    assert rows[-1]["material"] == ["#7 issues/x-design.md"]
    api = [ln for ln in (mission["tmp"] / "gh-calls.txt").read_text().splitlines() if ln.startswith("api")]
    assert len(api) == 1 and "repos/o/r/contents/issues/x-design.md?ref=abc123" in api[0]


def test_release_gate_does_not_fetch_design_docs(mission):
    code, out, rows = run_design(mission, ["issues/x-design.md"], gate="release")
    assert code == 0, out
    assert "ドメインモデルの節" not in (mission["tmp"] / "prompt.txt").read_text()
    assert rows[-1]["material"] == []


def test_an_unreadable_design_doc_goes_back_to_the_user(mission):
    code, out, rows = run_design(mission, ["issues/x-design.md"], api_fails=True)
    assert (code, llm_calls(mission)) == (10, 0)
    assert "issues/x-design.md" in out["summary"]
    assert rows[-1]["verdict"] == "machine"
