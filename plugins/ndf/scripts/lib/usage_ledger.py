"""使用量の帳簿: NDF が起動したエージェントの呼び出し 1 回ごとに 1 行を追記する jsonl（#1142・#1953）。

帳簿はランタイムごとに分ける（claude・codex・kiro・agy・jev）。料金体系が違うため、帳簿の間で単位を足さない。
置き場所は `<帳簿のディレクトリ>/<runtime>/<owner>__<repo>.jsonl`。帳簿のディレクトリは検査の記録
（`check-trigger.py`）と同じ順で決める:

1. `NDF_USAGE_DIR`（テストと、置き場所を明示したいとき）
2. `${CLAUDE_PLUGIN_DATA}/usage`
3. `${XDG_STATE_HOME}/ndf/usage`
4. `~/.local/state/ndf/usage`

帳簿のディレクトリの直下の `<owner>__<repo>.jsonl` は版 1（#1142）の行で、読むときに claude の行とみなす。

**1 行 1 呼び出しの事象の記録にする。** 既存の行は書き換えない（I4）。合計は読む側が導く。
claude の `usage` と `model_usage` は `claude -p` の結果の `usage` と `modelUsage` をそのまま持つ。5 分と 1 時間の
書き込みの区別は `usage.cache_creation` にしか無く、合計にした時点で戻らないためである。
使用量を取れなかった行は `usage` が `"unknown"` で、0 の数値を書かない（I3）。

ライブラリなので、標準ライブラリとライブラリだけを import する（I1）。
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

LIB = Path(__file__).resolve().parent
RUNTIMES = ("claude", "codex", "kiro", "agy", "jev")  # 帳簿のランタイム（cross-review の席順の ALL_RUNTIMES とは別）
STATUSES = ("ok", "error", "timeout", "killed")
SCHEMA = 2
UNKNOWN = "unknown"


def ledger_dir(env: Optional[dict] = None) -> Path:
    """帳簿のディレクトリ（モジュールの docstring の順）。"""
    env = os.environ if env is None else env
    if env.get("NDF_USAGE_DIR"):
        return Path(env["NDF_USAGE_DIR"])
    if env.get("CLAUDE_PLUGIN_DATA"):
        return Path(env["CLAUDE_PLUGIN_DATA"]) / "usage"
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "ndf" / "usage"
    if env.get("HOME"):
        return Path(env["HOME"]) / ".local" / "state" / "ndf" / "usage"
    return Path(env.get("TMPDIR") or "/tmp") / "ndf-usage"


@functools.lru_cache(maxsize=32)
def repo_key(root: str) -> str:
    """origin の URL から `<owner>__<repo>`。決められなければ `unknown`。"""
    try:
        url = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"], cwd=root, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    m = re.search(r"([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}__{m.group(2)}" if m else UNKNOWN


@functools.lru_cache(maxsize=1)
def ndf_version() -> str:
    """このライブラリを持つプラグインの `plugin.json` の版。読めなければ `unknown`。"""
    try:
        data = json.loads((LIB.parents[1] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNKNOWN
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version else UNKNOWN


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cache_writes(usage: dict) -> tuple[int, int]:
    """usage の書き込みを (5 分, 1 時間) に分ける。内訳が無ければ全部を 5 分に数える。"""
    usage = usage if isinstance(usage, dict) else {}
    cc = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    w1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
    if "ephemeral_5m_input_tokens" in cc:
        w5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
    else:
        w5 = max(0, int(usage.get("cache_creation_input_tokens") or 0) - w1h)
    return w5, w1h


def main_model(model_usage: Optional[dict]) -> str:
    """`modelUsage` の中で費用（無ければ出力）の最も大きいモデル。無ければ空。"""
    if not isinstance(model_usage, dict) or not model_usage:
        return ""

    def weight(item: tuple) -> tuple:
        v = item[1] if isinstance(item[1], dict) else {}
        return (float(v.get("costUSD") or 0), int(v.get("outputTokens") or 0))

    return max(model_usage.items(), key=weight)[0]


def _test_name(env: Optional[dict] = None) -> Optional[str]:
    """テストの中で書いた行の印（`PYTEST_CURRENT_TEST` のテストの名前。I9）。テストの外は None。"""
    cur = (os.environ if env is None else env).get("PYTEST_CURRENT_TEST") or ""
    return cur.rsplit(" (", 1)[0] or None


def known_usage(usage) -> object:
    """使用量を行の形にする。dict で空でなければそのまま、ほかは `"unknown"`（0 を書かない。I3）。"""
    return usage if isinstance(usage, dict) and usage else UNKNOWN


@dataclass
class UsageRecord:
    """帳簿の 1 行（版 2）。空を許さない列は `schema`・`runtime`・`at`・`ndf_version`・`source`・`kind`・`usage`・`status`。

    `usage` はランタイムごとの形の dict か `"unknown"`（取れなかった。I3）。プロンプト・環境変数の値・認証情報は
    持たない（I6。鍵はこの一覧だけ）。
    """

    source: str
    kind: str
    usage: object = UNKNOWN
    runtime: str = "claude"
    status: str = "ok"
    plan: str = ""
    step: str = ""
    model: str = ""
    model_usage: Optional[dict] = None
    cost_usd: Optional[float] = None
    turns: Optional[int] = None
    seconds: Optional[float] = None
    session_id: Optional[str] = None
    parent_session_id: Optional[str] = None
    agent_type: Optional[str] = None
    agent_id: Optional[str] = None
    at: str = field(default_factory=now_utc)
    ndf_version: str = field(default_factory=ndf_version)
    schema: int = SCHEMA
    pytest: Optional[str] = field(default_factory=_test_name)

    def __post_init__(self) -> None:
        self.usage = known_usage(self.usage)
        self.source = self.source or UNKNOWN
        self.kind = self.kind or UNKNOWN
        if self.runtime not in RUNTIMES:
            raise ValueError(f"帳簿の無いランタイム: {self.runtime}")
        if not self.model:
            self.model = main_model(self.model_usage)

    @classmethod
    def from_claude(
        cls,
        data: dict,
        *,
        source: str,
        kind: str,
        plan: str = "",
        step: str = "",
        seconds: Optional[float] = None,
        status: str = "ok",
    ) -> "UsageRecord":
        """`claude -p --output-format json` の結果の dict から作る。"""
        data = data if isinstance(data, dict) else {}
        mu = data.get("modelUsage")
        return cls(
            source=source,
            kind=kind,
            usage=data.get("usage"),
            status=status,
            plan=plan,
            step=step,
            model_usage=mu if isinstance(mu, dict) else None,
            cost_usd=data.get("total_cost_usd"),
            turns=data.get("num_turns"),
            seconds=seconds,
            session_id=data.get("session_id"),
        )

    def to_json(self) -> str:
        d = asdict(self)
        if d["pytest"] is None:
            del d["pytest"]
        return json.dumps(d, ensure_ascii=False, sort_keys=True)


class UsageLedger:
    """1 つのランタイム × 1 つのリポジトリの帳簿。追記と読み取りだけを持つ（書き換えない。I4）。

    置き場は `<帳簿のディレクトリ>/<runtime>/<owner>__<repo>.jsonl`。パスは `runtime` からだけ決める（I2）。
    """

    def __init__(self, root: str | os.PathLike, directory: Optional[Path] = None, runtime: str = "claude"):
        if runtime not in RUNTIMES:
            raise ValueError(f"帳簿の無いランタイム: {runtime}")
        self.dir = Path(directory) if directory is not None else ledger_dir()
        self.runtime = runtime
        self.path = self.dir / runtime / f"{repo_key(str(root))}.jsonl"

    def append(self, record: UsageRecord) -> Path:
        """1 行を追記する。行の `runtime` が帳簿と違えば ValueError、書けなければ OSError のまま投げる。"""
        if record.runtime != self.runtime:
            raise ValueError(f"{record.runtime} の行を {self.runtime} の帳簿へ書かない")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(record.to_json() + "\n")
        return self.path

    def records(self) -> Iterator[dict]:
        return read_rows(self.path)


def append_safely(root: str | os.PathLike, record: UsageRecord, directory: Optional[Path] = None) -> bool:
    """行の `runtime` の帳簿へ追記する。失敗しても投げず、stderr に 1 行を出して False を返す（I5）。"""
    try:
        UsageLedger(root, directory, record.runtime).append(record)
        return True
    except Exception as e:  # noqa: BLE001  帳簿の失敗で呼び出しの結果を失わない
        print(f"usage_ledger: 使用量の帳簿へ追記できない: {e}", file=sys.stderr)
        return False


def read_rows(path: Path) -> Iterator[dict]:
    """帳簿の 1 ファイルを読む。壊れた行と dict でない行は飛ばす。"""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            yield d


def as_v2(row: dict, runtime: str) -> dict:
    """読んだ行を版 2 の形にそろえる。版 1 の行（`schema` が無い）は claude の `ok` の行とみなし、`{}` は `"unknown"`。"""
    out = dict(row)
    out.setdefault("schema", 1)
    out.setdefault("runtime", runtime)
    out.setdefault("status", "ok")
    out["usage"] = known_usage(out.get("usage"))
    return out


def ledger_files(runtime: str, directory: Optional[Path] = None, repo: Optional[str] = None) -> list[Path]:
    """1 つの帳簿のファイル。`repo`（`<owner>__<repo>`）を渡せばそのリポジトリだけ。claude は版 1 の置き場（直下）も含む。"""
    d = Path(directory) if directory is not None else ledger_dir()
    pattern = f"{repo}.jsonl" if repo else "*.jsonl"
    files = sorted((d / runtime).glob(pattern)) if (d / runtime).is_dir() else []
    if runtime == "claude" and d.is_dir():
        files = sorted(d.glob(pattern)) + files
    return files


def read_usage_ledger(runtime: str = "claude", directory: Optional[Path] = None, repo: Optional[str] = None) -> list[dict]:
    """1 つの帳簿のすべての行（版 2 の形にそろえる）。別のランタイムの行は読まない（I7）。"""
    if runtime not in RUNTIMES:
        raise ValueError(f"帳簿の無いランタイム: {runtime}")
    return [as_v2(r, runtime) for p in ledger_files(runtime, directory, repo) for r in read_rows(p)]


def ledger_snapshot(directory: Path) -> dict[str, int]:
    """帳簿のディレクトリの下の jsonl の一覧と大きさ（テストの前後の比較に使う。I9）。"""
    d = Path(directory)
    out = {}
    for p in sorted(d.rglob("*.jsonl")) if d.is_dir() else []:
        try:
            out[str(p.relative_to(d))] = p.stat().st_size
        except OSError:
            continue
    return out


def marked_rows(directory: Path, before: dict[str, int], name: Optional[str] = None) -> list[dict]:
    """`before` の後に増えた行のうち、テストの印（`pytest`）を持つもの。`name` を渡せばそのテストの印だけ。"""
    d = Path(directory)
    out = []
    for rel, size in ledger_snapshot(d).items():
        start = before.get(rel, 0)
        if size <= start:
            continue
        try:
            with open(d / rel, "rb") as f:
                f.seek(start)
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("pytest") and (name is None or row["pytest"] == name):
                out.append({"file": rel, "pytest": row["pytest"], "source": row.get("source"), "kind": row.get("kind")})
    return out
