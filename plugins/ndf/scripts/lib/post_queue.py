#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""GitHub が使えない間の投稿を積む待ち行列（収束ループ共通層）。

GitHub の利用回数の上限に達すると投稿は失敗する。**失敗をそのまま止める側へ倒すと、
レビューを 1 巡も進められない。** 上限のときだけ投稿する内容をローカルへ積み、回復した
後に順に流す（#291）。GitHub の一時的な失敗（5xx・本文の無い応答・ネットワークの失敗）も
同じく積んで後で流す（#1843）。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 待ち行列 | 投稿できないときに、投稿する内容を順序付きでローカルへ積む仕組み |
| 積む | 待ち行列へ項目を 1 件足すこと |
| 流す | 待ち行列の項目を順に GitHub へ送り、送れたものを消すこと |
| 上限 | GitHub の利用回数の上限。一次（毎時の総数）と二次（短時間の集中）を区別しない |
| 一時的な失敗 | 送り直せば届く見込みのある失敗（HTTP 500・502・503・504、`(HTTP nnn)` の無い本文なしの応答、ネットワークの失敗）。上限とは別に見分ける |
| 投稿 | GitHub の状態を変える呼び出し |

## この層が持つもの・持たないもの

**持つのは、積む・流す・上限と一時的な失敗を見分けるところまでである。** どの投稿を積むか、積んだまま
収束させてよいかは呼び出し側（`cross-review` の `state.py` / `cross-refactoring` の
`refactor.py`）が決める。

## 待ち行列の形

項目は耐久の記録（`lib/durable.py`。種類 `posts`・鍵の元は待ち行列のディレクトリ
`<作業ツリー>/.cross_review/pending/` の絶対パス）に置く。項目の名前は `<連番 4 桁>-<種別>-<識別子>` で、
**順序は連番だけが決める**。積んだ記録と送りの試行 1 回が、それぞれ耐久ワークフロー
`post-<名前>-a<回>` 1 つである（回 0 が積んだ記録）。ディレクトリに残るのは、恒久の失敗の控え（`dropped/`）と、
移行の前に積まれて取り込んだファイル（`imported/`）だけである。

| 項目のキー | 意味 |
| --- | --- |
| `seq` | 連番。記録のある名前の最大値 + 1 |
| `kind` | 種別。冪等の照会をどれにするかを決める |
| `repo` / `pr` | 宛先 |
| `actor` | 投稿する主体のログイン名。冪等の照合で投稿者を見るために持つ |
| `created_at` / `attempts` / `last_error` | 積んだ時刻と、送ろうとした回数と、最後の失敗 |
| `last_transient` | 最後の送りが一時的な失敗で終わったか。真のときは、既投稿の照会ができないと送らない |
| `request` | 送る内容。`method` / `path` / `fields`（GraphQL は `query`） |
| `match` | 冪等の照会で「同じ」とみなす条件 |
| `extra` | 呼び出し側が使う付随情報（担当・ラウンドなど）。この層は読まない |

使う側のエントリポイントは `deps.require(..., "durable")` を先に呼ぶ。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import pathlib
import re
import sys
import time
from typing import Any, Iterator, NamedTuple

_LIB = pathlib.Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import clock  # noqa: E402  時刻の読み取り（#1142 の L0）
import deps  # noqa: E402  外部パッケージの環境（#1142 の決定 17）
import durable_keys  # noqa: E402  記録の有無を DBOS を読まずに確かめる
import gh_graphql  # noqa: E402  未解決のスレッドの問い合わせ（#1142 の L0）
import gh_quota  # noqa: E402  上限の語（#1142 の L0）
import proc  # noqa: E402  子プロセスの起動（#1142 の L0）

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

# 未解決のスレッドの識別子の一覧。問い合わせは gh_graphql と共有し、jq で識別子だけを取り出す。
# **解決の冪等はこの一覧だけで決まる**（一覧に無ければ、既に解決されている）。
_UNRESOLVED_QUERY = gh_graphql.UNRESOLVED_THREADS_QUERY
_UNRESOLVED_JQ = ".data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false) | .id"

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
# 指した位置を差分の中に見つけられないことを表す語。実測した応答は
# `Line could not be resolved` と `Path could not be resolved` の 2 つ。
_POSITION_WORD = "could not be resolved"
# 一時的な失敗とみなす HTTP の状態。
TRANSIENT_STATUSES = (500, 502, 503, 504)
# 応答の本文を読めなかったことを表す語（実例: HTTP 500・本文なしで `unexpected end of JSON input`、#1843）。
_EMPTY_BODY_WORDS = ("unexpected end of json input", "unexpected eof")
# ネットワークの失敗の語。`error connecting to`・`dial tcp`・`connection refused` は実測、残りは Go の標準の言い回し。
_NETWORK_WORDS = (
    "error connecting to",
    "dial tcp",
    "connection reset",
    "connection refused",
    "i/o timeout",
    "tls handshake timeout",
    "context deadline exceeded",
)
# 一時的な失敗のときに流し直す待ちの合計と間隔（秒）。上限の再実行の既定（900 秒・30 秒）を超えない（#1843 の決定 4）。
TRANSIENT_MAX_WAIT = 300.0
TRANSIENT_INTERVAL = 30.0


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
                elif isinstance(e, str):
                    # レビューの作成が拒まれたときは、語をつないだ文字列が並ぶ
                    # （実測: `["Line could not be resolved"]`）。
                    parts.append(e)
        return " ".join(p for p in parts if p)

    def summary(self) -> str:
        """状態ファイルへ残す短い失敗の説明。"""
        text = self.message or (self.stderr or "").strip()
        return f"exit={self.code} {text}"[:300]


def run(cmd: list[str], stdin: str | None = None) -> Attempt:
    """`gh` を 1 回実行する。**例外を投げない。**"""
    try:
        r = proc.run(cmd, check=False, input=stdin)
    except OSError as exc:
        return Attempt(127, "", f"gh の実行に失敗: {exc}")
    return Attempt(r.returncode, r.stdout or "", r.stderr or "")


def _has_rate_words(text: str) -> bool:
    """上限の語があるか。ライブラリの語（`gh_quota.is_rate_limited`）に、二次の上限の言い回しを足して見る。"""
    low = (text or "").lower()
    return gh_quota.is_rate_limited(text or "") or any(w in low for w in _RATE_WORDS)


def quota_remaining() -> int | None:
    """残り回数を引く。**この照会そのものは上限を消費しない**（実測）。

    読めなければ `None`。決まらないときの最後の材料であり、読めないときは上限では
    ないものとして扱う（止める側へ倒す）。
    """
    a = run(["gh", "api", "rate_limit", "--jq", "[.resources.core.remaining, .resources.graphql.remaining] | min"])
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


def is_position_unresolved(attempt: Attempt) -> bool:
    """この失敗が「指した位置を差分の中に見つけられない」ことによるものか。

    **レビューの作成は要求ごとに全件が拒まれる。** 差分の外の行やファイルを指した
    インラインが 1 件でもあると、正しいインラインも総評も作られない（実測、
    2026-09-22）。応答は語をつないだ 1 つの文字列で、どの項目かは指さないため、
    呼び出し側は要求のインラインをまとめて総評へ移して送り直す。

    **同じ状態で返る別の拒まれ方と分ける。** 判定の値の誤り
    （`Variable $event ... was provided invalid value`）と基準のコミットの誤り
    （`The commitOID is not part of the pull request`）はこの語を持たない。
    区別しないと、別の不具合が退避として飲み込まれる。
    """
    if attempt.ok or attempt.http != 422:
        return False
    return _POSITION_WORD in f"{attempt.message} {attempt.stderr}".lower()


def is_transient_failure(attempt: Attempt) -> bool:
    """この失敗が一時的なもの（送り直せば届く見込みがある）か（#1843）。

    次のどれかに当たり、上限の語を持たないとき真。**`gh` を呼ばない純粋な判定である。**
    5xx とネットワークの失敗は上限の状態（403・429）を持たないため、残り回数は引かない。

    - 標準エラーの `(HTTP <番号>)` が 500・502・503・504 のどれか
    - `(HTTP <番号>)` が無く、標準出力が空で、標準エラーが応答を読めなかったことを示す
    - `(HTTP <番号>)` が無く、標準エラーがネットワークの失敗を示す
    """
    if attempt.ok or _has_rate_words(f"{attempt.message} {attempt.stderr}"):
        return False
    status = attempt.http
    if status is not None:
        return status in TRANSIENT_STATUSES
    err = (attempt.stderr or "").lower()
    if not (attempt.stdout or "").strip() and any(w in err for w in _EMPTY_BODY_WORDS):
        return True
    return any(w in err for w in _NETWORK_WORDS)


# 項目そのものが送れないことを表す状態。送り直しても同じ応答が返る（宛先が無い・
# 要求が受け付けられない）。401 / 403 は項目ではなく認証か上限の問題であり、後ろの
# 項目も同じく送れないため含めない。
PERMANENT_STATUSES = (400, 404, 410, 422)
# 飛ばさない種別。レビューの拒否は呼び出し側が退避（`is_position_unresolved`）か
# 失敗かを決めるため、先頭で止めて返す。
_NOT_DROPPED_KINDS = ("review-post",)


def is_permanent_failure(item: dict[str, Any], attempt: Attempt) -> bool:
    """この項目の失敗が、送り直しても変わらない恒久的なものか（#962）。

    例はレビューの ID を宛先にした返信で、GitHub は `Parent comment not found` を返す。
    """
    if attempt.ok or item.get("kind") in _NOT_DROPPED_KINDS:
        return False
    return attempt.http in PERMANENT_STATUSES and not is_rate_limited(attempt)


# ---------------- 送る内容の組み立て ----------------


def _request_pr_comment(repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "request": {"method": "POST", "path": f"repos/{repo}/issues/{int(pr)}/comments", "fields": {"body": fields["body"]}},
        "match": {"body": fields["body"]},
    }


def _request_review_post(repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
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
    match = {"event": fields["event"], "body": fields.get("body", "")}
    # **照合をラウンドの開始より後へ絞る。** ラウンドの番号は実行ごとに 1 から数え
    # 直すため、番号と席の鍵だけでは前の実行のレビューに一致する。
    if fields.get("since"):
        match["since"] = fields["since"]
    return {
        "request": {"method": "POST", "path": f"repos/{repo}/pulls/{int(pr)}/reviews", "fields": body},
        "match": match,
    }


def _request_review_reply(repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    target = int(fields["in_reply_to"])
    return {
        "request": {
            "method": "POST",
            "path": f"repos/{repo}/pulls/{int(pr)}/comments/{target}/replies",
            "fields": {"body": fields["body"]},
        },
        "match": {"in_reply_to": target, "body": fields["body"]},
    }


def _request_thread_resolve(repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "request": {"method": "GRAPHQL", "path": "graphql", "query": _RESOLVE_MUTATION, "fields": {"threadId": fields["thread_id"]}},
        "match": {"thread_id": fields["thread_id"]},
    }


# 種別ごとの組み立て。**網羅はこの表が持つ。** 制御構文に散らすと、種別を足したときに
# どの枝を足し忘れたかが読めない。
_REQUEST_BUILDERS = {
    "pr-comment": _request_pr_comment,
    "review-post": _request_review_post,
    "review-reply": _request_review_reply,
    "thread-resolve": _request_thread_resolve,
}


def request_for(kind: str, repo: str, pr: int, fields: dict[str, Any]) -> dict[str, Any]:
    """種別ごとに、送る要求と冪等の照合条件を組む。"""
    builder = _REQUEST_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"未知の種別: {kind}")
    return builder(repo, pr, fields)


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
    argv = gh_graphql.unresolved_threads_argv(repo, pr, _UNRESOLVED_JQ)
    if argv is None:
        return None
    a = run(argv)
    if not a.ok:
        return None
    return [line.strip() for line in a.stdout.splitlines() if line.strip()]


def _by_actor(row: dict[str, Any], actor: str | None) -> bool:
    if not actor:
        return True
    return str((row.get("user") or {}).get("login") or "") == actor


def _head(body: Any) -> str:
    return str(body or "")[:BODY_MATCH_CHARS]


def review_match_key(body: Any) -> str:
    """レビューの本文から、同じ投稿かどうかを決める鍵を作る。

    先頭行は `## 🤖 cross-review | round <R> | <席> | <判定>` である。**鍵に取るのは
    席までで、判定の語を含めない。** 含めると、起動し直して判定が変わったときに別の
    投稿と読まれ、同じラウンド・同じ席のレビューが 2 件になる（#730 #583）。

    先頭行がこの形でないときは、本文の先頭 `BODY_MATCH_CHARS` 文字へ落とす。
    """
    first = str(body or "").splitlines()[0] if str(body or "") else ""
    parts = first.split("|")
    if len(parts) < 4:
        return _head(body)
    return "|".join(parts[:3]).strip() + "|"


def _comment_match(match: dict[str, Any], actor: str | None):
    head = _head(match.get("body"))
    return lambda row: _by_actor(row, actor) and _head(row.get("body")) == head


def _review_match(match: dict[str, Any], actor: str | None):
    """同じラウンド・同じ席のレビューか。

    **開始時刻を持つときは、それより後に出たレビューだけを見る。** 同じ実行の中の
    送り直しは見つかり、回し直す前の実行のレビューは外れる。開始時刻かレビューの
    時刻のどちらかを読めないときは、番号と席だけの照合へ落とす（二重に送る側へ
    倒さない）。
    """
    key = review_match_key(match.get("body"))
    since = clock.parse(match.get("since"), naive="reject")

    def _in_this_run(row: dict[str, Any]) -> bool:
        submitted = clock.parse(row.get("submitted_at"), naive="reject")
        return since is None or submitted is None or submitted >= since

    return lambda row: _by_actor(row, actor) and review_match_key(row.get("body")) == key and _in_this_run(row)


def _reply_match(match: dict[str, Any], actor: str | None):
    head = _head(match.get("body"))
    return lambda row: str(row.get("in_reply_to_id") or "") == str(match.get("in_reply_to")) and _head(row.get("body")) == head


_POSTED_MATCH_RULES = {
    "pr-comment": ("repos/{repo}/issues/{pr}/comments", _comment_match),
    "review-post": ("repos/{repo}/pulls/{pr}/reviews", _review_match),
    "review-reply": ("repos/{repo}/pulls/{pr}/comments", _reply_match),
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

    def _first(rows: list[dict[str, Any]] | None, pred) -> tuple[bool | None, dict | None]:
        if rows is None:
            return None, None
        found = next((r for r in rows if pred(r)), None)
        return (found is not None), found

    if kind == "thread-resolve":
        ids = unresolved_thread_ids(repo, pr)
        if ids is None:
            return None, None
        return (str(match.get("thread_id")) not in ids), None
    rule = _POSTED_MATCH_RULES.get(kind)
    if rule is not None:
        path_template, predicate_factory = rule
        return _first(
            _list_all(path_template.format(repo=repo, pr=pr)),
            predicate_factory(match, actor),
        )
    return None, None


# ---------------- 待ち行列 ----------------

_SEQ_RE = re.compile(r"^(\d{4,})-")
# 耐久ワークフローの ID `post-<名前>-a<回>`。名前は `<連番 4 桁>-<種別>-<識別子>`、回 0 は積んだ記録。
_ID_RE = re.compile(r"^post-(\d{4,}-.+)-a(\d+)$")
OPEN_STATES = ("queued", "unsent")  # 項目の記録の状態のうち、終わっていないもの


def read_item(path: pathlib.Path) -> dict[str, Any] | None:
    """移行の前のファイルの項目を 1 件読む。読めなければ `None`（書き込みの途中で終わったファイルが残りうる）。"""
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return item if isinstance(item, dict) else None


def rejected_by_position(item: dict[str, Any]) -> bool:
    """待ち行列に残った項目が、指した位置を解決できずに拒まれたものか（`is_position_unresolved` を項目の状態と説明で行う）。"""
    if int(item.get("last_status") or 0) != 422:
        return False
    return _POSITION_WORD in str(item.get("last_error") or "").lower()


class FlushResult(NamedTuple):
    """流した結果。"""

    sent: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    failed: dict[str, Any] | None
    remaining: int
    rate_limited: bool
    # 恒久的な失敗で飛ばし、`dropped/` へ控えを書いた項目（#962）。
    dropped: list[dict[str, Any]] = []
    # 先頭で止まった項目の失敗が一時的なものか（#1843）。上限（`rate_limited`）とは同時に立たない。
    transient: bool = False

    @property
    def waitable(self) -> bool:
        """待てば流れる失敗か（上限か一時的な失敗）。呼び出し側が「失敗に数えない」を決める唯一の印。"""
        return self.rate_limited or self.transient


def _seq_order(name: str) -> tuple[int, str]:
    return (int(m.group(1)) if (m := _SEQ_RE.match(name)) else 0, name)


def _aside(directory: pathlib.Path, name: str, item: dict[str, Any]) -> pathlib.Path:
    """送れない項目の控えを `dropped/<名前>.json` へ書く（人が読む）。"""
    dest = directory / "dropped" / f"{name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def _try_once(directory: str, name: str, item: dict[str, Any]) -> dict[str, Any]:
    """送りの試行 1 回。既投稿の照合 → 送る → 恒久の失敗の見分けの順で、項目の記録を返す。

    **前の送りが一時的な失敗で終わり、照会もできない項目は送らない**（#1843 の決定 3）。HTTP 500 は
    GitHub 側で作られた後に返ることがあり、届いたか分からないまま送ると同じ投稿が 2 件並ぶ。
    """
    item = dict(item)
    found, row = posted_match(item)
    if found is True:
        return {"state": "skipped", "item": {**item, "response": row} if row is not None else item}
    if found is None and item.get("last_transient"):
        return {"state": "unsent", "item": item, "rate_limited": False, "transient": True}
    attempt = send(item)
    if attempt.ok:
        item["response"] = None
        with contextlib.suppress(json.JSONDecodeError):
            item["response"] = json.loads(attempt.stdout or "null")
        return {"state": "sent", "item": item}
    item.update(attempts=int(item.get("attempts") or 0) + 1, last_error=attempt.summary(), last_status=attempt.http)
    if is_permanent_failure(item, attempt):
        _aside(pathlib.Path(directory), name, item)
        return {"state": "dropped", "item": item}
    rate_limited = is_rate_limited(attempt)
    transient = not rate_limited and is_transient_failure(attempt)
    item["last_transient"] = transient
    return {"state": "unsent", "item": item, "rate_limited": rate_limited, "transient": transient}


_FLOWS: dict[str, Any] = {}


def _flows() -> dict[str, Any]:
    """項目の記録（`note`）と送りの試行（`attempt`）の耐久ワークフロー。DBOS は項目を扱うときに初めて読む。"""
    if not _FLOWS:
        import durable

        tag = f"ndf.{__name__}"  # 同じファイルを別の名前で読んだモジュールと登録の名前を分ける
        try_step = durable.step(name=f"{tag}.try")(_try_once)
        note = durable.workflow(name=f"{tag}.note")(lambda record: record)
        _FLOWS.update(durable=durable, note=note, attempt=durable.workflow(name=f"{tag}.attempt")(lambda *a: try_step(*a)))
    return _FLOWS


class Queue:
    """1 つの Pull Request 分の待ち行列。項目は耐久の記録（種類 `posts`・鍵の元は待ち行列のディレクトリ）に置く。

    **項目の状態は、名前を接頭辞に持つ耐久ワークフローのうち最後に終わったものの出力が決める。** 回 0 が積んだ記録
    （`queued`）で、送りの試行ごとに回を進める（`sent`・`skipped`・`dropped`・`unsent`）。`drop` は `withdrawn` を記録する。
    試行は同期で流し、送れなければ `unsent` で終わる（待たない）。途中で落ちた試行は次に開くときに止める（`keep=()`）。"""

    def __init__(self, directory: str | pathlib.Path) -> None:
        self.dir = pathlib.Path(directory)
        self._left: list[pathlib.Path] = []  # 読めずに残った移行の前のファイル

    def _dormant(self) -> bool:
        """耐久の記録も移行の前のファイルも無い（DBOS を起動せずに空と答えられる）。"""
        return not self._import_legacy() and not durable_keys.record_path("posts", str(self.dir.absolute())).exists()

    @contextlib.contextmanager
    def _session(self) -> Iterator[dict[str, Any]]:
        """耐久の記録を開く。同じ記録を開いている間に呼べば、開いたものを使う。"""
        fl, ident = _flows(), str(self.dir.absolute())
        opened = fl["durable"].launched()
        if opened is not None and opened.key == fl["durable"].launch_key("posts", ident):
            yield fl
            return
        fl["durable"].launch("posts", ident, keep=())
        try:
            self._left = self._import_legacy(fl)
            yield fl
        finally:
            fl["durable"].close()

    def _scan(self, fl: dict[str, Any]) -> tuple[list[tuple[str, int, dict[str, Any]]], set[str]]:
        """終わっていない項目（名前・次の回・項目）を連番の順に並べたものと、記録のあるすべての名前。"""
        durable, last, done = fl["durable"], {}, {}
        for wid in durable.workflow_ids("post-"):
            if m := _ID_RE.match(wid):
                last[m.group(1)] = max(last.get(m.group(1), -1), int(m.group(2)))
        for wid in durable.workflow_ids("post-", ["SUCCESS"]):
            if (m := _ID_RE.match(wid)) and int(m.group(2)) >= done.get(m.group(1), (-1, ""))[0]:
                done[m.group(1)] = (int(m.group(2)), wid)
        records = {n: durable.output_of(wid) for n, (_, wid) in done.items()}
        rows = [(n, last[n] + 1, r["item"]) for n, r in records.items() if r.get("state") in OPEN_STATES]
        return sorted(rows, key=lambda r: _seq_order(r[0])), set(last)

    def _run(self, fl: dict[str, Any], func: Any, name: str, n: int, *args: Any) -> dict[str, Any]:
        wid = f"post-{name}-a{n}"
        fl["durable"].start(fl["durable"].WorkflowRef(wid, n, "start"), func, *args)
        if (got := fl["durable"].wait(wid, poll=0.01)).kind != "done":  # 同期で待つ（output_of の問い合わせは 1 秒おき）
            raise fl["durable"].DurableError(f"{wid} が終わらなかった: {got.value}")
        return got.value

    def _import_legacy(self, fl: dict[str, Any] | None = None) -> list[pathlib.Path]:
        """移行の前の `<連番>-*.json` を同じ名前（連番）のまま耐久の記録へ取り込み `imported/` へ移す。`fl` が無ければ数えるだけ。

        読めないファイルは取り込まずに残し、残ったファイルを返す。"""
        if not self.dir.is_dir():
            return []
        files = sorted((p for p in self.dir.glob("*.json") if _SEQ_RE.match(p.name)), key=lambda p: _seq_order(p.name))
        if fl is None or not files:
            return files
        known, left = self._scan(fl)[1], []
        for p in files:
            item = read_item(p)
            if item is None:
                left.append(p)
                continue
            if p.stem not in known:
                item.setdefault("seq", _seq_order(p.name)[0])
                self._run(fl, fl["note"], p.stem, 0, {"state": "queued", "item": item})
            (self.dir / "imported").mkdir(exist_ok=True)
            p.replace(self.dir / "imported" / p.name)
        return left

    def paths(self) -> list[pathlib.Path]:
        """連番の順に並べた、終わっていない項目の名前のパス（`<名前>.json`。ファイルは無い）と、読めずに残ったファイル。"""
        if self._dormant():
            return []
        with self._session() as fl:
            return sorted([self.dir / f"{n}.json" for n, _, _ in self._scan(fl)[0]] + self._left, key=lambda p: _seq_order(p.name))

    def count(self) -> int:
        return len(self.paths())

    def items(self) -> list[tuple[pathlib.Path, dict[str, Any]]]:
        """終わっていない項目（名前のパスと項目）を連番の順に返す。読めずに残ったファイルは含めない。"""
        if self._dormant():
            return []
        with self._session() as fl:
            return [(self.dir / f"{n}.json", item) for n, _, item in self._scan(fl)[0]]

    def _close_item(self, match: Any, state: str) -> dict[str, Any] | None:
        """`match(名前, 項目)` に当たる最初の終わっていない項目に `state` を記録し、項目を返す。"""
        with self._session() as fl:
            for n, nxt, item in self._scan(fl)[0]:
                if match(n, item):
                    self._run(fl, fl["note"], n, nxt, {"state": state, "item": item})
                    return {**item, "name": n}
        return None

    def drop(self, seq: Any) -> bool:
        """連番で指した項目を 1 件取り除く。差分の外を指す指摘を、送る内容を変えて積み直す前に使う（そのまま積むと 2 件並ぶ）。"""
        if seq is None or self._dormant():
            return False
        return self._close_item(lambda _n, item: item.get("seq") == seq, "withdrawn") is not None

    def set_aside(self, path: pathlib.Path) -> pathlib.Path:
        """送れない項目を待ち行列から外し、控えを `dropped/` へ残す。`path` は `paths()` の 1 つ。"""
        got = self._close_item(lambda n, _item: n == pathlib.Path(path).stem, "dropped")
        if got is None:
            raise FileNotFoundError(str(path))
        return _aside(self.dir, got.pop("name"), got)

    def add(self, item: dict[str, Any], ident: str | int) -> dict[str, Any]:
        """項目を 1 件足し、連番を入れた項目を返す。連番は記録のある名前の最大 + 1（実行の鍵の排他の中で決める）。"""
        with self._session() as fl:
            names = self._scan(fl)[1] | {p.stem for p in self._left}
            item["seq"] = max((_seq_order(n)[0] for n in names), default=0) + 1
            self._run(fl, fl["note"], f"{item['seq']:04d}-{item['kind']}-{ident}", 0, {"state": "queued", "item": item})
            return item

    def flush(self) -> FlushResult:
        """積んだ項目を連番の順に送る。項目ごとに、次の試行を 1 つの耐久ワークフローとして同期で流す。

        **1 件でも送れなければそこで止める。** 先の項目を飛ばして後の項目を送ると、
        Pull Request 上での順序が入れ替わる。**ただし恒久的な失敗（`is_permanent_failure`）
        の項目は飛ばし、`dropped/` へ控えを書いて後ろを送る**（#962）。送り直しても届かない
        項目で止まると、後ろの決着とまとめが何度流しても送られない。
        """
        done: dict[str, list[dict[str, Any]]] = {"sent": [], "skipped": [], "dropped": []}
        failed, rate_limited, transient, remaining = None, False, False, 0
        if not self._dormant():
            with self._session() as fl:
                rows = [(f"{n}.json", n, nxt, item) for n, nxt, item in self._scan(fl)[0]]
                for fname, name, nxt, item in sorted(rows + [(p.name, "", 0, {}) for p in self._left], key=lambda r: _seq_order(r[0])):
                    if not name:
                        failed = {"path": str(self.dir / fname), "last_error": f"待ち行列の項目を読めない ({fname})"}
                        break
                    out = self._run(fl, fl["attempt"], name, nxt, str(self.dir), name, item)
                    if out["state"] in done:
                        done[out["state"]].append(out["item"])
                        continue
                    failed, rate_limited, transient = out["item"], bool(out.get("rate_limited")), bool(out.get("transient"))
                    break
                remaining = len(self._scan(fl)[0]) + len(self._left)
        return FlushResult(done["sent"], done["skipped"], failed, remaining, rate_limited, done["dropped"], transient)


def enqueue(
    queue: Queue,
    kind: str,
    repo: str,
    pr: int,
    fields: dict[str, Any],
    actor: str | None = None,
    extra: dict[str, Any] | None = None,
    last_error: str = "",
    attempts: int = 0,
    last_transient: bool = False,
) -> dict[str, Any]:
    """投稿する内容を 1 件積み、連番を入れた項目を返す。"""
    if kind not in KINDS:
        raise ValueError(f"未知の種別: {kind}")
    built = request_for(kind, repo, int(pr), fields)
    created = _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
    item = {"seq": 0, "kind": kind, "repo": repo, "pr": int(pr), "actor": actor, "created_at": created, "attempts": attempts}
    item.update(last_error=last_error, request=built["request"], match=built["match"], extra=extra or {})
    if last_transient:
        item["last_transient"] = True
    return queue.add(item, extra.get("ident") if extra else pr)


def post(
    queue: Queue, kind: str, repo: str, pr: int, fields: dict[str, Any], actor: str | None = None, extra: dict[str, Any] | None = None
) -> tuple[str, Attempt | None]:
    """投稿を 1 件行う。上限か一時的な失敗のときは積んで先へ進む。

    **待ち行列に先客がいるときは、送らずに積む。** 先に流してから送らないと、
    Pull Request 上での順序が入れ替わる。
    """
    if queue.count():
        with queue._session():
            queue.flush()
            if queue.count():
                enqueue(queue, kind, repo, pr, fields, actor=actor, extra=extra)
                return QUEUED, None
    built = request_for(kind, repo, int(pr), fields)
    item = {"kind": kind, "repo": repo, "pr": int(pr), "actor": actor, "request": built["request"], "match": built["match"]}
    attempt = send(item)
    if attempt.ok:
        return POSTED, attempt
    transient = not is_rate_limited(attempt) and is_transient_failure(attempt)
    if transient or is_rate_limited(attempt):
        enqueue(queue, kind, repo, pr, fields, actor=actor, extra=extra, last_error=attempt.summary(), attempts=1, last_transient=transient)
        return QUEUED, attempt
    return FAILED, attempt


# ---------------- 上限のときに待って再実行する ----------------


def retry(cmd: list[str], max_wait: float = 900.0, interval: float = 30.0, stdin: str | None = None, sleep=time.sleep) -> Attempt:
    """上限のときだけ待って再実行する。ほかの失敗はそのまま返す。

    **Pull Request の作成は積めない。** 作成が終わるまで新しい番号が決まらず、番号が
    決まらないと以降のすべての項目の宛先が決まらない。巻き直しの 3 種（作成・close・
    reopen）はこの経路で回復を待つ。待つあいだラウンドは進まないが、巻き直しは
    8 ラウンドに 1 度しか起きない。待ちとやり直しは `lib/waits.py`（tenacity の包み）が持つ。
    """
    import waits  # 読む側（state.py ほか）が tenacity を要しないよう、ここで読む

    def announce(_seconds: float, _next: int) -> None:
        print(f"⏳ 上限のため {interval:g} 秒待って再実行します: {' '.join(cmd)}", file=sys.stderr)

    retried = lambda a: not a.ok and is_rate_limited(a)  # noqa: E731
    return waits.retry_call(
        lambda: run(cmd, stdin=stdin), retried, max_wait=max_wait, interval=interval, sleep=sleep, on_wait=announce
    ).value


# ---------------- CLI ----------------


def _read_body(path: str) -> str:
    return sys.stdin.read() if path == "-" else pathlib.Path(path).read_text(encoding="utf-8")


def cmd_post(args: argparse.Namespace) -> int:
    q = Queue(args.dir)
    outcome, attempt = post(q, args.kind, args.repo, args.pr, {"body": _read_body(args.body_file)}, actor=args.actor or None)
    print(f"QUEUED={'1' if outcome == QUEUED else '0'}")
    if outcome == QUEUED:
        if attempt is None:
            why = "先に積んだ投稿が残っている"
        else:
            why = "一時的な失敗の" if is_transient_failure(attempt) else "上限の"
        print(f"⏳ {why}ため待ち行列へ積みました（残り {q.count()} 件）", file=sys.stderr)
        return 0
    if outcome == FAILED:
        print(f"❌ 投稿に失敗しました: {attempt.summary() if attempt else ''}", file=sys.stderr)
        return 1
    return 0


def cmd_flush(args: argparse.Namespace) -> int:
    q = Queue(args.dir)
    result = q.flush()
    print(f"PENDING_SENT={len(result.sent)}")
    print(f"PENDING_SKIPPED={len(result.skipped)}")
    print(f"PENDING_DROPPED={len(result.dropped)}")
    print(f"PENDING_REMAINING={result.remaining}")
    print(f"PENDING_TRANSIENT={'1' if result.transient else '0'}")
    for item in result.dropped:
        print(f"⚠️ 送れない項目を飛ばしました ({item.get('kind')} #{item.get('seq')}): {item.get('last_error') or ''}", file=sys.stderr)
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    print(f"PENDING_COUNT={Queue(args.dir).count()}")
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    deps.require("waits")  # 標準入力を読む前に起動し直す
    stdin = None if sys.stdin.isatty() else sys.stdin.read()
    attempt = retry(args.command, max_wait=args.max_wait, interval=args.interval, stdin=stdin)
    sys.stdout.write(attempt.stdout)
    sys.stderr.write(attempt.stderr)
    return attempt.code


def main() -> None:
    p = argparse.ArgumentParser(description="投稿の待ち行列（#291）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("post", help="投稿する。上限か一時的な失敗のときは積んで終了コード 0")
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
    if args.cmd != "retry":
        deps.require("durable")  # 標準入力を読む前に起動し直す
    if getattr(args, "command", None) and args.command and args.command[0] == "--":
        args.command = args.command[1:]
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
