# プロジェクト MVV（`.ndf/mvv.md`・`.ndf/mvv.json`）

プロジェクト全体の Mission / Vision / Value と固有の「必ず人の承認が要る操作」（`P<番号>`）を定義する。11 系の NDF では、
承認ゲートの MVV 判定（[pace.md](pace.md) の「MVV 判定」）がこれを材料に読み、`pace: fast`・`auto` の承認ゲートを省けるかを決める。
この文書は、ファイルの形・状態・手順 0 での扱い・改訂の兆候を持つ。

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
| `absent` | `.ndf/mvv.json` が無い | 通る | 省かない（検査 1） |
| `unapproved` | `versions` に `sha256` が無い | 通る | 省かない |
| `mismatch` | 本文が承認の後に変わった | 止まる。本文を承認した形へ戻すか、承認し直して記録を足す | 省かない |
| `unreadable` | 設定か本文を読めない | 止まる | 省かない |

旧い形のキーが残る `.ndf/mvv.json` も、手順 0 の確かめが止まる。

## 改訂の兆候

承認ゲートの回答が直前の MVV 判定と食い違ったことを記録し、閾値を超えたら改訂を提案する材料にする。

| 兆候 | 記録 | 書き手 |
| --- | --- | --- |
| 覆し（「従う」を退けた `override_reject`・「従わない」を通した `override_pass`）・「判定できない」を人が通した `unknown_pass`（覆しに数えない）・「判定できない」の `unknown`（人の回答によらず） | MVV の記録の保存先の `mvv-signals.jsonl` | `ndf records mvv answer [--version <バージョン>]`。標準入力の JSON `{"verdict", "approved", "state", "gate", "pr"}` を受け、食い違うときと「判定できない」のときだけ書く |
| MVV 判定 | 同じ保存先の `mvv-validation.jsonl` | `ndf records mvv gate` |

`ndf records mvv signals --base <ベースブランチ>` は、ベースブランチの `.ndf/mvv.json` の今のバージョンの行だけを数え
（同じスプリント・承認ゲート・PR の組は 1 件）、`settings.revise_after`（既定 覆し 3・「判定できない」5）と比べて `metrics.revision_due` を返す。
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
