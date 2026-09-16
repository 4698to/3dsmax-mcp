# MCP Tools: execute_maxscript and Pitfalls

## When to Use `execute_maxscript`

**Almost never.** Only when there is genuinely no dedicated tool:
- Unsupported controller operations, render/environment settings, custom one-off scripted operations

**Do not use for:** anything a dedicated tool already does — properties, objects, materials, selection, batch ops, inspection.

### Passing code through `execute_maxscript` (serialization gotchas)

The `code` string is delivered as a JSON value, so it is **un-escaped once before MAXScript ever parses it**. A generic `parse error (BAD_PARAM)` with no line number almost always means the string was corrupted in transit — **not** that your logic is wrong. `if/then/else`, chained `and`, `not`, and `for` loops all parse fine on their own; the failures are escaping artifacts. Confirmed causes and fixes:

- **Backslashes in string literals break the literal.** `"C:\Users\...\textures\"` arrives with single backslashes, so MAXScript reads `\"`, `\U`, etc. as escapes — the trailing `\"` eats the closing quote and the string never terminates. **Use forward slashes in path literals** (`"C:/Users/.../textures/"` — Max accepts them on Windows), or derive paths from runtime values (`getFilenamePath`/`getFilenameFile`) instead of hardcoding.
- **`\n` / `\t` inside `"..."` become real control chars** and corrupt the literal the same way. Don't embed escapes in strings you send; build output without them.
- **Keep the whole script on one line, statements separated by `;`.** Multi-line code through the transport is unreliable; `;` is not.
- **Debug tell:** on a `BAD_PARAM` parse error, shrink to a known-good core — `try ( local n=0; for x in (getClassInstances C) do (...); n ) catch (getCurrentException() as string)` — and add pieces back. The piece that reintroduces a `\` or `\n` in a *literal* is the culprit.

## MCP Tool Pitfalls

- `set_modifier_property`: `name` + `modifier_index` (1-based) for one modifier; `modifier_class` + `names` for batch. Inspect with `inspect_properties(target="modifier")` first.
- `smart_import`: default `lod_filter="lod0"`. Shared maps match on asset id; variant meshes in a bundle folder with `Textures/` share one material key — omit `name_pattern` for all variants.
- `palette_laydown`: `sample_mode="random_per_subfolder"` for large per-subfolder asset libraries; `overflow_mode="palette_then_library"` when more than 24 picks.
- `scatter_forest_pack`: needs non-zero `widthlist`/`heightlist` per geometry item. Hide source meshes after scatter.
- `get_material_slots`: prefer `slot_scope="map"` unless you need every param (`slot_scope="all"` + `include_values:true` is huge on Arnold/Physical).
- `create_object`: default `pos_mode="ground"` — `pos` is bottom-center contact, not bbox center. Tripback includes `bbox`, `placement`, `groundContact`.
- Box: `width=X`, `length=Y`, `height=Z`.
- `boolean_operation`: non-live operands are **consumed** — scene node deleted, geometry captured; the operand keeps its node name inside the modifier (rename cutters *before* applying). `live=true` keeps the node (hidden) for later transform tweaks at extra eval cost. Never consume a node other tools still reference by name. Prefer inline `cutters` over scene-node cutters for cuts — pre-named, atomic, no litter on failure.
- `draw_spline`: all coordinates world-space; bezier `in_vec`/`out_vec` are absolute handle **positions**, not directions. Edits preserve a SplineShape base beneath modifiers. Bare parametric shapes auto-convert; a parametric base with modifiers requires `convert=true` to explicitly collapse. Conversion is reported as `converted_to_splineshape`.
- `edit_vertices`: edits the Editable_Poly **base** beneath the stack (cage editing — TurboSmooth above stays live); non-poly bases need `convert=true` (collapses) or `collapse_modifier_stack`. `conform` to geometry is ray-based — `skipped` verts had no hit along `axis`; unsigned tokens (`z`) cast both ways, signed (`-z`) one way.
- `snapshotAsMesh` evaluates the stack and returns a temporary world-space TriMesh. Read its vertices in `coordsys world` without applying `node.objectTransform` again; delete the temporary mesh afterward. Base-cage poly vertices still need the node's object transform.
- `list_wireable_params` paths include `[#Parameters]` levels — pass through to `wire_params` as-is.
- `create_shell_material`: `mcp_findMaterialByName` uses `sceneMaterials` — `getClassInstances Material` is invalid (Material is not a MAXClass).
- `material_class` must be the **material's** own class name, never a shortened token: `PhysicalMaterial`, not `Physical` — `Physical` is the Physical *Camera*. A non-material class name now returns `BAD_PARAM` with `hint.didYouMean`.
- `getHandleByAnim` formats as values like `12345P`; quote it as a string when building JSON, or the result is invalid JSON.
- MCP tripback is a structured `ToolEnvelope` dict (`ok`/`result`/`error`/`hint`), not a JSON string. Error envelopes may include `hint.suggested_tools`; tool-authored hints win over auto-hints.
- Success JSON payloads may include `message`; classify raw structured errors by `error`, `code`, or `status=error|failed`, not by `message` alone.
- Never issue Max-bound tool calls concurrently (including `list_instances`, `list_max_instances`, `get_bridge_status`, reads, and mutations): Max is single-threaded. Pre-guard bridges interleaved `theHold` transactions via nested message pumps (0xC0000005, then persistent corruption — phantom successes, wrong handles, bad class resolution). The main-thread executor now defers work items that arrive mid-item, but keep agent-side calls sequential regardless.
- `USER_BUSY` means Max has an open undo operation. The native write was rejected before mutation; continue read-only planning and retry after that operation finishes. Do not bypass it with MAXScript or repeated immediate writes.

### Keyframes (`keyframe_tracks`)
- **`action=timeline`** — targetless read/set of `frame_rate`, `current_frame`, and `range_start`/`range_end`; omit setters for a read-only query.
- **`action=list`** — read-only inspection; pass `from_time`/`to_time` for `loopGaps`. Parent `numKeys` is often 0 — keys live on Bezier Float sub-controllers.
- **`delete_keys` / `move_keys` / `scale_keys`** — deterministic key-time edits. They require `time`/`times` or both `from_time` and `to_time`; retimes reject destination collisions. Use `time_offset`, or `time_scale` with optional `pivot_time`.
- **`resample` / `bake`** — native sampled keys over a required `from_time`/`to_time` window. `sample_step` defaults to one frame; `bake` replaces keys in-window by default, while `resample` preserves existing keys unless `replace_keys=true`. List, constraint, expression, script, and motion-capture controllers are intentionally skipped.
- **`normalize_tangents`** — normalizes bounded Bezier keys to smooth tangents by default; pass `key_type`, `in_type`, or `out_type` to choose another deterministic tangent style.
- **`action=loop`** — copies evaluated pose from `from_time` to `to_time` parent-first; use for parented reflection rigs (e.g. `Plane001` → children). Defaults: frames 1→100.
- **`action=match`** with `order=hierarchy` — same parent-first copy as `loop` when closing endpoints on rigged hierarchies.
- Prefer **`value`/`move` on keyed tracks** over `transform_object` for animated objects — `transform_object` rewrites keys at the current slider frame.
- **`tracks`** accepts exact tokens only: `all`, `position`/`pos`, `rotation`/`rot`, `scale`/`scl`, `transform`/`tm` — not substring matches.
