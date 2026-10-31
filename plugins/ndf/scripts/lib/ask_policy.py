"""問い合わせの判定（#2206）。`AskUserQuestion` の区分（`metadata.source`）と宣言 `.ndf/ask.json` だけで決める。

hook（`hook_lib/ask_guard.py`）と relay の `question open`（`relay_lib/mark.py`）が同じ関数を呼ぶ（設計の決定 4）。
relay の複製にも入るため、標準ライブラリだけで書き、ファイルを読む以外の副作用を持たない。

区分の形は 3 つ:

| 区分 | 判定 |
| --- | --- |
| `ndf-gate:1`・`ndf-gate:2` | 承認ゲート。どの宣言でも通す（I1） |
| `ndf-ask:redline:<番号>`（`C1`〜`C8`・`P<数>`・`R<数>`） | 共通原則・プロジェクト・スプリントの承認が要る操作。どの宣言でも通す（I2） |
| `ndf-ask:<名前>` | `allow`（組み込みの 4 区分）か `extra[].name` にあれば通す |

区分が無い・形に合わない・許されていない問いは、`mode` が `enforce` なら `deny`、`advise` なら `advise`。
問いの文と選択肢は読まない（I6）。宣言が 1 か所でも誤っていれば全体を既定にし、誤りの箇所を `problem` に持つ（決定 6）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

DECL_PATH = Path(".ndf") / "ask.json"
MODES = ("enforce", "advise", "off")
BUILTIN = ("cost", "irreversible", "human-action", "legal")
RESERVED = ("redline",)
GATES = ("1", "2")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REDLINE_RE = re.compile(r"^(C[1-8]|P[0-9]+|R[0-9]+)$")
GATE_PREFIX = "ndf-gate:"
ASK_PREFIX = "ndf-ask:"
REDLINE_PREFIX = ASK_PREFIX + "redline:"

# 判定の理由の種類（Verdict.kind）
PASSING = ("gate", "redline", "allowed")
BLOCKING = ("missing", "malformed", "not-allowed")


@dataclass(frozen=True)
class Declaration:
    mode: str = "enforce"
    allow: frozenset = frozenset(BUILTIN)
    threshold_usd: float = 0
    extra: tuple = ()  # (name, description) の組
    problem: str = ""  # 読めなかった箇所。空は「読めた・無かった」

    def allowed_names(self) -> frozenset:
        return self.allow | {name for name, _ in self.extra}


@dataclass(frozen=True)
class Verdict:
    decision: str  # deny / allow / advise / off
    source: str
    kind: str  # gate / redline / allowed / missing / malformed / not-allowed / off


DEFAULT = Declaration()


def project_root(cwd: str | os.PathLike) -> Path:
    """作業ディレクトリから親へ辿り、`.git`（worktree ではファイル）を持つ最初のディレクトリ。無ければ作業ディレクトリ（決定 8）。"""
    start = Path(cwd)
    for d in (start, *start.parents):
        if (d / ".git").exists():
            return d
    return start


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_allow(v) -> str:
    if not isinstance(v, list):
        return "allow"
    return next((f"allow[{i}]" for i, x in enumerate(v) if x not in BUILTIN), "")


def _check_cost(v) -> str:
    if not isinstance(v, dict):
        return "cost"
    t = v.get("threshold_usd", 0)
    return "" if _is_number(t) and t >= 0 else "cost.threshold_usd"


def _check_extra(v) -> str:
    if not isinstance(v, list):
        return "extra"
    for i, e in enumerate(v):
        if not isinstance(e, dict):
            return f"extra[{i}]"
        name = e.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name) or name in BUILTIN or name in RESERVED:
            return f"extra[{i}].name"
        desc = e.get("description")
        if not isinstance(desc, str) or not desc.strip():
            return f"extra[{i}].description"
    return ""


def parse_declaration(data) -> Declaration:
    """読んだ JSON を宣言へ。誤りがあれば既定に最初の誤りの箇所を添えて返す。"""
    if not isinstance(data, dict):
        return Declaration(problem="（全体がオブジェクトでない）")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        return Declaration(problem="version")
    mode = data.get("mode", "enforce")
    if mode not in MODES:
        return Declaration(problem="mode")
    checks = (("allow", _check_allow), ("cost", _check_cost), ("extra", _check_extra))
    for key, check in checks:
        if key in data and (problem := check(data[key])):
            return Declaration(problem=problem)
    allow = frozenset(data["allow"]) if "allow" in data else frozenset(BUILTIN)
    threshold = (data.get("cost") or {}).get("threshold_usd", 0)
    extra = tuple((e["name"], e["description"]) for e in data.get("extra") or [])
    return Declaration(mode=mode, allow=allow, threshold_usd=threshold, extra=extra)


def load_ask_declaration(root: str | os.PathLike) -> Declaration:
    """`<root>/.ndf/ask.json` を読む。無ければ既定、読めなければ既定と箇所（I4）。"""
    path = Path(root) / DECL_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DEFAULT
    except (OSError, UnicodeDecodeError):
        return Declaration(problem="（ファイルを読めない）")
    try:
        data = json.loads(text)
    except ValueError:
        return Declaration(problem="（JSON として読めない）")
    return parse_declaration(data)


def source_of(tool_input) -> tuple[str, bool]:
    """`metadata.source` の値と、それが文字列だったか。無い・空は ("", True)。"""
    meta = tool_input.get("metadata") if isinstance(tool_input, dict) else None
    if not isinstance(meta, dict) or meta.get("source") is None:
        return "", True
    v = meta.get("source")
    return (v, True) if isinstance(v, str) else (str(v), False)


def parse_source(value: str) -> tuple[str, str]:
    """区分の形と名前。`("gate", "1")`・`("redline", "C4")`・`("ask", "cost")`・`("missing", "")`・`("malformed", value)`。"""
    if not value:
        return "missing", ""
    if value.startswith(GATE_PREFIX):
        n = value[len(GATE_PREFIX) :]
        return ("gate", n) if n in GATES else ("malformed", value)
    if value.startswith(REDLINE_PREFIX):
        n = value[len(REDLINE_PREFIX) :]
        return ("redline", n) if REDLINE_RE.match(n) else ("malformed", value)
    if value.startswith(ASK_PREFIX):
        n = value[len(ASK_PREFIX) :]
        return ("ask", n) if NAME_RE.match(n) and n not in RESERVED else ("malformed", value)
    return "malformed", value


def judge_ask(tool_input, decl: Declaration) -> Verdict:
    """1 回の `AskUserQuestion` への判定。問いの文は読まない（I6）。"""
    source, is_str = source_of(tool_input)
    if decl.mode == "off":
        return Verdict("off", source, "off")
    form, name = parse_source(source) if is_str else ("malformed", source)
    if form == "gate":
        kind = "gate"
    elif form == "redline":
        kind = "redline"
    elif form == "ask":
        kind = "allowed" if name in decl.allowed_names() else "not-allowed"
    else:
        kind = form
    if kind in PASSING:
        return Verdict("allow", source, kind)
    return Verdict("deny" if decl.mode == "enforce" else "advise", source, kind)


def passing_sources(decl: Declaration) -> list[tuple[str, str]]:
    """人へ回してよい区分と説明の一覧（案内に載せる）。"""
    rows = [
        ("ndf-gate:1", "承認ゲート 1（設計の承認）"),
        ("ndf-gate:2", "承認ゲート 2（本番系へ届く操作の承認）"),
        ("ndf-ask:redline:<番号>", "共通原則の C1〜C8・プロジェクトの P・スプリントの R に当たる操作の承認（宣言で外せない）"),
    ]
    builtin_desc = {
        "cost": "お金が出ていく操作",
        "irreversible": "取り消せない外部への操作（レッドラインに当たらないもの）",
        "human-action": "人にしかできない操作（ログイン・二要素認証・権限の付与など）",
        "legal": "規約・契約・ライセンス・個人情報の扱いへの同意",
    }
    rows += [(ASK_PREFIX + n, builtin_desc[n]) for n in BUILTIN if n in decl.allow]
    rows += [(ASK_PREFIX + name, desc) for name, desc in decl.extra]
    return rows


def judge_input(raw: dict, cwd: str | os.PathLike | None = None) -> tuple[Verdict, Declaration]:
    """hook の入力（辞書）から宣言を読み、判定する。`cwd` を省くと入力の `cwd`、無ければカレント。"""
    where = cwd or (raw.get("cwd") if isinstance(raw.get("cwd"), str) and raw.get("cwd") else os.getcwd())
    decl = load_ask_declaration(project_root(where))
    return judge_ask(raw.get("tool_input"), decl), decl
