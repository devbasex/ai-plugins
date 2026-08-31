"""説明文書の検査が `scripts/validate-runtime-plugins.sh` から呼ばれていることを固定する。

検査そのものが正しくても、既存の検査から呼ばれていなければ CI では実行されない。
配線が外れたことを、実物の検査を丸ごと動かさずに検出する。
"""
from __future__ import annotations

from doc_staleness_helpers import CHECKER, REPO_ROOT

VALIDATE = REPO_ROOT / "scripts/validate-runtime-plugins.sh"


def test_checker_script_exists() -> None:
    assert CHECKER.is_file()


def test_validate_invokes_the_checker() -> None:
    body = VALIDATE.read_text(encoding="utf-8")
    assert "scripts/check-doc-staleness.py" in body


def test_validate_passes_the_repository_root() -> None:
    """`--root` を渡さないと、検査は自分の位置から根を推測することになる。"""
    body = VALIDATE.read_text(encoding="utf-8")
    line = next(l for l in body.splitlines() if "check-doc-staleness.py" in l)
    assert "--root" in line
