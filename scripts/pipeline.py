#!/usr/bin/env python3
"""PDF 乐谱 -> MuseScore .mscz 流水线。

子命令：
    probe    : 判断 PDF 类型（矢量/扫描），给出建议
    render   : 300dpi 渲染成 name-1.png ... name-N.png
    omr      : Audiveris 批量识别，每页一个 .mxl
    merge    : 把每页 .mxl 合成一份连续小节号的 MusicXML，并写入标题/速度
    convert  : MuseScore 转 mscz + pdf，并把 pdf 渲染成 png 备查
    all      : 以上一条龙

依赖：pymupdf、lxml；外部程序 MuseScore 4 与 Audiveris（见 ensure_tools.ps1）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    import pymupdf
except ImportError:  # 旧版 pymupdf 只暴露 fitz
    import fitz as pymupdf  # type: ignore

from lxml import etree as ET

try:  # Windows 控制台默认 GBK，中文输出会乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

MUSICXML_DOCTYPE = (
    '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
    '"http://www.musicxml.org/dtds/partwise.dtd">'
)

MUSESCORE_CANDIDATES = [
    # Windows
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files (x86)\MuseScore 4\bin\MuseScore4.exe",
    # macOS
    "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
    "/Applications/MuseScore 4.app/Contents/MacOS/MuseScore 4",
    # Linux
    "/usr/bin/musescore4",
    "/usr/local/bin/musescore4",
    "/usr/bin/musescore",
    "/usr/local/bin/mscore",
    "/snap/bin/musescore",
]

AUDIVERIS_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "pdf-to-mscz" / "Audiveris",
    Path.home() / ".local" / "share" / "pdf-to-mscz" / "Audiveris",
    Path("/opt/pdf-to-mscz/Audiveris"),
    Path("/usr/local/share/pdf-to-mscz/Audiveris"),
]


# ---------------------------------------------------------------- 工具定位


def find_musescore(explicit: str | None = None) -> Path:
    explicit = explicit or os.environ.get("MUSESCORE_PATH")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        raise SystemExit(f"MuseScore 不存在: {p}")
    for cand in MUSESCORE_CANDIDATES:
        if Path(cand).is_file():
            return Path(cand)
    for name in ("MuseScore4", "musescore4", "musescore", "mscore", "MuseScore 4"):
        found = shutil.which(name)
        if found:
            return Path(found)
    raise SystemExit(
        "找不到 MuseScore 4。用 --musescore 指定可执行文件路径，或设置环境变量 MUSESCORE_PATH；"
        "Windows 上也可以先跑 scripts/ensure_tools.ps1。"
    )


def find_audiveris(explicit: str | None = None) -> Path:
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("AUDIVERIS_HOME"):
        cands.append(Path(os.environ["AUDIVERIS_HOME"]))
    cands.extend(AUDIVERIS_CANDIDATES)
    for c in cands:
        for java in ("runtime/bin/java.exe", "runtime/bin/java"):
            if (c / "app" / "audiveris.jar").is_file() and (c / java).is_file():
                return c
    raise SystemExit(
        "找不到 Audiveris。先运行 scripts/ensure_tools.ps1，或用 --audiveris 指定解压后的 Audiveris 目录"
        "（目录里应有 app/audiveris.jar 与 runtime/bin/java）。"
    )


# ---------------------------------------------------------------- probe


def cmd_probe(args) -> None:
    doc = pymupdf.open(args.pdf)
    meta = doc.metadata or {}
    creator = (meta.get("creator") or "") + " " + (meta.get("producer") or "")
    fonts: set[str] = set()
    pages = []
    for i in range(doc.page_count):
        p = doc[i]
        try:
            page_fonts = p.get_fonts()  # pymupdf >= 1.24
        except AttributeError:  # 老版本只有文档级接口
            page_fonts = doc.get_page_fonts(i)
        for f in page_fonts:
            fonts.add(f[3])
        pages.append(
            {
                "page": i + 1,
                "width": round(p.rect.width),
                "height": round(p.rect.height),
                "images": len(p.get_images(full=True)),
                "drawings": len(p.get_drawings()),
                "text_chars": len(p.get_text().strip()),
            }
        )
    music_fonts = sorted(
        f
        for f in fonts
        if re.search(
            r"Opus|Maestro|Emmentaler|Leland|Bravura|Gonville|Petaluma|Sonata|Sebastian|"
            r"Chaconne|Norfolk|November|Finale|MScore|MuseJazz|Engraver",
            f,
            re.IGNORECASE,
        )
    )
    scanned = all(pg["images"] > 0 and pg["drawings"] < 20 for pg in pages)
    # 大量矢量线条 = 谱线/符干，几乎可以断定是制谱软件导出（即便字体名不在名单里）
    engraved = all(pg["drawings"] > 200 and pg["images"] == 0 for pg in pages)
    if music_fonts:
        route = "vector-omr"
        hint = "矢量制谱导出：音符是字形轮廓、没有文本语义，必须 OMR"
    elif scanned:
        route = "scan-omr"
        hint = "整页位图扫描件：直接 OMR；分辨率低于 200dpi 先提醒用户"
    elif engraved:
        route = "vector-omr"
        hint = "大量矢量线条（疑似未识别的音乐字体导出）：按矢量谱走 OMR"
    else:
        route = "unknown"
        hint = "未见音乐字体也未见整页图像，可能是不可见的排版方式，先渲染一页目视确认"
    print(
        json.dumps(
            {
                "pdf": str(args.pdf),
                "pages": doc.page_count,
                "creator": creator.strip(),
                "fonts": sorted(fonts),
                "music_fonts": music_fonts,
                "page_detail": pages[:5],
                "route": route,
                "hint": hint,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


# ---------------------------------------------------------------- render


def cmd_render(args) -> None:
    pdf = Path(args.pdf)
    work = Path(args.work)
    pages_dir = work / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    stem = args.name or pdf.stem
    doc = pymupdf.open(pdf)
    for i in range(doc.page_count):
        out = pages_dir / f"{stem}-{i + 1}.png"
        doc[i].get_pixmap(dpi=args.dpi).save(out)
        print(f"rendered {out}")
    if doc.page_count == 0:
        raise SystemExit("PDF 没有页面")
    print(f"{doc.page_count} pages -> {pages_dir}")


# ---------------------------------------------------------------- omr


def cmd_omr(args) -> None:
    work = Path(args.work)
    pages_dir = work / "pages"
    stem = args.name
    pages = sorted(pages_dir.glob(f"{stem}-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    if not pages:
        raise SystemExit(f"{pages_dir} 里没有 {stem}-N.png，先运行 render")
    aud = find_audiveris(args.audiveris)
    out_dir = work / "mxl"
    out_dir.mkdir(parents=True, exist_ok=True)
    home = work / "home"
    tmp = work / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)

    java = aud / "runtime" / "bin" / ("java.exe" if os.name == "nt" else "java")
    sep = ";" if os.name == "nt" else ":"
    cp = f"{aud / 'app' / 'audiveris.jar'}{sep}{aud / 'app'}{os.sep}*"
    cmd = [
        str(java),
        f"-Duser.home={home}",
        f"-Djava.io.tmpdir={tmp}",
        f"-Xmx{args.heap}",
        "-cp",
        cp,
        "org.audiveris.omr.Main",
        "-batch",
        "-export",
        "-output",
        str(out_dir),
        "-force",
        *[str(p) for p in pages],
    ]
    env = dict(os.environ, TEMP=str(tmp), TMP=str(tmp))
    print("running audiveris ...")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, errors="replace")
    tail = (proc.stdout or "")[-4000:]
    if proc.returncode != 0:
        print(tail, file=sys.stderr)
        raise SystemExit(f"Audiveris 失败，exit={proc.returncode}")
    mxl = sorted(out_dir.glob("*.mxl"))
    if not mxl:
        print(tail, file=sys.stderr)
        raise SystemExit("Audiveris 跑完但没有产出 .mxl")
    print(f"{len(mxl)} mxl -> {out_dir}")


# ---------------------------------------------------------------- merge


def _read_score(path: Path) -> ET._Element:
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path) as z:
            member = next(
                n for n in z.namelist() if n.endswith(".xml") and "META-INF" not in n.upper()
            )
            return ET.fromstring(z.read(member))
    return ET.parse(path).getroot()


def cmd_merge(args) -> None:
    work = Path(args.work)
    mxl_dir = work / "mxl"
    stem = args.name or args.pdf_stem

    def sort_key(p: Path):
        m = re.search(r"-(\d+)$", p.stem)
        return int(m.group(1)) if m else 0

    files = sorted(mxl_dir.glob(f"{stem}-*.mxl"), key=sort_key) or sorted(
        mxl_dir.glob("*.mxl"), key=sort_key
    )
    if not files:
        raise SystemExit(f"{mxl_dir} 里没有 .mxl")
    if len(files) > 1 and not all(re.search(r"-\d+$", f.stem) for f in files):
        print("警告: 页文件名不带 -N 序号，按文件名排序，可能与实际页序不符", file=sys.stderr)

    root = _read_score(files[0])
    part = root.find("part")
    if part is None:
        raise SystemExit("MusicXML 里没有 <part>")

    counts = []
    n = 0
    for m in part.findall("measure"):
        n += 1
        m.set("number", str(n))
    counts.append(n)

    for f in files[1:]:
        other = _read_score(f)
        op = other.find("part")
        if op is None:
            continue
        cnt = 0
        for m in op.findall("measure"):
            n += 1
            cnt += 1
            m.set("number", str(n))
            part.append(m)
        counts.append(cnt)

    # 元信息
    work_el = root.find("work")
    if work_el is None:
        work_el = ET.Element("work")
        root.insert(0, work_el)
    wt = work_el.find("work-title")
    if wt is None:
        wt = ET.SubElement(work_el, "work-title")
    wt.text = args.title or ""

    mt = root.find("movement-title")
    if mt is None:
        mt = ET.Element("movement-title")
        root.insert(list(root).index(work_el) + 1, mt)
    # MuseScore 4 用 movement-title 渲染标题框主标题；\n 会被渲染成换行，
    # 所以副标题直接接在第二行（<credit> + credit-words 的坐标在 4.7 上不可靠）。
    mt.text = args.title or ""

    ident = root.find("identification")
    if ident is None:
        ident = ET.Element("identification")
        root.insert(list(root).index(mt) + 1, ident)
    for child in list(ident):
        ident.remove(child)
    if args.composer:
        ET.SubElement(ident, "creator", type="composer").text = args.composer
    if args.lyricist:
        ET.SubElement(ident, "creator", type="lyricist").text = args.lyricist

    for credit in root.findall("credit"):
        root.remove(credit)

    measures = {m.get("number"): m for m in part.findall("measure")}

    # OMR 输出的 <tuplet> 缺少 time-modification 时，MuseScore 能导入、但存成 mscz 后
    # 再也打不开（--score-meta 返回 21）。三角括号只是视觉信息，音符时值本身已经正确，
    # 因此默认剥掉它；需要保留时加 --no-sanitize。
    stripped_tuplets = 0
    if not getattr(args, "no_sanitize", False):
        for tup in root.findall(".//tuplet"):
            stripped_tuplets += 1
            tup.getparent().remove(tup)
    if stripped_tuplets:
        print(f"sanitize: removed {stripped_tuplets} <tuplet> markers", file=sys.stderr)

    def insert_at_start(m: ET._Element, el: ET._Element) -> None:
        m.insert(0, el)

    for spec in (s for s in (args.tempo or "").split(",") if s.strip()):
        mnum, bpm = spec.split(":")
        m = measures.get(str(int(mnum.strip())))
        if m is None:
            print(f"警告: 没有第 {mnum} 小节，跳过速度标记", file=sys.stderr)
            continue
        d = ET.Element("direction", placement="above")
        dt = ET.SubElement(d, "direction-type")
        met = ET.SubElement(dt, "metronome")
        ET.SubElement(met, "beat-unit").text = "quarter"
        ET.SubElement(met, "per-minute").text = str(int(bpm))
        ET.SubElement(d, "sound", tempo=str(int(bpm)))
        pos = 1 if (len(m) and m[0].tag == "attributes") else 0
        m.insert(pos, d)

    breaks = [b for b in (args.page_breaks or "").split(",") if b.strip()]
    for b in breaks:
        m = measures.get(str(int(b.strip())))
        if m is None:
            continue
        insert_at_start(m, ET.Element("print", new_page="yes"))

    out = Path(args.out) if args.out else work / "merged.musicxml"
    ET.ElementTree(root).write(
        str(out), xml_declaration=True, encoding="UTF-8", doctype=MUSICXML_DOCTYPE
    )
    notes = len(root.findall(".//note"))
    print(
        json.dumps(
            {
                "output": str(out),
                "pages": len(files),
                "measures_per_page": counts,
                "measures_total": n,
                "notes": notes,
            },
            ensure_ascii=False,
        )
    )


# ---------------------------------------------------------------- convert


def apply_subtitle(mscx: Path, title: str | None, subtitle: str | None) -> bool:
    """把标题拆成「主标题 + 副标题」两种文字样式。

    MusicXML 只有 movement-title 一条，换行写进去会整段套用 title 字号，
    副标题会撞到左上/右上的 composer/poet。这里直接改 MuseScore 的 mscx：
    title 段落只放主标题，紧跟一个 <Text><style>subtitle</style>。
    """
    if not subtitle:
        return False
    try:
        tree = ET.parse(str(mscx))
    except ET.XMLSyntaxError:
        return False
    root = tree.getroot()
    vbox = root.find(".//VBox")
    if vbox is None:
        return False
    title_el = None
    for t in vbox.findall("Text"):
        if (t.findtext("style") or "").lower() == "title":
            title_el = t
            break
    if title_el is None:
        return False

    if title:
        txt = title_el.find("text")
        if txt is not None:
            txt.text = title

    sub = ET.Element("Text")
    ET.SubElement(sub, "style").text = "subtitle"
    st = ET.SubElement(sub, "text")
    st.text = subtitle
    title_el.addnext(sub)

    tree.write(str(mscx), xml_declaration=True, encoding="UTF-8")
    return True


def cmd_convert(args) -> None:
    ms = find_musescore(args.musescore)
    src = Path(args.input)
    mscz = Path(args.out)
    mscz.parent.mkdir(parents=True, exist_ok=True)

    mscx = mscz.with_suffix(".tmp.mscx")
    subprocess.run([str(ms), "-o", str(mscx), str(src)], check=True)
    if not mscx.is_file():
        raise SystemExit(f"MuseScore 没有生成中间文件 {mscx}")

    if getattr(args, "subtitle", None) and apply_subtitle(mscx, args.title, args.subtitle):
        print("subtitle: 已拆成 title + subtitle 两种文字样式")

    subprocess.run([str(ms), "-o", str(mscz), str(mscx)], check=True)
    mscx.unlink(missing_ok=True)
    if not mscz.is_file():
        raise SystemExit(f"MuseScore 没有生成 {mscz}")
    print(f"mscz -> {mscz} ({mscz.stat().st_size} bytes)")


    pdf = mscz.with_suffix(".pdf")
    subprocess.run([str(ms), "-o", str(pdf), str(mscz)], check=True)
    print(f"pdf  -> {pdf}")

    check_dir = Path(args.check_dir) if args.check_dir else mscz.parent / "check"
    check_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf)
    for i in range(doc.page_count):
        doc[i].get_pixmap(dpi=args.dpi).save(check_dir / f"{mscz.stem}-p{i + 1}.png")
    print(f"check png -> {check_dir} ({doc.page_count} pages)")


# ---------------------------------------------------------------- all


def cmd_all(args) -> None:
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    args.name = args.name or Path(args.pdf).stem

    class NS:
        pass

    stage = NS()
    stage.pdf, stage.work, stage.name, stage.dpi = args.pdf, str(work), args.name, 300
    cmd_probe(stage)
    cmd_render(stage)

    omr = NS()
    omr.work, omr.name, omr.audiveris, omr.heap = str(work), args.name, args.audiveris, args.heap
    cmd_omr(omr)

    merge = NS()
    merge.work = str(work)
    merge.name = args.name
    merge.pdf_stem = args.name
    merge.title = args.title
    merge.subtitle = args.subtitle
    merge.composer = args.composer
    merge.lyricist = args.lyricist
    merge.tempo = args.tempo
    merge.page_breaks = args.page_breaks
    merge.no_sanitize = args.no_sanitize
    merge.out = str(work / "merged.musicxml")
    cmd_merge(merge)

    conv = NS()
    conv.musescore = args.musescore
    conv.input = str(work / "merged.musicxml")
    conv.out = args.out
    conv.check_dir = str(work / "check")
    conv.title = args.title
    conv.subtitle = args.subtitle
    conv.dpi = 200
    cmd_convert(conv)


# ---------------------------------------------------------------- CLI


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="乐谱 PDF -> MSCZ")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--pdf", required=True)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("render")
    p.add_argument("--pdf", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--name")
    p.add_argument("--dpi", type=int, default=300)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("omr")
    p.add_argument("--work", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--audiveris")
    p.add_argument("--heap", default="4g")
    p.set_defaults(func=cmd_omr)

    p = sub.add_parser("merge")
    p.add_argument("--work", required=True)
    p.add_argument("--name")
    p.add_argument("--pdf-stem")
    p.add_argument("--out")
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--composer")
    p.add_argument("--lyricist")
    p.add_argument("--tempo", help='形如 "1:70,18:60"')
    p.add_argument("--page-breaks", help='形如 "22,38"')
    p.add_argument("--no-sanitize", action="store_true", help="保留 <tuplet>（默认剥掉，见 README 的已知问题）")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("convert")
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--musescore")
    p.add_argument("--check-dir")
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--dpi", type=int, default=200)
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("all")
    p.add_argument("--pdf", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True, help="输出的 .mscz 路径")
    p.add_argument("--name", help="页文件名前缀，默认取 PDF 文件名")
    p.add_argument("--title")
    p.add_argument("--subtitle")
    p.add_argument("--composer")
    p.add_argument("--lyricist")
    p.add_argument("--tempo")
    p.add_argument("--page-breaks")
    p.add_argument("--no-sanitize", action="store_true")
    p.add_argument("--musescore")
    p.add_argument("--audiveris")
    p.add_argument("--heap", default="4g")
    p.set_defaults(func=cmd_all)

    args = ap.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
