"""人のいない実行の guard（#3242・#3746）: 印の作成・印の削除・次のコマンドの確かめ・差し戻しの上限と、区間の報告の契約の形。"""

from __future__ import annotations

import os
import time

import pytest

from ndf import identity
from ndf.domains.hooks import report, report_text, tables
from ndf.domains.hooks.guards import unattended

from ._hooks import advise_text, call_entry, records, sample, state_dir, stop

SID = "00000000-0000-0000-0000-000000000000"
SKILL = f"/{identity.load().prefix}"
CONTRACT = tables.load(report.FORM_TABLE)
# 写し元（今の NDF）の契約。11.0.0 で入れ替えた後は無い
ORIGINAL = tables.PLUGIN_ROOT.parent / "ndf" / "contracts" / "forms" / "unattended-report.json"


def _form() -> report.ReportForm:
    return report.load_form()


def _done() -> str:
    return report_text.form_examples(_form())["完了"]


def _mark():
    return unattended.mark_path(state_dir(), SID)


def _start(tmp_path, prompt=f"/goal {SKILL}development-workflow #12"):
    return call_entry("claude", "UserPromptSubmit", sample("claude", "UserPromptSubmit", tmp_path, prompt=prompt))


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setenv("NDF_FACTORY_RUN", "1")


@pytest.mark.parametrize("case", CONTRACT["cases"], ids=lambda c: c["text"][:24])
def test_contract_cases_decide_the_report_errors(case):
    form = _form()
    assert report.result_value(case["text"]) == case["result"]
    assert report.report_errors(case["text"], form) == case["errors"]


def test_every_example_of_the_form_passes():
    form = _form()
    assert all(report.report_errors(text, form) == [] for text in report_text.form_examples(form).values())


@pytest.mark.skipif(not ORIGINAL.is_file(), reason="写し元の契約が無い（11.0.0 で入れ替えた後）")
def test_the_copy_keeps_the_factory_contract():
    import json

    original = json.loads(ORIGINAL.read_text(encoding="utf-8"))
    # 例（cases）は語の検査に合わせて言い回しを直すことがあるため、Factory が読む形（shape）と規則を比べる
    assert {k: CONTRACT[k] for k in ("name", "rule", "shape")} == {k: original[k] for k in ("name", "rule", "shape")}
    assert [(c["result"], c["errors"]) for c in CONTRACT["cases"]] == [(c["result"], c["errors"]) for c in original["cases"]]


def test_the_conductor_prompt_creates_the_mark_and_returns_the_guide(tmp_path, factory):
    text = advise_text(_start(tmp_path))
    assert _mark().is_file()
    assert text.startswith("NDF: これは人のいない実行")


@pytest.mark.parametrize("prompt", [f"{SKILL}development-workflow-x", "実装して", f"{SKILL}fix #1", f"x {SKILL}development-workflow"])
def test_other_prompts_create_no_mark(tmp_path, factory, prompt):
    assert advise_text(_start(tmp_path, prompt)) is None
    assert not _mark().is_file()


def test_no_mark_without_the_factory_run(tmp_path):
    assert advise_text(_start(tmp_path)) is None
    assert not _mark().is_file()


def test_session_end_removes_the_mark(tmp_path, factory):
    _start(tmp_path)
    call_entry("claude", "SessionEnd", sample("claude", "SessionEnd", tmp_path))
    assert not _mark().is_file()


def test_session_start_prunes_only_old_marks(tmp_path, factory):
    _start(tmp_path)
    old = unattended.mark_dir(state_dir()) / "old-session.json"
    old.write_text("{}", encoding="utf-8")
    past = time.time() - tables.policy_value(unattended.MARK_POLICY, "seconds") - 60
    os.utime(old, (past, past))
    call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path))
    assert not old.exists() and _mark().is_file()


def test_stop_without_the_mark_is_not_pushed_back(tmp_path, factory):
    assert call_entry("claude", "Stop", stop(tmp_path, "途中")) is None


def test_stop_follows_the_contract_form(tmp_path, factory):
    _start(tmp_path)
    assert call_entry("claude", "Stop", stop(tmp_path, _done())) is None
    reason = call_entry("claude", "Stop", stop(tmp_path, "## 区間の報告\n\n- 結果: できなかった\n- 理由: 無し\n"))["reason"]
    assert report.ERR_MISSING.format(item="理由", value="できなかった") in reason
    assert report_text.TEXT_HEAD in reason


def test_the_guide_and_the_pushback_reach_the_conversation_whole(tmp_path, factory):
    # 形の説明（結果の値・要る項目・例）は注入の上限で切らずに届く（導入先を Read できない権限でも書けるように）
    form = report_text.describe_form(_form())
    assert form in advise_text(_start(tmp_path))
    reason = call_entry("claude", "Stop", stop(tmp_path, "途中\n次のコマンド: `ndf plan status p1`\n"))["reason"]
    assert reason.endswith(form) and "全文:" not in reason


def test_stop_with_background_work_is_not_pushed_back(tmp_path, factory):
    _start(tmp_path)
    assert call_entry("claude", "Stop", stop(tmp_path, "途中", background_tasks=[{"id": "b"}])) is None
    assert call_entry("claude", "Stop", stop(tmp_path, "途中", session_crons=[{"id": "c"}])) is None


@pytest.mark.parametrize(
    ("tail", "pushed"),
    [
        ("- 区分: 無し\n- 理由: テストが落ちた\n", True),
        ("- 区分: ndf-ask:human-action\n- 人に頼むこと: ログインする\n- 理由: 手動の確認が要る\n", False),
    ],
)
def test_a_pending_next_command_is_pushed_back(tail, pushed):
    text = f"次のコマンド: `ndf plan status p1`\n\n## 区間の報告\n\n- 結果: できなかった\n{tail}"
    assert (unattended.ERR_NEXT_COMMAND in unattended.stop_errors(text, _form())) is pushed


@pytest.mark.parametrize("value", ["無し", "", "無し（終わり）"])
def test_no_next_command_is_not_an_error(value):
    assert unattended.pending_command_errors(f"- 次のコマンド: {value}\n- 結果: できなかった\n- 理由: x\n") == []


def test_a_finished_report_may_keep_the_next_command():
    assert unattended.pending_command_errors(f"次のコマンド: ndf plan status\n\n{_done()}") == []


def test_pushbacks_stop_at_the_limit_and_restart_on_the_next_prompt(tmp_path, factory):
    limit = tables.policy_value(unattended.PUSHBACK_POLICY, "attempts")
    _start(tmp_path)
    reasons = [call_entry("claude", "Stop", stop(tmp_path, "途中")) for _ in range(limit + 1)]
    assert all(r is not None for r in reasons[:limit]) and reasons[limit] is None
    assert "残り 0 回" in reasons[limit - 1]["reason"]
    assert records()[-1]["verdict"] == "limited"
    _start(tmp_path)
    assert call_entry("claude", "Stop", stop(tmp_path, "途中")) is not None
