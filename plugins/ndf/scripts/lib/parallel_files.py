"""並列の計画の置き場をファイルだけで読む小さな部品（#2436）。

問いの hook（10 秒の上限）がレーンか一時レーンかを見るために使う。pydantic と DBOS を import しない。
計画の形の検査は `parallel_plan.py`、耐久の記録を読むレーンの判定は `parallel_lane.py` が持つ。
"""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Optional

PARALLEL_DIR = Path(".ndf") / "parallel"
# 計画名の形の唯一の定義。24 字まで（一時レーンの名前を 32 字に収める）。引継ぎ文書の名 `parallel-<計画名>` も組み込む（#2778）
PLAN_NAME = r"[a-z0-9][a-z0-9_-]{0,23}"
PLAN_RE = re.compile(rf"^{PLAN_NAME}$")
ROLE_FILE = (
    "role.json"  # リレーハブの作業ディレクトリの役割のファイル（relay_lib.hub.ROLE_FILE と同じ公開の形。リレーハブを import しない）
)
RELAY = Path(__file__).resolve().parent.parent / "relay.py"


def stop_self(main: Optional[Path] = None, env=None) -> Optional[str]:
    """セッションの最後に自分のラッパーを止める 1 行（判断 fca6fe464619 の (a)）。`ndf-next` を出さずに終えても対話の claude が
    入力待ちで残り、ラッパーが止まらないためである。ラッパーの外（`NDF_RELAY_NAME` が無い）では None（打たない）。
    `main` を渡すと、並列の計画のレーンか一時レーンのときだけ返す（利用者の端末のラッパーを止めない）。"""
    env = os.environ if env is None else env
    name = env.get("NDF_RELAY_NAME")
    if not name or (main is not None and not lane_homes_in(Path(main), name, env.get("NDF_RELAY_DIR"))):
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


def resolved_path(p: Path) -> str:
    """絶対パスの文字列（解けなければそのまま）。同じリポジトリかの比べに使う。"""
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(p)


def lane_repo_matches(lane: dict, home: Path, main: Path) -> bool:
    """宣言のレーン（JSON の値）のリポジトリ（`repo`。省けば計画の置き場 `home`）が、自分のメインディレクトリ `main` か（#2733 の決定 3）。"""
    repo = lane.get("repo")
    return resolved_path(Path(repo) if isinstance(repo, str) and repo else Path(home)) == resolved_path(Path(main))


def lane_homes_in(main: Path, name: Optional[str], relay_dir: Optional[str]) -> list[tuple[Path, str]]:
    """`name` をレーンに持つ計画の (計画の置き場, 計画名)。自分のメインディレクトリと計画の所在の一覧の置き場を探し、
    レーンのリポジトリが自分のメインディレクトリと一致する計画だけを当てる（#2733 の I5）。役割のファイルが示す一時レーンの
    計画は自分のメインディレクトリのものとして最後に足す。ファイルだけで見る（DBOS を読まない）。"""
    if not name:
        return []
    import parallel_homes

    out: list[tuple[Path, str]] = []
    for home in parallel_homes.search_homes(Path(main)):
        for plan in plans_in(home):
            try:
                lanes = json.loads((home / PARALLEL_DIR / plan / "plan.json").read_text(encoding="utf-8")).get("lanes") or []
            except (OSError, ValueError, AttributeError):
                continue
            if any(isinstance(ln, dict) and ln.get("name") == name and lane_repo_matches(ln, home, Path(main)) for ln in lanes):
                out.append((home, plan))
    helper = helper_plan_of(relay_dir)
    if helper and not any(p == helper for _h, p in out):
        out.append((Path(resolved_path(Path(main))), helper))
    return out


def plan_home(main: Path, name: str, lane: Optional[str] = None) -> Path:
    """自分（レーン `lane`。省けば `NDF_RELAY_NAME`）が属する計画 `name` の置き場。見つからなければ `main`（#2733 の I5）。"""
    for home, plan in lane_homes_in(Path(main), lane or os.environ.get("NDF_RELAY_NAME"), None):
        if plan == name:
            return home
    return Path(main)


def lane_name_in_plans(main: Path, name: Optional[str], relay_dir: Optional[str]) -> Optional[str]:
    """レーンか一時レーンかをファイルだけで見る（DBOS を読まない）。計画名か None。"""
    got = lane_plans_in(main, name, relay_dir)
    return got[0] if got else None


def lane_plans_in(main: Path, name: Optional[str], relay_dir: Optional[str]) -> list[str]:
    """自分のメインディレクトリで `name` をレーンに持つ計画の名前（並ぶ順）と、役割のファイルが示す一時レーンの計画。
    ファイルだけで見る（DBOS を読まない）。ほかの置き場の計画も探すときは `lane_homes_in` を使う。

    終わった計画と動いている計画が同じレーン名を持つことがあるため、どれが動いているかは送る側（`request-ask` の送り手の
    判定）が決める。問いの hook は候補を順に試す。"""
    if not name:
        return []
    out = []
    for plan in plans_in(main):
        try:
            lanes = json.loads((Path(main) / PARALLEL_DIR / plan / "plan.json").read_text(encoding="utf-8")).get("lanes") or []
        except (OSError, ValueError, AttributeError):
            continue
        if any(isinstance(ln, dict) and ln.get("name") == name and lane_repo_matches(ln, Path(main), Path(main)) for ln in lanes):
            out.append(plan)
    helper = helper_plan_of(relay_dir)
    return [*out, helper] if helper and helper not in out else out
