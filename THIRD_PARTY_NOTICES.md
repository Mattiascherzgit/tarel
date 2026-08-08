# Third-party components

TAREL's base installation has no Python runtime dependencies. The optional local browser UI
bundles Cytoscape.js as a static browser asset. The remaining components are installed or
downloaded only when a user explicitly enables their capability.

| Component | TAREL capability | Version or artifact | License |
|---|---|---|---|
| [Cytoscape.js](https://js.cytoscape.org/) | Local graph and lineage browser | `3.34.0` (bundled static asset) | MIT |
| [pymssql](https://github.com/pymssql/pymssql) | SQL Server connector | `>=2.3,<3` | [LGPL-2.1](https://github.com/pymssql/pymssql/blob/master/LICENSE) |
| [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) | Local CPU embeddings | `>=0.3.16,<0.4` | [MIT](https://github.com/abetlen/llama-cpp-python/blob/main/LICENSE.md) |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | Native runtime used by `llama-cpp-python` | selected by `llama-cpp-python` | [MIT](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) |
| [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | Recommended embedding model | upstream model | Apache-2.0 |
| [Qwen3-Embedding-0.6B Q4_K_M GGUF](https://huggingface.co/enacimie/Qwen3-Embedding-0.6B-Q4_K_M-GGUF) | Recommended quantized artifact | revision `51fe2a65af23d8cfd3c9c1d89846cf9073f8902b` | Apache-2.0 |

The GGUF artifact is downloaded only by `tarel model download`. TAREL pins its immutable revision,
expected size, and SHA-256 checksum. Users who supply a different model are responsible for that
model's license and usage terms.

## Known optional transitive advisory

`llama-cpp-python` currently depends on `diskcache`, whose default pickle-based disk cache is
covered by [GHSA-w8v5-vhqr-4h9v](https://github.com/advisories/GHSA-w8v5-vhqr-4h9v). TAREL does
not create or read a `LlamaDiskCache`: its llama.cpp cache remains disabled, and TAREL persists
retrieval vectors in its own SQLite index. Integrations must not attach a disk cache to TAREL's
embedding model or expose cache directories to untrusted writers. The exception should be removed
when the upstream dependency publishes a fixed release.
