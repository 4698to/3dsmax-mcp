/*
--------------------------------------------------------------------
场景管理结构体 (与 v1 相同)
--------------------------------------------------------------------
*/
global MCP_SceneManage

fn fn_dialogMonitor_missing_xfefs = (
	try (
		local hwnd = dialogMonitorOps.getWindowHandle()
		if hwnd != 0 do (
			local title = UIAccessor.GetWindowText hwnd
			if title != undefined do (
				if (findString title "Missing XRefs") != undefined do (
					UIAccessor.PressButtonByName hwnd "OK"
					print ("已自动关闭弹窗(dialogMonitor): " + title)
					return true
				)
				if (findString title "Missing External Files") != undefined do (
					UIAccessor.PressButtonByName hwnd "Continue"
					print ("已自动关闭弹窗: " + title)
					return true
				)
				if (findString title "Missing Map Coordinates") != undefined do (
					UIAccessor.PressButtonByName hwnd "Continue"
					print ("已自动关闭弹窗(dialogMonitor): " + title)
					return true
				)
			)
		)
	) catch ()
	false
)

-- Agent takeover banner: show pre-saved PNG via a borderless dialog
-- (gw has no portable bitmap blit API across Max versions).
global MCP_AgentBannerVisible = false
global MCP_AgentBannerBmp = undefined
global MCP_AgentBannerBmpSize = [0, 0]
global MCP_AgentBannerImagePath = ""
try (MCP_AgentBannerImagePath = (getFilenamePath (getSourceFileName())) + "agent_banner.png") catch ()
global MCP_AgentBannerForm

fn MCP_FreeAgentBannerBmp =
(
	try (if MCP_AgentBannerBmp != undefined do close MCP_AgentBannerBmp) catch ()
	MCP_AgentBannerBmp = undefined
	MCP_AgentBannerBmpSize = [0, 0]
)

fn MCP_LoadAgentBannerBmp =
(
	if MCP_AgentBannerBmp != undefined do return true
	local imgPath = MCP_AgentBannerImagePath
	if imgPath == undefined or imgPath == "" do return false
	if not (doesFileExist imgPath) do return false
	MCP_AgentBannerBmp = openBitmap imgPath
	if MCP_AgentBannerBmp == undefined do return false
	MCP_AgentBannerBmpSize = [MCP_AgentBannerBmp.width, MCP_AgentBannerBmp.height]
	true
)

fn MCP_GetAgentBannerDialogPos bw bh =
(
	-- Prefer bottom-right of the Max main window; fall back to primary screen.
	local margin = 16
	local sx = 0
	local sy = 0
	local sw = sysInfo.desktopSize.x
	local sh = sysInfo.desktopSize.y
	try
	(
		local hwnd = windows.getMAXHWND()
		local r = windows.getWindowPos hwnd
		-- Some Max builds return #(l,t,r,b); others Box2 / Point4-like.
		if classOf r == Array and r.count >= 4 then
		(
			sx = r[1]
			sy = r[2]
			sw = r[3] - r[1]
			sh = r[4] - r[2]
		)
		else
		(
			try
			(
				sx = r.x
				sy = r.y
				sw = r.w
				sh = r.h
			)
			catch ()
		)
	)
	catch ()
	local x = sx + sw - bw - margin
	local y = sy + sh - bh - margin - 48 -- leave room for Max status bar
	if x < sx + margin do x = sx + margin
	if y < sy + margin do y = sy + margin
	[x as integer, y as integer]
)

fn MCP_DestroyAgentBannerDialog =
(
	try
	(
		if MCP_AgentBannerForm != undefined do
		(
			try (if MCP_AgentBannerForm.BackgroundImage != undefined do MCP_AgentBannerForm.BackgroundImage.Dispose()) catch ()
			MCP_AgentBannerForm.Close()
			MCP_AgentBannerForm.Dispose()
		)
	)
	catch ()
	MCP_AgentBannerForm = undefined
)

fn MCP_ShowAgentBannerDialog =
(
	MCP_DestroyAgentBannerDialog()
	local imgPath = MCP_AgentBannerImagePath
	if imgPath == undefined or imgPath == "" do return false
	if not (doesFileExist imgPath) do return false
	local img = undefined
	try (img = (dotNetClass "System.Drawing.Image").FromFile imgPath) catch (img = undefined)
	if img == undefined do return false
	local bw = img.Width
	local bh = img.Height
	local pos = MCP_GetAgentBannerDialogPos bw bh
	local form = dotNetObject "System.Windows.Forms.Form"
	form.Text = "MCP Agent"
	form.FormBorderStyle = (dotNetClass "System.Windows.Forms.FormBorderStyle").None
	form.ShowInTaskbar = false
	form.TopMost = true
	form.StartPosition = (dotNetClass "System.Windows.Forms.FormStartPosition").Manual
	form.Size = dotNetObject "System.Drawing.Size" bw bh
	form.Location = dotNetObject "System.Drawing.Point" (pos.x as integer) (pos.y as integer)
	form.BackgroundImage = img
	form.BackgroundImageLayout = (dotNetClass "System.Windows.Forms.ImageLayout").None
	-- Soften click-through feel: no focus when shown
	try (form.ShowWithoutActivation = true) catch ()
	form.Show()
	MCP_AgentBannerForm = form
	MCP_AgentBannerBmpSize = [bw, bh]
	true
)

struct SceneManageStruct
(
	-- Load a .max scene file with existence + version guards.
	-- Returns "Loaded scene: <name>" or "ERROR: ...".
	fn loadScene filePath =
	(
		if not (doesFileExist filePath) do(
			return ("ERROR: file not found: " + filePath)
		)
		-- 版本检查：文件主版本号不得高于当前 3ds Max 主版本号
		local versionOk = true
		local versionInfo = ""
		try
		(
			local fileData = getMaxFileVersionData filePath
			local fileMajor = 0
			if fileData != undefined and fileData.count >= 1 do fileMajor = fileData[1]
			local curMajor = 0
			try
			(
				local mv = maxVersion()
				-- maxVersion() 返回数组（新版本）或整数（旧版本），两种都兼容
				if mv.count != undefined then
					(curMajor = mv[1])
				else
					(curMajor = mv)
			) catch ()
			versionInfo = "file " + (fileMajor as string) + " vs max " + (curMajor as string)
			if curMajor > 0 and fileMajor > curMajor do versionOk = false
		)
		catch
		(
			versionInfo = "version unreadable"
		)

		if not versionOk do(
			return ("ERROR: unsupported file version (" + versionInfo + ")")
		)
		dialogMonitorOps.enabled = true
		dialogMonitorOps.interactive = false
		dialogMonitorOps.registerNotification fn_dialogMonitor_missing_xfefs id:#missingXfefs

		missingXRefs_val = #()
		missingExtFiles_val = #()
		missingDLLsList_val = #()

		--loadMaxFile filePath quiet:true
		loadMAXFile filePath useFileUnits:true missingXRefsAction:#logmsg missingExtFilesActio:#logmsg missingDLLsAction:#logmsg missingXRefsList:&missingXRefs_val missingDLLsList:&missingDLLsList_val missingExtFilesList:&missingExtFiles_val quiet:true  -- 打开max 文件

		if objects.count == 0 do
		(
			format "loadMAXFile  文件版本不支持，放弃加载-> %\n" filePath
		)
		-- 注销 missing xfefs 弹窗监听
		dialogMonitorOps.unregisterNotification id:#missingXfefs
		dialogMonitorOps.enabled = false
		dialogMonitorOps.interactive = true

		"Loaded scene: " + maxFileName
	),

	-- Snapshot current state into Max's hold buffer.
	fn holdState =
	(
		holdMaxFile()
		"Hold saved successfully"
	),

	-- Restore the held state.
	fn fetchState =
	(
		fetchMaxFile quiet:true
		"Fetched (restored) held state"
	),

	-- Start a new empty scene.
	fn resetScene =
	(
		resetMaxFile #noPrompt
		"Scene reset to empty"
	),
	fn get_system_temp_dir =
	(
		local temp_dir = sysInfo.tempdir
		if temp_dir == undefined or temp_dir == "" do temp_dir = (getDir #temp)
		temp_dir = substituteString temp_dir "\\" "/"
		if temp_dir[temp_dir.count] != "/" do temp_dir += "/"
		temp_dir
	),

	-- ASCII local dir: %TEMP%\\3dsmax-mcp\\ (created if missing).
	fn getCommsDir =
	(
		local dir = this.get_system_temp_dir() + @"3dsmax-mcp/"
		makeDir dir all:true
		dir
	),

	fn isAbsolutePath p =
	(
		p = substituteString (p as string) "\\" "/"
		if p.count < 2 do return false
		-- Drive letter or UNC
		(p[2] == ":") or (p[1] == "/" and p[2] == "/")
	),

	-- Max cwd / plugin-config junk (relative saves often land here).
	fn isUnsafeSaveDir p =
	(
		local low = toLower (substituteString (p as string) "\\" "/")
		(findString low "plugcfg") != undefined or \
		(findString low "/enu/temp") != undefined or \
		(findString low "/enu/plugcfg") != undefined
	),

	-- Save as 3 major versions older when the year is known (e.g. Max 2022 -> 2019).
	-- Unnamed scenes get a random path under %TEMP%\\3dsmax-mcp\\.
	-- NOTE: do not name this `saveAsVersion` — that collides with saveMaxFile().
	fn saveToPath dest =
	(
		dest = substituteString (dest as string) "\\" "/"
		local parentDir = getFilenamePath dest
		if parentDir != undefined and parentDir != "" do makeDir parentDir all:true

		local mv = maxVersion() -- 低版本max 格式 #(17000, 44, 0)
		local curYear = 0
		try (curYear = ((mv[1] * 0.001) as integer) + 1998) catch (curYear = 0)

		local targetYear = 0
		if curYear > 0 then
		(
			targetYear = curYear - 3
			if targetYear < 2012 do targetYear = 2012
		)

		local saved = false
		try
		(
			-- With saveAsVersion, Max defaults useNewFile:false (Save Copy As) —
			-- file is written but maxFileName / maxFilePath stay on the old scene.
			-- Always force useNewFile:true so Save As updates the current scene.
			if targetYear > 0 then
				saved = saveMaxFile dest saveAsVersion:targetYear quiet:true useNewFile:true
			else
				saved = saveMaxFile dest quiet:true useNewFile:true
		)
		catch
		(
			return ("ERROR: save failed: " + (getCurrentException() as string))
		)
		if saved then dest else ("ERROR: saveMaxFile returned false: " + dest)
	),

	fn saveScene =
	(
		local _name = maxFileName
		local fp = maxFilePath + maxFileName
		-- Empty / Max plugcfg-temp junk → force local temp
		if _name == undefined do _name = ""
		if _name == "" or fp == "" or (this.isUnsafeSaveDir fp) then
		(
			if _name == "" then
			(
				local guid = ((dotNetClass "System.Guid").NewGuid()).ToString("N")
				_name = "scene_" + guid + ".max"
			)
			else
			(
				-- basename only if path was junk
				local leaf = filenameFromPath fp
				if leaf != undefined and leaf != "" do _name = leaf
			)
			fp = this.getCommsDir() + _name
		)
		else
		(
			fp = substituteString fp "\\" "/"
		)
		this.saveToPath fp
	),

	-- Save/copy the scene to an explicit path (caller should pass absolute path).
	-- Relative names are forced under getCommsDir so Max does not use plugcfg cwd.
	fn saveSceneAs filePath =
	(
		if filePath == undefined or (filePath as string) == "" do
			return "ERROR: empty file path"
		local dest = substituteString (filePath as string) "\\" "/"
		local ext = getFilenameType dest
		if ext == undefined or ext == "" do dest = dest + ".max"
		if not (this.isAbsolutePath dest) then
		(
			local leaf = filenameFromPath dest
			if leaf == undefined or leaf == "" do leaf = dest
			dest = this.getCommsDir() + leaf
		)
		this.saveToPath dest
	),

	-- Basic scene info as a JSON-ish string.
	fn getInfo =
	(
		local fp = maxFilePath + maxFileName
		if fp == "" do fp = "(unsaved)"
		local objCount = objects.count
		local polyCount = 0
		for obj in objects do
		(
			try
			(
				local m = snapshotAsMesh obj
				polyCount += m.numFaces
				delete m
			) catch ()
		)
		"{" + \
			"\"filePath\":\"" + (substituteString fp "\\" "\\\\") + "\"," + \
			"\"objectCount\":" + (objCount as string) + "," + \
			"\"polyCount\":" + (polyCount as string) + \
		"}"
	),

	-- Compact JSON array of the current selection: name/class/position/wirecolor.
	-- (Migrated from maxmcp/tools/_query_scene_core.py _selection_compact_maxscript)
	fn getSelectionCompact =
	(
		local arr = #()
		for obj in selection do (
			-- Biped_Object / helper nodes may not expose .pos; fall back safely.
			local p = [0, 0, 0]
			try (p = obj.transform.position) catch (try (p = obj.transform.position) catch (p = [0, 0, 0]))
			local posStr = this.point3ToJson p
			local colorStr = this.point3ToJson obj.wirecolor
			local entry = "{" + \
				"\"name\":\"" + obj.name + "\"," + \
				"\"class\":\"" + ((classOf obj) as string) + "\"," + \
				"\"position\":" + posStr + "," + \
				"\"wirecolor\":" + colorStr + \
			"}"
			append arr entry
		)
		local result = "["
		for i = 1 to arr.count do (
			if i > 1 do result += ","
			result += arr[i]
		)
		result += "]"
		result
	),

	-- Full JSON dump of the current selection, capped at max_items entries:
	-- name/class/parent/material/modifiers/pos/bbox per selected node.
	-- (Migrated from maxmcp/tools/_query_scene_core.py _selection_full_maxscript)
	fn getSelectionFull max_items:50 =
	(
		local esc = MCP_Server.escapeJsonString
		local arr = ""
		local count = 0
		local cap = max_items
		local total = selection.count

		for obj in selection while count < cap do (
			if count > 0 do arr += ","
			count += 1

			-- Biped_Object / helper nodes may not expose .pos; fall back safely.
			local p = [0, 0, 0]
			try (p = obj.transform.pos) catch (try (p = obj.transform.position) catch (p = [0, 0, 0]))
			local posStr = this.point3ToJson p

			local matField = if obj.material != undefined \
				then ("\"" + (esc obj.material.name) + "\"") else "null"

			local parentField = if obj.parent != undefined \
				then ("\"" + (esc obj.parent.name) + "\"") else "null"

			local modArr = ""
			for m = 1 to obj.modifiers.count do (
				if m > 1 do modArr += ","
				modArr += "\"" + (esc ((classOf obj.modifiers[m]) as string)) + "\""
			)

			local bboxStr = "null"
			try (
				local bb = nodeGetBoundingBox obj (matrix3 1)
				bboxStr = "[" + this.point3ToJson bb[1] + "," + this.point3ToJson bb[2] + "]"
			) catch ()

			arr += "{\"name\":\"" + (esc obj.name) + "\"" + \
			       ",\"class\":\"" + (esc ((classOf obj) as string)) + "\"" + \
			       ",\"parent\":" + parentField + \
			       ",\"material\":" + matField + \
			       ",\"modifiers\":[" + modArr + "]" + \
			       ",\"pos\":" + posStr + \
			       ",\"bbox\":" + bboxStr + "}"
		)
		"{\"selected\":" + (total as string) + ",\"objects\":[" + arr + "]}"
	),

	-- JSON map of every scene node keyed by AnimHandle, for scene-delta diffing:
	-- name/class/position/material/modifierCount/hidden per node.
	-- (Migrated from maxmcp/tools/_query_scene_core.py _capture_scene_state)
	fn captureSceneState =
	(
		local esc = MCP_Server.escapeJsonString
		local result = ""
		local count = 0
		for obj in objects do (
			if count > 0 do result += ","
			count += 1
			-- Biped_Object / helper nodes may not expose .pos; fall back safely.
			local p = [0, 0, 0]
			try (p = obj.transform.pos) catch (try (p = obj.transform.position) catch (p = [0, 0, 0]))
			local posStr = this.point3ToJson p
			local matName = if obj.material != undefined then (esc obj.material.name) else ""
			local cn = esc ((classOf obj) as string)
			local hidden = if obj.isHidden then "true" else "false"
			-- Key by node handle, not name: Max names aren't unique, and a name key
			-- would collapse duplicate-named nodes into one entry (last wins).
			local hkey = (getHandleByAnim obj) as string
			result += "\"" + hkey + "\":{" + \
				"\"name\":\"" + (esc obj.name) + "\"," + \
				"\"c\":\"" + cn + "\"," + \
				"\"p\":" + posStr + "," + \
				"\"m\":\"" + matName + "\"," + \
				"\"n\":" + (obj.modifiers.count as string) + "," + \
				"\"h\":" + hidden + "}"
		)
		"{" + result + "}"
	),

	-- Filter the whole scene (objects) by class/name pattern/layer/roots_only,
	-- apply limit+offset paging, and return a JSON object with totalMatched and
	-- per-node details: name/class/position/parent/numChildren/isHidden/isFrozen/layer.
	-- (Migrated from maxmcp/tools/_query_scene_core.py _filter_list_maxscript)
	fn filterObjects class_name:"" pattern:"" layer:"" roots_only:false limit:200 offset:0 =
	(
		local esc = MCP_Server.escapeJsonString
		local matched = #()
		for _obj in objects do (
			local _ok = true
			if class_name != "" and ((classOf _obj) as string) != class_name do _ok = false
			if _ok and pattern != "" and (not (matchPattern _obj.name pattern:pattern)) do _ok = false
			if _ok and layer != "" and _obj.layer.name != layer do _ok = false
			if _ok and roots_only and _obj.parent != undefined do _ok = false
			if _ok do append matched _obj
		)
		local totalMatched = matched.count
		local startIdx = offset + 1
		local endIdx = amin #(matched.count, offset + limit)
		local arr = #()
		for i = startIdx to endIdx do (
			local _obj = matched[i]
			local p = [0, 0, 0]
			try (p = _obj.transform.pos) catch (try (p = _obj.transform.position) catch (p = [0, 0, 0]))
			local posStr = this.point3ToJson p
			local parentName = if _obj.parent != undefined then _obj.parent.name else ""
			local parentField = if parentName == "" then "null" else ("\"" + (esc parentName) + "\"")
			local entry = "{" + \
				"\"name\":\"" + (esc _obj.name) + "\"," + \
				"\"class\":\"" + (esc ((classOf _obj) as string)) + "\"," + \
				"\"position\":" + posStr + "," + \
				"\"parent\":" + parentField + "," + \
				"\"numChildren\":" + (_obj.children.count as string) + "," + \
				"\"isHidden\":" + (if _obj.isHidden then "true" else "false") + "," + \
				"\"isFrozen\":" + (if _obj.isFrozen then "true" else "false") + "," + \
				"\"layer\":\"" + (esc _obj.layer.name) + "\"" + \
			"}"
			append arr entry
		)
		local result = "{\"totalMatched\":" + (totalMatched as string) + ",\"objects\":["
		for i = 1 to arr.count do (
			if i > 1 do result += ","
			result += arr[i]
		)
		result += "]}"
		result
	),
	temp_geo = #(),
	temp_bones = #(),
	temp_geo_handles = #(),
	temp_bones_handles = #(),
	-- Mesh name filter for getunhidden_meshes_bones (case-insensitive).
	-- Drop names starting with these prefixes, except exact allow-list entries.
	meshExcludePrefixes = #("v_"),
	meshExcludeAllowNames = #("v_body"),

	-- true = skip this mesh name from unhidden mesh list.
	fn isExcludedMeshName nodeName =
	(
		if nodeName == undefined do return false
		local low = toLower (nodeName as string)
		local allows = this.meshExcludeAllowNames
		if allows == undefined do allows = #("v_body")
		for a in allows do
		(
			if low == (toLower (a as string)) do return false
		)
		local prefixes = this.meshExcludePrefixes
		if prefixes == undefined do prefixes = #("v_")
		for pfx in prefixes do
		(
			local p = toLower (pfx as string)
			if p.count > 0 and (findString low p) == 1 do return true
		)
		false
	),

	-- string array -> JSON array literal, e.g. #("a","b") -> "[\"a\",\"b\"]"
	fn stringArrayToJson arr =
	(
		str_body = stringstream ""
		if arr == undefined or arr.count == 0 then (
			format "[]" to:str_body
			return (str_body as string)
		)else(
			format "[" to:str_body
			for i = 1 to arr.count do
			(
				if i != arr.count then (
					format "\"%\"," arr[i] to:str_body
				)else(
					format "\"%\"" arr[i] to:str_body
				)
			)
			format "]" to:str_body
			return (str_body as string)
		)
	),
	fn intarrayToJson arr =
	(
		str_body = stringstream ""
		if arr == undefined or arr.count == 0 then (
			format "[]" to:str_body
			return (str_body as string)
		)else(
			format "[" to:str_body
			for i = 1 to arr.count do
			(
				-- IntegerPtr (e.g. 12345P) must become a plain int for JSON
				local n = arr[i]
				try (n = n as integer) catch ()
				if i != arr.count then (
					format "%," n to:str_body
				)else(
					format "%" n to:str_body
				)
			)
			format "]" to:str_body
			return (str_body as string)
		)
	),
	fn point3ToJson p3 =
	(
		"[" + (p3.x as string) + "," + (p3.y as string) + "," + (p3.z as string) + "]"
	),
	-- Resolve a node by AnimHandle; prefers maxOps.getNodeByHandle, then scan.
	fn findNodeByHandle h =
	(
		local hi = h
		try (hi = h as integer) catch ()
		local obj = undefined
		try (obj = maxOps.getNodeByHandle hi) catch (obj = undefined)
		obj
	),

	-- Select nodes by handle array. Returns JSON:
	-- {"ok":true,"count":N,"handles":[...],"names":[...]}
	fn selectByHandles handleList =
	(
		clearSelection()
		local found = #()
		local foundNames = #()
		if handleList == undefined do handleList = #()
		_select_nodes = #()
		for h in handleList do
		(
			local obj = this.findNodeByHandle h
			if obj != undefined do
			(
				local hi = h
				try (hi = h as integer) catch ()
				append found hi
				append foundNames obj.name
				append _select_nodes obj
			)
		)
		select _select_nodes
		"{" + \
			"\"ok\":" + (if found.count > 0 then "true" else "false") + "," + \
			"\"count\":" + (found.count as string) + "," + \
			"\"handles\":" + (this.intarrayToJson found) + "," + \
			"\"names\":" + (this.stringArrayToJson foundNames) + \
		"}"
	),
	-- Group every unhidden node by its MAXScript class and emit JSON:
	-- {"type_count":K,"total":N,"types":{"<class>":{"count":N,"objects":["name",...]},...}}
	fn getSceneUnhiddenObjects =
	(
		local typeNames = #()   -- unique class names, first-seen order
		local typeObjects = #() -- parallel array: per-type name arrays
		local total = 0
		for o in objects where (not o.isHidden) do
		(
			local t = (classOf o) as string
			local idx = 0
			for i = 1 to typeNames.count do
				if typeNames[i] == t do idx = i
			if idx == 0 then
			(
				append typeNames t
				append typeObjects #(o.name)
			)
			else
			(
				append typeObjects[idx] o.name
			)
			total += 1
		)

		local result = "{\"type_count\":" + (typeNames.count as string) + \
		               ",\"total\":" + (total as string) + ",\"types\":{"
		for i = 1 to typeNames.count do
		(
			if i > 1 do result += ","
			result += "\"" + typeNames[i] + "\":{" + \
			          "\"count\":" + (typeObjects[i].count as string) + "," + \
			          "\"objects\":" + (this.stringArrayToJson typeObjects[i]) + "}"
		)
		result += "}}"
		result
	),
	-- Collect unhidden Editable_Poly/Mesh + Bone/Biped; keep nodes on this.temp_*
	-- and return a JSON string of names for the Python bridge.
	fn getunhidden_meshes_bones =
	(
		this.temp_geo = #()
		this.temp_bones = #()
		this.temp_geo_handles = #()
		this.temp_bones_handles = #()
        
		local _geo_name = #()
		local _geo_handle = #()
		local _bones_name = #()
		local _bones_handle = #()
		for o in objects where (not o.isHidden) do
		(
			if (isKindOf o BoneGeometry) or (isKindOf o Biped_Object) then
			(
				append _bones_name o.name
				append this.temp_bones o
				append _bones_handle o.handle
			)
			else
			(
				local m_type = classOf o.baseObject
				if m_type == Editable_Poly or m_type == Editable_Mesh do
				(
					if not (this.isExcludedMeshName o.name) do
					(
						append _geo_name o.name
						append this.temp_geo o
						append _geo_handle o.handle
					)
				)
			)
		)

		"{" + \
			"\"mesh_count\":" + (_geo_name.count as string) + "," + \
			"\"bone_count\":" + (_bones_name.count as string) + "," + \
			"\"bones_handle\":" + (this.intarrayToJson _bones_handle) + "," + \
			"\"meshes_handle\":" + (this.intarrayToJson _geo_handle) + "," + \
			"\"meshes\":" + (this.stringArrayToJson _geo_name) + "," + \
			"\"bones\":" + (this.stringArrayToJson _bones_name) + \
		"}"
	),

	-- Show Agent banner (pre-saved PNG in a borderless TopMost form).
	-- text: is ignored; image is maxscript/mcp/agent_banner.png.
	fn showAgentBanner text:undefined =
	(
		global MCP_AgentBannerVisible
		MCP_AgentBannerVisible = true
		if not (MCP_ShowAgentBannerDialog()) do
			return ("ERROR: agent banner image not found: " + (MCP_AgentBannerImagePath as string))
		"Agent banner shown"
	),

	-- Hide the Agent banner form (idempotent).
	fn hideAgentBanner =
	(
		global MCP_AgentBannerVisible
		MCP_AgentBannerVisible = false
		MCP_DestroyAgentBannerDialog()
		MCP_FreeAgentBannerBmp()
		"Agent banner hidden"
	)
)
MCP_SceneManage = SceneManageStruct()