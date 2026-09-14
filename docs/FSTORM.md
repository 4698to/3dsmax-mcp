# FStorm support

Texture-folder import, `palette_laydown` (both image previews and grouped PBR
materials), `smart_import`, and texture-built shell materials accept:

| `material_class` | Created class | Intended use |
| --- | --- | --- |
| `FStorm`, `fstorm_legacy` | `FStorm` | Legacy reflection/glossiness materials |
| `FStormPBR`, `fstorm_pbr` | `FStormPBR` | Metalness/roughness materials on FStorm versions exposing this class |

The requested class is preserved. Missing plugins or properties fail the build;
there is no fallback to another renderer. These tools do not switch the active
renderer. Install this source branch and restart the MCP server to use the new
Python routes; no native bridge rebuild is needed.

For example:

```json
{
  "texture_folder": "D:/Textures/Oak",
  "material_class": "FStormPBR",
  "material_name": "Oak"
}
```

Pass this to `create_material_from_textures`. Add `assign_to` with object names to
assign the result. For an existing scene, start with
`get_plugin_manifest(plugin_name="fstorm")` and inspect the material/network
before editing it.

## Material behavior

All images use `FStormBitmap`. Color maps enable `input_gamma`; data maps disable
it. `gamma` is 1.0. The filename is assigned only after construction and only when
nonempty; missing image files fail explicitly. Normal maps use the bitmap's
`normal_map` flag with the installed plugin's default axis/green-channel settings.
No automatic OpenGL/DirectX convention conversion is claimed.

AO multiplies the base color through `FStormMix` (`mix_mod=3`). Legacy roughness
uses `reflection_glossy_tex` with `refl_glossiness_invert=1`; legacy glossiness
uses the same slot with inversion disabled. PBR glossiness is inverted by its
bitmap before connection to `roughness_tex`. Roughness wins if both are supplied.

The provider also connects opacity, emission, translucency, specular and
displacement. Emission enables the material's emission switch and sets its power
to 1. Displacement enables both the map and material switches when requested,
leaving its scene-unit scale/center at plugin defaults. Review those values for
the asset's intended physical scale.

Unsupported or conflicting inputs appear in the tool's `Skipped` summary:

- Metallic maps require `FStormPBR`; a legacy material is not silently replaced.
- Packed ORM requires separate AO, roughness and metallic images. No generic OSL
  channel splitter is inserted into an FStorm graph.
- IOR images are not automatically wired: FStorm uses a nonlinear RGB-to-IOR
  interpretation, which may differ from the supplied texture's encoding.
- Normal takes priority over height bump because these materials have one bump
  input. Both files are not silently combined.
- AO without base color and displacement explicitly disabled by the caller are
  reported as skipped.

## Existing materials, FrontBack and reload

`FStorm` exposes `diffuse_tex`; `FStormPBR` exposes `color_tex`. A node carrying a
Multi/Sub-Object material has neither at its top level. Traverse sub-materials
and nested maps, tracking visited references to handle shared or cyclic graphs.

`FStormFrontBack` exposes `texture1`, `texture2`, `texture1_on`, `texture2_on`,
`color1` and `color2` on the validated build. Inspect other builds first. Snapshot
the original owner slots and inputs before inserting a wrapper. Never replace
every dependent reference after wiring the original bitmap into the wrapper:
that can replace the wrapper's own input and create a self-reference. Reject
direct and indirect cycles, preserve instancing, and process a shared FrontBack
only once when swapping. An undefined input is a valid empty slot.

FStorm 2.0.0Z publishes a reload method:

```maxscript
bitmapMap.FStormBitmap.reloadBitmap()
```

Here `bitmapMap` is an inspected `FStormBitmap` instance. The batch check calls
this method and verifies that its filename remains unchanged. It does not prove
that modified source pixels become visible in an interactive render. Inspect
the interface on other versions and report unsupported methods rather than
clearing filenames as a reload workaround. Generic map inspection/edit tools
remain available; this change adds no new bulk-reload or FrontBack mutation tool.

## Lighting

`lighting_capabilities(renderer="fstorm")` discovers the installed provider.
`create_lights`, `inspect_lights` and `edit_lights` now support native FStorm
lights through the existing transactional bridge. Creation accepts
`renderer="fstorm"` even if a different production renderer is selected, and
reports compatibility without switching renderers.

| Control | Area lights | Sun |
| --- | --- | --- |
| Creation | `rectangle` (plane), `disk` (disc), `sphere` | `directional`, body `sun` |
| Output | Positive native `renderer` power | Native `renderer` power, 0.001..100000 |
| Color | Scene-linear RGB or 500..24000 K | Physical model, or legacy RGB |
| Size | Full width/height or radius | `fstorm.sun_size`, native size multiplier |
| Visibility/contribution | `visible`, `gi_visible`, `double_sided`, `affect_diffuse`, `affect_glossy` | `visible`, `affect_diffuse`, `affect_glossy` |
| Enabled/shadows | Both switches | Enabled; no shadow switch |
| Direction | World `orientation.direction` or `orientation.aim_at` | Native `fstorm.solar` controls |

Area dimensions and positions accept `distance_unit` (`scene`, `mm`, `cm`, `m`,
`in`, `ft`). Readback dimensions are in scene units. FStorm's plane `size_x` and
`size_y` are half-extents; the provider translates full width/height accordingly.
Disc and sphere `size_x` is radius. Shape bindings and bounds were checked in Max.
Power remains FStorm's native value; resizing may change total emitted energy.

For example, pass this to `create_lights` to add a warm rectangular light:

```json
{
  "renderer": "fstorm",
  "distance_unit": "m",
  "lights": [{
    "name": "Warm fill",
    "kind": "area",
    "shape": "rectangle",
    "size": {"width": 2, "height": 1},
    "position": [0, -3, 2],
    "orientation": {"aim_at": [0, 0, 1]},
    "color": {"kelvin": 3200},
    "output": {"value": 10, "unit": "renderer"},
    "fstorm": {"visible": false, "double_sided": false}
  }]
}
```

Create a sun using FStorm's own solar controls, without `orientation`:

```json
{
  "renderer": "fstorm",
  "lights": [{
    "name": "Afternoon sun",
    "kind": "directional",
    "output": {"value": 1, "unit": "renderer"},
    "fstorm": {
      "solar": {"hour": 15, "month": 8, "latitude": 45, "north_direction": 10},
      "sun_model": "physical",
      "sun_size": 2
    }
  }]
}
```

Solar input accepts hour 0..24, month 1..12, latitude -90..90 and north direction
in degrees. At least one solar field is required for creation; omitted fields
retain plugin defaults. Editing `changes={"fstorm":{"solar":{"hour":9}}}`
updates only the hour. Pass the returned `light_ref` and the latest inspected
`light_token` as `expected_light` to `edit_lights`. An intervening change refuses
the edit until you inspect again. A batch is preflighted before any edits commit.

New suns are untargeted and keep their native solar driver. Existing targeted
suns can be inspected and have power/visibility edited, but solar edits refuse
them rather than changing their targeting mode. The node transform alone does
not describe an untargeted sun's evaluated direction. Supplying sun RGB selects
the legacy model; an explicit physical model plus RGB is refused. Suns do not
accept Kelvin. `sun_size` uses the native range (0.001..100000), independent of
the scene distance unit.

The typed provider does not create IES lights, texture-driven light color,
FStormSky/environment bindings, or VFB previews. A sun alone does not create a
sky. Generic plugin inspection/property tools remain available for other
settings. Camera and renderer conversion are outside this change. Cameras expose
`targ_dist`, while lights/suns expose `target_distance`; inspect actual target
references rather than inferring names.

## Evidence and validation

Property names, defaults and published interfaces were inspected on **FStorm
2.0.0Z, 3ds Max 2027.2 (29.2.0.20588)** in a disposable batch session. The existing
FStorm toolbox supplied a corroborating lead for AO multiply mode and legacy
roughness behavior. No third-party script implementation is copied here.

Vendor references:

- [FStorm material manual](https://fstormrender.com/manual/fstorm-material/):
  reflection roughness/glossiness, input-gamma policy and IOR interpretation.
- [FStorm bitmap manual](https://fstormrender.com/manual/fstorm-bitmap/): bitmap
  correction, image input and mapping concepts. Names above come from runtime
  inspection, not inferred UI labels.
- [FStorm light manual](https://fstormrender.com/manual/fstorm-light/): light
  types, native power, color and visibility controls.
- [FStorm sun manual](https://fstormrender.com/manual/fstorm-sun-light/): native
  solar and targeted direction, and the separate sky/environment setup.

Run offline checks with the project's Python environment:

```powershell
python scripts/test_fstorm_materials.py
python scripts/test_fstorm_lighting.py
python scripts/test_fstorm_materials.py --emit-maxscript local/fstorm-smoke.ms
& 'C:/Program Files/Autodesk/3ds Max 2027/3dsmaxbatch.exe' local/fstorm-smoke.ms
```

The generated script creates a 4x4 image beside itself, builds disposable graphs,
and writes `fstorm-smoke.txt`. **Check for `ALL PASS` in that file**: batch exit
status alone does not establish success. Use a disposable session with no scene
input; running the script in an interactive session creates test materials.

Recorded validation on 2026-09-14: six offline regression tests passed, all four
runtime material cases passed, and bitmap reload/FrontBack readback passed.
Lighting acceptance also runs from Python against an explicitly chosen disposable
Max instance with FStorm and the native bridge loaded:

```powershell
python scripts/test_fstorm_lighting.py --pid <disposable-max-process-id>
```

It creates uniquely named test lights and cleans them up, without rendering,
resetting or saving the scene. Five offline lighting tests and live acceptance
passed on the same build, including independent emitter bounding boxes, unit
conversion, color/visibility edits, partial solar changes, stale-token rejection,
invalid-batch refusal and preservation of targeted-sun ownership.

The material runtime cases check legacy/PBR slots, color/data gamma flags, AO links,
roughness/glossiness, normal/bump priority, metallic, emission and displacement
enablement. These are graph-construction/readback checks. Rendered appearance,
normal handedness and cross-version behavior are unvalidated. Lighting checks
establish construction, geometry and property readback; rendered appearance and
solar angular accuracy were not tested.
