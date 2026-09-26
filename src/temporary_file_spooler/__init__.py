"""Temporary File Spooler.

Writes data to a memory buffer and transparently spills to a temp file
when a threshold is exceeded.
"""

from temporary_file_spooler.core import SpooledBytesIO, SpooledBytesIOError

__all__ = ["SpooledBytesIO", "SpooledBytesIOError"]
