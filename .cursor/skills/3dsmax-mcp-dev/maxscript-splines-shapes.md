# MAXScript: Splines and Shapes Reference

## Shape Primitive Constructors

All shapes have superclass `shape` and super-superclass `node`. Every shape shares Interpolation and Rendering rollout properties.

```maxscript
-- Basic shapes
c = circle radius:50
a = arc radius:40 from:0 to:180 pie:false reverse:false
e = ellipse length:30 width:50
r = rectangle length:40 width:60 cornerRadius:5
s = star radius1:25 radius2:12 points:6 distort:0 fillet1:0 fillet2:0
n = ngon radius:30 nSides:8 scribe:0 circular:true corner_Radius:0
  -- scribe: 0=circumscribed, 1=inscribed
d = donut radius1:35 radius2:25
h = helix radius1:35 radius2:15 height:100 turns:3 bias:0 direction:0
  -- direction: 0=CW, 1=CCW
t = text font:"Arial" size:100 text:"Hello" kerning:0 leading:0 alignment:1
  -- alignment: 1=left, 2=center, 3=right, 4=justify
sc = section pos:[0,0,50] length:200 width:200
l = line()  -- equivalent to splineShape; all SplineShape methods apply
```

Note: `star.baseobject.points` must be used (conflicts with node `.points` property).

### Shared Interpolation Properties (all shapes)
```maxscript
<shape>.steps    -- Integer default:6, divisions between vertices
<shape>.optimize -- Boolean default:true, remove steps on straight segments
<shape>.adaptive -- Boolean default:false, auto-step for smooth curves
```

## SplineShape / Editable Spline

SplineShape is the editable form of all shape objects (like EditableMesh for geometry). Created by:

```maxscript
ss = splineShape()          -- empty spline shape
ss = bezierShape()          -- same thing
convertToSplineShape <shape> -- convert any shape to editable spline
```

**CRITICAL**: You MUST call `updateShape <shape>` after making changes. Without it, 3ds Max may crash. Do NOT call it after every single operation -- batch changes, then call once.

```maxscript
-- Coordinates are in the MAXScript working coordinate system (world by default)
-- In/out vectors are handle POSITIONS, not direction vectors
```

## Creating Splines from Scratch

### Line between two points
```maxscript
ss = splineShape()
addNewSpline ss
addKnot ss 1 #corner #line [0,0,0]
addKnot ss 1 #corner #line [100,50,0]
updateShape ss
```

### Triangle (closed, corner knots)
```maxscript
ss = splineShape()
addNewSpline ss
addKnot ss 1 #corner #line [0,0,0]
addKnot ss 1 #corner #line [100,0,0]
addKnot ss 1 #corner #line [50,80,0]
close ss 1
updateShape ss
```

### Smooth curve
```maxscript
ss = splineShape()
addNewSpline ss
addKnot ss 1 #smooth #curve [0,0,0]
addKnot ss 1 #smooth #curve [50,30,0]
addKnot ss 1 #smooth #curve [100,0,0]
updateShape ss
```

### Bezier with explicit handles
```maxscript
ss = splineShape()
addNewSpline ss
-- addKnot <shape> <spline_idx> <knot_type> <seg_type> <pos> [<inVec> <outVec>] [where]
addKnot ss 1 #bezier #curve [0,0,0] [-10,10,0] [10,-10,0]
addKnot ss 1 #bezier #curve [100,0,0] [90,20,0] [110,-20,0]
updateShape ss
```

### Multi-spline shape (e.g., letter "O" with hole)
```maxscript
ss = splineShape()
-- outer ring
addNewSpline ss
for i = 0 to 330 by 30 do (
    a = degToRad i
    addKnot ss 1 #smooth #curve [cos a * 50, sin a * 50, 0]
)
close ss 1
-- inner ring
addNewSpline ss
for i = 0 to 330 by 30 do (
    a = degToRad i
    addKnot ss 2 #smooth #curve [cos a * 25, sin a * 25, 0]
)
close ss 2
updateShape ss
```

### Programmatic curve from point array
```maxscript
fn createSplineFromPoints pts knotType:#smooth segType:#curve closed:false = (
    local ss = splineShape()
    addNewSpline ss
    for p in pts do addKnot ss 1 knotType segType p
    if closed do close ss 1
    updateShape ss
    ss
)
-- Usage:
pts = for i = 0 to 360 by 10 collect [i, sin(i) * 50, 0]
ss = createSplineFromPoints pts closed:false
```

## Knot Types

| Type | Keyword | Behavior |
|------|---------|----------|
| Corner | `#corner` | Sharp angle, no tangent handles |
| Smooth | `#smooth` | Auto-computed smooth tangents, locked |
| Bezier | `#bezier` | Manual tangent handles, locked (in/out linked) |
| BezierCorner | `#bezierCorner` | Manual tangent handles, independent in/out |

Segment types: `#line` (straight) or `#curve` (curved between knots).

## Shape-Level Methods

```maxscript
updateShape <shape>       -- MUST call after changes
resetShape <shape>        -- clear all splines from shape
numSplines <shape>        -- number of spline curves; also <shape>.numSplines
setFirstSpline <shape> <idx>  -- reorder so idx becomes spline 1
addAndWeld <toShape> <fromShape> <threshold> -- add splines and weld endpoints
```

## Spline Methods

```maxscript
addNewSpline <shape>      -- returns new spline index
deleteSpline <shape> <spline_idx>
numSegments <shape> <spline_idx>
numKnots <shape> [<spline_idx>]  -- without idx: total knots in shape
isClosed <shape> <spline_idx>    -- true/false
close <shape> <spline_idx>
open <shape> <spline_idx>
reverse <shape> <spline_idx> [keepFirstKnot:false]
setFirstKnot <shape> <spline_idx> <knot_idx>
weldSpline <shape> <tolerance>   -- welds selected knots

-- Selection
getSplineSelection <shape>       -- returns #(indices...)
setSplineSelection <shape> #(1,3) [keep:false]
```

## Knot Methods

```maxscript
-- Add knot (returns knot index)
addKnot <shape> <spline_idx> (#smooth|#corner|#bezier|#bezierCorner) \
        (#curve|#line) <point3> [<inVec> <outVec>] [where_int]
-- For #bezier/#bezierCorner: inVec and outVec are REQUIRED

deleteKnot <shape> <spline_idx> <knot_idx>

-- Get/Set knot type
getKnotType <shape> <spline_idx> <knot_idx>  -- returns #smooth etc.
setKnotType <shape> <spline_idx> <knot_idx> (#smooth|#corner|#bezier|#bezierCorner)

-- Get/Set knot position
getKnotPoint <shape> <spline_idx> <knot_idx>  -- returns point3
setKnotPoint <shape> <spline_idx> <knot_idx> <point3>

-- Get/Set tangent handles (position, not vector)
getInVec <shape> <spline_idx> <knot_idx>
setInVec <shape> <spline_idx> <knot_idx> <point3>
getOutVec <shape> <spline_idx> <knot_idx>
setOutVec <shape> <spline_idx> <knot_idx> <point3>

-- Selection
getKnotSelection <shape> <spline_idx>     -- returns #(indices...)
setKnotSelection <shape> <spline_idx> #(1,3) [keep:false]
```

## Segment Methods

```maxscript
getSegmentType <shape> <spline_idx> <seg_idx>   -- #curve or #line
setSegmentType <shape> <spline_idx> <seg_idx> (#curve|#line)

-- Add knot at position along segment (0.0-1.0), returns new knot index
refineSegment <shape> <spline_idx> <seg_idx> <param_float>

-- Subdivide segment into N divisions
subdivideSegment <shape> <spline_idx> <seg_idx> <divisions>

-- Selection
getSegSelection <shape> <spline_idx>
setSegSelection <shape> <spline_idx> #(1,2) [keep:false]

-- Material IDs
setMaterialID <shape> <spline_idx> <seg_idx> <matID>
getMaterialID <shape> <spline_idx> <seg_idx>

-- Segment lengths
getSegLengths <shape> <spline_idx> [cum:false] [byVertex:false] [numArcSteps:100]
```

## Path Interpolation

Two interpolation modes: **path** (vertex-based, non-uniform) and **length** (arc-length, uniform speed).

```maxscript
-- On SplineShape (low-level, spline index required)
interpCurve3D <shape> <spline_idx> <param_0to1> [pathParam:false]
tangentCurve3D <shape> <spline_idx> <param_0to1> [pathParam:false]
-- pathParam:false = length-based (uniform), pathParam:true = vertex-based

-- Per-segment interpolation
interpBezier3D <shape> <spline_idx> <seg_idx> <param> [pathParam:false]
tangentBezier3D <shape> <spline_idx> <seg_idx> <param> [pathParam:false]

-- Find segment from parameter
findPathSegAndParam <shape> <spline_idx> <param>   -- vertex interpolation
findLengthSegAndParam <shape> <spline_idx> <param>  -- length interpolation
-- Both return point2: [segment_index, fraction_within_segment]

-- On any shape node (high-level, optional curve_num defaults to 1)
pathInterp <shape> [<curve_num>] <param>       -- vertex-based point3
lengthInterp <shape> [<curve_num>] <param> [steps:<int>]  -- uniform point3
pathTangent <shape> [<curve_num>] <param>      -- vertex-based tangent
lengthTangent <shape> [<curve_num>] <param> [steps:<int>]
curveLength <shape> [<curve_num>]              -- arc length (no transforms)
nearestPathParam <shape> [<curve_num>] <point3> [steps:<int>]  -- closest param
pathToLengthParam <shape> [<curve_num>] <param> [steps:<int>]
lengthToPathParam <shape> [<curve_num>] <param> [steps:<int>]
resetLengthInterp()    -- clear cache if curve edited between calls
```

### Place objects along spline (uniform spacing)
```maxscript
sp = $MySpline
numObjs = 20
for i = 0 to (numObjs - 1) do (
    param = i / (numObjs - 1.0)
    pt = lengthInterp sp 1 param
    tan = lengthTangent sp 1 param
    p = point pos:pt size:5
)
```

## Spline Shape Common Methods

```maxscript
-- Offset (outline) -- converts to SplineShape if needed
applyOffset <shape> <offset_float>
-- Negative = left of spline direction. Open splines become closed outlines.

measureOffset <shape> <point3>
-- Returns signed distance from point to nearest spline point
```

## Rendering Splines (making them visible in render)

All shapes share these properties. Use directly on shape or via Renderable_Spline modifier.

### On the shape directly
```maxscript
ss = circle radius:50
ss.render_renderable = true          -- enable in renderer
ss.render_displayRenderMesh = true   -- show mesh in viewport
ss.render_useViewportSettings = false

-- Radial cross-section (default, render_rectangular = false)
ss.render_thickness = 2.0   -- diameter
ss.render_sides = 12
ss.render_angle = 0.0

-- Rectangular cross-section (set render_rectangular = true)
ss.render_rectangular = true
ss.render_length = 6.0
ss.render_width = 2.0
ss.render_angle2 = 0.0

-- Viewport overrides (when render_useViewportSettings = true)
ss.render_viewport_thickness = 1.0
ss.render_viewport_sides = 8

-- Mapping and smoothing
ss.render_mapcoords = true
ss.render_auto_smooth = true
ss.render_threshold = 40.0
```

### Renderable_Spline modifier
```maxscript
sp = circle radius:50
addModifier sp (Renderable_Spline())
sp.modifiers[#Renderable_Spline].renderable = true
sp.modifiers[#Renderable_Spline].thickness = 3.0
sp.modifiers[#Renderable_Spline].sides = 8
```

## splineOps (Modify Panel Operations)

These mirror UI buttons. Shape must be selected and in modify panel context.

```maxscript
splineOps.startCreateLine <shape>
splineOps.startBreak <shape>
splineOps.startAttach <shape>
splineOps.attachMultiple <shape>
splineOps.startRefine <shape>
splineOps.weld <shape>            -- welds selected verts
splineOps.startConnect <shape>
splineOps.startInsert <shape>
splineOps.makeFirst <shape>
splineOps.fuse <shape>
splineOps.reverse <shape>         -- spline sub-object level
splineOps.close <shape>
splineOps.delete <shape>
splineOps.divide <shape>          -- segment level
splineOps.detach <shape>
splineOps.explode <shape>
splineOps.startFillet <shape>
splineOps.startChamfer <shape>
splineOps.startOutline <shape>
splineOps.startTrim <shape>
splineOps.startExtend <shape>
-- Boolean (spline level, one closed spline selected)
splineOps.startUnion <shape>
splineOps.startSubtract <shape>
splineOps.intersect <shape>
-- Mirror (spline level)
splineOps.mirrorHoriz <shape>
splineOps.mirrorVert <shape>
splineOps.mirrorBoth <shape>
```

## Vertex Animation

```maxscript
animateVertex <shape> #all    -- or specific indices: #(1,2,3)
-- Exposes Spline_N___Vertex_N, Spline_N___InVec_N, Spline_N___OutVec_N
-- as animatable Point3 properties in Track View
```

## Common Patterns

### Convert shape to editable, modify knots
```maxscript
c = circle radius:50
convertToSplineShape c
-- Now c is a SplineShape with 4 knots
for k = 1 to (numKnots c 1) do (
    local pt = getKnotPoint c 1 k
    setKnotPoint c 1 k (pt + [0, 0, random -10 10])
)
updateShape c
```

### Section contour lines from mesh
```maxscript
obj = $MyMesh
for z = obj.min.z to obj.max.z by 10 do (
    s = section pos:[0, 0, z]
    max views redraw  -- required before converting
    convertToSplineShape s
    s.render_renderable = true
    s.render_thickness = 0.5
)
```

### Create renderable pipe along path
```maxscript
sp = splineShape()
addNewSpline sp
addKnot sp 1 #smooth #curve [0,0,0]
addKnot sp 1 #smooth #curve [50,30,20]
addKnot sp 1 #smooth #curve [100,0,40]
updateShape sp
sp.render_renderable = true
sp.render_displayRenderMesh = true
sp.render_thickness = 5.0
sp.render_sides = 12
```

### Walk along spline, place clones
```maxscript
fn distributeAlongSpline shp obj count = (
    for i = 0 to (count-1) do (
        local param = i / (count - 1.0)
        local pt = lengthInterp shp 1 param
        local tan = lengthTangent shp 1 param
        local inst = instance obj
        inst.pos = pt
        inst.dir = tan  -- orient along tangent
    )
)
```

### Weld endpoints to close gap
```maxscript
ss = splineShape()
addNewSpline ss
addKnot ss 1 #corner #line [0,0,0]
addKnot ss 1 #corner #line [100,0,0]
addKnot ss 1 #corner #line [100,50,0]
addKnot ss 1 #corner #line [1,0,0]  -- almost back to start
close ss 1
updateShape ss
select ss
subobjectlevel = 1
setKnotSelection ss 1 #(1,4) keep:false
weldSpline ss 5.0  -- weld within 5 units
updateShape ss
```

### Get total spline length
```maxscript
totalLen = curveLength $MySpline 1  -- curve index 1
-- Note: does not include node-level scale transforms
```

## Key Gotchas

- Always call `updateShape` after batch modifications, before anything else touches the shape.
- In/out vectors are absolute handle positions, NOT relative direction vectors.
- Coordinates are in the current MAXScript working coordinate system (world by default), not object-local.
- `convertToSplineShape` is required before using knot/spline methods on primitive shapes.
- If modifying a shape that is selected in the Modify panel, `updateShape` drops the sub-object selection.
- Spline and knot indices start at 1.
- `numSegments` = `numKnots` for closed splines, `numKnots - 1` for open.
- The `Section` shape requires a viewport redraw before `convertToSplineShape` inside loops.
