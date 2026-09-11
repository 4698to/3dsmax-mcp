$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class GT {
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder sb, int m);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
}
"@
$sb = New-Object System.Text.StringBuilder 256
[GT]::GetWindowText([IntPtr]0x91056,$sb,256) | Out-Null
Write-Host ("STATUS: " + $sb.ToString())
Write-Host ("IsWindow(91056): " + [GT]::IsWindow([IntPtr]0x91056))
