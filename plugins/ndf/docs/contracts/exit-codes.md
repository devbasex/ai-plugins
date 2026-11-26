# 終了コードと停止理由

<!-- ndf check --write-spec が生成する。手で直さない（SSOT は各節の出典） -->

結果 JSON の `status` と終了コードの区分、`stopped` の `cause.kind`（停止理由）である。SSOT は `contracts/facts/exit-codes.json` と `contracts/causes/`（1 種類 1 ファイル）。ルートは `cause.kind` だけで選び、終了コードは `status` の区分を表すだけである

## 終了コード

| 区分 | 終了コード | status | 見出し |
| --- | --- | --- | --- |
| `ok` | 0 | ok | 成功 |
| `failed` | 1 | stopped | 失敗 |
| `unreadable` | 2 | stopped | 読めない・呼び出しの誤り |
| `precondition` | 3 | stopped | 前提が無い・正常な否定の結果 |
| `base_conflict` | 4 | stopped | ベースブランチと衝突 |
| `cd_failed` | 5 | stopped | CD の結果が失敗 |
| `incomplete` | 6 | stopped | 欠けが残る |
| `gate` | 10〜19 | gate | 承認ゲート |
| `pause` | 20〜29 | gate | 判断待ち |
| `deps_missing` | 69 | stopped | 依存の欠け |
| `infra_wait` | 75 | stopped | 一時的な失敗・基盤待ち |

## 停止理由

| 種類 | 意味 | 標準の型 | 終了コード | ルート | 再試行の方針 | 尽きたとき |
| --- | --- | --- | --- | --- | --- | --- |
| `account_unavailable` | 中継のラッパーが、上限か認証の失敗の後に替え先も今のアカウントも用意できず、子を起動せずに会話を送らずに止まった | configuration | 3（precondition） | human | — | — |
| `approval_stale` | 承認の後に承認の対象（HEAD・マージ先）が変わった、または有効な承認が無い。承認ゲートを持つ工程を流し直して質問し直す | validation | 1（failed） | conductor | — | — |
| `bad_report` | 呼んだ作業が結果 JSON を返さなかった・形が違った | validation | 2（unreadable） | conductor | — | — |
| `base_conflict` | ベースブランチと衝突 | runtime | 4（base_conflict） | step | — | — |
| `check_failed` | テスト以外のチェック（静的解析・文書の検査・PR の本文の検査）が落ちた | validation | 1（failed） | step | — | — |
| `ci_pending` | CI のチェックが終わっていない | timeout | 1（failed） | retry | cause.ci-pending | step |
| `config_broken` | プラグインの定義（plugin.json）かコントラクトのファイルが読めず、起動できない | configuration | 2（unreadable） | human | — | — |
| `deploy_failed` | CD の結果が失敗 | runtime | 5（cd_failed） | human | — | — |
| `deps_missing` | 依存の欠け | configuration | 69（deps_missing） | human | — | — |
| `fix_limit` | 改修・収束の回数の上限 | runtime | 1（failed） | human | — | — |
| `format_mismatch` | 計画が記録した形の番号（保存先・耐久の記録・ステートマシンの表のバージョン）が自分のものと違い、レーンを起動しなかった。計画と同じ形の NDF で打ち直す | configuration | 2（unreadable） | human | — | — |
| `format_newer` | 保存先・ステートマシンの表・耐久の記録の形のバージョンが自分より新しい。何も書いていない。新しい NDF で打ち直す | configuration | 2（unreadable） | human | — | — |
| `incomplete` | 手順は終わったが欠けが残る（結果は gaps を持つ） | runtime | 6（incomplete） | conductor | — | — |
| `infra_wait` | 基盤の待ち（Runner が付かない・GitHub の API の上限・作業マシンのメモリが足りずテストを止めた） | communication | 75（infra_wait） | retry | cause.infra-wait | step |
| `input_missing` | 工程の入力のキーがスプリントの文脈に無く、工程を始めなかった | configuration | 3（precondition） | conductor | — | — |
| `interrupted` | 期限・シグナル・遅れの見張りで打ち切った | timeout | 1（failed） | conductor | — | — |
| `invalid` | 読めない・呼び出しの誤り・結果の形の誤り | validation | 2（unreadable） | conductor | — | — |
| `merge_method` | マージの方式がマージ先のブランチで許されていない | configuration | 1（failed） | human | — | — |
| `mvv_record_failed` | cross-review が、承認ゲートの判定に要る MVV の記録の行（redline・deferred）を書けなかった。記録が無いと承認ゲートが PR を省けるため、収束にせず止まる（原因は状態ファイルの mvv_record_error にある） | runtime | 1（failed） | conductor | — | — |
| `mvv_unresolved` | プロジェクト MVV に反する指摘か、PR が足した MVV の根拠の記述の誤りが残った。または、根拠の記述を確かめられなかった（欠けは結果の outputs.gaps にある） | runtime | 1（failed） | conductor | — | — |
| `needs_human` | 判断の呼び出しの回答が 2 回とも形に合わなかった | runtime | 1（failed） | human | — | — |
| `not_applicable` | 正常な否定の結果（立たない・変更なし・飛ばしてよい）。ルートはプランの on_cause が書く | validation | 3（precondition） | conductor | — | — |
| `not_ci` | マージが CI 以外の理由（保護の規則・権限）で断られた | authorization | 1（failed） | human | — | — |
| `precondition` | 前提が無い（設定・認証・対象のファイル） | configuration | 3（precondition） | human | — | — |
| `push_rejected` | push 前の検査（pre-push の hook）が push を断った | validation | 1（failed） | step | — | — |
| `refused` | 不変条件で操作を断った | validation | 1（failed） | human | — | — |
| `restore_unsupported` | PostgreSQL の URL でステートマシンの DB の戻しを求めた。バックアップと戻しは DB の運用で行う | configuration | 3（precondition） | human | — | — |
| `samples_mismatch` | 見本が本物の公開の記述と食い違う（相手の API の形が変わったか、見本が古い） | validation | 1（failed） | step | — | — |
| `spin` | 中継のラッパーが再起動ループの検出（起動から間もないセグメントの続き）に当たり、次のセグメントを起動せずに止まった | runtime | 1（failed） | human | — | — |
| `store_broken` | 記録の DB（ステートマシンの DB か DBOS のキーのファイル）が quick_check を通らない・ジャーナルに書けない。壊れたキーのファイルは broken/ へ移した。ステートマシンの DB は ndf progress restore でバックアップから戻す | runtime | 2（unreadable） | human | — | — |
| `store_busy` | ステートマシンの DB（SQLite）の書き込みの待ちが busy_timeout を超えた | timeout | 75（infra_wait） | retry | cause.store-busy | step |
| `store_in_use` | 排他が要る操作（保存先の退避・ステートマシンの DB の戻し・実行のキーのロック）で、ほかのプロセスが保存先か DB かキーを持っていた。何も変えていない（待ちの上限に当たったときはオーナーのホスト・pid・時刻を添える） | runtime | 3（precondition） | human | — | — |
| `store_unreachable` | 記録の DB へ繋げない・使えない（接続・認証の失敗・途中の切断、SQLite のファイルを開けない・ディスクの満杯や I/O の誤り）。DB は壊れていないのでバックアップから戻さず、打ち直す | communication | 75（infra_wait） | retry | cause.store-unreachable | step |
| `test_failed` | テストのチェック（CI か手元）が落ちた | runtime | 1（failed） | step | — | — |
| `transient` | 一時的な失敗（5xx・接続の切断・Cloudflare の HTML の 403・529 overloaded・secondary rate limit） | communication | 75（infra_wait） | retry | cause.transient | step |
| `unknown` | 種類を決められない（例外・結果 JSON を出さない外部のコマンド） | runtime | 1（failed） | conductor | — | — |
| `unreviewed` | レビューを経た HEAD の後に、レビューを通っていないコミットがある。ルートはプランの on_cause が書く | validation | 3（precondition） | step | — | — |
| `usage_limit` | LLM の利用上限。retry_after があればその秒を待って打ち直し、無ければ人へ戻す | communication | 1（failed） | retry | cause.usage-limit | human |
| `wait_timeout` | 待ちの上限に達しただけで、待つ相手は動いている。conductor は結果の next の同じ待ちを打つ | timeout | 3（precondition） | conductor | — | — |
| `work_stopped` | worker が作業を終えられずに欠けを残した | runtime | 1（failed） | step | — | — |
