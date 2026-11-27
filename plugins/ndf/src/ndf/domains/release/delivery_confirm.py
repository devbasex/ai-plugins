"""バージョンの無いリリース記録で、届け先ごとの反映を何で確かめたかを決める（確かめ 8。#4580）。

設計書の分けたファイル（issues/issue-4580-design-release-records.md）の確かめ 8 と、push の時点の設定の読み方・CD の結果の
照会・結果を読むコマンドの実行の境界・CD の結果の待ちのセクションのとおりに動く。

- 届け先の設定は、push の `after` の時点のものを `git cat-file --batch` の 1 回で読み、5 つ（`missing_ref`・`absent`・
  `broken`・`reserved`・`found`）に分ける。分ける規則はここだけにある
- CD の結果は、`result.workflow` なら push で起動した実行の一覧、`result.command` なら push の `after` を取り出した一時の
  作業場所で、決めた環境変数だけを渡してコマンドを 1 回実行して読む（終了コード 0 成功・75 未完了・1 失敗・ほかは未確定）
- 待つときは、1 回目の照会の直前に決めた期限までの残り時間を、照会・コマンド・実環境の確認の時間の上限にする

子のプロセスの起動・時計・待機・HTTP の送り先は `Reach` が持ち、テストは差し替える。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple, Optional

import httpx

from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError
from ndf import contracts
from ndf.contracts import facts, limits
from ndf.core import branch_strategy
from ndf.domains.release import record_decide

# 届け先の設定を読んだ結果の分類
MISSING_REF, ABSENT, BROKEN, RESERVED, FOUND = "missing_ref", "absent", "broken", "reserved", "found"
# `result.env` に書いても渡さない名前を拒む規則の名前（スキーマの `result.env` の項目の title。予約の一覧はその `not` の `pattern`）
RESERVED_ENV_RULE = "result.env の予約した名前"
# 結果を読むコマンドの終了コードと結果の形（cd-setup.md の「結果の読み方を決める」のコントラクト）
_EXIT_RUNS = {
    0: {"status": "completed", "conclusion": "success"},
    1: {"status": "completed", "conclusion": "failure"},
    75: {"status": "in_progress", "conclusion": None},
}
# 結果が確定しない（未確定）。待っても変わらないので照会し直さない
UNSETTLED = {"status": "unknown", "conclusion": None}
# CD の結果を待つ上限と照会の間隔
WAIT_LIMIT = "release-record.cd-wait"


class DeliveriesAt(NamedTuple):
    """push の `after` の時点の届け先の設定。`kind` は上の分類、`no_command` は `result.env` に予約した名前があり
    `result.command` を実行しない届け先の名前、`reason` は読めなかった理由。"""

    kind: str
    deliveries: tuple[Mapping[str, Any], ...] = ()
    no_command: frozenset[str] = frozenset()
    reason: str = ""

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "rows": list(self.deliveries), "no_command": sorted(self.no_command), "reason": self.reason}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> DeliveriesAt:
        return cls(raw["kind"], tuple(raw["rows"]), frozenset(raw["no_command"]), raw["reason"])

    def named(self, name: str) -> Mapping[str, Any]:
        """名前 `name` の届け先（無ければ空。設定の無い届け先は手動として扱う）。"""
        return next((d for d in self.deliveries if d.get("target") == name), {})


class Reach(NamedTuple):
    """届け先の結果を読む手段。`workdir` は push の `after` を取り出した一時の作業場所（`result.command` を読むときだけ）。"""

    hub: GitHub
    production: str
    workdir: Optional[Path] = None
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    run: Callable[..., Any] = subprocess.run
    transport: Optional[httpx.BaseTransport] = None


def _batch_objects(out: bytes, count: int) -> list[Optional[tuple[str, bytes]]]:
    """`git cat-file --batch` の出力から `count` 件の（オブジェクトの SHA, 中身）（無いものは None）。

    読む位置だけを進め、残りのバイト列を写さない（件数 N で写す量が N の二乗に増えないように）。"""
    got: list[Optional[tuple[str, bytes]]] = []
    pos = 0
    for _ in range(count):
        end = out.find(b"\n", pos)
        if end < 0:
            got.append(None)
            pos = len(out)
            continue
        fields = out[pos:end].split()
        pos = end + 1
        if len(fields) != 3 or fields[-1] == b"missing":
            got.append(None)
            continue
        size = int(fields[2])
        got.append((fields[0].decode("ascii", "replace"), out[pos : pos + size]))
        pos += size + 1
    return got


def _batch_entries(out: bytes, count: int) -> list[Optional[bytes]]:
    """`git cat-file --batch` の出力から `count` 件の中身（無いものは None）。"""
    return [None if o is None else o[1] for o in _batch_objects(out, count)]


def _titled(node: Any, title: str) -> Optional[Mapping[str, Any]]:
    """スキーマの中で title が `title` の項目（無ければ None）。"""
    if isinstance(node, Mapping):
        if node.get("title") == title:
            return node
        node = list(node.values())
    if isinstance(node, list):
        return next((got for child in node if (got := _titled(child, title)) is not None), None)
    return None


def _reserved_pattern() -> re.Pattern[str]:
    """予約した名前の正規表現（スキーマの `result.env` の項目の `not` の `pattern`。一覧はスキーマにだけ書く）。"""
    node = _titled(contracts.load_json(branch_strategy.SCHEMA), RESERVED_ENV_RULE)
    if node is None:
        raise branch_strategy.StrategyError(f"{branch_strategy.SCHEMA} に {RESERVED_ENV_RULE} の規則が無い")
    return re.compile(node["not"]["pattern"])


def _without_reserved(rows: list[Any]) -> tuple[list[Any], frozenset[str]]:
    """`result.env` から予約した名前を外した届け先の行と、外した届け先の名前。"""
    pattern = _reserved_pattern()
    out, names = [], set()
    for row in rows:
        result = row.get("result") if isinstance(row, dict) else None
        env = result.get("env") if isinstance(result, dict) else None
        if not isinstance(env, list) or not any(isinstance(n, str) and pattern.fullmatch(n) for n in env):
            out.append(row)
            continue
        names.add(str(row.get("target")))
        out.append({**row, "result": {**result, "env": [n for n in env if not (isinstance(n, str) and pattern.fullmatch(n))]}})
    return out, frozenset(names)


def deliveries_at(repo_git: Git, after: str) -> DeliveriesAt:
    """push の `after` の時点の届け先の設定を、`git cat-file --batch` を 1 回起動して読み、分類する。"""
    commit, raw = _batch_entries(
        repo_git.run_bytes("cat-file", "--batch", stdin=f"{after}^{{commit}}\n{after}:{branch_strategy.PROJECT}\n".encode()), 2
    )
    if commit is None:
        return DeliveriesAt(MISSING_REF, reason=f"push の after {after} が手元に無い。本番ブランチを取得してから打ち直す")
    return _classify(raw, after)


class Since(NamedTuple):
    """`declared_since` の結果。`tip` は追跡参照が指すコミットの SHA（読めなければ None）、`declared` はどれかの時点に
    届け先の設定があるか。"""

    tip: Optional[str]
    declared: bool


def declared_since(repo_git: Git, tip: str, commit: str, later: Sequence[str]) -> Optional[Since]:
    """コミット `commit` と、その後に設定のファイルを変えたコミット `later` のどれかの時点に、届け先の設定があるか
    （`git cat-file --batch` の起動 1 回。壊れた設定も、あるとみなす）。追跡参照 `tip` の SHA も同じ起動で読む。
    `commit` が手元に無ければ None。

    設定のファイルを変えても届け先の設定が無いままなら、どの時点も設定が無いので、push を読まずに済む。"""
    names = (commit, *later)
    stdin = "".join([f"{tip}^{{commit}}\n", f"{commit}^{{commit}}\n", *(f"{c}:{branch_strategy.PROJECT}\n" for c in names)]).encode()
    at_tip, head, *raws = _batch_objects(repo_git.run_bytes("cat-file", "--batch", stdin=stdin), len(names) + 2)
    if head is None:
        return None
    declared = any(_classify(None if raw is None else raw[1], at).kind != ABSENT for raw, at in zip(raws, names))
    return Since(at_tip[0] if at_tip is not None else None, declared)


def _classify(raw: Optional[bytes], after: str) -> DeliveriesAt:
    """`after` の時点の `.ndf/project.json` の中身 `raw`（無ければ None）の分類。"""
    if raw is None:
        return DeliveriesAt(ABSENT)
    try:
        project = json.loads(raw)
    except ValueError as exc:  # 利用者の入力: 壊れた設定は分類して止まる文に載せる
        return DeliveriesAt(BROKEN, reason=f"{after} の {branch_strategy.PROJECT} を JSON として読めない（{exc}）")
    section = project.get(branch_strategy.SECTION) if isinstance(project, dict) else None
    release = section.get("release") if isinstance(section, dict) else None
    rows = record_decide.declared_deliveries(release) if isinstance(release, dict) else None
    if rows is None:
        return DeliveriesAt(ABSENT)
    stripped, reserved = _without_reserved(rows)
    errors = branch_strategy.validate({**section, "release": {**release, record_decide.DELIVERIES: stripped}})
    if errors:
        found = ", ".join(f"{e['rule']} {e['pointer']}" for e in errors)
        return DeliveriesAt(BROKEN, reason=f"{after} の {branch_strategy.SECTION} の形が違う（{found}）")
    return DeliveriesAt(RESERVED if reserved else FOUND, tuple(rows), reserved)


def _limited_env(names: Sequence[str]) -> dict[str, str]:
    """渡す環境変数（環境変数の表の `result_command` が真の名前と `names` のうち、今の環境にあるもの）。"""
    return {k: os.environ[k] for k in (*facts.ids("env", result_command=True), *names) if k in os.environ}


@contextmanager
def after_tree(reach: Reach, repo_git: Git, after: str) -> Iterator[Path]:
    """push の `after` を一時の作業場所へ取り出し、終わったら外す。フックを起動せず、git へは決めた環境変数だけを渡す。"""
    path = Path(tempfile.mkdtemp(prefix="ndf-release-record-")) / "tree"
    git = ["git", "-c", "core.hooksPath=/dev/null"]
    env = _limited_env(())
    done = reach.run([*git, "worktree", "add", "--detach", str(path), after], cwd=repo_git.cwd, env=env, capture_output=True, check=False)
    if done.returncode != 0:
        raise GitError(f"push の after {after} を作業場所へ取り出せない: {done.stderr}")
    try:
        yield path
    finally:
        reach.run([*git, "worktree", "remove", "--force", str(path)], cwd=repo_git.cwd, env=env, capture_output=True, check=False)
        _drop_dir(path.parent)


def _drop_dir(path: Path) -> None:
    """一時の作業場所を置いた空のディレクトリを消す。"""
    try:
        path.rmdir()
    except OSError:  # noqa: NDF401 ファイル: 中身が残った一時のディレクトリは OS の一時ファイルの片付けに任せる
        return


def command_result(reach: Reach, command: str, env_names: Sequence[str], after: str, deadline: float) -> Mapping[str, Any]:
    """結果を読むコマンドを、`{revision}` を `after` にして作業場所 `reach.workdir` で 1 回実行した結果（`run` の形）。

    期限までの残り時間を上限にし、残りが無いか上限に当たれば未確定。出力は記録に残さない。"""
    remaining = deadline - reach.now()
    if remaining <= 0 or reach.workdir is None:
        return UNSETTLED
    try:
        done = reach.run(
            command.replace("{revision}", after),
            shell=True,  # 外部コマンド: 本番ブランチに入った push の after の設定の文字列だけを実行する（実行の境界）
            cwd=reach.workdir,
            env=_limited_env(env_names),
            timeout=remaining,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired:  # 外部コマンド: 期限に当たったコマンドは未確定として扱う
        return UNSETTLED
    return _EXIT_RUNS[done.returncode] if done.returncode in _EXIT_RUNS else UNSETTLED


def live_value(reach: Reach, delivery: Mapping[str, Any], timeout: float) -> tuple[Any, str]:
    """届け先の `observe.url` を 1 回読み、`observe.pointer` が指す値と読んだ URL。読めなければ値は `record_decide.MISSING`。"""
    spec = delivery.get("observe")
    if not isinstance(spec, Mapping) or timeout <= 0:
        return record_decide.MISSING, ""
    url = str(spec["url"])
    try:
        with httpx.Client(transport=reach.transport, timeout=timeout) as client:
            doc = client.get(url).raise_for_status().json()
    except (httpx.HTTPError, ValueError):  # ネットワーク: 読めない・JSON でない応答は確かめられなかったとして扱う
        return record_decide.MISSING, url
    return record_decide.json_pointer(doc, str(spec["pointer"])), url


def _timeout_of(delivery: Mapping[str, Any]) -> float:
    """届け先 1 つの判定の上限の秒（`result.timeout_seconds`。無ければ `WAIT_LIMIT` の秒）。"""
    result = delivery.get("result")
    got = result.get("timeout_seconds") if isinstance(result, Mapping) else None
    return float(got) if isinstance(got, (int, float)) and not isinstance(got, bool) else limits.seconds(WAIT_LIMIT)


def _cd_run(reach: Reach, delivery: Mapping[str, Any], after: str, deadline: float, runnable: bool) -> Optional[Mapping[str, Any]]:
    """CD の結果を 1 回照会する（`run` の形。実行がまだ無ければ None）。"""
    result = delivery["result"]
    if result.get("workflow"):
        remaining = deadline - reach.now()
        if remaining <= 0:
            return UNSETTLED
        try:
            runs = reach.hub.workflow_runs(str(result["workflow"]), reach.production, after, timeout=remaining)
        except GitHubError:
            if deadline - reach.now() > 0:
                raise
            return UNSETTLED  # 期限に当たった照会は未確定として扱う
        return runs[0] if runs else None
    if not runnable:
        return UNSETTLED  # `result.env` に予約した名前がある届け先はコマンドを実行しない
    names = result.get("env")
    return command_result(reach, str(result["command"]), names if isinstance(names, list) else [], after, deadline)


def _final_run(run: Optional[Mapping[str, Any]]) -> bool:
    """照会し直しても変わらない結果か（完了か未確定）。"""
    return run is not None and run.get("status") in ("completed", UNSETTLED["status"])


def confirm_delivery(
    reach: Reach, delivery: Mapping[str, Any], after: str, observed: bool, wait: bool, runnable: bool
) -> record_decide.Confirmation:
    """届け先 1 つの反映を確かめる（「CD の結果の待ち」の 1〜4）。`observed` は `--observed` でこの届け先を指定したか、
    `wait` は CD の完了を待つか（`--environment` が 1 つ）、`runnable` は `result.command` を実行してよいか。"""
    if not isinstance(delivery.get("result"), Mapping):
        return record_decide.confirm(delivery, None, record_decide.MISSING, after)
    deadline = reach.now() + _timeout_of(delivery)
    while True:
        run = _cd_run(reach, delivery, after, deadline, runnable)
        got = record_decide.confirm(delivery, run, record_decide.MISSING, after)
        if got.state != "pending":
            return got
        if observed:
            value, _url = live_value(reach, delivery, deadline - reach.now())
            got = record_decide.confirm(delivery, run, value, after)
            if got.state == "ok":
                return got
        remaining = deadline - reach.now()
        if not wait or _final_run(run) or remaining <= 0:
            return got if remaining > 0 else got._replace(reason=f"待ちの上限（{_timeout_of(delivery):g} 秒）に当たった。{got.reason}")
        reach.sleep(min(limits.value(WAIT_LIMIT, "interval"), remaining))
