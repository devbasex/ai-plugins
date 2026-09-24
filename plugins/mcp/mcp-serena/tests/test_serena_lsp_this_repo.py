"""このリポジトリの .serena/project.yml が検出の結果と食い違わない（AC4）。"""
from serena_lsp_testlib import PLUGIN, run_json
from serena_lsp import project_yml as py

REPO = PLUGIN.parents[2]


def test_this_repository_project_yml_matches_detection():
    code, out, _ = run_json("detect", "--root", str(REPO), "--json")
    assert code == 0
    detected = [d["language"] for d in out["detected"]]
    text = (REPO / ".serena/project.yml").read_text()
    assert py.read_list(text, "language_servers") == detected == ["python", "bash"]
    assert ".worktrees/**" in py.read_list(text, "ignored_paths")
    assert py.read_list(text, "mcp_serena_excluded") == []
