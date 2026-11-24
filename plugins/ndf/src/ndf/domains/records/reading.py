"""読み手役への指示と、読み手役が確認した記録を読み書きする口（#4285 の決定 7〜10）。

記録は状態の保存先の `reading-checks.jsonl`（表 ndf-files の reading-checks）へ 1 行ずつ追記する。文書の本文・質問の文・
残った文の引用は書かない。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import Git
from ndf.adapters.github import GitHub
from ndf import contracts
from ndf.contracts import facts, internal, writing
from ndf.domains.records import jsonl, mvv
from ndf.domains.records import reading_decide as rd
from ndf.domains.release import forms_decide

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


class FormMissing(ReadingInvalid):
    """リリースの質問を作れない（設定にリリースの形が無い・表に無い・扱わない形）。"""


class HeadUnknown(Exception):
    """GitHub から今の head を得られない PR がある（PR でない・無い番号）。"""


def checks_path() -> Path:
    """記録のファイル（保存先は承認ゲートの変更要求の記録と同じ mvv-state）。"""
    return mvv.record_path("reading-checks")


# --- 読ませたものの記録（reading-docs.jsonl。#4446 の決定 4） -----------------------------------------------

DOCS_SCHEMA = contracts.schema_path("internal/reading-docs")
# 読ませる対象のうち、ゲートと PR で引く名前（文書はパス）
QUESTION, MATERIAL = contracts.schema_enum(DOCS_SCHEMA, "/$defs/gated")
# 読み手の立場（計画で決めたこと (6): 質問の文と承認資料は承認する人、設計 PR の文書はエンジニア）
APPROVER, ENGINEER = "承認する人", "エンジニア"
# 承認資料の末尾の「読み手役が残した文」のセクションのマーカーと見出し（gate と check が同じ値を使う）
BEGIN = "<!-- ndf-reader-remained:begin -->"
END = "<!-- ndf-reader-remained:end -->"
REMAINED_HEADING = "## 読み手役が残した文"
_TARGET_LABEL = {QUESTION: "質問の文", MATERIAL: "承認資料"}


def reader_of(name: str) -> str:
    """対象の読み手の立場。"""
    return APPROVER if name in (QUESTION, MATERIAL) else ENGINEER


def docs_path() -> Path:
    """読ませたものの記録のファイル（表 ndf-files の reading-docs）。"""
    return mvv.record_path("reading-docs")


def materials_path(gate: str, number: int) -> Path:
    """承認資料のコピー（表 ndf-files の reading-materials。PR とゲートごとに 1 つ）。"""
    pattern = facts.value("ndf-files", "reading-materials", "path")
    return mvv.record_path("reading-docs").parent / pattern.replace("<gate>", gate).replace("<n>", str(number))


def append_doc(row: Mapping[str, Any]) -> dict[str, Any]:
    """読ませたものの記録へ 1 行足し、書いた行を返す。形が違えば書かずに `ReadingInvalid`。"""
    try:
        return jsonl.append_locked(docs_path(), row, kind="reading-docs")
    except internal.InternalError as exc:
        if exc.kind != "invalid":
            raise
        raise ReadingInvalid(str(exc)) from exc


DONE_VALUES = contracts.schema_enum(DOCS_SCHEMA, "/$defs/done_values")


def done_row(base: Mapping[str, Any], **values: Any) -> dict[str, Any]:
    """完了の行（`values` に無い値は null）。`base` は確認の id・リポジトリ・ブランチ・ゲート・PR。"""
    unknown = set(values) - set(DONE_VALUES)
    if unknown:
        raise ReadingInvalid(f"完了の行に無い値: {'・'.join(sorted(unknown))}")
    return {"kind": "done", **base, **dict.fromkeys(DONE_VALUES), **values}


def doc_rows() -> list[dict[str, Any]]:
    """読ませたものの記録の行（古い順）。壊れた行は除く。新しいバージョンの行があれば `InternalError`。"""
    found, _ = jsonl.read_rows(docs_path(), kind="reading-docs")
    return found


def completed(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """確認の id から完了の行。"""
    return {row["check"]: row for row in rows if row.get("kind") == "done"}


def read_row_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """読ませた行を引くキー。質問の文と承認資料はリポジトリ・ゲート・PR、文書はリポジトリ・ブランチ・パス。"""
    if row["name"] in (QUESTION, MATERIAL):
        return (row["repo"], row["gate"], row["pr"], row["name"])
    return (row["repo"], row["branch"], row["name"])


def latest_read(rows: Sequence[Mapping[str, Any]], key: tuple[Any, ...]) -> Optional[Mapping[str, Any]]:
    """完了した確認のうち、キー `key` の最後の読ませた行。無ければ None。"""
    done = completed(rows)
    found = None
    for row in rows:
        if row.get("kind") == "read" and row["check"] in done and read_row_key(row) == key:
            found = row
    return found


def last_done(rows: Sequence[Mapping[str, Any]], repo: str, gate: str, number: int) -> Optional[Mapping[str, Any]]:
    """同じ PR とゲートで最後に完了した確認の完了の行。"""
    found = None
    for row in rows:
        if row.get("kind") == "done" and row["repo"] == repo and row["gate"] == gate and row["pr"] == number:
            found = row
    return found


def remained_of(rows: Sequence[Mapping[str, Any]], repo: str, gate: str, number: int) -> list[Mapping[str, Any]]:
    """同じ PR とゲートで最後に完了した確認の、2 回目にも残った文の行。"""
    done = last_done(rows, repo, gate, number)
    return [] if done is None else remained_in(rows, done["check"])


def remained_in(rows: Iterable[Mapping[str, Any]], check: str) -> list[Mapping[str, Any]]:
    """確認 `check` の、2 回目にも残った文の行。"""
    return [r for r in rows if r.get("kind") == "read" and r["check"] == check and r["verdict"] == "remained"]


def pending_review(rows: Iterable[Mapping[str, Any]], repo: str, branch: str) -> Optional[Mapping[str, Any]]:
    """同じブランチの、解消していない再レビュー待ちの行。"""
    found: Optional[Mapping[str, Any]] = None
    for row in rows:
        if row.get("repo") != repo or row.get("branch") != branch:
            continue
        if row["kind"] == "review_pending":
            found = row
        elif row["kind"] in ("review_resolved", "review_cancelled") and found is not None and row["commit"] == found["commit"]:
            found = None
    return found


def sent_back(rows: Iterable[Mapping[str, Any]], repo: str, gate: str, number: int) -> bool:
    """同じ PR とゲートに変更要求の行があるか（変更要求で工程を止めるのは 1 回まで）。"""
    return any(r.get("kind") == "sent_back" and r["repo"] == repo and r["gate"] == gate and r["pr"] == number for r in rows)


def select_reads(
    current: Sequence[Mapping[str, Any]], past: Sequence[Mapping[str, Any]], hashes: Mapping[str, str], carried: Iterable[str]
) -> list[dict[str, Any]]:
    """`reading-checks.jsonl` の行の `reads`（設計書の「`reads` の作り方」の 1〜4）。

    `current` は今回読ませた行（完了の行が無くても使う）、`past` は記録の全行（完了した確認の行だけを使う）、`hashes` は対象の名前から
    今の中身のハッシュ、`carried` は前に残った文を引き継いだ対象の名前。
    """
    done = completed(past)
    candidates = [r for r in [*past, *current] if r["kind"] == "read" and (r["check"] in done or r in current)]
    keep = set(carried)
    out: list[dict[str, Any]] = []
    for name, now in hashes.items():
        reader = reader_of(name)
        matched = [r for r in candidates if r["name"] == name and r["reader"] == reader and r["hash"] == now]
        if not matched:
            continue
        newest = matched[-1]["check"]
        last = [r for r in matched if r["check"] == newest][-1]
        if name in keep:
            out.append({"name": name, "reader": reader, "round": 2, "result": "remained"})
        elif last["verdict"] in ("passed", "remained"):
            out.append({"name": name, "reader": reader, "round": last["round"], "result": last["verdict"]})
    return out


def remained_section(remained: Sequence[Mapping[str, Any]]) -> str:
    """残った文から作る、マーカー付きの「読み手役が残した文」のセクション（承認資料の末尾に足す）。残った文が無ければ空。"""
    lines = [f"- {_label(r['name'])}: {q['quote']}（{q['why']}）" for r in remained for q in r["quotes"]]
    if not lines:
        return ""
    return "\n".join(["", "", BEGIN, REMAINED_HEADING, "", *lines, END, ""])


def _label(name: str) -> str:
    return _TARGET_LABEL[name] if name in _TARGET_LABEL else name


def split_section(text: str, expected: str) -> tuple[str, bool]:
    """末尾の 1 組のマーカーの範囲が `expected` と 1 字も違わなければ、それを除いた本文と真。違えば元の本文と偽。"""
    if expected and text.endswith(expected) and text.count(BEGIN) == 1 and text.count(END) == 1:
        return text[: -len(expected)], True
    return text, False


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def material_hashes(text: str, expected: str) -> tuple[str, str]:
    """承認資料の（読ませた本文のハッシュ, ファイル全体のハッシュ）。読ませた本文は `split_section` の規則でセクションを除く。"""
    body, _ = split_section(text, expected)
    return sha256(body), sha256(text)


def row_fingerprint(row: Mapping[str, Any]) -> str:
    """記録の行の指紋（キーの順を固定した JSON の SHA-256）。"""
    return sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


# --- 承認の質問の決まった形（#4446 の決定 1） ---------------------------------------------------------------

_DESIGN_Q = "設計 Pull Request #{n}（{title}）をマージしてよいですか"
_RELEASE_Q = "リリースの Pull Request #{n}（{title}）を承認し、{approve}してよいですか"
_HEADER = "承認"


def gate_questions(gate: str, number: int, title: str, form: Optional[str]) -> list[dict[str, Any]]:
    """承認ゲートの `AskUserQuestion` の `questions`（`metadata` を持たない）。同じ引数から同じキーの質問を作る。

    `release` はリリースの形 `form` の表の行の列 `approve` を操作に入れる。形が無い・表に無い・扱わない形なら `FormMissing`。
    """
    name = title.strip()
    if gate == "design":
        text = _DESIGN_Q.format(n=number, title=name)
        yes = "設計 Pull Request をマージし、実装へ進みます"
    else:
        approve = _approve_of(form)
        text = _RELEASE_Q.format(n=number, title=name, approve=approve)
        yes = f"リリースの Pull Request を承認し、{approve}します"
    options = [
        {"label": "承認する", "description": yes},
        {"label": "変更を求める", "description": "直す所を伝え、直した後にもう一度承認を求めます"},
    ]
    return [{"question": text, "header": _HEADER, "multiSelect": False, "options": options}]


def _approve_of(form: Optional[str]) -> str:
    """リリースの形の、承認の質問で問う操作（表の列 approve）。"""
    if form is None:
        raise FormMissing("設定にリリースの形（branch_strategy.release.form）が無い")
    try:
        return forms_decide.form_of(form).approve or _no_approve(form)
    except forms_decide.FormUnsupported as exc:
        raise FormMissing(str(exc)) from exc


def _no_approve(form: str) -> str:
    raise FormMissing(f"リリースの形 {form} の行に承認の質問で問う操作（approve）が無い")


def prompt(entry: Mapping[str, Any]) -> str:
    """読み手役への指示。文書は加工しない（補足を外した文書・直した部分だけの文書は、頼む側が先に別のファイルへ作る）。"""
    _require_prompt(entry)
    files, reader, kind = entry["files"], entry["reader"], entry["questions_kind"]
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


def check(entry: Mapping[str, Any], root: Path, hub: Callable[[str], GitHub]) -> list[str]:
    """承認の手順用: 質問を出す前に足りないもの（`reading_decide.missing`）。PR の今の head は GitHub から読む。

    記録だけで足りないと決まれば、fetch せず GitHub も読まずに返す。後で動く hook が新しい head と比べられるように、
    先に記録の PR のリモート追跡ブランチを `git fetch` で新しくする（失敗しても続ける）。
    GitHub を読めなければ `GitHubError`、head を得られない PR があれば `HeadUnknown`（足りないものがあればそちらを返す）。
    """
    _require_keys(entry, CHECK_KEYS)
    key, prs = _key(entry["questions"]), entry["prs"]
    if not (isinstance(prs, list) and all(type(n) is int and n > 0 for n in prs)):
        raise ReadingInvalid("prs は、承認する PR の番号の配列にする")
    git = Git(root)
    remote = git.remote()
    repo, found = git.remote_repo(remote), rows()
    row = rd.used_row(key, repo, found)
    short = rd.missing(key, repo, found, {}, prs)
    if short or row is None or not row["prs"]:
        return short
    git.fetch(remote, *(f"+refs/heads/{pr['branch']}:refs/remotes/{remote}/{pr['branch']}" for pr in row["prs"]))
    # 足りないものが無いので、承認する PR はすべて記録の行にある
    pulls = hub(repo).pulls({pr["number"] for pr in row["prs"]})
    short = rd.missing(key, repo, found, {n: pull.head_sha for n, pull in pulls.items() if pull is not None}, prs)
    unread = sorted(n for n, pull in pulls.items() if pull is None)
    if unread and not short:
        raise HeadUnknown("GitHub に PR が無い: " + "・".join(f"#{n}" for n in unread))
    return short


def _require_prompt(entry: Any) -> None:
    _require_keys(entry, PROMPT_KEYS)
    files, reader, kind = entry["files"], entry["reader"], entry["questions_kind"]
    if not (isinstance(files, list) and files and all(_is_text(f) for f in files)):
        raise ReadingInvalid("files は、読ませるファイルのパスの空でない配列にする")
    if not (isinstance(reader, Mapping) and set(reader) == set(READER) and all(_is_text(v) for v in reader.values())):
        raise ReadingInvalid(f"reader は、{'・'.join(READER)} の 3 つの空でない文字列を持つオブジェクトにする")
    if not (isinstance(kind, str) and (kind == "none" or kind in RESTATE)):
        raise ReadingInvalid(f"questions_kind は {'・'.join(RESTATE)}・none のどれかにする")


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _key(questions: Any) -> str:
    if not (isinstance(questions, list) and questions and all(_is_question(q) for q in questions)):
        raise ReadingInvalid("questions は、AskUserQuestion に渡す questions と同じ形の空でない配列にする")
    return rd.question_key(questions)


def _is_question(q: Any) -> bool:
    return isinstance(q, dict) and isinstance(q.get("question"), str) and bool(q["question"].strip()) and isinstance(q.get("options"), list)


def _require_keys(entry: Any, keys: set[str]) -> None:
    if not isinstance(entry, Mapping) or set(entry) != keys:
        raise ReadingInvalid(f"入力は、キー {'・'.join(sorted(keys))} だけをすべて持つ JSON のオブジェクトにする")
