"""本物の claude: 実体の解決・素通し・引数の引き継ぎ・プラグインの版・会話の記録の読み取り（#895・#936・#1142 の C6）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from .common import config_dir, data_dir, env_num, launcher_path, parse_iso

# 素通しにする引数と副命令（Claude Code 2.1.280 の `claude --help` から写す）
PASS_FLAGS = {"-p", "--print", "-h", "--help", "-v", "--version"}
SUBCOMMANDS = {
    "agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install",
    "logs", "mcp", "plugin", "plugins", "project", "respawn", "rm", "setup-token",
    "stop", "kill", "ultrareview", "update", "upgrade",
}
# 2 つ目以降の区間へ引き継ぐ引数の解析（#936・決定 22）。Claude Code 2.1.281 の `claude --help` から写す。
# 値を取らない選択肢。これ以外の `-` で始まる選択肢は、次の語が `-` で始まらなければ値として取る
BOOL_FLAGS = {
    "--allow-dangerously-skip-permissions", "--ax-screen-reader", "--bg", "--background",
    "--bare", "--brief", "--chrome", "--no-chrome", "-c", "--continue",
    "--dangerously-skip-permissions", "--disable-slash-commands",
    "--exclude-dynamic-system-prompt-sections", "--fork-session", "--forward-subagent-text",
    "--ide", "--include-hook-events", "--include-partial-messages",
    "--no-session-persistence", "--replay-user-messages", "--restricted", "--safe-mode",
    "--strict-mcp-config", "--tmux", "--verbose",
}
# 可変長（`<x...>`）の選択肢。後続の `-` で始まらない語をすべて取る
VARIADIC_FLAGS = {
    "--add-dir", "--allowedTools", "--allowed-tools", "--disallowedTools",
    "--disallowed-tools", "--mcp-config", "--betas", "--tools", "--file",
}
# 必須の値（`<x>`）を取る選択肢。次の語が `-` で始まっても値として取る（commander と同じ）
REQUIRED_FLAGS = {
    "--agent", "--agents", "--append-system-prompt", "--autocompact", "--debug-file", "--effort",
    "--environment", "--fallback-model", "--input-format", "--json-schema", "--max-budget-usd",
    "--model", "-n", "--name", "--output-format", "--permission-mode", "--permission-prompts",
    "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix", "--session-id",
    "--setting-sources", "--settings", "--system-prompt", "--system-prompt-snapshot",
}
# 会話ごと・区間ごとに変わるもの。次の区間は新しい会話を始めるので引き継がない
SECTION_FLAGS = {
    "-c", "--continue", "-r", "--resume", "--session-id", "--fork-session", "--from-pr",
    "--teleport", "--cloud", "-n", "--name", "--bg", "--background", "--tmux",
    "-w", "--worktree",
}
# 子へ継がせない Claude Code の環境変数（中から起こしたプロセスが継ぐもの）
DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")


def _is_self(path: str) -> bool:
    real = os.path.realpath(path)
    mine = {os.path.realpath(launcher_path()), os.path.realpath(os.path.join(data_dir(), "relay.py")),
            os.path.realpath(os.path.join(config_dir(), "relay.py"))}
    if real in mine:
        return True
    try:
        with open(real, "rb") as f:
            return b"relay.py" in f.read(4096)
    except OSError:
        # 読めないものはラッパーと見なさない。飛ばし損ねた繰り返しは NDF_RELAY_DEPTH が止める
        return False


def resolve_claude() -> str | None:
    """alias は子に効かないため実体を探す。`claude` という名前でラッパーを呼ぶ別のスクリプトを飛ばす。"""
    forced = os.environ.get("NDF_RELAY_CLAUDE")
    if forced:
        return forced
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d or ".", "claude")
        if os.path.isfile(p) and os.access(p, os.X_OK) and not _is_self(p):
            return os.path.abspath(p)
    return None


def needs_no_relay(args: list[str]) -> bool:
    if any(a in PASS_FLAGS or a.startswith("--print=") for a in args):
        return True
    return bool(args) and args[0] in SUBCOMMANDS


def carried_args(args: list[str]) -> list[str]:
    """最初の区間の引数から、2 つ目以降の区間の先頭に付ける起動の方針を選ぶ（#936）。

    commander と同じ規則で選択肢と値を区切り、位置引数・`--` 以後・`SECTION_FLAGS` を
    値ごと落とす。未知の選択肢は値を取る規則のまま引き継ぐ。"""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            break
        if not a.startswith("-") or a == "-":
            continue  # 位置引数（最初のプロンプト）
        long = a.startswith("--")
        name = a.split("=", 1)[0] if long else a[:2]  # `-nfoo` は `-n` に値が付いた 1 語
        group = [a]
        one_word = "=" in a if long else len(a) > 2
        if not one_word and name in REQUIRED_FLAGS:
            group += args[i:i + 1]
            i += 1
        elif not one_word and name not in BOOL_FLAGS:
            take_all = name in VARIADIC_FLAGS
            while i < len(args) and not args[i].startswith("-"):
                group.append(args[i])
                i += 1
                if not take_all:
                    break
        if name not in SECTION_FLAGS:
            out += group
    return out


def say(msg: str) -> None:
    """`ndf-relay:` の 1 行を標準エラーへ出す。"""
    sys.stderr.write(f"ndf-relay: {msg}\n")
    sys.stderr.flush()


def depth() -> int:
    try:
        return int(os.environ.get("NDF_RELAY_DEPTH") or 0)
    except ValueError:
        return 0


def passthrough(claude: str, args: list[str]):
    """環境は入れ子を数える変数だけを足して、本物の claude を exec する。"""
    env = dict(os.environ)
    env["NDF_RELAY_DEPTH"] = str(depth() + 1)
    os.execve(claude, [claude] + args, env)


def child_env(relay_dir: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    env["NDF_RELAY_DEPTH"] = str(depth() + 1)
    env["NDF_RELAY_DIR"] = relay_dir
    return env


def plugin_cli(claude: str, args: list[str], timeout: float) -> subprocess.CompletedProcess | None:
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV and k != "NDF_RELAY_DIR"}
    try:
        return subprocess.run([claude, "plugin"] + args, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def read_plugin(claude: str, marketplace: str | None = None) -> tuple[str, str] | None:
    """`claude plugin list --json` から (マーケットプレイス, 版) を読む。"""
    p = plugin_cli(claude, ["list", "--json"], env_num("NDF_RELAY_LIST_TIMEOUT", 15))
    if p is None or p.returncode != 0:
        return None
    try:
        items = json.loads(p.stdout)
    except ValueError:
        return None
    for it in items if isinstance(items, list) else []:
        pid = it.get("id") if isinstance(it, dict) else None
        if not isinstance(pid, str) or not pid.startswith("ndf@"):
            continue
        mk = pid.split("@", 1)[1]
        if marketplace is not None and mk != marketplace:
            continue
        ver = it.get("version")
        if mk and isinstance(ver, str) and ver:
            return mk, ver
    return None


def update_plugin(claude: str, marketplace: str) -> str | None:
    """マーケットプレイスと ndf を更新し、更新後の版を返す。どれかに失敗したら None。"""
    t = env_num("NDF_RELAY_UPDATE_TIMEOUT", 120)
    for args in (["marketplace", "update", marketplace],
                 ["update", f"ndf@{marketplace}", "-y"]):
        p = plugin_cli(claude, args, t)
        if p is None or p.returncode != 0:
            return None
    got = read_plugin(claude, marketplace)
    return got[1] if got else None


# ---------------------------------------------------------------- 会話の記録


def file_snap(path: str) -> tuple[int, int] | None:
    """(大きさ, 更新時刻の ns)。無ければ None。"""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_size, st.st_mtime_ns


def _iter_transcript_rows(transcript_path: str):
    """会話の記録から JSON object の行だけを返す。"""
    if not transcript_path:
        return
    try:
        with open(transcript_path, errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def replied_after(transcript_path: str, written: float) -> bool:
    """会話の記録に、合図より後の `assistant` か `user` の行があれば真（G2）。"""
    for row in _iter_transcript_rows(transcript_path):
        if row.get("type") not in ("assistant", "user"):
            continue
        t = parse_iso(row.get("timestamp"))
        if t is not None and t > written:
            return True
    return False


def _is_user_prompt(row: dict) -> bool:
    """利用者が入力した行か。hook の差し戻し（`isMeta`）と Tool の結果は除く。"""
    if row.get("type") != "user" or row.get("isMeta"):
        return False
    content = (row.get("message") or {}).get("content") if isinstance(row.get("message"), dict) else None
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        items = [c for c in content if isinstance(c, dict)]
        if not items or any(c.get("type") == "tool_result" for c in items):
            return False
        # Esc で応答を止めた記録（ラッパーが書いた Esc でも出る）は入力に数えない
        return not all(str(c.get("text") or "").startswith("[Request interrupted by user")
                       for c in items)
    return False


def _starts_background(row: dict) -> bool:
    """背景の処理を起動する Tool の呼び出し（`run_in_background` が真）を含む `assistant` の行か。"""
    if row.get("type") != "assistant" or not isinstance(row.get("message"), dict):
        return False
    content = row["message"].get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(c, dict) and c.get("type") == "tool_use"
               and isinstance(c.get("input"), dict) and c["input"].get("run_in_background")
               for c in content)


def after_mark(transcript_path: str, written: float) -> tuple[bool, bool]:
    """合図より後の会話の記録を見て (目標が未達と判定された, 切り替えを取りやめる) を返す。
    取りやめるのは、利用者の入力か背景の処理の起動の行があるとき。"""
    unmet = cancel = False
    for row in _iter_transcript_rows(transcript_path):
        t = parse_iso(row.get("timestamp"))
        if t is None or t <= written:
            continue
        a = row.get("attachment")
        if (row.get("type") == "attachment" and isinstance(a, dict)
                and a.get("type") == "goal_status" and a.get("met") is False
                and not a.get("sentinel")):
            unmet = True
        if _is_user_prompt(row) or _starts_background(row):
            cancel = True
    return unmet, cancel
