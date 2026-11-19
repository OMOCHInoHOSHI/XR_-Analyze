# Windows で使う

Windows では、(A) ビルド済みの exe をダブルクリックで起動する方法と、
(B) macOS と同じく venv から起動する方法があります。exe を作る手順は (C) にあります。

## A. exe で起動する

`dist\XRAnalyze\` フォルダ (配布された zip を展開したもの) の `XRAnalyze.exe` を
ダブルクリックします。

1. コンソール窓が開き、少しするとブラウザで**起動画面**が開く
   (開かなければ `http://127.0.0.1:8100/` を開く)
2. 使うカメラをドロップダウンから選ぶ。名前・解像度と、下に出る縮小画像で、どのカメラかを見分けられる
3. 検出モデルを選ぶ。「初回にダウンロード」のものは開始時に自動で取得する
   (ネット接続が必要。exe と同じフォルダに保存される)
4. 「この設定で開始」(Enter) を押すと検出が始まり、いつもの画面に切り替わる

終了はコンソール窓を閉じるか、窓の中で `Ctrl+C`。

- **GPU**: NVIDIA の GPU (RTX 20 系以降) があれば自動で使います。起動画面の上の
  「推論デバイス」が `cuda` なら GPU、`cpu` なら CPU です。GPU があるのに `cpu` に
  なるときは、NVIDIA のドライバを最新 (580 以降) にしてください。
- **鑑定文と読み上げ**: Ollama と VOICEVOX を別に入れて起動しておく必要があります
  ([voice.md](voice.md))。無くても検出は動きます。
- **保存されるもの**: `calib.json` (調整値)・`explain_cache.json` (鑑定文)・
  `speak_cache\` (読み上げ音声)・ダウンロードした `.pt` は、すべて exe と同じフォルダに
  置かれます。フォルダごと移動・コピーできます。
- **ログ**: コンソールの出力は exe と同じフォルダの `XRAnalyze.log` にも書かれます
  (前回の分は `XRAnalyze.prev.log`)。落ちて窓が消えたときも、ここに最後の出力と、
  ネイティブコードで落ちた場合はその時点のスタックが残ります。
- 起動画面を出さずに始めるには、コマンドプロンプトから `XRAnalyze.exe --no-select`
  (環境変数 `CAM_INDEX` / `MODEL` などの設定で、すぐ検出を始めます)。

## B. venv から起動する (開発用)

Python 3.9 以降 (動作確認は 3.13) を入れておきます。PowerShell で:

```powershell
cd "XR_ Analyze"
python -m venv .venv
.venv\Scripts\Activate.ps1
# GPU を使うために CUDA 版の torch を先に入れる (PyPI の torch は Windows では CPU 版)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
pip install -r backend\requirements.txt
python -m backend.server
```

`Activate.ps1` が実行ポリシーで止められる場合は
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` を一度実行するか、
有効化せずに `.venv\Scripts\python -m backend.server` で起動します。

CUDA 版が入ったかは次で確かめられます (`True` なら GPU が使える)。

```powershell
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

環境変数は PowerShell では `$env:CAM_INDEX = "1"` のように設定してから起動します。

## C. exe をビルドする

B の venv に、ビルド用の依存を足してからスクリプトを実行します。

```powershell
.venv\Scripts\pip install -r backend\requirements-build.txt
.\build_exe.ps1                  # dist\XRAnalyze\XRAnalyze.exe ができる
.\build_exe.ps1 -IncludeModels   # 手元の .pt も同梱する (配布先でダウンロード不要になる)
```

- CPU 版の torch が入っているとビルドを止めます (GPU のある PC でも CPU 推論になるため)。
- できあがる `dist\XRAnalyze\` は数 GB あります (大半は CUDA 版 torch)。配布は
  フォルダごと zip にします。
- 単一の exe ではなくフォルダ形式にした理由、torch に CUDA 13.0 版を選んだ理由
  (RTX 20〜50 系を一つで覆える) などは [ADR-0042](adr/0042-windows-exe-packaging.md)。
