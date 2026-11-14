"""Skill を配る plugin family の一覧を出す。

family は `plugins/<名>/manifests/` を持ち、`.claude-plugin/marketplace.json` に
`./plugins/<名>` で登録されたものである。登録の無いディレクトリ（開発中で
`--plugin-dir` で読む `plugins/ndf-next/` など）は配らないため、build と検査の対象に入れない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def distributed_families(root: Path) -> list[str]:
    """配る family の名前を辞書順で返す。"""
    marketplace = json.loads((root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    registered = {Path(p["source"]).as_posix().removeprefix("./") for p in marketplace["plugins"]}
    return sorted(d.name for d in (root / "plugins").iterdir() if (d / "manifests").is_dir() and f"plugins/{d.name}" in registered)


if __name__ == "__main__":
    print("\n".join(distributed_families(Path(sys.argv[1]))))
