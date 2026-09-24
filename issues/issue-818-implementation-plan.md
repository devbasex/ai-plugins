# #818: Claude Code と Codex で言語サーバを使えるようにする — 実装計画

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/818
- 要求と受け入れ条件: [issue-818-requirements.md](issue-818-requirements.md)
- 設計: [issue-818-design.md](issue-818-design.md)
- 決定の記録: [issue-818-design-decisions.md](issue-818-design-decisions.md)
- テスト設計と未確認: [issue-818-design-tests.md](issue-818-design-tests.md)
- 設計の Pull Request: #957（関門 1 で承認・マージ済み）

## モード

standard（複数ファイル・新しいスクリプトと hook・公開インタフェースの変更。設計を関門 1 で通した）

## 目的と非目的

達成したい状態:

- 設計の「構成要素」の表のとおりに `plugins/mcp/mcp-serena/` を作り、受け入れ条件の単体・結合の分をテストで満たす
- 実機の確認（AC3・AC8・AC11・AC12・AC24〜AC27）を記録し、未確認 U2〜U7 を決める

やらないこと:

- 設計の「含まない」の各項目（起動のラッパー・名前の短縮・NDF の `lspServers`・Kiro / agy の調整・エージェント定義の構成の整理・devbase#236・#893）
- 版の更新（配布の工程が決める）

## 受け入れ条件の食い違いの解消（AC4b と AC18）

AC4b は「`configure` を打っていないリポジトリで SessionStart が未設定を知らせる」と求め、AC18 は「印の無いリポジトリでは何も出さない」と求める。AC4b は設計の関門の直前に利用者の指示で足したもので、AC18 の狙い（Serena が自動で作った `project.yml` の食い違いを毎回知らせない）とは両立できる。**次のとおり AC18 を書き換える。**

| リポジトリの状態 | SessionStart が出すもの |
| --- | --- |
| git のリポジトリでない・採る言語が 0 | 何も出さない |
| 印（`mcp_serena_excluded`）が無い（`project.yml` が無い・Serena が自動で作った）で、採る言語が 1 つ以上 | **未設定の 1 行と Skill の名前の 1 行だけ**（「プロジェクトごとに実行する」を含む）。食い違いと導入の欠けの行は出さない |
| 印がある | AC17 のとおり（食い違いか欠けがあるときだけ） |

「設計の『hook の振る舞い』」の節は設計文書に無かった。上の表をその節として設計文書に足す。PreToolUse は印が無ければ数えない（AC18 の後半のまま）。

## 修正対象

| ファイル | 区分 |
| --- | --- |
| `plugins/mcp/mcp-serena/.mcp.json` / `.codex.mcp.json` | 変える / 新設 |
| `plugins/mcp/mcp-serena/.claude-plugin/plugin.json` / `.codex-plugin/plugin.json` | 変える |
| `plugins/mcp/mcp-serena/scripts/languages.json` / `serena-lsp.py` / `serena_lsp/*.py` | 新設 |
| `plugins/mcp/mcp-serena/hooks/hooks.json` / `hooks/codex.json` | 変える / 新設 |
| `plugins/mcp/mcp-serena/skills/language-servers/SKILL.md` | 新設 |
| `plugins/mcp/mcp-serena/tests/test_serena_lsp_*.py` | 新設（全体の `pytest .` で名前が衝突しないよう接頭辞を付ける） |
| `plugins/mcp/mcp-serena/README.md` / `docs/serena-guide.md` | 変える |
| `plugins/mcp/mcp-serena/dev.kiro/install.sh` | 生成物（`bash scripts/build-runtime-plugins.sh`） |
| `.serena/project.yml` | `configure` を打った結果 |
| `plugins/ndf/skills/{refactoring,problem-solving,tdd-cycle}/SKILL.md` | 1 行ずつ |
| `plugins/ndf/agents/{corder,qa,director,debugger}.md` | Serena の行だけ |
| `.claude-plugin/marketplace.json` | mcp-serena の説明 |
| `issues/issue-818-requirements.md` / `issue-818-design.md` | AC18 の書き換えと「hook の振る舞い」の節 |

## タスク分解

### Task 1: 起動定義を固定の版とランタイムごとの文脈にする

- **対象:** `.mcp.json`・`.codex.mcp.json`・`.codex-plugin/plugin.json`
- **満たす条件:** AC1・AC2・AC16
- **進め方:** 起動定義を読んで引数の並びを照らすテスト → 定義を書く

### Task 2: 対応表と言語の検出（`detect`）

- **対象:** `languages.json`・`serena_lsp/table.py`・`serena_lsp/detect.py`・`serena-lsp.py detect`
- **変更内容:** 13 言語（設計の 4 言語と公式 LSP プラグインのある 9 言語）。拡張子は `claude-plugins-official` の `extensionToLanguage` を小文字に畳んで写す
- **満たす条件:** AC5、非機能の運用・保守性（架空の言語を足すとコードを変えずに載る）
- **進め方:** 9/10 ファイル・4.9%/5.0%・`.md` を分母に含めない の組のテスト → 判定の純粋な関数 → `git ls-files -z` の薄い層

### Task 3: `project.yml` の行単位の読み書き

- **対象:** `serena_lsp/project_yml.py`
- **満たす条件:** AC6（雛形どおり / 注釈つき / 流れの形の非空 / `project.local.yml` の上書き）
- **進め方:** 3 キー以外が 1 バイトも変わらないことを照らすテスト → 実装

### Task 4: 設定と 1 言語ずつの起動の検証（`configure`）

- **対象:** `serena_lsp/verify.py`・`serena-lsp.py configure`
- **満たす条件:** AC6（無いとき作る）・AC7・AC9・AC10
- **進め方:** 偽の `serena`（受けた引数・環境・cwd を記録し、言語ごとの終了コードを返す）で結合のテスト → 実装。例外・SIGTERM でも最後の値が書かれることを含める

### Task 5: 導入の検査（`check`）

- **対象:** `serena_lsp/check.py`・`serena-lsp.py check`
- **満たす条件:** AC13・AC14・AC15、知らない `extra_checks` で 2
- **進め方:** 偽の `installed_plugins.json`・PATH・`typescript` の `package.json` の組のテスト → 実装

### Task 6: hook（SessionStart の通知・PreToolUse の誘導と自動許可）

- **対象:** `serena_lsp/hooks.py`・`hooks/hooks.json`・`hooks/codex.json`
- **満たす条件:** AC4b（通知の側）・AC17・AC18（書き換え後）・AC19〜AC23
- **進め方:** 設計テストの AC17〜AC23 の組をそのままテストにする（時刻は差し替える）→ 実装。AC23 は 1 万ファイルの一時リポジトリで測る

### Task 7: Skill・文書・NDF の該当行

- **対象:** `skills/language-servers/SKILL.md`・README・`serena-guide.md`・NDF の 3 Skill と 4 エージェント定義・`marketplace.json`・`plugin.json` の説明
- **満たす条件:** AC4b（Skill と README の側）
- **進め方:** 文書のためテスト駆動は適用しない。`check-skill-frontmatter.py`・`check-markdown-links.py`・`check-doc-line-limit.py` で検査する。Skill に書くコマンドは書く前に打って確かめる

### Task 8: このリポジトリへの適用と配布物の同期

- **対象:** `.serena/project.yml`・`dev.kiro/install.sh`
- **満たす条件:** AC4・AC28・AC29
- **進め方:** 作業ツリーで `configure` を実際に打つ。`build-runtime-plugins.sh`・`claude plugin validate .`・Kiro の installer の dry-run と生成物の `agentSpawn` の実行

### Task 9: 実機の確認

- **満たす条件:** AC3・AC8・AC11・AC12・AC24〜AC27、未確認 U2〜U7
- **進め方:** `env -i` と一時の HOME で claude / codex を隔離して動かす。結果を設計テストの「未確認」と設計の「効果の見積もり」に書き足す。確かめられなかった項目は理由とともに PR 本文に残す

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 新設のスクリプトが多く、hook が既存のツール呼び出しを止める | hook は例外を握りつぶして 0 で終わる。テストで壊れた入力を送る |
| 実機の確認（AC24〜AC27）に時間とトークンがかかる | 小さな題材のリポジトリで回し、AC26 は設計テストの 2 条件 × 3 回に限る |
| `project.yml` の書き換えが利用者の設定を壊す | 知らない形では書かずに 3 で止める。3 キー以外の不変をテストで照らす |
| 触る対象の構造 | 新設のコードが中心で、既存コードの構造に依らない。タスクごとにテストを通す |

## 切り戻し手順

Pull Request を revert すれば、起動定義・hook・Skill は元に戻る。利用者の `project.yml` に残った `mcp_serena_excluded` は Serena が知らないキーとして無視する（決定 9 の実測）。

## 完了の定義

- [ ] 単体・結合の受け入れ条件がテストで通る（`uv run ... pytest plugins/mcp/mcp-serena -q -n 4`）
- [ ] 全体の `pytest . -n 4`・文書の検査・`claude plugin validate .`・`build-runtime-plugins.sh --check` が通る
- [ ] 実機の確認の結果が設計テストの文書と PR 本文に載る
