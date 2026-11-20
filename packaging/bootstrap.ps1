# XR Analyze をソース一式から起動する (XRAnalyze.bat から呼ばれる)。
#
# GitHub の Download ZIP を展開しただけの状態でも、ダブルクリックで動くようにする。
# 初回だけ uv (Python と依存の導入ツール) で Python 3.13 と依存一式を自動で取得し、
# 2回目以降はそのまま起動する。理由: docs/adr/0044-zip-bootstrap-launcher.md
#
# 取得したものは %LOCALAPPDATA%\XRAnalyze\ に置く (ZIP の展開先ではなく)。
#   - torch の中はパスが深く、展開先が深いと Windows のパス長 260 文字を超えうる
#   - 新しい ZIP を展開し直しても、数 GB を取り直さずに済む
# 消せば初回からやり直しになる。
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # PS 5.1 は進捗表示があるとダウンロードが極端に遅い
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot           # リポジトリ (ZIP の展開先) のルート
$Rt = Join-Path $env:LOCALAPPDATA "XRAnalyze"
$UvVersion = "0.13.6"
$Uv = Join-Path $Rt "uv\uv.exe"
$Venv = Join-Path $Rt "venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Marker = Join-Path $Rt "installed.txt"

# uv のキャッシュと Python も同じ場所へ。キャッシュと venv が同じドライブにあれば
# uv はハードリンクで済ませるので、数 GB が二重にならない。
$env:UV_CACHE_DIR = Join-Path $Rt "cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Rt "python"

function Invoke-Native([string]$exe, [string[]]$argv) {
    # Windows PowerShell 5.1 は native の stderr をエラー扱いにすることがあるので、
    # 成否は終了コードで見る
    $ErrorActionPreference = "Continue"
    & $exe @argv
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($code -ne 0) { throw "$([IO.Path]::GetFileName($exe)) $($argv -join ' ') が失敗しました (終了コード $code)" }
}

function Get-FileHashText([string]$path) {
    return (Get-FileHash -Algorithm SHA256 $path).Hash
}

try {
    New-Item -ItemType Directory -Force $Rt | Out-Null

    # --- 1. uv ---
    if (-not (Test-Path $Uv)) {
        Write-Host "[setup] 初回の準備をします。Python と依存 (GPU 版は約 3GB) を取得するため、" -ForegroundColor Cyan
        Write-Host "[setup] 回線によって 5〜15 分ほどかかります。2 回目からはすぐ起動します。" -ForegroundColor Cyan
        Write-Host "[setup] uv $UvVersion を取得しています..."
        $zip = Join-Path $Rt "uv.zip"
        $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
        Expand-Archive -Force $zip (Join-Path $Rt "uv")
        Remove-Item $zip
    }

    # --- 2. Python と venv ---
    if (-not (Test-Path $Py)) {
        Write-Host "[setup] Python 3.13 と仮想環境を用意しています..."
        Invoke-Native $Uv @("venv", $Venv, "--python", "3.13", "--python-preference", "only-managed", "--quiet")
    }

    # --- 3. 依存 ---
    # NVIDIA の GPU があれば CUDA 13.0 版の torch (RTX 20〜50 系。ADR-0042)、無ければ CPU 版。
    $hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    $variant = if ($hasNvidia) { "cu130" } else { "cpu" }
    $req = Join-Path $Root "backend\requirements.txt"
    # 依存の一覧か torch の種類が変わったときだけ入れ直す (uv は差分だけ入れるので速い)
    $want = "$variant $(Get-FileHashText $req)"
    $have = if (Test-Path $Marker) { (Get-Content $Marker -Raw).Trim() } else { "" }
    if ($have -ne $want) {
        if ($hasNvidia) {
            Write-Host "[setup] NVIDIA の GPU が見つかりました。GPU 版の torch を入れます (約 3GB)..."
            Invoke-Native $Uv @("pip", "install", "--python", $Py, "torch", "torchvision",
                "--index-url", "https://download.pytorch.org/whl/cu130")
        } else {
            Write-Host "[setup] NVIDIA の GPU が見つからないため、CPU 版の torch を入れます (遅くなります)..."
            Invoke-Native $Uv @("pip", "install", "--python", $Py, "torch", "torchvision")
        }
        Write-Host "[setup] そのほかの依存を入れています..."
        Invoke-Native $Uv @("pip", "install", "--python", $Py, "-r", $req)
        Set-Content -Path $Marker -Value $want -Encoding ASCII
        Write-Host "[setup] 準備ができました。" -ForegroundColor Cyan
    }
} catch {
    Write-Host ""
    Write-Host "[setup] 準備に失敗しました: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "[setup] ネット接続を確かめて、もう一度 XRAnalyze.bat を実行してください。"
    Write-Host "[setup] 直らないときは $Rt を削除してから実行すると、最初からやり直します。"
    Read-Host "Enter キーで閉じます"
    exit 1
}

# --- 4. 起動 ---
# exe と同じ入口 (packaging/launcher.py) を使う。ログ (XRAnalyze.log) と、エラー時に
# Enter を待ってから閉じる動作もそちらが受け持つ。
Set-Location $Root
$ErrorActionPreference = "Continue"
& $Py (Join-Path $Root "packaging\launcher.py") --open-browser @AppArgs
exit $LASTEXITCODE
