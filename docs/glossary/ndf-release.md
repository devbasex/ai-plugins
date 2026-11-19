# 用語集: NDF のリリース（`ndf-release`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

release が走らせるリリースの種別・リリースコマンド・公開操作・リリース記録の語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| リリース差分 | — | バージョンとバージョンの間（タグからタグまで）の変更 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース | `release` | 本番へのデプロイ。フェーズの名前でもある。工程の名前は「デプロイ」である。ベースブランチから本番チャネルへ変更を入れることもリリースと呼び、道筋の違いはリリースの経路の値が持つ | 配布、本番の配布、昇格 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースプラン | — | リリースの手順をステップの列として持つプラン | 配布の計画、昇格のプラン | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースコマンド | — | リポジトリが `.ndf/release.json` に定義し、`release` がリリースの段階に合わせて走らせる 1 つのコマンド | 配布のコマンド | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| インストール確認 | — | 隔離した HOME で ref からプラグインをインストールし、バージョンと中身が ref と一致するかを確かめる | 導入確認 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 導入の確認 | — | `.ndf/pace.json` の `<セクション>.verify` に書かれたコマンドを、ベースブランチの先頭で走らせること。pace: fast / auto で、リリースの Pull Request で届くときはリリース承認ゲートの前に、経路 merge だけのときは検査の後に走り、終了コードを承認資料へ載せ、出力は所有者だけが読めるログへ分ける | — | — | — |
| リリース完了の確認 | — | 公開が済んだことを、リリース先の状態から読み取れる値 | 完了の事実 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース記録 | — | release が Pull Request へ残す記録のブロック。段階・バージョン・スプリントの Pull Request に、CD を読んだときは CD の結果を持つ | リリースの記録、配布の記録 | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| リリース種別 | `release_type` | リリースの種類。production（本番リリース）/ verification（開発版のデプロイ） | — | — | `docs/specifications/ndf-release-steps-and-token-usage-snapshot.md` |
| 本番チャネル | — | リリースしたバージョンが常用する利用者へ届くブランチ | 本番のチャネル | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 本番系 | — | 利用者が現に使っているリリースのチャネル・環境・外部サービス。チャネルは本番系の一種 | 本番の系 | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| プラグインルート | — | リリースした先で scripts/ と skills/ が並ぶディレクトリ | — | — | — |
| 公開操作 | — | release の手順 4 で行う操作。レジストリへの公開・配備先への反映・署名したアーティファクトの設置・ストアへの提出 | 公開の操作 | — | `plugins/ndf/skills/release/references/completion-check.md` |
| 開発版 | — | ベースブランチ（develop）に載るチャネルと、そこへ出す接尾辞付きのバージョン。マージされた変更がそのまま載る。手動反映の本番系の形では、production: false の行（検証の環境）へ届くことも開発版に当たる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 本番 | — | 利用者が現に使っているチャネル・環境・外部サービス（本番系）。プラグインのリリースでは本番のブランチ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 正式版 | — | 本番チャネルへ出す、接尾辞の無いバージョン。出したらリリースタグを打つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 自動反映の本番チャネル | `auto_production_branch` | 本番チャネルのうち、マージ（push）で本番系への反映が自動で始まるもの。設定の delivery に kind: auto で本番チャネルを branch に持つ行があるときに当たる。そこへのマージはリリース承認ゲートに当たる | — | — | — |
| 手動反映の本番系 | `manual_production` | リリースの設定の行のうち、production: true と定義され、kind: manual（担い手が手で起動する）のもの。そこへ届ける操作の前にリリース承認ゲートを掛ける。自動反映の本番チャネルと対になる | — | — | — |
| リリースの経路 | `release_route` | 変更が本番系へ届く道筋の種類。template（release.form のテンプレートで組む）/ merge（マージで反映）/ manual（手で反映）/ none（届けない）。release.form があればそれ、無ければ設定の delivery から決まる。リリースの形とは別の軸 | 配布の経路 | — | — |
| CD | — | 継続的デリバリー（Continuous Delivery）。ブランチへのマージを起点に、環境への反映を NDF の工程の外の仕組みが行うこと。delivery の kind: auto で定義する | — | — | — |
| CD の案内 | — | CD が無いリポジトリで、工程がデプロイに入ったときに出す案内。CD の設定と方式の記録を促す。承認ゲートではない | — | — | `docs/specifications/ndf-release-via-cd.md` |
| 手動反映の理由 | — | delivery の手動の行に書く、あえて CD を置かない理由。これがある行には CD の案内を出さない | — | — | `docs/specifications/ndf-release-via-cd.md` |
| リリースの Pull Request | — | 本番チャネルへ変更を入れる Pull Request。リリースのテンプレートでは `release/v<バージョン>` が、経路 merge ではベースブランチが head になる | 昇格の Pull Request | — | `docs/specifications/ndf-release-pr-and-merge-route-terms.md` |
| リリースの承認資料 | — | リリース承認ゲートで利用者か MVV 判定が見る承認資料。承認したコミットの欄を持つ。経路 merge では、この欄の SHA がリリースの Pull Request の HEAD のときだけ本番チャネルへマージする。リリースのテンプレートでは、承認したコミットから比べた HEAD までに版上げなど許可した差分のほかが無いことを確かめ、比べた HEAD をマージする | 昇格の承認資料 | — | `docs/specifications/ndf-release-pr-and-merge-route-terms.md` |
| 載ったバージョン | — | PR が載った正式版。CHANGELOG のバージョンのセクション → マージの時刻の後の最初の正式版のタグ → 行の時刻の後の最初の正式版のタグ、の順で決める。計測でバージョンを寄せる規則 | — | — | — |
| 動いたバージョン | — | 呼び出しや会話を動かした NDF のバージョン（使用量の使用量ログの ndf_version・会話の Skill の保存先のバージョン） | — | — | — |
| 記録の PR | — | リリース記録（## リリース記録）を置いた Pull Request。まとめの close が --record-pr に受け、リリース後テストが記録を書く先。まとめを流し始める時点に解決する（#2230） | — | — | `docs/specifications/ndf-sprint-close-record-pr-at-run-time.md` |
| 記録の保存先が分からない | — | まとめの close が読むリリース記録の Pull Request が分からない状態。sprint-close.py --record-pr unknown で渡し、課題を開いたまま残す。まとめのプランは組まず（#2230）、手で打つエントリポイントと前のバージョンで組んだプランだけが渡す | — | — | `docs/specifications/ndf-sprint-misjudgments-unknown-record-and-manual-none.md` |
| 他のプラグイン | — | リリースの設定の release.plugin 以外で、前のタグからの差分にファイルがあるプラグイン。release-steps.py changed-plugins が列挙する | — | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| バージョン種別 | — | 版数のどの桁を上げるか（MAJOR / MINOR / PATCH）。セマンティックバージョニングに従う | 上げ幅 | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| 移行の手順 | — | 利用者が新しいバージョンへ移るときに自分で行う操作（引数の置き換え・認証のし直し・導入し直しなど）。PR 本文の「移行の手順」のセクションが正 | — | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| 互換なしのフラグ | — | 要求の「影響」の表の「公開インタフェース」の行に書く、互換の経路を持たないことを示す語（互換なし / 互換の経路は持たない）。release-steps.py changed-plugins がバージョン種別の候補を MAJOR にする材料 | — | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| 承認したコミット | — | リリース承認ゲートの承認資料が対象にした、ベースブランチの HEAD のコミットの SHA（40 桁）。本番チャネルへ入れてよい中身の上限を示す | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 比べた HEAD | — | リリースが承認したコミットと比べた時点の、本番チャネルへ出すブランチの HEAD のコミットの SHA（リリースのテンプレートでは `release/v<バージョン>`、経路 merge ではベースブランチ）。本番チャネルへマージしてよいのはこのコミットだけである | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 確かめたコミット | — | 導入の確認を走らせた、ベースブランチの先頭のコミットの SHA（40 桁）。導入の確認の承認資料に書かれ、pace: fast / auto でリリースの Pull Request をマージしてよい HEAD はこのコミットに限る | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 承認の外のコミット | — | 承認したコミットから比べた HEAD までに入ったコミットのうち、このバージョンのデプロイの PR のものでないもの。リリース対象（`plugins/`・`.claude-plugin/`）に触れるものがあればリリースは承認ゲートで止まる | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 承認の記録 | — | 承認資料の欄の 1 つ。リリース承認ゲートを承認したときの承認したコミットの SHA と、承認した者・時刻を持つ。承認資料を書き直すと消え、承認し直すまでリリースは通らない | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 更新情報のセクション | — | plugin の README の `## 更新情報` のセクション。バージョンを名乗らず、CHANGELOG.md のバージョンのセクションを参照する固定の案内だけを持つ。リリースの手順（bump・changelog・notes）は書かない | 更新情報の節 | — | `docs/specifications/ndf-release-readme-update-info.md` |
| バージョンのセクション | — | CHANGELOG.md の `## [<plugin> <基底のバージョン>]` の見出しから次の `## ` までのセクション。そのバージョンの利用者向けの変化と移行の手順の全件の SSOT | 版の節 | — | `docs/specifications/ndf-release-readme-update-info.md` |
| 範囲の終点 | — | バージョンのセクションと承認資料の「含む PR」に並べる PR の範囲の、新しい側の端のコミット。出すバージョンのコミット（リリースのブランチの先頭か、承認に出すコミット）を指す | — | — | — |
| リリースタグ | — | 正式版を出したときに本番チャネルのコミットへ打つタグ `<プラグイン>--v<バージョン>`。リリースタグのあるバージョンのセクションは工程から書き換えない | — | — | — |
| 子の箇条 | — | PR 本文の箇条書きで、前の項目より字下げした箇条。親の項目の一部として読む | — | — | `docs/specifications/ndf-release-readme-update-info.md` |
| リリース後テストの記録 | — | release-verification が Pull Request へ残す、見出し `## リリース後テスト` から `合否:` までのブロック。`対象のバージョン:` の行と課題ごとの表を持つ | — | — | `docs/specifications/ndf-production-release-record.md` |
| 課題の表 | — | リリース後テストの記録のうち、見出しに `課題` と `結果` の両方の列を持つ表。スプリントの課題を閉じる判定とリリースの承認資料が、課題ごとの合否をここから読む | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 補足の表 | — | リリース後テストの記録のうち、課題の表でない表（例: 主なテストの実行の表）。課題を閉じる判定にも承認資料にも使わない | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 版数を上げないリリース | — | 版数を変えず、既定ブランチへのマージかコミットの反映で本番へ届けるリリース | 版数を上げない配布 | — | `docs/specifications/ndf-production-release-record.md` |
| 範囲の PR | — | 直前の正式版のタグから範囲の終点までに、ベースブランチへ merge commit か squash merge で入った PR と、スプリント経由の PR の和。`--prs` で渡した番号は、マージコミットが範囲の終点の祖先であるものだけを含む。版上げの PR と、PR でない番号（squash の件名の `(#N)` が課題を指すもの）は含まない | — | — | `docs/specifications/ndf-release-approval-included-prs.md` |
| 版上げの PR | — | head が release/v<バージョン> で、版数と CHANGELOG を書き、承認の対象 release_pr でリリース承認ゲートの承認を受ける本番ブランチ宛ての PR。どのリリース方式でもマージの前に切る。リリース方式 trunk では本番ブランチから切り、版数と CHANGELOG だけを変える。ほかの行ではベースブランチか本番ブランチから切り、まだ本番へ出ていない変更（ベースブランチに入った変更か、集めたスプリント）も持つ | — | — | `docs/specifications/ndf-release-approval-included-prs.md` |
| 合格の行 | — | リリース後テストの表で、`結果` の列が `合格` で始まる行 | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 手で足したセクション | — | 承認資料の `## ` のセクションのうち、デプロイのステップ（approval-facts・notes --approval・changed-plugins --approval）が書く見出しに当たらないもの。作り直しの後も見出しと中身が残る | 手で足した節 | — | `plugins/ndf/skills/release/references/release-steps.md` |
| 置き換える箇条 | — | 後の PR の本文の `## 置き換える箇条` のセクションに書く行。同じバージョンに含む前の PR の「利用者向けの変化」の箇条を指し、その箇条を CHANGELOG と承認資料の「配る中身」から除く | — | — | `plugins/ndf/skills/release/references/release-steps.md` |
| スプリント経由の PR | — | 範囲の PR のうち、merge commit でベースブランチへ入った PR の 2 つ目の親の側（スプリントブランチ）へ merge commit で入った PR。変化と移行の手順はスプリント PR の本文が集めて持つ | — | — | `docs/specifications/ndf-release-approval-included-prs.md` |
| 承認の材料 | — | 承認資料が集める 5 つの材料。レビュー（cross-review のラウンド数と見送った指摘）・リファクタリング（採用と取り消しの件数）・全体テスト（コマンド・合否・件数）・設計文書の未確認・未実施のリリース後テスト | — | — | `docs/specifications/ndf-release-approval-materials.md` |
| マニフェスト | — | plugins/ndf/manifests/<ランタイム>-skills.txt。ランタイムごとに配る Skill の一覧で、デプロイの唯一の基準（#2231） | — | — | `docs/specifications/ndf-distribution-runtimes.md` |
| バージョンの差し込み | — | 前のバージョンの NDF が組んだリリースプランが、具体的なバージョンの代わりに持つ `{release_version}`（出すバージョン）と `{release_base}`（その接尾辞を外したバージョン）。プランの型の移行関数がバージョンの参照へ読み替える（#2344） | — | — | — |
| バージョンの記録 | — | 解決のスナップショットのうち、バージョンの参照の行。決めた・確かめたバージョンと、決めた根拠と、決め直しの履歴を持ち、流し直しはこれを読む。前のバージョンの NDF が書いた `version.json` は移行関数でこの行へ読み替える（#2344） | — | — | — |
| バージョンの決め直し | — | 開発版の流し直しで、バージョンの記録のバージョンが自分のデプロイのマージより前に別のスプリントに出ていたとき、次に空いているバージョンを決め、解決のスナップショットのバージョンの行をそのバージョンへ上書きし、前のバージョンと理由をバックアップの履歴に残すこと。プランのファイルは書き換えない。明示のバージョンと、自分のデプロイのマージの後には行わない（#2344） | — | — | — |
| 明示のバージョン | — | `new sprint` / `new close` の `--version`・`--prod`、`new release` の `--version` で渡したバージョン。プランを作る時点で決まり、キューはそのバージョンがまだ出ていないかだけを確かめる | — | — | — |
| 利用者の設定ディレクトリ | — | 利用者の Claude Code の設定ディレクトリ（`CLAUDE_CONFIG_DIR`、無ければ `~/.claude`）と Codex の設定ディレクトリ（`CODEX_HOME`、無ければ `~/.codex`）。リリース後テストはこの中の導入済みのプラグインと取得元の登録を変えない | — | — | — |
| 導入の操作 | — | プラグインの導入・更新と、取得元の登録・更新（`claude plugin install` / `update` / `marketplace add` / `marketplace update`、`codex plugin add` / `marketplace add`）。リリース後テストの worker は利用者の設定ディレクトリでこれを打たない | — | — | — |
| 利用者の設定のスナップショット | — | 利用者の設定ディレクトリの、導入済みのプラグインと取得元の登録をキーと値の組で書き出したもの。リリース後テストの前に取り、後で今の状態と比べる | — | — | — |
| 対象のバージョン | — | リリース後テストの記録の `対象のバージョン:` の行に書くバージョン。全角の括弧から後ろは注記として読まない。先頭の小文字の `v` の有無は同じバージョンとして扱う | — | — | — |
| 本番のバージョン | — | リリース記録の `バージョン:` の行の `→` の右に書く、本番へ出したバージョン。全角の括弧から後ろは注記として読まない。読み取った値は先頭の `v` を付けない形にそろえる | — | — | — |
| バージョンの表記の揺れ | — | 同じバージョンを指す書き方の違いのうち、リリース記録とリリース後テストの記録の読み取りが吸収するもの。先頭の小文字の `v` の有無だけを指す | — | — | — |
| デプロイ | — | 開発・本番を問わず、環境へ変更を届けること。本番へのデプロイだけをリリースと呼ぶ。工程の名前でもある | 配布 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 開発版のデプロイ | — | 開発版のチャネルへバージョンを出すことと、検証の環境への反映。フェーズの名前でもある | 検証リリース、検証への配布、開発版の配布 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| デプロイなし | — | リリース記録の段階の値。どの環境へも届けずに工程を終えたこと | 配布なし | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| リリース対象 | — | リリースで利用者へ届くファイルの集まり。ai-plugins では plugins/ と .claude-plugin/ | 配布物 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| アーティファクト | — | ビルドや署名で作る、公開操作で届ける生成物（パッケージ・署名したバイナリなど） | — | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 導入先のランタイム | — | マーケットプレイスのプラグインを導入できる形で届けるランタイム。#2231 の後は Claude Code と codex で、相手役のランタイムとは別に決まる（#2231） | 配布先のランタイム | — | `docs/specifications/ndf-distribution-runtimes.md` |
| 利用者向けの変化なし | — | PR の本文の `## 利用者向けの変化` が「無し」（「なし」も同じ）だけを持つこと。承認資料の「含む PR」の行にマーカーを付け、CHANGELOG のバージョンのセクションと「配る中身」には載せない。セクションの無い本文（題名で代える）とは別に扱う | 配布物の変化なし | — | `docs/specifications/ndf-release-approval-included-prs.md` |
| リリースの設定 | — | `.ndf/` に書く、そのリポジトリのリリースの設定（`.ndf/supervise.json` の release と `.ndf/release.json`） | 配布の宣言 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| リリースの形 | — | 利用者へ届く仕組みの種類（パッケージ・プラグイン、サービス、モバイルアプリ、業務文書など）。`.ndf/project.json` のセクション `branch_strategy` の `release.form` の値で、リリースの形の表の行を指す | 配布の形 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| リリースの手順 | — | バージョンを上げてから公開するまでの手順。`release` が持つ | 配布の手順 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 取得元 | — | プラグインを取得するマーケットプレイスの登録が指す先 | 配布元 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| CD の結果 | — | 本番チャネルへのマージの後に NDF が読んだ CD の状態。成功・失敗・未確定の 3 つで、リリース記録に残す | — | — | `docs/specifications/ndf-release-via-cd.md` |
| 結果の読み方 | — | CD の結果を読む手段の設定。delivery の行の result に書き、GitHub Actions のワークフローの名前かコマンドで指す | — | — | `docs/specifications/ndf-release-via-cd.md` |
| 公開物 | — | 正式版ごとのリリースタグと GitHub Release の組 | — | — | `docs/specifications/ndf-release-via-cd.md` |
| 反映失敗 | — | リリース記録の段階の値の 1 つ。本番チャネルへマージしたが CD の結果が失敗で、スプリントを閉じられない状態 | — | — | `docs/specifications/ndf-release-via-cd.md` |
| リリースブランチの PR | — | 選んで集めるリリース方式（`release_strategy: release_branch`）で、リリースブランチから本番チャネルへ出す PR | — | — | `docs/specifications/ndf-branch-flow-review-units.md` |
| 衝突の解消の差分 | — | スプリントブランチをリリースブランチへ集めるマージのコミットが、親を自動でマージし直した結果（衝突のマーカーを含む）と比べて持つ差分。衝突の解消と、組み合わせのためにマージのコミットで足した変更が入る | — | — | `plugins/ndf/skills/worktree/references/declaration.md` |
| 衝突の解消のレビューの PR | — | 衝突の解消の差分だけを PR の差分として見せるため、開発版のデプロイのプランが一時に作る PR。レビューで入った直しをリリースブランチへ移してから閉じる | — | — | `plugins/ndf/skills/release/references/release-steps.md` |
| リリースの PR | — | 本番チャネル（main）へバージョンを入れる Pull Request。版上げまでのコミットを積み終えた状態で承認を求める | — | — | `docs/specifications/ndf-release-approval-on-pr.md` |
| 証跡のチェック | — | リリースの PR に承認の証跡があり、書かれたコミットが PR の HEAD と一致し、書いた人が承認権限を持つときだけ通る必須ステータスチェック | — | — | `docs/specifications/ndf-release-approval-on-pr.md` |
| 承認権限 | — | リリースの承認の証跡を書いてよい権限。リポジトリの admin か maintain | — | — | `docs/specifications/ndf-release-approval-on-pr.md` |
| リリースの場合 | — | CD の有無・本番チャネルの承認数・迂回の設定で決まる 4 つの場合（CD があり、レビューなしでマージできる / CD があり、レビューが必須 / CD があり、レビューが必須で迂回の設定がある / CD が無い）。承認を取る場所とマージの主体を決める | — | — | `docs/specifications/ndf-release-production-admin-merge.md` |
| 承認資料のコメント | — | 承認を求める時点の承認資料を、リリースの PR へ置いたコメント。証跡とは別のマーカーを持ち、判定に数えない | — | — | `docs/specifications/ndf-release-approval-on-pr.md` |
| 開発版の workflow | — | 手元で作った開発版の PR を、CI の必須チェックが通るのを待って develop へマージする GitHub Actions の workflow | — | — | `docs/specifications/ndf-release-approval-on-pr.md` |
| 承認の証跡 | — | リリースの PR に置いた、承認したコミットの 40 桁の SHA を書いたコメント（証跡のマーカーと `承認したコミット:` の行を持つ）。承認ラベルや承認ゲートの記録は含まない | — | — | `docs/specifications/ndf-release-approval-on-pr.md` |
| 戻すマージ | — | 今の NDF（11.0.0 の入れ替えの前）だけの語。正式版のリリースの後、リリースブランチを `release/v<バージョン>-sync` 経由でベースブランチへマージするマージ。版数を持つ箇所が衝突したら本番のバージョンを取る。ndf-next（GitHub flow）は同期の PR を作らず、この操作を持たない | — | — | — |
| 本番の PR | — | 本番ブランチ（main）へ入れる Pull Request。リリース方式の行 `trunk` では本番ブランチ宛てのスプリントの PR と版上げの PR を指し、行 `merge_base_branch`・`release_branch` では版上げの PR を指す | — | — | `docs/specifications/ndf-release-production-admin-merge.md` |
| 管理者の迂回 | — | ruleset が迂回を許した役割の人が、必須の承認レビューと必須のチェックを満たさずにマージすること。`gh pr merge --admin` で打つ | — | — | `docs/specifications/ndf-release-production-admin-merge.md` |
| 迂回の設定 | — | 本番の PR を管理者の迂回でマージすることを選ぶ、`.ndf/project.json` の `production_merge.admin` の値。`true` のときだけ設定があるとみなし、無いときは迂回しない | — | — | `docs/specifications/ndf-release-production-admin-merge.md` |
| 迂回の記録 | — | 本番の PR を管理者の迂回でマージしたことを示す、本番の PR へ置くコメント。マージした HEAD と照らした承認の証跡を指す。証跡とは別のマーカーを持ち、承認の判定に数えない | — | — | `docs/specifications/ndf-release-production-admin-merge.md` |
| 承認済みのコミット | — | 承認ゲートの承認か、利用者の GitHub でのマージ（キューへ入れる操作を含む）が対象にした本番の PR の head コミット。本番ブランチへ入れてよいのはこの head だけである | 承認した head、承認した先端 | — | — |
| head の一致の確認 | — | マージかmerge queue への追加の API に承認済みのコミット を渡し、PR の head が違えば GitHub に断らせることと、マージの前に承認の記録を読み直すこと | 先端の照合 | — | — |
| 承認の経路 | — | 本番の PR の承認がどこで与えられたか。リリース承認ゲート（`gate`）か、利用者の GitHub でのマージ（`github-merge`）のどちらか | — | — | — |
| 変更要求 | — | リリース承認ゲートで承認しない回答、または必須のチェックの落ちた本番の PR を出さずに止めること。直しを同じ本番の PR（スプリントの PR）へ入れ、新しい HEAD で承認を取り直す | — | — | — |
| 修正ステップ | — | CI が落ちたときにコードを直して push するプランのステップ（テンプレートの `ci-fix`・`fix-push`・`fix-base` と、それに続く `push`） | 直しのステップ | — | — |
| fix の worker | — | 要求での修正ステップの呼び方。CI が落ちたときにコードを直して push するステップ（テンプレートの `ci-fix` とそれに続く `push`） | — | — | — |
| リリースブランチ | — | ndf-next のリリース方式の行 `trunk` では、版上げの PR の head になるブランチ（`release/v<バージョン>`）。main へのマージの後に CD が main の HEAD から作り、版上げの PR のマージで消える。行 `merge_base_branch`・`release_branch` では `ndf-workflow` の同じ語の意味（本番チャネルへ入れる変更を集めるブランチ） | — | — | — |
| 版上げのコミット | — | 版上げの PR の中で、バージョンの保存先のファイルと CHANGELOG のバージョンのセクションを変えるコミット。リリースの工程が版上げの PR のマージの前に入れる | — | — | — |
| バージョン種別のラベル | — | main 宛ての PR がバージョン種別（MAJOR・MINOR・PATCH）を示すラベル（`release:major` など）。CD がマージの時点に読んでバージョンを上げる | 上げ幅のラベル、上げ幅の宣言 | — | — |
| 検証ブランチ | — | main 宛ての開いた PR を集めて組む使い捨てのブランチ（ai-plugins では `verify/next`）。検証用マーケットプレイスが指し、main へ戻さない | 検証用ブランチ、検証の ref | — | — |
| 検証用マーケットプレイス | — | 正式版のマーケットプレイスと違う名前で検証ブランチを指すマーケットプレイス。名前が違うので正式版と両方を登録できる。検証環境の種類の 1 つ | 2 つ目のマーケットプレイス | — | — |
| ブランチ戦略 | — | リポジトリがどのブランチから作業を切り、どこへマージし、どこから本番へ出すかの決まり。リポジトリごとに `.ndf/project.json` のセクション `branch_strategy`（ブランチ戦略の設定）の `release_strategy` で、リリース方式の表の行を選ぶ | — | — | — |
| リリース方式の表 | — | ndf-next がブランチ戦略として選べる行の一覧（`plugins/ndf-next/contracts/domains/branch-flows.json`）。`merge_base_branch`・`release_branch` と、スプリントブランチを main から切って main へマージする `trunk` の行を持つ（行の名前は `.ndf/project.json` のセクション `branch_strategy` の `release_strategy` の値と同じ） | — | — | — |
| merge queue | — | GitHub のマージキュー。キューへ入れた PR を、main の HEAD と前に並ぶ PR を合わせた一時マージコミット（`gh-readonly-queue/main/...`）で必須のチェックに通し、通ったものだけを順にマージする | — | — | — |
| マージの方式 | — | 本番ブランチへのマージで、同時に承認した PR の組み合わせをどう確かめるか。merge-queue（merge queue を使う）・strict（head が本番ブランチの HEAD を含むときだけマージ）・direct（確かめない）のどれかで、ブランチ戦略の設定の merge.method が決める | — | — | — |
| 検証環境 | — | マージの前に変更を試す場所。リポジトリごとに設定ファイルで種類を選ぶ。プラグインなら検証用マーケットプレイス、Web なら staging 環境 | — | — | — |
| バージョンアップ | — | 版上げの PR の中でバージョンを上げること。書き換えるファイルは `.ndf/project.json` のセクション `branch_strategy` の `release.version_store` で決める | バージョン上げ | — | — |
| merge queue への追加 | — | 承認済みのコミット を `expectedHeadOid` に渡して、本番の PR を merge queue へ入れること（GraphQL の `enqueuePullRequest`）。PR の head が違えば GitHub が断る | キューへの投入 | — | — |
| バージョンの保存先 | — | リリースの単位のバージョンを持つファイルと、その読み書きのアダプタ（`plugin`・`json` など）。`.ndf/project.json` のセクション `branch_strategy` の `release.version_store` で選ぶ | — | — | — |
| リリースのワークフロー | — | 本番リリースのパイプライン。本番チャネルへ変更を届ける手順の並び | 本番のプラン | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| 本番のマージ | — | 承認済みのコミットを、検査がそろうのを待ってから、ブランチ戦略の設定のマージの主体と方式（land_approved）で本番ブランチへ入れること。結果は入れた・merge queue へ入れた・人のマージを待つ・止まったのどれか | — | — | `docs/specifications/ndf-release-production-land-and-trunk-sprint.md` |
| 名前の書式 | — | リリースタグ・CHANGELOG のバージョンのセクションの見出し・リリースブランチの名前を、バージョン（と必要ならプラグインの名前）から作る書式。`.ndf/project.json` のセクション `branch_strategy` の `release` で変えられ、無ければ今の形になる | — | — | — |
| リリース対象外のファイル | — | バージョンを上げるかの判定で数えないファイル（テストなど）。既定は `contracts/domains/test-files.json` の行から作り、設定の `undistributed` で足せる | — | — | — |
| リリースの形の表 | — | リリースの形ごとの届き方と、ndf-next での扱い（扱う / 11.x へ回す理由）を持つ表（`plugins/ndf-next/contracts/domains/release-forms.json`）。設定の `release.form` の値ごとに 1 行 | — | — | — |
| バージョンの単位 | — | 1 つのバージョンを持つリリースの対象。バージョンの保存先 `plugin` ではマーケットプレイスに登録したプラグイン 1 つ、ほかの保存先ではリポジトリ全体の 1 つ。選んで集めるリリース方式の「リリースの単位」（スプリントブランチ）とは別 | — | — | — |
| 保存先のアダプター | — | バージョンの保存先の種類ごとに、バージョンの単位の一覧・バージョンの読み書き・書き換えてよいパスを同じインタフェースで持つ部品（`plugins/ndf-next/src/ndf/domains/release/version_store.py`） | — | — | — |
| 前のリリースの起点 | — | 前に出したバージョンの位置を表すコミット。前のバージョンのタグ、無ければ本番ブランチで今のバージョンが入ったコミット、それも無ければ本番ブランチの最初のコミット。バージョンの単位ごとに決まる。範囲の起点に使うのは、リリース方式が `trunk` の `release_pr` と `release_branch` のときだけである | — | — | — |
| 範囲の起点 | — | 承認資料とリリースブランチに集める差分の範囲の、古い側の端のコミット。リリース方式で決まる。`merge_base_branch` は本番ブランチの HEAD、`trunk` の `release_pr` と `release_branch` はバージョンの単位ごとの前のリリースの起点、`production_pr` は本番の PR と本番ブランチの分岐点 | — | — | — |
| 再利用可能なワークフロー | — | GitHub Actions で `workflow_call` を持ち、ほかのリポジトリの workflow から `uses:` で呼べる workflow。NDF のリリースでは ai-plugins の `.github/workflows/ndf-release-reusable.yml` | — | — | — |
| 経路を動かすバージョン | — | リリース後に確かめる受け入れ条件の経路を実際に動かすコードのバージョン。配ったバージョンか、利用者の手元の旧いバージョンのどちらか。旧いバージョンなら確かめる時点は次のリリースの後 | — | — | — |
