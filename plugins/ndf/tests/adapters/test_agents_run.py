"""agents の起動（保存先の名前・ファイルへの書き出し・新しいセッション・子へ渡す環境）。"""

from __future__ import annotations

from pathlib import Path

from ndf.adapters import agents


def test_agent_paths_at_names_files_next_to_stem():
    paths = agents.AgentPaths.at(Path("/w/kiro"))

    names = (paths.out.name, paths.err.name, paths.launch.name, paths.progress.name)
    assert names == ("kiro.out", "kiro.err.log", "kiro-launch.json", "kiro-progress.log")
