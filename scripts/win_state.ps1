$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class MW {
    [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int m);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
foreach ($h in @(0x407DC, 0x59115C, 0x207B0, 0x207B6, 0x51102, 0x40E00, 0x604FC, 0x71156, 0x60786)) {
    $en = [MW]::IsWindowEnabled([IntPtr]$h)
    $vi = [MW]::IsWindowVisible([IntPtr]$h)
    $isw = [MW]::IsWindow([IntPtr]$h)
    $sb = New-Object System.Text.StringBuilder 256
    [MW]::GetWindowText([IntPtr]$h,$sb,256) | Out-Null
    $r = New-Object MW+RECT
    [MW]::GetWindowRect([IntPtr]$h, [ref]$r) | Out-Null
    Write-Host ("H={0:X} IsWindow={1} Enabled={2} Visible={3} rect=({4},{5})-({6},{7}) Text='{8}'" -f $h,$isw,$en,$vi,$r.L,$r.T,$r.R,$r.B,$sb.ToString())
}
