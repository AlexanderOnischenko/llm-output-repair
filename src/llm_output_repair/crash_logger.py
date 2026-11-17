"""Explicit, local-only crash capture for replayable parser failures.

No instance is created automatically. Raw outputs may contain sensitive data:
users must choose the destination directory and opt in at each integration point.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

_logger = logging.getLogger(__name__)


class CrashLogger:
    """Write one JSON crash dump per failed parse to a caller-selected directory.

    The directory is created on initialization (0700 on POSIX, subject to umask).
    New dump files are private (0600 on POSIX). Existing directory permissions
    are left unchanged. No network activity, uploading, or redaction is done.
    """

    def __init__(self, directory: str | os.PathLike[str], *, max_chars: int = 1_000_000):
        if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
            raise ValueError("max_chars must be a positive integer")
        if not isinstance(directory, (str, os.PathLike)) or not os.fspath(directory):
            raise ValueError("CrashLogger requires an explicit directory")
        self.directory = Path(directory).expanduser()
        self.max_chars = max_chars
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.directory.is_dir():
            raise NotADirectoryError(self.directory)

    def record(self, raw: str, *, format: str, policy: str, error: Exception) -> Path:
        """Persist the input and sanitized failure metadata; return the dump path.

        Error messages and tracebacks are deliberately omitted because they may
        contain unfiltered model output. Inputs longer than max_chars are
        truncated and marked as non-replayable.
        """
        if not isinstance(raw, str):
            raise TypeError("Crash raw output must be a string")
        timestamp = datetime.now(timezone.utc)
        payload = {
            "schema_version": 1,
            "captured_at": timestamp.isoformat().replace("+00:00", "Z"),
            "format": format,
            "policy": policy,
            "error_type": type(error).__name__,
            "raw_output": raw[: self.max_chars],
            "input_chars": len(raw),
            "input_sha256": hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest(),
            "truncated": len(raw) > self.max_chars,
        }
        destination = self.directory / (
            f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}.json"
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=".partial-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                # ASCII escaping permits unpaired Unicode surrogates in malformed output.
                json.dump(payload, handle, indent=2, ensure_ascii=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, destination)
            return destination
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def capture_failure(
    crash_logger: CrashLogger | None,
    raw: str,
    *,
    format: str,
    policy: str,
    error: Exception,
) -> None:
    """Never mask the parser failure if crash persistence itself fails."""
    if crash_logger is None or not isinstance(raw, str):
        return
    try:
        crash_logger.record(raw, format=format, policy=policy, error=error)
    except Exception as logging_error:
        # Do not log error messages: filesystem errors may disclose private paths.
        _logger.warning(
            "Unable to save opted-in crash dump: %s",
            type(logging_error).__name__,
        )
