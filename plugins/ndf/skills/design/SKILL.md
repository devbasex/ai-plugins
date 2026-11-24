---
name: design
description: "Write the design document before coding: data structures, interface contracts, decisions. Use when a change needs a design step（設計書を作る・データ構造を決める・APIのコントラクトを決める）."
---

# 設計

実装を始める前に、**どう作るか**を文書として確定させる。担当する AI が変わっても同じ構成の
成果物になるよう、作る文書と書く内容を規約で決める。

## この Skill の責務

| 質問 | 担当 |
| --- | --- |
| どの工程が必要か（モード判定） | `development-workflow` |
| 何を満たすか（受け入れ条件・仕様） | `requirements-design` |
| **どう作るか（データ構造・入出力のコントラクト・処理の流れ・決定の理由）と、詳細設計（ファイル配置・関数の責務・異常系の処理）** | この Skill |
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

### 0. 用語集が揃っているかを確かめる（`standard` / `legacy-refactor`）

`ndf records glossary render --check` を打つ。**`ok` でなければ設計へ進まない。** 用語集の設定（`.ndf/glossary.json`）か
用語集が無い（`cause.kind` が `precondition`）なら、`requirements-design` の手順 0 を示して止まる。読めない・人が読む文書が
用語集と食い違うなら、`summary` を示して止まる（上書きすると採った語が消える）。ほかのモードはこの手順を通らない。

### 1. 触る領域を決める

受け入れ条件と対象範囲を読み、触る領域を決める。**「すべての変更」の行が指す参照に加えて、
当たった領域の参照だけ**を読む。

| 触る領域 | 読む参照 |
| --- | --- |
| すべての変更 | [references/design-template.md](references/design-template.md) / [references/deliverables.md](references/deliverables.md) / [references/decisions.md](references/decisions.md) / [references/external-survey.md](references/external-survey.md) |
| 構成要素を追加・変更する、または配置・依存・反映先を変える | [references/system-architecture.md](references/system-architecture.md) |
| 型・クラスを追加または変更する、または型を変えずに状態遷移か処理の順序を変える | [references/structure-behavior.md](references/structure-behavior.md) |
| 永続データを持つ、またはスキーマを変える | [references/data-structure.md](references/data-structure.md) |
| 呼び出される約束（API・イベント・コマンド）を変える | [references/interface-api.md](references/interface-api.md) |
| 画面を追加・変更する | [references/interface-ui.md](references/interface-ui.md) |
| 非機能の条件が仕様にある | [references/nonfunctional.md](references/nonfunctional.md) |
| ドメインモデルのセクションを書く（集約・不変条件・ドメインイベントを変えるとき） | [references/domain-model.md](references/domain-model.md) |
| **読み手へ渡す文書を作る**（`documentation`） | 共通の [references/layout-common.md](references/layout-common.md) と、出力の形に当たる参照 1 つ（[slide](references/layout-slide.md) / [document](references/layout-document.md) / [spreadsheet](references/layout-spreadsheet.md) / [page](references/layout-page.md)） |

触らない領域の参照は読まない。

**`documentation` で形態固有の参照を決めるのは、触る領域ではなく出力の形である。**

**`light` / `operation` では、この表が起動の条件も決める。** 「すべての変更」以外の領域が
1 つ以上該当するときにこの Skill を通し、1 つも当たらなければ通さない。「すべての変更」の行と
「ドメインモデルのセクションを書く」の行は数に入れない。「すべての変更」の行は入れると常に該当し、条件が
条件でなくなる。ドメインモデルの行は、入れるとこの 2 モードの起動が増える。

### 2. 外部の調査を通す

**設計の判断の前に、触る領域の業界の標準・仕様、主要な製品の解き方、使える既存のライブラリとサービスを
一次情報で調べ、設計文書の `## 外部の調査` のセクションに残す。** 手元のコードと要求の文だけで組むと、比べないまま
「自前で作る」「古い形に寄せる」と決まるためである。手順の SSOT は
[references/external-survey.md](references/external-survey.md) にある。

- 3 種類の調べる対象ごとに調べるかを決め、調べない種類には理由を 1 行残す
- 調べた行には出典（URL・パス・コマンドのどれかと取得日）を付ける。取得できない対象は推定で埋めず、
  「確かめられなかったこと」へ書く
- 上限（種類ごとの候補 6 件・取得の試み 30 回）に当たったら止め、残りを「確かめられなかったこと」へ書く
- 省ける条件に当たるときは、セクションに `省く: <理由>` の 1 行だけを置く。省けるか決められないときは通す

この手順はプロジェクトの MVV に調査の項目があるかに依らず通す。

### 3. 設計文書を書く

テンプレートは [references/design-template.md](references/design-template.md) にある。セクションの並びと、
各節が `implementation-plan` のどのタスクへつながるかもそこにある。並びは `requirements-design` の
[document-roles.md](../requirements-design/references/document-roles.md)（文書の役割の表）の設計書の行が決め、承認する人が頭から読み、決定で立ち止まる順である。

**次の順に書く。** ［ ］は当たるときだけ書き、当たらなければ見出しごと消す（「無し」と書かない。外部の調査だけは省くときも `省く: <理由>` の 1 行を残す）。

1. 題（H1）と、見出しの無い要約の段落（4 文以内）
2. 今の姿（要求仕様へのリンクと、変更の位置が分かる図）→ 文脈と制約 → 解き方の全体像
3. ［ドメインモデル・機能一覧・構成要素・構造・データ構造・入出力のコントラクト・処理の流れ・非機能の実現方式］
4. 決定の記録 → 影響と危険 → ［用語］→ テスト設計 → ［どこを変えるか］→ 設計の結果 → ［人へ残す判断］→ ［未確認のまま残ること］
5. 外部の調査（付録。手順 2 で調べた結果）

承認する人が読む欄（題・要約の段落・今の姿・解き方の全体像・決定の見出し・影響と危険）の書き方は
[design-template.md の「承認する人が読む欄」](references/design-template.md#承認する人が読む欄) にある。
目的と受け入れ条件は要求仕様が持つので、設計文書には写さずにリンクで指す。
設計 Pull Request を分ける `legacy-refactor` は要求仕様を通らないので、欄を design-template.md の「`legacy-refactor` の書き方」（目的・適用範囲・目指す姿・目指す姿の根拠）で書く。
設計 Pull Request を出さない `light`・`operation` は承認資料に載せないため、欄を書かない（モードごとの要否は
[deliverables.md の「設計の書き先」](references/deliverables.md#設計の書き先)）。

**1 本の設計文書は 1 つの問題を扱う。** 関係の薄い課題を 1 本にまとめると、承認する人が何が課題で何をしようとしているのかを
読み取れなくなるためである。

- 複数の課題を 1 本の設計文書にまとめてよいのは、根本原因が共通（同じ部品の同じ欠け）のときだけである。共通かどうかは
  設計者が設計の工程の最初に決め、共通の原因を題と今の姿の筋にし、まとめた理由（共通の原因）を今の姿に書く。cross-review と
  設計承認ゲートがその理由を見る
- スプリントの都合で設計 Pull Request を 1 本にするのは許す。その Pull Request には課題ごとの設計文書を並べる
- まとめた設計文書は `issues/issue-<番号1>-<番号2>-design.md` と名付ける。名前に含む番号の**どれで**引いても見つかるものとし、
  課題の番号で設計文書を探すときは、`-` で区切った番号のどれかが一致するものを読む（先頭の番号だけと照らさず、`78` のように番号の一部とは合わせない）

**ドメインモデルのセクションは構成要素より先に書く。** 変更が属するコンテキスト・変える集約とそのオーナー・不変条件・
ドメインイベントを、構成要素より先に決める。書き方は
[references/domain-model.md](references/domain-model.md) にある。

**書いた後に語を用語集と突き合わせる。** 設計文書の「用語」の表の語と本文の語を用語集（設定の `source`）と照らし
（規則は `requirements-design` の [glossary-format.md の「用語チェックの規則」](../requirements-design/references/glossary-format.md#用語チェックの規則)）、
新しい語・意味の変わった語・廃止する語を、同じ変更の中で用語集へ反映して `ndf records glossary render` で文書を
作り直す。続けて `ndf records glossary meanings` が `ok` であることを確かめる。構成要素の名前などを語として新しく使うときは、設計文書の「用語」のセクションの表に書く。

**タスクを機械的に導けるだけの情報を書く。** 導けない設計は、次の工程が成立しない。

**テスト設計は、縛る振る舞いと落ちるべき壊し方までにする。** フィクスチャ・起動の形・待ち方は実装
（`tdd-cycle`）で走らせて決める。粒度は design-template.md の「テスト設計に書く粒度」にある。

**値の集合へ値を足す設計では、その集合を前提にした既存の規則を集め、新しい値に当てはまるかを
1 つずつ判定する。** モード・出力の形・種類のような集合へ値を足すと、それまでの値だけを想定して
書かれた規則が、新しい値に当てはまらないまま残る。規則は 2 つの経路で集める。

- 集合の名前と既存の値の名前でリリース対象を検索する。値ごとに分岐する規則が集まる
- **新しい値が通る手順を読む。** 値の名前を書かずに、すべての値へ同じように当たる規則は、
  検索に掛からない

**当てはまらない規則は、変える対象として構成要素の表へ載せる。** 当てはまる規則は記録しない。

この判定は文書の外を見るため、手順 5 の突き合わせには含めない。

### 4. 決定を記録する

決定ごとに 質問・選んだ案・採らなかった案・代償・根拠 を残す。書き方は
[references/decisions.md](references/decisions.md) にある。

**設計の判断（作り方・範囲の取り方・既定値・扱いの選択）は設計者が決める。** 「設計承認ゲートで利用者が決める」形で
判断を残さない。材料が無く決められないものは、選択肢を並べずに「未確認のまま残ること」へ事実として書く。
人へ残すのは、レッドライン（共通原則の `C<番号>`・プロジェクト MVV の `P<番号>`・スプリント MVV の `R<番号>`）に
当たる判断だけで、`## 人へ残す判断` の表に判断ごとの番号となぜ当たるかを書く。番号を挙げられない判断は人へ残さない。

**決定ごとに、根拠にした MVV の項目を最後の行に書く**（「根拠: Value 6（MVV バージョン {N}）」）。MVV に反する疑いのある決定は
設計に書かずに人へ戻す（理由と根拠の項目つき）。MVV の読み方は [project-mvv.md の「工程での読み方」](../development-workflow/references/project-mvv.md#工程での読み方)、根拠の行の形は [decisions.md の「根拠の行」](references/decisions.md#根拠の行) にある。

**承認の工程を足す決定を書かない。** 承認ゲートの数と置く場所は `development-workflow` の「人手の承認を求める承認ゲート」だけが持つ。
共通原則の「必ず人の承認が要る操作」（C1〜C8）やプロジェクト固有の操作に当たる変更も、設計承認ゲート・リリース承認ゲートで人が見る。
「別の Pull Request で承認を取ってから書く」「実装でコミットを止めて承認を待つ」のような決定は書かず、変更は実装の中に置く。

### 5. 進む前に文書の内部整合を突き合わせる

**[references/design-template.md](references/design-template.md) の「進む前に突き合わせる対」
の 7 つを通す。** いずれも同じ文書の中だけで確かめられるもので、外部の情報を必要としない。

**続けて、自前で作る決定に比べた候補があるかと、人へ残す判断がレッドラインを名指すかを検査で確かめる。**
検査のサブコマンドは無いため、書いた・直した設計文書と、名前の番号を共有する設計文書（`issue-7-8-design.md` のような
複数の番号の文書と、それを介してつながる文書）と受け入れ条件のファイルをすべて読んで確かめる（`light` の受け入れ条件の
ファイルや `operation` の実行の記録も同じ）。見るのは 2 つである。

- 自前で作る決定ごとに、外部の調査の表に比べた候補の行がある（[references/external-survey.md](references/external-survey.md)）。
  無ければ作り方・比べた候補を書き直すか、表へ候補の行を足す。比べずには直せないものは、設計を進めずに人へ戻す
- 「決定の記録」「未確認のまま残ること」「人へ残す判断」のセクションで、人へ残す判断がレッドラインの番号を名指す
  （[references/decisions.md](references/decisions.md)）。名指せない判断は、設計者が決めて決定の記録へ書き直すか、
  レッドラインに当たるなら `## 人へ残す判断` の表へ番号となぜ当たるかを添えて移す

### 6. 詳細設計を書く（`standard` / `legacy-refactor`）

**工程表の「詳細設計」が必須のモードだけが通る**（`light`・`operation`・`documentation` は通らない）。手順 5 の後に、
設計文書の構成要素と構造を、ファイル・関数の粒度へ下ろす。ファイル配置・関数の責務・異常系の処理を実装の前に決め、
設計 Pull Request に載せて設計レビューと設計承認ゲートで見せる。

**`standard` では、別ファイル（`issues/issue-<番号の並び>-design-detail.md`）にするのは、新設する関数が 2 つ以上あるか、既存の関数のシグネチャを変えるときだけである。**
それ以外は設計文書の「どこを変えるか」のセクションに畳む。判定はこの 2 つの数だけで決め、書き手の判断を挟まない。
関数 1 つの仕様は表の 1 行に収まり、別ファイルにすると 1 つの関数のために別の文書を開かせるためである。`legacy-refactor` は別ファイルに書く。

- 数えた結果は、「どこを変えるか」の頭の 1 行（畳んだとき）か、詳細設計書へのリンク（別ファイルのとき）で示す。どちらも無ければこの手順を終えていない
- 畳んだときの書き方は [references/design-template.md の「分量」](references/design-template.md#分量)、別ファイルのテンプレートは
  [references/detail-template.md](references/detail-template.md)、項目ごとの書き方と既存の参照との分担は
  [references/detailed-design.md](references/detailed-design.md) にある
- 書く関数は、新設する関数と、シグネチャ・責務・失敗の扱いのいずれかを変える関数だけである
- 別ファイルの名前の番号の並びは、組になる設計書と同じにする。複数の課題をまとめた `issue-7-8-design.md` なら `issue-7-8-design-detail.md` で、課題 1 つなら `issue-7-design-detail.md` になる。完了判定と実装計画は、名前に含む番号のどれかで探す
- 別ファイルは 1 本 1,000 行以下にする。超える主題は設計を 2 本に分けると報告して止まる
- 別ファイルを書いたら、地図と構成要素ごとの小見出しがそろい、設計文書の構成要素がどれも詳細設計のどこかに下りていることを読んで確かめる（項目の規則は
  [references/detailed-design.md](references/detailed-design.md)）。畳んだら、「どこを変えるか」の触るファイルが「設計の結果」の触るファイルと一致することを確かめる

**設計のプランのステップから呼ばれたときは、手前で止まるか、ここだけを書く。** 設計のプランの `design` のステップの指示に
「詳細設計は後のステップが書く」とあれば、手順 5 で止める。`/ndf:design #<番号> 詳細設計` で呼ばれたら、手順 0〜5 を
繰り返さずにこの手順だけを行う（設計文書は前のステップが書き、設計単体のレビューで直してある）。

### 7. 設計 Pull Request を出す

**この手順を行うのは、設計 Pull Request が必須または任意のモードだけである**（「設計の
書き先」の表）。`light` と `operation` は独立した設計文書を作らないため、この手順と、次節の
`document-rebuild`、工程「ドキュメントレビュー」を通らない。

`standard` では、**実装より先に設計をレビューへ通す**。設計の誤りを実装した
後で直す費用が大きいためである。

**出す前に、仕様のコピーが課題の本文と一致しているかを確かめる。**
`gh issue view <課題> --json body --jq .body` の `## 進行` より前の全文と `issues/issue-<番号>-requirements.md` を比べ、
食い違えば本文が正なので、本文からコピーを作り直す（`requirements-design` の手順 7）。コピーにしか無いセクション
（実装計画など）は食い違いに数えない。

設計 PR の cross-review は、1 ラウンド目でドメインモデルのセクションだけを見て（モデルレビュー）、2 ラウンド目以降で
残りを見る（詳細レビュー）。設計承認ゲートは詳細レビューの後の 1 回だけである。

**同じ表への指摘が 2 ラウンド続いたら、その表を実装へ送るかを supervisor が判断する。** 送るときは、
表を振る舞いと壊し方だけに削り、削った細部は `tdd-cycle` で走らせて決める。テストの組み方の細部は、
文面を直すたびに次の細部が指摘され、ラウンドが収まらない。

```text
pr → cross-review → merged → worktree（実装用に作り直す）
```

新しい Skill は使わない。既存の 3 Skill をそのまま呼ぶ。実装の Pull Request と違うのは
次の 2 点である。

| 項目 | 設計 Pull Request |
| --- | --- |
| 載せるもの | 要求仕様と設計文書（詳細設計を別ファイルにしたときはその文書を含む）だけ。実装を含めない |
| 本文 | 形は `/ndf:pr` の「設計 Pull Request の本文」に従う（決定の中身を写さない）。GitHub の closing keywords（`Closes` / `Fixes` / `Resolves` の後に課題の番号を書くと、マージ時にその課題を自動で閉じる機能）を書かない |

**closing keywords を書かないのは、実装が終わっていない段階でマージするためである。** 書くと、
マージした時点で課題が閉じ、実装の工程が残っていることが課題の一覧から見えなくなる。課題を
指すときは番号だけを書く。

**マージした後、実装は新しい worktree で行う。** `merged` が設計のブランチと worktree を消す
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

**この Skill は進捗記録を打たない。** 工程「設計」「詳細設計」「ドキュメントレビュー」の記録は、工程を流すプランが
スプリントのステートマシンへ付ける（`progress-tracking`）。

**手順 7 の前に `document-rebuild` を通す**（工程「ドキュメント再構成」）。

**詳細設計は詳細設計を通るモード（`standard`・`legacy-refactor`）だけ、ドキュメントレビューと `document-rebuild` は
設計 Pull Request を出すモードだけが通る。** `light` と `operation` では手順 6・7 を通らないため、どれも呼ばない。工程の分類でも
「ドキュメント再構成」と「ドキュメントレビュー」はこの 2 モードで対象外である。

## 関連

- `/ndf:requirements-design` — 何を満たすか（この工程の入力）
- `/ndf:implementation-plan` — どう分解するか（この工程の出力を受け取る）
- `/ndf:cross-review` — 設計 Pull Request のレビュー
- `/ndf:document-rebuild` — 書き上げた設計文書を章立てから組み直す（次の工程）
- `document-style` — 文書と図の書き方
