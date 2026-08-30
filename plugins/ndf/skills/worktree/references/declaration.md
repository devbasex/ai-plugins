# 宣言ファイルの書き方

作業ツリー運用の仕組みは、リポジトリごとの差を `.ndf/localenv.json` から読む。
**このファイルが無いリポジトリでは、すべての仕組みが何もせずに終わる。**

定義は [`../schemas/localenv.schema.json`](../schemas/localenv.schema.json) にある。
`$schema` を書いておくと編集時に補完が効く。読み取り側はこの項目を参照しない。

## 最小の宣言

編集時の案内だけを使うなら、これで足りる。

```json
{
  "$schema": "https://raw.githubusercontent.com/devbasex/ai-plugins/main/plugins/ndf/skills/worktree/schemas/localenv.schema.json",
  "version": 1
}
```

`version` だけを書くと、案内を出さないパスは組み込みの既定を使う。

| パス | 扱う内容 |
| --- | --- |
| `issues/` | 計画と仕様の草案 |
| `docs/` | リポジトリ知識 |
| `.claude/` `.codex/` `.kiro/` `.agents/` `.gemini/` | 各ランタイムの設定 |
| `.serena/` | コードインテリジェンスの設定と索引 |
| `.ndf/` | この宣言ファイル |
| `.gitignore` | 作業ツリーの登録そのものに必要 |

差し替えるときは `guard.allow_paths` を書く。**空の配列は「何も許可しない」という
指定になる**（既定へは戻らない）。

```json
{
  "version": 1,
  "guard": { "allow_paths": ["issues/", "notes/", ".gitignore"] }
}
```

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
| `copy_as_real` | `copy_from_main` の中で、実行中に書き換えられるもの。ハードリンクだと主ディレクトリ側も変わる |
| `branch_probe` | 環境に載っているブランチ名を**標準出力へ 1 行で**返すコマンド。応答ヘッダやログへブランチ名を出す仕掛けを入れて読む |
| `isolate_when` | 分離モードを促す変更パス。シェルのパターンで書く。`*` はパス区切りも越える |
| `healthcheck` | 照合が通ったときだけ実行される。終了コードがそのまま返る |

`branch_probe` は**照合の要になる**。返す値が作業ツリーのブランチ名と一致するかで、
環境に載っているコードを判定する。値を返せないとき（環境が動いていない、仕掛けが
入っていない）は「未起動または適用外」として扱われ、「不一致」とは区別される。

## 確かめる

```bash
# 宣言が読めているか（読めなければ何も出ない）
bash "$NDF_SCRIPTS/worktree-localenv.sh" mode

# 照合の状態（0 一致 / 1 不一致 / 2 未起動または適用外）
bash "$NDF_SCRIPTS/worktree-localenv.sh" verify; echo $?
```

宣言に誤りがあっても作業は止まらない。読めない宣言は無いものとして扱われ、
何も出力せずに終わる。誤りに気づけるよう、置いた直後に上のコマンドで確かめる。

## 互換性の規則

`version` を上げるのは、既存の項目の意味を変えるときに限る。項目の追加では上げない。
読み取り側は知らない項目を無視する。
