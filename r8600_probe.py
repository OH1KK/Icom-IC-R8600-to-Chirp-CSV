#!/usr/bin/env python3
"""
r8600_probe.py - Check whether an Icom IC-R8600 answers the Icom clone
protocol model query. Read-only: sends only the ID query, never clone data.

Usage:
    python3 r8600_probe.py                      # try /dev/ttyUSB0 and ttyUSB1
    python3 r8600_probe.py -p /dev/ttyUSB1 -b 38400

Frame format (PC -> radio):
    FE FE  EE EF  E0  00 00 00 00  FD
    preamble  src dst  cmd  payload    end
Expected answer (radio -> PC): command E1 with the model id 38 18 00 01.

Requires pyserial:  pip install pyserial
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing: pip install pyserial")

ADDR_PC = 0xEE
ADDR_RADIO = 0xEF
CMD_ID = 0xE0
CMD_MODEL = 0xE1
R8600_MODEL = bytes.fromhex("38180001")

DEFAULT_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1"]
DEFAULT_BAUDS = [9600, 19200, 38400, 57600, 115200]


def build_frame(cmd, payload):
    return bytes([0xFE, 0xFE, ADDR_PC, ADDR_RADIO, cmd]) + payload + b"\xFD"


def split_frames(data):
    """Return [(src, dst, cmd, payload)] for each FE FE ... FD frame."""
    frames = []
    i = 0
    while True:
        start = data.find(b"\xFE\xFE", i)
        if start < 0:
            break
        while data[start + 2:start + 3] == b"\xFE":   # extra FE padding
            start += 1
        end = data.find(b"\xFD", start)
        if end < 0 or end - start < 5:
            break
        f = data[start:end]
        frames.append((f[2], f[3], f[4], f[5:]))
        i = end + 1
    return frames


def probe(port, baud, wait):
    try:
        ser = serial.Serial(port, baud, timeout=0.1)
    except serial.SerialException as e:
        print("  %-14s %6d  cannot open: %s" % (port, baud, e))
        return False

    with ser:
        ser.reset_input_buffer()
        query = build_frame(CMD_ID, b"\x00\x00\x00\x00")
        ser.write(query)
        ser.flush()

        data = b""
        deadline = time.time() + wait
        while time.time() < deadline:
            chunk = ser.read(256)
            if chunk:
                data += chunk
                deadline = max(deadline, time.time() + 0.3)

    if not data:
        print("  %-14s %6d  no answer" % (port, baud))
        return False

    print("  %-14s %6d  %d bytes: %s" % (port, baud, len(data), data.hex(" ")))
    found = False
    for src, dst, cmd, payload in split_frames(data):
        if src == ADDR_PC and cmd == CMD_ID:
            print("      echo of our own query (USB echo back is on)")
            continue
        print("      frame src=%02X dst=%02X cmd=%02X payload=%s" %
              (src, dst, cmd, payload.hex(" ")))
        if cmd == CMD_MODEL:
            model = payload[:4]
            print("      -> model id %s %s" % (
                model.hex(), "= IC-R8600 \u2714" if model == R8600_MODEL
                else "(not IC-R8600)"))
            text = bytes(b for b in payload[4:] if 32 <= b < 127)
            if text:
                print("      -> extra text: %r" % text.decode())
            found = found or model == R8600_MODEL
    return found


def main():
    ap = argparse.ArgumentParser(description="IC-R8600 clone protocol probe")
    ap.add_argument("-p", "--port", action="append",
                    help="serial port (repeatable, default ttyUSB0 and ttyUSB1)")
    ap.add_argument("-b", "--baud", type=int, action="append",
                    help="baud rate (repeatable, default 9600..115200)")
    ap.add_argument("-w", "--wait", type=float, default=1.0,
                    help="seconds to wait for an answer (default 1.0)")
    args = ap.parse_args()

    ports = args.port or DEFAULT_PORTS
    bauds = args.baud or DEFAULT_BAUDS
    hits = []
    for port in ports:
        for baud in bauds:
            if probe(port, baud, args.wait):
                hits.append((port, baud))

    print()
    if hits:
        for port, baud in hits:
            print("IC-R8600 answered on %s at %d baud." % (port, baud))
    else:
        print("No IC-R8600 model answer. Copy the output above back to the chat.")


if __name__ == "__main__":
    main()
