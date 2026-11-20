# ADR-0044: Download ZIP からは exe でなく起動用 .bat で動かし、依存は初回に取得する

- ステータス: 承認済み
- 記録日: 2026-11-20
- 関連: [ADR-0042](0042-windows-exe-packaging.md), [ADR-0043](0043-startup-selection-screen.md)

## 文脈

GitHub の develop ブランチを **Download ZIP** して展開しただけで、すぐ起動できるようにしたい。
develop は「Windows へコピーしてそのまま使う」ためのブランチで、キャッシュやモデルも
コミットしてある。

最初に考えたのは、ビルドした exe のフォルダ ([ADR-0042](0042-windows-exe-packaging.md))
をそのまま develop に入れることだった。しかし次の理由で成り立たない (2026-11-20 に実測)。

- exe のフォルダは約 3.2GB、ファイル数は約 5000。
- 100MB を超えるファイルが 10 個ある (`cublasLt64_13.dll` 478MB、`torch_cuda.dll` 423MB、
  `torch_cpu.dll` 306MB など。CUDA 版 torch の DLL)。GitHub は 100MB を超えるファイルの
  push を拒否する。
- Git LFS に載せれば push はできる。ただし Download ZIP に LFS の実体を含めるには
  リポジトリ設定が要り、ダウンロードのたびに約 3GB の LFS 帯域を使う。ビルドし直すたびに
  LFS の容量も約 3GB ずつ増える。

## 決定

- リポジトリ直下に **`XRAnalyze.bat`** を置く。ダブルクリックすると
  `packaging/bootstrap.ps1` が次を行う。
  1. 初回だけ、uv (Python と依存の導入ツール。単体の exe) を GitHub のリリースから取得する。
     版は固定する。
  2. uv で Python 3.13 と仮想環境を用意し、torch を入れる。`nvidia-smi` があれば
     CUDA 13.0 版 (ADR-0042 と同じ理由)、無ければ CPU 版。続けて `backend/requirements.txt` を入れる。
  3. `packaging/launcher.py` (exe と同じ入口) でサーバを起動し、ブラウザで起動画面を開く。
- 取得したものは `%LOCALAPPDATA%\XRAnalyze\` に置く。uv のキャッシュも同じ場所にして、
  ハードリンクで二重持ちを避ける。
- 依存を入れ直すのは、`requirements.txt` の中身か torch の種類 (cu130 / cpu) が
  変わったときだけ。記録は `installed.txt` に残す。
- exe ([ADR-0042](0042-windows-exe-packaging.md)) はそのまま残す。手元でビルドして、
  フォルダごと渡す配り方には引き続き使える。

## 理由

- **リポジトリを軽く保てる**: 実体の DLL を git に入れないので、clone も ZIP も今の大きさの
  まま。LFS の帯域や容量の上限も気にしなくてよい。
- **Python の事前インストールが要らない**: uv は単体の exe で、Python 本体も自分で取得する。
  ZIP を展開した人に、Python の導入や PATH の設定を求めずに済む。
- **展開先に置かない**: torch の中は site-packages からの相対で最長 129 文字のパスがある。
  ダウンロードフォルダの深いところへ展開すると、Windows のパス長 260 文字に近づく。
  `%LOCALAPPDATA%` 直下なら余裕がある。さらに、新しい ZIP を展開し直しても数 GB を
  取り直さずに済む。
- **.bat は ASCII だけで書く**: `cmd.exe` はバッチを OEM コードページで読むので、UTF-8 の
  日本語があると行が壊れることがある。日本語の案内は BOM 付き UTF-8 の .ps1 側に置く。
  PowerShell の実行ポリシーは `-ExecutionPolicy Bypass` で、この起動に限って外す。
- **代替案**: GitHub Releases に exe の zip を置く案は、1 ファイル 2GB の上限で分割が要る。
  そのうえ「develop の Download ZIP で動く」という要件を満たさない。exe を CPU 版にして
  小さくする案は、100MB 超の DLL (`torch_cpu.dll` 306MB) が残るうえ、GPU を使えなくなる。

## 結果

- develop の Download ZIP を展開して `XRAnalyze.bat` をダブルクリックすれば起動する。
  初回は依存の取得に 5〜15 分かかる (GPU 版で約 3GB)。2 回目からはすぐ起動する。
- 初回はネット接続が必須。オフラインの PC で使うには、従来どおり exe のフォルダを渡す。
- インターネットから落とした .bat なので、初回に Windows の確認
  (SmartScreen / 発行元の警告) が出ることがある。
- ログ・調整値・キャッシュは ZIP を展開したフォルダ (リポジトリのルート) に置かれる。
  開発時と同じ置き場所 (`backend/paths.py` の DATA_DIR)。
