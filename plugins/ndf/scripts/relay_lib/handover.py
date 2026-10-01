"""ラッパーの入れ替えの判定と、入れ替えの申し送り（`handover.json`）（#1587）。

セッションの切り替えで子の claude が居ない間に、動いているラッパーを更新後の版のバージョンディレクトリの
コードへ `os.execve` で入れ替える。PID・端末・作業ディレクトリは変わらない。

1. `running_dir`: 動いているバージョンディレクトリの名前。プラグインのキャッシュから起動したものは None（判定しない）
2. `expected_dir`: 導入先のファイルから計算した、更新後に使うべき名前。動いている名前と同じなら何もしない
3. `prepare_target`: 違えば、更新後の版の `relay.py startup` を子プロセスで打ってバージョンディレクトリと
   `relay.current` を置かせ、動いているランチャーの置き場の `relay.current` を読み直す
4. `write_handover` / `exec_argv`: 申し送りを 0600 で書き、入れ替え先の python でランチャーを起動し直す。
   新しい版は環境変数 `NDF_RELAY_HANDOVER` で申し送りのパスを受け、`read_handover` で読んで消す（`Swapper.from_handover`）

ラッパーは複製を書き換えない（置くのは更新後の版の `startup`）。申し送りは認証情報を持たない。
`SectionInput`（次のセッションの起動の入力。申し送りの `next`）と、`Relay` が混ぜる `Swapper` もここに置く。
標準ライブラリ・relay_lib・バージョンディレクトリに入る `lib/` だけを import する。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass

from . import runtime, version_dir
from .claude import DROP_ENV
from .common import LOG_FILE, PKG_ROOT, env_num, read_text, remove
from .record import RelayRecord, StartLimit, current_section
from .switch import UsageWatch
from .terminal import Terminal
from .version_dir import VersionDir, plugin_version

import claude_accounts as ca  # noqa: E402,I001  common が lib/ を sys.path に置く

SCHEMA = 1
HANDOVER_FILE = "handover.json"
HANDOVER_ENV = "NDF_RELAY_HANDOVER"
LAUNCHER = "relay.py"
# `reexec_skipped` の `reason` の集合（設計の入出力の契約）
STARTUP_FAILED = "startup-failed"
NOT_PLACED = "not-placed"
UNREADABLE = "unreadable"
NO_ENV = "no-env"
NO_HANDOVER = "no-handover"
HANDOVER_WRITE = "handover-write"
EXEC_FAILED = "exec-failed"
REASONS = (STARTUP_FAILED, NOT_PLACED, UNREADABLE, NO_ENV, NO_HANDOVER, HANDOVER_WRITE, EXEC_FAILED)
# 読む側が無いと次のセッションを起動できないキー
REQUIRED = ("shown", "from_dir", "to_dir", "written_at", "claude", "marketplace", "version", "first_args", "state", "next")
STATE_KEYS = ("section", "account", "multi", "auth_section")
NEXT_KEYS = ("args", "cwd", "command", "from_session", "cwd_fallback", "carried", "plan")
# 環境の用意を含む startup の上限の秒（決定 10。中の uv sync と同じ値）
PREPARE_TIMEOUT = 600


@dataclass
class SectionInput:
    """区間の起動の入力。`plan` は上限の後に決めた (アカウント, 理由, 選んだ結果)。"""

    args: list[str]
    cwd: str
    command: str
    from_session: str
    cwd_fallback: str | None = None
    carried: list[str] | None = None
    plan: tuple | None = None

    def to_json(self) -> dict:
        """申し送りの `next`。`plan` の選んだ結果は `Choice` のキーの辞書になる。"""
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> SectionInput:
        plan = d.get("plan")
        if plan:
            to, reason, ch = plan
            if isinstance(ch, dict):
                ch = dict(ch)
                if ch.get("earliest"):
                    ch["earliest"] = tuple(ch["earliest"])
                ch = ca.Choice(**ch)
            plan = (to, reason, ch)
        return cls(**{k: d[k] for k in NEXT_KEYS if k != "plan"}, plan=plan or None)


class Skip(Exception):
    """入れ替えない。`reason` は REASONS の 1 つ、`detail` は 1 行。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def running_dir(root: str = PKG_ROOT) -> str | None:
    """入れ替えの対象になりうるときだけ返す、動いているバージョンディレクトリの名前。

    `own_dir` の名前のうち、隣にランチャーがあるものに限る。プラグインのキャッシュから起動した（または隣にランチャーが無い）ときは None。"""
    name = own_dir(root)
    if name is None or not os.path.isfile(os.path.join(copy_base(root), LAUNCHER)):
        return None
    return name


def own_dir(root: str = PKG_ROOT) -> str | None:
    """`start` の行（`relay_version_dir`）へ記録する、動いているバージョンディレクトリの名前。キャッシュから起動したら None。

    ランチャーが隣にあるかは見ない。入れ替えの判定には `running_dir` を使う。"""
    return os.path.basename(root) if runtime.is_version_dir(root) else None


def copy_base(root: str = PKG_ROOT) -> str:
    """動いているランチャーの置き場（バージョンディレクトリの親）。読み直す `relay.current` はここのもの（I10）。"""
    return os.path.dirname(root)


def expected_dir(install_path: str | None, version: str | None) -> str | None:
    """導入先 `install_path` のファイルから計算した、更新後に使うべきバージョンディレクトリの名前。読めなければ None。

    版は導入先の `plugin.json`（`startup` が使うもの）を先に、無ければ `version` を使う。"""
    if not install_path:
        return None
    try:
        ver = plugin_version(install_path) or version
        if not ver:
            return None
        return VersionDir("", source=os.path.join(install_path, "scripts")).name_for(ver)
    except OSError:
        return None


def startup_env() -> dict:
    """`startup` の子プロセスの環境。`plugin_cli` と同じく、子の claude の印と作業ディレクトリを除く。"""
    return {k: v for k, v in os.environ.items() if k not in DROP_ENV and k not in ("NDF_RELAY_DIR", HANDOVER_ENV)}


def prepare_target(install_path: str, running: str, base: str) -> str:
    """更新後の版の `startup` を打ち、`base` の `relay.current` が指す入れ替え先の名前を返す。入れ替えないなら Skip。"""
    launcher = os.path.join(install_path, "scripts", LAUNCHER)
    timeout = env_num("NDF_RELAY_PREPARE_TIMEOUT", PREPARE_TIMEOUT)
    try:
        p = subprocess.run(
            [sys.executable, launcher, "startup"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=startup_env(),
        )
    except subprocess.TimeoutExpired:
        raise Skip(STARTUP_FAILED, f"startup が {int(timeout)} 秒で終わらない") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise Skip(STARTUP_FAILED, f"startup を起動できない（{type(e).__name__}）") from None
    if p.returncode != 0:
        raise Skip(STARTUP_FAILED, f"startup の終了コード {p.returncode}")
    to = VersionDir(base).current()
    if to is None:
        raise Skip(UNREADABLE, "relay.current が無いか壊れている")
    if to == running:
        raise Skip(NOT_PLACED, "startup の後も relay.current が動いているものを指す")
    root = os.path.join(base, to)
    if not os.path.isfile(runtime.python_of(runtime.version_env(root))):
        raise Skip(NO_ENV, f"{to} に環境の python が無い")
    if not os.path.isfile(os.path.join(root, "relay_lib", "handover.py")):
        raise Skip(NO_HANDOVER, f"{to} は申し送りを読む仕組みを持たない")
    return to


def write_handover(relay_dir: str, data: dict) -> str:
    """申し送りを作業ディレクトリへ 0600 で書き、パスを返す。書けなければ Skip（handover-write）。"""
    path = os.path.join(relay_dir, HANDOVER_FILE)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"schema": SCHEMA, **data}, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as e:
        remove(tmp)
        raise Skip(HANDOVER_WRITE, f"申し送りを書けない（{type(e).__name__}）") from None
    return path


def _valid(data: dict) -> bool:
    """申し送りの形の検査。知っている `schema` で、必須のキーと型がそろっていれば True。"""
    schema = data.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or not 1 <= schema <= SCHEMA:
        return False
    state, nxt = data.get("state"), data.get("next")
    return not (
        any(k not in data for k in REQUIRED)
        or not isinstance(state, dict)
        or not isinstance(nxt, dict)
        or not isinstance(state.get("section"), int)
        or any(k not in state for k in STATE_KEYS)
        or any(k not in nxt for k in NEXT_KEYS)
        or not isinstance(data.get("first_args"), list)
    )


def read_handover(path: str) -> tuple[dict | None, str | None]:
    """申し送りを読んで消す。(中身か None, 利用者が手で打つ次のコマンドか None)。

    無い・JSON でない・知らない `schema`・必須のキーが無いときは中身を None にする。`shown` は読めれば返す。"""
    text = read_text(path)
    remove(path)
    try:
        data = json.loads(text) if text is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, None
    shown = data.get("shown") if isinstance(data.get("shown"), str) and data.get("shown") else None
    return (data if _valid(data) else None), shown


def exec_argv(base: str, to: str, first_args: list[str]) -> tuple[str, list[str]]:
    """入れ替え先の python と、ランチャーを `run 最初の引数` で起動する argv。"""
    python = runtime.python_of(runtime.version_env(os.path.join(base, to)))
    return python, [python, os.path.join(base, LAUNCHER), "run", *first_args]


def exec_env(path: str) -> dict:
    """ラッパーの環境に申し送りのパスだけを足したもの（中身は環境変数に入れない）。"""
    env = dict(os.environ)
    env.pop(runtime.REEXEC_ENV, None)
    env[HANDOVER_ENV] = path
    return env


def elapsed(since: float) -> float:
    return round(time.time() - since, 3)


class Swapper:
    """`Relay` が混ぜる、ラッパーの入れ替え（旧版の側）と申し送りの状態の復元（新版の側）。

    `Relay` の `dir`・`log`・`term`・`limit`・`section`・`account`・`multi`・`auth_section`・`claude`・`marketplace`・
    `version`・`install_path`・`first_args` を使う。"""

    first_args: list[str] = []
    install_path: str | None = None  # 更新後のプラグインの導入先（`prepare_next` が置く）

    def swap(self, s: SectionInput, shown: str) -> None:
        """動いているバージョンディレクトリと更新後に使うべきものが違えば、入れ替える（戻らない）。

        同じ名前・キャッシュからの起動では何もしない（外部コマンドも行も無い。I8・I10）。入れ替えられなければ
        画面に 1 行と `reexec_skipped` の行を残して戻る（I5）。例外を外へ出さない。"""
        running = running_dir()
        if running is None:
            return
        t0 = time.time()
        stage, to = UNREADABLE, None
        try:
            expected = expected_dir(self.install_path, self.version)
            if expected == running:
                return
            if expected is None:
                raise Skip(UNREADABLE, "更新後のプラグインの導入先を読めない")
            base = copy_base()
            stage = STARTUP_FAILED
            to = prepare_target(self.install_path, running, base)
            stage = HANDOVER_WRITE
            path = write_handover(self.dir, self.handover(s, shown, running, to, t0))
            stage = EXEC_FAILED
            self._exec(base, to, path)
        except Skip as e:
            self._swap_skipped(e.reason, running, to, e.detail, t0)
        except Exception as e:  # 入れ替えの失敗で次のセッションを失わない
            self._swap_skipped(stage, running, to, f"想定外の例外（{type(e).__name__}）", t0)

    def handover(self, s: SectionInput, shown: str, running: str, to: str, t0: float) -> dict:
        """申し送りの中身（設計の申し送りの形の表。認証情報と環境変数を持たない）。"""
        now = time.time()
        return dict(
            shown=shown,
            from_dir=running,
            to_dir=to,
            written_at=now,
            prepare_seconds=round(now - t0, 3),
            claude=self.claude,
            marketplace=self.marketplace,
            version=self.version,
            first_args=list(self.first_args),
            state=dict(section=self.section, account=self.account, multi=self.multi, auth_section=self.auth_section),
            next=s.to_json(),
        )

    def _exec(self, base: str, to: str, path: str) -> None:
        """`count.lock` を放し、端末を入力を捨てずに戻して exec する。失敗したら元へ戻して Skip（exec-failed）。"""
        python, argv = exec_argv(base, to, self.first_args)
        env = exec_env(path)
        self.limit.release()
        self.term.restore(keep_input=True)
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execve(python, argv, env)
        except OSError as e:
            remove(path)
            self.term.set_raw(keep_input=True)
            self.limit.take_within()
            raise Skip(EXEC_FAILED, f"{to} を起動できない（{e.strerror or type(e).__name__}）") from None

    def _swap_skipped(self, reason: str, running: str, to: str | None, detail: str, t0: float) -> None:
        row = {"section": self.section, "reason": reason, "from": running}
        if to:
            row["to"] = to
        if detail:
            row["detail"] = detail
        self.log(event="reexec_skipped", **row, seconds=elapsed(t0))
        self.term.screen(f"ndf-relay: ラッパーを新しい版へ入れ替えずに今の版で続ける（{reason}: {detail or '理由は不明'}）")

    @classmethod
    def from_handover(cls, path: str, serve) -> int:
        """入れ替えた後の `run`。起動の判定と `claude plugin list` を通さず、申し送りから組み立てて `serve` で続ける
        （決定 4）。読めなければ止めて終了コード（I6）。`serve` は run の `_serve_terminal`。"""
        d = os.path.dirname(path)
        h, shown = read_handover(path)
        if h is None:
            return stop_unreadable(d, shown)
        relay = cls(h["claude"], d, h["marketplace"], h["version"], Terminal(), StartLimit(os.path.join(d, LOG_FILE)))
        relay.restore_state(h["state"])
        # 新しい方へ印を置いてから古い方の印を消す（I11・決定 7。pid は exec で変わらない）
        inuse = version_dir.claim_inuse()
        if h["from_dir"] != os.path.basename(PKG_ROOT):
            version_dir.release_inuse(os.path.join(copy_base(), h["from_dir"], f"inuse-{os.getpid()}"))
        written = h["written_at"]
        relay.log(
            event="reexec",
            section=h["state"]["section"],
            **{"from": h["from_dir"], "to": h["to_dir"]},
            prepare_seconds=h.get("prepare_seconds"),
            seconds=elapsed(written) if isinstance(written, (int, float)) else None,
        )
        return serve(relay, relay.term, inuse, lambda: relay.resume(h), keep_input=True)

    def restore_state(self, state: dict) -> None:
        """申し送りの状態を戻す（セッションの番号は続き。アカウントを切り替えるかは数え直さない）。"""
        self.section = state["section"]
        self.account = state["account"]
        self.auth_section = state["auth_section"]
        multi = bool(state["multi"])
        if multi != self.multi:
            self.multi = multi
            self.watch = UsageWatch(self) if multi else None

    def resume(self, h: dict) -> int:
        """入れ替えた後: `count.lock` を取り直し（上限は判定し直さない）、申し送りの次のセッションを起動して続ける。"""
        self.first_args = list(h["first_args"])
        if not self.limit.take_within():
            return self.give_up("count-lock", "起動の数を数えるロックが取れない", h["shown"])
        self.term.screen(f"── ndf-relay: 区間 {self.section + 1} ──")
        code = self._start_next_section(SectionInput.from_json(h["next"]), h["shown"])
        if code is not None:
            return code
        return self._serve()


def stop_unreadable(d: str, shown: str | None) -> int:
    """申し送りを読めない: 次のセッションを起動せず、手で打つコマンドを示して止まる（I6）。"""
    try:
        RelayRecord(d).log(event="stop", section=current_section(d), reason="handover")
    except OSError:
        pass
    command = shown or "claude を打ち直し、前の会話は /resume で選ぶ"
    Terminal.screen(f"ndf-relay: 次の区間を起動できない（ラッパーの入れ替えの申し送りを読めない）。次のコマンド:\r\n{command}")
    return 2
