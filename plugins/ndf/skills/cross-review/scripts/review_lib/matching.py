"""指摘どうし・指摘とコメントの照合（#1142 の C2）。新規性の数え方と振動の判定が同じ照合を使う。"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any, NamedTuple

import review_lib  # noqa: E402
from classifications import COUNTED_CLASSIFICATIONS  # noqa: E402
from review_lib import findings as findings_mod, participants as participants_mod, store  # noqa: E402


# 指摘の位置がずれても同じ箇所として数える幅。修正で前後にずれる幅として、同じ処理の
# 範囲の中の移動を拾い、隣の指摘まで巻き込まない値を採る。**この値の根拠となる実測は
# まだ無い。** 出力へ内訳を出すのは、実測を集めるためである。
OSCILLATION_NEAR_LINES = 3
# 本文を比べる長さ。先頭だけを見るのは、末尾の言い回しの揺れで別物にならないようにするため。
OSCILLATION_BODY_CHARS = 80
# 正規化で落とすもの。**文字と数字は言語を問わず残す。** 指摘の本文は日本語で書かれるため、
# ASCII の英数字だけを残すと本文が空になり、別の指摘どうしが一致してしまう。
_OSCILLATION_DROP = re.compile(r"[^\w]", re.UNICODE)


def _normalized_body(body: object) -> str:
    """指摘の本文を、行番号・引用符・記号の違いで別物にならない形へ揃える。"""
    if not isinstance(body, str):
        return ""
    return _OSCILLATION_DROP.sub("", body.lower())[:OSCILLATION_BODY_CHARS]


def _evidence_completed(st: dict[str, Any], round_no: int) -> bool:
    """そのラウンドが証拠集約（統合・実行検証・反証）を通ったか（#156）。

    **`review_findings` の有無では判定できない。** 取り込み（`cmd_read_result`）は
    この変更より前から `review_findings[]` を積むため、旧い状態ファイルにも要素が
    ある。存在で判定すると、区分も `verification` も持たない旧いラウンドまで区分で
    絞り込むことになり、**修正必須の `major` が `insufficient_evidence` へ落ちて
    新規 0 件（`(0, True)`）で収束する**。目印を持たないラウンドは従来どおり全件を
    数える。
    """
    marked = st.get("evidence_rounds") or []
    for value in marked:
        try:
            if int(value) == int(round_no):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _finding_keys(
    st: dict[str, Any], pr: int, round_no: int
) -> list[tuple[str, int, str]]:
    """そのラウンドの指摘を (ファイル, 行, 正規化した本文) の並びで返す。

    **新規性の判定と振動の検知が同じ形を使う。** どちらも「前のラウンドと同じ指摘か」を
    別の目的で見る。判定を 2 つ持つと、片方だけが更新されたときに、収束はするのに振動と
    して中断する状態が作れてしまう。
    """
    keys: list[tuple[str, int, str]] = []
    for agent in participants_mod._round_reviewers(st, round_no):
        p = store._payload_path(agent, pr, round_no)
        payload = _read_finding_payload(agent, p)
        if payload is None:
            continue
        keys.extend(_comment_keys(agent, p, payload))
    return keys


def _read_finding_payload(agent: str, p: pathlib.Path) -> dict[str, Any] | None:
    """判定の直前に読む payload.json を dict として返す（第 1 段: 入力境界）。

    無い・JSON として読めないときは None を返して読み飛ばす。dict でないときは
    launcher のバグとして `die(code=3)` で止める（`_load_payload` と違い、ここで
    止めても失われる記録が無い）。
    """
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    # gemini round 4 指摘: payload は本来 dict (comments: [...]) だが、
    # launcher のバグで list / str が入り込むと `payload.get(...)` で
    # AttributeError になる。不正な review payload はバグなので
    # 即時 die(code=3) で停止させる。
    if not isinstance(payload, dict):
        review_lib.die(
            f"{agent}: payload.json が dict ではない "
            f"({p}, type={type(payload).__name__})。"
            " review launcher の出力形式不正。",
            code=3,
        )
    return payload


def _comment_keys(
    agent: str, p: pathlib.Path, payload: dict[str, Any]
) -> list[tuple[str, int, str]]:
    """`comments[]` を (ファイル, 行, 正規化した本文) の 3 つ組へ変換する（第 2 段）。

    要素が dict でなければ `die(code=3)`。位置（path / line）が欠ける要素と、行が
    整数に読めない要素は読み飛ばす。
    """
    keys: list[tuple[str, int, str]] = []
    for c in payload.get("comments", []):
        if not isinstance(c, dict):
            # comments エントリが dict でない場合も同様に致命扱い
            review_lib.die(
                f"{agent}: payload.comments のエントリが dict ではない "
                f"({p}, type={type(c).__name__})。",
                code=3,
            )
        path = c.get("path")
        line = c.get("line") or c.get("start_line")
        if path and line is not None:
            try:
                keys.append((str(path), int(line), _normalized_body(c.get("body"))))
            except (TypeError, ValueError):
                continue
    return keys


def _counted_finding_keys(
    st: dict[str, Any], round_no: int
) -> list[tuple[str, int, str]]:
    """新規性が数える指摘を、`_finding_keys` と同じ 3 つ組で返す（#156）。

    **一致の判定は変えない。** 変えるのは母集合だけである。
    """
    keys: list[tuple[str, int, str]] = []
    for finding in st.get("review_findings") or []:
        if finding.get("round") != round_no or finding.get("merged_into"):
            continue
        if findings_mod._apply_classification(finding) not in COUNTED_CLASSIFICATIONS:
            continue
        try:
            line = int(finding.get("line"))
        except (TypeError, ValueError):
            continue
        path = str(finding.get("path") or "")
        if not path:
            continue
        keys.append((path, line, _normalized_body(finding.get("body"))))
    return keys


def _finding_match_kind(
    current_key: tuple[str, int, str],
    previous_keys: list[tuple[str, int, str]],
) -> str | None:
    """前ラウンドの指摘リストに対する現指摘の一致種別を返す。

    同一ファイルに限定した候補全体に対して、完全一致 → 近傍 → 本文の優先順で判定し、
    "exact" / "near" / "body" / None を返す。
    """
    path, line, body = current_key
    same_file = [q for q in previous_keys if q[0] == path]
    if any(line == q[1] for q in same_file):
        return "exact"
    if any(abs(line - q[1]) <= OSCILLATION_NEAR_LINES for q in same_file):
        return "near"
    if body and any(body == q[2] for q in same_file):
        return "body"
    return None


def _new_finding_count(st: dict[str, Any], pr: int) -> tuple[int, bool]:
    """最後のラウンドの新しい指摘の `(件数, 測れたかどうか)` を返す。

    **一致の判定は振動の検知と同じである**（位置・近傍・本文のいずれかで結び付く）。

    **測れないことと、新規が 0 件であることは別である。** 指摘の記録（payload）が
    残っていないラウンドでは中身を読めない。これを 0 件として扱うと、修正必須の指摘が
    出ているラウンドまで収束させてしまう。測れないときは呼び出し側が従来の判定
    （全員が pass か）に従う。

    | 状態 | 返る値 |
    | --- | --- |
    | ラウンドが無い | `(0, False)` |
    | 指摘の記録を読めない | `(0, False)` |
    | 前のラウンドが無い（初回・巻き直しの直後） | `(件数, True)`。すべて新規 |
    | 前のラウンドがある | `(一致しない件数, True)` |
    """
    rounds = st.get("rounds") or []
    same_pr = [r for r in rounds if r.get("pr") == st.get("current_pr")]
    if not same_pr:
        return 0, False
    round_no = same_pr[-1]["round"]
    curr = _finding_keys(st, pr, round_no)
    # **測れたかどうかは区分で絞る前に決める。** 全件が `rejected` になったラウンドを
    # 「測れなかった」と扱うと、元の REQUEST_CHANGES のまま終わらない。
    if not curr:
        return 0, False
    # **証拠集約を通ったラウンドだけを、数える 3 つへ絞る**（#156、#732）。通っていない
    # ラウンドは従来どおり全件を数える（旧い状態ファイルと、3 本目より前に開いた
    # ラウンドがこれに当たる）。**`review_findings` の有無では判定しない**
    # （旧版でも取り込みの時点で積まれるため、区分も検証結果も持たない旧いラウンドが
    # 絞り込みに掛かる。詳細は `_evidence_completed`）。
    if _evidence_completed(st, round_no):
        curr = _counted_finding_keys(st, round_no)
    if len(same_pr) < 2:
        return len(curr), True
    prev = _finding_keys(st, pr, same_pr[-2]["round"])
    new = 0
    for key in curr:
        if _finding_match_kind(key, prev) is None:
            new += 1
    return new, True


class _OscillationOverlap(NamedTuple):
    """現ラウンドの指摘が前ラウンドとどれだけ重なるかの集計。

    件数の合計（`overlap_count`）と、現ラウンドの件数で割った比（`ratio`）を持つ。
    副作用を持たず、状態ファイルにも表示にも触れない。
    """

    exact: int
    near: int
    same_body: int
    overlap_count: int
    total: int
    ratio: float


def _oscillation_overlap(
    curr: list[tuple[str, int, str]],
    prev: list[tuple[str, int, str]],
) -> _OscillationOverlap:
    """現ラウンドの指摘キーを前ラウンドと突き合わせ、一致の内訳と重複率を返す。

    一致の判定は `_finding_match_kind`（位置・近傍・本文の優先順）に任せる。この関数は
    種別ごとの件数を数えるだけで、`curr` が空でないことは呼び出し側が保証する。
    """
    exact = near = same_body = 0
    for key in curr:
        kind = _finding_match_kind(key, prev)
        if kind == "exact":
            exact += 1
        elif kind == "near":
            near += 1
        elif kind == "body":
            same_body += 1
    overlap_count = exact + near + same_body
    return _OscillationOverlap(
        exact=exact,
        near=near,
        same_body=same_body,
        overlap_count=overlap_count,
        total=len(curr),
        ratio=overlap_count / len(curr),
    )
