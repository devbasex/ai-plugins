"""cross-review の記録を PR へ残すコメントの本文（#3939）。

状態ファイル（`store`）のラウンド・修正の記録・指摘のジャーナルだけから組む。`run` の終わりと、状態ファイルから書き足す
`ndf cross-review report` が同じ本文を作る。状態ファイルはセッションの作業場所にあって消えるため、PR に残すのはこの本文である。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Optional

from ndf import identity
from ndf.domains.crossreview import criteria_decide, scope_decide, store
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.records import mvv_decide

if TYPE_CHECKING:
    from ndf.domains.records.mvv import Snapshot

# コメントの先頭のマーカー（ほかのコメントと見分ける）
MARKER = f"<!-- {identity.FIXED_WORDS['entrypoint']}:cross-review-report -->"
# 記録のコメントに埋める未解決の指摘の一覧（次の実行が機械で読んで引き継ぐ。#3941）の前後
OPEN_MARK = f"<!-- {identity.FIXED_WORDS['entrypoint']}:cross-review-open "
# 本文の最後の行だけに当てる（表の自由記述に同じ形があっても読まない。PR 3944 の r2-4）
_OPEN = re.compile(re.escape(OPEN_MARK) + r"(?P<json>[^\n]*) -->")
# 指摘がプロジェクト MVV に反するときの欄（#4076。ジャーナルの指摘と、記録のコメントに埋める一覧の両方が持つ）。下の識別子は、
# どの MVV かを表す文字列（`records.mvv.Snapshot.identity`）
# 反する項目（`Value 5`・`P1` など）
MVV = mvv_decide.REVIEW_ITEM
# 重さを、修正担当へ渡す重さに固定したか（`Value`・`Vision`・`Mission` に反する指摘）と、固定する前の重さ
PINNED = "mvv_pinned"
UNPINNED = "unpinned_severity"
# 相手役が挙げた、必ず人の承認が要る操作の番号（`P1`・`C7`）
REDLINE_ITEMS = mvv_decide.REVIEW_REDLINE_ITEMS
# 上の 4 つを決めたときの MVV の識別子
JUDGED = mvv_decide.REVIEW_JUDGED
# 上の 5 つの欄
MARKS = (MVV, PINNED, REDLINE_ITEMS, JUDGED, UNPINNED)
# NDF が機械で確かめて作った指摘の `by`
BY_NDF = mvv_decide.BY_NDF
# 状態ファイルのキー（書いた記録のコメントの URL。次からは同じコメントを書き換える）
COMMENT_KEY = "report_comment"
# 状態ファイルのキー（記録のコメントへ公開できた状態の要約 `published_state`。同じなら書き直さない。PR 3944 の r3-2）
PUBLISHED_KEY = "report_published"
# 状態ファイルのキー（指摘の番号 → スレッドの書き込み状態。`threads.sync` が書く）
THREADS = "threads"
# 状態ファイルのキー（記録のコメント・スレッド・公開の要約が属する PR の番号。巻き直しの後の PR へ旧 PR の記録を書かない。
# PR 3944 の r4-5）
REPORT_PR = "report_pr"
# 状態ファイルのキー（巻き直しの前の PR の記録。PR の番号 → 記録のコメント・スレッド・公開の要約）
EARLIER_KEY = "report_earlier"
# `EARLIER_KEY` の中で、どの PR のものか確かめられない旧形式の記録（`REPORT_PR` の無いまま巻き直した状態ファイル）の名前
UNKNOWN_OWNER = "unknown-pr"
# 状態ファイルのキー（今の PR へ移る前に直した・見送った指摘の番号。新しい PR にスレッドを作らない。PR 3944 の r5-3）
SETTLED_KEY = "report_settled"
# PR ごとに分ける記録のキー
_PER_PR = (COMMENT_KEY, PUBLISHED_KEY, THREADS, SETTLED_KEY)
# コメントの URL の末尾の id
_COMMENT_ID = re.compile(r"#issuecomment-(\d+)$")
# 終わり方の表示
FINAL_LABEL = {
    fin.CONVERGED: "収束した",
    fin.MAX_ROUNDS: "ラウンドの上限で止まった",
    fin.OSCILLATING: "振動で止まった",
    fin.ERROR: "誤りで止まった",
    fin.INCOMPLETE_REVIEWERS: "相手役が差分を読めずに止まった",
}
# 指摘の終わり方の表示
OUTCOME_LABEL = {fin.FIXED: "直した", fin.SKIPPED: "見送った", fin.UNRESOLVED: "未解決"}
# 収束の妨げにならない重さで残した指摘の表示（修正担当へ渡さない。#3941）
LIGHT_LABEL = "軽い（直さずに残した）"
# 必ず人の承認が要る操作に当たる指摘の表示（修正担当へ渡さない。#4076）
REDLINE_LABEL = "人の承認を待つ（直さずに残した）"


# 一覧の文字列の中で JSON の文字の escape にする文字。公開の検査（`publish.scrub`）の形（URL の資格情報・認証ヘッダー・
# トークンの接頭辞・メール・ホーム・リポジトリへの参照）の手掛かりになる文字を消し、検査が 1 行の一覧の構造（閉じ括弧・
# `-->`）まで巻き込んで伏せないようにする（PR 3944 の r6-4）。秘密は呼び手の `clean` が値ごとに先に伏せる
_OPEN_ESCAPES = {":": "\\u003a", "=": "\\u003d", "@": "\\u0040", "/": "\\u002f"}
# 一覧の自由記述（`clean` を通す値）
_OPEN_TEXTS = ("id", "text", "by", "severity", "path", "section")
Cleaner = Callable[[Sequence[str]], Sequence[str]]


def _same(texts: Sequence[str]) -> Sequence[str]:
    return texts


def mvv_outputs(rows: Sequence[Mapping[str, Any]], snapshot: Snapshot, head: str, clean: Cleaner = _same) -> dict[str, Any]:
    """結果 JSON の `outputs.mvv`: MVV `snapshot` で判定した指摘の一覧。`head` はレビューしたコミット。

    見送りの理由・必ず人の承認が要る操作の指摘の本文・根拠の記述の誤りのパスと引いた項目は、`clean` に 1 回で通して出す。
    同じ 1 回に、記録だけに書く通常の指摘の見送りの理由も通す（`clean` が `publish.Once` なら、記録の側は照会し直さない）。
    """
    # 記録（`mvv.record_review`）だけに書く通常の指摘の自由記述も同じ 1 回で伏せ、公開範囲の照会を 1 回にまとめる
    everything = mvv_decide.judged_findings(rows, snapshot.identity, holds=True)
    found = [f for f in everything if not f.plain]
    cited = [f.row for f in found if f.item is None]
    texts = [t for f in everything for t in f.free_texts]
    texts += [t for row in cited for t in (row.get("path"), *store.list_of(row, "unknown")) if isinstance(t, str)]
    shown: dict[Any, str] = dict(zip(texts, clean(texts), strict=True)) if texts else {}
    out: dict[str, Any] = {"version": snapshot.version, "sha256": snapshot.sha256, "head": head}
    out |= {"findings": [], "citations": [], "redlines": [], "disputed": []}
    for f in found:
        ident = {"id": f.row["id"], "item": f.item}
        if f.item is None:
            place = {"path": shown.get(f.row.get("path")), "line": f.row.get("line"), "cited": shown.get(f.row.get("cited"))}
            unknown = [shown[t] for t in store.list_of(f.row, "unknown")]
            out["citations"].append({"id": f.row["id"], **place, "unknown": unknown, "outcome": f.outcome})
        elif f.outcome == fin.REDLINE:
            out["redlines"].append({**ident, "text": shown[f.row["text"]]})
        elif f.outcome == fin.SKIPPED:
            out["disputed"].append({**ident, "reason": shown.get(f.row.get("reason"))})
        else:
            out["findings"].append({**ident, "by": f.row.get("by"), "outcome": f.outcome})
    return out


def comment_id(url: Optional[str]) -> Optional[int]:
    """記録のコメントの URL から id（読めなければ None）。"""
    found = _COMMENT_ID.search(url) if url else None
    return int(found.group(1)) if found else None


def bind_pr(data: dict[str, Any], pr: int, origin: int) -> bool:
    """記録のコメント・スレッド・公開の要約を PR `pr` のものにする。別の PR（巻き直しの前）の記録なら `EARLIER_KEY` へ
    退避し、`pr` の記録を空から始める（旧 PR のコメントを書き換えず、旧スレッドの id を使わない。PR 3944 の r4-5）。
    `origin` は状態ファイルの元の番号。オーナー（`REPORT_PR`）の無い旧形式の記録は、巻き直していない（`pr` が `origin`）ときだけ
    `pr` のものとし、巻き直した後ならオーナーの分からない記録として退避して id を使わない（PR 3944 の r5-2）。別の PR へ移したときは、
    それまでに直した・見送った指摘を `SETTLED_KEY` に残し、新しい PR にスレッドを作らせない（PR 3944 の r5-3）。
    状態を変えたら True（呼び手が状態ファイルへ書く）。"""
    owner = data.get(REPORT_PR)
    data[REPORT_PR] = pr
    if owner == pr:
        return False
    held = [k for k in _PER_PR if k in data]
    if owner is None and pr == origin:
        return bool(held)
    if held:
        data.setdefault(EARLIER_KEY, {})[UNKNOWN_OWNER if owner is None else str(owner)] = {k: data.pop(k) for k in held}
    settled = [
        f.get("id") for f in store.list_of(data, store.FINDINGS) if isinstance(f, Mapping) and f.get("outcome") in (fin.FIXED, fin.SKIPPED)
    ]
    data[SETTLED_KEY] = [i for i in settled if isinstance(i, str)]
    return True


def published_state(pr: int, data: Mapping[str, Any], final: Optional[str], notices: Sequence[str] = ()) -> str:
    """記録のコメントに載る状態（PR の番号・終わり方・ラウンド・指摘・スレッドと、MVV の記録の失敗の注意 `notices`）の要約。公開できたときに `PUBLISHED_KEY` へ
    書き、前回の収束を返す打ち直しは、これが今の状態と同じときだけ公開を省く（古いラウンドの記録を残さない。PR 3944 の r3-2）。"""
    shown = {
        "pr": pr,
        "final": final,
        "rounds": store.list_of(data, store.ROUNDS),
        "findings": store.list_of(data, store.FINDINGS),
        "threads": data.get(THREADS),
    }
    # 注意が無ければキーを足さず、注意を比べないバージョンの要約と同じ値にする
    if notices:
        shown["notices"] = list(notices)
    return hashlib.sha256(json.dumps(shown, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _cell(value: Any) -> str:
    """表のセル。改行と縦棒で表が崩れないようにする。無ければ「—」。"""
    if value is None or value == "":
        return "—"
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _short(sha: Any) -> str:
    return f"`{sha[:9]}`" if isinstance(sha, str) and sha else "—"


def final_of(data: Mapping[str, Any]) -> Optional[str]:
    """状態ファイルの最後のラウンドの判定から読む終わり方（修正の途中で終わった起動は None）。"""
    verdict = store.last_round(data).get("verdict")
    return verdict if verdict in fin.FINALS else None


def _seats_cell(r: Mapping[str, Any]) -> str:
    """ラウンドの「相手役」のセル。席を ・ で並べ、欠けた相手役の席を埋めた席は `（<相手役> の席）` を添える（#3955）。"""
    absent = {f.seat: f.absent for f in scope_decide.filled_of(r) if f.absent is not None}
    seats = [f"{s}（{absent[s]} の席）" if s in absent else str(s) for s in store.list_of(r, scope_decide.REVIEWERS)]
    return _cell("・".join(seats))


def _round_rows(rounds: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = [
        "| ラウンド | 相手役 | レビューした head | 範囲 | 判定 | 修正のコミット | 送った | 直した指摘 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rounds:
        fix = r.get("fix") if isinstance(r.get("fix"), Mapping) else {}
        fixed = fix.get("fixed") if isinstance(fix.get("fixed"), list) else []
        pushed = "—" if not fix else ("送った" if fix.get("pushed") else "送れていない")
        rows.append(
            f"| {_cell(r.get('round'))} | {_seats_cell(r)} | {_short(r.get('head'))} | {_cell(r.get('scope'))} | {_cell(r.get('verdict'))} "
            f"| {_short(fix.get('commit'))} | {pushed} | {_cell('・'.join(map(str, fixed)))} |"
        )
    return rows


def _place(f: Mapping[str, Any]) -> Any:
    """表の「場所」（相手役が返した `path`・`line`、無ければ `section`）。"""
    path, line = f.get("path"), f.get("line")
    if isinstance(path, str) and path:
        return f"{path}:{line}" if type(line) is int else path
    return f.get("section")


def _light(f: Mapping[str, Any]) -> bool:
    """収束の妨げにならない重さの指摘か。"""
    return not criteria_decide.blocking(criteria_decide.severity_of(f.get("severity")))


def _finding_rows(findings: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = ["| 番号 | 担当 | 重要度 | 場所 | 指摘 | 終わり方 | 見送った理由 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for f in findings:
        outcome = f.get("outcome")
        shown = OUTCOME_LABEL[outcome] if outcome in OUTCOME_LABEL else outcome
        if outcome == fin.UNRESOLVED and (f.get(fin.REDLINE) or _light(f)):
            shown = REDLINE_LABEL if f.get(fin.REDLINE) else LIGHT_LABEL
        rows.append(
            f"| {_cell(f.get('id'))} | {_cell(f.get('by'))} | {_cell(criteria_decide.severity_of(f.get('severity')))} | {_cell(_place(f))} "
            f"| {_cell(f.get('text'))} | {_cell(shown)} | {_cell(f.get('reason'))} |"
        )
    return rows


def comment(pr: int, data: Mapping[str, Any], final: Optional[str], warnings: Sequence[str] = (), clean: Cleaner = _same) -> str:
    """PR へ残すコメントの本文。`final` は終わり方（`finish_decide.FINALS`。分からなければ None）。`clean` は末尾の一覧の
    自由記述を JSON にする前に通す伏せ字（`open_block`）。"""
    rounds = [r for r in store.list_of(data, store.ROUNDS) if isinstance(r, Mapping)]
    findings = [f for f in store.list_of(data, store.FINDINGS) if isinstance(f, Mapping)]
    waiting = sum(1 for f in findings if f.get("outcome") == fin.UNRESOLVED and f.get(fin.REDLINE))
    rest = [f for f in findings if f.get("outcome") != fin.UNRESOLVED or not f.get(fin.REDLINE)]
    counts = {key: sum(1 for f in rest if f.get("outcome") == key and (key != fin.UNRESOLVED or not _light(f))) for key in OUTCOME_LABEL}
    light = sum(1 for f in rest if f.get("outcome") == fin.UNRESOLVED and _light(f))
    label = FINAL_LABEL[final] if final in FINAL_LABEL else "途中（終わり方の記録が無い）"
    lines = [
        MARKER,
        f"## cross-review の記録（PR #{pr}）",
        "",
        f"終わり方: {label}。ラウンド {len(rounds)}・指摘 {len(findings)}"
        f"（直した {counts[fin.FIXED]}・見送った {counts[fin.SKIPPED]}・未解決 {counts[fin.UNRESOLVED]}"
        + (f"・軽い {light}" if light else "")
        + (f"・人の承認を待つ {waiting}" if waiting else "")
        + "）",
        "",
        "### ラウンドと修正",
        "",
        *(_round_rows(rounds) if rounds else ["ラウンドの記録は無い。"]),
        "",
        "### 指摘",
        "",
        *(_finding_rows(findings) if findings else ["指摘は無い。"]),
    ]
    if warnings:
        lines += ["", "### 注意", "", *(f"- {_cell(w)}" for w in warnings)]
    lines += ["", open_block(data, clean)]
    return "\n".join(lines) + "\n"


def _open_value(value: Any) -> str:
    """一覧の 1 つの値の JSON。文字列は `_OPEN_ESCAPES` と `--`（コメントを閉じない）を escape にする。"""
    encoded = json.dumps(value, ensure_ascii=False)
    if not isinstance(value, str):
        return encoded
    for ch, escaped in _OPEN_ESCAPES.items():
        encoded = encoded.replace(ch, escaped)
    return encoded.replace("--", "-\\u002d")


def open_block(data: Mapping[str, Any], clean: Cleaner = _same) -> str:
    """未解決の重い指摘と、重さによらず載せる指摘（必ず人の承認が要る操作に当たるもの・NDF が機械で確かめて作ったもの）の一覧を
    HTML のコメントに埋めた行（次の実行が `open_items` で読む）。`thread` はスレッドの最初のコメントの id（スレッドが無ければ null）。自由記述の値は JSON にする前に `clean`（書き先に応じた伏せ字。値の並びを
    1 回で受けて同じ順に返す）へ値ごとに通し、JSON にした後の本文全体の伏せ字が一覧の構造を壊さない形に escape する
    （PR 3944 の r6-4）。"""
    done = data.get(THREADS) if isinstance(data.get(THREADS), Mapping) else {}
    items = []
    for f in store.list_of(data, store.FINDINGS):
        if not isinstance(f, Mapping) or f.get("outcome") != fin.UNRESOLVED:
            continue
        if _light(f) and not f.get(fin.REDLINE) and f.get("by") != BY_NDF:
            continue
        mark = done.get(f.get("id"))
        thread = mark.get("comment") if isinstance(mark, Mapping) else None
        keep = {k: f.get(k) for k in ("id", "text", "by", "severity", "path", "line", "section", *MARKS)}
        items.append({**keep, "thread": thread if type(thread) is int else None})
    slots = [(item, k) for item in items for k in _OPEN_TEXTS if isinstance(item[k], str)]
    for (item, k), cleaned in zip(slots, clean([item[k] for item, k in slots]) if slots else ()):
        item[k] = cleaned
    payload = "[" + ", ".join("{" + ", ".join(f"{_open_value(k)}: {_open_value(v)}" for k, v in item.items()) + "}" for item in items) + "]"
    return f"{OPEN_MARK}{payload} -->"


def open_items(body: str) -> list[dict[str, Any]]:
    """記録のコメントに埋めた未解決の指摘の一覧（無い・読めなければ空）。`comment` が末尾に書いた行だけを読み、ほかの
    行（指摘の本文や見送った理由を埋めた表・注意）に同じ形があっても解釈しない（PR 3944 の r2-4）。"""
    # 書き出しの区切り（LF）だけで末尾の行を切る。`splitlines` は JSON にそのまま残る U+2028 などでも分け、末尾の行を
    # 読み損なう（PR 3944 の r3-3）。GitHub が CRLF へ変えた本文は行末の CR を外す
    last = body.rstrip().rsplit("\n", 1)[-1].rstrip("\r")
    found = _OPEN.fullmatch(last) if last else None
    if found is None:
        return []
    try:
        raw = json.loads(found.group("json"))
    except ValueError:
        return []
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
