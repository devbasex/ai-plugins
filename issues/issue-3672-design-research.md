# #3672 の外部の調査

設計文書 [issue-3672-design.md](issue-3672-design.md) から分けた外部の調査である。標準・仕様（`STD<数>`）・主要な製品（`PRD<数>`）・ライブラリとサービス（`LIB<数>`）の番号は、
設計文書・[決定の記録](issue-3672-design-decisions.md)・[削除の一覧](issue-3672-design-removal.md) から参照される。

## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | ブランチ戦略・版の規則・タグの形・マージの一致の確認は GitHub・Claude Code・Codex の仕様と公開の規約で決まる |
| 主要な製品 | 調べる | plugin とマーケットプレイスを配るリポジトリの実際の運用を、ai-plugins の値の出典にする |
| ライブラリとサービス | 調べる | 版上げ・組み合わせの確かめ・移行の差分を既存のツールに任せられるかを比べる |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | GitHub REST「Merge a pull request」 | `sha` は head が一致するときだけマージを許す。違えば 409 | I5 | https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request （2026-11-15、#3572 の STD1） |
| STD2 | Trunk-Based Development | 短命のブランチで trunk へ入れ、リリース用のブランチは必要なときに切って消す。長く残るブランチを複数持つモデルより勧める | ai-plugins の `trunk`（決定 12） | https://trunkbaseddevelopment.com/ （2026-11-16） |
| STD3 | GitHub Docs「Managing a merge queue」 | 本番ブランチの先端と前に並ぶ PR を合わせた一時のコミットで必須のチェックを回す。Organization の public か Enterprise Cloud の private で使える。workflow は `merge_group` で起動する | マージの方式 `merge-queue` と、使えないときの `strict`（決定 7） | https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue （2026-11-16） |
| STD4 | Claude Code「Plugin loading」「Host a marketplace」 | 「Users get a new copy only when the plugin's computed version differs from the one they have」。版数は `plugin.json` を優先し、マーケットプレイスの項目と両方に書かない。チャネルの概念は無く、名前の違う 2 つのマーケットプレイスで別の ref を指す | 版上げの PR のマージをリリースにする（決定 8・12） | https://code.claude.com/docs/en/plugins/loading.md ・ https://code.claude.com/docs/en/plugins/host-marketplace.md （2026-11-16） |
| STD5 | Claude Code「Host a marketplace」「Dependencies」 | タグの形は `<plugin-name>--v<version>`。`claude plugin tag --push` が版数の一致を確かめて作る。版の範囲の依存はこのタグで解決し、プレリリースを含まない | 種類 `plugin` のタグの形 `{plugin}--v{version}` | https://code.claude.com/docs/en/plugins/host-marketplace.md ・ https://code.claude.com/docs/en/plugins/dependencies.md （2026-11-16） |
| STD6 | RFC 6902 JSON Patch | `test`・`move`・`replace` の操作の列 | 移行の差分（決定 10） | https://www.rfc-editor.org/rfc/rfc6902 （2026-11-16、#3613 の STD3） |
| STD7 | JSON Schema「Conditional subschemas」 | `if`・`then`・`else` で値の組み合わせの規則を書ける（Draft 7 以降） | 組み合わせの規則 R1〜R10（決定 2） | https://json-schema.org/understanding-json-schema/reference/conditionals （2026-11-16、HTTP 200） |
| STD8 | GitHub Docs「GitHub flow」 | 既定ブランチから切ったブランチを PR で既定ブランチへマージし、ブランチを消す | 行 `trunk` の起点と宛先 | https://docs.github.com/en/get-started/using-github/github-flow （2026-11-16） |
| STD9 | Semantic Versioning 2.0.0 | プレリリースは正式版より順序が下。`v1.2.3` の `v` は版数ではなくタグの慣習 | 版数は `plugin.json` に semver で持ち、タグに `v` を付ける | https://semver.org/ （2026-11-16） |
| STD10 | Codex「Build plugins」と openai/codex のソース | キャッシュは `$MARKETPLACE/$PLUGIN/$VERSION/`。`.claude-plugin/marketplace.json` も読む。Git のマーケットプレイスは起動時に自動で更新され（`PluginGitMode::Automatic`）、リモートの revision が変わると、入れた plugin を版数を比べずに入れ直す（`NonCuratedCacheRefreshMode::ForceReinstall`） | Codex の利用者には、取得元の ref が進むたびに届く。ai-plugins の取得元の扱いを人へ残す（決定 12） | https://developers.openai.com/codex/plugins/build.md ・ https://github.com/openai/codex/blob/ca0bbbdf0c/codex-rs/core-plugins/src/manager.rs （`upgrade_configured_marketplaces_for_config_with_mode`）・ `codex-rs/core-plugins/src/loader.rs`（`refresh_non_curated_plugin_cache_with_mode`）（2026-11-16） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | anthropics/claude-plugins-official | main だけ。タグと Release は無い。外部の plugin は `sha` で固定し、毎晩 bump の PR を出す。merge commit を許し、承認 1 を求める | ai-plugins の main だけの開発と merge commit | `gh api repos/anthropics/claude-plugins-official/rulesets`・`branches`・`tags`（2026-11-16） |
| PRD2 | anthropics/skills・wshobson/agents・openai/plugins | main だけ。タグも Release も無い。openai/plugins は squash を必須にする | main だけの開発 | `gh api repos/<r>/branches`・`tags`・`rulesets`（2026-11-16） |
| PRD3 | openai/codex | main を trunk にして `release/*` を切る。プレリリースの Release を出す。squash と linear history を必須にする | リリースブランチは必要なときに切る（版上げの PR） | `gh api repos/openai/codex/rulesets`・`releases`（2026-11-16） |
| PRD4 | obra/superpowers | `dev` を持ち、`dev→main` の Release の PR で出す。タグは `vX.Y.Z` | develop→main の行の実例。ai-plugins では採らない（決定 12） | `gh pr list -R obra/superpowers --state merged`（2026-11-16） |
| PRD5 | git-flow（nvie/gitflow） | `git flow init` が開発と本番のブランチの名前を対話で問い、設定に書く。アーカイブ済み | 宣言を問いと答えで作る形は倣い、対話ではなく結果の問いで返す（決定 4） | `gh api repos/nvie/gitflow`（`archived: true`）と README（2026-11-16） |
| PRD6 | anthropics/claude-code | タグ `v2.1.325` 形式と Release。公開したタグを変えられない ruleset を持つ | ai-plugins のタグの保護 | `gh api repos/anthropics/claude-code/rulesets`（2026-11-16） |

### ライブラリとサービス

| # | 候補 | 版 | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | release-please | v17.11.2 | Apache-2.0 | 2026-08-24 | 無し | 満たさない。上げ幅を Conventional Commits から読み、タグの既定 `<component>-v` が STD5 と違う | `gh api repos/googleapis/release-please/releases/latest`（2026-11-16） |
| LIB2 | semantic-release | v25.0.9 | MIT | 2026-08-05 | 無し | 満たさない。LIB1 と同じくコミットの書式から上げ幅を読む | `gh api repos/semantic-release/semantic-release/releases/latest`（2026-11-16） |
| LIB3 | GitHub merge queue | — | GitHub のサービス | — | 無し | 満たす（使えるリポジトリで）。組み合わせの確かめを自前で作らない | STD3 |
| LIB4 | bors-ng | — | — | — | 有り（アーカイブ済み。GitHub の merge queue を案内） | 満たさない | `gh api repos/bors-ng/bors-ng`（2026-11-15、#3572 の LIB4） |
| LIB5 | jsonpatch（Python） | 1.47 | PyPI に記載なし | 2026-11-11 | 無し | 満たすが採らない。移行で使う操作は 3 つで、依存を足す量が無い（#3613 の決定 12 と同じ） | https://pypi.org/pypi/jsonpatch/json （2026-11-16） |
| LIB6 | GitHub REST API（pulls・tags・releases・rulesets・repos） | — | GitHub のサービス | — | 無し | 満たす。既存のリポジトリの事例をこの API で読む | https://docs.github.com/en/rest （2026-11-16） |

## 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| 各リポジトリのマージの設定（`allow_squash_merge` など） | `gh api repos/<r>` | 権限が無く空を返した。ruleset と commit の親の数から読んだ |
| release-please でタグの区切りを `--v` にする設定 | release-please の README と manifest-releaser.md | 取得したページに記述が無い。LIB1 を採らない理由は上げ幅の読み方だけで足りる |
| Codex のチャネルとタグの形の推奨 | build.md | 記述が無い |

調べた量: 候補 22 件・取得の試み 39 回（上限の 30 回を 9 回超えた。超えた回は LIB1・LIB2 の版とライセンスの確かめの 2 回と、Codex の再導入の条件の確かめ（STD10）の 7 回）。
