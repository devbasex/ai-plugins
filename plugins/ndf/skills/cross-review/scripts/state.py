#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-review state.json 操作 CLI。

`/tmp/cross-review-pr<PR>-state.json` の初期化 / 読み書きと、
ループ判定（round 開始 / 収束 / 振動 / PR ローテーション要否 / fix 結果マージ /
deferred nit レポート）を 1 つの CLI に集約する。

Subcommands:
  init           Step 0  state 初期化 or 再開（プリチェック込み）
  start-round    Step 1  round 開始判定 (ROUND/ROUND_IN_PR/PR を stdout に出す)
  read-result    Step 2.5 codex/gemini の result.json を state にマージ
  judge          Step 3  intent ベース pass 判定 (exit 0=approved, 2=continue)
  check-oscillation Step 4 path:line 重複率を計算
  merge-fix      Step 5 post  fix サブエージェント戻り値を state にマージ + CI 分類
  should-rotate  Step 6  rotate_after 到達判定 (exit 0=rotate, 2=keep)
  set-current-pr        PR ローテーション後の current_pr 更新
  report         Step 8  deferred nit + ラウンドサマリ表示

すべての出力は人間可読 + KEY=VALUE 形式（eval / read で取り回し可能）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import subprocess
import sys
from typing import Any


# ---------------- helpers ----------------

def _default_worktree_base() -> pathlib.Path:
    """worktree の親ディレクトリを環境に応じて解決する。

    優先順位:
      1. 環境変数 NDF_WORKTREE_BASE (明示オーバーライド)
      2. /work/worktrees (Linux コンテナ環境互換、書き込み可能ならそれを使う)
      3. $HOME/work/worktrees (macOS / WSL 等のフォールバック)
    """
    env = os.environ.get("NDF_WORKTREE_BASE")
    if env:
        return pathlib.Path(env)
    legacy = pathlib.Path("/work/worktrees")
    try:
        legacy.mkdir(parents=True, exist_ok=True)
        # mkdir 成功 = 書き込み可能 → 既存環境互換でこちらを使う
        return legacy
    except OSError:
        pass
    return pathlib.Path.home() / "work" / "worktrees"


def _tmp_dir(workspace: str | None = None) -> pathlib.Path:
    """cross-review 用 tmp ディレクトリを決定する。

    優先順位:
      1. 環境変数 `CROSS_REVIEW_TMP_DIR` (明示)
      2. `~/.gemini/tmp/<workspace-basename>/` (gemini workspace 制約を回避するため、
         `~/.gemini/tmp/` が存在するなら自動使用)
      3. `/tmp/` (フォールバック)

    `workspace` 未指定なら `os.getcwd()` の basename を使う。
    """
    env = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env:
        d = pathlib.Path(env)
        d.mkdir(parents=True, exist_ok=True)
        return d
    base_name = pathlib.Path(workspace or os.getcwd()).name
    gemini_root = pathlib.Path.home() / ".gemini" / "tmp"
    if gemini_root.is_dir() and base_name:
        d = gemini_root / base_name
        d.mkdir(parents=True, exist_ok=True)
        return d
    return pathlib.Path("/tmp")


def _state_path(pr: int) -> pathlib.Path:
    return _tmp_dir() / f"cross-review-pr{pr}-state.json"


def _payload_path(agent: str, pr: int, round_: int) -> pathlib.Path:
    return _tmp_dir() / f"{agent}-review-pr{pr}-round{round_}-payload.json"


def _existing_comments_path(pr: int) -> pathlib.Path:
    return _tmp_dir() / f"cross-review-pr{pr}-existing-comments.txt"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _load(pr: int) -> dict[str, Any]:
    p = _state_path(pr)
    if not p.exists():
        die(f"state.json not found: {p}")
    return json.loads(p.read_text())


def _save(pr: int, state: dict[str, Any]) -> None:
    p = _state_path(pr)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(p)


def _sh(cmd: list[str], check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"command failed ({' '.join(cmd)}): {r.stderr.strip()}")
    return r.stdout.strip()


def die(msg: str, code: int = 1) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------- subcommands ----------------

def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — state 初期化 or 既存 state 引き継ぎ + プリチェック。"""
    pr = args.pr
    # worktree path を先に解決してから tmp_dir を決定する。
    # gemini の workspace 制約 (~/.gemini/tmp/<workspace_basename>) と
    # 一致させるため、worktree basename ベースで tmp_dir を計算する必要がある。
    # 旧実装は _tmp_dir(args.worktree) を args.worktree=None のまま呼び、
    # os.getcwd() の basename (= 親リポジトリ名) を採用していたため、
    # launch-gemini.sh で `cd $WORKTREE` した後の gemini が
    # `~/.gemini/tmp/<repo>` への write をブロックして hard timeout していた。
    worktree = args.worktree or str(_default_worktree_base() / f"pr{pr}")
    tmp_dir = _tmp_dir(worktree)
    state_file = tmp_dir / f"cross-review-pr{pr}-state.json"

    # 再開
    if state_file.exists():
        st = json.loads(state_file.read_text())
        if st.get("final") is None:
            wt = st.get("worktree_path") or ""
            info(f"↻ 前回中断 state から再開（round={len(st.get('rounds', []))}）")
            print(f"PR={st['current_pr']}")
            print(f"WORKTREE={wt}")
            print(f"TMP_DIR={tmp_dir}")
            print(f"RESUMED=1")
            return

    # 新規 init: プリチェック
    me = _sh(["gh", "api", "user", "--jq", ".login"])
    author = _sh(["gh", "pr", "view", str(pr), "--json", "author", "--jq", ".author.login"])
    is_own = (me == author)
    event_downgrade = is_own
    if is_own:
        info(f"⚠ 自分の PR (author={me}) — REQUEST_CHANGES → COMMENT 強制ダウングレード")

    # worktree 分離
    head_branch = _sh(["gh", "pr", "view", str(pr), "--json", "headRefName", "--jq", ".headRefName"])
    base_branch = _sh(["gh", "pr", "view", str(pr), "--json", "baseRefName", "--jq", ".baseRefName"])
    if not pathlib.Path(worktree).exists():
        _sh(["git", "fetch", "origin", head_branch])
        # head branch が既に別の worktree (例: 現在の作業ディレクトリ) で checkout されている
        # 場合、`git worktree add <path> <branch>` は
        # `fatal: '<branch>' is already used by worktree at '<other>'`
        # で落ちる。これを避けるため、`origin/<head_branch>` を **detached** で展開する。
        # cross-review はファイル参照しかしないので detached HEAD で全く問題ない。
        _sh(["git", "worktree", "add", "--detach", worktree, f"origin/{head_branch}"])
        info(f"✅ worktree 作成 (detached @ origin/{head_branch}): {worktree}")
    else:
        info(f"↻ 既存 worktree 流用: {worktree}")

    # 既存コメントスナップショット（重複指摘防止）。
    # NOTE: `gh api --paginate` は REST のページごとに **JSON 配列が連続して** stdout に出る
    # ため、`json.loads(r.stdout)` は複数ページで JSONDecodeError になり、コメントが空に
    # 落ちる。`--jq '.[] | ...'` で gh CLI 側に整形させ、行単位で素直に書き出す。
    repo = _sh(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    jq_filter = (
        r'.[] | "\(.path // "?"):\(.line // .original_line // "?") '
        r'[\(.user.login)] \(.body // "" | split("\n")[0])"'
    )
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr}/comments", "--paginate", "--jq", jq_filter],
        capture_output=True, text=True,
    )
    existing_path = tmp_dir / f"cross-review-pr{pr}-existing-comments.txt"
    if r.returncode == 0:
        existing_path.write_text(r.stdout)
    else:
        info(f"⚠ 既存コメント取得失敗: {r.stderr.strip()[:200]}")
        existing_path.write_text("")

    state = {
        "started_at": _now(),
        "max_rounds": args.max_rounds,
        "rotate_after": args.rotate_after,
        "only": args.only,
        "current_pr": pr,
        "worktree_path": worktree,
        "tmp_dir": str(tmp_dir),
        "repo": repo,
        "head_branch": head_branch,
        "base_branch": base_branch,
        "pr_author": author,
        "is_own_pr": is_own,
        "event_downgrade": event_downgrade,
        "pr_history": [{"pr": pr, "opened_at": _now(), "closed_at": None, "rounds": 0}],
        "rounds": [],
        "deferred_nits": [],
        "final": None,
    }
    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    info(f"✅ state 初期化: {state_file}")
    print(f"PR={pr}")
    print(f"WORKTREE={worktree}")
    print(f"TMP_DIR={tmp_dir}")
    print(f"REPO={repo}")
    print(f"HEAD_BRANCH={head_branch}")
    print(f"BASE_BRANCH={base_branch}")
    print(f"IS_OWN_PR={'1' if is_own else '0'}")
    print(f"EVENT_DOWNGRADE={'1' if event_downgrade else '0'}")
    print("RESUMED=0")


def cmd_start_round(args: argparse.Namespace) -> None:
    """Step 1 — round 開始判定。"""
    st = _load(args.pr)
    total = len(st["rounds"])
    max_r = st["max_rounds"]
    if total >= max_r:
        st["final"] = "max_rounds"
        st["ended_at"] = _now()
        _save(args.pr, st)
        die(f"max_rounds={max_r} 到達。中断。", code=1)

    pr = st["current_pr"]
    round_no = total + 1
    round_in_pr = sum(1 for r in st["rounds"] if r["pr"] == pr) + 1

    # round エントリを開く
    st["rounds"].append({
        "round": round_no,
        "pr": pr,
        "started_at": _now(),
    })
    _save(args.pr, st)

    info(f"=== Round {round_no} / {max_r} (PR #{pr}, round_in_pr={round_in_pr}) ===")
    print(f"ROUND={round_no}")
    print(f"ROUND_IN_PR={round_in_pr}")
    print(f"PR={pr}")
    print(f"MAX_ROUNDS={max_r}")
    print(f"ROTATE_AFTER={st['rotate_after']}")


def cmd_read_result(args: argparse.Namespace) -> None:
    """Step 2.5 — codex/gemini の result.json を state にマージ。"""
    agent = args.agent
    pr = args.pr
    rfile = pathlib.Path(args.file or _tmp_dir() / f"{agent}-review-pr{pr}-result.json")
    if not rfile.exists() or rfile.stat().st_size == 0:
        die(f"{agent}: result 未生成 ({rfile})")

    r = json.loads(rfile.read_text())

    # 別名フィールドへのフォールバック (gemini が `intent` / `comment_count` を使う変則 JSON を
    # 書き出す既知のケースに対応する。仕様としては `event` / `comments_count` が正)
    intent = r.get("event") or r.get("intent")
    posted_as = r.get("posted_as") or intent
    comments = r.get("comments_count")
    if comments is None:
        comments = r.get("comment_count")

    if intent is None:
        die(
            f"{agent}: result.json に event / intent フィールドが無い ({rfile})。"
            " launcher prompt のスキーマ違反の可能性。"
        )

    st = _load(pr)
    if not st.get("rounds"):
        die(f"{agent}: state.rounds が空。`state.py start-round` を先に呼んでください")
    st["rounds"][-1][agent] = {
        "intent": intent,
        "posted_as": posted_as,
        "comments": comments,
        "review_url": r.get("review_url"),
        "by_severity": r.get("by_severity", {}),
    }
    _save(pr, st)
    info(f"✅ {agent}: intent={intent} posted_as={posted_as} comments={comments}")


def cmd_judge(args: argparse.Namespace) -> None:
    """Step 3 — intent ベース pass 判定。

    Exit code: 0=approved, 2=continue, 1=error
    """
    pr = args.pr
    st = _load(pr)
    if not st.get("rounds"):
        die("state.rounds が空。`state.py start-round` を先に呼んでください")
    last = st["rounds"][-1]
    only = st.get("only")

    def is_pass(intent: str | None, severity: dict[str, int] | None) -> bool:
        if intent in ("APPROVE", "SKIP"):
            return True
        if intent == "COMMENT":
            sev = severity or {}
            return (sev.get("critical", 0) == 0 and sev.get("major", 0) == 0)
        return False

    codex_intent = (last.get("codex") or {}).get("intent", "SKIP")
    gemini_intent = (last.get("gemini") or {}).get("intent", "SKIP")
    codex_sev = (last.get("codex") or {}).get("by_severity")
    gemini_sev = (last.get("gemini") or {}).get("by_severity")

    codex_pass = (only == "gemini") or is_pass(codex_intent, codex_sev)
    gemini_pass = (only == "codex") or is_pass(gemini_intent, gemini_sev)

    print(f"CODEX_INTENT={codex_intent}")
    print(f"GEMINI_INTENT={gemini_intent}")

    if codex_pass and gemini_pass:
        st["final"] = "approved"
        st["ended_at"] = _now()
        _save(pr, st)
        info("✅ 両方 APPROVE。収束。")
        sys.exit(0)

    info(f"→ codex={codex_intent} gemini={gemini_intent}。修正へ。")
    sys.exit(2)


def cmd_check_oscillation(args: argparse.Namespace) -> None:
    """Step 4 — path:line 重複率を計算。

    前ラウンドと現ラウンドで重複が 50% 以上なら final=oscillation で中断。
    rotation 直後は round_in_pr<2 なのでスキップ。
    """
    pr = args.pr
    st = _load(pr)
    rounds = st["rounds"]
    current_pr = st["current_pr"]
    same_pr = [r for r in rounds if r["pr"] == current_pr]
    if len(same_pr) < 2:
        info("⏭ round_in_pr<2: 振動検知スキップ")
        sys.exit(2)  # continue

    prev_round_no = same_pr[-2]["round"]
    curr_round_no = same_pr[-1]["round"]

    def collect_keys(round_no: int) -> set[str]:
        keys: set[str] = set()
        for agent in ("codex", "gemini"):
            p = _payload_path(agent, pr, round_no)
            if not p.exists():
                continue
            try:
                payload = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            for c in payload.get("comments", []):
                path = c.get("path")
                line = c.get("line") or c.get("start_line")
                if path and line is not None:
                    keys.add(f"{path}:{line}")
        return keys

    prev = collect_keys(prev_round_no)
    curr = collect_keys(curr_round_no)
    if not curr:
        info("⏭ 現ラウンドの payload なし: 振動検知スキップ")
        sys.exit(2)
    overlap = prev & curr
    ratio = len(overlap) / len(curr)
    info(f"振動検知: overlap={len(overlap)}/{len(curr)} ({ratio:.0%})")

    if ratio >= 0.5:
        st["final"] = "oscillation"
        st["ended_at"] = _now()
        _save(pr, st)
        die(f"振動検知 — 同一箇所が {ratio:.0%} 重複。中断。", code=4)
    sys.exit(2)


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """Step 5 後段 — fix サブエージェント戻り値を state にマージ + CI 分類。

    Exit code: 0=continue, 3=ci-code-fail (final=error)
    """
    pr = args.pr

    # 戻り値ファイルの探索順:
    #   1. --file 明示
    #   2. $TMP_DIR/fix-pr<PR>-result.json (正規; _tmp_dir() 解決先)
    #   3. /tmp/fix-pr<PR>-result.json (旧プロンプトで /tmp を指定したサブエージェント救済)
    candidates: list[pathlib.Path] = []
    if args.file:
        candidates.append(pathlib.Path(args.file))
    candidates.append(_tmp_dir() / f"fix-pr{pr}-result.json")
    candidates.append(pathlib.Path(f"/tmp/fix-pr{pr}-result.json"))

    ffile: pathlib.Path | None = None
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            ffile = c
            break
    if ffile is None:
        die(
            "fix サブエージェントが戻り値ファイルを生成しなかった "
            f"(checked: {[str(c) for c in candidates]})",
            code=3,
        )

    fix = json.loads(ffile.read_text())

    # key 名 fallback (サブエージェントが別名で書いた場合の救済)。
    # 正規は fix_commit / fixed_count、別名は commit_sha / fixed のみ受理する。
    fix_commit = fix.get("fix_commit") or fix.get("commit_sha")
    fixed_count = fix.get("fixed_count")
    if fixed_count is None:
        fixed_count = fix.get("fixed", 0)

    st = _load(pr)
    if not st.get("rounds"):
        die("state.rounds が空。`state.py start-round` を先に呼んでください", code=3)
    round_no = st["rounds"][-1]["round"]

    st["rounds"][-1]["fix"] = {
        "commit": fix_commit,
        "fixed": fixed_count,
        "deferred": len(fix.get("deferred", []) or []),
        "rejected": len(fix.get("rejected", []) or []),
        "resolved_threads": len(fix.get("resolved_threads", []) or []),
        "ci": fix.get("ci_status"),
        "ci_failed_checks": fix.get("ci_failed_checks", []) or [],
        "ci_note": fix.get("ci_note"),
        "by_severity": fix.get("by_severity", {}),
    }
    st["rounds"][-1]["ended_at"] = _now()
    for d in (fix.get("deferred") or []):
        st["deferred_nits"].append({**d, "pr": pr, "round": round_no})
    _save(pr, st)

    # CI 分類
    if (fix.get("ci_status") or "").upper() != "FAILURE":
        info(f"✅ fix マージ完了 (commit={fix_commit} fixed={fixed_count})")
        return

    code_patterns = ("pint", "larastan", "phpstan", "test", "lint", "type",
                     "build", "ruff", "eslint", "tsc", "mypy")
    meta_patterns = ("check_pr_requirements", "assignees", "reviewers", "labels", "meta")
    failed = fix.get("ci_failed_checks") or []
    code_fail = False
    meta_fail = False
    for name in failed:
        low = name.lower()
        if any(p in low for p in meta_patterns):
            meta_fail = True
        elif any(p in low for p in code_patterns):
            code_fail = True
        else:
            code_fail = True  # 不明は code-fail（保守的）

    if code_fail:
        st["final"] = "error"
        st["ended_at"] = _now()
        _save(pr, st)
        die(f"コード関連 CI 失敗。中断: {failed}", code=3)

    # meta only: 継続
    note = f"メタチェックのみ失敗: {failed} — コードと無関係のため継続"
    st["rounds"][-1]["fix"]["ci_note"] = note
    _save(pr, st)
    info(f"⚠ メタチェックのみ失敗 ({failed}) — 継続")


def cmd_should_rotate(args: argparse.Namespace) -> None:
    """Step 6 — PR ローテーション要否。Exit 0=rotate, 2=keep.

    判定は ``round_in_pr >= rotate_after && total < max_rounds`` のみで、
    rotate-pr.sh の ``--mode light|squash`` どちらでも同じ条件を使う。
    state.json の key は ``STATE_PR`` (最初に init した PR 番号) で固定なので、
    light モードで head_branch が変わらない場合でも整合する。
    """
    pr = args.pr
    st = _load(pr)
    current_pr = st["current_pr"]
    round_in_pr = sum(1 for r in st["rounds"] if r["pr"] == current_pr)
    total = len(st["rounds"])
    rotate_after = st["rotate_after"]
    max_r = st["max_rounds"]
    if round_in_pr >= rotate_after and total < max_r:
        info(f"🔄 PR #{current_pr} が {round_in_pr} round 経過 — ローテーション必要")
        print(f"CURRENT_PR={current_pr}")
        print(f"ROUND_IN_PR={round_in_pr}")
        sys.exit(0)
    sys.exit(2)


def cmd_set_current_pr(args: argparse.Namespace) -> None:
    """PR ローテーション完了後の state 更新。

    rotate-pr.sh の light / squash どちらでも、新 PR 番号を受け取って
    ``current_pr`` を切り替え、``pr_history`` に新 PR エントリを追加する。
    state.json のファイル名は ``STATE_PR`` (= ``args.pr``) ベースで不変なので、
    light モードで head_branch が変わらないケースでも問題なく追跡できる。
    """
    pr = args.pr  # 旧 PR (state file の key)
    new_pr = args.new_pr
    st = _load(pr)
    old_pr = st["current_pr"]
    now = _now()
    # 旧 PR の history を closed に
    for h in st["pr_history"]:
        if h["pr"] == old_pr and h["closed_at"] is None:
            h["closed_at"] = now
            h["rounds"] = sum(1 for r in st["rounds"] if r["pr"] == old_pr)
            break
    st["pr_history"].append({"pr": new_pr, "opened_at": now, "closed_at": None, "rounds": 0})
    st["current_pr"] = new_pr
    _save(pr, st)
    info(f"✅ current_pr: {old_pr} → {new_pr}")


def cmd_report(args: argparse.Namespace) -> None:
    """Step 8 — deferred nit + ラウンドサマリ表示。"""
    pr = args.pr
    st = _load(pr)
    final = st.get("final") or "in_progress"
    total = len(st["rounds"])
    prs = [h["pr"] for h in st["pr_history"]]
    rotated = max(0, len(prs) - 1)

    print(f"## 最終ステータス: {final}")
    print(f"## 総ラウンド数: {total} / PR数: {len(prs)} (rotated {rotated} 回)")
    print()
    print("## PR 履歴")
    for h in st["pr_history"]:
        state_str = "closed" if h.get("closed_at") else "open"
        print(f"- #{h['pr']} ({state_str}, {h.get('rounds', 0)} rounds)")
    print()
    print("## ラウンドサマリ")
    print("| round | PR | codex | gemini | fix | CI |")
    print("|---|---|---|---|---|---|")
    for r in st["rounds"]:
        codex = r.get("codex") or {}
        gemini = r.get("gemini") or {}
        fix = r.get("fix") or {}
        codex_s = f"{codex.get('intent', '-')} ({codex.get('comments', '-')})" if codex else "-"
        gemini_s = f"{gemini.get('intent', '-')} ({gemini.get('comments', '-')})" if gemini else "-"
        fix_s = "-"
        if fix:
            fix_s = f"{(fix.get('commit') or '')[:7]} ({fix.get('fixed', 0)} fixed, {fix.get('deferred', 0)} deferred)"
        ci_s = fix.get("ci") or "-"
        print(f"| {r['round']} | #{r['pr']} | {codex_s} | {gemini_s} | {fix_s} | {ci_s} |")
    print()

    nits = st.get("deferred_nits") or []
    if nits:
        print(f"## 残 deferred nit ({len(nits)} 件)")
        for n in nits:
            print(f"- [{n.get('severity')}] {n.get('path')}:{n.get('line')} — {n.get('summary')}")
        print()
        print("これらの nit を一括対応する場合は再度 `/ndf:fix <PR#>` を起動してください。")
    else:
        print("## 残 deferred nit: なし")


# ---------------- main ----------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="Step 0 — state 初期化 or 再開")
    sp.add_argument("pr", type=int)
    sp.add_argument("--max-rounds", type=int, default=6)
    sp.add_argument("--rotate-after", type=int, default=5)
    sp.add_argument("--only", choices=["codex", "gemini"], default=None)
    sp.add_argument("--worktree", default=None)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("start-round", help="Step 1 — round 開始判定")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_start_round)

    sp = sub.add_parser("read-result", help="Step 2.5 — review result を state にマージ")
    sp.add_argument("pr", type=int)
    sp.add_argument("agent", choices=["codex", "gemini"])
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=cmd_read_result)

    sp = sub.add_parser("judge", help="Step 3 — intent ベース pass 判定 (0=approved/2=continue)")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_judge)

    sp = sub.add_parser("check-oscillation", help="Step 4 — path:line 重複率を計算")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_check_oscillation)

    sp = sub.add_parser("merge-fix", help="Step 5 post — fix 戻り値マージ + CI 分類")
    sp.add_argument("pr", type=int)
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=cmd_merge_fix)

    sp = sub.add_parser("should-rotate", help="Step 6 — rotate 要否 (0=rotate/2=keep)")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_should_rotate)

    sp = sub.add_parser("set-current-pr", help="rotation 後の current_pr 更新")
    sp.add_argument("pr", type=int, help="state file の元 PR")
    sp.add_argument("new_pr", type=int)
    sp.set_defaults(func=cmd_set_current_pr)

    sp = sub.add_parser("report", help="Step 8 — deferred nit + サマリ表示")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
