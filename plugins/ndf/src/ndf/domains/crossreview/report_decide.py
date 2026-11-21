"""cross-review の記録を PR へ残すコメントの本文（#3939）。

状態ファイル（`store`）のラウンド・修正の記録・指摘のジャーナルだけから組む。`run` の終わりと、状態ファイルから書き足す
`ndf cross-review report` が同じ本文を作る。状態ファイルはセッションの作業場所にあって消えるため、PR に残すのはこの本文である。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from ndf import identity
from ndf.domains.crossreview import criteria_decide, store
from ndf.domains.crossreview import finish_decide as fin

# コメントの先頭のマーカー（ほかのコメントと見分ける）
MARKER = f"<!-- {identity.FIXED_WORDS['entrypoint']}:cross-review-report -->"
# 記録のコメントに埋める未解決の指摘の一覧（次の実行が機械で読んで引き継ぐ。#3941）の前後
OPEN_MARK = f"<!-- {identity.FIXED_WORDS['entrypoint']}:cross-review-open "
# 本文の最後の行だけに当てる（表の自由記述に同じ形があっても読まない。PR 3944 の r2-4）
_OPEN = re.compile(re.escape(OPEN_MARK) + r"(?P<json>[^\n]*) -->")
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
# PR ごとに分ける記録のキー
_PER_PR = (COMMENT_KEY, PUBLISHED_KEY, THREADS)
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


def comment_id(url: Optional[str]) -> Optional[int]:
    """記録のコメントの URL から id（読めなければ None）。"""
    found = _COMMENT_ID.search(url) if url else None
    return int(found.group(1)) if found else None


def bind_pr(data: dict[str, Any], pr: int) -> bool:
    """記録のコメント・スレッド・公開の要約を PR `pr` のものにする。別の PR（巻き直しの前）の記録なら `EARLIER_KEY` へ
    退避し、`pr` の記録を空から始める（旧 PR のコメントを書き換えず、旧スレッドの id を使わない。PR 3944 の r4-5）。
    旧 PR の記録を退避したら True（呼び手が状態ファイルへ書く）。"""
    owner = data.get(REPORT_PR)
    data[REPORT_PR] = pr
    if owner is None or owner == pr:
        return False
    moved = {k: data.pop(k) for k in _PER_PR if k in data}
    if moved:
        data.setdefault(EARLIER_KEY, {})[str(owner)] = moved
    return bool(moved)


def published_state(pr: int, data: Mapping[str, Any], final: Optional[str]) -> str:
    """記録のコメントに載る状態（PR の番号・終わり方・ラウンド・指摘・スレッド）の要約。公開できたときに `PUBLISHED_KEY` へ
    書き、前回の収束を返す打ち直しは、これが今の状態と同じときだけ公開を省く（古いラウンドの記録を残さない。PR 3944 の r3-2）。"""
    shown = {
        "pr": pr,
        "final": final,
        "rounds": store.list_of(data, store.ROUNDS),
        "findings": store.list_of(data, store.FINDINGS),
        "threads": data.get(THREADS),
    }
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


def _round_rows(rounds: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = [
        "| ラウンド | レビューした head | 範囲 | 判定 | 修正のコミット | 送った | 直した指摘 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rounds:
        fix = r.get("fix") if isinstance(r.get("fix"), Mapping) else {}
        fixed = fix.get("fixed") if isinstance(fix.get("fixed"), list) else []
        pushed = "—" if not fix else ("送った" if fix.get("pushed") else "送れていない")
        rows.append(
            f"| {_cell(r.get('round'))} | {_short(r.get('head'))} | {_cell(r.get('scope'))} | {_cell(r.get('verdict'))} "
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
        if outcome == fin.UNRESOLVED and _light(f):
            shown = LIGHT_LABEL
        rows.append(
            f"| {_cell(f.get('id'))} | {_cell(f.get('by'))} | {_cell(criteria_decide.severity_of(f.get('severity')))} | {_cell(_place(f))} "
            f"| {_cell(f.get('text'))} | {_cell(shown)} | {_cell(f.get('reason'))} |"
        )
    return rows


def comment(pr: int, data: Mapping[str, Any], final: Optional[str], warnings: Sequence[str] = ()) -> str:
    """PR へ残すコメントの本文。`final` は終わり方（`finish_decide.FINALS`。分からなければ None）。"""
    rounds = [r for r in store.list_of(data, store.ROUNDS) if isinstance(r, Mapping)]
    findings = [f for f in store.list_of(data, store.FINDINGS) if isinstance(f, Mapping)]
    counts = {
        key: sum(1 for f in findings if f.get("outcome") == key and (key != fin.UNRESOLVED or not _light(f))) for key in OUTCOME_LABEL
    }
    light = sum(1 for f in findings if f.get("outcome") == fin.UNRESOLVED and _light(f))
    label = FINAL_LABEL[final] if final in FINAL_LABEL else "途中（終わり方の記録が無い）"
    lines = [
        MARKER,
        f"## cross-review の記録（PR #{pr}）",
        "",
        f"終わり方: {label}。ラウンド {len(rounds)}・指摘 {len(findings)}"
        f"（直した {counts[fin.FIXED]}・見送った {counts[fin.SKIPPED]}・未解決 {counts[fin.UNRESOLVED]}"
        + (f"・軽い {light}" if light else "")
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
    lines += ["", open_block(data)]
    return "\n".join(lines) + "\n"


def open_block(data: Mapping[str, Any]) -> str:
    """未解決の重い指摘の一覧を HTML のコメントに埋めた行（次の実行が `open_items` で読む）。`thread` はスレッドの最初の
    コメントの id（スレッドが無ければ null）。本文の `--` はコメントを閉じないよう JSON の文字の escape にする。"""
    done = data.get(THREADS) if isinstance(data.get(THREADS), Mapping) else {}
    items = []
    for f in store.list_of(data, store.FINDINGS):
        if not isinstance(f, Mapping) or f.get("outcome") != fin.UNRESOLVED or _light(f):
            continue
        mark = done.get(f.get("id"))
        thread = mark.get("comment") if isinstance(mark, Mapping) else None
        keep = {k: f.get(k) for k in ("id", "text", "by", "severity", "path", "line", "section")}
        items.append({**keep, "thread": thread if type(thread) is int else None})
    payload = json.dumps(items, ensure_ascii=False).replace("--", "-\\u002d")
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
