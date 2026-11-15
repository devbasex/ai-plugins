# 宣言と入出力の規約

<!-- ndf check --write-spec が生成する。手で直さない（正本は各節の出典） -->

NDF を外から呼ぶ面（宣言・副命令・環境変数・受け渡しのファイル・hook・Skill・起動スクリプト）の規約である。機械が読む形は JSON Schema（`contracts/schemas/declarations/`・`contracts/schemas/io/`）で、このページの表はそれと正本の表から生成する。

**公開**の項目は外の呼び手（連携システム）が頼ってよく、名前と意味を版で約束する。**内部**の項目は NDF の中の Skill・エージェント・AI が使い、予告なく変えてよい。公開か内部かは、公開する面の表（`contracts/domains/entrypoints.json` と、`contracts/domains/ndf-files.json` の列 `reader`）に当たるかで決まる

## ページ

| ページ | 件数 |
| --- | --- |
| [宣言（.ndf/）](declarations.md) | 公開 18・内部 1 |
| [副命令の引数と結果](commands.md) | 公開 0・内部 27 |
| [終了コードと止まった理由](exit-codes.md) | 終了コード 11・止まった理由 32 |
| [環境変数](env.md) | 公開 10・内部 50 |
| [受け渡しのファイル](files.md) | 公開 12・内部 55 |
| [hook の入出力](hooks.md) | 公開 6・内部 11 |
| [Skill と起動スクリプト](skills.md) | 公開 1・内部 48 |

## 規約の置き場（公開する面の表の種類 spec）

| 行 | 置き場 | 公開する宣言 |
| --- | --- | --- |
| `spec-declarations` | `contracts/schemas/declarations` | `project`・`supervise`・`release`・`worktree`・`glossary`・`instructions`・`mvv`・`mvv-body`・`pace`・`review`・`backlog`・`runtime-policy`・`ask`・`context`・`estimate`・`issue-items`・`projects`・`code-metrics` |
| `spec-io` | `contracts/schemas/io` | — |
| `spec-pages` | `docs/contracts` | — |

## 呼び手と合わせる前の行

公開する面の表で順番が `caller-first` の行は、入れ替えの前に呼び手の側を新しいエントリポイントへ直す行で、規約の項目にまだ当たらなくてよい。

| 行 | 種類 | 名前 | 出所 |
| --- | --- | --- | --- |
| `gate-record` | command | `gate` | sprint-state.py gate と gate-rejections.py record に渡す承認ゲートの名前（volareinc/nd-factory#183） |
