# XR Analyze

InnoMaker U20CAM-1080P-S1 (UVC USBカメラ) からリアルタイムで映像を取得し、
YOLOで物体検出して結果を配信するバックエンド。Mac / Windows 両対応。

検出結果は **JSON over WebSocket** で出します。Web・Unity・Android のどれからでも
同じデータを読めます(設計判断: [ADR-0002](docs/adr/0002-detection-streaming-contract.md))。

## どんなアプリか

- カメラ映像から YOLO で物体を検出し、正規化座標のJSONをWebSocket配信する
- 検出した物体を1つに絞って「鑑定」し、ローカルLLMが生成した鑑定文をVOICEVOXで読み上げる
- ブラウザで検出枠・鑑定盤を重ねて表示する、ARの叩き台

```
XR_ Analyze/
├── backend/    Python: カメラ取得 + YOLO検出 + 配信 (FastAPI)
├── frontend/   Web: 黒背景で検出枠を描画(ARの叩き台)
├── docs/       使い方・設定・APIなどのドキュメント (docs/adr/ に設計判断の記録)
└── README.md
```

## インストール方法

Python 3.9+ を推奨。

```bash
cd "XR_ Analyze"
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

`(.venv)` が付けば有効化済み。抜けるときは `deactivate`。初回実行時、YOLOモデルは自動ダウンロードされます。

鑑定文の生成・読み上げには外部プロセスの **Ollama** と **VOICEVOX** が必要です。
それぞれインストールして起動しておいてください。セットアップ手順とモデル取得、
VOICEVOXのクレジット表記義務などの詳細は [docs/voice.md](docs/voice.md) を参照。

## 使用方法

```bash
ollama serve                    # 鑑定文の生成 (別ターミナル)
# VOICEVOX (アプリ版 or ENGINE単体) を起動しておく
python3 -m backend.server
```

ブラウザで `http://127.0.0.1:8100/` を開きます。

| キー | 動作 |
|---|---|
| `m` | 画面中央にモードの円盤を開く(単体鑑定/広域鑑定/調整) |
| `←` `↑` `↓` `→` | 単体鑑定の対象を選び替える |
| `Enter` | 対象を固定して鑑定文を表示・読み上げ |
| `Esc` | 選択・固定を解除する |
| `c` | 調整モードに入る(ズーム・切り抜き位置・文字サイズ・保存) |
| `s` | (調整モード中) 調整値を保存する |
| `d` | デバッグ映像のON/OFF |

各モードの詳しい振る舞い・鑑定盤の見方・枠の意匠切り替えは
[docs/usage.md](docs/usage.md) を参照してください。
