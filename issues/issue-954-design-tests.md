# #954: supervisor が長い待ちの後に文脈の全体を書き直す — テスト設計と未確認

| 文書 | 中身 |
| --- | --- |
| [issue-954-requirements.md](issue-954-requirements.md) | 要求と受け入れ条件 |
| [issue-954-design.md](issue-954-design.md) | 設計 |
| [issue-954-design-decisions.md](issue-954-design-decisions.md) | 決定の理由 |

## テスト設計

hook のテストは `plugins/ndf/scripts/tests/`、集計のテストは `scripts/tests/` に置く。

```bash
uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest plugins/ndf/scripts/tests scripts/tests -q -n 4
claude plugin validate .
```

hook のテストは、一時ディレクトリに偽の会話の記録を作り、hook の入力の JSON を標準入力で渡す。
作る記録は `<セッション>.jsonl` と、`<セッション>/subagents/` の `agent-<ID>.jsonl`・`agent-<ID>.meta.json` である。

| 受け入れ条件 | 何で確かめるか | 階層 |
| --- | --- | --- |
| AC1 | 2 つの定義の frontmatter を読み、`name`・`experimental.cacheTtl` が片方だけにあること・`tools` / `disallowedTools` が無いこと・`plugin.json` の `agents` に載ることを照らす。`claude plugin validate .` の終了コード | 単体・コマンド |
| AC2・AC4 | 文書の変更。文言を照合するテストは書かない（`AGENTS.md`）。設計 Pull Request と実装の `cross-review` が見る | レビュー |
| AC3 | `env -i` と一時の HOME で隔離した claude（`NPM_CONFIG_PREFIX` なども一時の場所へ）に、`--plugin-dir` で作業ツリーの NDF を渡し、`ndf:supervisor-waits` のサブエージェントを 1 本起動する。記録の `usage.cache_creation` の 1 時間と 5 分を読む。`ndf:supervisor` でも 1 本起動し、逆になることを見る | 手動（実機） |
| AC5 | 次の 7 つの入力で止める・通すを照らす: (1) supervisor・`cross-review`・C = 2P で止める (2) 同じで C = 1.9P なら通す (3) 止めた直後の同じ起動は通す (4) `agent_id` が無い（conductor）なら通す (5) `agentType` が `ndf:worker` なら通す (6) meta が読めない・記録が空なら通す (7) `NDF_SUPERVISOR_CUT_GUARD=0` なら通す。止めたときの理由の欄に `区切り` と `次の工程` が入ること | 単体（偽の記録） |
| AC6 | 間隔 4 分と 6 分の書き直しを 1 回ずつ持つ偽の記録で、`rewrite_tokens_after_5m` が 6 分の側の量だけになる。md の表に列が出る | 単体 |
| AC7〜AC9 | 配布の後、`token-usage.py --min-version <配布した版>` を持ち場ごとに回し、設計の「基準の実測」の表と同じ列で並べる。損益分岐は決定 3 の式で計算し直す | 手動（測定） |
| AC10 | この設計 Pull Request | レビュー |

## 未確認のまま残ること

| # | 項目 | 内容 | 確かめる時点 |
| --- | --- | --- | --- |
| U1 | プラグインの定義の `experimental.cacheTtl` が効くか | 公式の手引きはプラグインでも読むとするが、実機で `ephemeral_1h_input_tokens` を見ていない（#954 の調べでは隔離した HOME に認証が無かった） | 実装（AC3）。効かなければ決定 3 の割り当てを外し、候補 2 だけで進める |
| U2 | hook の入力の `agent_id` から `subagents/agent-<ID>.meta.json` を引けるか | ファイル名の形（`agent-<ID>`）は記録で確かめた。hook が走る時点で meta が書かれているかは見ていない | 実装（AC5 の実機の 1 回） |
| U3 | 区切りの後の supervisor が、前の報告だけで収束ループを始められるか | `cross-review` は PR 番号から状態を作り直す（`state.py init` の再開）。設計の持ち場の前半の判断が要る指摘が出たときに、読み直す量が R の見込み（5k）を超えるかもしれない | AC7 の測定で、区切りの後の区間の P と k を見る |
| U4 | 利用枠の超過中に frontmatter の `1h` が無視される | 無視されると 5 分に戻り、今と同じ費用になる（損はしない）。測定で 1 時間の区間に 5 分の書き込みが混ざっていたら、この場合と読む | AC7 |
