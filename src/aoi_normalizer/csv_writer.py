"""Deterministic CSV artifacts, using the existing output column contracts."""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .models import NG_COLUMNS, OK_COLUMNS, PanelResult, Record


@dataclass(frozen=True)
class Artifact:
    key: str
    path: Path
    content: bytes


def _token(value: str, limit: int) -> str:
    # Keep filenames usable on Windows, including when identifiers contain slashes.
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value[:limit].rstrip(" .") or "unknown"


def _csv_bytes(records: list[Record], columns: tuple[str, ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode("utf-8")


def build_artifacts(panels: list[PanelResult], config: Config, job_id: str) -> list[Artifact]:
    artifacts = []
    for panel in panels:
        tokens = (
            _token(panel.product_name, 24),
            _token(config.site, 12),
            _token(config.factory, 12),
            _token(config.line_id, 12),
            _token(panel.machine, 12),
            _token(panel.carrier, 24),
        )
        stem = f"{panel.timestamp:%Y%m%d%H%M%S}_{'_'.join(tokens)}_{job_id[:16]}_p{panel.index:03}"
        day = f"{panel.timestamp:%Y-%m-%d}"
        for status, records, columns in (
            ("OK", panel.ok_records, OK_COLUMNS),
            ("NG", panel.ng_records, NG_COLUMNS),
        ):
            name = f"{stem}_{status}.csv"
            content = _csv_bytes(records, columns)
            for target, root in (
                ("output", config.paths.output),
                ("backup", config.paths.csv_backup),
            ):
                artifacts.append(
                    Artifact(key=f"{target}/{day}/{name}", path=root / day / name, content=content)
                )
    return artifacts
