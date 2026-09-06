#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""GitHub が使えない間の投稿を積む待ち行列（収束ループ共通層）。

GitHub の利用回数の上限に達すると投稿は失敗する。**失敗をそのまま止める側へ倒すと、
レビューを 1 巡も進められない。** 上限のときだけ投稿する内容をローカルへ積み、回復した
後に順に流す（#291）。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 待ち行列 | 投稿できないときに、投稿する内容を順序付きでローカルへ積む仕組み |
| 積む | 待ち行列へ項目を 1 件足すこと |
| 流す | 待ち行列の項目を順に GitHub へ送り、送れたものを消すこと |
| 上限 | GitHub の利用回数の上限。一次（毎時の総数）と二次（短時間の集中）を区別しない |
| 投稿 | GitHub の状態を変える呼び出し |

## この層が持つもの・持たないもの

**持つのは、積む・流す・上限を見分けるところまでである。** どの投稿を積むか、積んだまま
収束させてよいかは呼び出し側（`cross-review` の `state.py` / `cross-refactoring` の
`refactor.py`）が決める。

## 待ち行列の形

置き場所は状態ファイルと同じ `<作業ツリー>/.cross_review/pending/` で、1 項目 1 ファイルの
JSON である。名前は `<連番 4 桁>-<種別>-<識別子>.json` で、**順序はファイル名の連番だけが
決める**。

| 項目のキー | 意味 |
| --- | --- |
| `seq` | 連番。既存の最大値 + 1 |
| `kind` | 種別。冪等の照会をどれにするかを決める |
| `repo` / `pr` | 宛先 |
| `actor` | 投稿する主体のログイン名。冪等の照合で投稿者を見るために持つ |
| `created_at` / `attempts` / `last_error` | 積んだ時刻と、送ろうとした回数と、最後の失敗 |
| `request` | 送る内容。`method` / `path` / `fields`（GraphQL は `query`） |
| `match` | 冪等の照会で「同じ」とみなす条件 |
| `extra` | 呼び出し側が使う付随情報（担当・ラウンドなど）。この層は読まない |
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Any, NamedTuple

# 積める種別。**この版で積む側があるのは `pr-comment` だけである。** ほかの 3 つは
# 受け皿として持つ（投稿の責務を進行側へ移すのは次の変更、#350）。
KINDS = ("pr-comment", "review-post", "review-reply", "thread-resolve")

# 本文を比べる長さ。振動の検知が指摘の同一性を測るときと同じ幅である
# （`cross-review` の `OSCILLATION_BODY_CHARS`）。**同じ判断に別々の値を持たない。**
BODY_MATCH_CHARS = 80

# 待ち行列を置くディレクトリの名前。状態ファイルと同じ親の下に置く。
QUEUE_DIRNAME = "pending"

# 一覧の照会で読むページ数の上限。1 ページ 100 件（REST の上限）で読む。
LIST_PER_PAGE = 100
LIST_MAX_PAGES = 10

# `post` の結果。
POSTED = "posted"
QUEUED = "queued"
FAILED = "failed"

# レビューの判定と、GitHub 側に残る状態の対応。
_REVIEW_STATE = {
    "APPROVE": "APPROVED",
    "REQUEST_CHANGES": "CHANGES_REQUESTED",
    "COMMENT": "COMMENTED",
}

# 未解決のスレッドの識別子だけを読む問い合わせ。**解決の冪等はこの一覧だけで決まる**
# （一覧に無ければ、既に解決されている）。
_UNRESOLVED_QUERY = """
query($owner: String!, $name: String!, $pr: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved }
      }
    }
  }
}
"""
_UNRESOLVED_JQ = (
    ".data.repository.pullRequest.reviewThreads.nodes[]"
    " | select(.isResolved == false) | .id"
)

_RESOLVE_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { id isResolved }
  }
}
"""


# ---------------- 上限の見分け ----------------

_HTTP_RE = re.compile(r"\(HTTP (\d{3})\)")
# 上限を指す語。一次・二次・GraphQL の 3 つの言い回しを拾う。
_RATE_WORDS = ("rate limit", "rate_limited", "abuse detection")


class Attempt(NamedTuple):
    """`gh` を 1 回実行した結果。"""

    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def http(self) -> int | None:
        """標準エラーに書かれた HTTP の状態。無ければ `None`。"""
        m = _HTTP_RE.search(self.stderr or "")
        return int(m.group(1)) if m else None

    @property
    def message(self) -> str:
        """標準出力の本文から `message` を読む。読めなければ空文字。"""
        try:
            body = json.loads(self.stdout or "")
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(body, dict):
            return ""
        parts = [str(body.get("message") or "")]
        errors = body.get("errors")
        if isinstance(errors, list):
            for e in errors:
                if isinstance(e, dict):
                    parts += [str(e.get("message") or ""), str(e.get("type") or "")]
        return " ".join(p for p in parts if p)

    def summary(self) -> str:
        """状態ファイルへ残す短い失敗の説明。"""
        text = self.message or (self.stderr or "").strip()
        return f"exit={self.code} {text}"[:300]


def run(cmd: list[str], stdin: str | None = None) -> Attempt:
    """`gh` を 1 回実行する。**例外を投げない。**"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, input=stdin)
    except OSError as exc:
        return Attempt(127, "", f"gh の実行に失敗: {exc}")
    return Attempt(r.returncode, r.stdout or "", r.stderr or "")


def _has_rate_words(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in _RATE_WORDS)


def quota_remaining() -> int | None:
    """残り回数を引く。**この照会そのものは上限を消費しない**（実測）。

    読めなければ `None`。決まらないときの最後の材料であり、読めないときは上限では
    ないものとして扱う（止める側へ倒す）。
    """
    a = run(["gh", "api", "rate_limit", "--jq",
             "[.resources.core.remaining, .resources.graphql.remaining] | min"])
    if not a.ok:
        return None
    text = a.stdout.strip().splitlines()
    try:
        return int(text[0]) if text else None
    except ValueError:
        return None


def is_rate_limited(attempt: Attempt) -> bool:
    """この失敗が上限によるものか。

    **標準エラーの `(HTTP <番号>)` と標準出力の `message` の両方を読む。** 403 は上限と
    権限の誤りの両方で返るため、片方だけでは分けられない。なお決まらないときだけ
    残り回数を引く。

    HTTP の番号が書かれないことがある。GraphQL の失敗は `gh: GraphQL: API rate limit
    already exceeded ...` の形で、状態行を持たない（#291 の実例）。その場合は語で決める。
    """
    if attempt.ok:
        return False
    status = attempt.http
    if status is not None and status not in (403, 429):
        return False
    if _has_rate_words(f"{attempt.message} {attempt.stderr}"):
        return True
    if status in (403, 429):
        return quota_remaining() == 0
    return False


# ---------------- 送る内容の組み立て ----------------


def request_for(kind: str, repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    """種別ごとに、送る要求と冪等の照合条件を組む。"""
    if kind == "pr-comment":
        return {
            "request": {"method": "POST",
                        "path": f"repos/{repo}/issues/{int(pr)}/comments",
                        "fields": {"body": fields["body"]}},
            "match": {"body": fields["body"]},
        }
    if kind == "review-post":
        body = {"body": fields.get("body", ""), "event": fields["event"]}
        # **投稿先の commit は積んだ時点で決める。** Reviews API は `commit_id` を
        # 省くと送った時点の head へ付けるため、積んでから流すまでに head が進むと、
        # レビューが読んでいない commit に付く。行を指す `comments` はその commit の
        # 差分で解決されるため、位置がずれるか 422 で落ちる。ラウンド開始時に読んだ
        # `rounds[-1].head_sha` を呼び出し側が渡す。
        if fields.get("commit_id"):
            body["commit_id"] = fields["commit_id"]
        if fields.get("comments"):
            body["comments"] = fields["comments"]
        return {
            "request": {"method": "POST",
                        "path": f"repos/{repo}/pulls/{int(pr)}/reviews",
                        "fields": body},
            "match": {"event": fields["event"], "body": fields.get("body", "")},
        }
    if kind == "review-reply":
        target = int(fields["in_reply_to"])
        return {
            "request": {"method": "POST",
                        "path": f"repos/{repo}/pulls/{int(pr)}/comments/"
                                f"{target}/replies",
                        "fields": {"body": fields["body"]}},
            "match": {"in_reply_to": target, "body": fields["body"]},
        }
    if kind == "thread-resolve":
        return {
            "request": {"method": "GRAPHQL", "path": "graphql",
                        "query": _RESOLVE_MUTATION,
                        "fields": {"threadId": fields["thread_id"]}},
            "match": {"thread_id": fields["thread_id"]},
        }
    raise ValueError(f"未知の種別: {kind}")


def send(item: dict[str, Any]) -> Attempt:
    """項目を 1 件 GitHub へ送る。"""
    req = item["request"]
    if req.get("method") == "GRAPHQL":
        cmd = ["gh", "api", "graphql", "-f", f"query={req['query']}"]
        for k, v in (req.get("fields") or {}).items():
            cmd += ["-F", f"{k}={v}"]
        return run(cmd)
    # 本文は標準入力から JSON で渡す。引数の長さの制限に掛からない。
    cmd = ["gh", "api", "--method", req.get("method", "POST"), req["path"], "--input", "-"]
    return run(cmd, stdin=json.dumps(req.get("fields") or {}, ensure_ascii=False))


# ---------------- 冪等の照会 ----------------


def _list_all(path: str) -> list[dict[str, Any]] | None:
    """一覧を読み切る。読めなければ `None`（0 件と区別する）。"""
    rows: list[dict[str, Any]] = []
    for page in range(1, LIST_MAX_PAGES + 1):
        sep = "&" if "?" in path else "?"
        a = run(["gh", "api", f"{path}{sep}per_page={LIST_PER_PAGE}&page={page}"])
        if not a.ok:
            return None
        try:
            body = json.loads(a.stdout or "[]")
        except json.JSONDecodeError:
            return None
        if not isinstance(body, list):
            return None
        rows += [r for r in body if isinstance(r, dict)]
        if len(body) < LIST_PER_PAGE:
            break
    return rows


def unresolved_thread_ids(repo: str, pr: int) -> list[str] | None:
    """未解決のスレッドの識別子。読めなければ `None`。"""
    owner, sep, name = str(repo or "").partition("/")
    if not (owner and sep and name):
        return None
    a = run(["gh", "api", "graphql", "--paginate",
             "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"pr={int(pr)}",
             "-f", f"query={_UNRESOLVED_QUERY}", "--jq", _UNRESOLVED_JQ])
    if not a.ok:
        return None
    return [line.strip() for line in a.stdout.splitlines() if line.strip()]


def _by_actor(row: dict[str, Any], actor: str | None) -> bool:
    if not actor:
        return True
    return str((row.get("user") or {}).get("login") or "") == actor


def _first(rows: list[dict[str, Any]] | None, pred) -> tuple[bool | None, dict | None]:
    if rows is None:
        return None, None
    found = next((r for r in rows if pred(r)), None)
    return (found is not None), found


def _match_pr_comment(
    repo: str, pr: int, match: dict[str, Any], actor: str | None
) -> tuple[bool | None, dict[str, Any] | None]:
    return _first(
        _list_all(f"repos/{repo}/issues/{pr}/comments"),
        lambda r: _by_actor(r, actor) and r.get("body") == match.get("body"))


def _match_review_post(
    repo: str, pr: int, match: dict[str, Any], actor: str | None
) -> tuple[bool | None, dict[str, Any] | None]:
    want = _REVIEW_STATE.get(str(match.get("event") or ""), "")
    head = str(match.get("body") or "")[:BODY_MATCH_CHARS]
    return _first(
        _list_all(f"repos/{repo}/pulls/{pr}/reviews"),
        lambda r: (_by_actor(r, actor)
                   and str(r.get("state") or "") == want
                   and str(r.get("body") or "")[:BODY_MATCH_CHARS] == head))


def _match_review_reply(
    repo: str, pr: int, match: dict[str, Any], actor: str | None
) -> tuple[bool | None, dict[str, Any] | None]:
    return _first(
        _list_all(f"repos/{repo}/pulls/{pr}/comments"),
        lambda r: (str(r.get("in_reply_to_id") or "") == str(match.get("in_reply_to"))
                   and r.get("body") == match.get("body")))


def _match_thread_resolve(
    repo: str, pr: int, match: dict[str, Any], actor: str | None
) -> tuple[bool | None, dict[str, Any] | None]:
    del actor
    ids = unresolved_thread_ids(repo, pr)
    if ids is None:
        return None, None
    return (str(match.get("thread_id")) not in ids), None


POSTED_MATCH_HANDLERS = {
    "pr-comment": _match_pr_comment,
    "review-post": _match_review_post,
    "review-reply": _match_review_reply,
    "thread-resolve": _match_thread_resolve,
}


def posted_match(item: dict[str, Any]) -> tuple[bool | None, dict[str, Any] | None]:
    """同じ内容が既に GitHub 側にあるか。あるときは、その投稿そのものも返す。

    **確かめられないときは送る側へ倒す**（`(None, None)`）。送らないと項目が永久に
    残る。確かめられないのは GitHub へ届いていないときであり、そのまま送っても同じ
    失敗で積まれ直す。

    **見つけた投稿を返すのは、流す側が送ったときと同じ形を作れるようにするためである。**
    送信に成功した直後に中断すると、GitHub 側には投稿があるのに項目は残る。次に流すと
    ここで見つかって送らずに消えるため、送った応答が呼び出し側へ渡らない。応答が無いと、
    呼び出し側は届いたことを確かめられないまま待ち行列を空にする（#261 の前提が崩れる）。
    照会で見つけた行を応答の代わりに渡すことで、送った場合と同じ経路に乗せる。

    照会の形が行を返さない種別（`thread-resolve`）は、あることだけを返す。
    """
    kind, repo, pr = item["kind"], item["repo"], int(item["pr"])
    match, actor = item.get("match") or {}, item.get("actor")
    handler = POSTED_MATCH_HANDLERS.get(kind)
    if handler is None:
        return None, None
    return handler(repo, pr, match, actor)


def already_posted(item: dict[str, Any]) -> bool | None:
    """同じ内容が既に GitHub 側にあるか。確かめられなければ `None`。"""
    return posted_match(item)[0]


# ---------------- 待ち行列 ----------------

_SEQ_RE = re.compile(r"^(\d{4})-")


def _read_item(path: pathlib.Path) -> dict[str, Any] | None:
    """待ち行列の項目を 1 件読む。読めなければ `None`。

    項目は作成先の JSON ファイルへ直接書かれるため、書き込みの途中で終了すると
    空または途中までのファイルが残りうる。
    """
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return item if isinstance(item, dict) else None


class FlushResult(NamedTuple):
    """流した結果。"""

    sent: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    failed: dict[str, Any] | None
    remaining: int
    rate_limited: bool


class Queue:
    """1 つの Pull Request 分の待ち行列。"""

    def __init__(self, directory: str | pathlib.Path) -> None:
        self.dir = pathlib.Path(directory)

    def paths(self) -> list[pathlib.Path]:
        """連番の順に並べた項目のファイル。"""
        if not self.dir.is_dir():
            return []
        return sorted(self.dir.glob("*.json"), key=lambda p: p.name)

    def count(self) -> int:
        return len(self.paths())

    def items(self) -> list[tuple[pathlib.Path, dict[str, Any]]]:
        """読める項目だけを連番の順に返す。

        **読めない項目をどう扱うかは `flush()` が決める。** ここで落とすのは、
        件数を数えるだけの呼び出し元に読み取りの失敗を持ち込まないためである。
        """
        out: list[tuple[pathlib.Path, dict[str, Any]]] = []
        for p in self.paths():
            item = _read_item(p)
            if item is not None:
                out.append((p, item))
        return out

    def _next_seq(self) -> int:
        seqs = [int(m.group(1)) for m in
                (_SEQ_RE.match(p.name) for p in self.paths()) if m]
        return (max(seqs) + 1) if seqs else 1

    def add(self, item: dict[str, Any], ident: str | int) -> pathlib.Path:
        """項目を 1 件足す。連番は `O_EXCL` で確保する。

        積むのは進行側の 1 プロセスだけであるため関門は要らないが、**中断して再開した
        ときに番号が重ならないようにする。**
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        seq = self._next_seq()
        while True:
            path = self.dir / f"{seq:04d}-{item['kind']}-{ident}.json"
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                seq += 1
                continue
            item["seq"] = seq
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(item, f, indent=2, ensure_ascii=False)
            return path

    def flush(self) -> FlushResult:
        """積んだ項目を連番の順に送る。

        **1 件でも送れなければそこで止める。** 先の項目を飛ばして後の項目を送ると、
        Pull Request 上での順序が入れ替わる。
        """
        sent: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: dict[str, Any] | None = None
        rate_limited = False
        for path in self.paths():
            item = _read_item(path)
            if item is None:
                # **読めない項目を黙って飛ばさない。** `count()` はファイルを数え
                # 続けるため、飛ばすと送りも失敗の報告もしないまま件数だけが残り、
                # 判定は終了コード 8 を返し続けて誰も直せない状態になる。ここで
                # 止めて理由を返せば、その項目を捨てるか直すかを人が選べる。
                failed = {
                    "path": str(path),
                    "last_error": f"待ち行列の項目を読めない ({path.name})",
                }
                break
            found, row = posted_match(item)
            if found is True:
                # **送った場合と同じ形で返す。** 呼び出し側は届いたことを応答から
                # 確かめるため、既に届いていた項目にも見つけた投稿を積んで渡す。
                if row is not None:
                    item["response"] = row
                path.unlink(missing_ok=True)
                skipped.append(item)
                continue
            attempt = send(item)
            if attempt.ok:
                try:
                    item["response"] = json.loads(attempt.stdout or "null")
                except json.JSONDecodeError:
                    item["response"] = None
                path.unlink(missing_ok=True)
                sent.append(item)
                continue
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_error"] = attempt.summary()
            path.write_text(json.dumps(item, indent=2, ensure_ascii=False),
                            encoding="utf-8")
            failed = item
            rate_limited = is_rate_limited(attempt)
            break
        return FlushResult(sent, skipped, failed, self.count(), rate_limited)


def enqueue(queue: Queue, kind: str, repo: str, pr: int, fields: dict[str, Any],
            actor: str | None = None, extra: dict[str, Any] | None = None,
            last_error: str = "", attempts: int = 0) -> pathlib.Path:
    """投稿する内容を 1 件積む。"""
    if kind not in KINDS:
        raise ValueError(f"未知の種別: {kind}")
    built = request_for(kind, repo, int(pr), fields)
    item = {
        "seq": 0,
        "kind": kind,
        "repo": repo,
        "pr": int(pr),
        "actor": actor,
        "created_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(
            timespec="seconds"),
        "attempts": attempts,
        "last_error": last_error,
        "request": built["request"],
        "match": built["match"],
        "extra": extra or {},
    }
    return queue.add(item, extra.get("ident") if extra else pr)


def post(queue: Queue, kind: str, repo: str, pr: int, fields: dict[str, Any],
         actor: str | None = None, extra: dict[str, Any] | None = None
         ) -> tuple[str, Attempt | None]:
    """投稿を 1 件行う。上限のときは積んで先へ進む。

    **待ち行列に先客がいるときは、送らずに積む。** 先に流してから送らないと、
    Pull Request 上での順序が入れ替わる。
    """
    if queue.count():
        queue.flush()
    if queue.count():
        enqueue(queue, kind, repo, pr, fields, actor=actor, extra=extra)
        return QUEUED, None
    built = request_for(kind, repo, int(pr), fields)
    item = {"kind": kind, "repo": repo, "pr": int(pr), "actor": actor,
            "request": built["request"], "match": built["match"]}
    attempt = send(item)
    if attempt.ok:
        return POSTED, attempt
    if is_rate_limited(attempt):
        enqueue(queue, kind, repo, pr, fields, actor=actor, extra=extra,
                last_error=attempt.summary(), attempts=1)
        return QUEUED, attempt
    return FAILED, attempt


# ---------------- 上限のときに待って再実行する ----------------


def retry(cmd: list[str], max_wait: float = 900.0, interval: float = 30.0,
          stdin: str | None = None, sleep=time.sleep) -> Attempt:
    """上限のときだけ待って再実行する。ほかの失敗はそのまま返す。

    **Pull Request の作成は積めない。** 作成が終わるまで新しい番号が決まらず、番号が
    決まらないと以降のすべての項目の宛先が決まらない。巻き直しの 3 種（作成・close・
    reopen）はこの経路で回復を待つ。待つあいだラウンドは進まないが、巻き直しは
    8 ラウンドに 1 度しか起きない。
    """
    waited = 0.0
    while True:
        attempt = run(cmd, stdin=stdin)
        if attempt.ok or not is_rate_limited(attempt):
            return attempt
        if interval <= 0 or waited + interval > max_wait:
            return attempt
        print(f"⏳ 上限のため {interval:g} 秒待って再実行します: {' '.join(cmd)}",
              file=sys.stderr)
        sleep(interval)
        waited += interval


# ---------------- CLI ----------------


def _read_body(path: str) -> str:
    return sys.stdin.read() if path == "-" else pathlib.Path(path).read_text(
        encoding="utf-8")


def cmd_post(args: argparse.Namespace) -> int:
    q = Queue(args.dir)
    outcome, attempt = post(q, args.kind, args.repo, args.pr,
                            {"body": _read_body(args.body_file)},
                            actor=args.actor or None)
    print(f"QUEUED={'1' if outcome == QUEUED else '0'}")
    if outcome == QUEUED:
        print(f"⏳ 上限のため待ち行列へ積みました（残り {q.count()} 件）", file=sys.stderr)
        return 0
    if outcome == FAILED:
        print(f"❌ 投稿に失敗しました: {attempt.summary() if attempt else ''}",
              file=sys.stderr)
        return 1
    return 0


def cmd_flush(args: argparse.Namespace) -> int:
    q = Queue(args.dir)
    result = q.flush()
    print(f"PENDING_SENT={len(result.sent)}")
    print(f"PENDING_SKIPPED={len(result.skipped)}")
    print(f"PENDING_REMAINING={result.remaining}")
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    print(f"PENDING_COUNT={Queue(args.dir).count()}")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    stdin = None if sys.stdin.isatty() else sys.stdin.read()
    attempt = retry(args.command, max_wait=args.max_wait, interval=args.interval,
                    stdin=stdin)
    sys.stdout.write(attempt.stdout)
    sys.stderr.write(attempt.stderr)
    return attempt.code


def main() -> None:
    p = argparse.ArgumentParser(description="投稿の待ち行列（#291）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("post", help="投稿する。上限のときは積んで終了コード 0")
    sp.add_argument("--dir", required=True)
    sp.add_argument("--kind", choices=list(KINDS), required=True)
    sp.add_argument("--repo", required=True)
    sp.add_argument("--pr", type=int, required=True)
    sp.add_argument("--body-file", required=True, help="`-` で標準入力から読む")
    sp.add_argument("--actor", default="")
    sp.set_defaults(func=cmd_post)

    sp = sub.add_parser("flush", help="積んだ投稿を連番の順に流す")
    sp.add_argument("--dir", required=True)
    sp.set_defaults(func=cmd_flush)

    sp = sub.add_parser("count", help="積んだ件数を数える")
    sp.add_argument("--dir", required=True)
    sp.set_defaults(func=cmd_count)

    sp = sub.add_parser("retry", help="上限のときだけ待って再実行する")
    sp.add_argument("--max-wait", type=float, default=900.0)
    sp.add_argument("--interval", type=float, default=30.0)
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_retry)

    args = p.parse_args()
    if getattr(args, "command", None) and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
