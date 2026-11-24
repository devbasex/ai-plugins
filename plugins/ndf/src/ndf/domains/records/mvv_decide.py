"""MVV の記録の判断（純粋な関数。N8b・#2603・#2614・#2777・#2970）。

- 記録の `repo` はメインディレクトリのパスにする（worktree で打った判定も同じキーで数える。#2603）
- 承認ゲートの回答から覆しの種類を決める。「判定できない」を人が通したものは覆しに数えず、別の数にする
  （AI が決められないときに人へ戻したのは MVV どおりである。#2777）。同じスプリント状態・承認ゲート・PR の組は 1 件
- スプリント MVV 判定は、MVV 判定の入力の全体（スプリント MVV・プロジェクト MVV・共通原則・指示文）が同じで最新の
  判定が「従う」なら、その判定を再利用する（判定の揺れで開発ループを止めない。#2614）
- 改訂の兆候は、ベースブランチの設定（現行のバージョン）で数える（#2970）
- cross-review の指摘のうち、プロジェクト MVV に反するとした指摘と根拠の記述の誤りを記録する形を決める。見送った指摘は
  改訂の兆候にも数える（#4076）
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, NamedTuple, Optional

from ndf import identity
from ndf.contracts import facts
from ndf.domains.records import files
from ndf.domains.records import mvv_material_decide as mm

# 判定の語は承認ゲートの判定（`ndf.progress.gate.checks`）の綴りを使う
from ndf.progress.gate.checks import FOLLOW, NOT_FOLLOW, UNKNOWN

# 覆しの種類: 「従う」を人が退けた・「反する疑い」を人が通した・「判定できない」を人が通した（覆しに数えない）
OVERRIDE_REJECT = "override_reject"
OVERRIDE_PASS = "override_pass"
OVERRIDE_UNKNOWN_PASS = "unknown_pass"
OVERRIDES = (OVERRIDE_REJECT, OVERRIDE_PASS)
# cross-review の修正担当が、MVV の項目に反する指摘を見送った（「判定できない」と合わせて数える）
REVIEW_DISPUTED = "review_disputed"
# MVV 判定の記録の種類（スプリント MVV 判定）
MVV_VALIDATION_SPRINT = "sprint-mvv"
# cross-review の指摘の記録の種類
MVV_REVIEW = "mvv-review"
# cross-review の指摘（ジャーナルの行）の欄: 反する項目・判定したときの MVV を見分ける値・必ず人の承認が要る操作に当たるかと、
# 当たる操作の番号
REVIEW_ITEM, REVIEW_JUDGED, REVIEW_REDLINE, REVIEW_REDLINE_ITEMS = "mvv", "judged_mvv", "redline", "redline_items"
# 指摘の終わり方。必ず人の承認が要る操作に当たる指摘は、記録では終わり方によらず `REVIEW_REDLINE` にする
REVIEW_FIXED, REVIEW_DEFERRED, REVIEW_UNRESOLVED = "fixed", "deferred", "unresolved"
# 同じキーの指摘が 2 件あるとき、後のものほど優先して記録する終わり方の順
_REVIEW_ORDER = (REVIEW_FIXED, REVIEW_UNRESOLVED, REVIEW_DEFERRED, REVIEW_REDLINE)
# NDF が機械で確かめて作った指摘（根拠の記述の誤り）の `by`
BY_NDF = identity.FIXED_WORDS["entrypoint"]
# 終わり方ごとの、記録に残す自由記述の欄
_REVIEW_NOTES = {REVIEW_DEFERRED: "reason", REVIEW_REDLINE: "text"}
# 前の記録と比べる欄（最初の組は MVV と指摘の状態、次の組は記録の中身）
_REVIEW_STATE = ("head", "outcome", "mvv_version", "sha256")
_REVIEW_CONTENT = ("item", "reason", "text", "withheld")
# 判定の行が持つ、判定の中身（承認ゲートの判定が読む。再利用の行へも写す）
VERDICT_DETAILS = ("reasons", "boundary", "basis")
# 改訂の設定（mvv.json の settings.revise_after）のキーと、無いときの閾値
REVISE_AFTER = "revise_after"
OVERRIDE_LIMIT_KEY, UNKNOWN_LIMIT_KEY = "overrides", "unknowns"
DEFAULT_LIMITS = {OVERRIDE_LIMIT_KEY: 3, UNKNOWN_LIMIT_KEY: 5}
# 本文の `## <名前>` の見出しと、Value のセクションの行頭の番号
_H2 = re.compile(r"^##[ \t]+(.+?)[ \t\r]*$", re.MULTILINE)
_NUMBERED = re.compile(r"^([0-9]+)\.[ \t]", re.MULTILINE)
# 項目を拾う本文のセクション
MISSION, VISION, VALUE, REDLINES = "Mission", "Vision", "Value", "必ず人の承認が要る操作"
# 根拠の記述（`- 根拠: Value 5（MVV バージョン 11）`）の始まりと、その中のバージョンの表記・項目の区切り
_CITATION = re.compile(r"\s*(?:-\s+)?根拠:(.*)")
_VERSION_MARK = "（MVV"
_CITED_VERSION = re.compile(r"（MVV バージョン ([0-9]+)(?:・スプリント MVV [0-9a-f]{8})?）")
_ITEM_SEPARATOR = re.compile(r" / |・")
# プロジェクト MVV の項目でないので突き合わせない項目（スプリント MVV の項目と、当たる項目が無いことの明記）
_SPRINT_ITEM, _NO_ITEM = "スプリント", "根拠なし"
# 根拠の記述の誤りの種類: 承認済みでないバージョン・そのバージョンに無い項目・バージョンを読めない
CITATION_VERSION, CITATION_UNKNOWN, CITATION_FORMAT = "version", "unknown", "format"


class MvvError(ValueError):
    """MVV の設定・本文が読めないか壊れている・メインディレクトリを解けない。"""


def override_kind(verdict: Optional[str], approved: bool) -> Optional[str]:
    """MVV 判定と人の回答から覆しの種類を決める。覆しでなければ None。"""
    if verdict == FOLLOW and not approved:
        return OVERRIDE_REJECT
    if verdict == NOT_FOLLOW and approved:
        return OVERRIDE_PASS
    if verdict == UNKNOWN and approved:
        return OVERRIDE_UNKNOWN_PASS
    return None


def signal_kinds(verdict: Optional[str], approved: bool) -> list[str]:
    """承認ゲートの回答 1 件から書く兆候の種類。「判定できない」は人の回答によらず `unknown` を数える。"""
    kind = override_kind(verdict, approved)
    return ([UNKNOWN] if verdict == UNKNOWN else []) + ([kind] if kind is not None else [])


@dataclass(frozen=True)
class Signals:
    """改訂の兆候。`overrides` は覆し、`unknown_pass` は「判定できない」を人が通した数（覆しに数えない）。"""

    mvv_version: Optional[int]
    overrides: int
    unknown_pass: int
    unknown: int
    revision_due: bool


def _pair(row: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return row.get("state"), row.get("gate"), row.get("pr")


def _settings(decl: Mapping[str, Any]) -> dict[str, int]:
    settings = decl.get("settings")
    raw = settings.get(REVISE_AFTER) if isinstance(settings, Mapping) else None
    got = dict(DEFAULT_LIMITS)
    if isinstance(raw, Mapping):
        got.update({k: int(raw[k]) for k in DEFAULT_LIMITS if isinstance(raw.get(k), int)})
    return got


def latest(decl: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    """設定の現行のバージョンの記録（versions の最後）。無ければ None。"""
    versions = decl.get("versions")
    if isinstance(versions, list) and versions and isinstance(versions[-1], Mapping):
        return versions[-1]
    return None


def current_version(decl: Mapping[str, Any]) -> Optional[int]:
    """設定の現行のバージョン（versions の最後の version）。無ければ None。"""
    last = latest(decl)
    v = last.get("version") if last is not None else None
    return v if isinstance(v, int) else None


class State:
    """MVV の状態の語（承認ゲートの検査 1 の `MvvFacts.status` と同じ綴り）。"""

    APPROVED = "approved"
    MISMATCH = "mismatch"
    UNAPPROVED = "unapproved"
    ABSENT = "absent"
    UNREADABLE = "unreadable"


def status(decl: Optional[Mapping[str, Any]], body_sha256: Optional[str]) -> str:
    """設定と本文の sha256 から MVV の状態を決める（設定が無ければ absent、本文が承認の記録と違えば mismatch）。"""
    if decl is None:
        return State.ABSENT
    last = latest(decl)
    if last is None or not isinstance(last.get("sha256"), str):
        return State.UNAPPROVED
    if body_sha256 is None:
        return State.UNREADABLE
    return State.APPROVED if last["sha256"] == body_sha256 else State.MISMATCH


def identity_of(state: str, version: Optional[int] = None, sha256: Optional[str] = None, principles_sha256: str = "") -> str:
    """読んだ MVV を見分ける値。承認済みなら状態・バージョン・本文の sha256・共通原則の sha256、ほかは状態だけで決まる。

    承認済みでない MVV は共通原則をレビューに使わないので、共通原則（NDF の更新で変わる）が変わっても同じ値にする。
    """
    if state != State.APPROVED:
        return f"{state}:::"
    return f"{state}:{version}:{sha256}:{principles_sha256}"


def approved_form(decl: Optional[Mapping[str, Any]], body: bytes) -> bytes:
    """本文のバイト列を、承認の記録の sha256 と合う改行の形にして返す（合う形が無ければ元のまま）。

    `core.autocrlf`・`eol=crlf` は作業場所と Git の blob で改行だけを変えるので、どちらで承認した記録も、どちらのバイト列
    からも同じに判定する。改行のほかの違いは合う形が無いので `mismatch` のまま残る。
    """
    last = latest(decl) if decl is not None else None
    approved = last.get("sha256") if last is not None else None
    lf = body.replace(b"\r\n", b"\n")
    for form in (body, lf, lf.replace(b"\n", b"\r\n")):
        if hashlib.sha256(form).hexdigest() == approved:
            return form
    return body


def body_path(decl: Mapping[str, Any]) -> str:
    """本文のパス（リポジトリの根からの POSIX のパス）。設定の `body`（文字列でなければ既定）を設定のディレクトリから解く。

    絶対パス・`..` を含む値・空は `MvvError`。
    """
    held = decl.get("body")
    body = held if isinstance(held, str) else facts.value("ndf-files", "mvv-body", "path")
    path = PurePosixPath(body)
    if not body or path.is_absolute() or ".." in path.parts:
        raise MvvError(f"MVV の設定の body は設定のディレクトリの中の相対パスにする: {body!r}")
    return str(PurePosixPath(files.relpath("mvv")).parent / path)


def _section_items(name: str, text: str) -> list[str]:
    if name in (MISSION, VISION):
        return [name]
    if name == VALUE:
        return [f"{VALUE} {no}" for no in _NUMBERED.findall(text)]
    return list(mm.redlines(text)) if name == REDLINES else []


def items_of(body: str, principles: str) -> tuple[str, ...]:
    """MVV の本文と共通原則の本文に実在する項目（`Mission`・`Value 1`・`P1`・`C1` など）。重ねずに出た順。"""
    heads = list(_H2.finditer(body))
    ends = [h.start() for h in heads[1:]] + [len(body)]
    got = [i for h, end in zip(heads, ends, strict=True) for i in _section_items(h.group(1), body[h.end() : end])]
    return tuple(dict.fromkeys([*got, *mm.redlines(principles)]))


class Citation(NamedTuple):
    """根拠の記述を解析した結果。`cited` は引用文とバージョンの表記を除いた項目の部分、`version` は読めなければ None。"""

    cited: str
    items: tuple[str, ...]
    version: Optional[int]


def _without_quotes(text: str) -> str:
    """`「` から対応する `」`（無ければ末尾）までを除く。引用文の中の `（` や区切りを項目の区切りと読まないため。"""
    out, depth = [], 0
    for ch in text:
        if ch == "「":
            depth += 1
        if depth == 0:
            out.append(ch)
        if ch == "」" and depth:
            depth -= 1
    return "".join(out)


def parse_citation(line: str, decision: bool) -> Optional[Citation]:
    """1 行を根拠の記述として解析する。根拠の記述でなければ None。

    根拠の記述は `根拠:` で始まる（前の空白と `- ` は除く）行のうち、`MVV` を含むか、決定の見出しの下にある（`decision`）もの。
    `decision` があるので、バージョンの表記を消した決定も根拠の記述として残り、誤りになる。
    """
    found = _CITATION.match(line)
    if found is None or not (decision or "MVV" in line):
        return None
    head, mark, tail = _without_quotes(found.group(1)).partition(_VERSION_MARK)
    cited = head.strip()
    # `）` までを形に合わせる（`（MVV バージョン 11x）` を 11 と読まない）。`）` の後ろの文は読まない
    version = _CITED_VERSION.match(mark + tail)
    named = (item.strip() for item in _ITEM_SEPARATOR.split(cited))
    items = tuple(i for i in named if i != _NO_ITEM and not i.startswith(_SPRINT_ITEM))
    return Citation(cited, items, int(version.group(1)) if version is not None else None)


def _citation_error(catalog: Mapping[int, Sequence[str]], path: str, line: int, text: str, decision: bool) -> Optional[dict[str, Any]]:
    got = parse_citation(text, decision)
    if got is None:
        return None
    unknown: list[str] = []
    if got.version is None:
        kind = CITATION_FORMAT
    elif got.version not in catalog:
        kind = CITATION_VERSION
    else:
        kind = CITATION_UNKNOWN
        unknown = [i for i in got.items if i not in catalog[got.version]]
        if not unknown:
            return None
    return {"path": path, "line": line, "cited": got.cited, "kind": kind, "version": got.version, "unknown": unknown}


def citation_errors(lines: Sequence[tuple[str, int, str, bool]], catalog: Mapping[int, Sequence[str]]) -> list[dict[str, Any]]:
    """根拠の記述の誤りを、`lines` の順に返す。

    `lines` は（パス・行の番号・1 行の文・決定の見出しの下か）、`catalog` は承認済みのバージョン → その項目。
    `cited`・`unknown` は文書から取ったままの文字列なので、公開する前に呼び手が秘密の値を伏せる。
    """
    found = (_citation_error(catalog, *row) for row in lines)
    return [error for error in found if error is not None]


def count_signals(rows: Iterable[Mapping[str, Any]], decl: Mapping[str, Any], repo: str) -> Signals:
    """設定の現行のバージョンで兆候を数える。覆しは同じ（スプリント状態・承認ゲート・PR）の組を 1 件にまとめる。

    cross-review の指摘の見送りは、同じ（PR・指摘のキー）を 1 件にまとめて「判定できない」に足す（指摘の id は起動ごとに変わる）。
    `repo` はメインディレクトリのパスで、記録の `repo` がこれと同じ行だけを数える。
    """
    version = current_version(decl)
    mine = [r for r in rows if r.get("repo") == repo and r.get("mvv_version") == version]
    pairs: dict[str, set[tuple[Any, ...]]] = {k: set() for k in (*OVERRIDES, OVERRIDE_UNKNOWN_PASS, UNKNOWN, REVIEW_DISPUTED)}
    for r in mine:
        kind = r.get("kind")
        if kind in pairs:
            pairs[kind].add((r.get("pr"), r.get("key")) if kind == REVIEW_DISPUTED else _pair(r))
    limits = _settings(decl)
    overrides = len(pairs[OVERRIDE_REJECT]) + len(pairs[OVERRIDE_PASS])
    unknown = len(pairs[UNKNOWN]) + len(pairs[REVIEW_DISPUTED])
    revision_due = overrides >= limits[OVERRIDE_LIMIT_KEY] or unknown >= limits[UNKNOWN_LIMIT_KEY]
    return Signals(version, overrides, len(pairs[OVERRIDE_UNKNOWN_PASS]), unknown, revision_due)


class Judged(NamedTuple):
    """今の MVV で判定した cross-review の指摘 1 件。`row` はジャーナルの行、`outcome` は記録する終わり方、`item` は反する MVV の項目
    （根拠の記述の誤りは None）。"""

    row: Mapping[str, Any]
    outcome: str
    item: Optional[str]

    @property
    def note(self) -> Optional[str]:
        """記録に残す自由記述の欄の名前（見送りの理由・必ず人の承認が要る操作に当たる指摘の本文）。"""
        return _REVIEW_NOTES.get(self.outcome)

    @property
    def free_texts(self) -> list[str]:
        """記録に残す自由記述。呼び手が秘密などを伏せる。"""
        held = [self.row.get(self.note)] if self.note is not None else []
        held += [self.row.get("cited")] if self.item is None else []
        return [t for t in held if isinstance(t, str)]


def judged_findings(rows: Iterable[Mapping[str, Any]], mvv: str) -> list[Judged]:
    """cross-review の指摘のうち、MVV `mvv`（`identity_of` の値）で判定したもの。前の MVV での判定は含まない。"""
    out = []
    for row in rows:
        if row.get(REVIEW_JUDGED) != mvv or row.get("outcome") not in _REVIEW_ORDER:
            continue
        if row.get(REVIEW_REDLINE):
            out.append(Judged(row, REVIEW_REDLINE, "・".join(row[REVIEW_REDLINE_ITEMS])))
        elif REVIEW_ITEM in row or row.get("by") == BY_NDF:
            out.append(Judged(row, row["outcome"], row.get(REVIEW_ITEM)))
    return out


def review_entry(found: Judged, shown: Optional[Mapping[str, str]]) -> dict[str, Any]:
    """MVV の記録の、指摘ごとに違う欄。`shown` は自由記述 → 伏せた後の値で、None なら自由記述を書かずに `withheld` を置く。"""
    out: dict[str, Any] = {"id": str(found.row["id"]), "outcome": found.outcome}
    if found.item is not None:
        out["item"] = found.item
    if shown is None:
        return out | {"withheld": True}
    held = {"item": found.row.get("cited")} if found.item is None else {}
    held |= {found.note: found.row.get(found.note)} if found.note is not None else {}
    return out | {k: shown[v] for k, v in held.items() if isinstance(v, str)}


def one_per_key(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """同じキーの記録を、人の判断が要る終わり方を優先して 1 件にする（同じ終わり方なら後の記録）。"""
    out: dict[str, dict[str, Any]] = {}
    for e in entries:
        held = out.get(e["key"])
        if held is None or _REVIEW_ORDER.index(e["outcome"]) >= _REVIEW_ORDER.index(held["outcome"]):
            out[e["key"]] = e
    return list(out.values())


def review_recorded(last: Optional[Mapping[str, Any]], entry: Mapping[str, Any]) -> bool:
    """同じキーの最後の記録 `last` があるので、`entry` を書かなくてよいか。

    伏せた記録は、伏せずに書いた同じ状態の記録があれば書かない。伏せた記録の後の、伏せない記録は書く。
    """
    if last is None:
        return False
    keys = _REVIEW_STATE if entry.get("withheld") and not last.get("withheld") else _REVIEW_STATE + _REVIEW_CONTENT
    return all(last.get(k) == entry.get(k) for k in keys)


def signals_ref(base_branch: str, remote: str) -> str:
    """改訂の兆候を数える設定を読む参照（ベースブランチ。本番のブランチの古いバージョンを読まない）。"""
    return f"{remote}/{base_branch}"


def mvv_validation_key(inputs: Mapping[str, str]) -> str:
    """MVV 判定の入力の全体を見分ける値（sha256）。どれか 1 字でも変われば別の値になる。"""
    blob = json.dumps(dict(sorted(inputs.items())), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def reusable(
    rows: Iterable[Mapping[str, Any]], key: str, excluded: Iterable[tuple[int, Optional[Mapping[str, Any]]]] = ()
) -> Optional[Mapping[str, Any]]:
    """同じ MVV 判定の入力の最新の判定が「従う」なら、その行（再利用の元）。無いか最新が「従う」でなければ None。

    `repo` は見ない（メインディレクトリと worktree のどちらから打っても同じ）。壊れた行（辞書でない・形が違う）は呼び手が除き、
    `excluded`（それより前の読めた行の数と、辞書として読めた中身）で渡す。最新の判定より後に、同じ入力の判定かもしれない除いた行
    （キーが同じか、キーを文字列として読めない）があれば、それが最新の判定かもしれないので None（古い「従う」へさかのぼらない）。
    """
    last_mvv_validation: Optional[Mapping[str, Any]] = None
    seen = 0
    for n, r in enumerate(rows, 1):
        if r.get("kind") == MVV_VALIDATION_SPRINT and r.get("key") == key:
            last_mvv_validation, seen = r, n
    if any(before >= seen and (raw is None or not isinstance(raw.get("key"), str) or raw["key"] == key) for before, raw in excluded):
        return None
    if last_mvv_validation is None or last_mvv_validation.get("verdict") != FOLLOW:
        return None
    return last_mvv_validation


def reuse_row(origin: Mapping[str, Any], at: str, repo: str) -> dict[str, Any]:
    """再利用したときに足す行。元の判定の時刻と、判定の理由・レッドライン（元の行にあれば）を持ち、費用と秒の実測値を持たない。"""
    first = origin["reused_from"] if "reused_from" in origin else origin.get("at")
    held = {k: origin[k] for k in VERDICT_DETAILS if k in origin}
    return {"at": at, "kind": MVV_VALIDATION_SPRINT, "key": origin["key"], "verdict": FOLLOW, "repo": repo, "reused_from": first, **held}


def judged_row(key: str, verdict: str, at: str, repo: str, cost: Mapping[str, Any]) -> dict[str, Any]:
    """LLM が判定したときに足す行（MVV 判定の入力を見分ける値を持ち、次の MVV 判定が再利用できる）。"""
    return {"at": at, "kind": MVV_VALIDATION_SPRINT, "key": key, "verdict": verdict, "repo": repo, **cost}
