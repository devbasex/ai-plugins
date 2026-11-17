# #3615 の決定の記録

設計文書の本体は [issue-3615-design.md](issue-3615-design.md) にある。

## 決定の記録

### 決定 1: 今のリリースの形 9 種の扱いを 1 か所で引けるよう、設定の値を 6 つにしてリリースの形の表に扱いを持たせる

今の NDF の release Skill は、リリースの形を 9 種持つ（`plugins/ndf/skills/release/SKILL.md` の「リリースの形」）。ndf-next では、これを次のように扱う。

| リリースの形 | 設定の `release.form` | 扱い | 理由 |
| --- | --- | --- | --- |
| パッケージ・プラグイン | `package-plugin` | アダプターがある | 保存先のアダプター `plugin`（Claude Code のプラグイン）・`json`（npm の `package.json`）・`tag`（PyPI のようにバージョンをタグから作るもの）で受ける。`pyproject.toml` を書く保存先は 11.x（TOML のアダプターが要り、`tag` で代えられる） |
| サービス | `service` | 設定で受ける | バージョンの保存先は `json`・`tag`・`none` のどれか。環境への反映は `deliveries` の行が指す利用者の CD が持ち、`release-steps` は承認資料・リリースブランチ・タグまでを持つ |
| デスクトップアプリ | `desktop` | 11.x へ回す | 署名と公証の確認、自動更新の配信の確かめを `release-steps` が持たず、届いたことを確かめる手段が決まっていない。代表のリポジトリの種類に含まれない |
| モバイルアプリ | `mobile` | アダプターがある | Flutter は保存先のアダプター `pubspec`、Swift は `tag`（`MARKETING_VERSION` は利用者の CD がタグから書く）。ストアの審査と提出は `deliveries` の手動の行として承認資料に並ぶ。Gradle を書く保存先は 11.x |
| 手順・設定 | `procedure` | 設定で受ける | バージョンを持たない（`none`）か `tag` で受け、配る手順は `deliveries` の手動の行が持つ |
| スライド | `document` | 設定で受ける | 生成と提出は `documentation` モードと置き先のシステム（`document-systems`）が持ち、文書ごとに形が違う。リポジトリの形は「業務文書」の 1 つで足りる |
| 文書 | `document` | 設定で受ける | 同上 |
| 表計算 | `document` | 設定で受ける | 同上 |
| ページ | `document` | 設定で受ける | 同上 |

設定の値は 6 つ（`package-plugin`・`service`・`desktop`・`mobile`・`procedure`・`document`）である。リリースの形の表（`plugins/ndf-next/contracts/domains/release-forms.json`）は、値ごとに 1 行を持つ。列は `id`・`label`・`reach`（届き方。承認資料に出す 1 行）・`supported`（真偽）・`why`（11.x へ回す理由）である。`desktop` を値として受けて表で止めるのは、スキーマで落とすと「形の名前と扱いの表の在りか」を理由に出せないためである（受け入れ条件 13）。文書の 4 形を 1 つの値にするのは、形を分けても `release-steps` の振る舞いが変わらず、値を増やしてもパースする処理が無いためである。

形ごとの処理をコードのアダプターにする案は採らない。今の 5 つの違い（起点・公開・完了の確かめ・取得・取り消し）のうち `release-steps` が持つのは承認資料の届き方の 1 行だけで、残りはバージョンの保存先と `deliveries` が持つ。

根拠: Value 4 / Value 11 / P2（MVV 版 8）

### 決定 2: バージョンの読み書きをプラグインに決め打たないため、#3572 の決定 22 のインタフェースで保存先のアダプターを main に置き、`pubspec` と `tag` を足す

#3572 の保存先のアダプター（決定 22）は、マージしていないブランチ `sprint/lrel-lock` にだけある。main には無い（`git ls-tree origin/main plugins/ndf-next/src/ndf/domains/release/` に `version_store.py` が無い。2026-11-17）。並列の計画 p8 では #3572 の残り（レーン lr-rel-rest）がこの課題の後に流れる。そのため、インタフェースは決定 22 の 4 つのメソッド（`units`・`read`・`write`・`paths`）のまま、この課題が main に置く。

保存先の設定は、前提 2 に従い `.ndf/project.json` の `branch_strategy.release.version_store` から読む。決定 22 は同じキーを `.ndf/release.json` に置くとしており、前提 2 と前提 3 はこの点で両立しない。main の設定のスキーマは #3681 で既に `branch_strategy.release.version_store` を持つ。そのため、この設計は前提 2 の側を採る。ブランチの実装との突き合わせの結果（設計文書の「未確認のまま残ること」）は、lr-rel-rest が合わせる。この読み方は設計文書の「人へ残す判断」の 4 に載せた。

| 種類 | バージョンの単位 | 読む | 書く | 書き換えてよいパス |
| --- | --- | --- | --- | --- |
| `plugin` | マーケットプレイスの登録の各プラグイン | ref のプラグインの定義の `version` | 今の `plugins.bump`（定義・登録の説明・`files`） | 登録・定義・`files` |
| `json` | リポジトリ全体の 1 つ | ref の `path` の `pointer` の値 | 同じ位置。`files` も今の規則で書く | `path`・`files` |
| `pubspec` | リポジトリ全体の 1 つ | ref の `path` の最上位の `version:` の行の `+` の前 | 決定 3 | `path` |
| `tag` | リポジトリ全体の 1 つ | ref から辿れるタグのうち、タグの書式に合う最も大きいバージョン | 書かない（空） | 無し |
| `none` | リポジトリ全体の 1 つ | 無し（None） | 書かない（空） | 無し |

リポジトリ全体の単位の名前は、設定の `release.plugin` があればその値、無ければリポジトリの名前（`owner/name` の `name`）である。`load(root, strategy, repo_name)` が設定から 1 つを選び、表に無い種類は止まる。材料の収集・組み立て・タグはインタフェースだけを呼び、`plugins` を直接呼ばない。

`publish` が出したバージョンを知る方法は、保存先がファイルを持つか（`paths()` が空でないか）で 2 つに分かれる。ファイルを持つ保存先は、コミットとその第 1 親で `read` の値が変わった単位を出したとする（今の振る舞い）。ファイルを持たない保存先（`tag`）は、そのコミットに結び付いたリリースの PR の head のブランチの名前をブランチの書式で読む。結び付いた PR が無ければ `not_applicable` を返す（`trunk` の `production_pr` でバージョンを上げる時点と主体は #3572 の決定 7）。

採らなかった案: インタフェースが入るまで待ち、この課題を lr-rel-rest の後へ回す案は、計画 p8 の順序（lrel-form2 → lr-rel-rest）と逆になる。lr-rel-rest が確かめる「#3615 の入った ndf-next」が作れない。保存先ごとに材料の収集を分ける案は、保存先を足すたびに工程の部品へ分岐が増える（要求の非機能の条件）。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: PRD1 / PRD2 / LIB1

根拠: Value 4 / Value 9 / Value 11（MVV 版 8）

### 決定 3: Flutter のストアの規則を壊さないため、保存先 `pubspec` は `version:` の 1 行だけを書き換え、数のビルド番号を 1 つ上げる

`pubspec.yaml` の `version: 1.2.3+45` の `+45` はビルド番号で、ストアは同じバージョンでもビルド番号の増加を求める。release-please の Dart の書き換え（PRD2）と同じく、最上位の `^version: ` の行だけを置き換え、ビルド番号が数なら 1 つ上げ、数でなければそのまま残す。行の置き換えにするのは、ファイルのほかの行（コメント・並び）を 1 文字も変えないためである。

採らなかった案: ruamel.yaml（LIB1）で読み書きする案は、コメントを保つが引用符と空白の形が変わることがあり、1 行の置き換えに見合わない。ビルド番号を捨てる案は、ストアへの提出で前のビルドと衝突する。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: PRD2 / LIB1

根拠: Value 4 / Value 12（MVV 版 8）

### 決定 4: 名前を設定で変えられるよう、`release.tag` の隣に `branch` と `changelog_heading` を置き、置き換えの語を `{plugin}`・`{version}`・`{date}` にそろえる

要求の未決 3 の回答である。置き換えの語は今の `release.tag` の `{plugin}`・`{version}` に合わせ、CHANGELOG の見出しだけ `{date}`（`YYYY-MM-DD`）を足す。キーが無いときの既定は今の形（`release/v{version}`・`## [{plugin} {version}] - {date}`）である。`tag` は今のスキーマで必須のキーで、`null` はタグを作らないことを表すため、既定を持たせない（ai-plugins は `{plugin}--v{version}` を書いている）。

そのため、受け入れ条件 7 の「名前の書式の設定が無いとき、タグは今の形」のうちタグの部分は、「設定が無い」場合が起きない。タグの部分は、ai-plugins の設定の値 `{plugin}--v{version}` で今の形 `<プラグイン>--v<バージョン>` になることで満たすと読む。リリースブランチと CHANGELOG の見出しは、キーが無いとき既定で今の形になる。この読み方は設計文書の「人へ残す判断」の 5 に載せた。

名前を読み戻すときは、書式の置き換えの語の外を正規表現の文字としてエスケープする。語は `{version}` を SemVer の形、`{plugin}` をその単位の名前、`{date}` を日付の形に置き換え、全体一致で読む（I3）。CHANGELOG のセクションは、見出しの書式から `{date}` 以降を除いた先頭で探す（今の `changelog_key` と同じ探し方）。リリースブランチを見分ける箇所（`included_decide`・`assemble.open_releases`・`land`・`record`）は、接頭辞 `release/v` の代わりにブランチの書式で読む。

採らなかった案: 書式を `prefix`・`separator` の組で持つ案は、Keep a Changelog の `## [1.2.3] - <日付>`（STD2）のように前後の両方に文字がある形を書けない。semantic-release の `${version}` の語（`tagFormat`）にそろえる案は、今の設定の `{version}` と食い違う。この語は確かめられなかった（外部の調査の「確かめられなかったこと」）。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: STD1 / STD2 / PRD1

根拠: Value 4 / Value 11（MVV 版 8）

### 決定 5: テストだけの変更でバージョンを上げないため、リリース対象外のファイルの既定をテストのファイルの表から作り、`conftest.py` を役割 `support` の行として表に足す

既定は `plugins/ndf-next/contracts/domains/test-files.json` の行から作る。保存先の行はバージョンの単位の根からの相対パスのディレクトリの 1 階層の名前、名前の行はファイル名の fnmatch である。この既定へ、設定の `undistributed`（gitignore の形）を**足す**。要求の受け入れ条件 8 の「足せる」に合わせ、置き換える形をやめる。

今の既定は `**/conftest.py` を含む。ai-plugins には `plugins/ndf/conftest.py` がある。そのため、表に無いままでは、直した後にこのファイルの変更でバージョンが上がる（受け入れ条件 7・15 と既存のテスト `test_release_decide.py` の #2217 の行に反する）。表に列 `role`（`test` が既定、`support` はテストを支えるファイル）を足し、行 `python-conftest`（名前 `conftest.py`・`support`）を置く。リリース対象外の既定は両方の役割の行を使い、cross-refactoring のテストの判定（`is_test_file`）は `test` の行だけを使う。`conftest.py` を `test` の行にすると、cross-refactoring が変わった `conftest.py` そのものをテストとして流し、集めるテストが 0 件になる。

テストのファイルの表の持ち主は #1142 の子 D2（表の `owner`）で、cross-review・cross-refactoring・試走の候補・リリースが読む共有カーネルである。列と行の追加は表の `columns` に型を書く形で行い、読む側の cross-refactoring を同じ PR で合わせる（設計文書の「ドメインモデル」のコンテキスト）。

採らなかった案: リリースの部品に `conftest.py` だけの既定を別に持つ案は、テストのファイルの見分け方が 2 か所になる（Value 11）。

- 作り方: 自前で作る
- 比べた候補: LIB3

根拠: Value 4 / Value 11（MVV 版 8）

### 決定 6: base と production が同じ設定で範囲が空にならないよう、前のリリースの起点をバージョンの単位ごとに決め、終点をリリース方式から決める

起点は単位ごとに次の順で決める（10 系の `release_prev.prev_release` と同じ順）。

1. タグの書式があれば、本番ブランチから辿れるその単位のタグのうち、最も大きいバージョンのタグ
2. タグの書式が無いか合うタグが無ければ、本番ブランチの履歴で、今のバージョンがバージョンの保存先に初めて入ったコミット（10 系の `untagged_since`）
3. どちらも無ければ（初めてのリリース・保存先 `none`）、本番ブランチの最初のコミット

終点はリリース方式（`route_decide.route_of`）で決める。

| リリース方式 | 範囲の終点 |
| --- | --- |
| `merge_base_branch` | ベースブランチの HEAD |
| `trunk` の `release_pr` | 本番ブランチの HEAD |
| `release_branch` | 選んだスプリントを集めた先頭（決定 8） |

`production_pr` はバージョンアップの PR を作らない。そのため `materials` は `--pr` の PR を範囲にし、起点を本番ブランチとその PR の分岐点にする。毎回のマージがリリースになるリリース方式では、前のリリースは本番ブランチの HEAD そのものであり、タグからさかのぼると出し済みの変更が承認資料に入る。

単位ごとの変化は、その単位の起点からの範囲で数える。含む PR は、バージョンを上げる単位の範囲の件名から読んだ番号の和にする。起点の順 1〜3 を当てるのは `trunk` の `release_pr` と `release_branch` である。`merge_base_branch` は今のとおり本番ブランチの HEAD を起点にする。ベースブランチを丸ごと出すため、本番ブランチの HEAD より前は出し済みである。

用語は 2 つに分ける。「範囲の起点」は差分の範囲の古い側の端で、上のとおりリリース方式で決まる。「前のリリースの起点」は上の順 1〜3 で決まるコミットで、範囲の起点に使うのは `trunk` の `release_pr` と `release_branch` だけである。

受け入れ条件の読み方を 2 つ決める。どちらも設計文書の「人へ残す判断」に載せた。

- 受け入れ条件 12 が挙げる `web-trunk`・`docs` は、リリース方式 `production_pr`・保存先 `none`・タグ無しで、前のバージョンのタグを持たない。この 2 つは「範囲が `origin/main..origin/main` の空にならない」ことで満たすと読み、要求 E3 の「最初のコミットから」は当てない（「人へ残す判断」の 1）
- 受け入れ条件 7 と受け入れ条件 12 は、ai-plugins（`trunk`・`release_pr`）で食い違う。直す前の範囲は空で、受け入れ条件 12 を満たすと `materials` の範囲が変わる。#1142 の「現行と同等」は既知の不具合を引き継がないため、受け入れ条件 12 を優先する。受け入れ条件 7 の「同じ」は、同じ範囲を与えたときの名前と承認資料の行の形で判定する（I9。「人へ残す判断」の 2）

採らなかった案: 全単位で 1 つの起点（最も古いタグ）にする案は、変化の無い単位まで古い変更でバージョンが上がる。

- 作り方: 自前で作る
- 比べた候補: PRD1

根拠: Value 4 / Value 7 / Value 11（MVV 版 8）

### 決定 7: `.claude-plugin` の無いリポジトリを確かめる設定として、nd-factory の設定のコピーを `release_branch`・保存先 `tag`・CD ありで組む

前提 6 は nd-factory の今の値を挙げる（起点 `qa/dev`・本番 `main`・選んで集める・CD の無い手動の反映・バージョンなし）。この値をブランチ戦略の設定へ移すと、規則 R4（`release_branch` でバージョンの保存先が要る）に落ちる。リリースブランチの名前がバージョンを持つためである。バージョンのファイルを持たないリポジトリが `release_branch` を選ぶ形は保存先 `tag` で、R11 と R10 により CD（置いたテンプレートの workflow）が要る。テストの設定のコピーは次の値にする。nd-factory のリポジトリは書き換えない。

| キー | 値 |
| --- | --- |
| `release_strategy` | `release_branch` |
| `branches` | `{production: main, base: qa/dev}` |
| `form` | `service` |
| `version_store` | `{kind: tag}` |
| `tag` | `v{version}` |
| `github_release` | `false` |
| `cd` | `{workflow: ndf-release.yml}` |
| `deliveries` | 手動の 1 行 |

このため、受け入れ条件 3 のテストの設定は前提 6 の値そのものではない。前提 6 の値（`release_branch`・バージョンを持たない・CD の無い手動の反映）のリポジトリは、この設計の後も ndf-next のリリースを使えない。R4 は #3681 が入れた規則で、この設計が扱える範囲を狭めるものではないが、nd-factory の今の運用を扱うかは設計文書の「人へ残す判断」の 3 に載せた。

採らなかった案: 保存先 `none` のまま R4 を緩める案は、リリースブランチの名前のバージョンを決められない。

根拠: Value 4 / Value 13（MVV 版 8）

### 決定 8: `release_branch` で選んだスプリントだけを出すため、`materials`・`assemble` は `--units` を受け、10 系の組み立てと含む PR の読み方を移す

`release_branch` の終点は、本番ブランチの HEAD から `--units` のスプリントブランチを順に `--no-ff` でマージした先頭である。組み方は 10 系の `strategy_route._build_release_branch` と同じで、件名は `Merge <スプリント> into <リリースブランチ>`、衝突すれば組まずに止まる。`materials` はブランチを残さないよう一時の作業場所（`git worktree add --detach`）で組み、終われば外す。`assemble` は同じ先頭からリリースブランチを作る。含む PR は、範囲の first-parent の PR のマージを数える。加えて、件名がリリースの単位のマージに当たるマージについて、第 1 親..第 2 親の PR を数える（10 系の `included.unit_merges`）。`--units` が無ければ原因 `precondition` で止まる。

採らなかった案: 利用者が組んだリリースブランチを渡させる案は、承認資料と組み立てで先頭が食い違いうる。

- 作り方: 自前で作る
- 比べた候補: PRD1

根拠: Value 3 / Value 4（MVV 版 8）

### 決定 9: リモート名を 1 か所で決めるため、`cli` がメインディレクトリの設定から求め、組み立ての指定（`Target`）で渡す

`cli` が `git.remote_of(<メインディレクトリ>)` を 1 回呼び、`Target.remote` に入れる。材料の収集・組み立ては引数のリモート名だけを使い、`adapters/git.py` の `__getattr__` の `ORIGIN` を消す（#3617 の決定 5 の引き継ぎ）。メインディレクトリの求め方は、今の `_strategy` と同じく `common_dir().parent` で、git の外なら `--root` である。受け入れ条件 11 の検索は語の単位（`rg -nw "ORIGIN"`）で打つ。語の一部に含む識別子（`ORIGIN_KEY`・`PRIVATE_ORIGIN`・`_ORIGINAL_ENV`）はリモート名と関係が無い。`rg -n "ORIGIN" plugins/ndf-next/src` の 18 件のうち、語の単位で当たるのは `git.py` の 2 件とリリースの部品の 5 件である（2026-11-17）。

根拠: Value 4 / Value 11（MVV 版 8）

### 決定 10: 利用者のリポジトリから起動できるよう、ai-plugins に再利用可能なワークフローを置き、利用者は短い呼び出し側のテンプレートを置く

ai-plugins の `.github/workflows/ndf-release-reusable.yml` は `on: workflow_call` で入力を受ける。入力は `step`（`assemble`・`merge`・`publish`）・`level`・`pr`・`units`・`revision`・`ndf_ref` である。`ndf_ref` の ai-plugins を取り出して `plugins/ndf-next/bin/ndf release-steps` を打つ。利用者は `plugins/ndf-next/skills/release/assets/workflows/ndf-release.yml` を自分の `.github/workflows/` へコピーする。`uses:` の参照と `ndf_ref` には、同じ NDF のタグを書く。ジョブの中身をコピーしないため、利用者は NDF のバージョンを上げるときに参照を書き換えるだけでよい（STD3）。

要求の未決 1 の回答: 書く操作（`assemble`・`merge`・`publish`）は workflow だけが持つ。会話のセッションは承認資料の提示と回答（`materials`・`present`・`answer`）を持つ（#3395 の決定 128 の分け方）。手順の文書（`plugins/ndf-next/skills/release/references/release-workflow.md`）は両方の並びを書く。ほかの CI は、同じサブコマンドの並びを利用者が呼ぶ形で文書に書き、テンプレートを作らない。

要求の未決 2 の回答: 秘密は `release_token` の 1 つで受け、呼び出し側が自分の秘密の名前を割り当てる。渡されなければ、`assemble`・`publish` は `github.token` で動く。このとき、`github.token` が作った PR では CI が起動しない（STD4 と ai-plugins の workflow の注記）ことを `::warning::` で出す。`merge` は `release_token` が無ければ本番ブランチへ入れる前に止まる。`github.token` の書いたリリース記録は読み手に信用されないためで（今の ai-plugins の workflow と同じ）、黙って倒すと承認の記録が後で読めなくなる。

ai-plugins の `.github/workflows/ndf-next-release.yml` は今のまま残す。ai-plugins はマージ前の ndf-next を同じコミットから動かし、利用者はタグで固定して動かすため、起動の形が違う。11.0.0 の入れ替え（#3304）で ai-plugins も呼び出し側へ移すかは入れ替えの設計が決める。

採らなかった案: ジョブの全体をテンプレートとしてコピーさせる案は、NDF のバージョンを上げるたびに利用者のファイルが古くなる（Value 11）。秘密が無ければすべてのジョブで止まる案は、`assemble` まで試せず導入の手間が増える。

- 作り方: ライブラリやサービスを使う・標準に倣う
- 比べた候補: STD3 / STD4

根拠: Value 1 / Value 4 / Value 11（MVV 版 8）

### 決定 11: 新しい保存先の種類でバージョンの衝突の確かめが壊れないよう、`land` と 10 系のバージョンの候補を JSON の位置を持たない種類で空にする

ndf-next の `land.version_rows` と 10 系の `release_lib/land_read.py` は、種類が `plugin` でなければ `path` と `pointer` を読む。`tag` は `path` を持たず、`pubspec` は `pointer` を持たないため、そのままでは読み出しで落ちる。2 つとも、`plugin`・`json` 以外の種類では候補を空にする（`none` と同じくバージョンのファイルを比べない）。`land` を保存先のアダプターへ移すのは #3572 の決定 22 の範囲で、lr-rel-rest が行う。スキーマの 10 系のコピー 2 つは、コントラクトの一致の検査（`scripts/check-ndf-contracts.py` の `COPY_DEFS`）が求めるため同じ PR でコピーする。10 系は `branch`・`changelog_heading` を読まず、今の形のまま動く。

根拠: Value 3 / Value 7（MVV 版 8）

### 決定 12: タグが別のコミットを指したまま進まないよう、`publish` は同じ名前のタグの指す先を比べ、違えば止まる

今の `publish` は、同じ名前のタグがあれば指す先を見ずに `exists` を返す。保存先 `tag` ではタグがバージョンそのものであり、別のコミットを指すタグを `exists` で通すと、出したと記録したコミットとタグの中身が食い違う（要求の E8）。同じコミットなら `exists`、違えば原因 `refused` で止まり、タグ・今の指す先・出したコミットを出す。

根拠: Value 7 / Value 13（MVV 版 8）

### 決定 13: 子の表と実装の順序をそろえるため、子 `B2`（#3615）を順番 9・レーン `lrel-form2` で足し、S2 の先に要る子に入れる

`children.json` に次の行を足し、S2 の `needs` に `B2` を足す。

```json
"B2": {"issue": 3615, "packages": [], "needs": ["B1", "M2"], "order": 9, "lane": "lrel-form2", "closed": false}
```

パッケージ `ndf.domains.release` のオーナーは M2 のままで、この子は新しいパッケージを持たない。#1142 の本文の子の表にも同じ行を足す（X4・G1 を足した設計と同じく、この設計の決定を子の表の正本とする）。

根拠: Value 14（MVV 版 8）

### 決定 14: 設定の置き場を 1 か所にする前提 2 と既存のキーを両立させるため、`.ndf/release.json` の既存のキーはそのまま読み、新しいキーは `branch_strategy.release` に置く

前提 2 は、リリースの形とリリースの形の設定を `branch_strategy.release` に置き、新しいキーもそこに足すと定める。この設計が足すキー（`branch`・`changelog_heading`・`version_store` の種類 `pubspec`・`tag`）はすべて `branch_strategy.release` に置く。

`.ndf/release.json` の `steps`・`undistributed`・`bump_undistributed`・`changelog` は、main で既にあるキーである。根拠は `plugins/ndf-next/contracts/domains/ndf-files.json` の行 `release` の `keys`（2026-11-17）。この設計はキーを足さず、`undistributed` の意味だけを「既定を置き換える」から「既定へ足す」に変える（決定 5）。OpenAPI の `info.version` の書き換えは既存の `steps` の 1 行で受け、CHANGELOG のパスは既存の `changelog` で受ける。これらはリリースの形ではなくリリースの段階で流す手順とファイルの置き場であり、前提 2 の「新しいキー」に当たらないとみなす。

`.ndf/release.json` のキーを `branch_strategy.release` へ移すかは、#3572 の決定 22 との突き合わせと合わせて決める。決定 22 は同じファイルの `version_store` を扱う（設計文書の「人へ残す判断」の 4）。

採らなかった案: この課題で 4 つのキーを `branch_strategy.release` へ移す案は、#3572 のブランチの `bump`・`pr-check` が読む `.ndf/release.json` との食い違いを増やす。lr-rel-rest の突き合わせの量も増える。

根拠: Value 4 / Value 8 / Value 11（MVV 版 8）
