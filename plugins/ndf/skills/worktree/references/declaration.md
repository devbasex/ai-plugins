# 宣言ファイルの書き方

worktree 運用の仕組みは、リポジトリごとの差を `.ndf/worktree.json` から読む。
**このファイルが無いリポジトリでは、すべての仕組みが何もせずに終わる。**

定義は [`../schemas/worktree.schema.json`](../schemas/worktree.schema.json) にある。
`$schema` を書いておくと編集時に補完が効く。読み取り側はこの項目を参照しない。

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

## 最小の宣言

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
| `.ndf/` | この宣言ファイル |
| `.gitignore` | worktree の登録そのものに必要 |

差し替えるときは `guard.allow_paths` を書く。**空の配列は「何も許可しない」という
指定になる**（既定へは戻らない）。

```json
{
  "version": 1,
  "guard": { "allow_paths": ["issues/", "notes/", ".gitignore"] }
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

## メインディレクトリのブランチを稼働中の worktree へ追従させる

**セッション開始時の hook は、既定ではメインディレクトリの HEAD を動かさない。** 並列に動く
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
| `follow_branch` | boolean | `false` | `true` のときだけ、セッション開始時の hook がメインディレクトリを稼働中の worktree へ追従させる。真偽値以外（`"true"` / `1` / `null`）は `false` と同じ |

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
| `golden_tag_paths` | データ構造を定める資産。**内容が同じなら基準を焼き直さない** |
| `test_kinds` | 種類ごとの選別と実行。**書かなければテスト実行の仕組みは何もせずに終わる** |
| `skip_reset` | 初期化を抑止する環境変数。渡さないと最初のテストが全体を作り直す構成がある |
| `port_role` | 入口の URL を組み立てるときに使う `port_roles` の役割名。既定は `http` |
| `expose.enabled` | **既定は無効。** マスク済みデータが整い、明示的に有効化したときだけ公開する |
| `expose.open_command` | 公開の口を開けるコマンド。**宣言が無ければ公開しない。** `NDF_EXPOSE_URL` / `NDF_EXPOSE_HOST` / `NDF_EXPOSE_ENVIRONMENT` / `NDF_EXPOSE_SLOT` が渡る |

台帳の定義は [`../schemas/registry.schema.json`](../schemas/registry.schema.json) にある。

## 機械ごとに違う値は個人の宣言へ書く

ポートの帯や持ち込み物は機械ごとに違う。**共有の宣言を書き換えると、各自の値が差分に
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

`version` は共有の宣言と同じく必須で、値は `1` である。追跡から外す登録は
`.ndf/.gitignore` が持ち、`worktree-setup.sh init` が共有の宣言と一緒に作る。**既に
宣言があるリポジトリでは作られない。** `worktree-setup.sh status` が登録の無い状態を
1 行で伝えるので、その行が出たら `.ndf/.gitignore` へ `worktree.local.json` を足す。

### 上書きできる項目

| 項目 | 型 | 重ね方 |
| --- | --- | --- |
| `localenv` | オブジェクト | 共有の `localenv` へ深く併合する |
| `testenv` | オブジェクト | `expose` を除いて、共有の `testenv` へ深く併合する |
| `follow_branch` | 真偽値 | 共有の値を置き換える |

**この 3 つ以外は反映しない。** `base_branch` / `production_branch` / `guard` と未知の
項目は、書いても共有の宣言の値が使われる。個人の宣言は追跡されずレビューを通らないため、
リポジトリの運用を個人が変えると、手元の動作と継続的統合の判定が食い違う。
`testenv.expose` も反映しない。追跡されないファイルから外部への公開を有効にできる状態を
作らないためである。 **`expose` を落として空になった `testenv` は節ごと反映しない。**
空の節を重ねると、`testenv` を宣言していないリポジトリで試験環境の仕組みが有効に変わる。

### 重ね合わせの規則

| 規則 | 内容 |
| --- | --- |
| オブジェクトは深く併合する | `port_roles` の役割を 1 つだけ変えても、他の役割は共有の値のまま残る |
| **配列は置き換える** | `copy_from_main` に `["node_modules"]` と書くと、共有の要素は残らない。継ぎ足す形にすると、共有の要素を個人が外せない |
| `null` は反映しない | 個人の宣言から共有の節を消せる形にしない。消せると、仕組みが手元でだけ黙って止まる |
| 型が合わない項目は、その項目だけ落ちる | `testenv` がオブジェクトでない・`follow_branch` が真偽値でないときは、その項目だけを無視して他は反映する |

### 読めないときの扱い

個人の宣言が読めない（JSON として壊れている・空・最上位が配列・`version` が `1` でない・
ディレクトリ）ときは、**共有の宣言だけで動く。** hook もコマンドも止まらない。
共有の宣言が無い・読めないときは、個人の宣言を使わない。

気づけるよう、置いた直後に状態を確かめる。

```bash
bash "$SCRIPTS/worktree-setup.sh" status
```

「個人の宣言:」の行が状態を、「個人の宣言で反映しない項目:」の行が無視した項目名を出す。

## 確かめる

```bash
WT="$main_dir/.worktrees/<ブランチ名>"

# 宣言が読めているか（読めなければ何も出ない）
bash "$SCRIPTS/worktree-localenv.sh" mode "$WT"

# 照合の状態（0 一致 / 1 不一致 / 2 未起動または適用外）
bash "$SCRIPTS/worktree-localenv.sh" verify "$WT"; echo $?
```

対象の worktree は引数で渡す。省略すると現在地が対象になる。

宣言に誤りがあっても作業は止まらない。読めない宣言は無いものとして扱われ、
何も出力せずに終わる。誤りに気づけるよう、置いた直後に上のコマンドで確かめる。

## 互換性の規則

`version` を上げるのは、既存の項目の意味を変えるときに限る。項目の追加では上げない。
読み取り側は知らない項目を無視する。
