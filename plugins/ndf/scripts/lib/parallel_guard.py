"""計画の外のセッションの起動の判定（#2733 の決定 5〜7）。

開発ループかスプリントの起動（`sprint-state.py loop start`・`supervise.py new sprint`）は、何かを書く前にここを呼ぶ。
動いている計画があるマシンで、レーン・一時レーン・オーガナイザーのどれでもなく、例外の起動の指定（`--outside-plan <理由>`）も
無ければ、終了コード 3 で止め、レーンの足し方を案内する（I9）。動いている計画が無いマシンと、Factory の実行ジョブ
（`NDF_ORGANIZER_DIR` か `NDF_FACTORY_RUN`）では今と同じに通す（I10）。計画の状態を読めないときは動いている計画として扱い、
読めなかった計画を出力に出す（I11）。例外の起動は `<状態の根>/parallel/outside-plan.jsonl` へ 1 行を足す（追記だけ）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Mapping, Optional

from state_home import container_home
from step_result import EXIT_PRECONDITION as EXIT_BLOCKED  # 止めたとき（正常な否定の結果）

PARALLEL_MD = "skills/development-workflow/references/parallel.md の「レーンの動き方」"
FACTORY_ENVS = ("NDF_ORGANIZER_DIR", "NDF_FACTORY_RUN")


def outside_log(env: Optional[Mapping[str, str]] = None) -> Path:
    return container_home(env) / "parallel" / "outside-plan.jsonl"


def _role(relay_dir: Optional[str]) -> Optional[str]:
    import parallel_files as pf

    if not relay_dir:
        return None
    try:
        got = json.loads((Path(relay_dir) / pf.ROLE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got.get("role") if isinstance(got, dict) else None


def live_plans(main: Path, status=None) -> tuple[list[str], list[str]]:
    """(動いている計画, 状態を読めなかった計画)。どちらも `<置き場>:<計画名>`。自分のメインディレクトリと計画の所在の一覧を探す。"""
    import parallel_files as pf
    import parallel_homes

    running, unreadable = [], []
    for home in parallel_homes.search_homes(Path(main)):
        for name in pf.plans_in(home):
            key = f"{home}:{name}"
            try:
                if status is None:
                    import parallel_lane as pl

                    st, live = pl.run_status(home, name), pl.RUNNING
                else:
                    st, live = status(home, name), ("PENDING", "ENQUEUED")
            except Exception:  # noqa: BLE001 — 読めない計画は動いている計画として扱う（決定 6）
                unreadable.append(key)
                continue
            if st in live:
                running.append(key)
    return running, unreadable


def judge_entry(main: Path, entry: str, reason: Optional[str] = None, env=None, status=None, is_lane=None) -> Optional[dict]:
    """止めるなら統一 JSON レポート（`status: stopped`・`metrics.blocked`）を、通すなら None を返す。例外の起動は記録してから通す。"""
    env = os.environ if env is None else env
    if any(env.get(k) for k in FACTORY_ENVS):
        return None
    running, unreadable = live_plans(main, status)
    if not running and not unreadable:
        return None
    if _role(env.get("NDF_RELAY_DIR")) in ("organizer", "helper"):
        return None
    if is_lane is None:
        import parallel_lane as pl

        is_lane = lambda: pl.lane_of(Path(main)) is not None  # noqa: E731
    try:
        if is_lane():
            return None
    except Exception:  # noqa: BLE001 — レーンか読めなければレーンでないものとして続ける
        pass
    plans = [*running, *unreadable]
    if reason:
        record_outside(main, entry, reason, plans)
        return None
    return blocked_report(entry, running, unreadable)


def entry_check(root: Optional[str], reason: Optional[str], entry: str) -> Optional[dict]:
    """起動のエントリポイントの判定（#2733 の I9）。止めるなら結果（`exit` つき）、通すなら None。何かを書く前に呼ぶ。
    理由が空の `--outside-plan` は終了コード 1 で断る。"""
    import decl
    import step_result

    if reason is not None and not reason.strip():
        return {**step_result.result("parallel-guard", "stopped", "--outside-plan には理由が要る（空の理由では通さない）"), "exit": 1}
    cwd = str(Path(root or ".").resolve())
    got = judge_entry(Path(decl.main_dir(cwd) or cwd), entry, (reason or "").strip() or None)
    return {**got, "exit": EXIT_BLOCKED} if got else None


def record_outside(main: Path, entry: str, reason: str, plans: list[str]) -> None:
    outside_log().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    row = {
        "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": str(main),
        "entry": entry,
        "reason": reason,
        "plans": plans,
    }
    with open(container_home() / "parallel" / "outside-plan.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def blocked_report(entry: str, running: list[str], unreadable: list[str]) -> dict:
    scripts = Path(__file__).resolve().parent.parent
    items = [{"plan": k, "state": "running"} for k in running] + [{"plan": k, "state": "unreadable"} for k in unreadable]
    items += [
        {"guide": PARALLEL_MD},
        {"command": f"python3 {scripts / 'relay.py'} cap --named <n>", "when": "レーン数の上限を変える（人の指示を受けてから）"},
        {"command": f"python3 {scripts / 'parallel.py'} lane <計画名> add --from <JSON>", "when": "計画へレーンを足す"},
    ]
    summary = (
        f"動いている並列の計画があるため、計画の外のセッションでは {entry} を始めない（基本不許可）。"
        "必要ならレーン数の上限を変えて計画へレーンを足す"
    )
    return {
        "tool": "parallel-guard",
        "status": "stopped",
        "summary": summary,
        "items": items,
        "metrics": {"blocked": True, "entry": entry, "running": running, "unreadable": unreadable},
        "next": f"急ぎの例外は理由を添えて {entry} --outside-plan <理由> で始める（理由はマシンの記録に残る）",
    }
