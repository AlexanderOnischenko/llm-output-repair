"""Opt-in, local-only capture of failed public parsing calls.

Raw model output is sensitive data. No recorder is created implicitly, and no
network, logging or upload operation is performed by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping

SCHEMA_VERSION = 1
_ALLOWED_OPERATIONS = frozenset({"parse_json", "repair_json", "repair_yaml"})


@contextmanager
def _directory_lock(directory: Path) -> Iterator[None]:
    """Interprocess lock to enforce a directory quota during publication."""
    fd = os.open(directory / ".crash-recorder.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            # Lock one byte, creating it if the lockfile is empty.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class CrashRecorder:
    """Save failed parses to a caller-owned, private directory.

    Constructing a recorder explicitly opts into storing *unredacted* model
    output. Capture is failure-only. Repeated identical inputs, operation and
    options share a single dump; quota exhaustion or I/O errors drop the dump
    rather than altering the parser's result.
    """

    def __init__(
        self,
        directory: str | os.PathLike[str],
        *,
        capture: str = "failed",
        max_dump_bytes: int = 1_000_000,
        max_total_bytes: int = 100_000_000,
        source: Mapping[str, str] | None = None,
    ) -> None:
        if capture != "failed":
            raise ValueError("Only capture='failed' is supported")
        if type(max_dump_bytes) is not int or max_dump_bytes <= 0:
            raise ValueError("max_dump_bytes must be a positive integer")
        if type(max_total_bytes) is not int or max_total_bytes <= 0:
            raise ValueError("max_total_bytes must be a positive integer")
        if source is not None and (
            not isinstance(source, Mapping)
            or len(source) > 16
            or any(
                not isinstance(k, str) or not isinstance(v, str)
                or len(k) > 128 or len(v) > 512
                for k, v in source.items()
            )
        ):
            raise ValueError("source must contain at most 16 bounded string pairs")

        self.directory = Path(directory).expanduser()
        self.max_dump_bytes = max_dump_bytes
        self.max_total_bytes = max_total_bytes
        self.source = dict(source or {})
        # Never create a default directory at import or parser invocation.
        # Refuse an explicitly supplied symlink or a shared existing folder.
        if self.directory.is_symlink():
            raise ValueError("Crash dump directory cannot be a symlink")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or self.directory.is_symlink():
            raise ValueError("Crash dump directory must be a real directory")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError("Crash dump directory must be private (mode 0700)")

    def record_failure(
        self,
        *,
        operation: str,
        raw: str,
        error: Exception,
        options: Mapping[str, str] | None = None,
    ) -> Path | None:
        """Best-effort atomic save; return the dump path or None if not saved.

        The exception message and traceback are deliberately not serialized:
        either could contain secrets. The raw input itself remains sensitive.
        """
        try:
            return self._write(operation, raw, error, options or {})
        except Exception:
            # Capture must never mask a parser error; also do not log raw data.
            return None

    def _write(
        self, operation: str, raw: str, error: Exception, options: Mapping[str, str]
    ) -> Path | None:
        if operation not in _ALLOWED_OPERATIONS or not isinstance(raw, str):
            return None
        # Avoid allocating large duplicate JSON strings for oversized inputs.
        if len(raw) > self.max_dump_bytes:
            return None
        if any(
            not isinstance(k, str) or not isinstance(v, str)
            or len(k) + len(v) > self.max_dump_bytes
            for k, v in options.items()
        ):
            return None
        inputs = {"raw": raw, "options": dict(options)}
        identity = {"schema_version": SCHEMA_VERSION, "operation": operation, "input": inputs}
        canonical = json.dumps(identity, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        destination = self.directory / f"{digest}.json"

        # The version is read lazily so importing this module has no dependency
        # on the state of package initialization.
        from . import __version__

        payload = {
            **identity,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "library_version": __version__,
            "observed": {"status": "failed", "exception_type": type(error).__name__},
            "source": self.source,
        }
        content = (json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("ascii")
        if len(content) > self.max_dump_bytes or len(content) > self.max_total_bytes:
            return None

        # Write completely to a private temporary file before making it visible.
        fd, tmp_path = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            with _directory_lock(self.directory):
                if destination.exists():
                    return destination  # exact-input deduplication
                size = sum(
                    path.stat().st_size
                    for path in self.directory.glob("*.json")
                    if path.is_file() and not path.is_symlink()
                )
                if size + len(content) > self.max_total_bytes:
                    return None
                # Atomic, no-clobber publish (even when another process races).
                try:
                    os.link(tmp_path, destination)
                except FileExistsError:
                    return destination
            return destination
        finally:
            os.unlink(tmp_path)


def _capture_failure(
    recorder: CrashRecorder | None,
    *,
    operation: str,
    raw: object,
    error: Exception,
    options: Mapping[str, str] | None = None,
) -> None:
    """Internal parser boundary: diagnostics must not affect behavior."""
    if recorder is None or not isinstance(raw, str):
        return
    try:
        recorder.record_failure(operation=operation, raw=raw, error=error, options=options)
    except Exception:
        # Also protect against user-supplied recorder subclasses.
        pass
