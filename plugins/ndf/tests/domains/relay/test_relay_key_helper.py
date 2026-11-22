"""API キーの helper の払い出しと記録（U11a。#3604 #3864 の受け入れ条件 #3864 の 9・決定 12・I9）。

- `ndf relay account api-key-helper metered <指紋>` は、環境変数の設定が API キーの設定で、キーの SHA-256 が指紋と同じときだけ
  キーを出す（別冊の「環境変数の設定の API キー」）
- `--record`・`--id` のある起動は、記録の錠の中で払い出しの上限（60 秒に 3 回・拒んだ後と拒否のマークの後は 0 回）を照らし、
  1 行を足してからキーを出す。錠・追記の失敗は失敗のマークに残す（別冊の「キーの子の認証の失敗の停止」）
値はすべて偽の値で、実のキーを使わない。
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time

import pytest

from ndf.domains.relay import cli, key_helper

FAKE_KEY = "sk-ant-api03-fake-test-key-not-real"
FP = key_helper.fingerprint(FAKE_KEY)


def _dir(tmp_path):
    d = tmp_path / "relay"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o700)
    return d


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _paid(path) -> list[dict]:
    """記録の見出しの行を除いた行。"""
    return _rows(path)[1:]


def at(t: float):
    return lambda: t


def _pin(tmp_path):
    """確かめた設定への固定のファイル（比べる設定ファイルを持たない形。確かめはいつも通る）。"""
    path = tmp_path / "t.pin.json"
    key_helper.write_pin(path, {}, {})
    return path


def _helper(capsys, *extra, pin=None) -> tuple[int, str, str]:
    code = cli.main(["account", "api-key-helper", "metered", *extra, *(["--pin", str(pin)] if pin else [])])
    got = capsys.readouterr()
    return code, got.out, got.err


@pytest.mark.parametrize(
    "decl, out",
    [
        (f"ANTHROPIC_API_KEY={FAKE_KEY}", FAKE_KEY),
        (f"ANTHROPIC_API_KEY={FAKE_KEY} ANTHROPIC_BASE_URL=https://gw.example.com", FAKE_KEY),
        ("ANTHROPIC_API_KEY=sk-ant-api03-fake-other-key", None),  # 指紋が違う
        (f"CLAUDE_CODE_USE_BEDROCK=1 ANTHROPIC_API_KEY={FAKE_KEY}", None),  # API キーの設定でない
        ("ANTHROPIC_API_KEY='unclosed", None),  # 解けない
        (None, None),  # 設定が無い
    ],
    ids=["match", "with-base-url", "other-key", "provider-mixed", "broken", "absent"],
)
def test_3864_9_metered_helper_gives_only_the_fingerprinted_key(tmp_path, capsys, monkeypatch, decl, out):
    # 別冊の単体テストの観点の `_acc_key_helper`（metered）: 合うときだけキーの値だけを出し、ほかは何も出さずに 1
    if decl is None:
        monkeypatch.delenv("NDF_SUPERVISE_CLAUDE_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", decl)
    code, stdout, stderr = _helper(capsys, FP, pin=_pin(tmp_path))
    assert (code, stdout) == ((0, FAKE_KEY) if out else (1, ""))
    assert FAKE_KEY not in stderr and FP not in stderr


def test_d12_helper_payout_is_three_per_minute_and_never_after_a_refusal(tmp_path):
    # 別冊の「払い出しの上限」: 0.1 秒ごとの呼び出しで 3 回出し、4 回目から出さず、61 秒後も出さない
    rec = key_helper.create(_dir(tmp_path))
    got = [key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0 + i / 10)) for i in range(5)]
    assert got == [True, True, True, False, False]
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1061.0)) is False
    rows = _paid(rec.path)
    assert [r["out"] for r in rows] == [True, True, True, False, False, False]
    assert all(FAKE_KEY not in json.dumps(r) and FP not in json.dumps(r) for r in rows)


def test_d12_concurrent_helpers_pay_at_most_three_times(tmp_path):
    rec = key_helper.create(_dir(tmp_path))
    paid: list[bool] = []
    threads = [
        threading.Thread(target=lambda: paid.append(key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0)))) for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert paid.count(True) == 3 and sum(r["out"] for r in _paid(rec.path)) == 3


def test_d12_a_deny_mark_stops_the_payout(tmp_path):
    rec = key_helper.create(_dir(tmp_path))
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0))
    key_helper.deny(rec)
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1001.0)) is False
    assert key_helper.watch(rec.path.parent, rec).auth


@pytest.mark.parametrize("swap", ["symlink", "hardlink", "removed"])
def test_i9_a_swapped_record_gives_no_key_and_leaves_the_target_alone(tmp_path, swap):
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    other = tmp_path / "other.jsonl"
    other.write_text("keep\n")
    before = other.stat().st_mtime_ns
    rec.path.unlink()
    if swap == "symlink":
        rec.path.symlink_to(other)
    elif swap == "hardlink":
        os.link(other, rec.path)
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0)) is False
    assert other.read_text() == "keep\n" and other.stat().st_mtime_ns == before
    assert key_helper.watch(d, rec).auth


def test_i9_the_record_comes_from_the_arguments_not_the_settings_env(tmp_path, capsys, monkeypatch):
    rec = key_helper.create(_dir(tmp_path))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", f"ANTHROPIC_API_KEY={FAKE_KEY}")
    monkeypatch.setenv("NDF_RELAY_DIR", str(elsewhere))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "other")
    code, out, _ = _helper(capsys, FP, "--record", str(rec.path), "--id", rec.helper_id, pin=_pin(tmp_path))
    assert (code, out) == (0, FAKE_KEY)
    assert not (elsewhere / key_helper.RECORD_FILE).exists()
    assert [r["id"] for r in _paid(rec.path)] == [rec.helper_id]
    assert _helper(capsys, FP, "--record", str(rec.path))[0] == 1
    assert _helper(capsys, FP, "--id", rec.helper_id)[0] == 1


def test_d12_a_lock_timeout_leaves_a_fail_mark_instead_of_a_row(tmp_path):
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    fd = os.open(rec.path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0)) is False
    finally:
        os.close(fd)
    assert _paid(rec.path) == []
    assert _rows(d / key_helper.FAIL_FILE) == [{"id": rec.helper_id, "by": "helper", "cause": "lock"}]
    assert key_helper.watch(d, rec) == key_helper.Seen(True, None)
    # ラッパーが見回る前でも、錠の取れた次の呼び出しは同じ ID の拒否の後なのでキーを出さない（r2-6）
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1001.0)) is False
    assert _paid(rec.path) == [{"id": rec.helper_id, "at": 1001.0, "out": False}]


def test_d12_a_fail_mark_left_while_a_payout_is_inside_the_lock_gives_no_key(tmp_path, monkeypatch):
    # A が錠の中で失敗のマークの確かめを通った後、B が錠の待ちを打ち切ってマークを残して拒んだら、A もキーを出さない（r3-1）
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    passed, refused, seen = threading.Event(), threading.Event(), []
    real = key_helper._failed

    def failed(directory, helper_id):
        got = real(directory, helper_id)
        if threading.current_thread() is not threading.main_thread() and not seen:
            seen.append(got)
            passed.set()
            assert refused.wait(5)
        return got

    monkeypatch.setattr(key_helper, "_failed", failed)
    paid: list = []
    a = threading.Thread(target=lambda: paid.append(key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0))))
    a.start()
    assert passed.wait(5)
    b = key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0))  # A が錠を持つので B は錠の待ちを打ち切る
    refused.set()
    a.join()
    assert seen == [False] and b is False and paid == [False]
    assert _rows(d / key_helper.FAIL_FILE) == [{"id": rec.helper_id, "by": "helper", "cause": "lock"}]


def test_d12_an_empty_fail_mark_is_a_refusal_for_both_readers(tmp_path, monkeypatch):
    # 錠の待ちを打ち切った呼び出しがマークを作れても最初の書き込みが落ちると空のファイルが残る。拒否として扱う（r3-2）
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    real = os.write

    def write(fd, data):
        if b'"by"' in data:
            raise OSError(28, "No space left on device")
        return real(fd, data)

    monkeypatch.setattr(key_helper.os, "write", write)
    fd = os.open(rec.path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0)) is False
    finally:
        os.close(fd)
    monkeypatch.setattr(key_helper.os, "write", real)
    assert (d / key_helper.FAIL_FILE).read_bytes() == b""
    assert key_helper.watch(d, rec).auth
    # 容量が戻っても、同じ ID ではキーを出さない
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1001.0)) is False


def test_d12_a_short_write_of_the_payout_row_gives_no_key(tmp_path, monkeypatch):
    # 空き容量の不足などで行の一部だけが書けたら、払い出しを数えられないのでキーを出さず失敗のマークを残す（r2-5）
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    real = os.write
    monkeypatch.setattr(key_helper.os, "write", lambda fd, data: real(fd, data[:5]) if b'"out"' in data else real(fd, data))
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0)) is False
    assert _rows(d / key_helper.FAIL_FILE) == [{"id": rec.helper_id, "by": "helper", "cause": "append"}]
    assert key_helper.watch(d, rec).auth


def test_d12_watch_tells_a_refusal_from_a_settings_hit(tmp_path):
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    assert key_helper.watch(d, rec) == key_helper.Seen(False, None)
    with open(rec.path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": "other", "at": 1.0, "out": False}) + "\n")
    assert key_helper.watch(d, rec) == key_helper.Seen(False, None)  # 別の起動の行は読まない
    with open(rec.path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": rec.helper_id, "gate": "利用者の設定"}) + "\n")
    assert key_helper.watch(d, rec) == key_helper.Seen(False, "利用者の設定")
    with open(rec.path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": rec.helper_id, "at": 2.0, "out": False}) + "\n")
    assert key_helper.watch(d, rec) == key_helper.Seen(True, "利用者の設定")


def test_i9_a_changed_directory_is_a_record_failure(tmp_path):
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    os.chmod(d, 0o755)
    assert key_helper.watch(d, rec).auth
    os.chmod(d, 0o700)
    assert not key_helper.watch(d, rec).auth


def test_d12_a_payout_takes_its_time_inside_the_lock(tmp_path):
    # 錠を待った呼び出しは、錠を取った後の時刻で窓を照らして行に書く（古い時刻で窓を早く抜けない）
    rec = key_helper.create(_dir(tmp_path))
    fd = os.open(rec.path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    taken: list[float] = []

    def clock() -> float:
        taken.append(time.monotonic())
        return 1000.0

    t = threading.Thread(target=lambda: key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, clock))
    t.start()
    time.sleep(0.2)
    released = time.monotonic()
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    t.join()
    assert len(taken) == 1 and taken[0] >= released


@pytest.mark.parametrize(
    "content", ["", "keep\n", json.dumps({"id": "x", "at": 1.0, "out": True}) + "\n"], ids=["empty", "text", "other-jsonl"]
)
def test_i9_a_file_without_this_header_is_not_a_record_and_is_left_alone(tmp_path, content):
    # 記録の取り違え: 自分の 0600 の単一リンクのファイルでも、この helper の ID の見出しが無ければ書き足さない
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    other = tmp_path / "mine.txt"
    other.write_text(content)
    os.chmod(other, 0o600)
    assert key_helper.pay(other, rec.helper_id, FAKE_KEY, at(1000.0)) is False
    assert other.read_text() == content
    rec.path.write_text(content)
    assert key_helper.watch(d, rec).auth


@pytest.mark.parametrize("swap", ["hardlink", "other-file"])
def test_i9_the_gate_does_not_append_to_a_swapped_record(tmp_path, swap):
    # `gate` も `pay` と同じ記録の確かめを通し、ハードリンクや別の用途のファイルへ書き足さない（失敗のマークで知らせる）
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    other = tmp_path / "other.jsonl"
    other.write_text("keep\n")
    os.chmod(other, 0o600)
    rec.path.unlink()
    if swap == "hardlink":
        os.link(other, rec.path)
    else:
        os.rename(other, rec.path)
        other = rec.path
    key_helper.gate(rec, "user_settings")
    assert other.read_text() == "keep\n"
    assert _rows(d / key_helper.FAIL_FILE) == [{"id": rec.helper_id, "by": "gate", "cause": "record"}]


def test_i9_the_pin_is_checked_inside_the_record_lock(tmp_path):
    # 払い出しの判断と同じ錠の中で固定を照らす（錠を待つ間に変わった設定の照合結果でキーを出さない）
    rec = key_helper.create(_dir(tmp_path))
    fd = os.open(rec.path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    checked: list[float] = []
    paid: list[bool] = []

    def pinned() -> bool:
        checked.append(time.monotonic())
        return False

    t = threading.Thread(target=lambda: paid.append(key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0), pinned=pinned)))
    t.start()
    time.sleep(0.2)
    released = time.monotonic()
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    t.join()
    assert paid == [False] and len(checked) == 1 and checked[0] >= released
    assert _paid(rec.path) == [{"id": rec.helper_id, "at": 1000.0, "out": False, "cause": "pin"}]


def test_d12_no_payout_after_a_gate_row_even_if_the_settings_come_back(tmp_path):
    # `gate` の行（拒んだ変更）の後は、設定が元に戻って固定の照合が通っても同じ ID ではキーを出さない
    rec = key_helper.create(_dir(tmp_path))
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0), pinned=lambda: True)
    key_helper.gate(rec, "user_settings")
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1001.0), pinned=lambda: True) is False


@pytest.mark.parametrize("swap", ["hardlink", "mode"])
def test_i9_a_swapped_fail_mark_is_not_appended_to(tmp_path, swap):
    # 失敗のマークも追記の前に確かめる。別のファイルへのハードリンクや自分の 0600 でないマークには書き足さず、読む側は拒否として扱う
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    other = tmp_path / "other.txt"
    other.write_text("keep\n")
    os.chmod(other, 0o600)
    if swap == "hardlink":
        os.link(other, d / key_helper.FAIL_FILE)
    else:
        (d / key_helper.FAIL_FILE).write_text("")
        os.chmod(d / key_helper.FAIL_FILE, 0o644)
        other = d / key_helper.FAIL_FILE
    before = other.read_text()
    rec.path.unlink()
    rec.path.write_text("keep\n")  # 差し替えた記録（`gate` は失敗のマークへ回る）
    os.chmod(rec.path, 0o600)
    key_helper.gate(rec, "user_settings")
    assert other.read_text() == before
    assert key_helper.watch(d, rec).auth or key_helper.watch(d, rec).source is not None
    assert key_helper._failed(d, rec.helper_id)


@pytest.mark.parametrize("kind", ["fifo", "device"])
def test_i9_a_settings_file_resolving_to_a_non_regular_file_is_not_read(tmp_path, kind):
    # 設定ファイルの symlink の先が FIFO なら open で止まらず、デバイスなら読み続けずに照らせないとする
    target = tmp_path / "fifo"
    if kind == "fifo":
        os.mkfifo(target)
    else:
        target = "/dev/zero"
    link = tmp_path / "settings.json"
    link.symlink_to(target)
    done: list = []
    t = threading.Thread(target=lambda: done.append(key_helper.file_sha(link)), daemon=True)
    t.start()
    t.join(5)
    assert done == [None]
