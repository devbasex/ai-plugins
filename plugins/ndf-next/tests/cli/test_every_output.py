"""既存のテストが同じプロセスの中で ok の出力を出さない副命令を、ok を出す引数で呼ぶ（#3518 の決定 9）。

結果 JSON の形は `tests/conftest.py` の `_checked_emit` が確かめ、全体の実行の終わりに ok の出力の無い副命令を落とす。
ここには、ほかのテストが同じプロセスで ok の出力を出さない副命令だけを足す（同じことを確かめるテストを重ねない）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from ndf import cli
from ndf.adapters import training
from ndf.domains.release import verify

# 副命令の名前 → ok を出す引数（どれもプラグインの実物を読むだけで、書き換えない）
CALLS: dict[str, list[str]] = {
    "launch-args": [],
    "notify": ["send"],
    "manifests": ["render", "--check"],
    "hooks": ["render", "--check"],
    "contracts": ["check"],
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_ok_output(name, capsys, last_result):
    assert name in cli.COMMANDS
    assert cli.main([name, *CALLS[name]]) == 0
    report = last_result(capsys.readouterr().out)
    assert (report["tool"], report["status"]) == (name, "ok")


def _ok(argv: list[str], capsys, last_result) -> None:
    assert cli.main(argv) == 0
    report = last_result(capsys.readouterr().out)
    assert (report["tool"], report["status"]) == (argv[0], "ok")


def test_plan_check_ok_output(tmp_path, capsys, last_result):
    """`ndf plan` のほかのテストは子プロセスで打つため、同じプロセスで ok を出す（プランの形を確かめるだけ）。"""
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps({"name": "p", "repo": str(tmp_path), "steps": [{"id": "a", "run": ["true"]}]}), encoding="utf-8")
    _ok(["plan", "check", str(plan)], capsys, last_result)


def test_release_steps_config_snapshot_ok_output(tmp_path, monkeypatch, capsys, last_result):
    """利用者の設定の控えを一時の置き場から取り、一時のファイルへ書く（利用者の設定を読まない）。"""
    monkeypatch.setenv(verify.CLAUDE_DIR_ENV, str(tmp_path / "claude"))
    monkeypatch.setenv(verify.CODEX_DIR_ENV, str(tmp_path / "codex"))
    _ok(["release-steps", "config-snapshot", "--out", str(tmp_path / "snap.json")], capsys, last_result)


def test_training_check_ok_output(tmp_path, monkeypatch, capsys, last_result):
    """学習の設定の照会を、学習に使わない答えを返す偽の HTTP で受ける（外へ送らない）。"""
    conf = tmp_path / "claude-conf"
    conf.mkdir()
    (conf / training.CLAUDE_CREDENTIALS).write_text(json.dumps({"claudeAiOauth": {"accessToken": "t" * 24}}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(conf))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"grove_enabled": False})))
    monkeypatch.setattr(training.httpx, "Client", lambda: client)
    _ok(["training", "check", "--runtime", "claude"], capsys, last_result)
