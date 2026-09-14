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

	-- Save the current scene back to its file path.
	-- Returns the saved path, or "NO_FILE" when the scene was never saved.
	fn saveScene =
	(
		local fp = maxFilePath + maxFileName
		if fp == "" then(
			"NO_FILE"
		)
		else
		(
			saveMaxFile fp
			fp
		)
	),

	-- Save the current scene as a file 3 major versions older than the
	-- running 3ds Max (e.g. Max 2022 -> saved as Max 2019).
	-- The copy is written to <name>_v<year>.max and does NOT overwrite
	-- the original. Returns the new file path, or "NO_FILE" / "ERROR: ...".
	-- NOTE: member name must NOT be `saveAsVersion` — that would collide
	-- with the `saveAsVersion:` keyword of saveMaxFile() inside this body.
	fn saveOlderVersion =
	(
		local fp = maxFilePath + maxFileName
		if fp == "" then
			return "NO_FILE"

		-- 确定当前版本年份（Max 2022 的 maxVersion() 为数组，[8]=2022；
		-- 旧版返回整数主版本，17 = Max 2015，年份 = 主版本 + 1998）
		local mv = maxVersion()
		local curYear = 0
		if classOf mv == Array then
		(
			-- 优先取数组中的年份元素（2010..2100 范围）
			curYear = mv[8]
		)
		else(
			curYear = mv + 1998
		)

		if curYear <= 0 do(
			return "ERROR: cannot determine current 3ds Max version"
		)

		local targetYear = curYear - 3
		if targetYear < 2012 do targetYear = 2012  -- 最低支持到 3ds Max 2012

		local newFp = \
			(getFilenamePath fp) + (getFilenameFile fp) + \
			"_v" + (targetYear as string) + (getFilenameType fp)

		local saved = false
		-- 新版（2016+）：saveMaxFile 原生 saveAsVersion:<年份> 参数（EMxsMaxFileVersion）
		saved = saveMaxFile newFp saveAsVersion:targetYear quiet:true
		if saved then newFp else "ERROR: save failed"
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
	temp_geo = #(),
	temp_bones = #(),
    temp_geo_handles = #(),
    temp_bones_handles = #(),

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
					append _geo_name o.name
					append this.temp_geo o
					append _geo_handle o.handle
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
	)
)
MCP_SceneManage = SceneManageStruct()