"""並列の計画が読む配るリポジトリの状態（ベースブランチの先頭・配るプラグイン・開発版の版）・ループの記録と、リレーハブの CLI の呼び出し・待つ秒。`parallel_run` が使う。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import limits
import legacy_values
import parallel_plan as pp
from parallel_files import RELAY
from sprint_loop_names import LOOP_DIR


def base_tracking_ref(main: Path) -> str:
    """ベースブランチ（`.ndf/worktree.json` の `base_branch` → 既定ブランチ）の `origin/<名>`。決まらなければ空。"""
    import repo

    b = repo.base_branch(main)
    return f"origin/{b}" if b else ""


def release_plugin(main: Path) -> str:
    """配るプラグイン（`.ndf/supervise.json` の `release.plugin`）。無ければ空。"""
    from supervise_lib.decl import read_decl

    return str((read_decl([Path(main)], "supervise.json").get("release") or {}).get("plugin") or "")


def git_head(main: Path) -> str:
    """ベースブランチの先頭のコミット。ベースブランチが決まらなければ空。"""
    ref = base_tracking_ref(main)
    if not ref:
        return ""
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=main, capture_output=True)
    got = subprocess.run(["git", "rev-parse", ref], cwd=main, capture_output=True, text=True)
    return (got.stdout or "").strip()


def dev_version(main: Path) -> str:
    """ベースブランチの配るプラグインの `plugin.json` の開発版の版数から接尾辞を外した版（出す版）。決まらなければ空。"""
    import re

    from release_version import plugin_json_rel

    ref, plugin = base_tracking_ref(main), release_plugin(main)
    rel = plugin_json_rel(main, ref, plugin) if ref and plugin else None
    if not rel:
        return ""
    got = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=main, capture_output=True, text=True)
    try:
        return re.sub(r"-.*$", "", json.loads(got.stdout).get("version") or "") if got.returncode == 0 else ""
    except ValueError:
        return ""


def loop_records(main: Path, name: str) -> list[tuple]:
    """計画のループのレーンと、読めたループの記録（loop.json）の組。計画を読めなければ空。"""
    try:
        plan = pp.load_valid_plan(main, name)
    except (OSError, ValueError):
        return []
    out = []
    for ln in (ln for ln in plan.lanes if ln.input.kind == "loop"):
        try:
            rec = json.loads((Path(main) / LOOP_DIR / ln.input.loop / "loop.json").read_text(encoding="utf-8"))
            out.append((ln, legacy_values.normalize_laps(rec)))
        except (OSError, ValueError):
            continue
    return out


def poll_seconds() -> float:
    """`inbox` と `resume` を待つ秒（既定 60。テストは `NDF_PARALLEL_POLL` で縮める）。"""
    try:
        return max(0.1, float(os.environ.get("NDF_PARALLEL_POLL") or 60))
    except ValueError:
        return 60.0


STALL_MINUTES = int(limits.seconds("parallel-repo.stall") // 60)  # 動かないレーンを問うまでの分（設計の失敗の 3 番の既定）
SPAWN_REFUSALS = 3  # 起動の断りがこの回数続いたら問う（設計の失敗の 17 番）


def spawn_refusals() -> int:
    """起動の断りを問うまでの回数（`NDF_PARALLEL_SPAWN_REFUSALS` で変えられる。不正な値なら既定の 3）。"""
    try:
        n = int(os.environ.get("NDF_PARALLEL_SPAWN_REFUSALS") or SPAWN_REFUSALS)
    except ValueError:
        return SPAWN_REFUSALS
    return n if n >= 1 else SPAWN_REFUSALS


def stall_seconds() -> float:
    """動かないレーンを問うまでの秒（`NDF_PARALLEL_STALL_MINUTES` で変えられる。既定 60 分）。"""
    try:
        return max(1.0, float(os.environ.get("NDF_PARALLEL_STALL_MINUTES") or STALL_MINUTES)) * 60
    except ValueError:
        return STALL_MINUTES * 60.0


def relay_argv() -> list[str]:
    """リレーハブの CLI の打ち方。`NDF_PARALLEL_RELAY` があればその 1 行（テストの差し替え）。"""
    alt = os.environ.get("NDF_PARALLEL_RELAY")
    return alt.split() if alt else [sys.executable, str(RELAY)]


def relay_proc(main: Path, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
    """リレーハブの CLI を打ち、(終わったプロセス, 統一 JSON レポート) を返す。読めなければ空の dict。"""
    got = subprocess.run([*relay_argv(), *args], cwd=main, capture_output=True, text=True)
    lines = [ln for ln in (got.stdout or "").splitlines() if ln.strip().startswith("{")]
    try:
        return got, json.loads(lines[-1]) if lines else {}
    except ValueError:
        return got, {}


def relay(main: Path, *args: str) -> tuple[int, dict]:
    """リレーハブの CLI を打ち、(終了コード, 統一 JSON レポート) を返す。読めなければ空の dict。"""
    got, res = relay_proc(main, *args)
    return got.returncode, res


def relay_unavailable(main: Path) -> str | None:
    """リレーハブの CLI でレーンを起動できない理由。起動できれば None（ラッパーの外・tmux が無い・リレーハブが居ない）。"""
    if not os.environ.get("NDF_RELAY_DIR"):
        return "ラッパーの外（NDF_RELAY_DIR が無い）"
    metrics = relay(main, "ls", "--json")[1].get("metrics") or {}
    if not metrics.get("tmux"):
        return "tmux が無い"
    if not metrics.get("hub"):
        return "リレーハブが居ない"
    return None
