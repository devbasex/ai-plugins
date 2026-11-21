# 依存の更新の決定の記録（#3370 #3691）

[issue-3370-3691-design.md](./issue-3370-3691-design.md) から決定のセクションを分けた文書である。外部の調査の番号（`LIB<数>`・`M<数>`）は [issue-3370-3691-design-survey.md](./issue-3370-3691-design-survey.md) にある。決定 1〜5・7・8 は前の設計（設計 PR #3522）の決定を 11 系の配置へ直したもので、変えた所は各決定に書いた。

## 決定の記録

### 決定 1: 自作の保守を負わずに遅れの知らせとバージョンアップを得るため、依存の更新の仕組みに Renovate を採り、#3347 の決定 107 の自作を置き換える

- **質問**: 依存の遅れを知らせ、バージョンを上げる仕組みを、Renovate・Dependabot・自作のどれで得るか
- **選んだ案**: Renovate を採る。7 日待つ・固定と理由・1 件の Issue での知らせ・承認してから PR を作る形を、コードを書かずに設定だけで満たす（3 案の比較の観点 1・5・知らせの形）。#3691 が広げる範囲（ほかの lock・推移的な依存・コードの中の固定）も同じ設定で扱える（観点 3・4）。#3347 の決定 107 との関係は**置き換え**で、「Dependabot を採らない」は Python の依存についてそのまま残し、「`scripts/plugin-deps.py` を自作する」を Renovate に置き換える
- 採らなかった案: Dependabot（固定の理由を書く項目を持たず、知らせが PR の単位で、1 件の Issue にまとめる設定が無い）
- 採らなかった案: 自作（決定 106 をそのまま表せるが、知らせ・上げる操作・推移的な依存をスクリプトと単体テストとワークフローで保守し続ける）
- **代償**: Renovate の設定の語とバージョンごとのキーの違いを追う手間を負う。脆弱性・確かめるテストの一覧・外部 CLI の照らし合わせは Renovate の外にあり、小さなスクリプトを自分で持つ（決定 9・10・11）
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB2 / LIB3 / LIB4
- 根拠: Value 12 / Value 11 / Value 6（MVV バージョン 9）

### 決定 2: 起動の時刻と条件をこのリポジトリで決め、PR の CI を自動で走らせるため、Renovate を GitHub Actions の上で GitHub App の token で動かし、Renovate 自身のバージョンは `.github/renovate-version` に置いて 7 日待って上げる

- **質問**: Renovate をどこで、どの token で動かし、Renovate 自身のバージョンをどう決めるか
- **選んだ案**: `renovatebot/github-action` を週 1 回の定時と手動で動かし、GitHub App から実行ごとに短い期限の token を作る（`actions/create-github-app-token`）。Renovate のバージョンは 1 行のファイル `.github/renovate-version` に置き、ワークフローがそれを読んで `renovate-version` に渡す。Renovate は正規表現の仕組み（決定 6 の `customManagers`）でこのファイルを拾い、ほかの依存と同じく 7 日待って上げる。依存の一覧の Issue のチェックは Renovate の待つ日数を飛ばせ、このファイルと `UV_VERSION` は uv の再解決を通らないため、チェックの後にも公開日を確かめる。PR では `script-structure.yml` の `age` のステップが、実行の前には `renovate.yml` が、版の形（`^[0-9]+\.[0-9]+\.[0-9]+$`）と公開から 7 日を確かめ、7 日に満たないか公開日が読めない版を拒む。PR の `renovate-config` のジョブは、宛先の先頭の検証器で確かめてから PR の版の Renovate を起動する。ファイルはワークフローの外にあるので、App に Workflows の権限が要らない
- 採らなかった案: Mend が運用する Renovate のアプリ（導入の手順とリポジトリの中身の扱いを確かめられず、起動の時刻と条件をこのリポジトリで決められない）
- 採らなかった案: PAT（特定の人の権限に結びつく）
- 採らなかった案: `GITHUB_TOKEN`（Renovate の PR の上の CI が手で承認するまで走らず、Actions に PR を作らせる設定の変更が要る）
- 採らなかった案: `renovate-version` を渡さず action の既定に任せる（既定は動くタグ `44` で、実行の時点の 44 系の最新を使い、7 日を待たない。LIB10）
- 採らなかった案: `renovate-version` をワークフローに書いて固定する（前の設計の形。書き換えには Workflows の権限か手の更新が要り、Dependabot はこの入力を読まない）
- **代償**: GitHub App の作成と秘密鍵の管理を人が負う（人へ残す判断の C1・C2）。バージョンの保存先が設定とワークフローの外に 1 つ増える
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB9 / LIB10 / LIB17 / LIB2
- 根拠: Value 2 / Value 3（MVV バージョン 9）

### 決定 3: 同じ lock を触る PR の衝突を減らすため、更新の PR はチェックを入れたときだけ作り、lock ごとに 1 本にまとめる

- **質問**: 更新の PR をいつ、どの単位で作るか
- **選んだ案**: `dependencyDashboardApproval` で、依存の一覧の Issue にチェックを入れるまで PR を作らない。uv のプロジェクトと npm の lock のディレクトリごとに 1 つのまとまり（`groupName`。major も分けない）を置き、1 回のチェックで lock ごとに 1 本の PR にする。lock の作り直し（決定 6）は、Renovate が通常の更新と同じまとまりを扱わないため別の行と別の PR にする。同じ lock を 2 本の PR が同時に触らないよう、Renovate のブランチを同時に 1 本に限り（`branchConcurrentLimit: 1`）、チェックの入った候補を lock ごとの更新 → ruamel-yaml の横断の更新 → lock の作り直しの順（`prPriority`）に 1 本ずつ作る。前の設計の「`plugins/ndf` の更新を 1 本」を、対象の lock の数（5 つ）へ広げた
- 採らなかった案: すべての lock を 1 本の PR にまとめる（1 つの lock が解決できないと全部が止まり、差分が大きく確かめるテストの一覧も長くなる）
- 採らなかった案: 依存ごとに PR を作る既定の形（子の PR が動いている間に PR が届き続け、`uv.lock` の衝突が増える）
- 採らなかった案: lock ごとの更新と lock の作り直しを同じ `groupName` に入れる（44.257.5 の `updates/generate.ts` が扱わない組み合わせ）
- **代償**: 複数の行にチェックを入れても PR は 1 本ずつ届き、次の 1 本は前の PR のマージの後の定時か手動の実行で作られる。同じパッケージを 2 つの lock でそろえる ruamel-yaml は横断のまとまりで 1 本にする（詳細設計の規則 9）
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1
- 根拠: Value 1 / Value 5（MVV バージョン 9）

### 決定 4: 7 日の守りを 1 か所の取りこぼしで破らないため、Renovate・Dependabot・uv の 3 か所に同じ 7 日を置き、値の一致をテストで縛る

- **質問**: 7 日待つ規則を、どの仕組みのどこに置くか
- **選んだ案**: 7 日を 3 つの仕組みに置く。Renovate の `minimumReleaseAge: "7 days"`、Dependabot の `cooldown` の `default-days: 7`、対象の 4 つの uv のプロジェクトの `[tool.uv] exclude-newer = "7 days"` である。正は `plugins/ndf/pyproject.toml` の値で、ほかがそれに合わせることを設定の検査のテストが縛る（I6）。lock を作り直すのは uv なので、Renovate が外しそこねても uv が拒む（M2）。決定 106 の「7 日たった最新の安定版が 365 日より古ければプレリリース」は、Renovate の設定では表せない（`matchCurrentAge` は今採用している版の年齢を見るため、今の版が 365 日より古いだけで、最近の安定版があってもプレリリースを選びうる）。そこで週 1 回の `report` が、候補の最新の安定版の公開日で判定し、当たる依存を「プレリリースを選べる依存」に出す。人がその依存を規則 6 の `matchPackageNames` に書いたときだけ `ignoreUnstable` を外す。安定版とプレリリースが両方あるときの期待値は、7 日たった最新の安定版が 365 日以内ならその安定版、365 日より古ければより新しいプレリリースである。Renovate は定義にプレリリースのバージョンを `==` で書くので、uv はその指定を見てプレリリースを解決に入れる
- 採らなかった案: `matchCurrentAge: "> 365 days"` で近づける（前の設計の形。今の版が古く最近の安定版とより新しいプレリリースが共存すると、決定 106 と違いプレリリースを選ぶ）
- 採らなかった案: Renovate だけに置く（Renovate は uv の設定を読まず、lock の作り直しと手の `uv lock` で推移的な依存に 7 日が効かない）
- 採らなかった案: uv だけに置く（Renovate が 7 日に満たないバージョンの PR を作り、解決の失敗が PR ごとに出る）
- **代償**: 同じ値を 6 か所に持つ。一致のテストで縛る
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB5 / LIB3
- 根拠: Value 3 / Value 11（MVV バージョン 9）

### 決定 5: 固定を解いてよい時機を機械で知るため、固定する理由を Renovate の規則の中で種類・相手・制約・見直す日の欄に分けて書く

- **質問**: 固定する理由（httpx の固定・DBOS を対象から外すこと）を、どこに、どの形で持つか
- **選んだ案**: Renovate の依存ごとの規則（`packageRules`）の `description` に、`種類:`・`相手:`・`制約:`・`見直す日:`・`理由:` の 5 行を決まった順で書く（行の形は詳細設計）。種類は `相手の制約`・`自分の非互換`・`対象の外`・`プレリリースを選ぶ`（決定 4）・`脆弱性の例外`（決定 9）の 5 つである。週 1 回のサプライチェーンの検査がこの欄を読み、相手の採用できる最新の版が固定した依存の制約を消したか、固定の版より新しい版を新たに許すように緩めたか、`見直す日:` を過ぎた固定を、脆弱性の知らせの「解いてよい固定」に出す（決定 9）。制約の文字列の違いでは判定しない（並びの違い・同じ意味の書き換え・強化は出さない。判定は詳細設計の「処理の手順」）。固定の一覧は依存の一覧の Issue の頭にも出し、httpx が遅れでなく固定として見える（#3370 の受け入れ条件 8）。前の設計の決定 5（理由は自由文）を置き換える
- 採らなかった案: 自由文の理由（前の設計の形。相手が制約を外しても機械では分からない）
- 採らなかった案: `pyproject.toml` の `[tool.ndf.deps.hold]` に別の表を持つ（#3347 の決定 106 の形。Renovate が読む規則と同じ事実を 2 か所に持つ）
- **代償**: Renovate の `description` は人が読む説明の欄で、決まった行の形はこのリポジトリだけの約束である。形はテストが縛る（I3）
- 作り方: 自前で作る・ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB11
- 根拠: Value 8 / Value 11 / Value 14（MVV バージョン 9）

### 決定 6: 漏れなく上げるため、対象を追跡しているすべての lock（`docs/presentations/` を除く）と推移的な依存と `UV_VERSION` へ広げる

- **質問**: Renovate の対象をどこまで広げ、推移的な依存とコードの中の固定をどう上げるか
- **選んだ案**: 対象は追跡している lock（`git ls-files` の `uv.lock` と `package-lock.json`）から Renovate の `ignorePaths` に書いた `docs/presentations/**` を除いたものすべてにする（I9）。今は 4 つの uv のプロジェクトとスライドのテンプレートの npm の lock の 5 つである。推移的な依存は、Renovate の lock の作り直し（`lockFileMaintenance`。uv では `uv lock --upgrade`、npm では lock を消して作り直す）を `pep621` と `npm` の仕組みの設定で有効にし、承認を求めて週 1 回の候補に出す（LIB12）。直接の依存に新しい版が出ていなくても、作り直しの行にチェックを入れれば推移的な依存だけを上げられる（#3691 の受け入れ条件 1）。lock の作り直しには Renovate の待つ日数を置かない（`minimumReleaseAge: null`）。作り直しで上がるパッケージには Renovate が見る日時が無く、置くと待ちのチェックが残り続けるためである。7 日は解決する側が守る。uv では `exclude-newer`（決定 4）、npm ではその lock のディレクトリの `.npmrc` の `min-release-age=7` で、Renovate が使う npm を `constraints.npm` で `min-release-age` を持つ版以上に固定する。Renovate の npm の後処理は、特定の解決の失敗で `--before` を外して再試行することがあり（44.257.5 の `post-update/npm.ts`）、直接の依存の PR でも 7 日に満たない推移的な依存が lock に入りうる。そこで生成された lock も検査する。lock を変えた PR では `script-structure.yml` の `age` のステップが、新しく入ったすべてのパッケージの版の公開日を読み、7 日に満たないか公開日が読めないものがあれば落とす（uv の lock と npm の lock の両方）。`UV_VERSION` の 2 か所と `.github/renovate-version` は正規表現の仕組み（`customManagers`）で GitHub の releases（`astral-sh/uv`・`renovatebot/renovate`）から拾う（LIB13）。前の設計の決定 6（`plugins/ndf` だけに絞る）を置き換える
- 採らなかった案: `plugins/ndf` だけに絞る（前の決定 6。根の lock の 47 本の遅れと playwright-kit の脆弱性が残る）
- 採らなかった案: 推移的な依存を 1 本ずつ定義に書いて上げる（推移的な依存を直接の依存に変え、定義が膨らむ）
- 採らなかった案: `docs/presentations/` の lock も対象にする（発表日時点の記録で、以後の変更に追随しない）
- 採らなかった案: npm の lock の作り直しを有効にしない（前の版のこの設計。直接の依存が `pptxgenjs` 1 本で、その新しい版が出るまで推移的な依存の行が出ず、調査の時点で既にある推移的な依存の high の報告を上げる経路が無い）
- **代償**: 依存の一覧の Issue の行が増える。lock の作り直しの PR は差分が大きく、確かめるテストの一覧（決定 10）が要る。npm の 7 日は `min-release-age` を持つ npm の版に依り、PR の上の公開日の検査が最後の守りになる
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB12 / LIB13
- 根拠: Value 6 / Value 13（MVV バージョン 9）

### 決定 7: 7 日の規則を最初のバージョンアップにも当てるため、実装の PR は上げる先を実装の時点で uv に決めさせ、DBOS を対象の外に置く

- **質問**: 最初のバージョンアップの上げる先をどう決め、DBOS をどう扱うか
- **選んだ案**: 実装の PR は、`exclude-newer = "7 days"` を置いた後に、DBOS・httpx・`sqlalchemy` を除く `==` の直接の依存（dev グループを含む）の宣言を、公開から 7 日たった上げる先（決定 106）へ書き換え、その宣言から `uv lock --upgrade` で lock を作り直す。`uv lock --upgrade` だけでは `==` の宣言を越えられないためである（手順は詳細設計の「依存の定義と lock と一致の検査」）。DBOS と httpx は `==` のまま残して固定を保つ。バージョンが変わった依存の一覧を PR 本文に書く。DBOS は規則に `enabled: false` と欄つきの理由（種類 `対象の外`）を書き、C1+ の続きが上げる。最初の 1 回を Renovate の PR にしないのは、Renovate の導入（App と秘密）がリリース承認ゲートの後になるためである
- 採らなかった案: #3347 の「14 本の最新のバージョン」の表をそのまま写す（公開から 7 日に満たないバージョンを含み、M2 で uv が拒んだ）
- 採らなかった案: DBOS も対象に含める（1 回のチェックで DBOS も上がり、C1+ の続きの持ち分を越える）
- **代償**: 上げる先が実装の時点まで決まらず、落ちるテストの数も実装の PR で分かる
- 根拠: Value 13 / Value 3（MVV バージョン 9）

### 決定 8: 解決できない上げ方を失敗として返すため、lock の作り直しの成否を Renovate のチェックで常に出し、赤のときは更新の PR をマージしない

- **質問**: 解決できない上げ方（#3370 の受け入れ条件 9）の失敗をどこで返すか
- **選んだ案**: `statusCheckWhen.artifactError` を `"always"` にし、更新の PR に `renovate/artifacts` を常に出す（LIB8）。赤の PR をマージしないことで、`main` の定義と lock を元のまま残す。`renovate/artifacts` そのものは必須のチェックに足さない（更新の PR にしか付かず、必須にすると付かない PR が止まる）。代わりに、関門の workflow `renovate-gate.yml` を `pull_request_target` と `status` の出来事で既定ブランチの定義と検証器で動かし、すべての PR の先端へ commit status `ndf/renovate-gate` を付けて `main` の ruleset の必須にする（出元を GitHub Actions のアプリに限る。`ndf/release-approval` と同じ形）。head のブランチが `renovate/` の同じリポジトリの PR だけ、今の head の `renovate/artifacts` が Renovate の App の書いた `success` でなければ `success` にしない。`pull_request` で動く `script-structure-check` の中に置かないのは、PR の中で関門の条件と検証器を書き換えられるためである。`uv-pin` の失敗のように `uv lock --check` では見つからない失敗も、merge queue と `ndf ci merge` の手前で止まる。PR もブランチも作られない失敗（定義の差分も既存のブランチも無い lock の作り直しの解決の失敗）は、`renovate.yml` が Renovate の記録を読んで実行を失敗で終え、ジョブの要約に並べる。受け入れ条件 9 の上げ方は、`main` の祖先のコミットから切った検証用のブランチ `verify/3370-httpx` を `renovate.yml` の手動の実行に宛先として渡し、httpx に `1.0.dev6` を許す規則を `renovate.yml` に固定した検証用の全体の設定（`RENOVATE_FORCE`）で与えて 1 度見る（既定では `baseBranchPatterns` が `main` だけのため。ブランチの側の設定とコードは信頼しない）
- 採らなかった案: `renovate/artifacts` を `main` の必須のチェックにする（Renovate の PR 以外の PR が止まる）
- 採らなかった案: 赤のチェックを見て止めるのを人に任せる（前の版のこの設計。merge queue が赤の PR を受け入れる）
- **代償**: すべての PR の push ごとに `renovate-gate.yml` のジョブが 1 本増える（Renovate の PR 以外は checkout も Python も打たずに `gh api` で `success` を付けるだけ）。Renovate の状態の書き込みごとと merge queue のエントリーごとにも 1 本ずつ増える。`main` の ruleset の必須に `ndf/renovate-gate` を足す操作が承認ゲート 2 に加わる
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB8
- 根拠: Value 13 / Value 3（MVV バージョン 9）

### 決定 9: 承認と 7 日を飛ばさずに脆弱性を知るため、脆弱性は週 1 回の `uv audit` と `npm audit` で 1 件の Issue に知らせ、Renovate の脆弱性の PR は有効にしない

- **質問**: 対象の lock の既知の脆弱性を、どの仕組みで見つけ、どこへ知らせ、7 日に満たない直しをどう入れるか
- **選んだ案**: `deps-weekly.yml` が週 1 回（と手動）、脆弱性を取得する。uv のプロジェクトでは `uv audit --frozen --output-format json`、npm の lock では `npm audit --package-lock-only --json` を打つ。`scripts/plugin-deps.py report` が結果を 1 件の脆弱性の知らせ（本文の目印 `<!-- ndf:supply-chain-report -->` と作成者 `github-actions[bot]` で見分ける。ラベル `supply-chain` は絞り込みにだけ使う）に書き直し、0 件なら閉じる。管理の対象を 1 件に決められなければ、どの Issue にも触れずに止まる。1 つでも取得が失敗したら Issue に触れずに失敗で終わる（I11）。直しは依存の一覧の Issue のチェックで上げる。直しのバージョンが公開から 7 日に満たないとき、またはダウングレードが要るとき（M12 の npm）は、Renovate の承認の経路では上げられない（Renovate の待つ日数が候補から外し、一覧のチェックは上げる向きにしか働かない）。そのときは、脆弱性の知らせを見た開発者が例外を 2 つの PR に分けて入れる。公開日の検査（`age`）は例外を PR の設定でなく宛先に入った設定からだけ読む（同じ PR で足した例外でその PR の版を通さない）ため、1 つの PR にまとめると止まる。1 つ目の PR は `renovate.json` に勧告の ID・`<パッケージ>==<版>`・外す日を持つ規則（種類 `脆弱性の例外`、`enabled: false`）だけを足し、レビューで承認してマージする。2 つ目の PR が、パッケージマネージャー側の例外と依存の更新を入れる。uv では、その lock の `pyproject.toml` に `exclude-newer-package` の例外（注記に勧告の ID・外す日・版）を足し、直接の依存なら `==` をその版に書き換え、推移的な依存なら `uv lock --upgrade-package <パッケージ>==<版>` で lock を作り直す。npm では、`package.json` の `overrides` にその版を書き、`npm install --min-release-age=0` で lock を作り直す（ダウングレードも同じ形）。`age` は宛先に入った規則の組だけを 7 日の外として通す。外す日を過ぎると同じ知らせの「解いてよい固定」に出る。どちらの PR も通常のレビューとマージの手順を通る
- 採らなかった案: Renovate の `osvVulnerabilityAlerts`・`vulnerabilityAlerts`（どちらの脆弱性の PR も承認・待つ日数・予定を飛ばして作られ、`exclude-newer` があると 7 日に満たない直しで lock の作り直しが失敗する。`osvVulnerabilityAlerts` は実験的な機能で、対象は直接の依存だけである。LIB15）
- 採らなかった案: Dependabot alerts（このリポジトリの依存グラフは `uv.lock` を読まず、マニフェストが 0 件である。M3）
- 採らなかった案: 脆弱性を依存の一覧の Issue に混ぜる（Renovate が本文を書き直すため、ほかの書き手が入れない）
- **代償**: `uv audit` はプレビューで、出力の形が変わると検査が失敗で終わる（I11 の失敗として見える）。例外を足し、7 日に満たない直しを手で入れるのは人の手で、PR のレビューが承認になる。例外の規則の PR を先にマージする分、直しが入るまでに PR が 1 本とレビューが 1 回増える
- 作り方: 自前で作る・ライブラリやサービスを使う
- 比べた候補: LIB15 / LIB19 / LIB18 / LIB3
- 根拠: Value 13 / Value 6 / Value 1（MVV バージョン 9）

### 決定 10: 上げた後に見るテストが分かるよう、lock を変えた PR に、変わったパッケージを import するコードを通るテストの一覧を出す

- **質問**: 「変わった依存の公開 API を使う箇所のテスト」を、どう機械で選ぶか
- **選んだ案**: すべての PR で走る `script-structure.yml` のステップが `scripts/plugin-deps.py affected` を打つ。比べるのは、同じイベントで取った PR のマージのコミットとその第 1 親である。lock の差分から、変わったパッケージの名前・前と後のバージョンを出す。uv の lock では配布名を import の名前へ対応表で直し（`python-dotenv` → `dotenv` など）、その lock のプロジェクトのソースの `import` 文（構文木で読む）と照らす。npm の lock では、そのディレクトリの JavaScript の `require` と `import` と照らす。使うモジュールと、そのモジュールを使うテスト（無ければ「テストが無い」）を並べる。推移的な依存は、lock の中の依存の関係を逆に辿った親を添える（`uv tree` は lock を書き換えうるので打たない）。`pull_request` の token に書き込みを与えないため、一覧はデータとして artifact に置き、`workflow_run` で起動する `deps-affected-comment.yml` が既定のブランチのコードで PR 番号と head の SHA を確かめてから 1 件のコメントに書く（目印の文で引いて書き直す）。投稿先は GitHub が実行に結び付けた PR だけで決める。fork からの PR ではこの結び付きが空になり、同じ fork のブランチと SHA から出した別の PR と区別できないため、一覧を作らずコメントも付けない（一覧の生成・artifact・投稿のジョブを打たない）。一覧を作れないときも理由で書き直し（#3691 の E6）、lock の差分が無くなった PR では前のコメントを消す
- 採らなかった案: `pull_request` のジョブに `pull-requests: write` を付けて投稿する（fork の PR では書けない。`pull_request_target` で PR のコードを動かすと書き込みの token に PR のコードが届く）
- 採らなかった案: lock のパスで絞った `pull_request` のワークフローにする（lock の変更を取り消した push で起動せず、前の一覧が残る）
- 採らなかった案: 公開 API の差分を取る（API の差分を出す道具はパッケージの形ごとに違い、型の無いパッケージでは取れない）
- 採らなかった案: すべてのテストを打つだけにする（今の CI と同じで、どこを見ればよいかが分からない。3380-6）
- **代償**: 文字列での import やプラグインの読み込みは拾えない（未確認のまま残ること）。fork からの PR には一覧が出ない
- 作り方: 自前で作る
- 比べた候補: LIB20 / LIB21 / LIB4
- 根拠: Value 13 / Value 1（MVV バージョン 9）

### 決定 11: 古い CLI での失敗を利用者の環境で初めて知ることが無いよう、外部 CLI の下限を NDF のコントラクトの表に置き、起動の前に比べて止まる

- **質問**: `gh`・`claude`・`codex` の下限をどこに置き、いつ比べ、`--help` をどう照らすか。本体の Claude Code と Codex は `claude` と `codex` の CLI そのものなので、表は 3 行である
- **選んだ案**: 下限の表 `plugins/ndf/contracts/domains/cli-floors.json`（新設）を置く。CLI ごとに実行ファイル・バージョンを出す引数・読み方の正規表現・下限・`--help` の見本を持ち、`claude` の見本は今の `contracts/data/claude-cli.json` を指す。NDF の `cli_floor.require(<名前>, <argv0>, <環境>, <作業ディレクトリ>)` が、その CLI を起動する箇所の前（既存の起動の前提を通った後）で、起動と同じ条件で解いた実行ファイルのバージョンを 5 秒の期限で読んで比べ、結果を実行ファイルの実パスごとにプロセスの中で再利用する。起動する箇所は `adapters/claude.py`・`adapters/agents.py`・`adapters/github.py`・`adapters/cli.py`（`bin/ndf-claude` が `exec claude` の前に打つ `launch-args`）・`domains/project/measure_ci.py`・`domains/relay/wrapper.py`・`domains/relay/cli.py`・`domains/relay/account_login.py`（ログイン・認証状態の確かめ・ログアウト）・`domains/relay/cli_account.py`（登録の解除）の 9 ファイルにある。古ければ、その箇所の今の「起動できない」の形で返し、理由に CLI の名前・見つけたバージョン・下限を載せる。形は `AgentState.REFUSED`・`Failure.REFUSED`・`GitHubError`・`GhUnavailable`・`LoginError("precondition")`・結果 JSON の `status: stopped` で、コマンドは 0 以外で終わる。新しい停止の形と終了コードは足さない。Kiro と agy は下限を持たない。毎日の `runtime-plugin-smoke-latest.yml` が既に入れている最新の CLI で、`scripts/plugin-deps.py cli-help` が `--help`（トップレベルとサブコマンド）を見本と照らし、見本の引数が無ければ失敗で終わる
- 採らなかった案: SessionStart の hook ですべてのコマンドを止める（CLI を使わないコマンドまで止まる。#3691 の起票者の推定 S5）
- 採らなかった案: 利用者の設定（`.ndf/`）に下限を置く（プロジェクトごとに違う値ではない）
- 採らなかった案: 起動のたびに `--help` を照らす（起動が遅くなり、見本の食い違いは利用者には直せない）
- 採らなかった案: 週 1 回の `cli-help` のジョブを新設して CLI を入れる（前の版のこの設計。既存の最新の CLI のジョブと同じ取得と準備を重ねる）
- 採らなかった案: CLI の名前だけで結果を再利用する（起動の `argv`・環境・作業ディレクトリで別の実行ファイルが選ばれると、別のバイナリの判定を使う）
- **代償**: 外部 CLI を起動する箇所が増えたときに `require` を呼び忘れうる。起動の箇所を探すテストで縛る（詳細設計）
- 作り方: 自前で作る
- 比べた候補: LIB22 / LIB4
- 根拠: Value 4 / Value 13（MVV バージョン 9）

### 決定 12: `UV_VERSION` とインストーラの sha256 を食い違わせないため、Renovate の更新の後に検査のスクリプトで sha256 を書き直す

- **質問**: `UV_VERSION` を Renovate が上げるとき、隣のインストーラの sha256 をどう合わせるか
- **選んだ案**: `UV_VERSION` の 2 か所（`plugins/ndf/bin/ndf` と `scripts/lib/ndf_wrappers.py`。どちらも自分の `UV_VERSION` と `UV_INSTALLER_SHA256` でインストーラを検証する）を 1 つのまとまりで同じ PR に上げ、ブランチごとに 1 回だけの更新の後の処理（`postUpgradeTasks` の `executionMode: "branch"`）で `python3 scripts/plugin-deps.py uv-pin` を打つ。新しいバージョンのインストーラを両方のファイルが使う取得元 `https://astral.sh/uv/<版>/install.sh` から 1 回だけ取得して sha256 を計算し、2 つのファイルの `UV_INSTALLER_SHA256` を同じ値に書き直す。取得元と計算した sha256 は結果 JSON に出し、PR の Renovate の記録から照らせる。2 つのファイルの版とハッシュの組が同じことを設定の検査のテストが縛る。許すコマンドは全体の設定にだけ書ける `allowedCommands`（旧名 `allowedPostUpgradeCommands`）に置き、Renovate を動かすワークフローが渡す（LIB13）
- 採らなかった案: sha256 の検証を外す（インストーラの差し替えを見つけられなくなる）
- 採らなかった案: 人が sha256 を書き直す（Renovate の PR が赤のまま届き、手の作業が週ごとに残る）
- **代償**: Renovate の実行の中で外部のインストーラを取得する。取得の失敗は `renovate/artifacts` の赤として出る
- 作り方: 自前で作る・ライブラリやサービスを使う
- 比べた候補: LIB13 / LIB1
- 根拠: Value 3 / Value 13（MVV バージョン 9）

### 決定 13: タグの付け替えで Action の中身が黙って変わらないよう、Actions を SHA で固定し、Renovate に渡さず Dependabot に 7 日待たせて上げさせる

- **質問**: Actions のバージョンを、どの形で参照し、どの仕組みで上げるか
- **選んだ案**: `.github/workflows/` の外部の Action の参照 72 か所を、公開から 7 日たった具体的なリリース（今のメジャーの中の最新）のタグが指す 40 桁の SHA と、後ろのそのバージョンの注記（`# v7.0.3` の形）にする。動くメジャーのタグを実装の時点の指す先へ解くと 7 日に満たないコミットを採りうるためである。SHA と注記の対応は、`uses:` を変えた PR で `action-pins` のステップがタグの指すコミットと比べて縛る。Dependabot の Actions の項目に `cooldown` の `default-days: 7` を足す（github-actions のエコシステムは `default-days` を受ける。LIB16）。Dependabot が SHA の参照の注記も書き換えるかは確かめられず、「未確認のまま残ること」に置いた。設定の検査のテストがタグの参照を落とす（I10）
- 採らなかった案: Renovate の `github-actions` の仕組みで上げる（ワークフローを書き換えるには App に Workflows の権限が要り、Renovate の書き込める範囲が広がる）
- 採らなかった案: タグのままにする（#3691 の受け入れ条件 4 を満たさない）
- **代償**: 依存の更新の仕組みが Renovate と Dependabot の 2 つに分かれる。分担は Dependabot の設定の頭の注記と Renovate の設定の `description` に書く
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB3 / LIB16 / LIB1
- 根拠: Value 3 / Value 9（MVV バージョン 9）
