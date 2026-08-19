#!/usr/bin/env python3
"""Shared ECP5 compressed-CRAM decoder used by PR research tools.

Project Trellis encodes a full compressed CRAM payload with a sixteen-entry
byte dictionary and a prefix-free bit stream.  Keep one checked decoder here:
`pr_delta.py` needs native frame order for comparisons, while
`ecp5_frames.py` needs the same data for geometry and address-map work.
"""

from __future__ import annotations

from pathlib import Path


ECP5_FRAME_BYTES = {
    7562: 74,    # 12F / 25F: 592 bits
    9470: 106,   # 45F: 846 bits plus two pad bits
    13294: 142,  # 85F: 1136 bits
}
ECP5_PREAMBLE = b"\xff\xff\xbd\xb3"
LSC_WRITE_COMP_DIC = b"\x02\x00\x00\x00"
LSC_PROG_INCR_CMP = 0xB8


class NotCompressedBitstream(ValueError):
    """The input does not contain a canonical full-chip compressed payload."""


def _read_bits(data: bytes, bit_offset: int, count: int) -> tuple[int, int]:
    value = 0
    for _ in range(count):
        if bit_offset // 8 >= len(data):
            raise ValueError("compressed frame data ends unexpectedly")
        value = (value << 1) | ((data[bit_offset // 8] >>
                                (7 - bit_offset % 8)) & 1)
        bit_offset += 1
    return value, bit_offset


def decode_compressed_block(data: bytes, byte_offset: int, frame_count: int,
                            frame_bytes: int, flags: int,
                            dictionary: list[int]) -> tuple[list[bytes], int]:
    """Decode one LSC_PROG_INCR_CMP payload in serialized stream order."""
    if len(dictionary) != 16 or any(not 0 <= value <= 0xFF for value in dictionary):
        raise ValueError("compression dictionary must contain sixteen bytes")
    if frame_count < 0 or frame_bytes < 1:
        raise ValueError("invalid compressed frame geometry")

    # Compression pads each decoded frame to a whole 64-bit unit.
    decoded_bytes = frame_bytes + (7 - ((frame_bytes - 1) % 8))
    check_crc = bool(flags & 0x80)
    crc_after_each = check_crc and not bool(flags & 0x40)
    dummy_bytes = flags & 0x0F
    frames: list[bytes] = []

    for frame_number in range(frame_count):
        bit_offset = byte_offset * 8
        decoded = bytearray()
        for _ in range(decoded_bytes):
            first, bit_offset = _read_bits(data, bit_offset, 1)
            if not first:
                decoded.append(0)
                continue
            second, bit_offset = _read_bits(data, bit_offset, 1)
            if second:
                literal, bit_offset = _read_bits(data, bit_offset, 8)
                decoded.append(literal)
            else:
                index, bit_offset = _read_bits(data, bit_offset, 4)
                decoded.append(dictionary[index])

        byte_offset = (bit_offset + 7) // 8
        if crc_after_each or (check_crc and frame_number == frame_count - 1):
            byte_offset += 2
        byte_offset += dummy_bytes
        if byte_offset > len(data):
            raise ValueError("compressed frame trailer ends unexpectedly")
        frames.append(bytes(decoded[:frame_bytes]))
    return frames, byte_offset


def extract_compressed_frames_bytes(
        data: bytes, *, expected_frame_bytes: int | None = None,
        expected_frames: int | None = None) -> list[bytes]:
    """Return a full compressed bitstream's CRAM frames in native order."""
    try:
        preamble = data.index(ECP5_PREAMBLE)
        dict_cmd = data.index(LSC_WRITE_COMP_DIC,
                              preamble + len(ECP5_PREAMBLE))
    except ValueError as exc:
        raise NotCompressedBitstream("no compressed ECP5 payload") from exc

    dict_start = dict_cmd + len(LSC_WRITE_COMP_DIC)
    patterns = data[dict_start:dict_start + 8]
    if len(patterns) != 8:
        raise ValueError("truncated compression dictionary")
    dictionary = [1 << index for index in range(8)] + [0] * 8
    # Patterns are stored pattern7 first, matching Trellis's reader.
    for pattern, index in zip(patterns, range(15, 7, -1)):
        dictionary[index] = pattern

    command = dict_start + 8
    if command + 4 > len(data) or data[command] != LSC_PROG_INCR_CMP:
        # A partial stream may carry a dictionary command but use ordinary
        # frame writes.  Let the command-stream parser handle that case.
        raise NotCompressedBitstream("dictionary is not followed by a compressed block")
    flags = data[command + 1]
    frame_count = int.from_bytes(data[command + 2:command + 4], "big")
    if frame_count not in ECP5_FRAME_BYTES:
        raise ValueError(f"unsupported ECP5 frame count {frame_count}")
    frame_bytes = ECP5_FRAME_BYTES[frame_count]
    if expected_frames is not None and frame_count != expected_frames:
        raise ValueError(f"frame count {frame_count} does not match expected {expected_frames}")
    if expected_frame_bytes is not None and frame_bytes != expected_frame_bytes:
        raise ValueError(
            f"frame width {frame_bytes} bytes does not match expected {expected_frame_bytes}")

    frames, _ = decode_compressed_block(
        data, command + 4, frame_count, frame_bytes, flags, dictionary)
    # ecppack serializes the highest frame first.  Expose native CRAM order,
    # which is also the order used when deciding which frames differ.
    frames.reverse()
    return frames


def extract_compressed_frames(path: str | Path, **kwargs) -> list[bytes]:
    source = Path(path)
    try:
        return extract_compressed_frames_bytes(source.read_bytes(), **kwargs)
    except (NotCompressedBitstream, ValueError) as exc:
        exc.args = (f"{source}: {exc}",)
        raise
