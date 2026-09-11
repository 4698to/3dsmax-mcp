$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class BT {
    [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int m);
}
"@
foreach ($h in @(0x140756, 0xD1142, 0x91056, 0x71156)) {
    $en = [BT]::IsWindowEnabled([IntPtr]$h)
    $vi = [BT]::IsWindowVisible([IntPtr]$h)
    $isw = [BT]::IsWindow([IntPtr]$h)
    $sb = New-Object System.Text.StringBuilder 256
    [BT]::GetWindowText([IntPtr]$h,$sb,256) | Out-Null
    Write-Host ("H={0:X} IsWindow={1} Enabled={2} Visible={3} Text='{4}'" -f $h,$isw,$en,$vi,$sb.ToString())
}
