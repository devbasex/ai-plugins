"""supervise.py の置き場から決まるパスと、プランの状態ディレクトリ・worktree の用意（#1142 の C1）。

`SELF` は分けた後も `supervise.py` を指す（プランのコマンドは `SELF` の副命令として書く）。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from supervise_lib import decl, plan as plan_mod

SELF = Path(__file__).resolve().parent.parent / "supervise.py"


SKILLS = SELF.parent.parent / "skills"
DRIVES = {"cross-review": SKILLS / "cross-review" / "scripts" / "drive.py",
          "cross-refactoring": SKILLS / "cross-refactoring" / "scripts" / "drive.py"}
EXTERNAL_AI = SKILLS / "external-ai" / "scripts" / "external-ai.py"
HERE = SELF.parent  # 配布したスクリプトの置き場。計画のコマンドはここからの絶対パスで書く
# run のステップの定型（"preset"）。作業場所（リポジトリの根）で動く。{base} は起点のブランチ
PRESETS = {
    "sync-check": f"python3 {SELF} sync-check --commit",
    "assess": f"python3 {SKILLS / 'cross-refactoring' / 'scripts' / 'refactor.py'} assess --base origin/{{base}}",
    "doc-lint": f"python3 {HERE / 'doc-lint.py'} --base origin/{{base}}",
}


def with_paths(cmd: str, paths: str) -> str:
    """テストのコマンドの {paths} を範囲に置き換える。{paths} が無ければ末尾に足す。"""
    return cmd.replace("{paths}", paths) if "{paths}" in cmd else f"{cmd} {paths}"


WORKTREE_LOCK_RETRIES = 5        # .git/config の lock で落ちたときのやり直しの回数
WORKTREE_LOCK_WAIT = 1.0         # やり直しの間隔（秒）


def is_config_lock(stderr: str) -> bool:
    return "could not lock config file" in stderr or "File exists" in stderr


def ensure_worktree(plan: dict, sleep=time.sleep) -> str | None:
    """計画に branch があり作業場所が無ければ、作業ツリーを作る。誤りの文を返す（無ければ None）。

    run と queue の両方が使う。同時に作ると .git/config の lock で落ちるので、そのときは
    WORKTREE_LOCK_WAIT 秒おきに WORKTREE_LOCK_RETRIES 回までやり直す。
    """
    plan = plan_mod.normalize_plan(dict(plan))
    branch = plan.get("branch")
    wt = Path(plan["作業場所"])
    if not branch or wt.exists():
        return None
    repo = plan.get("リポジトリ") or (str(wt).split("/.worktrees/")[0] if "/.worktrees/" in str(wt) else None)
    if not repo:
        return "作業ツリーの元のリポジトリが分からない（計画に リポジトリ を書く）"
    base = plan.get("起点")
    if not base:
        b = plan.get("base_branch") or decl.declared_base_of([repo])
        if not b:
            return "作業ツリーの起点が分からない（計画の 起点 か base_branch、または .ndf/worktree.json の base_branch）"
        base = f"origin/{b}"
    if base.startswith("origin/"):
        subprocess.run(["git", "-C", repo, "fetch", "-q", "origin"], capture_output=True, text=True)
    p = None
    for i in range(WORKTREE_LOCK_RETRIES + 1):
        if i:
            sleep(WORKTREE_LOCK_WAIT)
        has = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                             capture_output=True, text=True).returncode == 0
        cmd = ["git", "-C", repo, "worktree", "add", "-q"] + ([str(wt), branch] if has
                                                             else ["-b", branch, str(wt), base])
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0:
            return None
        if not is_config_lock(p.stderr):
            break
        # lock で途中まで作られた作業ツリーは、次のやり直しの前に片付ける
        subprocess.run(["git", "-C", repo, "worktree", "prune"], capture_output=True, text=True)
        if wt.exists() and not any(wt.iterdir()):
            wt.rmdir()
    return f"作業ツリーを作れない: {p.stderr.strip()[:300]}"


MERGE_CMD = f"python3 {HERE / 'merged-steps.py'} merge-when-green {{pr}}"
# マージの待ちのステップの一次の調査（遅れたとき PR のチェックを分け、取り残しを再実行する）
MERGE_PROBE = {"cmd": f"python3 {HERE / 'merged-steps.py'} probe --pr {{pr}} --act"}
CHECK_PY = f"python3 {HERE / 'check-trigger.py'}"
MVV_PY = f"python3 {HERE / 'mvv-gate.py'}"
GLOSSARY_PY = f"python3 {HERE / 'glossary.py'}"
SPEC_COPY_PY = f"python3 {HERE / 'spec-copy.py'}"
# 設計の PR の本文の「決めたこと」を設計文書の決定へ合わせてから push する（CI の pr-body-decisions が見る）
PUSH_DESIGN = f"git push -q && bash {HERE / 'pr-body-decisions.sh'} sync {{pr}}"
STEPS_PY = f"python3 {HERE / 'release-steps.py'}"
VERIFY_PY = f"python3 {HERE / 'release-verification-steps.py'}"
MERGED_PY = f"python3 {HERE / 'merged-steps.py'}"
WORKTREE_SETUP = SELF.parent / "worktree-setup.sh"


def report_result(text: str) -> str:
    m = re.search(r"^- 結果: (\S+)", text, re.M)
    return m.group(1) if m else "不明"


def state_dir_of(plan: str) -> Path:
    """プランの状態ディレクトリ `<プラン>-state`（パスは変えない）。

    プランが一時ディレクトリの下にあり、状態の置き場所がそうでないときだけ、実体を
    `<状態の置き場所>/<stem>-<プランの絶対パスの sha256 の先頭 8 字>/` に作り、`<プラン>-state` を
    そこへのシンボリックリンクにする（#1142 の不足 f。OS の再起動と /tmp の掃除で記録を失わない）。
    実体にはプランの写し `plan.json` を置く。既にある `<プラン>-state` はそのまま使う。
    """
    link = Path(plan).parent / (Path(plan).stem + "-state")
    try:
        if link.exists() or link.is_symlink():
            return link
        home = sv_state_home()
        if not under_temp(Path(plan)) or under_temp(home):
            return link
        digest = hashlib.sha256(str(Path(plan).resolve()).encode()).hexdigest()[:8]
        real = home / f"{Path(plan).stem}-{digest}"
        real.mkdir(parents=True, exist_ok=True)
        if Path(plan).is_file() and not (real / "plan.json").exists():
            shutil.copyfile(plan, real / "plan.json")
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(real, target_is_directory=True)
    except FileExistsError:
        pass  # 並行する起動が先に作った
    except OSError as e:
        print(f"supervise: 状態の実体を置けない（{link} をそのまま使う）: {e}", file=sys.stderr)
    return link


def temp_roots() -> tuple[Path, ...]:
    """一時ディレクトリとみなす場所（OS の再起動や掃除で消えうる）。"""
    roots = {Path(tempfile.gettempdir()), Path("/tmp")}
    out = []
    for r in roots:
        try:
            out.append(r.resolve())
        except OSError:
            continue
    return tuple(out)


def under_temp(path: Path) -> bool:
    try:
        p = Path(os.path.abspath(path)).resolve()
    except OSError:
        return False
    return any(p == r or r in p.parents for r in temp_roots())


def sv_state_home() -> Path:
    """プランの状態の実体の置き場所。`NDF_SV_STATE_DIR` → `${XDG_STATE_HOME}/ndf/sv` → `~/.local/state/ndf/sv`。"""
    if os.environ.get("NDF_SV_STATE_DIR"):
        return Path(os.environ["NDF_SV_STATE_DIR"])
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ndf" / "sv"


def queue_done_path(plans: list[str], done: str | None) -> Path:
    """queue の終わりに結果の JSON を書く所。省けば最初の計画の状態ディレクトリの queue-done.json。"""
    return Path(done) if done else state_dir_of(plans[0]) / "queue-done.json"


def queue_plans_path(done: Path) -> Path:
    """queue が始めに流す計画の一覧を書く所（done の隣）。wait が読む。"""
    return done.with_suffix(".plans.json")


def wait_cursor_path(done: Path) -> Path:
    """wait が attention をどこまで知らせたかを残す所（done の隣）。"""
    return done.with_suffix(".wait.json")


def sha256_of(path: Path) -> str:
    """ファイルの中身の sha256（MVV の承認の記録と照らす）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()
