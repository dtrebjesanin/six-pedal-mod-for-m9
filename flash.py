"""Send a .m9f firmware file to the Line 6 M9 over MIDI.

The pedal must be in FLASH UPDATE MODE: hold footswitches 1A and 2A while you
power it on, until the display reads "FLASH UPDATE MODE".
Start without --yes for a dry run that sends nothing.

Needs Python 3.8+ and python-rtmidi, for example:
  uv run --python 3.12 --with python-rtmidi python flash.py FILE --port NAME --yes
"""
import argparse
import re
import struct
import sys
import time
import zlib

NOTICE = "\n".join([
    "Six-pedal mod for the Line 6 M9 (unofficial), release 1.0.",
    "This is an unofficial community project. It is not supported, endorsed or maintained by Line 6.",
    "Use it at your own risk. A flash erases all saved scenes and the global settings of the pedal.",
    "To go back, flash the official 2.04 file from the Line 6 site.",
])
IDENT_REQUEST = [0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]
INSTALL = ("python-rtmidi is missing. Run this script with:\n"
           "  uv run --python 3.12 --with python-rtmidi python flash.py ...")


class FlashError(Exception):
    pass


def _say(text, file=None):
    """print() that survives a console or redirect that cannot show every character."""
    file = file or sys.stdout
    try:
        print(text, file=file)
    except UnicodeEncodeError:
        enc = getattr(file, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc), file=file)


def read_messages(path):
    """The SysEx messages of an L6FF file, in order, without the F7 fill bytes."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise FlashError("Cannot read %s: %s" % (path, e.strerror or e))
    if raw[:4] != b"FORM" or raw[8:12] != b"L6FF":
        raise FlashError("%s is not a .m9f firmware file." % path)
    pos, data = 12, None
    while pos + 8 <= len(raw):
        cid, size = struct.unpack(">4sI", raw[pos:pos + 8])
        if cid == b"data":
            try:
                inflater = zlib.decompressobj()
                data = inflater.decompress(raw[pos + 8:pos + 8 + size])
                if not inflater.eof:
                    raise zlib.error("incomplete data")
            except zlib.error:
                raise FlashError("%s is damaged (the data cannot be unpacked)." % path)
            break
        pos += 8 + size + (size & 1)
    if data is None:
        raise FlashError("%s is damaged (no data part)." % path)
    msgs, i = [], 0
    while i < len(data):
        if data[i] == 0xF0:
            j = data.find(0xF7, i + 1)
            if j == -1:
                raise FlashError("%s is damaged (a message is cut short)." % path)
            msgs.append(data[i:j + 1])
            i = j + 1
        elif data[i] == 0xF7:
            i += 1
        else:
            raise FlashError("%s is damaged (unexpected data)." % path)
    if not msgs:
        raise FlashError("%s holds no messages." % path)
    return msgs


def select_port(names, wanted, retry_without_number=False):
    """Index of the port: only digits = an index, else a part of a name (any case)."""
    listing = "Ports: " + (", ".join("%d: %s" % (k, n) for k, n in enumerate(names)) or "(none)")
    if wanted.isdigit():
        if int(wanted) < len(names):
            return int(wanted)
        raise FlashError("No port number %s. %s" % (wanted, listing))
    hits = [k for k, n in enumerate(names) if wanted.lower() in n.lower()]
    if not hits and retry_without_number:
        # Windows adds a list position to each port name, so the in and out name
        # of one interface can differ in the number at the end.
        trimmed = re.sub(r"\s+\d+$", "", wanted)
        if trimmed != wanted and trimmed:
            hits = [k for k, n in enumerate(names) if trimmed.lower() in n.lower()]
    if not hits:
        raise FlashError("No port matches %r. %s" % (wanted, listing))
    if len(hits) > 1:
        raise FlashError("More than one port matches %r. Use a longer part of the name. %s" % (wanted, listing))
    return hits[0]


PAUSE_S = 5.0   # wait after an erase, a section start and a section end (enough on the author's pedal)


def pause_after(msg, gap_ms=60.0, erase_s=PAUSE_S, start_s=PAUSE_S, commit_s=PAUSE_S):
    """Seconds to wait after sending msg."""
    gap = gap_ms / 1000.0
    cmd = msg[6] if len(msg) > 6 else 0
    if cmd == 0x59 or (cmd == 0x5A and len(msg) > 8 and msg[8] == 0x7F):
        return max(gap, 1.0) + erase_s
    if cmd == 0x5A:
        return max(gap, 1.0) + start_s
    if cmd == 0x5C:
        return max(gap, 1.0) + commit_s
    if cmd == 0x5D or (len(msg) > 1 and msg[1] == 0x7E):
        return max(gap, 2.0)
    return gap


def parse_identity(reply):
    """The 4 version characters of an identity reply (F0/F7 optional)."""
    b = list(reply)
    if b and b[0] == 0xF0:
        b = b[1:]
    if b and b[-1] == 0xF7:
        b = b[:-1]
    if len(b) != 15 or b[0] != 0x7E or b[2:4] != [0x06, 0x02]:
        raise FlashError("This is not an identity reply of the M9.")
    return bytes(b[11:15]).decode("ascii", "replace")


ACK_HEADER = [0xF0, 0x00, 0x01, 0x0C, 0x0E, 0x01, 0x5E]   # + <seq> <status> F7, status 00 = OK


def _hex(msg):
    return " ".join("%02X" % x for x in msg)


class Replies:
    """What the pedal answers during a flash: OK replies are counted, all others are shown."""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.ok = 0
        self.not_ok = 0

    def take(self, msg):
        msg = list(msg)
        is_ack = msg[:7] == ACK_HEADER and len(msg) == 10
        note = ""
        if is_ack and msg[8] == 0:
            self.ok += 1
            show = self.verbose
        elif msg[:2] == [0xF0, 0x7E]:          # identity reply to a request inside the stream
            show = self.verbose
        else:
            self.not_ok += 1
            show = True
            if is_ack:
                note = "  (status %02X, not OK)" % msg[8]
        if show:
            _say("  pedal says: " + _hex(msg) + note)


def _drain(midi_in, replies=None):
    while midi_in is not None:
        got = midi_in.get_message()
        if not got:
            return
        if replies is None:
            _say("  pedal says: " + _hex(got[0]))
        else:
            replies.take(got[0])


def _wait(seconds, midi_in, replies=None):
    """Sleep, and read what the pedal sends meanwhile."""
    end = time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            return
        _drain(midi_in, replies)
        time.sleep(min(0.05, left))


def _identity(midi_in, midi_out):
    _drain(midi_in)
    midi_out.send_message(IDENT_REQUEST)
    end = time.time() + 3.0
    while time.time() < end:
        got = midi_in.get_message()
        if got:
            try:
                version = parse_identity(got[0])
            except FlashError:
                continue
            _say("Version: %s  (0204 = official, 02M1 = six-pedal mod 1.0)" % version)
            return 0
        time.sleep(0.005)
    _say("No answer from the pedal within 3 seconds.", file=sys.stderr)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="flash.py", description="Send a .m9f file to the M9 over MIDI. "
        "Without --yes nothing is sent (dry run).")
    ap.add_argument("file", nargs="?", help="the .m9f file to send")
    ap.add_argument("--port", help="MIDI port: a part of its name, as --list shows it")
    ap.add_argument("--list", action="store_true", help="list the MIDI ports and stop")
    ap.add_argument("--identity", action="store_true",
                    help="ask the pedal for its version (0204 = official, 02M1 = mod) and stop")
    ap.add_argument("--yes", action="store_true", help="really send the file")
    ap.add_argument("--verbose", action="store_true", help="show every reply of the pedal")
    ap.add_argument("--ms", type=float, default=60.0, help="gap between messages in ms (60)")
    ap.add_argument("--erase-s", type=float, default=PAUSE_S, help="wait after an erase, seconds (5)")
    ap.add_argument("--start-s", type=float, default=PAUSE_S, help="wait after a section start, seconds (5)")
    ap.add_argument("--commit-s", type=float, default=PAUSE_S, help="wait after a section end, seconds (5)")
    _say(NOTICE)
    _say("")
    args = ap.parse_args(argv)
    if not args.list and not args.identity and not args.file:
        ap.error("the file is required")
    try:
        import rtmidi
    except ImportError:
        _say(INSTALL, file=sys.stderr)
        return 1
    try:
        midi_in, midi_out = rtmidi.MidiIn(), rtmidi.MidiOut()
        midi_in.ignore_types(sysex=False, timing=True, active_sense=True)  # the pedal answers in SysEx
        in_names, out_names = midi_in.get_ports(), midi_out.get_ports()
    except Exception as e:
        _say("Error: cannot use MIDI: %s" % e, file=sys.stderr)
        return 1
    if args.list:
        _say("MIDI out ports:")
        for k, n in enumerate(out_names):
            _say("  %d: %s" % (k, n))
        _say("MIDI in ports:")
        for k, n in enumerate(in_names):
            _say("  %d: %s" % (k, n))
        return 0
    try:
        msgs = None if args.identity else read_messages(args.file)
        if not args.port:
            raise FlashError("Say which port to use with --port (run with --list to see them). "
                             "Out ports: %s" % (", ".join(
                                 "%d: %s" % (k, n) for k, n in enumerate(out_names)) or "(none)"))
        oi = select_port(out_names, args.port)
        try:
            ii = select_port(in_names, args.port, retry_without_number=True)
        except FlashError:
            if args.identity:
                raise
            ii = None
    except FlashError as e:
        _say("Error: %s" % e, file=sys.stderr)
        return 1
    if msgs is not None:
        _say("%s: %d SysEx messages." % (args.file, len(msgs)))
    _say("Port out: %s" % out_names[oi])
    _say("Port in: %s" % (in_names[ii] if ii is not None
                           else "none (replies of the pedal will not be shown)"))
    if msgs is not None and not args.yes:
        _say("DRY RUN: nothing was sent. Run again with --yes to flash.")
        return 0
    try:
        midi_out.open_port(oi)
        if ii is not None:
            midi_in.open_port(ii)
    except Exception as e:
        _say("Error: cannot open the MIDI port (is another program using it?): %s" % e,
              file=sys.stderr)
        return 1
    if ii is None:
        midi_in = None
    if args.identity:
        try:
            return _identity(midi_in, midi_out)
        except Exception as e:
            _say("Error: MIDI failed: %s" % e, file=sys.stderr)
            return 1

    _say("")
    _say(NOTICE)
    _say("Port out: %s" % out_names[oi])
    _say("Check that the display reads FLASH UPDATE MODE. Do not switch the pedal off "
          "or unplug it until it has restarted.")
    sending = False
    replies = Replies(args.verbose)
    try:
        for s in range(10, 0, -1):
            _say("  first message in %d ..." % s)
            time.sleep(1.0)
        _drain(midi_in, replies)
        gap = dict(gap_ms=args.ms, erase_s=args.erase_s, start_s=args.start_s, commit_s=args.commit_s)
        t0 = time.time()
        for k, m in enumerate(msgs):
            sending = True
            midi_out.send_message(list(m))
            wait = pause_after(m, **gap)
            if wait >= 1.0:
                _say("  message %d/%d sent, waiting %.0f s (%.0f s elapsed)" % (
                    k + 1, len(msgs), wait, time.time() - t0))
            elif k % 100 == 0:
                _say("  ...%d/%d (%.0f s)" % (k, len(msgs), time.time() - t0))
            _wait(wait, midi_in, replies)
        _say("Sent %d messages in %.0f s." % (len(msgs), time.time() - t0))
        _wait(15.0, midi_in, replies)
    except (KeyboardInterrupt, Exception) as e:
        if not sending and isinstance(e, KeyboardInterrupt):
            _say("\nStopped before anything was sent. The pedal is unchanged.", file=sys.stderr)
            return 1
        reason = "Stopped" if isinstance(e, KeyboardInterrupt) else "MIDI failed: %s" % e
        _say("\n%s. The pedal may be half-flashed: switch it off, enter FLASH UPDATE MODE "
             "again and flash the file from the start." % reason, file=sys.stderr)
        return 1
    if midi_in is None:
        _say("The replies of the pedal were not checked (no in port).")
    elif replies.not_ok:
        _say("The pedal sent %d %s that %s not OK (shown above). If the display does not show "
             "DOWNLOAD SUCCESS, switch the pedal off, enter FLASH UPDATE MODE again and flash "
             "the file from the start." % (replies.not_ok, "reply" if replies.not_ok == 1 else "replies",
                                           "was" if replies.not_ok == 1 else "were"), file=sys.stderr)
        return 1
    elif replies.ok == 0:
        _say("The pedal sent no replies. Check that the in port is the one the pedal's MIDI out "
             "is connected to.")
    else:
        _say("The pedal confirmed %d messages, all OK." % replies.ok)
    _say("Watch the display: DOWNLOAD SUCCESS, then the pedal restarts by itself.")
    _say("Then run again with --identity to check the version.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
