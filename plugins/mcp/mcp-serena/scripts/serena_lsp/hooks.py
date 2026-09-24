"""SessionStart の通知と、PreToolUse の誘導・自動許可（決定 6・決定 9）。

閾値・待ち・数の有効期間・数を戻さないツール名の部分文字列は Serena 1.7.0 の
`serena-hooks remind` と同じにする。数えるのは configure の印のある project.yml の
採った言語の拡張子だけである。呼び出し側（serena-lsp.py）が例外を握りつぶす。
"""
import json
import os
import shlex
import time
from pathlib import Path

from . import check, detect, table
from . import project_yml as py

SKILL = "/mcp-serena:language-servers"
PREFIX = "[mcp-serena]"

THRESHOLDS = {"grep": 3, "read": 3, "mixed": 4}
PERIODS = {"grep": 1000, "read": 1000, "mixed": 2000}
DENY_INTERVAL = 120
NON_SYMBOLIC = ("pattern", "read", "diagnostics", "memory", "onboarding", "config", "list_file",
                "find_file", "shell", "dashboard", "restart_language_server")
GREP_COMMANDS = {"grep", "rg", "ag", "ack", "fgrep", "egrep"}
READ_COMMANDS = {"cat", "head", "tail", "sed", "less", "nl"}
SHELL_TOOLS = {"bash", "shell", "exec_command", "local_shell"}
SHELLS = {"bash", "sh", "zsh"}
AUTO_ALLOW_MODES = {"acceptEdits", "auto"}
ITEM_LABELS = {"plugin": "プラグイン", "binary": "本体", "typescript_major_5": "TypeScript の版"}

DENY_REASON = (
    f"{PREFIX} コードの grep / 読み込みが続いています。Serena でシンボル単位に読むと量が減ります。\n"
    "読む: get_symbols_overview → find_symbol（include_body）→ find_referencing_symbols。編集: replace_symbol_body\n"
    "数を戻したので、必要ならこのまま同じ操作を続けてよい。"
)


def _now() -> float:
    return time.time()


def _skip_for_client(client: str) -> bool:
    # Kiro は hooks.json を --client claude-code のまま写す。CLAUDE_PLUGIN_ROOT は Claude Code だけが置く
    return client == "claude-code" and not os.environ.get("CLAUDE_PLUGIN_ROOT")


def find_root(cwd) -> Path:
    start = Path(cwd).resolve()
    for directory in (start, *start.parents):
        if (directory / ".serena/project.yml").is_file() or (directory / ".git").exists():
            return directory
    return start


# ---- SessionStart ------------------------------------------------------------

def _context(event: str, lines: list) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(lines)}}


def _decision(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


def _unconfigured_lines(detected: list) -> list:
    if not detected:
        return []
    found = "・".join(f"{d['language']} {d['files']}" for d in detected)
    return [
        f"{PREFIX} このリポジトリでは言語サーバが未設定です（検出: {found}）",
        f"{PREFIX} {SKILL} をこのリポジトリで実行してください（プロジェクトごとに実行します。利用者単位で 1 回ではありません）",
    ]


def _configured_lines(root: Path, client: str, state: dict, detected: list) -> list:
    lines = []
    known = set(state["languages"]) | set(state["excluded"])
    for d in detected:
        if d["language"] not in known:
            lines.append(f"{PREFIX} 設定に無い言語があります: {d['language']}（{d['files']} ファイル）")
    try:
        missing = check.missing_items(root, client)
    except check.Unreadable:
        missing = []
    for item in missing:
        name = item["name"].split("@", 1)[0]
        if item["item"] in ITEM_LABELS:
            lines.append(f"{PREFIX} Claude Code の LSP が足りません: {name}（{ITEM_LABELS[item['item']]}）")
        else:
            lines.append(f"{PREFIX} {item['language']} の診断に要るものが足りません: {name}")
    if lines:
        lines.append(f"{PREFIX} {SKILL} で直せます（プロジェクトごとに実行します）")
    return lines


def session_start(payload: dict, client: str):
    if _skip_for_client(client) or not isinstance(payload.get("cwd"), str):
        return None
    root = find_root(payload["cwd"])
    try:
        state = py.load_state(root)
    except py.UnsupportedShape:
        return None
    try:
        detected, _ = detect.detect(root, table.load())
    except detect.GitUnavailable:
        return None

    if state is None or not state["marked"]:
        lines = _unconfigured_lines(detected)
    else:
        lines = _configured_lines(root, client, state, detected)
    return _context("SessionStart", lines) if lines else None


# ---- PreToolUse -------------------------------------------------------------

def _state_path(session_id: str) -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_") or "unknown"
    return Path(base) / "mcp-serena/hooks" / f"{safe}.json"


EMPTY = {"grep": 0, "read": 0, "mixed": 0, "last_grep": None, "last_read": None, "last_mixed": None,
         "last_deny": None}


def _load_counts(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and set(EMPTY) <= set(data):
            return data
    except (OSError, ValueError):
        pass
    return dict(EMPTY)


def _save_counts(path: Path, counts: dict, now: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(counts))
        for old in path.parent.glob("*.json"):
            if old != path and now - old.stat().st_mtime > 86400:
                old.unlink(missing_ok=True)
    except OSError:
        pass


def _shell_words(command) -> list:
    if isinstance(command, list):
        words = [str(w) for w in command]
        if len(words) >= 3 and os.path.basename(words[0]) in SHELLS and words[1] in ("-c", "-lc"):
            return _shell_words(words[2])
        return words
    try:
        return shlex.split(str(command))
    except ValueError:
        return str(command).split()


def _classify_shell(command, exts: set):
    words = [w for w in _shell_words(command)]
    while words and "=" in words[0] and not words[0].startswith("-"):
        words = words[1:]  # 先頭の環境変数の代入
    if not words:
        return None
    name = os.path.basename(words[0]).lower()
    if name in GREP_COMMANDS:
        return "grep"
    if name in READ_COMMANDS and any(table.suffix(w) in exts for w in words[1:] if not w.startswith("-")):
        return "read"
    return None


def _classify(tool: str, tool_input: dict, client: str, exts: set):
    lower = tool.lower()
    if "serena" in lower:
        if "search_for_pattern" in lower:
            return "grep"
        if not any(s in lower for s in NON_SYMBOLIC):
            return "symbolic"
        return None
    if client == "claude-code":
        if tool == "Grep":
            return "grep"
        if tool == "Read":
            return "read" if table.suffix(str(tool_input.get("file_path", ""))) in exts else None
    if lower in SHELL_TOOLS or tool == "Bash":
        return _classify_shell(tool_input.get("command", tool_input.get("cmd", "")), exts)
    return None


def _bump(counts: dict, kind: str, period: int, now: float) -> None:
    last = counts[f"last_{kind}"]
    counts[kind] = counts[kind] + 1 if last is not None and now - last <= period else 1
    counts[f"last_{kind}"] = now


def _reset(counts: dict) -> None:
    for kind in THRESHOLDS:
        counts[kind] = 0
        counts[f"last_{kind}"] = None


def _allow(tool: str, payload: dict, client: str):
    if client == "claude-code" and "serena" in tool.lower() and payload.get("permission_mode") in AUTO_ALLOW_MODES:
        return _decision("allow", f"{PREFIX} Serena のツールを自動で許可しました")
    return None


def pre_tool_use(payload: dict, client: str):
    if _skip_for_client(client):
        return None
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or not isinstance(payload.get("cwd"), str):
        return None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    try:
        state = py.load_state(find_root(payload["cwd"]))
    except py.UnsupportedShape:
        state = None
    if state is None or not state["marked"]:
        return _allow(tool, payload, client)

    exts = table.extensions_of(table.load(), state["languages"])
    kind = _classify(tool, tool_input, client, exts)
    if kind is None:
        return _allow(tool, payload, client)

    now = _now()
    path = _state_path(str(payload.get("session_id") or "unknown"))
    counts = _load_counts(path)
    if counts["last_deny"] is not None and now - counts["last_deny"] < DENY_INTERVAL:
        return _allow(tool, payload, client)
    if kind == "symbolic":
        _reset(counts)
        _save_counts(path, counts, now)
        return _allow(tool, payload, client)

    _bump(counts, kind, PERIODS[kind], now)
    _bump(counts, "mixed", PERIODS["mixed"], now)
    if any(counts[k] >= THRESHOLDS[k] for k in THRESHOLDS):
        _reset(counts)
        counts["last_deny"] = now
        _save_counts(path, counts, now)
        return _decision("deny", DENY_REASON)
    _save_counts(path, counts, now)
    return _allow(tool, payload, client)
