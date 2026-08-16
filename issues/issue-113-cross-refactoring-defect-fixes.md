# issue-113: cross-refactoring 実機検証で見つかった不具合 9 件の修正

## 関連リンク

- [issue-113-cross-refactoring-trial-report.md](issue-113-cross-refactoring-trial-report.md) — 不具合の内容とエビデンス
- [issue-113-cross-refactoring-fix-handoff.md](issue-113-cross-refactoring-fix-handoff.md) — 着手順と決めるべきこと
- 対象 PR: #118（実機検証に使った Draft）

## モード

`architecture`。`refactor.py` のサブコマンド境界（検証・取り消し・状態記録）と結果ファイルの
命名規約という公開インタフェースを変更し、`cross-refactoring` / `cross-review` 共通層 /
プロンプト / 手順書の複数モジュールにまたがるため。

## 目的と非目的

達成したい状態:

- 適用結果の検証で失敗した項目を、**他の項目のコミットと競合せずに**取り消せる
- 取り消しに失敗したときは進行を**止める**（握り潰して次ラウンドへ進まない）
- 中断しても、どこまで到達したかを状態ファイルから読める
- 検証を通っていない変更が Pull Request に残らない
- `--scope` の指定が適用結果の検証に反映される
- 提案・レビューの参加者が、認証切れで黙って脱落しない

やらないこと:

- レビューフェーズ以降の実機検証（本 PR では単体テストまで。実機は別途）
- 語彙の日本語表記を受理する正規化（**許容値の列挙**までに留める）
- 生成物同期の自動実行（進行側の責務として**手順に明記**するだけ）

## 前提

- 前提 1: 適用フェーズ時点では `apply_base_sha..HEAD` の全コミットが
  いずれかの改善項目に割り当て済みである（未割当があれば `merge-apply` が
  ラウンドごと取り消すため、項目単位の取り消し経路には到達しない）
- 前提 2: `git push --force` は使わない。履歴の書き換えではなく
  **revert + cherry-pick の積み直し**で前進のみを行う

## 受け入れ条件

- [ ] 1. 取り消し対象より新しい別項目のコミットがあっても、**変更が独立していれば**
      項目単位で取り消せる（`test_drop_older_item_keeps_the_newer_one`）
- [ ] 2. 積み直しが競合したときは、着手前の状態まで戻してラウンド全件を取り消し、
      半端な履歴を残さない（`test_adjacent_changes_fall_back_to_the_whole_round`）
- [ ] 3. 取り消しに失敗したら終了コード 4 で中断する。進行スクリプトは
      終了コード 2（全件失敗）と 4（中断）を区別する
- [ ] 4. 検証の途中で中断しても、そこまでの判定が状態ファイルへ残る
      （`items[].status` と `rounds[].apply.progress`）
- [ ] 5. 取り消しの push が完了するまで `pending_push` が立ち、
      次の実行が処理済み判定より先に再送信する（取り消し着手**前**に立てる）
- [ ] 6. `target_scope` の外を触ったコミットを含む項目は失敗になる
      （`test_out_of_scope_commit_fails_the_item`）
- [ ] 7. 提案の結果ファイル名にラウンド番号が入り、2 巡目が 1 巡目を上書きしない
- [ ] 8. gemini の作業ディレクトリで、配置した手順書を読み取れる設定が置かれる
- [ ] 9. 提案プロンプトに `smell` / `technique` / `severity` の許容値が列挙される
- [ ] 10. 初期化が参加ランタイムの認証状態を確認し、未認証なら失敗する
- [ ] 11. 既存 387 件のテストが退行しない

## 代替案と採否

### 取り消しの単位

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 項目単位を維持し、**範囲を新しい順に全て戻してから残す項目を積み直す** | 採用 | 「合意済みの項目は残す」という設計方針を保てる。範囲全体の巻き戻しは常に競合しない |
| B | ラウンド単位へ変更する | 不採用 | 実装は単純だが、1 件の失敗で合意済みの項目まで捨てることになる |
| C | 失敗項目のコミットだけを新しい順にまとめて戻す | 不採用 | 取り消し対象より新しい**別項目**のコミットが同じ箇所を触ると必ず競合する（不具合 1 の再現） |

案 A で積み直しが競合した場合だけ、案 B（ラウンド全件の取り消し）へ**退避**する。
これにより最悪でも決定的な状態に落ち、半端な履歴を残さない。

#### 実装して分かったこと（案 A の限界）

実機の git で確かめたところ、**同一ファイルの隣接行を触る項目どうしは積み直しでも
競合する**（`tests/test_drop_items_git.py`）。取り消した側の行が消えると、残す側の
パッチが前提にしている文脈も消えるためで、git だけでは決められない。

| 位置関係 | 結果 |
| --- | --- |
| 別ファイル | 項目単位 |
| 同一ファイルの離れた行 | 項目単位 |
| 同一ファイルの隣接行 | ラウンド全件へ退避 |

実測（PR #118）では採用 5 件のうち 4 件が同一ファイルの隣接領域を変更していたので、
**この構成では退避が普通に起こる**。それでも案 A を採る理由は 2 つある。

- 案 C（現状）は**進行が止まる**。案 A は最悪でも決定的な状態に落ちて進行を続けられる
- 変更が独立していれば項目単位が保たれる。範囲や採用件数を絞れば独立させられる

### 配布物同期の責務

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 進行側（ホスト）が収束後にまとめて生成する | 採用 | 範囲の指定と整合する。実装担当の差分が範囲内に収まり、差分予算も現実的になる |
| B | 実装担当に同期させる | 不採用 | 範囲外の変更が生まれ、差分が 4 倍に膨らんだ（不具合 5 の実測） |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 積み直し | 範囲を全て取り消したあと、残す項目のコミットを `git cherry-pick` で載せ直すこと |
| 退避 | 積み直しが競合したときに、ラウンド全件の取り消しへ切り替えること |
| 中断 | 進行を止めること。終了コード 4 で表す（2 の「全件失敗」と区別する） |

## 不変条件

- Pull Request に残るのは、**検証を通ったコミットだけ**である
- `git push --force` と `--no-verify` は使わない
- 取り消しは冪等である。叩き直しても二重に取り消さない
- 状態ファイルの `items[].commits` は、**現在の履歴に実在する SHA** を指す
  （積み直しで SHA が変わったら更新する）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `refactor.py` の終了コード | 中断を 4 として追加 | 追加のみ。0 / 2 / 3 の意味は変えない |
| 提案の結果ファイル名 | `-r<ラウンド>` を追加 | 破る。進行スクリプトと `--stem-template` を同時に変更する |
| 状態ファイル | `vocabulary` / `auth` / `apply.progress` を追加 | 追加のみ。欠けていても読める |
| `--scope` の意味 | 検証にも使う | 破る（これまで検証に反映されていなかった）。手順書に明記する |

## 修正対象

```
plugins/ndf-shared/skills/cross-refactoring/scripts/refactor.py
plugins/ndf-shared/skills/cross-refactoring/scripts/prepare-worktrees.sh
plugins/ndf-shared/skills/cross-refactoring/scripts/launch-cli.sh
plugins/ndf-shared/skills/cross-refactoring/prompts/propose.md
plugins/ndf-shared/skills/cross-refactoring/prompts/apply.md
plugins/ndf-shared/skills/cross-refactoring/prompts/fix.md
plugins/ndf-shared/skills/cross-refactoring/SKILL.md
plugins/ndf-shared/skills/cross-refactoring/docs/01-state-and-propose.md
plugins/ndf-shared/skills/cross-refactoring/docs/02-apply-and-review.md
plugins/ndf-shared/skills/cross-refactoring/tests/
plugins/ndf-{claude,codex,kiro}/skills/...   # 配布物（生成）
```

## タスク分解

### Task 1: 取り消しを「巻き戻して積み直す」形に変える

- **対象ファイル:** `scripts/refactor.py`、`tests/test_abandon_items.py`、`tests/test_merge_apply.py`
- **変更内容:** `_drop_items()` を追加する。範囲を新しい順に全て `git revert` し、
  残す項目のコミットを古い順に `git cherry-pick` で積み直す。積み直しが競合したら
  着手前 HEAD へ戻し、ラウンド全件の取り消しへ退避する。
  `cmd_merge_apply` と `cmd_abandon_items` を `_drop_items()` 経由に置き換える
- **満たす受け入れ条件:** 1, 2
- **進め方:** 競合する履歴を模す失敗テスト → 実装 → 既存の取り消しテストを新形へ移す

### Task 2: 中断と進捗記録を分ける

- **対象ファイル:** `scripts/refactor.py`、`SKILL.md`、`docs/02-apply-and-review.md`
- **変更内容:** 中断を終了コード 4 に統一する（`die` の既定値）。`cmd_merge_apply` は
  項目ごとの判定を**その都度**状態ファイルへ保存する。取り消しへ着手する**前**に
  `pending_push` を立てる。進行スクリプトは 2 と 4 を区別し、4 では `exit` する
- **満たす受け入れ条件:** 3, 4, 5
- **進め方:** 中断時の状態を確かめる失敗テスト → 実装 → 手順書の更新

### Task 3: 範囲外のファイル変更を検証で捕まえる

- **対象ファイル:** `scripts/refactor.py`、`prompts/apply.md`、`prompts/fix.md`、`docs/02-apply-and-review.md`
- **変更内容:** `commit_files()` を追加し、`collect_commit_facts()` の事実へ `files` を含める。
  `verify_apply_item()` / `verify_fix_commit()` が `target_scope` の外を触ったコミットを
  失敗にする。プロンプトへ「生成物の同期はしない」を明記し、手順書へ進行側の責務として書く
- **満たす受け入れ条件:** 6
- **進め方:** 範囲外コミットを含む事実を渡す失敗テスト → 実装 → 文書追従

### Task 4: 提案の結果ファイルをラウンドごとに分ける

- **対象ファイル:** `scripts/refactor.py`、`scripts/launch-cli.sh`、`SKILL.md`、`docs/01-state-and-propose.md`
- **変更内容:** `stem_for()` の `propose` にラウンド番号を入れ、呼び出し側と
  `--stem-template` を揃える
- **満たす受け入れ条件:** 7
- **進め方:** `stem_for()` の失敗テスト → 実装 → 進行スクリプトと手順書の更新

### Task 5: gemini が手順書を読めるようにする

- **対象ファイル:** `scripts/prepare-worktrees.sh`、`tests/test_prepare_worktrees.py`、`docs/01-state-and-propose.md`
- **変更内容:** gemini の作業ディレクトリへ `.gemini/settings.json` を置き、
  読み取り側の除外を無効にする。版差に備えて `context.fileFiltering` と
  `fileFiltering` の両方を書く。`.gemini/` ごと差分に出さない
- **満たす受け入れ条件:** 8
- **進め方:** 配置内容を確かめる失敗テスト → 実装

### Task 6: 語彙の許容値をプロンプトへ列挙する

- **対象ファイル:** `scripts/refactor.py`、`scripts/launch-cli.sh`、`prompts/propose.md`、`tests/test_init.py`
- **変更内容:** `init` が `SMELLS` / `TECHNIQUES` / 重要度を状態ファイルの `vocabulary` へ書き、
  `launch-cli.sh` が jq で読んでプロンプトへ差し込む
- **満たす受け入れ条件:** 9
- **進め方:** `vocabulary` の記録を確かめる失敗テスト → 実装 → 雛形の更新

### Task 7: 初期化で認証状態を確認する

- **対象ファイル:** `scripts/refactor.py`、`tests/test_init.py`、`SKILL.md`、`docs/01-state-and-propose.md`
- **変更内容:** 参加ランタイム（提案・レビューの母集合 ∪ 適用の母集合 − ホスト）ごとに
  認証確認コマンドを実行し、失敗したら初期化ごと中断する。結果を状態ファイルへ残す
- **満たす受け入れ条件:** 10
- **進め方:** 未認証を模す失敗テスト → 実装 → 前提の記載を更新

### Task 8: 配布物を生成する

- **対象ファイル:** `plugins/ndf-{claude,codex,kiro}/`
- **変更内容:** `bash scripts/build-runtime-plugins.sh` を実行する
- **満たす受け入れ条件:** 11
- **進め方:** 生成後に差分を確認する（テスト駆動の対象外。生成物のため）

## 影響範囲

- `cross-refactoring` の全フェーズ（初期化・提案・適用・レビュー・修正・見送り）
- `cross-review` 共通層は**変更しない**（`assignment` / `statefile` / `monitor` はそのまま）
- 配布物 3 系統

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 積み直しの競合が頻発し、実質ラウンド単位になる | 退避したことを状態と報告へ残し、頻度を実機で測れるようにする |
| 認証確認コマンドが CLI の版で変わる | ランタイムごとに 1 箇所へ表として置き、失敗理由に実行したコマンドを出す |
| 範囲検査が厳しすぎて正当な変更まで落ちる | 範囲の判定は前方一致のみ。除外規則は作らない（設定が増えると検証が骨抜きになる） |

## 切り戻し手順

- コード変更のみでデータ移行は無い。ブランチごと戻せる
- 状態ファイルへ追加した項目は欠けていても読めるため、旧版の状態ファイルとも互換

## 完了の定義

- [ ] 受け入れ条件 1〜11 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest python -m pytest <2 つの tests ディレクトリ> -q` が全件成功
- [ ] `python3 scripts/check-skill-frontmatter.py` が成功
- [ ] `claude plugin validate` が成功
- [ ] `/ndf:cross-review` が収束
