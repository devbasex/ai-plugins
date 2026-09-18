# #657: 中断と再開（上限で落ちた層を見分け、解除を待って再開する）

## 関連リンク

- 課題: #657（#550 の設計の 3 本目、マイルストーン 18）
- 要求: [issue-550-657-requirements.md](issue-550-657-requirements.md)（**AC40〜AC49 がこの Pull Request の範囲**）
- 設計: [issue-550-657-design.md](issue-550-657-design.md)（決定 18〜22・25）
- 契約: [issue-550-657-design-contracts.md](issue-550-657-design-contracts.md)（中断の点検・コマンドの表）
- 前の 2 本: #751（測定）/ #752（無人の運転、マージ済み）

## モード

`standard`。設計は承認済みでマージ済みのため、この計画は**設計の決定を分解するだけ**である。
決定を変える必要が出たら、変えずに報告する。

## 目的と非目的

達成したい状態:

- 上の層が、利用上限（429）で落ちた配下を**記録から**見分けられる
- 解除時刻を記録から取り、**固定の間隔で待たずに**解除を待てる
- 再開の割り当て（誰が誰を起こすか）が、落ちた層ごとに規約として読める

やらないこと:

- `skill-stats` の集計への追加（#751 で入っている。`ending` / `interruptions` は既にある）
- 運転の節（`agent-layers.md` の持ち場・報告・モデル・並行の本数）の書き換え（#752 の範囲）
- `parallel-work.md` と `issue-plan-strategy` の編集（G2 の範囲）
- `StopFailure` フックの利用（決定 21 で採らないと決まっている）
- 実機での再開の確認（AC43・AC44 はリリース後テスト）

## 前提

| # | 前提 | 後から成否を判定する文 |
| --- | --- | --- |
| 1 | `read_sessions()` と `AgentRecord.resets_at` は #751 で入っており、`ending` の判定順序も固定済みである | `test_transcript_agents.py` の既存のテストが変更なしで通る |
| 2 | `parent_agent_id` は `toolUseId` でたどる実装が入っている | `--parent` の絞り込みが既存の `_link_parent_agents` だけで動く |
| 3 | `agent-layers.md` は 358 行で、500 行まで 142 行の余りがある | 節を足した後も `check-doc-line-limit.py` が通る |
| 4 | `ending` の `api_error` は「最後の assistant が合成の応答で 429 以外」を全部拾う | `interrupted` は `ending == "rate_limit"` だけを返すため、`api_error` は自動的に外れる |

## 受け入れ条件

- [ ] AC40: `interrupted` が `ending` が `rate_limit` の記録だけを返す。`server_error`（500 / 529）と
  `authentication_failed` は返らない。契機の 4 つが規約にある —
  `test_transcript_agents.py` / `test_agent_layers_doc.py`
- [ ] AC41: 解除時刻を `quotaLimits.resetsAt` から取り、`resets_passed` を返す。固定の間隔で待たない —
  `test_transcript_agents.py`
- [ ] AC42: 再開が直下だけであること（conductor は `--depth 1`、supervisor は `--agent` / `--parent`）と、
  続けられないときの後段が層で分かれることが規約にある — `test_agent_layers_doc.py`
- [ ] AC43: 自動の継続が入った conductor の記録から、`interrupted` が解除済みの配下を返す —
  `test_transcript_agents.py`
- [ ] AC44: `wait-reset` が、過ぎていれば直ちに終わり、未来なら**最も早い解除時刻 + `--margin`** まで眠る。
  conductor が直接起動した worker だけが中断しているときも眠る — `test_transcript_agents.py`
- [ ] AC45: 自動の継続が起きなかったときの人の 1 通が規約にあり、**承認ではなく関門に数えない**と書かれている —
  `test_agent_layers_doc.py`
- [ ] AC46: 中断したすべての相手が返り、完了した記録は返らない — `test_transcript_agents.py`
- [ ] AC47: `--layer` / `--parent` / `--agent` で層ごと・起動元ごとに絞れ、解除時刻が読める —
  `test_transcript_agents.py`
- [ ] AC48: 再開した相手が既に済んだ外部への書き込みを重ねないことが、規約と起動の指示にある —
  `test_agent_layers_doc.py`
- [ ] AC49: 落ちた層ごとの検知と再開の割り当てが、**3 通りの表**として規約にある — `test_agent_layers_doc.py`
- [ ] 退行なし: `--agents` を持つ `skill-stats` の出力と `list` の振る舞いが変わらない。
  既存の `test_transcript_agents.py` / `test_agents_report.py` / `test_agent_layers_doc.py` が通る

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `interrupted` / `wait-reset` を `transcript_agents.py` の副コマンドとして足す | **採用** | 設計の決定 13・25。`list` と同じ読み取りを使い回せる |
| B | 解除時刻が読めない記録を「待つ」に倒す | 不採用 | 待ち先が無いまま止まる。**読めない記録は待たずに再開に回す**（下の不変条件） |
| C | `--now` を `wait-reset` にも足してテストを subprocess で書く | 不採用 | 契約の引数の表に無い。眠る関数と時刻を関数の引数で差し替える |
| D | `StopFailure` フックで中断を捕まえる | 不採用 | 決定 21。出力も終了コードも無視されるため conductor を起こせない |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 上限の中断 | 利用上限（429）で層が途中で終わること。`ending` が `rate_limit` |
| 解除時刻 | `quotaLimits.resetsAt`（UNIX 秒）。`AgentRecord.resets_at` が ISO 8601 で持つ |
| 直下 | 上の層が自分で起動した相手。conductor から見れば深さ 1、supervisor から見れば自分が起動した worker |
| 余白（`--margin`） | 解除時刻の後、実際に呼べるまでの猶予。既定 60 秒 |

## 不変条件

- `interrupted` は `ending` が `rate_limit` の記録しか返さない。`in_progress`（続けさせた直後）は返らない
- `wait-reset` は**まだ来ていない**解除時刻だけを見る。過ぎたものは眠る理由にならない
- **解除時刻を持たない上限の中断は `resets_passed` を真にする。** 待ち先が無いまま止まるのを避ける。
  固定の間隔で待たない（AC41）ことと矛盾しない
- 出力にプロンプト・本文・パス・`description` の `: ` より後ろを載せない（AC30）
- ネットワークを開かない（AC34）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `transcript_agents.py list` | 変えない | 既存のテストがそのまま通ること |
| `transcript_agents.py` の副コマンド | `interrupted` / `wait-reset` を追加 | 追加のみ |
| `agent-layers.md` | 節を追加 | 既存の節を書き換えない（#752 からの申し送り） |
| `skill-stats` | 触らない | — |

## 修正対象

- `plugins/ndf/scripts/lib/transcript_agents.py`
- `plugins/ndf/scripts/tests/test_transcript_agents.py`
- `plugins/ndf/scripts/tests/fixtures/transcript_agents/projects/-work-sample/`（`sess-c` / `sess-d` を追加）
- `plugins/ndf/skills/development-workflow/references/agent-layers.md`
- `plugins/ndf/skills/development-workflow/tests/test_agent_layers_doc.py`

## タスク分解

### Task 1: 中断の一覧（`interrupted`）

- **対象ファイル:** `transcript_agents.py`、`test_transcript_agents.py`、フィクスチャ（`sess-c`）
- **変更内容:** `interrupted(records, ...)` と副コマンドを足す。引数は契約のとおり
  `--session`（必須・繰り返し可）/ `--layer` / `--depth` / `--agent`（繰り返し可）/ `--parent` /
  `--now` / `--format md|json`。出力は `ending` が `rate_limit` の記録に `resets_passed` を足したもの
- **フィクスチャ:** `sess-c` に「中断した supervisor 2 本・中断した worker 1 本・完了した記録 1 本・
  自動の継続が追記された conductor」を置く
- **満たす受け入れ条件:** AC40・AC41・AC43・AC46・AC47
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 2: 解除の待ち（`wait-reset`）

- **対象ファイル:** `transcript_agents.py`、`test_transcript_agents.py`、フィクスチャ（`sess-d`）
- **変更内容:** `wait_reset(...)` と副コマンドを足す。`--margin`（既定 60）/ `--max-sleep`。
  眠るのは**まだ来ていない解除時刻のうち最も早いもの + `--margin`** まで。終了コードは
  0 = 過ぎた / 3 = `--max-sleep` で区切った / 2 = 引数の誤り。眠る関数と現在時刻を引数で差し替えられる形にする
- **フィクスチャ:** `sess-d` に「conductor が直接起動した worker だけが、遠い未来の解除時刻で中断している」を置く
- **満たす受け入れ条件:** AC44
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 3: 規約の「中断と再開」の節

- **対象ファイル:** `agent-layers.md`、`test_agent_layers_doc.py`
- **変更内容:** 末尾の「参照」の直前へ節を足す。**運転の節は書き換えない。**
  持つもの: 記録で見分けること（決定 18）/ 目を覚ます契機 4 つ / 落ちた層ごとの 3 通りの表（AC49）/
  解除を待つ手段 3 段（決定 20）/ conductor の中断の点検の手順 / supervisor の worker の点検の手順 /
  人の 1 通は承認ではない（AC45）/ 重ねて書かない（AC48）。
  あわせて `## 持ち場の一覧` の表へ「上限の中断から再開した回数」の列を足す（契約の文書が持つ列）
- **満たす受け入れ条件:** AC42・AC45・AC48・AC49（と AC40 の契機・AC44 の待ち）
- **進め方:** 失敗するテスト → 文書 → 行数の確認

## 影響範囲

- `development-workflow` を読む conductor / supervisor の手順（文書のみ）
- `skill-stats --agents` は触らないが、同じ `read_sessions()` を使うため、フィクスチャの追加で
  `test_agents_report.py` の件数を見るテストが動く可能性がある。**セッションを新設するため、
  既存の `sess-a` / `sess-b` は触らない**

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `agent-layers.md` が 500 行を超える | 節を足す前後で行数を測る。超えるときは決定 25 のとおり `agent-interruptions.md` へ分ける |
| 運転の節を触って #752 の 3 つの現状固定テストを落とす | 末尾の「参照」の直前へ足すだけにする。触るのは `## 持ち場の一覧` の表の列 1 つだけ |
| 既存の `sess-a` を触って #751 の件数のテストを落とす | 新しいセッション（`sess-c` / `sess-d`）を足す |
| `wait-reset` のテストが実時間で眠る | 眠る関数と現在時刻を引数で差し替える。subprocess のテストは「過ぎている」か `--max-sleep 0` だけ |
| 「窓」「親」を書いて `test_the_three_layer_docs_drop_the_old_words` を落とす | 起動した側は `起動元`、量は `context window` と書く |

## 切り戻し手順

追加だけで、既存の振る舞いを変えない。Pull Request を閉じれば元へ戻る。
データの移行・保存先の新設は無い。

## 完了の定義

- [ ] AC40〜AC49 に検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `python3 scripts/check-skill-frontmatter.py` と `python3 scripts/check-doc-line-limit.py` が通る
- [ ] `claude plugin validate .` が終了コード 0
