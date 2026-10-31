# 人へ問うか、AI が決めるか（問い合わせの区分）

この文書が、`AskUserQuestion` で人へ問ってよい場面と、問いに付ける区分の正本である。承認資料の中身は `approval-request.md`、判断を求めるときの材料の示し方は `decision-request` の Skill が持つ。どちらも区分の表を写さずにここを指す。

## 例

設計の途中で「ライブラリ A と B のどちらを使うか」を問おうとした。これは下の「区分」のどれにも当たらない。問わずに、根拠を集めて推奨の案を選び、次のコマンドで記録して進む。

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ask.py" decide \
  --question "ライブラリ A と B のどちらを使うか" --answer "A" --reason "既存の依存に入っていて、B は保守が止まっている"
```

設計 Pull Request のマージの承認は承認ゲート 1 である。`AskUserQuestion` の入力に `"metadata": {"source": "ndf-gate:1"}` を付けて問う。

## 原則: AI が自分で決める

人へ回すのは、AI が決めてよい範囲の外にある問いだけにする。**迷った・情報が足りない・好みが分かれるは、人へ回す理由にならない。** その場合は根拠を集めて推奨の案を選び、理由を記録して進む。

AI が決める例:

- 設計の選択・Tool とライブラリの選択・命名・ファイルの置き場
- テストの方針・範囲・順序、実装の順序、並列の数
- 範囲外の課題の扱い（`out-of-scope` で起票して進む）、課題を分ける・まとめる
- レビューの指摘を直すか退けるか（理由を記録する）
- 曖昧な依頼の解釈（最も筋の通る解釈を選び、選んだ理由を記録する）

## 区分

`AskUserQuestion` の `metadata.source` に、問いの区分を 1 つ付ける。区分の無い問いは hook が止める。

| 区分（`metadata.source`） | 当たるもの | 宣言で外せるか |
| --- | --- | --- |
| `ndf-gate:1` | 承認ゲート 1（設計 Pull Request のマージ。承認ゲート 1 の委任の承認を含む） | 外せない |
| `ndf-gate:2` | 承認ゲート 2（本番系へ届く操作・制作物承認） | 外せない |
| `ndf-ask:redline:<番号>` | 共通原則の C1〜C8・プロジェクトの P・スプリントの R に当たる操作の承認。`<番号>` は `C4`・`P1`・`R2` の形 | 外せない |
| `ndf-ask:cost` | お金が出ていく: 有料の資源の起動・有料の外部 API・購入・プランの変更。`cost.threshold_usd` 以下は AI が決める | 外せる |
| `ndf-ask:irreversible` | 取り消せない操作のうちレッドラインに当たらないもの: 外部の人への送信（メール・チャット・Issue での言及）・外部の系のデータの削除・公開範囲を広げる操作 | 外せる |
| `ndf-ask:human-action` | 人にしかできない操作: 対話のログイン・二要素認証・権限の付与・秘密の発行と登録 | 外せる |
| `ndf-ask:legal` | 規約・契約・ライセンス・個人情報の扱いへの同意 | 外せる |
| `ndf-ask:<名前>` | プロジェクトが `.ndf/ask.json` の `extra` に書いた区分 | — |

**レッドラインに当たる操作は、`irreversible`・`human-action` より先に `ndf-ask:redline:<番号>` を使う。** 例: 強制 push や `-D` の削除は `redline:C4`、秘密の登録は `redline:C1`、他のリポジトリへの起票は `redline:C5`、`.ndf/` の書き換えは `redline:C7`。

1 つの問いに複数の区分が当たるときは、表の上にあるものを付ける。どれに当たるかが中身で変わる手順では、区分ごとに場合を分け、どれにも当たらなければ AI が決める。

## 止められたとき

hook が問いを止めると、理由に止めた理由と記録の手段（`ask.py decide --ref <id>`）が載る。まず、問いが上の表のどれかに当たるかを見直す。当たるのに区分を付け忘れていた（特にレッドラインの操作の承認）ときは、当たる区分を付けて出し直す。当たらないのに区分を付けて出し直さない。当たらなければ自分で決め、載っているコマンドで記録して続ける。

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ask.py" decide --ref <id> --answer "<選んだ答え>" --reason "<理由>"
```

手順が「AI が決める」とした場面（hook に止められる前に決めた場面）は、`--ref` の代わりに `--question` で問いを書く。

## 問い合わせの宣言 `.ndf/ask.json`

プロジェクトごとに hook の働き方と人へ回す区分を変える。NDF はこのファイルを読むだけで、書かない。

```json
{
  "version": 1,
  "mode": "enforce",
  "allow": ["cost", "irreversible", "human-action", "legal"],
  "cost": {"threshold_usd": 0},
  "extra": [
    {"name": "customer-contact", "description": "顧客の担当者へ連絡するかどうか"}
  ]
}
```

| 項目 | 無いとき | 意味 |
| --- | --- | --- |
| `version` | 読めない扱い | `1` |
| `mode` | `enforce` | `enforce`（止める）/ `advise`（止めずに案内だけ出す）/ `off`（hook を働かせない） |
| `allow` | 4 つすべて | 人へ回してよい組み込みの区分。空配列は「1 つも許さない」。承認ゲートとレッドラインは書かなくても通る |
| `cost.threshold_usd` | `0` | これ以下の費用は AI が決める（hook は額を比べない） |
| `extra` | 無し | プロジェクトが足す区分。`name` は小文字・数字・`-` で、組み込みの名前と `redline` に重ねない |

- ファイルが無いリポジトリでも `enforce` で働く。止めたくないリポジトリは `"mode": "off"` を書く
- 1 か所でも形が誤っていれば宣言全体を既定（`enforce`・4 区分・しきい値 `0`）で扱い、誤った箇所を止めた理由と案内に出す
- 置き場は、作業ディレクトリから親へ辿って `.git` を持つ最初のディレクトリの `.ndf/ask.json`（worktree では worktree の中）

## 記録

hook の判定と代わりの判断は `${NDF_MVV_STATE_DIR:-~/.local/state/ndf}/ask.jsonl` へ 1 行ずつ追記する。振り返りでは次で数える。

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ask.py" summary [--since <ISO 8601>] [--cwd <パス>]
```

区分ごと・判定ごとの件数と、止めたのに代わりの判断が無い問いの件数が出る。区分を広く付けて人へ回し続けていないかを、ここで見直す。

## ランタイムごとの扱い

hook で止めるのは Claude Code だけである。Codex CLI・agy・Kiro CLI では、この文書の規則（区分に当たらない問いは出さず、決めて `ask.py decide --question` で記録する）だけで守る。区分に当たる問いは本文か各ランタイムの問い合わせの Tool で出し、区分の名前を問いの先頭に書く。確かめた結果は `docs/ndf-plugin-reference.md` の hook の節にある。
