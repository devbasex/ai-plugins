# #1593: CI の無いリポジトリで、スプリントの検査のプランが cross-review の unverified（exit 2）と on_fail: abort で毎回止まる

正は課題の本文（#1593）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

CI（GitHub Actions など）を持たないリポジトリでは、スプリントの検査のプランの `review`（cross-review の収束）が、確かめる CI が無いために exit 2（unverified）で返る。検査のプランの `review` は `on_fail: abort` のため、指摘が収束していてもプランがそこで止まる。続けるには `supervise.py run --from test-all` で手で再開するしか無かった。

## どこで見つけたか

takemi-ohama/novel のスプリント pipeline（PR takemi-ohama/novel#35）の検査のフェーズ。このリポジトリは CI を持たず、外部のレビューの検査（Amazon Q Developer）だけがある。

## exit 2 の経路（記録から分かること）

- 検査のプランの `review` は `plugins/ndf/scripts/supervise_lib/templates.py:260-269` の `drive: cross-review` で、`on_fail: abort` である
- PR takemi-ohama/novel#35 の cross-review の状態ファイル（`/work/novel/.git/ndf/worktree-trash/pr35-20261001213433/.cross_review/cross-review-pr35-state.json`）: 3 ラウンドで `final: approved`（2026-10-01 23:36 JST）。最後のラウンドの CI の判定は `unverified`（「チェックジョブを照会できない（未 push・権限・チェックジョブ 0 件のいずれか）」）。`sweep` は `null` で、最終スイープの結果が記録されていない
- CI の照会の経路は exit 2 にならない。`plugins/ndf/skills/cross-review/scripts/review_lib/commands/judge.py:216-217` は CI が `unverified` のとき「⚠ 継続的統合を確かめられないまま収束する」を出し、exit 0 で返す
- 駆動の結果の `review_status` は `plugins/ndf/scripts/lib/loop_drive.py:164-176` が決める。`final: approved` でも最終スイープの `verified` が真でなければ `unverified` を返す。PR #35 は `sweep: null` のため、この経路で `unverified` になる
- exit 2 を返したスクリプトは特定できていない。novel の supervise の記録（状態ディレクトリと進行のログ）が手元に無い。cross-review の駆動（`drive.py`）自身の終了コードは 0（終わった）・1（止まった）・20〜23（LLM を待つ止まり）で、2 を返さない（`plugins/ndf/scripts/lib/drive_pause.py:38-47`）

## 再現の手順と確かめる点

exit 2 の出所は novel の記録が無いため、CI の無い一時リポジトリで再現して特定する。

1. GitHub に CI（`.github/workflows/`）を持たない一時リポジトリを作り、Pull Request を 1 本開く
2. `supervise.py new sprint` で検査のプランを書き出し、`supervise.py queue` で流す
3. 次を確かめる
   - `review` ステップの駆動（`drive.py`）の結果 JSON の `status` と、`lib/loop_drive.py` の `review_status` の値（`unverified` になるか）の関係
   - `supervise.py run` と `review` ステップの終了コード（どこで 2 になるか）
   - 状態ファイルの `sweep` が `null` になる条件（最終スイープが走らないのか、走って記録されないのか）

## なぜこの変更の範囲外なのか

検査のプランの雛形と cross-review の終了コードの扱いの問題で、開発対象リポジトリでは直せない（MVV なし: 上流）。

## 直さないと何が起きるか

CI の無いリポジトリでは、スプリントの検査が毎回 `review` で止まり、conductor がプランの外で再開を手当てする（#1426 の「プランの外の手当てをなくす」に反する）。

## 関連

- #1426 — conductor がプランの外で打つ手当てをなくす。この課題の止まり方もその手当ての 1 つ
- #1340（閉じた） — cross-review を収束・異常終了の後にもう一度回す手段（`lib/loop_drive.py`）

## 由来

takemi-ohama/novel の PR #35 の振り返り。提案: プランを作るときにリポジトリの CI の有無を見て、CI が無ければ `review` の unverified を「止めない」（または `test-all` を確かめの代わりにする）扱いへ切り替える。

## 依頼（原文）

> CI（GitHub Actions など）を持たないリポジトリでは、スプリントの検査のプランの `review`（cross-review の収束）が、確かめる CI が無いために exit 2（unverified）で返る。検査のプランの `review` は `on_fail: abort` のため、指摘が収束していてもプランがそこで止まる。続けるには `supervise.py run --from test-all` で手で再開するしか無かった。

> 提案: プランを作るときにリポジトリの CI の有無を見て、CI が無ければ `review` の unverified を「止めない」（または `test-all` を確かめの代わりにする）扱いへ切り替える。

（書き手: 起票者（takemi-ohama/novel の PR #35 の振り返りを記録した AI）、日付: 2026-10-01。上の節「何を見つけたか」と「由来」の文。利用者の指示の原文は無い）

> #1593。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-10-24。進め方の指示で、要求の中身を持たない）

## 要求を書くときに読んだコード（2026-10-24、`design/issue-1593` = `4602364f`）

題の「unverified（exit 2）」は、今のコードの経路とは合わない。要求はこの読みを前提に書く。

| # | 読んだこと | 場所 |
| --- | --- | --- |
| R1 | supervise の drive のステップは、駆動の結果 JSON の `status` が `ok` のときだけ成功とする。`metrics.review_status` は成否に使わない | `plugins/ndf/scripts/supervise_lib/worker_steps.py:184-186`・`plugins/ndf/scripts/supervise_lib/plan.py:52-56` |
| R2 | cross-review の駆動の終了コードは 0・1・20〜23 だけである。止まったときは、止めたスクリプトの元の終了コードを `metrics.exit` へ写す。「exit 2」はこの `metrics.exit` の値だった見込みが高い | `plugins/ndf/scripts/lib/drive_pause.py:76-84` |
| R3 | 継続的統合の照会が `unverified` でも、判定は収束として exit 0 で返す | `plugins/ndf/skills/cross-review/scripts/review_lib/commands/judge.py:216-219` |
| R4 | 状態の `sweep` は、最終スイープの結果を `verify-sweep` が確かめた時点で書かれる。`final: approved` で `sweep: null` は、収束の後、`verify-sweep` より前で駆動が止まったことを示す | `plugins/ndf/skills/cross-review/scripts/review_lib/commands/report.py:120`・`plugins/ndf/skills/cross-review/scripts/drive.py:362-367` |
| R5 | 収束の直後に最終スイープの指示を組む `sweep_verify_lines` は、`.ndf/project.json` の `test` を解けないと終了コード 2 で止める。R2・R4 の 3 つ（`final: approved`・`sweep: null`・`metrics.exit` = 2）にすべて合う候補である。ほかに 2 で止まる所: `state.py init` が `TMP_DIR` を返さない（`drive.py:264-267`）、子のスクリプトが 2 を返した（`drive.py:111-115` の `must`） | `plugins/ndf/skills/cross-review/scripts/drive.py:58-83` |
| R6 | `review_status` の `unverified` は最終スイープが確かめられていないことを指し、継続的統合の照会の `unverified`（R3）とは別の意味である | `plugins/ndf/scripts/lib/loop_drive.py:164-176` |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 止まった原因は、継続的統合が無く確かめられないこと（unverified）である | 題・「何を見つけたか」 | 前提にしない。R1〜R5 の読みでは継続的統合の照会は止める経路に無い。原因は受け入れ条件 1 で特定する |
| S2 | CI の無いリポジトリでは毎回止まる | 題 | 受け入れ条件 1 の再現で確かめる |
| S3 | プランを作るときにリポジトリの CI の有無を見て扱いを切り替える | 「由来」の提案 | 採らない（手段として）。どの手段で満たすかは設計が決める。受け入れ条件は手段を問わない |
| S4 | CI が無ければ `review` の unverified を止めない | 「由来」の提案 | 受け入れ条件 2・3（継続的統合を確かめられないことだけでは止めず、確かめられなかったことを残す） |
| S5 | CI の代わりに `test-all` を確かめにする | 「由来」の提案 | 受け入れ条件 2（`review` の後に今の並びで `test-all` が流れる）。`test-all` を継続的統合の代わりと記録に書くかは未決 1 |

## 目的

- 継続的統合を持たないリポジトリでも、cross-review が収束したスプリントの検査が、conductor の手当て（`supervise.py run --from`）なしに `review` の先へ進む（#1426）
- 駆動が本当に止まるべきとき（収束しない・宣言が壊れている など）は、止まった理由が継続的統合の未検証と区別できる形で報告に出る

## 前提

- 前提 1: 止める経路は cross-review の駆動が `stopped` を返したこと（R1）であり、継続的統合の照会の結果そのものではない。受け入れ条件 1 の再現でこの前提が崩れたら、要求を書き直す
- 前提 2: 検査のプランの雛形はスプリントの検査（`new sprint`）と単発の検査（`new check`）で共有している（`plugins/ndf/scripts/supervise_lib/templates.py` の `plan_check`）。直した振る舞いは両方に効く
- 前提 3: takemi-ohama/novel の supervise の記録は手元に無い（`/work/novel` が無いことを 2026-10-24 に確かめた）。原因の特定は一時リポジトリでの再現で行う
- 前提 4: 継続的統合の有無は、PR の head のチェックジョブが 0 件であることで観測する（R3 の `unverified` の理由の 1 つ）

## 対象範囲

含む:
- CI の無いリポジトリでの、スプリントの検査と単発の検査の `review` のステップの止まり方
- 止めたスクリプトと終了コード 2 の出所の特定（再現）
- 止まったときの報告の理由の出し方（どのスクリプトが何で止めたか）
- 継続的統合を確かめられなかったことの記録

含まない:
- cross-review の収束の判定そのもの（ラウンド・承認の数え方）
- 継続的統合があるリポジトリでの、チェックジョブの失敗の扱い（今のとおり修正へ回す）
- `review_status` と継続的統合の照会の `unverified` の改名（R6。語の整理は別の課題にする）
- cross-refactoring の最終ゲートの判定（`review_status` を finalize へ渡す経路）
- takemi-ohama/novel 側の設定の修正

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 検査のプランを書き出した | `supervise.py new sprint` / `new check` | 宣言が無い・壊れていると止まる（今のまま） | — |
| E2 | `review` のステップが cross-review の駆動を起動した | プランの `refactor` の後 | 駆動の結果 JSON を読めない → ステップの失敗 | E1 |
| E3 | 判定が継続的統合を照会し、`unverified` を記録した | 収束したラウンドの判定 | 照会の失敗も `unverified` として収束する（R3） | E2 |
| E4 | 状態に `final: approved` を書いた | E3 | — | E3 |
| E5 | 最終スイープの指示を組んだ | 段階 `sweep-start` | 宣言の `test` を解けない → 終了コード 2 で止まる（R5）。受け入れ条件 1・4 | E4 |
| E6 | worker が最終スイープの結果ファイルを書いた | E5 の止まりを supervise が worker へ渡す | 書かない → ステップの失敗 | E5 |
| E7 | `verify-sweep` が状態の `sweep` を書いた | E6 | 0・6 以外 → 止まる | E6 |
| E8 | 駆動が `ok` を返した | E7 | `stopped` → `on_fail: abort` | E7 |
| E9 | 継続的統合を確かめられなかったことが報告に載った | E8 | 載らない → 受け入れ条件 3 を満たさない | E3・E8 |
| E10 | `base-sync`・`test-all` が流れた | `review` の `next` | 今のまま | E8 |

## 受け入れ条件

- [ ] 1. 前提: GitHub に `.github/workflows/` を持たず、宣言（`.ndf/`）が正しい一時リポジトリと、Pull Request が 1 本ある
      操作: `supervise.py new sprint`（または `new check`）で検査のプランを書き出し、`supervise.py queue` で流す
      結果: 次の値を課題のコメントか設計文書に書く。`review` の駆動の結果 JSON の `status`・`summary`・`metrics.exit`・`metrics.review_status`、`supervise.py run` の終了コード、状態ファイルの `final`・`sweep`・最後のラウンドの `ci.verdict`。止まったなら、止めた箇所をファイルと行で書く。止まらなかったなら、止まらなかった事実と試した条件を書く
- [ ] 2. 前提: 受け入れ条件 1 の一時リポジトリで、cross-review が `final: approved` で収束し、最終スイープの未解決が 0 件である
      操作: `supervise.py queue` で検査のプランを流す
      結果: `review` のステップが成功として `base-sync` へ進み、`test-all` まで流れる。`supervise.py run --from` を打たない
- [ ] 3. 受け入れ条件 2 の実行で、継続的統合を確かめられなかったこと（判定 `unverified` と理由）が、`review` のステップの件数（`progress.jsonl` の行か supervise の報告）に載る
- [ ] 4. 前提: 宣言の `test` を解けない（R5）など、駆動が止めるべき理由がある
      操作: 検査のプランを流す
      結果: `review` のステップが今のとおり止まり、supervise の報告の理由に、止めたスクリプトの理由文と元の終了コードが載る。理由文から継続的統合の未検証と区別できる
- [ ] 5. 退行しない: 継続的統合があり、チェックジョブがコードの失敗（`code_failure`）のときは、今のとおり判定が修正へ回す（`judge.py` の終了コード 2）
- [ ] 6. 退行しない: cross-review が収束しない（`final` が `approved` 以外）か駆動が `stopped` を返したときは、`review` の `on_fail: abort` のとおり止まる。ただし受け入れ条件 2 で止まらなくなった経路は除く
- [ ] 7. 既存のテスト（`.ndf/project.json` の `test` の suites）がすべて通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 止まった理由は、supervise の報告の 1 行から、止めたスクリプト・終了コード・理由文が読める |
| システム環境 | 継続的統合の手段（GitHub Actions の有無）に依らず同じ雛形が通る（Value 5） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 結果 JSON の `metrics` へキーを足す見込みがある（足すだけで、既存のキーの意味を変えない）。プランの雛形の JSON の形は変わる見込みがある（互換あり。既存のプランの読み方は変えない） |
| データ | 変わらない |
| 既存の振る舞い | CI の無いリポジトリで、`review` の止まり方と報告の理由 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `.ndf/project.json` の `test` の suites（`uv run --frozen --project . --all-extras pytest . -q -n 4` ほか） |
| 静的解析・型検査 | `python3 scripts/check-script-structure.py`・`ruff`（`.ndf/supervise.json` の `format`） |
| 手動確認 | 受け入れ条件 1・2・3: CI の無い一時リポジトリで検査のプランを `supervise.py queue` で流し、報告を見る。リリース後テストで確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `AGENTS.md`・`plugins/ndf/scripts/lib/README.md`。直す場所は止める原因の層（Value 6）。雛形で個別の値を埋め込まない（Value 5） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`scripts/check-script-structure.py` の行数の上限 |
| テスト戦略 | 単体: 駆動の止まりと supervise の drive のステップの成否・報告の理由。結合: 再現は手動確認（一時リポジトリを GitHub に作るため） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、整形の適用 |
| 確認してから行う | 結果 JSON の契約・プランの雛形の形の変更（承認ゲート 1・2 で人が見る） |
| 行わない | 継続的統合のあるリポジトリでの判定の緩和、takemi-ohama/novel への書き込み、cross-review の収束の判定の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 継続的統合が無いとき、`test-all` を継続的統合の代わりの確かめとして承認資料に書くか（S5） | 設計（承認ゲート 1 で利用者） | 設計 PR |
| 2. 受け入れ条件 1 で止めた原因が宣言の不正（R5）だったとき、宣言の不正を検査のプランの書き出し（E1）で先に見つけるか | 設計 | 設計 PR |
| 3. 一時リポジトリを GitHub のどこに作り、確かめた後に消すか（作る・消すは他のリポジトリへの公開とデータの削除に当たる。C4・C5） | 利用者（承認ゲート 1） | 再現の前 |

## MVV との突き合わせ（2026-10-24）

反する疑いは無い。進める。根拠: Value 4（conductor のプランの外の手当てを雛形の側でなくす）・Value 5（継続的統合の手段に依らず同じ雛形が通る）・Value 6（止める原因の層で直す。受け入れ条件 1 で原因を特定してから直す）・Vision（未検証のまま残ることを承認へ届ける。受け入れ条件 3）。継続的統合のあるリポジトリの判定は緩めない（受け入れ条件 5）。一時リポジトリの作成と削除は C4・C5 に当たるため未決 3 で利用者へ残す。
