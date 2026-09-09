/*
	3ds Max MCP Server - v2 (带控制面板 UI)
	====================================================================
	相比 v1 (mcp_server.ms) 的改进:
	  1. dotNet 浮动控制面板, 直接在 3ds Max 内管理 MCP 服务
	  2. 可填写绑定 IP 地址
	  3. 本地 / 远程模式切换:
	        本地模式 -> 绑定 127.0.0.1 (仅本机 Python 端可连)
	        远程模式 -> 绑定 0.0.0.0 或自定义 IP (局域网内可连)
	  4. 服务启动 / 停止按钮, 端口 / 安全模式可配置, 状态实时显示
	  5. 远程模式下提示 Python 端所需 MAXMCP_INSTANCES 配置

	用法:
	    fileIn "mcp_server_v2.ms"
	    -- 自动: 停止旧实例 -> 本地模式启动 -> 弹出控制面板

	协议与注册表机制与 v1 完全一致 (JSON over TCP, JSONL 注册表心跳)。
	全局对象:
	    MCP_Server   -- 服务端 (MCPServerStruct)
	    MCP_SceneManage -- 场景管理 (SceneManageStruct)
	    MCP_UI       -- 控制面板 (MCPUIStruct)
*/

global MCP_Server
global MCP_SceneManage
global MCP_UI

/*
	--------------------------------------------------------------------
	TCP 服务端结构体 (v2: 支持自定义绑定 IP)
	--------------------------------------------------------------------
*/
struct MCPServerStruct
(
	timer = undefined,
	heartbeatTimer = undefined,
	listener = undefined,
	isRunning = false,
	port = 8765,
	-- 绑定地址: 本地模式 "127.0.0.1", 远程模式 "0.0.0.0" 或自定义 IP
	bindIP = "127.0.0.1",
	safeMode = true,
	protocolVersion = 2,

	-- Parse a hex string (e.g. "00e9") to integer for \uXXXX decoding
	fn hexToInt hexStr =
	(
		local val = 0
		for i = 1 to hexStr.count do
		(
			local c = bit.charAsInt (toLower hexStr[i])
			local d = if c >= 48 and c <= 57 then c - 48
					  else if c >= 97 and c <= 102 then c - 87
					  else 0
			val = val * 16 + d
		)
		val
	),

	-- Escape a string for safe JSON embedding (handles all JSON-required escapes)
	fn escapeJsonString str =
	(
		if str == undefined then return ""
		local s = str as string
		-- Fast path: common escapes via native substituteString
		s = substituteString s "\\" "\\\\"
		s = substituteString s "\"" "\\\""
		s = substituteString s "\n" "\\n"
		s = substituteString s "\r" "\\r"
		s = substituteString s "\t" "\\t"
		-- Check for remaining control chars (backspace, form feed, etc.)
		local hasControl = false
		for i = 1 to s.count while not hasControl do
		(
			local code = bit.charAsInt s[i]
			if code < 32 do hasControl = true
		)
		if hasControl then
		(
			local result = ""
			local hexChars = "0123456789abcdef"
			for i = 1 to s.count do
			(
				local code = bit.charAsInt s[i]
				if code == 8 then result += "\\b"
				else if code == 12 then result += "\\f"
				else if code < 32 then
				(
					local hi = (mod (bit.shift code -4) 16) as integer + 1
					local lo = (mod code 16) as integer + 1
					result += "\\u00" + hexChars[hi] + hexChars[lo]
				)
				else result += s[i]
			)
			s = result
		)
		s
	),

	-- Safe execution: block dangerous commands, allow normal scene operations.
	-- Blocks DOSCommand, ShellLaunch, deleteFile, createFile, python.Execute
	fn safeExecute code =
	(
		local lowered = toLower (code as string)
		local blocked = #("doscommand", "shelllaunch", "deletefile", "createfile", "python.execute")
		for b in blocked do
		(
			if findString lowered b != undefined then
				throw ("Blocked by safe mode: " + b)
		)
		execute code
	),

	-- Build a JSON response string
	fn buildResponse success result errorMsg requestId:"" startedAt:0 cmdType:"maxscript" =
	(
		local safeResult = escapeJsonString (if result != undefined then (result as string) else "")
		local safeError = escapeJsonString errorMsg
		local successStr = if success then "true" else "false"
		local safeReqId = escapeJsonString requestId
		local safeCmdType = escapeJsonString cmdType
		local reqField = if requestId != "" then ("\"" + safeReqId + "\"") else "null"
		local durationMs = amax #(0, (timeStamp() - startedAt))

		"{" + \
			"\"success\":" + successStr + "," + \
			"\"requestId\":" + reqField + "," + \
			"\"result\":\"" + safeResult + "\"," + \
			"\"error\":\"" + safeError + "\"," + \
			"\"meta\":{" + \
				"\"protocolVersion\":" + (protocolVersion as string) + "," + \
				"\"cmdType\":\"" + safeCmdType + "\"," + \
				"\"safeMode\":" + (if safeMode then "true" else "false") + "," + \
				"\"durationMs\":" + (durationMs as string) + \
			"}" + \
		"}"
	),

	-- Extract a JSON string value by key (simple parser)
	fn extractJsonValue jsonStr key =
	(
		searchKey = "\"" + key + "\""
		keyPos = findString jsonStr searchKey
		if keyPos == undefined then return ""

		colonPos = findString (substring jsonStr (keyPos + searchKey.count) -1) ":"
		if colonPos == undefined then return ""
		startPos = keyPos + searchKey.count + colonPos

		while startPos <= jsonStr.count and (jsonStr[startPos] == " " or jsonStr[startPos] == "\t") do
			startPos += 1

		if startPos > jsonStr.count then return ""

		if jsonStr[startPos] == "\"" then
		(
			pos = startPos + 1
			result = ""
			while pos <= jsonStr.count do
			(
				if jsonStr[pos] == "\\" and pos + 1 <= jsonStr.count then
				(
					nextChar = jsonStr[pos + 1]
					if nextChar == "\"" then (result += "\""; pos += 2)
					else if nextChar == "\\" then (result += "\\"; pos += 2)
					else if nextChar == "n" then (result += "\n"; pos += 2)
					else if nextChar == "r" then (result += "\r"; pos += 2)
					else if nextChar == "t" then (result += "\t"; pos += 2)
					else if nextChar == "b" then (result += (bit.intAsChar 8); pos += 2)
					else if nextChar == "f" then (result += (bit.intAsChar 12); pos += 2)
					else if nextChar == "/" then (result += "/"; pos += 2)
					else if nextChar == "u" and pos + 5 <= jsonStr.count then
					(
						local hexStr = substring jsonStr (pos + 2) 4
						local codepoint = hexToInt hexStr
						if codepoint > 0 then
						(
							if codepoint <= 255 then
								result += bit.intAsChar codepoint
							else
								try (result += ((dotNetClass "System.Char").ConvertFromUtf32 codepoint))
								catch (result += "?")
						)
						pos += 6
					)
					else (result += jsonStr[pos]; pos += 1)
				)
				else if jsonStr[pos] == "\"" then
				(
					exit
				)
				else
				(
					result += jsonStr[pos]
					pos += 1
				)
			)
			return result
		)
		else
		(
			pos = startPos
			while pos <= jsonStr.count and jsonStr[pos] != "," and jsonStr[pos] != "}" do
				pos += 1
			return (trimRight (trimLeft (substring jsonStr startPos (pos - startPos))))
		)
	),

	-- Process a request and return JSON response
	fn processRequest jsonString =
	(
		local response = ""
		local startedAt = timeStamp()

		try
		(
			local command = extractJsonValue jsonString "command"
			local cmdType = extractJsonValue jsonString "type"
			local requestId = extractJsonValue jsonString "requestId"

			if cmdType == "" do cmdType = "maxscript"

			if cmdType != "ping" and command == "" then
			(
				return (buildResponse false undefined "Empty command" requestId:requestId startedAt:startedAt cmdType:cmdType)
			)

			local result = undefined
			local success = true
			local errorMsg = ""
			local savedSafeMode = safeMode

			try
			(
				if cmdType == "ping" then
				(
					local maxYear = ((1998 + ((maxVersion())[1] / 1000)) as integer)
					local rendererName = try ((classOf renderers.current) as string) catch "unknown"
					result = "{\"pong\":true" + \
						",\"server\":\"3dsmax-mcp\"" + \
						",\"protocolVersion\":" + (protocolVersion as string) + \
						",\"maxVersion\":" + (maxYear as string) + \
						",\"renderer\":\"" + (escapeJsonString rendererName) + "\"" + \
						",\"objectCount\":" + (objects.count as string) + \
						",\"selectionCount\":" + (selection.count as string) + \
						",\"safeMode\":" + (if safeMode then "true" else "false") + \
						",\"port\":" + (port as string) + \
						",\"bindIP\":\"" + (escapeJsonString bindIP) + "\"}"
				)
				else if cmdType == "maxscript" then
				(
					if safeMode then
						result = safeExecute command
					else
						result = execute command
				)
				else if cmdType == "python" then
				(
					if safeMode then
					(
						success = false
						errorMsg = "Python execution blocked by safe mode"
					)
					else
					(
						python.Execute command
						result = "Python executed"
					)
				)
				else
				(
					success = false
					errorMsg = "Unknown command type: " + cmdType
				)
			)
			catch
			(
				success = false
				errorMsg = getCurrentException()
			)

			-- Restore safeMode so commands cannot tamper with it
			safeMode = savedSafeMode

			response = buildResponse success result errorMsg requestId:requestId startedAt:startedAt cmdType:cmdType
		)
		catch
		(
			response = buildResponse false undefined ("Parse error: " + (getCurrentException())) startedAt:startedAt
		)

		response
	),

	-- Handle a single client connection
	fn handleClient client =
	(
		try
		(
			stream = client.GetStream()
			reader = dotNetObject "System.IO.StreamReader" stream
			writer = dotNetObject "System.IO.StreamWriter" stream

			-- Read the request line
			requestLine = reader.ReadLine()

			-- Convert .NET string to MAXScript string
			requestStr = requestLine as string

			format "MCP: Received request: %\n" requestStr

			if requestStr != undefined and requestStr.count > 0 then
			(
				-- Process and send response
				responseStr = processRequest requestStr
				format "MCP: Sending response: %\n" responseStr
				writer.WriteLine responseStr
				writer.Flush()
			)
			else
			(
				format "MCP: Empty request received\n"
			)

			reader.Close()
			writer.Close()
			client.Close()
		)
		catch
		(
			format "MCP: Client error: %\n" (getCurrentException())
			try (client.Close()) catch ()
		)
	),

	-- Timer tick: check for pending connections
	fn onTick sender args =
	(
		if MCP_Server == undefined or not MCP_Server.isRunning then return()

		try
		(
			-- Check if there's a pending connection (non-blocking)
			if MCP_Server.listener.Pending() then
			(
				client = MCP_Server.listener.AcceptTcpClient()
				MCP_Server.handleClient client
			)
		)
		catch
		(
			format "MCP: Tick error: %\n" (getCurrentException())
		)
	),

	-- Path of the JSONL registry file shared with the Python side
	fn registryPath =
	(
		local envReg = systemTools.getEnvVariable "MAXMCP_REGISTRY"
		if envReg != undefined and envReg.count > 0 then return envReg
		local sf = dotNetClass "System.Environment+SpecialFolder"
		local localAppData = ((dotNetClass "System.Environment").GetFolderPath sf.LocalApplicationData) as string
		localAppData + "\\3dsmax-mcp\\instances.jsonl"
	),

	-- Write this instance's registry line, replacing any line with our PID.
	-- Each line: {"host":...,"port":...,"pid":...,"name":...,"maxVersion":...,"lastSeen":...}
	-- host 字段写入实际绑定地址; 远程模式下 Python 端以 MAXMCP_INSTANCES 显式配置为准
	fn writeRegistryLine =
	(
		try
		(
			local p = registryPath()
			local dir = (dotNetClass "System.IO.Path").GetDirectoryName p
			if not (dotNetClass "System.IO.Directory").Exists dir do
				(dotNetClass "System.IO.Directory").CreateDirectory dir

			local pid = ((dotNetClass "System.Diagnostics.Process").GetCurrentProcess()).Id as string
			local maxYear = ((1998 + ((maxVersion())[1] / 1000)) as integer) as string
			-- Unix timestamp: ToUnixTimeSeconds() needs .NET 4.6+, so compute manually (works on 3ds Max 2015 / .NET 4.5)
			local epoch = dotNetObject "System.DateTime" 1970 1 1 0 0 0 0
			local now = (((dotNetClass "System.DateTime").UtcNow.Subtract epoch).TotalSeconds as integer) as string

			local hostStr = "127.0.0.1"
			if bindIP != undefined and (bindIP as string).count > 0 do hostStr = (bindIP as string)

			local lines = #()
			local f = openFile p
			if f != undefined then
			(
				while not eof f do
				(
					local line = readLine f
					if line.count > 0 do
					(
						local existingPid = extractJsonValue line "pid"
						if existingPid != pid do append lines line
					)
				)
				close f
			)
			append lines ("{\"host\":\"" + hostStr + "\",\"port\":" + (port as string) + \
				",\"pid\":" + pid + ",\"name\":\"max-" + (port as string) + \
				"\",\"maxVersion\":" + maxYear + ",\"lastSeen\":" + now + "}")

			local out = createFile p
			if out != undefined then
			(
				for l in lines do format "%\n" l to:out
				close out
			)
		)
		catch
		(
			format "MCP: Registry write failed: %\n" (getCurrentException())
		)
	),

	-- Remove this instance's line from the registry (called on stop)
	fn removeRegistryLine =
	(
		try
		(
			local p = registryPath()
			if not (dotNetClass "System.IO.File").Exists p then return()

			local pid = ((dotNetClass "System.Diagnostics.Process").GetCurrentProcess()).Id as string
			local lines = #()
			local f = openFile p
			if f != undefined then
			(
				while not eof f do
				(
					local line = readLine f
					if line.count > 0 do
					(
						local existingPid = extractJsonValue line "pid"
						if existingPid != pid do append lines line
					)
				)
				close f
			)

			if lines.count == 0 then
				(dotNetClass "System.IO.File").Delete p
			else
			(
				local out = createFile p
				if out != undefined then
				(
					for l in lines do format "%\n" l to:out
					close out
				)
			)
		)
		catch
		(
			format "MCP: Registry removal failed: %\n" (getCurrentException())
		)
	),

	-- Heartbeat tick: refresh the registry entry while running
	fn onHeartbeat sender args =
	(
		if MCP_Server == undefined or not MCP_Server.isRunning then return()
		MCP_Server.writeRegistryLine()
	),

	-- Start the TCP server on bindIP. Binds the first free port at or above
	-- `port` (auto-scan) so several 3ds Max instances can share a machine.
	fn start =
	(
		if not isRunning then
		(
			-- 解析绑定地址, 非法则回退到 127.0.0.1
			local bindAddr = "127.0.0.1"
			try
			(
				if bindIP != undefined and (bindIP as string).count > 0 do
				(
					local ip = (dotNetClass "System.Net.IPAddress").Parse (bindIP as string)
					bindAddr = ip.ToString()
				)
			)
			catch
			(
				format "MCP: 无效的绑定 IP '%' - 回退到 127.0.0.1\n" bindIP
				bindAddr = "127.0.0.1"
			)
			bindIP = bindAddr

			local maxAttempts = 100
			local attempt = 0
			while not isRunning and attempt < maxAttempts do
			(
				attempt += 1
				try
				(
					ipAddress = (dotNetClass "System.Net.IPAddress").Parse bindAddr
					listener = dotNetObject "System.Net.Sockets.TcpListener" ipAddress port
					listener.Start()
					isRunning = true
				)
				catch
				(
					listener = undefined
					port += 1
				)
			)

			if isRunning then
			(
				-- Create timer to poll for connections
				timer = dotNetObject "System.Windows.Forms.Timer"
				timer.Interval = 50  -- 50ms polling
				dotNet.setLifetimeControl timer #dotnet

				dotNet.addEventHandler timer "Tick" onTick
				timer.Start()

				-- Heartbeat: keep this instance visible in the registry file
				heartbeatTimer = dotNetObject "System.Windows.Forms.Timer"
				heartbeatTimer.Interval = 30000  -- 30s heartbeat
				dotNet.setLifetimeControl heartbeatTimer #dotnet

				dotNet.addEventHandler heartbeatTimer "Tick" onHeartbeat
				heartbeatTimer.Start()

				writeRegistryLine()

				format "MCP Server started (% on port %)\n" bindAddr port
			)
			else
			(
				format "MCP: Failed to find a free port after % attempts\n" maxAttempts
			)
		)
		else
		(
			format "MCP Server already running\n"
		)
	),

	-- Stop the server
	fn stop =
	(
		if isRunning then
		(
			try (timer.Stop()) catch ()
			try (dotNet.removeAllEventHandlers timer) catch ()
			try (heartbeatTimer.Stop()) catch ()
			try (dotNet.removeAllEventHandlers heartbeatTimer) catch ()
			try (listener.Stop()) catch ()
			isRunning = false
			removeRegistryLine()
			format "MCP Server stopped\n"
		)
		else
		(
			format "MCP Server is not running\n"
		)
	),

	-- Restart the server
	fn restart =
	(
		stop()
		start()
	)
)

/*
	--------------------------------------------------------------------
	场景管理结构体 (与 v1 相同)
	--------------------------------------------------------------------
*/
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
	)
)

MCP_SceneManage = SceneManageStruct()

/*
	--------------------------------------------------------------------
	控制面板 UI (dotNet WinForms)
	--------------------------------------------------------------------
*/
struct MCPUIStruct
(
	form = undefined,
	modeCombo = undefined,
	ipTextBox = undefined,
	portTextBox = undefined,
	safeCheckBox = undefined,
	closeStopCheckBox = undefined,
	startButton = undefined,
	stopButton = undefined,
	statusLabel = undefined,
	localIpLabel = undefined,
	hintLabel = undefined,
	settingMode = false,
	lastUIError = "",

	-- 枚举本机 IPv4 地址 (远程模式下供参考)
	fn getLocalIPv4 =
	(
		local ips = #()
		try
		(
			local hostName = (dotNetClass "System.Net.Dns").GetHostName()
			local addrs = (dotNetClass "System.Net.Dns").GetHostAddresses hostName
			for a in addrs do
				if (a.AddressFamily.ToString()) == "InterNetwork" do
					append ips (a.ToString())
		) catch ()
		ips
	),

	-- 刷新状态栏与按钮可用性
	fn updateStatus =
	(
		if MCP_Server != undefined and MCP_Server.isRunning then
		(
			statusLabel.Text = "状态: 运行中   " + MCP_Server.bindIP + ":" + (MCP_Server.port as string)
			startButton.Enabled = false
			stopButton.Enabled = true
			modeCombo.Enabled = false
			ipTextBox.Enabled = false
			portTextBox.Enabled = false
		)
		else
		(
			statusLabel.Text = "状态: 已停止"
			startButton.Enabled = true
			stopButton.Enabled = false
			modeCombo.Enabled = true
			ipTextBox.Enabled = true
			portTextBox.Enabled = true
		)
		updateHint()
	),

	-- 远程模式运行时给出 Python 端配置提示
	fn updateHint =
	(
		if hintLabel == undefined then return()
		if MCP_Server != undefined and MCP_Server.isRunning and modeCombo.SelectedIndex == 1 then
		(
			local ips = getLocalIPv4()
			local ipStr = if ips.count > 0 then ips[1] else ipTextBox.Text
			hintLabel.Text = "Python 端配置: MAXMCP_INSTANCES=" + ipStr + ":" + (MCP_Server.port as string)
		)
		else
			hintLabel.Text = ""
	),

	-- 模式切换: 本地 127.0.0.1 / 远程 0.0.0.0 (可自定义)
	fn onModeChanged sender args =
	(
		if settingMode then return()
		settingMode = true
		if modeCombo.SelectedIndex == 1 then
		(
			ipTextBox.Text = "0.0.0.0"
			ipTextBox.ReadOnly = false
		)
		else
		(
			ipTextBox.Text = "127.0.0.1"
			ipTextBox.ReadOnly = true
		)
		settingMode = false
		updateHint()
	),

	fn onStartClick sender args =
	(
		try (MCP_Server.bindIP = ipTextBox.Text) catch ()
		try (MCP_Server.port = (portTextBox.Text as integer)) catch (MCP_Server.port = 8765)
		MCP_Server.safeMode = safeCheckBox.Checked
		MCP_Server.start()
		updateStatus()
	),

	fn onStopClick sender args =
	(
		MCP_Server.stop()
		updateStatus()
	),

	-- 关闭窗口: 勾选"关闭时停止服务"则停止并关闭, 否则仅隐藏窗口
	fn onFormClosing sender args =
	(
		if closeStopCheckBox.Checked then
		(
			if MCP_Server != undefined and MCP_Server.isRunning do MCP_Server.stop()
			args.Cancel = false
		)
		else
		(
			args.Cancel = true
			form.Hide()
		)
	),

	-- 创建面板控件 (重复调用只创建一次)
	fn createUI =
	(
		if form != undefined then return()

		try
		(
			local font = dotNetObject "System.Drawing.Font" "Microsoft YaHei" 9.0

			form = dotNetObject "System.Windows.Forms.Form"
			form.Text = "3ds Max MCP Server v2"
			form.ClientSize = dotNetObject "System.Drawing.Size" 360 320
			form.FormBorderStyle = (dotNetClass "System.Windows.Forms.FormBorderStyle").FixedSingle
			form.MaximizeBox = false
			form.MinimizeBox = false
			form.StartPosition = (dotNetClass "System.Windows.Forms.FormStartPosition").CenterScreen
			form.Font = font

			-- 运行模式
			local lblMode = dotNetObject "System.Windows.Forms.Label"
			lblMode.Text = "运行模式:"
			lblMode.Location = dotNetObject "System.Drawing.Point" 12 16
			lblMode.Size = dotNetObject "System.Drawing.Size" 70 20
			form.Controls.Add lblMode

			modeCombo = dotNetObject "System.Windows.Forms.ComboBox"
			modeCombo.Location = dotNetObject "System.Drawing.Point" 88 13
			modeCombo.Size = dotNetObject "System.Drawing.Size" 260 24
			modeCombo.DropDownStyle = (dotNetClass "System.Windows.Forms.ComboBoxStyle").DropDownList
			modeCombo.Items.Add "本地模式 (仅本机)"
			modeCombo.Items.Add "远程模式 (局域网)"
			form.Controls.Add modeCombo

			-- 绑定 IP
			local lblIp = dotNetObject "System.Windows.Forms.Label"
			lblIp.Text = "绑定 IP:"
			lblIp.Location = dotNetObject "System.Drawing.Point" 12 50
			lblIp.Size = dotNetObject "System.Drawing.Size" 70 20
			form.Controls.Add lblIp

			ipTextBox = dotNetObject "System.Windows.Forms.TextBox"
			ipTextBox.Location = dotNetObject "System.Drawing.Point" 88 47
			ipTextBox.Size = dotNetObject "System.Drawing.Size" 260 24
			ipTextBox.Text = "127.0.0.1"
			ipTextBox.ReadOnly = true
			form.Controls.Add ipTextBox

			-- 端口
			local lblPort = dotNetObject "System.Windows.Forms.Label"
			lblPort.Text = "端口:"
			lblPort.Location = dotNetObject "System.Drawing.Point" 12 84
			lblPort.Size = dotNetObject "System.Drawing.Size" 70 20
			form.Controls.Add lblPort

			portTextBox = dotNetObject "System.Windows.Forms.TextBox"
			portTextBox.Location = dotNetObject "System.Drawing.Point" 88 81
			portTextBox.Size = dotNetObject "System.Drawing.Size" 120 24
			portTextBox.Text = "8765"
			form.Controls.Add portTextBox

			-- 安全模式
			safeCheckBox = dotNetObject "System.Windows.Forms.CheckBox"
			safeCheckBox.Text = "安全模式 (拦截危险命令)"
			safeCheckBox.Location = dotNetObject "System.Drawing.Point" 12 118
			safeCheckBox.Size = dotNetObject "System.Drawing.Size" 336 22
			safeCheckBox.Checked = true
			form.Controls.Add safeCheckBox

			-- 本机 IP 参考
			localIps = getLocalIPv4()
			local ipStr = "无"
			if localIps.count > 0 do ipStr = (join localIps ", ")
			localIpLabel = dotNetObject "System.Windows.Forms.Label"
			localIpLabel.Text = "本机 IP: " + ipStr
			localIpLabel.Location = dotNetObject "System.Drawing.Point" 12 148
			localIpLabel.Size = dotNetObject "System.Drawing.Size" 336 20
			form.Controls.Add localIpLabel

			-- 分隔线
			local sep = dotNetObject "System.Windows.Forms.Label"
			sep.Text = "-------------------------------------------"
			sep.Location = dotNetObject "System.Drawing.Point" 12 174
			sep.Size = dotNetObject "System.Drawing.Size" 336 18
			form.Controls.Add sep

			-- 状态
			statusLabel = dotNetObject "System.Windows.Forms.Label"
			statusLabel.Text = "状态: 已停止"
			statusLabel.Location = dotNetObject "System.Drawing.Point" 12 196
			statusLabel.Size = dotNetObject "System.Drawing.Size" 336 22
			form.Controls.Add statusLabel

			-- 启动 / 停止
			startButton = dotNetObject "System.Windows.Forms.Button"
			startButton.Text = "启动服务"
			startButton.Location = dotNetObject "System.Drawing.Point" 12 224
			startButton.Size = dotNetObject "System.Drawing.Size" 160 30
			form.Controls.Add startButton

			stopButton = dotNetObject "System.Windows.Forms.Button"
			stopButton.Text = "停止服务"
			stopButton.Location = dotNetObject "System.Drawing.Point" 188 224
			stopButton.Size = dotNetObject "System.Drawing.Size" 160 30
			stopButton.Enabled = false
			form.Controls.Add stopButton

			-- 关闭行为
			closeStopCheckBox = dotNetObject "System.Windows.Forms.CheckBox"
			closeStopCheckBox.Text = "关闭窗口时停止服务"
			closeStopCheckBox.Location = dotNetObject "System.Drawing.Point" 12 262
			closeStopCheckBox.Size = dotNetObject "System.Drawing.Size" 336 22
			closeStopCheckBox.Checked = true
			form.Controls.Add closeStopCheckBox

			-- 远程模式提示
			hintLabel = dotNetObject "System.Windows.Forms.Label"
			hintLabel.Text = ""
			hintLabel.Location = dotNetObject "System.Drawing.Point" 12 286
			hintLabel.Size = dotNetObject "System.Drawing.Size" 336 22
			form.Controls.Add hintLabel

			-- 事件
			dotNet.addEventHandler modeCombo "SelectedIndexChanged" onModeChanged
			dotNet.addEventHandler startButton "Click" onStartClick
			dotNet.addEventHandler stopButton "Click" onStopClick
			dotNet.addEventHandler form "FormClosing" onFormClosing

			-- 初始: 本地模式
			modeCombo.SelectedIndex = 0
		)
		catch
		(
			format "MCP UI: 创建界面失败: %\n" (getCurrentException())
		)
	),

	fn show =
	(
		createUI()
		if form == undefined then return()
		updateStatus()
		form.Show()
		form.Activate()
	)
)

/*
	--------------------------------------------------------------------
	初始化: 重建服务端 (兼容热加载) -> 本地模式启动 -> 弹出控制面板
	--------------------------------------------------------------------
*/
if MCP_Server != undefined and MCP_Server.isRunning then
(
	MCP_Server.stop()
)
MCP_Server = MCPServerStruct()

-- 允许通过 MAXMCP_PORT 环境变量覆盖默认端口
envPort = systemTools.getEnvVariable "MAXMCP_PORT"
if envPort != undefined and envPort.count > 0 then
(
	try (MCP_Server.port = envPort as integer)
	catch (format "MCP: Invalid MAXMCP_PORT '%' - using default port %\n" envPort MCP_Server.port)
)

-- 默认本地模式启动, 与 v1 行为兼容
MCP_Server.bindIP = "127.0.0.1"
MCP_Server.start()

-- 创建并显示控制面板
MCP_UI = MCPUIStruct()
MCP_UI.createUI()
MCP_UI.show()
