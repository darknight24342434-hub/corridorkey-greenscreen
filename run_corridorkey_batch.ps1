param(
    [string]$Root = ''
)

$ErrorActionPreference = "Stop"

# The batch root. This was originally a base64-encoded absolute path, which was how a
# non-ASCII directory name was carried through a PowerShell 5.1 script without an
# encoding accident. Pass -Root, or set CORRIDORKEY_BATCH_ROOT, instead.
$Root = if ($Root) { $Root } elseif ($env:CORRIDORKEY_BATCH_ROOT) { $env:CORRIDORKEY_BATCH_ROOT } else {
    throw "No batch root. Pass -Root <path>, or set CORRIDORKEY_BATCH_ROOT."
}
$OutRoot = Join-Path $Root "corridorkey"
# ComfyUI portable install root, e.g. D:\ComfyUI_windows_portable
if (-not $env:COMFYUI_ROOT) { throw "COMFYUI_ROOT is not set." }
$Python = Join-Path $env:COMFYUI_ROOT "python_embeded\python.exe"
# Defaults to the copy shipped in this repository.
$ScriptsDir = if ($env:CORRIDORKEY_SCRIPTS) { $env:CORRIDORKEY_SCRIPTS }
              else { Join-Path $PSScriptRoot "skills\corridorkey-video-matting\scripts" }
$MatteScript = Join-Path $ScriptsDir "run_corridorkey_video_matte.py"
$ImgSize = "512"
$DespeckleSize = "100"

# Prepend a directory holding ffmpeg, if one is configured.
if ($env:FFMPEG_DIR) { $env:Path = "$env:FFMPEG_DIR;$env:Path" }
$env:PYTHONUTF8 = "1"

New-Item -ItemType Directory -Force -Path $OutRoot | Out-Null
$Log = Join-Path $OutRoot "batch_corridorkey.log"
$Status = Join-Path $OutRoot "batch_status.csv"
$CompleteMarker = "_corridorkey_complete.txt"

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -LiteralPath $Log -Encoding UTF8 -Value $line
    Write-Output $line
}

function Test-Complete {
    param([string]$OutputDir)
    $marker = Join-Path $OutputDir $CompleteMarker
    if (!(Test-Path -LiteralPath $marker)) {
        return $false
    }
    return Test-OutputFiles -OutputDir $OutputDir
}

function Test-OutputFiles {
    param([string]$OutputDir)
    $files = @(
        "corridorkey_transparent_rgba.mov",
        "corridorkey_qc_checkerboard.mp4",
        "corridorkey_matte.mp4"
    )
    foreach ($name in $files) {
        $path = Join-Path $OutputDir $name
        if (!(Test-Path -LiteralPath $path)) {
            return $false
        }
        if ((Get-Item -LiteralPath $path).Length -le 0) {
            return $false
        }
    }
    return $true
}

function Remove-FrameDirs {
    param([string]$OutputDir)
    $resolvedOutRoot = (Resolve-Path -LiteralPath $OutRoot).Path
    $resolvedOutput = (Resolve-Path -LiteralPath $OutputDir).Path
    if (!$resolvedOutput.StartsWith($resolvedOutRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean outside output root: $resolvedOutput"
    }
    foreach ($child in @("source_frames", "hint_frames", "matte_frames", "qc_frames", "rgba_frames")) {
        $path = Join-Path $OutputDir $child
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

$files = Get-ChildItem -LiteralPath $Root -File |
    Where-Object { $_.Extension -match "^(?i)\.(mp4|mov|webm|mkv)$" } |
    Sort-Object Name

"timestamp,name,status,seconds,output_dir" | Set-Content -LiteralPath $Status -Encoding UTF8
Write-Log "BATCH START files=$($files.Count) root=$Root out=$OutRoot img_size=$ImgSize"

foreach ($file in $files) {
    $name = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
    $out = Join-Path $OutRoot $name
    New-Item -ItemType Directory -Force -Path $out | Out-Null

    if (Test-Complete -OutputDir $out) {
        Write-Log "SKIP $($file.Name) already complete"
        "$(Get-Date -Format o),$($file.Name),skip,0,$out" | Add-Content -LiteralPath $Status -Encoding UTF8
        continue
    }

    Write-Log "START $($file.Name)"
    $started = Get-Date
    try {
        $args = @(
            $MatteScript,
            "--input", $file.FullName,
            "--output-dir", $out,
            "--img-size", $ImgSize,
            "--despeckle-size", $DespeckleSize,
            "--device", "cuda",
            "--stream-encode"
        )
        $runStdout = Join-Path $out "corridorkey_stdout.log"
        $runStderr = Join-Path $out "corridorkey_stderr.log"
        $oldErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $Python @args > $runStdout 2> $runStderr
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $oldErrorActionPreference
        if (Test-Path -LiteralPath $runStdout) {
            Get-Content -LiteralPath $runStdout | ForEach-Object {
                Add-Content -LiteralPath $Log -Encoding UTF8 -Value "  $_"
            }
        }
        if (Test-Path -LiteralPath $runStderr) {
            Get-Content -LiteralPath $runStderr | ForEach-Object {
                Add-Content -LiteralPath $Log -Encoding UTF8 -Value "  STDERR: $_"
            }
        }
        if ($exitCode -ne 0) {
            throw "CorridorKey exited with code $exitCode"
        }
        if (!(Test-OutputFiles -OutputDir $out)) {
            throw "Expected output files were not created"
        }
        Set-Content -LiteralPath (Join-Path $out $CompleteMarker) -Encoding UTF8 -Value "completed $(Get-Date -Format o)"
        Remove-FrameDirs -OutputDir $out
        $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
        Write-Log "DONE $($file.Name) seconds=$seconds output=$out"
        "$(Get-Date -Format o),$($file.Name),done,$seconds,$out" | Add-Content -LiteralPath $Status -Encoding UTF8
    }
    catch {
        $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
        Write-Log "FAILED $($file.Name) seconds=$seconds error=$($_.Exception.Message)"
        "$(Get-Date -Format o),$($file.Name),failed,$seconds,$out" | Add-Content -LiteralPath $Status -Encoding UTF8
    }
}

Write-Log "BATCH END"
