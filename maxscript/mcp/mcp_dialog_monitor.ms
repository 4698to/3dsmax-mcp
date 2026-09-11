/*
	MCP_DialogMonitor — 发现插件对话框、截图、屏幕点击

	Usage (3ds Max Listener):
		fileIn @"...\dialog_monitor\mcp_dialog_monitor.ms"
		MCP_DialogMonitor.findDialog()
		MCP_DialogMonitor.captureDialogMeta()
		MCP_DialogMonitor.clickAtScreen 100 200
*/

global MCP_DialogMonitor
global MCP_Server_DialogMonitor
global MCP_DialogMouseHelperLoaded

struct DialogMonitorStruct
(
	titlePattern = "自动蒙皮4.*",
	vendorPattern = "*天晴数码",
	requireVendor = true,

	lastHwnd = 0,
	lastTitle = "",
	lastCapturePath = "",
	lastMatches = #(),
	lastImageWidth = 0,
	lastImageHeight = 0,
	lastScreenRect = #(0, 0, 0, 0), -- #(x, y, w, h)

	fn normalizeTitle raw =
	(
		if raw == undefined then "" else (raw as string)
	),

	-- windows.getChildrenHWND returns IntegerPtr on modern Max; many
	-- windows.* APIs crash or hard-fail when given IntegerPtr directly.
	fn asHwndInt hwnd =
	(
		if hwnd == undefined or hwnd == 0 then 0
		else
		(
			local n = 0
			try (n = hwnd as integer) catch (n = 0)
			n
		)
	),

	fn escapeJson str =
	(
		local s = if str == undefined then "" else (str as string)
		s = substituteString s "\\" "\\\\"
		s = substituteString s "\"" "\\\""
		s = substituteString s "\n" "\\n"
		s = substituteString s "\r" "\\r"
		s = substituteString s "\t" "\\t"
		s
	),

	fn matchTitle title titlePat: vendorPat: requireVendor: =
	(
		if titlePat == unsupplied do titlePat = this.titlePattern
		if vendorPat == unsupplied do vendorPat = this.vendorPattern
		if requireVendor == unsupplied do requireVendor = this.requireVendor
		local t = this.normalizeTitle title
		if t == "" then
			false
		else if not (matchPattern t pattern:titlePat) then
			false
		else if requireVendor and not (matchPattern t pattern:vendorPat) then
			false
		else
			true
	),

	-- Physical-pixel window rect; removeUIScaling:false matches captureScreenPixels:true
	fn getWindowGeom hwnd =
	(
		local h = this.asHwndInt hwnd
		if h == 0 do return undefined
		local pos = undefined
		try (pos = windows.getWindowPos h removeUIScaling:false) catch
		(
			try (pos = windows.getWindowPos h) catch (return undefined)
		)
		if pos == undefined do return undefined
		#(pos.x, pos.y, pos.w, pos.h)
	),

	fn findWindows titlePat: vendorPat: requireVendor: visibleOnly:true parentType:"desktop" =
	(
		if titlePat == unsupplied do titlePat = this.titlePattern
		if vendorPat == unsupplied do vendorPat = this.vendorPattern
		if requireVendor == unsupplied do requireVendor = this.requireVendor

		local matches = #()
		local rootHwnd = if parentType == "desktop" then windows.getDesktopHWND() else windows.getMAXHWND()
		local children = windows.getChildrenHWND rootHwnd
		if children == undefined do children = #()

		for child in children where child != undefined and child.count >= 5 do
		(
			local hwnd = this.asHwndInt child[1]
			local title = this.normalizeTitle child[5]
			if (this.matchTitle title titlePat:titlePat vendorPat:vendorPat requireVendor:requireVendor) do
			(
				local accept = true
				if visibleOnly do
				(
					try (accept = windows.isWindowVisible hwnd) catch (accept = true)
				)
				if accept do
				(
					child[1] = hwnd
					append matches child
				)
			)
		)

		this.lastMatches = matches
		if matches.count > 0 then
		(
			this.lastHwnd = matches[1][1]
			this.lastTitle = this.normalizeTitle matches[1][5]
		)
		else
		(
			this.lastHwnd = 0
			this.lastTitle = ""
		)
		matches
	),

	fn findDialog visibleOnly:true parentType:"desktop" =
	(
		this.findWindows \
			titlePat:this.titlePattern \
			vendorPat:this.vendorPattern \
			requireVendor:this.requireVendor \
			visibleOnly:visibleOnly \
			parentType:parentType
	),

	-- Back-compat alias for older scripts
	fn findGoSkin visibleOnly:true =
	(
		this.findWindows \
			titlePat:"自动蒙皮4.*" \
			vendorPat:"*天晴数码" \
			requireVendor:true \
			visibleOnly:visibleOnly \
			parentType:"desktop"
	),

	fn describeMatches matches: =
	(
		if matches == unsupplied do matches = this.lastMatches
		if matches == undefined do matches = #()
		local lines = #()
		for i = 1 to matches.count do
		(
			local row = matches[i]
			local hwnd = row[1]
			local title = this.normalizeTitle row[5]
			local className = if row.count >= 4 then (row[4] as string) else ""
			local posStr = ""
			local geom = this.getWindowGeom hwnd
			if geom != undefined do
			(
				posStr = " pos=(" + (geom[1] as string) + "," + (geom[2] as string) + \
					" " + (geom[3] as string) + "x" + (geom[4] as string) + ")"
			)
			append lines (
				"[" + (i as string) + "] hwnd=" + (hwnd as string) + \
				" class=\"" + className + "\" title=\"" + title + "\"" + posStr
			)
		)
		if lines.count == 0 then "(no matches)" else (lines as string)
	),

	fn defaultCapturePath =
	(
		local dir = (getDir #temp)
		if dir == undefined or dir == "" do dir = sysInfo.tempdir
		dir = substituteString dir "\\" "/"
		if dir[dir.count] != "/" do dir += "/"
		dir + "dialog_monitor_" + (timeStamp() as string) + ".png"
	),

	-- Returns #(path, imageW, imageH) or "ERROR: ..."
	fn captureHwndEx hwnd outfile: displayResult:false =
	(
		local h = this.asHwndInt hwnd
		if h == 0 do return "ERROR: invalid hwnd"
		local okWin = true
		try (okWin = windows.isWindow h) catch (okWin = true)
		if not okWin do return ("ERROR: hwnd is not a window: " + (h as string))

		local isVis = true
		try (isVis = windows.isWindowVisible h) catch ()
		if not isVis do return "ERROR: window is not visible"

		if outfile == unsupplied or outfile == undefined or outfile == "" do
			outfile = this.defaultCapturePath()
		outfile = substituteString (outfile as string) "\\" "/"

		local parentDir = getFilenamePath outfile
		if parentDir != "" do makeDir parentDir all:true

		local bmp = undefined
		try
		(
			-- Named kwargs (captureScreenPixels etc.) can yield solid-black bitmaps
			-- on older Max builds; keep the plain overload.
			bmp = windows.snapshot h
		)
		catch
		(
			return ("ERROR: windows.snapshot failed: " + (getCurrentException() as string))
		)
		if bmp == undefined do return "ERROR: snapshot returned undefined"

		local imageW = bmp.width
		local imageH = bmp.height

		local saved = false
		try
		(
			bmp.filename = outfile
			saved = save bmp
		)
		catch
		(
			try (close bmp) catch ()
			return ("ERROR: save failed: " + (getCurrentException() as string))
		)
		if displayResult do try (display bmp caption:"DialogMonitor Capture") catch ()
		try (close bmp) catch ()

		if not saved do return ("ERROR: save returned false: " + outfile)

		this.lastHwnd = h
		this.lastCapturePath = outfile
		this.lastImageWidth = imageW
		this.lastImageHeight = imageH
		#(outfile, imageW, imageH)
	),

	fn captureHwnd hwnd outfile: displayResult:false =
	(
		local result = this.captureHwndEx hwnd outfile:outfile displayResult:displayResult
		if classOf result == String then result else result[1]
	),

	fn captureDialog outfile: displayResult:false visibleOnly:true parentType:"desktop" =
	(
		local matches = this.findDialog visibleOnly:visibleOnly parentType:parentType
		if matches.count == 0 do
			return ("ERROR: dialog not found (title=" + this.titlePattern + " vendor=" + this.vendorPattern + ")")
		local hwnd = this.asHwndInt matches[1][1]
		this.lastTitle = this.normalizeTitle matches[1][5]
		format "MCP_DialogMonitor: capturing % hwnd=%\n" this.lastTitle hwnd
		this.captureHwnd hwnd outfile:outfile displayResult:displayResult
	),

	fn captureGoSkin outfile: displayResult:false visibleOnly:true =
	(
		local matches = this.findGoSkin visibleOnly:visibleOnly
		if matches.count == 0 do return "ERROR: dialog not found (自动蒙皮4.* / 天晴数码)"
		local hwnd = this.asHwndInt matches[1][1]
		this.lastTitle = this.normalizeTitle matches[1][5]
		this.captureHwnd hwnd outfile:outfile displayResult:displayResult
	),

	fn discoverAndCapture outfile: displayResult:false =
	(
		this.captureDialog outfile:outfile displayResult:displayResult
	),

	-- Find + capture + geometry JSON for Python OCR mapping
	fn captureDialogMeta outfile: displayResult:false visibleOnly:true parentType:"desktop" =
	(
		local matches = this.findDialog visibleOnly:visibleOnly parentType:parentType
		if matches.count == 0 do
		(
			return ("{\"ok\":false,\"error\":\"dialog not found\",\"title_pattern\":\"" + \
				(this.escapeJson this.titlePattern) + "\",\"vendor_pattern\":\"" + \
				(this.escapeJson this.vendorPattern) + "\"}")
		)

		local hwnd = this.asHwndInt matches[1][1]
		local title = this.normalizeTitle matches[1][5]
		this.lastTitle = title
		this.lastHwnd = hwnd

		local geom = this.getWindowGeom hwnd
		if geom == undefined do
			return "{\"ok\":false,\"error\":\"could not read window geometry\"}"

		local iconic = false
		try
		(
			local place = windows.getWindowPlacement hwnd removeUIScaling:false
			if place != undefined and place.count >= 1 do
			(
				local cmd = place[1] as string
				if cmd == "#showMinimized" or cmd == "#minimize" or cmd == "#forceMinimize" do iconic = true
			)
		)
		catch ()
		if iconic do return "{\"ok\":false,\"error\":\"window is minimized\"}"

		local cap = this.captureHwndEx hwnd outfile:outfile displayResult:displayResult
		if classOf cap == String do
			return ("{\"ok\":false,\"error\":\"" + (this.escapeJson cap) + "\"}")

		local path = cap[1]
		local imageW = cap[2]
		local imageH = cap[3]
		this.lastScreenRect = geom

		"{" + \
			"\"ok\":true," + \
			"\"hwnd\":" + (hwnd as string) + "," + \
			"\"title\":\"" + (this.escapeJson title) + "\"," + \
			"\"file\":\"" + (this.escapeJson path) + "\"," + \
			"\"screen_rect\":{\"x\":" + (geom[1] as string) + ",\"y\":" + (geom[2] as string) + \
				",\"w\":" + (geom[3] as string) + ",\"h\":" + (geom[4] as string) + "}," + \
			"\"image_width\":" + (imageW as string) + "," + \
			"\"image_height\":" + (imageH as string) + "," + \
			"\"match_count\":" + (matches.count as string) + "," + \
			"\"capture_method\":\"hwnd_snapshot\"" + \
		"}"
	),

	-- Capture an arbitrary screen rectangle (physical pixels) via .NET
	fn captureScreenRect x y w h outfile: =
	(
		local ix = x as integer
		local iy = y as integer
		local iw = w as integer
		local ih = h as integer
		if iw < 1 or ih < 1 do return "ERROR: invalid rect size"
		if outfile == unsupplied or outfile == undefined or outfile == "" do
			outfile = this.defaultCapturePath()
		outfile = substituteString (outfile as string) "\\" "/"
		local parentDir = getFilenamePath outfile
		if parentDir != "" do makeDir parentDir all:true

		try
		(
			local bmp = dotNetObject "System.Drawing.Bitmap" iw ih
			local gfx = (dotNetClass "System.Drawing.Graphics").FromImage bmp
			local size = dotNetObject "System.Drawing.Size" iw ih
			gfx.CopyFromScreen ix iy 0 0 size
			gfx.Dispose()
			bmp.Save outfile
			bmp.Dispose()
			this.lastCapturePath = outfile
			this.lastImageWidth = iw
			this.lastImageHeight = ih
			this.lastScreenRect = #(ix, iy, iw, ih)
			#(outfile, iw, ih)
		)
		catch
		(
			("ERROR: captureScreenRect failed: " + (getCurrentException() as string))
		)
	),

	fn metaJson hwnd title path geom imageW imageH extra: =
	(
		local extraStr = if extra == unsupplied or extra == undefined or extra == "" then "" else ("," + extra)
		"{" + \
			"\"ok\":true," + \
			"\"hwnd\":" + (hwnd as string) + "," + \
			"\"title\":\"" + (this.escapeJson title) + "\"," + \
			"\"file\":\"" + (this.escapeJson path) + "\"," + \
			"\"screen_rect\":{\"x\":" + (geom[1] as string) + ",\"y\":" + (geom[2] as string) + \
				",\"w\":" + (geom[3] as string) + ",\"h\":" + (geom[4] as string) + "}," + \
			"\"image_width\":" + (imageW as string) + "," + \
			"\"image_height\":" + (imageH as string) + \
			extraStr + \
		"}"
	),

	-- Capture 3ds Max main window top strip (menu bar area) for OCR
	fn captureMaxMenuBarMeta height:120 outfile: =
	(
		local hwnd = this.asHwndInt (windows.getMAXHWND())
		if hwnd == 0 do return "{\"ok\":false,\"error\":\"no Max HWND\"}"
		local geom = this.getWindowGeom hwnd
		if geom == undefined do return "{\"ok\":false,\"error\":\"could not read Max geometry\"}"
		local barH = height as integer
		if barH < 40 do barH = 40
		if barH > geom[4] do barH = geom[4]
		local cap = this.captureScreenRect geom[1] geom[2] geom[3] barH outfile:outfile
		if classOf cap == String do
			return ("{\"ok\":false,\"error\":\"" + (this.escapeJson cap) + "\"}")
		this.lastHwnd = hwnd
		this.lastTitle = "MaxMenuBar"
		this.metaJson hwnd "MaxMenuBar" cap[1] #(geom[1], geom[2], geom[3], barH) cap[2] cap[3] extra:"\"source\":\"max_menu_bar\""
	),

	-- List visible Win32 popup menus (#32768) as JSON array
	fn listPopupMenus =
	(
		local items = #()
		local kids = windows.getChildrenHWND (windows.getDesktopHWND())
		if kids == undefined do kids = #()
		for child in kids where child != undefined and child.count >= 5 do
		(
			local cls = child[4] as string
			if cls == "#32768" do
			(
				local hwnd = this.asHwndInt child[1]
				local vis = true
				try (vis = windows.isWindowVisible hwnd) catch ()
				if vis and hwnd != 0 do
				(
					local geom = this.getWindowGeom hwnd
					if geom != undefined and geom[3] > 8 and geom[4] > 8 do
					(
						append items (
							"{\"hwnd\":" + (hwnd as string) + \
							",\"title\":\"" + (this.escapeJson (this.normalizeTitle child[5])) + "\"" + \
							",\"screen_rect\":{\"x\":" + (geom[1] as string) + ",\"y\":" + (geom[2] as string) + \
								",\"w\":" + (geom[3] as string) + ",\"h\":" + (geom[4] as string) + "}}"
						)
					)
				)
			)
		)
		local body = ""
		for i = 1 to items.count do
		(
			if i > 1 do body += ","
			body += items[i]
		)
		("{\"ok\":true,\"count\":" + (items.count as string) + ",\"menus\":[" + body + "]}")
	),

	-- Capture the largest visible popup menu (typical after opening a menubar item)
	fn capturePopupMenuMeta outfile: =
	(
		local kids = windows.getChildrenHWND (windows.getDesktopHWND())
		if kids == undefined do kids = #()
		local bestHwnd = 0
		local bestGeom = undefined
		local bestArea = 0
		for child in kids where child != undefined and child.count >= 5 do
		(
			local cls = child[4] as string
			if cls == "#32768" do
			(
				local hwnd = this.asHwndInt child[1]
				local vis = true
				try (vis = windows.isWindowVisible hwnd) catch ()
				if vis and hwnd != 0 do
				(
					local geom = this.getWindowGeom hwnd
					if geom != undefined do
					(
						local area = geom[3] * geom[4]
						if area > bestArea and geom[3] > 20 and geom[4] > 20 do
						(
							bestArea = area
							bestHwnd = hwnd
							bestGeom = geom
						)
					)
				)
			)
		)
		if bestHwnd == 0 do return "{\"ok\":false,\"error\":\"no popup menu found\"}"
		local cap = this.captureHwndEx bestHwnd outfile:outfile
		if classOf cap == String do
			return ("{\"ok\":false,\"error\":\"" + (this.escapeJson cap) + "\"}")
		this.lastHwnd = bestHwnd
		this.lastTitle = "PopupMenu"
		this.lastScreenRect = bestGeom
		this.metaJson bestHwnd "PopupMenu" cap[1] bestGeom cap[2] cap[3] extra:"\"source\":\"popup_menu\""
	),

	fn ensureMouseHelper =
	(
		-- Already compiled in this Max session
		try
		(
			local existing = dotNetClass "McpDialogMouse"
			if existing != undefined do
			(
				MCP_DialogMouseHelperLoaded = true
				return true
			)
		)
		catch ()
		if MCP_DialogMouseHelperLoaded == true do return true

		local source = ""
		source += "using System;\n"
		source += "using System.Runtime.InteropServices;\n"
		source += "public class McpDialogMouse {\n"
		source += "  [DllImport(\"user32.dll\")] public static extern bool SetCursorPos(int X, int Y);\n"
		source += "  [DllImport(\"user32.dll\")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);\n"
		source += "  public const uint LEFTDOWN = 0x0002;\n"
		source += "  public const uint LEFTUP = 0x0004;\n"
		source += "  public static void Click(int x, int y) {\n"
		source += "    SetCursorPos(x, y);\n"
		source += "    System.Threading.Thread.Sleep(30);\n"
		source += "    mouse_event(LEFTDOWN, 0, 0, 0, UIntPtr.Zero);\n"
		source += "    System.Threading.Thread.Sleep(40);\n"
		source += "    mouse_event(LEFTUP, 0, 0, 0, UIntPtr.Zero);\n"
		source += "  }\n"
		source += "}\n"

		try
		(
			local provider = dotNetObject "Microsoft.CSharp.CSharpCodeProvider"
			local parms = dotNetObject "System.CodeDom.Compiler.CompilerParameters"
			parms.GenerateInMemory = true
			parms.ReferencedAssemblies.Add "System.dll"
			local results = provider.CompileAssemblyFromSource parms #(source)
			if results.Errors.HasErrors then
			(
				local msg = ""
				local errCount = results.Errors.Count
				for i = 0 to (errCount - 1) do
				(
					local err = results.Errors.Item[i]
					msg += (err.ErrorText as string) + "; "
				)
				format "MCP_DialogMonitor: mouse helper compile failed: %\n" msg
				return false
			)
			MCP_DialogMouseHelperLoaded = true
			true
		)
		catch
		(
			format "MCP_DialogMonitor: mouse helper load failed: %\n" (getCurrentException())
			false
		)
	),

	fn clickAtScreen x y foregroundHwnd:0 =
	(
		local ix = x as integer
		local iy = y as integer
		local fg = this.asHwndInt foregroundHwnd
		if fg != 0 do
		(
			try (windows.setForegroundWindow fg) catch ()
			sleep 0.05
		)
		if not (this.ensureMouseHelper()) do
			return "{\"ok\":false,\"error\":\"mouse helper unavailable\"}"
		try
		(
			local cls = dotNetClass "McpDialogMouse"
			cls.Click ix iy
			"{\"ok\":true,\"x\":" + (ix as string) + ",\"y\":" + (iy as string) + "}"
		)
		catch
		(
			("{\"ok\":false,\"error\":\"" + (this.escapeJson (getCurrentException() as string)) + "\"}")
		)
	),

	fn clickAtLocal hwnd lx ly =
	(
		local h = this.asHwndInt hwnd
		if h == 0 do return "{\"ok\":false,\"error\":\"invalid hwnd\"}"
		local geom = this.getWindowGeom h
		if geom == undefined do return "{\"ok\":false,\"error\":\"could not read window geometry\"}"
		local sx = geom[1] + (lx as integer)
		local sy = geom[2] + (ly as integer)
		this.clickAtScreen sx sy foregroundHwnd:h
	),

	fn toJsonResult path: title: hwnd: =
	(
		if path == unsupplied do path = this.lastCapturePath
		if title == unsupplied do title = this.lastTitle
		if hwnd == unsupplied do hwnd = this.lastHwnd
		local okFlag = path != undefined and path != "" and (findString (path as string) "ERROR:") != 1
		"{" + \
			"\"ok\":" + (if okFlag then "true" else "false") + "," + \
			"\"hwnd\":" + (hwnd as string) + "," + \
			"\"title\":\"" + (this.escapeJson title) + "\"," + \
			"\"file\":\"" + (this.escapeJson path) + "\"," + \
			"\"match_count\":" + (this.lastMatches.count as string) + \
		"}"
	)
)

MCP_DialogMonitor = DialogMonitorStruct()
MCP_Server_DialogMonitor = MCP_DialogMonitor

format "MCP_DialogMonitor ready.\n"
format "  MCP_DialogMonitor.findDialog()\n"
format "  MCP_DialogMonitor.captureDialogMeta()\n"
format "  MCP_DialogMonitor.captureMaxMenuBarMeta()\n"
format "  MCP_DialogMonitor.capturePopupMenuMeta()\n"
format "  MCP_DialogMonitor.clickAtScreen x y\n"
format "  MCP_DialogMonitor.clickAtLocal hwnd lx ly\n"
