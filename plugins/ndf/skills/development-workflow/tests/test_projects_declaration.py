"""宣言の読み取りとフィールド名の解決を検証する（受け入れ条件 1〜3）。

盤面への書き込みは行わない。宣言が無い環境で何も起きないことが、この層の主な責務である。
"""
from __future__ import annotations

import json

from projects_helpers import run_lib, write_declaration

VALID = json.dumps({"version": 1, "owner": "devbasex", "number": 1})


def test_no_declaration_is_not_enabled(repo) -> None:
    got = run_lib(f'pj_declaration "{repo}"; echo rc=$?')
    assert got.stdout.strip() == "rc=1"


def test_declaration_is_read(repo) -> None:
    write_declaration(repo, VALID)
    got = run_lib(f'pj_declaration "{repo}"; echo rc=$?')
    lines = got.stdout.splitlines()
    assert lines[-1] == "rc=0"
    assert json.loads(lines[0])["owner"] == "devbasex"


def test_wrong_version_is_rejected(repo) -> None:
    """版が違う宣言は読まない。知らない形を推測で解釈しない。"""
    write_declaration(repo, json.dumps({"version": 99, "owner": "x", "number": 1}))
    got = run_lib(f'pj_declaration "{repo}"; echo rc=$?')
    assert got.stdout.strip() == "rc=1"


def test_broken_json_is_rejected(repo) -> None:
    write_declaration(repo, "{ not json")
    got = run_lib(f'pj_declaration "{repo}"; echo rc=$?')
    assert got.stdout.strip() == "rc=1"


def test_owner_and_number_are_required(repo) -> None:
    """盤面を特定できない宣言は無効として扱う。"""
    write_declaration(repo, json.dumps({"version": 1, "owner": "devbasex"}))
    got = run_lib(f'pj_declaration "{repo}"; echo rc=$?')
    assert got.stdout.strip() == "rc=1"


def test_field_names_fall_back_to_defaults(repo) -> None:
    """宣言がフィールド名を持たないときは既定の名前を使う。"""
    write_declaration(repo, VALID)
    got = run_lib(
        f'json=$(pj_declaration "{repo}")\n'
        'for k in stage mode worktree plan; do pj_field_name "$json" "$k"; done'
    )
    assert got.stdout.split() == ["進行", "モード", "作業ツリー", "計画ファイル"]


def test_field_names_can_be_overridden(repo) -> None:
    """盤面のフィールド名は利用者が決める。宣言で差し替えられる。"""
    write_declaration(repo, json.dumps({
        "version": 1, "owner": "devbasex", "number": 1,
        "fields": {"stage": "Stage", "plan": "Plan file"},
    }))
    got = run_lib(
        f'json=$(pj_declaration "{repo}")\n'
        'for k in stage mode worktree plan; do pj_field_name "$json" "$k"; done'
    )
    assert got.stdout.splitlines() == ["Stage", "モード", "作業ツリー", "Plan file"]


def test_unknown_field_key_fails(repo) -> None:
    """知らないキーは既定を持たない。呼び出し側の誤りとして落とす。"""
    write_declaration(repo, VALID)
    got = run_lib(f'json=$(pj_declaration "{repo}")\npj_field_name "$json" nope; echo rc=$?')
    assert got.stdout.strip().endswith("rc=1")
