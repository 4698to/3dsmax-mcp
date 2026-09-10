/*
	3ds Max MCP Server - unified (TCP + native bridge selector)
	Non-blocking TCP command server for Model Context Protocol bridge.

	Transport selection (user chooses TCP vs native bridge):
	- #auto   : use the native bridge when present, else TCP fallback
	- #tcp    : always start the TCP server (even alongside a native bridge)
	- #native : never start TCP, rely on the native bridge only
	Pick it via the MCP Panel (macroScript MCP_Panel -> MCP Panel), or set
	`transport=auto|tcp|native` in %LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini.

	Usage:
		MCP_Server = MCPServerStruct()
		MCP_Server.loadConfig()      -- optional, reads mcp_config.ini
		MCP_Server.start()           -- starts per transport selection
		MCP_Server.stop()

	Protocol: JSON over TCP on port 8765 (auto-scans upward for multi-instance)
	- Client connects, sends JSON + newline
	- Server executes, returns JSON + newline, closes connection
*/

global MCP_Server
global MCP_NativePipeRunning
global MCP_SceneManage
global MCP_UI

struct MCPServerStruct
(
	timer = undefined,
	listener = undefined,
	isRunning = false,
	port = 8765,
	bindIP = "127.0.0.1",
	transport = #auto,  -- #auto | #tcp | #native
	safeMode = true,
	protocolVersion = 2,
	idlePollIntervalMs = 1500,
	activePollIntervalMs = 50,
	activePollTicksRemaining = 0,
	activePollTicksAfterRequest = 10,
	heartbeatTimer = undefined,

	fn nativeBridgeAvailable =
	(
		try
		(
			if MCP_NativePipeRunning != undefined do return MCP_NativePipeRunning
			local hwnds = windows.getChildHWND 0 "MCPBridgeExecutor"
			hwnds != undefined and hwnds.count > 0
		)
		catch
		(
			false
		)
	),

	fn configPath =
	(
		local iniPath = (dotNetClass "System.Environment").GetFolderPath (dotNetClass "System.Environment+SpecialFolder").LocalApplicationData
		iniPath += "\\3dsmax-mcp\\mcp_config.ini"
		iniPath
	),

	fn loadConfigString key defaultValue =
	(
		local val = defaultValue
		try (val = getINISetting (configPath()) "mcp" key) catch ()
		if val == "" do val = defaultValue
		val
	),

	fn loadConfigInt key defaultValue minValue maxValue =
	(
		local raw = loadConfigString key (defaultValue as string)
		local parsed = defaultValue
		try (parsed = raw as integer) catch (parsed = defaultValue)
		if parsed < minValue do parsed = minValue
		if parsed > maxValue do parsed = maxValue
		parsed
	),

	-- Load shared config from %LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
	fn loadSafeMode =
	(
		local val = loadConfigString "safe_mode" "true"
		if val == "" do val = "true"
		val = toLower (trimLeft (trimRight val))
		not (val == "false" or val == "0" or val == "off")
	),

	fn loadConfig =
	(
		safeMode = loadSafeMode()
		idlePollIntervalMs = loadConfigInt "tcp_idle_poll_interval_ms" idlePollIntervalMs 250 60000
		activePollIntervalMs = loadConfigInt "tcp_active_poll_interval_ms" activePollIntervalMs 10 5000
		activePollTicksAfterRequest = loadConfigInt "tcp_active_poll_ticks_after_request" activePollTicksAfterRequest 0 1000
		-- transport: auto (native preferred) | tcp | native
		local t = toLower (loadConfigString "transport" "auto")
		transport = case t of
		(
			"tcp": #tcp
			"native": #native
			default: #auto
		)
	),

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
	fn writeRegistryLine =
	(
		try
		(
			local p = registryPath()
			local dir = (dotNetClass "System.IO.Path").GetDirectoryName p
			if not (dotNetClass "System.IO.Directory").Exists dir do
				(dotNetClass "System.IO.Directory").CreateDirectory dir

			local pid = ((dotNetClass "System.Diagnostics.Process").GetCurrentProcess()).Id as string
			local mv = maxVersion()
			local maxYear = ((1998 + ((if mv.count != undefined then mv[1] else mv) / 1000)) as integer) as string
			local hostStr = "127.0.0.1"
			if bindIP != undefined and (bindIP as string).count > 0 do hostStr = (bindIP as string)
			-- Unix timestamp: computed manually for .NET < 4.6 (3ds Max 2015)
			local epoch = dotNetObject "System.DateTime" 1970 1 1 0 0 0 0
			local now = (((dotNetClass "System.DateTime").UtcNow.Subtract epoch).TotalSeconds as integer) as string

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
						",\"bindIP\":\"" + (escapeJsonString bindIP) + "\"" + \
						",\"maxVersion\":" + (maxYear as string) + \
						",\"renderer\":\"" + (escapeJsonString rendererName) + "\"" + \
						",\"objectCount\":" + (objects.count as string) + \
						",\"selectionCount\":" + (selection.count as string) + \
						",\"safeMode\":" + (if safeMode then "true" else "false") + \
						",\"port\":" + (port as string) + "}"
				)
				else if cmdType == "maxscript" then
				(
					if safeMode then
					(
						-- Block dangerous commands
						local lowerCmd = toLower command
						local blocked = #("doscommand", "shelllaunch", "deletefile", "python.execute", "createfile", "hiddendoscommand")
						local isBlocked = false
						for b in blocked while not isBlocked do
							if findString lowerCmd b != undefined do isBlocked = true
						if isBlocked then
						(
							success = false
							errorMsg = "Blocked by safe mode. Set safe_mode=false in %LOCALAPPDATA%\\3dsmax-mcp\\mcp_config.ini to disable."
						)
						else
							result = execute command
					)
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

	fn setPollInterval intervalMs =
	(
		if timer != undefined and timer.Interval != intervalMs do timer.Interval = intervalMs
	),

	fn armActivePolling =
	(
		activePollTicksRemaining = activePollTicksAfterRequest
		setPollInterval activePollIntervalMs
	),

	fn decayPolling =
	(
		if activePollTicksRemaining > 0 then
		(
			activePollTicksRemaining -= 1
			if activePollTicksRemaining <= 0 do setPollInterval idlePollIntervalMs
		)
		else
		(
			setPollInterval idlePollIntervalMs
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
				MCP_Server.armActivePolling()
				client = MCP_Server.listener.AcceptTcpClient()
				MCP_Server.handleClient client
			)
			else
			(
				MCP_Server.decayPolling()
			)
		)
		catch
		(
			format "MCP: Tick error: %\n" (getCurrentException())
		)
	),

	-- Start the TCP server. Transport selection (user choice):
	--   #auto   -> use the native bridge when present, else TCP fallback
	--   #tcp    -> always start TCP (even when a native bridge exists)
	--   #native -> never start TCP, rely on the native bridge only
	fn start =
	(
		if not isRunning then
		(
			if transport == #native then
			(
				format "MCP: transport = native bridge; TCP fallback not started\n"
				return #native_only
			)
			if transport == #auto and nativeBridgeAvailable() then
			(
				format "MCP: transport = auto and native bridge detected; using native pipe (TCP not started)\n"
				return #native_only
			)

			-- Resolve bind address; fall back to 127.0.0.1 if invalid
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
				format "MCP: invalid bind IP '%' - fallback to 127.0.0.1\n" bindIP
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
					-- Create TCP listener
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
				-- Create a sparse idle timer for the opt-in TCP fallback.
				timer = dotNetObject "System.Windows.Forms.Timer"
				timer.Interval = idlePollIntervalMs
				dotNet.setLifetimeControl timer #dotnet

				dotNet.addEventHandler timer "Tick" this.onTick
				timer.Start()

				-- Heartbeat: keep this instance visible in the registry file
				heartbeatTimer = dotNetObject "System.Windows.Forms.Timer"
				heartbeatTimer.Interval = 30000  -- 30s heartbeat
				dotNet.setLifetimeControl heartbeatTimer #dotnet

				dotNet.addEventHandler heartbeatTimer "Tick" this.onHeartbeat
				heartbeatTimer.Start()

				writeRegistryLine()

				format "MCP Server started (% on port %, idle poll % ms, active poll % ms)\n" bindAddr port idlePollIntervalMs activePollIntervalMs
				#started
			)
			else
			(
				format "MCP: Failed to find a free port after % attempts\n" maxAttempts
				#failed
			)
		)
		else
		(
			format "MCP Server already running\n"
			#already_running
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

-- Define helpers; do NOT auto-start.
--
-- Start TCP or pick the transport via `MCP Panel` (macroscript MCP_Panel,
-- Customize UI -> MCP -> MCP Panel), or `MCP Start` for a quick start using
-- the configured transport (auto | tcp | native). `MCP Stop` shuts the TCP
-- fallback back down. Keep native bridge as the default path for normal
-- interactive use.
if MCP_Server != undefined and MCP_Server.isRunning then
(
	MCP_Server.stop()
)
MCP_Server = MCPServerStruct()
MCP_Server.loadConfig()

-- 允许通过 MAXMCP_PORT 环境变量覆盖默认端口 (v2 兼容)
envPort = systemTools.getEnvVariable "MAXMCP_PORT"
if envPort != undefined and envPort.count > 0 then
(
	try (MCP_Server.port = envPort as integer)
	catch (format "MCP: Invalid MAXMCP_PORT '%' - using default port %\n" envPort MCP_Server.port)
)

(
	local transportName = case MCP_Server.transport of (#tcp:"tcp"; #native:"native"; default:"auto")
	local nativeAvailable = MCP_Server.nativeBridgeAvailable()
	format "MCP: helpers loaded (safe_mode = %, transport = %, native bridge = %, tcp idle poll = % ms)\n" MCP_Server.safeMode transportName nativeAvailable MCP_Server.idlePollIntervalMs
	format "MCP Commit :\n	MCP_Server.start()\n	MCP_Server.stop()\n	MCP_UI.show()\n"  
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
控制面板 (v2 移植 + 传输方式选择): 用户可自行选择 TCP / native bridge
--------------------------------------------------------------------
*/
-- UI 事件经全局函数中转: struct 方法直接传给 dotNet.addEventHandler 在个别
-- 3ds Max 版本 / 启动方式下事件可能不触发, 全局函数值做委托转换最可靠
global mcpUI_onModeChanged,mcpUI_onStartClick,mcpUI_onStopClick,mcpUI_onFormClosing
fn mcpUI_onModeChanged sender args = (if MCP_UI != undefined do MCP_UI.onModeChanged sender args)
fn mcpUI_onStartClick sender args = (if MCP_UI != undefined do MCP_UI.onStartClick sender args)
fn mcpUI_onStopClick sender args = (if MCP_UI != undefined do MCP_UI.onStopClick sender args)
fn mcpUI_onFormClosing sender args = (if MCP_UI != undefined do MCP_UI.onFormClosing sender args)
struct MCPUIStruct
(
	form = undefined,
	transportCombo = undefined,
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
	ownerWin = undefined,

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
		if this.statusLabel == undefined or this.startButton == undefined or this.stopButton == undefined then return()
		if MCP_Server != undefined and MCP_Server.isRunning then
		(
			this.statusLabel.Text = "状态: 运行中   " + MCP_Server.bindIP + ":" + (MCP_Server.port as string)
			this.startButton.Enabled = false
			this.stopButton.Enabled = true
			this.transportCombo.Enabled = false
			this.modeCombo.Enabled = false
			this.ipTextBox.Enabled = false
			this.portTextBox.Enabled = false
		)
		else
		(
			this.statusLabel.Text = "状态: 已停止"
			this.startButton.Enabled = true
			this.stopButton.Enabled = false
			this.transportCombo.Enabled = true
			this.modeCombo.Enabled = true
			this.ipTextBox.Enabled = true
			this.portTextBox.Enabled = true
		)
		this.updateHint()
	),

	-- 远程模式运行时给出 Python 端配置提示
	fn updateHint =
	(
		if this.hintLabel == undefined then return()
		if MCP_Server != undefined and MCP_Server.isRunning and this.modeCombo.SelectedIndex == 1 then
		(
			local ips = this.getLocalIPv4()
			local ipStr = if ips.count > 0 then ips[1] else this.ipTextBox.Text
			this.hintLabel.Text = "Python 端配置: MAXMCP_INSTANCES=" + ipStr + ":" + (MCP_Server.port as string)
		)
		else
			this.hintLabel.Text = ""
	),

	-- 模式切换: 本地 127.0.0.1 / 远程 0.0.0.0 (可自定义)
	fn onModeChanged sender args =
	(
		if this.settingMode then return()
		this.settingMode = true
		if this.modeCombo.SelectedIndex == 1 then
		(
			this.ipTextBox.Text = "0.0.0.0"
			this.ipTextBox.ReadOnly = false
		)
		else
		(
			this.ipTextBox.Text = "127.0.0.1"
			this.ipTextBox.ReadOnly = true
		)
		this.settingMode = false
		this.updateHint()
	),

	fn onStartClick sender args =
	(
		--format "MCP Server: onStartClick\n"
		try (MCP_Server.bindIP = this.ipTextBox.Text) catch ()
		try (MCP_Server.port = (this.portTextBox.Text as integer)) catch (MCP_Server.port = 8765)
		MCP_Server.safeMode = this.safeCheckBox.Checked
		MCP_Server.transport = case this.transportCombo.SelectedIndex of
		(
			1: #tcp
			2: #native
			default: #auto
		)
		local result = #failed
		try
		(
			result = MCP_Server.start()
		)
		catch
		(
			format "MCP UI: 启动失败: %\n" (getCurrentException())
		)
		case result of
		(
			#native_only:
			(
				-- 仅 Native Bridge: 不监听 TCP, 给出可见反馈而非看起来"没反应";
				-- 保持"启动"可用, 便于改传输方式后再次启动
				this.statusLabel.Text = "状态: 已启动 (Native Bridge, 未监听 TCP)"
				this.startButton.Enabled = true
				this.stopButton.Enabled = false
			)
			default: this.updateStatus()
		)
	),

	fn onStopClick sender args =
	(
		MCP_Server.stop()
		this.updateStatus()
	),

	-- 关闭窗口: 勾选"关闭时停止服务"则停止并关闭, 否则仅隐藏窗口
	fn onFormClosing sender args =
	(
		if this.closeStopCheckBox.Checked then
		(
			if MCP_Server != undefined and MCP_Server.isRunning do MCP_Server.stop()
			args.Cancel = false
			-- 窗体即将销毁(disposed): 清空引用, 下次 show() 重新创建
			this.clearControls()
		)
		else
		(
			args.Cancel = true
			this.form.Hide()
		)
	),

	-- 清空全部控件引用: 窗体已销毁 / 创建失败后重置, 保证下次 show() 完整重建
	fn clearControls =
	(
		this.form = undefined
		this.transportCombo = undefined
		this.modeCombo = undefined
		this.ipTextBox = undefined
		this.portTextBox = undefined
		this.safeCheckBox = undefined
		this.closeStopCheckBox = undefined
		this.startButton = undefined
		this.stopButton = undefined
		this.statusLabel = undefined
		this.localIpLabel = undefined
		this.hintLabel = undefined
	),

	-- 创建面板控件 (重复调用只创建一次)
	fn createUI =
	(
		if this.form != undefined then return()

		try
		(
			local font = dotNetObject "System.Drawing.Font" "Microsoft YaHei" 9.0

			this.form = dotNetObject "System.Windows.Forms.Form"
			this.form.Text = "3ds Max MCP Server"
			this.form.ClientSize = dotNetObject "System.Drawing.Size" 360 360
			this.form.FormBorderStyle = (dotNetClass "System.Windows.Forms.FormBorderStyle").FixedSingle
			this.form.MaximizeBox = false
			this.form.MinimizeBox = false
			this.form.StartPosition = (dotNetClass "System.Windows.Forms.FormStartPosition").CenterScreen
			this.form.Font = font

			-- 传输方式
			local lblTransport = dotNetObject "System.Windows.Forms.Label"
			lblTransport.Text = "传输方式:"
			lblTransport.Location = dotNetObject "System.Drawing.Point" 12 16
			lblTransport.Size = dotNetObject "System.Drawing.Size" 70 20
			this.form.Controls.Add lblTransport

			this.transportCombo = dotNetObject "System.Windows.Forms.ComboBox"
			this.transportCombo.Location = dotNetObject "System.Drawing.Point" 88 13
			this.transportCombo.Size = dotNetObject "System.Drawing.Size" 260 24
			this.transportCombo.DropDownStyle = (dotNetClass "System.Windows.Forms.ComboBoxStyle").DropDownList
			this.transportCombo.Items.Add "自动 (Native 优先)"
			this.transportCombo.Items.Add "仅 TCP (强制 TCP)"
			this.transportCombo.Items.Add "仅 Native Bridge (禁用 TCP)"
			this.form.Controls.Add this.transportCombo

			-- 运行模式
			local lblMode = dotNetObject "System.Windows.Forms.Label"
			lblMode.Text = "运行模式:"
			lblMode.Location = dotNetObject "System.Drawing.Point" 12 50
			lblMode.Size = dotNetObject "System.Drawing.Size" 70 20
			this.form.Controls.Add lblMode

			this.modeCombo = dotNetObject "System.Windows.Forms.ComboBox"
			this.modeCombo.Location = dotNetObject "System.Drawing.Point" 88 47
			this.modeCombo.Size = dotNetObject "System.Drawing.Size" 260 24
			this.modeCombo.DropDownStyle = (dotNetClass "System.Windows.Forms.ComboBoxStyle").DropDownList
			this.modeCombo.Items.Add "本地模式 (仅本机)"
			this.modeCombo.Items.Add "远程模式 (局域网)"
			this.form.Controls.Add this.modeCombo

			-- 绑定 IP
			local lblIp = dotNetObject "System.Windows.Forms.Label"
			lblIp.Text = "绑定 IP:"
			lblIp.Location = dotNetObject "System.Drawing.Point" 12 84
			lblIp.Size = dotNetObject "System.Drawing.Size" 70 20
			this.form.Controls.Add lblIp

			this.ipTextBox = dotNetObject "System.Windows.Forms.TextBox"
			this.ipTextBox.Location = dotNetObject "System.Drawing.Point" 88 81
			this.ipTextBox.Size = dotNetObject "System.Drawing.Size" 260 24
			this.ipTextBox.Text = "127.0.0.1"
			this.ipTextBox.ReadOnly = true
			this.form.Controls.Add this.ipTextBox

			-- 端口
			local lblPort = dotNetObject "System.Windows.Forms.Label"
			lblPort.Text = "端口:"
			lblPort.Location = dotNetObject "System.Drawing.Point" 12 118
			lblPort.Size = dotNetObject "System.Drawing.Size" 70 20
			this.form.Controls.Add lblPort

			this.portTextBox = dotNetObject "System.Windows.Forms.TextBox"
			this.portTextBox.Location = dotNetObject "System.Drawing.Point" 88 115
			this.portTextBox.Size = dotNetObject "System.Drawing.Size" 120 24
			this.portTextBox.Text = "8765"
			this.form.Controls.Add this.portTextBox

			-- 安全模式
			this.safeCheckBox = dotNetObject "System.Windows.Forms.CheckBox"
			this.safeCheckBox.Text = "安全模式 (拦截危险命令)"
			this.safeCheckBox.Location = dotNetObject "System.Drawing.Point" 12 152
			this.safeCheckBox.Size = dotNetObject "System.Drawing.Size" 336 22
			this.safeCheckBox.Checked = true
			this.form.Controls.Add this.safeCheckBox

			-- 本机 IP 参考
			local localIps = this.getLocalIPv4()
			local ipStr = "无"
			if localIps.count > 0 do
			(
				ipStr = localIps[1]
				for i = 2 to localIps.count do ipStr = ipStr + ", " + localIps[i]
			)
			this.localIpLabel = dotNetObject "System.Windows.Forms.Label"
			this.localIpLabel.Text = "本机 IP: " + ipStr
			this.localIpLabel.Location = dotNetObject "System.Drawing.Point" 12 182
			this.localIpLabel.Size = dotNetObject "System.Drawing.Size" 336 20
			this.form.Controls.Add this.localIpLabel

			-- 分隔线
			local sep = dotNetObject "System.Windows.Forms.Label"
			sep.Text = "-------------------------------------------"
			sep.Location = dotNetObject "System.Drawing.Point" 12 208
			sep.Size = dotNetObject "System.Drawing.Size" 336 18
			this.form.Controls.Add sep

			-- 状态
			this.statusLabel = dotNetObject "System.Windows.Forms.Label"
			this.statusLabel.Text = "状态: 已停止"
			this.statusLabel.Location = dotNetObject "System.Drawing.Point" 12 230
			this.statusLabel.Size = dotNetObject "System.Drawing.Size" 336 22
			this.form.Controls.Add this.statusLabel

			-- 启动 / 停止
			this.startButton = dotNetObject "System.Windows.Forms.Button"
			this.startButton.Text = "启动服务"
			this.startButton.Location = dotNetObject "System.Drawing.Point" 12 258
			this.startButton.Size = dotNetObject "System.Drawing.Size" 160 30
			this.form.Controls.Add this.startButton

			this.stopButton = dotNetObject "System.Windows.Forms.Button"
			this.stopButton.Text = "停止服务"
			this.stopButton.Location = dotNetObject "System.Drawing.Point" 188 258
			this.stopButton.Size = dotNetObject "System.Drawing.Size" 160 30
			this.stopButton.Enabled = false
			this.form.Controls.Add this.stopButton

			-- 关闭行为
			this.closeStopCheckBox = dotNetObject "System.Windows.Forms.CheckBox"
			this.closeStopCheckBox.Text = "关闭窗口时停止服务"
			this.closeStopCheckBox.Location = dotNetObject "System.Drawing.Point" 12 296
			this.closeStopCheckBox.Size = dotNetObject "System.Drawing.Size" 336 22
			this.closeStopCheckBox.Checked = true
			this.form.Controls.Add this.closeStopCheckBox

			-- 远程模式提示
			this.hintLabel = dotNetObject "System.Windows.Forms.Label"
			this.hintLabel.Text = ""
			this.hintLabel.Location = dotNetObject "System.Drawing.Point" 12 326
			this.hintLabel.Size = dotNetObject "System.Drawing.Size" 336 22
			this.form.Controls.Add this.hintLabel

			-- 事件: 经全局派发函数中转, 避免 struct 方法直接作为 .NET 事件处理器
			-- 在个别 3ds Max 版本 / 启动方式下 Click 等事件不触发的问题
			dotNet.addEventHandler this.modeCombo "SelectedIndexChanged" mcpUI_onModeChanged
			dotNet.addEventHandler this.startButton "Click" mcpUI_onStartClick
			dotNet.addEventHandler this.stopButton "Click" mcpUI_onStopClick
			dotNet.addEventHandler this.form "FormClosing" mcpUI_onFormClosing

			-- 初始: 传输方式与当前配置一致, 本地模式
			this.transportCombo.SelectedIndex = case (if MCP_Server != undefined then MCP_Server.transport else #auto) of
			(
				#tcp: 1
				#native: 2
				default: 0
			)
			this.modeCombo.SelectedIndex = 0
		)
		catch
		(
			format "MCP UI: 创建界面失败: %\n" (getCurrentException())
			-- 重置全部控件, 下次 show() 会重新创建, 避免半初始化残留导致二次报错
			this.clearControls()
		)
	),

	fn show =
	(
		-- 窗体若已被销毁(disposed), 先清空引用, 让 createUI 重建
		if this.form != undefined and this.form.IsDisposed do this.clearControls()
		this.createUI()
		if this.form == undefined then return()
		this.updateStatus()
		-- 以 3ds Max 主窗口为父级: 面板随 Max 置顶, 最小化时同步隐藏, 不占独立任务栏
		try
		(
			local hwnd = windows.getMAXHWND()
			if hwnd != 0 then
			(
				if this.ownerWin == undefined do
				(
					this.ownerWin = dotNetObject "System.Windows.Forms.NativeWindow"
					this.ownerWin.AssignHandle (dotNetObject "System.IntPtr" hwnd)
				)
				this.form.Show this.ownerWin
			)
			else
				this.form.Show()
		)
		catch
		(
			this.form.Show()
		)
		this.form.Activate()
	)
)
MCP_UI = MCPUIStruct()

macroScript MCP_Claim_This_Max category:"MCP" tooltip:"Make this Max the active MCP target" buttonText:"MCP Claim This Max"
(
	on execute do
	(
		try
		(
			local env = dotNetClass "System.Environment"
			local specialFolder = dotNetClass "System.Environment+SpecialFolder"
			local directory = dotNetClass "System.IO.Directory"
			local file = dotNetClass "System.IO.File"
			local process = dotNetClass "System.Diagnostics.Process"
			local localAppData = env.GetFolderPath specialFolder.LocalApplicationData
			local configDir = localAppData + "\\3dsmax-mcp"
			local instancesDir = configDir + "\\instances"
			directory.CreateDirectory configDir
			directory.CreateDirectory instancesDir

			local pid = (process.GetCurrentProcess()).Id
			local instanceId = "pid-" + (pid as string)
			local pipe = "\\\\.\\pipe\\3dsmax-mcp-" + instanceId
			local pipeJson = substituteString pipe "\\" "\\\\"
			local json = "{\n  \"instance_id\": \"" + instanceId + "\",\n  \"pid\": " + (pid as string) + ",\n  \"pipe\": \"" + pipeJson + "\"\n}\n"

			file.WriteAllText (instancesDir + "\\" + instanceId + ".json") json
			file.WriteAllText (configDir + "\\active_instance.json") json
			format "MCP: this 3ds Max instance is now the active MCP target (%)\n" instanceId
		)
		catch
		(
			format "MCP: failed to claim this Max: %\n" (getCurrentException())
		)
	)
)

macroScript MCP_Start category:"MCP" tooltip:"Start MCP server (transport: auto/tcp/native)" buttonText:"MCP Start"
(
	on execute do
	(
		if MCP_Server == undefined then
		(
			format "MCP: helpers not loaded; restart Max or fileIn mcp_server.ms\n"
		)
		else
		(
			-- transport 决策在 start() 内部处理:
			-- #auto -> native bridge 可用则用桥, 否则 TCP; #tcp -> 强制 TCP; #native -> 只用桥
			MCP_Server.start()
		)
	)
)

macroScript MCP_Panel category:"MCP" tooltip:"Open MCP control panel (choose TCP / native bridge)" buttonText:"MCP Panel"
(
	on execute do
	(
		if MCP_UI == undefined then
		(
			format "MCP: UI not initialized; restart Max or fileIn mcp_server.ms\n"
		)
		else
		(
			MCP_UI.show()
		)
	)
)

macroScript MCP_Stop category:"MCP" tooltip:"Stop MCP TCP fallback" buttonText:"MCP Stop"
(
	on execute do
	(
		if MCP_Server != undefined and MCP_Server.isRunning then MCP_Server.stop()
		else format "MCP: TCP fallback not running\n"
	)
)
