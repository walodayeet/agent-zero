import sys
import types

import pytest

import models


@pytest.fixture(autouse=True)
def _reset_embedding_cache():
    models._LOCAL_EMBEDDING_MODELS.clear()
    yield
    models._LOCAL_EMBEDDING_MODELS.clear()


def test_onnx_backend_selected_when_enabled(monkeypatch):
    monkeypatch.setenv("A0_EMBEDDING_BACKEND", "onnx")
    import helpers.onnx_embeddings as onnx_embeddings

    created = {}

    class FakeOnnx:
        def __init__(self, model, **kwargs):
            created["model"] = model

    monkeypatch.setattr(onnx_embeddings, "OnnxSentenceTransformer", FakeOnnx)

    instance = models._get_local_embedding_model("all-MiniLM-L6-v2", {})

    assert isinstance(instance, FakeOnnx)
    assert created["model"] == "all-MiniLM-L6-v2"


def test_onnx_backend_falls_back_when_unavailable(monkeypatch):
    monkeypatch.setenv("A0_EMBEDDING_BACKEND", "onnx")
    import helpers.onnx_embeddings as onnx_embeddings

    class Broken:
        def __init__(self, model, **kwargs):
            raise RuntimeError("onnxruntime missing")

    monkeypatch.setattr(onnx_embeddings, "OnnxSentenceTransformer", Broken)

    class FakeSentenceTransformer:
        def __init__(self, model, **kwargs):
            self.model = model

    stub = types.ModuleType("sentence_transformers")
    stub.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", stub)

    instance = models._get_local_embedding_model("all-MiniLM-L6-v2", {})

    assert isinstance(instance, FakeSentenceTransformer)


def test_sentence_transformers_backend_remains_default(monkeypatch):
    monkeypatch.delenv("A0_EMBEDDING_BACKEND", raising=False)

    class FakeSentenceTransformer:
        def __init__(self, model, **kwargs):
            self.model = model

    stub = types.ModuleType("sentence_transformers")
    stub.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", stub)

    instance = models._get_local_embedding_model("all-MiniLM-L6-v2", {})

    assert isinstance(instance, FakeSentenceTransformer)
