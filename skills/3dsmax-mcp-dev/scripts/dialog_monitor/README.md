# Local / maintainer skill — no runnable dialog_monitor Python here

This **3dsmax-mcp-dev** package is for developers with a **full repo checkout**.

- Do **not** expect `import maxmcp` from this skill zip.
- Probe / smoke runners live in the repository root:

```bash
uv run --directory <repo> python dialog_monitor/_test_goskin_prestart.py
uv run --directory <repo> python dialog_monitor/_probe_client_map.py
```

Remote agents on host **A** should install **`3dsmax-mcp-remote`** instead
(HTTP helper scripts + docs, no maxmcp).
