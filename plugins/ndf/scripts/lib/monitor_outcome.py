"""監視の結果（収束ループ共通層、#662）。

`monitor.py` が担当 1 者の監視を終えるたびに、2 つのファイルへ同じ辞書を残す。

| ファイル | 中身 | 読む側 |
| --- | --- | --- |
| `<stem>-monitor.json` | その担当の**最後の**監視の結果 | 結果なしの理由を引く側（stem から 1 つに決まる） |
| `monitor-outcomes.jsonl` | 監視の結果を 1 行 1 つで**追記だけ**で積む | 実行の要約（`run_metrics.py`） |

**語彙と読み書きをここ 1 か所に置く。** 書く側（`monitor.py`）と読む側（`state.py` /
`refactor_lib` / 要約）が同じ定数と関数を使う。`monitor.py` に置くと、読む側が監視の
実体を読み込むことになる（設計の決定 4）。

キーと値の形は `issues/issue-662-598-537-619-584-583-design-contracts.md` の
「監視の結果ファイル」にある。

**起動 1 回の結末を 1 つの値として読むのもここである（#729）。** `read_launch_outcome` が
結果ファイルの有無・読めるかと監視の結果を突き合わせ、使える結果（`payload`）か理由
（`reason`）と起動し直しの可否（`relaunch_same_agent`）を返す。結果なしの判断と可否の表を
cross-review / cross-refactoring がそれぞれ持つと、語彙を足すたびに片方が古くなる。
"""
# `from __future__ import annotations` を置かない。注釈が文字列になると `dataclass` が
# `sys.modules[<モジュール名>]` を引くが、読む側の多くはこのファイルを `importlib` で
# `sys.modules` に登録せずに読み込むため落ちる。実行時に評価できる形（3.10 以上）で書く。
import datetime as _dt
import json
import os
import pathlib
import sys
import threading
from dataclasses import dataclass
from typing import Any, Optional

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import clock  # noqa: E402  時刻の書き出し（#1142 の L0）

try:  # Windows には無い。無ければスレッドの排他だけで書く。
    import fcntl
except ImportError:  # pragma: no cover - POSIX では通らない
    fcntl = None  # type: ignore[assignment]

# 理由の語彙。先頭の 6 語は監視の状態（`status`）から決まる。`usage_limit` / `cli_timeout` は
# 監視が文言の照合で結末に添えたときだけ現れ、`unparsable` は読む側（`read_launch_outcome`）
# だけが書く（#729 の決定 7）。
REASONS = (
    "ok", "timeout", "stalled", "early_error", "missing", "pidfile_bad",
    "usage_limit", "cli_timeout", "unparsable",
)

# 同じ担当を同じ条件で起動し直しても解けない理由。利用上限は起動のたびに待ちと相手の
# 枠を使うだけで直らない（#619）。それ以外は対象や負荷で変わりうるので 1 度は起動し直せる。
# **理由を足すときはこの集合だけを見直す。** 偽のときに何をするかは Skill が決める。
NO_RELAUNCH_REASONS = frozenset({"usage_limit"})

# 監視がこの理由を書いていれば、監視が止めたか、結果を書けない終わり方をしたと分かっている。
# 結果ファイルの状態を見ずにその値を採る（`ok` / `missing` は結果ファイルの側で決め直す）。
_MONITOR_DECIDED_REASONS = frozenset(
    {"timeout", "stalled", "early_error", "usage_limit", "cli_timeout", "pidfile_bad"})

_STATUS_REASON = {
    "OK": "ok",
    "TIMEOUT": "timeout",
    "STALLED": "stalled",
    "EARLY_ERROR": "early_error",
    "NO_RESULT": "missing",
    "PIDFILE_BAD": "pidfile_bad",
}

# 結果ファイルのキー。**並びも契約の文書の表と揃える**（読む人が突き合わせやすい）。
# `phase` は P2（#598 / #537）で足した `--phase` の値。省いたときは null で、契約の文書の
# とおり末尾に置く。
OUTCOME_KEYS = (
    "agent", "stem", "status", "exit_code", "reason", "detail",
    "launched_at", "started_at", "ended_at", "elapsed", "idle_seconds",
    "progress_tail", "result_exists", "pid", "phase",
)

JOURNAL_NAME = "monitor-outcomes.jsonl"

# 同じプロセスの担当ごとのスレッドが同じ記録へ追記する。
_JOURNAL_LOCK = threading.Lock()


def iso_from_timestamp(ts: float) -> str:
    """UNIX 時刻（ファイルの更新時刻など）を、`clock.now_iso()` と同じ形へ変える。"""
    return clock.iso(_dt.datetime.fromtimestamp(ts, _dt.timezone.utc))


def reason_for(status: str) -> str:
    """監視の状態から理由を決める。表に無い状態は呼び出し側の誤りとして落とす。"""
    try:
        return _STATUS_REASON[status]
    except KeyError:
        raise ValueError(f"監視の状態として知らない値です: {status!r}") from None


def relaunch_same_agent(reason: Optional[str]) -> bool:
    """同じ担当を同じ条件で起動し直せば解けるか。`NO_RELAUNCH_REASONS` に無ければ可。"""
    return reason not in NO_RELAUNCH_REASONS


def outcome_path(tmp_dir: os.PathLike[str] | str, stem: str) -> pathlib.Path:
    return pathlib.Path(tmp_dir) / f"{stem}-monitor.json"


def default_result_path(tmp_dir: os.PathLike[str] | str, stem: str) -> pathlib.Path:
    """結果ファイルの既定の置き場所。`launch-cli.sh` の `<stem>-result.json` と同じ形。"""
    return pathlib.Path(tmp_dir) / f"{stem}-result.json"


def journal_path(tmp_dir: os.PathLike[str] | str) -> pathlib.Path:
    return pathlib.Path(tmp_dir) / JOURNAL_NAME


def write_outcome(tmp_dir: os.PathLike[str] | str, stem: str,
                  outcome: dict[str, Any]) -> None:
    """結果ファイルを原子的に置き換える。読みかけの半端な JSON を残さない。"""
    path = outcome_path(tmp_dir, stem)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(outcome, ensure_ascii=False), encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def read_outcome(tmp_dir: os.PathLike[str] | str, stem: str) -> Optional[dict[str, Any]]:
    """結果ファイルを読む。無い・読めないときは `None`。"""
    try:
        data = json.loads(outcome_path(tmp_dir, stem).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class LaunchOutcome:
    """起動 1 回の結末。`payload` があれば使える結果、無ければ `reason` が理由。"""
    payload: Optional[dict[str, Any]]
    reason: Optional[str]
    detail: str
    monitor: Optional[dict[str, Any]]
    relaunch_same_agent: bool


def _read_result_file(path: pathlib.Path) -> tuple[Optional[dict[str, Any]], str]:
    """結果ファイルを読む。使える辞書か、無ければ結果なしの理由（`missing` / `unparsable`）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None, "missing"
    if not text.strip():
        return None, "missing"
    try:
        data = json.loads(text)
    except ValueError:
        return None, "unparsable"
    return (data, "") if isinstance(data, dict) else (None, "unparsable")


def read_launch_outcome(tmp_dir: os.PathLike[str] | str, stem: str,
                        result_path: Optional[os.PathLike[str] | str] = None) -> LaunchOutcome:
    """起動 1 回の結末を 1 つの値として読む（#729 の決定 2）。

    **結果ファイルが JSON オブジェクトとして読めれば使える結果が勝つ。** 監視が止めた後にも
    結果ファイルが残っていれば、止める前に書き終えていた結果である。無いときの理由は、監視が
    理由を知っていればその値、知らなければ結果ファイルの状態（無い・空 → `missing`、あるが
    読めない → `unparsable`）で決める。

    **失敗しない。** 例外・`SystemExit`・標準出力/標準エラーへの出力を出さない。壊れた監視の
    結果ファイルは無いものとして扱う。読む側（両 Skill の取り込み）が終了コードを決める。
    """
    path = (pathlib.Path(result_path) if result_path is not None
            else default_result_path(tmp_dir, stem))
    payload, result_reason = _read_result_file(path)
    monitor = read_outcome(tmp_dir, stem)
    monitor_detail = str(monitor.get("detail") or "") if monitor else ""
    if payload is not None:
        return LaunchOutcome(payload=payload, reason=None, detail=monitor_detail,
                             monitor=monitor, relaunch_same_agent=True)
    monitor_reason = monitor.get("reason") if monitor else None
    reason = monitor_reason if monitor_reason in _MONITOR_DECIDED_REASONS else result_reason
    detail = monitor_detail or _unusable_detail(path, result_reason)
    return LaunchOutcome(payload=None, reason=reason, detail=detail, monitor=monitor,
                         relaunch_same_agent=relaunch_same_agent(reason))


def _unusable_detail(path: pathlib.Path, result_reason: str) -> str:
    """監視の詳細が無いときに `detail` へ置く、結果を読めなかった理由の 1 文。"""
    if result_reason == "missing":
        return f"結果ファイルが無い、または空です: {path}"
    return f"結果ファイルが JSON オブジェクトとして読めません: {path}"


def append_journal(tmp_dir: os.PathLike[str] | str, outcome: dict[str, Any]) -> None:
    """記録へ 1 行を追記する。

    **1 行を 1 回の `write` で書き、ファイルの排他を掛ける。** 担当ごとのスレッドと、
    別プロセスの監視（レビューと反証）が同じファイルへ追記しうる。`O_APPEND` だけでは
    書き込みが分割されたときに行が混ざりうるため、排他で塞ぐ。
    """
    line = (json.dumps(outcome, ensure_ascii=False) + "\n").encode("utf-8")
    path = journal_path(tmp_dir)
    with _JOURNAL_LOCK:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                view = view[written:]
        finally:
            os.close(fd)  # 閉じれば排他も外れる


def read_journal(tmp_dir: os.PathLike[str] | str) -> list[dict[str, Any]]:
    """記録の全行を読む。読めない行は飛ばす（書きかけの末尾行を含む）。"""
    try:
        text = journal_path(tmp_dir).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _record_outcome(
    agent: str, pr: int, stem_template: str, st: Any, started_at: str,  # st は monitor_types.AgentStatus
    phase: Optional[str] = None,
) -> None:
    """担当 1 者の監視の結果を、結果ファイルと記録へ書く（#662）。

    **書けなくても監視の結果は変えない。** 終了コードと標準出力は呼び出し側の分岐が
    読むため、書き出しの失敗は標準エラーへ 1 行出すだけにする。
    """
    import monitor_types  # 読む側（state.py ほか）が監視の型を読まずに済むよう、書くときだけ読む
    stem = stem_template.format(agent=agent, id=pr)
    try:
        paths = monitor_types.AgentPaths.for_(agent, pr, stem_template)
        # 結末が理由を持てばそれを、無ければ状態からの既定を書く（#729 の決定 8）
        st.reason = (st.outcome.reason if st.outcome and st.outcome.reason
                     else reason_for(st.status))
        st.started_at = started_at
        st.ended_at = clock.now_iso()
        try:
            st.launched_at = iso_from_timestamp(
                paths.pidfile.stat().st_mtime)
        except OSError:
            st.launched_at = None
        outcome = {
            "agent": agent,
            "stem": stem,
            "status": st.status,
            "exit_code": st.exit_code,
            "reason": st.reason,
            "detail": st.detail,
            "launched_at": st.launched_at,
            "started_at": st.started_at,
            "ended_at": st.ended_at,
            "elapsed": round(st.elapsed, 1),
            "idle_seconds": round(st.idle_seconds, 1),
            "progress_tail": st.progress_tail,
            "result_exists": st.result_exists,
            "pid": st.pid,
            # `--phase` の値。省いたときは null（#598 / #537）
            "phase": phase,
        }
        # 組み立てたキー集合を正本（`OUTCOME_KEYS`）と突き合わせる。
        # キーを片方だけへ足すと、ここで食い違いがその場で落ちる（#662）。
        assert set(outcome) == set(OUTCOME_KEYS), (
            set(outcome).symmetric_difference(OUTCOME_KEYS))
        tmp_dir = paths.pidfile.parent
        write_outcome(tmp_dir, stem, outcome)
        append_journal(tmp_dir, outcome)
    except Exception as exc:  # noqa: BLE001  書き出しの失敗で監視を落とさない
        print(f"[{agent}] ⚠ 監視の結果を書けません（{stem}）: {exc}",
              file=sys.stderr, flush=True)
