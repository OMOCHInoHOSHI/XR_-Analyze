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

# PyInstaller は進捗を stderr に出す。Windows PowerShell 5.1 は出力をリダイレクト
# していると stderr の行をエラー扱いにし、Stop だとそこで止まるので、成否は終了コードで見る。
$ErrorActionPreference = "Continue"
& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\XRAnalyze.spec
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($code -ne 0) { throw "PyInstaller が失敗しました (終了コード $code)。" }

$out = "dist\XRAnalyze"
# 書き換えるデータは exe の隣に置く (backend/paths.py の DATA_DIR)。
# 事前生成した鑑定文は初回から使えるよう同梱する (docs/adr/0038-explain-persistent-cache.md)。
Copy-Item explain_cache.json $out -Force
Copy-Item packaging\README_exe.txt (Join-Path $out "README.txt") -Force
if ($IncludeModels) {
    Get-ChildItem *.pt | ForEach-Object {
        Write-Host "[build] モデルを同梱: $($_.Name)"
        Copy-Item $_.FullName $out -Force
    }
}

$size = (Get-ChildItem $out -Recurse | Measure-Object -Property Length -Sum).Sum / 1GB
Write-Host ("[build] 完成: {0}\XRAnalyze.exe  (フォルダ全体 {1:N1} GB)" -f $out, $size)
