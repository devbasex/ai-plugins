# #312 / #315: 保持の判定を陳腐化の規則へ揃え、台帳の更新の失敗をすべての呼び出しで受け取る（実装計画）

## 関連リンク

- 課題: #312（`ndf_lock_is_held` が pid の無いロックを持ち主ありと返す）、#315（`worktree-testenv.sh` が `wt_registry_update` の失敗を見ない）
- 要求と受け入れ条件: [issue-312-315-requirements.md](issue-312-315-requirements.md)（AC1〜AC18）
- 設計: [issue-312-315-design.md](issue-312-315-design.md)（決定 1〜6）。設計 PR は #629（マージ済み）

## モード

standard（親が判定済み。複数ファイルにまたがる挙動の変更で、設計文書が要る）。

## 目的と非目的

達成したい状態:

- 持ち主を書く前に落ちたプロセスのロックが残っても、5 分を過ぎればテスト環境が `reap` の停止の対象へ戻る
- 台帳へ書けなかったとき、`worktree-testenv.sh` のサブコマンドが完了を装わない。書けなかった事実と、次にすることが標準エラーに出る

やらないこと（要求の「含まない」のとおり）:

- `worktree-common.sh` の変更（`wt_slot_acquire` の終了コードの区別は #626。同じファイルの別の節を担当 B が触る）
- `reap` による残ったロックのディレクトリの削除（決定 2）
- `expose` の先着 1 本の関門（485 行）の変更。既に `|| return 1` で受けている
- 陳腐化の分数（`NDF_LOCK_STALE_MINUTES`）の変更

## 前提

- 前提 1: 台帳の更新の失敗はどの原因でも `wt_registry_update` の終了コード 1 として届く（決定 5）。文言で原因を区別しない
- 前提 2: `last_used_at` を読むのは `reap` だけである。書けなくても割り当て・ポート・公開の記録は食い違わない（決定 3）
- 前提 3: テストで失敗を作る手段は `PATH` の先頭に置く偽の `jq` と、生きている `pid` で握った台帳のロックの 2 つである（決定 6）

## 受け入れ条件

要求文書の AC1〜AC18 をそのまま使う。番号はそれに揃える。検証手段は設計文書の「テスト設計」の表にある。

## 修正対象

- `plugins/ndf/scripts/lib/lock-common.sh` — `ndf_lock_is_held` の判定
- `plugins/ndf/scripts/worktree-testenv.sh` — 台帳の更新 10 か所と小関数 `touch_or_warn`
- `plugins/ndf/skills/worktree/tests/test_testenv.py` — 追加するテスト
- `plugins/ndf/skills/worktree/references/test-execution.md` — `reap` の説明に 1 文

`plugins/ndf/dev.agy/scripts` は `../scripts` への symlink のため、配布物の同期で差分は出ない。

## タスク分解

### Task 1: 保持の判定を陳腐化の判定の否定にする（#312）

- **対象ファイル:** `lock-common.sh`、`test_testenv.py`（排他の節）
- **変更内容:** `ndf_lock_is_held` を「ディレクトリであること」と「`_ndf_lock_is_stale "$dir" "$(cat "$dir/token")"` の否定」で書く（設計の「処理の流れ: 保持の判定」の形）。説明コメントも「陳腐化していなければ 0」へ直す
- **満たす受け入れ条件:** AC1〜AC6、AC18
- **進め方:** AC1（古い空のロックで 1）が変更前で落ちることを確かめてから実装する。AC2〜AC6 はパラメータ化して足す。AC18 は既存テストが変更なしで通ることで見る

### Task 2: `reap` が古い空のロックのテスト環境を止める（#312）

- **対象ファイル:** `test_testenv.py`（reap の節）、`references/test-execution.md`
- **変更内容:** 稼働中のコンテナを返す偽の実行系（`stub_docker_with_running_container`）で、実行中のロックの状態ごとに `stop` の有無を固定する。参照文書の `reap` の説明に「持ち主を書く前に落ちたロックは 5 分で対象へ戻る」の 1 文を足す
- **満たす受け入れ条件:** AC7、AC8
- **進め方:** AC7 が変更前で落ちること（`stop` が呼ばれない）を確かめる。Task 1 の実装で通る見込みで、このタスクの実装はテストと文書だけになる

### Task 3: 偽の `jq` を作る補助関数（テストの足場）

- **対象ファイル:** `test_testenv.py`
- **変更内容:** 引数のどれかが指定の部分文字列を含むときだけ終了コード 5 で終わり、それ以外は本物の `jq` へ渡す偽の `jq` を、`PATH` の先頭に置く補助関数を 1 つ足す（決定 6）
- **満たす受け入れ条件:** AC9〜AC16 の前提
- **進め方:** 偽の `jq` を単独で動かし、狙った代入式の呼び出しだけが落ちることを実際に確かめてからテストへ組み込む（採番の `jq` プログラムに当たらないこと）

### Task 4: `env` でポートの記録と解放の失敗を受ける（#315）

- **対象ファイル:** `worktree-testenv.sh`（`do_env`）、`test_testenv.py`
- **変更内容:** 139 行の `wt_slot_release` と 144 行の `wt_slot_set_ports` の戻り値を受ける。ポートの記録に失敗したら JSON を出さず、`had_slot` が 0 なら解放を試み、1 を返す（決定 4）
- **満たす受け入れ条件:** AC9、AC10
- **進め方:** AC9 が変更前で落ちること（0 と JSON）を確かめてから実装する

### Task 5: `up` で基準のタグの記録の失敗を受け、時刻の記録は警告に留める（#315）

- **対象ファイル:** `worktree-testenv.sh`（`do_up`、新設の `touch_or_warn`）、`test_testenv.py`
- **変更内容:** 266 行の `wt_registry_update` の失敗で `compose up` を呼ばずに 1 を返す。277 行の `wt_slot_touch` を `touch_or_warn` に置き換える（常に 0 を返す。決定 3）
- **満たす受け入れ条件:** AC11、AC12、AC16（`up` の側）
- **進め方:** AC11 が変更前で落ちること（`compose up` が呼ばれる）を確かめてから実装する。AC12 は台帳のロックを生きている `pid` で握って 1 件だけ作る（待ちの上限 5 秒がかかる）

### Task 6: `test` の時刻の記録を警告に留める（#315）

- **対象ファイル:** `worktree-testenv.sh`（`do_test`）、`test_testenv.py`
- **変更内容:** 374 行・388 行の `wt_slot_touch` を `touch_or_warn` に置き換える。コマンドの終了コードはそのまま返す
- **満たす受け入れ条件:** AC16（`test` の側）
- **進め方:** `run` が `exit 3` の宣言で、偽の `jq`（`.last_used_at = (now`）のもとで 3 と警告を確かめる

### Task 7: `down` / `unexpose` / `expose` で解放と公開の記録の失敗を知らせる（#315）

- **対象ファイル:** `worktree-testenv.sh`（`do_down`、`do_unexpose`、`do_expose`）、`test_testenv.py`
- **変更内容:** 306 行の `wt_slot_release`、424 行の `_close_record`、510 行の巻き戻しの `_close_record` の戻り値を受け、設計の表の文言を出す。510 行は「記録を戻しました」を成功のときだけ出す
- **満たす受け入れ条件:** AC13、AC14、AC15
- **進め方:** AC15 が変更前で落ちること（「記録を戻しました」が出る）を確かめてから実装する

### Task 8: 退行の確認

- **対象ファイル:** なし
- **変更内容:** `uv run --with pytest pytest plugins/ndf/skills/worktree/tests scripts/tests/test_lock_common.py -q`、`bash -n` / `dash -n`、`shellcheck`（あれば）
- **満たす受け入れ条件:** AC17、AC18
- **進め方:** 検証だけ

## 影響範囲

- `reap` が古い空のロックを持つテスト環境を止めるようになる。呼び出し元は `do_reap` の 1 か所（`grep -rn lock_is_held` で確認）
- `env` / `up` の終了コードが、台帳へ書けないときに 0 から 1 へ変わる。`up` / `test` は `do_env >/dev/null` で `env` を呼ぶため、`env` の JSON を出さない変更の影響を受けない
- `workflow-common.sh` は保持の判定を使わない

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `_ndf_lock_is_stale` の規則を変えると保持の判定も変わる | 意図した結合である。AC5 で「否定と一致する」ことを固定し、片方だけが変わる形へ戻れないようにする |
| 偽の `jq` の部分文字列が、狙った呼び出し以外にも当たる | Task 3 で単独に動かして確かめる。採番のプログラムは `名前: 値` の形で当たらない（設計の実測） |
| AC12 の待ち 5 秒でテストが遅くなる | 1 件だけに留める（設計の決定 6） |
| `worktree-testenv.sh` の触る範囲は 10 か所で 1 ファイルに散る | タスクごとにテストを通す。構造改善は実装の後の cross-refactoring で足りる（触る範囲が狭く、テストが厚い） |

## 切り戻し手順

- 変更は 4 ファイルに閉じ、データ（台帳）の形は変わらない。PR の取り消しで戻る

## 完了の定義

- [ ] AC1〜AC18 をすべて満たし、条件ごとにテストが対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `claude plugin validate .` が終了コード 0
- [ ] `bash scripts/build-runtime-plugins.sh --check` が差分なし
- [ ] cross-refactoring が収束し、最後の cross-review が `APPROVE`
