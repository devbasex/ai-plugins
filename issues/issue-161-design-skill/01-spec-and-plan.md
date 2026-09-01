# issue #161: 設計工程の成果物を規約化する Skill を作る

## 関連リンク

- [issue #161](https://github.com/devbasex/ai-plugins/issues/161) — 設計フェーズの成果物を規約化する Skill を作る
- 過去の計画: `issues/old/ndf-development-skills/04-development-skills.md`（設計品質の 3 Skill 構想）
- 設計成果物の実例: `issues/old/issue-146-worktree-first/04〜06`

## モード

`architecture`。3 ランタイムへ配布する公開 Skill を追加し、工程表・マニフェスト・説明文書という
複数の配布物にまたがる（`development-workflow` の判定）。

## 依頼（原文）

> /ndf:development-workflow
> https://github.com/devbasex/ai-plugins/issues/161
> workflow完了まで進めてください。
> なお、並行で https://github.com/devbasex/ai-plugins/issues/202 の開発が進行しています
> (.worktrees内)。衝突がないかどうかは確認しながら進めてください

> また、設計成果物完成後、一度PRを作成して 設計についての /ndf:cross-review を実施し、approve後にmergeする、という工程を追加したいです
> 設計だけでなくその手前の要求仕様などもPRとレビューに含まれます

issue #161 の課題（原文）:

> `development-workflow` は 4 つのモードに設計工程を割り当てているが、**その工程で何という文書を作り、
> 何を書くかの規約を持っていない**。このため設計の成果物が担当した AI ごとに変わる。

## 目的

- 設計工程の成果物を、担当する AI が変わっても同じ構成になる形へ規約化する
- データ構造と入出力の契約を、実装を始める前に確定した状態にする
- 設計から `implementation-plan` のタスクを導ける状態を保証する
- 設計を実装より先にレビューへ通す工程を、開発ループへ組み込む

## 前提

- 前提 1: 新設する Skill の名前は `design` とし、`/ndf:design` で起動する
- 前提 2: 成果物は `issues/` 配下へ置き、完了後に `plan-to-spec` が `docs/` へ移す既存の流れに乗せる
- 前提 3: 外部の記述標準（OpenAPI・データ契約・Design Tokens）は、対象となる領域を触る変更でのみ必須とする。
  触らない領域については設計文書に節を作らない
- 前提 4: 設計品質の 3 Skill 構想（`design-review` / `domain-modeling` / `object-design`）は、
  `design` 1 個へ統合する。分割すると設計工程が 3 つの Skill に散り、どれを起動するかの判断が新たに要る
- 前提 5: 盤面（GitHub Projects）へ新しい工程の値を足すのは、盤面を持つリポジトリ側の設定である。
  この変更では対応表と呼び出しまでを扱う

## 対象範囲

含む:

- `plugins/ndf/skills/design/` の新設（`SKILL.md` と `references/`）
- `development-workflow` の工程表・標準フローの図・「`architecture` モードの現状」節の差し替え
- 同じ `SKILL.md` の本文のうち、新しい工程表と食い違う記述の更新（「設計行の `standard` と
  `legacy-refactor` は専用の設計 Skill を起動しない」の注記、標準フローの図の下の「`standard` は
  D・E・F を通らない」の解説）
- 工程表への「設計レビュー」行の追加（設計成果物を Pull Request にして `cross-review` を通す）
- `references/projects-tracking.md` の工程と値の対応表の更新
- 3 ランタイムのマニフェスト（`manifests/*-skills.txt`）への追加
- 説明文書（`README.md` / `plugins/ndf/README.md`）の Skill 数とカテゴリ内訳の更新

含まない:

- `requirements-design` / `implementation-plan` / `plan-to-spec` の手順そのものの変更（接続の記述は加える）
- `cross-review` / `pr` の手順の変更。設計 Pull Request は `design` の側から既存の手順を呼ぶ
- 記述標準を検査する道具（Spectral・Data Contract CLI・Style Dictionary）の導入。
  設計文書がどの道具で検査できるかは示すが、このリポジトリへは導入しない
- `develop` ブランチへの移行（issue #202 / #192 の担当）
- 版を上げること。配布はまとまり単位で行う（`release` の担当）

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| 設計文書 | `design` が作る成果物。`issues/` 配下に置く Markdown |
| 設計 Pull Request | 要求仕様と設計文書だけを載せ、実装を含まない Pull Request |
| 記述標準 | 機械が読める形式で契約を書く外部の取り決め（OpenAPI・Open Data Contract Standard・Design Tokens Format Module） |
| 契約 | 呼び出す側と応じる側が守る約束。API の経路と入出力、画面の入力規則がこれにあたる |

## 受け入れ条件

### 新設する Skill

- [ ] `plugins/ndf/skills/design/SKILL.md` が存在し、`python3 scripts/check-skill-frontmatter.py` が
      終了コード 0 で終わる
- [ ] `SKILL.md` に、モードごとの必須成果物を定めた表がある（`light` は対象外、`standard` /
      `architecture` / `legacy-refactor` はそれぞれ何を作るか）
- [ ] `SKILL.md` が、触る領域に対応する `references/` のファイルだけを読ませる書き方になっている。
      永続データを持たない変更ではデータ構造の参照を読ませず、API と画面を持たない変更では
      契約の参照を読ませない
- [ ] 設計文書の雛形が `references/` にあり、雛形の各節が `implementation-plan` のどのタスクへ
      つながるかが書かれている
- [ ] データ構造の参照に、時系列を保護する手法（二重時間軸・履歴保持型の次元・事象の記録・定期取得）が
      並び、どれを採るかと採らなかった理由を残す形が示されている
- [ ] 呼び出される約束の参照に、API を OpenAPI で書くことが定めてある
- [ ] 画面の参照に、当面の表と図の形式が定めてある

### 開発ループへの結線

- [ ] `development-workflow` の工程表の「設計」行が、`standard` / `architecture` / `legacy-refactor` の
      3 モードで `design` を指す
- [ ] 工程表に「設計レビュー」行がある。`standard` と `architecture` で必須、`legacy-refactor` で任意、
      `light` は対象外
- [ ] 設計 Pull Request の本文に、課題を自動で閉じる語（`Closes` / `Fixes` / `Resolves`）を
      書かないことが定めてある
- [ ] 設計 Pull Request をマージした後、実装用の作業ツリーを作り直す手順が定めてある
- [ ] 「`architecture` モードの現状」節が、縮退運用の記述から導入済みの記述へ差し替わっている
- [ ] 標準フローの図が、設計と設計レビューを含む形へ更新されている
- [ ] `references/projects-tracking.md` の対応表に `設計` の記録 Skill として `design` が入り、
      `設計レビュー` の行が加わっている

### 配布と説明文書

- [ ] `manifests/claude-skills.txt` / `codex-skills.txt` / `kiro-skills.txt` の 3 本に `design` が載る
- [ ] `bash scripts/build-runtime-plugins.sh --check` が終了コード 0 で終わる
- [ ] `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる（Skill 数と版数の突き合わせを含む）
- [ ] `python3 scripts/check-markdown-links.py` が終了コード 0 で終わる
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が終了コード 0 で終わる

### 起きてはいけないこと

- [ ] 既存 32 個の Skill の `SKILL.md` の手順が変わらない（`development-workflow` の工程表と、
      呼び出し元 Skill への 1 行の案内を除く）
- [ ] 判定基準（どのモードに当たるか）が `development-workflow` の外へ写らない。`design` は
      判定結果を受け取る側に徹する
- [ ] 記述標準を、対象となる領域を触らない変更にも求める記述になっていない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `/ndf:design` が増える。既存 Skill の起動名は変わらない |
| データ | なし（永続データを持たない） |
| 既存の振る舞い | `development-workflow` の工程表に 1 行増え、設計行の振り分け先が変わる |
| 配布物 | 3 ランタイムの公開 Skill 数が 1 ずつ増える（Claude Code 33 / Kiro 32 / Codex 31） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| Skill の frontmatter | `python3 scripts/check-skill-frontmatter.py` |
| 配布物の同期 | `bash scripts/build-runtime-plugins.sh --check` |
| 説明文書の突き合わせ | `bash scripts/validate-runtime-plugins.sh` |
| リンク | `python3 scripts/check-markdown-links.py` |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 手動確認 | `claude --plugin-dir plugins/ndf` で `/ndf:design` が一覧に出ることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | Skill の実体は `plugins/ndf/skills/` の 1 箇所。配布先は `manifests/*-skills.txt` が決める（`AGENTS.md`） |
| 執筆規約 | `plugins/ndf/skills/README.md`（frontmatter）と `markdown-writing`（本文） |
| 分量 | 1 ファイル 500 行を上限とし、超えたら分割する（`markdown-writing`） |
| テスト戦略 | 検査スクリプトの単体テストは `scripts/tests/`。Skill の記述の整合はマニフェストと説明文書の突き合わせで担保する |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存の検査スクリプトの実行、配布物の再生成、説明文書の数の更新 |
| 確認してから行う | 既存 Skill の手順の変更、記述標準を必須にする範囲の拡大 |
| 行わない | 版を上げること、`develop` への移行、検査道具の新規導入 |

## 並行して進む開発との衝突

issue #202（`fix/issue-202-base-branch`）が同時に進んでいる。触る対象を突き合わせた結果、
重なるファイルは無い。

| 対象 | #161 | #202 |
| --- | --- | --- |
| `skills/design/` | 新設 | 触らない |
| `skills/development-workflow/` | 工程表・図・参照を更新 | 触らない |
| `skills/worktree/` | 触らない | 起点ブランチの解決を変更 |
| `scripts/lib/worktree-common.sh` | 触らない | 同上 |
| `skills/ndf-policies/` `cherry-pick-pr/` `deploy/` | 触らない | `origin/main` の取り込みを変更 |
| `manifests/*-skills.txt` | `design` を追加 | 触らない |
| `README.md` / `plugins/ndf/README.md` | Skill 数とカテゴリ内訳 | 触らない |

同じ行を触らないため、どちらが先にマージされても取り込みで衝突しない。

## 実装計画

### タスク 1: `design` Skill の新設

| 対象 | 内容 |
| --- | --- |
| `skills/design/SKILL.md` | モードごとの必須成果物、手順、参照の読み分け、設計 Pull Request の呼び出し |
| `references/design-template.md` | 設計文書の雛形、各節が `implementation-plan` のどこへつながるか、図の水準 |
| `references/decisions.md` | 決定の記録の書き方と置き場所 |
| `references/data-structure.md` | データ構造の観点、時系列を保護する手法、分析視点での検査 |
| `references/interface-api.md` | 呼び出される約束の記述標準と契約の検査 |
| `references/interface-ui.md` | 画面の当面の書き方と、見た目の値の記述標準 |

### タスク 2: 開発ループへの結線

| 対象 | 内容 |
| --- | --- |
| `development-workflow/SKILL.md` | 工程表の設計行の差し替え、設計レビュー行の追加、標準フローの図、`architecture` モードの現状節、図の下の解説と注記 |
| `development-workflow/references/projects-tracking.md` | 工程と値の対応表 |
| `requirements-design/SKILL.md` | 設計工程への引き継ぎを 1 行 |
| `implementation-plan/SKILL.md` | 設計文書を入力に取ることを 1 行 |
| `plan-to-spec/SKILL.md` | 設計文書を確定仕様へ引き継ぐことを 1 行 |

### タスク 3: 配布と説明文書

| 対象 | 内容 |
| --- | --- |
| `manifests/*-skills.txt` | 3 本へ `design` を追加 |
| `README.md` | 元 Skill 数 36 → 37、開発方法論 9 → 10、ランタイム別の公開数 |
| `plugins/ndf/README.md` | 配布先の表、レイアウト図の実体数 |
| 配布物 | `bash scripts/build-runtime-plugins.sh` で再生成 |

### タスク 4: 検証

受け入れ条件の検証手段をすべて実行し、証跡を残す（`quality-gates`）。

## 進め方

この変更自身が、新しく定める工程を通る。

1. 要求仕様と設計文書を書く（タスク 1 の設計まで）
2. 設計 Pull Request を作り、`cross-review` の承認を得てマージする。本文に課題を自動で閉じる語を書かない
3. マージ後に後片付けを行い、実装用の作業ツリーを新しく作る
4. その作業ツリーで Skill 本体を実装し、2 本目の Pull Request を出す

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 盤面へ `設計レビュー` の選択肢を足すこと | 盤面を持つリポジトリの管理者 | この Skill を使い始めるまで |
