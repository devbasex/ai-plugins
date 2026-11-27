"""相手役の割り当てと、相手役の 1 回の起動の組み立て。

- 母集合はランタイムの表（`contracts/domains/runtimes.json`）の `default_seat` が真で `review_args` を持つ行で、
  表の行の順に並べる。修正担当（`claude -p` を打つランタイム）は相手役にしない。利用者が名指した相手役は
  `review_args` を持てば `default_seat` が偽でも入れる
- 相手役は読むだけの起動で、指摘を最後の 1 行の JSON（`{"findings": [...]}`）で返す。起動の引数は表の
  `review_args` が持つ（`{workdir}` は worktree に置き換える）。質問は最後の引数で渡す
- `{sandbox}` は表の `sandbox_args` に広げる。ユーザー名前空間を作れない環境では `unsandboxed_args` に広げる
  （codex のサンドボックスがコマンドを打てず差分を読めない。#3893）
- リポジトリのランタイムの設定（`.ndf/runtimes.json` の `allowed`。メインディレクトリから読む）があれば、相手役をその中から
  選び、外の名前を名指せば `UnknownParticipant`。修正担当が外なら起動の前に `FixerNotAllowed`（#3941。`declared_allowed`）
- 始める前に担当すべての学習の設定のバックアップを確かめる（`unready`）。無いか期限切れならその場で確かめ、
  それでも起動できない担当があれば、提案やレビューを流す前に止める（#3907）
- cross-review の席（`review_seats`）: 名指しが無く、`allowed` の中の相手役が `SEAT_COUNT` に満たなければ、足りない席を
  claude の読むだけの起動（席の名前 `claude`・`claude-2`…）で埋める。席の名前からランタイムを引くのは `runtime_of` だけ（#3955）
- claude の読むだけの起動（`read_only_launch`）と、その出力から本文を取り出す処理（`answer_text`）は cross-refactoring と
  cross-review の席の claude が共有する（#3955）
"""

from __future__ import annotations

import json
import os
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.adapters import agents, claude, training, transcript
from ndf.contracts import facts, limits

RUNTIMES = "runtimes"
REVIEW_ARGS = "review_args"
SANDBOX = "{sandbox}"
# 修正担当: `claude -p` を打つランタイム（claude のアダプターの実行ファイルの行）
FIXER = next(r["id"] for r in facts.rows(RUNTIMES) if r["executable"] == claude.CLAUDE)
# 相手役・修正担当 1 回の監視の上限
REVIEW_TIMEOUT = "monitor.phase.review"
# 読むだけの claude に許す Tool
READ_TOOLS = "Read,Grep,Glob"
# cross-review の 1 ラウンドの席の数（10 系の #1598 の利用者の決定。#3955）
SEAT_COUNT = 2


class UnknownParticipant(ValueError):
    """名指した相手役がランタイムの表に無いか、読むだけの起動の引数を持たない。"""


class FixerNotAllowed(Exception):
    """修正担当がリポジトリのランタイムの設定（`allowed`）の外にある（前提の欠け。呼び手は precondition で止める）。"""


def _check_requested(requested: Sequence[str], able: Sequence[str]) -> None:
    """名指した相手役が読むだけの起動を持つかを確かめる。持たないものがあれば `UnknownParticipant`。"""
    bad = [r for r in requested if r not in able]
    if bad:
        raise UnknownParticipant(f"相手役 {'・'.join(bad)} はランタイムの表に無いか、読むだけの起動を持たない（{'・'.join(able)}）")


def _named(requested: Sequence[str], able: Sequence[str], fixer: str) -> tuple[str, ...]:
    """名指しを確かめ（`_check_requested`）、重なりと修正担当を除いて名指しの順に並べる。"""
    _check_requested(requested, able)
    return tuple(dict.fromkeys(r for r in requested if r != fixer))


def _able(allowed: Optional[Collection[str]]) -> list[str]:
    """ランタイムの表の行の順に、読むだけの起動の引数を持ち `allowed` の中にある名前（`assign` と `review_seats` の母集合）。"""
    return [r["id"] for r in facts.rows(RUNTIMES) if REVIEW_ARGS in r and (allowed is None or r["id"] in allowed)]


def _fixer_allowed(fixer: str, allowed: Optional[Collection[str]]) -> None:
    if allowed is not None and fixer not in allowed:
        raise FixerNotAllowed(f"修正担当 {fixer} がランタイムの設定の allowed（{'・'.join(sorted(allowed))}）に無い")


def _defaults(able: Sequence[str], fixer: str) -> tuple[str, ...]:
    seats = set(facts.ids(RUNTIMES, default_seat=True))
    return tuple(r for r in able if r in seats and r != fixer)


def assign(requested: Sequence[str] = (), fixer: str = FIXER, allowed: Optional[Collection[str]] = None) -> tuple[str, ...]:
    """相手役を決める。`requested` が空なら表の既定の席から、読むだけの起動を持つものを修正担当を除いて並べる。
    `allowed`（`.ndf/runtimes.json` の `allowed`）を渡すと、その外の相手役を選ばず、名指せば `UnknownParticipant`。
    選べる相手役が残らなければ `UnknownParticipant`。修正担当 `fixer` が `allowed` の外なら `FixerNotAllowed`
    （どの担当も起動する前に止める）。"""
    _fixer_allowed(fixer, allowed)
    able = _able(allowed)
    if requested:
        _check_requested(requested, able)
        return tuple(dict.fromkeys(r for r in requested if r != fixer))
    chosen = _defaults(able, fixer)
    if not chosen:
        names = "・".join(sorted(allowed)) if allowed is not None else "—"
        raise UnknownParticipant(f"ランタイムの設定の allowed（{names}）に、既定の席の相手役が無い")
    return chosen


class Seats(NamedTuple):
    """cross-review の起動の前に決めた席。`added` は `allowed` のために足した claude の席、`reason` は足した理由、
    `may_fill` は以後に席を埋めてよいか（名指しが 1 者なら偽）。"""

    names: tuple[str, ...]
    added: tuple[str, ...] = ()
    reason: Optional[str] = None
    may_fill: bool = True


def is_claude_seat(seat: str) -> bool:
    """席が claude のものか（`claude` か `claude-<数>`）。"""
    head, _, num = seat.rpartition("-")
    return seat == FIXER or (head == FIXER and num.isdigit())


def runtime_of(seat: str) -> str:
    """席の名前のランタイム（`claude-<数>` は claude、ほかはそのまま）。"""
    return FIXER if is_claude_seat(seat) else seat


def free_seat(taken: Collection[str]) -> str:
    """`claude`・`claude-2`・`claude-3`… のうち `taken` に無い最初の席の名前。"""
    n = 1
    while (name := FIXER if n == 1 else f"{FIXER}-{n}") in taken:
        n += 1
    return name


def review_seats(requested: Sequence[str], allowed: Optional[Collection[str]], fixer: str = FIXER) -> Seats:
    """cross-review の起動の前の席。名指しがあれば重なりと修正担当を除いた名指し（埋めない。1 者なら以後も埋めない）。
    無ければ既定の席に、`SEAT_COUNT` に足りない分の claude の席を続ける（選べる相手役が 0 でも投げない）。"""
    _fixer_allowed(fixer, allowed)
    able = _able(allowed)
    if requested:
        names = _named(requested, able, fixer)
        return Seats(names, may_fill=len(names) != 1)
    chosen = _defaults(able, fixer)
    added: list[str] = []
    while len(chosen) + len(added) < SEAT_COUNT:
        added.append(free_seat((*chosen, *added)))
    if not added:
        return Seats(chosen)
    reason = f"allowed の相手役 {len(chosen)}" + (f"（{'・'.join(chosen)}）" if chosen else "")
    return Seats((*chosen, *added), tuple(added), reason)


def fill_targets(seats: Sequence[str], unread: Collection[str]) -> tuple[str, ...]:
    """起動し直しの後に claude で埋める席（`seats` の順）。結果の無い席が 0 か全員か、claude の席を含めば空
    （全員なら precondition、claude の席を含めば incomplete_reviewers を呼び手が決める）。"""
    missing = tuple(s for s in seats if s in unread)
    if not missing or len(missing) == len(seats) or any(runtime_of(s) == FIXER for s in missing):
        return ()
    return missing


# リポジトリのランタイムの設定（表 ndf-files の行）と形
POLICY_FILE = "runtime-policy"
POLICY_SCHEMA = "schemas/ndf-settings/runtime-policy.schema.json"


class SettingUnreadable(ValueError):
    """設定のパスはあるが、通常のファイルとして読めない（リンク切れ・ディレクトリなど）。"""


def setting_present(path: Path) -> bool:
    """設定のファイル `path` があるか。パスもその親も無ければ False（設定が無い）。通常のファイル（リンク先が通常の
    ファイルのものを含む）なら True。パスか親があるのに読めない（リンク切れ・通常のファイルやディレクトリでない）なら
    `SettingUnreadable`（設定が無いときと分け、読めない設定を未設定として扱わない。PR 3944 の r6-3）。"""
    for p in (path.parent, path):
        if os.path.lexists(p) and not p.exists():
            raise SettingUnreadable(f"{p} はリンク切れで、設定を読めない")
    if path.parent.exists() and not path.parent.is_dir():
        raise SettingUnreadable(f"{path.parent} がディレクトリでなく、設定を読めない")
    if not path.exists():
        return False
    if not path.is_file():
        raise SettingUnreadable(f"{path} が通常のファイルでなく、設定を読めない")
    return True


def declared_allowed(main: Path) -> Optional[frozenset[str]]:
    """メインディレクトリ `main` の `.ndf/runtimes.json` の `allowed`。設定が無ければ None。パスがあるのに読めない
    （`setting_present`）・形が違えば `UnknownParticipant`（認可の設定を黙って無視しない）。"""
    location = facts.value("ndf-files", POLICY_FILE, "location")
    path = main / facts.value("locations", location, "path") / facts.value("ndf-files", POLICY_FILE, "path")
    try:
        present = setting_present(path)
    except SettingUnreadable as exc:
        raise UnknownParticipant(f"ランタイムの設定を読めない: {exc}") from exc
    if not present:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        contracts.require_schema(raw, POLICY_SCHEMA, contracts.ContractError, str(path))
    except (OSError, ValueError, contracts.ContractError) as exc:
        raise UnknownParticipant(f"ランタイムの設定 {path} を読めない: {exc}") from exc
    return frozenset(raw["allowed"])


def refused_runtimes(runtimes: Sequence[str]) -> dict[str, str]:
    """起動を断られたランタイムから理由への対応（渡した順。断られなかったものは含まない）。同じランタイムは 1 回だけ確かめる。
    学習の設定のバックアップが無いか期限切れなら、その場で確かめて記録する。"""
    checked = {rt: training.ensure(rt) for rt in dict.fromkeys(runtimes)}
    return {rt: why for rt, why in checked.items() if why is not None}


def unready(runtimes: Sequence[str]) -> list[str]:
    """起動できない担当の理由の並び（すべて起動できれば空）。"""
    return list(refused_runtimes(runtimes).values())


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


def _review_limit(timeout: Optional[float]) -> float:
    """監視の上限。`timeout` を省けば相手役の監視の上限（`REVIEW_TIMEOUT`）。"""
    return timeout if timeout is not None else limits.seconds(REVIEW_TIMEOUT)


def review_launch(agent: str, worktree: Path, prompt: str, stem: Path, timeout: Optional[float] = None) -> agents.Launch:
    """相手役 `agent` の読むだけの 1 回の起動。出力は `stem` の名前のファイルへ書く。"""
    row = facts.row(RUNTIMES, agent)
    args = _review_args(row, worktree)
    limit = timeout if timeout is not None else limits.seconds(REVIEW_TIMEOUT)
    return agents.Launch(agent, (row["executable"], *args, prompt), worktree, agents.AgentPaths.at(stem), limit)


def _reading_call(system: str, readable: Sequence[Path]) -> claude.ClaudeCall:
    """`READ_TOOLS` だけを許した、会話を残さない最小構成の呼び出し。"""
    return claude.ClaudeCall(system=system, permission=claude.Permission.TOOLS, tools=READ_TOOLS, readable=tuple(readable))


def _launch_call(call: claude.ClaudeCall, workdir: Path, prompt: str, stem: Path, timeout: Optional[float]) -> agents.Launch:
    return agents.Launch(FIXER, (*claude.claude_args(call), prompt), workdir, agents.AgentPaths.at(stem), _review_limit(timeout))


def read_only_launch(
    system: str, workdir: Path, prompt: str, stem: Path, timeout: Optional[float] = None, readable: Sequence[Path] = ()
) -> agents.Launch:
    """`READ_TOOLS` だけを許した、会話を残さない最小構成の `claude -p` の 1 回の起動（修正担当とは別のプロセスと会話）。
    `readable` を渡すと、読める場所を `workdir` とそれらだけに限る（#3955）。`timeout` を省けば相手役の監視の上限。"""
    return _launch_call(_reading_call(system, readable), workdir, prompt, stem, timeout)


def traced_launch(system: str, workdir: Path, prompt: str, stem: Path, readable: Sequence[Path]) -> agents.Launch:
    """`read_only_launch` と同じ起動で、出力を 1 歩ごとの行にする（呼び出した Tool を `read_files` で確かめる起動が使う）。"""
    return _launch_call(replace(_reading_call(system, readable), stream=True), workdir, prompt, stem, None)


class ClaudeReader(NamedTuple):
    """claude の席の読むだけの起動だけが使う値（`read_only_launch` の `system`・`readable`）。"""

    system: str
    readable: Sequence[Path] = ()


def reading_launch(
    seat: str, reader: ClaudeReader, workdir: Path, prompt: str, stem: Path, timeout: Optional[float] = None
) -> agents.Launch:
    """席 `seat` の読むだけの 1 回の起動。claude の席は `read_only_launch`（`reader` を使う）、ほかは `review_launch`。"""
    if runtime_of(seat) != FIXER:
        return review_launch(seat, workdir, prompt, stem, timeout)
    return read_only_launch(reader.system, workdir, prompt, stem, timeout, readable=reader.readable)


class ClaudeAnswer(NamedTuple):
    """claude の `--output-format json` の出力を 1 回読んだもの。`text` は結果を探す本文（外側の JSON の `result`。外側が JSON で
    なければ出力のまま）、`denials` は外側の JSON の `permission_denials` の要素（辞書だけ）。`verified` は、外側の JSON が
    文字列の `result` と、辞書だけの並びの `permission_denials` を持つか（偽なら、本文と拒否の一覧のどちらかが欠けうる）。"""

    text: str
    denials: tuple[dict[str, Any], ...]
    verified: bool = False


def _json_dict(text: str) -> Optional[dict[str, Any]]:
    """`text` の全体が JSON の辞書ならその辞書、ほかは None。"""
    try:
        found = json.loads(text)
    except ValueError:  # noqa: NDF401 外部コマンド: 途中で切れた出力と、JSON でない行
        return None
    return found if isinstance(found, dict) else None


def claude_answer(out: Path) -> ClaudeAnswer:
    """claude の出力を 1 回だけ読み、本文と拒否を取り出す（読めなければ `OSError`）。外側の JSON は、出力の全体か、
    1 歩ごとの行の出力（stream-json）の最後の結果の行である。"""
    raw = out.read_text(encoding="utf-8", errors="replace")
    payload = _json_dict(raw)
    if payload is None:
        last = _json_dict(raw.strip().split("\n")[-1])
        payload = last if last is not None and last.get("type") == "result" else None
    if payload is None:
        return ClaudeAnswer(raw, ())
    body = payload.get("result")
    denials = payload.get("permission_denials")
    found = tuple(d for d in denials if isinstance(d, dict)) if isinstance(denials, list) else ()
    verified = isinstance(body, str) and isinstance(denials, list) and len(found) == len(denials)
    return ClaudeAnswer(body if isinstance(body, str) else raw, found, verified)


def read_files(out: Path, workdir: Path) -> frozenset[Path]:
    """1 歩ごとの行の出力（stream-json）から、`Read` が成功したファイル。相対パスは claude の作業ディレクトリ `workdir` から
    解決する。出力を読めなければ空。"""
    try:
        lines = out.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:  # 出力の無い起動は、読んだファイルが無い
        return frozenset()
    return transcript.succeeded_reads(lines, workdir)


def without_tool_results(data: bytes) -> bytes:
    """1 歩ごとの行の出力から、Tool の結果の行（読んだファイルの中身を持つ）を除く。保存する記録に使う。"""
    return b"".join(line for line in data.splitlines(keepends=True) if not line.startswith(b'{"type":"user"'))


def answer_text(runtime: str, out: Path) -> str:
    """担当の出力から結果を探す本文。claude は `--output-format json` の外側の JSON の `result` を本文にし、ほかは出力の末尾。"""
    if runtime != FIXER:
        return agents.tail(out)
    return claude_answer(out).text


# `READ_TOOLS` の Tool ごとの、読む場所を指す引数の名前（相対パスは claude の作業ディレクトリから解決される）
READ_PATH_ARGS = {"Read": ("file_path",), "Grep": ("path",), "Glob": ("path",)}


def denied_reads(denials: Sequence[Mapping[str, Any]], required: Sequence[Path], workdir: Path) -> tuple[Path, ...]:
    """`denials`（`ClaudeAnswer.denials`）のうち、`READ_TOOLS` で `required` のファイル（か、それを含むディレクトリ）を読む
    呼び出しが拒否された `required` のファイル。引数のパスは claude の作業ディレクトリ `workdir` から解決する（PR 4461 の r1-1・r2-3）。"""
    wanted = [Path(os.path.normpath(os.path.abspath(p))) for p in required]
    hit: set[Path] = set()
    for d in denials:
        tool = str(d.get("tool_name"))
        names = READ_PATH_ARGS[tool] if tool in READ_PATH_ARGS else ()
        args = d.get("tool_input")
        if not isinstance(args, dict):
            continue
        for name in names:
            value = args.get(name)
            if isinstance(value, str) and value:
                denied = Path(os.path.normpath(Path(workdir, value)))
                hit |= {w for w in wanted if denied == w or denied in w.parents}
    return tuple(w for w in wanted if w in hit)


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
