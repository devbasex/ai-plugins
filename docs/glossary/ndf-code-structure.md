# 用語集: NDF のコードの構造（`ndf-code-structure`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

NDF のスクリプトの構造・エントリポイントとコントラクト・公開する面・依存のバージョン・AI の兆候の検査の語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 分割した表 | — | 1 項目 1 ファイルへ分けたコントラクトの表。ディレクトリの名前がもとの表の名前で、ファイル名が項目のキーになる | — | — | `docs/specifications/ndf-split-tables-toolchain-and-budget.md` |
| 固定する理由（依存のバージョンの） | — | 依存を上げる先へ上げずに今のバージョンに止める理由。種類・相手・制約・見直す日・理由の欄を持ち、依存の更新の仕組み（Renovate）の依存ごとの規則の description に行で書く（#3370 #3691 の決定 5） | — | — | `docs/specifications/dependency-updates-renovate-and-supply-chain.md` |
| 安定版（依存の） | — | 依存のパッケージのバージョンのうち、PEP 440 の pre-release でも developmental release でもないバージョン。NDF の変更の経路を分ける「安定版と実験版」の安定版とは別の語で、「最新の安定版」の形でだけ使う | — | — | `docs/specifications/ndf-rebuild.md` |
| ライブラリ | `library` | 2 つ以上のスクリプトが使う関数とクラスの保存先。今の plugins/ndf/scripts/lib/ に当たる。pace.json の「重点領域」（点数を重くする領域）とは別 | — | — | `docs/specifications/ndf-rebuild-libraries.md` |
| エントリポイント | `entry_point` | Skill・プラン・hook・設定・利用者が呼ぶスクリプトのパスとサブコマンドと引数と出力の形 | 入口 | — | `docs/specifications/ndf-rebuild.md` |
| 構造チェック | `structure_check` | テストを除くスクリプトの行数と、本体の同じ関数・同じ名前で本体の違う関数を構文木で数えるCI のチェック。ファイルを指定すると、検査は木全体で行い、合否を指定したファイルに関わる違反だけで決める | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 依存の欠け | — | 手順のスクリプトが deps.require で外部パッケージを用意できずに止まったこと。終了コード 69 で終わり、status は stopped。「飛ばしてよい」とも「前提が無い（3）」とも読まない | — | — | `docs/specifications/ndf-deps-missing-exit-code.md` |
| 依存グループ | — | lib/deps.py の GROUPS と pyproject.toml の extra に並ぶ、外部パッケージの用途ごとのまとまり（procs・locks など）。deps.require へ名前で渡す | — | — | `docs/specifications/ndf-deps-require-groups-check.md` |
| 未設定のグループ | — | モジュールから import をたどって届く外部パッケージの依存グループのうち、そのモジュールの中で覆う deps.require が無いもの。そのモジュールを読む側が引き受ける | — | — | `docs/specifications/ndf-deps-require-groups-check.md` |
| 任意の依存 | — | 無ければ代わりの手段へ落ちる import。except ImportError / ModuleNotFoundError で受けるか、同じ関数の中で find_spec / deps.importable で確かめ、確かめが成り立ったときだけ通る枝で読む | — | — | `docs/specifications/ndf-deps-require-groups-check.md` |
| コントラクトの一覧 | `contract_registry` | エントリポイントごとの入力（引数・環境変数・.ndf/ の設定のキー・標準入力）・出力・書く状態・終了コードと、受け渡しのファイル・設定・環境変数の書き手と読み手と保存先を持つ、リポジトリで 1 つの設定 | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 環境変数のキー | — | 環境変数のコントラクト（plugins/ndf/contracts/domains/env.json）の各項目が持つ `key` の値。コードはこのキーから環境変数名を引き、環境変数名を文字列リテラルで書かない | — | — | — |
| 名前の対応表 | `internal-names` | 同じ意味の値の 1 つの名前と、消した別名と、使う種類を持つ表（plugins/ndf/contracts/data/internal-names.json。#2339） | — | — | `docs/specifications/ndf-internal-data-versions.md` |
| 読み手の指定 | `read_by` | 上限の設定の方針が持つ、その方針を引くモジュールの名前（read_by。#3684） | — | — | `docs/specifications/ndf-contract-fact-checks.md` |
| 受け渡しのファイル | `handoff_file` | あるエントリポイントが書き、別のエントリポイントか Skill の手順が読むファイル（状態・記録・結果）。1 つのエントリポイントの中で作って消す一時ファイルは含まない | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 例外リスト | `allow_list` | 構造チェックとリポジトリ前提の検査が違反として数えない今の外れを 1 項目 1 ファイルで持つ保存先。チェックごとに置く（scripts/script-structure-allow/・scripts/contract-allow/・scripts/repo-assumptions-allow/）。違反に当たらなくなった項目は落ち、減る方向にだけ変える | 許可の一覧 | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 移行の読み | `migration_read` | 1 か所へ寄せた設定の値を、新しい保存先に無いときだけ古い保存先から読むこと。コントラクトの一覧の設定ファイルの項目に migrate_from として書く | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 設定の保存先 | `setting_place` | .ndf/ の設定ファイルを読む木。worktree（呼ばれた木。チームの設定）か main（メインディレクトリ。個人の設定）で、コントラクトの一覧の設定ファイルの項目の place が決める | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 呼び手 | — | 関数かエントリポイントを呼ぶ、テストの外のファイル（Skill の本文・テンプレート・hook・CI・ほかのスクリプト）。テストだけが呼ぶものは呼び手 0 と数える | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 残す関数の一覧 | — | 関数の棚卸し（uvx vulture）が呼び手 0 と見るが、vulture に見えない呼ばれ方などで残す関数を、理由の種類と理由と定義のあるファイルを 1 行ずつ添えて持つ一覧。保存先は scripts/ndf-vulture-keep.txt。構造チェックの例外リストとは別のもの | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 旧い値の読み替え | — | バージョンを上げる前に書かれた記録の値を、読む時点で新しい値へ変換すること | — | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 兆候 | — | AI が陥りやすい作り（7 種類）に当たる箇所の候補。直す対象とは限らない | — | — | `docs/specifications/ndf-rebuild-data-and-smells.md` |
| 兆候の基準ファイル | — | `scripts/ai-smells-baseline/` に元のファイル 1 本につき 1 つ置く、規則ごとの許す数の JSON | — | — | `docs/specifications/ndf-rebuild-data-and-smells.md` |
| 抑制コメント | `suppressions` | 握りつぶし・フォールバック・ループの中の呼び出しを許す行に付ける `noqa` コメント `# noqa: <規則> <境界の種類>: <理由>`。ループの中の呼び出し（`NDF201`）は `# noqa: NDF201 上限: <件数と根拠>` も使える | 印（単独） | — | `docs/specifications/ndf-rebuild-data-and-smells.md` |
| 判定データ | — | 分岐の多い判定を置き換える「キー → 値か判定の関数」のデータと、それを引く 1 つの関数。要求（#1142 の本文）の「判定表（データ）」はこれを指す。用語集の「判定表」（モードを判定する順の表）と、詳細設計のテンプレートの「分岐の条件と結果」に書く判定表（条件の組み合わせと結果の表）とは別 | — | — | `docs/specifications/ndf-rebuild-data-and-smells.md` |
| ラチェット | — | 基準ファイルの数より増やす変更を CI で落とし、減らした変更では基準を下げる検査。数は減る向きにしか動かない | — | — | `docs/specifications/ndf-contract-fact-checks.md` |
| アダプター | — | 外部ライブラリを呼ぶ唯一の `lib/` のモジュール（`lib/md.py`・`lib/pathmatch.py` など）。ほかのモジュールはライブラリを直に import せず、これを呼ぶ。relay の意味の「ラッパー」とは別 | — | — | `docs/specifications/ndf-rebuild-libraries.md` |
| 事実の表 | — | 1 つの事実（例: ランタイムの一覧）を行と列で持つコントラクトの JSON。`contracts/facts/` の 1 ファイル | — | — | `docs/specifications/ndf-rebuild-data-and-smells.md` |
| オーナーのモジュール | — | 1 つのコンテキストの Python だけが読む事実を、定数として 1 か所で持つモジュール。ほかのモジュールは import する | — | — | `docs/specifications/ndf-rebuild.md` |
| 重複の組 | — | 同じ事実を持つと見える 2 つ以上の最上位の定数の組。種類は `same-set`・`subset`・`affix`・`same-str`・`member`・`same-num` | — | — | `docs/specifications/ndf-rebuild-data-and-smells.md` |
| 重複の基準 | — | 移行の前に拾った重複の組と、組ごとの扱いの一覧。ラチェットの基準になる | — | — | `docs/specifications/ndf-rebuild-data-and-smells.md` |
| 公開の関数 | — | モジュールの外から呼ばれる関数。詳細設計の「関数の仕様」に書くのは公開の関数だけである | — | — | `docs/specifications/ndf-next-closed-children-subcommands.md` |
| 非公開の関数 | — | 公開の関数だけが呼ぶ関数。名前が `_` で始まる | — | — | `docs/specifications/ndf-next-closed-children-subcommands.md` |
| チェックアウトの保存先 | — | <git の共通ディレクトリ>/<プラグイン名>/state/。どの worktree からも同じ所に解決し、ステートマシンの DB と実行のキーごとの耐久の記録とバックアップを置く（#1142 の作り直し） | — | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| 形の番号 | — | チェックアウトの保存先の format.json と耐久の記録のアプリのバージョンが持つ整数。読み書きの形が変わるときだけ上げ、NDF のバージョンとは別に動く | — | — | `docs/specifications/ndf-rebuild.md` |
| 公開する面 | — | 外の呼び手が頼ってよい面（サブコマンド・環境変数・受け渡しのファイル・hook・設定とそのスキーマの保存先）。名前と意味をバージョンで約束し、公開する面の表に載る | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| 内部の面 | — | NDF の中だけで使い、形を約束しない面。公開する面の表に載らない | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| サブコマンド | `subcommand` | `bin/ndf <名前>` の `<名前>`。ndf-next のサブコマンドの表（`cli.py` の `COMMANDS`）に載るもの | 副命令 | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| 配る Skill の表 | — | ndf-next が配る Skill と配るランタイムの一覧（`plugins/ndf-next/manifests/skills.json`） | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| 起動スクリプト | — | 利用者と AI が打つ `plugins/ndf-next/bin/` のスクリプト（`ndf`・`ndf-claude`） | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| コントラクトのページ | — | ndf-next の設定と入出力の規約を、面ごとのページ（設定・サブコマンド・終了コード・環境変数・受け渡しのファイル・hook・Skill）で表した文書（`plugins/ndf-next/docs/contracts/`）。SSOT の表とスキーマから生成し、手で書かない | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| Skill の入出力の表 | — | 配る Skill ごとの `/goal` の終わりの条件・読む設定・結果の形と、公開する面に無い Skill の引数の形と環境変数を持つ事実の表（`plugins/ndf-next/contracts/domains/skill-io.json`） | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| 起動スクリプトの表 | — | `plugins/ndf-next/bin/` のファイルごとに、起動スクリプトか内部の部品かを持つ事実の表（`plugins/ndf-next/contracts/domains/launchers.json`） | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| 手書きの引数の表 | — | argparse を使わないサブコマンドの呼び方（下位の命令の道ごとに 1 行）と引数を持つ事実の表（`plugins/ndf-next/contracts/domains/manual-args.json`）。規約の生成がこの表から引数のスキーマを作る | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
| 上げる先（依存のバージョンの） | — | #3347 の決定 106 の規則で選んだ依存のバージョンのうち、最初のアップロードから 7 日たったもの | — | — | `docs/specifications/dependency-updates-renovate-and-supply-chain.md` |
| 遅れ（依存のバージョンの） | — | 設定のバージョンが上げる先より古く、固定する理由も無い状態。週 1 回の検査が知らせる | — | — | `docs/specifications/dependency-updates-renovate-and-supply-chain.md` |
| 依存の更新の仕組み | — | 依存のバージョンの遅れを知らせ、バージョンを上げる手段。このリポジトリでは Renovate を GitHub Actions で動かす形（#3370 の決定 1） | — | — | `docs/specifications/dependency-updates-renovate-and-supply-chain.md` |
| 依存の一覧の Issue | — | 依存の更新の仕組みが遅れを知らせる 1 件の Issue。Renovate の Dependency Dashboard で、チェックを入れると更新の PR が作られる | — | — | `docs/specifications/dependency-updates-renovate-and-supply-chain.md` |
| 推移的な依存 | — | 定義に書かず、定義した依存が求めるために lock に入るパッケージ。依存の更新の仕組みは lock の作り直しで上げる | — | — | `docs/specifications/dependency-updates-renovate-and-supply-chain.md` |
| 脆弱性の知らせ | — | 週 1 回のサプライチェーンの検査が、対象の lock の既知の脆弱性と解いてよくなった固定をまとめる 1 件の Issue。依存の一覧の Issue とは別に置く | — | — | `docs/specifications/dependency-updates-renovate-and-supply-chain.md` |
