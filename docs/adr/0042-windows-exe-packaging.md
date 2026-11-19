# ADR-0042: Windows 向けに PyInstaller のフォルダ形式で exe を作り、CUDA 版 torch を同梱する

- ステータス: 承認済み
- 記録日: 2026-11-19
- 関連: [ADR-0008](0008-calibration-persistence.md), [ADR-0009](0009-inference-device-selection.md), [ADR-0038](0038-explain-persistent-cache.md), [ADR-0039](0039-speak-persistent-cache.md), [ADR-0041](0041-windows-camera-support.md), [ADR-0043](0043-startup-selection-screen.md)

## 文脈

起動するには Python・venv・依存の導入と、ターミナルでの `python -m backend.server`
が必要だった。Python 環境を用意しない人 (主にゲーミングノート PC の Windows 利用者)
でも、ダブルクリックで起動できるようにしたい。

制約:

- 推論には GPU が要る。`yoloe-11l-seg-pf.pt` は CPU では実用的な fps が出ない。
  PyPI の `torch` は Windows では CPU 版なので、CUDA 版を別の索引から入れる必要がある。
- ゲーミングノートの GPU は RTX 20〜50 系にわたる。CUDA 12.6 版の torch は RTX 50 系
  (Blackwell, sm_120) に対応しておらず、CUDA 12.8 版は torch 2.11 で更新が止まっている。
  CUDA 13.0 版 (cu130) は Turing (RTX 20) 以降をすべて含む。
- 保存値 (`calib.json`)・キャッシュ (`explain_cache.json`, `speak_cache/`)・
  ダウンロードした `.pt` は、これまで `backend/` の親 (プロジェクトルート) に置いていた。
  exe ではそこが展開先の `_internal/` になる。

## 決定

- **PyInstaller のフォルダ形式 (onedir)** で `dist/XRAnalyze/XRAnalyze.exe` を作る。
  手順は `build_exe.ps1` (設定は `packaging/XRAnalyze.spec`、入口は `packaging/launcher.py`)。
- 同梱する torch は **CUDA 13.0 版** (`--index-url https://download.pytorch.org/whl/cu130`)。
  GPU が使えない PC では torch が CUDA 無しと判断し、既存の自動判定
  ([ADR-0009](0009-inference-device-selection.md)) で CPU に落ちる。
  CPU 版の torch のままビルドしようとしたら、`build_exe.ps1` が止める。
- 置き場所を `backend/paths.py` で二つに分ける。
  - `RESOURCE_DIR` (読むだけ): `frontend/`・`ja_vocab.json`・ultralytics の yaml。exe では `_internal/`。
  - `DATA_DIR` (書く・増える): `calib.json`・`explain_cache.json`・`speak_cache/`・`.pt`。
    **exe の隣**。開発時はどちらもプロジェクトルートで、従来と変わらない。
- exe は起動時にカレントディレクトリを `DATA_DIR` へ移す (ultralytics は名前だけで
  指定したモデルをカレントへダウンロードするため)。
- コンソール窓を残す。サーバが接続を受け付け始めたらブラウザを自動で開く
  (`OPEN_BROWSER`。exe では既定で有効、開発時は無効)。エラーで終わったときは
  Enter を待ってから閉じる。

## 理由

- **フォルダ形式にした (単一 exe でなく)**: 単一 exe は起動のたびに中身を一時
  フォルダへ展開する。CUDA 版 torch を含むと数 GB になり、毎回の起動に数十秒かかる。
  フォルダ形式なら展開が無く、配布はフォルダごと zip にすれば済む。
- **cu130 にした**: 想定する GPU (RTX 20〜50 系) を一つのビルドで覆える唯一の版。
  ドライバは 580 以降が要るが、古いドライバでも落ちずに CPU で動くので、
  「更新すれば速くなる」と案内できる。
- **書くものを exe の隣に置いた (`_internal/` の中でなく)**: 新しいビルドで
  フォルダを差し替えても保存値とキャッシュを残せる。利用者が場所を見つけやすい。
  `%APPDATA%` に置く案は、フォルダごと別の PC へ持ち運ぶ使い方と合わないので採らなかった。
- **コンソールを残した**: 推論 fps・カメラの切断などのログがそのまま読め、窓を
  閉じれば終了する、という分かりやすさを優先した。
- **UPX を使わない**: torch の DLL を UPX で縮めると読み込めなくなることがある。

## 結果

- Python を入れていない PC でも、フォルダをコピーして `XRAnalyze.exe` を
  ダブルクリックすれば起動画面まで届く。
- 配布サイズは数 GB になる (大半は CUDA 版 torch の DLL)。
- 鑑定文と読み上げには、従来どおり Ollama と VOICEVOX を別に入れる必要がある
  (exe には含めない)。
- `CLASSES` によるテキスト指定 (YOLOE の `set_classes`) は、ultralytics が実行時に
  追加パッケージを pip で入れようとすることがあり、exe では使えない場合がある。
- macOS 版の配布物は作っていない (macOS は従来どおり venv から起動する)。
