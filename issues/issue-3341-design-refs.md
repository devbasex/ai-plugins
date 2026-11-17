# #3341 の外からの参照の対応表: 入れ替えで切れる参照と、入れ替えの後の参照先

設計文書 [issue-3341-design.md](issue-3341-design.md) の受け入れ条件 1 の表である。**外からの参照**は、`plugins/ndf/` の外にあって `plugins/ndf/` の中のパスを名指すものを指す（用語は設計文書の「用語」）。

例: `.github/workflows/release-cd.yml` の 36 行は `plugins/ndf/scripts/release-steps.py publish` を打つ。入れ替えの後の `plugins/ndf/` には `scripts/` が無く、`main` へのマージで正式版のタグを作るジョブが落ちる。下の表の R9 の行が、この参照を `plugins/ndf/bin/ndf release-steps publish` へ書き換えると決める。

## 外からの参照の 4 つの種類

`plugins/ndf/` を名指す外のファイルは 813 本・4,989 箇所ある。813 本を、入れ替えでの扱いで 4 つの種類に分ける。

| 種類 | 何か | ファイル | 数える単位 | 表 |
| --- | --- | ---: | --- | --- |
| 実行する参照 | workflow・宣言・ISSUE_TEMPLATE・指示書・根の検査・Python の設定。打つコマンドかパスの規則として読まれる | 48 | ファイルと参照先の組（147 組） | 「実行する参照」 |
| 作り直す生成物 | 許可ファイル・計測の記録・宣言の `docs.tested` など、今のプラグインの中身から作られたもの | 533 | ファイルか置き場 | 「作り直す生成物」 |
| 根の検査のテスト | `scripts/tests/` の下の、根の検査を確かめるテスト | 26 | ファイル | 「根の検査のテスト」 |
| 文書 | `docs/` と `CHANGELOG.md` | 206 | 置き場 | 「文書」 |

合計は 48 + 533 + 26 + 206 = 813 本である。`issues/` は数えない（文書の行き先の表の `issues/` の行が扱う）。

**実行する参照は、ファイルと参照先の組を 1 行にする。** 同じファイルの中で同じ参照先を何行も名指す場合は、行番号を 1 つのセルに並べる。

## 数え方

2026-11-17 の `design/issue-3341`（`main` の 9a817e18f の上）で数えた。

```bash
git ls-files | grep -v -E '^(plugins/ndf/|plugins/ndf-next/|issues/)' \
  | xargs grep -l 'plugins/ndf/' | wc -l                       # 813
git ls-files | grep -v -E '^(plugins/ndf/|plugins/ndf-next/|issues/)' \
  | xargs grep -o 'plugins/ndf/' | wc -l                        # 4989
```

147 組は次で数え直せる。

```bash
git ls-files -z -- .github .ndf CLAUDE.md AGENTS.md README.md KIRO.md CONTRIBUTING.md SUPPORT.md \
  pyproject.toml conftest.py plugins/mcp tests 'scripts/check-*' scripts/lib scripts/contracts_lib \
  scripts/structure_checks scripts/token-usage.py scripts/validate-runtime-plugins.sh ':!.ndf/project.json' \
  | xargs -0 grep -oH 'plugins/ndf/[A-Za-z0-9_./*{},-]*' | sed 's/[.,]$//' | sort -u | wc -l   # 147
```

`.ndf/project.json` は 196 箇所のうち 194 箇所が `docs.tested`（文書が確かめたパスの一覧）で、作り直す生成物へ入れた。残りの 2 箇所（`$schema` と `test.suites` のコマンド）は「作り直す生成物」の表の該当の行に書いた。

## 入れ替えの後の参照先の決め方

| 判定 | 何をするか | 組の数 |
| --- | --- | ---: |
| 同じパス | 入れ替えの後も新しいプラグインの同じ相対パスにある。書き換えない | 53 |
| 向け直す | 新しいプラグインの別のパスか `bin/ndf <副命令>` へ書き換える | 73 |
| 未作成 | 行き先の副命令が新しいプラグインにまだ無い。機能の対応表（`scripts/ndf-next-parity/parity/features.json`。#3342）の行の行き先の子が作るまで、入れ替えの PR を出さない | 9 |
| 消す | 参照ごと消す（作らない機能・配らない置き場） | 8 |
| ファイルを消す | 参照を持つファイルそのものを入れ替えの PR で消す | 4 |

合計は 147 組である。**「未作成」の 9 組は、入れ替えの前提を満たしていないことを示す。** 外からの参照の検査（設計文書の決定 1）は、入れ替えの後のツリーで実在しない参照先を数える。「未作成」の行は既知の失敗として落とさず、行き先が実在した時点で行を「向け直す」へ書き換えないと落ちる。S2-0 は `--strict` で打ち、「未作成」が 1 組でも残れば落ちる。

「同じパス」の判定は、パスが実在するかだけを見る。中身の形が変わるものもある（例: `plugins/ndf/contracts/` は `env/`・`forms/` を持たず `domains/` を持つ）。それを読む根の検査は、入れ替えの PR が新しい形へ寄せる（手順 S2-5）。

## 外からの参照の検査が見るもの

設計文書の決定 1 の検査（`scripts/check-plugin-refs.py`。R0 の子が作る）は、上の 4 種類のうち **実行する参照と文書のリンク** を見る。生成物と根の検査のテストは、それぞれを作るコマンドと既存の CI のジョブが見る。

| モード | 何を見るか | 落ちる条件 |
| --- | --- | --- |
| 今のツリー | 実行する参照の参照先が `plugins/ndf/` に実在するか | 実在しない参照先が 1 組でもある |
| 入れ替えの後のツリー | `plugins/ndf-next/` を `plugins/ndf/` と読み替えた上で、書き換えの表を当てた参照先が実在するか | 書き換えの表に行が無く、実在しない参照先がある。または表の行き先が実在しない |

## 実行する参照（147 組）

### workflow（14 行）

| # | 参照する場所 | 今の参照先 | 入れ替えの後の参照先 |
| ---: | --- | --- | --- |
| R1 | `.github/workflows/glossary.yml:34` | `plugins/ndf/scripts/glossary.py` | `plugins/ndf/bin/ndf records glossary`（`check`・`gate` は未作成。対応表 → M7） |
| R2 | `.github/workflows/ndf-next-checks.yml:6,17` | `plugins/ndf/**` | ファイルごと消す（入れ替えの PR。決定 7） |
| R3 | `.github/workflows/ndf-next-release.yml:6` | `plugins/ndf/scripts/` | ファイルごと消す（入れ替えの PR。決定 7） |
| R4 | `.github/workflows/ndf-next.yml:135` | `plugins/ndf/` | ファイルごと消す（入れ替えの PR。決定 7） |
| R5 | `.github/workflows/ndf-next.yml:5,16` | `plugins/ndf/**` | ファイルごと消す（入れ替えの PR。決定 7） |
| R6 | `.github/workflows/pr-base-guard.yml:27` | `plugins/ndf/uv.lock` | 同じパス（入れ替えの後も新しい側にある） |
| R7 | `.github/workflows/pr-body-decisions.yml:36` | `plugins/ndf/scripts/pr-body-decisions.sh` | 未作成（対応表 `entrypoint:scripts/pr-body-decisions.sh` → M7） |
| R8 | `.github/workflows/release-approval.yml:48` | `plugins/ndf/scripts/release-steps.py` | `plugins/ndf/bin/ndf release-steps approval-check`（ジョブを `release-cd.yml` へ移し、このファイルは消す。決定 7） |
| R9 | `.github/workflows/release-cd.yml:36` | `plugins/ndf/scripts/release-steps.py` | `plugins/ndf/bin/ndf release-steps publish`（決定 7） |
| R10 | `.github/workflows/runtime-plugin-validate.yml:117` | `plugins/ndf/scripts/instructions-check.py` | 未作成（対応表 `entrypoint:scripts/instructions-check.py` → M7） |
| R11 | `.github/workflows/script-structure.yml:30` | `plugins/ndf/**` | 同じパス（入れ替えの後も新しい側にある） |
| R12 | `.github/workflows/script-structure.yml:7` | `plugins/ndf/contracts/` | 同じパス（入れ替えの後も新しい側にある） |
| R13 | `.github/workflows/script-structure.yml:15` | `plugins/ndf/scripts/data/issue-items.json` | `plugins/ndf/contracts/schemas/ndf-settings/issue-items.schema.json` の既定（対応表 `data:scripts/data/issue-items.json`） |
| R14 | `.github/workflows/script-structure.yml:62` | `plugins/ndf/scripts/issue-items.py` | 未作成（対応表 `entrypoint:scripts/issue-items.py` → M6） |

### ISSUE_TEMPLATE・CODEOWNERS（6 行）

| # | 参照する場所 | 今の参照先 | 入れ替えの後の参照先 |
| ---: | --- | --- | --- |
| R15 | `.github/CODEOWNERS:9` | `plugins/ndf/` | 同じパス（入れ替えの後も新しい側にある） |
| R16 | `.github/ISSUE_TEMPLATE/bug_report.yml:11` | `plugins/ndf/scripts/data/issue-items.json` | `plugins/ndf/contracts/schemas/ndf-settings/issue-items.schema.json` の既定（対応表 `data:scripts/data/issue-items.json`） |
| R17 | `.github/ISSUE_TEMPLATE/feature_request.yml:9` | `plugins/ndf/scripts/data/issue-items.json` | `plugins/ndf/contracts/schemas/ndf-settings/issue-items.schema.json` の既定（対応表 `data:scripts/data/issue-items.json`） |
| R18 | `.github/ISSUE_TEMPLATE/skill_proposal.yml:9` | `plugins/ndf/README.md` | `plugins/ndf/README.md`（S2 が新しい根へ置く。行き先の表） |
| R19 | `.github/ISSUE_TEMPLATE/skill_proposal.yml:13` | `plugins/ndf/scripts/data/issue-items.json` | `plugins/ndf/contracts/schemas/ndf-settings/issue-items.schema.json` の既定（対応表 `data:scripts/data/issue-items.json`） |
| R20 | `.github/ISSUE_TEMPLATE/skill_proposal.yml:11` | `plugins/ndf/skills/AUTHORING.md` | 同じパス（入れ替えの後も新しい側にある） |

### 宣言（22 行）

| # | 参照する場所 | 今の参照先 | 入れ替えの後の参照先 |
| ---: | --- | --- | --- |
| R21 | `.ndf/design.json:5` | `plugins/ndf/**` | 同じパス（入れ替えの後も新しい側にある） |
| R22 | `.ndf/glossary.json:48` | `plugins/ndf/scripts/data/legacy-values.json` | 消す（作らない行。対応表 `data:scripts/data/legacy-values.json`） |
| R23 | `.ndf/glossary.json:19` | `plugins/ndf/scripts/lib/README.md` | 消す（archive へ移す。行き先の表） |
| R24 | `.ndf/glossary.json:50` | `plugins/ndf/scripts/tests/test_estimate_predict.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R25 | `.ndf/glossary.json:49` | `plugins/ndf/scripts/tests/test_legacy_values.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R26 | `.ndf/glossary.json:51` | `plugins/ndf/skills/qa-security-scan/02-auth-checklist.md` | 同じパス（入れ替えの後も新しい側にある） |
| R27 | `.ndf/glossary.json:52` | `plugins/ndf/skills/qa-security-scan/03-report-template.md` | 同じパス（入れ替えの後も新しい側にある） |
| R28 | `.ndf/pace.json:18` | `plugins/ndf/hooks/**` | 同じパス（入れ替えの後も新しい側にある） |
| R29 | `.ndf/pace.json:18` | `plugins/ndf/scripts/*-guard.sh` | `plugins/ndf/src/ndf/domains/hooks/guards/**` |
| R30 | `.ndf/pace.json:17` | `plugins/ndf/scripts/lib/**` | `plugins/ndf/src/ndf/**` |
| R31 | `.ndf/pace.json:23` | `plugins/ndf/scripts/lib/auth.py` | `plugins/ndf/src/ndf/adapters/github.py` |
| R32 | `.ndf/pace.json:23` | `plugins/ndf/scripts/lib/git-credential.sh` | `plugins/ndf/src/ndf/adapters/git.py` |
| R33 | `.ndf/pace.json:21` | `plugins/ndf/scripts/merged-steps.py` | `plugins/ndf/bin/ndf worktree`（対応表 → M8） |
| R34 | `.ndf/pace.json:15` | `plugins/ndf/scripts/relay.py` | `plugins/ndf/src/ndf/domains/relay/`（`bin/ndf relay`） |
| R35 | `.ndf/pace.json:20` | `plugins/ndf/scripts/release-steps.py` | `plugins/ndf/bin/ndf release-steps` |
| R36 | `.ndf/pace.json:6,11,20` | `plugins/ndf/scripts/release-verification-steps.py` | `plugins/ndf/src/ndf/domains/release/`（`bin/ndf release-steps`） |
| R37 | `.ndf/pace.json:15` | `plugins/ndf/scripts/sprint-state.py` | `plugins/ndf/src/ndf/progress/`（`bin/ndf progress`） |
| R38 | `.ndf/pace.json:15` | `plugins/ndf/scripts/supervise.py` | `plugins/ndf/src/ndf/core/plan/`（`bin/ndf plan`） |
| R39 | `.ndf/pace.json:24` | `plugins/ndf/skills/google-workspace/**` | 同じパス（入れ替えの後も新しい側にある） |
| R40 | `.ndf/pace.json:16` | `plugins/ndf/skills/*/scripts/drive.py` | `plugins/ndf/bin/ndf cross-review`・`bin/ndf refactor` |
| R41 | `.ndf/release.json:2` | `plugins/ndf/skills/release/schemas/release.schema.json` | 同じパス（入れ替えの後も新しい側にある） |
| R42 | `.ndf/supervise.json:19` | `plugins/ndf/scripts/instructions-check.py` | 未作成（対応表 `entrypoint:scripts/instructions-check.py` → M7） |

### 指示書（26 行）

| # | 参照する場所 | 今の参照先 | 入れ替えの後の参照先 |
| ---: | --- | --- | --- |
| R43 | `AGENTS.md:145` | `plugins/ndf/` | 同じパス（入れ替えの後も新しい側にある） |
| R44 | `AGENTS.md:147` | `plugins/ndf/agents/` | 同じパス（入れ替えの後も新しい側にある） |
| R45 | `AGENTS.md:123` | `plugins/ndf/contracts/` | 同じパス（入れ替えの後も新しい側にある） |
| R46 | `AGENTS.md:146` | `plugins/ndf/manifests/{claude,codex}-skills.txt` | `plugins/ndf/manifests/skills.json` |
| R47 | `AGENTS.md:141` | `plugins/ndf/README.md` | `plugins/ndf/README.md`（S2 が新しい根へ置く。行き先の表） |
| R48 | `AGENTS.md:79` | `plugins/ndf/scripts/experimental/` | 消す（実験版の置き場は #3381・#3388 が決める） |
| R49 | `AGENTS.md:84` | `plugins/ndf/scripts/tests/test_experimental.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R50 | `AGENTS.md:146` | `plugins/ndf/skills/` | 同じパス（入れ替えの後も新しい側にある） |
| R51 | `AGENTS.md:27` | `plugins/ndf/skills/development-workflow/references/pace.md` | 同じパス（入れ替えの後も新しい側にある） |
| R52 | `AGENTS.md:149` | `plugins/ndf/skills/external-ai/references/cli-kiro.md` | 同じパス（入れ替えの後も新しい側にある） |
| R53 | `CLAUDE.md:34` | `plugins/ndf/manifests/` | 同じパス（入れ替えの後も新しい側にある） |
| R54 | `CLAUDE.md:44` | `plugins/ndf/scripts/instructions-check.py` | 未作成（対応表 `entrypoint:scripts/instructions-check.py` → M7） |
| R55 | `CLAUDE.md:34` | `plugins/ndf/skills/AUTHORING.md` | 同じパス（入れ替えの後も新しい側にある） |
| R56 | `CLAUDE.md:50` | `plugins/ndf/skills/cross-refactoring/SKILL.md` | 同じパス（入れ替えの後も新しい側にある） |
| R57 | `CLAUDE.md:50` | `plugins/ndf/skills/cross-review/SKILL.md` | 同じパス（入れ替えの後も新しい側にある） |
| R58 | `CONTRIBUTING.md:179` | `plugins/ndf/manifests/` | 同じパス（入れ替えの後も新しい側にある） |
| R59 | `CONTRIBUTING.md:92` | `plugins/ndf/scripts/lib/monitor.py` | 未作成（対応表 `entrypoint:skills/cross-review/scripts/wait-review.sh` → M5） |
| R60 | `CONTRIBUTING.md:103,175` | `plugins/ndf/skills/` | 同じパス（入れ替えの後も新しい側にある） |
| R61 | `CONTRIBUTING.md:180` | `plugins/ndf/skills/AUTHORING.md` | 同じパス（入れ替えの後も新しい側にある） |
| R62 | `CONTRIBUTING.md:181` | `plugins/ndf/skills/document-style/SKILL.md` | 同じパス（入れ替えの後も新しい側にある） |
| R63 | `KIRO.md:17` | `plugins/ndf/dev.kiro/install.sh` | 消す（Kiro へは配らない。相手役の起動は `skills/external-ai/references/cli-kiro.md`） |
| R64 | `KIRO.md:17` | `plugins/ndf/README.md` | `plugins/ndf/README.md`（S2 が新しい根へ置く。行き先の表） |
| R65 | `KIRO.md:12` | `plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh` | `plugins/ndf/bin/ndf refactor`（対応表 → M4） |
| R66 | `KIRO.md:11` | `plugins/ndf/skills/external-ai/references/cli-kiro.md` | 同じパス（入れ替えの後も新しい側にある） |
| R67 | `README.md:28,30,51,96` | `plugins/ndf/README.md` | `plugins/ndf/README.md`（S2 が新しい根へ置く。行き先の表） |
| R68 | `SUPPORT.md:13` | `plugins/ndf/README.md` | `plugins/ndf/README.md`（S2 が新しい根へ置く。行き先の表） |

### 根の検査（62 行）

| # | 参照する場所 | 今の参照先 | 入れ替えの後の参照先 |
| ---: | --- | --- | --- |
| R69 | `scripts/check-cross-skill-refs.py:4` | `plugins/ndf/manifests/*-skills.txt` | `plugins/ndf/manifests/skills.json` |
| R70 | `scripts/check-cross-skill-refs.py:162` | `plugins/ndf/scripts/lib/` | `plugins/ndf/src/ndf/` |
| R71 | `scripts/check-cross-skill-refs.py:38` | `plugins/ndf/skills` | 同じパス（入れ替えの後も新しい側にある） |
| R72 | `scripts/check-cross-skill-refs.py:60` | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | `plugins/ndf/bin/ndf refactor` |
| R73 | `scripts/check-cross-skill-refs.py:55` | `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/vocabulary.py` | `plugins/ndf/src/ndf/domains/crossrefactor/` |
| R74 | `scripts/check-cross-skill-refs.py:50` | `plugins/ndf/skills/cross-review/scripts/review_lib/github.py` | `plugins/ndf/src/ndf/adapters/github.py` |
| R75 | `scripts/check-cross-skill-refs.py:65` | `plugins/ndf/skills/pr-review/scripts/pr-review-steps.py` | `plugins/ndf/bin/ndf cross-review`（対応表 → M5） |
| R76 | `scripts/check-doc-staleness.py:5,8,62,724` | `plugins/ndf/README.md` | `plugins/ndf/README.md`（S2 が新しい根へ置く。行き先の表） |
| R77 | `scripts/check-markdown-links.py:8` | `plugins/ndf/scripts/lib/md.py` | `plugins/ndf/src/ndf/adapters/markdown.py` |
| R78 | `scripts/check-ndf-contracts.py:2` | `plugins/ndf/contracts/` | 同じパス（入れ替えの後も新しい側にある） |
| R79 | `scripts/check-ndf-contracts.py:98` | `plugins/ndf/hooks` | 同じパス（入れ替えの後も新しい側にある） |
| R80 | `scripts/check-ndf-contracts.py:4,77` | `plugins/ndf/scripts` | `plugins/ndf/src/ndf` |
| R81 | `scripts/check-ndf-contracts.py:100` | `plugins/ndf/scripts/` | `plugins/ndf/src/ndf/` |
| R82 | `scripts/check-ndf-contracts.py:114` | `plugins/ndf/scripts/data/branch-strategy-presets.json` | `plugins/ndf/contracts/data/branch-strategy-presets.json` |
| R83 | `scripts/check-ndf-contracts.py:118` | `plugins/ndf/scripts/data/branch-strategy.result.schema.json` | `plugins/ndf/contracts/schemas/result.schema.json` |
| R84 | `scripts/check-ndf-contracts.py:127` | `plugins/ndf/scripts/data/branch-strategy.schema.json` | `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` の `branch_strategy` |
| R85 | `scripts/check-ndf-contracts.py:115` | `plugins/ndf/scripts/data/ndf-settings-renames.json` | `plugins/ndf/contracts/data/ndf-settings-renames.json` |
| R86 | `scripts/check-ndf-contracts.py:77` | `plugins/ndf/skills` | 同じパス（入れ替えの後も新しい側にある） |
| R87 | `scripts/check-ndf-contracts.py:126` | `plugins/ndf/skills/development-workflow/schemas/project.schema.json` | 同じパス（入れ替えの後も新しい側にある） |
| R88 | `scripts/check-ndf-contracts.py:4` | `plugins/ndf/skills/*/scripts` | `plugins/ndf/src/ndf/domains/` |
| R89 | `scripts/check-pr-base.sh:5,30` | `plugins/ndf/scripts/lib/branch_strategy_cli.py` | `plugins/ndf/bin/ndf project branch-strategy` |
| R90 | `scripts/check-script-structure.py:4` | `plugins/ndf/` | 同じパス（入れ替えの後も新しい側にある） |
| R91 | `scripts/check-script-structure.py:43` | `plugins/ndf/contracts/limits.json` | 同じパス（入れ替えの後も新しい側にある） |
| R92 | `scripts/check-script-structure.py:100` | `plugins/ndf/scripts/` | `plugins/ndf/src/ndf/` |
| R93 | `scripts/check-script-structure.py:66` | `plugins/ndf/scripts/lib/README.md` | 消す（archive へ移す。行き先の表） |
| R94 | `scripts/check-script-structure.py:794,796` | `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | `plugins/ndf/bin/ndf refactor` |
| R95 | `scripts/check-script-structure.py:793,796` | `plugins/ndf/skills/cross-review/scripts/drive.py` | `plugins/ndf/bin/ndf cross-review` |
| R96 | `scripts/check-skill-frontmatter.py:23` | `plugins/ndf/skills` | 同じパス（入れ替えの後も新しい側にある） |
| R97 | `scripts/check-skill-frontmatter.py:4,45` | `plugins/ndf/skills/AUTHORING.md` | 同じパス（入れ替えの後も新しい側にある） |
| R98 | `scripts/check-skill-repo-assumptions.py:105` | `plugins/ndf/` | 同じパス（入れ替えの後も新しい側にある） |
| R99 | `scripts/check-skill-repo-assumptions.py:395` | `plugins/ndf/scripts/data/branch-strategy-presets.json` | `plugins/ndf/contracts/data/branch-strategy-presets.json` |
| R100 | `scripts/check-skill-repo-assumptions.py:394` | `plugins/ndf/scripts/data/branch-strategy.schema.json` | `plugins/ndf/contracts/schemas/ndf-settings/project.schema.json` の `branch_strategy` |
| R101 | `scripts/check-skill-repo-assumptions.py:398` | `plugins/ndf/scripts/lib/branch_strategy.py` | `plugins/ndf/src/ndf/domains/project/` |
| R102 | `scripts/check-skill-repo-assumptions.py:400` | `plugins/ndf/scripts/release_lib/land.py` | `plugins/ndf/src/ndf/domains/release/` |
| R103 | `scripts/check-skill-repo-assumptions.py:399` | `plugins/ndf/scripts/release_lib/strategy_route.py` | `plugins/ndf/src/ndf/domains/release/` |
| R104 | `scripts/check-skill-repo-assumptions.py:46` | `plugins/ndf/skills` | 同じパス（入れ替えの後も新しい側にある） |
| R105 | `scripts/check-skill-repo-assumptions.py:8,726` | `plugins/ndf/skills/AUTHORING.md` | 同じパス（入れ替えの後も新しい側にある） |
| R106 | `scripts/check-skill-shell-vars.py:51` | `plugins/ndf/skills/cross-refactoring` | 同じパス（入れ替えの後も新しい側にある） |
| R107 | `scripts/contracts_lib/core_checks.py:5,32` | `plugins/ndf/` | 同じパス（入れ替えの後も新しい側にある） |
| R108 | `scripts/contracts_lib/core_checks.py:103` | `plugins/ndf/hooks` | 同じパス（入れ替えの後も新しい側にある） |
| R109 | `scripts/contracts_lib/core_checks.py:102` | `plugins/ndf/hooks/*.json` | 同じパス（入れ替えの後も新しい側にある） |
| R110 | `scripts/contracts_lib/core_checks.py:254` | `plugins/ndf/scripts` | `plugins/ndf/src/ndf` |
| R111 | `scripts/contracts_lib/core_checks.py:29` | `plugins/ndf/scripts/lib/exit_codes.py` | `plugins/ndf/contracts/causes.json` |
| R112 | `scripts/contracts_lib/core_checks.py:30` | `plugins/ndf/scripts/lib/step_read.py` | `plugins/ndf/src/ndf/contracts/` |
| R113 | `scripts/contracts_lib/core_checks.py:104,254` | `plugins/ndf/skills` | 同じパス（入れ替えの後も新しい側にある） |
| R114 | `scripts/contracts_lib/draft.py:22` | `plugins/ndf/contracts/` | 同じパス（入れ替えの後も新しい側にある） |
| R115 | `scripts/contracts_lib/draft.py:9` | `plugins/ndf/scripts/lib/README.md` | 消す（archive へ移す。行き先の表） |
| R116 | `scripts/contracts_lib/limits_decl.py:32` | `plugins/ndf/` | 同じパス（入れ替えの後も新しい側にある） |
| R117 | `scripts/contracts_lib/limits_decl.py:1,17` | `plugins/ndf/contracts/limits.json` | 同じパス（入れ替えの後も新しい側にある） |
| R118 | `scripts/contracts_lib/limits_decl.py:3,18` | `plugins/ndf/scripts/lib/limits.py` | `plugins/ndf/src/ndf/contracts/`（表は `contracts/limits.json`） |
| R119 | `scripts/contracts_lib/model.py:9` | `plugins/ndf/contracts` | 同じパス（入れ替えの後も新しい側にある） |
| R120 | `scripts/contracts_lib/model.py:1` | `plugins/ndf/contracts/` | 同じパス（入れ替えの後も新しい側にある） |
| R121 | `scripts/contracts_lib/render.py:12` | `plugins/ndf/contracts/` | 同じパス（入れ替えの後も新しい側にある） |
| R122 | `scripts/lib/ndf_wrappers.py:1` | `plugins/ndf/scripts/lib/` | `plugins/ndf/src/ndf/` |
| R123 | `scripts/lib/py_imports.py:12` | `plugins/ndf/scripts/` | `plugins/ndf/src/ndf/` |
| R124 | `scripts/structure_checks/limits_rules.py:4` | `plugins/ndf/contracts/limits.json` | 同じパス（入れ替えの後も新しい側にある） |
| R125 | `scripts/structure_checks/limits_rules.py:25` | `plugins/ndf/scripts/` | `plugins/ndf/src/ndf/` |
| R126 | `scripts/structure_checks/limits_rules.py:14` | `plugins/ndf/scripts/experimental/` | 消す（実験版の置き場は #3381・#3388 が決める） |
| R127 | `scripts/token-usage.py:63,424` | `plugins/ndf/` | 同じパス（入れ替えの後も新しい側にある） |
| R128 | `scripts/validate-runtime-plugins.sh:58` | `plugins/ndf/README.md` | `plugins/ndf/README.md`（S2 が新しい根へ置く。行き先の表） |
| R129 | `tests/runtime-smoke/assertions/assert-hook-fixtures.sh:12` | `plugins/ndf/scripts/ensure-retention.sh` | 未作成（対応表 `entrypoint:scripts/ensure-retention.sh` → M3a） |
| R130 | `tests/runtime-smoke/assertions/assert-hook-fixtures.sh:14,18` | `plugins/ndf/scripts/wait-notify.py` | 未作成（対応表 `entrypoint:scripts/wait-notify.py` → AD2+） |

### Python の設定（17 行）

| # | 参照する場所 | 今の参照先 | 入れ替えの後の参照先 |
| ---: | --- | --- | --- |
| R131 | `conftest.py:13` | `plugins/ndf/conftest.py` | 消す（新しい側はテストを `uv run --project plugins/ndf` で走らせる。決定 78） |
| R132 | `conftest.py:84` | `plugins/ndf/scripts/tests/test_relay_hub.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R133 | `conftest.py:73` | `plugins/ndf/skills/cross-refactoring/tests` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R134 | `conftest.py:76` | `plugins/ndf/skills/cross-review/tests` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R135 | `conftest.py:70` | `plugins/ndf/skills/development-workflow/tests` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R136 | `conftest.py:80` | `plugins/ndf/skills/release/tests/test_slide_template.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R137 | `conftest.py:69` | `plugins/ndf/skills/worktree/tests` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R138 | `plugins/mcp/mcp-serena/pyproject.toml:4` | `plugins/ndf/pyproject.toml` | 同じパス（入れ替えの後も新しい側にある） |
| R139 | `pyproject.toml:3,55` | `plugins/ndf/pyproject.toml` | 同じパス（入れ替えの後も新しい側にある） |
| R140 | `pyproject.toml:81` | `plugins/ndf/scripts/tests/test_claude_accounts.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R141 | `pyproject.toml:74` | `plugins/ndf/scripts/tests/test_gh_call.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R142 | `pyproject.toml:77` | `plugins/ndf/scripts/tests/test_gh_pr_info.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R143 | `pyproject.toml:75` | `plugins/ndf/scripts/tests/test_gh_quota.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R144 | `pyproject.toml:76` | `plugins/ndf/scripts/tests/test_gh_rest.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R145 | `pyproject.toml:79` | `plugins/ndf/scripts/tests/test_project_mvv_normal.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R146 | `pyproject.toml:83` | `plugins/ndf/scripts/tests/test_relay.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |
| R147 | `pyproject.toml:82` | `plugins/ndf/scripts/tests/test_supervise.py` | `plugins/ndf/tests/`（新しい側のテストの置き場。根の `conftest.py`・`pyproject.toml` からは外す） |

## 作り直す生成物（533 本）

**今のプラグインの中身から作ったものは、向け直さずに作り直す。** 旧い行は消し、新しいプラグインの中身から作り直した行だけを残す。決定 73 の旧い本文が数えた「許可ファイル 526 個」は、2026-11-17 には許可ファイルの置き場 3 つで 599 本（うち `plugins/ndf/` を名指すのは 526 本）である。

| # | 置き場 | 本数 | 扱い | 作り直すコマンドか持ち主 |
| --- | --- | ---: | --- | --- |
| G1 | `scripts/contract-allow/` | 255 | 旧い許可を消し、新しい中身で作り直す | 入れ替えの PR（手順 S2-5）。許可の要否は新しいプラグインの検査（`plugins/ndf/bin/ndf check`）が決める |
| G2 | `scripts/script-structure-allow/` | 189 | 同上 | 同上 |
| G3 | `scripts/repo-assumptions-allow/`（155 本のうち `plugins/ndf/` を名指す分） | 82 | 同上 | 同上 |
| G4 | `scripts/pytest-shard-seconds.json` | 1 | 新しいテストの実測秒で作り直す | `scripts/pytest_shard.py seconds`（ファイルの `summary` が名指すコマンド） |
| G5 | `scripts/ndf-vulture-keep.txt` | 1 | 旧い行を消す。新しいプラグインの許可は新しい検査が持つ | 入れ替えの PR |
| G6 | `scripts/legacy-branch-decl.json` | 1 | 旧いパスの行を消す（#3672 の削除の一覧。消えたファイルを見る必要が無い） | 入れ替えの PR |
| G7 | `scripts/repo-secrets-allow.json` | 1 | 旧いテストのパスの行を消す | 入れ替えの PR |
| G8 | `scripts/ndf-next-parity/parity/trial/2026-11-15.json`・`2026-11-16.json` | 2 | 書き換えない（試走の記録で、書いた時点の値を残す） | — |
| G9 | `.ndf/project.json` | 1 | `docs.tested` の 194 箇所は新しいパスで作り直す。`$schema` は同じパス（`skills/development-workflow/schemas/project.schema.json` は新しいプラグインにある。中身の食い違いは設計文書の決定 12）。`test.suites` の `instructions-check.py` のコマンドは R42 と同じ行き先へ | 入れ替えの PR。`.ndf/` の書き換えは C7 に当たり、リリース承認ゲートで人が見る |

合計は 255 + 189 + 82 + 1 + 1 + 1 + 1 + 2 + 1 = 533 本である。

## 根の検査のテスト（26 本）

`scripts/tests/` の次の 26 本は、根の検査（実行する参照の表の「根の検査」）が今のプラグインを読む形を確かめる。**検査と同じ PR で、新しいプラグインを読む形へ書き直すか消す。** 書き直す形は検査の書き直し（手順 S2-5）が決める。

- `doc_staleness_helpers.py`
- `test_check_legacy_branch_decl.py`
- `test_check_markdown_links.py`
- `test_check_ndf_contracts.py`
- `test_check_script_structure.py`
- `test_check_skill_repo_assumptions.py`
- `test_ci_heavy_skip.py`
- `test_cross_review_codistribution.py`
- `test_cross_skill_refs.py`
- `test_distribution_runtimes.py`
- `test_doc_staleness.py`
- `test_docs_decl.py`
- `test_external_ai_codistribution.py`
- `test_lock_common.py`
- `test_push_fallback_docs.py`
- `test_refactoring_codistribution.py`
- `test_root_conftest.py`
- `test_shared_lib_layout.py`
- `test_skill_frontmatter_yaml.py`
- `test_skill_positional_args.py`
- `test_skill_shell_vars.py`
- `test_token_usage.py`
- `test_token_usage_ledger.py`
- `test_token_usage_snapshot.py`
- `test_validate_manifests_version.py`
- `test_vocabulary_single_source.py`

## 文書（206 本）

**文書の行き先と扱いは [docs/ndf-swap-doc-destinations.md](../docs/ndf-swap-doc-destinations.md) の行き先の表が持ち、入れ替えの PR はその表どおりに直す。** この課題が持つのは、入れ替えで切れる参照の向け直しだけで、行き先と整理の判断は #3382 が持つ。

| # | 置き場 | 本数 | 行き先の表の行 |
| --- | --- | ---: | --- |
| W1 | `docs/specifications/` | 177 | 「`docs/specifications/`」: 固定の URL へ向ける |
| W2 | `docs/` 直下の 6 本（`glossary.md`・`ndf-experiments.md`・`ndf-plugin-reference.md`・`ndf-swap-doc-destinations.md`・`plugin-development-guide.md`・`versioning-and-distribution.md`） | 6 | 各本の行（`ndf-experiments.md` は archive へ移す。`ndf-swap-doc-destinations.md` は行が無い。下の注記） |
| W3 | `docs/development-history/` | 5 | 固定の URL へ向ける |
| W4 | `docs/ndf-12-survey/` | 4 | 行が無い（下の注記） |
| W5 | `docs/metrics/` | 4 | 「そのまま」（リンクでなく本文の語として名指す。下の注記） |
| W6 | `docs/scripts-map/` | 3 | archive へ移す |
| W7 | `docs/archive/` | 2 | 固定の URL へ向ける |
| W8 | `docs/presentations/`・`docs/articles/`・`docs/changelog/` | 3 | 固定の URL へ向ける |
| W9 | `docs/glossary/` | 1 | 固定の URL へ向ける |
| W10 | `CHANGELOG.md` | 1 | 書き直す（入れ替えで切れるリンクとパスだけ） |

合計は 177 + 6 + 5 + 4 + 4 + 3 + 2 + 3 + 1 + 1 = 206 本である。

**行き先の表に無いもの・表と事実が合わないものは、入れ替えの PR で決めずに #3382 へ送る。** 2026-11-17 に次の 3 件を見つけた。

- `docs/ndf-12-survey/`（4 本）の行が無い。`AGENTS.md` の表では「書いた日時点の記録」なので、`docs/development-history/` と同じ扱い（固定の URL へ向ける）が当たると読めるが、決めるのは #3382 である
- `docs/metrics/` は「今のプラグインへのリンクを持たない」とされているが、4 本が本文の語として `plugins/ndf/` を名指す。リンクの検査は落ちない
- 行き先の表自身が、入れ替えの起点のコミットを `git merge-base origin/develop HEAD` と書いている。`develop` は #3672 で無くなったため、起点は `git merge-base origin/main HEAD` で求める（入れ替えの手順 S2-6 はこの形で書いた）
