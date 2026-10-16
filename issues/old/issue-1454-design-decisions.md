# #1454: 決定の記録・テスト設計・未確認のまま残ること

設計の本体は `issues/issue-1454-design.md` にある。要求と受け入れ条件は #1454 の本文にある（コピーは
`issues/issue-1454-requirements.md` ）。

## 決定の記録

### 決定 1: 「本番系へ届く」は行の真偽値の項目 `production` で表し、無いときは決めない

判定に要るのは「本番系か、そうでないか」の 2 値だけで、dev と staging の違いはどの判定も使わない。環境の区分
（ `production` / `staging` / `development` ）の列挙にすると、使わない値の意味と、列挙に無い環境の名前の扱いを決める必要が
出る。環境の名前は今の `target` が持つ。無いときを `false` へ倒さないのは、既存の宣言の本番の行を検証の環境と読み、承認ゲート 2
を落とさないためである（前提 2）。

項目の名前に `channel` は使わない。用語集で「本番チャネル」はブランチを指し、行の区分に同じ語を使うと 1 語が 2 つの意味を持つ。

根拠: Value 8 / Value 1（MVV 版 1）

### 決定 2: 開発版のチャネルの判定を `lib/delivery.py` の `dev_channel` に置き、起点と本番チャネルが違うときは `delivery` を読まない

`pace_refusal` と `route_waves` が同じ判定を使い、判定が 2 か所で食い違わないようにする（非機能の条件）。起点と本番チャネルが
違うときに `delivery` を読まないのは、今そのリポジトリで宣言が壊れていても `auto` が通っており、読むと振る舞いが変わるため
である（AC9・前提 5）。宣言の読み込みは `supervise_lib/decl.py` の `release_routes` から切り出した `delivery_decl` 1 つにし、
`pace_refusal` が自分で origin/HEAD を読む今のコードを消す。

`pace_refusal` の中で `delivery` を読み直す案は採らない。 `apply_routes` が既に同じ宣言を読んでおり、読む箇所が 2 つになる。

根拠: Value 6 / Value 7（MVV 版 1）

### 決定 3: 本番チャネルへのマージで自動で届くかを `reaches_by_merge` に置き、 `production: false` の自動反映の行を除く

`dev_channel` の表の 4 と `judge_target` は同じ問い（本番チャネルへのマージで本番系が変わるか）を持つため、1 つの関数にする。
`main` へのマージで staging へ自動でデプロイする行（ `kind: auto` ・ `branch: main` ・ `production: false` ）は本番系を変えない
ため、承認ゲート 2 に数えない。 `production` を書いていない行は `is not False` で今と同じに数える。

`judge_target` を変えない案は採らない。その案では、同じ行を `dev_channel` は開発版と読み、hook は本番と読んで、検査のマージが
承認ゲート 2 で止まる。

根拠: Value 6 / C4（MVV 版 1）

### 決定 4: `production: true` の行がすべて手動で、1 つ以上あるときだけ手動反映の本番系の形に数える

本番系へ届く行が 1 つも宣言されていない形は、本番系が手動で届くのかを NDF が知らないため、今と同じく断る（AC3）。
`production: true` の自動反映の行が別のブランチにある形（例 `release` ブランチで本番へ届く）は、承認ゲート 2 を置く場所を
この変更では組まないため断る。検証の環境の行が無い宣言（本番の手動の行だけ）も数え、 `verify` は検査のマージの後に走らせる
（要求の未決 2 の既定の案）。導入の確認はデプロイ先の確認（スタックの差分やイメージのダイジェスト）で書けるためである。

根拠: Value 2 / 共通原則の「必ず人の承認が要る操作を緩めない」（MVV 版 1）

### 決定 5: 手で届ける経路を「開発版」「承認ゲート 2」「本番」の 3 ステージに分け、承認ゲート 2 は承認ゲート 2 のプランの MVV 判定で取る

今の「リリース」のステージ 1 つに承認ゲート 2 を足す案は採らない。手で行うステージはプランを持たず、MVV 判定のステップ・
`verify` ・ `by: mvv` の記録・ `handoff` を置く場所が無い。承認ゲート 2 だけを新しいプラン `gate-2` にし、デプロイそのものは
今の手で行うステージのまま担い手が起こす（前提 3）。ステージの名前は既存の語（開発版・承認ゲート・本番）を使い、新しい語を作らない。

`production` を書いていない手動の行は「本番」へ入れる（I7）。承認の後に届けても失うのは待ち時間だけだが、本番系の行を承認の
前に届けると取り消せない。

`normal` の並び（「リリース」1 つ）は変えない。 `normal` は `/ndf:release` の中で利用者が承認ゲート 2 を取っており、分けても
承認の回数も場所も変わらない。

根拠: Value 2 / Value 4 / C4（MVV 版 1）

### 決定 6: 「開発版」の手で行うステージがあるときは、承認ゲート 2 を単独の queue にする

queue の `--then` は前のステージのプランが完了したら続けて流すため、手で行う「開発版」を挟めない。続けて流すと、staging へ
届く前に `verify` が走る。「開発版」が無いときは、承認ゲート 2 を検査の後に `--then` で続け、conductor の手を 1 回減らす。

単独の queue でも MVV 判定へ `--pr` を渡す。 `--pr` を渡さないと、 `mvv-gate.py` の `boundary_paths` （秘密・認証認可・利用者の
データ）と `.ndf/mvv.*` （C7）の照合が走らず、それらを変えたスプリントが MVV 判定だけで承認ゲート 2 を通れてしまう。 `auto` は
`{queue_pr:check}` を渡し、queue が同じディレクトリの検査の計画の報告から番号を埋める（承認ゲートの後に単独で流すときの既存の置き換え）。
`fast` の単独の queue は前のステージの Pull Request を集められないため、MVV 判定を打たずに利用者の承認ゲートにする。番号が `0` に
埋まったときも `mvv-gate.py` が人へ戻す。承認資料にはベースブランチの先頭のコミット（SHA）を載せ、「本番」のステージはそのコミットを
届ける。承認の後にベースブランチへ入った変更（ `fast` の直接のマージ・別のスプリント）を承認なしに届けないためで、先頭が違えば
承認ゲート 2 からやり直す。

根拠: Value 2 / Value 4 / C1 / C2 / C3 / C4 / C7（MVV 版 1）

### 決定 7: `verify` の実行と承認資料の組み立てを `release-steps.py deploy-facts` の 1 ステップにする

確認の出力は承認資料に載せる材料であり、分けると出力の受け渡しにファイルの約束が 1 つ増える。承認資料は宣言の行・git の
先頭・確認の出力という判断の要らない材料からできるため、スクリプトが組む。置き場は承認資料の材料を組む `approval-facts` と
同じ `release-steps.py` にする。

根拠: Value 4（MVV 版 1）

### 決定 8: `fast` の実践投入は、ベースブランチへのマージと `production: false` の行への反映に当てる

この形ではベースブランチへのマージが本番系に届かないため、実装の Pull Request の直接のマージがそのまま実践投入になる。
検証の環境へのデプロイがあれば、そこまでを開発版として扱う。 `fast` のステージは `auto` と同じ `route_waves` で組む。

根拠: Value 1（MVV 版 1）

### 決定 9: 起点と本番チャネルが違うリポジトリでは、 `production` を書いても承認ゲート 2 のステージを置かない

`route_waves` が 3 ステージへ分けるのは `dev_channel` が `manual-production` のときだけにする。対象範囲が「起点と本番チャネルが
違うリポジトリの工程の変更」を含まないためで、要求の対象範囲に従う。そのリポジトリの手で届ける経路は今の「リリース」のステージのままになる。

根拠: 根拠なし（MVV 版 1）

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| AC1・I2 | 起点 = 本番 = `main` 、 `production: true` の手動の行（と `production: false` の行）の宣言で、 `new sprint --pace auto` と `--pace fast` が `ok` を返し `sprint.json` を書く | `dev_channel` を起点と本番の比較だけに戻すと落ちる |
| AC2・I2 | 同じ形に `kind: auto` ・ `branch: main` （ `production` 無しか `true` ）の行を足すと `stopped` で、理由が開発版のチャネルが無い | 表の 4 を外すと落ちる |
| AC3・I2 | `delivery` が無い・ `{"unknown": ...}` ・壊れている・ `production: true` の行が無い、の 4 つで `stopped` | 表の 3 か 5 を外す、または `production` の無い手動の行を本番系に数えると落ちる |
| I2（表の 6） | `production: true` ・ `kind: auto` ・ `branch: release` の行があると `stopped` | 表の 6 を外すと落ちる |
| AC4・I5 | AC1 の形の `auto` の `sprint.json` で、 `mvv-gate.py check --gate release` を持つステップが全プランで 1 つだけで、それが「本番」の手で行うステージより前のステージにあり、検査のプランには無い | 承認ゲート 2 のプランを置かない、検査のプランへ MVV 判定を足す、「本番」を承認ゲート 2 の前に置くと落ちる |
| AC5・I4 | 起点 = 本番 = `main` で手動の行だけの宣言と、 `production: false` の `kind: auto` ・ `branch: main` の行の宣言で、 `judge_target(decl, "main")` が `not-production` | `reaches_by_merge` が `production` を見ないと後者で落ちる |
| I4 | `production` を書いていない `kind: auto` ・ `branch: main` の行で `judge_target` が `production` （今と同じ） | `is not False` を `is True` にすると落ちる |
| AC6・I6 | `gate-2` のプランを実際に `supervise.py run` で流し、 `verify` を `false` にすると `mvv` のステップが走らずにプランが止まる。 `true` なら `mvv` へ進む | `facts` に `on_fail` を付けて先へ進めると落ちる |
| AC6 | `deploy-facts` は確認が非 0 なら 1 で終わり、承認資料に終了コードと出力を載せる | 確認の終了コードを捨てると落ちる |
| AC7 | ai-plugins の `.ndf/project.json` と `production` を書いた宣言の両方が `project-decl.py check` を通り、 `production: "yes"` は通らない。生成したスキーマと置いたスキーマが一致する | モデルに項目を足さない、スキーマを作り直さないと落ちる |
| AC8・I8 | ai-plugins の宣言の写しで `new sprint` を `--pace normal` / `auto` / `fast` で打った `sprint.json` と各プランが、変更前に取った期待値と一致する。 `production` を 1 行も書いていない手動の宣言の `auto` （起点 ≠ 本番）も同じ | `Route.as_dict` が `production: null` を出す、 `route_waves` が形を見ずに分けると落ちる |
| AC9・I1 | 起点 `develop` ・本番 `main` で、 `delivery` が無い・壊れている・ `production` を書いた、のどれでも `dev_channel` が `separate-branch` | 表の 2 と 3 の順を入れ替えると落ちる |
| I3 | `dev_channel` と `judge_target` の呼び出しで `gh` が呼ばれない | `gh` を呼ぶ処理を足すと落ちる |
| I7 | `production` を書いていない手動の行は「本番」のステージの note に載り、「開発版」には載らない | 未記入を検証の環境として扱うと落ちる |
| 決定 6 | 「開発版」があると承認ゲート 2 は `command` を持ち、 `auto` は `{queue_pr:check}` を渡す（単独の queue で検査の報告の番号に埋まる）。 `fast` の単独の queue の `mvv` は 10 を返す。無いと `then_of` で検査に続き、 `auto` は `{queue_pr:check}` を渡す | `then_of` を常に付ける、単独の queue で `--pr` を外す、 `fast` の単独の queue で MVV 判定を打つと落ちる |
| 決定 6 | 「本番」のステージの note が承認資料のコミットの checkout と、先頭が違えば承認ゲート 2 からやり直すことを書く | note から SHA の指示を外すと落ちる |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| `/ndf:release` が承認ゲート 2 の記録を確かめるか | 手で行う「本番」のステージで、担い手が承認ゲート 2 の通過（状態の記録）を確かめずに `trigger` を打つことを、NDF は機械では止めない。ステージの note で示すだけである。止める仕組みは `trigger` の自動実行（前提 3 で範囲外）と同じ課題で扱うのが自然で、実装の段階で起票するかを決める |
| `fast` の単独の queue の承認ゲート 2 | Pull Request を集められないため、毎回利用者の承認になる。承認の回数が問題になるかは carmo-cdk のリリース後テストで見る |
| `<節>.verify` の上限時間 | `facts` のステップの `timeout` は宣言に無いため固定値にする。値は実装で既存の `verify-install` の所要から決める |
| merge / promote の経路で `verify` が走らない | 設計中に見つけた範囲外の欠陥として #1457 に起票した |
| carmo-cdk での実地の確認 | 利用側の宣言の書き換えを伴うため、リリース後テストで扱う（要求の検証手段） |
