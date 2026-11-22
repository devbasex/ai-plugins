# #3500 の詳細設計: 担当が入れた作業ファイルを公開の前のコミットから除く

設計文書 [issue-3500-design.md](./issue-3500-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 4 つ（`Git.drop_paths`・`Git.run_bytes`・`git.unrecovered`・`drive._unrecovered`）あるため、設計文書の「どこを変えるか」から別ファイルへ移した。中身は移す前と同じで、変える前と後の振る舞い・失敗の扱いは設計文書の「処理の流れ」（「索引のロック」と「失敗したとき」の表）を正とする。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/adapters/git.py
├── src/ndf/domains/crossrefactor/drive.py
├── src/ndf/domains/crossrefactor/final_gate.py
├── skills/cross-refactoring/docs/01-state-and-propose.md
└── tests/
    ├── adapters/test_git.py
    └── domains/crossrefactor/test_drop_result_files.py *
```

依存の向き: `drive` と `final_gate` が `adapters.git` を呼び、`adapters.git` はドメインを読まない。除く置き場の名前（`launch_decide.RESULT_DIR`）は `drive` が引数で渡すので、循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/adapters/git.py` | 変更 | コミットから最上位のディレクトリの下を除いて作り直す | git（外部コマンド） | `Git`・モジュール | `Git.drop_paths`（新設）・`Git.run_bytes`（新設）・`RewriteUnrecovered`（新設の例外）・`unrecovered`（新設） |
| `plugins/ndf/src/ndf/domains/crossrefactor/drive.py` | 変更 | 担当の起動の後に作業ファイルを除き、戻せなかった停止から再開の前に復旧を確かめる | `adapters.git`・`launch_decide` | モジュール | `_launch_status`（変更）・`_attempt_flaky_fix`（変更）・`_open`（変更）・`_unrecovered`（新設） |
| `plugins/ndf/src/ndf/domains/crossrefactor/final_gate.py` | 変更 | 戻せなかった停止で最終ゲートの後片付けを飛ばす | `adapters.git` | モジュール | `_fix_or_undo`（変更） |
| `plugins/ndf/skills/cross-refactoring/docs/01-state-and-propose.md` | 変更 | 作業場所と状態ファイルの説明 | — | — | — |
| `plugins/ndf/tests/adapters/test_git.py` | 変更 | `drop_paths` の単体テスト | `adapters.git` | — | — |
| `plugins/ndf/tests/domains/crossrefactor/test_drop_result_files.py` | 新設 | 担当の偽物の起動でのテスト | `drive` | — | — |

## git のアダプター（`adapters/git.py`）

満たす決定: 決定 3・4、受け入れ条件 2・3・6・7・8・9

`Git.drop_paths(base: str, top: str) -> list[str]` を足す。入力: 起点のコミット `base` と最上位のディレクトリの名前 `top`。返り: 作り直した元のコミット（古い順。当たらなければ空）。範囲は `git log -z --reverse --no-renames --diff-merges=separate '--format=%x01%H %P' --name-only base..HEAD` の 1 回で読む（パスで絞らない。パスで絞ると既定の履歴の簡略化で、マージの取り込んだ側をたどらないことがある）。`-z` はパスを引用せずに NUL で区切らせる（既定では日本語などを含むパスが `"` で囲まれ 8 進でエスケープされ、`top/` と前方一致しない）。`--diff-merges=separate` はマージのコミットを親ごとに 1 回ずつ、その親との差分のパスと一緒に出させる（既定ではマージのパスが出ず、解決のときにだけ足した作業ファイルを見落とす）。**バイトのまま扱う**: `drop_paths` の git の起動は、出力をデコードしない（`text=True` を使わない）起動 `Git.run_bytes(*args, env=None, stdin=b"") -> bytes` を通す（`run_git` と同じく `NO_PROMPT` を渡し、終了コードが 0 でなければ `GitError`。誤りの文は標準エラーを `errors="replace"` でデコードして載せる）。`log`・`diff`・`ls-files`・`ls-tree` の出力のパスと `update-index` へ渡す行はバイトのまま持ち、`top` は `os.fsencode(top) + b"/"` で前方一致を見る。UTF-8 として不正なバイトを含むパスでも `UnicodeDecodeError` にならず、`GitError` の停止経路を外れない。SHA だけは ASCII でデコードする。解析: 出力を NUL で切り、各片の頭の改行を除いて空の片を捨てる。見出しは、片の全体が `\x01<SHA>( <SHA>)*`（SHA は 40 桁か 64 桁の小文字の 16 進で、1 つの出力の中では同じ長さ）に一致する片で、続く片がその見出しのコミットの変えたパスである。先頭の `\x01` だけでは判定しない。パスは NUL 以外のどのバイトも含みうるので、見出しの形に一致するパスがありえないとは言えない。そのため、作り直すときは並びを確かめる: 範囲のコミットを古い順に並べ、最初のコミットの親が `base`、以後の各コミットの親が 1 つ前のコミットで、最後のコミットが起動の直後の HEAD であることを確かめ、外れれば作り直さずに `GitError`（パスを見出しと取り違えても、偽の見出しは並びを壊すので作り直しへ進まない）。同じ SHA の見出しが続く（マージの親ごと）ときは 1 件のコミットにまとめ、パスを合わせる。2026-11-22 に git 2.53.0 で、見出しの後に NUL と改行が続き、パスが生の UTF-8 で出ることと、マージが親ごとに出ることを実測した。どのコミットも `top/` の下のパスを変えていなければ何もしない。当たったコミットがあり、範囲に親が 2 つ以上のコミットがあれば作り直さずに `GitError`（要求仕様の前提 1「担当は起動の間にマージのコミットを作らない」を外れる。マージを作り直す手順は持たない）。一直線なら、最初に当たったコミットから HEAD までを古い順に、一時の索引（`GIT_INDEX_FILE`）で木を読み、`top` を外して `base` の `top` の中身を置き直し（`base` に無ければ外したまま）、木を書き（一時の索引から外すときは `rm -r -f --cached --ignore-unmatch -- <top>`。後のコミットで担当が `top` を消した木でも止まらない）、元の作者・日付（`GIT_AUTHOR_*`）とメッセージで `commit-tree` する。親は 1 つ前の作り直した先へ付け替える。元は親と違う木だったのに作り直すと親と同じ木になったコミットは落とし、元から空のコミットは残す。最初に当たったコミットより前のコミットは元の SHA のまま使う。済んだら `rev-parse --git-path index` で本来の索引のパスを得て、その隣の `index.lock` を `os.open(..., O_CREAT|O_EXCL|O_WRONLY)` で取る（取れなければ何もせずに `GitError`）。ここから最後まで、どこで終わっても `finally` でロックを消す。ロックの下で本来の索引に入れる行を組む: `diff --name-only --no-renames -z <旧> <新> -- <top>` で中身の変わったパスを出し、そのうち本来の索引の行（`ls-files -s -z -- <top>`）が旧い HEAD の行（`ls-tree -r -z <旧> -- <top>`）と同じパスだけを、新しい HEAD の行（`ls-tree -r -z <新> -- <top>`。無ければ消す）へ置き換える行にする。索引の行が旧い HEAD と違うパス（担当がステージしてコミットしていない変更）は変えない。それから `update-ref HEAD <新> <起動の直後の HEAD>` を 1 回打ち、本来の索引を同じディレクトリの写し（`index.ndf-drop`）へ複写して、組んだ行を `GIT_INDEX_FILE=<写し>` の `update-index -z --index-info` で入れ（標準入力で渡す）、`os.replace(<写し>, <本来の索引>)` で置き換える。合わせる行が無ければ写しも置き換えもしない。`update-ref` より前の失敗は何も戻さずに `GitError`。`update-ref` が旧い値の不一致で断ったら HEAD に触れずに `RewriteUnrecovered`。写しへの `update-index` か置き換えが失敗したら、写しを消し、`update-ref HEAD <旧> <新>` で比較付きで戻し、戻せたら `GitError`、戻せなければ `RewriteUnrecovered`。`reset`・`clean`・`checkout` は打たない（「処理の流れ」の「索引のロック」と「失敗したとき」の表）。`RewriteUnrecovered(GitError)` と、例外そのものか `__cause__` が `RewriteUnrecovered` なら真を返す `unrecovered(exc: BaseException) -> bool` をこのファイルに足す

## 起動の口と再開（`drive.py`）

満たす決定: 決定 1・2・5、受け入れ条件 1・4・5・9・10

`_launch_status`: 工程が `launch_decide.coding_phases()` に入るときだけ、`launch.run` の前に `work` の HEAD を控える（読み取りの工程では HEAD も控えない）。`launch.run` が返ったら、今と同じく先に `_record_spent` と保存を済ませ、その後で（安全網の判定より前に）`run.work.drop_paths(before, launch_decide.RESULT_DIR)` を呼ぶ。返りが空でなければ `state["dropped_results"]` へ `{"phase", "stem", "commits"}` を足して保存し、標準エラーへ 1 行出す。`drop_paths` が `GitError` を上げたら `work` に触れず（`_reset` を呼ばない）、`RewriteUnrecovered` のときだけ `state["drop_unrecovered"]` へ `{"phase", "stem", "before", "launched", "detail"}` を書いて保存し、`raise DriveStopped("precondition", <作業ファイルを除けなかった旨と git の誤り>) from exc` で上げる。HEAD を戻せた失敗の後片付けは今の呼び出し側（`_attempt_flaky_fix`・`final_gate._fix_or_undo`・再開の `_items`）に任せる。`_attempt_flaky_fix`: `except DriveStopped as exc` で `git.unrecovered(exc)` が真なら `_reset` を呼ばずに上げる。`_open`: `_resume_mismatch` の後、`workspace.prepare` の前に `_unrecovered(root, state, path) -> Optional[Finish]` を呼ぶ。状態ファイルに `drop_unrecovered` があれば、`git.Git(workspace.layout(root, ()).work)` で `rev-parse HEAD` と `diff --cached --quiet HEAD -- <RESULT_DIR>` を打ち、HEAD が記録の `before` と同じで索引の差が無ければ記録を消して保存し `None` を返す。違うか、`work` が無いか git が失敗すれば `Finish("precondition", <work のパス・記録の before・今の HEAD と、保存の要る変更を退けてから `git -C <work> reset --hard <before>` を打ち、`index.lock` が残っていれば消してから打ち直す旨>)` を返す。`_ready` は変えない。シグネチャは変えない

## 最終ゲートの後片付け（`final_gate.py`）

満たす決定: 決定 1、受け入れ条件 5・9

`_fix_or_undo`: `fix()` が上げた例外で `git.unrecovered(exc)` が真なら、`finally` の `reset --hard` を飛ばしてそのまま上げる（結果を出さずに終わったときと、ほかの例外のときの戻しは今のまま）。シグネチャは変えない

## Skill の文書（`01-state-and-propose.md`）

満たす決定: 決定 5

「作業場所」の箇条に「担当が除外を越えて作業ファイルをコミットしたら、起動の後にそのコミットから除く」を足し、「状態ファイル」の表に `dropped_results` と `drop_unrecovered`（HEAD を戻せずに止めた記録。`work` の HEAD を記録の `before` へ戻すまで再開しない。戻すと次の実行が記録を消す）の行を足す

## 単体テストの観点

| ファイル | 観点 |
| --- | --- |
| `plugins/ndf/tests/adapters/test_git.py` | `drop_paths` の受け入れ条件 2・3・6・7・8・8b・8c・9f の行と、9・9c のうち `drop_paths` が HEAD・索引・作業ツリーをどう残すかの行 |
| `plugins/ndf/tests/domains/crossrefactor/test_drop_result_files.py` | 担当の偽物の起動での受け入れ条件 1・4・5・9・9b・9c・9d・9e・10 の行（9c の最終ゲートの直しの行は `_fix_or_undo` を通す） |
