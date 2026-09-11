param([string]$Text, [string]$H)
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class SciX {
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("kernel32.dll")] public static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll")] public static extern bool VirtualFreeEx(IntPtr h, IntPtr lp, uint sz, uint t);
    [DllImport("kernel32.dll")] public static extern IntPtr VirtualAllocEx(IntPtr h, IntPtr lp, uint sz, uint t, uint p);
    [DllImport("kernel32.dll")] public static extern bool WriteProcessMemory(IntPtr h, IntPtr addr, byte[] buf, uint sz, out UIntPtr w);
    [DllImport("kernel32.dll")] public static extern bool ReadProcessMemory(IntPtr h, IntPtr addr, byte[] buf, uint sz, out UIntPtr read);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
$pid2 = 0
[SciX]::GetWindowThreadProcessId([IntPtr]0x604FC, [ref]$pid2) | Out-Null
$PROC_VM_OP = 0x0008; $PROC_VM_READ = 0x0010; $PROC_VM_WRITE = 0x0020; $PROC_QUERY_INFO = 0x0400
$hProc = [SciX]::OpenProcess(($PROC_VM_OP -bor $PROC_VM_READ -bor $PROC_VM_WRITE -bor $PROC_QUERY_INFO), $false, $pid2)
$MEM_COMMIT = 0x1000; $MEM_RESERVE = 0x2000; $PAGE_READWRITE = 0x04
$tgt = [Convert]::ToInt64($H,16)
$SCI_GETLENGTH = 2183; $SCI_INSERTTEXT = 2003; $SCI_GETTEXT = 2182; $SCI_CLEARALL = 2004
$len = [SciX]::SendMessage([IntPtr]$tgt, $SCI_GETLENGTH, [IntPtr]::Zero, [IntPtr]::Zero).ToInt64()
Write-Host ("H={0:X} len before={1}" -f $tgt, $len)

$bytes = [System.Text.Encoding]::ASCII.GetBytes($Text)
$buf = [SciX]::VirtualAllocEx($hProc, [IntPtr]::Zero, $bytes.Length + 1, ($MEM_COMMIT -bor $MEM_RESERVE), $PAGE_READWRITE)
$wr = [UIntPtr]::Zero
[SciX]::WriteProcessMemory($hProc, $buf, $bytes, $bytes.Length, [ref]$wr) | Out-Null
$r = [SciX]::SendMessage([IntPtr]$tgt, $SCI_INSERTTEXT, [IntPtr]$len, $buf)
Write-Host ("SCI_INSERTTEXT sent, ret=" + $r.ToString("X"))
[SciX]::VirtualFreeEx($hProc, $buf, 0, 0x8000)

Start-Sleep -Milliseconds 500
# read back tail
$len2 = [SciX]::SendMessage([IntPtr]$tgt, $SCI_GETLENGTH, [IntPtr]::Zero, [IntPtr]::Zero).ToInt64()
$l = [Math]::Min([int]$len2 + 1, 300)
$buf2 = [SciX]::VirtualAllocEx($hProc, [IntPtr]::Zero, $l, ($MEM_COMMIT -bor $MEM_RESERVE), $PAGE_READWRITE)
[SciX]::SendMessage([IntPtr]$tgt, $SCI_GETTEXT, [IntPtr]$l, $buf2) | Out-Null
$rb = New-Object byte[] $l
$rd = [UIntPtr]::Zero
[SciX]::ReadProcessMemory($hProc, $buf2, $rb, $l, [ref]$rd) | Out-Null
$tail = [System.Text.Encoding]::ASCII.GetString($rb, 0, [Math]::Min([int]$rd.ToUInt64(), $l))
Write-Host ("---- tail after insert (len={0}) ----" -f $len2)
Write-Host $tail.Substring([Math]::Max(0, $tail.Length - 250))
[SciX]::VirtualFreeEx($hProc, $buf2, 0, 0x8000)
[SciX]::CloseHandle($hProc)
