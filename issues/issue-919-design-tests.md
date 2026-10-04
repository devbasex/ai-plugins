# #919: 結果を残さなかった担当の振り替え — テスト設計・既存の規則への当てはめ・未確認

設計の本体は [issue-919-design.md](issue-919-design.md)、決定の記録は [issue-919-design-decisions.md](issue-919-design-decisions.md) にある。

## テスト設計

置き場は `plugins/ndf/scripts/tests/`（`scripts/lib/` のテスト）、`plugins/ndf/skills/cross-review/tests/`、`plugins/ndf/skills/cross-refactoring/tests/` である。要求の検証手段の表にある `skills/*/scripts/tests/` は実在しないため、既存のテストが置かれた上の 2 つを使う。

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| AC1 | `after_no_result` の単体テストで 5 つの組（`usage_limit` 以外で未起動し直し → `relaunch`、`usage_limit` → `reassign`、起動し直し後 → `reassign`、外した担当と `busy` は振り替え先にならない、候補が尽きた → `abort`）を確かめる | 順 1 と順 4 を入れ替える・`busy` を候補から引かない・候補が空でも `reassign` を返す |
| AC2 | `kiro` が `usage_limit` で外れた記録のもとで、`kiro` も `kiro-2` も振り替え先・次のラウンドの席にならない | `excluded_runtimes` が席の名前で照合する（`-2` を外し損ねる） |
| AC3・I3 | `available` に無いランタイム（母集合の外の agy、`allowed` の外の者）を候補に入れた入力でも返さない | 候補を `ALL_RUNTIMES` から作る |
| AC4・F6 | claude の `usage_limit` で `pick_account` が名前を返せば同じ席のそのアカウント、`None` なら別のランタイム。理由が `stalled` の 2 度目では `pick_account` を呼ばない | 順 3 の理由の条件を外す・`pick_account` の結果を見ずに別のランタイムへ進む |
| AC5 | 規則の正本を差し替えたテスト（`assignment.NO_RELAUNCH_REASONS` に別の理由を足す）で、judge・`reassign` の答えが変わる。設計の「非機能の実現方式」の検索が 0 件 | judge か `reassign` が自分の集合や分岐で可否を決める |
| AC6・F2 | 偽の監視で 3 者のうち 1 席が `usage_limit` のラウンドを `drive.py` に通すと、振り替え先の席が起動され、judge が 0 か 2 で進み、`final` が `error` にならない | `drive.py` が `REASSIGNED` を読まない・judge が `reviewers` を書き換えない |
| AC7 | 同じ形で、`stalled` が 2 度続いた席が振り替えられて先へ進む | 2 度目を今のとおり中断する |
| AC8・F3・I2 | 外した担当が記録にある状態で `start-round` を打つと、以後のラウンドの席にそのランタイムが出ない。状態ファイルに件と理由が残る | `_round_reviewers` が `seats_pool` を通さない |
| AC9 | 2 者で 1 席が `usage_limit`、または `--only` の席が `usage_limit` のとき、`final = error`・終了コード 1 で、メッセージに試した担当と理由が並ぶ | 候補の無いときに 7 を返す・`--only` で振り替える |
| AC10 | 振り替えのある judge の出力に `REASSIGNED` の 1 行が出て、`RELAUNCH_*` の 3 行は起動し直しの席があるときだけ今の形で出る | `REASSIGNED` の席を `RELAUNCH_AGENTS` に混ぜる |
| AC11・F4・I7 | 実装担当が `usage_limit` の後、範囲が取り消され、振り替え先が同じ工程で起動され、前の工程で採った項目のコミットと状態が変わらない | 範囲を取り消さずに振り替える・採用済みの項目を範囲に含める |
| AC12 | リファクタリング計画の後の振り替えで止まらない。再開で参加者を作り直したときは今のとおり止まる | 振り替えに `_recheck_implementer` の止まり方を当てる |
| AC13 | 最終ゲート修正担当が `usage_limit` で、候補があれば次の修正ラウンドが振り替え先で起動され、`no_relaunch` が立たない。候補が無ければ今の打ち切り | `_close_failed_final_fix` が起動結果から `no_relaunch` を立てる |
| 決定 14（`fix_no_relaunch`） | `fix` 工程で `usage_limit` になり振り替え先の候補が無いとき（`abort`）、`fix_no_relaunch` だけが立って `final_gate.no_relaunch` は立たない。次の `verify` は時計の締め切りの前でも `VERIFY=fix` を出さず、範囲テストで落ちた項目と全体テストを落とした原因の項目の両方を取り消して `VERIFY=done` で進み、最終ゲート修正は今のとおり起動できる | `fix` の `abort` で `final_gate.no_relaunch` を流用する・旗を `_give_up` の経路だけで読み、全体テストの経路が `VERIFY=fix` を出す |
| AC14・F5 | 提案担当の全員が結果なしなら、提案を出していない担当（claude の別のアカウントを含む）で起動される。一部だけが結果なしなら今のとおり残りの提案で進み、`reassign` を打たない | 一部の結果なしでも `reassign` を打つ・全員の結果なしで今のとおり止まる |
| AC15・F7 | 振り替えの後の状態ファイルに `no_results` の件（工程・試行・元の担当・振り替え先・理由）があり、結果 JSON の `metrics.reassigned` が件数と一致する。振り替えが無ければ 0 | 件数を `failed_attempts` から数える |
| AC16 | PR #1673 の再現: 14 件を計画した実行で、実装の工程の claude が `usage_limit`。手の操作なしで振り替え先が 14 件を引き継ぎ、最終ゲートまで進み、`metrics.exit` が 4 にならない | `drive.py` が今のとおり `Stop` する |
| AC17 | 結果なしの無い実行で、席・実装担当・状態ファイルの欄（`no_results` は空か無い）・終了コードが今と同じ。既存のテストが通る（結果なしの振る舞いを確かめる既存のテストは、下の当てはめの表のとおり期待値を直す） | 結果なしが無くても `reviewers` を書き換える・`metrics` の既存の欄を変える |
| AC18 | `usage_limit` 以外の 1 度目の結果なしは、cross-review で同じ席を 1 度だけ起動し直す | 1 度目から振り替える |
| I1 | 判定を 2 度打ち直しても、記録の既存の件が変わらず、同じ件が重ならない | 記録を上書きする・打ち直しで同じ件を足す |
| I4 | 同じ工程・試行・担当で `relaunch` は 1 度だけ。アカウントを替えた担当はもう 1 度起動し直せる | 照合の鍵からアカウントを外す |
| I5 | 参加者 3 者・アカウント 2 つで全員が結果なしを続けても、`reassign` の件数が 5 を超えず `abort` で終わる | 外した担当を候補へ戻す |
| I6 | 3 者で 1 席を振り替えた後、2 つの席のランタイムが重ならない | `busy` を引かない |
| I8 | `--only` の実行で答えが `reassign` にならない（AC9 と同じテストで縛る） | 順 2 を外す |
| I9 | 振り替えの後の状態ファイル・耐久ステップの引数・出力の行にトークンの値も環境の辞書も現れず、アカウントの名前だけが現れる。子の環境に `CLAUDE_CODE_OAUTH_TOKEN` などのトークンの変数が無い | 環境の辞書を状態ファイルへ保存する・`account_env` を通さずに環境を組む |

## 既存の規則への当てはめ

振り替えは「結果なしの後の出口」という値の集合に `reassign` を足し、judge の終了コード 7 の意味を広げ、`implementer_reason` に `reassigned` を足す。既存の値だけを想定した規則のうち、当てはまらないものを変える対象として挙げる（構成要素の表に載せた）。

| 既存の規則 | 置き場 | 当てはまるか | 扱い |
| --- | --- | --- | --- |
| 判断の結果なしは 2 度目で中断する | `judge.py` の `_handle_no_result_round` | 当てはまらない | 規則の答えに従う |
| 7 は 1 度だけ受ける | cross-review の `drive.py` の `collect_reviews` | 当てはまらない | 7 の間繰り返す |
| 利用上限の最終ゲート修正は打ち切る | `final_fix.py` の `_close_failed_final_fix`・`gate.py` の `_final_fix_stop` | 当てはまらない | `no_relaunch` は `abort` のときだけ立てる |
| 監視の非ゼロ（上限での打ち切りを除く）で実装の工程は止まる | cross-refactoring の `drive.py` の `impl_phase` | 当てはまらない | `reassign` を打つ |
| 提案担当の全員が結果なしなら止まる | cross-refactoring の `drive.py` の `propose` | 当てはまらない | `reassign` を打つ |
| 実装担当は 1 回の実行で 1 者 | `assignment.choose_implementer` の説明、cross-refactoring の `SKILL.md` | 当てはまらない | 「振り替えのときだけ替わる」と書き直す |
| 席の記録は開いた時点で決まる（`start_round.py` の「後から引き直さない」） | `start_round.py` | 当てはまる | 振り替えは引き直しでなく、記録に残す書き換えである。説明に 1 文足す |
| 実装担当の決め方の表示 | `report.py` の実装担当の行 | 当てはまる | `reassigned` もそのまま表示できる |
| 結果なしの期待値（今の振る舞いを確かめるテスト） | `test_judge_no_result_reason.py`・`test_state_no_result.py`・`test_intake.py`・`test_final_fix.py`・`test_refactor_drive_resume.py`・`test_monitor_outcome_unit.py` | 一部当てはまらない | 候補があるときの期待値を振り替えへ直す。候補が無いとき（2 者・`--only`）の中断は残す |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 共有の設定と登録済みアカウントが同じログインのとき | 実装担当が共有の設定（`NDF_CLAUDE_ACCOUNT` が無い）で上限に当たると、試したアカウントは空のまま `pick_account` が選ぶ。同じ人の同じ組織のアカウントが登録されていれば、同じ上限へ振り替わりうる。`account_env` は利用者の識別を照合しているが（#1576）、共有の設定の側の識別とは照らしていない。リリース後テストで、上限の後の 2 件目の記録の理由で確かめる |
| 振り替えた後の締め切り | 決定 13 のとおり延ばさない。振り替えまでに締め切りを使い切った項目は `not_done` になる。PR #1673 では落ちるまで 180 秒で、締め切りへの影響は小さいと見込むが、測っていない |
| judge の中断の後の `drive.py` の止まり方 | 調べた範囲では、judge の 1（`final = error`）を `drive.py` は最終スイープへ回さず止まりとして扱う。judge の文言（「最終スイープを通してから」）と食い違う。この課題の範囲外で、AC9 は今の止まり方を保つ |
| `pick_account` の所要 | judge の中で使用量の取得が走る。上限の後の 1 回だけで、所要は測っていない |
| agy を `--include agy` で戻したときの振り替え | 規則は同じに当たるが、agy の結果なしの理由の分布は #794 の 1 件しか無い |
