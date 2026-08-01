# Ch 32 — VMware 架構：vmx process 與閉源逆向法

> **目標**：建立對 VMware Workstation 組成元件的正確心智模型，理解 vmware-vmx 為何是主要攻擊目標，並掌握在沒有原始碼情況下定位 guest-host 介面的逆向研究方法。

---

## 為什麼需要這個？

前五個 Part 打的是開源 hypervisor：QEMU 原始碼公開，VirtualBox 原始碼公開，device 的 parser 邏輯直接看 `.c` 就能讀懂。Part 6 開始我們換一個完全不同的戰場。

VMware Workstation 是閉源商業軟體。沒有 `hw/` 目錄可以翻，沒有 git blame 可以追，當你想知道「RPCI 封包的 parser 怎麼寫」，答案只存在於一個幾十 MB 的 ELF/PE binary 裡。這不是理論上的差異，而是研究工作流程的根本差異：**你花在「看懂程式在做什麼」的時間，比花在「分析漏洞」的時間還長**。

但閉源不代表無法研究。VMware Workstation 在 Pwn2Own 歷年 Virtualization 分類裡是常客——Keen Lab、Computest、Atlas Research、STAR Labs 等都打過它並留下了公開 writeup。這些研究者用的方法是可以學習的，而且在你熟悉這套方法之後，它比看開源源碼更有意思：你是在根據行為線索推斷一個黑盒的內部設計。

本章做兩件事：（1）建立 VMware Workstation 的元件模型，讓你知道哪個元件跑在哪裡、做什麼、在攻擊者眼中的地位是什麼；（2）講清楚閉源 hypervisor 的逆向研究工作流——從二進位取得攻擊面入口，到動態調試驗證假設。Ch 33 才深入具體的 backdoor/RPCI 通道；本章是讓後面三章有意義的那一步。

---

## 先建立直覺

開源 QEMU 的模型你已經熟了：

```
guest vCPU 觸發 VMEXIT
  → KVM 攔截（ring-0）
  → QEMU userspace 行程接手
  → device 的 .read/.write callback
```

VMware Workstation 的模型在結構上類似，但分工的邊界不一樣：

```
guest vCPU 觸發 VMEXIT
  → vmmon（ring-0 kernel driver）攔截
  → 路由決策：可以在 vmmon 內快速處理的 exit，vmmon 自己消化
  → 無法快速消化的（I/O emulation、device 模擬）→ vmware-vmx（host userspace）
  → vmware-vmx 裡的 device 模擬層
```

這裡有一個關鍵點：**vmware-vmx 是一個普通的 host userspace 行程**（Linux 下是 ELF 行程，Windows 下是 PE 行程）。每台開起來的 VM 對應一個 vmware-vmx instance。它在 host 上有自己的 PID，有 heap，有記憶體映射，受 host OS 的正常保護機制管轄（ASLR、NX）。

換句話說，如果你能從 guest 觸發 vmware-vmx 裡的 heap overflow 或 UAF，後續的利用邏輯跟打 QEMU 沒有本質差別：infoleak → 洩漏 PIE base / heap 位址 → 劫持 function pointer → ROP 落到 `system()`。**攻擊目標是一個 host userspace 行程**，這是 VM escape 利用階段的不變框架。

---

## 底層機制：VMware Workstation 的組成元件

```
┌─────────────────────────────────────────────────────────────────┐
│                         Host OS（Linux / Windows）               │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              vmware-vmx（host userspace 行程）             │   │
│  │                                                            │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │   │
│  │  │ VMM（軟體層）  │  │ device 模擬  │  │ RPCI/backdoor │  │   │
│  │  │ 部分 VT-x 設  │  │ SVGA II      │  │ handler       │  │   │
│  │  │ 定與 exit 路  │  │ VMCI         │  │               │  │   │
│  │  │ 由            │  │ shared folder│  │               │  │   │
│  │  │               │  │ mks（input） │  │               │  │   │
│  │  └──────────────┘  └──────────────┘  └───────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                    │
│                    ioctl / 共享記憶體                             │
│                              │                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              vmmon.ko / vmmon.sys（ring-0 driver）         │   │
│  │  負責：VMCS 設定、VMEXIT 攔截、快速 exit 在 kernel 處理、  │   │
│  │  把需要 device 模擬的 exit 發信號給 vmware-vmx             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │    vmnet.ko / vmnet.sys（host 端網路驅動）                  │   │
│  │    vmblock.ko（Linux 上 shared folder 的 FUSE 替代方案）   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │    vmware-tools（guest 端，執行在 VM 內）                   │   │
│  │    負責：balloon driver、時鐘同步、guest-host 通道啟動       │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘

攻擊路徑：guest 惡意程式
  → 透過 backdoor / RPCI / SVGA / VMCI 等介面送入畸形資料
  → vmware-vmx 裡的 parser/handler 處理時觸發 bug
  → 在 host userspace 執行任意程式碼
```

### vmware-vmx

每台開起來的 VM，host OS 上就有一個對應的 `vmware-vmx` 行程。這是**攻擊者的主要目標**，理由：

1. 它在 host **userspace** 執行——漏洞利用不需要 ring-0 能力，打 heap/stack 的手法直接可用。
2. 它包含所有 device 模擬邏輯——SVGA II GPU、VMCI、HGFS（shared folder）、backdoor/RPCI handler、mks（mouse-keyboard-screen 輸入）、drag&drop、clipboard 等都在這裡實作。
3. guest 送到這些 device 的資料，最終都由 vmware-vmx 的 parser 函式處理——parser 有 bug，guest 就能影響 host 記憶體。

vmware-vmx 的確切內部結構是逆向推測，沒有公開原始碼。但從多篇公開 writeup（Keen Lab、Computest 等）可以得知它使用類似 dispatch table 的架構將不同的 guest-host 通道分發到對應的 handler。

### vmmon

vmmon（Linux 的 `vmmon.ko`，Windows 的 `vmmon.sys`）是 ring-0 kernel module，公開文件確認其職責：

- 設定 VMCS（Virtual Machine Control Structure），啟動/停止 vCPU
- 攔截 VMEXIT
- 在 ring-0 快速處理部分 exit（例如某些 MSR 存取）
- 對於需要 device 模擬的 exit，通知 vmware-vmx（透過 ioctl 或共享記憶體，具體機制是**逆向推測**）

vmmon 本身不是 device bug 的主要戰場——它的攻擊面相對窄（主要是 ioctl 介面，guest 無法直接觸發），但它是一個擁有 ring-0 權限的驅動，如果 vmmon 有 bug，提權的影響更嚴重。

### vmware-tools

vmware-tools 跑在 **guest 內**，是攻擊者已有的一側（你已經在 guest 裡了）。它負責：

- 啟動 backdoor / RPCI 通道（透過 I/O port 0x5658）
- balloon driver（記憶體熱插拔）
- 時鐘同步
- 傳遞 drag&drop、clipboard 資料

從攻擊者角度看，vmware-tools 本身不是目標，它的通道是觸發 vmware-vmx bug 的**媒介**。你在 guest 裡直接用 `in/out` 指令存取 backdoor port，不需要 vmware-tools 存在。

---

## 攻擊面全圖：哪些介面是入口

VMware Workstation 對外暴露的 guest-host 介面，是公開研究文獻中確認的攻擊面：

| 介面 | 底層機制 | 存取方式（guest 端） | 備注 |
|------|---------|-------------------|------|
| **backdoor** | I/O port 0x5658 / 0x5659（Enhanced Backdoor） | `in/out` 指令 | 最基礎通道，幾乎所有 RPCI 都走這裡 |
| **RPCI / guestRPC** | 建在 backdoor 之上的 RPC 協定 | 透過 backdoor port 發 RPCI 命令 | 字串形式的命令，高層功能入口，攻擊面最廣 |
| **VMCI（VMware VMCI）** | 類 socket 的 hypervisor-to-guest 通道，有 PCI device | guest 的 VMCI PCI device 驅動 | 支援 datagram 與 stream，有較複雜的 multiplexing |
| **SVGA II** | PCI device（FIFO 佇列 + MMIO + VRAM）| 寫 SVGA 命令到 FIFO | GPU 指令 parser 是 Pwn2Own 的常客 |
| **HGFS（shared folder）** | RPCI 的 HGFS 子協定，或 vmblock kernel module | RPCI 呼叫或 filesystem mount | 路徑處理 parser，過去有 traversal 類 bug |
| **drag&drop / clipboard** | 建在 RPCI 或 VMCI 之上 | 透過 vmware-tools 觸發 | 牽涉資料格式 parser，公開 Pwn2Own 案例有這個入口 |
| **mks（mouse-keyboard-screen）**| 輸入模擬通道 | 透過 RPCI 或特定 device | 較少出現在公開 writeup，但面積存在 |

**backdoor（I/O port 0x5658）是所有這些入口的基礎**。RPCI、部分 VMCI 操作、drag&drop 都建在它之上。這就是為什麼 Ch 33 從 backdoor/RPCI 開始。

---

## 閉源逆向法：在沒有原始碼時定位攻擊面

這一節是本章最核心的技能轉移。QEMU 你去看 `hw/` 底下的 `.c`；VMware 你要學以下這套工作流。

### 第一步：取得 binary

Linux 上 vmware-vmx 通常在 `/usr/lib/vmware/bin/vmware-vmx`（路徑隨版本不同，**據公開研究，未實測**）。Windows 上是 `vmware-vmx.exe`（在 VMware 安裝目錄下）。

取得 binary 後：

```bash
# 確認格式與架構
file vmware-vmx
# → ELF 64-bit LSB pie executable, x86-64 ...

# 大致估量符號情況
nm vmware-vmx 2>/dev/null | wc -l
# release build 通常是 strip 過的，大部分 function 無名
# 但 symbol 資訊可能部分存在（imported symbols、C++ RTTI）

# 找 imported 函式（動態連結的部分有名字）
objdump -d --plt vmware-vmx | grep '<' | head -40
```

### 第二步：字串搜尋定位攻擊面入口（公開研究標準做法）

在 strip 過的 binary 裡，**字串是最可靠的線索**。device parser 通常有 error message、命令名稱、協定識別碼。

```bash
# 找 RPCI 相關字串（據公開研究，vmware-vmx 內有 RPCI 相關 debug string）
strings vmware-vmx | grep -i rpci

# 找 backdoor 相關
strings vmware-vmx | grep -i backdoor
strings vmware-vmx | grep -i "0x5658"

# 找 SVGA 相關（GPU parser 的 command 名稱）
strings vmware-vmx | grep -i svga
strings vmware-vmx | grep -i fifo

# 找 HGFS / shared folder
strings vmware-vmx | grep -i hgfs
strings vmware-vmx | grep -i "shared folder"

# 找 VMCI
strings vmware-vmx | grep -i vmci

# 找可讀的版本資訊 / 模組名
strings vmware-vmx | grep -i "vmware" | grep -v "^VMware" | head -30
```

這不是在找漏洞——是在確認哪些功能子系統存在，以及確認這個 binary 裡包含你感興趣的攻擊面。

### 第三步：IDA Pro / Ghidra 逆向 vmware-vmx

找到字串之後，進 IDA 或 Ghidra：

1. **在字串視窗（Strings window）找到目標字串**，例如 `"RPCI"` 相關的 error log
2. **交叉引用（Xref）**：看這個字串被哪些函式引用 → 這些函式是 RPCI handler 附近的函式
3. **從那裡往上追 call chain**：找 parser loop、找 dispatch switch/table
4. **C++ virtual function table（vtable）**：如果 vmware-vmx 用 C++（從 RTTI 可以確認，**逆向推測，未實測**），在 Ghidra 裡用 RTTI 自動分析可以部分重建 class 結構

```
Ghidra 工作流範例（逆向推測，未實測）：

Window → Strings → 搜 "rpci"
  → 找到 "RPCI: unknown command %s" 之類的字串
  → 右鍵 → References → Show References to Address
  → 跳到引用這個字串的函式（可能是 error path）
  → 向上看這個函式的 caller
  → 找到 command dispatch switch
  → 每個 case 對應一個命令 handler
```

這是研究界的標準流程。Keen Lab 的 VMware 研究（他們曾在 Pwn2Own 打過 VMware）、Computest 的公開演講都提到類似的起手式。

### 第四步：patch diff 分析（VMSA 出來後）

VMware 發 VMSA（VMware Security Advisory）時，通常只說「漏洞類型」和「影響元件」，不給 diff。但你可以：

1. **下載前後兩個版本的 vmware-vmx**
2. **bindiff**（IDA 的 BinDiff 外掛，或開源的 `diffware`）比較兩個版本的函式差異
3. 重點看**函式邏輯發生變化**的地方（新增了 bounds check、新增了長度驗證）
4. 從 patch 的防禦動作反推洞在哪裡

這個技術叫做 **1-day 分析（patch diffing）**，是 VM escape 研究者的核心技能之一。VMSA 出來之後幾天到一週內，熟練的研究者通常能從 diff 重建出漏洞 PoC。

### 第五步：動態調試（host 端 gdb/x64dbg attach vmware-vmx）

逆向完畢，建立假設之後，用動態調試驗證：

**Linux 端**（使用 gdb）：

```bash
# 找到目標 VM 的 vmware-vmx PID
pgrep -a vmware-vmx

# attach（需要 ptrace 權限，在 root 或調整 ptrace_scope 後）
gdb -p <PID>

# 在你認為的 handler 位址下斷點（位址是從 IDA 分析出的偏移 + 執行時 base）
# 取得 ASLR base：
(gdb) info proc mappings
# 找 vmware-vmx 的載入 base，加上 IDA 裡的 offset

(gdb) b *0x<base+offset>
(gdb) c

# 然後從 guest 端觸發對應的 I/O（下一章細節）
# 看 gdb 是否斷在你猜測的函式
```

**Windows 端**：用 x64dbg attach `vmware-vmx.exe`，流程類似，但 ASLR base 從 x64dbg 的 Modules 視窗讀。

這個動態調試循環是驗證靜態逆向假設的唯一方法。靜態分析告訴你「這裡可能是 parser」，動態調試告訴你「guest 送這個輸入時，確實落在這個函式、暫存器是這個值」。

---

## 對比與取捨

| 維度 | QEMU（開源）| VMware Workstation（閉源）|
|------|------------|--------------------------|
| 攻擊面理解方式 | 直接讀 `hw/` 下的 `.c` 原始碼 | 字串搜尋 → IDA/Ghidra 逆向 → 動態驗證 |
| parser 邊界條件 | 直接看 if 判斷式與 bounds check | 逆向反編譯器輸出，可能有誤讀，需動態確認 |
| patch 分析 | 看 git commit diff，一目瞭然 | bindiff 兩個版本的 binary，需人工比對 |
| 復現環境 | 自編 debug QEMU，完全可控 | 只能用 release binary，符號不全，調試難度高 |
| 功能文件 | QEMU dev doc、QOM、memory API 文件 | 只有 VMware 公開的少數協定文件（SVGA、VMCI SDK）|
| CVE 公開資訊 | patch commit 附 CWE 分類，細節多 | VMSA 只說影響元件，細節少，靠 writeup |
| 研究回報 | 研究社群大，writeup 多 | Pwn2Own 級研究，writeup 少但品質高 |
| 攻擊者優勢 | N/A | 研究者少競爭低，洞的存活時間可能更長 |

**沒有原始碼，意味著你必須把「理解程式在做什麼」和「找洞」兩件事同時進行**。在 QEMU 上你可以先讀清楚再找洞；在 VMware 上兩件事是交織的——你在逆向的過程中同時在觀察「這個地方的 bounds check 夠嗎」。

---

## 踩雷集錦

**1. 「strip binary 就沒有資訊可用了」**

錯誤直覺。strip 只去掉 symbol table，不去掉字串常數、C++ RTTI、imported function 名稱、PLT stub 名稱。vmware-vmx 裡的字串（包括 log message、error string、協定命令名）通常是 parser 入口最直接的導航工具。先跑 `strings`，再進 IDA。

**2. 「vmmon 才是主要攻擊目標，因為它有 ring-0 權限」**

錯誤直覺。vmmon 的攻擊面窄得多（主要是 ioctl，且 guest 無法直接觸發），而且 ring-0 漏洞利用比 userspace 難，不是說高權限就更好打。vmware-vmx 才是主要攻擊目標——它有廣大的 device 模擬攻擊面，而且利用手法是熟悉的 userspace heap pwn。

**3. 「IDA 的反編譯就是真實 C 程式碼」**

逆向推測≠事實。反編譯器輸出是近似，特別是最佳化過的 release binary，可能有型別推斷錯誤、函式邊界判斷錯誤、pointer aliasing 混淆。靜態分析建立假設，動態調試驗證——這個順序不能省。

**4. 「我在 guest 裡以 root 跑，可以直接 attach vmware-vmx」**

不行。vmware-vmx 跑在 **host** 上，你在 guest 裡的 root 是 guest 的 root，沒有 host 的存取權。這正是 VM escape 的意義——你要先從 guest 打穿 vmware-vmx，才能在 host 上有任何能力。

**5. 「VMware 有公開文件就代表那個介面是安全的」**

不對。SVGA II（有 VMware 官方 SDK）、VMCI（有官方文件）都出現過 Pwn2Own 級漏洞。公開文件只說「介面長什麼樣」，不保證「parser 的每個邊界都正確」。事實上有文件的介面可能更危險——你更容易理解協定格式，也更容易構造邊界條件的輸入。

---

## 進階：再往深一層

### vmmon 的 VMEXIT 路由（逆向推測）

vmmon 在攔截到 VMEXIT 後要決定：這個 exit 可以在 ring-0 快速處理，還是要切換回 vmware-vmx userspace。快速處理的 exit 類型（例如某些 CR 存取、部分 MSR）讓 VMM 保持低延遲；需要 device 模擬的（I/O port、MMIO）則要喚醒 vmware-vmx。

具體的喚醒機制——是 `ioctl`、`eventfd`、共享記憶體 flag 還是其他方式——是**逆向推測**，未見公開文件明確描述。但從行為上觀察：vmware-vmx 是個有事件迴圈的行程，跟 QEMU 的 main loop 類似，等待 exit 通知然後調度 handler。

### VMCI 的多路複用（據公開 VMCI SDK）

VMCI（VMware VMCI Bus，在 VMware 官方文件中有部分描述）支援 datagram 和 stream 兩種通訊，並有 context ID（CID）概念，每台 VM 有自己的 CID。host 端（vmware-vmx）維護一個 multiplexer，把不同 VM 的 VMCI 流量分離。這個 multiplexer 的實作細節在 vmware-vmx 的 binary 裡，沒有原始碼。

### Enhanced Backdoor（I/O port 0x5659）

標準 backdoor 走 I/O port 0x5658，Enhanced Backdoor（據公開研究文件）走 0x5659，支援更大的資料傳輸（用 `rep ins`/`rep outs`）。Enhanced Backdoor 的存在意義是讓 RPCI 能傳大塊資料（例如 drag&drop 的 binary payload）。Ch 33 會細講這個。

---

## 動手練習

> 注意：以下練習在**有安裝 VMware Workstation 的 host** 上執行，需要 Linux host（或 Windows host + WSL）。VMware Workstation 需要合法授權。

**練習 1：確認 vmware-vmx 行程存在**

啟動一台 VMware Workstation VM，在 host 上觀察：

```bash
# Linux host
pgrep -a vmware-vmx
# 應看到至少一個 vmware-vmx 行程，對應你開的 VM

ps aux | grep vmware-vmx
# 注意 PID、記憶體使用量、執行時間

# 查看它的記憶體映射（理解 ASLR 佈局）
cat /proc/<PID>/maps | head -30
```

記錄：vmware-vmx 的二進位在哪個路徑？載入了哪些 shared library？

**練習 2：字串搜尋攻擊面入口（未實測，請自行驗證）**

找到 vmware-vmx binary（路徑通常在安裝目錄），執行：

```bash
# 基本統計
strings vmware-vmx | wc -l

# 找 RPCI / backdoor 相關
strings vmware-vmx | grep -i "rpci"
strings vmware-vmx | grep -i "guestRPC"
strings vmware-vmx | grep -i "vmci"
strings vmware-vmx | grep -i "svga"
strings vmware-vmx | grep -i "hgfs"

# 找可能的版本/模組資訊
strings vmware-vmx | grep -E "^[0-9]+\.[0-9]+\.[0-9]+"
```

記錄：哪些介面的相關字串最多？有沒有看到像 `"RPCI: unrecognized command"` 或 `"SVGA FIFO:"` 這類 parser 附近的 debug 字串？

**練習 3：用 Ghidra 開啟 vmware-vmx（逆向推測，未實測）**

```
1. 下載 Ghidra（NSA 開源）：https://ghidra-sre.org/
2. 開新 Project → Import vmware-vmx binary
3. 讓 Ghidra 自動分析（耗時數分鐘）
4. 在 Window → Defined Strings 搜尋 "rpci" 或 "SVGA"
5. 點擊找到的字串 → 右鍵 → References → Show References
6. 跳到引用這個字串的函式，看函式的反編譯輸出
7. 嘗試追蹤 caller chain，看能不能找到 dispatch 結構
```

你不需要完全理解——這個練習的目的是體驗「從字串找入口」的感覺，以及逆向輸出的可讀性（和 QEMU 原始碼比較）。

**練習 4：gdb attach vmware-vmx（需要 Linux host）**

```bash
# 啟動 VM 後
sudo gdb -p $(pgrep vmware-vmx)

# 查看行程映射
(gdb) info proc mappings

# 讓行程繼續跑
(gdb) c

# 在 guest 裡做一些操作（移動滑鼠、開 terminal）
# 然後 Ctrl-C 中斷
(gdb) backtrace
# 看 vmware-vmx 此時在哪個函式裡，了解事件迴圈的大概結構
```

---

## 本章重點整理

- VMware Workstation 的核心元件：vmware-vmx（host userspace，每 VM 一個行程，含 device 模擬）、vmmon（ring-0 驅動，負責 VMCS 管理與 VMEXIT 路由）、vmware-tools（guest 端）、vmnet（host 端網路）。
- **vmware-vmx 是攻擊的主要目標**：它在 host userspace、有廣大的 device 模擬攻擊面，利用手法是熟悉的 heap pwn。
- 攻擊面入口（公開研究確認）：backdoor（I/O port 0x5658）、RPCI/guestRPC、VMCI、SVGA II FIFO、HGFS（shared folder）、drag&drop/clipboard、mks。
- 閉源逆向工作流：`strings` 找入口字串 → IDA/Ghidra 交叉引用 → 動態調試（gdb/x64dbg attach）驗證假設。
- VMSA 後的 patch diff（bindiff）是 1-day 研究的核心技能。
- 始終區分：公開文件確認 vs 逆向推測 vs 未實測。VMware 的 binary 細節屬於後兩者。

---

## 自我檢核

- [ ] 我能說出 vmware-vmx、vmmon、vmware-tools 各自跑在哪一層（host/guest、user/kernel）
- [ ] 我知道為什麼 vmware-vmx 是攻擊目標而不是 vmmon
- [ ] 我能列出至少 4 個公開確認的 VMware guest-host 介面（攻擊面入口）
- [ ] 我理解 `strings vmware-vmx | grep -i rpci` 這個動作在做什麼、能找到什麼
- [ ] 我能解釋「IDA 交叉引用」如何幫助定位 parser 函式
- [ ] 我能解釋 patch diff（bindiff）在 1-day 分析中的作用
- [ ] 我清楚哪些關於 vmware-vmx 的說法是公開文件確認的，哪些是逆向推測

---

## 延伸閱讀

1. **Keen Lab「VMware Guest to Host Escape in the Wild」（各年 Black Hat/DEF CON 演講）**
   - 讀哪裡：在 BlackHat conference archive 搜 "Keen Lab VMware"；多份 slide 可公開下載
   - 學什麼：研究界如何系統性地逆向 vmware-vmx、定位 RPCI/SVGA 攻擊面、從靜態逆向到動態驗證的完整流程
   - 關聯：本章逆向工作流的真實對照

2. **Computest「Make VMware Escape Again」（DEF CON 26 / 2018）**
   - 讀哪裡：https://www.computest.nl/en/knowledge-hub/blog/make-vmware-escape-again/ 及對應 slide
   - 學什麼：針對 VMware Workstation drag&drop 功能的逆向與漏洞利用；patch diff 實例；如何從 VMSA 反推洞的位置
   - 關聯：本章 patch diff 流程的具體案例

3. **VMware VMCI SDK 文件（VMware Developer Documentation）**
   - 讀哪裡：VMware Developer portal 的 VMCI 相關文件（公開）
   - 學什麼：VMCI 的 datagram/stream 協定、CID 概念、host 端 API；這是為數不多 VMware 公開的通道設計文件
   - 關聯：了解 VMCI 攻擊面的協定層設計，再去逆向時有更好的心智模型

4. **NSA Ghidra 官方文件與教學**
   - 讀哪裡：https://ghidra-sre.org/ → Documentation；YouTube「Ghidra beginner tutorial」
   - 學什麼：Ghidra 的基本操作（匯入 binary、自動分析、Strings 視窗、Xref、反編譯器）；足夠你做本章練習 3
   - 關聯：閉源逆向的核心工具，VMware 研究的起點

5. **VMware Security Advisories（VMSA）歷史列表**
   - 讀哪裡：https://www.vmware.com/security/advisories.html
   - 學什麼：VMware 歷年對 Workstation 的安全修補；注意「vmx process」/ "guest-to-host" 標記的 advisory，這些就是 device emulation 的洞；對照影響元件（SVGA、HGFS、RPCI）和本章攻擊面圖
   - 關聯：1-day 研究的起點，也是練習 patch diff 的原料

---

前五個 Part 我們打的是開源 hypervisor，有原始碼當地圖。Part 6 換了一個根本規則：你沒有地圖，只有一個 binary 和從它的行為推斷出的假設。本章建立了在這種條件下工作的基礎框架——元件組成、攻擊面入口、逆向工作流。

接下來我們拿第一個具體的攻擊面入口開刀。

→ [Ch 33 — Backdoor / RPCI：guest→host 通訊通道](./33-vmware-backdoor-rpci.md)
