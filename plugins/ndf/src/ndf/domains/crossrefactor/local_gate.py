"""殻: 手元の最終ゲート（#4566 の決定 4・5・6・9・10・11）。必須のチェックが無いリポジトリで、テスト全体の合否を手元で決める。

1 周は次の順である（設計文書の「処理の流れ」）。周は `max_fix + 1` 回まで。

1. PR のブランチのリモートの先頭を取得する。先頭が `HEAD` の祖先なら載せ直さない。そうでなければ `sync_fetched` で載せ直す
2. 取得した先頭の設定（`.ndf/project.json`。コミットに無ければ `--work` の設定）の手段が手元のテストでなければ `precondition`
3. 送るコミットが無ければ、取得した先頭がこの実行が送った SHA のときだけ確かめ直す。ほかはテストを打たずに完了（`skipped`）
4. この実行のコミットがテストの定義のファイルを変えていれば `refused`
5. 検証用の作業場所でテスト全体を打つ（合格の記録にある組は打たない）
6. 合格なら、送る SHA を記録してから `<SHA>:refs/heads/<ブランチ>` を push する。拒まれたら、ブランチが進んでいれば次の周
7. 落ちたら基準側（取得した先頭だけ）と suite の単位で比べ、変更後だけで落ちた suite を、周が残れば直させる

NDF はテストの伏せる前のコマンドと出力をファイルに書かない。ログ（`rf<PR>/logs/local-gate-*.log`）は伏せ字の全文を
実行ユーザーだけが読める権限で書く。結果 JSON・状態ファイル・停止の理由には suite の呼び名と終了コードだけを載せる。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.adapters import git, run_log, secret_lines
from ndf.contracts import limits
from ndf.core.converge import worktree
from ndf.domains.crossrefactor import final_gate, launch_decide, protected_decide, verify, workspace
from ndf.testing import declared, means, slots

# 指示文に載せる上限（落ちた suite 1 件あたり）と、呼び名に使う名前の上限
NAME_CHARS = 100
COMMAND_CHARS = 500
OUTPUT_LINES = 100
OUTPUT_CHARS = 8_000
# ログの名前に入れる起動の識別値の文字数
RUN_CHARS = 12
# シェルがコマンドを起動できなかったときの終了コード（基準側と比べてから環境の問題かを決める）
ENV_CODES = frozenset({126, 127})
# 上限で止めた停止理由（drive の安全網も同じ）
LIMIT_KIND = "fix_limit"
_KEY_BEGIN = re.compile(r"-----BEGIN[^\n]*PRIVATE KEY")
_KEY_END = re.compile(r"-----END[^\n]*PRIVATE KEY")


@dataclass(frozen=True)
class LocalGateConfig:
    pr: int
    branch: str
    scope: tuple[str, ...]
    max_fix: int
    repo_work: Path
    root: Path
    total_seconds: float
    # suite 1 つの上限（`test.local_total_minutes` があれば None で、累計の残りだけを掛ける）
    suite_seconds: Optional[float]


@dataclass(frozen=True)
class Failed:
    """落ちた suite。`code` は終了コード（時間切れは None）。コマンドと出力は伏せる前の値で、メモリの中だけで持つ。"""

    index: int
    label: str
    code: Optional[int]
    command: str
    output: str


@dataclass
class TestBudget:
    total: float
    suite: Optional[float]
    used: float = 0.0


@dataclass(frozen=True)
class Exec:
    """テストの起動と時計（テストで差し替える）。"""

    run: Callable[..., subprocess.CompletedProcess[str]] = slots.run_command
    clock: Callable[[], float] = time.monotonic
    waited: Callable[[float], float] = slots.waited_since


# 最終ゲート修正の修正担当を起動する（指示文に載せる抜粋と周の番号を渡し、結果を出したかを返す）
FixLauncher = Callable[[Sequence[str], int], bool]
Save = Callable[[], None]


def listed(state: Mapping[str, Any], key: str) -> list[Any]:
    """状態の並びのキーの値。無いか並びでなければ空。"""
    held = state[key] if key in state else []
    return list(held) if isinstance(held, list) else []


def _str(mapping: Mapping[str, Any], key: str) -> str:
    return str(mapping[key]) if key in mapping else ""


def redact_output(text: str) -> str:
    """コマンド・出力・suite の名前の認証情報を伏せる。秘密鍵のブロックを原文で先に伏せ、全文と行ごとの伏せ字を掛ける。"""
    lines = text.split("\n")
    out: list[str] = []
    inside = False
    for line in lines:
        begin = None if inside else _KEY_BEGIN.search(line)
        if begin is not None:
            inside = not _KEY_END.search(line[begin.end() :])
            out.append(secret_lines.MASK)
            continue
        if inside:
            if _KEY_END.search(line):
                inside = False
            out.append(secret_lines.MASK)
            continue
        out.append(line)
    masked = str(run_log.redact("\n".join(out)))
    return "\n".join(secret_lines.redact(line) for line in masked.split("\n"))


def suite_label(index: int, name: str) -> str:
    """NDF の外や記録へ出す suite の呼び名（`#<設定の順番> <名前の先頭 100 文字>`。名前が伏せ字に当たれば順番だけ）。"""
    short = name[:NAME_CHARS]
    if not short or redact_output(short) != short:
        return f"#{index}"
    return f"#{index} {short}"


def _failure_text(f: Failed) -> str:
    return "時間切れ" if f.code is None else f"終了コード {f.code}"


def fix_excerpt(failed: Sequence[Failed]) -> list[str]:
    """指示文に載せる抜粋（1 件 1 文字列）。全文を伏せてから上限で切る。"""
    out: list[str] = []
    for f in failed:
        command = redact_output(f.command)[:COMMAND_CHARS]
        tail = "\n".join(redact_output(f.output).split("\n")[-OUTPUT_LINES:])[-OUTPUT_CHARS:]
        out.append(f"- {f.label}（{_failure_text(f)}）\n  コマンド: {command}\n  出力の末尾:\n{tail}")
    return out


def settings_at(g: git.Git, commit: str, repo_work: Path) -> dict[str, Any]:
    """そのコミットのルートの `.ndf/project.json`。コミットに無ければ `--work` の設定。読めなければ `precondition`。"""
    rel = f"{identity.FIXED_WORDS['ndf_settings_dir']}/{declared.PROJECT_FILE}"
    if g.run("ls-tree", "--name-only", commit, "--", rel).strip():
        text, where = g.run("show", f"{commit}:{rel}"), f"{commit} の {rel}"
        try:
            data: Any = json.loads(text)
        except ValueError as exc:
            raise final_gate.GateStopped("precondition", f"設定が JSON でない（{where}）: {exc}") from None
    else:
        try:
            found = declared.read_ndf_settings(repo_work)
        except declared.NdfSettingsError as exc:
            raise final_gate.GateStopped("precondition", f"設定を読めない: {exc}") from None
        data = found[1] if found is not None else {}
    return data if isinstance(data, dict) else {}


class _LinkTree(Mapping[str, Optional[str]]):
    """コミットのツリー（パス → リンク先。リンクでなければ None）。

    リンク先の blob は、保護の判定が名指しのパスの解決で引いたリンクの分だけ読み、読んだものは覚える。
    """

    def __init__(self, g: git.Git, commit: str) -> None:
        self._g = g
        self._links: dict[str, str] = {}
        self._paths: set[str] = set()
        self._read: dict[str, str] = {}
        for row in g.run("ls-tree", "-r", "-z", commit).split("\0"):
            if not row:
                continue
            meta, _, path = row.partition("\t")
            mode, _, sha = meta.split(" ")
            self._paths.add(path)
            if mode == "120000":
                self._links[path] = sha

    def __getitem__(self, path: str) -> Optional[str]:
        if path not in self._paths:
            raise KeyError(path)
        if path not in self._links:
            return None
        if path not in self._read:
            self._read[path] = self._g.run("cat-file", "blob", self._links[path])
        return self._read[path]

    def __contains__(self, path: object) -> bool:
        return path in self._paths

    def __iter__(self) -> Iterator[str]:
        return iter(self._paths)

    def __len__(self) -> int:
        return len(self._paths)


# 同じコミットのツリーを、最終ゲートの周をまたいで使い回す（コミットの SHA が同じならツリーも同じ）
Trees = dict[str, _LinkTree]


def _tree_links(g: git.Git, commit: str, trees: Optional[Trees] = None) -> _LinkTree:
    """コミットのツリー。`trees` があれば、同じ SHA のツリーを読み直さずに使う。"""
    if trees is None:
        return _LinkTree(g, commit)
    if commit not in trees:
        trees[commit] = _LinkTree(g, commit)
    return trees[commit]


def _search_path(environ: Mapping[str, str]) -> list[str]:
    """`PATH` の相対の要素（空と `.` はルート）。"""
    return [e for e in _str(environ, "PATH").split(os.pathsep) if not e.startswith("/")]


def protection_at(
    g: git.Git, commit: str, project: Mapping[str, Any], repo_work: Path, trees: Optional[Trees] = None
) -> protected_decide.Protected:
    """そのコミットのツリーと設定から、保護するファイルの判定を組む。コマンドが `--work` の配下を絶対パスで指せば `precondition`。

    `trees` を渡せば、同じコミットのツリーを周をまたいで使い回す。
    """
    found = means.decide(project)
    roots = [str(repo_work.absolute()), os.path.realpath(repo_work)]
    for i, suite in enumerate(found.declarations, 1):
        for key in ("command", "scope_command"):
            text = suite[key] if key in suite else None
            if isinstance(text, str) and protected_decide.inside_work([text], roots):
                label = suite_label(i, _str(suite, "name"))
                detail = (
                    f"テストの suite {label} の {key} が、呼び出し元の worktree の配下を絶対パスで指す。"
                    "検証用の作業場所ではないコードを確かめることになるので打たない。リポジトリの中のファイルは相対パスで指す"
                )
                raise final_gate.GateStopped("precondition", detail)
    return protected_decide.protected_of(_tree_links(g, commit, trees), found.commands, found.protected, _search_path(os.environ))


def _ndf_lock(repo: git.Git, path: Path) -> Optional[str]:
    """登録のある作業場所の lock の理由（lock が無ければ空の文字列）。登録が無ければ None。"""
    target = path.absolute()
    for entry in repo.worktrees():
        if Path(entry.path).absolute() == target or Path(entry.path).resolve() == path.resolve():
            got = worktree.lock_of(repo, path)
            return "" if got is None else got
    return None


def _remove(repo: git.Git, path: Path) -> Optional[str]:
    """NDF が作った検証用の作業場所を取り除く。残したら理由を返す。"""
    held = _ndf_lock(repo, path)
    if held is None:
        return None if not path.exists() else f"{path}: git の作業場所として登録が無いので残した"
    if held and not held.startswith(worktree.LOCK_PREFIX):
        return f"{path}: 利用者の lock なので残した（理由: {held}）"
    try:
        if held:
            repo.run("worktree", "unlock", str(path))
        repo.run("worktree", "remove", "--force", str(path))
    except git.GitError as exc:
        return f"{path}: 取り除けない: {exc}"
    return None


@contextlib.contextmanager
def verify_tree(repo: git.Git, root: Path, pr: int, commit: str, state: dict[str, Any], save: Save) -> Generator[Path]:
    """検証用の作業場所 `rf<PR>/verify-<ランダム>` を `commit` から作り、打ち終えたら取り除く。作る前にパスを記録する。"""
    path = root / f"verify-{uuid.uuid4().hex[:12]}"
    state["verify_trees"] = [*listed(state, "verify_trees"), str(path)]
    save()
    try:
        repo.run("worktree", "add", "--detach", str(path), commit)
        worktree.lock_review_worktree(repo, path, launch_decide.SKILL, pr)
    except (git.GitError, worktree.LockFailed) as exc:
        raise final_gate.GateStopped("precondition", f"検証用の作業場所を作れない: {exc}") from None
    try:
        yield path
    finally:
        kept = _remove(repo, path)
        if kept is not None:
            print(f"⚠ 検証用の作業場所を残した（次の実行か release が片付ける）: {kept}", file=sys.stderr)
        else:
            state["verify_trees"] = [p for p in listed(state, "verify_trees") if p != str(path)]
            save()


def sweep_verify_trees(repo: git.Git, state: dict[str, Any], save: Save) -> list[str]:
    """中断で残った検証用の作業場所（状態の `verify_trees` に記録のあるもの）を取り除き、残した理由を返す。ロックを持って呼ぶ。"""
    kept: list[str] = []
    left: list[str] = []
    for raw in listed(state, "verify_trees"):
        reason = _remove(repo, Path(raw))
        if reason is not None:
            kept.append(reason)
            left.append(raw)
    if left != listed(state, "verify_trees"):
        state["verify_trees"] = left
        save()
    return kept


def _stop_limit(budget: TestBudget) -> final_gate.GateStopped:
    detail = (
        f"テスト全体の実行の累計 {budget.used:.0f} 秒が上限 {budget.total:.0f} 秒に達した。打ち直すと累計は 0 から数える。"
        "1 周がいつも上限より長ければ、設定 test.local_total_minutes に上限（分）を書いてから打ち直す"
    )
    return final_gate.GateStopped(LIMIT_KIND, detail)


# suite の順番と伏せ字にしたログの中身を受けて書く（`_Gate.writer`）
LogWriter = Callable[[int, str], None]


def _run_one(tree: Path, found: means.Means, command: str, timeout: float, ex: Exec) -> tuple[Optional[int], str]:
    """suite 1 つを打ち、(終了コード（時間切れは None）, 出力) を返す。起動できなければ `OSError` を送る。"""
    try:
        proc = ex.run(command, tree, timeout, suites=list(found.declarations))
    except subprocess.TimeoutExpired as exc:
        return None, _text(exc.output) + _text(exc.stderr)
    return proc.returncode, _text(proc.stdout) + _text(proc.stderr)


def run_suites(tree: Path, found: means.Means, budget: TestBudget, log: LogWriter, ex: Exec = Exec()) -> tuple[Failed, ...]:
    """テストの suite の `command` を設定の順にすべて打ち、落ちた suite を返す。出力は伏せ字にして `log` へ渡す。"""
    failed: list[Failed] = []
    before = git.Git(tree).rev_parse("HEAD")
    for i, (name, command) in enumerate(found.suites, 1):
        shown = suite_label(i, name)
        remaining = budget.total - budget.used
        if remaining <= 0:
            raise _stop_limit(budget)
        timeout = remaining if budget.suite is None else min(budget.suite, remaining)
        began = ex.clock()
        try:
            code, output = _run_one(tree, found, command, timeout, ex)
        except OSError as exc:
            raise final_gate.GateStopped("precondition", f"テストの suite {shown} を起動できない（{type(exc).__name__}）") from None
        budget.used += max(0.0, ex.clock() - began - ex.waited(began))
        log(i, redact_output(f"{shown}\n$ {command}\n{output}"))
        if code is None and (budget.suite is None or budget.suite >= remaining):
            raise _stop_limit(budget)
        if code != 0:
            failed.append(Failed(i, shown, code, command, output))
    _check_untouched(git.Git(tree), before)
    return tuple(failed)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def _check_untouched(g: git.Git, commit: str) -> None:
    """テストの後に `HEAD` が対象のコミットのままで、追跡ファイルに変更が無いことを確かめる。"""
    head = g.rev_parse("HEAD")
    changed = [line[3:] for line in g.run("status", "--porcelain", "--untracked-files=no").splitlines() if line]
    if head != commit or changed:
        what = ", ".join(changed) if changed else f"HEAD が {commit} から {head} へ動いた"
        raise final_gate.GateStopped("precondition", f"テストが追跡ファイルを書き換えたか HEAD を動かした: {what}")


def path_digest(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return hashlib.sha256(_str(env, "PATH").encode("utf-8")).hexdigest()


def _rows(failed: Sequence[Failed]) -> list[dict[str, Any]]:
    return [{"suite": f.label, "code": f.code} for f in failed]


@dataclass
class _Gate:
    """1 回の起動の手元の最終ゲート。"""

    g: git.Git
    cfg: LocalGateConfig
    state: dict[str, Any]
    save: Save
    ex: Exec
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    pd: str = field(default_factory=path_digest)
    trees: Trees = field(default_factory=dict)
    budget: TestBudget = field(init=False)

    def __post_init__(self) -> None:
        self.budget = TestBudget(self.cfg.total_seconds, self.cfg.suite_seconds)

    def key(self, sha: str, digest: str) -> dict[str, str]:
        return {"sha": sha, "digest": digest, "path_digest": self.pd}

    def _match(self, row: Mapping[str, Any], sha: str, digest: str) -> bool:
        return all(k in row and row[k] == v for k, v in self.key(sha, digest).items())

    def passed(self, sha: str, digest: str, this_run: bool = False) -> bool:
        return any(
            self._match(r, sha, digest) and (not this_run or _str(r, "run") == self.run_id) for r in listed(self.state, "local_passed")
        )

    def record_pass(self, sha: str, digest: str) -> None:
        self.state["local_passed"] = [*listed(self.state, "local_passed"), {**self.key(sha, digest), "run": self.run_id}]
        self.save()

    def writer(self, label: str) -> LogWriter:
        """ログ `local-gate-<起動>-<周>-<対象>-<順番>.log` を書く関数。`<起動>` は起動ごとのランダムな値で、前の起動のログと重ならない。"""
        logs = self.cfg.root / "logs"
        run = self.run_id[:RUN_CHARS]

        def write(index: int, text: str) -> None:
            workspace.write_private_log(logs, f"{workspace.GATE_LOG_PREFIX}{run}-{label}-{index}.log", text, self.state, self.save)

        return write

    def test(self, sha: str, found: means.Means, label: str) -> tuple[Failed, ...]:
        with verify_tree(self.g, self.cfg.root, self.cfg.pr, sha, self.state, self.save) as tree:
            return run_suites(tree, found, self.budget, self.writer(label), self.ex)

    def baseline(self, tip: str, found: means.Means, env_only: bool, round_no: int) -> dict[int, Optional[int]]:
        """基準側（取得した先頭だけ）で落ちた suite の順番 → 終了コード。記録があれば使い、無ければ打って記録する。"""
        if self.passed(tip, found.digest, this_run=env_only):
            return {}
        for r in listed(self.state, "local_base_failed"):
            if self._match(r, tip, found.digest) and _str(r, "run") == self.run_id:
                return {int(x["index"]): x["code"] for x in r["failed"]}
        failed = self.test(tip, found, f"{round_no}-base")
        if not failed:
            self.record_pass(tip, found.digest)
            return {}
        rows = [{"index": f.index, "suite": f.label, "code": f.code} for f in failed]
        self.state["local_base_failed"] = [
            *listed(self.state, "local_base_failed"),
            {**self.key(tip, found.digest), "failed": rows, "run": self.run_id},
        ]
        self.save()
        return {f.index: f.code for f in failed}


def _record(status: str, round_no: int, sha: str, tests: str = "", digest: str = "", **more: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {"status": status, "rounds": round_no, "head": sha, "means": means.Kind.LOCAL_TEST.value}
    if tests:
        rec["tests"] = tests
    if digest:
        rec["digest"] = digest
    return {**rec, **more}


def _sent(state: Mapping[str, Any]) -> set[str]:
    """この実行が送った SHA（push の直前の記録と、テストを打って完了した記録）。"""
    out: set[str] = set()
    if "pushing" in state and isinstance(state["pushing"], Mapping):
        out.add(str(state["pushing"]["sha"]))
    done = state["done"] if "done" in state else None
    if isinstance(done, Mapping) and _str(done, "tests") == "passed" and "head" in done:
        out.add(str(done["head"]))
    return out


def _take_in(gate: _Gate, tip: str) -> str:
    """取得した先頭 `tip` へ合わせ、`HEAD` を返す。祖先なら触れない。載せ直しは `rebasing` を残してから行う。"""
    g, state = gate.g, gate.state
    head = g.rev_parse("HEAD")
    if g.is_ancestor(tip, head):
        state["onto"] = tip
        gate.save()
        return head
    state["rebasing"] = {"head": head, "tip": tip}
    gate.save()
    synced = worktree.sync_fetched(g, tip)
    state.pop("rebasing", None)
    if synced.conflicts:
        gate.save()
        raise final_gate.GateStopped("base_conflict", worktree.conflict_detail(synced))
    state["onto"] = tip
    gate.save()
    return g.rev_parse("HEAD")


def _resend_sent(gate: _Gate, tip: str, found: means.Means, round_no: int) -> dict[str, Any]:
    """送るコミットが無い周。この実行が送った SHA なら確かめ直し、ほかはテストを打たずに完了する。"""
    if tip not in _sent(gate.state):
        return _record("passed", round_no, tip, "skipped")
    if not gate.passed(tip, found.digest):
        failed = gate.test(tip, found, f"{round_no}-head")
        if failed:
            detail = "送った後のコミットのテスト全体が落ちた（送った後なので直さない）: " + ", ".join(
                f"{f.label}（{_failure_text(f)}）" for f in failed
            )
            raise final_gate.GateStopped("test_failed", detail, _record("test_failed", round_no, tip, head_failed=_rows(failed)))
        gate.record_pass(tip, found.digest)
    return _record("passed", round_no, tip, "passed", found.digest)


def _push_tested(gate: _Gate, sha: str, digest: str, remote: str) -> bool:
    gate.state["pushing"] = gate.key(sha, digest)
    gate.save()
    done = gate.g.attempt("push", "-q", "--", remote, f"{sha}:refs/heads/{gate.cfg.branch}")
    if done.returncode == 0:
        return True
    gate.state.pop("pushing", None)
    gate.save()
    return False


def _compare(gate: _Gate, tip: str, head: str, found: means.Means, failed: Sequence[Failed], round_no: int) -> list[Failed]:
    """落ちた周を基準側と比べ、直させる suite（変更後だけで落ちた suite）を返す。直さないなら止める。"""
    env = any(f.code in ENV_CODES for f in failed)
    last = round_no >= gate.cfg.max_fix

    def stop(kind: str, detail: str, base: Sequence[Mapping[str, Any]] = ()) -> final_gate.GateStopped:
        return final_gate.GateStopped(kind, detail, _record(kind, round_no, head, base_failed=list(base), head_failed=_rows(failed)))

    names = ", ".join(f"{f.label}（{_failure_text(f)}）" for f in failed)
    if last and not env:
        raise stop("test_failed", f"最終ゲートのテスト全体が直しの上限でも落ちる（送っていない）: {names}")
    base = gate.baseline(tip, found, env, round_no)
    base_rows = [{"suite": suite_label(i, found.suites[i - 1][0]), "code": c} for i, c in sorted(base.items())]
    hit = [f for f in failed if f.code in ENV_CODES and f.index in base and base[f.index] in ENV_CODES]
    if hit:
        what = ", ".join(f"{f.label}（{_failure_text(f)}）" for f in hit)
        raise stop("precondition", f"取得した先頭だけのテストでも同じ suite が起動できない。コマンドか環境を直す: {what}", base_rows)
    own = [f for f in failed if f.index not in base]
    if not own:
        both = ", ".join(f"{r['suite']}（{r['code']}）" for r in base_rows)
        detail = (
            f"変更後に落ちた suite（{names}）が、すべて取得した先頭 {tip} でも落ちる（{both}）。同じ suite が変更の前から"
            "落ちるので、この実行の変更が新しい失敗を足したかを NDF は確かめていない（suite の単位では原因を見分けていない）"
        )
        raise stop("test_failed", detail, base_rows)
    if last:
        raise stop("test_failed", f"最終ゲートのテスト全体が直しの上限でも落ちる（送っていない）: {names}", base_rows)
    return own


def run_local_gate(
    g: git.Git, cfg: LocalGateConfig, state: dict[str, Any], save: Save, fix: FixLauncher, ex: Exec = Exec()
) -> dict[str, Any]:
    """取得と載せ直し → テスト → push か直し、を周の上限まで回し、最終ゲートの記録を返す。止まれば `final_gate.GateStopped`。"""
    gate = _Gate(g, cfg, state, save, ex)
    _check_pending_fix(gate)
    remote = g.remote()
    tip: Optional[str] = None
    for round_no in range(cfg.max_fix + 1):
        worktree.refuse_dirty(g)
        if tip is None:
            tip = worktree.fetch_tips(g, cfg.branch, remote, None)[0]
        head = _take_in(gate, tip)
        project = settings_at(g, tip, cfg.repo_work)
        found = means.decide(project)
        if found.kind != means.Kind.LOCAL_TEST:
            raise final_gate.GateStopped("precondition", f"取得した先頭 {tip} の設定では、テストを確かめる手段が手元のテストでない")
        protected = protection_at(g, tip, project, cfg.repo_work, gate.trees)
        if head == tip:
            return _resend_sent(gate, tip, found, round_no)
        changed = paths_changed_between(g, tip, head)
        hits = sorted(p for p in changed if protected.covers(p))
        if hits:
            detail = f"この実行のコミットがテストの定義のファイルを変えたので送らない: {', '.join(hits)}"
            raise final_gate.GateStopped("refused", detail, _record("refused", round_no, head, protected_files=hits))
        known = gate.passed(head, found.digest)
        failed = () if known else gate.test(head, found, f"{round_no}-head")
        if not failed:
            if not known:
                gate.record_pass(head, found.digest)
            if _push_tested(gate, head, found.digest, remote):
                return _record("passed", round_no, head, "passed", found.digest)
            again = worktree.fetch_tips(g, cfg.branch, remote, None)[0]
            if again == tip or round_no >= cfg.max_fix:
                raise final_gate.GateStopped(
                    "push_rejected", f"PR のブランチ {cfg.branch} へ送れない（ブランチが進んでいないか、取得し直す周が残っていない）",
                    _record("push_rejected", round_no, head),
                )  # fmt: skip
            tip = again
            continue
        own = _compare(gate, tip, head, found, failed, round_no)
        labels = [f.label for f in own]
        # 担当のコミットの後・範囲の検査の前に中断しても、再開で検査できるよう直す前の SHA を残す
        state["fixing"] = {"before": head, "tip": tip, "labels": labels}
        save()
        stopped = _record("work_stopped", round_no, head)
        _checked_fix(
            gate, partial(final_gate.fix_or_undo, g, head, partial(fix, fix_excerpt(own), round_no + 1), stopped), head, labels, protected
        )
        tip = None
    raise AssertionError("最終ゲートの回数の上限の後に戻った")


def _checked_fix(
    gate: _Gate, launch: Callable[[], None], before: str, labels: Sequence[str], protected: protected_decide.Protected
) -> None:
    """修正担当を起動し（`launch`）、直しの範囲を検査してから `fixing` を消す。止まれば消してから止める。

    担当のコミットの後・検査の前に中断すれば `fixing` が残り、再開の頭の `_check_pending_fix` が同じ検査をする。
    """
    try:
        launch()
        final_gate.check_fix(gate.g, before, labels, gate.cfg.scope, protected.covers)
    except final_gate.GateStopped:
        gate.state.pop("fixing", None)
        gate.save()
        raise
    gate.state.pop("fixing", None)
    gate.save()


def _check_pending_fix(gate: _Gate) -> None:
    """前の起動が最終ゲート修正の範囲の検査の前に中断していれば、その直しの範囲を検査する（範囲外なら戻して `refused`）。"""
    pending = gate.state["fixing"] if "fixing" in gate.state else None
    if not isinstance(pending, Mapping):
        return
    tip = str(pending["tip"])
    protected = protection_at(gate.g, tip, settings_at(gate.g, tip, gate.cfg.repo_work), gate.cfg.repo_work, gate.trees)
    _checked_fix(gate, lambda: None, str(pending["before"]), [str(x) for x in pending["labels"]], protected)


def suite_seconds(project: Mapping[str, Any]) -> Optional[float]:
    """suite 1 つの上限。`test.local_total_minutes` があれば None（累計の残りだけを掛ける）。"""
    return None if means.total_minutes(project) is not None else limits.seconds(verify.RUN_POLICY)


def total_seconds(project: Mapping[str, Any], safety_net_minutes: float) -> float:
    """テスト全体の実行の累計の上限（`test.local_total_minutes`、無ければ安全網の上限）。"""
    given = means.total_minutes(project)
    return (given if given is not None else safety_net_minutes) * 60


def tracked_files(g: git.Git, commit: str) -> list[str]:
    """コミットのツリーの追跡ファイル。"""
    return [p for p in g.run("ls-tree", "-r", "--name-only", "-z", commit).split("\0") if p]


# 再開で作業コミットが新しい保護に当たったときの復旧の手順
RECOVER = (
    "復旧の手順: (1) ndf refactor release --pr {pr} で作業場所と状態を片付けてから同じコマンドを打ち直す（採った項目と作業コミットを捨て、"
    "新しい保護の範囲で最初からやり直す）。(2) 広げた test.protected_files の指定を戻して打ち直す（採った項目と作業コミットを残して続ける）"
)
# `--pushed-out` に置き換えてよい既存のファイルの中身（SHA 1 行）
_SHA_LINE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\n?")


def write_pushed(out: Path, head: str) -> None:
    """`--pushed-out` へ SHA を 1 行で書く。リンク・ディレクトリ・SHA 1 行でない既存のファイルなら書かずに `precondition`。

    同じディレクトリの一時ファイルから名前を置き換えるので、同じファイルへのハードリンクの中身は変わらない。
    """
    if out.is_symlink() or (out.exists() and not out.is_file()):
        raise final_gate.GateStopped("precondition", f"--pushed-out の出力先がシンボリックリンクかディレクトリなので書かない: {out}")
    if out.exists() and not _SHA_LINE.fullmatch(out.read_text(encoding="utf-8", errors="replace")):
        raise final_gate.GateStopped("precondition", f"--pushed-out の出力先が SHA 1 行でない既存のファイルなので書かない: {out}")
    try:
        fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(head + "\n")
        os.replace(tmp, out)
    except OSError as exc:
        raise final_gate.GateStopped("precondition", f"--pushed-out の出力先に書けない: {out}: {exc}") from None


def follow_pushed(g: git.Git, pushed: Path, remote: str, branch: str) -> str:
    """呼び出し元の作業場所を、手元の最終ゲートが送った SHA（`--pushed-out` のファイル）へ早送りし、その SHA を返す。

    すでにその SHA なら何もしない。SHA のコミットが手元にあれば取得せずに早送りし、無いときだけ PR のブランチを取得する。
    ファイルが無いか空、早送りできない（先へ進んでいる・無視したファイルを上書きする）なら `precondition`。
    """
    sha = pushed.read_text(encoding="utf-8").strip() if pushed.is_file() else ""
    if not sha:
        raise final_gate.GateStopped("precondition", f"送った SHA のファイルが無いか空: {pushed}")
    if g.rev_parse("HEAD") == sha:
        return sha
    if g.attempt("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        g.run("fetch", "-q", "--", remote, f"refs/heads/{branch}")
    done = g.attempt("merge", "-q", "--ff-only", "--no-overwrite-ignore", sha)
    if done.returncode != 0 or g.rev_parse("HEAD") != sha:
        detail = f"呼び出し元の作業場所を送った SHA {sha} へ早送りできない（先へ進んでいるか、無視したファイルを上書きする）"
        raise final_gate.GateStopped("precondition", detail)
    return sha


def paths_changed_between(g: git.Git, base: str, head: str) -> list[str]:
    """`base` から `head` までに変わったパス（改名を検出しないので、削除したパスと改名前のパスも含む）。"""
    return [p for p in g.run("diff-tree", "-r", "--name-only", "-z", base, head).split("\0") if p]
