"""
XRAnalyze.exe の入口 (PyInstaller がこのファイルから exe を組み立てる)。

中身は `python -m backend.server` と同じ。exe はエクスプローラーからダブルクリックで
起動されるため、エラーで終わったときに窓が一瞬で閉じて理由が読めない、という
ことが無いよう、失敗時だけ Enter を待ってから閉じる
(理由: docs/adr/0042-windows-exe-packaging.md)。
"""
from __future__ import annotations

import multiprocessing
import sys
import traceback


def _run() -> int:
    try:
        # import も try の中に置く。依存の読み込み失敗 (DLL が見つからない等) でも
        # 窓をすぐ閉じず、理由を読めるようにするため
        from backend.server import main

        main()
    except SystemExit as e:  # uvicorn はポート使用中などで sys.exit(1) する
        if e.code is None:
            return 0
        return e.code if isinstance(e.code, int) else 1
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
    code = _run()
    if code:
        print("\nエラーで終了しました。上のメッセージを確認してください。Enter キーで閉じます。")
        try:
            input()
        except Exception:
            pass
    sys.exit(code)
