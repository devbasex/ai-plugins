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

from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub
from ndf import contracts
from ndf.contracts import facts, internal, writing
from ndf.core import branch_strategy
from ndf.domains.records import jsonl, mvv, pull_view
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
# 2 回目にも残った文の読ませた行の合否
REMAINED = "remained"
# 変更要求の行の直す対象（PR の題・本文）
SENT_BACK_TARGETS = contracts.schema_enum(DOCS_SCHEMA, "/properties/targets/items")
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


def materials_path(repo: str, gate: str, number: int) -> Path:
    """承認資料のコピー（表 ndf-files の reading-materials。リポジトリ `repo`（owner/name）の PR とゲートごとに 1 つ）。"""
    pattern = facts.value("ndf-files", "reading-materials", "path")
    return mvv.record_path("reading-docs").parent / pattern.replace("<repo>", repo.replace("/", "__")).replace("<gate>", gate).replace(
        "<n>", str(number)
    )


def append_doc(row: Mapping[str, Any]) -> dict[str, Any]:
    """読ませたものの記録へ 1 行足し、書いた行を返す。形が違えば書かずに `ReadingInvalid`。"""
    return _append_checked(docs_path, row, kind="reading-docs")


def _append_checked(path: Callable[[], Path], row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    """記録へ 1 行足し、書いた行を返す。形が違えば書かずに `ReadingInvalid`。"""
    try:
        return jsonl.append_locked(path(), row, kind=kind)
    except internal.InternalError as exc:
        if exc.kind != "invalid":
            raise
        raise ReadingInvalid(str(exc)) from exc


DONE_VALUES = contracts.schema_enum(DOCS_SCHEMA, "/$defs/done_values")
# 完了の行の承認資料と PR の本文のハッシュと、記録の行の指紋の名前
_, _, MATERIAL_SHA, MATERIAL_FILE_SHA, BODY_SHA, FINGERPRINT = DONE_VALUES


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


def _same_gate(row: Mapping[str, Any], repo: str, gate: str, number: int) -> bool:
    """行のリポジトリ・ゲート・PR が引数と同じか。"""
    return row["repo"] == repo and row["gate"] == gate and row["pr"] == number


def last_done(rows: Sequence[Mapping[str, Any]], repo: str, gate: str, number: int) -> Optional[Mapping[str, Any]]:
    """同じ PR とゲートで最後に完了した確認の完了の行。"""
    found = None
    for row in rows:
        if row.get("kind") == "done" and _same_gate(row, repo, gate, number):
            found = row
    return found


def remained_of(rows: Sequence[Mapping[str, Any]], repo: str, gate: str, number: int) -> list[Mapping[str, Any]]:
    """同じ PR とゲートで最後に完了した確認の、2 回目にも残った文の行。"""
    done = last_done(rows, repo, gate, number)
    return [] if done is None else remained_in(rows, done["check"])


def remained_in(rows: Iterable[Mapping[str, Any]], check: str) -> list[Mapping[str, Any]]:
    """確認 `check` の、2 回目にも残った文の行。"""
    return [r for r in rows if r.get("kind") == "read" and r["check"] == check and r["verdict"] == REMAINED]


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
    return any(r.get("kind") == "sent_back" and _same_gate(r, repo, gate, number) for r in rows)


def select_reads(
    current: Sequence[Mapping[str, Any]],
    past: Sequence[Mapping[str, Any]],
    hashes: Mapping[str, str],
    carried: Iterable[str],
    base: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """`reading-checks.jsonl` の行の `reads`（設計書の「`reads` の作り方」の 1〜4）。

    `current` は今回読ませた行（完了の行が無くても使う）、`past` は記録の全行（完了した確認の行だけを使う）、`hashes` は対象の名前から
    今の中身のハッシュ、`carried` は前に残った文を引き継いだ対象の名前、`base` は今回の確認のリポジトリ・ブランチ・ゲート・PR。
    候補は、`base` と対象の名前から作るキー（`read_row_key`）の行に限る。
    """
    done = completed(past)
    candidates = [r for r in [*past, *current] if r["kind"] == "read" and (r["check"] in done or r in current)]
    keep = set(carried)
    out: list[dict[str, Any]] = []
    for name, now in hashes.items():
        reader = reader_of(name)
        key = read_row_key({**base, "name": name})
        matched = [r for r in candidates if read_row_key(r) == key and r["reader"] == reader and r["hash"] == now]
        if not matched:
            continue
        newest = matched[-1]["check"]
        last = [r for r in matched if r["check"] == newest][-1]
        if name in keep:
            out.append({"name": name, "reader": reader, "round": 2, "result": REMAINED})
        elif last["verdict"] in ("passed", REMAINED):
            out.append({"name": name, "reader": reader, "round": last["round"], "result": last["verdict"]})
    return out


def remained_section(remained: Sequence[Mapping[str, Any]]) -> str:
    """残った文から作る、マーカー付きの「読み手役が残した文」のセクション（承認資料の末尾に足す）。残った文が無ければ空。"""
    lines = [f"- {target_label(r['name'])}: {q['quote']}（{q['why']}）" for r in remained for q in r["quotes"]]
    if not lines:
        return ""
    return "\n".join(["", "", BEGIN, REMAINED_HEADING, "", *lines, END, ""])


def target_label(name: str) -> str:
    """対象の名前を、承認する人に見せる語にする（文書はパスのまま）。"""
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


def material_matches(text: str, expected: str, done: Mapping[str, Any]) -> bool:
    """承認資料の 2 つのハッシュ（`material_hashes`）が、完了の行 `done` とどちらも同じか。"""
    return material_hashes(text, expected) == (done[MATERIAL_SHA], done[MATERIAL_FILE_SHA])


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


def add(entry: Mapping[str, Any], root: Path, repo: Optional[str] = None) -> dict[str, Any]:
    """確認の記録を 1 行追記し、追記した行を返す。形が違えば `ReadingInvalid` で、追記しない。GitHub を読まない。

    `repo` を省けば作業場所 `root` のリモートから求める。
    """
    _require_keys(entry, ADD_KEYS)
    key = _key(entry["questions"])
    row = {"repo": repo if repo is not None else Git(root).remote_repo(), "question_key": key, "prs": entry["prs"], "reads": entry["reads"]}
    if not rd.has_used_keys(row):
        raise ReadingInvalid("prs は、number（整数）・branch（1 つのブランチの名前）・head（SHA）を持つオブジェクトの配列にする")
    return _append_checked(checks_path, row, kind="reading-check")


def rows() -> list[dict[str, Any]]:
    """記録の行（古い順）。壊れた行（ブランチの名前でない `branch` を含む）は除く。ファイルが無ければ空、開けなければ `OSError`、新しいバージョンの行があれば `InternalError`。"""
    found, _ = jsonl.read_rows(checks_path(), kind="reading-check")
    return [row for row in found if rd.has_used_keys(row)]


def comment_has(comments: Iterable[str], file_sha256: str) -> bool:
    """同じ承認資料のコメントがあるか（改行の CRLF と末尾の改行の有無をそろえて比べる）。gate と check が同じ規則を使う。"""
    for body in comments:
        text = body.replace("\r\n", "\n")
        if file_sha256 in (sha256(text), sha256(text + "\n")):
            return True
    return False


def release_form(root: Path) -> Optional[str]:
    """設定のリリースの形（`branch_strategy.release.form`）。無ければ None（質問を作れずに止まる）。読めなければ `FormMissing`。"""
    try:
        section = branch_strategy.load(Path(Git(root).run("rev-parse", "--show-toplevel").strip())).section
    except (GitError, branch_strategy.StrategyError) as exc:
        raise FormMissing(f"設定のブランチ戦略を読めない: {exc}") from None
    release = section["release"] if isinstance(section.get("release"), dict) else {}
    return release["form"] if isinstance(release.get("form"), str) else None


def check(
    gate: str, number: int, material: Optional[Path], root: Path, hub: Callable[[str], GitHub]
) -> tuple[list[str], list[dict[str, Any]]]:
    """承認の手順用: 質問を出す前に足りないものと、決まった形の質問（設計書の「確認の id と完了の行」）。

    PR を 1 回だけ取得し、その題で質問を作り直し、同じ取得の head・本文・コメントと突き合わせる。記録の行は hook と同じ選び方で選び、
    行の指紋で完了の行と結び付ける。承認資料は、PR の本文の経路では同じ中身のコメントと PR の本文のハッシュ、`material` の経路では
    そのファイルの 2 つのハッシュを比べる。後で動く hook が新しい head と比べられるように、記録の PR のリモート追跡ブランチを
    `git fetch` で新しくする（失敗しても続ける）。GitHub を読めなければ `GitHubError`、PR が無ければ `HeadUnknown`。
    """
    git = Git(root)
    remote = git.remote()
    repo = git.remote_repo(remote)
    view = pull_view.pull_view(hub(repo), number, None)
    if view is None:
        raise HeadUnknown(f"GitHub に PR が無い: #{number}")
    questions = gate_questions(gate, number, view.title, release_form(root) if gate == "release" else None)
    key, found = rd.question_key(questions), rows()
    row = rd.used_row(key, repo, found)
    if row is not None and row["prs"]:
        git.fetch(remote, *(f"+refs/heads/{pr['branch']}:refs/remotes/{remote}/{pr['branch']}" for pr in row["prs"]))
    short = rd.missing(key, repo, found, {number: view.head_sha}, [number])
    if short or row is None:
        return short, questions
    docs = doc_rows()
    done = next((r for r in completed(docs).values() if r[FINGERPRINT] == row_fingerprint(row)), None)
    if done is None:
        return [rd.NO_RECORD], questions
    return (["material_changed"] if _material_changed(done, docs, view, material) else []), questions


def _material_changed(
    done: Mapping[str, Any], docs: Sequence[Mapping[str, Any]], view: pull_view.PullView, material: Optional[Path]
) -> bool:
    if material is None:
        return not comment_has(view.comments, done[MATERIAL_FILE_SHA]) or sha256(view.body) != done[BODY_SHA]
    expected = remained_section(remained_in(docs, done["check"]))
    return not material_matches(material.read_text(encoding="utf-8"), expected, done)


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
