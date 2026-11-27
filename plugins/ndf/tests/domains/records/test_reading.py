"""読み手役への指示と、読み手役が確認した記録（`ndf records reading`）。#4285 の受け入れ条件 12・13・17。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import git_sandbox
import pytest

from ndf import cli
from ndf.adapters.github import GitHubError
from ndf.contracts import internal, result, writing
from ndf.domains.records import cli as records_cli
from ndf.domains.records import reading
from ndf.domains.records import reading_decide as rd

HEAD, MOVED = "a" * 40, "b" * 40
READER = {"position": "承認する人。\n進めてよいかを判断する", "words": "ふだんの言葉", "knowledge": "作業の経過は知らない"}
QUESTION = {
    "question": '設計を承認しますか？\n「U1」の "head" は\n次のとおりです',
    "header": "設計",
    "multiSelect": False,
    "options": [{"label": "承認する", "description": "マージします\n（戻せます）"}, {"label": "直す所がある", "description": ""}],
}
READS = [
    {"name": "question", "reader": "承認する人", "round": 1, "result": "passed"},
    {"name": "issues/x.md", "reader": "エンジニア", "round": 2, "result": "remained"},
]
PRS = [{"number": 7, "branch": "design/x", "head": HEAD}]
ENTRY = {"questions": [QUESTION], "prs": PRS, "reads": READS}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    root = tmp_path / "r"
    git_sandbox.sh(tmp_path, "init", "-q", str(root))
    git_sandbox.sh(root, "remote", "add", "origin", "https://github.com/o/r.git")
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "state"))
    return root


@pytest.fixture(autouse=True)
def fetched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """`git fetch` へ渡したリモートと refspec（打たずに残す）。"""
    asked: list[tuple[str, ...]] = []
    monkeypatch.setattr(reading.Git, "fetch", lambda self, remote, *refs: asked.append((remote, *refs)))
    return asked


def _hub(monkeypatch: pytest.MonkeyPatch, heads: dict[int, str]) -> list[set[int]]:
    """GitHub の偽物。PR の番号から head を返し、読んだ番号を残す。"""
    asked: list[set[int]] = []

    def pulls(numbers: Any) -> dict[int, Any]:
        asked.append(set(numbers))
        return {n: SimpleNamespace(head_sha=heads[n]) if n in heads else None for n in numbers}

    monkeypatch.setattr(records_cli, "_repo_hub", lambda repo: SimpleNamespace(pulls=pulls))
    return asked


def _run(capsys, last_result, root: Path, action: str, entry: Any) -> tuple[int, dict]:
    path = root / "entry.json"
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    code = cli.main(["records", "--cwd", str(root), "reading", action, "--file", "entry.json"])
    got = last_result(capsys.readouterr().out)
    assert result.errors(got, code) == []
    return code, got


# --- 質問の SHA-256 -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"question": "別の文"},
        {"multiSelect": True},
        {"options": [{**QUESTION["options"][0], "description": "別の説明"}, QUESTION["options"][1]]},
        {"options": [{**QUESTION["options"][0], "label": "別の名前"}, QUESTION["options"][1]]},
        {"options": QUESTION["options"][:1]},
    ],
)
def test_the_key_changes_with_what_the_person_reads(change: dict) -> None:
    assert rd.question_key([{**QUESTION, **change}]) != rd.question_key([QUESTION])


def test_the_key_ignores_the_header_and_outer_spaces_and_keeps_the_boundary_of_the_texts() -> None:
    padded = {**QUESTION, "header": "別", "question": f" {QUESTION['question']}\n"}
    assert rd.question_key([padded]) == rd.question_key([QUESTION])

    def one(label: str, description: str) -> str:
        return rd.question_key([{"question": "q", "options": [{"label": label, "description": description}]}])

    assert one("a\nb", "c") != one("a", "b\nc")
    assert one('a","description":"b', "") != one("a", "b")
    two = [{"question": "a", "options": []}, {"question": "b", "options": []}]
    assert rd.question_key(two) != rd.question_key(list(reversed(two)))


# --- 足りないもの ---------------------------------------------------------------------------------------


def _row(**change: Any) -> dict[str, Any]:
    return {"repo": "o/r", "question_key": "k", "prs": PRS, **change}


@pytest.mark.parametrize(
    ("rows", "heads", "prs", "words"),
    [
        ([_row()], {7: HEAD}, (), []),
        ([_row()], {7: HEAD}, (7,), []),
        ([], {}, (), [rd.NO_RECORD]),
        ([_row(repo="o/other"), _row(question_key="別")], {7: HEAD}, (7,), [rd.NO_RECORD]),
        ([_row()], {7: HEAD}, (7, 8), ["no_pr"]),
        ([_row()], {7: MOVED}, (), [rd.HEAD_MOVED]),
        ([_row()], {7: MOVED}, (8,), ["no_pr", rd.HEAD_MOVED]),
        ([_row()], {}, (7,), []),
        ([_row(prs=[])], {}, (), []),
        # 同じ質問の記録が複数あれば、最後の行を使う
        ([_row(prs=[{**PRS[0], "head": MOVED}]), _row()], {7: HEAD}, (), []),
        ([_row(), _row(prs=[{**PRS[0], "head": MOVED}])], {7: HEAD}, (), [rd.HEAD_MOVED]),
    ],
)
def test_missing(rows: list, heads: dict, prs: tuple, words: list[str]) -> None:
    assert rd.missing("k", "o/r", rows, heads, prs) == words


def test_light_rows_reads_the_written_rows_and_skips_the_broken(repo: Path) -> None:
    written = reading.add(ENTRY, repo)
    path = reading.checks_path()
    with path.open("a", encoding="utf-8") as f:
        f.write('{"repo": "o/r"}\n[1]\n{"repo": "o/r", "question_key": "k", "prs": [{"number": "7"}]}\n{書きかけ')
    assert rd.light_rows(path.read_text(encoding="utf-8"), internal.version()) == [written]
    newer = json.dumps({**written, "version": internal.version() + 1})
    with pytest.raises(rd.NewerRow):
        rd.light_rows(newer, internal.version())


# --- 記録 -----------------------------------------------------------------------------------------------


def test_add_fills_the_row_and_keeps_no_text(repo: Path, capsys, last_result) -> None:
    code, got = _run(capsys, last_result, repo, "add", ENTRY)
    assert (code, got["status"]) == (0, "ok")
    [row] = reading.rows()
    assert got["items"] == [row]
    assert (row["repo"], row["question_key"], row["prs"], row["reads"]) == ("o/r", rd.question_key([QUESTION]), PRS, READS)
    assert "承認しますか" not in reading.checks_path().read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "change",
    [
        {"reads": [READS[1]]},
        {"reads": [{**READS[0], "result": "failed"}]},
        {"prs": [{"number": 7, "branch": "design/x"}]},
        {"prs": [{**PRS[0], "branch": "*"}]},
        {"questions": []},
        {"questions": [{"question": " ", "options": []}]},
        {"questions": "設計を承認しますか"},
        {"background": "経緯"},
    ],
)
def test_add_refuses_a_wrong_shape_without_writing(change: dict, repo: Path, capsys, last_result) -> None:
    code, got = _run(capsys, last_result, repo, "add", {**ENTRY, **change})
    assert code != 0 and got["cause"]["kind"] == "invalid"
    assert reading.rows() == []


@pytest.mark.parametrize(
    ("branch", "kept"),
    [("main", True), ("feature/4285-日本語.1", True)]
    + [(b, False) for b in ("*", "a/*", "a:b", "^a", "a..b", "/a", "a/", "a//b", ".a", "a/.b", "a.lock", "a.", "a@{1}", "a b", "a\n", "")],
)
def test_rows_keep_only_a_single_branch_name(branch: str, kept: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """記録のブランチは強制更新の refspec に入るので、書いてある行も 1 つのブランチを表す名前だけを読む。"""
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path))
    row = _row(version=internal.version(), at=internal.stamp(0), question_key="0" * 64, prs=[{**PRS[0], "branch": branch}], reads=READS)
    text = json.dumps(row) + "\n"
    reading.checks_path().write_text(text, encoding="utf-8")
    assert reading.rows() == ([row] if kept else [])
    assert rd.light_rows(text, internal.version()) == ([row] if kept else [])


def test_rows_stop_on_a_newer_version(repo: Path) -> None:
    row = reading.add(ENTRY, repo)
    path = reading.checks_path()
    path.write_text(json.dumps({**row, "version": internal.version() + 1}) + "\n", encoding="utf-8")
    with pytest.raises(internal.InternalError):
        reading.rows()


# --- 承認の手順の確かめ（突き合わせそのものは tests/domains/docrebuild/test_docrebuild_gate.py が gate の記録で確かめる） ---------


class _Hub:
    """GitHub の偽物（題・本文・head を返す。`broken` なら読めない）。"""

    def __init__(self, broken: bool = False) -> None:
        self.broken, self.fetches = broken, 0

    def repository(self, query: str, variables: Any = None) -> dict:
        self.fetches += 1
        if self.broken:
            raise GitHubError("502", 502)
        node = {"number": 7, "url": "u", "title": "題", "body": "本文", "headRefOid": HEAD, "headRefName": "design/x", "baseRefName": "main",
                "changedFiles": 1, "additions": 1, "deletions": 0, "commits": {"totalCount": 1}, "comments": {"nodes": []}}  # fmt: skip
        return {"found": node}


@pytest.mark.parametrize(("broken", "kind"), [(False, "precondition"), (True, "unknown")])
def test_check_writes_the_rebuilt_question_and_names_what_is_missing(
    broken: bool, kind: str, repo: Path, capsys, last_result, monkeypatch, fetched
) -> None:
    """`check --gate --pr` は PR を 1 回だけ読み、題から作り直した質問を --questions-out へ書く。記録が無ければ no_record、読めなければ unknown。"""
    hub = _Hub(broken)
    monkeypatch.setattr(records_cli, "_repo_hub", lambda repo: hub)
    code = cli.main(["records", "--cwd", str(repo), "reading", "check", "--gate", "design", "--pr", "7", "--questions-out", "q.json"])
    got = last_result(capsys.readouterr().out)
    assert result.errors(got, code) == [] and got["cause"]["kind"] == kind and hub.fetches == 1
    if broken:
        return
    assert got["items"] == [{"missing": rd.NO_RECORD}] and fetched == []
    written = json.loads((repo / "q.json").read_text(encoding="utf-8"))["questions"]
    assert written == reading.gate_questions("design", 7, "題", None)
    assert cli.main(["records", "--cwd", str(repo), "reading", "check", "--file", "entry.json"]) != 0


# --- 読み手役への指示 -----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [*sorted(reading.RESTATE), "none"])
def test_the_prompt_has_the_reader_and_the_types(kind: str, repo: Path, capsys, last_result) -> None:
    entry = {"files": ["issues/a.md", "/tmp/b.md"], "reader": READER, "questions_kind": kind}
    code, got = _run(capsys, last_result, repo, "prompt", entry)
    text = got["metrics"]["prompt"]
    assert code == 0 and writing.types_section() in text
    assert all(f"- {f}\n" in text for f in entry["files"]) and all(v in text for v in READER.values())
    assert [name for name, question in reading.RESTATE.items() if question in text] == ([kind] if kind != "none" else [])


@pytest.mark.parametrize(
    "change",
    [
        {"files": []},
        {"reader": {**READER, "history": "経緯"}},
        {"reader": {k: v for k, v in READER.items() if k != "words"}},
        {"questions_kind": "report"},
        {"background": "経緯"},
    ],
)
def test_the_prompt_takes_no_background(change: dict) -> None:
    """受け入れ条件 12: 文書を作った経緯を渡す引数が無い。"""
    with pytest.raises(reading.ReadingInvalid):
        reading.prompt({"files": ["a.md"], "reader": READER, "questions_kind": "none", **change})


@pytest.mark.parametrize(
    ("change", "named"),
    [
        *(({"files": files}, "files") for files in ("a.md", ("a.md",), None, [""], [" \n"], ["a.md", ""], [1], [None])),
        *(({"reader": reader}, "reader") for reader in (None, "承認する人", list(READER), {})),
        *(({"reader": {**READER, key: value}}, "reader") for key in READER for value in ("", " \n", None, 1, ["語"])),
        *(({"questions_kind": kind}, "questions_kind") for kind in ("", "None", "design ", None, 1, [], ["design"], {})),
        ({"files": [], "reader": {}, "questions_kind": ""}, "files"),
        ({"reader": {}, "questions_kind": ""}, "reader"),
        ({"files": [], "reader": {}, "questions_kind": "", "background": ""}, "入力"),
    ],
)
def test_the_prompt_names_the_first_wrong_input(change: dict, named: str) -> None:
    """値の型と空白だけの文字列まで見て、キー・files・reader・questions_kind の順で最初の違いを知らせる。"""
    with pytest.raises(reading.ReadingInvalid) as caught:
        reading.prompt({"files": ["a.md"], "reader": READER, "questions_kind": "none", **change})
    assert str(caught.value).startswith((f"{named} は", f"{named}は"))


@pytest.mark.parametrize("kind", sorted(reading.RESTATE))
def test_the_prompt_differs_only_by_the_restate_question(kind: str) -> None:
    """文書の種類で変わるのは、足す質問だけ。none は質問の所に何も足さない。"""
    entry = {"files": ["a.md", "b.md"], "reader": READER}
    with_question = reading.prompt({**entry, "questions_kind": kind})
    assert with_question.count(reading.RESTATE[kind]) == 1
    assert with_question.replace(reading.RESTATE[kind], "") == reading.prompt({**entry, "questions_kind": "none"})


def test_the_prompt_keeps_the_given_text_and_order() -> None:
    """ファイルの順はそのまま、読み手の順は READER の順。前後の空白を削らず、入力を変えない。"""
    entry = {"files": [" b.md ", "a.md"], "reader": {key: f" {key} の説明 " for key in reversed(READER)}, "questions_kind": "none"}
    before = json.loads(json.dumps(entry))
    lines = reading.prompt(entry).splitlines()
    assert lines[lines.index("-  b.md ") + 1] == "- a.md"
    first = next(i for i, line in enumerate(lines) if line.endswith(" position の説明 "))
    assert lines[first : first + 3] == [f"- {label}:  {key} の説明 " for key, label in reading.READER.items()]
    assert entry == before
