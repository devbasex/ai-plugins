# ランタイム別の固有対応

[← 01-overview.md](01-overview.md)

codex と gemini は `cross-review` の起動スクリプトに実績があるためそのまま流用する。
新たに扱いを決める必要があるのは **Kiro と Claude** である。
実行ログと出典は [CLI 実機検証の記録](../issue-113-task3-cli-verification.md) にある。

## 1. codex

```bash
codex exec --dangerously-bypass-approvals-and-sandbox < prompt.md
```

**プロンプトは必ず標準入力から渡す。** コマンド引数に載せると、標準入力が開いている限り
`Reading additional input from stdin...` を表示したまま待ち続ける（600 秒でも終了しない）。
コマンド引数の形式を採るなら `< /dev/null` が必須になる。

## 2. gemini

`cross-review` の `_gemini-env.sh` 経由で信頼済みディレクトリを設定し、`--skip-trust` と
設定ファイルの無害化を行う（環境変数 `GEMINI_CLI_TRUST_WORKSPACE=true` を含む）。

gemini は作業領域の外への書き込みが拒否されるため、一時ファイルは必ず各自の作業
ディレクトリ内に置く。

## 3. Kiro

kiro-cli 2.18.0 で実機検証済み。起動形式は次のとおり。

```bash
cat prompt.md | kiro-cli chat --no-interactive --trust-all-tools > out.txt 2> err.txt
```

| 事項 | 内容 | 対応 |
| --- | --- | --- |
| 実行形式 | `--no-interactive` は**標準入力からプロンプトを受け取る**（26KB で実測）。コマンド引数でも渡せる | 標準入力から渡す |
| ツールの事前承認 | 絞り込みは効くが、**シェル実行を許可した時点で他ツールの制限は迂回される** | `--trust-all-tools` で全許可する（codex / gemini と同じ整理）。**専用のエージェント定義も生成しない** |
| 承認漏れの現れ方 | 停止せず、**標準エラー出力に拒否メッセージを出して終了コード 0** で終わる | 早期エラーの検出語に `is rejected because it matches one or more rules on the denied list` を加える。無反応の打ち切りは別要因への保険として残す |
| 完了検知 | **終了コードは使えない。** ツール拒否でもシェルの失敗でも 0 を返す。1 は未認証と入力なしのみ | 結果ファイルの有無と標準エラー出力の照合で判定する。終了コード 1 は即エラー扱い |
| 出力の色 | `NO_COLOR=1` / `TERM=dumb` / 非 TTY でも **ANSI エスケープが残る** | 照合の前に ANSI エスケープを除去する |
| 既定エージェント | `~/.local/share/kiro-cli/data.sqlite3` に保存される**マシン全体の設定** | `kiro-cli agent set-default` を呼ばない。`kiro-cli agent create` もエディタを開くため非対話から呼ばない |
| エージェント一覧の出力先 | 一覧を**標準エラー出力**に書く | 存在確認で標準出力を解析しない |
| スラッシュコマンド | `/ndf:*` 形式のコマンドは存在しない | プロンプトは**自己完結した平文**にする |
| Skill 本文の読み込み | **配置しただけでは本文を読まない** | プロンプトに `.kiro/skills/<name>/SKILL.md` の**明示パス**を書いて読ませる（[04-skill-provisioning.md](04-skill-provisioning.md)） |

> **ツールの絞り込みは防御にならない。** シェル実行を許可したうえでファイル書き込みを
> 拒否した状態でも、`echo ... > file` をシェル経由で実行してファイルが作成された。
> 実効的な防御は作業ディレクトリの隔離だけである。加えてツール名を綴り間違えても警告が
> 出るだけで終了コード 0 になり、「何も信頼しない状態で正常終了した」ように見える。
> 防御力が無いまま事故のリスクだけが残るため、絞り込みは使わない。

## 4. Claude

claude 2.1.233 で実機検証済み。起動形式は次のとおり。

```bash
cat prompt.md | claude -p \
    --permission-mode acceptEdits \
    --allowed-tools "Bash,Write" \
    --output-format json
```

この経路を通るのは次の 2 つで、いずれも同じ起動形式である。

1. ホストが Codex / Kiro CLI のとき、claude が提案・レビューに参加する
2. ホストが Claude Code のとき、claude が適用を担当する

2 の場合もサブエージェント機能は使わず、**別プロセスの `claude -p` を起動する**。
ホストセッションの作業文脈は汚れず、レビュー担当 2 者が実装者と別モデルである性質も保たれる。

| 事項 | 内容 |
| --- | --- |
| プロンプトの渡し方 | **標準入力から渡せる。** 長いプロンプトをコマンド引数に載せる必要がない |
| 作業領域の信頼 | `-p` 指定時は信頼確認が省略される。gemini の `--skip-trust` に相当する対応は不要 |
| 権限モード | **`bypassPermissions` は root 実行で拒否される**（`--dangerously-skip-permissions cannot be used with root/sudo privileges`）。継続的インテグレーションやコンテナは root 実行が多いため既定にできない。`acceptEdits` と `--allowed-tools` の明示なら root でも通る |
| 完了検知 | `--output-format json` の `is_error` / `subtype` で確定できる。完了印のファイルは不要 |
| 承認失敗の検知 | 同じ JSON の `permission_denials` が非空なら承認失敗。早期エラーとして扱える |
| 作業ディレクトリ | 参加者用の作業ディレクトリを指定する（`--add-dir` で範囲を広げない） |
| Skill の参照 | 対象リポジトリに NDF が入っている保証がないため、`/ndf:*` を呼ばず、配置した Skill を明示パスで読ませる |
| 実行コスト | 単純な 3 ターンの作業で 0.259 ドル、実際のリファクタリング 1 件（29 ターン）で 1.42 ドル |
