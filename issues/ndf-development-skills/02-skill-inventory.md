# Skill の棚卸

用語は [01-overview.md](01-overview.md) を参照。

## 統合

| 統合後 | 統合元 | 増減 |
| --- | --- | --- |
| `external-ai` | `codex` + `gemini` | -1 |
| `review` | `review` + `review-branch`（`--branch` 引数で切替） | -1 |
| `git-cleanup` | `clean` + `merged` + `sync-main` | -2 |
| `fix` | `review-pr-comments` + `fix` + `resolve-pr-comments` | -2 |
| `data-analyst-ops` | `data-analyst-export` + `data-analyst-sql-optimization` | -1 |
| `branch-fix-strategy` | `branch-fix-strategy` + `cherry-pick-pr` | -1 |
| ブラウザ自動テスト 4 個 | 既存 8 個 + `browser-test`（結果報告・証跡保管・接続設定・スクリプト運用を補助ファイルへ退避） | -5 |

合計 **-13**（49 → 36）。

統合は単純連結ではなく、重複記述を落として再構成する。各統合で「統合前の合計行数」と「統合後の行数」を記録し、増えていれば再構成し直す。

## 整理候補

次の 8 個は「現在のモデルが自力でできる」または「利用実績が乏しい」疑いがある。**削除を先に決めず、`skill-stats` による実測起動率を根拠に判定する。**

| Skill | 疑う理由 |
| --- | --- |
| `python-execution` | 実行環境の検出は現在のモデルが自力で行える。トリガ `'python'` が広すぎる |
| `git-gh-operations` | Git とコマンドラインツールの一般操作は現在のモデルが熟知。トリガ `'git add'` 等が全セッションにヒット |
| `deepwiki-transfer` | 用途が限定的 |
| `google-chat` | 用途が限定的 |
| `mcp-builder` | `when_to_use` 未設定で自動発動しておらず、利用実績が不明 |
| `ml-model-structure` | 対象プロジェクトが限定的 |
| `skill-stats` | 棚卸の道具としては必要だが、常設が必要かは要判断 |
| `official-skills-autoloader` | ランタイム側の公式 Skill 提供状況に依存するため、現状を確認して判断 |

判定は「維持 / `paths` でスコープ限定 / 縮小 / 削除」の 4 択とする。用途が限定的なだけのものは `paths` を優先する。削除せず残す場合も、プロジェクト固有の落とし穴だけを残して縮小する（`git-gh-operations` は 228 行 → 40 行以下を目安）。

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

- `review` / `pr` / `pr-tests` / `git-cleanup` から `disable-model-invocation` を外し、`when_to_use` を付与する
- `deploy` と `cherry-pick-pr` 相当の破壊的操作は明示指示専用を維持する
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
| `context: fork` + `background: false` | 一気通貫実行機能、`cross-review`（長時間実行をメインコンテキストから隔離） |
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
