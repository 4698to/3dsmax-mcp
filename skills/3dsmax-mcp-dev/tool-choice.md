# Tool Choice

Scene reads — use **`query_scene(action=...)`**:
- `overview` | `filter` | `class` | `property` | `selection` | `delta`
- **`get_instances`** / **`get_dependencies`** — instancing and reference graph
- **`resolve_node_refs`** — turn a name, handle, or absolute JSON-Pointer hierarchy path into a canonical handle/name/path identity; multiple selectors are cross-checked
- **`scene_qa(action="scan")`** — deterministic naming, transform, hierarchy/group, and timeline checks only; it never analyzes meshes, UVs, topology, normals, skinning, or visual quality
- **`get_session_context`** — bridge + capabilities + overview + selection (on demand only)

Object/material/plugin inspection:
- `inspect_object`, `inspect_properties`, `get_material_slots`, `get_materials`, `get_material_library`
- `analyze_node_orientation` — pivot, bbox, local axes, world matrix before rig/vehicle/camera transforms
- `introspect_class`, `introspect_instance`, `introspect_osl`, `discover_plugin_classes`, `map_class_relationships` — unfamiliar plugin APIs and exact param names
- Arnold materials such as `ai_standard_surface` may not appear in class discovery; inspect with `inspect_plugin_class` or `introspect_osl`

Lighting:
- `lighting_capabilities` → `create_lights` → `inspect_lights` → `edit_lights`.
  Choose a supported renderer route, shape and explicit output unit. A finite bulb
  is `area/sphere`; an HDRI dome is `environment`. Never infer integer enum meanings.
- Distances accept scene/mm/cm/m/in/ft. Area emitters take a complete size and an
  aim point or direction. RGB values are linear in the rendering color space;
  Kelvin is explicit. EXR/HDR inputs get no extra gamma; preserve the renderer's
  input primaries conversion and exposure. Existing sky maps can be bound directly.
- For other plugin settings, use `inspect_plugin_class`/`inspect_plugin_instance`
  with `schema_version=2`, a query or exact fields. Follow returned map references
  and pass schema/state tokens to `plugin_patch`. Named enums use `{"enum":"name"}`
  from the returned choices. Shared or animated resources require deliberate handling.

Mutation:
- Use object, modifier, material, controller, organization, and viewport tools when they match.
- Use `scene_patch` for a preflighted batch of rename, relative transform, visibility/freeze/render flags, or parenting edits that must commit as one native undo step. Pass the mutation-only `expected_scene_seq` from `resolve_node_refs` when stale targeting matters; selection and sub-object selection do not invalidate it.
- Use `scene_qa(action="fix")` only for its explicit deterministic naming fixes; preview with `dry_run=true` when the caller has not already approved the repair.
- Verify after meaningful edits with `query_scene(action=delta)`, re-inspection, or viewport capture.

Debugging:
- `walk_references` — trace dependencies from a live object
- `watch_scene` — track user actions during an interactive session
- `execute_maxscript` — fallback only when no dedicated tool exists
- `execute_python(code)` — embedded Python fallback with `pymxs`; assign a JSON-compatible `result` to return a value alongside stdout/stderr. Requires bridge safe mode off. Calls have fresh variables and one undo step for undoable scene edits; uncaught errors roll those edits back and return a traceback. File I/O and other non-undoable effects persist.

## Scene Organization

**Layers** — `manage_layers`:
- Actions: `list`, `create`, `delete`, `set_current`, `set_properties`, `add_objects`, `select_objects`
- Properties: hidden, frozen, renderable, color, boxMode, castShadows, rcvShadows, xRayMtl, backCull, rename, parent

**Groups** — `manage_groups`:
- Actions: `list`, `create`, `ungroup`, `open`, `close`, `attach`, `detach`

**Named Selection Sets** — `manage_selection_sets`:
- Actions: `list`, `create`, `delete`, `select`, `replace`
