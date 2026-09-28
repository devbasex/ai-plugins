# プロジェクト MVV（`.ndf/mvv.md`・`.ndf/mvv.json`）

プロジェクト全体の Mission / Vision / Value と固有の「必ず人の承認が要る操作」（`P<番号>`）を宣言し、NDF が判断する地点
（承認ゲートの MVV 判定・レビューの指摘と修正の可否・リファクタリングの提案の採否・judge のステップ・範囲外の起票）が
それに従う。承認済みなら、どの `pace`（`normal` / `auto` / `fast`）でも要求・設計・実装の工程も MVV を判断の基準として読む。
この文書は、手順 0 での扱い・策定・改訂・人へ示す文面の雛形・工程での読み方・記録の読み方を持つ。

## 例: ai-plugins でプロジェクト MVV を定め、承認ゲートがそれを読むまで

1. 手順 0 で `project-mvv.py check` が終了コード 2（「プロジェクト MVV が無い」）を返す。LLM も `gh` も呼ばず、何も書かない
2. `project-mvv.py collect` がコミット 3787 件・課題 574 件で履歴モードを選び、出典つきの材料 `materials.json` を
   リポジトリの外（`~/.local/state/ndf/project-mvv/<根の名前>/`）へ書く（実測 6.2 秒）
3. `project-mvv.py propose --materials <materials.json>` が候補 A・B と「利用者に決めてもらう点」を `candidates.md` に書く
4. conductor が `candidates.md` を示し、分かれる点を `AskUserQuestion` で問い、答えから本文を固める。
   ai-plugins では利用者が承認した `issues/mvv-ai-plugins.md` の文面がそのまま本文になる。**ここまで `.ndf/` に何も書かない**
5. `project-mvv.py vet --kind candidate --body <本文>` が「従う」（0）を返す
6. 利用者が同じ本文を承認した後に `project-mvv.py approve --body <本文> --by <承認者>` を打つ。`.ndf/mvv.md`（本文）と
   `.ndf/mvv.json`（版 1・sha256・日時・承認者・照合の記録）ができ、`check` が 0 を返す
7. 次の承認ゲートの `mvv-gate.py check` が、NDF の共通原則 → プロジェクト MVV（版 1）→ スプリント MVV の順の材料で判定し、
   `mvv-gate.jsonl` に `"project_mvv": {"status": "approved", "version": 1, ...}` と `"basis": ["Value 1"]` を残す

## 置き場と層

| 層 | 置き場 | 誰が変えるか |
| --- | --- | --- |
| NDF の共通原則（上位の原則・優先順位・AI の行動の 2 択・判断の範囲と記録・人と AI の対話・C1〜C8） | 配布物の `scripts/data/ndf-common-principles.md` | NDF のリリースだけ。プロジェクトは上書きも除外もできない |
| プロジェクト MVV（Mission / Vision / Value・固有の操作 `P<番号>`） | `.ndf/mvv.md`（本文）と `.ndf/mvv.json`（版の履歴。形は `schemas/mvv.schema.json`） | `project-mvv.py approve` だけ（利用者の承認の後） |
| スプリント MVV（`R<番号>` を含む） | スプリント状態ファイルの隣の `mvv.md` | `sprint-state.py init`（`fast` / `auto` は必須で、写せなければ止まる。`normal` は `--mvv` か `--milestone` から写せたときだけ写し、止めない。[pace.md](pace.md)） |

判断の地点へ渡す MVV の節は、共通原則の本文全体 → プロジェクト MVV（無ければ「MVV なし」とその理由）→ スプリント MVV →
判断の決まり（取れる行動は「進める」か「止めて人へ戻す（理由と根拠つき）」の 2 つ・根拠の項目の番号を返す）の順で、
`project-mvv.py context` で同じ文を読める。スプリント MVV の節は、プロジェクト MVV が承認済みで、スプリントが特定できるとき
（`context --sprint <状態>` / `--mvv <ファイル>` / `--milestone M`）だけ入る。見出しに本文の sha256 の先頭 8 文字が付き、
その項目は根拠の欄で頭に「スプリント」を付ける（`スプリント Value 4`。`R<番号>` はそのまま）。特定できなければ止めずに
プロジェクト MVV だけの節になる。

本文の形: `## Mission` / `## Vision` / `## Value`（番号つきの箇条 `1. ...`）は必須。固有の操作は `## 必ず人の承認が要る操作`
の表に `| P1 | 操作 | 理由 |` で書く。共通原則の写し（`## 上位の原則`・`## 優先順位` の節・`| C<番号> |` の行）は持てない。
ほかの節（共通原則との関係・改訂など）は持ってよい。

## 手順 0 での扱い（`project-mvv.py check`）

| 終了コード | 意味 | 次に行うこと |
| --- | --- | --- |
| 0 | 承認済みで、本文が最後の版と一致する | 進む |
| 2 | 無い | 止めずに進む。利用者が MVV を定めると決めたら下の「策定」へ入る（判断の地点は共通原則と「MVV なし」で動く） |
| 3 | `.ndf/mvv.json` が壊れている | 出力を示して利用者に直してもらう。`approve` は壊れた宣言に版を足さない |
| 4 | 承認と一致しない（`mvv.md` が承認の後に変わった）・未承認（`mvv.md` だけがある） | 本文を戻すか、「改訂」（未承認なら「策定」の 4 から）へ入る。直るまで MVV 判定は承認ゲートを省かない |

## 策定

| 順 | 打つもの・すること | 失敗したとき |
| --- | --- | --- |
| 1 | `project-mvv.py collect [--request-file <依頼文>]` | 欠け（`git` が無い・`gh` が読めない）は `missing` に残り、集めた材料で進む。宣言の `settings` が壊れていれば 3 |
| 2 | `project-mvv.py propose --materials <materials.json>` | 1: 候補の形が足りない・LLM が返さない。`materials.json` を示して利用者に直接問う |
| 3 | `candidates.md` を示し、「利用者に決めてもらう点」を `AskUserQuestion` で問う（下の雛形の形） | 答えが割れたら問い直す |
| 4 | 答えから本文を組み、作業ディレクトリ（リポジトリの外）に置く。候補ごとの本文の案は `candidate-<ID>.md` にある | — |
| 5 | `project-mvv.py vet --kind candidate --body <本文>` | 10「反する疑い」: 箇所（`C4`・`priority` など）を示して本文を直し、5 をやり直す。10「判定できない」: 引き受けるかを利用者に問う |
| 6 | 本文を示して承認を問う（`AskUserQuestion`） | 承認されない: 3 へ戻る。`approve` を打たない |
| 7 | `project-mvv.py approve --body <本文> --by <承認者> [--accept-unknown]` | 1: 照合が無い・本文が照合の後に変わった・形が足りない。5 から |
| 8 | `.ndf/mvv.md`・`.ndf/mvv.json` をコミットし、通常の Pull Request で届ける | 宣言を変える Pull Request は MVV 判定で通らず、承認ゲートで利用者が見る（共通原則の C7） |

**承認の前に `.ndf/` へ書かない。** `collect` と `propose` の出力はリポジトリの外に置く。`approve` は同じ本文への直近の照合が
「従う」（か、人が引き受けた「判定できない」）のときだけ書く。「反する疑い」の本文は利用者が望んでも承認へ進めない
（上位の原則が利用者の承認より上にある）。

**履歴が少ないとき（傾向モード）。** コミット数と課題数の両方が閾値（既定 100 件・20 件。`.ndf/mvv.json` の
`settings.trend_commits` / `trend_issues` か `--trend-commits` / `--trend-issues` で変える）に満たないと、
README・指示書（`AGENTS.md` / `CLAUDE.md` など）・依頼文（`--request-file`）だけを材料にする。

**Claude Code 以外のランタイム。** `AskUserQuestion` が無ければ、同じ雛形の文面を会話で示して問う。

## 改訂

契機は 3 つで、どれも同じ手順へ入る。

| 契機 | 出どころ |
| --- | --- |
| 振り返りの問い「MVV を見直すか」 | `retrospective` が `project-mvv.py signals` の集計を示して問う |
| 改訂の兆候が閾値を超えた | `mvv-gate.py check` の結果の `items` の `kind: revise`（同じ版のもとで「判定できない」が `settings.unknown_streak` 回続いた、または `signals` の閾値を超えた） |
| 利用者の指示 | — |

| 順 | 打つもの・すること |
| --- | --- |
| 1 | 理由をファイルに書き、`project-mvv.py collect` → `project-mvv.py propose --materials <materials.json> --current --reason-file <理由>` |
| 2 | 改訂案（`candidates.md`。末尾に現行との差分）を示し、分かれる点を問う |
| 3 | `project-mvv.py vet --kind revision --body <本文>` |
| 4 | 承認を問い、承認の後に `project-mvv.py approve --body <本文> --by <承認者> --reason <理由>` |

`approve` は版を 1 つ上げ、理由と前の版との差分（`changes`。項目の単位で added / changed / removed）を追記する。過去の版は
書き換えない。`project-mvv.py show --version N` で版 N の本文を、`show --diff M` で版 M から現行への差分を出す。
改訂で本文の sha256 が変わると、承認済みだったスプリントの `pace: fast` は次の `new sprint --pace fast` と承認ゲートで
参照の食い違いを検出し、利用者の承認へ戻る。

## 人へ示す文面の雛形（候補の提示・改訂の提案・承認ゲートの資料）

人に判断や承認を求める文面は、次の 4 節をこの順に先頭へ置く（共通原則の「人と AI の対話」）。`propose` の
`candidates.md` はこの形で出る。承認ゲートの資料と改訂の提案は conductor がこの形で組む。

| 節 | 書くこと |
| --- | --- |
| 経緯 | なぜ今この判断が要るか・ここまでに何を決めたか |
| 前提 | この答えで何が決まり、何が決まらないか |
| 根拠 | 判断の材料（ファイルと行・課題の番号・コミットの件名・判定の記録）。URL とパスを省かない |
| 選択肢 | 選べるものと、選んだときに決まること。推奨があれば理由を添える |

内部の用語（MVV・版・照合・覆し・傾向モードなど）は言い換えるか、その場で 1 行の説明を付ける。

## 判断の地点と記録

| 地点 | MVV の節の渡り方 | 渡した事実と根拠の記録 |
| --- | --- | --- |
| 承認ゲートの MVV 判定（`auto` / `fast`） | `mvv-gate.py` の材料の先頭 | `mvv-gate.jsonl` の `project_mvv`・`basis`・`pace`・`sprint_mvv`。「従う」でレッドラインが無ければ状態の承認ゲートの記録（`by: mvv`） |
| 承認ゲートの助言の MVV 判定（`normal` と `supervise.py new sprint --state`） | `mvv-gate.py check --advise` の材料の先頭 | `mvv-gate.jsonl` の `pace: normal` の行。判定の記録を承認ゲート 1 では設計 PR のコメント、承認ゲート 2 では承認資料の末尾へ置く。承認ゲートの記録は書かない（承認は利用者） |
| supervise の `work` のステップ（要求・設計・実装・修正の worker） | worker のシステムプロンプトの末尾（プロジェクト MVV が承認済みのときだけ。プランの `スプリント状態` があればスプリント MVV も） | 記録しない（worker の判断は下の 3 行の記録に残る） |
| `requirements-design` | 下の「工程での読み方」 | 反する疑いは作業の報告の理由（根拠の項目つき） |
| `design` | 下の「工程での読み方」 | 設計文書の決定ごとの「根拠:」の行。反する疑いは作業の報告の理由 |
| `tdd-cycle` | 下の「工程での読み方」 | 反する疑いは作業の報告の理由（根拠の項目つき） |
| cross-review の担当・修正担当・見送りの返信 | `init` が状態ファイルの `review_criteria.mvv_block` へ写し、`reviewer_block` / `fixer_block` の後ろに付く | 状態の `review_criteria.project_mvv`。振り分けの `mvv_basis`・見送りの返信の末尾の「根拠: Value 1（MVV 版 1）」 |
| cross-refactoring の提案と改修計画 | 状態の `project_mvv.block` を `RF_MVV` で | 状態の `project_mvv.ref`。候補・項目・見送りの `mvv_basis`（改修計画の見送りの表の「根拠（MVV）」） |
| supervise の judge | プロンプトの先頭（`work` のステップと同じ節） | `state.json` の `project_mvv`・`log[].basis` |
| 範囲外の起票（`out-of-scope`） | 3 択の前に `project-mvv.py context` を読む | 起票の本文の「なぜこの変更の範囲外なのか」の節に根拠の項目と版 |

根拠の項目は `Mission` / `Vision` / `Value 3` / `C4` / `P1` / `R2` / `スプリント Value 4` の形で、本文に無い番号は落とす。
返されなければ「根拠なし」、プロジェクト MVV が無ければ「MVV なし」と入り、欠けても止めない。宣言が 未承認・承認と一致しない・
壊れている とき、mvv-gate は LLM を呼ばずに承認ゲートへ戻し（終了コード 10）、助言の MVV 判定は LLM も行も判定の記録も
出さずに「MVV なし」（`verdict: none`）を返し、ほかの地点は「MVV なし」で進む。

### 工程での読み方（`requirements-design`・`design`・`tdd-cycle`）

1. MVV の節を読む。supervise の worker はシステムプロンプトの末尾にある。無ければ `project-mvv.py context` を打つ
   （課題にマイルストーンがあれば `--milestone M`、起動指示がスプリント MVV のファイルを名指せば `--mvv <ファイル>` を付ける）。
   「MVV なし」なら、この手順を飛ばして今どおり進む
2. 工程の判断（要求と範囲・設計の決定・実装の選択）を MVV と突き合わせる。優先順位は判断の決まりのとおりで、プロジェクト MVV が
   スプリント MVV に勝つ
3. 反する疑いがあれば、その判断を書かずに人へ戻す。supervise の worker は作業の報告を「結果: 判断が要る」で終え、理由と
   根拠の項目（両方の MVV に関わるなら両方。例: `スプリント Value 7` と `Value 2`）を書く。対話の中では `decision-request` の形で示す
4. 根拠の行の形は見送りの返信と同じ「根拠: Value 6（MVV 版 1）」。スプリント MVV の項目があれば括弧にその sha256 の先頭 8 文字を
   足す（「根拠: Value 6 / スプリント Value 4（MVV 版 1・スプリント MVV 3f9a1c2e）」）。当たる項目が無ければ「根拠: 根拠なし（MVV 版 1）」、
   MVV が無ければ「（MVV なし）」

### 改訂の兆候

| 兆候 | 記録 | 書き手 |
| --- | --- | --- |
| 覆し（承認ゲートで「従う」を退けた `override_reject`・「反する疑い」か「判定できない」を通した `override_pass`） | `~/.local/state/ndf/project-mvv-signals.jsonl` | `sprint-state.py gate <状態> <承認ゲート> --what <要約> --by user [--pr N] [--outcome approved\|rejected]`（直前の MVV 判定と食い違うときだけ 1 行。`--pr` を渡せばその PR の判定と比べる。助言の MVV 判定も同じ） |
| 「判定できない」 | `mvv-gate.jsonl`（`pace` で進め方を分けられる。集計は分けずに数える） | `mvv-gate.py` |
| 流出不具合 | 検査の記録の `kind: escape` | `check-trigger.py escape` |

`project-mvv.py signals` は現行の版の承認の日時より後の記録だけを数え、`settings.revise_after`（既定 覆し 3・「判定できない」5・
流出不具合 3）と比べる。照合の履歴は `~/.local/state/ndf/project-mvv.jsonl` にある。記録の置き場は `NDF_MVV_STATE_DIR` で変えられる。
