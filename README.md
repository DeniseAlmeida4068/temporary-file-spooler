# Temporary File Spooler

A small, zero-dependency Python library that buffers bytes in memory and transparently spills them to a temporary file once a configurable threshold is crossed.

## Usage

```python
from temporary_file_spooler import SpooledBytesIO

with SpooledBytesIO(max_bytes=4096) as spool:
    spool.write(b"some data")
    if spool.spilled():
        print("overflowed to disk")
    data = spool.getvalue()  # full contents, regardless of backing store
    total = spool.tell_total()  # bytes written across the object's lifetime
```

Exported names: `SpooledBytesIO`, `SpooledBytesIOError`.

## Why this exists

When a caller wants to assemble a byte payload of unknown size — a serialized response, a tarball-in-progress, a decoded attachment — holding the whole thing in memory risks OOM, while writing straight to disk for every small payload is wasteful. This library sits in the middle: stay in memory until a threshold is exceeded, then move to a temp file without the caller changing code.

The trade-off is a deliberately narrow API. There is no `seek`, `read`, or `truncate`. Random access across the memory-to-disk transition is a meaningfully different problem, and supporting it well is what `tempfile.SpooledTemporaryFile` already does. Callers who need random access should use that instead.

## Edge cases worth knowing

- The threshold is checked **after** each write, not before. A single write larger than `max_bytes` will spill to disk and the full write is preserved.
- `max_bytes=0` means "always spill"; the first write goes straight to a temp file.
- After a spill, `tell()` reports the position within the temp file, not the cumulative byte count. Use `tell_total()` for the latter.
- `getvalue()` restores the position of the underlying file after reading, so it is safe to call between writes.

## Running the tests

```
PYTHONPATH=src python -m unittest discover -s tests
```
