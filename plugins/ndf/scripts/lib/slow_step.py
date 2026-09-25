"""段の遅れの見張りの材料: 想定時間・所要の履歴・設定・組み込みの一次の調査。

supervise.py が段を回す間に使う。想定は同じ段（フェーズ, 段の id）の直近 `window` 件の所要の中央値 ×
`factor` とし、`floor` を下限にする。履歴が `min_samples` 件に満たなければ `default` を使う。
所要の履歴は 1 行 1 つの JSON で積み、書き換えない（置き場所の既定は <git の共通ディレクトリ>/ndf/）。
"""
from __future__ import annotations

import json
import os
import shlex
import statistics
import subprocess
from dataclasses import dataclass, fields
from pathlib import Path

HISTORY_NAME = "step-history.jsonl"
WATCHED_TYPES = ("run", "work", "drive")
ACTIONS = ("wait", "remedied", "retry", "fix", "stop", "judge")
DECISIONS = ("retry", "fix", "stop", "wait")


class SlowConfigError(ValueError):
    """slow の設定が読めない。args[0] は読めない鍵。"""


@dataclass(frozen=True)
class SlowConfig:
    enabled: bool = True
    window: int = 10
    min_samples: int = 3
    factor: float = 3.0
    floor: float = 300.0
    default: float = 900.0
    max_waits: int = 3
    max_llm: int = 2
    max_retry: int = 1
    probe_timeout: float = 120.0
    judge_timeout: float = 300.0
    history: str | None = None


def _coerce(key: str, kind, value):
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false"):
            return value.lower() == "true"
        raise SlowConfigError(key)
    if key == "history":
        if value is None or (isinstance(value, str) and value):
            return value
        raise SlowConfigError(key)
    if isinstance(value, bool):
        raise SlowConfigError(key)
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            raise SlowConfigError(key) from None
    if not isinstance(value, (int, float)) or value < 0:
        raise SlowConfigError(key)
    if kind is int:
        if float(value) != int(value):
            raise SlowConfigError(key)
        return int(value)
    return float(value)


def parse_overrides(pairs) -> dict:
    """引数の `K=V` の並びを辞書にする。形が違えば SlowConfigError。"""
    out = {}
    for pair in pairs or []:
        k, sep, v = str(pair).partition("=")
        if not sep or not k:
            raise SlowConfigError(str(pair))
        out[k.strip()] = v.strip()
    return out


def resolve_config(args: dict | None = None, plan: dict | None = None, decl: dict | None = None) -> SlowConfig:
    """引数 → 計画の slow → 宣言の slow → 既定の順に重ねる。知らない鍵・形の違う値は SlowConfigError。"""
    kinds = {f.name: f.type for f in fields(SlowConfig)}
    types = {"bool": bool, "int": int, "float": float, "str | None": str}
    merged: dict = {}
    for layer in (decl, plan, args):  # 後の層が先に効く
        if layer is None:
            continue
        if not isinstance(layer, dict):
            raise SlowConfigError("slow")
        for k, v in layer.items():
            if k not in kinds:
                raise SlowConfigError(k)
            merged[k] = _coerce(k, types.get(kinds[k], kinds[k]), v)
    cfg = SlowConfig(**merged)
    if cfg.window < 1:
        raise SlowConfigError("window")
    return cfg


def expected_for(seconds: list[float], cfg: SlowConfig, step_expected=None) -> tuple[float, dict]:
    """同じ段の所要（古い順）から想定の秒と根拠を返す。step_expected があればそれに固定する。"""
    if step_expected is not None:
        return float(step_expected), {"source": "step"}
    recent = [float(s) for s in seconds][-cfg.window:]
    basis = {"samples": len(recent), "factor": cfg.factor, "floor": cfg.floor}
    if len(recent) < cfg.min_samples:
        return float(cfg.default), {"source": "default", **basis, "default": cfg.default}
    median = round(statistics.median(recent), 2)
    value = round(median * cfg.factor, 1)
    if value < cfg.floor:
        return float(cfg.floor), {"source": "floor", **basis, "median": median}
    return value, {"source": "history", **basis, "median": median}


# --- 所要の履歴 ---

def _git(cwd, *args) -> str | None:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    except OSError:
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def history_path(cwd, state_dir, configured: str | None = None) -> Path:
    """所要の履歴のファイル。設定があればそれ（相対ならリポジトリの根から）、無ければ
    <git の共通ディレクトリ>/ndf/step-history.jsonl。git でなければ <状態ディレクトリ>/step-history.jsonl。"""
    cwd_ok = cwd and Path(cwd).is_dir()
    if configured:
        p = Path(configured)
        if p.is_absolute():
            return p
        root = _git(cwd, "rev-parse", "--show-toplevel") if cwd_ok else None
        return Path(root or cwd or ".") / p
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir") if cwd_ok else None
    if common:
        return Path(common) / "ndf" / HISTORY_NAME
    return Path(state_dir) / HISTORY_NAME


def _rows(path: Path):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for raw in text.splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("seconds"), (int, float)):
            yield d


def read_history(path: Path, phase: str, step: str, window: int | None = None) -> list[float]:
    """同じ段（フェーズ, 段の id）の所要を古い順に返す（window があれば直近の件数だけ）。読めなければ []。"""
    out = [float(d["seconds"]) for d in _rows(path) if d.get("phase") == phase and d.get("step") == step]
    return out[-window:] if window else out


def history_record(phase, step: str, type_: str, seconds: float, exit_code: int, plan: str | None,
                   at: str) -> dict:
    return {"at": at, "phase": phase or "?", "step": step, "type": type_, "seconds": round(float(seconds), 1),
            "exit": exit_code, "plan": plan or ""}


def append_history(path: Path, rec: dict) -> str | None:
    """1 行を O_APPEND の 1 回の write で足す。誤りの文を返す（無ければ None）。"""
    data = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError as e:
        return f"所要の履歴を書けない: {e}"
    return None


def _history_key(d: dict) -> tuple:
    return (d.get("plan") or "", d.get("at"), d.get("phase"), d.get("step"), d.get("type"))


def keeps(exit_code) -> bool:
    """履歴へ積む終了コード（成功と関門）。"""
    return exit_code == 0 or (isinstance(exit_code, int) and 10 <= exit_code <= 19)


def plan_of_progress(progress: Path) -> tuple[str, str]:
    """状態ディレクトリ（<名前>-state/）の隣の計画（<名前>.json）から (計画のパス, フェーズ) を読む。"""
    d = Path(progress).resolve().parent
    if d.name.endswith("-state"):
        plan = d.with_name(d.name[: -len("-state")] + ".json")
        try:
            data = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "", "?"
        phase = data.get("フェーズ") or data.get("持ち場") if isinstance(data, dict) else None
        return str(plan), phase or "?"
    return "", "?"


def import_progress(paths, history: Path) -> dict:
    """progress.jsonl の step の行（run・work・drive の成功と関門）を履歴へ移す。同じ行は 2 度積まない。
    返り値: {"added", "skipped", "unreadable": [パス...]}。"""
    seen = {_history_key(d) for d in _rows(history)}
    added = skipped = 0
    unreadable = []
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            unreadable.append(str(p))
            continue
        plan, phase = plan_of_progress(Path(p))
        for raw in text.splitlines():
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if (not isinstance(d, dict) or d.get("kind") != "step" or d.get("type") not in WATCHED_TYPES
                    or not keeps(d.get("exit")) or not isinstance(d.get("seconds"), (int, float))
                    or not d.get("at") or not d.get("step")):
                continue
            rec = history_record(phase, d["step"], d["type"], d["seconds"], d["exit"], plan, d["at"])
            key = _history_key(rec)
            if key in seen:
                skipped += 1
                continue
            err = append_history(history, rec)
            if err:
                raise OSError(err)
            seen.add(key)
            added += 1
    return {"added": added, "skipped": skipped, "unreadable": unreadable}


# --- 組み込みの一次の調査 ---

def probe_output(err_path, prev_size: int) -> tuple[dict, int]:
    """stderr のファイルが前の確認から伸びたか。(調査の結果, 今の大きさ) を返す。"""
    try:
        size = Path(err_path).stat().st_size if err_path else 0
        text = Path(err_path).read_text(encoding="utf-8", errors="replace") if err_path else ""
    except OSError:
        size, text = 0, ""
    last = next((l.strip()[:300] for l in reversed(text.splitlines()) if l.strip()), "")
    if size > prev_size:
        return {"name": "output", "class": "progress", "action": "wait",
                "summary": f"出力が伸びている（{size - prev_size} バイト）: {last}".rstrip(": ")}, size
    return {"name": "output", "class": "silent", "action": "judge",
            "summary": f"前の確認から出力が伸びていない: {last or '（出力なし）'}"}, size


def commit_count(cwd) -> int | None:
    """作業場所の HEAD までのコミットの数。git でなければ None。"""
    if not cwd or not Path(cwd).is_dir():
        return None
    out = _git(cwd, "rev-list", "--count", "HEAD")
    return int(out) if out and out.isdigit() else None


def probe_worker(new_lines: int, new_commits: int, last_text: str, since_last: float | None) -> dict:
    """前の確認から worker の行かコミットが足されたか。"""
    if new_lines > 0 or new_commits > 0:
        return {"name": "worker", "class": "progress", "action": "wait",
                "summary": f"worker の行 {new_lines} 件・コミット {new_commits} 件が足された"}
    ago = f"（最後の行から {round(since_last)} 秒）" if since_last is not None else ""
    return {"name": "worker", "class": "silent", "action": "judge",
            "summary": f"worker の行もコミットも足されていない。最後の行: {last_text or '無し'}{ago}"}


def fill_argv(template: str, values: dict) -> list[str]:
    """雛形を argv に分け、各要素の中の {鍵} を値で置き換える（シェルを通さない）。"""
    argv = shlex.split(template)
    out = []
    for a in argv:
        for k, v in values.items():
            a = a.replace("{" + k + "}", str(v))
        out.append(a)
    return out


def probe_cmd(template: str, values: dict, cwd, timeout: float) -> dict:
    """コマンドの一次の調査。最後の行の JSON（lib/step_result.py の形）を読む。読めなければ unknown・judge。"""
    try:
        argv = fill_argv(template, values)
    except ValueError as e:
        return {"name": "cmd", "class": "unknown", "action": "judge", "summary": f"probe を分けられない: {e}"}
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"name": "cmd", "class": "unknown", "action": "judge", "summary": f"probe が {timeout} 秒で終わらない"}
    except OSError as e:
        return {"name": "cmd", "class": "unknown", "action": "judge", "summary": f"probe を起動できない: {e}"}
    out = None
    for line in reversed(p.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                out = json.loads(line)
                break
            except ValueError:
                continue
    metrics = (out or {}).get("metrics") if isinstance(out, dict) else None
    if p.returncode != 0 or not isinstance(metrics, dict) or metrics.get("action") not in ACTIONS:
        tail = (p.stderr or p.stdout).strip()[-200:]
        return {"name": "cmd", "class": "unknown", "action": "judge",
                "summary": f"probe が読めない（exit={p.returncode}）: {tail}"}
    return {"name": "cmd", "class": str(metrics.get("class") or "unknown"), "action": metrics["action"],
            "summary": str(out.get("summary") or "")[:300], "items": (out.get("items") or [])[:10]}
