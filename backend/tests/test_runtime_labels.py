"""
vocab.runtime_labels (事前生成の既定対象) の unit test。

ultralytics のモデル読み込みは monkeypatch で差し替え、実モデルは読まない。
実行: .venv/bin/python -m pytest
"""
from __future__ import annotations

import sys
import types

import pytest

from backend import config as config_mod
from backend import vocab


def test_classes_take_precedence_without_loading_model(monkeypatch):
    """CLASSES があればその語彙をそのまま返し、モデルは読み込まない。"""
    monkeypatch.setattr(config_mod, "CLASSES", ["wallet", "watch"])
    # ultralytics を読みに行ったら失敗するように仕込む
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    assert vocab.runtime_labels() == ["wallet", "watch"]


def test_model_vocab_is_filtered_like_detector(monkeypatch):
    """CLASSES が無ければモデルの names を VOCAB で絞り、モデル語彙の名前で返す。"""
    monkeypatch.setattr(config_mod, "CLASSES", [])
    monkeypatch.setattr(config_mod, "MODEL", "dummy.pt")
    monkeypatch.setattr(config_mod, "VOCAB", "lvis")

    names = {0: "spray can", 1: "darkness", 2: "cup"}
    fake = types.ModuleType("ultralytics")
    fake.YOLO = lambda path: types.SimpleNamespace(names=names)
    monkeypatch.setitem(sys.modules, "ultralytics", fake)
    # LVIS 語彙は同義語込みの集合。モデル側の呼び名 "spray can" が残ること
    monkeypatch.setattr(vocab, "_lvis_terms", lambda: {"aerosol can", "spray can", "cup"})

    assert vocab.runtime_labels() == ["spray can", "cup"]


def test_unfiltered_when_policy_is_all(monkeypatch):
    monkeypatch.setattr(config_mod, "CLASSES", [])
    monkeypatch.setattr(config_mod, "MODEL", "dummy.pt")
    monkeypatch.setattr(config_mod, "VOCAB", "all")
    fake = types.ModuleType("ultralytics")
    fake.YOLO = lambda path: types.SimpleNamespace(names=["b", "a"])  # list 形式でも受ける
    monkeypatch.setitem(sys.modules, "ultralytics", fake)
    assert vocab.runtime_labels() == ["b", "a"]
