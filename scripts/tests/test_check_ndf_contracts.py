"""根の契約の検査（`scripts/check-ndf-contracts.py`）の振る舞いを固定する（#2336・#3304）。

一時ディレクトリへ作った小さな木に、違反を 1 つずつ入れて落ちることと、外すと通ることを確かめる。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-ndf-contracts.py"
LIMITS = {"policies": {"monitor.cli-margin": {"summary": "CLI の上限の差", "seconds": 120}}}
CODE = "plugins/ndf/src/ndf/tool.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_ndf_contracts_for_test", CHECK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ck = _module()


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _tree(root: Path) -> Path:
    """違反の無い小さな木（スキーマのコピー 4 本・上限の宣言・宣言どおりに引くコード）。"""
    for copy, name in ck.SCHEMA_COPIES:
        body = json.dumps({"title": name}, ensure_ascii=False) + "\n"
        _write(root / f"{ck.SETTINGS_SCHEMAS}/{name}.schema.json", body)
        _write(root / copy, body)
    _write(root / "plugins/ndf/contracts/limits.json", json.dumps(LIMITS, ensure_ascii=False))
    _write(
        root / CODE,
        'from ndf.contracts import limits\nMARGIN = "monitor.cli-margin"\n\n\ndef f(name):\n'
        "    limits.seconds(MARGIN)\n    limits.value(name, 'seconds')\n",
    )
    return root


def run(root: Path, *args: str) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, str(CHECK), "--root", str(root), *args], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.splitlines()[-1])


def _kinds(out: dict) -> list[tuple[str, str, str]]:
    return [(i["kind"], i["path"], i["name"]) for i in out["items"]]


def test_clean_tree_passes(tmp_path: Path):
    code, out = run(_tree(tmp_path))
    assert (code, out["status"], out["items"]) == (0, "ok", [])
    assert out["metrics"]["schema_copies"] == len(ck.SCHEMA_COPIES)


def test_base_option_is_accepted_and_ignored(tmp_path: Path):
    code, out = run(_tree(tmp_path), "--base", "origin/main")
    assert (code, out["status"]) == (0, "ok")


@pytest.mark.parametrize("index", range(len(ck.SCHEMA_COPIES)))
def test_schema_copy_differs_by_one_byte(tmp_path: Path, index: int):
    copy, name = ck.SCHEMA_COPIES[index]
    root = _tree(tmp_path)
    with (root / copy).open("a", encoding="utf-8") as f:
        f.write(" ")
    origin = f"{ck.SETTINGS_SCHEMAS}/{name}.schema.json"
    code, out = run(root)
    assert code == 1
    assert _kinds(out) == [("copy-mismatch", copy, origin)]
    assert out["items"][0]["detail"].endswith(f"cp {origin} {copy} で写す")


def test_schema_copy_or_origin_missing_names_which(tmp_path: Path):
    root = _tree(tmp_path)
    copy0, (copy1, name1) = ck.SCHEMA_COPIES[0][0], ck.SCHEMA_COPIES[1]
    (root / copy0).unlink()
    (root / f"{ck.SETTINGS_SCHEMAS}/{name1}.schema.json").unlink()
    got = {i["path"]: i["detail"] for i in ck.copy_mismatches(root)}
    assert got[copy0].startswith("コピーが無い")
    assert got[copy1].startswith("契約のスキーマが無い")


def test_unknown_policy_name_fails(tmp_path: Path):
    root = _tree(tmp_path)
    with (root / CODE).open("a", encoding="utf-8") as f:
        f.write("\n\nlimits.policy('no.such')\n")
    code, out = run(root)
    assert code == 1
    assert _kinds(out) == [("limits-unknown", CODE, "no.such")]


def test_plain_import_alias_is_also_read(tmp_path: Path):
    root = _tree(tmp_path)
    _write(root / "plugins/ndf/src/ndf/other.py", "import limits as L\nL.seconds('no.such')\n")
    assert _kinds(run(root)[1]) == [("limits-unknown", "plugins/ndf/src/ndf/other.py", "no.such")]


def test_names_that_cannot_be_read_statically_and_unused_policies_pass(tmp_path: Path):
    root = _tree(tmp_path)
    decl = root / "plugins/ndf/contracts/limits.json"
    decl.write_text(json.dumps({"policies": {**LIMITS["policies"], "never.used": {"summary": "s", "seconds": 1}}}), encoding="utf-8")
    assert run(root)[0] == 0


@pytest.mark.parametrize("text", ["{", "[]", '{"policies": []}'])
def test_unreadable_limits_declaration_fails(tmp_path: Path, text: str):
    root = _tree(tmp_path)
    (root / "plugins/ndf/contracts/limits.json").write_text(text, encoding="utf-8")
    code, out = run(root)
    assert code == 1
    assert _kinds(out) == [("limits-shape", "plugins/ndf/contracts/limits.json", "")]


def test_scope_counts_violations_by_path_or_via(tmp_path: Path):
    root = _tree(tmp_path)
    copy, name = ck.SCHEMA_COPIES[0]
    with (root / copy).open("a", encoding="utf-8") as f:
        f.write(" ")
    origin = f"{ck.SETTINGS_SCHEMAS}/{name}.schema.json"
    assert run(root, CODE)[1]["metrics"]["outside"] == 1
    assert run(root, CODE)[0] == 0
    assert _kinds(run(root, copy)[1]) == [("copy-mismatch", copy, origin)]
    assert _kinds(run(root, origin)[1]) == [("copy-mismatch", copy, origin)]


def test_scope_with_the_checker_counts_the_whole_tree(tmp_path: Path):
    items = [{"kind": "copy-mismatch", "path": "a", "name": "", "result": "violation", "detail": ""}]
    assert ck.judge(items, {"scripts/contracts_lib/limits_decl.py"}) == (items, 0)
    assert ck.judge(items, {"scripts"}) == (items, 0)
    assert ck.judge(items, {"b"}) == ([], 1)


def test_repository_tree_has_no_violation():
    code, out = run(REPO)
    assert (code, out["status"]) == (0, "ok"), out["items"]
