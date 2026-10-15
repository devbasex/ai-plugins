# #1336: 決定の記録・テスト設計・未確認のまま残ること

設計の本体は `issues/issue-1336-design.md` にある。この文書は、その設計で選んだ結論と理由・受け入れ条件と不変条件の確かめ方・
確かめられなかったことを持つ。

## 決定の記録

### 決定 1: 止める場所はスクリプトに置き、プランでは判定のステップをマージのステップの前に分ける

プランのマージは、すべて `verify_steps.merge_step` から `merged-steps.py merge-when-green` を通る。そこで判定すれば、
4 ランタイムのどれから流しても同じに止まる。プランには `merge-gate` → `merge` → `merge-approved` の 3 ステップを置き、
承認ゲート 2 で止まったら `gate_next: end` でプランを終え、承認の後は `--from merge-approved` で続ける。

`merge` のステップに `gate_next: end` を付けるだけにしなかったのは、 `merge-when-green` が後片付けで `git branch -D` の同意を
求めるときも同じ終了コード 10 を返すためである。マージの後の承認ゲートでプランが終わると、検査の記録（ `record` ）や
まとめの `close` が流れなくなる。判定のステップを分ければ、2 つの 10 がプランの中で別のステップから出る。

hook だけで止める案は採らない。 `merge-when-green` は `gh` を子のプロセスで呼ぶため、hook からは見えない。

根拠: Value 6 / C4（MVV 版 1）

### 決定 2: 自動反映の本番チャネルは、本番チャネルと `delivery` の `kind: auto` の行の `branch` の一致で決め、宣言の形は拡げない

本番チャネルは今の定義（ `production_branch` → 既定ブランチ。 `development-workflow` の「どのブランチが本番チャネルか」）を
そのまま使う。その本番チャネルを `branch` に持つ `auto` の行があれば、マージが本番系への反映になる。trygroup の `develop` の行は
本番チャネルでないため検証への反映として扱う。

`delivery` の行へ系（本番 / 検証）のキーを足す案は採らない。本番を表す値が `production_branch` と `delivery` の 2 か所に
分かれ、食い違ったときにどちらを正とするかの規則が要る。解析（#1333）もそのキーを埋めないため、足しても宣言の多くで欠ける。
本番チャネル以外のブランチから本番へ出るリポジトリが現れたら、そのときに `production_branch` の側を並びへ広げる。

根拠: Value 5 / Value 7（MVV 版 1）

### 決定 3: 判定できないときは `undetermined` として止め、宣言が無いリポジトリも本番チャネル宛てなら止める

宣言が壊れている・本番チャネルを決められない・宛先が本番チャネルで `delivery` が不明か無い、の 3 つは、マージで本番へ出るかを
決められない。止めない側へ倒すと、承認の無いまま本番へ届く経路が残る。止まったときの承認資料と `next` に、 `project-decl.py` で
`delivery` を宣言すれば次から判定できることを書く。

`delivery` が無いときだけ通す案は採らない。宣言の無いリポジトリで今の振る舞い（止まらない）を保てるが、宣言の無いリポジトリは
本番系へ出るかどうかが最も分からないリポジトリでもある。本番チャネル宛てでないマージ（ベースブランチが別のリポジトリの実装）は
表の 3 行目で通るため、止まるのは本番チャネル宛てに限られる。

根拠: C4 / 上位の原則（人を守る）（MVV 版 1）

### 決定 4: 承認の引数は `--gate-approved user|mvv` にし、誰が承認したかを結果に残す

値で承認の担い手（利用者か MVV 判定か）を分けるのは、後から監査で辿るためである。マージした先頭のコミットも同じ行に残す。
引数を付けるステップは `--from` でしか入れない `merge-approved` ・ `promote-approved` と、MVV 判定が従ったときだけ進む
昇格のプランに限る。

承認を先頭のコミットへ縛る案（ `--approved-head <sha>` ）は採らない。プランを書く時点では先頭のコミットが決まらず、
ステップの引数に書けない。承認の後に push が入る流れは今のプランに無い。

根拠: Value 2 / C4（MVV 版 1）

### 決定 5: リリースの経路は `delivery` から導き、 `release.form` に値を足さない。 `release.form` があればそちらを優先する

配布の仕方を表す正本は `delivery` である（#1333）。 `release.form` に `merge` などの値を足すと、同じ事実を 2 か所に書くことになる。
`release.form` は雛形を選ぶ宣言として残し、あれば経路 `template` として先に効く。ai-plugins の `delivery` （ `manual` ・
`versioned: true` ）は `release.form: package-plugin` が先に効くため、今の雛形のまま動く。

根拠: Value 7 / Value 5（MVV 版 1）

### 決定 6: ベースブランチと本番チャネルが同じリポジトリでは、ミッションブランチからの検査の Pull Request のマージで 1 回止める

`normal` のミッションでは、実装の Pull Request はミッションブランチへ入り、ベースブランチへ入るのは検査の Pull Request の
1 本だけである。そのマージが本番への反映で、承認ゲート 2 はミッションで 1 回になる。実装の Pull Request ごとに止める形にはならない。
`fast` と `auto` は、ベースブランチと本番チャネルが同じリポジトリを今も断る（ `pace_refusal` の「開発版のチャネルが無い」）。
この変更で断る条件は変えない。

ミッションを通さない `new impl` ・ `new fix` は、ベースブランチへ直接マージするため Pull Request ごとに止まる。1 本のマージが
1 回の本番への反映なので、承認ゲート 2 の定義どおりである。まとめて 1 回にしたいときはミッションで流す。

根拠: Value 2 / Mission（MVV 版 1）

### 決定 7: 手で反映する経路（ `manual` ）では、NDF はコマンドを実行せず、 `/ndf:release` の手で行うステージを置く

`delivery` の `trigger` は解析が書く文であり、実行してよいコマンドの形をしていない。本番への反映のコマンドは認証情報を使い
（C1）、反映の成否を見届ける仕組み（要求の範囲外）も無い。手で行うステージは今の `manual_release_wave` と同じく
`/ndf:release` を指し、note に `target` と `trigger` を書く。 `/ndf:release` は形 `service` の手順で承認ゲート 2 を取る。

宣言にコマンドを持たせて承認の後に実行する案は採らない。宣言の形を拡げ、反映の監視までを要する。

根拠: C1 / Value 1（MVV 版 1）

### 決定 8: `package-plugin` から宣言へ移すのはブランチ・タグの接頭辞・題・リポジトリ名で、CHANGELOG・配置・版の形は形の約束として残す

ブランチは宣言に正本（ `base_branch` ・ `production_branch` ）があり、雛形の側（ `release_templates.py` ）は既に読んでいる。スクリプトが
直書きのままだと、宣言を変えた仮のリポジトリで雛形とスクリプトが別のブランチを使う（AC10）。タグの接頭辞と題は
`release.plugin` から決まり、リポジトリ名は `origin` の URL から決まる。どれも新しい宣言を要らない。

CHANGELOG の置き場と見出し・ `plugins/<名>/` の配置・版の形は、 `package-plugin` を選んだリポジトリが守る約束として
`form-package-plugin.md` に書く。この形を選ぶリポジトリは今 ai-plugins だけで、ほかの形に合わせる値の出所を決める材料が無い。
値ごとに宣言のキーを足す案は、使う者の無いキーを増やす。

根拠: Value 5 / Value 1（MVV 版 1）

### 決定 9: hook は自動反映の本番チャネル宛ての直接の `gh pr merge` を拒み、 `merge-when-green` へ案内する

エージェントが直接打つ `gh pr merge` は、スクリプトの判定を通らない。hook は今の問い合わせ（ `pulls/<番号>` ）の応答から
宛先を得られるため、 `gh` の呼び出しを足さずに判定できる。判定は `hook.py merge-target` が `judge_target` を呼んで行い、
シェルへ規則を写さない。

hook の側で承認を受ける案（承認ラベルなど）は採らない。承認を示す場所が 2 つになる。直接のマージを拒めば、承認は
`merge-when-green` の `--gate-approved` の 1 か所に集まる。人の手によるマージ（端末・GitHub の画面）は hook を通らず、止めない。

根拠: Value 6 / C4（MVV 版 1）

### 決定 10: 設計 Pull Request のマージが自動反映の本番チャネルへ入るときは、承認ゲート 1 の問いに本番への反映を載せ、1 回の承認で進める

`normal` でベースブランチと本番チャネルが同じリポジトリでは、設計 Pull Request のマージも本番への反映を起こす。承認ゲート 1 と 2 を
別々に問うと、同じ 1 回のマージに 2 回の承認を求めることになる。承認ゲート 1 の承認資料に「このマージで本番へ出る」ことを書き、
承認を得たら conductor が `merge-when-green --gate-approved user` でマージする。承認ゲートの数は増えない。

根拠: Value 2（MVV 版 1）

### 決定 11: 昇格の Pull Request は `promote` が作り、マージの後に後片付けをしない

昇格の Pull Request の head はベースブランチである。 `merge-when-green` の後片付けは head のブランチとworktreeを消すため、
そのまま使うとベースブランチを消す。 `promote` は `--no-cleanup` と同じに振る舞う（I7）。Pull Request の作成は取り消せる操作なので、
承認の前に作り、承認資料に URL と差分を載せる。

`release-steps.py` の `develop` → `main` の処理を流用する案は採らない。そちらは版数・タグ・GitHub Release と一体で、
`template` の経路の手順である。

根拠: Value 6 / C4（MVV 版 1）

### 決定 12: `fast` / `auto` の `merge` の経路では、開発版のステージを置かない

`merge` の経路では、ベースブランチへのマージがそのまま検証への反映（trygroup の stg）になる。開発版として別に出す物が無い。
本番のステージ（昇格のプラン）は今の本番のリリースプランと同じく先頭で MVV 判定を行い、承認ゲート 2 の数は変わらない。

根拠: Value 1 / Value 4（MVV 版 1）

### 決定 13: 用語集に 3 語を足し、「ミッション」の意味を版数の無いプロジェクトへ広げる

「自動反映の本番チャネル」「リリースの経路」「昇格の Pull Request」はこの設計で初めて名前の要る語である。「リリースの形」
（ `release` の形 `package-plugin` / `service` ほか）とは別の軸なので、同じ語へ入れない。形 `service` の中に `merge` と `manual` の
経路があり得る。用語集の正本は `docs/glossary/glossary.json` で、 `.ndf/` の書き換え（C7）に当たらない。採否は承認ゲート 1 で
利用者が見る。

根拠: Value 8（MVV 版 1）

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| AC1 | carmo-system-console の宣言の仮のリポジトリで、 `main` 宛ての `merge-when-green` が `gh pr merge` を打たずに 10 と `metrics.gate: production-merge` を返す | 判定を飛ばす・宛先を見ずに `base_branch` と比べる |
| AC2 | trygroup の宣言で、 `main` 宛ては 10、 `develop` 宛てはマージへ進む | `delivery` の `auto` の行をすべて本番とみなす |
| AC3 | 版数の無い宣言（すべて `versioned: false` と `[]` の 2 通り）で `new mission` が `--version` 無しで `ok` を返し、プランを書く | `check_mission` が `--version` を必須のまま |
| AC4 | 同じ宣言で `new close` が `--version` ・ `--prod` ・ `release.form` 無しで通り、 `merge` は昇格か置かない、 `manual` は手で行うステージ、 `[]` は配布のステージ無し | `NEEDS` の `close` に `release` が残る・ `none` でステージを置く |
| AC5 | `delivery` が `{"unknown": ...}` と無しの 2 通りで `new close` が通り、手で行うステージの note に理由が出る | 経路を決められないときに止める |
| AC6 | 既存の `release-steps.py` ・ `release-verification-steps.py` ・ `release_templates` のテストが通り、ai-plugins の宣言で `develop` 宛ての `merge-gate` が 0 を返す | 直書きの値を宣言へ移したときに別の値へ解ける |
| AC7 | 壊れた `worktree.json` と壊れた `project.json` のそれぞれで、 `merge-gate` と `merge-when-green` が 10 を返し、理由にファイルが出る | 読めないときに `{}` として扱う |
| AC8 | 10 で止まった同じ Pull Request を `--gate-approved user` で呼ぶとマージへ進み、引数が無ければ何度呼んでも進まない | 承認の引数を見ない・一度止めた後は通す |
| AC9 | 文書のレビューで確かめる（文言のテストは書かない） | — |
| AC10 | `base_branch` と `production_branch` を別の名前にした仮のリポジトリで、 `release` ・ `approval-facts` がその名前へ Pull Request を作り差分を取る | `develop` ・ `main` の直書きが残る |
| AC11 | 4 つのサンプルの宣言で `new mission` と `new close` が通り、 `mission.json` の `リリースの経路` が例の表のとおりになる | 経路の表の行の順序を変える |
| I1 | `production` と `undetermined` のそれぞれで、承認の引数の無い呼び出しが `gh pr merge` を 1 度も打たない（差し替えた `gh` の呼び出しを数える） | CI を待った後に判定する |
| I2 | 判定の表の 1・2・4 行目の入力がそれぞれ `undetermined` になる | どれかを `not-production` へ倒す |
| I3 | `judge_target` の呼び出しの間に `gh` が呼ばれない | 判定のために PR を読み直す |
| I4 | ベースブランチ宛て（本番チャネルと違う）が `not-production` になる | ベースブランチを本番として扱う |
| I5 | `release.form` と `delivery` が両方あるとき `template` になり、どちらも無いとき `manual` と理由になる | 優先を逆にする |
| I6 | `template` のときだけ `--version` が無いと止まる | 経路によらず版数を求める・ `template` でも求めない |
| I7 | `promote` がマージの後にベースブランチとworktreeを消さない | 後片付けを通す |
| I8 | マージした結果の Pull Request の行に `gate_approved` と `head` がある | 引数を結果へ写さない |
| I9 | ai-plugins の宣言で、タグ `ndf--v<版>` ・題 `Release: ndf v<版>` ・リポジトリ名 `devbasex/ai-plugins` ・ブランチ `develop` / `main` が今と同じに出る | 値の出所を変えたときに別の値になる |
| 決定 9 | hook が自動反映の本番チャネル宛ての `gh pr merge` を拒み、ベースブランチ宛ては通す | hook が宛先を読まない |

実測（要求の検証手段）: 4 つのサンプルの宣言を仮のリポジトリへ写し、 `new mission` / `new close` と `merge-gate` の結果を
課題か Pull Request に残す。

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| サンプルの宣言の実際の値 | 例の表の `delivery` は要求の記述から組んだ。#1333 の解析が作った実物と同じかは、実装の実測で写すときに確かめる。違えば例と AC11 の期待を実物に合わせる |
| 承認の後の push | 承認の後に先頭のコミットが変わっても、 `--gate-approved` は同じに効く（決定 4）。今のプランにその流れは無いが、手で push したときは承認した中身と違うものがマージされ得る |
| `normal` と `--state` の助言の MVV 判定 | 昇格のプランには #1400 の助言の MVV 判定を置かない。置くかは、 `merge` の経路のミッションを実際に流してから決める |
| `mvv-gate.py check --gate release` の材料 | 昇格の承認資料（Pull Request と差分）で MVV 判定が今の本番のリリースと同じ質で働くかは、 `fast` / `auto` の実測で確かめる |
| hook の無いランタイム | Kiro CLI と agy で `workflow-guard.sh` が同じに効くかは確かめていない。効かなくても、プランのマージはスクリプトで止まる |
| 宣言の無いリポジトリの振る舞いの変化 | 決定 3 により、宣言の無いリポジトリで本番チャネル（既定ブランチ）宛ての NDF のマージが止まるようになる。影響を受ける利用者の数は測っていない。リリースの説明文（利用者向けの変化）に書く |
