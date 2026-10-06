#!/usr/bin/env python3
"""
r8600_restore.py - Write an .icf memory image back to an Icom IC-R8600 over USB
using the Icom clone protocol ("clone in").

THIS OVERWRITES THE WHOLE RADIO MEMORY: channels, groups, scan edges and
settings. By default the script first saves a backup of the current radio
memory, asks for confirmation, and reads the radio back afterwards to verify.

Usage:
    python3 r8600_restore.py backup.icf
    python3 r8600_restore.py backup.icf -p /dev/ttyUSB0 -b 9600

Protocol (PC = 0xEE, radio = 0xEF, frames are FE FE src dst cmd payload FD):
    PC    -> E0 00 00 00 00          model query
    radio -> E1 38 18 00 01 rev ...  model answer (byte 5 = memory map revision)
    PC    -> E3 38 18 00 01          "clone in" = I am sending you memory
    PC    -> E4 <block>              64-byte blocks, repeated
    PC    -> E5 "Icom Inc.A8"        end of clone
    radio -> E6 00                   clone OK (anything else = failure)
Block = address (4 bytes BE), length, data with every byte XOR 0x80, checksum
(two's complement of the sum of the bytes before it). Bytes 0xFA..0xFF in the
block are escaped as 0xFF + low nibble.

Requires pyserial (pip install pyserial) and r8600_dump.py in the same folder.
"""

import argparse
import os
import sys
import time

try:
    import serial
except ImportError:
    serial = None

import r8600_dump as dump

CMD_CLONE_IN = 0xE3
CMD_CLONE_OK = 0xE6
BLOCK = 64
DEFAULT_ENDFRAME = b"Icom Inc.A8"     # what the IC-R8600 itself sends at the end
CHAN_BITMAP = 0x1CFCC


def load_icf(path):
    """Return (model hex, headers dict, image, set of covered addresses)."""
    image = bytearray(b"\xFF" * dump.MEMSIZE)
    covered = bytearray(dump.MEMSIZE)
    headers = {}
    model = None
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                key, _, val = line[1:].partition("=")
                headers[key] = val
                continue
            if model is None and len(line) == 8:
                model = line
                continue
            addr = int(line[0:8], 16)
            length = int(line[8:10], 16)
            data = bytes.fromhex(line[10:10 + 2 * length])
            if len(data) != length or addr + length > dump.MEMSIZE:
                raise ValueError("bad data line at %08X" % addr)
            image[addr:addr + length] = data
            covered[addr:addr + length] = b"\x01" * length
    return model, headers, bytes(image), covered


def escape(raw):
    out = bytearray()
    for b in raw:
        if b > 0xF9:
            out += bytes([0xFF, b & 0x0F])
        else:
            out.append(b)
    return bytes(out)


def build_block(addr, data):
    raw = addr.to_bytes(4, "big") + bytes([len(data)]) + \
        bytes(b ^ 0x80 for b in data)
    raw += bytes([(-sum(raw)) & 0xFF])
    return dump.build_frame(dump.CMD_DATA, escape(raw))


def used_channels(image):
    bm = image[CHAN_BITMAP:CHAN_BITMAP + 250]
    return sum(1 for n in range(2000) if not (bm[n // 8] >> (n % 8)) & 1)


def model_query(ser):
    frames = dump.transact(ser, dump.build_frame(dump.CMD_ID, b"\x00" * 4), 2.0)
    for src, dst, cmd, payload in frames:
        if src == dump.ADDR_RADIO and cmd == dump.CMD_MODEL:
            return payload
    return None


def read_radio(port, baud, timeout):
    with serial.Serial(port, baud, timeout=0.2) as ser:
        model, image, received, complete = dump.dump(ser, timeout, None)
    if not complete or received != dump.MEMSIZE:
        sys.exit("Reading the radio failed, nothing was written.")
    return model, bytes(image)


def write_radio(ser, image, endframe, delay):
    reader = dump.FrameReader()

    def check_incoming():
        chunk = ser.read(ser.in_waiting or 0) if ser.in_waiting else b""
        for src, dst, cmd, payload in reader.feed(chunk):
            if src == dump.ADDR_RADIO:
                return cmd, payload
        return None

    ser.reset_input_buffer()
    ser.write(dump.build_frame(CMD_CLONE_IN, dump.MODEL))
    ser.flush()
    time.sleep(0.2)

    last_print = 0.0
    for addr in range(0, dump.MEMSIZE, BLOCK):
        ser.write(build_block(addr, image[addr:addr + BLOCK]))
        ser.flush()
        if delay:
            time.sleep(delay)
        early = check_incoming()
        if early:
            print("\nRadio interrupted the clone at %06X: cmd %02X payload %s"
                  % (addr, early[0], early[1].hex(" ")))
            return False
        now = time.time()
        sent = addr + BLOCK
        if now - last_print > 0.5 or sent >= dump.MEMSIZE:
            last_print = now
            print("\r  %6d / %d bytes (%3d%%)" %
                  (min(sent, dump.MEMSIZE), dump.MEMSIZE,
                   min(sent, dump.MEMSIZE) * 100 // dump.MEMSIZE),
                  end="", flush=True)
    print()

    ser.write(dump.build_frame(dump.CMD_END, escape(endframe)))
    ser.flush()

    deadline = time.time() + 15
    while time.time() < deadline:
        chunk = ser.read(256)
        for src, dst, cmd, payload in reader.feed(chunk):
            if src != dump.ADDR_RADIO:
                continue
            if cmd == CMD_CLONE_OK:
                ok = payload[:1] == b"\x00"
                print("Radio clone result: %s (%s)" %
                      ("OK" if ok else "FAILED", payload.hex(" ")))
                return ok
            print("Unexpected answer: cmd %02X payload %s" %
                  (cmd, payload.hex(" ")))
            return False
    print("No clone result from the radio within 15 s.")
    return False


def wait_for_radio(port, baud, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with serial.Serial(port, baud, timeout=0.2) as ser:
                if model_query(ser):
                    return True
        except serial.SerialException:
            pass
        time.sleep(1)
    return False


def main():
    ap = argparse.ArgumentParser(description="Write an .icf image to an IC-R8600")
    ap.add_argument("icf")
    ap.add_argument("-p", "--port", default="/dev/ttyUSB0")
    ap.add_argument("-b", "--baud", type=int, default=9600,
                    help="speed (default 9600; 115200 is faster once a "
                         "restore has worked at 9600)")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="extra seconds to wait after each block (default 0)")
    ap.add_argument("--endframe", default=DEFAULT_ENDFRAME.decode(),
                    help="end-of-clone payload (default 'Icom Inc.A8')")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip reading a backup of the radio first")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip reading the radio back after writing")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="do not ask for confirmation")
    args = ap.parse_args()

    if serial is None:
        sys.exit("pyserial missing: pip install pyserial")

    # 1. check the file
    model_hex, headers, image, covered = load_icf(args.icf)
    if model_hex != dump.MODEL.hex():
        sys.exit("%s is not an IC-R8600 file (model %s)." % (args.icf, model_hex))
    missing = covered.count(0)
    if missing:
        sys.exit("%s covers only %d of %d bytes; refusing to write an "
                 "incomplete image." % (args.icf, dump.MEMSIZE - missing,
                                        dump.MEMSIZE))
    file_rev = headers.get("MapRev")
    print("File: %s, MapRev %s, %d channels in use" %
          (args.icf, file_rev, used_channels(image)))

    # 2. check the radio
    with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
        answer = model_query(ser)
    if not answer:
        sys.exit("No answer from the radio on %s." % args.port)
    if answer[:4] != dump.MODEL:
        sys.exit("Radio is not an IC-R8600 (model %s)." % answer[:4].hex())
    radio_rev = answer[5] if len(answer) > 5 else None
    print("Radio: IC-R8600, memory map revision %s" % radio_rev)
    if file_rev is None or radio_rev is None or str(radio_rev) != file_rev:
        sys.exit("Memory map revision of the file (%s) does not match the "
                 "radio (%s); refusing to write." % (file_rev, radio_rev))

    # 3. backup
    if not args.no_backup:
        backup = time.strftime("ic-r8600-before-restore-%Y%m%d-%H%M%S.icf")
        print("Reading a backup of the radio first...")
        model, current = read_radio(args.port, args.baud, 10.0)
        dump.write_icf(backup, current, model.hex())
        print("Backup saved to %s" % backup)
        if current == image:
            print("The radio already contains exactly this image; nothing to do.")
            return

    # 4. confirm
    if not args.yes:
        print()
        print("About to OVERWRITE the whole IC-R8600 memory with %s." % args.icf)
        print("Do not disconnect or switch off the radio while writing.")
        if input("Type 'yes' to continue: ").strip().lower() != "yes":
            sys.exit("Cancelled, nothing was written.")

    # 5. write
    print("Writing...")
    started = time.time()
    with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
        ok = write_radio(ser, image, args.endframe.encode("ascii"), args.delay)
    print("Write took %.1f s." % (time.time() - started))
    if not ok:
        sys.exit("Restore FAILED. The radio memory may be incomplete; run the "
                 "restore again (the backup file is your safety copy).")

    # 6. verify
    if args.no_verify:
        print("Done (not verified).")
        return
    print("Waiting for the radio...")
    time.sleep(2)
    if not wait_for_radio(args.port, args.baud, 30):
        sys.exit("Radio did not answer after the restore; check it and run "
                 "r8600_dump.py to verify manually.")
    print("Reading the radio back to verify...")
    _, readback = read_radio(args.port, args.baud, 10.0)
    diffs = [a for a in range(dump.MEMSIZE) if readback[a] != image[a]]
    if not diffs:
        print("Verified: radio memory matches %s exactly." % args.icf)
    else:
        print("WARNING: %d bytes differ after restore, first at %06X."
              % (len(diffs), diffs[0]))
        dump.write_icf("ic-r8600-after-restore.icf", readback, dump.MODEL.hex())
        print("Saved what the radio now contains to ic-r8600-after-restore.icf")
        sys.exit(1)


if __name__ == "__main__":
    main()
