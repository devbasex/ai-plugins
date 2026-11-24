from __future__ import annotations

import contextlib
import hashlib
import io
import json
from collections import defaultdict
from pathlib import Path

import pytest

from ndf import identity
from ndf.checks import git_objects, ratchet, run
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


def _answer(monkeypatch, plugin) -> None:
    """git の応答を渡した値にする。"""
    answers = {"--show-toplevel": f"{plugin.repo}\n".encode(), "HEAD": b"c1\n"}
    monkeypatch.setattr(run, "_git_stdout", lambda _root, *args: answers[args[-1]])


def _objects(monkeypatch, found: dict[str, git_objects.GitObject]) -> list[list[str]]:
    """git のものを `found`（名前 → もの）にする。git を開くたびに、問われた名前の並びを返す並びへ足す。"""
    asked: list[list[str]] = []

    @contextlib.contextmanager
    def opened(_root):
        names: list[str] = []
        asked.append(names)
        yield lambda name: names.append(name) or found.get(name)

    monkeypatch.setattr(git_objects, "opened", opened)
    return asked


def _file(text: str) -> git_objects.GitObject:
    return git_objects.GitObject(git_objects.blob_id(text, "0" * 40), "blob", text.encode())


COMMIT = git_objects.GitObject("c" * 40, "commit", b"tree x\n")


def _limit_name(plugin) -> str:
    return f"HEAD^1:{(plugin.root / ratchet.RATCHET_REL).relative_to(plugin.repo).as_posix()}"


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


def _tree(files: dict[str, str], found: dict[str, git_objects.GitObject]) -> git_objects.GitObject:
    """`files`（パス → 本文）の木。中のものを id ごとに `found` へ足す。"""
    below: dict[str, dict[str, str]] = {}
    rows = [(b"120000", "link.py", "0" * 40), (b"160000", "module.py", "0" * 40)]
    for rel, text in files.items():
        head, _, rest = rel.partition("/")
        if rest:
            below.setdefault(head, {})[rest] = text
        else:
            found[_file(text).oid] = _file(text)
            rows.append((b"100644", head, _file(text).oid))
    rows += [(b"40000", name, _tree(inner, found).oid) for name, inner in below.items()]
    data = b"".join(mode + b" " + name.encode() + b"\0" + bytes.fromhex(oid) for mode, name, oid in rows)
    tree = git_objects.GitObject(hashlib.sha1(data, usedforsecurity=False).hexdigest(), "tree", data)
    found[tree.oid] = tree
    return tree


def _base(monkeypatch, plugin, files: dict[str, str], limit: int = 0) -> list[list[str]]:
    """変更の前の commit を、`src/` が `files`（パス → 本文）で基準が `limit` のものにする。問われた名前の並びを返す。"""
    found = {"HEAD^1^{commit}": COMMIT, _limit_name(plugin): _file(body(limit))}
    src = (plugin.root / "src").relative_to(plugin.repo).as_posix()
    found[f"HEAD^1:{src}"] = _tree(files, found)
    return _objects(monkeypatch, found)


@pytest.fixture
def against(plugin, checked, monkeypatch):
    """変更の前の `src/` を渡した中身（パス → 本文）にして `ndf check` を打つ関数。git に問われた名前の並びも返す。"""

    def call(limit: int, files: dict[str, str]):
        plugin.write(ratchet.RATCHET_REL, body(limit))
        asked = _base(monkeypatch, plugin, files, limit)
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
    assert len(asked) == 1  # git の起動は 1 回（PR 4358 の r6-3）


def test_a_finding_that_a_rule_change_adds_is_not_in_the_base(plugin, rules, against):
    same = {"ndf/a.py": "def twice():\n    return 1\n", "ndf/b.py": "def twice():\n    return 1\n"}
    assert _paths(against(0, same)[0]) == ["ratchet.json"]
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
    with run._base_of(scope, "HEAD^1") as before:
        base = run._base_scope(scope, run._base_files(scope, before))
    now = {s.rel: s for s in scope.sources}
    got = {s.path.name: (s.text, s is now.get(s.rel)) for s in base.sources}
    assert got == {"a.py": ("x = '日本語'\n", True), "b.py": ("x = 2\n", False), "gone.py": ("x = 3\n", False)}
    blobs = {_file(text).oid for text in ("x = '日本語'\n", "x = 1\n", "x = 2\n", "x = 3\n", "{}")}
    assert [name for name in asked[0] if name in blobs] == [_file("x = 2\n").oid, _file("x = 3\n").oid]


def _ask(answer: bytes, name: str):
    ask = io.BytesIO()
    return git_objects.read_object(ask, io.BytesIO(answer), name), ask.getvalue()


def test_an_object_is_read_by_the_number_of_its_bytes():
    oid, text = "a" * 40, "日本語の件名\n"
    answer = f"{oid} blob {len(text.encode())}\n{text}\nnext".encode()
    stream = io.BytesIO(answer)
    assert git_objects.read_object(io.BytesIO(), stream, "HEAD:a") == git_objects.GitObject(oid, "blob", text.encode())
    assert stream.read() == b"next"


def test_a_name_that_git_does_not_have_is_none():
    assert _ask("HEAD:a b missing\n".encode(), "HEAD:a b") == (None, b"HEAD:a b\n")
    assert _ask(b"abcd ambiguous\n", "abcd") == (None, b"abcd\n")
    assert _ask(b"", "a\nb") == (None, b"")  # 改行を含む名前は問わない


def test_git_that_ends_without_an_answer_stops():
    with pytest.raises(CheckError):
        _ask(b"", "HEAD:a")


def test_the_rows_of_a_sha256_tree_are_read():
    blob = git_objects.GitObject("b" * 64, "tree", b"100755 a.py\0" + b"\x12" * 32)
    assert git_objects.files_under({"t": blob}.get, "t", "src") == {"src/a.py": "12" * 32}
    assert git_objects.files_under({}.get, "t", "src") == {}
    assert len(git_objects.blob_id("x", "0" * 64)) == 64


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
    monkeypatch.setattr(run, "_git_stdout", lambda _root, *args: asked.append(args[-1]) or f"{plugin.repo}\n".encode())
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
    [(_file(body(1)), 0), (_file(body(2)), 0), (_file(body(0)), 1), (None, 0), (_file("{"), 2)],
)
def test_the_limit_is_compared_with_the_one_before_the_change(plugin, checked, monkeypatch, second, code):
    plugin.write(ratchet.RATCHET_REL, body(1))
    asked = _objects(monkeypatch, {"HEAD^1^{commit}": COMMIT, **({_limit_name(plugin): second} if second else {})})
    assert checked("--ratchet-base", "HEAD^1")[0] == code
    assert asked == [["HEAD^1^{commit}", _limit_name(plugin)]]


def test_an_unknown_base_stops(plugin, checked, monkeypatch):
    plugin.write(ratchet.RATCHET_REL, body(1))
    _objects(monkeypatch, {})
    code, report = checked("--ratchet-base", "nope")
    assert code == exit_codes.unreadable_code() and "nope" in report["summary"]


def test_the_plugin_limit_file_has_a_row_for_every_ratcheted_rule():
    assert set(ratchet.baseline(Path(ratchet.__file__).with_name("ratchet.json"))) == set(run.RATCHETED)
