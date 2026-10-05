"""mvv-gate.py: 関門 1・2 の材料が MVV に従うかの判定と、関門を省く条件（#1078）。

claude は NDF_MVV_CLAUDE で、gh は PATH の先頭の偽物で差し替える。差し替えた claude は呼ばれるたびに
calls.txt へ 1 行を足す（機械のチェックで止めるときに LLM を呼ばないことを数で見る）。
"""

from __future__ import annotations

import hashlib
import json
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


def write_rest_files(tmp_path: Path, files: list) -> None:
    """REST の `pulls/<n>/files` の形で、2 ページに分けて書く（`--paginate` の出力）。要素が (旧, 新) なら rename。"""
    rows = [
        {"filename": f[1], "previous_filename": f[0], "status": "renamed", "additions": 0, "deletions": 0}
        if isinstance(f, tuple)
        else {"filename": f, "status": "modified", "additions": 1, "deletions": 0}
        for f in files
    ]
    (tmp_path / "files.json").write_text(json.dumps(rows[:1]) + json.dumps(rows[1:]))


def fake_gh(tmp_path: Path, files: list, changed: int | None = None) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    info = {"title": "変更", "body": "本文", "changedFiles": len(files) if changed is None else changed}
    (tmp_path / "info.json").write_text(json.dumps(info, ensure_ascii=False))
    write_rest_files(tmp_path, files)
    gh = bindir / "gh"
    gh.write_text(f'#!/bin/sh\nif [ "$1" = api ]; then cat {tmp_path / "files.json"}; exit 0; fi\ncat {tmp_path / "info.json"}\n')
    gh.chmod(0o755)
    return str(bindir)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def sprint(tmp_path):
    """MVV を写して利用者が承認したスプリントの状態（sprint-state.py の形）。"""
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "pace.json").write_text(
        json.dumps(
            {
                "version": 1,
                "fast": {"enabled": True, "verify": "true"},
                "areas": [],
                "boundary_paths": ["lib/auth.py", ".github/workflows/**"],
            }
        )
    )
    mvv = tmp_path / "state" / "mvv.md"
    mvv.parent.mkdir()
    mvv.write_text(MVV)
    state = tmp_path / "state" / "sprint-state.json"
    state.write_text(
        json.dumps(
            {
                "name": "m",
                "milestone": "26",
                "issues": [1],
                "versions": {"dev": "", "prod": ""},
                "plans": [],
                "done": [],
                "goal_template": "",
                "pace": "fast",
                "mvv": {"path": str(mvv), "sha256": sha(MVV)},
                "gates": [{"name": "MVV", "what": "MVV を承認", "at": "2026-09-25T00:00:00+00:00", "sha256": sha(MVV)}],
            },
            ensure_ascii=False,
        )
    )
    material = tmp_path / "approval.md"
    material.write_text("# 配布\n")
    return {"root": root, "state": state, "mvv": mvv, "material": material, "log": tmp_path / "log.jsonl", "tmp": tmp_path}


def run(m: dict, text: str, *extra: str, files: list | None = None, changed: int | None = None) -> tuple[int, dict, list[dict]]:
    tmp = m["tmp"]
    env = {
        "PATH": f"{fake_gh(tmp, files or ['app/x.py'], changed)}:/usr/bin:/bin",
        "HOME": str(tmp),
        "NDF_MVV_CLAUDE": fake_claude(tmp, text),
    }
    args = [
        sys.executable,
        str(SCRIPT),
        "check",
        "--sprint",
        str(m["state"]),
        "--gate",
        "release",
        "--material",
        str(m["material"]),
        "--log",
        str(m["log"]),
        "--root",
        str(m["root"]),
        *extra,
    ]
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


def test_follow_passes_the_gate_and_is_recorded(sprint):
    code, out, rows = run(sprint, FOLLOW, "--pr", "5")
    assert (code, out["status"]) == (0, "ok")
    assert len(rows) == 1 and rows[0]["passed"] is True and rows[0]["reasons"] == ["Value 1 に沿う"]
    g = gates(sprint)["関門 2"]
    assert (g["by"], g["verdict"], g["reasons"], g["log"]) == ("mvv", "follow", ["Value 1 に沿う"], str(sprint["log"]))
    assert "速くする" in (sprint["tmp"] / "prompt.txt").read_text()


APPROVED = "a" * 40
MATERIAL = f"# 配布\n\n## 2. 承認の判断に使うもの\n\n| 項目 | 内容 |\n| --- | --- |\n| 承認したコミット | {APPROVED} |\n"


@pytest.mark.parametrize(
    "text, extra, recorded",
    [(FOLLOW, (), APPROVED), ('{"verdict": "unknown", "reasons": ["x"], "boundary": []}', (), None), (FOLLOW, ("--advise",), None)],
)
def test_release_follow_records_the_approved_commit_in_the_material(sprint, text, extra, recorded):
    """#815 の I10: 関門 2 を MVV 判定で通したときだけ、承認資料へ承認の記録（by: mvv）を書く。助言では書かない。"""
    sys.path.insert(0, str(SCRIPT.parent / "lib"))
    import approved_commit as ac

    sprint["material"].write_text(MATERIAL)
    run(sprint, text, "--pr", "5", *extra)
    assert ac.recorded_sha(sprint["material"]) == recorded
    if recorded:
        assert ac.from_material(sprint["material"]) == APPROVED and " を mvv が " in sprint["material"].read_text()


def test_design_gate_is_recorded_as_gate_1(sprint):
    tmp = sprint["tmp"]
    env = {"PATH": f"{fake_gh(tmp, ['issues/x.md'])}:/usr/bin:/bin", "HOME": str(tmp), "NDF_MVV_CLAUDE": fake_claude(tmp, FOLLOW)}
    p = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "check",
            "--sprint",
            str(sprint["state"]),
            "--gate",
            "design",
            "--pr",
            "7",
            "--log",
            str(sprint["log"]),
            "--root",
            str(sprint["root"]),
            "--note",
            str(tmp / "note.md"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert gates(sprint)["関門 1"]["by"] == "mvv"
    assert "follow" in (tmp / "note.md").read_text()


@pytest.mark.parametrize(
    "text",
    [
        '{"verdict": "not_follow", "reasons": ["Value 2"], "boundary": []}',
        '{"verdict": "unknown", "reasons": [], "boundary": []}',
        '{"verdict": "follow", "reasons": [], "boundary": ["他のリポジトリへの公開"]}',
        "判定できませんでした",
        '{"verdict": "follow", "reasons": ["Value 1"]}',
        '{"verdict": "follow", "reasons": ["Value 1"], "boundary": ""}',
        '{"verdict": "follow", "reasons": ["Value 1"], "boundary": {}}',
        '{"verdict": "follow", "reasons": ["Value 1"], "boundary": [{}]}',
        '{"verdict": "follow", "reasons": "Value 1", "boundary": []}',
        '{"verdict": "follow", "boundary": []}',
    ],
)
def test_anything_but_a_clean_follow_asks_the_user(sprint, text):
    code, out, rows = run(sprint, text)
    assert (code, out["status"]) == (10, "gate")
    assert rows[-1]["passed"] is False
    assert "関門 2" not in gates(sprint)


def test_without_the_approval_the_llm_is_not_called(sprint):
    st = json.loads(sprint["state"].read_text())
    st["gates"] = []
    sprint["state"].write_text(json.dumps(st))
    code, out, _ = run(sprint, FOLLOW)
    assert (code, llm_calls(sprint)) == (10, 0)


def test_a_changed_mvv_after_the_approval_goes_back_to_the_user(sprint):
    sprint["mvv"].write_text(MVV + "\n4. LLM が足した行\n")
    code, out, _ = run(sprint, FOLLOW)
    assert (code, llm_calls(sprint)) == (10, 0)
    assert "ハッシュ" in out["summary"]


@pytest.mark.parametrize("mode", ["operation", "documentation"])
def test_modes_outside_fast_never_skip_the_gate(sprint, mode):
    code, out, _ = run(sprint, FOLLOW, "--mode", mode)
    assert (code, llm_calls(sprint)) == (10, 0)


@pytest.mark.parametrize("path", ["lib/auth.py", ".github/workflows/ci.yml"])
def test_boundary_paths_never_skip_the_gate(sprint, path):
    code, out, rows = run(sprint, FOLLOW, "--pr", "5", files=["app/x.py", path])
    assert (code, llm_calls(sprint)) == (10, 0)
    assert path in out["summary"]
    assert rows[-1]["verdict"] == "machine"


def test_a_rename_out_of_a_boundary_path_never_skips_the_gate(sprint):
    code, out, rows = run(sprint, FOLLOW, "--pr", "5", files=["app/x.py", ("lib/auth.py", "lib/plain.py")])
    assert (code, llm_calls(sprint)) == (10, 0)
    assert "lib/auth.py → lib/plain.py" in out["summary"] and rows[-1]["verdict"] == "machine"


def test_files_cut_short_of_changed_files_never_skip_the_gate(sprint):
    # REST の files が 3000 件で切れたときの形: 取れた件数が changedFiles に届かない
    code, out, _ = run(sprint, FOLLOW, "--pr", "5", files=["app/x.py"], changed=3001)
    assert (code, llm_calls(sprint)) == (10, 0)
    assert "全件読めない" in out["summary"]


def test_a_missing_material_goes_back_to_the_user(sprint):
    sprint["material"].unlink()
    code, out, _ = run(sprint, FOLLOW)
    assert (code, llm_calls(sprint)) == (10, 0)


DESIGN_DOC = "# 設計\n\nドメインモデルの節\n"


def fake_gh_with_design(tmp_path: Path, files: list[str], api_fails: bool = False) -> str:
    """`gh pr view` は PR の情報を、`gh api .../contents/...` は設計文書の中身を返す偽物。呼び出しを gh-calls.txt へ残す。"""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    info = {"title": "設計", "body": "本文", "headRefOid": "abc123", "changedFiles": len(files)}
    (tmp_path / "info.json").write_text(json.dumps(info, ensure_ascii=False))
    write_rest_files(tmp_path, files)
    (tmp_path / "doc.md").write_text(DESIGN_DOC)
    api = "echo 'HTTP 404' >&2; exit 1" if api_fails else f"cat {tmp_path / 'doc.md'}"
    gh = bindir / "gh"
    gh.write_text(
        f'#!/bin/sh\necho "$*" >> {tmp_path / "gh-calls.txt"}\ncase "$*" in *pulls/*/files*) cat {tmp_path / "files.json"}; exit 0;; esac\nif [ "$1" = api ]; then {api}; exit 0; fi\ncat {tmp_path / "info.json"}\n'
    )
    gh.chmod(0o755)
    return str(bindir)


def run_design(m: dict, files: list[str], gate: str = "design", api_fails: bool = False) -> tuple[int, dict, list[dict]]:
    tmp = m["tmp"]
    env = {
        "PATH": f"{fake_gh_with_design(tmp, files, api_fails)}:/usr/bin:/bin",
        "HOME": str(tmp),
        "NDF_MVV_CLAUDE": fake_claude(tmp, FOLLOW),
    }
    p = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "check",
            "--sprint",
            str(m["state"]),
            "--gate",
            gate,
            "--pr",
            "7",
            "--log",
            str(m["log"]),
            "--root",
            str(m["root"]),
            "--repo",
            "o/r",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=m["root"],
    )
    out = json.loads(p.stdout.splitlines()[-1])
    assert validate_result(out, p.returncode) == [], (out, p.returncode, p.stderr)
    rows = [json.loads(ln) for ln in m["log"].read_text().splitlines()] if m["log"].exists() else []
    return p.returncode, out, rows


def test_design_gate_passes_the_design_doc_of_the_pr_as_material(sprint):
    code, out, rows = run_design(sprint, ["issues/x-design.md", "app/x.py"])
    assert code == 0, out
    assert "ドメインモデルの節" in (sprint["tmp"] / "prompt.txt").read_text()
    assert rows[-1]["material"] == ["#7 issues/x-design.md"]
    api = [ln for ln in (sprint["tmp"] / "gh-calls.txt").read_text().splitlines() if ln.startswith("api") and "/contents/" in ln]
    assert len(api) == 1 and "repos/o/r/contents/issues/x-design.md?ref=abc123" in api[0]


def test_release_gate_does_not_fetch_design_docs(sprint):
    code, out, rows = run_design(sprint, ["issues/x-design.md"], gate="release")
    assert code == 0, out
    assert "ドメインモデルの節" not in (sprint["tmp"] / "prompt.txt").read_text()
    assert rows[-1]["material"] == []


def test_an_unreadable_design_doc_goes_back_to_the_user(sprint):
    code, out, rows = run_design(sprint, ["issues/x-design.md"], api_fails=True)
    assert (code, llm_calls(sprint)) == (10, 0)
    assert "issues/x-design.md" in out["summary"]
    assert rows[-1]["verdict"] == "machine"
