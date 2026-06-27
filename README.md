# XR Analyze

InnoMaker U20CAM-1080P-S1 (UVC USBカメラ) からリアルタイムで映像を取得し、
YOLOで物体検出して結果を配信するバックエンド。Mac / Windows 両対応。

検出結果は **JSON over WebSocket** という、クライアント非依存の契約で出します。
Web・Unity・Android のどれからでも同じデータを読めるので、将来Unity/Androidへ
移行してもバックエンドはそのまま使えます。

## フォルダ構成

```
XR_ Analyze/
├── backend/            Python: カメラ取得 + YOLO検出 + 配信
│   ├── config.py       設定(環境変数で上書き可)
│   ├── camera.py       クロスプラットフォームのカメラ取得 (Mac/Win/Linux自動切替)
│   ├── detector.py     YOLO推論 → 正規化座標のJSON
│   ├── pipeline.py     背景スレッドで取得+推論し最新結果を保持
│   ├── server.py       FastAPI: /ws (JSON), /video (MJPEG), /healthz
│   ├── check_camera.py サーバ無しで映るか確認するスタンドアロン
│   └── requirements.txt
├── frontend/           Web: 黒背景で検出枠を描画(ARの叩き台)
│   └── index.html
└── README.md
```

> 構成判断: バックエンド/フロントは検出座標とWebSocket仕様を共有するため、
> 同じフォルダ(モノレポ)に置きつつ `backend/` `frontend/` に分離するのが最適。
> `backend/` はクライアント非依存なので、フロントがWeb→Unityに替わっても不変です。

## セットアップ

Python 3.9+ を推奨。

```bash
cd "XR_ Analyze"
python3 -m venv .venv
# Mac/Linux:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

pip install -r backend/requirements.txt
```

初回実行時、YOLOモデル (`yolov8n.pt`) は自動ダウンロードされます。

## 使い方

### 0. どのカメラが使われるか確認 (内蔵 vs InnoMaker)

OpenCV のカメラ index の並び順は環境依存で、**内蔵が index 0 とは限りません**
(実機で InnoMaker=index 0, 内蔵=index 1 のケースを確認)。

既定で **名前一致 `CAM_NAME=Innomaker` により外付けを自動選択**します。これを
macOSで正しく機能させるには `pyobjc-framework-AVFoundation` (requirements.txt 同梱)
が必要です。これで OpenCV と同じ並び順のカメラ名を取得して名前一致できます。
未導入時は名前選択が効かないため `CAM_INDEX` を明示してください。

確認:

```bash
python3 -m backend.list_cameras
```

AVFoundation(=OpenCVのindex順)のカメラ名・各 index の実解像度・選ばれる番号を表示。
最終確認は check_camera で実映像を見るのが確実です:

```bash
CAM_INDEX=0 python3 -m backend.check_camera --raw
CAM_INDEX=1 python3 -m backend.check_camera --raw
```

InnoMaker が映った番号を `CAM_INDEX` に指定すれば確実に固定できます。

### 1. まずカメラが映るか確認 (推奨)

```bash
python3 -m backend.check_camera          # YOLO検出つき
python3 -m backend.check_camera --raw    # 素の映像のみ
```

ウィンドウが開きます。`q` または `ESC` で終了。起動時に「どのカメラを選んだか」を
ログ表示します。意図と違う場合は `CAM_INDEX` を指定して再試行 (下記)。

### 2. サーバ起動

```bash
python3 -m backend.server
```

- ブラウザで `http://127.0.0.1:8100/` → 黒背景に検出枠 + デバッグ映像
  - `d` キーで映像のON/OFF (OFFにすると黒背景+枠だけ = AR表示の叩き台)
- `http://127.0.0.1:8100/video` → 注釈付きMJPEGを直接表示
- `http://127.0.0.1:8100/healthz` → 稼働確認 (fpsなど)

## WebSocket の出力フォーマット (クライアント非依存の契約)

`/ws` が新フレームごとに送るJSON:

```json
{
  "frame_id": 1234,
  "ts": 1750000000.12,
  "fps": 14.8,
  "source_size": { "w": 1280, "h": 720 },
  "detections": [
    {
      "label": "cup", "class_id": 41, "confidence": 0.87,
      "x": 0.31, "y": 0.42, "w": 0.12, "h": 0.18
    }
  ]
}
```

- 座標 `x,y,w,h` は **0.0–1.0 の正規化値**。原点は左上、x右/y下が正。
  表示側の解像度に依存しないので、Unity/Androidでもそのまま使えます。

## 設定 (環境変数で上書き)

| 変数 | 既定 | 説明 |
|------|------|------|
| `CAM_NAME` | Innomaker | この名前を含むカメラを自動選択(部分一致・macOS)。空で無効 |
| `CAM_INDEX` | (未指定) | USBカメラ番号。**指定すると名前選択より優先**。内蔵は0、外付けは1など |
| `CAM_WIDTH` / `CAM_HEIGHT` | 1280 / 720 | 取得解像度 |
| `MODEL` | yolov8n.pt | 検出モデル。下記「認識できる物体を増やす」参照 |
| `CLASSES` | (未指定) | オープン語彙モデルで検出する物体名(カンマ区切り)。例: `wallet,watch,ring` |
| `CONF_THRES` | 0.35 | 信頼度しきい値 |
| `IMG_SIZE` | 640 | 推論サイズ(小さいほど速い) |
| `DEVICE` | (自動) | `cpu` / `cuda` / `mps`。**Apple Siliconでは自動でmps(GPU)** |
| `STREAM_FPS` | 15 | WebSocket/MJPEGの配信fps上限 |
| `PORT` | 8100 | サーバポート |

> Apple Silicon(M1〜)では `DEVICE` を指定しなくても自動で `mps`(GPU)が有効に
> なります。明示的にCPUを使いたい場合のみ `DEVICE=cpu` を指定してください。

例 (別カメラ・高精度モデルで起動):

```bash
CAM_INDEX=1 MODEL=yolov8s.pt python3 -m backend.server
```

## 認識できる物体を増やす

認識できる種類は**モデル**で決まります。用途に応じて `MODEL` を切り替えてください。

### A) モノの名前を幅広く知りたい(専門性不要・推奨)

オープン語彙の「プロンプトフリー」モデルに切り替えます。LVIS等 **1200以上の語彙**で、
クラスを指定せずに見えたモノへ名前を付けます。

```bash
MODEL=yoloe-11s-seg-pf.pt python3 -m backend.server
```

- 初回はモデルを自動ダウンロード。`-pf` がプロンプトフリーの印。
- ラベルは英語(例: `cup`, `wallet`, `keyboard`)。重い場合は速度重視で
  `IMG_SIZE=480` などを併用。

### B) 検出したい物体を自分で挙げる(テキスト指定)

オープン語彙モデル + `CLASSES` で、挙げた物体だけを検出します。

```bash
MODEL=yoloe-11s-seg.pt CLASSES="wallet,watch,ring,coin,trading card" \
  python3 -m backend.server
```

- `CLASSES` に無い物体は出ません。狙った物だけ拾いたいときに有効。
- テキスト指定モードは初回にテキストエンコーダ(CLIP系)を自動取得する場合があります。

### C) 既定(最軽量)

`MODEL` 未指定なら `yolov8n.pt`(COCO 80カテゴリ)。最速・最小です。

> 補足: いずれも Apple Silicon では自動で `mps`(GPU)が使われます。オープン語彙
> モデルは標準YOLOより重いので、まず `yoloe-11s-...`(s=small)から試すのが無難です。

## メモ / 既知の制約

- 既定の COCO 学習済みモデルは約80カテゴリ。上記Aのオープン語彙モデルなら1200+に拡張。
- いずれも一般物体の認識です。「鑑定」固有の特定個体(特定商品・カード等)の判別には
  独自データでの追加学習(fine-tune)が別途必要です。
- macOSでは初回にカメラ使用許可のダイアログが出ます。許可してください。
- AR(XREAL Air2Pro)での現実への重ね合わせは、カメラ画角と視界の座標対応が
  別途必要です(memoryの未決事項)。本リポジトリは検出基盤までを提供します。
