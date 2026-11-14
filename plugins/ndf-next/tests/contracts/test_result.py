from __future__ import annotations

import io
import json

import pytest

from ndf import contracts
from ndf.contracts import exit_codes, result

GAP = {"kind": "record", "name": "x", "reason": "書けなかった"}
GO = result.command("go")


def _stopped(kind="test_failed", **extra):
    return result.result("t", "stopped", "止まった", cause={"kind": kind, "detail": "理由"}, **extra)


@pytest.mark.parametrize(
    ("obj", "code"),
    [
        (result.result("t", "ok", "済んだ"), 0),
        (_stopped(), 1),
        (_stopped("deps_missing"), 69),
        (_stopped("incomplete", gaps=[GAP]), 6),
        (result.result("t", "gate", "承認を待つ", routes={"approved": GO, "rejected": None}), 10),
        (result.result("t", "gate", "判断を待つ", routes={"revise": result.command("loop", "answer", ask=("comment",))}), 20),
        (_stopped("fix_limit", routes={"retry": result.command("retry")}), 1),
        (_stopped("wait_timeout", next=result.command("test-slot", "run", "--", "pytest")), 3),
        (result.result("t", "ok", "済んだ", outputs={"pr": 3, "issues": [1, 2], "branch": "feat/x", "version": "11.0.0-dev.3"}), 0),
        (result.result("t", "ok", "済んだ", run_id="r1"), 0),
    ],
)
def test_valid_results(obj, code):
    assert result.errors(obj, code) == []


@pytest.mark.parametrize(
    ("obj", "code"),
    [
        (result.result("t", "stopped", "理由なし"), 1),
        (result.result("t", "ok", "済んだ", cause={"kind": "unknown", "detail": "x"}), 0),
        (result.result("t", "gate", "承認を待つ", next=GO), 10),
        (result.result("t", "ok", "済んだ", routes={"approved": GO}), 0),
        # next・routes はそのまま打てる引数の配列で、先頭はエントリポイントの名前 ndf。文やパスを書かない（#3366）
        (result.result("t", "gate", "承認を待つ", routes={"approved": "ndf x"}), 10),
        (result.result("t", "gate", "承認を待つ", routes={"approved": {"argv": ["/p/ndf/10.27.62/scripts/x.py"]}}), 10),
        (result.result("t", "gate", "承認を待つ", routes={"approved": {"argv": ["ndf"]}}), 10),
        (result.result("t", "gate", "承認を待つ", routes={"approved": {"argv": ["ndf", "x"], "ask": ["<ID>"]}}), 10),
        (result.result("t", "gate", "承認を待つ", routes={"maybe": GO}), 10),
        (_stopped("wait_timeout", next="ndf test-slot run"), 3),
        # outputs は閉じた語彙の型付きの欄（#3367）
        (result.result("t", "ok", "済んだ", outputs={"pr": "12"}), 0),
        (result.result("t", "ok", "済んだ", outputs={"anything": 1}), 0),
        (result.result("t", "ok", "済んだ", outputs={"version": "ndf v11.0.0"}), 0),
        (_stopped(gaps=[GAP]), 1),
        (_stopped("incomplete"), 6),
        (_stopped("no_such_kind"), 1),
        (_stopped("base_conflict"), 1),
        (_stopped(), 10),
        (result.result("t", "ok", "済んだ"), 1),
        ({**result.result("t", "ok", "済んだ"), "extra": 1}, 0),
        ({"tool": "t", "status": "ok", "summary": "s", "items": []}, 0),
        (_stopped(process=[{"run": " ", "result": "x"}]), 1),
        ("not an object", None),
    ],
)
def test_wrong_results(obj, code):
    assert result.errors(obj, code)


def test_code_for_takes_stopped_code_from_kind():
    assert result.code_for(_stopped("precondition")) == 3
    assert result.code_for(result.result("t", "gate", "g", routes={"cancel": None})) == 10
    assert result.code_for(result.result("t", "stopped", "s")) is None


def test_command_builds_the_argv_with_the_entry_name():
    assert result.command("loop", "answer", ask=("comment",)) == {"argv": ["ndf", "loop", "answer"], "ask": ["comment"]}
    assert result.command("go") == {"argv": ["ndf", "go"]}


def test_answer_words_are_the_answers_table(contracts_copy):
    routes = {"approved": GO, "hold": GO}
    assert result.answer_errors(routes) == ["routes のキー 'hold' は答えの語彙（facts/answers.json）に無い"]
    contracts_copy("facts/answers.json", lambda t: {**t, "rows": [*t["rows"], {"id": "hold", "label": "保留する"}]})
    assert result.answer_errors(routes) == []


def test_stopped_helper_builds_cause():
    obj = result.stopped("t", "push_rejected", "hook が断った", instance="pre-push")
    assert obj["summary"] == "hook が断った"
    assert obj["cause"] == {"kind": "push_rejected", "detail": "hook が断った", "instance": "pre-push"}
    assert result.errors(obj) == []


def test_emit_prints_one_line_and_returns_the_kind_code():
    out = io.StringIO()
    assert result.emit(_stopped("base_conflict"), stream=out) == 4
    lines = out.getvalue().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["cause"]["kind"] == "base_conflict"


def test_emit_refuses_wrong_shape(capsys):
    out = io.StringIO()
    assert result.emit(result.result("t", "stopped", "理由なし"), stream=out) == 2
    assert out.getvalue() == ""
    assert "結果の形の誤り" in capsys.readouterr().err


@pytest.mark.parametrize(("version", "ok"), [("10.27.57", True), ("11.0.0-dev.2", True), ("ndf v10.27.57", False), ("v10.27.57", False)])
def test_release_verification_version_is_bare(version, ok):
    # #3115: 対象の版は前置きを持たない版数だけで、本番の版と文字列のまま照合できる
    record = {"version": version, "issues": [{"number": 3115, "result": "passed", "evidence": "PR #3102"}]}
    assert (contracts.schema_errors(record, contracts.schema_path("release-verification")) == []) is ok


# 現状固定（I-009）: exit-codes の区分の範囲の読み方（始点・終点・範囲外・last の省略・範囲内の非整数）。
def _stopped_range(data):
    data["rows"] = [{**r, "last": 89} if r["id"] == "deps_missing" else r for r in data["rows"]]
    return data


def test_stopped_range_is_read_from_code_to_last(contracts_copy):
    contracts_copy("facts/exit-codes.json", _stopped_range)
    assert exit_codes.codes("stopped") == frozenset([1, 2, 3, 4, 5, 6, *range(69, 90), 75])
    assert [exit_codes.matches("stopped", c) for c in (68, 69, 80, 89, 90, 75, 7)] == [False, True, True, True, False, True, False]
    assert [exit_codes.matches("gate", c) for c in (9, 10, 19, 20, 29, 30)] == [False, True, True, True, True, False]
    assert exit_codes.matches("ok", 0) and not exit_codes.matches("ok", 1)


def test_exit_codes_matches_compares_non_integers_by_range():
    assert exit_codes.matches("gate", 10.5)
    assert not exit_codes.matches("stopped", 3.5)
    assert not exit_codes.matches("ok", 0.5)


# 現状固定（I-011）: `_schema_validator` の引き方（ファイル全体・`#/$defs/<名前>`・誤りの扱い・置き場を差し替えるまで読み直さない）
_DEFS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["whole"],
    "$defs": {"num": {"type": "integer"}},
}


def test_schema_validator_reads_whole_file_and_defs_pointer(contracts_copy):
    contracts_copy("schemas/probe.schema.json", lambda _: _DEFS_SCHEMA)
    whole = contracts.schema_path("probe")
    assert contracts.schema_errors({}, whole) == ["(最上位): 'whole' is a required property"]
    assert contracts.schema_errors({"whole": 1}, whole) == []
    part = f"{whole}#/$defs/num"
    assert contracts.schema_errors(3, part) == []
    assert contracts.schema_errors("x", part) == ["(最上位): 'x' is not of type 'integer'"]


def test_schema_validator_refuses_pointer_without_defs(contracts_copy):
    contracts_copy("schemas/probe.schema.json", lambda _: {"type": "object"})
    rel = f"{contracts.schema_path('probe')}#/$defs/num"
    with pytest.raises(contracts.ContractError) as exc:
        contracts.schema_errors({}, rel)
    assert str(exc.value) == f"スキーマ schemas/probe.schema.json に $defs が無い（{rel} を引けない）"


def test_schema_validator_refuses_invalid_schema(contracts_copy):
    contracts_copy("schemas/probe.schema.json", lambda _: {"type": 5})
    rel = contracts.schema_path("probe")
    with pytest.raises(contracts.ContractError) as exc:
        contracts.schema_errors({}, rel)
    assert str(exc.value).startswith(f"スキーマ {contracts_copy.root / rel} がスキーマとして誤り: ")


def test_schema_validator_reads_once_until_use_dir(contracts_copy):
    contracts_copy("schemas/probe.schema.json", lambda _: {"type": "object"})
    rel = contracts.schema_path("probe")
    assert contracts.schema_errors([], rel) == ["(最上位): [] is not of type 'object'"]
    (contracts_copy.root / rel).write_text(json.dumps({"type": "array"}), encoding="utf-8")
    assert contracts.schema_errors([], rel) == ["(最上位): [] is not of type 'object'"]
    contracts.use_dir(contracts_copy.root)
    assert contracts.schema_errors([], rel) == []


def test_require_schema_raises_reader_error_with_label(contracts_copy):
    class ReaderError(contracts.ContractError):
        pass

    contracts_copy("schemas/probe.schema.json", lambda _: _DEFS_SCHEMA)
    with pytest.raises(ReaderError) as exc:
        contracts.require_schema({}, contracts.schema_path("probe"), ReaderError, "表")
    assert str(exc.value) == "表 の形が違う: (最上位): 'whole' is a required property"
