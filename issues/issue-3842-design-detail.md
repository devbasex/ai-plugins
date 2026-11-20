# #3842 の詳細設計: 子の claude を起動できないときの終わり方

設計書（[issue-3842-design.md](./issue-3842-design.md)）の解き方を、ファイルと関数の粒度へ下ろした文書である。別ファイルにしたのは、起動する関数（`Wrapper._start`）の返り値の型を変えるため（#3868 の決定 6 の条件）。パスは `plugins/ndf/` からの相対で書く。

## 地図

触るファイルは 5 つで、新設のファイルは無い。

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   ├── wrapper.py          … spawn_pty・_enter（新設）・Settings.cwd_lost（新設）・Wrapper._before_start・_start・_loop・_on_switch・_on_resume
│   └── cli.py              … wrapper_settings・_cwd（新設）
├── skills/development-workflow/references/
│   └── relay.md            … 「落ちたときの続け方」に 1 行
└── tests/domains/relay/
    ├── test_relay_wrapper.py
    └── test_relay_cli.py
```

依存の向きは今のまま変わらない。`wrapper` が `markers`（ラッパーが送った入力のマーカー）と `ptyprocess` を呼び、`cli._ended` が `Ended` を終了コードへ変える（`cli.py` は変えない）。

呼び出しの関係:

```text
cli._run ─→ wrapper_settings ─→ _cwd（作業ディレクトリを例外なしで読む）─→ Wrapper.run ─→ _loop
_loop ────────┐
_on_switch ───┼─→ Wrapper._start ─→ self.spawn（既定は spawn_pty）─→ PtyProcess.spawn ─fork→ 子: _enter（chdir）→ execvpe
_on_resume ───┘         │
                         └─ 停止理由を返す ─→ 呼び出し元がそのまま返す ─→ _loop が _end
```

理由の文の形は `wrapper.py` の定数に置く。

| 名前 | 文の形 |
| --- | --- |
| `LAUNCH_FAILED` | `子の claude を起動できない（{why}）` |
| `NO_CWD` | `作業ディレクトリ {cwd} が無い` |
| `NO_ENTER` | `作業ディレクトリ {cwd} に入れない: {why}` |

## 設定の作業ディレクトリ（`cli.wrapper_settings`・`_cwd`）

満たす決定: 決定 2、受け入れ条件 1・2・3

基点の公開 CLI（`ndf relay run`）は、ラッパーを作る前に `wrapper_settings` で作業ディレクトリを読む。今の `Path.cwd()` は、シェルの作業ディレクトリが既に消えていると `FileNotFoundError` を上げ、`Wrapper._start` の確かめに届く前にトレースバックで終わる。

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_cwd` | `cli.py` | 新設 | `(environ: Mapping[str, str]) -> tuple[Path, Optional[str]]` | 作業ディレクトリを例外なしで読む。`Path.cwd()` が成功すれば `(その値, None)`。`OSError` なら `(Path(label), label)` を返す。`label` は表示の文字列で、`os.readlink("/proc/self/cwd")` の末尾の ` (deleted)` を外したもの、読めなければ環境変数 `PWD`、どちらも無ければ `（不明）` |
| `wrapper_settings` | `cli.py` | 変更 | 変わらない | `Path.cwd()` の代わりに `_cwd(environ)` を使い、2 つ目を `Settings.cwd_lost` に入れる。ほかは変えない |
| `Settings.cwd_lost` | `wrapper.py` | 新設 | フィールド `Optional[str]`（既定は None） | 設定を作るときに `Path.cwd()` が失敗したなら、停止理由に出す作業ディレクトリの文字列。None でなければ起動しない |

- `_cwd` の事後条件: 作業ディレクトリが在れば今と同じ `Path.cwd()` の値と None。`Path.cwd()` が失敗したら、2 つ目が必ず None でない
- 起動できるかは `Path.cwd()` が失敗したことだけで決め、復元した文字列は停止理由の表示にしか使わない。復元したパスで `is_dir()` を確かめると、消したディレクトリと同じ名前のディレクトリが作り直されていた・`/proc` が無い環境で `PWD` が別の実在するディレクトリを指していたとき、確かめを通って別のディレクトリで子を起動する
- 消えた作業ディレクトリの `Path(".").is_dir()` は真を返す（開いたままのディレクトリを指す）ので、`.` へは倒さない（2026-11-19、消したディレクトリの中で `python3 -I` で確かめた。`Path.cwd()` は `FileNotFoundError`、`os.readlink("/proc/self/cwd")` は `/tmp/cwdtest_x (deleted)`）

設定を作った後は、`Wrapper._before_start` の作業ディレクトリの確かめ（[子を起動する関数](#子を起動する関数wrapper_start)）が `Settings.cwd_lost` を見て、プラグインの更新と起動の前に `NO_CWD` の停止理由（`cwd` は `cwd_lost` の文字列）を返し、`_loop` の `_end` が `wrapper.json` の `stopped` と `log.jsonl` の `end` の行を書き、`cli._ended` が `ndf-relay:` の 1 行と終了コード 3 を返す。どれも最初のセグメントの起動と同じ停止の経路である。

単体テストの観点:

- 正常: 在る作業ディレクトリで今と同じ `Path.cwd()` の値と None
- 異常: 作業ディレクトリへ入ってから消し（`monkeypatch.chdir` の後に `rmdir`）、`cli.main(["run", …])` を呼ぶ。偽の起動と偽の更新を差し替え、終了コード 3・標準エラーの `ndf-relay: 子の claude を起動できない（作業ディレクトリ <消したパス> が無い）` の 1 行・`log.jsonl` の最後が `end`（`cause: precondition`）・`wrapper.json` が `stopped`・`spawn` も更新も呼ばれない・トレースバックが出ない。`/proc/self/cwd` と `PWD` を読めないとき（差し替え）は `（不明）` の文で同じ
- 異常: 作業ディレクトリへ入ってから消し、同じパスにディレクトリを作り直してから `cli.main(["run", …])` を呼ぶ。`Path.cwd()` は失敗したままなので、同じく終了コード 3 で `spawn` も更新も呼ばれない。`/proc/self/cwd` を読めない差し替えで `PWD` を別の実在するディレクトリにしても同じ（停止理由の文には `PWD` の値が出る）

## 擬似端末の起動（`spawn_pty`・`_enter`）

満たす決定: 決定 2、受け入れ条件 1・2・3・7

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `spawn_pty` | `wrapper.py` | 変更 | 変わらない | 擬似端末で子を起動し、作業ディレクトリへは子の `preexec_fn` で入る。`PtyProcess.spawn` へ `cwd=None` と `preexec_fn=functools.partial(_enter, cwd)` を渡す |
| `_enter` | `wrapper.py` | 新設 | `(cwd: Path) -> None` | fork した子で `os.chdir(cwd)` し、`OSError` を `type(exc)(NO_ENTER.format(cwd=cwd, why=exc.strerror))` で上げ直す |

- `spawn_pty` の事後条件: 子が入れなければ親で `OSError`（型は子の `chdir` の例外のまま、文は `NO_ENTER` の形）
- `_enter` の事前条件: fork した子の中だけで呼ぶ。副作用は `chdir` だけで、ロックも入出力もしない

異常系:

| 失敗 | 原因 | 検出する場所 | 扱い | 後始末 |
| --- | --- | --- | --- | --- |
| 子が作業ディレクトリへ入れない | `is_dir()` の後に消えた・入る権限が無い・ディレクトリでなくなった | 子の `os.chdir` の `OSError`。ptyprocess が型と文を親へ返し、親の `spawn` が同じ型で上げる | `Wrapper._start` の `OSError` と同じ経路で止める | 子は claude を実行せずに終わっている（ptyprocess が `os._exit(1)` し、親が回収する） |

単体テストの観点:

- 正常: 本物の擬似端末で、子の `pwd` が作業ディレクトリを出す
- 異常: `is_dir()` の後・`spawn_pty` の前にディレクトリを消すと `FileNotFoundError`。入る権限の無いディレクトリ（0o600。root では飛ばす）で `PermissionError`。どちらも `Wrapper.run` が `precondition`・`end` の行・`stopped` で終わり、`spawn` は 1 回

## 子を起動する関数（`Wrapper._start`）

満たす決定: 決定 1・2、受け入れ条件 1・2・3・5

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._start` | `wrapper.py` | 変更 | `(self, req: Launch) -> None` から `(self, req: Launch) -> Optional[Ended]` へ | 子を起動し、起動できなければ理由を持つ停止理由を返す |
| `Wrapper._before_start` | `wrapper.py` | 変更 | 変わらない | 先頭で作業ディレクトリを確かめ、`self.s.cwd_lost` が None でないか、`self.s.cwd.is_absolute() and self.s.cwd.is_dir()` が偽なら、プラグインの更新も確認もせずに `Ended("precondition", LAUNCH_FAILED.format(why=NO_CWD.format(cwd=…)))` を返す。プラグインの更新（`plugin_update.Updater`）は作業ディレクトリでコマンドを打つので、更新より前に確かめる |

- 事前条件: 前の子は止めてある（呼び出し元が `_stop_child` を先に呼ぶ）
- 事後条件: 起動したら `self.section` が新しい子を持ち、`running` が書かれて None を返す。起動できなければ `self.section` は前のまま、`Ended("precondition", LAUNCH_FAILED.format(why=…))` を返し、このとき置いたマーカーは消してある
- 手順: (1) `self.s.cwd.is_absolute() and self.s.cwd.is_dir()` が偽なら、マーカーを置く前に `NO_CWD` の文で停止理由を返す（呼び出し元はどれも先に `_before_start` を呼ぶので、ふつうはそこで止まっている。`_before_start` の後に消えたときのために残す） (2) 今と同じく引数と環境を作り、最初の入力があればマーカーを置く (3) `self.spawn` を呼び、`OSError` なら `markers.take` で置いたマーカーを消して、例外の文で停止理由を返す (4) 起動したら今と同じ
- 停止理由の記録（`wrapper.json` の `stopped` と `log.jsonl` の `end` の行）は `_start` では書かず、`_loop` の `_end` が書く。書く場所を 1 つにして、3 か所の文を同じにする

異常系:

| 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行 |
| --- | --- | --- | --- | --- | --- |
| 作業ディレクトリが無い | ワークツリーの片付け・削除（起動の前にシェルの作業ディレクトリが消えていたときを含む。[設定の作業ディレクトリ](#設定の作業ディレクトリcliwrapper_settings_cwd)） | `_before_start` の `cwd_lost` が None でない、か `_before_start`・`_start` の `is_absolute() and is_dir()` が偽 | 止める（更新も `spawn` も呼ばない） | `ndf-relay: 子の claude を起動できない（作業ディレクトリ <cwd> が無い）`、終了コード 3 | 何も残らない。ディレクトリを戻して `claude` を打ち直す |
| `spawn` が `OSError` | `claude` が PATH に無い・実行できない・擬似端末を開けない・子が作業ディレクトリへ入れない | `spawn` の呼び出し | 止める | `ndf-relay: 子の claude を起動できない（<例外の文>）`、終了コード 3。`log.jsonl` の最後は `end`（`cause: precondition`）、`wrapper.json` は `stopped` | 置いたマーカーを `markers.take` で消す。原因を直して打ち直す |

単体テストの観点（偽の起動 `Spawner` と偽の子 `FakeChild` で）:

- 正常: 在る作業ディレクトリで起動し、`spawn` に渡す引数・環境・作業ディレクトリと `section` の行が今と同じ
- 異常: 無い作業ディレクトリで `spawn` を呼ばない。`spawn` の `FileNotFoundError` で `precondition` を返し、マーカーを消す。どちらも `spawn` の呼び出しは多くて 1 回

## 起動の呼び出し元（`_loop`・`_on_switch`・`_on_resume`）

満たす決定: 決定 1、受け入れ条件 2・3・4・5

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._loop` | `wrapper.py` | 変更 | 変わらない | 最初の `_start` が停止理由を返したら、`_end` して返す |
| `Wrapper._on_switch` | `wrapper.py` | 変更 | 変わらない | 前の子を止めてから `_start` を呼び、`_start` の停止理由をそのまま返す（`_loop` が `_end` する） |
| `Wrapper._on_resume` | `wrapper.py` | 変更 | 変わらない | 前の子を止めてから `_start` を呼び、`_start` の停止理由をそのまま返す。`account` と `restart` はどちらもここを通る |

- 前の子を止める（`_stop_child`）順は今のまま `_start` の前に置く。起動に失敗しても前の子は残らない（受け入れ条件 4）
- 停止理由を返した後にポーリングへ戻らないので、同じ起動を打ち直さない（受け入れ条件 5）
- #3814 も `_on_switch`・`_on_resume` の `_start` より前を変える。この課題が変えるのは `_start` の返りを返す部分だけである

単体テストの観点:

- 異常: 無い作業ディレクトリで、最初・`switch`・`restart` のどの起動でも更新と `spawn` を呼ばずに `precondition` で終わる。最初のセグメントは `cli.main` から通す経路でも確かめる（[設定の作業ディレクトリ](#設定の作業ディレクトリcliwrapper_settings_cwd)）
- 異常: 次のセグメントの起動に失敗したとき、前の子が止まっている。`spawn` の呼び出しは 1 回

## 手順書（`relay.md` の「落ちたときの続け方」）

満たす決定: 決定 1、受け入れ条件 6

表に次の 1 行を、「メジャーバージョンが…」の行の後・「上の 3 つ以外」の行の前に足し、「上の 3 つ以外」を「上の 4 つ以外」に直す。

| 1 行 | 状態 | 続け方 |
| --- | --- | --- |
| `子の claude を起動できない（<理由>）` | ラッパーは終わった（終了コード 3）。子は起動していない | 理由（作業ディレクトリが無い・`claude` が PATH に無いなど）を直してから、conductor のブロックの中身で `claude` を打ち直す |

確かめは受け入れ条件の `grep` と `python3 scripts/check-markdown-links.py` で行い、文言のテストは書かない。
