# API (エンドポイント一覧・WebSocket契約)

`backend/server.py` が提供する HTTP/WebSocket エンドポイントの一覧と、
クライアント非依存の契約である `/ws` の出力フォーマットをまとめる。

## エンドポイント一覧

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/` | フロント(`frontend/index.html`)。検出を始める前は起動画面(`frontend/setup.html`) |
| GET | `/setup` | 起動画面 (カメラと検出モデルを選んで検出を始める) |
| GET | `/setup/options` | 接続中のカメラ・使える検出モデルの一覧と初期選択。`?refresh=1` でカメラを調べ直す。検出の稼働中・立ち上げ中は 409 |
| GET | `/setup/thumb/{index}` | 一覧を作ったときに撮ったカメラの縮小画像 (JPEG) |
| POST | `/setup/start` | `{"cam_index": 0, "model": "yoloe-11l-seg-pf.pt"}` で検出を始める。既に立ち上げ中・稼働中なら 409 |
| GET | `/setup/status` | 立ち上げの進み具合。`state` は `selecting` / `starting` / `running` / `error` (`message` に理由) |
| GET | `/healthz` | 稼働確認 + 実測fps + カメラの状態 (`camera_status`)。検出を始める前は 503 で `setup` に起動画面の状態 |
| WS | `/ws` | 検出結果JSONを `STREAM_FPS` で配信 (Web/Unity/Android共通の契約) |
| GET | `/video` | 注釈付きMJPEG (デバッグ確認用。ブラウザで直接開ける)。`?annotate=0` で枠を焼き込まない素の映像 (フロントはこちらを使う) |
| POST | `/calib` | 稼働中のズーム/切り抜き位置の調整 (グラスの視界合わせ) |
| POST | `/calib/save` | 現在の調整値をファイルへ保存 (次回起動時に自動で復元) |
| POST | `/explain` | 物体ラベルから日本語の説明文を生成 (Ollama のローカルLLM) |
| POST | `/speak` | 鑑定文の読み上げ音声(WAV)を返す (VOICEVOX または macOS の `say`) |
| WS | `/voice` | 音声コマンド: マイクの音量と聞き取った言葉を約30回/秒で配信。最後に開いた接続にだけ送る (下記) |
| POST | `/shutdown` | アプリを終了する (音声の「終了」)。この PC (ループバック) からの要求だけ受け付け、他は 403 |

`/video` は単体で開く分は既定 (注釈付き) のままでよく、枠が二重になることはありません。
トップページが `?annotate=0` を使うのは、枠をページ側で描くためです。
`/healthz` はブラウザで開けば fps や `camera_status` などの稼働状況をそのまま読めます。

起動画面 (`/setup/*`) の流れと理由は [ADR-0043](adr/0043-startup-selection-screen.md)。
`--no-select` (または `SETUP=0`) で起動した場合、検出は起動と同時に始まり、`/setup/status` は
最初から `running` を返します。

## 音声コマンドの配信 (`/voice`)

約30回/秒 (`backend/voice.py` の `LEVEL_HZ`) で次の JSON を送ります。言葉を聞き取ったときは
`cmd` と `word` が付いた分が1つ増えます。つないだ時点より前に聞き取った言葉は送りません。

```json
{"state": "listening", "level": 0.42}
{"state": "listening", "level": 0.42, "cmd": "appraise", "word": "鑑定"}
```

| キー | 値 |
|---|---|
| `state` | `listening` (聞き取り中) / `loading` (モデルの読み込み・取得中) / `off` (`VOICE=0`) / `nomic` (マイクが無い・開けない) / `nomodel` (モデルが無く取得もできない) / `elsewhere` (もっと新しい接続がある。この接続には `cmd` を送らない) |
| `level` | マイクの音量 0〜1 (-60〜-10 dBFS を dB で直線に対応) |
| `cmd` | `mode` / `single` / `multi` / `appraise` / `close` / `quit` |
| `word` | 表示用の聞き取った言葉 (例: `モード変更`、`単体鑑定`、`に`) |

`cmd` を今の画面でどう扱うかはクライアントが決めます。トップページの扱いは
[voice-recognizer.md](voice-recognizer.md)、理由は [ADR-0045](adr/0045-voice-commands.md)。

## WebSocket の出力フォーマット (クライアント非依存の契約)

`/ws` が新フレームごとに送るJSON:

```json
{
  "frame_id": 1234,
  "ts": 1750000000.12,
  "fps": 14.8,
  "source_size": { "w": 1280, "h": 720 },
  "view": { "zoom": 1.0, "offset_x": 0.0, "offset_y": 0.0 },
  "camera_status": "ok",
  "detections": [
    {
      "label": "cup", "class_id": 41, "confidence": 0.87,
      "x": 0.31, "y": 0.42, "w": 0.12, "h": 0.18
    }
  ]
}
```

- 座標 `x,y,w,h` は **0.0–1.0 の正規化値**。原点は左上、x右/y下が正
  ([ADR-0002](adr/0002-detection-streaming-contract.md))。
- `view` は現在のズーム/切り抜き位置(camera.md のライブ調整の確定値)。座標は切り抜き後の
  フレーム基準で正規化されるため、クライアント側で補正する必要はありません。
- `label` は英語([ADR-0003](adr/0003-label-localization.md))、`label_ja` は日本語表示名です。
  辞書(`backend/labels_ja.py`)に無い語は `label_ja` も英語のままになります。
- `camera_status` はカメラの稼働状態。通常は `"ok"`、切断を検知して再接続を
  試みている間は `"reconnecting"` になります。切断中は `detections` が空で、
  `/video` には「カメラを再接続しています...」のプレースホルダー映像が流れます。
  後から追加したフィールドであり、既存のフィールドは不変のため、旧クライアントも
  そのまま動きます([ADR-0035](adr/0035-camera-auto-reconnect.md))。

> 表示: ブラウザのオーバーレイ枠と MJPEG映像は、検出名を**日本語・大きめ**で
> 表示します。MJPEGの日本語描画には日本語フォントが必要で、macOS/Windowsでは
> 自動検出します。文字化け(□)する場合のみ `JP_FONT` でフォントを指定してください。
