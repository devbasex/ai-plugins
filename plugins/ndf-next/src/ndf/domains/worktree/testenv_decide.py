"""worktree ごとのテスト環境の判断（純粋な関数）: 環境名・空きスロット・ポート・compose へ渡す値。

- 環境名は `<リポジトリ>-wt-<ブランチ>-<要約値 6 桁>` を小文字英数と `-` に揃えて 40 文字で切る。要約値は必ず残す
  （先頭が同じ長いブランチ名どうしで同じ名前にしない）。detached HEAD は worktree のパスを要約値の元にする
- ポートは `帯の下限 + スロット × 20 + 役割の番号`。帯の上限を超えたら使わない（ほかの用途と衝突する）
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping, Optional

# 環境名の長さの上限と、要約値の桁
NAME_MAX = 40
DIGEST_LEN = 6
# スロットの数（0 から数える）と、1 スロットが使うポートの幅
SLOTS = 64
PORTS_PER_SLOT = 20
_SLUG = re.compile(r"[^a-z0-9-]+")
_ROLE = re.compile(r"[^A-Z0-9]")


class PortsError(Exception):
    """採番が帯を超えた。"""


def slug(text: str) -> str:
    return re.sub(r"-+", "-", _SLUG.sub("-", text.lower())).strip("-")


def env_name(repo_name: str, branch: str, source: Optional[str] = None) -> str:
    """環境名。`source` は要約値の元（省けばブランチ名）。"""
    digest = hashlib.sha1((source if source is not None else branch).encode("utf-8"), usedforsecurity=False).hexdigest()[:DIGEST_LEN]
    head = f"{slug(repo_name)}-wt-{slug(branch)}"[: NAME_MAX - DIGEST_LEN - 1].rstrip("-")
    return slug(f"{head}-{digest}")


def free_slot(active: Iterable[int]) -> Optional[int]:
    """使われていない最も小さいスロット。無ければ `None`。"""
    used = set(active)
    return next((s for s in range(SLOTS) if s not in used), None)


def ports(band: Optional[list[Any]], slot: int, roles: Mapping[str, Any]) -> dict[str, int]:
    """役割ごとのポート。帯が無ければ空。上限を超えたら `PortsError`。"""
    if not band or not isinstance(band[0], int):
        return {}
    low = band[0]
    high = band[1] if len(band) > 1 and isinstance(band[1], int) else None
    out: dict[str, int] = {}
    for role, number in roles.items():
        if not isinstance(number, int):
            continue
        port = low + slot * PORTS_PER_SLOT + number
        if high is not None and port > high:
            raise PortsError(f"採番が帯を超えた（役割 {role} のポート {port} が上限 {high} を超える）")
        out[role] = port
    return out


def compose_values(environment: str, slot: int, worktree: str, network: str, port_map: Mapping[str, int]) -> dict[str, str]:
    """compose の定義へ渡す値（定義は `${NDF_PORT_HTTP}` のように参照して worktree ごとにポートとネットワークを分ける）。"""
    out = {"NDF_ENVIRONMENT": environment, "NDF_SLOT": str(slot), "NDF_WORKTREE": worktree, "NDF_SHARED_NETWORK": network}
    for role, port in port_map.items():
        out[f"NDF_PORT_{_ROLE.sub('_', role.upper())}"] = str(port)
    return out


def discards_volumes(value: Any) -> bool:
    """破棄でボリュームも消すか。無し・`null`・`true` で消す。読めない値は残す側へ倒す（消すと戻せない）。"""
    return value is None or value is True
