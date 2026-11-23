"""`worker_paths` の保存先のパスを組み、リンクをたどらずに開くところ（`report_paths`・`prepare_paths`・`open_workdir`・`open_resolved`）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ndf.contracts import worker_paths, worker_paths_open


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_report_paths_returns_four_files_under_directory():
    paths = worker_paths.report_paths(Path("/w"), "n")
    assert paths == worker_paths.ReportPaths(
        prompt=Path("/w/n.prompt.md"),
        report=Path("/w/n.report.md"),
        done=Path("/w/n.report.md.done"),
        result=Path("/w/n.result.json"),
    )


@pytest.mark.parametrize("name", ["a", "a" * 64])
def test_report_paths_accepts_names_of_1_and_64_chars(name):
    assert worker_paths.report_paths(Path("/w"), name).report == Path(f"/w/{name}.report.md")


def test_report_paths_absolutizes_relative_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paths = worker_paths.report_paths(Path("d"), "n")
    assert all(p.is_absolute() and p.parent == tmp_path.resolve() / "d" for p in paths)


def test_report_paths_does_not_resolve_link_named_result(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    d = tmp_path / "d"
    d.mkdir()
    (d / "n.result.json").symlink_to(outside)
    assert worker_paths.report_paths(d, "n").result == d.resolve() / "n.result.json"


@pytest.mark.parametrize("name", ["", "../x", "a/b", "日本語", "a" * 65])
def test_report_paths_rejects_bad_names_without_touching_files(tmp_path, name):
    before = _tree(tmp_path)
    with pytest.raises(worker_paths.NameInvalid):
        worker_paths.report_paths(tmp_path, name)
    assert _tree(tmp_path) == before


def test_report_paths_resolves_a_link_to_an_existing_directory_and_keeps_missing_rest(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    assert worker_paths.report_paths(tmp_path / "link" / "new", "n").report == real.resolve() / "new" / "n.report.md"


def test_report_paths_resolves_a_link_after_a_missing_part_cancelled_by_dotdot(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    paths = worker_paths.report_paths(tmp_path / "missing" / ".." / "link", "n")
    assert paths.report == real.resolve() / "n.report.md"
    with worker_paths.open_workdir(paths.report.parent, create=True) as wd:
        assert wd.path == real.resolve()
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("under", [False, True])
def test_report_paths_refuses_a_dangling_link_before_anything_is_created(tmp_path, under):
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "target")
    before = _tree(tmp_path)
    with pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.report_paths(link / "new" if under else link, "n")
    assert e.value.kind == "symlink"
    assert _tree(tmp_path) == before and not (tmp_path / "target").exists()


def test_open_workdir_gives_fd_for_names_and_closes_on_exit(workdir):
    d, _ = workdir
    (d / "f").write_text("x", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        assert wd.path == d.resolve()
        assert os.stat("f", dir_fd=wd.fd).st_size == 1
    with pytest.raises(OSError):
        os.fstat(wd.fd)


def test_open_workdir_refuses_a_link_in_the_path_instead_of_resolving_it(tmp_path, workdir):
    d, _ = workdir
    link = tmp_path / "link"
    link.symlink_to(d)
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(link):
        pass
    assert e.value.kind == "symlink"


def test_report_paths_without_resolve_keeps_a_link_in_the_directory(tmp_path, workdir):
    d, _ = workdir
    link = tmp_path / "link"
    link.symlink_to(d)
    assert worker_paths.report_paths(link, "n", resolve=False).report == link.absolute() / "n.report.md"


def test_open_workdir_refuses_a_link_in_the_middle_of_the_path_as_symlink(tmp_path, workdir):
    d, _ = workdir
    (d / "inner").mkdir()
    link = tmp_path / "link"
    link.symlink_to(d)
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(link / "inner"):
        pass
    assert e.value.kind == "symlink"


def test_open_workdir_creates_missing_directories_when_asked(tmp_path):
    target = tmp_path / "a" / "b"
    with worker_paths.open_workdir(target, create=True) as wd:
        assert wd.path == target.absolute()
    assert target.is_dir()


def test_open_workdir_does_not_create_under_a_link(tmp_path, workdir):
    d, _ = workdir
    link = tmp_path / "link"
    link.symlink_to(d)
    with pytest.raises(worker_paths.EntryUnsafe), worker_paths.open_workdir(link / "new", create=True):
        pass
    assert not (d / "new").exists()


def _foreign(monkeypatch, foreign: Path) -> None:
    """`foreign` の inode だけを別の利用者の持ち物に見せる（テストは別の利用者を作れないため）。"""
    ino = foreign.stat().st_ino
    real_fstat = os.fstat

    def fstat(fd):
        st = real_fstat(fd)
        if st.st_ino != ino:
            return st
        fields = list(st)
        fields[4] = os.geteuid() + 1  # st_uid
        return os.stat_result(fields)

    monkeypatch.setattr(worker_paths_open.os, "fstat", fstat)


def test_open_workdir_refuses_a_created_directory_swapped_for_another_users_one(tmp_path, workdir, monkeypatch):
    d, _ = workdir
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    _foreign(monkeypatch, d)
    target = tmp_path / "new"
    real_mkdir = os.mkdir

    def swap(part, *args, dir_fd=None, **kw):  # 作った直後に名前を前の報告の残る別の利用者のディレクトリへ差し替える
        real_mkdir(part, *args, dir_fd=dir_fd, **kw)
        if part == "new":
            os.rmdir(part, dir_fd=dir_fd)
            os.rename(d, target)

    monkeypatch.setattr(worker_paths_open.os, "mkdir", swap)
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(target, create=True):
        pass
    assert e.value.kind == "replaced"
    assert (target / "n.report.md.done").exists()


def test_open_workdir_refuses_another_users_directory_that_appears_before_mkdir(tmp_path, workdir, monkeypatch):
    d, _ = workdir
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    _foreign(monkeypatch, d)
    target = tmp_path / "new"

    def appear(part, *args, dir_fd=None, **kw):  # 最初の open の後、mkdir の前に別の利用者のディレクトリが現れる
        if part == "new":
            os.rename(d, target)
        raise FileExistsError(part)

    monkeypatch.setattr(worker_paths_open.os, "mkdir", appear)
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(target, create=True):
        pass
    assert e.value.kind == "replaced"
    assert (target / "n.report.md.done").exists()


@pytest.mark.parametrize("before_mkdir", [False, True])
def test_open_workdir_accepts_own_directory_filled_by_a_concurrent_prepare(tmp_path, monkeypatch, before_mkdir):
    target = tmp_path / "new"
    real_mkdir = os.mkdir

    def concurrent(part, *args, dir_fd=None, **kw):  # 同じ利用者の別の prepare が先に作って報告を置く
        if part == "new" and before_mkdir:
            real_mkdir(part, *args, dir_fd=dir_fd, **kw)
            (target / "other.report.md").write_text("", encoding="utf-8")
            raise FileExistsError(part)
        real_mkdir(part, *args, dir_fd=dir_fd, **kw)
        if part == "new":
            (target / "other.report.md").write_text("", encoding="utf-8")

    monkeypatch.setattr(worker_paths_open.os, "mkdir", concurrent)
    with worker_paths.open_workdir(target, create=True) as wd:
        assert worker_paths.entry_state(wd, "other.report.md") is not None
        assert wd.created
        worker_paths.check_entries(wd, ["n.report.md", "n.report.md.done", "n.result.json"])


@pytest.mark.parametrize("before_mkdir", [False, True])
def test_check_entries_refuses_own_existing_directory_renamed_over_a_created_name(tmp_path, workdir, monkeypatch, before_mkdir):
    d, _ = workdir
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    target = tmp_path / "new"
    real_mkdir = os.mkdir

    def swap(part, *args, dir_fd=None, **kw):  # 同じ親の中で、呼んだ利用者の既存のディレクトリを作った名前へ改名する
        if part != "new":
            return real_mkdir(part, *args, dir_fd=dir_fd, **kw)
        if not before_mkdir:
            real_mkdir(part, *args, dir_fd=dir_fd, **kw)
            os.rmdir(part, dir_fd=dir_fd)
        os.rename(d, target)
        if before_mkdir:
            raise FileExistsError(part)

    monkeypatch.setattr(worker_paths_open.os, "mkdir", swap)
    with worker_paths.open_workdir(target, create=True) as wd:
        assert wd.created
        with pytest.raises(worker_paths.EntryUnsafe) as e:
            worker_paths.check_entries(wd, ["n.report.md", "n.report.md.done", "n.result.json"])
    assert e.value.kind == "replaced"
    assert (target / "n.report.md.done").exists()


def test_check_entries_accepts_names_in_a_directory_that_existed(workdir):
    d, _ = workdir
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    with worker_paths.open_workdir(d, create=True) as wd:
        assert not wd.created
        worker_paths.check_entries(wd, ["n.report.md.done"])


def test_prepare_paths_tells_a_missing_directory(tmp_path):
    assert worker_paths.prepare_paths(tmp_path / "new", "n")[1].fresh
    assert not worker_paths.prepare_paths(tmp_path, "n")[1].fresh


def test_check_entries_refuses_a_directory_that_appeared_after_the_paths_were_resolved(tmp_path, workdir):
    d, _ = workdir
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    target = tmp_path / "new"
    paths, origin = worker_paths.prepare_paths(target, "n")
    os.rename(d, target)  # 解いてから開くまでに、既存のディレクトリが同じ名前へ現れる
    with worker_paths.open_workdir(paths.report.parent, create=True, origin=origin) as wd:
        assert wd.created
        with pytest.raises(worker_paths.EntryUnsafe) as e:
            worker_paths.check_entries(wd, ["n.report.md", "n.report.md.done", "n.result.json"])
    assert e.value.kind == "replaced"
    assert (target / "n.report.md.done").exists()


def test_open_workdir_refuses_another_users_directory_that_appeared_after_the_paths_were_resolved(tmp_path, workdir, monkeypatch):
    d, _ = workdir
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    _foreign(monkeypatch, d)
    target = tmp_path / "new"
    paths, origin = worker_paths.prepare_paths(target, "n")
    os.rename(d, target)  # 解いてから開くまでに、別の利用者のディレクトリが同じ名前へ現れる（最初の open で開ける）
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(paths.report.parent, create=True, origin=origin):
        pass
    assert e.value.kind == "replaced"
    assert (target / "n.report.md.done").exists()


def _on_missing(monkeypatch, name: str, action) -> None:
    """解く途中で `name` が無いと観測した直後に `action` を 1 回だけ走らせる。"""
    real_lookup = worker_paths_open._lookup
    done = []

    def lookup(parent, part):
        child = real_lookup(parent, part)
        if part == name and child is None and not done:
            done.append(True)
            action()
        return child

    monkeypatch.setattr(worker_paths_open, "_lookup", lookup)


def test_prepare_paths_keeps_a_part_observed_missing_that_appears_right_after(tmp_path, workdir, monkeypatch):
    d, _ = workdir
    target = tmp_path / "new"
    _on_missing(monkeypatch, "new", lambda: os.rename(d, target))  # 無いと観測した直後に既存のディレクトリが同じ名前へ移される
    paths, origin = worker_paths.prepare_paths(target, "n")
    assert origin.fresh == 1
    assert paths.report == tmp_path.resolve() / "new" / "n.report.md"


def test_prepare_paths_keeps_a_parent_observed_missing_that_appears_before_its_child_is_checked(tmp_path, monkeypatch):
    parent = tmp_path.resolve() / "parent"
    _on_missing(monkeypatch, "parent", parent.mkdir)  # parent が無いと観測した直後に、別の利用者の parent が現れる
    paths, origin = worker_paths.prepare_paths(parent / "child", "n")
    assert origin.fresh == 2
    assert paths.report == tmp_path.resolve() / "parent" / "child" / "n.report.md"


def test_prepare_paths_keeps_a_place_observed_missing_after_dotdot_returns_to_it(tmp_path, workdir, monkeypatch):
    d, _ = workdir
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    target = tmp_path.resolve() / "new"
    _on_missing(monkeypatch, "new", lambda: os.rename(d, target))  # new が無いと観測した後、既存のディレクトリが new へ移される
    paths, origin = worker_paths.prepare_paths(tmp_path / "new" / ".." / "new", "n")
    assert origin.fresh == 1
    assert paths.report == target / "n.report.md"
    with worker_paths.open_workdir(paths.report.parent, create=True, origin=origin) as wd:
        assert wd.created
        with pytest.raises(worker_paths.EntryUnsafe) as e:
            worker_paths.check_entries(wd, ["n.report.md", "n.report.md.done", "n.result.json"])
    assert e.value.kind == "replaced"
    assert (target / "n.report.md.done").exists()


def test_prepare_paths_refuses_a_link_back_to_a_place_observed_missing(tmp_path, workdir, monkeypatch):
    d, _ = workdir
    (d / "n.report.md.done").write_text("", encoding="utf-8")
    target = tmp_path.resolve() / "new"
    (tmp_path / "alias").symlink_to("new")
    _on_missing(monkeypatch, "new", lambda: os.rename(d, target))  # new が無いと観測した後、既存のディレクトリが new へ移される
    with pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.prepare_paths(tmp_path / "new" / ".." / "alias", "n")
    assert e.value.kind == "replaced"
    assert (target / "n.report.md.done").exists()


def test_prepare_paths_refuses_a_parent_swapped_before_an_absolute_link_is_followed(tmp_path, monkeypatch):
    shared = tmp_path.resolve() / "shared"
    (shared / "sub").mkdir(parents=True)
    (shared / "alias").symlink_to(shared / "sub")
    real_lookup = worker_paths_open._lookup
    swapped = []

    def lookup(parent, part):  # alias を読む直前に、shared を移して別の shared/sub を置く
        if part == "alias" and not swapped:
            swapped.append(True)
            shared.rename(tmp_path / "moved")
            (shared / "sub").mkdir(parents=True)
            (shared / "sub" / "n.report.md").write_text("別の作業の報告", encoding="utf-8")
        return real_lookup(parent, part)

    monkeypatch.setattr(worker_paths_open, "_lookup", lookup)
    with pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.prepare_paths(shared / "alias", "n")
    assert e.value.kind == "replaced"
    assert (shared / "sub" / "n.report.md").read_text(encoding="utf-8") == "別の作業の報告"


def test_open_workdir_refuses_a_parent_swapped_after_the_paths_were_resolved(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    paths, origin = worker_paths.prepare_paths(parent / "new", "n")
    parent.rename(tmp_path / "moved")
    (parent / "new").mkdir(parents=True)  # 解いてから開くまでに、あった親が別のディレクトリへ差し替えられる
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(paths.report.parent, create=True, origin=origin):
        pass
    assert e.value.kind == "replaced"
    assert not (parent / "new" / "n.report.md").exists()


def test_prepare_paths_ties_each_part_to_the_parent_it_checked(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    (shared / "sub").mkdir(parents=True)
    real_lookup = worker_paths_open._lookup
    swapped = []

    def lookup(parent, part):  # sub を開く直前に、shared を移して別の shared/sub/new を置く
        if part == "sub" and not swapped:
            swapped.append(True)
            shared.rename(tmp_path / "moved")
            (shared / "sub" / "new").mkdir(parents=True)
            (shared / "sub" / "new" / "n.report.md").write_text("別の作業の報告", encoding="utf-8")
        return real_lookup(parent, part)

    monkeypatch.setattr(worker_paths_open, "_lookup", lookup)
    paths, origin = worker_paths.prepare_paths(shared / "sub" / "new", "n")
    assert origin.fresh == 1  # 確かめた親（移された shared）の下の sub には new が無い
    assert origin.anchor == ((tmp_path / "moved" / "sub").stat().st_dev, (tmp_path / "moved" / "sub").stat().st_ino)
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(paths.report.parent, create=True, origin=origin):
        pass
    assert e.value.kind == "replaced"
    assert (shared / "sub" / "new" / "n.report.md").read_text(encoding="utf-8") == "別の作業の報告"
    assert not (tmp_path / "moved" / "sub" / "new").exists()


def test_open_workdir_does_not_recreate_a_parent_moved_after_the_paths_were_resolved(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    paths, origin = worker_paths.prepare_paths(parent / "new", "n")
    parent.rename(tmp_path / "moved")  # 解いてから開くまでに、あった親が移される
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(paths.report.parent, create=True, origin=origin):
        pass
    assert e.value.kind == "replaced"
    assert not parent.exists() and not (tmp_path / "moved" / "new").exists()


def test_report_paths_refuses_a_file_link_followed_by_dotdot(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "file").write_text("x", encoding="utf-8")
    (victim / "n.report.md").write_text("別の作業の報告", encoding="utf-8")
    (tmp_path / "link").symlink_to(victim / "file")
    before = _tree(tmp_path)
    with pytest.raises(worker_paths.EntryUnsafe) as e:
        worker_paths.report_paths(tmp_path / "missing" / ".." / "link" / "..", "n")
    assert e.value.kind == "other"
    assert _tree(tmp_path) == before


def test_open_workdir_needs_only_search_permission_on_ancestors(tmp_path):
    shared = tmp_path / "shared"
    (shared / "mine").mkdir(parents=True)
    shared.chmod(0o311)
    try:
        with worker_paths.open_workdir(shared / "mine") as wd:
            assert os.stat(".", dir_fd=wd.fd).st_ino == (shared / "mine").stat().st_ino
    finally:
        shared.chmod(0o755)


def test_open_workdir_refuses_a_parent_swapped_to_a_link_after_the_paths_were_built(tmp_path):
    parent = tmp_path / "shared"
    (parent / "d").mkdir(parents=True)
    paths = worker_paths.report_paths(parent / "d", "n")
    other = tmp_path / "other"
    (other / "d").mkdir(parents=True)
    (other / "d" / "n.result.json").write_text("{}", encoding="utf-8")
    parent.rename(tmp_path / "moved")
    parent.symlink_to(other)
    with pytest.raises(worker_paths.EntryUnsafe), worker_paths.open_workdir(paths.report.parent):
        pass
    assert (other / "d" / "n.result.json").read_text(encoding="utf-8") == "{}"


def test_open_workdir_path_is_the_opened_directory(workdir):
    d, _ = workdir
    with worker_paths.open_workdir(d.resolve()) as wd:
        assert wd.path == d.resolve()
        assert os.fstat(wd.fd).st_ino == d.stat().st_ino


def test_open_workdir_stays_on_opened_directory_after_swap(tmp_path, workdir):
    d, _ = workdir
    (d / "r").write_text("元", encoding="utf-8")
    (d / "gone").write_text("消す", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    for n in ("r", "w", "gone"):
        (other / n).write_text("差し替え先", encoding="utf-8")
    with worker_paths.open_workdir(d) as wd:
        d.rename(tmp_path / "moved")
        d.symlink_to(other)
        assert worker_paths.entry_state(wd, "w") is None
        assert worker_paths.read_file(wd, "r") == "元"
        worker_paths.write_file(wd, "w", "新")
        assert worker_paths.remove_file(wd, "gone") == "removed"
    assert {n: (other / n).read_text(encoding="utf-8") for n in ("r", "w", "gone")} == dict.fromkeys(("r", "w", "gone"), "差し替え先")
    assert (tmp_path / "moved" / "w").read_text(encoding="utf-8") == "新"


# --- #3877 r1: `..` が打ち消す要素・保存先の識別子 ---------------------------------------------------


def test_open_workdir_refuses_dotdot_that_cancels_a_link_or_a_missing_part(tmp_path, workdir):
    d, _ = workdir
    elsewhere = tmp_path / "elsewhere" / "inner"
    elsewhere.mkdir(parents=True)
    (d / "alias").symlink_to(elsewhere)
    (d / "file").write_text("x", encoding="utf-8")
    for rel, kind in [("alias/..", "symlink"), ("no-such/..", "other"), ("file/..", "other")]:
        with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_workdir(d / rel):
            pass
        assert e.value.kind == kind, rel


def test_open_workdir_accepts_dotdot_that_cancels_a_real_directory(workdir):
    d, _ = workdir
    (d / "real").mkdir()
    with worker_paths.open_workdir(d / "real" / "..") as wd:
        assert wd.path == d


def test_check_dir_id_refuses_another_directory(workdir):
    d, _ = workdir
    (d / "other").mkdir()
    with worker_paths.open_workdir(d) as wd, worker_paths.open_workdir(d / "other") as other:
        worker_paths.check_dir_id(wd, worker_paths.dir_id(wd))
        with pytest.raises(worker_paths.EntryUnsafe) as e:
            worker_paths.check_dir_id(wd, worker_paths.dir_id(other))
    assert e.value.kind == "replaced"


# --- #3877 r2: 検索権限の無い要素を打ち消す `..` ------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root はアクセス権を無視する")
def test_open_workdir_refuses_dotdot_that_cancels_a_directory_without_search_permission(workdir):
    d, _ = workdir
    (d / "result.json").write_text("前", encoding="utf-8")
    (d / "private").mkdir()
    (d / "private").chmod(0o600)
    try:
        with pytest.raises(PermissionError), worker_paths.open_workdir(d / "private" / ".."):
            pass
    finally:
        (d / "private").chmod(0o700)
    assert (d / "result.json").read_text(encoding="utf-8") == "前"


# --- #3877 r3: 一覧を読めない保存先・祖先のリンクを解いて開く ----------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root はアクセス権を無視する")
@pytest.mark.parametrize("rel", ["", "real/.."])
def test_open_workdir_writes_into_a_directory_whose_listing_cannot_be_read(workdir, rel):
    # 名前を指定した作成・削除に要るのは書き込みと検索の権限だけ。一覧の読み取り権限は求めない
    d, _ = workdir
    (d / "real").mkdir()
    (d / "f").write_text("前", encoding="utf-8")
    d.chmod(0o300)
    try:
        with worker_paths.open_workdir(d / rel if rel else d) as wd:
            worker_paths.write_file(wd, "f", "後")
            worker_paths.write_file(wd, "g", "作った")
            assert worker_paths.read_file(wd, "f") == "後"
            assert worker_paths.remove_file(wd, "g") == "removed"
    finally:
        d.chmod(0o700)
    assert sorted(p.name for p in d.iterdir()) == ["f", "real"]


def test_open_resolved_resolves_ancestor_links_and_leaves_a_cancelled_part_to_the_check(tmp_path):
    real = tmp_path / "real"
    (real / "w" / "sub").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    (real / "w" / "alias").symlink_to(real / "w" / "sub")
    base = real.resolve() / "w"
    with worker_paths.open_resolved(tmp_path / "link" / "w") as wd:
        assert wd.path == base and worker_paths.dir_id(wd) == (base.stat().st_dev, base.stat().st_ino)
    with worker_paths.open_resolved(tmp_path / "link" / "w" / "sub" / "..") as wd:
        assert wd.path == base and worker_paths.dir_id(wd) == (base.stat().st_dev, base.stat().st_ino)
    # `..` が打ち消す要素から先はリンクをたどらずに開く
    with pytest.raises(worker_paths.EntryUnsafe) as e, worker_paths.open_resolved(tmp_path / "link" / "w" / "alias" / ".."):
        pass
    assert e.value.kind == "symlink"


@pytest.mark.skipif(os.geteuid() == 0, reason="root はアクセス権を無視する")
def test_open_resolved_refuses_dotdot_in_a_link_target_through_a_directory_without_search_permission(workdir):
    # #3877 r4: リンクの先の `..` も記述子からカーネルに引かせる。検索権限の無いディレクトリは `..` で通れない
    d, _ = workdir
    (d / "private").mkdir()
    (d / "via").symlink_to("private/..")
    (d / "private").chmod(0o600)
    try:
        with pytest.raises(PermissionError), worker_paths.open_resolved(d / "via"):
            pass
    finally:
        (d / "private").chmod(0o700)


def test_open_resolved_refuses_a_link_placed_in_a_shared_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "state").symlink_to(real)
    shared.chmod(0o777)  # ほかの利用者も保存先の名前を差し替えられる共有の領域
    try:
        with pytest.raises(worker_paths.EntryUnsafe) as e:
            with worker_paths.open_resolved(shared / "state"):
                pass
    finally:
        shared.chmod(0o755)
    assert e.value.kind == "symlink"


def test_open_resolved_follows_own_link_in_a_sticky_shared_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "state").symlink_to(real)
    shared.chmod(0o1777)  # /tmp と同じ: 自分のリンクはほかの利用者に消されない
    try:
        with worker_paths.open_resolved(shared / "state") as wd:
            assert wd.path == real.resolve()
    finally:
        shared.chmod(0o755)
