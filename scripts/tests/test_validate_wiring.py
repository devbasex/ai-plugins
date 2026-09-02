"""説明文書の検査が `scripts/validate-runtime-plugins.sh` から呼ばれていることを固定する。

検査そのものが正しくても、既存の検査から呼ばれていなければ CI では実行されない。
配線が外れたことを、実物の検査を丸ごと動かさずに検出する。

確かめたいのは**呼び出しの形**であって、ファイル名に触れた行の中身ではない。呼び出し行の
近くで参照先を説明することはむしろ望ましいため、候補からコメント行を除く。
"""
from __future__ import annotations

from doc_staleness_helpers import CHECKER, REPO_ROOT

VALIDATE = REPO_ROOT / "scripts/validate-runtime-plugins.sh"
CHECKER_NAME = "scripts/check-doc-staleness.py"


def invocation_lines(body: str) -> list[str]:
    """説明文書の検査を起動している行。コメント行は候補から除く。"""
    return [
        line
        for line in body.splitlines()
        if CHECKER_NAME in line and not line.lstrip().startswith("#")
    ]


def test_checker_script_exists() -> None:
    assert CHECKER.is_file()


def test_validate_invokes_the_checker() -> None:
    """コメントで名前に触れているだけの状態を、呼ばれていると読まない。"""
    assert invocation_lines(VALIDATE.read_text(encoding="utf-8"))


def test_a_comment_before_the_call_is_not_taken_as_the_call() -> None:
    body = "\n".join(
        [
            f"# 版数の書式は `{CHECKER_NAME}` の定義に揃える",
            f'run python3 "$ROOT_DIR/{CHECKER_NAME}" --root "$ROOT_DIR"',
        ]
    )
    assert invocation_lines(body) == [f'run python3 "$ROOT_DIR/{CHECKER_NAME}" --root "$ROOT_DIR"']


def test_a_body_without_the_call_has_no_candidate() -> None:
    """呼び出しが外れた状態を、素通りさせない。"""
    assert invocation_lines(f"# {CHECKER_NAME} へ触れるだけの行\n") == []


def test_validate_passes_the_repository_root() -> None:
    """`--root` を渡さないと、検査は自分の位置から根を推測することになる。"""
    lines = invocation_lines(VALIDATE.read_text(encoding="utf-8"))
    assert lines, f"{VALIDATE} に {CHECKER_NAME} の呼び出し行が無い"
    for line in lines:
        assert "--root" in line


def test_a_call_without_the_root_option_is_detected() -> None:
    lines = invocation_lines(f'run python3 "$ROOT_DIR/{CHECKER_NAME}"\n')
    assert lines and all("--root" not in line for line in lines)
