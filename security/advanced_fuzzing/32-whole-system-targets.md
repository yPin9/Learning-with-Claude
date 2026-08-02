# Ch 32 — 全系統 target

> **目標：** 理解「全系統 fuzzing」的意思——fuzz 的不是一個函數或一個進程，而是整個系統的某個攻擊面——並學會如何為這類 target 設計 harness、選擇 input 注入點，以及從現有 CVE / 研究案例裡提煉出可複用的方法論。
>
> **環境：** 本章以架構分析和案例研究為主。部分 QEMU device fuzzing 的概念可以在 WSL2 上理解但不完整實測（需要 KVM + Intel PT）。標注「[未實測]」的部分說明有適當環境時的驗證方法。

---

## 什麼是「全系統 target」

普通 fuzzing 的 target：

```
fuzzer → harness → 某個函數 / library
         (在同進程內或子進程)
```

全系統 fuzzing 的 target：

```
fuzzer → input 注入機制 → 整個 VM（含 kernel + 裝置 + userland）
```

「全系統」的意思是：**你 fuzz 的攻擊面橫跨多個軟體層，你不事先限制「bug 在哪一層」**。一個 input 可能觸發 kernel bug、hypervisor bug、device emulation bug、bootloader bug，或者這幾層的組合。

這類 target 的共同特徵：

1. **沒有原始碼**，或有原始碼但不能插樁（kernel 在 guest 裡，你的 fuzzer 在 host 裡）
2. **攻擊面在硬體/韌體介面**：MMIO、DMA、PCI config、virtio queue、中斷
3. **必須用 VM 隔離**：bug 觸發可能讓整個系統掛掉，不能在 host 直接跑

---

## 典型的全系統 target 類型

### 1. QEMU Device Model（Hypervisor 攻擊面）

QEMU 在 host 上模擬各種硬體裝置（e1000 網卡、Floppy、USB controller、IDE controller 等）。Guest VM 透過 MMIO 或 I/O port 存取這些裝置。

攻擊面：**Guest 能控制的所有 MMIO 讀寫和 I/O port 操作**。

為什麼有 bug？Device model 程式碼很舊，當年沒有 fuzzing，手工 code review 很難涵蓋所有 guest-controlled 的輸入路徑。VENOM（CVE-2015-3456，Floppy controller UAF）、QEMU e1000 overflow（CVE-2016-1981）都是這類 bug。

這個攻擊面和 vm_escape 課（vm_escape/Ch X）直接銜接——那裡的 device emulation bug 就是這裡 fuzzing 找到的。

### 2. Kernel 本身的非 syscall 介面

syzkaller（Ch 24–26）打的是 syscall 介面。但 kernel 還有其他攻擊面：

- **/proc、/sys、/dev** 的特殊 ioctl 序列
- **網路封包的 kernel-side 處理**（netfilter、nftables、BPF verifier）
- **USB gadget driver**（插 USB → 觸發 kernel 解析 USB descriptor）
- **Bluetooth HCI packet**
- **NFS/CIFS/overlayfs 的特殊路徑**

這些介面的 fuzzing 通常要在 VM 裡做，因為 bug 觸發直接讓 kernel panic。

### 3. Bootloader 和 UEFI

UEFI 韌體在 OS 啟動之前執行，有大量解析邏輯（PE loader、file system driver、網路 stack）。攻擊面是：

- **UEFI 變數（NVRAM）**：OS 可以寫入，韌體重開機後讀取並解析
- **UEFI 映像（.efi 檔案）**：PE 格式解析
- **Secure Boot 的憑證鏈**

Snapshot fuzzing 特別適合 bootloader：讓 UEFI 跑到「初始化完畢，開始解析可控 input」的點，存快照，從那裡 fuzz。

### 4. Closed Binary（閉源 binary）

攻擊面：你只有 binary，沒有原始碼。典型是：

- 商用 VPN client、防毒軟體的核心模組
- Proprietary driver（.ko 或 .sys）
- 已出貨但無法重新編譯的 firmware

Snapshot + Intel PT 是這類目標的唯一 greybox 方案。

---

## Harness 設計：選哪個 input 注入點

Harness 是「fuzzer 產生的 bytes → target 能接受的操作」的翻譯層。全系統 fuzzing 的 harness 設計比單函數 fuzzing 複雜，因為你有多個注入點可選：

### 注入點的選擇原則

```
高攻擊面覆蓋 vs 低噪聲
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│ 注入點        覆蓋面   確定性  Setup 難度  適合 target    │
├──────────────────────────────────────────────────────────┤
│ MMIO read/write  高     高      低         device model  │
│ I/O port         中     高      低         legacy device │
│ DMA buffer       高     高      中         network/disk  │
│ syscall 序列     中     中      中         kernel        │
│ 網路封包         高     低*     中         協定 stack    │
│ USB descriptor   高     高      高         USB stack     │
│ NVRAM variable   中     高      低         UEFI          │
└──────────────────────────────────────────────────────────┘
* 網路封包涉及時序，確定性較低
```

**低確定性是最大的敵人**：如果同樣的 input 有時 crash 有時不 crash，coverage 追蹤就沒意義。選注入點時優先選「給定 input，target 行為確定」的介面。

### Device Model 的 harness 設計（QEMU 案例）

**[以下為概念性描述，基於 Nyx 論文和 QEMU Fuzz（OSS-Fuzz）的公開設計，未實測。]**

打 QEMU e1000 device model 的 harness：

```
輸入格式設計：
  4 bytes：操作類型（MMIO_WRITE / MMIO_READ / IO_PORT / DMA_WRITE）
  4 bytes：目標地址（MMIO address 或 I/O port）
  4 bytes：資料大小
  N bytes：資料

Harness 動作（guest 端 agent）：
  解析輸入的每個操作，依序執行對應的 guest 記憶體讀寫
  例：MMIO_WRITE 0x10000000 4 [AA BB CC DD]
     → movl $0xDDCCBBAA, (0x10000000)
     （對 e1000 的 MMIO base 做寫入）
```

這個 harness 讓 fuzzer 能系統性地探索 e1000 register space 的所有寫入組合。

---

## 案例：Nyx 打 QEMU e1000

**[以下為對 Nyx 論文 Section 6 的描述，非實測輸出。]**

QEMU e1000 device model 是 Nyx 論文的主要 benchmark target。

**Target 特性**：
- 代碼在 host 上跑（QEMU 進程）
- Guest 透過 MMIO 和 DMA 控制 e1000 register
- e1000 是 legacy driver，程式碼有超過 20 年歷史，已知有多個 CVE

**Nyx 的 setup（概念）**：

```
Guest agent：
  - 映射 e1000 的 MMIO region（讀 /proc/iomem 找到地址）
  - 接收 fuzzer 輸入，解析成 MMIO 操作序列
  - 執行操作，回報 crash/no-crash

Snapshot 點：
  - e1000 driver 初始化完成後（interrupt handler 已設好）
  - Snapshot 涵蓋 guest 記憶體 + e1000 device state

Fuzzer：
  - 變異 MMIO 操作序列
  - 看哪些序列觸發 host 端的 QEMU segfault 或 assertion
  - Coverage 由 Intel PT 從 host QEMU 進程讀取
```

**結果**：Nyx 在 e1000 上找到了多個已知 CVE 的重現，並在其他 device model（Floppy、virtio-net）上找到新 bug。exec/s 比 syzkaller 風格快 10–50 倍。

---

## 案例：全系統 Bootloader Fuzzing

Bootloader 是典型的「不能插樁、需要在真實 boot 流程裡 fuzz」的 target。

**UEFI 的特殊性**：

```
UEFI 執行環境：
  - 沒有 OS（直接跑在 bare metal 或 UEFI 模擬器）
  - 讀取 NVRAM 變數（EFI_VARIABLE）
  - 執行 UEFI 服務（DXE driver、file system driver）
  - 解析 PE/COFF image
```

**Snapshot 方法**：

1. 讓 UEFI 在 QEMU/OVMF 環境下 boot
2. 在「開始解析 EFI variable」之前設 snapshot
3. 從快照 reset，注入不同的 EFI variable 內容
4. 看 UEFI 是否在解析時 crash

**現有工具**：
- **UEFI Fuzz（Microsoft）**：針對 UEFI 的 AFL-based fuzzer，需要 UEFI simulation 環境
- **Mousse**：基於 unicorn 的 UEFI fuzzer，rehost UEFI binary

---

## 全系統 fuzzing 的 crash oracle 設計

普通 userland fuzzing 的 oracle 很直接：`SIGSEGV` 或 `SIGABRT` 就是 crash。

全系統 fuzzing 的 oracle 需要更多考量：

```
Oracle 類型：
  ├── Guest kernel panic         → hypervisor 觀察到 guest 掛起
  ├── QEMU（host process）crash → host 端 SIGSEGV
  ├── Guest 無 response（timeout）→ 可能是 deadlock 或 hang bug
  ├── Guest 輸出異常             → 功能性 oracle（差分測試）
  └── Host KASAN/UBSAN 觸發     → sanitizer oracle
```

**Sanitizer 的角色**：在 QEMU 上開 ASAN/UBSAN 重新編譯，讓記憶體錯誤在 exploit 之前就被 oracle 抓到。代價是 QEMU 本身變慢。

```bash
# 用 ASAN 編譯 QEMU（概念步驟）
# [未實測完整流程]
./configure --enable-sanitizers
make -j$(nproc)
# 跑有 ASAN 的 QEMU：任何記憶體錯誤都會輸出錯誤報告並 exit(1)
```

---

## Harness 的 input 格式：二進位 vs 結構化

**二進位 input（raw bytes）**：

```
pros：fuzzer 不受格式限制，能嘗試任意位元組序列
cons：大部分 input 會在 harness 的「格式解析」層就被丟棄，不進 target
```

**結構化 input（Nyx Affine Types / libprotobuf-mutator）**：

```
pros：每個 input 都能到達 target 的實際處理邏輯
cons：格式定義的品質決定了覆蓋面，過窄的格式定義會漏掉 bug
```

全系統 fuzzing 通常選結構化 input，原因是 MMIO 操作序列有天然結構（操作類型 + 地址 + 資料），用 raw bytes fuzzing 浪費大量 exec 在無效操作上。

---

## 全系統 fuzzing 的工程挑戰

### 挑戰一：確定性

網路 buffer race、timer 精度、中斷排程順序——這些都會讓同一個 input 在兩次執行裡走不同路徑。

**解法**：在 snapshot 前停用所有時序相關元件（guest clock 固定、模擬裝置的 timer 固定），確保 snapshot 後的執行是確定性的。

### 挑戰二：Corpus 從哪裡來

全系統 fuzzing 的起始 corpus 通常是：
- 從真實設備的 trace 擷取（packet capture → 轉換成 MMIO 序列）
- 從 spec（PCI 規範、VirtIO 規範）手工構造合法序列
- 從已知 PoC 改造

好的 seed corpus 能讓 fuzzer 快速到達有趣的 code path，而不是卡在「驅動初始化流程」。

### 挑戰三：Crash triage 更難

全系統 crash 通常只有 QEMU crash log 或 guest kernel oops。沒有 AddressSanitizer 的精確報告，你需要手動做 root cause analysis——先找到觸發 crash 的最小 input（crash minimization），再用 debugger 跟 crash path。

---

## 踩雷

**錯誤直覺一：「全系統 fuzzing 一定比單函數 fuzzing 慢，不值得」**

正確理解：全系統 fuzzing 的 exec/s 確實比單函數 fuzzing 低（VM overhead 在），但它能覆蓋單函數 harness 不能到達的攻擊面。對「根本不知道 bug 在哪一層」的目標（比如一個 closed firmware），全系統 fuzzing 是唯一方法，不是效能選擇，是能打到 vs 打不到的問題。

**錯誤直覺二：「Harness 越靠近底層越好（直接 fuzz MMIO），因為能觸發更多路徑」**

正確理解：過於底層的 harness 會讓大部分 input 在很早的地方就被 reject（比如「MMIO address 不在合法範圍」），coverage 增長緩慢。理想的 harness 讓 fuzzer 能快速到達「target 真正在處理業務邏輯」的地方，而不是卡在格式解析。通常這意味著 harness 要做一些「保證合法性」的前置處理（比如填正確的 DMA descriptor header）。

**錯誤直覺三：「全系統 fuzzing 只能找 memory corruption bug，找不到邏輯 bug」**

正確理解：全系統 fuzzing 的 oracle 不必侷限在記憶體錯誤。你可以加一個「差分 oracle」——用兩個不同的 QEMU 版本執行同一個輸入，比較輸出；或者定義「合法操作的預期結果」作為 oracle，任何偏差都是 bug。這讓全系統 fuzzing 也能找邏輯錯誤（CVE-2021-3527，USB MBIM bug，是功能性 oracle 找到的）。

---

## 進階延伸

**QEMU-based fuzzing 的 OSS-Fuzz 整合**：QEMU 本身已在 OSS-Fuzz 上持續 fuzzing，用的是 QEMU 的 in-process fuzzing harness（不是全 VM 快照）。看 QEMU 的 `tests/qtest/fuzz/` 目錄可以找到現有的 device fuzzer 實作，是學習 device harness 設計的好範例。

**Virtio fuzzing**：Virtio 是 QEMU 現代 device 的標準介面，比 legacy device（e1000、Floppy）更結構化，但攻擊面一樣大。Virtio 的 ring buffer 格式適合用 libprotobuf-mutator 做結構化 fuzzing。

**Hypervisor 之上再套 hypervisor（Nested VM fuzzing）**：如果你想 fuzz 一個 hypervisor，你需要在另一個 hypervisor 裡跑它。Nyx 就是這樣用的——QEMU/KVM 在 host 跑，Nyx fuzzer 控制 KVM，guest 裡再跑一個 QEMU 作為 target。這需要支援 nested virtualization 的環境。

---

## 動手練習

1. 查閱 QEMU 的 `tests/qtest/fuzz/` 目錄（https://github.com/qemu/qemu/tree/master/tests/qtest/fuzz），找到 `fuzz-e1000e.c` 或類似的 device fuzzer，閱讀它的 harness 設計：它選了哪個 input 注入點？格式是什麼？

2. 閱讀 VENOM CVE（CVE-2015-3456）的 PoC，理解 Floppy controller 的 MMIO 界面是什麼，哪個 register 的什麼操作觸發了 UAF。然後思考：如果要為這個 bug 設計 fuzzing harness，輸入格式會長什麼樣？

3. 閱讀 Nyx 論文 Section 6（Evaluation），找到他們在 e1000 / Floppy 上找到的 bug 清單，並確認這些 bug 的觸發路徑是什麼。

4. 如果有 KVM 環境（非 WSL2）：啟動一個 QEMU VM，用 QEMU monitor 的 `savevm` / `loadvm` 做一次手動快照和還原，感受 VM-level snapshot 的時間成本。

---

## 本章重點

- 全系統 fuzzing 的目標橫跨多個軟體層（kernel + device + hypervisor），不預設 bug 在哪一層
- 典型目標：QEMU device model、bootloader/UEFI、closed binary、USB/Bluetooth kernel stack
- Harness 設計的核心問題：選哪個 input 注入點，讓 fuzzer 能快速到達有趣的業務邏輯
- Crash oracle 需要超越「SIGSEGV」——hypervisor 觀察 guest panic、差分 oracle、sanitizer 都要考慮
- 確定性是最大工程挑戰：時序、中斷、timer 都要在 snapshot 前鎖定

---

## 自我檢核

- [ ] 能說出三種全系統 fuzzing 的典型 target 類型及其攻擊面？
- [ ] 為 QEMU device model 設計 harness 時，最重要的 input 注入點是什麼？
- [ ] 全系統 fuzzing 的 crash oracle 比單函數 fuzzing 複雜在哪裡？
- [ ] 確定性問題在全系統 fuzzing 裡從哪裡來？怎麼緩解？
- [ ] 為什麼 seed corpus 的品質對全系統 fuzzing 特別重要？

---

## 延伸閱讀

1. **Nyx: Greybox Hypervisor Fuzzing using Fast Snapshots and Affine Types**（Schumilo et al., USENIX Security 2021）
   - 讀 Section 6（Evaluation）——具體的 QEMU device target（e1000, Floppy, virtio-net）fuzzing 結果，每個 target 的 harness 設計選擇，以及找到的 bug 類型；是全系統 fuzzing 方法論的具體示範
   - https://www.usenix.org/conference/usenixsecurity21/presentation/schumilo

2. **VENOM: Virtual Machine Escape with QEMU**（CrowdStrike 技術報告，2015）
   - 讀完整報告——QEMU Floppy controller 的 UAF，是「全系統 fuzzing 能找到的 bug」的典型範例，也是理解 device model 攻擊面的最好入口；和 vm_escape 課的 CVE 案例直接銜接
   - https://www.crowdstrike.com/blog/venom-vulnerability-details/

3. **QEMU Fuzzing（OSS-Fuzz QEMU target 說明）**
   - 讀 `tests/qtest/fuzz/README.md` 和幾個 device fuzzer 的原始碼——QEMU 官方的 in-process device fuzzer，是「如何在不用 VM snapshot 的情況下 fuzz device model」的對照方案，理解它的限制也是理解 Nyx 優勢的方法
   - https://github.com/qemu/qemu/tree/master/tests/qtest/fuzz

---

→ [練習 E：snapshot fuzz 一個不可重置目標](./practice-e-snapshot-fuzz.md)
