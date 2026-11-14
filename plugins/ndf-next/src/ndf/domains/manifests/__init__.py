"""配る Skill の表（`manifests/skills.json`）と、その表から作るランタイムの定義（#3409）。

- `table`: 表の読み手と形の確かめ
- `front_matter`: Skill の frontmatter の読み書き（ruamel-yaml）
- `render`: `.codex-plugin/plugin.json` の `skills` と、Codex の Skill ごとの `agents/openai.yaml` の生成と照合
- `cli`: `ndf manifests render [--check]`
"""
