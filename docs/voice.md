# 固定した物体の AI 説明 (ローカルLLM) と読み上げ

単体鑑定で対象を固定したときに表示される鑑定文の生成 (Ollama) と、
読み上げ (VOICEVOX / macOS `say`) のセットアップ・仕様をまとめる。

## 固定した物体の AI 説明 (ローカルLLM)

単体鑑定で `Enter` により鑑定を**固定**すると、画面中央に**説明ウィンドウ**が開き、
対象の**鑑定文**が日本語で表示されます。鑑定文はローカルLLM (Ollama / `gemma4:e4b`) が
検出ラベルから 2〜3 文で生成します (モデル・人物像の選定経緯:
[ADR-0024](adr/0024-llm-object-explanation.md))。

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
できます(configuration.md「設定 (環境変数で上書き)」参照)。鑑定文は検出ラベルのみから生成する
ため、画角に見えている個体の特徴(色・状態など)は含まれません。

鑑定文は表示と同時に音声でも読み上げられます。既定はキャラクター性のある声を
出せる **VOICEVOX ENGINE** です(声の選定経緯は
[ADR-0025](adr/0025-inspector-voice-readout.md) /
[ADR-0026](adr/0026-voicevox-character-voice.md) を参照)。

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
`VOICEVOX_PITCH` / `VOICEVOX_INTONATION` の環境変数で上書きできます(configuration.md「設定 (環境変数で上書き)」参照)。`TTS_BACKEND=say` にすれば、従来どおり
macOS 同梱の `say` コマンドで読み上げることもできます(`TTS_VOICE` /
`TTS_RATE` で調整。ターミナルで `say -v '?'` を実行すると導入済みの声を
一覧できます)。
