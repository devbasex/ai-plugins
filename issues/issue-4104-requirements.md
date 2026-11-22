即時修正のプラン（`contracts/templates/fix.json`）で、マージの前に MVV 判定を行い、判定した PR・宛先・head を CI からマージまで同じものに束ねる。

## 何が起きたか

#3959（MVV 判定を進め方によらず行う）の設計 PR https://github.com/devbasex/ai-plugins/pull/4093 で、即時修正のマージの前に MVV 判定を置く設計を書いた。cross-review の 3 ラウンドで、判定した PR・宛先・head とマージする PR を束ねる仕組みへの指摘が毎ラウンド続き（r1-2 → r2-3・r2-8 → r3-2・r3-3・r3-4・r3-7）、直すたびに `ndf ci watch`・`ndf ci merge`・GitHub の adapter・プランの部品へ広がった。2026-11-22 に conductor が範囲を分けると決め、#3959 から外してこの Issue にした。

今の即時修正のプラン（test → push → pr → conflict-check → ci → merge）は MVV 判定を通らずに main へマージする。利用者の指示は「MVV 判定は常に実施してください」「normal でも即時修正でも必要です」（2026-11-21、#3959）。

## 種類

機能追加（即時修正のプランに、今のコントラクトに無い判定のステップと、CI・判定・マージの間で PR を受け渡す形を足す）

## 今の姿

即時修正は、conductor が `ndf route run immediate_fix` で流すプランで、ステートマシン（`ndf progress`）の外で動く。承認ゲートを通らず、CI が通れば人を待たずに main へマージする（`development-workflow` の `references/conductor-entrypoints.md`・`pace.md`）。

| ステップ | 今の振る舞い | 置き場 |
| --- | --- | --- |
| `pr` | `gh pr create --base <宛先> --head <ブランチ>` で PR を出し、`gh pr view <ブランチ>` で番号を読む | `contracts/templates/parts/steps.json` の部品 `pr` |
| `ci` | `gh pr view <ブランチ>` で選んだ PR の番号を `ndf ci watch --pr` に渡し、結果を `{state_dir}/ci.json` に書く。成功の結果は確かめた PR の宛先と head を持たない | 部品 `ci`・`src/ndf/ci_cli.py` の `_watch`・`_report` |
| `merge` | `ndf ci merge --branch <ブランチ>` が、ブランチの開いた PR を探し直し、作業場所の HEAD を期待の head にしてマージする。済んだかの確かめ（`probe`）も `gh pr view <ブランチ>` の状態で見る | 部品 `merge`・`ci_cli._merge`・`adapters/github.py` の `merge`・`_preflight` |
| MVV 判定 | 無い。`ndf records mvv gate` は区分 `design`・`release` だけを受け、ステートマシンの工程の入力から動く | `src/ndf/domains/records/mvv_gate.py`・`records/cli.py` |

## 何を足すか・変えるか

| | 前 | 後 |
| --- | --- | --- |
| 即時修正のマージの前 | MVV 判定が無い | CI の後・マージの前に MVV 判定のステップがある。進めてよいときだけマージへ進み、進めないときは判定の記録を PR に書いて人の回答を待つ |
| CI・判定・マージが扱う PR | ステップごとにブランチ名から PR を探し直す | CI が確かめた PR（リポジトリ・番号・宛先・head）を判定とマージが受け取り、違えば止まる |
| マージの前の突き合わせ | head（作業場所の HEAD）だけ | 宛先と head |

### 今の利用者への影響と互換

承認済みの MVV を持たないリポジトリでは、判定を打たずに今どおりマージへ進む（記録も書かない）。承認済みの MVV を持つリポジトリでは、即時修正の 1 回ごとに LLM の判定 1 回と PR のコメント 1 件が増え、進めない判定では人の回答を待つ。後方互換は求めない（MVV Value 4。普及のフェーズまで）。

## なぜ困るか

即時修正は承認ゲートを通らないので、MVV に反する変更・レッドラインに当たる変更も、CI が通れば人が見ないまま main へ入る。利用者が「即時修正でも必要」と言った判定が無い。判定を足しても、判定した PR とマージする PR が別のもの（別の宛先・判定の後の head）になりうるなら、判定はマージを縛らない。

## 目的

即時修正も MVV に照らし、判定した PR と head だけがマージされる。

## 対象範囲

含む:

- 即時修正のプランの、CI とマージの間の MVV 判定のステップ（区分 `fix`）と、進めない判定の後に人の回答を待つ経路
- CI が確かめた PR の識別（リポジトリ・番号・宛先・head）を、CI の結果から判定とマージへ渡すこと
- マージの前に、渡された PR の宛先と head を突き合わせること

含まない:

- 設計承認ゲート・リリース承認ゲートの判定と省略（#3959・#4105）
- 省いた回答（`service:mvv`）を消費の時点で突き合わせる仕組み（#4106）
- 即時修正のプランの CI の直し（`repeat-check`）・衝突の直し（`base-sync`）の経路（今のまま）
- 判定の後に宛先のブランチが進んだこと（判定が規則を読んだベースのコミットと今の先端の差）の突き合わせ（入れない理由は設計の決定に書く）

## 前提

- 前提 1: #3959 の実装（判定を打つ条件と省く条件の分離・ベースの MVV から事実を作る `snapshot_facts`・判定の手順 (1) が PR を最初に 1 回読む形・`CHECKS_BY_GATE`）が main へ入った後に実装する。判定の本体（`ndf records mvv gate`）に区分 `fix` を足す
- 前提 2: 即時修正のプランは 1 つの作業場所で 1 本ずつ流れ、CI・判定・マージのステップは同じ作業場所の origin のリポジトリを読む

## 受け入れ条件

- [ ] 1. 即時修正のプランがマージの前に MVV 判定のステップを持ち、判定が「従う」でレッドラインが空で、機械の検査（ベースの MVV が承認済み・レッドラインのパス・MVV の設定の変更・対応表・レビューの保留）が外れていないときだけマージへ進む。ほかは止めて理由を添えて人へ戻す。承認済みの MVV が無いリポジトリでは今どおりマージへ進む（#3959 の要求仕様の受け入れ条件 6 から移した）
- [ ] 2. CI が確かめた PR・判定した PR・マージする PR が、リポジトリ・番号・宛先・head で同じである。判定の後に head か宛先が動けば、マージは止まる
- [ ] 3. 判定がマージへ進めず、判定の記録を PR に書けたときは、プランが人の回答を待つ。人が承認すれば判定した PR をその head のままマージし、却下すればマージせずに終える。判定の記録を PR に書けない止まり方（PR を読めない・CI の PR と食い違う・ベースの MVV が承認の記録と合わない・記録を書けない）は人の回答を待たずに止まる（2026-11-22、設計者が足した。#4093 の指摘 r1-16 の「止まった後の扱い」を条件にした）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる（`互換なし`）。`ndf records mvv gate` に区分 `fix` と引数を足す。`ndf ci watch` の成功の結果に CI が確かめた PR の項目を足す。`ndf ci merge` に、ブランチで探さず結果の PR の項目でマージする引数を足す。即時修正のプランのステップが変わる |
| 重さ | 承認済みの MVV を持つリポジトリで、即時修正が MVV に照らされずに main へ入る |
| データ | 永続のデータの形は変えない。プランの状態ディレクトリに判定の結果のファイルが 1 つ増える |
| 既存の振る舞い | 承認済みの MVV を持つリポジトリの即時修正で、LLM の判定 1 回・PR のコメント 1 件が増え、進めない判定では人を待つ |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | CI のステップが PR の必須のチェックの成功を確かめ、確かめた PR の識別を結果に書いた | プランの `ci` | CI が落ちた → 今どおり直しへ（`repeat-check`） | `pr` の後 |
| E2 | 判定のステップが E1 の PR を読み、識別が一致することを確かめた | プランの判定のステップ | 違う・読めない → 止まる（人の回答を待たない） | E1 の後。E1 と E2 の間に push・宛先の変更が入りうる |
| E3 | 判定のステップが MVV に照らして判定し、記録を PR に書いた | E2 が通った | 判定を打てない → 止まる | E2 の後 |
| E4 | 進めない判定の後に、人が承認か却下を回答した | E3 が進めない判定 | 却下 → マージせずに終える | E3 の後。待つ間に push・宛先の変更が入りうる |
| E5 | マージのステップが E1 の PR を、宛先と head を突き合わせてマージした | E3 が進める判定か、E4 の承認 | 宛先か head が違う → 止まる | E3 か E4 の後 |

## 検証手段

- 単体テスト: `env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras pytest plugins/ndf/tests -q -n 4` の該当するテスト（判定の区分 `fix`・CI の結果の PR の項目・マージの突き合わせ・即時修正のプランの読み込み）。このリポジトリはテストを CI に寄せる（`.ndf/project.json` の `test.ci.only`）ので、合否は PR の CI で決める
- LLM の判定の中身に依る条件は、判定の関数をスタブに差し替えて確かめる（CI で LLM を呼ばない）
- 受け入れ条件 2・3 の、判定の後の push・宛先の変更は、GitHub の adapter のスタブで PR の head と宛先を変えて確かめる

## 関連

- #3959（親の課題。判定を打つ条件・レビューの保留・`--machine`）
- #4106（省いた回答を消費の時点で突き合わせる。判定の出力の `pr`・`base_ref`・`base_sha` を足す）
- #4105（リリース承認ゲートの保留の範囲。即時修正とは重ならない）
- 範囲を分けた判断: #3959 の設計文書の決定 8

## 移した cross-review の指摘

設計 PR https://github.com/devbasex/ai-plugins/pull/4093 のコミット `19adf3511ffa0492ecd019dcc61cd317096baf7b` の cross-review の記録（https://github.com/devbasex/ai-plugins/pull/4093#issuecomment-7077635480 ）から写した。どれも #3959 の設計 PR では直した扱いだが、この範囲を #3959 から外したので、この Issue の設計で同じ論点を満たす。

| 番号 | 担当 | 重要度 | 場所 | 指摘 | 終わり方 | 見送った理由 |
| --- | --- | --- | --- | --- | --- | --- |
| r1-2 | codex | critical | issues/issue-3959-4055-design-detail.md:148 | `fix` の `merge` は判定の `head` を受け取らず、`ndf ci merge` は実行時の作業場所の HEAD を期待値にします。判定後に同じ worktree で修正して push すると、判定を通っていないコミットがマージされ得ます。判定した PR と SHA をマージに渡し、既存の `--reviewed` と期待 SHA の照合を使って、対象が動いたら再判定まで止めてください。 | 直した | — |
| r2-3 | codex | major | issues/issue-3959-4055-design-detail.md:164 | マージへの引き渡しが head SHA だけなので、同じ head のまま PR の宛先を変更すると、別のベースの MVV で得た許可でマージできます。同じブランチから異なる宛先に PR がある場合も、マージ時の再検索で別の PR を選べます。判定したリポジトリ・PR 番号・宛先・head を引き渡し、マージ側の直前確認で一致を要求してください。 | 直した | — |
| r2-8 | codex | major | issues/issue-3959-4055-design-detail.md:162 | `fix` は直前の `ci` が確認した head を受け取らず、その時点の PR の head にマージ許可を出します。`ndf ci watch` の成功結果には head がないため、CI 後に同じ作業場所から追加コミットを push すると、新しい head が MVV だけを通ってマージへ進めます。CI が確認した head を成功結果に残し、MVV 判定とマージがその head に束縛されるようにしてください。 | 直した | — |
| r3-2 | codex | major | issues/issue-3959-4055-design-detail.md:183 | 即時修正の CI は引き続き `gh pr view <branch>` で PR を選びますが、MVV 判定は宛先で絞った PR を選び、CI の結果とは head だけを照合します。同じコミットから別の宛先にも PR がある場合、別の PR の CI 成功を使って判定対象の PR をマージへ進められます。CI の前にリポジトリ・PR 番号・宛先を確定し、CI 結果から MVV 判定・マージまで同じ識別情報を照合してください。 | 直した | — |
| r3-3 | codex | major | issues/issue-3959-4055-design-detail.md:179 | `fix-merge` の `probe` が既存の `merge` と同じだと、ブランチ名で見つかった別の宛先の PR が `MERGED` の場合に、記録した PR が開いたままでもステップが成功します。`probe` は本体より先に実行されるため、`--judged` の検証も通りません。記録のリポジトリ・PR 番号で完了を確認し、別の PR のマージ済み状態では成功しないようにしてください。 | 直した | — |
| r3-4 | codex | major | issues/issue-3959-4055-design-detail.md:184 | `--judged` は `hub.pull(pr)` で宛先を照合した後に `hub.merge` を呼びますが、現行の `GitHub._merge_with` は内部で PR を再取得し、そこで読んだ宛先を使ってマージ方式やキューを選びます。その再取得で宛先の変更を観測しても処理を続けます。また、仕様どおりの呼び出しではプランの `--method` が渡らず、宣言と異なる方式を選び得ます。既存のマージ前検査に期待する宛先と head の照合を組み込み、`args.method` も渡してください。 | 直した | — |
| r3-7 | codex | major | issues/issue-3959-4055-design-detail.md:184 | `_mvv_gate` が `--judged-out` に書く repo・PR 番号・宛先は `_judged` 内で決まりますが、`gate` の返却契約には head しかありません。この仕様のままでは CLI が同じ値を得るために PR を探し直す実装になり、呼び出しが増えるうえ、判定に使った PR と異なる宛先や番号を記録し得ます。最初に取得した PR の識別情報を判定結果と一緒に CLI まで返す内部契約を定めてください。 | 直した | — |
| r1-16 | kiro | major | issues/issue-3959-4055-design-detail.md | 即時修正の判定(`--gate fix`)は、検査 4(`boundary_paths` に当たる)・5(`.ndf/mvv.md`・`.ndf/mvv.json` を変える)・7(保留)が外れると `stopped`・`refused` で止める。`fix.json` の `on_cause` は空で、止まった後に進める手順がない。同じ PR は打ち直しても同じ理由で外れるので、プランを再開しても毎回 `mvv` ステップで止まる。人がマージへ進める経路も、`fast`・`auto` の承認ゲートのように人の承認を判定の代わりにする経路もない。レッドラインのパスや MVV の設定を変える即時修正は、人が判定を使わず手でマージするしかない。実装者が違う回避策を作る。止まった後の扱いを決めて書くこと。例: 人が承認して `mvv` ステップを飛ばして再開する手順、または検査 4・5 の理由は `fix` では止めずに PR のコメントへ載せるだけにする、のどちらか。 | 直した | — |

## 移した設計（設計 PR https://github.com/devbasex/ai-plugins/pull/4093 のコミット `19adf3511ffa0492ecd019dcc61cd317096baf7b` から）

#3959 の設計文書と詳細設計から外し、ここへ移した。設計の起点にした記録で、この Issue で採った形は設計文書（`issues/issue-4104-design.md`）にある。下の「受け入れ条件 6」「前提 2」は #3959 の要求仕様の番号である。

#### 決定 3（#3959 の設計から移した）: 即時修正も MVV に照らすため、プランの CI とマージの間に同じ判定を `--gate fix` で置く

- **質問**: 即時修正のプランのどこで、どのコマンドで判定し、外れたらどう止めるか
- **選んだ案**: `fix.json` の `ci` と `merge` の間にステップ `mvv`（`ndf records mvv gate --gate fix --base <宛先> --ci-result <状態のディレクトリ>/ci.json --judged-out <状態のディレクトリ>/mvv-judged.json`）を置く。判定する PR は、CI の前に `ndf ci watch --branch --base` が作業場所のブランチから宛先 `base` への開いた PR を 1 件に決め（決まらなければ止まる）、CI の成功の結果に残したリポジトリ・番号・宛先・head の PR で、判定はその番号の PR だけを読み、宛先と head が CI の値と一致しなければ止まる。当てる検査は 1・4・5・6・7（スプリントの参照とモードは即時修正に無い）。進めてよい（理由が空・「従う」・レッドラインが空）ときだけ `ok` でマージへ進む。ほかは既存の原因 `refused` で、既存の部品 `merge-gate`（人の回答を待つ）へ進み、人が PR のコメントの判定の記録を読んで承認すればマージし、却下すれば終える。マージは判定したリポジトリ・PR 番号・宛先・head を `ndf ci merge --judged` に渡し、ブランチで PR を探し直さずに記録の番号の PR を、既存のマージ前の検査で宛先・head と照合してからマージし（ステップの済みの確かめも記録の番号の PR で行う）、作業場所の HEAD・PR の head・宛先のどれかが記録と違えば止まる（判定の後に直して push したコミットを判定なしでマージしない）。PR のコメントは設計・リリースと同じ `note` で書く。PR のベースの MVV が承認されていないリポジトリは判定せず `ok`（要求仕様の前提 2）。CI の後に置くのは、CI が落ちて直す間に head が変わるため、マージする head で判定するためである
- 採らなかった案: PR を出した直後（`pr` の後）に置く（CI の直し（`repeat-check`）で head が進むと、判定した head とマージする head が違う）
- 採らなかった案: 新しい原因の種類（`mvv_hold`）を足す（行き先は既存の `refused` と同じ人で、原因の表と exit_cause の行が増えるだけになる）
- 採らなかった案: 承認済みの MVV が無いリポジトリでも止める（MVV を持たない利用先の即時修正がすべて止まる。#4076 の前提 2 と同じく、MVV の無いリポジトリの振る舞いは変えない）
- 採らなかった案: `refused` で止めたまま行き先を持たない（同じ PR は打ち直しても同じ理由で外れ、プランを再開しても毎回止まる。人は判定を通らずに手でマージするしかなく、実装する人ごとに違う回避策ができる）
- 採らなかった案: 即時修正では検査 4・5 を止めずに PR のコメントへ載せるだけにする（レッドラインのパスや MVV の設定を変える即時修正が人を通らずに main へ入る。受け入れ条件 6 の「機械の検査が外れていないときだけ進む」に反する）
- **代償**: 即時修正の 1 回につき LLM の 1 回の費用と PR のコメント 1 件が増える。「判定できない」でも人の承認を待つので、即時修正が人を待つことがある
- 根拠: Value 1 / Value 3 / Value 4（MVV バージョン 11）


### 詳細設計の「即時修正のプラン」

#### 即時修正のプラン

満たす決定: 決定 3、受け入れ条件 6

| ファイル | 変える中身 |
| --- | --- |
| `contracts/templates/parts/steps.json` | 部品 `fix-ci` を足す: id は `ci`、`run` は `["bash", "-c", "set -o pipefail; out=$1; shift; ndf ci watch \"$@\" \| tee \"$out\"", "ci", "{state_dir}/ci.json", "--branch", "{{branch}}", "--base", "{{base}}", "--budget", "{{ci_budget}}", "--required={{required}}", "--test={{tests}}"]`、`on_cause` は部品 `ci` と同じ（`gh pr view <branch>` で PR を選ばない）。部品 `mvv` を足す: `{"id": "mvv", "run": ["ndf", "records", "mvv", "gate", "--gate", "fix", "--base", "{{base}}", "--ci-result", "{state_dir}/ci.json", "--judged-out", "{state_dir}/mvv-judged.json"]}`。原因は結果 JSON の `cause.kind`（`refused`・`precondition`）から読み、`exit_cause` は置かない。部品 `fix-merge` を足す: id は `merge`、`run` は `["ndf", "ci", "merge", "--branch", "{{branch}}", "--method", "{{merge_method}}", "--judged", "{state_dir}/mvv-judged.json"]`、`probe` は `["ndf", "ci", "merged", "--judged", "{state_dir}/mvv-judged.json"]`（部品 `merge` の `gh pr view <branch>` の probe を使わない） |
| `contracts/templates/fix.json` | `steps` を `test`・`push`・`pr`・`conflict-check`・`{"use": "fix-ci", "on_cause": {}}`・`{"use": "mvv", "on_cause": {"refused": "merge-gate"}, "next": "merge"}`・`{"use": "merge-gate"}`・`{"use": "fix-merge", "next": "end"}` にする。`params` に `gate`（`params.json` の既定 `ndf answer ask`）を足す。`summary` に MVV 判定と、進めない判定のときは人の承認でマージすることを足す |
| `src/ndf/ci_cli.py` | `watch` に `--branch <b> --base <base>` を足す（`--pr` と排他。どちらか必須）。`hub.pulls_by_head([b])` の開いた PR のうち `base_ref` が `base` のものを 1 件選び、0 件・2 件以上は `stopped`・`precondition`（「ブランチ <b> から <base> 宛ての開いた PR が 1 件に決まらない」。API の呼び出しは `gh pr view` の 1 回を置き換えるだけ）。`_report` の成功の結果にも `instance` に CI が確かめた head（`reader.pull.head_sha`。`HeadReader` が作業場所の HEAD に追いつくまで読み直した PR の head）を置き、`items` の先頭に CI が確かめた PR `{"kind": "pr", "repo": hub.repo, "number": 番号, "base": reader.pull.base_ref, "head": reader.pull.head_sha}` を置く。`merge` に引数 `--judged <パス>` を、サブコマンド `merged --judged <パス>` を足す（下） |
| `src/ndf/adapters/github.py` | `merge`・`_merge_with`・`_preflight` に `expect_base: Optional[str] = None`・`expect_head_ref: Optional[str] = None` を足す。`_preflight` は、マージ済みかを見る前に、`_merge_with` が読んだ `pull` の `base_ref`・`head_ref` が与えられた値と違えば `MergeOutcome(False, "refused", "#<n> の宛先が <base> でない（今は <base'>）" など)` を返す（マージ方式とキューの選択はこの照合を通った `pull.base_ref` で行う）。省けば今と同じ |

- CI が確かめた PR に束ねる: `fix-ci` のステップは CI の前に `--branch` と `--base` で PR を 1 件に決め、結果を `{state_dir}/ci.json` に書く。成功の結果は `items` に CI が確かめた PR（repo・番号・宛先・head）を持つ。`_mvv_gate` の `fix` はその項目を `Target` にして `gate(..., expect=<その Target>)` を呼ぶ。ファイルが無い・成功でない・`kind: "pr"` の項目が無いか形が違えば判定せずに `stopped`・`precondition`（「CI が確かめた PR が無い」）。`_judged` は CI の PR の番号の PR だけを読み（`pr_number`）、リポジトリ・宛先・head のどれかが CI の値と違えば止まる（`pr_number` の `GateError` か (1)。どちらも `precondition`）。同じコミットから別の宛先に PR があっても、その PR の CI の成功で判定する PR を進めない。CI の後に同じ作業場所から追加のコミットを push しても、CI を通らない head は MVV 判定もマージも通らない。止まったらプランを `ci` から打ち直す
- 判定した PR でマージする: `_mvv_gate` の `fix` は、`gate` の返した `Gated.target` が None でなければ `{"repo": target.repo, "pr": target.pr, "base": target.base, "head": target.head}` を `--judged-out <パス>` に書いてから結果を返す（`ok`・`stopped` のどちらでも書く）。None（PR を読む前に止まった）なら空のファイルを書く。CLI は PR を探し直さず、`outputs.mvv_gate` からも識別情報を組まない（判定に使った PR と記録が分かれない）。`ndf ci merge --judged <パス>` は、(1) ファイルが無い・空・形が違えば `stopped`・`precondition`（終了コード 2。判定を通らない PR をマージしない）。(2) `head` が作業場所の HEAD と違えば、PR を読まずに `refused`（今の `--reviewed` と同じ照合）。(3) `repo` が `--repo`（省けば作業場所の origin）と違えば `refused`。(4) `--branch` で開いた PR を探し直さず、`hub.merge(pr, expect_head=head, expect_base=base, expect_head_ref=args.branch, method=args.method)` を呼ぶ。宛先・ブランチ・head の照合は `_merge_with` が読む 1 回の `pull` で `_preflight` が行い、マージ方式とキューもその `pull` の宛先で選ぶ（別に `hub.pull` を読まないので、照合した宛先と方式を選ぶ宛先が分かれず、API の呼び出しも増えない）。断りは `refused`（宛先を変えた PR・同じブランチの別の宛先の PR を、判定に使ったベースの MVV と違うまま通さない）。プランの `--method` は今の `merge` と同じく渡す。`--judged` と `--reviewed` は同時に渡せない（`invalid`）。`ndf ci merged --judged <パス>`（`fix-merge` の `probe`）は記録の `repo` の `pr` を `hub.pull` で 1 回読み、マージ済みで `base_ref`・`head_sha` が記録と同じときだけ終了コード 0、ほか（記録が無い・空・形が違うを含む）は 1 を返す。ブランチ名で PR を探さないので、同じブランチの別の宛先の PR がマージ済みでも、記録した PR が開いていればステップを済んだとみなさず本体（`merge --judged`）へ進む。読んでからマージ API までの間に宛先を変える操作は、GitHub の API が head の SHA しか条件にできないので残る（脅威の前提はうっかりと権限の無い人で、宛先の変更には書き込みの権限が要る）。止まったら、プランを `ci` から打ち直して今の PR で判定し直す
- 判定が進めないとき（`refused`）の扱い: `mvv` は `merge-gate`（既存の部品。`{{gate}}` で人の回答を待ち、`approved` は `merge`、`rejected` は `end`）へ進む。`refused` になるのは、判定の記録を PR のコメントに書けた終わり方だけである（上の「PR を読んだ後の失敗」の表）。人は PR のコメントの判定の記録（理由・レッドライン・保留・外れた検査 4・5・7）を読んで決める。承認したら `fix-merge` が記録した PR と head のままマージする（承認の後に head・宛先が動けば上の照合で止まる）。PR を直して進めるときは `rejected` で終え、直したコミットでプランを打ち直す（`ci` と `mvv` が新しい head で確かめ直す）。判定を通らずに手でマージする経路は作らない。検査 4・5 で外れる即時修正（レッドラインのパス・MVV の設定を変えるもの）も同じ経路で人が承認してマージする（受け入れ条件 6 の「機械の検査が外れていないときだけ進む」を保つため、検査を注意に下げない）。`precondition`・`invalid` など `refused` 以外の原因は今どおり止まって人へ戻り、`merge-gate` へ進まない（コメントの無い理由で人に承認させない）
- CLI（`_mvv_gate` の `fix`）: `gate` の出力の `skip` が真なら `ok`（`summary`: 判定したときは「MVV 判定: マージへ進める」、ベースの MVV が承認されていないときは `why` の「MVV が承認されていないので判定しない」）。偽なら、出力に `head` が無い・`noted: false`・上の表で「コメント」が「書かない」の行のときは `result.stopped(TOOL, "precondition", "MVV 判定を打てない: " + " / ".join(why))`、ほかは `result.stopped(TOOL, "refused", "MVV 判定でマージへ進めない: " + " / ".join(why))`。どちらも `outputs.mvv_gate` を添える。`_judged` は表の行を見分けられるよう、「書かない」の行の出力に `noted: false` を置く（コメントを書けなかった (9) と同じ印）。CLI は `noted` と `head` の有無だけで原因を決め、MVV の状態を読み直さない（判定と例外を `_judged` が同じベースの `snap` から決める）

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `fix.json` | プランを読み込むと、ステップの順が test・push・pr・conflict-check・ci・mvv・merge-gate・merge。`mvv` の `refused` は `merge-gate`、成功は `merge` へ | — | — |
| `ci_cli._watch`・`_report` | 成功の結果の `instance` が `HeadReader` の読んだ PR の head、`items` に repo・番号・宛先・head の `kind: "pr"` | push の直後で PR が前のコミットを指す間は成功を返さない（今のまま）。同じブランチから別の宛先にも PR がある → `--base` 宛ての PR の CI を待つ | `--base` 宛ての開いた PR が 0 件・2 件 → `precondition`。`--pr` と `--branch` を同時 → `invalid` |
| `_mvv_gate`（`fix`） | 「従う」・レッドライン空・保留無し → 終了コード 0、`--judged-out` に `Gated.target` の repo・PR・宛先・head（PR の検索の API を呼ばない） | ベースの MVV が `absent` → 0 で判定しない（記録は書く） | 「判定できない」・レッドラインあり・保留あり・ベースの取得の失敗 → 1・`refused`、記録は書く。ベースが `mismatch` → 1・`precondition`。CI の PR と head・宛先が違う・CI の PR が `--base` 宛てでない・コメントを書けない・材料の間に head か宛先が動いた・材料の後の読み直しの失敗 → 1・`precondition`（`merge-gate` へ進まない）。`ci.json` が無い・`kind: "pr"` の項目が無い → `precondition`。PR を読めない → `precondition`・記録は空 |
| `ci merge --judged` | 記録と PR・作業場所の HEAD が一致 → 記録の PR を `--method` の方式でマージ。PR の読み取りは `_merge_with` の 1 回だけ | 判定の後に作業場所でコミットした → `refused`。判定の後に PR の宛先を変えた → `_preflight` が `refused`（方式・キューを選ぶ前）。同じブランチから別の宛先にも PR がある → 記録の番号の PR だけを読む | 記録が無い・空 → 2・`precondition`。`--reviewed` と同時 → `invalid` |
| `ci merged --judged` | 記録の PR がマージ済みで宛先・head が記録と同じ → 0 | 同じブランチの別の宛先の PR がマージ済みで、記録の PR は開いている → 1（本体へ進む） | 記録が無い・空 → 1 |
| `GitHub._preflight` | `expect_base`・`expect_head_ref` が `pull` と同じ → None | 省けば今と同じ | 宛先が違う → マージ済みでも `refused` |
| `merge-gate`（`fix`） | `refused` の後に承認 → 判定した PR と head でマージ | 承認の後に head が進んだ → マージが `refused` | 却下 → `end`（マージしない） |

### 判定の本体の `fix` に関わる部分（詳細設計の「判定を打つ条件と省く条件」から）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Target` | `records/mvv_gate.py` | 新設 | `NamedTuple(repo: str, pr: int, base: str, head: str)` | 判定した PR の識別情報。`fix` では CI が確かめた PR の識別情報（`ci.json` の `items` の `kind: "pr"`）にも使う |
| `Gated` | `records/mvv_gate.py` | 新設 | `NamedTuple(outcome: dict[str, Any], target: Optional[Target])` | `gate` の返り値。`outcome` は今の `outputs.mvv_gate`。`target` は (1) で読んだ PR の識別情報（`Target(hub.repo, 番号, pull.base_ref, pull.head_sha)`）で、PR を読む前に止まれば None。CLI は `target` をそのまま `--judged-out` へ書き、PR を探し直さない |
| `gate` | `records/mvv_gate.py` | 変更 | `gate(root, gate_name, inputs, hub, ask=llm.ask, expect: Optional[Target] = None) -> Gated` | 下の手順。`expect` は `fix` で CI が確かめた PR |
| `_judged` | `records/mvv_gate.py` | 変更 | `_judged(root, gate_name, inputs, open_hub, ask, expect) -> Gated` | 下の手順 |
| `pr_number` | `records/mvv_gate.py` | 変更 | シグネチャは同じ | `design` は `inputs["worktree"]`（文字列のとき。無ければ `root`）のブランチの開いた PR。`fix` はブランチで探さず `expect.pr`（CI が確かめた PR の番号）を返す。`expect` が無い・`expect.repo` が `hub.repo` と違う・`expect.base` が `inputs["base"]` と違えば `GateError`（「CI が確かめた PR が判定する PR と違う」。CI と判定とマージで同じ PR を使う）。`release` は今のまま（文脈の `pr`）。文脈の `design_pr` は読まない（プランの経路の入力に無いので、両入口で同じ PR を選ぶため） |

| 段階 | 失敗 | 検出する例外・条件 | `verdict` | `why` に足す理由 | コメント | `design`・`release` | `fix` の原因 | 再試行 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (1) | CI が確かめた PR と、読んだ PR の head か宛先が違う | `expect.head != pull.head_sha`・`expect.base != pull.base_ref` | null | 「CI が確かめた #<n>（<base> 宛て・<a>）と PR の今（<base'> 宛て・<b>）が違う」 | 書かない | —（`fix` だけ） | `precondition` | プランを `ci` から打ち直す |

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `pr_number` | 開いた PR が無い（`design`）・CI が確かめた PR が無いか判定の対象と合わない（`fix`） | PR を出していない・`ci.json` の PR のリポジトリか宛先が `--base`・作業場所の origin と違う | `GateError` | `verdict: null`・`head` 無し・コメント無し | `design`・`release` は `ok` で人へ問う。`fix` は `stopped`（`precondition`） | PR を整えて打ち直す |


## 起票者の推定

| 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- |
| 判定を CI の後・マージの前に置く | #3959 の設計の決定 3（CI の直しで head が進むため） | 受け入れ条件 1（置く位置は設計で決める） |
| CI が確かめた PR・判定した PR・マージする PR をリポジトリ・番号・宛先・head で同じにする | #4093 の cross-review の指摘 r1-2・r2-3・r2-8・r3-2・r3-7 | 受け入れ条件 2 |
| マージの済んだかの確かめを、別の宛先の PR のマージ済みで成功させない | 指摘 r3-3 | 受け入れ条件 2（マージする PR が同じであることに含める） |
| マージの方式とキューを選ぶ宛先と、照合した宛先を同じにする | 指摘 r3-4 | 受け入れ条件 2 |
| 進めない判定の後に、人が承認してマージへ進める経路を持つ | 指摘 r1-16 | 受け入れ条件 3 |
| #3959 の実装の後に実装する | conductor の分割の判断（2026-11-22） | 前提 1 |

## リクエスト（原文）

> MVV 判定は常に実施してください

> normal でも即時修正でも必要です

書き手: 利用者・2026-11-21（#3959 の会話）

> #4104 は #3959 の設計（PR https://github.com/devbasex/ai-plugins/pull/4093 、`issues/issue-3959-*`、マージ済み）から分けた課題。本文に、分ける前の設計と cross-review の指摘（r1-2・r2-3・r2-8・r3-2・r3-3・r3-4・r3-7・r1-16）が写してある。指摘は「直すたびに ndf ci watch・ndf ci merge・プランの部品まで変更が広がった」もの。範囲を広げすぎない最小の形を探し、広がる部分は決定で切る
> 根拠は MVV 版 11（`/work/ai-plugins/.ndf/mvv.md`）で引く。普及の段階までは後方互換は要らない（利用者の方針 2026-11-16）

書き手: AI の起動指示（計画 p12 のレーン lcrfast の conductor）・2026-11-22。「普及の段階までは後方互換は要らない」は利用者の方針（2026-11-16）の引用

