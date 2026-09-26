"""指標の測定の記録（`code_metrics`）を `init` が作る（#1319 の E1）。

`init` の新規の実行と再開がここを呼ぶ。測るのは `commands/measure.py` で、ここは測定の設定を
読んで `pending` の記録を作るだけである。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

from . import codemetrics as cm, info


def propose_started(state: dict[str, Any]) -> bool:
    """提案の手順を始めた後か（`phases.propose.started_at` がある）。始めた後は測らない（I2）。"""
    return bool(((state.get("phases") or {}).get("propose") or {}).get("started_at"))


def code_metrics_record(work: pathlib.Path, enabled: bool) -> dict[str, Any]:
    """測定の設定を読み、`pending` の記録を作る（E1。`init` が呼ぶ）。

    読むのは書き込み用の作業ディレクトリ（Pull Request の head）の宣言である。**宣言が壊れていても
    止めない**（前提 8）。既定で測り、宣言を使わなかった理由を設定に残す。`enabled` は
    `--no-code-metrics` なら偽で、引数を渡さない新規の実行は測る。
    """
    try:
        text: Optional[str] = (work / cm.DECLARATION_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        text = None
    except (OSError, UnicodeDecodeError) as exc:
        config = cm.load_config(None, enabled)
        config.update(source=cm.SOURCE_INVALID, error=f"読めない（{exc}）")
        return {"config": config, "status": cm.STATUS_PENDING}
    config = cm.load_config(text, enabled)
    if config["source"] == cm.SOURCE_INVALID:
        info(f"⚠ {cm.DECLARATION_FILE} を使いません（{config['error']}）。既定で測ります")
    return {"config": config, "status": cm.STATUS_PENDING}


def ensure_record(state: dict[str, Any], enabled: Optional[bool]) -> None:
    """旧い状態ファイル（測定の記録が無い）の再開。提案を始める前だけ記録を作る（決定 8）。

    始めた後に測ると、既に動いている参加者と後から読む者で材料が食い違う（前提 6）。
    """
    if isinstance(state.get("code_metrics"), dict) or propose_started(state):
        return
    state["code_metrics"] = code_metrics_record(pathlib.Path(state["worktrees"]["work"]), enabled is not False)


def recorded_enabled(state: dict[str, Any]) -> Optional[bool]:
    """再開で `--code-metrics` と比べる値（`init` の「知らせる」の表）。記録が無ければ `None`。"""
    record = state.get("code_metrics")
    return (record.get("config") or {}).get("enabled") if isinstance(record, dict) else None
