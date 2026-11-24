"""読み手役への指示と、読み手役が確認した記録を読み書きする口（#4285 の決定 7〜10）。

記録は状態の保存先の `reading-checks.jsonl`（表 ndf-files の reading-checks）へ 1 行ずつ追記する。文書の本文・質問の文・
残った文の引用は書かない。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ndf.adapters.git import Git
from ndf.adapters.github import GitHub
from ndf.contracts import internal, writing
from ndf.domains.records import jsonl, mvv
from ndf.domains.records import reading_decide as rd

# 読み手の説明の 3 点（入力のキーと、指示に書く見出し）
READER = {
    "position": "立場（何をする人か。読んだ後に何を判断するか、どんな作業をするか）",
    "words": "ふだん使う言葉",
    "knowledge": "知っていること・知らないこと",
}
# 文書の種類ごとに、要点の言い直しへ含めさせる質問（どちらでもない文書は none で、質問を足さない）
RESTATE = {
    "requirements": "言い直しには、次の 2 つへの回答を含める。(a) 何が起きて誰が困ったか。(b) 何を満たせば終わりか。",
    "design": "言い直しには、次の 2 つへの回答を含める。(a) どう解くか。(b) 何を採らなかったか、なぜか。",
}
PROMPT_KEYS = {"files", "reader", "questions_kind"}
ADD_KEYS = {"questions", "prs", "reads"}
CHECK_KEYS = {"questions", "prs"}


class ReadingInvalid(ValueError):
    """入力の形が違う。文に入力の値を含まない。"""


def checks_path() -> Path:
    """記録のファイル（保存先は承認ゲートの変更要求の記録と同じ mvv-state）。"""
    return mvv.record_path("reading-checks")


def prompt(entry: Mapping[str, Any]) -> str:
    """読み手役への指示。文書は加工しない（補足を外した文書・直した部分だけの文書は、頼む側が先に別のファイルへ作る）。"""
    _require_keys(entry, PROMPT_KEYS)
    files, reader, kind = entry["files"], entry["reader"], entry["questions_kind"]
    if not (isinstance(files, list) and files and all(isinstance(f, str) and f.strip() for f in files)):
        raise ReadingInvalid("files は、読ませるファイルのパスの空でない配列にする")
    if not (isinstance(reader, Mapping) and set(reader) == set(READER) and all(isinstance(v, str) and v.strip() for v in reader.values())):
        raise ReadingInvalid(f"reader は、{'・'.join(READER)} の 3 つの空でない文字列を持つオブジェクトにする")
    if not (isinstance(kind, str) and (kind == "none" or kind in RESTATE)):
        raise ReadingInvalid(f"questions_kind は {'・'.join(RESTATE)}・none のどれかにする")
    lines = [
        "次の文書を、下に示す読み手になったつもりで最初から最後まで読み、読み手に通じるかを確かめてください。",
        "読むのは次の文書だけです。ほかのファイル・Issue・Pull Request・会話の記録を読まず、ファイルを変えないでください。",
        "",
        "## 読む文書",
        *(f"- {f}" for f in files),
        "",
        "## 読み手",
        *(f"- {label}: {reader[key]}" for key, label in READER.items()),
        "",
        "## 通じない文の見分け方",
        "次は、文書の書き方の基準の一部である。",
        "",
        writing.types_section(),
        "",
        "## 返すもの",
        "1. 要点を自分の言葉で言い直したもの。" + (RESTATE[kind] if kind in RESTATE else ""),
        "2. 通じない文の一覧。1 件ごとに、文の引用・型（A〜D）・読み手がつまずく理由・書き直した案・区分（「判断や作業を誤らせる文」か「読みにくいだけの文」）を書く。無ければ「無し」と書く。",
        "3. 2 に入れるか迷って入れなかった文。最大 3 つで、迷った理由も書く。無ければ「無し」と書く。",
        "",
        "合格か不合格かは決めない（頼んだ側が決める）。",
    ]
    return "\n".join(lines)


def add(entry: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """確認の記録を 1 行追記し、追記した行を返す。形が違えば `ReadingInvalid` で、追記しない。GitHub を読まない。"""
    _require_keys(entry, ADD_KEYS)
    key = _key(entry["questions"])
    row = {"repo": Git(root).remote_repo(), "question_key": key, "prs": entry["prs"], "reads": entry["reads"]}
    if not rd.has_used_keys(row):
        raise ReadingInvalid("prs は、number（整数）・branch（1 つのブランチの名前）・head（SHA）を持つオブジェクトの配列にする")
    try:
        return jsonl.append_locked(checks_path(), row, kind="reading-check")
    except internal.InternalError as exc:
        if exc.kind != "invalid":
            raise
        raise ReadingInvalid(str(exc)) from exc


def rows() -> list[dict[str, Any]]:
    """記録の行（古い順）。壊れた行（ブランチの名前でない `branch` を含む）は除く。ファイルが無ければ空、開けなければ `OSError`、新しいバージョンの行があれば `InternalError`。"""
    found, _ = jsonl.read_rows(checks_path(), kind="reading-check")
    return [row for row in found if rd.has_used_keys(row)]


def check(entry: Mapping[str, Any], root: Path, hub: Callable[[Path], GitHub]) -> list[str]:
    """承認の手順用: 質問を出す前に足りないもの（`reading_decide.missing`）。PR の今の head は GitHub から読む。

    後で動く hook が新しい head と比べられるように、先に記録の PR のリモート追跡ブランチを `git fetch` で新しくする（失敗しても続ける）。
    GitHub を読めなければ `GitHubError`。
    """
    _require_keys(entry, CHECK_KEYS)
    key, prs = _key(entry["questions"]), entry["prs"]
    if not (isinstance(prs, list) and all(type(n) is int and n > 0 for n in prs)):
        raise ReadingInvalid("prs は、承認する PR の番号の配列にする")
    git = Git(root)
    repo, found = git.remote_repo(), rows()
    row = rd.used_row(key, repo, found)
    recorded = row["prs"] if row is not None else []
    if recorded:
        remote = git.remote()
        git.fetch(remote, *(f"+refs/heads/{pr['branch']}:refs/remotes/{remote}/{pr['branch']}" for pr in recorded))
    numbers = {*prs, *(pr["number"] for pr in recorded)}
    heads: dict[int, str] = {}
    if numbers:
        heads = {n: pull.head_sha for n, pull in hub(root).pulls(numbers).items() if pull is not None}
    return rd.missing(key, repo, found, heads, prs)


def _key(questions: Any) -> str:
    if not (isinstance(questions, list) and questions and all(_is_question(q) for q in questions)):
        raise ReadingInvalid("questions は、AskUserQuestion に渡す questions と同じ形の空でない配列にする")
    return rd.question_key(questions)


def _is_question(q: Any) -> bool:
    return isinstance(q, dict) and isinstance(q.get("question"), str) and bool(q["question"].strip()) and isinstance(q.get("options"), list)


def _require_keys(entry: Any, keys: set[str]) -> None:
    if not isinstance(entry, Mapping) or set(entry) != keys:
        raise ReadingInvalid(f"入力は、キー {'・'.join(sorted(keys))} だけをすべて持つ JSON のオブジェクトにする")
