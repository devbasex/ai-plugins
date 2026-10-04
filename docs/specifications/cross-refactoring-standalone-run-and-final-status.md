# cross-refactoring: ai-plugins 以外のリポジトリで手順どおりに単独起動すると止まり、最終ゲートでは未検証のレビューが approved と記録されていた → 対象のリポジトリの中から打てば finalize まで通り、最終ステータスは cross-review の駆動が決めた値だけが記録される

## 目的

- **ai-plugins 以外のリポジトリでも、`SKILL.md` のとおりに打てば単独起動が `assess` から finalize まで通る。**
  対象のリポジトリは打った場所で決まり、決められない場所で打つと別のリポジトリを黙って対象にせずに止まる
- **単独起動の最終ステータスを決める規則は `loop_drive.review_status` の 1 か所だけにある。** 最後の HEAD が承認されて
  いない実行（スイープが未検証など）は配分の履歴へ入らず、Draft も解除されない

例: `example/sample` の Draft の Pull Request 12 を、その clone の中から単独起動する。

| 時点 | 振る舞い |
| --- | --- |
| `assess` | 解決の入口が返した絶対パスの `refactor.py assess --base origin/main` が、clone の差分を見て 0 を返す |
| `drive.py 12 --scope src tests` | 打った場所の git の作業ツリーの根（clone）を対象のリポジトリとして耐久の記録に 1 度だけ残し、init が `example/sample` の PR 12 を取る |
| 最終ゲート（終了コード 23） | `items[0].command` は cross-review の駆動に `--result-file <回答ファイル>` を付けたもの、`items[0].cwd` は clone の根 |
| cross-review の駆動が ok で終わる | スイープが未検証だったため、駆動が回答ファイルへ `{"review_status": "unverified"}` を書く |
| 打ち直し | drive が回答ファイルを読み `finalize --review-status unverified` を打つ。配分の履歴へは足さず、`metrics.review_status` は `unverified`。Draft のまま報告して止まる |
| clone の外（インストール先の Skill のディレクトリなど）で打つ | `assess` は「対象のリポジトリを決められない」と出して 2、drive は耐久の記録を開かずに中断（`metrics.exit` 2） |

**手順は Skill が正である。** ここに書き写さない。

| 何を | 正本 |
| --- | --- |
| 打つ場所・入口のブロック（`RF=` / `LIB=` / `RC=`）・assess の終了コードの読み方 | [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md) の「前提」 |
| drive と `bg-wait.sh` の打ち方・終了コード 23 の扱いと Draft を解除する条件 | 同じ SKILL.md の「実行」 |
| 入口を探すコマンド（`$R` を決める for 文） | [`scripts-lookup.md`](../../plugins/ndf/skills/development-workflow/references/scripts-lookup.md) の「入口を探すコマンド」 |
| 最終ゲートの cross-review から finalize までの流れ・配分の履歴へ足す条件 | [`docs/04-verify-and-report.md`](../../plugins/ndf/skills/cross-refactoring/docs/04-verify-and-report.md) の「単独起動の終わり」「配分テーブル」 |

この文書が扱うのは、Skill に書かない決定の理由、常に成り立つ条件、部品の契約、テスト観点である。

## 用語

定義は [用語集](../glossary.md) が正である（`docs/glossary/glossary.json`）。この文書で使う語は
「単独起動」「対象のリポジトリ」「最終ステータス」「回答ファイル」「最終ゲート」である。

## 対象範囲

| 扱う | 扱わない |
| --- | --- |
| cross-refactoring の単独起動で `assess` と drive が対象のリポジトリを決める仕組み | cross-review の単独起動の打つ場所（別の課題で扱う） |
| 単独起動の最終ゲートで最終ステータスを回答ファイルへ書く担い手と形 | `loop_drive.review_status` の規則そのもの（持ち手を 1 つにするだけ） |
| cross-review の駆動の引数 `--result-file` | supervise から起動される経路の振る舞い（回答ファイルの書き方を共通の関数へ寄せるだけ） |
| | GitHub 以外の origin・配分の履歴にすでに入った行の訂正 |

## 背景

スクリプトは対象のリポジトリを現在のディレクトリから決める（origin・`gh repo view`・`git fetch`・`git worktree add`・
`.ndf/runtimes.json`・状態の置き場）。手順が「Skill のディレクトリで」打つと書いていたため、インストール先
（git リポジトリでない）で打つと `assess` は 2、drive は init で止まった。ai-plugins では Skill のディレクトリが対象の
リポジトリの中にあるため偶然動いていた。

最終ゲートでは、止まりのプロンプトが最終ステータスの規則を文として写し、LLM に cross-review の状態ファイルから
決めさせていた。写した規則は `review_status()` と違い、`final=approved`・`sweep.verified=false` を `approved` と読んだ。
そのまま finalize へ進むと、最後の HEAD が承認されていない実行が配分の履歴へ入り、次の計画の見積りが狂う。

## 決定と理由

- **対象のリポジトリは打つ場所で決め続け、`SKILL.md` をその場所から絶対パスで打つ形に直す。** 直すのは手順の
  打つ場所で、スクリプトの決め方は保つ（一時の git リポジトリの中で絶対パスの `assess` が 0 を返すことを実測した）。
  `--repo` のような引数を足すと、現在のディレクトリを読む 6 か所すべてへ通す変更になり、省いたときは結局現在の
  ディレクトリから決めるため、利用者の打ち方が 2 通りに増える
- **決められないときの検査は共通ライブラリの `proc.git_root` に寄せ、`assess` と drive の入口で 1 度だけ行う。**
  案内の文だけを呼び手が渡せるようにして、cross-review の単独起動も同じ関数を使えるようにする。
  drive は耐久の記録を開く前に検査し、鍵が `<現在のディレクトリ>#<PR>` になる実行を作らない。利用者が打つのは
  `assess` と drive の 2 つで、drive の子は同じ現在のディレクトリで動くため、子の各コマンドへ検査を散らさない
- **最終ステータスは LLM に書かせず、cross-review の駆動が `--result-file` で回答ファイルへ書く。** 値は
  `review_status()` そのもので判断が要らず、LLM に写させると写し違いを防ぐ手が無い。cross-refactoring の drive が
  cross-review の状態ファイルを読む形は、cross-review の作業ディレクトリと状態の置き場の規則を写すことになり、
  その規則は cross-review の単独起動を直すときに変わりうる
- **回答ファイルの形を作る関数は `loop_drive` に 1 つ置き、supervise の書き手も同じ関数を使う。** 書き手が
  cross-review の駆動と supervise の `worker_steps.py` の 2 つになるため、式を 2 か所に置くと片方だけが変わりうる。
  supervise の側は書く値・形・時点を変えない
- **cross-review の駆動を打つ場所は、止まりの `items[0].cwd` に対象のリポジトリの根として載せる。** 機械が読める欄で、
  supervise は `command` を持つ止まりではこの欄を読まないため振る舞いが変わらない。`command` を
  `cd <根> && ...` にすると、supervise が自分の作業場所で打つ文字列まで変わる
- **#1656（最終ステータス）を #1655（打つ場所）の 1 本に取り込む。** どちらも `drive.py` の `pause_review` と
  `SKILL.md` の終了コード 23 の行を書き換え、分けると同じ関数への 2 本の差分になる

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | 回答ファイルに入る最終ステータスは、その cross-review の状態に対する `loop_drive.review_status()` の値と一致する。最終ステータスを導く規則は `review_status` だけが持ち、cross-refactoring の `drive.py`・止まりのプロンプト・`SKILL.md`・`docs/04` は規則を写さない | 一致しない値は書かない。`drive.py` は `review_status` を import しない（`test_module_owners.py` が落とす） |
| I2 | 回答ファイルの中身は `{"review_status": "<値>"}` だけで、作るのは `loop_drive.review_answer` だけである。値が無ければ `unknown` | 形の違う回答ファイルは cross-refactoring の drive が `unknown` として読む |
| I3 | 配分の履歴へ 1 行を足すのは、最終ゲートが通り、単独起動なら最終ステータスが `approved` の実行だけである | `cmd_finalize` が足さずに理由を 1 行出す |
| I4 | Draft を解除するのは、結果 JSON の `metrics.review_status` が `approved` のときだけである | 解除せずに最終ステータスを報告して止まる |
| I5 | 対象のリポジトリは、現在のディレクトリが属する git の作業ツリーの根である。決められなければ別のリポジトリを対象にせず止まる | `assess` は終了コード 2、drive は耐久の記録を開かず子を打たずに中断（`metrics.exit` 2） |
| I6 | supervise の経路の回答ファイルの形と、駆動の終了コードの表（0・23・1）は変わらない | 既存のテストが落とす |

### 対象のリポジトリの根（`refactor_lib/paths.target_repo_root`）

| 項目 | 契約 |
| --- | --- |
| 実体 | `proc.git_root(hint=TARGET_REPO_HINT)`。`git rev-parse --show-toplevel` の値で、下位のディレクトリで打っても根になる |
| 決められないとき | `proc.StepError`（コード 2）。文は `proc.git_root` の既定の文の括弧に `TARGET_REPO_HINT`（`対象のリポジトリを決められない。対象のリポジトリの中で打つ`）を入れたもの |
| `proc.git_root` の `hint` | 括弧の中の案内の文を呼び手が渡す。既定は `--root を渡す`（他の呼び手の振る舞いは変わらない） |
| `assess` | 差分を見る前に呼ぶ。`StepError` なら `ERROR: <文>` を標準エラーへ出して 2。差分は根で取る |
| drive | `run` の先頭（耐久の記録を開く前）で `_target_root` が呼び、`StepError` を `Stop`（2）にする。中断の結果は `status: stopped`・終了コード 1・`metrics.exit` 2 で、`gh` も子も呼ばない |

drive は根を耐久ワークフロー `refactor_drive` の入力 `root` として 1 度だけ記録し、打ち直しでは記録した値を使う。
入力に `root` を持たない旧い記録では、止まりの `cwd` に現在のディレクトリを使う。

### 最終ゲートの止まり（`drive.py` の `pause_review`。終了コード 23）

| 欄 | 中身 |
| --- | --- |
| `items[0].command` | `python3 <cross-review の drive.py の絶対パス> <PR> --focus <観点> --result-file <回答ファイル>` |
| `items[0].cwd` | 対象のリポジトリの根 |
| `items[0].result_file` | 回答ファイル（状態の置き場の `drive-rf<ID>-cross-review.json`） |
| `prompt_file` | `cwd` で `command` を打つこと（gate の間はその `prompt_file` に従って打ち直す）、駆動が ok で終わると回答ファイルを書き自分では書かないこと、この駆動を打ち直すことだけを書く。規則も cross-review の状態ファイルのキーも書かない |

打ち直した drive は回答ファイルを読む（`review_result`）。

| 回答ファイル | 扱い |
| --- | --- |
| 無い | 同じ止まりを番号を増やして返す（cross-review の駆動が中断で終わったときもここ） |
| JSON として読めない | `Stop`（2）で中断 |
| 読める | `review_status` の値（無ければ `unknown`）を `finalize <ID> --review-status <値>` へそのまま渡し、結果の `metrics.review_status` に載せる |

### cross-review の駆動の `--result-file`（`loop_drive.write_review_answer`）

| 項目 | 契約 |
| --- | --- |
| 引数 | `--result-file PATH`。drive 自身の引数として受け、`state.py init` へ渡す残りには入れない。省けば書かない |
| 書く時点 | 結果 JSON を出す直前、`status` が `ok` のとき。終わった収束ループを打ち直して記録した結果を返す経路（`keep_finished`）でも書く。`gate` と `stopped` では書かない |
| 中身 | `review_answer(out["metrics"])`（I2）。`metrics.review_status` は駆動が状態ファイルから `review_status()` で決めた値 |
| 書けないとき | `OSError` なら `⚠ 回答ファイル <パス> を書けなかった: <理由>` を標準エラーへ出し、結果と終了コードは変えない（読む側は回答ファイル無しとして同じ止まりを返す） |

supervise の `worker_steps.py` は `command` を持つ止まりを自分の作業場所で入れ子に回し、終わったら同じ
`review_answer(sub["metrics"])` を `result_file` へ書く。`items[0].cwd` は `command` を持たない止まりでだけ作業場所に使う。

### 状態ファイル・履歴

形は変えない。cross-refactoring の状態ファイルの `final_gate.review_status` に渡された最終ステータスが残り、
`history_written` が真になるのは I3 を満たして配分の履歴へ足せた実行だけである。

## テスト観点

- ai-plugins の外の一時の git リポジトリ（origin が GitHub・差分あり）を現在のディレクトリにして、`assess` を絶対パスで
  打つと 0 か 3、git でないディレクトリでは決められないことを示す文と 2（`plugins/ndf/skills/cross-refactoring/tests/test_assess.py`）
- 同じリポジトリの下位のディレクトリで init を打つと、そのリポジトリの owner/repo で PR を取る
  （`plugins/ndf/skills/cross-refactoring/tests/test_init.py`）
- git でない場所で drive を打つと、子を打たず耐久の記録を開かずに `stopped`・`metrics.exit` 2 になること。
  最終ゲートの止まりの `items[0].cwd` が対象のリポジトリの根（下位のディレクトリで打っても根）で、`command` に
  `--result-file` があること。回答が `unverified` なら finalize へそのまま渡り、結果も `unverified` になること
  （`plugins/ndf/skills/cross-refactoring/tests/test_refactor_drive_resume.py`）
- cross-review の駆動に `--result-file` を渡し、(a) final=approved・sweep.verified=true・remaining_open=0・commit=null、
  (b) sweep.verified=false、(c) sweep.commit あり、(d) final=max_rounds で終えると、回答ファイルの値がそれぞれ
  `approved`・`unverified`・`unverified`・`max_rounds` になること。中断で終わると書かないこと
  （`plugins/ndf/scripts/tests/test_drive_review.py`）
- `review_status` の規則と、`review_answer` が `{"review_status": ...}` だけを返し値が無ければ `unknown` になること
  （`plugins/ndf/scripts/tests/test_lib_loop_drive.py`）
- 単独起動の finalize が `unverified` で配分の履歴へ足さず、`approved` でだけ足すこと
  （`plugins/ndf/skills/cross-refactoring/tests/test_phases_git.py`）
- cross-refactoring の `drive.py` が `review_status` を持たないこと（`plugins/ndf/skills/cross-refactoring/tests/test_module_owners.py`）
- supervise の経路で `command` を持つ止まりを回すと、回答ファイルが内側の `metrics.review_status` を写し、終了コードの表が
  変わらないこと（`plugins/ndf/scripts/tests/test_drive_refactor.py`）
- `SKILL.md` の打つ場所と Draft を解除する条件は `.md` の文言を照合するテストを書かず、レビューで読む

## 関連リンク

- [`cross-refactoring` の SKILL.md](../../plugins/ndf/skills/cross-refactoring/SKILL.md)
- [`docs/04-verify-and-report.md`](../../plugins/ndf/skills/cross-refactoring/docs/04-verify-and-report.md)
- [cross-refactoring-verify-and-final-gate.md](cross-refactoring-verify-and-final-gate.md)（最終ゲートと配分の履歴へ足す時点）
- [cross-refactoring-round-tests-and-assess.md](cross-refactoring-round-tests-and-assess.md)（`assess` の判定）
- [`scripts/lib/loop_drive.py`](../../plugins/ndf/scripts/lib/loop_drive.py)・[`scripts/lib/proc.py`](../../plugins/ndf/scripts/lib/proc.py)
- 課題: #1655（取り込んだもの #1656、関連 #1707）
