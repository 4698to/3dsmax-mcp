# MAXScript: UI and Rollouts Reference

## Complete Working Example

```maxscript
rollout myTool "Object Toolkit" (
    local pickedObj = undefined
    fn getSelNames = for o in selection collect o.name

    group "Object Selection" (
        pickbutton btnPick "Pick Object" width:140 autoDisplay:true
        listbox lbxObjects "Scene Objects:" items:(getSelNames()) height:5
    )
    group "Transform" (
        spinner spnX "X:" range:[-9999,9999,0] type:#worldunits fieldWidth:60 across:3
        spinner spnY "Y:" range:[-9999,9999,0] type:#worldunits fieldWidth:60
        spinner spnZ "Z:" range:[-9999,9999,0] type:#worldunits fieldWidth:60
        slider sldScale "Scale %:" range:[1,500,100] type:#integer ticks:5
    )
    group "Properties" (
        colorpicker cpWire "Wire Color:" color:red
        checkbox chkFreeze "Freeze" checked:false across:2
        checkbox chkHide "Hide" checked:false
        dropdownlist ddlDisplay "Display:" items:#("Smooth","Wireframe","Box")
        edittext edtName "Name:" fieldWidth:120
    )
    button btnApply "Apply" width:140 height:24
    progressbar pbProgress color:green

    on myTool open do (
        lbxObjects.items = for o in objects collect o.name
    )
    on btnPick picked obj do (
        pickedObj = obj
        spnX.value = obj.pos.x; spnY.value = obj.pos.y; spnZ.value = obj.pos.z
        cpWire.color = obj.wirecolor
        edtName.text = obj.name
    )
    on spnX changed val do if pickedObj != undefined do pickedObj.pos.x = val
    on spnY changed val do if pickedObj != undefined do pickedObj.pos.y = val
    on spnZ changed val do if pickedObj != undefined do pickedObj.pos.z = val
    on cpWire changed col do if pickedObj != undefined do pickedObj.wirecolor = col
    on edtName entered txt do if pickedObj != undefined do pickedObj.name = txt
    on btnApply pressed do messagebox "Applied!"
    on myTool close do format "Tool closed.\n"
)
createDialog myTool width:220
```

---

## Rollout Definition

```maxscript
rollout <name> <title_string> [width:<int>] [height:<int>] [autoLayoutOnResize:<bool>]
(
    <local_variable_decl>  -- local myVar = 0
    <function_decl>        -- fn myFunc x = x * 2
    <ui_controls>          -- button, spinner, etc.
    <event_handlers>       -- on btnX pressed do ...
)
```

Rollouts are containers for UI controls. They cannot run alone -- they must be displayed via `createDialog`, `newRolloutFloater`, a `utility`, or a scripted plug-in.

### Rollout Properties (runtime)

| Property | Type | Access | Description |
|----------|------|--------|-------------|
| `.name` | String | R | Internal name |
| `.title` | String | RW | Title bar text |
| `.open` | Boolean | RW | Rolled-up state |
| `.width` / `.height` | Integer | RW | Pixel dimensions |
| `.controls` | Array | R | All UI controls |
| `.hwnd` | Integer | R | Window handle (0 if closed) |
| `.isDisplayed` | Boolean | R | True if currently shown |
| `.visible` | Boolean | RW | Show/hide dialog (2021+) |
| `.inDialog` | Boolean | R | True if in a createDialog |
| `.scrollPos` | Integer | RW | Panel scroll position |

---

## Displaying Rollouts

### createDialog -- Standalone floating dialog

```maxscript
createDialog <rollout> [width:<int>] [height:<int>] [pos:<Point2>]
    [bgcolor:<color>] [fgcolor:<color>] [menu:<RCMenu>]
    [style:<array>] [modal:<bool>] [lockHeight:<bool>] [lockWidth:<bool>]
    [escapeEnable:<bool>] [parent:<HWND>]
    [autoLayoutOnResize:<bool>]

DestroyDialog <rollout>          -- close it
GetDialogPos <rollout>           -- returns Point2
GetDialogSize <rollout>          -- returns Point2
SetDialogPos <rollout> <Point2>
SetDialogSize <rollout> <Point2>
```

Style flags: `#style_titlebar`, `#style_border`, `#style_sysmenu`, `#style_resizing`, `#style_toolwindow`, `#style_minimizebox`, `#style_maximizebox`, `#style_sunkenedge`.

Default style: `#(#style_titlebar, #style_border, #style_sysmenu)`.

### newRolloutFloater -- Container for multiple rollouts

```maxscript
theFloater = newRolloutFloater <title> <width> <height> [<x> <y>]
    [lockHeight:<bool>] [lockWidth:<bool>] [scrollBar:<#on|#off|#asNeeded>]
addRollout <rollout> theFloater [rolledUp:<bool>] [border:<bool>]
removeRollout <rollout> theFloater
closeRolloutFloater theFloater
```

Floater properties: `.title`, `.size` (Point2 RW), `.pos` (Point2 RW), `.open` (Bool R), `.visible` (Bool RW), `.rollouts` (Array R), `.hwnd`.

### utility -- Utilities panel

```maxscript
utility <name> <description_string> [rolledUp:<bool>] [silentErrors:<bool>]
(
    -- same body as rollout: controls + handlers
)
-- Opens via MAXScript > Utilities list. Has auto Close button.
openUtility <utility>
closeUtility <utility>
```

---

## Rollout Event Handlers

```maxscript
on <rollout> open do <expr>                   -- rollout opened
on <rollout> close do <expr>                  -- rollout closing
on <rollout> oktoclose do <expr>              -- return true/false to allow close
on <rollout> resized <Point2> do <expr>       -- dialog/floater resized
on <rollout> moved <Point2> do <expr>         -- dialog/floater moved
on <rollout> rolledUp <bool> do <expr>        -- rolled up/down (true=open)
on <rollout> help do <expr>                   -- F1 pressed

-- Mouse events (dialog only, Point2 = position in client area):
on <rollout> lbuttondown <pt> do ...
on <rollout> lbuttonup <pt> do ...
on <rollout> lbuttondblclk <pt> do ...
on <rollout> mbuttondown <pt> do ...
on <rollout> rbuttondown <pt> do ...
on <rollout> mousemove <pt> do ...
```

---

## Common Layout Parameters (all controls)

```maxscript
align:#left | #center | #right     -- horizontal alignment (default varies by type)
offset:<Point2>                     -- [x,y] pixel offset from auto-placed position
width:<int>                         -- force width in pixels
height:<int>                        -- force height in pixels
across:<int>                        -- lay out N items horizontally
pos:<Point2>                        -- absolute [x,y] position in rollout
```

### Common Properties (all controls)

```maxscript
<ctrl>.enabled  Boolean             -- enable/disable (grayed out)
<ctrl>.visible  Boolean             -- show/hide
<ctrl>.caption  String              -- label text
<ctrl>.text     String              -- alias for .caption (except edittext/combobox)
<ctrl>.pos      Point2              -- position in rollout
<ctrl>.hwnd     Integer             -- window handle array (read-only)
```

### Grouping controls

```maxscript
group "Label" (
    button btn1 "A" across:2
    button btn2 "B"
    on btn1 pressed do ...     -- handlers allowed inside group
)
```

---

## UI Controls Quick Reference

### button

```maxscript
button <name> [<caption>] [toolTip:<str>] [border:<bool>] [images:<array>]
    [iconName:<str> iconSize:<Point2>]
-- Events: on <name> pressed do ...; on <name> rightclick do ...
-- Properties: .tooltip, .images (write-only), .width, .height
```

### spinner

```maxscript
spinner <name> [<caption>] [range:[min,max,val]] [type:#float|#integer|#worldunits]
    [scale:<float>] [fieldWidth:<int>] [controller:<controller>]
-- Events:
on <name> changed val do ...              -- val = new value
on <name> changed val inSpin do ...       -- inSpin: true=mouse, false=keyboard
on <name> entered do ...                  -- focus lost / Enter pressed
on <name> buttondown do ...               -- mouse click start
on <name> buttonup do ...                 -- mouse release
-- Properties: .value (Float|Int), .range (Point3), .indeterminate, .enabled
```

### checkbox / checkbutton

```maxscript
checkbox <name> [<caption>] [checked:<bool>] [triState:<0|1|2>]
-- Event: on <name> changed state do ...   -- state = true/false
-- Properties: .checked, .state, .triState

checkbutton <name> [<caption>] [checked:<bool>] [highlightColor:<color>]
-- Event: on <name> changed state do ...   -- state = true/false
-- Properties: .checked, .state
```

### edittext

```maxscript
edittext <name> [<caption>] [text:<str>] [fieldWidth:<int>] [height:<int>]
    [bold:<bool>] [labelOnTop:<bool>] [readOnly:<bool>] [multiLine:<bool>]
-- Events:
on <name> changed txt do ...      -- every keystroke
on <name> entered txt do ...      -- Enter/Tab pressed (single-line only!)
-- Properties: .text, .bold, .readOnly
-- WARNING: height >= 17px or multiLine:true = multi-line mode, "entered" NOT called
```

### dropdownlist

```maxscript
dropdownlist <name> [<caption>] [items:<#("a","b")>] [selection:<int>] [height:<int>]
-- Event: on <name> selected idx do ...
-- Properties: .items (Array), .selection (Int 1-based), .selected (String)
```

### listbox

```maxscript
listbox <name> [<caption>] [items:<array>] [selection:<int>] [height:<int>]
    [readOnly:<bool>]
-- Events:
on <name> selected idx do ...
on <name> doubleClicked idx do ...
on <name> rightClick idx do ...
-- Properties: .items, .selection, .selected
```

### multilistbox

```maxscript
multilistbox <name> [<caption>] [items:<array>] [selection:<bitarray>] [height:<int>]
-- Events: on <name> selected idx do ...; on <name> selectionEnd do ...
-- Properties: .items, .selection (BitArray -- set via #(1,3), #{1,3}, or int)
```

### radiobuttons

```maxscript
radiobuttons <name> [<caption>] labels:<#("A","B","C")>
    [default:<int>] [columns:<int>]
-- Event: on <name> changed state do ...   -- state = 1-based index
-- Properties: .state (Int, 0=none selected)
```

### slider

```maxscript
slider <name> [<caption>] [range:[min,max,val]] [type:#float|#integer]
    [orient:#horizontal|#vertical] [ticks:<int>]
-- Events: on <name> changed val do ...; on <name> buttondown/buttonup do ...
-- Properties: .value, .range, .ticks
```

### colorpicker

```maxscript
colorpicker <name> [<caption>] [color:<color>] [modal:<bool>] [alpha:<bool>]
    [title:<str>] [fieldWidth:<int>] [height:<int>]
-- Event: on <name> changed newColor do ...
-- Property: .color
```

### pickbutton

```maxscript
pickbutton <name> [<caption>] [message:<str>] [filter:<fn>] [autoDisplay:<bool>]
-- Event: on <name> picked obj do ...
-- Property: .object (Node or undefined)
-- filter: fn myFilter obj = (classof obj == Box)
```

### label

```maxscript
label <name> [<string>] [style_sunkenedge:<bool>]
-- No events. Properties: .text/.caption, .width, .height
```

### progressbar

```maxscript
progressbar <name> [value:<0-100>] [color:<color>] [orient:#horizontal|#vertical]
-- Event: on <name> clicked pct do ...
-- Properties: .value (Int 0-100), .color
-- Tip: .value = 100. * i / total   (multiply first to avoid int division)
```

### timer (invisible)

```maxscript
timer <name> [interval:<ms>] [active:<bool>]
-- Event: on <name> tick do ...
-- Properties: .interval (Int ms), .active (Bool), .ticks (Int counter)
```

### combobox

```maxscript
combobox <name> [<caption>] [items:<array>] [selection:<int>] [height:<int>]
-- Events: selected, doubleClicked, entered (focus lost), changed (per keystroke)
-- Properties: .items, .selection, .selected, .text (edit box text)
```

### SubRollout

```maxscript
subRollout <name> [height:<int>] [width:<int>]
-- Then:
addSubRollout <parentRollout>.<subRolloutName> <childRollout>
removeSubRollout <parentRollout>.<subRolloutName> <childRollout>
```

### groupBox (independent positioning)

```maxscript
groupBox <name> [<caption>] [pos:<Point2>] [width:<int>] [height:<int>]
-- Unlike group{}, groupBox is freely positionable, does not wrap controls.
```

---

## MacroScripts

MacroScripts are toolbar/menu/keyboard-assignable actions.

```maxscript
macroScript MyTool
    category:"MyCategory"
    buttonText:"My Tool"
    toolTip:"Does something cool"
    iconName:"MyIcons/my_icon"
    autoUndoEnabled:true
(
    -- Simple form: single expression body
    -- OR event handler form:
    on isEnabled do (selection.count > 0)
    on isChecked do (myToolRollout.isDisplayed)
    on execute do (
        createDialog myToolRollout
    )
    on closeDialogs do (
        destroyDialog myToolRollout
    )
)
```

Handler form events: `on execute do`, `on isEnabled do`, `on isChecked do`, `on isVisible do`, `on isIndeterminate do`, `on closeDialogs do`, `on altExecute type do`.

`closeDialogs` toggles off when `isChecked` returns true. Requires `isChecked` to be defined.

MacroScript locals are heap-based (persist between executions). Access via `macros.run "Category" "Name"`.

---

## Right-Click Menus (RCMenu)

```maxscript
rcmenu myMenu (
    fn needsSel = selection.count > 0
    menuItem mi_delete "Delete Selected" filter:needsSel enabled:true
    separator sep1
    subMenu "Transform" (
        menuItem mi_move "Move to Origin"
        menuItem mi_reset "Reset XForm"
    )
    on mi_delete picked do delete selection
    on mi_move picked do selection.pos = [0,0,0]
    on mi_reset picked do (
        for o in selection do (addModifier o (ResetXForm()); collapseStack o)
    )
)
popUpMenu myMenu                             -- show at cursor
popUpMenu myMenu pos:[100,100]               -- show at screen position
-- Also: createDialog myRollout menu:myMenu  -- as dialog menu bar
```

---

## Custom Attributes

Attach parameters + rollout UI to any object, modifier, or material.

```maxscript
myCA = attributes gameData (
    parameters main rollout:params (
        hitPoints type:#float ui:spnHP default:100
        team      type:#integer ui:ddTeam default:1
        tag       type:#string default:"none"
    )
    rollout params "Game Data" (
        spinner spnHP "HP:" type:#float range:[0,9999,100]
        dropdownlist ddTeam "Team:" items:#("Red","Blue","Green")
        on ddTeam selected i do team = i
    )
)

-- Add to objects:
custAttributes.add $myObj myCA                 -- adds to base object
custAttributes.add $myObj myCA #unique         -- private copy of definition
custAttributes.add $myObj myCA baseObject:false -- add to node, not base object

-- Access:
$myObj.hitPoints        -- direct
$myObj.gameData.team    -- via attribute block name

-- Remove:
custAttributes.delete $myObj 1                 -- by index
custAttributes.delete $myObj myCA              -- by definition

-- Query:
custAttributes.count $myObj
custAttributes.get $myObj <index>
custAttributes.getDef $myObj <index>
```

Parameter types: `#float`, `#integer`, `#string`, `#boolean`, `#color`, `#point3`, `#node`, `#matrix3`, `#floatTab`, etc. Use `ui:<controlName>` to auto-wire a parameter to a rollout control.

---

## Dynamic Rollout Creation (RolloutCreator)

```maxscript
rci = rolloutCreator "myRollout" "My Dynamic Rollout"
rci.begin()
rci.addLocal "counter" init:0
rci.addControl #button #btnGo "Go!" paramStr:"width:140"
rci.addControl #spinner #spnVal "Value:" paramStr:"range:[0,100,50] type:#integer"
rci.addHandler #btnGo #pressed filter:on codeStr:"MessageBox @Button pressed!@"
rci.addHandler #spnVal #changed paramStr:"val" codeStr:"format @Value: %\\n@ val"
createDialog (rci.end())
```

Use `@` instead of inner `"` for strings inside `codeStr`. Pass `filter:on` when `codeStr` contains `@`-delimited strings.

---

## Key Patterns

**Prevent close:** `on myRollout oktoclose do myCheckbutton.state`

**Access controls from outside:** `myRollout.btnApply.enabled = false`

**Invoke handler from code:** `myRollout.btnApply.pressed()`

**Refresh list:** `lbx.items = for o in objects collect o.name`

**across layout:** First control gets `across:N`, next N-1 controls fill the row.

**Dialog with menu bar:** `createDialog myRollout menu:myRCMenu`

**Parent dialog to another:** `createDialog child pos:[200,200] parent:parentRollout.hwnd`

**Resizable dialog:** `createDialog myRollout style:#(#style_titlebar, #style_border, #style_sysmenu, #style_resizing)`
