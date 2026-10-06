#!/usr/bin/env python3
"""
r8600_dump.py - Read the memory of an Icom IC-R8600 over USB using the Icom
clone protocol and save it as an .icf file. Read-only: nothing is ever written
to the radio.

Usage:
    python3 r8600_dump.py                       # /dev/ttyUSB0, 115200 baud
    python3 r8600_dump.py -p /dev/ttyUSB0 -b 9600 -o backup.icf
    python3 r8600_icf2chirp.py backup.icf        # then convert as usual

Protocol (PC = 0xEE, radio = 0xEF, frames are FE FE src dst cmd payload FD):
    PC    -> E0 00 00 00 00        model query
    radio -> E1 38 18 00 01 ...    model answer
    PC    -> E2 38 18 00 01        "clone out" = send me your memory
    radio -> E4 <hex text>         data frames, repeated
    radio -> E5 ...                end of clone
Each E4 payload holds: address (4 bytes, big endian), length, data, checksum
(two's complement of the sum of address, length and data bytes). The IC-R8600
sends this as raw binary: bytes 0xFA..0xFF are escaped as 0xFF + low nibble,
and every data byte has its high bit inverted. Older Icoms send ASCII hex.

Requires pyserial:  pip install pyserial
"""

import argparse
import struct
import sys
import time

try:
    import serial
except ImportError:
    serial = None

ADDR_PC = 0xEE
ADDR_RADIO = 0xEF
CMD_ID = 0xE0
CMD_MODEL = 0xE1
CMD_CLONE_OUT = 0xE2
CMD_DATA = 0xE4
CMD_END = 0xE5
MODEL = bytes.fromhex("38180001")
MEMSIZE = 0x1EA00
HEXCHARS = set(b"0123456789ABCDEFabcdef")


def build_frame(cmd, payload):
    return bytes([0xFE, 0xFE, ADDR_PC, ADDR_RADIO, cmd]) + payload + b"\xFD"


class FrameReader:
    """Collects bytes and yields complete (src, dst, cmd, payload) frames."""

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        self.buf += data
        frames = []
        while True:
            start = self.buf.find(b"\xFE\xFE")
            if start < 0:
                self.buf = self.buf[-1:]
                break
            while self.buf[start + 2:start + 3] == b"\xFE":
                start += 1
            end = self.buf.find(b"\xFD", start)
            if end < 0:
                self.buf = self.buf[start:]
                break
            f = self.buf[start:end]
            self.buf = self.buf[end + 1:]
            if len(f) >= 5:
                frames.append((f[2], f[3], f[4], f[5:]))
        return frames


def unescape_raw(payload):
    """Raw frames escape bytes 0xFA..0xFF as 0xFF followed by the low nibble."""
    out = bytearray()
    i = 0
    while i < len(payload):
        b = payload[i]
        if b == 0xFF:
            i += 1
            if i >= len(payload):
                raise ValueError("escape byte at end of frame")
            b = 0xF0 | payload[i]
        out.append(b)
        i += 1
    return bytes(out)


def split_record(raw):
    """Split address / length / data / checksum, verifying the checksum."""
    for alen in (4, 2):
        if len(raw) > alen + 1 and len(raw) == alen + 1 + raw[alen] + 1:
            break
    else:
        raise ValueError("unexpected data frame layout: %s" % raw[:40].hex(" "))
    addr = int.from_bytes(raw[:alen], "big")
    length = raw[alen]
    data = raw[alen + 1:alen + 1 + length]
    calc = (-sum(raw[:-1])) & 0xFF
    if raw[-1] != calc:
        raise ValueError("checksum error at %06X: got %02X, expected %02X"
                         % (addr, raw[-1], calc))
    return addr, data


def decode_data_payload(payload):
    """Return (address, data) from an E4 payload.

    Older Icoms send ASCII hex text. The IC-R8600 sends raw binary frames:
    bytes >= 0xFA escaped, and every data byte with its high bit inverted.
    """
    if set(payload) <= HEXCHARS and len(payload) % 2 == 0:
        try:
            return split_record(bytes.fromhex(payload.decode()))
        except ValueError:
            pass                                  # fall through to raw
    addr, data = split_record(unescape_raw(payload))
    return addr, bytes(b ^ 0x80 for b in data)


def write_icf(path, image, model_hex):
    with open(path, "w", newline="\r\n") as f:
        f.write(model_hex.upper() + "\n")
        f.write("#Comment=\n")
        # Header values as written by CS-R8600; the result opens in CS-R8600.
        f.write("#MapRev=3\n")
        f.write("#EtcData=080014\n")
        for addr in range(0, len(image), 32):
            chunk = image[addr:addr + 32]
            f.write("%08X%02X%s\n" % (addr, len(chunk), chunk.hex().upper()))


def transact(ser, frame, wait):
    """Send a frame and return frames received within `wait` seconds."""
    reader = FrameReader()
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    frames = []
    deadline = time.time() + wait
    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            frames += reader.feed(chunk)
            if any(f[0] == ADDR_RADIO for f in frames):
                break
    return frames


def dump(ser, timeout, log):
    # 1. model query
    frames = transact(ser, build_frame(CMD_ID, b"\x00" * 4), 2.0)
    answers = [f for f in frames if f[0] == ADDR_RADIO and f[2] == CMD_MODEL]
    if not answers:
        sys.exit("No model answer from the radio. Run r8600_probe.py first.")
    model = answers[0][3][:4]
    print("Radio model id %s" % model.hex())
    if model != MODEL:
        sys.exit("Not an IC-R8600 (expected %s)." % MODEL.hex())

    # 2. clone out
    ser.reset_input_buffer()
    ser.write(build_frame(CMD_CLONE_OUT, MODEL))
    ser.flush()

    reader = FrameReader()
    image = bytearray(b"\xFF" * MEMSIZE)
    received = 0
    expected_addr = 0
    gaps = []
    last_rx = time.time()
    end_payload = None
    last_print = 0.0

    while True:
        chunk = ser.read(4096)
        now = time.time()
        if chunk:
            last_rx = now
            if log:
                log.write(chunk)
        elif now - last_rx > timeout:
            print("\nTimeout: no data for %.0f s." % timeout)
            break

        for src, dst, cmd, payload in reader.feed(chunk):
            if src == ADDR_PC:
                continue                      # echo of our own frame
            if cmd == CMD_DATA:
                addr, data = decode_data_payload(payload)
                if addr != expected_addr:
                    gaps.append((expected_addr, addr))
                if addr + len(data) > len(image):
                    image.extend(b"\xFF" * (addr + len(data) - len(image)))
                image[addr:addr + len(data)] = data
                expected_addr = addr + len(data)
                received += len(data)
                if now - last_print < 0.5 and received < MEMSIZE:
                    continue
                last_print = now
                print("\r  %6d / %d bytes (%3d%%)" %
                      (received, MEMSIZE, received * 100 // MEMSIZE),
                      end="", flush=True)
            elif cmd == CMD_END:
                end_payload = payload
                break
            else:
                print("\n  unexpected frame cmd=%02X payload=%s" %
                      (cmd, payload[:40].hex(" ")))
        if end_payload is not None:
            break

    print()
    if end_payload is not None:
        print("Clone end received (payload %s)." % end_payload.hex(" "))
    for a, b in gaps:
        print("  note: address jump %06X -> %06X" % (a, b))
    return model, image, received, end_payload is not None


def main():
    ap = argparse.ArgumentParser(description="Read IC-R8600 memory to .icf")
    ap.add_argument("-p", "--port", default="/dev/ttyUSB0")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-o", "--output", default=None,
                    help="output .icf (default ic-r8600-YYYYMMDD-HHMMSS.icf)")
    ap.add_argument("-t", "--timeout", type=float, default=10.0,
                    help="seconds without data before giving up (default 10)")
    ap.add_argument("--log", help="save all raw bytes from the radio here")
    args = ap.parse_args()

    if serial is None:
        sys.exit("pyserial missing: pip install pyserial")

    out = args.output or time.strftime("ic-r8600-%Y%m%d-%H%M%S.icf")
    log = open(args.log, "wb") if args.log else None
    started = time.time()
    try:
        with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
            model, image, received, complete = dump(ser, args.timeout, log)
    finally:
        if log:
            log.close()

    print("Received %d bytes in %.1f s." % (received, time.time() - started))
    if received == 0:
        sys.exit("Nothing received, no file written.")
    if not complete or received != MEMSIZE:
        print("WARNING: dump looks incomplete, check before trusting it.")
    write_icf(out, image, model.hex())
    print("Wrote %s" % out)


if __name__ == "__main__":
    main()
