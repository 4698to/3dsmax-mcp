param(
    [string]$Endpoint = "http://192.168.139.76:8000/mcp"
)
$ErrorActionPreference = 'Stop'
$script:sessionId = $null

function Invoke-Mcp([hashtable]$Payload) {
    $headers = @{ Accept = "application/json, text/event-stream" }
    if ($sessionId) { $headers["Mcp-Session-Id"] = $sessionId }
    $body = $Payload | ConvertTo-Json -Depth 12 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $r = Invoke-WebRequest -Uri $Endpoint -Method Post -Body $bytes -ContentType "application/json; charset=utf-8" -Headers $headers -TimeoutSec 300
    if (-not $sessionId -and $r.Headers["Mcp-Session-Id"]) { $script:sessionId = $r.Headers["Mcp-Session-Id"] }
    $text = [string]$r.Content
    if ($text -match '(?ms)^data: (.+?)(?:\r?\n\r?\n|\r?\n$|$)') { $text = $Matches[1] }
    $text
}

$init = Invoke-Mcp @{ jsonrpc = "2.0"; id = 1; method = "initialize"; params = @{ protocolVersion = "2025-03-26"; capabilities = @{}; clientInfo = @{ name = "remote-test"; version = "1.0" } } }
try { Invoke-Mcp @{ jsonrpc = "2.0"; method = "notifications/initialized" } | Out-Null } catch {}

$code1 = @'
(
    ws = @"C:\Users\199505\AppData\Local\Temp\3dsmax-mcp\workspace\"
    fs = getFiles (ws + "*.max")
    if fs.count == 0 then "NO_MAX_FILES" else ((for f in fs collect (getFilenameFile f)) as string)
)
'@
Write-Host "== list workspace .max files =="
$r1 = Invoke-Mcp @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "execute_maxscript"; arguments = @{ command = $code1 } } }
Write-Host $r1

$code2 = @'
(
    ws = @"C:\Users\199505\AppData\Local\Temp\3dsmax-mcp\workspace\"
    all = getFiles (ws + "*")
    if all.count == 0 then "EMPTY" else ((for f in all collect (getFilenameFile f)) as string)
)
'@
Write-Host "`n== list ALL workspace files =="
$r2 = Invoke-Mcp @{ jsonrpc = "2.0"; id = 3; method = "tools/call"; params = @{ name = "execute_maxscript"; arguments = @{ command = $code2 } } }
Write-Host $r2
