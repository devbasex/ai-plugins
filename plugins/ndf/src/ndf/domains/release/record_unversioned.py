"""バージョンの無いリリース記録を届け先ごとに確かめて書き、対象の一覧を訂正する（`record --version`・`--correct-targets`。#4580）。

手順は設計書の分けたファイル（issues/issue-4580-design-release-records.md）の「バージョンの無いリリース記録の書き方」の
確かめ 1〜8（確かめ 0 は引数の受け口の `cli._record_args`）と書く順の手順 1〜4、「対象の一覧の訂正」のとおりである。

- 確かめ 1〜7 は、記録の PR・リポジトリ・push の記録だけで決まるので、CD を照会する確かめ 8 より先に行う。1〜6 のどれかに
  当たれば、どの PR にも書かずに `RecordError` で止まる
- 反映したコミット・push・push の時点の届け先の設定・差分のパスは、届け先ごとに並べて打った呼び出しで 1 回だけ作り、
  共通の git ディレクトリの下の保存先（`ndf-release-record/<本番の PR>.json`）で共有する（`shared_prefix`）
- 確かめ 8 と書き込みは届け先ごとにスレッドで並べ、互いを待たない。並行した書き込みの重なりは、書いた後の読み直しで
  自分のコメントを消して戻す（GitHub のコメントに条件つきの書き込みが無いため）
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, ContextManager, NamedTuple, Optional

from filelock import FileLock, Timeout

from ndf.adapters.git import Git
from ndf.adapters.github import Authored, GitHub, GitHubError, Pull
from ndf.adapters.publish import Publisher
from ndf.contracts import limits
from ndf.domains.release import delivery_confirm, names_decide, record, record_decide
from ndf.domains.release.delivery_confirm import DeliveriesAt
from ndf.domains.release.record import KEPT, WRITTEN, RecordError, Selection, Written

# 書いた後の読み直しで、前に同じ記録があったので消した PR の結果
REMOVED = "removed"
# 共通の前処理の保存先のロックを待つ上限
SHARE_LOCK = "release-record.share-lock"
_SHARE_DIR = "ndf-release-record"


class Unversioned(NamedTuple):
    """`record --version` の引数。`targets` は `--target`（渡さなければ空）、`observed` は `--observed` の届け先、
    `tip` は本番ブランチの HEAD の参照（`refs/remotes/<リモート>/<本番ブランチ>`。push を決めるときに読む）。"""

    version: str
    environments: tuple[str, ...]
    targets: tuple[str, ...]
    observed: frozenset[str]
    production_sha: Optional[str]
    tip: str


class Hands(NamedTuple):
    """手元の git と、届け先の結果を読む手段（時計・待機・子のプロセスの起動・HTTP の送り先。テストが差し替える）。"""

    repo_git: Git
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    run: Callable[..., Any] = delivery_confirm.subprocess.run
    transport: Any = None

    def reach(self, hub: GitHub, production: str, workdir: Optional[Path] = None) -> delivery_confirm.Reach:
        return delivery_confirm.Reach(hub, production, workdir, self.now, self.sleep, self.run, self.transport)


class Prefix(NamedTuple):
    """届け先によらず決まる前処理の結果。`deliveries` は push の `after` の時点の届け先の設定（worktree に設定が無ければ None）。"""

    commit: str
    push: Optional[record_decide.Push]
    deliveries: Optional[DeliveriesAt]
    pr_paths: frozenset[str]
    push_paths: frozenset[str]

    def as_json(self) -> dict[str, Any]:
        return {
            "commit": self.commit,
            "push": self.push._asdict() if self.push is not None else None,
            "at": self.deliveries.as_json() if self.deliveries is not None else None,
            "pr_paths": sorted(self.pr_paths),
            "push_paths": sorted(self.push_paths),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> Prefix:
        push, at = raw["push"], raw["at"]
        return cls(
            raw["commit"],
            record_decide.Push(push["before"], push["after"]) if push is not None else None,
            DeliveriesAt.from_json(at) if at is not None else None,
            frozenset(raw["pr_paths"]),
            frozenset(raw["push_paths"]),
        )


class Outcome(NamedTuple):
    """`record --version` の結果の `outputs` の元。`record` は届け先を持たない代表の記録、`failed`・`pending` は届け先 → 理由。"""

    record: record_decide.Record
    recorded: tuple[str, ...]
    kept: tuple[str, ...]
    failed: dict[str, str]
    pending: dict[str, str]
    complete: bool
    missing: tuple[str, ...]
    written: list[Written]


class CommentLeft(GitHubError):
    """書いた後の読み直しで自分のコメントを消せなかった。`urls` は残ったコメント（判定は変えない）。"""

    def __init__(self, cause: GitHubError, urls: Sequence[str]) -> None:
        super().__init__(f"自分のコメントを消せなかった（{cause}）: {', '.join(urls)}", cause.status, cause.retry_after, cause.quota)
        self.urls = tuple(urls)


class _Shape(NamedTuple):
    """確かめ 1〜6 を通った、書く前の形。`holder` は記録の PR、`state` は記録の PR の候補、`targets` は今回の対象。"""

    pull: Pull
    commit: str
    included: tuple[int, ...]
    holder: int
    state: record.Candidates
    targets: tuple[str, ...]


def _record_pulls(hub: GitHub, pull: Pull, production: str, formats: names_decide.NameFormats) -> tuple[int, ...]:
    """含めた PR（本番の PR と作業の PR。小さい順）。作業の PR が無ければ本番の PR だけ。"""
    return tuple(sorted({pull.number, *record._included(record._release_commits(hub, pull), pull, production, formats)}))


def _landed_value(hub: GitHub, number: int, production: str, version: str, production_sha: Optional[str]) -> tuple[Pull, str]:
    """確かめ 1・2。本番ブランチへ入った PR と反映したコミット（値の中にその SHA の先頭 7 文字以上の語があること）。"""
    pull, commit = record.landed_commit(hub, number, production, production_sha)
    if not commit:
        raise RecordError(f"#{number} の反映したコミットが読めない")
    if not record_decide.value_matches(version, commit):
        raise RecordError(f"値 {version} に、反映したコミット {commit} の先頭 {record_decide.SHA_PREFIX} 文字以上の SHA が無い")
    return pull, commit


def _diff_paths(repo_git: Git, base: str, head: str) -> frozenset[str]:
    """`base` から `head` への差分のパス（移動は前後の両方。`-z` で日本語のパスもそのまま）。"""
    return frozenset(p for p in repo_git.diff_between(base, head, "--name-only", "-z", "--no-renames").split("\0") if p)


def pull_base(hub: GitHub, repo_git: Git, number: int, commit: str) -> str:
    """本番の PR `number` の差分の起点。反映したコミット `commit` から第一親を PR のコミットの数までたどり、同じ PR に紐づく
    最後のコミットの第一親（squash とマージのコミットでは `commit` の第一親、rebase では PR のコミットの範囲の下）。
    マージの方式は見分けない。紐づく PR を照会できなければ推測せずに `RecordError`。"""
    count = hub.pull_commit_count(number)
    chain = repo_git.run("rev-list", "--first-parent", f"--max-count={count}", commit).split() if count > 1 else [commit]
    last = commit
    for sha in chain[1:]:  # PR のコミットの数までで、別の PR のコミットに当たれば止まる
        try:
            linked = hub.commit_pulls(sha)
        except GitHubError as exc:
            if exc.retry_after is not None:
                raise
            raise RecordError(
                f"コミット {sha} に紐づく PR を照会できず、#{number} の差分の起点を決められない（{exc}）。時間を置いて打ち直す"
            ) from exc
        if number not in linked:
            break
        last = sha
    return f"{last}^1"


def scope_paths(repo_git: Git, base: str, commit: str, push: record_decide.Push) -> tuple[frozenset[str], frozenset[str]]:
    """「対象を決める変更の範囲」の 2 つの差分（本番の PR の差分 `base`..`commit` と push の差分）のパス。git の起動は 2 回。"""
    return _diff_paths(repo_git, base, commit), _diff_paths(repo_git, push.before, push.after)


def find_push(hub: GitHub, repo_git: Git, production: str, commit: str, tip: str) -> record_decide.Push:
    """反映したコミットを本番ブランチへ入れた push（git の起動 1 回とブランチの更新の読み取り）。決められなければ `RecordError`。"""
    landed = frozenset(repo_git.run("rev-list", "--first-parent", f"{commit}^1..{tip}").split())
    push = record_decide.push_of(hub.branch_activity(production), landed)
    if push is None:
        raise RecordError(
            f"反映したコミット {commit} を入れた push を {production} の更新の記録から決められない"
            "（push の直後はまだ載っていないことがある）。時間を置いて打ち直す"
        )
    return push


def _prefix(hub: GitHub, hands: Hands, production: str, pull: Pull, tip: str, known: Optional[record_decide.Push]) -> Prefix:
    """worktree に届け先の設定があるリポジトリの前処理（push・push の時点の設定・差分）。`pull` は本番へ入った PR
    （`merge_sha` が反映したコミット）。止まるときは `RecordError`。"""
    commit = pull.merge_sha
    push = known if known is not None else find_push(hub, hands.repo_git, production, commit, tip)
    at = delivery_confirm.deliveries_at(hands.repo_git, push.after)
    if at.kind in (delivery_confirm.MISSING_REF, delivery_confirm.BROKEN):
        raise RecordError(f"{at.reason}。CD の結果が失敗のときと同じく、課題は人が確かめてから閉じる" if at.kind == "broken" else at.reason)
    if at.kind == delivery_confirm.ABSENT:
        return Prefix(commit, push, at, frozenset(), frozenset())
    base = pull_base(hub, hands.repo_git, pull.number, commit)
    return Prefix(commit, push, at, *scope_paths(hands.repo_git, base, commit, push))


def _share_path(repo_git: Git, number: int) -> Path:
    return repo_git.common_dir() / _SHARE_DIR / f"{int(number)}.json"


def _read_share(path: Path, commit: str) -> Optional[Prefix]:
    """保存先の前処理（反映したコミットが今と同じときだけ）。無い・壊れていれば None。"""
    try:
        got = Prefix.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):  # noqa: NDF401 ファイル: 無い・壊れた保存先は None で返して作り直す
        return None
    return got if got.commit == commit else None


def shared_prefix(repo_git: Git, number: int, commit: str, compute: Callable[[], Prefix]) -> Prefix:
    """届け先ごとに並べて打った呼び出しで共有する前処理。保存先のロックを取り、反映したコミットが同じものがあれば読み、
    無いか違えば `compute` で作って書く。"""
    path = _share_path(repo_git, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=limits.seconds(SHARE_LOCK)):
        got = _read_share(path, commit)
        if got is None:
            got = compute()
            path.write_text(json.dumps(got.as_json(), ensure_ascii=False), encoding="utf-8")
    return got


def drop_share(repo_git: Git, number: int) -> None:
    """本番の PR の保存先を消す（`complete` が真になった呼び出し）。"""
    path = _share_path(repo_git, number)
    with FileLock(str(path) + ".lock", timeout=limits.seconds(SHARE_LOCK)):
        path.unlink(missing_ok=True)


def _drop_stale(path: Path, older_than: float, now: Callable[[], float]) -> None:
    """ロックされておらず、作ってから `older_than` 秒より長く経った保存先 `path` を消す。"""
    try:
        with FileLock(str(path) + ".lock", timeout=0):
            if now() - path.stat().st_mtime > older_than:
                path.unlink(missing_ok=True)
    except (OSError, Timeout):  # noqa: NDF401 ファイル: 使っている保存先と消えた保存先は残す
        return


def sweep_shares(repo_git: Git, older_than: float, now: Callable[[], float] = time.time) -> None:
    """`complete` にならずに残った保存先のうち、ロックされておらず `older_than` 秒より長く経ったものを消す。"""
    for path in (repo_git.common_dir() / _SHARE_DIR).glob("*.json"):
        _drop_stale(path, older_than, now)


def _trusted_state(hub: GitHub, number: int, version: str, commit: str, comments: Optional[Sequence[Authored]] = None) -> record.Candidates:
    """PR の信用する書き手の記録（値 `version` かコミット `commit` を起点に集めた有効な記録と、値ごとの正の一覧）。"""
    return record._candidates(hub, number, Selection(version, None, None), commit, comments)


def _target_set(targets: Sequence[str]) -> tuple[str, ...]:
    """`--target` の集合（重複を除いて整列。記録の `targets` と比べるため順をそろえる）。"""
    return tuple(sorted(set(targets)))


def _record_targets(prefix: Prefix, args: Unversioned) -> tuple[str, ...]:
    """確かめ 3。今回の対象の届け先。"""
    at = prefix.deliveries
    if at is None or at.kind == delivery_confirm.ABSENT:
        if not args.targets:
            raise RecordError("push の時点に届け先の設定が無い。--target で今回の対象の届け先を渡して打ち直す")
        return _target_set(args.targets)
    if args.targets:
        raise RecordError("push の時点に届け先の設定があり、対象はそこから決まる。--target を渡さずに打ち直す")
    got = record_decide.targets_of(at.deliveries, prefix.pr_paths, prefix.push_paths)
    if not got:
        raise RecordError("今回の対象の届け先が無い（同じ push の中で変更が打ち消された場合を含む）")
    return got


def _check_names(targets: tuple[str, ...], args: Unversioned) -> None:
    """確かめ 4。`--environment` が対象の部分集合か、`--observed` が `--environment` に含まれるか。"""
    outside = sorted(set(args.environments) - set(targets))
    if outside:
        raise RecordError(f"--environment の {', '.join(outside)} は今回の対象の届け先（{', '.join(targets)}）に無い")
    stray = sorted(args.observed - set(args.environments))
    if stray:
        raise RecordError(f"--observed の {', '.join(stray)} は --environment に無い")


def _check_records(state: record.Candidates, targets: tuple[str, ...], version: str, commit: str, number: int) -> None:
    """確かめ 5・6。正の一覧と、値とコミットの 1 対 1。"""
    authoritative = state.authoritative.get(version)
    if authoritative is not None and authoritative != targets:
        listed = " ".join(f"--target {t}" for t in authoritative)
        raise RecordError(
            f"今回の対象の届け先（{', '.join(targets)}）が、値 {version} の正の一覧（{', '.join(authoritative)}）と違う。"
            f'一覧を直すなら ndf release-steps record --pr {number} --version "{version}" --correct-targets {listed} --reason <理由>'
        )
    conflict = record_decide.value_commit_conflict(state.valid, commit, version)
    if conflict is not None:
        value, other = conflict
        if value == version:
            raise RecordError(f"値 {version} の記録が別のコミット {other} で既にある。今回のコミット {commit} だけを指す値で打ち直す")
        raise RecordError(f"コミット {commit} の記録が別の値 {value} で既にある。その値で打ち直す")


def _shape(hub: GitHub, hands: Hands, number: int, production: str, release: Mapping[str, Any], args: Unversioned) -> tuple[_Shape, Prefix]:
    """確かめ 1〜6。通らなければ `RecordError`（何も書かない）。"""
    pull, commit = _landed_value(hub, number, production, args.version, args.production_sha)
    included = _record_pulls(hub, pull, production, names_decide.formats_of(release))
    state = _trusted_state(hub, included[0], args.version, commit)
    if record_decide.declared_deliveries(release) is None:
        prefix = Prefix(commit, None, None, frozenset(), frozenset())
    else:
        known = next((r.push for r in state.valid if r.version == args.version and r.push is not None), None)
        prefix = shared_prefix(hands.repo_git, number, commit, lambda: _prefix(hub, hands, production, pull, args.tip, known))
    targets = _record_targets(prefix, args)
    _check_names(targets, args)
    _check_records(state, targets, args.version, commit, number)
    return _Shape(pull, commit, included, included[0], state, targets), prefix


def _has(found: Sequence[record_decide.Record], made: record_decide.Record) -> bool:
    return record_decide.has_unversioned(found, made.version, made.environments, tuple(made.targets) if made.targets is not None else ())


def _own_at(comments: Sequence[Authored], own: int) -> int:
    return next((i for i, c in enumerate(comments) if c.comment_id == own), len(comments))


def _delete(hub: GitHub, own: Sequence[tuple[int, str]]) -> None:
    """自分が書いたコメント（ID, URL）を消す。消せなければ残った URL を持つ `CommentLeft`。"""
    for i, (comment_id, url) in enumerate(own):
        try:
            hub.delete_comment(comment_id)
        except GitHubError as exc:
            raise CommentLeft(exc, [u for _c, u in own[i:]]) from exc


def _duplicated(hub: GitHub, number: int, made: record_decide.Record, own: int, comments: Optional[Sequence[Authored]] = None) -> bool:
    """手順 4。PR `number` の自分のコメントより前に、同じ値・同じ `environments`・正の一覧の信用する記録があるか。"""
    rows = hub.comments(number) if comments is None else comments
    before = rows[: _own_at(rows, own)]
    return _has(_trusted_state(hub, number, made.version, str(made.commit), before).valid, made)


def write_one(hub: GitHub, made: record_decide.Record, holder: int, others: Sequence[int], present: frozenset[int]) -> list[Written]:
    """届け先 1 つの記録を、書く順の手順 1〜4 で含めた PR の各々へ書く。`present` は既に同じ記録がある PR。"""
    publisher, body = Publisher(hub), record_decide.render_record(made)
    written: dict[int, tuple[int, str]] = {}
    comments: Optional[Sequence[Authored]] = None
    if holder not in present:  # 手順 1
        got = publisher.comment(holder, body)
        written[holder] = (got.comment_id, got.url)
        comments = hub.comments(holder)  # 手順 2
        state = _trusted_state(hub, holder, made.version, str(made.commit), comments)
        lost = state.authoritative.get(made.version) != made.targets
        conflict = record_decide.value_commit_conflict(state.valid, str(made.commit), made.version)
        if lost or conflict is not None:
            _delete(hub, [written[holder]])
            raise RecordError(_lost_reason(made, state.authoritative.get(made.version), conflict))
    for n in others:  # 手順 3
        if n not in present:
            got = publisher.comment(n, body)
            written[n] = (got.comment_id, got.url)
    out = [Written(n, KEPT) for n in (holder, *others) if n not in written]
    for n, (own, url) in written.items():  # 手順 4（記録の PR は手順 2 で読んだものを使う）
        dup = _duplicated(hub, n, made, own, comments if n == holder else None)
        if dup:
            _delete(hub, [(own, url)])
        out.append(Written(n, REMOVED if dup else WRITTEN))
    return sorted(out)


def _lost_reason(made: record_decide.Record, authoritative: Optional[tuple[str, ...]], conflict: Optional[tuple[str, str]]) -> str:
    """並行した書き込みに負けて自分のコメントを消したときの止まる文。"""
    if conflict is not None:
        return f"並行して書かれた記録と値とコミットの組が合わない（既にある値 {conflict[0]}・コミット {conflict[1]}）。その値かコミットで打ち直す"
    listed = ", ".join(authoritative) if authoritative else "無し"
    return f"並行して書かれた記録が値 {made.version} の正の一覧（{listed}）になった。正の一覧で打ち直す"


def _holding_pulls(hub: GitHub, shape: _Shape, version: str, environment: str) -> frozenset[int]:
    """確かめ 7。届け先 `environment` の同じ値・正の一覧の記録が既にある PR。"""
    probe = record_decide.Record(version, shape.pull.number, shape.included, None, (environment,), shape.targets, shape.commit)
    got = {shape.holder} if _has(shape.state.valid, probe) else set()
    got |= {n for n in shape.included[1:] if _has(_trusted_state(hub, n, version, shape.commit).valid, probe)}
    return frozenset(got)


def _made(shape: _Shape, prefix: Prefix, version: str, environment: str, got: record_decide.Confirmation) -> record_decide.Record:
    evidence = {environment: got.evidence} if got.evidence is not None else None
    return record_decide.Record(
        version,
        shape.pull.number,
        shape.included,
        None,
        (environment,),
        shape.targets,
        shape.commit,
        prefix.push,
        {environment: str(got.kind)},
        evidence,
    )


def _command_tree(hands: Hands, hub: GitHub, production: str, prefix: Prefix, names: Sequence[str]) -> ContextManager[Optional[Path]]:
    """`result.command` の届け先を照会するときだけ、push の `after` を取り出した作業場所（1 回だけ作り、終われば外す）。"""
    at = prefix.deliveries
    commands = [n for n in names if at is not None and n not in at.no_command and _command_of(at.named(n))]
    if not commands or prefix.push is None:
        return nullcontext(None)
    return delivery_confirm.after_tree(hands.reach(hub, production), hands.repo_git, prefix.push.after)


def _command_of(delivery: Mapping[str, Any]) -> bool:
    result = delivery.get("result")
    return isinstance(result, Mapping) and bool(result.get("command"))


class _Run(NamedTuple):
    """確かめ 8 と書き込みに渡す、1 回の `record` で共通のもの。"""

    hub: GitHub
    reach: delivery_confirm.Reach
    shape: _Shape
    prefix: Prefix
    args: Unversioned
    # 届け先 → 同じ値・正の一覧の記録が既にある PR（確かめ 7）
    present: Mapping[str, frozenset[int]]


def _confirm_and_write(run: _Run, environment: str) -> tuple[str, record_decide.Confirmation, list[Written]]:
    """届け先 1 つの確かめ 8 と書き込み。"""
    shape, args = run.shape, run.args
    at = run.prefix.deliveries
    delivery = at.named(environment) if at is not None else {}
    runnable = at is None or environment not in at.no_command
    after = run.prefix.push.after if run.prefix.push is not None else shape.commit
    wait = len(args.environments) == 1
    got = delivery_confirm.confirm_delivery(run.reach, delivery, after, environment in args.observed, wait, runnable)
    if got.state != "ok":
        return environment, got, []
    made = _made(shape, run.prefix, args.version, environment, got)
    return environment, got, write_one(run.hub, made, shape.holder, shape.included[1:], run.present[environment])


@contextmanager
def _pool(size: int) -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=max(size, 1)) as pool:
        yield pool


def write_unversioned(hub: GitHub, hands: Hands, number: int, production: str, release: Mapping[str, Any], args: Unversioned) -> Outcome:
    """`record --version`。確かめ 1〜8 と書く順の手順 1〜4（モジュールの説明）。`release` は worktree の設定の `release`。"""
    sweep_shares(hands.repo_git, _share_age(release))
    shape, prefix = _shape(hub, hands, number, production, release, args)
    present = {e: _holding_pulls(hub, shape, args.version, e) for e in args.environments}
    kept = tuple(e for e in args.environments if present[e] >= set(shape.included))
    rest = [e for e in args.environments if e not in kept]
    if rest:
        _require_writer(hub)
    with _command_tree(hands, hub, production, prefix, rest) as workdir:
        run = _Run(hub, hands.reach(hub, production, workdir), shape, prefix, args, present)
        with _pool(len(rest)) as pool:
            results = list(pool.map(lambda e: _confirm_and_write(run, e), rest))
    recorded = tuple(e for e, got, _w in results if got.state == "ok")
    rep = record_decide.Record(args.version, number, shape.included, None, (), shape.targets, shape.commit)
    covered = [*shape.state.valid, *(rep._replace(environments=(e,)) for e in (*kept, *recorded))]
    complete, missing = record_decide.complete_of(covered, rep, shape.targets)
    if complete and record_decide.declared_deliveries(release) is not None:
        drop_share(hands.repo_git, number)
    return Outcome(
        rep,
        recorded,
        kept,
        {e: str(got.reason) for e, got, _w in results if got.state == "failed"},
        {e: str(got.reason) for e, got, _w in results if got.state == "pending"},
        complete,
        missing,
        [w for _e, _g, ws in results for w in ws] + [Written(n, KEPT) for e in kept for n in shape.included],
    )


def _require_writer(hub: GitHub) -> None:
    record._require_trusted_writer(hub)


def _share_age(release: Mapping[str, Any]) -> float:
    """保存先を残しておく秒（届け先の設定の `result.timeout_seconds` の最大、無ければ待ちの上限）。"""
    rows = record_decide.declared_deliveries(release)
    if rows is None:
        return limits.seconds(delivery_confirm.WAIT_LIMIT)
    results = [r["result"] for r in rows if isinstance(r, Mapping) and isinstance(r.get("result"), Mapping)]
    got = [float(r["timeout_seconds"]) for r in results if isinstance(r.get("timeout_seconds"), int)]
    return max(got) if got else limits.seconds(delivery_confirm.WAIT_LIMIT)


def correct_targets(
    hub: GitHub,
    hands: Hands,
    number: int,
    production: str,
    release: Mapping[str, Any],
    fix: tuple[str, tuple[str, ...], str, Optional[str]],
) -> str:
    """`record --correct-targets`。`fix` は (値, 新しい一覧, 理由, `--production-sha`)。確かめ 1・2・6 と訂正だけの確かめを通し、
    記録の PR へ訂正の記録を 1 件書いて、その URL を返す。"""
    version, targets, reason, production_sha = fix
    pull, commit = _landed_value(hub, number, production, version, production_sha)
    holder = _record_pulls(hub, pull, production, names_decide.formats_of(release))[0]
    state = _trusted_state(hub, holder, version, commit)
    same = [r for r in state.valid if r.version == version and r.targets is not None]
    if not same:
        raise RecordError(f"記録の PR #{holder} に値 {version} の信用するリリース記録が無い。先に record --version を打つ")
    _check_records(state, state.authoritative[version], version, commit, number)
    if record_decide.declared_deliveries(release) is not None:
        _refuse_declared(hands, same)
    _require_writer(hub)
    made = record_decide.Correction(version, commit, _target_set(targets), state.authoritative[version], reason)
    return Publisher(hub).comment(holder, record_decide.render_correction(made)).url


def _refuse_declared(hands: Hands, same: Sequence[record_decide.Record]) -> None:
    """worktree に届け先の設定があるリポジトリでは、記録の push の `after` の時点に設定が無いときだけ訂正を受ける。"""
    push = next((r.push for r in same if r.push is not None), None)
    at = delivery_confirm.deliveries_at(hands.repo_git, push.after) if push is not None else None
    if at is None or at.kind != delivery_confirm.ABSENT:
        raise RecordError("push の時点に届け先の設定があり、対象の一覧は設定から決まる。--correct-targets では直せない")
