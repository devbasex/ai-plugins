"""判定の子（#4000 の決定 1・4・8・9・10・11）。

hook の閉包の外で動く（hook の側はモジュール名の文字列で起動するだけで import しない）。hook の側（`ndf.domains.hooks.reply_judge`）が新しいプロセスの組でこのモジュールを
起動し、標準入力に判定の入力を渡して、標準出力の 1 行の JSON（`{"kind"|"failure", "calls"}`）を読む。

- `endpoint_refusal`: 送信先がセッション自身の Claude の API と同じかを、親の起動引数と実効の設定で確かめる（決定 11）
- `classify`: 質問の文で軽いモデルを 1 回だけ打ち、`wait` か `report` の 1 語を読む（決定 1・4・8）
- `main`: 標準入力の判定の入力を読み、送信先を確かめてから `classify` を打ち、1 行の JSON を書く
- `run_cases`: 判定の例の組を周の数だけ判定する（`ndf hooks judge-cases`。決定 9）

設定（モデル・秒・周の数・認証の失敗の文言・送信先の確かめの値）は `limits.json` の `hook.stop-wait.judge`
から `setting` が読む。値をこのモジュールへ写さない。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, MutableMapping, Optional, Sequence

from ndf import contracts
from ndf.adapters import claude
from ndf.contracts import facts, limits
from ndf.domains.hooks import reply_judge
from ndf.domains.relay import account_dir, claude_args

# 判定の設定の方針の名前（`contracts/limits.json`）
POLICY = "hook.stop-wait.judge"
# 判定の例の組（`contracts/` からの相対）
CASES = "hooks/reply-judge-cases.json"
# 相対パスなら送信先を確かめられないとみなす設定ディレクトリの変数（判定用の `claude` はセッションと違う
# 作業ディレクトリで動くので、相対パスが同じディレクトリに解ける保証が無い）
RELATIVE_REFUSED = ("CLAUDE_CONFIG_DIR", "ANTHROPIC_CONFIG_DIR")
# 打つ前に残っていなければならない秒。これを切れば起動せずに時間切れにする
LEAST_SECONDS = 0.5
# 質問の文で本文を囲むタグ
OPEN_TAG = "<reply>"
CLOSE_TAG = "</reply>"
# 判定の子が軽いモデルへ渡す役割の文（決定 4）
SYSTEM = "あなたは AI の最後の応答を読み、人の回答を待って終えているかだけを判定する。ほかの仕事をしない。"
# 質問の文（決定 4。外部の調査の MEA6 の形。通すべき報告の形を例として並べ、1 語だけを返させる）
QUESTION = f"""{OPEN_TAG} と {CLOSE_TAG} の間は、AI が作業の区切りで書いた最後の応答である。
応答を書いた本人が人に判断・選択・承認を求め、その回答を待って終えているなら wait、そうでなければ report と回答する。

- 質問が応答の途中にあり、後に報告が続いていても wait
- 次のどれかは report
  - 他人の質問を「」で引用して経緯を説明している
  - ほかのセッションやレーンの承認待ちを伝えている
  - 承認を求める資料を用意したことや作業の結果を報告している

wait か report の 1 語だけを回答し、ほかに何も書かない。"""

# 判定の 1 回の呼び出し（`claude.call_claude` と同じ形）
Caller = Callable[..., claude.ClaudeReply]
# 空の作業ディレクトリを作る親の候補（先頭は環境の一時領域。`TMPDIR` などで変わる）と、親か祖先にあれば
# プロジェクトの中とみなす名前（claude は作業ディレクトリから上の CLAUDE.md を読む）
SCRATCH_PARENTS = ("/tmp", "/var/tmp", "/dev/shm")
PROJECT_MARKS = ("CLAUDE.md", "CLAUDE.local.md", ".git", os.path.join(".claude", "CLAUDE.md"))


class JudgeSettingError(contracts.ContractError):
    """判定の設定の行が無い・引いた値が無い。"""


class JudgeRefused(RuntimeError):
    """判定の起動を不変条件で断られた（学習の設定・CLI の下限・中継の確かめ）。`reason` は理由の文。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def setting(key: str) -> Any:
    """判定の設定（`limits.json` の `hook.stop-wait.judge`）の 1 つの値。

    方針の行を `ndf.contracts` から読む口はここ 1 つで、値をこのモジュールへ写さない。
    """
    with contracts.raised_as(JudgeSettingError):
        row = limits.policy(POLICY)._asdict()
    got = row.get(key)
    if got is None:
        raise JudgeSettingError(f"上限の設定の方針 {POLICY} に {key} が無い")
    return got


def cases_path() -> Path:
    """判定の例の組の既定のパス。"""
    return Path(contracts.contracts_dir()) / CASES


# ---------------------------------------------------------------- 送信先の確かめ（決定 11）


def endpoint_refusal(
    env: Mapping[str, str],
    parent_argv: Sequence[str],
    project_dir: Path,
    resolve: Callable[..., account_dir.Resolved] = account_dir.effective_env,
) -> Optional[str]:
    """送信先がセッション自身の Claude の API と同じと確かめられない理由の項目名（値を含まない）。確かめられれば None。

    親の起動引数の接続のフラグと、実効の設定（解けない源・`apiKeyHelper`・指定のキー・設定の層が出所の接続の
    変数）を見る。子の環境が出所の変数は、セッション自身が使う接続なので理由にしない。
    相対パスの設定ディレクトリ（`RELATIVE_REFUSED`）は、セッションと判定用の `claude` の作業ディレクトリが違うと
    別のディレクトリに解けるので、同じ送信先と確かめられないとみなす。
    """
    endpoint = setting("endpoint")
    names = {name for name, _ in claude_args.options(list(parent_argv)[1:])}
    flagged = next((f for f in endpoint["flags"] if f in names), None)
    if flagged is not None:
        return flagged
    relative = next((n for n in RELATIVE_REFUSED if env.get(n) and not Path(env[n]).is_absolute()), None)
    if relative is not None:
        return relative
    config = env.get("CLAUDE_CONFIG_DIR")
    where = Path(config) if config else Path.home() / ".claude"
    got = resolve(env, where, project_dir, list(parent_argv), extra=account_dir.Extra(keys=tuple(endpoint["keys"])))
    return _resolved_refusal(got, tuple(endpoint["env_names"]))


def _resolved_refusal(got: account_dir.Resolved, prefixes: tuple[str, ...]) -> Optional[str]:
    """実効の設定から、送信先を確かめられない理由の項目名。無ければ None。"""
    if got.unresolved:
        return got.unresolved[0]
    if got.helper:
        return "apiKeyHelper"
    if got.key_origin:
        return min(got.key_origin)
    return min((k for k, o in got.origin.items() if o != account_dir.CHILD_ENV and k.startswith(prefixes)), default=None)


# ---------------------------------------------------------------- 1 回の判定（決定 1・4・8）


def classify(
    text: str,
    seconds: float,
    exe: Optional[str],
    call: Caller = claude.call_claude,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """質問の文で軽いモデルを 1 回だけ打ち、`{"kind", "calls"}` か `{"failure", "calls", "error"}` を返す。

    `exe` は起動する実行ファイル（hook から来て `main` が親の実体と同じと確かめた実パス）。None なら PATH の
    `claude`（`ndf hooks judge-cases`）で、スロットを設定の上限まで待ち、新しい組で起動して CLI の下限も確かめる。
    """
    start = clock()
    left = seconds - (clock() - start)
    if left < LEAST_SECONDS:
        return {"failure": "timeout", "calls": 0, "error": None}
    parent = scratch_parent()
    if parent is None:
        return {"failure": "launch", "calls": 0, "error": "プロジェクトの外の一時領域が無い"}
    spec = claude.ClaudeCall(system=SYSTEM, permission=claude.Permission.JUDGE, model=setting("model"))
    argv = claude.claude_args(spec, base=(exe,)) if exe else claude.claude_args(spec)
    # プロジェクトの外の空の作業ディレクトリで起動する。プロジェクトの中で起動すると、claude がそこから上の CLAUDE.md を
    # 読んで質問の前に置き（`--setting-sources ''` でも読む）、回答がプロジェクトの指示に引きずられる（引用を待ちと読む）
    with tempfile.TemporaryDirectory(prefix="ndf-judge-", dir=parent) as empty:
        reply = call(argv, _prompt(text), left, cwd=Path(empty), options=_options(exe))
    return _read(reply)


def scratch_parent(candidates: Optional[Sequence[str]] = None) -> Optional[str]:
    """空の作業ディレクトリを作る親: 自身と祖先に `PROJECT_MARKS` の無い、書ける最初の候補。無ければ None。

    候補の既定は環境の一時領域（`tempfile.gettempdir()`）の後に `SCRATCH_PARENTS`。一時領域をリポジトリの中に
    置いた環境でも、祖先のプロジェクトの指示を判定の入力に入れない。
    """
    for path in candidates if candidates is not None else (tempfile.gettempdir(), *SCRATCH_PARENTS):
        real = Path(os.path.realpath(path))
        if not real.is_dir() or not os.access(real, os.W_OK | os.X_OK):
            continue
        if not any((d / mark).exists() for d in (real, *real.parents) for mark in PROJECT_MARKS):
            return str(real)
    return None


def _options(exe: Optional[str]) -> claude.LaunchOptions:
    """前置きの選び方。親の実体を打つ hook からの起動は、スロットを待たず・同じ組で・下限を確かめない（決定 8）。"""
    if exe:
        return claude.LaunchOptions(slot_wait=0, new_group=False, cli_floor=False, ensure_training=False)
    return claude.LaunchOptions(ensure_training=False, switch_account=False)


def _prompt(text: str) -> str:
    """質問の文に本文を囲んで足した、1 回の起動へ渡す質問。"""
    return f"{QUESTION}\n{OPEN_TAG}\n{text}\n{CLOSE_TAG}"


def _read(reply: claude.ClaudeReply) -> dict:
    """1 回の回答を判定の返りにする。回答の揺れ・失敗の種類の対応付けはここ 1 つが持つ。"""
    if reply.failure is not None:
        failure = str(reply.failure)
    elif not reply.ok:
        failure = "auth" if _auth(reply.raw) else "exit"
    elif (word := "".join(reply.raw.split())) in reply_judge.KINDS:
        return {"kind": word, "calls": 1}
    else:
        failure = "shape"
    return {"failure": failure, "calls": 1, "error": reply.error}


def _auth(raw: str) -> bool:
    """失敗の本文が、認証の失敗と読む文字列（設定の `auth_markers`。決定 10・MEA8）を含むか。"""
    return any(marker in raw for marker in setting("auth_markers"))


# ---------------------------------------------------------------- 子のエントリポイント


def main(argv: Optional[list[str]] = None, stat: Callable[[str], Any] = os.stat, call: Caller = claude.call_claude) -> int:
    """標準入力の判定の入力を読み、送信先を確かめてから 1 回打ち、1 行の JSON を標準出力へ書く。

    `claude` を起動する直前に `stat(parent_exe)` の `[st_dev, st_ino]` を `parent_exe_id` と比べ、同じときだけ
    `parent_exe` を起動の実行ファイルとして使う（PATH で引き直さない。決定 11）。
    """
    del argv  # 引数は取らない（hook の側は標準入力だけで渡す）
    got = _input(sys.stdin.read())
    if got is None:
        return _write({"failure": "shape", "calls": 0})
    if _endpoint_broken(got, stat) is not None:
        return _write({"failure": "endpoint", "calls": 0})
    answer = classify(got["text"], float(got["seconds"]), got["parent_exe"], call)
    return _write({k: v for k, v in answer.items() if k != "error"})


def _write(answer: dict) -> int:
    """判定の返りを 1 行の JSON で標準出力へ書く。"""
    print(json.dumps(answer, ensure_ascii=False))
    return 0


def _input(text: str) -> Optional[dict]:
    """標準入力の JSON。欠けか形が違えば None（`main` が `shape` を書く）。"""
    try:
        got = json.loads(text)
    except ValueError:  # noqa: NDF401 利用者の入力: 呼び手の誤りは形の違いとして扱う
        return None
    need = ("text", "seconds", "parent_argv", "project_dir", "parent_exe", "parent_exe_id")
    if not isinstance(got, dict) or not all(k in got for k in need):
        return None
    exe, ident = got["parent_exe"], got["parent_exe_id"]
    if not isinstance(exe, str) or not Path(exe).is_absolute() or not _two_ints(ident):
        return None
    return got if isinstance(got["text"], str) and isinstance(got["seconds"], (int, float)) else None


def _two_ints(value: Any) -> bool:
    """実行ファイルの実体を指す値（`[st_dev, st_ino]`）の形か。"""
    return isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in value)


def _endpoint_broken(got: dict, stat: Callable[[str], Any]) -> Optional[str]:
    """送信先を確かめられない理由の項目名（確かめられれば None）。確かめ自体が落ちたときも確かめられないとみなす。

    実効の設定を見た後、起動の直前の最後の確かめとして、親の起動後に実行ファイルが差し替わっていないか
    （`stat(parent_exe)` の `[st_dev, st_ino]` が `parent_exe_id` と同じか）を見る。消えていれば `stat` の例外で理由になる。
    """
    try:
        reason = endpoint_refusal(os.environ, got["parent_argv"], Path(got["project_dir"]))
        if reason is not None:
            return reason
        found = stat(got["parent_exe"])
    except Exception as exc:  # noqa: BLE001 ファイル: 想定外の失敗でも送信先を確かめられないので起動しない
        return type(exc).__name__
    return None if [found.st_dev, found.st_ino] == got["parent_exe_id"] else "parent_exe"


# ---------------------------------------------------------------- 判定の例の組（決定 9）


def run_cases(path: Path, rounds: int, call: Caller = claude.call_claude) -> dict:
    """判定の例の組を 1 件ずつ `rounds` 周判定し、外れた例・件数・打った回数・秒を返す。

    判定が `refused` で返れば残りの例を打たずに `JudgeRefused` を投げる。組が読めない・`rounds` が正の整数でなければ
    `ValueError`。打つ間は、Stop の経路で hook の側が子へ渡さない変数（環境変数の表の `child` が `drop` の行。
    `CLAUDECODE`・`CLAUDE_CODE_SESSION_ID` を含む）をこのプロセスの環境から外し、Claude Code のセッションの中で
    打っても入れ子の起動として断られないようにする（終われば戻す）。
    """
    if not positive_rounds(rounds):
        raise ValueError(f"周の数は 1 以上の整数にする: {rounds!r}")
    cases = _load_cases(path)
    seconds = float(setting("seconds"))
    missed: list[dict] = []
    spent: list[float] = []
    with _outside_session(os.environ):
        for case in cases:
            got = _rounds(case, rounds, seconds, call)
            spent += [used for _, used in got]
            wrong = [word for word, _ in got if word != case["expect"]]
            if wrong:
                missed.append({"id": case["id"], "expect": case["expect"], "got": wrong})
    return {"items": missed, "metrics": _metrics(len(cases), len(missed), spent)}


def positive_rounds(value: Any) -> bool:
    """周の数として使える値（1 以上の整数。真偽値は外す）か。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


@contextlib.contextmanager
def _outside_session(environ: MutableMapping[str, str]) -> Iterator[None]:
    """環境変数の表の `child` が `drop` の変数を外し、Stop の判定と同じく思考を切る。抜けるときに元の値へ戻す。"""
    names = [*facts.ids("env", child="drop"), *reply_judge.THINKING_OFF]
    kept = {name: environ.pop(name) for name in names if name in environ}
    environ.update(reply_judge.THINKING_OFF)
    try:
        yield
    finally:
        for name in reply_judge.THINKING_OFF:
            environ.pop(name, None)
        environ.update(kept)


def _metrics(cases: int, missed: int, spent: list[float]) -> dict:
    """件数・外れた例の件数・打った回数・秒の最小と最大。"""
    return {
        "cases": cases,
        "missed": missed,
        "calls": len(spent),
        "min_seconds": round(min(spent), 3) if spent else 0.0,
        "max_seconds": round(max(spent), 3) if spent else 0.0,
    }


def _rounds(case: dict, rounds: int, seconds: float, call: Caller) -> list[tuple[str, float]]:
    """1 つの例を `rounds` 周判定し、(回答か失敗の種類, 秒) を周ごとに返す。"""
    out = []
    for _ in range(rounds):
        start = time.monotonic()
        answer = classify(case["text"], seconds, None, call)
        out.append((_answer_word(answer), time.monotonic() - start))
    return out


def _answer_word(answer: dict) -> str:
    """判定の返りの 1 語（失敗なら種類）。`refused` は残りを打たずに投げる。"""
    if answer.get("failure") == "refused":
        raise JudgeRefused(answer.get("error") or "refused")
    return answer["kind"] if "kind" in answer else str(answer["failure"])


def _load_cases(path: Path) -> list[dict]:
    """判定の例の組を読む。無い・JSON でない・形が違うなら `ValueError`。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"判定の例の組を読めない: {path}: {exc}") from exc
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases or not all(_is_case(c) for c in cases):
        raise ValueError(f"判定の例の組の形が違う: {path}")
    return cases


def _is_case(case: Any) -> bool:
    """例 1 件の形（`id`・`text`・`expect`）か。"""
    if not isinstance(case, dict) or not all(isinstance(case.get(k), str) for k in ("id", "text")):
        return False
    return case.get("expect") in ("wait", "report")


if __name__ == "__main__":  # pragma: no cover - 子プロセスのエントリポイント
    sys.exit(main())
