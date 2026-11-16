# #3672 の決定の記録

設計文書 [issue-3672-design.md](issue-3672-design.md) から分けた決定の記録である。不変条件の番号（`I<数>`）と外部の調査の番号
（`STD<数>`・`PRD<数>`・`LIB<数>`）は設計文書を、削除の一覧の番号（`D<数>`）は [issue-3672-design-removal.md](issue-3672-design-removal.md) を指す。

## 決定の記録

### 決定 1: 利用者が 1 か所を読めば方法論が分かるよう、宣言を `.ndf/project.json` の節 `branch_strategy` に置く

要求の未決 1 を決める。`.ndf/project.json` は、リポジトリの形（言語・テスト・CI・デプロイ）を表すファイルで、解析（`project-decl.py`・`ndf project`）が
すでに測って書いている。ブランチ戦略もリポジトリの形の一部で、作る部品（決定 4）と同じファイルに置くと、作る・読む・更新するが 1 つのファイルで閉じる。
旧い 4 か所のうち 2 つ（`production_merge`・`delivery`）が既にこのファイルにある。

次は節へ移さず、今のファイルに残す。ブランチ戦略ではなく、worktree の作り方とリリースの手順の設定だからである。

| 残すもの | ファイル | 理由 |
| --- | --- | --- |
| `guard`・`follow_branch`・`deps`・`localenv`・`testenv`・`remote` | `.ndf/worktree.json` | worktree の作り方。`follow_branch` が追う先の名前は節の `branches.base` から読む |
| `steps`・`changelog`・`undistributed`・`bump_undistributed` | `.ndf/release.json` | リリースの手順と、版を上げる変化の範囲 |

新しいファイル（`.ndf/branch-strategy.json`）は採らない。ファイルの数が 1 つ増え、解析が書くファイルと読み手が読むファイルが分かれる。

根拠: Value 8 / Value 11 / Value 1（MVV 版 8）

### 決定 2: 9 つの事柄を節のキーで表し、組み合わせの検査を 1 本の JSON Schema に置く

要求の S1 の 9 つを、設計文書の「データ構造」の表のキーで表す。組み合わせの規則（`trunk` なのに版数の置き場が無い、など）は JSON Schema の
`if`/`then`（STD7）で書き、読む部品・作る部品・移行のコマンドが同じスキーマで検査する。

スキーマの正本は 11.0 の `plugins/ndf-next/contracts/schemas/declarations/project.schema.json` の `$defs/BranchStrategy` に置く。
10 系はプラグインが別のディレクトリに入るため、同じ中身のコピー（`plugins/ndf/skills/development-workflow/schemas/project.schema.json` の同じ定義）を持ち、
`scripts/check-ndf-contracts.py` が 2 つの一致を確かめる。代表のリポジトリの種類の既定の表（決定 5）も同じ形で正本とコピーを持つ。

**スキーマに既定値を置かない。** 節があれば全部のキーを書く。既定値を置くと、どれか 1 つの運用（develop→main か GitHub flow）が製品の既定になり、
ai-plugins の運用が製品に入る経路が残る（#3617・#3618）。節が無いときは、読む部品が止まって作成のコマンドを示す（I3）。

コードで組み合わせを検査する形は採らない。10 系と 11.0 の 2 か所に同じ規則を書くことになる。

- 作り方: 標準に倣う
- 比べた候補: STD7

根拠: Value 11 / Value 8 / Value 4（MVV 版 8）

### 決定 3: キーと値の名前を #3613 の改名表にそろえ、改名表を「ファイルをまたいで移す」行まで広げる

`release_strategy` とその値 `merge_base_branch`・`release_branch`・`trunk` は #3613 の改名表（`issues/issue-3613-design.md` の「データ構造」）のとおりにする。
取り込み方の表の行 id も `merge-base-branch`・`release-branch`・`trunk` にする。`promotion`・`whole`・`selected` はスキーマに無い。

#3613 の改名表の行は、同じファイルの中の位置の改名（`file`・`old`・`new`）だけを持つ。この課題では、行に `new_file`（移す先のファイル。無ければ同じファイル）を足し、
旧い 4 か所から `.ndf/project.json` の `/branch_strategy/...` へ移す行を持たせる。#3613 の行 `/branch_flow/promotion` → `/branch_flow/release_strategy` は、
この課題の行 `.ndf/worktree.json#/branch_flow/promotion` → `.ndf/project.json#/branch_strategy/release_strategy` が置き換える。
#3613 が先にマージされれば、その行をこの課題の実装で置き換え、後にマージされれば #3613 の実装が置き換えた後の行を持つ。

根拠: Value 14 / Value 11（MVV 版 8）

### 決定 4: 作る・更新する・消すの主体を、系ごとに今の解析のスクリプト 1 つにする

要求の未決 3 を決める。10 系は `project-decl.py` に、11.0 は `ndf project` にサブコマンド `branch-strategy` を足す。サブコマンドの動詞は
`init`（新規）・`measure`（既存の測定）・`write`（答えを受けて書く）・`migrate`（旧い宣言を移す）・`remove`（節を消す）である。2 つの系は同じ入出力の契約（設計文書の「入出力の契約」）を持つ。
Skill は `development-workflow` の `references/project-analysis.md` がこのサブコマンドを打つ手順を持ち、ほかの Skill と文書は宣言を手で書く手順を持たない（D31〜D33）。

今の解析（`measure_branch_flow.py`・`measure_flow.py`）は、直近のマージ済み PR から起点と宛先を推す仕組みと、確からしさの基準をすでに持つ。
別のスクリプトを作ると、同じ PR の読み方が 2 か所になる。

手で書き換えた節は検出しない（要求の未決 7）。読む部品が毎回スキーマで検査するので、手で書いた値もスキーマを通れば正しく動き、通らなければ止まる。
書いた中身のハッシュを節に持たせる形は、同じ事実（節の中身）を 2 か所に持つことになるため採らない。

- 作り方: 自前で作る・ライブラリやサービスを使う
- 比べた候補: PRD5 / LIB6

根拠: Value 3 / Value 11 / Value 1（MVV 版 8）

### 決定 5: 新規のリポジトリには、代表のリポジトリの種類ごとの既定を 1 つの表から書く

代表のリポジトリの種類を 5 つ（`web-develop`・`web-trunk`・`mobile`・`docs`・`plugin`）とし、種類ごとの値を表 `branch-strategy-presets.json` の行に持つ（値は設計文書の「データ構造」の表）。
`init --kind <種類>` は、その行に、リポジトリから測る値（本番ブランチの名前・プラグインの名前・merge queue を使えるか）を足して書く。
merge queue を使えるか（Organization の public か、Enterprise Cloud の private。STD3）が分からないときは、`merge-queue` と `strict` を候補にして人へ問う。

種類を渡さず、履歴も無いときは書かず、5 つの種類を候補にした問いを返す（受け入れ条件 8）。種類を推して書く形は採らない。
新規のリポジトリには推す材料（PR・タグ）が無く、推した値は根拠を持たない。

根拠: Value 4 / Value 1 / Value 13（MVV 版 8）

### 決定 6: 既存のリポジトリでは、値ごとに 3 件以上・9 割以上の事例がそろったときだけ根拠とみなし、食い違いと不足は書かずに問う

要求の未決 4 を決める。標本（直近のマージ済み PR 100 件・タグ・Release・ruleset・CI のワークフロー）から値ごとに数え、合う事例が 3 件以上かつ 9 割以上なら、
その値と事例の一覧（PR の番号・タグ・ファイルと行）を根拠として結果に出す。今の解析の「高」の基準（`measure_flow.py` の `MIN_FLOW_SAMPLES`・`HIGH`）と同じ値である。
「中」（6 割以上）は書かずに問う。推した値の 4 割が外れうる値を宣言に書くと、誤った宛先で PR が作られる。

文書（`README.md`・`CONTRIBUTING.md`・`AGENTS.md`）は、ブランチ名と宛先を述べる行を拾い、測った値と違えば食い違いとして問う（受け入れ条件 11）。
文書だけが述べて事例が無い値も、根拠不足として問う。答えが組み合わせの検査を通れば書き、通らなければ理由を添えて問い直す（E4）。

根拠: Value 13 / Value 1 / Value 14（MVV 版 8）

### 決定 7: 承認とマージの形を 4 つのキーで宣言し、証跡の書き手をケースごとに 1 つに決める

承認の対象（`approval.target`）・承認の受け方（`approval.via`）・マージの主体（`merge.actor`）・マージの方式（`merge.method`）の 4 つで、
承認を取る場所とマージの主体を表す。証跡の書き手の表は設計文書の「処理の流れ」の「承認とマージ」にある。

- `approval.via: review` は、本番ブランチの ruleset が PR のレビューの承認を求めるリポジトリのための値である。GitHub のレビューの承認を、リリース承認ゲートの答えとして読む。
  NDF は問いを送らず、証跡も書かない。2 つ目の承認（03-release の §1.2 のリリース 8・14・23）は、この値か ruleset の承認の数 0 で起きなくなる
- `approval.via: gate` で ruleset がレビューの承認を求めるときは、組み合わせの不一致として止まる（I10）。2 つ目の承認を求める工程を作らない
- マージの方式は `merge-queue`・`strict`・`direct` の 3 つにする（要求の未決 6）。`strict` は、NDF が head が本番ブランチの先端を含むかを確かめてからマージする方式で、
  ruleset が無いリポジトリ（private の Free）でも NDF の確かめだけで働く。ruleset があれば GitHub も同じ規則を強制する
- #3641 の `serial`（NDF が本番ブランチごとのロックを取り、一時のマージコミットで CI を回す）は採らない。merge queue と同じ役割を自前で作ることになり（LIB3・LIB4）、
  使えないリポジトリは `strict` で組み合わせを確かめられる
- 管理者の迂回（`merge.admin: true`）は `direct` のときだけ許す。`merge-queue`・`strict` で迂回すると、組み合わせの確かめを飛ばす（#3572 の I17）

承認した head と違う head をマージしない仕組み（#3572 の決定 1・18・19 の head の一致の確認、本番のプランから worker を外す）は、そのまま取り込む。

- 作り方: ライブラリやサービスを使う・自前で作る
- 比べた候補: STD1 / STD3 / LIB3 / LIB4

根拠: Value 1 / Value 3 / C2 / Value 12（MVV 版 8）

### 決定 8: `trunk` の版数は、マージの後の CD の版上げの PR を使わず、承認の対象で 2 つに分ける

`trunk` の行で版数を持つリポジトリは、承認の対象を `release_pr` にする。NDF のリリースの工程が本番ブランチの先端から `release/v<版>` を切り、
版数と CHANGELOG を書いた版上げの PR を作り、その PR でリリース承認ゲートを 1 回通してマージする。スプリントの PR は承認ゲートを通らずに本番ブランチへ入る（`sprint.integrate: check`）。
リリースは版上げの PR のマージの 1 回で、Claude Code の利用者には版数が変わったときだけ届く（STD4）。

版数を持たないリポジトリ（Web サービスの継続的なデプロイ・文書）は、承認の対象を `production_pr` にし、本番ブランチ宛ての PR ごとにリリース承認ゲートを通す。版数の置き場は `none` にする。

#3572 の決定 7（マージの後に CD が版上げの PR を作り、迂回せずに入れてからタグを作る）は採らない。承認の後にもう 1 本の PR がマージの方式を通り、
キューの追い越し・承認の無い PR の自動マージの止まり（#3641 の設計の I14）を別に守る必要がある。版上げを承認の前に置けば、承認した head に版数が入っている。

版上げの PR は、head が本番ブランチの先端を含むときだけキューの先頭へ入れる（I7）。先端が動いたら作り直して承認を取り直す（E10）。

release-please（LIB1）は採らない。上げ幅を Conventional Commits から読み、このリポジトリの題の書式（`Add:`・`Fix:`）と合わず、タグの区切りも公式の形（STD5）と違う。
版上げと CHANGELOG は、今の 10 系の `bump`・`changelog` を使い続ける。

- 作り方: 自前で作る
- 比べた候補: LIB1 / LIB2 / STD4 / PRD1

根拠: Value 3 / Value 5 / Value 13（MVV 版 8）

### 決定 9: 宣言を変える前に始めたスプリントが変えた後の宣言で動かないよう、スプリントの開始時に節の全体のスナップショットを取る

#3310・#3598 を解く。今は `branch_flow` の 4 項目だけのスナップショット（`flow_snapshot`）を取り、`base_branch` などは今の宣言を読む。
スナップショットを節の全体（`branch_strategy`）にし、スプリントの中の部品はスナップショットを読む。スナップショットと今の節が違えば、違うキーを結果に出す。

根拠: Value 3 / Value 8（MVV 版 8）

### 決定 10: 10 系と 11.0 の移行のコマンドを、同じ移す行の表から作る

要求の未決 2 を決める。10 系は `project-decl.py branch-strategy migrate`、11.0 は #3613 の `ndf settings migrate` を使う。
どちらも決定 3 の移す行の表（正本は 11.0 の `contracts/domains/ndf-settings-renames.json`、10 系はコピー）を読み、差分を示す実行と、示した差分のハッシュを渡して書く実行に分ける（#3613 の決定 12）。
書く前に新しいスキーマで検査し、旧い 4 か所のキーを消して新しい節を書く。

10 系に 11.0 の `ndf settings migrate` を呼ばせる形は採らない。10 系を使うリポジトリに 11.0 が入っているとは限らない。

読む部品は、旧いキーが 1 つでも残るリポジトリで止まり、キーの位置と移行のコマンドを理由に出す（I4、受け入れ条件 22）。
旧いキーを新しいキーの別名として読む形は採らない（後方互換は要らない。要求の前提 1）。

- 作り方: 自前で作る
- 比べた候補: LIB5 / STD6

根拠: Value 4 / C7 / Value 11（MVV 版 8）

### 決定 11: `delivery` の行は、ブランチ戦略に入らない行も含めて全部 `release.deliveries` へ移す

要求の未決 8 を決める。`delivery` の行（Web サービスのデプロイ先・プラグインの公開）は、本番へ何が・どのチャネルで・どう届くかを表し、
S1 の「リリースの形」と「CD の有無」に当たる。行を 2 つの置き場に分けると、同じリリースの経路が 2 か所に書かれる。
行の形（`target`・`kind`・`trigger`・`channel`・`versioned`・`publishes_release`・`result`）は変えずに移す。

根拠: Value 8 / Value 11（MVV 版 8）

### 決定 12: ai-plugins の値を plugin とマーケットプレイスの開発の慣行から決め、「plugin」の種類の既定と同じにする

値と、今の運用から変える点は設計文書の「ai-plugins の値」にある。決めた順序は次のとおりである。

1. 利用者へ届く単位は Claude Code の仕様で決まる。既に入れた利用者には `plugin.json` の版数が変わったときだけ届き、版数を持たせれば「マージ」と「リリース」が分かれる（STD4）。
   そのため、ブランチを分けてリリースを分ける理由が無い
2. 調べた 8 リポジトリのうち 7 つが main だけで開発し、develop を持つのは 1 つだった（PRD1〜PRD4）。trunk-based development も長く残るブランチを勧めない（STD2）
3. タグは公式の形 `<plugin>--v<version>`（STD5）で、今の ai-plugins の形と同じである
4. 利用者の取得元の登録は変えない。既定ブランチ main を指す今の登録のまま、版上げの PR のマージで届く（P1 の影響を最小にする）
5. マージは merge queue（要求の前提 4）で、ruleset の承認の数を 0 にして、リリース承認ゲートを唯一の承認にする

GitHub flow を前提に置き、今の運用の値をそのまま移す形は採らない。上の 1〜5 は、それぞれの出典から導いた。develop→main の行（PRD4 の obra/superpowers が採る）は、
新しく入れる利用者に未リリースの変更を見せない利点があるが、長く残るブランチの間で版数と検査の範囲を運ぶ事故（03-release の §2.A）が ai-plugins で起きているため採らない。

根拠: Value 12 / Value 5 / P1 / C2（MVV 版 8）

### 決定 13: ai-plugins の運用が製品に入らないことを、今の 2 つの CI の検査を広げて止める

受け入れ条件 16 は、`scripts/check-skill-repo-assumptions.py`（#3617 が ndf-next へ広げた語の検査）に次の 3 つの規則を足して満たす。

- スキーマ `$defs/BranchStrategy` が `default` を持たない（決定 2）
- 既定の表 `branch-strategy-presets.json` の値に、ai-plugins の名前（リポジトリ名・組織名・`ndf--v` の決め打ちのタグ）が無い。タグの形は `{plugin}--v{version}` の置き換えの語で書く
- 10 系・11.0 のコードと Skill の本文に、ブランチ名 `develop`・`main` を宛先として決め打つ形と、`--admin` を条件なしで渡す形が無い（今の語 `quoted-develop`・`admin-merge` の当たりのうち、削除の一覧の行に当たるものを例外から外す）

削除の一覧（受け入れ条件 21）は、新しい検査 `scripts/check-legacy-branch-decl.py` が一覧のデータ `scripts/legacy-branch-decl.json` の各行を照らす。
選べる値が減っていないかは今の `scripts/check-ndf-choices.py` が見る。行の改名は基準のファイルの `removed` に #3672 の番号と理由を持つ行で書く。

根拠: Value 4 / Value 13 / P2（MVV 版 8）

### 決定 14: 置き換える設計と開いている設計 PR の行き先を、取り込む部分を名指して決める

#3649・#3650 を閉じ、取り込む部分（承認した head の一致の確認・本番のプランから worker を外す・`merge-queue` と `strict` の判定・人のマージの記録・10 系の `trunk` の行）を
この設計の決定 7・8 と不変条件が持つ。一覧は [issue-3672-design-removal.md](issue-3672-design-removal.md) の「置き換える設計と開いている設計 PR の行き先」にある。
閉じる操作そのものは計画の運用が行う（要求の「含まない」）。

根拠: Value 11 / Value 14（MVV 版 8）
