# Skill の棚卸

用語は [01-overview.md](01-overview.md) を参照。

## 利用実績の実測

2026-05-20 〜 2026-08-07（80 日）の会話ログ 1,938 セッション / 20 プロジェクトを解析した。集計対象は、エージェントが自動起動した回数と利用者がコマンドで明示起動した回数の合計である。

| Skill | 計 | 自動 | 明示 | 最終利用 | 利用 PJ 数 |
| --- | ---: | ---: | ---: | --- | ---: |
| `fix` | 299 | 235 | 64 | 2026-08-07 | 9 |
| `cross-review` | 285 | 14 | 271 | 2026-08-07 | 16 |
| `merged` | 247 | 0 | 247 | 2026-08-07 | 15 |
| `pr` | 174 | 3 | 171 | 2026-08-07 | 16 |
| `issue-plan-strategy` | 140 | 37 | 103 | 2026-08-07 | 13 |
| `review` | 58 | 1 | 57 | 2026-08-06 | 7 |
| `markdown-writing` | 41 | 21 | 20 | 2026-08-07 | 10 |
| `implementation-plan` | 33 | 33 | 0 | 2026-08-07 | 10 |
| `investigation-rules` | 30 | 30 | 0 | 2026-08-07 | 10 |
| `cherry-pick-pr` | 16 | 1 | 15 | 2026-06-10 | 2 |
| `docker-container-access` | 6 | 6 | 0 | 2026-07-26 | 5 |
| `browser-test` | 6 | 6 | 0 | 2026-06-04 | 1 |
| `playwright-browser-connect` | 5 | 5 | 0 | 2026-06-15 | 4 |
| `google-auth` | 5 | 5 | 0 | 2026-07-18 | 4 |
| `problem-solving` / `codex` / `branch-fix-strategy` | 各 4 | 4 | 0 | 〜2026-07-26 | 2〜4 |
| `review-branch` / `playwright-execution` / `statusline` / `playwright-scenario-test` | 各 3 | | | 〜2026-06-16 | 1〜3 |
| `playwright-kit-ops` / `google-drive` / `pr-tests` / `ml-model-structure` | 各 2 | | | 〜2026-07-31 | 1〜2 |
| `sync-main` / `playwright-test-planning` / `gemini` / `deepwiki-transfer` | 各 1 | | | 〜2026-07-22 | 1 |
| 起動ゼロ 20 個 | 0 | 0 | 0 | — | 0 |

起動ゼロの 20 個:

```text
clean, data-analyst-export, data-analyst-sql-optimization, deploy,
git-gh-operations, google-chat, knowledge-reorg, logging-guidelines,
mcp-builder, ndf-policies, official-skills-autoloader, plan-to-spec,
playwright-evidence-drive, playwright-report, playwright-script-creation,
python-execution, qa-security-scan, resolve-pr-comments,
review-pr-comments, skill-stats
```

### 起動ゼロの解釈

起動ゼロには「需要がなかった」と「トリガが発火しなかった」の 2 種類がある。利用者の発話に該当語が含まれた回数を「機会」として数えて切り分けた。

| Skill | 起動 | 機会 | 解釈 |
| --- | ---: | ---: | --- |
| `deploy` | 0 | 340 | 明示指示専用だが利用者が一度も打っていない。別手段で代替されている |
| `clean` | 0 | 251 | 機会数が `merged` の起動数 247 とほぼ一致する。ブランチ整理は `merged` で行われている |
| `qa-security-scan` | 0 | 66 | 機会があるのに発火しない |
| `data-analyst-export` | 0 | 45 | 同上 |
| `official-skills-autoloader` | 0 | 43 | 同上 |
| `python-execution` | 0 | 38 | 同上 |
| `logging-guidelines` | 0 | 3 | 機会自体がほぼない |
| `plan-to-spec` | 0 | 1 | 機会自体がほぼない |
| `git-gh-operations` / `knowledge-reorg` / `google-chat` / `mcp-builder` | 0 | 0 | 需要がない |

`ndf-policies` は `user-invocable: false` で説明のみを常時注入する設計のため、起動ゼロは想定どおりであり判定対象外とする。

### 測定の限界

- 機会の判定は正規表現によるキーワード一致であり、単なる言及も数えるため過大に出る
- 対象は 1 名の 20 プロジェクト / 80 日分であり、チーム全体の傾向ではない
- `disable-model-invocation: true` の Skill は自動起動しない設計のため、自動起動数は常にゼロになる

### 測定ツールの不具合

`skill-stats` は次の 2 点で今回の測定に使えず、集計は個別に実装した。Task 0-1 の前提となるため、修正するか置き換えるかの判断が要る。

- 49 個中 48 個で `when_to_use` からのトリガ抽出に失敗し、ヒット率が算出されない
- 利用者のスラッシュコマンドを数えず、エージェントの自動起動しか数えない。`cross-review` を 14 と報告するが実際は 285

## 統合

**上位 5 個（`fix` `cross-review` `merged` `pr` `issue-plan-strategy`）のコマンド名は変更しない。** 合計 1,145 回の起動実績があり、改名は日常運用を直接壊す。統合する場合も利用実績の多い側の名前を残す。

| 統合後 | 統合元（起動数） | 増減 |
| --- | --- | --- |
| `fix` | `fix`(299) + `review-pr-comments`(0) + `resolve-pr-comments`(0) | -2 |
| `merged` | `merged`(247) + `clean`(0、機会 251) | -1 |
| `review` | `review`(58) + `review-branch`(3) | -1 |
| `cherry-pick-pr` | `cherry-pick-pr`(16) + `branch-fix-strategy`(4) | -1 |
| `external-ai` | `codex`(4) + `gemini`(1) | -1 |
| ブラウザ自動テスト 3 個 | 既存 8 個 + `browser-test`（計 20 回） | -6 |

合計 **-12**（49 → 37）。

統合の方向は実績に従う。`merged` を `git-cleanup` のような新名へ改名しない。`cherry-pick-pr`(16) は知識 Skill の `branch-fix-strategy`(4) より使われているため、実行コマンド側の名前を残す。

統合は単純連結ではなく、重複記述を落として再構成する。各統合で統合前後の行数を記録し、増えていれば再構成し直す。

## 削除

起動ゼロかつ機会ゼロのもの、および現在のモデルが自力で代替できるものを削除する。

| Skill | 起動 / 機会 | 理由 |
| --- | --- | --- |
| `git-gh-operations` | 0 / 0 | 需要がない。Git とコマンドラインツールの一般操作は現在のモデルが熟知 |
| `knowledge-reorg` | 0 / 0 | 需要がない |
| `google-chat` | 0 / 0 | 需要がない |
| `mcp-builder` | 0 / 0 | 需要がない |
| `python-execution` | 0 / 38 | 機会はあるが、実行環境の検出は現在のモデルが自力で行える |
| `data-analyst-export` | 0 / 45 | データ分析エージェントの定義に直接書けば足りる |
| `data-analyst-sql-optimization` | 0 / 0 | 同上 |
| `deepwiki-transfer` | 1 | 最終利用 2026-05-21。以降 2 か月半使われていない |
| `sync-main` | 1 | 最終利用 2026-07-22。Git 操作 1 コマンドに 48 行を割いている |

合計 **-9**（37 → 28）。

## 発動改善

起動ゼロだが機会があるものは、削除ではなく発動条件を見直す。

| Skill | 起動 / 機会 | 対応 |
| --- | --- | --- |
| `deploy` | 0 / 340 | 明示指示専用のまま使われていない。実際の運用手順を確認し、不要なら削除、必要なら周知と `description` 改善 |
| `qa-security-scan` | 0 / 66 | `description` に発動条件を含めて自動発動させる |
| `official-skills-autoloader` | 0 / 43 | 各ランタイムの公式 Skill 提供状況を確認したうえで、発動条件を見直すか削除する |
| `logging-guidelines` | 0 / 3 | `paths` でコード変更時に限定する |
| `plan-to-spec` | 0 / 1 | 機会自体が少ない。運用に組み込まれていないため、[04-development-skills.md](04-development-skills.md) の改修とあわせて発動条件を設計し直す |
| `skill-stats` | 0 / — | 測定機能が壊れている。修正するか、今回の集計方法へ置き換える |

## 自動発動の実態

`fix` の自動起動 235 回は**すべてサブエージェントのセッション**で発生しており、`cross-review` のループからの内部呼び出しである。利用者の自然文から独立して発見された起動は 64 回の明示指示のみとなる。

`merged`(247) `pr`(171) `cross-review`(271) `review`(57) は、いずれもほぼ全数が利用者の明示指示である。これらは `disable-model-invocation: true` が付いており自動起動できない。自然文からの発動を有効にする対象として優先度が高い。

一方 `implementation-plan`(33) `investigation-rules`(30) は全数が自動起動であり、現在の `when_to_use` が機能している例といえる。

## frontmatter 規約

`plugins/ndf-shared/skills/README.md` に明文化し、`scripts/check-skill-frontmatter.py` で機械検査する。

### `description` を単一の真実とする

3 ランタイムで共通に効くのは Agent Skills 仕様の 6 項目だけなので、**発動判定に必要な情報はすべて `description` に入れる**。Claude Code 独自項目はその上乗せとして扱う。詳細は [03-runtime-conformance.md](03-runtime-conformance.md)。

```yaml
# 3 ランタイムのいずれでも発動判定できる書き方
description: "Review a PR or local branch diff and post an approve/changes verdict. Use when asked to review a PR, check a diff before merge, or self-review a branch (レビューして / PR確認 / マージ前チェック)."
when_to_use: "Claude Code 向けの追加トリガのみ"
```

### 発動制御の 4 分類

| 分類 | Claude Code | Codex | Kiro | 対象 |
| --- | --- | --- | --- | --- |
| 自動発動（既定） | `when_to_use` 併記 | 既定で暗黙起動可 | 自動ロード | 知識・判断基準・ワークフロー |
| パス限定自動発動 | 上記 + `paths` | `paths` 無効 | `paths` 無効 | 特定ディレクトリでのみ意味を持つもの |
| 明示指示専用 | `disable-model-invocation: true` + `argument-hint` | `agents/openai.yaml` の `policy.allow_implicit_invocation: false` | 制御手段なし。`description` に「利用者が明示的に指示したときのみ実行する」と記載 | 破壊的操作・外部への書き込み |
| 常時注入のみ | `user-invocable: false` | 相当機能なし | 相当機能なし | `ndf-policies` |

`disable-model-invocation: true` の Skill は `description` がコンテキストへ載らない。`user-invocable: false` は載る。

### 適用方針

- `merged`(247) / `pr`(171) / `cross-review`(271) / `review`(57) / `pr-tests`(2) から `disable-model-invocation` を外し、`description` に発動条件を含める。いずれも明示指示でしか使えていない
- `deploy` は環境ブランチへ書き込む破壊的操作のため明示指示専用を維持する。ただし起動ゼロなので存続自体を判断する
- `plan-to-spec` と `cross-review` は `description` に長文を入れているため、`when_to_use` へ移して `description` を短くする
- 広すぎるトリガを具体化する（`'python'` → `'uv run'` `'venv が見つからない'`、`'git add'` → `'fatal:'` `'non-fast-forward'`、`'調査'` → `'調査レポートを書く'`）
- frontmatter に `<` と `>` を含めない。Agent Skills 仕様がシステムプロンプトへの注入リスクとして警告している
- `description` は二重引用符で囲む。Kiro はコロンを含む未引用の `description` を持つ Skill を検出対象から落とす（[kirodotdev/Kiro#8329](https://github.com/kirodotdev/Kiro/issues/8329)）

### 上限値

| 項目 | 上限 | 根拠 |
| --- | --- | --- |
| `name` | 64 文字、小文字英数とハイフンのみ、先頭末尾ハイフン不可、連続ハイフン不可、親ディレクトリ名と一致 | Agent Skills 仕様（必須） |
| `description` | 1,024 文字。運用目標は 300 文字以内 | 仕様上限 / 運用目標 |
| `description` + `when_to_use` | 1,536 文字を超えると一覧で切り詰められる | Claude Code |
| `SKILL.md` 行数 | 500 行。超えるものは補助ファイルへ分割 | 仕様の推奨、コンパクション対策 |
| `SKILL.md` 本文 | 5,000 トークン | 仕様の推奨 |
| 全 Skill の frontmatter 合計 | 棚卸完了時点の実測値を基準に設定 | 独自 |
| `compatibility` | 500 文字 | Agent Skills 仕様 |

運用目標の 300 文字は仕様上限より厳しい。「何をするか + 主要トリガ」を入れて 3 ランタイムで発動させるには 1 行では足りないが、49 Skill 分が常時注入されるため、300 文字 × 40 個でも 12,000 文字に達する。仕様上限は 1 個で使い切ってよい量ではない。

Claude Code のコンパクション後は、呼び出し済み Skill の先頭 5,000 トークンのみが再添付され、全体で 25,000 トークンの共通予算を新しい順に消費する。480 行級の Skill は圧縮後に後半が失われる。500 行上限は推奨ではなく必須条件として扱う。

### 未使用項目の導入

Claude Code は 17 項目を提供するが、現在使っているのは 7 項目（`name` / `description` / `when_to_use` / `argument-hint` / `allowed-tools` / `disable-model-invocation` / `user-invocable`）である。

| 項目 | 適用先 |
| --- | --- |
| `paths` | `ml-model-structure`（`analysis/**`）、ブラウザ自動テスト群（`tests/**` `e2e/**`）、`logging-guidelines`、`python-execution`（残す場合） |
| `context: fork` + `background: false` | `cross-review`（長時間実行をメインコンテキストから隔離）。組み込みの `/goal` と併用する Skill には使わない。セッション単位の Stop フックとして動く評価器が分離実行では働かない |
| `effort: high` | `design-review`、`domain-modeling`、`review` |
| `arguments` | `argument-hint` を持つ全 Skill。引数の手動解析を名前付き引数に置き換える |
| `license` / `metadata` | 上流由来 Skill に参照元名・固定コミット・ライセンスを記録 |

`context: fork` は `background: false` を付けないと結果が非同期で返る。利用者の判断を仰ぐ必要がある Skill では `background: false` を明示する。

## 仕様準拠状況

Agent Skills 仕様と Claude Code の項目定義に照らして全 49 Skill を検査した。**違反はない。**

| 検査項目 | 上限 | 実測 | 判定 |
| --- | --- | --- | --- |
| `name` が親ディレクトリ名と一致 | 必須 | 49/49 一致 | 適合 |
| `name` の文字種 | 64 文字 | 違反なし | 適合 |
| `description` 長 | 1,024 文字 | 最大 401（`plan-to-spec`） | 適合 |
| `description` + `when_to_use` 合計 | 1,536 文字 | 最大 401 | 適合 |
| `SKILL.md` 行数 | 500 行 | 最大 484（`playwright-browser-connect`） | 上限間際 |
| frontmatter 合計 | — | 8,900 文字 | 基準値として記録 |
| `description` 内の未引用コロン | Kiro が検出対象から落とす | 該当 1 個。二重引用符付きのため回避済み | 適合 |
