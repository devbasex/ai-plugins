# 用語集

この文書は `docs/glossary/glossary.json` から `glossary.py render` で作る。手で直さない。

## プロジェクトの用語集（`project-glossary`）

各プロジェクトが持つユビキタス言語。語・意味・コンテキスト・廃止した語・正本

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| ユビキタス言語 | プロジェクトの関係者とエージェントが、要求・設計・コードで同じ意味に使う語の集まり | — | `issues/issue-1111-requirements.md` |
| 用語集 | ユビキタス言語を持つ構造化ファイル（正）と、そこから作る人が読む Markdown の文書。どちらもプロジェクトのリポジトリに置く | — | `issues/issue-1111-requirements.md` |
| コンテキスト | 語の意味が 1 つに決まる範囲（境界づけられたコンテキスト） | — | `issues/issue-1111-requirements.md` |
| ドメインモデルの節 | 設計文書の先頭に置く節。変更が属するコンテキスト・変える集約とその持ち主・不変条件・ドメインイベントを書く | — | `issues/issue-1111-requirements.md` |
| 廃止した語 | 用語集で別の語へ置き換えた語。文書に出たら落とす | — | `issues/issue-1111-requirements.md` |
| 不変条件 | 集約がいつも満たす条件。設計のドメインモデルの節に書き、実装の前にテストにする | — | `issues/issue-1111-requirements.md` |
| 未登録の語 | 用語として書かれているのに用語集に無い語。見出しが「用語」の節の表の 1 列目に書いた語を指す | — | `issues/issue-1111-requirements.md` |

## NDF の開発ワークフロー（`ndf-workflow`）

NDF が配る工程・関門・モード・段の語。development-workflow/references/glossary.md が持つ

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 用語集の宣言 | `.ndf/glossary.json`。用語集の置き場・形式・検査の対象を持つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 語のチェック | `glossary.py check`。用語集の形と、文書の追加した行の語を見る | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| モデルの段 | 設計 PR のレビューの 1 ラウンド目。ドメインモデルの節だけを見る | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 詳細の段 | 設計 PR のレビューの 2 ラウンド目以降。確定したモデルを前提に残りを見る | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 仕様の写し | 課題の本文にある要求を、設計 PR と一緒にコミットする `issues/` のファイル | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |

## NDF の Slack 通知（`ndf-notification`）

利用者の手を待つ時点・その種類・通知の本文と戻り先

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 待ちの通知 | 利用者の回答か承認が無いと進まない時点で、Slack へ送る知らせ | — | `issues/issue-821-wait-notify-design.md` |
| 回答待ち | 利用者に問いへの答え（選択・情報・指示）を求めている待ち | — | `issues/issue-821-wait-notify-design.md` |
| 承認待ち | 利用者に操作の許可（ツールの実行・計画・マージ・配布など）を求めている待ち | — | `issues/issue-821-wait-notify-design.md` |
| 待ちの鍵 | 1 つの待ちを見分ける値。transcript の最後の assistant の項目の `uuid` | — | `issues/issue-821-wait-notify-design.md` |
| 戻り先 | 通知から当該セッションへ戻る手段。ホスト名・cwd の行と、作れればセッションの URL か再開のコマンド | — | `issues/issue-821-wait-notify-design.md` |
