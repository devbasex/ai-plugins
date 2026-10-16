"""主ディレクトリの `git pull --ff-only` が落ちた理由の判定（merged-steps.py の後片付けが使う）。"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _blocked_paths(pull):
    return [l.strip() for l in pull.stderr.splitlines() if l.startswith("\t")]


def same_untracked(main_dir, pull):
    """pull を止めた未追跡のファイルが、すべて上流とバイト列で同じ（CRLF と LF も別物）ならその一覧を返す。1 つでも違えば空。"""
    if "untracked working tree files would be overwritten" not in pull.stderr:
        return []
    rels = _blocked_paths(pull)
    for rel in rels:
        up = subprocess.run(["git", "-C", str(main_dir), "show", f"@{{u}}:{rel}"], capture_output=True)
        if up.returncode != 0 or not (path := Path(main_dir) / rel).is_file() or path.read_bytes() != up.stdout:
            return []
    return rels


def blocking_local_changes(main_dir, pull):
    """pull を止めたのが追跡ファイルの未コミットの変更なら、そのファイルの一覧を返す。別の理由なら空。"""
    if "Your local changes to the following files would be overwritten" not in pull.stderr:
        return []
    status = subprocess.run(
        ["git", "-C", str(main_dir), "status", "--porcelain=v1", "--untracked-files=no"], capture_output=True, text=True
    )
    return _blocked_paths(pull) or [line[3:] for line in status.stdout.splitlines()]
