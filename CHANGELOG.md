# Changelog

## 1.0.0 — 2026-11-02

First public release, extracted from a working conversion of two full Animenz piano
transcriptions (7 pages and 6 pages, 124 / 128 measures).

Pipeline:

- `probe` — classifies a PDF as vector score / scan / already-MusicXML and prints per-page
  font, image and drawing statistics
- `render` — PyMuPDF 300dpi rasterisation with Audiveris-friendly `name-1.png … name-N.png`
  naming
- `omr` — batch Audiveris invocation with a writable `user.home` / `java.io.tmpdir` inside
  the work directory (sandboxes deny `%APPDATA%`)
- `merge` — page-level `.mxl` → one continuous score: renumbering, cross-page attribute
  retention, title/credit metadata, per-measure tempo injection, optional page breaks
- `convert` — MusicXML → `.mscx` → `.mscz` → `.pdf` → per-page PNG for verification
- `all` — the whole chain

Notable fixes carried over from development:

- **tuplet sanitizing**: OMR emits `<tuplet>` without `<time-modification>`; such a score
  imports and renders, but the saved `.mscz` cannot be reopened (exit 21 / 1320).
  Markers are stripped by default, durations and pitches untouched.
- **subtitle handling**: rewrite the `.mscx` title frame into `title` + `subtitle` text
  styles instead of abusing `<credit>` coordinates, which MuseScore 4.7 ignores.
- **Windows tool bootstrap**: parallel ranged download with byte-exact length check before
  `msiexec /a`, because a truncated concatenation fails with exit code 1639.

## Roadmap

- [ ] keep tuplets but repair `time-modification` instead of dropping the bracket
- [ ] options for instrument/part mapping on multi-instrument scores
- [ ] automatic diff against the source PDF's vector data to flag suspicious measures
- [ ] macOS/Linux bootstrap scripts
