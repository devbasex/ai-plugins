"""説明文書の検査（`scripts/check-doc-staleness.py`）のテスト補助。

実物の `README.md` / `AGENTS.md` / `plugins/ndf/README.md` は書き換えない。代わりに、
突き合わせの対象になる最小の木を一時ディレクトリへ作り、そこの説明文書だけを崩す。

数は実物（33 / 31 / 32 / 31）と重ならない小さい値にしてある。版数も実物とは別の
値（9.3.0）にしてある。テストが実物の値へ依存していないことを、値そのもので示すためである。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-doc-staleness.py"

VERSION = "9.3.0"

# 一覧表の行ごとに、その名前の `plugin.json` と突き合わせることを確かめるための 2 つ目の
# プラグイン。NDF とは別の版数にしておく。
OTHER_PLUGIN = "fixture-kit"
OTHER_VERSION = "1.4.2"

# 実体（`skills/`）5 個と任意 Skill（`optional-skills/`）2 個。元 Skill 数は 7 になる。
SKILL_DIRS = ["alpha", "bravo", "charlie", "delta", "echo"]
OPTIONAL_DIRS = ["xray", "yankee"]

# ランタイムごとの配布 Skill 数。4 つとも別の値にして、取り違えを検出できるようにする。
MANIFESTS = {
    "claude": ["alpha", "bravo", "charlie", "delta", "echo"],
    "codex": ["alpha", "bravo", "charlie"],
    "kiro": ["alpha", "bravo", "charlie", "delta"],
    "agy": ["alpha", "bravo"],
}

# 記号（G〜M）は `issues/parallel-batch-03/04-issue-209.md` の「検査する記載」に対応する。
# G: 概要の版数 / H: プラグイン一覧表の版数の列
ROOT_README = """# Fixture Marketplace

**NDFプラグイン v9.3.0** の検査用の最小構成です。

- **公開Skills**: Claude Code向け core 5個、Kiro向け core 4個、Codex向け core 3個、agy向け core 2個に分離。
- **元Skills（7個）**:
  - 第1群 (4): alpha, bravo, charlie, delta
  - 第2群 (3): echo, xray, yankee
- **8つの専門エージェント**: director

### 利用可能なプラグイン

| プラグイン名 | バージョン | 説明 |
|------------|----------|------|
| **ndf** | 9.3.0 | 検査用の最小構成 |
| **fixture-kit** | 1.4.2 | 一覧表の行ごとの突き合わせを確かめるための 2 つ目 |

### NDF v9.0.0 の主な変更（非互換）

- v4.0.0 で古い経路を廃止しました。それより前の版（8.5.4 以前）には戻せません
"""

# I: 「主要プラグインです（v<版>）」 / J: 版の付け方の節（区間の検査）
AGENTS_MD = """# Fixture Guidelines

## ポリシー

### 版の付け方と開発版の配布

| 版 | 形 | 意味 |
| --- | --- | --- |
| 正式版 | `9.3.0` | 利用者が常用してよい |
| 開発版 | `9.3.0-dev.1` | 検証中 |

- 接尾辞は次に出す正式版の版数へ付ける。`9.3.0` の次を開発するなら `9.4.0-dev.1`

### 検査が突き合わせる箇所

版数の基準は `plugins/ndf/.claude-plugin/plugin.json` の `version` である。

## NDFプラグインについて

**NDFプラグイン**は、このマーケットプレイスの主要プラグインです（v9.3.0）。

## 変更の履歴

v8.5.4 で古い経路を廃止した。それより前の版（8.4.0 以前）は対象外である。
"""

# K: Kiro の確認例 / L: Codex のキャッシュパスの例（2 箇所） / M: `codex plugin list` の出力例
PLUGIN_README = """# NDF Plugin

配布物は 1 ディレクトリにまとまっています。

| ランタイム | 公開 Skill | マニフェスト |
| --- | --- | --- |
| Claude Code | 5 個 | `.claude-plugin/plugin.json` |
| Codex | 3 個 | `.codex-plugin/plugin.json` |
| Kiro CLI | 4 個 | `dev.kiro/install.sh` |
| agy | 2 個 | `dev.agy/plugin.json` |

## レイアウト

```text
plugins/ndf/
├── skills/                      # 配布 Skill の唯一の実体（5 個）
├── optional-skills/             # どの配布先にも載せない Skill（2 個）
└── manifests/                   # ランタイム別の配布 Skill 一覧
```

## v9.3.0 へ更新するとき

**Skill が 1 個増えます。** 既存の Skill の手順は変わりません。

## Kiro CLI で確かめる

```bash
kiro agent list
# => NDF統合開発エージェント（Kiro CLI用 / v9.3.0）
```

## Codex で確かめる

```text
~/.codex/plugins/cache/ai-plugins/ndf/9.3.0/skills/deploy/SKILL.md を読んでください。
```

```bash
codex plugin list
# => ndf@ai-plugins  installed, enabled  9.3.0  <path>
# ~/.codex/plugins/cache/ai-plugins/ndf/9.3.0/skills/deploy/SKILL.md
```
"""


def build_tree(base: Path) -> Path:
    """突き合わせに要るものだけを備えた木を作り、その根を返す。"""
    root = base / "repo"
    ndf = root / "plugins/ndf"

    (ndf / ".claude-plugin").mkdir(parents=True)
    (ndf / ".claude-plugin/plugin.json").write_text(
        '{\n  "name": "ndf",\n  "version": "%s"\n}\n' % VERSION, encoding="utf-8"
    )

    other = root / "plugins" / OTHER_PLUGIN / ".claude-plugin"
    other.mkdir(parents=True)
    (other / "plugin.json").write_text(
        '{\n  "name": "%s",\n  "version": "%s"\n}\n' % (OTHER_PLUGIN, OTHER_VERSION),
        encoding="utf-8",
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
    (root / "AGENTS.md").write_text(AGENTS_MD, encoding="utf-8")
    (ndf / "README.md").write_text(PLUGIN_README, encoding="utf-8")
    return root


def edit(path: Path, old: str, new: str) -> None:
    """説明文書の 1 箇所だけを差し替える。見つからなければテスト側の誤りとして落とす。"""
    body = path.read_text(encoding="utf-8")
    if body.count(old) != 1:
        raise AssertionError(f"{path} に {old!r} がちょうど 1 箇所ない（{body.count(old)} 箇所）")
    path.write_text(body.replace(old, new), encoding="utf-8")


def edit_all(path: Path, old: str, new: str, expected: int) -> None:
    """同じ書き方が複数箇所にある記載を、まとめて差し替える。

    箇所の数まで指定させるのは、文書を書き換えたときにテストが黙って対象を減らさない
    ようにするためである。
    """
    body = path.read_text(encoding="utf-8")
    if body.count(old) != expected:
        raise AssertionError(f"{path} に {old!r} が {expected} 箇所ない（{body.count(old)} 箇所）")
    path.write_text(body.replace(old, new), encoding="utf-8")


def bump_plugin_version(root: Path, version: str) -> None:
    """木の `plugin.json` の版だけを上げる。説明文書には触らない。"""
    (root / "plugins/ndf/.claude-plugin/plugin.json").write_text(
        '{\n  "name": "ndf",\n  "version": "%s"\n}\n' % version, encoding="utf-8"
    )


def base_of(version: str) -> str:
    """接尾辞を捨てた数字 3 つ。`9.7.0-dev.1` の基底は `9.7.0` になる。"""
    return version.split("-", 1)[0]


def next_minor(version: str) -> str:
    """基底の minor を 1 つ進めた版数。版の付け方の節が置く「次の版」の例に使う。"""
    major, minor, patch = base_of(version).split(".")
    return f"{major}.{int(minor) + 1}.{patch}"


def retarget_version(root: Path, version: str) -> None:
    """木の現行版を指す記載を、`plugin.json` ごとまとめて別の版へ揃える。

    `bump_plugin_version` が `plugin.json` だけを動かして食い違いを作るのに対し、こちらは
    突き合わせ先もすべて動かし、その版で検査が通る状態を作る。接尾辞の付いた版で通ることは、
    版数を書く箇所がすべて揃った木でしか確かめられない。
    """
    old_base, new_base = base_of(VERSION), base_of(version)
    bump_plugin_version(root, version)

    readme = root / "README.md"
    edit(readme, f"**NDFプラグイン v{VERSION}**", f"**NDFプラグイン v{version}**")
    edit(readme, f"| **ndf** | {VERSION} |", f"| **ndf** | {version} |")

    agents = root / "AGENTS.md"
    edit(agents, f"主要プラグインです（v{VERSION}）", f"主要プラグインです（v{version}）")
    # 版の付け方の節は基底で比べる。例に並ぶ版数の基底が現行版より古ければ落ちるため、
    # 現行版の例も次の版の例も、新しい基底へ寄せる。
    edit_all(agents, f"`{old_base}`", f"`{new_base}`", 2)
    edit(agents, f"`{old_base}-dev.1`", f"`{new_base}-dev.1`")
    edit(agents, f"`{next_minor(VERSION)}-dev.1`", f"`{next_minor(version)}-dev.1`")

    plugin_readme = root / "plugins/ndf/README.md"
    edit(plugin_readme, f"## v{VERSION} へ更新するとき", f"## v{version} へ更新するとき")
    edit(plugin_readme, f"Kiro CLI用 / v{VERSION}）", f"Kiro CLI用 / v{version}）")
    edit_all(plugin_readme, f"ndf/{VERSION}/skills/", f"ndf/{version}/skills/", 2)
    edit(plugin_readme, f"enabled  {VERSION}  <path>", f"enabled  {version}  <path>")


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
