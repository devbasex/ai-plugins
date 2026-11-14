"""学習に使わない設定の控えと、控えを読んで起動を断る規則（#3391 の 3391-5）。

控えは状態の置き場の `training.json` に、`ndf training check`（`training.py`）が時刻つきで書く。LLM と外部 CLI の
起動の口（`launch_slot.gate`）は `refusal` で控えを読むだけで、学習に使う・控えが無い・期限切れ
（`contracts/limits.json` の `training.check-valid`）・読めないなら起動を断り、理由に確認の副命令を載せる。

外部の API から設定を読む接続（httpx・認証ファイル・キーチェーン）は `training.py` が持ち、ここは読まない。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

from ndf import identity
from ndf.adapters.run_log import record
from ndf.contracts import limits

CHECK_COMMAND = "ndf training check"
# 確かめる口の無い担当（kiro・agy など）の記録の値
UNSUPPORTED = "unsupported"
# 控えを読んで起動を断ったときの記録の値
REFUSED = "refused"
CACHE_FILE = "training.json"
# 控えの期限の方針（contracts/limits.json）
VALID_POLICY = "training.check-valid"
# 確かめる口のある担当（読み手は `training.READERS`。ほかの担当は確かめず起動を通す）
CHECKED_RUNTIMES = frozenset({"claude", "codex"})


class Reading(NamedTuple):
    """1 つの担当の確認の結果。`training` は真（学習に使う）・偽（使わない）・None（確かめられない）。"""

    runtime: str
    training: Optional[bool]
    reason: str

    def item(self, checked_at: str) -> dict[str, object]:
        return {"runtime": self.runtime, "training": self.training, "reason": self.reason, "checked_at": checked_at}


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 無いか壊れていれば無いとして扱う
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 控え


def cache_path() -> Path:
    """控えの置き場（状態の置き場の `training.json`）。"""
    return identity.prepare_state_dir(identity.load()) / CACHE_FILE


def _stamp(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")


def save(readings: list[Reading], now: float) -> Path:
    """確かめた担当の結果を控えへ書く（ほかの担当の控えは残す）。一時ファイルから置き換える。"""
    path = cache_path()
    kept = _read_json(path)
    if kept is None:
        kept = {}
    kept.update({r.runtime: r.item(_stamp(now)) for r in readings})
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(kept, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _ask_check(runtime: str, why: str) -> str:
    return f"学習に使わない設定と確かめられていないので {runtime} を起動しない（{why}）。`{CHECK_COMMAND} --runtime {runtime}` で確かめる"


def _cached_refusal(runtime: str, entry: object, now: float) -> Optional[str]:
    if not isinstance(entry, dict):
        return _ask_check(runtime, "控えが無い")
    try:
        checked_at = datetime.fromisoformat(str(entry.get("checked_at"))).timestamp()
    except ValueError:
        return _ask_check(runtime, "控えの時刻が読めない")
    if now - checked_at > limits.seconds(VALID_POLICY):
        return _ask_check(runtime, f"控えの期限切れ（{entry.get('checked_at')} に確かめた）")
    training = entry.get("training")
    if training is True:
        return f"{runtime} のアカウントが入力を学習に使う設定なので起動しない。設定を Off にしてから `{CHECK_COMMAND} --runtime {runtime}` で確かめ直す"
    if training is not False:
        return _ask_check(runtime, f"確かめられなかった: {entry.get('reason')}")
    return None


def refusal(runtime: str, now: Optional[float] = None) -> Optional[str]:
    """`runtime` を起動してよければ None、断るなら理由。控えを読むだけで、確かめ直さない。

    確かめる口の無い担当（kiro・agy など）は通し、記録に `unsupported` を残す。
    """
    if runtime not in CHECKED_RUNTIMES:
        record("training", runtime=runtime, state=UNSUPPORTED)
        return None
    try:
        path = cache_path()
    except (identity.IdentityError, OSError) as exc:
        return _ask_check(runtime, f"控えの置き場を用意できない: {exc}")
    cache = _read_json(path) if path.is_file() else {}
    if cache is None:
        return _ask_check(runtime, "控えが読めない")
    why = _cached_refusal(runtime, cache.get(runtime), time.time() if now is None else now)
    if why is not None:
        record("training", runtime=runtime, state=REFUSED)
    return why
