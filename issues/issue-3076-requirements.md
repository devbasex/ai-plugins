# #3076: Fix: test-run.py が衝突した Pull Request の CI を上限まで空待ちする

正は課題の本文（#3076）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。この課題は本文の全体を起票者（レーンを観察したオーガナイザー）が書いており、利用者の指示の原文は無い。設計承認ゲートで確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 衝突した Pull Request では、CI の上限まで待たずに `stopped` で止まり、ベースブランチの取り込み（`base-sync`）を促す | 本文の「期待する結果」 | 受け入れ条件 1・2 |
| S2 | 衝突の状態は `gh pr view --json mergeable` で、照会のたびか最初の未完了のときに 1 回読む | 本文の「起票者の推定」の 1 段落目 | 読む時期と手段は設計で決める。上限は非機能の条件（照会 1 回につき 1 回まで） |
| S3 | 衝突の無い Pull Request の CI の待ちは今と変わらない | 本文の「受け入れ条件」2 | 受け入れ条件 3・6 |
| S4 | 再現するテストを置く | 本文の「受け入れ条件」3 | 受け入れ条件 7 |
| S5 | `supervise.py queue` の `--from` で `base-sync` より後のステップから再開するときに取り込みを飛ばしてよいかを、この課題で一緒に見る | 本文の「起票者の推定」の 2 段落目 | 含まない（未決の 1 行目） |

## 事象

`test-run.py whole --pr <番号>` が、ベースブランチと衝突した Pull Request の CI を上限（`ci_wait_timeout`）まで待ち続ける。衝突した Pull Request では GitHub が `pull_request` のワークフローを起動しないため、チェックは一覧に現れない。CI の待ちはチェックが一覧に無いことを「未完了（pending）」と読むため（`lib/gh_checks.py` の `checks_outcome`）、上限まで照会を繰り返してから `stopped` で戻る。

実例（2026-11-12、計画 p5 のレーン lflow）: PR #3069 https://github.com/devbasex/ai-plugins/pull/3069 （#2997、ベースは `sprint/lflow-1`）が衝突したまま、`supervise.py queue` の中の `test-run.py whole --pr 3069` が 40 分以上、子プロセスも CPU も使わずに待っていた。`gh pr checks 3069` の出力は `no checks reported`。

2026-11-12 に照会した PR #3069 の状態（マージ済みだが衝突の記録が残る）:

```text
$ gh pr view 3069 --json mergeable,mergeStateStatus,state,baseRefName
{"baseRefName":"sprint/lflow-1","mergeStateStatus":"DIRTY","mergeable":"CONFLICTING","state":"MERGED"}
```

## 期待する結果

チェックの結論が未完了のとき、`test-run.py` が Pull Request の衝突の状態を確かめ、衝突していれば上限を待たずに「衝突のため CI が起動しない」として `stopped` で止まり、ベースブランチの取り込み（`base-sync`）を促す。

## 再現手順

1. スプリントブランチへの Pull Request を出し、その後にスプリントブランチへ同じファイルを触る別の Pull Request をマージして衝突させる
2. CI に任せる戦略のリポジトリで `python3 plugins/ndf/scripts/test-run.py whole --pr <番号>` を打つ
3. CI のチェックが現れず、`ci_wait_timeout` まで待ってから `stopped` で戻る

## 環境

- NDF 10.27.55（develop 81bbcd924 時点）
- 待ちの経路: `plugins/ndf/scripts/test-run.py` の `_wait_ci` → `plugins/ndf/scripts/lib/test_triage.py` の `wait_check`（`gh_checks.checks_outcome` はチェックの未登録を `pending` と返す）
- 2026-11-12 に `_wait_ci` を読んで確かめた事実: `--pr` の値は引数として受け取るが `_wait_ci` では使っておらず、照会するのは HEAD のコミットのチェックだけである（`grep -n "a\.pr" plugins/ndf/scripts/test-run.py` は 0 件）

## 影響

衝突した Pull Request ではテストの工程が上限まで空待ちし、レーンが止まって見える。実例では lflow の #2997 と、ファイルが重なって後に控える #2998 が 40 分以上進まなかった。

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。`test-run.py whole` の引数・結果 JSON の形・終了コードの意味は変えない。衝突で止まったときは既存の `stopped`（終了コード 2）を使う |
| データ | 変わらない |
| 既存の振る舞い | CI に任せる戦略で `--pr` を渡した全体テストの、チェックが未完了の間だけ変わる |

## 目的

衝突した Pull Request で、全体テストのステップが上限まで空待ちせずに止まり、次にすること（ベースブランチの取り込み）が結果に書かれている状態にする。

## 前提

- 前提 1: 衝突を確かめるのは `--pr` を渡したときだけとする。工程が組み立てる全体テストのステップは常に `--pr {pr}` を渡す（`plugins/ndf/scripts/supervise_lib/verify_steps.py` の `whole_cmd`）。`--pr` を渡さない呼び出しは今と同じに待つ
- 前提 2: 衝突の状態は GitHub の Pull Request の `mergeable` の値（`MERGEABLE` / `CONFLICTING` / `UNKNOWN`）で読む。`UNKNOWN` は GitHub がまだ計算していない状態で、衝突とは読まない
- 前提 3: 衝突の状態を読めなかった（照会の失敗）ときは、待ちを止めずに今と同じく続ける。照会の失敗で止めると、一時的な失敗で衝突の無い Pull Request まで止まる
- 前提 4: 衝突で止まったときの状態は `stopped` とする（`gate` にしない）。ベースブランチの取り込みは `merged-steps.py base-sync` が受け持ち、人の判断を待つ操作ではない。取り込みを誰がいつ打つかはこの課題で変えない
- 前提 5: チェックの結論が既に出ている（成功・失敗）ときは、衝突の状態を読まずに今と同じ扱いにする。衝突の状態を見るのは、結論が未完了の間だけである

## 対象範囲

含む:
- CI に任せる戦略の全体テスト（`test-run.py whole --pr`）の CI の待ちで、結論が未完了の間に Pull Request の衝突の状態を確かめ、衝突していれば止まる
- 止まったときの結果の要約と次の 1 手（衝突であることと、ベースブランチの取り込みのコマンド）
- 上の振る舞いを再現するテスト

含まない:
- `supervise.py queue` の `--from` で `base-sync` より後のステップから再開するときに、取り込みを飛ばしてよいか（S5。未決の 1 行目）
- 衝突を自動で解く・自動で `base-sync` を打つこと
- 手元で全体テストを走らせる戦略、範囲テスト（`test-run.py scope`）
- マージの待ち（`merged-steps.py merge-when-green`）と `gh_parts.py pr-status` の判定。`pr-status` は既にマージの状態 `DIRTY` を「人へ戻す」と判定している（`plugins/ndf/scripts/lib/gh_pr_status.py` の `decide_action`）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 全体テストのステップが CI の待ちを始めた | CI に任せる戦略で `test-run.py whole --pr <番号>` が打たれた | origin か HEAD が分からない・待つチェックの名前が無い: 今と同じく `stopped` | — |
| E2 | チェックを照会し、結論が未完了だった | E1 の後の照会（間隔は `limits.policy("test-triage.check-wait")`） | 照会の失敗: 今と同じく結論なしとして扱う | E1 |
| E3 | Pull Request の衝突の状態を読んだ | E2（結論が未完了で、`--pr` が渡されている） | 読めない: 前提 3 のとおり待ちを続ける。`UNKNOWN`: 前提 2 のとおり待ちを続ける | E2 |
| E4 | 衝突のため待ちを打ち切り、`stopped` で戻った | E3 で `CONFLICTING` を読んだ | — | E3 |
| E5 | チェックの結論が出たか、上限に達した | E2 の繰り返し | 上限: 今と同じく `stopped` | E2（E4 が起きていない） |

## 用語

| 用語 | 意味 |
| --- | --- |
| 衝突の状態 | Pull Request をベースブランチへ取り込めるかを GitHub が計算した値（`mergeable`）。`CONFLICTING` のとき GitHub は `pull_request` のワークフローを起動しない |
| CI の待ち | CI に任せる戦略の全体テストで、`test-run.py` が Pull Request のチェックの結論が出るまで照会を繰り返すこと（`_wait_ci`） |

## 受け入れ条件

- [ ] 1. CI に任せる戦略で、衝突の状態が `CONFLICTING` の Pull Request に `test-run.py whole --pr <番号>` を打ち、チェックの照会が未完了を返すと、`ci_wait_timeout` に達する前に状態 `stopped`・終了コード 2 で戻る。照会の結論が未完了のまま衝突の状態を `CONFLICTING` と読んだ時点で、次のチェックの照会を待たずに戻る
- [ ] 2. 1 の結果の要約に、Pull Request の番号と「衝突のため CI が起動しない」ことが書かれ、次の 1 手（結果の `next`）にベースブランチの取り込みのコマンド（`merged-steps.py base-sync --pr <番号>`）が書かれる
- [ ] 3. 衝突の状態が `MERGEABLE` の Pull Request では、CI の待ちの結果（成功なら通過、失敗なら JUnit による見分け、上限なら既存の文言の `stopped`）・照会の間隔・上限が今と変わらない
- [ ] 4. 衝突の状態が `UNKNOWN` のとき、または衝突の状態を照会できなかったときは止まらず、次のチェックの照会へ進む。その後に `CONFLICTING` を読めば 1 のとおり止まる
- [ ] 5. `--pr` を渡さない `test-run.py whole` は、衝突の状態を照会せず、今と同じに待つ
- [ ] 6. チェックの照会が最初から成功か失敗の結論を返すときは、衝突の状態を照会しない（Pull Request の照会の回数が 0）
- [ ] 7. 1・3・4・5・6 を、GitHub の照会と待ちの `sleep` を差し替えた pytest で再現するテストがある（実際の GitHub へ問い合わせない）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 衝突の状態の照会は、未完了を返したチェックの照会 1 回につき 1 回まで。衝突の無い Pull Request の CI の待ちで増える GitHub の照会はこれだけとする |
| 可用性 | 衝突の状態の照会の失敗で、CI の待ちを止めない（前提 3） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_test_run.py plugins/ndf/scripts/tests/test_lib_test_triage.py -q`（置くファイルは計画で決める） |
| 静的解析・型検査 | プロジェクトの宣言（`.ndf/project.json` の `test.suites`）の静的解析を `test-run.py scope --only-kind lint` で当てる |
| 手動確認 | リリース後テストで、衝突した Pull Request（例: 衝突させた検証用の Pull Request）に `test-run.py whole --pr <番号>` を打ち、上限より前に `stopped` で戻り要約に衝突が書かれることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | NDF のスクリプトは `plugins/ndf/scripts/`、共通の処理は `plugins/ndf/scripts/lib/`。GitHub の照会は既存の層（`gh_call`・`gh_pr_info`・`gh_checks`）を使い、`test-run.py` に `gh` の呼び出しを直に増やさない |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。結果 JSON は `lib/step_result.py` の `result` |
| テスト戦略 | 単体テストで判定（衝突の状態ごとの止まる・続ける）を確かめる。`.md` の文言を照合するテストは書かない（`AGENTS.md` の DON'T） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 範囲テストと静的解析の実行、既存の照会の層の再利用 |
| 確認してから行う | `test-run.py` の引数・結果 JSON の形・終了コードの意味の変更（この課題では変えない。設計承認ゲートで見る） |
| 行わない | 衝突の自動解決、`base-sync` の自動実行、`--from` の再開の扱いの変更、マージの待ちと `pr-status` の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `supervise.py queue --from` で `base-sync` より後のステップから再開するとき、取り込みを飛ばしてよいか（S5）。この課題の範囲から外し、扱うなら別の課題として起票する | 設計承認ゲートで利用者 | 設計承認ゲート |
| `--pr` を渡さないときに、ブランチから Pull Request を引いて衝突を確かめるか（前提 1）。工程は常に `--pr` を渡すため、この課題では扱わない | 設計承認ゲートで利用者 | 設計承認ゲート |

## 依頼（原文）

起動指示（書き手: AI の起動指示、日付: 2026-11-12）:

> #3076。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

課題の本文（書き手: 起票者。レーンを観察したオーガナイザーが書いた。日付: 2026-11-12）:

> ## 事象
>
> `test-run.py whole --pr <番号>` が、ベースと衝突した（`mergeable: CONFLICTING`）Pull Request の CI を上限（`ci_wait_timeout`）まで待ち続ける。衝突した Pull Request では GitHub が `pull_request` のワークフローを起動しないため、チェックは「未登録（pending）」のまま現れず、待つだけで終わる。
>
> 実例（2026-11-12、計画 p5 のレーン lflow）: PR #3069 https://github.com/devbasex/ai-plugins/pull/3069 （#2997、base `sprint/lflow-1`）が衝突したまま、`supervise.py queue` の中の `test-run.py whole --pr 3069` が 40 分以上、子プロセスも CPU も使わずに待っていた。`gh pr checks 3069` は `no checks reported`。
>
> ## 期待する結果
>
> チェックが pending のまま現れないとき、`test-run.py` が Pull Request の `mergeable`（`CONFLICTING`）を確かめ、上限を待たずに「衝突のため CI が起動しない」として止まる（`stopped`。ベースへの追従を促す）。
>
> ## 再現手順
>
> 1. スプリントブランチへの Pull Request を出し、その後にスプリントブランチへ同じファイルを触る別の Pull Request をマージして衝突させる
> 2. `python3 plugins/ndf/scripts/test-run.py whole --pr <番号>` を打つ（CI に寄せる戦略のリポジトリ）
> 3. CI のチェックが現れず、`ci_wait_timeout` まで待ってから `stopped` で戻る
>
> ## 環境
>
> - NDF 10.27.55（develop 81bbcd924 時点）
> - `plugins/ndf/scripts/test-run.py` の `_wait_ci` → `lib/test_triage.py` の `wait_check`（`gh_checks.checks_outcome` は未登録を `pending` と返す）
>
> ## 影響
>
> 衝突した Pull Request ではテストの工程が上限まで空待ちし、レーンが止まって見える。今回は lflow の #2997 と、ファイルが重なって後に控える #2998 が 40 分以上進まなかった。
>
> ## 起票者の推定
>
> `_wait_ci` の `fetch` は check runs だけを見ており、Pull Request が CI を起動できる状態か（`mergeable`）を見ていない。照会のたびか、最初の pending のときに `gh pr view --json mergeable` を 1 回読み、`CONFLICTING` なら待たずに止めるのがよい。
>
> なお今回の衝突そのものは、レーンがキューを `--from 6-impl-2997=pr` で再開し、ベースへの追従（`base-sync`）を飛ばしたことで起きた。`--from` で `base-sync` より後のステップから再開するときに追従を飛ばしてよいかは、別の論点としてこの課題で一緒に見る。
>
> ## 受け入れ条件
>
> 1. 衝突した Pull Request に `test-run.py whole --pr` を打つと、`ci_wait_timeout` を待たずに `stopped` で戻り、理由に衝突が書かれる
> 2. 衝突の無い Pull Request の CI の待ちは今と変わらない
> 3. 再現するテストがある
>
> ## 由来
>
> PR #3069 https://github.com/devbasex/ai-plugins/pull/3069 （計画 p5 のレーン lflow の #2997）。オーガナイザーがレーンの観察で見つけた
