param([string]$H, [int]$MaxLen)
$ErrorActionPreference = 'Stop'
$MaxLen = if ($MaxLen) { $MaxLen } else { 65536 }
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class Sci {
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("kernel32.dll")] public static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll")] public static extern bool VirtualFreeEx(IntPtr h, IntPtr lp, uint sz, uint t);
    [DllImport("kernel32.dll")] public static extern IntPtr VirtualAllocEx(IntPtr h, IntPtr lp, uint sz, uint t, uint p);
    [DllImport("kernel32.dll")] public static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, uint sz, out UIntPtr read);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
$pid2 = 0
[Sci]::GetWindowThreadProcessId([IntPtr]0x604FC, [ref]$pid2) | Out-Null
Write-Host ("Listener pid: " + $pid2)
$PROC_VM_OP = 0x0008; $PROC_VM_READ = 0x0010; $PROC_VM_WRITE = 0x0020; $PROC_QUERY_INFO = 0x0400
$hProc = [Sci]::OpenProcess(($PROC_VM_OP -bor $PROC_VM_READ -bor $PROC_VM_WRITE -bor $PROC_QUERY_INFO), $false, $pid2)
if ($hProc -eq [IntPtr]::Zero) { Write-Host "OpenProcess failed"; exit 1 }
$MEM_COMMIT = 0x1000; $MEM_RESERVE = 0x2000; $PAGE_READWRITE = 0x04
$buf = [Sci]::VirtualAllocEx($hProc, [IntPtr]::Zero, $MaxLen, ($MEM_COMMIT -bor $MEM_RESERVE), $PAGE_READWRITE)
if ($buf -eq [IntPtr]::Zero) { Write-Host "VirtualAllocEx failed"; exit 1 }
$tgt = [Convert]::ToInt64($H,16)
$SCI_GETLENGTH = 2183; $SCI_GETTEXT = 2182
$len = [Sci]::SendMessage([IntPtr]$tgt, $SCI_GETLENGTH, [IntPtr]::Zero, [IntPtr]::Zero)
Write-Host ("H={0:X} SCI_GETLENGTH={1}" -f $tgt, $len.ToInt64())
if ($len.ToInt64() -gt 0) {
    $l = [Math]::Min([int]$len.ToInt64() + 1, $MaxLen)
    $r = [Sci]::SendMessage([IntPtr]$tgt, $SCI_GETTEXT, [IntPtr]$l, $buf)
    $bytes = New-Object byte[] $l
    $read = [UIntPtr]::Zero
    [Sci]::ReadProcessMemory($hProc, $buf, $bytes, $l, [ref]$read) | Out-Null
    $txt = [System.Text.Encoding]::ASCII.GetString($bytes, 0, [int]$read.ToUInt64())
    Write-Host ("---- content of {0:X} (len={1}) ----" -f $tgt, $read.ToUInt64())
    Write-Host $txt
    Write-Host "---- end ----"
}
[Sci]::VirtualFreeEx($hProc, $buf, 0, 0x8000)
[Sci]::CloseHandle($hProc)
