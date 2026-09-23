# MAXScript Pitfalls

- **No parens with keyword args**: `Box width:10` not `Box() width:10`
- **Wrap in try/catch**: `try (...) catch (ex) (ex)`
- **`Noise` vs `Noisemodifier`**: texture map vs modifier
- **`(getDir #temp)`** is Max temp, not OS temp
- **.NET strings**: convert to MAXScript strings before string methods
- Controller/wire paths: normalize display tokens like `[#Z Position]` to `[#z_position]`
- TCP fallback is opt-in; prefer the native bridge, and if Max viewport interaction stutters while fallback is running, stop the fallback and use the native bridge path.

### OSL
- Use `write_osl_shader` for file I/O and compilation
- Use `introspect_osl` before wiring — not `introspect_class` on OSLMap (massive output)
- Shader function name must match `shader_name`; use unique names (cache reuse)
- OSLMap lowercases param names
