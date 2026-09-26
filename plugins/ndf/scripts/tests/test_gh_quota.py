"""2 つの枠の使い分けと上限の扱い（lib/gh_quota.py・#1142 の L0・不足 g）。待ちと時刻は差し替え、実際には待たない。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_fake import RATE, REST_RATE, fake, rest_out  # noqa: E402,F401
import gh_call  # noqa: E402
import gh_quota  # noqa: E402

Attempt = gh_quota.Attempt


def test_fallback_uses_the_other_quota_only_on_rate_limit():
    calls = []
    alt = lambda: calls.append("alt") or Attempt("b", "", "graphql")  # noqa: E731
    assert gh_quota.with_fallback(lambda: Attempt("a", "", "rest"), alt) == Attempt("a", "", "rest")
    assert gh_quota.with_fallback(lambda: Attempt(None, "HTTP 404", "rest"), alt).error == "HTTP 404"
    assert calls == []
    assert gh_quota.with_fallback(lambda: Attempt(None, REST_RATE, "rest"), alt) == Attempt("b", "", "graphql")
    assert calls == ["alt"]


def test_fallback_when_both_quotas_are_exhausted_keeps_both_reasons():
    a = gh_quota.with_fallback(lambda: Attempt(None, REST_RATE, "rest"), lambda: Attempt(None, RATE, "graphql"))
    assert a.limited and REST_RATE in a.error and RATE in a.error


def test_fallback_without_an_alternate_returns_the_limit():
    assert gh_quota.with_fallback(lambda: Attempt(None, RATE, "graphql")).limited


def test_wait_for_reset_sleeps_until_the_reset_then_retries_once():
    tries, slept = [Attempt(None, RATE, "graphql"), Attempt("ok", "", "graphql")], []
    a = gh_quota.wait_for_reset(lambda: tries.pop(0), "graphql", sleep=slept.append, now=lambda: 1000.0,
                                limits=lambda: {"graphql": {"remaining": 0, "reset": 1300}})
    assert a.value == "ok" and slept == [301.0]


def test_wait_for_reset_without_a_readable_reset_waits_60_and_is_capped():
    slept = []
    gh_quota.wait_for_reset(lambda: Attempt(None, RATE), sleep=slept.append, now=lambda: 0.0, limits=lambda: None)
    gh_quota.wait_for_reset(lambda: Attempt(None, RATE), sleep=slept.append, now=lambda: 0.0,
                            limits=lambda: {"graphql": {"remaining": 0, "reset": 10 ** 9}})
    assert slept == [60.0, float(gh_quota.MAX_WAIT)]


def test_wait_for_reset_does_not_wait_on_other_failures():
    slept = []
    a = gh_quota.wait_for_reset(lambda: Attempt(None, "HTTP 404"), sleep=slept.append)
    assert a.error == "HTTP 404" and slept == []


def test_rate_limits_reads_both_quotas(fake):
    fake.on("api", "-i", "rate_limit", out=rest_out({"resources": {
        "graphql": {"remaining": 0, "reset": 1700}, "core": {"remaining": 4700, "reset": 1800}}}))
    assert gh_quota.rate_limits() == {"graphql": {"remaining": 0, "reset": 1700},
                                      "core": {"remaining": 4700, "reset": 1800}}


def test_rate_limits_unreadable_is_none(fake):
    fake.on("api", rc=1, err="HTTP 500")
    assert gh_quota.rate_limits() is None


def test_poll_interval_grows_to_the_cap_and_shrinks_on_change():
    iv = gh_quota.PollInterval()
    assert [iv.next(False) for _ in range(4)] == [20, 40, 60, 60]
    assert iv.next(True) == 10


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def resp(body, not_modified=False, error=""):
    return gh_call.RestResponse({}, body, None, None, 304 if not_modified else 200, not_modified, error)


def test_poll_until_backs_off_while_nothing_changes_and_stops_when_done():
    reads = [resp({"s": "pending"}), resp({"s": "pending"}, True), resp({"s": "pending"}, True),
             resp({"s": "done"})]
    clock = Clock()
    body, reached = gh_quota.poll_until("p", lambda b: b["s"] == "done", timeout=600, sleep=clock.sleep,
                                        now=clock.now, read=lambda path: reads.pop(0))
    assert reached and body == {"s": "done"}
    assert clock.slept == [10, 20, 40]


def test_poll_until_gives_up_at_the_timeout():
    clock = Clock()
    body, reached = gh_quota.poll_until("p", lambda b: False, timeout=35, sleep=clock.sleep, now=clock.now,
                                        read=lambda path: resp({"s": "pending"}, True))
    assert not reached and body == {"s": "pending"} and clock.slept == [20]


def test_poll_until_uses_etag_reads_that_do_not_count(fake):
    """既定の読みは gh_call.rest_cached（ETag 付き）。2 回目からは If-None-Match が付く。"""
    outs = [(0, rest_out({"s": "pending"}, etag='"e"'), ""), (1, rest_out(None, "304 Not Modified"), "gh: HTTP 304"),
            (0, rest_out({"s": "done"}, etag='"f"'), "")]
    fake.on_fn("api", fn=lambda args, stdin: gh_call.GhResult(*outs.pop(0)))
    clock = Clock()
    body, reached = gh_quota.poll_until("repos/o/r/pulls/1", lambda b: b["s"] == "done", timeout=600,
                                        sleep=clock.sleep, now=clock.now)
    assert reached and body == {"s": "done"}
    assert [a[1] == "-H" for a, _ in fake.calls] == [False, True, True]
