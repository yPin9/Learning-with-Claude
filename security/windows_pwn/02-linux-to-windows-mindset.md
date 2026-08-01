# Ch 2 — Linux→Windows 攻堅直覺遷移對照表

> **目標**：把你在 Linux pwn 建立的每一個具體直覺，逐條對應到 Windows 的等價物，並且解釋「為什麼不一樣」——不只是並列名詞，而是理解設計分岔的原因。讀完這章，你有一本隨時可查的翻譯字典，之後每遇到一個 Windows 概念，你都能問「這是 Linux 的哪個東西的對應？差在哪裡？」

## 為什麼需要這個？

你的 Linux pwn 直覺是最大的資產，也是最大的陷阱。「資產」因為底層邏輯（找漏洞→建原語→劫控制流→落地 payload）是可遷移的；「陷阱」因為太多具體細節——某個 struct 的欄位位置、某個保護的觸發條件、某個函數的語意——在 Windows 下根本不同。

最浪費時間的學習路徑是：每次撞到差異才被動修正直覺。這章做相反的事：**主動列出差異的完整清單**，讓你帶著「已知哪裡不同」的意識進入後面的章節，而不是一邊學一邊踩地雷。

## 先建立直覺：一本翻譯字典的結構

```
Linux 世界                             Windows 世界
─────────────────────────────────────────────────────────
ELF                      ←→           PE（Portable Executable）
GOT / PLT                ←→           IAT（Import Address Table）
ld.so                    ←→           ntdll loader（LDR）
__libc_start_main        ←→           mainCRTStartup / CRT init
environ                  ←→           PEB->ProcessParameters->Environment
stack canary             ←→           /GS cookie（__security_check_cookie）
RELRO（GOT read-only）   ←→           IAT 可寫性（無直接對應旗標）
system("/bin/sh")        ←→           WinExec("cmd.exe") / CreateProcess
ret2libc                 ←→           ret2(kernel32) / ret2(ntdll)
dlsym / GOT overwrite    ←→           GetProcAddress / PEB-walk export resolve
one_gadget               ←→           （無現成，需自建 ROP chain）
syscall（int 0x80/syscall + rax 號）←→ Nt* stub + SSN（版本相依）
/proc/self/maps          ←→           PEB Ldr 模組列舉
seccomp                  ←→           Process Mitigation Policy / Win32k lockdown
─────────────────────────────────────────────────────────
```

下面每一條都有「為什麼不同」的解釋。

## 大對照表

### ELF ↔ PE（Portable Executable）

| | Linux | Windows |
|---|---|---|
| 格式 | ELF（Executable and Linkable Format）| PE（Portable Executable，基於 COFF） |
| 起點 | ELF header → Program headers → Sections | DOS header → PE signature → COFF header → Optional header → Section table |
| 動態連結資訊 | `.dynamic` section，`PT_DYNAMIC` segment | Data directory 的 Import Directory（指向 IAT/ILT） |
| 重定位 | `.rela.dyn`、`.rela.plt`（lazy binding 透過 PLT/GOT） | `.reloc` section（load-time relocation，非 lazy） |
| 緩解資訊 | `GNU_STACK`、`GNU_RELRO` program header；RUNPATH | `DllCharacteristics`（ASLR/DEP/CFG 旗標）；Load Config Directory（/GS cookie、CFG bitmap 位址）|

**為什麼不同**：ELF 和 PE 是獨立演化的格式，設計年代和目標平台不同。ELF 的 lazy binding（第一次呼叫才 resolve）靠 PLT 跳板，這讓 GOT 在執行期是可寫的，也造就了 GOT overwrite 這類攻擊。PE 的 IAT 在 loader 啟動時就全部 resolve 好（load-time binding），所以沒有 PLT 機制，攻擊 IAT 的路徑也不同（需要 IAT 所在頁面真的可寫）。

**對 pwn 的影響**：逆向一個 Windows binary 時，先看 PE Optional header 的 `DllCharacteristics`（ASLR/DEP 旗標）和 Load Config（CFG、/GS cookie 位址）——這相當於你在 Linux 做 `checksec` 那件事。

### GOT / PLT ↔ IAT（Import Address Table）

Linux 的 PLT/GOT 機制：

```
call malloc@plt
      │
      ▼
PLT stub: jmp [GOT+offset]   ← 第一次：GOT 裡放的是 resolver
      │  （lazy binding）      ← resolve 後：GOT 裡放的是 malloc 真實位址
      ▼
libc!malloc
```

Windows 的 IAT 機制：

```
call [IAT+offset_of_malloc]   ← IAT 在 loader 啟動時就已填好真實位址
      │  （load-time binding）
      ▼
ucrtbase!malloc
```

**為什麼不同**：PLT/GOT 的 lazy binding 是 ELF ABI 的設計，目的是加快啟動速度（不用在啟動時 resolve 所有符號）。Windows 選擇在 loader 階段一次 resolve 全部，因為 Windows 的 loader（LDR）和 DLL 機制有更完整的相依順序管理，load-time resolve 更適合這個架構。

**對 pwn 的影響**：Linux 的 GOT overwrite 靠的是「GOT 是可寫的資料段」這個特性；Windows 的 IAT 是否可寫取決於記憶體保護（通常是 `PAGE_READWRITE`，但不保證）。用 `VirtualQuery` 查 IAT 所在頁面的 `Protect` 欄位才是正確的判斷方式，而不是假設。

### `ld.so` ↔ ntdll loader（LDR）

| | Linux | Windows |
|---|---|---|
| 實體 | `/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2` | `ntdll.dll`（其中的 LDR 子系統） |
| 觸發時機 | kernel execve → 把控制交給 `ld.so` | kernel `NtCreateUserProcess` → 最初只映射 ntdll，由 ntdll 的 LDR 繼續 |
| 模組列表 | `/proc/self/maps` 或 `dl_iterate_phdr` | `PEB->Ldr`（`_PEB_LDR_DATA`）的三條雙向鏈結串列 |
| DLL 搜尋路徑 | `RPATH`、`LD_LIBRARY_PATH`、`/etc/ld.so.conf` | `DLL Search Order`：application dir → System32 → Windows → CWD → PATH |

**為什麼不同**：`ld.so` 在 Linux 是一個獨立的 ELF interpreter，本身是個特殊 shared object。Windows 的 LDR 是 ntdll 內部的一個子系統，因為 ntdll 是 Windows NT 架構的最低使用者態層，比任何 Win32 DLL 更早進入 process。

**對 pwn 的影響**：從 PEB->Ldr 走訪模組列表，是 Windows shellcode 最常見的「找 kernel32 基址」方法（因為 shellcode 不能用靜態位址，也不能呼叫 `GetModuleHandle`）。Ch 25 的 shellcode 章會把這個走訪寫成組語。

### `__libc_start_main` ↔ `mainCRTStartup` / CRT init

Linux 程式的啟動：

```
kernel execve
  → ELF entry point（通常是 _start）
  → __libc_start_main（glibc 的 C runtime bootstrap）
    → 呼叫 main()
    → 處理 exit handlers、atexit、__fini_array
```

Windows 程式的啟動：

```
kernel NtCreateUserProcess
  → ntdll loader 映射所有 DLL、執行 DLL initializer（DllMain）
  → 跳到 PE entry point（通常是 mainCRTStartup 或 wmainCRTStartup）
  → CRT（C runtime）初始化：設定 heap、初始化 C++ global 物件、設定 locale
  → 呼叫 main() 或 WinMain()
  → 處理 atexit、C++ global destructor
```

**為什麼不同**：兩者的 CRT 啟動流程結構相似，但 Windows 的 CRT（`vcruntime140.dll`、`ucrtbase.dll`）和作業系統的分離程度比 glibc 高——glibc 同時承擔 C runtime 和 syscall wrapper 的角色，而 Windows 的 C runtime 幾乎純粹是「C 標準函數的實作」，不負責 syscall。

**對 pwn 的影響**：Windows 的 CRT 初始化在 PE entry point，不是在 `ntdll`。如果你想 hook 程式最早的執行點，要注意 DllMain（DLL 的 init）在 CRT init 之前就已跑過。

### `environ` ↔ `PEB->ProcessParameters->Environment`

Linux：環境變數在 `environ`（`char **`），放在 stack 頂端的 auxiliary vector 附近，也可以用 `getenv()` 讀。

Windows：環境變數在 `PEB->ProcessParameters->Environment`，是一個 wide string（UTF-16LE）的 multi-string（每個 `KEY=VALUE\0`，最後以 `\0\0` 結束），位址在 `PEB->ProcessParameters` 的偏移 `0x80`（x64）。

```c
// 真實可跑（Python + ctypes）
import ctypes, ctypes.wintypes

ntdll = ctypes.WinDLL("ntdll")
# 從 TEB(GS:[0x30]) 拿到 PEB 這件事要用 inline asm 或其他方式；
# 比較直接的方法是用 GetEnvironmentStringsW
kernel32 = ctypes.WinDLL("kernel32")
env = kernel32.GetEnvironmentStringsW()
print("env block addr:", hex(env))
```

**為什麼不同**：Windows 的所有 process 參數（命令列、環境變數、工作目錄等）都集中在 `ProcessParameters` 這個結構，而 Linux 把這些資訊分散在 stack 的 argv/envp/auxv 區域。這個設計讓 Windows 的 process 建立更標準化（`PEB->ProcessParameters` 是官方的 API 通道），但對 exploit 來說，位置是可以靠 PEB 位址推算的。

### stack canary（`__stack_chk_fail`）↔ `/GS`（`__security_check_cookie`）

| | Linux GCC stack canary | Windows MSVC /GS cookie |
|---|---|---|
| 實作層 | 編譯器在 prologue/epilogue 插 `mov rax, [fs:0x28]` / 檢查 | 編譯器在 prologue/epilogue 插對 `__security_cookie` 的比對 |
| Cookie 存放 | `fs:0x28`（TLS 區域，由 glibc 在 `__libc_start_main` 設定） | 全域變數 `__security_cookie`（在 PE 的 `.data` 或 `.rdata`，由 CRT init 設定） |
| Cookie 生成 | `getentropy`/`getrandom`/時間戳混合 | `GetSystemTimeAsFileTime` + PID + TID + tick count + 效能計數器混合後 XOR |
| 失敗 handler | `__stack_chk_fail` → `abort()` | `__security_check_cookie` 失敗 → `__report_gsfailure` → `UnhandledExceptionFilter` → 終止 |
| XOR 保護 | 無（cookie 直接比對） | x64 下 cookie 和 stack frame 位址 XOR，讓猜測更難 |

**為什麼不同**：/GS 和 GCC stack canary 解決同一個問題（stack smashing），但 Microsoft 在 Win XP SP2 時期設計 /GS，有更多關於 SEH frame 保護的考量（/GS 保護的是函數的 SEH frame，不只是 ret addr）。Cookie 值存在全域變數而非 TLS，是一個設計上的差異：攻擊者如果能 arbitrary read，讀到 `__security_cookie` 就能算出 cookie 繞過 /GS。這也是為什麼現代 Windows exploit 通常先做 info leak。

### RELRO ↔ IAT 可寫性（Windows 無直接對應旗標）

Linux Full RELRO：linker 在 load time 把 GOT 完全 resolve 好，然後把 GOT 頁面設成 `PROT_READ`（`mprotect` 呼叫），讓 GOT overwrite 失效。這是一個**明確的 linker 選項**（`-Wl,-z,relro,-z,now`）。

Windows IAT：

```
IAT 所在頁面的保護  =  由 loader 決定，通常是 PAGE_READWRITE 或 PAGE_READONLY
                      視 PE 的 section flags 和 loader 的行為
```

Windows **沒有一個對應 Full RELRO 的編譯/連結選項**。IAT 在某些情況下是可寫的，在某些情況下（例如 CFG 的 `GUARD_EH_CONTINUATION` 相關機制，或者 loader 對唯讀 section 的映射）是不可寫的。要確認，用：

```
VirtualQuery(iat_address, &mbi, sizeof(mbi));
printf("IAT page protect: 0x%x\n", mbi.Protect);
```

**對 pwn 的影響**：不要假設 Windows IAT 一定可寫（像 Linux 的 Partial RELRO 那樣），也不要假設它一定不可寫（像 Full RELRO）。動態查，才不會在開發 exploit 時踩錯方向。

### `system("/bin/sh")` ↔ `WinExec("cmd.exe")` / `CreateProcess`

Linux 最簡潔的 payload 落地：

```c
system("/bin/sh");   // glibc，內部 fork+exec
```

Windows 對應：

```c
// 選項 1：最簡單，類似 system()
WinExec("cmd.exe", SW_SHOW);   // kernel32，同步執行外部程式

// 選項 2：有完整控制的版本
STARTUPINFOA si = {0};
PROCESS_INFORMATION pi = {0};
si.cb = sizeof(si);
CreateProcessA(NULL, "cmd.exe", NULL, NULL, FALSE,
               CREATE_NEW_CONSOLE, NULL, NULL, &si, &pi);
```

**為什麼不同**：Linux 的 `system()` 是 POSIX 標準，底層是 `fork()` + `execve()`。Windows 沒有 `fork()`，process 建立的 API 是 `CreateProcess`，完全不同的設計。`WinExec` 是舊版 API（Windows 3.x 時代），功能受限但呼叫最簡單，在 shellcode 裡偶爾用。`CreateProcess` 是現代正確方法，但參數多。

**對 pwn 的影響**：你在 ROP chain 裡「呼叫 system」的段落，在 Windows 換成「呼叫 WinExec 或 CreateProcess」，要把對應的引數放進 RCX（第一個引數，x64 Windows calling convention）。

### ret2libc ↔ ret2(kernel32) / ret2(ntdll)

Linux ret2libc：leak libc 基址 → 算出 `system` 的位址 → 把 ret addr 覆寫成 `system` → 把 "/bin/sh" 位址放進 RDI。

Windows 等效路徑：

```
1. leak ntdll 或 kernel32 的基址（方法：stack 上洩漏、格式字串、堆洩漏……）
2. 算出目標函數的位址（WinExec / CreateProcess / VirtualProtect + 自製 shellcode）
3. 用 ROP chain 設好 RCX = 第一引數（Windows x64 用 RCX/RDX/R8/R9，不是 RDI/RSI）
4. 轉到目標函數
```

**最關鍵的差異**：Windows x64 calling convention 是 **Microsoft x64 ABI**，引數順序是 `RCX, RDX, R8, R9`，而不是 Linux x64 的 `RDI, RSI, RDX, RCX, R8, R9`。這個差異在寫 ROP chain 時每次都要記——你的 Linux ROP 工具（ROPgadget）找到的 gadget 類型也會不同，因為 Windows 的 code 對 `RCX` 的操作比 `RDI` 多。

### `dlsym` / GOT overwrite ↔ `GetProcAddress` / PEB-walk export resolve

Linux 的「動態查函數位址」：

```c
void *handle = dlopen("libm.so", RTLD_LAZY);
double (*sin_fn)(double) = dlsym(handle, "sin");
```

或者直接覆寫 GOT 某個 entry 讓它指向目標。

Windows 的「動態查函數位址」：

```c
HMODULE h = GetModuleHandleA("kernel32.dll");
FARPROC fn = GetProcAddress(h, "WinExec");
```

shellcode 裡不能用 `GetProcAddress`（因為那個位址不知道），所以 shellcode 自己走 PEB→LDR→模組→Export Directory→名稱表→序號表，手動 resolve。這個「PEB-walk」是 Windows shellcode 的標準開頭。Ch 25 會寫完整的組語實作。

**為什麼不同**：`dlopen/dlsym` 是 POSIX 的運行時 DLL 機制，glibc 實作它。Windows 的 `GetProcAddress` + `LoadLibrary` 是對應物。但 shellcode 的限制（不能假設任何位址）讓「自己走 export table」成為必要，這是 Windows shellcode 最明顯的特徵。

### one_gadget ↔ （無現成，談等價思路）

Linux 的 one_gadget：glibc 的 `execve("/bin/sh", NULL, NULL)` 路徑在某些 glibc 版本、某些 context 下，剛好有一個位置可以用單一個 gadget 跳過去就拿 shell（通常是 `do_system` 或 `__libc_start_main` 附近的路徑）。`one_gadget` 工具掃描 glibc binary 找這些位置。

Windows 沒有 one_gadget，理由是設計差異：

- Windows 的 `WinExec`/`CreateProcess` 呼叫約定需要你明確設好引數，沒有「剛好 context 符合就可以直接跳」這種場景。
- ntdll / kernel32 不像 glibc 那樣有「呼叫 `execve` 的路徑剛好引數在固定位置」這種巧合。

**等價思路**：Windows 上你需要的是一個**完整的 ROP chain**，把 `VirtualProtect` 或 `VirtualAlloc` 呼叫完之後，把目標記憶體設成可執行，然後跳進你的 shellcode；或者直接 ROP 到 `WinExec("cmd.exe")` 並設好引數。沒有捷徑，但 ROP chain 的建構工具（`rp++`、`mona.py`）有對應的生成功能。

### syscall（`int 0x80` / `syscall` + rax 號）↔ `Nt*` stub + SSN

| | Linux x64 syscall | Windows x64 syscall |
|---|---|---|
| 指令 | `syscall`（或 x86 的 `int 0x80`） | `syscall`（相同的 CPU 指令）|
| 號碼存放 | `rax`（System Call Number） | `eax`（System Service Number, SSN） |
| 號碼穩定性 | **跨核心版本穩定**（`read`=0，`write`=1……永遠不變）| **每個 Windows 版本都可能不同** |
| 查詢方法 | `ausyscall --dump` 或 `unistd_64.h` | j00ru syscall table；或 runtime parse ntdll stub |
| 正常呼叫路徑 | glibc wrapper → `syscall` | Win32 API → `ntdll!Nt*` stub → `syscall` |
| 繞過路徑（anti-hook）| 直接寫 `syscall` 指令（號碼固定，沒問題）| 直接寫 `syscall`（需要先知道正確 SSN，否則呼叫到錯誤函數）|

**為什麼不同**：Linux 的 syscall 號碼是 Linux 核心對 userspace 的穩定 ABI 承諾——Linus 說「我們不會破壞 userspace」。Windows 沒有這個承諾：`Nt*` 函數的 SSN 是 Windows 的內部實作細節，Microsoft 保留隨時改動的權利（雖然大版本之間通常穩定）。

**對 pwn 的影響**：EDR（Endpoint Detection & Response）通常 hook ntdll 的 `Nt*` stub；anti-EDR 技法之一是「direct syscall」（繞過 ntdll，直接寫 syscall stub）。但 direct syscall 的代價是必須知道 SSN，所以現代實作通常搭配 runtime SSN 解析（走 ntdll export table 動態讀 stub 裡的 `eax` 值）。Ch 7 細講。

### `/proc/self/maps` ↔ PEB Ldr 模組列舉

Linux：

```c
FILE *f = fopen("/proc/self/maps", "r");
// 讀出每個映射的 [start-end] permissions name
```

Windows：

```c
// 走 PEB->Ldr 的三條鏈結串列
// InLoadOrderModuleList / InMemoryOrderModuleList / InInitializationOrderModuleList
// 每個 entry 是 LDR_DATA_TABLE_ENTRY，含 DllBase（模組基址）、FullDllName 等
```

> **未實測，理論預期**（需要 cdb + symbols）：
> ```bat
> cdb -c "dt ntdll!_PEB_LDR_DATA; q" notepad.exe
> ```
> 會印出 `_PEB_LDR_DATA` 的三條 `LIST_ENTRY`，各自是模組列表的頭。

Ch 5 會完整實作 PEB-walk，包括用 Python ctypes 從 `GS:[0x60]` 拿到 PEB，然後走 Ldr 列出所有已載入模組的基址和名稱。

**為什麼不同**：`/proc/self/maps` 是 Linux procfs 的虛擬檔案系統機制，是 kernel 向 userspace 暴露 process 狀態的一種方式。Windows 沒有 procfs，Process 資訊（包括模組列表）是透過 `PEB`（由 kernel 在 process 建立時填入並維護）和 `NtQueryVirtualMemory`（系統呼叫）兩個途徑暴露的。

### seccomp ↔ Process Mitigation Policy / Win32k lockdown

Linux seccomp：

```c
// 在 process 或 thread 層級安裝 BPF filter，限制可以呼叫哪些 syscall
prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog);
```

Windows 的對應不是一對一的：

- **Process Mitigation Policy**（`SetProcessMitigationPolicy`）：可以設定一系列的安全策略，例如 `ProcessSystemCallDisablePolicy`（禁止直接 syscall）、`ProcessDynamicCodePolicy`（ACG，禁止動態程式碼）。
- **Win32k lockdown**（`PROCESS_MITIGATION_SYSTEM_CALL_FILTER_POLICY`）：禁止 process 呼叫 Win32k（GDI/User 的 kernel 部分），這在 Chrome 等 sandbox 環境中大量使用，因為 Win32k 歷史上是高密度漏洞區。
- **AppContainer**：UWP 的隔離機制，比 seccomp 更高層——限制的是 object 存取和 capability，不是 syscall 號碼。

**為什麼不同**：seccomp 是 Linux 的 syscall-level 過濾，依賴 Linux syscall ABI 穩定（號碼固定）才能寫有意義的 BPF filter。Windows 因為 SSN 不固定，而且 Win32 API 透過 ntdll 做間接層，「syscall 層級的 filter」實作起來更複雜。Windows 選擇在更高的抽象層做限制（禁止某類 API 或某個 kernel component），而不是直接過濾 syscall 號碼。

## 一個具體攻擊骨架：洩漏基址 → 算偏移 → 呼叫 system/WinExec

下面是「同一個攻擊邏輯在 Linux vs Windows 的完整骨架」對比。兩邊都是 pseudocode，標未實測——重點是讓你看到相同攻擊邏輯在兩個系統上的具體映射。

```
───────────── Linux x64 版（pseudocode，未實測）─────────────
target: 64-bit ELF，NX on，ASLR on，Partial RELRO，PIE off

Step 1：洩漏 libc 位址
  - 透過格式字串或 stack leak，讀出 stack 上某個 libc 函數的指標
  - 算出 libc_base = leaked_addr - known_offset_of_that_function

Step 2：算 system 位址
  - system_addr = libc_base + elf.symbols['system']
  - "/bin/sh" addr = libc_base + next(libc.search(b"/bin/sh"))

Step 3：覆寫 ret addr
  - payload = b"A" * offset_to_ret
  - payload += pop_rdi_gadget        # 設 RDI = "/bin/sh" 位址
  - payload += p64(binsh_addr)
  - payload += p64(system_addr)
  - send(payload)

Result: shell
─────────────────────────────────────────────────────────────
```

```
───────────── Windows x64 版（pseudocode，未實測）─────────────
target: 64-bit PE，NX on（DEP），ASLR on，無 CFG（或 CFG 已繞過）

Step 1：洩漏 ntdll 或 kernel32 位址
  - 透過 stack leak 或 heap leak，讀出 stack/heap 上某個 ntdll 或 kernel32 函數的指標
  - 算出 module_base = leaked_addr - known_rva_of_that_function
  - （RVA 可從 PE 的 Export Directory 查，或用 IDA 事先算好）

Step 2：算 WinExec 位址
  - 從 kernel32 的 Export Directory（透過 PEB-walk 或靜態計算）找 WinExec
  - winexec_addr = kernel32_base + rva_of_WinExec

Step 3：覆寫 ret addr
  - cmd_str = b"cmd.exe\x00"          # 目標字串（寫進某個可寫位址）
  - payload = b"A" * offset_to_ret
  - payload += pop_rcx_gadget         # 設 RCX = "cmd.exe" 位址（Windows x64：第1引數是 RCX）
  - payload += p64(cmd_str_addr)
  - payload += pop_rdx_gadget         # 設 RDX = 1（SW_SHOWNORMAL）（第2引數是 RDX）
  - payload += p64(1)
  - payload += ret_gadget             # stack alignment（Windows x64 要求呼叫前 rsp 16-byte aligned）
  - payload += p64(winexec_addr)
  - send(payload)

Result: cmd.exe 彈出
─────────────────────────────────────────────────────────────
```

**兩版骨架的關鍵差異**：

1. **引數暫存器**：Linux 是 `RDI`（第一引數），Windows 是 `RCX`。搞混這個，ROP chain 一定失敗。
2. **Stack alignment**：Windows x64 ABI 要求在 `call` 之前 `rsp` 必須是 16 的倍數（有時需要 `add rsp, 8` 或多插一個 `ret` gadget 做對齊）。Linux x64 也有這個要求，但 Linux CTF 題有時不嚴格——Windows 的 `WinExec` 等 API 在 debug 模式下經常有 `assert` 檢查對齊，不對齊直接 crash。
3. **Shadow space（home space）**：Windows x64 要求 caller 在呼叫前在 stack 上預留 32 bytes（4 個引數大小）的「shadow space」給 callee 使用。這在 ROP chain 裡通常用 `sub rsp, 0x28` 之類的 gadget 處理。Linux 沒有這個要求。

> **未實測，理論預期**：以上骨架的細節（正確的 gadget、stack 對齊處理、shadow space）在 Ch 23 的 DEP + ROP 章會補齊並有可跑的範例。

## 心態調整清單：Windows pwn 新手最常帶錯的 5 個 Linux 假設

### 假設 1：「我能 leak 到 libc 基址，就找得到所有我需要的東西」

**在 Linux**：對。glibc 是一個整合的 shared library，`system`、`execve`、malloc hooks 都在同一個 `.so` 裡，算一次 offset 就全搞定。

**在 Windows**：`WinExec` 在 `kernel32.dll`，`VirtualAlloc` 在 `kernelbase.dll`，heap 管理函數在 `ntdll.dll`。三個不同的模組，各自有各自的基址。Leak 了 ntdll 的基址，還要再 leak（或算出）kernel32 的基址，才能用 `WinExec`。

**調整後的心態**：Windows exploit 通常需要 leak 兩個以上的模組基址，或者用 PEB-walk 動態解析需要的函數位址。

### 假設 2：「ROP gadget 在 libc/ntdll 裡很多，一定找得到我要的」

**在 Linux**：通常對。glibc 很大，gadget 密度高，`pop rdi; ret` 這類簡單 gadget 一定有。

**在 Windows**：Windows x64 的 calling convention（Microsoft ABI）和 Linux 不同，需要的 gadget（`pop rcx; ret`、`pop rdx; ret`、`sub rsp, 0x28; ret`）在 ntdll/kernel32 裡**不一定**像 Linux 那樣容易找到。Windows 系統 DLL 有時被 CFG 保護，indirect call 的 target 受限，進一步縮小有效 gadget 集合。使用 `rp++` 或 `mona.py` 掃出 gadget 後要仔細驗證每個 gadget 是否在 CFG 的 valid target bitmap 裡（如果 CFG 開著的話）。

**調整後的心態**：提前用 `rp++ -f ntdll.dll --rop 5 -x 64` 掃 gadget，確認你需要的 gadget 真的存在。

### 假設 3：「overflow 到 ret addr 就行了，不用管別的」

**在 Linux**：在沒有 stack canary 和 CFG 的情況下，覆寫 ret addr 通常就夠了（加上 NOP sled 或 ROP chain）。

**在 Windows**：x86 下，stack overflow 還可能蓋到 SEH handler——你不想意外觸發 SEH 走到錯誤的處理路徑（除非你是故意做 SEH overwrite）。x64 下，SEH 不在 stack 上，但 /GS cookie 在 ret addr 前面，你得先設法繞過或用正確的 cookie 值。而且 Windows 的 exception handler（`UnhandledExceptionFilter`）在程式 crash 時的行為和 Linux 的 `SIGSEGV` 不同，有時會彈出「這個程式停止運作」對話框，讓 exploit 的時間特性不同。

**調整後的心態**：Windows x64 CTF 的基礎 pwn 題，先確認 /GS 有沒有開（`dumpbin /headers` 看 `Load Config` 的 `Security Cookie RVA`），再決定攻擊路徑。

### 假設 4：「shellcode 就是跳到 RWX 然後執行」

**在 Linux**：對，前提是你能把某個頁面設成 `PROT_READ|PROT_WRITE|PROT_EXEC`。

**在 Windows**：如果目標 process 開了 **ACG（Arbitrary Code Guard）**，process 不允許動態生成可執行程式碼——即使你呼叫 `VirtualProtect` 把頁面設成 `PAGE_EXECUTE_READWRITE`，也會失敗。ACG 開著的情況下，你只能走 data-only attack 或 ROP chain，不能執行自製 shellcode。Ch 36 細講 ACG。

**調整後的心態**：先確認目標 process 的 mitigation policy（`winchecksec` 或 `GetProcessMitigationPolicy`），再決定是走 shellcode 還是純 ROP。

### 假設 5：「Python pwntools 在 Windows 上一樣好用」

**在 Linux**：pwntools 設計就是以 Linux 為主，`process()`、`gdb.attach()`、`rop.chain()` 全部無縫。

**在 Windows**：pwntools 有 Windows 支援，但功能比 Linux 版閹割：`gdb.attach()` 不能用（要換成 `windbg.attach()` 或自己寫 ctypes）；某些 `shellcraft` 的 shellcode 是 Linux 版的，要確認用的是 Windows 版；`ELF()` 要換成 `PE()`（pwntools 的 `PE` class 功能比 `ELF` 弱）。

**調整後的心態**：Windows pwn 的 scripting 工具組：**pwntools 的 socket/tube 功能**（對 remote target）+ **Python ctypes**（本機驗證 Win32 API）+ **WinDbg 的 Python scripting（pykd）**（除錯器層）。這三個組合比單靠 pwntools 更實際。

## 底層機制：翻譯字典的完整流程圖

```
攻擊骨架（通用）：
  漏洞觸發 → 建立讀/寫原語 → info leak → 控制流劫持 → payload 落地

Linux 實例：
  stack bof     →  arbitrary write  →  leak GOT     →  ret addr 覆寫  →  system("/bin/sh")
  heap uaf      →  tcache poison    →  leak libc    →  __malloc_hook  →  one_gadget
  fmt string    →  任意讀/寫        →  leak stack   →  GOT 覆寫       →  execve

Windows 對應：
  stack bof     →  arbitrary write  →  leak stack   →  ret addr 覆寫  →  WinExec("cmd.exe")
  heap uaf      →  LFH bucket ctrl  →  leak ntdll   →  vtable 覆寫    →  ROP→WinExec
  fmt string    →  任意讀           →  leak PEB/Ldr →  IAT 覆寫       →  目標函數

關鍵映射差異：
  GOT → IAT（機制不同，IAT 是 load-time resolved，可寫性需動態查）
  libc base → ntdll/kernel32 base（兩個不同模組各有各的基址）
  system() → WinExec/CreateProcess（引數放 RCX，不是 RDI）
  one_gadget → 完整 ROP chain（沒有捷徑）
  __malloc_hook → RtlAllocateHeap callback / ntdll function pointer（位置不同）
```

## 對比與取捨

| 攻擊步驟 | Linux（典型） | Windows（典型） | 關鍵差異 |
|---|---|---|---|
| 洩漏目標 | GOT entry 位址推算 libc base | stack/heap 上的指標推算 ntdll/kernel32 base | Windows 需要洩漏正確的模組 |
| 找目標函數 | `elf.symbols['system']` + libc base | RVA from Export Directory + module base | Windows 要查 PE Export Table |
| 控制第一引數 | `pop rdi; ret` gadget | `pop rcx; ret` gadget | x64 calling convention 完全不同 |
| Stack alignment | 8-byte aligned（通常自動滿足）| 16-byte aligned + 32-byte shadow space | Windows 嚴格，不對齊直接 crash |
| 落地 payload | `jmp rsp` → shellcode（NX 開著要 ROP） | `jmp rsp` → shellcode（ACG 開著就不行）| ACG 禁 shellcode |
| 緩解旗標來源 | ELF program header（`checksec`） | PE DllCharacteristics + Load Config（`winchecksec`）| 查的地方不同 |

## 踩雷集錦

1. **「Windows x64 的引數用 RDI/RSI，和 Linux 一樣」**：完全錯誤。Windows x64 ABI：`RCX, RDX, R8, R9`；Linux x64 SysV ABI：`RDI, RSI, RDX, RCX, R8, R9`。在 ROP chain 裡搞混這個，payload 送出去函數根本拿到錯誤的引數。

2. **「Leak 到 ntdll 就能用 WinExec」**：`WinExec` 在 `kernel32.dll`，不在 `ntdll.dll`。你 leak 到 ntdll 的位址，還要再算出（或 leak）`kernel32` 的基址。如果 `kernel32` 的基址可以從 ntdll 某個全域指標推算（偶爾可以），才能一步到位。否則需要第二次 leak。

3. **「Windows 沒有 /proc，所以不能 enumerate 模組」**：可以，方法是走 `PEB->Ldr`。每個已載入的 DLL 都在 `_LDR_DATA_TABLE_ENTRY` 的鏈結串列裡，可以用 Python ctypes 直接讀（Ch 5 有完整實作），也可以用 `CreateToolhelp32Snapshot` 這個 Win32 API 枚舉。

4. **「格式字串在 Windows 一樣打 %n 讀寫」**：在 Win XP SP2 之後，Microsoft 的 CRT 預設把 `printf %n` 設成無效操作（呼叫 invalid parameter handler）。如果目標用的是 MSVC CRT，`%n` 不會寫入記憶體，而是觸發 exception。用 mingw 的 CRT（UCRT 或 msvcrt）行為略有不同，但也不保證。Windows 的格式字串漏洞利用路徑和 Linux 不同，需要查目標的 CRT 行為。

5. **「Shadow space 不重要，CTF 題不會在意這種細節」**：在真實 Windows 環境（非 CTF），`WinExec`、`CreateProcess` 等 API 在 debug 模式下確實會因 stack 不對齊或 shadow space 不足而 crash，而且 crash 發生在 callee 的 prologue，不在 overflow 觸發點，所以除錯時很容易找錯地方。現代 Windows CTF 題已經開始考這個細節了。

## 進階：再往深一層

### Windows 的「gadget 搜尋」有哪些工具

- **`rp++`**（跨平台，最快）：`rp++ -f C:\Windows\System32\ntdll.dll --rop 5 -x 64 > ntdll_gadgets.txt`
- **`mona.py`**（WinDbg / Immunity Debugger 外掛）：`!mona rop -m ntdll.dll -cpb "\x00"` — 直接在除錯器內搜尋並生成 ROP chain 骨架，是 Corelan 風格的標準流程
- **`ropper`**：`ropper -f ntdll.dll --type rop --arch x86_64`

搜出來的 gadget 如果目標有 CFG，要額外確認每個 gadget 的位址是否在 CFG valid target bitmap 裡（Ch 32 細講），否則間接跳過去會被 CFG 攔截。

### import-free shellcode 的 PEB-walk

Ch 25 的主題，但這裡先給你骨架：

```asm
; Windows x64 shellcode 開頭標準範本（偽組語，未實測）
; 目標：找到 kernel32.dll 的基址

xor rdx, rdx
mov rdx, gs:[rdx+0x60]   ; GS:[0x60] = PEB 位址（x64 Windows 固定）
mov rdx, [rdx+0x18]       ; PEB->Ldr（_PEB_LDR_DATA*）
mov rdx, [rdx+0x20]       ; Ldr->InMemoryOrderModuleList.Flink
; 第一個 entry 是自己（.exe）
mov rdx, [rdx]            ; .Flink（ntdll entry）
mov rdx, [rdx]            ; .Flink（kernel32 entry，load order 第三）
mov rdx, [rdx+0x20]       ; LDR_DATA_TABLE_ENTRY->DllBase = kernel32.dll 基址
; 之後用 rdx 走 kernel32 的 Export Directory 找 WinExec
```

> **未實測，理論預期**：上面的 offset 在 Win11 x64 應該正確（`PEB` 在 `GS:[0x60]`、`Ldr` 在 PEB `+0x18`、`InMemoryOrderModuleList` 在 `_PEB_LDR_DATA +0x20`），但以你的環境用 `dt ntdll!_PEB` 和 `dt ntdll!_PEB_LDR_DATA` 驗證 offset 為準。

## 動手練習

**任務**：用 Python ctypes 做一個「Windows 版的 /proc/self/maps」——從 `GS:[0x60]` 拿 PEB 位址，走 `PEB->Ldr->InMemoryOrderModuleList`，列出所有已載入模組的 base address 和名稱（DllBase + FullDllName）。

提示：`GS:[0x60]` 在 Python ctypes 裡需要用 `ctypes.windll.ntdll.NtCurrentTeb()` 或 inline assembly 工具（如 `ctypes.WinDLL` 配合 tiny stub）——Ch 5 會給完整實作，這個練習先嘗試用 `EnumProcessModules` 做 Windows 版的模組列舉，觀察輸出格式和 `/proc/self/maps` 的差異。

```python
# 真實可跑（Python 3.12 + ctypes，本機 Win11 x64）
import ctypes
import ctypes.wintypes

psapi = ctypes.WinDLL("psapi")
kernel32 = ctypes.WinDLL("kernel32")

hProcess = kernel32.GetCurrentProcess()
hMods = (ctypes.wintypes.HMODULE * 1024)()
needed = ctypes.wintypes.DWORD()

if psapi.EnumProcessModules(hProcess, ctypes.byref(hMods), ctypes.sizeof(hMods), ctypes.byref(needed)):
    count = needed.value // ctypes.sizeof(ctypes.wintypes.HMODULE)
    for i in range(count):
        name = ctypes.create_unicode_buffer(260)
        psapi.GetModuleFileNameExW(hProcess, hMods[i], name, 260)
        print(f"0x{hMods[i]:016x}  {name.value}")
```

跑出來的輸出和 `/proc/self/maps` 做對比：兩邊都能看模組基址，但 Windows 沒有 permission 資訊（要用 `VirtualQuery` 額外查每個頁面的保護屬性）。

## 本章重點整理

- 這張「翻譯字典」（ELF↔PE、GOT/PLT↔IAT、ld.so↔LDR、system↔WinExec……）是本課全程的骨架；每次遇到新的 Windows 概念，先問「Linux 對應是什麼、差在哪」。
- 最重要的三個具體差異：(1) x64 calling convention `RDI`→`RCX`、(2) 沒有 one_gadget 需要完整 ROP chain、(3) 洩漏的模組基址需要對應正確的 DLL（ntdll ≠ kernel32）。
- Windows shellcode 的開頭幾乎固定是「PEB-walk 找 kernel32 → walk Export Directory 找函數位址」，沒有 `dlsym` 可用。
- seccomp 在 Windows 沒有直接對應物；Process Mitigation Policy（ACG / Win32k lockdown）是功能接近但機制完全不同的替代品。

## 自我檢核

- [ ] 不看表，能說出 Windows x64 函數呼叫的前四個引數暫存器（按順序），以及和 Linux x64 SysV ABI 的差異
- [ ] 能解釋「PEB-walk」為什麼是 Windows shellcode 的標準開頭，而不是直接呼叫 `GetProcAddress`
- [ ] 能說出「為什麼 leak 到 ntdll 基址，不代表能直接呼叫 WinExec」
- [ ] 面試被問「Linux 的 seccomp 在 Windows 上的對應物是什麼」，能說出 Process Mitigation Policy 和兩者設計差異的一句話
- [ ] 能說出 shadow space（home space）是什麼，以及在 ROP chain 裡什麼時候要處理它
- [ ] 不看筆記，能說出 Windows x64 ASLR 對 image 有幾位熵（大概），以及這對 brute-force 的影響

## 延伸閱讀

### 官方文件

- **[x64 calling convention — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/build/x64-calling-convention)**
  - **讀哪裡**：「Parameter passing」和「Return values」兩節，把暫存器順序和 shadow space 的規則看清楚
  - **學什麼**：Microsoft x64 ABI 的完整規格——這是你寫 ROP chain 時最常翻的參照
  - **和本章的關聯**：本章「Windows x64 引數用 RCX 不是 RDI」的權威來源；Ch 40 的 ABI 章也以這份為基礎
  - **前提知識**：知道什麼是 calling convention 和 stack frame

- **[PE Format — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)**
  - **讀哪裡**：「Optional Header」的 `DllCharacteristics`、「Load Config」目錄、「Export Directory」
  - **學什麼**：PE 結構的官方定義；ELF 和 PE 哪裡對應、哪裡沒有對應，這份文件是最終仲裁
  - **和本章的關聯**：本章 ELF↔PE 對照的底層依據；Ch 3 的 PE 格式章的前置讀物
  - **前提知識**：ELF 格式的基本結構

### 部落格 / 技術文章

- **[Corelan — Exploit writing tutorial part 9: Introduction to Win32 shellcoding](https://www.corelan.be/index.php/2010/02/25/exploit-writing-tutorial-part-9-introduction-to-win32-shellcoding/)** — Peter Van Eeckhoutte
  - **讀哪裡**：「Finding the base address of kernel32.dll」那一節，是 PEB-walk 組語的經典解說
  - **學什麼**：PEB-walk 找 kernel32、Export Directory 走訪找函數位址的完整實作邏輯
  - **和本章的關聯**：本章「shellcode PEB-walk 骨架」的詳細展開；Ch 25 的直接前置
  - **前提知識**：基本 x86 組語；PEB/LDR 的概念（本章和 Ch 5 涵蓋）

- **[j00ru — Windows x64 System Call Table](https://j00ru.vexillium.org/syscalls/nt/64/)** — Mateusz Jurczyk
  - **讀哪裡**：找任意幾個你熟悉的 Linux syscall（read/write/mmap）的對應 Windows Nt* 函數，看它的 SSN 跨版本怎麼變
  - **學什麼**：syscall 號碼不固定這件事的直觀感受；Win7 到 Win11 跨了多少個 SSN 版本
  - **和本章的關聯**：本章 syscall SSN 不固定那條的具體佐證；Ch 7 的直接準備
  - **前提知識**：知道什麼是 syscall 即可

### 書籍

- **《The Shellcoder's Handbook, 2nd Edition》（第 11–13 章，Windows 部分）** — Anley, Heasman, Linde, Richarte（Wiley）
  - **讀哪裡**：Ch 11「Overflows on Windows」和 Ch 12「Windows Shellcode」，雖然年代稍舊（2007），但 PEB-walk 和 SEH 的基礎邏輯沒有過時
  - **學什麼**：從「做過 Linux exploit 的人第一次看 Windows」這個視角，整理了哪些直覺可用、哪些要丟掉
  - **和本章的關聯**：這本書做了和本章相同的「遷移直覺」工作，可以對照看
  - **前提知識**：本章全部讀完；基本 x86 組語

進入下一章，我們開始真正挖 Windows 的執行環境 internals。第一個目標是 PE 格式——把 ELF 你已知的知識，對應到 PE 的每一個對應（和不對應）的地方。

→ [Ch 3 — PE 格式深挖（vs ELF）](./03-pe-format.md)
