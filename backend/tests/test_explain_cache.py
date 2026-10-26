"""
鑑定文の生成 (backend/explain.py) と永続キャッシュの unit test
(docs/adr/0038-explain-persistent-cache.md)。

Ollama / VOICEVOX には一切接続しない。urllib.request.urlopen を monkeypatch した
擬似 NDJSON ストリームで generate() を検証し、キャッシュの読み書きはファイル
システム(tmp_path)だけで完結させる。

実行: .venv/bin/python -m pytest
"""
from __future__ import annotations

import json

from backend import config as config_mod
from backend import explain
from backend import server


class _FakeStreamResponse:
    """urlopen() の戻り値を模す。for 文で1行(bytes)ずつ返す NDJSON ストリーム。"""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._lines)


def _ndjson(*contents_and_done: tuple[str, bool]) -> list[bytes]:
    """(content, done) の並びから NDJSON の行(bytes)一覧を組み立てる。"""
    lines = []
    for content, done in contents_and_done:
        chunk = {"message": {"content": content}, "done": done}
        lines.append((json.dumps(chunk) + "\n").encode("utf-8"))
    return lines


# --- save_cache / load_cache ---

def test_save_and_load_cache_roundtrip(tmp_path):
    path = tmp_path / "explain_cache.json"
    data = {"cup": "これはカップです。", "car": "これは車です。"}
    explain.save_cache(str(path), data)

    assert path.exists()
    # atomic replace の中間ファイルが残っていないこと
    assert not path.with_name(path.name + ".tmp").exists()
    assert explain.load_cache(str(path)) == data


def test_load_cache_missing_file_returns_empty(tmp_path):
    path = tmp_path / "does_not_exist.json"
    assert explain.load_cache(str(path)) == {}


def test_load_cache_corrupted_returns_empty(tmp_path, capsys):
    path = tmp_path / "explain_cache.json"
    path.write_text("{not valid json", encoding="utf-8")

    result = explain.load_cache(str(path))

    assert result == {}
    assert "[explain]" in capsys.readouterr().out


def test_cache_disabled_when_path_empty(tmp_path):
    # 空文字は永続化無効の意味。save_cache は例外を投げず何もしない。
    explain.save_cache("", {"cup": "これはカップです。"})
    assert explain.load_cache("") == {}


# --- generate() の NDJSON パース ---

def test_generate_calls_on_sentence_per_sentence(monkeypatch):
    lines = _ndjson(
        ("これはテストです。", False),
        ("次の文です。", False),
        ("", True),
    )
    monkeypatch.setattr(
        explain.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeStreamResponse(lines),
    )

    sentences: list[str] = []
    text = explain.generate("cup", "カップ", on_sentence=sentences.append)

    assert sentences == ["これはテストです。", "次の文です。"]
    assert text == "これはテストです。次の文です。"


def test_generate_without_on_sentence_returns_full_text(monkeypatch):
    # 区切り文字が来ないまま終わる断片も、最後の一文として全文に含まれること
    lines = _ndjson(("句読点のない断片", False), ("", True))
    monkeypatch.setattr(
        explain.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeStreamResponse(lines),
    )

    text = explain.generate("cup", None, on_sentence=None)

    assert text == "句読点のない断片"


def test_generate_raises_explain_unavailable_on_connection_error(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(explain.urllib.request, "urlopen", fake_urlopen)

    try:
        explain.generate("cup", None)
        assert False, "ExplainUnavailable が送出されるはず"
    except explain.ExplainUnavailable:
        pass


def test_generate_raises_explain_empty_on_blank_response(monkeypatch):
    lines = _ndjson(("", True))
    monkeypatch.setattr(
        explain.urllib.request, "urlopen",
        lambda req, timeout=None: _FakeStreamResponse(lines),
    )

    try:
        explain.generate("cup", None)
        assert False, "ExplainEmpty が送出されるはず"
    except explain.ExplainEmpty:
        pass


# --- /explain が新規生成後にファイルへ保存すること ---

def test_explain_endpoint_persists_new_generation(monkeypatch, tmp_path):
    cache_path = tmp_path / "explain_cache.json"
    monkeypatch.setattr(config_mod, "EXPLAIN_CACHE_PATH", str(cache_path))
    server._explain_cache.clear()

    def fake_generate(label, ja, on_sentence=None):
        return "テストの鑑定文。"

    monkeypatch.setattr(explain, "generate", fake_generate)

    req = server.ExplainRequest(label="test_label", ja="テスト")
    result = server.explain(req)

    assert result == {"label": "test_label", "text": "テストの鑑定文。"}
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert saved == {"test_label": "テストの鑑定文。"}

    # プロセス内キャッシュにも入り、2回目は fake_generate を呼ばずに返る
    monkeypatch.setattr(
        explain, "generate",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("再生成された")),
    )
    result2 = server.explain(server.ExplainRequest(label="test_label"))
    assert result2 == {"label": "test_label", "text": "テストの鑑定文。"}
