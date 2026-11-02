# pdf-to-mscz

[![CI](https://github.com/Mi-Luu/pdf-to-mscz/actions/workflows/ci.yml/badge.svg)](https://github.com/Mi-Luu/pdf-to-mscz/actions/workflows/ci.yml)

Turn a **printed sheet-music PDF** into an **editable MuseScore 4 project** (`.mscz`).

Most sheet-music PDFs are dead ends: the notes are vector glyph outlines from a music font
(Opus, Maestro, Emmentaler, …), the text layer only contains glyph IDs, and there is no
MusicXML hiding inside. This tool accepts that reality and rebuilds the score the only way
that actually works — **optical music recognition**, then assembly — and it refuses to
pretend the result is perfect.

```
PDF ──▶ 300dpi raster ──▶ Audiveris OMR ──▶ per-page MusicXML
                                              │
                                              ▼
                          merge + renumber + metadata + tempo
                                              │
                                              ▼
                     MuseScore 4 ──▶ .mscz ──▶ .pdf ──▶ PNG contact check
```

---

## What it gives you

- `NAME.mscz` — a MuseScore 4 project you can open, edit and re-export
- `NAME.pdf` — layout preview produced from that same project
- `work/check/NAME-p*.png` — page images for side-by-side verification against the source

Optional extras: `NAME.mid` via MuseScore, and a `merged.musicxml` intermediate if you want
to edit in another notation app first.

## Requirements

| Component | Why | How |
|---|---|---|
| Python 3.10+ | runs the pipeline | `pip install -r requirements.txt` (PyMuPDF + lxml) |
| MuseScore 4 | MusicXML → mscz/pdf/midi | official installer, or AppImage/apt/flatpak on Linux |
| Audiveris 5.x | optical music recognition | official MSI/DEB/DMG, unpacked so that `app/audiveris.jar` and `runtime/bin/java*` both exist |

Both tools are free and open source. Nothing here calls a paid API.

## Install

```bash
git clone <this repo> pdf-to-mscz
cd pdf-to-mscz
pip install -r requirements.txt
```

**Windows (one command):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ensure_tools.ps1
```

It finds an existing MuseScore, then looks for a cached Audiveris, and only downloads when
nothing is found. `-Check` reports without installing. Audiveris lands in
`%LOCALAPPDATA%\pdf-to-mscz\Audiveris` (~156 MB, bundled JRE, no registry entries).

**macOS / Linux:** install MuseScore 4 normally, unpack Audiveris into a directory with the
same `app/` + `runtime/` layout, then:

```bash
export AUDIVERIS_HOME=/opt/pdf-to-mscz/Audiveris
export MUSESCORE_PATH=/usr/bin/musescore4
```

Or pass `--audiveris` / `--musescore` on any command.

## Quick start

One command, whole book:

```bash
python scripts/pipeline.py all \
  --pdf  "scores/lit.pdf" \
  --work "build/lit" \
  --out  "out/lit.mscz" \
  --title "5 Variations on lit" \
  --subtitle "Koe no Katachi OST" \
  --composer "Arranged by Animenz" \
  --lyricist "Original music by Kensuke Ushio" \
  --tempo "1:70,18:60"
```

Then verify — always verify:

```bash
# compare build/lit/check/lit-p1.png with page 1 of the PDF
```

Stage by stage, if you want control:

```bash
python scripts/pipeline.py probe   --pdf scores/lit.pdf
python scripts/pipeline.py render  --pdf scores/lit.pdf --work build/lit --dpi 300
python scripts/pipeline.py omr     --work build/lit --name lit
python scripts/pipeline.py merge   --work build/lit --name lit --title "..." --tempo "1:70,18:60"
python scripts/pipeline.py convert --input build/lit/merged.musicxml --out out/lit.mscz
```

### Options

| Option | Applies to | Meaning |
|---|---|---|
| `--pdf` | all, probe, render | source PDF |
| `--work` | all, render, omr, merge | scratch directory (pages, mxl, logs) |
| `--out` | all, convert | target `.mscz` |
| `--title` / `--subtitle` | all, merge, convert | title block (subtitle gets its own text style) |
| `--composer` / `--lyricist` | all, merge | right / left credit line |
| `--tempo "1:70,18:60"` | all, merge | measure:quarter-BPM, comma separated |
| `--page-breaks "22,38"` | merge | force `new-page` hints (often ignored by MuseScore) |
| `--no-sanitize` | merge | keep `<tuplet>` markers (see caveat below) |
| `--musescore` / `--audiveris` | all, omr, convert | explicit tool paths |
| `--dpi` | render (default 300), convert (default 200) | raster resolution |
| `--heap 4g` | all, omr | JVM heap for Audiveris |

## What the pipeline fixes for you

- **Page merge & renumbering** — Audiveris restarts measure numbers at 1 on every page;
  they are renumbered into one continuous score.
- **Metadata** — `movement-title` becomes the MuseScore title, `creator` elements become
  the composer/poet text boxes.
- **Subtitle** — MusicXML has no subtitle slot. Writing a newline into the title makes the
  subtitle inherit the title font and collide with the credits, so the `.mscx` is rewritten
  to carry a real `subtitle` text style before packaging.
- **Tempo marks** — printed `♩ = 70` is essentially never recognized by OMR; inject it by
  measure number.
- **Tuplet sanitizing** — see below. This one is the difference between a project that
  opens and one that doesn't.

## Known limitations (read before trusting the output)

OMR on dense piano music is roughly 85–95 % correct per note. Expect:

- wrong notes in 16th-note runs, tuplet passages and cross-staff figures
- rehearsal marks (boxed A/B/C), `l.h./r.h.` annotations, pedal marks and some 8va lines
  are usually lost
- slurs/ties occasionally attached to the wrong note
- odd stray characters at the end of systems
- pagination is re-flowed: page count/layout will differ from the source even if the music
  is continuous

**The tuplet caveat.** OMR often emits `<tuplet>` markers with no matching
`<time-modification>`. MuseScore imports that MusicXML fine and even exports a PDF from it —
but once saved as `.mscz`, the project **can no longer be opened** (`--score-meta` exits 21,
`-o` exits 1320). The pipeline therefore strips `<tuplet>` markers by default: pitches and
durations stay correct, only the little "3" bracket disappears. Re-add brackets by hand if
the engraving matters. `--no-sanitize` keeps them and accepts the risk.

**Title/credit quirks.** MuseScore 4 ignores `<work-title>` and places `<credit>` boxes
unreliably; the tool uses the `movement-title` + text-style rewrite path instead. Do not
"improve" this without testing in the actual MuseScore version.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `MuseScore` exit **1320**, or `--score-meta` exit **21** | the saved score is unusable — almost always the tuplet issue; re-run `merge` without `--no-sanitize` |
| `-o` returns **1639** during install | `msiexec` needs `TARGETDIR="C:\path with spaces"` quoted |
| Audiveris spews `AccessDeniedException` and does nothing | `%APPDATA%` is not writable in your sandbox; the pipeline always passes `-Duser.home` and `-Djava.io.tmpdir` into the work dir |
| Chinese/Japanese text looks like mojibake in the console | Windows console is GBK; the script reconfigures stdout, but redirecting through another tool may break it again |
| Title loses its quotation marks on Windows | PowerShell 5.1 eats embedded `"` when handing args to native programs; rename in MuseScore afterwards |
| Audiveris produces no `.mxl` | `-batch` without `-export` only recognizes; `-export` without `-batch` opens the GUI |
| Two Audiveris runs overwrite each other | never point two runs at the same `-output` directory |

## How it decides what to do

`probe` classifies the PDF:

- music fonts present (`Opus*`, `Maestro`, `Emmentaler`, `Leland`, `Bravura`, …) and a short
  text layer → vector score, OMR required
- full-page images and almost no vector drawing → scanned score, OMR required
- already `.mxl`/`.musicxml` → skip OMR entirely

## Repository layout

```
SKILL.md                 agent-facing instructions (this is also a DSH/Claude skill)
scripts/pipeline.py      the whole pipeline, six subcommands
scripts/ensure_tools.ps1 Windows tool finder / Audiveris bootstrapper
```

## License

MIT — see [LICENSE](LICENSE).

Built on the shoulders of [Audiveris](https://github.com/Audiveris/audiveris) (AGPL) and
[MuseScore](https://musescore.org) (GPL). This repository ships neither binary; the Windows
helper downloads Audiveris from its official GitHub release. Music you convert stays yours —
converting a PDF you own for personal study is on you and your local law.

---

## 中文说明

把**印刷体乐谱 PDF** 转成可编辑的 **MuseScore 4 工程 `.mscz`**。

多数乐谱 PDF 的文本层只有字形编号（音符是音乐字体的矢量轮廓），解析不出音高时值，所以
只能走光学识谱（OMR）：300dpi 光栅化 → Audiveris 识别 → 按页合卷并重编小节号 → MuseScore
导入导出 → 回渲 PDF 逐页比对。

**装机**：`pip install -r requirements.txt`，装 MuseScore 4 与 Audiveris 5.x。Windows 上
跑一次 `scripts\ensure_tools.ps1` 即可（自动找 MuseScore、自动下载并解包 Audiveris 到
`%LOCALAPPDATA%\pdf-to-mscz`，约 156 MB，不改注册表）；macOS/Linux 手动装好后设置
`AUDIVERIS_HOME` 与 `MUSESCORE_PATH`。

**用法**：

```bash
python scripts/pipeline.py all --pdf 谱.pdf --work 构建 --out 输出.mscz \
  --title "曲名" --subtitle "副标题" --composer "编曲" --lyricist "原曲" --tempo "1:70,18:60"
```

**务必验收**：看 `构建/check/*-p1.png` 与原始 PDF 对应页，重点核对密集十六分、三连音、
跨谱表段落。OMR 在密集钢琴谱上通常 85–95% 正确，排练记号、踏板、`l.h./r.h.` 基本读不出来。

**已知坑**：OMR 产出的 `<tuplet>` 缺少 `<time-modification>` 时，MuseScore 能导入也能导
PDF，但存成 `.mscz` 后**再也打不开**（返回码 1320 / 21）——所以默认剥掉三连音标记，时值
不变、只丢三角括号；副标题走 `.mscx` 的 `subtitle` 文本样式，避免套用大标题字号撞到人名。
