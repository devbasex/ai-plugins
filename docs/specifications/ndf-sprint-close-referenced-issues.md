# スプリントを閉じる: 参照だけの課題を「開いたまま」に載せ、閉じる課題が 0 件なら止まる（sprint-close.py）

## 概要

**「スプリントを閉じる」（`plugins/ndf/scripts/sprint-close.py`）は、スプリントの Pull Request の題か本文が番号で参照したのに
閉じる対象に入らない開いた課題を、`kept_open` の行として理由つきで結果 JSON に載せる。** 閉じも、ボードへの書き込みもしない。
あわせて、スプリントの Pull Request を 1 本以上読んだのに閉じる課題が 0 件のときは、課題もボードも書かずに
`stopped`（終了コード 2）で止まる。

この文書は、その形に**なぜしたか**（背景・決定と理由・常に成り立つ条件・参照の読み方の境界・既知の限界・テスト観点）を残す。
**手順・引数・結果の読み方の正は Skill とスクリプトにある。** 呼び方・`status` ごとの次の手・`result` ごとに報告に載せるものは
[`progress-tracking` の SKILL.md の「スプリントを閉じる」](../../plugins/ndf/skills/progress-tracking/SKILL.md)、
処理の順序・引数・終了コードは [`sprint-close.py`](../../plugins/ndf/scripts/sprint-close.py) の冒頭の説明、
参照の読み取りの規則は [`lib/closing.py`](../../plugins/ndf/scripts/lib/closing.py) の `referenced_numbers` を読む。

決定の番号は #767 の設計の番号である。

## 用語

「スプリント課題」「参照だけの課題」「closing keywords」の定義は用語集（[docs/glossary.md](../glossary.md)）が正である。
語の正本の行は `development-workflow` の
[`references/glossary.md`](../../plugins/ndf/skills/development-workflow/references/glossary.md) にもある。
closing keywords は、Pull Request の本文に課題の番号と一緒に書くと、マージのときに GitHub がその課題を自動で閉じるキーワードである。

## 背景

`sprint-close.py` が閉じる課題を集める入力は、スプリントの Pull Request の本文の closing keywords と `--issues` だけである。
closing keywords を伴わない参照（`Refs #554`・`関連 #550`・`- 課題: #1743`）は、結果 JSON のどの行にも現れなかった。
`kept_open` にも載らないため、漏れたこと自体が報告から読めない。

- `10.15.0-dev.1` のリリース後テスト（PR #766）では、14 本の Pull Request のうち closing keywords を持つのは 1 本だけで、
  スプリントの 8 課題のうち集まったのは 2 課題だった
- スプリント PR #1825 の本文は異なる番号を 16 件参照し、closing keywords は 0 件だった。集めた課題が 0 件のまま
  「失敗 0 件」の `ok` で終わる経路があった

closing keywords を書かない経路は規約として残る（`pace: fast` の実装 PR は develop へ直接入り、課題は `--issues` で渡す）。
本文は作った後に手で書き直されることもある。そのため、突き合わせは閉じる側に置く。

## 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| — | 突き合わせの相手は、スプリントの Pull Request の題と本文が番号で参照する課題にする | スプリントはマイルストーンから切り出すため、マイルストーンの開いた課題を全部並べると別のスプリントの課題が毎回数十件並び、漏れが埋もれる。観測した 2 件の事例は、どちらも Pull Request が番号で参照していた | マイルストーンの課題（`gh issue list --milestone`）と突き合わせる |
| — | 参照だけの課題は閉じない | 参照だけでは、スプリントで終えた課題か、関連として挙げただけの課題かを決められない。閉じるなら運用者が `--issues` で渡す | 参照した課題も閉じる |
| — | PR を作る側（`pr-steps.py`）では closing keywords の欠けを検査しない | 作った後に本文を手で書き直すと closing keywords が落ちうるが、その書き直しは `pr-steps.py` を通らない。閉じる側の突き合わせは、本文がどの経路で書かれても効く | `pr-steps.py plan` に本文の closing keywords の検査を足す |
| 1 | 参照の読み取りを `lib/closing.py` の `referenced_numbers` に置き、closing keywords は今と同じく `lib/closing-issues.sh` で読む | Python の closing keywords の判定（`CLOSING`）の隣で「closing keywords でない参照」の規則を読める。bash の `grep -E` には後読みが無く、`x/y#3` の `#3` を外す規則を別の書き方で組むことになる | `sprint-close.py` の closing keywords の判定を `closing.py` へ切り替える（`closing.py` の `[\w.-]` は日本語の文字にも当たり、`closing-issues.sh` の `[A-Za-z0-9._-]` と判定が食い違いうる。closing keywords の判定は変えない） |
| 2 | 題は参照としてだけ読み、closing keywords は本文だけから読む | GitHub は題の closing keywords で課題を閉じない。題の closing keywords を読むと、マージで閉じない課題をスプリントが閉じる。題にだけ番号がある PR を拾うため、参照としては読む | 題と本文を 1 つの文字列へつないで `closing-issues.sh` へ渡す |
| 3 | 参照だけの課題は既存の `kept_open` で載せ、`result` の値を増やさない。区別は `reason` と `metrics.referenced_only` で付ける | `kept_open` は「開いたまま残した」を意味し、参照だけの課題の扱いと一致する。`kept_open` は止める理由にならないため、棚卸しへの進み方も変わらない | `result` に新しい値を置く（呼ぶ側の分岐が増える） |
| 4 | 0 件の停止は参照の状態を読まずに出し、参照した番号は `next` の文へ並べる | 止まる経路で `gh issue view` を数十回打つと止まるまでの時間が延び、`items` が停止の理由と別の内容で埋まる。番号を文として並べれば、運用者は `--issues` へ渡す候補を見られる | 停止の前に参照の状態を読んで `items` に載せる |
| 5 | マイルストーンで絞り込む引数（`--milestone`）を持たない | 参照は PR が書いた番号に限られ、スプリントの外の課題が数十件並ぶことは起きにくい。マイルストーンを使わないプロジェクトでは効かず、呼ぶ側の雛形にも引数が要る | `--milestone` を足す |
| 6 | 参照だけの課題の状態と種別は `gh issue view --json state,url -q '[.state, .url] \| @tsv'` の 1 回で読む。スプリントの PR は `gh pr view --json title,body` の 1 回で読む | Pull Request の番号は `gh issue view` が `MERGED` と `/pull/` を含む URL を返すため、課題と同じ 1 回の読み取りで見分けられる。PR の題と本文を JSON で分けて受ければ、決定 2 のとおり closing keywords の読み取りへ題が混ざらない | 状態と URL を別々に読む。`-q` で題と本文を 1 つの文字列へつなぐ |

## 仕様

### 常に成り立つ条件

| 条件 | 破れたときの扱い |
| --- | --- |
| 参照だけの課題には `gh issue close` もボードの書き込み（`projects-sync.sh`）も行わない。`close_one` を通さない | 実装の誤り。テストが落とす |
| `items` の `kind: "issue"` の行は `(repo, number)` ごとに 1 件。`--issues` と参照が重なれば `--issues` 側の結果だけが載り、同じ番号を何度参照しても 1 件 | 実装の誤り。テストが落とす |
| スプリントの PR を 1 本以上読み、スプリント課題が 0 件なら、課題もボードも書かずに `stopped`（2）で終わる。この判定は閉じる条件の判定より前にあり、配布の記録が本番の前でも止まる | 実装の誤り。テストが落とす |
| 参照だけの課題の行は、記録のリポジトリの、状態が `CLOSED` でなく URL に `/pull/` を含まない番号だけ | 当たらない番号は行にしない |
| 参照だけの課題の状態を読めなくても、他の課題の処理と終了コードは変わらない | その番号を `kept_open`（理由の先頭に「状態を読めない」）にして続ける |
| closing keywords は本文だけから読む。題の closing keywords で課題を閉じない | 実装の誤り。テストが落とす |
| 参照だけの課題の判定は、閉じる条件にも `--dry-run` にも左右されない（どちらでも `kept_open` で載る） | テストが落とす |
| 参照だけの課題の行は、スプリント課題の行の後、ボードの NOTE の行の前に、PR の並び・題・本文の順で現れた順に並ぶ | — |
| 参照だけの課題 1 件につき `gh issue view` は 1 回、スプリントの PR 1 本につき `gh pr view` は 1 回 | テストが落とす |
| `--record-pr 0`（本番の記録なし）ではスプリントの PR を読まないため、参照だけの課題の行も 0 件の停止も起きない | — |

### 参照の読み方の境界

| 形 | 読むか | 例 |
| --- | --- | --- |
| `#<番号>`（直前が英数字・`.`・`_`・`/`・`-` でなく、直後が英数字・`_` でない） | 読む | `Refs #554`・`関連 #550`・`課題#5`・`- 課題: #1743` |
| 記録のリポジトリの課題の URL（`https://github.com/<記録のリポジトリ>/issues/<番号>`、大文字小文字を区別しない） | 読む | `https://github.com/o/r/issues/77` |
| 他のリポジトリの `<所有者>/<リポジトリ>#<番号>`・課題の URL | 読まない | `x/y#3`・`https://github.com/x/y/issues/4` |
| `<記録のリポジトリ>#<番号>` | 読まない | `o/r#9` |
| Pull Request の URL・番号に英字が続くもの・英数字などの直後の `#` | 読まない | `.../pull/8`・`#123abc`・`a#6` |

`#<番号>` が Pull Request の番号でも、読む時点では区別しない。状態を読んだ後で、URL が `/pull/` を含むものを外す。

### 振る舞いの変化の範囲

引数は変わらない。結果 JSON は `items` に `kept_open` の行が増え、`metrics` に `referenced_only` が 1 つ増える
（`metrics.kept_open` と `metrics.issues` は参照だけの課題を含む）。`ok` から `stopped`（2）へ変わるのは、
スプリント課題が 0 件のスプリントだけである。supervise.py の `close` のステップは `new sprint --issue` の全課題を
`--issues` で渡すため、この経路では 0 件の停止は起きない。

## 運用

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| コードブロックの中の参照 | 本文のコードブロックやログの引用にある `#<番号>` も参照として読む。参照だけの課題が増えるだけで閉じはしない |
| `<記録のリポジトリ>#<番号>` の形 | 読まない。この形だけで参照された課題は、参照だけの課題にも現れない |
| 停止の `next` に並ぶ番号 | 状態を読まずに並べるため、Pull Request の番号や閉じた課題が混ざりうる。`--issues` へ渡す番号は運用者が選ぶ |
| 関連として挙げただけの課題 | スプリントで扱っていない課題も、番号で参照されれば `kept_open` で並ぶ。マイルストーンでの絞り込みは持たない（決定 5） |
| 2 つの closing keywords の判定 | `sprint-close.py` は `closing-issues.sh`、`pr-steps.py` などは `closing.py` の `CLOSING` で読み、日本語の文字の直前の扱いが食い違いうる（決定 1） |
| `--issues` に渡し漏れた課題 | PR が番号で参照していない課題は、どの行にも現れない |

## テスト観点

参照だけの課題と 0 件の停止は `plugins/ndf/scripts/tests/test_sprint_close_references.py`、参照の読み取りは
`plugins/ndf/scripts/tests/test_closing.py` にある。どちらも gh の偽物で再現する。

- 本文が closing keywords で 1 件を指し、別の開いた課題を `Refs` で参照するとき、`--dry-run` では前者が `would_close`、後者が `kept_open` で `reason` に「--issues も無い参照」を含むこと。`--dry-run` を外すと前者が `closed`、後者は `kept_open` のままで、後者への `issue close` と `projects-sync.sh` が呼ばれないこと
- 題にだけある参照も `kept_open` で載り、題の closing keywords では閉じないこと
- Pull Request の番号と `CLOSED` の課題が `items` に無いこと
- `--issues` と重なる参照、同じ番号の 2 回の参照が、どちらも 1 行になること
- 状態を読めない参照が「状態を読めない」の `kept_open` になり、他の課題は閉じ、終了コードが 0 であること
- 他のリポジトリの参照が `items` に無いこと
- `metrics.referenced_only` が参照だけの課題の行数と等しく、`summary` にその数が入ること
- closing keywords も `--issues` も無いとき、`stopped`・終了コード 2 で「閉じる課題が 0 件」を返し、`issue close` もボードの書き込みも呼ばれないこと。`--issues` を足すと止まらずに閉じること
- 配布の記録が本番の前のとき、参照だけの課題も `kept_open` で載り、何も閉じないこと
- PR #766 の事例（closing keywords が 2 件、`Refs #554`・`関連 #550`・本文中の `#540`）で、#554 #550 #540 が `kept_open` で載ること
- `referenced_numbers` が上の「参照の読み方の境界」の表の各行の例のとおりに読む・読まないを返し、現れた順で重ならないこと
- 既存の `test_sprint_close_issues.py`・`test_sprint_close_merge_green.py`・`test_release_record.py` と supervise の雛形の契約テスト（`tests/fixtures/supervise-contract/`）が期待値を変えずに通ること

## 関連リンク

- [`progress-tracking` の SKILL.md](../../plugins/ndf/skills/progress-tracking/SKILL.md)（「スプリントを閉じる」の手順と結果の読み方の正）
- [`sprint-close.py`](../../plugins/ndf/scripts/sprint-close.py)・[`lib/closing.py`](../../plugins/ndf/scripts/lib/closing.py)・[`lib/closing-issues.sh`](../../plugins/ndf/scripts/lib/closing-issues.sh)
- [ndf-production-release-record.md](ndf-production-release-record.md)（`--record-pr` が読む配布の記録と閉じる条件 1）
- [ndf-backlog-refinement-stage.md](ndf-backlog-refinement-stage.md)（`sprint-close.py` が `ok` で終わった後の棚卸し）
- #767（この形を決めた課題）、PR #766・PR #1825（漏れを観測した事例）
