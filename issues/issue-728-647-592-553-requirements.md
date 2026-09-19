# cross-refactoring: 実装担当が結果を残さないと同じ群が上限なしに開き直され、未検証のコミットが残る → 結果なしを取り込みの 1 か所で受けて取り消し、群が開いた回数と結末で開き直しを決める（要求と受け入れ条件 / #728 #647 #592 #553）

## 目的

- **壊れていること**: cross-refactoring の実装担当が結果ファイルを残さずに終わることがある。このとき取り込みは、下位の読み取りがプロセスを終わらせるため止まる。同じ群が上限なしに開き直される。担当が作ったコミットは検証を受けずに残る。テスト整備の採用 0 件では項目の無い群を起動し続ける。claude が担当の群は帰属行のためトレーラーが読めずに落ちる
- **困る人**: cross-refactoring を回す進行側（手で止めるまで CLI の起動と利用料が続く）と、その Pull Request を読む人（未検証の差分が混じる）
- **直すと成り立つこと**: 3 つの取り込み（適用・修正・最終ゲートの修正）が結果なしを同じ手順で受ける。未検証のコミットを取り消し、結末を記録し、終了コードを返す。適用ラウンドの繰り返しは有限回で終わる。群は開いた回数と前回の結末で、開き直すか・担当を替えるか・取り消すかが決まる。採用 0 件では群を作らない。起動し直しても解けない結末（利用上限）では、同じ担当を同じ工程で起動し直さない。帰属行の後ろでもトレーラーが読める

この文書は「何を満たすか」だけを扱う。設計、置き換える既存の要求、範囲に入れる子 issue は末尾の「関連文書と前後関係」にある。

## 用語

本文は左の用語で書く。右の識別子は、引用・表・受け入れ条件の判定値で使う。

| 用語 | 意味 |
| --- | --- |
| 取り込み | 担当の CLI が作ったコミットを、進行側が検証して受け入れるコマンド。適用の取り込み `merge-apply` / 修正の取り込み `merge-fix` / 最終ゲートの修正の取り込み `merge-final-fix` の 3 つ |
| 群を開く | `next-apply-round`。次に適用する群を選んで担当を出す |
| 最終ゲート | `final-gate`。全体のテストを実行して判定する |
| 結末の読み取り | `gitfacts.read_result`。担当の結果ファイルを読む |
| 共通層の読み取り | `lib/monitor_outcome.py` の `read_launch_outcome`。G3 が作る |
| 群 | 適用ラウンド。書き換えるファイルが重ならない項目の集まりで、状態の `rounds[].apply_rounds[]` の 1 件 |
| 試行 | 1 つの群に対して適用担当を起動し、適用の取り込みで取り込もうとした 1 回。番号は `attempt` |
| 結末 | 担当 1 回の起動の終わり方。G3 の `LaunchOutcome`（使える結果か、結果なしの理由か） |
| 結果なし | `LaunchOutcome.payload` が `None`。理由は `reason`（`missing` / `unparsable` / `stalled` など） |
| 起動し直しの可否 | `LaunchOutcome.relaunch_same_agent`。偽は同じ担当を同じ条件で起動しても解けない（`usage_limit`） |
| 結末の記録 | `failed_attempts[]`。結果を残さなかった起動の記録で、群と最終ゲートの記録（`final_gate`）が持つ |
| 取り消しの理由 | 群の `drop_reason`（`no_result` / `empty`） |
| 開き直しの判定 | `rounds.group_reopening` |
| 輪番から担当を引く関数 | `rounds.impl_for_seq` |
| 取り込みの共通手順 | 新設の `refactor_lib/intake.py`。取り消しの本体は `intake.discard_unverified` |
| 範囲 | 取り込みが検査するコミットの列。起点（`apply_base_sha` / `fix_base_sha`）から HEAD まで |
| 未検証のコミット | 範囲にあるが、結果なしで検証を受けられなかったコミット |
| 修正ラウンドの数と上限 | `fix_rounds` と `--max-fix-rounds` |
| 無進捗の許容 | `init` が出す `IMPL_STALL_TIMEOUT`。テストの制限時間（`--test-timeout`）+ 900 秒 |
| 帰属行 | Claude Code がコミットメッセージへ足す `Co-Authored-By:` / `Claude-Session:` の行 |
| トレーラーの段落 | `git interpret-trailers --parse` がトレーラーとして読む段落 |
| トレーラーの読み取り | `gitfacts.commit_trailers` |
| G1 / G3 | 実行計画の束の名前。G1 = 参加者の決め方（#727、PR #782）、G3 = 結末の読み取りの共通層（#729、PR #781） |

## 依頼（原文）

### #728（根本原因の親）

> `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/gitfacts.py` の `read_result` は、結果ファイルが無い・読めないと `die(code=2)` で進行の終了コードを決める（1089 / 1093 / 1096 行目）
>
> それを呼ぶ取り込みが 3 つある（`commands/apply.py` の `merge-apply`、`commands/converge.py` の `merge-fix`、`commands/gate.py` の `merge-final-fix`）。下位の読み取りがプロセスを終わらせるため、群の状態を書く機会と、未検証のコミットを取り消す機会が無い
>
> 群を開き直す `apply.py` の `next-apply-round` は、中断からの再開と失敗した試行のやり直しを同じ `pending` / `applied` で区別しない。`rounds.apply_groups` は項目 0 件の群も作る
>
> ## 採る手
>
> - 向きの修正（`fix_dependency_direction`）: 下位の `read_result` は結果なしを値として返し、進行の扱いは取り込みが決める
> - 統合（`consolidate_duplication`）: 3 つの取り込みの「範囲の確定 → 未検証コミットの取り消し → 群の状態の記録」を 1 つの手順にする
>
> ## 完了条件
>
> - `read_result` は結果なしを値で返し、3 つの取り込みが同じ手順で取り消しと群の状態の記録を行う
> - 群が開いた回数と前回の結末を持ち、開き直しの判定が 1 か所にある。空の群は作らない
> - 各子 issue の再現手順を実行し、現象が出ないことを確かめる

### #647

> `/ndf:cross-refactoring` で、同じ適用ラウンド（書き換えるファイルが重ならない改善項目の群）の適用が**上限なしに再試行される**。後ろの群へ順番が回らず、収束の判定と最終ゲートへ届かない。
>
> **PR #757:** kiro が適用フェーズで 15 秒で終わり `kiro-apply-r1-result.json` を残さない → `merge-apply` が 2 を返し、駆動側の `continue` が `next-apply-round` へ戻る → 同じ群を再び開く。**29 回繰り返した時点で手で止めた。**

### #592

> **テスト整備ラウンドの採用が 0 件のとき、項目の無い適用ラウンドが開き、上限なしに同じ群を繰り返す。**

### #553

> `cross-refactoring` の適用ラウンドで、**claude が実装担当のときだけ**必須トレーラー（`Item-Id` / `Round` / `Impl-Runtime` / `Impl-Model`）が読めず、群が丸ごと取り消される。
>
> **担当に書かせて、git の最終段落の定義で読み返す構造**が、ランタイムが後ろへ段落を足すたびに壊れる。読み取りを段落単位にする直しは現れている場所の直しで、次に別の書式の署名を足すランタイムが現れれば同じ形が起きうる。進行側が知っている値を進行側が取り込みで書けば、担当のランタイムの帰属行の書式に左右されない。

### #674

> `merge-final-fix` は `read_result` が結果ファイルの欠落で `die(code=2)` し、範囲の検査（`unassigned_fix_commits` / `verify_final_fix_commit`）と取り消しへ進まない。`final-gate` は担当が作ったコミットを含む HEAD でテストし、落ちれば `fix_base_sha` を HEAD へ置き直す。そのコミットは以後どの範囲にも入らない。**検証を受けていないコミットが Pull Request に残りうる。**

## 前提

| # | 前提 |
| --- | --- |
| 1 | G3（#729、PR #781）の契約に従う。`lib/monitor_outcome.py` に `read_launch_outcome(tmp_dir, stem, result_path=None) -> LaunchOutcome`（`payload` / `reason` / `detail` / `monitor` / `relaunch_same_agent`）と `NO_RELAUNCH_REASONS = {"usage_limit"}` が入り、理由の語彙は 9 語（`ok` / `timeout` / `stalled` / `early_error` / `usage_limit` / `cli_timeout` / `missing` / `pidfile_bad` / `unparsable`）である。**G3 の実装 Pull Request が `develop` に入った後に、この変更の実装を始める** |
| 2 | 監視の終了コード 0〜6 と標準出力は変わらない（G3 の申し送り）。骨組みは終了コードで分岐しない |
| 3 | P1（監視の結果ファイル `<stem>-monitor.json`）と P2（`--phase` と `lib/limits.py`）は v10.13.0 で `develop` に入っている |
| 4 | 輪番の母集合は G1（#727、PR #782）が変える（既定を codex / kiro / ホストにし、`impl_assign(participants, seq)` を新設）。この変更が担当を引く呼び出しは `rounds.impl_for_seq` の 1 つで、G1 と先後どちらでも変える場所はその中だけである |
| 5 | Claude Code が足す帰属行は、メッセージの末尾に独立した段落として付く（#553 の実測 `26a0fff`）。他のランタイムが足す署名もトレーラーの形（`Key: value` の行だけの段落）である |
| 6 | 除外されない参加者が 2 者以上いる。1 者しかいない実行では、担当の交代先が無いため、結末の可否だけで 2 回目を開くか取り消すかを決める（設計文書の決定 8） |

## 対象範囲

含む:

| 変えるもの | 内容 |
| --- | --- |
| 結末の読み取りの契約 | 結果なしを値で返す。プロセスを終わらせない（`die` しない） |
| 取り込みの共通手順の新設 | 3 つの取り込みの「範囲の確定 → 未検証コミットの取り消し → 結末の記録」を共通化する（`refactor_lib/intake.py`） |
| 取り消しの本体の一本化 | `gitfacts.revert_unverified_range` と `apply._revert_unverified_apply_round` の 2 つを 1 つにする |
| 群の開き直しの判定の一本化 | 開き直しの判定（`rounds.group_reopening`）と、群が持つ試行の記録（`attempt` / `failed_attempts` / `drop_reason`） |
| 試行の上限と担当の交代 | 同じ群の試行の上限（2 回）と、2 回目の担当の交代 |
| 採用 0 件の扱い | 採用 0 件の提案ラウンドで群を作らない。項目の無い群を開かない（#592） |
| 修正の取り込みが読む担当 | 修正の取り込みが読む結果の担当と、結果が無いときの修正ラウンドの数え方 |
| 最終ゲートの修正の結果なし | 最終ゲートの修正の取り込みが、結果なしで未検証のコミットを取り消す（#674） |
| 起動し直せない結末 | 起動し直しの可否が偽のときの 3 つの取り込みの振る舞い |
| トレーラーの読み方 | トレーラーの読み取りの読み方と、適用・修正の雛形のコミットの規約（#553） |
| 無進捗の許容 | 適用・修正・最終ゲートの修正の監視に渡す無進捗の許容と、雛形の進捗マーカー（#647 の無進捗の対策。既存の設計から引き継ぐ） |
| 手順書 | `SKILL.md` の語の表・「別の上限を置かない」の段落・骨組みの監視の引数、`docs/02-apply-and-review.md` / `docs/04-fix-and-report.md` の対応箇所 |

含まない:

| 扱わないもの | 理由 |
| --- | --- |
| `lib/monitor_outcome.py` / `lib/monitor.py` / `lib/launch-cli.sh` の変更 | G3（#729）が所有する。この変更は `read_launch_outcome` を呼ぶ側 |
| `lib/assignment.py` と参加者の決め方 | G1（#727）が所有する。この変更は `rounds.impl_for_seq` の中で呼ぶだけ |
| 利用上限で進行全体を止めること | 結末の可否を見て担当を替えるか、修正の上限へ進める（設計文書の決定 8・10） |
| 骨組みの bash を `scripts/` へ出すこと | #560 |
| 進行側が取り込みで `git commit --amend` によりトレーラーを足し直すこと | 設計文書の決定 13。SHA が変わり申告との対応が切れる。群に複数のコミットがあると先頭の書き換えが以降をすべて書き換える |
| `deferred_items` の 2 通りの形（`rounds.deferred_record` の固定鍵と、`apply.py:178` の提案の複製）を揃えること | この変更が足す見送りは `deferred_record` の形を使う。読む側（`plan.py`）は鍵が無くても落ちない |
| `cross-review` 側の取り込み | G3 が `state.py` の `read-result` を変える |
| `CHANGELOG.md` と版数 | 配布の工程が書く |
| クラス図 | 設計文書の「構造」に触る型（`LaunchOutcome` / `IntakeScope` / `ClosedAttempt`）だけを載せる |

## 受け入れ条件（結末の読み取り）

- [ ] AC1: 結果ファイルが無い状態で結末の読み取り（`gitfacts.read_result`）を呼ぶと、例外（`SystemExit`）を出さず、標準出力・標準エラーに書かない。結果なしの値（`payload` が `None`、`reason` が `missing`）を返す
- [ ] AC2: 監視の結果ファイル（`<impl>-apply-r<R>-monitor.json`）に無進捗の理由（`reason: stalled`）があるとき、結末の読み取りの理由は `stalled`、起動し直しの可否は真である。利用上限の理由（`reason: usage_limit`）のとき、可否は偽である
- [ ] AC3: 結末の読み取りに渡す結果ファイルの名前の幹（stem）は、監視の名前の雛形（`--stem-template`: `{agent}-apply-r$ROUND` / `{agent}-fix-r$ROUND` / `{agent}-final-fix`）を担当名で埋めた値と一致する。幹を組む関数（`paths.stem_for`）の 3 つの工程の値を、骨組みの雛形から作った値と突き合わせる

## 受け入れ条件（共通の手順）

- [ ] AC4: 前提: 結果なしで、起点から HEAD までにコミットが 1 件以上ある
      操作: 3 つの取り込みのいずれかを呼ぶ
      結果: そのコミットは取り消され、起点（`apply_base_sha` と群の `base_sha` / `fix_base_sha` / `final_gate.fix_base_sha`）は取り消し後の HEAD になる
- [ ] AC5: 結果なしのとき、3 つの取り込みのいずれでも、記録の辞書（群 / `final_gate`）の結末の記録（`failed_attempts`）に 1 件（`{phase, attempt, impl, reason, detail, at, reverted}`）が足される。理由（`reason`）は結末の読み取りの値、取り消した数（`reverted`）は取り消したコミットの数である
- [ ] AC6: 結果なしで範囲にコミットが無いとき、`git revert` も `git push` も実行されない
- [ ] AC7: 結果なしの取り込みを、同じ試行番号でもう一度呼ぶと、結果ファイルを読まずに前回と同じ終了コード 2 を返す。結末の記録の件数は増えない。その間に結果ファイルが現れても読まない
- [ ] AC8: 3 つの取り込みで範囲を確定できないとき（起点が無い、または git が範囲を返さない）の終了コードは次のとおりである。適用の取り込みは 4、修正の取り込みは修正ラウンドを 1 進めて 2、最終ゲートの修正の取り込みは 2

## 受け入れ条件（#647: 適用ラウンド）

- [ ] AC9: 群が 2 つ（1 つ目の担当 agy、2 つ目の担当 codex）の状態で、1 つ目の結果ファイルを置かずに群を開く → 適用の取り込みを呼ぶ。終了コードは 2。1 つ目の群は未着手（`status: pending`）のまま担当が agy 以外に替わる。試行の番号（`attempt`）は 1、結末の記録は 1 件（`phase: apply`、`attempt: 1`、`impl: agy`）である
- [ ] AC10: AC9 の後、替わった担当の結果ファイルも置かずにもう一度、群を開く → 適用の取り込みを呼ぶ。1 つ目の群は取り消し済み（`status: dropped`・`drop_reason: no_result`）、項目は `abandoned` になる。見送り（`deferred_items`）に `実装担当が結果を残しませんでした（agy: missing → codex: missing）` の形の理由で入る
- [ ] AC11: 結果ファイルを 1 つも置かずに、群を開く操作が 1 を返すまで繰り返す。群を開く操作の呼び出しは 5 回（開く 4 回 + 尽きた 1 回）で終わり、両方の群が取り消し済み（`dropped`）になる
- [ ] AC12: 結果ファイルが JSON として読めない場合と JSON の配列の場合も AC9 と同じ状態になり、結末の記録の理由（`failed_attempts[].reason`）は `unparsable` である
- [ ] AC13: 群が 4 つ（輪番の通し番号 `apply_seq` が 4）あり、先頭の群（担当 codex）が結果を残さない。替えた後の担当は codex 以外である。輪番の通し番号は進めた分だけ進み、他の群の担当は変わらない
- [ ] AC14: 監視の結果ファイルの理由が `usage_limit` で、輪番から担当を引く関数（`rounds.impl_for_seq`）の差し替えにより交代先が無い状態では、1 回目の失敗で群が取り消し済み（`dropped`、`drop_reason: no_result`）になる。理由が `missing` で交代先が無い状態では、同じ担当で 2 回目を開く
- [ ] AC15: 群を開く操作を、適用の取り込みを挟まず 2 回呼ぶ（取り込みの前に進行が止まった再開）。群の試行の番号は 1 のまま進まない
- [ ] AC16: 着手前のテストの状態が `green` でない状態で適用の取り込みを呼ぶと、結果ファイルを読まずに終了コード 4 で終わる
- [ ] AC17: 適用の取り込みが終了コード 2 で終わった後の群は、取り消し済み（`dropped`）か、結末の記録を持つ未着手（`pending`）のどちらかである。確かめる経路は 4 つ（結果なし / 未割当のコミット / 適用の検証の失敗 / 取り込み済みで採用 0 件）
- [ ] AC18: 開き直しの判定（`rounds.group_reopening`）を差し替えると、群を開く側の開き方（開く・再開・開かない）と、適用の取り込みの結果なしの後の扱い（担当の交代・取り消し）の両方が、差し替えた関数の返す値に従う

## 受け入れ条件（#592: 採用 0 件と項目の無い群）

- [ ] AC19: テスト整備ラウンドで提案が 0 件の状態で提案の取り込み（`merge-proposals`）を呼んだ後、群を開く操作を呼ぶ。1 回目で終了コード 1 を返し、そのラウンドの群の配列（`apply_rounds`）は空のままである
- [ ] AC20: 群の配列の鍵（`apply_rounds`）を持たない状態ファイル（群を導入する前の版）では、群を開く操作が従来どおりラウンド全体を 1 つの群として開く
- [ ] AC21: 前提: rf587 で残った形の群（`status: applied`・`items: []`・`apply.merged_at` あり・`applied: []`）
      操作: 適用の取り込みを呼ぶ
      結果: 終了コード 2 で終わり、群が取り消し済み（`dropped`、`drop_reason: empty`）になる。続く群を開く操作は 1 を返す
- [ ] AC22: 未着手で項目が無い群（`status: pending`・`items: []`）を持つ状態で、群を開く操作を呼ぶ。その群は開かれずに取り消し済み（`dropped`、`drop_reason: empty`）になる。次の群があればそれを開き、無ければ終了コード 1 を返す

## 受け入れ条件（修正ラウンド）

- [ ] AC23: 群の担当が agy、提案ラウンドの担当が codex の状態で、agy の結果ファイル（`agy-fix-r1-result.json`）を置いて修正の取り込みを呼ぶ。agy の結果が取り込まれ、修正ラウンドの数（`fix_rounds`）が 1 になる
- [ ] AC24: 修正の結果ファイルが無い状態で修正の取り込みを呼ぶと、終了コード 2 で終わり、修正ラウンドの数が 1 進む。群の結末の記録に `phase: fix` の 1 件が足される。上限（`--max-fix-rounds`）の回数だけ続けた後の見送りの判定（`should-abandon`）は終了コード 0 を返す
- [ ] AC25: AC24 の直後に検証（`verify-round`）を挟まず修正の取り込みをもう一度呼んでも、修正ラウンドの数は進まない（AC7 の修正ラウンドの形）
- [ ] AC26: 修正の結果なしで監視の理由が `usage_limit` のとき、修正の取り込みは修正ラウンドの数を上限の値にする。続く見送りの判定は終了コード 0 を返す
- [ ] AC27: 修正の結果なしで起点から HEAD にコミットがあるとき、取り消され、起点（`fix_base_sha`）が取り消し後の HEAD になる（AC4 の修正ラウンドの形）

## 受け入れ条件（#674: 最終ゲートの修正）

- [ ] AC28: 前提: 最終ゲートの修正の結果ファイルが無く、最終ゲートの起点（`final_gate.fix_base_sha`）から HEAD にコミットが 1 件ある
      操作: 最終ゲートの修正の取り込みを呼ぶ
      結果: 終了コード 2。そのコミットは取り消され、最終ゲートの起点は取り消し後の HEAD になる。最終ゲートの結末の記録（`final_gate.failed_attempts`）は 1 件（`phase: final-fix`）
- [ ] AC29: AC28 の後に最終ゲートを呼ぶと、テストは取り消し後の HEAD で実行され、修正のコミットの一覧（`fix_commits`）に取り消したコミットは入らない
- [ ] AC30: 最終ゲートの修正の結果なしで監視の理由が `usage_limit` のとき、最終ゲートの修正ラウンドの数（`final_gate.fix_rounds`）は上限の値になる。続く最終ゲートは、テストが落ちれば終了コード 1（取り消さず報告）で終わる
- [ ] AC31: 結果ファイルがあり検証を通る最終ゲートの修正は、変更前と同じく取り込まれ、最終ゲートの結末の記録を持たない

## 受け入れ条件（#553: 帰属行の後ろのトレーラー）

一時リポジトリで実際にコミットを作って確かめる:

- [ ] AC32: 必須トレーラー 4 つの段落の後に、空行を挟んで `Co-Authored-By:` の段落が付いたコミットで、トレーラーの読み取りが 4 つとも値を返す
- [ ] AC33: AC32 の段落の後に `Co-Authored-By:` と `Claude-Session:` の 2 行の段落が付いても、4 つとも返す
- [ ] AC34: 必須トレーラーの段落と末尾の段落の間に散文の段落があるコミットで、散文より前にある `Round: …` の形の行を読まない
- [ ] AC35: 末尾の段落に散文とトレーラーの形の行が混ざる（git がトレーラーの段落と判定しない）コミットで、その行を読まない
- [ ] AC36: 同じ鍵が 2 つの段落にあるとき、末尾に近い段落の値を返す
- [ ] AC37: AC32 の形のコミットを申告した適用ラウンドが、トレーラーの欠落で取り消されない
- [ ] AC38: 本文がトレーラーの段落 1 つだけで、題名が `Round: 本文の題名` の形のコミットで、題名を読まない
- [ ] AC39: 適用と修正の雛形（`prompts/apply.md` / `prompts/fix.md`）のコミットの規約が、必須トレーラーをメッセージの最後の段落に置くことを書く

## 受け入れ条件（無進捗の打ち切り）

- [ ] AC40: 起動（`init`）の出力に無進捗の許容（`IMPL_STALL_TIMEOUT`）が入り、値がテストの制限時間（`--test-timeout`）の値 + 900 である（既定で 1800）
- [ ] AC41: `SKILL.md` の骨組みで、適用・修正・最終ゲートの修正（`--phase apply` / `fix` / `final-fix`）の 3 つの監視（`monitor.py`）の呼び出しが `--stall-timeout "$IMPL_STALL_TIMEOUT"` を持ち、`--timeout` を持たない
- [ ] AC42: 適用・修正・最終ゲートの修正の雛形（`prompts/apply.md` / `fix.md` / `final-fix.md`）が、作業段階ごとに進捗の記録（`$RF_STEM-progress.log`）へ 1 行追記する指示を持つ

## 受け入れ条件（文書）

- [ ] AC43: `SKILL.md` の「この Skill で使う語」の適用ラウンドの行が、同じ群の試行の上限（2 回）を書く。`grep -n "別の上限を置かない\|別に置かない" SKILL.md` が何も出力しない
- [ ] AC44: `docs/02-apply-and-review.md` の Step 4 と `docs/04-fix-and-report.md` の Step 6・Step 7 が 2 つを書く。結果なしのときの取り込みの振る舞い（取り消し・記録・終了コード）と、`SKILL.md` と同じ監視の引数である
- [ ] AC45: `docs/02-apply-and-review.md` のトレーラーの節が、git の標準の読み方（`git log --format='%(trailers:…)'`）が最後の段落しか読まないことと、進行側の読み方の 2 つを書く

## 受け入れ条件（退行しない）

- [ ] AC46: 結果ファイルがあり検証を通る適用ラウンドは、変更前と同じく 1 回目の試行で取り込まれ、結末の記録を持たない
- [ ] AC47: `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] AC48: 配布物の同期・定義・frontmatter の 3 つの検査が終了コード 0 で終わる（コマンドは「検証手段」の表）

## 受け入れ条件（他の設計との契約）

- [ ] AC49: 輪番から担当を引く関数（`rounds.impl_for_seq`）を差し替えると、群を割り当てたときの担当・結果を残さなかった群の交代先・最終ゲートの修正担当の 3 つが、差し替えた関数の返す担当になる
- [ ] AC50: 取り消しの本体は取り込みの共通手順の 1 つ（`intake.discard_unverified`）になる。`gitfacts.revert_unverified_range` は無くなり、`apply._revert_unverified_apply_round` は `discard_unverified` を呼ぶ

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 中断と再開を挟まない実行で、1 つの提案ラウンドで適用担当を起動する回数が群の数 × 2 回を超えない。修正担当を起動する回数も群の数 × `--max-fix-rounds` 回を超えない。最終ゲートの修正担当の起動は `--max-fix-rounds` 回を超えない |
| 運用・保守性 | 群を取り消した理由（担当と結末の理由）が改修計画の「見送った項目」の表から読める。3 つの取り込みの結果なしの記録が同じ形（`failed_attempts[]`）で、実行の要約が同じ読み方で数えられる |

## 影響

| 対象 | 影響 |
| --- | --- |
| `gitfacts.read_result` | 引数と戻り値が変わる（結果なしを値で返す）。呼び出し元 3 か所とテスト（`test_git_facts.py` の 3 件）を書き直す |
| `gitfacts.revert_unverified_range` | 無くなる。呼び出し元 2 か所（`converge.py:383` / `gate.py:203`）は `intake.discard_unverified` へ |
| `merge-apply` の終了コード | 着手前テストの未確認と範囲の未確定が 2 から 4（中断）へ変わる。`SKILL.md` の終了コードの表は既に 4 と書いている |
| `merge-fix` / `merge-final-fix` の終了コード | 結果なしで 2（新）。骨組みは終了コードを見ないため変わらない |
| 状態ファイル | 群に `attempt` / `failed_attempts` / `drop_reason`、`final_gate` に `failed_attempts` が増える。既存の状態ファイルは鍵が無いまま読める（試行 0 回・失敗なし） |
| 群の担当 | 結果を残さなかった群だけ、2 回目の試行で次の輪番の担当へ替わる |
| 無進捗の打ち切り | 適用・修正・最終ゲートの修正で、どの担当も既定の許容より長くなる（既定 1800 秒） |
| トレーラーの読み取り | 最後の段落に加え、その直前に続くトレーラーの段落も読む |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q`（cross-refactoring だけなら `plugins/ndf/skills/cross-refactoring/tests`） |
| 配布物の同期 | `bash scripts/build-runtime-plugins.sh --check` |
| 定義の検査 | `claude plugin validate .` と `python3 scripts/check-skill-frontmatter.py` |
| 手動確認 | 次に cross-refactoring を回した実行で、進捗の記録（`<impl>-apply-r*-progress.log`）に作業段階が残るか。担当が結果を残さなかった群の結末の記録の理由が、監視の結果ファイルの理由と一致するか |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 状態の判定は `refactor_lib/` に置き、骨組みの bash は判定を持たない（`SKILL.md` の「実行」）。`commands/` どうしの取り込みを作らず、複数のコマンドが読む処理は `refactor_lib/` の直下（`rounds.py` / `intake.py`）に置く |
| コーディング規約 | 外部コマンド（`git interpret-trailers` / `git revert`）の挙動は書く前に実行して確かめる（`AGENTS.md` の DO） |
| テスト戦略 | 状態の遷移は既存の形（`tests/conftest.py` の `no_git` / `patch_lib` と `test_merge_apply.py` の `git_facts`）で関数を直接呼ぶ。トレーラーの読み取りは一時リポジトリで実際に git を実行する。共通層 `read_launch_outcome` はテストで差し替えず、一時ディレクトリに監視の結果ファイルと結果ファイルを置いて本物を通す |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、配布物の同期の検査 |
| 確認してから行う | 同じ群の試行の上限を引数にすること（この変更では固定の 2 回） |
| 行わない | `monitor.py` / `monitor_outcome.py` / `assignment.py` の変更、骨組みを `scripts/` へ出すこと、進行側によるコミットの書き換え（`--amend`） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| G1（#727）の `impl_assign` / `participants` と、この変更の `rounds.impl_for_seq` のどちらが先に `develop` へ入るか | 進行側（実装の持ち場の着手時点） | 実装の計画 |

## 既存の受け入れ条件との対応

| 既存（PR #665） | この文書 | 変わったこと |
| --- | --- | --- |
| AC1〜AC3、AC5、AC7 | AC9〜AC11、AC13、AC15 | 記録の名前を `failed_attempts[]`（`phase` 付き）に揃えた |
| AC4 | AC12 | 理由 `unparsable` を明記 |
| AC6 | AC7 | 3 つの取り込みに広げた |
| AC8 | AC4、AC6 | 3 つの取り込みに広げた。コミットが無いときは push しない |
| AC9、AC10 | AC16、AC8 | 同じ |
| AC11 | AC17 | 同じ |
| AC12 | AC2、AC5 | `_monitor_reason` を `read_launch_outcome` の値に置き換えた |
| AC13〜AC16 | AC19〜AC22 | `drop_reason: empty` を明記 |
| AC17〜AC19 | AC23〜AC25 | 記録を `failed_attempts[]` に揃えた |
| AC20〜AC27 | AC32〜AC39 | 同じ |
| AC28〜AC30 | AC40〜AC42 | 同じ |
| AC31〜AC33 | AC43〜AC45 | AC44 に Step 7 と結果なしの記述を足した |
| AC34〜AC36 | AC46〜AC48 | 同じ |
| AC37 | AC49 | 同じ |
| — | AC1、AC3、AC14、AC18、AC26〜AC31、AC50 | 新設（結末の読み取り、起動し直しの可否、最終ゲート、開き直しの判定の一本化、取り消しの本体の一本化） |

## 関連文書と前後関係

| 項目 | 内容 |
| --- | --- |
| 設計 | [issue-728-647-592-553-design.md](issue-728-647-592-553-design.md)。この文書は「何を満たすか」だけを扱う |
| 置き換える既存の要求 | [issue-647-592-553-requirements.md](issue-647-592-553-requirements.md)（PR #665）。**この文書が置き換える。** 対応は「既存の受け入れ条件との対応」にある |
| 親の名前で置く理由 | 親 #728 が根本原因の場所（結果の読み取りの向きと、3 つの取り込みの重複）を定め直したため、受け入れ条件を親の名前で改めて置く |
| 範囲に入れる子 issue | #674（最終ゲートの修正）は #728 の子として範囲に入れる。閉じるのは棚卸に任せる |
