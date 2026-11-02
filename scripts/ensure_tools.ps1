# 准备 pdf-to-mscz 需要的外部程序：MuseScore 4 + Audiveris 5.x
#
#   pwsh -File ensure_tools.ps1            # 查找 / 下载安装
#   pwsh -File ensure_tools.ps1 -Check     # 只检查
#
# Audiveris 解压到 %LOCALAPPDATA%\pdf-to-mscz\Audiveris（自带 JRE，不改系统）。
# 下载走 GitHub release；单线程会被限速到几十 KB/s，因此按 Content-Length 切 4 段并行 + 断点续传，
# 拼好后必须与 Content-Length 完全一致才解压（长度不符时的 MSI 会让 msiexec 报 1639）。

param(
    [switch]$Check,
    [string]$CacheDir = (Join-Path $env:LOCALAPPDATA "pdf-to-mscz"),
    [string]$AudiverisVersion = "5.11.0",
    [int]$Parts = 4
)

$ErrorActionPreference = "Stop"

function Find-MuseScore {
    $cands = @(
        "D:\MuseScore4\bin\MuseScore4.exe",
        "C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
        "C:\Program Files (x86)\MuseScore 4\bin\MuseScore4.exe"
    )
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    foreach ($n in @("MuseScore4", "musescore", "mscore")) {
        $p = Get-Command $n -ErrorAction SilentlyContinue
        if ($p) { return $p.Source }
    }
    return $null
}

function Find-Audiveris([string]$root) {
    if (-not (Test-Path $root)) { return $null }
    $jar = Join-Path $root "app\audiveris.jar"
    $java = Join-Path $root "runtime\bin\java.exe"
    if ((Test-Path $jar) -and (Test-Path $java)) { return $root }
    return $null
}

function Get-RemoteLength([string]$url) {
    $headers = & curl.exe -sL -r 0-0 -D - -o NUL --max-time 60 $url 2>$null
    foreach ($line in $headers) {
        if ($line -match '(?i)^content-range:\s*bytes\s+\d+-\d+/(\d+)') { return [int64]$Matches[1] }
        if ($line -match '(?i)^content-length:\s*(\d+)') { $cl = [int64]$Matches[1] }
    }
    if ($cl) { return $cl }
    return 0
}

function Get-FileParallel([string]$url, [string]$outFile, [int]$parts) {
    $total = Get-RemoteLength $url
    if ($total -le 0) { throw "拿不到 $url 的 Content-Length" }
    Write-Host ("download {0:N1} MB in {1} parts" -f ($total / 1MB), $parts)

    $dir = "$outFile.parts"
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $dir | Out-Null

    $chunk = [int64][math]::Floor($total / $parts)
    $jobs = @()
    for ($i = 0; $i -lt $parts; $i++) {
        $start = $i * $chunk
        $end = if ($i -eq $parts - 1) { $total - 1 } else { ($i + 1) * $chunk - 1 }
        $pf = Join-Path $dir "p$i.bin"
        $need = $end - $start + 1
        $jobs += Start-Job -ScriptBlock {
            param($url, $pf, $start, $end, $need)
            for ($k = 0; $k -lt 2000; $k++) {
                $have = 0
                if (Test-Path $pf) { $have = (Get-Item $pf).Length }
                if ($have -ge $need) { break }
                $from = $start + $have
                $tmp = "$pf.t"
                & curl.exe -L -s --max-time 45 -r "$from-$end" -o $tmp $url 2>$null | Out-Null
                if (Test-Path $tmp) {
                    $len = (Get-Item $tmp).Length
                    if ($len -gt 0) {
                        $fs = [System.IO.File]::Open($pf, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write)
                        $bytes = [System.IO.File]::ReadAllBytes($tmp)
                        $fs.Write($bytes, 0, $bytes.Length)
                        $fs.Close()
                    }
                    Remove-Item $tmp -Force
                }
            }
            if (Test-Path $pf) { (Get-Item $pf).Length } else { 0 }
        } -ArgumentList $url, $pf, $start, $end, $need
    }

    $null = $jobs | Wait-Job -Timeout 3600
    $got = $jobs | Receive-Job
    $jobs | Remove-Job -Force

    $fs = [System.IO.File]::Create($outFile)
    for ($i = 0; $i -lt $parts; $i++) {
        $pf = Join-Path $dir "p$i.bin"
        if (-not (Test-Path $pf)) { $fs.Close(); throw "分段 $i 缺失" }
        $bytes = [System.IO.File]::ReadAllBytes($pf)
        $fs.Write($bytes, 0, $bytes.Length)
    }
    $fs.Close()
    Remove-Item $dir -Recurse -Force

    $size = (Get-Item $outFile).Length
    if ($size -ne $total) { throw "拼接后 $size 字节，应为 $total 字节" }
    Write-Host ("downloaded {0} bytes OK" -f $size)
}

function Install-Audiveris {
    $msi = Join-Path $CacheDir "audiveris-$AudiverisVersion.msi"
    $rels = "github.com/Audiveris/audiveris/releases/download/$AudiverisVersion/Audiveris-$AudiverisVersion-windows-x86_64.msi"
    $mirrors = @("https://gh-proxy.com/https://$rels", "https://$rels", "https://ghproxy.net/https://$rels")

    New-Item -ItemType Directory -Force $CacheDir | Out-Null
    $ok = $false
    foreach ($m in $mirrors) {
        try {
            Write-Host "try $m"
            Get-FileParallel -url $m -outFile $msi -parts $Parts
            $ok = $true
            break
        } catch {
            Write-Warning "mirror failed: $($_.Exception.Message)"
        }
    }
    if (-not $ok) { throw "所有镜像都下载失败，请手动下载 Audiveris-$AudiverisVersion-windows-x86_64.msi 放到 $CacheDir" }

    $tmp = Join-Path $CacheDir "_extract"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $tmp | Out-Null
    # TARGETDIR 必须带引号，否则 msiexec 退出码 1639
    $argStr = '/a "' + $msi + '" /qn TARGETDIR="' + $tmp + '"'
    $p = Start-Process msiexec.exe -ArgumentList $argStr -PassThru -Wait
    if ($p.ExitCode -ne 0) { throw "msiexec 解包失败，exit=$($p.ExitCode)" }

    $src = Join-Path $tmp "Audiveris"
    if (-not (Test-Path (Join-Path $src "app\audiveris.jar"))) { throw "解包后找不到 app/audiveris.jar" }
    $dest = Join-Path $CacheDir "Audiveris"
    Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
    Move-Item $src $dest
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    return $dest
}

# ---------------------------------------------------------------- main

$ms = Find-MuseScore
if ($ms) { Write-Host "MuseScore : $ms" } else { Write-Warning "没找到 MuseScore 4" }
if ($ms) { $v = & $ms --version 2>$null | Select-Object -First 1; if ($v) { Write-Host "          $v" } }

$aud = Find-Audiveris (Join-Path $CacheDir "Audiveris")
if (-not $aud -and -not $Check) { $aud = Install-Audiveris }
if ($aud) { Write-Host "Audiveris : $aud" } elseif (-not $Check) { Write-Warning "Audiveris 未就绪" }

if (-not $ms -and $Check) { Write-Host "MuseScore 缺失：装 MuseScore 4 或后续用 --musescore 指定路径" }
if (-not $aud -and $Check) { Write-Host "Audiveris 缺失：运行本脚本不带 -Check 即可下载" }

Write-Host ""
Write-Host "AUDIVERIS_HOME=$aud"
Write-Host "MUSESCORE=$ms"
