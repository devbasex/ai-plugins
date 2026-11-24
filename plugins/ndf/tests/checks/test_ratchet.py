from __future__ import annotations

import hashlib
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
    counts, rows = {"copy-dup": 3, "kept": 2}, ratchet.baseline(path)
    assert ratchet.below(counts, rows) == ["copy-dup"]
    assert ratchet.lower(path, counts, rows, "c1", "2026-02-02") == ratchet.baseline(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "rules": {"copy-dup": {"max": 3, "measured": "2026-02-02", "commit": "c1"}, "kept": {**ROW, "max": 2}},
    }


@pytest.mark.parametrize("count", [5, 6])
def test_a_count_not_below_its_limit_is_not_lowered(count):
    assert ratchet.below({"copy-dup": count}, limits(5)) == []


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


def _paths(report) -> list[str]:
    return [i["path"].rsplit("/", 1)[-1] for i in report["items"]]


def test_one_more_finding_lists_all_the_places_only_when_the_rule_is_selected(plugin, checked):
    plugin.write(ratchet.RATCHET_REL, body(0))
    code, report = checked()
    assert code == exit_codes.code_of("failed")
    assert _paths(report) == ["ratchet.json"] and "1 件" in report["items"][0]["detail"]
    code, report = checked("--only", "copy-dup")
    assert code == exit_codes.code_of("failed") and _paths(report) == ["a.py", "ratchet.json"]
    assert checked("--only", "I3")[0] == 0


def _blob(text: str) -> str:
    """git の blob の id。"""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _base(monkeypatch, plugin, files: dict[str, str], commit: bytes = b"") -> list[tuple[str, str]]:
    """git の応答を、変更の前の `src/` が `files`（パス → 本文）のものにする。git の（引数の先頭, 標準入力）を返す並びへ足す。"""
    src = (plugin.root / "src").relative_to(plugin.repo).as_posix()
    bodies = {_blob(text): text for text in files.values()}
    tree = "".join(f"100644 blob {_blob(text)}\t{src}/{rel}\0" for rel, text in files.items())
    asked = []

    def git(_root, *args, stdin=""):
        asked.append((args[0], stdin))
        if args[0] == "cat-file":
            return commit if "^{commit}" in stdin else b"".join(_file(bodies[blob]) for blob in stdin.split())
        return {"rev-parse": f"{plugin.repo}\n", "ls-tree": f"{tree}120000 blob {'0' * 40}\t{src}/ndf/link.py\0"}[args[0]].encode()

    monkeypatch.setattr(run, "_git_stdout", git)
    return asked


@pytest.fixture
def against(plugin, checked, monkeypatch):
    """変更の前の `src/` を渡した中身（パス → 本文）にして `ndf check` を打つ関数。git の引数と標準入力の並びも返す。"""

    def call(limit: int, files: dict[str, str]):
        plugin.write(ratchet.RATCHET_REL, body(limit))
        asked = _base(monkeypatch, plugin, files, _object("commit", "tree x\n") + _file(body(limit)))
        code, report = checked("--ratchet-base", "HEAD^1")
        assert code == exit_codes.code_of("failed")
        return report, asked

    return call


def test_one_more_finding_lists_the_places_the_base_does_not_have(plugin, against):
    # 変更の前: a.py・b.py に twice（行は今と違う）、broken.py は読めない
    twice = "\n\ndef twice():\n    return 1\n"
    plugin.write("src/ndf/c.py", "def added():\n    return 1\n")
    plugin.write("src/ndf/d.py", "def added():\n    return 1\n")
    report, asked = against(1, {"ndf/a.py": twice, "ndf/b.py": twice, "ndf/broken.py": "def (:\n", "ndf/x.json": "{}"})
    assert _paths(report) == ["c.py", "ratchet.json"] and "2 件" in report["items"][1]["detail"]
    assert all(set(i) == {"rule", "path", "line", "detail"} for i in report["items"])
    assert [name for name, _ in asked] == ["rev-parse", "cat-file", "ls-tree", "cat-file"]


def test_a_finding_that_a_rule_change_adds_is_not_in_the_base(plugin, rules, against):
    same = {"ndf/a.py": "def twice():\n    return 1\n", "ndf/b.py": "def twice():\n    return 1\n"}
    report, asked = against(0, same)
    assert _paths(report) == ["ratchet.json"] and [name for name, _ in asked] == ["rev-parse", "cat-file", "ls-tree"]
    # 変更の前の規則は twice の同名を認めていた
    old = {"copy_dup": {**rules["copy_dup"], "allow_names": ["twice"]}}
    assert _paths(against(0, {**same, "ndf/checks/rules.json": json.dumps(old)})[0]) == ["a.py", "ratchet.json"]


def test_the_base_reads_only_the_changed_and_removed_files_and_reuses_the_others(plugin, monkeypatch):
    # 変わらないファイルは構文木を作り直さず、本文も git から読まない（PR 4358 の r5-5）
    plugin.write("src/ndf/a.py", "x = '日本語'\n")
    plugin.write("src/ndf/b.py", "x = 1\n")
    plugin.write("src/ndf/untracked.py", "x = 1\n")
    scope = plugin.scope()
    asked = _base(monkeypatch, plugin, {"ndf/a.py": "x = '日本語'\n", "ndf/b.py": "x = 2\n", "ndf/gone.py": "x = 3\n", "ndf/x.json": "{}"})
    base = run._base_scope(scope, run._base_files(scope, "HEAD^1"))
    now = {s.rel: s for s in scope.sources}
    got = {s.path.name: (s.text, s is now.get(s.rel)) for s in base.sources}
    assert got == {"a.py": ("x = '日本語'\n", True), "b.py": ("x = 2\n", False), "gone.py": ("x = 3\n", False)}
    assert asked[1:] == [("cat-file", f"{_blob('x = 2' + chr(10))}\n{_blob('x = 3' + chr(10))}\n")]


def test_a_kept_limit_fails_until_write_spec_lowers_it(plugin, checked):
    path = plugin.write(ratchet.RATCHET_REL, body(2))
    code, report = checked()
    assert code == exit_codes.code_of("failed")
    assert [i["path"].rsplit("/", 1)[-1] for i in report["items"]] == ["ratchet.json"]
    code, report = checked("--write-spec")
    assert code == 0 and report["metrics"]["written"] == 1 and report["items"] == []
    row = json.loads(path.read_text(encoding="utf-8"))["rules"]["copy-dup"]
    assert (row["max"], row["commit"]) == (1, "c1")


def test_write_spec_that_lowers_nothing_neither_asks_for_the_head_nor_reads_the_limits_twice(plugin, checked, monkeypatch):
    plugin.write(ratchet.RATCHET_REL, body(1))
    asked, read, real = [], [], ratchet.baseline
    monkeypatch.setattr(run, "_git_stdout", lambda _root, *args, stdin="": asked.append(args[-1]) or f"{plugin.repo}\n".encode())
    monkeypatch.setattr(ratchet, "baseline", lambda path: read.append(path) or real(path))
    code, report = checked("--write-spec")
    assert code == 0 and report["metrics"]["written"] == 0
    assert asked == ["--show-toplevel"] and len(read) == 1


@pytest.mark.parametrize("extra", [(), ("--write-spec",), ("--ratchet-base", "HEAD^1")])
def test_a_check_of_other_rules_does_not_read_the_limits(plugin, checked, extra):
    path = plugin.write(ratchet.RATCHET_REL, "{")
    assert checked("--only", "I3", *extra)[0] == 0
    assert path.read_text(encoding="utf-8") == "{"


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
