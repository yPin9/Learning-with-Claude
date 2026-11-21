# Ch 46 — ARM64 kernel pwn：PAC / MTE / BTI、syscall ABI 差異、Android kernel 生態

> **目標**：把 Part 0–7 建立的 x86-64 kernel pwn 心智模型搬到 ARM64 上，知道哪些概念是「名字不同、原理一樣」（exception level、PAN/UAO），哪些是「x86-64 根本沒有、ARM64 獨有」（PAC、BTI、MTE），並理解這些硬體級 mitigation 為什麼讓 Android LPE 的打法和桌面/伺服器 x86-64 分道揚鑣。
>
> **環境**：以近期 mainline Linux（6.x）與 Android Generic Kernel Image（GKI，5.10/5.15/6.1 分支）為參照，架構為 AArch64（ARMv8.3 起支援 PAC，ARMv8.5 起支援 BTI/MTE）。涉及硬體特性的段落若無法在你手上的裝置驗證，會標「未實測，理論預期行為」。

## 為什麼要單獨學 ARM64？

Part 0–7 整本書幾乎是在 x86-64 上打的：QEMU 跑 x86-64 kernel、SMEP/SMAP、KPTI、KASLR，ROP gadget 都是 x86-64 指令集。但現實世界的 kernel LPE（Local Privilege Escalation，本機提權）有一大塊戰場根本不在 x86-64 上——**手機**。

Android 跑的是 Linux kernel，架構幾乎全是 ARM64。Google Project Zero、各大安全團隊每年公開的 Android kernel 0-day/1-day，絕大多數是 ARM64 上的漏洞，利用鏈卻常常要用上 x86-64 完全沒有的硬體機制：PAC 把回傳位址的簽章做進了硬體、MTE 用硬體 tag 抓 UAF、BTI 卡住 wild jump。這些不是「x86-64 的某個 mitigation 換了名字」，是**全新的攻擊面與全新的繞過研究方向**。

學 ARM64 kernel pwn 的正確心態不是「重學一遍 kernel pwn」，而是**對照**：同一個 bug class（UAF、heap overflow、race）在 ARM64 上還是那個 bug class，變的是「你能不能把它變成控制流劫持」這一步，因為 ARM64 在控制流劫持的路上多擺了三道硬體關卡。

## 先建立直覺：同一張地圖，換了地基

本書 Ch 1 建立的攻擊者地圖（Bug → Primitive → Leak → Control/Data → Privesc → Cleanup）完全適用。差別在「Control」這一格：

```
x86-64：
  劫 RIP → ROP chain（SMEP/SMAP 逼你 ret2dir 或純 kernel ROP）
           → commit_creds(prepare_kernel_cred(0))

ARM64：
  劫 PC  → 同樣想走 ROP，但：
           - 每個函式的 x30 (LR) 存進 stack 前後都被 PAC 簽過/驗過
           - 跳轉目標如果不是合法 BTI landing pad，直接 trap
           - 如果目標裝置開了 MTE，你用來做 UAF 的那次存取可能
             在漏洞觸發的瞬間就被硬體抓到，exploit 連起手式都沒走完
```

x86-64 的 mitigation（SMEP/SMAP/KASLR/KPTI）管的是「kernel 能不能碰 user 記憶體、你能不能猜到 kernel 位址」。ARM64 多出來的三個硬體機制管的是「**你拿到的任意寫/UAF，能不能真正變成控制流劫持**」——這是防禦縱深往前推了一層，也是為什麼現代 Android LPE 愈來愈多轉向 data-only attack（對應 [Ch 29](./29-data-only-attack.md)）而非傳統 ROP。

## Exception Level vs Ring：權限模型對照

x86-64 用 ring 0–3（kernel 只用 ring 0 和 ring 3），ARM64 用 **Exception Level（EL）**：

| | x86-64 | ARM64 |
|---|---|---|
| user mode | ring 3 | EL0 |
| kernel mode | ring 0 | EL1 |
| hypervisor | ring -1（VMX root） | EL2 |
| 最高特權（secure firmware） | SMM | EL3 |
| 進入更高特權的方式 | `syscall` / `int 0x80` | `svc` |
| 回到低特權 | `sysret` / `iret` | `eret` |

Linux kernel 本身跑在 EL1，一般情況下不用到 EL2（除非你自己就是在跑 KVM host）、EL3（通常是 ARM Trusted Firmware，和 Linux 無關）。所以對 kernel pwn 來說，**EL0 ↔ EL1 的邊界就是你唯一要打穿的邊界**，概念上等同 x86-64 的 ring 3 ↔ ring 0。

一個容易搞混的地方：x86-64 有 `iret` 這種「回到任意特權層」的單一指令，ARM64 的 `eret` 行為類似，但 ARM64 額外用 `SPSR_EL1`（Saved Program Status Register）記錄要回去的 EL 與中斷狀態，你在構造 fake exception frame 時要對的欄位和 x86-64 的 `iret` frame（`rip/cs/rflags/rsp/ss`）不是一一對應，**不能照搬 x86-64 的 fake stack frame 版面**。

## Syscall ABI：x8 當 syscall number，x0–x5 傳參

ARM64 的 syscall 入口：

```
mov x8, #<syscall number>
mov x0, <arg0>
mov x1, <arg1>
...
svc #0
```

對照表：

| | x86-64 | ARM64 |
|---|---|---|
| 觸發 syscall 的指令 | `syscall` | `svc #0` |
| syscall number 放哪 | `rax` | `x8` |
| 參數 1–6 | `rdi, rsi, rdx, r10, r8, r9` | `x0–x5` |
| 回傳值 | `rax` | `x0` |
| kernel entry 入口 | `entry_SYSCALL_64` | `el0_svc` |

這裡有一個容易踩的坑：**ARM64 一般函式呼叫（AAPCS calling convention）用 `x0–x7` 傳最多 8 個整數參數，但 syscall ABI 只用到 `x0–x5`，最多 6 個參數**——這和 glibc 包的 user-space 函式呼叫介面不是同一回事。如果你在寫 exploit 時直接把 `ioctl()` 的參數個數套進 x0–x7，會錯誤地以為能塞 8 個參數進 syscall；實際上 Linux syscall 介面本身就限制 6 個參數，這是 x86-64 和 ARM64 共通的限制，只是暫存器名字不同。

> 如果你對 syscall boundary 的 user/kernel 資料搬運（`copy_from_user`/`copy_to_user`）還不熟，先回看 [Ch 4 — syscall 與 user/kernel boundary](./04-syscall-boundary.md)，這一節的機制在 ARM64 上完全一樣，只是底層搬資料用的指令是 `ldr`/`str` 而非 `mov`。

## 位址空間：TTBR0/TTBR1 取代單一 CR3

x86-64 用一顆 `CR3` 指向頁表根，kernel 和 user 共用一張位址空間，靠 canonical address 的上下半（`0x0000...`／`0xffff...`）配合頁表權限位切開——這正是 Meltdown 能夠讀到 kernel 記憶體、KPTI 要解決的問題（見 [Ch 15](./15-kpti.md)）。

ARM64 的做法不同：它有**兩顆**頁表基底暫存器：

```
TTBR0_EL1 → 指向 user space 頁表（低半位址，0x0000_xxxx_xxxx_xxxx）
TTBR1_EL1 → 指向 kernel space 頁表（高半位址，0xffff_xxxx_xxxx_xxxx）
```

概念上你可以把它想成「x86-64 的單一位址空間被硬體拆成兩個獨立的頁表 walk 起點」。這個設計本身就讓 kernel 和 user 的翻譯路徑更容易隔離，但**不代表 ARM64 天生沒有 Meltdown 類問題**——早期 Cortex-A75 等核心仍受影響，ARM64 一樣有自己的 KPTI 實作，只是用的 cmdline 參數名字和 x86-64 不同：

- x86-64：`pti=off`（關閉 KPTI；見 [Ch 15](./15-kpti.md)）
- ARM64：`kpti=`，可設 `0`（強制關閉）/ `1`（強制開啟）；預設在已知需要 mitigation 的核心上自動開啟。2018 年曾有一版 patch 想把 x86 風格的 `pti=`/`nopti` 也映射進 ARM64 的 `kpti=` 框架，但**主要、穩定可用的仍是 `kpti=` 本身**——這點請以你手上的 kernel 版本的 `kernel-parameters.txt` 為準，不同版本對別名參數的支援程度不一致。

context switch 時，ARM64 只需要切換 `TTBR0_EL1`（user 那一半），`TTBR1_EL1`（kernel 那一半）通常維持不變——這是 ARM64 context switch 比 x86-64 單頁表方案更輕的原因之一（**未實測，理論預期行為**，實際效能差異取決於 TLB 行為與具體核心的 ASID 支援）。

## PAN / UAO：ARM64 版的 SMAP，但多了一個優化開關

[Ch 13](./13-smep-smap.md) 講過 SMEP（Supervisor Mode Execution Prevention，禁止 kernel 執行 user 頁的程式碼）和 SMAP（Supervisor Mode Access Prevention，禁止 kernel 直接讀寫 user 頁，逼你走 `copy_from_user`）。這兩個防禦直接封死了經典的 ret2usr（把 kernel 執行流導回 user space 的 shellcode）。

ARM64 的對應機制：

- **PAN（Privileged Access Never，ARMv8.1 引入）**：對應 SMAP。開啟後，EL1（kernel）用一般的 load/store 指令碰 EL0（user）可存取的記憶體會直接 fault，除非走特別的「unprivileged load/store」指令形式。這和 SMAP 的語意幾乎一致——**都是「kernel 預設不准碰 user 記憶體，要碰就得明確地、用規定好的介面碰」**。
- **UAO（User Access Override，ARMv8.2 引入）**：這是 ARM64 特有、x86-64 沒有對應物的優化。問題是：PAN 開著的時候，`copy_from_user`/`copy_to_user` 這類本來就該碰 user 記憶體的函式，要怎麼辦？如果每次呼叫都要「關 PAN → 存取 → 開 PAN」，成本不低。UAO 讓這些「unprivileged load/store」指令的行為可以被動態 override，Linux 用它讓有 UAO 支援的 CPU **不需要在每次 `copy_to_user()` 呼叫前後反覆開關 PAN**，而是直接用 alternative instruction patching，在支援 UAO 的核心上走較快的路徑，在不支援的核心上退回「切 PAN」的做法。
- **PXN（Privileged eXecution Never，頁表 PTE 裡的位元）**：對應 SMEP 的機制是頁表層級的 UXN/PXN 位元——`PXN` 位元設起的頁，EL1 不能執行它。這在機制上類似 SMEP「硬體擋下 kernel 執行 user 頁程式碼」的效果，但**這是我們從頁表權限模型推論出的對應關係，不是某份官方文件明確稱其為「SMEP 的 ARM64 版」**，標注為推論而非定論。

結論：**ret2usr 在 ARM64 上一樣死了**，死法和 x86-64 幾乎同構，差別只在 PAN 比 SMAP 多了一個 UAO 性能優化層，不影響攻擊者視角的結論——劫到 PC 之後，想跳回 user space 執行 shellcode 這條路，在開了 PAN 的現代裝置上不通。

## PAC：指標簽章，讓 ROP 的地基鬆動

**這是本章的核心，也是 x86-64 完全沒有對應物的機制。**

### 直覺

想像每個指標除了它本身指向的位址，還背著一個「只有 CPU 知道怎麼算」的簽章，簽在指標沒用到的高位 bit 裡。CPU 要用這個指標跳轉之前，先驗簽章對不對；驗不過，直接 fault。你（攻擊者）即使透過 heap overflow、UAF 把某個函式回傳位址或函式指標改成了你想要的值，**CPU 一樣會先驗那段你填不出來的簽章**，驗不過就不會跳過去。

### 機制

ARMv8.3 引入 Pointer Authentication（PAC）。核心指令家族：

- `PACIA`（Pointer Authentication Code, Instruction key, A）：用一個保密金鑰（存在特殊的系統暫存器，EL0 讀不到）加上指標本身的值、一個 modifier（通常是 stack pointer 或某個 context 值），算出一段驗證碼，塞進指標未使用的高位。
- `AUTIA`：驗證一個帶簽章的指標，驗證通過就還原回原始指標值；驗不過，指標的高位會被設成一個保證無效的樣式，之後用這個指標跳轉會直接 fault，而不是「悄悄跳到錯誤位址」。

Linux kernel 用它來做什麼？最直接的用法是**簽章函式回傳位址**——函式進入時把 `x30`（Link Register，存回傳位址）簽過章再存進 stack，函式回傳前驗章再 `ret`。如果你的漏洞是 stack buffer overflow 想覆寫回傳位址做 ROP（對應 [Ch 11](./11-stack-overflow.md) 的技巧），在開了 kernel PAC 的機器上，**你能覆寫那段記憶體，但你簽不出一個能通過驗證的簽章**，`AUTIA` 會抓到你填的值不對勁。

幾個需要誠實標注的限制：

- kernel 全域開 PAC 是相對晚才補上的能力——早期（Linux 5.0 前後）PAC 主要用在 user space 和 KVM guest 身上，**kernel 自身程式碼用 PAC 保護回傳位址（`CONFIG_ARM64_PTR_AUTH_KERNEL`）是後續才補強的方向**，不是「ARMv8.3 一出來 kernel 就全保護了」。
- PAC 只簽/驗「你叫它簽/驗的那個東西」——它不是全面 CFI。學術研究（如 Camouflage、PAL 等論文）指出，單純用 PAC 保護函式指標對 kernel 常見的程式寫法（大量合法的間接呼叫路徑）保護力有限，而且編譯器優化有時會產生「簽章 gadget」（一段程式碼恰好可以被你利用來簽出任意值），削弱保護強度。
- PAC 的金鑰是 per-process 隨機（在 `exec()` 時指派），金鑰本身不分 bank（not banked）——這代表如果 kernel 要在 kernel context 也用 PAC，進入 kernel 時金鑰的管理要特別小心，這塊的細節**建議以你實測的 kernel 版本的官方文件（`Documentation/arch/arm64/pointer-authentication.rst`）為準**，本章只講原理層級。

對控制流劫持的影響：**PAC 不是讓你的 bug 消失，是讓「有了任意寫之後想走 ROP」這條路變窄。** 這正是為什麼現代 ARM64 kernel exploit（尤其 Android）愈來愈多轉向 data-only attack（覆寫 `cred`、覆寫資料結構裡「不需要簽章驗證」的欄位，而非覆寫函式指標／回傳位址）——對應 [Ch 29](./29-data-only-attack.md) 的技術集在 ARM64 上的重要性比 x86-64 更高，因為 x86-64（截至目前主流桌面/伺服器 CPU）還沒有同等級的硬體指標簽章機制。

### 底層再挖一層：modifier 是什麼、簽哪些指標、為什麼簽章 gadget 能繞過

PAC 的簽章公式概念上是：

```
簽章值 = PAC_hash(金鑰, 指標值, modifier)
```

三個輸入缺一不可，理解 `modifier` 是什麼,是理解 PAC 強度上限的關鍵：

- **金鑰**：有五組獨立金鑰（`APIAKey`/`APIBKey` 給 instruction 類指標、`APDAKey`/`APDBKey` 給 data 類指標、`APGAKey` 給一般用途），存在只有 EL1 能讀寫的系統暫存器裡，EL0 程式碼拿不到，這是防禦的根基——如果金鑰能被讀出來，攻擊者就能自己算簽章,PAC 直接失效。
- **指標值**：要保護的那個指標本身。
- **modifier**：一個額外混進雜湊計算的「context」值，**不是隨便填零**。簽回傳位址時，Linux 用的 modifier 通常是**當下的 stack pointer（SP）**；簽一般資料指標或函式指標時，可以用另一個穩定的 context 值（例如某個 struct 的位址）。modifier 的作用是讓**同一個指標值在不同情境下簽出不同的簽章**——如果沒有 modifier,攻擊者只要在情境 A 偷到一個合法簽章,就能把它原封不動搬到情境 B 用（這叫 **replay**）。加了 SP 當 modifier 之後，情境 A 和情境 B 的 SP 通常不同，簽章就對不上。

具體到 kernel 怎麼用：函式入口執行 `PACIASP`（等同 `PACIA x30, sp`，用 instruction A key、以 SP 為 modifier 簽 `x30`），函式回傳前執行 `AUTIASP`（驗證並還原 `x30`）再 `RET`。所以**kernel 目前的 PAC 用法，主要保護的是「回傳位址」這一類指標，其次是部分編譯器插入的函式指標保護**；一般資料結構裡「不會被拿去跳轉」的指標（例如單純的 `next` 鏈結串列指標），PAC 完全不管——這也是為什麼 data-only attack 這條路線在 PAC 之下反而更好走:**PAC 只保護「會被拿去當控制流用」的那一小類指標,資料本身的完整性它不負責**。

**簽章 gadget（signing gadget）/ re-sign oracle 為什麼危險**：PAC 的安全性建立在「攻擊者拿不到任何可以呼叫 `PACIA`/`PACIB` 類指令、而且可以控制輸入的程式碼路徑」這個假設上。但 kernel（或任何大型程式碼庫）裡，合法程式碼本來就到處在呼叫 `PACIASP` 這類指令——如果攻擊者透過某個漏洞，能找到一段**合法存在、可以被誘發執行、而且會對攻擊者可控的值做簽章**的程式碼路徑（例如：某個函式把一個攻擊者可以透過系統呼叫間接控制的指標存進某個會被稍後簽章的欄位），就相當於擁有了一台「簽章機」——不需要偷到金鑰本身,只要能讓合法程式碼「代勞」簽出攻擊者想要的值。這類 gadget 在學術文獻裡被稱為 signing gadget 或更廣義的 **re-sign oracle**（讓你重複送入值、換回簽過章的輸出，本質上是一種 oracle 攻擊）。這正是本章前面表格把 PAC 標注「不是全面 CFI」的根本原因：**PAC 防的是「你自己算不出簽章」,不是「你永遠找不到一條合法路徑幫你簽」**；程式碼庫越大、間接呼叫越多，這類 gadget 存在的機率越高。

### 具體對照：同一個 ret2usr/ROP 攻擊鏈，在 x86-64 vs ARM64 長什麼樣

把本書 [Ch 11](./11-stack-overflow.md)、[Ch 13](./13-smep-smap.md) 的 stack overflow → ROP 套路，用 pseudo-asm 並排看兩個架構的差異：

```
x86-64（SMEP 擋 ret2usr，逼你走純 kernel ROP）：
  vulnerable_func:
      sub    rsp, 0x40          ; 分配 stack buffer
      call   copy_from_user     ; 漏洞：沒檢查長度，攻擊者控制的資料
                                 ; 可以寫過 buffer，蓋到 saved return address
      ...
      ret                       ; 直接從 stack 讀回傳位址並跳過去
                                 ; → 攻擊者已把它蓋成 gadget1 的位址
                                 ; → CPU 無條件相信這個值，跳過去

ARM64（PAC 開啟時，同一個漏洞）：
  vulnerable_func:
      sub    sp, sp, #0x40
      paciasp                   ; 進入時：簽章 x30（LR），modifier = 當下 SP
      str    x30, [sp, #0x38]   ; 把「簽過章的」x30 存進 stack
      bl     copy_from_user     ; 同一個漏洞：overflow 可以蓋到這個位置
                                 ; → 攻擊者能蓋的是「簽過章版本」的欄位，
                                 ;   但攻擊者填不出對應這個 SP 的合法簽章
      ldr    x30, [sp, #0x38]   ; 讀回被攻擊者覆寫過的值
      autiasp                   ; 驗證：對這個值 + 當下 SP 重算一次雜湊比對
                                 ; → 不符 → 把 x30 的高位打成無效樣式
      ret                       ; 用這個「已知無效」的值去跳
                                 ; → 直接 fault，而不是跳到攻擊者的 gadget
```

這組對照的重點不是「ARM64 比較難打」這麼籠統的結論,而是**攻擊者能蓋的位元組完全一樣多,蓋得進去這件事本身沒有被擋**——PAC 擋的是「蓋進去的值，CPU 願不願意相信它、拿去跳轉」這一步。這就是為什麼本章一開始的攻擊者地圖要把 PAC/BTI/MTE 放在「Control」這一格,而不是「Primitive」這一格：**它們不讓你的 bug 消失，只讓 bug 變成 primitive 之後，往控制流劫持方向走的那一步變得不可靠**。

## BTI：卡住 wild jump 的落點檢查

### 直覺

PAC 管的是「你跳轉用的指標本身有沒有被動過」，BTI 管的是另一件事：「你跳過去的那個位置，本來就允許被跳進來嗎？」

### 機制

ARMv8.5 引入 Branch Target Identification（BTI）。合法的間接跳轉目標在編譯時會被打上一個特殊的 `BTI` guard instruction（實際上是 HINT 空間的指令，對不支援 BTI 的核心而言只是個 no-op，保證向下相容）。CPU 執行 `BR`／`BLR`（間接分支指令）跳過去之後，如果落點**不是**一個合法的 BTI landing pad，直接產生 Branch Target Exception。

關鍵限制：**BTI 不保護 `RET`（函式回傳）**。它只管 `BR`/`BLR` 這種「跳到一個暫存器裡存的位址」的間接分支，回傳位址的保護是 PAC 的責任。這是刻意的分工：BTI 補的是「函式指標 / vtable / jump table 被覆寫後亂跳」這一類攻擊面，PAC 補的是「回傳位址被覆寫」這一類。兩者合起來才覆蓋控制流劫持的兩個主要入口。

Linux 支援從 5.8 起，`CONFIG_ARM64_BTI`（含 `CONFIG_ARM64_BTI_KERNEL` 讓 kernel 自身程式碼也打上 landing pad）開啟，可以用 `arm64.nobti` 在開機時關掉做對照實驗。user space 要吃到 BTI 保護，二進位檔也要用支援 BTI 的工具鏈（GCC 9+）編譯，舊的二進位檔不受影響（也不受保護）。

對攻擊者的影響：如果你的利用鏈是「覆寫一個 `struct ops` 裡的函式指標，之後觸發一次呼叫跳過去」——這是 [Ch 21](./21-heap-to-rip.md) `tty_struct`/`seq_operations` hijack 的核心套路——在開了 BTI 的 kernel 上，**你覆寫的那個函式指標必須指向一個合法的 BTI landing pad**，不能隨便指到 gadget 中間的任意位置。這把可用 gadget 的集合大幅縮小，但不是沒有：kernel 本身合法的函式入口本來就打了 landing pad，你仍可以把指標改成另一個**合法但非預期**的函式（例如直接改成 `commit_creds` 這種本身就是合法入口的函式），這條路 BTI 擋不住。

### 具體對照：同一個 `seq_operations` hijack，BTI 開和不開差在哪

對照 [Ch 21](./21-heap-to-rip.md) 的套路，覆寫一個 `struct seq_operations` 裡的 `.show` 函式指標，之後觸發一次對應的讀取操作讓 kernel 呼叫它：

```
BTI 關閉：
  struct seq_operations {
      ...
      void *show;   ← 攻擊者透過 UAF/OOB 把這個欄位改成任意位址 X
  };
  ...
  blr  x_show       ; 直接跳到 X，不論 X 是不是一個函式的正常入口
                     ; X 可以是某段 gadget 中間的任意一條指令

BTI 開啟：
  struct seq_operations {
      ...
      void *show;   ← 同樣可以被改成任意位址 X
  };
  ...
  blr  x_show       ; 跳到 X 之前，硬體先看 X 那個位置是不是合法 landing pad
                     ; X 開頭是  bti c          → 合法，繼續執行
                     ; X 開頭不是 bti c/j/jc     → Branch Target Exception，fault

  可用的 X 收斂成：編譯器在每個「可能被間接呼叫」的函式入口自動插的
  bti 指令位置——也就是 kernel 裡所有「正常函式」的入口，而不是任意
  gadget 中段。攻擊者能選的從「整個 kernel 的任意位元組」收斂成
  「kernel 裡所有合法函式的入口集合」。
```

這組對照說明 BTI 的實際防禦收益：**它不減少攻擊者能覆寫的位元組，只減少覆寫完之後「跳過去會成功」的落點集合**——從理論上任意位址收斂到合法函式入口集合，但「合法函式入口」本身就包含像 `commit_creds` 這種單呼叫就能提權的函式，所以 BTI 對這類攻擊鏈的防禦收益主要是擋住**傳統 ROP 式「跳進函式中段拼 gadget」**的做法,不是擋住「直接跳到另一個合法函式開頭」這條路。

## MTE：用硬體抓 UAF / OOB

### 直覺

軟體版的 KASAN（見 [Ch 44](./44-debug-toolchain.md)）靠編譯器插樁和 shadow memory 記錄「這塊記憶體現在合不合法用」，開銷相當大，通常只在 debug/fuzzing 環境開，production 不開。MTE 想做的是：把這個檢查**搬進硬體**，讓它便宜到可以在 production 上開。

### 機制

ARMv8.5 的 Memory Tagging Extension（MTE）把實體記憶體以 16 bytes 為一個 granule，每個 granule 配一個 4-bit 的 tag，存在額外的記憶體區域裡（不占用你原本的位址空間）。同時，每個指標本身也帶一個 4-bit 的 tag（存在指標高位的保留 bit）。硬體在執行記憶體存取時，比對「指標帶的 tag」跟「它指向那塊記憶體當下的 tag」是否一致——**不一致就 fault**。

allocator（kernel 的 SLUB、或 user space 的 heap）在配置一塊記憶體時，給它一個 tag，並把對應的指標也打上同一個 tag；釋放時，重新配一個不同的 tag。這樣：

- **Use-after-free**：你拿著舊指標（帶舊 tag）去存取已經被重新配置、換了新 tag 的記憶體 → tag 不符 → fault。
- **Out-of-bounds**：你的指標合法地指向 buffer 開頭，但往後走超出邊界，踩進下一個 granule（可能是別的物件、帶著不同 tag）→ fault。

這就是為什麼 Android 文件把它定位為硬體版的 tag-based KASAN——**同一套偵測邏輯，從軟體插樁換成硬體電路**，開銷低到可以考慮在部分 production 場景（高風險 process）開啟，而不只是 fuzzing 環境。Android 的 `android:memtagMode` 讓 app 選擇開啟，官方建議對暴露大量攻擊面的 process 用 `SYNC` 模式（同步偵測，立即 fault）而非 `ASYNC`（非同步，先記錄、之後批次檢查，偵測延遲但開銷更低）。

**兩個誠實的限制，攻擊者和防禦者都該知道：**

1. **同一個 16-byte granule 內的 overflow 抓不到**——如果你溢位的量小到還在同一個 tag 粒度內，MTE 看不出異狀。這和軟體 KASAN 的 byte-level 精度不同，MTE 是以 granule 為單位，有盲區。
2. **Tag 只有 4 bit（16 種值），存在機率性碰撞**——如果攻擊者能夠多次嘗試（例如一個可重複觸發的 race 或可重試的 UAF），用舊指標碰上「剛好被分配到同一個 tag」的新物件的機率是 1/16，多試幾次就可能繞過偵測而不觸發 fault。MTE 不是不可繞過的保證，是把攻擊成本從「穩定可用」推向「機率性、需要多次嘗試」。

### SYNC vs ASYNC：對攻擊者而言，差的不是「能不能繞」，是「繞的策略」

前面提到 Android 建議高風險 process 用 `SYNC` 模式，但這句話對攻擊者意味著什麼，值得拆開講：

- **SYNC 模式**：tag 不符的那一次存取,立即 fault,程式當場終止（kernel 視情境可能是 oops/panic，user space 是 SIGSEGV）。對攻擊者的意義：**你的 exploit 沒有「試一次、觀察結果、調整再試一次」的空間**——一旦踩中 tag mismatch，process/kernel 直接死亡，攻擊鏈必須在**第一次**就用對的 tag,不能靠不斷重試去碰撞那 1/16 的機率。這逼著攻擊者必須先用別的手段把 tag 洩漏出來（**tag leak**），而不是盲猜。
- **ASYNC 模式**：tag 不符時，硬體記錄下這次違規（寫進某個系統暫存器的旗標），但**不立即中斷執行**，而是等到下一次例外或排程點才回頭檢查、觸發處理。對攻擊者的意義：違規發生和被偵測到之間有一段時間差——如果你的利用鏈能在這段時間差裡完成關鍵操作（例如在違規被處理之前就已經達成 UAF 想要的效果），偵測到了也不影響你已經做完的事。這是 ASYNC 用效能換來的代價，Android 官方文件也點出這點,這是**為什麼高風險 process 建議用 SYNC 而非 ASYNC**——不是 SYNC 更快,是 SYNC 沒有這段時間差可利用。

**Tag leak 是繞過 MTE 的核心思路，不是盲猜 1/16**：與其靠機率性碰撞，更實際的攻擊路線是先找一個**資訊洩漏**管道把目標記憶體當下的 tag 值讀出來（例如透過某個旁路：timing、某個會回傳部分指標高位的 API、或者另一個獨立於 MTE 檢查之外的 OOB read），拿到正確 tag 之後，構造出的指標直接帶對的 tag，**MTE 這一關形同沒開**——因為 MTE 檢查的前提是「攻擊者不知道正確 tag 值」，一旦這個前提被打破，檢查本身並沒有任何阻擋力。這也是為什麼這類硬體 mitigation 的論文/writeup 裡,大量篇幅在討論「怎麼構造一個 tag oracle」,而不是單純討論 1/16 的碰撞機率——**機率性碰撞是沒有 leak 管道時的最後手段,真正穩定的攻擊鏈幾乎都繞著「怎麼先洩漏 tag」設計**。

在 kernel 端，HW KASAN（硬體 tag-based KASAN，基於 MTE）和 kernel 本身動態管理 tag storage 之間存在已知的實作張力——2024 年的 kernel patch 討論中，開發者因為 kernel 配置路徑的 non-preemptible context 限制，選擇在 HW KASAN 開啟時停用 tag storage 的動態管理（這段**未實測，為對 kernel mailing list 討論的轉述，實際行為請以你驗證的 kernel 版本為準**）。

## 三者合起來看：控制流劫持被夾在哪裡

```
   傳統 x86-64 ROP 攻擊鏈：
   overflow 回傳位址 ──▶ 直接改成 gadget 位址 ──▶ ret 跳過去 ──▶ 執行

   同一條路在開滿 PAC+BTI+MTE 的 ARM64 上：
   overflow 回傳位址 ──▶ PAC：你改的值簽章驗不過 ──▶ fault（擋下）
          │
          └─ 换成覆寫函式指標 ──▶ BTI：目標不是合法 landing pad ──▶ fault（擋下）
                  │
                  └─ 换成改成「合法」函式指標（如 commit_creds）──▶ 兩者都繞過
                          │
                          └─ 但觸發這次 overflow/UAF 的那一瞬間
                             若裝置開了 MTE：tag 不符，可能在你做任何事之前就 fault
```

這張圖的結論：**三個機制互相補位，單獨繞過一個不代表利用鏈能走完**，但「改成合法函式指標」這條路線目前沒有一個機制直接擋住——這也是為什麼 data-only attack（直接偽造資料而非劫持控制流，或者劫持到「本來就合法」的函式）在 ARM64 kernel pwn 的現代研究裡分量愈來愈重。

## Android 裝置生態：這些機制實際開了多少

前面三節把 PAC/BTI/MTE 的機制講到了原理層級，但寫 exploit 時必須面對一個更現實的問題：**你打的那一支裝置，到底開了哪些？** 這點沒有統一答案，取決於 SoC 世代、Android 版本、甚至同一款晶片不同批次的 fuse 設定，幾個已知的現實狀況：

- **PAC 在支援 ARMv8.3 的 SoC 上普遍存在，但 kernel-wide 保護是否開啟要看 GKI 的 `defconfig`**——`CONFIG_ARM64_PTR_AUTH_KERNEL` 是否打開，不同廠牌、不同 GKI 分支（5.10/5.15/6.1）可能不同，不能假設「這台裝置的 CPU 支援 PAC，kernel 就一定用了」。
- **BTI 需要二進位檔本身用支援 BTI 的工具鏈編譯才有效**——即使 kernel 開了 `CONFIG_ARM64_BTI_KERNEL`，如果某個 vendor module 是用舊版工具鏈編的，那個 module 裡的程式碼不帶 landing pad，BTI 檢查對它形同沒開。這對攻擊者的意義：**vendor driver（本章前面強調過的 Android LPE 重災區）往往是 BTI 覆蓋率最低的地方**，剛好和漏洞密度最高的地方重疊。
- **MTE 的硬體需求比 PAC/BTI 都更嚴苛**——需要 SoC 設計時就把 tag storage 的額外記憶體預留出來，不是單靠軟體/固件更新就能補上。截至目前,主流旗艦 SoC 才開始在部分型號支援，中低階裝置大概率完全沒有這個硬體能力，**在這些裝置上，MTE 這一關根本不存在，不用考慮繞不繞過的問題**。

實務上，拿到一台目標裝置，判斷這三個機制各自的狀態，比直接套用本章的原理推演更重要：

```
查 CPU 硬體能力：     cat /proc/cpuinfo | grep Features
                      看有沒有 paca/pacg（PAC）、bti、mte

查 kernel 是否啟用：   zcat /proc/config.gz 2>/dev/null | grep -E \
                      'ARM64_PTR_AUTH|ARM64_BTI|ARM64_MTE'
                      （部分裝置不開放讀 /proc/config.gz，退而求其次
                      看 dmesg 開機訊息或已知該機型的公開資料）

查二進位檔是否吃到保護： readelf -n <binary> | grep -i 'AArch64 PAuth\|BTI'
                      （ELF note section 會記錄編譯時是否啟用）
```

這張流程圖的結論：**本章前面的機制講解是「天花板」——理論上這些保護能做到什麼程度；實際打一台裝置時，你面對的往往是天花板之下某個不確定的高度**，每一台目標都要重新確認,不能把一台裝置驗證過的結論直接套用到另一台。

## 對比與取捨

| 機制 | 對應 x86-64 | 擋什麼 | 已知限制 |
|---|---|---|---|
| EL0/EL1 | ring 3/0 | 權限邊界本體 | 無額外限制，概念相同 |
| PAN/UAO | SMAP | kernel 直接讀寫 user 記憶體 | UAO 只是效能優化，不影響安全語意 |
| PXN | SMEP（推論對應） | kernel 執行 user 頁程式碼 | 機制層級類比，非官方明文對應 |
| kpti= | pti=off（KPTI） | Meltdown 類 info leak | 參數名不同，行為概念相同 |
| PAC | （無對應） | 回傳位址 / 指標被覆寫後拿去跳轉 | 不是全面 CFI；簽章 gadget 可能繞過；kernel-wide 保護較晚才完整 |
| BTI | （無對應，部分類似 Intel CET 的 IBT） | 間接分支跳到非法落點 | 不保護 RET；舊二進位檔不受保護 |
| MTE | （無對應，部分類似軟體 KASAN 但硬體化） | UAF / OOB 存取 | granule 內 overflow 抓不到；4-bit tag 有機率性碰撞 |

## 踩雷集錦

1. **把 PAC 當成「kernel 全都簽過章，ROP 完全死了」**：早期 PAC 主要保護 user space 與 KVM guest，kernel 自身的回傳位址保護是後續才補上的能力，且即使開了也不是對每個指標都簽章——實際保護範圍要看 kernel config 與版本，不要一概而論。
2. **以為 BTI 擋得住所有間接跳轉攻擊**：BTI 不檢查 `RET`，只檢查 `BR`/`BLR`。如果你的攻擊鏈走的是回傳位址劫持而非函式指標劫持，BTI 根本不在路上，要看的是 PAC 擋不擋得住。
3. **把 syscall ABI 的 x0–x5 和 AAPCS 函式呼叫的 x0–x7 搞混**：syscall 最多 6 個參數（x0–x5），x8 放 number；一般函式呼叫可以用到 x0–x7（最多 8 個整數參數）。寫 shellcode 或組 syscall 呼叫時，塞第 7、8 個參數是沒用的，Linux syscall 介面本身就沒有對應的暫存器。
4. **以為 MTE 一開就能抓住所有 UAF**：4-bit tag 只有 16 種值，機率性碰撞是真實存在的限制；同一個 16-byte granule 內的小幅 overflow 完全在它的偵測範圍之外。
5. **拿 x86-64 的 fake `iret` frame 版面直接套用在 ARM64 的 `eret`**：兩者控制回到低特權層的機制概念相似，但儲存的狀態欄位（`SPSR_EL1`/`ELR_EL1` vs `cs/rip/rflags/rsp/ss`）不是一一對應，照搬會直接讓你的 exploit 在狀態還原這一步死掉。
6. **以為「找到一個 signing gadget」等同「拿到 PAC 金鑰」**：這是兩個完全不同量級的事。簽章 gadget 讓你能對**特定、受那段合法程式碼邏輯限制**的輸入值得到有效簽章,拿到金鑰則是能對**任意值**簽章。多數公開研究屬於前者——構造出能用的簽章 gadget 往往還要搭配其他漏洞才能拼出完整利用鏈,不是找到一個 gadget 就直接贏了。
7. **把 MTE 的 SYNC/ASYNC 差異簡化成「SYNC 比較安全，ASYNC 比較快」然後就不再深想**：這句話對防禦端成立，但對攻擊端的意義是「SYNC 逼你必須先 leak tag 才能動手，ASYNC 給你一段可利用的時間差」——理解這個差異決定你會把力氣放在找 tag oracle，還是放在設計一條能在偵測延遲內完成的攻擊鏈。

## 進階：再往深一層

想往下深挖的方向：

- **PACMAN 類攻擊**：利用推測執行（speculative execution）側通道去「暗中驗證」PAC 簽章猜測，是繞過 PAC 的研究方向之一，屬於硬體層側通道攻擊而非傳統記憶體破壞，值得之後對照 Spectre/Meltecy 類研究一起看。
- **Camouflage / PAL 等學術 CFI 方案**：這些論文把 PAC 用得更徹底（不只保護回傳位址，連 forward-edge 的函式指標呼叫也納入），是「PAC 的極限用法」長什麼樣的參考。
- **GKI 與 vendor hook 生態**：Android 的 Generic Kernel Image 把 core kernel 和各廠牌客製碼切開，透過 `android_rvh_*`／`android_vh_*` 這類 vendor hook 讓廠商不用 fork 整個 kernel 就能插自己的邏輯。這個設計一方面讓 Google 能統一升級核心、縮小碎片化，另一方面**vendor driver（相機、GPU、modem、各種客製周邊驅動）仍是大量 out-of-tree、審查強度遠低於 mainline 的程式碼**——這正是為什麼 Android kernel LPE 的重災區常常不在 core kernel（netfilter、io_uring 這些 Part 5 講的子系統），而在某個廠牌自己寫的驅動裡。想往這個方向深入，去讀 Project Zero 近年公開的 Android 0-day writeup，絕大多數命中點都是 vendor driver。

## 動手練習

找一台支援 ARM64 的虛擬環境（QEMU `-M virt -cpu max`，或實體 ARMv8.3+ 的裝置如近年的 Raspberry Pi / 雲端 ARM 實例）：

1. 編一個最小的 kernel module，故意留一個 stack overflow（複用 [Ch 11](./11-stack-overflow.md) 的靶題改成 ARM64 編譯），分別在 `CONFIG_ARM64_PTR_AUTH_KERNEL=y` 開和關的兩顆 kernel 上觸發，比較 panic 訊息的差異——開 PAC 時你應該會看到 `AUTIA` 驗證失敗產生的特定 oops 訊息，而非單純的跳轉到非法位址。
2. 用 `cat /proc/cpuinfo` 看你手上的 ARM64 裝置 `Features` 欄位是否列出 `paca`（Address Authentication）、`bti`、`mte`，確認你的測試環境實際支援哪些機制——很多雲端 ARM 實例目前仍不給 MTE。
3. 比較同一題 x86-64 版和 ARM64 版的 exploit 骨架，列出你需要改寫的每一行組語，確認自己說得出「為什麼這裡要改」而不是照抄範本。

## 本章重點整理

- ARM64 的權限模型（EL0/EL1）、syscall ABI（svc + x8 + x0–x5）、位址空間切分（TTBR0/TTBR1）、PAN/UAO 都是 x86-64 對應機制的「改名換姓」，原理可以直接遷移理解。
- PAC、BTI、MTE 是 x86-64（至少截至目前的主流桌面/伺服器 CPU）沒有對應物的硬體機制，分別堵住「回傳位址/指標被覆寫」「間接分支跳到非法落點」「UAF/OOB 記憶體存取」三個攻擊面。
- 三者都有明確、可查證的限制（PAC 非全面 CFI、BTI 不護 RET、MTE 有 granule 盲區與機率性碰撞），不是「開了就天下太平」。
- Android 的 GKI/vendor hook 架構把大量客製驅動擺在審查較弱的 out-of-tree 區域，是 Android LPE 重災區的結構性原因，和硬體 mitigation 本身無關。

## 自我檢核

- [ ] 能不看筆記解釋 PAC、BTI、MTE 分別擋住控制流劫持鏈的哪一步，以及為什麼三者要分工而非合一
- [ ] 能說出 ARM64 syscall ABI 和一般函式呼叫 ABI 的暫存器用法差異，解釋為什麼塞第 7 個 syscall 參數沒有意義
- [ ] 能解釋為什麼「改成合法但非預期的函式指標」目前沒有被 BTI 或 PAC 直接擋住
- [ ] 如果有人問你「Android LPE 為什麼那麼多在 vendor driver」，能從 GKI 架構的角度回答，而不是只說「ARM64 比較不安全」

## 延伸閱讀

### 官方文件 / Spec

- **[Pointer authentication in AArch64 Linux](https://docs.kernel.org/arm64/pointer-authentication.html)**
  - **讀哪裡**：整篇不長，重點看「Basic support」與「Key management」兩節
  - **能學到什麼**：kernel 如何管理 PAC 金鑰、哪些指標被簽章、`CONFIG_ARM64_PTR_AUTH_KERNEL` 的實際涵蓋範圍
  - **前提知識**：本章「PAC」一節的機制說明

- **[Arm Memory Tagging Extension（Android 官方文件）](https://source.android.com/docs/security/test/memory-safety/arm-mte)**
  - **讀哪裡**：「How MTE works」與「Limitations」兩節
  - **能學到什麼**：MTE 的 SYNC/ASYNC 模式差異、Android 如何透過 `memtagMode` 導入、官方自己承認的偵測盲區
  - **前提知識**：本章「MTE」一節

- **[Arm security features for Linux system administrators](https://developer.arm.com/community/arm-community-blogs/b/operating-systems-blog/posts/arm-security-features-for-linux-system-administrators)**
  - **讀哪裡**：PAN / BTI 兩節
  - **能學到什麼**：ARM 官方對 PAN 與 SMAP 的對應關係給出的說法，補足本章「推論」標注的部分
  - **前提知識**：無，適合先讀這篇再回來對照本章

### 部落格 / 技術文章

- **[Google Project Zero blog（Android/ARM64 相關文章）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：搭配本書 README 已列的 P0 blog，特別去找標籤或關鍵字含 "Pixel"、"Android"、"ARM" 的文章
  - **讀哪裡**：任何 vendor driver LPE 的 writeup，重點看作者怎麼在開了 PAC/BTI 的裝置上規劃利用鏈
  - **為什麼值得讀**：這是目前把「現代 ARM64 kernel pwn 實戰長什麼樣」寫得最清楚的來源，而且永遠是最新的裝置與 kernel 版本

### 論文

- **ARM Pointer Authentication based Forward-Edge and Backward-Edge Control Flow Integrity for Kernels**（arXiv，見本章搜尋結果列出的連結）
  - **核心貢獻**：探討把 PAC 用到 kernel 的 forward-edge（函式指標呼叫）與 backward-edge（回傳位址）兩種保護，比單純的回傳位址簽章更完整
  - **讀哪裡**：Motivation 與 Design 兩節，實作細節可跳過
  - **和本章的關聯**：補足本章「PAC 不是全面 CFI」這句話背後的學術脈絡——這篇論文就是在補那個缺口

- **Camouflage: Hardware-assisted CFI for the ARM Linux kernel**（arXiv，見本章搜尋結果列出的連結）
  - **核心貢獻**：具體分析 kernel 程式碼裡大量「合法但會被攻擊者濫用」的間接呼叫路徑，並討論用 PAC 強化 kernel CFI 時會踩到哪些 kernel 特有的程式寫法問題
  - **讀哪裡**：Threat Model 一節，直接對應本章「簽章 gadget / re-sign oracle」小節的學術版本
  - **和本章的關聯**：把本章用文字描述的「signing gadget 為什麼危險」講成正式的威脅模型，適合想把這段論證講得更嚴謹的讀者

把 ARM64 的硬體關卡搞懂之後，下一步要往「隔離邊界本身」看——kernel LPE 在容器與虛擬化堆疊裡意味著什麼，這決定了你手上的一個 kernel bug 到底值多少。

→ [Ch 47 容器 / 虛擬化逃逸邊緣](./47-container-vm-escape.md)
