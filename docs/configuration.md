# 設定 (環境変数で上書き)

サーバの挙動を上書きできる環境変数の一覧。カメラ・検出モデル・鑑定文生成・
読み上げなど、各機能に対応する変数をここにまとめる。

設定の解決順序 (環境変数 > `calib.json` > 既定値) は
[ADR-0004](adr/0004-config-resolution-order.md) を参照してください。

| 変数 | 既定 | 説明 |
|------|------|------|
| `CAM_NAME` | Innomaker | この名前を含むカメラを自動選択(部分一致・macOS)。空で無効 |
| `CAM_INDEX` | (未指定) | USBカメラ番号。**指定すると名前選択より優先**。内蔵は0、外付けは1など |
| `CAM_WIDTH` / `CAM_HEIGHT` | 1280 / 720 | 取得解像度 |
| `CAM_FLIP` | 180 | 映像の向き補正。`180`(上下+左右) / `v`(上下) / `h`(左右) / `none`(補正なし) |
| `CAM_ZOOM` | 1.0 | デジタルズーム倍率(中央クロップ)。1.0で等倍、1.5なら幅・高さを1/1.5に中央クロップ。1.0未満は無効(1.0にクランプ)。**指定すると `calib.json` の保存値より優先** |
| `CAM_OFFSET_X` / `CAM_OFFSET_Y` | 0.0 / 0.0 | 切り抜き中心のオフセット。フレーム幅/高さに対する正規化値(0.1=幅の10%右へ、yは+で下へ)。**指定すると保存値より優先** |
| `CALIB_FILE` | ./calib.json | 合わせ込み値(ズーム/位置)の保存先。ブラウザで `s` を押すと書き込まれ、次回起動時に自動で読まれる |
| `MODEL` | yolov8n.pt | 検出モデル。detection.md「認識できる物体を増やす」参照 |
| `CLASSES` | (未指定) | オープン語彙モデルで検出する物体名(カンマ区切り)。例: `wallet,watch,ring` |
| `MAX_DET` | 15 | 一度に表示する検出数の上限(信頼度上位N件)。0以下で無制限 |
| `CONF_THRES` | 0.35 | 信頼度しきい値 |
| `IMG_SIZE` | 640 | 推論サイズ(小さいほど速い) |
| `FAST_INFER` | 1 | マスク生成を省いた高速推論経路を使う。`0` で ultralytics の `predict()` に戻す |
| `DEDUP_IOU` | 0.85 | 同一物体に別ラベルの枠が重なったとき、信頼度の高い方だけ残すIoU閾値。0以下で無効 |
| `VOCAB` | auto | 検出語彙の絞り込み。`auto`(語彙の大きいモデルだけLVISで絞る) / `lvis` / `all`(絞らない) / 1行1語のファイルパス |
| `DEVICE` | (自動) | `cpu` / `cuda` / `mps`。**Apple Siliconでは自動でmps(GPU)** |
| `STREAM_FPS` | 15 | WebSocket/MJPEGの配信fps上限 |
| `JP_FONT` | (自動) | MJPEGの日本語描画フォント。自動検出に失敗する時だけ .ttc/.ttf を指定 |
| `PORT` | 8100 | サーバポート |
| `OLLAMA_URL` | http://localhost:11434 | AI説明(voice.md「固定した物体の AI 説明」)で呼ぶ Ollama のアドレス |
| `OLLAMA_MODEL` | gemma4:e4b | AI説明の生成に使うモデル。`ollama pull` 済みのモデル名 |
| `EXPLAIN_TIMEOUT_SEC` | 30.0 | AI説明の生成完了を待つ上限秒。初回生成は実測約7.5秒 |
| `TTS_BACKEND` | voicevox | 鑑定文の読み上げ方式。`voicevox`(VOICEVOX ENGINE) / `say`(macOS同梱コマンド) / `off`(無効) |
| `VOICEVOX_URL` | http://localhost:50021 | VOICEVOX ENGINE の接続先 |
| `VOICEVOX_SPEAKER` | 82 | VOICEVOX の話者ID。既定は「青山龍星」のスタイル「不機嫌」。`GET /speakers` で一覧できる |
| `VOICEVOX_SPEED` | 1.0 | VOICEVOX の話速(speedScale)。1.0が標準、小さいほど遅い |
| `VOICEVOX_PITCH` | -0.05 | VOICEVOX の音高(pitchScale)。0.0が標準、負で低い声になる |
| `VOICEVOX_INTONATION` | 1.0 | VOICEVOX の抑揚(intonationScale)。1.0が標準 |
| `TTS_VOICE` | Grandpa | (say専用)読み上げの声。`say -v '?'` で導入済みの声を一覧できる |
| `TTS_RATE` | 130 | (say専用)読み上げの話速(words/min相当)。say の既定175より遅く、重々しく読ませる |
| `TTS_TIMEOUT_SEC` | 20.0 | 読み上げの合成完了を待つ上限秒(say/voicevox共通)。実測は1件あたり voicevox 1.3〜2.9秒 / say 約0.5秒 |

> Apple Silicon(M1〜)では `DEVICE` を指定しなくても自動で `mps`(GPU)が有効に
> なります。明示的にCPUを使いたい場合のみ `DEVICE=cpu` を指定してください。
> ([ADR-0009](adr/0009-inference-device-selection.md))

> カメラを逆さに取り付けている前提で、既定で映像を180°回転させています。向きが
> おかしい場合は `CAM_FLIP` を変更してください(`CAM_FLIP=none` で補正なし)。
> ([ADR-0007](adr/0007-viewport-crop-calibration.md))
