# #573: worktree-setup.sh init が読めない宣言ファイルを「既にあります」と報告し、運用が無効のまま残る

設計は [issue-573-design.md](issue-573-design.md) にある。この文書は「何を満たすか」だけを扱う。

## 依頼（原文）

> **`worktree-setup.sh init` は、読めない宣言ファイルを「既にある」と報告して終了コード 0 で終わる。**
> 作業ツリー運用は宣言を読めないと何もしないため、`init` を通したのに運用が無効のまま残る。
>
> ```console
> $ mkdir .ndf && echo '{broken' > .ndf/worktree.json
> $ bash plugins/ndf/scripts/worktree-setup.sh init; echo "exit=$?"
> 宣言ファイルは既にあります: .ndf/worktree.json
> exit=0
> $ bash plugins/ndf/scripts/worktree-setup.sh status | sed -n 2p
> 宣言ファイル: 読めません（版が未対応か、JSON として壊れています）
> ```
>
> 同じ状態を `status` は「読めません」と区別し、`worktree-setup.sh check` も終了コード 3（読めない）で返す
> （#527）。**ただし `init` の報告は変わっておらず、`worktree` の手順 0 が実行するのは `init` だけである。**
>
> `worktree` だけを使う利用者が、壊れた宣言（手で編集した JSON の誤り、未対応の `version`）を
> 抱えたまま「宣言はある」と読んで進む。

（issue #573 の本文から「何を見つけたか」と「直さないと何が起きるか」を引いた。モードは `standard`）

## 解釈

**`--force` を付けない `init` が、宣言の状態を `status` / `check` と同じ関数で判定し、読めない宣言を
失敗として報告する状態にする。** `worktree` の手順 0 は、`init` が失敗したら先へ進まない。

状態を分ける基準は `wt_declaration_state` の 1 か所に置いたまま変えない（#527 の方針）。`init` はその
結果を読むだけにする。

## 目的

- `worktree` を単独で起動した経路でも、読めない宣言に気づいて止まる
- `init` と `status` / `check` が、同じ宣言を別の状態として報告しない

## 前提

- 前提 1: 宣言の読み取り層（`worktree-common.sh` の `wt_declaration` / `wt_declaration_state`）は変えない。
  担当 D が #495 で共有と個人の宣言へ分ける予定で、読み取り層の構造はそちらが持つ
- 前提 2: `init --force` の振る舞いは変えない。読めない宣言を上書きする経路はこれまでどおり残る
- 前提 3: `development-workflow` の手順 0 は `check` を使い、状態 3 では `init` を呼ばない。この変更で
  `development-workflow` の分岐は変わらない
- 前提 4: 読めない宣言を消すか直すかは利用者が決める。`init` も手順 0 も、宣言を書き換えない

## 用語

| 用語 | 意味 |
| --- | --- |
| 宣言 | 主ディレクトリの `.ndf/worktree.json` |
| 読める宣言 | `wt_declaration_state` が `present` を返す宣言 |
| 読めない宣言 | `wt_declaration_state` が `unreadable` を返す宣言。JSON として壊れている・`version` が未対応・空・ディレクトリ・読み取り権限が無い、のいずれか |
| 宣言の行 | `status` と `check` が出す `宣言ファイル:` で始まる 1 行 |
| 手順 0 | `plugins/ndf/skills/worktree/SKILL.md` の「0. 宣言ファイルを用意する」 |

## 受け入れ条件

読めない宣言を失敗にする:

- [ ] AC1: JSON として壊れた宣言がある状態で `init` を実行すると、終了コード 1 で終わり、「既にあります」を出さない
- [ ] AC2: AC1 の標準エラー出力の 1 行目が、同じ状態で `status` と `check` が出す宣言の行と一字一句同じである
- [ ] AC3: AC1 の標準エラー出力に、宣言を直すか消してから `init` をもう一度実行する案内が入る。`--force` を勧めない
- [ ] AC4: `version` が未対応（`99`）・空のファイル・ディレクトリ・読み取り権限の無いファイルでも、AC1 と同じ終了コードと
  宣言の行になる
- [ ] AC5: AC1 と AC4 のどの形でも、`init` の前後で宣言の中身（ディレクトリならその中身）が変わらない

読める宣言と無い宣言の振る舞いを保つ:

- [ ] AC6: 読める宣言がある状態で `init` を実行すると、終了コード 0 で「宣言ファイルは既にあります」を出し、中身を変えない
- [ ] AC7: 宣言が無い状態で `init` を実行すると、これまでどおり宣言を作り、終了コード 0 で終わる。直後の `check` は 0 を返す
- [ ] AC8: 読める宣言を指す symlink がある状態で `init` を実行すると、終了コード 0 で「既にあります」を出す
  （現状は「symlink です」で 1）
- [ ] AC9: `init --force` は、読めない宣言があっても宣言を作り直して終了コード 0 で終わる。symlink の宣言と symlink の `.ndf`
  は、これまでどおり 1 で断る

手順 0:

- [ ] AC10: 手順 0 が、`init` の終了コードが 0 でなければ先へ進まず、出力を利用者に示すことを書く。宣言を消す・
  `--force` で作り直すのは利用者が決めると書く
- [ ] AC11: 手順 0 の「既にあれば上書きしない」が、読める宣言に限ることと、読めない宣言では 1 で終わることを書く

退行しない:

- [ ] AC12: `status` と `check` の出力と終了コードが、AC1・AC4・AC6〜AC8 のすべての形で変更前と同じである
- [ ] AC13: `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る。既存の `test_setup.py` と
  `test_declaration_check.py` の期待値を変えない
- [ ] AC14: 仕様の文書が「`init` の後にもう一度 `check` を通す」に添える理由が、変更後の `init` の終了コードと
  食い違わない。仕様の文書は `docs/specifications/ndf-workflow-unit-and-gates.md` である

## 対象範囲

含む:

- `plugins/ndf/scripts/worktree-setup.sh` の `do_init`
- `plugins/ndf/skills/worktree/SKILL.md` の手順 0
- `plugins/ndf/skills/worktree/tests/test_setup.py` への受け入れ条件のテスト
- `docs/specifications/ndf-workflow-unit-and-gates.md` の、`init` の終了コードに触れる 1 文

含まない:

- 宣言の読み取り層（`wt_declaration` / `wt_declaration_state`）の変更（前提 1）
- 読めない理由の区別（壊れた JSON とディレクトリを別の文言にすること）。状態は 3 語のまま
- `init --force` の書き込みの経路。宣言の位置がディレクトリのときに一時ファイルが残る事象は #628 が扱う
- 壊れた symlink（指す先が無い）で `init` が「symlink です」で断る振る舞い
- `development-workflow` の本文と、その手順 0 の分岐
- `plugins/ndf/README.md` と `references/declaration.md` の `init` の記述。どちらも「作る」「上書きしない」までを書き、
  読めない宣言の扱いに触れていない
- クラス図とデータ構造の成果物。bash の関数と手順書だけを変え、型も永続データも持たない

## 影響

| 対象 | 影響 |
| --- | --- |
| `init` の終了コード | 読めない宣言で 0 から 1 に変わる。読める symlink の宣言で 1 から 0 に変わる |
| `init` の出力 | 読めない宣言で、標準出力の「既にあります」が、標準エラーの宣言の行と案内の 2 行に変わる |
| `worktree` を起動したエージェント | 読めない宣言のリポジトリで手順 0 から先へ進まず、利用者に出力を示す |
| `development-workflow` | 変わらない。状態 3 では `init` を呼ばない |
| hook（`worktree-guard.sh` / `worktree-session.sh`） | 変わらない。`init` を呼ばない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest plugins/ndf/skills/worktree/tests/test_setup.py plugins/ndf/skills/development-workflow/tests/test_declaration_check.py -q`、仕上げに `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 生成物 | `bash scripts/validate-runtime-plugins.sh` |
| 手動確認 | 一時ディレクトリの git リポジトリで AC1・AC4 の形を作り、`init` / `status` / `check` を実行して終了コードと宣言の行を並べる（設計文書の「実測」と同じ手順） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 状態の判定は `wt_declaration_state` を呼ぶだけにし、`worktree-setup.sh` に基準を書き写さない（#527） |
| コーディング規約 | `worktree-setup.sh` の先頭の約束（0 完了 / 1 処理できなかった。2・3 は `check` だけ）に従う |
| テスト戦略 | `test_setup.py` の `run` と `write_declaration` でスクリプトを子プロセスとして走らせ、終了コードと出力を確かめる。手順 0 の文言は既存の `test_declaration_check.py` と同じく本文の抜き出しで確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、変更した分岐への理由のコメント |
| 確認してから行う | 宣言の読み取り層の変更、`--force` の経路の変更 |
| 行わない | 読めない宣言の自動の書き換え、`development-workflow` の分岐の変更 |
