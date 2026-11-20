# XR Analyze の Windows 向け exe をビルドする。
#
#   .\build_exe.ps1                  # dist\XRAnalyze\XRAnalyze.exe を作る
#   .\build_exe.ps1 -IncludeModels   # 手元の .pt も同梱する (配布先でダウンロード不要)
#
# 事前準備 (docs/windows.md):
#   python -m venv .venv
#   .venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
#   .venv\Scripts\pip install -r backend\requirements-build.txt
#
# 構成の理由: docs/adr/0042-windows-exe-packaging.md
param(
    [string]$Python = ".venv\Scripts\python.exe",
    [switch]$IncludeModels
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path $Python)) {
    throw "$Python が見つかりません。先に venv を作ってください (docs/windows.md)。"
}

# CPU 版 torch のままビルドすると、GPU のある PC でも CPU 推論になる。気づけるよう止める。
$torchInfo = & $Python -c "import torch; print(torch.__version__, torch.version.cuda or 'cpu')"
if ($LASTEXITCODE -ne 0) { throw "torch を import できません。" }
Write-Host "[build] torch: $torchInfo"
if ($torchInfo -match " cpu$") {
    throw "CPU 版の torch が入っています。CUDA 版を入れてからビルドしてください (docs/windows.md)。"
}

$out = "dist\XRAnalyze"
if (Get-Process XRAnalyze -ErrorAction SilentlyContinue) {
    throw "XRAnalyze.exe が起動中です。閉じてからビルドしてください。"
}

# PyInstaller は dist\XRAnalyze を丸ごと消して作り直す。exe の隣に溜まった利用者の
# データ (調整値・キャッシュ・ダウンロードしたモデル・ログ) は退避して戻す。
$keepNames = @("calib.json", "explain_cache.json", "speak_cache", "*.pt", "XRAnalyze*.log")
$keep = "build\_keep"
if (Test-Path $keep) { Remove-Item $keep -Recurse -Force }
New-Item -ItemType Directory -Force $keep | Out-Null
if (Test-Path $out) {
    foreach ($name in $keepNames) {
        Get-ChildItem (Join-Path $out $name) -ErrorAction SilentlyContinue |
            ForEach-Object { Move-Item $_.FullName $keep }
    }
}

# PyInstaller は進捗を stderr に出す。Windows PowerShell 5.1 は出力をリダイレクト
# していると stderr の行をエラー扱いにし、Stop だとそこで止まるので、成否は終了コードで見る。
$ErrorActionPreference = "Continue"
& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\XRAnalyze.spec
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($code -ne 0) {
    throw "PyInstaller が失敗しました (終了コード $code)。退避したデータは $keep にあります。"
}

# 書き換えるデータは exe の隣に置く (backend/paths.py の DATA_DIR)。
# リポジトリにある鑑定文・読み上げ音声のキャッシュと調整値は初回から使えるよう同梱する
# (docs/adr/0038-explain-persistent-cache.md)。
# 退避したものがあればそちらを優先して戻す (使ううちに増えたキャッシュや調整値を消さないため)。
Copy-Item explain_cache.json $out -Force
if (Test-Path speak_cache) { Copy-Item speak_cache $out -Recurse -Force }
if (Test-Path calib.json) { Copy-Item calib.json $out -Force }
Get-ChildItem $keep | ForEach-Object {
    Write-Host "[build] 前回のデータを戻す: $($_.Name)"
    $dest = Join-Path $out $_.Name
    if ($_.PSIsContainer -and (Test-Path $dest)) {
        # Move-Item はフォルダを既存のフォルダへ統合できないので、中身を上書きコピーする。
        # speak_cache はリポジトリの分と前回の分を合わせたものになる。
        Copy-Item (Join-Path $_.FullName "*") $dest -Recurse -Force
        Remove-Item $_.FullName -Recurse -Force
    } else {
        Move-Item $_.FullName $out -Force
    }
}
Remove-Item $keep -Recurse -Force
Copy-Item packaging\README_exe.txt (Join-Path $out "README.txt") -Force
if ($IncludeModels) {
    Get-ChildItem *.pt | ForEach-Object {
        Write-Host "[build] モデルを同梱: $($_.Name)"
        Copy-Item $_.FullName $out -Force
    }
}

$size = (Get-ChildItem $out -Recurse | Measure-Object -Property Length -Sum).Sum / 1GB
Write-Host ("[build] 完成: {0}\XRAnalyze.exe  (フォルダ全体 {1:N1} GB)" -f $out, $size)
