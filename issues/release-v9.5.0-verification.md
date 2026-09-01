# v9.5.0 リリース後テスト

配布したのは **NDF v9.5.0**（バッチ 02 のまとまり 8 本）。この文書は `release-verification`
の出力物である。

## リリース後テスト

対象の版: **NDF v9.5.0**（2026-09-01 13:27 マージ / タグ `ndf--v9.5.0` → `5dc16be`）
導入経路: 利用者が実行するのと同じ手順（3 ランタイム）

| 受け入れ条件 | 実行したこと | 結果 |
| --- | --- | --- |
| 1. 配布した版を利用者が取得できる（Claude Code） | `claude plugin marketplace update ai-plugins` → `claude plugin update ndf@ai-plugins` | 合格 / `updated from 9.4.0 to 9.5.0` |
| 2. 配布した版を利用者が取得できる（Codex） | `codex plugin marketplace upgrade ai-plugins` → `codex plugin add ndf@ai-plugins` | 合格 / `installed, enabled 9.5.0` |
| 3. 配布した版を利用者が取得できる（Kiro） | `bash plugins/ndf/dev.kiro/install.sh --project <検証用> --yes` | 合格 / `.kiro/agents/ndf.json` が `v9.5.0` |
| 4. 新設した `release` Skill が 3 ランタイムへ届く | 導入物の `skills/release/SKILL.md` の存在確認 | 合格 / 3 ランタイムすべてに存在 |
| 5. 形ごとの参照ファイルが届く | 導入物の `skills/release/references/` の一覧 | 合格 / `form-package-plugin` / `form-service` / `form-desktop` / `form-mobile` / `form-procedure` + 索引 |
| 6. 公開 Skill 数が manifests と一致（Claude 32） | `wc -l manifests/claude-skills.txt` と `skills/` の実体数 | 合格 / 32 = 32 |
| 7. 公開 Skill 数が一致（Codex 30） | 導入物の `manifests/codex-skills.txt` | 合格 / 30 |
| 8. 公開 Skill 数が一致（Kiro 31） | 生成された `.kiro/skills/` 30 個 + `.kiro/steering/ndf-policies.md` 1 個 | 合格 / 30 + 1 = 31（`ndf-policies` は steering へ移る設計） |
| 9. Projects の記録（#176）が届く | 導入物の `scripts/projects-sync.sh` と `scripts/lib/projects-common.sh` | 合格 / 両方存在 |
| 10. cross-review の作業ツリー同期（#203）が届く | 導入物の `state.py` に `_sync_worktree` が存在 | 合格 / 3 箇所 |
| 11. worktree の `cd` 反映（#186）が届く | 導入物の `worktree-common.sh` の `wt_extract_write_target` | 合格 / 存在 |
| 12. 配布されたコードが動く | 導入物で `uv run --with pytest pytest skills/cross-review/tests skills/development-workflow/tests -q` | 合格 / **235 passed** |
| 13. リリースタグが公開されている | `git ls-remote --tags origin` | 合格 / `ndf--v9.5.0` → `5dc16be` |

**合否: 合格（13 件すべて実施、保留なし）**

起票したもの: **#209**（版を上げても説明文書の本文の版数が古いまま残り、検査を通る。
配布の過程で踏んだ）

## 届き方

| ランタイム | 利用者が実行すること |
| --- | --- |
| Claude Code | `claude plugin marketplace update ai-plugins` → `claude plugin update ndf@ai-plugins`（**再起動が要る**） |
| Codex | `codex plugin marketplace upgrade ai-plugins` → `codex plugin add ndf@ai-plugins` |
| Kiro CLI | clone を更新して `bash plugins/ndf/dev.kiro/install.sh` |

**サードパーティのマーケットプレイスは自動更新が既定で無効である。** 上のコマンドを実行した
時点で届く。

## 取り消しの手段と、その限界

**`ndf--v9.5.0` が最初のタグである。** 9.4.0 以前へはタグでは戻せない。

| 手段 | 限界 |
| --- | --- |
| 取得元ごとタグへ固定（`marketplace add devbasex/ai-plugins@ndf--v9.5.0`） | 同じ取得元の他のプラグインも同時に過去の状態になる |
| `git-subdir` で対象だけ固定 | 利用者が定義ファイルを持つ必要がある |

**配布した側から前の版へ戻す手段は無い。** どちらも利用者の環境の操作である。

## 参照

- [issue-188-release-step.md](issue-188-release-step.md) — 配布の工程の実装プラン
- PR [#208](https://github.com/devbasex/ai-plugins/pull/208) — v9.5.0 の配布
