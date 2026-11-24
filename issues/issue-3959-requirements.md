# #3959: MVV 判定: 進め方 normal では承認ゲートの手前で MVV と突き合わせず、設計者の引いた Value とバージョンも確かめない

正は課題の本文（#3959）で、この文書はそのコピーである。本文から作り直す。手で直さない。

承認ゲート（設計・リリース）の手前で、進め方（`normal`・`auto`・`fast`）によらず MVV 判定を行う。`normal` では判定の結果を人へ渡す材料として承認資料と PR のコメントに載せ、承認資料が引く MVV のバージョンと項目の番号を機械で確かめる。#4055（プランの外で MVV 判定を打てない）をこの Issue にまとめる。即時修正のマージの前の判定は #4104 へ、リリース承認ゲートで保留を読む PR の範囲は #4105 へ分けた（2026-11-22、conductor の判断）。

## 何が起きたか

承認ゲートの手前で MVV と突き合わせる確かめが、進め方 `normal` では行われていない。2026-11-21 の計画 p9（`normal`）で届いた設計承認の資料 3 件（ls3-spec #3412・lsplit #3702・lz13-deps #3370 #3691）は、どれも「MVV 判定: 判定していない（`normal` は承認ゲートを MVV 判定で省かない）」と書き、`mvv_gate` は `{"gate": "design", "skip": false, "verdict": null}` だった。

- `development-workflow` の `references/pace.md` の「MVV 判定」の順 2 は、進め方が `fast` か `auto` でなければ「判定しない」と決めている。判定は承認ゲートを省くための材料としてだけ使われ、`normal` では人へ渡す材料にもならない
- 人が見る MVV の材料は、設計者が書いた「目指す姿の根拠: Value N（MVV バージョン N）」だけで、自己申告である。これを確かめる検査が無い。ls3-spec の資料は決定 10 件すべてを「MVV バージョン 1」と書いたが、今の MVV はバージョン 9 である（`design` の `references/decisions.md` の例が「MVV バージョン 1」を使い、それをコピーしたと見られる）。引いた項目が今のバージョンで何を指すかも確かめられない（バージョンは当時のもの。2026-11-22 にバージョン 11 へ改訂された）
- 即時修正のプラン（`contracts/templates/fix.json`。テスト → push → PR → 衝突の確かめ → CI → マージ）は、MVV 判定を通らずにマージする
- `ndf records mvv gate` は工程の入力を環境変数 `NDF_STAGE_INPUT` からしか読まない。プランのテンプレートが使えず supervisor が手で回すフェーズ（#3908）では、conductor が `NDF_STAGE_INPUT` を手で組んで打つしかない。2026-11-22 の #3995 のスプリントでは `NDF_STAGE_INPUT='{"pace":"normal","mode":"standard","issues":[3995],"design_pr":4041}' ndf records mvv gate --gate design` と打った（#4055）
- 工程の入力のコントラクト（`contracts/workflow/stage-io.schema.json` の `stage-in`）は `{"context": {...}}` の形を定めるが、プランが `NDF_STAGE_INPUT` に入れるのは文脈のキーを平らに並べた object（`progress/invoke.py` の `resolve_inputs` の結果）で、`stage-in` で確かめるコードは無い。#3687 の設計（2026-11-23）がこの食い違いを見つけた。コントラクトどおりに組んだ `NDF_STAGE_INPUT='{"context":{"pace":"normal","mode":"standard","issues":[3959]}}' ndf records mvv gate --gate design` は、`status: ok`・終了コード 0 で「承認ゲートを人へ問う: モード None は承認ゲート design を持たない」を返し、判定を打たない（11.0.22 で実測）

2026-11-22 の #4076 の設計承認ゲートで、利用者は「承認ゲートがレビューの記録（レッドラインの指摘・修正担当の見送り）を読み、それが残る PR では `fast`・`auto` でも承認ゲートを省かない仕組み」をこの Issue へ移すと選んだ（#4076 の設計 PR https://github.com/devbasex/ai-plugins/pull/4077 ）。

## 種類

機能追加（`pace.md` の「MVV 判定」は `normal` では判定しないことをコントラクトとしている。進め方によらず判定する・即時修正に判定を置く・承認資料の根拠の行を確かめる・承認ゲートがレビューの記録を読む・プランの外で判定を打てる、はどれも今の SSOT に無いコントラクトである）

## 今の姿

MVV（プロジェクトの目指す姿を書いた `.ndf/mvv.md`。Mission・Vision・Value の番号つきの項目と、必ず人の承認が要る操作の `P<番号>` を持ち、利用者の承認の記録 `.ndf/mvv.json` がバージョンを持つ。共通原則の操作は `C<番号>`）に PR が従うかを LLM に比較させて確かめるのが MVV 判定である。MVV 判定の手順は [pace.md の「MVV 判定」](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/skills/development-workflow/references/pace.md#mvv-判定) に、承認資料の「MVV 判定」の欄は [approval-request.md](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/skills/development-workflow/references/approval-request.md) にある。

| 場面 | 今の振る舞い |
| --- | --- |
| 設計承認ゲート・リリース承認ゲートの手前（`normal`） | `ndf records mvv gate` は判定せず、`verdict: null` と「進め方 normal は承認ゲートを MVV 判定で省かない」を出す。PR のコメントも書かない |
| 同（`fast`・`auto`） | 機械の検査 6 つのどれかが外れると LLM を呼ばずに人へ問う。通れば判定し、「従う」でレッドラインが空なら `service:mvv` で省く |
| 同（`fast`・`auto`）で cross-review がレッドラインの指摘を残した PR | 判定はレビューの記録を読まないので、省かれうる（#4076 の設計の代償。#4076 の cross-review の指摘 r1-5・r9-1） |
| 即時修正のマージの前 | 判定しない |
| 承認資料の根拠の行 | 確かめない。PR の差分の根拠の行は #4076 の実装の後に cross-review が確かめる |
| プランの外（supervisor が回すフェーズ） | `NDF_STAGE_INPUT` を手で組まないと打てない |
| 工程の入力の形 | コントラクトの `stage-in` は `{"context": {...}}`、プランが渡すのは平らな object。`ndf records mvv gate` は入力の形を確かめず、形の違う入力でも止まらない |

## 何を足すか・変えるか

| | 今 | 足した後 |
| --- | --- | --- |
| 承認ゲートの手前の判定 | `fast`・`auto` だけ。機械の検査が外れると判定しない | 進め方によらず、承認済みの MVV があり PR の材料がそろえば判定する。機械の検査は「省くか」だけを決める |
| `normal` の承認ゲート | 判定の材料が無い | 判定・理由・レッドラインが PR のコメントと承認資料に載る。省かない |
| 承認ゲートを省く条件 | 判定と機械の検査 6 つ | 設計承認ゲートは、加えて設計 PR の cross-review の記録に、直っていないレッドラインと、利用者の判断が要る未解消の見送りが無いこと（記録を読めなければ省かない）。リリース承認ゲートは保留を読む範囲が決まるまで（#4105）省かない |
| 即時修正 | 判定しない | 変えない（#4104 へ分けた） |
| 承認資料の根拠の行 | 確かめない | 承認資料を出す前に、バージョンと番号を機械で確かめるコマンドを打ち、外れれば止まる |
| プランの外の判定 | `NDF_STAGE_INPUT` を手で組む | ステートマシンの ID を渡せば、プランと同じ入力で判定する |
| 工程の入力の形 | コントラクトとプランが渡す形が違い、判定は形を確かめない | コントラクトがプランの渡す平らな形を定め、判定は `NDF_STAGE_INPUT` をプランと同じ規則（工程の入力のキーと文脈のスキーマ）で確かめ、外れれば `invalid` で止まる |

## 今の利用者への影響と互換

- `normal` のスプリントで、承認ゲートの手前に LLM の判定が 1 回増え、PR にコメントが 1 件増える
- `fast`・`auto` で、機械の検査が外れたときも判定を打つので、LLM の呼び出しが増えうる（同じ材料の「従う」は記録を使い直す）
- `fast`・`auto` でも、リリース承認ゲートは #4105 まで人が答える（MVV 判定で省かない）
- 承認済みの MVV が無いリポジトリでは、承認ゲートの出力の形が今と変わらない（前提 2）

## なぜ困るか

- `normal` の承認ゲートでは、MVV に従っているかを見る者が人だけになり、その材料は設計者の自己申告である。MVV の Value が判断の基準として働いているかが承認の場で分からない
- 覆しの記録（`mvv-signals.jsonl`）は判定と人の回答の食い違いから作るため、`normal` では判定が無く覆しも数えられない。MVV の改訂の兆候が `normal` では集まらない
- 誤ったバージョンの引用がそのまま確定仕様へ載る
- `fast`・`auto` では、cross-review がレッドラインに当たると書いた指摘が残っても、承認ゲートが `service:mvv` で自動で省かれる（#4076 の設計が残した critical の危険）
- 即時修正は MVV と突き合わされずに main へ入る
- プランの外のフェーズで判定を打つたびに、入力の JSON を手で組む

## 目的

- どの進め方でも、承認ゲートの手前で MVV 判定が行われ、その結果を人が承認の場で読める
- 承認ゲートを省いてよいのは今どおり `fast`・`auto` だけで、直っていないレッドラインの指摘か、利用者の判断が要る未解消の見送りが残る PR では省かない
- 承認資料が引く MVV のバージョンと項目の番号が、今の承認の記録と一致することを機械で確かめる

## 対象範囲

含む:

- `ndf records mvv gate` の判定を進め方によらず打つこと（設計承認ゲート・リリース承認ゲート）と、`normal` の判定の結果を PR のコメントと承認資料に載せること
- 承認ゲートの省略の条件に cross-review の記録（`mvv-validation.jsonl` の `mvv-review` の行）を足すことと、その保留を承認資料に載せること（#4076 から移した仕組み。cross-review が承認ゲートの判定に要る記録を書けなかったら止まることを含む）
- 承認資料の根拠の行のバージョンと番号の確かめ
- プランの外で `ndf records mvv gate` を打つエントリポイント（#4055）
- 工程の入力のコントラクト（`stage-in`）をプランが渡す形に直すことと、`ndf records mvv gate` が `NDF_STAGE_INPUT` の形を確かめること（#3687 の設計で見つけた食い違い）
- `pace.md`・`approval-request.md`・`agent-layers.md` の手順の書き換え

含まない:

- MVV のコピー（`ndf records mvv current`）と根拠の行の確かめの関数（`citation_errors`・`fenced_lines`）を作ること。#4076 が作り、この Issue はそれを使う
- PR の差分の根拠の行の確かめ（#4076 の cross-review が持つ）
- `design` の `references/decisions.md` の例のバージョンを直すこと（#4076 の詳細設計の「手順書」が持つ）。この Issue の受け入れ条件 7 は、#4076 の実装の後に成り立つことを確かめるだけである
- 即時修正のプランのマージの前の MVV 判定（#4104 へ分けた）
- リリース承認ゲートで保留を読む PR の範囲（#4105 へ分けた。それまでリリース承認ゲートは `fast`・`auto` でも省かない）
- 承認ゲートを省いた回答（`service:mvv`）を、消費の時点で判定した PR・宛先・宛先の先端と照合すること（#4106 へ分けた。この Issue の照合は今のまま head だけ）
- MVV 判定で通る設計を増やす判定の指示文（#2912・#3017）
- MVV の本文の改訂（利用者の承認で行う）

## 前提

- 前提 1: #4076 の実装（MVV のコピー・根拠の行の確かめの関数・cross-review の `mvv-review` の記録。#4090・#4099 の直しを含む）が main へ入った後に、この Issue を実装する。2026-11-23 時点で #4076 はクローズされているが、その実装（`current`・`citation_errors`・`record_review` など）は main に無い
- 前提 2: 承認済みの MVV が無いリポジトリ（状態 `absent`・`unapproved`）では、承認ゲートの判定は今どおり `verdict: null` で人へ問う
- 前提 3: 「今の承認の記録のバージョン」は、PR の宛先のブランチ（ベースブランチ）の `.ndf/mvv.json` の最後のバージョンである（#4076 の要求仕様の前提 1 と同じ）

## 受け入れ条件

- [ ] 1. `normal` のスプリントで `ndf records mvv gate --gate <design か release>` を打つと、承認済みの MVV があり PR の材料がそろえば、LLM の判定（従う・従わない・判定できない、理由、レッドライン）が出力の `mvv_gate.verdict` に載り、`skip` は偽のままで、判定の記録が PR のコメントに載る
- [ ] 2. `fast`・`auto` で、MVV が承認済みでハッシュが一致するという前提の検査を除く機械の検査（スプリントの参照・モード・レッドラインのパス・MVV の設定の変更・出し直しの指摘への対応表）が外れても判定は打たれ、`skip` は偽で、外れた理由が `why` と PR のコメントに載る
- [ ] 3. 承認資料の「MVV 判定」の欄に、`normal` でも判定と理由とレッドラインが載る
- [ ] 4. `fast`・`auto` で、設計 PR の `mvv-review` の行に、直っていないレッドラインの指摘（後に同じキーの `fixed` の行が無い `redline`）か、利用者の判断が要る今のバージョンの未解消の見送り（見送りの区分が `needs_user` か区分の無い `deferred`）があると、設計承認ゲートは `service:mvv` で省かれず人へ問い、その保留（PR・項目・理由）が承認資料の「MVV 判定」の欄に載る。記録を読めない・読めない行があるときも省かない。後に同じキーの `fixed` があるレッドラインと、区分が `filed`（範囲外を起票した）・`not_applicable`（基準に当たらない・意図どおり）の見送りでは、保留にならない。リリース承認ゲートは保留を読む PR の範囲が決まるまで（#4105）、`fast`・`auto` でも省かない
- [ ] 5. cross-review が承認ゲートの判定に要る記録（`redline`・`deferred` の行）を書けなかったら、その起動は収束にせず `STOPPED`・`cause.kind: mvv_record_failed` で終わる
- ~~[ ] 6. 即時修正のプランがマージの前に MVV 判定のステップを持ち、判定が「従う」でレッドラインが空で、機械の検査が外れていないときだけマージへ進む。ほかは止めて理由を添えて人へ戻す。承認済みの MVV が無いリポジトリでは今どおりマージへ進む~~ → #4104 へ移した（2026-11-22、conductor の判断）
- [ ] 7. 承認資料が引く「MVV バージョン N」が今の承認の記録のバージョンと違うか、引いた Value・P・C の番号がそのバージョンに実在しなければ、承認資料を組む手順が出す前に打つ確かめのコマンドが止め（`refused`）、承認資料は確かめた結果の 1 行を持つ。`design` の `references/decisions.md` の例が「MVV バージョン 1」でなく今のバージョンを読んで書く形になっている（#4076 の実装で満たす）
- [ ] 8. `NDF_STAGE_INPUT` が無いところで `ndf records mvv gate --gate <design か release> --machine <ステートマシンの ID>` を打つと、プランが渡すのと同じ入力で判定し、同じ形の出力を返す（#4055）
- [ ] 9. `NDF_STAGE_INPUT` の JSON が工程の入力の形（判定のステップを持つ工程の入力のキーを平らに持ち、文脈のスキーマに合う）でなければ、`ndf records mvv gate` は判定せずに `stopped`・`invalid`（キーが足りなければ `input_missing`）で止まる（例: `{"context": {...}}` の形）。コントラクトの `stage-in` は、プランが `NDF_STAGE_INPUT` に入れる平らな形を定める

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | LLM の判定は承認ゲート 1 回につき 1 回。同じ材料の「従う」は今の記録の使い直しで LLM を呼ばない |
| 運用・保守性 | 承認ゲートの省略の判定と承認資料の保留は同じ関数の結果を使う（2 か所で同じ規則を書かない） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`ndf records mvv gate` に `--machine` が足され、`ndf records mvv cite` が足される。`NDF_STAGE_INPUT` の形が合わない入力は `invalid` で止まる。コントラクトの `stage-in` の形を、プランが渡している形に直す。プランが渡す入力と出力の形は変わらない |
| データ | `mvv-validation.jsonl` の読み手が足される。`mvv-review` の `deferred` の行に、任意のキー `defer`（見送りの区分: `needs_user`・`filed`・`not_applicable`）が足される。区分の無い行は保留になる |
| 既存の振る舞い | `normal` の承認ゲートで判定とコメントが増える。`fast`・`auto` でレビューの記録が残る設計 PR は省かれなくなり、リリース承認ゲートは #4105 まで省かれなくなる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | PR の CI（`.ndf/project.json` の `test.ci.only` が真なので手元では打たない） |
| 静的解析・型検査 | PR の CI |
| 手動確認 | 受け入れ条件 1・3: リリースの後の最初の `normal` のスプリントの設計承認ゲートで、PR のコメントと承認資料に判定が載るかを見る（LLM の出力に依るため CI では判定の中身を確かめない） |

## リリース後の確認の計画

| 条件 | 経路を動かすバージョン | 確かめる時点 |
| --- | --- | --- |
| 1・3. `normal` の承認ゲートで判定が PR のコメントと承認資料に載る | 配ったバージョン | このリリースの後の最初の `normal` のスプリントの設計承認ゲート |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 即時修正で「従う」でないときは止めて人へ戻す | 共通原則の AI の行動は「進める / 止めて人へ戻す」の 2 つ | #4104 へ移した |
| S2 | 判定の費用（LLM の 1 回）は承認ゲート 1 回につき 1 回で、同じ材料の再判定は記録を使い直す今の仕組みで抑えられる | pace.md の「同じ材料への『従う』の判定は記録を使い直し、LLM を呼ばない」 | 非機能の条件 |
| S3 | バージョンと番号の検査は、設計文書と承認資料の両方に掛ける | ls3-spec の資料の 51〜60 行 | 受け入れ条件 7。設計文書（PR の差分）は #4076 の cross-review が持ち、この Issue は承認資料に掛ける |
| S4 | #4055 はステートマシンの ID か引数で入力を渡せるようにする | #4055 の本文の「期待する結果」 | 受け入れ条件 8。エントリポイントはステートマシンの ID だけにする（設計の決定で理由を書く） |

## リクエスト（原文）

> MVV 判定は常に実施してください
> normal でも即時修正でも必要です

（書き込み元: 利用者、日付: 2026-11-21）

> 進め方（`normal`・`auto`・`fast`）によらず、設計承認ゲートとリリース承認ゲートの手前で MVV 判定を行う。承認ゲートを省いてよいのは今どおり `fast`・`auto` だけで、`normal` では判定の結果を人へ渡す材料として承認資料と PR のコメントに載せる

（書き込み元: #3959 の起票者が利用者の指示をまとめた方針、日付: 2026-11-21）

## #4076 の設計から移した仕組み（2026-11-22）

> 2026-11-23 の追記: #3959 の設計承認ゲートで、利用者が保留の規則を絞った（下の「レッドライン」「見送り」の規則と例は絞った後の形に直した）。今の規則では、redline の行が 1 行でもあるキーは直した後もずっと保留になり、今のバージョンの deferred が 1 件でも残れば保留になる。cross-review はほとんどの PR で何かを見送るので、事実上どの PR も承認ゲートを省けないためである。保留にするのは、直っていないレッドラインと、利用者の判断が要る見送りだけで、記録を読めないときは今どおり保留にする

> 2026-11-22 の追記: 下の規則のうち、リリース承認ゲートでスプリントの単位の PR の行を読む部分は #4105 が持つ。それまでリリース承認ゲートは `fast`・`auto` でも省かない（受け入れ条件 4）。

承認ゲートがレビューの記録（レッドラインの指摘・修正担当の見送り）を読み、それが残る PR では `fast`・`auto` でも承認ゲートを `service:mvv` で省かない仕組みを、#4076 の設計 PR（https://github.com/devbasex/ai-plugins/pull/4077 ）からこの Issue へ移した。#4076 の設計承認ゲートで、利用者が「仕組みを #3959 へ移す」を選んだ。承認ゲートの省略の条件と承認資料の「MVV 判定」の欄はこの Issue が持つためである。

- **残っている危険（critical）**: `fast`・`auto` では、cross-review がレッドライン（`C<番号>`・`P<番号>`）に当たると書いた指摘が残っても、承認ゲートが `service:mvv` で自動で省かれる。今の判定（`ndf.progress.gate.checks.refusals`）は cross-review の結果を読まないためである。この Issue が直すまで残る
- **前提（#4076 が作るもの）**: #4076 の cross-review は、MVV の指摘ごとに `mvv-validation.jsonl` へ種類 `mvv-review` の行を書く。行は `repo`・`pr`・`head`・`key`（指摘のキー）・`id`・`item`（項目。レッドラインは `P1・C7` のように `・` でつなぐ）・`outcome`（`fixed`・`deferred`・`unresolved`・`redline`）・`version`・`sha256`・`at` を持つ。`deferred` は `reason`、`redline` は `text` を持つ。レッドラインの指摘は修正担当へ渡さず、収束も止めない
- **関わる cross-review の指摘**: r1-5（critical。この仕組みを足した元）、r6-4・r6-5・r7-1〜r7-4、r8-1〜r8-8（この仕組みの規則の穴）。指摘と直しの記録は https://github.com/devbasex/ai-plugins/pull/4077#issuecomment-7075168516 にある
- **移したバージョン**: 下の規則は、#4077 のコミット `c5ac9fced7777d68e5303d5cb4d80a241dce3708` の設計文書（`issues/issue-4076-design.md`・`issues/issue-4076-design-detail.md`）からコピーした。#4076 の設計文書からは外した

### 守る振る舞い

- 人が見ることを `fast`・`auto` でも成り立たせるため、承認ゲートの省略の判定（`may_skip` の前の機械の検査 `refusals`）に検査を 1 つ足す。設計承認ゲートは設計 PR の、リリース承認ゲートはスプリントの単位の PR ごとの、`mvv-review` の行を head によらず読み、指摘のキーごとに、`redline` の行が 1 行でもあるもの（記録の上では外さず、人が承認した後のゲートでも問う）と、今のバージョンの `deferred` の後に同じバージョンの `fixed` が無いものがあれば省かない（理由に項目と PR を並べる）。記録を読めない・読めない行があるときも省かない（記録の後に head が進んだ PR や、別の起動が別の指摘だけを書いた PR で、残ったレッドラインと見送りを見落とさない側へ倒す）。`normal` は今どおり人が答える

### 決まった規則（#4077 の詳細設計から）

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `review_rows` | `records/mvv.py` | 新設 | `review_rows(root: Path) -> tuple[list[dict[str, Any]], int]` | `mvv-validation.jsonl` を `jsonl.read_rows` で読み、`mvv-review` の行と、読めなかった行の数を返す。ファイルを読めない `OSError` は呼び出し元へ返す |
| `review_holds` | `records/mvv_decide.py` | 新設 | `review_holds(rows: Iterable[Mapping[str, Any]], broken: int, repo: str, prs: Iterable[int], mvv: tuple[str, str]) -> list[str]` | 対象の PR ごとに、下の規則で保留になる指摘を「PR #N の <項目>（<終わり方>）」の形で返す。`broken` が 1 以上なら「mvv-validation.jsonl に読めない行が N 件ある」も返す。`mvv` はゲートが使う MVV の（`version`・`sha256`） |
| `check_review_holds` | `progress/gate/checks.py` | 新設 | `check_review_holds(f: MvvFacts) -> Optional[str]` | `f.review_holds` が空でなければ省かない理由を返す |
| `MvvFacts` | `progress/gate/checks.py` | 変更 | 欄 `review_holds: tuple[str, ...] = ()` を足す | — |

- `review_holds`: 各 PR の行を head によらずすべて読み、指摘のキーごとに記録の順（下の「最後」の決め方）に並べて、次の 2 つの規則で保留を決める。head で行を選ばない（記録の後に head が進んだ PR・別の `--state-dir` の起動が別のキーだけを書いた head でも、前の head の保留を落とさない）。承認依頼の「MVV 判定」の行も同じ規則の結果を使う
  - レッドライン: キーに `redline` の行があり、そのキーの最後の `redline` の行より後に同じキーの `fixed` の行が無ければ（どの head・どのバージョンでも）、そのキーは `redline` で保留する。後の `fixed` があれば直ったとみなして外す。項目はそのキーの `redline` の行の `item` を `・` で分けた和を出た順に並べ、本文は最後の `redline` の行の `text` を使う
  - 見送り: 保留していないキーで、バージョンが `mvv` と同じ `deferred` の行のうち、見送りの区分 `defer` が `needs_user`（利用者の判断が要る）か区分の無い行があり、それより後に同じキー・同じバージョンの `fixed` の行が無ければ、`deferred` で保留する。区分が `filed`（範囲外を起票した）・`not_applicable`（基準に当たらない・意図どおり）の見送りは保留にしない（理由は最後の `deferred` の行の `reason`）。外すのは同じキー・同じバージョンの後の `fixed` だけで、後の `unresolved`・別のキーの行・行が無いことでは外さない。古いバージョンの `deferred` は数えず、古いバージョンの `fixed` も解除に使わない。見送りの区分 `defer` は、修正担当が見送るときに `needs_user`・`filed`・`not_applicable` のどれかを書き、cross-review のジャーナルが理由とともに持ち、`record_review` が `mvv-review` の `deferred` の行に書く。区分が無い・知らない値の行は、記録を読めないときと同じく保留にする
  - 例: 同じ head・同じキーが `deferred`（`needs_user`）→ `fixed` → `deferred`（`needs_user`）と記録されたら `deferred` で保留する。`deferred`（`filed`）だけなら保留にしない。`redline` の後に同じキーの `fixed` があれば外し、`fixed` の後にまた `redline` が記録されたら保留する
- 人の承認との関係: ゲートで人が承認しても記録を書き換えず、承認を表す行も作らない。その PR を対象にする後のゲート（設計承認の後のリリース承認・同じゲートの打ち直し）は同じ保留を返し、`fast`・`auto` でも人に問う（必ず人の承認が要る操作を含む PR は、ゲートごとに人が見る。余分な人の承認は許す）。保留はその PR を対象にするゲートにだけ掛かる
- 「最後」の決め方: キーごとの行の順は `at` の古い順で、`at` が同じ（`jsonl.now()` は秒精度）ときは `mvv-validation.jsonl` の追記順に従う。`record_review` の重ねない判定もこの順の最後の行を使う
- 記録の欠落: `record_review` が `redline`・`deferred` の行（承認ゲートの判定に要る行）を書けなかったら、その起動は収束にせず止める（`STOPPED`・`cause.kind: mvv_record_failed`）。`fixed`・`unresolved` の行だけが書けなかったときは注意に留める
- 呼ぶ順序と最終の判定: `_summary` は要約を書いた後に、PR に届いた最後のコミット（`run.sent`。修正の push に失敗したときも、手元の HEAD は使わない）を `head` にして `record_review` を呼び、承認ゲートの判定に要る行を書けなかったら `Outcome.mvv_record_failed` に書けなかった行（キーと終わり方）を入れる（判定は作らない）。書けなかった原因は、例外の文が保存先の絶対パスを含むので、状態ファイルの `mvv_record_error` だけに残し、`warnings` には `_summary` が固定文を足す。`cli.cmd_run` は今の `fin.finish(got.review, cfg.handed)` を `fin.finish(got.review, cfg.handed, record_failed=got.mvv_record_failed)` に変え、この 1 回の呼び出しの `Verdict` だけを最終の判定にする（`_summary` の中で判定を作って CLI が作り直す二重の経路を作らない）。`finish` は `handed` を含む普段の判定を終えた後に、`record_failed` があれば status を `STOPPED`・kind を `mvv_record_failed` にし、本来の kind と要約を `summary` の後ろに足し、`gaps`・`decisions` はそのままにし、生の原因を `summary` にも `warnings` にも足さない（`mvv_unresolved` を含むどの終わり方より優先する。記録が無いとゲートが PR を省けるため）。この `Verdict` を今の `_emit_verdict` が結果 JSON と終了コードにする。`ndf cross-review finish`（記録を書かない）は `record_failed` を渡さない。保存先を直して打ち直すと、`record_review` が欠けた行だけを書き、書ければ `finish` の普段の判定（`mvv_unresolved` なら `STOPPED`、欠け無しなら収束）がそのまま終わり方になる
- 承認ゲートでの使い方（`mvv_gate._judged`）: 設計承認ゲートは設計 PR の番号、リリース承認ゲートはスプリントの状態の単位の PR の番号を `prs` に、ゲートが判定に使う MVV のバージョンを `mvv` にして、`review_rows` の行と読めなかった行の数で `review_holds` を呼び、`MvvFacts.review_holds` に入れる。`review_rows` が `OSError` を投げたら `review_holds` を呼ばず、`MvvFacts.review_holds` を「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」の 1 件にする（読めない記録を「該当なし」と扱わない）。`refusals` の検査の並びに `check_review_holds` を足し、理由があれば `may_skip` は偽になって `service:mvv` の承認を書かない（`progress/run.py` の `_consume` は今のまま人の回答を待ち、理由は承認依頼の「MVV 判定」の行に載る）。LLM を呼ぶ前の検査なので費用は増えない

### 異常系

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_review` | `redline`・`deferred` の行を書けない | 保存先の権限 | `OSError` | 収束にせず止める（承認ゲートが記録の無い PR を省けるため） | 終了コード `STOPPED`・`cause.kind: mvv_record_failed`、`warnings` に固定文（原因は状態ファイルの `mvv_record_error`） | 保存先を直して `ndf cross-review run` を打ち直す（終わりにまた書く。重ねない） |
| `review_rows` | `mvv-validation.jsonl` を読めない | 権限・容量・壊れた符号 | `OSError`（`mvv_gate._judged` が受ける） | 「該当なし」にせず、読めない理由を `MvvFacts.review_holds` の 1 件にする | ゲートが省かれず、承認依頼の「MVV 判定」の行に理由 | 原因を直せば次のゲートの判定で読み直す |
| `review_holds` | 読めない行がある | 書き込みの途中の中断・手で壊した | `jsonl.read_rows` の読めなかった行の数 | どの repo・PR の行か分からないので、その数を保留の 1 件にする | 同上（「mvv-validation.jsonl に読めない行が N 件ある」） | 壊れた行を直すか消せば次のゲートの判定で外れる |

### テストで落とす壊し方

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_holds` | 直っていない `redline`・区分が `needs_user` の `deferred` を返す。`item: P1・C7` の `redline` → `PR #N の P1・C7（redline）` | `fixed` だけ → 空。`redline` の後に同じキーの `fixed`（別の head・別の `--state-dir` でも）→ 外す。`redline` → `fixed` → `redline` → 保留。同じキーに `P1` と後の `P1・C7` → `P1・C7` の 1 件。区分が `filed`・`not_applicable` の `deferred` だけ → 空。区分の無い `deferred`・知らない区分 → 保留。同じ `at` の `deferred` と `fixed` → 追記順で後の方。head A のキー K の `deferred`（`needs_user`）の後に head B で別のキー L の `fixed` だけ → K を返す。`deferred` の後の `unresolved` → `deferred` のまま。古いバージョンの `deferred` だけ → 空 | 別の repo の行は読まない。`broken` が 1 以上 → 読めない行の件数を 1 件返す |
| `_summary`・`finish`・`cmd_run` | `record_review` が書ける → `mvv_record_failed` が None で、`finish` の普段の判定のまま | `mvv_unresolved` と書けない `redline` が重なる → `mvv_record_failed`（`summary` に `mvv_unresolved`）。`handed` の欠けと書けない `deferred` が重なる → `mvv_record_failed` で、`handed` の欠けが `gaps` に残る。直して打ち直す → `mvv_unresolved` で止まる | `redline` を書けない → 収束の判定でも `cmd_run` の結果 JSON と終了コードが `STOPPED`・`mvv_record_failed`（CLI が `finish` を打ち直しても成功に戻らない） |
| `mvv_gate._judged` | 記録が読める → `review_holds` の結果 | 記録のファイルが無い → 空 | `review_rows` の `OSError` → 読めない理由の 1 件で `may_skip` が偽 |
| `check_review_holds` | `review_holds` があれば理由を返し `may_skip` が偽 | 空 → None | — |

- 受け入れ条件の形: 対象の PR の `mvv-review` に、直っていない `redline` か、区分が `needs_user`（または区分の無い）未解消の `deferred` の行があると、`refusals` が理由を返し、承認ゲートが `service:mvv` で省かれない（壊し方: `fast`・`auto` で省かれる。逆の壊し方: 直った `redline` や `filed`・`not_applicable` の見送りだけの PR が省かれない）

### 承認資料

| `skills/development-workflow/references/approval-request.md` | 「MVV 判定」の行の出所に、`review_holds` と同じ規則（その PR の全 head の行をキーごとに `at` の順、同じなら追記順に並べる。`redline` は後に同じキーの `fixed` が無いもの。`deferred` はゲートの MVV のバージョンと同じもので、区分が `needs_user` か区分が無く、後に同じキー・同じバージョンの `fixed` が無いもの。読めない記録・読めない行はその旨）で選んだ保留を足す。設計承認ゲートは設計 PR の行、リリース承認ゲートはスプリントの単位の PR の行を、項目と、`deferred` は最後の `deferred` の行の `reason`、`redline` は最後の `redline` の行の `text` とともに載せる |

## 由来

issue #3692（計画 p9 の 11.0.x の実地の e2e 検証）。2026-11-21 に計画 p9 のオーガナイザーが、設計承認の資料 3 件と利用者の問いから見つけた。2026-11-22 に #4076 の設計承認ゲートで、承認ゲートがレビューの記録を読む仕組みがこの Issue へ移った（移したバージョンは #4077 のコミット `c5ac9fced7777d68e5303d5cb4d80a241dce3708`、関わる cross-review の指摘と直しの記録は https://github.com/devbasex/ai-plugins/pull/4077#issuecomment-7075168516 ）。同じ日に #4055（プランの外で打てない）をこの Issue にまとめた（設計の決定 6）。設計 PR #4093 の cross-review で指摘が続いた 2 つの仕組みを、conductor の判断で #4104（即時修正の判定）と #4105（リリースの保留の範囲）へ分けた（設計の決定 3・8）。続く cross-review で指摘が続いた省いた回答の照合を #4106 へ分けた（設計の決定 9）。2026-11-23 に、計画 p9 のレーン lz2-ci が #3687 の設計で見つけた工程の入力の形の食い違いを受け入れ条件 9 に足し、後の設計（#4090・#4099 の決定 16）が求める直しを設計へ入れて実装計画を作り直した。
