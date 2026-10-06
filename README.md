# r8600_icf2chirp

Convert **Icom IC-R8600** memory channels from a CS-R8600 `.icf` save file into a
**CHIRP** generic CSV file — no more typing the same channels in twice.

CHIRP has no IC-R8600 driver, and CS-R8600 cannot export to CHIRP. This script
reads the `.icf` file directly, decodes the memory channels and their groups,
and writes a CSV that CHIRP can open and import into other radios.

## Features

- Reads `.icf` files saved by Icom CS-R8600 (model ID `38180001`)
- Exports all 2000 regular memory channels, group by group, in the same order as
  the receiver
- Decodes frequency, name, mode (all 18 R8600 modes), duplex and offset, tuning
  step and CTCSS tone squelch
- Puts the group number, group name and position in CHIRP's Comment column, e.g.
  `G02 Repeaters #05`
- Flags anything CHIRP can't represent exactly (S-AM, dPMR, DCR, unusual tuning
  steps) in the Comment column, so no information is silently lost
- Single file, Python 3 standard library only, no dependencies

## Requirements

Python 3.6 or newer.

## Usage

```
python3 r8600_icf2chirp.py memories.icf
```

This writes `memories.csv` next to the input file. Open it in CHIRP with
**File → Open**, then copy and paste the channels into your radio's memory tab.

### Options

| Option | Description |
|---|---|
| `-o FILE`, `--output FILE` | Output CSV file (default: input name with `.csv`) |
| `--start N` | First CHIRP location number (default `0`) |
| `--group N` | Export only group `N`; repeat to export several groups |
| `--debug` | Print the raw 47-byte record of every channel to stderr |

Examples:

```
python3 r8600_icf2chirp.py memories.icf -o scanner.csv --start 1
python3 r8600_icf2chirp.py memories.icf --group 1 --group 5
```

## Mode mapping

CHIRP has fewer modes than the IC-R8600. Where a mode has no exact match, the
original R8600 mode is added to the Comment column in brackets.

| R8600 | CHIRP | | R8600 | CHIRP |
|---|---|---|---|---|
| LSB | LSB | | S-AM(L) | AM |
| USB | USB | | FM | FM |
| CW | CW | | WFM | WFM |
| CW-R | CWR | | D-STAR | DV |
| FSK | FSK | | P25 | P25 |
| FSK-R | FSKR | | dPMR | DIG |
| AM | AM | | NXDN-VN | NXDN |
| S-AM(D) | AM | | NXDN-N | NXDN |
| S-AM(U) | AM | | DCR | DIG |

## Known limitations

- **DTCS** is not decoded yet. Channels using DTCS (or any squelch type other than
  CTCSS) get a marker like `[SQL?2/…]` in the Comment column instead.
- **Skip** flags are not exported yet.
- **Digital mode settings** (D-STAR, P25 NAC, NXDN RAN, dPMR/DCR codes) are not
  exported, because CHIRP's CSV format has no columns for them.
- **Tuning steps** that CHIRP doesn't support (10 Hz, 100 Hz, 3.125 kHz,
  8.33 kHz, programmable) are exported as 5 kHz, with the real step noted in the
  Comment column.
- Scan edges and auto-memory-write channels are not exported.
- Only tested with files from CS-R8600. Files from other Icom software will not
  work.

## ICF file format

Notes on the reverse-engineered memory layout, for anyone who wants to extend
the script or write a proper CHIRP driver.

The `.icf` file is Icom's usual text format. The first line is the model ID
(`38180001`), followed by `#` header lines. Each data line is an 8-digit hex
address, a 2-digit hex byte count (`20` = 32 bytes), and the data bytes in hex.
Assembling all lines gives a binary memory image.

### Memory image

| Offset | Size | Contents |
|---|---|---|
| `0x00000` | 2400 × 47 bytes | Channel slots: 0–1999 memory channels, 2300–2399 scan edges |
| `0x1B8C0` | 100 × 19 bytes | Group table |
| `0x1C02C` | 2000 × uint16 | "Next channel" table |
| `0x1CFCC` | 250 bytes | 2000-bit bitmap (purpose not confirmed, possibly skip) |

### Groups

Channels are not stored in group order. Each group table entry is
`[group number][first channel, uint16 LE][name, 16 chars]`, and the "next
channel" table links each channel to the next one in its group. `0xFFFF` ends the
chain. Unused channels form their own free chain and are skipped by the script.

### Channel record (47 bytes)

| Offset | Type | Field |
|---|---|---|
| `0x00` | uint32 LE | Frequency in Hz |
| `0x04` | uint8 | Bits 7–3: mode (0–17, see table above). Bits 2–0: filter (unconfirmed) |
| `0x05` | uint16 LE | Flags. Bits 5–6: duplex (0 = simplex, 1 = −, 2 = +). Other bits unknown |
| `0x07` | uint8 | Tuning step index: 10 Hz, 100 Hz, 1k, 3.125k, 5k, 6.25k, 8.33k, 9k, 10k, 12.5k, 20k, 25k, 100k |
| `0x08` | uint16 LE | Unknown (always `500` so far, possibly the programmable step) |
| `0x0A` | uint32 LE | Duplex offset in Hz |
| `0x0F` | char[16] | Name, padded with spaces |
| `0x1F` | uint8 | Analog modes: squelch type (0 = off, 1 = CTCSS tone squelch) |
| `0x21` | uint8 | Analog modes: CTCSS tone index (0 = 67.0 Hz, standard 50-tone list) |

For digital modes, bytes `0x1F`–`0x2E` hold mode-specific settings instead of
tone data.

## Contributing

Bug reports and pull requests are welcome. The easiest way to help decode the
remaining fields is to save a small `.icf` file in CS-R8600 with a few channels
whose names describe their settings (for example `DTCS 023 N` or `SKIP`), then
run the script with `--debug` and open an issue with the output and the file.

## Credits

- Written by Kari, OH1KK
- Format reverse-engineering and code developed together with
  [Claude](https://claude.ai) by Anthropic

## License

MIT — see [LICENSE](LICENSE).

IC-R8600 and CS-R8600 are trademarks of Icom Inc. This project is not affiliated
with or endorsed by Icom.
