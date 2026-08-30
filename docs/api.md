# API (エンドポイント一覧・WebSocket契約)

`backend/server.py` が提供する HTTP/WebSocket エンドポイントの一覧と、
クライアント非依存の契約である `/ws` の出力フォーマットをまとめる。

## エンドポイント一覧

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/` | フロント(`frontend/index.html`)があれば配信 |
| GET | `/healthz` | 稼働確認 + 実測fps |
| WS | `/ws` | 検出結果JSONを `STREAM_FPS` で配信 (Web/Unity/Android共通の契約) |
| GET | `/video` | 注釈付きMJPEG (デバッグ確認用。ブラウザで直接開ける)。`?annotate=0` で枠を焼き込まない素の映像 (フロントはこちらを使う) |
| POST | `/calib` | 稼働中のズーム/切り抜き位置の調整 (グラスの視界合わせ) |
| POST | `/calib/save` | 現在の調整値をファイルへ保存 (次回起動時に自動で復元) |
| POST | `/explain` | 物体ラベルから日本語の説明文を生成 (Ollama のローカルLLM) |
| POST | `/speak` | 鑑定文の読み上げ音声(WAV)を返す (VOICEVOX または macOS の `say`) |

`/video` は単体で開く分は既定 (注釈付き) のままでよく、枠が二重になることはありません。
トップページが `?annotate=0` を使うのは、枠をページ側で描くためです。
`/healthz` はブラウザで開けば fps などの稼働状況をそのまま読めます。

## WebSocket の出力フォーマット (クライアント非依存の契約)

`/ws` が新フレームごとに送るJSON:

```json
{
  "frame_id": 1234,
  "ts": 1750000000.12,
  "fps": 14.8,
  "source_size": { "w": 1280, "h": 720 },
  "view": { "zoom": 1.0, "offset_x": 0.0, "offset_y": 0.0 },
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

> 表示: ブラウザのオーバーレイ枠と MJPEG映像は、検出名を**日本語・大きめ**で
> 表示します。MJPEGの日本語描画には日本語フォントが必要で、macOS/Windowsでは
> 自動検出します。文字化け(□)する場合のみ `JP_FONT` でフォントを指定してください。
