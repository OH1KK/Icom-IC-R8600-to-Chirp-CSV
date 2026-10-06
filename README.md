# IC-R8600 memory tools: radio → .icf → CHIRP

Read the memory of an **Icom IC-R8600** receiver directly over USB on Linux,
and convert its memory channels into a **CHIRP** generic CSV file. No CS-R8600,
no Windows, and no typing the same channels in twice.

CHIRP has no IC-R8600 driver, and Icom's CS-R8600 programming software is
Windows-only and cannot export to CHIRP. This project contains three small
Python scripts:

| Script | What it does |
|---|---|
| `r8600_probe.py` | Checks that the radio answers on its USB serial port |
| `r8600_dump.py` | Reads the whole radio memory and saves it as an `.icf` file |
| `r8600_icf2chirp.py` | Converts an `.icf` file into a CHIRP CSV |

The `.icf` files written by `r8600_dump.py` open in CS-R8600 too, so the dump
script also works as a Linux backup tool. Reading is always read-only: nothing
is ever written to the radio.

## Features

- Reads the radio directly over USB with the Icom clone protocol (about 13
  seconds for the full memory), or `.icf` files saved by CS-R8600
- Exports all memory channels with their real IC-R8600 channel numbers (0–1999)
  as CHIRP locations, including gaps between channels
- Decodes frequency, name, mode (all 18 R8600 modes), duplex and offset, tuning
  step (including programmable steps), CTCSS and DTCS tone squelch, and
  Skip / P-Skip
- Puts the channel number, group number and group name in CHIRP's Comment column,
  e.g. `CH0021 G00 PMR-446`, plus R8600-only settings such as antenna, P.AMP,
  ATT, IP+ and scan SEL
- Flags anything CHIRP can't represent exactly (S-AM, dPMR, DCR, unusual tuning
  steps) in the Comment column, so no information is silently lost
- The converter uses only the Python standard library; the radio scripts need
  pyserial

## Requirements

- Python 3.6 or newer
- For reading the radio: pyserial (`sudo apt install python3-serial` or
  `pip install pyserial`) and an IC-R8600 connected with a USB cable

## Quick start

```
python3 r8600_dump.py                        # writes ic-r8600-YYYYMMDD-HHMMSS.icf
python3 r8600_icf2chirp.py ic-r8600-*.icf    # writes ic-r8600-....csv
```

Open the CSV in CHIRP with **File → Open**, then copy and paste the channels into
your radio's memory tab.

## Reading the radio

The IC-R8600 shows up on Linux as two CP2102 USB serial ports, usually
`/dev/ttyUSB0` and `/dev/ttyUSB1`. The clone protocol answers on the first one
(serial number ending in `A`). Your user needs access to serial ports:

```
sudo usermod -aG dialout $USER     # then log out and back in
```

Close any other program that may hold the port open (rigctld, flrig, scanner
software) before reading.

### r8600_probe.py

Sends only the model query to both ports at several speeds and prints what the
radio answers. Use it to find the right port, or when something doesn't work.

```
python3 r8600_probe.py
python3 r8600_probe.py -p /dev/ttyUSB0 -b 9600
```

### r8600_dump.py

Reads the full memory (channels, groups, scan edges and settings) and writes an
`.icf` file that both the converter and CS-R8600 can open.

| Option | Description |
|---|---|
| `-p PORT` | Serial port (default `/dev/ttyUSB0`) |
| `-b BAUD` | Speed (default `115200`; the radio answered at 9600–115200) |
| `-o FILE` | Output file (default `ic-r8600-YYYYMMDD-HHMMSS.icf`) |
| `-t SECONDS` | Give up after this long without data (default `10`) |
| `--log FILE` | Save every raw byte from the radio, for troubleshooting |

## Converting to CHIRP CSV

```
python3 r8600_icf2chirp.py memories.icf
```

This writes `memories.csv` next to the input file.

### Options

| Option | Description |
|---|---|
| `-o FILE`, `--output FILE` | Output CSV file (default: input name with `.csv`) |
| `--renumber` | Number CHIRP locations sequentially instead of using the R8600 channel numbers |
| `--start N` | First location number with `--renumber` (default `0`) |
| `--group N` | Export only group `N`, numbered as in CS-R8600 (`00`–`99`); repeat to export several groups |
| `--debug` | Print the raw 47-byte record of every channel to stderr |

Examples:

```
python3 r8600_icf2chirp.py memories.icf -o scanner.csv --renumber --start 1
python3 r8600_icf2chirp.py memories.icf --group 0 --group 5
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

- **R8600-only settings** (antenna, P.AMP, ATT, IP+, scan SEL 1–9, filter) have
  no CHIRP columns. All except the filter are noted in the Comment column.
- **Digital mode settings** (D-STAR, P25 NAC, NXDN RAN, dPMR/DCR codes) are not
  exported, because CHIRP's CSV format has no columns for them.
- **Tuning steps** that CHIRP doesn't support (100 Hz, 3.125 kHz, 8.33 kHz, and
  programmable values like 12.3 kHz) are exported as 5 kHz, with the real step
  noted in the Comment column.
- Scan edges and auto-memory-write channels are not exported.
- Writing to the radio is not supported, on purpose. Use CS-R8600 to program the
  radio.
- Tested with one IC-R8600 (Europe) and CS-R8600 files. Other firmware versions
  or regional models may differ.

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
| `0x00000` | 2400 × 47 bytes | Record slots: 0–1999 memory channels, 2000–2299 auto memory write, 2300–2399 scan edges |
| `0x1B8C0` | 100 × 19 bytes | Group table |
| `0x1C02C` | 2000 × uint16 LE | "Next slot" table |
| `0x1CFCC` | 250 bytes | Channel number bitmap, 2000 bits, 0 = number in use |
| `0x1D4E1` | 2 × uint16 LE | Free chain head and tail slot |
| `0x1D4E5` | 2000 bytes | Per-slot flags |

### Groups, slots and channel numbers

A channel's storage slot is not its channel number. Each group table entry is
`[group number][first slot, uint16 LE][name, 16 chars]`. The stored group number
is 1-based; CS-R8600 shows it 0-based. The "next slot" table links each slot to
the next one in its group, and `0xFFFF` ends the chain. Unused slots form a
separate free chain.

Channel numbers come from the bitmap. Taking all groups in table order, and each
group's channels in chain order, the channels map one to one onto the in-use
bits of the bitmap in ascending order. Empty rows inside a group are simply
numbers whose bit is not set.

### Per-slot flags (`0x1D4E5`)

| Bits | Field |
|---|---|
| 0 | Slot empty |
| 1 | Skip |
| 2 | P-Skip |
| 4–7 | Scan SEL (0 = off, 1–9) |

### Channel record (47 bytes)

| Offset | Type | Field |
|---|---|---|
| `0x00` | uint32 LE | Frequency in Hz |
| `0x04` | uint8 | Bits 7–3: mode (0–17, see table above). Bits 2–0: filter (0 = FIL1, 1 = FIL2, 2 = FIL3) |
| `0x05` | uint16 LE | Flags, see below |
| `0x07` | uint8 | Tuning step index: 0 = 100 Hz, 1k, 2.5k, 3.125k, 5k, 6.25k, 8.33k, 9k, 10k, 12.5k, 20k, 25k, 12 = 100k, 13 = programmable |
| `0x08` | uint16 LE | Programmable tuning step in 0.1 kHz units |
| `0x0A` | uint32 LE | Duplex offset in Hz |
| `0x0F` | char[16] | Name, padded with spaces |
| `0x1F` | uint8 | Analog modes: tone mode (0 = off, 1 = TSQL, 2 = DTCS) |
| `0x20` | uint8 | Analog modes: DTCS polarity (0 = normal, 1 = reverse) |
| `0x21` | uint8 | Analog modes: CTCSS tone index (0 = 67.0 Hz, standard 50-tone list) |
| `0x22` | uint8 | Analog modes: DTCS code index (0 = 023, standard 104-code list) |

For digital modes, bytes `0x1F`–`0x2E` hold mode-specific settings instead of
tone data. These are not decoded yet.

Flags word at `0x05`:

| Bits | Field |
|---|---|
| 5–6 | Duplex (0 = off, 1 = −, 2 = +) |
| 10 | P.AMP |
| 11–12 | ATT (0 = off, 1 = 10 dB, 2 = 20 dB, 3 = 30 dB) |
| 13–14 | Antenna (0 = ANT1, 1 = ANT2, 2 = ANT3) |
| 15 | IP+ |
| others | Unknown; bit 7 may be TS Function |

## Clone protocol

`r8600_dump.py` uses Icom's clone protocol on the first USB serial port. Every
frame is `FE FE <src> <dst> <cmd> <payload> FD`, with the PC as `EE` and the
radio as `EF`.

| Step | Direction | Command | Payload |
|---|---|---|---|
| Model query | PC → radio | `E0` | `00 00 00 00` |
| Model answer | radio → PC | `E1` | `38 18 00 01`, then revision and other data |
| Clone out | PC → radio | `E2` | `38 18 00 01` |
| Memory data | radio → PC | `E4` | one block per frame, see below |
| End of clone | radio → PC | `E5` | `Icom Inc.` followed by two more bytes |

Each `E4` payload holds a 4-byte big-endian address, a 1-byte length (64 bytes
per block), the data, and a checksum. The checksum is the two's complement of
the sum of the address, length and data bytes as sent.

Unlike older Icom radios, which send ASCII hex text, the IC-R8600 sends these
frames as raw binary, encoded like this:

- Bytes `0xFA`–`0xFF` are escaped as `0xFF` followed by the byte's low nibble,
  so they can't be mistaken for frame markers. Undo this first.
- Every data byte has its high bit inverted (`byte ^ 0x80`). The checksum is
  calculated over the inverted bytes, so check it before inverting back.

After decoding, the 125 440-byte image (`0x1EA00`) is identical to the data in a
CS-R8600 `.icf` file. The radio answered at every tested speed from 9600 to
115200 baud; a full read takes about 13 seconds.

## Contributing

Bug reports and pull requests are welcome. The easiest way to help decode the
remaining fields is to save a small `.icf` file in CS-R8600 with a few channels
whose names describe their settings (for example `P25 NAC 293`), then
run the script with `--debug` and open an issue with the output and the file.

## Credits

- Written by Kari, OH1KK
- Format and protocol reverse-engineering and code developed together with
  [Claude](https://claude.ai) by Anthropic

## License

MIT — see [LICENSE](LICENSE).

IC-R8600 and CS-R8600 are trademarks of Icom Inc. This project is not affiliated
with or endorsed by Icom.
