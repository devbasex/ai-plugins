# #446: cross-refactoring からレビュー機構の残骸を取り除く

## 関連リンク

| 種類 | 場所 |
| --- | --- |
| 課題 | #446 |
| 要求と受け入れ条件 | [issue-446-cross-refactoring-review-remnants.md](issue-446-cross-refactoring-review-remnants.md) |
| 設計 | [design-refactor-lib-structure.md](design-refactor-lib-structure.md)（PR #460 でマージ済み） |

## モード

`standard`。テストの削除を伴う構造変更で、何を担保しなくなるかの判断が要る。

## 目的と非目的

達成したい状態:

- **起動されない実体と、実態と合わない名前が `cross-refactoring` に残っていない**

やらないこと:

- 段階 2（#440）と段階 3（#441）の構造整理
- `state` の `is_own_pr` / `event_downgrade` の削除。**どこからも読まれていないが**、
  再開時の互換に関わり、#446 の受け入れ条件にも含まれない（別途起票する）
- `test_models_and_metrics.py` の `durations` の `review` キー。集計のテストデータであり、
  実装は `review` を書かない（`apply` / `fix` のみ）。`launch-cli.sh` の `review` フェーズとは
  別の対象である

## 範囲を広げた判断

**`review_post_note` を範囲内へ入れる。** 設計の時点では挙げていなかったが、実測すると
`prompts/review.md` だけがこのプレースホルダを使う。**`prompts/review.md` を消すと、
`launch-cli.sh` の `RF_POST_EVENT_NOTE` と `setup.py` の `_review_post_note` は読み手を失う。**
分けると片方だけが残る。

## 実測（2026-09-07、着手の時点で数え直したもの）

| 対象 | 実測 |
| --- | --- |
| `tests/` の収集 | 580 件 |
| `review` フェーズに触れるテストファイル | 3 本（`test_launch_cli.py` / `test_launch_agy_phases.py` / `test_models_and_metrics.py`） |
| `_prompt_for` を使うテスト | `test_launch_cli.py` の 4 件 |
| `review_post_note` を確かめるテスト | `test_launch_cli.py` と `test_init.py` |
| `is_own_pr` / `event_downgrade` の参照 | `setup.py` の外に 0 件 |

## 受け入れ条件

- [ ] 1. `prompts/review.md` が無く、`launch-cli.sh` がその名前を参照しない
- [ ] 2. `launch-cli.sh` に `review` フェーズが無く、`--phase review` が弾かれる
- [ ] 3. `RF_POST_EVENT_NOTE` と `_review_post_note` と `state["review_post_note"]` が無い
- [ ] 4. `commands/review.py` が `commands/converge.py` になっている
- [ ] 5. `docs/03-review-viewpoints.md` が `prompts/review.md` を指していない
- [ ] 6. **消したテストごとに、何を担保しなくなるかが記録されている**
- [ ] 7. **本番の振る舞いが変わっていない**（生きている経路のテストが通る）
- [ ] 8. 検査 8 本が終了コード 0
- [ ] 9. `uv run --with pytest pytest scripts/tests plugins/ndf -q` が終了コード 0

## テストの扱い（条件 6 の記録）

| テスト | 何を確かめているか | 扱い | 何を担保しなくなるか |
| --- | --- | --- | --- |
| `test_launch_cli.py` の 4 件 | `review_post_note` がレビューのプロンプトへ届くこと | **消す** | 確かめる対象（レビューのプロンプトと注記）そのものが無くなる |
| `test_launch_agy_phases.py` の `MONITOR_TIMEOUT` | フェーズごとの `--print-timeout` が監視の上限より長いこと | **`review` を外して残す** | 無し（性質は他の 3 フェーズで確かめ続ける） |
| `test_models_and_metrics.py` の `durations` | 集計が過去の記録を読めること | **触らない** | 無し（範囲外） |

## 修正対象

- `plugins/ndf/skills/cross-refactoring/prompts/review.md`（削除）
- `plugins/ndf/skills/cross-refactoring/scripts/launch-cli.sh`
- `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/setup.py`
- `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/review.py`（改名）
- `plugins/ndf/skills/cross-refactoring/scripts/refactor.py`（import の名前）
- `plugins/ndf/skills/cross-refactoring/docs/03-review-viewpoints.md`
- `plugins/ndf/skills/cross-refactoring/tests/test_launch_cli.py`（削除）
- `plugins/ndf/skills/cross-refactoring/tests/test_launch_agy_phases.py`
- `plugins/ndf/skills/cross-refactoring/tests/test_init.py`

## タスク分解

### Task 1: `review` フェーズと投稿の注記を取り除く

- **対象ファイル:** `prompts/review.md` / `launch-cli.sh` / `commands/setup.py` /
  `tests/test_launch_cli.py` / `tests/test_launch_agy_phases.py` / `tests/test_init.py`
- **変更内容:** レビューの起動の受け口と、レビューのプロンプトへ渡す注記を消す
- **満たす受け入れ条件:** 1 / 2 / 3 / 6
- **進め方:** `--phase review` が弾かれることを確かめる失敗するテストを先に書く →
  受け口を消す → 依存するテストを表の判断どおりに処理する

### Task 2: `commands/review.py` を `commands/converge.py` へ改名する

- **対象ファイル:** `commands/review.py` → `commands/converge.py` / `refactor.py`
- **変更内容:** ファイル名と import の名前を変える。中身は変えない
- **満たす受け入れ条件:** 4 / 7
- **進め方:** 改名 → 既存のテストが通ることを確かめる（入口経由の参照は段階 2 まで変わらない
  ため、テストの書き換えは要らない）

### Task 3: 文書から消えた実体への参照を外す

- **対象ファイル:** `docs/03-review-viewpoints.md`
- **変更内容:** `prompts/review.md` への言及を消す
- **満たす受け入れ条件:** 5 / 8
- **進め方:** 書き換え → `check-markdown-links.py` と `check-doc-line-limit.py` を通す

## 影響範囲

| 対象 | 影響 |
| --- | --- |
| `cross-refactoring` の生きている経路 | 変わらない（起動されない実体だけを消す） |
| 配布 Skill の数 | 変わらない |
| 4 ランタイムの配布物 | `cross-refactoring` の中身が変わる（全ランタイムへ届く） |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `review` フェーズが実は使われている | 呼び出し元を全文検索で確かめた（同じファイルの中の分岐だけ）。Task 1 の前にもう一度確かめる |
| 改名で import が漏れる | 改名の直後にテストを実行する。`refactor.py` の import は 1 箇所 |
| 消したテストが担保していた性質が失われる | 表に「何を担保しなくなるか」を書き、Pull Request の本文へ載せる |

## 切り戻し手順

コミットを戻すだけで元へ戻る。データ移行も外部の系への操作も含まない。

## 完了の定義

- [ ] 受け入れ条件 9 件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `standard` の検証の段階（限定的な検証 → 全体テスト → 静的解析）を通している
