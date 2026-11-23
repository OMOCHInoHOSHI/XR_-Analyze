"""
音声コマンドの聞き取り (理由: docs/adr/0045-voice-commands.md)。

マイクの音を Vosk (ローカルの音声認識) に流し、決まった言葉だけを聞き取る。
聞き取ったコマンドと音の大きさは /voice (WebSocket) でブラウザへ送り、
「今その言葉で何をするか」はブラウザ側 (frontend/app.js) が決める。キー操作と
同じく、モードや固定の状態はブラウザだけが持っているため
(docs/adr/0010-client-side-display-policy.md)。

- 認識する言葉を PHRASES に限定する (Vosk の grammar 指定)。「に」のような1音の
  語は、言葉を絞らないとほぼ拾えない。
- 認識結果が**ちょうど1つのコマンドだけ**のときに限って受け付ける。鑑定文の
  読み上げをマイクが拾うと「[unk] 鑑定 [unk]」のように文の途中のコマンド語が
  出るため、前後に何か付いていたら捨てる。
- vosk / sounddevice の import は使う直前に行う。入っていない・マイクが無い・
  モデルが取れない場合も、状態を報せるだけでアプリの起動は止めない。
"""
from __future__ import annotations

import collections
import json
import shutil
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

import numpy as np

# 認識結果の文字列 -> コマンド。Vosk は語をスペースで区切って返すので、
# キーもモデルの語彙の区切りどおりに書く (「たんたいかんてい」は語彙では「単体 鑑定」)。
# 「いち」「に」「とじる」は漢字 (一・二・閉じる) より仮名のほうが、合成音声での
# 試験で取り違えが少なかった (二 は「これは何でしょうか」の中から拾われた)。
PHRASES: dict[str, str] = {
    "モード 変更": "mode",
    "単体 鑑定": "single",
    "いち": "single",
    "広域 鑑定": "multi",
    "に": "multi",
    "鑑定": "appraise",
    "とじる": "close",
    "終了": "quit",
}
UNK = "[unk]"
# [unk] を混ぜておくと、関係ない会話がコマンド語へ無理に当てはめられず [unk] になる。
GRAMMAR: list[str] = [*PHRASES, UNK]

# 状態。ブラウザは鑑定盤の「音声」の行の出し分けに使う。
OFF = "off"            # VOICE=0 で使わない設定
LOADING = "loading"    # モデルの読み込み・ダウンロード中
LISTENING = "listening"
NO_MIC = "nomic"       # マイクが無い / 開けない / sounddevice が無い
NO_MODEL = "nomodel"   # モデルが無く取得もできない / vosk が無い

# 音の大きさを 0〜1 にする範囲 (dBFS)。-60 以下は無音、-10 以上は振り切り。
LEVEL_FLOOR_DB = -60.0
LEVEL_CEIL_DB = -10.0
# 1秒あたりの音量の更新回数。ブラウザの波形の描き直しと同じ頻度にそろえる。
LEVEL_HZ = 30
# マイクを開けなかったとき・切れたときに開き直す間隔 (秒)。
MIC_RETRY_SEC = 3.0
# 途中の認識結果 (partial) がこの秒数変わらなければ、発話が終わったとみなして確定させる。
# Vosk 自身の区切り (endpoint) は、関係ない会話のあとだと 1 秒の無音でも区切らないことが
# あり、続けて言ったコマンドが「[unk] いち」のように前の会話とつながって捨てられた。
# 合成音声での試験で 0.3 秒は「広域 / 鑑定」の間で切れ、0.5〜0.7 秒は8語とも正しく
# 取れたので間を取った (理由: docs/adr/0045-voice-commands.md)。
SETTLE_SEC = 0.6


def parse(text: str) -> tuple[str, str] | None:
    """
    認識結果の文字列をコマンドにする。ちょうど1つのコマンドだけなら
    (コマンド, 表示用の言葉) を、それ以外 (空・[unk] 混じり・複数) なら None を返す。
    """
    key = " ".join(text.split())
    cmd = PHRASES.get(key)
    if cmd is None:
        return None
    return cmd, key.replace(" ", "")


def level_of(pcm: bytes) -> float:
    """16bit モノラル PCM の塊の音の大きさを 0〜1 で返す (dB で直線に並べる)。"""
    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if rms <= 0.0:
        return 0.0
    db = 20.0 * np.log10(rms / 32768.0)
    v = (db - LEVEL_FLOOR_DB) / (LEVEL_CEIL_DB - LEVEL_FLOOR_DB)
    return float(min(1.0, max(0.0, v)))


def model_ready(model_dir: Path) -> bool:
    """Vosk のモデルフォルダとして使える形になっているか。"""
    return (model_dir / "am" / "final.mdl").is_file() and (model_dir / "conf").is_dir()


def ensure_model(model_dir: Path, url: str) -> bool:
    """
    モデルが無ければ url の zip を取ってきて model_dir に展開する。使える状態に
    なれば True。url が空なら取りに行かない。失敗しても例外は投げず False を返す。

    途中で落ちても半端なフォルダが残らないよう、一時フォルダに展開してから
    最後に名前を変えて置く。
    """
    if model_ready(model_dir):
        return True
    if not url:
        print(f"[voice] 音声認識のモデルがありません: {model_dir}")
        return False
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    print(f"[voice] 音声認識のモデルを取得しています (初回だけ、約50MB): {url}")
    try:
        with tempfile.TemporaryDirectory(dir=model_dir.parent) as tmp:
            zip_path = Path(tmp) / "model.zip"
            with urllib.request.urlopen(url, timeout=30) as r, open(zip_path, "wb") as f:
                shutil.copyfileobj(r, f)
            out = Path(tmp) / "x"
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(out)
            # zip は「vosk-model-.../am/...」のように1段フォルダを挟む作り。
            # 挟まない作りでも受け付ける。
            found = out if model_ready(out) else next(
                (p for p in out.iterdir() if p.is_dir() and model_ready(p)), None
            )
            if found is None:
                print("[voice] 取得した zip に Vosk のモデルが見つかりませんでした")
                return False
            if model_dir.exists():
                shutil.rmtree(model_dir)
            found.rename(model_dir)
    except Exception as e:  # ネット不通・書き込み不可など。起動は止めない
        print(f"[voice] 音声認識のモデルを取得できませんでした: {e}")
        return False
    print(f"[voice] モデルを置きました: {model_dir}")
    return True


def find_input_device(name: str) -> int | None:
    """
    名前の一部 (大文字小文字は無視) が一致する入力デバイスの番号を返す。name が空なら
    None (= OS の既定のマイク)。一致が無ければ LookupError。
    """
    if not name:
        return None
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            return i
    raise LookupError(f"MIC_NAME={name!r} に一致するマイクがありません")


class VoiceListener:
    """
    マイクの音を読み続け、音の大きさと聞き取ったコマンドを溜める裏方のスレッド。

    読み手 (/voice の各接続) は state・level を見て、events() で seq より新しい
    コマンドだけを受け取る。読み手ごとの配送先は持たない (WebSocket 側が自分で
    取りに来る)。/ws と同じくポーリングにして、スレッドと asyncio の橋渡しを省いた。

    open_audio・make_recognizer は試験でマイクや Vosk の代わりを差し込むための口。
    """

    def __init__(
        self,
        model_dir: Path,
        model_url: str = "",
        mic_name: str = "",
        *,
        open_audio: Callable[[int | None, Callable[[bytes], None]], tuple[object, int]] | None = None,
        make_recognizer: Callable[[int], object] | None = None,
    ):
        self.model_dir = Path(model_dir)
        self.model_url = model_url
        self.mic_name = mic_name
        self._open_audio = open_audio or _open_sounddevice
        self._make_recognizer = make_recognizer
        self.state = LOADING
        self.level = 0.0
        self.seq = 0
        self._events: collections.deque[tuple[int, str, str]] = collections.deque(maxlen=32)
        self._partial = ""          # 直前の途中結果
        self._partial_since = 0.0   # その途中結果になった時刻
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- 読み手向け ---

    def events(self, after: int) -> list[tuple[int, str, str]]:
        """seq が after より新しいコマンドを古い順に返す。要素は (seq, コマンド, 言葉)。"""
        with self._lock:
            return [e for e in self._events if e[0] > after]

    # --- 起動・停止 ---

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # --- 中身 ---

    def emit(self, cmd: str, word: str) -> None:
        with self._lock:
            self.seq += 1
            self._events.append((self.seq, cmd, word))
        print(f"[voice] 聞き取り: {word} ({cmd})")

    def feed(self, recognizer, pcm: bytes, now: float | None = None) -> None:
        """音の塊を1つ処理する。音量を更新し、発話が区切れたら結果を見る。"""
        self.level = level_of(pcm)
        if recognizer.AcceptWaveform(pcm):
            self._partial = ""
            self._take(recognizer.Result())
            return
        # Vosk が区切らなくても、途中結果が SETTLE_SEC 動かなければこちらで区切る。
        now = time.monotonic() if now is None else now
        partial = json.loads(recognizer.PartialResult()).get("partial", "")
        if partial != self._partial:
            self._partial = partial
            self._partial_since = now
        elif partial and now - self._partial_since >= SETTLE_SEC:
            self._partial = ""
            # FinalResult は確定と同時に認識器を空にし、続けて次の発話を受け付ける
            self._take(recognizer.FinalResult())

    def _take(self, result_json: str) -> None:
        hit = parse(json.loads(result_json).get("text", ""))
        if hit is not None:
            self.emit(*hit)

    def _recognizer_factory(self) -> Callable[[int], object] | None:
        """サンプルレートを受け取って認識器を作る関数を返す。用意できなければ None。"""
        if self._make_recognizer is not None:
            return self._make_recognizer
        try:
            import vosk
        except ImportError:
            print("[voice] vosk が入っていません (pip install -r backend/requirements.txt)")
            return None
        if not ensure_model(self.model_dir, self.model_url):
            return None
        vosk.SetLogLevel(-1)
        model = vosk.Model(str(self.model_dir))
        grammar = json.dumps(GRAMMAR, ensure_ascii=False)
        return lambda rate: vosk.KaldiRecognizer(model, rate, grammar)

    def _run(self) -> None:
        try:
            factory = self._recognizer_factory()
        except Exception as e:
            print(f"[voice] 音声認識のモデルを読み込めませんでした: {e}")
            factory = None
        if factory is None:
            self.state = NO_MODEL
            return
        while not self._stop.is_set():
            self._listen_once(factory)
            if not self._stop.is_set():
                self._stop.wait(MIC_RETRY_SEC)

    def _listen_once(self, factory) -> None:
        """マイクを開いて、切れるか止められるまで聞く。開けなければすぐ戻る。"""
        chunks: "collections.deque[bytes]" = collections.deque(maxlen=200)
        got = threading.Event()

        def on_audio(pcm: bytes) -> None:
            chunks.append(pcm)
            got.set()

        try:
            if self.state == NO_MIC and self._open_audio is _open_sounddevice:
                _refresh_devices()   # 前回開けなかった: 後から挿したマイクを見えるようにする
            device = find_input_device(self.mic_name)
            stream, rate = self._open_audio(device, on_audio)
        except Exception as e:
            if self.state != NO_MIC:
                print(f"[voice] マイクを開けません: {e}")
            self.state = NO_MIC
            self.level = 0.0
            return
        recognizer = factory(rate)
        self.state = LISTENING
        print(f"[voice] 聞き取りを始めました ({rate} Hz)")
        try:
            while not self._stop.is_set():
                if not got.wait(0.5):
                    # 音が来ない = マイクが抜かれた等。開き直す
                    if not getattr(stream, "active", True):
                        print("[voice] マイクが切れました。開き直します")
                        break
                    continue
                got.clear()
                while chunks:
                    self.feed(recognizer, chunks.popleft())
        finally:
            self.level = 0.0
            try:
                stream.close()
            except Exception:
                pass
            if not self._stop.is_set():
                self.state = NO_MIC


def _refresh_devices() -> None:
    """
    PortAudio が覚えている機器の一覧を取り直す。PortAudio は初期化した時点の一覧を
    使い続けるため、起動後に挿したマイクは取り直さないと見えない。
    """
    try:
        import sounddevice as sd

        sd._terminate()
        sd._initialize()
    except Exception:
        pass


def _open_sounddevice(device: int | None, on_audio: Callable[[bytes], None]):
    """マイクを 16bit モノラルで開いて (ストリーム, サンプルレート) を返す。"""
    import sounddevice as sd

    info = sd.query_devices(device, "input")
    # 16kHz を要求すると対応しない機器があるため、機器の既定のレートで開き、
    # そのレートを Vosk に伝える (Vosk 側で変換される)。
    rate = int(info["default_samplerate"])
    stream = sd.RawInputStream(
        samplerate=rate,
        blocksize=max(1, rate // LEVEL_HZ),
        device=device,
        channels=1,
        dtype="int16",
        callback=lambda data, frames, t, status: on_audio(bytes(data)),
    )
    stream.start()
    return stream, rate


def disabled() -> VoiceListener:
    """VOICE=0 のときに置く、何もしない聞き手 (状態は OFF のまま)。"""
    v = VoiceListener(Path("."))
    v.state = OFF
    return v
