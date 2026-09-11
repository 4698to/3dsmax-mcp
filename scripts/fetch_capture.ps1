param(
    [string]$Endpoint = "http://192.168.139.76:8000/mcp",
    [string]$CapFile = "viewport_5508f0d5db1c445d8080c329d3935e9c.png",
    [string]$OutDir = "G:\UGit\3dsmax-mcp\screenshots"
)
$ErrorActionPreference = 'Stop'
$script:sessionId = $null

function Invoke-Mcp([hashtable]$Payload) {
    $headers = @{ Accept = "application/json, text/event-stream" }
    if ($sessionId) { $headers["Mcp-Session-Id"] = $sessionId }
    $body = $Payload | ConvertTo-Json -Depth 12 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $r = Invoke-WebRequest -Uri $Endpoint -Method Post -Body $bytes -ContentType "application/json; charset=utf-8" -Headers $headers -TimeoutSec 600
    if (-not $sessionId -and $r.Headers["Mcp-Session-Id"]) { $script:sessionId = $r.Headers["Mcp-Session-Id"] }
    $text = [string]$r.Content
    if ($text -match '(?ms)^data: (.+?)(?:\r?\n\r?\n|\r?\n$|$)') { $text = $Matches[1] }
    $text
}

Write-Host "== 1/3 initialize =="
$init = Invoke-Mcp @{ jsonrpc = "2.0"; id = 1; method = "initialize"; params = @{ protocolVersion = "2025-03-26"; capabilities = @{}; clientInfo = @{ name = "remote-test"; version = "1.0" } } }
Write-Host $init

Write-Host "`n== 2/3 notifications/initialized =="
try { Invoke-Mcp @{ jsonrpc = "2.0"; method = "notifications/initialized" } | Out-Null; Write-Host "sent" } catch { Write-Host ("WARN: " + $_.Exception.Message) }

$ws = "C:\Users\199505\AppData\Local\Temp\3dsmax-mcp\workspace"
$src = "C:\Users\199505\AppData\Local\Temp\3dsmax-mcp\$CapFile"
$dst = "$ws\$CapFile"
$code = "src = `"$src`"; dst = `"$dst`"; ok = copyfile src dst; if ok then (`"COPIED `" + dst) else `"COPY_FAILED`""
Write-Host "`n== 3/3 execute_maxscript copy =="
$exec = Invoke-Mcp @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "execute_maxscript"; arguments = @{ command = $code } } }
Write-Host $exec

Write-Host "`n== 4/4 workspace_download =="
$dl = Invoke-Mcp @{ jsonrpc = "2.0"; id = 3; method = "tools/call"; params = @{ name = "workspace_download"; arguments = @{ file_name = $CapFile } } }
Write-Host $dl

$imgB64 = $null
if ($dl -match '"data_b64"\s*:\s*"([^"]+)"') { $imgB64 = $Matches[1] }
if (-not $imgB64) { Write-Host "NO_IMAGE_DATA"; exit 1 }

if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$outImg = Join-Path $OutDir "m_fash_004_b_skin_viewport.png"
[System.IO.File]::WriteAllBytes($outImg, [Convert]::FromBase64String($imgB64))
Write-Host "SAVED=$outImg"
