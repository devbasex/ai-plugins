# 設定と入出力の規約

<!-- ndf check --write-spec が生成する。手で直さない（SSOT は各節の出典） -->

NDF を外から呼ぶ面（設定・サブコマンド・環境変数・受け渡しのファイル・hook・Skill・起動スクリプト）の規約である。機械が読む形は JSON Schema（`contracts/schemas/ndf-settings/`・`contracts/schemas/io/`）で、このページの表はそれと SSOT の表から生成する。

**公開**の項目は外の呼び手（連携システム）が頼ってよく、名前と意味をバージョンで約束する。**内部**の項目は NDF の中の Skill・エージェント・AI が使い、予告なく変えてよい。公開か内部かは、公開する面の表（`contracts/domains/entrypoints.json` と、`contracts/domains/ndf-files.json` の列 `reader`）に当たるかで決まる

## ページ

| ページ | 件数 |
| --- | --- |
| [設定（.ndf/）](ndf-settings.md) | 公開 18・内部 1 |
| [サブコマンドの引数と結果](commands.md) | 公開 0・内部 28 |
| [終了コードと停止理由](exit-codes.md) | 終了コード 11・停止理由 32 |
| [環境変数](env.md) | 公開 10・内部 54 |
| [受け渡しのファイル](files.md) | 公開 12・内部 56 |
| [hook の入出力](hooks.md) | 公開 6・内部 11 |
| [Skill と起動スクリプト](skills.md) | 公開 1・内部 48 |

## 規約の保存先（公開する面の表の種類 spec）

| 行 | 保存先 | 公開する設定 |
| --- | --- | --- |
| `spec-io` | `contracts/schemas/io` | — |
| `spec-ndf_settings` | `contracts/schemas/ndf-settings` | `project`・`supervise`・`release`・`worktree`・`glossary`・`instructions`・`mvv`・`mvv-body`・`pace`・`review`・`backlog`・`runtime-policy`・`ask`・`context`・`estimate`・`issue-items`・`projects`・`code-metrics` |
| `spec-pages` | `docs/contracts` | — |

## 呼び手と合わせる前の行

公開する面の表で順番が `caller-first` の行は、入れ替えの前に呼び手の側を新しいエントリポイントへ直す行で、規約の項目にまだ当たらなくてよい。

| 行 | 種類 | 名前 | 出所 |
| --- | --- | --- | --- |
| `gate-record` | command | `gate` | sprint-state.py gate と gate-rejections.py record に渡す承認ゲートの名前（volareinc/nd-factory#183） |
