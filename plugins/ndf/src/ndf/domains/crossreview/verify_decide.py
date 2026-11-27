"""判定役の判断（#4363。純粋な関数）。

判定役は、相手役が書いた重い指摘（critical・major）が実際に起きるかを確かめる読むだけの AI である。ここは、渡す指摘を
選び（`select`）、指示を組み（`instruction`）、回答を読み（`read`）、下げてよいかを決める（`lowers`）。起動は持たず、
指摘の行へ書くのは `journal.FindingJournal.apply_checks` である。指示の文は `contracts/data/review-criteria.json` の `verify`。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.domains.crossreview import criteria_decide
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.records import mvv_decide

# ラウンドの記録で、そのラウンドが判定役の起動を試み、出力を保存できたときの名前（ファイル名の拡張子より前）を持つキー。回答の有無は表さない
ROUND_KEY = "judge_output"
# 前の head からの差分の抜粋（`excerpt_lines`）の先頭の行。行の書き方も判定役へ伝える
EXCERPT_NOTE = (
    "前の head からの差分の抜粋である。削除行は「削除 旧<前の head の行番号> 新<削除が起きた今の位置>: <本文>」と書く（新は、削除した所の"
    "後ろに行があればその行番号、末尾なら最終行の次の番号、今のファイルが空か無ければ 0）。追加行のうち全差分に追加行として載らないものは"
    "「追加 新<行番号>: <本文>」と書く。全差分に載るものは本文を書かず、ファイルごとに 1 行「追加（本文は全差分） 新<最初>-<最後>,…」と書く。"
)
# hunk の見出し。取り出すのは、前の側と今の側の最初の行の番号
_HUNK_HEAD = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)")
# 指示に載せる指摘の欄（書いた相手役 `by` は載せない）
_SHOWN = ("id", "severity", "text", "impact", "path", "line", "section", "diffs")


class Verdict(NamedTuple):
    """指摘 1 件への回答。`holds` は起きると示せたか（None は資料が足りず判断できない）。`tracks` は指摘の種類の表にある
    値だけで、`unknown` は表に無い値が書かれていたか。`invalid` は不正な回答から保護の種類だけを残したもの（`holds` は None）。"""

    id: str
    holds: Optional[bool]
    condition: str = ""
    path: str = ""
    result: str = ""
    reason: str = ""
    tracks: tuple[str, ...] = ()
    unknown: bool = False
    invalid: bool = False


class Answers(NamedTuple):
    """判定役の回答。`verdicts` は番号から、有効な回答、判断できない回答、保護の種類を示した不正な回答（`Verdict.invalid`）を持つ。"""

    verdicts: Mapping[str, Verdict]
    warnings: tuple[str, ...] = ()
    # 起動を試みて保存できた出力の名前（回答が無くても持つ）。起動を試みていないか、保存できた出力が無ければ空
    record: str = ""


def _never_asked(row: Mapping[str, Any]) -> bool:
    """判定役へ渡さない指摘（必ず人の承認が要る操作の指摘と、NDF が機械で作った指摘）か。"""
    return bool(row.get(fin.REDLINE)) or row.get("by") == mvv_decide.BY_NDF


def _never_lowered(row: Mapping[str, Any]) -> bool:
    """回答がどうであっても下げない指摘（MVV に反する指摘と、保護の種類を持つ指摘）か。"""
    return bool(row.get(mvv_decide.REVIEW_ITEM)) or bool(row.get(criteria_decide.PROTECT))


def select(rows: Sequence[Mapping[str, Any]], use_tracks: bool = False) -> list[Mapping[str, Any]]:
    """呼び手が渡す指摘（このラウンドで新しく出たか戻った critical・major）のうち、判定役へ渡すもの。

    下がらない指摘（`_never_lowered`）は、ほかに渡す指摘があるか、呼び手が回答の指摘の種類を使う（`use_tracks`）ときだけ渡す。"""
    asked = [row for row in rows if not _never_asked(row)]
    if use_tracks or any(not _never_lowered(row) for row in asked):
        return asked
    return []


def _hunk_lines(diff: str) -> Iterator[tuple[tuple[str, ...], int, int, str]]:
    """差分の hunk の行ごとに、ファイルの見出し、前の側と今の側の行番号、行を返す。"""
    header: list[str] = []
    old = new = 0
    in_hunk = False
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            header, in_hunk = [line], False
        elif found := _HUNK_HEAD.match(line):
            old, new, in_hunk = int(found[1]), int(found[2]), True
        elif not in_hunk:
            header.append(line)
        else:
            yield tuple(header), old, new, line
            old += line.startswith((" ", "-"))
            new += line.startswith((" ", "+"))


def _place(header: Sequence[str], number: int) -> tuple[str, int]:
    """追加行の場所（今の側のパスの見出しと、行番号）。"""
    return next((line for line in header if line.startswith("+++ ")), ""), number


def excerpt_lines(since: str, full: str) -> str:
    """前の head からの差分 `since` を、変わった行の抜粋にする。変わった行が無ければ空。`full` は全差分で、今の側は同じ head。

    直前の修正で何を変えたかは、全差分からは分からない（削除は載らないことがあり、追加は前からの追加と見分けられない）。
    `since` の全体を渡すと入力トークンが増えるので、前後の行は渡さず、`full` に追加行として本文が載る行は範囲だけにする。
    `full` に載らない追加行（分岐点にあった行を戻したもの）は、判定役が読んだことを抜粋の読了で確かめられるように本文も書く。"""
    listed = {_place(header, new) for header, _, new, line in _hunk_lines(full) if line.startswith("+")}
    # ファイルの見出しごとの、本文を書く行と、範囲だけを書く追加行の [最初, 最後]
    files: dict[tuple[str, ...], tuple[list[str], list[list[int]]]] = {}
    for header, old, new, line in _hunk_lines(since):
        if not line.startswith(("-", "+")):
            continue
        texts, ranges = files.setdefault(header, ([], []))
        if line.startswith("-"):
            texts.append(f"削除 旧{old} 新{new}: {line[1:]}")
        elif _place(header, new) not in listed:
            texts.append(f"追加 新{new}: {line[1:]}")
        elif ranges and ranges[-1][1] == new - 1:
            ranges[-1][1] = new
        else:
            ranges.append([new, new])
    out: list[str] = []
    for header, (texts, ranges) in files.items():
        out += [*header, *texts]
        if ranges:
            out.append("追加（本文は全差分） 新" + ",".join(f"{first}-{last}" for first, last in ranges))
    return "".join(f"{line}\n" for line in (EXCERPT_NOTE, *out)) if out else ""


def listing(rows: Sequence[Mapping[str, Any]]) -> str:
    """判定役が読む指摘の一覧のファイルの中身（指摘 1 件が JSON の 1 行）。誰が書いた指摘かは載せない。"""
    return "".join(json.dumps({k: row[k] for k in _SHOWN if row.get(k) is not None}, ensure_ascii=False) + "\n" for row in rows)


def instruction(listed: Path, diffs: Sequence[Path], worktree: Path, criteria: Sequence[str]) -> str:
    """判定役への指示。`listed` は指摘の一覧（`listing`）のファイル、`criteria` は指摘の基準の行。指示はプロセスの引数に
    載るので、指摘の文は書かずにファイルのパスだけを書く。`diffs` は確かめに使う差分のファイルで、2 つ以上なら、どの指摘が
    どれに基づくかは一覧の行の `diffs` が持つ。"""
    text = criteria_decide.verify_text()
    kinds = {"protect": "・".join(criteria_decide.protect_kinds()), "all": "・".join(criteria_decide.track_kinds())}
    return "\n".join(
        [
            *text["steps"],
            f"作業場所: {worktree}",
            f"このラウンドの差分: {diffs[0]}"
            if len(diffs) == 1
            else f"差分: {'・'.join(map(str, diffs))}（指摘が基づく差分は、一覧のその行の diffs にある）",
            "## 指摘の基準",
            *criteria,
            "## 指摘の一覧",
            text["data"],
            f"指摘の一覧のファイル: {listed}",
            "## 回答の形",
            text["tracks"].format(**kinds),
            text["answer"],
            text["shape"],
        ]
    )


def _filled(value: object) -> str:
    """文字列なら前後の空白を除いた値、ほかは空。"""
    return value.strip() if isinstance(value, str) else ""


def _tracks(value: object) -> tuple[tuple[str, ...], bool]:
    """回答の `tracks` を、表にある種類（表の順）と、回答の形から外れた値があったかに分ける。並びでない値は形から外れた値と
    するが、表の種類を 1 つの文字列で書いたものは、分類を失わないように種類として拾う。"""
    if value is None:
        return (), False
    kinds = criteria_decide.track_kinds()
    if not isinstance(value, list):
        return tuple(k for k in kinds if k == value), True
    return tuple(k for k in kinds if k in value), any(v not in kinds for v in value)


def _verdict(fid: str, item: Mapping[str, Any]) -> Optional[Verdict]:
    """回答の 1 件。起きると示せたのに条件・経路・結果が欠ける、成り立たないのに理由か `tracks` の配列が無い（分類を確かめ
    られない）、`holds` が無いか真偽値でも null でもない回答は None（不正）。"""
    if "holds" not in item:
        return None
    holds = item["holds"]
    condition, path, result, reason = (_filled(item.get(k)) for k in ("condition", "path", "result", "reason"))
    if holds is True:
        valid = bool(condition and path and result)
    elif holds is False:
        valid = bool(reason) and isinstance(item.get("tracks"), list)
    else:
        valid = holds is None
    if not valid:
        return None
    tracks, unknown = _tracks(item.get("tracks"))
    return Verdict(fid, holds, condition, path, result, reason, tracks, unknown)


def _invalid(fid: str, item: Mapping[str, Any]) -> Optional[Verdict]:
    """不正な回答の 1 件。回答としては使わないが、示した保護の種類は行に残すので（#4363 の不変条件 I4）、その種類だけを持つ
    回答にする。保護の種類が無ければ None。"""
    protect = criteria_decide.protect_kinds()
    kinds = tuple(k for k in _tracks(item.get("tracks"))[0] if k in protect)
    return Verdict(fid, None, tracks=kinds, invalid=True) if kinds else None


def _repeated(fid: str, items: Sequence[Mapping[str, Any]]) -> Verdict:
    """同じ番号への複数の回答。どれが正しいか決められないので判断できないとし、どれかが示した指摘の種類はすべて残す。"""
    shown = {kind for item in items for kind in _tracks(item.get("tracks"))[0]}
    kinds = tuple(k for k in criteria_decide.track_kinds() if k in shown)
    return Verdict(fid, None, reason="同じ番号の回答が複数ある", tracks=kinds)


def _warnings(ids: Sequence[str], verdicts: Mapping[str, Verdict], repeated: Sequence[str], unknown: int) -> tuple[str, ...]:
    """読んだ回答の注意。公開の記録に載るので、判定役が書いた文（理由・渡していない番号）は写さず、渡した指摘の番号と
    件数だけを書く。`repeated` は複数の回答があった番号、`unknown` は渡していない番号への回答の件数。"""
    invalid = [fid for fid in ids if fid not in verdicts or verdicts[fid].invalid]
    listed = (
        ("判定役の有効な回答が無いので下げない", invalid),
        ("判定役が同じ番号に複数の回答を返したので下げない", repeated),
        ("判定役が判断できないので下げない", [v.id for v in verdicts.values() if v.holds is None and v.id not in (*repeated, *invalid)]),
    )
    warnings = [f"{head}: {'・'.join(found)}" for head, found in listed if found]
    if unknown:
        warnings.append(f"判定役が渡していない番号に {unknown} 件の回答を返した")
    return tuple(warnings)


def read(found: Optional[Sequence[Mapping[str, Any]]], ids: Sequence[str]) -> Answers:
    """回答の並び（出力から取り出せなければ None）を、渡した指摘 `ids` ごとの回答にする。読めない分は注意にする。"""
    if found is None:
        return Answers({}, (f"判定役の回答を読めないので下げない: {'・'.join(ids)}",))
    given: dict[str, list[Mapping[str, Any]]] = {}
    unknown = 0
    for item in found:
        fid = str(item.get("id"))
        if fid not in ids:
            unknown += 1
        else:
            given.setdefault(fid, []).append(item)
    verdicts: dict[str, Verdict] = {}
    for fid, items in given.items():
        verdict = _repeated(fid, items) if len(items) > 1 else _verdict(fid, items[0]) or _invalid(fid, items[0])
        if verdict is not None:
            verdicts[fid] = verdict
    repeated = [fid for fid, items in given.items() if len(items) > 1]
    return Answers(verdicts, _warnings(ids, verdicts, repeated, unknown))


def unlowering(answers: Answers, why: str = "判定役がファイルの読み取りを拒否された") -> Answers:
    """否定の回答を採れない起動（理由 `why`。資料のほかのファイルの読み取りの拒否か、確かめられない出力）の回答。どの指摘に
    響くかを決められないので、成り立たないとした回答はどれも判断できないにする（指摘の種類は残す）。起きると示せた回答は
    変えない。"""
    refused = [v.id for v in answers.verdicts.values() if v.holds is False]
    if not refused:
        return answers
    verdicts = {fid: v._replace(holds=None) if fid in refused else v for fid, v in answers.verdicts.items()}
    warning = f"{why}ので、成り立たないとした回答を採らず下げない: {'・'.join(refused)}"
    return answers._replace(verdicts=verdicts, warnings=(*answers.warnings, warning))


def lowers(row: Mapping[str, Any], verdict: Verdict) -> bool:
    """指摘 `row` を、回答 `verdict` を理由に軽い扱いへ下げてよいか。成り立たないとした指摘のうち、下げない指摘
    （`_never_asked`・`_never_lowered`）と、回答の種類が保護の種類か表に無い値を持つものを除く。"""
    if verdict.holds is not False or _never_asked(row) or _never_lowered(row):
        return False
    return not verdict.unknown and not set(verdict.tracks) & set(criteria_decide.protect_kinds())
