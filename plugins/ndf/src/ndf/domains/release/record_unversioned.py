"""バージョンの無いリリース記録を届け先ごとに確かめて書き、対象の一覧を訂正する（`record --version`・`--correct-targets`。#4580）。

手順は設計書の分けたファイル（issues/issue-4580-design-release-records.md）の「バージョンの無いリリース記録の書き方」の
確かめ 1〜8（確かめ 0 は引数の受け口の `cli._record_args`）と書く順の手順 1〜4、「対象の一覧の訂正」のとおりである。

- 確かめ 1〜7 は、記録の PR・リポジトリ・push の記録だけで決まるので、CD を照会する確かめ 8 より先に行う。1〜6 のどれかに
  当たれば、どの PR にも書かずに `RecordError` で止まる
- push を読むと決めたときの push・push の時点の届け先の設定・差分のパスは、届け先ごとに並べて打った呼び出しで 1 回だけ
  作り、共通の git ディレクトリの下の保存先（`ndf/release-record/<本番の PR>.json`）で共有する（`shared_prefix`）。
  保存先があれば、後の呼び出しは push を読むかの判定もやり直さない。push を読まないと決めた結果は保存しない
- 確かめ 8 と書き込みは届け先ごとにスレッドで並べ、互いを待たない。並行した書き込みの重なりは、書いた後の読み直しで
  自分のコメントを消して戻す（GitHub のコメントに条件つきの書き込みが無いため）
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import string
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, ContextManager, NamedTuple, Optional

from filelock import FileLock, Timeout

from ndf import identity
from ndf.adapters.git import Git, GitError, tracking_refspec
from ndf.adapters.github import Authored, GitHub, GitHubError, Pull
from ndf.adapters.publish import Publisher
from ndf.contracts import limits
from ndf.core import branch_strategy
from ndf.domains.release import delivery_confirm, names_decide, record, record_decide
from ndf.domains.release.delivery_confirm import DeliveriesAt
from ndf.domains.release.record import KEPT, WRITTEN, RecordError, Selection, Written

# 書いた後の読み直しで、前に同じ記録があったので消した PR の結果
REMOVED = "removed"
# 共通の前処理の保存先のロックを待つ上限
SHARE_LOCK = "release-record.share-lock"
# 保存先のディレクトリ（共通の git ディレクトリの下の `<NDF の名前>/release-record`）
_SHARE_DIR = "release-record"
# 保存先の JSON の `kind`。NDF が書いた保存先だけを読み、掃除する
_SHARE_KIND = "ndf-release-record.prefix/v1"
# 書きかけの一時ファイルの接尾辞。書き終えてから保存先へ差し替えるので、中断で残るのはこの名前のファイルだけである
_SHARE_TMP = ".ndf-tmp"
# 一時ファイルを作るディレクトリ（保存先のディレクトリの下）。NDF だけが作る場所で、掃除はここだけを見る
_SHARE_TMP_DIR = "tmp"
# 一時ファイルの名前の乱数の長さ（`secrets.token_hex` のバイト数。名前には 16 進でその 2 倍の文字が入る）
_SHARE_TMP_TOKEN = 8
# 保存先の名前の形（`{number}` は本番の PR の番号）
_SHARE_NAME = "{number}.json"
# NDF の一時ファイルの名前の形（`<本番の PR>.json.<16 進 16 文字>.ndf-tmp`）。作る側（`_tmp_name`）も掃除の一致の判定
# （`_SHARE_TMP_NAME`）もこの 1 つの形から組み立て、片側だけ形が変わって掃除が一時ファイルを見落とすことを防ぐ
_SHARE_TMP_FORMAT = _SHARE_NAME + ".{token}" + _SHARE_TMP
# 形の中の値ごとの一致を見る正規表現（名前付きの組で、掃除は `number` から同じ PR のロックを取る）
_SHARE_TMP_FIELDS = {"number": r"\d+", "token": rf"[0-9a-f]{{{2 * _SHARE_TMP_TOKEN}}}"}
# 掃除はこの名前だけを消す
_SHARE_TMP_NAME = re.compile(
    "".join(
        re.escape(literal) + (f"(?P<{field}>{_SHARE_TMP_FIELDS[field]})" if field is not None else "")
        for literal, field, _, _ in string.Formatter().parse(_SHARE_TMP_FORMAT)
    )
)


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
    """手元の git と、届け先の結果を読む手段（時計・待機・子のプロセスの起動・HTTP の送り先。テストが差し替える）。
    `common` は呼び手が解決済みの共通の git ディレクトリ（None なら `share_dir` が git を起動して解決する）。"""

    repo_git: Git
    now: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    run: Callable[..., Any] = delivery_confirm.subprocess.run
    transport: Any = None
    common: Optional[Path] = None

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
            "kind": _SHARE_KIND,
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


def pull_base(hub: GitHub, repo_git: Git, number: int, commit: str) -> tuple[str, int]:
    """本番の PR `number` の差分の起点。反映したコミット `commit` から第一親を PR のコミットの数までたどり、同じ PR に紐づく
    最後のコミットの第一親（squash とマージのコミットでは `commit` の第一親、rebase では PR のコミットの範囲の下）。
    マージの方式は見分けない。PR のコミットの数の照会が 1 回と、紐づく PR の照会がたどったコミットを `github.graphql-batch`
    件ずつまとめた 1 回ずつ。バッチごとに判定し、別の PR のコミットに当たって起点が決まれば、後のバッチは照会しない
    （squash とマージのコミットでは 1 回。コミットが 1 つなら 0 回）。紐づく PR を照会できなければ推測せずに `RecordError`。
    返すのは起点と PR のコミットの数（浅い clone で起点が手元に無いときに、取得する深さを示すため）。"""
    count = hub.pull_commit_count(number)
    chain = repo_git.run("rev-list", "--first-parent", f"--max-count={count}", commit).split() if count > 1 else [commit]
    size = int(limits.value("github.graphql-batch", "count"))
    last = commit
    for start in range(1, len(chain), size):
        batch = chain[start : start + size]
        linked = _linked_pulls(hub, number, batch)
        for sha in batch:  # PR のコミットの数までで、別の PR のコミットに当たれば止まる
            got = linked.get(sha)
            if got is None:
                raise RecordError(f"コミット {sha} が GitHub に無く、#{number} の差分の起点を決められない。時間を置いて打ち直す")
            if number not in got:
                return f"{last}^1", count
            last = sha
    return f"{last}^1", count


def _linked_pulls(hub: GitHub, number: int, batch: Sequence[str]) -> Mapping[str, Optional[list[int]]]:
    """`batch` のコミットに紐づく PR（1 回の照会）。照会できなければ `RecordError`（待つ時間が分かるレート制限はそのまま）。"""
    try:
        return hub.commits_pulls(batch)
    except GitHubError as exc:
        if exc.retry_after is not None:
            raise
        raise RecordError(f"#{number} のコミットに紐づく PR を照会できず、差分の起点を決められない（{exc}）。時間を置いて打ち直す") from exc


def scope_paths(repo_git: Git, base: str, commit: str, push: record_decide.Push) -> tuple[frozenset[str], frozenset[str]]:
    """「対象を決める変更の範囲」の 2 つの差分（本番の PR の差分 `base`..`commit` と push の差分）のパス。git の起動は 2 回。"""
    return _diff_paths(repo_git, base, commit), _diff_paths(repo_git, push.before, push.after)


def _fetch_hint(production: str, tip: str) -> str:
    """手元の追跡参照が古いときに打つ取得のコマンド（`tip` は `refs/remotes/<リモート>/<本番ブランチ>`）。取得元と取得先の
    参照を明示する。`git fetch <リモート> <ブランチ>` だけでは、本番ブランチが `remote.<リモート>.fetch` の対象外
    （別のブランチだけを clone した場合など）のとき、`record` が読む追跡参照が更新されないため。"""
    parts = tip.split("/")
    remote = parts[2] if len(parts) > 3 and parts[:2] == ["refs", "remotes"] else "<リモート>"
    return f"git fetch {remote} {tracking_refspec(remote, production)}"


def _declared_at(repo_git: Git, commit: str, fetch: str) -> bool:
    """反映したコミット `commit` の時点に届け先の設定があるか（`git cat-file --batch` の起動 1 回）。worktree の設定で
    決めると、同じリリースでも設定を外す前と後で対象の出典が変わり、外す前に失敗した CD の届け先を `--target` で
    記録できてしまう。コミットが手元に無ければ、`fetch` を示して `RecordError`。"""
    at = delivery_confirm.deliveries_at(repo_git, commit)
    if at.kind == delivery_confirm.MISSING_REF:
        raise RecordError(f"反映したコミット {commit} が手元に無い。{fetch} で本番ブランチを取得してから打ち直す")
    return at.kind != delivery_confirm.ABSENT


def _deepen_hint(production: str, tip: str, depth: int) -> str:
    """浅い clone で親のコミットが手元に無いときに、本番ブランチの履歴を `depth` だけ深く取得するコマンド。"""
    return _fetch_hint(production, tip).replace("git fetch ", f"git fetch --deepen={int(depth)} ", 1)


def find_push(hub: GitHub, production: str, commit: str, tip: str, landed: frozenset[str]) -> record_decide.Push:
    """反映したコミットを本番ブランチへ入れた push（ブランチの更新の読み取り）。`landed` は `_push_needed` が返したコミットの集合。決められなければ
    `RecordError`（新しい更新の `after` が集合の外になる、追跡参照が古い場合を含む）。"""
    push = record_decide.push_of(hub.branch_activity(production), landed)
    if push is None:
        raise RecordError(
            f"反映したコミット {commit} を入れた push を {production} の更新の記録から決められない。"
            f"手元の {tip} が古ければ {_fetch_hint(production, tip)} で取得し、push の直後でまだ更新の記録に載っていなければ"
            "時間を置いて打ち直す"
        )
    return push


def _history_hint(production: str, tip: str) -> str:
    """追跡参照から反映したコミットまで第一親の履歴がつながらないときに打つ取得のコマンド。"""
    fetch = _fetch_hint(production, tip)
    return f"追跡参照が古ければ {fetch}、浅い clone で履歴が途中で切れていれば {fetch.replace('git fetch ', 'git fetch --unshallow ', 1)}"


def _first_parent_history(repo_git: Git, production: str, commit: str, tip: str) -> tuple[list[tuple[str, str]], list[str]]:
    """反映したコミットの後から `tip` までの本番ブランチの第一親のコミット（新しい順に (SHA, 第一親)）と、そのうち設定の
    ファイルを変えたコミット（git の起動 1 回）。`--sparse` で設定のファイルを変えないコミットも親を書き換えずに並べ、
    `--name-only` は設定のファイルだけを出す。反映したコミットの親は解決しないので、浅い clone の境界でも読める。
    `--no-follow` で利用者の `log.follow` の値によらず同じ出力にする。"""
    try:
        out = repo_git.run(
            "log",
            "--first-parent",
            "--sparse",
            # 利用者の `log.follow` を継がない（真だと、パスの指定で名前の変更を追い、変更の無いコミットを出さない）
            "--no-follow",
            "--diff-merges=first-parent",
            "--format=%x01%H %P",
            "--name-only",
            "--end-of-options",
            f"{commit}..{tip}",
            "--",
            branch_strategy.PROJECT,
        )
    except GitError as exc:
        raise RecordError(
            f"反映したコミット {commit} か {tip} が手元に無い（{exc}）。{_fetch_hint(production, tip)} で本番ブランチを取得してから打ち直す"
        ) from exc
    chain: list[tuple[str, str]] = []
    changed: list[str] = []
    for line in out.splitlines():
        if line.startswith("\x01"):
            sha, *parents = line[1:].split()
            chain.append((sha, parents[0] if parents else ""))
        elif line.strip() == branch_strategy.PROJECT and chain:
            changed.append(chain[-1][0])
    return chain, changed


def _push_needed(repo_git: Git, production: str, commit: str, tip: str) -> Optional[frozenset[str]]:
    """同じ値の記録に push が無いとき、push を決めてその時点の設定を読むか（git の起動 2 回。親のコミットは解決しない）。
    読むなら、push を決めるコミットの集合（反映したコミットから `tip` までの第一親）を返し、読まなければ None。

    反映したコミットの時点か、その後に第一親で設定のファイルを変えたコミットのどれかの時点に届け先の設定があれば読む。
    後者は、同じ push の後の PR が設定を足した場合に、CD の結果を確かめずに `--target` で書かせないためである。
    push は参照をまとめて動かすので、反映したコミットを含む追跡参照には、同じ push の `after` も含まれる。`after` の
    設定は、反映したコミットか、`after` までに設定のファイルを変えた最後のコミットの設定と同じなので、どの時点にも
    届け先の設定が無ければ `after` にも無い。この推論は、追跡参照から反映したコミットまで第一親の履歴がつながって
    いるときだけ成り立つので、つながらなければ（追跡参照が古い・浅い clone の履歴が途中で切れている）止まる。"""
    chain, changed = _first_parent_history(repo_git, production, commit, tip)
    since = delivery_confirm.declared_since(repo_git, tip, commit, changed)
    if since is None:
        raise RecordError(f"反映したコミット {commit} が手元に無い。{_fetch_hint(production, tip)} で本番ブランチを取得してから打ち直す")
    reached = chain[-1][1] == commit if chain else since.tip == commit
    if not reached:
        raise RecordError(
            f"手元の {tip} から反映したコミット {commit} まで本番ブランチの第一親の履歴がつながらない。"
            f"{_history_hint(production, tip)} で取得してから打ち直す"
        )
    return frozenset((commit, *(sha for sha, _p in chain))) if since.declared else None


def _prefix(
    hub: GitHub, hands: Hands, production: str, pull: Pull, tip: str, found: tuple[Optional[record_decide.Push], frozenset[str]]
) -> Prefix:
    """push を読むと決めた後の前処理（push・push の時点の設定・差分）。`pull` は本番へ入った PR（`merge_sha` が反映した
    コミット）、`found` は（同じ値の記録の push, push を決めるコミットの集合）で、記録の push が無ければ集合から決める。
    止まるときは `RecordError`。"""
    commit = pull.merge_sha
    known, landed = found
    push = known if known is not None else find_push(hub, production, commit, tip, landed)
    at = delivery_confirm.deliveries_at(hands.repo_git, push.after)
    if at.kind in (delivery_confirm.MISSING_REF, delivery_confirm.BROKEN):
        raise RecordError(f"{at.reason}。CD の結果が失敗のときと同じく、課題は人が確かめてから閉じる" if at.kind == "broken" else at.reason)
    if at.kind == delivery_confirm.ABSENT:
        return Prefix(commit, push, at, frozenset(), frozenset())
    base, count = pull_base(hub, hands.repo_git, pull.number, commit)
    try:
        return Prefix(commit, push, at, *scope_paths(hands.repo_git, base, commit, push))
    except GitError as exc:
        # 浅い clone では、本番の PR の差分の起点（反映したコミットから最大で PR のコミットの数だけ下の親）が手元に無い
        raise RecordError(
            f"#{pull.number} の差分を手元の git で読めない（{exc}）。浅い clone なら {_deepen_hint(production, tip, count + 1)} で"
            f"履歴を深く取得し、そうでなければ {_fetch_hint(production, tip)} で本番ブランチを取得してから打ち直す"
        ) from exc


def share_dir(hands: Hands) -> Path:
    """共有の保存先のディレクトリ（共通の git ディレクトリの下）。呼び手が解決済みなら git を起動せず、無ければ 1 回起動する。"""
    common = hands.common if hands.common is not None else hands.repo_git.common_dir()
    return common / identity.load().name / _SHARE_DIR


def _share_path(share: Path, number: int) -> Path:
    return share / _SHARE_NAME.format(number=int(number))


def _read_regular(path: Path) -> Optional[str]:
    """`path` が通常のファイルならその中身、そうでなければ None。

    リンクをたどらず、FIFO などで読み取りが止まらないように開いてから、通常のファイルかを確かめて読む。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:  # noqa: NDF401 ファイル: 無い・リンク・開けないファイルは None で返し、NDF が書いたものとして扱わない
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as raw:
            return raw.read().decode("utf-8")
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めないファイルは None で返し、NDF が書いたものとして扱わない
        return None
    finally:
        os.close(fd)


def _kind_of(text: str) -> Optional[Mapping[str, Any]]:
    """`text` が `kind` の `_SHARE_KIND` な JSON のオブジェクトならその中身、そうでなければ None。"""
    try:
        raw = json.loads(text)
    except ValueError:  # noqa: NDF401 ファイル: 壊れた中身は None で返し、NDF が書いたものとして扱わない
        return None
    return raw if isinstance(raw, dict) and raw.get("kind") == _SHARE_KIND else None


def _ndf_share(path: Path) -> Optional[Mapping[str, Any]]:
    """NDF が書いた保存先の中身（名前が `<本番の PR>.json` で、`kind` が `_SHARE_KIND`）。それ以外・無い・壊れていれば None。"""
    if not path.stem.isdigit():
        return None
    text = _read_regular(path)
    return None if text is None else _kind_of(text)


def _read_share(path: Path, commit: str, push: Optional[record_decide.Push] = None) -> Optional[Prefix]:
    """保存先の前処理（NDF が書き、反映したコミットが今と同じで、`push` を渡せばその push と同じときだけ）。当たらなければ None。

    同じ値の信用する記録の push（`push`）は保存先より優先する。保存先が別の push を指していれば使わずに作り直す。"""
    raw = _ndf_share(path)
    if raw is None:
        return None
    try:
        got = Prefix.from_json(raw)
    except (KeyError, TypeError, ValueError):  # noqa: NDF401 ファイル: 形の違う保存先は None で返して作り直す
        return None
    if got.commit != commit or (push is not None and got.push != push):
        return None
    return got


def shared_prefix(
    share: Path, number: int, commit: str, compute: Callable[[], Prefix], push: Optional[record_decide.Push] = None
) -> Prefix:
    """届け先ごとに並べて打った呼び出しで共有する前処理（`share` は `share_dir`）。保存先のロックを取り、反映したコミットが
    同じもの（`push` を渡せばその push のもの）があれば読み、無いか違えば `compute` で作って書く。

    NDF が書いたものでないファイルが同じ名前で置かれているか、一時ファイルを作れない（`tmp/` が塞がれている・書き込めない）
    ときは、ロックを外してから `compute` で作ってその呼び出しだけで使う。共有できない前処理で、並べて打った呼び出しを
    待たせないためである（Value 5・6）。一時ファイルは `compute` の前に作り、作れるかを前処理の前に確かめる。"""
    path = _share_path(share, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=limits.seconds(SHARE_LOCK)):
        got = _read_share(path, commit, push)
        if got is not None:
            return got
        opened = _open_tmp(share, number) if not path.exists() or _ndf_share(path) is not None else None
        if opened is not None:
            return _publish(path, opened, compute)
    # 同じ名前のファイルがあるか、一時ファイルを作れないので共有できない。その呼び出しだけで使う
    return compute()


def _tmp_name(number: int) -> str:
    """本番の PR `number` の一時ファイルの名前（`_SHARE_TMP_FORMAT` の形で、`_SHARE_TMP_NAME` に当たる）。"""
    return _SHARE_TMP_FORMAT.format(number=int(number), token=secrets.token_hex(_SHARE_TMP_TOKEN))


def _open_tmp(share: Path, number: int) -> Optional[tuple[int, Path]]:
    """本番の PR `number` の一時ファイルを、NDF だけが作るディレクトリ（`share` の下の `tmp/`。無ければ作る）に新しく作り、
    書き込み用に開く。ディレクトリがリンクや通常のファイルで塞がれている・書き込めないなど、作れなければ None。"""
    tmp_dir = share / _SHARE_TMP_DIR
    try:
        tmp_dir.mkdir(exist_ok=True)
        if not stat.S_ISDIR(tmp_dir.lstat().st_mode):
            return None
        tmp = tmp_dir / _tmp_name(number)
        return os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), tmp
    except OSError:  # noqa: NDF401 ファイル: 一時ファイルを作れなければ None で返し、共有せずに続ける
        return None


def _publish(path: Path, opened: tuple[int, Path], compute: Callable[[], Prefix]) -> Prefix:
    """`compute` で作った前処理を、開いた一時ファイル `opened` に書き終えてから `path` へ差し替え、前処理を返す。
    中断しても `path` が書きかけにならない。

    書き込みが途中で止まると一部だけ書かれた一時ファイルが残ることがあるので、掃除は中身を読まず、`tmp/` にあることと
    名前（`_SHARE_TMP_NAME`）で判断する。`compute` が失敗したときは一時ファイルを消す。"""
    fd, tmp = opened
    try:
        with os.fdopen(fd, "wb") as out:
            got = compute()
            out.write(json.dumps(got.as_json(), ensure_ascii=False).encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return got


def drop_share(share: Path, number: int) -> None:
    """本番の PR の保存先を消す（push を読み、`complete` が真になった呼び出し）。NDF が書いたものでなければ残す。"""
    path = _share_path(share, number)
    with FileLock(str(path) + ".lock", timeout=limits.seconds(SHARE_LOCK)):
        if _ndf_share(path) is not None:
            path.unlink(missing_ok=True)


def _drop_stale(path: Path, older_than: float, now: Callable[[], float]) -> None:
    """NDF が書き、ロックされておらず、作ってから `older_than` 秒より長く経った保存先 `path` を消す（ほかのファイルは残す）。"""
    if _ndf_share(path) is None:
        return
    try:
        with FileLock(str(path) + ".lock", timeout=0):
            # ロックを取る前の確かめの後に置き換わっていないかを、ロックの中で確かめ直す
            if _ndf_share(path) is not None and now() - path.stat().st_mtime > older_than:
                path.unlink(missing_ok=True)
    except (OSError, Timeout):  # noqa: NDF401 ファイル: 使っている保存先と消えた保存先は残す
        return


def _drop_stale_tmp(share: Path, path: Path, number: int, older_than: float, now: Callable[[], float]) -> None:
    """NDF だけが作るディレクトリに中断で残った本番の PR `number` の一時ファイル `path` を、作ってから `older_than` 秒より
    長く経っていれば消す。

    書く側は一時ファイルを前処理の前に作り、前処理の間も保存先のロックを持つ。前処理が `older_than` より長くかかっても
    消さないよう、同じ PR のロックをすぐ取れたときだけ、ロックの中で経った時間を確かめ直して消す。中身は読まない
    （途中まで書かれたものも消す）。リンクをたどらず、通常のファイルだけを消す。"""
    try:
        if not _stale_regular(path, older_than, now):
            return
        with FileLock(str(_share_path(share, number)) + ".lock", timeout=0):
            if _stale_regular(path, older_than, now):
                path.unlink(missing_ok=True)
    except (OSError, Timeout):  # noqa: NDF401 ファイル: 書いている途中の一時ファイルと消えた一時ファイルは残す
        return


def _stale_regular(path: Path, older_than: float, now: Callable[[], float]) -> bool:
    """`path` がリンクでない通常のファイルで、作ってから `older_than` 秒より長く経っていれば真。"""
    got = path.lstat()
    return stat.S_ISREG(got.st_mode) and now() - got.st_mtime > older_than


def sweep_shares(share: Path, older_than: float, now: Callable[[], float] = time.time) -> None:
    """`complete` にならずに残った保存先と、中断で残った書きかけの一時ファイルのうち、`older_than` 秒より長く経ったものを
    消す（保存先はロックされていないものだけ）。"""
    for path in share.glob("*.json"):
        _drop_stale(path, older_than, now)
    tmp_dir = share / _SHARE_TMP_DIR
    try:
        if not stat.S_ISDIR(tmp_dir.lstat().st_mode):
            return
        names = list(tmp_dir.iterdir())
    except OSError:  # noqa: NDF401 ファイル: 無い・列挙できない一時ファイルのディレクトリは掃除せず、記録へ進む
        return
    for path in names:
        matched = _SHARE_TMP_NAME.fullmatch(path.name)
        if matched is not None:
            _drop_stale_tmp(share, path, int(matched["number"]), older_than, now)


def _trusted_state(hub: GitHub, number: int, version: str, commit: str, comments: Optional[Sequence[Authored]] = None) -> record.Candidates:
    """PR の信用する書き手の記録（値 `version` かコミット `commit` を起点に集めた有効な記録と、値ごとの正の一覧）。"""
    return record._candidates(hub, number, Selection(version, None, None), commit, comments)


def _target_set(targets: Sequence[str]) -> tuple[str, ...]:
    """`--target` の集合（重複を除いて整列。記録の `targets` と比べるため順をそろえる）。"""
    return tuple(sorted(set(targets)))


def _known_push(state: record.Candidates, version: str) -> Optional[record_decide.Push]:
    """記録の PR の同じ値の信用する記録が持つ push（無ければ None）。"""
    return next((r.push for r in state.valid if r.version == version and r.push is not None), None)


def _record_targets(prefix: Prefix, args: Unversioned) -> tuple[str, ...]:
    """確かめ 3。今回の対象の届け先。"""
    at = prefix.deliveries
    if at is None or at.kind == delivery_confirm.ABSENT:
        if not args.targets:
            where = "反映したコミットの時点に届け先の設定（release.deliveries）が無い" if at is None else "push の時点に届け先の設定が無い"
            raise RecordError(f"{where}。--target で今回の対象の届け先を 1 つ以上渡して打ち直す")
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


class _Decided(NamedTuple):
    """確かめ 1〜6 の結果。`share` は共有の保存先のディレクトリ。"""

    shape: _Shape
    prefix: Prefix
    share: Path


def _shape(hub: GitHub, hands: Hands, number: int, production: str, release: Mapping[str, Any], args: Unversioned) -> _Decided:
    """確かめ 1〜6。通らなければ `RecordError`（何も書かない）。"""
    pull, commit = _landed_value(hub, number, production, args.version, args.production_sha)
    included = _record_pulls(hub, pull, production, names_decide.formats_of(release))
    state = _trusted_state(hub, included[0], args.version, commit)
    # 残った保存先の掃除は push を読むかによらず毎回行う（保存先の解決は CLI が済ませた値を使い、git を起動しない）
    share = share_dir(hands)
    sweep_shares(share, _share_age(release))
    # 対象の出典は worktree でなく本番へ反映した時点で決める。並べて打った別の呼び出しが push を読んで保存先に書いて
    # いれば、判定もやり直さずに使う。push を読まないと決めた結果は保存先に書かない（判定は git の起動 2 回で済む）
    known = _known_push(state, args.version)
    prefix = _read_share(_share_path(share, number), commit, known)
    if prefix is None:
        landed = _push_needed(hands.repo_git, production, commit, args.tip) if known is None else frozenset()
        if known is None and landed is None:
            prefix = Prefix(commit, None, None, frozenset(), frozenset())
        else:
            found = (known, landed or frozenset())
            prefix = shared_prefix(share, number, commit, lambda: _prefix(hub, hands, production, pull, args.tip, found), known)
    targets = _record_targets(prefix, args)
    _check_names(targets, args)
    _check_records(state, targets, args.version, commit, number)
    return _Decided(_Shape(pull, commit, included, included[0], state, targets), prefix, share)


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


class WroteOne(NamedTuple):
    """`write_one` の結果。`reread` は手順 2 で読み直した記録の PR の有効な記録（読み直さなければ空）。"""

    written: list[Written]
    reread: tuple[record_decide.Record, ...]


def write_one(hub: GitHub, made: record_decide.Record, holder: int, others: Sequence[int], present: frozenset[int]) -> WroteOne:
    """届け先 1 つの記録を、書く順の手順 1〜4 で含めた PR の各々へ書く。`present` は既に同じ記録がある PR。"""
    publisher, body = Publisher(hub), record_decide.render_record(made)
    written: dict[int, tuple[int, str]] = {}
    comments: Optional[Sequence[Authored]] = None
    reread: tuple[record_decide.Record, ...] = ()
    if holder not in present:  # 手順 1
        got = publisher.comment(holder, body)
        written[holder] = (got.comment_id, got.url)
        comments = hub.comments(holder)  # 手順 2
        state = _trusted_state(hub, holder, made.version, str(made.commit), comments)
        reread = tuple(state.valid)
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
    return WroteOne(sorted(out), reread)


def _lost_reason(made: record_decide.Record, authoritative: Optional[tuple[str, ...]], conflict: Optional[tuple[str, str]]) -> str:
    """並行した書き込みに負けて自分のコメントを消したときの止まる文。"""
    if conflict is not None:
        return f"並行して書かれた記録と値とコミットの組が合わない（既にある値 {conflict[0]}・コミット {conflict[1]}）。その値かコミットで打ち直す"
    listed = ", ".join(authoritative) if authoritative else "無し"
    return f"並行して書かれた記録が値 {made.version} の正の一覧（{listed}）になった。正の一覧で打ち直す"


def _holding_pulls(hub: GitHub, shape: _Shape, version: str, environments: Sequence[str]) -> dict[str, frozenset[int]]:
    """確かめ 7。届け先ごとの、同じ値・正の一覧の記録が既にある PR。含めた PR のコメントは届け先の数によらず PR ごとに
    1 回だけ読み（記録の PR は確かめ 5・6 で読んだもの）、届け先ごとの有無は読んだ記録の上で決める。"""
    valid = {shape.holder: shape.state.valid, **{n: _trusted_state(hub, n, version, shape.commit).valid for n in shape.included[1:]}}
    out = {}
    for environment in environments:
        probe = record_decide.Record(version, shape.pull.number, shape.included, None, (environment,), shape.targets, shape.commit)
        out[environment] = frozenset(n for n, rows in valid.items() if _has(rows, probe))
    return out


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


def _confirm_and_write(run: _Run, environment: str) -> tuple[str, record_decide.Confirmation, WroteOne]:
    """届け先 1 つの確かめ 8 と書き込み。"""
    shape, args = run.shape, run.args
    at = run.prefix.deliveries
    delivery = at.named(environment) if at is not None else {}
    runnable = at is None or environment not in at.no_command
    after = run.prefix.push.after if run.prefix.push is not None else shape.commit
    wait = len(args.environments) == 1
    got = delivery_confirm.confirm_delivery(run.reach, delivery, after, environment in args.observed, wait, runnable)
    if got.state != "ok":
        return environment, got, WroteOne([], ())
    made = _made(shape, run.prefix, args.version, environment, got)
    return environment, got, write_one(run.hub, made, shape.holder, shape.included[1:], run.present[environment])


@contextmanager
def _pool(size: int) -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(max_workers=max(size, 1)) as pool:
        yield pool


def write_unversioned(hub: GitHub, hands: Hands, number: int, production: str, release: Mapping[str, Any], args: Unversioned) -> Outcome:
    """`record --version`。確かめ 1〜8 と書く順の手順 1〜4（モジュールの説明）。`release` は worktree の設定の `release`。"""
    shape, prefix, share = _shape(hub, hands, number, production, release, args)
    present = _holding_pulls(hub, shape, args.version, args.environments)
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
    # 書く前に読んだ記録だけでは、並べて打った別の呼び出しが後に書いた届け先を見落とす。手順 2 で自分の記録を書いた後に
    # 読み直した記録の PR の記録も数える（最後に書いた呼び出しの読み直しには、ほかの呼び出しの記録がすべて載る）
    reread = [r for _e, _g, wrote in results for r in wrote.reread]
    covered = [*shape.state.valid, *reread, *(rep._replace(environments=(e,)) for e in (*kept, *recorded))]
    complete, missing = record_decide.complete_of(covered, rep, shape.targets)
    if complete and prefix.push is not None:
        drop_share(share, number)
    return Outcome(
        rep,
        recorded,
        kept,
        {e: str(got.reason) for e, got, _w in results if got.state == "failed"},
        {e: str(got.reason) for e, got, _w in results if got.state == "pending"},
        complete,
        missing,
        [w for _e, _g, wrote in results for w in wrote.written] + [Written(n, KEPT) for e in kept for n in shape.included],
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
    _refuse_declared(hands, production, commit, _known_push(state, version))
    _require_writer(hub)
    made = record_decide.Correction(version, commit, _target_set(targets), state.authoritative[version], reason)
    return Publisher(hub).comment(holder, record_decide.render_correction(made)).url


def _refuse_declared(hands: Hands, production: str, commit: str, push: Optional[record_decide.Push]) -> None:
    """対象の一覧が設定で決まる記録の訂正を断る。worktree の設定は見ず、`record` と同じく本番へ反映した時点で決める。同じ値の
    記録に push があれば、その push の `after` の時点に設定が無いときだけ受ける。push が無ければ、反映したコミット
    `commit` の時点に設定が無いときだけ受ける（設定を外した後でも、設定で決まった一覧を手で書き換えさせない）。"""
    fetch = _fetch_hint(production, "")
    if push is None:
        if _declared_at(hands.repo_git, commit, fetch):
            raise RecordError("反映したコミットの時点に届け先の設定があり、対象の一覧は設定から決まる。--correct-targets では直せない")
        return
    at = delivery_confirm.deliveries_at(hands.repo_git, push.after)
    if at.kind == delivery_confirm.MISSING_REF:
        raise RecordError(f"push の after {push.after} が手元に無い。{fetch} で本番ブランチを取得してから打ち直す")
    if at.kind != delivery_confirm.ABSENT:
        raise RecordError("push の時点に届け先の設定があり、対象の一覧は設定から決まる。--correct-targets では直せない")
