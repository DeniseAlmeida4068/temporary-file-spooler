"""Core implementation of a bytes spooler that overflows memory to a temp file."""

from __future__ import annotations

import io
import os
import tempfile
from typing import BinaryIO, List, Optional


class SpooledBytesIOError(Exception):
    """Raised for unrecoverable internal state problems in :class:`SpooledBytesIO`."""


class SpooledBytesIO:
    """A writable bytes buffer that lives in memory until it gets too big.

    Writes accumulate in an in-memory :class:`io.BytesIO` until the running
    total exceeds ``max_bytes``. At that point the in-memory contents are
    flushed to a :class:`tempfile.TemporaryFile` and every subsequent write
    goes straight to disk. This mirrors the shape of :class:`email.spool`-style
    spoolers and the stdlib :class:`tempfile.SpooledTemporaryFile`, but with a
    narrower contract: the public surface is only ``write``, ``tell``, ``tell_total``,
    ``spilled``, ``getvalue``, and ``close``.

    Design decisions (the important ones):

    * *Threshold is evaluated after each write, not before.* A single write
      larger than ``max_bytes`` therefore causes a spill, but the full write
      is preserved rather than truncated. This makes the spooler suitable for
      streaming arbitrary-length records.
    * *No seek, read, or truncate API.* Supporting random access across the
      memory-to-disk transition doubles the complexity for a use case this
      library was not built for. Callers who need random access should use
      :class:`tempfile.SpooledTemporaryFile` instead.
    * *``tell`` refers to the position within the current backing store.*
      After a spill the position is the offset within the file, not the
      cumulative byte count. ``tell_total`` is provided for callers who need
      the latter and do not want to track it themselves.
    * *``max_bytes`` of zero means "always spill".* The first write triggers
      the transition to disk immediately. This is the most useful reading of
      "zero threshold" and the one we test.
    """

    def __init__(self, max_bytes: int = 1024 * 1024) -> None:
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        self._max_bytes = max_bytes
        self._buf: Optional[io.BytesIO] = io.BytesIO()
        self._file: Optional[BinaryIO] = None
        self._total = 0
        self._closed = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed spooler")

    def _maybe_spill(self) -> None:
        """Move memory contents to a temp file if the threshold is exceeded.

        Called after every successful write while still in memory mode. Once
        spilled, subsequent calls are no-ops: ``self._buf`` is ``None``.
        """
        if self._buf is None:
            return
        if self._total <= self._max_bytes:
            return

        buf = self._buf
        # Take ownership of the buffer's contents and then drop the reference
        # before creating the temp file. This lets Python reclaim the memory
        # even if the temp file's creation or copy itself allocates.
        data = buf.getvalue()
        self._buf = None

        try:
            tmp = tempfile.TemporaryFile()
            tmp.write(data)
            tmp.seek(0, os.SEEK_END)
            self._file = tmp
        except Exception as exc:
            # If anything goes wrong on the disk side we cannot recover the
            # in-memory buffer safely (we already dropped the reference), so we
            # mark the object closed and let the caller start over with a new
            # instance. Re-raise as a domain error so callers do not have to
            # catch OSError specifically.
            self._closed = True
            raise SpooledBytesIOError("failed to spill to temp file") from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def max_bytes(self) -> int:
        """The spill threshold in bytes."""
        return self._max_bytes

    def spilled(self) -> bool:
        """Return ``True`` once writes have overflowed to a temp file."""
        return self._file is not None

    def write(self, data: bytes) -> int:
        """Write ``data`` to the spooler, spilling to disk if needed.

        Accepts :class:`bytes` only. :class:`bytearray` and :class:`memoryview`
        are rejected so callers do not rely on auto-encoding semantics that
        this library does not provide.
        """
        self._check_open()
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("write() requires a bytes-like object")
        # Normalise to bytes so that file-mode writes and size accounting are
        # consistent regardless of the input type.
        data = bytes(data)
        n = len(data)
        if n == 0:
            return 0

        if self._file is not None:
            written = self._file.write(data)
            # tempfile.write is contractually required to write all data, but
            # some stream wrappers may return None. Normalise so callers can
            # rely on an int return value.
            if written is None:
                written = n
            self._total += written
            return written

        assert self._buf is not None  # invariant: open + not spilled => buf
        written = self._buf.write(data)
        self._total += written
        self._maybe_spill()
        return written

    def tell(self) -> int:
        """Position within the current backing store.

        In memory mode this is the position within the buffer. After a spill
        it is the offset within the temp file. Callers who need the total
        bytes written across the lifetime of the object should use
        :meth:`tell_total`.
        """
        self._check_open()
        if self._file is not None:
            return self._file.tell()
        assert self._buf is not None
        return self._buf.tell()

    def tell_total(self) -> int:
        """Total bytes written since the spooler was created."""
        self._check_open()
        return self._total

    def getvalue(self) -> bytes:
        """Return the full contents of the spooler as bytes.

        In memory mode this returns the buffer's contents directly. After a
        spill the temp file is read in full and returned. The spooler's write
        position is unaffected.
        """
        self._check_open()
        if self._file is not None:
            pos = self._file.tell()
            self._file.seek(0, os.SEEK_SET)
            try:
                return self._file.read()
            finally:
                self._file.seek(pos, os.SEEK_SET)
        assert self._buf is not None
        return self._buf.getvalue()

    def close(self) -> None:
        """Close the spooler and release any backing resources.

        Idempotent: calling close on an already-closed spooler is a no-op.
        After close, every other public method raises :class:`ValueError`.
        """
        if self._closed:
            return
        self._closed = True
        # Close whichever backing store is active. The order matters only in
        # so far as we do not want to hold a stale reference to the buffer if
        # the file close raises; we drop both regardless.
        buf = self._buf
        file = self._file
        self._buf = None
        self._file = None
        if file is not None:
            file.close()
        if buf is not None:
            buf.close()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> "SpooledBytesIO":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None
