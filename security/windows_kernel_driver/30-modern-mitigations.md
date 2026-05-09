# Ch 30 — 現代緩解機制

> 目標：理解 SMEP/SMAP、KVAS/KVA Shadow、CFG、KASLR 等機制的原理，以及它們如何改變 exploit 難度。

## SMEP（Supervisor Mode Execution Prevention）

**原理**：CR4 暫存器的第 20 位（SMEP bit）。當設定時，CPU Ring 0 代碼不能執行 Ring 3（用戶態）頁面的代碼。

**阻止的攻擊**：把 Shellcode 放在用戶態頁面，然後讓核心 EIP 跳過去執行。有 SMEP 後，執行用戶態頁面觸發 #PF（Page Fault）→ BSOD。

**繞過技術**：
1. **Kernel ROP**：在核心代碼段找 gadget，不執行用戶態頁面
2. **把 Shellcode 放在核心 Pool**（`NonPagedPoolNx` 不可執行，但 `NonPagedPool` 舊版可以）
3. **Clear SMEP bit**：用 ROP 執行 `mov cr4, rax; ret`，把 CR4 的 SMEP bit 清零，再執行用戶態 Shellcode（PatchGuard 會後來發現）

## SMAP（Supervisor Mode Access Prevention）

**原理**：CR4 第 21 位。核心代碼不能讀寫用戶態頁面，除非明確設定了 `AC` flag（RFLAGS 的第 18 位）。

Windows 用 `STAC`（Set AC）/`CLAC`（Clear AC）指令在核心安全的讀取用戶記憶體前後：
- `ProbeForRead` 內部使用 STAC
- 讀取完後 CLAC

**阻止的攻擊**：直接從核心態讀寫用戶提供的指針（沒有用 ProbeForRead 的路徑）。

**繞過技術**：找到核心中已有 `STAC` 的代碼路徑（gadget），在那個時刻讀寫用戶態。

## KASLR（Kernel Address Space Layout Randomization）

**原理**：每次開機，ntoskrnl 的基址隨機化（在特定範圍內）。

**Windows 的實作**：相比 Linux，Windows 的 KASLR 熵較小（通常 256 個可能的位置），但有 boot-time 隨機化。

**繞過**：
- `NtQuerySystemInformation(SystemModuleInformation)` = 洩漏 kernel base（**普通用戶可呼叫**！這是 Windows 的一個長期爭議點）
- 各種 infoleak（讀取包含核心指針的資料結構）

Windows 11 試圖限制 `NtQuerySystemInformation` 的資訊，但仍有其他 infoleak 路徑。

## KVAS（Kernel Virtual Address Shadow）/ KVA Shadow

**背景**：Meltdown（CVE-2017-5754）硬體漏洞讓用戶態程式可以讀取核心記憶體。

**緩解**：KVAS（Windows 的 KPTI 實作）在用戶態使用一個「影子」頁表，只包含核心的最小部分（syscall 入口等）。

**性能影響**：每次 syscall 需要切換頁表（TLB flush），在有 PCID 支援的 CPU 上影響較小（約 5-15%）。

**對 exploit 的影響**：需要 KVAS Bypass 才能在用戶態讀到核心地址。通常先 Bypass KASLR，取得核心地址後在核心態操作。

## CFG（Control Flow Guard）

**原理**：在函式指針呼叫前，驗證目標地址是否是合法的函式入口（在 CF bitmap 中）。

**Windows 實作**：
```
indirect call instruction → __guard_check_icall_fptr → 查 CFG bitmap
如果目標地址不在 bitmap → 呼叫 ntdll!RtlpHandleInvalidUserCallTarget（崩潰或 abort）
```

**核心的 CFG（KCFG）**：Windows 10 的核心也有 CFG，保護核心的間接呼叫（如 HalDispatchTable）。

**繞過技術**：
1. 找在 CFG bitmap 中的合法函式，用 ROP（gadget 都是合法函式入口）
2. 漏洞讓你控制 RIP 到的地址恰好是合法函式入口
3. 修改 CFG bitmap 本身（需要任意寫）

## Arbitrary Code Guard（ACG）/ Code Integrity Guard（CIG）

**ACG**（Windows 10 1703+）：進程選擇性的記憶體保護，JIT 代碼必須通過特定 API 申請可執行頁面。

主要影響用戶態 shellcode，核心利用可以繞過（直接在核心 Pool 執行）。

## 安全核心（Hyper-V 保護）

**VBS（Virtualization Based Security）** 和 **HVCI（Hypervisor-Protected Code Integrity）**：
- 利用 Hyper-V 的 Level 1 特權保護核心代碼完整性
- 核心不能自我修改代碼（動態 Patch 被禁止）
- 驅動簽章驗證在 Hypervisor 層執行，即使核心被攻陷也難以繞過

這是目前最強的緩解機制，CH 40 詳述。

## 緩解機制時間線

| 緩解機制 | 引入版本 | 保護的攻擊 |
|----------|---------|-----------|
| KASLR | Vista 64-bit | Hardcode 地址的 shellcode |
| SMEP | Windows 8 (Ivy Bridge+) | 跳到用戶態 shellcode |
| SMAP | Windows 10（Broadwell+）| 直接讀寫用戶態指針 |
| CFG | Windows 8.1 | 跳到非函式入口 |
| KVAS | Windows 10 1803（Meltdown patch）| Meltdown 核心讀取 |
| VBS/HVCI | Windows 10 21H1（需要硬體）| 核心代碼完整性 |
| Segment Heap | Windows 10 21H1 | Pool 溢出覆蓋 |

## 自我檢核

- [ ] SMEP：核心不能執行用戶態頁面；繞過需要 kernel ROP 或清 CR4 SMEP bit
- [ ] SMAP：核心不能讀寫用戶態（除非 STAC）；繞過需要找有 STAC 的 gadget
- [ ] KASLR bypass：Windows 的 `NtQuerySystemInformation(SystemModuleInformation)` 洩漏 kernel base（普通用戶可用）
- [ ] CFG：間接呼叫前驗證目標在 CF bitmap 中；ROP gadget 都是合法入口，可以繞過
- [ ] VBS/HVCI：Hypervisor 層保護核心完整性，是目前最強緩解

→ [練習 C：IOCTL 漏洞利用模擬](./practice-c-ioctl-exploit.md)
