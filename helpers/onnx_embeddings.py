"""ONNX Runtime backend for local sentence-transformers embeddings.

The default local embedding model (``sentence-transformers/all-MiniLM-L6-v2``)
only needs a transformer forward pass, mean pooling and L2 normalisation. Loading
it through ``sentence-transformers`` pulls in torch and transformers (~410 MB RSS,
~10 s on Windows); the ONNX export of the same weights runs on onnxruntime with
~220 MB and loads in well under a second, and produces identical vectors.

Enable with ``A0_EMBEDDING_BACKEND=onnx`` (environment or ``usr/.env``). Any other
value keeps the sentence-transformers path.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Iterable, Sequence

import numpy as np

# Keep the prompt/memory text window aligned with the model card (256 tokens).
_MAX_TOKENS = 256
_BATCH_SIZE = 32


def _repo_id(model: str) -> str:
    return model if "/" in model else f"sentence-transformers/{model}"


def _normalizes_output(repo: str, cache_dir: str | None) -> bool:
    """True when the sentence-transformers pipeline ends with a Normalize module."""
    from huggingface_hub import hf_hub_download

    try:
        path = hf_hub_download(repo, "modules.json", cache_dir=cache_dir)
        modules = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return True  # the MiniLM defaults normalise; keep parity when unsure
    for module in modules if isinstance(modules, list) else []:
        if str(module.get("type", "")).endswith("Normalize"):
            return True
    return False


class OnnxSentenceTransformer:
    """Drop-in replacement for the slice of ``SentenceTransformer`` used by A0.

    ``models.LocalSentenceTransformerWrapper`` only calls ``encode(texts,
    convert_to_tensor=False)``; this class mirrors that contract, including the
    normalisation applied by the model's sentence-transformers pipeline.
    """

    def __init__(self, model: str, cache_folder: str | None = None, **kwargs: Any):
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
        import onnxruntime as ort

        self.model_name = model
        self.repo = _repo_id(model)
        cache_dir = cache_folder or None
        self._tokenizer = Tokenizer.from_file(
            hf_hub_download(self.repo, "tokenizer.json", cache_dir=cache_dir)
        )
        self._tokenizer.enable_truncation(max_length=_MAX_TOKENS)
        self._tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        # The CPU arena preallocates buffers we do not reuse across queries; with it
        # off RSS is ~40 MB lower at identical encode speed (measured 47 ms / 20x5).
        options.enable_cpu_mem_arena = False
        self._session = ort.InferenceSession(
            hf_hub_download(self.repo, "onnx/model.onnx", cache_dir=cache_dir),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = {item.name for item in self._session.get_inputs()}
        self._normalize = _normalizes_output(self.repo, cache_dir)
        self._lock = threading.Lock()

    # -- inference ---------------------------------------------------------
    def _encode_batch(self, texts: Sequence[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(list(texts))
        width = max((len(encoding.ids) for encoding in encodings), default=1) or 1
        ids = np.zeros((len(encodings), width), dtype=np.int64)
        mask = np.zeros_like(ids)
        for index, encoding in enumerate(encodings):
            length = len(encoding.ids)
            ids[index, :length] = encoding.ids
            mask[index, :length] = encoding.attention_mask
        inputs = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            inputs["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, inputs)[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.maximum(
            weights.sum(axis=1), 1e-9
        )
        if self._normalize:
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            pooled = pooled / np.maximum(norms, 1e-12)
        return pooled.astype(np.float32)

    def encode(
        self,
        sentences: str | Sequence[str],
        batch_size: int = _BATCH_SIZE,
        convert_to_tensor: bool = False,
        **kwargs: Any,
    ) -> np.ndarray:
        if isinstance(sentences, str):
            sentences = [sentences]
        texts = [str(sentence) for sentence in sentences] or [""]
        chunks = [
            self._encode_batch(texts[index : index + batch_size])
            for index in range(0, len(texts), batch_size)
        ]
        return np.concatenate(chunks, axis=0)
