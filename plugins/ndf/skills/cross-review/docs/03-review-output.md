# 03: レビュー出力の制約と運用の切り分け

`SKILL.md` 本体から参照される、**投稿の書式・継続的統合の失敗の分類・過去に踏んだ形**の
一次資料。実行の途中で読む必要は無く、規約を変えるときと進行が止まったときに読む。

レビュー出力の制約は `scripts/launch-codex.sh` / `scripts/launch-gemini.sh` の
プロンプトが同じ内容を持つ。ここは規約の記録である。

## レビュー出力の制約

**目的**: PR 上に Resolve 義務を伴うインラインコメントを増やさない。
**修正アクションを伴わない記述は一切出さない** ことを両 launcher プロンプトで強制する。

### 1. body 先頭 identifier prefix（必須）

人間アカウントから AI が投稿するため、GitHub UI 上では誰のレビューか分からない。
body 先頭に必ず以下を入れる:

```
## 🤖 cross-review | round 1 | codex | REQUEST_CHANGES
```

書式: `## 🤖 cross-review | round <N> | <agent> | <event>`

- `<agent>`: `codex` / `gemini` のいずれか
- `<event>`: AI の本来の判定（`REQUEST_CHANGES` / `APPROVE` / `COMMENT`）
  `posted_as` ではなく `intent` を書く

### 2. インラインコメントの最小化（最重要）

インラインコメントは GitHub 上で **Resolve 操作が必須** になるため、本当に直すものだけ作る:

| 重要度 | インライン化 | 説明 |
|---|---|---|
| `critical` / `major` | ✅ する | 修正必須 |
| `minor` | ✅ する | 明らかな改善のみ。判断が割れるなら出さない |
| `nit` | ❌ **出さない** | 好み・スタイルはコメント化禁止。気になっても無視する |

**1 インラインコメント = 1 修正アクション** を厳守。
コメント本文は `[重要度 / カテゴリ] 修正提案` の 1 文で完結させ、
コード引用ブロック（``` ... ```）や現状説明だけのコメントは作らない。

**インラインは PR の差分に含まれる行にしか付かない。** 差分外の行を指定すると GitHub が
`HTTP 422 Line could not be resolved` を返し、**インラインだけでなくレビュー本体も投稿
されない**（指摘が丸ごと失われ、PR 上には何も残らない）。差分に無い箇所を指摘するときは
body に「ファイル名:行 + 指摘」の形で書く。422 が返ったら該当インラインを body へ移して
再投稿する。

### 3. body（総評）に書かないこと

- ❌ **「良い点」/「Strengths」/「Positives」/「評価できる点」セクション** — 一切書かない
- ❌ 個別ファイル・関数の褒め言葉
- ❌ 「特に問題ありません」「概ね良好です」等の評価文
- ❌ 対応不要な観察コメント（「〜のようです」「〜と思われます」止まり）

body に書くのは **設計レベル・PR 横断の修正提案** のみ。
書くことが無ければ body は `## 🤖 cross-review ...` の prefix 行 + 1 行サマリのみで良い。

### 4. event 判定

- `APPROVE` — 修正必須の指摘なし（minor 以下しか無い場合も APPROVE で良い）
- `REQUEST_CHANGES` — critical / major の指摘あり
- `COMMENT` — **基本使わない**。雑感だけの投稿は禁止

指摘をこの PR の範囲外と判断したときは、`/ndf:out-of-scope` で起票し、番号を返信へ
書いてから resolve する。番号の無い resolve は、指摘した側からは無視と区別できない。

## CI failure の分類（誤中断防止）

「CI 失敗 → 即 `final=error`」は乱暴。`scripts/state.py merge-fix` が
fix 戻り値ファイル (`$TMP_DIR/fix-pr<PR>-result.json`) を受け取った際に
`ci_failed_checks` を以下で分類する:

| 分類 | パターン | 振る舞い |
|---|---|---|
| code-fail | `pint` / `larastan` / `phpstan` / `test` / `lint` / `type` / `build` / `ruff` / `eslint` / `tsc` / `mypy` | `final=error` で中断 (exit 3) |
| meta-only | `check_pr_requirements` / `assignees` / `reviewers` / `labels` / `meta` | `ci_note` に記録して継続 |
| 不明 | 上記以外 | 保守的に **code-fail 扱い** |

PR メタデータ系の check（Assignees / Reviewers / Labels）は **継続**、
pint / larastan / test / build などは **中断** を原則とする。

## アンチパターン

- ❌ **修正をメインセッション内で行う** — context が一気に膨れる。必ずサブエージェント
- ❌ **AI に Markdown だけ返させる** — メインがパース・投稿する設計は禁物。AI 直接投稿
- ❌ **result.json の申告だけで判定を進める** — 投稿が失敗しても件数は残る。GitHub 側の
  実数と突き合わせないと、修正担当が読むべき指摘が存在しないまま収束する
- ❌ **nit を都度ユーザに問う** — ループ中は deferred 記録のみ。最終スイープ (Step 7.5) で Resolve
- ❌ **未解決スレッドを残したまま終了する** — approved/max_rounds 等いずれの終了経路でも
  Step 7.5 の最終スイープを必ず実行し、open review thread 0 で終える。特に **最終 APPROVE
  ラウンドの minor/nit インラインコメント**はループ内 fix を通らないため取りこぼしやすい
- ❌ **`max-rounds` なしで回す** — 無限ループの温床
- ❌ **PR ローテーションを忘れる** — 100+ コメントの巨大 PR になる
- ❌ **light モードで Agent (general-purpose) 呼び出しを省略する** — newtext.json が無いと `rotate-pr.sh execute --mode light` はエラーで止まる。prepare → Agent → execute の 3 段は不可分
- ❌ **light モードで新 PR の title/body に内部用語を漏らす** — 「round N」「rotated」「cross-review」「レビュー指摘で〜」等は禁止 (Agent プロンプトで明示禁止)
- ❌ **newtext.json に旧 PR の title/body をそのままコピーする** — 「現状の差分・実装を反映」が必須。古い説明が残ると後続 PR / 将来のレビュアーが混乱
- ❌ **`rotate-pr.sh` 内から `claude` CLI を呼んで title/body を生成する** — 環境依存・コスト管理外。Agent tool でメイン側から呼ぶ
- ❌ **CI 失敗を一律で中断** — コード関連／メタチェックを分類（上記参照）
- ❌ **自分の PR に `REQUEST_CHANGES` で投稿** — 必ず 422。事前判定 + COMMENT ダウングレード
- ❌ **`gemini --yolo` だけで起動** — trusted directory で YOLO 無効化。`--skip-trust` 併用
- ❌ **`pgrep -fa <prompt>` で完了判定** — gemini は long prompt が引数に乗り検知失敗。pidfile 必須
- ❌ **sentinel 単独で完了判定** — codex がクラッシュすると永遠に出ない。`monitor.py` の多軸判定 (pidfile / sentinel / 早期エラー / stall / hard timeout / result.json) を使うこと
- ❌ **投稿に失敗したまま result.json を書かずに終了する** — 収束ループは前ラウンドの結果を読むか、結果なしで止まる。エラー時ほど `post_error` 付きの result.json が要る（launcher が起動時に前ラウンドの result / payload を消すため、書かれなければ「結果なし」として扱われる）
- ❌ **タイムアウトなしで wait** — ハング検知不能。`monitor.py` の hard timeout (30 分既定) + stall timeout (10 分既定) を必ず効かせる
- ❌ **EARLY_ERROR の曖昧パターンで kill する** — 行頭の生 `Error:` / `Traceback` は codex がレビュー対象 diff の test コード片を echo するケースで誤検知する。明確な致命 (auth / quota / sandbox / HTTP 401-403-429 / gemini の YOLO 降格) **のみ** kill 対象とし、曖昧パターンは警告ログに留める。誤検知が再発する場合は `--no-early-error` / `MONITOR_NO_EARLY_ERROR=1` で検知自体を無効化する (sentinel / result.json / timeout で十分判定可能)

## monitor.py が誤って kill する場合の手順

`monitor.py` が EARLY_ERROR で codex / gemini を即時 kill してしまい、`result.json` が
生成されないケースは以下で切り分け・回避できる:

1. **err.log の冒頭を確認**: 検知パターン (`fatal_err` の `early error (fatal) in err.log: ...`) が
   本当に致命なのか、それとも diff body の echo / config validation 警告なのかを判別
   - **v4.11.0 で benign 自動判定を強化**: `_match_is_quoted()` が backtick / 「」 に加え
     **ダブル/シングルクォート文字列リテラル** (`"quota exceeded: ..."`) を、`EARLY_ERROR_BENIGN`
     が **grep 形式のソース引用行** (`path/to/file.py:22:    <code>`) を自動で benign 扱いする。
     codex が tests/*.py 等のテスト用文字列 (`"quota exceeded"`, `"sandbox error"`) を
     レビュー中に echo しても誤 kill しなくなった（旧版で PR #23 round 2 に発生した事例）
2. **gemini の `Error in: mcpServers.<name>` 警告**: `.gemini/settings.json` に `disabled: false`
   等の非互換キーがあると毎回出る。`launch-gemini.sh` の sanitize ロジック (v4.7.2+) で
   自動退避するため、最新版にアップデートすれば解消する
3. **誤検知が継続する場合**: `monitor.py --no-early-error` (もしくは `MONITOR_NO_EARLY_ERROR=1`
   環境変数) で EARLY_ERROR 検知自体を無効化し、hard timeout / stall / sentinel / result.json
   のみで判定するモードに切り替える
4. **新しい致命パターンを観測した場合**: `EARLY_ERROR_FATAL` に追記する (PR で plugin に反映)。
   曖昧パターンは `EARLY_ERROR_WARN` 側に置き、kill 対象にはしない
- ❌ **fix サブエージェントが Resolve をスキップ** — reply だけでは未対応扱い。Resolve まで実行
- ❌ **review body に identifier prefix を付け忘れる** — GitHub UI 上で誰のレビューか不明になる

