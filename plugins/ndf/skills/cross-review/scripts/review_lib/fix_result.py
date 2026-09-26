"""修正の結果ファイルを見つけ、今のラウンドのものかを確かめる（#1142 の C2）。"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
from typing import Any

import review_lib  # noqa: E402
from review_lib import store  # noqa: E402


def _round_started_unixtime(round_entry: dict[str, Any]) -> float | None:
    """``round.started_at`` (ISO 8601) を UNIX time (秒) に変換する。

    fix 戻り値ファイルの mtime と比較して、round 開始前に書かれた古い
    ファイルを fallback から除外するために使う。
    パース失敗時は None を返し、呼び出し側で「検証スキップ」を選ばせる。
    """
    started = round_entry.get("started_at")
    if not started:
        return None
    try:
        return _dt.datetime.fromisoformat(started).timestamp()
    except (TypeError, ValueError):
        return None


def _is_fresh_fix_result(
    path: pathlib.Path,
    pr: int,
    round_started_ts: float | None,
    is_canonical: bool = False,
) -> tuple[bool, dict[str, Any] | None]:
    """fallback 候補の fix 戻り値ファイルを採用してよいか判定する。

    検証項目:
      1. ファイル mtime が `round_started_ts` 以降であること
         (round 開始前に作られた = 古い実行 / 別リポジトリの同番号 PR の残骸)
      2. JSON 内に `pr` フィールドがある場合は対象 PR と一致すること
         (`pr` フィールドが無い場合は 1 のみで判定)

    Returns:
      ``(is_fresh, parsed_payload)`` のタプル。``is_fresh=True`` の場合のみ
      ``parsed_payload`` (dict) が返る。呼び出し側はこれを使って再パースを省略できる。

    挙動:
      - 古い候補・`pr` 不一致は警告を stderr に出して ``(False, None)`` を返し、
        呼び出し側で次の候補へ進む。
      - `is_canonical=True` (= ``$TMP_DIR/fix-pr<PR>-result.json`` 正規パス) で
        **読み取り失敗 (OSError / JSONDecodeError)** が発生した場合のみ
        即時 ``die(code=3)`` する (codex round 2 指摘: 正規パスが壊れているのに
        後続候補へ流れて別 PR の戻り値を誤マージする事故を防ぐ)。
        正規パスでも `pr` 不一致 / stale mtime は fallback 継続対象とする。
    """
    if not _is_fresh_mtime(path, round_started_ts):
        return False, None

    payload = _read_and_parse_fix_payload(path, is_canonical=is_canonical)
    if payload is None:
        return False, None
    if not _matches_pr(path, payload, pr):
        return False, None
    return True, payload


def _is_fresh_mtime(path: pathlib.Path, round_started_ts: float | None) -> bool:
    """fallback 候補の mtime が round 開始以降かを返す。"""
    if round_started_ts is not None:
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            review_lib.info(f"⚠ fallback 候補 stat 失敗 ({path}): {exc} — skip")
            return False
        if mtime < round_started_ts:
            review_lib.info(
                f"⚠ fallback 候補が round 開始前の古いファイル ({path}, "
                f"mtime={_dt.datetime.fromtimestamp(mtime).isoformat(timespec='seconds')} "
                f"< round_started={_dt.datetime.fromtimestamp(round_started_ts).isoformat(timespec='seconds')}) "
                "— skip"
            )
            return False
    return True


def _read_and_parse_fix_payload(
    path: pathlib.Path,
    is_canonical: bool = False,
) -> dict[str, Any] | None:
    """fix 戻り値ファイルを dict として読み取る。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if is_canonical:
            review_lib.die(
                f"正規パスの fix 戻り値ファイルの読み取りに失敗 ({path}): {exc}。"
                " 後続 fallback への流れ込みを防ぐため即時中断。",
                code=3,
            )
        review_lib.info(f"⚠ fallback 候補 JSON 解析失敗 ({path}): {exc} — skip")
        return None
    # gemini round 3 指摘: `json.loads` は dict 以外 (list 等) も返す。
    # 後続の `payload.get(...)` や cmd_merge_fix 側の `.get()` でクラッシュしないよう、
    # dict でない場合は warn を出して fallback 不採用 ((False, None)) として扱う。
    #
    # codex round 4 指摘: ただし `is_canonical=True` (= 正規パス) で非 dict が返った
    # 場合は parse 失敗と同じく即時 die(code=3) する。skip で後続 `/tmp/` fallback に
    # 流れると、壊れた正規出力を無視して別実行の戻り値を誤マージする経路が残るため。
    if not isinstance(payload, dict):
        if is_canonical:
            review_lib.die(
                f"正規パスの fix 戻り値ファイルが dict ではない "
                f"({path}, type={type(payload).__name__})。"
                " 後続 fallback への流れ込みを防ぐため即時中断。",
                code=3,
            )
        review_lib.info(
            f"⚠ fallback 候補 JSON が dict ではない ({path}, type={type(payload).__name__}) "
            "— skip"
        )
        return None
    return payload


def _matches_pr(path: pathlib.Path, payload: dict[str, Any], pr: int) -> bool:
    """payload の pr フィールドが対象 PR と一致するかを返す。"""
    file_pr = payload.get("pr")
    if file_pr is not None:
        try:
            file_pr_int = int(file_pr)
        except (TypeError, ValueError):
            review_lib.info(
                f"⚠ fallback 候補の pr フィールドが数値として解釈できない "
                f"({path}, file_pr={file_pr!r}) — skip"
            )
            return False
        if file_pr_int != int(pr):
            review_lib.info(
                f"⚠ fallback 候補の pr 不一致 ({path}, file_pr={file_pr} != pr={pr}) "
                "— 別 PR の戻り値の可能性。skip"
            )
            return False
    return True


# 読んだ修正の結果ファイルの場所。投稿の組み立ては本文を引数に取らず、ファイルの
# パスを受け取る（#730 の決定 3）。記録へは写らない（`_normalize_fix_result` は
# 決まった鍵だけを読む）。
FIX_SOURCE_KEY = "_source_path"


def _read_fix_result(
    pr: int | str,
    explicit_file: str | pathlib.Path | None,
    round_started_ts: float | None,
) -> dict[str, Any]:
    """fix サブエージェントの戻り値ファイルを探索・検証して辞書として読み込む。

    探索順:
      1. explicit_file 明示 (ユーザー指定なので mtime/pr 検証はスキップ)
      2. $TMP_DIR/fix-pr<PR>-result.json (正規; _tmp_dir() 解決先)
      3. /tmp/fix-pr<PR>-result.json (旧プロンプトで /tmp を指定したサブエージェント救済)
    2, 3 は PR 番号だけで命名されているため、別 round / 別リポジトリの
    古い結果を拾わないよう mtime と (あれば) JSON 内の `pr` で検証する。
    """
    explicit = pathlib.Path(explicit_file) if explicit_file else None
    canonical_path = store._resolve_tmp_dir(pr) / f"fix-pr{pr}-result.json"
    legacy_tmp_path = pathlib.Path(f"/tmp/fix-pr{pr}-result.json")
    # (path, is_canonical) のタプル: 正規パス (canonical) の parse 失敗は die(code=3) する
    fallback_candidates: list[tuple[pathlib.Path, bool]] = [
        (canonical_path, True),
        (legacy_tmp_path, False),
    ]

    if explicit is not None:
        fix = _read_explicit_fix_result(explicit)
        fix.setdefault(FIX_SOURCE_KEY, str(explicit))
        return fix

    fix = _find_fallback_fix_result(fallback_candidates, pr, round_started_ts)

    if fix is None:
        checked = ([str(explicit)] if explicit else []) + [str(c) for c, _ in fallback_candidates]
        review_lib.die(
            "fix サブエージェントが戻り値ファイルを生成しなかった "
            f"(checked: {checked})",
            code=3,
        )

    return fix


def _read_explicit_fix_result(explicit: pathlib.Path) -> dict[str, Any]:
    """明示された fix 戻り値を読み、形式不正なら fallback せず終了する。"""
    if not explicit.exists():
        review_lib.die(f"--file で指定されたパスが存在しません: {explicit}", code=3)
    if explicit.stat().st_size == 0:
        review_lib.die(f"--file で指定されたファイルが空です: {explicit}", code=3)
    try:
        fix = json.loads(explicit.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        review_lib.die(
            f"--file 指定の fix 戻り値ファイルの読み取り / parse に失敗 "
            f"({explicit}): {exc}",
            code=3,
        )
    if not isinstance(fix, dict):
        review_lib.die(
            f"--file 指定の fix 戻り値ファイルが dict ではない "
            f"({explicit}, type={type(fix).__name__})。"
            " fix サブエージェント出力の形式不正。",
            code=3,
        )
    return fix


def _find_fallback_fix_result(
    candidates: list[tuple[pathlib.Path, bool]],
    pr: int | str,
    round_started_ts: float | None,
) -> dict[str, Any] | None:
    """canonical、legacy の順に fresh な fix 戻り値を探す。"""
    for candidate, is_canonical in candidates:
        if not (candidate.exists() and candidate.stat().st_size > 0):
            continue
        is_fresh, parsed = _is_fresh_fix_result(
            candidate, pr, round_started_ts, is_canonical=is_canonical
        )
        if is_fresh:
            parsed.setdefault(FIX_SOURCE_KEY, str(candidate))
            return parsed
    return None
