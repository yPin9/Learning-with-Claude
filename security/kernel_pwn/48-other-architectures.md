# Ch 48 — 其他架構速覽：RISC-V / MIPS / 嵌入式 Linux kernel 差異

> **目標**：把本書在 x86-64 和 ARM64（[Ch 46](./46-arm64-kernel-pwn.md)）上建立的 kernel pwn 方法論,再往外推一層,看 RISC-V、MIPS 這兩個架構,以及嵌入式 Linux 這一整類環境,分別要重新校準哪些假設——並理解「嵌入式反而常常更好打」這句話背後的具體原因,而不是停在印象層次。
>
> **環境**：概念/生態速覽章,以 RISC-V 近期特權架構規格(Privileged Architecture)、常見 32/64-bit MIPS 嵌入式路由器韌體(基於較舊 2.6/3.x/4.x 系列 kernel)為參照。本章涉及硬體行為的細節,沒有十足把握的地方會降級措辭或標注未實測。

## 為什麼要單獨講這兩個架構?

x86-64 是桌面/伺服器的王者,ARM64 是手機/平板的王者,但世界上還有一大片 kernel 跑在另外兩類硬體上:

- **RISC-V**:一個開放、免授權費的指令集架構,近幾年從學術圈快速擴散到嵌入式 SoC、部分伺服器實驗項目。它的特權架構設計乾淨、規格公開,是「看一個全新架構怎麼設計特權分層」的好教材,也是你會在新一代嵌入式裝置上愈來愈常碰到的目標。
- **MIPS**:老牌架構,曾是嵌入式和路由器市場的主力(雖然近年被 ARM 架構持續侵蝕份額)。大量現役的家用路由器、工業控制設備、舊款 IoT 裝置仍跑著 MIPS kernel——而且往往是**十年前的 kernel 版本**,防禦幾乎是空的。

這兩個架構加上「嵌入式 Linux」這個更大的環境類別,構成了 kernel pwn 的第三張地圖:**不是因為技術上特別難,而是因為方法論的每個假設都要重新檢查**——你在 x86-64/ARM64 上訓練出的直覺(「kernel 版本夠新」「mitigation 應該都開了」「地址會被隨機化」)在這裡大概率是錯的,而且錯的方向通常對攻擊者有利。

## 先建立直覺:同一套方法論,不同的校準旋鈕

```
                   x86-64 / 近期 ARM64 Android          RISC-V / MIPS 嵌入式
kernel 版本        6.x,持續更新                         2.6 ~ 5.x,凍結多年不更新
SMEP/SMAP/PAN      有,預設開                            RISC-V 等效機制剛起步;MIPS 無
KASLR              有,預設開                            常常沒開,甚至編譯時關閉(XIP 需求)
Stack canary       有                                   部分嵌入式 BSP 編譯選項關閉
除錯環境            QEMU + gdb remote,一行指令起手        客製 toolchain、客製 bootloader,起手就要啃說明書
攻擊面寬度          大(多子系統、多 syscall)              窄(精簡過的 kernel config,驅動是客製重災區)
漏洞密度            相對低(持續被 fuzz/審查)              相對高(vendor driver 幾乎沒人審查)
硬體 CFI(PAC/BTI 類) ARM64 已量產;x86-64 無對應       RISC-V 規格剛定案,硬體/工具鏈支援早期;MIPS 無
失敗代價            高(panic 會被監控、被注意到)          低(自動重啟,通常沒人注意)
```

這張表的結論不是「嵌入式 kernel pwn 比較簡單」——除錯環境的建置成本可能比 x86-64 高得多(客製工具鏈、沒有現成 gdbserver、要自己接 JTAG 或 UART)。結論是:**一旦你把除錯環境搭起來,剩下的利用難度通常比桌面/手機 kernel 低**,因為防禦密度低了一整個世代。

## RISC-V:特權架構與 syscall ABI

### 特權等級:M/S/U,比 ARM64 的 EL 少一層,但概念相通

RISC-V 的特權架構(Privileged Architecture)定義三個模式:

| 模式 | 類比 | 角色 |
|---|---|---|
| **M-mode**(Machine) | 類似 ARM64 的 EL3,但 RISC-V 規定**M-mode 是唯一強制存在的模式** | 開機最早執行的固件層,通常是 bootloader/firmware(例如 OpenSBI) |
| **S-mode**(Supervisor) | 類似 ARM64 的 EL1 / x86-64 ring 0 | Linux kernel 本身跑在這裡 |
| **U-mode**(User) | 類似 ARM64 的 EL0 / x86-64 ring 3 | 一般程式 |

跑 Linux 的系統通常是 M + S + U 三層都有:M-mode 跑 SBI(Supervisor Binary Interface)固件,負責啟動並把控制權交給 S-mode 的 kernel,kernel 本身管理 U-mode 的使用者程式。這個三層結構和 x86-64/ARM64 的「kernel 直接管硬體」不完全一樣——**kernel(S-mode)要透過 SBI 呼叫向 M-mode 固件請求一部分特權操作**(例如某些電源管理、某些 CPU 核心控制),這是 RISC-V 特有的一層,x86-64/ARM64 沒有直接對應物。對 kernel pwn 的實務影響:S-mode 裡的 kernel LPE,目標仍是「在 S-mode 裡拿到完整控制權(等同 root)」,通常不需要進一步打穿到 M-mode——這和拿到 ring 0/EL1 權限後的終點一致。

### syscall ABI:a7 放 number,呼應 x86-64 的 rax、ARM64 的 x8

RISC-V 的 syscall 觸發指令是 `ecall`。從 U-mode 執行 `ecall` 會觸發一個 "environment-call-from-U-mode" 例外,把控制權交給更高特權層(S-mode kernel)處理。

| | x86-64 | ARM64 | RISC-V |
|---|---|---|---|
| 觸發指令 | `syscall` | `svc #0` | `ecall` |
| syscall number | `rax` | `x8` | `a7` |
| 參數 1–6 | `rdi,rsi,rdx,r10,r8,r9` | `x0–x5` | `a0–a5` |
| 回傳值 | `rax` | `x0` | `a0` |

這組對照表背後的規律值得記住,因為它幾乎是每個架構 syscall ABI 的固定模式:**一個暫存器放 syscall number,接下來幾個暫存器依序放參數,第一個參數暫存器兼職放回傳值**。只要抓住這個規律,換一個新架構時,找到對應暫存器的速度會快很多——不用每次從零記一套全新規則。

### mitigation 現況:比 ARM64 年輕得多的生態

這是 RISC-V 和 ARM64 最大的差異:**ARM64 的硬體 mitigation(PAC/BTI/MTE)已經是量產裝置的現實,RISC-V 同等級的機制大部分還在規格階段或剛開始落地**。RISC-V 的控制流完整性擴展由 **Zicfilp**(forward-edge,對應 ARM64 BTI 的角色)和 **Zicfiss**(backward-edge,對應 ARM64 PAC 保護回傳位址的角色)兩個子規格組成,規格本身(Shadow Stacks and Landing Pads,v1.0)已於近年正式定案(ratified)。

把機制拆開看,和本書 [Ch 46](./46-arm64-kernel-pwn.md) 的 BTI/PAC 對照會更清楚:

- **Zicfilp 用 `LPAD` 指令標記合法的間接跳轉落點**——概念上和 ARM64 的 BTI landing pad 幾乎是同一套設計哲學：合法的間接呼叫/跳轉目標在編譯時插入 `LPAD`,`LPAD` 帶一個 label 值,跳轉前把預期的 label 放進一個指定的暫存器,CPU 執行到 `LPAD` 時比對兩者是否一致,不一致就丟出例外。和 BTI 一樣,**它只管「跳到哪裡」,不管「跳轉用的值有沒有被動過」**。
- **Zicfiss 提供一個獨立的 shadow stack 來保護回傳位址**,和 ARM64 用 PAC「簽章」回傳位址的做法走的是不同路線——shadow stack 的思路更接近 x86 Intel CET 的 shadow stack(而非 PAC 的指標簽章):函式呼叫時把回傳位址額外存一份進一個攻擊者寫不到的獨立 stack,函式回傳前比對一般 stack 上的回傳位址和 shadow stack 上存的那一份是否一致,不一致就代表一般 stack 被動過。這代表 RISC-V 在 backward-edge 保護上選的是「多一份不可竄改的副本去比對」,而不是 ARM64 的「用密碼學簽章證明沒被動過」——兩條路線最終都想擋住覆寫回傳位址的攻擊,但底層機制完全不同,不能把本書 [Ch 46](./46-arm64-kernel-pwn.md) PAC 的 modifier/簽章 gadget 那套分析直接套用到 RISC-V 上。

**截至目前主流 RISC-V Linux 發行版與硬體的支援程度仍在早期階段,規格定案不代表硬體已經普及、也不代表 kernel/工具鏈的支援已經成熟,不應假設你手上的 RISC-V 裝置已經具備這類保護**——這點請以你實際測試的硬體與 kernel 版本為準,RISC-V 生態演進速度快,本章內容的時效性比 x86-64/ARM64 的章節更短。對攻擊者的實務意義很直接:**在規格定案到硬體普及這段空窗期,RISC-V 上的 kernel ROP/ret2usr 類攻擊鏈,目前大概率不需要考慮任何等同 PAC/BTI 的硬體阻礙**——這是本章「嵌入式/新興架構反而常常更好打」這條主線在 RISC-V 身上的具體體現。

同理,SMEP/SMAP/PAN 這類「kernel 不可碰 user 記憶體/執行 user 頁」的保護,在 RISC-V 上有自己的頁表權限位機制可以達成類似效果(U 位元控制某頁是否可被 U-mode 存取,配合 S-mode 的存取檢查),但**具體到哪個版本的 Linux RISC-V port 預設啟用到什麼程度,建議直接查你要打的那顆 kernel 的 config 與原始碼,不要套用 x86-64/ARM64 的經驗值假設它「應該」開了什麼**。

### 生態現況

RISC-V 的 Linux 支援主要集中在少數開發板(如 StarFive VisionFive、SiFive 系列)和部分嵌入式 SoC,伺服器級產品仍是少數廠商的早期產品。對 kernel pwn 學習者的實務建議:**用 QEMU 的 `riscv64` 目標跑 mainline kernel**,是目前成本最低、最貼近「未來 RISC-V 生態長什麼樣」的練習環境——這和你在 Part 0 用 QEMU 跑 x86-64 kernel 的流程幾乎一致,只是把 `-M q35` 換成 RISC-V 對應的 machine type,把 toolchain 換成 `riscv64-linux-gnu-*`。

## MIPS:路由器韌體裡的常客

### 大小端(Endianness)不是細節,是會讓你 payload 直接失效的分野

MIPS 同時存在大端(big-endian,`mips`)和小端(little-endian,`mipsel`)兩種變體,**同一份組語原始碼,編譯成 `mips` 和 `mipsel` 跑出來的位元組順序完全相反**。對 kernel pwn 實務的直接影響:

- 構造 ROP chain、shellcode、任何需要手寫位址或立即數的 payload,**要先確認目標是 `mips` 還是 `mipsel`**,位址的位元組順序寫反,payload 不會報一個清楚的錯誤,而是悄悄跳到完全不是你以為的位置——這是除錯這類 exploit 時最容易浪費時間的地方。
- 家用路由器市場裡 `mipsel`(小端)相對常見,但**不能假設**——拿到一份韌體,第一步永遠是用 `file` 或 `binwalk` 確認架構與端序,再動手。

### Cache coherency 的坑:DMA 和指令快取不會自動幫你同步

這是 MIPS 上一個真實存在、而且在 x86-64/ARM64 上大多數人沒機會踩到的坑:**部分 MIPS SoC 不保證硬體層級的 cache coherency**,尤其是:

- **指令快取(I-cache)和資料快取(D-cache)不同步**:如果你的利用鏈牽涉到「寫一段資料,之後把它當程式碼執行」(例如某些需要動態產生/修改程式碼的場景),寫進去的資料停留在 D-cache 裡,而 CPU 抓指令時走的是 I-cache,**兩者不保證看到同一份最新內容**,除非明確執行快取同步操作(kernel 裡對應的是 `flush_icache_range()` 之類的函式)。這在 x86-64 上幾乎不是問題(x86 的快取一致性模型對這類情境處理得透明),在 MIPS 上是真實會讓你的 shellcode「寫進去了,但 CPU 執行的是舊內容」的來源。
- **DMA 與 cache 的同步**:部分 MIPS 平台的周邊裝置透過 DMA 直接寫記憶體,不經過 CPU 的 cache 層——如果 CPU 剛好對同一塊記憶體有 cache 住的舊資料,你讀到的可能是 cache 裡的舊值而非 DMA 剛寫入的新值,kernel 驅動程式裡常見要手動呼叫 `dma_cache_wback_inv()` 這類函式明確地把 cache 同步掉。這類問題本質上是**硬體層級的一致性假設在 MIPS 上比 x86-64 弱**,具體某顆 SoC 是否受影響、受影響到什麼程度,**請以該 SoC 的技術手冊為準,這是高度硬體特定的行為,本章只提供「要注意這類坑」的警示,不給出放諸四海皆準的結論**。

### kernel 版本與 mitigation 現況

路由器韌體常見的 MIPS kernel 落在 2.6.x~4.x 這個區間,原因是:

- 原廠出貨時用的是當時 SoC vendor 提供的 BSP(Board Support Package),這份 BSP 往往基於廠商自己 fork 的某個舊版 kernel,**之後極少跟進 mainline 更新**。
- 消費性路由器的韌體更新頻率低,很多裝置出廠後幾年內完全沒再收到 kernel 層的安全更新。

這帶來的直接後果:**這些裝置上大概率沒有 SMEP/SMAP 等效機制、沒有 KASLR(甚至編譯時的 base address 是固定的,方便 XIP 之類的嵌入式啟動需求)、常常連 stack canary 都在 BSP 編譯選項裡被關掉以節省空間**。本書 Part 2、Part 4 花了大量篇幅講的「繞過 SMEP/SMAP」「繞過 KASLR」,在這類目標上經常直接變成「不需要繞,因為它根本沒開」。

具體把這份「缺什麼」清單對照本書章節看會更有感:

| 本書章節講的 mitigation | 典型路由器 MIPS kernel 的現況 |
|---|---|
| [Ch 13](./13-smep-smap.md) SMEP/SMAP | 無等效機制;ret2usr 式的「跳回 user space 執行 shellcode」通常直接可用 |
| [Ch 14](./14-kaslr-infoleak.md) KASLR | 常見完全沒開,kernel 與驅動的載入位址固定,不需要任何 info leak 就能算出目標函式位址 |
| [Ch 15](./15-kpti.md) KPTI | 這些 kernel 版本大多早於 Meltdown(2018)公開前就已經凍結,連 KPTI 的程式碼都不存在 |
| stack canary([Ch 11](./11-stack-overflow.md)) | 部分 BSP 編譯選項為了省空間直接關閉 |
| SLUB hardening([Ch 9](./09-slub-hardening.md)) | 舊版 SLUB 實作,沒有 FREELIST_HARDENED/RANDOM 等後期才加入的硬化 |

**一個具象的例子**(未實測,為依據上述已知版本特徵推演的理論預期行為,實際結果請以你手上的裝置驗證為準):假設你在一台跑 2.6.36 分支 MIPS kernel 的路由器上找到一個 stack buffer overflow——因為沒有 KASLR,目標函式(例如某個等效 `commit_creds`/`prepare_kernel_cred` 的程式碼位址)在每次開機都是同一個固定值,不需要任何 info leak 步驟;因為沒有 stack canary,溢位可以直接覆寫到回傳位址而不會先踩到一個會讓程式提前崩潰的檢查值;因為沒有 SMEP 等效機制,甚至可以直接把回傳位址指向一段放在 user space 的 shellcode。本書 Part 2 整個 Part 花篇幅處理的「繞過」,在這台裝置上全部變成「不存在,直接跳過」。

### 具體對照:同一個利用鏈,x86-64 要先 leak,MIPS 路由器不用

把本書 [Ch 14](./14-kaslr-infoleak.md) 教的「先 leak 再打」套路,和一台沒開 KASLR 的 MIPS 路由器並排看:

```
x86-64(KASLR 開啟,標準流程):
  1. 觸發一個 info leak primitive(例如 uninitialized stack 洩漏、
     某個 oracle 式的側通道),拿到一個 kernel 位址
  2. 用這個位址反推 kernel base 的隨機化 offset
  3. 用算出來的 offset,把寫死在 exploit 裡的「相對位址」
     (例如某個已知 gadget、commit_creds 的位置)校正成
     「這次開機實際的絕對位址」
  4. 才能開始構造 ROP chain / 覆寫目標

MIPS 路由器(無 KASLR,base address 編譯時固定):
  1. (沒有這一步——位址本來就是固定的)
  2. (沒有這一步)
  3. 直接把 exploit 裡寫死的絕對位址當成這次、
     以及下一萬次開機都成立的值使用
  4. 直接構造 ROP chain / 覆寫目標
```

這組對照解釋了為什麼本章一開始的表格把 MIPS 嵌入式的「漏洞密度」標成相對高——**不是因為 MIPS 的 bug 比較多,是因為同一個 bug,少了一整個 info leak 階段要構造,利用鏈的工程量直接少了一截**。對剛接觸 kernel pwn 的人來說,拿一台沒開 KASLR 的嵌入式裝置練手,也是驗證「我真的會從 primitive 走到控制流劫持」這件事本身的好起點——把 info leak 這個通常最磨人的步驟暫時拿掉,先確認後半段利用鏈你是真的會寫,而不是卡在 leak 這一步就放棄。

### 實務補充:幫一台 MIPS 路由器建開發/除錯環境要多走幾步

對照本書 Part 0 用 QEMU 一行指令跑起 x86-64 kernel 的流程,MIPS 韌體的環境建置通常要多走這幾步,先列出來避免你低估前置工作量:

1. **交叉編譯工具鏈(cross toolchain)**:你的開發機大概率不是 MIPS,需要 `mipsel-linux-gnu-*` 或對應的工具鏈來編譯任何要塞進裝置或模擬環境的程式碼(exploit、測試用 kernel module)。
2. **韌體拆解**:用 `binwalk` 從原廠韌體映像裡拆出根檔案系統(常見是 SquashFS)與 kernel 映像,確認 bootloader(常見 U-Boot)如何把 kernel 載入記憶體——這一步決定你能不能用 QEMU 的 `-kernel`/`-initrd` 直接模擬起來,還是必須在實機上接 UART/JTAG 才能動手。
3. **選擇模擬方式**:QEMU 的 **system mode**(`qemu-system-mips`/`mipsel`)模擬整台機器,包括 kernel 本身,適合分析 kernel 層漏洞;**user mode**(`qemu-mipsel-static` 之類)只模擬單一使用者態程式的指令集,適合快速驗證一段 shellcode 或單一二進位檔的行為,但不會有真實的 kernel 環境可以互動——分析 kernel pwn 目標通常要走 system mode,這點容易被只做過 user-space pwn 的人忽略。
4. **除錯介面**:如果韌體本身沒有附帶除錯用的 gdbserver,你常常要自己想辦法把一支靜態編譯的 gdbserver 塞進韌體的根檔案系統(或透過 NFS/TFTP 等方式讓裝置在開機時掛載),才能在實機上掛上 remote gdb。

這份清單呼應本章前面的結論:**嵌入式環境的「好打」是指利用階段的防禦密度低,不是指整個流程輕鬆**——環境建置本身常常比桌面/手機的 kernel pwn 練習多花數倍時間,這筆成本要提前算進你的時間規劃。

## 嵌入式 Linux 的共通差異:為什麼它反而常常更好打

把 RISC-V 和 MIPS 的具體細節抽掉,留下的是嵌入式 Linux kernel 這整個環境類別共通的幾個特徵,這些才是真正決定「好不好打」的關鍵:

### 1. 舊 kernel 版本 = 舊漏洞還活著 + 缺現代防禦

嵌入式裝置的 kernel 版本往往凍結在出貨當下,之後不動。這代表:

- 之後所有主線 kernel 修補過的漏洞,在這台裝置上**可能仍然存在**——不需要找新漏洞,翻舊 CVE 清單對照這台裝置的 kernel 版本,常常就能找到現成、公開、甚至有 PoC 的利用對象。
- Part 4 講的那些「2023+ mitigation」(random kmalloc caches、SLAB_VIRTUAL、CFI/KCFI)全部建立在近期 kernel 版本之上,舊 kernel 連這些機制的程式碼都不存在,根本沒有繞過的必要。

### 2. 客製 driver 多,審查密度低

嵌入式裝置的「與眾不同」幾乎全部體現在驅動層——某個 SoC 特有的 GPIO 控制、某個廠牌特有的網路晶片驅動、某個客製的 ioctl 介面給使用者空間的管理程式用。這些驅動:

- 大部分是 **out-of-tree**,從沒進過 mainline kernel 的審查流程(對照 [Ch 38 — Android / 廠商 kernel 子系統](./38-android-vendor-subsystems.md)講的 vendor driver 問題,嵌入式路由器的客製驅動是同一類問題,只是換了產業)
- 幾乎沒有被 Syzkaller([Ch 43](./43-syzkaller.md))這類 fuzzer 覆蓋過——Syzkaller 的 syscall 描述(syzlang)主要針對 mainline 子系統撰寫,一個廠牌自己發明的 ioctl 介面,fuzzer 根本不知道怎麼產生有效輸入去打它。
- 程式碼品質參差不齊,邊界檢查疏漏、整數處理錯誤的密度遠高於經過多年 review 的 mainline 子系統。

### 3. 缺現代 mitigation 的根本原因:資源與相容性限制,不是疏忽

嵌入式 SoC 的 CPU 通常比桌面/伺服器弱得多,記憶體也小得多(路由器級 SoC 常見 64–256MB RAM 這個量級)。KASLR、完整的 SLUB hardening、CFI 這些機制都有實際的效能與記憶體開銷,廠商在產品定義階段為了成本與相容性,很多時候是**主動選擇關閉或不啟用**,而不是不知道這些機制存在。這一點對理解「為什麼嵌入式更好打」很重要:**不是防禦技術落後,是產品的成本結構讓廠商沒有動機投入**。

### 4. 裝置重啟容忍度高,等同給了攻擊者免費的重試機會

桌面/伺服器 kernel panic 是重大事故,會被監控系統立刻標記、觸發告警;嵌入式裝置(尤其家用路由器)panic 之後的標準行為往往只是**自動重啟,沒有任何人會注意到**。這對攻擊策略的意義很直接:**本書前面章節強調的「exploit 穩定性工程」([Ch 45](./45-exploit-stabilization.md))在嵌入式目標上重要性相對降低**——如果你的 exploit 有十分之一的機率把 kernel 打 panic,在桌面/伺服器環境這是不可接受的(會被發現、會留下紀錄、服務會中斷被注意到),在一台沒人盯著的家用路由器上,panic 了就等重啟,重啟完再試一次即可,不需要像對待生產環境伺服器一樣要求「幾乎不能失敗」的穩定性。

### 5. 靜態、可預測的記憶體版面,不只是 kernel 沒開 KASLR

嵌入式系統常見把 kernel 和 user space 程式都編譯/連結成**固定載入位址**(這類系統有時完全不跑一般桌面系統那種帶 MMU 分頁與位址隨機化的流程,或者即使有 MMU,userspace 的 ASLR 也常常因為客製 toolchain、靜態連結的習慣而形同沒開)。這代表除了 kernel 本身沒有 KASLR,**user space 程式(例如某個會被攻擊的管理介面常駐程式)的記憶體版面也往往是可預測的**——如果你的攻擊鏈需要先在 user space 拿到一個初始立足點再往 kernel 打,這一步在嵌入式裝置上通常比對應的桌面/伺服器場景容易得多。

## 方法論怎麼重新校準:換一個新架構的檢查清單

碰到一個陌生架構的 kernel pwn 目標,照這個順序重新確認本書前面每個假設,而不是照搬:

1. **架構與 ABI**:syscall 怎麼觸發?number 放哪個暫存器?參數放哪幾個?(本章給的三個架構對照表是範例,換成新架構時找對應的官方 ABI 文件)
2. **端序**:大端還是小端?這決定你怎麼寫位址、怎麼讀 dump 出來的記憶體。
3. **kernel 版本與 config**:`uname -r` 或從韌體裡挖出 `.config`,對照這個版本有沒有本書 Part 2/4 講的各個 mitigation——不要假設「應該有」,去確認。
4. **特權分層**:這個架構有幾層特權等級(x86-64 兩層實際用到、ARM64 四層 EL、RISC-V 三層 M/S/U)?kernel pwn 的終點通常是「拿到 kernel 跑的那一層的完整控制權」,先確認那一層是哪一層。
5. **除錯環境**:能不能跑在 QEMU 裡?有沒有現成的 gdb stub?如果是實機韌體,有沒有 UART/JTAG 可以接出除錯介面?這一步常常是嵌入式 kernel pwn 實際花時間最多的地方,遠超過漏洞分析本身。
6. **攻擊面盤點**:這個 kernel config 實際編進去的子系統有哪些?客製驅動佔比多高?這決定你該把力氣放在翻 Part 5 那些成熟子系統的舊 CVE,還是該去逆向這個裝置獨有的驅動。

## 對比與取捨

| 架構/環境 | 學習/練習成本 | 實戰目標豐富度 | mitigation 密度 | 除錯環境搭建難度 |
|---|---|---|---|---|
| x86-64(本書主線) | 低(QEMU 一行指令) | 高(kernelCTF、CTF 題目多) | 高 | 低 |
| ARM64(Android) | 中(需要裝置或模擬) | 高(真實 0-day 熱點) | 最高(PAC/BTI/MTE) | 中 |
| RISC-V | 低(QEMU 支援良好) | 低(生態尚早期,真實目標少) | 低(機制剛起步) | 低 |
| MIPS 嵌入式 | 高(客製 toolchain、韌體拆解) | 中(真實裝置多,但偏設定/驅動漏洞) | 很低 | 高 |

## 踩雷集錦

1. **把 x86-64/ARM64 的 mitigation 假設直接套在嵌入式目標上**:看到一個 MIPS 路由器就假設「現代 kernel 應該有 KASLR」,結果浪費時間想繞一個根本不存在的防禦。永遠先用 `.config` 或實際測試確認,再決定利用策略。
2. **忽略端序,直接複製一份別的架構的 payload 位址寫法**:大端/小端寫反,症狀是「exploit 跳到莫名其妙的位置」,而不是一個清楚的錯誤訊息,排查起來特別浪費時間。
3. **以為 MIPS 的 cache coherency 問題只在「寫自己的 shellcode 再執行」才會碰到**:任何牽涉到 DMA 裝置、或者你的利用鏈恰好需要讀到剛被另一個 context(另一個 CPU 核心、或周邊裝置)寫入的最新資料,都可能踩到 cache 不同步的坑,不是只有自我修改程式碼這麼窄的場景。
4. **以為 RISC-V 已經有等同 ARM64 的硬體 CFI**:Zicfilp/Zicfiss 這類擴展還在早期落地階段,碰到具體裝置時,不要假設它已經具備對應保護,直接查那顆 CPU 的規格支援清單。
5. **低估嵌入式除錯環境搭建的時間成本**:很多人以為「沒有 mitigation = 好打」,忽略了「連能不能跑起來、能不能接上除錯器」本身就是一大工程,導致低估整個任務的時間規劃。
6. **把 RISC-V 的 Zicfiss shadow stack 和 ARM64 的 PAC 當成同一種機制換了名字**:兩者都保護回傳位址,但 Zicfiss 是「多存一份不可竄改的副本去比對」,PAC 是「用密碼學簽章證明沒被動過」——繞過的思路完全不同(前者要找到能寫 shadow stack 的路徑,後者要找簽章 gadget),不能把本書 [Ch 46](./46-arm64-kernel-pwn.md) 對 PAC 的分析直接套用過來。
7. **把「嵌入式裝置容易當機也沒人管」當成防禦可以放鬆的理由**:這是從攻擊者角度看到的現實,不是給防禦端的建議——如果你是在設計或維護嵌入式系統,panic 後靜默重啟且無人知曉,正是這類裝置長期被攻擊者悄悄利用、卻很晚才被發現的原因之一,不該被當成「反正會自動恢復所以沒關係」。

## 進階:再往深一層

- **自己動手建一套 RISC-V kernel QEMU 環境**:流程和 Part 0 的 x86-64 環境搭建幾乎一致(QEMU + buildroot 或 Yocto 產生根檔案系統 + gdb remote),是驗證本章「RISC-V 生態現況」說法最直接的方式,也是對未來這個架構份額成長的一個提前布局。
- **拆一份真實路由器韌體**:用 `binwalk` 解開一份公開的家用路由器韌體(選擇廠商已經停止支援、版本明確老舊的型號,避免涉及仍在服役且可能影響真實使用者的裝置),確認架構、端序、kernel 版本,對照公開 CVE 資料庫找這個版本已知但從未被這台裝置修補的漏洞——這是體會「嵌入式反而更好打」最直接的練習,但**務必只在你自己擁有的裝置或公開韌體映像上操作,不要碰任何仍在服役、你不擁有或未獲授權的真實裝置**。
- **讀 RISC-V 控制流完整性擴展(Zicfilp/Zicfiss)的規格文件**,對照本書 [Ch 46](./46-arm64-kernel-pwn.md) 的 PAC/BTI 說明,思考這組機制在硬體與工具鏈普及之後,RISC-V 上的 kernel pwn 利用鏈會被推往哪個方向——這是提前理解「下一個十年的 RISC-V kernel pwn 會長什麼樣」的練習。
- **對照本章「失敗代價」這一行,思考你自己做過的桌面/伺服器 kernel pwn 練習,有多少步驟是為了「不能讓它 panic」而特別設計的**(例如 heap grooming 的保守策略、race window 的重試上限控制)。在一台可以隨意重啟的嵌入式測試裝置上,刻意放寬這些保守限制,體會「失敗容忍度」對 exploit 設計策略的實際影響,而不只是把它當一句結論背下來。

## 動手練習

1. 在 QEMU 上分別啟動一顆 x86-64 mainline kernel 和一顆 riscv64 mainline kernel(同一個 kernel 原始碼樹,只是切換 `ARCH=` 編譯),比較兩邊 `/proc/cpuinfo` 或等效資訊的輸出差異,確認你能從輸出裡辨認出架構特有的欄位。
2. 找一份公開的 MIPS 路由器韌體 dump(選擇已停止支援、廠商已公開棄用的型號),用 `file` 確認它是 `mips` 或 `mipsel`,再用 `strings` 找裡面的 kernel 版本字串,對照公開 CVE 清單列出至少三個該版本理論上存在、且本書前面章節教過原理的漏洞類型。
3. （純概念)寫下你自己對「這套 kernel pwn 方法論要換到一個全新架構(假設是你完全沒聽過的某個架構)」的檢查清單,對照本章「方法論怎麼重新校準」小節的六步,確認自己能補齊每一步該查的具體資訊來源。

## 本章重點整理

- RISC-V 的特權架構(M/S/U)和 ARM64/x86-64 的特權分層概念相通,syscall ABI(`ecall` + `a7` + `a0–a5`)遵循「一個暫存器放 number,接下來幾個放參數」這個跨架構的通用規律;硬體級 CFI 機制(Zicfilp/Zicfiss)仍在早期落地階段,不能假設已經具備。
- MIPS 的實務難點不在特權模型,而在大小端差異(直接影響 payload 構造)與 cache coherency 的硬體層坑(指令快取/資料快取不同步、DMA 與 cache 的同步問題)。
- 嵌入式 Linux 普遍更好打的根本原因是結構性的:舊 kernel 版本凍結不更新、客製驅動審查密度低、硬體資源限制讓廠商主動不啟用現代 mitigation、裝置重啟容忍度高讓失敗重試幾乎沒有成本、記憶體版面普遍可預測——不是技術落後,是成本結構與維運習慣共同決定的選擇。
- 碰到任何新架構,先照六步檢查清單重新確認 ABI、端序、kernel 版本、特權分層、除錯環境、攻擊面,再決定利用策略,不要把 x86-64/ARM64 的經驗值直接套用。

## 自我檢核

- [ ] 能不看筆記畫出 x86-64/ARM64/RISC-V 三個架構 syscall ABI 的對照表,解釋「一個暫存器放 number」這個規律為什麼幾乎是所有架構的共通設計
- [ ] 能解釋 MIPS 的 cache coherency 問題具體會在利用鏈的哪一步讓你的 exploit 看起來「莫名其妙失敗」
- [ ] 能說出至少三個「嵌入式 Linux 更好打」的結構性原因,而不只是「嵌入式比較舊比較弱」這種籠統說法
- [ ] 能解釋 RISC-V 的 Zicfiss shadow stack 和 ARM64 的 PAC 在保護回傳位址這件事上,底層機制的具體差異
- [ ] 如果拿到一個完全陌生架構的 kernel pwn 目標,能說出自己會按什麼順序重新確認方法論的每個假設

## 延伸閱讀

### 官方文件 / Spec

- **[RISC-V Privileged Architecture Specification](https://docs.riscv.org/reference/isa/priv/priv-intro.html)**
  - **讀哪裡**：Privilege Levels 與 Trap-Handling 兩節
  - **能學到什麼**：M/S/U 三層特權等級的精確定義與 `ecall` 的例外代碼(cause code 8/9/11 分別對應 U/S/M-mode 呼叫),補足本章的簡化說明
  - **前提知識**：本章「RISC-V:特權架構與 syscall ABI」一節

- **[RISC-V Shadow Stacks and Landing Pads(Zicfilp/Zicfiss 規格,v1.0)](https://docs.riscv.org/reference/isa/extensions/zicfiss-zicfilp/_attachments/riscv-cfi.pdf)**
  - **讀哪裡**：`LPAD` 指令的定義段落(forward-edge)與 `sspush`/`sspopchk` 的定義段落(backward-edge)
  - **能學到什麼**：這兩個擴展的精確指令編碼與例外行為,補足本章「mitigation 現況」小節只講到機制層級、沒有展開指令細節的部分
  - **前提知識**：本章「RISC-V:mitigation 現況」一節,以及 [Ch 46](./46-arm64-kernel-pwn.md) 的 BTI/PAC 機制(方便對照兩套設計哲學的差異)

- **[Linux Kernel Documentation — RISC-V](https://docs.kernel.org/arch/riscv/index.html)**
  - **讀哪裡**：boot-image-header 與 uabi 相關章節
  - **能學到什麼**：mainline kernel 對 RISC-V 的實際支援現況,用來核實本章「生態現況」小節隨時間可能變化的部分
  - **前提知識**：無

### 部落格 / 技術文章

- **[pawnyable — Linux Kernel](https://pawnyable.cafe/linux-kernel/)**（本書 README 已列)
  - **這篇說什麼**：雖然主體是 x86-64,部分章節的方法論(環境搭建、靶題設計思路)可以直接套用到換架構練習
  - **讀哪裡**：環境搭建相關章節,對照你自己搭 RISC-V QEMU 環境時卡住的地方
  - **為什麼值得讀**：本書 Part 0 多次參考的來源,方法論和本章「方法論怎麼重新校準」互相呼應

- **MIPS 路由器韌體安全研究類文章(建議用「MIPS router firmware exploitation」「embedded MIPS kernel exploit」等關鍵字搜尋近期會議(如 DEF CON、Black Hat)公開的議程與投影片)**
  - **這篇說什麼**：這類研究通常聚焦特定廠牌/型號的真實案例,能具體看到本章講的「cache coherency 坑」「舊 kernel 版本」如何在真實利用鏈裡體現
  - **讀哪裡**：議程摘要先篩選,挑和你手上目標架構/廠牌相近的深入讀
  - **為什麼值得讀**：嵌入式這塊的高品質公開資源相對分散,沒有單一權威來源,搜尋近期會議材料比找固定部落格更有效率

### 書籍

- **《The RISC-V Reader: An Open Architecture Atlas》** — David Patterson & Andrew Waterman(Strawberry Canyon, 2017)
  - **這本書的定位**：RISC-V ISA 本身的權威入門讀物,補足本章沒有展開的指令集細節
  - **讀哪幾章**：特權架構相關章節和本章「RISC-V」一節最直接對應;純使用者態指令集部分可以選讀

把 x86-64、ARM64、RISC-V、MIPS 的差異都放進同一張方法論地圖之後,本書 Part 8 的視角拉到了最寬——下一步,我們把這些章節累積的攻擊者視角,拿去對真實世界的比賽賽制、完整的 N-day walkthrough,以及防禦者的立場走一遍。

→ [Ch 49 kernelCTF 賽制與流程](./49-kernelctf-overview.md)
