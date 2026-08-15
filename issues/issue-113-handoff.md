# Issue 113: cross-refactoring 引き継ぎメモ

このブランチ（`claude/cross-refactoring-loop-skill-zmnhbx`）は **設計と事前調査のみ**で、
実装コードは 1 行も入っていない。次に着手する人が読む順序と、引き継ぎ時点で決まっている
こと・決まっていないことをまとめる。

## 1. 成果物

| ファイル | 内容 |
| --- | --- |
| [issue-113-cross-refactoring-loop.md](issue-113-cross-refactoring-loop.md) | 実施計画の本体。設計方針 / state スキーマ / 12 タスクの分解 / 受け入れ条件 |
| [issue-113-task3-cli-verification.md](issue-113-task3-cli-verification.md) | Task 3（CLI 非対話実行）の検証記録。実測値と出典、未確認項目のチェックリスト |
| 本ファイル | 進捗と引き継ぎ |

読む順序は **本ファイル → 実施計画 → 検証記録**。実施計画は長いので、実装に入る前に
§2（ランタイム輪番）・§5（ランタイム別の固有対応）・§6（bash 骨組み）だけ先に読めばよい。

## 2. この Skill が何をするものか（3 行）

- codex / gemini / kiro / claude のうち**ホストを除いた 3 CLI** にリファクタリング箇所を提案させる
- 提案ごとに **実装 1 : レビュー 2** で輪番を組み、レビュー指摘が尽きるまで直す
- 全提案を消化したら提案フェーズへ戻り、新しい提案が出なくなったら完了

## 3. 進捗

### 完了

- [x] 設計の確定（三重ループ / ランタイム輪番 / worktree 構成 / 終了条件 / state スキーマ）
- [x] 12 タスクへの分解と受け入れ条件の定義
- [x] **Task 3 のうち Claude CLI 部分**（実測で起動形式・完了検知・root 制約を確定）
- [x] **Task 3 のうち Kiro 部分の公開情報調査**（`allowedTools` に依存しない方針を確定）
- [x] git worktree の同一ブランチ制約の実測
- [x] frontmatter 予算の実測（残余 588 文字）

### 未着手

- [ ] **Task 3 の残件（Kiro の実機確認）— 最優先。これが終わるまで実装に入らない**
- [ ] Task 1〜2, 4〜12（実装・テスト・配布物同期）

## 4. 次にやること（この順序で）

### 4-1. Task 3 の残件を潰す（最優先）

**kiro-cli が入った環境**が要る。このブランチを作った Claude Code on the web の
コンテナには codex / gemini / kiro-cli が無く、確認できなかった。

確認項目は [検証記録 §4](issue-113-task3-cli-verification.md) の 6 点。特に重要なのは
次の 2 つで、結果次第で設計が変わる。

1. **`--trust-tools` / `--trust-all-tools` が `--no-interactive` と併用できるか**
   → 併用できなければ Kiro は実装フェーズに参加できず、`impl_capable` から外す縮退設計へ移る
2. **`--trust-tools` に渡すツール名の正確な綴り**
   → 既存実測は `execute_bash`、Kiro#7467 の例は `read,write,aws,report` と表記が揺れている

結果は検証記録に追記し、`external-ai/references/cli-kiro.md` の初版に反映する。

### 4-2. Task 9（`monitor.py` の汎用化）を先に片付ける

Task 1〜8 のどれもが `monitor.py` を呼ぶ。既存 Skill（cross-review）を壊さないことが
最重要なので、**既存テストを 1 つも変更せずに通す**ことを完了条件にして先に済ませる。

### 4-3. Task 1 → 2 → 4 → 5 → 6 → 7 → 8 の順で実装

state（Task 1）と worktree（Task 2）が無いと他が動かない。以降は
提案 → 適用 → レビュー → 修正 → 収束判定 の実行順と同じ。

## 5. 引き継ぎ時点で確定していること

判断の経緯も含めて残す。**同じ検討を繰り返さないための記録**。

| 決定 | 理由 |
| --- | --- |
| 参加者は「全ランタイム − ホスト」の 3 つ | gemini がホストになり得ないため、どのホストでも必ず 3 つ揃う。輪番の式が全ホストで同一になる |
| ホストはどのフェーズにも参加しない | オーケストレータに徹することで、実装とレビューの独立性が構造的に保たれる |
| 参加者は全員 CLI（claude も `claude -p`） | ホストの Agent tool を使わないため、**ループ全体を 1 本の bash で駆動できる**。cross-review の骨組みをそのまま流用できる |
| 読み取り用 worktree は `--detach` | git が同一ブランチの二重 checkout を拒否する（実測済み） |
| 適用は直列、提案とレビューは並列 | 同一ブランチへの同時コミットは競合とレビュー単位の曖昧化を招く |
| 収束しない item は revert して捨てる | リファクタリングは任意作業。揉める提案を PR に残すより捨てる方が安全 |
| Kiro の承認は**フラグ**で与える（agent JSON に依存しない） | agent JSON の `allowedTools` が honored されない報告があるため（Kiro#7467） |
| claude の権限は `acceptEdits` + `--allowed-tools` | `bypassPermissions` は root 実行で拒否される（実測済み）。CI / コンテナは root が多い |
| `monitor.py` は複製せず汎用化 | 多軸完了判定は運用で作り込まれた資産。複製すると片方だけ直る事故が起きる |
| PR ローテーションは v1 では作らない | 件数上限で総量を抑える方針を先に検証する（最小限のコード実装） |
| 配布は 3 ランタイムすべて | Agent tool 依存が消えて Skill がランタイム中立になったため |

### 設計変更の履歴（初版から変わった点）

1. **参加者に claude を含める案 → ホストを除く 3 者の輪番へ**
   初版は「codex / gemini / claude が参加、claude は Agent サブエージェント」だった。
   利用者の指摘で「ホストを除いた 3 者」へ変更した結果、Agent tool 依存が消え、
   独自に作る予定だった ACTION 状態機械が不要になった（**新規機構が 1 つ減った**）。
2. **Kiro 専用 agent JSON の生成 → 廃止**
   `allowedTools` で事前承認する案は Kiro#7467 の報告により破棄。フラグ指定へ移行し、
   `prepare-worktrees.sh` の該当処理も削除した。

## 6. 落とし穴（実装前に必ず読む）

- **`kiro-cli agent set-default` を呼んではいけない。** 既定エージェントは
  `~/.local/share/kiro-cli/data.sqlite3` に保存される**マシン全体の設定**であり、
  利用者の既存設定を奪う。常に `--agent` で明示指定する
- **Kiro の承認漏れはエラーではなくハングとして現れる**（Kiro#7483）。終了コードでは
  検知できないため、stall timeout が唯一の検知手段になる
- **`claude --permission-mode bypassPermissions` は root で必ず失敗する。** ローカルの
  非 root 環境でだけ動作確認すると、CI / コンテナで初めて詰まる
- **frontmatter 予算の残余は 588 文字しかない**（上限 11200 / 現在 10612）。
  cross-review の frontmatter は 407 文字。新 Skill + `external-ai` の description 拡張で
  ほぼ使い切るため、`argument-hint` を短く保つ。超えたら `FRONTMATTER_TOTAL_MAX` の
  見直しか既存 `description` の圧縮を同 PR で行う
- **`monitor.py` の変更で cross-review を壊さない。** 追加オプションはすべて既定値で
  現行挙動を維持し、既存テストは 1 行も変更しない
- **参加ランタイムに NDF が入っている前提を置かない。** 対象リポジトリは任意なので、
  `/ndf:*` 形式のコマンドをプロンプトに書かず、worktree 内の `refs/` を明示パスで読ませる

## 7. 判断が要る未決事項

実装者が勝手に決めず、利用者に確認した方がよいもの。

| # | 未決事項 | 選択肢 |
| --- | --- | --- |
| 1 | Kiro でシェル実行が通らなかった場合の扱い | (a) 提案・レビュー専任へ縮退（`impl_capable` から除外） / (b) Kiro 参加自体を見送る |
| 2 | `--trust-all-tools` を既定にしてよいか | worktree は隔離済みで、codex の `--dangerously-bypass-approvals-and-sandbox` / gemini の `--skip-trust` と同じ整理。より厳しくするなら `--trust-tools` の明示列挙を既定にする |
| 3 | claude 参加時の実行コスト上限 | 単純な 3 ターンで $0.26。1 ラウンド最低 6 回の CLI 起動が走る。上限値の既定（`--max-items-per-round` = 5 / `--max-outer-rounds` = 3）をさらに絞るか |
| 4 | 対象スコープ（`--scope`）を必須にするか | 必須にすると誤爆しないが手間が増える。現計画では必須 |

## 8. 検証コマンド

このブランチの内容（ドキュメントのみ）に対して通るもの:

```bash
python3 scripts/check-markdown-links.py
```

実装に入ったあと、PR を出す前に通すもの:

```bash
python3 scripts/check-skill-frontmatter.py
bash scripts/build-runtime-plugins.sh --check
bash scripts/validate-runtime-plugins.sh
python3 scripts/check-markdown-links.py
claude plugin validate
```

## 9. 関連

- GitHub Issue: https://github.com/devbasex/ai-plugins/issues/113
- 参考実装: `plugins/ndf-shared/skills/cross-review/`（骨組みの流用元）
- 手順の委譲先: `plugins/ndf-shared/skills/refactoring/`
