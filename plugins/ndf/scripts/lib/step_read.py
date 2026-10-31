"""step_read.py: 手順のスクリプトの結果と材料を読む側の共通の規則（#2227）。

- `pick_ok_attempt`: 同じステップの試行（プランの実行の記録の `NN-<ステップ>.out`）から、`status` が
  `ok` の試行のうち番号の最も大きいものだけを採る。`ok` の試行が無ければ値を返さず、理由を返す
- `open_place` / `place_stop`: 結果の置き場（ファイル・ディレクトリ）が無い・読めないことと、
  置き場はあるが材料が 0 件であることを別の値で返す
- `result_line` / `not_ok_reason`: 子プロセスの標準出力の最後の行を結果 JSON として読み、`ok`（終了コード 0）で
  なければ理由の 1 行を返す。結果 JSON が無い・形が誤っていることも成功と読まない

読むだけで、試行も置き場も書き換えない。形の正本は同じディレクトリの README.md の
「手順のスクリプトの結果」。標準ライブラリだけで書く。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import step_result  # noqa: E402

# Pick.outcome の語彙
PICK_OK = "ok"
PICK_NO_OK_ATTEMPT = "no_ok_attempt"
PICK_NO_ATTEMPT = "no_attempt"
PICK_NO_PLACE = "no_place"
PICK_UNREADABLE = "unreadable"

# Place.state の語彙
PLACE_MISSING = "missing"
PLACE_UNREADABLE = "unreadable"
PLACE_EMPTY = "empty"
PLACE_PRESENT = "present"

_ATTEMPT_RE = re.compile(r"^(\d+)-(.+)\.out$")


@dataclass(frozen=True)
class Pick:
    """試行の選び方の結果。採れたときだけ result と number を持つ。"""

    outcome: str
    result: dict | None = None
    number: int | None = None
    attempts: tuple = field(default_factory=tuple)  # (番号, status) の組。結果 JSON の無い試行は "none"


@dataclass(frozen=True)
class Place:
    """置き場の状態。present のときだけ text（ファイル）か entries（ディレクトリ）を持つ。"""

    path: Path
    state: str
    text: str | None = None
    entries: tuple = field(default_factory=tuple)
    reason: str = ""


def _last_result(text: str, tool: str) -> dict | None:
    """ファイルの最後の、tool が一致し形の正しい結果 JSON。"""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("tool") == tool and not step_result.validate_result(obj):
            return obj
    return None


def _attempt_files(d: Path, step: str) -> list[tuple[int, Path]]:
    found = []
    for p in d.iterdir():
        m = _ATTEMPT_RE.match(p.name)
        if m and m.group(2) == step and p.is_file():
            found.append((int(m.group(1)), p))
    return sorted(found)


def pick_ok_attempt(dir, step: str, tool: str) -> Pick:
    """`dir` の `NN-<step>.out` から、status が ok の試行のうち番号の最も大きいものを採る。例外を投げない。"""
    d = Path(dir)
    if not d.exists():
        return Pick(PICK_NO_PLACE)
    try:
        files = _attempt_files(d, step)
    except OSError:
        return Pick(PICK_UNREADABLE)
    if not files:
        return Pick(PICK_NO_ATTEMPT)
    attempts = []
    best = None
    for n, p in files:
        try:
            obj = _last_result(p.read_text(encoding="utf-8"), tool)
        except (OSError, UnicodeDecodeError):
            obj = None
        status = obj["status"] if obj else "none"
        attempts.append((n, status))
        if status == "ok":
            best = (n, obj)
    if best is None:
        return Pick(PICK_NO_OK_ATTEMPT, attempts=tuple(attempts))
    return Pick(PICK_OK, result=best[1], number=best[0], attempts=tuple(attempts))


def open_place(path) -> Place:
    """置き場を開き、無い / 読めない / 空 / ある に分ける。例外を投げない。"""
    p = Path(path)
    try:
        if not p.exists():
            return Place(p, PLACE_MISSING)
        if p.is_dir():
            entries = tuple(sorted(p.iterdir()))
            return Place(p, PLACE_PRESENT, entries=entries) if entries else Place(p, PLACE_EMPTY)
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return Place(p, PLACE_UNREADABLE, reason=str(e))
    return Place(p, PLACE_PRESENT, text=text) if text.strip() else Place(p, PLACE_EMPTY, text=text)


def place_stop(tool: str, place: Place, what: str):
    """置き場が無い・読めないときの stopped の結果と終了コードの組。空・ありなら None（呼ぶ側が読む）。"""
    if place.state == PLACE_MISSING:
        return step_result.result(tool, "stopped", f"{what} の置き場が無い: {place.path}"), step_result.EXIT_PRECONDITION
    if place.state == PLACE_UNREADABLE:
        why = f"（{place.reason}）" if place.reason else ""
        return step_result.result(tool, "stopped", f"{what} の置き場を読めない: {place.path}{why}"), step_result.EXIT_UNREADABLE
    return None


def result_line(stdout: str) -> dict | None:
    """標準出力の最後の空でない行を結果 JSON として読む。無い・形が誤っていれば None。"""
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    try:
        obj = json.loads(lines[-1]) if lines else None
    except ValueError:
        return None
    return obj if obj is not None and not step_result.validate_result(obj) else None


def not_ok_reason(stdout: str, code: int, stderr: str = "") -> str | None:
    """結果 JSON が ok（終了コード 0）なら None、そうでなければ状態・終了コード・要約・欠けの理由の 1 行を返す。"""
    obj = result_line(stdout)
    if obj is None:
        return f"結果 JSON が無い（exit={code}）: {(stderr or '').strip()[:200]}"
    if obj["status"] == "ok" and code == 0:
        return None
    reasons = "; ".join(g["reason"] for g in obj.get("gaps", []))
    return f"{obj['status']}（exit={code}）: {obj['summary']}" + (f"（{reasons}）" if reasons else "")


def process_if_not_ok(run: str, completed) -> list[dict]:
    """子プロセス（`subprocess.CompletedProcess`）の結果 JSON が ok でなければ、過程の項目 1 件（`run` / `result`）の列を返す。"""
    why = not_ok_reason(completed.stdout, completed.returncode, completed.stderr)
    return [{"run": run, "result": why}] if why else []
