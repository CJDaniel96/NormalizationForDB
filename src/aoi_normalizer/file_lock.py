"""OS-owned advisory lock; released automatically if the process exits."""

import hashlib
import os
import tempfile
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    pass


class InputLock:
    def __init__(self, input_path: Path):
        identity = os.path.normcase(str(input_path.resolve()))
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.path = Path(tempfile.gettempdir()) / "aoi-normalizer-locks" / f"{key}.lock"
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise AlreadyRunningError(
                "Another process is monitoring this input directory"
            ) from error
        self.stream = stream
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.stream is not None:
            # Closing the descriptor releases the lock on both supported platforms.
            self.stream.close()
            self.stream = None
