# レビューの収束判定に未解決の指摘を入れる（#33 / #37）

## 関連リンク

- https://github.com/devbasex/ai-plugins/issues/33 — 再開したラウンドで即収束し、前のラウンドの未解決の指摘が判定に入らない
- https://github.com/devbasex/ai-plugins/issues/37 — ラウンドごとの返信と Resolve の抜けを検知できない
- `issues/parallel-batch-01/02-issue-33-37.md` — この変更の指示書

## モード

`standard`。テストが揃っている領域への振る舞いの追加であり、公開インタフェース（サブコマンド）が増える。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 指摘 | Pull Request 上のレビューコメントの塊（review thread）。Resolve できる単位 |
| 未解決の指摘 | Resolve されていない指摘 |
| 引き継いだ指摘 | レビューを再開した時点で残っていた未解決の指摘 |
| 投稿数 | そのラウンドで外部の AI が新しく投稿したインラインコメントの件数 |
| 修正の工程 | 収束ループの Step 5。修正サブエージェントが返信と Resolve まで行う段 |
| 最終スイープ | ループの終了後に残った未解決の指摘を片づける Step 7.5 |
| 進行側 | 収束ループを駆動するメインセッション |

## 目的と非目的

達成したい状態:

- レビューを再開したラウンドで両者が承認しても、再開時点で残っていた未解決の指摘が修正の工程を 1 度も通らないまま収束することがない
- 返信と Resolve を飛ばして次のラウンドへ進んだとき、ループがその場で止まる
- 最終スイープの後に未解決の指摘が 0 件であることを、申告ではなく GitHub 側の実数で確かめる

やらないこと:

- すべてのラウンドで未解決の指摘が 0 件になるまで収束させる形にはしない。承認されたラウンドで軽微な指摘が新しく投稿されるたびにラウンドが増えるため
- 新規に開始したレビューでの未解決の指摘の検出は行わない。「引き継いだ指摘」は再開の時点で決まる
- 収束ループの共通層（`plugins/ndf/skills/cross-review/scripts/lib/`）は変更しない

## 前提

- 前提 1: 未解決の指摘の数え直しは GitHub GraphQL の `reviewThreads` で行い、`gh api graphql --paginate` が 100 件を超えるスレッドも取得する
- 前提 2: 取得に失敗したときは判定を止めない。GitHub 側の一時的な不調でループが進まなくなるのを避けるため、既存の投稿数の突き合わせと同じ扱いにする
- 前提 3: 状態ファイルは版をまたいで残る。この変更で追加する項目が無い状態ファイルを読んでも、既存の経路が動く

## 判定へ入れる対象の分け方

指示書が求める設計上の判断である。判定へ入れる対象を 2 つに分け、次のとおり採否を決めた。

| 対象 | 判定への入れ方 | 理由 |
| --- | --- | --- |
| そのラウンドで新しく投稿された指摘 | 現行どおり、外部の AI が返した判定と重要度で見る | 承認されたラウンドに軽微な指摘が乗るのは通常の経路であり、ここを収束の条件にするとラウンドが増え続ける |
| 引き継いだ指摘 | 修正の工程を 1 度通すまで収束させない。1 度通した後は最終スイープへ渡す | 中断の前に受けた修正必須の指摘が、修正の工程を 1 度も通らずに残る経路を塞ぐ。1 ラウンドで打ち切るため、増えるラウンドは最大 1 回に収まる |

引き継いだ指摘のうち、前のラウンドで対応を見送った軽微な指摘（`deferred` / `rejected`）も対象へ含める。除外する案は採らない。除外には修正サブエージェントが申告するスレッド識別子が要り、申告が欠けたときに重要度の高い指摘まで取りこぼす。含めた場合の費用は再開 1 回あたり 1 ラウンドで、上限が決まっている。

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| 収束の条件に未解決の指摘 0 件を加える | すべてのラウンドで 0 件になるまで続ける | 不採用 | 承認されたラウンドの軽微な指摘でラウンドが増え続ける |
| 引き継いだ指摘を記録し、修正の工程を 1 度通すまで収束させない | 再開の時点で数え、1 ラウンドだけ収束を止める | 採用 | 修正必須の指摘が修正の工程を通ることを保証し、増えるラウンドが 1 回で収まる |
| 文書へ注意を書くだけにする | 最終スイープが受け皿であることを明記する | 一部採用 | 判定の流れの記述は行う。ただし記述だけでは進行側が手順を外れたときに止まらない |

## 受け入れ条件

- [ ] 1. 引き継いだ指摘が記録されているとき、そのラウンドの判定が両者とも承認でも収束せず、修正の工程へ進む（自動テスト）
- [ ] 2. 引き継いだ指摘が無いとき、収束の判定が現行と変わらない（既存テストと自動テスト）
- [ ] 3. 引き継いだ指摘に対して修正の工程を 1 度通した後は、両者が承認したラウンドで収束する（自動テスト）
- [ ] 4. 前のラウンドが修正必須の判定だったのに修正の記録が無い状態で次のラウンドを開始すると、終了コード 5 で失敗する（自動テスト）
- [ ] 5. 前のラウンドで Resolve したと申告されたスレッドが GitHub 側で未解決のまま残っているとき、次のラウンドの開始が終了コード 5 で失敗する（自動テスト）
- [ ] 6. 未解決の指摘を数える経路が取得の失敗と 0 件を区別する。取得できないときは判定を止めず、その旨を出力へ残す（自動テスト）
- [ ] 7. 最終スイープの結果を検証する経路があり、未解決の指摘が 0 件なら終了コード 0、残っていれば終了コード 6 を返す（自動テスト）
- [ ] 8. 0 件にできないとき、残った件数と理由が完了報告（`state.py report` の出力）に含まれる（自動テスト）
- [ ] 9. 修正サブエージェントを起動するテンプレートと最終スイープのテンプレートの両方に、投稿数ではなく未解決の指摘を数え直す指示がある（文書の確認）
- [ ] 10. 判定の流れが `docs/01-state-and-review.md` に書かれている（文書の確認）
- [ ] 11. `uv run --with pytest pytest plugins/ndf/skills/cross-review/tests -q` が通り、既存の 148 件が引き続き通る

## 不変条件

- 未解決の指摘を数える処理は、取得の失敗を 0 件として扱わない
- 状態ファイルへ追加する項目が無くても、既存のサブコマンドが動く
- 引き継いだ指摘が理由で収束を止めるのは、再開 1 回につき最大 1 ラウンド

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `state.py` のサブコマンド | `unresolved-threads` と `verify-sweep` を追加 | 追加のみ。既存のサブコマンドの引数と終了コードは変えない |
| 状態ファイル | `carried_over` / `sweep` / ラウンドの `verdict` / `fix.resolved_thread_ids` を追加 | 追加のみ。項目が無い状態ファイルは従来どおり読める |
| `start-round` の終了コード | 5 を追加（返信と Resolve の抜けを検知） | 新しい失敗の経路。従来の成功時の出力は変えない |
| 収束ループの共通層 | 変更しない | `cross-refactoring` は `scripts/lib/` だけを参照する（`refactor.py:40` の `sys.path.insert`）。`state.py` を読む経路は無い |

## 修正対象

- `plugins/ndf/skills/cross-review/scripts/state.py`
- `plugins/ndf/skills/cross-review/SKILL.md`
- `plugins/ndf/skills/cross-review/docs/01-state-and-review.md`
- `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md`
- `plugins/ndf/skills/fix/SKILL.md`
- `plugins/ndf/skills/cross-review/tests/test_state_unresolved_threads.py`（新規）
- `plugins/ndf/skills/cross-review/tests/test_state_carried_over.py`（新規）
- `plugins/ndf/skills/cross-review/tests/test_state_round_guard.py`（新規）
- `plugins/ndf/skills/cross-review/tests/test_state_verify_sweep.py`（新規）

配布物の同期は `bash scripts/build-runtime-plugins.sh` で行い、同じ Pull Request に含める。

## 状態ファイルへ追加する項目

```json
{
  "carried_over": {
    "detected_at": "2026-08-31T00:00:00+00:00",
    "count": 3,
    "thread_ids": ["PRRT_kwDO..."],
    "fixed_in_round": null
  },
  "rounds": [
    {
      "verdict": "changes_requested",
      "fix": {"resolved_thread_ids": ["PRRT_kwDO..."]}
    }
  ],
  "sweep": {
    "declared_remaining_open": 0,
    "remaining_open": 0,
    "remaining_reason": null,
    "verified": true
  }
}
```

`carried_over.fixed_in_round` は、引き継いだ指摘に対して修正の工程を通したラウンドの番号を入れる。`null` のあいだは収束させない。

## タスク分解

### Task 1: 未解決の指摘を数える

- **対象ファイル:** `scripts/state.py`、`tests/test_state_unresolved_threads.py`
- **変更内容:** GraphQL で未解決の指摘を取得する関数と、件数と識別子を出力するサブコマンド `unresolved-threads` を追加する。取得の失敗は `None` として返し、0 件と区別する
- **満たす受け入れ条件:** 6
- **進め方:** 応答を差し替えるテストを先に書き、通す実装を入れる

### Task 2: 再開したときに引き継いだ指摘を記録し、収束を止める

- **対象ファイル:** `scripts/state.py`、`tests/test_state_carried_over.py`
- **変更内容:** 再開の経路で未解決の指摘を数え、`carried_over` へ記録する。`judge` は記録があり `fixed_in_round` が空のあいだ収束させない。`merge-fix` が修正の工程を通したラウンド番号を書き込む
- **満たす受け入れ条件:** 1 / 2 / 3
- **進め方:** 失敗するテスト（承認 2 件でも収束しない）から入る

### Task 3: 返信と Resolve の抜けで次のラウンドを止める

- **対象ファイル:** `scripts/state.py`、`tests/test_state_round_guard.py`
- **変更内容:** `judge` がラウンドへ判定の結果（`verdict`）を残す。`start-round` は前のラウンドが修正必須の判定で修正の記録が無いとき、または申告どおり Resolve されていないスレッドが残るときに終了コード 5 で止まる
- **満たす受け入れ条件:** 4 / 5
- **進め方:** 判定の結果が無い古い状態ファイルでは、保存された重要度から判定を作り直して同じ経路へ通す

### Task 4: 最終スイープの結果を検証する

- **対象ファイル:** `scripts/state.py`、`tests/test_state_verify_sweep.py`
- **変更内容:** サブコマンド `verify-sweep` が最終スイープの結果ファイルを読み、未解決の指摘を数え直して状態ファイルへ記録する。0 件なら終了コード 0、残っていれば 6。`report` が件数と理由を出力へ入れる
- **満たす受け入れ条件:** 7 / 8
- **進め方:** 残 1 件のときの出力に件数と理由が入ることをテストで固定する

### Task 5: 手順と判定の流れを文書へ残す

- **対象ファイル:** `SKILL.md`、`docs/01-state-and-review.md`、`docs/02-fix-and-rotation.md`、`plugins/ndf/skills/fix/SKILL.md`
- **変更内容:** 判定の流れを図と表で `docs/01-state-and-review.md` へ書く。修正サブエージェントの起動テンプレートと最終スイープのテンプレートへ「投稿数は未解決の指摘の数ではない。GraphQL で数え直す」を入れる。ループの骨組みへ `verify-sweep` を入れる
- **満たす受け入れ条件:** 9 / 10
- **進め方:** 文書の規約（`markdown-writing`）のセルフチェックを通す

## 影響範囲

- `cross-review` の収束ループ。再開したラウンドで承認が揃っても、引き継いだ指摘があれば修正の工程を 1 回通る
- `fix` の手順。返信と Resolve の後に未解決の指摘を数え直す確認が入る
- `cross-refactoring` への影響は無い。共通層（`scripts/lib/`）を変更しないため

## リスクと対処

| リスク | 対処 |
| --- | --- |
| GitHub 側の取得に失敗して判定が止まる | 取得の失敗は `None` として扱い、判定を止めず出力へ残す |
| 再開のたびに軽微な指摘で 1 ラウンド増える | 収束を止めるのは修正の工程を通すまでの 1 回に限る |
| 古い状態ファイルに判定の結果が無い | 保存された重要度から判定を作り直す。作り直せないときは検査を飛ばして警告を出す |
| 返信と Resolve の検査が誤って止める | 申告されたスレッド識別子が無いときは検査を行わない |

## 切り戻し手順

`state.py` の変更は状態ファイルへの追加のみで、既存の項目を書き換えない。取り消す場合は Pull Request を revert すれば、追加された項目は読まれなくなる。

## 完了の定義

- [ ] 受け入れ条件 1〜11 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest plugins/ndf/skills/cross-review/tests -q` が通る
- [ ] `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] `claude plugin validate` が通る
- [ ] 配布物の同期を同じ Pull Request に含めた

## #37 のうち対応が不要な項目

`fix` の返信の呼び出し方は、型付きの指定（`-F in_reply_to`）へ既に直っている。Skill 名も `fix` へ改名済みで、issue 本文にある `resolve-pr-comments` は現存しない。

```console
$ grep -rn "in_reply_to" plugins/ndf/skills/
plugins/ndf/skills/fix/SKILL.md:266:# 特定のコメントに返信（in_reply_to にコメント ID を指定）
plugins/ndf/skills/fix/SKILL.md:268:  -f body="対応しました。" -F in_reply_to={comment_id}
```

issue を閉じるときにこの記録を残す。
