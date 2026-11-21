# The Passport Page of a Country That Isn't There

The Republic of Nordhavn is a fictional North Atlantic island nation — a
fishing republic of steep harbours and lighthouses, capital Havnvik, motto
"Ex mare, perduramus" (from the sea, we endure), whose bilingual
(Nordhavnisk/English) passport this spread reproduces.

The open spread shows the data page of Elena Sofia Marisdottir (born 14 Mar
1991 in Havnvik, passport NRD 482 916, issued 02 May 2023) with her portrait,
gold state seal, signature and a valid machine-readable zone, facing a visa
page stamped with a plausible travel history — Keflavík, Bergen, London
Heathrow and Tórshavn, 2024–2025 — plus a harbourmaster's fee stamp, an
Austerland visa, and the telling detail: a seafarer's endorsement
registering her as crew of the fishing vessel FV Skarven.

## Build

`main.py` is pure Python 3.13 stdlib (no PIL, no network in the grading
sandbox). It draws the whole spread on a raw RGB buffer — text from glyph
bitmaps baked into `font.bin`, the portrait from `portrait.bin` — and writes
`/output/entry.png` (1024×724 PNG, ~230 KB). Run it with:

```sh
python3 main.py
```

Deterministic: two runs produce byte-identical output.
