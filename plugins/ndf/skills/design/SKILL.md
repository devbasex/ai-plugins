---
name: design
description: "Write the design document before coding: data structures, interface contracts, decisions. Use when a change needs a design step（設計書を作る・データ構造を決める・APIの契約を決める）."
---

# 設計

実装を始める前に、**どう作るか**を文書として確定させる。担当する AI が変わっても同じ構成の
成果物になるよう、作る文書と書く内容を規約で決める。

## この Skill の責務

| 問い | 担当 |
| --- | --- |
| どの工程が必要か（モード判定） | `development-workflow` |
| 何を満たすか（受け入れ条件・仕様） | `requirements-design` |
| **どう作るか（データ構造・入出力の契約・処理の流れ・決定の理由）** | この Skill |
| どう分解するか（タスク・順序・対象ファイル） | `implementation-plan` |
| 完了後に何を残すか | `plan-to-spec` |

**モードの判定はしない。** どのモードに当たるかの基準は `development-workflow` が持つ。
この Skill は判定結果を受け取る側に徹する。

この文書で「タスク」と書くのは、`implementation-plan` が並べる作業の単位を指す。課題管理の
issue のことではない。

## 受け取るもの

| 入力 | 出所 | 無いときの扱い |
| --- | --- | --- |
| モード | `development-workflow` の判定結果 | 判定を先に通す |
| 受け入れ条件 | `requirements-design` が書いた仕様 | 仕様を先に書く（`legacy-refactor` を除く） |
| 触る領域 | 受け入れ条件と対象範囲から読む | 設計の時点では実装の差分がないため、差分からは判断しない |

**`legacy-refactor` は仕様を入力に取らない。** 工程表でも要求と受け入れ条件を通らないためである。
このモードでは、直す対象の現状の構造から触る領域を読む。

## モードごとの成果物

**何を書くかはモードと領域の 2 つで決まる。** モードが水準を決め、領域が該当を決める。
成果物ごとの要否表と中身は [references/deliverables.md](references/deliverables.md) にある。

| 水準 | 対象が存在するとき | 対象が存在しないとき |
| --- | --- | --- |
| 必須 | そのモードで `design` を通すなら書く | 書かない。**対象が無いことを、下の書き先へ書く** |
| 該当時 | 触る領域に当たれば書く | 書かない。理由も要らない |

**「不要」を表す値は置かない。** どの成果物も、変更の中身が求めれば書く対象になる。モードが
決めるのは無条件に求めるかどうかであって、書いてはいけない成果物を決めるものではない。

### 設計の書き先

| モード | 設計の書き先 | 設計 Pull Request |
| --- | --- | --- |
| `light` | 受け入れ条件を書いたファイルの節（`issues/` 配下） | 不要 |
| `operation` | 実行の記録と同じファイルの節（置き場所は `operation-run.md` が決める） | 不要 |
| `legacy-refactor` | 独立したファイル、または実装の Pull Request に載せる | 任意 |
| `standard` | 独立したファイル | 必須 |

独立したファイルの置き場所は `issues/` 配下とし、完了後に `plan-to-spec` が `docs/` へ移す。
仕様と同じ場所に置くのは、同じ変更の記録が 2 箇所に分かれないようにするためである。

### 省いた理由の書き先

| モード | 書き先 |
| --- | --- |
| `light` / `operation` / `standard` | 仕様の「対象範囲（含まない）」 |
| `legacy-refactor` | 設計文書の「決定の記録」 |

**触らない領域の節は作らない。** 空の見出しを残すと、書き忘れと意図的な省略を読み手が
区別できない。**`legacy-refactor` だけ書き先が違うのは、このモードが仕様を入力に取らない
ためである。**

独立したファイルにするのは、**設計として確定した内容と、実装の段階で入る計画の更新を、
別の差分として読めるようにする**ためである。**設計の分量が小さく、仕様と混ざっても読み分け
られるときだけ**、同じファイルの別の節でよい。判断は「**設計 Pull Request として単独で
読めるか**」で見る。

1 ファイルが 500 行を超えたら分割する（`markdown-writing` の分量の基準）。

## 手順

### 1. 触る領域を決める

受け入れ条件と対象範囲を読み、触る領域を決める。**「すべての変更」の行が指す参照に加えて、
当たった領域の参照だけ**を読む。

| 触る領域 | 読む参照 |
| --- | --- |
| すべての変更 | [references/design-template.md](references/design-template.md) / [references/deliverables.md](references/deliverables.md) / [references/decisions.md](references/decisions.md) |
| 構成要素を追加・変更する、または配置・依存・反映先を変える | [references/system-architecture.md](references/system-architecture.md) |
| 型・クラスを追加または変更する、または型を変えずに状態遷移か処理の順序を変える | [references/structure-behavior.md](references/structure-behavior.md) |
| 永続データを持つ、またはスキーマを変える | [references/data-structure.md](references/data-structure.md) |
| 呼び出される約束（API・イベント・コマンド）を変える | [references/interface-api.md](references/interface-api.md) |
| 画面を追加・変更する | [references/interface-ui.md](references/interface-ui.md) |
| 非機能の条件が仕様にある | [references/nonfunctional.md](references/nonfunctional.md) |

触らない領域の参照は読まない。

**`light` / `operation` では、この表が起動の条件も決める。** 「すべての変更」以外の領域が
1 つ以上該当するときにこの Skill を通し、1 つも当たらなければ通さない。「すべての変更」の行を
数に入れないのは、入れると常に該当し、条件が条件でなくなるためである。

### 2. 設計文書を書く

雛形は [references/design-template.md](references/design-template.md) にある。節の並びと、
各節が `implementation-plan` のどのタスクへつながるかもそこにある。

**タスクを機械的に導けるだけの情報を書く。** 導けない設計は、次の工程が成立しない。

### 3. 決定を記録する

選んだ結論と、その理由と、採らなかった案を残す。書き方は
[references/decisions.md](references/decisions.md) にある。

### 4. 進む前に文書の内部整合を突き合わせる

**[references/design-template.md](references/design-template.md) の「進む前に突き合わせる対」
の 6 つを通す。** いずれも同じ文書の中だけで確かめられるもので、外部の情報を必要としない。

**確かめた結果は残さない。** 残らないことが結果である。レビューが内部整合の指摘を出さなく
なることで効果が見える。

**この手順は 4 モードすべてで行う。** 設計 Pull Request を出さないモードでも、文書の中で
食い違ったまま実装へ進むことは同じように起きる。

### 5. 設計 Pull Request を出す

**この手順を行うのは、設計 Pull Request が必須または任意のモードだけである**（「設計の
書き先」の表）。`light` と `operation` は独立した設計文書を作らないため、この手順と、次節の
`document-restructuring`、進行の記録の「ドキュメントレビュー」を通らない。

`standard` では、**実装より先に設計をレビューへ通す**。設計の誤りを実装した
後で直す費用が大きいためである。

```text
pr → cross-review → merged → worktree（実装用に作り直す）
```

新しい Skill は使わない。既存の 3 Skill をそのまま呼ぶ。実装の Pull Request と違うのは
次の 2 点である。

| 項目 | 設計 Pull Request |
| --- | --- |
| 載せるもの | 要求仕様と設計文書だけ。実装を含めない |
| 本文 | 課題を自動で閉じる語（`Closes` / `Fixes` / `Resolves`）を書かない |

**自動で閉じる語を書かないのは、実装が終わっていない段階でマージするためである。** 書くと、
マージした時点で課題が閉じ、実装の工程が残っていることが課題の一覧から見えなくなる。課題を
指すときは番号だけを書く。

**マージした後、実装は新しい作業ツリーで行う。** `merged` が設計のブランチと作業ツリーを消す
ため、そのまま実装を続けられない。`worktree` を実装用のブランチ名で呼び直す。

## 設計で重視する 4 点

### 1. 構造と振る舞い

**何がどう組み合わさるか（構造）と、どういう順序と状態で動くか（振る舞い）を図として
確定させる。** 文章だけで書くと、要素どうしの関係と、起こらない遷移が表せない。クラス図は
変更が触る型だけに絞り、**実装後の追随義務は負わない**。詳細は
[references/structure-behavior.md](references/structure-behavior.md) と
[references/system-architecture.md](references/system-architecture.md)。

### 2. データ構造

業務処理が通ることだけを条件にしない。集計・比較・追跡の単位が取り出せる構造にし、状態を
上書きして過去を失う構造は設計の時点で退ける。詳細は
[references/data-structure.md](references/data-structure.md)。

### 3. 入出力

利用者向けの画面と API の規約は、**設計の時点でほぼ確定した状態にする**。実装しながら形が
決まる状態にしない。

### 4. 人と AI の双方が解釈できる形式

API は仕様記述形式で書く。**対象となる領域を触る変更でだけ求める。** API を持たない変更に
API の記述を求めない。

## 設計から実装計画へ

設計文書の各節は、`implementation-plan` のタスクの単位になる。

| 節 | 実装計画での使われ方 |
| --- | --- |
| 構成要素 | タスクの単位になる |
| データ構造 | 移行タスクと検査タスクになる |
| 入出力の契約 | 契約テストのタスクになる |
| 処理の流れ | 順序の依存を読む |
| 決定の記録 | 代替案の再検討を止める |
| テスト設計 | テストのタスクになる |
| 未確認のまま残ること | リスクとして計画へ移す |

## 範囲外の課題を見つけたとき

設計の過程で、この変更の受け入れ条件にも直す対象にも含まれない課題が出たら、**その場で**
`out-of-scope` が issue にする。設計から実装までの間に時間が空くと、どこで、なぜ範囲外と
判断したのかが記憶に頼ることになる。

## 進行を記録する

**契機は 2 つあり、別々の時点で 1 つずつ呼ぶ。** 手順 1（触る領域を決める）に入るときに
`/ndf:progress-tracking <issue番号> "設計"`、手順 5（設計 Pull Request を出す）に入るときに
`/ndf:progress-tracking <issue番号> "ドキュメントレビュー"` を呼ぶ（記録の手順はその Skill が
持つ）。**手順 5 の前に `document-restructuring` を通す**（工程「ドキュメント再構成」）。

**2 つ目の契機と `document-restructuring` は、設計 Pull Request を出すモードだけに掛かる。**
`light` と `operation` では手順 5 を通らないため、どちらも呼ばない。工程の分類でも
「ドキュメント再構成」と「ドキュメントレビュー」はこの 2 モードで対象外である。

## 関連

- `/ndf:requirements-design` — 何を満たすか（この工程の入力）
- `/ndf:implementation-plan` — どう分解するか（この工程の出力を受け取る）
- `/ndf:cross-review` — 設計 Pull Request のレビュー
- `/ndf:document-restructuring` — 書き上げた設計文書を章立てから組み直す（次の工程）
- `/ndf:markdown-writing` — 文書と図の書き方
