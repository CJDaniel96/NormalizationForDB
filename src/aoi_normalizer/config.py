"""Strict JSON configuration, with paths relative to the config file."""

import json
import math
import os
from dataclasses import dataclass, fields
from pathlib import Path, PureWindowsPath


class ConfigError(ValueError):
    """Invalid application configuration."""


@dataclass(frozen=True)
class Paths:
    input: Path
    output: Path
    csv_backup: Path
    xml_archive: Path
    error: Path
    state: Path
    logs: Path


@dataclass(frozen=True)
class Runtime:
    strict_xml_attributes: bool = False
    poll_interval_seconds: float = 10
    settle_seconds: float = 10
    retry_interval_seconds: float = 30
    max_parse_attempts: int = 3
    batch_size: int = 500
    batch_pause_seconds: float = 2
    exclude_directory_names: tuple[str, ...] = ("upload_files",)
    ok_board_statuses: tuple[str, ...] = ("PASS", "OK")


@dataclass(frozen=True)
class Config:
    site: str
    factory: str
    line_id: str
    paths: Paths
    runtime: Runtime


def _object(value: object, label: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object")
    if extra := value.keys() - allowed:
        raise ConfigError(f"Unknown {label} fields: {', '.join(sorted(extra))}")
    if missing := required - value.keys():
        raise ConfigError(f"Missing {label} fields: {', '.join(sorted(missing))}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a nonempty string")
    return value


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _runtime(raw: object) -> Runtime:
    raw = _object(raw, "runtime", {f.name for f in fields(Runtime)}, set())
    values = {f.name: getattr(Runtime(), f.name) for f in fields(Runtime)} | raw
    if type(values["strict_xml_attributes"]) is not bool:
        raise ConfigError("runtime.strict_xml_attributes must be a boolean")
    for name in ("max_parse_attempts", "batch_size"):
        if type(values[name]) is not int or values[name] < 1:
            raise ConfigError(f"runtime.{name} must be a positive integer")
    for name in (
        "poll_interval_seconds",
        "settle_seconds",
        "retry_interval_seconds",
        "batch_pause_seconds",
    ):
        value = values[name]
        minimum = 0 if name in {"settle_seconds", "batch_pause_seconds"} else 0.001
        if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
            raise ConfigError(f"Invalid runtime.{name}: {value!r}")
    for name in ("exclude_directory_names", "ok_board_statuses"):
        value = values[name]
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ConfigError(f"runtime.{name} must be a list of nonempty strings")
        values[name] = tuple(value)
    if any(
        "/" in name or "\\" in name or name in {".", ".."}
        for name in values["exclude_directory_names"]
    ):
        raise ConfigError("exclude_directory_names must contain directory names, not paths")
    statuses = tuple(value.upper() for value in values["ok_board_statuses"])
    if not statuses or "FAIL" in statuses:
        raise ConfigError("ok_board_statuses must be nonempty and cannot contain FAIL")
    values["ok_board_statuses"] = statuses
    return Runtime(**values)


def load_config(filename: Path) -> Config:
    filename = filename.resolve()
    try:
        raw = json.loads(filename.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_keys)
    except (OSError, ValueError) as error:
        raise ConfigError(f"Cannot read config {filename}: {error}") from error
    names = {"schema_version", "machine_brand", "site", "factory", "line_id", "paths", "runtime"}
    raw = _object(raw, "config", names, names - {"runtime"})
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ConfigError("schema_version must be 1")
    if _text(raw["machine_brand"], "machine_brand").lower() != "holly":
        raise ConfigError("Only machine_brand 'holly' is supported")
    path_names = {f.name for f in fields(Paths)}
    paths_raw = _object(raw["paths"], "paths", path_names, path_names)
    paths = {}
    for name, value in paths_raw.items():
        text = _text(value, f"paths.{name}")
        if os.name != "nt" and PureWindowsPath(text).drive:
            raise ConfigError(f"paths.{name} is a Windows path; use the local example on this OS")
        if (
            os.name == "nt"
            and PureWindowsPath(text).drive
            and not PureWindowsPath(text).is_absolute()
        ):
            raise ConfigError(f"paths.{name} must not be drive-relative: {text}")
        path = Path(text).expanduser()
        paths[name] = (filename.parent / path).resolve()
    source = paths["input"]
    managed = {name: path for name, path in paths.items() if name != "input"}
    for name, path in managed.items():
        if source == path or source.is_relative_to(path):
            raise ConfigError(f"paths.{name} cannot equal or contain paths.input")
        for other, other_path in managed.items():
            if name != other and path.is_relative_to(other_path):
                raise ConfigError(f"Managed directories must not overlap: {name}, {other}")
    return Config(
        site=_text(raw["site"], "site"),
        factory=_text(raw["factory"], "factory"),
        line_id=_text(raw["line_id"], "line_id"),
        paths=Paths(**paths),
        runtime=_runtime(raw.get("runtime", {})),
    )
