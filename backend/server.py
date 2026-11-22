"""
配信サーバ (FastAPI)。

エンドポイント:
  GET  /            -> フロント(frontend/index.html)。検出を始める前は起動画面(setup.html)
  GET  /setup       -> 起動画面 (カメラと検出モデルを選んで検出を始める)
  GET  /setup/options      -> 接続中のカメラ・使える検出モデルの一覧と初期選択
  GET  /setup/thumb/{index} -> 一覧を作ったときに撮ったカメラの1枚 (JPEG)
  POST /setup/start        -> 選んだカメラ・モデルで検出を始める
  GET  /setup/status       -> 起動の進み具合 (selecting / starting / running / error)
  GET  /static/*    -> フロントの静的資源 (style.css / app.js)
  GET  /healthz     -> 稼働確認 + 実測fps
  WS   /ws          -> 検出結果JSONをSTREAM_FPSで配信 (Web/Unity/Android共通の契約)
  GET  /video       -> 注釈付きMJPEG (デバッグ確認用。ブラウザで直接開ける)
                       ?annotate=0 で枠を焼き込まない素の映像 (フロントはこちらを使う)
  POST /calib       -> 稼働中のズーム/切り抜き位置の調整 (グラスの視界合わせ)
  POST /calib/save  -> 現在の調整値をファイルへ保存 (次回起動時に自動で復元)
  POST /explain     -> 物体ラベルから日本語の説明文を生成 (Ollama のローカルLLM)
  POST /speak       -> 鑑定文の読み上げ音声(WAV)を返す (VOICEVOX / macOS の say)

起動:
  cd "XR_ Analyze"
  python3 -m backend.server               # 起動画面で選んでから検出を始める
  python3 -m backend.server --no-select   # 従来どおり環境変数の設定ですぐ始める
"""
from __future__ import annotations

import argparse
import asyncio
import os
import queue
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import calib_store, config, devices, models, speak_cache, startup, tts
# モジュール名を明示的に別名にするのは、下の /explain ハンドラ関数名が
# `explain` で、モジュール名とかぶるため(ハンドラ定義でグローバルの
# `explain` がハンドラ関数自身に上書きされてしまう)。
from . import explain as explain_mod
from .detector import Detector
from .paths import DATA_DIR, FROZEN, RESOURCE_DIR
from .pipeline import Pipeline

pipeline: Pipeline | None = None
FRONTEND_DIR = RESOURCE_DIR / "frontend"

# 終了の合図。Ctrl+C (SIGINT/SIGTERM) を受けた"瞬間"に立てる。/ws (async) と
# /video (同期ジェネレータ) の両方から読むため threading.Event を使う。
# lifespan の終了処理で立てるのでは遅い: lifespan の finally が走るのは
# uvicorn が「接続の終了待ち」を終えた後であり、その時点では /video の
# 無限ループ (while True) は自分から終わらないため、SHUTDOWN_GRACE_SEC 経過後の
# 強制キャンセルに必ず捕まってしまう(理由: docs/adr/0032-prompt-shutdown.md)。
# main() でシグナルハンドラに割り込み、キャンセルされる前にループへ知らせる。
_shutting_down = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline
    # 事前生成済みの鑑定文キャッシュを読み込む(理由: docs/adr/0038-explain-persistent-cache.md)。
    # ファイルが無い/壊れていても explain.load_cache 側で空dictに落として起動を止めない。
    _explain_cache.update(explain_mod.load_cache(config.EXPLAIN_CACHE_PATH))
    print(f"[explain] 永続キャッシュから {len(_explain_cache)} 件を読み込みました")
    if config.SETUP:
        # カメラとモデルは起動画面で選ぶ。検出は POST /setup/start から始まる
        # (理由: docs/adr/0043-startup-selection-screen.md)
        print(f"[setup] ブラウザで http://{_browser_host()}:{config.PORT}/ を開き、"
              "カメラと検出モデルを選んでください")
    else:
        pipeline = Pipeline()
        pipeline.start()
        _startup.mark_running({"cam_index": pipeline.cam_index, "model": pipeline.model})
    try:
        yield
    finally:
        _startup.cancel()
        if pipeline is not None:
            pipeline.stop()


app = FastAPI(title="XR Analyze backend", lifespan=lifespan)


def _set_pipeline(p: Pipeline) -> None:
    global pipeline
    pipeline = p


# 起動画面の状態。検出パイプラインはここから組み立てて pipeline に入れる。
_startup = startup.Startup(
    pipeline_factory=lambda cam_index, cam_name, model: Pipeline(cam_index, cam_name, model),
    on_running=_set_pipeline,
)


@app.get("/healthz")
async def healthz():
    if pipeline is None:
        return JSONResponse(
            {"status": "starting", "setup": _startup.state}, status_code=503
        )
    payload = pipeline.detections_payload()
    return {
        "status": "ok",
        "camera_status": payload["camera_status"],
        "fps": payload["fps"],
        "frame_id": payload["frame_id"],
        "model": pipeline.model,
        "device": config.DEVICE or "auto",
        "source_size": payload["source_size"],
        "error": payload["error"],
    }


# `/` は同じ URL のまま「開始前は起動画面、開始後は本画面」と中身が変わる。
# キャッシュの指定が無いと、ブラウザは Last-Modified から鮮度を推定し (経過時間の
# 1割、ファイルが3日前なら約7時間)、その間サーバに問い合わせずに古い起動画面を出す。
# 起動画面は「もう稼働中」と見て `/` へ移り、またキャッシュの起動画面が出る、という
# 再読み込みの無限ループになった。ページは常にサーバから取らせる
# (理由: docs/adr/0043-startup-selection-screen.md の追記)。
_NO_STORE = {"Cache-Control": "no-store"}


@app.get("/")
async def index():
    # 検出を始める前は起動画面を出す。始まった後は従来どおりの画面
    html = FRONTEND_DIR / ("index.html" if pipeline is not None else "setup.html")
    if html.exists():
        return FileResponse(str(html), headers=_NO_STORE)
    return JSONResponse(
        {"message": "backend running", "ws": "/ws", "video": "/video", "health": "/healthz"}
    )


@app.get("/setup")
async def setup_page():
    return FileResponse(str(FRONTEND_DIR / "setup.html"), headers=_NO_STORE)


# --- 起動画面 (理由: docs/adr/0043-startup-selection-screen.md) ---
# 一覧づくりはカメラを1台ずつ開くので数秒かかる。結果は取っておき、
# 「再読み込み」(refresh=1) のときだけ調べ直す。
_probe_lock = threading.Lock()
_probed: list[devices.CameraInfo] | None = None


def _probe(refresh: bool) -> list[devices.CameraInfo]:
    global _probed
    with _probe_lock:
        if _probed is None or refresh:
            print("[setup] 接続中のカメラを調べています...")
            _probed = devices.probe_cameras(
                config.CAM_PROBE_MAX, config.CAM_WIDTH, config.CAM_HEIGHT, config.CAM_FOURCC
            )
            for c in _probed:
                size = f"{c.width}x{c.height}" if c.readable else "読み取り失敗"
                print(f"[setup]   index {c.index}: {c.name or '(名前不明)'} [{size}]")
            if not _probed:
                print("[setup]   カメラが見つかりませんでした")
        return _probed


def _default_camera(cams: list[devices.CameraInfo]) -> int | None:
    """初期選択: CAM_INDEX 明示 > CAM_NAME の名前一致 > 最初に映ったカメラ。"""
    if not cams:
        return None
    indexes = [c.index for c in cams]
    if config.CAM_INDEX_ENV is not None and config.CAM_INDEX_ENV in indexes:
        return config.CAM_INDEX_ENV
    if config.CAM_NAME:
        for c in cams:
            if config.CAM_NAME.lower() in c.name.lower():
                return c.index
    readable = [c.index for c in cams if c.readable]
    return readable[0] if readable else indexes[0]


@app.get("/setup/options")
def setup_options(refresh: bool = False):
    if pipeline is not None or _startup.state == startup.STARTING:
        # 稼働中・立ち上げ中にカメラを開き直すと、使用中のカメラを奪い合う
        return JSONResponse(_startup.status(), status_code=409)
    cams = _probe(refresh)
    return {
        "cameras": [c.to_dict() for c in cams],
        "models": [m.to_dict() for m in models.list_models(config.MODEL)],
        "default": {"cam_index": _default_camera(cams), "model": config.MODEL},
        "device": config.DEVICE or "auto",
        "model_dir": str(DATA_DIR),
        **_startup.status(),
    }


@app.get("/setup/thumb/{index}")
def setup_thumb(index: int):
    for c in _probed or []:
        if c.index == index and c.thumbnail is not None:
            return Response(content=c.thumbnail, media_type="image/jpeg",
                            headers={"Cache-Control": "no-store"})
    raise HTTPException(status_code=404, detail="no thumbnail")


class SetupRequest(BaseModel):
    cam_index: int
    model: str


@app.post("/setup/start")
def setup_start(req: SetupRequest):
    model = req.model.strip()
    if not model:
        raise HTTPException(status_code=400, detail="モデルを選んでください")
    names = {c.index: c.name for c in _probed or []}
    # 一覧づくりとカメラの取り合いにならないよう、調べている最中なら待ってから始める
    with _probe_lock:
        started = _startup.start(req.cam_index, names.get(req.cam_index, ""), model)
    if not started:
        return JSONResponse(_startup.status(), status_code=409)
    return _startup.status()


@app.get("/setup/status")
async def setup_status():
    return _startup.status()


class _RevalidatedStaticFiles(StaticFiles):
    """
    使うたびにサーバへ確かめさせる (Cache-Control: no-cache) 静的配信。

    指定が無いと、ZIP を展開し直して JS を更新しても、ブラウザが推定した鮮度の間は
    古い JS が使われる。no-cache なら ETag で確かめるだけなので、変わっていなければ
    304 で済み、転送は増えない。
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# フロントの静的資源 (style.css / app.js) の配信。index.html だけは上の / から
# FileResponse で配るので、ここへ含める必要はない。
app.mount("/static", _RevalidatedStaticFiles(directory=FRONTEND_DIR), name="static")


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
# 再生成はしない。起動時に EXPLAIN_CACHE_PATH から事前生成分を読み込み、新規に
# 生成したラベルはその都度ファイルへ書き戻す(永続化。理由: docs/adr/0038-explain-persistent-cache.md)。
_explain_cache: dict[str, str] = {}

# _explain_cache をファイルへ保存する処理の直列化用ロック。ラベル単位の
# _explain_locks とは別物: 保存のたびに _explain_cache 全体を書き出すため、
# 異なるラベルの生成が同時に完了しても書き込み同士が競合しないようにする。
_explain_cache_write_lock = threading.Lock()

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

# 合成タスクのキュー。要素は (label, 文, 締めの全文) の3つ組。
#   - 文の合成: (label, 文, None)
#   - 締めの合図: (label, None, 全文)  文が出そろったラベルの確定した本文を運ぶ。
#     ディスクキャッシュ (speak_cache) は本文のハッシュをキーに含むため、
#     ワーカー自身は本文を持たず、締めの合図に載せてもらう必要がある。
# 単一のワーカースレッドが順番に処理するため、同じラベルの文が並行合成されて
# 順序が狂うことはない。
_speak_queue: "queue.Queue[tuple[str, str | None, str | None]]" = queue.Queue()

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
    _speak_queue.put((label, sentence, None))


def _finalize_speak_job(label: str, text: str = "") -> None:
    """
    ラベルの文がすべて出そろったことをワーカーへ知らせる(締めの合図)。

    text は確定した鑑定文の全文。ディスクキャッシュへの保存に使うため、
    合成結果とセットで持たせる。呼び出し側で全文が確定していない
    (= 例外で打ち切られた)場合は空文字のままでよく、その場合ワーカーは
    ディスクへ保存しない。
    """
    _speak_queue.put((label, None, text))


def _speak_worker() -> None:
    """
    先読み合成の専任スレッド。キューから (label, 文, 締めの全文) を1件ずつ
    取り出し、順番に合成する。文が None のときは「締め」の合図で、それまでに
    合成できた断片を結合して _speak_cache へ格納し(あわせてディスクキャッシュ
    speak_cache へも保存し)、待っている /speak を起こす。

    tts.TTSUnavailable に加え、concat_wav が壊れた断片に対して投げうる
    wave.Error/EOFError など想定外の例外もすべてここで受け止める。ここで
    ワーカーが死ぬとキューを処理する者が誰もいなくなり、以後 Event が二度と
    set() されず、あらゆる /speak が毎回 TTS_TIMEOUT_SEC 待ってから同期合成に
    落ちる劣化状態がプロセス終了まで続いてしまうため。
    """
    while True:
        label, sentence, full_text = _speak_queue.get()
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
                        # 本文が確定している(= 正常終了)場合だけディスクへ保存する。
                        # 例外で打ち切られた場合は full_text が空文字のままなので
                        # store は no-op 相当(speak_cache.store 自体は空文字でも
                        # 動くが、意図的に「本文とずれた保存」を避けるため呼ばない)。
                        if full_text:
                            speak_cache.store(label, full_text, data)
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


class ExplainRequest(BaseModel):
    """説明を生成する物体。label は検出ラベル(英語)、ja はフロントの日本語表示名(任意)。"""

    label: str
    ja: str | None = None


def _explain_and_prefetch_speech(label: str, ja: str | None) -> str:
    """
    explain.generate を呼んで鑑定文を得つつ、確定した文から読み上げの先読み
    合成キューへ積む(_enqueue_speak_sentence)。生成そのものの実装(プロンプト・
    NDJSON受信・文分割)は explain.py に切り出してあり、ここは「1文でも積んだら
    締めの合図(_finalize_speak_job)を送る」という server 側の責務だけを持つ。
    ただし合成は別スレッドで進むため、この関数(ひいては /explain)を遅らせることはない。
    """
    job_started = False  # 先読みジョブを1文でも積んだか(締めの合図が要るか)
    text = ""            # 確定した全文。正常終了時のみ埋まる(締めの合図に載せる)

    def on_sentence(sentence: str) -> None:
        nonlocal job_started
        _enqueue_speak_sentence(label, sentence)
        job_started = True

    try:
        text = explain_mod.generate(label, ja, on_sentence=on_sentence)
        return text
    finally:
        # 1文でも積んだなら、合成は別スレッドで進む。締めの合図を必ず送り、
        # 待っている /speak の Event が固まらないようにする。text を渡すと
        # ワーカー側で結合後にディスクキャッシュへも保存する(空文字なら保存しない)。
        if job_started:
            _finalize_speak_job(label, text)


@app.post("/explain")
def explain(req: ExplainRequest):
    """
    物体ラベルから日本語の説明文を生成する(フロントの説明ウィンドウ用)。

    クラウドAPIではなくローカルLLM(Ollama)にした理由: docs/adr/0024-llm-object-explanation.md
    同一ラベルはキャッシュを返し、生成は1回だけ行う。プロセス内キャッシュに無くても
    起動時に読み込んだ永続キャッシュ(EXPLAIN_CACHE_PATH)に載っていればそれを返す
    (理由: docs/adr/0038-explain-persistent-cache.md)。

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
        try:
            text = _explain_and_prefetch_speech(req.label, req.ja)
        except (explain_mod.ExplainUnavailable, explain_mod.ExplainEmpty) as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        _explain_cache[req.label] = text
        # 永続キャッシュへ書き戻す。失敗しても生成自体は成功しているので 500 にはしない。
        with _explain_cache_write_lock:
            try:
                explain_mod.save_cache(config.EXPLAIN_CACHE_PATH, dict(_explain_cache))
            except OSError as e:
                print(f"[explain] 永続キャッシュの保存に失敗しました: {e}")
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
    現状どおりその場で同期合成する(退避路)。同一ラベルはメモリキャッシュを
    返し、再合成はしない。

    メモリキャッシュ(プロセス再起動で消える)にも無ければ、ディスクの永続
    キャッシュ(backend/speak_cache.py)を試す。本文のハッシュも一致していれば
    合成をスキップしてそのまま返せる(理由: docs/adr/0039-speak-persistent-cache.md)。
    退避路・先読みで新たに合成した結果はディスクにも書き、次回以降に備える。
    """
    with _speak_lock:
        cached = _speak_cache.get(req.label)
        if cached is not None:
            _speak_cache.move_to_end(req.label)
            return Response(content=cached, media_type="audio/wav")

    disk_cached = speak_cache.load(req.label, req.text)
    if disk_cached is not None:
        with _speak_lock:
            _speak_cache[req.label] = disk_cached
            _trim_speak_cache()
        return Response(content=disk_cached, media_type="audio/wav")

    with _speak_lock:
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
    speak_cache.store(req.label, text, data)
    return Response(content=data, media_type="audio/wav")


@app.websocket("/ws")
async def ws_detections(ws: WebSocket):
    """検出結果JSONを一定fpsで送り続ける。"""
    await ws.accept()
    interval = 1.0 / max(1, config.STREAM_FPS)
    last_sent = -1
    try:
        while True:
            # 終了の合図が立っていたら、接続を保ったまま自分から抜ける
            # (理由: docs/adr/0032-prompt-shutdown.md の追補。uvicorn の強制キャンセル
            # 経路に入る前に閉じることで、CancelledError のトレースバックを防ぐ)
            if _shutting_down.is_set():
                return
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
        # 終了の合図が立っていたら自分から抜ける(理由: ws_detections と同じ。
        # docs/adr/0032-prompt-shutdown.md の追補)。time.sleep で待っている間に
        # 立つので、ループの先頭で見るだけでよい。
        if _shutting_down.is_set():
            return
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


def _browser_host() -> str:
    # 0.0.0.0 などで全体に公開していても、自分のブラウザからは 127.0.0.1 で開ける
    return "127.0.0.1" if config.HOST in ("0.0.0.0", "::", "") else config.HOST


def _open_browser_when_ready(server) -> None:
    """サーバが接続を受け付け始めたらブラウザで画面を開く (exe の既定動作)。"""
    import webbrowser

    def wait_and_open() -> None:
        deadline = time.time() + 60
        while not server.started and time.time() < deadline:
            if server.should_exit:
                return
            time.sleep(0.1)
        if server.started:
            # 時刻を付けて毎回別の URL にする。この修正より前の版で、ブラウザに `/` の
            # 起動画面がキャッシュされたまま残っていても、それを使わせないため
            webbrowser.open(f"http://{_browser_host()}:{config.PORT}/?t={int(time.time())}")

    threading.Thread(target=wait_and_open, daemon=True).start()


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="XR Analyze backend")
    ap.add_argument("--no-select", action="store_true",
                    help="起動画面を出さず、環境変数の設定 (CAM_INDEX/MODEL など) ですぐ検出を始める")
    ap.add_argument("--no-browser", action="store_true", help="起動後にブラウザを開かない")
    ap.add_argument("--open-browser", action="store_true", help="起動後にブラウザを開く")
    args = ap.parse_args(argv)
    if args.no_select:
        config.SETUP = False
    if args.no_browser:
        config.OPEN_BROWSER = False
    elif args.open_browser:
        config.OPEN_BROWSER = True

    if FROZEN:
        # 名前だけで指定したモデルを ultralytics がカレントへダウンロードするので、
        # exe の隣 (DATA_DIR) をカレントにして保存先をそろえる
        # (理由: docs/adr/0042-windows-exe-packaging.md)
        os.chdir(DATA_DIR)

    model_note = "起動画面で選択" if config.SETUP else config.MODEL
    print(f"[XR Analyze] http://{_browser_host()}:{config.PORT}  (model={model_note})")
    # timeout_graceful_shutdown を渡さないと、Ctrl+C のあと処理中の /explain や
    # /speak が終わるまで待ち続ける (理由: docs/adr/0032-prompt-shutdown.md)。
    # これは保険として残す。/ws・/video が何らかの理由で自分から抜けられなかった
    # 場合の最後の砦になる。
    server_config = uvicorn.Config(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info",
        timeout_graceful_shutdown=config.SHUTDOWN_GRACE_SEC,
    )
    server = uvicorn.Server(server_config)

    # Ctrl+C (SIGINT) / SIGTERM を受けた"瞬間"に _shutting_down を立てる。
    # uvicorn.Server.handle_exit はシグナルハンドラそのものなので、ここに割り込めば
    # 「接続の終了待ち」が始まるより前に /ws・/video のループへ知らせられる。
    # lifespan の終了処理(finally)で立てるのでは遅く、その時点では既に
    # SHUTDOWN_GRACE_SEC 超過の強制キャンセルに入ってしまっている。
    original_handle_exit = server.handle_exit

    def handle_exit(sig, frame):
        _shutting_down.set()
        original_handle_exit(sig, frame)

    server.handle_exit = handle_exit
    if config.OPEN_BROWSER:
        _open_browser_when_ready(server)
    try:
        server.run()
    except KeyboardInterrupt:
        # uvicorn は graceful shutdown 完了後に SIGINT を再送出する (capture_signals)。
        # これは仕様であり、サーバ自体は正常終了済み。トレースバック表示を抑えるため
        # ここで吸収する。
        pass


if __name__ == "__main__":
    main()
