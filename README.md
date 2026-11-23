# XR Analyze

InnoMaker U20CAM-1080P-S1 (UVC USBカメラ) からリアルタイムで映像を取得し、
YOLOで物体検出して結果を配信するバックエンド。Mac / Windows 両対応。

検出結果は **JSON over WebSocket** で出します。Web・Unity・Android のどれからでも
同じデータを読めます(設計判断: [ADR-0002](docs/adr/0002-detection-streaming-contract.md))。

## どんなアプリか

- カメラ映像から YOLO で物体を検出し、正規化座標のJSONをWebSocket配信する
- 検出した物体を1つに絞って「鑑定」し、ローカルLLMが生成した鑑定文をVOICEVOXで読み上げる
- キーに加えて、決まった言葉を声で言っても操作できる (ローカルの音声認識 Vosk)
- ブラウザで検出枠・鑑定盤を重ねて表示する、ARの叩き台

```
XR_ Analyze/
├── backend/    Python: カメラ取得 + YOLO検出 + 配信 (FastAPI)
├── frontend/   Web: 黒背景で検出枠を描画(ARの叩き台)
├── docs/       使い方・設定・APIなどのドキュメント (docs/adr/ に設計判断の記録)
├── packaging/  Windows 向け exe の設定 (build_exe.ps1 で使う)
├── build_exe.ps1  exe のビルド (docs/windows.md)
├── XRAnalyze.bat  Windows でダブルクリックして起動 (docs/windows.md)
└── README.md
```

## インストール方法

**Windows では**、GitHub の develop ブランチを Download ZIP して展開し、
`XRAnalyze.bat` をダブルクリックするだけで起動できます (初回だけ Python と依存を
自動で取得します)。ビルド済みの `XRAnalyze.exe` があれば、それをダブルクリックしても
構いません。exe の使い方・作り方と、Windows で GPU (CUDA) を使う
venv の作り方は [docs/windows.md](docs/windows.md) を参照してください。

Python 3.9+ を推奨。

```bash
cd "XR_ Analyze"
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
# Windows で NVIDIA GPU を使う場合は先に CUDA 版の torch を入れる (docs/windows.md)
pip install -r backend/requirements.txt
```

`(.venv)` が付けば有効化済み。抜けるときは `deactivate`。初回実行時、検出モデル(既定 `yoloe-11l-seg-pf.pt`、71MB)は自動ダウンロードされます。

鑑定文の生成・読み上げには外部プロセスの **Ollama** と **VOICEVOX** が必要です。
それぞれインストールして起動しておいてください。セットアップ手順とモデル取得、
VOICEVOXのクレジット表記義務などの詳細は [docs/voice.md](docs/voice.md) を参照。

## 使用方法

```bash
ollama serve                    # 鑑定文の生成 (別ターミナル)
# VOICEVOX (アプリ版 or ENGINE単体) を起動しておく
python3 -m backend.server
```

ブラウザで `http://127.0.0.1:8100/` を開くと、まず**起動画面**が出ます。接続中の
カメラ (名前・解像度・縮小画像) と使える検出モデルが並ぶので、選んで
「この設定で開始」(`Enter`) を押すと検出が始まり、下のキーで操作する画面に切り替わります
([ADR-0043](docs/adr/0043-startup-selection-screen.md))。起動画面を飛ばして環境変数の
設定ですぐ始めるには `python3 -m backend.server --no-select` です。

| キー | 動作 |
|---|---|
| `m` | 画面中央にモードの円盤を開く(単体鑑定/広域鑑定/調整) |
| `←` `↑` `↓` `→` | 単体鑑定の対象を選び替える |
| `Enter` | 対象を固定して鑑定文を表示・読み上げ |
| `Esc` | 選択・固定を解除する |
| `c` | 調整モードに入る(ズーム・切り抜き位置・文字サイズ・保存) |
| `s` | (調整モード中) 調整値を保存する |
| `d` | デバッグ映像のON/OFF |

### 音声コマンド

マイクに向かって次の言葉を言っても操作できます。キー操作はそのまま使えます。
初回の起動で日本語の音声モデル (約50MB) を自動で取得し、2回目以降はネットなしで
動きます。鑑定盤 (画面左上) の「音声」の行に、マイクの音量が波形で出ます。

| 言葉 | 効く場面 | 動作 |
|---|---|---|
| 「モード変更」 | 円盤が閉じているとき | モードの円盤を開く (声で開いたときは6秒で閉じる) |
| 「たんたいかんてい」「いち」 | 円盤が開いているとき | 単体鑑定にする |
| 「こういきかんてい」「に」 | 円盤が開いているとき | 広域鑑定にする |
| 「鑑定」 | 単体鑑定中 | 照準の対象を固定して鑑定 (固定中なら照準の対象へ移して鑑定し直す) |
| 「とじる」 | 円盤・鑑定ウィンドウが開いているとき | 閉じる |
| 「終了」 | いつでも | アプリを終了する |

鑑定ウィンドウが開いている間は「とじる」「終了」だけを受け付けます (読み上げの声を
拾って誤動作しないように)。使わないときは `VOICE=0` で切れます。仕様と設定は
[docs/voice-recognizer.md](docs/voice-recognizer.md)、方式を選んだ理由は
[ADR-0045](docs/adr/0045-voice-commands.md) を参照してください。

各モードの詳しい振る舞い・鑑定盤の見方・枠の意匠切り替えは
[docs/usage.md](docs/usage.md) を参照してください。鑑定文と読み上げ音声は事前生成して
ディスクに永続化でき(サーバと同じ環境変数を付けて
`python3 -m backend.precompute_explain` / `precompute_speak`)、詳細は
[docs/voice.md](docs/voice.md) を参照してください。
