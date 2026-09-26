"""gh_parts.py: PR / issue の取得と本文の節の差し替えの共通部品（#849）。

LLM が `gh` / `gh api` を手順の文どおりに組み立てていた取得と更新を、ここへ閉じる。
GitHub を呼ぶのは `RUNNER` 1 か所だけである。課題追跡の共通層（#480）が入ったら、
`RUNNER` とリポジトリ名の解決をそちらへ差し替える。

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
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, NamedTuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import step_result  # noqa: E402

# ---------------- gh の呼び出し ----------------


class GhResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _subprocess_gh(args: list[str], stdin: str | None = None, cwd: str | None = None) -> GhResult:
    try:
        p = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, cwd=cwd)
    except OSError as exc:
        return GhResult(127, "", f"gh を実行できない: {exc}")
    return GhResult(p.returncode, p.stdout, p.stderr)


# テストはここを見本の応答へ差し替える。引数は `gh` の後ろの argv と標準入力（`cwd` を渡すときだけ `cwd=`）。
RUNNER: Callable[..., GhResult] = _subprocess_gh


def gh(args: list[str], stdin: str | None = None, cwd: str | None = None) -> GhResult:
    if cwd is None:
        return RUNNER(list(args), stdin)
    return RUNNER(list(args), stdin, cwd=str(cwd))


_RATE_LIMIT_MARKERS = ("rate limit", "RATE_LIMIT", "unknown owner type")


def is_rate_limited(text: str) -> bool:
    """上限の応答か。`projects-common.sh` の `pj_is_rate_limited` と同じ語で見分ける。

    `unknown owner type` は、GraphQL が上限のときに `gh` が返す誤った文言である（実測）。
    """
    text = str(text or "")
    return any(m in text for m in _RATE_LIMIT_MARKERS) or "rate limit" in text.lower()


class RestResponse(NamedTuple):
    """`gh api -i` の 1 回の応答。残量は通常の要求の応答ヘッダからしか読めない。"""

    headers: dict[str, str]
    body: Any
    rate_remaining: int | None
    rate_reset: str | None


def parse_rest_headers(text: str) -> tuple[dict[str, str], str]:
    """`gh api -i` の出力を、ヘッダの辞書と本文へ分ける。

    状態行だけが `\\r` を持たず、以降のヘッダは `\\r\\n` で終わる（実測）。行末の
    違いで分けられなくなるため、空行そのものを区切りとして読む。
    """
    headers: dict[str, str] = {}
    lines = text.splitlines(keepends=True)
    body_at = len(lines)
    for i, line in enumerate(lines):
        if not line.strip():
            body_at = i + 1
            break
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return headers, "".join(lines[body_at:])


def rest(path: str, method: str = "GET", payload: Any = None) -> RestResponse | None:
    """REST の 1 回の要求。失敗は `None`（例外を投げず、呼び出し側が「確かめられなかった」と読む）。"""
    args = ["api", "-i", path]
    stdin = None
    if method != "GET":
        args[1:1] = ["-X", method]
    if payload is not None:
        args += ["--input", "-"]
        stdin = json.dumps(payload, ensure_ascii=False)
    r = gh(args, stdin)
    if r.returncode != 0:
        return None
    headers, raw = parse_rest_headers(r.stdout)
    try:
        body = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        return None
    remaining = headers.get("x-ratelimit-remaining")
    try:
        rate_remaining = int(remaining) if remaining is not None else None
    except ValueError:
        rate_remaining = None
    return RestResponse(headers, body, rate_remaining, headers.get("x-ratelimit-reset"))


_REPO_URL = re.compile(r"(?:github\.com[:/])(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")


def resolve_repo(repo: str | None = None) -> str | None:
    """`owner/repo` を決める。渡された値 → `origin` の URL → `gh repo view` の順。"""
    if repo:
        return repo
    try:
        p = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
        m = _REPO_URL.search(p.stdout.strip()) if p.returncode == 0 else None
    except OSError:
        m = None
    if m:
        return f"{m.group('owner')}/{m.group('name')}"
    r = gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return r.stdout.strip() or None if r.returncode == 0 else None


def _output_via_runner(cmd: list[str]) -> str | None:
    """`gh ...` の argv を受け、標準出力を返す。失敗は `None`。"""
    r = gh(cmd[1:] if cmd and cmd[0] == "gh" else cmd)
    return r.stdout if r.returncode == 0 else None


# ---------------- view-json（gh pr view / gh issue view の --json） ----------------


def _rest_state(d: dict[str, Any]) -> str:
    """REST の state を GraphQL の形（OPEN / CLOSED / MERGED）へ揃える。MERGED は merged_at で見分ける。"""
    if d.get("merged_at"):
        return "MERGED"
    return str(d.get("state") or "").upper()


def _rest_merge_commit(d: dict[str, Any]) -> dict[str, str] | None:
    """GraphQL の mergeCommit は `{"oid": ...}` か null。REST の merge_commit_sha は未マージでも試しの
    マージを指すので、merged_at があるときだけ載せる。"""
    sha = d.get("merge_commit_sha")
    return {"oid": sha} if d.get("merged_at") and sha else None


# REST の応答から GraphQL の `--json` のフィールドを作る対応表。release-steps.py が読むフィールドだけ
_VIEW_FIELDS: dict[str, dict[str, Callable[[dict[str, Any]], Any]]] = {
    "pr": {
        "number": lambda d: d.get("number"),
        "title": lambda d: d.get("title") or "",
        "body": lambda d: d.get("body") or "",
        "state": _rest_state,
        "url": lambda d: d.get("html_url") or "",
        "mergeCommit": _rest_merge_commit,
    },
    "issue": {
        "number": lambda d: d.get("number"),
        "title": lambda d: d.get("title") or "",
        "body": lambda d: d.get("body") or "",
        "state": lambda d: str(d.get("state") or "").upper(),
        "url": lambda d: d.get("html_url") or "",
    },
}
_VIEW_REST_PATH = {"pr": "pulls", "issue": "issues"}


def view_json(kind: str, number: int, fields: str, repo: str | None = None,
              cwd: str | None = None) -> GhResult:
    """`gh <pr|issue> view <n> --json <fields>` を呼ぶ。GraphQL が上限のときだけ REST で読み直し、
    GraphQL の `--json` と同じ形の JSON を stdout に入れて返す。

    上限以外の失敗と、対応表（`_VIEW_FIELDS`）に無いフィールドを頼まれたときは、元の結果をそのまま返す。
    `repo` を省くと `gh` が `cwd` のリポジトリを使う（REST は `{owner}/{repo}` の置き換え）。
    """
    args = [kind, "view", str(int(number))] + (["--repo", repo] if repo else []) + ["--json", fields]
    r = gh(args, cwd=cwd)
    if r.returncode == 0 or not is_rate_limited(r.stderr + r.stdout):
        return r
    table = _VIEW_FIELDS[kind]
    names = [f.strip() for f in fields.split(",") if f.strip()]
    if not names or any(f not in table for f in names):
        return r
    path = f"repos/{repo or '{owner}/{repo}'}/{_VIEW_REST_PATH[kind]}/{int(number)}"
    rr = gh(["api", path], cwd=cwd)
    try:
        d = json.loads(rr.stdout) if rr.returncode == 0 else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        why = rr.stderr.strip() or "REST の応答を読めない"
        return GhResult(rr.returncode or 1, "", f"{r.stderr.strip()}\nREST でも読めない: {why}")
    return GhResult(0, json.dumps({f: table[f](d) for f in names}, ensure_ascii=False), "")


# ---------------- unresolved-threads ----------------

UNRESOLVED_THREADS_QUERY = """
query($owner: String!, $name: String!, $pr: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved path line }
      }
    }
  }
}
"""

UNRESOLVED_THREADS_JQ = (
    ".data.repository.pullRequest.reviewThreads.nodes[]"
    " | select(.isResolved == false)"
    ' | [.id, (.path // ""), (.line // "" | tostring)] | @tsv'
)


def unresolved_threads(repo: str, pr: int,
                       output: Callable[[list[str]], str | None] | None = None
                       ) -> list[dict[str, str]] | None:
    """未解決のレビュースレッドを `thread_id` つきで返す。0 件は空の一覧、取得できなければ `None`。

    `output` は `gh` の argv を受けて標準出力（失敗は `None`）を返す関数。省くと `RUNNER` を使う。
    Resolve の状態は REST から読めないため、上限のときは退避せず `None` を返す。
    """
    owner, sep, name = str(repo or "").partition("/")
    if not (owner and sep and name):
        return None
    out = (output or _output_via_runner)([
        "gh", "api", "graphql", "--paginate",
        "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"pr={int(pr)}",
        "-f", f"query={UNRESOLVED_THREADS_QUERY}",
        "--jq", UNRESOLVED_THREADS_JQ,
    ])
    if out is None:
        return None
    threads: list[dict[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        threads.append({
            "thread_id": cols[0],
            "path": cols[1] if len(cols) > 1 else "",
            "line": cols[2] if len(cols) > 2 else "",
        })
    return threads


# ---------------- checks ----------------

CHECK_RUNS_PER_PAGE = 100
CHECK_RUNS_MAX_PAGES = 10
FAILED_CONCLUSIONS = ("failure", "timed_out", "action_required", "startup_failure")


def fetch_check_runs(repo: str, sha: str,
                     rest_get: Callable[[str], Any] | None = None,
                     per_page: int = CHECK_RUNS_PER_PAGE,
                     max_pages: int = CHECK_RUNS_MAX_PAGES) -> list[dict[str, Any]] | None:
    """head の commit のチェックジョブを `total_count` に届くまで読む（畳む前の生の一覧）。

    **「照会できなかった」と「すべて成功」を区別する。** 失敗・`total_count` 0 はどちらも `None`。
    `rest_get` は `.body` を持つ応答（失敗は `None`）を返す関数。省くと `rest` を使う。
    """
    if not repo or not sha:
        return None
    get = rest_get or rest
    base = f"repos/{repo}/commits/{sha}/check-runs?per_page={per_page}"
    runs: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, max_pages + 1):
        resp = get(f"{base}&page={page}")
        if resp is None or not isinstance(resp.body, dict):
            return None
        if total is None:
            try:
                total = int(resp.body.get("total_count") or 0)
            except (TypeError, ValueError):
                return None
            if total <= 0:
                return None
        chunk = resp.body.get("check_runs")
        if not isinstance(chunk, list) or not chunk:
            break
        runs.extend(r for r in chunk if isinstance(r, dict))
        if len(runs) >= total:
            break
    return runs or None


def _run_order(indexed: tuple[int, dict[str, Any]]) -> tuple[str, int, int]:
    i, run = indexed
    try:
        run_id = int(run.get("id") or 0)
    except (TypeError, ValueError):
        run_id = 0
    # ISO 8601 の UTC（`Z` 付き）は文字列の比較で時刻の順になる。
    stamp = max(str(run.get("completed_at") or ""), str(run.get("started_at") or ""))
    return (stamp, run_id, i)


def fold_check_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名のチェックジョブを、名前ごとの最新の実行 1 件へ畳む（#632）。

    再実行で `failure` → `success` になったチェックは `success` として返す。新しさは
    `completed_at` と `started_at` の新しい方 → 実行の番号（`id` は増える一方）→
    一覧の中の順で決める。
    並びは最初に現れた名前の順を保つ。
    """
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    order: list[str] = []
    for indexed in enumerate(runs):
        name = str(indexed[1].get("name") or "")
        if name not in latest:
            order.append(name)
            latest[name] = indexed
        elif _run_order(indexed) >= _run_order(latest[name]):
            latest[name] = indexed
    return [latest[n][1] for n in order]


def run_result(run: dict[str, Any]) -> str:
    """1 件の結果を 1 語にする。未完了は `pending`。"""
    if str(run.get("status") or "completed").lower() != "completed":
        return "pending"
    return str(run.get("conclusion") or "").lower() or "unknown"


def check_result(runs: list[dict[str, Any]] | None, name: str) -> str | None:
    """名前の一致したチェックの、最新の実行の結果。照会できない・一致なしは `None`。"""
    if not runs or not name:
        return None
    for run in fold_check_runs(runs):
        if str(run.get("name") or "") == name:
            return run_result(run)
    return None


_JOB_ID = re.compile(r"/job/(\d+)")


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("_") or "check"


def save_failed_log(repo: str, run: dict[str, Any], out_dir: pathlib.Path) -> tuple[str | None, str]:
    """失敗したチェックのログ（`gh run view --log-failed`）をファイルへ書き、パスを返す。"""
    m = _JOB_ID.search(str(run.get("details_url") or run.get("html_url") or ""))
    if not m:
        return None, "GitHub Actions のジョブではない"
    r = gh(["run", "view", "--repo", repo, "--job", m.group(1), "--log-failed"])
    if r.returncode != 0:
        return None, f"ログを取得できない: {r.stderr.strip()[:200]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"check-{_safe(str(run.get('name') or ''))}-{m.group(1)}.log"
    path.write_text(r.stdout, encoding="utf-8")
    return str(path), ""


# ---------------- pr-info ----------------

PR_VIEW_FIELDS = ("number,title,body,state,isDraft,author,headRefName,headRefOid,"
                  "baseRefName,url,additions,deletions,changedFiles,labels,isCrossRepository")
WITH_PARTS = ("checks", "threads", "diff", "logs")


def _meta_from_graphql(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": d.get("number"), "title": d.get("title") or "", "body": d.get("body") or "",
        "state": str(d.get("state") or "").lower(), "draft": bool(d.get("isDraft")),
        "author": str((d.get("author") or {}).get("login") or ""),
        "head_branch": d.get("headRefName") or "", "head_sha": d.get("headRefOid") or "",
        "base_branch": d.get("baseRefName") or "", "url": d.get("url") or "",
        "is_fork": bool(d.get("isCrossRepository")),
        "labels": [str(x.get("name")) for x in d.get("labels") or [] if isinstance(x, dict)],
        "additions": d.get("additions"), "deletions": d.get("deletions"),
        "changed_files": d.get("changedFiles"),
    }


def _meta_from_rest(repo: str, d: dict[str, Any]) -> dict[str, Any]:
    head = d.get("head") or {}
    head_repo = (head.get("repo") or {}).get("full_name") or ""
    state = "merged" if d.get("merged") or d.get("merged_at") else str(d.get("state") or "")
    return {
        "number": d.get("number"), "title": d.get("title") or "", "body": d.get("body") or "",
        "state": state.lower(), "draft": bool(d.get("draft")),
        "author": str((d.get("user") or {}).get("login") or ""),
        "head_branch": head.get("ref") or "", "head_sha": head.get("sha") or "",
        "base_branch": (d.get("base") or {}).get("ref") or "", "url": d.get("html_url") or "",
        "is_fork": bool(head_repo) and head_repo != repo,
        "labels": [str(x.get("name")) for x in d.get("labels") or [] if isinstance(x, dict)],
        "additions": d.get("additions"), "deletions": d.get("deletions"),
        "changed_files": d.get("changed_files"),
    }


def fetch_pr_meta(repo: str, pr: int) -> tuple[dict[str, Any] | None, str, str]:
    """PR のメタと本文を返す。`(meta, 取得元, 失敗の理由)`。GraphQL が上限なら REST へ退避する。"""
    r = gh(["pr", "view", str(int(pr)), "--repo", repo, "--json", PR_VIEW_FIELDS])
    if r.returncode == 0:
        try:
            return _meta_from_graphql(json.loads(r.stdout)), "graphql", ""
        except (ValueError, TypeError, AttributeError):
            return None, "graphql", "gh pr view の出力を読めない"
    if not is_rate_limited(r.stderr + r.stdout):
        return None, "graphql", f"gh pr view が失敗: {r.stderr.strip()[:200]}"
    resp = rest(f"repos/{repo}/pulls/{int(pr)}")
    if resp is None or not isinstance(resp.body, dict) or not resp.body.get("number"):
        return None, "rest", "GraphQL が上限で、REST でも取得できない"
    return _meta_from_rest(repo, resp.body), "rest", ""


def default_out_dir(repo: str, pr: int) -> pathlib.Path:
    return pathlib.Path(tempfile.gettempdir()) / "ndf" / f"pr-info-{repo.replace('/', '--')}-{int(pr)}"


def _save_diff(repo: str, pr: int, out_dir: pathlib.Path) -> tuple[str | None, str]:
    r = gh(["api", "-H", "Accept: application/vnd.github.v3.diff", f"repos/{repo}/pulls/{int(pr)}"])
    if r.returncode != 0:
        return None, f"差分を取得できない: {r.stderr.strip()[:200]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "pr.diff"
    path.write_text(r.stdout, encoding="utf-8")
    return str(path), ""


def pr_info(pr: int, repo: str | None = None, with_parts: set[str] | None = None,
            out_dir: pathlib.Path | None = None) -> tuple[dict[str, Any], int]:
    """PR の取得を 1 つの結果にまとめる。`(結果, 終了コード)`。

    items の `kind` は `pr` / `check` / `thread` / `diff`。checks は名前ごとの最新の実行へ
    畳んでから返す。失敗したチェックのログは `--with logs` のときだけファイルへ書く。
    """
    parts = set(with_parts or ())
    slug = resolve_repo(repo)
    if not slug:
        return step_result.result("pr-info", "stopped", "リポジトリを決められない"), \
            step_result.EXIT_PRECONDITION
    meta, source, reason = fetch_pr_meta(slug, pr)
    if meta is None:
        return step_result.result("pr-info", "stopped", f"PR #{pr} を取得できない: {reason}",
                                  metrics={"source": source}), step_result.EXIT_UNREADABLE
    out = out_dir or default_out_dir(slug, pr)
    items: list[dict[str, Any]] = [{"kind": "pr", "name": f"#{pr}", "result": meta["state"],
                                    "repo": slug, **meta}]
    metrics: dict[str, Any] = {"source": source, "additions": meta["additions"],
                               "deletions": meta["deletions"],
                               "changed_files": meta["changed_files"]}
    unavailable: list[str] = []
    notes: list[str] = []

    if "diff" in parts:
        path, why = _save_diff(slug, pr, out)
        items.append({"kind": "diff", "name": "diff", "result": "saved" if path else "unavailable",
                      "path": path, **({"reason": why} if why else {})})
        if not path:
            unavailable.append("diff")

    if "checks" in parts or "logs" in parts:
        raw = fetch_check_runs(slug, meta["head_sha"])
        if raw is None:
            unavailable.append("checks")
            metrics.update(failed_checks=None, pending_checks=None)
        else:
            folded = fold_check_runs(raw)
            failed = pending = 0
            for run in folded:
                res = run_result(run)
                item = {"kind": "check", "name": str(run.get("name") or ""), "result": res,
                        "url": run.get("details_url") or run.get("html_url") or ""}
                if res in FAILED_CONCLUSIONS:
                    failed += 1
                    if "logs" in parts:
                        path, why = save_failed_log(slug, run, out)
                        item.update({"log_path": path} if path else {"log_error": why})
                elif res == "pending":
                    pending += 1
                items.append(item)
            metrics.update(failed_checks=failed, pending_checks=pending,
                           superseded_checks=len(raw) - len(folded))
            notes.append(f"checks 失敗 {failed} / 保留 {pending}")

    if "threads" in parts:
        threads = unresolved_threads(slug, pr)
        if threads is None:
            unavailable.append("threads")
            metrics["unresolved_threads"] = None
        else:
            for t in threads:
                line: Any = t["line"]
                try:
                    line = int(line)
                except (TypeError, ValueError):
                    line = None
                items.append({"kind": "thread", "name": t["thread_id"], "result": "unresolved",
                              "thread_id": t["thread_id"], "path": t["path"], "line": line})
            metrics["unresolved_threads"] = len(threads)
            notes.append(f"未解決 {len(threads)}")

    if unavailable:
        metrics["unavailable"] = unavailable
        notes.append("取得できない: " + ", ".join(unavailable))
    summary = " / ".join([f"PR #{pr} {meta['state']}（{source}）", *notes])
    return step_result.result("pr-info", "ok", summary, items, metrics), step_result.EXIT_OK


# ---------------- body-section ----------------
#
# 節は「見出しの行から、同じか上の段の次の見出しまで」である。**最後の節の後ろへ足した行を
# 節に含めないため、書いた節の終わりへ目印（`SECTION_END`）を置く。** 目印があれば節は目印で終わり、
# 目印より後ろは節の外として残す（#659: 最後の節を差し替えると、末尾へ足した 1 行が消えた）。

SECTION_END = "<!-- ndf:section-end -->"
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})[ \t]+\S")


class _Span(NamedTuple):
    start: int      # 見出しの行
    content: int    # 見出しの次の行
    end: int        # 節の終わり（目印の行を含まない）
    after: int      # 節の外が始まる行（目印があれば目印の次）
    last: bool      # 後ろに見出しが無い


def _lines(body: str) -> list[str]:
    return body.replace("\r\n", "\n").split("\n")


def _heading_lines(lines: list[str]) -> list[tuple[int, int]]:
    """コードブロックの外にある見出しの `(行, 段)`。"""
    out: list[tuple[int, int]] = []
    fence = None
    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
            continue
        if fence is None:
            h = _HEADING.match(line)
            if h:
                out.append((i, len(h.group(1))))
    return out


def _find(lines: list[str], heading: str) -> _Span | None:
    heading = heading.strip()
    level = len(heading) - len(heading.lstrip("#"))
    heads = _heading_lines(lines)
    for n, (i, _) in enumerate(heads):
        if lines[i].rstrip() != heading:
            continue
        nxt = next((j for j, lv in heads[n + 1:] if lv <= level), None)
        end = len(lines) if nxt is None else nxt
        marker = next((k for k in range(i + 1, end) if lines[k].strip() == SECTION_END), None)
        if marker is not None:
            return _Span(i, i + 1, marker, marker + 1, nxt is None)
        return _Span(i, i + 1, end, end, nxt is None)
    return None


def get_section(body: str, heading: str) -> str | None:
    """節の中身（見出しと目印を除く）。節が無ければ `None`。"""
    lines = _lines(body)
    span = _find(lines, heading)
    if span is None:
        return None
    return "\n".join(lines[span.content:span.end]).strip("\n")


def _section_block(heading: str, content: str) -> list[str]:
    text = content.replace("\r\n", "\n").strip("\n")
    first, _, rest = text.partition("\n")
    if first.rstrip() == heading.strip():
        text = rest.strip("\n")
    return [heading.strip(), "", *(text.split("\n") if text else []), SECTION_END]


def replace_section(body: str, heading: str, content: str) -> str:
    """節を差し替える。無ければ末尾へ足す。節の外は変えず、同じ呼び出しを繰り返しても変わらない。"""
    lines = _lines(body)
    block = _section_block(heading, content)
    span = _find(lines, heading)
    if span is None:
        head = "\n".join(lines).rstrip("\n")
        return (head + "\n\n" if head else "") + "\n".join(block) + "\n"
    before = lines[:span.start]
    if span.after != span.end:
        # 目印の後ろは節の外である。空行も含めてそのまま残す。
        return "\n".join(before + block + lines[span.after:])
    after = lines[span.after:]
    while after and not after[0].strip():
        after = after[1:]
    if any(x.strip() for x in after):
        return "\n".join(before + block + [""] + after)
    return "\n".join(before + block) + "\n"


def append_line(body: str, line: str) -> str:
    """本文の末尾へ 1 行を足す。同じ行が既にあれば足さない。

    **最後の節が目印を持たなければ、足す前に目印を置いて閉じる。** 目印が無いまま足すと、足した行が
    最後の節の中に入り、次の節の差し替えで消える（#659）。
    """
    line = line.strip("\n")
    lines = _lines(body)
    if any(x.rstrip() == line.rstrip() for x in lines):
        return body
    heads = _heading_lines(lines)
    if heads:
        last_i, _ = heads[-1]
        tail = lines[last_i + 1:]
        if not any(x.strip() == SECTION_END for x in tail):
            while lines and not lines[-1].strip():
                lines.pop()
            lines.append(SECTION_END)
    head = "\n".join(lines).rstrip("\n")
    return (head + "\n\n" if head else "") + line + "\n"


def fetch_body(repo: str, number: int) -> str | None:
    """issue / PR の本文を REST で取る（PR も issues の端点で読める）。取得できなければ `None`。"""
    resp = rest(f"repos/{repo}/issues/{int(number)}")
    if resp is None or not isinstance(resp.body, dict) or "body" not in resp.body:
        return None
    return str(resp.body.get("body") or "")


def update_body(repo: str, number: int, body: str) -> bool:
    return rest(f"repos/{repo}/issues/{int(number)}", method="PATCH", payload={"body": body}) is not None


def body_section(op: str, number: int, repo: str | None, heading: str = "",
                 content: str = "", line: str = "") -> tuple[dict[str, Any], int]:
    """本文を取り、節を取得・置換・追記する。変わらないときは書き込まない。"""
    tool = "body-section"
    slug = resolve_repo(repo)
    if not slug:
        return step_result.result(tool, "stopped", "リポジトリを決められない"), step_result.EXIT_PRECONDITION
    body = fetch_body(slug, number)
    if body is None:
        return step_result.result(tool, "stopped", f"#{number} の本文を取得できない"), \
            step_result.EXIT_UNREADABLE
    if op == "get":
        text = get_section(body, heading)
        item = {"kind": "section", "name": heading, "result": "absent" if text is None else "found"}
        if text is not None:
            item["content"] = text
        return step_result.result(tool, "ok", f"#{number} {heading}: {item['result']}", [item]), 0
    new = replace_section(body, heading, content) if op == "replace" else append_line(body, line)
    name = heading if op == "replace" else "append"
    if new == body:
        return step_result.result(tool, "ok", f"#{number} {name}: 変更なし",
                                  [{"kind": "section", "name": name, "result": "unchanged"}]), 0
    if not update_body(slug, number, new):
        return step_result.result(tool, "stopped", f"#{number} の本文を更新できない"), \
            step_result.EXIT_VIOLATION
    return step_result.result(tool, "ok", f"#{number} {name}: 更新した",
                              [{"kind": "section", "name": name, "result": "updated"}]), 0


# ---------------- review-post ----------------


def viewer_login() -> str | None:
    r = gh(["api", "user", "--jq", ".login"])
    return r.stdout.strip() or None if r.returncode == 0 else None


def review_post(payload: str, result: str, pr: int, round_no: int, seat: str,
                repo: str | None = None, head_sha: str | None = None,
                queue_dir: str | None = None) -> tuple[dict[str, Any], int]:
    """レビューを 1 回で投稿する。PR の作成者が自分なら REQUEST_CHANGES を COMMENT へ下げる。

    投稿そのものは `result_posts.post_review`（待ち行列・位置の拒否の退避）に任せる。
    """
    import post_queue
    import result_posts

    tool = "review-post"
    slug = resolve_repo(repo)
    if not slug:
        return step_result.result(tool, "stopped", "リポジトリを決められない"), step_result.EXIT_PRECONDITION
    resp = rest(f"repos/{slug}/pulls/{int(pr)}")
    if resp is None or not isinstance(resp.body, dict) or not resp.body.get("number"):
        return step_result.result(tool, "stopped", f"PR #{pr} を取得できない"), step_result.EXIT_UNREADABLE
    meta = _meta_from_rest(slug, resp.body)
    me = viewer_login()
    if me is None:
        return step_result.result(tool, "stopped", "自分のアカウントを確かめられない"), \
            step_result.EXIT_PRECONDITION
    is_own = meta["author"] == me
    qdir = pathlib.Path(queue_dir) if queue_dir else default_out_dir(slug, pr) / post_queue.QUEUE_DIRNAME
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
                    help=f"カンマ区切り: {', '.join(WITH_PARTS)}")
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
        unknown = parts - set(WITH_PARTS)
        if unknown:
            step_result.emit(step_result.result("pr-info", "stopped",
                                                f"知らない --with: {', '.join(sorted(unknown))}"), 2)
        obj, code = pr_info(args.pr, args.repo, parts,
                            pathlib.Path(args.out_dir) if args.out_dir else None)
    elif args.cmd == "unresolved-threads":
        slug = resolve_repo(args.repo)
        threads = unresolved_threads(slug or "", args.pr)
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
