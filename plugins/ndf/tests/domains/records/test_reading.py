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
    monkeypatch.setattr(reading.Git, "fetch", lambda self, remote, *refs: None)
    return root


def _hub(monkeypatch: pytest.MonkeyPatch, heads: dict[int, str]) -> list[set[int]]:
    """GitHub の偽物。PR の番号から head を返し、読んだ番号を残す。"""
    asked: list[set[int]] = []

    def pulls(numbers: Any) -> dict[int, Any]:
        asked.append(set(numbers))
        return {n: SimpleNamespace(head_sha=heads[n]) if n in heads else None for n in numbers}

    monkeypatch.setattr(records_cli, "_hub", lambda root: SimpleNamespace(pulls=pulls))
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


def test_the_key_ignores_the_header_and_outer_spaces() -> None:
    padded = {**QUESTION, "header": "別", "question": f" {QUESTION['question']}\n"}
    assert rd.question_key([padded]) == rd.question_key([QUESTION])


def test_the_key_keeps_the_boundary_of_the_texts() -> None:
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
        {"reads": [{**READS[0], "result": "remained"}]},
        {"prs": [{"number": 7, "branch": "design/x"}]},
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


def test_rows_stop_on_a_newer_version(repo: Path) -> None:
    row = reading.add(ENTRY, repo)
    path = reading.checks_path()
    path.write_text(json.dumps({**row, "version": internal.version() + 1}) + "\n", encoding="utf-8")
    with pytest.raises(internal.InternalError):
        reading.rows()


# --- 承認の手順の確かめ ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("head", "prs", "words"), [(HEAD, [7], []), (MOVED, [7], [rd.HEAD_MOVED]), (HEAD, [7, 8], ["no_pr"])])
def test_check_compares_with_the_heads_on_github(
    head: str, prs: list[int], words: list[str], repo: Path, capsys, last_result, monkeypatch
) -> None:
    reading.add(ENTRY, repo)
    asked = _hub(monkeypatch, {7: head, 8: HEAD})
    code, got = _run(capsys, last_result, repo, "check", {"questions": [QUESTION], "prs": prs})
    assert asked == [{7, *prs}]
    assert [i["missing"] for i in got["items"]] == words
    assert (code == 0) is (words == []) and ("cause" not in got or got["cause"]["kind"] == "precondition")


def test_check_without_a_pull_request_does_not_read_github(repo: Path, capsys, last_result, monkeypatch) -> None:
    reading.add({**ENTRY, "prs": []}, repo)
    asked = _hub(monkeypatch, {})
    code, got = _run(capsys, last_result, repo, "check", {"questions": [QUESTION], "prs": []})
    assert (code, got["status"], asked) == (0, "ok", [])


def test_check_stops_as_unknown_when_github_cannot_be_read(repo: Path, capsys, last_result, monkeypatch) -> None:
    def broken(numbers: Any) -> dict:
        raise GitHubError("502", 502)

    monkeypatch.setattr(records_cli, "_hub", lambda root: SimpleNamespace(pulls=broken))
    reading.add(ENTRY, repo)
    _, got = _run(capsys, last_result, repo, "check", {"questions": [QUESTION], "prs": [7]})
    assert got["status"] == "stopped" and got["cause"]["kind"] == "unknown"


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
