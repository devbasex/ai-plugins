"""副命令 `record-fix`（#1340 の決定 8）。

ホストが `/ndf:fix` を通さずに自分で直して送ったとき、修正の記録を JSON の手書きなしで作る。確かめるのは 2 つで、
どちらかが欠ければ記録を作らずに終了コード 5 で止まる（I7）。

1. 修正のコミットが PR の head から辿れる（`compare/<commit>...<head>` が `identical` か `ahead`）
2. 申告したスレッドが未解決の一覧に無い（一覧を取れないときも止める。確かめずに作ると `start-round` の確認の抜け道になる）

戻り値ファイルは `docs/04-contracts.md` の契約の形で書き、取り込みは `merge-fix` と同じ関数（`ingest`）を通す。
`commands/` どうしは import し合わないため、取り込みの関数は `state.py` が渡す。
"""

from __future__ import annotations

import argparse
from typing import Any, Callable

import review_lib  # noqa: E402
import jsonio  # noqa: E402
from review_lib import fix_result, github, store  # noqa: E402

# 記録の出所の印。取り込みは読まない
RECORDED_BY = "record-fix"
# compare の status のうち、コミットが head から辿れるもの
_REACHABLE = ("identical", "ahead")


def _commit_on_head(repo: str, commit: str, head: str) -> bool:
    resp = github._gh_rest(f"repos/{repo}/compare/{commit}...{head}")
    body = resp.body if resp is not None else None
    return isinstance(body, dict) and body.get("status") in _REACHABLE


def _check_threads(repo: str, pr: int, claimed: list[str]) -> None:
    if not claimed:
        return
    threads = github._fetch_unresolved_threads(repo, pr)
    if threads is None:
        review_lib.die("未解決のスレッドの一覧を取れないため、Resolve を確かめられません。記録を作らずに止めます", code=5)
    open_ids = {t["id"] for t in threads}
    still_open = [i for i in claimed if i in open_ids]
    if still_open:
        review_lib.die(f"Resolve したと申告されたスレッドが未解決のまま残っています: {' '.join(still_open)}", code=5)


def build_fix_record(pr: int, commit: str, threads: list[str]) -> dict[str, Any]:
    """戻り値ファイルの中身。形は契約のまま、値だけが決まっている。"""
    return {
        "pr": pr,
        "fix_commit": commit,
        "fixed_count": len(threads),
        "resolved_threads": [{"thread_id": t} for t in threads],
        "deferred": [],
        "rejected": [],
        "ci_status": None,
        "recorded_by": RECORDED_BY,
    }


def cmd_record_fix(args: argparse.Namespace, ingest: Callable[..., None]) -> None:
    """ホストが自分で直した修正を、確かめてから修正の記録にする。"""
    pr = args.pr
    st = store._load(pr)
    rounds = st.get("rounds") or []
    if not rounds:
        review_lib.die("記録を付けるラウンドがありません。`state.py start-round` の後に打ってください")
    if rounds[-1].get("fix"):
        review_lib.die(
            f"round {rounds[-1].get('round')} には修正の記録が既にあります。取り込みの続きは `state.py merge-fix {pr}` で行います"
        )
    repo = str(st.get("repo") or "")
    current_pr = int(st.get("current_pr") or pr)
    meta = github._fetch_pr_metadata(current_pr, repo or None)
    if meta is None or not meta.head_sha:
        review_lib.die(f"PR #{current_pr} の head を取れないため、コミットを確かめられません。記録を作らずに止めます", code=5)
    commit = args.commit or meta.head_sha
    if not _commit_on_head(repo, commit, meta.head_sha):
        review_lib.die(f"コミット {commit} が PR の head にありません（送ってから打ってください）", code=5)
    threads = list(dict.fromkeys(args.resolved_thread or []))
    _check_threads(repo, current_pr, threads)

    path = store._resolve_tmp_dir(pr) / f"fix-pr{pr}-result.json"
    jsonio.write_atomic(path, build_fix_record(current_pr, commit, threads))
    review_lib.info(f"✍ 修正の記録の戻り値ファイルを書いた: {path}")
    fix = fix_result._read_fix_result(pr, path, None)
    ingest(pr, st, fix, pushed_by_host=True)
