"""
XRAnalyze.exe の入口 (PyInstaller がこのファイルから exe を組み立てる)。

中身は `python -m backend.server` と同じ。exe はエクスプローラーからダブルクリックで
起動されるため、次の二つを足してある (理由: docs/adr/0042-windows-exe-packaging.md)。

- エラーで終わったときは窓をすぐ閉じず、Enter を待ってから閉じる。
- コンソールの出力を exe の隣の XRAnalyze.log にも書く。ネイティブコード (torch /
  OpenCV / DirectShow) で落ちて窓ごと消えた場合でも、faulthandler がその時点の
  スタックを同じファイルへ書き残すので、後から原因を追える。
  前回の分は XRAnalyze.prev.log に残す (落ちた直後に起動し直しても消えないように)。
"""
from __future__ import annotations

import faulthandler
import multiprocessing
import os
import re
import sys
import time
import traceback
from pathlib import Path


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class _Tee:
    """書き込みをコンソールとログファイルの両方へ流す。それ以外は元のストリームに任せる。"""

    def __init__(self, stream, log):
        self._stream = stream
        self._log = log

    def write(self, data):
        try:
            # 色付け (uvicorn のログなど) の制御文字はファイルでは読みにくいだけなので落とす
            self._log.write(_ANSI.sub("", data))
            self._log.flush()
        except Exception:
            pass
        if self._stream is None:
            return len(data)
        return self._stream.write(data)

    def flush(self):
        try:
            self._log.flush()
        except Exception:
            pass
        if self._stream is not None:
            self._stream.flush()

    def isatty(self):
        return self._stream is not None and self._stream.isatty()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _setup_log():
    """exe の隣に XRAnalyze.log を開き、stdout/stderr と faulthandler をそこへ向ける。"""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    path = base / "XRAnalyze.log"
    try:
        if path.exists():
            os.replace(path, base / "XRAnalyze.prev.log")
        log = open(path, "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return None  # 書けない場所 (読み取り専用など) ならログ無しで続ける
    log.write(f"=== XRAnalyze 起動 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    faulthandler.enable(file=log, all_threads=True)
    sys.stdout = _Tee(sys.stdout, log)
    sys.stderr = _Tee(sys.stderr, log)
    return path


def _run() -> int:
    if not getattr(sys, "frozen", False):
        # ソースから起動したとき (XRAnalyze.bat → bootstrap.ps1) は、スクリプトの
        # フォルダ (packaging/) しか import 先に入らないので、リポジトリのルートを足す
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        # import も try の中に置く。依存の読み込み失敗 (DLL が見つからない等) でも
        # 窓をすぐ閉じず、理由を読めるようにするため
        from backend.server import main

        main()
    except SystemExit as e:  # uvicorn はポート使用中などで sys.exit(1) する
        if e.code is None:
            return 0
        return e.code if isinstance(e.code, int) else 1
    except KeyboardInterrupt:  # 起動途中の Ctrl+C は利用者が止めただけ
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    # torch / ultralytics が子プロセスを作る場合に、exe が自分自身を再起動して
    # しまわないようにする (PyInstaller で multiprocessing を使うときの決まり)
    multiprocessing.freeze_support()
    # コンソールの文字コードで表せない文字が出ても落ちないようにする
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    log_path = _setup_log()
    code = _run()
    if code:
        print("\nエラーで終了しました。上のメッセージを確認してください。")
        if log_path is not None:
            print(f"同じ内容を {log_path} に保存しています。")
        print("Enter キーで閉じます。")
        try:
            input()
        except Exception:
            pass
    sys.exit(code)
