from __future__ import annotations

import json
import os

import pytest

from ndf import cli, identity
from ndf.contracts import causes
from ndf.contracts import cli as contracts_cli
from ndf.contracts import exit_codes


def _run(capsys, *argv):
    code = cli.main(["contracts", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def _run_top(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def _emit(capsys, status, *extra):
    """`emit --tool w --status <status> --summary s` に、テストごとの差（cause・gap・route・out）を足して打つ。"""
    return _run(capsys, "emit", "--tool", "w", "--status", status, "--summary", "s", *extra)


def test_check_passes_on_plugin_contracts(capsys):
    code, out, _ = _run(capsys, "check")
    assert code == 0
    assert json.loads(out)["status"] == "ok"


def test_check_reports_every_broken_table(contracts_copy, capsys):
    contracts_copy("causes/unknown.json", text="{")
    contracts_copy("limits/_meta.json", lambda d: {**d, "version": 1})
    code, out, _ = _run(capsys, "check")
    report = json.loads(out)
    assert code == 2
    assert report["cause"]["kind"] == "invalid"
    assert len(report["items"]) >= 2


@pytest.mark.parametrize("rel", ["schemas/result.schema.json", "causes/unknown.json"])
def test_check_reports_json_when_output_contract_is_broken(contracts_copy, capsys, rel):
    contracts_copy(rel, text="{")
    code, out, _ = _run(capsys, "check")
    report = json.loads(out)
    assert code == exit_codes.code_of("unreadable")
    assert (report["status"], report["cause"]["kind"]) == ("stopped", "invalid")


def test_check_reports_missing_ref_target(contracts_copy, fact_table):
    table = fact_table({"phase": {"type": "string", "summary": "s", "ref": "limits#monitor.phase."}}, [{"id": "a", "phase": "nope"}])
    contracts_copy("facts/sample.json", lambda _: table)
    assert contracts_cli.check_all() == ["sample:a の phase 'nope' の方針 monitor.phase.nope が上限の設定に無い"]


def test_fact_ids_and_value(capsys):
    assert _run(capsys, "fact-ids", "exit-codes", "status=gate")[1].split() == ["gate", "pause"]
    assert _run(capsys, "fact-value", "exit-codes", "gate", "last")[1].strip() == "19"


def test_fact_value_prints_arrays_and_booleans(contracts_copy, fact_table, capsys):
    table = fact_table(
        {"tags": {"type": "array", "summary": "s"}, "flag": {"type": "boolean", "summary": "s"}},
        [{"id": "a", "tags": ["x", "y"], "flag": False}],
    )
    contracts_copy("facts/sample.json", lambda _: table)
    assert _run(capsys, "fact-value", "sample", "a", "tags")[1].split() == ["x", "y"]
    assert _run(capsys, "fact-value", "sample", "a", "flag")[1].strip() == "false"


def test_fact_lookup_error_is_one_stderr_line(capsys):
    code, out, err = _run(capsys, "fact-value", "exit-codes", "nope", "code")
    assert code == 2 and out == ""
    assert len(err.strip().splitlines()) == 1


def test_emit_stopped_with_cause_takes_the_kind_code(capsys, tmp_path):
    dest = tmp_path / "r.json"
    code, out, _ = _emit(capsys, "stopped", "--cause", "infra_wait", "Runner が付かない", "--out", str(dest))
    assert code == 75
    assert json.loads(out)["cause"]["kind"] == "infra_wait"
    assert json.loads(dest.read_text()) == json.loads(out)


def test_emit_incomplete_with_gaps(capsys):
    code, _, _ = _emit(capsys, "stopped", "--cause", "incomplete", "欠け", "--gap", "test", "x", "未確認")
    assert code == 6


def test_emit_gate_with_routes(capsys):
    go = {"argv": ["ndf", "go"], "ask": ["comment"]}
    code, out, _ = _emit(capsys, "gate", "--route", "approved", json.dumps(go), "--route", "rejected", "-")
    assert code == 10
    assert json.loads(out)["routes"] == {"approved": go, "rejected": None}


@pytest.mark.parametrize("args", [("approved", '{"argv": ["/x/ndf/10.0.0/scripts/go.py"]}'), ("maybe", '{"argv": ["ndf", "go"]}')])
def test_emit_refuses_routes_that_cannot_be_run_as_is(capsys, args):
    code, out, _ = _emit(capsys, "gate", "--route", *args)
    assert code == exit_codes.code_of("unreadable")
    assert json.loads(out)["cause"]["kind"] == "invalid" and "routes" not in json.loads(out)


def test_emit_refuses_a_route_that_is_not_an_object(capsys):
    code, out, _ = _emit(capsys, "gate", "--route", "approved", "ndf go")
    assert (code, out) == (exit_codes.code_of("unreadable"), "")


def test_emit_next_outputs_and_run_id(capsys):
    nxt = {"argv": ["ndf", "test-slot", "run", "--", "pytest"]}
    code, out, _ = _emit(capsys, "ok", "--next", json.dumps(nxt), "--outputs", '{"pr": 12, "branch": "feat/x"}', "--run-id", "r1")
    assert code == 0
    got = json.loads(out)
    assert (got["next"], got["outputs"], got["run_id"]) == (nxt, {"pr": 12, "branch": "feat/x"}, "r1")


def test_emit_refuses_stopped_without_cause_and_writes_nothing(capsys, tmp_path):
    dest = tmp_path / "r.json"
    code, out, err = _emit(capsys, "stopped", "--out", str(dest))
    assert code == 2 and json.loads(out)["cause"]["kind"] == "invalid"
    assert "結果の形の誤り" in err
    assert not dest.exists()


# 現状固定（I-007）: fact-value の値の取り出しと、断るときの理由の順（行 → 列の設定 → 行の列）。
@pytest.mark.parametrize(
    ("argv", "printed"),
    [
        (("exit-codes", "gate", "id"), "gate\n"),
        (("exit-codes", "ok", "code"), "0\n"),
        (("exit-codes", "ok", "label"), "成功\n"),
        (("exit-codes", "gate", "last"), "19\n"),
    ],
)
def test_fact_value_prints_one_value(capsys, argv, printed):
    assert _run(capsys, "fact-value", *argv) == (0, printed, "")


@pytest.mark.parametrize(
    ("argv", "err"),
    [
        (("exit-codes", "ok", "last"), "contracts: 事実の表 exit-codes の行 ok は列 last を持たない\n"),
        (("exit-codes", "ok", "nope"), "contracts: 事実の表 exit-codes に列 nope が無い\n"),
        (("exit-codes", "nope", "nope"), "contracts: 事実の表 exit-codes に行 'nope' が無い\n"),
        (("exit-codes", "nope", "code"), "contracts: 事実の表 exit-codes に行 'nope' が無い\n"),
        (("Bad", "a", "b"), "contracts: 表の名前が違う形: 'Bad'\n"),
    ],
)
def test_fact_value_refusals(capsys, argv, err):
    assert _run(capsys, "fact-value", *argv) == (2, "", err)


def test_fact_value_missing_table(capsys):
    code, out, err = _run(capsys, "fact-value", "no-such", "a", "b")
    assert (code, out) == (2, "")
    assert err.startswith("contracts: コントラクト ") and "facts/no-such.json を読めない" in err


# 現状固定（I-010）: check_all が確かめるスキーマの名前と、壊れた 1 ファイルごとの誤りの数と順。
@pytest.mark.parametrize(
    ("broken", "count"),
    [
        ("schemas/result.schema.json", 2),
        ("schemas/causes.schema.json", 2),
        ("schemas/limits.schema.json", 3),
        ("schemas/release-verification.schema.json", 1),
        ("schemas/split-meta.schema.json", 2),
        ("causes/unknown.json", 1),
        ("causes/_meta.json", 1),
        ("facts/exit-codes.json", 2),
        ("facts/answers.json", 2),
        ("schemas/route-chosen.schema.json", 2),
        ("schemas/version.schema.json", 2),
        ("version.json", 1),
    ],
)
def test_check_all_reports_one_broken_file(contracts_copy, broken, count):
    path = contracts_copy(broken, text="{")
    errs = contracts_cli.check_all()
    assert len(errs) == count
    assert all(e.startswith(f"コントラクト {path} が JSON として壊れている: ") for e in errs)


def test_check_all_broken_limits_also_breaks_causes(contracts_copy):
    path = contracts_copy("limits/cause.ci-pending.json", text="{")
    errs = contracts_cli.check_all()
    assert len(errs) == 2
    assert errs[0].startswith(f"コントラクト {path} が JSON として壊れている: ")
    assert errs[1].startswith("停止理由の表 causes の誤り: ci_pending の再試行の方針: ")


def test_check_all_ignores_schemas_outside_its_list(contracts_copy):
    contracts_copy("schemas/extra.schema.json", text="{")
    assert contracts_cli.check_all() == []


# 現状固定（I-011）: emit の --code の扱いと、--out を書くのは形と終了コードが合うときだけであること。
def test_emit_explicit_code_writes_out(capsys, tmp_path):
    dest = tmp_path / "r.json"
    code, out, err = _emit(capsys, "ok", "--code", "0", "--out", str(dest))
    line = '{"tool": "w", "status": "ok", "summary": "s", "items": [], "metrics": {}}\n'
    assert (code, out, err) == (0, line, "")
    assert dest.read_text(encoding="utf-8") == line


@pytest.mark.parametrize(
    ("status", "extra", "err"),
    [
        ("ok", ("--code", "739241"), "結果の形の誤り: status ok と終了コードが合わない\n"),
        (
            "stopped",
            ("--cause", "infra_wait", "d", "--code", "2"),
            "結果の形の誤り: cause.kind infra_wait の終了コードは 75 で、渡された終了コードと合わない\n",
        ),
        ("stopped", ("--cause", "nope", "d"), "結果の形の誤り: cause.kind が停止理由の表に無い\n"),
        ("gate", (), "結果の形の誤り: allOf/1/then/required（required）: 'routes' is a required property\n"),
    ],
)
def test_emit_refusals_write_no_file(capsys, tmp_path, status, extra, err):
    dest = tmp_path / "r.json"
    code, out, told = _emit(capsys, status, *extra, "--out", str(dest))
    assert (code, told) == (2, err)
    assert json.loads(out)["tool"] == "ndf" and json.loads(out)["cause"]["kind"] == "invalid"
    assert "739241" not in out
    assert not dest.exists()


def test_emit_passes_every_field(capsys):
    code, out, _ = _emit(
        capsys,
        "stopped",
        "--cause",
        "infra_wait",
        "d",
        "--cause-instance",
        "i1",
        "--retry-after",
        "30",
        "--item",
        '{"a": 1}',
        "--metrics",
        '{"n": 2}',
        "--next",
        '{"argv": ["ndf", "nx"]}',
        "--presentation-path",
        "p.md",
    )
    assert code == 75
    assert json.loads(out) == {
        "tool": "w",
        "status": "stopped",
        "summary": "s",
        "items": [{"a": 1}],
        "metrics": {"n": 2},
        "cause": {"kind": "infra_wait", "detail": "d", "instance": "i1", "retry_after": 30.0},
        "presentation_path": "p.md",
        "next": {"argv": ["ndf", "nx"]},
    }


# 現状固定（I-011）: --out の既存ファイルは断ったとき書き換えず（標準出力には代わりの stopped を出す）、書けない書き込み先では標準出力へも出さない。
def test_emit_mismatched_code_keeps_existing_out(capsys, tmp_path):
    dest = tmp_path / "r.json"
    dest.write_text("old\n", encoding="utf-8")
    code, out, err = _emit(capsys, "ok", "--code", "3", "--out", str(dest))
    assert (code, err) == (2, "結果の形の誤り: status ok と終了コードが合わない\n") and json.loads(out)["status"] == "stopped"
    assert dest.read_text(encoding="utf-8") == "old\n"


@pytest.mark.parametrize("rel", ["causes/invalid.json", "facts/exit-codes.json"])
def test_unreadable_contracts_stop_with_config_broken(contracts_copy, capsys, rel):
    contracts_copy(rel, text="{")
    code, out, _ = _run_top(capsys, "no-such-command")
    report = json.loads(out)
    assert code == exit_codes.UNREADABLE_FALLBACK
    assert (report["status"], report["cause"]["kind"]) == ("stopped", "config_broken")


def test_unreadable_fallback_matches_the_table():
    assert exit_codes.UNREADABLE_FALLBACK == exit_codes.code_of("unreadable")


def test_emit_unwritable_out_is_invalid(capsys, tmp_path):
    code, out, err = _emit(capsys, "ok", "--out", str(tmp_path / "no-such" / "r.json"))
    assert (code, out) == (2, "")
    assert err.startswith("contracts: ") and err.count("\n") == 1


def test_emit_refuses_an_out_that_is_a_directory(capsys, tmp_path):
    # #3877: 通常のファイルでない --out はリンクと同じく stopped（invalid）の結果 JSON で断る
    code, out, _ = _emit(capsys, "ok", "--out", str(tmp_path / "."))
    got = json.loads(out)
    assert (code, got["status"], got["cause"]["kind"]) == (2, "stopped", "invalid") and "directory" in got["summary"]


@pytest.mark.parametrize("metrics", ["1", "[]", '"s"', "null"])
def test_emit_non_object_metrics_is_invalid(capsys, last_result, metrics):
    # 使い方の誤りは stopped/invalid の結果 JSON で終わる（#3394 の 3394-3）
    code, out, err = _emit(capsys, "ok", "--metrics", metrics)
    assert code == causes.exit_code("invalid")
    assert last_result(out)["cause"]["kind"] == "invalid"
    assert "JSON のオブジェクトでない" in err


# #3877: --out はリンクをたどらずに書く。リンクの先も、共有する inode も書き換えない
@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_emit_refuses_an_out_that_links_outside(capsys, tmp_path, link):
    outside = tmp_path / "outside.json"
    outside.write_text("利用者のファイル\n", encoding="utf-8")
    work = tmp_path / "w"
    work.mkdir()
    dest = work / "r.json"
    dest.symlink_to(outside) if link == "symlink" else dest.hardlink_to(outside)
    code, out, _ = _emit(capsys, "ok", "--out", str(dest))
    got = json.loads(out)
    assert (code, got["status"], got["cause"]["kind"]) == (causes.exit_code("invalid"), "stopped", "invalid")
    assert str(dest) in got["summary"] and link in got["summary"]
    assert outside.read_text(encoding="utf-8") == "利用者のファイル\n"


def test_emit_keeps_a_read_only_out(capsys, tmp_path):
    dest = tmp_path / "r.json"
    dest.write_text("old\n", encoding="utf-8")
    dest.chmod(0o444)
    code, out, err = _emit(capsys, "ok", "--out", str(dest))
    assert (code, out) == (causes.exit_code("invalid"), "") and err.startswith("contracts: ")
    assert dest.read_text(encoding="utf-8") == "old\n"


def test_emit_refuses_an_out_whose_dotdot_cancels_a_link(capsys, tmp_path, monkeypatch):
    # #3877 r1: `alias/..` を文字列の上で正規化しない。alias の先の親でも、文字列の上で正規化した場所でもなく、書く前に断る
    (tmp_path / "far" / "inner").mkdir(parents=True)
    work = tmp_path / "w"
    work.mkdir()
    (work / "alias").symlink_to(tmp_path / "far" / "inner")
    (work / "r.json").write_text("利用者のファイル\n", encoding="utf-8")
    monkeypatch.chdir(work)
    code, out, _ = _emit(capsys, "ok", "--out", "alias/../r.json")
    got = json.loads(out)
    assert (code, got["status"], got["cause"]["kind"]) == (causes.exit_code("invalid"), "stopped", "invalid")
    assert (work / "r.json").read_text(encoding="utf-8") == "利用者のファイル\n" and not (tmp_path / "far" / "r.json").exists()


@pytest.mark.parametrize("same", [True, False])
def test_emit_checks_the_result_dir_handed_by_worker_run(capsys, tmp_path, monkeypatch, same):
    # #3877 r1: worker run が開いた保存先と違うディレクトリ（同名への差し替え）へは書かない
    work = tmp_path / "w"
    work.mkdir()
    st = (work if same else tmp_path).stat()
    monkeypatch.setenv(identity.WORKER_RESULT_DIR_ENV, f"{st.st_dev}:{st.st_ino}:{work}")
    code, out, _ = _emit(capsys, "ok", "--out", str(work / "r.json"))
    got = json.loads(out)
    if same:
        assert (code, got["status"]) == (0, "ok") and (work / "r.json").exists()
    else:
        assert (got["status"], got["cause"]["kind"]) == ("stopped", "invalid") and "replaced" in got["summary"]
        assert not (work / "r.json").exists()


def test_emit_keeps_the_mode_of_an_existing_out(capsys, tmp_path):
    dest = tmp_path / "r.json"
    dest.write_text("old\n", encoding="utf-8")
    dest.chmod(0o644)
    code, _, _ = _emit(capsys, "ok", "--out", str(dest))
    assert code == 0 and dest.stat().st_mode & 0o777 == 0o644 and json.loads(dest.read_text(encoding="utf-8"))["status"] == "ok"


@pytest.mark.parametrize("suffix", ["/", "/.", "/.."])
def test_emit_refuses_an_out_written_as_a_directory_without_touching_the_file(capsys, tmp_path, suffix):
    # #3877 r3: `Path` は末尾の `/`・`/.` を落とす。生の引数でディレクトリを指す形なら、同名のファイルを書き換えない
    dest = tmp_path / "r.json"
    dest.write_text("利用者のファイル\n", encoding="utf-8")
    code, out, _ = _emit(capsys, "ok", "--out", str(dest) + suffix)
    got = json.loads(out)
    assert (code, got["status"], got["cause"]["kind"]) == (causes.exit_code("invalid"), "stopped", "invalid")
    assert dest.read_text(encoding="utf-8") == "利用者のファイル\n"


def test_emit_writes_the_worker_result_without_write_access_for_others(capsys, tmp_path, monkeypatch):
    # PR 4307 の r8-6: worker の結果ファイルは私有で書く（`ndf worker run` が共有の保存先で私有の結果だけを受ける）
    work = tmp_path / "w"
    work.mkdir()
    st = work.stat()
    monkeypatch.setenv(identity.WORKER_RESULT_DIR_ENV, f"{st.st_dev}:{st.st_ino}:{work}")
    before = os.umask(0o000)
    try:
        code, _, _ = _emit(capsys, "ok", "--out", str(work / "r.json"))
    finally:
        os.umask(before)
    assert code == 0 and (work / "r.json").stat().st_mode & 0o022 == 0
