"""API キーの helper の払い出しと記録（U11a。#3604 #3864 の受け入れ条件 #3864 の 9・決定 12・I9）。

- `ndf relay account api-key-helper metered <指紋>` は、環境変数の宣言が API キーの宣言で、キーの SHA-256 が指紋と同じときだけ
  キーを出す（別冊の「環境変数の宣言の API キー」）
- `--record`・`--id` のある起動は、記録の錠の中で払い出しの上限（60 秒に 3 回・拒んだ後と拒否の印の後は 0 回）を照らし、
  1 行を足してからキーを出す。錠・追記の失敗は失敗の印に残す（別冊の「キーの子の認証の失敗の停止」）
値はすべて偽の値で、実のキーを使わない。
"""

from __future__ import annotations

import fcntl
import json
import os
import threading

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
        (f"CLAUDE_CODE_USE_BEDROCK=1 ANTHROPIC_API_KEY={FAKE_KEY}", None),  # API キーの宣言でない
        ("ANTHROPIC_API_KEY='unclosed", None),  # 解けない
        (None, None),  # 宣言が無い
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
    got = [key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, 1000.0 + i / 10) for i in range(5)]
    assert got == [True, True, True, False, False]
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, 1061.0) is False
    rows = _rows(rec.path)
    assert [r["out"] for r in rows] == [True, True, True, False, False, False]
    assert all(FAKE_KEY not in json.dumps(r) and FP not in json.dumps(r) for r in rows)


def test_d12_concurrent_helpers_pay_at_most_three_times(tmp_path):
    rec = key_helper.create(_dir(tmp_path))
    paid: list[bool] = []
    threads = [threading.Thread(target=lambda: paid.append(key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, 1000.0))) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert paid.count(True) == 3 and sum(r["out"] for r in _rows(rec.path)) == 3


def test_d12_a_deny_mark_stops_the_payout(tmp_path):
    rec = key_helper.create(_dir(tmp_path))
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, 1000.0)
    key_helper.deny(rec)
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, 1001.0) is False
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
    assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, 1000.0) is False
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
    assert [r["id"] for r in _rows(rec.path)] == [rec.helper_id]
    assert _helper(capsys, FP, "--record", str(rec.path))[0] == 1
    assert _helper(capsys, FP, "--id", rec.helper_id)[0] == 1


def test_d12_a_lock_timeout_leaves_a_fail_mark_instead_of_a_row(tmp_path):
    d = _dir(tmp_path)
    rec = key_helper.create(d)
    fd = os.open(rec.path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert key_helper.pay(rec.path, rec.helper_id, FAKE_KEY, 1000.0) is False
    finally:
        os.close(fd)
    assert rec.path.read_text() == ""
    assert _rows(d / key_helper.FAIL_FILE) == [{"id": rec.helper_id, "by": "helper", "cause": "lock"}]
    assert key_helper.watch(d, rec) == key_helper.Seen(True, None)


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
