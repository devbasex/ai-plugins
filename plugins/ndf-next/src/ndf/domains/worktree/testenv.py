"""worktree ごとのテスト環境（宣言の `testenv` 節。起動の定義は `localenv.compose_files`）。

- `assign`: 環境名・スロット・ポートを決めて台帳（`ledger`）へ記録する。スロットが尽きたら回収（`reclaim`）を 1 回挟む
- `reclaim`: worktree の消えた割り当てのテスト環境を破棄して解放する（判定は `ndf.testing.testenv`）
- `tag`: 基準のタグ（宣言の `golden_tag_paths` の内容の要約）。同じ内容なら同じ値で、焼き直しが要るかを内容で決める
- `bake`: 基準のボリューム（宣言の `golden_volumes`）をタグ付きで作る。足りないものだけを作る
- `up`・`stop`・`down`: compose で起動・停止・破棄する。破棄に失敗したらスロットを解放しない
- `run_test`: 宣言の `test_kinds.<種類>.run` を、割り当ての値（エントリポイントの URL・証跡の置き場）を渡して打つ。終了コードはそのまま返す。
  種類に `service` があれば、そのコンテナが worktree を見ているかを先に確かめる（`reach`）
- `compose_env`: 割り当て済みのテスト環境をコンテナへ渡す値（割り当てを新しく取らない）

宣言・`testenv` 節が無いリポジトリでは `target` が `None` を返し、どれも打たない。
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from filelock import FileLock, Timeout

from ndf.adapters.git import Git
from ndf.domains.worktree import container, declaration, deps, ledger, localenv, outcomes, repo, testenv_decide
from ndf.testing import testenv as placement

SECTION = declaration.TESTENV
# 証跡の置き場（worktree の下。共通の git ディレクトリの info/exclude で追跡から外す）
EVIDENCE = ".ndf-evidence"
# 同じテスト環境の実行の目印を待つ秒
INUSE_WAIT = 5


class TestenvError(Exception):
    """処理できない（宣言の値が足りない・実行系が無い・台帳に書けない・compose が失敗した）。"""


@dataclass(frozen=True)
class Target:
    """対象の worktree と、宣言の `testenv`・`localenv` 節・台帳・起動の手段。"""

    path: Path
    main_dir: Path
    section: dict[str, Any]
    compose_files: tuple[str, ...]
    shell: container.Shell
    book: ledger.Ledger

    @property
    def key(self) -> str:
        return str(self.path)

    def contains(self, real: str) -> bool:
        """実体のパス `real` が worktree の配下か（worktree の根そのものは含まない）。"""
        return real.startswith(self.key + os.sep)

    def at(self, *keys: str) -> Any:
        """節の入れ子の値（無ければ `None`）。"""
        value: Any = self.section
        for k in keys:
            value = value.get(k) if isinstance(value, dict) else None
        return value


def target(path: Path, shell: Optional[container.Shell] = None) -> Optional[Target]:
    """対象（`path` が worktree の下のディレクトリでも、worktree の根）。宣言・節が無ければ `None`。"""
    loc = repo.locate(Git(path))
    main_dir = loc.main_dir
    decl = declaration.load(main_dir)
    section = decl.section(SECTION) if decl is not None else None
    if decl is None or section is None:
        return None
    return Target(loc.top, main_dir, section, localenv.compose_files(decl),
                  shell if shell is not None else container.Shell.from_env(), ledger.Ledger.of(Git(main_dir)))  # fmt: skip


# --- 割り当て ------------------------------------------------------------------


def environment_name(t: Target, branch: Optional[str]) -> str:
    """環境名。`branch` は今のブランチ（detached HEAD なら `None`）。"""
    if branch is not None:
        return testenv_decide.env_name(t.main_dir.name, branch)
    return testenv_decide.env_name(t.main_dir.name, f"detached-{t.path.name}", t.key)


def assign(t: Target) -> dict[str, Any]:
    """今の割り当て（無ければ取る）。空きが無い・ポートを決められなければ `TestenvError`。"""
    branch = repo.current_branch(Git(t.path))
    name = environment_name(t, branch)
    recorded = branch if branch is not None else ""
    had = t.book.current(t.key) is not None
    row = t.book.acquire(t.key, recorded, name)
    if row is None:
        reclaim(t, limit=1)
        row = t.book.acquire(t.key, recorded, name)
    if row is None:
        raise TestenvError(f"空きスロットが無い（上限 {testenv_decide.SLOTS}）")
    roles = t.at("port_roles")
    try:
        ports = testenv_decide.ports(t.at("port_band"), row["slot"], roles if isinstance(roles, dict) else {})
    except testenv_decide.PortsError as exc:
        if not had:
            t.book.update(t.key, _release)
        raise TestenvError(str(exc)) from exc
    t.book.update(t.key, lambda r: r.update(ports=ports))
    return {**row, "ports": ports}


def inuse_lock(t: Target, environment: str) -> Path:
    """テストの実行中に握る目印（reap は握られた環境を止めない）。"""
    return t.book.path.parent / f"{environment}.inuse.lock"


def _release(row: dict[str, Any]) -> None:
    row["released_at"] = ledger.now()


def _touch(row: dict[str, Any]) -> None:
    row["last_used_at"] = ledger.now()


# --- compose -------------------------------------------------------------------


def compose_file_args(t: Target) -> list[str]:
    """`docker compose` へ渡す `-f <絶対パス>` の並び。worktree の外（字面・symlink・実体）を指せば `TestenvError`。"""
    out: list[str] = []
    for rel in t.compose_files:
        if not deps.safe_relative(rel):
            raise TestenvError(f"compose_files の {rel} は worktree の外を指す")
        full = t.path / rel
        if full.is_symlink():
            raise TestenvError(f"compose_files の {rel} は symlink（たどらない）")
        real = os.path.realpath(full)
        if not t.contains(real):
            raise TestenvError(f"compose_files の {rel} の実体が worktree の外（{real}）にある")
        out += ["-f", real]
    return out


def compose_values(t: Target, row: dict[str, Any]) -> dict[str, str]:
    """`row` の環境を起動したときと同じ、compose へ渡す値（worktree は行が持つもの）。"""
    network = t.at("shared_network")
    ports = row.get("ports")
    worktree = str(row["worktree"]) if row.get("worktree") else t.key
    return testenv_decide.compose_values(row["environment"], row["slot"], worktree, network if isinstance(network, str) else "",
                                         ports if isinstance(ports, dict) else {})  # fmt: skip


def _row_files(t: Target, base: Path) -> list[str]:
    """ほかの worktree の行の環境へ渡す `-f <絶対パス>` の並び。`base` の外を指すもの・無いものは渡さない。"""
    out: list[str] = []
    root = os.path.realpath(base)
    for rel in t.compose_files:
        full = base / rel
        real = os.path.realpath(full)
        if deps.safe_relative(rel) and not full.is_symlink() and full.is_file() and real.startswith(root + os.sep):
            out += ["-f", real]
    return out


def compose_row(t: Target, row: dict[str, Any], *args: str) -> int:
    """台帳の行の環境へ `docker compose` を打つ（見回り・回収が使う）。定義・値・作業場所は起動したときに揃える。

    worktree が残っていればそこの定義を、消えていればメインディレクトリの同じ定義を渡す（プロセスの現在地から探させない）。
    """
    recorded = row.get("worktree")
    base = Path(recorded) if isinstance(recorded, str) and recorded and Path(recorded).is_dir() else t.main_dir
    files = _row_files(t, base)
    return t.shell.docker_run("compose", "-p", row["environment"], *files, *args, env=compose_values(t, row), cwd=base).returncode


def compose(t: Target, row: dict[str, Any], *args: str) -> int:
    """`docker compose -p <環境名> -f ... <args>` を打ち、終了コードを返す。定義が 1 件も無ければ打たずに 2。"""
    files = compose_file_args(t)
    if not files:
        return 2
    return t.shell.docker_run("compose", "-p", row["environment"], *files, *args, env=compose_values(t, row), cwd=t.path).returncode


def compose_env(t: Target) -> dict[str, str]:
    """割り当て済みのテスト環境をコンテナへ渡す値。割り当てが無ければ空（新しく取らない）。"""
    row = t.book.current(t.key)
    if row is None:
        return {}
    out = compose_values(t, row)
    out["COMPOSE_PROJECT_NAME"] = row["environment"]
    files = compose_file_args(t)[1::2]
    if files:
        out["COMPOSE_FILE"] = ":".join(files)
    return out


# --- 基準 ----------------------------------------------------------------------


def tag(t: Target) -> Optional[str]:
    """基準のタグ。宣言に `golden_tag_paths` が無ければ `None`。指す対象が無ければ `TestenvError`。"""
    paths = t.at("golden_tag_paths")
    names = [p for p in paths if isinstance(p, str) and p] if isinstance(paths, list) else []
    if not names:
        return None
    listing = Git(t.path).run("ls-tree", "-r", "HEAD", "--", *names)
    if not listing:
        raise TestenvError(f"golden_tag_paths が指す対象が無い: {' '.join(names)}")
    return hashlib.sha1(listing.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def bake(t: Target, value: str) -> tuple[int, int]:
    """基準のボリュームを作り、(作った数, 既にあった数) を返す。失敗は `TestenvError`（作りかけは消す）。"""
    if not t.shell.has_docker():
        raise TestenvError("コンテナの実行系が見つからない")
    volumes = t.at("golden_volumes")
    created = existing = 0
    for source, golden in volumes.items() if isinstance(volumes, dict) else []:
        name = f"{golden}-{value}"
        if t.shell.docker_run("volume", "inspect", name).returncode == 0:
            existing += 1
            continue
        if t.shell.docker_run("volume", "create", name).returncode != 0:
            raise TestenvError(f"基準のボリュームを作れない: {name}")
        copied = t.shell.docker_run(
            "run", "--rm", "-v", f"{source}:/from:ro", "-v", f"{name}:/to", "alpine", "sh", "-c", "cd /from && cp -a . /to"
        )
        if copied.returncode != 0:
            t.shell.docker_run("volume", "rm", name)
            raise TestenvError(f"基準を作れない: {name}")
        created += 1
    return created, existing


# --- 起動・停止・破棄 ----------------------------------------------------------


def _tag_to_record(t: Target, row: dict[str, Any], value: Optional[str]) -> Optional[str]:
    """起動が通ったら台帳へ書く基準のタグ。ボリュームが残っているかを確かめられないときは書かない（古いデータのまま公開させない）。"""
    if value is None:
        return None
    if not row.get("golden_tag"):
        volumes = t.shell.docker_run("volume", "ls", "-q", "--filter", f"label={container.PROJECT_LABEL}={row['environment']}")
        if volumes.returncode != 0 or volumes.stdout.strip():
            return None
    return value


def _record_tag(t: Target, row: dict[str, Any], value: Optional[str]) -> None:
    """起動が通った後に、載せた基準のタグを台帳へ書く。"""
    if value is None:
        return
    if not t.book.update(t.key, lambda r: r.update(golden_tag=value)):
        raise TestenvError(f"基準のタグを台帳へ記録できない。起動していない: {row['environment']}")


def up(t: Target, profile: Optional[str] = None, value: Optional[str] = None, wait: tuple[str, ...] = ()) -> int:
    """起動する。基準のタグは起動が通ってから台帳へ書く（失敗した起動のタグで公開させない）。"""
    if not t.shell.has_docker():
        raise TestenvError("コンテナの実行系が見つからない")
    row = assign(t)
    recorded = _tag_to_record(t, row, value if value is not None else tag(t))
    services = t.at("profiles", profile) if profile else None
    names = [s for s in services if isinstance(s, str)] if isinstance(services, list) else []
    t.book.update(t.key, _touch)
    if recorded is not None and row.get("golden_tag") != recorded:
        # 載せ替える間は前の基準のタグを公開の判定に使わせない
        t.book.update(t.key, lambda r: r.pop("golden_tag", None))
    rc = compose(t, row, "up", "-d", *names)
    if rc == 0 and wait:
        # 宣言のサービスと依存だけを待つ（テストに関係しないサービスの不調で止まらない）
        rc = compose(t, row, "up", "-d", "--wait", *wait)
    if rc == 0:
        _record_tag(t, row, recorded)
    return rc


def stop(t: Target) -> int:
    row = t.book.current(t.key)
    if row is None or not t.shell.has_docker():
        return 0
    return compose(t, row, "stop")


def _down_args(t: Target, volumes: bool = False) -> list[str]:
    """破棄の `compose` の引数。ボリュームを消すのは求められたときか、宣言の `discard_volumes` が許すときだけ（消すと戻せない）。"""
    drop = volumes or testenv_decide.discards_volumes(t.at("discard_volumes"))
    return ["down", *(["--volumes"] if drop else [])]


def down(t: Target, volumes: bool = False) -> int:
    """破棄してスロットを解放する。破棄に失敗したら解放しない（コンテナとポートが残ったまま番号を渡さない）。"""
    row = t.book.current(t.key)
    if row is None:
        return 0
    if not t.shell.has_docker():
        raise TestenvError(f"コンテナの実行系が見つからず、破棄できたか確かめられない。スロットは解放していない: {row['environment']}")
    t.book.update(t.key, lambda r: r.pop("golden_tag", None))
    if compose_file_args(t):
        rc = compose(t, row, *_down_args(t, volumes))
    else:  # 定義が無いと compose で破棄できない。コンテナが残っていないと確かめられたときだけ解放する
        left = _workdirs(t, row["environment"])
        rc = 0 if left == [] else 1
    if rc != 0:
        raise TestenvError(f"破棄に失敗した。スロットは解放していない: {row['environment']}")
    if not t.book.update(t.key, _release):
        raise TestenvError(f"スロットの解放を台帳へ記録できない。down を打ち直す: {row['environment']}")
    return 0


# --- テストの実行 --------------------------------------------------------------


def _exclude_evidence(t: Target) -> None:
    """証跡の置き場を、共通の git ディレクトリの info/exclude へ登録する（worktree 固有の exclude は git が読まない）。"""
    exclude = Git(t.path).common_dir() / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
    if f"{EVIDENCE}/" not in lines:
        with exclude.open("a", encoding="utf-8") as f:
            f.write(f"{EVIDENCE}/\n")


def _kind_values(spec: dict[str, Any], ports: dict[str, Any]) -> dict[str, str]:
    """種類の宣言から、初期化の抑止（`skip_reset`）とエントリポイントの URL（`base_url_env`）の値を組み立てる。"""
    skip = spec.get("skip_reset")
    values = {str(k): str(v) for k, v in skip.items()} if isinstance(skip, dict) else {}
    base_url = spec.get("base_url_env")
    port = ports.get(spec["port_role"] if isinstance(spec.get("port_role"), str) else "http")
    if isinstance(base_url, str) and port is not None:
        values[base_url] = f"http://localhost:{port}"
    return values


def _prepare_evidence(t: Target, environment: str, out: Optional[Path]) -> str:
    """証跡の置き場を決めて作り、info/exclude へ登録して実体のパスを返す。worktree の外を指せば `TestenvError`。"""
    where = out if out is not None else t.path / EVIDENCE / f"{environment}-{time.strftime(ledger.COMPACT_STAMP, time.gmtime())}"
    real = os.path.realpath(where if where.is_absolute() else t.path / where)
    if not t.contains(real):
        raise TestenvError(f"証跡の置き場が worktree の外を指す: {real}")
    Path(real).mkdir(parents=True, exist_ok=True)
    _exclude_evidence(t)
    return real


def test_values(t: Target, kind: str, row: dict[str, Any], out: Optional[Path]) -> dict[str, str]:
    """テストのコマンドへ渡す値（初期化の抑止・エントリポイントの URL・証跡の置き場）。証跡が worktree の外を指せば `TestenvError`。"""
    spec = t.at("test_kinds", kind)
    spec = spec if isinstance(spec, dict) else {}
    values = _kind_values(spec, row["ports"])
    out_env = spec.get("out_env")
    if isinstance(out_env, str):
        values[out_env] = _prepare_evidence(t, row["environment"], out)
    return values


def reach(t: Target, service: str) -> dict[str, str]:
    """サービスのコンテナが worktree を見ているかを、探りの印を読ませて確かめ、コンテナへ渡す値を返す。

    見ていなければ `TestenvError`（メインディレクトリのコードで走った偽の成功を出さない）。印は必ず消す。
    """
    if not t.shell.has_docker():
        raise TestenvError(f"コンテナの実行系（{t.shell.docker}）が見つからない")
    values = compose_env(t)
    _exclude_evidence(t)
    token = secrets.token_hex(16)
    rel = f"{EVIDENCE}/reach-{token}"
    mark = t.path / rel
    mark.parent.mkdir(exist_ok=True)
    mark.write_text(token, encoding="utf-8")
    try:
        done = t.shell.docker_run("compose", "exec", "-T", service, "cat", rel, env=values, cwd=t.path)
    finally:
        mark.unlink(missing_ok=True)
    if done.returncode != 0 or done.stdout.strip() != token:
        raise TestenvError(f"サービス {service} のコンテナが worktree を見ていない（{t.key}）。テストを走らせない")
    return values


def run_test(t: Target, kind: str, out: Optional[Path] = None) -> int:
    """宣言の種類のテストを打ち、その終了コードを返す（成否を包み隠さない）。種類の宣言が無ければ打たずに 0。"""
    command = t.at("test_kinds", kind, "run")
    if not isinstance(command, str) or not command:
        return 0
    row = assign(t)
    values = test_values(t, kind, row, out)
    service = t.at("test_kinds", kind, "service")
    if isinstance(service, str) and service:
        values = {**reach(t, service), **values}
    t.book.update(t.key, _touch)
    # 実行中は reap の対象から外れるよう、目印を握ったまま走らせる
    try:
        with FileLock(str(inuse_lock(t, row["environment"])), timeout=INUSE_WAIT):
            rc = t.shell.attached(["sh", "-c", command], t.path, values)
    except Timeout as exc:
        raise TestenvError(f"同じテスト環境で別の実行が動いている: {row['environment']}") from exc
    t.book.update(t.key, _touch)
    return rc


# --- 回収 ----------------------------------------------------------------------


def _workdirs(t: Target, environment: str) -> Optional[list[str]]:
    """環境のコンテナ（停止済みを含む）の作業ディレクトリ（compose のラベル）。調べられなければ `None`（「無い」と区別する）。"""
    fmt = '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
    listed = t.shell.docker_run("ps", "-a", "--filter", f"label={container.PROJECT_LABEL}={environment}", "--format", "{{.Names}}")
    if listed.returncode != 0:
        return None
    names = [n for n in listed.stdout.split() if n]
    if not names:
        return []
    done = t.shell.docker_run("inspect", "-f", fmt, *names)
    return [line.strip() for line in done.stdout.splitlines()] if done.returncode == 0 else [""]


def _reclaim_one(t: Target, row: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    env, wt = row["environment"], Path(row["worktree"])
    item: dict[str, Any] = {"kind": "testenv", "name": env, "worktree": str(wt)}
    if not placement.location_seen(wt, t.main_dir):
        return {**item, "result": outcomes.KEPT, "reason": "worktree の置き場をここから確かめられない"}
    if ledger.exposing(row):
        return {**item, "result": outcomes.KEPT, "reason": "公開が開いている（unexpose で閉じてから回収する）"}
    if not t.shell.has_docker():
        return {**item, "result": outcomes.KEPT, "reason": "コンテナの実行系が見つからず、破棄できたか確かめられない"}
    workdirs = _workdirs(t, env)
    if workdirs is None:
        return {**item, "result": outcomes.KEPT, "reason": "コンテナを調べられない（docker ps が失敗した）"}
    owner, why = placement.owner_of(workdirs, [str(r.get("worktree")) for r in rows if r.get("environment") == env])
    if owner == placement.FOREIGN:
        return {**item, "result": outcomes.KEPT, "reason": why}
    if owner == placement.OWN:
        rc = compose_row(t, row, *_down_args(t))
        if rc != 0:
            return {**item, "result": outcomes.KEPT, "reason": f"破棄に失敗した（終了コード {rc}）"}
    t.book.update(str(wt), _release)
    return {**item, "result": outcomes.RECLAIMED if owner == placement.OWN else outcomes.RELEASED}


def reclaim(t: Target, limit: Optional[int] = None) -> list[dict[str, Any]]:
    """worktree の消えた割り当てを回収し、1 件ごとの結果を返す。`limit` は解放する数の上限（スロットを空けるとき 1）。"""
    rows = t.book.rows()
    items: list[dict[str, Any]] = []
    for row in rows:
        if not ledger.active(row) or Path(str(row.get("worktree"))).exists():
            continue
        items.append(_reclaim_one(t, row, rows))
        if limit is not None and sum(1 for i in items if i["result"] != outcomes.KEPT) >= limit:
            break
    return items
