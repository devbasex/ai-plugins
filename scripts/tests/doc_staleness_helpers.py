"""説明文書の検査（`scripts/check-doc-staleness.py`）のテスト補助。

実物の `README.md` と `plugins/ndf/README.md` は書き換えない。代わりに、突き合わせの
対象になる最小の木を一時ディレクトリへ作り、そこの説明文書だけを崩す。

数は実物（31 / 29 / 30 / 4）と重ならない小さい値にしてある。テストが実物の値へ依存して
いないことを、値そのもので示すためである。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-doc-staleness.py"

VERSION = "9.3.0"

# 実体（`skills/`）5 個と任意 Skill（`optional-skills/`）2 個。元 Skill 数は 7 になる。
SKILL_DIRS = ["alpha", "bravo", "charlie", "delta", "echo"]
OPTIONAL_DIRS = ["xray", "yankee"]

# ランタイムごとの配布 Skill 数。3 つとも別の値にして、取り違えを検出できるようにする。
MANIFESTS = {
    "claude": ["alpha", "bravo", "charlie", "delta", "echo"],
    "codex": ["alpha", "bravo", "charlie"],
    "kiro": ["alpha", "bravo", "charlie", "delta"],
}

ROOT_README = """# Fixture Marketplace

**NDFプラグイン v9.3.0** の検査用の最小構成です。

- **公開Skills**: Claude Code向け core 5個、Kiro向け core 4個、Codex向け core 3個に分離。
- **元Skills（7個）**:
  - 第1群 (4): alpha, bravo, charlie, delta
  - 第2群 (3): echo, xray, yankee
- **8つの専門エージェント**: director
"""

PLUGIN_README = """# NDF Plugin

配布物は 1 ディレクトリにまとまっています。

| ランタイム | 公開 Skill | マニフェスト |
| --- | --- | --- |
| Claude Code | 5 個 | `.claude-plugin/plugin.json` |
| Codex | 3 個 | `.codex-plugin/plugin.json` |
| Kiro CLI | 4 個 | `dev.kiro/install.sh` |

## レイアウト

```text
plugins/ndf/
├── skills/                      # 配布 Skill の唯一の実体（5 個）
├── optional-skills/             # どの配布先にも載せない Skill（2 個）
└── manifests/                   # ランタイム別の配布 Skill 一覧
```

## v9.3.0 へ更新するとき

**Skill が 1 個増えます。** 既存の Skill の手順は変わりません。
"""


def build_tree(base: Path) -> Path:
    """突き合わせに要るものだけを備えた木を作り、その根を返す。"""
    root = base / "repo"
    ndf = root / "plugins/ndf"

    (ndf / ".claude-plugin").mkdir(parents=True)
    (ndf / ".claude-plugin/plugin.json").write_text(
        '{\n  "name": "ndf",\n  "version": "%s"\n}\n' % VERSION, encoding="utf-8"
    )

    (ndf / "manifests").mkdir(parents=True)
    for runtime, skills in MANIFESTS.items():
        body = "# コメント行と空行は数えない\n\n" + "".join(f"{name}\n" for name in skills)
        (ndf / f"manifests/{runtime}-skills.txt").write_text(body, encoding="utf-8")

    for name in SKILL_DIRS:
        (ndf / "skills" / name).mkdir(parents=True)
        (ndf / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    # SKILL.md を持たないディレクトリは実体として数えない。
    (ndf / "skills/README.md").write_text("# 規約\n", encoding="utf-8")

    for name in OPTIONAL_DIRS:
        (ndf / "optional-skills" / name).mkdir(parents=True)
        (ndf / "optional-skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    (root / "README.md").write_text(ROOT_README, encoding="utf-8")
    (ndf / "README.md").write_text(PLUGIN_README, encoding="utf-8")
    return root


def edit(path: Path, old: str, new: str) -> None:
    """説明文書の 1 箇所だけを差し替える。見つからなければテスト側の誤りとして落とす。"""
    body = path.read_text(encoding="utf-8")
    if body.count(old) != 1:
        raise AssertionError(f"{path} に {old!r} がちょうど 1 箇所ない（{body.count(old)} 箇所）")
    path.write_text(body.replace(old, new), encoding="utf-8")


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    """検査を子プロセスとして実行し、終了コードと出力を観測する。"""
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr
