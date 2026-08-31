import json

code = """# 例 (ループ先頭に追加):
if [ "$c" = '\\\\' ] && [ "$quote" != "'" ]; then
  cur+="$c"
  i=\\$((i + 1))
  [ "$i" -lt "$n" ] && cur+="${s:i:1}"
  continue
fi"""
code = code.replace("\\$", "$")

payload = {
  "body": "## 🤖 cross-review | round 12 | gemini | REQUEST_CHANGES\n\n`_wt_tokenize` の字句解析において、バックスラッシュ (`\\\\`) によるエスケープが考慮されていない不具合が見つかりました。\nこれにより、エスケープされた `\\\\\"` や `\\\\)` が誤解釈され、後続の `cd` 追跡が壊れる（作業ツリー外の誤検知・検知漏れにつながる）重大なリスクがあります。インラインコメントにて修正方針を提案しています。",
  "event": "COMMENT",
  "comments": [
    {
      "path": "plugins/ndf/scripts/lib/worktree-common.sh",
      "line": 260,
      "body": "[critical / 正確性]\nこの関数の字句解析で、バックスラッシュ（`\\\\`）によるエスケープが考慮されていません。\n現状では `\"` の中で `\\\\\"` が出現すると文字列が閉じられたと誤認され、また外側の `\\\\)` が部分シェルの終わり（`__WT_SUBSHELL_END__`）として誤認されてしまいます。これにより、以降の `cd` の状態追跡が壊れ、書き込み先の誤検知や検知漏れが生じるリスクがあります。\n\nシングルクォート内を除き、`\\\\` の次の 1 文字を評価対象からスキップする処理を追加してください。\n\n```bash\n" + code + "\n```"
    }
  ]
}

with open("post_payload.json", "w") as f:
    json.dump(payload, f, ensure_ascii=False)
with open("post_payload.json", "r") as f:
    data = json.load(f)
data["commit_id"] = "356991212e376eff3e29164a8b308e4621762e42"
with open("post_payload.json", "w") as f:
    json.dump(data, f, ensure_ascii=False)
