param([string]$Text, [string]$H)
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class Inj {
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
}
"@
function Rects($h) {
    $r = New-Object Inj+RECT
    [Inj]::GetWindowRect([IntPtr]$h, [ref]$r) | Out-Null
    return $r
}
$r1 = Rects 0x51068
$r2 = Rects 0x50F4E
Write-Host ("51068 rect: L={0} T={1} R={2} B={3}" -f $r1.L,$r1.T,$r1.R,$r1.B)
Write-Host ("50F4E rect: L={0} T={1} R={2} B={3}" -f $r2.L,$r2.T,$r2.R,$r2.B)

$target = [Convert]::ToInt64($H,16)
Write-Host ("Inject into: {0:X} IsWindow={1}" -f $target, [Inj]::IsWindow([IntPtr]$target))
$WM_CHAR = 0x0102
$VK_RETURN = 13
foreach ($ch in $Text.ToCharArray()) {
    $code = [int]$ch
    [Inj]::PostMessage([IntPtr]$target, $WM_CHAR, [IntPtr]$code, [IntPtr]::Zero) | Out-Null
    Start-Sleep -Milliseconds 3
}
[Inj]::PostMessage([IntPtr]$target, $WM_CHAR, [IntPtr]$VK_RETURN, [IntPtr]::Zero) | Out-Null
Write-Host "Injected + ENTER"
