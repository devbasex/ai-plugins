## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | `not_run` の行がある課題は、閉じるかを人が決める（閉じてよいとだけ返さない） | `close_decide.py` の 25 行目のコメントと、#3812 を開いたまま残した conductor の判断 | 受け入れ条件 1 |
| S2 | 3 つの文書と実装の閉じる条件をそろえる | 本文の「何が起きたか」の表 | 受け入れ条件 3（文書を `close-check` の振る舞いへそろえる） |

## 目的

- スプリントを閉じるとき、リリース後テストで保留（`not_run`）にした条件を持つ課題が、確かめていないまま閉じられない
- `close-check` の結果だけを読む conductor・supervisor が、「閉じてよい」「閉じない」「人が決める」を見分けられる

## 事象

#3812 のスプリントを閉じるとき、リリース後テストの記録で #3812 の受け入れ条件 1 を `not_run`（保留）にした。`ndf records close-check 3812 --verification <記録> --require` は「#3812 を閉じてよい: リリース後テストに不合格の行が無い」を返した（2026-11-19 20:10 JST ごろ、ndf 11.0.8）。

文書と実装が 3 通りに食い違っている。

| どこ | 何と書いているか |
| --- | --- |
| `plugins/ndf/skills/release-verification/SKILL.md` | 「スプリント課題を閉じる条件は、本文の受け入れ条件の番号のすべてに合格の行があることを見る」 |
| `plugins/ndf/skills/progress-tracking/SKILL.md` の「閉じる条件」の 2 | 「その課題の行に不合格（`failed`）が無い」 |
| `plugins/ndf/src/ndf/domains/records/close_decide.py` の 25 行目 | 「not_run（打てなかった）は閉じるかを人が決めるので数えない」。`ok` を返すので、人が決める場面であることが結果に出ない |

conductor は今回、#3812 を開いたまま残した（条件 1 は 11.0.9 の後に確かめる）。

## 期待する結果

- `not_run` の行があり `failed` の行が無い課題で、`close-check` が「人が決める」と読める結果を返す
- 3 つの閉じる条件の書き方が 1 つにそろう

## 環境

NDF 11.0.8・Claude Code・Linux

## 前提

- 前提 1: 課題を閉じる判定が読むのはリリース後テストの記録の JSON（`issues[]` の `number`・`result`）だけで、受け入れ条件の番号を持たない。「受け入れ条件の番号のすべてに合格の行がある」はこの JSON からは判定できない
- 前提 2: `close-check` を打つ supervisor は人へ問えない。「人が決める」課題は閉じずに残し、理由を報告へ載せる（今の `ok` でない結果と同じ扱い）

## 対象範囲

含む:
- `ndf records close-check` の `not_run` の扱い（結果の `status`・`items[]`・`summary`）
- `release-verification` と `progress-tracking` の閉じる条件の書き方

含まない:
- 記録の JSON に受け入れ条件の番号を足すこと（スキーマの変更）
- 記録に課題の行が 1 行も無いときの扱い（今と同じく `ok`。範囲外の課題として #3860 に起票した）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | リリース後テストの記録を書いた | `release-verification` | — | リリース |
| E2 | 課題を閉じてよいかを判定した | 「スプリントを閉じる」の手順 3 の `close-check` | 記録が読めなければ `precondition` | E1 |
| E3 | 人が決める課題を開いたまま残した | E2 が「人が決める」を返した | — | E2 |

## 用語

| 用語 | 意味 |
| --- | --- |
| リリース後テストの記録 | 用語集のとおり（`ndf-release`） |
| スプリント課題 | 用語集のとおり（`ndf-workflow`） |

## 受け入れ条件

- [ ] `not_run` の行がある課題で、`close-check` の結果から「人が決める」ことが読める（閉じてよいとだけ返さない）
- [ ] release-verification と progress-tracking の閉じる条件の書き方が、`close-check` の振る舞いと一致する
- [ ] `failed` の行がある課題は、`not_run` の行の有無に依らず今と同じく `stopped`（`refused`）で閉じない。`passed` の行だけの課題と、記録に行の無い課題は今と同じく `ok` を返す（退行しない）

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる: `close-check` が `not_run` の行のある課題に `gate`（終了コード 10）を返す。今まで `ok`（0）だった |
| データ | 変わらない |
| 既存の振る舞い | 「スプリントを閉じる」で、保留の行を持つ課題が閉じられずに残る（今は閉じてよいと返る） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/domains/records -q -n 4` |
| 静的解析・型検査 | リポジトリの CI |

## 由来

起点: #3812 のスプリントの閉じ（https://github.com/devbasex/ai-plugins/issues/3812 ）

## リクエスト（原文）

> #3855 `ndf records close-check` が not_run（保留）の課題に「閉じてよい」を返す。結果から人が決めることが読めるようにし、release-verification と progress-tracking の閉じる条件の書き方を振る舞いと一致させる（plugins/ndf/src/ndf/domains/records/close_decide.py）

（書き手: AI の起動指示（conductor、計画 p8-5 のレーン lclose）、日付: 2026-11-19。利用者の指示の原文として扱わない。何が起きたか（事象）と受け入れ条件の 1・2 は起票者の本文のまま、3 は要求の工程で足した）
