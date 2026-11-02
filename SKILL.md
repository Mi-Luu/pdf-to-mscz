---
name: pdf-to-mscz
description: 把印刷体乐谱 PDF（Sibelius/MuseScore/Finale/扫描件）通过 OMR 转成可编辑的 MuseScore 工程 .mscz，并给出 PDF 预览与逐页校验。用于「PDF 转 mscz」「曲谱转 MuseScore」「谱子做成可编辑工程」这类请求；不用于音频扒谱（那是 audio-to-band-score）、也不是把现成 .mxl/.musicxml 换个后缀。
---

# 乐谱 PDF → MSCZ

目标是交付一个**能打开、能改、主体正确**的 MuseScore 4 工程，并如实说明哪些地方还需要人工校对。不做「看起来很完整」的假交付。

## 先判断这活能不能干

运行 `scripts/pipeline.py probe --pdf <路径>`，看输出：

| 判据 | 结论 |
|---|---|
| 有 `/Creator (Sibelius…)`、`OpusStd`/`Maestro`/`Emmentaler` 等音乐字体，文字层很短 | 矢量制谱导出，音符是**轮廓字形**、无文本语义，必须走 OMR |
| 页面是整张位图（`images > 0` 且无字形绘制） | 扫描件，同样走 OMR，但先看分辨率，低于 200dpi 的先提醒用户 |
| 已有 `.mxl` / `.musicxml` / `.mvt` | 不要 OMR，直接 `MuseScore4 -o out.mscz in.musicxml` |

一页钢琴谱 OMR 大约 20–40 秒。页数超过 20 页先跟用户确认时间预期。

## 标准流程

```text
probe → render(300dpi) → omr(Audiveris) → merge(合卷+元信息) → convert(MuseScore) → verify(逐页看图)
```

一条命令跑完（工作目录、输出目录都要显式给）：

```text
python scripts/pipeline.py all --pdf "F:\谱\lit.pdf" --work "F:\谱\_work_lit" --out "F:\谱\lit.mscz" --title "5 Variations on lit" --subtitle "Koe no Katachi OST" --composer "Arranged By: Animenz" --tempo "1:70,18:60"
```

标题里要带英文双引号（如 `5 Variations on "lit"`）时，PowerShell 5.1 转交原生程序会把它吃掉，结果只剩 `5 Variations on lit`。需要精确引号时，事后再在 MuseScore 里改，或用 `--%` 之外的方式传参（写成临时变量文件也行）。

分步执行时各阶段产物：`work/pages/lit-1.png …`、`work/mxl/lit-1.mxl …`、`work/merged.musicxml`、`out.mscz` + `out.pdf` + `work/check/out-p1.png …`。

## 工具准备（只需一次）

MuseScore 4 与 Audiveris 缺一不可。

Windows 上跑一次即可：

```text
powershell -ExecutionPolicy Bypass -File scripts\ensure_tools.ps1
```

它会先找本机已装的 MuseScore，再找缓存里的 Audiveris，都没有才下载。加 `-Check` 只检查不安装。
脚本用 Windows PowerShell 5.1 编写并**存为 UTF-8 带 BOM**；用 UTF-8 无 BOM 保存会被 5.1 按 GBK 解析，中文字符串直接语法错误。改这个文件后请保持 BOM。

macOS / Linux 没有一键脚本，手动准备：

1. 装 MuseScore 4（官网 dmg / AppImage / apt / flatpak 均可），保证命令行能找到。
2. 装 Audiveris 5.x：Windows 用官方 msi（解包后保留 `app/` 与 `runtime/` 两级目录即可），Linux 用官方 deb 解包到同一结构，macOS 用 dmg 里的 app 包。
3. 把 Audiveris 根目录写进 `AUDIVERIS_HOME`，MuseScore 路径写进 `MUSESCORE_PATH`（或直接用 `--audiveris` / `--musescore` 传）。

`pipeline.py` 会自动尝试的默认位置：Windows `%LOCALAPPDATA%\pdf-to-mscz\Audiveris`、Linux/macOS `~/.local/share/pdf-to-mscz/Audiveris`、`/opt/pdf-to-mscz/Audiveris`；MuseScore 则扫常见安装路径与 PATH。

- MuseScore CLI 用法 `MuseScore4 -o 输出 输入`，转 mscz 和转 pdf 都用它。
- Audiveris 自带 JRE，不污染系统。
- Windows 下载 GitHub release 会限速，脚本按 Content-Length 切成 4 段并行拉、断点续传、最后核对**字节数与 Content-Length 完全一致**再解压。旧版本撞上残留文件会拼出坏包，`msiexec` 直接报 1639，这一点不要绕过。

## 各阶段要点

**render**：PyMuPDF 300dpi，文件名必须是 `lit-1.png … lit-N.png`（同前缀、连号），Audiveris 才按页序处理。300dpi 是 Audiveris 的标定分辨率，别随手改。

**omr**：`java -Duser.home=<work>/home -Djava.io.tmpdir=<work>/tmp -cp app/audiveris.jar;app/* org.audiveris.omr.Main -batch -export -output <work>/mxl -force <pages>`。
沙箱里 `%APPDATA%` 常常不可写，不加这两个 `-D` 会抛 `AccessDeniedException` 后空跑。

**merge**：每页导出的 `.mxl` 小节号都从 1 开始，必须重编号成连续小节。合卷时保留每页开头的 `<attributes>`（调号/拍号/谱号会被重复写一次，MuseScore 会正确忽略）。

**convert**：先 `-o out.mscz merged.musicxml`，再 `-o out.pdf out.mscz`。MuseScore 同一时刻只能有一个实例在跑同一路径，别并发。

**metadata**：MuseScore 4 里 `<movement-title>` → 标题框主标题，`<creator type="composer">` → 右上、`<creator type="lyricist">` → 左上，`<work-title>` 会被忽略。用 `<credit>` 摆自定义版式在 MuseScore 4.7 上坐标不可靠，别用。

**tempo**：`--tempo "1:70,18:60"` 在指定小节插 `<direction><metronome>` + `<sound tempo>`。谱面印的速度标记 OMR 基本读不出来，这是必须手工补的一环。

**sanitize（默认开）**：OMR 导出的 `<tuplet>` 常常没有配对的 `<time-modification>`。MusicXML 本身能导入、也能导出 PDF，但 MuseScore 存成 `.mscz` 之后就再也打不开（`--score-meta` 返回 21，`-o` 返回 1320）。症状极易误判成路径/权限问题，其实是谱面内容坏了。合卷阶段默认剥掉 `<tuplet>`（音符时值已经是对的，只丢三角括号），需要保留时加 `--no-sanitize`，并接受那次往返可能失败。

**subtitle**：`<movement-title>` 里写换行会让副标题套用 title 字号，撞上左右两角的人名。convert 阶段改为先生成 `.mscx`，把标题拆成 `title` + `subtitle` 两个 `<Text>` 样式，再打包成 `.mscz`——这是 MuseScore 4 上唯一稳的副标题办法。

## 验收（不做这步等于没交付）

1. 导出 PDF 逐页转 PNG，与原始 PDF 对应页并排看：小节号是否对得上、拍号/调号变化是否在正确小节、密集段有没有整拍丢失。
2. 小节总数与来源页累计是否一致（每页 OMR 出的小节数相加 = 合卷小节数 = 输出小节数）。
3. 随机挑 2–3 个密集段（16 分连奏、三连音、跨谱表连线）放大比对，统计明显错音。
4. 复算 `note` 总数，给用户一个量级（例如「124 小节 / 2083 个音符」），而不是笼统说「已完成」。

报告要具体到「哪几页/哪几小节需要核对」，不能只写「可能有少量错误」。已知不易识别：装饰音、跨谱表符干、非常规连音括号、踏板与反复记号、系统末尾偶发的杂字符。

## 容易踩的坑

- **别指望文本层**：Sibelius 导出的 PDF 里音乐字形是子集 TrueType，`post`/`cmap` 表被剥掉，`get_text()` 拿到的只是 CID 码，做不了「直接读音符」。
- **别并发跑 Audiveris**：同一 output 目录会被两个进程互相覆盖。
- **`-batch` 不加 `-export` 只识别不导出**；只加 `-export` 不加 `-batch` 会拉起 GUI。
- **分页会重排**：MusicXML 的 `<print new-page="yes"/>` 在 MuseScore 4.7 导入时不一定生效，输出页数与原件常不同，内容连续即可，不要为了凑页数硬改。
- **文件命名带空格和中文**：`msiexec` 的 `TARGETDIR="D:\a b\c"` 必须带引号，否则退出码 1639。
- 大文件（>30 页）建议先跑前 2 页验证流程通，再跑全本。

## 交付话术

给 `.mscz` 和预览 `.pdf`，一句话说明来源与方式（OMR，非人工听音），给出小节/音符量级，列出需要人工核对的段落，并提出可以继续把指定页面修到 100%。不夸大准确率。
