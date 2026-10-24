"""check-skill-frontmatter.py の measure_aggregate が数える初期一覧の文字数を固定する。

配布先ごとの数え方（Claude Code は description と when_to_use を連結して切り詰め、
パスを載せない。Codex / Kiro / agy は description だけでパスを載せる）と、
manifest に無い Skill を数えないこと、frontmatter 総量を固定する。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-skill-frontmatter.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_skill_frontmatter", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path: Path, manifests: dict[str, list[str]]) -> Path:
    plugin = tmp_path / "plugins/ndf"
    skills_dir = plugin / "skills"
    skills_dir.mkdir(parents=True)
    (plugin / "manifests").mkdir()
    for runtime, names in manifests.items():
        (plugin / f"manifests/{runtime}-skills.txt").write_text("".join(f"{n}\n" for n in names), encoding="utf-8")
    return skills_dir


def _skill(dir_name: str, block: str, **fm) -> dict:
    return {"dir": dir_name, "fm": fm, "block": block}


def test_counts_name_description_and_path_per_runtime(tmp_path):
    m = _load()
    skills_dir = _tree(tmp_path, {"claude": ["alpha"], "codex": ["alpha"], "kiro": ["alpha"], "agy": ["alpha"]})
    skills = [_skill("alpha", "x" * 10, name="alpha", description="desc", when_to_use="wtu")]
    rel = len("skills/alpha/SKILL.md")
    got = m.measure_aggregate(skills, skills_dir)
    assert got == {
        "listings": {
            "claude": len("alpha") + len("descwtu"),
            "codex": len("alpha") + len("desc") + rel,
            "kiro": len("alpha") + len("desc") + rel,
            "agy": len("alpha") + len("desc") + rel,
        },
        "frontmatter_total": 10,
    }


def test_skips_skills_missing_from_manifest_but_counts_frontmatter(tmp_path):
    m = _load()
    skills_dir = _tree(tmp_path, {"claude": ["alpha"], "codex": []})
    skills = [
        _skill("alpha", "aaa", name="alpha", description="d"),
        _skill("beta", "bbbbb", name="beta", description="e"),
    ]
    got = m.measure_aggregate(skills, skills_dir)
    assert got == {"listings": {"claude": len("alpha") + 1, "codex": 0}, "frontmatter_total": 8}


def test_claude_item_is_truncated_after_concatenation(tmp_path):
    m = _load()
    skills_dir = _tree(tmp_path, {"claude": ["alpha"]})
    limit = m.CLAUDE_ITEM_TRUNCATE
    skills = [_skill("alpha", "", name="alpha", description="d" * limit, when_to_use="w" * 50)]
    got = m.measure_aggregate(skills, skills_dir)
    assert got["listings"] == {"claude": len("alpha") + limit}


def test_missing_frontmatter_falls_back_to_dir_name(tmp_path):
    m = _load()
    skills_dir = _tree(tmp_path, {"codex": ["gamma"]})
    skills = [{"dir": "gamma", "fm": None, "block": "zz"}]
    got = m.measure_aggregate(skills, skills_dir)
    assert got == {"listings": {"codex": len("gamma") + len("skills/gamma/SKILL.md")}, "frontmatter_total": 2}


def test_manifest_comments_are_ignored(tmp_path):
    m = _load()
    skills_dir = _tree(tmp_path, {})
    (skills_dir.parent / "manifests/kiro-skills.txt").write_text("# header\nalpha  # note\n\n", encoding="utf-8")
    skills = [_skill("alpha", "", name="alpha", description="dd")]
    got = m.measure_aggregate(skills, skills_dir)
    assert got["listings"] == {"kiro": len("alpha") + 2 + len("skills/alpha/SKILL.md")}
