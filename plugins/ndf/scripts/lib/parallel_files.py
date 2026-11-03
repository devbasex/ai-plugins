"""並列の計画の置き場をファイルだけで読む小さな部品（#2436）。

問いの hook（10 秒の上限）がレーンか一時レーンかを見るために使う。pydantic と DBOS を import しない。
計画の形の検査は `parallel_plan.py`、耐久の記録を読むレーンの判定は `parallel_lane.py` が持つ。
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Optional

PARALLEL_DIR = Path(".ndf") / "parallel"
ROLE_FILE = "role.json"  # 統括役の作業ディレクトリの役割のファイル（relay_lib.hub.ROLE_FILE と同じ公開の形。統括役を import しない）
RELAY = Path(__file__).resolve().parent.parent / "relay.py"


def stop_self(main: Optional[Path] = None, env=None) -> Optional[str]:
    """セッションの最後に自分のラッパーを止める 1 行（判断 fca6fe464619 の (a)）。`ndf-next` を出さずに終えても対話の claude が
    入力待ちで残り、ラッパーが止まらないためである。ラッパーの外（`NDF_RELAY_NAME` が無い）では None（打たない）。
    `main` を渡すと、並列の計画のレーンか一時レーンのときだけ返す（利用者の端末のラッパーを止めない）。"""
    env = os.environ if env is None else env
    name = env.get("NDF_RELAY_NAME")
    if not name or (main is not None and lane_name_in_plans(Path(main), name, env.get("NDF_RELAY_DIR")) is None):
        return None
    return f'python3 {shlex.quote(str(RELAY))} stop "$NDF_RELAY_NAME"'


def plans_in(main: Path) -> list[str]:
    """メインディレクトリにある並列の計画の名前（`plan.json` を持つもの）。"""
    root = Path(main) / PARALLEL_DIR
    return sorted(p.name for p in root.iterdir() if (p / "plan.json").is_file()) if root.is_dir() else []


def helper_plan_of(relay_dir: Optional[str]) -> Optional[str]:
    """作業ディレクトリ（`NDF_RELAY_DIR`）の役割のファイルが `helper` なら、その計画名。"""
    if not relay_dir:
        return None
    try:
        got = json.loads((Path(relay_dir) / ROLE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got.get("plan") if isinstance(got, dict) and got.get("role") == "helper" and got.get("plan") else None


def lane_name_in_plans(main: Path, name: Optional[str], relay_dir: Optional[str]) -> Optional[str]:
    """レーンか一時レーンかをファイルだけで見る（DBOS を読まない）。計画名か None。"""
    if not name:
        return None
    for plan in plans_in(main):
        try:
            lanes = json.loads((Path(main) / PARALLEL_DIR / plan / "plan.json").read_text(encoding="utf-8")).get("lanes") or []
        except (OSError, ValueError, AttributeError):
            continue
        if any(isinstance(ln, dict) and ln.get("name") == name for ln in lanes):
            return plan
    return helper_plan_of(relay_dir)
