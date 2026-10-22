# onnx_embeddings.py DOX

## Purpose

- Own the `onnx_embeddings.py` helper module.
- Runs the local sentence-transformers embedding model through onnxruntime instead of torch/transformers.
- Keep this file-level DOX profile synchronized with `onnx_embeddings.py` because this directory is intentionally flat.

## Ownership

- `onnx_embeddings.py` owns the runtime implementation.
- `onnx_embeddings.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Classes:
- `OnnxSentenceTransformer` (no explicit base class)
  - `encode(self, sentences: str | Sequence[str], batch_size: int=..., convert_to_tensor: bool=..., **kwargs) -> np.ndarray`
- Top-level functions:
- `_repo_id(model: str) -> str`
- `_normalizes_output(repo: str, cache_dir: str | None) -> bool`

## Runtime Contracts

- Enabled by `A0_EMBEDDING_BACKEND=onnx` (environment or `usr/.env`); any other value keeps the sentence-transformers path. `models._get_local_embedding_model()` falls back to sentence-transformers with a warning when the backend cannot be built.
- `encode()` matches `SentenceTransformer.encode(texts, convert_to_tensor=False)` for the models it serves: mean pooling over the attention mask, then L2 normalisation when the model's `modules.json` ends with a `Normalize` module. On `all-MiniLM-L6-v2` the output is identical to sentence-transformers within float32 rounding (max abs diff < 2e-7), so stored vectors stay compatible.
- Tokens are truncated to 256 (the model card's window) and padded per batch; batches are capped at 32 texts.
- Files come from the HuggingFace cache (`onnx/model.onnx`, `tokenizer.json`, `modules.json`), so the download is shared with the sentence-transformers path and happens once.
- Observed side-effect areas: filesystem reads (HF cache download), CPU inference; no network calls after the files are cached.
- Imported dependency areas include: `json`, `os`, `threading`, `numpy`, `huggingface_hub`, `tokenizers`, `onnxruntime`.

## Work Guidance

- Keep the class duck-compatible with the slice of `SentenceTransformer` that `models.LocalSentenceTransformerWrapper` uses (`encode`, `model_name`); do not add torch imports here.
- Prefer failing over to sentence-transformers rather than raising from `models` when onnxruntime or the model files are unavailable.

## Verification

- `tests/test_embedding_model_preload.py` covers the preload/startup contract.
- Manual check used when adding the backend: compare `embed_documents()` against `SentenceTransformer.encode()` on sample texts (norms 1.0, max abs diff < 2e-7) and confirm the first use is ~1.7 s versus ~10 s and ~220 MB versus ~490 MB RSS.

## Child DOX Index

No child DOX files.
