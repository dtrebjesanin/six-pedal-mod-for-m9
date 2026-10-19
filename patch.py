#!/usr/bin/env python3
"""Six-pedal mod for the Line 6 M9 (unofficial): patcher.

Reads your own official M7_BFlash_2_04_00.m9f, applies the patch steps from
the patch file and writes a new .m9f. This file contains no Line 6 firmware.
Python 3.8 or later, standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path

NOTICE = "\n".join([
    "Six-pedal mod for the Line 6 M9 (unofficial), release 1.0.",
    "This is an unofficial community project. It is not supported, endorsed or maintained by Line 6.",
    "Use it at your own risk. A flash erases all saved scenes and the global settings of the pedal.",
    "To go back, flash the official 2.04 file from the Line 6 site.",
])
PATCH_FILE = "six-pedal-mod-1.0.patch.json"

OFFICIAL_NAME = "M7_BFlash_2_04_00.m9f"

# The only images this release may read and write (SysEx stream sha256). A patch
# file with other hashes is refused, so --patch cannot write an untested image.
INPUT_STREAM_SHA256 = "88236679c4da9fb6d79c78e36bfadf8b3c7003216fb740dcae98a89d8be690f9"
OUTPUT_STREAM_SHA256 = "52611003291b047d53d333c67e9f566cf355ed072b118b4d1b18bfef02a81ac9"


class PatchError(Exception):
    """The input or the patch is not usable. The message says what to do."""


# ---------------------------------------------------------------- constants

SYSEX_HEADER = bytes([0xF0, 0x00, 0x01, 0x0C, 0x0E, 0x01])
IDENT_REQUEST = bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])
CMD_ERASE_OR_START, CMD_DATA, CMD_END, CMD_QUERY = 0x5A, 0x5B, 0x5C, 0x5D
DATA_CHUNK = 128
SEQ_MODULO = 128
END_ARG = 0x7F
ERASE_BY_ADDRESS = 0x7F
FLASH_BASE = 0x80000000
BLOCK = 0x10000
PAD = 0xF7

# (first block, last block, F7 filler after the message)
ERASE_PLAN = ((0x0A, 0x11, 5), (0x12, 0x1B, 5), (0x1C, 0x1F, 10),
              (0x20, 0x2F, 5), (0x38, 0x3F, 5))
PAD_AFTER_QUERY = 10
PAD_AFTER_SECTION_START = 6
PAD_AFTER_FINAL_QUERY = 10

# Region ids of the three sections, in stream order: section 1, 2, 3.
REGION_IDS = (2, 4, 0)

HEAD_FORMAT = bytes.fromhex("00010000")
HEAD_PRODUCT = bytes.fromhex("000e0001")
HEAD_COUNT = bytes.fromhex("00000001")


# ----------------------------------------------------------------- encoding

def pack7(data: bytes) -> bytes:
    """Groups of [high-bit byte, up to 7 data bytes]; bit 6 = first byte."""
    out = bytearray()
    for k in range(0, len(data), 7):
        grp = data[k:k + 7]
        hi = 0
        for t, byte in enumerate(grp):
            if byte & 0x80:
                hi |= 1 << (6 - t)
        out.append(hi)
        out.extend(b & 0x7F for b in grp)
    return bytes(out)


def unpack7(packed: bytes, n: int) -> bytes:
    """Inverse of pack7; strict (exact length, 7 bit clean)."""
    out, p = bytearray(), 0
    while len(out) < n:
        if p >= len(packed):
            raise ValueError("packed data too short")
        hi = packed[p]
        grp = packed[p + 1:p + 8][:n - len(out)]
        if hi & 0x80 or any(b & 0x80 for b in grp) or not grp:
            raise ValueError("packed data is not 7 bit clean")
        for t, b in enumerate(grp):
            out.append(b | (0x80 if hi & (1 << (6 - t)) else 0))
        p += 1 + len(grp)
    if p != len(packed):
        raise ValueError("packed data longer than the declared length")
    return bytes(out)


def pack_word(value: int) -> bytes:
    """A 32 bit little endian word in the 7 bit packing (5 bytes)."""
    return pack7(struct.pack("<I", value))


def check_byte(body: bytes) -> int:
    return sum(body) & 0x7F


def sysex(cmd: int, body: bytes) -> bytes:
    return SYSEX_HEADER + bytes([cmd]) + body + b"\xF7"


def query_message(arg: int) -> bytes:
    return sysex(CMD_QUERY, bytes([0x00, arg]))


def erase_message(index: int, first_block: int, last_block: int) -> bytes:
    first = FLASH_BASE + first_block * BLOCK
    last = FLASH_BASE + last_block * BLOCK + BLOCK - 1
    return sysex(CMD_ERASE_OR_START,
                 bytes([index, ERASE_BY_ADDRESS]) + pack_word(first) + pack_word(last))


def section_start_message(region_id: int) -> bytes:
    return sysex(CMD_ERASE_OR_START, bytes([0x00, region_id]) + bytes(10))


def data_message(seq: int, chunk: bytes) -> bytes:
    body = bytes([len(chunk) - 1]) + pack7(chunk)
    return sysex(CMD_DATA, bytes([seq]) + body + bytes([check_byte(body)]))


def section_end_message(seq: int) -> bytes:
    return sysex(CMD_END, bytes([seq, END_ARG]) + bytes(10))


# -------------------------------------------------------------------- build

def build_stream(sections) -> bytes:
    """The SysEx stream for three flash sections, with the F7 filler runs."""
    out = bytearray()

    def emit(msg: bytes, filler: int = 0) -> None:
        out.extend(msg)
        out.extend(bytes([PAD]) * filler)

    emit(IDENT_REQUEST)
    emit(query_message(1), PAD_AFTER_QUERY)
    for i, (first, last, filler) in enumerate(ERASE_PLAN):
        emit(erase_message(i, first, last), filler)
    emit(IDENT_REQUEST)
    for region_id, sec in zip(REGION_IDS, sections):
        emit(section_start_message(region_id), PAD_AFTER_SECTION_START)
        count = 0
        for off in range(0, len(sec), DATA_CHUNK):
            count += 1
            out.extend(data_message(count % SEQ_MODULO, sec[off:off + DATA_CHUNK]))
        emit(section_end_message((count + 1) % SEQ_MODULO))
        emit(IDENT_REQUEST)
    emit(query_message(0), PAD_AFTER_FINAL_QUERY)
    return bytes(out)


def _version_bytes(version) -> bytes:
    major, minor, patch = version
    return bytes([major, minor, patch, 0])


def build_container(stream: bytes, version) -> bytes:
    """The .m9f file: IFF "FORM" / "L6FF" with HEAD, dinf and zlib data."""
    ver = _version_bytes(version)
    head = (HEAD_FORMAT + ver + HEAD_PRODUCT + HEAD_COUNT
            + struct.pack(">I", len(stream)) + hashlib.md5(stream).digest() + bytes(16))
    dinf = bytes(4) + ver + bytes(4) + struct.pack(">I", len(stream))
    body = bytearray(b"L6FF")
    for cid, payload in ((b"HEAD", head), (b"dinf", dinf), (b"data", zlib.compress(stream, 9))):
        body += cid + struct.pack(">I", len(payload)) + payload
        if len(payload) & 1:
            body += b"\x00"
    return b"FORM" + struct.pack(">I", len(body)) + bytes(body)


# --------------------------------------------------------------------- read

def _wrong_file() -> PatchError:
    return PatchError("This is not an M9 update file. Use the official file %s "
                      "from the Line 6 site, unchanged." % OFFICIAL_NAME)


def _damaged(why: str) -> PatchError:
    return PatchError("The input file is damaged (%s). Download %s from the Line 6 "
                      "site again." % (why, OFFICIAL_NAME))


def read_stream(raw: bytes) -> bytes:
    """The inflated SysEx stream of a .m9f file; PatchError if it is not usable."""
    if raw[:4] == b"PK\x03\x04":
        raise PatchError("This is a zip archive. Please unpack it first and use the file %s "
                         "inside, unchanged." % OFFICIAL_NAME)
    if raw[:4] != b"FORM" or raw[8:12] != b"L6FF":
        raise _wrong_file()
    pos, chunks = 12, {}
    while pos + 8 <= len(raw):
        cid, size = struct.unpack(">4sI", raw[pos:pos + 8])
        chunks[cid] = raw[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    if not all(c in chunks for c in (b"HEAD", b"dinf", b"data")):
        raise _damaged("a part of the file is missing")
    head = chunks[b"HEAD"]
    if len(head) < 36:
        raise _damaged("the header is cut short")
    try:
        inflater = zlib.decompressobj()
        stream = inflater.decompress(chunks[b"data"])
        if not inflater.eof:
            raise zlib.error("incomplete data")
    except zlib.error:
        raise _damaged("the data cannot be unpacked")
    if struct.unpack(">I", head[16:20])[0] != len(stream):
        raise _damaged("the length does not match")
    if hashlib.md5(stream).digest() != head[20:36]:
        raise _damaged("the checksum does not match")
    return stream


def split_sections(stream: bytes):
    """The three flash sections of a stream (5A start, 5B data, 5C end)."""
    secs, cur, i = [], None, 0
    try:
        while i < len(stream):
            if stream[i] == PAD:
                i += 1
                continue
            if stream[i] != 0xF0:
                raise ValueError("stray byte")
            j = stream.index(0xF7, i + 1)
            m = stream[i:j + 1]
            i = j + 1
            if m[:6] != SYSEX_HEADER:
                continue
            cmd = m[6]
            if cmd == CMD_ERASE_OR_START and m[8] != ERASE_BY_ADDRESS:
                cur = bytearray()
                secs.append(cur)
            elif cmd == CMD_END:
                cur = None
            elif cmd == CMD_DATA and cur is not None:
                if check_byte(m[8:-2]) != m[-2]:
                    raise ValueError("bad check byte")
                cur += unpack7(m[9:-2], m[8] + 1)
    except (ValueError, IndexError):
        raise _damaged("the update data is not readable")
    if len(secs) != 3:
        raise _damaged("it does not hold three sections")
    return [bytes(s) for s in secs]


# -------------------------------------------------------------------- steps

def apply_steps(steps, stock) -> bytes:
    """["copy", n, offset, length] copies from stock section n (1-3);
    ["data", hex] adds the bytes."""
    out = bytearray()
    for step in steps:
        if step[0] == "copy":
            _, n, offset, length = step
            if not (1 <= n <= len(stock)) or offset < 0 or length < 0 \
                    or offset + length > len(stock[n - 1]):
                raise PatchError("The patch does not fit this file. Use the official "
                                 "file %s, unchanged." % OFFICIAL_NAME)
            out += stock[n - 1][offset:offset + length]
        elif step[0] == "data":
            out += bytes.fromhex(step[1])
        else:
            raise PatchError("The patch file has an unknown step.")
    return bytes(out)


# ---------------------------------------------------------------- patch file

def _bad_patch(path, why: str) -> PatchError:
    return PatchError("The patch file %s is not usable (%s). Download the patch file again "
                      "and keep it unchanged." % (path, why))


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_sha(v) -> bool:
    return isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) is not None


def _check_sections(path, where: str, sections) -> None:
    if not isinstance(sections, list) or len(sections) != 3:
        raise _bad_patch(path, "%s needs three sections" % where)
    for sec in sections:
        if not (isinstance(sec, dict) and _is_int(sec.get("length")) and sec["length"] >= 0
                and _is_sha(sec.get("sha256"))):
            raise _bad_patch(path, "%s has a damaged section entry" % where)


def _check_step(path, step) -> None:
    if isinstance(step, list) and step and step[0] == "copy":
        if len(step) == 4 and all(_is_int(v) for v in step[1:]):
            return
    elif isinstance(step, list) and step and step[0] == "data":
        if len(step) == 2 and isinstance(step[1], str):
            try:
                bytes.fromhex(step[1])
                return
            except ValueError:
                pass
    raise _bad_patch(path, "a step is malformed")


def load_patch(path) -> dict:
    """Read and check the patch file; PatchError for anything that is not right."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        raise PatchError("The patch file %s was not found. Keep it next to patch.py, or give "
                         "its path with --patch." % path)
    except OSError as e:
        raise PatchError("The patch file %s cannot be read (%s)." % (path, e.strerror or e))
    try:
        p = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        raise _bad_patch(path, "it is not valid JSON")
    if not isinstance(p, dict) or p.get("format") != 1:
        raise _bad_patch(path, "unknown format")
    version = p.get("version")
    if not (isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z._-]{1,20}", version)):
        raise _bad_patch(path, "no version")
    inp, out = p.get("input"), p.get("output")
    if not (isinstance(inp, dict) and isinstance(out, dict)
            and _is_sha(inp.get("stream_sha256")) and _is_sha(out.get("stream_sha256"))):
        raise _bad_patch(path, "input or output is missing")
    if inp["stream_sha256"] != INPUT_STREAM_SHA256 or out["stream_sha256"] != OUTPUT_STREAM_SHA256:
        raise _bad_patch(path, "it is not the patch file of release 1.0")
    _check_sections(path, "input", inp.get("sections"))
    _check_sections(path, "output", out.get("sections"))
    cv = out.get("container_version")
    if not (isinstance(cv, list) and len(cv) == 3 and all(_is_int(v) and 0 <= v < 256 for v in cv)):
        raise _bad_patch(path, "no container version")
    steps = p.get("sections")
    if not (isinstance(steps, list) and len(steps) == 3 and all(isinstance(s, list) for s in steps)):
        raise _bad_patch(path, "it needs three lists of steps")
    for sec in steps:
        for step in sec:
            _check_step(path, step)
    return p


# ------------------------------------------------------------------ patching

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _same_path(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return os.path.samefile(str(a), str(b))
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def patch_file(in_path, out_path=None, patch_path=None) -> Path:
    """Patch the official 2.04 file and write the result; returns the output path.
    Every check runs before anything is written. PatchError if anything is wrong."""
    in_path = Path(in_path)
    if patch_path is None:
        patch_path = Path(__file__).resolve().parent / PATCH_FILE
    patch = load_patch(patch_path)
    try:
        raw = in_path.read_bytes()
    except FileNotFoundError:
        raise PatchError("The input file %s was not found." % in_path)
    except OSError as e:
        raise PatchError("The input file %s cannot be read (%s)." % (in_path, e.strerror or e))
    stream = read_stream(raw)
    if _sha256(stream) != patch["input"]["stream_sha256"]:
        if _sha256(stream) == patch["output"]["stream_sha256"]:
            raise PatchError("This file is already the six-pedal mod. Patch the official file "
                             "%s from the Line 6 site, unchanged." % OFFICIAL_NAME)
        raise PatchError("This is not the official 2.04 file. Use %s from the Line 6 site, "
                         "unchanged." % OFFICIAL_NAME)
    stock = split_sections(stream)
    want_in = patch["input"]["sections"]
    if any(len(s) != w["length"] or _sha256(s) != w["sha256"] for s, w in zip(stock, want_in)):
        raise PatchError("This is not the official 2.04 file. Use %s from the Line 6 site, "
                         "unchanged." % OFFICIAL_NAME)
    try:
        new = [apply_steps(steps, stock) for steps in patch["sections"]]
    except (ValueError, TypeError, IndexError, OverflowError, MemoryError):
        raise _bad_patch(patch_path, "a step does not work")
    except PatchError as e:
        raise PatchError("%s (patch file: %s)" % (e, patch_path))
    for s, w in zip(new, patch["output"]["sections"]):
        if len(s) != w["length"] or _sha256(s) != w["sha256"]:
            raise _bad_patch(patch_path, "the result does not match the tested image")
    out_stream = build_stream(new)
    if _sha256(out_stream) != patch["output"]["stream_sha256"]:
        raise _bad_patch(patch_path, "the result does not match the tested image")
    data = build_container(out_stream, patch["output"]["container_version"])
    if out_path is None:
        out_path = in_path.parent / ("six-pedal-mod-%s.m9f" % patch["version"])
    out_path = Path(out_path)
    if out_path.is_dir():
        out_path = out_path / ("six-pedal-mod-%s.m9f" % patch["version"])
    if _same_path(in_path, out_path):
        raise PatchError("The output file would overwrite the input file. Choose another "
                         "output path with -o.")
    try:
        out_path.write_bytes(data)
    except OSError as e:
        raise PatchError("The output file %s cannot be written (%s)." % (out_path, e.strerror or e))
    return out_path


# ------------------------------------------------------------- command line

def _say(text: str, file=None) -> None:
    file = file or sys.stdout
    try:
        print(text, file=file)
    except UnicodeEncodeError:
        enc = getattr(file, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc), file=file)


def main(argv=None) -> int:
    _say(NOTICE)
    _say("")
    ap = argparse.ArgumentParser(
        prog="patch.py", description="Write the six-pedal mod from your own official "
        "%s." % OFFICIAL_NAME)
    ap.add_argument("input", help="the official %s, unchanged" % OFFICIAL_NAME)
    ap.add_argument("-o", "--output", help="output path (default: next to the input file)")
    ap.add_argument("--patch", help="patch file (default: %s next to patch.py)" % PATCH_FILE)
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
    try:
        out = patch_file(args.input, args.output, args.patch)
    except PatchError as e:
        _say("REFUSED: %s" % e, sys.stderr)
        return 1
    except OSError as e:
        _say("REFUSED: a file cannot be read or written (%s)." % (e.strerror or e), sys.stderr)
        return 1
    # The .m9f bytes depend on the zlib of your Python, so the file sha256 differs
    # between PCs. The SysEx stream inside is the same everywhere.
    _say("Written: %s" % out)
    _say("Content matches the tested image.")
    _say("Content sha256 (the same on every PC): %s" % OUTPUT_STREAM_SHA256)
    _say("")
    _say("Next steps:")
    _say("  1. Put the pedal into FLASH UPDATE MODE (see the README).")
    _say("  2. Run flash.py with the file above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
