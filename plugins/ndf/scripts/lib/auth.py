"""参加する CLI の参加の確認（収束ループ共通層）。

**認証が通るだけでは足りない。** 設定のモデルを引けない CLI や更新トークンが失効した CLI も
認証の状態確認は通り、担当に入ると結果を残さずに終わってラウンドを潰す（#461・#1589）。
参加の確認は参加者ごとに、認証確認（種類 `auth`）→ 最小の呼び出し（種類 `model`）→
（設定のモデルを引けなければ）既定のモデルでの引き直し（種類 `default`）を順に行う。

この層は確認のコマンドの表・1 回の確認の実行と分類（`run_check`）・参加者ごとの並行の実行と
出力（`probe_auth`）を持つ。**どこまで確かめ、担当に入れるか・既定のモデルへ切り替えるかの判断は
`assignment.admit` が持つ**（#1290 の決定 4）。分類の文言は監視と同じ `monitor_patterns` の表を読む（I8）。

`cross-refactoring` と `cross-review` の `init`、`external-ai.py check` が同じ確認を通る（#852）。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Optional

import assignment
import claude_settings
import monitor_patterns
import secret_redact

ProbeResult = dict[str, dict[str, Any]]

# 認証状態の確認コマンド（種類 `auth`）。CLI ごとに、認証を通ったときだけ成功する最も短い操作を選ぶ。
AUTH_PROBES: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "auth", "status"),
    "codex": ("codex", "login", "status"),
    # agy は認証を通ったときだけモデルの一覧を返す。プロンプトを投げる形より
    # 短く終わり、モデルの呼び出しを 1 回消費しない。
    "agy": ("agy", "models"),
    "kiro": ("kiro-cli", "whoami"),
}

# 最小の呼び出し（種類 `model`）。担当の起動と同じ CLI で、`PROBE_PROMPT` を標準入力から 1 回答えさせる。
# 応答の中身は見ない。claude は道具・MCP・Skill・会話の保存を切って費用を下げる（2026-10-06 の実測で
# 0.44 → 0.02 米ドル）。codex の `{workdir}` は確認を走らせるディレクトリ（担当と同じプロジェクト）に置き換える。
# 確認はプロジェクトのディレクトリで走らせて設定を継ぐが、指示書は読ませない（`PROBE_ENV` と codex の
# `project_doc_max_bytes=0`）。2026-10-06 の実測（ai-plugins で 1 回、キャッシュ無し）で、claude は CLAUDE.md を
# 読むと 0.114 米ドル（キャッシュ書き込み 14.3k）、読ませないと 0.020 米ドル（2.4k）。codex は 10.7k → 6.1k トークン。
# kiro の steering を外す引数は無いため、kiro だけは指示書を読む。
# agy はこの表に無い（応答の形を測れていない。#1290 の決定 1）ため、認証確認だけで担当に入る。
MODEL_PROBES: dict[str, tuple[str, ...]] = {
    "claude": (
        "claude",
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--system-prompt",
        "Answer briefly.",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ),
    "codex": (
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "-s",
        "read-only",
        "-c",
        "project_doc_max_bytes=0",
        "-C",
        "{workdir}",
    ),
    "kiro": ("kiro-cli", "chat", "--no-interactive"),
}
# 最小の呼び出しに足す環境変数。claude にプロジェクトの CLAUDE.md を読ませない（設定は読む）
PROBE_ENV: dict[str, dict[str, str]] = {"claude": {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}}
# 明示のモデルを渡す引数（種類 `model` に足す）
MODEL_FLAG = "--model"
# 既定のモデルで引き直す引数（種類 `default`。種類 `model` に足す）。どれも利用者の設定ファイルを
# 書き換えない（C6）。kiro は `--model` を受けず自ら既定のモデルで答えるため持たない（決定 2・決定 3）。
# codex の `--ignore-user-config` は提供元（`model_provider` など）も外すが、担当は利用者の設定のまま
# `--model <既定のモデル>` で起動する。そのため codex は、読めた既定のモデルを利用者の設定のまま
# `--model` で引き直して通ったときだけ通す（`CONFIRM_DEFAULT`）。
DEFAULT_MODEL_ARGS: dict[str, tuple[str, ...]] = {
    "claude": ("--model", "default"),
    "codex": ("--ignore-user-config",),
}
# 既定のモデルを、担当の起動と同じ形（利用者の設定のまま `--model <名前>`）で確かめ直す CLI
CONFIRM_DEFAULT = frozenset({"codex"})
# claude は `--model default` を起動の引数としてそのまま受ける（決定 2）
CLAUDE_DEFAULT_MODEL = "default"
PROBE_PROMPT = "Reply with the single word OK."

# 1 者の持ち時間（秒）。3 種類の確認で共有する（#1290 の決定 8）
AUTH_PROBE_TIMEOUT = 120

# **終了コード 0 でも未認証を示すことがある。** kiro は成否を終了コードで表さない。
UNAUTHENTICATED_MARKERS = (
    "not logged in",
    "not authenticated",
    "authentication failed",
    "login required",
    "unauthorized",
    "please log in",
)
# kiro-cli 2.24.1 は `--model` を受けず、警告だけ出して既定のモデルで答える（決定 3）
KIRO_MODEL_REJECTED = re.compile(r"failed to set model")
# codex が標準エラーの見出しに出す、応答したモデルの名前
CODEX_MODEL_HEADER = re.compile(r"^model:\s*(\S+)", re.MULTILINE)
DETAIL_CHARS = 200

SKIP_ENV = "NDF_SKIP_AUTH_CHECK"


class Check(NamedTuple):
    """1 回の確認の結果。`reason` は `ok` か参加の確認の理由の語（`assignment.CHECK_REASONS`）。"""

    step: str
    ok: bool
    reason: str
    detail: str
    seconds: float
    model: Optional[str]
    command: str


def _skipped(env: Optional[Mapping[str, str]], info: Callable[[str], None]) -> bool:
    """`NDF_SKIP_AUTH_CHECK` が立っているか。立っていれば飛ばしたことを出力へ残す。"""
    environ = os.environ if env is None else env
    if not environ.get(SKIP_ENV):
        return False
    info(f"⚠ {SKIP_ENV} が設定されているため参加の確認を飛ばしました")
    return True


def _command(step: str, runtime: str, model: Optional[str], workdir: str) -> Optional[tuple[str, ...]]:
    """種類と明示のモデルから確認のコマンドを組み立てる。その CLI が種類を持たなければ `None`。"""
    if step == "auth":
        return AUTH_PROBES.get(runtime)
    base = MODEL_PROBES.get(runtime)
    if base is None:
        return None
    cmd = tuple(workdir if a == "{workdir}" else a for a in base)
    if step == "model":
        return cmd + ((MODEL_FLAG, model) if model else ())
    extra = DEFAULT_MODEL_ARGS.get(runtime)
    return None if extra is None else cmd + extra


def _detail(text: str) -> str:
    """詳細の 1 文。秘密を伏せてから先頭 `DETAIL_CHARS` 文字に切る（非機能のセキュリティ）。"""
    redacted = secret_redact.redact_output(text.strip(), max_lines=10**6, max_chars=10**9)
    return redacted.strip()[:DETAIL_CHARS]


def _first_hit(patterns: Iterable[re.Pattern[str]], text: str) -> Optional[str]:
    """表の最初の一致を含む行を返す。"""
    for pat in patterns:
        m = pat.search(text)
        if m:
            start = text.rfind("\n", 0, m.start()) + 1
            end = text.find("\n", m.end())
            return text[start : end if end != -1 else len(text)]
    return None


def _observed_model(step: str, runtime: str, stdout: str, stderr: str) -> Optional[str]:
    """応答した（引けなかった）モデルの名前。種類 `default` の claude は起動の引数 `default` を返す。"""
    if runtime == "codex":
        m = CODEX_MODEL_HEADER.search(stderr)
        return m.group(1) if m else None
    if runtime == "claude":
        if step == "default":
            return CLAUDE_DEFAULT_MODEL
        m = re.search(r'"modelUsage"\s*:\s*\{\s*"([^"]+)"', stdout)
        return m.group(1) if m else None
    return None


def _classify_output(step: str, runtime: str, model: Optional[str], rc: int, stdout: str, stderr: str) -> tuple[str, Optional[str]]:
    """確認の出力を分類の順序（設計の契約）で照らし、`(理由の語, 当たった行)` を返す。"""
    merged = f"{stdout}\n{stderr}"
    claude_stdout = stdout if runtime == "claude" else ""
    hit = _first_hit(monitor_patterns.AUTH_EXPIRED_FATAL, merged)
    if hit:
        return "auth_expired", hit
    hit = _first_hit(monitor_patterns.MODEL_UNAVAILABLE_FATAL, merged) or _first_hit(
        monitor_patterns.CLAUDE_STDOUT_MODEL_UNAVAILABLE, claude_stdout
    )
    if not hit and runtime == "kiro" and model and step == "model":
        hit = _first_hit([KIRO_MODEL_REJECTED], merged)
    if hit:
        return "model_unavailable", hit
    # 利用上限は通す。上限の扱いは監視と `after_no_result` に任せる（決定 6）
    if _first_hit(monitor_patterns.USAGE_LIMIT_FATAL, merged) or _first_hit(monitor_patterns.CLAUDE_STDOUT_USAGE_LIMIT, claude_stdout):
        return "ok", None
    if any(m in merged.lower() for m in UNAUTHENTICATED_MARKERS):
        return "unauthenticated", None
    empty_reply = runtime == "kiro" and step != "auth" and not stdout.strip()
    if rc != 0 or empty_reply:
        return ("unauthenticated" if step == "auth" else "probe_failed"), None
    return "ok", None


def _launch_args(runtime: str, cmd: tuple[str, ...], cwd: str) -> list[str]:
    """担当の起動と同じ設定の上書きを足した引数。claude には従量の接続の宣言を `--settings` でも渡す（#1543）。"""
    if runtime != "claude":
        return list(cmd)
    return claude_settings.metered_settings(list(cmd), dict(os.environ), cwd, 1)


def run_check(
    step: str, runtime: str, model: Optional[str] = None, *, timeout: float = AUTH_PROBE_TIMEOUT, cwd: Optional[str] = None
) -> Optional[Check]:
    """確認を 1 回走らせて分類する。その CLI が種類を持たなければ `None`。例外は上げない。

    **起動できない理由が何であっても「通らない」として返す**（#813）。PATH に読めない
    ディレクトリがあると、コマンドがどこにも無いときに権限の例外が上がる。

    環境は親から継承し、引数でも環境変数でも認証の情報を渡さない。種類 `model` / `default` は
    担当と同じ設定で確かめるため `cwd`（省けば今のディレクトリ。担当のプロジェクト）で走らせ、
    claude には担当の起動と同じ設定の上書き（`claude_settings.metered_settings`）を足す。指示書は
    読ませない（claude は `PROBE_ENV`、codex は `project_doc_max_bytes=0`）。
    どの確認も道具を持たないか読み取りだけで、利用者の CLI の設定ファイルを書き換えない（I6）。
    codex の種類 `default` は、読めた既定のモデルを利用者の設定のまま引き直して確かめる（`CONFIRM_DEFAULT`）。
    """
    workdir = os.path.abspath(cwd or os.getcwd())
    cmd = _command(step, runtime, model, workdir)
    if cmd is None:
        return None
    deadline = time.monotonic() + max(timeout, 0.1)
    first = _probe_once(step, runtime, model, cmd, workdir, deadline)
    if step != "default" or runtime not in CONFIRM_DEFAULT or not first.ok or not first.model:
        return first
    confirm = _probe_once("model", runtime, first.model, _command("model", runtime, first.model, workdir) or (), workdir, deadline)
    seconds = round(first.seconds + confirm.seconds, 1)
    command = f"{first.command} && {confirm.command}"
    if not confirm.ok:
        return confirm._replace(step=step, seconds=seconds, command=command)
    return first._replace(seconds=seconds, command=command)


def _probe_once(step: str, runtime: str, model: Optional[str], cmd: tuple[str, ...], workdir: str, deadline: float) -> Check:
    """確認のコマンドを 1 回走らせて分類する（`run_check` の 1 回分）。"""
    shown = " ".join(a if a != workdir else "<cwd>" for a in cmd)
    started = time.monotonic()

    def done(reason: str, detail: str, found_model: Optional[str] = None) -> Check:
        seconds = round(time.monotonic() - started, 1)
        return Check(step, reason == "ok", reason, _detail(detail), seconds, found_model, shown)

    try:
        r = subprocess.run(
            _launch_args(runtime, cmd, workdir) if step != "auth" else list(cmd),
            input=None if step == "auth" else PROBE_PROMPT,
            capture_output=True,
            text=True,
            timeout=max(deadline - time.monotonic(), 0.1),
            cwd=workdir if step != "auth" else None,
            env={**os.environ, **PROBE_ENV[runtime]} if step != "auth" and runtime in PROBE_ENV else None,
        )
    except FileNotFoundError:
        return done("missing_cli", "コマンドが見つかりません")
    except subprocess.TimeoutExpired:
        return done("timeout", f"{AUTH_PROBE_TIMEOUT} 秒の持ち時間で応答しませんでした")
    except OSError as exc:
        return done("missing_cli", f"コマンドを実行できません（{exc.strerror or exc}）")
    stdout = monitor_patterns._strip_ansi(r.stdout or "")
    stderr = monitor_patterns._strip_ansi(r.stderr or "")
    reason, hit = _classify_output(step, runtime, model, r.returncode, stdout, stderr)
    found = _observed_model(step, runtime, stdout, stderr)
    return done(reason, hit or stderr.strip() or stdout.strip(), found)


def _budgeted_check(level: str, cwd: Optional[str] = None) -> Callable[[str, str, Optional[str]], Optional[Check]]:
    """1 者の確認の実行。持ち時間を 3 種類で共有し、`level="auth"` なら種類 `auth` だけを走らせる。"""
    deadline = time.monotonic() + AUTH_PROBE_TIMEOUT

    def check(step: str, runtime: str, model: Optional[str]) -> Optional[Check]:
        if level == "auth" and step != "auth":
            return None
        return run_check(step, runtime, model, timeout=deadline - time.monotonic(), cwd=cwd)

    return check


def _line(runtime: str, a: "assignment.Admission") -> str:
    """参加者ごとの 1 行。先頭の `✅` / `❌` は変えない。"""
    if not a.ok:
        return f"❌ {runtime}: {a.reason} — {a.detail}（{a.seconds} 秒）"
    if a.default_model:
        origin = f"設定のモデル {a.from_model}" if a.from_model else "設定のモデル"
        return f"↪ {runtime}: {origin} を引けないため、既定のモデル {a.default_model} で担当に入れる（{a.seconds} 秒）"
    if a.level == "model":
        return f"✅ {runtime}: 認証とモデル（{a.seconds} 秒）"
    what = "認証だけ" if runtime in MODEL_PROBES else "認証だけ（モデルの確認を持たない）"
    return f"✅ {runtime}: {what}（{a.seconds} 秒）"


def probe_auth(
    runtimes: Iterable[str],
    *,
    info: Callable[[str], None],
    env: Optional[Mapping[str, str]] = None,
    models: Optional[Mapping[str, str]] = None,
    level: str = "model",
    cwd: Optional[str] = None,
) -> tuple[ProbeResult, bool]:
    """参加の確認を参加者ごとに並行に走らせ、結果だけを返す（止めない確認。#727）。

    返り値は `(結果, 飛ばしたか)`。結果は名前 → `assignment.Admission.to_probe()` の辞書
    （`command` / `ok` / `detail` に `reason` / `level` / `seconds` / `default_model` / `from_model` / `model`）。
    `models` はランタイム → 引数で明示したモデル。`level="auth"` は認証確認だけを走らせる（external-ai の `run`）。
    `cwd` は確認を走らせるディレクトリで、省けば今のディレクトリ（担当と同じプロジェクトの設定を読む）。

    **例外を上げず、呼び出し側も中断させない。** 通らなかった者を外して続けるか、全員を要して
    止めるかは `assignment.resolve_participants` が決める。出力の 1 行は全員の確認が終わってから
    `ALL_RUNTIMES` の順に出す。

    `NDF_SKIP_AUTH_CHECK` が立てば確認のコマンドを 1 回も呼ばず `({}, True)` を返す（I7）。
    """
    if _skipped(env, info):
        return {}, True
    order = {r: i for i, r in enumerate(assignment.ALL_RUNTIMES)}
    targets = sorted({r for r in runtimes if r in AUTH_PROBES}, key=lambda r: order.get(r, len(order)))
    explicit = dict(models or {})
    if not targets:
        return {}, False
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {r: pool.submit(assignment.admit, r, explicit_model=explicit.get(r), check=_budgeted_check(level, cwd)) for r in targets}
        admissions = {r: f.result() for r, f in futures.items()}
    for runtime in targets:
        info(_line(runtime, admissions[runtime]))
    return {r: a.to_probe() for r, a in admissions.items()}, False
