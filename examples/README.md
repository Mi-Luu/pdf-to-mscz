# Examples

No sample scores are bundled — the ones this tool was built against are copyrighted
arrangements, and a printed PDF is exactly the input people cannot share.

Drop your own PDF here and run:

```bash
python scripts/pipeline.py probe --pdf examples/myscore.pdf
python scripts/pipeline.py all   --pdf examples/myscore.pdf --work build/myscore --out out/myscore.mscz
```

Good first candidates:

| Source | Why |
|---|---|
| a clean MuseScore/LilyPond/Sibelius export of a single-staff melody | widest margin for OMR, ~99 % note accuracy |
| a two-staff piano piece with moderate density | representative real-world case |
| a scanned method-book page at 300dpi | tests the `scan-omr` path |

Avoid starting with: handwritten music, photocopied pages below 200dpi, dense orchestral
scores with 20+ staves, or anything with heavy tuplets — those stress exactly the parts of
Audiveris this tool cannot fix for you.
