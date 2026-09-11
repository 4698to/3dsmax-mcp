$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinEnum {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hParent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
}
"@
$out = @()
$cb = [WinEnum+EnumWindowsProc]{
    param($h,$l)
    $sb = New-Object System.Text.StringBuilder 256
    $cls = New-Object System.Text.StringBuilder 256
    [WinEnum]::GetWindowText($h,$sb,256) | Out-Null
    [WinEnum]::GetClassName($h,$cls,256) | Out-Null
    $pid2 = 0
    [WinEnum]::GetWindowThreadProcessId($h,[ref]$pid2) | Out-Null
    if ($pid2 -eq 12116) {
        $script:out += [pscustomobject]@{H=$h.ToString("X"); Title=$sb.ToString(); Class=$cls.ToString()}
    }
    return $true
}
[WinEnum]::EnumWindows($cb,[IntPtr]::Zero) | Out-Null
$panel = $script:out | Where-Object { $_.Title -eq '3ds Max MCP Server' }
if (-not $panel) { Write-Host "PANEL NOT FOUND"; exit 1 }
Write-Host ("PANEL: " + ($panel | ConvertTo-Json -Compress))
$target = $panel.H
$targetDec = [Convert]::ToInt64($target,16)
$children = @()
$cb2 = [WinEnum+EnumWindowsProc]{
    param($h,$l)
    $sb = New-Object System.Text.StringBuilder 256
    $cls = New-Object System.Text.StringBuilder 256
    [WinEnum]::GetWindowText($h,$sb,256) | Out-Null
    [WinEnum]::GetClassName($h,$cls,256) | Out-Null
    $script:children += [pscustomobject]@{H=$h.ToString("X"); Title=$sb.ToString(); Class=$cls.ToString()}
    return $true
}
[WinEnum]::EnumChildWindows([IntPtr]$targetDec,$cb2,[IntPtr]::Zero) | Out-Null
$children | ConvertTo-Json -Compress
