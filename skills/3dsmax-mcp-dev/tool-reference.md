# Tool Reference

### Scene reads
`query_scene` `resolve_node_refs` `scene_qa` `get_hierarchy` `get_instances` `get_dependencies`

### Atomic scene edits
- `scene_patch` — accepts NodeRefs (`handle`, `name`, or JSON-Pointer `path`), validates every operation before editing, rejects hierarchy/name conflicts, supports mutation-only stale-sequence guards and dry-run, and rolls back the whole native hold on apply failure; activity-only selection events remain observable without blocking writes
- Node handles are stable only within the current loaded scene/session. Cross-check a cached handle with `name` and/or `path`, and refresh after a scene reset/load.

### Objects
`get_object_properties` `analyze_node_orientation` `set_object_property` `create_object` `delete_objects` `transform_object` `select_objects` `set_visibility` `clone_objects` `set_parent` `batch_rename_objects`

### Modifiers
`add_modifier` `remove_modifier` `set_modifier_state` `set_modifier_property` `collapse_modifier_stack` `make_modifier_unique`

### Modeling
- `curve_model` — named local-plane curves, rounded profiles, tangent arcs, sweeps and resampled quad lofts with saved numeric controls. `preview` checks locally; `create` saves the recipe on one editable node; `read`/`update` use a model token to preserve placement and modifiers. Read [curve-construction.md](curve-construction.md) for recipes, supported operations and limits.
- `inspect_curve` / `edit_curve` — world knot/handle readback, sampled curve QA, labeled AGENT VIEWPORT capture and image targeting; atomic base edits guarded by `curve_token`. Re-inspect after topology changes. See [curve-construction.md](curve-construction.md).
- `create_mesh` — explicit world-space vertices and 1-based polygon faces, preserving quads/n-gons. Compute curved panels, lofted sections, and furniture geometry in Python, then send the arrays to Max.
- `loft_mesh` — matched cross sections become a quad cage with optional caps or closed path. Coordinates can use arithmetic expressions of named parameters, such as `width` or `backrest_curvature`. The definition persists on the mesh in the `.max` file. `read` returns compact controls (`include_definition=true` for sections); `update` changes parameters while preserving placement, subdivision, and cage IDs. Manual cage edits or instanced bases block parameter updates. Keep profile ordering consistent; no automatic resampling.
- `inspect_mesh` — base-cage vertex/edge/face IDs, centers, normals, mesh token, and optional labeled viewport capture. Omit the target for the one selected mesh. Filter by IDs, current selection, world bounds/proximity, face normal, open borders, or sharp edges.
- `pick_component` — normalized image x/y plus the capture's `expected_view` finds editable vertex/edge/face candidates. Defaults to the hit node; pass name/handle for silhouettes. It favors correspondence to the visible surface within screen tolerance. Inspect ambiguity and surface evidence, then pass the returned `mesh_token` to `mesh_edit(expected_mesh=...)`. Subdivision can separate the visible surface from its cage; proximity does not prove visibility.
- `mesh_edit` — batch select/move/scale/extrude/inset/bevel/chamfer/connect/bridge/delete/cap/relax in one undo step. Pass the inspected `expected_mesh` token when reusing IDs. Omitted component filters use the current sub-object selection; use `selection:{all:true}` explicitly for the whole mesh. World-space moves/scales preserve modifiers above an Editable Poly base; `convert=true` explicitly collapses a non-poly stack. Shared geometry changes in every instance.
- `geometry_qa` — evaluated mesh boundaries, non-manifold edges, inconsistent winding, degenerate/duplicate triangles, components and isolated vertices. Samples use world coordinates and evaluated snapshot IDs; re-inspect the base cage near a sample before editing. No intersection, thickness or outward-normal certification. Open seams and separate components may be intentional.
- `boolean_operation` — Boolean modifier (BooleanMod): apply union/subtract/intersect/merge/attach operands, list/retune/rename/extract them. Inline `cutters` build scratch primitives in the same call ({name, shape: box|cylinder|sphere, size, pos (bbox center), rot, operation?}) — consumed on apply, zero scene litter; `repeat` {count, axis, spacing} arrays every cutter (`vent_1..N`) for vents/ribs/window grids. Recipes: hole = Z-axis cylinder cutter overshooting both faces (rot to orient); slot = box cutter; panel line = cutter + `operation_option="imprint"`.
- `draw_spline` — spline shapes from world-space point lists; base-knot editing preserves modifiers above the spline (including CrossSection, Surface, and Sweep), holes via add_spline, renderable thickness
- `edit_vertices` — Editable_Poly verts in world space: get (filtered), move (soft falloff), set, conform to a spline or ray-projected onto geometry
- Curved-form recipe: `draw_spline` the reference profile → Lathe/Extrude/Bevel_Profile/Sweep via `add_modifier` → refine with `set_knots` or `edit_vertices conform`

### Materials
- Create + assign: `assign_material`, `create_material_from_textures`, `smart_import`, `palette_laydown`
- Share an existing material: `assign_material(names=[...], source_name="ExistingObject")` (or `source_handle`). Shares the full material/maps in one undo step; omit creation arguments.
- Edit: `set_material_property`, `set_material_properties`
- Inspect: `get_material_slots`, `get_materials`, `get_material_library`
- Scratch libraries: `backup_material_library` saves `currentMaterialLibrary` / `meditMaterials` to `.mat`
- Multi/Sub: `set_sub_material`
- Textures: `create_texture_map`, `set_texture_map_properties`
- Dual pipeline: `create_shell_material`, `replace_material`, `batch_replace_materials`
- OSL: `write_osl_shader`

### Dialog OCR / Auto GoSkin
- Qt plugin dialogs without child HWNDs (e.g. **自动蒙皮 / GoSkinning**): screenshot → external OCR → `SetCursorPos` + `mouse_event` clicks.
- Before any GoSkin/scene call: `list_instances` only (serial). Idle = `online` and not `busy`. `online` means TCP and/or Native. If **more than one** idle Max is available, stop and let the user pick; then `acquire_instance(name=...)`. Do not auto-grab the first idle instance. Do not probe the same Max with a second tool in parallel.
- Prefer `goskin_ensure_ready` → `goskin_run_skin` (default pauses before 「开始蒙皮」) → show `user_prompt` → only then `goskin_confirm_start(user_confirmed=true)`.
- **Must** click list slot 「(选中后在编辑区添加)」 before 「选定」, or a warning covers the UI.
- Remote desktop must stay **unlocked** (lock screen → empty-success clicks). Config: `max_instances.ini` `[ocr] base=` and optional `[workspace]`.
- Progressive toolset: `dialog_ui`. Read [goskin-ocr-click.md](goskin-ocr-click.md) completely before automating GoSkin.
- **Remote agents (`3dsmax-mcp-remote`):** prefer `scripts/goskin_dev_flow.py` or MCP `goskin_*`. Do not invent temp smoke scripts on A.
- **Full-repo maintainers (`3dsmax-mcp-dev`):** reuse repo-root `dialog_monitor/_test_goskin_*.py` (requires `maxmcp`).

### Material notes
- `create_material_from_textures` and `smart_import` default to **OpenPBR**. Pass `material_class` for Physical, Arnold, Redshift, V-Ray, CoronaPhysicalMtl, MaterialX, Octane, etc. (see tool tripback `hint.renderers`). `palette_laydown` honors it for both single-image previews and grouped PBR sets.
- `create_shell_material` wraps two scene materials in `Shell_Material` (render slot 0, export/viewport slot 1), or builds from `texture_folder` with `render_material_class` / `export_material_class`. Shell is a container, not a renderer.

### Viewport
- `agent_viewport(action="open")` reserves a shaded floating **AGENT VIEWPORT**. After opening, navigation and capture default to it. `start_minimized=true` parks it initially; `minimize`/`restore` park it between inspections. Captures require a visible on-screen panel and explicitly refuse while minimized. `status` reports `capture_ready`; `release` closes only the owned panel. Initial opening may briefly activate the panel before restoring user focus.
- `agent_viewport` also frames hierarchies, orbits (yaw/pitch degrees), pans (view-plane scene units), zooms (factor<1 closer), and picks surfaces (normalized image x/y, top-left origin). Pass the single capture's `view_token` as `expected_view` for picking; then inspect the hit node near its world point before editing base-cage components. View/scene changes invalidate the token; mesh IDs still require `expected_mesh`.
- Interactive preview: `agent_viewport(action="render", mode="activeshade"|"vray_ipr"|"vray_vfb"|"corona_vfb"|"shaded")`. ActiveShade uses the assigned ActiveShade renderer (`renderer_source="production"` uses production if compatible). V-Ray previews enable progressive IPR and denoising. `vray_vfb` and `corona_vfb` lock the VFB to the agent view; Corona uses the current production renderer and preserves its denoising settings. Start only when rendering is requested. Existing renders elsewhere are refused. Wait for `session_state="running"`, then use `action="capture"` or `"stop_capture"` (save image, then stop). Optional `crop=[x,y,width,height]` trims VFB pixels. Return to shaded before component targeting or minimizing. Captures do not certify convergence or completed denoising.
- Picking supports visible thick splines as well as geometry and returns a world surface normal. Use `draw_spline(action="get")` for spline knots. On thin panels, narrow face inspection by proximity and normal to separate the front from the back; frame the part before capturing labels.
- Aim/frame: `set_viewport` — world-space `eye` + `target`, named elevations, or `frame_names`; no camera node is created
- Fast: `capture_viewport`
- Multi-angle grid: `capture_multi_view` (`frame_root` frames a hierarchy). In the agent panel it does not hide other scene nodes; arbitrary-object isolation is not yet supported there. The legacy active-view route also temporarily isolates the hierarchy.
- `source="agent"` requires the agent panel; `source="active"` explicitly targets the user's active view. The default `auto` uses the agent panel once opened, and fails if that owned panel becomes unavailable instead of redirecting into the user's viewport. Release and reopen after a scene load or layout replacement.
- `inspect_mesh(capture=true)` uses the agent panel when open, drawing component labels into the saved image without adding overlays to the user's viewport.
- Fullscreen: `capture_screen` (requires `enabled=True`)
- Frame-buffer screen crop: `capture_screen(enabled=True, target="vray_vfb"|"corona_vfb")`; optional `crop=[x,y,width,height]` trims inside its current client area in physical pixels before resizing. Recheck dimensions after resizing the VFB. It must be visible and on screen; overlapping windows appear in the capture. No render is started by capture.
- Blocked production render recovery: `render_automations(action="cancel_capture", job_id=...)` saves visible VFB pixels and requests cancellation for the production job you armed and started. `capture_target="screen"` supports an explicit desktop crop for another renderer. Configure progressive sampling and its denoiser before starting; recovery cannot change blocked render settings. Cancellation is cooperative; check the done-signal separately and treat the image as partial.

### Cosmos assets
- `cosmos_search` finds models, materials and HDRIs compatible with the selected Max instance and renderer.
- `cosmos_download` caches an asset without importing it; ready means completed, queued/downloading means call again to wait.
- `cosmos_import` downloads if needed, then imports through the renderer and returns asset-scoped nodes/materials/maps with file checks. Selection is preserved.
- Use returned node refs with existing transform, layer and instance tools. Materials/HDRIs may create editor resources instead of scene nodes.
- Sign in through Cosmos when requested. If an import returns unknown/unverified, inspect before retrying; repeating a completed model import creates another instance.

### External .max files (no scene load)
- `inspect_max_file`, `search_max_files`, `merge_from_file`, `batch_file_info`

### Plugin discovery
- `discover_plugin_surface`, `get_plugin_manifest`, `refresh_plugin_manifest`
- `inspect_plugin_class`, `inspect_plugin_constructor`, `inspect_plugin_instance`
- MCP resources: `resource://3dsmax-mcp/plugins/{name}/manifest|guide|recipes|gotchas`

### tyFlow
- Create: `create_tyflow`, `create_tyflow_preset`
- Inspect: `get_tyflow_info` (`include_operator_properties` for deep readback)
- Edit: `modify_tyflow_operator`, `set_tyflow_shape`, `set_tyflow_physx`, `add_tyflow_collision`
- Simulate: `reset_tyflow_simulation`, `get_tyflow_particle_count`, `get_tyflow_particles`

For tyFlow graph work (event/operator topology, wiring, transactional edits, operator
discovery, per-event census), read [tyflow-graphs.md](tyflow-graphs.md) completely before
acting. It covers `get_tyflow_graph`, `tyflow_apply_patch`, the wiring ledger and its
staleness rules, `harvest_tyflow_manifest` / `list_tyflow_operators`, `tyflow_event_census`,
and `capture_tyflow_editor` for foreign flows.

### Forest Pack
- `scatter_forest_pack` — surfaces + source geometry; auto footprint per variant

### Controllers & wiring
- `assign_controller`, `inspect_controller`, `inspect_track_view`, `set_controller_props`, `add_controller_target`
- `list_wireable_params`, `wire_params`, `get_wired_params`, `unwire_params`

### Procedural graph systems

For Data Channel or Max Creation Graph work, read [procedural-graphs.md](procedural-graphs.md) completely before acting. It contains the dedicated tool workflows, agentic compile/verify loop, safety gates, validation rules, and runtime pitfalls.

### Scene management
- `manage_scene` (hold/fetch/reset/save/info/save_as/save_scene_as/show_agent_banner/hide_agent_banner) — `save_as` needs `file_path`; **basename only** → `{WORKSPACE_DIR}/<name>.max`
- `save_scene_as` / `save_as` — same Save As (directories in `file_path` ignored)
- `load_scene` — `MCP_SceneManage.loadScene`
- `get_unhidden_meshes_bones` — unhidden Editable_Poly/Mesh + Bone/Biped names and AnimHandles
- `select_by_handles` — select by AnimHandle array (`MCP_SceneManage.selectByHandles`)
- `get_state_sets`, `get_camera_sequence`
- Agent viewport HUD: `acquire_instance` auto-shows 「正在被 AI Agent 接管，请勿操作」 (bottom-right); `release_instance` hides it. Manual: `manage_scene(action="show_agent_banner"|"hide_agent_banner")`.
