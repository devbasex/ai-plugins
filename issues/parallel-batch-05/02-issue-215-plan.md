# 215 の実装プラン — 配布先ランタイムへ agy を加える

設計は [02-issue-215.md](02-issue-215.md) にある。この文書は**分解**だけを扱う。受け入れ条件
（A1〜A14）・決定の記録・テスト設計は設計文書の側にあり、ここへは写さない。

## 関連リンク

- 課題: [#215](https://github.com/devbasex/ai-plugins/issues/215)
- 設計: [02-issue-215.md](02-issue-215.md)
- 境界と順序: [00-overview.md](00-overview.md)

## モード

`architecture`。配布先ランタイムという構成上の単位が 3 から 4 へ増え、検査・生成・説明文書の
すべてが影響を受ける。

## 目的と非目的

達成したい状態:

- Skill・エージェント・hook を agy へも配れる。配る基準は `manifests/agy-skills.txt` だけが持つ
- 既存 3 ランタイムの配布数（33 / 31 / 32）が変わらない

やらないこと:

- 参加 CLI としての agy（#214、担当 A）
- `rules/AGENTS.md` による常時注入（設計の決定 3）
- agy 向けの取得元の登録（登録の手段が無い）

## 前提

- 前提 1: `agy plugin validate <ディレクトリ>` は `plugin.json` の存在で対象を判別する。
  実測（agy 1.1.25）で `plugins/ndf` は終了コード 1、`plugins/playwright-kit` は 0 で終わる
- 前提 2: `agy plugin install` は `~/.gemini/config/plugins/<name>/` へ複製し、symlink は実体へ
  解決される
- 前提 3: hook の実行時の作業ディレクトリは導入先のプラグインディレクトリである（実測）

## 着手の順序

**先行してよい**（担当 A・F と接しない）:

- `plugins/ndf/dev.agy/` 一式、`manifests/agy-skills.txt`
- `scripts/` の生成と検査、`tests/runtime-smoke/`
- 説明文書一式

**担当 A のマージを待つ**:

- `plugins/ndf/scripts/lib/worktree-common.sh` / `worktree-guard.sh` / `worktree-session.sh`
- `plugins/ndf/skills/worktree/tests/` の新しいテスト（上の 3 本に依存する）
- `dev.agy/hooks.json` の `matcher`（`wt_tool_matcher` の出力と一致させる）

**担当 F のマージを待つ**:

- 「`$SCRIPTS` を決める」節の候補の一覧と、`worktree/tests/test_scripts_reference.py`

## 修正対象

| 区分 | パス |
| --- | --- |
| 新設 | `plugins/ndf/dev.agy/{plugin.json,hooks.json,agents,scripts,skills/*}` |
| 新設 | `plugins/ndf/manifests/agy-skills.txt` |
| 新設 | `tests/runtime-smoke/{Containerfile.agy,adapters/agy.sh}` |
| 新設 | `scripts/tests/test_agy_distribution.py` ほか agy の検査のテスト 3 本 |
| 新設 | `plugins/ndf/skills/worktree/tests/test_agy_hooks.py`（担当 A のマージ後） |
| 変更 | `scripts/{build-runtime-plugins.sh,validate-runtime-plugins.sh,runtime-smoke-test.sh}` |
| 変更 | `scripts/{check-skill-frontmatter.py,check-doc-staleness.py}` |
| 変更 | `tests/runtime-smoke/assertions/{assert-plugin-files.sh,assert-authenticated-smoke.sh,assert-no-host-contamination.sh}` |
| 変更 | `README.md` / `AGENTS.md` / `CLAUDE.md` / `plugins/ndf/README.md` / `.claude-plugin/marketplace.json` |
| 変更 | `plugins/ndf/skills/README.md` / `docs/specifications/` / `docs/plugin-development-guide.md` |
| 変更 | `conftest.py`（生成物を収集の対象から外す） / `.github/workflows/runtime-plugin-*.yml` |

## タスク分解

### Task 1: ルート直下の `plugin.json` を置かないことを固定する

- **対象:** `scripts/tests/test_agy_distribution.py`
- **変更内容:** `plugins/<family>/plugin.json` を置くと Codex の配布数が変わるため、`ndf` に
  それが無いことと、4 ランタイムの manifest の行数（33 / 31 / 32 / 31）を検査する
- **満たす受け入れ条件:** A12
- **進め方:** 先に失敗するテストを書く（`agy-skills.txt` がまだ無い状態で落ちる）

### Task 2: 配布の基準と生成物

- **対象:** `plugins/ndf/manifests/agy-skills.txt`、`scripts/build-runtime-plugins.sh`
- **変更内容:** `codex-skills.txt` と同じ 31 個から始める。`dev.agy/skills/` の symlink を
  基準から生成し、`--check` で不足・余分・向き先の違いを検出する
- **満たす受け入れ条件:** A8、A12
- **進め方:** 失敗するテスト（symlink を 1 本消す / 余分を足す）→ 生成の実装

### Task 3: プラグイン定義と静的検査

- **対象:** `plugins/ndf/dev.agy/{plugin.json,hooks.json,agents,scripts}`、
  `scripts/validate-runtime-plugins.sh`
- **変更内容:** `dev.agy` を agy のプラグインとして宣言する。版数と Skill 数を Claude 版
  `plugin.json` と `agy-skills.txt` へ突き合わせ、`agy plugin validate` を検査へ入れる
- **満たす受け入れ条件:** A1、A9
- **進め方:** 版数を古くした木で落ちることをテストで確かめてから実装する

### Task 4: 初期一覧の予算

- **対象:** `scripts/check-skill-frontmatter.py`
- **変更内容:** 対応ランタイムの一覧（予算・manifest の読み取り・重大度）へ agy を足す。
  予算は Kiro CLI と同じく Claude Code の 1% を借り、借りたことを注記に残す
- **満たす受け入れ条件:** A10

### Task 5: 説明文書の検査

- **対象:** `scripts/check-doc-staleness.py`、`scripts/tests/test_doc_staleness.py`
- **変更内容:** ランタイムの対応表へ agy を足し、`dev.agy/plugin.json` の `version` と
  `description` を突き合わせの対象へ入れる（版数を持つ箇所が 13 から 15 になる）
- **満たす受け入れ条件:** A11

### Task 6: 説明文書

- **対象:** `README.md` / `AGENTS.md` / `CLAUDE.md` / `plugins/ndf/README.md` /
  `.claude-plugin/marketplace.json` / `plugins/ndf/skills/README.md` / `docs/`
- **変更内容:** 3 ランタイム構成の記述を 4 ランタイムへ書き直し、agy の導入手順を足す。
  担当 A から引き渡された `gemini` の記述もここで直す
- **満たす受け入れ条件:** A11、A13

### Task 7: 実機確認

- **対象:** `scripts/runtime-smoke-test.sh`、`tests/runtime-smoke/`
- **変更内容:** `--runtime agy` を足し、容器の中で `agy plugin install` と
  `agy plugin list` を実行して取り込んだ要素を固定する
- **満たす受け入れ条件:** A2、A3、A7、A13

### Task 8: 誘導の経路（担当 A のマージ後）

- **対象:** `plugins/ndf/scripts/lib/worktree-common.sh`、`worktree-guard.sh`、
  `worktree-session.sh`、`dev.agy/hooks.json`、`plugins/ndf/skills/worktree/tests/`
- **変更内容:** `PreToolUse` で控えの `pending` へ積み、`PreInvocation` の
  `injectSteps[].userMessage` で渡す
- **満たす受け入れ条件:** A4、A5、A6

### Task 9: 解決手順の候補（担当 F のマージ後）

- **対象:** `development-workflow/references/projects-tracking.md`、
  `development-workflow/tests/test_projects_scripts_lookup.py`、
  `worktree/tests/test_scripts_reference.py`
- **変更内容:** `~/.gemini/config/plugins/ndf/scripts` を候補へ足し、2 つのテストへ同じ配置を作る
- **満たす受け入れ条件:** A14

## 設計から変えたこと

### `.agents/` は置かない

設計の構成要素は `.agents/plugins.json` を挙げていた。**実測で、作業領域に置いたプラグインを
agy が読み込まないことが分かった。**

公式ドキュメント（antigravity.google/docs/plugins）は作業領域の探索先を `.agents/plugins/` と
書き、`plugins.json` には触れていない。書かれているとおりに `.agents/plugins/ndf` を置いて
確かめた。symlink でも実体の複製でも、読み込まれている Skill の一覧は組み込みの 2 個だけで、
`agy plugin list` も `No imported plugins.` を返した。

```console
$ ls .agents/plugins/ndf
agents  hooks.json  plugin.json  scripts  skills
$ agy plugin list
No imported plugins.
$ agy --output-format text -p="いま読み込めている Skill の名前を、1 行に 1 つだけ列挙して。"
agy-customizations
antigravity-guide
```

読み込ませる手段は `agy plugin install` だけである。**受け入れ条件（A1〜A14）はどれも
`.agents/` に依らない**ため、動かないファイルを配布物へ残さず、開発時も導入の手順を使う。

## リスクと対処

| リスク | 対処 |
| --- | --- |
| ルート直下へ `plugin.json` を置いてしまい Codex の配布数が変わる | Task 1 のテストが置かないことを固定する |
| `dev.agy/skills/` の symlink が基準からずれる | `build-runtime-plugins.sh --check` が検出する |
| `agy` が無い環境で検査が落ちる | `claude plugin validate` と同じく読み飛ばし、その旨を出力する |

## 完了の定義

- [ ] A1〜A14 のうち、担当 A・F のマージを待つもの以外を満たす
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が 1569 件以上で通る
- [ ] `bash scripts/validate-runtime-plugins.sh` と `python3 scripts/check-doc-staleness.py --root .`
      が終了コード 0 で終わる
- [ ] `agy plugin validate plugins/ndf/dev.agy` が終了コード 0 で終わる
