"""工程から、そのステップに要る Skill だけを worker へ届ける（#2376）。

対応表（`lib/stage-skills.json`）・解決（`lib/stage_skills.py`）・引くコマンド（`stage-skills.py`）・プランの検査・
進捗ログの `kind: skills` の行・worker の起動の組み立てを、`claude` を起動せずに見る。
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import stage_skills  # noqa: E402
from supervise_lib import claude as claude_mod, engine, worker_steps  # noqa: E402
from supervise_lib.pr import PrStep  # noqa: E402
from supervise_lib.prompts import FULL_SYSTEM, JUDGE_SYSTEM, PR_SYSTEM, WORK_SYSTEM  # noqa: E402
from supervise_lib.state import RunState  # noqa: E402
from supervise_lib.steps import JudgeStep  # noqa: E402

NDF = SCRIPTS.parent
WF = NDF / "skills" / "development-workflow"
TABLE = stage_skills.load_table()
RULES = TABLE["rule_skills"]
DOC_STAGES = ["要求と受け入れ条件", "設計", "ドキュメント再構成", "ドキュメントレビュー", "計画", "完了判定", "Pull Request", "確定仕様化", "振り返り", "棚卸し"]


def _lookup_module():
    spec = importlib.util.spec_from_file_location("stage_skills_cli", SCRIPTS / "stage-skills.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def body_of(name: str) -> str:
    return stage_skills.FRONTMATTER.sub("", stage_skills.skill_path(name).read_text(encoding="utf-8"), 1).strip()


class FakeClaude:
    def __init__(self, texts=()):
        self.texts, self.calls = list(texts), []

    def call(self, system, prompt, tools, cwd, timeout, **kw):
        self.calls.append({"system": system, "prompt": prompt, **kw})
        text = self.texts.pop(0) if self.texts else "## 作業の報告\n- 結果: 完了"
        return {"ok": True, "text": text, "seconds": 0, "session": "s1", "usage": {}}

    def record_usage(self, kind, res):
        pass


def ctx_of(tmp_path: Path, texts=()) -> SimpleNamespace:
    plan = {"作業場所": str(tmp_path), "課題": []}
    state = RunState(tmp_path / "sv", plan)
    state.cur = {"id": "w"}
    return SimpleNamespace(plan=plan, cwd=str(tmp_path), state=state, claude=FakeClaude(texts), inputs_text=lambda s: "", tick=lambda: None)


def skill_rows(ctx) -> list[dict]:
    lines = ctx.state.progress.read_text().splitlines() if ctx.state.progress.is_file() else []
    return [r for r in map(json.loads, lines) if r["kind"] == "skills"]


@pytest.fixture
def broken_table(tmp_path, monkeypatch):
    bad = tmp_path / "broken.json"
    bad.write_text("{")
    monkeypatch.setattr(stage_skills, "TABLE", bad)
    return bad


# ---------------------------------------------------------------- 対応表（受け入れ条件 1・2・I1・I2）


def test_rule_skill_names_live_only_in_the_table():
    """I1: 届ける経路のファイルは規則の Skill の名前を持たない。"""
    paths = [*sorted((SCRIPTS / "supervise_lib").glob("*.py")), SCRIPTS / "lib" / "stage_skills.py", SCRIPTS / "stage-skills.py"]
    paths += [NDF / "agents" / "worker.md", WF / "references" / "agent-layers.md"]
    hits = [(p.name, n) for p in paths for n in RULES if n in p.read_text(encoding="utf-8")]
    assert hits == []


def test_the_table_keys_are_stages_and_values_are_existing_rule_skills():
    """I2: キーは工程表の行名、値は rule_skills に載り SKILL.md が実在する。rule_skills に工程 Skill が無い。"""
    stages = subprocess.run(
        ["bash", "-c", f'source "{WF}/scripts/lib/workflow-common.sh"; wf_stages'], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    assert set(TABLE["stages"]) <= {s for s in stages if s}
    assert all(n in RULES and stage_skills.skill_path(n).is_file() for v in TABLE["stages"].values() for n in v)
    text = WF.joinpath("SKILL.md").read_text(encoding="utf-8")
    table = text.split("## モードごとに起動する Skill", 1)[1].split("\n\n**", 1)[0]
    stage_skills_named = {m for m in re.findall(r"`([a-z][a-z0-9-]+)`", table) if (NDF / "skills" / m).is_dir()}
    assert stage_skills_named and not stage_skills_named & set(RULES)


def test_a_table_value_that_is_not_a_rule_skill_is_unreadable(tmp_path):
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"version": 1, "rule_skills": ["document-style"], "stages": {"設計": ["development-workflow"]}}))
    with pytest.raises(stage_skills.SkillsError, match="development-workflow"):
        stage_skills.load_table(f)


@pytest.mark.parametrize("stage", DOC_STAGES)
def test_document_writing_stages_get_document_style(stage):
    """受け入れ条件 13・決定 9: 他者が読む文書を書く 10 工程に document-style が届く。"""
    assert "document-style" in stage_skills.resolve({"stage": stage}, TABLE)


@pytest.mark.parametrize("step", [{}, {"stage": None}, {"stage": "無い工程"}, {"stage": "配布"}])
def test_steps_without_a_table_row_get_nothing(step, tmp_path):
    """受け入れ条件 2: stage が無い・対応表に無い工程は空で、本文の節が入らない。"""
    assert stage_skills.resolve(step, TABLE) == []
    ctx = ctx_of(tmp_path)
    worker_steps.WorkStep().execute(ctx, {"id": "w", "prompt": "直す", **step})
    assert ctx.claude.calls[-1]["system"] == WORK_SYSTEM


# ---------------------------------------------------------------- skills・skills_add（受け入れ条件 3〜6・I3・I4）


def test_skills_replaces_and_an_empty_list_delivers_nothing():
    assert stage_skills.resolve({"stage": "設計", "skills": ["logging-guidelines"]}, TABLE) == ["logging-guidelines"]
    assert stage_skills.resolve({"stage": "設計", "skills": []}, TABLE) == []


def test_skills_add_appends_without_duplicates():
    assert stage_skills.resolve({"stage": "設計", "skills_add": ["logging-guidelines", "document-style"]}, TABLE) == [
        "document-style",
        "logging-guidelines",
    ]
    step = {"stage": "設計", "skills": ["logging-guidelines"], "skills_add": ["document-style", "logging-guidelines"]}
    assert stage_skills.resolve(step, TABLE) == ["logging-guidelines", "document-style"]


def test_plan_errors_name_the_step_the_skill_and_the_reason():
    steps = [
        {"id": "a", "skills": ["development-workflow"]},
        {"id": "b", "skills_add": ["no-such-skill"]},
        {"id": "c", "skills": ["document-style"], "skills_add": []},
        {"id": "d"},
    ]
    errs = stage_skills.plan_errors(steps)
    assert len(errs) == 2
    assert errs[0].startswith("a: development-workflow は規則の Skill でない")
    assert errs[1] == "b: no-such-skill の SKILL.md が無い"


def test_a_plan_with_a_bad_skill_stops_before_any_step(tmp_path):
    """受け入れ条件 5: ステップを 1 つも流さずに止まり、id と名前が出る。"""
    mark = tmp_path / "ran"
    plan = {
        "フェーズ": "検査",
        "作業場所": str(tmp_path),
        "steps": [
            {"id": "first", "type": "run", "cmd": f"touch {mark}", "next": "w"},
            {"id": "w", "type": "work", "prompt": "直す", "skills": ["no-such-skill"], "next": "end"},
        ],
    }
    text = engine.Engine(plan, tmp_path / "state").run(None)
    assert "結果: 止まった" in text and "w: no-such-skill" in text
    assert not mark.exists()


def test_a_plan_without_the_fields_passes_and_uses_the_table(tmp_path):
    """受け入れ条件 6: 欄の無い既存のプランは検査を通り、工程どおりに届く。"""
    steps = [{"id": "w", "type": "work", "stage": "設計", "prompt": "直す"}]
    assert stage_skills.plan_errors(steps) == []
    ctx = ctx_of(tmp_path)
    worker_steps.WorkStep().execute(ctx, steps[0])
    assert body_of("document-style") in ctx.claude.calls[-1]["system"]


# ---------------------------------------------------------------- worker への渡し方（受け入れ条件 7〜9・12・I5）


def test_the_plain_worker_gets_only_the_resolved_skill_between_kind_and_mvv(tmp_path):
    ctx = ctx_of(tmp_path)
    worker_steps.WorkStep().execute(ctx, {"id": "w", "stage": "実装", "prompt": "直す"})
    system = ctx.claude.calls[-1]["system"]
    assert system == WORK_SYSTEM + "\n\n" + stage_skills.render(["logging-guidelines"])
    assert "document-style" not in system  # 対応表のほかの行の Skill は入らない


def test_the_runtime_worker_gets_the_section_in_the_prompt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_steps, "run_ticking", lambda *a, **k: SimpleNamespace(stdout='{"status": "ok"}'))
    ctx = ctx_of(tmp_path)
    worker_steps.WorkStep().execute(ctx, {"id": "w", "stage": "設計", "prompt": "直す", "runtime": "codex"})
    text = (ctx.state.dir / "w-prompt.md").read_text()
    assert text.startswith(WORK_SYSTEM + "\n\n" + stage_skills.render(["document-style"]) + "\n\n")


def test_the_full_worker_keeps_the_same_system_on_resume_and_logs_once(tmp_path):
    """受け入れ条件 8・14・I5・I7: 起こし直しを 2 回しても system は同じで、行は 1 行。"""
    ctx = ctx_of(tmp_path, texts=["途中", "途中"])
    worker_steps.WorkStep().execute(ctx, {"id": "w", "stage": "設計", "prompt": "/ndf:design #1", "full": True})
    systems = [c["system"] for c in ctx.claude.calls]
    assert len(systems) == 3 and systems == [FULL_SYSTEM + "\n\n" + stage_skills.render(["document-style"])] * 3
    assert [(r["step"], r["stage"], r["names"], r["form"]) for r in skill_rows(ctx)] == [("w", "設計", ["document-style"], "本文")]


def test_the_work_row_is_written_before_the_call_even_when_empty(tmp_path):
    ctx = ctx_of(tmp_path)
    seen = []
    original = ctx.claude.call
    ctx.claude.call = lambda *a, **k: (seen.append(len(skill_rows(ctx))), original(*a, **k))[1]
    worker_steps.WorkStep().execute(ctx, {"id": "fix", "prompt": "直す"})
    assert seen == [1]
    assert skill_rows(ctx)[0]["names"] == [] and skill_rows(ctx)[0]["stage"] is None and "error" not in skill_rows(ctx)[0]


def test_the_pr_writer_gets_the_section_after_pr_system_and_stays_pr(tmp_path):
    """受け入れ条件 9: PR_SYSTEM で始まり、Pull Request の工程の Skill の本文を含み、種類は pr。"""
    ctx = ctx_of(tmp_path, texts=["本文"])
    body, err = PrStep()._llm_body(ctx, {"id": "pr", "stage": "Pull Request"}, "機械", "## 利用者向けの変化\n- 無し", "#1")
    system = ctx.claude.calls[-1]["system"]
    assert err is None and system.startswith(PR_SYSTEM) and body_of("document-style") in system
    assert claude_mod.claude_kind(system, False) == "pr"
    assert [r["step"] for r in skill_rows(ctx)] == ["pr"]


def test_the_pr_step_without_llm_never_resolves(tmp_path, broken_table, monkeypatch):
    """I6・I7: body が llm 以外の pr は解決も記録もせず、対応表が壊れていても落ちない。"""
    ctx = ctx_of(tmp_path)
    step = PrStep()
    monkeypatch.setattr(step, "design_diff", lambda c, s: (None, None))
    monkeypatch.setattr(step, "_push", lambda c: ("b", "boom"))  # push の後は見ない
    ok, text = step.execute(SimpleNamespace(**vars(ctx), base_branch=lambda: "main"), {"id": "pr", "stage": "Pull Request", "body": "machine"})
    assert text == "boom" and skill_rows(ctx) == []


def test_the_judge_never_gets_a_section(tmp_path):
    ctx = ctx_of(tmp_path, texts=['{"decision": "stop", "reason": "x"}'])
    JudgeStep().execute(ctx, {"id": "j", "stage": "設計", "question": "?", "choices": ["stop"]})
    assert ctx.claude.calls[-1]["system"] == JUDGE_SYSTEM and skill_rows(ctx) == []


def test_the_fix_doc_step_of_the_template_gets_document_style():
    """受け入れ条件 13: 雛形の fix-doc は doc-lint と同じ工程を持ち、document-style が届く。"""
    src = (SCRIPTS / "supervise_lib" / "templates.py").read_text(encoding="utf-8")
    block = src.split('"id": "fix-doc"', 1)[1].split("}", 1)[0]
    stage = re.search(r'"stage": "([^"]+)"', block).group(1)
    assert stage == "完了判定" and "document-style" in stage_skills.resolve({"stage": stage}, TABLE)


# ---------------------------------------------------------------- drive の pause（I6・I7）


def pause(ctx, tmp_path, n: int) -> str | None:
    pf, res = tmp_path / f"p{n}.md", tmp_path / f"r{n}.json"
    pf.write_text("直す")
    res.write_text("{}")
    ctx.state.cur.setdefault("pauses", []).append("fix")
    item = {"prompt_file": str(pf), "result_file": str(res)}
    return worker_steps.DriveStep()._delegate_pause(ctx, {"id": "d", "stage": "実装"}, item, "fix", res, str(tmp_path), [])


def test_each_pause_worker_is_resolved_and_logged(tmp_path):
    ctx = ctx_of(tmp_path)
    assert pause(ctx, tmp_path, 1) is None and pause(ctx, tmp_path, 2) is None
    assert [r["step"] for r in skill_rows(ctx)] == ["d", "d"]
    assert all(c["system"] == WORK_SYSTEM + "\n\n" + stage_skills.render(["logging-guidelines"]) for c in ctx.claude.calls)


def test_a_broken_table_stops_only_the_pause_worker(tmp_path, broken_table):
    ctx = ctx_of(tmp_path)
    err = pause(ctx, tmp_path, 1)
    assert err and str(broken_table) in err and ctx.claude.calls == []
    assert str(broken_table) in skill_rows(ctx)[0]["error"]


# ---------------------------------------------------------------- 読めないとき（受け入れ条件 15・I6）


def test_a_broken_table_fails_the_work_step_without_a_call(tmp_path, broken_table):
    ctx = ctx_of(tmp_path)
    ok, text = worker_steps.WorkStep().execute(ctx, {"id": "w", "stage": "設計", "prompt": "直す"})
    assert not ok and str(broken_table) in text and ctx.claude.calls == []
    row = skill_rows(ctx)[0]
    assert row["names"] == [] and str(broken_table) in row["error"] and ctx.state.cur["exit"] == 1


def test_a_missing_skill_md_fails_the_work_step_with_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(stage_skills, "SKILLS", tmp_path / "skills")
    ctx = ctx_of(tmp_path)
    ok, _ = worker_steps.WorkStep().execute(ctx, {"id": "w", "stage": "実装", "prompt": "直す"})
    assert not ok and ctx.claude.calls == []
    assert str(tmp_path / "skills" / "logging-guidelines" / "SKILL.md") in skill_rows(ctx)[0]["error"]


def test_a_broken_table_fails_the_pr_writer_without_a_call(tmp_path, broken_table):
    ctx = ctx_of(tmp_path)
    _, err = PrStep()._llm_body(ctx, {"id": "pr", "stage": "Pull Request"}, "機械", "", "#1")
    assert err and ctx.claude.calls == [] and skill_rows(ctx)[0]["error"]


# ---------------------------------------------------------------- 引くコマンド（受け入れ条件 10）


def test_lookup_returns_names_and_absolute_paths():
    p = subprocess.run([sys.executable, str(SCRIPTS / "stage-skills.py"), "lookup", "設計"], capture_output=True, text=True)
    out = json.loads(p.stdout)
    assert p.returncode == 0 and out["status"] == "ok"
    assert [i["name"] for i in out["items"]] == ["document-style"]
    assert all(Path(i["path"]).is_absolute() and Path(i["path"]).is_file() for i in out["items"])


def test_lookup_of_an_unknown_stage_is_an_empty_ok():
    p = subprocess.run([sys.executable, str(SCRIPTS / "stage-skills.py"), "lookup", "存在しない工程"], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["items"] == []


def test_lookup_of_an_unreadable_table_exits_1(tmp_path, monkeypatch):
    mod = _lookup_module()
    bad = tmp_path / "x.json"
    bad.write_text("[]")
    monkeypatch.setattr(mod.stage_skills, "TABLE", bad)
    out, code = mod.lookup("設計")
    assert code == 1 and out["status"] == "stopped"


# ---------------------------------------------------------------- ndf:worker の定義（受け入れ条件 11・I8）


def test_the_worker_definition_names_no_skill_and_keeps_skill_and_agent_off():
    text = (NDF / "agents" / "worker.md").read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    disallowed = re.search(r"^disallowedTools:\s*(.+)$", front, re.M).group(1)
    assert {"Skill", "Agent"} <= {t.strip() for t in disallowed.split(",")}
    assert not re.search(r"^skills:", front, re.M)
