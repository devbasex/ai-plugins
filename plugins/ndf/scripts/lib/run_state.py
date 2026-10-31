#!/usr/bin/env python3
"""実行をまたいで残る状態の持ち主・寿命・片付け（#2229）。

    python3 run_state.py sweep [--root <dir>] [--tmp-only]

NDF の実行（cross-review・cross-refactoring・merged の後片付け・セッション・`worktree-deps.sh`・テストのセッション）が
作り、実行の後もディスクに残るもの（残る状態）を、種類ごとの持ち主と寿命で片付ける。種類の一覧は `KINDS` が唯一の
置き場で、確定仕様はこの表を指す。片付けは `git worktree remove`（`--force` なし）とファイルの削除だけで行い、
持ち主が生きている・Pull Request が open・作業（変更・未追跡・未 push のコミット）が残る・判定できない状態は残して
理由を返す。結果は 1 件ずつ `{"kind", "name", "result": "removed" | "kept", "reason"}` で、単独の起動は統一の JSON
（`tool: run-state`）を 1 行で出す。終了コードは 0 = 片付けた / 2 = 引数が読めない。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import worktree_base  # noqa: E402

TOOL = "run-state"
# 寿命の窓: 記録が終わっていなくても、最後に動いた時刻からこの秒数を過ぎた実行を終わったとみなす（決定 4）。
WINDOW = 24 * 3600
# レビュー worktree の中の NDF の状態（未追跡の判定から外し、外す前に消す）。
NDF_STATE_DIRS = (".cross_review", ".cross_refactoring")
_REVIEW_NAME = re.compile(r"^(pr|rf)(\d+)$")
_FIX_NAME = re.compile(r"^fix-pr(\d+)-")
_LIMIT_NAMES = 5


@dataclass(frozen=True)
class Kind:
    """残る状態の種類 1 つ。`creators` はこの種類を一時の置き場へ作るスクリプト（`plugins/ndf` からの相対）。"""

    name: str
    place: str
    owner: str
    life: str
    when: str
    creators: tuple[str, ...]


# 状態の一覧（AC1）。NDF のスクリプトが一時の置き場へ作るものは、どれかの行の `creators` に載る（テストが確かめる）。
KINDS: tuple[Kind, ...] = (
    Kind(
        "review-worktree",
        "<base>/<slug>/pr<n> と <base>/<slug>/rf<n>/{work,<ランタイム>}。記録は <base>/<slug>/{pr,rf}<n>.owner.json",
        "cross-review・cross-refactoring の実行（記録の run）",
        "持ち主が終わり、Pull Request が open でなく、作業（変更・未追跡・未 push のコミット）が無い",
        "merged の cleanup・cross-review と cross-refactoring の開始の sweep",
        (
            "scripts/lib/worktree_base.py",
            "scripts/merged-steps.py",
            "scripts/lib/run_state.py",
            "skills/cross-review/scripts/review_lib/workspace.py",
            "skills/cross-refactoring/scripts/refactor_lib/commands/setup.py",
        ),
    ),
    Kind(
        "session-state",
        "<tmp>/ndf-worktree-<session>.json（中の owner）",
        "ランタイムのセッション（プロセスの pid と開始時刻）",
        "持ち主のプロセスが居ない。記録が無ければ最終更新から寿命の窓を過ぎた",
        "sweep",
        ("scripts/hook_lib/worktree.py", "scripts/worktree-session.sh"),
    ),
    Kind(
        "deps-work",
        "<tmp>/ndf-deps.XXXXXX/（中の owner）",
        "worktree-deps.sh のプロセス（pid）",
        "持ち主のプロセスが居ない（cmdline に worktree-deps.sh を含む pid が無い）。記録が無ければ寿命の窓",
        "sweep",
        ("scripts/worktree-deps.sh",),
    ),
    Kind(
        "fix-files",
        "<tmp>/fix-pr<n>-*（CROSS_REVIEW_TMP_DIR が無いときの置き場）",
        "fix の実行",
        "最終更新から寿命の窓を過ぎた",
        "sweep",
        ("skills/fix/scripts/fix-steps.py",),
    ),
    Kind(
        "test-tmp",
        "<tmp>/ndf-tests-*",
        "テストのセッション",
        "最終更新から寿命の窓を過ぎた",
        "sweep・テストのセッションの終わり",
        ("conftest.py",),
    ),
    Kind(
        "post-item",
        "耐久の記録（種類 posts）と pending/dropped/",
        "積んだ実行",
        "送った・既投稿・控えへ外した・取り下げた",
        "Queue.flush（sweep は扱わない）",
        ("scripts/lib/post_queue.py",),
    ),
    Kind(
        "stale-copy",
        "<base>/<slug>/*.stale-<時刻>・work.stale-<時刻>",
        "退避した開始",
        "片付けない（現リポジトリの worktree でない中身で、何を持つか分からない）",
        "—",
        (),
    ),
    Kind(
        "out-of-scope",
        "<tmp> の下の Skill の置き場（ndf/・ndf-stages・ndf-guards・ndf-checks・ndf-usage・token-guard など）",
        "作るスクリプトの実行",
        "片付けない（この課題の対象外。持ち主と寿命は #2243 で決める）",
        "—",
        (
            "scripts/check-trigger.py",
            "scripts/ensure-retention.sh",
            "scripts/experimental/hook-trial/ht_checks.py",
            "scripts/experimental/hook-trial/ht_hook.py",
            "scripts/hook_lib/token_guard.py",
            "scripts/lib/gh_pr_info.py",
            "scripts/lib/sprint_loop_record.py",
            "scripts/lib/step-result.sh",
            "scripts/lib/step_result.py",
            "scripts/lib/usage_ledger.py",
            "scripts/lib/worktree-registry.sh",
            "scripts/progress-record.sh",
            "scripts/projects-sync.sh",
            "scripts/statusline-switch.sh",
            "scripts/supervise_lib/paths.py",
            "scripts/worktree-setup.sh",
            "skills/backlog-refinement/scripts/upkeep.py",
            "skills/development-workflow/scripts/lib/workflow-common.sh",
            "skills/external-ai/scripts/external-ai.py",
            "skills/pr-review/scripts/pr-review-steps.py",
        ),
    ),
)
SWEPT_KINDS = ("review-worktree", "session-state", "deps-work", "fix-files", "test-tmp")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(kind: str, name: Path | str, removed: bool, reason: str | None = None) -> dict[str, Any]:
    it: dict[str, Any] = {"kind": kind, "name": str(name), "result": "removed" if removed else "kept"}
    if reason:
        it["reason"] = reason
    return it


def _names(paths: list[str]) -> str:
    return ", ".join(paths[:_LIMIT_NAMES]) + ("…" if len(paths) > _LIMIT_NAMES else "")


# ---------------- 持ち主の記録 ----------------


def owner_path(state: Path | str) -> Path:
    """レビュー worktree（`pr<n>`・`rf<n>`）の持ち主の記録の置き場。worktree の外（隣）に置く（決定 3）。"""
    p = Path(state)
    return p.with_name(f"{p.name}.owner.json")


def read_owner(state: Path | str) -> tuple[dict[str, Any] | None, str | None]:
    """(記録, 読めない理由)。記録が無ければ (None, None)。"""
    path = owner_path(state)
    if not path.exists():
        return None, None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"持ち主の記録を読めない ({path}: {e})"
    if not isinstance(record, dict) or not record.get("run"):
        return None, f"持ち主の記録の形が違う ({path})"
    return record, None


def _write_owner(state: Path | str, record: dict[str, Any]) -> None:
    path = owner_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def claim(state: Path | str, *, tool: str, pr: int, run: str | None = None) -> str:
    """持ち主の記録を新しく書き（以前の実行の記録は上書きして引き継ぐ）、実行の識別子を返す。"""
    run = run or str(uuid.uuid4())
    now = _now_iso()
    _write_owner(
        state,
        {"version": 1, "kind": "review-worktree", "tool": tool, "pr": int(pr), "run": run, "started_at": now, "seen_at": now, "finished_at": None},
    )
    return run


def _update(state: Path | str, run: str | None, key: str) -> tuple[bool, str | None]:
    record, err = read_owner(state)
    if err:
        return False, err
    if record is None:
        return False, f"持ち主の記録が無い ({owner_path(state)})"
    if not run or record.get("run") != run:
        return False, f"持ち主の記録は別の実行 {record.get('run')} のもの（渡した実行 {run or '無し'}）"
    record[key] = _now_iso()
    _write_owner(state, record)
    return True, None


def touch(state: Path | str, run: str | None) -> tuple[bool, str | None]:
    """記録の `run` と一致するときだけ、最後に動いた時刻を書く。"""
    return _update(state, run, "seen_at")


def finish(state: Path | str, run: str | None) -> tuple[bool, str | None]:
    """記録の `run` と一致するときだけ、終わった時刻を書く（E2）。"""
    return _update(state, run, "finished_at")


def _last_seen(state: Path, record: dict[str, Any] | None) -> float:
    if record is not None:
        got = _epoch(record.get("seen_at")) or _epoch(record.get("started_at"))
        if got is not None:
            return got
    return _mtime(state)


def _mtime(path: Path) -> float:
    """状態の最終更新の時刻。ディレクトリは NDF の状態の置き場の時刻も見る。"""
    times = []
    for p in (path, *(path / d for d in NDF_STATE_DIRS)):
        with contextlib.suppress(OSError):
            times.append(p.stat().st_mtime)
    return max(times) if times else 0.0


def alive(state: Path, record: dict[str, Any] | None, now: float) -> bool:
    """記録（無ければ状態の最終更新）から、持ち主の実行が生きているかを決める（決定 4）。"""
    if record is not None and record.get("finished_at"):
        return False
    return now - _last_seen(state, record) < WINDOW


def _alive_reason(state: Path, record: dict[str, Any] | None) -> str:
    seen = _iso(_last_seen(state, record))
    who = f"持ち主の実行 {record.get('run')}" if record else "持ち主の記録が無く、最終更新が寿命の窓の中の実行"
    return f"{who} が動いている（最後に動いた時刻 {seen}）"


# ---------------- レビュー worktree ----------------


def _git(cwd: Path | str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _listed(root: Path | str) -> dict[str, dict[str, Any]]:
    """`git worktree list --porcelain` を {解決したパス: {path, detached}} にする。"""
    out, cur, rows = _git(root, "worktree", "list", "--porcelain").stdout, None, {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree ") :], "detached": False}
            rows[str(Path(cur["path"]).resolve())] = cur
        elif cur is not None and line == "detached":
            cur["detached"] = True
    return rows


def _local_work(path: Path) -> str | None:
    """追跡ファイルの未コミットの変更と、NDF の状態でない未追跡のファイル。無ければ None、あれば理由。"""
    st = _git(path, "status", "--porcelain=v1")
    if st.returncode != 0:
        return f"判定できない: git status が失敗 ({st.stderr.strip()[:200]})"
    excluded = list(NDF_STATE_DIRS)
    env = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env:
        with contextlib.suppress(ValueError):
            excluded.append(str(Path(env).resolve().relative_to(path.resolve())))
    tracked, untracked = [], []
    for line in st.stdout.splitlines():
        if len(line) < 4:
            continue
        status, rel = line[:2], line[3:]
        if status != "??":
            tracked.append(rel)
        elif not any(rel.rstrip("/") == e or rel.startswith(f"{e}/") for e in excluded):
            untracked.append(rel)
    if tracked:
        return f"追跡ファイルに未コミットの変更が {len(tracked)} 件ある（{_names(tracked)}）"
    if untracked:
        return f"NDF の状態でない未追跡のファイルが {len(untracked)} 件ある（{_names(untracked)}）"
    return None


def _unpushed(path: Path, head_oid: str | None) -> str | None:
    """未 push のコミット（どのリモートにも PR の head にも含まれない HEAD の祖先）。無ければ None、あれば理由。"""
    extra: list[str] = []
    if head_oid:
        if _git(path, "cat-file", "-e", f"{head_oid}^{{commit}}").returncode != 0:
            return f"判定できない: Pull Request の head {head_oid[:7]} が手元に無く、未 push のコミットを数えられない"
        extra = [head_oid]
    r = _git(path, "rev-list", "--count", "HEAD", "--not", "--remotes", *extra)
    try:
        count = int(r.stdout.strip()) if r.returncode == 0 else -1
    except ValueError:
        count = -1
    if count < 0:
        return f"判定できない: 未 push のコミットを数えられない ({r.stderr.strip()[:200]})"
    if count:
        return f"未 push のコミットが {count} 件ある"
    return None


def worktree_work(path: Path | str, head_oid: str | None = None) -> str | None:
    """レビュー worktree の作業（I4 の 3 つ）。無ければ None、あれば理由。"""
    return _local_work(Path(path)) or _unpushed(Path(path), head_oid)


def remove_review_worktree(root: Path | str, path: Path | str, pr: int, pr_state: dict[str, Any] | None) -> tuple[bool, str | None]:
    """レビュー worktree 1 本を `--force` を使わずに外す。外せなければ (False, 理由)。"""
    path = Path(path)
    w = _listed(root).get(str(path.resolve()))
    if w is None:
        return False, "この repo の作業ツリーとして登録されていない"
    if not w["detached"]:
        return False, "detached でない"
    why = _local_work(path) or _unpushed(path, (pr_state or {}).get("headRefOid"))
    if why:
        return False, why
    for d in NDF_STATE_DIRS:
        shutil.rmtree(path / d, ignore_errors=True)
    env = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env and Path(env).resolve().is_relative_to(path.resolve()) and Path(env).resolve() != path.resolve():
        shutil.rmtree(env, ignore_errors=True)
    r = _git(root, "worktree", "remove", str(path))
    if r.returncode != 0:
        return False, f"git worktree remove が拒否: {(r.stderr or r.stdout).strip()[:300]}"
    return True, None


def remove_run_dir(root: Path | str, rf_dir: Path | str, pr: int, pr_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """`rf<n>` の中の worktree を 1 本ずつ外し、すべて外れたら `rf<n>` と記録を消す。結果の行を返す。"""
    rf_dir, items = Path(rf_dir), []
    if not rf_dir.is_dir():
        return items
    left = False
    for child in sorted(p for p in rf_dir.iterdir() if p.is_dir()):
        ok, why = remove_review_worktree(root, child, pr, pr_state)
        items.append(_item("worktree", child, ok, why))
        left = left or not ok
    rest = [p.name for p in rf_dir.iterdir()] if rf_dir.is_dir() else []
    if left or rest:
        why = "中の worktree が残る" if left else f"worktree でないものが残る（{_names(rest)}）"
        items.append(_item("run-dir", rf_dir, False, why))
        return items
    rf_dir.rmdir()
    owner_path(rf_dir).unlink(missing_ok=True)
    items.append(_item("run-dir", rf_dir, True))
    return items


def remove_pr_dir(root: Path | str, path: Path | str, pr: int, pr_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """`pr<n>` を外し、外れたら記録も消す。結果の行を返す。"""
    path = Path(path)
    ok, why = remove_review_worktree(root, path, pr, pr_state)
    if ok:
        owner_path(path).unlink(missing_ok=True)
    return [_item("worktree", path, ok, why)]


def pr_view(root: Path | str, pr: int) -> dict[str, Any] | None:
    """Pull Request の状態と head（`gh pr view --json state,headRefOid`）。照会に失敗すれば None。テストが差し替える。"""
    r = subprocess.run(["gh", "pr", "view", str(pr), "--json", "state,headRefOid"], cwd=str(root), capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        got = json.loads(r.stdout)
    except ValueError:
        return None
    return got if isinstance(got, dict) and got.get("state") else None


def check_start(state: Path | str, run: str | None, head_oid: str | None = None) -> str | None:
    """開始が既存のレビュー worktree（`pr<n>` か `rf<n>/work`）を使ってよいか。止める理由か None を返す（I11・I12）。

    `state` は記録の置き場を決めるパス（`pr<n>` か `rf<n>`）、作業を見るのは `state` か `state/work`。"""
    state = Path(state)
    record, err = read_owner(state)
    if err:
        return f"{err}。手で確かめてから記録を直すか消す"
    if record is not None and alive(state, record, time.time()):
        if run and record.get("run") == run:
            return None
        return (
            f"別の実行 {record.get('run')} が {state} を使っている（最後に動いた時刻 {_iso(_last_seen(state, record))}）。"
            f"その実行の続きなら --run {record.get('run')} を渡す。終わっていれば寿命の窓（24 時間）を過ぎるか finish の後に開始する"
        )
    work = state / "work" if state.name.startswith("rf") else state
    if not work.is_dir() or not (work / ".git").exists():
        return None
    why = worktree_work(work, head_oid)
    if why:
        return f"以前の実行が残した {work} に作業が残る: {why}。手で確かめて外すか、続きの実行なら記録を戻してから開始する"
    return None


# ---------------- 片付け ----------------


def _sweep_review(root: Path, base: Path, skip: set[str], now: float, view: Callable[[Path, int], Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not base.is_dir():
        return items
    for path in sorted(p for p in base.iterdir() if p.is_dir() and _REVIEW_NAME.match(p.name)):
        if str(path.resolve()) in skip:
            continue
        kind, pr = _REVIEW_NAME.match(path.name).groups()
        try:
            items += _sweep_one_review(root, path, kind, int(pr), now, view)
        except Exception as e:  # noqa: BLE001  1 つの失敗でほかの片付けを止めない（I6）
            items.append(_item("worktree", path, False, f"判定できない: {type(e).__name__}: {e}"))
    return items


def _sweep_one_review(root: Path, path: Path, kind: str, pr: int, now: float, view: Callable[[Path, int], Any]) -> list[dict[str, Any]]:
    label = "run-dir" if kind == "rf" else "worktree"
    record, err = read_owner(path)
    if err:
        return [_item(label, path, False, f"判定できない: {err}")]
    if alive(path, record, now):
        return [_item(label, path, False, _alive_reason(path, record))]
    trees = sorted(p for p in path.iterdir() if p.is_dir()) if kind == "rf" else [path]
    for tree in trees:
        if (tree / ".git").exists() and (why := _local_work(tree)):
            return [_item("worktree", tree, False, why)]
    state = view(root, pr)
    if state is None:
        return [_item(label, path, False, f"判定できない: Pull Request #{pr} の状態の照会が失敗した")]
    if state.get("state") == "OPEN":
        return [_item(label, path, False, f"Pull Request #{pr} が open")]
    if kind == "rf":
        return remove_run_dir(root, path, pr, state)
    return remove_pr_dir(root, path, pr, state)


def _pid_record(raw: Any) -> tuple[int, float | None] | None:
    if isinstance(raw, dict) and isinstance(raw.get("pid"), int):
        started = raw.get("started")
        return raw["pid"], float(started) if isinstance(started, (int, float)) else None
    return None


def _process_alive(pid: int, started: float | None, cmd: str | None = None) -> bool:
    import procs  # psutil を要する。プロセスの持ち主を見るときだけ読む

    if not procs.pid_alive(pid):
        return False
    if started is not None:
        now_started = procs.start_time(pid)
        return now_started is not None and abs(now_started - started) < 1.0
    if cmd is not None:
        return procs.cmdline_contains(pid, cmd) is not False
    return True


def _window_item(kind: str, path: Path, mtime: float, now: float, remove: Callable[[], None]) -> dict[str, Any]:
    if now - mtime < WINDOW:
        return _item(kind, path, False, f"寿命の窓の中（最終更新 {_iso(mtime)}）")
    remove()
    return _item(kind, path, True)


def _rm(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _sweep_session(tmp: Path, now: float) -> list[dict[str, Any]]:
    items = []
    for path in sorted(tmp.glob("ndf-worktree-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            items.append(_item("session-state", path, False, f"判定できない: 読めない ({e})"))
            continue
        owner = _pid_record(data.get("owner") if isinstance(data, dict) else None)
        if owner is None:
            items.append(_window_item("session-state", path, path.stat().st_mtime, now, lambda p=path: p.unlink(missing_ok=True)))
        elif _process_alive(*owner):
            items.append(_item("session-state", path, False, f"持ち主のプロセス {owner[0]} が動いている"))
        else:
            path.unlink(missing_ok=True)
            items.append(_item("session-state", path, True))
    return items


def _sweep_deps(tmp: Path, now: float) -> list[dict[str, Any]]:
    items = []
    for path in sorted(p for p in tmp.glob("ndf-deps.*") if p.is_dir()):
        owner_file = path / "owner"
        pid = None
        with contextlib.suppress(OSError, ValueError):
            pid = int(owner_file.read_text(encoding="utf-8").strip())
        if pid is None:
            items.append(_window_item("deps-work", path, _mtime(path), now, lambda p=path: shutil.rmtree(p)))
        elif _process_alive(pid, None, "worktree-deps.sh"):
            items.append(_item("deps-work", path, False, f"持ち主のプロセス {pid} が動いている"))
        else:
            shutil.rmtree(path)
            items.append(_item("deps-work", path, True))
    return items


def _sweep_fix(tmp: Path, now: float) -> list[dict[str, Any]]:
    groups: dict[str, list[Path]] = {}
    for path in tmp.glob("fix-pr*-*"):
        if m := _FIX_NAME.match(path.name):
            groups.setdefault(m.group(1), []).append(path)
    items = []
    for n, paths in sorted(groups.items(), key=lambda kv: int(kv[0])):
        newest = max(_mtime(p) for p in paths)
        items.append(_window_item("fix-files", tmp / f"fix-pr{n}-*", newest, now, lambda ps=paths: [_rm(p) for p in ps]))
    return items


def _sweep_tests(tmp: Path, now: float) -> list[dict[str, Any]]:
    return [_window_item("test-tmp", p, _mtime(p), now, lambda p=p: _rm(p)) for p in sorted(tmp.glob("ndf-tests-*"))]


def _guarded(kind: str, place: Path, fn: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001  1 つの種類の失敗でほかの片付けを止めない（I6・決定 6）
        return [_item(kind, place, False, f"判定できない: {type(e).__name__}: {e}")]


def review_base(root: Path | str) -> Path | None:
    """`root` のリポジトリのレビュー worktree の置き場（`<base>/<slug>`）。slug が求まらなければ None。"""
    import repo as repo_lib

    slug = repo_lib.slug(repo_lib.owner_repo(root))
    return worktree_base.worktree_parent() / slug if slug else None


def sweep(
    root: Path | str | None,
    *,
    kinds: Iterable[str] | None = None,
    tmp_only: bool = False,
    skip: Iterable[Path | str] = (),
    now: float | None = None,
    view: Callable[[Path, int], Any] | None = None,
) -> list[dict[str, Any]]:
    """持ち主の終わった残る状態を外し、見た状態 1 つにつき結果を 1 件返す（`skip` は見ない）。例外を外へ出さない。"""
    now = time.time() if now is None else now
    wanted = set(kinds or SWEPT_KINDS)
    tmp = Path(tempfile.gettempdir())
    items: list[dict[str, Any]] = []
    if "review-worktree" in wanted and root is not None and not tmp_only:
        base = None
        try:
            base = review_base(root)
        except Exception as e:  # noqa: BLE001
            items.append(_item("worktree", root, False, f"判定できない: 置き場を求められない ({e})"))
        if base is not None:
            skipped = {str(Path(p).resolve()) for p in skip}
            items += _guarded("worktree", base, lambda: _sweep_review(Path(root), base, skipped, now, view or pr_view))
    for name, fn in (("session-state", _sweep_session), ("deps-work", _sweep_deps), ("fix-files", _sweep_fix), ("test-tmp", _sweep_tests)):
        if name in wanted:
            items += _guarded(name, tmp, lambda fn=fn: fn(tmp, now))
    return items


def counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {"removed": sum(1 for i in items if i["result"] == "removed"), "kept": sum(1 for i in items if i["result"] == "kept")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sw = sub.add_parser("sweep", help="持ち主の終わった残る状態を片付ける")
    sw.add_argument("--root", default=None, help="リポジトリの中のディレクトリ（既定は今のディレクトリ）")
    sw.add_argument("--tmp-only", action="store_true", help="レビュー worktree を見ず、一時の置き場だけを片付ける")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    items = sweep(Path(a.root or os.getcwd()), tmp_only=a.tmp_only)
    c = counts(items)
    out = {"tool": TOOL, "status": "ok", "summary": f"残る状態 {c['removed']} 件を外し、{c['kept']} 件を残した", "items": items, "metrics": c}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
