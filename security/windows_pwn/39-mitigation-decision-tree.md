# Ch 39 — 緩解總表 + 繞過決策樹

> **目標**：把 Part 3/4/5 學過的所有緩解整合成一張可查表與一棵決策樹；面對任意 Windows 目標能在五分鐘內判斷「它開了什麼、各緩解之間的依賴關係是什麼、應該走哪條攻擊路徑」；能把現代 Win11 全緩解目標和典型 CTF 題的攻擊差異說清楚。

---

Part 3/4/5 把每個緩解各自拆開：GS 的 cookie 機制、CFG 的 bitmap、CET 的 shadow stack、ACG 的動態碼禁令。讀完你知道它們各自的細節，但實戰的問題不是「這個緩解怎麼運作」，而是「**面對這個目標，我該怎麼走**」——你面對的是一個真實的 PE，同時開了七八個緩解，你必須在 10 分鐘內決定攻擊路線，不然你就在 WinDbg 裡亂摸。

這章是一張地圖，而不是更多細節。

## 為什麼需要這個？

Linux userland pwn 的決策流程你已經內化了：

```
有沒有 leak？→ 有 heap / stack / libc base 嗎？
開了什麼防護？→ NX（ROP）、PIE（需 leak base）、Full RELRO（不能打 GOT）、canary（需繞）
有什麼 primitive？→ 任意讀/任意寫/控制流劫持
```

Linux 的防護比較少、組合比較固定，你很快能過腦。Windows 的情況複雜得多：

- **緩解數量更多**：DEP / ASLR / GS / SafeSEH / SEHOP / CFG / XFG / CET-SS / CET-IBT / ACG / CIG，11 個，互相依賴。
- **緩解的粒度是 per-module**：同一個行程裡，某個 DLL 可能沒有 CFG（舊版）、某個 DLL 有 CET，而主程式只有 GS+ASLR。你不能假設全有或全無。
- **緩解的啟用需要編譯器支援 + OS 支援 + 行程政策**：三者缺一就沒有效果。Edge renderer 的 ACG 在一般桌面 exe 根本不存在。

沒有這張地圖，你每次遇到新目標都要從頭想。

## 先建立直覺：緩解的四個層次

在細節之前，先建立緩解的分類直覺：

```
  ┌─────────────────────────────────────────────────────────────────┐
  │  Layer 4：程式碼完整性（ACG / CIG）                              │
  │  「你連注入/載入新程式碼都做不到」                               │
  ├─────────────────────────────────────────────────────────────────┤
  │  Layer 3：控制流完整性（CFG / XFG / CET-SS / CET-IBT）          │
  │  「你能改指標，但 CPU/OS 在執行前攔截你」                        │
  ├─────────────────────────────────────────────────────────────────┤
  │  Layer 2：記憶體佈局保護（ASLR / 高熵 ASLR）                    │
  │  「你不知道目標在哪，需要 info leak 先破」                       │
  ├─────────────────────────────────────────────────────────────────┤
  │  Layer 1：基礎破壞防禦（DEP / GS / SafeSEH / SEHOP）            │
  │  「最基礎的利用原語（stack shellcode / SEH overwrite）被擋」     │
  └─────────────────────────────────────────────────────────────────┘
```

**關鍵認識**：這四層是累加的，不是替代的。Win11 全緩解目標四層全開；典型的舊版企業應用可能只有 Layer 1/2；CTF 題通常刻意關掉某幾個來留出可打的空間。

Linux 對照：Layer 1 ≈ NX+canary+RELRO；Layer 2 ≈ PIE+ASLR；Layer 3 ≈ clang CFI（不普遍）；Layer 4 ≈ Linux 沒有等價的主流機制（有 seccomp 但那是 syscall filter）。這就是為什麼 Windows 現代緩解對抗比 Linux 難。

## 大總表：11 個緩解逐一橫剖

> 欄位說明：「前提」= 有效的必要條件；「典型繞過」= 這課學到的攻擊向量；「補強者」= 哪個後續緩解把這個繞過方法擋掉；「如何判斷開沒開」= 具體指令。

### DEP（Data Execution Prevention）

| 項目 | 內容 |
|---|---|
| **擋什麼** | Stack / heap shellcode 執行（把 `NX` bit 標在所有不是程式碼的頁面）|
| **不擋什麼** | 已存在於程式碼段的 gadget；`VirtualProtect`/`VirtualAlloc + PAGE_EXECUTE` 後的頁面 |
| **前提** | CPU NX/XD bit 支援 + 程式設 `NX_COMPAT`（`DllCharacteristics & 0x0100`）+ 行程政策 OptIn/OptOut/AlwaysOn |
| **典型繞過** | ROP chain 呼叫 `VirtualProtect`（把 stack 改成 +X）或直接 ret2libc；跳向已有 `PAGE_EXECUTE` 的記憶體 |
| **補強者** | CFG（限制 ROP 的最終 indirect call 目標）、ASLR（讓 gadget 位址未知）|
| **判斷方式** | `objdump -p target.exe \| grep NX_COMPAT`；`winchecksec target.exe`；`Get-ProcessMitigationPolicy -Id <pid>` 裡的 `DEP.Enable` |

**Linux 對照**：Linux 的 NX bit（`GNU_STACK RW`）是完整對應；`checksec` 的 `NX enabled` 就是這個。差異在 Windows 有 `AlwaysOn`/`OptIn`/`OptOut` 三種行程政策，Linux 就是開或不開。

---

### ASLR（Address Space Layout Randomization）

| 項目 | 內容 |
|---|---|
| **擋什麼** | 硬編碼位址的 exploit（知道 return address 在哪 → 直接填上去）|
| **不擋什麼** | 有 info leak 的 exploit；非 ASLR 模組（沒有 `DYNAMIC_BASE` 的 DLL）；部分覆寫（利用 ASLR 的對齊殘留）|
| **前提** | 程式設 `DYNAMIC_BASE`（`DllCharacteristics & 0x0040`）且有 reloc table（缺 reloc 就算設旗標也無法重定位）；Vista+ 才有 image ASLR；Win8 才有高熵 ASLR（需 `HIGH_ENTROPY_VA & 0x0020`）|
| **典型繞過** | info leak 洩漏 module base（Ch 31）；找沒開 ASLR 的舊版 DLL 當 pivot；stack/heap spray（降低位址不確定性）；x86 暴力猜測（256 可能性）|
| **補強者** | CFG（leak 了 base 也不能跳到任意位址）；DEP（就算知道 stack 位址也不能執行）|
| **判斷方式** | `objdump -p target.exe \| grep DYNAMIC_BASE`；`winchecksec`；`dumpbin /headers` 的 DLL characteristics；Process Hacker / Task Manager 的模組清單看 base 有沒有隨機化 |

**Win11 x64 的熵**：image base 約 17–19 bits 熵，stack 約 17 bits，heap 更低（約 5 bits 因為對齊限制）。x86 mode 熵仍只有 8 bits，暴力破解仍可行（256 次）。

---

### `/GS`（Buffer Security Check / Stack Cookie）

| 項目 | 內容 |
|---|---|
| **擋什麼** | 線性 stack buffer overflow 直接覆蓋 return address |
| **不擋什麼** | 越過 cookie 的目標（SEH handler、vtable 在 cookie 「上方」的變數、函式指標局部變數）；info leak 洩漏 `__security_cookie`；非線性覆寫（越過的位元組剛好保留原 cookie 值）|
| **前提** | MSVC 編譯時有 `/GS`（預設開，可 `/GS-` 關閉）；執行期讀 `__security_cookie`（.data section）；epilogue 驗證失敗呼叫 `__report_gsfailure` |
| **典型繞過** | 先打 SEH handler（在 cookie 驗證之前先觸發例外）；洩漏 `__security_cookie` 值後填進 payload；使用 heap overflow 或 type confusion 繞過 stack cookie 完全不觸碰它 |
| **補強者** | SEHOP（擋掉打 SEH handler 的路）；SafeSEH（同上，更精確的白名單）|
| **判斷方式** | `dumpbin /headers` 看有無 `GS`（MSVC 預設開）；`winchecksec` 的 `GS` 欄位；沒有 MSVC 的 mingw binary 等效靠 gcc stack canary（`-fstack-protector`）|

---

### SafeSEH

| 項目 | 內容 |
|---|---|
| **擋什麼** | x86 SEH overwrite：攻擊者把 handler 蓋成任意地址，OS 在 dispatch exception 前查 SafeSEH 白名單 |
| **不擋什麼** | x64（x64 根本不用 SEH chain，改用 table-based，SafeSEH 無意義）；不在 SafeSEH 白名單的模組（沒有 `/SAFESEH` 的舊 DLL 可以拿來跳板）；heap 上的 SEH-like 結構偽造 |
| **前提** | MSVC 連結時 `/SAFESEH`（x86 only）；PE 的 Load Config 有 `SEHandlerTable` + `SEHandlerCount`；OS 在 dispatch 時比對 handler 必須在某個已登錄的 SafeSEH 模組裡 |
| **典型繞過** | 找一個沒有 SafeSEH 的 DLL（`!safeseh` 的模組）放 gadget 當 handler；或直接在 TEB 的 exception chain 上偽造（需要完全可控的記憶體）|
| **補強者** | SEHOP（配合 SafeSEH 封堵剩餘繞過）|
| **判斷方式** | `dumpbin /loadconfig target.exe` 看 `SEHandlerTable`；`!exploitable` 外掛；mona.py `!mona safeseh -m target.dll` |

---

### SEHOP（Structured Exception Handler Overwrite Protection）

| 項目 | 內容 |
|---|---|
| **擋什麼** | x86 SEH chain 的任意覆寫：在 dispatch 前驗證 chain 最後一個 handler 必須是 `ntdll!FinalExceptionHandler`（固定的合法 terminus）|
| **不擋什麼** | x64（同 SafeSEH，x64 不用 chain）；Info leak 洩漏 `FinalExceptionHandler` 位址後在偽造的 chain 末端填上它；完全控制連續記憶體、偽造一整條合法 chain |
| **前提** | Vista SP1+ OS 層啟用；`SEHOP` Group Policy 或 IFEO `DisableExceptionChainValidation = 0`；只保護 x86 行程 |
| **典型繞過** | 洩漏 `ntdll!FinalExceptionHandler` 地址（它是個固定函式，ASLR 下需要 ntdll leak）→ 偽造 chain 讓最後一個 `_EXCEPTION_REGISTRATION_RECORD.Next = NULL`、Handler = `FinalExceptionHandler`（Ch 22 的技法）|
| **補強者** | ASLR（讓 `FinalExceptionHandler` 位址未知）；CFG（就算繞了 SEHOP 也不能 indirect call 到任意 gadget）|
| **判斷方式** | `winchecksec`；`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\kernel\DisableExceptionChainValidation`（0 = SEHOP on）；Process Mitigation 政策 |

---

### CFG（Control Flow Guard）

| 項目 | 內容 |
|---|---|
| **擋什麼** | indirect call / jmp 跳向非 Guard CF Function Table 裡的合法目標（vtable 劫持、函式指標覆寫後的任意跳轉）|
| **不擋什麼** | direct call / jmp（不查 CFG）；return address（ROP 的 ret 指令不查 CFG——這是 CFG 最大的弱點）；CFG bitmap 裡有效的函式（攻擊者讓指標指向合法函式但傳錯參數 = data-only 攻擊）；非 CFG 模組的 gadget |
| **前提** | MSVC `cl /guard:cf` + `link /guard:cf`；OS Win8.1 Update 3+；行程 `SetProcessMitigationPolicy(ProcessControlFlowGuardPolicy)` 確認有效 |
| **典型繞過** | 使用非 CFG 模組的 gadget 當跳板（找行程裡沒有 `/guard:cf` 的 DLL）；利用 `SetProcessValidCallTargets` 動態加白名單（需要 arbitrary write）；call-oriented programming（只跳向 CFG 表內的函式，但串成 chain 達到效果）；data-only（Ch 37）|
| **補強者** | XFG（把 CFG 的地址粒度精化成型別哈希）；CET-IBT（indirect branch 硬體攔截）|
| **判斷方式** | `dumpbin /loadconfig target.exe \| findstr Guard`；`winchecksec` 的 `CFG` 欄位；Process Mitigation Policy `ControlFlowGuardPolicy.EnableControlFlowGuard` |

---

### XFG（eXtended Flow Guard）

| 項目 | 內容 |
|---|---|
| **擋什麼** | CFG 的粒度問題：CFG 只看「地址是否是合法函式入口」，XFG 進一步要求「地址的函式簽名 hash 必須和 call site 預期的一致」|
| **不擋什麼** | 同型別的函式之間的呼叫（型別相同但語意不同的合法目標）；data-only 攻擊（改的是普通資料不是函式指標）；ROP（ret 不查 XFG）|
| **前提** | MSVC `/guard:xfg`；Win10 2004+；功能尚在部署中，目前只有部分 Microsoft 元件開啟 |
| **典型繞過** | 找同型別的 gadget 函式（型別 hash 相同但做壞事的實作）；在舊版 Windows 上 XFG 不存在；非 XFG 模組繞過（同 CFG 策略）|
| **補強者** | CET-IBT（更硬的硬體層 IBT 攔截）|
| **判斷方式** | `dumpbin /loadconfig` 的 `Guard XFG Check Function Pointer`；實務上目前只有少數 MS 二進位有 XFG |

---

### CET-SS（Control-flow Enforcement Technology — Shadow Stack）

| 項目 | 內容 |
|---|---|
| **擋什麼** | ROP 攻擊中修改 stack 上的 return address：CPU 硬體維護 shadow stack，每次 `call` 把 return address 壓進 shadow stack（不可寫的 kernel-managed 記憶體），每次 `ret` 對比 shadow stack 頂，不符就 `#CP` 例外終止 |
| **不擋什麼** | JOP（Jump-Oriented Programming）/ COP（Call-Oriented Programming）——只有 `ret` 被保護，`jmp`/`call` 不在這裡管；data-only；stack 上返回地址之外的資料（局部變數、saved 非 return 的指標）|
| **前提** | Intel Tiger Lake+ 或 AMD Zen 3+ CPU（硬體 CET 支援）；Windows 10 2004+；PE 用 MSVC `/CETCOMPAT` 標記；行程政策 `ProcessUserShadowStackPolicy` |
| **典型繞過** | COP gadget chain（只用 call/jmp，避開 ret）；longjmp / 例外展開豁免機制；找不支援 CET 的 DLL 當 chain pivot；攻擊 CET 豁免機制本身（`SetUnwindFunctionTable` 等）|
| **補強者** | CFG/XFG（封堵 COP 裡的 indirect call 跳轉）；CET-IBT（封堵 COP 裡的 indirect branch）|
| **判斷方式** | `winchecksec` CET 欄位；`Get-ProcessMitigationPolicy -Id <pid>` 的 `UserShadowStack.Enable`；硬體要求可查 `cpuid` 輸出的 `CET_SS` bit |

---

### CET-IBT（Control-flow Enforcement Technology — Indirect Branch Tracking）

| 項目 | 內容 |
|---|---|
| **擋什麼** | JOP/COP 中的 indirect jmp/call 跳向非 `ENDBR64` 指令的地址：CPU 要求 `jmp reg` / `call reg` 後的第一條指令必須是 `ENDBR64`（`F3 0F 1E FA`），否則 `#CP` |
| **不擋什麼** | direct call/jmp（不用 ENDBR64）；ROP 的 `ret`（CET-SS 的工作）；data-only |
| **前提** | 同 CET-SS 的硬體要求 + OS 支援；編譯器必須在所有合法 indirect call 目標前插入 `ENDBR64`（MSVC `/CETCOMPAT` 或 GCC/clang `-fcf-protection=full`）|
| **典型繞過** | 找 ENDBR64 gadget（合法的 `ENDBR64` 後面接上有用的指令）；找不支援 IBT 的 DLL；data-only |
| **補強者** | XFG（對 call target 進一步型別限制）|
| **判斷方式** | 反組譯看函式入口有無 `ENDBR64`（位元組序列 `F3 0F 1E FA`）；`winchecksec`；`dumpbin /headers` |

---

### ACG（Arbitrary Code Guard）

| 項目 | 內容 |
|---|---|
| **擋什麼** | 動態生成可執行程式碼：任何試圖把 non-image-backed 記憶體標記為 `PAGE_EXECUTE*` 的 `VirtualAlloc`/`VirtualProtect` 都失敗；也阻止把已有的 +X 頁面再改成 +W |
| **不擋什麼** | 利用已存在的 image-backed +X 頁面（ROP/JOP）；data-only；out-of-process JIT 架構（Edge/Chrome 的 JIT renderer 用此繞過自身 ACG）|
| **前提** | Win10 1703+；行程必須自己呼叫 `SetProcessMitigationPolicy(ProcessDynamicCodePolicy)` 或父行程在 `CreateProcess` 時注入；只保護設定的行程 |
| **典型繞過** | 找行程裡已有的 +X gadget（ROP/JOP 不受限）；注入到沒有 ACG 的行程；`WriteProcessMemory` 到非 +X 的段（ACG 封鎖 permission flip，但 RW 頁面仍可寫資料）|
| **補強者** | CIG（確保連 DLL 注入也被擋）；CFG/CET（ACG 沒擋的 ROP/JOP 路線靠這些擋）|
| **判斷方式** | `Get-ProcessMitigationPolicy -Id <pid> -Policy DynamicCode`；Process Hacker 看 mitigation flags；只有特定行程（Edge renderer、某些安全產品）會開 |

---

### CIG（Code Integrity Guard）

| 項目 | 內容 |
|---|---|
| **擋什麼** | 未簽署的 DLL 注入：`LoadLibrary` 只接受 Microsoft 或 WHQL 簽署的影像；也擋沒有合法簽章的 `VirtualAllocEx` + `WriteProcessMemory` 方式注入 shellcode |
| **不擋什麼** | 攻擊者已在行程裡（exploit 本身的程式碼執行）；用合法的簽署 DLL 作為 proxy（living-off-the-land）；data-only；記憶體中的篡改（CIG 只在 load 時查簽章）|
| **前提** | Win10 1703+；行程設 `SetProcessMitigationPolicy(ProcessSignaturePolicy)`；UEFI Secure Boot 不是必須（CIG 是 user-mode 簽章檢查）|
| **典型繞過** | 找已有合法簽章的 DLL 裡的 gadget（Edge 載入的 DLL 全是合法簽署的，gadget 仍可用）；攻擊行程自己；針對沒有 CIG 的父行程注入 |
| **補強者** | ACG（即使找到了合法 DLL，也不能用它把新程式碼 map 成 +X）|
| **判斷方式** | `Get-ProcessMitigationPolicy -Id <pid> -Policy ImageLoad`；`winchecksec` CIG 欄位 |

---

## 緩解互相補強的全局圖

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │                    攻擊者持有 memory corruption primitive            │
  └────────────────────────────┬────────────────────────────────────────┘
                               │
              ┌────────────────▼─────────────────┐
              │         ASLR 把位址藏起來          │
              │  攻擊者需要 info leak 先洩漏 base  │
              └────────────────┬─────────────────┘
                               │ 有 leak
  ┌────────────────────────────▼─────────────────────────────────────────┐
  │  嘗試控制 return address（stack overflow / UAF 寫 stack）            │
  │         ↓ GS cookie 擋線性覆寫                                       │
  │  嘗試打 SEH handler（GS 的繞過路線）                                 │
  │         ↓ SafeSEH 要求 handler 在白名單                              │
  │         ↓ SEHOP 要求 chain 走向 FinalExceptionHandler                │
  │  嘗試 ROP（繞 DEP 的標準路線）                                       │
  │         ↓ DEP 讓 stack 上的 shellcode 不可執行                       │
  │         ↓ CET-SS 讓 ret 對比 shadow stack，ROP chain 直接死          │
  └────────────────────────────┬─────────────────────────────────────────┘
                               │ ROP/SS 被擋，改打 indirect call
  ┌────────────────────────────▼─────────────────────────────────────────┐
  │  嘗試 vtable 劫持 / 函式指標覆寫（→ 任意 indirect call）             │
  │         ↓ CFG bitmap 查目標地址是否合法                              │
  │         ↓ XFG 進一步要求型別 hash 匹配                               │
  │         ↓ CET-IBT 要求目標前有 ENDBR64                               │
  └────────────────────────────┬─────────────────────────────────────────┘
                               │ CFI 全被擋，嘗試注入新程式碼
  ┌────────────────────────────▼─────────────────────────────────────────┐
  │  嘗試 VirtualAlloc + PAGE_EXECUTE_READWRITE（注入 shellcode）         │
  │         ↓ ACG 讓動態 +X 分配失敗                                     │
  │  嘗試 LoadLibrary 注入惡意 DLL                                        │
  │         ↓ CIG 要求 DLL 有合法簽章                                    │
  └────────────────────────────┬─────────────────────────────────────────┘
                               │ 所有控制流路線被擋
                    只剩 data-only attack
                    （不碰函式指標，操作普通資料欄位）
                    或逃脫行程邊界（sandbox escape）
```

## 繞過決策樹：拿到記憶體破壞原語後怎麼走

這棵樹是你面對真實目標時的思考腳本。每個節點都是一個問題，答案決定你往哪走。

```
  [START] 確認 primitive 的類型
       │
       ├─► stack buffer overflow（線性連續寫）
       │        │
       │        ├─► 目標有 /GS？
       │        │       ├─► 沒有 → 直接覆蓋 ret addr → 需要 DEP bypass → 走 ROP
       │        │       └─► 有  → 繞 GS：
       │        │               ├─► 打 SEH handler（在 cookie 驗證前觸發例外）
       │        │               │        ├─► 有 SafeSEH？→ 找非 SafeSEH 模組跳板
       │        │               │        └─► 有 SEHOP？→ 需要 ntdll base leak
       │        │               │                         → 偽造合法 chain
       │        │               └─► info leak 洩漏 cookie → 暴力猜（x86 有機會）
       │        │
       │        └─► 解決 GS 後，有 CET-SS？
       │                ├─► 沒有 → ROP chain 即可
       │                └─► 有  → 不能用 ret → 改走 COP/JOP 或 data-only
       │
       ├─► heap primitive（UAF / overflow → 控制 heap object）
       │        │
       │        ├─► 能控制什麼？
       │        │       ├─► vtable / 函式指標 → 控制流劫持路線
       │        │       │       ├─► 有 CFG？
       │        │       │       │       ├─► 沒有 → 直接跳任意地址
       │        │       │       │       └─► 有  → 需要跳向 CFG 白名單內的地址
       │        │       │       │               ├─► 找非 CFG 模組的 gadget（優先）
       │        │       │       │               ├─► 找 CFG-bypass gadget
       │        │       │       │               │   （dispatch function pointer）
       │        │       │       │               └─► 走 data-only（不碰函式指標）
       │        │       │       └─► 有 CET-IBT？→ 只能跳 ENDBR64 gadget
       │        │       └─► 普通資料（不是指標）→ data-only attack
       │        │
       │        └─► 有 ASLR？→ 先找 heap/stack/module info leak（Ch 31）
       │
       ├─► 任意讀（arbitrary read）
       │        └─► 洩漏 module base（PEB → LDR → DllBase）
       │            洩漏 stack 位址（TEB.StackBase）
       │            洩漏 __security_cookie
       │            → 降級 ASLR / GS，回到上面的路
       │
       └─► 任意寫（arbitrary write）
                └─► 目標是什麼？
                        ├─► RIP/RSP → 需要知道 stack 位址（先 leak）
                        │            + 有 CET-SS 的話 shadow stack 會擋
                        ├─► 函式指標 → 需要目標位址 + CFG 白名單
                        ├─► vtable → 同上 + 需要 heap 位址
                        ├─► 非函式指標資料（data-only）→ 不需要 CFI bypass
                        └─► CFG bitmap 本身 → 向白名單加任意地址
                                              （需要 bitmap 位址；高風險）
```

## 常見緩解組合分析

### 組合 1：典型桌面 exe（DEP + ASLR + GS）

**場景**：企業內部工具、老遊戲、一般 Win32 應用。

```
  winchecksec 輸出（典型）：
  DEP:          ENABLED
  ASLR:         ENABLED
  HighEntropy:  ENABLED  （x64 的話）
  GS:           ENABLED
  SafeSEH:      ENABLED  （x86 的話）
  CFG:          DISABLED
  XFG:          DISABLED
  CET:          DISABLED
  ACG:          DISABLED
  CIG:          DISABLED
```

**攻擊路線**：

這是最友善的現代靶。CFG 沒開，所以 vtable 劫持後可以跳任意地址。路線：

1. 觸發 heap UAF / overflow 取得 arbitrary write primitive
2. 用 info leak（Ch 31）洩漏任意 module base（優先：洩漏 stack 位址或行程內任何 module base）
3. 蓋 vtable / 函式指標 → 跳向 ROP gadget chain
4. ROP chain 呼叫 `VirtualProtect` 把 heap 改成 +X → 跳到 shellcode（或直接 ROP 呼叫 `WinExec`）

**Linux 對應難度**：比 glibc 2.39 靶簡單一點——沒有 CFG 代表 vtable 打法和 Linux 幾乎一樣，只差在 Windows heap 的 grooming 手法（Ch 28/29）。

---

### 組合 2：Edge renderer process（CFG + ACG + CIG + CET 全開）

**場景**：Chromium 系瀏覽器的 renderer，Windows 最硬的 userland 目標之一。

```
  Process Mitigation 狀態（概略，未實測）：
  DEP:          ENABLED
  ASLR:         ENABLED (HIGH_ENTROPY)
  GS:           ENABLED
  CFG:          ENABLED
  XFG:          ENABLED  （部分 MS 元件）
  CET-SS:       ENABLED  （Tiger Lake+ / Zen 3+ 硬體上）
  CET-IBT:      ENABLED
  ACG:          ENABLED
  CIG:          ENABLED
```

**攻擊路線**：

這是最難的全緩解目標。漏洞需要整個 chain：

1. 需要 renderer 中的記憶體破壞（V8 type confusion 是典型來源——`browser_pwn` 課學過）
2. info leak 是必須的（ASLR 高熵）
3. CFG / XFG / IBT 全開 → 控制流劫持極難：必須走 data-only（Ch 37），或找 renderer 裡 non-XFG 的 DLL
4. ACG 關掉動態 +X → 不能注入 shellcode，只能靠 ROP/JOP 操作已有函式
5. CET-SS 擋 ROP → 必須用 COP；CET-IBT 擋任意 call → 只能跳 ENDBR64 gadget
6. 最終目標通常不是在 renderer 裡拿 shell，而是**逃脫 sandbox** → 打 Windows 核心或更高特權的行程

**現實認知**：這個目標的 1-day 利用通常需要兩個漏洞（renderer 記憶體破壞 + sandbox escape），Chain 往往在一個 CVE 上打兩三個月。CTF 題不會這樣出。

---

### 組合 3：系統服務 / NT service（DEP + ASLR + GS + CFG）

**場景**：`svchost.exe` 下的服務、第三方驅動配套的 userland 元件。

```
  典型緩解：DEP + ASLR + GS + CFG（無 ACG / CIG / CET）
```

**攻擊路線**：

CFG 是主要障礙，ACG/CIG 沒有，代表可以注入 DLL 或分配 +X 記憶體當跳板。

1. info leak 取得 module base
2. heap UAF / overflow 控制物件
3. CFG 繞過選擇：
   - 找行程裡沒有 `/guard:cf` 的第三方 DLL（高機率存在）
   - 把目標指向 CFG 白名單必定有的函式（如 `VirtualAlloc`）→ 分配 +X 記憶體 → 注入 shellcode（ACG 不在，這行得通）
4. 無 CET → ROP 不被 shadow stack 攔截

**注意**：系統服務常跑在更高 integrity level（Medium 或 High），打成功可能直接拿到提權後的程式碼執行——天梯銜接 Part 7 的 token stealing。

---

### 組合 4：CTF 題的典型配置

CTF 組織者通常刻意留出攻擊空間：

| CTF 難度 | 典型緩解狀態 | 留的空間 |
|---|---|---|
| 入門 | DEP only（無 ASLR） | 位址固定，直接 ROP |
| 初中 | DEP + ASLR，無 CFG | 需要 info leak；vtable 打法有效 |
| 中等 | DEP + ASLR + GS + CFG | 需要找非 CFG 模組跳板或 CFG bypass gadget |
| 困難 | 上述全開 + CET-SS | 必須 COP/data-only；幾乎沒有 CET 題（工具鏈不普及）|
| 幾乎不出 | ACG + CIG 全開 | 這是真實環境的頂配，CTF 不出 |

判斷 CTF 靶的緩解是你的第一步，永遠在做其他事之前先跑 `winchecksec`：

```console
# winchecksec 裝好後的用法（未實測輸出格式）
> winchecksec ctf_target.exe
Dynamic Base:    true
ASLR:            true
High Entropy VA: true
Force Integrity: false
Isolation:       true
NX:              true
SEH:             true
CFG:             false
RFG:             false
SafeSEH:         false (x64 target)
GS:              true
Authenticode:    false
.NET:            false
```

---

## 如何快速判斷目標開了什麼（五分鐘速查）

### Step 1：winchecksec（最快）

```powershell
# 從 https://github.com/trailofbits/winchecksec 下 Release 或：
winget install winchecksec

winchecksec target.exe
```

一行輸出包含所有 PE 層的緩解狀態。它不能看 process-level 的 ACG/CIG（那是執行期政策）。

### Step 2：objdump（mingw 隨附，馬上能用）

```console
$ objdump -p target.exe | grep -iE "DllCharacteristics|GUARD|DYNAMIC|NX|ENTROPY|SAFESEH|CETCOMPAT"
```

核心欄位：

| 關鍵字 | 對應緩解 | bit 值 |
|---|---|---|
| `DYNAMIC_BASE` | ASLR | 0x0040 |
| `HIGH_ENTROPY_VA` | 高熵 ASLR（64 位元） | 0x0020 |
| `NX_COMPAT` | DEP | 0x0100 |
| `GUARD_CF` | CFG | 0x4000 |
| `CETCOMPAT` | CET | Load Config 旗標 |

### Step 3：dumpbin（MSVC 裝好後，資訊最全）

> **未實測，MSVC 安裝後驗證**

```bat
dumpbin /headers target.exe      :: 看 DLL characteristics
dumpbin /loadconfig target.exe   :: 看 Guard Flags、CFG Function Table、SEHandlerTable
```

`/loadconfig` 的 `Guard Flags` 欄位最有用：

| Guard Flag 值 | 意義 |
|---|---|
| `0x100` | CF Instrumented（有 CFG 插樁）|
| `0x400` | CF Function Table Present |
| `0x2000` | RF Instrumented（Retpoline）|
| `0x100000` | XFG Instrumented |
| `0x20000000` | Shadow Stack–enabled（CET-SS）|

### Step 4：執行期政策（ACG / CIG 等只能這樣看）

```powershell
# 取得目標 PID 後
$pid = (Get-Process target).Id
Get-ProcessMitigationPolicy -Id $pid
```

`Get-ProcessMitigationPolicy` 回傳的是行程執行時實際生效的政策，比 PE 標頭更準確——有些緩解是父行程在 `CreateProcess` 時注入的，PE 本身看不出來。

---

## 對照：Linux 現代緩解決策 vs Windows

| 攻擊場景 | Linux 決策 | Windows 對應決策 | 關鍵差異 |
|---|---|---|---|
| Stack overflow | canary 在嗎？→ 打 FSB leak 或例外前的路徑 | GS 在嗎？→ 打 SEH handler / 洩漏 cookie | Windows 的 SEH 路線在 Linux 沒有等價 |
| vtable 劫持 | PIE 的話先 leak base；glibc hook 已死 → 打 heap metadata | 先 leak；CFG 在嗎？→ 找非 CFG 模組 | CFG 是 Windows 獨有的主要難點 |
| Return address 控制 | NX → ROP；canary → 需 leak | DEP → ROP；GS → 需 bypass；CET-SS → 不能用 ROP | CET-SS 是 Windows 比 Linux 現代的地方（Linux CET 支援尚不普遍）|
| 動態程式碼注入 | 常見（`mprotect(PROT_EXEC)`）| ACG 在嗎？→ 完全擋死 | ACG 是 Windows 比 Linux 嚴格的地方（Linux 沒有等價的 userland 政策）|
| 繞過後執行 shellcode | 跳到 mprotect 過的記憶體 / ORW chain | 沒 ACG → 用 `VirtualAlloc`；有 ACG → 只能 ROP/JOP 呼叫函式 | 相同邏輯，API 名稱不同 |
| 資訊洩漏目標 | libc base / pie base / heap base | ntdll base / 主程式 base / heap 位址 | Windows 的 PEB/LDR 是主要洩漏來源（Ch 31）|
| 最終目標 | 通常一個 bug 夠拿 shell | 全緩解下通常需要 2–3 個 bug chain | Windows sandbox 邊界更硬 |

## 踩雷集錦

1. **「CFG 關掉就等於沒有緩解了吧」**：錯。CFG 只管 indirect call。就算 CFG 全開，ROP（透過 `ret`）本來就不被 CFG 擋——那是 CET-SS 的工作。關掉 CFG 只是讓 vtable 劫持後可以跳任意地址，ROP 本來就能做到這點。

2. **「winchecksec 說 CET:Disabled，代表沒有 shadow stack」**：不完全對。winchecksec 看的是 PE 的 `/CETCOMPAT` 旗標，但 CET 實際效果還取決於硬體支援（Tiger Lake+ / Zen 3+）和 OS 政策。即使 PE 有旗標，跑在舊 CPU 上就是 no-op。確認方式是看 `Get-ProcessMitigationPolicy` 的 `UserShadowStack.Enable`。

3. **「找到了非 CFG 的 DLL，直接用它的 gadget 跳就好」**：進一步說明：從 CFG-enabled 模組跳進非 CFG 模組的 gadget 這個 indirect call 本身被 CFG 攔截（因為 call 的發出方是有 CFG 插樁的）。正確做法是透過 CFG 白名單裡的一個函式當跳板（或找 dispatch 機制），讓執行流到達非 CFG 模組。這是 Ch 33 的精髓，不要混淆。

4. **「data-only 攻擊就是沒辦法時的最後手段」**：資安研究者的認知已經翻轉了。當 CFG + XFG + CET 全開時，data-only 反而是**最主流**的路線（Win11 的 Edge 利用幾乎都走這條）。不要把它當 fallback，要把它當主要路線考慮，從一開始就評估有哪些資料欄位可以操作（Ch 37）。

5. **「SEHOP 在 x64 上很重要」**：x64 根本不用 SEH chain（改用 table-based exception，`RUNTIME_FUNCTION` 表）。SEHOP 只保護 x86 行程。在 64 位元的 Win11 目標，SEHOP 對你沒有任何影響——但如果打的是 32 位元相容模式（WoW64）行程，就要考慮。

6. **「ACG 開了就不能執行新程式碼了」**：精確說法是「不能動態分配 +X 記憶體，也不能把 non-image 頁面 flip 成 +X」。行程自己的程式碼段、所有已載入 DLL 的程式碼段本身仍然可執行——ROP/JOP 不受 ACG 影響。ACG 只擋「生成新程式碼」，不擋「重用已有程式碼」。

## 進階：再往深一層

### 緩解的時序問題

緩解不是在行程存活期間恆定有效的；它們在不同的時間點生效：

- **ASLR**：在 loader 決定 image base 時（行程建立期，最早）
- **GS cookie**：在 `_security_init_cookie`（`exe` 啟動時的 CRT 初始化）
- **CFG bitmap**：`ntdll!LdrpCfgInitialize`（行程啟動，在任何 TLS / DLL 初始化之前）
- **ACG / CIG**：呼叫 `SetProcessMitigationPolicy` 的那一刻（通常在 main 之前的框架程式碼，如 Edge 的 `ContentMain`）
- **CET-SS**：行程建立時由 OS 決定（如果 PE 有旗標且硬體支援）

**意義**：如果你能在 DLL 初始化期間（`DllMain`）或 CRT 初始化之前跑程式碼（例如 TLS callback 攻擊），某些緩解還沒有完全就位。這是偏進階的研究領域。

### 緩解政策的繼承

`CreateProcess` 有 `PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY` attribute，讓父行程強加緩解政策給子行程：

```c
// 概念性示範，未實測
DWORD64 policy =
    PROCESS_CREATION_MITIGATION_POLICY_DEP_ENABLE
  | PROCESS_CREATION_MITIGATION_POLICY_ASLR_FORCE_RELOCS_ALWAYS_ON
  | PROCESS_CREATION_MITIGATION_POLICY_CONTROL_FLOW_GUARD_ALWAYS_ON;
// 寫進 STARTUPINFOEX 的 attribute list
UpdateProcThreadAttribute(si.lpAttributeList,
    0, PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY,
    &policy, sizeof(policy), NULL, NULL);
```

Edge 和系統容器（AppContainer）廣泛用這個機制。研究一個目標的緩解時，不只要看 PE 旗標，也要追溯父行程的政策注入——`Get-ProcessMitigationPolicy` 這時就是你的朋友。

### 面試題：「如何打一個 Win11 全緩解的行程？」

標準回答架構：

1. **先確認緩解組合**（winchecksec + Get-ProcessMitigationPolicy）
2. **取得記憶體破壞原語**（UAF / overflow / type confusion），確認 primitive 類型
3. **必須有 info leak**（ASLR + 高熵 → 沒有 base 就沒有後續）
4. **CFG + XFG + CET 全開 → 控制流劫持幾乎不可行** → 走 data-only 或找特定豁免點
5. **ACG + CIG → 不能注入新程式碼** → 只能用已有的程式碼段 gadget
6. **最終目標通常是 sandbox escape** → 打 IPC / kernel / 其他 process
7. **一個 bug 往往不夠** → 現代 full-chain 通常需要 2–3 個漏洞串成 chain

## 動手練習

目標：建立你自己的靶目標緩解檔案庫。

選 3 個不同的 Windows PE（建議：1 個你用 mingw 自己編的、1 個系統元件如 `notepad.exe`、1 個開源應用如 VS Code 的 `Code.exe`），對每一個完成：

1. 用 `objdump -p` 列出 `DllCharacteristics`，拆解各個 bit，手動算出開了哪些緩解
2. 用 `winchecksec`（如果已安裝）確認各緩解欄位，和你的 `objdump` 計算結果比對
3. 執行它，用 `Get-ProcessMitigationPolicy -Id <pid>` 確認執行期政策（注意和 PE 標頭的差異）
4. 對著本章的繞過決策樹，**用筆在紙上**走一遍決策路徑：假設你有 arbitrary write primitive，你要怎麼走？
5. 記下你的答案（「這個靶的攻擊路線是：先 leak → 再打 vtable → 繞 CFG 用非 CFG 模組 → ROP 呼叫 WinExec」）

這個練習強迫你把「懂緩解原理」轉化成「能快速判斷攻擊路線」的本能。

## 本章重點整理

- **11 個緩解四層架構**：基礎破壞防禦（DEP / GS / SafeSEH / SEHOP）→ 佈局保護（ASLR）→ 控制流完整性（CFG / XFG / CET-SS / CET-IBT）→ 程式碼完整性（ACG / CIG）。每層擋的攻擊面不同，缺任何一層就有對應的空間。
- **per-module 粒度是關鍵**：同一行程裡，老舊 DLL 可能沒有 CFG；非 `/CETCOMPAT` 的模組沒有 shadow stack 保護。找弱模組是繞過現代緩解的標準起手式。
- **決策樹的核心問題**：有沒有 info leak？primitive 是 stack / heap / arbitrary write？CFG 在嗎？CET 在嗎？四個問題定義你的攻擊空間。
- **全緩解目標（Edge renderer）的現實**：CFG + ACG + CET 全開下，data-only 是主線，控制流劫持幾乎必須走 chain 繞過，且通常需要多個漏洞串接。

## 自我檢核

- [ ] 不看表，能說出 CFG 擋什麼、**不**擋什麼——說清楚為什麼 ROP 的 `ret` 不被 CFG 攔截
- [ ] 能說出面對「DEP + ASLR + GS + CFG，無 ACG / CET」的目標時的攻擊路線（不超過三句話）
- [ ] 能解釋為什麼 SEHOP 對 x64 行程無意義，以及 x64 的例外如何 dispatch
- [ ] 能說出 ACG 和 CIG 各自擋的是什麼、兩者合力補強了哪個攻擊面
- [ ] 看到一個不熟悉的 Windows PE 靶，能說出你的前三個判斷步驟（工具、指令、看什麼欄位）
- [ ] 能解釋「全緩解目標為什麼通常需要 2–3 個漏洞」——說清楚每一步需要什麼

## 延伸閱讀

### 論文 / 白皮書

- **[Preventing the Exploitation of Structured Exception Handler (SEH) Overwrites with SEHOP](https://research.microsoft.com/en-us/people/nicolas/sehop-techreport.pdf)** — Nicolas Falliere & Thomas Garnier，Microsoft（2009）
  - **讀哪裡**：全文（30 頁），重點是 Section 3「SEHOP design」和 Section 5「Bypass Techniques」
  - **學什麼**：為什麼 SafeSEH 的白名單不夠、SEHOP 如何補足、以及設計者本人對已知繞過的分析
  - **和本章的關聯**：總表 SafeSEH / SEHOP 兩欄的設計理據直接來自這篇
  - **前提知識**：x86 SEH 機制（Ch 11）

- **[Control Flow Integrity: Principles, Implementations, and Applications](https://dl.acm.org/doi/10.1145/1102120.1102165)** — Abadi, Budiu, Erlingsson, Ligatti，CCS 2005
  - **讀哪裡**：Section 1–3（背景與原理），第 6 節（和 CFG / XFG 的關係）
  - **學什麼**：CFI 的原始學術定義；理解為什麼地址粒度（CFG）和型別粒度（XFG / clang CFI）是設計上的根本取捨
  - **和本章的關聯**：CFG / XFG 一欄「補強者」關係的理論基礎
  - **前提知識**：CFG 機制（Ch 32）

### 官方文件

- **[Process Mitigation Policy — Win32 apps — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessmitigationpolicy)**
  - **讀哪裡**：`PROCESS_MITIGATION_POLICY` 的每個 enum value 說明（`ProcessDEPPolicy`、`ProcessDynamicCodePolicy`、`ProcessControlFlowGuardPolicy` 等）
  - **學什麼**：`Get-ProcessMitigationPolicy` PowerShell 指令回傳的每個欄位對應的 C 結構；如何在程式裡設定 / 查詢緩解
  - **和本章的關聯**：「如何判斷目標開了什麼」一節的執行期政策查詢的權威來源

- **[Control Flow Guard for platform security — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/secbp/control-flow-guard)**
  - **讀哪裡**：全文（短，約 1500 字），重點是「How Control Flow Guard works」和「Developer guidance」段落
  - **學什麼**：Microsoft 官方對 CFG 保護範圍的定義（和本章總表 CFG 欄的「不擋什麼」直接比對）
  - **和本章的關聯**：補強總表 CFG 欄，也是判斷靶開了 CFG 的工具參照

### 部落格

- **[Exploring Control Flow Guard in Windows 10](https://www.nccgroup.com/globalassets/our-research/us/whitepapers/2015/12/exploring_control_flow_guard_in_windows10.pdf)** — Morten Schenk，NCC Group（2015）
  - **讀哪裡**：Section 3「Bypassing CFG」和 Section 4「Limitations」
  - **學什麼**：CFG 最早的系統性繞過分析；「找非 CFG 模組」、「利用 SetProcessValidCallTargets」、「利用 dispatch pointer」三條路線的原始文獻
  - **和本章的關聯**：決策樹 CFG 節點的「找非 CFG 模組 gadget」和「CFG bypass gadget」分支的技術根據
  - **前提知識**：CFG 原理（Ch 32）、CFG 繞過技法（Ch 33）

- **[Windows Exploitation Tricks: Exploiting Arbitrary File Writes for Local Elevation of Privilege](https://googleprojectzero.blogspot.com/2018/08/windows-exploitation-tricks-exploiting.html)** — James Forshaw，Project Zero（2018）
  - **讀哪裡**：全文，尤其是作者如何在 ACG/CIG 全開的環境裡用 data-only primitive 做 EoP 的思路
  - **學什麼**：「data-only 不只是 bypass CFI 的手段，也可以是完整的 exploit primitive」——把本章 data-only 一欄的思路延伸到真實 EoP 場景
  - **和本章的關聯**：決策樹最後分支（data-only attack）的真實案例，也是「組合 2」（全緩解目標）攻擊邏輯的實際體現
  - **前提知識**：ACG / CIG（Ch 36）、data-only（Ch 37）

---

Part 5 到這裡完整閉合：你有了每個緩解的機制（Ch 32–37）、它的歷史脈絡（Ch 38）、以及把全部整合起來的實戰決策框架（這章）。動手把 CFG 從「被擋」打到「繞過」，再進 Part 6。

→ [練習 E — 打穿 CFG：從被擋到繞過](./practice-e-cfg-bypass.md)
