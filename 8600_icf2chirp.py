#!/usr/bin/env python3
"""
r8600_icf2chirp.py - Convert Icom CS-R8600 .icf memory files to CHIRP generic CSV.

Usage:
    python3 r8600_icf2chirp.py input.icf [-o output.csv] [--start 0]
                               [--group N ...] [--debug]

Memory map (reverse-engineered from a CS-R8600 file, model id 38180001):

  0x00000  2400 slots x 47 bytes
           slots    0..1999  regular memory channels
           slots 2000..2299  (auto-memory / other, not exported)
           slots 2300..2399  scan edges (not exported)
  0x1B8C0  group table, 19 bytes/entry: [group no][first ch uint16 LE][name 16]
  0x1C02C  "next channel" table, 2000 x uint16 LE (0xFFFF = end of chain)

  Channel record (47 bytes):
    0x00  uint32 LE  frequency in Hz                    (confirmed)
    0x04  uint8      bits 7-3 mode, bits 2-0 filter(?)  (confirmed, all 18 modes)
    0x05  uint16 LE  flags; bits 5-6 duplex 0/1-/2+     (confirmed)
    0x07  uint8      tuning step index                  (confirmed 6.25/12.5/25k)
    0x0A  uint32 LE  duplex offset in Hz                (confirmed)
    0x0F  char[16]   name                               (confirmed)
    0x1F  uint8      analog squelch type, 1 = CTCSS TSQL(confirmed; DTCS unknown)
    0x21  uint8      CTCSS tone index (0 = 67.0 Hz)     (confirmed)
    0x1F..0x2E is reused for digital-mode settings (NAC, RAN, UC ...).

Channels are stored as linked lists per group, so the converter walks each
group's chain in order. Unknown bits are printed with --debug.
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

# bits 5-6 of flag word at 0x05
DUPLEX_MAP = {0: "", 1: "-", 2: "+"}

# byte 0x07: R8600 tuning step list in kHz (indexes 5, 9, 11 verified)
TSTEP_MAP = {0: 0.01, 1: 0.1, 2: 1.0, 3: 3.125, 4: 5.0, 5: 6.25, 6: 8.33,
             7: 9.0, 8: 10.0, 9: 12.5, 10: 20.0, 11: 25.0, 12: 100.0}
CHIRP_STEPS = (5.0, 6.25, 10.0, 12.5, 15.0, 20.0, 25.0, 30.0, 50.0, 100.0,
               125.0, 200.0, 9.0, 1.0, 2.5)

TONES = (67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
         94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
         131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
         171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
         203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1)

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
    groups = []
    for i in range(NUM_GROUPS):
        o = GROUP_TABLE + i * GROUP_ENTRY
        entry = img[o:o + GROUP_ENTRY]
        if len(entry) < GROUP_ENTRY:
            break
        num = entry[0]
        head = struct.unpack_from("<H", entry, 1)[0]
        name = clean_name(entry[3:19])
        if num == 0xFF or head == END:
            continue
        groups.append((num, head, name))
    return groups


def walk_chain(img, head):
    seen = set()
    ch = head
    while ch != END and ch < NUM_MEM and ch not in seen:
        seen.add(ch)
        yield ch
        ch = struct.unpack_from("<H", img, NEXT_TABLE + ch * 2)[0]


def decode_channel(img, slot):
    r = img[slot * REC_SIZE:(slot + 1) * REC_SIZE]
    freq = struct.unpack_from("<I", r, 0)[0]
    modebyte = r[4]
    flags = struct.unpack_from("<H", r, 5)[0]
    offset = struct.unpack_from("<I", r, 10)[0]
    name = clean_name(r[15:31])
    return {
        "raw": r,
        "freq": freq,
        "mode_code": modebyte >> 3,
        "filter": modebyte & 0x07,
        "tstep_code": r[7],
        "sql_type": r[31],
        "tone_idx": r[33],
        "flags": flags,
        "duplex_code": (flags >> 5) & 0x03,
        "offset": offset,
        "name": name,
    }


def fmt_mhz(hz):
    return "%.6f" % (hz / 1e6)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("icf")
    ap.add_argument("-o", "--output", help="CSV file (default: <input>.csv)")
    ap.add_argument("--start", type=int, default=0,
                    help="first CHIRP location number (default 0)")
    ap.add_argument("--group", type=int, action="append",
                    help="export only this group number (repeatable)")
    ap.add_argument("--debug", action="store_true",
                    help="print raw record bytes for each channel")
    args = ap.parse_args()

    model, img = load_icf(args.icf)
    if model != EXPECTED_MODEL:
        print("Warning: model id %s, expected %s (IC-R8600)" %
              (model, EXPECTED_MODEL), file=sys.stderr)

    out_path = args.output or args.icf.rsplit(".", 1)[0] + ".csv"
    rows = []
    loc = args.start
    unknown_modes = set()
    unknown_sql = set()

    for gnum, head, gname in read_groups(img):
        if args.group and gnum not in args.group:
            continue
        for pos, slot in enumerate(walk_chain(img, head)):
            ch = decode_channel(img, slot)
            notes = []
            mc = ch["mode_code"]
            if mc in MODE_MAP:
                rname, mode = MODE_MAP[mc]
                if rname.replace("-", "") != mode:
                    notes.append(rname)
            else:
                unknown_modes.add(mc)
                mode = "Auto"
                notes.append("mode?%d" % mc)

            duplex = DUPLEX_MAP.get(ch["duplex_code"], "")
            offset = ch["offset"] if duplex else 0

            step = TSTEP_MAP.get(ch["tstep_code"])
            if step not in CHIRP_STEPS:
                notes.append("TS %s" % (("%gk" % step) if step is not None
                                        else "?%d" % ch["tstep_code"]))
                step = 5.0

            tmode, tone = "", 88.5
            if mc in ANALOG_MODES and ch["sql_type"]:
                if ch["sql_type"] == 1 and ch["tone_idx"] < len(TONES):
                    tmode, tone = "TSQL", TONES[ch["tone_idx"]]
                else:
                    unknown_sql.add(ch["sql_type"])
                    notes.append("SQL?%d/%d" % (ch["sql_type"], ch["tone_idx"]))

            comment = "G%02d %s #%02d" % (gnum, gname, pos) if gname \
                else "G%02d #%02d" % (gnum, pos)
            if notes:
                comment += " [" + ", ".join(notes) + "]"

            rows.append([loc, ch["name"], fmt_mhz(ch["freq"]), duplex,
                         fmt_mhz(offset), tmode, "%.1f" % tone, "%.1f" % tone,
                         "023", "NN", "023", "Tone->Tone", mode,
                         "%.2f" % step, "", "50W", comment])
            if args.debug:
                print("loc %4d slot %4d G%02d %-16s %s" %
                      (loc, slot, gnum, ch["name"], ch["raw"].hex(" ")),
                      file=sys.stderr)
            loc += 1

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        w.writerows(rows)

    print("Wrote %d channels to %s" % (len(rows), out_path))
    if unknown_modes:
        print("Note: unknown mode codes %s exported as 'Auto' - send a sample "
              "file so they can be mapped." % sorted(unknown_modes),
              file=sys.stderr)
    if unknown_sql:
        print("Note: unknown squelch types %s (DTCS?) noted in Comment column."
              % sorted(unknown_sql), file=sys.stderr)


if __name__ == "__main__":
    main()

