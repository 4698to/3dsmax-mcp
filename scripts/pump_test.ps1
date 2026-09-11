$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class Pump {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hParent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll", SetLastError=true)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint msg, IntPtr w, IntPtr l, uint flags, uint timeout, out IntPtr result);
}
"@
$wins = @()
$cb = [Pump+EnumWindowsProc]{
    param($h,$l)
    $sb = New-Object System.Text.StringBuilder 256
    $cls = New-Object System.Text.StringBuilder 256
    [Pump]::GetWindowText($h,$sb,256) | Out-Null
    [Pump]::GetClassName($h,$cls,256) | Out-Null
    $pid2 = 0
    [Pump]::GetWindowThreadProcessId($h,[ref]$pid2) | Out-Null
    if ($pid2 -eq 12116) { $script:wins += [pscustomobject]@{H=$h.ToString("X"); Title=$sb.ToString(); Class=$cls.ToString()} }
    return $true
}
[Pump]::EnumWindows($cb,[IntPtr]::Zero) | Out-Null
$wins | ConvertTo-Json -Compress

$titles = @('3ds Max MCP Server','MAXScript Listener','3ds Max')
foreach ($t in $titles) {
    $w = $wins | Where-Object { $_.Title -eq $t }
    if ($w) {
        foreach ($w0 in $w) {
            $h = [Convert]::ToInt64($w0.H,16)
            $res = [IntPtr]::Zero
            $r = [Pump]::SendMessageTimeout([IntPtr]$h, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero, 2, 3000, [ref]$res)
            Write-Host ("SMT WM_NULL -> {0} ({1}): returned={2} err={3}" -f $w0.H,$t,$r,[Runtime.InteropServices.Marshal]::GetLastWin32Error())
        }
    } else {
        Write-Host ("NOT FOUND: " + $t)
    }
}
