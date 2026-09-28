"""upkeep_gh.py: upkeep.py の gh の呼び出しと待ち（上限に当たったときの待ち・マイルストーンの引き当て）。"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import post_queue
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, StepError

# 待ちの既定。倍々の起点は、作成の二次的な制限で実測した 60 秒の間隔に合わせる。
DOUBLING_START = 60.0
DEFAULT_MAX_WAITS = 8
DEFAULT_MAX_WAIT = 900.0


def _decode_concat(text: str) -> list:
    """`gh api --paginate` が出す、連結された JSON 配列を 1 つの列にする。"""
    out, dec, i, text = [], json.JSONDecoder(), 0, text or ""
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return out
        obj, i = dec.raw_decode(text, i)
        out.extend(obj if isinstance(obj, list) else [obj])


def _split_include(stdout: str) -> tuple[dict, str]:
    """`gh api -i` の出力をヘッダ（小文字のキー）と本文に分ける。"""
    if not stdout.startswith("HTTP/"):
        return {}, stdout
    parts = re.split(r"\r?\n\r?\n", stdout, maxsplit=1)
    headers = {}
    for line in parts[0].splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers, parts[1] if len(parts) > 1 else ""


class Partial(Exception):
    """待ちの回数か長さが上限を超えた。"""


class Gh:
    """gh api を呼ぶ。上限に当たれば応答から待つ長さを決めて待ち、再実行する。"""

    def __init__(self, repo: str, max_waits: int = DEFAULT_MAX_WAITS, max_wait: float = DEFAULT_MAX_WAIT, sleep=None, now=None):
        self.repo = repo
        self.max_waits = max_waits
        self.max_wait = max_wait
        self.sleep = sleep or (lambda s: None if os.environ.get("NDF_UPKEEP_NO_SLEEP") else time.sleep(s))
        self.now = now or time.time
        self.waits: list[dict] = []
        self._doubling = 0.0

    def _wait_for(self, headers: dict) -> tuple[float, str]:
        ra = headers.get("retry-after")
        if ra and ra.strip().isdigit():
            return float(ra), "retry-after"
        reset = headers.get("x-ratelimit-reset")
        if headers.get("x-ratelimit-remaining") == "0" and reset and reset.isdigit():
            return max(1.0, float(reset) - self.now()), "reset"
        self._doubling = self._doubling * 2 if self._doubling else DOUBLING_START
        return self._doubling, "doubling"

    def call(self, args: list[str], stdin: str | None = None, target=None, paginate=False):
        """`gh api <args>` を呼び、本文の JSON を返す。失敗は StepError。"""
        cmd = ["gh", "api", *(["--paginate"] if paginate else ["-i"]), *args]
        while True:
            a = post_queue.run(cmd, stdin=stdin)
            headers, body = _split_include(a.stdout)
            att = post_queue.Attempt(a.code, body, a.stderr)
            if att.ok:
                self._doubling = 0.0
                if not body.strip():
                    return None
                try:
                    return _decode_concat(body) if paginate else json.loads(body)
                except ValueError:
                    raise StepError(f"gh api {' '.join(args)} の出力を読めない", EXIT_UNREADABLE)
            if not post_queue.is_rate_limited(att):
                raise StepError(f"gh api {' '.join(args)} が失敗: {att.summary()}")
            seconds, why = self._wait_for(headers)
            if len(self.waits) >= self.max_waits or seconds > self.max_wait:
                raise Partial(f"待ちが上限を超えた（{len(self.waits)} 回・次は {seconds:g} 秒）")
            self.waits.append({"number": target, "seconds": round(seconds, 1), "why": why})
            print(f"⏳ 上限のため {seconds:g} 秒待つ（{why}）: gh api {' '.join(args)}", file=sys.stderr)
            self.sleep(seconds)


def _repo(root, arg) -> str:
    if arg:
        return arg
    p = post_queue.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if not p.ok or not p.stdout.strip():
        raise StepError(f"リポジトリを決められない（--repo を渡す）: {p.summary()}", EXIT_PRECONDITION)
    return p.stdout.strip()


def _issues(gh: Gh, query: str) -> list[dict]:
    rows = gh.call([f"repos/{gh.repo}/issues?{query}&per_page=100"], paginate=True) or []
    return [r for r in rows if isinstance(r, dict) and not r.get("pull_request")]


class Milestones:
    def __init__(self, gh: Gh):
        self.gh, self._by_title = gh, None

    def number(self, title: str, target) -> int:
        if self._by_title is None:
            rows = self.gh.call([f"repos/{self.gh.repo}/milestones?state=all&per_page=100"], target=target, paginate=True) or []
            self._by_title = {r["title"]: r["number"] for r in rows}
        if title not in self._by_title:
            made = self.gh.call(
                [f"repos/{self.gh.repo}/milestones", "-X", "POST", "--input", "-"], stdin=json.dumps({"title": title}), target=target
            )
            self._by_title[title] = made["number"]
        return self._by_title[title]


def _with_labels(cur: dict, got, add=(), drop=None) -> dict:
    """ラベルの書き込みの応答（いまのラベルの一覧）を課題へ写す。応答が無ければ手元で足し引きする。"""
    if isinstance(got, list):
        labels = [lb if isinstance(lb, dict) else {"name": lb} for lb in got]
    else:
        labels = [lb for lb in cur.get("labels") or [] if lb.get("name") != drop] + [{"name": x} for x in add]
    return {**cur, "labels": labels}
