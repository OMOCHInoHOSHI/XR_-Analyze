# XR Analyze

InnoMaker U20CAM-1080P-S1 (UVC USBカメラ) からリアルタイムで映像を取得し、
YOLOで物体検出して結果を配信するバックエンド。Mac / Windows 両対応。

検出結果は **JSON over WebSocket** で出します。Web・Unity・Android のどれからでも
同じデータを読めます(設計判断: [ADR-0002](docs/adr/0002-detection-streaming-contract.md))。

## フォルダ構成

```
XR_ Analyze/
├── backend/            Python: カメラ取得 + YOLO検出 + 配信
│   ├── config.py       設定(環境変数で上書き可)
│   ├── camera.py       クロスプラットフォームのカメラ取得 (Mac/Win/Linux自動切替)
│   ├── detector.py     YOLO推論 → 正規化座標のJSON
│   ├── pipeline.py     背景スレッドで取得+推論し最新結果を保持
│   ├── calib_store.py  ズーム/位置の保存・復元 (calib.json)
│   ├── server.py       FastAPI: /ws (JSON), /video (MJPEG), /healthz
│   ├── check_camera.py サーバ無しで映るか確認するスタンドアロン
│   └── requirements.txt
├── frontend/           Web: 黒背景で検出枠を描画(ARの叩き台)
│   └── index.html
├── docs/adr/           設計判断の記録 (ADR)
└── README.md
```

> 構成の設計判断: [ADR-0001](docs/adr/0001-monorepo-layout.md)

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

### .venv の入り方・抜け出し方

```bash
# 入る (Mac/Linux)
source .venv/bin/activate
# 入る (Windows PowerShell)
.venv\Scripts\Activate.ps1

# 抜ける (共通)
deactivate
```

有効時はプロンプトの先頭に `(.venv)` が付きます。`deactivate` でシステムの Python に戻ります。

初回実行時、YOLOモデル (`yolov8n.pt`) は自動ダウンロードされます。

## 使い方

### 0. どのカメラが使われるか確認 (内蔵 vs InnoMaker)

既定で **名前一致 `CAM_NAME=Innomaker` により外付けを自動選択**します。これを
macOSで正しく機能させるには `pyobjc-framework-AVFoundation` (requirements.txt 同梱)
が必要です。未導入時は名前選択が効かないため `CAM_INDEX` を明示してください。
(選択順序の設計判断: [ADR-0005](docs/adr/0005-camera-selection.md))

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
  - `m` で画面中央に**モードの円盤**を開き、鑑定法(単体鑑定/広域鑑定)や調整に
    切り替え (下記「モードの切り替え」参照)
  - `↑` `↓` `←` `→` で単体鑑定の対象を選び替え、`Enter` で鑑定を固定
    (下記「単体鑑定・対象の選び替え・鑑定の固定」参照)
  - `1` 〜 `6` で枠の意匠(デザイン)を切り替え **※暫定のデバッグ機能** (下記「枠の意匠」参照)
  - `c` (または `m` → `3`) で調整モードに入り、ズーム・切り抜き位置・保存を行う
    (下記「カメラをスマートグラスに合わせ込む」参照)
- `http://127.0.0.1:8100/video` → 注釈付きMJPEGを直接表示。単体で開く分はこのままでOK
  - `?annotate=0` を付けると枠・ラベルを焼き込まない素の映像になります
  - トップページは `?annotate=0`(枠はページ側で描くため)を使っています。
    `/video` を直接開く場合はページ側の枠が無いので、既定(注釈付き)のままで二重にはなりません
    ([ADR-0002](docs/adr/0002-detection-streaming-contract.md))
- `http://127.0.0.1:8100/healthz` → 稼働確認 (fpsなど)

## モードの切り替え (単体鑑定 / 広域鑑定 / 調整)

トップページ(`/`)で `m` を押すと**画面中央にモードの円盤**が開き、モードを
切り替えられます([ADR-0021](docs/adr/0021-mode-wheel-with-calibration.md))。
円盤には鑑定法(単体鑑定/広域鑑定)と調整の**3項目**が並びます。

- 数字キー (`1`/`2`/`3`) を押すと**即座に確定**します。
- 矢印キーを押すと、カーソルではなく**盤そのものが回り**、選んでいる項目が
  必ず12時の指標へ来ます。`Enter` で確定します。押し続けても回転角は巻き戻らず、
  同じ向きへ回り続けます
  ([ADR-0023](docs/adr/0023-rotating-mode-wheel.md))。
- 無操作で **3秒**放置するか、上記以外のキー (`Esc` や `m` を含む) を押すと
  選択を取り消して閉じます (そのキー本来の機能も動きません)。
- 鑑定法と調整は**排他**に扱われ、「現在」の印は常に1つだけ付きます。調整を
  選ぶと鑑定法は覚えたまま調整モードへ入り、鑑定法を選ぶと調整から抜けます。
  抜けたときに鑑定法が変わらなければ、単体鑑定の選択・固定は解除されません
  (実際に鑑定法が変わったときだけ解除されます)。
- **調整モード中も `m` は効きます**。円盤から鑑定法を選べば、そのまま調整を
  終えられます。

| キー | モード | 表示 |
|---|---|---|
| `m` → `1` (または矢印+Enter) | 単体鑑定 | 常に**1件だけ**枠を出す。`↑↓←→` で対象選択、`Enter` で鑑定を固定 |
| `m` → `2` (または矢印+Enter) | 広域鑑定 (既定) | 信頼度の高い順に**最大10件** |
| `m` → `3` (または矢印+Enter、`c` の単独キーも同じ) | 調整 | ズーム・切り抜き位置・文字サイズ・保存 (下記「カメラをスマートグラスに合わせ込む」参照) |

鑑定法・調整を増やすときは `frontend/index.html` の `MODES` 配列に1行足すだけで、
円盤の配置・数字キーの割り当て・案内文が追従します。

### 単体鑑定・対象の選び替え・鑑定の固定

- 対象を選んでいない**照準ベース**の状態では、枠を出す1件は**画面中央の照準で
  狙っているもの**です。XRグラスでは頭を向けて対象を狙うため、画面中央 = 見ている
  ものとして扱います。判定は次の順です
  (距離の測り方と根拠: [ADR-0011](docs/adr/0011-gaze-target-selection.md))。
  1. **照準(画面中心)を含む検出**。複数あれば面積の小さい方 (より具体的な対象)
  2. 無ければ、照準から**表示領域の短辺の25%以内**で最も近い検出。同距離なら信頼度の高い方
  3. どちらも無ければ**枠を出しません** (見ていないものに勝手にフォーカスしない)
- 照準は画面中央に小さな菱形で表示されます (単体鑑定のときだけ)。
- 半径は `frontend/index.html` の `GAZE_RADIUS` (既定 `0.25`) で調整できます。
  狙いを合わせにくいときは大きく、余計なものを拾うときは小さくしてください。
- **矢印キー (`↑↓←→`)** を押すと、今の対象 (無ければ照準) から見てその向きにある
  最も近い検出へ対象を移せます。手前の物体に隠れた対象や、密集した対象を狙い分ける
  ための操作です。半径による足切りは矢印には掛かりません
  ([ADR-0020](docs/adr/0020-arrow-target-navigation.md))。
- 対象の状態は **照準ベース → 選択中 → 固定** の3段です。矢印で選ぶと「選択中」
  (枠の外側に破線+ラベル `[選択]`)になり、`Enter` で「固定」(枠が金色+ラベル
  `[固定]`)になります。固定中に矢印を押すと選択中へ降格したうえで隣へ移ります。
- 固定すると**説明ウィンドウ**が画面中央に開き、対象の鑑定文(異世界の鑑定士の口調で
  LLM生成)が表示されます(下記「固定した物体の AI 説明」参照)。
- `Esc` を押すと、選択も固定も解除して照準ベースへ戻ります。固定中に `Enter` をもう一度
  押した場合も同じで、選択中には戻らず照準ベースへ戻ります。鑑定法を切り替えたときも
  解除されます。
- 選択中・固定中はどちらも簡易追従で対象を引き継ぎます
  ([ADR-0012](docs/adr/0012-lock-tracking-by-iou.md))。
- 対象を見失うと枠は破線+半透明(`[見失い]`)になり、**2秒**戻らなければ自動で解除されます。

### 鑑定盤 (画面左上)

鑑定法と状態は画面左上の**鑑定盤**に出ます。意匠 `六` に合わせた四隅の菱形と明朝体で、
枠と同じ色トークン(`--c`)を使うため意匠を切り替えると盤も一緒に変わります。

| 行 | 内容 |
|---|---|
| 見出し | `鑑 定 眼` と現在の状態 (`走査中` / `選択中` / `固定` / `見失い` / `調整中` / `広域走査`) |
| 鑑定法 | `単体鑑定` または `広域鑑定` |
| 調整モードの案内窓 | `c` または `m` → `3` で調整モードに入っている間だけ**画面中央**に開く。切り抜き位置・ズーム・文字サイズ・保存・モードへ戻る・終了のキー一覧 |
| 報せ | 固定・解除・保存などの一時メッセージ (数秒で消える) |

モードの選択肢(単体鑑定/広域鑑定/調整)は画面中央の円盤 (`m` で開閉) に出ます。
視界の隅より中央のほうがXRグラスでは読み取りやすいためです
([ADR-0021](docs/adr/0021-mode-wheel-with-calibration.md))。

円盤・鑑定ウィンドウ・調整モードの案内窓の文字の大きさは、調整モード中の
`f` (大きく) / `v` (小さく)
で調整できます (`0` でリセット)。グラスごとの見え方の個体差を吸収するための
調整で、値はブラウザに保存されます。

盤の下に fps・検出数・ズーム調整値・意匠・キー一覧のデバッグ行が続きます。

> 枠は映像が実際に占める矩形(レターボックスを除く)を基準に配置されます:
> [ADR-0013](docs/adr/0013-overlay-coordinate-basis.md)
>
> 鑑定法の切替と固定はブラウザ側の表示ポリシーで、`/ws` の出力は変わりません。
> 検出が `MAX_DET`(既定15)を超える場面での制約を含め:
> [ADR-0010](docs/adr/0010-client-side-display-policy.md)

### 固定した物体の AI 説明 (ローカルLLM)

単体鑑定で `Enter` により鑑定を**固定**すると、画面中央に**説明ウィンドウ**が開き、
対象の**鑑定文**が日本語で表示されます。鑑定文はローカルLLM (Ollama / `gemma4:e4b`) が
検出ラベルから 2〜3 文で生成します (モデル・人物像の選定経緯:
[ADR-0024](docs/adr/0024-llm-object-explanation.md))。

異世界アニメの「鑑定」がモチーフのため、鑑定文は**異世界の無口な職人鑑定士**の
口調になります。傘や剣など昔から存在する道具は素直に鑑定されますが、PC や携帯など
この世界に存在しない科学技術の産物は「古代文明の遺物」「謎の物体」として、
正体を直接明かされないまま観察と推測だけで語られます。

- 初回は生成に**数秒**かかります(ウィンドウには「鑑定中…」と出ます)。以後、
  同じラベルはキャッシュから即座に表示されます。
- ウィンドウが出ている間は `Enter` / `Esc` が「ウィンドウを閉じる」に専任になり、
  固定や選択は解除されません。ウィンドウを閉じても固定は続きます。
- 対象を見失うなどして固定が外れると、ウィンドウも一緒に閉じます。
- Ollama が起動していないと失敗メッセージが出ます。固定自体は継続します。

セットアップ (事前に一度だけ):

```bash
brew install --cask ollama   # Ollama のインストール (Mac)
ollama pull gemma4:e4b       # 鑑定文を生成するモデル
ollama serve                 # 起動 (http://localhost:11434)
```

`OLLAMA_URL` / `OLLAMA_MODEL` / `EXPLAIN_TIMEOUT_SEC` の3つの環境変数で上書き
できます(下記「設定 (環境変数で上書き)」参照)。鑑定文は検出ラベルのみから生成する
ため、画角に見えている個体の特徴(色・状態など)は含まれません。

鑑定文は表示と同時に音声でも読み上げられます。既定はキャラクター性のある声を
出せる **VOICEVOX ENGINE** です(声の選定経緯は
[ADR-0025](docs/adr/0025-inspector-voice-readout.md) /
[ADR-0026](docs/adr/0026-voicevox-character-voice.md) を参照)。

セットアップ (事前に一度だけ):

- [公式サイト](https://voicevox.hiroshiba.jp/) からアプリ版 VOICEVOX を入れて
  起動するか、ENGINE 単体([voicevox_engine](https://github.com/VOICEVOX/voicevox_engine))
  を起動してください。既定の接続先は `http://localhost:50021` です。
- **クレジット表記が必須です。** 既定の話者「青山龍星」の利用規約は、個人が
  生成した音声について「`VOICEVOX:青山龍星` とクレジットを記載すれば、商用・
  非商用で利用可能」と定めています(企業が携わる形で利用する場合は権利元への
  事前確認が必要)。本アプリを公開・配布する際は必ず記載してください。
  規約は話者ごとに異なります。`VOICEVOX_SPEAKER` を変えたときは、ENGINE 起動中に
  `GET /speaker_info?speaker_uuid=...` を叩くか公式サイトで、その話者の規約を
  確認してください。

`TTS_BACKEND` / `VOICEVOX_URL` / `VOICEVOX_SPEAKER` / `VOICEVOX_SPEED` /
`VOICEVOX_PITCH` / `VOICEVOX_INTONATION` の環境変数で上書きできます(下記
「設定 (環境変数で上書き)」参照)。`TTS_BACKEND=say` にすれば、従来どおり
macOS 同梱の `say` コマンドで読み上げることもできます(`TTS_VOICE` /
`TTS_RATE` で調整。ターミナルで `say -v '?'` を実行すると導入済みの声を
一覧できます)。

## 枠の意匠 (デザイン候補の切り替え) ※暫定

テーマに合わせた枠デザインを見比べるための**暫定機能**です。

トップページ(`/`)で数字キー `1`〜`6` を単独で押すと、枠とラベルの意匠が切り替わります。
既定は `六: 蒼環の菱標` です。通常時・調整モード (`c`) 中のどちらでも効きますが、
円盤 (`m`) を開いている間は数字キー `1`〜`3` がモード(単体鑑定/広域鑑定/調整)の
確定に使われるため、意匠は切り替わりません
([ADR-0021](docs/adr/0021-mode-wheel-with-calibration.md))。
`0`(調整のリセット)は調整モードの中へ移ったため、意匠キーとは衝突しません
([ADR-0019](docs/adr/0019-key-binding-policy-v2.md))。

候補の一覧・採用理由・削除手順: [ADR-0014](docs/adr/0014-box-theme-and-dom-reuse.md)

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
  ([ADR-0002](docs/adr/0002-detection-streaming-contract.md))。
- `view` は現在のズーム/切り抜き位置(下記のライブ調整の確定値)。座標は切り抜き後の
  フレーム基準で正規化されるため、クライアント側で補正する必要はありません。
- `label` は英語([ADR-0003](docs/adr/0003-label-localization.md))、`label_ja` は日本語表示名です。
  辞書(`backend/labels_ja.py`)に無い語は `label_ja` も英語のままになります。

> 表示: ブラウザのオーバーレイ枠と MJPEG映像は、検出名を**日本語・大きめ**で
> 表示します。MJPEGの日本語描画には日本語フォントが必要で、macOS/Windowsでは
> 自動検出します。文字化け(□)する場合のみ `JP_FONT` でフォントを指定してください。

### 日本語の語彙を完全網羅する(任意)

YOLOEプロンプトフリーの語彙は約4585語あり、`labels_ja_lvis.py`(LVIS1203)+手動辞書で
カバーしきれない語は英語のまま出ます。残り全部を機械翻訳で一括日本語化できます:

```bash
# 1) 実際の語彙を書き出し
MODEL=yoloe-11m-seg-pf.pt python3 -m backend.dump_vocab
# 2) 翻訳エンジン(どちらか)
pip install argostranslate      # 推奨: オフライン
# pip install deep-translator   # オンライン(Google)
# 3) 未翻訳語を一括翻訳して ja_vocab.json を生成
python3 -m backend.build_ja_dict
```

生成された `ja_vocab.json` はアプリが自動で読み込みます。優先順位は
**機械翻訳 < LVIS < COCO < 手動**です([ADR-0003](docs/adr/0003-label-localization.md))。
気になる訳は `ja_vocab.json` か `backend/labels_ja.py` を直接編集してください。

## 設定 (環境変数で上書き)

設定の解決順序 (環境変数 > `calib.json` > 既定値) は
[ADR-0004](docs/adr/0004-config-resolution-order.md) を参照してください。

| 変数 | 既定 | 説明 |
|------|------|------|
| `CAM_NAME` | Innomaker | この名前を含むカメラを自動選択(部分一致・macOS)。空で無効 |
| `CAM_INDEX` | (未指定) | USBカメラ番号。**指定すると名前選択より優先**。内蔵は0、外付けは1など |
| `CAM_WIDTH` / `CAM_HEIGHT` | 1280 / 720 | 取得解像度 |
| `CAM_FLIP` | 180 | 映像の向き補正。`180`(上下+左右) / `v`(上下) / `h`(左右) / `none`(補正なし) |
| `CAM_ZOOM` | 1.0 | デジタルズーム倍率(中央クロップ)。1.0で等倍、1.5なら幅・高さを1/1.5に中央クロップ。1.0未満は無効(1.0にクランプ)。**指定すると `calib.json` の保存値より優先** |
| `CAM_OFFSET_X` / `CAM_OFFSET_Y` | 0.0 / 0.0 | 切り抜き中心のオフセット。フレーム幅/高さに対する正規化値(0.1=幅の10%右へ、yは+で下へ)。**指定すると保存値より優先** |
| `CALIB_FILE` | ./calib.json | 合わせ込み値(ズーム/位置)の保存先。ブラウザで `s` を押すと書き込まれ、次回起動時に自動で読まれる |
| `MODEL` | yolov8n.pt | 検出モデル。下記「認識できる物体を増やす」参照 |
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
| `OLLAMA_URL` | http://localhost:11434 | AI説明(上記「固定した物体の AI 説明」)で呼ぶ Ollama のアドレス |
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
> ([ADR-0009](docs/adr/0009-inference-device-selection.md))

> カメラを逆さに取り付けている前提で、既定で映像を180°回転させています。向きが
> おかしい場合は `CAM_FLIP` を変更してください(`CAM_FLIP=none` で補正なし)。
> ([ADR-0007](docs/adr/0007-viewport-crop-calibration.md))

### カメラをスマートグラスに合わせ込む (ズーム・パン)

カメラをスマートグラスに取り付けると、グラス越しの肉眼の視界とカメラ映像の
画角・位置がズレることがあります。`CAM_ZOOM`(デジタルズーム)と
`CAM_OFFSET_X` / `CAM_OFFSET_Y`(切り抜き中心のオフセット)で調整できます。
パンできる範囲はズーム倍率で決まります(zoom=1.0ならパン不可、1.5なら約±0.17、
2.0なら±0.25)。ズームを先に決めてからオフセットで位置を詰めるとよいです。
グラスを掛けたまま映像を見ながらライブ調整できます。**サーバ稼働中のブラウザ画面**と
`check_camera` のどちらでも調整できます。両者は別プロセスなので調整中の値はその場では
共有されませんが、ブラウザ画面で `s` を押して保存すれば `calib.json` 経由で両方に
引き継がれます。

#### サーバ稼働中に調整する (推奨)

`python3 -m backend.server` を起動してブラウザ (`http://127.0.0.1:8100/`) を開き、
実際のAR表示を見ながらキーで調整します。調整モードに入る手段は**`c` の単独キー**と
**円盤経由の `m` → `3`** の2つです(矢印キーは通常時、単体鑑定の対象選択に
使われるため調整は専用モードに隔離してあります。
[ADR-0019](docs/adr/0019-key-binding-policy-v2.md) /
[ADR-0021](docs/adr/0021-mode-wheel-with-calibration.md))。
調整モードに入ったら以下のキーが効きます。

- `c` (または `m` → `3`) : 調整モードの出入り
- 調整モード中:
  - `+` / `=` : ズーム +0.1、`-` : ズーム -0.1(下限1.0)
  - `↑` `↓` `←` `→` : 切り抜き位置を上/下/左/右へ 0.01 ずつ移動
    (`↑` で「より上」が見えるようになる = 映像の中身は下へ動く)
  - `s` : 現在のズーム・位置を保存 (次回起動時に自動で復元)
  - `0` : ズーム・位置をリセット
  - `d` : デバッグ映像のON/OFF (調整と直交するため調整モード中も効く)
  - `m` : **調整モード中もモードの円盤を開ける**。円盤から単体鑑定/広域鑑定を
    選べば、そのまま調整を終えられる
  - `c` / `Esc` : 調整モードを終える
- `m` : モードの円盤を開く (上記「モードの切り替え」参照。調整モードの外からでも
  中からでも使える)

現在値は画面左上のHUDに出ます。値を変えるたびに、サーバのターミナルへ
`[calib] CAM_ZOOM=... CAM_OFFSET_X=... CAM_OFFSET_Y=...` が出力されます。

##### 調整結果を次回に引き継ぐ

気に入ったところで `s` を押すと、プロジェクト直下の `calib.json` に保存されます
(HUDに `saved → calib.json`、ターミナルに `[calib] 保存しました -> ...` と出ます)。
次回以降は起動時に自動で読み込まれ、同じ画角で立ち上がります
(保存方式の設計判断: [ADR-0008](docs/adr/0008-calibration-persistence.md)):

```text
[calib] /path/to/XR_ Analyze/calib.json を読み込みました。初期値: CAM_ZOOM=1.3 ...
```

- 保存先を変えたい場合は `CALIB_FILE=/path/to/my_calib.json` を指定します
  (機材ごとに使い分けられます)。
- 保存値を使わず一時的に別の値で試したいときは、環境変数を明示すればそちらが勝ちます:

```bash
CAM_ZOOM=1.3 CAM_OFFSET_X=0.02 CAM_OFFSET_Y=-0.01 python3 -m backend.server
```

- 保存値を捨てて等倍に戻すには、`0` でリセットしてから `s`(上書き保存)、または
  `calib.json` を削除します。
- `calib.json` は機材・装着位置ごとに変わる値なので `.gitignore` 済みです。
- `check_camera` も同じ `calib.json` を初期値として読みます(あちらは `s` が
  「下方向へ移動」に割り当て済みのため、保存はブラウザ画面から行います)。

> 内部的にはブラウザから `POST /calib` (差分 `dzoom` / `dx` / `dy` / `reset`) を
> 呼んでいるので、Unity等の他クライアントからも同じ調整ができます。
> ([ADR-0007](docs/adr/0007-viewport-crop-calibration.md))

#### サーバを立てずに調整する

```bash
python3 -m backend.check_camera
```

- `+` / `=` : ズーム +0.1、`-` : ズーム -0.1(下限1.0)
- `w` / `s` : 垂直オフセット 上 / 下、`a` / `d` : 水平オフセット 左 / 右
- `0` : ズーム・オフセットをリセット
- `q` / `ESC` : 終了。終了時に確定値が `CAM_ZOOM=... CAM_OFFSET_X=... CAM_OFFSET_Y=...`
  という、そのままコピペできるenv形式でターミナルに出力されます。これを
  `python3 -m backend.server` の前に付けて起動すれば調整結果が反映されます。

例 (別カメラ・高精度モデルで起動):

```bash
CAM_INDEX=1 MODEL=yolov8s.pt python3 -m backend.server
```

## 認識できる物体を増やす

認識できる種類は**モデル**で決まります。用途に応じて `MODEL` を切り替えてください。

### A) モノの名前を幅広く知りたい(専門性不要・推奨)

オープン語彙の「プロンプトフリー」モデルに切り替えます。LVIS **1203カテゴリ**で、
クラスを指定せずに見えたモノへ名前を付けます。**この1203語は全て日本語対訳済み**
(`backend/labels_ja_lvis.py`)なので、表示は日本語になります。

精度優先なら大きいモデルを使います(s < m < l で精度↑/速度↓):

```bash
# 精度優先(推奨): l サイズ これをよく使う
MODEL=yoloe-11l-seg-pf.pt python3 -m backend.server

# 速度優先: s サイズ
MODEL=yoloe-11s-seg-pf.pt python3 -m backend.server
```

- 初回はモデルを自動ダウンロード。`-pf` がプロンプトフリーの印。
- 重い場合は `MAX_DET`(同時表示数)を下げる、`IMG_SIZE=512` 等を併用。
- 辞書外の語(オープン語彙でまれに出る)は英語のまま表示され、`labels_ja_lvis.py`
  に追記すれば日本語化できます。

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

## 設計判断の記録 (ADR)

「なぜこう作ったか」は [docs/adr/](docs/adr/) にまとめてあります。一覧は
[docs/adr/README.md](docs/adr/README.md) を参照してください。

## ラベルがコロコロ変わるとき (語彙の絞り込み)

オープン語彙モデルの内蔵語彙には、**物体ではない語**が大量に混ざっています。
`yoloe-11l-seg-pf.pt` の 4585 語には `stack` `activity` `accident` `darkness`
`computer room` `street scene` といった語が含まれ、これらが実在の物体名と競合して
毎フレーム最上位ラベルが入れ替わります。暗い部屋にカメラを向けると
「ダークネス」が信頼度0.92で最上位に出る、といった具合です。

既定 (`VOCAB=auto`) では、語彙の大きいモデルに限って **LVIS 1203カテゴリに
一致する語だけ**に絞ります (4585語 → 1054語)。実機フレーム40枚での実測:

| | 最上位ラベルの入れ替わり(生) | 安定化層の通過後 | トップラベル |
|---|---|---|---|
| `VOCAB=all` | 24回 | 6回 | `stack` 21 / `monitor` 9 / `computer chair` 6 |
| `VOCAB=auto` (既定) | **4回** | **0回** | **`monitor` 40/40** |

副次的に速くもなります (91.0ms → 81.9ms)。残したクラスのスコアは絞り込み前と
完全に同じで、消えた語の枠が「許可されている中での最良の名前」へ付け替わります。

**語彙が狭すぎる場合**は、書き出して必要な語を足してください。LVIS に無い物体
(`laptop` `humidifier` `smartphone` など) は名前が付かなくなります。

```bash
python3 -m backend.vocab > my_vocab.txt   # 現在の語彙を書き出す
# my_vocab.txt に laptop などを書き足す (1行1語、# 以降はコメント)
VOCAB=my_vocab.txt python3 -m backend.server
```

絞り込みをやめるには `VOCAB=all` を指定します。詳細と、検討して採らなかった案
(同義語の統合・機械的な語彙拡張) は
[ADR-0018](docs/adr/0018-vocabulary-restriction.md) を参照してください。

## 速度について

`yoloe-11l-seg-pf.pt` のようなセグメンテーション版のオープン語彙モデルは、
**使わないマスクの生成に毎フレーム 40ms 前後**を払っています。マスク復元は
検出件数に比例するため、「何も映っていないと速く、モノを映して枠が出ると遅くなる」
という形で効きます。

既定 (`FAST_INFER=1`) ではこのマスク経路を切り離してあり、Apple M4 / `IMG_SIZE=640`
での実測は次のとおりです (検出12件の画像、旧経路と交互に25回測定)。

| 経路 | 1フレーム | fps |
|---|---|---|
| `FAST_INFER=0` (ultralytics `predict()`) | 193.2ms | 5.2 |
| `FAST_INFER=1` (既定) | 140.0ms | 7.1 |

実機 (InnoMaker / `CAM_ZOOM=2.4` / 検出5件) では **4.4fps 前後 → 6.5fps 前後**でした。
検出結果は変わりません。詳細と検証方法は
[ADR-0016](docs/adr/0016-mask-free-fast-inference.md) を参照してください。

さらに速くしたい場合は、精度とのトレードオフになりますが次の順で効きます。

0. `VOCAB` で語彙を絞る — 上記のとおり既定で有効。ラベルの安定にも効く
1. `IMG_SIZE=512` — 推論サイズを落とす。小さい物体を取りこぼしやすくなる
2. `MODEL=yoloe-11s-seg-pf.pt` — 同じ語彙のまま小型版へ。実測で約2倍速い
3. `MODEL=yolov8n.pt` — COCO 80カテゴリに割り切る。実測 27fps

> 実装言語を C 系へ移す案は効果がありません。処理時間の 9 割以上は
> PyTorch / Metal のカーネル内にあり、Python のグルーコードは 10% 未満です。
> CoreML / Neural Engine への書き出しは実機でさらに 1.87 倍速い (9.6fps) と実測
> できていますが、安定化層を通した後の表示ラベルの入れ替わりが約2倍に増えるため
> 未採用です ([ADR-0016](docs/adr/0016-mask-free-fast-inference.md))。

## メモ / 既知の制約

- 既定の COCO 学習済みモデルは約80カテゴリ。上記Aのオープン語彙モデルなら1200+に拡張。
- いずれも一般物体の認識です。「鑑定」固有の特定個体(特定商品・カード等)の判別には
  独自データでの追加学習(fine-tune)が別途必要です。
- macOSでは初回にカメラ使用許可のダイアログが出ます。許可してください。
- AR(XREAL Air2Pro)での現実への重ね合わせは、カメラ画角と視界の座標対応が
  別途必要です(memoryの未決事項)。本リポジトリは検出基盤までを提供します。
