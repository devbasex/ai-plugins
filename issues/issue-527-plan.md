# #527: `development-workflow` の起動時に作業ツリーの宣言を確かめる — 実装計画

## 関連リンク

- issue: https://github.com/devbasex/ai-plugins/issues/527
- 要求と受け入れ条件: [issue-527-requirements.md](issue-527-requirements.md)
- 設計: [issue-527-design.md](issue-527-design.md)（設計 PR #576 でマージ済み）

## モード

`standard`。配布する Skill の手順と、`worktree-setup.sh` の副コマンド（公開インタフェース）を足す。

## 目的と非目的

達成したい状態:

- `development-workflow` を起動した会話で、宣言の無いまま作業ツリー運用が黙って無効になる状態を無くす

やらないこと:

- `hooks/*.json` / `worktree-guard.sh` / `worktree-session.sh` / `workflow-guard.sh` / `development-workflow` の frontmatter の変更（受け入れ条件 8。#565 と重なる）
- `init` が読めない宣言を「既にあります」と報告する件（#573）
- `scripts-lookup.md` の「`worktree` は 3 本を呼ぶ」の書き足し（設計の「作るもの」に無い）

## 受け入れ条件

要求文書の 12 件をそのまま使う。条件ごとの検証手段は設計文書の「テスト設計」の表にある。

## 実装で決める 2 件

### 決定 A: 実機で通すのは Claude Code だけにする

**Codex / Kiro CLI / agy では通さない。** 理由は 3 つある。

| 理由 | 内容 |
| --- | --- |
| 手順が同じファイルである | 手順 0 は `SKILL.md` の本文にあり、hook に依存しない。4 ランタイムへ同じ本文が配られることは `bash scripts/validate-runtime-plugins.sh` が確かめる |
| 開発中の配布物を読ませる経路が利用者の登録を触る | Codex は取得元の登録を差し替えるか `CODEX_HOME` の隔離に認証の複製が要る。agy は導入済みの `ndf` を上書きする（`AGENTS.md` の「ローカルのディレクトリを同じ名前でマーケットプレイスとして追加しない」） |
| CLI を他の担当と共有している | 並行する cross-review / cross-refactoring の実行とレート制限を奪い合う |

残る未確認は「Codex / Kiro / agy のエージェントが手順 0 を守るか」で、PR 本文へそのまま書く。

### 決定 B: 「判定する単位」の工程の順序へ手順 0 を書き足さない

**`要求と受け入れ条件 → モード判定 → 作業場所の用意` の並びはそのまま残す。**

- 並びに載るのは工程（工程表の行か、行を持たない「モード判定」）である。手順 0 はモード判定の
  中の手順で、並びへ書くと新しい工程に読める。工程の値と工程表の行の追加は要求の範囲外である
- 設計の決定 1 は「Skill を読み込んだ時点で最初に通す」として、工程の順序の議論と独立させた。
  並びへ書くと、その独立が崩れる
- 走査テストで、並びの行が変わっていないこと（`宣言` の語を含まないこと）を固定する

## 修正対象

| ファイル | 変更 |
| --- | --- |
| `plugins/ndf/scripts/lib/worktree-common.sh` | `wt_declaration_state` を新設 |
| `plugins/ndf/scripts/worktree-setup.sh` | `check` を新設。`status` の分岐を関数へ寄せる |
| `plugins/ndf/skills/worktree/tests/test_setup.py` | `check` の終了コード・作らないこと・`status` との 1 行目の一致 |
| `plugins/ndf/skills/worktree/SKILL.md` | 手順 0 の末尾で `check` を案内する |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 受け取る値の節・手順 0・出力に `宣言:` の行 |
| `plugins/ndf/skills/development-workflow/tests/test_declaration_check.py` | 新設 |

## タスク分解

### Task 1: 宣言の状態を分ける関数と `check`

- **対象ファイル:** `worktree-common.sh` / `worktree-setup.sh` / `worktree/tests/test_setup.py`
- **変更内容:** `wt_declaration_state` を足し、`check`（終了コード 0 / 1 / 2 / 3 と 3 行）を足す。`status` の「宣言ファイル:」の行を同じ関数から得る
- **満たす受け入れ条件:** 1 / 2 / 3 / 7 / 10（実体側）
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 2: `development-workflow` の手順 0 と `worktree` の案内

- **対象ファイル:** `development-workflow/SKILL.md` / `worktree/SKILL.md` / `development-workflow/tests/test_declaration_check.py`
- **変更内容:** 受け取る値の節と手順 0（bash と終了コードの表・拒否しない理由）を置き、出力の例に `宣言:` の行を足す
- **満たす受け入れ条件:** 4 / 9 / 10（本文側）/ 11 / 12
- **進め方:** 本文の走査テストを先に書いて落とす → 本文を書く

### Task 3: 実機の確認

- **対象:** `claude -p --plugin-dir <作業ツリー>/plugins/ndf` を `$HOME` 配下の一時リポジトリで 2 回
- **満たす受け入れ条件:** 5 / 6
- **進め方:** テスト駆動は当たらない（エージェントの振る舞いの観測）。記録を PR へ貼る

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `development-workflow/SKILL.md` が 475 行で、上限 500 行に近い | 手順 0 を 25 行未満に収める。超えるなら理由の段落を短くする。`check-skill-frontmatter.py` で確かめる |
| #565 の実装が同じ `SKILL.md` の frontmatter を触る | 本文の「判定の手順」だけを触る。先にマージされた側に合わせて再基点化する |
| `status` の出力が変わる | 既存の `test_status_reports_*` と、ディレクトリ・壊れた symlink の 2 通りで `check` と 1 行目を突き合わせる |

## 切り戻し手順

コードとテストと本文だけの変更で、データを持たない。Pull Request を revert すれば戻る。

## 完了の定義

- [ ] 受け入れ条件 12 件に検証手段と結果が対応している
- [ ] `uv run --with pytest pytest plugins/ndf/skills/worktree/tests plugins/ndf/skills/development-workflow/tests -q` が通る
- [ ] `bash scripts/build-runtime-plugins.sh` の後に `bash scripts/validate-runtime-plugins.sh` / `python3 scripts/check-skill-frontmatter.py` / `python3 scripts/check-doc-staleness.py` / `claude plugin validate .` が通る
