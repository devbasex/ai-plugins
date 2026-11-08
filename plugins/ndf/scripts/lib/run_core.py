"""run_core.py: エントリポイントの実行の核（#2337）。標準ライブラリと同じディレクトリの部品だけで書く。

エントリポイントは末尾で核を呼び、核が本体の終わり方（返った・`step_result.emit` で結果を出した・例外・SIGTERM・
時間切れ）を `ok` / `gate` / `stopped` の結果 JSON と決まった終了コードへ変える。

    # Python のエントリポイント（`__main__` の中身はこの 1 文）
    if __name__ == "__main__":
        run_core.main("scripts/<名前>.py", main)

    # シェルのエントリポイント（先頭の実行文）
    . "$(dirname "$0")/lib/run-core.sh"; rc_enter "scripts/<名前>.sh" "$@"

- id はプラグインの根からのパス。契約の一覧（プラグインの根の `contracts/entrypoints/`）の項目を引き、`stdout` と
  `exit` を核が読む（項目のファイル名は契約の `path` の `/` を `__` にしたもの）
- 契約の `stdout` が `step-result` なら、核が確かめた結果を標準出力へ 1 行出す。それ以外は標準出力へ何も足さず、
  止まった理由を標準エラーへ 1 行出す
- 呼ぶ側が `NDF_RESULT_FILE` でファイルを渡せば、最終の結果 JSON をそこへ 1 行で書く
- `NDF_RUN_TIMEOUT`（秒）があれば期限を置く。`NDF_RESULT_FILE` と `NDF_RUN_TIMEOUT` は子プロセスへ継がない
- 本体より前に止まる部品（`deps.require`）は `exit_early` で同じ規則の結果を出す

規則の正本は同じディレクトリの README.md の「実行の核」。
"""

from __future__ import annotations

import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))
import jsonio  # noqa: E402
import secret_redact  # noqa: E402
import step_result  # noqa: E402

PLUGIN_ROOT = LIB.parents[1]
CONTRACTS = PLUGIN_ROOT / "contracts" / "entrypoints"
# 契約の項目のファイル名の頭（契約の path はマーケットプレイスの根からのパスで、プラグインの根は plugins の下の ndf）
CONTRACT_FILE_HEAD = "plugins__ndf__"
ENTRY_ENV = "NDF_RUN_ENTRY"
TIMEOUT_ENV = "NDF_RUN_TIMEOUT"
RESULT_ENV = "NDF_RESULT_FILE"
KILL_GRACE = 5.0
STEP_RESULT = "step-result"


@dataclass
class RunContext:
    entry_id: str
    run_id: str
    argv: list[str]
    deadline: float | None
    stdout_kind: str
    exit_codes: frozenset[int]
    result_file: str | None


_CTX: RunContext | None = None


def run_context() -> RunContext | None:
    """動いている実行の文脈（核の外では None）。"""
    return _CTX


class _Interrupt(BaseException):
    """SIGTERM・時間切れで本体を中断した（`except Exception` に握られないよう BaseException の子にする）。"""

    def __init__(self, kind: str, summary: str):
        super().__init__(summary)
        self.kind, self.summary = kind, summary


# --- 契約 -----------------------------------------------------------------------


def contract_of(entry_id: str) -> dict | None:
    """id（プラグインの根からのパス）の契約の項目。無い・読めなければ None。"""
    f = CONTRACTS / (CONTRACT_FILE_HEAD + entry_id.replace("/", "__") + ".json")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _exit_codes(contract: dict) -> frozenset[int]:
    return frozenset(int(k) for k in contract.get("exit", {}) if str(k).lstrip("-").isdigit())


def entry_of_script(script: str) -> str:
    """スクリプトのパスから id（プラグインの根からのパス）を決める。根の外なら空。"""
    try:
        return Path(script).resolve().relative_to(PLUGIN_ROOT).as_posix()
    except ValueError:
        return ""


# --- 結果の組み立て ---------------------------------------------------------------


def status_of_code(code: int) -> str | None:
    """結果 JSON が無い終わり方の状態。0 は ok、10〜29 は gate、表の stopped の値は stopped、それ以外は None。"""
    for st in step_result.STATUSES:
        if step_result.code_matches(st, code):
            return st
    return None


def _tool(entry_id: str) -> str:
    return Path(entry_id).stem or "run-core"


def core_stopped(entry_id: str, kind: str, name: str, summary: str, code: int = step_result.EXIT_VIOLATION) -> tuple[dict, int]:
    gap = {"kind": kind, "name": name or kind, "reason": summary}
    return step_result.result(_tool(entry_id), "stopped", summary, gaps=[gap]), code


def _from_code(entry_id: str, code: int) -> tuple[dict, int]:
    """本体が結果 JSON を出さずに終えたとき、終了コードから結果を組む（標準出力へは出さない）。"""
    st = status_of_code(code)
    if st is None:
        return core_stopped(entry_id, "result-shape", "exit", f"終了コード {code} は終了コードの表に無い")
    summary = f"終了コード {code}（{step_result.label_of(code)}）"
    if st == "gate":
        return step_result.result(_tool(entry_id), st, summary, next=f"{summary}。本体の出力を読む"), code
    if code == step_result.EXIT_INCOMPLETE:
        return core_stopped(entry_id, "incomplete", "exit", summary, code)
    return step_result.result(_tool(entry_id), st, summary), code


def verify_emitted(entry_id: str, obj, code: int, exit_codes: frozenset[int] | None) -> tuple[dict, int, bool]:
    """本体の結果を確かめ、(結果, 終了コード, 置き換えたか) を返す。I2（ok に欠け）と I3（形・食い違い・契約に無い終了コード）。"""
    if isinstance(obj, dict) and obj.get("status") == "ok" and isinstance(obj.get("gaps"), list) and obj["gaps"]:
        obj, code = dict(obj, status="stopped"), step_result.EXIT_INCOMPLETE
    errs = step_result.validate_result(obj, code)
    if errs:
        return (*core_stopped(entry_id, "result-shape", "result", "結果の形が誤っている: " + " / ".join(errs)), True)
    if exit_codes is not None and code not in exit_codes:
        return (*core_stopped(entry_id, "result-shape", "exit", f"終了コード {code} は契約の exit に無い"), True)
    return obj, code, False


# --- 出力 --------------------------------------------------------------------------


def _write_result_file(path: str | None, obj: dict) -> None:
    if not path:
        return
    try:
        jsonio.write_atomic(path, obj, indent=None)
    except OSError as e:
        print(f"[ndf run-core] 結果ファイルへ書けない: {path}: {e}", file=sys.stderr)


def deliver(obj: dict, *, stdout_kind: str, print_result: bool, result_file: str | None, run_id: str | None) -> None:
    """結果を出す。`step-result` で print_result なら標準出力へ 1 行。それ以外で stopped なら理由を標準エラーへ 1 行。"""
    if run_id:
        obj = dict(obj, run_id=run_id)
    if print_result and stdout_kind == STEP_RESULT:
        print(json.dumps(obj, ensure_ascii=False), flush=True)
    elif print_result and obj.get("status") == "stopped":
        print(f"[ndf run-core] {obj['tool']}: {obj['summary']}", file=sys.stderr, flush=True)
    _write_result_file(result_file, obj)


def _flush() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.flush()
        except (OSError, ValueError):
            pass


def _leave(code: int, interrupted: bool) -> NoReturn:
    """中断で耐久の記録が開いていれば DBOS を閉じずに抜ける（決定 9）。`durable` は読まれているときだけ見る。"""
    durable = sys.modules.get("durable")
    if interrupted and durable is not None and getattr(durable, "launched", lambda: None)() is not None:
        durable.exit_leaving_pending(code)
    _flush()
    raise SystemExit(code)


def exit_early(summary: str, code: int, gaps: list[dict] | None = None) -> NoReturn:
    """核に入る前（`deps.require` など）の止まりを、核と同じ規則の結果にして終える。"""
    ctx = _CTX
    entry_id = ctx.entry_id if ctx else entry_of_script(sys.argv[0])
    contract = contract_of(entry_id) if entry_id else None
    kind = ctx.stdout_kind if ctx else (contract or {}).get("stdout", "")
    result_file = ctx.result_file if ctx else os.environ.pop(RESULT_ENV, None)
    obj = step_result.result(_tool(entry_id), "stopped", summary, gaps=gaps or [{"kind": "stopped", "name": "exit", "reason": summary}])
    deliver(obj, stdout_kind=kind, print_result=kind == STEP_RESULT, result_file=result_file, run_id=ctx.run_id if ctx else None)
    _flush()
    raise SystemExit(code)


# --- シグナルと期限 ----------------------------------------------------------------


def _timeout_seconds() -> float | None:
    raw = os.environ.pop(TIMEOUT_ENV, None)
    try:
        sec = float(raw) if raw else None
    except ValueError:
        print(f"[ndf run-core] {TIMEOUT_ENV} が数でない: {raw!r}（期限を置かない）", file=sys.stderr)
        return None
    return sec if sec and sec > 0 else None


class _Guards:
    """SIGTERM と時間切れの受け手を主スレッドに置き、抜けるときに戻す。"""

    def __init__(self, timeout: float | None):
        self.timeout, self.saved = timeout, {}
        self.active = threading.current_thread() is threading.main_thread()

    def _raise_term(self, *_):
        raise _Interrupt("interrupted", "SIGTERM で中断した")

    def _raise_alarm(self, *_):
        raise _Interrupt("timeout", f"{self.timeout:g} 秒の上限で中断した")

    def __enter__(self):
        if not self.active:
            return self
        self.saved[signal.SIGTERM] = signal.signal(signal.SIGTERM, self._raise_term)
        if self.timeout:
            self.saved[signal.SIGALRM] = signal.signal(signal.SIGALRM, self._raise_alarm)
            signal.setitimer(signal.ITIMER_REAL, self.timeout)
        return self

    def __exit__(self, *exc):
        if not self.active:
            return False
        if self.timeout:
            signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, h in self.saved.items():
            signal.signal(sig, h)
        return False


def _begin(entry_id: str, argv: list[str]) -> tuple[RunContext | None, tuple[dict, int] | None]:
    """文脈を作る。契約に無ければ止まりの結果を返す。"""
    global _CTX
    result_file = os.environ.pop(RESULT_ENV, None)
    timeout = _timeout_seconds()
    contract = contract_of(entry_id)
    if contract is None:
        stub = RunContext(entry_id, uuid.uuid4().hex[:12], argv, None, STEP_RESULT, frozenset(), result_file)
        _CTX = stub
        return stub, core_stopped(entry_id, "contract", entry_id, f"契約の一覧に無い: {entry_id}", step_result.EXIT_UNREADABLE)
    deadline = time.monotonic() + timeout if timeout else None
    _CTX = RunContext(entry_id, uuid.uuid4().hex[:12], argv, deadline, contract.get("stdout", ""), _exit_codes(contract), result_file)
    return _CTX, None


# --- Python のエントリポイント ----------------------------------------------------


def _call(body: Callable, argv: list[str]):
    try:
        params = inspect.signature(body).parameters
    except (TypeError, ValueError):
        params = {"argv": None}
    return body(argv) if params else body()


def _system_exit_code(e: SystemExit) -> int:
    if e.code is None:
        return 0
    if isinstance(e.code, int):
        return e.code
    print(e.code, file=sys.stderr)
    return 1


def run_body(entry_id: str, body: Callable, argv: list[str] | None = None) -> tuple[dict, int, bool, bool]:
    """本体を核の上で動かし、(結果, 終了コード, 標準出力へ出すか, 中断したか) を返す（出さない）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    ctx, early = _begin(entry_id, argv)
    if early is not None:
        return (*early, True, False)
    timeout = (ctx.deadline - time.monotonic()) if ctx.deadline else None
    step_result.CAPTURE = True
    try:
        with _Guards(timeout):
            try:
                ret = _call(body, argv)
            except step_result.Emitted as e:
                obj, code, _ = verify_emitted(entry_id, e.result, e.code, ctx.exit_codes)
                return obj, code, True, False
    except _Interrupt as e:
        return (*core_stopped(entry_id, e.kind, e.kind, e.summary), True, True)
    except step_result.Emitted as e:  # 受け手を戻す間に届いた結果
        obj, code, _ = verify_emitted(entry_id, e.result, e.code, ctx.exit_codes)
        return obj, code, True, False
    except SystemExit as e:
        code = _system_exit_code(e)
        return _no_result(entry_id, ctx, code)
    except BaseException as e:  # noqa: BLE001 例外は結果に変え、例外の跡を標準エラーへ出す
        traceback.print_exc()
        text = secret_redact.redact_output(f"{type(e).__name__}: {e}", max_lines=3)
        return (*core_stopped(entry_id, "exception", type(e).__name__, text), True, False)
    finally:
        step_result.CAPTURE = False
    if ret is not None and (isinstance(ret, bool) or not isinstance(ret, int)):
        return (*core_stopped(entry_id, "result-shape", "return", f"本体の戻り値が終了コードでない: {type(ret).__name__}"), True, False)
    if ctx.stdout_kind == STEP_RESULT:
        return (*core_stopped(entry_id, "result-shape", "result", "本体が結果 JSON を出さずに返った"), True, False)
    return _no_result(entry_id, ctx, 0 if ret is None else ret)


def _no_result(entry_id: str, ctx: RunContext, code: int) -> tuple[dict, int, bool, bool]:
    """結果 JSON の無い終わり方。終了コードが表か契約から外れれば stopped・1 を標準出力の規則どおり出す。"""
    if status_of_code(code) is None or (ctx.exit_codes and code not in ctx.exit_codes):
        where = "終了コードの表" if status_of_code(code) is None else "契約の exit"
        return (*core_stopped(entry_id, "result-shape", "exit", f"終了コード {code} は{where}に無い"), True, False)
    obj, code = _from_code(entry_id, code)
    return obj, code, False, False


def main(entry_id: str, body: Callable, argv: list[str] | None = None) -> NoReturn:
    """エントリポイントの `__main__` が呼ぶ。本体を動かし、結果を出して終える。"""
    obj, code, show, interrupted = run_body(entry_id, body, argv)
    ctx = _CTX
    deliver(obj, stdout_kind=ctx.stdout_kind, print_result=show, result_file=ctx.result_file, run_id=ctx.run_id)
    _leave(code, interrupted)


# --- シェルのエントリポイント（`run-core.sh` の `rc_enter` が再実行する） ----------------


def _kill_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        p.wait(timeout=KILL_GRACE)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        p.wait()


def _pump(p: subprocess.Popen, held: list[str]) -> None:
    """子の標準出力を流し、最後の行だけを持っておく。"""
    for line in p.stdout:
        if held:
            sys.stdout.write(held.pop())
            sys.stdout.flush()
        held.append(line)


def run_shell(entry_id: str, script: str, args: list[str]) -> tuple[dict, int, bool, bool]:
    ctx, early = _begin(entry_id, list(args))
    if early is not None:
        return (*early, True, False)
    env = dict(os.environ, **{ENTRY_ENV: entry_id})
    capture = ctx.stdout_kind == STEP_RESULT
    held: list[str] = []
    timeout = (ctx.deadline - time.monotonic()) if ctx.deadline else None
    p = subprocess.Popen(["bash", script, *args], env=env, stdout=subprocess.PIPE if capture else None, text=True, start_new_session=True)
    try:
        with _Guards(timeout):
            if capture:
                _pump(p, held)
            rc = p.wait()
    except _Interrupt as e:
        _kill_group(p)
        if held:
            sys.stdout.write(held.pop())
        return (*core_stopped(entry_id, e.kind, e.kind, e.summary), True, True)
    last = held[-1] if held else ""
    obj = _json_line(last)
    if capture and obj is not None:
        res, code, _ = verify_emitted(entry_id, obj, rc, ctx.exit_codes)
        return res, code, True, False
    if last:
        sys.stdout.write(last)
        sys.stdout.flush()
    if rc < 0:
        return (*core_stopped(entry_id, "interrupted", "signal", f"シグナル {-rc} で終わった"), True, False)
    return _no_result(entry_id, ctx, rc)


def _json_line(line: str):
    try:
        obj = json.loads(line) if line.strip().startswith("{") else None
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def exec_main(argv: list[str]) -> NoReturn:
    """`run_core.py exec <id> -- <スクリプト> <引数>`。"""
    if len(argv) < 3 or argv[0] != "exec" or "--" not in argv:
        print("使い方: run_core.py exec <契約の path> -- <スクリプト> [引数...]", file=sys.stderr)
        raise SystemExit(step_result.EXIT_UNREADABLE)
    sep = argv.index("--")
    entry_id, rest = argv[1], argv[sep + 1 :]
    if not rest:
        print("スクリプトが無い", file=sys.stderr)
        raise SystemExit(step_result.EXIT_UNREADABLE)
    obj, code, show, interrupted = run_shell(entry_id, rest[0], rest[1:])
    ctx = _CTX
    deliver(obj, stdout_kind=ctx.stdout_kind, print_result=show, result_file=ctx.result_file, run_id=ctx.run_id)
    _flush()
    raise SystemExit(code)


if __name__ == "__main__":
    exec_main(sys.argv[1:])
