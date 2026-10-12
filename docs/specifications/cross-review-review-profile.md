# レビューのプロファイル: プロジェクトが `.ndf/review.json` の `version: 2` で指摘の基準・見送り・自動レビュー観点を差し替え、外せない基準だけは残す（cross-review・/ndf:fix）

## 概要

**プロジェクトは `.ndf/review.json` に `version: 2` の `profile` を書くと、cross-review のレビュー担当・修正担当へ渡す
「指摘の基準」の節、見送りの種類と見送りの返信、自動レビュー観点を差し替えられる。** システム開発以外の成果物
（例: Markdown の小説の原稿）の PR も、cross-review の収束ループ（2 者がレビューし、1 者が直し、新しい指摘が出なくなるまで
回す）で通すためである。書いた鍵だけが既定を差し替え、`profile` を書かないリポジトリでは出力も状態ファイルも 1 字も
変わらない。**基準 2（秘密・認証認可・利用者のデータ・戻せない操作）は外せない基準で、プロファイルがあっても番号 2 のまま残る。**
`version: 3` では `profiles` に名前つきのプロファイルを複数書き、PR の変更ファイルのパスで 1 つを選ぶ。選んだ 1 つは
`version: 2` の `profile` と同じに効き、この文書の決定・条件・境界はそのまま当てはまる。`version: 3` の最上位に書いた
`criteria`・`not_written` は、選んだ組（選ばなかった PR では既定の組）へ足され、番号がどれかの組と重なる宣言は壊れたものとして捨てる。

この文書は、その形に**なぜしたか**（背景・決定と理由・常に成り立つ条件・境界・既知の限界・テスト観点）を残す。
**宣言の鍵と規則・状態ファイルの項目・修正担当の手順の正は Skill とスクリプトにある。**

| 何を | 正本 |
| --- | --- |
| 宣言（`.ndf/review.json`）の鍵と規則・宣言の例・状態ファイルの `profile_status` / `profile` / `profile_error` / `profile_name` / `profile_others`・`init` の出力の `REVIEW_PROFILE=` / `REVIEW_PROFILE_NAME=`・`version: 3` のプロファイルの選び方と最上位の `criteria`・`not_written`・再開で組み直す条件 | `cross-review` の [docs/02-fix-and-rotation.md](../../plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md) の「レビューの宣言（`.ndf/review.json`）」の節 |
| 自動レビュー観点の分類の一覧と、プロファイルの分類・`replace_auto_categories` の効き方・`auto_review_categories` | `cross-review` の [docs/04-contracts.md](../../plugins/ndf/skills/cross-review/docs/04-contracts.md) |
| 指摘の基準の方針（基準 1〜4 とプロファイルで差し替えられる範囲） | `cross-review` の [references/design-principles.md](../../plugins/ndf/skills/cross-review/references/design-principles.md) の「指摘の基準」の行 |
| 修正担当の振り分け（`criterion` / `waive_kind` の許す値・見送りの返信・「対象のファイル」の語） | [`fix` の SKILL.md](../../plugins/ndf/skills/fix/SKILL.md) と [`fix-steps.py`](../../plugins/ndf/skills/fix/scripts/fix-steps.py) の冒頭の説明 |
| 既定の基準・書かないもの・見送りの種類・返信の雛形の文、`reviewer` / `fixer` の打ち方 | [`scripts/lib/review_criteria.py`](../../plugins/ndf/scripts/lib/review_criteria.py) の冒頭の説明と定数 |

決定の番号は #1910 の設計の番号である。

## 用語

「レビューのプロファイル」「指摘の基準」「外せない基準」「見送りの種類」「見送りの返信」「自動レビュー観点」の定義は
用語集（[docs/glossary.md](../glossary.md) の `ndf-cross-review`）が正である。語はすべて NDF の cross-review
（`ndf-cross-review`）の 1 つのコンテキストに収まり、`/ndf:fix` を単独で使うときも同じ語と同じ部品（`review_criteria.py`）で読む。

## 背景

指摘の基準・書かないもの・見送りの種類・見送りの返信・自動レビュー観点は、ndf 10.17.70 まですべて NDF の定数で、
プロジェクトからは `.ndf/review.json` の `focus`（基準 3）と `--focus` / `--extra-instructions-file` で**足すことしかできなかった**。
利用者のプロジェクトで「小説の原稿を 10 話ごとに PR にまとめて cross-review したい」という求めが出たが、原稿の主な指摘
（言い回し・文の自然さ・人物の口調・設定との食い違い）は既定の「書かないもの」（字句や言い回し）に当たり、書かれても修正担当が
`wording` で見送った。観点を足しても「書かないもの」と食い違う指示が同じプロンプトに並び、プロジェクトは同じ形のループを
自前の Skill で組んでいた。

## 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| 1 | 基準 2 を外せない基準にし、番号 2 と 3 をプロファイルの `criteria` に使わせない（書けば壊れたプロファイル） | プロファイルは PR の head から読むため、基準 2 を消せると PR の作り手がその PR の安全の基準を外せる。原稿のリポジトリでも実在の人物の個人情報や秘密の混入は起こりうる。番号 2 を固定すると、どのリポジトリでも `criterion: 2` が同じ意味になり、振り分けの記録を横断して数えられる。番号 3 は重点が後から足されても衝突しないよう、重点が無いときも予約する。承認ゲート 1（2026-10-11）で利用者が決めた | プロファイルが基準 2 も差し替える（課題の「提案」の例の形）。外せない基準を末尾へ別の番号で足す（既定の出力と番号の意味がプロファイルの有無で変わる） |
| 2 | プロファイルの `paths` は `.ndf/pace.json` の `areas` と同じ規則で照らし、`scripts/lib/pace.py` の `matches` を使う（`*` と `?` は `/` をまたがず、`**` はまたぐ） | `.ndf/` の宣言のパスの書き方が鍵ごとに違うと、書き手が覚え分ける。`fnmatch` は `*` が `/` をまたぎ、`works/*/chapters/*/text.md` が `works/a/x/chapters/011/text.md` にも当たる | `fnmatch` や `PurePath.match` で別に照らす（同じ役割の関数が 2 つになる） |
| 3 | 修正担当の語はプロファイルの有無だけで「対象のファイル」に切り替え、成果物の呼び名の鍵を足さない。`/ndf:fix` の手順は常にコードに限らない語で書く | 切り替わるのは修正担当の節の見送りの句 1 か所で、呼び名を変えても指示の意味は変わらない。`SKILL.md` は宣言を読まない | プロファイルに呼び名の鍵（例: `artifact`）を足す（使い道が 1 句だけで、検査する鍵が増える） |
| 4 | 壊れたプロファイルは丸ごと捨てて既定で続け、終了コードを変えない。知らない鍵があるときも丸ごと捨てる。重点（`focus`）はプロファイルと独立に読む | 鍵ごとに捨てると、`waive_kinds` だけ既定で `reply` はプロファイルのまま、のような書き手の意図に無い組み合わせが生まれる。丸ごと捨てれば、出るのは既定かプロファイルかのどちらかで、報告の 1 行で状態が分かる。鍵の打ち間違い（`waive_kind` など）を黙って無視すると、意図に無い既定が効く | 壊れた鍵だけ既定に戻す。宣言を止める（今の重点の宣言の扱い〈止めない〉と食い違う） |
| 5 | `review_kind`（設計 / コード）とラウンドの段は既定の分類から決め、観点を渡す分類の列（`auto_review_categories`）とは分ける | `replace_auto_categories` が真のとき観点の列から `design` が外れ、この列で決めると設計 PR が実装 PR の上限（12 ラウンド）で回る。プロファイルの分類の名前が既定の名前と同じときは、列では 1 つにまとめ、観点の文は既定の後ろにプロファイルの文を足す | 既定の分類の列を状態ファイルに別に持つ（再開で `changed_files` から組み直せる） |
| 6 | 新規・再開とも worktree を同期した後に宣言を読み、再開では保存した `changed_files` から観点と基準を組み直して、保存した値と違うときだけ状態ファイルを書く | 宣言は PR の head の worktree にあるため、新規でも worktree を用意してから観点を組む。再開で組み直さなければ、プロファイルの書き換えが次のラウンドに効かない。組み直しに GitHub を呼び直さない | 再開ごとに PR の変更ファイルを GitHub から取り直す（今の再開の範囲を超える） |
| 7 | `fix-steps.py context` がプロファイルを振り分けの JSON（`review_profile`）へ写し、`finalize` はその写しで検査して返信を組む | `finalize` が宣言を読み直すと、`context` の後に宣言が変わったとき、修正担当が見た基準で使った種類を拒む。重点（`review_focus`）と同じ経路で写す | `finalize` が状態ファイルか宣言を読み直す |
| 8 | 見送りの返信の重点の句（「。レビューの重点（…）にも当たりません」）は既定の雛形のときだけ足す | 重点の句は既定の雛形の括弧の中に合わせた文で、プロファイルの `{inner}` に入れると「好みの文体。レビューの重点（…）にも当たりませんに当たるためです。」のように崩れる | 重点の名前の置き場（`{focus}`）を足す（要求に無い） |
| 9 | 状態ファイルと振り分けの JSON には、プロファイルがあるときだけ項目を置く | 常に置くと、古い状態ファイルからの再開で `review_criteria` の値が変わり、宣言を変えていないのに書き直しが起きる。項目が無いことを「プロファイルなし」と読む | 項目を常に置く |

## 仕様

### 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| `plugins/ndf/scripts/lib/review_criteria.py` | 既定の基準・書かないもの・見送りの種類・返信の雛形の唯一の置き場。宣言を 1 回読み（`load_review_decl` → `ReviewDecl(focus, profile, profiles, shared, others)`。例外を上げない。`version: 3` は `select_profile` で 1 つを選び、最上位の基準（`shared`）を足す）、写しからプロファイルを組み直し（`profile_from` / `state_profile` / `context_decl`）、節（`reviewer_block` / `fixer_block`）・返信（`waiver_reply`）・許す値（`criterion_numbers` / `waive_kinds`）・分類の照合（`profile_categories`）・状態の `review_criteria`（`as_state`）を組む |
| `cross-review` の `review_lib/review_focus.py` | 宣言の読み取り（`_read_review_decl`）、`review_criteria` の組み立て（`_review_criteria`）、既定の分類とプロファイルの分類を合わせた観点の組み立て（`_auto_review`・再開の組み直し `_apply_auto_review`）。新規と再開の両方の経路が同じ部品を呼ぶ |
| `cross-review` の `review_lib/commands/init.py` | 新規は worktree を用意した後に、再開は同期した後に宣言を読み、`review_kind` を既定の分類から、観点と `review_criteria` をプロファイル込みで組んで状態ファイルへ書く |
| `cross-review` の `review_lib/commands/report.py` | プロファイルが `unreadable` のとき理由を出す。`version: 3`（`profile_name` がある）では選んだプロファイルの名前（選ばなかったときは「既定」）と、ほかに当たった名前（`profile_others`）の 1 行を出す |
| `fix` の `scripts/fix-steps.py` | `context` で状態ファイルの写し（無ければ `--root` の宣言。`version: 3` では PR の変更ファイルで選んだプロファイル）から節を書き、`review_profile` を振り分けの JSON へ写す。`finalize` は写しで `_decision_problems` / `_criterion_problems` を検査し、返信を組む |

`launch-reviewer.sh` は変えない。状態ファイルの `review_criteria.reviewer_block` と `review_instructions` を読むだけで、
差し替えはどちらも `init` が済ませている。

```mermaid
graph TD
    D[".ndf/review.json"] --> R[review_criteria.py<br/>宣言の読み取りと基準の組み立て]
    R --> P[pace.matches]
    I[init] --> R
    I --> F[review_focus._auto_review]
    F --> R
    I --> S[(状態ファイル)]
    S --> L[launch-reviewer.sh]
    S --> O[report]
    S --> X[fix-steps.py context / finalize]
    X --> R
```

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | プロファイルが無い（`none`）とき、レビュー担当・修正担当への節、見送りの返信、自動レビュー観点、状態ファイルの `review_criteria` は変更前と 1 字も変わらない | テストが落とす |
| I2 | 外せない基準（基準 2）は、プロファイルがあっても両方の節に出て、振り分けで `criterion: 2` が許される | `criteria` に `"2"` を書いたプロファイルは壊れたものとして捨てる（I4） |
| I3 | プロファイルの鍵は個別に効く。書いた鍵だけが既定を差し替え、書かない鍵は既定の値のまま | テストが落とす |
| I4 | 壊れたプロファイルは丸ごと捨てて既定で続け、終了コードを変えない。理由を標準エラー（cross-review では報告）に出す。重点はプロファイルと独立に読む | 止めない |
| I5 | `finalize` が許す `criterion` と `waive_kind` は、`context` が修正担当へ渡した節と同じプロファイル（振り分けの JSON の写し）から組んだ集合である | 許さない値は `finalize` が `invalid` で止める |
| I6 | `review_kind` とラウンドの段は既定の分類から決め、プロファイル（`replace_auto_categories` を含む）で変わらない | テストが落とす |
| I7 | 再開で状態ファイルの `review_criteria` と自動レビュー観点を書き直すのは、宣言から組んだ値が保存した値と違うときだけである | テストが落とす |
| I8 | 見送りの返信は、雛形の `{inner}` を見送りの種類の名前に置き換えた文である（`str.format` は使わず、ほかの波括弧は残す）。重点の句は既定の雛形のときだけ足す | テストが落とす |

### 境界の決まり

- **変わるのはレビュー担当と修正担当へ渡す文と、見送りの返信と、自動レビュー観点だけである。** 収束の判定・投稿の形・
  状態ファイルでの再開・参加者の選び方・ラウンドの数は変えない
- **許す `criterion` の番号には 3 が常に含まれる。** `criterion_numbers(profile)` は既定の `CRITERION_NUMBERS` と同じく
  3 を含め、重点が無いときの 3 は `fix-steps.py` の `_criterion_problems` が別に拒む（重点の検査を 1 か所に残す）
- **`--focus` と `--extra-instructions-file` は、プロファイルがあっても自動レビュー観点の後ろに足される。**
- **プロファイルの分類の名前が既定の自動カテゴリの名前と同じときは**、`auto_review_categories` では 1 つにまとめ、
  観点の文は既定の後ろにプロファイルの文を足す（決定 5）
- **`version: 2` の宣言を古い NDF が読むと**、宣言全体を読めないとして基準 1・2・4 で続ける（止まらない）
- `cross-refactoring` は最終ゲートとして cross-review を呼ぶため、その中ではプロファイルがそのまま効く。`pr-review`
  （単発のレビュー）の基準は差し替えない（[pr-review-steps-script.md](pr-review-steps-script.md) の決定 4 で、外部 AI に
  `review_criteria.py reviewer` の節を渡していない）

## セキュリティ

プロファイルは PR の head から読むため、PR の作り手が基準を変えられる。今の重点の宣言と同じ扱いで、新しい危険は足さない。
PR の作り手が変えられるのは、基準の追加と、基準 1・4・書かないもの・見送りの種類と返信・自動レビュー観点の差し替えまでで、
外せない基準（基準 2）は消せない（決定 1）。

## 運用

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| 実際の原稿の PR での効き目 | プロファイルの基準でレビュー担当が言い回し・口調の指摘を書くか、収束までのラウンド数は、リリース後テストでしか分からない |
| 既定の観点名との重なり | プロファイルの分類の名前が既定の自動カテゴリの名前（例: `code`）と同じときの扱い（決定 5）は、利用の実例が無い |
| `/ndf:fix` の手順の語 | `SKILL.md` の「対象のファイル」の語は `.md` の文言のテストを書かない方針のため、レビューで見る |
| 課題の「提案」の例 | 課題の本文の「提案」の JSON は `criteria` の番号 2 に原稿の基準を書いている。この形は壊れたプロファイルとして既定で続く。番号 2 の基準は 5 など別の番号へ移して書く（正しい例は `docs/02-fix-and-rotation.md`） |

## テスト観点

テストは `plugins/ndf/scripts/tests/test_review_criteria.py`、`cross-review` の `tests/test_review_profile.py`・
`tests/test_review_criteria_flow.py`、`fix` の `tests/test_fix_steps.py` にある。

- 宣言の無い根と `version: 1` だけの根で、`reviewer` / `fixer` の出力・`waiver_reply` の全種類・自動レビュー観点・`as_state` が変更前と一致すること（I1）
- `criteria` と `not_written` を書いた根の `reviewer` 出力に、プロファイルの基準が番号つきで並び、基準 2 の文があり、既定の基準 1・4 の文と「字句や言い回し」が無いこと
- `waive_kinds` と `reply` を書いた振り分けで、`finalize` の返信が `{inner}` に種類の名前を入れた文になり、重点があっても重点の句が無いこと。既定の雛形では重点の句が付くこと（I8）
- 写しを持つ振り分けで、プロファイルに無い `wording` と `criteria` に無い番号を `finalize` が `invalid` で止め、`criterion: 2` と（重点があるとき）`3` は通すこと（I5）
- `works/*/chapters/*/text.md` の分類が `works/novel-a/chapters/011/text.md` に当たり、`works/novel-a/x/chapters/011/text.md` には当たらないこと
- `replace_auto_categories` が真なら観点の列に `common` / `docs_only` が無く文はプロファイルの分だけ、偽か無ければ既定の文の後ろにプロファイルの文が付くこと
- プロファイルがある根の `fixer` 出力と `context` の文脈のファイルに「コードを変え」が無く「対象のファイルを変えずに」があること
- `reply` に `{inner}` が無い宣言・知らない鍵のある宣言で、`review_criteria.py` が終了コード 0・既定の節・標準エラーに理由を出し、`init` の状態ファイルが `profile_status: unreadable` と既定の `reviewer_block` を持ち、`report` が理由を出し、同じ宣言の `focus` は効くこと（I4）
- 再開の前にプロファイルを書き換えると `reviewer_block` と `review_instructions` が新しい値になり、書き換えなければ状態ファイルが書き直されないこと（I7）
- プロファイルがあっても `--focus` と `--extra-instructions-file` の文が観点の後ろに付くこと
- `criteria` に `"2"` か `"3"` を書いた宣言が `unreadable` になること（I2）。`criteria` だけの宣言で「書かないもの」・見送りの種類・返信が既定のままであること（I3）
- `replace_auto_categories` が真で設計文書だけを変えた PR の `review_kind` が `design`、ラウンドの上限が 3 であること（I6）

## 関連リンク

- [`scripts/lib/review_criteria.py`](../../plugins/ndf/scripts/lib/review_criteria.py)・[`scripts/lib/pace.py`](../../plugins/ndf/scripts/lib/pace.py)・[`fix-steps.py`](../../plugins/ndf/skills/fix/scripts/fix-steps.py)
- `cross-review` の [docs/02-fix-and-rotation.md](../../plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md)・[docs/04-contracts.md](../../plugins/ndf/skills/cross-review/docs/04-contracts.md)・[references/design-principles.md](../../plugins/ndf/skills/cross-review/references/design-principles.md)
- [cross-review-evidence-based.md](cross-review-evidence-based.md)（指摘区分と収束の判定。プロファイルはこれを変えない）
- #1910（この形を決めた課題）
