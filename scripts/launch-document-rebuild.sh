#!/usr/bin/env bash
# 工程「設計」「ドキュメント再構成」の文書の組み直しを worker へ出す（.ndf/project.json の workflow.launch.rebuild）。
# NDF が rebuild の既定を持ったら（#4337）、設定とこのファイルを消す。
#
# 使い方: launch-document-rebuild.sh --out <結果のファイル>
#   作業場所（カレントディレクトリ）の origin/main からの差分にある issues/ の設計の文書を組み直してコミットする。
#   内容を足さずに見つけた「足すべき内容」は、結果 JSON の items に {"id", "text"} で残る。
set -euo pipefail

if [ "$#" -ne 2 ] || [ "$1" != "--out" ]; then
  echo "使い方: $0 --out <結果のファイル>" >&2
  exit 2
fi
out=$2
docs=$(git diff --name-only --diff-filter=d origin/main...HEAD -- 'issues/*.md')
if [ -z "$docs" ]; then
  exec ndf contracts emit --tool rebuild --status ok --summary "組み直す設計の文書が差分に無い" --out "$out"
fi

work="$(dirname "$out")/rebuild-work"
mkdir -p "$work"
cat > "$work/prompt.md" <<EOF
document-rebuild の Skill に従い、次の文書を組み直して作業場所でコミットする。

$docs

- 章立てと順序を直し、文を短くし、document-style の規則で整える。内容（決定・値・条件・範囲）を足さない・変えない
- 要求仕様のコピー（issues/issue-*-requirements.md）は組み直さない
- push と Pull Request の作成・更新はしない
- 組み直す所が無ければコミットしない
- 内容を足さずに見つけた「足すべき内容」は、結果ファイルの items に 1 件ずつ {"id": "R1", "text": "<どの文書に何が足りないか>"} で残す（無ければ空）
EOF
exec ndf worker run --kind 修正 --prompt-file "$work/prompt.md" --result-file "$out" --cwd "$PWD" --state-dir "$work"
