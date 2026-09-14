# MAXScript: Scripted Plugins Reference

## Plugin Definition Syntax

```maxscript
plugin <superclass> <varname>
    name:<string>            -- UI display name (button/list entry)
    classID:#(<int>,<int>)   -- unique persistent ID (use genClassID() to generate)
    category:<string>        -- Create panel category (default "Standard")
    extends:<maxClass>       -- base class to extend (delegation)
    replaceUI:<boolean>      -- replace delegate rollouts (default false)
    version:<integer>        -- definition version (default 1)
    invisible:<boolean>      -- hide from Create panel (default false)
    silentErrors:<boolean>   -- suppress runtime errors (default false)
    autoPromoteDelegateProps:<boolean> -- auto-search delegate props (default false)
( <plugin_body> )
```

**Superclasses:** `Geometry`, `SimpleObject`, `Shape`, `Light`, `Camera`, `Helper`, `Modifier`, `SimpleMod`, `SimpleMeshMod`, `TrackViewUtility`, `Material`, `TextureMap`, `RenderEffect`, `Atmospheric`, `SimpleManipulator`, `floatController`, `point3Controller`, `point4Controller`, `colorController`, `positionController`, `rotationController`, `scaleController`, `transformController`

**Generate class ID:**
```maxscript
genClassID()                    -- prints random ID to Listener
genClassID returnValue:true     -- returns #(0x..., 0x...)
```

## Built-in Plugin Locals

Available in all scripted plugins:
- `this` -- current plugin instance
- `delegate` -- extended plugin's base object (undefined if no extends:)
- `version` -- integer version number
- `loading` -- true during scene load

## Parameter Blocks

```maxscript
parameters <blockName> [rollout:<rolloutName>] [type:#class]
(
    <paramName> type:<#type> [default:<val>] [animatable:<bool>]
        [ui:<uiName>] [subAnim:<bool>] [tabSize:<int>] [tabSizeVariable:<bool>]
        [invisibleInTV:<bool>]
)
```

Each parameter block links to ONE rollout; each rollout links to ONE parameter block.
`type:#class` makes parameters shared across all instances (class-level params).

### Parameter Types

| Type | Animatable | Notes |
|------|-----------|-------|
| `#float` | yes | |
| `#integer` | yes | |
| `#index` | yes | 0-based internal, 1-based in MXS |
| `#boolean` | yes | |
| `#color` / `#rgb` | yes | |
| `#frgba` | yes | float RGBA |
| `#point2` | no | since 2018 |
| `#point3` | yes | |
| `#point4` | yes | |
| `#angle` | yes | |
| `#percent` | yes | |
| `#worldUnits` | yes | system-units float |
| `#colorChannel` | yes | |
| `#time` | yes | |
| `#string` | no | |
| `#filename` | no | use with `assetType:` |
| `#matrix3` | no | |
| `#node` | no | use `subAnim:true` for TV |
| `#material` | no | use `subAnim:true` for TV |
| `#texturemap` | no | use `subAnim:true` for TV |
| `#bitmap` | no | |
| `#maxObject` | no | |
| `#radiobtnIndex` | no | |

**Tab (array) variants:** append `Tab` to any type name: `#floatTab`, `#intTab`, `#nodeTab`, `#point3Tab`, etc. Use `tabSize:` for initial count, `tabSizeVariable:true` to allow resize.

### Parameter-to-UI Wiring

| Param type | UI control |
|-----------|-----------|
| `#integer` | spinner, slider, radioButtons, checkbox, checkbutton |
| `#float` / `#angle` / `#percent` / `#worldUnits` / `#time` / `#colorChannel` | spinner, slider |
| `#boolean` | checkbox, checkbutton |
| `#color` | colorpicker |
| `#node` | pickButton |
| `#texturemap` | mapButton |
| `#material` | materialButton |

Tab params wire to multiple UI items: `ui:(item1, item2, item3)`.

### Parameter Event Handlers

```maxscript
on <param> set <newVal> do ( ... )         -- called on every value change
on <param> get <curVal> do ( ...; curVal )  -- must return a value
on <param> preSet <newVal> do ( ... )       -- original val in param; return altered val
on <param> postSet <newVal> do ( ... )      -- param already updated
-- Tab params get index as 2nd arg:
on <param> set <val> <index> do ( ... )
on <param> get <val> <index> do ( ...; val )
on <param> tabChanged <changeType> <tabIndex> <count> do ( ... )
-- changeType: #insert | #append | #delete | #refDeleted | #setCount | #sort
```

## Plugin Event Handlers (All Types)

```maxscript
on create do ( ... )               -- new instance created
on postCreate do ( ... )           -- after create + all param set handlers
on load do ( ... )                 -- instance loaded from file
on postLoad do ( ... )             -- after load + all param set handlers
on update do ( ... )               -- definition changed, existing instances updated
on clone <original> do ( ... )     -- called on the NEW clone
on attachedToNode <node> do ( ... )
on detachedFromNode <node> do ( ... )
on deleted do ( ... )
on refAdded <caller> do ( ... )
on refDeleted <caller> do ( ... )
```

## Mouse Tools (create tool)

```maxscript
tool create [numPoints:<int>]
(
    on mousePoint <clickNum> do ( ... )  -- each click (1=first down, 2=first up, ...)
    on mouseMove <clickNum> do ( ... )   -- drag/move after first click
    on mouseAbort <clickNum> do ( ... )  -- right-click / ESC
    on freeMove do ( ... )               -- move before first click
    on start do ( ... )
    on stop do ( ... )
)
```

**Tool locals (auto-available):**
- `worldPoint` (Point3), `gridPoint` (Point3) -- mouse pos
- `worldDist` (Point3), `gridDist` (Point3) -- delta from last click
- `worldAngle`, `gridAngle` (Point3)
- `viewPoint` (Point2) -- screen coords
- `nodeTM` (Matrix3) -- node transform (read/write, grid coords)
- `shiftKey`, `ctrlKey`, `altKey`, `lButton`, `mButton`, `rButton` (Boolean)

Return `#stop` from any handler to end the tool.
For SimpleObject plugins, ALWAYS use `gridPoint`/`gridDist` (not world).

## Scripted SimpleObject (Geometry Primitives)

Superclass: `SimpleObject`. Requires `create` tool and `on buildMesh` handler.
Built-in local: `mesh` (TriMesh) -- set this in buildMesh.

```maxscript
plugin simpleObject myBox
    name:"MyBox"
    classID:#(0x1a2b3c4d, 0x5e6f7a8b)
    category:"Scripted Primitives"
(
    parameters main rollout:params
    (
        width  type:#worldUnits ui:spnW default:0
        height type:#worldUnits ui:spnH default:0
        depth  type:#worldUnits ui:spnD default:0
    )
    rollout params "Parameters"
    (
        spinner spnW "Width"  type:#worldunits range:[0,1e9,0]
        spinner spnH "Height" type:#worldunits range:[0,1e9,0]
        spinner spnD "Depth"  type:#worldunits range:[0,1e9,0]
    )
    on buildMesh do
    (
        setMesh mesh \
            verts:#([0,0,0],[width,0,0],[width,depth,0],[0,depth,0]) \
            faces:#([3,2,1],[1,4,3])
        extrudeFace mesh #(1,2) height 0 dir:#common
    )
    tool create
    (
        on mousePoint click do case click of
        (
            1: nodeTM.translation = gridPoint
            3: #stop
        )
        on mouseMove click do case click of
        (
            2: (width = gridDist.x; depth = gridDist.y)
            3: height = gridDist.z
        )
    )
)
```

**Optional SimpleObject handlers:**
```maxscript
on OKtoDisplay do <bool_expr>          -- can mesh be drawn?
on hasUVW do <bool_expr>               -- has UVW coords?
on setGenUVW <bool> do ( ... )         -- Max requests mapping coords
```

## Scripted SimpleMod (Vertex Deformers)

Superclass: `SimpleMod`. Core handler: `on map`. Auto-provides gizmo + center sub-objects.

```maxscript
plugin simpleMod mySaddle
    name:"Saddle"
    classID:#(0x11111111, 0x22222222)
    version:1
(
    parameters main rollout:params
    (
        amount type:#float ui:spnAmt default:10
    )
    rollout params "Saddle Params"
    (
        spinner spnAmt "Amount:" range:[0,1000,10]
    )
    on map i p do
    (
        -- i=vertex index (0=gizmo bbox call), p=Point3 position
        p.z += amount * sin(p.x * 22.5 / extent.x) * sin(p.y * 22.5 / extent.y)
        p  -- MUST return modified point
    )
)
```

**Map handler locals:** `min`, `max`, `center`, `extent` (bounding box of modifier context).

**Optional limit display:**
```maxscript
on modLimitZMin do <float_expr>
on modLimitZMax do <float_expr>
on modLimitAxis do #x | #y | #z
```

## Scripted SimpleMeshMod (Topology-Changing Modifiers)

Superclass: `SimpleMeshMod`. Since 3ds Max 2016. Core handler: `on modifyMesh`.
Built-in locals: `mesh` (incoming TriMesh, modify in place), `owningNode`, `transform`, `inverseTransform`, `boundingBox`, plus SimpleMod locals.

```maxscript
plugin simpleMeshMod cloneMod
    name:"CloneMod"
    classID:#(0x3a4087e9, 0x2c2fdff7)
    category:"MXS"
(
    parameters main rollout:params
    (
        copies type:#integer ui:spnN default:2 animatable:true
        offset type:#float   ui:spnOff default:10 animatable:true
    )
    rollout params "Parameters"
    (
        spinner spnN "Copies:" type:#integer range:[1,100,2]
        spinner spnOff "Offset:" range:[0,1000,10]
    )
    on modifyMesh do
    (
        local work = copy mesh
        for n = 2 to copies do
        (
            meshop.moveVert work #all [offset,0,0]
            meshop.attach mesh work
        )
        free work
    )
)
```

## Scripted Modifier (Extending Existing Modifiers)

Superclass: `Modifier`. MUST use `extends:`. Use `delegate.<prop>` to control base modifier.

```maxscript
plugin modifier superBend
    name:"Super Bend"
    classID:#(0xAAAA, 0xBBBB)
    extends:Bend
    replaceUI:true
    version:1
(
    parameters main rollout:params
    (
        amt type:#float animatable:true ui:spnAmt default:0
        on amt set val do delegate.angle = val
    )
    rollout params "Super Bend"
    (
        spinner spnAmt "Bendiness:"
    )
)
```

## Scripted Geometry (Extending / System-Style)

Superclass: `Geometry`. With `extends:`, delegates to base class. Without extends, acts like a System object (no persistent instance unless classID given).

```maxscript
plugin geometry cuboid
    name:"Cuboid"
    classID:#(0x133067, 0x54374)
    category:"Scripted Primitives"
    extends:Box
(
    tool create
    (
        on mousePoint click do case click of
        (
            1: nodeTM.translation = gridPoint
            2: #stop
        )
        on mouseMove click do
            if click == 2 then
                delegate.width = delegate.length = delegate.height = \
                    2 * (amax (abs gridDist.x) (abs gridDist.y))
    )
)
```

## Scripted Helper

Superclass: `Helper`. Must extend an existing helper (e.g., Dummy).

```maxscript
plugin helper myHelper
    name:"My Helper"
    classID:#(0x47db14fe, 0x4e9b5f90)
    category:"Standard"
    extends:Dummy
(
    local lastSize, meshObj
    parameters pblock rollout:params
    (
        size type:#float animatable:true ui:spnSize default:40
    )
    rollout params "Parameters"
    (
        spinner spnSize "Size:" range:[0,1e9,40]
    )
    on getDisplayMesh do
    (
        if meshObj == undefined do
            (meshObj = createInstance box length:size width:size height:size; lastSize = size)
        if size != lastSize do
            (meshObj.length = meshObj.width = meshObj.height = size; lastSize = size)
        meshObj.mesh
    )
    on useWireColor do true   -- use node wire color for display
    tool create
    (
        on mousePoint click do (nodeTM.translation = gridPoint; #stop)
    )
)
```

## Scripted Shape (Extending Splines)

```maxscript
plugin shape roundRect
    name:"RoundRect"
    classID:#(0x133067, 0x54375)
    extends:Rectangle
    category:"Splines"
    version:1
(
    tool create
    (
        local sp
        on mousePoint click do case click of
        (
            1: sp = nodeTM.translation = gridPoint
            3: #stop
        )
        on mouseMove click do case click of
        (
            2: (delegate.width = abs gridDist.x; delegate.length = abs gridDist.y;
                nodeTM.translation = sp + gridDist/2.0)
            3: delegate.corner_radius = amax 0 (-gridDist.x)
        )
    )
)
```

## Scripted Utility (Utility Panel)

Not a plugin per se, but a common tool type. Appears in Utilities panel list.

```maxscript
utility myTool "My Tool Name"
    rolledUp:false
    silentErrors:false
(
    local myVar = 0
    spinner spnVal "Value:" range:[0,100,50]
    button btnGo "Execute" width:140
    on btnGo pressed do
    (
        for obj in selection do obj.pos.z += spnVal.value
    )
    on myTool open do ( ... )   -- utility opened
    on myTool close do ( ... )  -- utility closed
)
```

**Multi-rollout utility:**
```maxscript
utility myUtil "Multi Rollout"
(
    rollout r1 "Settings" ( spinner spn1 "Val:" )
    rollout r2 "Actions" ( button btn1 "Go" )
    on myUtil open do (addRollout r1; addRollout r2)
    on myUtil close do (removeRollout r1; removeRollout r2)
)
```

## MacroScript vs Plugin

| Feature | MacroScript | Scripted Plugin |
|---------|------------|----------------|
| Purpose | Toolbar/menu action | New object/modifier/material class |
| Persistence | Button binding | Scene-savable with classID |
| Parameters | No param blocks | Full param blocks, animatable |
| UI location | Floating dialog or none | Command panel / Material Editor |

```maxscript
macroScript myAction
    category:"MyTools"
    tooltip:"Do Something"
    buttonText:"DoIt"
    icon:#("MyIcons",1)        -- legacy icon
    -- iconName:"MyIcons/icon"  -- modern (2017+)
(
    on isEnabled do selection.count > 0
    on isChecked do false
    on execute do ( ... )
    on closeDialogs do ( ... )  -- toggle off (requires isChecked)
)
```

## Standalone Mouse Tool (Outside Plugins)

```maxscript
tool myTool prompt:"Click to place boxes" numPoints:5
(
    local b
    on mousePoint click do
    (
        if click == 1 then b = box pos:worldPoint
        else #stop
    )
    on mouseMove click do b.pos = worldPoint
)
startTool myTool [snap:#3D] [prompt:"..."]
stopTool myTool  -- abort programmatically
```

## Key Patterns & Tips

- `createInstance <class>` creates base objects without scene nodes (use in SimpleObject buildMesh).
- `addPluginRollouts <obj>` installs rollouts for another object during a create tool (System-style plugins).
- Param `set` handlers fire on create AND load. Use `loading` variable to skip logic during load.
- Tab-type param `set` handlers do NOT fire on create/load; use `tabChanged` instead.
- `delegate` is the base object, NOT the node. Access node transform via `nodeTM` in create tools.
- SimpleMod `on map` is called with index=0 for gizmo bbox display. Guard index-dependent logic.
- Errors in `buildMesh` / `map` / `modifyMesh` DISABLE the plugin. Re-evaluate definition to re-enable.
- `replaceUI:true` hides delegate from TrackView.
- Params are NOT undoable. Use `theHold` for custom undo if needed.
- For extending plugins: properties resolve as common props > parameters > locals. Use `this.<param>` or `delegate.<prop>` to be explicit.
