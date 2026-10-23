from types import SimpleNamespace


def test_onnx_backend_forwards_revision_and_token_to_all_hub_assets(monkeypatch):
    import helpers.onnx_embeddings as onnx

    calls = []

    class FakeTokenizer:
        @classmethod
        def from_file(cls, path):
            return cls()

        def enable_truncation(self, **_kwargs):
            pass

        def enable_padding(self):
            pass

    class FakeSessionOptions:
        pass

    class FakeSession:
        def __init__(self, path, **_kwargs):
            pass

        def get_inputs(self):
            return []

    fake_ort = SimpleNamespace(
        SessionOptions=FakeSessionOptions,
        GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL=1),
        InferenceSession=FakeSession,
    )
    monkeypatch.setattr(onnx, "_hub_download", lambda repo, filename, **kwargs: (
        calls.append((repo, filename, kwargs.copy())) or filename
    ), raising=False)
    monkeypatch.setitem(__import__("sys").modules, "tokenizers", SimpleNamespace(Tokenizer=FakeTokenizer))
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", fake_ort)
    # The module imports these lazily inside __init__, so patch their source module.
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", lambda repo, filename, **kwargs: (
        calls.append((repo, filename, kwargs.copy())) or filename
    ))

    onnx.OnnxSentenceTransformer(
        "sentence-transformers/test-model",
        revision="pinned-rev",
        use_auth_token="test-token",
    )

    assert {name for _repo, name, _kwargs in calls} == {
        "tokenizer.json", "onnx/model.onnx", "modules.json"
    }
    assert all(kwargs["revision"] == "pinned-rev" for _, _, kwargs in calls)
    assert all(kwargs["token"] == "test-token" for _, _, kwargs in calls)
