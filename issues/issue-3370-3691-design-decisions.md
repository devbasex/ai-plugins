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
- **選んだ案**: `renovatebot/github-action` を週 1 回の定時と手動で動かし、GitHub App から実行ごとに短い期限の token を作る（`actions/create-github-app-token`）。Renovate のバージョンは 1 行のファイル `.github/renovate-version` に置き、ワークフローがそれを読んで `renovate-version` に渡す。Renovate は正規表現の仕組み（決定 6 の `customManagers`）でこのファイルを拾い、ほかの依存と同じく 7 日待って上げる。ファイルはワークフローの外にあるので、App に Workflows の権限が要らない
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
- **選んだ案**: `dependencyDashboardApproval` で、依存の一覧の Issue にチェックを入れるまで PR を作らない。uv のプロジェクトと npm の lock のディレクトリごとに 1 つのまとまり（`groupName`）を置き、1 回のチェックで lock ごとに 1 本の PR にする。lock の作り直し（決定 6）も同じまとまりに入れる。前の設計の「`plugins/ndf` の更新を 1 本」を、対象の lock の数（5 つ）へ広げた
- 採らなかった案: すべての lock を 1 本の PR にまとめる（1 つの lock が解決できないと全部が止まり、差分が大きく確かめるテストの一覧も長くなる）
- 採らなかった案: 依存ごとに PR を作る既定の形（子の PR が動いている間に PR が届き続け、`uv.lock` の衝突が増える）
- **代償**: 同じパッケージ（例 ruamel-yaml は `plugins/ndf` と `plugins/mcp/mcp-serena` の両方）を別々の PR で上げることがある。そろえる規則は詳細設計の「同じパッケージをそろえる規則」に置く
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1
- 根拠: Value 1 / Value 5（MVV バージョン 9）

### 決定 4: 7 日の守りを 1 か所の取りこぼしで破らないため、Renovate・Dependabot・uv の 3 か所に同じ 7 日を置き、値の一致をテストで縛る

- **質問**: 7 日待つ規則を、どの仕組みのどこに置くか
- **選んだ案**: 7 日を 3 つの仕組みに置く。Renovate の `minimumReleaseAge: "7 days"`、Dependabot の `cooldown` の `default-days: 7`、対象の 4 つの uv のプロジェクトの `[tool.uv] exclude-newer = "7 days"` である。正は `plugins/ndf/pyproject.toml` の値で、ほかがそれに合わせることを設定の検査のテストが縛る（I6）。lock を作り直すのは uv なので、Renovate が外しそこねても uv が拒む（M2）。決定 106 の「最新の安定版が 365 日より古ければプレリリース」は、前の設計どおりの形で近づける。`matchCurrentAge: "> 365 days"` の依存だけ `ignoreUnstable` を外す。Renovate は定義にプレリリースのバージョンを `==` で書くので、uv はその指定を見てプレリリースを解決に入れる
- 採らなかった案: Renovate だけに置く（Renovate は uv の設定を読まず、lock の作り直しと手の `uv lock` で推移的な依存に 7 日が効かない）
- 採らなかった案: uv だけに置く（Renovate が 7 日に満たないバージョンの PR を作り、解決の失敗が PR ごとに出る）
- **代償**: 同じ値を 6 か所に持つ。一致のテストで縛る
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB5 / LIB3
- 根拠: Value 3 / Value 11（MVV バージョン 9）

### 決定 5: 固定を解いてよい時機を機械で知るため、固定する理由を Renovate の規則の中で種類・相手・制約・見直す日の欄に分けて書く

- **質問**: 固定する理由（httpx の固定・DBOS を対象から外すこと）を、どこに、どの形で持つか
- **選んだ案**: Renovate の依存ごとの規則（`packageRules`）の `description` に、`種類:`・`相手:`・`制約:`・`見直す日:`・`理由:` の 5 行を決まった順で書く（行の形は詳細設計）。種類は `相手の制約`・`自分の非互換`・`対象の外` の 3 つである。週 1 回のサプライチェーンの検査がこの欄を読み、相手の最新の上げる先が `制約:` と違う制約を持つか、`見直す日:` を過ぎた固定を、脆弱性の知らせの「解いてよい固定」に出す（決定 9）。前の設計の決定 5（理由は自由文）を置き換える
- 採らなかった案: 自由文の理由（前の設計の形。相手が制約を外しても機械では分からない）
- 採らなかった案: `pyproject.toml` の `[tool.ndf.deps.hold]` に別の表を持つ（#3347 の決定 106 の形。Renovate が読む規則と同じ事実を 2 か所に持つ）
- **代償**: Renovate の `description` は人が読む説明の欄で、決まった行の形はこのリポジトリだけの約束である。形はテストが縛る（I3）
- 作り方: 自前で作る・ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB11
- 根拠: Value 8 / Value 11 / Value 14（MVV バージョン 9）

### 決定 6: 漏れなく上げるため、対象を追跡しているすべての lock（`docs/presentations/` を除く）と推移的な依存と `UV_VERSION` へ広げる

- **質問**: Renovate の対象をどこまで広げ、推移的な依存とコードの中の固定をどう上げるか
- **選んだ案**: 対象は追跡している lock（`git ls-files` の `uv.lock` と `package-lock.json`）から Renovate の `ignorePaths` に書いた `docs/presentations/**` を除いたものすべてにする（I9）。今は 4 つの uv のプロジェクトとスライドのテンプレートの npm の lock の 5 つである。推移的な依存は、Renovate の lock の作り直し（`lockFileMaintenance`。uv では `uv lock --upgrade` を打つ）を uv のプロジェクトだけで有効にし、承認を求めて週 1 回の候補に出す（LIB12）。uv の lock の作り直しには Renovate の待つ日数を置かない（`minimumReleaseAge: null`）。作り直しで上がるパッケージには Renovate が見る日時が無く、置くと待ちのチェックが残り続けるためで、7 日は uv の `exclude-newer` が守る（決定 4）。npm の lock は作り直しを有効にせず、推移的な依存は直接の依存の更新（7 日を待つ）と一緒にだけ動く。npm の側に uv の `exclude-newer` に当たる守りを置かないためである。`UV_VERSION` の 2 か所と `.github/renovate-version` は正規表現の仕組み（`customManagers`）で GitHub の releases（`astral-sh/uv`・`renovatebot/renovate`）から拾う（LIB13）。前の設計の決定 6（`plugins/ndf` だけに絞る）を置き換える
- 採らなかった案: `plugins/ndf` だけに絞る（前の決定 6。根の lock の 47 本の遅れと playwright-kit の脆弱性が残る）
- 採らなかった案: 推移的な依存を 1 本ずつ定義に書いて上げる（推移的な依存を直接の依存に変え、定義が膨らむ）
- 採らなかった案: `docs/presentations/` の lock も対象にする（発表日時点の記録で、以後の変更に追随しない）
- **代償**: 依存の一覧の Issue の行が増える。lock の作り直しの PR は差分が大きく、確かめるテストの一覧（決定 10）が要る。npm の lock の推移的な依存は、直接の依存が動くまで遅れたまま残る（脆弱性は決定 9 が知らせる）
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB12 / LIB13
- 根拠: Value 6 / Value 13（MVV バージョン 9）

### 決定 7: 7 日の規則を最初のバージョンアップにも当てるため、実装の PR は上げる先を実装の時点で uv に決めさせ、DBOS を対象の外に置く

- **質問**: 最初のバージョンアップの上げる先をどう決め、DBOS をどう扱うか
- **選んだ案**: 実装の PR は、`exclude-newer = "7 days"` を置いた後に、DBOS と固定した依存を除いて `uv lock --upgrade` を打ち、その時点の上げる先へ上げる。バージョンが変わった依存の一覧を PR 本文に書く。DBOS は規則に `enabled: false` と欄つきの理由（種類 `対象の外`）を書き、C1+ の続きが上げる。最初の 1 回を Renovate の PR にしないのは、Renovate の導入（App と秘密）がリリース承認ゲートの後になるためである
- 採らなかった案: #3347 の「14 本の最新のバージョン」の表をそのまま写す（公開から 7 日に満たないバージョンを含み、M2 で uv が拒んだ）
- 採らなかった案: DBOS も対象に含める（1 回のチェックで DBOS も上がり、C1+ の続きの持ち分を越える）
- **代償**: 上げる先が実装の時点まで決まらず、落ちるテストの数も実装の PR で分かる
- 根拠: Value 13 / Value 3（MVV バージョン 9）

### 決定 8: 解決できない上げ方を失敗として返すため、lock の作り直しの成否を Renovate のチェックで常に出し、赤のときは更新の PR をマージしない

- **質問**: 解決できない上げ方（#3370 の受け入れ条件 9）の失敗をどこで返すか
- **選んだ案**: `statusCheckWhen.artifactError` を `"always"` にし、更新の PR に `renovate/artifacts` を常に出す（LIB8）。赤の PR をマージしないことで、`main` の定義と lock を元のまま残す。必須のチェックには足さない（更新の PR にしか付かず、必須にすると付かない PR が止まる）。受け入れ条件 9 の上げ方は、検証用のブランチで httpx の規則を一時的に `1.0.dev6` を許す形にして 1 度見る
- 採らなかった案: `renovate/artifacts` を `main` の必須のチェックにする（Renovate の PR 以外の PR が止まる）
- **代償**: 赤のチェックを見て止めるのは、マージする人と NDF のマージの手順である
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB1 / LIB8
- 根拠: Value 13 / Value 3（MVV バージョン 9）

### 決定 9: 承認と 7 日を飛ばさずに脆弱性を知るため、脆弱性は週 1 回の `uv audit` と `npm audit` で 1 件の Issue に知らせ、Renovate の脆弱性の PR は有効にしない

- **質問**: 対象の lock の既知の脆弱性を、どの仕組みで見つけ、どこへ知らせ、7 日に満たない直しをどう入れるか
- **選んだ案**: `deps-weekly.yml` が週 1 回（と手動）、脆弱性を取得する。uv のプロジェクトでは `uv audit --frozen --output-format json`、npm の lock では `npm audit --package-lock-only --json` を打つ。`scripts/plugin-deps.py report` が結果を 1 件の脆弱性の知らせ（ラベル `supply-chain`）に書き直し、0 件なら閉じる。1 つでも取得が失敗したら Issue に触れずに失敗で終わる（I11）。直しは依存の一覧の Issue のチェックで上げる。直しのバージョンが公開から 7 日に満たないときだけ、その lock の `pyproject.toml` に `exclude-newer-package` の例外を足す。例外の行の注記には勧告の ID と外す日を書き、外す日を過ぎると同じ知らせの「解いてよい固定」に出る
- 採らなかった案: Renovate の `osvVulnerabilityAlerts`・`vulnerabilityAlerts`（どちらの脆弱性の PR も承認・待つ日数・予定を飛ばして作られ、`exclude-newer` があると 7 日に満たない直しで lock の作り直しが失敗する。`osvVulnerabilityAlerts` は実験的な機能で、対象は直接の依存だけである。LIB15）
- 採らなかった案: Dependabot alerts（このリポジトリの依存グラフは `uv.lock` を読まず、マニフェストが 0 件である。M3）
- 採らなかった案: 脆弱性を依存の一覧の Issue に混ぜる（Renovate が本文を書き直すため、ほかの書き手が入れない）
- **代償**: `uv audit` はプレビューで、出力の形が変わると検査が失敗で終わる（I11 の失敗として見える）。例外を足すのは人の手である
- 作り方: 自前で作る・ライブラリやサービスを使う
- 比べた候補: LIB15 / LIB19 / LIB18 / LIB3
- 根拠: Value 13 / Value 6 / Value 1（MVV バージョン 9）

### 決定 10: 上げた後に見るテストが分かるよう、lock を変えた PR に、変わったパッケージを import するコードを通るテストの一覧を出す

- **質問**: 「変わった依存の公開 API を使う箇所のテスト」を、どう機械で選ぶか
- **選んだ案**: `deps-weekly.yml` の PR のジョブ（lock のパスが変わった PR だけで走る）が `scripts/plugin-deps.py affected` を打つ。lock の差分から、変わったパッケージの名前・前と後のバージョンを出す。パッケージの import 名を、その lock のプロジェクトのソースの `import` 文（構文木で読む）と照らす。import するモジュールと、そのモジュールを import するテストを並べ、PR に 1 件のコメントで出す（目印の文で引いて書き直す）。推移的な依存は、それを求める直接の依存（`uv tree --invert`）を添える
- 採らなかった案: 公開 API の差分を取る（API の差分を出す道具はパッケージの形ごとに違い、型の無いパッケージでは取れない）
- 採らなかった案: すべてのテストを打つだけにする（今の CI と同じで、どこを見ればよいかが分からない。3380-6）
- **代償**: 文字列での import やプラグインの読み込みは拾えない（未確認のまま残ること）
- 作り方: 自前で作る
- 比べた候補: LIB20 / LIB21 / LIB4
- 根拠: Value 13 / Value 1（MVV バージョン 9）

### 決定 11: 古い CLI での失敗を利用者の環境で初めて知ることが無いよう、外部 CLI の下限を NDF のコントラクトの表に置き、起動の前に比べて止まる

- **質問**: `gh`・`claude`・`codex` の下限をどこに置き、いつ比べ、`--help` をどう照らすか。本体の Claude Code と Codex は `claude` と `codex` の CLI そのものなので、表は 3 行である
- **選んだ案**: 下限の表 `plugins/ndf/contracts/domains/cli-floors.json`（新設）を置く。CLI ごとに実行ファイル・バージョンを出す引数・読み方の正規表現・下限・`--help` の見本を持ち、`claude` の見本は今の `contracts/data/claude-cli.json` を指す。NDF の `cli_floor.require(<名前>)` が、その CLI を起動する箇所の前で、プロセスにつき 1 回バージョンを読んで比べる。起動する箇所は `adapters/claude.py`・`adapters/agents.py`・`adapters/github.py`・`domains/project/measure_ci.py`・`domains/relay/wrapper.py`・`domains/relay/cli.py`・`domains/relay/account_login.py` の 7 ファイルにある。古ければ、その箇所の今の「起動できない」の形で返し、理由に CLI の名前・見つけたバージョン・下限を載せる。形は `AgentState.REFUSED`・`Failure.REFUSED`・`GitHubError`・`GhUnavailable`・`LoginError("precondition")` で、コマンドは 0 以外で終わる。新しい停止の形と終了コードは足さない。週 1 回、`scripts/plugin-deps.py cli-help` が最新の CLI を入れて `--help` を見本と照らし、見本の引数が無ければ失敗で終わる
- 採らなかった案: SessionStart の hook ですべてのコマンドを止める（CLI を使わないコマンドまで止まる。#3691 の起票者の推定 S5）
- 採らなかった案: 利用者の設定（`.ndf/`）に下限を置く（プロジェクトごとに違う値ではない）
- 採らなかった案: 起動のたびに `--help` を照らす（起動が遅くなり、見本の食い違いは利用者には直せない）
- **代償**: 外部 CLI を起動する箇所が増えたときに `require` を呼び忘れうる。起動の箇所を探すテストで縛る（詳細設計）
- 作り方: 自前で作る
- 比べた候補: LIB22 / LIB4
- 根拠: Value 4 / Value 13（MVV バージョン 9）

### 決定 12: `UV_VERSION` とインストーラの sha256 を食い違わせないため、Renovate の更新の後に検査のスクリプトで sha256 を書き直す

- **質問**: `UV_VERSION` を Renovate が上げるとき、隣のインストーラの sha256 をどう合わせるか
- **選んだ案**: Renovate の更新の後の処理（`postUpgradeTasks`）で `python3 scripts/plugin-deps.py uv-pin` を打つ。新しいバージョンのインストーラを取得して sha256 を計算し、`plugins/ndf/bin/ndf` の `UV_INSTALLER_SHA256` を書き直す。許すコマンドは全体の設定にだけ書ける `allowedCommands`（旧名 `allowedPostUpgradeCommands`）に置き、Renovate を動かすワークフローが渡す（LIB13）
- 採らなかった案: sha256 の検証を外す（インストーラの差し替えを見つけられなくなる）
- 採らなかった案: 人が sha256 を書き直す（Renovate の PR が赤のまま届き、手の作業が週ごとに残る）
- **代償**: Renovate の実行の中で外部のインストーラを取得する。取得の失敗は `renovate/artifacts` の赤として出る
- 作り方: 自前で作る・ライブラリやサービスを使う
- 比べた候補: LIB13 / LIB1
- 根拠: Value 3 / Value 13（MVV バージョン 9）

### 決定 13: タグの付け替えで Action の中身が黙って変わらないよう、Actions を SHA で固定し、Renovate に渡さず Dependabot に 7 日待たせて上げさせる

- **質問**: Actions のバージョンを、どの形で参照し、どの仕組みで上げるか
- **選んだ案**: `.github/workflows/` の外部の Action の参照 72 か所を、実装の時点のタグが指す 40 桁の SHA と、後ろのバージョンの注記（`# v7.0.0` の形）にする。Dependabot の Actions の項目に `cooldown` の `default-days: 7` を足す（github-actions のエコシステムは `default-days` を受ける。LIB16）。Dependabot が SHA の参照の注記も書き換えるかは確かめられず、「未確認のまま残ること」に置いた。設定の検査のテストがタグの参照を落とす（I10）
- 採らなかった案: Renovate の `github-actions` の仕組みで上げる（ワークフローを書き換えるには App に Workflows の権限が要り、Renovate の書き込める範囲が広がる）
- 採らなかった案: タグのままにする（#3691 の受け入れ条件 4 を満たさない）
- **代償**: 依存の更新の仕組みが Renovate と Dependabot の 2 つに分かれる。分担は Dependabot の設定の頭の注記と Renovate の設定の `description` に書く
- 作り方: ライブラリやサービスを使う
- 比べた候補: LIB3 / LIB16 / LIB1
- 根拠: Value 3 / Value 9（MVV バージョン 9）
