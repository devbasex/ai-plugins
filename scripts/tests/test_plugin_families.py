"""配る plugin family を manifests/ と marketplace.json の登録の両方で決めることを固定する。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("plugin_families", Path(__file__).resolve().parents[1] / "lib/plugin_families.py")
assert _SPEC and _SPEC.loader
plugin_families = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(plugin_families)


def _repo(tmp_path: Path, registered: list[str], with_manifests: list[str]) -> Path:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/marketplace.json").write_text(
        json.dumps({"plugins": [{"name": n, "source": f"./plugins/{n}"} for n in registered]}),
        encoding="utf-8",
    )
    for name in set(registered) | set(with_manifests):
        (tmp_path / "plugins" / name).mkdir(parents=True)
    for name in with_manifests:
        (tmp_path / "plugins" / name / "manifests").mkdir()
    return tmp_path


def test_unregistered_plugin_with_manifests_is_not_distributed(tmp_path: Path) -> None:
    """開発中で --plugin-dir で読むディレクトリは、manifests/ を持っても配る系統に入らない。"""
    root = _repo(tmp_path, registered=["ndf", "kit"], with_manifests=["ndf", "kit", "ndf-next"])
    assert plugin_families.distributed_families(root) == ["kit", "ndf"]


def test_registered_plugin_without_manifests_is_not_a_family(tmp_path: Path) -> None:
    root = _repo(tmp_path, registered=["ndf", "mcp-x"], with_manifests=["ndf"])
    assert plugin_families.distributed_families(root) == ["ndf"]
