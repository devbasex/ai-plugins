# #1767: supervise new sprint: 設計を付けない課題に「何をするか」と受け入れ条件が無くても実装のプランを組み、実装の worker が判断待ちで止まる

正は課題の本文（#1767）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> #1767。この要求へ次を取り込む: #1751（スプリントの実装のプランの sync が push 前の検査 validate-runtime-plugins.sh を含まず、pr のステップの push で落ちて止まる）。どちらも supervise new sprint が組む実装のプランの不足の問題として 1 本の設計で扱う。#1751 の受け入れ条件も同じ写し（issues/issue-1767-requirements.md）へ含め、#1751 の本文には #1767 の要求へ取り込んだことを書く。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

### 起票の時点の事実（#1767）

`supervise.py new sprint --issue 815 1273 1752 1685 659 --design 815 1273 1752` で組んだスプリント m815 で、`--design` を付けなかった #1685 の実装のプラン（`5-impl-1685.json`）が、最初の `impl` のステップで作業せずに止まった（25 秒・$0.275）。

- #1685 の本文は起票の形のままで、`## 何をするか` と `## 受け入れ条件` の節が無かった
- 実装の指示は「本文は `gh issue view 1685` で読む（何をするか と 受け入れ条件）」で、無い節を読む前提になっている（`plugins/ndf/scripts/supervise_lib/templates.py` の `impl_prompt`）
- worker の報告は `判断が要る`（「課題 #1685 には『何をするか』と『受け入れ条件』の節がありません」）
- 利用者が直し方を決め、conductor が本文を書いてから `5-impl-1685-r2.json` で流し直した
- 記録: `~/.local/state/ndf/sv/sprint-m815/5-impl-1685-state/01-impl.out`・`queue-5.log`

### 起票の時点の事実（#1751 から取り込んだもの）

スプリント m744 の実装のプラン（`5-impl-744.json`）で、`sync` のステップ（`supervise.py sync-check`）と範囲テストが通った後、`pr` のステップの push が `.githooks/pre-push` の検査で落ちた（exit=1。description の Skill 数の食い違い）。

| 原因 | 事実 |
| --- | --- |
| `sync` が push 前の検査を含まない | `.ndf/supervise.json` の `sync_checks` は build / frontmatter / line-limit / links / instructions の 5 本。`.githooks/pre-push` が打つ `scripts/validate-runtime-plugins.sh` と `scripts/check-lint.sh` が入っていない |
| `pr` のステップが `on_fail` を持たない | `plan_to_merge`（`templates.py`）の `pr` のステップに `on_fail` が無く、push が落ちるとプラン全体が `止まった` で終わる。`sync` には `fix-sync`、`doc-lint` には `fix-doc` がある |

conductor が手で直し、`run --from pr` で流し直した（`~/.local/state/ndf/sv/sprint-m744/run-5-pr.log`・`5-impl-744-state/06-pr.out`）。

## 目的

`supervise.py new sprint` が組む実装のプランが、実装の入力（課題の受け入れ条件）と push 前の検査の 2 点で欠けたまま流れ、キューの中で止まって conductor の手当て（利用者へ問う・本文を書く・手で直す・プランを流し直す）を要する状態を無くす。

- 受け入れ条件の無い課題は、組む時点（承認ゲート 1 より前）で見つかる。その課題を他の設計と一緒に承認できる
- push 前の検査で落ちる変更は、`sync` で見つかるか、`pr` のステップの修正で直り、プランが止まらない

## 前提

- 前提 1: 本文が実装の入力の形を持つかは `## 受け入れ条件` の見出しだけで決める。`## 何をするか` は見ない。`requirements-design` の雛形（`spec-template.md`）が `## 何をするか` を持たず、見ると要求を書き終えた課題を誤って止めるため
- 前提 2: `## 受け入れ条件` の見出しがあり、その節（次の `## ` まで）に空でない行が 1 行以上あれば満たす。項目の書き方（`- [ ]` か否か）は見ない
- 前提 3: 確かめるのは `new sprint`（normal / fast / auto の 3 つの進め方）だけである。`new close`（スプリントを閉じる計画）と単独の `new impl` / `new fix` は確かめない（`new impl` は課題を 1 つ指して conductor が組むもので、#1767 の経路ではない）
- 前提 4: `pr` のステップの失敗を直す経路は、`plan_to_merge` が組む実装のプラン（スプリントの実装・`new impl`・`new fix`）に置く。雛形を共有するため 3 つに同時に効く
- 前提 5: push 前の検査（`core.hooksPath` の `pre-push` フックが push の前に打つ検査）の中身はプロジェクトごとに違う。雛形（`supervise_lib/`）はこのリポジトリの検査のコマンド名を持たず、このリポジトリの検査は宣言（`.ndf/supervise.json`）で受ける（Value 5）
- 前提 6: `sync` へ足す検査は、2026-10-05 の実測で `scripts/validate-runtime-plugins.sh` が 11.2 秒、`scripts/check-lint.sh` が 21.2 秒である。`sync` は実装のプランごとに 1 回以上走るため、足すと 1 本あたり約 32 秒延びる

## 対象範囲

含む:
- `new sprint` が計画を書く前に、`--design` に無い課題の本文の `## 受け入れ条件` を確かめ、無ければ計画を書かずに止まる
- 実装のプランの `pr` のステップの失敗（push 前の検査の不合格）を、修正の worker で直して打ち直す経路
- このリポジトリの `.ndf/supervise.json` の `sync_checks` へ、`.githooks/pre-push` が打つ 2 本の検査を足す（C7。利用者の承認を取ってから）

含まない:
- `## 何をするか` の有無の確かめ（前提 1）
- 実装の指示文（`impl_prompt` の「何をするか と 受け入れ条件」）の書き換え
- 本文の足りない課題へ、受け入れ条件を自動で書き足すこと（直し方の判断が要るため。#1767 の m815 では利用者が決めた）
- `supervise.py sync-check` が `.githooks/pre-push` を読み、宣言に無い検査を知らせる仕組み。フックは任意のスクリプトで、打つコマンドを機械が確実に取り出せない（Value 5）。食い違いが残っても `pr` のステップの修正の経路で止まらない
- 設計のプラン・検査のプラン・確定仕様化の `pr` のステップの失敗の扱い（未決 1）
- 既に書き出したスプリントの計画の書き直し

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | `new sprint` が `--design` に無い課題の本文を取った | conductor が `new sprint` を打った | 本文を取れなければ計画を書かずに止まり、番号と理由を示す（受け入れ条件 3） | — |
| E2 | 受け入れ条件の無い課題を見つけて止まった | E1 の本文に `## 受け入れ条件` が無い | — | E1 |
| E3 | スプリントの計画を書いた | E1 の全課題が受け入れ条件を持つ | 既存のとおり | E1 |
| E4 | `sync` が push 前の検査の不合格を見つけた | 実装の worker のコミットの後、`sync` のステップ | 既存の `fix-sync` が直して `sync` を打ち直す | E3 の後の `impl` |
| E5 | `pr` のステップの push が push 前の検査で拒まれた | 宣言に無い検査の不合格 | 修正の worker が直し、`pr` を打ち直す（受け入れ条件 6） | 範囲テストの後 |
| E6 | 修正の繰り返しが上限に達してプランが止まった | E5 の修正で直らない | `止まった` で終わり、最後の push の出力を残す | E5 |

## 受け入れ条件

### 受け入れ条件の無い課題を組む時点で止める（#1767）

- [ ] 1. `supervise.py new sprint --issue A B --design A` で、B の本文に `## 受け入れ条件` が無いとき、結果の `status` が `stopped` で、スプリントの出力のディレクトリに `sprint.json` も計画のファイルも書かれない
- [ ] 2. 1 の結果の `summary` か `items` に B の番号が載り、`next` に「B を `--design` へ入れて打ち直す」と「B の本文に `## 受け入れ条件` を書いて打ち直す」の 2 つの手が載る。該当する課題が複数あれば全部の番号が 1 回の結果に載る
- [ ] 3. `--design` に無い課題の本文を `gh` で取れないとき、計画を書かずに `stopped` で止まり、取れなかった番号と `gh` の出力が結果に載る（確かめずに通さない）
- [ ] 4. `--design` に入っている課題は、本文に `## 受け入れ条件` が無くても止めない
- [ ] 5. `--design` に無い全課題の本文に `## 受け入れ条件` と空でない行があるとき、書かれる計画のファイルの中身がこの変更の前と同じである（normal・`--pace fast`・`--pace auto` のそれぞれ）

### push 前の検査の不合格でプランを止めない（#1751）

- [ ] 6. 実装のプラン（`plan_to_merge` が組むもの）の `pr` のステップが `on_fail` を持ち、push が落ちたとき、push の出力を入力に受けた修正の worker が起き、コミットした後に `pr` のステップを打ち直す。打ち直した push が通れば `test-all` へ進む
- [ ] 7. 6 の修正が直せないとき、プランは上限（`上限`）の中で `止まった` で終わり、無限に回らない。止まったときの記録に最後の push の出力が残る
- [ ] 8. 6・7 は `.ndf/supervise.json` に `sync_checks` を持たないプロジェクトの実装のプランでも同じに働く
- [ ] 9. `plugins/ndf/scripts/supervise_lib/` の雛形のコードに、このリポジトリの検査のコマンド名（`validate-runtime-plugins.sh`・`check-lint.sh`）が現れない
- [ ] 10. このリポジトリの `.ndf/supervise.json` の `sync_checks` に、`bash scripts/validate-runtime-plugins.sh` と `bash scripts/check-lint.sh` の 2 本が載る（利用者の承認を取ってから。C7）
- [ ] 11. 10 の後、plugin.json の description の Skill 数を manifests と食い違わせたworktreeで `python3 plugins/ndf/scripts/supervise.py sync-check --root .` を打つと終了コードが 0 でなく、落ちた項目の名前が結果に載る

### 退行しないこと

- [ ] 12. 既存のテスト（`uv run --frozen --project . --all-extras pytest plugins/ndf/scripts -q -n 4`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | `new sprint` の確かめは課題 1 件あたり `gh issue view` を 1 回までにする。`sync` の延びは前提 6 の実測（約 32 秒 / 本）を上限の目安とし、実装の PR で実測を示す |
| 運用・保守性 | 止まったときの結果は、conductor が次の手を本文の外を調べずに選べる形（番号・理由・打ち直すコマンド）で返す |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`new sprint` が受け入れ条件の無い課題で `stopped` を返すようになる（互換の経路は持たない。止まるのは前は キューの中で止まっていた入力だけ）。実装のプランの JSON に `pr` の `on_fail` と修正のステップが増える |
| データ | 変わらない |
| 既存の振る舞い | `new sprint` の結果・実装のプランの `pr` の失敗時の動き・このリポジトリの `sync` の所要 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts -q -n 4`（受け入れ条件 1〜9・12 は `gh` を差し替えた単体テストで確かめる） |
| 静的解析 | `bash scripts/check-lint.sh` |
| 手動確認 | 受け入れ条件 11 をworktreeで打って確かめる。受け入れ条件 10 の承認は利用者が設定の差分を見て判定する |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | スクリプトは `plugins/ndf/scripts/supervise_lib/`、宣言は `.ndf/supervise.json`（`AGENTS.md`・`plugins/ndf/scripts/supervise_lib/plan.py` の宣言の説明） |
| コーディング規約 | `bash scripts/check-lint.sh`（ruff・shellcheck） |
| テスト戦略 | 雛形とプランの組み立ては単体テスト。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、check-lint の適用 |
| 確認してから行う | `.ndf/supervise.json` の書き換え（C7。設計 PR の承認とは別に、利用者の明示の承認を取る。MVV への委任では省かない） |
| 行わない | 課題の本文への受け入れ条件の自動の書き足し、`.githooks/pre-push` の変更、依頼範囲外のリファクタリング |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 設計・検査・確定仕様化のプランの `pr` のステップにも同じ修正の経路を置くか（`pr_step` の共通の層へ寄せるか） | 設計（`design`） | 設計 PR |
| 2. push の失敗のうち、コミットを直しても通らないもの（認証・ネットワーク・先行の拒否）を修正へ回さずに止めるか、その見分け方 | 設計（`design`） | 設計 PR |
| 3. 受け入れ条件 10 の 2 本を `sync` に足すか、時間の重い `check-lint.sh` は範囲（変更したファイル）だけに掛けるか | 設計で案を出し、利用者が C7 の承認で決める | 設計 PR |

## 由来

issue #1685（スプリント m815、PR #1763 の振り返り）。#1751（PR #1745・スプリント m744 の振り返り）を 2026-10-05 にこの要求へ取り込んだ。
