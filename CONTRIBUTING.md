# Contributing to TAREL

TAREL welcomes focused bug fixes, documentation improvements, and small features that strengthen
its analytics context compiler.

## Before changing code

- Open an issue before changing a core contract, graph identity rule, persisted format, connector
  boundary, or context packet.
- Keep generated connectors inactive until a human has reviewed their permissions and queries.
- Never commit credentials, connection URLs, local database targets, samples, `.tarel/` state, or
  model files.
- Keep new runtime dependencies optional unless the core cannot reasonably provide the required
  behavior with the Python standard library.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Before opening a pull request, run:

```bash
ruff check src tests tools
python -m unittest discover -s tests -q
python -m compileall -q src tests tools
python -m build
python tools/check_distribution.py dist
```

Pull requests should explain the concrete use case, the evidence behind semantic or connector
behavior, and any intentionally changed CLI or serialized output. Add focused tests for behavior
that could regress. Live-system tests must use ignored local configuration and must never expose
their targets in logs or fixtures.

By submitting a contribution, you agree that it is licensed under the repository's MIT License.
