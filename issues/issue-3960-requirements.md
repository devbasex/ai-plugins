# #3960: #3412 の続き: plugins/ndf のコメントが指す作り直しの決定を、確定仕様 ndf-rebuild-*.md の決定へ書き換える

正は課題の本文（#3960）で、この文書はそのコピーである。本文から作り直す。手で直さない。

## 何が起きたか

11.0.0 の作り直し（#1142）の設計は `issues/` の設計文書 36 本に書かれていた。#3412 は 2026-11-21 に、その中身を確定仕様 6 本（`docs/specifications/ndf-rebuild.md` と、名前が `ndf-rebuild-` で始まる 5 本）へ移した。#3412 の設計は、プラグインのコードのコメントも確定仕様の決定を指すように直すとしていたが、`plugins/ndf` のコメントは同じ時期に動いていた 4 本のレーンと変更が重なるため、書き換えだけを外してこの Issue に分けた。

その結果、`plugins/ndf` を直す人がコメントの根拠を辿ると、次のどれかに当たる（2026-11-24 の `main` 167ea3bbc で確かめた）。

- 「決定 85」のように番号だけがあり、どの文書の決定かが書かれていない
- 「#3345 の決定 88」のように課題の番号で書かれ、今の決定がある確定仕様のファイルが分からない
- もうすぐ消す設計文書（例: `issues/issue-1142-design-smells.md`）のパスが書かれている。36 本を消す作業（#3990）は、この Issue のマージを待って止まっている

## 種類

機能追加（コメントが確定仕様のファイルと決定を指すという約束は、#3412 の要求と設計にだけあり、今の確定仕様に無い。子の表の SSOT に `packages` と `tables` を載せることも新しい約束である）

## 今の姿

**決定を指すコメント。** `plugins/ndf` には「決定 N」を書く行が 588 行（261 ファイル）ある。`docs/specifications/ndf-rebuild` で始まるパスを書く行は 0 行である。

| 書き方 | 行 | 例と、確定仕様での行き着く先 |
| --- | ---: | --- |
| 作り直しの課題（#1142・#3347・#3388・#3395）の番号か、36 本のパスを添えた行 | 19 | `identity.py` の「#1142 の決定 75」→ `ndf-rebuild.md` の決定 75 |
| ほかの課題の番号か確定仕様のパスを添えた行 | 318 | 多くは別の設計の決定。ただし #3344 の決定 81〜86 と #3345 の決定 88〜96 は、`ndf-rebuild.md` の同じ番号の決定である |
| 番号だけの行 | 251 | `durable.py` の「決定 84」は `ndf-rebuild.md` の決定 84。`wrapper.py` の「決定 13」は中継の別の設計の決定。設計文書の書き方の例（Skill の本文）も含む |

- 番号が同じでも中身が違う行がある。`progress/gate/answering.py` の「決定 99」は「課題を外すのは設計承認ゲートの回答だけが持つ」という意味で、`ndf-rebuild.md` の決定 99（P3・P4 のレーンの割り当て）ではない
- 確定仕様で置き換わった番号を指す行がある。`domains/worktree/write_target.py` の「#1142 の決定 20」は、[ndf-rebuild.md の「番号の対応表」](https://github.com/devbasex/ai-plugins/blob/main/docs/specifications/ndf-rebuild.md#番号の対応表) で決定 89 に畳まれている。6 本に見出しの無い番号は 3・20・26・27・28・64・69〜72・102・107・121 である

**36 本のファイル名を指す箇所。** `git grep -n -E 'issue-(1142|3347|3395|3388)-' -- plugins/ndf` は 7 ファイルに当たる。

| ファイル | 役割 |
| --- | --- |
| `pyproject.toml`・`src/ndf/checks/invariants.py`・`src/ndf/core/durable.py`・`src/ndf/core/plan/transition_decide.py` | コメントと docstring の根拠 |
| `src/ndf/checks/README.md`・`src/ndf/checks/children.json` の `summary` | 子の表の SSOT への参照 |
| `src/ndf/checks/rules.json` の `spec.review_items_source`・`children.json` の `review_items` の `routed_to`（62 行） | 検査の規則 `review-items` の入力。設計レビューの項目の表（`issues/issue-1142-design-review-routing.md`）と 11.x の対応表（`issues/issue-3388-design-routing.md`）を読む。この Issue の起票の後（2026-11-23、#3684）に足された |

**子の表の SSOT。** 検査の規則 `child-deps` は、作り直しの子の表のコピー `children.json` を、コードの import と比べる。[`checks/README.md` の「子の表の規則」](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/src/ndf/checks/README.md#子の表の規則) は「表を変えるときは、SSOT を直す設計 PR を先に通す」と約束し、SSOT として設計文書 2 本を指す。確定仕様の側で SSOT を名乗るのは [ndf-rebuild.md の「子の記号」](https://github.com/devbasex/ai-plugins/blob/main/docs/specifications/ndf-rebuild.md#子の記号) だが、次の 2 つが欠けている。

- 子ごとのサブパッケージ（`packages`）と、共有の表の書き込み元（`tables`）。例: `ndf.core.refs` のオーナーが子 C4 であること、`contracts/limits/` の書き込み元が D1・T1+・M3a であること
- 子 M3b+ と X9 の行（`children.json` の 54 件のうち 2 件）。残る 52 件の Issue・先に要るもの・順番・レーンは一致している

## 何を足すか・変えるか

| | 今 | 足した後 |
| --- | --- | --- |
| 作り直しの決定を指すコメント | 番号だけ・課題の番号・設計文書のパスが混ざる | `docs/specifications/ndf-rebuild-<主題>.md の決定 N` の形（`ndf-rebuild.md` はその名前）で、実在する同じ中身の決定を指す |
| 別の設計の決定を指すコメント | 番号だけの行と、課題の番号を添えた行がある | 変えない |
| 36 本のファイル名を指す箇所 | `plugins/ndf` の 7 ファイル | 0 件。36 本を消しても `ndf check` が通る |
| 子の表の SSOT | `packages`・`tables`・2 件の子が、消す設計文書にだけある | 消さない SSOT に、`children.json` の子と表のすべてが載る |

## なぜ困るか

- コードを直す人が、番号だけの「決定 N」から根拠に行き着けない。番号が同じ別の決定を読むと、意図と違う直し方をする
- 36 本を消す #3990 が着手できない。書き換えずに消すと、`checks/README.md` のリンクが切れて Markdown のリンクの検査が落ち、規則 `review-items` は入力を読めずに `config_broken` で止まる
- 子の表の SSOT が無くなると、「SSOT を直す設計 PR を先に通す」の約束が成り立たず、`children.json` の値が正しいかをレビューで確かめられない

## 目的

- `plugins/ndf` のコメントから、作り直しの決定の根拠へ 1 回の検索で行き着ける
- 作り直しの設計文書 36 本を、検査と参照を壊さずに消せる状態にする（#3990 の着手の条件を満たす）
- 子の表を変えるときに、先に直す SSOT がある

## 対象範囲

含む:
- `plugins/ndf` のコメント・docstring・README・JSON の説明の、作り直しの決定と設計文書 36 本への参照を、確定仕様の決定への参照へ書き換えること
- 作り直しの決定を、#3344・#3345 など別の課題の番号で指す行を同じ形にそろえること
- 規則 `review-items` の入力が 36 本に依らないようにすることと、それに合わせて入力のパスを書く箇所（`.github/workflows/script-structure.yml` の `REVIEW_ITEMS_PATHS`、`docs/specifications/ndf-contract-fact-checks.md` の 2 つのリンク）をそろえること
- 子の表の SSOT に `packages`・`tables`・子 M3b+ と X9 の行を載せること

含まない:
- 36 本を消すことと、開いている Issue のリンクの固定（#3990）
- `plugins/ndf` の外の、作り直しの決定を指すコメント（#3412 の PR #4008 で済み）と、`scripts/ndf-next-parity/` の参照（#3692）
- 確定仕様の決定の中身の変更・計測（#3412）
- コードの振る舞いの変更（検査の判定を変えることを含む）
- `children.json` の `closed` の値の見直しと、規則 `child-deps`・`review-items` を残すかの見直し
- 別の設計の決定を指す、番号だけのコメントの書き換え

## 前提

- 前提 1: 書き換えの形は #3412 の設計（[issue-3412-design.md](https://github.com/devbasex/ai-plugins/blob/748e01e0d6b2ba24861e78eddcd4eaa3e8de3c96/issues/issue-3412-design.md) の「解き方の全体像」と決定 3・4）に従う
- 前提 2: ある行が作り直しの決定を指すかは、番号ではなく、行とその前後の文が決定の中身と合うかで決める。見分ける順は #3412 の詳細設計と同じで、前後 10 行のコメントと同じファイルのほかの決定の参照を見る
- 前提 3: 行数とファイル数は 2026-11-24 の `main`（167ea3bbc）の値で、着手の時点で数え直す

## 受け入れ条件

- [ ] 1. `git grep -n -E 'issue-(1142|3347|3395|3388)-' -- plugins/ndf` が 0 件になる
- [ ] 2. 作り直しの決定を指すコメント・docstring・README・JSON の説明が、`docs/specifications/ndf-rebuild-<主題>.md の決定 N`（索引の文書は `docs/specifications/ndf-rebuild.md の決定 N`）の形で書かれている。1 行に並ぶ番号（`決定 82・83` など）は、ファイルごとに 1 回のパスの後ろに並べてよい
- [ ] 3. `plugins/ndf` で `docs/specifications/ndf-rebuild` で始まるファイルの決定を指す参照のうち、そのファイルに `決定 N` の見出しが無いものが 0 件である。1 つのコマンドで数える
- [ ] 4. 書き換えた行ごとに、書き換えの前の（系統か課題・番号）と後の（ファイル・番号）、合うと判断した根拠を並べた対応の一覧が PR に載る。「番号の対応表」で番号が替わった決定（畳んだ・振り直した）は、替わった先を指す
- [ ] 5. 作り直しの決定か別の設計の決定かを決められない行は書き換えず、行と理由を PR の本文に挙げる
- [ ] 6. 別の設計の決定を指す行（例: `progress/gate/answering.py` の「決定 99」、中継の「決定 13」）が変わらない
- [ ] 7. 子の表の SSOT に、`children.json` の `children` のすべての子の `packages`・`needs`・`order`・`lane` と、`tables` のすべての表の書き込み元が載り、値が一致する。`ndf.core.refs` のオーナー C4 と、`contracts/limits/` の書き込み元 D1・T1+・M3a を SSOT から引ける
- [ ] 8. `checks/README.md` の「子の表の規則」と `children.json` の `summary` が指す SSOT が 7 の保存先になる。「入れ替え（S2）がこのファイルと規則 `child-deps` を消す」のような、今の姿と違う説明（`children.json` の `summary`・`child_deps.py` の docstring）が残らない
- [ ] 9. 設計文書 36 本を手元で消した状態で、`plugins/ndf/bin/ndf check` と `python3 scripts/check-markdown-links.py` が、`plugins/ndf` と `docs/specifications/ndf-contract-fact-checks.md` の参照を理由に落ちない
- [ ] 10. 振る舞いが変わらない: `plugins/ndf` の Python は、コメントと docstring を除いた構文木が変更の前後で一致する。規則 `review-items` の入力のパスだけは例外とする
      前提の確認: `plugins/ndf/bin/ndf check` → `1768 ファイルに違反なし`（167ea3bbc）
- [ ] 11. 規則 `review-items` は、変更の前と同じ 182 件の項目とルートを確かめる（対応表の文書の名前だけは、保存先に合わせて替わってよい）。ルートの空の項目・対応表に無い 11.x の項目・生成し直した中身との違いを、変更の前と同じく違反にする
      前提の確認: `plugins/ndf/bin/ndf check --only review-items` → `ok`（167ea3bbc。`review_items` は 182 件）
- [ ] 12. 生成物（`plugins/ndf/docs/contracts/`・JSON の説明から作るページ・用語集の文書）が `ndf check --write-spec` と `ndf records glossary render --check` に合う
- [ ] 13. 退行しない: CI の必須のジョブが通る。足した文書は 1 本 500 行を超えない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（コマンド・引数・結果 JSON・終了コード・Skill の手順は変えない。`docs/contracts/` の説明文は、JSON の説明の書き換えで文だけが変わる） |
| データ | 無し。`review_items` の `routed_to` の値（対応表の文書の名前）は、入力の保存先が替われば替わる |
| 既存の振る舞い | 変わらない。コメントと文書の参照先、規則 `review-items` の入力のパス、子の表の SSOT の保存先が替わる |
| 書き換えで当たる検査 | 耐久ワークフローの関数の中のコメントを変えると、ソースの hash のテスト（`tests/core/test_durable_format.py`）が落ちる。hash を書き直すときは、呼び出しの順が変わらないことを確かめる。`# noqa` の理由の中の決定は、抑制コメントの形を保ったまま書き換える |
| 並行する変更 | `plugins/ndf` の多くのファイルのコメントの行に触るため、同じ計画のほかのレーンと衝突しうる |

## 用語

| 用語 | 意味 |
| --- | --- |
| 子の表 | #1142 の作り直しの子の Issue を、記号ごとに番号・依存・順番・閉じたかで持つ表（`children.json`） |
| 振り分け表 | #1142 の設計レビューの全項目について、ルートの子とバージョンを持つ表。規則 `review-items` の入力 |
| 11.x の対応表 | 11.x へ回した設計レビューの項目と、そのルートの表 |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 子の表の SSOT に欠けた値を載せた | 設計の承認 | `children.json` と違えば条件 7 が成り立たない | — |
| E2 | 「決定 N」の行を、作り直しの決定・別の設計の決定・決められない行に分けた | 実装の開始 | 決められない行は書き換えずに PR に挙げる（条件 5） | — |
| E3 | 作り直しの決定を指す行を確定仕様の形へ書き換えた | E2 | 実在しない決定を指せば条件 3 のコマンドが挙げる | E2 |
| E4 | 子の表と規則 `review-items` の参照を、消さない保存先へ向けた | E1 | 入力を読めなければ `ndf check` が `config_broken` で止まる | E1 |
| E5 | 36 本を消した状態で検査が通ることを確かめた | E3・E4 | 落ちた参照へ戻って直す | E3・E4 |
| E6 | 36 本を消した（#3990） | この Issue のマージ | #3990 が扱う | E5 |

E2 の後に、ほかのレーンのマージで「決定 N」の行が増えることがある。マージの直前に条件 1 と 3 のコマンドを打ち直して拾う。

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 参照の 0 件 | `git grep -n -E 'issue-(1142\|3347\|3395\|3388)-' -- plugins/ndf` が 0 件（変更の前は 7 ファイルに当たる） |
| 決定の実在 | 確定仕様 6 本の `決定 N` の見出しと、`plugins/ndf` の参照を比べるコマンド（形は設計で決める）。存在しない番号を 1 件混ぜると挙がることも確かめる |
| 子の表の一致 | `children.json` と SSOT の値を比べるコマンド（形は設計で決める） |
| 構文木の一致 | 変更の前後の `plugins/ndf` の `.py` を、docstring を除いて `ast.dump` で比べる |
| 36 本を消した状態 | 使い捨ての worktree で 36 本を消し、`plugins/ndf/bin/ndf check` と `python3 scripts/check-markdown-links.py` を打つ |
| テスト | `uv run --frozen --project plugins/ndf pytest plugins/ndf/tests -q` |
| 静的解析 | `plugins/ndf/bin/ndf check`・`python3 scripts/check-markdown-links.py`・`ndf records glossary render --check` |
| 手動確認（マージ前） | 対応の一覧（条件 4）から、番号だけの行を書き換えたものを抜き出し、コメントの中身と決定の中身が合うことをレビュー担当が見る |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 子の表の SSOT の保存先（`packages` と `tables` を別の確定仕様に置くか、`children.json` 自身を SSOT にするか） | 設計で選び、設計承認ゲートで利用者が確かめる | 設計承認ゲート |
| 規則 `review-items` の入力（振り分け表と 11.x の対応表）の、消さない保存先 | 設計で選び、設計承認ゲートで利用者が確かめる | 設計承認ゲート |

`ndf-rebuild.md` は今 500 行で、足すと 1 本の上限を超える（PR #3986 で試すと 542 行になった）。

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 書き換えの形は #3412 の設計の決定のとおりで、詳細は確定仕様 6 本で確かめる | 起票者（#3412 の承認資料の「作るもの」、2026-11-21） | 前提 1・受け入れ条件 2 |
| S2 | lz1-contract の行の順は #3955 の後とする | 起票者（計画 p9 のオーガナイザーの判断、2026-11-21） | 含まない（行の順は並列の計画が持つ） |
| S3 | `packages` と `tables` の表は、500 行に収まる別の確定仕様（例 `docs/specifications/ndf-rebuild-children.md`）へ置き、`ndf-rebuild.md` の「子の記号」から参照する。`children.json` 自身を SSOT にする案もある | 起票者（PR #3986 の cross-review r6-1・r6-2、2026-11-21） | 未決・受け入れ条件 7（どちらの案でも判定できる形にした） |
| S4 | 規則 `review-items` の入力も「設計文書へのパスの参照」に数え、36 本に依らない形にする | 要求を書いた worker（起票の後に #3684 が足した参照で、#3990 は `plugins/ndf` の書き換えをこの Issue に任せているため、2026-11-24） | 受け入れ条件 1・9・10・11 |
| S5 | #3344・#3345 など別の課題の番号で書かれた作り直しの決定（81〜86・88〜96）も書き換える | 要求を書いた worker（番号と中身が `ndf-rebuild.md` の決定と同じため、2026-11-24） | 受け入れ条件 2 |
| S6 | 子 M3b+ と X9 の行も SSOT に載せる | 要求を書いた worker（`issues/issue-3395-design.md` にだけあり、36 本を消すと失われるため、2026-11-24） | 受け入れ条件 7 |
| S7 | 対応の一覧を PR に載せ、決められない行は書き換えない | 要求を書いた worker（番号が同じでも中身の違う行があるため。決められない行の扱いは #3412 の詳細設計と同じ、2026-11-24） | 受け入れ条件 4・5 |
| S8 | 規則 `review-items` の入力のパスの変更は、「振る舞いを変えない」に反しないとみなす | 要求を書いた worker（条件 1 と「コードの振る舞いの変更を含まない」を両立させるため、2026-11-24） | 受け入れ条件 10・11 |

## リクエスト（原文）

書き手: 起票者（#3412 の設計承認ゲートの後にオーガナイザーが起票。追記は PR #3986 の cross-review の後）、日付: 2026-11-21。利用者の指示の原文は含まない。原文の受け入れ条件は、上の条件へ次のとおり移した: 1 行目 → 1・9、2 行目 → 2・3・4・5・6、3 行目 → 10・11・12・13、4 行目 → 7・8。

<details>
<summary>起票時の本文</summary>

> ## 目的
>
> #3412 は、作り直し（#1142）の設計文書を確定仕様 6 本（`docs/specifications/ndf-rebuild*.md`）へ移し、`issues/` の設計文書を消す（設計 PR #3952、2026-11-21 に設計承認）。設計書は、`plugins/ndf` のコメントが作り直しの決定を `docs/specifications/ndf-rebuild-<主題>.md の決定 N` の形で指すように直すとしている。
>
> 計画 p9 では、このコメントの書き換えだけをレーン ls3-spec から外した。対象は `plugins/ndf` の `決定 N` を指すファイル 155（`main` 6d4a28077 の `git grep -l -E "決定 [0-9]+" -- plugins/ndf`。src 91・tests 44・contracts 10・skills 6・hooks 2・bin 1・pyproject.toml 1）と、`issues/issue-(1142|3347|3395|3388)-` を指す 6 ファイルである。ls3-spec と同時に動く lsplit・lz13-deps・lz2-ci・lz1-contract のすべてと触る場所が重なる。この Issue はそれを lz1-contract の後ろの行で受け持つ。
>
> ## 対象範囲
>
> - 含む: `plugins/ndf` のコメント・docstring・README・JSON の説明の、作り直しの決定と設計文書への参照を、#3412 が作った確定仕様の決定へ書き換える
> - 含まない: 確定仕様の本文・`issues/` の設計文書の削除・計測（#3412 の ls3-spec が持つ）。コードの振る舞いの変更
>
> ## 受け入れ条件
>
> - `plugins/ndf` に、消した設計文書（`issues/issue-1142-*`・`issue-3347-*`・`issue-3395-*`・`issue-3388-*`）へのパスの参照が残らない
> - 作り直しの決定を指すコメントが、`docs/specifications/ndf-rebuild-<主題>.md の決定 N` の形で、その確定仕様に実在する決定を指す
> - 振る舞いが変わらない（テストと検査がそのまま通る）
> - `plugins/ndf/src/ndf/checks/README.md` の「子の表の規則」と `children.json` の `summary` が指す SSOT に、子ごとのサブパッケージ `packages` と共有の表の書き手 `tables` が実際に載っている。今の付け先（`issues/issue-3347-design-detail.md` の「実装計画」・`issues/issue-3395-design.md` の「構成要素」）はどちらも持たない（例 `ndf.core.refs` の持ち主 C4、`contracts/limits.json` の書き手 D1・T1+・M3a を復元できない）。直す先が無いままでは「表を変えるときは SSOT を直す設計 PR を先に通す」の規則が成り立たない
>
> ## 起票者の推定
>
> - 書き換えの形は設計書 https://github.com/devbasex/ai-plugins/blob/748e01e0d6b2ba24861e78eddcd4eaa3e8de3c96/issues/issue-3412-design.md の決定のとおりで、詳細は確定仕様 6 本（`docs/specifications/ndf-rebuild*.md`。PR #3964 で `main` へ入った）で確かめる（推した出所: 承認資料の「作るもの」）
> - lz1-contract の行の順は #3955 の後とする（推した出所: 計画 p9 のオーガナイザーの判断。lz1-contract は plugins/ndf/src・tests・contracts を既に持つ）
> - `packages` と `tables` の表は、500 行の上限に収まる別の確定仕様（例 `docs/specifications/ndf-rebuild-children.md`）へ置き、`ndf-rebuild.md` の「子の記号」から参照する。`ndf-rebuild.md` へ足すと 542 行になり上限を超える（PR #3986 で試して戻した）。`children.json` 自身を正本にする案もあり、どちらにするかは設計承認ゲートで確かめる（推した出所: PR #3986 の cross-review r6-1・r6-2）
>
> ## 由来
>
> issue #3412（設計 PR #3952 の設計承認ゲート、2026-11-21）。ls3-spec の触る場所を広げる `ndf lanes set` が、`plugins/ndf/` で他のレーンと重なる（I13）として断られたため、オーガナイザーが分けた。根拠: MVV 3「同じ手順は毎回同じ結果になる」（MVV バージョン 9）
>
> 追記の由来: PR #3986（cross-review r6-1・r6-2、2026-11-21）
>

</details>

書き手: 起票者（PR #3986 の作業からのコメントによる引き渡し）、日付: 2026-11-21。コメントの要点は「今の姿」の子の表と「未決」に反映した。原文は https://github.com/devbasex/ai-plugins/issues/3960 のコメントにある。
