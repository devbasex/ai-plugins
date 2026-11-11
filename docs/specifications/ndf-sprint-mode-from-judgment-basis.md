# 開発ループのスプリントのモード: 課題ごとの判定の根拠から決め、課題を外したら決め直す

## 概要

開発ループ（`--loop`）のループの 1 回は、課題ごとの判定の根拠がそろわない限り組まれない。conductor は課題ごとに
判定表の行を選んで判定の根拠のファイルに書き、`loop sprint` がその根拠からスプリントのモードを算出して記録に残す。
設計承認ゲートで課題を外したときは、記録に残った根拠から残りの課題のモードを決め直し、下がれば設計の結果から後の
ステージを新しいモードで作り直す。

判定の材料（触るファイルの候補・公開インタフェースの変化・テストのファイル）は
[mode-materials.py](../../plugins/ndf/scripts/mode-materials.py) が課題の本文と設計文書から集める。行を選ぶのは conductor である。

`loop sprint` の引数・終了コード・ループの記録の項目の一覧と、ループの全体の流れの正本は
[ndf-workflow-development-loop.md](ndf-workflow-development-loop.md) である。この文書は、判定の根拠の契約・常に成り立つ条件・
材料を集める規則・決め直しの規則を持つ。

## 用語

語の定義は用語集（ [docs/glossary.md](../glossary.md) ）が正である。この文書で使う主な語は次のとおり。

| 用語 | この文書での使い方 |
| --- | --- |
| 判定表 | `development-workflow` の SKILL.md の「上から順に条件を判定する」の表。順 1〜5 とモードの対応を [lib/mode_judgment.py](../../plugins/ndf/scripts/lib/mode_judgment.py) の `ROWS` が持つ |
| 判定の根拠 | 1 つの課題の `{number, row, mode, condition, files}`。ループの記録の `judgments[]` にそのまま残る |
| 判定の材料 | `mode-materials.py` が課題ごとに出す事実。記録には持たず、呼ぶたびに本文と設計文書から作り直す |
| スプリントのモード | ループの 1 回の `mode`。判定の根拠のうち順が最も小さい行のモード |
| 判定の根拠のファイル | conductor が埋めて `loop sprint --judgments` に渡す JSON のファイル。既定の置き場はループの 1 回のディレクトリの `judgments.json` |
| モードの決め直し | 設計承認ゲートで課題を外したとき、残った課題の判定の根拠からスプリントのモードを決め直すこと |

## 背景

lproc のループの 13 周はすべて `standard` で組まれ、判定の根拠はどこにも残っていなかった。課題を外して 1 件だけに
なった周も、最初のモードのまま続いた。値だけを受け取る形では、根拠の無いモードで設計・cross-refactoring・
cross-review・全体テストが掛かる（要求: https://github.com/devbasex/ai-plugins/issues/2923 ）。

| 決めたこと | 理由 |
| --- | --- |
| 判定の根拠は JSON のファイルで `loop sprint` に渡す | 1 件の根拠は順・モード・条件の文・触るファイルの 4 つを持ち、課題が増えると値が多くなる。ファイルなら材料のスクリプトの下書きを埋めてそのまま渡せ、渡した形が記録の形と 1 対 1 になる。条件の文やパスを 1 つの引数に詰める区切りの規則が要らない |
| スプリントのモードは根拠から `loop sprint` が算出し、`--mode` は照合だけに使う | 判定表は上の行が勝つ規則で、課題ごとの行が決まればモードは機械的に決まる。conductor に計算させると根拠と食い違う値を渡せる。`--mode` を残すのは、conductor が自分の判定を明示して照合させるため |
| 判定表の行を選ぶのは conductor、材料を集めるのはスクリプト | 条件に当たるかは課題の中身を読む判断である。スクリプトは推した値を事実として渡さない |
| 決め直しは、`loop sprint` で記録した根拠から読む | `loop sprint` が選んだ課題すべての根拠を記録するため、残った課題の根拠は必ず手元にある。機械が進める設計承認ゲートの処理が conductor の入力を待たずに済む |
| 下がったときは続きのステージを `supervise.py new sprint --designed` で作り直す | モードはプランの組み立て（リファクタリングのステップ・設計との突き合わせ・リリース承認ゲートの判定）に効き、値だけを書き換えても工程は変わらない。組み立ての規則を `new sprint` の 1 か所に保つ |
| モードが同じときは作り直さない | 変わらない場合に既存の続きの組み立てをそのまま使う |
| 課題を外したときに下げるのは、変更の分割に当たるため | `workflow-modes.md` の「途中でモードが変わったとき」は、下げたいときは変更そのものを分割するとする。課題を外すことはこの分割に当たる。工程が重いという理由では下げない |
| 触るファイルの候補は code span とリンクの宛先からだけ取る | 課題の本文はパスを code span かリンクで書く。地の文からパスらしい語を推すと識別子が混じる。コードブロックの中は CommonMark の code span でないため、再現手順のコマンドが候補に入らない |
| 課題の本文の「進行」の `モード:` の行はループのスクリプトが書く | 通過記録が進め方を書くのは最初に進め方を持つプラン（実装）の時点で、設計の間は `モード: —` のままになる。決めた時点で書けば、設計の工程の間から読める |
| 判定の根拠を持たないループの 1 回は決め直さない | 版を上げた時点で進行中のループの 1 回は根拠を持たない。断るとそのループが設計承認ゲートの後で止まる |

## 仕様

### 打つ順と担い手

| # | 担い手 | 打つもの | 中身 |
| --- | --- | --- | --- |
| 1 | conductor | `loop select` | 選んだ課題と、次に打つ 2 つのコマンドの案内を受ける |
| 2 | conductor | `mode-materials.py --issue <選んだ課題>... --out <ループの 1 回のディレクトリ>/judgments.json` | 材料と、判定の根拠のファイルの下書きを受ける |
| 3 | conductor | （判断） | 課題ごとに判定表の行を選び、下書きの `row`・`mode`・`condition` を埋め、`files` を確かめる。対象外の課題は `loop exclude` で外す |
| 4 | conductor | `sprint-state.py loop sprint <ループ名> --judgments <ファイル>` | 検査を通ればモードを算出して組み、記録と課題の本文に書く |
| 5 | 設計承認ゲートの処理 | （`loop gate` / `loop lap --done` の続きの組み立て） | 課題を外したら残りの根拠でモードを決め直す |

手順書の具体例は [loop.md](../../plugins/ndf/skills/development-workflow/references/loop.md) の「ループの 1 回ごとに打つ順」と
「課題を外した後のモード」にある。

### 判定の材料を集める規則

材料のスクリプトは課題ごとに次を出す。判定表の行は選ばない。

| 項目 | 規則 |
| --- | --- |
| 種類（`kind`） | 本文の `## 種類` の節の最初の空でない行。節が無ければ `null` |
| 公開インタフェース（`public_interface`） | 本文の `## 影響` の節の表で、1 列目が `公開インタフェース` の行の 2 列目。`変わる` / `変わらない` で始まればその値、ほかは `記載なし`。セルの字面を `cell` に添える |
| 触るファイルの候補（`files[]`） | 下の手順で取ったパス。`exists`・`source`（`本文` か `設計`）・`section`（見つけた節の見出し、設計なら設計文書のパス）・`tests` を持つ |
| 候補の件数（`files_found`） | `files` の件数。0 のときは 0 と出し、推して埋めない |

触るファイルの候補を取る手順:

1. 本文の code span の中身とリンクの宛先（`#` から後を除く）を、出てくる順に集める。フェンスのコードブロックとインデントのコードブロックの中は読まない。表のセルの中の code span は読む
2. 前後の空白・先頭の `./`・末尾の行番号（`:120` や `:12-34`）を外す
3. 空白を含む・`-` か `$` か `#` で始まる・`://` を含むものは捨てる（コマンド・引数・アンカー・URL）
4. `git ls-files` に同じパスがあるか、そのパスの下にファイルがあれば `exists: true` で採る
5. 当たらないもののうち、`/` を含み、拡張子を持つか `/` で終わるものを `exists: false` で採る（新しく作るファイル）。ほかは捨てる
6. 設計文書（`issues/issue-<番号>-design*.md`）があれば、「設計の結果」の表のその課題の行の「触るファイル」を `source: 設計` で足す
7. 同じパスは最初に見つけた 1 件にまとめる

テストのファイル（`tests`）は、存在するファイルにだけ付ける。テストのファイル自身（`tests/` の下か、
`test_<名>.*`・`<名>_test.*`・`<名>.test.*`・`<名>.spec.*` の名前）なら自分だけ、ほかは拡張子を除いた名前 `<stem>` について
`test_<stem>.*`・`<stem>_test.*`・`<stem>.test.*`・`<stem>.spec.*` の名前の、`git ls-files` にあるファイルである。

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | `loop sprint` で組んだループの 1 回は、選んだ課題すべてに判定の根拠を 1 件ずつ持ち、選んでいない課題の根拠と同じ課題の 2 件目を持たない | `loop sprint` が終了コード 1 で断り、記録を書き換えず、`new sprint` を打たない。理由は `items` に 1 件ずつ |
| I2 | 根拠の `row` は 1〜5 の整数で、`mode` はその行のモードと一致し、`condition` と `files` が空でない | 同上 |
| I3 | スプリントのモードは根拠のうち `row` が最も小さい行のモードに等しい。`--mode` を渡したときはその値とも等しい | 同上 |
| I4 | `documentation`（順 2）の根拠と他のモードの根拠が 1 回に混ざらない | 同上。理由に混ざった課題の番号をモードごとに並べる |
| I5 | 決め直したモードは残った課題の根拠だけから I3 と同じ規則で決まり、決め直す前より上の行にならない | 続きを組まず、`refused`（step は `続きの queue`）で止まる |
| I6 | 決め直しの後に流す続きのプランとまとめのプランのモードは、ループの 1 回の `mode` と等しい | 作り直しが断れば `mode`・`manifest`・`mode_decisions` を変えずに `refused` で止まる |
| I7 | 判定の根拠を持たないループの 1 回（判定の根拠を導入する前に組んだもの）は、読み込み・再開・続きの組み立てで失敗せず、決め直しもモードの決定の追加もしない | — |
| I8 | 判定の材料は本文と設計文書から取れた候補だけを出し、取れなければ 0 件と出す | テストが落ちる |

課題を外しても、混ざらない集合の部分集合は混ざらないため、I4 は決め直しでは破れない。

### モードの決め直し

決め直しは、設計承認ゲートの処理が課題を人の判断待ちへ移したとき（判定が「従う」以外、または設計が完了していない課題を
除いたとき）に、続きの queue を組む関数 `continuation`（[lib/sprint_loop_gate.py](../../plugins/ndf/scripts/lib/sprint_loop_gate.py)）
の中で行う。`loop sprint` の前の `loop exclude` は決め直しに当たらない。その時点ではモードが決まっておらず、外した後の集合で
材料から組み直すためである。

決め直しの時点では queue は設計承認ゲートで止まっており、ゲートの後のステージ（設計の結果・スプリントブランチ・実装・検査・
開発版・本番）はどれも流れていない。そのため決め直したモードはゲートの後のすべてのステージに当たり、下げられない工程まで
進んでいる場合は生じない。

| モード | 続きの組み立て | 記録と課題の本文 |
| --- | --- | --- |
| 同じ | 組み済みのマニフェストのゲートの後のステージから外した課題を除き、まとめを足す | `mode_decisions` に同じモードの 1 件を足す。本文の `モード:` の行は書き直さない（`loop sprint` が書き済み） |
| 下がった | `supervise.py new sprint --pace auto --state <状態> --name <スプリント> --worktree <根> --issue <残り> --mode <新しいモード> --designed <残り> [--version <開発版>] --out <ループの 1 回のディレクトリ>/sprint-<k> [--outside-plan <理由>] <sprint_extra>` で設計の結果から本番までを作り直し、まとめも新しいモードで組む | 組めてから `mode`・`manifest`・`dev_plan`・`prod_plan`・`sprint_command` を書き換え、`mode_decisions` に 1 件足し、残った課題の本文の `モード:` の行を書く |

`--design` を渡さないため、作り直しで組まれるのは設計の結果から本番までのステージで、ゲートの後のステージと同じ並びになる。
計画の外の理由（`--outside-plan`）は、ループの記録に `outside_plan` があるときだけ付ける。続きを組み直す `loop start` の再開も同じ関数を通るため、
決め直しの記録は組み直しが通ったときにだけ足される。

## データ・設定

### 判定の根拠のファイル

```json
{
  "issues": [
    {
      "number": 2748,
      "row": 3,
      "mode": "standard",
      "condition": "公開インタフェース（API・イベント・コマンド）の追加・変更・削除",
      "files": ["plugins/ndf/scripts/lib/sprint_loop.py"]
    }
  ]
}
```

- `mode-materials.py --out` の下書きは `row`・`mode` を `null`、`condition` を空の文字列、`files` を `exists: true` の候補で書き、`materials` に材料を添える。埋める前の下書きは I2 で断られる
- ループの 1 回を組む処理は 5 つの項目だけを読み、`materials` を含むほかの項目は捨てる。`condition` と `files` の前後の空白は除いて記録する
- 書き手と読み手は契約 [contracts/files/judgments-json.json](../../plugins/ndf/contracts/files/judgments-json.json) が持つ

### `mode-materials.py` の結果

```text
python3 mode-materials.py --issue <番号>... [--root <リポジトリ>] [--out <判定の根拠のファイル>]
```

| 終了コード | 結果 |
| --- | --- |
| 0 | `ok`。`items` に課題ごとの材料、`metrics` に `issues`・`files_found`（課題 → 件数）・`out`。候補 0 件の課題は要約に並ぶ |
| 1 | `stopped`。本文を読めない課題があり、`gaps` に課題と理由を載せる。下書きは書かない |
| 2 | 引数の誤り・`git ls-files` の失敗 |

エントリポイントの契約は [contracts/entrypoints/plugins__ndf__scripts__mode-materials.py.json](../../plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__mode-materials.py.json) にある。

### ループの記録の項目

ループの記録（`.ndf/loop/<ループ名>/loop.json`）の `laps[]` に足す 3 項目は省ける項目で、記録の `version` は上げず、移行もしない。

| 項目 | 形 | 書く時点 |
| --- | --- | --- |
| `judgments` | 根拠の配列（課題の番号の順） | `loop sprint` |
| `mode_decisions` | `{at, trigger, issues, from_mode, mode}` の配列。`trigger` は `loop sprint` か `設計承認ゲート`、`issues` はその時点の課題、`from_mode` は `loop sprint` の行で `null` | `loop sprint`（1 件目）、決め直しのたび |
| `sprint_extra` | `loop sprint` の `--` の後の引数 | `loop sprint`（作り直しの `new sprint` へ渡し直す） |

今のスプリントのモードは `mode` が持ち、過去の値は `mode_decisions` に残る。状態の表示（`loop status` / `loop list`）が読むのは `mode` だけである。
`loop sprint` の結果の `metrics` は `mode` と `judgments`（課題の番号 → 順）を持つ。

## 運用

- 判定の根拠で断られたら、結果の `items` の理由を読んで根拠のファイルを直し、`loop sprint` を打ち直す
- I4 で断られたら、一方のモードの課題を `loop exclude` で外し、別のループの 1 回へ回す。`loop exclude` は空いた枠へ順位の後ろの課題を補充するため、出力の `metrics.selected` で選んだ課題を取り直し、外した課題の根拠を除いて新しい課題の根拠を足してから打ち直す（手順は [loop.md](../../plugins/ndf/skills/development-workflow/references/loop.md) の「判定の根拠で断られたとき」）
- 本文の `モード:` の行を書けなくても止めない。`loop sprint` の結果の `items` に `note` で載る
- ループの外のスプリント（`supervise.py new sprint --mode`）と、ループの外の判定の記録の場所（`workflow-modes.md` の「判定を記録する場所」）は変わらない
- 判定表の行と条件は変えない。過去のループの 1 回の記録は書き直さない

## テスト観点

単体テストは [test_mode_judgment.py](../../plugins/ndf/scripts/tests/test_mode_judgment.py) と
[test_sprint_loop_mode.py](../../plugins/ndf/scripts/tests/test_sprint_loop_mode.py) にある。

- 選んだ課題の 1 件の根拠を欠いたファイル・ファイルが無い・JSON として読めないときに、`loop sprint` が終了コード 1 で断り、ループの記録のバイト列が変わらず、`new sprint` を打たないこと
- 選んでいない課題の根拠・同じ課題の 2 件目・順が 1〜5 の外・順とモードの不一致・空の `condition`・空の `files` のそれぞれで断ること
- `standard`（順 3）と `light`（順 5）の根拠で組むと、`mode` が `standard`、`judgments` が 2 件、`mode_decisions` が `trigger: loop sprint` の 1 件で、`new sprint` に `--mode standard` が渡ること
- 渡した `--mode` が根拠から決まるモードと違うときに断ること
- `documentation` と `standard` の根拠で断り、理由に両方の課題の番号が出ること。`documentation` だけなら通ること
- code span とリンクの宛先のパスが候補に出て、`exists`・`tests`・`public_interface`・`kind` が付き、設計文書の触るファイルが `source: 設計` で足されること
- 候補の無い本文で `files_found` が 0 で `files` が空になり、コードブロックの中のパス・`--mode` のような語・URL が候補に入らないこと
- 本文を読めない課題があると終了コード 1 で `gaps` に載ること。埋める前の下書きが `loop sprint` の検査を通らないこと
- `standard` と `light` の 2 件で組んで `standard` の課題を外すと、`mode` が `light`、`mode_decisions` の最後が時刻・残った課題・`from_mode: standard`・`light` を持ち、作り直しの `new sprint` に `--mode light --designed <残り>` が渡って `--design` が無く、まとめのモードも `light` になること
- `standard` の 2 件で組んで 1 件を外すと、`mode` は `standard` のまま、同じモードの決定が 1 件足され、`new sprint` を呼び直さず、本文へ書き直さないこと
- 作り直しの `new sprint` が断ると、`mode`・`manifest`・`mode_decisions` が変わらず `refused` で止まること
- 残った課題の根拠が無い・上の行になる入力で決め直しが失敗すること
- `loop sprint` と、モードが下がった決め直しで、課題ごとに `projects-sync.sh <課題> mode <モード>` が打たれ、失敗しても終了コード 0 で `note` が出ること
- `judgments` の無いループの 1 回で、設計承認ゲートの続きが今と同じに通り、`mode_decisions` が足されないこと
- ループの外の `new sprint --mode` の組み立てのテスト（[test_supervise_new.py](../../plugins/ndf/scripts/tests/test_supervise_new.py)・[test_supervise_pace_auto.py](../../plugins/ndf/scripts/tests/test_supervise_pace_auto.py)）が根拠なしで通ること

リリースの後に確かめる方法:

- 次の開発ループの 1 周で、`.ndf/loop/<ループ名>/loop.json` のそのループの 1 回に `judgments` と `mode_decisions` が残り、課題の本文の「進行」の `モード:` の行が `—` でないことを見る
- 設計承認ゲートで課題を外してモードが下がった周で、本物の `supervise.py` が `--designed` だけの `new sprint` を断らず、作り直したマニフェストのステージがゲートの後のステージと同じ並びになることを見る（単体テストは `new sprint` を偽の実行で置き換えている）
- 版を `--dev` で明示したループの 1 回で作り直すとき、同じ `--version` を渡し直して組めることを見る

## 関連リンク

- 要求: https://github.com/devbasex/ai-plugins/issues/2923 （設計 https://github.com/devbasex/ai-plugins/pull/2943 ・実装 https://github.com/devbasex/ai-plugins/pull/2971 ）
- 開発ループの全体と `loop sprint` の引数・記録の項目: [ndf-workflow-development-loop.md](ndf-workflow-development-loop.md)
- 設計承認ゲートで外した課題と続きのステージ: [ndf-parallel-lane-prod-stage-and-dropped-issues.md](ndf-parallel-lane-prod-stage-and-dropped-issues.md)
- 判定表: [development-workflow の SKILL.md](../../plugins/ndf/skills/development-workflow/SKILL.md)、モードを途中で変える規則: [workflow-modes.md](../../plugins/ndf/skills/development-workflow/references/workflow-modes.md)
