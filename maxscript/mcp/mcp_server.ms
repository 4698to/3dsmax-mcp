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
global MCP_SkinManage
global MCP_UI

-- .NET 事件经全局函数中转: struct 方法直接传给 dotNet.addEventHandler 在个别
-- 3ds Max 版本 / 启动方式下事件可能不触发(委托易被 GC 回收), 全局函数委托最可靠
global mcpServer_onTick, mcpServer_onHeartbeat
-- Do not use isKindOf here: after fileIn redefines MCPServerStruct, old
-- instances fail isKindOf and Tick would go silent or reload would skip stop().
-- Probe a known field; HWND Pointers / wrong values throw and count as dead.
fn mcpServer_isLive =
(
	try (MCP_Server != undefined and MCP_Server.isRunning != undefined) catch false
)
fn mcpServer_onTick sender args =
(
	if mcpServer_isLive() do MCP_Server.onTick sender args
)
fn mcpServer_onHeartbeat sender args =
(
	if mcpServer_isLive() do MCP_Server.onHeartbeat sender args
)

struct MCPServerStruct
(
	timer = undefined,
	tcpListener = undefined,
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
	pendingCheckErrorLogged = false,

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
		try (val = getINISetting (this.configPath()) "mcp" key) catch ()
		if val == "" do val = defaultValue
		val
	),

	fn loadConfigInt key defaultValue minValue maxValue =
	(
		local raw = this.loadConfigString key (defaultValue as string)
		local parsed = defaultValue
		try (parsed = raw as integer) catch (parsed = defaultValue)
		if parsed < minValue do parsed = minValue
		if parsed > maxValue do parsed = maxValue
		parsed
	),

	-- Load shared config from %LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini
	fn loadSafeMode =
	(
		local val = this.loadConfigString "safe_mode" "true"
		if val == "" do val = "true"
		val = toLower (trimLeft (trimRight val))
		not (val == "false" or val == "0" or val == "off")
	),

	fn loadConfig =
	(
		this.safeMode = this.loadSafeMode()
		this.idlePollIntervalMs = this.loadConfigInt "tcp_idle_poll_interval_ms" this.idlePollIntervalMs 250 60000
		this.activePollIntervalMs = this.loadConfigInt "tcp_active_poll_interval_ms" this.activePollIntervalMs 10 5000
		this.activePollTicksAfterRequest = this.loadConfigInt "tcp_active_poll_ticks_after_request" this.activePollTicksAfterRequest 0 1000
		-- transport: auto (native preferred) | tcp | native
		local t = toLower (this.loadConfigString "transport" "auto")
		this.transport = case t of
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
		local safeResult = this.escapeJsonString (if result != undefined then (result as string) else "")
		local safeError = this.escapeJsonString errorMsg
		local successStr = if success then "true" else "false"
		local safeReqId = this.escapeJsonString requestId
		local safeCmdType = this.escapeJsonString cmdType
		local reqField = if requestId != "" then ("\"" + safeReqId + "\"") else "null"
		local durationMs = amax #(0, (timeStamp() - startedAt))

		"{" + \
			"\"success\":" + successStr + "," + \
			"\"requestId\":" + reqField + "," + \
			"\"result\":\"" + safeResult + "\"," + \
			"\"error\":\"" + safeError + "\"," + \
			"\"meta\":{" + \
				"\"protocolVersion\":" + (this.protocolVersion as string) + "," + \
				"\"cmdType\":\"" + safeCmdType + "\"," + \
				"\"safeMode\":" + (if this.safeMode then "true" else "false") + "," + \
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
						local codepoint = this.hexToInt hexStr
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
			local p = this.registryPath()
			local dir = (dotNetClass "System.IO.Path").GetDirectoryName p
			if not (dotNetClass "System.IO.Directory").Exists dir do
				(dotNetClass "System.IO.Directory").CreateDirectory dir

			local pid = ((dotNetClass "System.Diagnostics.Process").GetCurrentProcess()).Id as string
			local mv = maxVersion()
			local maxYear = ((1998 + ((if mv.count != undefined then mv[1] else mv) / 1000)) as integer) as string
			local hostStr = "127.0.0.1"
			if this.bindIP != undefined and (this.bindIP as string).count > 0 do hostStr = (this.bindIP as string)
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
						local existingPid = this.extractJsonValue line "pid"
						if existingPid != pid do append lines line
					)
				)
				close f
			)
			append lines ("{\"host\":\"" + hostStr + "\",\"port\":" + (MCP_Server.port as string) + \
				",\"pid\":" + pid + ",\"name\":\"max-" + (MCP_Server.port as string) + \
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
			local p = this.registryPath()
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
						local existingPid = this.extractJsonValue line "pid"
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
			local command = this.extractJsonValue jsonString "command"
			local cmdType = this.extractJsonValue jsonString "type"
			local requestId = this.extractJsonValue jsonString "requestId"

			if cmdType == "" do cmdType = "maxscript"

			if cmdType != "ping" and command == "" then
			(
				return (this.buildResponse false undefined "Empty command" requestId:requestId startedAt:startedAt cmdType:cmdType)
			)

			local result = undefined
			local success = true
			local errorMsg = ""
			local savedSafeMode = this.safeMode

			try
			(
				if cmdType == "ping" then
				(
					local maxYear = ((1998 + ((maxVersion())[1] / 1000)) as integer)
					local rendererName = try ((classOf renderers.current) as string) catch "unknown"
					result = "{\"pong\":true" + \
						",\"server\":\"3dsmax-mcp\"" + \
						",\"protocolVersion\":" + (this.protocolVersion as string) + \
						",\"bindIP\":\"" + (this.escapeJsonString this.bindIP) + "\"" + \
						",\"maxVersion\":" + (maxYear as string) + \
						",\"renderer\":\"" + (this.escapeJsonString rendererName) + "\"" + \
						",\"objectCount\":" + (objects.count as string) + \
						",\"selectionCount\":" + (selection.count as string) + \
						",\"safeMode\":" + (if this.safeMode then "true" else "false") + \
						",\"port\":" + (MCP_Server.port as string) + "}"
				)
				else if cmdType == "maxscript" then
				(
					if this.safeMode then
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
					if this.safeMode then
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
			this.safeMode = savedSafeMode

			response = this.buildResponse success result errorMsg requestId:requestId startedAt:startedAt cmdType:cmdType
		)
		catch
		(
			response = this.buildResponse false undefined ("Parse error: " + (getCurrentException())) startedAt:startedAt
		)

		response
	),

	-- Max 2015/2016 often resolve Write/WriteLine(MAXScript string) to the
	-- char overload (only the first character is sent). Writing one character at
	-- a time makes that overload correct by construction.
	-- Do NOT AutoFlush per character: Python closes as soon as it sees '\n',
	-- and a slow per-char flush races into "connection aborted by the host".
	fn writeResponseLine stream text =
	(
		local s = if text != undefined then (text as string) else ""
		-- UTF8Encoding(false) = no BOM. Default Encoding.UTF8 writes a BOM that
		-- breaks JSON parsers on the Python side.
		local enc = dotNetObject "System.Text.UTF8Encoding" false
		-- Prefer leaveOpen=true so disposing the writer does not tear down the
		-- NetworkStream. Fall back if the 4-arg ctor is unavailable.
		local writer = undefined
		try (writer = dotNetObject "System.IO.StreamWriter" stream enc 1024 true) catch ()
		if writer == undefined do writer = dotNetObject "System.IO.StreamWriter" stream enc
		writer.AutoFlush = false
		for i = 1 to s.count do writer.Write (substring s i 1)
		writer.Write ((dotNetClass "System.Environment").NewLine)
		writer.Flush()
		try (stream.Flush()) catch ()
		try (writer.Dispose()) catch ()
	),

	fn isBenignSocketAbort errText =
	(
		local e = if errText != undefined then (errText as string) else ""
		(findString e "connection was aborted") != undefined or \
			(findString e "Connection was aborted") != undefined or \
			(findString e "forcibly closed") != undefined or \
			(findString e "Connection reset") != undefined
	),

	-- Handle a single client connection
	fn handleClient client =
	(
		try
		(
			stream = client.GetStream()
			-- Prevent permanent wedging: a dead/stuck client must not block
			-- the poll loop (and the whole MAXScript main thread) forever.
			try (stream.ReadTimeout = 10000) catch ()
			reader = dotNetObject "System.IO.StreamReader" stream (dotNetClass "System.Text.Encoding").UTF8

			-- Read the request line
			requestLine = reader.ReadLine()

			-- Convert .NET string to MAXScript string
			requestStr = if requestLine != undefined then (requestLine as string) else ""

			if requestStr.count > 0 then
			(
				-- Background online probes use requestId "probe"; keep Listener quiet.
				local isProbe = (findString requestStr "\"requestId\":\"probe\"") != undefined or \
					(findString requestStr "\"requestId\": \"probe\"") != undefined
				if not isProbe do format "MCP: Received request: %\n" requestStr
				responseStr = this.processRequest requestStr
				if not isProbe do format "MCP: Sending response: %\n" responseStr
				try
				(
					this.writeResponseLine stream responseStr
				)
				catch
				(
					-- Client often closes right after reading the JSON line (probes /
					-- short timeouts). That abort on the final flush is benign.
					local err = getCurrentException()
					if not (isProbe and (this.isBenignSocketAbort err)) do \
						format "MCP: Write response failed: %\n" err
				)
			)
			else
			(
				-- Peer connected and closed with no line (old TCP probes / port
				-- scanners). Stay quiet — do not spam the Listener.
			)

			try (reader.Close()) catch ()
			try (client.Close()) catch ()
		)
		catch
		(
			format "MCP: Client error: %\n" (getCurrentException())
			try (client.Close()) catch ()
		)
	),

	fn setPollInterval intervalMs =
	(
		if this.timer != undefined and this.timer.Interval != intervalMs do this.timer.Interval = intervalMs
	),

	fn armActivePolling =
	(
		this.activePollTicksRemaining = this.activePollTicksAfterRequest
		this.setPollInterval this.activePollIntervalMs
	),

	fn decayPolling =
	(
		if this.activePollTicksRemaining > 0 then
		(
			this.activePollTicksRemaining -= 1
			if this.activePollTicksRemaining <= 0 do this.setPollInterval this.idlePollIntervalMs
		)
		else
		(
			this.setPollInterval this.idlePollIntervalMs
		)
	),

	-- Call a parameterless instance method via Reflection.
	-- Max 2015's `dotNet` struct has no `invokeMethod`. Direct calls like
	-- `tcpListener.Pending()` also fail on some Chinese Max builds (IME name clash).
	fn invokeNetMethod obj methodName =
	(
		local mi = (obj.GetType()).GetMethod methodName
		if mi == undefined do throw ("Method not found: " + methodName)
		mi.Invoke obj undefined
	),

	-- Non-blocking pending-connection check.
	fn hasPending =
	(
		if MCP_Server.tcpListener == undefined do (return false)
		try
		(
			--local result = MCP_Server.invokeNetMethod MCP_Server.tcpListener "Pending"
			local result = MCP_Server.tcpListener.Pending()
			return result
		)
		catch
		(
			if MCP_Server.pendingCheckErrorLogged != true then
			(
				format "MCP: TcpListener Pending check failed: %\n" (getCurrentException())
				MCP_Server.pendingCheckErrorLogged = true
			)
			false
		)
	),

	-- Timer tick: check for pending connections
	fn onTick sender args =
	(
		if MCP_Server == undefined or not MCP_Server.isRunning then return()
		if MCP_Server.tcpListener == undefined then return()

		try
		(
			if MCP_Server.hasPending() then
			(
				MCP_Server.armActivePolling()
				local client = MCP_Server.invokeNetMethod MCP_Server.tcpListener "AcceptTcpClient"
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
		if not MCP_Server.isRunning then
		(
			if this.transport == #native then
			(
				format "MCP: transport = native bridge; TCP fallback not started\n"
				return #native_only
			)
			if this.transport == #auto and this.nativeBridgeAvailable() then
			(
				format "MCP: transport = auto and native bridge detected; using native pipe (TCP not started)\n"
				return #native_only
			)

			-- Resolve bind address; fall back to 127.0.0.1 if invalid
			local bindAddr = "127.0.0.1"
			try
			(
				if MCP_Server.bindIP != undefined and (MCP_Server.bindIP as string).count > 0 do
				(
					local ip = (dotNetClass "System.Net.IPAddress").Parse (MCP_Server.bindIP as string)
					bindAddr = ip.ToString()
				)
			)
			catch
			(
				format "MCP: invalid bind IP '%' - fallback to 127.0.0.1\n" MCP_Server.bindIP
				bindAddr = "127.0.0.1"
			)
			MCP_Server.bindIP = bindAddr

			local maxAttempts = 100
			local attempt = 0
			while not MCP_Server.isRunning and attempt < maxAttempts do
			(
				attempt += 1
				try
				(
					-- Create the TCP listener with the single-arg (IPEndPoint)
					-- constructor: IPEndPoint construction is verified working in
					-- Max 2015, and one untyped argument resolves unambiguously.
					-- (Direct multi-arg construction and Object[] indexing both
					-- fail on the 2015 bridge, so avoid both.)
					local ipAddress = (dotNetClass "System.Net.IPAddress").Parse bindAddr
					local ipEndPoint = dotNetObject "System.Net.IPEndPoint" ipAddress MCP_Server.port
					MCP_Server.tcpListener = dotNetObject "System.Net.Sockets.TcpListener" ipEndPoint
					dotNet.setLifetimeControl MCP_Server.tcpListener #mxs  -- keep listener alive; #dotnet would let GC close the socket
					MCP_Server.tcpListener.Start()
					MCP_Server.isRunning = true
					MCP_Server.pendingCheckErrorLogged = false
				)
				catch
				(
					format "MCP: listener create failed (attempt %): %\n" attempt (getCurrentException())
					try (MCP_Server.tcpListener.Stop()) catch ()
					MCP_Server.tcpListener = undefined
					MCP_Server.port += 1
				)
			)

			if MCP_Server.isRunning then
			(
				-- Create a sparse idle timer for the opt-in TCP fallback.
				this.timer = dotNetObject "System.Windows.Forms.Timer"
				this.timer.Interval = this.idlePollIntervalMs
				dotNet.setLifetimeControl this.timer #dotnet

				dotNet.addEventHandler this.timer "Tick" mcpServer_onTick
				this.timer.Start()

				-- Heartbeat: keep this instance visible in the registry file
				this.heartbeatTimer = dotNetObject "System.Windows.Forms.Timer"
				this.heartbeatTimer.Interval = 30000  -- 30s heartbeat
				dotNet.setLifetimeControl this.heartbeatTimer #dotnet

				dotNet.addEventHandler this.heartbeatTimer "Tick" mcpServer_onHeartbeat
				this.heartbeatTimer.Start()

				MCP_Server.writeRegistryLine()

				format "MCP Server started (% on port %, idle poll % ms, active poll % ms)\n" bindAddr MCP_Server.port this.idlePollIntervalMs this.activePollIntervalMs
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
		if MCP_Server.isRunning then
		(
			try (this.timer.Stop()) catch ()
			try (dotNet.removeAllEventHandlers this.timer) catch ()
			try (this.heartbeatTimer.Stop()) catch ()
			try (dotNet.removeAllEventHandlers this.heartbeatTimer) catch ()
			try (this.tcpListener.Stop()) catch ()
			MCP_Server.isRunning = false
			MCP_Server.removeRegistryLine()
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
		this.stop()
		this.start()
	)
)

-- Define helpers; do NOT auto-start.
--
-- Start TCP or pick the transport via `MCP Panel` (macroscript MCP_Panel,
-- Customize UI -> MCP -> MCP Panel), or `MCP Start` for a quick start using
-- the configured transport (auto | tcp | native). `MCP Stop` shuts the TCP
-- fallback back down. Keep native bridge as the default path for normal
-- interactive use.
-- Guard corrupted MCP_Server (e.g. HWND Pointer 923704P): probing .isRunning
-- must not block reload. Always recreate the struct after optional stop.
if MCP_Server != undefined then
(
	try (if MCP_Server.isRunning do MCP_Server.stop()) catch ()
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
控制面板 (v2 移植 + 传输方式选择): 用户可自行选择 TCP / native bridge
--------------------------------------------------------------------
*/
-- UI 事件经全局函数中转: struct 方法直接传给 dotNet.addEventHandler 在个别
-- 3ds Max 版本 / 启动方式下事件可能不触发, 全局函数值做委托转换最可靠
global mcpUI_onModeChanged,mcpUI_onStartClick,mcpUI_onStopClick,mcpUI_onFormClosing
global mcpUI_onControlGotFocus,mcpUI_onControlLostFocus
fn mcpUI_onModeChanged sender args = (if MCP_UI != undefined do MCP_UI.onModeChanged sender args)
fn mcpUI_onStartClick sender args = (if MCP_UI != undefined do MCP_UI.onStartClick sender args)
fn mcpUI_onStopClick sender args = (if MCP_UI != undefined do MCP_UI.onStopClick sender args)
fn mcpUI_onFormClosing sender args = (if MCP_UI != undefined do MCP_UI.onFormClosing sender args)
-- WinForms TextBox 在 Max 中拿不到键盘输入: Max 主窗口加速键会先吃掉按键。
-- 焦点进入控件时关闭加速键, 离开时恢复 (Autodesk / 社区标准做法)。
fn mcpUI_onControlGotFocus sender args = (enableAccelerators = false)
fn mcpUI_onControlLostFocus sender args = (enableAccelerators = true)
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
			this.statusLabel.Text = "状态 / Status: 运行中 / Running   " + MCP_Server.bindIP + ":" + (MCP_Server.port as string)
			this.startButton.Enabled = false
			this.stopButton.Enabled = true
			this.transportCombo.Enabled = false
			this.modeCombo.Enabled = false
			this.ipTextBox.Enabled = false
			this.portTextBox.Enabled = false
		)
		else
		(
			this.statusLabel.Text = "状态 / Status: 已停止 / Stopped"
			this.startButton.Enabled = true
			this.stopButton.Enabled = false
			this.modeCombo.Enabled = true
			this.ipTextBox.Enabled = true
			this.portTextBox.Enabled = true
			-- 远程必须 TCP; 仅本地可选 Auto / TCP / Native
			this.applyModeTransportRules()
		)
		this.updateHint()
	),

	-- 远程模式运行时给出 Python 端配置提示
	fn updateHint =
	(
		if this.hintLabel == undefined then return()
		local remote = this.modeCombo != undefined and this.modeCombo.SelectedIndex == 1
		if MCP_Server != undefined and MCP_Server.isRunning and remote then
		(
			local ips = this.getLocalIPv4()
			local ipStr = if ips.count > 0 then ips[1] else this.ipTextBox.Text
			this.hintLabel.Text = "Python 配置 / Config: MAXMCP_INSTANCES=" + ipStr + ":" + (MCP_Server.port as string)
		)
		else if remote then
			this.hintLabel.Text = "远程模式强制 TCP / Remote requires TCP (LAN cannot use Native pipe)"
		else
			this.hintLabel.Text = ""
	),

	-- 远程 → 锁定 TCP; 本地 → 开放传输方式多选
	fn applyModeTransportRules =
	(
		if this.transportCombo == undefined or this.modeCombo == undefined then return()
		local remote = this.modeCombo.SelectedIndex == 1
		if remote then
		(
			this.transportCombo.SelectedIndex = 1  -- 仅 TCP / TCP only
			this.transportCombo.Enabled = false
		)
		else
		(
			-- 服务未运行时才允许改传输方式
			local running = MCP_Server != undefined and MCP_Server.isRunning
			this.transportCombo.Enabled = not running
		)
	),

	-- 模式切换: 本地 127.0.0.1 / 远程 0.0.0.0 (可自定义); 远程强制 TCP
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
		this.applyModeTransportRules()
		this.settingMode = false
		this.updateHint()
	),

	fn onStartClick sender args =
	(
		--format "MCP Server: onStartClick\n"
		try (MCP_Server.bindIP = this.ipTextBox.Text) catch ()
		try (MCP_Server.port = (this.portTextBox.Text as integer)) catch (MCP_Server.port = 8765)
		try
		(
			if this.safeCheckBox != undefined do
				MCP_Server.safeMode = this.safeCheckBox.Checked
		)
		catch ()
		-- 远程模式必须监听 TCP (Native named pipe 无法跨机)
		if this.modeCombo != undefined and this.modeCombo.SelectedIndex == 1 then
			MCP_Server.transport = #tcp
		else if this.transportCombo != undefined then
			MCP_Server.transport = case this.transportCombo.SelectedIndex of
			(
				1: #tcp
				2: #native
				default: #auto
			)
		else
			MCP_Server.transport = #auto
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
				this.statusLabel.Text = "状态 / Status: Native Bridge (未监听 TCP / no TCP)"
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
		-- 窗口关掉时务必恢复 Max 加速键, 避免 LostFocus 未触发导致快捷键失效
		enableAccelerators = true
		-- createUI 中途失败会 clearControls, 但 FormClosing 仍可能触发
		local stopOnClose = true
		try
		(
			if this.closeStopCheckBox != undefined do
				stopOnClose = this.closeStopCheckBox.Checked
		)
		catch (stopOnClose = true)
		if stopOnClose then
		(
			if MCP_Server != undefined and MCP_Server.isRunning do MCP_Server.stop()
			args.Cancel = false
			-- 窗体即将销毁(disposed): 清空引用, 下次 show() 重新创建
			this.clearControls()
		)
		else
		(
			args.Cancel = true
			try (if this.form != undefined do this.form.Hide()) catch ()
		)
	),

	-- 给需要键盘输入的控件挂焦点钩子 (TextBox / 可键入 ComboBox)
	fn hookKeyboardFocus ctrl =
	(
		if ctrl == undefined then return()
		try
		(
			dotNet.addEventHandler ctrl "GotFocus" mcpUI_onControlGotFocus
			dotNet.addEventHandler ctrl "LostFocus" mcpUI_onControlLostFocus
		)
		catch ()
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
			local labelW = 140
			local fieldX = 156
			local fieldW = 260

			this.form = dotNetObject "System.Windows.Forms.Form"
			this.form.Text = "3ds Max MCP Server"
			this.form.ClientSize = dotNetObject "System.Drawing.Size" 430 380
			this.form.FormBorderStyle = (dotNetClass "System.Windows.Forms.FormBorderStyle").FixedSingle
			this.form.MaximizeBox = false
			this.form.MinimizeBox = true
			this.form.StartPosition = (dotNetClass "System.Windows.Forms.FormStartPosition").CenterScreen
			this.form.Font = font

			-- 传输方式
			local lblTransport = dotNetObject "System.Windows.Forms.Label"
			lblTransport.Text = "传输方式 / Transport:"
			lblTransport.Location = dotNetObject "System.Drawing.Point" 12 16
			lblTransport.Size = dotNetObject "System.Drawing.Size" labelW 20
			this.form.Controls.Add lblTransport

			this.transportCombo = dotNetObject "System.Windows.Forms.ComboBox"
			this.transportCombo.Location = dotNetObject "System.Drawing.Point" fieldX 13
			this.transportCombo.Size = dotNetObject "System.Drawing.Size" fieldW 24
			this.transportCombo.DropDownStyle = (dotNetClass "System.Windows.Forms.ComboBoxStyle").DropDownList
			this.transportCombo.Items.Add "自动 / Auto (Native first)"
			this.transportCombo.Items.Add "仅 TCP / TCP only"
			this.transportCombo.Items.Add "仅 Native / Native only (no TCP)"
			this.form.Controls.Add this.transportCombo

			-- 运行模式
			local lblMode = dotNetObject "System.Windows.Forms.Label"
			lblMode.Text = "运行模式 / Mode:"
			lblMode.Location = dotNetObject "System.Drawing.Point" 12 50
			lblMode.Size = dotNetObject "System.Drawing.Size" labelW 20
			this.form.Controls.Add lblMode

			this.modeCombo = dotNetObject "System.Windows.Forms.ComboBox"
			this.modeCombo.Location = dotNetObject "System.Drawing.Point" fieldX 47
			this.modeCombo.Size = dotNetObject "System.Drawing.Size" fieldW 24
			this.modeCombo.DropDownStyle = (dotNetClass "System.Windows.Forms.ComboBoxStyle").DropDownList
			this.modeCombo.Items.Add "本地 / Local (this PC)"
			this.modeCombo.Items.Add "远程 / Remote (LAN, TCP only)"
			this.form.Controls.Add this.modeCombo

			-- 绑定 IP
			local lblIp = dotNetObject "System.Windows.Forms.Label"
			lblIp.Text = "绑定 IP / Bind IP:"
			lblIp.Location = dotNetObject "System.Drawing.Point" 12 84
			lblIp.Size = dotNetObject "System.Drawing.Size" labelW 20
			this.form.Controls.Add lblIp

			this.ipTextBox = dotNetObject "System.Windows.Forms.TextBox"
			this.ipTextBox.Location = dotNetObject "System.Drawing.Point" fieldX 81
			this.ipTextBox.Size = dotNetObject "System.Drawing.Size" fieldW 24
			this.ipTextBox.Text = "127.0.0.1"
			this.ipTextBox.ReadOnly = true
			this.form.Controls.Add this.ipTextBox
			this.hookKeyboardFocus this.ipTextBox

			-- 端口
			local lblPort = dotNetObject "System.Windows.Forms.Label"
			lblPort.Text = "端口 / Port:"
			lblPort.Location = dotNetObject "System.Drawing.Point" 12 118
			lblPort.Size = dotNetObject "System.Drawing.Size" labelW 20
			this.form.Controls.Add lblPort

			this.portTextBox = dotNetObject "System.Windows.Forms.TextBox"
			this.portTextBox.Location = dotNetObject "System.Drawing.Point" fieldX 115
			this.portTextBox.Size = dotNetObject "System.Drawing.Size" 120 24
			this.portTextBox.Text = "8765"
			this.form.Controls.Add this.portTextBox
			this.hookKeyboardFocus this.portTextBox

			-- 安全模式
			this.safeCheckBox = dotNetObject "System.Windows.Forms.CheckBox"
			this.safeCheckBox.Text = "安全模式 / Safe mode (block dangerous cmds)"
			this.safeCheckBox.Location = dotNetObject "System.Drawing.Point" 12 152
			this.safeCheckBox.Size = dotNetObject "System.Drawing.Size" 404 22
			this.safeCheckBox.Checked = true
			this.form.Controls.Add this.safeCheckBox

			-- 本机 IP 参考
			local localIps = this.getLocalIPv4()
			local ipStr = "无 / none"
			if localIps.count > 0 do
			(
				ipStr = localIps[1]
				for i = 2 to localIps.count do ipStr = ipStr + ", " + localIps[i]
			)
			this.localIpLabel = dotNetObject "System.Windows.Forms.Label"
			this.localIpLabel.Text = "本机 IP / Local IP: " + ipStr
			this.localIpLabel.Location = dotNetObject "System.Drawing.Point" 12 182
			this.localIpLabel.Size = dotNetObject "System.Drawing.Size" 404 20
			this.form.Controls.Add this.localIpLabel

			-- 分隔线
			local sep = dotNetObject "System.Windows.Forms.Label"
			sep.Text = "-------------------------------------------"
			sep.Location = dotNetObject "System.Drawing.Point" 12 208
			sep.Size = dotNetObject "System.Drawing.Size" 404 18
			this.form.Controls.Add sep

			-- 状态
			this.statusLabel = dotNetObject "System.Windows.Forms.Label"
			this.statusLabel.Text = "状态 / Status: 已停止 / Stopped"
			this.statusLabel.Location = dotNetObject "System.Drawing.Point" 12 230
			this.statusLabel.Size = dotNetObject "System.Drawing.Size" 404 22
			this.form.Controls.Add this.statusLabel

			-- 启动 / 停止
			this.startButton = dotNetObject "System.Windows.Forms.Button"
			this.startButton.Text = "启动服务 / Start"
			this.startButton.Location = dotNetObject "System.Drawing.Point" 12 258
			this.startButton.Size = dotNetObject "System.Drawing.Size" 196 30
			this.form.Controls.Add this.startButton

			this.stopButton = dotNetObject "System.Windows.Forms.Button"
			this.stopButton.Text = "停止服务 / Stop"
			this.stopButton.Location = dotNetObject "System.Drawing.Point" 222 258
			this.stopButton.Size = dotNetObject "System.Drawing.Size" 196 30
			this.stopButton.Enabled = false
			this.form.Controls.Add this.stopButton

			-- 关闭行为
			this.closeStopCheckBox = dotNetObject "System.Windows.Forms.CheckBox"
			this.closeStopCheckBox.Text = "关闭窗口时停止服务 / Stop service on close"
			this.closeStopCheckBox.Location = dotNetObject "System.Drawing.Point" 12 300
			this.closeStopCheckBox.Size = dotNetObject "System.Drawing.Size" 404 22
			this.closeStopCheckBox.Checked = true
			this.form.Controls.Add this.closeStopCheckBox

			-- 远程模式提示
			this.hintLabel = dotNetObject "System.Windows.Forms.Label"
			this.hintLabel.Text = ""
			this.hintLabel.Location = dotNetObject "System.Drawing.Point" 12 330
			this.hintLabel.Size = dotNetObject "System.Drawing.Size" 404 36
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
			this.applyModeTransportRules()
			this.updateHint()
		)
		catch
		(
			format "MCP UI: 创建界面失败: %\n" (getCurrentException())
			-- 半初始化窗体仍可能触发 FormClosing: 先 Dispose 再清空引用 (勿 Close, 避免重入)
			try (if this.form != undefined do this.form.Dispose()) catch ()
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
