"""`record --version` の確かめ 1〜6 のうち、共有済みの前処理を読む・push を読むかを決める・前処理を作る部分の振る舞い。

保存先の前処理・同じ値の信用する記録の push・push を読むかの判定の組み合わせごとに、どれを使い、git と GitHub を
どこまで読み、保存先に何を残すかを固定する。
"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.domains.release import record_decide, record_unversioned

from .test_release_record_unversioned import (
    AFTER,
    BEFORE,
    COMMIT,
    PROD,
    SUCCESS,
    WEB,
    WORKER,
    Repo,
    _args,
    _hub,
    _push_activity,
    _release,
    _write,
    _written,
)

PUSH = record_decide.Push(BEFORE, AFTER)
OTHER = record_decide.Push("e1" * 20, "e2" * 20)


def _share(repo: Repo) -> Path:
    return repo.common / "ndf" / "release-record" / f"{PROD}.json"


def _saved(repo: Repo) -> record_unversioned.Prefix:
    return record_unversioned.Prefix.from_json(json.loads(_share(repo).read_text(encoding="utf-8")))


def _put(repo: Repo, prefix: record_unversioned.Prefix) -> None:
    path = _share(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefix.as_json()), encoding="utf-8")


def _activity_reads(hub) -> list:
    return [r for r in hub.requests if r[1].endswith("/activity")]


def test_a_known_push_skips_the_decision_and_is_shared(tmp_path: Path) -> None:
    """同じ値の信用する記録に push があり保存先が無ければ、判定の git も更新の記録も読まずにその push で作り、保存先に書く。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    _write(hub, repo, _args(WEB))
    _share(repo).unlink()
    repo.calls.clear()
    hub.requests.clear()
    hub.activities = []
    got = _write(hub, repo, _args(WEB))
    assert got.kept == (WEB,) and got.record.targets == (WEB, WORKER)
    assert "log" not in [c[0] for c in repo.calls]
    assert not _activity_reads(hub)
    assert _saved(repo).push == PUSH and _saved(repo).commit == COMMIT


def test_a_share_of_another_commit_is_recomputed_and_replaced(tmp_path: Path) -> None:
    """保存先が別のコミットの前処理なら使わず、push を決めて作り直し、保存先を今のコミットのもので置き換える。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    _put(repo, record_unversioned.Prefix("f0" * 20, OTHER, None, frozenset(), frozenset()))
    got = _write(hub, repo, _args(WEB))
    assert got.recorded == (WEB,) and got.record.targets == (WEB, WORKER)
    assert _activity_reads(hub)
    assert [r.push for r in _written(hub, 11)] == [PUSH]
    assert _saved(repo).commit == COMMIT and _saved(repo).push == PUSH


def test_a_share_with_another_push_than_the_known_one_is_rebuilt_from_the_known_push(tmp_path: Path) -> None:
    """保存先が信用する記録と別の push を指せば使わず、記録の push で作り直して保存先を置き換える（更新の記録は読まない）。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    _write(hub, repo, _args(WEB))
    # 使われれば届け先の設定が無いので --target を求めて止まる
    _put(repo, record_unversioned.Prefix(COMMIT, OTHER, None, frozenset(), frozenset()))
    hub.requests.clear()
    hub.activities = []
    repo.calls.clear()
    got = _write(hub, repo, _args(WEB))
    assert got.kept == (WEB,) and got.record.targets == (WEB, WORKER)
    assert not _activity_reads(hub) and "log" not in [c[0] for c in repo.calls]
    assert _saved(repo).push == PUSH


def test_a_share_of_the_same_commit_is_used_before_deciding_without_a_known_push(tmp_path: Path) -> None:
    """信用する記録が無くても、保存先に同じコミットの前処理があれば、判定の git も更新の記録も読まずにそれを使う。"""
    hub = _hub()
    repo = Repo(tmp_path, deliveries=None)
    _put(repo, record_unversioned.Prefix(COMMIT, PUSH, None, frozenset(), frozenset()))
    got = _write(hub, repo, _args(WEB, targets=(WEB,)), _release(None))
    assert got.recorded == (WEB,)
    assert repo.calls == [] and not _activity_reads(hub)
    assert [r.push for r in _written(hub, 11)] == [PUSH]


def test_without_a_known_push_and_without_a_setting_the_record_has_no_push_and_no_share(tmp_path: Path) -> None:
    """記録の push も保存先も無く、どの時点にも届け先の設定が無ければ、push の無い前処理で書き、保存先は作らない。"""
    hub = _hub()
    repo = Repo(tmp_path, deliveries=None)
    got = _write(hub, repo, _args(WEB, targets=(WEB,)), _release(None))
    assert got.recorded == (WEB,) and got.record.targets == (WEB,)
    assert [r.push for r in _written(hub, 11)] == [None]
    assert not _share(repo).exists()


def test_a_decided_push_is_written_to_the_share_and_dropped_when_complete(tmp_path: Path) -> None:
    """判定で push を読むと決めれば保存先に書き、すべての対象がそろった呼び出しで保存先を消す。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS], [SUCCESS]]))
    repo = Repo(tmp_path)
    first = _write(hub, repo, _args(WEB))
    assert not first.complete and _saved(repo).push == PUSH
    second = _write(hub, repo, _args(WORKER))
    assert second.complete and not _share(repo).exists()
