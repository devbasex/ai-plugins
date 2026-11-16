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
    """ベースブランチ（ブランチ戦略の宣言の `branches.base`）の `origin/<名>`。宣言を読めなければ空。"""
    import repo

    s = repo.strategy_or_none(main)
    return f"origin/{s.base}" if s else ""


def release_plugin(main: Path) -> str:
    """配るプラグイン（ブランチ戦略の宣言の `release.plugin`）。無ければ空。"""
    import repo

    s = repo.strategy_or_none(main)
    return str((s.section["release"].get("plugin") if s else None) or "")


def git_head(main: Path) -> str:
    """ベースブランチの先頭のコミット。ベースブランチが決まらなければ空。"""
    ref = base_tracking_ref(main)
    if not ref:
        return ""
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=main, capture_output=True)
    got = subprocess.run(["git", "rev-parse", ref], cwd=main, capture_output=True, text=True)
    return (got.stdout or "").strip()


class OriginUnfetchable(RuntimeError):
    """origin を取り直せない。版を読めないこと（`NO_VERSION`）とは別の理由として、git の stderr を添えて伝える。"""


def dev_version(main: Path, fetch: bool = True) -> str:
    """まとめたリリースで出す版。ベースブランチの配るプラグインの `plugin.json` の版から接尾辞を外した版で、それが既に出した
    最新の正式版のタグ以下なら、そのタグの次の PATCH（#3551。同期の PR が出した版へ揃えた後に、出した版で組まない）。
    決まらなければ空。`fetch` を偽にすると origin を取り直さず手元の追跡ブランチとタグで決める（起動の前の検査が使う。
    取り直しに落ちて版があるのに組めないと警告しないため）。取り直しに落ちたら `OriginUnfetchable` を上げる
    （`step_build` が git の stderr を添えた理由で答え、版を読めないという実際と違う理由で組み直しを問わないため）。"""
    import release_version as rv

    ref, plugin = base_tracking_ref(main), release_plugin(main)
    rel = rv.plugin_json_rel(main, ref, plugin) if ref and plugin else None
    if not rel:
        return ""
    if fetch and (err := rv.fetch_origin(main)) is not None:
        raise OriginUnfetchable(f"origin を取り直せない: {err}")
    import delivery
    import release_tag

    # タグを作らない宣言だけ本番ブランチを読み、出済みの版を見落とさない（タグを作る宣言はタグで足りる）
    prod = None if release_tag.makes_tags_of(main) else delivery.load_delivery(main).production
    return rv.collected_release(rv.observe(main, plugin, ref.removeprefix("origin/"), prod, fetch=False, rel=rel)) or ""


def loop_records(main: Path, name: str) -> list[tuple]:
    """計画のループのレーンと、読めたループの記録（loop.json）の組。計画を読めなければ空。
    記録はレーンのリポジトリ（`repo` を省けば計画の置き場）から読む（#2733 の決定 2）。"""
    try:
        plan = pp.load_valid_plan(main, name)
    except (OSError, ValueError):
        return []
    out = []
    for ln in (ln for ln in plan.lanes if ln.input.kind == "loop"):
        try:
            rec = json.loads((ln.repo_dir(Path(main)) / LOOP_DIR / ln.input.loop / "loop.json").read_text(encoding="utf-8"))
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


def mark_organizer(main: Path, name: str, clear: bool = False) -> bool:
    """打ったセッションへ計画 `name` のオーガナイザーの印を付け、同じ計画の他の印を消す（`clear` なら計画の印をすべて消す）。

    同じ計画は計画の置き場の絶対パス（耐久の記録の識別と同じ値）で照合する（#2647 の I13・決定 13）。付けられたかを返す。"""
    plan_dir = str(pp.plan_dir(main, name).resolve())
    args = ("--clear", "--plan-dir", plan_dir) if clear else ("organizer", "--plan", name, "--plan-dir", plan_dir)
    return relay(main, "role", *args)[0] == 0


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
