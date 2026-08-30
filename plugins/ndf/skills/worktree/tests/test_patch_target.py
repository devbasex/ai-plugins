"""パッチ本文からの書き込み先の推定を検証する（受け入れ条件 6〜8 の apply_patch 側）。

Codex CLI はファイルの編集を `apply_patch` で渡し、パスは `tool_input.command` の
本文にある。実機の入力で確認した形を対象とする。
"""
from __future__ import annotations

from worktree_helpers import run_lib

CODEX_PATCH = """*** Begin Patch
*** Update File: plugins/ndf/README.md
@@
-# sample
+# sample edited
*** End Patch
"""


def extract(patch: str) -> tuple[list[str], int]:
    # 改行を含むためヒアドキュメントで渡す。引数へ埋めると 1 行に潰れる。
    snippet = (
        "patch=$(cat <<'WT_EOF'\n" + patch.rstrip("\n") + "\nWT_EOF\n)\n"
        "wt_extract_patch_target \"$patch\"; echo rc=$?"
    )
    got = run_lib(snippet)
    lines = [ln for ln in got.stdout.splitlines() if ln]
    rc = int(lines.pop().removeprefix("rc="))
    return lines, rc


def test_update_file() -> None:
    targets, rc = extract(CODEX_PATCH)
    assert rc == 0
    assert targets == ["plugins/ndf/README.md"], targets


def test_add_and_delete_and_move() -> None:
    patch = """*** Begin Patch
*** Add File: plugins/ndf/new.md
+x
*** Delete File: plugins/ndf/old.md
*** Update File: plugins/ndf/moved.md
*** Move to: plugins/ndf/dest.md
*** End Patch
"""
    targets, rc = extract(patch)
    assert rc == 0
    assert targets == [
        "plugins/ndf/new.md",
        "plugins/ndf/old.md",
        "plugins/ndf/moved.md",
        "plugins/ndf/dest.md",
    ], targets


def test_body_without_paths() -> None:
    targets, rc = extract("*** Begin Patch\n*** End Patch\n")
    assert rc == 1
    assert targets == []


def test_content_lines_are_not_paths() -> None:
    """本文の追加行が `*** Update File:` を含んでいても、行頭でなければ拾わない。"""
    patch = """*** Begin Patch
*** Update File: docs/a.md
+この行には *** Update File: plugins/ndf/README.md と書いてある
*** End Patch
"""
    targets, _ = extract(patch)
    assert targets == ["docs/a.md"], targets
