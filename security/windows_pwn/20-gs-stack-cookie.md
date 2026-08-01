# Ch 20 — /GS stack cookie：機制與繞過思路

> **目標**：搞清楚 MSVC `/GS` 的 `__security_cookie` 是怎麼產生的、存在哪裡、如何在 prologue/epilogue 使用，以及它**選擇性保護**哪些函式這個最大的繞過切入點。學完能說清楚 `/GS` 和 GCC stack canary 的三個核心差異，以及四種主要繞過思路（附條件）。

> **環境**：mingw-w64 i686 GCC 14.2（本機已驗）；MSVC 相關行為標「未實測，理論預期」（需安裝 MSVC C++ workload + cdb）。

---

## 為什麼需要這個？

Ch 19 的靶是純「沒有防護」的世界，76 bytes overflow 直接蓋掉 saved EIP 就搞定。現實中 MSVC 編出來的 Windows binary 幾乎都帶 `/GS`，GCC 也有 `-fstack-protector`。這章的任務是：

1. 把 `/GS` 的機制搞透（不是「有個 cookie 就過不了」的黑盒概念）
2. 認識它**設計上的漏洞**——選擇性保護、覆蓋 SEH handler 的路、`__security_cookie` 沒有加密 saved EBP 的版本
3. 為 Ch 21（SEH overwrite）搭橋：SEH overwrite 最初就是為了在有 `/GS` 的環境下繼續利用 stack overflow 而設計的

**和 Linux 對照**：Linux 的 GCC canary 和 `/GS` 在概念上相同（在 buf 和 saved RIP 之間插隨機值），但實作、初始化方式、儲存位置、保護啟發式都有重要差異——這章把這些差異說清楚。

---

## 先建立直覺：cookie 是一道門鎖，但這扇門不是每間房間都有

想像每個函式的 stack frame 是一個辦公室，buffer overflow 是一個可以從走廊衝進辦公室的攻擊者。`/GS` 的做法是：

**在門口放一個偵測器（cookie）**。攻擊者從走廊（buf）衝進來，一定會先碰到偵測器，觸發警報，再才能到 saved EIP。

問題是：**不是每個辦公室都有偵測器**。MSVC 的 `/GS` 用啟發式決定哪些函式值得保護：**只有函式裡有「夠大的」char buffer（或 pointer 型別的局部變數，某些情況）才插 cookie**。只有 int、long 之類的 scalar local vars，不插。

這個啟發式的設計初衷是「只在真正有 overflow 風險的函式插 cookie 以節省效能」，但它同時造成了繞過的切入點——找一個有漏洞但**沒有被 `/GS` 保護**的函式。

---

## `/GS` 機制：`__security_cookie` 的完整生命週期

### 1. Cookie 的產生

`__security_cookie` 是一個 **per-module 的全域變數**，定義在 MSVC CRT 的 `gs_cookie.c`（`seccheck.obj`）裡：

```c
/* MSVC CRT 內部（簡化） */
extern UINT_PTR __security_cookie;  /* 全域；預設初始值 0xBB40E64E（x86）或 0x2B992DDFA23249D6（x64） */
```

預設初始值是個非零的 magic number，用途是「如果初始化函式沒被呼叫（非常早期的 crash），cookie 仍然是非零值，避免 0 被當成合法 cookie」。

**真正的隨機化初始化** 在模組載入時，由 `__security_init_cookie()`（MSVC CRT 初始化鏈的一部分）執行：

```c
/* 理論實作（MSVC CRT 無官方完整原始碼，從逆向整理）*/
void __cdecl __security_init_cookie(void) {
    UINT_PTR cookie;

    /* 取數個低熵源做 XOR 混合 */
    cookie = (UINT_PTR)GetCurrentProcessId();
    cookie ^= (UINT_PTR)GetCurrentThreadId();

    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    cookie ^= ft.dwLowDateTime;
    cookie ^= ft.dwHighDateTime;

    LARGE_INTEGER pc;
    QueryPerformanceCounter(&pc);
    cookie ^= pc.LowPart;
    cookie ^= pc.HighPart;

    /* 避免 cookie 變成某些「壞值」 */
    if (cookie == 0xBB40E64E)  cookie = 0xBB40E64F;   /* 不能等於預設值 */
    if ((cookie & 0xFFFF0000) == 0) cookie |= (cookie | 0x4711) << 16;

    __security_cookie = cookie;
    /* 備份 XOR 反向確認用 */
    __security_cookie_complement = ~cookie;
}
```

> **未實測，理論預期**：上面是根據公開逆向分析整理的實作概要，確切版本因 VS 版本而異。在 WinDbg 裡，`bp ucrtbase!__security_init_cookie; g; dq &__security_cookie L1` 可以看到初始化後的實際值。

**關鍵特性**：
- **Per-module**：每個 DLL 和 EXE 各有自己的 `__security_cookie`，互不相同
- **Process 啟動時隨機化**：進程啟動後固定，同一進程內不同執行緒看到的 cookie **相同**
- **不是 per-thread**：這和 Linux GCC 的 canary 不同（Linux 存在 `fs:0x28`，per-thread）

> **和 Linux GCC canary 的對比**：
> - Linux：`__stack_chk_guard` via `fs:0x28`（TLS，per-thread；每條執行緒各有一份）
> - Windows GCC（mingw）：全域 `__stack_chk_guard`（per-process，本機實測：每次啟動重新隨機化，但同一進程共用）
> - Windows MSVC `/GS`：全域 `__security_cookie`（per-module）
>
> 本機實測（mingw32 `-fstack-protector-all`，見下節）確認：Windows GCC 的 `__stack_chk_guard` 在同一進程的不同執行緒之間是**相同的**，因為它是全域變數而非 TLS。

### 2. Cookie 在 Prologue 存入 Stack

MSVC 在每個被 `/GS` 保護的函式 prologue 插入（理論預期，需 MSVC 編才能觀察）：

```nasm
; /GS x86 prologue（MSVC 編出；未實測）
push    ebp
mov     ebp, esp
sub     esp, N           ; N = locals + cookie 空間

; 取全域 cookie，XOR saved EBP，存到 stack
mov     eax, dword ptr [__security_cookie]
xor     eax, ebp                            ; ← 關鍵：與 EBP 做 XOR
mov     dword ptr [ebp-4], eax              ; 存在 saved EBP 正上方（EBP-4）
```

**為什麼 XOR EBP？**：讓 cookie 的 stack 值和函式位址綁定——即使攻擊者知道全域 `__security_cookie` 的值，還要知道這個 frame 的 EBP 值才能算出正確的 stack cookie 值。這讓「假設洩漏了 `__security_cookie` 就能直接填 cookie」的思路失效（還要配合 EBP leak）。

> **未實測**：請用 MSVC 裝好後，`cl /GS target.c`，在 WinDbg 裡 `bp target!vuln; g; u eip` 看 prologue 的 XOR 指令。

### 3. Cookie 在 Epilogue 驗證

```nasm
; /GS x86 epilogue（MSVC 編出；未實測）
; 返回前，取回 stack cookie、還原 XOR、和全域值比對
mov     ecx, dword ptr [ebp-4]   ; 取回 stack 上存的 cookie
xor     ecx, ebp                 ; 還原 XOR
call    __security_check_cookie   ; 比對 ecx == __security_cookie？
; 如果不等 → __report_gsfailure → 程式終止
add     esp, N
pop     ebp
ret
```

`__security_check_cookie` 的邏輯極簡單：`if (cookie != __security_cookie) abort()`。失敗就呼叫 `__report_gsfailure`，後者會觸發 Windows Error Reporting（WER）並終止進程，**不呼叫 SEH handler**（這很重要，下面細講）。

### 4. Stack 上 Cookie 的位置

被 `/GS` 保護的 x86 stack frame 佈局：

```
高位址
┌────────────────────────────────────────────────────────┐
│ 呼叫者 frame                                           │
│ ...                                                    │
│ argument 1             EBP+8                           │
│ saved EIP              EBP+4  ← 攻擊目標               │
├────────────────────────────────────────────────────────┤ ← EBP
│ saved EBP              EBP+0                           │
│ Stack Cookie           EBP-4  ← /GS 的門衛              │
│   = __security_cookie XOR EBP                         │
│                                                        │
│ local vars             EBP-8, -12, ...                 │
│                                                        │
│ char buf[N]            EBP-X  ← overflow 起點          │
└────────────────────────────────────────────────────────┘
低位址（ESP）
```

攻擊路徑：buf → ... → cookie（必須正確）→ saved EBP → saved EIP

---

## GCC `/fstack-protector` 的實測對照

mingw32 GCC 的 canary 行為可以本機直接看（不需要 MSVC）。這給了我們一個能真實觀察的對照組。

### 反組譯（本機實測）

```nasm
; mingw32 gcc -O0 -fstack-protector-all 的 vuln() prologue（AT&T 語法）
004014ff <_vuln>:
  4014ff:  55                push   %ebp
  401500:  89 e5             mov    %esp,%ebp
  401502:  83 ec 78          sub    $0x78,%esp     ; 比無防護多 0x20（cookie + padding）

  ; 取全域 canary 存到 stack ebp-0xc
  40150b:  a1 b0 d0 40 00    mov    0x40d0b0,%eax  ; ← __stack_chk_guard 全域位址（bss 段）
  401510:  89 45 f4          mov    %eax,-0xc(%ebp) ; 存到 stack（ebp-0xc）

  ; ...（主要邏輯 strcpy 等）...

  ; epilogue 驗證
  40153b:  8b 45 f4          mov    -0xc(%ebp),%eax ; 取回 stack 上的 canary copy
  40153e:  2b 05 b0 d0 40 00 sub    0x40d0b0,%eax   ; 減去全域值：應該是 0
  401544:  74 05             je     40154b <_vuln+0x4c>  ; 相等就繼續返回
  401546:  e8 a5 10 00 00    call   4025f0 <___stack_chk_fail>  ; 不相等就崩
  40154b:  c9                leave
  40154c:  c3                ret
```

**注意**：GCC 用 `sub` + `je`（等價於 `cmp + je`）；**沒有** MSVC 的 `XOR EBP`。GCC 的 canary copy 就是純粹從全域 `__stack_chk_guard` 複製，不和 EBP 混合。

### GCC stack frame 佈局（本機實測）

```
ebp-0x4c  buf[64]    ← overflow 起點
           ...（padding）
ebp-0x0c  canary      ← 全域 __stack_chk_guard 的 copy
ebp+0x00  saved EBP
ebp+0x04  saved EIP
```

```
距離：buf 到 canary = 0x4c - 0x0c = 64 bytes（正好等於 buf 大小）
      canary 本身     4 bytes（必須正確）
      canary 到 saved EIP = 0x0c + 4 = 16 bytes
```

**本機實測確認** `__stack_chk_guard` 值：每次執行隨機，但同一進程內不變：

```console
$ ./canary_test.exe    # 第一次
__stack_chk_guard = 0xdbf7660b

$ ./canary_test.exe    # 第二次（不同進程）
__stack_chk_guard = 0x2b8c21cb
```

### MSVC vs GCC 差異對照表

| 面向 | MSVC `/GS`（`__security_cookie`） | GCC `-fstack-protector`（`__stack_chk_guard`） |
|---|---|---|
| Cookie 儲存 | 全域變數，per-module | 全域變數（Win mingw）/ TLS `fs:0x28`（Linux） |
| Stack 上的值 | `__security_cookie XOR EBP` | `__stack_chk_guard` 的直接 copy |
| 失敗處理 | `__report_gsfailure` → WER → 終止 | `__stack_chk_fail` → 通常是 `abort()` |
| 保護啟發式 | 只保護有 char buffer 或 pointer 的函式 | 預設保護「有 buffer 的函式」，`-all` 強制全部 |
| 對應 SEH | `__report_gsfailure` 不走 SEH chain | `abort()` 可能走 signal/SEH，但 cookie 失敗直接終止 |
| 初始化 | `__security_init_cookie`（進程啟動） | 進程啟動時用 OS 亂數初始化 |

---

## `/GS` 的覆蓋啟發式：最大的繞過切入點

這是整章最重要的一個知識點。**`/GS` 不是保護所有函式**，它的啟發式規則（根據 MSVC 的官方文件和逆向觀察）大致是：

**會插 cookie 的情況**：
- 函式有 `char`、`unsigned char`、`wchar_t` 的 buffer（陣列）
- 函式有局部指標變數（pointer-type locals）
- 函式用了 `_alloca`

**不會插 cookie 的情況**：
- 函式只有 `int`、`long`、`float`、`double` 等 scalar 型別
- 函式非常短（可能被 inline 掉）
- 標注了 `__declspec(safebuffers)` 的函式（明確告訴 MSVC「這個函式不需要 GS」）

```c
/* 這個函式有 cookie（有 char buffer） */
void protected_func(const char *input) {
    char buf[64];
    strcpy(buf, input);   /* /GS 保護的目標 */
}

/* 這個函式沒有 cookie（只有 int） */
int unprotected_func(int x) {
    int arr[16];          /* int 陣列，不觸發 /GS */
    arr[0] = x;
    return arr[0];
}

/* 明確標注不保護 */
__declspec(safebuffers)
void explicitly_unsafe(char *input) {
    char buf[64];
    strcpy(buf, input);   /* 沒有 cookie！ */
}
```

> **未實測，理論預期**：用 MSVC 編這三個函式，用 `dumpbin /disasm target.obj` 看三個函式的 prologue 是否有插 cookie 指令。`__declspec(safebuffers)` 在某些效能敏感場景（遊戲、音訊框架）被用來關掉特定函式的 GS，正是安全漏洞的溫床。

**繞過切入點**：如果漏洞在一個「因為只有 int buffer 而沒被保護的函式」裡，`/GS` 完全沒用。Pwn 開始時要做的事之一是：用 `dumpbin /disasm` 確認漏洞函式的 prologue 裡**是否有 `mov eax, [__security_cookie]; xor eax, ebp; mov [ebp-4], eax` 這段**，如果沒有——`/GS` 不擋你。

---

## 攻擊 `/GS` 的四條路

### 路線一：蓋 SEH handler 而非 saved EIP

這是 Ch 21 的核心。關鍵觀察是：**`/GS` 的 cookie 在 saved EIP 前面，但 SEH record 的 Handler 欄位在 cookie 後面**——不對，讓我說清楚方向。

x86 stack frame 的完整版（含 SEH record，MSVC `__try`）：

```
高位址
┌────────────────────────────────────────────────────┐
│ argument 1               EBP+8                     │
│ saved EIP                EBP+4  ← /GS 擋在這之前  │
├────────────────────────────────────────────────────┤ ← EBP
│ saved EBP                EBP+0                     │
│ Stack Cookie             EBP-4  ← /GS 的守衛       │
│ EH state (TryLevel)      EBP-8  ← MSVC __try 插的  │
│ ScopeTable 指標           EBP-C                    │
│ SEH Record Next          EBP-10 ← EXCEPTION_REGISTRATION_RECORD  │
│ SEH Record Handler       EBP-14 ← 攻擊目標！        │
│ ...                                                │
│ char buf[N]              EBP-X  ← overflow 起點    │
└────────────────────────────────────────────────────┘
低位址（ESP）
```

等等——SEH record 在 **EBP 下方**（低位址），**buf 在更低位址**。overflow 往高位址方向寫，所以路徑是：

```
buf → ... → SEH record Handler → SEH record Next → EH state → Cookie → saved EBP → saved EIP
```

**buf 到 SEH Handler 的距離比 buf 到 Cookie 的距離短！**

這意味著：overflow 先碰到 SEH Handler，才碰到 Cookie。蓋掉 SEH Handler 時，Cookie 還是原始值（沒被蓋）。然後攻擊者觸發例外——OS 在呼叫 handler 之前**不會驗證 cookie**（cookie 是 prologue/epilogue 的事，例外分發走的是另一條路）。

所以 `/GS` 保護了 saved EIP，**但沒保護 SEH handler**。這就是 SEH overwrite 技法的正當性：繞過 `/GS`，直接攻擊 SEH。

> **等一下，這只在有 `__try` 的函式裡才有 SEH record**？是的。但即使漏洞函式本身沒有 `__try`，上層呼叫者（main / WinMain / dispatcher）通常都有。stack overflow 把 SEH record 蓋掉，觸發例外，分發器找鏈——找的是整條 SEH chain，不只是漏洞函式的那一個。Ch 21 詳講這個利用流程。

### 路線二：攻擊未受 `/GS` 保護的函式

前面說了，`/GS` 的啟發式不保護「只有 scalar locals」的函式。如果漏洞點是 int 陣列越界，`/GS` 完全沒有任何作用。

### 路線三：蓋 Cookie 之前的局部指標

考慮這種佈局（MSVC 在某些情況會這樣排列 locals）：

```c
void vuln2(void) {
    char buf[32];
    char *dest;     /* 局部指標 */
    /* dest 在 buf 的高位址方向 */
    dest = some_ptr;
    strcpy(buf, get_input());  /* overflow 能蓋 dest */
    strcpy(dest, another_input);  /* 用被蓋的 dest 做第二次 strcpy */
}
```

如果 overflow 能蓋掉 `dest` 指標，而 `dest` 在 stack 上的位置比 cookie 更靠近 buf（低位址），那攻擊者可以讓 `dest` 指向任意記憶體，在第二次 `strcpy` 時寫任意內容到任意位址——這是 write-what-where 原語，完全不需要蓋 saved EIP，也就不需要過 cookie 這道關。

> **未實測，理論預期**：MSVC 的 `/GS` 啟用時，會把「危險」的局部指標排列在 stack 上的保護位置——把指標放在 buffer **上方**（高位址），cookie 在更上方，試圖讓 overflow 先過 cookie 才碰指標。但這個重排規則不是 100% 覆蓋所有情況，且在 SEH record 夾入後佈局更複雜。

### 路線四：Leak `__security_cookie` 然後填正確的 cookie

如果你有 info leak（format string、越界讀、UAF 等），可以讀出 `__security_cookie` 的值。此時：

```python
# 假設已 leak 出 __security_cookie = 0x12345678
# 假設已 leak 出目標 frame 的 EBP = 0xDEADC0DE（需要 EBP leak）
cookie_on_stack = 0x12345678 ^ 0xDEADC0DE  # MSVC 的 XOR EBP 版本
# GCC 版本（沒有 XOR）：cookie_on_stack = 0x12345678
```

然後在 overflow payload 裡，把 cookie 那 4 bytes 填成正確的 `cookie_on_stack`，cookie 驗證就會通過。需要同時有 `__security_cookie` 的 leak 和目標 frame EBP 的 leak（MSVC 版），難度較高。

---

## `__report_gsfailure`：cookie 失敗後發生什麼

Cookie 不匹配時，`__security_check_cookie` 呼叫 `__report_gsfailure`：

1. **設定 `__proc_attached = 0`**（告訴 CRT 進程已損毀）
2. **呼叫 `SetUnhandledExceptionFilter(NULL)`**——把 UEF（UnhandledExceptionFilter）清掉，防止攻擊者的 UEF 被觸發
3. **呼叫 `RaiseFailFastException`** 或 `NtRaiseHardError`，直接送 kernel 終止
4. **不走 SEH chain**——這是設計，避免攻擊者用 SEH overwrite 在 cookie 失敗後劫持

這個設計很聰明：即使攻擊者成功蓋了 SEH handler（在 cookie 失敗前），`__report_gsfailure` 也清掉了 UEF 並直接硬終止，讓 SEH overwrite 的後手失效。所以**SEH overwrite 必須在 cookie 驗證觸發之前就控制執行流**——通過例外分發路徑，不是等 cookie 失敗。

---

## 底層機制：MSVC vs GCC 的 cookie 對比圖解

```
MSVC /GS（x86，含 XOR EBP）
═══════════════════════════════════
載入時：
  __security_init_cookie() 執行
  __security_cookie = f(PID, TID, QPC, GSTAF) = 0xABCD1234
  （全模組共用，random per run）

進入 vuln() prologue：
  cookie_on_stack = 0xABCD1234 XOR current_EBP
  mov [ebp-4], cookie_on_stack

離開 vuln() epilogue：
  mov ecx, [ebp-4]           ; 取回
  xor ecx, ebp               ; 還原 XOR
  call __security_check_cookie ; ecx == __security_cookie?
  → 不等 → __report_gsfailure → 硬終止

GCC -fstack-protector（Windows mingw，x86）
═══════════════════════════════════
啟動時：
  __stack_chk_guard = OS 亂數 = 0x9F2A3B7C
  （全域，per process，random per run）

進入 vuln() prologue：
  mov eax, [__stack_chk_guard]
  mov [ebp-0xc], eax         ; 直接 copy，不 XOR EBP

離開 vuln() epilogue：
  mov eax, [ebp-0xc]
  sub eax, [__stack_chk_guard]  ; 應該是 0
  jne → __stack_chk_fail → abort()

Linux GCC -fstack-protector（x86-64）
═══════════════════════════════════
啟動時：
  fs:0x28 = OS 亂數（per-thread TLS）

進入 vuln() prologue：
  mov rax, fs:0x28
  mov [rbp-8], rax

離開 vuln() epilogue：
  mov rax, [rbp-8]
  xor rax, fs:0x28
  jne → __stack_chk_fail
```

---

## 對比：`/GS` vs GCC canary 的三個核心差異

| 面向 | MSVC `/GS` | GCC（Windows）| GCC（Linux） |
|---|---|---|---|
| Cookie 存在哪 | 全域 `__security_cookie`（per-module） | 全域 `__stack_chk_guard`（per-process） | TLS `fs:0x28`（per-thread） |
| Stack 上的 cookie | `__security_cookie XOR EBP` | 直接 copy | 直接 copy |
| Cookie 的位置 | `EBP-4`（緊貼 saved EBP） | 取決於 gcc 版本（本機觀察 EBP-0xc） | `RBP-8`（緊貼 saved RBP） |
| 保護啟發式 | 有 char buffer 或 pointer 才插 | 視 `-fstack-protector` / `-all` 旗標 | 同左 |
| 失敗後行為 | `__report_gsfailure` 硬終止，清 UEF，不走 SEH | `abort()` | `abort()` |
| 可被 SEH overwrite 繞過 | **是**（SEH record 在 cookie 前） | 非常困難（沒有 SEH record 在 stack 上） | 無此問題（x64 table-based SEH） |

**最關鍵的一行**：MSVC `/GS` 可以被 SEH overwrite 繞過，GCC 在 Linux x64 上幾乎不存在這個攻擊面（ch 11/12 解釋過 x64 的 table-based SEH）。

---

## 踩雷集錦

1. **「有 /GS 就不能 overflow」**：錯。`/GS` 只是讓「蓋 saved EIP」這條路需要過 cookie 關卡。攻擊者可以繞過 cookie 蓋 SEH handler（Cookie 之前的那格）、或在 cookie 驗證前就讓例外發生、或從 info leak 取得 cookie 值。`/GS` ≠ 鐵板。

2. **「`/GS` 和 GCC canary 一樣」**：不一樣。最重要的差異是 MSVC 的 stack cookie 是 `__security_cookie XOR EBP`，GCC 是直接 copy。這意味著即使你洩漏了 `__security_cookie`，MSVC 版本你還需要 EBP leak 才能算出 stack 上應有的 cookie 值。

3. **「所有函式都受 /GS 保護」**：MSVC 的啟發式只保護有 char buffer 或 pointer 的函式。找一個只有 int 陣列但越界的函式——`/GS` 完全沉默。`__declspec(safebuffers)` 更是直接告訴編譯器「這個函式不要 GS」，某些性能敏感代碼庫有這個標注。

4. **「Cookie 失敗後可以 catch SEH」**：`__report_gsfailure` 明確呼叫 `SetUnhandledExceptionFilter(NULL)` 再硬終止，設計上就是要防止攻擊者透過 SEH 在 cookie 失敗後劫持控制流。試圖在 cookie 失敗後的 SEH chain 裡找 gadget 是無效的。

5. **「SEH overwrite 和 /GS 沒關係」**：恰好相反。SEH overwrite 正是**為了繞過 /GS** 而被廣泛使用的技法——因為 SEH handler 在 stack 上的位置比 cookie 更靠近 buf（更低位址），overflow 先碰到它，蓋掉 handler 後再觸發例外，整個過程 cookie 驗證根本沒機會執行。

---

## 進階：再往深一層

### MSVC `__security_cookie` 的初始化弱點

`__security_init_cookie` 用 PID + TID + 兩個時間相關值 XOR 作為 cookie 的熵來源。問題是：在某些受控環境下（容器、sandbox、特定的 exploit 場景），這些值可能有規律——比如進程 PID 是 4（system）、TID 是固定的、系統啟動時間已知。這讓 cookie **理論上可以被預測**，但在現代實作中額外加了 ASLR 的 image base 偏移，難度很高。

### SafeSEH 和 `/GS` 的配合

`/SAFESEH`（連結器旗標）在 PE 的 Load Config 目錄裡建立一個「合法 SEH handler 白名單」。`RtlIsValidHandler` 在呼叫 handler 之前驗證 handler 地址在白名單裡。有了 SafeSEH，SEH overwrite 必須選一個**在白名單裡的 gadget**（或者找一個沒有 SafeSEH 的模組裡的 gadget）。Ch 21 詳講如何用 mona.py 找符合條件的 gadget。

### EH4 vs EH3：`__except_handler4` 的 ScopeTable XOR

Ch 11 提過，MSVC 的 `__except_handler4` 把 `ScopeTable` 指標用 stack cookie XOR 加密。如果攻擊者把 `Handler` 蓋成 `__except_handler4` 的位址，呼叫時 `__except_handler4` 試著解密 `ScopeTable`（用它自己的 cookie key），得到亂值，跳進去就崩——這讓傳統的「把 Handler 指向 __except_handler4 讓它跳轉」的想法無效。正確的 SEH overwrite gadget 不應該是 CRT handler 函式。

### 面試題：為什麼 `/GS` 不能防住 heap overflow？

`/GS` 只管 stack frame 裡、saved EIP 和 saved EBP 下方的 cookie。heap 上的 metadata 和 function pointer 完全不在 `/GS` 的管轄範圍。Heap overflow 打 vtable、打 heap metadata、打 heap-based function pointer——`/GS` 完全沉默。Part 4（Ch 26–31）會展開這些。

---

## 動手練習

> **環境**：mingw32 gcc（本機可跑，不需 MSVC）。

1. 用 mingw32 GCC 編兩個版本的 `vuln32.c`：一個帶 `-fstack-protector-all`，一個不帶。
2. 對比兩個版本的反組譯（`objdump -d`）：找出帶 canary 版本的 prologue 在哪裡插了 cookie 的 store，epilogue 在哪裡做 compare。
3. 計算帶 canary 版本的 stack layout：buf 到 canary 幾 bytes？canary 到 saved EIP 幾 bytes？
4. 思考題：如果 `__stack_chk_guard` 的值被格式化字串漏洞洩漏了，你能否在不知道 EBP 的情況下填入正確的 stack cookie？（GCC 版本 vs MSVC 版本，答案不同。）
5. （進階）閱讀 MSVC 的 `/GS` 文件（延伸閱讀），找出「`__declspec(safebuffers)` 的合法用途」——它存在的理由是什麼，安全研究者應該怎麼對待它。

---

## 本章重點整理

- MSVC `/GS` 在 stack 上插入 `__security_cookie XOR EBP` 當 cookie，prologue 存、epilogue 驗，不匹配就硬終止（清 UEF、不走 SEH）。
- **選擇性保護**是最大的設計盲點：只有帶 char buffer 或 pointer 的函式才插 cookie，scalar-only 函式完全不保護。
- SEH overwrite 繞過 `/GS` 的根本原因：SEH handler 在 stack 上比 cookie 更靠近 buf（更低位址），overflow 先蓋 handler，例外分發在 cookie 驗證前就被劫持。
- GCC canary（mingw Windows）和 MSVC `/GS` 的三個核心差異：EBP XOR、per-thread vs per-module、失敗處理路徑。

---

## 自我檢核

- [ ] 不看筆記，能畫出含 `/GS` cookie 的 x86 stack frame（buf / cookie / saved EBP / saved EIP 的順序和偏移方向）
- [ ] 能說出 MSVC `__security_cookie` 和 GCC `__stack_chk_guard` 的兩個重要差異（EBP XOR 和 per-thread vs per-module）
- [ ] 能解釋「`/GS` 的選擇性保護啟發式」——哪些函式有 cookie，哪些沒有，並舉出一個繞過切入點
- [ ] 能說出 SEH overwrite 在空間上為什麼能繞過 `/GS`（SEH record 在哪個位址，cookie 在哪個位址，哪個先被 overflow 蓋到）
- [ ] 面試被問「如果你 leak 了 `__security_cookie`，能直接填 cookie 讓 /GS 過關嗎？」：能回答「MSVC 版本還需要 EBP 的值，GCC 版本不需要」

---

## 延伸閱讀

### 官方文件

- **[/GS（Buffer Security Check）— MSVC 文件](https://learn.microsoft.com/en-us/cpp/build/reference/gs-buffer-security-check)**
  - **讀哪裡**：全文，特別是「How /GS works」和「__declspec(safebuffers)」兩節
  - **學什麼**：官方描述哪些情況插 cookie、哪些情況不插；`__declspec(safebuffers)` 的語義和適用場景
  - **和本章關聯**：本章的「選擇性保護」小節直接來自這份文件的描述；讀後能對照本章的反組譯確認理解
  - **前提**：本章讀完

### 論文 / 研究

- **"Bypassing Browser Memory Protections"** — Alexander Sotirov & Mark Dowd（Black Hat US 2008）([bhusa08-sotirov-dowd-wp.pdf](https://www.blackhat.com/presentations/bh-usa-08/Sotirov_Dowd/bh08-sotirov-dowd-wp.pdf))
  - **讀哪裡**：第 3 節「Stack Cookies」和第 4 節「SafeSEH」
  - **學什麼**：從攻擊者角度系統化地分析 stack cookie 和 SafeSEH 的弱點，包括 info leak 配合 cookie bypass 的框架；這是「看懂防護背後的攻擊面」的經典示範
  - **和本章關聯**：本章的四條繞過思路，這篇論文給了更嚴謹的形式化描述
  - **前提**：本章 + Ch 11（SEH 機制）

### 部落格

- **Corelan Team — "Exploit writing tutorial part 3b: SEH Based Exploits – the sequel"** — Peter Van Eeckhoutte（[corelan.be](https://www.corelan.be/index.php/2009/07/28/seh-based-exploit-writing-tutorial-continued-just-another-example/)）
  - **讀哪裡**：開頭的「/GS vs SEH overwrite」章節分析
  - **學什麼**：Corelan 直接展示了「有 /GS 的靶怎麼用 SEH overwrite 打」的實際工作流；本章是理論，這篇是你打完 Ch 21 後來驗證的實作參照
  - **和本章關聯**：本章解釋了「為什麼可以」，這篇展示「怎麼實際操作」
  - **前提**：本章 + Ch 21 兩章都讀完

### 工具 / 逆向參考

- **[mona.py 文件 — find /GS unprotected functions](https://github.com/corelan/mona#monapy-commands)**
  - **讀哪裡**：`!mona modules` 和 `!mona seh` 的說明，看輸出裡的「GS」欄位
  - **學什麼**：`!mona modules` 列出所有載入模組的防護狀態（GS / ASLR / SafeSEH / NX），讓你一眼找到「沒有 GS 的 DLL 作為 gadget 來源」的目標
  - **和本章關聯**：本章講了選擇性保護是繞過切入點，mona 是你實際找這些切入點的工具

有了 `/GS` 機制的全貌，下一章展開 SEH overwrite——這是 Windows 漏洞利用最具代表性的技法，也是第一個在有 `/GS` 的環境下依然有效的攻擊路徑。

→ [Ch 21 — SEH overwrite：Windows 經典技法](./21-seh-overwrite.md)
