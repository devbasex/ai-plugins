---
name: logging-guidelines
description: "Choose log levels and write safe, useful application logs when adding or reworking logging in code（ログ追加 / logger / ログレベル / デバッグログ / エラーログ / print文をログに）. Use when editing source code that emits logs, to pick the level and keep secrets and personal data out of the output."
paths:
  - "**/*.py"
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.js"
  - "**/*.jsx"
  - "**/*.go"
  - "**/*.rb"
  - "**/*.java"
  - "**/*.kt"
  - "**/*.php"
  - "**/*.rs"
  - "**/*.sh"
---

# ログ運用ガイドライン

コードにログを追加・修正する際は、以下のルールに従うこと。言語/フレームワークに依存しない原則として記述している。

## ログレベルの選択基準

| レベル | 用途 | 本番出力（推奨） |
|--------|------|---------------|
| `error` | 例外発生、処理失敗 | o |
| `warning` | データ不備でスキップ、処理継続可能な異常 | o |
| `info` | バッチ開始/完了、重要なビジネスイベント | 環境による（本番off推奨） |
| `debug` | 開発向けデバッグ情報 | x |

**推奨**: 本番は `LOG_LEVEL=warning` 以上。info/debug は開発・ステージングのみで出力する。

## 使用を避けるログレベル

以下は用途が曖昧または過剰なため、明示的な運用規則がない限り使わない:

- `notice` — error/warning/info と区別が曖昧
- `critical`, `alert`, `emergency` — 通常のアプリには過剰。運用規則として「PagerDuty起動基準」などが定義されていない限り使わない

## ループ内ログのルール

### 原則: ループ内では info/warning を出力しない

ループ内で1件ずつログを出力すると、大量データ処理時にログが爆発する。ループ後にサマリーとしてまとめて出力すること。

### サマリーログ化パターン（擬似コード）

```
# NG: ループ内で1件ずつ出力
for item in items:
    log.info("処理完了", id=item.id)

# OK: ループ後にまとめて出力
processed_count = 0
for item in items:
    # 処理...
    processed_count += 1
log.info("バッチ処理完了", processed_count=processed_count)
```

### エラー蓄積パターン

ループ内で例外が発生し処理を継続する場合は、エラー情報を蓄積してループ後にまとめて報告する。先頭N件のみ含めることで、ログサイズ爆発を防ぐ。

```
errors = []
for item in items:
    try:
        process(item)
    except Exception as e:
        errors.append({"id": item.id, "error": str(e)})

if errors:
    log.error(
        "処理で一部失敗",
        total_count=len(items),
        failed_count=len(errors),
        sample_errors=errors[:10],  # 先頭10件のみ
    )
```

### ループ内 debug も必要最小限

ループ内での debug 出力は、他に代替手段がなく調査に不可欠な場合のみ許容。デフォルトは「ループ外で件数サマリ」を基本とする。

## 例外処理のルール

1. **例外は最上位でログ出力** — エントリポイント（コマンド/コントローラー/ジョブ）で catch してログ出力
2. **再スロー時はログ不要** — 上位で出力されるため二重出力を避ける
3. **例外を握りつぶさない** — catch後に何も報告せず続行するのは禁止
4. **広めの例外型で捕捉** — 言語の最上位例外型（Python `Exception`、PHP `Throwable`、Java `Throwable` 等）でトップレベル catch する

## 必須ルール

1. **コンテキスト情報を含める** — 調査に必要なID等を構造化ログとして渡す
2. **機密情報を含めない** — パスワード、トークン、クレジットカード番号、個人特定情報は禁止
3. **メッセージは明確に** — 何が起きたか分かる言葉で記述（プロジェクトの言語ポリシーに従う）
4. **ロガー呼び出しを統一** — プロジェクトで統一ファサード/クライアントを使う（例: Laravel は `Log::`, Python は `logging.getLogger(__name__)`）
5. **グローバル/暗黙の名前空間を使わない** — 明示的にimport/useする

## ログとメトリクスの使い分け

- **ログ**: 個別のイベント、エラー、コンテキスト情報（構造化ログ）
- **メトリクス**: 件数、レイテンシ、成功/失敗率の集計（Prometheus/DataDog等）
- **トレース**: リクエスト横断の実行フロー（OpenTelemetry等）

ループ件数カウントなどは、ログではなくメトリクスに寄せるのが望ましい場合が多い。

## アンチパターン一覧

| アンチパターン | 問題 |
|--------------|------|
| `log.info("")` / 空メッセージ | 意図が伝わらない |
| `log.error(e)` のみ | スタックトレース/contextが欠ける |
| 機密情報をそのままログに入れる | 情報漏洩リスク |
| ループ内で毎回 info 出力 | ログ爆発 |
| try/except で握りつぶし、何も報告しない | 障害の気配を消す |
| 複数行の ASCII ART をログに含める | grep/集計が困難 |

## 関連スキル

- `/ndf:problem-solving` — ログから根本原因を特定する手順
- `/ndf:investigation-rules` — ログをエビデンスとして扱う際の注意点
