$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WR {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
$r = New-Object WR+RECT
[WR]::GetWindowRect([IntPtr]0x604FC, [ref]$r) | Out-Null
Write-Host ("LISTENER rect: L={0} T={1} R={2} B={3} W={4} H={5}" -f $r.L,$r.T,$r.R,$r.B,($r.R-$r.L),($r.B-$r.T))
# capture bottom third (input pane area)
$w = $r.R - $r.L
$h = $r.B - $r.T
$inH = [Math]::Max(120, [int]($h * 0.35))
$inY = $r.B - $inH
if ($inY -lt $r.T) { $inY = $r.T }
& "g:\UGit\3dsmax-mcp\scripts\capture_region.ps1" -X $r.L -Y $inY -W $w -H $inH -Out "g:\UGit\3dsmax-mcp\listener_input.png"
