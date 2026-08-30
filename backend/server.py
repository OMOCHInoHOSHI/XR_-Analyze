"""
配信サーバ (FastAPI)。

エンドポイント:
  GET  /            -> フロント(frontend/index.html)があれば配信
  GET  /healthz     -> 稼働確認 + 実測fps
  WS   /ws          -> 検出結果JSONをSTREAM_FPSで配信 (Web/Unity/Android共通の契約)
  GET  /video       -> 注釈付きMJPEG (デバッグ確認用。ブラウザで直接開ける)
                       ?annotate=0 で枠を焼き込まない素の映像 (フロントはこちらを使う)
  POST /calib       -> 稼働中のズーム/切り抜き位置の調整 (グラスの視界合わせ)
  POST /calib/save  -> 現在の調整値をファイルへ保存 (次回起動時に自動で復元)
  POST /explain     -> 物体ラベルから日本語の説明文を生成 (Ollama のローカルLLM)
  POST /speak       -> 鑑定文の読み上げ音声(WAV)を返す (macOS の say)

起動:
  cd "XR_ Analyze"
  python3 -m backend.server
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import calib_store, config, tts
from .detector import Detector
from .pipeline import Pipeline

pipeline: Pipeline | None = None
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    pipeline = Pipeline()
    pipeline.start()
    try:
        yield
    finally:
        if pipeline is not None:
            pipeline.stop()


app = FastAPI(title="XR Analyze backend", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    if pipeline is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    payload = pipeline.detections_payload()
    return {
        "status": "ok",
        "fps": payload["fps"],
        "frame_id": payload["frame_id"],
        "model": config.MODEL,
        "device": config.DEVICE or "auto",
        "source_size": payload["source_size"],
        "error": payload["error"],
    }


@app.get("/")
async def index():
    html = FRONTEND_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return JSONResponse(
        {"message": "backend running", "ws": "/ws", "video": "/video", "health": "/healthz"}
    )


class CalibRequest(BaseModel):
    """ズーム/オフセットの調整量。すべて差分(絶対値ではない)。"""

    dzoom: float = 0.0     # ズーム倍率の増減
    dx: float = 0.0        # 切り抜き中心の水平移動 (全体比、+で右)
    dy: float = 0.0        # 切り抜き中心の垂直移動 (全体比、+で下)
    reset: bool = False    # 真なら差分を無視して zoom=1.0 / offset=0,0 に戻す


@app.post("/calib")
async def calib(req: CalibRequest):
    """
    稼働中にカメラのデジタルズーム/切り抜き位置を調整する(グラスの視界合わせ用)。

    差分で受けクランプ後の確定値を返す設計の理由: docs/adr/0010-client-side-display-policy.md
    """
    if pipeline is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    view = pipeline.adjust_view(req.dzoom, req.dx, req.dy, req.reset)
    # check_camera の終了時出力と同じ形式。次回起動にそのままコピペできる。
    print(
        f"[calib] CAM_ZOOM={view['zoom']} "
        f"CAM_OFFSET_X={view['offset_x']} CAM_OFFSET_Y={view['offset_y']}"
    )
    return view


@app.post("/calib/save")
async def calib_save():
    """
    現在のズーム/切り抜き位置をファイルに保存する(フロントの 's' キー)。

    次回起動時に config が読み込み、初期値として復元する。環境変数
    (CAM_ZOOM / CAM_OFFSET_*)で明示された場合はそちらが優先される。
    """
    if pipeline is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    view = pipeline.view()
    try:
        path = calib_store.save(view["zoom"], view["offset_x"], view["offset_y"])
    except OSError as e:
        # 保存に失敗しても稼働中の調整値はそのまま。フロントに失敗を伝えて終わる。
        print(f"[calib] 保存に失敗しました: {e}")
        return JSONResponse(
            {"status": "error", "message": str(e), **view}, status_code=500
        )
    print(
        f"[calib] 保存しました -> {path} "
        f"(CAM_ZOOM={view['zoom']} CAM_OFFSET_X={view['offset_x']} "
        f"CAM_OFFSET_Y={view['offset_y']})"
    )
    return {"status": "ok", "path": str(path), "file": path.name, **view}


# 説明文のキャッシュ (label -> text)。生成は秒単位の処理なので、同一ラベルの
# 再生成はしない。プロセスを起動し直せば消える(恒久保存はしない方針)。
_explain_cache: dict[str, str] = {}

# ラベルごとの生成ロック。窓を閉じてもサーバ側の生成は止まらないため、
# 「固定→窓を閉じる→固定解除→再固定」等で同じ未キャッシュのラベルに対する
# /explain が並行に走りうる。ロック無しだとどちらの生成由来かを区別できず、
# 先読み合成の断片(_speak_parts)が両者の文で混ざってしまう。ラベル単位で
# 直列化し、LLMの二重呼び出しも防ぐ。COCO/LVIS語彙は有限なので、ロックの
# 数が際限なく増える心配は無く、後始末も不要。
_explain_locks: dict[str, threading.Lock] = {}
_explain_locks_guard = threading.Lock()


def _explain_lock_for(label: str) -> threading.Lock:
    """ラベル専用の生成ロックを返す(無ければ作る)。"""
    with _explain_locks_guard:
        lock = _explain_locks.get(label)
        if lock is None:
            lock = threading.Lock()
            _explain_locks[label] = lock
        return lock

# 読み上げ音声のキャッシュ (label -> WAVバイト列)。説明文キャッシュと違い
# 1件あたり0.5〜1.0MBと大きいため、無制限には溜めず古いものから捨てる
# (32件で最大32MBほど。理由: docs/adr/0025-inspector-voice-readout.md)。
_speak_cache: OrderedDict[str, bytes] = OrderedDict()
_SPEAK_CACHE_MAX = 32

# --- 先読み合成(ストリーミングで確定した文を、鑑定文の生成中に裏で合成しておく) ---
# 鑑定文の生成には約7.5秒かかる(docs/adr/0024-llm-object-explanation.md)。その間に
# 文ごとの合成を進めておけば、全文が画面に出る頃には音声もほぼ揃っている。
#
# _speak_jobs: ラベルごとの「合成が完了したか」の合図。/speak はこれを待てる。
# _speak_cache と同じ上限を共有し、キャッシュから追い出したラベルのジョブも捨てる。
_speak_jobs: OrderedDict[str, threading.Event] = OrderedDict()
_SPEAK_JOBS_MAX = _SPEAK_CACHE_MAX

# ワーカースレッドだけが読み書きする作業領域。
# label -> 合成済みWAV断片の並び(結合前)。
_speak_parts: dict[str, list[bytes]] = {}
# label -> ここまでの断片合成がすべて成功しているか(1つでも失敗したら False)。
_speak_ok: dict[str, bool] = {}

# 合成タスクのキュー。要素は (label, 文) で、文が None ならそのラベルの
# 文がすべて出そろったことを示す「締め」の合図。
# 単一のワーカースレッドが順番に処理するため、同じラベルの文が並行合成されて
# 順序が狂うことはない。
_speak_queue: "queue.Queue[tuple[str, str | None]]" = queue.Queue()

# _speak_cache / _speak_jobs / _speak_parts / _speak_ok はメインスレッド(FastAPIの
# スレッドプール)とワーカースレッドの双方から触るため、変更はこのロックの下で行う。
_speak_lock = threading.Lock()


def _trim_speak_cache() -> None:
    """_speak_cache の上限超過分を古い順に捨てる。対応するジョブも一緒に捨てる。"""
    while len(_speak_cache) > _SPEAK_CACHE_MAX:
        old_label, _ = _speak_cache.popitem(last=False)
        _speak_jobs.pop(old_label, None)


def _trim_speak_jobs() -> None:
    """
    _speak_jobs 単体の上限。合成完了前(まだキャッシュに乗っていない)のジョブが
    大量に積み上がった場合の保険で、通常は _trim_speak_cache 側で一緒に減る。

    追い出す Event は捨てる前に必ず set() する。しないと、締め処理が
    _speak_jobs から見つけられずに set() し損ね、待っている /speak が
    TTS_TIMEOUT_SEC を丸ごと使い切ってしまう(その後キャッシュが無ければ
    退避路の同期合成に落ちるので、早く起こしてやるだけで無駄待ちが消える)。
    """
    while len(_speak_jobs) > _SPEAK_JOBS_MAX:
        _, event = _speak_jobs.popitem(last=False)
        event.set()


def _enqueue_speak_sentence(label: str, sentence: str) -> None:
    """一文を先読み合成キューに積む。ラベル初回ならジョブ(Event)も用意する。"""
    with _speak_lock:
        if label not in _speak_jobs:
            _speak_jobs[label] = threading.Event()
            _trim_speak_jobs()
        _speak_ok.setdefault(label, True)
    _speak_queue.put((label, sentence))


def _finalize_speak_job(label: str) -> None:
    """ラベルの文がすべて出そろったことをワーカーへ知らせる(締めの合図)。"""
    _speak_queue.put((label, None))


def _speak_worker() -> None:
    """
    先読み合成の専任スレッド。キューから (label, 文) を1件ずつ取り出し、
    順番に合成する。文が None のときは「締め」の合図で、それまでに合成できた
    断片を結合して _speak_cache へ格納し、待っている /speak を起こす。

    tts.TTSUnavailable に加え、concat_wav が壊れた断片に対して投げうる
    wave.Error/EOFError など想定外の例外もすべてここで受け止める。ここで
    ワーカーが死ぬとキューを処理する者が誰もいなくなり、以後 Event が二度と
    set() されず、あらゆる /speak が毎回 TTS_TIMEOUT_SEC 待ってから同期合成に
    落ちる劣化状態がプロセス終了まで続いてしまうため。
    """
    while True:
        label, sentence = _speak_queue.get()
        try:
            if sentence is None:
                parts = _speak_parts.pop(label, [])
                ok = _speak_ok.pop(label, False)
                if ok and parts:
                    try:
                        data = tts.concat_wav(parts)
                        with _speak_lock:
                            _speak_cache[label] = data
                            _trim_speak_cache()
                    except Exception as e:
                        print(f"[speak] 先読み結果の結合に失敗しました ({label}): {e}")
                with _speak_lock:
                    event = _speak_jobs.get(label)
                if event is not None:
                    event.set()
            else:
                try:
                    data = tts.synthesize(sentence)
                    _speak_parts.setdefault(label, []).append(data)
                except Exception as e:
                    print(f"[speak] 先読み合成に失敗しました ({label}): {e}")
                    _speak_ok[label] = False
        except Exception as e:
            # 上の分岐で捕まえきれない想定外の失敗の保険。ラベルの残骸を捨て、
            # 待っている /speak を解放してから次のジョブへ進む。
            print(f"[speak] ワーカーで想定外のエラー ({label}): {e}")
            _speak_parts.pop(label, None)
            _speak_ok.pop(label, None)
            with _speak_lock:
                event = _speak_jobs.get(label)
            if event is not None:
                event.set()
        finally:
            _speak_queue.task_done()


# デーモンスレッドとして常駐させる(プロセス終了時に道連れで終わってよい)。
threading.Thread(target=_speak_worker, daemon=True).start()

# 文の区切り。句読点(。！？!?)と改行を区切りとみなし、区切り文字は文に含めたまま切り出す。
_SENTENCE_END_RE = re.compile(r"[。！？!?\n]")


def _split_sentences(buffer: str) -> tuple[list[str], str]:
    """
    ストリーミング中のバッファから確定した文を切り出す。

    区切り文字(。！？!?・改行)を含めたまま各文を返し、まだ区切りが来ていない
    末尾は次回に持ち越すバッファとして返す。空白だけの断片は捨てる。
    """
    sentences: list[str] = []
    start = 0
    for m in _SENTENCE_END_RE.finditer(buffer):
        end = m.end()
        piece = buffer[start:end]
        if piece.strip():
            sentences.append(piece)
        start = end
    return sentences, buffer[start:]


class ExplainRequest(BaseModel):
    """説明を生成する物体。label は検出ラベル(英語)、ja はフロントの日本語表示名(任意)。"""

    label: str
    ja: str | None = None


def _ollama_explain(label: str, ja: str | None) -> str:
    """
    Ollama の /api/chat を呼び、物体の日本語説明文を得る。

    依存を増やさないため HTTP クライアントは stdlib の urllib で済ませる。
    この関数は同期 def なエンドポイントから呼ぶため、FastAPI がスレッドプールで
    実行し、イベントループを塞がない。

    ストリーミング(stream:True)で受け、一文が確定するたびに読み上げの先読み
    合成キューへ渡す。鑑定文の生成には約7.5秒かかるため、その裏で合成を
    進めておけば、全文が画面に出る頃には音声もほぼ揃っている。ただし合成は
    別スレッドで進むため、この関数(ひいては /explain)を遅らせることはない。
    """
    name = ja if ja else label
    # 鑑定士の人物像と2分岐ルール(昔からの道具は素直に/科学の産物は遺物として
    # 正体を明かさない)は実測で調整した。世界観の理由: docs/adr/0024-llm-object-explanation.md
    prompt = (
        "あなたは剣と魔法の異世界の「無口な職人鑑定士」。口数が少なく、"
        "事実と評価のみを淡々と述べる。今まさに手元にある物体を鑑定し、日本語でつぶやく。\n"
        f"物体名: {name} ({label})\n"
        "判定のルール:\n"
        "- 傘・杯・剣・本・果物など、文明の如何を問わず昔から存在しうる道具 → "
        "正体を素直に言い当て、用途や価値を語る。過度な神秘化はしない\n"
        "- 電気・機械・科学技術の産物(PC・携帯・カメラ・車など) → "
        "「古代文明の遺物」「謎の物体」として、正体を直接明かさず、"
        "見た目の観察と推測・畏怖を交えて語る\n"
        "- 2〜3文。感嘆詞(ふむ・ほう等)は使わない\n"
        "- 見出し・太字(*)・箇条書きなどの装飾記法は使わず、鑑定文だけを出力する"
    )
    # think:false は必須。付けないと英語の思考トレースが出て、時間も余計にかかる
    # (理由: docs/adr/0024-llm-object-explanation.md)
    body = json.dumps({
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "think": False,
        "options": {"temperature": 0.4, "num_predict": 150},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{config.OLLAMA_URL.rstrip('/')}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    buffer = ""                  # 区切りがまだ来ていない末尾の断片
    full_parts: list[str] = []   # 確定した文の並び。連結すると全文になる
    job_started = False          # 先読みジョブを1文でも積んだか(締めの合図が要るか)
    try:
        with urllib.request.urlopen(req, timeout=config.EXPLAIN_TIMEOUT_SEC) as resp:
            # NDJSON: 1行1JSON。message.content を連結して全文を組み立てる
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                chunk = json.loads(line)
                content = (chunk.get("message") or {}).get("content", "")
                if content:
                    buffer += content
                    sentences, buffer = _split_sentences(buffer)
                    for sentence in sentences:
                        full_parts.append(sentence)
                        _enqueue_speak_sentence(label, sentence)
                        job_started = True
                if chunk.get("done"):
                    break
        # 区切り文字が来ないまま終わった末尾も、最後の一文として合成に回す
        if buffer.strip():
            full_parts.append(buffer)
            _enqueue_speak_sentence(label, buffer)
            job_started = True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        # HTTPError(接続先の4xx/5xx)は URLError の派生なので、ここでまとめて受ける
        raise HTTPException(
            status_code=503,
            detail="鑑定の力 (Ollama) に接続できません。起動を確認してください (ollama serve)",
        ) from e
    finally:
        # 1文でも積んだなら、合成は別スレッドで進む。締めの合図を必ず送り、
        # 待っている /speak の Event が固まらないようにする。
        if job_started:
            _finalize_speak_job(label)
    text = "".join(full_parts).strip()
    if not text:
        raise HTTPException(
            status_code=503, detail="鑑定文の生成が空でした。もう一度お試しください"
        )
    return text


@app.post("/explain")
def explain(req: ExplainRequest):
    """
    物体ラベルから日本語の説明文を生成する(フロントの説明ウィンドウ用)。

    クラウドAPIではなくローカルLLM(Ollama)にした理由: docs/adr/0024-llm-object-explanation.md
    同一ラベルはキャッシュを返し、生成は1回だけ行う。

    同一ラベルの生成はラベル単位のロックで直列化する(single-flight)。理由:
    ロック無しで同じ未キャッシュのラベルに対する呼び出しが並行すると、
    先読み合成の断片(_speak_parts)が両方の生成の文で混ざってしまうため。
    """
    cached = _explain_cache.get(req.label)
    if cached is not None:
        return {"label": req.label, "text": cached}
    with _explain_lock_for(req.label):
        # ロック待ちの間に先行呼び出しが生成を終えているかもしれない。その場合は
        # LLMを呼ばずキャッシュを返す。
        cached = _explain_cache.get(req.label)
        if cached is not None:
            return {"label": req.label, "text": cached}
        text = _ollama_explain(req.label, req.ja)
        _explain_cache[req.label] = text
    return {"label": req.label, "text": text}


class SpeakRequest(BaseModel):
    """読み上げる鑑定文。label はキャッシュのキー、text は本文。"""

    label: str
    text: str


@app.post("/speak")
def speak(req: SpeakRequest):
    """
    鑑定文を読み上げた音声(WAV)を返す(フロントの説明ウィンドウ用)。

    合成は VOICEVOX ENGINE / macOS の say コマンドで行う(実装: backend/tts.py)。
    /explain のストリーミング中に文ごとの先読み合成(_speak_jobs)が進んでいれば
    その完了を待ってから返す。先読みが無い(または間に合わなかった)場合は、
    現状どおりその場で同期合成する(退避路)。同一ラベルはキャッシュを返し、
    再合成はしない。
    """
    with _speak_lock:
        cached = _speak_cache.get(req.label)
        if cached is not None:
            _speak_cache.move_to_end(req.label)
            return Response(content=cached, media_type="audio/wav")
        job = _speak_jobs.get(req.label)

    if job is not None:
        # /explain が先読み合成中(または合成済み)。終わるまで待ってからキャッシュを引く。
        job.wait(timeout=config.TTS_TIMEOUT_SEC)
        with _speak_lock:
            cached = _speak_cache.get(req.label)
            if cached is not None:
                _speak_cache.move_to_end(req.label)
                return Response(content=cached, media_type="audio/wav")

    # 先読みが無かった/間に合わなかった場合の退避路: その場で同期合成する。
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="読み上げる文がありません")
    try:
        data = tts.synthesize(text)
    except tts.TTSUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    with _speak_lock:
        _speak_cache[req.label] = data
        _trim_speak_cache()
    return Response(content=data, media_type="audio/wav")


@app.websocket("/ws")
async def ws_detections(ws: WebSocket):
    """検出結果JSONを一定fpsで送り続ける。"""
    await ws.accept()
    interval = 1.0 / max(1, config.STREAM_FPS)
    last_sent = -1
    try:
        while True:
            # 新しいフレームのときだけ組み立てて送る(無駄な再送とJSON化を防ぐ)
            if pipeline is not None and pipeline.frame_id != last_sent:
                payload = pipeline.detections_payload()
                last_sent = payload["frame_id"]
                await ws.send_json(payload)
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return
    except Exception:
        # クライアント切断時の各種例外は握りつぶして終了
        return


def _mjpeg_generator(annotate: bool):
    boundary = b"--frame"
    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), config.JPEG_QUALITY]
    last_sent = -1
    while True:
        if pipeline is None:
            time.sleep(0.05)
            continue
        # 推論fpsがSTREAM_FPSより低いと同じフレームが何度も来る。
        # snapshot() はフレームをコピーするので、送る分が無いなら呼ばない。
        if pipeline.frame_id == last_sent:
            time.sleep(0.005)
            continue
        fid, frame, dets = pipeline.snapshot()
        if frame is None:
            time.sleep(0.02)
            continue
        last_sent = fid
        if annotate:
            frame = Detector.annotate(frame, dets)
        ok, buf = cv2.imencode(".jpg", frame, encode_params)
        if not ok:
            continue
        yield (
            boundary + b"\r\n"
            + b"Content-Type: image/jpeg\r\n\r\n"
            + buf.tobytes() + b"\r\n"
        )
        time.sleep(1.0 / max(1, config.STREAM_FPS))


@app.get("/video")
async def video(annotate: bool = True):
    """
    注釈付きMJPEG。`?annotate=0` で枠・ラベルを焼き込まない素の映像になる。

    フロントは annotate=0 で読む (理由: docs/adr/0002-detection-streaming-contract.md)
    """
    return StreamingResponse(
        _mjpeg_generator(annotate),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


def main() -> None:
    import uvicorn

    print(f"[XR Analyze] http://{config.HOST}:{config.PORT}  (model={config.MODEL})")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
