# #2600: 工程が書くファイルの置き場・持ち主・消す時点をスクリプトで固定する（.ndf/ と issues/）

正は課題の本文（#2600）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> 引継ぎ文書の整理方法が定義されていない。
> .ndf/handoff にだいぶファイルが溜まっていますが、後片付けされた形跡がありません。
> 後片付けの方法を定義してください。引継ぎ文書以外の .ndf/ や.issue/ についても同様です。

（書き手: 利用者、日付: 2026-11-06。並列の計画 p3 のオーガナイザーの会話で受けた。「.issue/」は `issues/` を指すと解釈した）

> あと、そもそも /ndf:development-workflow の工程の中で何をどこに置くのかが定義されていないファイルが多そうです

> すべてについて言えますが、手順書ではなく、スクリプトで固定する方が良いです。

（書き手: 利用者、日付: 2026-11-06。同じ会話の続き）

> 資源を使いつぶさない設計を優先的に入れる
>   * 特にファイルやvolumeの後片付けは優先して企画設計し、実装する

（書き手: 利用者、日付: 2026-11-07）

> 終ったloopの情報などは統計量以外は不要

（書き手: 利用者、日付: 2026-11-06。手で掃除したときの発言を起票者が本文へ写した）

2026-11-06 に利用者の指示で手で掃除した内容: 終わった 9 ループと `.ndf/sprints`・`.ndf/tmp` を `loop.json`・`laps.md`・`report.md` だけ残して削除（446 MB → 13 MB）、済んだ承認資料 42 本、worktree の消えたテストのコンテナ 14 計画とボリューム 86 個。これを自動の決まりにする。無視の設定は Pull Request 2682「工程の実行の記録をコミットの対象から外し、残す文書を issues/old へ移す」（未マージ）。

（書き手: 起票者、日付: 2026-11-07）

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 工程が書くファイルのすべてが契約に置き場・持ち主・終わりを持ち、契約に無いパスへ書くとテストが落ちる | 起票時の受け入れ条件 1。「スクリプトで固定する」から推した | 受け入れ条件 1〜3 |
| S2 | 消すのはスクリプトで、手順書に「AI が消す」手順を残さない | 起票時の受け入れ条件 2 | 読み替えて受け入れ条件 27。決定が課題か PR にあるかの判断は語の意味の判断なので AI に残し、対象の列挙・消す操作・打つ時点をスクリプトが持つ（Value 5） |
| S3 | ループが `stopped`・`finished`・名前を替えた始め直しのどれかになると文書と記録が消える | 起票時の受け入れ条件 3 | 読み替えて受け入れ条件 11〜14。ループの記録に `finished` の状態は無く、`stopped` の多くは再開で解ける（下の「調べて分かったこと」） |
| S4 | 並列の計画が `finished` で文書と記録が消える | 起票時の受け入れ条件 4 | 受け入れ条件 15・16 |
| S5 | 棚卸しの工程が、持ち主の終わった記録の一覧を出す | 起票時の受け入れ条件 5 | 受け入れ条件 21〜23 |
| S6 | `.ndf/tmp/` へ新しく書くスクリプト・手順が無い | 起票時の受け入れ条件 6 | 受け入れ条件 6・28 |
| S7 | リリースの承認資料を `settle` の後に消す | 起票時の受け入れ条件 7。#2009（リリースの後片付けが承認資料を消さない）の受け入れ条件 | 受け入れ条件 18・19 |
| S8 | 工程が書くファイルの置き場そのものも定義する | 起票時の「定義の案」の範囲。利用者の 2 つ目の文から推した | 対象範囲に含む。手で書かれたファイルは、名の形を定め、形に合わないものを一覧に「持ち主不明」で出すところまでとする |
| S9 | 取りこぼしは棚卸しで拾う。拾うものが課題か PR に無いときは消さない | 起票時の原則 2 | 受け入れ条件 14・21 |
| S10 | `.ndf/backlog/*.lock` を、ロックを外すときに消す | 起票時の定義の案 | 採らない。0 バイトで資源を使わず、消すと同じパスの排他が別の inode に分かれる。持ち主の扱いは #2342（実行の持ち主と後片付け）に揃える。契約には「残る」と書く |
| S11 | 追跡の `issues/*.md` を `issues/old/` へ移す操作をマイルストーンを閉じる手順へ入れる | 起票時の定義の案 | 縮めて受け入れ条件 23。移す操作は追跡ファイルの変更で Pull Request が要るため、一覧に並べるところまでとする |
| S12 | 周回を締めた時点で、その周回の残す統計以外を消す | 起票者（要求の工程）。実測で、動いているループ 1 本の 1 プランに 162 MB が溜まった | 受け入れ条件 9・10 |
| S13 | 残す統計は `loop.json`・`laps.md`・各プランの `report.md`、並列の計画は `plan.json`・`plan.md` とする | 起票者（要求の工程）。手で掃除したときに残したもの | 前提 1 |
| S14 | ボリュームの回収は #2660（worktree を消した後もテスト環境の割り当てとコンテナが残る）へ寄せ、この課題は一覧に並べるところまでとする | 起票者（要求の工程）。原因が同じで、#2660 は同じマイルストーンにある | 受け入れ条件 24・未決 1 |
| S15 | 進み方が止まらないように、片付けの失敗ではループを止めない | 起票者（要求の工程）。Value 3 | 受け入れ条件 26 |

## 目的

- 工程が書くファイルの置き場・持ち主・消す時点をスクリプトと契約で固定し、AI の記憶と手順書に頼らずに片付くようにする
- 終わった作業の記録は統計だけを残し、ディスクと `git status` と再開の文書探しを圧迫しない状態を保つ

## 前提

- 前提 1: 統計量として残すものは、開発ループでは `loop.json`・`laps.md`・各プランの `report.md`、並列の計画では `plan.json`・`plan.md` である。手で掃除したときに残したものに揃えた。20 周回のループ（`lroot3`）で残す統計は 900 KB（2026-11-07 実測）
- 前提 2: 締めた周回の `lap-<n>/` の中身は、残す統計を除いて再開でも次の周回でも読まれない。`lap-` を作る箇所は `sprint_loop_select.py:435` の 1 か所で、ほかのループのディレクトリを読むのは `loop.json` だけ（`parallel_repo.py:68`・`parallel_lane.py:113`）。周回の中の読み手までは未確認で、設計の工程で確かめる
- 前提 3: ループの引継ぎ文書は現在地と再開のコマンドだけを持つ（`loop.md` の「記録」）。ループの片付けの前に確かめるのは文書の本文ではなく、`loop.json` に進行中の周回・保留の周回・判断待ちが無いことである
- 前提 4: 新しいプランの置き場は `.ndf/sprints/<名>/`（開発ループの中は今の `.ndf/loop/<名>/lap-<n>/sprint/`）とする。名前と形は設計で確定する
- 前提 5: Pull Request 2682 が先にマージされ、`.ndf/` の作業の記録と承認資料は `git status` に出なくなる。マージされない場合も受け入れ条件は変わらない
- 前提 6: `handoff.py` の説明文の「`.gitignore` にも書かない」は Pull Request 2682 と食い違うため、この課題で実態へ合わせる

## 対象範囲

含む:
- メインディレクトリの作業の記録: `.ndf/handoff/`・`.ndf/loop/`・`.ndf/sprints/`・`.ndf/parallel/`・`.ndf/tmp/`・`.ndf/backlog/` の記録・`issues/approval-*.md`・`issues/execution-plan-*.md`・リポジトリ直下に残るロックのファイル
- 契約への持ち主と終わりの欄、契約に無いパスへ書くことの検出
- `--out` などで AI がプランの置き場を選ぶ形の解消
- 棚卸しの工程で出す、終わった記録の一覧（追跡の `issues/*.md`、worktree の消えたテスト環境の一覧を含む）
- Factory のランナーの保存の対象を、新しいプランの置き場へ揃えること
- #2009（リリースの承認資料）の受け入れ条件。#2009 はこの課題の実装で解消を確かめて閉じる

含まない:
- `$TMPDIR` の下の残る状態（#2243）とリポジトリの外の置き場の寿命（#2606）
- 宣言ファイル（`.ndf/*.json`）の作成・更新・削除（#1512）
- 実行の持ち主の記録と `*.lock` の持ち方（#2342）
- テスト環境のコンテナとボリュームを消す手段（#2660。未決 1）
- 追跡の `issues/*.md` を `issues/old/` へ移す自動化。一覧に並べるところまでとする
- 手で書く文書（伝言・レーン用の文書）の書き手をスクリプトにすること。名の形と持ち主を決め、形に合わないものを一覧へ出す
- リポジトリの外のプラン（一時の置き場に作るプラン）の掃除

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | プランの置き場を決めた | `supervise.py new sprint`・`new close` | 決まった置き場の外を指す `--out` は断り、何も作らない | — |
| E2 | 周回を締めた | `loop lap`・`loop gate` など周回の締め | 片付けの失敗は結果に並べ、ループは続ける | E1 |
| E3 | 周回の残す統計以外を消した | E2 のうち保留でない周回 | 消せないパスは結果に並べ、次の片付けでやり直す | E2 |
| E4 | ループが終わった | `mvv_changed` で止まった、または `loop close` | `loop close` は `running` を断る | E2 |
| E5 | ループの記録を片付けた | E4 の後の `loop start`・`loop close`・棚卸し | 進行中・保留の周回か判断待ちがあれば消さずに理由を報告する | E4 |
| E6 | 並列の計画が終わった | `[ndf-parallel] finished` | 動いている計画には触らない | — |
| E7 | 並列の計画の記録を片付けた | E6 | E3 と同じ | E6 |
| E8 | 承認資料を消した | リリースのプランの最後のステップ | 承認待ちで止まっている間は打たれない | 承認の後 |
| E9 | 実行計画を PR へ投稿して消した | 実行計画を閉じるコマンド | 投稿できなければ消さない | すべての行がマージされた後 |
| E10 | 終わった記録の一覧を出した | 棚卸しのプランのステップ | 読めない項目は「未確認」として並べ、棚卸しを止めない | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| 作業の記録 | 工程の実行が書く、追跡しないファイルとディレクトリ。プラン・周回の記録・引継ぎ文書・承認資料を指す |
| 記録の持ち主 | 作業の記録を作り、それを片付ける責任を持つ実行の単位（ループ・並列の計画・スプリント・リリースのプラン・conductor） |
| 記録の終わり | 作業の記録が要らなくなる時点。持ち主の状態で判定できる形で書く |
| 残す統計 | 作業の記録を片付けた後も残すファイル。費用・秒・周回の結果を後から集計するために使う |
| 持ち主不明 | 名の形が契約のどのパターンにも当たらない作業の記録 |

## 受け入れ条件

契約:

- [ ] 受け入れ条件 1: `plugins/ndf/contracts/files/` の項目から持ち主か終わりの欄を外すと、`python3 scripts/check-ndf-contracts.py` が 0 以外で終わる
- [ ] 受け入れ条件 2: 契約の一覧（`plugins/ndf/contracts/index.md`）に、引継ぎ文書・承認資料・実行計画・スプリントのプラン・周回のディレクトリ・棚卸しの記録の項目があり、どの行にも持ち主と終わりが載る
- [ ] 受け入れ条件 3: スクリプトがメインディレクトリの `.ndf/` か `issues/` の、契約のどのパターンにも当たらないパスへ書く 1 行を足すと、テストか構造チェックが落ちる

パスの固定:

- [ ] 受け入れ条件 4: worktree の中で `supervise.py new sprint --name x` を `--out` なしで打つと、プランはメインディレクトリの決まった置き場にでき、カレントには何もできない。`new close` も同じ
- [ ] 受け入れ条件 5: `--out` にリポジトリの中の決まった置き場の外（例: `.ndf/tmp/sprint-x`）を渡すと、何も作らず 0 以外で終わる。一時の置き場を指す `--out` は今のまま通る
- [ ] 受け入れ条件 6: `rg -n '\.ndf/tmp' plugins/ndf/scripts plugins/ndf/skills -g '!**/tests/**'` が 0 件で、`test-run.py` のログは契約に載る置き場へ書かれる
- [ ] 受け入れ条件 7: `sprint-state.py gate p2436 …` のようにスプリント状態ファイルを相対の名前で打っても、リポジトリ直下に `*.lock` ができない
- [ ] 受け入れ条件 8: `handoff.py path parallel-p3` と `handoff.py path notice-to-lroot2` が 0 で返る
      前提の確認: 今はどちらも 2 で返る（「名が loop-<名>・sprint-<名>・milestone-<番号>・issue-<番号> の形でない」。2026-11-07 実行）

周回とループの片付け:

- [ ] 受け入れ条件 9: 保留でない周回を締めると、その `lap-<n>/` に残す統計だけが残る
- [ ] 受け入れ条件 10: 保留の周回（リリース待ち・質問待ち）の `lap-<n>/` は、締め直すまで中身が減らない
- [ ] 受け入れ条件 11: `mvv_changed` で止まり、進行中の周回・保留の周回・判断待ちが無いループは、次に `loop start` を打つと（別の名前でも）残す統計だけが残り、`loop-<名>.md`・`loop-<名>-lane.md`・その履歴が消える
- [ ] 受け入れ条件 12: 再開で解ける止まり方（`user_stop`・`same_failure`・`budget_*`・`no_issue`・`outside_mvv`・`gate2_returned`）のループは、`loop start` でも棚卸しでも片付けられない
- [ ] 受け入れ条件 13: `loop close <名>` を打つと、再開で解ける止まり方のループが受け入れ条件 11 と同じ形に片付く。`running` のループには 0 以外で断る
- [ ] 受け入れ条件 14: 受け入れ条件 11・13 で、進行中の周回・保留の周回・判断待ちのどれかがあるループは 1 つも消えず、結果の `items` にループ名と理由が並ぶ

並列の計画とスプリントの片付け:

- [ ] 受け入れ条件 15: 並列の計画が `finished` になると、`.ndf/parallel/<計画>/` に残す統計だけが残り、`.ndf/handoff/parallel-<計画>.md` が消える
- [ ] 受け入れ条件 16: 動いている並列の計画と、承認を待つ提示がある並列の計画のファイルは 1 つも消えない
- [ ] 受け入れ条件 17: ループの外のスプリントで棚卸しのプラン（`new close`）が最後まで通ると、そのスプリントのプランの置き場に残す統計だけが残る

承認資料と実行計画:

- [ ] 受け入れ条件 18: リリースのプランを最後まで流すと、そのプランが書いた承認資料（`issues/approval-<plugin>-v<版>.md`・`-parallel-<連番>-<提示>.md`）がメインディレクトリに残らない
- [ ] 受け入れ条件 19: 承認を待って止まっている間は、承認資料が消えない
- [ ] 受け入れ条件 20: 実行計画を閉じるコマンドを打つと、全文が最後にマージした Pull Request のコメントになり、ファイルが消える。投稿が失敗するとファイルは残り 0 以外で終わる

取りこぼしの一覧:

- [ ] 受け入れ条件 21: 棚卸しのプランのステップが、記録の終わりを過ぎた作業の記録の一覧を、パス・持ち主・大きさ・理由つきで結果へ出す
- [ ] 受け入れ条件 22: 2026-11-07 時点の `.ndf/handoff/` と同じ名前のファイルを置いたテストで、`mvv_changed` のループの文書は「終わった」、`notice-to-*`・`lroot-exclude.json` は持ち主つきか「持ち主不明」で並ぶ
- [ ] 受け入れ条件 23: 閉じた課題の追跡文書（例: #2047 の `issues/issue-2047-design.md`）が一覧に並ぶ
- [ ] 受け入れ条件 24: worktree の消えたテスト環境のコンテナとボリュームが一覧に並ぶ。コンテナの実行環境に接続できないときは「未確認」と出して一覧は最後まで出る

安全:

- [ ] 受け入れ条件 25: 片付けは追跡ファイル（`.ndf/*.json`・`.ndf/mvv.md` を含む）を 1 つも消さない
- [ ] 受け入れ条件 26: 片付けの途中でファイルが消せないとき、ループと並列の計画は止まらず、消せなかったパスが結果に並び、次の片付けで同じパスが対象になる
- [ ] 受け入れ条件 27: `handoff.md` の「消す」とループ・並列の手順書に、ファイルを消す操作そのもの（`rm` など）の指示が無く、スクリプトの呼び出しと、呼ぶ前の判断だけが書かれている
- [ ] 受け入れ条件 28: Factory のランナーの保存の対象に新しいプランの置き場が入り、片付けで引継ぎ文書が消えた後でも状態の保存が失敗しない
      前提の確認: `factory/runner/src/state.ts:58` は `test -e` で在るパスだけを保存へ渡す（2026-11-07 読んだ）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 20 周回のループの片付けの後に残る大きさが 2 MB 以下（`lroot3` の実測 1.3 MB、残す統計 900 KB）。片付けはファイル数に比例するだけで、課題の API を呼ばない |
| 運用・保守性 | 片付けの結果は `lib/step_result.py` の形の JSON で、消したパスの数・バイト数・残した理由を持つ。何度打っても同じ結果になる |
| 移行性 | 既存の残骸は `loop close` と一覧の順に片付けられる。旧い `.ndf/tmp/` の中身を自動で移さない。Factory の保存済みの状態に含まれる `.ndf/tmp` は復元できる |
| セキュリティ | 片付けの対象はメインディレクトリの契約に載るパスに限り、`..` と絶対パスを含む名前を断る。シンボリックリンクはリンク自体だけを消し、リンク先をたどらない |
| システム環境 | ai-plugins の形に固定しない。ベースブランチやデプロイの手段に依存せず、`.ndf/` を持つどのリポジトリでも同じに動く（Value 6） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。リポジトリの中の決まった置き場の外を指す `--out` は断る。`互換なし`。`sprint-state.py loop close` と一覧のコマンドが増える。`handoff.py` の受ける名が増える（互換） |
| データ | 契約の項目に欄が増える。`.ndf/` の作業の記録が片付けで減る（追跡外で戻せない） |
| 既存の振る舞い | 周回の締め・`loop start`・並列の計画の終わり・リリースの後片付け・棚卸しのプランに片付けが入る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`。Factory は `factory/runner` のテスト |
| 静的解析・型検査 | `python3 scripts/check-ndf-contracts.py`・`python3 scripts/check-service-boundary.py` |
| 手動確認 | リリース後に、メインディレクトリで `mvv_changed` のループが片付き、`.ndf/` の大きさが下がることを実測する。受け入れ条件 27 は手順書を読んで判定する |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 持ち主と終わりの一覧の形は `lib/run_state_kinds.py` の `KINDS` に揃える。正本は契約の項目 1 か所で、手順書へ写さない（Value 8） |
| コーディング規約 | `AGENTS.md`。`.md` の文言を照合するテストを書かない |
| テスト戦略 | 片付けの判定は単体テスト、周回の締めとループの開始は `sprint-state.py` を通すテスト。テストの入力は実物の `loop.json` の形から作る |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 契約の一覧の生成（`check-ndf-contracts.py --render`）、既存テストの実行 |
| 確認してから行う | `--out` を断る変更（公開インタフェース）、作業の記録の自動削除の規則（未決 2） |
| 行わない | 追跡ファイルの削除、宣言ファイルの変更、依頼範囲外のリファクタリング |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. ボリュームの回収を #2660 へ寄せるか。推奨は寄せる。#2660 の本文にボリュームの語が無い（`gh issue view 2660` の本文を照合して確かめた）ため、受け入れ条件へ足す必要がある | 利用者 | 設計承認ゲート |
| 2. 作業の記録の自動削除は追跡外のファイルで git では戻せない。共通原則の「必ず人の承認が要る操作」C4（戻せない操作）に当たるか。方向は利用者の指示（統計量以外は不要）に沿うが、規則そのものは人が見て承認する前提とし、MVV 判定に任せない | 利用者 | 設計承認ゲート |
| 3. 28 件を 1 つのスプリントで出すか、契約とパスの固定（1〜8）と片付けと一覧（9〜28）に分けるか（Value 2） | `implementation-plan` が案を出し、利用者が確かめる | 設計承認ゲート |

## 調べて分かったこと（2026-11-07、メインディレクトリの実測）

手で 13 MB まで減らした翌日に、`.ndf/` は 182 MB へ戻った。

| 置き場 | ファイル数 | 大きさ |
| --- | --- | --- |
| `.ndf/handoff/` | 29 | 296K |
| `.ndf/loop/` | 8,478（14 ループ） | 175M |
| `.ndf/sprints/` | 16 | 140K |
| `.ndf/parallel/` | 600 | 3.1M |
| `.ndf/tmp/` | 109 | 924K |
| `.ndf/backlog/` | 32 | 1.9M |
| `issues/approval-*.md` | 9（未追跡） | — |

- **1 プランが大半を占める。** `.ndf/loop/lflow3/lap-3/sprint/3-impl-2623-state/work/` が 162 MB で、`work/h/.cache/uv` などの作業の置き場である。動いているループの中の周回なので、ループの終わりを待つ片付けでは減らない。受け入れ条件 9 の理由である
- **ループの記録に終わりの状態が無い。** `rec["status"]` は `running` と `stopped` だけである（`sprint_loop.py:181`・`sprint_loop_record.py:380` を `grep` した）。14 ループの内訳は `running` 3、`mvv_changed` 9、`user_stop` 1、`gate2_returned` 1。再開で解けないのは `mvv_changed` だけで（`loop.md` の「止まる条件」）、ほかは `loop start` で続く。このため「`stopped` で消す」と再開できるループの記録を失う
- **`handoff.py remove` は `parallel-<計画>` と `notice-to-*` を受けない。** 受ける名は `NAME_RE`（`handoff.py:47`）が決める
- **`.ndf/tmp` は Factory が使っている。** `factory/runner/src/state.ts:6` の `STATE_PATHS` が `.ndf/tmp`・`.ndf/loop`・`.ndf/organizer` を保存の対象にする。確定仕様（`docs/specifications/ndf-factory-runs-and-runner.md:258,383`・`ndf-factory-dev-loops.md:318`）も同じ。プランの置き場を変えるとランナーの保存も揃える必要がある
- **`.ndf/organizer` は洗い出しの表に無い。** Factory のランナーが書く（`factory/runner/src/organizer.ts:20`）。ai-plugins のメインディレクトリには無い（`ls` で確かめた）。Factory の側の置き場として、契約に載せるかを設計で決める

## 関連

- #2606（リポジトリの外の置き場の寿命と、コンテナ・Factory の ECS での永続化）: 同じ契約の欄に寿命を足す。この課題と 2 つで 1 つの一覧になる
- #2009（リリースの承認資料）: 受け入れ条件 18・19 に取り込んだ
- #2660（worktree を消した後もテスト環境の割り当てとコンテナが残る）: 未決 1
- #2243（一時の置き場の持ち主と寿命）: 置き場が違うが、持ち主と寿命の一覧（`run_state_kinds.KINDS`）の形を揃える
- #2342（実行の持ち主と後片付け、柱 P3）: `*.lock` の扱いを揃える
- #1512（`.ndf/` の定義ファイルの作成・更新・削除）: 設定の側。作業の記録はこの課題で扱う
- Pull Request 2682（作業の記録を無視の対象にする）: 前提 5

## 起票時の記録（2026-11-06）

以下は起票時の実測と案である。起票時の案と受け入れ条件が違うときは、受け入れ条件を正とする。

## 何が困るか

メインディレクトリの作業の記録（追跡外）が、作った実行が終わった後も消えずに溜まっている。2026-11-06 の実測（develop 8ace2c767）:

| 置き場 | 数 | 大きさ | 最も古いもの | 消す決まり |
| --- | --- | --- | --- | --- |
| `.ndf/handoff/` | 23 ファイル | 172K | 2026-10-30（`milestone-35-history.md`） | **ある**（`development-workflow/references/handoff.md` の「消す」。`handoff.py remove`）が、**誰も打っていない** |
| `.ndf/loop/<ループ名>/` | 8 ループ・7,561 ファイル | 135M | `lroot`・`l20261031`（終わったループ） | 無い |
| `.ndf/sprints/sprint-*/` | 2 件・338 ファイル | 2.3M | `sprint-p2436` | 無い |
| `.ndf/parallel/<計画名>/` | 1 件・328 ファイル | 1.6M | `p3`（動いている） | 無い |
| `.ndf/tmp/` | 24,638 ファイル | **296M** | `sprint-m1426a` ほか（マイルストーン 26 の頃） | 無い。**作り手が決まっていない**（conductor が `supervise.py --out` の置き場としてその都度選んだ） |
| `.ndf/backlog/` | 4 ファイル | 16K | `*.lock` が残る | 無い（中身は続けて使う） |
| `issues/approval-*.md` | 36 本（未追跡） | — | `approval-ndf-v10.22.0.md` | 無い（#2009） |
| `issues/execution-plan-26.md` | 1 本（未追跡） | — | マイルストーン 26 | 無い |
| `issues/*.md`（追跡） | 60 本ほど | — | — | `issues/README.md` が「完了したものは `old/` へ移す」と書くが、いつ・誰が移すかは無い。確定仕様化（`plan-to-spec`）が消すのは渡した plan だけ |

困ること:

- `git status` と SessionStart の案内に、開発の変更と見分けにくい未追跡の行が 40 行近く並ぶ
- 新しい会話が `.ndf/handoff/` から再開の文書を探すとき、終わったループ・マイルストーン・伝言（`notice-to-*`）が混ざり、古い指示を読むおそれがある（`loop-lroot2-lane.md` は `loop-lroot2.md` を読むだけにすると書き分けている）
- ディスクを使う（`.ndf/` 全体で 430M を超える）

## 今ある決まりと欠けている点

| 決まり | どこ | 欠けている点 |
| --- | --- | --- |
| 引継ぎ文書を消す | `handoff.md` の「消す」（対象が閉じた後に conductor が、決定・未検証・起票が課題か PR にあるのを確かめて `handoff.py remove`） | 1. 終わり方が「スプリント・課題・マイルストーン」だけで、**ループ・並列の計画・伝言（`notice-to-*`・`notice-version-*`）・付属の JSON（`lroot-exclude.json`）の終わりが無い** 2. **打つ工程が無い**。棚卸しの工程の中にも、ループを閉じる手順にも入っていない 3. ループを名前を替えて始め直す（`mvv_changed` で止まったとき）と、旧い名前の文書は「閉じた」のか分からないまま残る |
| 承認資料を消す | #2009（未着手） | リリースの後片付けが消さない |
| 一時の置き場の持ち主と寿命 | #2229・#2243 | 対象は `$TMPDIR` の下で、メインディレクトリの `.ndf/` の実行の記録は入らない |
| 実行の持ち主と後片付け | #2342（柱 P3） | 対象は #2321・#2316・#2325 の 3 件に絞った（設計承認ゲートの答え）。#2009 も外した |
| `.ndf/` の定義ファイルの作成・更新・削除 | #1512 | 対象は設定（宣言）のファイルで、実行の記録ではない |

## 定義の案（起票者の推定）

**範囲: 後片付けだけでなく、工程が書くファイルの置き場そのものを定義する。** 置き場が決まっていないファイル（`.ndf/tmp/` の中身・`issues/` 直下の `mvv-*.md`・リポジトリ直下の `p2436.lock` など）は、消す時点も決められない。置き場の洗い出しの結果は末尾の節「置き場の洗い出し」にある（書き込まれるもの 51 件。固定のされ方は (a) スクリプトがパスを決める 22 件・(b) スクリプトが書くがパスは AI が選ぶ 14 件・(c) 手順書の指示で AI が書く 16 件で、(c) のうち 9 件は指示も無い。**契約の項目には持ち主と終わりの欄が無い**）。

**原則 1（利用者の方針）: 置き場・持ち主・消す時点は手順書ではなくスクリプトで固定する。** パスはスクリプトが決めて書き・消し、AI に `--out` などでパスを選ばせない。手順書が「ここへ書く」「終わったら消す」と AI に指示する形は、守られないことが今の溜まり方で分かっている（`handoff.md` の「消す」は書かれているが 1 度も実行されていない）。契約（`contracts/files/`）の項目に置き場と終わりを持たせ、契約に無いパスへ書くとテストが落ちる形にする。

**原則 2: メインディレクトリの作業の記録は、作った実行（持ち主）と、要らなくなる時点（終わり）を持つ。消すのは持ち主の最後の工程のスクリプトで、会話の記憶に頼らない。** 取りこぼしは、棚卸しの工程で一覧を出して拾う。

| 置き場 | 持ち主 | 終わり | 消す前に拾うもの | 消し方 |
| --- | --- | --- | --- | --- |
| `.ndf/handoff/loop-<名>.md`・`loop-<名>-lane.md`・付属の JSON | ループ | ループが `stopped`/`finished` で、続きのループへ移した後（新しい名前で始め直したなら、その `loop start` の時点） | 決定・除外の理由・未起票（今の `handoff.md` の手順 1〜2） | `loop` の終わりの手順で `handoff.py remove` |
| `.ndf/handoff/parallel-<計画>.md` | オーガナイザー | `[ndf-parallel] finished` の後 | 同上 | `parallel.py` の終わりで |
| `.ndf/handoff/milestone-<N>*.md`・`sprint-*`・`issue-*` | conductor | 今の規則のまま（閉じた後） | 同上 | 今の規則のまま。棚卸しの工程から打つ |
| `.ndf/handoff/notice-to-<受け手>.md`・`notice-version-*` | 受け手 | 受け手が読んで引継ぎ文書か課題へ反映した後 | 反映した先 | 受け手が消す |
| `.ndf/loop/<名>/` | ループ | ループの引継ぎ文書を消した後 | 周回の要約（`laps.md`）が課題か PR にあるか | まとめて消す（または `laps.md` と `loop.json` だけを残して消す。どちらにするかは設計で決める） |
| `.ndf/sprints/<名>/` | スプリント | 振り返り・棚卸しの後 | 同上 | `merged` か棚卸しのスクリプト |
| `.ndf/parallel/<計画>/` | オーガナイザー | `finished` の後 | 提示ごとの結果が PR とリリースにあるか | `parallel.py` の終わりで |
| `.ndf/tmp/` | **作り手を決めない置き場をやめる** | — | — | プランの状態の置き場は `.ndf/sprints/` か `.ndf/loop/` の下に限り、`--out` に任意の場所を渡さない。今あるものは一度だけ手で消す |
| `.ndf/backlog/*.lock` | 書いたプロセス | 書き終えた時点 | — | ロックを外すときに消す（#2342 の持ち主の記録と同じ扱い） |
| `issues/approval-*.md` | リリースのプラン | `settle` の後（#2009 の受け入れ条件） | 承認の記録が PR とリリースにあるか | リリースの後片付けのステップ |
| `issues/execution-plan-*.md`・`issues/*.md` | 実行計画・plan の持ち主 | マイルストーンを閉じた後 | 確定仕様にしたか | `issues/old/` へ移す（`issues/README.md` の今の決まり）を、マイルストーンを閉じる手順に入れる |

**取りこぼしを拾う一覧:** 棚卸し（`backlog-refinement`）の工程で、持ち主が終わっているのに残っている記録を一覧にして出す（例: `handoff.py stale`）。消すかは今の `handoff.md` の手順 1〜3 で決める。取り消せない削除（追跡外のファイル）なので、拾うものが課題か PR に無いときは消さずに報告する。

## 置き場の洗い出し（2026-11-06、worker の調査）

調べた日: 2026-11-05（JST）。対象: develop `8ace2c767` のメインディレクトリ `/work/ai-plugins`。
範囲: `plugins/ndf/scripts/`・`plugins/ndf/skills/*/scripts/`（`tests/`・`experimental/` を除く）と `plugins/ndf/skills/**/*.md`・`plugins/ndf/contracts/`。
契約の正本は `plugins/ndf/contracts/index.md`（`contracts/files/*.json`）。**契約の項目が持つのは `name`・`pattern`・`kind`・`place`・`skill_readers` だけで、持ち主と終わり（消す時点）の欄はどの項目にも無い**（`contracts/files/plan-json.json` などを見た）。

列の読み方:
- 区分: main = メインディレクトリ（追跡外）/ wt = worktree / tmp = `$TMPDIR`・`/tmp` / state = `~/.local/state/ndf/…` / 追跡 = コミットする
- 定義: 契約 = `contracts/files` に項目あり / 手順書 = Skill・references に置き場の記述あり / 無し
- 固定のされ方: **(a)** スクリプトがパスを決めて書く・消す（AI はパスを選ばない）/ **(b)** スクリプトが書くがパスは AI が引数で選ぶ / **(c)** 手順書の指示で AI が書く（記述が無いものは「(c) 指示なし」）

### 1. 書かれるものの表

### 1.1 `.ndf/handoff/`（引継ぎ文書）

| # | パスのパターン | 区分 | 書き手 | 定義 | 持ち主・終わり | 固定 |
| --- | --- | --- | --- | --- | --- | --- |
| H1 | `.ndf/handoff/<名>.md`（`milestone-<N>`・`sprint-<名>`・`issue-<N>`） | main | `scripts/handoff.py:45`（`HANDOFF_DIR`）・`handoff.py:93`（`path`/`init`）。中身は conductor | 手順書（`development-workflow/references/handoff.md:20-37`）。契約に無い | 持ち主 conductor、終わり「対象の最後の工程の後」（`handoff.md:114-121`）。打つ工程が無い（#2600） | パスは (a)、名は (c)（表から AI が選ぶ）、中身は (c) |
| H2 | `.ndf/handoff/<名>-history.md` | main | conductor（`handoff.md:23`） | 手順書だけ | 本体と同じ扱いか書かれていない | (c) |
| H3 | `.ndf/handoff/loop-<ループ名>.md` | main | `scripts/lib/sprint_loop_record.py:405-406`（`handoff_path`） | 手順書（`handoff.md:33`・`loop.md:179`）。契約に無い | 持ち主はループ。ループの終わりで消す定めは無い | (a) |
| H4 | `.ndf/handoff/parallel-<計画名>.md` | main | `scripts/lib/parallel_lane.py:33`・`parallel_plan.py:348`（`handoff.py` の `write_doc`） | 手順書（`development-workflow/references/parallel.md:80`・`issue-plan-strategy/references/parallel-plan.md:22-23`）。**`handoff.md` の名の表（:31-37）に `parallel-` の行が無い**。契約に無い | 終わり無し | (a) |
| H5 | `.ndf/handoff/loop-<名>-lane.md`（実在: `loop-lroot2-lane.md` ほか 3 本） | main | 見つからない | **無し**（`-lane\.md`・`lane.md` を `plugins/ndf`・`docs/specifications` で grep して 0 件） | 無し | (c) 指示なし |
| H6 | `.ndf/handoff/notice-to-<受け手>.md`・`notice-version-<名>.md`（実在 6 本） | main | 見つからない（`loop-lfactory2.md`・`loop-l20261031.md`・`milestone-35.md` の本文が名指しする） | **無し**（`notice-` を同範囲で grep して 0 件） | 無し | (c) 指示なし |
| H7 | `.ndf/handoff/lroot-exclude.json` | main | 見つからない。読み手の候補は `NDF_LOOP_EXCLUDE`（`scripts/lib/sprint_loop_select.py:360`。環境変数でファイルではない） | **無し**（`exclude\.json` を grep して 0 件） | 無し | (c) 指示なし |
| H8 | `.ndf/handoff/milestone-25-ndf-factory.md`（名の形 `milestone-<番号>` に接尾辞） | main | conductor | 名の形が `handoff.md:35` に合わない | 無し | (c) |

### 1.2 `.ndf/loop/<ループ名>/`（開発ループの記録）

| # | パスのパターン | 区分 | 書き手 | 定義 | 持ち主・終わり | 固定 |
| --- | --- | --- | --- | --- | --- | --- |
| L1 | `.ndf/loop/<名>/loop.json` | main | `scripts/lib/sprint_loop_record.py:175-178`（`LOOP_DIR` は `sprint_loop_names.py:13`） | 契約（`loop-json`, main）・手順書（`loop.md:177`） | 終わり無し | (a) |
| L2 | `.ndf/loop/<名>/laps.md` | main | `sprint-state.py`（同上） | 契約（`laps-md`, main）・手順書（`loop.md:178`） | 終わり無し | (a) |
| L3 | `.ndf/loop/<名>/loop.json.lock` | main | `sprint_loop_record.py:224` → `lib/locks.py:31`（`<target>.lock`。解放後も残る） | 無し（契約にも手順書にも無い） | 無し | (a) |
| L4 | `.ndf/loop/<名>/stop` | main | `sprint-state.py loop stop`（`loop.md:112`） | 手順書だけ（`loop.md:112,128,137`） | 再開で消す（`loop.md:112`） | (a) |
| L5 | `.ndf/loop/<名>/lap-<n>/sprint/…`（プラン・`*-state/`・`*.log`） | main | `sprint_loop_select.py:409-415`（`out=lap-<n>/sprint`）→ `sprint_loop.py:294`（`--out`） | 契約は中身の一部だけ（`plan-json` 等は parallel/sprint の書き手として載る）。lap の置き場そのものは手順書にも無い | 無し | (a) |
| L6 | `.ndf/loop/<名>/lap-<n>/state.json`(+`.lock`)・`close/`・`done-*.json`・`done-*.plans.json` | main | `sprint_loop.py:315`・`sprint_loop_gate.py:517`（`close-<k>`）・`supervise.py:57`（`<done>.plans.json`） | `state-json` は契約で place=**state-dir**（実物は main にある）。ほかは無し | 無し | (a) |
| L7 | `.ndf/loop/<名>/lap-<n>/queue-<k>.log`（実在: `l20261031/lap-2/queue-6.log` ほか） | main | スクリプトに無い（`queue-` を `sprint_loop*.py`・`supervise_lib` で grep して 0 件）。AI のリダイレクト | 無し | 無し | (c) 指示なし |

### 1.3 `.ndf/parallel/<計画名>/`

| # | パスのパターン | 区分 | 書き手 | 定義 | 持ち主・終わり | 固定 |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | `plan.json`・`plan.md`・`run.log`・`run.pid`・`answers-sent.json` | main | `scripts/lib/parallel_plan.py:266`（`plan_dir`）・`parallel.py:176-188` | 契約（`plan-json`・`plan-md`・`run-log`・`run-pid`・`answers-sent-json`, main）・手順書（`parallel-plan.md:22`） | 終わり無し | (a) |
| P2 | `releases/<連番>/<提示>/`（`present.json`・`presented.json`・`answer.json`・`requests.json` ほか） | main | `parallel.py:11`（`present`）・`parallel_run.py:559` | 契約（`present-json` 等, main） | 終わり無し | (a) |
| P3 | `notes/<名>.md`（実在 9 本: `g2479.md` など。レーンへの指示文） | main | スクリプトに無い（`notes` を `parallel*.py` で grep して書き手 0 件） | **無し** | 無し | (c) 指示なし |
| P4 | `<NDF_ORGANIZER_DIR>/…`（外のオーガナイザーのレーン） | 環境変数が指す場所 | `scripts/lib/parallel_external.py:9-45` | 手順書（`parallel.md:139`） | 無し | (b)（場所は起動側が環境変数で選ぶ） |

### 1.4 スプリントのプラン（`supervise.py new sprint/close` の `--out`）とプランの状態

| # | パスのパターン | 区分 | 書き手 | 定義 | 持ち主・終わり | 固定 |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | `<--out>/`（既定 `sprint-<名>`、**カレントからの相対**） | AI 次第。実在: `.ndf/sprints/sprint-p2436`・`sprint-p2439`、`.ndf/tmp/sprint-m1426a` ほか 10 件 | `scripts/supervise_lib/procedures.py:203-205`（`sprint_out`）・`new_args.py:136-143` | **置き場の定義無し**。手順書は `<作業ディレクトリ>/sprint-m6`（`development-workflow/SKILL.md:120`・`references/waiting.md:162-167`）で、`<作業ディレクトリ>` がどこかを書かない。`.ndf/sprints` は `plugins/ndf` のどこにも出てこない（grep `sprints` 0 件） | 無し | (b) |
| S2 | `<プラン>-state/`（プランの隣。プランが一時ディレクトリなら `~/.local/state/ndf/sv/<stem>-<hash>/` への symlink） | プランと同じ場所 / state | `scripts/supervise_lib/paths.py:185-210`（`sv_state_home` は `:233-236`） | 契約（`state-json`・`report-md` など, state-dir）。プランを main に置くと実体も main に入る | 無し | (b)（プランのパスで決まる） |
| S3 | `<--done>`・`<done>.plans.json` | AI 次第 | `scripts/supervise.py:57,146` | 無し | 無し | (b) |
| S4 | `<プラン>.log`（queue の報告） | プランの隣 | `scripts/supervise_lib/queue.py:311` | 無し | 無し | (b) |
| S5 | スプリント状態ファイル `sprint-state.json` | AI 次第（例は `~/.local/state/ndf/sv/r7/sprint-state.json`） | `scripts/sprint-state.py init <パス>` | 手順書の例だけ（`development-workflow/references/relay.md:226`） | 無し | (b) |
| S6 | `<sprint-state のパス>.lock`（実在: リポジトリ直下 `p2436.lock`、0 バイト、11-02 20:43） | 状態ファイルの隣 | `scripts/sprint-state.py:263`（`locks.exclusive(a.sprint)`）→ `lib/locks.py:31` | 無し | 無し | (b) |

### 1.5 `.ndf/backlog/`（棚卸しの記録）

| # | パスのパターン | 区分 | 書き手 | 定義 | 持ち主・終わり | 固定 |
| --- | --- | --- | --- | --- | --- | --- |
| B1 | `.ndf/backlog/depths.jsonl`(+`.lock`) | main | `skills/backlog-refinement/scripts/upkeep_depth.py:66-71`（`backlog_dir`） | 手順書だけ（`backlog-refinement/references/depths.md:19,57`）。契約に無い | 続けて使う（終わり無しが意図）。`.lock` は残る | (a) |
| B2 | `.ndf/backlog/owner.json`(+`.lock`) | main | `upkeep_owner.py:9,45` | 手順書だけ（`depths.md:49`） | TTL（`backlog.json` の `depths.owner.ttl`。`lib/run_owner.py:177`）。`.lock` の終わり無し | (a) |
| B3 | `.ndf/backlog/research/<側面>--<対象>.json`・`calls.jsonl` | main | `upkeep_research.py:10` | 契約（`research-item-json`・`research-calls-jsonl`, main）・手順書（`research.md:6`） | 無し | (a) |
| B4 | `.ndf/backlog/overhaul/<run>/before.json` | main | `upkeep.py`（`overhaul apply`） | 契約（`before-json`, main）・手順書（`overhaul.md:38`） | 無し | (a) |
| B5 | `overhaul snapshot/count/measure --out <dir>` | AI 次第 | `upkeep_overhaul.py:171-179` | 契約は place=tmp。手順書は `<作業ディレクトリ>`（`overhaul.md:23`） | 無し | (b) |

### 1.6 `.ndf/tmp/`

| # | パスのパターン | 区分 | 書き手 | 定義 | 持ち主・終わり | 固定 |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | `<root>/.ndf/tmp/test-run/<prefix>-<i>.log` | wt（root が main なら main） | `scripts/test-run.py:47,131-166`（`LOG_DIR`） | **無し**（契約・手順書に無い。スクリプトの定数だけ） | 無し | (a) |
| T2 | `.ndf/tmp/sprint-*/`（10 件、`sprint-m2171` だけで 249M） | main | S1 の `--out` | 無し | 無し | (b) |
| T3 | `.ndf/tmp/*.md`（`2047.md`・`2291.md`・`issue-heavy-tests.md` ほか 6 本。起票の下書き） | main | 見つからない | 無し | 無し | (c) 指示なし |
| T4 | `.ndf/tmp/lap4-*.sh`・`lap5-*.sh`・`lap5-close.json` | main | 見つからない（中身は `supervise.py queue …/.ndf/loop/l20261031/lap-5/…` の手書き） | 無し | 無し | (c) 指示なし |

補足: `.ndf/tmp/` はリポジトリの `.gitignore:34` の `tmp/` で無視され、`git status` に出ない（`--ignored` でだけ見える）。Factory の確定仕様は `.ndf/tmp` を状態の保存対象として名指しする（`docs/specifications/ndf-factory-runs-and-runner.md:258,383`・`ndf-factory-runs-state-and-data.md:156`）が、NDF 側に書き手の定義は無い。

### 1.7 `issues/`

| # | パスのパターン | 区分 | 書き手 | 定義 | 持ち主・終わり | 固定 |
| --- | --- | --- | --- | --- | --- | --- |
| I1 | `issues/approval-<plugin>-v<版>.md` | main（追跡外） | `scripts/supervise_lib/release_templates.py:363,482`・`sprint_routes.py:219`（`approval-facts` が書く） | 手順書（`development-workflow/references/glossary.md:118`・`pace.md:399`・`supervise_lib/plan.py:91`）。契約に無い | 終わり無し（#2009 が未着手） | (a) |
| I2 | `issues/approval-<plugin>-v<版>-parallel-<連番>-<提示>.md` | main | `scripts/lib/parallel_release.py:320` | 手順書に形の記述無し。契約に無い | 無し | (a) |
| I3 | `issues/approval-ndf-v10.27.21-lflow2.md`（ループ名の接尾辞） | main | 見つからない（`approval_path` を渡す側は `parallel_release.py:370` だけ） | 無し | 無し | (c) 指示なし（手で写した名残と推定） |
| I4 | `issues/execution-plan-<キー>.md` | main（追跡外） | AI | 手順書（`issue-plan-strategy/SKILL.md:41`・`references/execution-plan.md:18-26`）。契約に無い | **ある**: 閉じるとき PR へ投稿してから消す（`execution-plan.md:229-235`） | (c) |
| I5 | `issues/issue-<N>-requirements.md` | 追跡 | `spec-copy.py write <課題> <パス>`（パスは引数）。定数は `supervise_lib/procedures.py:16` | 手順書（`requirements-design/SKILL.md:194`・`design/SKILL.md:161`） | 確定仕様化で消す（`plan-to-spec-steps.py:130-160`） | (b)（雛形の内では (a)） |
| I6 | `issues/issue-<N>-design.md`・`issue-<N>-design-<主題>.md` | 追跡 | AI（設計）。定数は `supervise_lib/sprint_waves.py:132`・`plan.py:15` | 手順書（`design/references/design-template.md:180`）・`glossary.py:49` の既定 | plan-to-spec で消す | (c) |
| I7 | `issues/issue-<N>-<その他>.md`（`issue-159-telemetry.md`・`issue-2291-test-ledger.md`） | 追跡 | AI | 名の形の定義無し。`issues/README.md:8` が「1 issue = 1 ファイル」 | `issues/README.md:4`「完了したら old/ へ」だけ。いつ・誰が無し | (c) |
| I8 | `issues/mvv-<名>.md`（追跡: `mvv-ai-plugins.md`・`mvv-candidates-ai-plugins.md`・`mvv-milestone26.md`） | 追跡 | AI | `project-mvv.md:15` が ai-plugins の固有例として `mvv-ai-plugins.md` だけを名指し | 無し | (c) |
| I9 | `issues/mvv-v6-candidate-A.md`・`-B.md`・`mvv-v6-revision.md` | main（追跡外） | AI | **無し。しかもスクリプトの規則と食い違う**: `scripts/project-mvv.py:88-98` は作業ファイルをリポジトリの外（`mvv_state_base()/project-mvv/<root>`）に置き、中なら止める | 無し | (c) 指示なし（`--out-dir` を使わず手で書いた） |

### 1.8 一時の置き場（`$TMPDIR`・state）とその他（参考。#2229・#2243 の対象）

| # | パスのパターン | 区分 | 書き手 | 定義 | 持ち主・終わり | 固定 |
| --- | --- | --- | --- | --- | --- | --- |
| X1 | `CROSS_REVIEW_TMP_DIR`・`CROSS_REFACTORING_TMP_DIR`・`NDF_EXTERNAL_AI_TMP_DIR` の下 | tmp | 各 Skill のスクリプト | 契約（`drive-prx-report-md` ほか, tmp）・環境変数の契約 | `lib/run_state.py:422`（`--tmp-only` の掃除） | (a) |
| X2 | `{state_dir}/work/approval-{verify,promote,deploy}.md` | state | `supervise_lib/delivery_templates.py:19-21,173` | 無し（契約に無い） | 状態ディレクトリと同じ | (a) |
| X3 | `project-decl.py measure --out "$TMP/measure.json"` | tmp | `scripts/project-decl.py:430` | 手順書（`project-analysis.md:10,37`） | 無し | (b) |
| X4 | `pr-steps.py template --out /tmp/pr-body.md`・`--body-file /tmp/pr-body.md` | `/tmp` 固定 | `scripts/pr-steps.py:473` | 手順書（`pr/SKILL.md:92,114-116`）が `/tmp` の固定名を書く（並列のレーンで同名になる） | 無し | (b) |
| X5 | `fix-steps.py --out`（既定 `$TMP_DIR/fix-pr<PR>-result.json`） | tmp | `skills/fix/scripts/fix-steps.py:571` | スクリプトの既定 | 無し | (b)（既定は (a)） |
| X6 | `supervise.py design-glossary --out <候補の語.md>` | AI 次第 | `scripts/supervise.py:183` | 無し | 無し | (b) |
| X7 | `<worktree の隣>/<pr<n>\|rf<n>>.owner.json` | `.worktrees/` の隣 | `scripts/lib/run_owner.py:3,44-46` | 手順書・設計（#2342） | #2342 の範囲 | (a) |
| X8 | `<git の共通ディレクトリ>/ndf-main.lock` | `.git/` | `merged_lib/main_lock.py:16-20`・`worktree-session.sh:160` | スクリプトの docstring | 残る（排他の外は空） | (a) |

### 2. 実態との突き合わせ（今メインディレクトリにあって、どの書き手にも当たらないもの）

| もの | 推定される出所 |
| --- | --- |
| `.ndf/handoff/notice-to-l20261031.md`・`notice-to-lfactory2.md`・`notice-to-lroot2.md`・`notice-to-m1426.md`・`notice-to-m26-2.md`・`notice-version-lfactory2.md` | conductor・オーガナイザーが手で書いた伝言（H6） |
| `.ndf/handoff/loop-lfactory2-lane.md`・`loop-lflow-lane.md`・`loop-lroot2-lane.md` | 手で書いたレーン用の文書（H5） |
| `.ndf/handoff/lroot-exclude.json` | 手で書いた除外の一覧（H7） |
| `.ndf/handoff/milestone-25-ndf-factory.md` | 名の形に合わない手書き（H8） |
| `.ndf/handoff/loop-p3.md` | `p3` は並列の計画名。並列は `parallel-p3.md`（H4）を書くので、`loop-p3.md` は名を取り違えた手書きか旧版の名残（推定） |
| `.ndf/sprints/sprint-p2436`・`sprint-p2439` | `new sprint --out .ndf/sprints/…` を AI が選んだ（S1） |
| `.ndf/tmp/sprint-m1426a`〜`sprint-m26-3`（10 件）・`*.md` 6 本・`*.sh` 4 本・`lap5-close.json` | マイルストーン 26 の頃に AI が選んだ置き場（T2〜T4） |
| `.ndf/parallel/p3/notes/*.md`（9 本） | オーガナイザーがレーンへの指示を手で書いた（P3） |
| `.ndf/loop/l20261031/lap-2/queue-6.log`〜`queue-10.log` | AI のリダイレクト（L7） |
| リポジトリ直下の `p2436.lock` | `sprint-state.py gate p2436 …` を相対パスで打った錠の残り（S6。推定） |
| `issues/approval-ndf-v10.27.21-lflow2.md` | 書き手の名の形に無い（I3） |
| `issues/mvv-v6-candidate-A.md`・`-B.md`・`mvv-v6-revision.md` | MVV の改訂を手で書いた（I9。スクリプトの規則に反する） |
| `issues/execution-plan-26.md` | 書き手（I4）には当たるが、閉じ方（PR へ投稿して消す）が打たれていない |
| 追跡中の `issues/issue-2047-design.md`・`issue-2047-requirements.md` | 書き手には当たるが、#2047 は CLOSED なのに plan-to-spec も `old/` への移動もされていない |

### 3. 要約

### 3.1 置き場の定義が無い: 17 件
H5・H6・H7・H8・L3・L7・P3・S1・S3・S4・S6・T1・T2・T3・T4・I3・I9（ほかに X2・X6 も契約・手順書に無い）
探した語と範囲: `notice-`・`lane\.md`・`exclude\.json`・`sprints`・`queue-`・`notes` を `plugins/ndf`（tests・experimental を除く）と `docs/specifications` で grep。

代表例:
1. `.ndf/sprints/`・`.ndf/tmp/sprint-*` — `supervise_lib/procedures.py:205` の既定がカレント相対の `sprint-<名>`、手順書は `<作業ディレクトリ>`（`development-workflow/SKILL.md:120`）のまま
2. `.ndf/handoff/notice-to-*.md` — 書き手も名の形も無い（`handoff.md:31-37` の表に無い）
3. `.ndf/tmp/test-run/` — `scripts/test-run.py:47` の定数だけで、契約・手順書・終わりが無い
4. `<状態ファイル>.lock`（`p2436.lock`）— `scripts/sprint-state.py:263` → `lib/locks.py:31` が状態ファイルの隣に作り、消さない
5. `.ndf/parallel/<計画>/notes/` — 書き手のスクリプトも手順書も無い

### 3.2 置き場が手順書（かスクリプト）にだけあり契約に無い: 13 件
H1・H2・H3・H4・L4・L5・B1・B2・I1・I2・I4・I5・I6（契約の `files` に `.ndf/handoff/` と `issues/` の項目が 1 つも無い）
代表例:
1. `.ndf/handoff/<名>.md` — `handoff.md:20`・`scripts/handoff.py:45`
2. `.ndf/handoff/parallel-<計画>.md` — `lib/parallel_lane.py:33`。しかも `handoff.md:31-37` の名の表にも無い
3. `issues/approval-<plugin>-v<版>.md` — `supervise_lib/release_templates.py:363`・`glossary.md:118`
4. `.ndf/backlog/depths.jsonl`・`owner.json` — `upkeep_depth.py:66`・`depths.md:49,57`
5. `issues/execution-plan-<キー>.md` — `issue-plan-strategy/references/execution-plan.md:21`

あわせて: **契約の項目は持ち主と終わりの欄を持たない**。契約に載る 34 種も、終わりの定めはどこにも無い。`state-json` は place=state-dir だが、ループの周回では `.ndf/loop/<名>/lap-<n>/state.json`（main）に書かれる（`sprint_loop.py:315`）。

### 3.3 同じ種類が複数の場所に置かれている: 6 種
1. **スプリントのプラン**: `.ndf/sprints/`・`.ndf/tmp/sprint-*`・`.ndf/loop/<名>/lap-<n>/sprint/`（`sprint_loop_select.py:413`）・`~/.local/state/ndf/sv/`（`supervise_lib/paths.py:233`）・`/tmp`
2. **設計・起票の下書き**: `issues/issue-<N>-design*.md`（追跡）・`.ndf/tmp/2047.md` ほか（追跡外）・`.ndf/parallel/p3/notes/`
3. **承認資料**: `issues/approval-*.md`（`release_templates.py:363`）・`{state_dir}/work/approval-*.md`（`delivery_templates.py:19-21`）・`.ndf/parallel/<計画>/releases/<連番>/<提示>/`
4. **MVV の作業ファイル**: `mvv_state_base()/project-mvv/<root>`（`project-mvv.py:88-98`）と `issues/mvv-v6-*.md`（追跡外）・`issues/mvv-candidates-ai-plugins.md`（追跡）
5. **引継ぎ・伝言**: `.ndf/handoff/<名>.md`・`notice-to-*.md`・`*-lane.md` と `.ndf/parallel/<計画>/notes/`
6. **錠**: 対象の隣の `<target>.lock`（`.ndf/loop/*/loop.json.lock`・`.ndf/backlog/*.lock`・リポジトリ直下 `p2436.lock`）・`~/.claude/ndf/locks/issue-body/`（`lib/gh_parts.py:136-139`）・`.git/ndf-main.lock`

### 3.4 固定のされ方（利用者の方針 2026-11-06: スクリプトで固定するのが良い）
表の行 51 件の内訳: (a) 22 件 / (b) 14 件 / (c) 16 件（うち「指示なし」9 件）。H1 はパス (a)・名と中身 (c) で両方に数えた。

**(b) スクリプトが書くがパスは AI が選ぶ: 14 件**（P4・S1〜S6・B5・T2・I5・X3〜X6）。代表例:
1. `supervise.py new sprint/close --out` — `supervise_lib/procedures.py:205`・`new_args.py:136-143`（`.ndf/sprints`・`.ndf/tmp/sprint-*` の出所）
2. `<プラン>-state/` — `supervise_lib/paths.py:192`（プランの置き場で実体の置き場が決まる）
3. `sprint-state.py init <パス>` とその `.lock` — `sprint-state.py:263`（`p2436.lock` の出所）
4. `supervise.py queue --done` と `<done>.plans.json` — `supervise.py:57`
5. `pr-steps.py template --out /tmp/pr-body.md` — `pr-steps.py:473`・`pr/SKILL.md:92`（固定名の `/tmp`）

**(c) 手順書の指示で AI が書く: 16 件**（H1 の名と中身・H2・H5〜H8・L7・P3・T3・T4・I3・I4・I6〜I9）。代表例:
1. 引継ぎ文書の名と中身 — `handoff.md:31-37,58`
2. `issues/execution-plan-<キー>.md` — `execution-plan.md:18-26`
3. `issues/issue-<N>-design*.md` — `design/references/design-template.md:180`
4. 指示なし: `.ndf/handoff/notice-to-*.md`・`*-lane.md`・`lroot-exclude.json`
5. 指示なし: `.ndf/tmp/*.md`・`*.sh`、`.ndf/parallel/<計画>/notes/`、`issues/mvv-v6-*.md`
