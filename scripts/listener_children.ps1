$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class LC {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hParent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
}
"@
$kids = @()
$cb = [LC+EnumWindowsProc]{
    param($h,$l)
    $cls = New-Object System.Text.StringBuilder 256
    $sb = New-Object System.Text.StringBuilder 256
    [LC]::GetClassName($h,$cls,256) | Out-Null
    [LC]::GetWindowText($h,$sb,256) | Out-Null
    $script:kids += [pscustomobject]@{H=$h.ToString("X"); Class=$cls.ToString(); Title=$sb.ToString()}
    return $true
}
[LC]::EnumChildWindows([IntPtr]0x604FC,$cb,[IntPtr]::Zero) | Out-Null
$kids | ConvertTo-Json -Compress
