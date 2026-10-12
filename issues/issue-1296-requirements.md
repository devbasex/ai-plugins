# #1296: supervise.py run --from で検査のプランを再開すると、前の実行が作った Pull Request を失い「{pr} を置き換える Pull Request がまだ無い」で止まる

正は課題の本文（#1296）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> スプリントの検査のプランを `supervise.py run <プラン> --from refactor` で途中から流し直すと、`{pr}` が埋まらずに止まった。前の実行では Pull Request を作っていて、その報告にも Pull Request の URL が残っていた。

> - 1 回目の報告（`3-check.log`、20:15:53）には `- Pull Request: https://github.com/devbasex/devbase/pull/308` がある
> - 再開の報告（`3-check-rerun.log`、20:16:17）は `- Pull Request: 無し` で、`report.md` を上書きした
> - 回避: conductor が Pull Request の番号を直書きしたプラン（`3-check-308.json`）を別の状態の置き場で流した

> ## 直し方の案
>
> - 状態の置き場を絶対パスへ解決してから `report.md` を読む
> - 上書きされる `report.md` ではなく、`progress.jsonl` の `pr` のステップの要約（URL）から Pull Request を読み直す。再開の報告が `Pull Request: 無し` で `report.md` を上書きしても、2 回目の再開で失わない

> ## 現状（ndf 10.17.68 で確認）
>
> - `--state-dir` を相対で渡したときも、省いて既定の `<プラン>-state` を使ったときも、同じ cwd から `--from` で再開すると `report.md` の Pull Request を読み直し、`{pr}` が埋まる。engine はプロセスの cwd を変えないため、相対パスの見立てだけでは起きない
> - 残る原因の候補は 2 つで、どちらも確かめていない: 1 回目と再開とで cwd が違い、渡したパスが別のディレクトリを指した / 1 回目が `report.md` を書く前に止まった
> - 原因によらず、`_restore_pr` が上書きされる `report.md` だけを読む弱さは残る。`progress.jsonl` の `pr` のステップの要約から読み直す案はこれを直す

（書き手: 起票者（devbasex/devbase issue #273 / PR #308 の振り返りで拾った AI）、日付: 起票時。「現状」の節は ndf 10.17.68 で確かめた起票者の追記。利用者の指示の原文は無い。起票時の本文の全文は下の「起票時の記録」に残す）

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 原因は状態の置き場が相対パスで、cwd に依存して `report.md` を読み外したこと | 起票時の「見立て」。10.17.68 の確認で「同じ cwd では起きない」と否定された | 採らない（原因の特定はこの課題の受け入れ条件にしない。前提 2） |
| S2 | 状態の置き場を絶対パスへ解決してから読む | 「直し方の案」の 1 つ目 | 含まない（同じ cwd では既に読めており、cwd が違う再開は別のディレクトリを指すため、絶対パスにしても直らない。前提 2） |
| S3 | `report.md` ではなく `progress.jsonl` の `pr` のステップの要約から読み直す | 「直し方の案」の 2 つ目 | 受け入れ条件 1・2 の振る舞いとして採る。読む元（`progress.jsonl` か別の記録か）は設計が決める（前提 3） |

## 目的

- `supervise.py run <プラン> --from <ステップ>` で再開したとき、前の実行の pr のステップが作った Pull Request を `{pr}` / `{pr_url}` に使い、Pull Request を作った後のステップから番号を直書きしたプランを作らずに再開できるようにする
- 再開が 1 度失敗して `report.md` が `Pull Request: 無し` で上書きされても、2 度目の再開で Pull Request を失わない

## 前提

- 前提 1: 対象は `supervise.py run` の `--from` による再開だけである。`queue` が先行のプランの報告から読む `{queue_pr:<計画名>}` / `{queue_prs}` の経路は変えない
- 前提 2: 1 回目と再開とで同じ状態の置き場（同じ cwd から同じ `--state-dir`、または既定の `<プラン>-state`）を指すことを再開の条件とする。cwd が違い別のディレクトリを指した再開は、この課題では直さない（devbase の事例の原因は特定できておらず、特定は受け入れ条件にしない）
- 前提 3: Pull Request の読み直しの元は、再開の報告で上書きされない記録とする。どの記録を正にするか（`progress.jsonl` の pr のステップの行・`state.json` の記録・新しい記録）は設計が決める。`progress.jsonl` の `summary` は出力の最後の空でない行で 160 文字に切られ、`gh pr create` の stderr に行があると URL にならないことに設計は注意する
- 前提 4: プランに `"Pull Request"` が書かれているときは、今と同じくその値を使い、読み直さない

## 対象範囲

含む:
- `--from` で再開するときの Pull Request の読み直し（`Engine._restore_pr` に当たる振る舞い）
- 読み直しの元が上書きされないこと（再開の報告が `Pull Request: 無し` を書いても次の再開で失わない）
- 読み直せずに `{pr}` で止まるとき、止まった理由から読んだ状態の置き場が分かること

含まない:
- 状態の置き場を絶対パスへ解決し直す変更（S2）
- devbase の事例の原因の特定
- `--from` を付けない実行・耐久ワークフローの続き（落ちた前の起動を続ける経路）の振る舞いの変更
- `fill_pr` の置き換えの規則（`{pr}` は番号、`{pr_url}` は URL）の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 1 回目の実行が pr のステップで Pull Request を作った（または既存の PR を見つけた） | プランの pr のステップ | PR を作れなければそのステップが落ち、URL は残らない | — |
| E2 | Pull Request の URL を上書きされない記録へ残した | E1 の成功 | 書けなければ再開で読み直せない（受け入れ条件 4 の理由で分かる） | E1 |
| E3 | 1 回目の実行が後のステップで止まり、報告（`report.md`）を書いた | 後のステップの失敗 | 報告を書く前にプロセスが落ちることがある（起票者の候補の 1 つ） | E1 |
| E4 | 利用者か conductor が `--from <ステップ>` で再開した | 人か conductor の操作 | 別の状態の置き場を指すと記録を読めない（前提 2） | E3 |
| E5 | 再開が前の実行の Pull Request を読み直した | E4（プランに `"Pull Request"` が無いとき） | 読めなければ `{pr}` を含むステップで止まる | E2・E4 |
| E6 | 再開が `{pr}` / `{pr_url}` を置き換えてステップを流した | E5 | — | E5 |
| E7 | 再開が止まり、報告を書き直した | 再開のステップの失敗 | — | E4 |
| E8 | 2 度目の再開が同じ Pull Request を読み直した | E7 の後の `--from` | 読めなければ E5 と同じ | E2・E7 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 状態の置き場 | `supervise.py run` の `--state-dir`（省けば `<プラン>-state/`）。`progress.jsonl`・`report.md`・`state.json` を置く |

## 受け入れ条件

- [ ] 1. プランに `"Pull Request"` が無く、同じ状態の置き場の前の実行で pr のステップが Pull Request を作っていたとき、`--from <pr より後のステップ>` で再開すると、そのステップの `cmd` / `args` の `{pr}` が前の実行の PR の番号に、`{pr_url}` が URL に置き換わり、「cmd の {pr} を置き換える Pull Request がまだ無い」で止まらない
- [ ] 2. 受け入れ条件 1 の再開が止まって `report.md` を書き直した後（または `report.md` を消した後）に、同じ状態の置き場でもう 1 度 `--from` で再開しても、受け入れ条件 1 と同じ Pull Request で `{pr}` が置き換わる
- [ ] 3. 受け入れ条件 1・2 の再開の報告（`report.md` と標準出力の `## フェーズの報告`）の `- Pull Request:` の行が、前の実行の PR の URL になる
- [ ] 4. 前の実行が pr のステップを成功させていない状態の置き場で `--from` で再開し `{pr}` を含むステップに当たったときは、今と同じく止まり、止まった理由の文に読んだ状態の置き場の絶対パスが含まれる
- [ ] 5. プランに `"Pull Request"` が書かれているときは、前の実行の記録に別の PR があっても、プランの値で `{pr}` が置き換わる（退行しない）
- [ ] 6. 前の実行で pr のステップが 2 回以上成功していたときは、最後に成功した回の Pull Request を使う
- [ ] 7. 既存の `supervise` のテスト（`plugins/ndf/scripts/tests/` の supervise に関わるもの）がすべて通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 読み直しに LLM も GitHub への問い合わせも使わない（状態の置き場のファイルだけで決まる。`{pr_url}` の URL を番号から引く既存の経路は除く） |
| 移行性 | 修正より前の版が書いた状態の置き場（`report.md` にだけ PR が残るもの）から再開しても、今と同じく `report.md` から読み直せる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（`supervise.py run` の引数と報告の形は同じ） |
| データ | 状態の置き場に PR を残す記録の形が増えるかは設計が決める。既存の記録の形は変えない |
| 既存の振る舞い | `--from` の再開で `{pr}` が埋まる場合が増える。止まるときの理由の文に状態の置き場のパスが加わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`（受け入れ条件 1〜6 は `gh` を偽物に替えた状態の置き場の再開のテストで確かめる） |
| 静的解析・型検査 | リポジトリの CI と同じ検査（`.github/workflows/` の lint） |
| 手動確認 | リリース後、pr のステップの後で止まったプランを `--from` で再開し、`{pr}` が埋まることを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 変更は `plugins/ndf/scripts/supervise_lib/` と `plugins/ndf/scripts/tests/` に置く。生成物は手で直さない（`AGENTS.md`） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`.md` の文言を照合するテストは書かない |
| テスト戦略 | 再開の振る舞いを単体の階層（Engine と状態の置き場、`gh` は偽物）で確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、変更範囲内の命名の統一 |
| 確認してから行う | 状態の置き場へ新しい記録の形を足すこと（承認ゲート 1 の設計で見る） |
| 行わない | `queue` の PR の受け渡し・耐久ワークフローの続きの経路・`fill_pr` の置き換えの規則の変更 |

## 未決

- devbase の事例の原因（cwd が違った / 1 回目が `report.md` を書く前に止まった）は確かめていない。受け入れ条件 4 の理由の文で、次に起きたときに読んだ場所が分かるようにする
- 読み直しの元をどの記録にするかは設計が決める（前提 3）

## 起票時の記録

<details>
<summary>起票時の本文（書き手: 起票者）</summary>

### 何を見つけたか

スプリントの検査のプランを `supervise.py run <プラン> --from refactor` で途中から流し直すと、`{pr}` が埋まらずに止まった。前の実行では Pull Request を作っていて、その報告にも Pull Request の URL が残っていた。

devbasex/devbase のスプリント issue-273 の `3-check.json`（ndf 10.17.29）の状態ディレクトリ `3-check-state/progress.jsonl`:

| 時刻 | ステップ | exit | 要約 |
| --- | --- | ---: | --- |
| 20:15:51 | pr | 0 | https://github.com/devbasex/devbase/pull/308 |
| 20:15:53 | refactor | 1 | --scope にテストの置き場所が含まれていません（別件） |
| 20:16:17 | refactor（`--from refactor` で再開） | 2 | cmd の {pr} を置き換える Pull Request がまだ無い |

- 1 回目の報告（`3-check.log`、20:15:53）には `- Pull Request: https://github.com/devbasex/devbase/pull/308` がある
- 再開の報告（`3-check-rerun.log`、20:16:17）は `- Pull Request: 無し` で、`report.md` を上書きした
- 回避: conductor が Pull Request の番号を直書きしたプラン（`3-check-308.json`）を別の状態ディレクトリで流した

**原因はまだ分かっていない。** 次のことは確かめた。

- `plan_sprint_check`（`sprint_waves.py` 227 行。232 行で外す）は、プランから `Pull Request` を外す。この値は pr のステップが実行中に埋める
- 再開のとき、`Engine._restore_pr`（`engine.py` 216〜221 行。171 行から呼ぶ。10.17.29 では `supervise.py` 1625 行）は `self.state.dir / "report.md"` の `^- Pull Request: (\S*/pull/\d+)` から番号を読み直す。この処理は 10.17.29 にもある
- 再開の報告は、状態ディレクトリを相対パス（`.cross_review/mission-273/3-check-state/...`）で表示していた。1 回目は絶対パスだった。どのパスで `report.md` を読んだかは確かめていない
- 見立て: `self.state.dir` が相対パスのとき、`report.md` は cwd に依存して解決される。再開の cwd が 1 回目と違うと `report.md` を読めず、`{pr}` が埋まらない

### どこで見つけたか

- `plugins/ndf/scripts/supervise_lib/engine.py` の `_restore_pr`（216〜221 行。再開のとき Pull Request を読み直す部分）
- `plugins/ndf/scripts/supervise_lib/steps.py` の `fill_pr`（69 行の文言。73 行で `/pull/` を判定する）
- devbasex/devbase PR #308 の検査のプラン

### 直し方の案

- 状態ディレクトリを絶対パスへ解決してから `report.md` を読む
- 上書きされる `report.md` ではなく、`progress.jsonl` の `pr` のステップの要約（URL）から Pull Request を読み直す。再開の報告が `Pull Request: 無し` で `report.md` を上書きしても、2 回目の再開で失わない

### なぜこの変更の範囲外なのか

devbase の変更の受け入れ条件と関係しない。NDF の supervise の再開の不具合である。#1263 は完了した状態が残っているときに前回の結果を返す件で、ここで扱う「再開すると Pull Request を失う」とは別の問題である。

### 直さないと何が起きるか

- Pull Request を作った後のステップで止まったプランは、`--from` で再開できない。番号を直書きした別のプランを作る必要がある
- 再開の報告が `report.md` を `Pull Request: 無し` で上書きする。そのため 2 回目の再開も同じように失敗する

### 由来

devbasex/devbase issue #273 / PR #308（振り返りで拾った）

### 現状（ndf 10.17.68 で確認）

- `_restore_pr` は `supervise_lib/engine.py` の 288〜293 行（169 行の `setup` から呼ぶ）、`fill_pr` は `supervise_lib/steps.py` の 62〜69 行にある
- `--state-dir` を相対で渡したときも、省いて既定の `<プラン>-state` を使ったときも、同じ cwd から `--from` で再開すると `report.md` の Pull Request を読み直し、`{pr}` が埋まる。engine はプロセスの cwd を変えないため、相対パスの見立てだけでは起きない
- 残る原因の候補は 2 つで、どちらも確かめていない: 1 回目と再開とで cwd が違い、渡したパスが別のディレクトリを指した / 1 回目が `report.md` を書く前に止まった
- 原因によらず、`_restore_pr` が上書きされる `report.md` だけを読む弱さは残る。`progress.jsonl` の `pr` のステップの要約から読み直す案はこれを直す

</details>
