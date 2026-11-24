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
        (result.result("t", "gate", "判断を待つ", routes={"request_changes": result.command("loop", "answer", ask=("comment",))}), 20),
        (_stopped("fix_limit", routes={"retry": result.command("retry")}), 1),
        (_stopped("wait_timeout", next=result.command("test-slot", "run", "--", "pytest")), 3),
        (result.result("t", "ok", "済んだ", outputs={"pr": 3, "issues": [1, 2], "branch": "feat/x", "product_version": "11.0.0-dev.3"}), 0),
        (result.result("t", "ok", "済んだ", run_id="r1"), 0),
        ({**result.result("t", "ok", "済んだ"), "version": 1}, 0),
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
        (result.result("t", "ok", "済んだ", outputs={"product_version": "ndf v11.0.0"}), 0),
        (result.result("t", "ok", "済んだ", outputs={"version": "11.0.0"}), 0),
        (_stopped(gaps=[GAP]), 1),
        (_stopped("incomplete"), 6),
        (_stopped("no_such_kind"), 1),
        (_stopped("base_conflict"), 1),
        (_stopped(), 10),
        (result.result("t", "ok", "済んだ"), 1),
        ({**result.result("t", "ok", "済んだ"), "extra": 1}, 0),
        *[({**result.result("t", "ok", "済んだ"), "version": v}, 0) for v in (0, 2, 2.0, "1", True, None)],
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
    assert result.answer_errors(routes) == ["routes のキーが回答の語彙（facts/answers.json）に無い"]
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


def _instead(obj, code=None):
    """形の違う結果を出したときの、標準出力の 1 行（代わりの結果）。"""
    out = io.StringIO()
    assert result.emit(obj, code, stream=out) == 2
    (line,) = out.getvalue().splitlines()
    got = json.loads(line)
    assert result.errors(got, 2) == [] and got["cause"]["kind"] == "invalid"
    return got


def test_emit_stops_instead_of_a_wrong_shape(capsys):
    got = _instead(result.result("t", "stopped", "理由なし"))
    assert got["tool"] == "t" and got["items"] == []
    assert "形が違う（1 件）" in got["summary"] and "required" in got["summary"]
    assert "結果の形の誤り" in capsys.readouterr().err


SECRET = "sk-test-secret"


@pytest.mark.parametrize(
    "obj",
    [
        result.result("t", SECRET, "済んだ"),
        result.result("t", "ok", "済んだ", items=[SECRET]),
        result.result("t", "ok", "済んだ", items=[{"severity": SECRET}]),
        result.result("t", "ok", "済んだ", outputs={SECRET: SECRET}),
        result.result("t", "ok", "済んだ", routes={SECRET: None}),
        result.result("t", "stopped", SECRET, cause={"kind": SECRET, "detail": SECRET}),
        {**result.result("t", "ok", "済んだ"), SECRET: {"token": SECRET}},
        {**result.result("t", "ok", "済んだ"), "version": SECRET},
        result.result(f"{SECRET} x", "ok", "済んだ", outputs={"pr": SECRET}),
        SECRET,
    ],
)
def test_the_result_put_out_instead_and_the_errors_have_no_value_of_the_wrong_one(obj, capsys):
    assert SECRET not in json.dumps(_instead(obj, 0), ensure_ascii=False)
    err = capsys.readouterr().err
    assert "結果の形の誤り" in err and SECRET not in err


@pytest.mark.parametrize(
    ("tool", "kept"), [("some-tool", "some-tool"), ("T", "ndf"), ("a b", "ndf"), ("t\n", "ndf"), ("", "ndf"), (1, "ndf"), (None, "ndf")]
)
def test_the_result_put_out_instead_keeps_only_a_tool_of_the_known_form(tool, kept):
    assert _instead({**result.result("t", "stopped", "理由なし"), "tool": tool})["tool"] == kept


def test_emit_puts_out_an_unchecked_result_when_the_causes_are_unreadable(contracts_copy, capsys):
    contracts_copy("causes/invalid.json", text="{")
    code = result.emit({**result.result("t", "ok", "済んだ"), "extra": SECRET})
    (line,) = capsys.readouterr().out.splitlines()
    assert code == exit_codes.unreadable_code() and json.loads(line)["cause"]["kind"] == "invalid" and SECRET not in line


def test_a_result_of_a_newer_version_is_received_as_stopped():
    got = result.received({**result.result("t", "ok", "済んだ"), "version": 2})
    assert got is not None and got["tool"] == "t" and got["cause"]["kind"] == "precondition" and "プラグインを更新する" in got["summary"]
    assert result.received(result.result("t", "ok", "済んだ")) == result.result("t", "ok", "済んだ")
    assert result.received({**result.result("t", "ok", "済んだ"), "version": "1"}) is None


@pytest.mark.parametrize(("version", "ok"), [("10.27.57", True), ("11.0.0-dev.2", True), ("ndf v10.27.57", False), ("v10.27.57", False)])
def test_release_verification_version_is_bare(version, ok):
    # #3115: 対象のバージョンは前置きを持たない版数だけで、本番のバージョンと文字列のまま検証できる
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


# 現状固定（I-011）: `_schema_validator` の引き方（ファイル全体・`#/$defs/<名前>`・誤りの扱い・保存先を差し替えるまで読み直さない）
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


# 現状固定（I-012）: `use_dir` は保存先を差し替え（`None` でプラグインの保存先へ戻し）、読み手のキャッシュを捨てる
def test_use_dir_switches_and_resets_the_location(tmp_path):
    contracts.use_dir(tmp_path)
    assert contracts.contracts_dir() == tmp_path
    contracts.use_dir(str(tmp_path))
    assert contracts.contracts_dir() == tmp_path
    contracts.use_dir(None)
    assert contracts.contracts_dir() == contracts.DEFAULT_DIR


def test_use_dir_makes_cached_readers_read_again(tmp_path):
    (tmp_path / "probe.json").write_text('{"n": 1}', encoding="utf-8")
    contracts.use_dir(tmp_path)
    calls = []

    @contracts.cached_reader
    def read():
        calls.append(1)
        return contracts.load_json("probe.json")

    assert read() == {"n": 1}
    (tmp_path / "probe.json").write_text('{"n": 2}', encoding="utf-8")
    assert read() == {"n": 1}
    assert len(calls) == 1
    contracts.use_dir(tmp_path)
    assert read() == {"n": 2}
    assert len(calls) == 2


def test_use_dir_changes_where_load_json_reads(tmp_path):
    (tmp_path / "probe.json").write_text('{"n": 1}', encoding="utf-8")
    contracts.use_dir(tmp_path)
    assert contracts.load_json("probe.json") == {"n": 1}
    contracts.use_dir(None)
    with pytest.raises(contracts.ContractError) as exc:
        contracts.load_json("probe.json")
    assert str(exc.value).startswith(f"コントラクト {contracts.DEFAULT_DIR / 'probe.json'} を読めない: ")
