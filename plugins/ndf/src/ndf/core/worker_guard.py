"""worker のサブコマンドが共有する守り: 呼び出しの誤り（`WorkerError`）と、起動時に開いた保存先との突き合わせ（`check_held`）。

`ndf worker` の本体（`ndf.core.worker`）と書き込みの CLI（`ndf.core.worker_write`）の両方が使うので、どちらにも置かない。
"""

from __future__ import annotations

from ndf.contracts import worker_paths


class WorkerError(Exception):
    """受け渡しの前提が無い・呼び出しの誤り。`kind` は停止理由の種類。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def check_held(wd: worker_paths.WorkDir, doing: str) -> None:
    """worker の中なら `wd` を `ndf worker run` が開いた保存先と、`ndf contracts emit --out` と同じ識別子で突き合わせる（`held_dir_id`）。

    違えば `WorkerError`（precondition）。共有の保存先の名前を差し替えられても、起動時に確かめた保存先の外へ書かない。
    """
    expected = worker_paths.held_dir_id(wd.path)
    if expected is None:
        return
    try:
        worker_paths.check_dir_id(wd, expected)
    except worker_paths.EntryUnsafe:
        raise WorkerError("precondition", f"{wd.path} で{doing}: 起動時に確かめた保存先と違う（差し替えられた）") from None
