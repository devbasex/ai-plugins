"""同時に動かす名前つきのラッパーとレーンの数の上限（#3989 の決定 4）。

上限の値は判定のたびに、マシンの設定（`<状態の保存先>/relay/hub/cap.json`。`ndf relay cap` が書く）→ 環境変数
`NDF_RELAY_MAX_NAMED` → 上限の設定 `relay.max-named` の順で読む。マシンの設定を先にするのは、動いているプロセスが
引き継いだ環境変数より、後から書いた値を効かせるためである。値を持ち越さないので、動いている耐久ワークフローと
リレーハブも次の判定から新しい値を使う。

どの出所の値も 1 以上の整数だけを受け、ほかの値（`0`・`0.5`・`1.5`・`inf`・数でない値）は切り捨てずに `NamedCapError` で断る。
状態の保存先のパスは呼ぶ側が渡す（このモジュールは `identity` を読まない）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple, Optional, Union

from ndf import contracts
from ndf.contracts import facts, limits

# 上限の設定の名前とキー（`ndf.progress.lane.machine.CAP_POLICY` はこれを指す）
POLICY = ("relay.max-named", "count")
FILE = "cap.json"
CAP_FILE_VERSION = 1
# 出所: マシンの設定・環境変数・上限の設定
MACHINE, FROM_ENV, FROM_LIMITS = "machine", "env", "limits"
# リレーハブが本数の上限で断った結果の items の項目の値（耐久ワークフローの起動の手がこれを待ちとして読む。#3989 の決定 7）
REFUSAL = "max-named"
_DIGITS = re.compile(r"^[1-9][0-9]*$")


class NamedCapError(contracts.ContractError):
    """上限の値が 1 以上の整数でない（マシンの設定・環境変数・上限の設定のどれか）。"""


class Cap(NamedTuple):
    """上限の値と出所（`machine`・`env`・`limits`）。"""

    value: int
    source: str


def path(state_dir: Path) -> Path:
    """マシンの設定のパス（リレーハブの保存先の中）。"""
    return state_dir / facts.value("locations", "relay-hub", "path") / FILE


def parse(raw: object, where: str) -> int:
    """1 以上の整数だけを返す。文字列は 10 進の数字だけ、数は bool でない int だけを受ける。"""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
        return raw
    if isinstance(raw, str) and _DIGITS.match(raw):
        return int(raw)
    raise NamedCapError(f"{where} の上限 {raw!r} が 1 以上の整数でない")


def _machine(state_dir: Path) -> Optional[int]:
    """マシンの設定の値。ファイルが無ければ None、壊れていれば `NamedCapError`。"""
    file = path(state_dir)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:  # noqa: NDF401 ファイル: マシンの設定が無ければ次の出所を読む
        return None
    except (OSError, ValueError) as exc:
        raise NamedCapError(f"マシンの設定 {file} を読めない: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != CAP_FILE_VERSION:
        raise NamedCapError(f"マシンの設定 {file} の形が違う（version が {CAP_FILE_VERSION} の object でない）")
    return parse(data.get("max_named"), f"マシンの設定 {file}")


def fallback(environ: Mapping[str, str]) -> Cap:
    """マシンの設定を読まずに、環境変数 → 上限の設定の順で値と出所を返す（ハブへの要求に載せる値）。"""
    raw = environ.get("NDF_RELAY_MAX_NAMED")
    if raw:
        return Cap(parse(raw, "環境変数 NDF_RELAY_MAX_NAMED"), FROM_ENV)
    return Cap(parse(limits.value(*POLICY), f"上限の設定 {POLICY[0]}"), FROM_LIMITS)


def read(environ: Mapping[str, str], state_dir: Path, fallback_cap: Optional[Union[Cap, NamedCapError]] = None) -> Cap:
    """マシンの設定 → 環境変数 → 上限の設定の順で最初にある値と出所。

    マシンの設定があればその値を返し、環境変数と上限の設定は読まない。無ければ `fallback_cap` が `Cap` ならそれを、
    `NamedCapError` ならそれを送出し、渡さなければ `fallback(environ)` を返す（ハブは要求に載った頼む側の値を渡す）。
    """
    own = _machine(state_dir)
    if own is not None:
        return Cap(own, MACHINE)
    if isinstance(fallback_cap, NamedCapError):
        raise fallback_cap
    return fallback_cap if fallback_cap is not None else fallback(environ)


def write(state_dir: Path, value: Optional[int]) -> None:
    """マシンの設定を書く（一時ファイルからの改名で置き換える）。`None` ならファイルを消す。"""
    file = path(state_dir)
    if value is None:
        file.unlink(missing_ok=True)
        return
    parse(value, "書く値")
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=f".{FILE}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"version": CAP_FILE_VERSION, "max_named": value}, fh)
    os.replace(tmp, file)
