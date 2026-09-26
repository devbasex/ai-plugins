"""gh_parts.py: PR / issue の取得と本文の節の差し替えの共通部品（#849）。

LLM が `gh` / `gh api` を手順の文どおりに組み立てていた取得と更新を、ここへ閉じる。
このファイルはエントリポイント（CLI）と、`step_result` の形で結果を返す 2 つのコマンド（`body_section`・
`review_post`）だけを持つ。部品は `lib/` の `gh_*` の 8 本にあり、公開の名前はここから再エクスポートする
（#1142 の L0）。GitHub を呼ぶのは `gh_call` だけで、テストは `gh_call.RUNNER` を差し替える。
`RUNNER` はここから再エクスポートしない（再エクスポートを差し替えても効かず、テストが黙って GitHub を呼ぶ）。

| モジュール | 持つもの |
| --- | --- |
| `gh_call` | `gh`・REST の 1 回の要求（ETag 付きの読み直し `rest_cached`）・githubkit のクライアント・リポジトリの解決 |
| `gh_quota` | 上限の見分け・枠の残り・枠の代替（`with_fallback`）・回復の待ち・待ちの間隔の伸長（`poll_until`） |
| `gh_fields` | REST の応答を GraphQL の `--json` の形へ変える対応表 |
| `gh_rest` | PR と課題の単発の読み書き（REST。上限なら GraphQL で代わる） |
| `gh_graphql` | 入れ子の読み取りと REST に無い操作（GraphQL） |
| `gh_checks` | チェックジョブの読み取りと畳み方 |
| `gh_pr_info` | `pr-info` の組み立て |
| `gh_sections` | 本文の節の文字列の操作 |

    python3 gh_parts.py pr-info 812 --with checks,threads > ctx.json
    python3 gh_parts.py unresolved-threads 812
    python3 gh_parts.py body-section get --issue 659 --heading "## 進行"
    python3 gh_parts.py body-section replace --issue 659 --heading "## 進行" --content-file s.md
    python3 gh_parts.py body-section append --issue 659 --line "振り返り: https://..."
    python3 gh_parts.py review-post --payload p.json --result r.json --pr 812 --round 1 --seat codex

| 部品 | 返すもの |
| --- | --- |
| `pr-info` | メタ・本文・差分の統計・checks（名前ごとの最新の実行へ畳んだもの）・未解決のスレッド。差分とログはファイルへ書き、パスを返す |
| `unresolved-threads` | 未解決のレビュースレッド（`thread_id` / `path` / `line`） |
| `body-section` | 本文の指定の節の取得・置換と、末尾への 1 行の追記 |
| `review-post` | レビューの payload を 1 回で投稿する。自分の PR なら REQUEST_CHANGES を COMMENT へ下げる |

結果は `step_result` の形（#846）で 1 行の JSON にして出す。**取得できなかった値は `null` にし、
0 件と区別する。**

**GraphQL が上限のときは REST へ退避する**（#271。尽きるのは GraphQL 側である）。
本文の取得と更新は初めから REST で行い、GraphQL を消費しない。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gh_call  # noqa: E402
import gh_checks  # noqa: E402
import gh_fields  # noqa: E402
import gh_graphql  # noqa: E402
import gh_pr_info  # noqa: E402
import gh_quota  # noqa: E402
import gh_rest  # noqa: E402
import gh_sections  # noqa: E402
import step_result  # noqa: E402

# ---------------- 再エクスポート（`gh_parts.<名前>` で引く呼び出し側のため。RUNNER は除く） ----------------

GhResult = gh_call.GhResult
RestResponse = gh_call.RestResponse
parse_rest_headers = gh_call.parse_rest_headers
gh = gh_call.gh
request = gh_call.request
rest = gh_call.rest
rest_cached = gh_call.rest_cached
resolve_repo = gh_call.resolve_repo
client = gh_call.client

is_rate_limited = gh_quota.is_rate_limited
Attempt = gh_quota.Attempt
rate_limits = gh_quota.rate_limits
with_fallback = gh_quota.with_fallback
wait_for_reset = gh_quota.wait_for_reset
PollInterval = gh_quota.PollInterval
poll_until = gh_quota.poll_until

to_json_shape = gh_fields.to_json_shape

view_json = gh_rest.view_json
view = gh_rest.view
pr_list = gh_rest.pr_list
issue_list = gh_rest.issue_list
pr_create = gh_rest.pr_create
issue_create = gh_rest.issue_create
pr_edit = gh_rest.pr_edit
issue_edit = gh_rest.issue_edit
comment = gh_rest.comment
pr_merge = gh_rest.pr_merge
fetch_body = gh_rest.fetch_body
update_body = gh_rest.update_body
viewer_login = gh_rest.viewer_login

UNRESOLVED_THREADS_QUERY = gh_graphql.UNRESOLVED_THREADS_QUERY
UNRESOLVED_THREADS_JQ = gh_graphql.UNRESOLVED_THREADS_JQ
unresolved_threads = gh_graphql.unresolved_threads
graphql = gh_graphql.graphql

CHECK_RUNS_PER_PAGE = gh_checks.CHECK_RUNS_PER_PAGE
CHECK_RUNS_MAX_PAGES = gh_checks.CHECK_RUNS_MAX_PAGES
FAILED_CONCLUSIONS = gh_checks.FAILED_CONCLUSIONS
fetch_check_runs = gh_checks.fetch_check_runs
fold_check_runs = gh_checks.fold_check_runs
run_result = gh_checks.run_result
check_result = gh_checks.check_result
save_failed_log = gh_checks.save_failed_log

PR_VIEW_FIELDS = gh_pr_info.PR_VIEW_FIELDS
WITH_PARTS = gh_pr_info.WITH_PARTS
fetch_pr_meta = gh_pr_info.fetch_pr_meta
default_out_dir = gh_pr_info.default_out_dir
pr_info = gh_pr_info.pr_info

SECTION_END = gh_sections.SECTION_END
get_section = gh_sections.get_section
replace_section = gh_sections.replace_section
append_line = gh_sections.append_line


# ---------------- body-section ----------------


def body_section(op: str, number: int, repo: str | None, heading: str = "",
                 content: str = "", line: str = "") -> tuple[dict[str, Any], int]:
    """本文を取り、節を取得・置換・追記する。変わらないときは書き込まない。"""
    tool = "body-section"
    slug = gh_call.resolve_repo(repo)
    if not slug:
        return step_result.result(tool, "stopped", "リポジトリを決められない"), step_result.EXIT_PRECONDITION
    body = gh_rest.fetch_body(slug, number)
    if body is None:
        return step_result.result(tool, "stopped", f"#{number} の本文を取得できない"), \
            step_result.EXIT_UNREADABLE
    if op == "get":
        text = gh_sections.get_section(body, heading)
        item = {"kind": "section", "name": heading, "result": "absent" if text is None else "found"}
        if text is not None:
            item["content"] = text
        return step_result.result(tool, "ok", f"#{number} {heading}: {item['result']}", [item]), 0
    new = gh_sections.replace_section(body, heading, content) if op == "replace" else gh_sections.append_line(body, line)
    name = heading if op == "replace" else "append"
    if new == body:
        return step_result.result(tool, "ok", f"#{number} {name}: 変更なし",
                                  [{"kind": "section", "name": name, "result": "unchanged"}]), 0
    if not gh_rest.update_body(slug, number, new):
        return step_result.result(tool, "stopped", f"#{number} の本文を更新できない"), \
            step_result.EXIT_VIOLATION
    return step_result.result(tool, "ok", f"#{number} {name}: 更新した",
                              [{"kind": "section", "name": name, "result": "updated"}]), 0






# ---------------- review-post ----------------


def review_post(payload: str, result: str, pr: int, round_no: int, seat: str,
                repo: str | None = None, head_sha: str | None = None,
                queue_dir: str | None = None) -> tuple[dict[str, Any], int]:
    """レビューを 1 回で投稿する。PR の作成者が自分なら REQUEST_CHANGES を COMMENT へ下げる。

    投稿そのものは `result_posts.post_review`（待ち行列・位置の拒否の退避）に任せる。
    """
    import post_queue
    import result_posts

    tool = "review-post"
    slug = gh_call.resolve_repo(repo)
    if not slug:
        return step_result.result(tool, "stopped", "リポジトリを決められない"), step_result.EXIT_PRECONDITION
    resp = gh_call.rest(f"repos/{slug}/pulls/{int(pr)}")
    if resp is None or not isinstance(resp.body, dict) or not resp.body.get("number"):
        return step_result.result(tool, "stopped", f"PR #{pr} を取得できない"), step_result.EXIT_UNREADABLE
    meta = gh_pr_info._meta_from_rest(slug, resp.body)
    me = gh_rest.viewer_login()
    if me is None:
        return step_result.result(tool, "stopped", "自分のアカウントを確かめられない"), \
            step_result.EXIT_PRECONDITION
    is_own = meta["author"] == me
    qdir = pathlib.Path(queue_dir) if queue_dir else gh_pr_info.default_out_dir(slug, pr) / post_queue.QUEUE_DIRNAME
    posted = result_posts.post_review(post_queue.Queue(qdir), payload, result, slug, int(pr),
                                      int(round_no), seat, head_sha or meta["head_sha"], is_own,
                                      actor=me)
    item = {"kind": "review", "name": seat, "result": "posted" if posted.review_url else
            ("queued" if posted.queued else "failed"),
            "intent": posted.intent, "posted_as": posted.posted_as,
            "review_url": posted.review_url or ""}
    metrics = {"posted_inline": posted.posted_inline, "posted_body": posted.posted_body,
               "queued": posted.queued, "findings": posted.findings, "is_own_pr": is_own}
    if posted.failed:
        return step_result.result(tool, "stopped", f"投稿できない: {posted.detail}", [item], metrics), \
            step_result.EXIT_VIOLATION
    summary = f"PR #{pr} へ {posted.posted_as} で" + ("投稿した" if posted.review_url else "積んだ（上限）")
    return step_result.result(tool, "ok", summary, [item], metrics), 0


# ---------------- CLI ----------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gh_parts.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("pr-info", help="PR のメタ・本文・差分の統計・checks・未解決のスレッド")
    sp.add_argument("pr", type=int)
    sp.add_argument("--repo")
    sp.add_argument("--with", dest="with_parts", default="",
                    help=f"カンマ区切り: {', '.join(gh_pr_info.WITH_PARTS)}")
    sp.add_argument("--out-dir", help="差分とログを書く場所")

    sp = sub.add_parser("unresolved-threads", help="未解決のレビュースレッド")
    sp.add_argument("pr", type=int)
    sp.add_argument("--repo")

    sp = sub.add_parser("body-section", help="本文の節の取得・置換・末尾への追記")
    sp.add_argument("op", choices=("get", "replace", "append"))
    target = sp.add_mutually_exclusive_group(required=True)
    target.add_argument("--issue", type=int)
    target.add_argument("--pr", type=int)
    sp.add_argument("--repo")
    sp.add_argument("--heading", default="")
    sp.add_argument("--content-file", help="置換する中身。`-` で標準入力")
    sp.add_argument("--line", default="")

    sp = sub.add_parser("review-post", help="レビューの payload を 1 回で投稿する")
    sp.add_argument("--payload", required=True)
    sp.add_argument("--result", required=True)
    sp.add_argument("--pr", type=int, required=True)
    sp.add_argument("--round", dest="round_no", type=int, required=True)
    sp.add_argument("--seat", required=True)
    sp.add_argument("--repo")
    sp.add_argument("--head-sha")
    sp.add_argument("--queue-dir")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.cmd == "pr-info":
        parts = {x.strip() for x in args.with_parts.split(",") if x.strip()}
        unknown = parts - set(gh_pr_info.WITH_PARTS)
        if unknown:
            step_result.emit(step_result.result("pr-info", "stopped",
                                                f"知らない --with: {', '.join(sorted(unknown))}"), 2)
        obj, code = gh_pr_info.pr_info(args.pr, args.repo, parts,
                            pathlib.Path(args.out_dir) if args.out_dir else None)
    elif args.cmd == "unresolved-threads":
        slug = gh_call.resolve_repo(args.repo)
        threads = gh_graphql.unresolved_threads(slug or "", args.pr)
        if threads is None:
            obj, code = step_result.result("unresolved-threads", "stopped",
                                           f"PR #{args.pr} の未解決のスレッドを取得できない"), 2
        else:
            items = [{"kind": "thread", "name": t["thread_id"], "result": "unresolved", **t}
                     for t in threads]
            obj, code = step_result.result("unresolved-threads", "ok", f"未解決 {len(threads)}",
                                           items, {"unresolved_threads": len(threads)}), 0
    elif args.cmd == "body-section":
        number = args.issue if args.issue is not None else args.pr
        if args.op in ("get", "replace") and not args.heading.strip().startswith("#"):
            step_result.emit(step_result.result("body-section", "stopped",
                                                "--heading に見出しの行（`## 進行` など）を渡す"), 2)
        if args.op == "append" and not args.line.strip():
            step_result.emit(step_result.result("body-section", "stopped", "--line が空"), 2)
        content = ""
        if args.op == "replace":
            if not args.content_file:
                step_result.emit(step_result.result("body-section", "stopped",
                                                    "--content-file が要る"), 2)
            content = (sys.stdin.read() if args.content_file == "-"
                       else pathlib.Path(args.content_file).read_text(encoding="utf-8"))
        obj, code = body_section(args.op, number, args.repo, args.heading, content, args.line)
    else:
        obj, code = review_post(args.payload, args.result, args.pr, args.round_no, args.seat,
                                args.repo, args.head_sha, args.queue_dir)
    step_result.emit(obj, code)


if __name__ == "__main__":
    main()
