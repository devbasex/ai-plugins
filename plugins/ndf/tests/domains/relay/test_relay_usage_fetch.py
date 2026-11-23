"""中継のアカウントの使用率の API の取得の記録・取得の排他・取り直しの判定・取得の本体とトークンの更新（#3864 の決定 4、I16）。

取得の本体（`fetch_usage`・`_refresh`）は、偽の API（`httpx.MockTransport`）と一時ディレクトリの登録簿で確かめる。
トークンの値はすべて偽の値で、実のアカウントとネットワークを使わない。
"""

from __future__ import annotations

import json
import math
import os
import stat
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ndf.adapters import claude_usage
from ndf.domains.relay import account_dir as ad
from ndf.domains.relay import account_registry as reg
from ndf.domains.relay import accounts
from ndf.domains.relay.account_decide import Usage

NOW = 1_795_000_000.0
LATER = NOW + 3600
WEEK = NOW + 5 * 86400
INTERVAL = 300.0


def _fetch_record(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _got(**kw) -> claude_usage.Usage:
    return claude_usage.Usage(fetched_at=NOW, **kw)


def test_decision4_fetch_record_lives_in_the_store(tmp_path):
    store = tmp_path / "store"
    path = accounts.fetch_path(store, "work2", "g2")
    assert path == store / ".usage-fetch" / "work2" / "g2.json"
    assert accounts.fetch_path(store, "work2", "") == path.with_name("_.json")
    accounts.write_fetch(path, _got(five_hour=(40.0, LATER), seven_day=(20.0, WEEK), scoped=[("opus", 95.0, WEEK)]), NOW)
    rec = _fetch_record(path)
    assert rec["error"] is None and rec["fetched_at"] == NOW
    assert rec["windows"]["seven_day_scoped"] == {"opus": {"percent": 95.0, "resets_at": WEEK}}
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    # モデル別の週の窓 95 を使用率に読む
    assert accounts.usages(store, NOW, {"work2": "g2"}) == [Usage("work2", 95.0, 40.0, 95.0, WEEK, False)]
    # 旧い世代の取得の記録は読まない
    assert accounts.usages(store, NOW, {"work2": "g3"}) == []


def test_decision4_write_fetch_replaces_the_windows_without_merging(tmp_path):
    path = accounts.fetch_path(tmp_path, "work1", "g1")
    thursday, friday = NOW + 4 * 86400, NOW + 5 * 86400
    accounts.write_fetch(path, _got(five_hour=(10.0, LATER), scoped=[("a", 95.0, friday)]), NOW)
    # 次の応答の窓で丸ごと置き換える。応答に無い 5 時間の窓と識別 A の窓は残らない
    accounts.write_fetch(path, _got(scoped=[("b", 100.0, thursday)]), NOW + 60)
    rec = _fetch_record(path)
    assert rec["windows"] == {"seven_day_scoped": {"b": {"percent": 100.0, "resets_at": thursday}}} and rec["fetched_at"] == NOW + 60
    assert accounts.usages(tmp_path, NOW, {"work1": "g1"}) == [Usage("work1", 100.0, None, 100.0, None, True)]
    # resets_at が早まった・使用率が下がった応答もそのまま採る。崩れたモデル別の窓（None）は載せない
    accounts.write_fetch(path, _got(five_hour=(3.0, LATER - 0.6), scoped=None), NOW + 120)
    assert _fetch_record(path)["windows"] == {"five_hour": {"percent": 3.0, "resets_at": LATER - 0.6}}
    assert accounts.usages(tmp_path, NOW + 120, {"work1": "g1"}) == [Usage("work1", 3.0, 3.0, None, None, False)]


def test_decision4_spend_limit_follows_the_last_response(tmp_path):
    store = tmp_path / "store"
    path = accounts.fetch_path(store, "work2", "g2")
    # 最初の応答が窓を持たず spend_limit_reached が真なら、窓もキャッシュも無くても上限の行で返る
    accounts.write_fetch(path, _got(spend_limit_reached=True), NOW)
    assert accounts.usages(store, NOW, {"work2": "g2"}) == [Usage("work2", None, None, None, None, True)]
    # 最後の応答がすべてを決める。応答に無い（None）なら前の真を残さない
    accounts.write_fetch(path, _got(five_hour=(10.0, LATER)), NOW)
    assert accounts.usages(store, NOW, {"work2": "g2"}) == [Usage("work2", 10.0, 10.0, None, None, False)]


def test_decision4_usages_reads_the_fetch_record_once_per_account(tmp_path, monkeypatch):
    store = tmp_path / "store"
    path = accounts.fetch_path(store, "work2", "g2")
    accounts.write_fetch(path, _got(five_hour=(10.0, LATER), spend_limit_reached=True), NOW)
    real, reads = accounts._read_json, []

    def replaced_after_first_read(p):  # 1 度読んだ直後に、書き手が記録を「上限は解けた・使用率 100」へ置き換える
        got = real(p)
        if p == path:
            reads.append(p)
            if len(reads) == 1:
                monkeypatch.setattr(accounts, "_read_json", real)  # 書き手の読みは数えない
                accounts.write_fetch(path, _got(five_hour=(100.0, LATER + 1), spend_limit_reached=False), NOW)
                monkeypatch.setattr(accounts, "_read_json", replaced_after_first_read)
        return got

    monkeypatch.setattr(accounts, "_read_json", replaced_after_first_read)
    # 同じ 1 回の内容から窓と上限の到達を取る（差し替え前の 10% と差し替え後の上限なしを組み合わせない）
    assert accounts.usages(store, NOW, {"work2": "g2"}) == [Usage("work2", 10.0, 10.0, None, None, True)] and len(reads) == 1


def test_decision4_write_fetch_keeps_the_attempt_time_and_clears_the_error(tmp_path):
    path = accounts.fetch_path(tmp_path, "work1", "g1")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"at": NOW - 5, "error": "network", "spend_limit_reached": None, "windows": {}}), encoding="utf-8")
    accounts.write_fetch(path, _got(five_hour=(10.0, LATER)), NOW)
    rec = _fetch_record(path)
    assert rec["at"] == NOW - 5 and rec["fetched_at"] == NOW and rec["error"] is None and rec["spend_limit_reached"] is None


def _attempted(store, at, windows=None):
    path = accounts.fetch_path(store, "work1", "g1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": at, "error": None, "spend_limit_reached": None, "windows": windows or {}}), encoding="utf-8")
    return path


def test_i16_due_once_per_interval(tmp_path):
    fetch = accounts.fetch_path(tmp_path, "work1", "g1")
    assert accounts.due_at(fetch, INTERVAL, None) == -math.inf  # 試したことが無い
    _attempted(tmp_path, NOW)
    assert accounts.due_at(fetch, INTERVAL, None) == NOW + INTERVAL  # 別のラッパー（tried を持たない）も記録の at で数える
    assert accounts.due_at(fetch, INTERVAL, NOW + 10) == NOW + 10 + INTERVAL  # このラッパーがキューへ入れた時刻
    assert accounts.due_at(fetch, INTERVAL, NOW - 10) == NOW + INTERVAL  # 大きいほうの時刻から数える
    fetch.write_text("{", encoding="utf-8")  # 読めない取得の記録は at が無い
    assert accounts.due_at(fetch, INTERVAL, None) == -math.inf


def test_i16_due_once_per_reset_after_the_last_attempt(tmp_path):
    fetch = _attempted(tmp_path, NOW, {"five_hour": {"percent": 100.0, "resets_at": NOW + 100}})
    assert accounts.due_at(fetch, INTERVAL, None) == NOW + 100  # 最後に試した後の最初のリセット
    # そのリセットで試した後は、取得が失敗して過ぎた窓が残っても間隔まで待つ
    _attempted(tmp_path, NOW + 120, {"five_hour": {"percent": 100.0, "resets_at": NOW + 100}})
    assert accounts.due_at(fetch, INTERVAL, None) == NOW + 120 + INTERVAL
    # モデル別の窓のリセットでも取り直す。tried がそのリセットより後なら間隔まで待つ
    _attempted(tmp_path, NOW, {"seven_day_scoped": {"opus": {"percent": 90.0, "resets_at": NOW + 100}}})
    assert accounts.due_at(fetch, INTERVAL, None) == NOW + 100
    assert accounts.due_at(fetch, INTERVAL, NOW + 120) == NOW + 120 + INTERVAL


def test_i16_fetch_lock_is_one_per_store_and_name_and_never_taken_from_a_live_owner(tmp_path, hold_lock):
    store = tmp_path / "store"  # 旧い登録簿（.fetch-locks も .usage-fetch も無い）でも親を作って通る
    lock = accounts.fetch_lock_path(store, "work1")
    assert lock == store / ".fetch-locks" / "work1.lock"
    with accounts.fetch_lock(store, "work1", 0) as got:
        assert got
        assert stat.S_IMODE(lock.parent.stat().st_mode) == 0o700
        with accounts.fetch_lock(store, "work1", 0) as again:  # 同じ名前の 2 つ目の取得は待たずに諦める
            assert not again
        with accounts.fetch_lock(store, "work2", 0) as other:
            assert other
    fd = hold_lock(lock)
    os.utime(lock, (NOW - 86400, NOW - 86400))  # 古いファイルでも、オーナーが生きている間は奪わない
    with accounts.fetch_lock(store, "work1", 0) as got:
        assert not got
    os.close(fd)  # オーナーが死ねば取れる
    with accounts.fetch_lock(store, "work1", 0) as got:
        assert got


def test_i16_forget_drops_the_fetch_record_of_the_generation_but_not_the_lock(tmp_path):
    store, state = tmp_path / "store", tmp_path / "state"
    for g in ("old", "new"):
        accounts.write_fetch(accounts.fetch_path(store, "work1", g), _got(five_hour=(10.0, LATER)), NOW)
    with accounts.fetch_lock(store, "work1", 0):
        pass
    accounts.forget(state, store, "work1", "old")
    assert sorted(p.name for p in accounts.fetch_path(store, "work1", "new").parent.iterdir()) == ["new.json"]
    accounts.forget(state, store, "work1", "new")
    assert not accounts.fetch_path(store, "work1", "new").parent.exists()
    assert accounts.fetch_lock_path(store, "work1").is_file()


# ---------------------------------------------------------------- 取得の本体とトークンの更新（`fetch_usage`・`_refresh`）

ACCESS = "fake-access-u5b2-fetch"
REFRESH = "fake-refresh-u5b2-fetch"
NEW_ACCESS = "fake-new-access-u5b2-fetch"
NEW_REFRESH = "fake-new-refresh-u5b2-fetch"
OTHER_ACCESS = "fake-other-access-u5b2-fetch"
OTHER_REFRESH = "fake-other-refresh-u5b2-fetch"
SHARED_ACCESS = "fake-shared-access-u5b2-fetch"
TOKENS = (ACCESS, REFRESH, NEW_ACCESS, NEW_REFRESH, OTHER_ACCESS, OTHER_REFRESH, SHARED_ACCESS)
SCOPES = ["user:inference", "user:profile"]


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _usage_body(five_resets: float = LATER) -> dict:
    return {
        "five_hour": {"utilization": 40, "resets_at": _iso(five_resets)},
        "seven_day": {"utilization": 20, "resets_at": _iso(WEEK)},
        "limits": [{"kind": "weekly_scoped", "scope": {"model": {"id": "opus"}}, "percent": 95, "resets_at": _iso(WEEK)}],
        "extra_usage": {"spend_limit_reached": True},
    }


TOKEN_BODY = {"access_token": NEW_ACCESS, "expires_in": 3600, "refresh_token": NEW_REFRESH}


def _oauth(access: str, refresh: str, expires: float, scopes=SCOPES) -> dict:
    o = {"accessToken": access, "refreshToken": refresh, "expiresAt": int(expires * 1000)}
    if scopes is not None:
        o["scopes"] = list(scopes)
    return o


def _write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".test.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)  # 置き換えで書く（認証ファイルの実体が替わる）
    return path


class _Clock:
    """テストが進める時計（排他の中の時刻はすべてこれで読む）。"""

    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class _Api:
    """偽の API。使用率の GET とトークンの更新の POST を数え、並べた応答を順に返す（最後の応答を繰り返す）。"""

    def __init__(self, usage=None, token=None, on_post=None) -> None:
        self.usage = list(usage or [(200, _usage_body())])
        self.token = list(token or [(200, TOKEN_BODY)])
        self.on_post = on_post
        self.gets: list[str] = []
        self.posts: list[dict] = []

    @staticmethod
    def _next(queue: list):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and url == claude_usage.USAGE_URL:
            self.gets.append(request.headers.get("authorization", ""))
            status, body = self._next(self.usage)
        elif request.method == "POST" and url == claude_usage.TOKEN_URL:
            self.posts.append(json.loads(request.content))
            if self.on_post:
                self.on_post()
            status, body = self._next(self.token)
        else:
            return httpx.Response(404)
        return httpx.Response(status, json=body)


@pytest.fixture
def world(tmp_path, monkeypatch) -> dict:
    """共有の設定ディレクトリの下の登録簿に、動いていない登録済みアカウント `work2`（世代 g2）を 1 つ置く。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    shared = tmp_path / "shared"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(shared))
    monkeypatch.delenv("NDF_SHARED_CONFIG_DIR", raising=False)
    monkeypatch.delenv("NDF_ACCOUNTS_DIR", raising=False)
    _write_json(shared / ".claude.json", {"projects": {}})
    _write_json(shared / reg.CRED_FILE, {"claudeAiOauth": _oauth(SHARED_ACCESS, "fake-shared-refresh", LATER)})
    store = shared / "ndf" / "accounts"
    acc = store / "work2"
    _write_json(acc / reg.ACCOUNT_FILE, {"name": "work2", "email": "work2@example.com", "needs_relogin": False, "generation": "g2"})
    _write_json(acc / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": "work2@example.com"}})
    w = {"store": store, "acc": acc, "state": tmp_path / "state", "shared": shared, "auth": acc / reg.CRED_FILE}
    _set_auth(w, _oauth(ACCESS, REFRESH, LATER))
    return w


def _set_auth(w: dict, oauth: dict) -> None:
    _write_json(w["auth"], {"claudeAiOauth": oauth, "mcpOAuth": {"server": {"kept": True}}})


def _auth(w: dict) -> dict:
    return json.loads(w["auth"].read_text(encoding="utf-8"))


def _fetch(w: dict, api: _Api, clock: _Clock, gate=None, force=False):
    target = accounts.FetchTarget(w["store"], "work2", "g2")
    options = accounts.FetchOptions(httpx.MockTransport(api), clock)
    return accounts.fetch_usage(target, clock(), force, gate or accounts.FetchGate(), options)


def _record(w: dict) -> dict:
    return _fetch_record(accounts.fetch_path(w["store"], "work2", "g2"))


def _no_token_in(*texts: str) -> None:
    for text in texts:
        for token in TOKENS:
            assert token not in text


def test_decision4_fetch_usage_takes_an_idle_account_into_the_store_record(world):
    clock, api = _Clock(), _Api()
    got = _fetch(world, api, clock)
    assert got is not None and got.error is None
    assert api.gets == [f"Bearer {ACCESS}"] and api.posts == []
    rec = _record(world)
    assert rec["at"] == NOW and rec["fetched_at"] == NOW and rec["error"] is None and rec["spend_limit_reached"] is True
    assert rec["windows"]["five_hour"] == {"percent": 40.0, "resets_at": LATER}
    assert not world["state"].exists()  # 状態の保存先には書かない
    # 記録を読み（モデル別の週の 95 と上限の到達）、300 秒の中で API を呼ばない
    assert accounts.usages(world["store"], NOW, {"work2": "g2"}) == [Usage("work2", 95.0, 40.0, 95.0, WEEK, True)]
    clock.t = NOW + 10
    assert _fetch(world, api, clock) is None
    assert len(api.gets) == 1


def test_decision4_forced_fetch_does_not_wait_for_the_interval(world):
    full = {**_usage_body(), "five_hour": {"utilization": 100, "resets_at": _iso(LATER - 0.6)}}
    clock, api = _Clock(), _Api(usage=[(200, _usage_body()), (200, full)])
    assert _fetch(world, api, clock).error is None
    clock.t = NOW + 10  # 間隔の中でも、上限を見た直後の取り直しは呼ぶ
    got = _fetch(world, api, clock, force=True)
    assert got is not None and got.error is None and len(api.gets) == 2
    rec = _record(world)
    assert rec["fetched_at"] == NOW + 10 and rec["windows"]["five_hour"]["percent"] == 100.0
    assert accounts.usages(world["store"], NOW + 10, {"work2": "g2"})[0].five == 100.0


def test_decision4_fetch_usage_retries_once_per_reset_inside_the_interval(world):
    clock, api = _Clock(), _Api(usage=[(200, _usage_body(NOW + 100)), (500, {})])
    assert _fetch(world, api, clock).error is None
    clock.t = NOW + 101  # 最後に試した後に 5 時間の窓のリセットを過ぎた
    assert _fetch(world, api, clock).error == "http-500"
    clock.t = NOW + 150  # 同じリセットで試した後は、失敗して過ぎた窓が残っても呼ばない
    assert _fetch(world, api, clock) is None
    assert len(api.gets) == 2


def test_decision4_failure_keeps_windows_and_writes_only_the_error(world):
    clock = _Clock()
    assert _fetch(world, _Api(), clock).error is None
    before = _record(world)["windows"]
    for at, status, body, error in (
        (NOW + 301, 500, {}, "http-500"),
        (NOW + 602, 200, {"five_hour": {"utilization": 5}, "seven_day": None}, "shape"),  # resets_at が欠けた窓
    ):
        clock.t = at
        assert _fetch(world, _Api(usage=[(status, body)]), clock).error == error
        rec = _record(world)
        assert rec["windows"] == before and rec["error"] == error and rec["at"] == at and rec["spend_limit_reached"] is True


def test_decision4_reregistered_after_queueing_reads_no_credentials_and_writes_nothing(world):
    clock, api = _Clock(), _Api()
    _write_json(world["acc"] / reg.ACCOUNT_FILE, {"name": "work2", "email": "work2@example.com", "generation": "g3"})
    got = _fetch(world, api, clock)
    assert got is not None and got.error == "reregistered"
    assert api.gets == [] and api.posts == []
    assert not (world["store"] / ".usage-fetch").exists()


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_decision4_auth_pointing_elsewhere_is_not_account_local(world, link):
    clock, api = _Clock(), _Api()
    shared_auth = world["shared"] / reg.CRED_FILE
    before = shared_auth.read_bytes()
    world["auth"].unlink()
    (os.symlink if link == "symlink" else os.link)(shared_auth, world["auth"])
    got = _fetch(world, api, clock)
    assert got is not None and got.error == "not_account_local"
    assert api.gets == [] and api.posts == []
    assert shared_auth.read_bytes() == before and _record(world)["error"] == "not_account_local"


def test_i16_expired_token_is_refreshed_and_only_claude_ai_oauth_changes(world, capsys):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    clock, api = _Clock(), _Api()
    got = _fetch(world, api, clock)
    assert got is not None and got.error is None
    assert len(api.posts) == 1 and api.posts[0]["refresh_token"] == REFRESH and api.posts[0]["scope"] == " ".join(SCOPES)
    assert api.gets == [f"Bearer {NEW_ACCESS}"]
    body = _auth(world)
    assert body["mcpOAuth"] == {"server": {"kept": True}}
    o = body["claudeAiOauth"]
    assert (o["accessToken"], o["refreshToken"], o["expiresAt"], o["scopes"]) == (NEW_ACCESS, NEW_REFRESH, int((NOW + 3600) * 1000), SCOPES)
    assert not (world["acc"] / ad.REFRESH_LOCK).exists()
    # 画面・返り・取得の記録にトークンの値が出ない（C1・C2）
    out = capsys.readouterr()
    _no_token_in(out.out, out.err, repr(got), accounts.fetch_path(world["store"], "work2", "g2").read_text(encoding="utf-8"))


def test_i16_refresh_is_skipped_while_the_account_is_in_use(world):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    with reg.locked(world["store"], "work2"):
        ad._new_lease(world["store"], "work2", "g2")  # このプロセスの届け（生きている）
    before = world["auth"].read_bytes()
    clock, api = _Clock(), _Api()
    assert _fetch(world, api, clock).error == "token"  # 元のトークンは失効しているので送らない
    assert api.posts == [] and api.gets == [] and world["auth"].read_bytes() == before


def test_i16_refresh_never_waits_for_or_takes_a_held_refresh_lock(world):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    (world["acc"] / ad.REFRESH_LOCK).mkdir()  # 動いている claude が更新の途中
    clock, api = _Clock(), _Api()
    started = time.monotonic()
    assert _fetch(world, api, clock).error == "token"
    assert time.monotonic() - started < 2
    assert api.posts == [] and api.gets == [] and (world["acc"] / ad.REFRESH_LOCK).is_dir()
    assert _auth(world)["claudeAiOauth"]["accessToken"] == ACCESS


def test_i16_refresh_uses_a_live_token_found_on_reread_without_calling_the_endpoint(world, monkeypatch):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    real = ad.in_use

    def logged_in_meanwhile(store, name):  # 取得の (2) の後・更新の (1) の読み直しの前に、claude が更新を書いた
        monkeypatch.setattr(ad, "in_use", real)
        _set_auth(world, _oauth(OTHER_ACCESS, OTHER_REFRESH, LATER))
        return real(store, name)

    monkeypatch.setattr(ad, "in_use", logged_in_meanwhile)
    clock, api = _Clock(), _Api()
    assert _fetch(world, api, clock).error is None
    assert api.posts == [] and api.gets == [f"Bearer {OTHER_ACCESS}"]
    assert _auth(world)["claudeAiOauth"]["accessToken"] == OTHER_ACCESS


@pytest.mark.parametrize(("status", "relogin"), [(400, True), (403, True), (429, False), (500, False)])
def test_i16_revoked_refresh_marks_relogin_and_a_transient_failure_does_not(world, status, relogin):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    before = world["auth"].read_bytes()
    clock, api = _Clock(), _Api(token=[(status, {})])
    assert _fetch(world, api, clock).error == "token"  # 期限切れのまま GET を送らない
    assert len(api.posts) == 1 and api.gets == []
    assert reg.load(world["store"], "work2").needs_relogin is relogin
    assert world["auth"].read_bytes() == before


def test_i16_401_refreshes_once_and_retries_the_get_once(world):
    clock, api = _Clock(), _Api(usage=[(401, {}), (200, _usage_body())])
    assert _fetch(world, api, clock).error is None
    assert len(api.posts) == 1 and api.gets == [f"Bearer {ACCESS}", f"Bearer {NEW_ACCESS}"]
    clock.t = NOW + 301
    _set_auth(world, _oauth(ACCESS, REFRESH, LATER))
    api = _Api(usage=[(401, {})])  # 打ち直しの 401 でも更新しない
    assert _fetch(world, api, clock).error == "http-401"
    assert len(api.posts) == 1 and len(api.gets) == 2


def test_i16_401_after_a_near_expiry_refresh_does_not_refresh_again(world):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW + 30))  # 残りが relay.token-margin（60 秒）以下
    clock, api = _Clock(), _Api(usage=[(401, {})])
    assert _fetch(world, api, clock).error == "http-401"
    assert len(api.posts) == 1 and api.gets == [f"Bearer {NEW_ACCESS}"]


def test_i16_401_with_a_failed_refresh_does_not_retry(world):
    clock, api = _Clock(), _Api(usage=[(401, {})], token=[(500, {})])
    assert _fetch(world, api, clock).error == "http-401"
    assert len(api.posts) == 1 and len(api.gets) == 1


def test_i16_near_expiry_refresh_failure_still_sends_the_live_original_token(world):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW + 30))
    clock, api = _Clock(), _Api(token=[(500, {})])
    assert _fetch(world, api, clock).error is None
    assert len(api.posts) == 1 and api.gets == [f"Bearer {ACCESS}"]


def test_i16_original_token_that_expires_while_refreshing_is_not_sent(world):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW + 1))
    clock = _Clock()

    def slow() -> None:  # 更新の POST が 3 秒待った末に失敗する
        clock.t += 3

    api = _Api(token=[(500, {})], on_post=slow)
    assert _fetch(world, api, clock).error == "token"
    assert len(api.posts) == 1 and api.gets == []


def test_i16_token_without_the_usage_scope_is_not_sent(world):
    clock = _Clock()
    _set_auth(world, _oauth(ACCESS, REFRESH, LATER, scopes=["user:inference"]))
    api = _Api()
    assert _fetch(world, api, clock).error == "scope"
    assert api.gets == []
    # scopes の無い記録は持つとみなして送る
    clock.t = NOW + 301
    _set_auth(world, _oauth(ACCESS, REFRESH, LATER, scopes=None))
    assert _fetch(world, api, clock).error is None and api.gets == [f"Bearer {ACCESS}"]
    # 更新の応答の scope で替わった scopes にも当てる
    clock.t = NOW + 602
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    api = _Api(token=[(200, {**TOKEN_BODY, "scope": "user:inference"})])
    assert _fetch(world, api, clock).error == "scope"
    assert len(api.posts) == 1 and api.gets == []


def test_i16_closed_gate_sends_no_get_and_no_refresh(world):
    gate = accounts.FetchGate()
    gate.close()
    clock, api = _Clock(), _Api()
    assert _fetch(world, api, clock, gate=gate) is None
    clock.t = NOW + 301
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    assert _fetch(world, api, clock, gate=gate) is None
    assert api.gets == [] and api.posts == []
    assert _record(world)["error"] is None  # 停止で送らなかった回は理由を書かない


@pytest.mark.parametrize("status", [200, 400])
def test_i16_auth_replaced_during_the_post_is_kept_and_its_live_token_is_used(world, status):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))

    def relogin() -> None:  # POST の間に利用者が別のログインで認証ファイルを置き換えた
        _set_auth(world, _oauth(OTHER_ACCESS, OTHER_REFRESH, LATER))

    clock, api = _Clock(), _Api(token=[(status, TOKEN_BODY if status == 200 else {})], on_post=relogin)
    assert _fetch(world, api, clock).error is None
    assert api.gets == [f"Bearer {OTHER_ACCESS}"]
    assert _auth(world)["claudeAiOauth"]["accessToken"] == OTHER_ACCESS
    assert reg.load(world["store"], "work2").needs_relogin is False


def test_i16_refreshed_token_goes_to_pending_when_the_registry_lock_is_busy(world, monkeypatch):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    ino = world["auth"].stat().st_ino
    real = reg.locked

    @contextmanager
    def busy(store, name, wait=None):
        raise reg.AccountLockTimeout("held by the test")
        yield

    clock, api = _Clock(), _Api(on_post=lambda: monkeypatch.setattr(reg, "locked", busy))
    assert _fetch(world, api, clock).error is None
    assert api.gets == [f"Bearer {NEW_ACCESS}"]  # この取得の GET は新しいトークンで送る
    assert world["auth"].stat().st_ino == ino and _auth(world)["claudeAiOauth"]["accessToken"] == ACCESS
    pending = json.loads((world["acc"] / reg.PENDING_FILE).read_text(encoding="utf-8"))
    assert pending["generation"] == "g2" and pending["claudeAiOauth"]["accessToken"] == NEW_ACCESS
    monkeypatch.setattr(reg, "locked", real)
    local = ad.check_local(world["store"], world["acc"], world["shared"] / ".claude.json")
    assert ad.apply_pending("g2", local) == "written"
    assert _auth(world)["claudeAiOauth"]["accessToken"] == NEW_ACCESS


def test_i16_refreshed_token_goes_to_pending_when_writing_the_auth_file_fails(world, monkeypatch):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    real = ad.replace_auth
    monkeypatch.setattr(ad, "replace_auth", lambda *a: False)  # 容量不足・一時的な I/O の失敗（旧い組のまま）
    clock, api = _Clock(), _Api()
    assert _fetch(world, api, clock).error is None
    assert api.gets == [f"Bearer {NEW_ACCESS}"]
    assert _auth(world)["claudeAiOauth"]["accessToken"] == ACCESS
    pending = json.loads((world["acc"] / reg.PENDING_FILE).read_text(encoding="utf-8"))
    assert pending["claudeAiOauth"]["refreshToken"] == NEW_REFRESH  # 更新先が回した新しいリフレッシュトークンを捨てない
    monkeypatch.setattr(ad, "replace_auth", real)
    local = ad.check_local(world["store"], world["acc"], world["shared"] / ".claude.json")
    assert ad.apply_pending("g2", local) == "written"
    assert _auth(world)["claudeAiOauth"]["refreshToken"] == NEW_REFRESH


def _auth_tmps(w: dict) -> list[str]:
    return sorted(p.name for p in w["acc"].iterdir() if p.name.startswith(reg.CRED_FILE + ".ndf-"))


@pytest.mark.parametrize("target", ["auth", "pending"])
def test_i16_a_failed_replace_removes_the_temporary_copy_of_the_credentials(world, monkeypatch, target):
    local = ad.check_local(world["store"], world["acc"], world["shared"] / ".claude.json")
    body, st = ad.read_auth(local)
    real = os.replace

    def fail(src, dst, **kw):
        if isinstance(src, str) and f".ndf-{target}." in src:
            raise OSError(5, "Input/output error")
        return real(src, dst, **kw)

    monkeypatch.setattr(os, "replace", fail)
    oauth = _oauth(NEW_ACCESS, NEW_REFRESH, LATER)
    if target == "auth":
        assert ad.replace_auth(local, st, oauth, body) is False
    else:
        assert ad.write_pending(local, "g2", (ACCESS, REFRESH), oauth) is False
    assert _auth_tmps(world) == []  # 新しいトークンを持つ一時ファイルを残さない


def test_i16_refresh_whose_token_cannot_be_saved_anywhere_is_not_a_success(world, monkeypatch):
    _set_auth(world, _oauth(ACCESS, REFRESH, NOW - 10))
    monkeypatch.setattr(ad, "replace_auth", lambda *a: False)  # 容量不足で認証ファイルも保留も書けない
    real = ad.account_files._put

    def full(d, what, data):
        if what == "pending":
            raise OSError(28, "No space left on device")
        return real(d, what, data)

    monkeypatch.setattr(ad.account_files, "_put", full)
    clock, api = _Clock(), _Api()
    assert _fetch(world, api, clock).error == "unsaved"
    assert api.gets == [] and _record(world)["error"] == "unsaved"


def test_i16_attempt_time_is_recorded_right_before_the_get_is_allowed(world, monkeypatch):
    clock, api = _Clock(), _Api()
    real = claude_usage.get_usage

    def slow(*a, **kw):  # HTTP クライアントの準備の間に時計が進む
        clock.t = NOW + 100
        return real(*a, **kw)

    monkeypatch.setattr(claude_usage, "get_usage", slow)
    assert _fetch(world, api, clock).error is None
    assert _record(world)["at"] == NOW + 100


def test_i16_get_is_not_sent_when_the_attempt_time_cannot_be_recorded(world, monkeypatch):
    clock, api = _Clock(), _Api()
    real, calls = accounts._mark, []

    def fails_at_send(path, **changes):
        calls.append(changes)
        return real(path, **changes) if len(calls) == 1 else False

    monkeypatch.setattr(accounts, "_mark", fails_at_send)
    assert _fetch(world, api, clock) is None
    assert api.gets == []
