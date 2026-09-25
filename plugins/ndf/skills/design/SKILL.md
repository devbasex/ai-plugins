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
成果物の要否、水準、設計の書き先、設計 Pull Request の要否、省いた理由の書き先は
[references/deliverables.md](references/deliverables.md) を唯一の定義として読む。

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
| **読み手へ渡す文書を作る**（`documentation`） | 共通の [references/layout-common.md](references/layout-common.md) と、出力の形に当たる参照 1 つ（[slide](references/layout-slide.md) / [document](references/layout-document.md) / [spreadsheet](references/layout-spreadsheet.md) / [page](references/layout-page.md)） |

触らない領域の参照は読まない。

**`documentation` で形態固有の参照を決めるのは、触る領域ではなく出力の形である。**

**`light` / `operation` では、この表が起動の条件も決める。** 「すべての変更」以外の領域が
1 つ以上該当するときにこの Skill を通し、1 つも当たらなければ通さない。「すべての変更」の行を
数に入れないのは、入れると常に該当し、条件が条件でなくなるためである。

### 2. 設計文書を書く

雛形は [references/design-template.md](references/design-template.md) にある。節の並びと、
各節が `implementation-plan` のどのタスクへつながるかもそこにある。

**タスクを機械的に導けるだけの情報を書く。** 導けない設計は、次の工程が成立しない。

**値の集合へ値を足す設計では、その集合を前提にした既存の規則を集め、新しい値に当てはまるかを
1 つずつ判定する。** モード・出力の形・種類のような集合へ値を足すと、それまでの値だけを想定して
書かれた規則が、新しい値に当てはまらないまま残る。規則は 2 つの経路で集める。

- 集合の名前と既存の値の名前で配布物を検索する。値ごとに分岐する規則が集まる
- **新しい値が通る手順を読む。** 値の名前を書かずに、すべての値へ同じように当たる規則は、
  検索に掛からない

**当てはまらない規則は、変える対象として構成要素の表へ載せる。** 当てはまる規則は記録しない。

この判定は文書の外を見るため、手順 4 の突き合わせには含めない。

### 3. 決定を記録する

選んだ結論と、その理由と、採らなかった案を残す。書き方は
[references/decisions.md](references/decisions.md) にある。

### 4. 進む前に文書の内部整合を突き合わせる

**[references/design-template.md](references/design-template.md) の「進む前に突き合わせる対」
の 6 つを通す。** いずれも同じ文書の中だけで確かめられるもので、外部の情報を必要としない。

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
| 本文 | 形は `/ndf:pr` の「設計 Pull Request の本文」に従う（決定の中身を写さない）。課題を自動で閉じる語（`Closes` / `Fixes` / `Resolves`）を書かない |

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

設計文書の各節は、`implementation-plan` のタスクの単位になる。各節が実装計画のどこへ
つながるか（書く内容と使われ方の対応）は、
[references/design-template.md](references/design-template.md) の「各節が実装計画のどこへつながるか」を
唯一の定義として読む。

## 範囲外の課題を見つけたとき

設計の過程で、この変更の受け入れ条件にも直す対象にも含まれない課題が出たら、**その場で**
`out-of-scope` が issue にする。設計から実装までの間に時間が空くと、どこで、なぜ範囲外と
判断したのかが記憶に頼ることになる。

## 進行を記録する

**契機は 2 つあり、別々の時点で 1 つずつ記録のコマンドを打つ。** 手順 1（触る領域を決める）に
入るときに 1 つ目、手順 5（設計 Pull Request を出す）に入るときに 2 つ目を打つ。issue の本文の
`## 進行` とボードの両方に残る（`$SCRIPTS` の決め方は `development-workflow` の
`references/scripts-lookup.md`。3 層では起動指示の「記録のコマンド」をそのまま使う）。

```bash
bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "設計"
bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "ドキュメントレビュー"
```

**手順 5 の前に `document-restructuring` を通す**（工程「ドキュメント再構成」）。

**2 つ目の契機と `document-restructuring` は、設計 Pull Request を出すモードだけに掛かる。**
`light` と `operation` では手順 5 を通らないため、どちらも呼ばない。工程の分類でも
「ドキュメント再構成」と「ドキュメントレビュー」はこの 2 モードで対象外である。

## 関連

- `/ndf:requirements-design` — 何を満たすか（この工程の入力）
- `/ndf:implementation-plan` — どう分解するか（この工程の出力を受け取る）
- `/ndf:cross-review` — 設計 Pull Request のレビュー
- `/ndf:document-restructuring` — 書き上げた設計文書を章立てから組み直す（次の工程）
- `/ndf:markdown-writing` — 文書と図の書き方
