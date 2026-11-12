"""supervise.py の置き場から決まるパスと、プランの状態ディレクトリ・worktree の用意（#1142 の C1）。

`SELF` は分けた後も `supervise.py` を指す（プランのコマンドは `SELF` の副命令として書く）。
"""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import worktree_deps
from state_home import checkout_home
from supervise_lib import decl, plan as plan_mod

SELF = Path(__file__).resolve().parent.parent / "supervise.py"


SKILLS = SELF.parent.parent / "skills"
DRIVES = {
    "cross-review": SKILLS / "cross-review" / "scripts" / "drive.py",
    "cross-refactoring": SKILLS / "cross-refactoring" / "scripts" / "drive.py",
}
REFACTOR_VOCABULARY = DRIVES["cross-refactoring"].parent / "refactor_lib" / "vocabulary.py"
EXTERNAL_AI = SKILLS / "external-ai" / "scripts" / "external-ai.py"


@functools.cache
def _refactor_vocabulary():
    spec = importlib.util.spec_from_file_location("ndf_refactor_vocabulary", REFACTOR_VOCABULARY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_refactor_budget_minutes() -> int:
    """cross-refactoring の既定の実装の予算（分）。値を写さず、cross-refactoring の定数を読む（#1916 の決定 8）。"""
    return _refactor_vocabulary().DEFAULT_BUDGET_MINUTES


def refactor_step_seconds(budget_minutes: int) -> int:
    """cross-refactoring のステップの見込みの秒 = 実装の予算 ×（1 + 安全網の倍率）（#2457 決定 8）。

    テストの実行・CI の待ちはこの外にあり、駆動が子プロセスの上限ごとにステップの延長を書いて締め切りを延ばす。"""
    import limits

    factor = float(limits.value("cross-refactoring.safety-net", "factor"))
    return int(round(float(budget_minutes) * 60 * (1 + factor)))


HERE = SELF.parent  # デプロイしたスクリプトの置き場。計画のコマンドはここからの絶対パスで書く
# run のステップの定型（"preset"）。作業場所（リポジトリの根）で動く。{base} は起点のブランチ
PRESETS = {
    "sync-check": f"python3 {SELF} sync-check --commit",
    "format": f"python3 {SELF} format --commit",  # 宣言の整形（.ndf/supervise.json の format）を当ててコミットする
    "assess": f"python3 {SKILLS / 'cross-refactoring' / 'scripts' / 'refactor.py'} assess --base origin/{{base}}",
    "doc-lint": f"python3 {HERE / 'doc-lint.py'} --base origin/{{base}}",
}


# テストの実行の入口（#1334）。範囲テスト（scope）と全体テスト（whole）を、宣言の戦略に沿って走らせる
TEST_RUN_PY = f"python3 {HERE / 'test-run.py'}"
# CI の結果の監視（#3056）。PR の CI のテストのチェックを待たずに 1 回だけ読み、記録を状態ディレクトリへ足す
CI_WATCH_PY = f"python3 {HERE / 'ci-watch.py'}"
CI_PEEK_CMD = f"{CI_WATCH_PY} peek --pr {{pr}} --state {{state_dir}}"


def is_config_lock(stderr: str) -> bool:
    return "could not lock config file" in stderr or "File exists" in stderr


def _prepare_deps(wt, **kw) -> str | None:
    """依存を用意し、失敗したら誤りの文を返す（`worktree_deps.prepare_reporting` の先頭行を包む）。"""
    err = worktree_deps.prepare_reporting(wt, **kw)
    return f"作業ツリーは作ったが依存の用意に失敗した: {err}" if err else None


def _resolve_base(plan: dict, repo: str) -> tuple[str | None, str | None]:
    """作業ツリーの起点（起点のコミット → 起点 → base_branch → 宣言の順）を決め、origin なら fetch する。(起点, 誤りの文) を返す。

    「起点のコミット」は作業ツリーだけが読む（まとめたリリースの本番のプランが承認したコミットを入れる）。版の判定と
    probe の {base} は「起点」（`origin/<ベースブランチ>`）を読むため、承認したコミットを「起点」へ入れない。"""
    base = plan.get("起点のコミット") or plan.get("起点")
    if not base:
        b = plan.get("base_branch") or decl.declared_base_of([repo])
        if not b:
            return None, "作業ツリーの起点が分からない（計画の 起点 か base_branch、または .ndf/worktree.json の base_branch）"
        base = f"origin/{b}"
    if base.startswith("origin/"):
        subprocess.run(["git", "-C", repo, "fetch", "-q", "origin"], capture_output=True, text=True)
    return base, None


def _add_worktree_with_retry(repo: str, wt: Path, branch: str, base: str, sleep) -> subprocess.CompletedProcess:
    """作業ツリーを作る。.git/config の lock で落ちたら途中の生成物を片付けてやり直す（上限の宣言の方針
    `supervise.worktree-lock`。打ち直しは lib/waits.py）。最後の結果を返す。"""
    import limits
    import waits

    def attempt() -> subprocess.CompletedProcess:
        has = (
            subprocess.run(
                ["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], capture_output=True, text=True
            ).returncode
            == 0
        )
        cmd = ["git", "-C", repo, "worktree", "add", "-q"] + ([str(wt), branch] if has else ["-b", branch, str(wt), base])
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0 and is_config_lock(p.stderr):
            # lock で途中まで作られた作業ツリーは、次のやり直しの前に片付ける
            subprocess.run(["git", "-C", repo, "worktree", "prune"], capture_output=True, text=True)
            if wt.exists() and not any(wt.iterdir()):
                wt.rmdir()
        return p

    got = waits.retry_call(
        attempt,
        lambda p: p.returncode != 0 and is_config_lock(p.stderr),
        policy=limits.policy("supervise.worktree-lock"),
        reason=lambda p: p.stderr,
        sleep=sleep,
    )
    return got.value


def ensure_worktree(plan: dict, sleep=time.sleep) -> str | None:
    """計画に branch があり作業場所が無ければ、作業ツリーを作る。誤りの文を返す（無ければ None）。

    run と queue の両方が使う。同時に作ると .git/config の lock で落ちるので、そのときは
    上限の宣言の方針 `supervise.worktree-lock` の間隔と回数でやり直す。
    """
    plan = plan_mod.normalize_plan(dict(plan))
    branch = plan.get("branch")
    wt = Path(plan["作業場所"])
    if not branch:
        return None
    if wt.exists():
        # 使い回す作業ツリーは、組み直した起点のコミットへ進め、依存の用意が済んでいなければ用意する（#1337・#3121）
        return _follow_start(plan, wt) or _prepare_deps(wt, if_unprepared=True)
    repo = plan.get("リポジトリ") or (str(wt).split("/.worktrees/")[0] if "/.worktrees/" in str(wt) else None)
    if not repo:
        return "作業ツリーの元のリポジトリが分からない（計画に リポジトリ を書く）"
    base, err = _resolve_base(plan, repo)
    if err:
        return err
    p = _add_worktree_with_retry(repo, wt, branch, base, sleep)
    if p.returncode == 0:
        # 在るローカルのブランチから作ったときも、起点のコミットより古ければ進める
        return _follow_start(plan, wt) or _prepare_deps(wt, main_dir=repo)
    return f"作業ツリーを作れない: {p.stderr.strip()[:300]}"


def _follow_start(plan: dict, wt: Path) -> str | None:
    """作業ツリーの HEAD が「起点のコミット」の祖先なら、そこまで fast-forward する（本番のプランを作った後に組み直した
    リリースブランチの先頭へ追いつく。#3121）。HEAD が既に含むなら何もしない。どちらでもない（作業ツリーに組み直しの前の
    コミットがある）なら、消さずに誤りの文を返す。起点のコミットが無い・解けない計画は何もしない。"""
    start = plan.get("起点のコミット")
    if not start:
        return None
    if start.startswith("origin/"):
        subprocess.run(["git", "-C", str(wt), "fetch", "-q", "origin"], capture_output=True, text=True)
    if (
        subprocess.run(["git", "-C", str(wt), "rev-parse", "--verify", "--quiet", f"{start}^{{commit}}"], capture_output=True).returncode
        != 0
    ):
        return None

    def ancestor(a: str, b: str) -> bool:
        return subprocess.run(["git", "-C", str(wt), "merge-base", "--is-ancestor", a, b], capture_output=True).returncode == 0

    if ancestor(start, "HEAD"):
        return None
    if ancestor("HEAD", start):
        p = subprocess.run(["git", "-C", str(wt), "merge", "-q", "--ff-only", start], capture_output=True, text=True)
        return None if p.returncode == 0 else f"作業ツリーを起点のコミット {start} へ進められない: {p.stderr.strip()[:300]}"
    return f"作業ツリーの HEAD が起点のコミット {start} と分かれている（組み直しの前のコミットがある。作業ツリーを片付けてから打ち直す）"


MERGE_CMD = f"python3 {HERE / 'merged-steps.py'} merge-when-green {{pr}}"
MERGE_GATE_CMD = f"python3 {HERE / 'merged-steps.py'} merge-gate --pr {{pr}}"  # 宛先は PR から読む（プランの途中で宛先が変わる）
# マージの待ちのステップの一次の調査（遅れたとき PR のチェックを分け、取り残しを再実行する）
MERGE_PROBE = {"cmd": f"python3 {HERE / 'merged-steps.py'} probe --pr {{pr}} --act"}
CHECK_PY = f"python3 {HERE / 'check-trigger.py'}"
# 検査のプランが cross-refactoring へ渡す範囲を PR の差分と宣言から組む（#1484）
REFACTOR_SCOPE_PY = f"python3 {HERE / 'refactor-scope.py'}"
# 振る舞いを決める文書を変えた PR の、振る舞いの確認の対象・記録・確認（#2925 の決定 6）。検査のプランがコードレビューの後に置く。
# 振る舞いを決める文書の変更が無ければ（終了コード 3）全体テストの前の取り込みへ進む。手順の本文は targets の結果（record.procedure）が持つ
BEHAVIOR_PY = f"python3 {HERE / 'behavior-check.py'}"
_BEHAVIOR_RUN = {"type": "run", "stage": "完了判定", "on_fail": "abort"}
BEHAVIOR_STEPS = (
    {**_BEHAVIOR_RUN, "id": "behavior-targets", "cmd": f"{BEHAVIOR_PY} targets --pr {{pr}}", "skip_to": "base-sync", "next": "behavior"},
    {
        "id": "behavior",
        "type": "work",
        "kind": "振る舞いの確認",
        "stage": "完了判定",
        "inputs": ["behavior-targets"],
        "prompt": "入力の behavior-targets の結果の record にある手順（procedure）どおりに振る舞いを確かめ、post のコマンドで PR へ記録する。",
        "next": "behavior-verify",
    },
    {**_BEHAVIOR_RUN, "id": "behavior-verify", "cmd": f"{BEHAVIOR_PY} verify --pr {{pr}}", "next": "base-sync"},
)
MVV_PY = f"python3 {HERE / 'mvv-gate.py'}"
SPRINT_STATE_PY = f"python3 {HERE / 'sprint-state.py'}"
GLOSSARY_PY = f"python3 {HERE / 'glossary.py'}"
SPEC_COPY_PY = f"python3 {HERE / 'spec-copy.py'}"
# 完了判定の設計との突き合わせ（確認 (a) tests・確認 (b) specs。#1241）
DESIGN_MATCH_PY = f"python3 {HERE / 'design-match.py'}"
# 自前で作る決定に外部の調査で比べた候補があるかの検査（#2519）
DESIGN_SURVEY_PY = f"python3 {HERE / 'design-survey.py'}"
DECISIONS_SH = HERE / "pr-body-decisions.sh"  # 設計の PR の本文の「決めたこと」を設計文書の決定へ合わせる
# 設計の PR の本文の「決めたこと」を設計文書の決定へ合わせてから push する（CI の pr-body-decisions が見る）
PUSH_DESIGN = f"git push -q && bash {DECISIONS_SH} sync {{pr}}"
# PR を送ってから Draft を外す。push が落ちたら gh pr ready へ進まず止まる（`;` でつなぐと落ちても ready になる。#1908）
READY = "sh -c 'git push -q && gh pr ready {pr}'"
# 設計のプランの Draft 外し。push は直前の push-glossary（on_fail 無し）が済ませ、落ちればここへ来ない。
# レビューの駆動が落ちた経路では別の場所の直しに追いついていない HEAD を送らない
DESIGN_READY = "gh pr ready {pr}"
# PR を作った直後に本文の「決めたこと」を確かめる。pr のステップ（decisions）が作る時点で揃えるため、
# 揃っていれば書き込まず（本文の編集で CI を再起動しない）、揃わなかったときだけ直す
SYNC_DESIGN_BODY = f"bash {DECISIONS_SH} sync {{pr}}"
STEPS_PY = f"python3 {HERE / 'release-steps.py'}"
VERIFY_PY = f"python3 {HERE / 'release-verification-steps.py'}"
MERGED_PY = f"python3 {HERE / 'merged-steps.py'}"
PARALLEL_PY = f"python3 {HERE / 'parallel.py'}"  # 並列の計画と進行（#2436）
WORKTREE_SETUP = SELF.parent / "worktree-setup.sh"


def report_result(text: str) -> str:
    m = re.search(r"^- 結果: (\S+)", text, re.M)
    return m.group(1) if m else "不明"


def is_dir_entry(path: str, root: str) -> bool:
    """`触るファイル` の 1 行がディレクトリか（末尾が `/` か、`root` の下の既存のディレクトリ。#1674）。"""
    return str(path).endswith("/") or (Path(root) / str(path)).is_dir()


def state_dir_of(plan: str) -> Path:
    """プランの状態ディレクトリ `<プラン>-state`（パスは変えない）。

    プランが一時ディレクトリの下にあり、状態の置き場所がそうでないときだけ、実体を
    `<状態の置き場所>/<stem>-<プランの絶対パスの sha256 の先頭 8 字>/` に作り、`<プラン>-state` を
    そこへのシンボリックリンクにする（#1142 の不足 f。OS の再起動と /tmp の掃除で記録を失わない）。
    実体にはプランの写し `plan.json` を置く。既にある `<プラン>-state` はそのまま使う（切れたリンクは指し直す）。
    """
    link = Path(plan).parent / (Path(plan).stem + "-state")
    try:
        if link.exists():
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
        if link.is_symlink():  # 実体を移した（`state-home.py migrate`）後の切れたリンク。移し先の実体を指し直す
            link.unlink()
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
    """プランの状態の実体の置き場所。`NDF_SV_STATE_DIR` → `<チェックアウトの根>/sv`（根は `state_home.checkout_home`）。"""
    if os.environ.get("NDF_SV_STATE_DIR"):
        return Path(os.environ["NDF_SV_STATE_DIR"])
    return checkout_home() / "sv"


def queue_done_path(plans: list[str], done: str | None) -> Path:
    """queue の終わりに結果の JSON を書く所。省けば最初の計画の状態ディレクトリの queue-done.json。"""
    return Path(done) if done else state_dir_of(plans[0]) / "queue-done.json"


def queue_done_key(done_path: Path) -> str:
    """持ち主の鍵に使う done のパス（#2342）。リポジトリの根からの相対にし、worktree の違いで鍵を割らない。git の外は絶対パス。"""
    import state_home

    path = Path(done_path).resolve()
    got = state_home.find_git_dir(path.parent if path.parent.exists() else Path.cwd())
    if got is not None and path.is_relative_to(got[0]):
        return path.relative_to(got[0]).as_posix()
    return str(path)


def queue_plans_path(done: Path) -> Path:
    """queue が始めに流す計画の一覧を書く所（done の隣）。wait が読む。"""
    return done.with_suffix(".plans.json")


def wait_cursor_path(done: Path) -> Path:
    """wait が attention をどこまで知らせたかを残す所（done の隣）。"""
    return done.with_suffix(".wait.json")


def sha256_of(path: Path) -> str:
    """ファイルの中身の sha256（MVV の承認の記録と照らす）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()
