"""Atomic file publication, verified archival, and persistent job journals."""

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path


class OutputConflict(OSError):
    """An existing output contains different data and must not be overwritten."""


class SourceChanged(OSError):
    """A source was modified after the stable snapshot was read."""


def signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_snapshot(path: Path, expected: tuple[int, int]) -> bytes:
    before = signature(path)
    if before != expected:
        raise SourceChanged(f"Source changed before reading: {path}")
    content = path.read_bytes()
    if signature(path) != before or len(content) != before[0]:
        raise SourceChanged(f"Source changed while reading: {path}")
    return content


def ensure_unchanged(path: Path, expected: tuple[int, int], digest: str) -> None:
    if content_hash(read_snapshot(path, expected)) != digest:
        raise SourceChanged(f"Source content changed: {path}")


def atomic_write(path: Path, content: bytes, *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not replace and path.exists():
        if path.read_bytes() != content:
            raise OutputConflict(f"Refusing to overwrite different content: {path}")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".aoi-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if not replace and path.exists():
            if path.read_bytes() != content:
                raise OutputConflict(f"Refusing to overwrite different content: {path}")
            return
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def archive_xml(
    source: Path, root: Path, relative: Path, content: bytes, expected: tuple[int, int]
) -> Path:
    digest = content_hash(content)
    ensure_unchanged(source, expected, digest)
    target = root / relative
    if target.exists() and target.read_bytes() != content:
        target = target.with_name(f"{target.stem[:100]}.{digest}{target.suffix}")
    # Copy to a temp file on the destination drive, then publish, then remove source.
    # A crash after publication can be recovered by comparing the existing bytes.
    atomic_write(target, content)
    ensure_unchanged(source, expected, digest)
    source.unlink()
    return target


class JobStore:
    def __init__(self, root: Path):
        self.root = root / "jobs"

    def load(self, job_id: str) -> dict:
        path = self.root / f"{job_id}.json"
        if not path.exists():
            return {
                "schema_version": 1,
                "job_id": job_id,
                "phase": "processing",
                "parse_attempts": 0,
                "retry_after": 0,
                "published": [],
            }
        record = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != 1
            or record.get("job_id") != job_id
            or record.get("phase")
            not in {"processing", "outputs_written", "quarantining", "completed", "quarantined"}
            or type(record.get("parse_attempts")) is not int
            or record.get("parse_attempts", -1) < 0
            or not isinstance(record.get("published"), list)
            or any(not isinstance(key, str) for key in record.get("published", []))
            or type(record.get("retry_after")) not in (int, float)
            or not math.isfinite(record.get("retry_after", 0))
        ):
            raise ValueError(f"Invalid job journal; source retained: {path}")
        return record

    def save(self, record: dict) -> None:
        content = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        atomic_write(self.root / f"{record['job_id']}.json", content, replace=True)
