---
name: pr-review
description: "Review a PR diff, or the branch diff with --branch, and post an approve or request-changes verdict. Use when reviewing a PR（PRレビュー・マージ前チェック・セルフレビュー）."
argument-hint: "[PR番号 | --branch] [AIエージェント(codex|agy)] [--focus AREA]"
effort: high
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# コードレビューコマンド

PR 差分、または `--branch` 指定時は現在のブランチの差分を、専門家としてレビューする。

## 引数

| 引数 | 意味 | 既定 |
|---|---|---|
| `[PR番号]` | レビュー対象の PR | 今のブランチの PR |
| `--branch` | PR ではなく **ローカルブランチの差分**をレビューする（PR 作成前のセルフレビュー） | OFF |
| `[AIエージェント]` | `codex` / `agy` に委譲。省略時は Claude 自身 | Claude |
| `--focus AREA` | 重点観点（`security` / `performance` / `tests` / 任意の文字列） | なし |

```
/ndf:pr-review                       # 今のブランチの PR をレビュー
/ndf:pr-review 9352                  # PR 番号を指定
/ndf:pr-review 9352 codex            # Codex CLI に委譲
/ndf:pr-review --branch              # ローカルブランチをセルフレビュー
/ndf:pr-review --branch security     # セキュリティに焦点を当ててセルフレビュー
```

## 2 つのモード

| 観点 | PR モード（既定） | `--branch` モード |
|---|---|---|
| 対象 | GitHub 上の PR 差分 | `git diff origin/<ベースブランチ>` の差分 |
| 出力先 | **PR 上にインラインコメント + 総評を投稿** | セッション上の報告のみ（投稿しない） |
| 判定 | `APPROVE` / `REQUEST_CHANGES` / `COMMENT` | 判定を出さず改善提案を返す |
| 用途 | PR 作成後のレビュー | PR 作成前のセルフレビュー |

**どちらのモードでもコード修正は行わない**（分析と指摘のみ。修正は `/ndf:fix`）。

## 観点

**第 1 段（仕様適合）を先に通す。** コード品質だけを見ると、きれいに書かれた「仕様を
満たさない実装」を通してしまう。

### 第 1 段: 仕様適合

| 確認 | 見るもの |
| --- | --- |
| 受け入れ条件を満たすか | プラン・PR 本文の受け入れ条件と、対応するテスト |
| ドメインの不変条件を破っていないか | 状態遷移・数量・期限・権限の扱い。上位層から迂回して不整合な状態を作れないか |
| 対象範囲外の変更がないか | 依頼・プランの範囲と差分の一致。無関係な整形や改名の混入 |
| テストが仕様を表しているか | テスト名と検証内容が受け入れ条件に対応しているか。実装の複製になっていないか |

第 1 段で**満たさない項目があれば、その時点で `REQUEST_CHANGES` とする**。第 2 段の
指摘を積み上げても、仕様を満たさない実装は直しようがない。

受け入れ条件が PR 本文にもプランにも書かれていない場合は、その不在自体を指摘する
（条件がなければ第 1 段のレビューが成立しない）。

### 第 2 段: コード品質

| 確認 | 見るもの |
| --- | --- |
| 責務・凝集度・結合度 | 1 つの単位が複数の変更理由を持っていないか |
| 依存の向き | 業務ロジックが外部の仕組みへ直接依存していないか。循環がないか |
| 可読性・単純性 | 分岐の深さ、名前と実態の一致、不要な抽象化 |
| コードの兆候 | 重複、長すぎる単位、基本型への固執など（`refactoring` の一覧） |
| セキュリティ・性能 | 下の「具体的なチェックポイント」 |
| テストが実装詳細に結合していないか | 内部呼び出し回数の検証、private への直接依存（`tdd-cycle` の脆いテスト） |

### 具体的なチェックポイント（第 2 段の詳細）

- **その言語らしい記述方式**: イディオム・標準ライブラリ・言語機能の活用
- **メモリ効率・演算性能**
  - キャッシュ利用、不要なループ・コピーの排除
  - Python: numpy 利用、内包表記、ジェネレータ
  - PHP: switch 文の map（連想配列）化
  - N+1 クエリ、不要なデータベースアクセス、インデックスの活用
- **関数・メソッド・ファイル行数の適正化**
  - 目安: 関数/メソッド 50 行、ファイル 300 行。ただしプロジェクトの慣例に従う
  - 単一責任原則から外れていないか
- **重複・冗長コードの排除**
  - PR 範囲にこだわらず積極的にまとめるよう指摘
  - 逆に過剰な抽象化（YAGNI 違反）も指摘する
- **柔軟性を損なう定数化の排除**
  - 数字をそのまま定数にするような硬直化を避ける
  - 定数よりも DB の master テーブル、または json/yaml による外部化を検討
- **セキュリティ**
  - SQL インジェクション / XSS / CSRF 対策、入力値バリデーション
  - 認証・認可の適切性、機密情報（トークン、キー、個人情報）の取り扱い
- **エラーハンドリング**
  - 例外が適切に捕捉されているか、リトライ / タイムアウトの設計
  - ログ出力の妥当性（詳細は `/ndf:logging-guidelines`）

`--focus` が指定された場合は、該当する観点を優先し、他の観点は重大なもののみ指摘する。

### 重要度

| 重要度 | 定義 | 後段（`/ndf:fix`）の扱い |
|---|---|---|
| `critical` | セキュリティ・データ破損・本番障害につながる | **必ず自動修正** |
| `major` | 保守性・性能・仕様逸脱の重要問題 | **必ず自動修正** |
| `minor` | 改善推奨（マージを止めない） | **直さず**、見送りの返信を付けて閉じる（`waived`）。中身が指摘の基準（`/ndf:fix` の「重要度の判定」）に当たれば `major` 以上として直す |
| `nit` | 好み・スタイル | **直さず**、見送りの返信を付けて閉じる（`waived`） |

過剰な nit 量産は避ける。critical / major で対応すべき真の問題に集中すること。

## 指摘の振り分け

| 指摘の種類 | 指摘ファイルでの書き方 |
|---|---|
| 特定の行・ファイル単位（行を絞れなければ代表行） | `path` + `line`（インラインコメントになる） |
| 複数ファイルにまたがる設計指摘 | 代表箇所に `path` + `line`、補足は `summary` |
| 設計レベル・PR 全体の所見 | `summary`（総評。個別の指摘を繰り返さない） |
| 第 1 段で満たさない項目 | `stage: "spec"`（総評の冒頭の「仕様適合」に載る） |
| この PR の範囲外 | `/ndf:out-of-scope` で起票し、番号を `summary` へ書く（起票先はその Skill が決める） |

## 手順

集める・判定する・投稿するはスクリプトが行い、LLM はレビューだけを行う。`$R` の決め方は
`development-workflow/references/scripts-lookup.md`。`status` が `stopped` なら `summary` と `items` を
報告して止まる。

```bash
PRR=$(bash "$R/scripts/resolve.sh" scripts pr-review) || exit 3
```

Claude 自身がレビューする（第二引数なし）:

1. `python3 "$PRR/pr-review-steps.py" collect [<PR番号> | --branch] [--focus AREA]` で対象・差分・未解決のスレッドを文脈ファイル（`metrics.context`）に集める
2. 文脈ファイルと差分を読み、「観点」で第 1 段 → 第 2 段の順に見る。第 1 段で満たさない項目が出たら、第 2 段は同じ箇所を直すときに一緒に直すものだけに絞る。未解決のスレッドと同じ趣旨の指摘は出さない。指摘は文脈ファイルの「指摘ファイルの書き方」に従い `metrics.findings` へ書く。重要度の高いものから並べ、良い点は列挙しない
3. `python3 "$PRR/pr-review-steps.py" finish --findings <metrics.findings> (--pr <番号> | --branch)` で判定して投稿する。判定は指摘から決まる（`critical` / `major` / `spec` があれば `REQUEST_CHANGES`、`minor` / `nit` だけなら `COMMENT`、0 件なら `APPROVE`）。自分の PR は `COMMENT` で送られ、本来の判定は `metrics.intent` に残る。`--branch` は投稿せず `metrics.report` に報告を書く

外部 AI への委譲（第二引数 `codex` / `agy`）は `python3 "$PRR/pr-review-steps.py" delegate <codex|agy> [<PR番号> | --branch] [--focus AREA]`
の 1 回で、集める → 「観点」と文脈ファイルからプロンプトを組む → `external-ai.py run --phase review` で上限つきで待つ →
判定して投稿する、を行う。外部 AI は指摘ファイルを書くだけで投稿しない。上限越え・結果なし・読めない指摘ファイルでは
投稿せず非 0 で終わる。**Claude 自身による追加判定は行わず**、外部 AI の指摘と判定をそのまま採用する。

## 作業完了報告（必須）

結果の JSON から、利用エージェント（claude / codex / agy）・`metrics.review_url`・`metrics.intent` と `metrics.posted_as`・
`metrics.by_severity`・総評の要約と PR URL を報告する。指摘の中身は PR 上にあるため繰り返さない。
`--branch` では `metrics.report` の報告をセッション上に出す。レビュー結果は提案であり、最終判断は開発者が行う。

この工程に入ったら進捗記録 `bash "$SCRIPTS/projects-sync.sh" <issue番号> stage "実装レビュー"` を 1 行打つ（issue の本文とボードの両方に残る。`$SCRIPTS` の決め方は `development-workflow` の `references/scripts-lookup.md`、3 層では起動指示の「進捗記録」を使う）。

## 関連

- `/ndf:fix` — レビュー指摘の分類と修正対応
- `/ndf:cross-review` — codex + agy の収束レビュー
- `/ndf:external-ai` — 外部 CLI の起動と上限つきの待ち
- `/ndf:logging-guidelines` — ログ設計
