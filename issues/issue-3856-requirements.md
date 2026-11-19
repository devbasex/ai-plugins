## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで確かめるまで、利用者の決定として扱わない。扱いを書いた行は、設計者が決めた判断として読む。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 原因は、supervisor への起動の指示と `agent-layers.md` の報告の形が、工程ごとに `outputs` に置いてよいキーを supervisor に示していないことである | #3812 の仕上げの supervisor の記録 | 前提 1 |
| S2 | 知る手段は手順書の参照か書き出しの検査のどちらかでよい | 課題の受け入れ条件の 1 行め | 受け入れ条件 1・3（両方を行う） |

## 事象

#3812 の仕上げのフェーズで、supervisor が工程の出力の JSON の `outputs` に規約に無いキーを書いた。conductor の `ndf progress finish` が止まった（2026-11-19、ndf 11.0.8）。

- 振り返り: `outputs: Additional properties are not allowed ('retrospective_comment' was unexpected)`
- 棚卸し: `outputs: Additional properties are not allowed ('apply', 'candidates', 'rank' were unexpected)`

conductor が `outputs` を `{}` にした写しで終えた。`agent-layers.md` は「conductor は出力を自分で組み立てない」と書くので、写しを作ること自体が規則の外にある。

仕上げの supervisor の記録では、起動指示が `agent-layers.md` の規則 15（`ndf progress show` の行の `outputs` のキーに値を入れ、空なら省く）を渡さず、出力の例を `"outputs":{}` だけで示し、フェーズレポートに振り返りのコメントの URL を求めていた。supervisor はその URL と棚卸しの結果ファイルのパスを `outputs` に入れ、`jq -e .`（JSON として読めるか）だけで確かめた。

工程の出力を、ステートマシンへ書かずに `finish` と同じ規則で確かめる手段は無い（`ndf progress finish --help` に試し打ちの引数が無い。`ndf contracts emit --outputs` は結果 JSON の語彙で確かめ、工程の出力の語彙とは違う）。

## 期待する結果

supervisor が工程の出力を書いたときに、その工程の `outputs` に置いてよいキーを知り、規約に合わないことをフェーズレポートを返す前に知る。conductor の `finish` は写しを作らずに通る。

## 再現手順

1. 工程「振り返り」の出力として `{"steps": [], "summary": "x", "outputs": {"retrospective_comment": "https://example.invalid"}}` を書く
2. `ndf progress finish <ステートマシンの ID> "振り返り" --output <そのファイル>` を打つと、`refused` で止まる
3. 書いた側（supervisor）が 2 より前に同じ判定を得る手段が無い

## 環境

NDF 11.0.8（main 2e43af7bc と同じ）・Claude Code・Linux

## 前提

- 前提 1: 原因は起動指示が規則 15 を渡さなかったことと、書いた側が `finish` と同じ規則で確かめる手段を持たないことの 2 つである（S1 と、仕上げの supervisor の記録）
- 前提 2: 工程の出力の規約（`stage-io.schema.json` の `stage-out` と `context.schema.json`）と、工程ごとの定義のキー（`contracts/workflow/stages/<工程>.json`）は変えない
- 前提 3: 文脈のキーにあるが工程が定義しないキー（振り返りの `pr` など）は、今の `finish` のとおり捨てて警告にする（断らない）

## 対象範囲

含む:
- 工程の出力を、ステートマシンを書き換えずに `finish` と同じ規則で確かめる手段
- 確かめた結果に、その工程が置いてよい `outputs` のキーを載せる
- supervisor の規則（`agent-layers.md` の規則 15）と supervisor の定義から、その手段を指す

含まない:
- 工程の出力の規約・定義のキーの変更（前提 2）
- 定義しない文脈のキーを断る変更（前提 3）
- conductor の起動指示を検査する hook

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | supervisor が工程を終えた | 工程 Skill の手順の終わり | — | — |
| E2 | supervisor が工程の出力の JSON を作業ディレクトリへ書いた | E1 | 書けなければ `結果: 止まった` で返す | E1 |
| E3 | supervisor が工程の出力を確かめた | E2 | 規約に合わなければ出力を直して E3 をやり直す | E2 |
| E4 | supervisor がフェーズレポートの `終えた工程` に工程と出力のパスを足した | E3 が規約に合うと返した | — | E3 |
| E5 | conductor が `ndf progress finish` で工程を終えた | フェーズレポートを受けた | 規約に合わなければ `refused` で止まる（E3 を通っていれば起きない） | E4 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 工程の出力の確かめ | 工程の出力の JSON を、ステートマシンへ書かずに `ndf progress finish` と同じ規則で確かめること（`ndf progress check`） |

## 受け入れ条件

- [ ] 1. `ndf progress check <ID> "<工程>" --output <ファイル>` を打つと、規約に合う出力では `status: ok`（終了コード 0）を返し、`metrics` にその工程が置いてよい `outputs` のキーの一覧を載せる
- [ ] 2. 1 で規約に合わない出力（文脈のキーに無いキー・定義のキーの欠け・`summary` の欠け）では、`finish` と同じ誤りの文で `status: stopped`・`cause.kind: refused` を返す。再現手順 1 の出力で `retrospective_comment` を名指す
- [ ] 3. 1・2 のどちらでも、ステートマシンの行の状態と履歴が変わらない（打つ前と後で `ndf progress show` の `items` と `metrics.history` が同じ）
- [ ] 4. 文脈のキーにあるが工程が定義しないキーを置いた出力では、1 の `ok` に、`finish` が捨てるキーを `warnings` で添える
- [ ] 5. `agent-layers.md` の規則 15 が、出力を書いた後に 1 を打ち、`ok` になってから `終えた工程` に足すと書く
- [ ] 6. supervisor の定義（`plugins/ndf/agents/supervisor.md`・`supervisor-waits.md`）が、起動指示に規則が無いときも規則 15 と 1 の確かめを行うと書く
- [ ] 7. 退行しない: `ndf progress finish` の振る舞いと、プランが工程を終えるときの検査（`progress/run.py`）が変わらず、既存の `plugins/ndf/tests/progress/` のテストが CI で通る

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。サブコマンド `ndf progress check` を足す（足すだけで、`finish` の呼び方は変わらない） |
| データ | 変わらない |
| 既存の振る舞い | 変わらない（`finish` とプランの工程の検査） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | PR の CI（`.ndf/project.json` の `test.ci.only`）。`ndf progress` のコマンドを一時のステートマシンで打つテスト |
| 手動確認 | リリース後テストで、supervisor のフェーズの通しの記録に `ndf progress check` の呼び出しがあり、conductor の `finish` が止まらないことを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `ndf progress` は `plugins/ndf/src/ndf/progress/`。規約は `plugins/ndf/contracts/workflow/`。コマンドの一覧は `plugins/ndf/docs/contracts/commands.md` |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。検査は `finish` と同じ関数を使う |
| テスト戦略 | `ndf progress` のコマンドのテスト。`.md` の文言を照らすテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | `check` と `finish` の検査を同じ関数で行う |
| 確認してから行う | 工程の出力の規約の変更（設計承認ゲート） |
| 行わない | conductor の起動指示の検査 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 無し | — | — |

## リクエスト（原文）

> ## 何が起きたか
>
> #3812 の仕上げのフェーズで、supervisor が工程の出力の JSON の `outputs` に規約に無いキーを書いた。conductor の `ndf progress finish` が止まった（2026-11-19、ndf 11.0.8）。
>
> - 振り返り: `outputs: Additional properties are not allowed ('retrospective_comment' was unexpected)`
> - 棚卸し: `outputs: Additional properties are not allowed ('apply', 'candidates', 'rank' were unexpected)`
>
> conductor が `outputs` を `{}` にした写しで終えた。`agent-layers.md` は「conductor は出力を自分で組み立てない」と書くので、写しを作ること自体が規則の外にある。
>
> 原因: supervisor への起動の指示と `agent-layers.md` の「supervisor → conductor」の報告の形が、工程ごとに `outputs` に置いてよいキー（`contracts/workflow/context.schema.json`）を supervisor に示していない。
>
> ## 受け入れ条件
>
> - [ ] supervisor が工程の出力を書くときに、その工程で置いてよい `outputs` のキーを知る手段がある（手順書の参照か、書き出しの検査）
> - [ ] 規約に合わない出力は、supervisor の側でフェーズレポートを返す前に分かる
>
> 起点: https://github.com/devbasex/ai-plugins/issues/3812#issuecomment-7007103154

（書き手: 起票者、日付: 2026-11-19。書き手が利用者でない文は、利用者の指示の原文として扱わない）

