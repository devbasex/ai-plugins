"""相手役の割り当てと、相手役の 1 回の起動の組み立て。

- 母集合はランタイムの表（`contracts/domains/runtimes.json`）の `default_seat` が真で `review_args` を持つ行で、
  表の行の順に並べる。修正担当（`claude -p` を打つランタイム）は相手役にしない。利用者が名指した相手役は
  `review_args` を持てば `default_seat` が偽でも入れる
- 相手役は読むだけの起動で、指摘を最後の 1 行の JSON（`{"findings": [...]}`）で返す。起動の引数は表の
  `review_args` が持つ（`{workdir}` は worktree に置き換える）。質問は最後の引数で渡す
- `{sandbox}` は表の `sandbox_args` に広げる。ユーザー名前空間を作れない環境では `unsandboxed_args` に広げる
  （codex のサンドボックスがコマンドを打てず差分を読めない。#3893）
- リポジトリのランタイムの設定（`.ndf/runtimes.json` の `allowed`。メインディレクトリから読む）があれば、相手役をその中から
  選び、外の名前を名指すか修正担当が外なら `UnknownParticipant`（#3941。`declared_allowed`）
- 始める前に担当すべての学習の設定のバックアップを確かめる（`unready`）。無いか期限切れならその場で確かめ、
  それでも起動できない担当があれば、提案やレビューを流す前に止める（#3907）
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.adapters import agents, claude, training
from ndf.contracts import facts, limits

RUNTIMES = "runtimes"
REVIEW_ARGS = "review_args"
SANDBOX = "{sandbox}"
# 修正担当: `claude -p` を打つランタイム（claude のアダプターの実行ファイルの行）
FIXER = next(r["id"] for r in facts.rows(RUNTIMES) if r["executable"] == claude.CLAUDE)
# 相手役・修正担当 1 回の監視の上限
REVIEW_TIMEOUT = "monitor.phase.review"


class UnknownParticipant(ValueError):
    """名指した相手役がランタイムの表に無いか、読むだけの起動の引数を持たない。"""


def _check_requested(requested: Sequence[str], able: Sequence[str]) -> None:
    """名指した相手役が読むだけの起動を持つかを確かめる。持たないものがあれば `UnknownParticipant`。"""
    bad = [r for r in requested if r not in able]
    if bad:
        raise UnknownParticipant(f"相手役 {'・'.join(bad)} はランタイムの表に無いか、読むだけの起動を持たない（{'・'.join(able)}）")


def assign(requested: Sequence[str] = (), fixer: str = FIXER, allowed: Optional[Collection[str]] = None) -> tuple[str, ...]:
    """相手役を決める。`requested` が空なら表の既定の席から、読むだけの起動を持つものを修正担当を除いて並べる。
    `allowed`（`.ndf/runtimes.json` の `allowed`）を渡すと、その外の相手役を選ばず、名指せば `UnknownParticipant`。
    修正担当 `fixer` が `allowed` の外でも、選べる相手役が残らなくても `UnknownParticipant`（どの担当も起動する前に止める）。"""
    if allowed is not None and fixer not in allowed:
        raise UnknownParticipant(f"修正担当 {fixer} がランタイムの設定の allowed（{'・'.join(sorted(allowed))}）に無い")
    able = [r["id"] for r in facts.rows(RUNTIMES) if REVIEW_ARGS in r and (allowed is None or r["id"] in allowed)]
    if requested:
        _check_requested(requested, able)
        return tuple(dict.fromkeys(r for r in requested if r != fixer))
    seats = set(facts.ids(RUNTIMES, default_seat=True))
    chosen = tuple(r for r in able if r in seats and r != fixer)
    if not chosen:
        names = "・".join(sorted(allowed)) if allowed is not None else "—"
        raise UnknownParticipant(f"ランタイムの設定の allowed（{names}）に、既定の席の相手役が無い")
    return chosen


# リポジトリのランタイムの設定（表 ndf-files の行）と形
POLICY_FILE = "runtime-policy"
POLICY_SCHEMA = "schemas/ndf-settings/runtime-policy.schema.json"


def declared_allowed(main: Path) -> Optional[frozenset[str]]:
    """メインディレクトリ `main` の `.ndf/runtimes.json` の `allowed`。設定が無ければ None。読めない・形が違えば
    `UnknownParticipant`（認可の設定を黙って無視しない）。"""
    location = facts.value("ndf-files", POLICY_FILE, "location")
    path = main / facts.value("locations", location, "path") / facts.value("ndf-files", POLICY_FILE, "path")
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        contracts.require_schema(raw, POLICY_SCHEMA, contracts.ContractError, str(path))
    except (OSError, ValueError, contracts.ContractError) as exc:
        raise UnknownParticipant(f"ランタイムの設定 {path} を読めない: {exc}") from exc
    return frozenset(raw["allowed"])


def unready(runtimes: Sequence[str]) -> list[str]:
    """起動できない担当の理由の並び（すべて起動できれば空）。学習の設定のバックアップが無いか期限切れなら、その場で確かめて記録する。"""
    reasons = (training.ensure(rt) for rt in dict.fromkeys(runtimes))
    return [why for why in reasons if why is not None]


def _sandbox_args(row: dict[str, Any]) -> list[str]:
    """`{sandbox}` に入れる引数。名前空間を作れない環境で `unsandboxed_args` を持つ行はそれを、ほかは `sandbox_args`。"""
    if "unsandboxed_args" in row and not agents.user_namespaces():
        return list(row["unsandboxed_args"])
    return list(row["sandbox_args"]) if "sandbox_args" in row else []


def _review_args(row: dict[str, Any], worktree: Path) -> tuple[str, ...]:
    out: list[str] = []
    for a in row[REVIEW_ARGS]:
        out += _sandbox_args(row) if a == SANDBOX else [a.replace("{workdir}", str(worktree))]
    return tuple(out)


def review_launch(agent: str, worktree: Path, prompt: str, stem: Path, timeout: Optional[float] = None) -> agents.Launch:
    """相手役 `agent` の読むだけの 1 回の起動。出力は `stem` の名前のファイルへ書く。"""
    row = facts.row(RUNTIMES, agent)
    args = _review_args(row, worktree)
    limit = timeout if timeout is not None else limits.seconds(REVIEW_TIMEOUT)
    return agents.Launch(agent, (row["executable"], *args, prompt), worktree, agents.AgentPaths.at(stem), limit)


def _decode_at(decoder: json.JSONDecoder, text: str, at: int) -> Any:
    """`at` から始まる JSON の値。JSON の始まりでなければ None。"""
    try:
        found, _ = decoder.raw_decode(text, at)
    except json.JSONDecodeError:
        # 相手役の出力の途中の `{` は JSON の始まりでない。呼び手が前の `{` を試す
        found = None
    return found


def _listed_in(found: Any, key: str) -> Optional[list[dict[str, Any]]]:
    """`{key: [...]}` の形なら辞書の要素だけを返す。ほかは None。"""
    if isinstance(found, dict) and isinstance(found.get(key), list):
        return [f for f in found[key] if isinstance(f, dict)]
    return None


def listed_of(text: str, key: str) -> Optional[list[dict[str, Any]]]:
    """担当の出力の最後の `{key: [...]}` の要素。見つからなければ None（空の並びと分ける）。

    読むだけの起動の担当はファイルを書けないため、結果を標準出力の最後の JSON で返す（cross-refactoring の提案も同じ形）。
    """
    decoder = json.JSONDecoder()
    at = text.rfind("{")
    while at >= 0:
        found = _listed_in(_decode_at(decoder, text, at), key)
        if found is not None:
            return found
        at = text.rfind("{", 0, at)
    return None


def findings_of(text: str) -> Optional[list[dict[str, Any]]]:
    """相手役の出力の最後の `{"findings": [...]}` の指摘。見つからなければ None（空の指摘と分ける）。"""
    return listed_of(text, "findings")
