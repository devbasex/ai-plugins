"""`start-round` が提案・レビューの母集合を返すこと（#518-1）。

**繰り返しの中で使う値は、繰り返しの中で得られる。** 母集合を `init` だけが返すと、
状態ファイルから再開する経路と、骨組みを抜粋して写す経路の両方で未定義になる。
"""
from __future__ import annotations

import shlex

import pytest

from crossref_helpers import make_state


def _args(state_id=130):
    return type("A", (), {"id": state_id})()


def _emitted(capsys) -> dict[str, str]:
    """`emit` が書いた KEY=VALUE を辞書にする。"""
    values: dict[str, str] = {}
    for line in capsys.readouterr().out.splitlines():
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if not key.isupper():
            continue
        values[key] = " ".join(shlex.split(raw))
    return values


def test_start_round_emits_the_review_pool(refactor, tmp_path, env_tmp_dir, capsys):
    state_path = make_state(tmp_path, runtimes=["codex", "agy", "kiro"])
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())

    emitted = _emitted(capsys)
    assert emitted["RUNTIMES"] == "codex agy kiro"
    assert emitted["RUNTIMES_CSV"] == "codex,agy,kiro"


def test_emitted_pool_follows_the_state_file(refactor, tmp_path, env_tmp_dir, capsys):
    """母集合の出所は状態ファイルであり、その場で数え直さない。"""
    state_path = make_state(tmp_path, runtimes=["claude", "agy", "kiro"])
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())

    assert _emitted(capsys)["RUNTIMES"] == "claude agy kiro"


def test_existing_values_are_untouched(refactor, tmp_path, env_tmp_dir, capsys):
    """足すだけで、既存の呼び出し側が読む値は変えない。"""
    state_path = make_state(tmp_path)
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())

    emitted = _emitted(capsys)
    for key in (
        "ROUND", "ROUND_KIND", "PROPOSE_PHASE", "IMPL", "IMPL_MODEL",
        "REVIEWERS", "REVIEWERS_CSV", "MAX_FIX_ROUNDS",
    ):
        assert key in emitted, f"{key} が出力から消えている"


def test_the_pool_is_wider_than_the_reviewers(refactor, tmp_path, env_tmp_dir, capsys):
    """`REVIEWERS` で代用すると提案する者が 1 人減る。"""
    state_path = make_state(tmp_path, runtimes=["codex", "agy", "kiro"])
    env_tmp_dir(state_path)
    refactor.cmd_start_round(_args())

    emitted = _emitted(capsys)
    assert len(emitted["RUNTIMES"].split()) == 3
    assert len(emitted["REVIEWERS"].split()) == 2
