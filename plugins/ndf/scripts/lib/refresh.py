#!/usr/bin/env python3
"""観点の出典を調べ直す部品（#554）。

**提示するだけで、ファイルを書き換えない。** 外部の記述の読み違いがそのまま利用者の検査の
基準になることを避けるためである。機械が判定するのは 2 つだけで、**取得できたか**と
**前回取得した本文の指紋と一致するか**である。主張が今も成り立つかは人が読む。

**待ちは 1 件あたりの総経過時間である。** 接続と読み取りの無通信時間だけを見る形にすると、
指定の秒数より短い間隔でデータを返し続ける相手で止まらない。単調に進む時計で期限を持ち、
読み取りのたびに残りを計り直す。

#743（Skill の陳腐化）が同じ引き金と同じ出典の持ち方を必要とするため、取得・指紋の比較・
一覧の提示・待ちの扱いをここへ置く。観点のデータと判定の手続きは呼ぶ側が持つ。
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# 出典 1 件あたりの待ちの既定（秒）。宣言の `refresh_timeout_seconds` と
# 引数 `--refresh-timeout` が上書きする。
DEFAULT_TIMEOUT_SECONDS = 10

# 1 度に読み取る大きさ。残りの待ちを計り直す間隔でもある。
CHUNK_BYTES = 65536


class FetchTimeout(Exception):
    """総経過時間が待ちを越えた。"""


@dataclass
class FetchResult:
    """1 件の取得の結果。`ok` が偽なら `error` に理由が入る。"""

    url: str
    ok: bool
    fingerprint: str | None = None
    error: str | None = None


def fingerprint(body: bytes) -> str:
    """本文の指紋。前回の値と比べるためだけに使う。"""
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _set_socket_timeout(response, seconds: float) -> bool:
    """読み取りの残り時間を socket へ渡す。**渡せたかどうかを返す。**

    渡せない相手では、1 回の読み取りの**最中**は期限を見張れない。呼ぶ側が真偽を
    受け取り、越えたときの理由へその事実を残す（黙って「総経過時間で止まる」と
    言わないため）。
    """
    stream = getattr(response, "fp", None)
    candidates = [
        getattr(getattr(stream, "raw", None), "_sock", None),
        getattr(stream, "_sock", None),
        stream,
    ]
    for sock in candidates:
        setter = getattr(sock, "settimeout", None)
        if callable(setter):
            try:
                setter(max(0.001, seconds))
            except (OSError, ValueError):
                continue
            return True
    return False


def fetch(url: str, timeout: float, opener=None) -> FetchResult:
    """URL を取得する。**待ちは 1 件あたりの総経過時間**で数える。

    `opener` は `urllib.request.urlopen` と同じ形の呼び出し可能なもので、テストが
    差し替える。取得の失敗は例外にせず `FetchResult` へ理由として入れる
    （黙って落とさずに一覧へ出すため）。
    """
    opener = opener or urllib.request.urlopen
    deadline = time.monotonic() + timeout
    try:
        response = opener(url, timeout=max(0.001, deadline - time.monotonic()))
    except Exception as exc:  # noqa: BLE001 - 取得の失敗は理由として残す
        return FetchResult(url=url, ok=False, error=_reason(exc))

    chunks: list[bytes] = []
    bounded = True
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchTimeout(_timeout_reason(timeout, bounded))
            # **読み取りの最中も期限を見張る。** 渡せなかったときは、その事実を
            # 越えたときの理由へ残す。
            bounded = _set_socket_timeout(response, remaining) and bounded
            chunk = response.read(CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
    except Exception as exc:  # noqa: BLE001
        return FetchResult(url=url, ok=False, error=_reason(exc))
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()

    return FetchResult(url=url, ok=True, fingerprint=fingerprint(b"".join(chunks)))


def _timeout_reason(timeout: float, bounded: bool) -> str:
    if bounded:
        return f"{timeout} 秒を越えた"
    return (f"{timeout} 秒を越えた"
            "（読み取りの最中は上限を掛けられなかった。socket へ届いていない）")


def _reason(exc: Exception) -> str:
    if isinstance(exc, FetchTimeout):
        return f"待ちを越えた（{exc}）"
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"接続できない（{exc.reason}）"
    return f"{type(exc).__name__}: {exc}"


def compare(result: FetchResult, previous: str | None) -> str:
    """前回と内容が変わったか。**指紋が無い出典は「前回の記録が無い」と出す。**"""
    if not result.ok:
        return "判定できない"
    if not previous:
        return "前回の記録が無い"
    return "変わっていない" if previous == result.fingerprint else "変わった"


def row(source: dict, result: FetchResult) -> str:
    """出典ごとの 1 行。名前・前回の参照日・取得の成否・変化・主張を並べる。"""
    state = "取得できた" if result.ok else f"取得できなかった（{result.error}）"
    return "  ".join([
        source.get("name", source.get("id", "?")),
        source.get("checked_at", "-"),
        state,
        compare(result, source.get("fingerprint")),
        source.get("claim", "-"),
    ])


def refresh(sources: list[dict], timeout: float, opener=None) -> tuple[list[str], int]:
    """出典を順に取得し、**全件の 1 行**と失敗の件数を返す。

    **取れなかった URL を黙って落とさない。** 成功した分を理由に成否を 0 へ畳まない
    のは呼ぶ側の仕事で、ここは件数だけを返す。
    """
    lines: list[str] = []
    failed = 0
    for source in sources:
        result = fetch(source.get("url", ""), timeout, opener=opener)
        if not result.ok:
            failed += 1
        lines.append(row(source, result))
    return lines, failed
