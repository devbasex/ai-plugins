# cross-review: 起きることを書けない重い指摘を軽い指摘として残し、ラウンドを止めない

## 概要

**cross-review の相手役（codex・kiro）は、`critical`・`major` を付ける指摘ごとに「利用者の経路で何が起きるか」の 1 文（`impact`）を書く。**
駆動はジャーナルへ入れるときに、1 文の無い `critical`・`major` と重要度の無い指摘を `minor` へ下げる。下げた指摘は修正担当へ渡らず、
ラウンドの収束を止めず、PR の記録のコメントの表に「軽い（直さずに残した）」で残る。相手役が付けた元の重要度はジャーナルの行に残る。

**例: 1 文の無い `major` の 1 件だけが出たラウンド。**

| 順 | 誰が | 何をする | 何が起きるか |
| ---: | --- | --- | --- |
| 1 | 相手役 | `{"text": "…", "severity": "major", "impact": "", …}` を返す | — |
| 2 | 駆動（`journal.enter`） | `effective_severity("major", "")` で重要度を決める | 行の `severity` は `minor`、`raised_severity` は `major` |
| 3 | 駆動 | 収束の妨げを数える | 妨げが 0 件なので修正担当を起動せず、ラウンドは収束と判定される |
| 4 | 駆動（記録のコメント） | 未解決の行を表にする | その行は重要度 `minor`・終わり方「軽い（直さずに残した）」で出る。スレッドは作らない |

同じ指摘に相手役が `"impact": "設定を読んだ利用者の PR が誤って閉じられる"` を書いていれば、行は `major` のまま修正担当へ渡り、
修正担当への指示のその指摘の行に「— 起きること: <1 文>」が添わる。

承認済みのプロジェクト MVV があるときだけ、MVV の項目を挙げた指摘（`mvv`）の重要度と扱いが上と変わる。違いは各節に書き、
仕組みの全体は [cross-review-project-mvv.md](cross-review-project-mvv.md) が正である。

この文書は、その形に**なぜしたか**（背景・決定と理由・常に成り立つ条件・境界・テスト観点）を残す。手順と表の正は次にある。

| 何を | 正本 |
| --- | --- |
| 1 ラウンドの出力の形（`impact` を含む）・指摘の基準・判定の表・状態ファイルの `findings[]` の項目 | `cross-review` の [docs/01-state-and-review.md](../../plugins/ndf/skills/cross-review/docs/01-state-and-review.md) |
| 修正担当へ渡す指示の中身（起きることの 1 文の添え書き） | `cross-review` の [docs/02-fix-and-rotation.md](../../plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md) |
| 相手役への指示の文（`impact.reviewer`）と修正担当への添え書きの名前（`impact.fixer_label`） | [contracts/data/review-criteria.json](../../plugins/ndf/contracts/data/review-criteria.json) |
| 重要度の尺度と収束の妨げになるか（`rank`・`blocking`） | [contracts/domains/severities.json](../../plugins/ndf/contracts/domains/severities.json)（この変更では変えない） |
| 取り込む重要度の規則 | [criteria_decide.py](../../plugins/ndf/src/ndf/domains/crossreview/criteria_decide.py) の `effective_severity`・`downgraded`・`heavier_raised`・`reviewer_block`・`fixer_impact` |

## 用語

「相手役」「修正担当」「指摘の基準」「ジャーナル」などの定義は用語集（[docs/glossary.md](../glossary.md) の `ndf-cross-review`）が正である。
この文書では次の 3 語を使う。

| 語 | 意味 |
| --- | --- |
| 起きることの 1 文 | 指摘の項目 `impact`。空白を除いて空でない文字列のときだけ「1 文を持つ」と読む。中身の妥当さは駆動が判定しない |
| 取り込み | 相手役の出力の指摘をジャーナルの行にすること（`journal.enter`） |
| 引き継ぎ | 前の実行が PR のスレッドと記録のコメントに残した指摘を読んでジャーナルの行にすること（`journal.carry`） |

## 背景

2026-11-22 にあるマシンの cross-review の記録（55 回の実行・225 ラウンド）を集計すると、相手役が付けた重要度は major 726・重要度なし 280・
minor 17・critical 7 だった。2 ラウンド目より後の 172 ラウンドのうち 76 ラウンドは新しい指摘が 1〜2 件だけで、それでも修正担当を起動して
次のラウンドへ進んでいた（合計 175 分）。

cross-review は `minor`・`nit` を収束の妨げにしない。しかし相手役への指示が「基準に当たる指摘は critical・major」と書き、基準に当たるもの
だけを書かせていたため、`minor` を付ける余地がほとんど無かった。重要度の無い指摘は止める側に倒していたので、280 件もすべてラウンドを止めていた。

重要度を「何が起きるか」（影響）で決め、影響を示せない指摘を重い側に数えない考え方は、SonarQube の重要度の定義（品質への影響）と
CodeRabbit の重要度（critical・major に影響の大きさを結ぶ）に倣った。止める指摘に根拠を添えさせ、確かでない指摘を止めない側へ寄せる形は
Conventional Comments の `(blocking)`・`(non-blocking)` と Google のレビューの手引きの `Nit`・`Optional` に倣った。

## 対象範囲

- **働く範囲**: `ndf cross-review run` を打つすべてのリポジトリとモード。プロジェクトごとの設定は無い（規則と指示の文は NDF の契約が持つ）
- **対象外**:
  - 指摘の基準の項目そのもの
  - `severities.json` の尺度と `blocking` の値
  - `pr-review`・`fix`・`cross-refactoring` の重要度の扱い（同じ尺度を読むが、相手役の出力の形を持たない）
  - 引き継いだ指摘の重要度の付け直し（前の実行の規則で決まった重要度のまま扱う）
  - 2 ラウンド目より後に、変えていない行への major で進まない決まり（#3780）

## 仕様

### 常に成り立つ条件

| # | 対象 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | 初めて取り込んだ行（そのとき） | `severity` は必ず尺度の値で、`effective_severity(元の重要度, impact, redline=…)` の返りである。ただし、MVV の Mission・Vision・Value に反する指摘は、その返りと元の重要度の重い方を `major` 以上にした値で、必ず人の承認が要る操作（`P`・`C` の番号）に当たる指摘は 1 文が無くても下げない（`redline` が真） | テストが落ちる |
| I2 | 初めて取り込んだ行（そのとき） | `raised_severity` を必ず持つ（尺度に無い・無いときは null）。`impact` は 1 文があるときだけ、前後の空白を除いて持つ | テストが落ちる |
| I3 | 引き継いだ行・組み直しの行・前の版で書いた行（入れたとき） | `impact`・`raised_severity` を足さず、記録された `severity` のまま読む。記録に今と同じ MVV での Mission・Vision・Value の判定が残っていれば、`major` 以上に固定する | テストが落ちる |
| I3a | 出し直された行（取り込み・引き継ぎのどちらの行も） | `severity` は既にある値と新しい項目を下げた後の値の重い方で、既にある値より軽くならない。例外は MVV の判定で重さを固定した行で、固定を外すとき（MVV が変わったときと、必ず人の承認が要る操作と言われたとき）に、固定する前の重さ（`unpinned_severity`）へ戻してから重ねるので、軽くなることがある。`impact` は無ければ足し、あれば保つ。`raised_severity` は持つ行だけ更新する（下の「出し直された指摘の重ね方」）。I1・I3 は出し直しには当てはめない | テストが落ちる |
| I4 | 収束の妨げ・未解決の重い指摘・記録のコメントの表・スレッド | どれも行の `severity` だけを読む。下げた行は今の `minor` と同じに扱われる。例外は MVV の 2 つで、必ず人の承認が要る操作に当たる指摘（`redline`）は、重さによらず収束を妨げず、未解決なら表に「人の承認を待つ」と出る。この指摘と、根拠の行の誤りを NDF が見つけた指摘（`by: ndf`）は、重さによらず、次の実行が読む未解決の指摘の一覧に載る | テストが落ちる |
| I5 | 相手役への指示 | 「基準に当たる指摘は critical・major」の文を持たず、`critical`・`major` に `impact` を求め、書けない指摘は `minor` にすると書く。文は `review-criteria.json` の `impact.reviewer` から組む | テストが落ちる |

### 取り込む重要度の規則

`effective_severity(severity, impact, redline=False)` は次のとおり決める（`severity` は `severity_of` を通した値）。下げる先（`minor`）は「収束の妨げにならない
重さのうち最も重いもの」、上げる先（`major`）は「収束の妨げになる重さのうち最も軽いもの」で、どちらも尺度の表から引き、文字列で直書きしない。

| 相手役の重要度 | 1 文あり | 1 文なし |
| --- | --- | --- |
| `critical`・`major` | そのまま | `minor` |
| `minor`・`nit` | そのまま | そのまま |
| 無い・尺度に無い値（`"重い"` など） | `major` | `minor` |

`redline` が真（必ず人の承認が要る操作に当たる指摘）なら、1 文が無くても「1 文あり」の列で決める。MVV の Mission・Vision・Value に反する
指摘は、この表の値に続けて `major` 以上に固定する（`criteria_decide.at_least_blocking`・`journal._classify`）。どちらも、1 文が無ければ
結果の注意（`warnings`）に指摘の id が載る。

`impact` が文字列でない（数値・配列・null）・空・空白だけのときは 1 文なしと読む。項目の無い古い出力も同じである。

下げた行は「`raised_severity` が収束の妨げになる重さか null で、`severity` が `minor`」の行で、`criteria_decide.downgraded(row)` の 1 か所で
見分ける。`raised_severity` の無い行は下げていない行として読む。止める判定・表・スレッドは `downgraded` を呼ばない（行の `severity` で足りる）。
`downgraded` の読み手は、リリースの後の集計（状態ファイルの `findings` を数える）とテストである。

### 出し直された指摘の重ね方

既にある指摘が別の相手役か別のラウンドから出し直されたら、`journal._raised_again` が次のとおり重ねる。直した指摘（`fixed`）が戻ったときも
同じ規則で、位置とセクションだけを再発したラウンドの値に置き換える。

| 項目 | 重ね方 |
| --- | --- |
| `severity` | 新しい項目を `effective_severity` で下げた後の値と、既にある行の値の重い方（`heavier`） |
| `impact` | 既にある行に無く新しい項目にあれば足す。既にあれば上書きしない |
| `raised_severity` | 行が既に持つときだけ、`heavier_raised`（尺度にある値を null より優先し、両方あれば重い方）で更新する。引き継いだ行には足さない |

`raised_severity` に `heavier` を使わないのは、`heavier` が null を major と同じ重さに数えるためである。同じ指摘を一人が重要度なし、もう一人が
major で出すと、入れる順で `raised_severity` が変わり、元の重要度の分布の集計がぶれる。

### 相手役と修正担当への指示

- 相手役の出力の最後の行の JSON は `text`・`severity`・`impact`・`path`・`line`・`section` を持つ（`drive._review_prompt`）。承認済みの MVV があるときは、反する項目を書く `mvv` も持つ
- 相手役への「指摘の基準」の重要度の行は「重要度 severity は critical・major・minor・nit から選ぶ。」に `impact.reviewer` の文を続ける。
  基準ごとの「起きること」の言い換えは次のとおりで、当たるものを 1 つ書けばよい
  - 基準 2（秘密・認証認可・利用者のデータ・戻せない操作）: 何が誰に漏れるか・何が戻せなくなるか・誰がどの操作を権限の外で実行できるか・
    どのデータに何が起きるか。認可の指摘はデータへの影響が無くても「誰がどの操作を権限の外で実行できるか」だけで足りる。起きる確率は書かない
  - 基準 3（トークン・所要時間・処理の回数を増やす変更）: 何がどの単位でどれだけ増えるか
  - 基準 4（設計文書の食い違い）: 実装する人が何を作り違えるか
- 修正担当への指示の各指摘の行は、行が `impact` を持てば末尾に「— 起きること: <1 文>」を添える（`criteria_decide.fixer_impact`）。持たない行
  （引き継いだ行・前の版の行）は今の形のまま
- 記録のコメントの表とスレッドの書式は変えない

### 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| 1 | 1 文の項目名を `impact` にする | 既存の項目（`text`・`severity`・`path`・`line`・`section`）と同じ英語の短い 1 語にそろえる。重要度を影響で決める考え方（SonarQube）と名前が合う | `user_path`（基準 2〜4 の指摘は利用者の経路を持たないことがある）・`consequence`（長い）・日本語の項目名（並べると揃わない） |
| 2 | 下げる判定は取り込み（`journal.enter`）で 1 回だけ行い、行の `severity` に下げた後の値を書く | 読む側で判定すると、記録のコメントの表・スレッド・引き継ぎに元の重要度が出て止める判定と食い違う。読む箇所が 5 つあり、1 つ漏れると下げた指摘でラウンドが止まる | 終わり方の判定や `journal.pending` で行を読むたびに判定する |
| 3 | 取り込んだ行に `raised_severity`（元の重要度）と `impact` を持たせる | 下げた指摘が元は `critical`・`major`・重要度なしのどれだったかを、リリースの後に数えて読むため | 下げた印の真偽だけを持つ（元の重要度の分布が取れない）・引き継いだ行にも置く（前の実行の記録は元の値を持たず、推した値を置くと分布が狂う） |
| 4 | 出し直しは下げた後の値と既にある値の重い方を残し、1 文は先の値を保つ。直した指摘の再発も同じ規則にする | 同じ操作を繰り返しても結果が変わらない。1 文つきの重い指摘を直した後に 1 文の無い形で戻っても、`minor` に落ちて直し損ねが残ることが無い | 直した指摘の再発だけ付け直す・下げずに重ねる（下げた指摘が止める側へ戻る）・1 文を上書きする |
| 5 | 指示の文はデータ（`review-criteria.json` の `impact`）に置き、「基準に当たる指摘は critical・major」を外す | 基準と重要度の文の保存先を 1 か所にする。古い文を残すと `minor` を付ける余地が無いまま残る | 指示の文をコードに書く・古い文を残して 1 文だけを足す |
| 6 | 1 文は修正担当への指示にだけ載せ、記録のコメントの表とスレッドの書式は変えない | 表の「重要度」のセルとスレッドの本文は次の実行の引き継ぎが読み戻す。列や本文を変えると読み取りと重ねるキーが変わる | 表に「起きること」の列を足す・スレッドの最初のコメントに載せる |
| 7 | 項目の無い既存の行は付け直さず、尺度は変えない | 実行の途中で版を上げると、同じ実行の前後のラウンドで止める判定が変わる | 状態ファイルを読むときに前の版の行も付け直す |

決定 1 と 5 の代償として、基準に当たる指摘でも `minor` が付き得る。その指摘は修正担当へ渡らず、記録のコメントの表に「軽い」で残るだけである。
決定 6 の代償として、PR を読む人は 1 文を状態ファイルでだけ読める。決定 7 の代償として、版を上げる前に始めた実行の重要度の無い行は、
続きの実行でも止める側に倒れる。

## データ・設定

状態ファイル（`cross-review-pr<元の番号>-state.json`）のジャーナルの行（`findings[]`）の重要度に関わる項目。

| 行の出所 | `severity` | `impact` | `raised_severity` |
| --- | --- | --- | --- |
| 取り込み | 下げた後の値 | 1 文があれば | 置く（null を含む） |
| 引き継ぎ（`t<コメントの id>`・`p-<元の id>`） | 記録された値（出し直されれば重い方。I3a。今と同じ MVV の Mission・Vision・Value の判定が残っていれば `major` 以上） | 置かない（出し直された項目が 1 文を持てば足す） | 置かない |
| 組み直しの足すべき内容 | 置かない | 置かない | 置かない |
| 前の版で書いた行 | そのまま | 無い | 無い |

`review-criteria.json` の `impact` は `reviewer`（相手役への指示の文。`{blocking}`・`{light_first}`・`{light}` を尺度から埋める）と
`fixer_label`（修正担当への添え書きの名前「起きること」）を持つ。キーが無ければ `reviewer_block` は `KeyError` で止まる（契約のテストが先に落とす）。

1 文と `raised_severity` は、`ndf cross-review run` の引数と結果 JSON（`cross-review.result.schema.json`）に現れない。

## 運用

**既知の限界。**

| 項目 | 内容 |
| --- | --- |
| 今より早く収束するラウンドがある | 相手役が利用者の経路への影響を書けない重い不具合を見つけたとき、その指摘は修正担当へ渡らずに表へ「軽い」で残る |
| 基準 2 の重い指摘も下がる（#4045） | 駆動は指摘がどの基準に当たるかを知らないので、秘密の漏れ・認証認可・利用者のデータ・戻せない操作に触れる `critical`・`major` も、相手役が `impact` を書き忘れるか項目の形が崩れるだけで `minor` へ下がる。その指摘は修正担当へ渡らず、ラウンドは収束してマージへ進み得る。相手役が `mvv` に必ず人の承認が要る操作の番号（`P`・`C`）を書いた指摘だけは下がらず、「人の承認を待つ」として残る。基準 2 に当たる指摘を下げずに渡す変更は [#4045](https://github.com/devbasex/ai-plugins/issues/4045) が扱い、この版の実装には入っていない |
| 相手役が 1 文を書く割合 | codex・kiro が指示どおりに `impact` を書くか、書けない指摘を自分で `minor` にするかは、実際の実行の出力でしか分からない |

リリースの後、5 回以上の実行の状態ファイルの `raised_severity`・`severity`・`impact` と相手役の出力（`round<n>-<相手役>.out`）を集計し、
重要度の分布・ラウンド数・下げた指摘のうち実は重かったものの数（抜き取って読む）を、背景の実測と比べた結果を #4014 に書く。

## テスト観点

テストは `plugins/ndf/tests/domains/crossreview/` にある。

| 観点 | 縛るテスト |
| --- | --- |
| `effective_severity` が 4 つの重要度と None × 1 文の有り無しの 10 通りで表のとおりに返し、空文字・空白だけ・数値の 1 文を 1 文なしと読むこと | `test_crossreview_criteria.py` の `test_issue_4014_effective_severity_downgrades_heavy_findings_without_impact` |
| `downgraded` が下げた行だけで真で、`raised_severity` の無い行・尺度に無い値で正しく読むこと | `test_crossreview_criteria.py` の `test_issue_4014_downgraded_reads_raised_severity` |
| 相手役への指示が `impact.reviewer` から組まれ、基準 2・3・4 の言い換えを持ち、「基準に当たる指摘は」の文を持たないこと（I5） | `test_crossreview_criteria.py` の `test_issue_4014_reviewer_block_asks_for_the_impact_from_the_data` |
| 取り込んだ行が元の重要度と 1 文を持ち、1 文の無い重い指摘が `minor` になること（I1・I2） | `test_crossreview_journal.py` の `test_issue_4014_raised_keeps_the_original_severity_and_the_impact`・`test_issue_4014_enter_downgrades_heavy_findings_without_impact` |
| 出し直しがどちらの順でも `major` と 1 文を残し、下げた行が 1 文つきで戻ると `major` に戻り、直した指摘の再発が前の 1 文を保つこと | `test_crossreview_journal.py` の `test_issue_4014_reraised_*`・`test_issue_4014_unrated_and_major_without_impact_*`・`test_issue_4014_downgraded_row_reraised_with_impact_goes_back_to_major`・`test_issue_4014_fixed_finding_coming_back_without_impact_stays_major_with_the_old_impact` |
| 1 文の無い重い指摘だけのラウンドで修正担当を起動せず収束し、1 文が修正担当への指示に載り、重要度の無い指摘が 1 文の有無で `major`・`minor` に分かれること | `test_crossreview_run.py` の `test_issue_4014_heavy_findings_without_impact_do_not_launch_the_fixer`・`test_issue_4014_impact_reaches_the_fixer_and_rates_findings_without_a_severity` |
| 下げた行が記録のコメントの表に `minor` と「軽い（直さずに残した）」で出て、スレッドを作らないこと（I4） | `test_crossreview_report.py` の `test_issue_4014_downgraded_finding_is_a_light_row_without_a_thread` |

手順書の文は文言を突き合わせるテストを書かず、ドキュメントレビューで見る。

## 関連リンク

- [issue #4014](https://github.com/devbasex/ai-plugins/issues/4014) — 要求と受け入れ条件
- [Pull Request #4023](https://github.com/devbasex/ai-plugins/pull/4023) — 実装
- [issue #4045](https://github.com/devbasex/ai-plugins/issues/4045) — 1 文の欠けた基準 2 の重い指摘を下げずに修正担当へ渡す
- [cross-review-project-mvv.md](cross-review-project-mvv.md) — MVV に反する指摘の重要度の固定と、人の承認を待つ指摘の扱い
- [cross-review-evidence-based.md](cross-review-evidence-based.md) — 指摘の採否を根拠で決める枠組み
- [cross-review-fixer-sessions.md](cross-review-fixer-sessions.md) — 同じ実測から起こした、修正担当の会話を続ける仕様（#4013）
- Conventional Comments: https://conventionalcomments.org/
- SonarQube のソフトウェアの品質と重要度: https://docs.sonarsource.com/sonarqube-server/latest/user-guide/clean-code/software-qualities/
