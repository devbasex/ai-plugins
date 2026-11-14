# 設定ファイルの書き方

worktree 運用の仕組みは、リポジトリごとの差を `.ndf/worktree.json` から読む。
**このファイルが無いリポジトリでは、すべての仕組みが何もせずに終わる。**

定義は [`../schemas/worktree.schema.json`](../schemas/worktree.schema.json) にある。
`$schema` を書いておくと編集時に補完が効く。読み取り側はこの項目を参照しない。

**目次:** [この文書が受け取る値](#この文書が受け取る値) / [作る](#作る) / [最小の設定](#最小の設定) / [ツールが書き換えるファイルを追跡しているリポジトリ](#ツールが書き換えるファイルを追跡しているリポジトリ) / [開発の起点が既定ブランチと違うリポジトリ](#開発の起点が既定ブランチと違うリポジトリ) / [メインディレクトリのブランチを稼働中の worktree へ追従させる](#メインディレクトリのブランチを稼働中の-worktree-へ追従させる) / [ローカル環境を持つリポジトリ](#ローカル環境を持つリポジトリ) / [依存物を worktree へ用意するリポジトリ](#依存物を-worktree-へ用意するリポジトリ) / [テスト実行を分けるリポジトリ](#テスト実行を分けるリポジトリ) / [機械ごとに違う値は個人設定へ書く](#機械ごとに違う値は個人設定へ書く) / [確かめる](#確かめる) / [互換性の規則](#互換性の規則)

## この文書が受け取る値

| 変数 | 値 | 決め方 |
| --- | --- | --- |
| `$SCRIPTS` | プラグインの `scripts/` の絶対パス | [SKILL.md](../SKILL.md) の手順 0。シェルが変わったら決め直す |
| `$main_dir` | メインディレクトリの絶対パス | `wt_main_dir`（[SKILL.md](../SKILL.md) の手順 1） |

**続けて実行するときは 1 つの bash ブロックへまとめる。** 先頭で 1 度決めれば、後続のコマンドは決め直さずに済む（推奨であり、1 コマンドずつ実行してもよい）。

## 作る

```bash
bash "$SCRIPTS/worktree-setup.sh" init
```

`/ndf:worktree` を起動すると、手順 0 としてこれが走る。既にあるファイルは上書きしない。

## 最小の設定

編集時の案内だけを使うなら、これで足りる。手で書くなら次の内容になる。

```json
{
  "$schema": "https://raw.githubusercontent.com/devbasex/ai-plugins/main/plugins/ndf/skills/worktree/schemas/worktree.schema.json",
  "version": 1
}
```

`version` だけを書くと、案内を出さないパスは組み込みの既定を使う。

| パス | 扱う内容 |
| --- | --- |
| `issues/` | 計画と仕様の草案 |
| `docs/` | リポジトリ知識 |
| `.claude/` `.codex/` `.kiro/` `.agents/` | 各ランタイムの設定 |
| `.serena/` | コードインテリジェンスの設定と索引 |
| `.ndf/` | この設定ファイル |
| `.gitignore` | worktree の登録そのものに必要 |

差し替えるときは `guard.allow_paths` を書く。**空の配列は「何も許可しない」という
指定になる**（既定へは戻らない）。

```json
{
  "version": 1,
  "guard": { "allow_paths": ["issues/", "notes/", ".gitignore"] }
}
```

## ツールが書き換えるファイルを追跡しているリポジトリ

CLI が起動したツール（Serena MCP など）は、利用者の操作なしに worktree の中のファイルを書き換える。
このパスを「ツールのパス」と呼び、cross-review・cross-refactoring・`pr` の手順は検査とコミットの
対象から外す。既定は `.serena/project.yml` と `.serena/serena_config.yml` の 2 つである。

| 工程 | 扱い |
| --- | --- |
| cross-review のレビュー worktree・cross-refactoring の書き込み用の作業ディレクトリ | 作った後に `git update-index --skip-worktree` を掛ける。HEAD を動かす前に外して HEAD の内容へ戻し、動かした後に掛け直す |
| ラウンドの開始・同期の前の検査 | ツールのパスの変更だけでは止まらない。外したパスを `↷ ツールのパスを検査から外した:` の 1 行で出す |
| push の直前 | 送るコミットにツールのパスがあれば push しない（追跡対象外のツールのパスは `.gitignore` へ入れる） |
| `pr-steps.py plan` / `commit` | `plan` は `tool_paths` の項目に分けて数え、`commit` は index から外す（ファイルの中身は残す）。意図した変更は `git commit` で直接コミットする |

足すときは `tool_paths.add`、既定から外すときは `tool_paths.remove` を書く。末尾が `/` の項目は
前方一致、それ以外は完全一致である。読むのは検査する worktree の直下のこのファイルだけで、
レビュー worktree では Pull Request の head の設定になる。`tool_paths` があるのに読めない
（JSON が壊れている・`version` が 1 でない・型が違う）ときは、既定へ戻さずに止まる。

```json
{
  "version": 1,
  "tool_paths": { "add": [".idea/workspace.xml"], "remove": [".serena/project.yml"] }
}
```

## 開発の起点が既定ブランチと違うリポジトリ

既定ブランチに正式版を置き、開発の本流を別のブランチに置く構成では、`base_branch` を書く。
worktree の起点がこのブランチになる（`follow_branch: true` のときはメインディレクトリの追従先にもなる）。

```json
{
  "version": 1,
  "base_branch": "develop"
}
```

書かなければ `origin` の HEAD が指すブランチ（既定ブランチ）が起点になる。**書いた名前が
origin にもローカルにも無いときは、既定ブランチへは落ちず、解決が失敗する。** 落とすと、
開発の変更が正式版から分岐したまま進む。

## ブランチ運用を宣言するリポジトリ

スプリントをどのブランチから切り、本番チャネルへ何をどう入れるかを `branch_flow` に書く。
書かなければ既定値の行（ベースブランチから切り、設計 PR をベースブランチへ出し、ベースブランチを
丸ごと本番チャネルへ出す）で動く。ブランチ名は写さず、`base_branch`・`production_branch` をチャネルで指す。

| キー | 値 | 既定 | 意味 |
| --- | --- | --- | --- |
| `start` | `base` / `production` | `base` | スプリントの起点。worktree の `create` の既定の起点もこれになる |
| `integrate` | `check` | `check` | スプリントブランチをベースブランチへ入れる時点（検査の Pull Request のマージ） |
| `doc_prs` | `base` / `sprint` | `base` | 設計 PR と確定仕様化の PR の宛先 |
| `promotion` | `whole` / `selected` | `whole` | 本番への取り込み方 |

取り込み方ごとに許す組み合わせは決まっている。

| `promotion` | 許す `start` | 許す `doc_prs` | リリースの範囲 |
| --- | --- | --- | --- |
| `whole` | `base` | `base` | 本番チャネル..提示したベースブランチのコミット |
| `selected` | `production` | `sprint` | 前の本番のタグ..リリースブランチ（本番チャネルから切り、選んだスプリントブランチを集める）の先頭 |

選んで集める取り込み方の例:

```json
{
  "version": 1,
  "base_branch": "develop",
  "production_branch": "main",
  "branch_flow": {
    "start": "production",
    "integrate": "check",
    "doc_prs": "sprint",
    "promotion": "selected"
  }
}
```

- 組み合わせが表に合わない・知らないキー・表に無い値は、読む側（`supervise.py new sprint` など）が
  直すキーと許す値を出して終了コード 2 で止まる。既定値の行へは倒さない
- スプリントを始めるときは、`branch_flow` をメインディレクトリの設定から読む（`production_branch` と同じ）
- スプリントに属さないリリース（並列の計画のまとめたリリース・`supervise.py new release`）は、本番チャネル宛の Pull Request の検査と同じく
  本番チャネルの宣言（`origin/<本番チャネル>` の `.ndf/worktree.json`）の `branch_flow` で組む。ベースブランチの
  Pull Request だけではリリースの範囲の決め方を変えられない。本番チャネルの宣言を読めなければ宣言の無いリポジトリと
  同じく組み、`branch_flow` が壊れていれば止まる。ブランチの名前は、どちらでもメインディレクトリの設定から解く
- 変えた宣言は、それから始めるスプリントにだけ効く。動いているスプリントは、開始時に目録（`sprint.json`）の
  `ブランチ運用` へ書いた値のまま進む
- 既存のリポジトリの使い方から案を作るときは `project-decl.py measure` を打ち、宣言と実際の使い方のずれは
  `project-decl.py drift` で見る（`development-workflow` の `references/project-analysis.md`）

### レビューの単位

どの PR に何のレビューを掛けるかを、`branch_flow.reviews` に PR の種類ごとに書く。書かない種類は既定値で動く。

| キー | 値 | 意味 |
| --- | --- | --- |
| `reviews.<種類>` | `impl`（実装 PR）・`sprint`（スプリントの PR）・`release`（リリースブランチの PR）・`design`（設計 PR）・`spec`（確定仕様化の PR） | PR の種類 |
| `reviews.<種類>.checks` | `cross-refactoring`・`cross-review`・`behavior`（振る舞いの確認）の重複の無い並び | 掛けるレビュー。並びの順は意味を持たず、ステップは常にリファクタリング → cross-review → 振る舞いの確認の順に置く |
| `reviews.<種類>.diff` | `pr`（PR の差分全体）・`unchecked`（未検査の差分）・`resolution`（衝突の解消の差分）・`none`（掛けない） | 対象の差分 |

種類ごとに許す値:

| 種類 | 許す `checks` | 許す `diff` | 書ける条件 |
| --- | --- | --- | --- |
| `impl` | 3 つすべて | `pr` / `none` | 常に |
| `sprint` | 3 つすべて | `unchecked` / `none` | 常に |
| `release` | `cross-review` | `pr` / `resolution` / `none` | `promotion` が `selected` のとき |
| `design` | `cross-review` | `pr` / `none` | 常に |
| `spec` | `cross-review` | `pr` / `none` | 常に |

書かない種類の既定値は、課題のまとまりの記録（並列の計画のレーンのまとまり）の有無で決まる。

| 種類 | まとまりの記録あり | まとまりの記録なし |
| --- | --- | --- |
| `impl` | `cross-review` / `pr` | 掛けない |
| `sprint` | `cross-refactoring`・`behavior` / `unchecked` | 3 つすべて / `unchecked` |
| `release` | 掛けない | 掛けない |
| `design` | `cross-review` / `pr` | `cross-review` / `pr` |
| `spec` | 掛けない | 掛けない |

成り立たない組み合わせは、読む側（`supervise.py new sprint`・`project-decl.py check / write`）が直すキーと理由を
1 件 1 行で出して終了コード 2 で止まる。

- `diff` が `none` なのに `checks` を書いた・`none` 以外なのに `checks` が空
- `promotion` が `whole` なのに `release` を書いた
- 実装の差分がどの PR でも cross-review されない。実装の差分を覆うのは `impl` の `pr`・`sprint` の `unchecked`・
  `release` の `pr` の 3 つで、`release` の `resolution` は衝突の解消の差分しか見ないため数えない。書かない種類を
  既定値で埋め、まとまりの記録の有無の両方で確かめる
- 書かない種類の既定値が、書いた行と同じ実装の差分を cross-review する（同じ差分を 2 回レビューする）。
  キーは書かない種類（`branch_flow.reviews.<種類>`）で、その種類を書いて cross-review を掛けるかを決める。
  両方の行に書いた重なりは意図したものとして通す

リファクタリングを止める条件（CI テストの宣言の無いリポジトリ・リファクタリングの無いモード）は宣言より先に効き、
`cross-refactoring` を選んでも外れる。

実装 PR で cross-review し、リリースブランチの PR では衝突の解消だけをレビューする例:

```json
{
  "version": 1,
  "base_branch": "develop",
  "production_branch": "main",
  "branch_flow": {
    "start": "production",
    "integrate": "check",
    "doc_prs": "sprint",
    "promotion": "selected",
    "reviews": {
      "impl": {"checks": ["cross-review"], "diff": "pr"},
      "sprint": {"checks": ["cross-refactoring", "behavior"], "diff": "unchecked"},
      "release": {"checks": ["cross-review"], "diff": "resolution"},
      "design": {"checks": ["cross-review"], "diff": "pr"},
      "spec": {"checks": [], "diff": "none"}
    }
  }
}
```

- リリースブランチの PR のレビューは、開発版のデプロイのプランが承認資料より前に掛ける。`release-steps.py
  review-open` が一時の PR（衝突の解消のレビューの PR）を組み、レビューの直しは `review-close` がリリースブランチへ
  移す（`release` の `references/release-steps.md`）。衝突したマージが無ければレビューを打たずに進む
- 動いているスプリントは、開始時に目録（`sprint.json`）の `ブランチ運用` の `reviews` へ書いた値のまま進む

## メインディレクトリのブランチを稼働中の worktree へ追従させる

**セッション開始 hook は、既定ではメインディレクトリの HEAD を動かさない。** 並列に動く
エージェントのどれが開始・再開しても、他の担当が読んでいるメインディレクトリの内容が入れ替わらない
ためである。1 人が 1 つの worktree で作業し、メインディレクトリを開いたエディタでその内容を見たい
ときは `follow_branch` を書く。

```json
{
  "version": 1,
  "follow_branch": true
}
```

| 項目 | 型 | 既定 | 意味 |
| --- | --- | --- | --- |
| `follow_branch` | boolean | `false` | `true` のときだけ、セッション開始 hook がメインディレクトリを稼働中の worktree へ追従させる。真偽値以外（`"true"` / `1` / `null`）は `false` と同じ |

有効にしたときの追従先は [SKILL.md](../SKILL.md) の「メインディレクトリのブランチ」にある。
worktree が 1 つならそのブランチのコミットへ、0 個または複数なら `base_branch`（無ければ既定
ブランチ）へ detached HEAD で合わせる。メインディレクトリに未コミットの変更があるときは追従しない。

## ローカル環境を持つリポジトリ

画面を触って動作を確かめるサービス一式があるなら、`localenv` を足す。

```json
{
  "version": 1,
  "localenv": {
    "kind": "compose",
    "layout": "indirect",
    "compose_files": ["docker-compose.dev.yml"],
    "app_service": "app",
    "src_target": "/src",
    "copy_from_main": ["vendor", "node_modules", "public/build", ".env"],
    "copy_as_real": ["vendor/composer"],
    "build_before_aim": ["npm ci", "npm run build"],
    "reload_signal": { "process": "php-fpm", "signal": "USR2" },
    "branch_probe": "curl -sI http://localhost/ | sed -n 's/^x-worktree: //p'",
    "isolated_services": ["app", "nginx"],
    "isolate_when": ["database/migrations/**", "docker/**", "docker-compose*.yml", "Dockerfile*"],
    "healthcheck": "curl -sf http://localhost/health"
  }
}
```

### 項目の決め方

| 項目 | 決め方 |
| --- | --- |
| `kind` | `compose` 以外は未対応。書かなければ `localenv` の仕組みは動かない |
| `layout` | コンテナ内のコードの置かれ方。[`local-environment.md`](local-environment.md) の「型を見分ける」で判定する |
| `src_target` | コンテナ内でコードが置かれる位置。`layout` が `indirect` のとき要る |
| `copy_from_main` | 追跡されないが動作に要るもの。依存物・ビルド成果物・環境ファイル |
| `copy_as_real` | `copy_from_main` の中で、実行中に書き換えられるもの。ハードリンクだとメインディレクトリ側も変わる |
| `branch_probe` | ローカル環境に載っているブランチ名を**標準出力へ 1 行で**返すコマンド。応答ヘッダやログへブランチ名を出す仕掛けを入れて読む |
| `isolate_when` | 分離モードを促す変更パス。シェルのパターンで書く。`*` はパス区切りも越える |
| `healthcheck` | 照合が通ったときだけ実行される。終了コードがそのまま返る |

`branch_probe` は**照合の要になる**。返す値が worktree のブランチ名と一致するかで、
ローカル環境に載っているコードを判定する。値を返せないとき（ローカル環境が動いていない、仕掛けが
入っていない）は「未起動または適用外」として扱われ、「不一致」とは区別される。

## 依存物を worktree へ用意するリポジトリ

pre-commit やテストが `vendor/`・`node_modules/`・`.env` などの追跡されないファイルを使うなら、`deps` を足す。
NDF が worktree を作った直後に、`copy_from_main`・`copy_as_real`・`run` の順に用意する（`worktree-deps.sh prepare`）。

```json
{
  "version": 1,
  "deps": {
    "copy_from_main": ["vendor", "node_modules"],
    "copy_as_real": [".env", "vendor/composer/autoload_real.php"],
    "run": ["uv sync --frozen"]
  }
}
```

| 項目 | 決め方 |
| --- | --- |
| `copy_from_main` | メインディレクトリからハードリンクで複製するパス（使えない配置では実体で複製する）。**worktree の中でその場で書き換えないパスだけを置く。** 書き換えるとメインディレクトリの同じファイルも変わる |
| `copy_as_real` | 実体で複製するパス。worktree の中で書き換えるもの（`.env`・自動読み込みの生成物）を置く。`copy_from_main` の内側を指してよい |
| `run` | 複製の後に、worktree を作業ディレクトリにして `sh -c` で順に走らせるコマンド。1 つでも 0 以外なら止まる |

- パスは worktree の根からの相対パスで書く。`..` と絶対パスは断る
- **複製元がメインディレクトリに無ければ失敗にする。** 依存物が無いまま「済み」と出すと、依存物に頼る検査が黙って飛ぶ
- 依存物は git が無視するパスに置く。用意の前後で `git status --porcelain` が変わると失敗にする。`run` が lock
  ファイルを書き換えた場合も同じである
- `run` がハードリンクで複製したファイルをその場で書き換えると失敗にする（そのパスは `copy_as_real` へ移す）
- **`deps` は個人設定から反映しない。** 走らせるコマンドは、レビューを通る共有設定に書いたものだけである
- `deps` が無いか空なら、worktree を作る処理は今までと同じ動きをする
- 設定が JSON として読めない・`deps` が object でない・3 つの項目が文字列の配列でないときは、何もせずに
  設定のファイルと誤りの箇所を出して終了コード 3 で終わる

NDF は依存の用意の設定を推測で書かない。同じリポジトリでも、使う人の環境で正しい手段（`composer install` が
使えるか、メインディレクトリから複製するか）が変わるためである。

## テスト実行を分けるリポジトリ

worktree ごとにテスト環境を立てるなら、`testenv` を足す。

```json
{
  "version": 1,
  "testenv": {
    "port_band": [20000, 29999],
    "port_roles": { "http": 0, "db": 1, "mail": 2, "object": 4, "search": 6 },
    "profiles": { "core": ["app", "mysql"], "browser": ["app", "mysql", "nginx"] },
    "shared_network": "",
    "discard_volumes": true,
    "golden_tag_paths": ["database/migrations", "database/seeders"],
    "golden_volumes": { "sail-mysql": "ndf-golden-mysql" },
    "test_kinds": {
      "pure":     { "select": "...", "run": "..." },
      "stateful": { "select": "...", "run": "...", "skip_reset": { "TEST_SKIP_MIGRATE_FRESH": "true" } },
      "browser":  { "run": "...", "base_url_env": "PWK_BASE_URL", "port_role": "http", "out_env": "PWK_OUT_DIR" }
    },
    "expose": {
      "enabled": false,
      "public_tag": "golden-public",
      "base_domain": "",
      "ttl": "8h",
      "open_command": "<公開の口を開けるコマンド>",
      "close_command": "<公開の口を閉じるコマンド>"
    }
  }
}
```

| 項目 | 決め方 |
| --- | --- |
| `port_band` | 他の用途と重ならない帯。スロット 1 つあたり 20 番を使う |
| `port_roles` | 役割ごとの番号。ポートは `帯の下限 + スロット*20 + 役割番号` |
| `discard_volumes` | `false` のとき、`worktree-testenv.sh down` がテスト環境のボリュームを残す。**無しと `true` では消し、消えたデータは戻せない。** 作り直せないデータを持つプロジェクトは `false` を書く。真偽値以外の値は残す側に倒す。メインディレクトリの宣言を読む |
| `golden_tag_paths` | データ構造を定める資産。**内容が同じなら基準を焼き直さない** |
| `test_kinds` | 種類ごとの選別と実行。**書かなければテスト実行の仕組みは何もせずに終わる** |
| `skip_reset` | 初期化を抑止する環境変数。渡さないと最初のテストが全体を作り直す構成がある |
| `port_role` | エントリポイントの URL を組み立てるときに使う `port_roles` の役割名。既定は `http` |
| `service` | その種類のテストを走らせるコンテナのサービス名。書くと、走らせる前にそのコンテナが worktree を見ているかを確かめ、見ていなければ走らせずに 1 で終わる（[test-execution.md](test-execution.md) の到達の確認） |
| `expose.enabled` | **既定は無効。** マスク済みデータが整い、明示的に有効化したときだけ公開する |
| `expose.open_command` | 公開のエンドポイントを開けるコマンド。**設定が無ければ公開しない。** `NDF_EXPOSE_URL` / `NDF_EXPOSE_HOST` / `NDF_EXPOSE_ENVIRONMENT` / `NDF_EXPOSE_SLOT` が渡る |

worktree レジストリの定義は [`../schemas/registry.schema.json`](../schemas/registry.schema.json) にある。

## 機械ごとに違う値は個人設定へ書く

ポートの帯や持ち込み物は機械ごとに違う。**共有設定を書き換えると、各自の値が差分に
載る。** 個人の値は `.ndf/worktree.local.json` へ書く。

| ファイル | 追跡 | 何を書くか |
| --- | --- | --- |
| `.ndf/worktree.json` | する | リポジトリの運用。clone した全員で同じ値のまま残る |
| `.ndf/worktree.local.json` | **しない** | 各自の機械に依存する値 |

```json
{
  "version": 1,
  "testenv": { "port_band": [40000, 40999], "port_roles": { "db": 5 } },
  "localenv": { "copy_from_main": ["node_modules"] },
  "follow_branch": true
}
```

`version` は共有設定と同じく必須で、値は `1` である。追跡から外す登録は
`.ndf/.gitignore` が持ち、`worktree-setup.sh init` が共有設定と一緒に作る。**既に
設定があるリポジトリでは作られない。** `worktree-setup.sh status` が登録の無い状態を
1 行で伝えるので、その行が出たら `.ndf/.gitignore` へ `worktree.local.json` を足す。

### 上書きできる項目

| 項目 | 型 | 重ね方 |
| --- | --- | --- |
| `localenv` | オブジェクト | 共有の `localenv` へ深く併合する |
| `testenv` | オブジェクト | `expose` を除いて、共有の `testenv` へ深く併合する |
| `follow_branch` | 真偽値 | 共有の値を置き換える |

**この 3 つ以外は反映しない。** `base_branch` / `production_branch` / `guard` と未知の
項目は、書いても共有設定の値が使われる。個人設定は追跡されずレビューを通らないため、
リポジトリの運用を個人が変えると、手元の動作と継続的統合の判定が食い違う。
`testenv.expose` も反映しない。追跡されないファイルから外部への公開を有効にできる状態を
作らないためである。 **`expose` を落として空になった `testenv` は節ごと反映しない。**
空の節を重ねると、`testenv` を設定していないリポジトリで試験環境の仕組みが有効に変わる。

### 重ね合わせの規則

| 規則 | 内容 |
| --- | --- |
| オブジェクトは深く併合する | `port_roles` の役割を 1 つだけ変えても、他の役割は共有の値のまま残る |
| **配列は置き換える** | `copy_from_main` に `["node_modules"]` と書くと、共有の要素は残らない。継ぎ足す形にすると、共有の要素を個人が外せない |
| `null` は反映しない | 個人設定から共有の節を消せる形にしない。消せると、仕組みが手元でだけ黙って止まる |
| 型が合わない項目は、その項目だけ落ちる | `testenv` がオブジェクトでない・`follow_branch` が真偽値でないときは、その項目だけを無視して他は反映する |

### 読めないときの扱い

個人設定が読めない（JSON として壊れている・空・最上位が配列・`version` が `1` でない・
ディレクトリ）ときは、**共有設定だけで動く。** hook もコマンドも止まらない。
共有設定が無い・読めないときは、個人設定を使わない。

気づけるよう、置いた直後に状態を確かめる。

```bash
bash "$SCRIPTS/worktree-setup.sh" status
```

「個人の宣言:」の行が状態を、「個人の宣言で反映しない項目:」の行が無視した項目名を出す。

## 確かめる

```bash
WT="$main_dir/.worktrees/<ブランチ名>"

# 設定が読めているか（読めなければ何も出ない）
bash "$SCRIPTS/worktree-localenv.sh" mode "$WT"

# 照合の状態（0 一致 / 1 不一致 / 2 未起動または適用外）
bash "$SCRIPTS/worktree-localenv.sh" verify "$WT"; echo $?
```

対象の worktree は引数で渡す。省略すると現在地が対象になる。

設定に誤りがあっても作業は止まらない。読めない設定は無いものとして扱われ、
何も出力せずに終わる。誤りに気づけるよう、置いた直後に上のコマンドで確かめる。

## 互換性の規則

`version` を上げるのは、既存の項目の意味を変えるときに限る。項目の追加では上げない。
読み取り側は知らない項目を無視する。
