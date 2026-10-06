#!/usr/bin/env python3
"""
r8600_icf2chirp.py - Convert Icom CS-R8600 .icf memory files to CHIRP generic CSV.

Usage:
    python3 r8600_icf2chirp.py input.icf [-o output.csv] [--renumber [--start N]]
                               [--group N ...] [--debug]

By default each channel keeps its IC-R8600 channel number (0-1999) as its CHIRP
location. Use --renumber to number channels sequentially instead.

Memory map (reverse-engineered from CS-R8600 files, model id 38180001):

  0x00000  2400 slots x 47 bytes
           slots    0..1999  memory channel records (storage slots)
           slots 2000..2299  auto memory write (not exported)
           slots 2300..2399  scan edges (not exported)
  0x1B8C0  group table, 19 bytes/entry: [group no][first slot uint16 LE][name 16]
           (stored group no is 1-based; CS-R8600 shows it 0-based)
  0x1C02C  "next slot" table, 2000 x uint16 LE (0xFFFF = end of chain)
  0x1CFCC  channel number bitmap, 2000 bits, 0 = channel number in use
  0x1D4E1  free chain head slot (uint16 LE), 0x1D4E3 free chain tail slot
  0x1D4E5  per-slot flags, 2000 bytes:
             bit 0  slot empty
             bit 1  Skip
             bit 2  P-Skip
             bits 4-7  scan SEL (0 = off, 1-9)

  Channel record (47 bytes):
    0x00  uint32 LE  frequency in Hz
    0x04  uint8      bits 7-3 mode, bits 2-0 filter (0 = FIL1)
    0x05  uint16 LE  flags:
                       bits 5-6   duplex (0 off, 1 -, 2 +)
                       bit 10     P.AMP
                       bits 11-12 ATT (0 off, 1 10dB, 2 20dB, 3 30dB)
                       bits 13-14 antenna (0 ANT1, 1 ANT2, 2 ANT3)
                       bit 15     IP+
    0x07  uint8      tuning step index (13 = programmable)
    0x08  uint16 LE  programmable tuning step, 0.1 kHz units
    0x0A  uint32 LE  duplex offset in Hz
    0x0F  char[16]   name
    0x1F  uint8      analog: tone mode (0 off, 1 TSQL, 2 DTCS)
    0x20  uint8      analog: DTCS polarity (0 normal, 1 reverse)
    0x21  uint8      analog: CTCSS tone index (0 = 67.0 Hz)
    0x22  uint8      analog: DTCS code index (0 = 023)
    0x1F..0x2E is reused for digital-mode settings (not decoded).

Storage slots and channel numbers are separate: the channels of all groups,
taken in group order and chain order, map one to one onto the in-use bits of
the channel number bitmap in ascending order.
"""

import argparse
import csv
import struct
import sys

REC_SIZE = 47
NUM_MEM = 2000
GROUP_TABLE = 0x1B8C0
GROUP_ENTRY = 19
NUM_GROUPS = 100
NEXT_TABLE = 0x1C02C
CHAN_BITMAP = 0x1CFCC
SLOT_FLAGS = 0x1D4E5
END = 0xFFFF
EXPECTED_MODEL = "38180001"

# byte 0x04 >> 3  ->  (R8600 name, CHIRP mode)
MODE_MAP = {
    0: ("LSB", "LSB"),
    1: ("USB", "USB"),
    2: ("CW", "CW"),
    3: ("CW-R", "CWR"),
    4: ("FSK", "FSK"),
    5: ("FSK-R", "FSKR"),
    6: ("AM", "AM"),
    7: ("S-AM(D)", "AM"),
    8: ("S-AM(U)", "AM"),
    9: ("S-AM(L)", "AM"),
    10: ("FM", "FM"),
    11: ("WFM", "WFM"),
    12: ("D-STAR", "DV"),
    13: ("P25", "P25"),
    14: ("dPMR", "DIG"),
    15: ("NXDN-VN", "NXDN"),
    16: ("NXDN-N", "NXDN"),
    17: ("DCR", "DIG"),
}
ANALOG_MODES = set(range(0, 12))

DUPLEX_MAP = {0: "", 1: "-", 2: "+"}

# byte 0x07 -> kHz; 13 = programmable (value at 0x08)
TSTEP_MAP = {0: 0.1, 1: 1.0, 2: 2.5, 3: 3.125, 4: 5.0, 5: 6.25, 6: 8.33,
             7: 9.0, 8: 10.0, 9: 12.5, 10: 20.0, 11: 25.0, 12: 100.0}
TSTEP_PROG = 13
CHIRP_STEPS = (5.0, 6.25, 10.0, 12.5, 15.0, 20.0, 25.0, 30.0, 50.0, 100.0,
               125.0, 200.0, 9.0, 1.0, 2.5)

TONES = (67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
         94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
         131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
         171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
         203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1)

DTCS_CODES = (
    23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74,
    114, 115, 116, 122, 125, 131, 132, 134, 143, 145, 152, 155, 156, 162,
    165, 172, 174, 205, 212, 223, 225, 226, 243, 244, 245, 246, 251, 252,
    255, 261, 263, 265, 266, 271, 274, 306, 311, 315, 325, 331, 332, 343,
    346, 351, 356, 364, 365, 371, 411, 412, 413, 423, 431, 432, 445, 446,
    452, 454, 455, 462, 464, 465, 466, 503, 506, 516, 523, 526, 532, 546,
    565, 606, 612, 624, 627, 631, 632, 654, 662, 664, 703, 712, 723, 731,
    732, 734, 743, 754)

ATT_MAP = {1: "ATT 10dB", 2: "ATT 20dB", 3: "ATT 30dB"}

CSV_HEADER = ["Location", "Name", "Frequency", "Duplex", "Offset", "Tone",
              "rToneFreq", "cToneFreq", "DtcsCode", "DtcsPolarity",
              "RxDtcsCode", "CrossMode", "Mode", "TStep", "Skip", "Power",
              "Comment"]


def load_icf(path):
    """Parse ICF text into (model, bytearray image)."""
    image = bytearray()
    model = None
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if model is None and len(line) == 8:
                model = line
                continue
            addr = int(line[0:8], 16)
            length = int(line[8:10], 16)
            data = bytes.fromhex(line[10:10 + 2 * length])
            end = addr + len(data)
            if len(image) < end:
                image.extend(b"\xff" * (end - len(image)))
            image[addr:end] = data
    return model, image


def clean_name(raw):
    return raw.decode("ascii", errors="replace").rstrip(" \x00\xff")


def read_groups(img):
    """Return [(display group number, first slot, name)] in table order."""
    groups = []
    for i in range(NUM_GROUPS):
        o = GROUP_TABLE + i * GROUP_ENTRY
        entry = img[o:o + GROUP_ENTRY]
        if len(entry) < GROUP_ENTRY:
            break
        num = entry[0]
        head = struct.unpack_from("<H", entry, 1)[0]
        if num == 0xFF or head == END:
            continue
        groups.append((num - 1, head, clean_name(entry[3:19])))
    return groups


def walk_chain(img, head):
    seen = set()
    slot = head
    while slot != END and slot < NUM_MEM and slot not in seen:
        seen.add(slot)
        yield slot
        slot = struct.unpack_from("<H", img, NEXT_TABLE + slot * 2)[0]


def used_channel_numbers(img):
    bm = img[CHAN_BITMAP:CHAN_BITMAP + NUM_MEM // 8]
    return [n for n in range(NUM_MEM) if not (bm[n // 8] >> (n % 8)) & 1]


def decode_channel(img, slot):
    r = img[slot * REC_SIZE:(slot + 1) * REC_SIZE]
    flags = struct.unpack_from("<H", r, 5)[0]
    sflags = img[SLOT_FLAGS + slot]
    return {
        "raw": r,
        "freq": struct.unpack_from("<I", r, 0)[0],
        "mode_code": r[4] >> 3,
        "filter": (r[4] & 0x07) + 1,
        "duplex_code": (flags >> 5) & 0x03,
        "pamp": bool(flags & 0x0400),
        "att": (flags >> 11) & 0x03,
        "ant": ((flags >> 13) & 0x03) + 1,
        "ipplus": bool(flags & 0x8000),
        "tstep_code": r[7],
        "prog_step": struct.unpack_from("<H", r, 8)[0] / 10.0,
        "offset": struct.unpack_from("<I", r, 10)[0],
        "name": clean_name(r[15:31]),
        "tone_mode": r[31],
        "dtcs_rev": bool(r[32]),
        "tone_idx": r[33],
        "dtcs_idx": r[34],
        "skip": "P" if sflags & 0x04 else ("S" if sflags & 0x02 else ""),
        "sel": sflags >> 4,
    }


def fmt_mhz(hz):
    return "%.6f" % (hz / 1e6)


def build_row(loc, ch, gnum, gname, chnum, unknown):
    notes = []
    mc = ch["mode_code"]
    if mc in MODE_MAP:
        rname, mode = MODE_MAP[mc]
        if rname.replace("-", "") != mode:
            notes.append(rname)
    else:
        unknown.add("mode %d" % mc)
        mode = "Auto"
        notes.append("mode?%d" % mc)

    duplex = DUPLEX_MAP.get(ch["duplex_code"], "")
    offset = ch["offset"] if duplex else 0

    if ch["tstep_code"] == TSTEP_PROG:
        step = ch["prog_step"]
    else:
        step = TSTEP_MAP.get(ch["tstep_code"])
    if step not in CHIRP_STEPS:
        notes.append("TS %s" % (("%gk" % step) if step is not None
                                else "?%d" % ch["tstep_code"]))
        step = 5.0

    tmode, tone, dtcs, pol = "", 88.5, 23, "NN"
    if mc in ANALOG_MODES and ch["tone_mode"]:
        if ch["tone_mode"] == 1 and ch["tone_idx"] < len(TONES):
            tmode, tone = "TSQL", TONES[ch["tone_idx"]]
        elif ch["tone_mode"] == 2 and ch["dtcs_idx"] < len(DTCS_CODES):
            tmode, dtcs = "DTCS", DTCS_CODES[ch["dtcs_idx"]]
            pol = "RR" if ch["dtcs_rev"] else "NN"
        else:
            unknown.add("tone mode %d" % ch["tone_mode"])
            notes.append("tone?%d" % ch["tone_mode"])

    if ch["ant"] != 1:
        notes.append("ANT%d" % ch["ant"])
    if ch["pamp"]:
        notes.append("P.AMP")
    if ch["att"]:
        notes.append(ATT_MAP[ch["att"]])
    if ch["ipplus"]:
        notes.append("IP+")
    if ch["sel"]:
        notes.append("SEL %d" % ch["sel"])

    comment = "CH%04d G%02d %s" % (chnum, gnum, gname) if gname \
        else "CH%04d G%02d" % (chnum, gnum)
    if notes:
        comment += " [" + ", ".join(notes) + "]"

    return [loc, ch["name"], fmt_mhz(ch["freq"]), duplex, fmt_mhz(offset),
            tmode, "%.1f" % tone, "%.1f" % tone, "%03d" % dtcs, pol,
            "%03d" % dtcs, "Tone->Tone", mode, "%.2f" % step, ch["skip"],
            "50W", comment]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("icf")
    ap.add_argument("-o", "--output", help="CSV file (default: <input>.csv)")
    ap.add_argument("--renumber", action="store_true",
                    help="number CHIRP locations sequentially instead of "
                         "using the IC-R8600 channel numbers")
    ap.add_argument("--start", type=int, default=0,
                    help="first location with --renumber (default 0)")
    ap.add_argument("--group", type=int, action="append",
                    help="export only this group, numbered as in CS-R8600 "
                         "(repeatable)")
    ap.add_argument("--debug", action="store_true",
                    help="print raw record bytes for each channel")
    args = ap.parse_args()

    model, img = load_icf(args.icf)
    if model != EXPECTED_MODEL:
        print("Warning: model id %s, expected %s (IC-R8600)" %
              (model, EXPECTED_MODEL), file=sys.stderr)

    # Pair every channel (group order, chain order) with its channel number
    channels = []
    for gnum, head, gname in read_groups(img):
        for slot in walk_chain(img, head):
            channels.append((gnum, gname, slot))
    numbers = used_channel_numbers(img)
    if len(numbers) != len(channels):
        print("Warning: %d channels in groups but %d channel numbers in use; "
              "channel numbers may be wrong." % (len(channels), len(numbers)),
              file=sys.stderr)
        numbers = list(range(len(channels)))

    out_path = args.output or args.icf.rsplit(".", 1)[0] + ".csv"
    rows = []
    unknown = set()
    loc = args.start
    for (gnum, gname, slot), chnum in zip(channels, numbers):
        if args.group and gnum not in args.group:
            continue
        ch = decode_channel(img, slot)
        rows.append(build_row(loc if args.renumber else chnum, ch, gnum,
                              gname, chnum, unknown))
        if args.debug:
            print("CH%04d slot %4d G%02d %-16s %s %02x" %
                  (chnum, slot, gnum, ch["name"], ch["raw"].hex(" "),
                   img[SLOT_FLAGS + slot]), file=sys.stderr)
        loc += 1

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        w.writerows(rows)

    print("Wrote %d channels to %s" % (len(rows), out_path))
    if unknown:
        print("Note: unknown values (%s) are marked in the Comment column."
              % ", ".join(sorted(unknown)), file=sys.stderr)


if __name__ == "__main__":
    main()
