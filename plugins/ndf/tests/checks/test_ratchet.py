from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from ndf import identity
from ndf.checks import ratchet, run
from ndf.checks.model import CheckError
from ndf.contracts import exit_codes

ROW = {"max": 5, "measured": "2026-01-01", "commit": "c0"}


def body(count: int, **rows) -> str:
    return json.dumps({"version": 1, "rules": {"copy-dup": {**ROW, "max": count}, **rows}})


def limits(count: int) -> dict[str, ratchet.Baseline]:
    return {"copy-dup": ratchet.Baseline(count, "2026-01-01", "c0")}


@pytest.mark.parametrize(
    ("count", "current", "base", "words"),
    [
        (5, 5, {}, []),
        (5, 5, {"copy-dup": 5}, []),
        (6, 5, {"copy-dup": 5}, ["6 件", "5 件"]),  # 足した
        (4, 5, {"copy-dup": 5}, ["--write-spec"]),  # 減らして基準を据え置いた
        (4, 4, {"copy-dup": 5}, []),  # 減らして基準も下げた
        (6, 6, {"copy-dup": 5}, ["引き上げ"]),  # 基準を上げて増加を通そうとした
        (6, 6, {}, []),  # 変更の前の基準が無い
        (6, 6, {"other": 0}, []),  # 変更の前の基準に規則が無い
        (7, 6, {"copy-dup": 5}, ["7 件", "引き上げ"]),
        (0, 0, {"copy-dup": 1}, []),
    ],
)
def test_the_count_must_equal_a_limit_that_never_rises(count, current, base, words):
    why = ratchet.verdicts({"copy-dup": count}, limits(current), {rule: limits(n)["copy-dup"] for rule, n in base.items()})
    assert bool(why) == bool(words)
    assert all(word in why["copy-dup"] for word in words)


def test_a_rule_without_a_limit_stops():
    with pytest.raises(CheckError, match="fresh"):
        ratchet.verdicts({"fresh": 0}, limits(5), {})


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        json.dumps({"version": 2, "rules": {"copy-dup": ROW}}),
        json.dumps({"version": True, "rules": {"copy-dup": ROW}}),
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "rules": {"copy-dup": 5}}),
        json.dumps({"version": 1, "rules": {"copy-dup": {**ROW, "max": True}}}),
        json.dumps({"version": 1, "rules": {"copy-dup": {**ROW, "max": -1}}}),
        json.dumps({"version": 1, "rules": {"copy-dup": {"max": 5, "measured": "2026-01-01"}}}),
        json.dumps({"version": 1, "rules": {"copy-dup": {**ROW, "commit": ""}}}),
        json.dumps({"version": 1, "rules": {"copy-dup": {**ROW, "unknown": 1}}}),
        '{"version": 1, "rules": {}, "rules": {}}',
    ],
)
def test_a_broken_limit_file_stops(tmp_path, text):
    path = tmp_path / "ratchet.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CheckError):
        ratchet.baseline(path)


def test_a_missing_or_undecodable_limit_file_stops(tmp_path):
    with pytest.raises(CheckError):
        ratchet.baseline(tmp_path / "none.json")
    with pytest.raises(CheckError):
        ratchet.limits_of(b"\xff", "x")


def test_lower_rewrites_only_the_rules_below_their_limit(tmp_path):
    path = tmp_path / "ratchet.json"
    path.write_text(body(5, kept={**ROW, "max": 2}), encoding="utf-8")
    ratchet.lower(path, {"copy-dup": 3, "kept": 2}, ratchet.baseline(path), "c1", "2026-02-02")
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "rules": {"copy-dup": {"max": 3, "measured": "2026-02-02", "commit": "c1"}, "kept": {**ROW, "max": 2}},
    }


@pytest.mark.parametrize(("count", "below"), [(4, ["copy-dup"]), (5, []), (6, [])])
def test_below_names_the_rules_under_their_limit(count, below):
    assert ratchet.below({"copy-dup": count}, limits(5)) == below


def test_lower_raises_when_the_file_cannot_be_written(tmp_path, monkeypatch):
    path = tmp_path / "ratchet.json"
    path.write_text(body(5), encoding="utf-8")
    monkeypatch.setattr(Path, "write_text", _refused)
    with pytest.raises(ratchet.RatchetError):
        ratchet.lower(path, {"copy-dup": 3}, limits(5), "c1", "2026-02-02")


def _refused(*_args, **_kwargs):
    raise PermissionError("denied")


def _answer(monkeypatch, plugin, objects: bytes = b"") -> list[str]:
    """git の応答を渡した値にする（`objects` は `cat-file --batch` の出力）。渡した標準入力を返す並びへ足す。"""
    asked = []
    answers = {"--show-toplevel": f"{plugin.repo}\n".encode(), "HEAD": b"c1\n", "--batch": objects}

    def git(_root, *args, stdin=""):
        asked.append(stdin)
        return answers[args[-1]]

    monkeypatch.setattr(run, "_git_stdout", git)
    return asked


def _object(kind: str, text: str) -> bytes:
    """`git cat-file --batch` が出す 1 つのもの（大きさはバイトの数）。"""
    return f"b1de8b43d6a9186071e727fd5c9e0b1a79de193d {kind} {len(text.encode())}\n{text}\n".encode()


def _file(text: str) -> bytes:
    return _object("blob", text)


@pytest.mark.parametrize(
    ("out", "found"),
    [
        (b"", []),
        (_file("abc"), [b"abc"]),
        (_file(""), [b""]),  # 空の本文は「無い」ではない
        (_file("\na\n\n"), [b"\na\n\n"]),
        (_file("日本語"), ["日本語".encode()]),
        (_object("blob", "x missing\ny ambiguous\n"), [b"x missing\ny ambiguous\n"]),  # 本文の中の同じ語
        (b"HEAD:a b.json missing\n", [None]),
        (b"b1de ambiguous\n", [None]),
        (b"HEAD:a.json missing", [None]),  # 末尾の改行が無い
        (b"x missing\ny ambiguous\nz missing\n", [None, None, None]),
        (_file("a") + b"x missing\n" + _file("") + b"y ambiguous\n" + _file("b\n"), [b"a", None, b"", None, b"b\n"]),
        (b"b1de blob 5\nabc", [b"abc"]),  # 途中で切れた本文
        (b"b1de blob 3\nabc", [b"abc"]),  # 本文の後の改行が無い
    ],
)
def test_each_batch_answer_is_its_body_or_none(out, found):
    assert run._git_objects(out) == found


@pytest.mark.parametrize("out", [b"b1de blob x\n", b"\n", b"missing\n", _file("a") + b"b1de submodule\n"])
def test_a_batch_answer_without_a_size_raises(out):
    with pytest.raises(ValueError):
        run._git_objects(out)


@pytest.fixture
def checked(plugin, monkeypatch, capsys, last_result):
    """`ndf check` を打ち（ruff と規約の規則を除く）、終了コードと結果 JSON を返す関数。"""
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    monkeypatch.setattr(run.lint, "scan", lambda *_: defaultdict(set))
    monkeypatch.setattr(run.spec_docs, "check", lambda *_: [])
    _answer(monkeypatch, plugin)
    plugin.write("src/ndf/a.py", "def twice():\n    return 1\n")
    plugin.write("src/ndf/b.py", "def twice():\n    return 1\n")

    def call(*argv: str):
        code = run.main(list(argv))
        return code, last_result(capsys.readouterr().out)

    return call


def test_findings_equal_to_the_limit_are_not_violations(plugin, checked):
    plugin.write(ratchet.RATCHET_REL, body(1))
    code, report = checked()
    assert code == 0 and report["items"] == [] and report["metrics"]["findings"] == {}


def test_one_more_finding_shows_the_count_and_lists_the_places_only_when_asked(plugin, checked):
    plugin.write(ratchet.RATCHET_REL, body(0))
    code, report = checked()
    assert code == exit_codes.code_of("failed")
    assert [(i["rule"], i["path"].rsplit("/", 1)[-1]) for i in report["items"]] == [("copy-dup", "ratchet.json")]
    _, report = checked("--only", "copy-dup")
    assert [i["path"].rsplit("/", 1)[-1] for i in report["items"]] == ["a.py", "ratchet.json"]


def test_a_check_that_does_not_select_the_rule_does_not_read_the_limit(plugin, checked):
    (plugin.root / ratchet.RATCHET_REL).unlink()
    assert checked("--only", "I3")[0] == 0


def test_a_kept_limit_fails_until_write_spec_lowers_it(plugin, checked):
    path = plugin.write(ratchet.RATCHET_REL, body(2))
    code, report = checked()
    assert code == exit_codes.code_of("failed")
    assert [i["path"].rsplit("/", 1)[-1] for i in report["items"]] == ["ratchet.json"]
    code, report = checked("--write-spec")
    assert code == 0 and report["metrics"]["written"] == 1 and report["items"] == []
    row = json.loads(path.read_text(encoding="utf-8"))["rules"]["copy-dup"]
    assert (row["max"], row["commit"]) == (1, "c1")


def test_write_spec_does_not_raise_the_limit(plugin, checked):
    path = plugin.write(ratchet.RATCHET_REL, body(0))
    assert checked("--write-spec")[0] == exit_codes.code_of("failed")
    assert path.read_text(encoding="utf-8") == body(0)


def test_an_unwritable_limit_file_stops_as_failed(plugin, checked, monkeypatch):
    plugin.write(ratchet.RATCHET_REL, body(2))
    monkeypatch.setattr(Path, "write_text", _refused)
    code, report = checked("--write-spec")
    assert code == exit_codes.code_of("failed") and report["status"] == "stopped" and report["items"] == []


def test_a_missing_limit_file_is_config_broken(plugin, checked):
    (plugin.root / ratchet.RATCHET_REL).unlink()
    code, report = checked()
    assert code == exit_codes.unreadable_code() and report["cause"]["kind"] == "config_broken"


def test_a_syntax_error_neither_judges_nor_lowers_the_limit(plugin, checked):
    path = plugin.write(ratchet.RATCHET_REL, body(1))
    plugin.write("src/ndf/b.py", "def twice(:\n")
    _, report = checked("--write-spec")
    assert [i["rule"] for i in report["items"]] == ["syntax"]
    assert path.read_text(encoding="utf-8") == body(1)


@pytest.mark.parametrize(
    ("second", "code"),
    [(_file(body(1)), 0), (_file(body(2)), 0), (_file(body(0)), 1), (b"HEAD^1:ratchet.json missing\n", 0), (_file("{"), 2)],
)
def test_the_limit_is_compared_with_the_one_before_the_change(plugin, checked, monkeypatch, second, code):
    plugin.write(ratchet.RATCHET_REL, body(1))
    asked = _answer(monkeypatch, plugin, _object("commit", "tree x\n\n日本語の件名\n") + second)
    assert checked("--ratchet-base", "HEAD^1")[0] == code
    rel = (plugin.root / ratchet.RATCHET_REL).relative_to(plugin.repo).as_posix()
    assert [a for a in asked if a] == [f"HEAD^1^{{commit}}\nHEAD^1:{rel}\n"]


def test_an_unknown_base_stops(plugin, checked, monkeypatch):
    plugin.write(ratchet.RATCHET_REL, body(1))
    _answer(monkeypatch, plugin, b"nope^{commit} missing\nnope:ratchet.json missing\n")
    code, report = checked("--ratchet-base", "nope")
    assert code == exit_codes.unreadable_code() and "nope" in report["summary"]


def test_the_plugin_limit_file_has_a_row_for_every_ratcheted_rule():
    assert set(ratchet.baseline(Path(ratchet.__file__).with_name("ratchet.json"))) == set(run.RATCHETED)
