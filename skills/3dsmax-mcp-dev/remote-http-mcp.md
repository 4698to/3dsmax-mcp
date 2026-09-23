# Remote Usage (HTTP MCP)

Remote workflows talk to the HTTP MCP server (`http://<host>:8000/mcp`) instead of
stdio. The server forwards tool calls to the Max-side TCP listener. Confirmed flow
and pitfalls (validated over HTTP on a Max 2015 host):

### Session handshake (per new connection)
1. POST `initialize` (JSON-RPC 2.0) with header `Accept: application/json, text/event-stream`; capture the `Mcp-Session-Id` response header and send it on every later request.
2. POST `notifications/initialized` (no meaningful response body).
3. POST `tools/call` with `{name, arguments}`. The reply is an SSE event — parse the `data: ` line (the first `data: ` payload), not the raw body.

### File transfer
- **Upload:** `workspace_upload(file_name, data_b64)` saves the decoded bytes into
  the Max machine's workspace and returns
  `{ok, result: {name, size, local_path, url}}`. Use the returned `local_path`
  directly as `file_path` for `load_scene`. `get_file_service_info` describes the
  HTTP-only upload endpoint. A ~17 MB file (~22 MB base64) transfers fine; give the
  HTTP call a large timeout (e.g. 900 s).
- **Download:** `workspace_download(file_name)` returns `data_b64` for files
  under the shared workspace **or** `%TEMP%/3dsmax-mcp` (viewport captures).
  Max always writes captures under `%TEMP%/3dsmax-mcp` first; when `[workspace]`
  is valid they are also copied to the shared path. Prefer `result.download_url`
  from `capture_viewport` over streamable-http (`GET /files/{name}`) — no shared
  `[workspace]` config required for same-host Python↔Max.

### One-call remote open + capture: `upload_scene_and_capture`
`upload_scene_and_capture(data_b64, file_name, max_width=1600, return_png_b64=True)`
is the server-side composite tool that wraps the five-step recipe below into a
single `tools/call`: upload (original filename kept — CJK/space safe) →
`load_scene` → wait for the viewport to redraw → capture (auto-retries while the
capture is still a tiny placeholder) → copy into the workspace. Returns
`{ok, result: {scene, file_name, local_path, upload_size, capture_file,
capture_name, download_url, png_size, png_b64, attempts}}`.

Getting the capture back — two options:
- **Fast path (HTTP):** `result.download_url` → plain `GET`, no base64 overhead.
  Prefer this over HTTP transport; the file is served from the workspace.
- **Transport-agnostic:** decode `result.png_b64` and save it. The only option
  when the server is reached over stdio (HTTP file routes do not exist there).
  Set `return_png_b64=False` to omit the inline blob when you will GET
  `download_url` instead (keeps the MCP response small).


### CJK filenames: transport is lossless (display-only artifact)
Byte-level probes (`scripts/probe_cjk_bytes.py`) proved the whole chain is lossless:
- HTTP layer: `streamable_http.py` does `json.loads(await request.body())` directly on
  bytes (UTF-8, lossless). Python→Max: `max_client.py` sends `json.dumps(..., ensure_ascii=True)`
  (CJK becomes `\uXXXX`), and Max's `extractJsonValue` already decodes `\uXXXX`.
- Verified on the Max machine: `9f4f3292_m_fash_004_b_skin#2026.8.26雷世坤.max`,
  `测试_CJK_upload.txt`, `cjk_测试_upload.txt` all land on disk as correct UTF-8; an
  `execute_maxscript` echo of `测试A_RawUTF8` / `测试B_Escaped` returns the exact UTF-8
  bytes `e6 b5 8b e8 af 95`.
- The mojibake (`æµè¯`, `é·ä¸å¤`) seen earlier is a **display artifact**: SSE responses
  have `Content-Type: text/event-stream` with no charset, so `requests` decodes them as
  ISO-8859-1 (`r.encoding == 'ISO-8859-1'`), and PowerShell consoles render UTF-8 bytes
  with the OEM codepage. Nothing is corrupted on disk, in Max, or in transit.
- Correct client pattern: decode the SSE body as `r.content.decode("utf-8")` (or
  `json.loads(r.content)`), never `r.text`.
- Recommendation: still prefer ASCII upload names as a general robustness practice
  (URLs with CJK and older tooling can misbehave), but it is NOT required for correctness.

### Proven end-to-end recipe (open a local .max remotely + capture)
> Shortcut: `upload_scene_and_capture` (above) does all of this in one call.
1. Read the local file and base64-encode it.
2. `workspace_upload` with an ASCII `file_name`; take `local_path` from the reply.
3. `load_scene(file_path=<local_path>)` → `Loaded scene: <name>`.
4. `capture_viewport(source="auto", max_width=1600)` → `result.file` (outside workspace).
5. `execute_maxscript` → `copyfile <capture> <workspace>\<basename>` to move it inside.
6. `workspace_download(file_name=<basename>)` → decode base64 → save locally.
