"""Recursive polling with stable snapshots, recoverable outputs, and archival."""

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .csv_writer import build_artifacts
from .holly_parser import parse_holly_xml
from .models import DataError
from .storage import (
    JobStore,
    SourceChanged,
    archive_xml,
    atomic_write,
    content_hash,
    ensure_unchanged,
    read_snapshot,
    signature,
)

logger = logging.getLogger(__name__)


@dataclass
class Observation:
    signature: tuple[int, int]
    stable_since: float


@dataclass
class ScanResult:
    discovered: int = 0
    completed: int = 0
    quarantined: int = 0
    failed: int = 0
    pending: int = 0
    scan_errors: int = 0
    batch_full: bool = False


class Pipeline:
    def __init__(self, config: Config, *, clock=time.monotonic, wall_clock=time.time):
        self.config = config
        self.clock = clock
        self.wall_clock = wall_clock
        self.store = JobStore(config.paths.state)
        self.observations: dict[Path, Observation] = {}
        self.retry_until: dict[Path, float] = {}
        # Include output-affecting settings so a different destination is a different job.
        self.contract = {
            "site": config.site,
            "factory": config.factory,
            "line_id": config.line_id,
            "ok_board_statuses": config.runtime.ok_board_statuses,
            "strict_xml_attributes": config.runtime.strict_xml_attributes,
            "destinations": [
                str(getattr(config.paths, name))
                for name in ("output", "csv_backup", "xml_archive", "error")
            ],
        }

    def _discover(self, result: ScanResult) -> list[Path]:
        root = self.config.paths.input
        try:
            available = root.is_dir()
        except OSError:
            available = False
        if not available:
            logger.error("Input is unavailable; waiting for recovery: %s", root)
            result.scan_errors += 1
            return []
        managed = tuple(
            getattr(self.config.paths, name)
            for name in ("output", "csv_backup", "xml_archive", "error", "state", "logs")
        )
        excluded = {name.casefold() for name in self.config.runtime.exclude_directory_names}
        found = []

        def on_error(error: OSError) -> None:
            result.scan_errors += 1
            logger.error("Cannot scan directory; will retry: %s", error)

        def include_directory(path: Path) -> bool:
            try:
                return (
                    path.name.casefold() not in excluded
                    and not path.is_symlink()
                    and not path.is_junction()
                    and not any(path.is_relative_to(target) for target in managed)
                )
            except OSError as error:
                on_error(error)
                return False

        for directory, subdirectories, filenames in os.walk(
            root, followlinks=False, onerror=on_error
        ):
            parent = Path(directory)
            subdirectories[:] = sorted(
                name for name in subdirectories if include_directory(parent / name)
            )
            for name in sorted(filenames):
                path = parent / name
                try:
                    if path.suffix.casefold() == ".xml" and not path.is_symlink():
                        found.append(path)
                except OSError as error:
                    on_error(error)
        return found

    def _job_id(self, relative: Path, digest: str) -> str:
        identity = self.contract | {"source": relative.as_posix(), "sha256": digest}
        return content_hash(json.dumps(identity, sort_keys=True).encode("utf-8"))

    def _quarantine(
        self, path: Path, relative: Path, content: bytes, observed: tuple, record: dict
    ):
        target = archive_xml(path, self.config.paths.error, relative, content, observed)
        record.update(phase="quarantined", archived_path=str(target), retry_after=0)
        self.store.save(record)
        logger.error("Quarantined %s: %s", relative, record["last_error"])
        return "quarantined"

    def _advance(
        self, path: Path, relative: Path, content: bytes, observed: tuple, record: dict
    ) -> str:
        phase = record["phase"]
        if phase not in {"outputs_written", "completed"}:
            try:
                panels = parse_holly_xml(content, self.config)
            except DataError as error:
                ensure_unchanged(path, observed, record["sha256"])
                record["last_error"] = str(error)
                if phase in {"quarantining", "quarantined"}:
                    self.store.save(record)
                    return self._quarantine(path, relative, content, observed, record)
                record["parse_attempts"] += 1
                record["retry_after"] = (
                    self.wall_clock() + self.config.runtime.retry_interval_seconds
                )
                if record["parse_attempts"] >= self.config.runtime.max_parse_attempts:
                    record["phase"] = "quarantining"
                self.store.save(record)
                if record["phase"] == "quarantining":
                    return self._quarantine(path, relative, content, observed, record)
                logger.warning(
                    "Invalid data (%s/%s), retained for retry: %s: %s",
                    record["parse_attempts"],
                    self.config.runtime.max_parse_attempts,
                    relative,
                    error,
                )
                return "failed"
            # Revalidate previously rejected XML with the current parser, including
            # quarantined sources deliberately returned to input after a parser fix.
            record["phase"] = "processing"
            ensure_unchanged(path, observed, record["sha256"])
            for artifact in build_artifacts(panels, self.config, record["job_id"]):
                if artifact.key in record["published"]:
                    continue
                atomic_write(artifact.path, artifact.content)
                record["published"].append(artifact.key)
                self.store.save(record)
            record.update(phase="outputs_written", retry_after=0)
            self.store.save(record)
        target = archive_xml(path, self.config.paths.xml_archive, relative, content, observed)
        record.update(phase="completed", archived_path=str(target), retry_after=0)
        self.store.save(record)
        logger.info("Completed %s -> %s", relative, target)
        return "completed"

    def _process(self, path: Path, observed: tuple[int, int]) -> str:
        content = read_snapshot(path, observed)
        relative = path.relative_to(self.config.paths.input)
        digest = content_hash(content)
        record = self.store.load(self._job_id(relative, digest))
        record.update(source=relative.as_posix(), sha256=digest)
        if record["retry_after"] > self.wall_clock():
            self.retry_until[path] = record["retry_after"]
            return "pending"
        # A writable journal is required before publishing or moving any source data.
        self.store.save(record)
        try:
            outcome = self._advance(path, relative, content, observed, record)
        except SourceChanged:
            raise
        except OSError as error:
            record.update(
                last_error=str(error),
                retry_after=self.wall_clock() + self.config.runtime.retry_interval_seconds,
            )
            self.store.save(record)
            raise
        if outcome == "failed":
            self.retry_until[path] = record["retry_after"]
        return outcome

    def poll(self, *, limit_batch: bool = True) -> ScanResult:
        result = ScanResult()
        files = self._discover(result)
        result.discovered = len(files)
        seen = set(files)
        self.observations = {p: value for p, value in self.observations.items() if p in seen}
        self.retry_until = {p: value for p, value in self.retry_until.items() if p in seen}
        ready = []
        for path in files:
            try:
                current = signature(path)
                previous = self.observations.get(path)
                if previous is None or previous.signature != current:
                    previous = Observation(current, self.clock())
                    self.observations[path] = previous
                    self.retry_until.pop(path, None)
                if (
                    self.clock() - previous.stable_since < self.config.runtime.settle_seconds
                    or self.retry_until.get(path, 0) > self.wall_clock()
                ):
                    result.pending += 1
                else:
                    ready.append((path, current))
            except OSError:
                result.failed += 1
                logger.exception("Cannot inspect source; retained for retry: %s", path)
        attempts = 0
        for index, (path, current) in enumerate(ready):
            if limit_batch and attempts >= self.config.runtime.batch_size:
                result.pending += len(ready) - index
                result.batch_full = True
                break
            try:
                outcome = self._process(path, current)
                setattr(result, outcome, getattr(result, outcome) + 1)
                if outcome != "pending":
                    attempts += 1
            except SourceChanged:
                result.pending += 1
                self.observations.pop(path, None)
                self.retry_until.pop(path, None)
                logger.info("Source changed; waiting for stability again: %s", path)
            except Exception:
                # Programming, filesystem and journal errors never classify XML as bad data.
                attempts += 1
                result.failed += 1
                self.retry_until[path] = (
                    self.wall_clock() + self.config.runtime.retry_interval_seconds
                )
                logger.exception("Processing failed; source retained for retry: %s", path)
        logger.info(
            "Scan: found=%s completed=%s quarantined=%s failed=%s pending=%s scan_errors=%s",
            result.discovered,
            result.completed,
            result.quarantined,
            result.failed,
            result.pending,
            result.scan_errors,
        )
        return result

    def run_once(self) -> ScanResult:
        if self.config.runtime.settle_seconds:
            self.poll(limit_batch=False)
            time.sleep(self.config.runtime.settle_seconds)
        return self.poll(limit_batch=False)

    def watch(self) -> None:
        logger.info("Watching recursively: %s", self.config.paths.input)
        while True:
            result = self.poll()
            delay = (
                self.config.runtime.batch_pause_seconds
                if result.batch_full
                else self.config.runtime.poll_interval_seconds
            )
            time.sleep(delay)
