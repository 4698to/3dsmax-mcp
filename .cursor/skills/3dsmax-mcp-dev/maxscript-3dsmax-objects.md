# MAXScript: 3ds Max Objects Reference

## Node Subclasses

All scene objects are nodes. Subclasses: `GeometryClass`, `Shape`, `Light`, `Camera`, `Helper`, `SpacewarpObject`, `System`.

## Creating Objects

```maxscript
-- Constructor syntax: ClassName [keyword:value ...]
b = box length:50 width:30 height:20 pos:[0,0,0] name:"MyBox"
s = sphere radius:25 segs:32 pos:[100,0,0]
c = cylinder radius:10 height:40 sides:24
t = teapot radius:15
p = plane length:200 width:200 lengthsegs:10 widthsegs:10
cn = cone radius1:20 radius2:0 height:40
tr = torus radius1:30 radius2:5
tb = tube radius1:20 radius2:15 height:30
py = pyramid width:30 depth:30 height:40
gs = geosphere radius:20

-- Common constructor keywords (apply to all node types):
-- name: prefix: pos: position: rotation: scale: pivot:
-- transform: isSelected: dir: material: target:
b = box name:"foo" pos:[10,10,10] height:20 material:(standard())
-- prefix: generates unique names
for i in 1 to 100 do sphere prefix:"baz"  -- $baz001, $baz002 ...
```

## Standard Geometry Properties

```maxscript
-- Box: .length .width .height .lengthsegs .widthsegs .heightsegs .mapcoords
-- Sphere: .radius .segs .smooth .hemisphere .chop .slice .sliceFrom .sliceTo
-- Cylinder: .radius .height .heightsegs .capsegs .sides .smooth .slice
-- Teapot: .radius .segs .smooth .body .handle .spout .lid
-- Plane: .length .width .lengthsegs .widthsegs
-- Cone: .radius1 .radius2 .height .heightsegs .capsegs .sides .smooth
-- Torus: .radius1 .radius2 .segs .sides .smooth
```

## Node Properties

```maxscript
<node>.name             -- String: get/set name
<node>.baseObject       -- base object before modifiers
<node>.material         -- Material or undefined
<node>.parent           -- parent node or undefined
<node>.children         -- NodeChildrenArray (iterable)
<node>.wireColor        -- Color: wireframe display color
<node>.isHidden         -- Boolean: hidden in viewport
<node>.isFrozen         -- Boolean: frozen state
<node>.isSelected       -- Boolean: selection state
<node>.renderable       -- Boolean: renderable flag
<node>.castShadows      -- Boolean
<node>.receiveShadows   -- Boolean
<node>.visibility       -- Boolean (animatable): render visibility
<node>.gbufferChannel   -- Integer: Object ID (0-65535)
<node>.mesh             -- TriMesh: copy of world-state mesh
<node>.boundingBox      -- Box3 (read-only, 2022+)

-- Hierarchy
$foo.parent = $baz              -- link child to parent
$foo.parent = undefined         -- unlink (make top-level)
append $foo.children $baz       -- add child
num = $foo.children.count       -- count children
for c in $baz.children do print c

-- classOf returns world-state class (top of stack)
classOf $box01                  -- Box (or Editable_Mesh if modifiers collapsed)
classOf $box01.baseObject       -- always the original class
```

## Accessing Nodes

```maxscript
$Box01                          -- by name (scene path)
$box*                           -- wildcard: all nodes starting with "box"
$                               -- current selection (objectSet)
selection                       -- current selection
objects                         -- all scene objects
geometry                        -- all geometry
lights                          -- all lights
cameras                         -- all cameras
helpers                         -- all helpers
shapes                          -- all shapes

-- By name programmatically
getNodeByName "Box01"                           -- first match
getNodeByName "Box01" exact:true                -- exact name
getNodeByName "Box01" all:true                  -- array of all matches
getNodeByName "Box01" ignoreCase:false          -- case sensitive

-- Objectset indexing
objects[1]                      -- first scene object
objects.count                   -- total object count
```

## Selection

```maxscript
select $Box01                   -- select single node (deselects others)
select $box*                    -- select by wildcard
select geometry                 -- select all geometry
selectMore $Sphere01            -- add to selection
deselect $box*                  -- deselect matching
clearSelection()                -- deselect all

-- Build selection programmatically
clearSelection()
for obj in objects where classOf obj == Box do selectMore obj
```

## Scene Traversal

```maxscript
-- Iterate all objects
for obj in objects do format "%: %\n" obj.name (classOf obj)

-- Iterate geometry only
for obj in geometry do print obj.name

-- Collect by class
allBoxes = for obj in objects where classOf obj == Box collect obj

-- Collect by property
bigObjects = for obj in geometry where obj.max.z > 100 collect obj

-- Collect by name pattern
fooObjs = for obj in objects where matchPattern obj.name pattern:"foo*" collect obj

-- Recursive hierarchy traversal
fn traverseHierarchy node level:0 =
(
    format "%\n" node.name
    for child in node.children do
        traverseHierarchy child level:(level+1)
)
for obj in objects where obj.parent == undefined do traverseHierarchy obj

-- getClassInstances: find all instances of a class in scene
allBitmaps = getClassInstances bitmaptex
allBends = getClassInstances Bend
getClassInstances sphere target:$myNode  -- search under specific node

-- Test nodes
IsValidNode $Box01     -- true if exists and not deleted
isShapeObject $Line01  -- true if shape
isGroupHead $grp01     -- true if group head
isGroupMember $obj01   -- true if in a group
```

## Transforms

```maxscript
-- Properties (respect current coordsys; default = world)
<node>.pos          -- Point3: position (.position synonym)
<node>.rotation     -- Quat: rotation
<node>.scale        -- Point3: scale (fraction, [1,1,1] = 100%)
<node>.transform    -- Matrix3: full transform matrix
<node>.pivot        -- Point3: pivot point position
<node>.dir          -- Point3: local Z-axis direction
<node>.center       -- Point3: bounding box center (read-only)
<node>.min          -- Point3: bounding box min (read-only)
<node>.max          -- Point3: bounding box max (read-only)

-- Object offset (geometry offset from pivot)
<node>.objectOffsetPos    -- Point3
<node>.objectOffsetRot    -- Quat
<node>.objectOffsetScale  -- Point3
<node>.objectTransform    -- Matrix3 (read-only): combined node + offset

-- Setting position (absolute)
$Box01.pos = [100, 50, 0]

-- Move (relative/incremental)
move $Box01 [10, 0, 0]           -- moves BY offset, not TO

-- Rotate
rotate $Box01 (eulerangles 0 45 0)
rotate $Box01 (angleaxis 30 [0,0,1])
rotate $Box01 (quat 0 0 0.383 0.924)

-- Scale (relative/multiplicative)
scale $Box01 [2, 2, 2]           -- doubles size each call

-- Coordinate systems
in coordsys world  move $Box01 [10,0,0]
in coordsys local  rotate $Box01 (eulerangles 0 0 45)
in coordsys parent move $Box01 [0,0,10]

-- Set rotation in world space without affecting position
fn setWorldRotation node rot =
(
    in coordsys (transmatrix node.transform.pos)
        node.rotation = rot
)
setWorldRotation $Box01 (eulerangles 45 45 0)

-- Extract from matrix
pos = $Box01.transform.translationpart   -- Point3
rot = $Box01.transform.rotationpart      -- Quat
scl = $Box01.transform.scalepart         -- Point3
```

## Cloning

```maxscript
newObj = copy $Box01                -- independent copy
newObj = instance $Box01            -- instance (shared base object)
newObj = reference $Box01           -- reference (shared base, local mods)
snap = snapshot $Box01              -- mesh snapshot (collapsed world state)

-- Clone with keywords
newObj = copy $Box01 pos:[100,0,0] name:"CopyBox"

-- Preferred method (retains interdependencies)
maxOps.cloneNodes $Box01 cloneType:#copy newNodes:&clones
-- cloneType: #copy | #instance | #reference

-- Check instances
areNodesInstances $Box01 $Box02
```

## Modifier Stack

```maxscript
-- Access modifiers on a node
$Box01.modifiers            -- array of modifiers
$Box01.modifiers.count      -- number of modifiers
$Box01.modifiers[1]         -- top modifier (index from top)
$Box01.modifiers[#Bend]     -- by class name
$Box01.bend                 -- shorthand by name (if unique)

-- Add modifier
addModifier $Box01 (Bend angle:45)
addModifier $Box01 (TurboSmooth iterations:2)
addModifier $Box01 (Bend()) before:2   -- insert before 2nd modifier

-- Modifier properties
$Box01.bend.angle = 90
$Box01.bend.direction = 45
$Box01.bend.axis = 2              -- 0=X, 1=Y, 2=Z
$Box01.bend.enabled = false       -- disable modifier
$Box01.bend.enabledInViews = false
$Box01.bend.enabledInRenders = false

-- Delete modifier
deleteModifier $Box01 1           -- by index (from top)
deleteModifier $Box01 $Box01.bend -- by reference

-- Collapse stack
collapseStack $Box01              -- collapse to editable base class

-- Validate before adding
if validModifier $Box01 Bend do addModifier $Box01 (Bend())

-- Common modifiers
addModifier obj (Bend angle:30 direction:0 axis:2)
addModifier obj (Taper amount:0.5)
addModifier obj (Twist angle:90)
addModifier obj (NoiseModifier scale:20 strength:[10,10,10])
addModifier obj (TurboSmooth iterations:2)
addModifier obj (Shell innerAmount:0 outerAmount:2)
addModifier obj (UVWmap maptype:4)       -- 0=Planar,1=Cyl,2=Sph,3=Shrink,4=Box,5=Face
addModifier obj (Symmetry())
addModifier obj (Edit_Poly())
addModifier obj (meshSmooth())
addModifier obj (Skin())
addModifier obj (Morpher())

-- Modifier gizmo transform
$Box01.bend.gizmo.position = [0,0,10]
$Box01.bend.gizmo.rotation = quat 0 0 0 1
```

## Node Conversion

```maxscript
convertToMesh $Box01              -- to Editable Mesh (removes modifiers)
convertToSplineShape $Circle01    -- to SplineShape
convertTo $Box01 Editable_Poly    -- to Editable Poly
convertTo $Box01 NURBSSurface     -- to NURBS
canConvertTo $Box01 Editable_Poly -- test before converting: true/false
```

## Materials

```maxscript
-- Assign material
$Box01.material = Standard()
$Box01.material = PhysicalMaterial()

-- Standard Material
mat = Standard()
mat.diffuse = color 200 50 50
mat.specular = color 255 255 255
mat.specularLevel = 80
mat.glossiness = 50
mat.opacity = 100
mat.selfIllumAmount = 0
mat.twoSided = true
mat.diffuseMap = bitmaptex filename:"C:/tex/diffuse.jpg"
mat.diffuseMapEnable = true
mat.bumpMap = bitmaptex filename:"C:/tex/bump.jpg"
mat.bumpMapAmount = 50
mat.bumpMapEnable = true
mat.opacityMap = bitmaptex filename:"C:/tex/alpha.png"
mat.showInViewport = true
$Box01.material = mat

-- Physical Material (2017+)
pm = PhysicalMaterial()
pm.base_color = color 200 100 50
pm.metalness = 0.0             -- 0=dielectric, 1=metal
pm.roughness = 0.3
pm.reflectivity = 1.0
pm.transparency = 0.0
pm.trans_ior = 1.52
pm.emission = 0.0
pm.bump_map = bitmaptex filename:"C:/tex/normal.png"
pm.bump_map_amt = 1.0
pm.base_color_map = bitmaptex filename:"C:/tex/albedo.jpg"
$Box01.material = pm

-- Multi/Sub-Object Material
mm = multimaterial numsubs:3
mm.materialList[1] = Standard diffuse:(color 255 0 0)
mm.materialList[2] = Standard diffuse:(color 0 255 0)
mm.materialList[3] = Standard diffuse:(color 0 0 255)
mm.materialIDList = #(1, 2, 3)
$Box01.material = mm

-- Access sub-materials
getNumSubMtls mat              -- count sub-materials
getSubMtl mm 1                 -- get indexed sub-material
setSubMtl mm 1 (Standard())   -- set indexed sub-material

-- Texture maps
getNumSubTexmaps mat           -- count sub-texmaps
getSubTexmap mat 1             -- get indexed sub-texmap
setSubTexmap mat 1 (Checker()) -- set indexed sub-texmap

-- Material from scene
sceneMats = for obj in geometry where obj.material != undefined collect obj.material
```

## Groups

```maxscript
grp = group #($Box01, $Box02) name:"MyGroup"
group selection name:"SelGroup"
ungroup $MyGroup                  -- ungroup one level
explodeGroup $MyGroup             -- ungroup all levels
setGroupOpen $MyGroup true        -- open group
setGroupOpen $MyGroup false       -- close group
isGroupHead $MyGroup              -- true
isGroupMember $Box01              -- true if in group
attachNodesToGroup #($Sphere01) $MyGroup   -- add to group (2010+)
detachNodesFromGroup #($Box01)             -- remove from group (2010+)
```

## Inspection / Introspection

```maxscript
showClass "box.*"               -- list properties for Box class
showClass "*:modifier*"         -- list all modifier classes
showProperties $Box01           -- properties of selected object
showProperties $Box01.bend      -- properties of a modifier
getPropNames $Box01             -- array of property names
getPropNames $Box01.bend        -- array of modifier property names

getProperty $Box01 #height      -- get by computed name
setProperty $Box01 #height 50   -- set by computed name
hasProperty $Box01 "height"     -- true (supports wildcards)
isProperty $Box01 #height       -- true (exact match)
isPropertyAnimatable $Box01 #height  -- true

-- Dump all properties
for p in getPropNames $Box01 do
    format "% = %\n" p (getProperty $Box01 p)

classOf $Box01                  -- class of world state
superClassOf $Box01             -- GeometryClass
```

## Deleting Objects

```maxscript
delete $Box01                   -- delete single
delete $box*                    -- delete by wildcard
delete selection                -- delete selection
delete objects                  -- delete all
```

## Hierarchy / Parenting

```maxscript
$child.parent = $parentObj      -- link
$child.parent = undefined       -- unlink
attachObjects $parent $child move:false  -- attach without moving child
-- Iterate children
for c in $parent.children do print c.name
```

## Common Patterns

```maxscript
-- Create array of objects in a grid
for x = 0 to 4 do
    for y = 0 to 4 do
        box pos:[x*50, y*50, 0] length:20 width:20 height:20

-- Apply modifier to selection
for obj in selection do
    addModifier obj (TurboSmooth iterations:1)

-- Set material on all geometry
redMat = Standard diffuse:red
for obj in geometry do obj.material = redMat

-- Find objects by class
allSpheres = for obj in objects where classOf obj == Sphere collect obj

-- Find objects by material
objsWithMat = for obj in geometry where obj.material != undefined \
    and classOf obj.material == Standardmaterial collect obj

-- Rename objects sequentially
for i = 1 to selection.count do selection[i].name = "Part_" + (formattedPrint i format:"03d")

-- Randomize transforms
for obj in selection do
(
    obj.pos += random [-10,-10,-10] [10,10,10]
    rotate obj (eulerangles (random -15 15) (random -15 15) (random -15 15))
)

-- Copy modifiers from one object to another
srcMods = $source.modifiers
for i = srcMods.count to 1 by -1 do
    addModifier $target (copy srcMods[i])
```

## Object Sets / Collections

```maxscript
objects     -- all nodes
geometry    -- GeometryClass nodes
lights      -- Light nodes
cameras     -- Camera nodes
helpers     -- Helper nodes
shapes      -- Shape nodes
selection   -- selected nodes
$pattern*   -- wildcard path name

-- Mapped operations (apply to collections automatically)
hide geometry           -- hide all geometry
unhide objects          -- unhide all
freeze selection        -- freeze selection
unfreeze objects
move $box* [0,0,10]     -- move all box* up 10
delete $temp*           -- delete all temp*
```
