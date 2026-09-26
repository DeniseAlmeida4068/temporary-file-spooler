import io
import os
import tempfile
import unittest

from temporary_file_spooler import SpooledBytesIO, SpooledBytesIOError


class TestSpooledBytesIO(unittest.TestCase):

    # ---- happy path --------------------------------------------------
    def test_stays_in_memory_under_threshold(self):
        sp = SpooledBytesIO(max_bytes=100)
        sp.write(b"hello")
        self.assertFalse(sp.spilled())
        self.assertEqual(sp.tell_total(), 5)
        self.assertEqual(sp.getvalue(), b"hello")

    def test_write_returns_byte_count(self):
        sp = SpooledBytesIO(max_bytes=1000)
        n = sp.write(b"abcdef")
        self.assertEqual(n, 6)
        self.assertEqual(sp.tell_total(), 6)

    def test_multiple_writes_accumulate(self):
        sp = SpooledBytesIO(max_bytes=1000)
        sp.write(b"abc")
        sp.write(b"defgh")
        self.assertEqual(sp.getvalue(), b"abcdefgh")
        self.assertEqual(sp.tell_total(), 8)

    # ---- threshold / spill ------------------------------------------
    def test_spill_occurs_when_threshold_exceeded(self):
        sp = SpooledBytesIO(max_bytes=10)
        sp.write(b"1234567890")  # exactly 10, not exceeded
        self.assertFalse(sp.spilled())
        sp.write(b"x")  # now 11, exceeds
        self.assertTrue(sp.spilled())
        self.assertEqual(sp.tell_total(), 11)
        self.assertEqual(sp.getvalue(), b"1234567890x")

    def test_single_oversized_write_causes_spill(self):
        sp = SpooledBytesIO(max_bytes=8)
        n = sp.write(b"this is way more than eight bytes")
        self.assertTrue(sp.spilled())
        self.assertEqual(n, len(b"this is way more than eight bytes"))
        self.assertEqual(sp.getvalue(), b"this is way more than eight bytes")

    def test_zero_max_bytes_spills_on_first_write(self):
        sp = SpooledBytesIO(max_bytes=0)
        sp.write(b"a")
        self.assertTrue(sp.spilled())
        self.assertEqual(sp.getvalue(), b"a")

    def test_writes_after_spill_go_to_file(self):
        sp = SpooledBytesIO(max_bytes=5)
        sp.write(b"hello world")  # spills immediately
        sp.write(b" more")
        self.assertTrue(sp.spilled())
        self.assertEqual(sp.getvalue(), b"hello world more")
        self.assertEqual(sp.tell_total(), len(b"hello world more"))

    # ---- tell semantics ---------------------------------------------
    def test_tell_in_memory_matches_buf_position(self):
        sp = SpooledBytesIO(max_bytes=1000)
        sp.write(b"abc")
        self.assertEqual(sp.tell(), 3)

    def test_tell_after_spill_is_file_offset(self):
        sp = SpooledBytesIO(max_bytes=3)
        sp.write(b"overflow")  # spills
        # After spill the file position is at the end of written data.
        self.assertEqual(sp.tell(), len(b"overflow"))

    def test_getvalue_preserves_position_after_spill(self):
        sp = SpooledBytesIO(max_bytes=3)
        sp.write(b"overflow")
        pos_before = sp.tell()
        _ = sp.getvalue()
        self.assertEqual(sp.tell(), pos_before)

    # ---- bytearray / memoryview -------------------------------------
    def test_write_accepts_bytearray(self):
        sp = SpooledBytesIO(max_bytes=1000)
        n = sp.write(bytearray(b"xyz"))
        self.assertEqual(n, 3)
        self.assertEqual(sp.getvalue(), b"xyz")

    def test_write_accepts_memoryview(self):
        sp = SpooledBytesIO(max_bytes=1000)
        n = sp.write(memoryview(b"xyz"))
        self.assertEqual(n, 3)
        self.assertEqual(sp.getvalue(), b"xyz")

    def test_write_rejects_str(self):
        sp = SpooledBytesIO(max_bytes=1000)
        with self.assertRaises(TypeError):
            sp.write("not bytes")  # type: ignore[arg-type]

    # ---- empty / edge writes ----------------------------------------
    def test_empty_write_is_noop_and_returns_zero(self):
        sp = SpooledBytesIO(max_bytes=5)
        n = sp.write(b"")
        self.assertEqual(n, 0)
        self.assertFalse(sp.spilled())
        self.assertEqual(sp.tell_total(), 0)

    def test_empty_write_after_spill_returns_zero(self):
        sp = SpooledBytesIO(max_bytes=3)
        sp.write(b"overflow")
        n = sp.write(b"")
        self.assertEqual(n, 0)
        self.assertEqual(sp.tell_total(), len(b"overflow"))

    # ---- close semantics --------------------------------------------
    def test_close_is_idempotent(self):
        sp = SpooledBytesIO(max_bytes=100)
        sp.write(b"data")
        sp.close()
        sp.close()  # must not raise

    def test_operations_after_close_raise(self):
        sp = SpooledBytesIO(max_bytes=100)
        sp.write(b"data")
        sp.close()
        with self.assertRaises(ValueError):
            sp.write(b"more")
        with self.assertRaises(ValueError):
            sp.getvalue()
        with self.assertRaises(ValueError):
            sp.tell()
        with self.assertRaises(ValueError):
            sp.tell_total()

    def test_context_manager_closes(self):
        with SpooledBytesIO(max_bytes=100) as sp:
            sp.write(b"data")
            self.assertEqual(sp.getvalue(), b"data")
        with self.assertRaises(ValueError):
            sp.getvalue()

    # ---- constructor validation -------------------------------------
    def test_negative_max_bytes_raises(self):
        with self.assertRaises(ValueError):
            SpooledBytesIO(max_bytes=-1)

    def test_default_max_bytes_is_one_mib(self):
        sp = SpooledBytesIO()
        self.assertEqual(sp.max_bytes, 1024 * 1024)

    # ---- spill produces real temp file ------------------------------
    def test_spill_uses_temp_file_not_memory(self):
        # After a spill the backing store is a real file, so the process's
        # memory footprint should not include the written data. We cannot
        # assert on RSS portably, but we can confirm the temp file exists on
        # disk by inspecting the underlying file object's name.
        sp = SpooledBytesIO(max_bytes=3)
        sp.write(b"overflow content here")
        self.assertTrue(sp.spilled())
        # SpooledBytesIO._file is a TemporaryFile; its .name is a path on
        # disk (or a non-None sentinel on some platforms). We only assert that
        # the attribute exists, which is true for real temp files.
        file_obj = sp._file  # type: ignore[attr-defined]
        self.assertIsNotNone(file_obj)
        self.assertTrue(hasattr(file_obj, "name"))
        sp.close()


if __name__ == "__main__":
    unittest.main()
