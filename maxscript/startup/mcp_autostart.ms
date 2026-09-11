/*
	MCP Server Auto-Start
	Place this file in: [3ds Max Install Dir]\scripts\startup\
	Also copy MCP scripts to: [3ds Max Install Dir]\scripts\mcp\*.ms / *.mse
*/

(
	local scriptDir = (getDir #scripts) + "\\mcp\\"
	if not (doesFileExist scriptDir) then
	(
		format "MCP: Script folder not found: %\n" scriptDir
		format "MCP: Copy mcp *.ms / *.mse into that folder for auto-start\n"
	)
	else
	(
		local files = #()
		local msFiles = getFiles (scriptDir + "*.ms")
		local mseFiles = getFiles (scriptDir + "*.mse")
		if msFiles != undefined do for f in msFiles do append files f
		if mseFiles != undefined do for f in mseFiles do append files f

		-- Prefer mcp_server.ms first so the bridge is up before helpers.
		local ordered = #()
		local serverPath = toLower (scriptDir + "mcp_server.ms")
		for f in files where (toLower f) == serverPath do append ordered f
		for f in files where (toLower f) != serverPath do append ordered f

		if ordered.count == 0 then
		(
			format "MCP: No .ms / .mse files in: %\n" scriptDir
		)
		else
		(
			format "MCP: Auto-start loading % file(s) from %\n" ordered.count scriptDir
			for f in ordered do
			(
				try
				(
					fileIn f
					format "MCP: fileIn OK: %\n" (filenameFromPath f)
				)
				catch
				(
					format "MCP: fileIn FAILED: % — %\n" (filenameFromPath f) (getCurrentException())
				)
			)
			format "MCP: Auto-start complete\n"
		)
	)
)
