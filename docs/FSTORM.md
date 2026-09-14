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

## Lights, cameras and conversion limits

FStorm classes participate in plugin discovery and receive curated recipes and
gotchas through the manifest/guide resources. Generic object and property tools
can inspect and edit their exposed settings. The typed `create_lights` provider,
environment binding and VFB interactive preview are not extended by this change.

User observations and the current sun property dump distinguish an untargeted
`FStormSunLight` driven by `hour`, `month`, `latitude` and `north_direction` from a
targeted sun. Its node transform alone is insufficient evidence of the evaluated
solar emission direction. Temporarily targeting a duplicate is a hypothesis for
baking that direction, not a validated conversion procedure. Do not delete or
retarget the original based on that assumption. Automatic FStorm-to-Corona sun,
camera and material conversion is outside this provider.

Light shapes reported by the UI are plane=0, disc=1, sphere=2, IES=3; Corona uses
different shape values. Treat these as version-specific leads for a future typed
provider, requiring class/PB identities and geometric validation. A brightness
match in one scene is not a universal renderer-power or photometric conversion.
FStorm cameras expose `targ_dist`, while FStorm lights/suns expose
`target_distance`; resolve the actual target reference instead of guessing its
name or reading Corona's `targetDistance` property.

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

Run offline checks with the project's Python environment:

```powershell
python scripts/test_fstorm_materials.py
python scripts/test_fstorm_materials.py --emit-maxscript local/fstorm-smoke.ms
& 'C:/Program Files/Autodesk/3ds Max 2027/3dsmaxbatch.exe' local/fstorm-smoke.ms
```

The generated script creates a 4x4 image beside itself, builds disposable graphs,
and writes `fstorm-smoke.txt`. **Check for `ALL PASS` in that file**: batch exit
status alone does not establish success. Use a disposable session with no scene
input; running the script in an interactive session creates test materials.

Recorded validation on 2026-09-14: six offline regression tests passed, all four
runtime material cases passed, and bitmap reload/FrontBack readback passed.
The runtime cases check legacy/PBR slots, color/data gamma flags, AO links,
roughness/glossiness, normal/bump priority, metallic, emission and displacement
enablement. These are graph-construction/readback checks. Rendered appearance,
normal handedness, cross-version behavior and solar conversion are unvalidated.
