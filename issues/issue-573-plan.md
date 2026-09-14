# #573: init が読めない宣言を失敗として報告し、手順 0 がそこで止まる（実装計画）

## 関連リンク

- 要求と受け入れ条件: [issue-573-requirements.md](issue-573-requirements.md)（AC1〜AC14）
- 設計文書: [issue-573-design.md](issue-573-design.md)（決定 1〜7）
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/636 （マージ済み）
- 課題: #573

## モード

`standard`。`worktree-setup.sh` の振る舞いと Skill の手順書の両方が変わり、`init` の終了コードの
契約が変わるため（親が判定済み）。

## 目的と非目的

達成したい状態:

- `--force` を付けない `init` が、宣言の状態を `status` / `check` と同じ関数（`wt_declaration_state`）で
  判定し、読めない宣言を 1 で失敗として報告する
- `worktree` の手順 0 が、`init` の終了コードが 0 でなければ先へ進まない

やらないこと:

- 宣言の読み取り層（`worktree-common.sh` の `wt_declaration` / `wt_declaration_state`）の変更（#495 が持つ）
- `init --force` の書き込みの経路の変更（ディレクトリの形で一時ファイルが残る事象は #628）
- 読めない理由の区別（状態は 3 語のまま）
- `development-workflow` の手順 0 の分岐の変更
- `plugins/ndf/README.md` と `references/declaration.md` の `init` の記述

## 前提

- 前提 1: 新しいファイルは作らない。変更は設計文書の「変わるファイル」の 4 つに収まる
- 前提 2: 既存の `test_setup.py` と `test_declaration_check.py` の期待値は変えない（追加だけ）
- 前提 3: 手順 0 の見出し「0. 宣言ファイルを用意する」と `worktree-setup.sh check` の文は残す
  （`test_declaration_check.py` が本文から探す）

## 受け入れ条件

**14 件は [issue-573-requirements.md](issue-573-requirements.md) の「受け入れ条件」にある。**
ここでは写さない。タスクごとに、そのタスクが満たす条件を指す。

## 修正対象

- `plugins/ndf/scripts/worktree-setup.sh`（`do_init` だけ）
- `plugins/ndf/skills/worktree/SKILL.md`（手順 0）
- `plugins/ndf/skills/worktree/tests/test_setup.py`
- `docs/specifications/ndf-workflow-unit-and-gates.md`（`init` の終了コードに触れる 1 文）
- 生成物（`bash scripts/build-runtime-plugins.sh` で同期する各ランタイムの写し）

## タスク分解

### Task 1: `init` が読めない宣言を失敗として報告する

- **対象ファイル:** `plugins/ndf/scripts/worktree-setup.sh`、`plugins/ndf/skills/worktree/tests/test_setup.py`
- **変更内容:** `do_init` で `--force` が無いとき、`refuse_symlink` より前に `wt_declaration_state` を
  読み、`present` は「既にあります」で 0、`unreadable` は `print_declaration_line unreadable` と案内
  （直すか消してから `init`。`--force` は勧めない）を標準エラーへ出して 1、`absent` はこれまでの
  作成の経路へ進む（決定 1〜5）
- **満たす受け入れ条件:** AC1・AC2・AC3・AC4・AC5・AC6・AC7・AC8・AC9・AC12
- **進め方:** 失敗するテスト（AC1〜AC5・AC8 は変更前に落ちる。AC6・AC7・AC9・AC12 は既存か
  現状固定）→ `do_init` の分岐 → 整理。`init` / `status` / `check` を一時リポジトリで実際に走らせ、
  終了コードと宣言の行を並べて確かめる

### Task 2: 手順 0 が `init` の失敗で止まる

- **対象ファイル:** `plugins/ndf/skills/worktree/SKILL.md`、`plugins/ndf/skills/worktree/tests/test_setup.py`
- **変更内容:** 手順 0 のコマンドを `init; echo "exit=$?"` にし、終了コードが 0 でなければ先へ進まず
  出力を利用者に示すこと、宣言を直す・消す・`--force` で作り直すのは利用者が決めることを書く。
  「既にあれば上書きしない」を「読める宣言が既にあれば上書きしない。読めない宣言では 1 で終わる」へ
  直す（決定 6）
- **満たす受け入れ条件:** AC10・AC11
- **進め方:** 本文の抜き出しで確かめるテスト → 本文の変更

### Task 3: 仕様の文書を変更後の終了コードに合わせる

- **対象ファイル:** `docs/specifications/ndf-workflow-unit-and-gates.md`
- **変更内容:** 「`init` の後にもう一度 `check` を通す」に添える理由の 1 文から「読めるとは限らない」を
  外し、変更後の `init`（0 なら読める）と食い違わない理由へ直す
- **満たす受け入れ条件:** AC14
- **進め方:** 文書の変更。`grep` で「読めるとは限らない」が残っていないことを確かめる

### Task 4: 全体テストと生成物の同期

- **対象ファイル:** 生成物
- **変更内容:** `bash scripts/build-runtime-plugins.sh` で各ランタイムの写しを同期する
- **満たす受け入れ条件:** AC13
- **進め方:** `uv run --with pytest pytest scripts/tests plugins/ndf -q` と
  `bash scripts/build-runtime-plugins.sh --check`

## 影響範囲

- `init` の終了コード: 読めない宣言で 0 → 1、読める symlink の宣言で 1 → 0
- `worktree` を起動したエージェント: 読めない宣言のリポジトリで手順 0 から先へ進まない
- `development-workflow` と hook: 変わらない（`init` を呼ばないか、状態 3 では呼ばない）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `do_init` の分岐の順序（状態判定と `refuse_symlink`）を誤ると、読める symlink で止まる | AC8 のテストで固定する。触る範囲が `do_init` の 1 関数に閉じており、実装の後の構造改善で足りる |
| 権限 `000` のテストが root では通らない | `os.geteuid() == 0` のとき skip する（設計のテスト設計） |

## 切り戻し手順

- 4 ファイルの変更を revert する。永続データも型も持たないため、他に戻すものは無い

## 完了の定義

- [ ] 受け入れ条件 14 件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `claude plugin validate .` が終了コード 0 で終わる
- [ ] `bash scripts/build-runtime-plugins.sh --check` が差分なしで終わる
- [ ] cross-refactoring が収束し、最後の cross-review が `APPROVE` になる
