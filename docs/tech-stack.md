# 使用技術

このアプリが何の上に成り立っているかをまとめる。**「なぜそれを選んだか」は
[adr/](adr/) に個別の記録がある**ので、判断の背景を知りたいときはそちらを読む。

版はいずれも 2026-08-30 時点の実機 (Apple M4 / 24GB RAM / macOS) の実測値。
`requirements.txt` が要求する下限とは別なので、両方を併記した。

## 全体の流れ

```
  USBカメラ ──▶ OpenCV ──▶ YOLO (torch/MPS) ──┬──▶ WebSocket /ws ──▶ ブラウザ
  (UVC)         取得        物体検出           └──▶ MJPEG /video ──▶ (枠を重ねて描画)

  鑑定 (Enter) ──▶ Ollama (gemma4) ──▶ 鑑定文 ──▶ VOICEVOX ──▶ WAV ──▶ 読み上げ
                   ローカルLLM                    音声合成
```

検出はカメラが動いている限り回り続け、鑑定は対象を固定したときだけ走る。
両者は独立していて、Ollama や VOICEVOX が落ちていても検出と表示は動く。

## バックエンド (Python)

| パッケージ | 実機の版 | 要求 | 何に使っているか | 関連 |
|---|---|---|---|---|
| `ultralytics` | 8.4.80 | >=8.2.0 | YOLO の物体検出。モデルの読み込みと推論 | [ADR-0016](adr/0016-mask-free-fast-inference.md) |
| `torch` | 2.12.1 | (ultralytics 経由) | 推論の実体。Apple Silicon では MPS (GPU) を使う | [ADR-0009](adr/0009-inference-device-selection.md) |
| `torchvision` | 0.27.1 | >=0.15 | NMS (`batched_nms`)。自前の高速推論経路で直接呼ぶ | [ADR-0016](adr/0016-mask-free-fast-inference.md) |
| `opencv-python` | 4.13.0.92 | >=4.9.0 | カメラ取得 (UVC)、クロップ・反転、JPEG エンコード、枠の描画 | [ADR-0007](adr/0007-viewport-crop-calibration.md) |
| `numpy` | 2.3.5 | >=1.24 | フレームと座標の配列処理 | — |
| `fastapi` | 0.138.1 | >=0.110.0 | HTTP と WebSocket のサーバ | [ADR-0002](adr/0002-detection-streaming-contract.md) |
| `uvicorn[standard]` | 0.49.0 | >=0.29.0 | ASGI サーバ。終了処理に手を入れている | [ADR-0032](adr/0032-prompt-shutdown.md), [ADR-0034](adr/0034-streams-close-themselves.md) |
| `websockets` | 16.0 | >=12.0 | WebSocket の実装 (uvicorn が使う) | — |
| `pyobjc-framework-AVFoundation` | 12.2.1 | >=10.0 (macOSのみ) | カメラ名を OpenCV と同じ並び順で取得し、`CAM_NAME` で選ぶ | [ADR-0005](adr/0005-camera-selection.md) |

`pydantic` (2.13.4) と `starlette` (1.3.1) は FastAPI が連れてくる。`pillow`・
`matplotlib`・`PyYAML` は ultralytics の依存で、こちらから直接は使っていない。

### 標準ライブラリで済ませているもの

pip の依存を増やさない方針を採っており ([ADR-0024](adr/0024-llm-object-explanation.md))、
外部サービスとのやり取りも自前の並行処理も stdlib だけで書いている。

| 使うもの | 用途 |
|---|---|
| `urllib` | Ollama と VOICEVOX への HTTP。専用クライアントは入れない |
| `wave` / `io` | 文ごとに合成した WAV の結合 ([ADR-0027](adr/0027-speech-prefetch-during-generation.md)) |
| `subprocess` | macOS の `say` を叩く退避路 ([ADR-0025](adr/0025-inspector-voice-readout.md)) |
| `threading` / `queue` | 取得・推論スレッド、読み上げの先読みワーカー ([ADR-0006](adr/0006-single-background-pipeline.md), [ADR-0017](adr/0017-decoupled-capture-thread.md)) |
| `json` / `tempfile` / `signal` | 契約の直列化、`say` の出力先、終了の合図 |

## フロントエンド

**素の HTML / CSS / JavaScript のみ。`frontend/index.html` の 1 ファイルで完結**し、
外部スクリプトもフォントも CDN も読み込まない。フレームワークもビルド工程も無い。

XR グラスに映す 1 枚の画面が目的であり、画面遷移も状態管理の複雑さも無い。
ビルドを挟まないぶん、`index.html` を保存してブラウザを再読み込みすれば即座に
反映される。表示の決定はクライアント側に閉じている
([ADR-0010](adr/0010-client-side-display-policy.md))。

描画は CSS の `transform` と DOM の使い回しで行い、Canvas も WebGL も使わない
([ADR-0014](adr/0014-box-theme-and-dom-reuse.md))。利用者ごとの設定
(文字サイズ・直前のモード) は `localStorage` に置く
([ADR-0029](adr/0029-mode-persistence.md))。

## 外部プロセス

どちらもローカルで動かす。クラウドの API は使わない。

| | 実機の版 | 役割 | 関連 |
|---|---|---|---|
| **Ollama** | 0.33.2 | 鑑定文の生成。モデルは `gemma4:e4b` (9.6GB) | [ADR-0024](adr/0024-llm-object-explanation.md) |
| **VOICEVOX ENGINE** | 0.25.2 | 鑑定文の読み上げ。既定の話者は「青山龍星・不機嫌」(ID 82) | [ADR-0026](adr/0026-voicevox-character-voice.md) |
| macOS `say` | (OS 同梱) | VOICEVOX を用意できない場合の退避路。`TTS_BACKEND=say` | [ADR-0025](adr/0025-inspector-voice-readout.md) |

**VOICEVOX の音声を公開・配布物に使うときは「VOICEVOX:青山龍星」のクレジット表記が
必須**である。詳細は [voice.md](voice.md) を参照。

## 検出モデル

`.pt` ファイルは初回実行時に自動でダウンロードされる。

| ファイル | サイズ | 語彙 | 用途 |
|---|---|---|---|
| `yolov8n.pt` | 6.2MB | COCO 80カテゴリ | 既定。最軽量・最速 |
| `yoloe-11s-seg-pf.pt` | 27MB | LVIS 1200+ | オープン語彙・速度優先 |
| `yoloe-11m-seg-pf.pt` | 61MB | LVIS 1200+ | 中間 |
| `yoloe-11l-seg-pf.pt` | 71MB | LVIS 1200+ | 精度優先。実機で常用 |

プロンプトフリーの語彙 4585 語のうち、物体でない語を除いた 1054 語に絞って使う
([ADR-0018](adr/0018-vocabulary-restriction.md))。切り替え方は
[detection.md](detection.md) にある。

## 実行環境

- **Python 3.12.14** (`requirements.txt` は 3.9+ を想定)。`venv` + `pip` で管理し、
  Poetry や uv などは挟まない
- **Apple M4 / 24GB RAM / macOS (arm64)** が実機。`torch.backends.mps.is_available()`
  が真で、推論は自動的に MPS (GPU) を使う ([ADR-0009](adr/0009-inference-device-selection.md))
- カメラ取得は Mac / Windows / Linux で切り替わる ([ADR-0005](adr/0005-camera-selection.md))
  が、読み上げの `say` 経路と VOICEVOX の GPU 非対応は macOS 前提の話になる
- リポジトリは backend / frontend / docs を 1 つに収めた単一構成
  ([ADR-0001](adr/0001-monorepo-layout.md))

## 依存についての方針

新しい pip パッケージを足す前に、stdlib で書けないかを先に考える。Ollama と
VOICEVOX への HTTP に `requests` や `httpx` を入れず `urllib` で済ませているのも、
音声の結合に `pydub` を入れず `wave` で書いているのもこの方針による。

理由は、このアプリが**実機 1 台で完結して動き続けること**を重視しているためである。
依存が増えるほど、環境を作り直すときと OS を更新したときに壊れる面が増える。
速度や品質のために足す価値があると判断したものは ADR に理由を残す。
