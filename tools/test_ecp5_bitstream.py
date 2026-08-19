#!/usr/bin/env python3
"""Deterministic tests for the shared ECP5 frame decoder."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from .ecp5_bitstream import (
        ECP5_PREAMBLE, LSC_PROG_INCR_CMP, LSC_WRITE_COMP_DIC,
        decode_compressed_block, extract_compressed_frames_bytes,
    )
    from .ecp5_frames import parse
except ImportError:
    from ecp5_bitstream import (
        ECP5_PREAMBLE, LSC_PROG_INCR_CMP, LSC_WRITE_COMP_DIC,
        decode_compressed_block, extract_compressed_frames_bytes,
    )
    from ecp5_frames import parse


def pack_tokens(values: list[int], dictionary: list[int]) -> bytes:
    bits = ""
    for value in values:
        if value == 0:
            bits += "0"
        elif value in dictionary:
            bits += "10" + format(dictionary.index(value), "04b")
        else:
            bits += "11" + format(value, "08b")
    bits += "0" * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, "big") if bits else b""


class CompressedFramesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dictionary = [1 << index for index in range(8)] + [
            0xA5, 0x5A, 0x3C, 0xC3, 0xF0, 0x0F, 0x33, 0xCC,
        ]

    def test_prefix_free_literals_dictionary_and_zero(self) -> None:
        # Three useful bytes plus five 64-bit padding bytes per frame.
        first = [0, 0xA5, 0x7E] + [0] * 5
        second = [0x02, 0x5A, 0] + [0] * 5
        payload = pack_tokens(first, self.dictionary) + pack_tokens(second, self.dictionary)
        frames, used = decode_compressed_block(
            payload, 0, 2, 3, 0, self.dictionary)
        self.assertEqual(frames, [bytes(first[:3]), bytes(second[:3])])
        self.assertEqual(used, len(payload))

    def test_truncated_prefix_free_payload_is_rejected(self) -> None:
        payload = pack_tokens([0x7E] + [0] * 7, self.dictionary)
        with self.assertRaisesRegex(ValueError, "ends unexpectedly"):
            decode_compressed_block(payload[:-1], 0, 1, 3, 0, self.dictionary)

    def test_full_compressed_geometry_and_native_order(self) -> None:
        # The smallest supported ECP5 geometry keeps the test compact: an
        # all-zero 80-byte decoded unit takes only ten compressed bytes.
        count = 7562
        patterns = bytes(reversed(self.dictionary[8:]))
        header = (ECP5_PREAMBLE + LSC_WRITE_COMP_DIC + patterns +
                  bytes([LSC_PROG_INCR_CMP, 0]) + count.to_bytes(2, "big"))
        data = header + pack_tokens([0] * 80, self.dictionary) * count
        frames = extract_compressed_frames_bytes(
            data, expected_frames=count, expected_frame_bytes=74)
        self.assertEqual(len(frames), count)
        self.assertEqual(frames[0], bytes(74))
        self.assertEqual(frames[-1], bytes(74))

    def test_wrong_geometry_is_rejected(self) -> None:
        count = 7562
        patterns = bytes(reversed(self.dictionary[8:]))
        data = (ECP5_PREAMBLE + LSC_WRITE_COMP_DIC + patterns +
                bytes([LSC_PROG_INCR_CMP, 0]) + count.to_bytes(2, "big") +
                pack_tokens([0] * 80, self.dictionary) * count)
        with self.assertRaisesRegex(ValueError, "does not match expected"):
            extract_compressed_frames_bytes(data, expected_frame_bytes=106)

    def test_uncompressed_command_stream_still_parses(self) -> None:
        frame0, frame1 = b"abc", b"XYZ"
        data = (b"\xff\xff\xbd\xb3" + b"\x46\x00\x00\x00" +
                b"\x82\x00\x00\x02" + frame0 + frame1 + b"\x7e")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tiny.bit"
            path.write_bytes(data)
            parsed = parse(path, 3)
        self.assertFalse(parsed["compressed"])
        self.assertEqual([frame for _, frame in parsed["frames"]], [frame0, frame1])

    def test_addressed_partial_stream_pairs_every_frame(self) -> None:
        frame0, frame1 = b"abc", b"XYZ"
        data = (b"\xff\xff\xbd\xb3" +
                b"\xb4\x00\x00\x00" + (17).to_bytes(4, "big") +
                b"\x82\x00\x00\x01" + frame0 +
                b"\xb4\x00\x00\x00" + (9).to_bytes(4, "big") +
                b"\x82\x00\x00\x01" + frame1 + b"\x7e")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.bit"
            path.write_bytes(data)
            parsed = parse(path, 3)
        self.assertEqual(parsed["explicit_addresses"], [17, 9])
        self.assertEqual([frame for _, frame in parsed["frames"]], [frame0, frame1])


if __name__ == "__main__":
    unittest.main()
