# 進行を盤面へ記録する

工程の進行を GitHub Projects の盤面へ残す。セッションが変わっても、いまどの工程にいるか・
どのモードで判定したか・対応する作業ツリーと計画ファイルがどれかを引き継げる。

**この仕組みは任意である。** リポジトリに `.ndf/projects.json` が無ければ、呼び出しは何も
出力せず終了コード 0 で終わる。盤面が使えない環境でも工程はそのまま通る。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 盤面 | GitHub Projects v2 のプロジェクト 1 つ |
| アイテム | 盤面に載る 1 行。1 つの issue に対応する |
| 宣言 | リポジトリの `.ndf/projects.json`。これが無ければ何も動かない |

## 宣言ファイル

```json
{
  "version": 1,
  "owner": "devbasex",
  "number": 1
}
```

| キー | 内容 |
| --- | --- |
| `version` | 宣言の形式。現在は `1` のみ。違う値は読まない |
| `owner` | 盤面を持つ組織または利用者のログイン名 |
| `number` | 盤面の番号（`https://github.com/orgs/<owner>/projects/<number>`） |
| `fields` | 省略可。盤面のフィールド名を差し替える（後述） |

`owner` と `number` のどちらかが欠けていると、宣言は無効として扱う。盤面を特定できない
まま推測で書き込むと、別の盤面を更新しかねない。

**宣言はコミットする。** リポジトリの設定であり、同じ運用を他の開発者にも適用する。

## 盤面に要るフィールド

| キー | 既定の名前 | 種類 | 値 |
| --- | --- | --- | --- |
| `stage` | `進行` | 単一選択 | 工程表の行名（下の対応表） |
| `mode` | `モード` | 単一選択 | `light` / `standard` / `architecture` / `legacy-refactor` |
| `worktree` | `作業ツリー` | 文字列 | `.worktrees/<ブランチ名>` |
| `plan` | `計画ファイル` | 文字列 | `issues/<ファイル名>` |
| `status` | `Status` | 単一選択 | `Todo` / `In Progress` / `Done`（GitHub の既定） |

`Linked pull requests` と `Repository` は GitHub が最初から持つ。1 つの issue から複数の
Pull Request が出る場合も、既定の `Linked pull requests` に並ぶため対応付けを新しく作らない。

名前を変えたい場合は宣言で差し替える。

```json
{ "version": 1, "owner": "devbasex", "number": 1,
  "fields": { "stage": "Stage", "plan": "Plan file" } }
```

## 工程と値の対応

**値は工程表の行名と一致させる。** 綴りの違う値を書き込むと、盤面の側に工程表に無い値が増える。
スクリプトは一覧に無い値を弾く。

| 工程表の行 | 記録する Skill | `stage` の値 |
| --- | --- | --- |
| 作業場所の用意 | `worktree` | `作業場所の用意` |
| 要求と受け入れ条件 | `requirements-design` | `要求と受け入れ条件` |
| 設計 | （専用 Skill なし。`implementation-plan` に記録する） | `設計` |
| 計画 | `implementation-plan` | `計画` |
| 実装 | `tdd-cycle` | `実装` |
| 構造改善 | `refactoring` | `構造改善` |
| レビュー | `cross-review` / `pr-review` | `レビュー` |
| 完了判定 | `quality-gates` | `完了判定` |
| Pull Request | `pr` | `Pull Request` |
| 確定仕様化 | `plan-to-spec` | `確定仕様化` |
| 後片付け | `merged` | `後片付け` |
| 配布 | `release` | `配布` |
| リリース後テスト | `release-verification` | `リリース後テスト` |
| 振り返り | `retrospective` | `振り返り` |

## 呼び方

```bash
bash "$SCRIPTS/projects-sync.sh" <issue番号> <キー> <値>
```

```bash
bash "$SCRIPTS/projects-sync.sh" 186 stage レビュー
bash "$SCRIPTS/projects-sync.sh" 186 mode standard
bash "$SCRIPTS/projects-sync.sh" 186 worktree .worktrees/fix/issue-186
bash "$SCRIPTS/projects-sync.sh" 186 plan issues/issue-186.md
```

### `$SCRIPTS` を決める

プラグインの `scripts/` の位置はランタイムで変わる。候補を順に試し、最初に当たったものを
絶対パスで採る。

```bash
# Claude Code は SKILL.md 内の ${CLAUDE_PLUGIN_ROOT} をプラグインルートの絶対パスへ置き換えて
# から渡す。シングルクォートで囲むのは、置き換えられなかったときにシェルへ展開させないため
# である。Codex と Kiro CLI は置き換えない。
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'
case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac
SCRIPTS=
for candidate in \
  ${PLUGIN_ROOT:+"$PLUGIN_ROOT/scripts"} \
  ".kiro/skills/../scripts" \
  "plugins/ndf/scripts"
do
  [ -f "$candidate/projects-sync.sh" ] || continue
  SCRIPTS="$(cd "$candidate" && pwd)"
  break
done
[ -n "$SCRIPTS" ] || SCRIPTS=  # 見つからなければ記録を飛ばす
```

見つからない場合は記録を飛ばす。**進行管理が理由で工程を止めない。**

## 何もしない条件

次のいずれでも、何も出力せず終了コード 0 で終わる。

- `.ndf/projects.json` が無い
- `gh` または `jq` が無い
- 盤面への問い合わせや更新が失敗した（権限不足を含む。`project` スコープが要る）

**呼び出し側の誤りだけは 2 を返す。** 知らないキー・工程表に無い値・引数の不足がこれにあたる。
黙って進むと、綴りの違う値が盤面へ入るか、書き込んだつもりの値が入らない。

## 既知の制約

`gh project item-list --format json` が返す JSON は、フィールド名を小文字化する際に
**日本語のフィールド名の先頭 1 文字を壊す**（`進行` が `���行` になる）。値は壊れない。

```console
$ gh project item-list 1 --owner devbasex --format json | jq '.items[0] | keys'
[ "content", "id", "labels", "repository", "status", "title", "___ード", "___行" ]
```

このため、**盤面から現在の値を読み返す用途にこの出力を使わない**。フィールドの名前と
識別子は `gh project field-list` から取る（そちらは壊れない）。書き込みだけを行う
`projects-sync.sh` はこの制約を踏まない。
