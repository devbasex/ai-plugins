"""gws の導入の同意を求める承認資料を組み、状態の置き場の下（ファイルの表の行 `gws-install-material`。パスは表が持つ）へ書く。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ndf import identity
from ndf.domains.records import files
from ndf.domains.worktree import atomic

def render(*, package: str, package_url: str, command: Sequence[str], where: str) -> str:
    """承認資料の Markdown（対象を開くためのもの・承認の判断に使うもの・同意を求めること・戻し方）。"""
    shown = " ".join(command)
    return "\n".join(
        [
            "# gws（Google Workspace CLI）の導入の同意",
            "",
            "## 対象を開くためのもの",
            "",
            f"- npm の {package}: {package_url}",
            "",
            "## 承認の判断に使うもの",
            "",
            "| 項目 | 値 |",
            "| --- | --- |",
            f"| 取得元 | npm の `{package}` |",
            f"| 打つコマンド | `{shown}` |",
            f"| 入る先 | {where} |",
            "| 注意 | Google の公式サポート外の CLI で、版は 0.x |",
            "",
            "## 同意を求めること",
            "",
            "- 上の取得元から利用者の環境へ全体インストールを行う（npm の全体インストール 1 件）",
            "",
            "## 戻し方",
            "",
            f"`npm uninstall -g {package}`",
            "",
        ]
    )


def write(text: str) -> Path:
    """承認資料を状態の置き場の下へ置き換えで書き、そのパスを返す。"""
    dest = files.path(identity.load().state_dir, "gws-install-material")
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(dest, text)
    return dest
