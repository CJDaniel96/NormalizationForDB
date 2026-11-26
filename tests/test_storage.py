import os
import subprocess
import sys
from pathlib import Path

import pytest

from aoi_normalizer.file_lock import InputLock
from aoi_normalizer.storage import (
    OutputConflict,
    SourceChanged,
    archive_xml,
    atomic_write,
    signature,
)


def test_atomic_publication_never_overwrites_different_content(tmp_path):
    target = tmp_path / "out.csv"
    atomic_write(target, b"original")
    atomic_write(target, b"original")
    with pytest.raises(OutputConflict):
        atomic_write(target, b"different")
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]


def test_changed_source_is_not_removed_during_archival(tmp_path):
    source = tmp_path / "source.xml"
    source.write_bytes(b"original")
    observed = signature(source)
    source.write_bytes(b"changed source")
    with pytest.raises(SourceChanged):
        archive_xml(source, tmp_path / "archive", Path("source.xml"), b"original", observed)
    assert source.read_bytes() == b"changed source"


def test_archive_copy_can_be_recovered_after_interrupted_source_removal(tmp_path, monkeypatch):
    source = tmp_path / "source.xml"
    source.write_bytes(b"original")
    observed = signature(source)
    original = Path.unlink

    def fail_source_removal(path, *args, **kwargs):
        if path == source:
            raise PermissionError("source in use")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_source_removal)
    archive = tmp_path / "archive"
    with pytest.raises(PermissionError):
        archive_xml(source, archive, Path("source.xml"), b"original", observed)
    assert (archive / "source.xml").read_bytes() == b"original"
    assert source.exists()
    monkeypatch.setattr(Path, "unlink", original)
    archive_xml(source, archive, Path("source.xml"), b"original", observed)
    assert not source.exists()
    assert len(list(archive.glob("*.xml"))) == 1


def test_input_lock_blocks_another_process_and_releases_on_exit(tmp_path):
    code = """
import sys
from pathlib import Path
from aoi_normalizer.file_lock import InputLock, AlreadyRunningError
try:
    with InputLock(Path(sys.argv[1])):
        pass
except AlreadyRunningError:
    sys.exit(2)
"""
    environment = os.environ | {"PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    with InputLock(tmp_path):
        locked = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=environment)
    released = subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=environment)
    assert locked.returncode == 2
    assert released.returncode == 0
