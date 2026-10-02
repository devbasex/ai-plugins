"""作業ツリーの退避と、退避先の回収（#824）。

退避（`evacuate`）: `git worktree remove` が拒否した作業ツリーの未追跡・無視されたファイルを
`<共通の git ディレクトリ>/ndf/worktree-trash/<ラベル>-<UTC の YYYYmmddHHMMSS>/` へ移す。
作り直せる生成物（名前が `GENERATED` に当たるディレクトリ）は退避せずに消し、台帳の `discarded` に載せる。
退避するものが残らなければ退避先を作らない。

台帳: 退避先と同じ名前に `.json` を付けたファイルを退避先の隣に置く（退避先の中に置くと、
`cp -a <退避先>/. <worktree>/` で戻すときに一緒に戻る）。`branch`・`head`（退避したときの HEAD）・
`merge_commit`（PR のマージコミット。分かるときだけ）・`evacuated_at`・`discarded` を持つ。

回収（`sweep`）: 本番に出たコミット `ref` に、台帳の `head` か `merge_commit` が含まれる退避先を消す。
戻す必要が出るのはそのブランチを含む版が本番へ出る前だけで、出た後は戻す先が無い。
台帳の無い退避先（この形より前の退避）は消さずに件数だけを報告する。日数による期限は持たない。
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
from pathlib import Path

from step_result import git

# 作り直せる生成物。パスのどこかにこの名前のディレクトリがあれば、その下ごと退避せずに消す
GENERATED = frozenset({".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "target"})
LEDGER_VERSION = 1


def trash_root(path) -> Path | None:
    """`<共通の git ディレクトリ>/ndf/worktree-trash`。git の作業ツリーでなければ None。"""
    p = git(path, "rev-parse", "--git-common-dir", check=False)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    d = Path(p.stdout.strip())
    return (d if d.is_absolute() else (Path(path) / d).resolve()) / "ndf" / "worktree-trash"


def generated_root(path, rel) -> str | None:
    """`rel` が作り直せる生成物の下にあれば、その生成物のディレクトリ（相対パス）を返す。"""
    parts = rel.split("/")
    for i, part in enumerate(parts):
        if part not in GENERATED:
            continue
        if i < len(parts) - 1 or (Path(path) / rel).is_dir():
            return "/".join(parts[: i + 1])
    return None


def _discard(target: Path) -> None:
    if target.is_symlink() or not target.is_dir():
        target.unlink(missing_ok=True)
    else:
        shutil.rmtree(target)


def ledger_of(trash: Path) -> Path:
    return trash.with_name(trash.name + ".json")


def read_ledger(trash) -> dict | None:
    try:
        return json.loads(ledger_of(Path(trash)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def evacuate(path, label, merge_commit=None):
    """未追跡・無視されたファイルを退避先へ移し、(退避先のパス（退避するものが無ければ None）, 捨てた生成物) を返す。

    作り直せる生成物は退避せずに消す。消したものと、退避先とブランチの対応は台帳に残す。
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    root = trash_root(path)
    if root is None:
        raise OSError(f"{path} の共通の git ディレクトリが分からない")
    trash = root / f"{label.replace('/', '__')}-{stamp}"
    head = git(path, "rev-parse", "HEAD", check=False).stdout.strip() or None
    discarded, moved = [], 0
    for rel in _stray_paths(path):
        src = Path(path) / rel
        if gen := generated_root(path, rel):
            if gen not in discarded:
                discarded.append(gen)
                _discard(Path(path) / gen)
            continue
        dst = trash / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # 作業ツリーが /tmp（tmpfs）にあると、.git の下へはファイルシステムをまたぐ。
        # os.replace は EXDEV で落ちるため、コピーと削除へ切り替わる shutil.move を使う
        shutil.move(str(src), str(dst))
        moved += 1
    if not moved:
        return None, discarded
    _write_ledger(trash, label, head, merge_commit, discarded)
    return str(trash), discarded


def _stray_paths(path):
    """git status の未追跡（??）と無視（!!）のパスのうち、まだ在るものを順に返す。"""
    out = git(path, "status", "--ignored", "--untracked-files=all", "--porcelain=v1", "-z").stdout
    for ent in out.split("\0"):
        if ent[:3] not in ("?? ", "!! "):
            continue
        rel = ent[3:].rstrip("/")
        if not rel:
            continue
        if not os.path.lexists(Path(path) / rel):
            continue
        yield rel


def _write_ledger(trash, label, head, merge_commit, discarded):
    ledger = {
        "version": LEDGER_VERSION,
        "branch": label,
        "head": head,
        "merge_commit": merge_commit,
        "evacuated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "discarded": discarded,
    }
    ledger_of(trash).write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _contains(root, commit, ref) -> bool:
    return bool(commit) and git(root, "merge-base", "--is-ancestor", commit, ref, check=False).returncode == 0


def sweep(root, ref) -> tuple[list[dict], dict]:
    """`ref` に台帳のコミットが含まれる退避先を消す。(items, metrics) を返す。"""
    items, metrics = [], {"swept_trash": 0, "kept_trash": 0, "unledgered_trash": 0}
    base = trash_root(root)
    if base is None or not base.is_dir():
        return items, metrics
    unledgered = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        led = read_ledger(d)
        if led is None:
            unledgered.append(d.name)
            continue
        hit = next((c for c in (led.get("merge_commit"), led.get("head")) if _contains(root, c, ref)), None)
        if hit is None:
            metrics["kept_trash"] += 1
            continue
        try:
            shutil.rmtree(d)
            ledger_of(d).unlink(missing_ok=True)
        except OSError as e:
            items.append({"kind": "trash", "name": str(d), "result": "kept", "reason": f"消せない: {e}"})
            metrics["kept_trash"] += 1
            continue
        items.append(
            {
                "kind": "trash",
                "name": str(d),
                "result": "removed",
                "reason": f"{led.get('branch')} の {hit[:12]} が本番（{ref[:12]}）に含まれる",
            }
        )
        metrics["swept_trash"] += 1
    if unledgered:
        metrics["unledgered_trash"] = len(unledgered)
        names = ", ".join(unledgered[:5]) + ("…" if len(unledgered) > 5 else "")
        items.append(
            {
                "kind": "trash",
                "name": str(base),
                "result": "kept",
                "reason": f"台帳の無い退避先 {len(unledgered)} 件（{names}）は本番に出たかを決められないため消さない",
            }
        )
    return items, metrics
