# #3842: 中継のラッパーが子の claude を起動できないとき、理由の 1 行と終了コード 3 で終える

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/3842
- 要求仕様: [issue-3842-requirements.md](./issue-3842-requirements.md)
- 設計書: [issue-3842-design.md](./issue-3842-design.md)
- 詳細設計: [issue-3842-design-detail.md](./issue-3842-design-detail.md)
- 同じファイルを触る後続: [issue-3814-design.md](./issue-3814-design.md)（#3814）

## モード

standard（pace: normal）

## 目的と非目的

目的は要求仕様の「目的」のとおりで、子の claude を起動できないときに、トレースバックではなく理由の 1 行と停止理由 `precondition`（終了コード 3）で終わり、状態のファイルを `stopped` にすることである。

非目的は要求仕様の「対象範囲」の「含まない」のとおりである。作業ディレクトリを別の場所へ逃がして起動すること・起動の打ち直し・ラッパーを挟まない起動（`_passthrough`）・リレーハブ（`ndf relay spawn`）の起動の失敗は扱わない。

## 前提

- 作業する場所は worktree `/work/ai-plugins/.worktrees/fix/issue-3842`（ブランチ `fix/issue-3842`）で、Pull Request の宛先はスプリントブランチ `sprint/lrelay-r2` である
- **この Pull Request を #3814 より先にスプリントブランチへ入れる。** #3814 は同じ `wrapper.py` の `_start`・`_on_switch`・`_on_resume` を変え、この変更の `_start` の返り値（停止理由か無し）の上に載る
- このリポジトリはテストを CI で打つ設定（`.ndf/project.json` の `test.ci.only: true`）である。テストの合否は Pull Request の CI（`gh pr checks <PR>`）で決める。手元で打つのは静的解析の `bash scripts/check-lint.sh`・`python3 scripts/check-script-structure.py`・`python3 scripts/check-ndf-contracts.py` だけにする
- 起動に使うライブラリ ptyprocess 0.7.0 の振る舞い（`preexec_fn` の例外が型と文のまま親へ返る）は、設計書の「外部の調査」の実測を前提にする

## 受け入れ条件

番号は要求仕様の「受け入れ条件」の番号で、文はそちらが正である。ここには検証の方法だけを添える。

- [ ] 1. PATH に `claude` が無いとき、終了コード 3 と `ndf-relay: ` で始まる 1 行で終わり、トレースバックを出さない — 検証: 偽の起動が `FileNotFoundError` を出す単体テスト（CI）と、マージ前の手動確認（要求仕様の「検証の方法」）
- [ ] 2. 受け入れ条件 1 のとき、`log.jsonl` の最後の行と `wrapper.json` の理由が 1 行と同じ文になる — 検証: 同じ単体テストで 3 か所の文を比べる（CI）
- [ ] 3. 作業ディレクトリが無いとき、最初・`switch`・`account`・`restart` のどの起動でも `spawn` を呼ばずに同じ形で終わる — 検証: 起動の経路ごとの単体テストと、`cli.main(["run", …])` から通すテスト（CI）
- [ ] 4. 次のセグメントの起動に失敗しても、前の子が残らない — 検証: 単体テスト（CI）
- [ ] 5. 起動に失敗したとき `spawn` の呼び出しは 1 回だけである — 検証: 単体テスト（CI）
- [ ] 6. 手順書 `relay.md` の「落ちたときの続け方」に 1 行・状態・続け方が載る — 検証: レビューと `python3 scripts/check-markdown-links.py`
- [ ] 7. 退行しない（既存の中継のテストが通り、起動できるときの引数・環境・作業ディレクトリと `section` の行が変わらない） — 検証: CI の pytest と、本物の擬似端末で子が作業ディレクトリを出すテスト

## 代替案と採否

設計書の「決定の記録」（決定 1・決定 2）にある。この計画で新しく決めることは無い。

## 不変条件

設計書に番号付きの不変条件の表は無い。実装で守る条件は次の 3 つで、どれも設計書と詳細設計の事後条件から引いた。

| 条件 | 出所 |
| --- | --- |
| 起動できる状態では、`spawn` に渡す引数・環境・作業ディレクトリと `section` の行が今と同じ | 要求仕様の受け入れ条件 7 |
| 停止理由の記録（`wrapper.json` の `stopped`・`log.jsonl` の `end` の行）を書くのは `_loop` の `_end` の 1 か所だけ | 詳細設計の「子を起動する関数」 |
| 呼び出し元は前の子を止めてから起動の関数を呼ぶ（今の順を変えない） | 詳細設計の「起動の呼び出し元」 |

## 互換性

- `ndf relay run` が起動できないときの終わり方が、トレースバック（終了コード 1）から `ndf-relay:` の 1 行と終了コード 3 へ変わる。起動できるときの振る舞いは変わらない（要求仕様の「影響」）
- `log.jsonl` の行の種類と `wrapper.json` の形は変わらない
- 停止理由の種類（`contracts/causes.json`）は足さない

## 修正対象

詳細設計の「地図」の表から引き写した。新設のファイルは無い。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/domains/relay/wrapper.py` | `spawn_pty`・`_enter`（新設）・`Settings.cwd_lost`（新設）・理由の文の定数 3 つ（新設）・`Wrapper._before_start`・`_start`・`_loop`・`_on_switch`・`_on_resume` |
| `plugins/ndf/src/ndf/domains/relay/cli.py` | `wrapper_settings`・`_cwd`（新設） |
| `plugins/ndf/skills/development-workflow/references/relay.md` | 「落ちたときの続け方」に 1 行 |
| `plugins/ndf/tests/domains/relay/test_relay_wrapper.py` | 起動の失敗・作業ディレクトリの欠け・本物の擬似端末のテスト |
| `plugins/ndf/tests/domains/relay/test_relay_cli.py` | 設定の生成から通すテスト |

## タスク分解

Task 1〜4 の進め方は「失敗するテストを書く → 通す最小実装 → 整理」で、テストの合否は Pull Request の CI で確かめる。手元では静的解析の 3 つだけを打つ。

### Task 1: 起動の例外を受けて停止理由で終える

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/wrapper.py`、`plugins/ndf/tests/domains/relay/test_relay_wrapper.py`
- 変更内容: 詳細設計の「子を起動する関数」と「起動の呼び出し元」のとおり。`_start` の返り値を停止理由か無し（`Optional[Ended]`）へ変え、`spawn` の `OSError` で置いたマーカーを消して `precondition` の停止理由を返す。理由の文の定数（`LAUNCH_FAILED` など）を置く。`_loop`・`_on_switch`・`_on_resume` は返った停止理由をそのまま返す。最初に書くテストは、偽の起動が `FileNotFoundError` を出すと `Wrapper.run` が `precondition` を返し、`log.jsonl` の最後が `end`、`wrapper.json` が `stopped`、`cli._ended` が `ndf-relay:` の 1 行と 3 を返すもの（設計書の「テスト設計」の再現のテスト）
- 満たす受け入れ条件: 1・2・4・5
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 2: 起動の前に作業ディレクトリを確かめる

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/wrapper.py`、`plugins/ndf/tests/domains/relay/test_relay_wrapper.py`
- 変更内容: 詳細設計の「子を起動する関数」の `_before_start` と `_start` の手順 (1)。プラグインの更新の前と、マーカーを置く前に、作業ディレクトリが絶対パスのディレクトリとして在るかを確かめ、無ければ `NO_CWD` の文で停止理由を返す。テストは無い作業ディレクトリで、最初・`switch`・`account`・`restart` のどれでも更新と `spawn` が呼ばれないことを見る
- 満たす受け入れ条件: 3・5
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 3: 設定の生成で作業ディレクトリを例外なしで読む

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/cli.py`、`plugins/ndf/src/ndf/domains/relay/wrapper.py`（`Settings.cwd_lost`）、`plugins/ndf/tests/domains/relay/test_relay_cli.py`
- 変更内容: 詳細設計の「設定の作業ディレクトリ」のとおり。`_cwd` を新設し、`wrapper_settings` が `Path.cwd()` の代わりに使う。失敗したことを `Settings.cwd_lost` に残し、Task 2 の `_before_start` の確かめがそれを見て止める。テストは作業ディレクトリへ入ってから消し、`cli.main(["run", …])` が終了コード 3 と `ndf-relay:` の 1 行で終わること、同じ名前で作り直しても起動しないこと、`（不明）` の文になることを見る
- 満たす受け入れ条件: 1・2・3
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 4: 子の側で作業ディレクトリへ入り、入れなければ親へ例外を返す

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/wrapper.py`、`plugins/ndf/tests/domains/relay/test_relay_wrapper.py`
- 変更内容: 詳細設計の「擬似端末の起動」のとおり。`spawn_pty` は ptyprocess へ作業ディレクトリを渡さず、`preexec_fn` に `_enter` を渡す。`_enter` は `chdir` の `OSError` を `NO_ENTER` の文で同じ型のまま上げ直す。テストは本物の `spawn_pty` で、(a) 入る権限の無いディレクトリ（root では飛ばす）、(b) 確かめの後に消したディレクトリで `precondition` に終わること、起動できるときに子の `pwd` が作業ディレクトリを出すことを見る
- 満たす受け入れ条件: 1・2・3・7
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 5: 手順書に起動できないときの 1 行を足す

- 対象ファイル: `plugins/ndf/skills/development-workflow/references/relay.md`
- 変更内容: 詳細設計の「手順書」のとおり、「落ちたときの続け方」の表に 1 行を足し、「上の 3 つ以外」を「上の 4 つ以外」に直す。文言のテストは書かない
- 満たす受け入れ条件: 6
- 進め方: 文書の変更なので、`python3 scripts/check-markdown-links.py` でリンクを確かめ、書いた続け方を手順として実行できるかをレビューで見る

## 影響範囲

- 子を起動するすべての経路（最初のセグメント・カットポイントの次のセグメント・`account`・`restart`）。作業ディレクトリへ入る場所が ptyprocess の中から `preexec_fn` へ移るので、起動できるときの振る舞いも Task 4 のテストと既存の中継のテストで確かめる（設計書の「影響と危険」）
- 基点の公開 CLI（`ndf relay run`）の設定の生成
- 手順書 `relay.md` の「落ちたときの続け方」。同じファイルを #3814 が「承認ゲートを越えない守り」の節で変える

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `preexec_fn` で作業ディレクトリへ入る形へ変えて、起動できるときの子の作業ディレクトリ・環境が変わる | タスクごとにテストを通す（Task 4 の本物の擬似端末のテストと既存の中継のテスト） |
| `_start` の返り値の型の変更が、呼び出し元の 1 つで受け漏れる | 先に構造を整える（Task 1 で返り値と 3 つの呼び出し元を同時に変える） |
| #3814 が同じ関数を変え、後から載せるときに衝突する | 実装の後のリファクタリングで足りる（この変更は呼び出し元で `_start` の返りを返す 1 点に絞り、#3814 は先にこの変更を取り込んでから着手する） |
| 入る権限の無いディレクトリのテストが root の CI で意味を失う | タスクごとにテストを通す（root では飛ばし、消したディレクトリの (b) で同じ経路を通す） |

## 切り戻し手順

1. スプリントブランチ `sprint/lrelay-r2` でこの Pull Request のマージコミットを `git revert -m 1 <マージコミット>` で戻す Pull Request を出す
2. #3814 が既に載っていれば、#3814 の `_on_handover` が `_start` の停止理由を返す部分も同時に戻す必要があるので、#3814 の Pull Request も合わせて戻す
3. データの形は変えていないので、記録と状態のファイルの後始末は要らない

## 完了の定義

- 受け入れ条件 1〜7 のチェックがすべて付いている
- Pull Request の CI がすべて通っている（`gh pr checks <PR>`）
- 手元の静的解析（`bash scripts/check-lint.sh`・`python3 scripts/check-script-structure.py`・`python3 scripts/check-ndf-contracts.py`）が通っている
- マージ前の手動確認（PATH から `claude` を外して `ndf relay run` を打ち、標準エラーの 1 行・終了コード 3・`log.jsonl` の最後の行を見る）の結果を Pull Request に書いている
- スプリントブランチ `sprint/lrelay-r2` へマージされ、#3814 の着手者へ知らせてある
