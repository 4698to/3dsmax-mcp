$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class Btn {
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
}
"@
$btns = @{ Stop = 0xD1142; Start = 0x140756 }
$target = $btns[$args[0]]
if (-not $target) { Write-Host "usage: click.ps1 Stop|Start"; exit 1 }
$h = [IntPtr]$target
Write-Host ("IsWindow: " + [Btn]::IsWindow($h))
$sb = New-Object System.Text.StringBuilder 256
[Btn]::GetWindowText($h,$sb,256) | Out-Null
Write-Host ("Button: " + $sb.ToString())
$BM_CLICK = 0x00F5
$r = [Btn]::SendMessage($h, $BM_CLICK, [IntPtr]::Zero, [IntPtr]::Zero)
Write-Host ("BM_CLICK SendMessage returned: " + $r.ToString("X"))
