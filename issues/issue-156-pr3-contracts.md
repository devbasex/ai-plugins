# 156-3: 入出力の契約

**この設計の本体は `issues/issue-156-pr3-design.md` にある。** 本体が 500 行の上限に達した
ため、最も大きい節をこちらへ分けた（`markdown-writing` の分割の基準）。**この文書は契約
だけを持つ。** 概要・機能一覧・構成要素・処理の流れ・収束の判定・非機能の実現方式・決定の
記録・テスト設計・未確認のまま残ることは [本体](issue-156-pr3-design.md) にある。

## 指摘の識別子（`finding_id`）

**反証も実行の結果も、指摘 1 件へ結ぶ。** 結ぶ先を指す値が要る。`review_findings[]` の
要素は `pr` / `round` / `agent` を持つが（`state.py:2379-2382`）、同じ担当の同じラウンドの中で
1 件を指す値を持たない。

**`finding_id` を取り込みの時点で採番する。** 形は `<agent>-r<round>-<索引>` で、索引は
その担当の `payload.json` の `comments[]` の並びである。取り込みは
`(pr, round, agent)` の組ごとに入れ替える（`state.py:2372-2376`）ため、再実行しても
同じ指摘へ同じ値が付く。

```json
{"finding_id": "codex-r3-0", "pr": 539, "round": 3, "agent": "codex"}
```

## 重複の統合（`_merge_duplicates`）

**同じ指摘を複数の担当が出したとき、1 件へ束ねる。** 読む値は振動の検知と同じ 3 つ組
（`_finding_keys`、`state.py:2668-2709` の `(ファイル, 行, 正規化した本文)`）である。
**ただし結び方は同じにしない。**

| 判定 | 結び方 | 誤って結んだときに何が起きるか |
| --- | --- | --- |
| 振動の検知（`state.py:2745-2749`） | 位置・近傍（`OSCILLATION_NEAR_LINES` 以内）・本文の**いずれか** | 重複率が 1 件ぶん動く。閾値 0.5 の中で薄まる |
| 統合（この設計） | 近傍**かつ**本文（同じファイル・行差 3 以内・正規化本文が一致） | 代表以外が区分と収束の判定から外れ、**別の修正事項が消える** |

**誤りの代償が違うため、条件を同じにしない。** 近傍だけで結ぶと、`api.py:40` の null
入力と `api.py:42` の認可漏れが 1 件へ束ねられる。**過剰な統合は指摘を失うが、統合し
損ねても失われるものは無い**（両方が区分に載り、`origin_runtimes` が 1 者ずつになる
だけである）。非対称であるため、厳しい側へ倒す。

**本文が一致しない候補は統合しない。** 近傍で当たっただけの組は `duplicate_candidates`
へ両方の `finding_id` を残し、反証で相互に `duplicate` が付いたときに当ラウンドの
2 段目として統合する（後述）。**位置の一致は候補の抽出までである。**

ラウンドをまたぐ一致を見る振動の検知に対し、統合は**同じラウンドの中**で見る。

| キー | 何を持つか |
| --- | --- |
| `origin_runtimes` | その指摘を出した担当の一覧。統合しても消さない |
| `finding_id` | 束ねた先の代表 1 件の値。代表は**先に取り込まれた担当**の指摘とする（`origin_runtimes` の先頭）。**重要度・`verification`・根拠は代表の値ではなく組の集約を採る**（後述） |
| `evidence_from` | 代表が持つ。集約した `evidence` と `falsification` の出所の `finding_id`。代表自身が根拠を持つときは代表の値 |
| `merged_from` | 代表が持つ。束ねられた側の `finding_id` の一覧 |
| `merged_into` | **束ねられた側が持つ。**代表の `finding_id`。代表は持たない |
| `duplicate_candidates` | 近傍で当たったが本文が一致せず、統合しなかった相手の `finding_id` |

```json
{"finding_id": "codex-r3-0", "origin_runtimes": ["codex", "kiro"],
 "merged_from": ["kiro-r3-2"]}
{"finding_id": "kiro-r3-2", "origin_runtimes": ["kiro"], "merged_into": "codex-r3-0"}
```

**束ねられた側は消さない。** `merged_into` へ代表の `finding_id` を書いて残す。消すと、反証の
結果ファイルがその `finding_id` を指したときに結び先を失う。判定が読むのは代表の 1 件である。

**代表は組の集約を持つ。重要度は最も高いもの、実行の結果は `reproduced` > `not_reproduced` >
`not_run` の順で最初に当たった 1 件である**（[本体](issue-156-pr3-design.md)の決定 2 の向き）。取り込みの順序で決めると、
`minor` が代表の組は再現しても `verified_non_blocking` へ落ち、`not_reproduced` と `not_run`
の組は代表が `not_run` になって順 3 を素通りし、順 4 で `needs_human_judgment` へ昇格する。
実行検証は束ねた組の全員の `suggested_check` を対象にし、出所は `verification.finding_id` へ
残す。**この集約は 1 段目にも 2 段目にも掛かる。ただし 2 段目では実行し直さない。**

**実行検証が走るのは 1 段目の後の 1 度だけである。** 2 段目は反証の後にあり、その時点では
組の全員が `verification` を持っている（1 段目で束ねなかった側も、`finding_id` ごとに実行の
対象になっている）。**2 段目の集約は、記録済みの `verification.result` を上の順で選び直す
だけである。** 実行し直すと、同じ `suggested_check` を 2 度走らせたうえに、反証を返した
担当が読んだ結果と、区分を決めるときの結果が食い違いうる。

**根拠も同じ向きで集約する。組のいずれかが `has_evidence` を持てば、代表も持つ。**
`has_evidence` は `evidence` と `falsification` の**両方**が空でないときだけ真になる派生の
項目で、取り込みのときに付く（`issues/issue-156-design.md` の「指摘の取り込み」）。統合の
条件は同じファイル・行差 3 以内・正規化本文の一致であり、**根拠の有無は本文の一致に含まれ
ない**。同じ主張へ片方だけが根拠と反証条件を書いた組は普通に起こる。

代表の値だけを読むと、根拠を書いた側が先に取り込まれたかどうかで区分が変わる。組の全員が
`not_run`・重要度が `major`・`origin_runtimes` が 2 者のとき、代表が偽なら順 4 の「根拠を
持ち」に当たらず `insufficient_evidence` へ落ちて収束し、真なら `needs_human_judgment` に
なる。**最も強い一致である全員一致が、取り込みの順序だけで収束する。** 重要度と
`verification` を集約するのと同じ理由で、根拠も順序に依存させない。

**写すのは真偽だけではない。`evidence` と `falsification` の本文も、対のまま代表へ写す。**
真偽だけを引き継ぐと、修正の担当と次のラウンドのレビュワーが読む代表の要素に、根拠の本文が
無いまま「根拠あり」の印だけが付く。

**対は同じ要素から採る。** `has_evidence` が真である要素を組から 1 つ選び、その要素の
`evidence` と `falsification` を両方写して、出所を `evidence_from` へ残す。選び方は
`origin_runtimes` の順で最初に当たった 1 件である。**別々の要素から片方ずつ集めない。**
`evidence` だけを書いた要素と `falsification` だけを書いた要素はどちらも `has_evidence` が
偽であり、両者を継ぎ合わせると、誰も書いていない組を根拠として作り出すことになる。

**独立して出したかを区別する。** `origin_runtimes` の長さは「同じ指摘へ到達した担当の
数」であり、反証の `support` の数とは別に数える。支持は他者の指摘を読んだうえでの賛成で、
こちらは読まずに同じ結論へ達したことを表す。

**1 段目の統合は反証より前に行う。** 後にすると、束ねられる 2 件へ別々に反証が付き、
どちらの値を採るかという判断が増える。担当の申告による 2 段目は反証の後に置く（後述）。

## 実行検証（`cmd_verify_findings`）

**実行してよいのは、起動した側が渡したコマンドだけである。**

| 受け取り方 | 何を渡すか |
| --- | --- |
| `--verify-command CMD` | 実行を許すコマンド。**繰り返し指定できる。トークン列として読む** |

```bash
/ndf:cross-review 123 --verify-command "pytest" --verify-command "uv run --with pytest pytest"
```

**渡されなければ実行検証を行わない。** 行わなかったことを記録へ残し、区分は根拠と反証で
決める。**新しい実行系は導入しない。**

**宣言ファイルを新設しない。** `cross-refactoring` は同じ性質の値を `--baseline-test` の
引数で受け取っており、**同じものを 2 つの Skill が別の方法で受け取る形にしない**。

**引数にすると、いつ・誰が・どう決めるかが起動の時点で決まる。** 宣言ファイルは置き場所を
決めても、**作る手段と作る時期が別に要る**。`.ndf/projects.json` は雛形が手順書にあるだけで
作る手段が無く、利用者が盤面の番号を自分で調べてファイルを書くことになっている。同じ形を
増やさない。

### 照合はトークン単位で行う

**文字列の前方一致では照合しない。** `--verify-command "pytest"` に対し `pytest-danger --evil` が
一致してしまう。渡されたコマンドと `suggested_check` の両方を `shlex.split` でトークン列にし、
**渡されたコマンドのトークン列が `suggested_check` のトークン列の先頭と全要素で一致する**ことを求める。

```console
$ python3 -c "import shlex; print(shlex.split('pytest-danger --evil'))"
['pytest-danger', '--evil']
$ python3 -c "import shlex; print(shlex.split('pytest; rm -rf /'))"
['pytest;', 'rm', '-rf', '/']
```

**実測のとおり、区切りのメタ文字はトークンの一部になる。** `pytest; rm -rf /` の先頭
トークンは `pytest;` であり、渡された `pytest` と一致しない。**メタ文字の一覧を持たない。**
弾く文字を列挙する方式は、列挙から漏れた文字がそのまま通る。

**渡されたコマンドより後ろのトークンは、`-` で始まらないものだけを許す。** トークン境界だけでは
`pytest -c /tmp/evil.ini` や `pytest -p reviewer_plugin` が通り、レビュワーが書いた
文字列で任意の設定ファイルとプラグインを読み込ませられる。実行してよいのは、対象を絞る
引数（テストの位置指定）までである。

### 位置指定は作業ツリーの中に限る

**`-` で始まらないことだけでは足りない。** 位置指定は `pytest` が import する対象で
あるため、`pytest ../external/test_payload.py` や `pytest /tmp/evil.py` を通すと、
レビュワーが書いた文字列でリポジトリの外の任意のコードを実行できる。

**後ろの各トークンを実行時の作業ディレクトリから解決し、その配下に収まることを求める。**
作業ディレクトリはその Pull Request の作業ツリーの根である。**解決は
`os.path.realpath` で行う。`os.path.abspath` は symlink をたどらないため使わない。**

```console
$ ln -s ../outside/evil.py repo/link.py     # 作業ツリーの中から外を指す
$ python3 -c "import os; r=os.path.realpath('repo')
print(os.path.realpath('repo/link.py').startswith(r+os.sep))"   # realpath
False
$ python3 -c "import os; r=os.path.realpath('repo')
print(os.path.abspath('repo/link.py').startswith(r+os.sep))"    # abspath は見逃す
True
```

**`::` を含むトークンはファイル名の部分だけを解決する。** `tests/t.py::test_x` は
ファイルとテスト ID を `::` で結ぶ形である。

**実行は `shell=False` で行う。** トークン列をそのまま渡し、シェルを経由させない。
経由させなければ、`$(...)` や `` ` `` を含むトークンは展開されずに引数として渡る。

`review_findings[]` の各要素へ `verification` を足す。

```json
{"verification": {"command": "pytest tests/test_foo.py::test_null_input", "exit_code": 1,
                  "result": "reproduced", "finding_id": "kiro-r3-2", "ran_at": "..."}}
```

| `result` | 決まり方 |
| --- | --- |
| `reproduced` | 実行が終わり、終了コードが**再現の終了コード**に一致した |
| `not_reproduced` | 実行が終わり、終了コードが 0 |
| `not_run` | `--verify-command` が渡されていない / トークン照合に当たらない / 渡されたコマンドより後ろに `-` で始まるトークンがある / **位置指定が作業ツリーの外を指す** / **位置指定を 1 つも持たない** / **上記のどちらでもない終了コード** / **上限時間で打ち切った** / **起動に失敗した** |

### 「0 でない」を再現としない

**再現とみなす終了コードは `--verify-exit-code` で渡す。** 既定は `[1]` である。0 以外をすべて再現として
扱うと、指摘とは無関係な失敗まで「指摘のとおり壊れている」ことになる。

```bash
/ndf:cross-review 123 --verify-command "pytest" --verify-exit-code 1
```

pytest の終了コードを実測すると、**バグの再現と、指摘の書き誤りが別の値で分かれる**。

```console
$ uv run --with pytest pytest test_ng.py   >/dev/null 2>&1; echo $?   # テストが失敗
1
$ uv run --with pytest pytest test_missing.py >/dev/null 2>&1; echo $?  # ファイルが無い
4
$ uv run --with pytest pytest test_ok.py::test_absent >/dev/null 2>&1; echo $?  # テスト ID が無い
4
$ uv run --with pytest pytest -k 'nothing_matches' test_ok.py >/dev/null 2>&1; echo $?  # 収集 0 件
5
```

**4 と 5 は、指摘が正しいことの証拠にならない。** レビュワーが書いた `suggested_check` の
対象が存在しないか、引数が誤っているだけである。既定 `[1]` はこれらを `not_run` へ落とす。
**終了コードの意味はランナーごとに違う**ため、値は起動する側が渡す。

**打ち切りと起動の失敗も `reproduced` にしない。** `subprocess.run` は上限時間で
`TimeoutExpired` を、コマンドが無いときは `OSError`（`FileNotFoundError` /
`PermissionError`）を送出するため、**終了コードを読む前に例外で分かれる**。例外で分かれた
経路は `not_run` とし、`reason` へどの理由であったかを残す。

**再現の向きに注意する。** 指摘は「壊れている」という主張であるため、**テストが失敗する
ことが再現である。**

### 終了コードが答えるのは、書かれた検証手順の成否だけである

**対象を絞らない実行は、指摘と無関係な失敗を拾う。** 実測では、対象のテストが通って
いても同じ範囲に落ちるテストが 1 つあれば 1 を返す。**位置指定を 1 つも持たない値を
`not_run` とするのはこのためである。**

```console
$ uv run --with pytest pytest -q >/dev/null 2>&1; echo $?              # 対象は通るが無関係が落ちる
1
$ uv run --with pytest pytest -q test_target.py >/dev/null 2>&1; echo $?  # 対象だけ
0
```

**それでも「実行した対象が、その指摘を検査しているか」は機械では確かめられない。**
ここは `suggested_check` を**指摘を出した担当自身が書く**ことに支えている。反証条件と
検証手順は指摘の一部であり（`issues/issue-156-design.md`）、自分が「これで分かる」と
提案者が書いた手順が 0 を返したなら、その形では主張が成り立たなかったことになる。**他者が
書いた手順で棄却される経路は無い。** 実行した値は `verification.command` へ残し、
`rejection_reason` から読めるようにする。

## 反証（`cmd_collect_critiques`）

**提案者以外の担当が、各指摘へ 1 つの値を返す。**

| 値 | 意味 |
| --- | --- |
| `support` | 根拠を独立に確認できた |
| `refute` | 反例・仕様・コードから誤りを示せる |
| `insufficient_evidence` | 可能性はあるが立証できない |
| `duplicate` | 別の指摘と同一。相手の `finding_id` を `duplicate_of` へ書く |
| `out_of_scope` | この Pull Request の範囲・目的から外れる |

**結果は担当ごとに 1 ファイルへ書く。** 名前は
`<agent>-critique-pr<PR>-round<ラウンド>.json` とする。既存の
`<agent>-review-pr<PR>-round<ラウンド>-payload.json`（`state.py:881-882`）と同じ位置
（`_resolve_tmp_dir(pr)`）に置き、同じ組み立て方に揃える。

```json
{"critiques": [
  {"finding_id": "codex-r3-0", "verdict": "refute",
   "reason": "handle() は呼び出し前に None を弾く（api.py:70）"}
]}
```

`cmd_collect_critiques` は `finding_id` で突き合わせ、`review_findings[]` の該当要素へ
`critiques` を足す。積むときに `agent` を添えるが、**その値はファイル名から採る**。本文の
申告を採ると、別の担当を名乗った値をそのまま数えることになる。

**`finding_id` が既知でない要素は捨てず、`unmatched_critiques` へ残す。** 黙って捨てると、
反証が 0 件のラウンドと、結び先を誤ったラウンドが同じに見える。

**自分の指摘へは返さない。** 自己支持を数に入れると、1 者が出した指摘が常に 1 票を持つ。
統合された指摘では `origin_runtimes` に載る担当すべてが提案者であり、いずれも返さない。

## 区分（`_classify_finding`）

**上から順に見て、最初に当たった区分を採る。**

| 順 | 区分 | 条件 |
| --- | --- | --- |
| 1 | `verified_blocking` | `verification.result` が `reproduced`、かつ重要度が `major` 以上 |
| 2 | `verified_non_blocking` | `verification.result` が `reproduced`、かつ重要度が `minor` 以下 |
| 3 | `rejected` | `verification.result` が `not_reproduced`、または `refute` が 1 件以上ある |
| 4 | `needs_human_judgment` | 根拠を持ち、重要度が `major` 以上で、`support` が 1 件以上**または** `origin_runtimes` が 2 者以上 |
| 5 | `insufficient_evidence` | 上のいずれにも当たらない |

**区分ごとの行き先は[本体](issue-156-pr3-design.md)の「収束の判定」にある。** 数える 2 つは
当ラウンドの修正の工程へ、残る 3 つは記録と最終スイープへ渡る。

**実行で再現した指摘を先に採るのは、順序そのもので決定 2 を表すためである。** 再現した
指摘へ `refute` が付いていても、順 1 と順 2 が先に当たるため `rejected` へ落ちない。
#156 の受け入れ条件「実行で再現した指摘は、支持した担当が少数でも棄却されない」がこの
向きを求めている。**順 3 を先に置くと、機械が再現した事実を担当の再評価が覆す。**

**`refute` が効くのは再現していない指摘だけである。** 反証は「なぜ成り立たないか」を
示すもので、支持の「確かにそう見える」より確かめられる形をしている。ただし実行の結果が
`reproduced` のときは、より確かな根拠が既にあるため見ない。

**`not_run` は実行の結果を持たないものとして扱う。** `reproduced` でも `not_reproduced`
でもないため、順 1・順 2 と順 3 の前半（`not_reproduced`）はいずれも当たらず、区分は
`refute` の有無と根拠で決まる。**実行できなかったことを、再現しなかったことと同じにしない。**

**`needs_human_judgment` は `major` 以上に限る。** この区分は収束の判定が数える
（[本体](issue-156-pr3-design.md)の「収束の判定」）ため、重要度を問わないと `minor` の指摘へ `support` が 1 件付いただけで
ラウンドが増える。PR #157 の round 6 で `minor` 2 件だけの `REQUEST_CHANGES` が出た事象が
これにあたる。**`minor` 以下で支持を得た指摘は `insufficient_evidence` へ落ちる**が、
記録には残るため修正の担当は読める。実行で再現した `minor` を `verified_non_blocking` へ
分け、収束の判定から外す扱いと揃う。**`minor` は区分を問わず新規性へ入らない。**

**独立に到達した担当の数も、支持と並べて数える。** レビュワーは 2 者である
（`cross-review` の `SKILL.md`「レビュワーの母集合」）。2 者が同じ指摘を独立に出すと
`origin_runtimes` が 2 者になり、**提案者以外が 1 人も残らないため `support` は必ず
0 件になる**（「自分の指摘へは返さない」）。支持の数だけを見ると、最も強い一致である
**全員一致が `insufficient_evidence` へ落ちて収束する。** `origin_runtimes` の長さを
別に数える（前述）のはこのためである。

**順 4 のこの分岐は、ラウンドの担当が 2 者であることに支えられている。** 順 3 は順 4 より
先に当たるため、提案者以外が 1 人でも `refute` を返した指摘は `rejected` になり、順 4 へ
届かない。担当が 2 者のとき、`origin_runtimes` が 2 者の指摘には提案者以外が残らないため、
`refute` は必ず 0 件になる。**全員一致が単一の `refute` で覆る状態は、いまの母集合では
起こらない。** 1 ラウンドの担当を 3 者以上へ広げるときは、順 3 と順 4 の順序を決め直す
（[本体](issue-156-pr3-design.md)の「未確認のまま残ること」）。**この変更では広げない。**

**棄却の理由を残す。** `rejected` の要素へ `rejection_reason` を書く（実行の結果か、
`refute` の理由）。

### `duplicate` と `out_of_scope` は区分を決めない

**5 つの値のうち、区分の条件に現れるのは `support` と `refute` の 2 つだけである。**
残りの 3 つは、区分ではなく別の行き先を持つ。

| 値 | 何が起きるか |
| --- | --- |
| `insufficient_evidence` | 数えない。支持でも反証でもないため、区分は他の担当の値と根拠で決まる |
| `duplicate` | **当ラウンドの 2 段目の統合の入力にする**（後述）。区分は代表の 1 件が持つ |
| `out_of_scope` | 区分を変えない。`out_of_scope` を返した担当の一覧を要素へ残し、**修正の担当が `/ndf:out-of-scope` で起票する材料にする** |

**`refute` の代わりに使わせない。** `duplicate` は「別の指摘と同一」、`out_of_scope` は
「この Pull Request の範囲から外れる」であり、どちらも**指摘が誤っているという主張では
ない**。範囲外の指摘は、この Pull Request で直さないだけで、課題としては残る。

**機械では結べない重複は担当が見つける。その申告は次のラウンドへ回さず、当ラウンドの区分の
前に 2 段目の統合として適用する。** 回すと、同じ `major` の指摘を 2 者が別の本文で出した組が
どちらも `origin_runtimes` 1 者・`support` 0 件のまま `insufficient_evidence` へ落ち、統合
される前に収束する（前述の全員一致と同じ事象）。**2 段目は相互の申告に限る**（片側は
`duplicate_candidates` へ残す）。形は 1 段目と同じで、統合の後は組の担当の値を数えない。
