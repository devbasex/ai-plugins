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
| AC1 | 2 つの定義の frontmatter を読み、`name`・`experimental.cacheTtl` が片方だけにあること・`tools` / `disallowedTools` が無いこと・`plugin.json` の `agents` に載ることを照らす。`claude plugin validate .` の終了コード。定義の数を書いた文書の更新はレビューで見る（文言のテストは書かない） | 単体・コマンド |
| AC2・AC4 | 文書の変更。文言を照合するテストは書かない（`AGENTS.md`）。設計 Pull Request と実装の `cross-review` が見る | レビュー |
| AC3 | `env -i` と一時の HOME で隔離した claude（`NPM_CONFIG_PREFIX` なども一時の場所へ）に、`--plugin-dir` で作業ツリーの NDF を渡し、`ndf:supervisor-waits` のサブエージェントを 1 本起動する。記録の `usage.cache_creation` の 1 時間と 5 分を読む。`ndf:supervisor` でも 1 本起動し、逆になることを見る | 手動（実機） |
| AC5 | 下の「AC5 の入力の表」の入力で止める・通すを照らす。止めたときの理由の欄に `区切り` と `次の工程` が入ること | 単体（偽の記録） |
| AC6 | 間隔 4 分と 6 分の書き直しを 1 回ずつ持ち、量が違う（例 30k と 50k）偽の記録で、`rewrite_tokens_after_5m` が 6 分の側の量だけになる。間隔 6 分で書き直しにならない呼び出し（読み込み 80k）を足すと `read_tokens_after_5m` が 80k になる。時刻を欠く呼び出しはどちらにも足さない。md の表に列が出る | 単体 |
| AC7〜AC9 | 配布の後、`token-usage.py --min-version <配布した版>` を持ち場ごとに回し、設計の「基準の実測」の表と同じ列で並べる。損益分岐は決定 3 の式で計算し直す | 手動（測定） |
| AC10 | この設計 Pull Request | レビュー |

### AC5 の入力の表

P は偽の記録の最初の呼び出しの文脈、C は最後の呼び出しの文脈である。

| # | `agentType` | Skill の名前 | C | そのほか | 期待 |
| --- | --- | --- | --- | --- | --- |
| 1 | `ndf:supervisor` | `cross-review` | 2.5P | — | 止める |
| 2 | `ndf:supervisor` | `ndf:cross-review` | 2.5P | — | 止める |
| 3 | `ndf:supervisor` | `cross-refactoring` | 3P | — | 止める |
| 4 | `ndf:supervisor` | `ndf:cross-refactoring` | 3P | — | 止める |
| 5 | `ndf:supervisor` | `cross-review` | 2.4P | — | 通す |
| 6 | `ndf:supervisor` | `pr` | 3P | — | 通す |
| 7 | `ndf:supervisor` | `cross-review` | 2.5P | 1 の直後の同じ起動 | 止める（1 度通しを持たない） |
| 8 | — | `cross-review` | 3P | 入力に `agent_id` が無い（conductor） | 通す |
| 9 | `ndf:worker` | `cross-review` | 3P | — | 通す |
| 10 | `general-purpose` | `cross-review` | 3P | — | 通す |
| 11 | — | `cross-review` | 3P | 入力に `agent_id` があるが meta が無い | 通す |
| 11b | `ndf:supervisor` | `cross-review` | — | meta はあるが記録が空（P・C が読めない） | 通す |
| 17 | `ndf:supervisor-waits` | `cross-review` | 5P | — | 通す（1 時間の区間では区切らない） |
| 18 | `ndf:supervisor` | `cross-review` | 3P | `NDF_CONTEXT_GUARD=0` | 止める（conductor の判定と独立） |
| 12 | `ndf:supervisor` | `cross-review` | 3P | `NDF_SUPERVISOR_CUT_GUARD=0` | 通す |
| 13 | `ndf:supervisor` | `cross-review` | 2.4P | 親の記録（`transcript_path`）の最後の文脈は自身の P の 10 倍 | 通す（自身の記録を読むこと） |
| 14 | `ndf:supervisor` | `cross-review` | 2.5P | 自身の記録が 300 行を超え、最初の呼び出しが末尾 200 行の外にある。末尾 200 行の中の最も古い呼び出しの文脈は 2.4P | 止める（P を先頭から読むこと） |
| 15 | `ndf:supervisor` | `cross-review` | 2.5P | `NDF_SUPERVISOR_CUT_RATIO=3` | 通す |
| 16 | `ndf:supervisor` | `cross-review` | 3P | `NDF_SUPERVISOR_CUT_RATIO=3` | 止める |

## 未確認のまま残ること

| # | 項目 | 内容 | 確かめる時点 |
| --- | --- | --- | --- |
| U1 | プラグインの定義の `experimental.cacheTtl` が効くか | 公式の手引きはプラグインでも読むとするが、実機で `ephemeral_1h_input_tokens` を見ていない（#954 の調べでは隔離した HOME に認証が無かった） | 実装（AC3）。効かなければ決定 3 の割り当てを外し、候補 2 だけで進める |
| U2 | hook の入力の `agent_id` から `subagents/agent-<ID>.meta.json` を引けるか。`agentType` に定義の名前が入るか | ファイル名の形（`agent-<ID>`）は記録で確かめた。hook が走る時点で meta が書かれているかは見ていない。**プラグインの定義で起動したときの `agentType` の値も見ていない**（手元の記録は `general-purpose` の起動だけ）。`ndf:supervisor` 以外の形（接頭辞なし・別のキー）で入るなら、判定の照らし方を実測の値へ合わせる | 実装（AC3 の実機の起動で meta を読み、AC5 の判定の値を決める） |
| U3 | 区切りの後の supervisor が、前の報告だけで収束ループを始められるか | `cross-review` は PR 番号から状態を作り直す（`state.py init` の再開）。設計の持ち場の前半の判断が要る指摘が出たときに、読み直す量が R の見込み（5k）を超えるかもしれない | AC7 の測定で、区切りの後の区間の P と k を見る |
| U4 | 利用枠の超過中に frontmatter の `1h` が無視される | 無視されると 5 分に戻り、今と同じ費用になる（損はしない）。測定で 1 時間の区間に 5 分の書き込みが混ざっていたら、この場合と読む | AC7 |
