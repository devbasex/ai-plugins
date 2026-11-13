# 待ちと回復の DBOS の挙動の実測（#3092）

工程の進行表の設計（[issue-3092-design.md](issue-3092-design.md) ）の決定 7 と決定 21 が頼る DBOS 3.1.0 の挙動を、手元の版（`uv.lock` の `dbos==3.1.0`）で確かめた（2026-11-13）。実験のコードは、ステップ 1 つの後に `recv` で答えを待つワークフローである。

```python
@DBOS.step()
def prepare():
    print("step prepare ran", flush=True)

@DBOS.workflow()
def wf():
    prepare()
    print("recv waiting", flush=True)
    ans = DBOS.recv("answer", timeout_seconds=3600)
    print(f"recv got {ans!r}", flush=True)
    return f"done:{ans}"

# start: executor_id と application_version を引数で受けて DBOS.launch() し、
#        SetWorkflowID で wf を始め、3 秒後に状態を出して os._exit(0)
# send:  DBOSClient(system_database_url=URL).send(WID, "approved", "answer")
# resume: 同じく DBOS.launch() し、5 秒後に状態と結果を出して os._exit(0)
# cancel: DBOSClient(system_database_url=URL).cancel_workflow(WID)
```

実行と出力（`INFO` の行を除く。DB は SQLite のファイル 1 つ）:

```text
$ python -I probe.py start E1 V1
step prepare ran
recv waiting
status before exit PENDING executor E1 version V1
$ python -I probe.py send - -
sent; status PENDING
$ python -I probe.py resume E1 V2        # アプリの版が違う
status after 5s PENDING
$ python -I probe.py resume E2 V1        # executor_id が違う
status after 5s PENDING
$ python -I probe.py resume E1 V1        # 同じ executor_id と版
recv waiting
recv got 'approved'
status after 5s SUCCESS
result done:approved
$ WID=probe-wf-2 python -I probe.py start E1 V1
status before exit PENDING executor E1 version V1
$ python -I cancel.py probe-wf-2
after cancel CANCELLED
$ WID=probe-wf-2 python -I probe.py resume E1 V1
status after 5s CANCELLED
```

回復のときに `step prepare ran` が出ないことから、終わったステップは流し直されない。`recv` の待ちの上限は開始からの期限として控えに残るため（`_sys_db.py` の `recv_setup` が `record_sleep` を呼ぶ）、期限を過ぎた後の回復では答えが無くても `None` が戻る。期限の後の回復は実測していない。
