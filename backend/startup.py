"""
起動画面の状態管理: カメラとモデルを選んでから検出パイプラインを立ち上げる。

状態は次の4つで、ブラウザの起動画面は /setup/status をポーリングして遷移を追う
(理由: docs/adr/0043-startup-selection-screen.md)。

    selecting ──start()──> starting ──成功──> running
        ^                      │
        └──── start() ──── error <─失敗─┘   (選び直して再挑戦できる)

モデルの読み込み (初回はダウンロード) とカメラのオープンは数秒〜数十秒かかるため、
start() は背景スレッドで行い、すぐに返る。
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

SELECTING = "selecting"
STARTING = "starting"
RUNNING = "running"
ERROR = "error"


class Startup:
    """
    pipeline_factory(cam_index, cam_name, model) がパイプラインを組み立てて返す。
    組み立てたものは start() してから on_running に渡す。start() に失敗したら
    stop() して片付け、状態を error にする。
    """

    def __init__(
        self,
        pipeline_factory: Callable[[int, str, str], object],
        on_running: Callable[[object], None],
    ) -> None:
        self._factory = pipeline_factory
        self._on_running = on_running
        self._lock = threading.Lock()
        self._state = SELECTING
        self._message = ""
        self._choice: dict = {}
        self._thread: Optional[threading.Thread] = None
        self._cancelled = threading.Event()

    def status(self) -> dict:
        with self._lock:
            return {"state": self._state, "message": self._message, "choice": dict(self._choice)}

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def mark_running(self, choice: dict) -> None:
        """起動画面を通さずに起動した (SETUP=0) ことを記録する。"""
        with self._lock:
            self._state = RUNNING
            self._message = ""
            self._choice = dict(choice)

    def start(self, cam_index: int, cam_name: str, model: str) -> bool:
        """
        選んだ構成で立ち上げを始める。既に立ち上げ中・稼働中なら何もせず False。
        """
        with self._lock:
            if self._state in (STARTING, RUNNING):
                return False
            self._state = STARTING
            self._message = "モデルを読み込んでいます (初回はダウンロードに時間がかかります)"
            self._choice = {"cam_index": cam_index, "cam_name": cam_name, "model": model}
        self._thread = threading.Thread(
            target=self._run, args=(cam_index, cam_name, model), daemon=True
        )
        self._thread.start()
        return True

    def cancel(self) -> None:
        """サーバ終了時に呼ぶ。立ち上げ中なら、終わった時点でパイプラインを止める。"""
        self._cancelled.set()

    def join(self, timeout: float) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _set(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
            self._message = message

    def _run(self, cam_index: int, cam_name: str, model: str) -> None:
        try:
            pipeline = self._factory(cam_index, cam_name, model)
        except Exception as e:
            print(f"[setup] モデル {model} を読み込めませんでした: {e}")
            self._set(ERROR, f"モデル {model} を読み込めませんでした: {e}")
            return

        self._set(STARTING, "カメラを開いています")
        try:
            pipeline.start()
        except Exception as e:
            print(f"[setup] カメラ index={cam_index} を開けませんでした: {e}")
            try:
                pipeline.stop()
            except Exception:
                pass
            self._set(ERROR, f"カメラ index={cam_index} を開けませんでした: {e}")
            return

        if self._cancelled.is_set():
            # 立ち上げ中にサーバが止められた。掴んだカメラを放して終わる。
            pipeline.stop()
            return
        self._on_running(pipeline)
        self._set(RUNNING, "")
        print(f"[setup] 検出を開始しました (camera index={cam_index}, model={model})")
