# プロジェクト MVV（`.ndf/mvv.md`・`.ndf/mvv.json`）

プロジェクト全体の Mission / Vision / Value と固有の「必ず人の承認が要る操作」（`P<番号>`）を定義する。11 系の NDF では、
承認ゲートの MVV 判定（[pace.md](pace.md) の「MVV 判定」）が、どの進め方でもこれを材料に読んで判定し、判定を承認資料に載せる。
`pace: fast`・`auto` では、設計承認ゲートを省けるかもこの判定で決まる。
この文書は、ファイルの形・状態・手順 0 での扱い・工程での読み方・改訂の兆候を持つ。

## 例: 承認ゲートがプロジェクト MVV を読むまで

1. 利用者が承認した本文を `.ndf/mvv.md` に、承認の記録（バージョン 1・本文の sha256・日時・承認者）を `.ndf/mvv.json` に置き、
   通常の Pull Request で届ける
2. 手順 0 の `ndf records preflight` が MVV の状態を `approved` と読んで通る
3. `ndf progress new s12 --mode standard --issues 12 --pace auto` が承認の記録の sha256 をスプリントの文脈（`mvv_ref`）へ書く
4. 設計承認ゲートの直前の `ndf records mvv gate --gate design` が、NDF の共通原則 → プロジェクト MVV → PR の本文と差分 の材料で判定する

## 保存先と層

| 層 | 保存先 | 誰が変えるか |
| --- | --- | --- |
| NDF の共通原則（上位の原則・優先順位・C1〜C8） | プラグインの `contracts/data/ndf-common-principles.md` | NDF のリリースだけ。プロジェクトは上書きも除外もできない |
| プロジェクト MVV（Mission / Vision / Value・固有の操作 `P<番号>`） | `.ndf/mvv.md`（本文。保存先は `mvv.json` の `body`、既定 `mvv.md`）と `.ndf/mvv.json`（承認の記録。形は `contracts/schemas/ndf-settings/mvv.schema.json`） | 利用者の承認の後に書き、Pull Request で届ける。設定を変える PR は MVV 判定で省かれず（検査 5。共通原則の C7）、承認ゲートで利用者が見る |

本文のセクションは `contracts/schemas/ndf-settings/mvv-body.sections.json` の順で、表題（`# <プロジェクト名> のプロジェクト MVV`）・
`## Mission`・`## Vision`・`## Value`（番号つきの項目）が必須、`## NDF の共通原則との関係`・`## 必ず人の承認が要る操作`
（`| P1 | 操作 | 理由 |` の表）・`## 改訂` は任意である。MVV 判定は共通原則とこの本文の表からレッドラインを引き、材料の先に置く。

`.ndf/mvv.json` は `version`（`1`）と `versions`（1 件以上）を持つ。`versions` の 1 件は `version`・`sha256`（本文の sha256）・
`approved_at`・`approved_by`・`reason`・`validation`・`changes`・`body` を持ち、**最後の 1 件が承認された今の本文**である。
任意の `settings.revise_after` で改訂の兆候の閾値（下）を変える。

## 状態と手順 0 での扱い（`ndf records preflight`）

状態は、`.ndf/mvv.json` の最後のバージョンの `sha256` と本文の sha256 で決まる。本文の改行が LF か CRLF かだけの違い（Git の改行の変換）は、同じ本文として扱う。

| 状態 | 条件 | 手順 0 の確かめ | MVV 判定 |
| --- | --- | --- | --- |
| `approved` | 最後のバージョンの `sha256` が本文と一致する | 通る | 判定する |
| `absent` | `.ndf/mvv.json` が無い | 通る | 判定しない（検査 1） |
| `unapproved` | `versions` に `sha256` が無い | 通る | 判定しない（検査 1） |
| `mismatch` | 本文が承認の後に変わった | 止まる。本文を承認した形へ戻すか、承認し直して記録を足す | 判定しない（検査 1） |
| `unreadable` | 設定か本文を読めない | 止まる | 判定しない |

MVV 判定の列は、判定する PR のベースブランチの状態で決まる（作業場所の状態ではない）。判定しないときも、承認ゲートは人へ問う。

旧い形のキーが残る `.ndf/mvv.json` も、手順 0 の確かめが止まる。

## 工程での読み方

要求・設計・詳細設計・実装・リファクタリング・レビュー・完了判定の担当は、作業の前に承認済みの MVV を読み、自分の判断がそれに
反しないようにする。読むのは**ベースブランチで承認された本文**である。作業場所の `.ndf/mvv.md` は読まない。作業場所の本文は、
承認の前の改訂を含んでいることがある。

### 承認済みの MVV を受け取る

起動指示に `MVV:` で始まる行があれば、その行のパスを読む。無ければ、担当が自分で次のコマンドを打つ。`--base` には、作業の
Pull Request のマージ先のブランチを渡す。Pull Request がまだ無ければ、作業場所のブランチの起点にしたブランチを渡す。

```bash
ndf records mvv current --base <マージ先のブランチ>
```

このコマンドはリモートからブランチを取得し、そのブランチの MVV の本文と NDF の共通原則をファイルに保存する。保存したファイルを
**MVV のスナップショット**と呼ぶ。結果 JSON の `outputs.mvv` は次の形になる。

```json
{"state": "approved", "version": 11, "sha256": "<64 桁>", "path": "<本文のスナップショット>",
 "principles_path": "<共通原則のスナップショット>", "items": ["Mission", "Vision", "Value 1", "…", "P1", "…", "C1", "…"]}
```

起動指示の MVV の欄は、この結果から次の 1 行で書く。本文は書き写さず、パスだけを渡す。

| 結果 | MVV の欄に書く 1 行 |
| --- | --- |
| `state` が `approved` | `MVV: バージョン <version>・本文 <path>・共通原則 <principles_path>（承認済み）` |
| `state` が `absent`・`unapproved`（ほかのキーは無い） | `MVV: 無し（<state>）` |
| `status` が `stopped`（ブランチを取得できない・設定を読めない・本文が承認の記録と合わない・スナップショットを保存できない） | 書かない。作業を始めずに、`summary` の理由を添えて人へ戻す。古い MVV や作業場所の MVV で代わりにしない |

本文が承認の記録と合わない状態を直す作業だけは、`--allow-mismatch` を付けて打つ。結果の `state` は `mismatch` になるので、
`MVV: 無し（mismatch）` と書く。

### 読んで判断に使う

1. 本文（Mission・Vision・Value・必ず人の承認が要る操作）と共通原則のスナップショットの全体を読む
2. 工程の成果物に関わる項目を、本文の文から選ぶ。例えば、設計で既定値を決めるときは、利用者の操作や待ちについて書いた項目を選ぶ。
   番号の意味はプロジェクトごとに違うので、「Value 1〜5 だけを読む」のように番号で読む範囲を決めない
3. 選んだ項目に反しない案を採る
4. 設計の決定には、根拠にした項目を書く。書き方は [decisions.md の「根拠の行」](../../design/references/decisions.md#根拠の行) に
   ある。バージョンには、MVV の欄か `current` の結果のバージョンを書く

### 人へ戻す

次のどちらかに当たったら、その判断で作業を進めずに人へ戻す。

| 場合 | 例 |
| --- | --- |
| 要求・設計・実装の選択が MVV の項目に反する疑いがあり、担当では決められない | 課題の要求どおりに作ると、Value の文に反する |
| 必ず人の承認が要る操作（本文の `P<番号>`・共通原則の `C<番号>`）に当たる | 別のリポジトリへの書き込みが要る。設計の決定なら「人へ残す判断」の表に書く（[decisions.md の「人へ委ねない」](../../design/references/decisions.md#人へ委ねない)） |

worker は `結果: 判断が要る`、supervisor は `結果: 止まった` で返し、conductor が人に問う。返すときは、反する疑いのある判断・
MVV の項目とその文・反すると考えた理由・選べる案を書く。人に見せる文は下の
「[人へ示す文面のテンプレート](#人へ示す文面のテンプレートmvv-の提示改訂の提案承認ゲートの資料)」に従う。

### 承認済みの MVV が無いとき

MVV の欄が `MVV: 無し` なら、MVV と突き合わせずに作業を進める。設計の決定の根拠の行には `（MVV なし）` と書く。
共通原則の「必ず人の承認が要る操作」は、MVV が無くても人の承認が要る。

### 承認ゲートでの判定

承認ゲートの直前の MVV 判定も、ベースブランチで承認された本文を材料にする。**判定はどの進め方でも打つ。** 進め方が決めるのは、
判定の後に承認ゲートを省くかだけである。判定を打つ条件・省く条件・プランを使わないときの打ち方（`--machine`）は
[pace.md の「MVV 判定」](pace.md#mvv-判定) にある。

### レビューでの確かめ

`ndf cross-review run` は起動の始めに自分で同じスナップショットを保存し、相手役と修正担当へパスを渡す。呼ぶ側が MVV の欄を渡す必要は
ない。MVV に反する指摘と、根拠の行の誤りの扱いは、[cross-review の SKILL.md](../../cross-review/SKILL.md) にある。

## 改訂の兆候

承認ゲートの回答が直前の MVV 判定と食い違ったことと、cross-review で MVV に反するとされた指摘を修正担当が「反しない」として見送ったことを
記録し、閾値を超えたら改訂を提案する材料にする。

| 兆候 | 記録 | 書き手 |
| --- | --- | --- |
| 覆し（「従う」を退けた `override_reject`・「従わない」を通した `override_pass`）・「判定できない」を人が通した `unknown_pass`（覆しに数えない）・「判定できない」の `unknown`（人の回答によらず） | MVV の記録の保存先の `mvv-signals.jsonl` | `ndf records mvv answer [--version <バージョン>]`。標準入力の JSON `{"verdict", "approved", "state", "gate", "pr"}` を受け、食い違うときと「判定できない」のときだけ書く |
| cross-review の指摘の見送り（`review_disputed`。「判定できない」と合わせて数える） | 同じ `mvv-signals.jsonl` | `ndf cross-review run` |
| MVV 判定 | 同じ保存先の `mvv-validation.jsonl` | `ndf records mvv gate` |
| MVV に反するとされた指摘の終わり方（`mvv-review`。直した・見送った・未解決・人の承認を待つ） | 同じ `mvv-validation.jsonl` | `ndf cross-review run` |

`ndf records mvv signals --base <ベースブランチ>` は、ベースブランチの `.ndf/mvv.json` の今のバージョンの行だけを数え
（同じスプリント・承認ゲート・PR の組は 1 件、`review_disputed` は同じ PR・指摘の組を 1 件）、`settings.revise_after`（既定 覆し 3・「判定できない」5）と比べて `metrics.revision_due` を返す。
真なら、振り返りなどで利用者へ改訂を問う。記録の保存先は状態の保存先の下で、`NDF_MVV_STATE_DIR` で変えられる。

## 人へ示す文面のテンプレート（MVV の提示・改訂の提案・承認ゲートの資料）

人に判断や承認を求める文面は、次の 4 セクションをこの順に先頭へ置く（共通原則の「人と AI の対話」）。`AskUserQuestion` で問うときは、
入力の `metadata.source` に区分（承認ゲートの資料は `ndf-gate:<番号>`、MVV の本文の提示と改訂は `ndf-ask:redline:C7`。SSOT は [asking.md](asking.md)）を付ける。

| セクション | 書くこと |
| --- | --- |
| 経緯 | なぜ今この判断が要るか・ここまでに何を決めたか |
| 前提 | この回答で何が決まり、何が決まらないか |
| 根拠 | 判断の材料（ファイルと行・課題の番号・コミットの件名・判定の記録）。URL とパスを省かない |
| 選択肢 | 選べるものと、選んだときに決まること。推奨があれば理由を添える |

内部の用語（MVV・バージョン・MVV 判定・覆しなど）は言い換えるか、その場で 1 行の説明を付ける。
