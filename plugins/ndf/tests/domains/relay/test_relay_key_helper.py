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
import stat
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


def _helper(capsys, *extra) -> tuple[int, str, str]:
    code = cli.main(["account", "api-key-helper", "metered", *extra])
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
def test_3864_9_metered_helper_gives_only_the_fingerprinted_key(capsys, monkeypatch, decl, out):
    # 別冊の単体テストの観点の `_acc_key_helper`（metered）: 合うときだけキーの値だけを出し、ほかは何も出さずに 1
    if decl is None:
        monkeypatch.delenv("NDF_SUPERVISE_CLAUDE_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", decl)
    code, stdout, stderr = _helper(capsys, FP)
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
    key_helper.deny(rec)
    assert other.read_text() == "keep\n" and other.stat().st_mtime_ns == before
    assert key_helper.watch(d, rec).auth


def test_i9_the_record_comes_from_the_arguments_not_the_settings_env(tmp_path, capsys, monkeypatch):
    rec = key_helper.create(_dir(tmp_path))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", f"ANTHROPIC_API_KEY={FAKE_KEY}")
    monkeypatch.setenv("NDF_RELAY_DIR", str(elsewhere))
    monkeypatch.setenv("NDF_RELAY_LAUNCH", "other")
    code, out, _ = _helper(capsys, FP, "--record", str(rec.path), "--id", rec.helper_id)
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


@pytest.mark.parametrize("kind", ["fifo", "hardlink"])
def test_i9_a_swapped_record_or_fail_mark_neither_blocks_nor_is_written(tmp_path, kind):
    # 記録と失敗のマークが FIFO でも見回りと払い出しは止まらず、ハードリンクの先の利用者のファイルへ書かない
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    other = tmp_path / "user.txt"
    other.write_text("keep\n")
    os.chmod(other, 0o600)
    for name in (key_helper.RECORD_FILE, key_helper.FAIL_FILE):
        (d / name).unlink(missing_ok=True)
        if kind == "fifo":
            os.mkfifo(d / name, 0o600)
        else:
            os.link(other, d / name)
    assert key_helper.watch(d, rec).auth
    key_helper.deny(rec)
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, at(1000.0)) is False
    assert other.read_text() == "keep\n"


def test_i9_create_fixes_only_its_own_directory_and_never_follows_a_link(tmp_path):
    d = tmp_path / "relay"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    key_helper.create(d)
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    link = tmp_path / "link"
    link.symlink_to(shared)
    with pytest.raises(OSError):
        key_helper.create(link)
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
