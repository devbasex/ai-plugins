"""サブコマンド `ndf progress`（受け入れ条件 1・2・3・5・10。P1-2 の戻し）。"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
import strategy_fixture

from ndf import contracts, identity
from ndf.adapters.git import Completed, Git, GitError
from ndf.contracts import causes
from ndf.core import durable
from ndf.progress import command, definition, invoke, model, stages, store


def _run(capsys, last_result, *argv: str) -> tuple[int, dict]:
    code = command.main(list(argv))
    return code, last_result(capsys.readouterr().out)


def _new(capsys, last_result, *extra: str) -> tuple[int, dict]:
    return _run(capsys, last_result, "new", "s1", "--mode", "standard", "--issues", "3369", *extra)


def test_new_lays_out_every_row_of_the_mode(repo, capsys, last_result):
    code, out = _new(capsys, last_result)
    assert code == 0 and out["status"] == "ok"
    rows = out["items"]
    assert [r["stage"] for r in rows][0] == "要求と受け入れ条件" and rows[-1]["stage"] == "棚卸し"
    assert len(rows) == 18 and {r["label"] for r in rows} == {"未着手"}


def test_second_table_for_the_same_sprint_is_refused_with_the_current_id(repo, capsys, last_result):
    _, first = _new(capsys, last_result)
    code, out = _new(capsys, last_result)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"
    assert out["metrics"]["current_id"] == first["metrics"]["machine_id"]
    assert code != 0


def test_disabled_rows_come_from_the_argument_and_the_project_ndf_settings(repo, capsys, last_result):
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(
        json.dumps({"workflow": {"stages": {"リファクタリング": {"enabled": False, "reason": "止めている"}}}})
    )
    _, out = _new(capsys, last_result, "--disable", "振り返り=今回は不要")
    reasons = {r["stage"]: r["reason"] for r in out["items"] if r["state"] == "disabled"}
    assert reasons == {"リファクタリング": "止めている", "振り返り": "今回は不要"}


def test_unknown_stage_in_disable_is_an_invocation_error(repo, capsys, last_result):
    code, out = _new(capsys, last_result, "--disable", "存在しない=理由")
    assert out["cause"]["kind"] == "invalid" and code == 2


def test_requirements_output_marks_the_first_row_finished(repo, capsys, tmp_path, last_result):
    good = tmp_path / "req.json"
    good.write_text(json.dumps({"steps": [], "summary": "要求を書いた"}))
    _, out = _new(capsys, last_result, "--requirements", str(good))
    assert out["items"][0]["label"] == "終了"


def test_conductor_operations_leave_reason_and_time(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    assert _run(capsys, last_result, "disable", tid, "実装", "--reason", "外す")[0] == 0
    assert _run(capsys, last_result, "enable", tid, "実装", "--reason", "戻す")[0] == 0
    out_file = tmp_path / "plan.json"
    out_file.write_text(json.dumps({"steps": [], "summary": "手で計画した"}))
    assert _run(capsys, last_result, "finish", tid, "計画", "--output", str(out_file), "--reason", "手で行った")[0] == 0
    _, shown = _run(capsys, last_result, "show", tid)
    history = shown["metrics"]["history"]
    assert [(h["kind"], h["reason"]) for h in history[-3:]] == [
        ("disabled", "外す"),
        ("enabled", "戻す"),
        ("manual_finished", "手で行った"),
    ]
    assert all(h["at"] for h in history)
    assert {r["stage"]: r["label"] for r in shown["items"]}["計画"] == "終了"


def _start(tid: str, row: str) -> None:
    root = command.store_root_of(Path.cwd())
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        last = store.read_events(conn, tid)[-1].seq
        store.append_events(conn, tid, [model.EventDraft("started", "workflow", row, rev=1)], last)


def test_running_row_cannot_be_disabled_and_bad_output_cannot_finish(repo, capsys, tmp_path, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    _start(tid, "実装")
    _, out = _run(capsys, last_result, "disable", tid, "実装", "--reason", "止めたい")
    assert out["cause"]["kind"] == "refused" and "実行中" in out["cause"]["detail"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"steps": []}))
    _, out = _run(capsys, last_result, "finish", tid, "要求と受け入れ条件", "--output", str(bad), "--reason", "手で")
    assert out["cause"]["kind"] == "refused" and "規約に合わない" in out["cause"]["detail"]


def test_retry_only_on_a_failed_row(repo, capsys, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    _, out = _run(capsys, last_result, "retry", tid, "設計", "--reason", "やり直す")
    assert out["cause"]["kind"] == "refused"


def test_cancel_closes_the_remaining_rows_and_the_table(repo, capsys, last_result):
    _, made = _new(capsys, last_result, "--disable", "振り返り=不要")
    tid = made["metrics"]["machine_id"]
    _, out = _run(capsys, last_result, "cancel", tid, "--reason", "計画から外した")
    assert out["metrics"]["cancelled_rows"] == 17
    _, shown = _run(capsys, last_result, "show", tid)
    assert shown["metrics"]["state"] == "cancelled"
    labels = {r["stage"]: r["label"] for r in shown["items"]}
    assert labels["振り返り"] == "無効" and labels["設計"] == "中止"
    _, again = _run(capsys, last_result, "enable", tid, "振り返り", "--reason", "戻す")
    assert again["cause"]["kind"] == "refused"


def test_restore_command_puts_back_a_backup(repo, capsys, last_result):
    _new(capsys, last_result)
    root = command.store_root_of(repo)
    durable.release_store(root)
    backup = store.latest_backup(root)
    code, out = _run(capsys, last_result, "restore", "--from", str(backup))
    assert code == 0 and out["items"][0]["from"] == str(backup)


def test_restore_command_refuses_a_postgresql_url(repo, capsys, monkeypatch, tmp_path, last_result):
    monkeypatch.setenv("NDF_PROGRESS_DB", "postgresql://ndf@127.0.0.1:1/none")
    code, out = _run(capsys, last_result, "restore", "--from", str(tmp_path / "x.sqlite"))
    assert out["cause"]["kind"] == "restore_unsupported" and code == 3


def test_show_of_an_unknown_table_is_refused(repo, capsys, last_result):
    _, out = _run(capsys, last_result, "show", "sprint:nope:s9")
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"


# --- 現状固定: 例外から止まった結果への対応（stopped_from） -------------------------------------------


def _stopped(exc: Exception) -> tuple[str, str, list, dict]:
    out = command.stopped_from(exc)
    assert out["tool"] == "progress" and out["status"] == "stopped"
    return out["cause"]["kind"], out["cause"]["detail"], out["items"], out["metrics"]


def test_stopped_from_refused_carries_the_current_id_only_when_present():
    assert _stopped(model.Refused("断った", current_id="sprint:x:s1")) == ("refused", "断った", [], {"current_id": "sprint:x:s1"})
    assert _stopped(model.Refused("断った")) == ("refused", "断った", [], {})


def test_stopped_from_store_stopped_uses_the_kind_of_the_subclass_and_lists_the_paths():
    assert _stopped(durable.StoreStopped("壊れた", (Path("/a"), Path("/b")))) == (
        "store_broken",
        "壊れた",
        [{"path": "/a"}, {"path": "/b"}],
        {},
    )
    assert _stopped(durable.StoreInUse("使用中"))[:3] == ("store_in_use", "使用中", [])
    assert _stopped(store.StoreBusy("待ちすぎ"))[0] == "store_busy"


def test_stopped_from_workflow_def_error_is_invalid_with_one_item_per_error():
    kind, detail, items, metrics = _stopped(definition.WorkflowDefError(["a が無い", "b が無い"]))
    assert (kind, items, metrics) == ("invalid", [{"error": "a が無い"}, {"error": "b が無い"}], {})
    assert detail == "工程の定義が検査を通らない: a が無い; b が無い"


def test_stopped_from_contract_and_git_errors_are_invalid_without_items():
    assert _stopped(contracts.ContractError("形が違う")) == ("invalid", "形が違う", [], {})
    assert _stopped(GitError("git が落ちた")) == ("invalid", "git が落ちた", [], {})


def test_stopped_from_identity_error_is_config_broken():
    assert _stopped(identity.IdentityError("plugin.json が無い")) == ("config_broken", "plugin.json が無い", [], {})


def test_stopped_from_anything_else_is_a_precondition_outside_git():
    exc = subprocess.CalledProcessError(128, ["git", "rev-parse"])
    kind, detail, items, _ = _stopped(exc)
    assert kind == "precondition" and items == []
    assert detail == f"git の中で打つ（チェックアウトの保存先を求められない）: {exc}"


# --- 現状固定: `.ndf/project.json` の工程の設定の読み取り（_declared） ----------------------------------


def _declare(repo: Path, raw: str) -> Path:
    (repo / ".ndf").mkdir(exist_ok=True)
    path = repo / ".ndf" / "project.json"
    path.write_text(raw, encoding="utf-8")
    return path


def test_declared_is_empty_without_the_project_file(repo):
    assert command._declared(repo) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '"文字列"',
        "{}",
        '{"workflow": []}',
        '{"workflow": {}}',
        '{"workflow": {"stages": []}}',
        '{"workflow": {"stages": "x"}}',
    ],
)
def test_declared_is_empty_when_the_workflow_stages_are_not_an_object(repo, raw):
    _declare(repo, raw)
    assert command._declared(repo) == {}


def test_declared_returns_the_stages_as_written(repo):
    stages = {"リファクタリング": {"enabled": False, "reason": "止めている"}, "振り返り": {"enabled": True}}
    _declare(repo, json.dumps({"workflow": {"stages": stages, "other": 1}, "test": {}}))
    assert command._declared(repo) == stages


def test_declared_reads_the_file_at_the_top_of_the_checkout_from_a_subdirectory(repo):
    _declare(repo, json.dumps({"workflow": {"stages": {"実装": {"enabled": False, "reason": "外す"}}}}))
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert command._declared(sub) == {"実装": {"enabled": False, "reason": "外す"}}


def test_declared_raises_a_contract_error_naming_the_file_when_it_is_not_json(repo):
    path = _declare(repo, "{壊れている")
    with pytest.raises(contracts.ContractError) as caught:
        command._declared(repo)
    assert str(caught.value).startswith(f"{path} を読めない: ")


def test_new_stops_as_invalid_when_the_project_file_is_not_json(repo, capsys, last_result):
    _declare(repo, "{壊れている")
    code, out = _new(capsys, last_result)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "invalid" and code == 2
    assert "project.json を読めない" in out["cause"]["detail"]


@pytest.mark.parametrize(
    "decl",
    [
        "止めている",
        {"reason": "enabled が無い"},
        {"enabled": "false", "reason": "真偽でない"},
        {"enabled": False},
        {"enabled": False, "reason": ""},
    ],
)
def test_new_stops_as_invalid_when_a_stage_ndf_settings_has_the_wrong_shape(repo, capsys, decl, last_result):
    _declare(repo, json.dumps({"workflow": {"stages": {"リファクタリング": decl}}}))
    code, out = _new(capsys, last_result)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "invalid" and code == 2
    assert "workflow.stages.リファクタリング" in out["cause"]["detail"]


def test_requirements_is_refused_in_a_mode_without_the_requirements_stage(repo, capsys, tmp_path, last_result):
    good = tmp_path / "req.json"
    good.write_text(json.dumps({"steps": [], "summary": "要求を書いた"}))
    code, out = _run(capsys, last_result, "new", "s1", "--mode", "legacy-refactor", "--issues", "3369", "--requirements", str(good))
    assert out["status"] == "stopped" and out["cause"]["kind"] == "invalid" and code == 2
    assert "要求と受け入れ条件の工程が無い" in out["cause"]["detail"]


# --- 現状固定: ステートマシンの作成（_cmd_new） ------------------------------------------------------------


def _stored(tid: str) -> tuple[dict, dict]:
    root = command.store_root_of(Path.cwd())
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        table = store.read_machine(conn, tid)
        _, doc = store.read_definition(conn, tid)
    return table, doc


def test_new_reports_the_machine_id_rows_and_disabled_in_summary_and_metrics(repo, capsys, last_result):
    code, out = _new(capsys, last_result, "--disable", "振り返り=不要")
    tid = model.machine_id("sprint", str(repo), "s1")
    assert code == 0 and out["tool"] == "progress"
    assert out["metrics"] == {"machine_id": tid, "rows": 18, "disabled": ["振り返り"]}
    assert out["summary"] == f"ステートマシン {tid} を作った（18 行、無効 1 行）"
    assert set(out["items"][0]) == {"stage", "phase", "state", "label", "at", "reason", "gates", "outputs"}


def test_new_names_the_table_by_the_origin_url_when_there_is_one(repo, capsys, last_result):
    subprocess.run(["git", "remote", "add", "origin", "https://example.invalid/o/r.git"], cwd=repo, check=True)
    _, out = _new(capsys, last_result)
    assert out["metrics"]["machine_id"] == model.machine_id("sprint", "https://example.invalid/o/r.git", "s1")


def test_new_drops_the_credentials_of_the_origin_url_from_the_machine_id(repo, capsys, last_result):
    subprocess.run(["git", "remote", "add", "origin", "https://x-access-token:secret@example.invalid/o/r.git"], cwd=repo, check=True)
    _, out = _new(capsys, last_result)
    assert out["metrics"]["machine_id"] == model.machine_id("sprint", "https://example.invalid/o/r.git", "s1")
    table, _ = _stored(out["metrics"]["machine_id"])
    assert "secret" not in json.dumps(table, ensure_ascii=False, default=str)


@pytest.mark.parametrize("url", ["git@example.invalid:o/r.git", "https://example.invalid/o/r.git", "/srv/o/r"])
def test_without_userinfo_keeps_urls_without_credentials(url):
    assert command._without_userinfo(url) == url


def test_unreachable_database_stops_as_store_unreachable_without_the_password(repo, capsys, monkeypatch, last_result):
    monkeypatch.setenv("NDF_PROGRESS_DB", "postgresql+psycopg://u:hunter2@127.0.0.1:1/none")
    code, out = _new(capsys, last_result)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "store_unreachable"
    assert code == causes.exit_code("store_unreachable")
    assert "hunter2" not in json.dumps(out, ensure_ascii=False)


def test_stopped_from_a_bare_sqlalchemy_error_shows_only_its_type():
    import sqlalchemy as sa

    assert _stopped(sa.exc.ArgumentError("postgresql://u:pw@h/db"))[:2] == (
        "store_unreachable",
        "ステートマシンの DB を使えない: ArgumentError",
    )


def test_sqlite_url_with_a_query_stops_as_invalid_before_preparing_the_store(repo, capsys, monkeypatch, tmp_path, last_result):
    db = tmp_path / "progress.sqlite"
    monkeypatch.setenv("NDF_PROGRESS_DB", f"sqlite:///{db}?timeout=30")
    code, out = _new(capsys, last_result)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "invalid"
    assert not list(tmp_path.glob("progress.sqlite*"))


def test_new_stores_the_context_the_enablement_and_the_parent(repo, capsys, last_result):
    _declare(repo, json.dumps({"workflow": {"stages": {"リファクタリング": {"enabled": False, "reason": "止めている"}}}}))
    _, out = _run(
        capsys,
        last_result,
        *("new", "s1", "--mode", "standard", "--issues", "1", "2", "--pace", "fast"),
        *("--disable", "振り返り=不要", "--parent", "lane:abc:l1"),
    )
    table, doc = _stored(out["metrics"]["machine_id"])
    assert table["parent_id"] == "lane:abc:l1" and table["level"] == "sprint" and table["name"] == "s1"
    # MVV の設定が無いリポジトリでは、承認済み MVV の参照は null（#3745）
    assert doc["context"] == {"issues": [1, 2], "mode": "standard", "pace": "fast", "mvv_ref": None}
    assert doc["mode"] == "standard" and [s["name"] for s in doc["stages"]] == [r["stage"] for r in out["items"]]
    en = doc["enablement"]
    assert en["振り返り"] == {"enabled": False, "reason": "不要", "source": "sprint"}
    assert en["リファクタリング"] == {"enabled": False, "reason": "止めている", "source": "project"}
    others = {k: v for k, v in en.items() if k not in ("振り返り", "リファクタリング")}
    assert len(others) == 16 and all(v == {"enabled": True, "reason": None, "source": "definition"} for v in others.values())


def test_new_writes_created_then_disabled_then_finished_events(repo, capsys, tmp_path, last_result):
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"steps": [], "summary": "要求を書いた"}))
    _, made = _new(capsys, last_result, "--disable", "振り返り=不要", "--requirements", str(req))
    _, shown = _run(capsys, last_result, "show", made["metrics"]["machine_id"])
    history = shown["metrics"]["history"]
    rows = [r["stage"] for r in made["items"]]
    assert [(h["kind"], h["row_key"]) for h in history[:18]] == [("created", r) for r in rows]
    assert all(h["actor"] == "conductor" and h["rev"] == 1 for h in history)
    tail = [(h["kind"], h["row_key"], h["reason"], h["payload"]) for h in history[18:]]
    assert tail == [
        ("disabled", "振り返り", "不要", {"source": "sprint"}),
        ("manual_finished", "要求と受け入れ条件", "ステートマシンの作成の時点で済んでいる", {"output": str(req)}),
    ]
    assert shown["metrics"]["mode"] == "standard" and shown["metrics"]["rev"] == 1


@pytest.mark.parametrize(
    ("extra", "prefix"),
    [
        (("--pace", "slow"), "スプリントの文脈: "),
        (("--disable", "存在しない=理由"), "--disable の工程がモード standard に無い: 存在しない"),
    ],
)
def test_new_stops_as_invalid_with_one_item_per_error(repo, capsys, extra, prefix, last_result):
    code, out = _new(capsys, last_result, *extra)
    assert code == 2 and out["cause"]["kind"] == "invalid"
    assert out["items"] and all(i["error"].startswith(prefix) for i in out["items"])


@pytest.mark.parametrize("extra", [("--pace", "slow"), ("--issues", "5", "5"), ("--disable", "存在しない=理由")])
def test_new_that_stops_as_invalid_leaves_no_table(repo, capsys, extra, last_result):
    """現状固定: 文脈か --disable で止まった new はステートマシンを残さず、同じ名前で作り直せる。"""
    code, out = _new(capsys, last_result, *extra)
    assert code == 2 and out["cause"]["kind"] == "invalid"
    code, out = _new(capsys, last_result)
    assert code == 0 and out["status"] == "ok"


def test_new_of_an_unknown_mode_is_invalid(repo, capsys, last_result):
    code, out = _run(capsys, last_result, "new", "s1", "--mode", "nope", "--issues", "1")
    assert code == 2 and out["cause"]["kind"] == "invalid"
    assert out["items"][0]["error"].startswith("モード nope の定義が無い")


def test_new_needs_the_mode_and_the_issues_or_a_loop(repo, capsys, last_result):
    code, out = _run(capsys, last_result, "new", "s1", "--issues", "1")
    assert code == 2 and "--loop" in out["items"][0]["error"]


def test_new_reports_missing_arguments_before_reading_a_broken_origin(repo, capsys, last_result):
    # 引数の不足は origin の URL を解くより先に invalid で返す（解けない URL の ValueError で落ちない）
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://[broken"], check=True)
    code, out = _run(capsys, last_result, "new", "s1")
    assert code == 2 and out["cause"]["kind"] == "invalid" and "--loop" in out["items"][0]["error"]


def test_a_loop_table_takes_neither_the_sprint_arguments_nor_a_missing_lane(repo, capsys, last_result):
    # #3558: 開発ループの行のステートマシンは工程を持たず、レーンの子にしか作らない
    code, out = _run(capsys, last_result, "new", "s1", "--loop", "lr", "--mode", "standard", "--parent", "x")
    assert code == 2 and "--mode" in out["items"][0]["error"]
    code, out = _run(capsys, last_result, "new", "s1", "--loop", "lr")
    assert code == 2 and "--parent" in out["items"][0]["error"]


def test_new_with_duplicated_issues_is_invalid(repo, capsys, last_result):
    code, out = _run(capsys, last_result, "new", "s1", "--mode", "standard", "--issues", "5", "5")
    assert code == 2 and out["items"][0]["error"].startswith("スプリントの文脈: ")


def test_new_refuses_requirements_that_break_the_output_contract_or_are_not_json(repo, capsys, tmp_path, last_result):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"steps": []}))
    code, out = _new(capsys, last_result, "--requirements", str(bad))
    assert code == 2 and out["items"][0]["error"].startswith("要求と受け入れ条件 の出力が規約に合わない: ")
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    code, out = _new(capsys, last_result, "--requirements", str(broken))
    assert code == 2 and out["cause"]["detail"].startswith(f"{broken} を JSON として読めない: ")
    _, made = _new(capsys, last_result)
    assert made["status"] == "ok"


# --- 承認ゲートの工程は無効にできない（3364-2） ---------------------------------------------------


@pytest.mark.parametrize("stage", ["設計", "デプロイ"])
def test_gate_stage_cannot_be_disabled_by_the_argument(repo, capsys, stage, last_result):
    code, out = _new(capsys, last_result, "--disable", f"{stage}=外したい")
    assert out["cause"]["kind"] == "refused" and "承認ゲート" in out["cause"]["detail"] and stage in out["cause"]["detail"]
    assert code != 0


def test_gate_stage_cannot_be_disabled_by_the_project_ndf_settings(repo, capsys, last_result):
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(
        json.dumps({"workflow": {"stages": {"デプロイ": {"enabled": False, "reason": "止めている"}}}})
    )
    code, out = _new(capsys, last_result)
    assert out["cause"]["kind"] == "refused" and "デプロイ" in out["cause"]["detail"]
    assert code != 0


def test_gate_stage_cannot_be_disabled_by_the_conductor(repo, capsys, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    _, out = _run(capsys, last_result, "disable", tid, "設計", "--reason", "外したい")
    assert out["cause"]["kind"] == "refused" and "承認ゲート" in out["cause"]["detail"]
    # ほかの工程は今のまま外せる
    assert _run(capsys, last_result, "disable", tid, "振り返り", "--reason", "外す")[0] == 0


# --- 現状固定: command._append（I-014。番号のぶつかりで読み直して作り直し、回数が尽きれば投げる） ---------


def test_append_rebuilds_after_a_conflict_and_raises_when_the_attempts_run_out(repo, capsys, conflicts, last_result):
    _, made = _new(capsys, last_result)
    tid = made["metrics"]["machine_id"]
    calls = conflicts(1)
    code, out = _run(capsys, last_result, "disable", tid, "実装", "--reason", "外す")
    assert code == 0 and calls["n"] == 2 and len(out["metrics"]["seqs"]) == 1

    builds: list[int] = []

    def build(rev, snap, events):
        builds.append(rev)
        return [model.EventDraft("enabled", command.ACTOR, "実装", reason="戻す", rev=rev)]

    calls = conflicts(None)
    root = command.store_root_of(repo)
    with store.open_store(store.default_url(root), root) as st, pytest.raises(store.Conflict):
        command._append(st, tid, build)
    assert calls["n"] == command.append_attempts() and len(builds) == command.append_attempts()


def _git(repo: Path, *args: str) -> None:
    env = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "-C", str(repo), *env, *args], check=True, capture_output=True)


def _docs_repo(repo: Path, remote: str, changed: dict[str, str], project: dict | None = None) -> None:
    """`<remote>/main` を指す ref と、その上に `changed` を足したコミットを持たせる。"""
    if project is not None:
        (repo / ".ndf").mkdir(exist_ok=True)
        (repo / ".ndf" / "project.json").write_text(json.dumps(project), encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "update-ref", f"refs/remotes/{remote}/main", "HEAD")
    for name, text in changed.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")


@pytest.mark.parametrize(
    ("changed", "kind"),
    [
        ({"docs/a.md": "# a\n", "skills/s/SKILL.md": "# s\n"}, "not_applicable"),  # 振る舞いの文書の既定は無し（受け入れ条件 11）
        ({"docs/a.md": "# a\n", "src/a.py": "a = 1\n"}, None),
    ],
)
def test_docs_only_without_ndf_settings_skips_only_markdown(repo, capsys, last_result, changed, kind):
    _docs_repo(repo, "origin", changed)
    code, out = _run(capsys, last_result, "docs-only", "--base", "main")
    assert (out["cause"]["kind"] if out["status"] == "stopped" else None) == kind


def test_docs_only_follows_the_docs_ndf_settings_and_the_remote(repo, capsys, last_result):
    """`docs.files` にすべて当たれば文書だけ、1 つ外れれば違う。リモート名は設定から読む（受け入れ条件 8・10）。"""
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "remote": "upstream"}), encoding="utf-8")
    _docs_repo(repo, "upstream", {"openapi/a.yaml": "a: 1\n"}, {"docs": {"files": ["**/*.md", "openapi/*.yaml"]}})
    _, out = _run(capsys, last_result, "docs-only", "--base", "main")
    assert out["cause"]["kind"] == "not_applicable"
    (repo / "src.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "code")
    _, out = _run(capsys, last_result, "docs-only", "--base", "main")
    assert out["status"] == "ok"


def test_docs_only_matches_the_stage_condition(repo, capsys, last_result):
    """`assess` と工程の条件 `docs_only_diff` は同じ判定を使う（I8a）。"""
    _docs_repo(repo, "origin", {"skills/s/SKILL.md": "# s\n"}, {"docs": {"behavior": ["skills/**/*.md"]}})
    _, out = _run(capsys, last_result, "docs-only", "--base", "main")
    assert out["status"] == "ok"
    assert command.docs_only(Git(repo), "origin/main...HEAD", repo) is False


def test_docs_only_without_the_remote_ref_is_a_precondition(repo, capsys, last_result):
    _docs_repo(repo, "origin", {"a.md": "# a\n"})
    _, out = _run(capsys, last_result, "docs-only", "--base", "develop")
    assert out["cause"]["kind"] == "precondition"


def _sprint_branch_repo(repo: Path) -> None:
    """ベースブランチ develop（ブランチ戦略 web-develop）から切ったスプリントブランチ sprint/s1 に居るリポジトリにする。"""
    _git(repo, "checkout", "-q", "-b", "develop")
    strategy_fixture.write(repo, "web-develop")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-q", "-b", "sprint/s1")


def test_base_ref_is_the_local_branch_or_the_remote_tracking_one(repo):
    # #3908 の決定 7: 差分の起点の規則（工程の条件 code_in_diff と同じ）を切り出したもの。
    # 手元に読めるブランチはその名前、読めなければ作業場所のリモート名を付けた参照
    _sprint_branch_repo(repo)
    assert command.base_ref(Git(repo), "develop") == "develop"
    assert command.base_ref(Git(repo), "release") == "origin/release"
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "remote": "upstream"}), encoding="utf-8")
    assert command.base_ref(Git(repo), "release") == "upstream/release"


def test_base_ref_is_resolved_once_while_a_stage_starts(tmp_path):
    # #3908 の r2-5: 工程の始まりの中では、条件の判定と既定の起動の組み立てが同じ起点を 1 回だけ求める。範囲の外では毎回求める
    calls: list[list[str]] = []

    def runner(args: list[str], cwd: Path | None) -> Completed:
        calls.append(args)
        return Completed(0, "0" * 40 + "\n", "")

    git = Git(tmp_path, runner)
    with command.base_refs_once():
        assert command.base_ref(git, "develop") == "develop"
        assert command.base_ref(Git(tmp_path / ".", runner), "develop") == "develop"
    assert len(calls) == 1
    assert command.base_ref(git, "develop") == "develop" and len(calls) == 2


def _commit(repo: Path, changed: dict[str, str]) -> str:
    """`changed` を書いてコミットし、そのコミットを返す。"""
    for name, text in changed.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    return Git(repo).run("rev-parse", "HEAD").strip()


def _code_review_enabled(context: dict, repo: Path) -> bool:
    """工程「コードレビュー」が定義の条件（when）で有効か（条件の語は `facts_of` で求める）。"""
    snap = definition.load_definition("standard")
    stage = snap.stage("コードレビュー")
    facts = command.facts_of(stage.when, context, repo)
    return stages.resolve_enabled(dataclasses.replace(snap, stages=(stage,)), facts, {}, {})["コードレビュー"].enabled


def test_current_code_review_runs_on_a_docs_only_sprint_diff(repo):
    # 現状固定（#3879 Task 8）: 文脈に gathered が無いスプリントは、文書だけの差分でも工程「コードレビュー」を流す
    _sprint_branch_repo(repo)
    _commit(repo, {"docs/a.md": "# a\n"})
    assert _code_review_enabled({"worktree": str(repo), "branch": "sprint/s1"}, repo) is True


SPRINT = {"branch": "sprint/s1"}
SEAM = ("code_in_unreviewed_diff",)


def _seam(repo: Path, context: dict) -> bool:
    return command.facts_of(SEAM, {"worktree": str(repo), **context}, repo)["code_in_unreviewed_diff"]


@pytest.mark.parametrize(
    ("after", "expect"),
    [
        ({"src/a.py": "a = 1\n"}, True),  # 集めた後にコードのコミットがある
        ({"docs/b.md": "# b\n"}, False),  # 集めた後は文書のコミットだけ
        (None, False),  # 集めた後にコミットが無い
    ],
)
def test_code_in_unreviewed_diff_reads_only_the_commits_after_gathered(repo, after, expect):
    # #3879 構成要素 3: つなぎ目（gathered..<HEAD>）にコードのファイルがあるか。集める前のコードは見ない
    _sprint_branch_repo(repo)
    gathered = _commit(repo, {"src/unit.py": "u = 1\n"})
    if after is not None:
        _commit(repo, after)
    assert _seam(repo, {**SPRINT, "gathered": gathered}) is expect


@pytest.mark.parametrize("changed", [{"src/a.py": "a = 1\n"}, {"docs/a.md": "# a\n"}])
def test_code_in_unreviewed_diff_without_gathered_is_true_whatever_the_sprint_diff_holds(repo, changed):
    # gathered を書く前に始まったスプリントは、今のとおり差分の中身を問わずにコードレビューを流す
    _sprint_branch_repo(repo)
    _commit(repo, changed)
    assert _seam(repo, SPRINT) is True


def test_code_in_unreviewed_diff_cannot_be_decided_from_a_gathered_that_is_not_a_commit(repo):
    _sprint_branch_repo(repo)
    _commit(repo, {"src/a.py": "a = 1\n"})
    with pytest.raises(command.FactUnknown, match="code_in_unreviewed_diff"):
        _seam(repo, {**SPRINT, "gathered": "0" * 40})


def test_code_in_unreviewed_diff_cannot_be_decided_when_the_sprint_branch_was_rewritten(repo):
    # 集めた後にスプリントブランチを作り直し、gathered が HEAD の祖先でない。理由に gathered と HEAD を書く
    _sprint_branch_repo(repo)
    gathered = _commit(repo, {"src/a.py": "a = 1\n"})
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    tip = _commit(repo, {"src/b.py": "b = 1\n"})
    with pytest.raises(command.FactUnknown) as info:
        _seam(repo, {**SPRINT, "gathered": gathered})
    assert gathered in str(info.value) and tip in str(info.value)


def test_code_in_unreviewed_diff_cannot_be_decided_on_another_branch_of_the_worktree(repo):
    # worktree のブランチが文脈の branch と違う。理由に 2 つのブランチ名を書く
    _sprint_branch_repo(repo)
    gathered = _commit(repo, {"src/a.py": "a = 1\n"})
    _git(repo, "checkout", "-q", "-b", "other")
    with pytest.raises(command.FactUnknown) as info:
        _seam(repo, {**SPRINT, "gathered": gathered})
    assert "sprint/s1" in str(info.value) and "other" in str(info.value)


def _pushed_from_elsewhere(repo: Path, tmp_path: Path, changed: dict[str, str]) -> str:
    """sprint/s1 を origin へ push して gathered にし、別の作業場所から `changed` のコミットを push する（手元は gathered のまま）。"""
    _sprint_branch_repo(repo)
    gathered = _commit(repo, {"src/unit.py": "u = 1\n"})
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "origin.git")], check=True)
    _git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(repo, "push", "-q", "origin", "sprint/s1")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "sprint/s1", str(tmp_path / "origin.git"), str(other)], check=True)
    _commit(other, changed)
    _git(other, "push", "-q", "origin", "sprint/s1")
    return gathered


@pytest.mark.parametrize(("changed", "expect"), [({"src/b.py": "b = 1\n"}, True), ({"docs/b.md": "# b\n"}, False)])
def test_code_in_unreviewed_diff_reads_the_tip_of_the_pr_pushed_from_another_worktree(repo, tmp_path, changed, expect):
    # PR 3904 の指摘: 手元が gathered のままでも、別の作業場所から push されたコードがあれば工程「コードレビュー」を飛ばさない
    gathered = _pushed_from_elsewhere(repo, tmp_path, changed)
    assert _seam(repo, {**SPRINT, "gathered": gathered}) is expect


def test_code_in_unreviewed_diff_cannot_be_decided_when_the_pr_tip_lost_gathered(repo, tmp_path):
    # リモートのスプリントブランチを書き換えて gathered が PR の HEAD の祖先でなければ、PR の中身を確かめずに飛ばさない
    gathered = _pushed_from_elsewhere(repo, tmp_path, {"src/b.py": "b = 1\n"})
    _git(repo, "push", "-q", "--force", "origin", "HEAD~1:refs/heads/sprint/s1")
    with pytest.raises(command.FactUnknown, match="祖先でない"):
        _seam(repo, {**SPRINT, "gathered": gathered})


def test_unreviewed_range_is_from_gathered_to_the_tip_of_the_sprint_branch(repo):
    _sprint_branch_repo(repo)
    gathered = _commit(repo, {"src/a.py": "a = 1\n"})
    _, rng, got = command.unreviewed_range({"worktree": str(repo), **SPRINT, "gathered": gathered}, repo)
    assert (rng, got) == (f"{gathered}..sprint/s1", gathered)


# --- 工程の出力が要る文脈のキー（show の各行の outputs）と、対象のリポジトリの位置（--cwd） ---------------------


def _pushed_sprint(repo: Path, tmp_path: Path) -> str:
    """スプリントブランチ sprint/s1 を origin（裸のリポジトリ）へ push し、その HEAD を返す。"""
    _sprint_branch_repo(repo)
    tip = _commit(repo, {"src/unit.py": "u = 1\n"})
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "origin.git")], check=True)
    _git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(repo, "push", "-q", "origin", "sprint/s1")
    return tip


def test_gathered_returns_the_tip_of_the_sprint_branch_after_every_unit_pr_is_merged(repo, tmp_path, capsys, last_result, monkeypatch):
    # #3879: 工程「実装」の出力 gathered は、単位の PR をすべて集めた時点のスプリントブランチの HEAD
    tip = _pushed_sprint(repo, tmp_path)
    monkeypatch.setattr(command, "open_unit_prs", lambda _git, remote, branch: [])
    _commit(repo, {"docs/after.md": "# after\n"})  # 手元が先へ進んでいても、集めた時点はリモートの HEAD
    code, out = _run(capsys, last_result, "gathered")
    assert code == 0 and out["outputs"] == {"gathered": tip}


def test_gathered_stops_while_a_unit_pr_into_the_sprint_branch_is_open(repo, tmp_path, capsys, last_result, monkeypatch):
    _pushed_sprint(repo, tmp_path)
    monkeypatch.setattr(command, "open_unit_prs", lambda _git, remote, branch: [12, 13] if branch == "sprint/s1" else [])
    _, out = _run(capsys, last_result, "gathered", "--branch", "sprint/s1")
    assert out["cause"]["kind"] == "precondition" and "#12" in out["summary"] and "outputs" not in out


def test_gathered_stops_when_the_worktree_lacks_the_remote_tip(repo, tmp_path, capsys, last_result, monkeypatch):
    # 工程「コードレビュー」の unreviewed_range は gathered が HEAD の祖先かを確かめるので、マージしていない HEAD は書かない
    _pushed_sprint(repo, tmp_path)
    monkeypatch.setattr(command, "open_unit_prs", lambda _git, remote, branch: [])
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    _, out = _run(capsys, last_result, "gathered")
    assert out["cause"]["kind"] == "precondition" and "merge --ff-only" in out["summary"]


def test_gathered_stops_on_a_precondition_when_the_sprint_branch_is_not_pushed(repo, tmp_path, capsys, last_result, monkeypatch):
    # 利用者が直せる前提の欠けは invalid でなく precondition。理由に直し方（push してから打つ）を書く
    _pushed_sprint(repo, tmp_path)
    monkeypatch.setattr(command, "open_unit_prs", lambda _git, remote, branch: [])
    _git(repo, "checkout", "-q", "-b", "sprint/s2")
    _, out = _run(capsys, last_result, "gathered")
    assert out["cause"]["kind"] == "precondition" and "push" in out["summary"] and "outputs" not in out


def test_gathered_stops_on_a_precondition_on_a_detached_head(repo, tmp_path, capsys, last_result, monkeypatch):
    _pushed_sprint(repo, tmp_path)
    monkeypatch.setattr(command, "open_unit_prs", lambda _git, remote, branch: [])
    _git(repo, "checkout", "-q", "--detach")
    _, out = _run(capsys, last_result, "gathered")
    assert out["cause"]["kind"] == "precondition" and "チェックアウト" in out["summary"]


def test_gathered_stops_on_a_precondition_when_the_remote_is_not_github(repo, tmp_path, capsys, last_result):
    # open_unit_prs を差し替えない: origin は裸のリポジトリのパスで、GitHub の PR を読めない
    _pushed_sprint(repo, tmp_path)
    _, out = _run(capsys, last_result, "gathered")
    assert out["cause"]["kind"] == "precondition" and "GitHub" in out["summary"]


def test_gathered_stops_when_branch_differs_from_the_branch_of_the_worktree(repo, tmp_path, capsys, last_result, monkeypatch):
    # 祖先の確かめは worktree の HEAD に対して行うので、別のブランチの worktree では確かめられない（unreviewed_range と同じ）
    _pushed_sprint(repo, tmp_path)
    monkeypatch.setattr(command, "open_unit_prs", lambda _git, remote, branch: [])
    _git(repo, "checkout", "-q", "-b", "other")  # sprint/s1 の HEAD を含むので、祖先の確かめだけでは通ってしまう
    _, out = _run(capsys, last_result, "gathered", "--branch", "sprint/s1")
    assert out["cause"]["kind"] == "precondition" and "other" in out["summary"] and "sprint/s1" in out["summary"]


def test_gathered_on_the_base_branch_does_not_ask_for_unit_prs(repo, tmp_path, capsys, last_result, monkeypatch):
    # pace: fast の課題はベースブランチへ直接入るので、ベースブランチ宛ての無関係な PR で止めない
    _pushed_sprint(repo, tmp_path)
    _git(repo, "checkout", "-q", "develop")
    _git(repo, "push", "-q", "origin", "develop")
    monkeypatch.setattr(command, "open_unit_prs", lambda _git, remote, branch: [99])
    code, out = _run(capsys, last_result, "gathered")
    assert code == 0 and out["outputs"]["gathered"] == Git(repo).rev_parse("HEAD")


def test_show_lists_the_context_keys_each_stage_output_needs(repo, capsys, last_result):
    _, made = _new(capsys, last_result)
    _, shown = _run(capsys, last_result, "show", made["metrics"]["machine_id"])
    outputs = {r["stage"]: r["outputs"] for r in shown["items"]}
    assert outputs["作業場所の用意"] == ["worktree", "branch"]
    assert outputs["設計"] == ["design_pr", "mvv_gate"]
    assert outputs["実装"] == ["gathered"]


@pytest.fixture
def outside(repo, tmp_path, monkeypatch):
    """git の外のディレクトリを作ってそこへ移る関数。移る時点はテストが決める。"""

    def move() -> Path:
        path = tmp_path / "outside"
        path.mkdir()
        monkeypatch.chdir(path)
        return path

    return move


def test_cwd_targets_the_repository_when_run_outside_git(repo, outside, capsys, last_result):
    outside_dir = outside()
    cwd_args = ("--cwd", str(repo))
    code, made = _new(capsys, last_result, *cwd_args)
    assert code == 0 and made["metrics"]["machine_id"] == model.machine_id("sprint", str(repo), "s1")
    tid = made["metrics"]["machine_id"]
    out_file = outside_dir / "plan.json"
    out_file.write_text(json.dumps({"steps": [], "summary": "手で計画した"}))
    assert _run(capsys, last_result, "finish", tid, "計画", "--output", str(out_file), "--reason", "r", *cwd_args)[0] == 0
    code, shown = _run(capsys, last_result, "show", tid, *cwd_args)
    assert code == 0 and {r["stage"]: r["label"] for r in shown["items"]}["計画"] == "終了"


def test_without_cwd_outside_git_still_stops_with_precondition(repo, outside, capsys, last_result):
    outside()
    _, out = _run(capsys, last_result, "show", "x")
    assert out["status"] == "stopped" and out["cause"]["kind"] == "precondition"


def test_cwd_reaches_the_show_of_the_ndf_progress_entry(repo, outside, capsys, last_result):
    """`ndf progress show` は工程を流す側（run）の show を通る。そちらも --cwd を受ける。"""
    from ndf.progress import gate

    _, made = _new(capsys, last_result)
    outside()
    code = gate.progress_main(["show", made["metrics"]["machine_id"], "--cwd", str(repo)])
    shown = last_result(capsys.readouterr().out)
    assert code == 0 and shown["status"] == "ok" and shown["items"][0]["outputs"] == []


# --- 流し直しで作業場所を引き継ぐ --carry（#3879 構成要素 9） ---------------------------------------


def test_new_disabling_only_stages_without_outputs_is_made_as_today(repo, capsys, last_result):
    """現状固定: 出力のキーを持たず承認ゲートも持たない工程だけを --disable した new は、今のとおり作れる。"""
    snap = definition.load_definition("standard")
    plain = [s.name for s in snap.stages if not s.outputs and not s.gates]
    disables = [arg for name in plain for arg in ("--disable", f"{name}=不要")]
    code, out = _new(capsys, last_result, *disables)
    assert code == 0 and out["status"] == "ok"
    assert sorted(out["metrics"]["disabled"]) == sorted(plain)


WORKTREE_OUT = {"worktree": "/work/repo/.worktrees/sprint/s1", "branch": "sprint/s1"}
REDESIGN = ("--disable", "要求と受け入れ条件=既にある", "--disable", "作業場所の用意=既にある")


def _sprint(capsys, last_result, name: str, *extra: str) -> tuple[int, dict]:
    return _run(capsys, last_result, "new", name, "--mode", "standard", "--issues", "1", *extra)


def _cancelled_with_worktree(capsys, last_result, tmp_path, outputs: dict | None = WORKTREE_OUT, cancel: bool = True) -> str:
    """s1 を作り、工程「作業場所の用意」を outputs で終え、中止する。ステートマシンの ID を返す。"""
    _, made = _sprint(capsys, last_result, "s1")
    tid = made["metrics"]["machine_id"]
    if outputs is not None:
        out_file = tmp_path / "worktree.json"
        out_file.write_text(json.dumps({"steps": [], "summary": "作業場所を用意した", "outputs": outputs}))
        assert _run(capsys, last_result, "finish", tid, "作業場所の用意", "--output", str(out_file), "--reason", "手で")[0] == 0
    if cancel:
        assert _run(capsys, last_result, "cancel", tid, "--reason", "設計へ戻す")[0] == 0
    return tid


def _refused_detail(out: dict) -> str:
    return " ".join(i["error"] for i in out["items"])


def test_carry_copies_the_disabled_stages_outputs_and_the_design_inputs_resolve(repo, capsys, tmp_path, last_result):
    src = _cancelled_with_worktree(capsys, last_result, tmp_path)
    code, out = _sprint(capsys, last_result, "s1-redesign", *REDESIGN, "--carry", src)
    assert code == 0 and out["status"] == "ok"
    _, doc = _stored(out["metrics"]["machine_id"])
    assert {k: doc["context"][k] for k in WORKTREE_OUT} == WORKTREE_OUT
    design = definition.load_definition("standard").stage("設計")
    picked = invoke.resolve_inputs(design, doc["context"])
    assert picked["worktree"] == WORKTREE_OUT["worktree"]


def test_disabling_a_stage_with_outputs_without_carry_is_refused(repo, capsys, last_result):
    code, out = _sprint(capsys, last_result, "s1", *REDESIGN)
    assert code == 2 and out["cause"]["kind"] == "invalid"
    assert "作業場所の用意" in _refused_detail(out) and "--carry" in _refused_detail(out)


def test_carry_from_a_machine_that_is_not_cancelled_is_refused(repo, capsys, tmp_path, last_result):
    src = _cancelled_with_worktree(capsys, last_result, tmp_path, cancel=False)
    code, out = _sprint(capsys, last_result, "s1-redesign", *REDESIGN, "--carry", src)
    assert code == 2 and "中止" in _refused_detail(out)


def test_carry_under_the_same_name_is_refused_with_the_original_name(repo, capsys, tmp_path, last_result):
    src = _cancelled_with_worktree(capsys, last_result, tmp_path)
    code, out = _sprint(capsys, last_result, "s1", *REDESIGN, "--carry", src)
    assert code == 2 and "元と違う名前" in _refused_detail(out) and "s1" in _refused_detail(out)


def test_carry_from_a_machine_of_other_issues_is_refused(repo, capsys, tmp_path, last_result):
    src = _cancelled_with_worktree(capsys, last_result, tmp_path)
    code, out = _run(capsys, last_result, "new", "s2", "--mode", "standard", "--issues", "2", *REDESIGN, "--carry", src)
    assert code == 2 and "issues" in _refused_detail(out)


def test_carry_without_the_output_keys_in_the_source_context_is_refused(repo, capsys, tmp_path, last_result):
    src = _cancelled_with_worktree(capsys, last_result, tmp_path, outputs=None)
    code, out = _sprint(capsys, last_result, "s1-redesign", *REDESIGN, "--carry", src)
    detail = _refused_detail(out)
    assert code == 2 and "作業場所の用意" in detail and "worktree" in detail and "branch" in detail


def test_carry_is_checked_even_when_no_disabled_stage_has_outputs(repo, capsys, tmp_path, last_result):
    """写すキーが無い --disable でも、--carry を渡したら写し元（中止・名前・課題番号）を確かめる。"""
    src = _cancelled_with_worktree(capsys, last_result, tmp_path, cancel=False)
    code, out = _sprint(capsys, last_result, "s1-redesign", "--disable", "振り返り=不要", "--carry", src)
    assert code == 2 and "中止" in _refused_detail(out)
    other = _run(capsys, last_result, "new", "s2", "--mode", "standard", "--issues", "2", "--carry", src)
    assert other[0] == 2


def test_carry_from_a_machine_of_another_repo_is_refused(repo, capsys, tmp_path, last_result):
    """NDF_PROGRESS_DB を共有する別リポジトリの、課題番号が同じ中止済みのステートマシンからは写さない。"""
    src = _cancelled_with_worktree(capsys, last_result, tmp_path)
    root = command.store_root_of(Path.cwd())
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        conn.execute(store.MACHINES.update().where(store.MACHINES.c.id == src).values(repo="https://example.com/other.git"))
    code, out = _sprint(capsys, last_result, "s1-redesign", *REDESIGN, "--carry", src)
    assert code == 2 and "リポジトリ" in _refused_detail(out)


def test_loop_with_carry_is_refused(repo, capsys, last_result):
    code, out = _run(capsys, last_result, "new", "l1", "--loop", "dev", "--parent", "lane-x", "--carry", "missing")
    assert code == 2 and "--carry" in _refused_detail(out)
