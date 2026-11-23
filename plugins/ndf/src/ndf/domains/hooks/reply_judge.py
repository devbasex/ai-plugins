"""軽いモデルへの判定のリクエスト（#4000 の決定 1・3・6・7・10・11）。

hook の閉包の中にあるので、標準ライブラリと同じパッケージの `tables` だけを読む。軽いモデルの起動は hook の外の
子プロセス（`ndf.domains.hooks.judge_child`）が行い、ここはその子を新しいプロセスの組で起動して秒の上限で待つ。

- `judge_text`: コードブロックの行と空行を除いた本文の末尾（設定の字数）を、質問へ渡す文にする（決定 7）
- `parent_launch`: 送信先の確かめの hook の側。起動の形と、親の Claude Code の起動引数・実行ファイルの同一性を読む（決定 11）
- `ask`: 判定の子を起動して待ち、どの終わり方でも組ごと止めてから回収する（決定 3・6）
- `resting` / `endpoint_resting` / `mark_rest`: 起動が続けて失敗する環境で、休む間だけ子を起動しないためのファイル
  （決定 10）。起動の失敗は親の探索の前に `resting` で、送信先の不一致（`endpoint`）は親の起動引数を含む確かめの
  対象（`endpoint_scope`）ごとに親の探索の後で `endpoint_resting` で確かめる

設定（モデル・秒・字数・休む間・送信先の確かめの値）は `limits.json` の `hook.stop-wait.judge` から `_judge` が読む。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import select
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Optional, Sequence

from ndf.domains.hooks import tables
from ndf.domains.relay import claude_options, signals

# 判定の子のモジュール（hook の閉包の外。import しない）
CHILD_MODULE = "ndf.domains.hooks.judge_child"
# 軽いモデルの思考を切る環境（#4000 の決定 4・MEA3）。Stop の判定と judge-cases の確かめが同じ値で打つ
THINKING_OFF = {"MAX_THINKING_TOKENS": "0"}
# 休みのマーカーのファイルの名前（状態の保存先の hook の保存先）
REST_NAME = "judge-rest.json"
# 休みのマーカーの更新を直列にする錠のファイルの名前（`.` で始まる名前は `metrics.prune_state` が消さない）
REST_LOCK_NAME = ".judge-rest.lock"
# 組へ SIGTERM を送った後、SIGKILL までに待つ秒
GROUP_GRACE = 0.2
# 子の標準出力を 1 回に読む上限のバイト数
READ_CHUNK = 65536
# 子の標準入力へ 1 回に書くバイト数（書ける状態と分かった後なら止まらない上限）
WRITE_CHUNK = int(getattr(select, "PIPE_BUF", 512))
# 子が返してよい判定
KINDS = ("wait", "report")


class Launch(NamedTuple):
    """親の Claude Code の起動引数・プロジェクトの根・実行ファイルの実パスと `[st_dev, st_ino]`。`ask` が子へ渡す。"""

    argv: list[str]
    project_dir: str
    exe: str
    exe_id: list[int]


class Judged(NamedTuple):
    """判定の結果。回答が得られなければ `kind` が None で `failure` が種類を持つ。"""

    kind: Optional[str]
    failure: Optional[str]
    ms: float
    calls: int


def _judge(key: str) -> Any:
    """判定の設定（`limits.json` の `hook.stop-wait.judge`）の 1 つの値。"""
    return tables.policy_value("hook.stop-wait.judge", key)


def _markdown_lines(text: str) -> list[str]:
    """`ndf.adapters.markdown.code_lines` と同じ数え方の行（改行は `\\r\\n`・`\\r`・`\\n` だけ。末尾の改行の後は行にしない）。

    `str.splitlines` は U+2028 なども改行に数えるので使わない（行がずれるとコードの行を本文と取り違える）。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return []
    lines = normalized.split("\n")
    return lines[:-1] if normalized.endswith("\n") else lines


def judge_text(text: str, code_lines: list[bool], max_chars: int, keep: Sequence[str] = ()) -> str:
    """コードブロックの行と空行を除いた本文。`max_chars` を超えれば末尾の `max_chars` 字（決定 7）。

    `keep` はモデルへ判定を委ねた見出しの文（`reply_wait.ProseReading.delegated`。本文の順）。本文が上限を超えて
    切ったとき、最終的に残す末尾に無い見出しを「## 」を付けて先頭に置き、合わせて `max_chars` 字に収める。
    先頭に置く見出しは本文の順に並べ、上限の半分までで、超えれば本文で後ろの見出しを残す。先頭に置いて末尾が縮むと、
    縮めた末尾から外れた見出しも先頭へ足す（足す見出しが増えなくなるまで）。確定した欠けは探し直さず、末尾にある
    見出しはどれも見て、外れた分を足す（見出しの文が後ろに引用されて末尾に残っても、その後の見出しを見落とさない）。
    `code_lines` の数が行の数と合わなければ、どの行がコードか分からないので空の文を返す（本文を作らない）。
    """
    lines = _markdown_lines(text)
    if len(code_lines) != len(lines):
        return ""
    prose = "\n".join(line for line, code in zip(lines, code_lines) if not code and line.strip())
    if len(prose) <= max_chars:
        return prose
    tail = prose[-max_chars:]
    # 末尾に無い見出し（確定した欠け）。確定した欠けは探し直さず、末尾にある見出しのうち外れた分だけを足す
    missing = {heading for heading in keep if heading not in tail}
    while missing:
        lead = "".join(f"\n## {heading}" for heading in keep if heading in missing)[1:][-(max_chars // 2) :]
        tail = prose[-(max_chars - len(lead) - 1) :]
        dropped = {heading for heading in keep if heading not in missing and heading not in tail}
        if not dropped:
            return f"{lead}\n{tail}"
        missing |= dropped
    return tail


def _proc_file(pid: int, name: str) -> Path:
    return Path("/proc") / str(pid) / name


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _readlink(path: Path) -> str:
    """リンクの指す実パス（`/proc/<pid>/exe`）。"""
    return os.path.realpath(os.readlink(path))


def _ppid(read: Callable[[Path], bytes], pid: int) -> int:
    """`/proc/<pid>/stat` の親のプロセスの番号。実行ファイルの名前は括弧を含むので、最後の括弧の後ろから読む。"""
    fields = read(_proc_file(pid, "stat")).decode("utf-8", "replace").rpartition(")")[2].split()
    if len(fields) < 2:
        raise OSError(f"/proc/{pid}/stat の形が違う")
    return int(fields[1])


def _cmdline(read: Callable[[Path], bytes], pid: int) -> list[str]:
    """`/proc/<pid>/cmdline` の起動引数（NUL 区切り）。

    外すのは末尾の終端の NUL だけで、途中の空引数（`--system-prompt ''` の値など）は残す。落とすと次のオプション
    （`--settings`）が前のオプションの値に読まれ、子の `endpoint_refusal` が拒めなくなる。
    """
    raw = read(_proc_file(pid, "cmdline")).decode("utf-8", "replace")
    if not raw:
        return []
    return raw.removesuffix("\0").split("\0")


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _found(current: int, exe: str, read: Callable[..., bytes], stat: Callable[..., os.stat_result]) -> Optional[Launch]:
    """Claude Code と分かった親の起動引数と実行ファイルの同一性。プロジェクトの根は呼び手が入れる。

    実行ファイルが消えたか置き換わった（` (deleted)`）なら、親が読み込んだ実体を確かめられないので None。
    """
    # `/proc/<pid>/exe` の読みは、親の起動後に実行ファイルが消えたか置き換わると末尾に ` (deleted)` が付く
    if exe.endswith(" (deleted)"):
        return None
    # stat は /proc/<pid>/exe のリンクの先のパスでなく、親が読み込んだ実体を返す
    found = stat(_proc_file(current, "exe"))
    return Launch(argv=_cmdline(read, current), project_dir="", exe=exe, exe_id=[found.st_dev, found.st_ino])


def _claude_launch(
    pid: int,
    endpoint: Mapping[str, Any],
    read: Callable[..., bytes],
    readlink: Callable[..., str],
    stat: Callable[..., os.stat_result],
) -> Optional[Launch]:
    """hook のプロセスの親からたどり、最も近い Claude Code の実プロセスを返す（決定 11）。

    Claude Code かは実行ファイルの実パスだけで決め、引数の中身では決めない。node などスクリプトを動かす実行ファイル
    （`endpoint.script_hosts`）に当たったら、起動引数を見ずにそこで止める（node の上の Claude Code は起動引数からも
    読み込んだスクリプトからも確証できず、そこより外側の Claude Code も採らない）。見つからなければ None。
    """
    current = pid
    try:
        for _ in range(int(endpoint["ancestors"])):
            current = _ppid(read, current)
            exe = readlink(_proc_file(current, "exe"))
            if _matches(exe, endpoint["cli_exe"]):
                return _found(current, exe, read, stat)
            if _matches(os.path.basename(exe), endpoint["script_hosts"]):
                return None
    except (OSError, ValueError):  # noqa: NDF401 ファイル: /proc を読めない環境（macOS を含む）では親を確かめない
        return None
    return None


def parent_launch(
    env: Mapping[str, str],
    pid: int,
    read: Callable[..., bytes] = _read_bytes,
    readlink: Callable[..., str] = _readlink,
    stat: Callable[..., os.stat_result] = os.stat,
) -> Optional[Launch]:
    """送信先の確かめの hook の側（決定 11）。親の Claude Code の起動の形とプロジェクトの根、確かめられなければ None。

    起動の形（`CLAUDE_CODE_ENTRYPOINT`）・`CLAUDE_PROJECT_DIR`・`CLAUDE_CONFIG_DIR` を見てから `/proc` をたどる。
    引数の中身（`--settings` など）はここでは見ない（判定の子の `endpoint_refusal` が実効の設定で確かめる）。
    """
    endpoint = _judge("endpoint")
    project_dir = env.get("CLAUDE_PROJECT_DIR")
    config_dir = env.get("CLAUDE_CONFIG_DIR")
    comparable = (
        env.get("CLAUDE_CODE_ENTRYPOINT") in endpoint["entrypoints"]
        and bool(project_dir)
        and os.path.isabs(project_dir)
        and (config_dir is None or os.path.isabs(config_dir))
    )
    if not comparable:
        return None
    found = _claude_launch(pid, endpoint, read, readlink, stat)
    return found._replace(project_dir=str(project_dir)) if found and found.argv else None


def _child_env(env: Mapping[str, str]) -> dict[str, str]:
    """子へ渡す環境。環境変数の表の `child` が `drop` の行を外し、`PYTHONPATH` と思考の token の上限を入れる。"""
    drop = {row["id"] for row in tables.load("domains/env.json")["rows"] if row.get("child") == "drop"}
    child = {name: value for name, value in env.items() if name not in drop}
    child["PYTHONPATH"] = str(tables.PLUGIN_ROOT / "src")
    child.update(THINKING_OFF)
    return child


def _ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _signal_group(pid: int, sig: int, killpg: Callable[[int, int], None]) -> None:
    """プロセスの組へ 1 つ送る。組が先に終わっていれば送る相手がいない。"""
    try:
        killpg(pid, sig)
    except (ProcessLookupError, PermissionError):  # noqa: S110 外部コマンド: 組が先に終わっていれば送る相手がいない
        pass


def _pause(seconds: float) -> None:
    """SIGTERM の後の猶予を待つ（テストは差し替えて実時間を待たない）。"""
    time.sleep(seconds)


def _stop_group(proc: Any, killpg: Callable[[int, int], None], gentle: bool) -> None:
    """組を止めてから組のリーダー（判定の子）を回収する。

    リーダーを回収するまでは組の番号が再利用されないので、組への送信は必ず回収の前に済ませる。リーダーが先に
    終わっても組に `claude` が残りうるため、`gentle` なら SIGTERM の後に `GROUP_GRACE` 秒おき、どの経路でも
    最後に組へ SIGKILL を送ってから回収する。
    """
    try:
        try:
            if gentle:
                _signal_group(proc.pid, signal.SIGTERM, killpg)
                _pause(GROUP_GRACE)
        finally:
            # 猶予の待ちが締め切りの SIGALRM などで破られても、SIGKILL を送ってから回収する
            _signal_group(proc.pid, signal.SIGKILL, killpg)
    finally:
        proc.wait()


def _write_some(selector: selectors.BaseSelector, proc: Any, fd: int, pending: memoryview) -> memoryview:
    """標準入力へ書ける分を書き、残りを返す。書き終えたら待ちから外して閉じる。子が読まずに終われば残りを捨てる。"""
    try:
        rest = pending[os.write(fd, pending[:WRITE_CHUNK]) :]
    except BrokenPipeError:
        # 外部コマンド: 子が読まずに終わった。回答の無い終わりとして読む
        rest = pending[len(pending) :]
    if not rest:
        selector.unregister(proc.stdin)
        proc.stdin.close()
    return rest


def _step(
    selector: selectors.BaseSelector, proc: Any, pending: memoryview, chunks: list[bytes], remaining: float
) -> tuple[memoryview, bool]:
    """1 回の待ちで用意できた分を書いて読む。書き残しと、標準出力が終わりまで来たかを返す。"""
    # 子は標準入力を読み終えてから回答を書くので、両方が同時に用意できたら書く方を先にする
    for key, _ in sorted(selector.select(remaining), key=lambda event: event[0].fileobj is proc.stdout):
        if key.fileobj is proc.stdin:
            pending = _write_some(selector, proc, key.fd, pending)
        elif data := os.read(key.fd, READ_CHUNK):
            chunks.append(data)
        else:
            return pending, True
    return pending, False


def _exchange(proc: Any, payload: bytes, deadline: float) -> Optional[bytes]:
    """子の標準入力へ `payload` を書いて閉じ、標準出力を終わりまで読む。`deadline` までに終わらなければ None。

    `communicate` はリーダーを回収してしまうので使わない（回収の前に組を止めるため、`_stop_group`）。
    """
    pending = memoryview(payload)
    chunks: list[bytes] = []
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        if pending:
            selector.register(proc.stdin, selectors.EVENT_WRITE)
        else:
            proc.stdin.close()
        while (remaining := deadline - time.monotonic()) > 0:
            pending, done = _step(selector, proc, pending, chunks, remaining)
            if done:
                return b"".join(chunks)
    return None


def _judged(out: str, code: int, ms: float) -> Judged:
    """子の 1 行の JSON を判定の結果にする。

    回答を読めたら終了コードは見ない（読んだ後に組へ送る SIGKILL が、終わりかけの子の終了コードになりうる）。
    読めなければ、終了コードが 0 以外で `exit`、0 で `shape`。
    """
    answer = tables.json_object(out)
    if answer is None:
        return Judged(None, "exit" if code != 0 else "shape", ms, 0)
    raw_calls = answer.get("calls")
    calls = raw_calls if isinstance(raw_calls, int) else 0
    failure = answer.get("failure")
    if isinstance(failure, str) and failure:
        return Judged(None, failure, ms, calls)
    kind = answer.get("kind")
    return Judged(kind, None, ms, calls) if kind in KINDS else Judged(None, "shape", ms, calls)


def _awaited(
    proc: Any,
    payload: str,
    seconds: float,
    started: float,
    killpg: Callable[[int, int], None],
) -> Judged:
    """子の回答を待つ。どの終わり方でも組を止めてから回収し、割り込みは投げ直す。"""
    try:
        try:
            out = _exchange(proc, payload.encode("utf-8"), started + seconds)
        except BaseException:
            _stop_group(proc, killpg, gentle=True)
            raise
        if out is None:
            _stop_group(proc, killpg, gentle=True)
            return Judged(None, "timeout", _ms(started), 0)
        _stop_group(proc, killpg, gentle=False)
    finally:
        for stream in (proc.stdin, proc.stdout):
            stream.close()
    return _judged(out.decode("utf-8", "replace"), proc.returncode, _ms(started))


def ask(
    text: str,
    seconds: float,
    env: Mapping[str, str],
    launch: Launch,
    popen: Callable[..., Any] = subprocess.Popen,
    killpg: Callable[[int, int], None] = os.killpg,
) -> Judged:
    """判定の子を新しいプロセスの組で起動し、`seconds` で待って回答を読む（決定 3）。

    子は `claude` を同じ組で起動するので、組への送信が `claude` まで届く。時間切れ・割り込み・正常な終わりの
    どれでも組を止めてから回収する。回答が得られなければ `kind` が None の `Judged` を返す（決定 6）。
    """
    started = time.monotonic()
    payload = json.dumps(
        {
            "text": text,
            "seconds": seconds,
            "parent_argv": list(launch.argv),
            "project_dir": launch.project_dir,
            "parent_exe": launch.exe,
            "parent_exe_id": list(launch.exe_id),
        },
        ensure_ascii=False,
    )
    try:
        proc = popen(
            [sys.executable, "-P", "-m", CHILD_MODULE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=_child_env(env),
            cwd=str(tables.PLUGIN_ROOT),
        )
    except OSError:
        # 外部コマンド: 子を起動できない環境では文字列の判定へ戻す（決定 6）
        return Judged(None, "launch", _ms(started), 0)
    return _awaited(proc, payload, seconds, started, killpg)


def rest_path(state_dir: Path) -> Path:
    """休みのマーカーのファイル。"""
    return tables.hooks_dir(state_dir) / REST_NAME


def endpoint_scope(env: Mapping[str, str], launch: Launch) -> str:
    """送信先の確かめの対象の名前。判定の子の `endpoint_refusal` の結果を変える材料だけから作る。

    材料は、親の起動引数のうち `endpoint.flags` に当たるオプションの名前（子はこの名前があれば値によらず拒む
    ので値は入れない）・プロジェクトの根・`CLAUDE_CONFIG_DIR`（設定の層の保存先）・`ANTHROPIC_CONFIG_DIR`（子の
    `account_dir.effective_env` がプロファイルを読む保存先。相対パスはプロジェクトの根から解くので値のまま。`endpoint.flags`
    のオプションがあれば子はプロファイルを読む前に拒むので入れない）・名前が
    `endpoint.env_names` に前方一致する環境変数の名前（値は入れない）・親の実行ファイルの実パスと `[st_dev, st_ino]`
    （子は同一性が合わなければ `endpoint` を返すので、差し替えを検出した旧版の休みで新版の判定を止めない。`endpoint.flags`
    のオプションがあれば子は同一性によらず拒むので入れず、更新の後も同じ拒否のために子を起動し直さない）。
    プロンプトなどの位置引数とほかのオプション、API キーなどの値は
    入れないので、同じ起動の形でプロンプトだけが違う `claude -p` は同じ対象になり、マーカーに秘密の材料が残らない。
    引数の読み方は `claude_args` と同じ（`claude_options`）。
    """
    endpoint = _judge("endpoint")
    table = claude_options.table_of(tables.load(claude_options.TABLE))
    flags = sorted({name for name, _ in claude_options.options(table, launch.argv[1:]) if name in endpoint["flags"]})
    names = sorted(name for name in env if name.startswith(tuple(endpoint["env_names"])))
    source = json.dumps(
        {
            "flags": flags,
            "project_dir": launch.project_dir,
            "config_dir": env.get("CLAUDE_CONFIG_DIR"),
            "profile_dir": None if flags else env.get("ANTHROPIC_CONFIG_DIR"),
            "env": names,
            "exe": None if flags else launch.exe,
            "exe_id": None if flags else list(launch.exe_id),
        },
        ensure_ascii=False,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]


def _rest_marker(state_dir: Path) -> Optional[dict[str, Any]]:
    """休みのマーカーの中身。無い・読めない・形が違うときは None。"""
    try:
        text = rest_path(state_dir).read_text(encoding="utf-8")
    except OSError:  # noqa: NDF401 ファイル: マーカーが無い・読めないときは休まない
        return None
    return tables.json_object(text)


def _within(at: Any, now: float) -> bool:
    return isinstance(at, (int, float)) and now - float(at) < float(_judge("rest_seconds"))


def resting(state_dir: Path, now: float) -> bool:
    """起動の失敗から休む間が過ぎていなければ真。親の探索の前に見る。マーカーが無い・読めないときは偽（決定 10）。"""
    marker = _rest_marker(state_dir)
    if marker is None:
        return False
    launch = marker.get("launch")
    return isinstance(launch, dict) and _within(launch.get("at"), now)


def endpoint_resting(state_dir: Path, now: float, env: Mapping[str, str], launch: Launch) -> bool:
    """この対象（`endpoint_scope(env, launch)`）の送信先の不一致から休む間が過ぎていなければ真。親の探索の後に見る。"""
    marker = _rest_marker(state_dir)
    if marker is None:
        return False
    endpoints = marker.get("endpoint")
    return isinstance(endpoints, dict) and _within(endpoints.get(endpoint_scope(env, launch)), now)


def _rest_body(marker: dict[str, Any], failure: str, now: float, scope: str) -> dict[str, Any]:
    """今のマーカーに 1 つの失敗を足した中身。休む間を過ぎた対象は外す。"""
    raw = marker.get("endpoint")
    endpoints = {key: at for key, at in (raw.items() if isinstance(raw, dict) else ()) if _within(at, now)}
    launch = marker.get("launch")
    kept = launch if isinstance(launch, dict) and _within(launch.get("at"), now) else None
    if failure == "endpoint":
        endpoints[scope] = now
    else:
        kept = {"at": now, "failure": failure}
    body: dict[str, Any] = {"endpoint": endpoints}
    if kept is not None:
        body["launch"] = kept
    return body


def mark_rest(state_dir: Path, failure: str, now: float, env: Mapping[str, str], launch: Launch) -> None:
    """起動の失敗と送信先の不一致のときだけ休みのマーカーを書く。書けなくても例外を投げない（決定 10）。

    起動の失敗（`launch`・`refused`・`usage_limit`・`auth`）はどの対象も休む。送信先の不一致（`endpoint`）は
    `endpoint_scope(env, launch)` の対象だけが休む。時間切れとスロットの空き待ちは書かない。
    読んで足して書く間は錠で別のセッションの Stop と直列にし、読み手へは一時ファイルからの置き換えで渡す
    （ファイルは所有者だけが読める 0600）。
    錠を `lock_seconds` で取れなければ書かない（次の失敗でまた書く）。
    """
    if failure not in ("launch", "refused", "usage_limit", "auth", "endpoint"):
        return
    path = rest_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with signals.held(path.with_name(REST_LOCK_NAME), float(_judge("lock_seconds"))) as got:
            if not got:
                return
            marker = _rest_marker(state_dir)
            body = _rest_body({} if marker is None else marker, failure, now, endpoint_scope(env, launch))
            signals.replace_text(path, json.dumps(body, ensure_ascii=False), mode=0o600)
    except OSError:  # noqa: S110 ファイル: マーカーを書けなくても判定は変えない
        pass
