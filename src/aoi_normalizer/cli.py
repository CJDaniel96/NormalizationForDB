"""Application entry point for Windows and local development."""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import ConfigError, load_config
from .file_lock import AlreadyRunningError, InputLock
from .pipeline import Pipeline


def _configure_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "aoi_normalizer.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), handler],
        force=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize Holly XML into OK/NG CSV files")
    parser.add_argument("--config", type=Path, default=Path("config/zj_z36_holly.json"))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="Scan once after the stability wait")
    modes.add_argument("--watch", action="store_true", help="Keep polling recursively (default)")
    modes.add_argument(
        "--check-config", action="store_true", help="Validate JSON without writing files"
    )
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.check_config:
            print(f"Configuration valid: {args.config.resolve()}")
            return 0
        with InputLock(config.paths.input):
            _configure_logging(config.paths.logs)
            pipeline = Pipeline(config)
            if args.once:
                result = pipeline.run_once()
                return int(
                    bool(
                        result.failed or result.pending or result.quarantined or result.scan_errors
                    )
                )
            pipeline.watch()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Stopped by user")
        return 0
    except (ConfigError, AlreadyRunningError, OSError) as error:
        print(f"Cannot start AOI normalizer: {error}", file=sys.stderr)
        return 2
    return 0
