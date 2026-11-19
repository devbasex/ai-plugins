# #3842 の詳細設計: 子の claude を起動できないときの終わり方

設計書（[issue-3842-design.md](./issue-3842-design.md)）の解き方を、ファイルと関数の粒度へ下ろした文書である。別ファイルにしたのは、起動する関数（`Wrapper._start`）の返り値の型を変えるため（#3868 の決定 6 の条件）。パスは `plugins/ndf/` からの相対で書く。

## 地図

触るファイルは 3 つで、新設のファイルは無い。

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   └── wrapper.py          … spawn_pty・_enter（新設）・Wrapper._start・_loop・_on_switch・_on_resume
├── skills/development-workflow/references/
│   └── relay.md            … 「落ちたときの続け方」に 1 行
└── tests/domains/relay/
    └── test_relay_wrapper.py
```

依存の向きは今のまま変わらない。`wrapper` が `markers`（ラッパーが送った入力のマーカー）と `ptyprocess` を呼び、`cli._ended` が `Ended` を終了コードへ変える（`cli.py` は変えない）。

呼び出しの関係:

```text
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

- 事前条件: 前の子は止めてある（呼び出し元が `_stop_child` を先に呼ぶ）
- 事後条件: 起動したら `self.section` が新しい子を持ち、`running` が書かれて None を返す。起動できなければ `self.section` は前のまま、`Ended("precondition", LAUNCH_FAILED.format(why=…))` を返し、このとき置いたマーカーは消してある
- 手順: (1) `self.s.cwd.is_dir()` が偽なら、マーカーを置く前に `NO_CWD` の文で停止理由を返す (2) 今と同じく引数と環境を作り、最初の入力があればマーカーを置く (3) `self.spawn` を呼び、`OSError` なら `markers.take` で置いたマーカーを消して、例外の文で停止理由を返す (4) 起動したら今と同じ
- 停止理由の記録（`wrapper.json` の `stopped` と `log.jsonl` の `end` の行）は `_start` では書かず、`_loop` の `_end` が書く。書く場所を 1 つにして、3 か所の文を同じにする

異常系:

| 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行 |
| --- | --- | --- | --- | --- | --- |
| 作業ディレクトリが無い | ワークツリーの片付け・削除 | `self.s.cwd.is_dir()` が偽 | 止める（`spawn` を呼ばない） | `ndf-relay: 子の claude を起動できない（作業ディレクトリ <cwd> が無い）`、終了コード 3 | 何も残らない。ディレクトリを戻して `claude` を打ち直す |
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

- 異常: 無い作業ディレクトリで、最初・`switch`・`restart` のどの起動でも `spawn` を呼ばずに `precondition` で終わる
- 異常: 次のセグメントの起動に失敗したとき、前の子が止まっている。`spawn` の呼び出しは 1 回

## 手順書（`relay.md` の「落ちたときの続け方」）

満たす決定: 決定 1、受け入れ条件 6

表に次の 1 行を、「メジャーバージョンが…」の行の後・「上の 3 つ以外」の行の前に足し、「上の 3 つ以外」を「上の 4 つ以外」に直す。

| 1 行 | 状態 | 続け方 |
| --- | --- | --- |
| `子の claude を起動できない（<理由>）` | ラッパーは終わった（終了コード 3）。子は起動していない | 理由（作業ディレクトリが無い・`claude` が PATH に無いなど）を直してから、conductor のブロックの中身で `claude` を打ち直す |

確かめは受け入れ条件の `grep` と `python3 scripts/check-markdown-links.py` で行い、文言のテストは書かない。
