# MAXScript: Rendering and Cameras Reference

## render() Function

The `render()` method invokes the current renderer. It does NOT use the Render Scene Dialog settings -- it operates independently.

```maxscript
-- Basic render to VFB
render()

-- Render from camera with output size
render camera:$cam01 outputwidth:1920 outputheight:1080

-- Alternative size syntax
render outputSize:[1920,1080]

-- Render to file
render camera:$cam01 outputfile:@"C:\output\frame.png" vfb:off

-- Render frame range
render camera:$cam01 framerange:(interval 0 100) outputfile:@"C:\output\frame.png"
render fromframe:10 toframe:50 nthframe:2

-- Render single frame
render frame:50
render frame:#current

-- Render into existing bitmap (reuses memory in loops)
bm = bitmap 1920 1080
render camera:$cam01 to:bm

-- Capture cancel state
render cancelled:&wasCancelled
if (not wasCancelled) do ( /* post-process */ )

-- Render with g-buffer channels
bm = render camera:$cam01 channels:#(#zDepth, #coverage, #objectID)

-- Render elements
render renderElements:true renderElementBitmaps:&reb outputfile:@"C:\out\beauty.exr"
if reb != undefined do for b in reb do display b

-- Quiet mode (suppress dialogs)
render quiet:true

-- Render types
render renderType:#normal       -- full view
render renderType:#selection    -- selected objects only
render renderType:#region region:#(100,100,500,400)
render renderType:#regionCrop region:#(100,100,500,400)

-- HDR output
render outputHDRbitmap:true

-- Color management (3ds Max 2024+)
render outputColorConversion:#automatic
render outputColorSpace:"sRGB"

-- Scanline-specific options
render antiAliasing:true shadows:true mapping:true
render objectMotionBlur:true imageMotionBlur:true
render force2sided:true forceWireframe:false
```

**Memory management in loops:**
```maxscript
bm = bitmap 640 480
for t = 0 to 100 do (
    sliderTime = t
    render to:bm outputfile:(@"C:\out\frame" + (formattedPrint t format:"04d") + ".png")
)
close bm
```

## Render Setup Dialog Globals

```maxscript
-- Output size (affects Render Scene Dialog)
renderWidth = 1920
renderHeight = 1080
renderPixelAspect = 1.0
rendImageAspectRatio = 1.777    -- sets renderHeight accordingly
rendLockImageAspectRatio = true

-- Time output
rendTimeType = 1   -- 1=Single, 2=Active segment, 3=Range, 4=Pickup frames
rendStart = 0f; rendEnd = 100f; rendNThFrame = 1
rendPickupFrames = "1,3,5-12"

-- Render output
rendSaveFile = true
rendOutputFilename = @"C:\output\render.png"
rendShowVFB = true

-- Options
rendHidden = false; rendForce2Side = false; rendAtmosphere = true
renderEffects = true; renderDisplacements = true
rendSimplifyAreaLights = false; rendColorCheck = false

-- Area to render
setRenderType #view   -- #view #selected #region #crop #blowUp
getRenderType()

-- Viewport lock
rendUseActiveView = true
rendViewIndex = 1         -- viewport index when locked

-- Scripts
usePreRendScript = true; preRendScript = @"C:\scripts\pre.ms"
usePostRendScript = true; postRendScript = @"C:\scripts\post.ms"

-- Aperture
getRendApertureWidth()
setRendApertureWidth 36.0
```

## Camera Types

### FreeCamera
```maxscript
cam = freeCamera pos:[0,-500,200] fov:45.0
cam.baseObject.targetDistance = 500.0   -- must use baseObject for free cam
```

### TargetCamera
```maxscript
cam = targetCamera pos:[0,-500,200] target:(targetObject pos:[0,0,100])
cam.fov = 60.0
```

### Physical Camera (3ds Max 2016+)
```maxscript
cam = Physical targeted:true pos:[0,-500,200] target:(targetObject pos:[0,0,0])
cam.focal_length_mm = 50.0
cam.film_width_mm = 36.0          -- "35mm" preset
cam.film_preset = "35mm"          -- or "APS-C (Canon)", "Custom", etc.
cam.f_number = 2.8
cam.specify_fov = false            -- true to use explicit .fov
cam.fov = 48.112

-- Focus / DOF
cam.use_dof = true
cam.specify_focus = 0              -- 0=Use Target Distance, 1=Custom
cam.focus_distance = 500.0

-- Exposure
cam.exposure_gain_type = 1         -- 0=Manual (uses .iso), 1=Target (uses .exposure_value)
cam.exposure_value = 6.0
cam.iso = 100.0

-- White balance
cam.white_balance_type = 0         -- 0=Illuminant, 1=Temperature, 2=Custom
cam.white_balance_illuminant = 0   -- 0=Daylight 6500K, 4=Incandescent 3200K...
cam.white_balance_kelvin = 5500.0

-- Shutter / motion blur
cam.motion_blur_enabled = true
cam.shutter_unit_type = 3          -- 0=1/sec, 1=sec, 2=degrees, 3=frames
cam.shutter_length_frames = 0.5

-- Bokeh
cam.bokeh_shape = 1                -- 0=Circular, 1=Bladed, 2=Custom Texture
cam.bokeh_blades_number = 7

-- Perspective control
cam.horizontal_shift = 0.0; cam.vertical_tilt_correction = 0.0
cam.auto_vertical_tilt_correction = false

-- Clipping
cam.clip_on = true; cam.clip_near = 1.0; cam.clip_far = 10000.0

-- Vignetting
cam.vignetting_enabled = true; cam.vignetting_amount = 1.0
```

### Camera Common Properties (all camera types)
```maxscript
cam.fov = 45.0                 -- horizontal FOV in degrees
cam.curFOV                     -- current FOV respecting fovType
cam.fovType = 1                -- 1=Horizontal, 2=Vertical, 3=Diagonal
cam.orthoProjection = false
cam.type                       -- #free or #target
cam.nearclip = 1.0; cam.farclip = 1000.0
cam.clipManually = true
cam.nearrange = 0.0; cam.farrange = 1000.0   -- env range for atmospherics
cam.targetDistance = 160.0
cam.showCone = true; cam.showHorizon = true

-- FOV conversion utilities
cameraFOV.FOVtoMM 45.0        --> 43.4558
cameraFOV.MMtoFOV 50.0        --> lens mm to degrees
cameraFOV.CurFOVtoFOV cam 54.7  -- diagonal to horizontal
cameraFOV.FOVtoCurFOV cam 45.0  -- horizontal to current type
```

## Light Types

### Standard Lights -- Common Properties
```maxscript
-- Shared by: Omnilight, targetSpot, freeSpot, directionalLight, targetDirectionalLight
lt.enabled = true              -- alias: .on
lt.rgb = color 255 220 180     -- alias: .color
lt.multiplier = 1.5
lt.castShadows = true          -- use .baseObject.castShadows to be safe
lt.contrast = 0.0
lt.affectDiffuse = true; lt.affectSpecular = true
lt.excludeList = #($obj1, $obj2)   -- or .includeList (setting one clears the other)

-- Shadows
lt.shadowGenerator = shadowMap()       -- or raytraceShadow(), Adv__Ray_traced(), Area_Shadows()
lt.shadowColor = color 0 0 0
lt.shadowMultiplier = 1.0

-- Attenuation
lt.useFarAtten = true; lt.farAttenStart = 100.0; lt.farAttenEnd = 500.0
lt.useNearAtten = false
lt.attenDecay = 1              -- 1=None, 2=Inverse, 3=Inverse Square

-- Projector map
lt.projector = true; lt.projectorMap = bitmapTexture filename:@"C:\tex\gobo.jpg"
```

### Omnilight
```maxscript
om = omniLight pos:[0,0,300] rgb:(color 255 255 220) multiplier:1.0
```

### Target Spot / Free Spot
```maxscript
sp = targetSpot pos:[0,-200,300] target:(targetObject pos:[0,0,0])
sp.hotspot = 30.0; sp.falloff = 45.0
sp.coneShape = 1               -- 1=Circle, 2=Rectangle
sp.aspect = 1.0; sp.overShoot = false

fs = freeSpot pos:[0,0,300]
fs.hotspot = 40.0; fs.falloff = 50.0
```

### Directional / Target Directional
```maxscript
dl = directionalLight pos:[0,0,500]
dl.hotspot = 100.0; dl.falloff = 120.0   -- measured in scene units, not degrees
dl.coneShape = 1

tdl = targetDirectionalLight pos:[0,-200,300] target:(targetObject pos:[0,0,0])
```

### Photometric Lights (3ds Max 2009+)
All photometric lights share one class; switch type via `.type`:
```maxscript
lt = Free_Light pos:[0,0,300]  -- or Target_Point, Free_Point, etc.
lt.type = #Free_Point          -- #Free_Point #Free_Line #Free_Rectangle #Free_Disc
                               -- #Free_Sphere #Free_Cylinder #Target_Point #Target_Line
                               -- #Target_Rectangle #Target_Disc #Target_Sphere #Target_Cylinder

lt.distribution = 0            -- 0=Uniform Spherical, 1=Spotlight, 2=Hemispherical, 3=Web File
lt.intensityType = 1           -- 0=lm, 1=cd, 2=lx at
lt.intensity = 1500.0          -- always in candelas internally
lt.useKelvin = true; lt.kelvin = 5500.0
lt.color = color 255 255 255   -- RGB alternative
lt.castShadows = true
lt.shadowGenerator = shadowMap()

-- Spotlight distribution
lt.hotSpot = 30.0; lt.falloff = 60.0

-- Area shape
lt.light_length = 120.0; lt.light_width = 60.0; lt.light_radius = 13.0

-- Photometric web
lt.distribution = 3; lt.webFile = @"C:\ies\fixture.ies"

-- Dimming
lt.useMultiplier = true; lt.Multiplier = 75.0  -- percent
```

## Render Elements

```maxscript
re = maxOps.GetCurRenderElementMgr()  -- get current render element manager
re.RemoveAllRenderElements()

-- Add elements
re.AddRenderElement (diffuse elementname:"Diffuse")
re.AddRenderElement (specular elementname:"Specular")
re.AddRenderElement (reflection elementname:"Reflection")
re.AddRenderElement (z_depth elementname:"ZDepth")
re.AddRenderElement (alpha elementname:"Alpha")

-- Query
n = re.NumRenderElements()
el = re.GetRenderElement 0     -- 0-based index!
el.elementname

-- Output filenames
re.SetRenderElementFilename 0 @"C:\out\diffuse.exr"
re.GetRenderElementFilename 0

-- Enable/disable
re.SetElementsActive true
re.SetDisplayElements true
```

## Viewports

```maxscript
-- Active viewport
viewport.activeViewport = 4            -- set active to 4th viewport
viewport.numViews                      -- number of 3D viewports

-- Type
viewport.setType #view_persp_user      -- #view_top #view_front #view_left #view_camera etc.
viewport.getType()

-- Camera
viewport.setCamera $cam01              -- set viewport to camera view
viewport.getCamera()                   -- returns camera node or undefined
getActiveCamera()                      -- same as above

-- Layout
viewport.setLayout #layout_4           -- #layout_1 #layout_2v #layout_2h #layout_4 etc.
viewport.getLayout()

-- Transform
viewport.setTM (matrix3 [1,0,0] [0,0,-1] [0,1,0] [0,-500,200])
viewport.getTM()
viewport.SetFOV 50.0
viewport.GetFOV()
viewport.pan 10 0; viewport.zoom 2.0; viewport.rotate (quat 5 [0,0,1])

-- Render level
viewport.SetRenderLevel #smoothhighlights  -- #wireFrame #smooth #flat #hiddenline #Box
viewport.SetShowEdgeFaces true

-- Region
viewport.setRegionRect 1 (box2 100 100 500 400)

-- Capture viewport as bitmap
bm = viewport.getViewportDib()
bm = viewport.getViewportDib index:2 captureAlpha:true

-- Viewport size
getViewSize()                          -- returns point2 [width, height]

-- Screen to world
mapScreenToWorldRay [400, 300]         -- returns Ray
mapScreenToView [400,300] -100.0       -- returns point3 in view coords

-- Background
viewport.DispBkgImage = true
displaySafeFrames = true

-- Redraw
completeredraw()
forceCompleteRedraw()
```

## Bitmap Operations

```maxscript
-- Create
bm = bitmap 1920 1080 color:black
bm = bitmap 512 512 color:white hdr:true   -- 32-bit float per channel

-- Open existing
bm = openBitmap @"C:\textures\photo.jpg"

-- Save
bm.filename = @"C:\output\result.png"
save bm
-- Save JPEG with quality
jpeg.setQuality 90
bm.filename = @"C:\output\result.jpg"
save bm
-- Save with frame number (image sequence)
save bm frame:42

-- Copy
bm2 = copy bm

-- Properties
bm.width; bm.height; bm.gamma; bm.aspect; bm.hasAlpha; bm.numframes

-- Pixel access
pixels = getPixels bm [0, 50] 100     -- 100 pixels from row 50
setPixels bm [0, 50] #((color 255 0 0), (color 0 255 0))

-- Display / close
display bm caption:"My Render"
unDisplay bm
close bm       -- closes file handle and frees memory
free bm        -- frees memory immediately

-- G-buffer channels
getChannel bm [x,y] #zDepth           -- returns array of floats
getChannelAsMask bm #zDepth           -- returns grayscale bitmap

-- Last rendered image
bm = getLastRenderedImage()
bm = getLastRenderedImage copy:false   -- shares renderer bitmap

-- Viewport capture to file
dib = gw.getViewportDib()
dib.filename = @"C:\out\viewport.png"
save dib
```

## Environment and Exposure

```maxscript
-- Exposure control
SceneExposureControl.exposureControl = Physical_Camera_Exposure_Control()
SceneExposureControl.exposureControl = Logarithmic_Exposure_Control()
SceneExposureControl.exposureControl = Linear_Exposure_Control()
SceneExposureControl.exposureControl = undefined   -- remove

-- Environment map
environmentMap = bitmapTexture filename:@"C:\hdri\env.hdr"

-- Background color
backgroundColor = color 0 0 0
useEnvironmentMap = true
```

## Batch Rendering

```maxscript
mgr = batchRenderMgr

-- Create views from cameras
for cam in cameras where superclassof cam == camera do (
    v = mgr.CreateView cam
    v.name = cam.name
    v.enabled = true
    v.outputFilename = @"C:\batch\" + cam.name + ".png"
    v.width = 1920; v.height = 1080
    v.startFrame = 0f; v.endFrame = 100f
    v.pixelAspect = 1.0
)

-- Query views
mgr.numViews
v = mgr.GetView 1              -- 1-based index
v.camera; v.name; v.outputFilename

-- Modify
v.overridePreset = true
v.presetFile = @"C:\presets\hq.rps"
v.sceneStateName = "DayLight"

-- Delete
mgr.DeleteView 1

-- Execute
mgr.netRender = false
mgr.Render()
```

## Common Patterns

### Render All Cameras to Files
```maxscript
for cam in cameras where superclassof cam == camera do (
    render camera:cam outputfile:(@"C:\out\" + cam.name + ".exr") \
        outputwidth:1920 outputheight:1080 vfb:off quiet:true
)
```

### Turntable Render
```maxscript
cam = freeCamera pos:[500,0,100] fov:45
cam.target = targetObject pos:[0,0,50]   -- won't work on free; use targetCamera
cam = targetCamera pos:[500,0,100] target:(targetObject pos:[0,0,50])
bm = bitmap 1920 1080
for i = 0 to 359 by 5 do (
    cam.pos = [500 * cos i, 500 * sin i, 100]
    render camera:cam to:bm outputfile:(@"C:\turn\frame_" + (formattedPrint (i/5) format:"04d") + ".png")
)
close bm
```

### Create Physical Camera from Scratch
```maxscript
cam = Physical targeted:true pos:[0,-800,200]
cam.target = targetObject pos:[0,0,100]
cam.focal_length_mm = 35.0
cam.f_number = 5.6
cam.use_dof = true
cam.exposure_gain_type = 1
cam.exposure_value = 10.0
SceneExposureControl.exposureControl = Physical_Camera_Exposure_Control()
viewport.setCamera cam
render camera:cam outputwidth:1920 outputheight:1080
```

### Render Region to Bitmap
```maxscript
bm = render renderType:#regionCrop region:#(100, 100, 800, 600) \
    outputwidth:700 outputheight:500
display bm
```
