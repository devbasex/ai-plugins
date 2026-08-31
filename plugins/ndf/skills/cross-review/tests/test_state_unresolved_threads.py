"""Pull Request 上の未解決の指摘を数える経路のテスト。

ここでいう指摘は、Pull Request 上のレビューコメントの塊（review thread）で、
Resolve できる単位を指す。未解決の指摘は Resolve されていない指摘である。

**投稿数と未解決の指摘の数は一致しない。** 投稿数はそのラウンドで外部の AI が
新しく投稿した件数で、未解決の指摘は前のラウンドの分も含む Pull Request 上の総数である。

| GitHub 側 | 返り値 | 扱い |
| --- | --- | --- |
| 未解決 2 件 | 2 件の一覧 | 判定へ入れる |
| 未解決 0 件 | 空の一覧 | 引き継いだ指摘は無い |
| 取得できない | `None` | 判定を止めず、確認できなかったことを残す |

「取得できなかった」と「0 件」を混同しない。取得の失敗で止めると、GitHub 側の
一時的な不調でループが進まなくなる。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 4242
REPO = "o/r"


def _seed_state(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-08-31T00:00:00+00:00"}],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def gh_output(monkeypatch, state_mod):
    """`gh` の標準出力を差し替える。`None` は取得できなかったことを表す。"""
    calls: list[list[str]] = []

    def _set(output):
        def _fake(cmd):
            calls.append(list(cmd))
            return output
        monkeypatch.setattr(state_mod, "_gh_output", _fake)
        return calls

    return _set


# ---------------- 取得と解釈 ----------------

def test_unresolved_threads_are_listed_with_their_identifiers(state_mod, gh_output):
    """未解決の指摘だけが、Resolve に使える識別子つきで返る。"""
    calls = gh_output("PRRT_a\tsrc/foo.py\t42\nPRRT_b\tdocs/bar.md\t7\n")

    threads = state_mod._fetch_unresolved_threads(REPO, PR)

    assert [t["id"] for t in threads] == ["PRRT_a", "PRRT_b"]
    assert threads[0]["path"] == "src/foo.py"
    assert threads[0]["line"] == "42"
    # 所有者と名前を分けて渡し、対象の Pull Request 番号を含めている
    joined = " ".join(calls[0])
    assert "owner=o" in joined and "name=r" in joined and f"pr={PR}" in joined


def test_no_unresolved_thread_returns_an_empty_list(state_mod, gh_output):
    """0 件は空の一覧で返る。取得できなかったこととは区別する。"""
    gh_output("")

    assert state_mod._fetch_unresolved_threads(REPO, PR) == []


def test_failure_to_fetch_returns_none(state_mod, gh_output):
    """取得できないときは `None` を返し、0 件と区別する。"""
    gh_output(None)

    assert state_mod._fetch_unresolved_threads(REPO, PR) is None


def test_missing_repository_is_treated_as_unavailable(state_mod, monkeypatch):
    """リポジトリを決められないときは GitHub を呼ばずに `None` を返す。"""
    monkeypatch.setattr(
        state_mod, "_gh_output",
        lambda cmd: pytest.fail("リポジトリが無いのに GitHub を呼んでいる"),
    )

    assert state_mod._fetch_unresolved_threads("", PR) is None
    assert state_mod._fetch_unresolved_threads("no-slash", PR) is None


# ---------------- サブコマンド ----------------

def test_subcommand_prints_the_count_and_the_identifiers(tmp_dir, state_mod, gh_output, capsys):
    _seed_state(tmp_dir)
    gh_output("PRRT_a\tsrc/foo.py\t42\nPRRT_b\tdocs/bar.md\t7\n")

    state_mod.cmd_unresolved_threads(argparse.Namespace(pr=PR))

    out = capsys.readouterr().out
    assert "UNRESOLVED_COUNT=2" in out
    assert "PRRT_a" in out and "PRRT_b" in out


def test_subcommand_reports_zero_without_failing(tmp_dir, state_mod, gh_output, capsys):
    _seed_state(tmp_dir)
    gh_output("")

    state_mod.cmd_unresolved_threads(argparse.Namespace(pr=PR))

    assert "UNRESOLVED_COUNT=0" in capsys.readouterr().out


def test_subcommand_fails_when_the_count_cannot_be_fetched(tmp_dir, state_mod, gh_output):
    """取得できないことを 0 件として出力しない。"""
    _seed_state(tmp_dir)
    gh_output(None)

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_unresolved_threads(argparse.Namespace(pr=PR))
    assert e.value.code == 1
