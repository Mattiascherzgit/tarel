# Local retrieval

TAREL uses retrieval only to choose graph anchors. The graph compiler remains responsible for
tables, fields, reviewed relationships, expansion paths, and the final agent context.

```text
question -> BM25 + local vectors -> reciprocal-rank fusion
         -> object anchors -> reviewed graph expansion -> TAREL context
```

## Model and runtime

The recommended model is Qwen3-Embedding-0.6B in Q4_K_M GGUF form. TAREL's model registry pins the
community GGUF conversion to an immutable Hugging Face revision, exact byte size, and SHA-256. Its
upstream model is [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B); the
pinned conversion source is shown by `tarel model status --format json`. Both the upstream model
and pinned conversion declare the Apache-2.0 license. Optional runtime licenses are listed in the
[third-party notices](../THIRD_PARTY_NOTICES.md).

The model runs in-process on the CPU through the optional `llama-cpp-python` package. TAREL sets
`n_gpu_layers=0`, uses a 2048-token embedding context, batches index inputs, and applies the Qwen
retrieval instruction only to questions. No local generation model, reranker, API server,
LlamaIndex, vector database, Torch, or Sentence Transformers layer is involved.

An existing GGUF can be used without downloading another copy:

```bash
tarel index build adventureworks_dw --model /absolute/path/model.gguf
tarel context adventureworks_dw "sales per year" \
  --mode hybrid \
  --model /absolute/path/model.gguf
```

`TAREL_EMBEDDING_MODEL` can provide the same path for repeated commands. `TAREL_CACHE_DIR` changes
the download cache root; otherwise TAREL follows `XDG_CACHE_HOME` and then `~/.cache/tarel`.

## Persistence contract

The source of truth remains `.tarel/graphs/<graph>/graph.json`. The SQLite retrieval file contains
only rebuildable documents, normalized float32 vectors, and compatibility metadata:

- graph content hash;
- retrieval contract version;
- model identifier, path, and SHA-256;
- document count and vector dimensions.

Any graph or model mismatch is an error requiring an explicit index rebuild. The first version uses
a transparent linear cosine scan because DWH metadata corpora contain hundreds or a few thousand
documents, not millions. A specialized vector extension is deferred until measurements justify it.

## Data boundary

Retrieval documents are constructed from an allowlist. They may contain names, data types, key
flags, technical descriptions, annotation descriptions, roles, synonyms, and semantic types. They
never copy samples, connection strings, arbitrary metadata dictionaries, evidence values, or
provenance payloads. Generated indexes and downloaded GGUF files are excluded from Git and package
builds. The default projection includes draft, deferred, and validated annotations but excludes
rejected semantic claims. A review change makes an existing vector index stale and requires an
explicit rebuild.
