param(
    [string]$Endpoint = "http://192.168.139.76:8000/mcp",
    [string]$ScenePath = "C:\Users\Administrator\AppData\Roaming\im\199505@nd\RecvFile\肖家鑫_920401\m_fash_004_b_skin#2026.8.26雷世坤.max",
    [string]$OutDir = "G:\UGit\3dsmax-mcp\screenshots"
)
$ErrorActionPreference = 'Stop'
$script:sessionId = $null
$asciiName = "m_fash_004_b_skin_2026_08_26_ascii.max"

function Invoke-Mcp([hashtable]$Payload) {
    $headers = @{ Accept = "application/json, text/event-stream" }
    if ($sessionId) { $headers["Mcp-Session-Id"] = $sessionId }
    $body = $Payload | ConvertTo-Json -Depth 12 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $r = Invoke-WebRequest -Uri $Endpoint -Method Post -Body $bytes -ContentType "application/json; charset=utf-8" -Headers $headers -TimeoutSec 900
    if (-not $sessionId -and $r.Headers["Mcp-Session-Id"]) { $script:sessionId = $r.Headers["Mcp-Session-Id"] }
    $text = [string]$r.Content
    if ($text -match '(?ms)^data: (.+?)(?:\r?\n\r?\n|\r?\n$|$)') { $text = $Matches[1] }
    $text
}

if (-not (Test-Path -LiteralPath $ScenePath)) { Write-Host "SCENE NOT FOUND: $ScenePath"; exit 1 }
Write-Host "SCENE=$ScenePath"

Write-Host "== 1/6 initialize =="
$init = Invoke-Mcp @{ jsonrpc = "2.0"; id = 1; method = "initialize"; params = @{ protocolVersion = "2025-03-26"; capabilities = @{}; clientInfo = @{ name = "remote-test"; version = "1.0" } } }
Write-Host $init

Write-Host "`n== 2/6 notifications/initialized =="
try { Invoke-Mcp @{ jsonrpc = "2.0"; method = "notifications/initialized" } | Out-Null; Write-Host "sent" } catch { Write-Host ("WARN: " + $_.Exception.Message) }

Write-Host "`n== 3/6 workspace_upload (ascii name) =="
$b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($ScenePath))
Write-Host ("base64 length = " + $b64.Length)
$up = Invoke-Mcp @{ jsonrpc = "2.0"; id = 2; method = "tools/call"; params = @{ name = "workspace_upload"; arguments = @{ file_name = $asciiName; data_b64 = $b64 } } }
Write-Host $up

$upPath = $null
foreach ($key in @('file_path','path','file','local_path','name')) {
    if ($up -match ('"{0}"\s*:\s*"([^"]+)"' -f [regex]::Escape($key))) { $upPath = $Matches[1] -replace '\\\\', '\'; break }
}
if (-not $upPath) { $upPath = $asciiName }
Write-Host "UP_PATH=$upPath"

Write-Host "`n== 4/6 load_scene =="
$load = Invoke-Mcp @{ jsonrpc = "2.0"; id = 3; method = "tools/call"; params = @{ name = "load_scene"; arguments = @{ file_path = $upPath } } }
Write-Host $load

Write-Host "`n== 5/6 capture_viewport =="
$cap = Invoke-Mcp @{ jsonrpc = "2.0"; id = 4; method = "tools/call"; params = @{ name = "capture_viewport"; arguments = @{ source = "auto"; max_width = 1600; max_height = 0 } } }
Write-Host $cap

$capFile = $null
if ($cap -match '"file"\s*:\s*"([^"]+)"') { $capFile = $Matches[1] -replace '\\\\', '\' }
if (-not $capFile) { Write-Host "NO_CAPTURE_FILE"; exit 1 }
Write-Host "CAPTURE_FILE=$capFile"
$capName = Split-Path -Leaf $capFile

Write-Host "`n== 6/6 workspace_download =="
$dl = Invoke-Mcp @{ jsonrpc = "2.0"; id = 5; method = "tools/call"; params = @{ name = "workspace_download"; arguments = @{ file_name = $capName } } }
Write-Host $dl

$imgB64 = $null
if ($dl -match '"data_b64"\s*:\s*"([^"]+)"') { $imgB64 = $Matches[1] }
if (-not $imgB64) { Write-Host "NO_IMAGE_DATA"; exit 1 }

if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
$outImg = Join-Path $OutDir "m_fash_004_b_skin_viewport.png"
[System.IO.File]::WriteAllBytes($outImg, [Convert]::FromBase64String($imgB64))
Write-Host "SAVED=$outImg"
