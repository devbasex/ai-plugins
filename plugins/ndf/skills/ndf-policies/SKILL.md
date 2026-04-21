---
name: ndf-policies
description: |
  NDFプラグインの基本ポリシー。
  応答・ドキュメント・コミットメッセージは日本語。
  mainブランチへの直接push/merge禁止（featureブランチ+PR必須）。
  commit/push/PR mergeはユーザー確認後に実行。
  コンテキスト節約: ファイル全体を読む前にSerenaのシンボル概要を確認。
  複雑タスクはndf:directorに委譲。専門タスクは対応エージェントに直接委譲。
  知識はdocs/に、手順はskills/に配置（AGENTS.mdを肥大化させない）。
  Serena memoryは使用禁止。知識管理は3層構造（AGENTS.md/docs/skills）で行う。
user-invocable: false
---

# NDFポリシー

このスキルはNDFプラグインの基本ポリシーを定義します。
descriptionフィールドが常時コンテキストに注入されるため、本文の参照は不要です。
