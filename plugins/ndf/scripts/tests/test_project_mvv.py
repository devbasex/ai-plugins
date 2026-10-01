"""プロジェクト MVV（#1366）: 宣言の判定・材料・候補・照合・承認・改訂・兆候と、判断の地点への配線。

claude は NDF_SUPERVISE_CLAUDE（project-mvv.py）と NDF_MVV_CLAUDE（mvv-gate.py）で、gh は PATH の先頭の偽物で差し替える。
差し替えた claude と gh は呼ばれるたびに calls.txt へ 1 行を足す（呼ばないことを数で見る）。
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
REPO = SCRIPTS.parents[2]
MVV_PY = SCRIPTS / "project-mvv.py"
GATE_PY = SCRIPTS / "mvv-gate.py"
STATE_PY = SCRIPTS / "sprint-state.py"
PY = sys.executable
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import project_mvv as pm  # noqa: E402
import project_mvv_decl as pmd  # noqa: E402
import project_mvv_signals as pms  # noqa: E402
from step_result import validate_result  # noqa: E402

BODY = """# 例のプロジェクト MVV

## Mission

利用者の手を減らす。

## Vision

人が止まるのは承認の 2 回だけ。

## Value

1. **まず届ける**: 早く出す。
2. **測って決める**: 実測で示す。

## 必ず人の承認が要る操作（固有のもの）

| # | 操作 | 理由 |
| --- | --- | --- |
| P1 | 配布の規則を変える | 利用者全員に効く |

## 改訂

MVV は進化する。
"""
FOLLOW = '{"verdict": "follow", "locations": []}'


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def env(tmp_path):
    """偽の claude と gh・記録の置き場・git のリポジトリ。"""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.txt"
    gh = bindir / "gh"
    gh.write_text(
        f"#!/bin/sh\necho gh $* >> {calls}\n"
        f'case "$*" in *pulls/*/files*) cat {tmp_path / "gh-files.json"}; exit 0;; esac\n'
        f"cat {tmp_path / 'gh-out.json'} 2>/dev/null || echo '[]'\n"
    )
    gh.chmod(0o755)
    claude = bindir / "claude"
    claude.write_text(
        f"#!/bin/sh\necho claude >> {calls}\ncat > {tmp_path / 'prompt.txt'}\n"
        f'python3 -c \'import json,sys; print(json.dumps({{"result": open(sys.argv[1]).read(), "total_cost_usd": 0.01}}))\' {tmp_path / "answer.txt"}\n'
    )
    claude.chmod(0o755)
    root = tmp_path / "repo"
    root.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    (root / "README.md").write_text("# 例\n\n利用者の手を減らす道具。\n")
    (root / "AGENTS.md").write_text("# 指示\n\n## 方針\n\n測って決める。\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "最初"], cwd=root, check=True)
    e = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "NDF_MVV_STATE_DIR": str(tmp_path / "state"),
        "NDF_SUPERVISE_CLAUDE": str(claude),
        "NDF_MVV_CLAUDE": str(claude),
        "NDF_USAGE_DIR": str(tmp_path / "usage"),
    }
    e.pop("NDF_SV_STATE_DIR", None)
    return {"env": e, "root": root, "tmp": tmp_path, "calls": calls}


def answer(env: dict, text: str) -> None:
    (env["tmp"] / "answer.txt").write_text(text)


def calls(env: dict, what: str = "") -> int:
    f = env["calls"]
    return sum(1 for ln in f.read_text().splitlines() if ln.startswith(what)) if f.exists() else 0


def run(env: dict, *args: str, script: Path = MVV_PY) -> tuple[int, dict, str]:
    p = subprocess.run([PY, str(script), *args], capture_output=True, text=True, env=env["env"], cwd=env["root"], timeout=60)
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
    out = json.loads(lines[-1]) if lines else {}
    if out:
        assert validate_result(out) == [], (out, p.stderr)
    return p.returncode, out, p.stdout + p.stderr


def body_file(env: dict, text: str = BODY, name: str = "body.md") -> str:
    p = env["tmp"] / name
    p.write_text(text)
    return str(p)


def approve(env: dict, text: str = BODY, reason: str | None = None, name: str = "body.md") -> int:
    b = body_file(env, text, name)
    answer(env, FOLLOW)
    code, _, out = run(env, "vet", "--body", b, "--kind", "candidate" if reason is None else "revision", "--root", str(env["root"]))
    assert code == 0, out
    extra = ["--reason", reason] if reason else []
    code, _, out = run(env, "approve", "--body", b, "--by", "利用者", *extra, "--root", str(env["root"]))
    return code


# ---------------------------------------------------------------- check（AC1・AC6）


def test_check_without_a_declaration_is_2_and_calls_nothing_and_writes_nothing(env):
    code, out, _ = run(env, "check", "--root", str(env["root"]))
    assert code == 2 and out["status"] == "stopped" and "プロジェクト MVV が無い" in out["summary"]
    assert calls(env) == 0
    assert not (env["tmp"] / "state").exists() and not (env["root"] / ".ndf").exists()


def test_approved_declaration_is_0_and_a_hand_edit_is_4(env):
    assert approve(env) == 0
    assert (env["root"] / ".ndf" / "mvv.md").read_text() == BODY
    decl = json.loads((env["root"] / ".ndf" / "mvv.json").read_text())
    v = decl["versions"][0]
    assert (v["version"], v["sha256"], v["approved_by"], v["reason"]) == (1, sha(BODY), "利用者", "初版") and v["approved_at"]
    code, out, _ = run(env, "check", "--root", str(env["root"]))
    assert code == 0 and out["metrics"]["version"] == 1
    (env["root"] / ".ndf" / "mvv.md").write_text(BODY + "足した\n")
    code, out, _ = run(env, "check", "--root", str(env["root"]))
    assert code == 4 and "承認と一致しない" in out["summary"]


def test_body_without_the_history_is_unapproved_and_a_broken_history_is_3(env):
    d = env["root"] / ".ndf"
    d.mkdir()
    (d / "mvv.md").write_text(BODY)
    assert run(env, "check", "--root", str(env["root"]))[0] == 4
    (d / "mvv.json").write_text("{")
    assert run(env, "check", "--root", str(env["root"]))[0] == 3


# ---------------------------------------------------------------- collect（AC2・AC3・秘密）


def test_collect_on_a_young_repo_is_trend_mode_and_reads_only_readme_instructions_request(env):
    req = body_file(env, "依頼: 速くする", "request.txt")
    code, out, _ = run(env, "collect", "--root", str(env["root"]), "--request-file", req)
    assert code == 0 and calls(env, "claude") == 0
    mats = json.loads(Path(out["metrics"]["materials"]).read_text())
    assert mats["mode"] == "trend" and mats["counts"]["commits"] == 1
    assert {s["kind"] for s in mats["sources"]} == {"readme", "instructions", "request"}
    assert not Path(out["metrics"]["materials"]).is_relative_to(env["root"])


def test_collect_is_history_mode_when_either_count_reaches_its_threshold(env):
    code, out, _ = run(env, "collect", "--root", str(env["root"]), "--trend-commits", "1")
    mats = json.loads(Path(out["metrics"]["materials"]).read_text())
    assert mats["mode"] == "history" and "commit" in {s["kind"] for s in mats["sources"]}


def test_collect_reads_thresholds_from_the_declaration(env):
    assert approve(env) == 0
    decl_path = env["root"] / ".ndf" / "mvv.json"
    decl = json.loads(decl_path.read_text())
    decl["settings"]["trend_commits"] = 1
    decl_path.write_text(json.dumps(decl, ensure_ascii=False))
    _, out, _ = run(env, "collect", "--root", str(env["root"]))
    assert out["metrics"]["mode"] == "history"


def test_collect_masks_secrets_in_issue_bodies(env):
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/o/r.git"], cwd=env["root"], check=True)
    token = "ghp_" + "a" * 36
    (env["tmp"] / "gh-out.json").write_text(json.dumps([{"number": i, "title": f"課題 {i}", "body": f"token {token}"} for i in range(30)]))
    code, out, _ = run(env, "collect", "--root", str(env["root"]))
    text = Path(out["metrics"]["materials"]).read_text()
    assert code == 0 and out["metrics"]["mode"] == "history" and out["metrics"]["issues"] == 30
    assert token not in text and "（伏せた）" in text
    assert all(ln.split()[1:3] == ["issue", "list"] for ln in env["calls"].read_text().splitlines() if ln.startswith("gh"))


# ---------------------------------------------------------------- propose（AC4・AC18）


def candidate(cid: str, ev: str = "S1") -> dict:
    return {
        "id": cid,
        "recommended": cid == "A",
        "mission": {"text": "手を減らす", "evidence": [ev]},
        "vision": {"text": "2 回だけ止まる", "evidence": [ev]},
        "values": [{"title": "まず届ける", "text": "早く出す", "evidence": [ev]}],
        "redlines": [{"id": "P1", "text": "配布の規則を変える", "reason": "全員に効く", "evidence": [ev]}],
    }


def proposal(cands: list[dict], questions=True) -> str:
    return json.dumps(
        {
            "context": "MVV が無い",
            "premise": "判断の基準が決まる",
            "evidence": "README と指示書",
            "options": "A と B",
            "glossary": [{"term": "MVV", "meaning": "Mission / Vision / Value"}],
            "candidates": cands,
            "questions": [{"question": "速さと確かさのどちらを先にするか", "options": [{"label": "速さ", "effect": "A"}]}]
            if questions
            else [],
        },
        ensure_ascii=False,
    )


def materials(env: dict) -> str:
    _, out, _ = run(env, "collect", "--root", str(env["root"]))
    return out["metrics"]["materials"]


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda: proposal([candidate("A")]), id="one"),
        pytest.param(lambda: proposal([candidate("A"), candidate("B", "S999")]), id="unknown-evidence"),
        pytest.param(lambda: proposal([candidate("A"), candidate("B")], questions=False), id="no-questions"),
        pytest.param(
            lambda: proposal([candidate("A"), candidate("B")]).replace('"context": "MVV が無い"', '"context": ""'), id="no-context"
        ),
        pytest.param(lambda: "候補は書けない", id="not-json"),
    ],
)
def test_propose_stops_with_the_materials_when_the_shape_is_short(env, bad):
    mats = materials(env)
    answer(env, bad())
    code, out, _ = run(env, "propose", "--materials", mats, "--root", str(env["root"]))
    assert code == 1 and out["items"][0]["name"] == mats and not (Path(mats).parent / "candidates.md").exists()


def test_propose_writes_candidates_with_the_four_sections_first(env):
    mats = materials(env)
    answer(env, proposal([candidate("A"), candidate("B")]))
    code, out, _ = run(env, "propose", "--materials", mats, "--root", str(env["root"]))
    assert code == 0 and calls(env, "claude") == 1
    md = Path(out["metrics"]["candidates_md"]).read_text()
    heads = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert heads[:4] == ["## 経緯", "## 前提", "## 根拠", "## 選択肢"]
    assert "## 利用者に決めてもらう点" in heads and "S1（README.md:1）" in md
    prompt = (env["tmp"] / "prompt.txt").read_text()
    assert prompt.startswith(pm.principles()) and pm.contract() in prompt
    body = Path(out["items"][0]["body"]).read_text()
    assert pm.shape_problems(body) == [] and "P1" in pm.item_ids(body)
    assert not (env["root"] / ".ndf").exists()  # 承認の前に .ndf/ へ書かない（AC5）


def _proposal(cands, **over) -> dict:
    return {**json.loads(proposal(cands)), **over}


def _without(c: dict, key: str) -> dict:
    return {k: v for k, v in c.items() if k != key}


_A, _B = candidate("A"), candidate("B")
PROBLEM_CASES = {
    "ok": (lambda: _proposal([_A, _B]), []),
    "not-an-object": (lambda: [1], ["JSON のオブジェクトでない"]),
    "none": (lambda: None, ["JSON のオブジェクトでない"]),
    "four-sections": (
        lambda: _proposal([_A, _B], context="  ", premise=None, evidence=3, options=""),
        ["経緯（context）が無い", "前提（premise）が無い", "根拠（evidence）が無い", "選択肢（options）が無い"],
    ),
    "candidates-not-a-list": (lambda: _proposal("x"), ["候補が 2 案以上ない"]),
    "candidates-missing": (lambda: _without(_proposal([_A, _B]), "candidates"), ["候補が 2 案以上ない"]),
    "one-candidate": (lambda: _proposal([_A]), ["候補が 2 案以上ない"]),
    "candidate-not-an-object": (lambda: _proposal([_A, "x", _B]), ["候補 2: オブジェクトでない"]),
    # id が無い候補は並びの番号で呼ぶ
    "no-id": (lambda: _proposal([_A, _without(_without(_B, "id"), "mission")]), ["候補 2: Mission の本文が無い"]),
    "values-empty": (lambda: _proposal([_A, {**_B, "values": []}]), ["候補 B: Value が無い"]),
    "values-not-a-list": (lambda: _proposal([_A, {**_B, "values": "x"}]), ["候補 B: Value が無い"]),
    "redlines-missing": (lambda: _proposal([_A, _without(_B, "redlines")]), ["候補 B: レッドライン（redlines）が無い"]),
    # レッドラインは空の配列でよい
    "redlines-empty": (lambda: _proposal([_A, {**_B, "redlines": []}]), []),
    "redline-not-an-object": (lambda: _proposal([_A, {**_B, "redlines": ["x"]}]), ["候補 B: P の本文が無い"]),
    "redline-without-id": (lambda: _proposal([_A, {**_B, "redlines": [{"text": "t", "evidence": []}]}]), ["候補 B: P の根拠が無い"]),
    "vision-blank": (lambda: _proposal([_A, {**_B, "vision": {"text": "  ", "evidence": ["S1"]}}]), ["候補 B: Vision の本文が無い"]),
    "evidence-shapes": (
        lambda: _proposal([_A, {**_B, "values": [{"text": "t"}, {"text": "u", "evidence": "S1"}, {"text": "v", "evidence": []}]}]),
        ["候補 B: Value 1 の根拠が無い", "候補 B: Value 2 の根拠が無い", "候補 B: Value 3 の根拠が無い"],
    ),
    "unknown-evidence": (
        lambda: _proposal([_A, {**_B, "mission": {"text": "t", "evidence": ["S1", "S9", 7]}}]),
        ["候補 B: Mission の根拠が材料に無い ID を指す（S9, 7）"],
    ),
    "questions-missing": (lambda: _without(_proposal([_A, _B]), "questions"), ["利用者に決めてもらう点（questions）が無い"]),
    "questions-empty": (lambda: _proposal([_A, _B], questions=[]), ["利用者に決めてもらう点（questions）が無い"]),
    "questions-blank": (lambda: _proposal([_A, _B], questions=["x", {"question": " "}]), ["利用者に決めてもらう点（questions）が無い"]),
    # 並びの順: 4 節 → 案の数 → 候補ごと（Value と redlines の有無 → Mission・Vision・Value・P の本文と根拠）→ questions
    "order": (
        lambda: _proposal(
            [
                {**_A, "values": None, "redlines": None, "mission": None},
                {
                    **_B,
                    "mission": {"text": "t"},
                    "values": [{"text": ""}],
                    "redlines": ["x", {"id": "P2", "text": "t", "evidence": ["S8"]}],
                },
                7,
            ],
            context="",
            questions=None,
        ),
        [
            "経緯（context）が無い",
            "候補 A: Value が無い",
            "候補 A: レッドライン（redlines）が無い",
            "候補 A: Mission の本文が無い",
            "候補 B: Mission の根拠が無い",
            "候補 B: Value 1 の本文が無い",
            "候補 B: P の本文が無い",
            "候補 B: P2 の根拠が材料に無い ID を指す（S8）",
            "候補 3: オブジェクトでない",
            "利用者に決めてもらう点（questions）が無い",
        ],
    ),
}


@pytest.mark.parametrize("name", list(PROBLEM_CASES))
def test_candidate_problems_names_each_missing_part(name):
    """現状固定: 候補の形の誤りは、この文面とこの並びで返る。"""
    from project_lib import mvv_candidates as mc

    make, errs = PROBLEM_CASES[name]
    assert mc.candidate_problems(make(), {"S1", "S2"}) == errs


# ---------------------------------------------------------------- vet と approve（AC5・AC16・AC19・I4・決定 6）


def test_approve_without_a_vet_writes_nothing(env):
    code, out, _ = run(env, "approve", "--body", body_file(env), "--by", "u", "--root", str(env["root"]))
    assert code == 1 and "照合の記録が無い" in out["summary"]
    assert not (env["root"] / ".ndf" / "mvv.md").exists() and not (env["root"] / ".ndf" / "mvv.json").exists()


def test_a_suspect_body_cannot_be_approved_and_the_body_is_unchanged(env):
    b = body_file(env)
    answer(env, '{"verdict": "suspect", "locations": [{"item": "C4", "reason": "強制 push を承認なしで行えると書く"}]}')
    code, out, _ = run(env, "vet", "--body", b, "--kind", "candidate", "--root", str(env["root"]))
    assert code == 10 and out["items"][0]["name"] == "C4"
    assert Path(b).read_text() == BODY
    code, _, _ = run(env, "approve", "--body", b, "--by", "u", "--root", str(env["root"]))
    assert code == 1 and not (env["root"] / ".ndf").exists()


def test_unknown_needs_accept_unknown_and_is_recorded(env):
    b = body_file(env)
    answer(env, '{"verdict": "unknown", "locations": []}')
    assert run(env, "vet", "--body", b, "--kind", "candidate", "--root", str(env["root"]))[0] == 10
    assert run(env, "approve", "--body", b, "--by", "u", "--root", str(env["root"]))[0] == 1
    assert run(env, "approve", "--body", b, "--by", "u", "--accept-unknown", "--root", str(env["root"]))[0] == 0
    v = json.loads((env["root"] / ".ndf" / "mvv.json").read_text())["versions"][0]
    assert v["vet"]["verdict"] == "unknown" and v["vet"]["accepted_unknown"] is True


def test_a_body_changed_after_the_vet_cannot_be_approved(env):
    b = body_file(env)
    answer(env, FOLLOW)
    assert run(env, "vet", "--body", b, "--kind", "candidate", "--root", str(env["root"]))[0] == 0
    Path(b).write_text(BODY + "後から足した\n")
    assert run(env, "approve", "--body", b, "--by", "u", "--root", str(env["root"]))[0] == 1


@pytest.mark.parametrize(
    "text,item",
    [
        (BODY.replace("| P1 |", "| C4 |"), "C4"),
        (BODY + "\n## 上位の原則\n\n人類を守る。\n", "principle"),
        (BODY + "\n## 優先順位\n\n効率 > 承認\n", "priority"),
        (BODY.replace("## Vision", "## 目指す姿"), "Vision"),
    ],
)
def test_copies_of_the_common_principles_and_missing_headings_stop_without_the_llm(env, text, item):
    b = body_file(env, text)
    code, out, _ = run(env, "vet", "--body", b, "--kind", "candidate", "--root", str(env["root"]))
    assert code == 10 and item in [i["name"] for i in out["items"]] and calls(env, "claude") == 0
    assert run(env, "approve", "--body", b, "--by", "u", "--root", str(env["root"]))[0] == 1


def test_the_approved_ai_plugins_text_has_the_expected_shape():
    text = (REPO / "issues" / "mvv-ai-plugins.md").read_text(encoding="utf-8")
    assert pm.shape_problems(text) == []
    assert pm.item_ids(text) == ["Mission", "Vision", *[f"Value {i}" for i in range(1, 9)], "P1"]


def test_vet_prompt_puts_the_common_principles_first_and_the_contract(env):
    answer(env, FOLLOW)
    run(env, "vet", "--body", body_file(env), "--kind", "candidate", "--root", str(env["root"]))
    prompt = (env["tmp"] / "prompt.txt").read_text()
    assert prompt.startswith(pm.principles()) and pm.contract() in prompt and "利用者の手を減らす" in prompt


# ---------------------------------------------------------------- 改訂（AC10・I3）


def test_revision_keeps_the_old_version_and_records_reason_and_changes(env):
    assert approve(env) == 0
    v2 = BODY.replace("2. **測って決める**: 実測で示す。", "2. **測って決める**: 実測で示す。\n3. **理由を残す**: 記録する。")
    assert run(env, "approve", "--body", body_file(env, v2, "v2.md"), "--by", "u", "--root", str(env["root"]))[0] == 1  # 照合なし
    b = body_file(env, v2, "v2.md")
    answer(env, FOLLOW)
    assert run(env, "vet", "--body", b, "--kind", "revision", "--root", str(env["root"]))[0] == 0
    assert run(env, "approve", "--body", b, "--by", "u", "--root", str(env["root"]))[0] == 1  # 理由なし
    assert run(env, "approve", "--body", b, "--by", "u", "--reason", "Value を足す", "--root", str(env["root"]))[0] == 0
    decl = json.loads((env["root"] / ".ndf" / "mvv.json").read_text())
    assert [v["version"] for v in decl["versions"]] == [1, 2]
    assert decl["versions"][0]["body"] == BODY and decl["versions"][1]["changes"] == [{"item": "Value 3", "kind": "added"}]
    assert decl["versions"][1]["reason"] == "Value を足す"
    code, out, text = run(env, "show", "--version", "1", "--root", str(env["root"]))
    assert code == 0 and "3. **理由を残す**" not in text and "2. **測って決める**" in text
    code, out, text = run(env, "show", "--diff", "1", "--root", str(env["root"]))
    assert code == 0 and "+3. **理由を残す**" in text
    assert run(env, "show", "--version", "5", "--root", str(env["root"]))[0] == 2


def test_the_same_body_cannot_be_a_new_version(env):
    assert approve(env) == 0
    b = body_file(env)
    answer(env, FOLLOW)
    run(env, "vet", "--body", b, "--kind", "revision", "--root", str(env["root"]))
    code, out, _ = run(env, "approve", "--body", b, "--by", "u", "--reason", "同じ", "--root", str(env["root"]))
    assert code == 1 and "同じ" in out["summary"]


def test_a_failed_history_write_restores_the_old_body(env, monkeypatch):
    assert approve(env) == 0
    body_path, decl_path = pm.decl_paths(env["root"])
    old_body, old_decl = body_path.read_text(), decl_path.read_text()
    real = Path.replace

    def failing(self, target):
        if Path(target).name == "mvv.json":
            raise OSError("書けない")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", failing)
    with pytest.raises(OSError):
        pmd.write_decl(body_path, BODY + "\n追記\n", decl_path, {"version": 1})
    assert body_path.read_text() == old_body and decl_path.read_text() == old_decl
    assert sorted(p.name for p in body_path.parent.iterdir()) == ["mvv.json", "mvv.md"]
    assert pm.load_mvv(env["root"]).status == "approved"


def test_load_mvv_at_ref_reads_the_base_and_ignores_the_head_edit(env):
    root = env["root"]
    assert pm.load_mvv_at_ref(root, ["HEAD"]).status == "none"
    assert approve(env) == 0
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "MVV"], cwd=root, check=True)
    body_path, _ = pm.decl_paths(root)
    body_path.write_text(BODY.replace("実測で示す", "変更者の都合で決める"))
    assert pm.load_mvv(root).status == "mismatch"
    base = pm.load_mvv_at_ref(root, ["origin/nothing", "HEAD"])
    assert base.status == "approved" and base.body == BODY
    missing = pm.load_mvv_at_ref(root, ["origin/nothing"])
    assert missing.status == "unreadable" and "origin/nothing" in missing.error


# ---------------------------------------------------------------- 節と根拠（AC8・AC9・AC16・I5・I7・I16）


def test_block_starts_with_the_whole_common_principles_even_without_a_declaration(tmp_path):
    none = pm.load_mvv(tmp_path)
    text = pm.block(none)
    assert none.status == "none" and text.startswith(pm.principles()) and pm.contract() in text and pm.NO_MVV in text
    assert "C8" in text and "人を守り、人の発展を支える" in text


def test_block_orders_principles_project_sprint_contract(env):
    assert approve(env) == 0
    mvv = pm.load_mvv(env["root"])
    text = pm.block(mvv, "## Mission\nミッション\n")
    i = [text.index(x) for x in ("# NDF の共通原則", "利用者の手を減らす", "# スプリント MVV", "## 判断の決まり")]
    assert i == sorted(i) and "版 1" in text


def test_basis_normalizes_drops_unknown_and_fills_the_empty(env):
    none = pm.load_mvv(env["root"])
    assert pm.basis(None, none) == [pm.NO_MVV]
    assert pm.basis(["c4", "Value 3"], none) == ["C4"]
    assert approve(env) == 0
    mvv = pm.load_mvv(env["root"])
    assert pm.basis(["value2（測って決める）", "P1", "P9", "Mission"], mvv) == ["Value 2", "P1", "Mission"]
    assert pm.basis([], mvv) == [pm.NO_BASIS]
    assert pm.basis(["R2"], mvv, ["R2"]) == ["R2"]
    assert pm.basis_phrase(["Value 1"], mvv) == "根拠: Value 1（MVV 版 1）"
    assert pm.basis_phrase([pm.NO_MVV], none) == f"（{pm.NO_MVV}）"


def test_context_prints_the_block(env):
    p = subprocess.run([PY, str(MVV_PY), "context", "--root", str(env["root"])], capture_output=True, text=True, env=env["env"])
    assert p.returncode == 0 and p.stdout.startswith(pm.principles())


# ---------------------------------------------------------------- 兆候（AC11・AC17・I13・I18）


def gate_row(sha_: str | None, verdict: str, at: str = "2999-01-01T00:00:00Z", repo: str | None = None) -> dict:
    return {"at": at, "verdict": verdict, "project_mvv": {"status": "approved", "version": 1, "sha256": sha_}, "repo": repo}


def test_unknown_streak_counts_only_the_same_sha_and_resets():
    rows = [gate_row("a", "unknown"), gate_row("b", "unknown"), gate_row("b", "machine"), gate_row("b", "unknown")]
    assert pms.unknown_streak(rows, "b") == 2
    assert pms.unknown_streak(rows + [gate_row("b", "follow")], "b") == 0
    assert pms.unknown_streak(rows, "a") == 0


def test_signals_count_after_the_current_version_and_suggest_revision(env):
    assert approve(env) == 0
    mvv = pm.load_mvv(env["root"])
    key = pm.mvv_repo_key(env["root"])
    sig_log = env["tmp"] / "state" / "project-mvv-signals.jsonl"
    old = {"at": "2000-01-01T00:00:00Z", "repo": key, "kind": "override_reject", "project_sha256": mvv.sha256}
    new = {"at": "2999-01-01T00:00:00Z", "repo": key, "kind": "override_pass", "project_sha256": mvv.sha256}
    for r in [old, new, new, new]:
        pms.append_jsonl(sig_log, r)
    sig = pms.signals(env["root"], mvv, signals_log=sig_log, gate_log=env["tmp"] / "none.jsonl")
    assert sig["counts"]["overrides"] == 3 and sig["counts"]["override_pass"] == 3 and sig["over"] == ["overrides"]
    code, out, _ = run(env, "signals", "--root", str(env["root"]), "--format", "json")
    assert code == 0 and out["items"][0]["kind"] == "revise"


def test_signals_without_a_declaration_is_0(env):
    code, out, _ = run(env, "signals", "--root", str(env["root"]), "--format", "json")
    assert code == 0 and out["items"] == []


# ---------------------------------------------------------------- sprint-state（AC12・AC17・I11・I18）


def sprint_gate(env, state: Path, *args: str) -> int:
    return run(env, "gate", str(state), "関門 2", "--what", "本番", "--root", str(env["root"]), *args, script=STATE_PY)[0]


def signal_rows(env) -> list[dict]:
    return pms.read_jsonl(env["tmp"] / "state" / "project-mvv-signals.jsonl")


def new_state(env, tmp: Path) -> Path:
    state = tmp / "m.json"
    code, out, text = run(env, "init", str(state), "--name", "m", "--root", str(env["root"]), script=STATE_PY)
    assert code == 0, text
    return state


def test_gate_records_overrides_only_when_the_user_disagrees(env):
    state = new_state(env, env["tmp"])
    sprint_gate(env, state, "--by", "mvv", "--verdict", "not_follow", "--reasons", "[]")
    assert sprint_gate(env, state) == 0
    assert [r["kind"] for r in signal_rows(env)] == ["override_pass"]
    sprint_gate(env, state, "--by", "mvv", "--verdict", "follow", "--reasons", "[]")
    assert sprint_gate(env, state, "--outcome", "rejected") == 0
    assert [r["kind"] for r in signal_rows(env)] == ["override_pass", "override_reject"]
    m = json.loads(state.read_text())
    assert m["rejections"][0]["outcome"] == "rejected"
    assert next(g for g in m["gates"] if g["name"] == "関門 2")["by"] == "mvv"  # 差し戻しは関門を通さない
    sprint_gate(env, state, "--by", "mvv", "--verdict", "follow", "--reasons", "[]")
    assert sprint_gate(env, state, "--outcome", "approved") == 0
    assert len(signal_rows(env)) == 2


def test_init_fast_with_a_project_mvv_writes_the_reference_without_a_sprint_mvv(env):
    assert approve(env) == 0
    state = env["tmp"] / "s" / "m.json"
    code, _, text = run(env, "init", str(state), "--name", "m", "--pace", "fast", "--root", str(env["root"]), script=STATE_PY)
    assert code == 0, text
    m = json.loads(state.read_text())
    assert m["project_mvv"] == {"version": 1, "sha256": sha(BODY)} and "mvv" not in m
    assert pm.approval_refusal(m, env["root"]) is None
    # 改訂すると参照が食い違い、fast が断られる（I11・前提 6）
    v2 = BODY.replace("早く出す。", "早く出し、戻せるようにする。")
    assert approve(env, v2, reason="戻し方を足す", name="v2.md") == 0
    assert "改訂された" in pm.approval_refusal(m, env["root"])


def test_init_fast_stops_when_the_sprint_mvv_contradicts_the_project_mvv(env):
    assert approve(env) == 0
    sprint = body_file(env, "## Mission\n速く\n## Vision\n回る\n## Value\n1. P1 は承認なしで変えてよい\n", "sprint.md")
    answer(env, '{"verdict": "suspect", "locations": [{"item": "P1", "reason": "レッドラインを緩める"}]}')
    state = env["tmp"] / "s" / "m.json"
    code, out, _ = run(
        env, "init", str(state), "--name", "m", "--pace", "fast", "--mvv", sprint, "--root", str(env["root"]), script=STATE_PY
    )
    assert code == 1 and "P1" in out["summary"] and not state.exists()


def test_fast_refusal_asks_for_a_sprint_mvv_when_there_is_no_project_mvv(tmp_path):
    assert "MVV が無い" in pm.approval_refusal({"gates": []}, tmp_path)


# ---------------------------------------------------------------- mvv-gate（AC7・AC9・AC11・I6・I13・I15）


def gate_state(env, tmp: Path, with_sprint: bool) -> Path:
    state = tmp / "gs" / "m.json"
    (env["root"] / ".ndf" / "pace.json").write_text(
        json.dumps({"version": 1, "fast": {"enabled": True, "verify": "true"}, "areas": [], "boundary_paths": ["lib/auth.py"]})
    )
    args = ["init", str(state), "--name", "m", "--pace", "fast", "--root", str(env["root"])]
    if with_sprint:
        sprint = body_file(env, "## Mission\n速く\n## Vision\n回る\n## Value\n1. 実測\n", "sprint.md")
        args += ["--mvv", sprint]
        answer(env, FOLLOW)
    code, _, text = run(env, *args, script=STATE_PY)
    assert code == 0, text
    if with_sprint:
        run(env, "gate", str(state), "MVV", "--what", "承認", script=STATE_PY)
    return state


def gate_check(env, state: Path, *extra: str) -> tuple[int, dict, list[dict]]:
    material = env["tmp"] / "approval.md"
    material.write_text("# 配布\n")
    log = env["tmp"] / "gate.jsonl"
    code, out, text = run(
        env,
        "check",
        "--sprint",
        str(state),
        "--gate",
        "release",
        "--material",
        str(material),
        "--log",
        str(log),
        "--root",
        str(env["root"]),
        *extra,
        script=GATE_PY,
    )
    return code, out, pms.read_jsonl(log)


def test_mvv_gate_judges_with_only_the_project_mvv_and_records_the_basis(env):
    assert approve(env) == 0
    state = gate_state(env, env["tmp"], with_sprint=False)
    answer(env, '{"verdict": "follow", "reasons": ["Value 1"], "boundary": [], "basis": ["Value 1", "Z9"]}')
    before = calls(env, "claude")
    code, out, rows = gate_check(env, state)
    assert code == 0 and calls(env, "claude") == before + 1
    assert rows[-1]["project_mvv"] == {"status": "approved", "version": 1, "sha256": sha(BODY)} and rows[-1]["basis"] == ["Value 1"]
    prompt = (env["tmp"] / "prompt.txt").read_text()
    assert prompt.startswith(pm.principles()) and "利用者の手を減らす" in prompt and pm.contract() in prompt


def test_mvv_gate_goes_back_without_the_llm_when_the_declaration_does_not_match(env):
    assert approve(env) == 0
    state = gate_state(env, env["tmp"], with_sprint=True)
    (env["root"] / ".ndf" / "mvv.md").write_text(BODY + "足した\n")
    before = calls(env, "claude")
    code, out, rows = gate_check(env, state)
    assert code == 10 and calls(env, "claude") == before and rows[-1]["verdict"] == "machine"


def test_mvv_gate_never_passes_a_pr_that_changes_the_declaration(env):
    assert approve(env) == 0
    state = gate_state(env, env["tmp"], with_sprint=True)
    # REST の `pulls/<n>/files` の形（変更したファイルは `gh pr view --json files` でなく REST の全件で読む）
    (env["tmp"] / "gh-out.json").write_text(json.dumps({"title": "t", "body": "b", "changedFiles": 1}))
    (env["tmp"] / "gh-files.json").write_text(
        json.dumps([{"filename": ".ndf/mvv.md", "status": "modified", "additions": 1, "deletions": 0}])
    )
    before = calls(env, "claude")
    code, out, rows = gate_check(env, state, "--pr", "5")
    assert code == 10 and calls(env, "claude") == before and "C7" in rows[-1]["reasons"][0]


def test_mvv_gate_suggests_revision_after_the_unknown_streak(env):
    assert approve(env) == 0
    state = gate_state(env, env["tmp"], with_sprint=False)
    answer(env, '{"verdict": "unknown", "reasons": ["決められない"], "boundary": []}')
    outs = [gate_check(env, state)[1] for _ in range(3)]
    assert [any(i.get("kind") == "revise" for i in o["items"]) for o in outs] == [False, False, True]
    assert gate_check(env, state)[2][-1]["basis"] == [pm.NO_BASIS]


# ---------------------------------------------------------------- スキーマと既定（AC14）


def test_published_schema_matches_the_model(env):
    published = SCRIPTS.parent / "skills" / "development-workflow" / "schemas" / "mvv.schema.json"
    p = subprocess.run([PY, str(MVV_PY), "schema"], capture_output=True, text=True, env=env["env"])
    assert published.read_text(encoding="utf-8") == p.stdout


def test_the_written_declaration_passes_the_pydantic_model(env):
    import deps

    deps.require("schema")
    import schema

    assert approve(env) == 0
    decl, _ = pmd.decl_models()
    schema.load_shape(decl, json.loads((env["root"] / ".ndf" / "mvv.json").read_text()))


def test_defaults_hold_no_repository_specific_values():
    src = (SCRIPTS / "lib" / "project_mvv.py").read_text() + MVV_PY.read_text()
    assert "ai-plugins" not in src and "devbasex" not in src
    assert pm.DEFAULTS["trend_commits"] == 100 and pm.DEFAULTS["revise_after"] == {"overrides": 3, "unknowns": 5, "escapes": 3}


# ---------------------------------------------------------------- cross-review・fix・supervise の judge（AC8・AC9）


def test_review_state_carries_the_reference_and_the_block(env):
    import review_criteria as rc

    assert approve(env) == 0
    mvv = pm.load_mvv(env["root"])
    st = rc.as_state(rc.NO_FOCUS, mvv)
    assert st["project_mvv"] == pm.record(mvv) and st["mvv_block"] == pm.block(mvv)
    assert pm.principles() in st["reviewer_block"] and "利用者の手を減らす" in st["reviewer_block"]
    fixer = rc.fixer_block(rc.NO_FOCUS, st["mvv_block"])
    assert pm.contract() in fixer and rc.MVV_FIXER in fixer
    assert rc.reviewer_block(rc.NO_FOCUS) == rc.reviewer_block()  # 省けば今の出力と同じ
    assert rc.waiver_reply("wording", (), "根拠: Value 1（MVV 版 1）").endswith("根拠: Value 1（MVV 版 1）")


def test_fix_finalize_records_the_basis_and_the_reply_phrase(env):
    import importlib.util

    spec = importlib.util.spec_from_file_location("fix_steps_mvv", SCRIPTS.parent / "skills" / "fix" / "scripts" / "fix-steps.py")
    fs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fs)
    d = {
        "project_mvv": {"status": "approved", "version": 2, "sha256": "x"},
        "decisions": [
            {"thread_id": "T1", "decision": "waived", "waive_kind": "wording", "severity": "nit", "mvv_basis": ["value 1"]},
            {"thread_id": "T2", "decision": "fixed", "severity": "major"},
        ],
    }
    res = fs.build_result(1, d, "abc", "SUCCESS", [])
    assert res["deferred"][0]["mvv_basis"] == ["Value 1"] and res["deferred"][0]["reply"].endswith("根拠: Value 1（MVV 版 2）")
    assert res["resolved_threads"][0]["mvv_basis"] == [pm.NO_BASIS] and res["project_mvv"]["version"] == 2


def test_judge_gets_the_block_and_records_the_basis(env, monkeypatch):
    from supervise_lib import engine

    assert approve(env) == 0
    fake = env["tmp"] / "judge.sh"
    fake.write_text(
        f"#!/bin/sh\ncat > {env['tmp'] / 'judge-prompt.txt'}\n"
        """echo '{"result": "{\\"decision\\": \\"stop\\", \\"reason\\": \\"x\\", \\"basis\\": [\\"Value 2\\"]}", "usage": {}}'\n"""
    )
    fake.chmod(0o755)
    for k in ("NDF_MVV_STATE_DIR", "NDF_USAGE_DIR", "HOME"):
        monkeypatch.setenv(k, env["env"][k])
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", str(fake))
    plan = {
        "フェーズ": "試験",
        "課題": [1],
        "作業場所": str(env["root"]),
        "steps": [{"id": "j", "type": "judge", "question": "?", "choices": ["stop"]}],
    }
    s = engine.Engine(plan, env["tmp"] / "sv")
    s.run()
    state = json.loads((env["tmp"] / "sv" / "state.json").read_text())
    assert state["project_mvv"] == {"status": "approved", "version": 1, "sha256": sha(BODY)}
    assert state["log"][-1]["basis"] == ["Value 2"]
    assert (env["tmp"] / "judge-prompt.txt").read_text().startswith(pm.principles())
