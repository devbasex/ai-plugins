#!/usr/bin/env python3
"""external-ai.py: 外部 CLI（codex / agy / kiro / claude）の起動・上限つきの待ち・回収を 1 本で行う。

    external-ai.py check <runtime>
    external-ai.py run <runtime> --prompt-file P --output-file O [--phase review]
                       [--model M] [--workdir D] [--timeout 秒] [--stall-timeout 秒]

`run` は共通層の `launch-cli.sh` で背景起動し、`monitor.py` で上限つきで監視し、
結果ファイル → stdout → stderr の順に回収する。最後に 1 行の JSON（`step_result` の形）を出す。

    {"tool": "external-ai", "status": "ok|stopped", "summary": "...",
     "items": [{"kind": "cli", "name": "codex", "result": "ok|no_result|timeout|stalled|
                early_error|usage_limit|auth|missing_cli|launch_failed", ...}],
     "metrics": {"outcome": "...", "result": "out.md", "source": "file|stdout|stderr",
                 "runtime": "codex", "model": "...", "monitor_status": "OK", "reason": "ok", ...}}

`outcome` が `ok` のときだけ `status` は `ok`（終了コード 0）。回収した本文は必ず
`--output-file` に置く。監視の結果（`<stem>-monitor.json`）の状態と理由を `metrics` に写す。
待ちの上限は `limits.py` の工程の値で、上限を超えると必ず終わる。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

LIB = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import auth  # noqa: E402
import limits  # noqa: E402
import models  # noqa: E402
import monitor_outcome  # noqa: E402
import step_result as sr  # noqa: E402

TOOL = "external-ai"
RUNTIMES = ("codex", "agy", "kiro", "claude")
EXECUTABLE = {"codex": "codex", "agy": "agy", "kiro": "kiro-cli", "claude": "claude"}
STEM_TEMPLATE = "{agent}-ext{id}"
TMP_ENV = "NDF_EXTERNAL_AI_TMP_DIR"
ERR_TAIL_LINES = 200
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
AUTH_DETAIL = re.compile(
    r"HTTP/\S+ (?:401|403)|Authentication failed|Permission denied|API key|Unauthorized",
    re.IGNORECASE)

# 監視の状態 → 結末。どれも `ok` に畳まない。
MONITOR_OUTCOME = {
    "TIMEOUT": "timeout",
    "STALLED": "stalled",
    "EARLY_ERROR": "early_error",
    "PIDFILE_BAD": "launch_failed",
}


def tmp_dir(arg: str | None) -> pathlib.Path:
    d = pathlib.Path(arg or os.environ.get(TMP_ENV)
                     or pathlib.Path(tempfile.gettempdir()) / "ndf" / "external-ai")
    d = d.resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def finish(runtime: str, outcome: str, summary: str, metrics: dict, code: int | None = None,
           next_: str | None = None):
    status = "ok" if outcome == "ok" else "stopped"
    metrics = {"outcome": outcome, "runtime": runtime, **metrics}
    item = {"kind": "cli", "name": runtime, "result": outcome}
    sr.emit(sr.result(TOOL, status, summary, [item], metrics, next=next_),
            code if code is not None else sr.default_code(status))


def precheck(runtime: str, skip_auth: bool) -> tuple[str, str] | None:
    """前提を確かめる。通らなければ `(結末, 理由)` を返す。"""
    if shutil.which(EXECUTABLE[runtime]) is None:
        return "missing_cli", f"{EXECUTABLE[runtime]} が PATH に無い"
    if skip_auth:
        return None
    results, skipped = auth.probe_auth([runtime], info=lambda m: print(m, file=sys.stderr))
    if skipped:
        return None
    r = results.get(runtime)
    if r and not r["ok"]:
        return "auth", f"{r['command']} が通らない: {r['detail']}"
    return None


def cmd_check(a) -> None:
    pre = precheck(a.runtime, False)
    if pre:
        finish(a.runtime, pre[0], f"{a.runtime} は使えない（{pre[1]}）", {}, sr.EXIT_PRECONDITION)
    finish(a.runtime, "ok", f"{a.runtime} は使える", {})


def read_stdout(runtime: str, path: pathlib.Path) -> str:
    """stdout から本文を取り出す。claude は JSON の `result`、kiro は ANSI を除く。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if runtime == "kiro":
        text = ANSI.sub("", text)
    if runtime == "claude":
        import json
        try:
            payload = json.loads(text)
        except ValueError:
            return ""
        if not isinstance(payload, dict) or payload.get("is_error"):
            return ""
        text = payload.get("result") or ""
    return text if text.strip() else ""


def recover(runtime: str, stem: pathlib.Path, output: pathlib.Path) -> tuple[str | None, str | None]:
    """三段の回収。`(source, 本文のパス)` を返す。stderr は結果なしの手がかりとして返す。"""
    if output.is_file() and output.stat().st_size > 0:
        return "file", str(output)
    body = read_stdout(runtime, pathlib.Path(f"{stem}-stdout.log"))
    if body:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")
        return "stdout", str(output)
    err = pathlib.Path(f"{stem}-err.log")
    if err.is_file() and err.stat().st_size > 0:
        lines = err.read_text(encoding="utf-8", errors="replace").splitlines()[-ERR_TAIL_LINES:]
        text = "\n".join(lines)
        if runtime == "kiro":
            text = ANSI.sub("", text)
        tail = pathlib.Path(f"{stem}-err-tail.log")
        tail.write_text(text + "\n", encoding="utf-8")
        return "stderr", str(tail)
    return None, None


def with_output_instruction(prompt: str, output: pathlib.Path) -> str:
    if str(output) in prompt:
        return prompt
    return (prompt.rstrip("\n") + "\n\n## 出力先（必須）\n"
            f"最終結果を `{output}` に書き出したうえで、stdout にも同内容を出力すること。"
            "tool 呼び出しのみで終了せず、最後に必ず 1 回出力すること。\n")


def cmd_run(a) -> None:
    runtime = a.runtime
    prompt = pathlib.Path(a.prompt_file)
    if not prompt.is_file() or prompt.stat().st_size == 0:
        finish(runtime, "launch_failed", f"プロンプトが無いか空: {prompt}", {}, sr.EXIT_PRECONDITION)
    if a.phase not in limits.PHASE_TIMEOUT:
        finish(runtime, "launch_failed",
               f"上限の表に無い工程: {a.phase}（{' / '.join(limits.PHASE_TIMEOUT)}）", {},
               sr.EXIT_UNREADABLE)
    workdir = pathlib.Path(a.workdir or os.getcwd()).resolve()
    if not workdir.is_dir():
        finish(runtime, "launch_failed", f"作業ディレクトリが無い: {workdir}", {},
               sr.EXIT_PRECONDITION)
    skip_auth = a.no_auth_check or bool(os.environ.get(auth.SKIP_ENV))
    pre = precheck(runtime, skip_auth)
    if pre:
        finish(runtime, pre[0], f"{runtime} を起動しない（{pre[1]}）", {}, sr.EXIT_PRECONDITION)

    output = pathlib.Path(a.output_file).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)  # 前の実行の結果を今回のものと読まない
    tdir = tmp_dir(a.tmp_dir)
    run_id = time.time_ns() // 1000 % 10**12
    stem_name = STEM_TEMPLATE.format(agent=runtime, id=run_id)
    stem = tdir / stem_name
    prompt_copy = pathlib.Path(f"{stem}-prompt.md")
    prompt_copy.write_text(with_output_instruction(
        prompt.read_text(encoding="utf-8"), output), encoding="utf-8")

    cli_limit = (str(a.timeout + limits.CLI_MARGIN) if a.timeout else a.phase)
    launch = [str(LIB / "launch-cli.sh"), runtime, str(workdir), str(prompt_copy), str(stem),
              a.model or "", str(output.parent), cli_limit]
    p = subprocess.run(launch, capture_output=True, text=True)
    if p.returncode != 0:
        finish(runtime, "launch_failed", f"起動できない: {p.stderr.strip()[:300]}",
               {"stem": str(stem)})
    # 監視は `<stem>-result.json` の有無で結果を見る。結果ファイルへのリンクにして、
    # 結果ファイルが書かれたことを完了の証拠にする。
    link = pathlib.Path(f"{stem}-result.json")
    link.unlink(missing_ok=True)
    link.symlink_to(output)

    mon = [sys.executable, str(LIB / "monitor.py"), str(run_id), "--agents", runtime,
           "--tmp-dir", str(tdir), "--stem-template", STEM_TEMPLATE, "--phase", a.phase,
           "--poll", str(a.poll)]
    if a.timeout:
        mon += ["--timeout", str(a.timeout)]
    if a.stall_timeout:
        mon += ["--stall-timeout", str(a.stall_timeout)]
    subprocess.run(mon, stdout=subprocess.DEVNULL)

    rec = monitor_outcome.read_outcome(tdir, stem_name) or {}
    mstatus, reason = rec.get("status", "PIDFILE_BAD"), rec.get("reason", "pidfile_bad")
    source, path = recover(runtime, stem, output)
    stdout_log = pathlib.Path(f"{stem}-stdout.log")
    observed = models.observed_model(
        runtime, stdout_log.read_text(encoding="utf-8", errors="replace")
        if stdout_log.is_file() else "")
    metrics = {"result": path, "source": source, "model": observed or a.model or "default",
               "monitor_status": mstatus, "reason": reason, "detail": rec.get("detail", ""),
               "elapsed": rec.get("elapsed"), "phase": a.phase, "stem": str(stem)}

    if mstatus in ("OK", "NO_RESULT") and source in ("file", "stdout"):
        finish(runtime, "ok", f"{runtime} の結果を回収した（{source}）: {path}", metrics)
    if mstatus in ("OK", "NO_RESULT"):
        finish(runtime, "no_result",
               f"{runtime} は終わったが結果が無い（理由: {reason}）", metrics,
               next_=f"stderr の末尾を読む: {path}" if path else None)
    outcome = MONITOR_OUTCOME.get(mstatus, "launch_failed")
    if reason == "usage_limit":
        outcome = "usage_limit"
    elif outcome == "early_error" and AUTH_DETAIL.search(rec.get("detail", "")):
        outcome = "auth"
    finish(runtime, outcome, f"{runtime} を止めた（{mstatus} / 理由: {reason}）", metrics,
           next_=f"stderr の末尾を読む: {path}" if path else None)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="CLI があり認証が通るか")
    c.add_argument("runtime", choices=RUNTIMES)
    c.set_defaults(func=cmd_check)
    r = sub.add_parser("run", help="起動・上限つきの待ち・回収")
    r.add_argument("runtime", choices=RUNTIMES)
    r.add_argument("--prompt-file", required=True)
    r.add_argument("--output-file", required=True)
    r.add_argument("--phase", default=limits.DEFAULT_PHASE,
                   help=f"上限の表の工程（{' / '.join(limits.PHASE_TIMEOUT)}）")
    r.add_argument("--model", default=None)
    r.add_argument("--workdir", default=None, help="CLI の作業ディレクトリ（既定はカレント）")
    r.add_argument("--timeout", type=int, default=None, help="監視の上限（秒）。既定は工程の値")
    r.add_argument("--stall-timeout", type=int, default=None, help="無進捗の許容（秒）")
    r.add_argument("--poll", type=int, default=15, help="監視の周期（秒）")
    r.add_argument("--tmp-dir", default=None, help=f"一時ファイルの置き場所（env: {TMP_ENV}）")
    r.add_argument("--no-auth-check", action="store_true", help="認証の確認を飛ばす")
    r.set_defaults(func=cmd_run)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
