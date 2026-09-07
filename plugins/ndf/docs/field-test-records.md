# 実機検証の記録

**その時点の実測であり、以後の構成変更には追随しない。** 現行の仕様は
[../README.md](../README.md) と各 Skill の `SKILL.md` にある。

kiro-cli **2.16.1** / 検証日 **2026-08-07**（ランタイム規約の調査）、**2026-08-08**（本変更の導入方式の検証）。

`docs/specifications/ndf-skill-inventory/`（Skill 棚卸台帳）は本ブランチ時点で未作成のため、検証結果はここに記録します。台帳への転記は台帳作成後に行います。

| 検証項目 | 結果 | 根拠 |
| --- | --- | --- |
| シンボリックリンク経由の Skill を認識するか | 認識する（[#6401](https://github.com/kirodotdev/Kiro/issues/6401) は 2.16.1 で再現せず） | 実体ディレクトリとリンクを並べ、両方が一覧・読み取りとも成功 |
| 起動時に Skill 本文を読み込むか | 読み込まない（[#6680](https://github.com/kirodotdev/Kiro/issues/6680) は 2.16.1 で再現せず） | 「ファイルを読まずに本文中のマーカーを出力せよ」に対し「本文なし」と応答 |
| `description` 一致で自動発動するか | 発動する（[#5867](https://github.com/kirodotdev/Kiro/issues/5867) は 2.16.1 で再現せず） | `skill://` 指定を削除した状態で、該当依頼に対し `docker-container-access/SKILL.md` を自ら読みに行った |
| プロジェクト配置で `allowed-tools` が事前承認になるか | **ならない**（[#6055](https://github.com/kirodotdev/Kiro/issues/6055)） | `allowed-tools: execute_bash` を持つ検査用 Skill が denied list で拒否された |
| `install.sh` 後に `kiro-cli agent list` へ現れるか | 現れる | `ndf  Workspace  NDF統合開発エージェント（Kiro CLI用）` |
| `--agent ndf` で agentSpawn フックが動くか | 動く | `[NDF] CLAUDE.ndf.md が検出されました…` が文脈へ注入された。`kiro_default` では注入されない |
| `--set-default` で既定が切り替わるか | 切り替わる | `agent list` の `*` が `ndf` へ移り、素の `kiro-cli chat` でも agentSpawn フックが動いた |
| `--project` で別ディレクトリへ導入したとき `--set-default` が効くか | 効く（修正後） | 修正前は導入先以外の cwd から実行すると `Failed to set default agent: No agent with name ndf found` になり、しかも終了コード 0 で「変更しました」と表示していた。修正後は導入先で `kiro-cli` を実行し、`agent list` で反映を検証する |
| `--scope global --set-default` が効くか | 効く | `$HOME` で `kiro-cli` を実行し `agent list` の `*` が `ndf` へ移った。検証後に `kiro-cli agent set-default kiro_default` で復旧し、`~/.kiro` の `find` 比較で検証前と一致することを確認 |
| `--scope global` で `~/.kiro/` へ配置されるか | 配置される | `~/.kiro/{skills,steering,prompts,agents}` が生成され、プロジェクト外でも `Global` として一覧に出た |
| steering がエージェント選択に依存せず読まれるか | 読まれる | `kiro_default` の `/context show` にも `.kiro/steering/**/*.md` の一致として現れた |
| 再インストールで利用者管理の設定が残るか | 残る | `mcpServers.bigquery` / `hooks.userPromptSubmit` / `toolsSettings` を書き足してから再実行し、すべて残ることを確認。ログに `利用者管理の設定を引き継ぎました: hooks.userPromptSubmit, mcpServers.bigquery, toolsSettings` |
| `--with-codex` を外した再実行の挙動 | `mcpServers.codex` だけ消える | 同じ再実行で `mcpServers.bigquery` は残った。`codex` は installer 管理のため |
| 既存 `ndf.json` が壊れた JSON のとき | テンプレートから再生成する | `WARN: 既存の … を読めないため引き継ぎません` を出して続行し、`.bak` は残る |
| `kiro-cli agent set-default` の保存先 | `~/.local/share/kiro-cli/data.sqlite3`（マシン全体の設定） | 実行した cwd に `.kiro/settings.json` は生成されず、`find ~/.kiro ~/.aws` にも差分が出なかった |
| 既定エージェントが cwd 依存で復旧できるか | 導入先から実行すれば復旧できる | 対象プロジェクト限定の workspace エージェントを既定にした状態では、別 cwd からの `set-default` が `No agent with name … found` になりつつ終了コード 0 を返し、既定が戻らなかった |

コンテキスト占有率を `kiro-cli chat --agent <名前> --no-interactive '/context show'` で実測しました。測定用プロジェクトには本リポジトリの `AGENTS.md` と `README.md` を置き、`install.sh --project <測定用ディレクトリ>` で配布物を導入しています。**下表は Kiro manifest が 21 個だった時点の測定値**で、4 構成を比較するために同一プロジェクトで測ったものです（`ndf-policies` は `.kiro/steering/` へ回すため `.kiro/skills/` に並ぶのは 20 個）。`一致ファイル数` と `合計文字数` は `/context show` が列挙したファイルを数え上げた値、`占有率` は `Context files total` の表示値です。

| 構成 | 一致ファイル数 | `ndf-policies` の注入回数 | 占有率 | 文脈ファイルの合計文字数 |
| --- | --- | --- | --- | --- |
| 変更前 `default` エージェント | 26 | 2（`resources` + Skill） | 0.6% | 125,723 |
| 本 PR 初版 `ndf` エージェント | 26 | 2（Skill + steering） | 0.6% | 125,746 |
| 修正後 `ndf` エージェント | 25 | 1（steering のみ） | 0.6% | 125,562 |
| 参考: 組み込み `kiro_default` | 25 | 1（steering のみ） | 0.6% | 125,562 |

`resources` の二重登録を解消しただけでは、代わりに steering が 1 件増えるためファイル数は 26 のまま減りませんでした。`ndf-policies` を `.kiro/skills/` へ symlink しない変更を加えて、はじめて 26 → 25 に減っています。ただし `ndf-policies/SKILL.md` は 184 文字しかないため、合計文字数の削減は 125,746 → 125,562（-184 文字）にとどまり、`/context show` の表示（0.1% 刻み）は 0.6% のまま変わりません。重複解消の目的は表示上の占有率低減ではなく、同じ指示が 2 回注入される状態を解消することです。

`ndf-policies` を Skill として置かなくても機能は落ちません。`user-invocable: false` で本文の参照を前提としない Skill であり、内容は steering として常時読み込まれるためです。

なお 2026-08-07 に別プロジェクトで測った 0.2% / 112,598 文字という値は、測定用プロジェクトの `AGENTS.md` / `README.md` が異なるため本表とは比較できません。上表は 4 構成すべてを同一プロジェクトで測り直した値です。

その後、ブラウザ自動テストの 3 個を 3 ランタイムへ配布する変更で Kiro manifest は
**24 個**（`.kiro/skills/` に並ぶのは 23 個）になりました。同じ手順で測り直した現行
構成の値は次のとおりです。

| 構成 | 一致ファイル数 | `ndf-policies` の注入回数 | 占有率 | 文脈ファイルの合計文字数 |
| --- | --- | --- | --- | --- |
| 現行 `ndf` エージェント（manifest 24 個） | 26 | 1（steering のみ） | 0.9% | 139,182 |

Skill が 3 個増えても `ndf-policies` の注入は 1 回のままです。占有率が 0.6% から
0.9% へ上がったのは、Skill が 3 個増えたことに加え、測定用プロジェクトへ置いた
`AGENTS.md` / `README.md` が棚卸の記載追加でその間に大きくなったためです（同一
プロジェクトでの前後比較ではないため、この差分だけを Skill 増加の影響として
読まないこと）。

