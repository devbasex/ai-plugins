# #1455: project-mvv.py propose / vet が psutil の無い python3 で ModuleNotFoundError になる

正は課題の本文（#1455）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> `project-mvv.py propose` が、`psutil` の入っていない `python3` で `ModuleNotFoundError: No module named 'psutil'` を出して止まる。
>
> ```
> File ".../scripts/project_lib/mvv_llm.py", line 37, in call_llm_json
>   from supervise_lib.claude import call_claude
> File ".../scripts/supervise_lib/claude.py", line 25, in <module>
>   import procs
> File ".../scripts/lib/procs.py", line 23, in <module>
>   import psutil
> ```
>
> 同じ環境で `supervise.py queue` は最後まで流れた（別の経路では `procs` を読まないか、遅れて読んでいる）。`project-mvv.py check` と `collect` も通る。止まるのは LLM を呼ぶ `propose`（と `vet`）だけである。
>
> carmo-cdk でプロジェクト MVV を策定したとき（2026-09-29、ndf 10.17.41、`/usr/bin/python3` 3.14.4、psutil 無し）。`uv run --no-project --python 3.12 --with psutil python .../project-mvv.py propose ...` で回避した。
>
> 手順 0 の案内どおり `python3 "$SCRIPTS/project-mvv.py" propose` を打つと策定の 2 手目で止まり、利用者は依存を足す方法を自分で探すことになる。

（書き手: 起票者、日付: 2026-09-29。carmo-cdk での策定の作業中に AI が起票した本文の「何を見つけたか」「どこで見つけたか」「直さないと何が起きるか」。利用者の指示の原文は本文に無いため、利用者の文としては扱わない）

> - **第一候補:** `propose` / `vet` のエントリポイントで `deps.require("procs")` を呼ぶ（`supervise.py:85` と同じ #1142 の依存の仕組み。`lib/deps.py:53` の `procs` グループが `psutil` を持つ）。`project-mvv.py` は今 `cmd_schema`（419-421 行）だけが `deps.require("schema")` を呼ぶ
> - `procs.py` の `psutil` を使う箇所で遅延 import にし、無ければ代わりの手段（`/proc` を読む・`os.kill(pid, 0)`）へ落とす
> - または、スクリプトの冒頭で依存の欠けを検知し、`uv run --with psutil` の打ち方を案内して終了コード 2 で止まる
>
> - 現象レイヤー: `project-mvv.py` の `propose` / `vet`（LLM を呼ぶ副命令）
> - 修正レイヤー: `lib/deps.py` の `require` の契約と、エントリポイントが推移的に読むモジュールの依存グループの検査（同じ形の欠けを全エントリポイントで落とす）
> - 修正方針: 新設。エントリポイントが推移的に読むモジュール（`supervise_lib.claude` → `procs` など）の依存グループと `require` の引数の突き合わせを継続的統合で検査する（またはグループを要るモジュール側で宣言する）。案の第一候補は、この検査が落とす 1 件の直しとして残る（`mvv-gate.py:59`・`parallel-measure.py:50` は同じ `procs` を手で足している）
>
> - #1330 — `procs` を単独で import できない（cross-review の `test_monitor_zombie.py`）。`lib/procs.py:23` の最上位の `import psutil` を共有する

（書き手: 起票者、日付: 2026-09-29。同じ本文の「案」「直す場所」「関連」）

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。本文の全体が起票者の文なので、直し方と範囲の条件はすべてここに入る。承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | `propose` / `vet` が psutil の無い python3 で止まらないようにする | 本文の「何を見つけたか」（2026-09-29 の carmo-cdk での実際の失敗） | 受け入れ条件 1〜3 |
| S2 | 直し方の第一候補は `propose` / `vet` で `deps.require("procs")` を呼ぶこと | 本文の「案」の第一候補（`supervise.py`・`mvv-gate.py`・`parallel-measure.py` の前例） | 受け入れ条件 1〜3 で振る舞いだけを決め、直し方は設計へ渡す（前提 2） |
| S3 | `procs.py` の psutil を遅延 import にして `/proc` などへ落とす | 本文の「案」の 2 つ目 | 採らない（#1142 の決定 19 で psutil を呼ぶのは `procs.py` だけと決めてあり、代わりの手段を持つと同じ役割の処理が 2 つになる。Value 6） |
| S4 | 冒頭で欠けを検知し、`uv run --with psutil` を案内して終了コード 2 で止める | 本文の「案」の 3 つ目 | 採らない（依存の欠けの終了コードは `lib/deps.py` の 69 が持つ（#1654）。案内して止めると利用者の手が止まる。Value 2） |
| S5 | 同じ形の欠け（エントリポイントが推移的に読むモジュールの外部パッケージと `require` の引数の食い違い）を継続的統合で全エントリポイントについて落とす | 本文の「直す場所」の修正レイヤーと修正方針 | 受け入れ条件 4〜7 |
| S6 | 検査の代わりに、グループを要るモジュールの側で宣言する形でもよい | 本文の「修正方針」の括弧の中 | 前提 3（どちらにするかは設計が決める） |
| S7 | #1330（`procs` を単独で import できない）は関連だが別の課題 | 本文の「関連」 | 含まない |

## 目的

- 利用者が案内どおり `python3 "$SCRIPTS/project-mvv.py" propose` / `vet` を打ったとき、手元の python3 に psutil が無くても、ほかの NDF のスクリプトと同じく依存の仕組み（#1142 の `deps.require`）が足りない外部パッケージを用意して最後まで進むこと（Mission・Value 2・Value 5）
- 同じ形の欠け（副命令の中で遅れて読むモジュールが外部パッケージへ届くのに、その経路で `deps.require` が呼ばれていない）を、利用者の手元で落ちる前に継続的統合で落とすこと（Value 6）

## 前提

- 前提 1: 再現は 2026-10-30 に確かめた。psutil の無い python 3.12 の venv（`uv venv`）で `NDF_SUPERVISE_CLAUDE=/bin/true python plugins/ndf/scripts/project-mvv.py propose --root . --materials m.json`（`m.json` は `{}`）を打つと、`lib/procs.py:23` の `import psutil` で `ModuleNotFoundError` を出し、終了コード 1 で止まる。`check` と `collect` は、最上位で読むモジュールが外部パッケージへ届かないため通る
- 前提 2: `propose` / `vet` をどの形で直すか（副命令のハンドラーで `deps.require` を呼ぶ・`call_llm_json` の側で呼ぶ・グループを要るモジュールの側で宣言する など）は設計が決める。この要求は、受け入れ条件 1〜3 の振る舞いだけを決める
- 前提 3: 同じ形の欠けを落とす手段は、既存の構造チェック（`scripts/check-script-structure.py`）の種類 `require-groups` を広げる形を第一に考える。今の `require-groups` は、モジュールの最上位で `deps.require(…)` を呼ぶエントリポイントだけを対象にし、読み込み時に走る import（関数の中の import を除く）だけをたどる。`project-mvv.py` は最上位で `require` を呼ばず、`psutil` へ届く import が `call_llm_json` の関数の中にあるため、今の検査の 2 つの外し方の両方に当たって見落とされる。広げるか、グループを宣言する別の形にするかは設計が決める
- 前提 4: 関数の中の import までたどって `require` の引数と突き合わせると、`deps.require` を最上位で呼ばないエントリポイントを含めて候補が多数出る。2026-10-30 に関数の中の import も含めて素朴にたどった試算では、`plugins/ndf/scripts/` の直下と `experimental/` のエントリポイントのうち 27 本に、`require` に無いグループへ届く経路が見つかった。この中には、`except ImportError` で受けて代わりの手段へ落ちる任意の依存（`github` の `gh` コマンドへの切り替えなど）と、呼ばれる側のモジュールが自分で `deps.require` を呼んでから読む経路（`lib/monitor.py` の `deps.require("procs", "locks")` など）が含まれる。検査はこの 2 つを誤検出しない形にする。どの経路を誤検出とみなすかの規則は設計が決める
- 前提 5: 検査が落とす `project-mvv.py` 以外のエントリポイントは、この課題の中で直すか、構造チェックの例外リスト（`scripts/script-structure-allow/`）へ理由つきで載せる。どちらにするかは 1 件ごとに即時修正の 4 条件（Value 1）で決め、満たさないものは例外リストへ載せたうえで別の課題に起こす
- 前提 6: 利用者の手元に uv が無く、ネットワークも無いときは、`deps.require` の既存の契約どおり、何が無いかと手で入れるコマンドを標準エラーへ出して終了コード 69 で止まる。この振る舞いはこの課題で変えない

## 対象範囲

含む:
- `project-mvv.py` の `propose` / `vet` が、psutil の無い python3 で依存の仕組みを通って進むこと
- 副命令の中で遅れて読むモジュールが外部パッケージへ届くのに、その経路でグループが `deps.require` に渡されていない形を落とす継続的統合の検査（既存の構造チェックを広げるか、それに代わる形）
- 検査を入れたときに落ちる既存のエントリポイントの扱い（直すか、例外リストへ理由つきで載せて別の課題に起こす）

含まない:
- #1330（`lib/procs.py` の最上位の `import psutil` により `procs` を単独で import できない件）
- `procs.py` に psutil の代わりの手段（`/proc` の読み取り・`os.kill(pid, 0)`）を持たせること（S3）
- `deps.require` の起動し直しの手順・終了コード 69・uv の入れ方の変更
- hook の経路（`deps.require` を呼ばない。I13・決定 20）の依存の扱い
- `project-mvv.py` の `check` / `collect` / `approve` / `show` / `context` / `signals` / `schema` の振る舞いの変更
- クラス図（型・クラスを追加も変更もしない。変えるのはモジュールの関数と構造チェックの規則だけ）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 利用者が `project-mvv.py propose` か `vet` を打った | `development-workflow` の `project-mvv.md` の手順 0 の案内 | — | — |
| E2 | 依存の仕組みが、要るグループ（`procs` など）の外部パッケージが今の python で import できるかを確かめた | E1 の後、LLM を呼ぶモジュールを読む前 | — | E1 |
| E3 | 足りなかったので、uv の環境（`~/.cache/ndf/venv/<版>`）で同じ副命令と引数のまま自分を起動し直した | E2 で足りないと分かった | uv を入れられない → 終了コード 69 と手で入れるコマンド（前提 6）。起動し直した後もまだ import できない → 終了コード 69 | E2 |
| E4 | LLM を 1 回呼び、候補（`propose`）か照合の結果（`vet`）を書いた | E2 で揃っていた、または E3 の起動し直し | LLM の呼び出しの失敗は今の経路のまま（この課題で変えない） | E2・E3 |
| E5 | 継続的統合の構造チェックが、エントリポイントから副命令の中の import までをたどり、届いた外部パッケージのグループと、その経路で呼ぶ `deps.require` の引数を突き合わせた | Pull Request の継続的統合 | 足りないグループがある → エントリポイントのパスとグループと届いたモジュールを出して落ちる | — |

## 用語

| 用語 | 意味 |
| --- | --- |
| エントリポイント | 用語集のとおり（利用者が呼ぶスクリプトのパスと副命令と引数と出力の形） |
| 構造チェック | 用語集のとおり（`scripts/check-script-structure.py`） |

## 受け入れ条件

- [ ] 1. psutil の無い python3（前提 1 の venv）で、uv が使える環境で `project-mvv.py propose --root <根> --materials <材料>` を打つと、`ModuleNotFoundError` を出さず、LLM を呼ぶところまで進む（`NDF_SUPERVISE_CLAUDE` で差し替えた LLM が呼ばれる）。終了コードは psutil の入った python3 で同じ引数を打ったときと同じになる
- [ ] 2. 同じ条件で `project-mvv.py vet --root <根> --body <本文> --kind candidate` を打つと、受け入れ条件 1 と同じく `ModuleNotFoundError` を出さずに LLM を呼ぶところまで進む
- [ ] 3. 同じ条件で `project-mvv.py check` と `collect` を打っても、今と同じ出力と終了コードになる（LLM を呼ばない副命令の振る舞いを変えない）
- [ ] 4. 構造チェックは、エントリポイントから副命令のハンドラー（関数の中の import を含む）をたどって外部パッケージへ届くのに、その経路で呼ぶ `deps.require` にそのグループが無い形を、違反として落とす。違反にはエントリポイントのパス・足りないグループ・届いたモジュールが出る
- [ ] 5. 受け入れ条件 1・2 の直しを外した木（`project-mvv.py` が今のまま）に構造チェックを打つと、`project-mvv.py` と `procs` の違反が出て終了コードが 0 でない
- [ ] 6. `except ImportError`（または `ModuleNotFoundError`）で受けて代わりの手段へ落ちる import と、呼ばれる側のモジュールが自分で `deps.require` を呼んでから読む import は、受け入れ条件 4 の違反にしない（前提 4）
- [ ] 7. この課題の最終の木で `python3 scripts/check-script-structure.py` が終了コード 0 で通る。新しく例外リストへ載せた項目は、1 項目ごとに理由と、起こした課題の番号を持つ（前提 5）
- [ ] 8. `mvv-gate.py`・`parallel-measure.py`・`supervise.py` など、今 `procs` を手で `deps.require` に並べているエントリポイントの振る舞い（引数・出力・終了コード）は変わらない
- [ ] 9. 全体テスト（`uv run --frozen --project . --all-extras pytest . -q -n 4`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | psutil の入った python3 で打つ `propose` / `vet` は、起動し直しを挟まない（今と同じ 1 プロセスで進む） |
| 運用・保守性 | 構造チェックの所要は、今の所要から目に見えて増えない（PR に変更前と変更後の所要の実測を秒で書く。Value 3） |
| システム環境 | 利用者の手元の python3 に psutil が無くても動く。uv が無ければ `deps.require` の既存の手順で入れる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（`project-mvv.py` の副命令・引数・出力の形は同じ。psutil の無い環境で `ModuleNotFoundError` の代わりに uv の環境へ起動し直す） |
| データ | 無し |
| 既存の振る舞い | `propose` / `vet` が psutil の無い python3 で起動し直す。構造チェックが副命令の中の import もたどり、今は通っているエントリポイントが落ちうる（前提 5） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | psutil の無い venv（`uv venv /tmp/nops --python 3.12`）の python で `NDF_SUPERVISE_CLAUDE=<差し替え> /tmp/nops/bin/python plugins/ndf/scripts/project-mvv.py propose --root <根> --materials <材料>` と `vet` を打ち、`ModuleNotFoundError` が出ないことと終了コードを見る |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（`plugins/ndf/scripts/tests/test_project_mvv.py`・`scripts/tests/test_check_script_structure.py` を含む） |
| 静的解析・型検査 | `python3 scripts/check-script-structure.py`・`python3 scripts/check-skill-frontmatter.py` |
| 手動確認 | 次の版を入れた利用者の環境（psutil の無い `/usr/bin/python3`）で、`project-mvv.md` の手順 0 の案内どおり `propose` を打ち、最後まで進むことをリリース後テストで確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 外部パッケージの用意は `lib/deps.py` の `require` が持つ（#1142 の決定 17・23）。psutil を呼ぶのは `lib/procs.py` だけ（決定 19）。検査は `scripts/check-script-structure.py` に置く。hook の経路は `deps.require` を呼ばない（I13・決定 20） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。同じ役割の定数・関数を分けない（Value 6）。`.md` の文言を照合するテストを書かない |
| テスト戦略 | 受け入れ条件 1〜3 は psutil の無い python で副命令を打つ結合のテスト（`tests/test_deps.py` の `refactor-assess` の例と同じ形）で見る。受け入れ条件 4〜6 は構造チェックのテストに、違反になる木と違反にならない木の小さな例を置いて見る |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体テストと構造チェックの実行。検査が落とした既存のエントリポイントの件数と扱いを PR に書く |
| 確認してから行う | 構造チェックの規則の変更（承認ゲート 1 で設計を見る）。例外リストへの新しい項目（承認ゲート 2 で見る） |
| 行わない | `procs.py` の代わりの手段の追加。`deps.require` の終了コードと手順の変更。#1330 の直し。依頼範囲外のエントリポイントのリファクタリング |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| `propose` / `vet` の直し方（ハンドラーで `require` を呼ぶ・`call_llm_json` の側で呼ぶ・グループを要るモジュールの側で宣言する）| 設計（承認ゲート 1 で利用者が確かめる） | 設計 PR |
| 検査を広げる形（`require-groups` の対象と、たどる import の範囲と、誤検出とみなす規則） | 設計（承認ゲート 1 で利用者が確かめる） | 設計 PR |
| 検査が落とす既存のエントリポイントのうち、この課題で直すものと例外リストへ載せて別の課題にするものの分け方 | 実装（前提 5 の 4 条件で 1 件ごとに決め、承認ゲート 2 で利用者が見る） | 実装 PR |
