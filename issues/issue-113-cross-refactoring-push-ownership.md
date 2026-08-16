# issue-113: 公開の責務を進行側へ一本化し、適用失敗の項目を対象外へ記録する

## 関連リンク

- [issue-113-cross-refactoring-retrial.md](issue-113-cross-refactoring-retrial.md) — 不具合 10・11 を見つけた再検証（PR #120）
- [issue-113-cross-refactoring-defect-fixes.md](issue-113-cross-refactoring-defect-fixes.md) — 不具合 1〜9 の修正（PR #119）

## モード

`architecture`。`init` の引数（`--sync-command`）と「誰が push するか」という
役割の契約を変更し、`refactor.py` / プロンプト / 手順書にまたがる。

## 目的と非目的

達成したい状態:

- 生成物の同期を検査する pre-push を持つリポジトリでも、収束ループが成立する
- **検証を通っていない変更が Pull Request に現れない**（不具合 4 の経路を根絶する）
- 適用の検証で失敗した提案が、次のラウンドで再び採用されない

やらないこと:

- 生成物を持たないリポジトリへの影響（`--sync-command` は省略可能にする）
- `--no-verify` による回避（検証を飛ばす手段は増やさない）

## 受け入れ条件

- [ ] 1. 実装担当は push しない。プロンプトから push の手順が消え、禁止として明記される
- [ ] 2. `merge-apply` は成功・失敗のどちらでも、検証後に進行側が push する
- [ ] 3. `merge-fix` も検証後に進行側が push する
- [ ] 4. `--sync-command` を指定すると、**push の直前**に同期が走り、差分があれば
      進行側のコミットとして積まれる（`test_push_syncs_generated_files_first`）
- [ ] 5. 同期に失敗したら中断する（終了コード 4）。黙って push しない
- [ ] 6. `--sync-command` 未指定なら同期は走らない（既存の利用者に影響しない）
- [ ] 7. 適用の検証で失敗した項目が `deferred_items` へ理由付きで記録され、
      次ラウンドの提案で「対象外」として渡る（`test_failed_items_are_deferred`）
- [ ] 8. 既存 430 件のテストが退行しない

## 代替案と採否

### 生成物の同期を誰がどこで行うか

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | **進行側が push の直前に同期する** | 採用 | push は `_push_head()` の 1 経路に集約されているため、同期を差し込む場所も 1 つで済む |
| B | 進行側が収束後にまとめて同期する（現行） | 不採用 | **ループ中に push が起きることを見落としていた**。実測で全 push が落ち、実装担当を範囲違反へ誘導した |
| C | 実装担当に同期させる | 不採用 | 範囲外の変更になり、範囲の検査で全件失敗する（実測 0/5） |
| D | `--no-verify` で検査を飛ばす | 不採用 | 検証を飛ばす手段を増やさないという方針に反する |

### 誰が push するか

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | **進行側だけが、検証を通した後に push する** | 採用 | 「未検証の変更が公開される」経路自体が無くなる。不具合 4 は緩和ではなく根絶になる |
| B | 実装担当が項目ごとに push する（現行） | 不採用 | 検証前に公開されるため、取り消しの反映漏れが Pull Request に残る |

## 不変条件

- Pull Request に現れるのは、**検証を通ったコミットと進行側の同期コミットだけ**である
- `git push --force` と `--no-verify` は使わない
- 同期コミットはどの改善項目にも属さない。取り消しでは積み直さない
  （次の push で作り直されるため失われても問題にならない）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `init` の引数 | `--sync-command` を追加 | 追加のみ。省略時は同期しない |
| 状態ファイル | `sync_command` を追加 | 追加のみ。欠けていても読める |
| 実装担当の手順 | push を禁止に変える | **破る**。プロンプトと手順書を同時に変更する |
| `merge-apply` / `merge-fix` の副作用 | 常に push するようになる | **破る**。手順書に明記する |

## 修正対象

```
plugins/ndf-shared/skills/cross-refactoring/scripts/refactor.py
plugins/ndf-shared/skills/cross-refactoring/prompts/apply.md
plugins/ndf-shared/skills/cross-refactoring/prompts/fix.md
plugins/ndf-shared/skills/cross-refactoring/SKILL.md
plugins/ndf-shared/skills/cross-refactoring/docs/02-apply-and-review.md
plugins/ndf-shared/skills/cross-refactoring/tests/
plugins/ndf-{claude,codex,kiro}/skills/...   # 配布物（生成）
```

## タスク分解

### Task 1: 適用で失敗した項目を「対象外」へ記録する

- **対象ファイル:** `scripts/refactor.py`、`tests/test_merge_apply.py`
- **変更内容:** `_defer_abandoned_items()` を追加し、取り消しの完了時に呼ぶ。
  `item_id` で重複を防ぐ
- **満たす受け入れ条件:** 7
- **進め方:** 再提案が防げることを確かめる失敗テスト → 実装

### Task 2: push の直前に生成物を同期する

- **対象ファイル:** `scripts/refactor.py`、`tests/test_merge_apply.py`
- **変更内容:** `--sync-command` を `init` へ追加し状態へ保存する。
  `_sync_generated()` を追加し、`_push_head()` の冒頭で呼ぶ。差分があれば
  進行側のコミットとして積む。同期の失敗は中断（終了コード 4）
- **満たす受け入れ条件:** 4, 5, 6
- **進め方:** 同期→コミット→push の順序を確かめる失敗テスト → 実装

### Task 3: 公開の責務を進行側へ移す

- **対象ファイル:** `scripts/refactor.py`、`prompts/apply.md`、`prompts/fix.md`、
  `SKILL.md`、`docs/02-apply-and-review.md`、`tests/`
- **変更内容:** `merge-apply` / `merge-fix` が検証後に必ず push する。
  プロンプトから push の手順を消し、禁止として明記する。手順書を追従させる
- **満たす受け入れ条件:** 1, 2, 3
- **進め方:** 成功経路でも push することを確かめる失敗テスト → 実装 → 文書追従

### Task 4: 配布物を生成する

- **対象ファイル:** `plugins/ndf-{claude,codex,kiro}/`
- **変更内容:** `bash scripts/build-runtime-plugins.sh`
- **満たす受け入れ条件:** 8

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 同期コマンドが遅い / 固まる | テストと同じ打ち切り時間を掛け、超えたら中断する |
| 同期コミットが取り消しで消える | 次の push で作り直される。取り消し対象にも積み直し対象にもしない |
| 実装担当が指示に反して push する | 検証は git の事実から取るため判定は変わらない。範囲外なら従来どおり失敗する |

## 完了の定義

- [ ] 受け入れ条件 1〜8 をすべて満たす
- [ ] 2 つの tests ディレクトリのテストが全件成功
- [ ] `python3 scripts/check-skill-frontmatter.py` / `claude plugin validate` が成功
- [ ] `/ndf:cross-review` が収束
