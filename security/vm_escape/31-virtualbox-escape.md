# Ch 31 — VirtualBox 逃逸 exploit 復刻

> **目標**：以 Zelenyuk 的 E1000 0day（CVE-2018-3295）為主線，走完「整數下溢 → OOB write → vtable 劫持 → host ring-3 RCE」完整利用鏈，並和 QEMU 利用鏈（Ch 16-22）做逐點對照。

> **環境**：VirtualBox 5.2.20 debug build / x86-64 / Linux host（Zelenyuk 版本）

---

## 為什麼需要這個？

Ch 29 建立了 VirtualBox 的架構直覺：VBoxDD.so 跑在 host ring-3，每個虛擬裝置都是 C++ class，ring-0 驅動（VBoxDrv）和 ring-3 process 之間靠 ioctl 溝通。Ch 30 深挖了 E1000/AHCI/HDA 的具體 bug，重點在 `e1kFallbackAddToFrame()` 的整數下溢。

現在的問題是：拿到一個「可以往 heap 某個位移 OOB write 幾個 byte」的原語（primitive），要怎麼把它變成 host 上的 `system("/bin/bash")`？

這一章做三件事：

1. 把 Ch 30 的 bug 轉化成具體的利用鏈，每一步說清楚原理
2. 點出 VirtualBox 特有的陷阱：IPRT heap、C++ vtable、沒有 seccomp
3. 和 QEMU 的利用鏈做對照，讓你看清楚「虛擬化逃逸」這類漏洞的共同骨架

**誠實聲明**：本章所有 exploit 步驟均標示**【未實測，以公開 writeup 及原始碼為據】**。公開原始碼是 Zelenyuk 釋出的 GitHub 倉庫（MorteNoir1/virtualbox_e1000_0day）以及 VirtualBox 5.2.20 source。復刻請自備 debug build 環境，不要對生產系統測試。

---

## 先建立直覺

### 整個攻擊的骨架

```
guest kernel (ring-0 in guest)
  │  構造惡意 E1000 TX descriptor
  │  mmio / pio write 觸發 host 端 E1000 模擬
  ▼
host ring-3 VBoxDD.so
  │  e1kFallbackAddToFrame() 整數下溢
  │  OOB write → 覆蓋 heap 上某物件的 vtable pointer
  │  OOB read  → leak VBoxDD.so 位址（計算 PIE base）
  │  觸發 virtual method call
  │  → fake vtable → ROP chain → system("/bin/bash")
  ▼
host ring-3 shell（已逃逸）
  │  （選做）/dev/vboxdrv ioctl 提權
  ▼
host ring-0
```

三個關鍵問題：
- **哪裡 OOB write**：E1000 TX frame 重組緩衝區（`aFallbackBuffer`）
- **覆蓋什麼**：`DevE1000State` 或鄰近 heap chunk 內的 C++ vtable pointer
- **怎麼跳**：virtual destructor / timer callback 被 dispatch 時，CPU 跟著假 vtable 走進 ROP

### 為什麼是 vtable？

VirtualBox 的每個模擬裝置（E1000、AHCI、HDA）都是 C++ class。物件在記憶體裡的開頭 8 bytes（64-bit）是 vtable pointer，指向一張函式指標陣列。只要把這 8 bytes 覆蓋成攻擊者控制的位址，下一次 virtual method dispatch 就等於把控制流交出去了。

QEMU 沒有 vtable，它用 C struct 加 function pointer；道理一樣，但操作細節不同（後面對照表詳述）。

### loopback 模式的用處

E1000 支援「loopback」模式：TX 送出的封包直接回環到 RX，不經真實網卡。這讓我們可以：

1. 不需要真實網路連線就能持續觸發 TX/RX 流程
2. 繞過某些只在「有外部連線」時才走的狀態分支
3. 快速重複觸發，方便 heap spray 和 timing 控制

開啟方式：寫 E1000 的 CTRL 暫存器，設定 `CTRL_LOOP_BACK_MODE`（bit 6）。

---

## 底層機制：完整利用鏈逐步拆解

### 前置條件

| 條件 | 說明 |
|------|------|
| VirtualBox 版本 | ≤ 5.2.20 |
| 網卡型態 | Intel PRO/1000 MT Desktop（E1000），**NAT 模式** |
| guest 權限 | root（需要能直接操作 MMIO/PIO 暫存器或載入 LKM） |
| host OS | Linux x86-64（Zelenyuk PoC 目標） |

NAT 模式是因為只有 NAT 下 E1000 的 TX 封包會走 `e1kFallbackAddToFrame()`；bridged 或 host-only 走不同的程式碼路徑。**【未實測，以公開 writeup 及原始碼為據】**

---

### Step 1：觸發整數下溢

**【未實測，以公開 writeup 及原始碼為據】**

回顧 Ch 30 的 bug。`e1kFallbackAddToFrame()` 用來把分散的 TX descriptor 重組成完整 frame，內部維護一個計數器追蹤還剩多少空間：

```c
/* 簡化示意，非原始碼 */
static int e1kFallbackAddToFrame(PE1KSTATE pThis, E1KTXDESC *pDesc, ...)
{
    uint16_t cbLeft = sizeof(pThis->aFallbackBuffer) - pThis->u16TxPktLen;
    /* 若 pThis->u16TxPktLen > sizeof(aFallbackBuffer)
       cbLeft 是 uint16_t，下溢變成大正數 */
    memcpy(pThis->aFallbackBuffer + pThis->u16TxPktLen,
           pDesc->data.u64BufAddr_mapped, cbLeft);  /* OOB write */
}
```

`aFallbackBuffer` 是 `DevE1000State` struct 內的固定大小陣列（約 1514 bytes，MTU）。當 `u16TxPktLen` 被構造成超過這個大小時，`cbLeft` 下溢成大數，`memcpy` 就往 `aFallbackBuffer` 結尾之後寫入 guest 控制的資料。

**構造方式**：送多個 context descriptor + data descriptor，讓累計長度剛好超過 1514 bytes，然後再送一個帶資料的 descriptor 觸發 `e1kFallbackAddToFrame()`。

---

### Step 2：決定 OOB write 的目標

**【未實測，以公開 writeup 及原始碼為據】**

`aFallbackBuffer` 之後的記憶體佈局取決於 IPRT heap 如何配置 `DevE1000State`。

```
heap chunk：
  [DevE1000State header]
  [aFallbackBuffer, 1514 bytes]
  ← OOB write 從這裡開始往後覆蓋
  [DevE1000State 其他成員，包含 vtable pointer 或嵌入 C++ 物件]
  [chunk footer / next chunk header]
  [鄰近 chunk，可能是另一個 C++ 物件]
```

目標有兩個候選：

1. **同一個 chunk 內**：`DevE1000State` 若有繼承自某個基底 class 的 vtable pointer，且這個 pointer 在 `aFallbackBuffer` 的偏移之後，直接覆蓋。
2. **鄰近 chunk**：IPRT heap 在 `DevE1000State` 之後配置了另一個有 vtable 的物件，OOB write 跨越 chunk 邊界覆蓋它的 vtable pointer。

Zelenyuk 的 PoC 採用第二種策略，透過 heap spray 讓目標物件可靠地落在 `DevE1000State` 之後固定偏移。

IPRT heap（`RTMemAlloc`）底層仍是 mmap 或 malloc，但 VirtualBox 自己管理 pool，所以 chunk 的相對位置需要實測。debug build 下用 gdb 看 `p &pThis->aFallbackBuffer` 和周圍物件的位址差就能確認。

---

### Step 3：Leak primitive — 繞過 ASLR

**【未實測，以公開 writeup 及原始碼為據】**

在 OOB write 覆蓋 vtable pointer 之前，要先知道 VBoxDD.so 的 PIE base，才能算出 fake vtable 和 ROP gadget 的正確位址。

Zelenyuk 的做法是利用同一個 `e1kFallbackAddToFrame()` bug 的**讀路徑**：

1. 把 `aFallbackBuffer` 後方某個偏移的 8 bytes 讀回 guest（透過某個 TX 完成後的狀態回報，或構造特定封包讓 E1000 把 host 記憶體的內容放到 RX descriptor 讓 guest 讀）。
2. 這 8 bytes 是 VBoxDD.so 內部某個 pointer（例如 vtable 本身的某個 entry，或是 pThis 某個成員指向的函式）。
3. 減掉已知的 section 偏移，得到 PIE base。

這裡的細節是 exploit 裡最脆弱的部分，需要對具體 VirtualBox 5.2.20 build 的記憶體佈局有精確的理解。debug build 下可以先用 gdb 硬查偏移，再把偏移寫進 exploit。

```
計算：
  PIE base = leaked_ptr - offset_within_VBoxDD_so
  heap base ≈ PIE base + 已知距離（視 build 而定）
```

---

### Step 4：構造 fake vtable

**【未實測，以公開 writeup 及原始碼為據】**

vtable 是一張函式指標陣列。我們只需要讓其中**一個被呼叫的 entry** 指向 ROP chain 的第一個 gadget（或直接指向 `system` 的位址，如果不需要繞任何保護的話）。

```
fake_vtable（放在 guest 可控的某塊記憶體，或透過 spray 放在 host heap）:
  [0x00] → pointer to gadget_0  (對應 virtual method index 0)
  [0x08] → pointer to gadget_1  (若被呼叫的是 method 1)
  ...
```

因為沒有 seccomp，一旦跳到 `system()` 就能執行任意 host 命令。ROP chain 可以很短：找一個 `pop rdi; ret` gadget，把 `"/bin/bash"` 字串位址放進 `rdi`，然後 `ret` 到 `system`。

gadget 位址 = PIE base + gadget_offset_in_VBoxDD_so（用 ROPgadget 或 ropper 在 VBoxDD.so 上找）。

---

### Step 5：觸發 virtual method call

**【未實測，以公開 writeup 及原始碼為據】**

覆蓋完 vtable pointer 之後，需要讓 VirtualBox 去呼叫那個被覆蓋物件的某個 virtual method。

常用的觸發路徑：

1. **Timer callback**：E1000 有多個 IPRT timer，timer 到期時 VirtualBox 呼叫 `pTimer->pfnTimer()`，如果 timer 物件的 vtable 被覆蓋，dispatch 就跳到 fake vtable。
2. **Destructor**：如果可以讓某個物件被釋放（例如 reset/reinitialize E1000），其 virtual destructor 被呼叫。
3. **某個 E1000 virtual method**：直接找 `DevE1000State` 的某個繼承 method，在 guest 操作對應的 MMIO 暫存器觸發。

Zelenyuk 的 PoC 具體用哪條路徑，請參考原始碼，這裡不憑記憶猜測。

---

### Step 6：host ring-3 RCE

**【未實測，以公開 writeup 及原始碼為據】**

ROP chain 執行完，`system("/bin/bash")` 在 host ring-3 跑起來。這個 shell 的身份是 `VBoxHeadless` 或 `VirtualBoxVM` 的執行身份（通常是啟動 VM 的那個 user）。

從 guest 的角度：送完最後一個 descriptor，過幾毫秒（timer 週期），host 上出現 shell。如果改成 reverse shell，可以從 guest 裡的 terminal 接到 host 的 shell。

---

### Step 7（bonus）：/dev/vboxdrv ioctl 提權

**【未實測，以公開 writeup 及原始碼為據】**

Host ring-3 shell 還不是 ring-0。VirtualBox 的 ring-0 驅動是 `/dev/vboxdrv`，只有 `vboxusers` 群組成員可以開它。

如果 VBoxHeadless 的執行 user 本來就在 `vboxusers` 裡（通常是，不然 VM 跑不起來），可以對 `/dev/vboxdrv` 送特定 ioctl。VirtualBox 的 ring-0 interface 有 `SUPR0... ` 系列函式（可在 `include/VBox/sup.h` 找到），透過 ioctl 可以讓 ring-0 執行任意程式碼。

這一步把 host ring-3 shell 升級到 host kernel，等同於完整接管整台機器。Zelenyuk 在公開說明裡有提及這個路徑的存在，但 PoC 主體停在 ring-3 shell。

---

### ASCII 圖：利用鏈與 vtable 覆蓋

```
guest kernel
  │
  │  構造惡意 TX descriptor ring
  │  loopback 模式 on
  │  mmio write → 觸發 E1000 TX 模擬
  ▼
host VBoxDD.so — e1kFallbackAddToFrame()
  │
  │  u16TxPktLen 下溢 → cbLeft = 大數
  │  memcpy(aFallbackBuffer + offset, guest_data, cbLeft)
  │       │
  │       ├─ [0..1513]  aFallbackBuffer 正常範圍
  │       └─ [1514..]   OOB write ──────────────────────────┐
  │                                                          │
  │  先做 OOB read → leak VBoxDD.so ptr → 計算 PIE base     │
  │                                                          ▼
  │                                              [鄰近 heap chunk]
  │                                               vtable_ptr: 0xXXXX
  │                                                    │
  │                                                    │ 被覆蓋成
  │                                                    ▼
  │                                              fake_vtable（guest 控制）
  │                                               [0] → pop rdi; ret
  │                                               [1] → addr of "/bin/bash"
  │                                               ...  ret → system()
  │
  │  觸發 virtual method call（timer / destructor）
  │       │
  │       └─ CPU 讀 vtable_ptr（已被覆蓋）
  │          → 跳 fake_vtable[N]
  │          → ROP chain
  │          → system("/bin/bash")
  ▼
host ring-3 shell ✓
  │
  │（選做）open("/dev/vboxdrv") + ioctl
  ▼
host ring-0 ✓
```

---

## 對比與取捨

### VirtualBox vs QEMU 利用鏈對比

| 面向 | QEMU（Ch 16-22） | VirtualBox（本章） |
|------|-----------------|-------------------|
| 漏洞位置 | MMIO handler（e.g. E1000、PCNET） | E1000 TX descriptor 重組（E1000 NAT） |
| OOB 類型 | MMIO OOB read/write | TX buffer 整數下溢 → OOB write |
| 劫持目標 | C struct 的 function pointer | C++ class 的 vtable pointer |
| 記憶體配置 | glibc malloc / g_malloc（GLib） | IPRT RTMemAlloc（VirtualBox 自有 pool） |
| heap spray 策略 | 控制 glibc chunk 佈局 | 需要理解 IPRT pool 的配置順序 |
| sandbox | QEMU seccomp（需繞 sandbox） | 無 seccomp；ring-3/ring-0 靠 VBoxDrv 分離 |
| ASLR 繞過 | OOB read leak glibc/QEMU .text ptr | OOB read leak VBoxDD.so ptr |
| ROP chain | 較短，seccomp 後找可用 syscall | 較短，無 seccomp 直接 system() |
| 最終目標 | host ring-3 process exec | host ring-3 + 可選 ring-0（/dev/vboxdrv） |
| 可靠度 | 視 heap feng shui 而定 | Zelenyuk 聲稱 100% reliable |
| 公開 PoC | 多個 CTF writeup | MorteNoir1/virtualbox_e1000_0day |

**共同骨架**：OOB primitive → leak → 計算位址 → 覆蓋跳轉點 → ROP → shell。差別只在每一步的具體實現。

---

## 踩雷集錦

### 1. IPRT heap 不是 glibc，別直接套 glibc 技巧

`RTMemAlloc` 內部有自己的 pool allocator，chunk 的 header layout 和 glibc 不同，也沒有 tcache/fastbin。你在 glibc exploit 裡用的 `unsorted bin attack` 之類的技術在這裡全部失效。需要重新實測 IPRT 的配置行為才能做 heap feng shui。

### 2. NAT 模式才觸發，bridged/host-only 走不同路徑

`e1kFallbackAddToFrame()` 在 NAT 模式下的 TX 路徑才會被呼叫。如果你把 VM 設成 bridged adapter，exploit 送進去什麼都不會發生。改模式之後要整個重開 VM。

### 3. vtable 偏移必須對準具體 build

vtable pointer 在 `DevE1000State`（或鄰近物件）的偏移，隨 VirtualBox 版本和 compiler 選項不同而變。你不能直接把 Zelenyuk PoC 的 hardcoded offset 套在另一個 build 上。每次換 build 都要用 gdb 重查。

### 4. release build 有 stack canary / RELRO

在 release build 的 VBoxDD.so 上，stack canary 和 RELRO 都可能開啟，這會讓某些簡單的跳法失效。學習階段先用 debug build（`./configure --build-type=debug`），確認利用鏈走通之後再考慮繞保護的問題。

### 5. loopback 要在 CTRL 暫存器設對 bit

E1000 loopback 模式靠 CTRL 暫存器的 `LOOP_BACK_MODE`（bit 6）和 `TXCW` 的某些位控制。設錯了封包根本送不進去，exploit 完全不觸發。先用正常模式確認 descriptor 有被 dispatch，再開 loopback。

---

## 進階：再往深一層

### Pwn2Own 2023 — 現代 VirtualBox 逃逸

Qrious Security 在 Pwn2Own 2023 對 VirtualBox 7.0.6 拿下逃逸，用了兩個洞的組合：

- **CVE-2023-21987**（TPM MMIO stack OOB write）：提供 write primitive
- **CVE-2023-21991**（VGA OOB read）：提供 infoleak

這和 Zelenyuk 的邏輯一模一樣（一個 leak + 一個 write），只是漏洞位置換到 TPM 和 VGA 子系統。說明了這套「兩洞組合」的架構有多普遍。

### 為什麼 VirtualBox 沒有 seccomp？

QEMU 的設計選擇是在 guest 開始運行後，主執行緒進入 seccomp 沙盒，限制自身能呼叫的 syscall 集合。VirtualBox 的設計哲學不同：它把 ring-3 和 ring-0 分離成 VBoxDD.so 和 VBoxDrv，認為這樣的分層已經提供足夠的隔離。

這個選擇的後果是：一旦攻擊者拿到 VBoxDD.so 的 code exec，就能直接呼叫任意 syscall，不需要繞 seccomp filter。

### 對 `DevE1000State` 做結構逆向

如果沒有 source code（假設你只有 VBoxDD.so binary），可以：

1. 用 Ghidra / IDA 的 RTTI 分析找 `E1000` 相關的 class hierarchy
2. 找 vtable 的 xref，確定 vtable pointer 在物件裡的偏移
3. 用 `ptype` 指令在 debug build 的 gdb 裡確認 struct layout（有 debug symbol 的話）

這個技術直接可以遷移到任何有 vtable 的 C++ 漏洞研究。

---

## 動手練習

### 環境建置

```bash
# 下載 VirtualBox 5.2.20 source（Oracle Archive）
# https://download.virtualbox.org/virtualbox/5.2.20/VirtualBox-5.2.20.tar.bz2

./configure --build-type=debug --disable-hardening
# --disable-hardening 在 debug build 下關掉某些保護，方便研究

make -j$(nproc) 2>&1 | tail -20

# 啟動 debug build 的 VBoxHeadless
./out/linux.amd64/debug/bin/VBoxHeadless --startvm "YourVM"
```

### 練習 A：確認 `e1kFallbackAddToFrame()` 觸發

**【未實測，以公開 writeup 及原始碼為據】**

```bash
# 在另一個 terminal
gdb -p $(pgrep VBoxHeadless)

# 設斷點
(gdb) break e1kFallbackAddToFrame
(gdb) continue

# 從 guest 送一個正常 TCP 封包，確認斷點觸發
# 然後觀察 pThis->u16TxPktLen 和 aFallbackBuffer 的位址
(gdb) p pThis->u16TxPktLen
(gdb) p &pThis->aFallbackBuffer
(gdb) p sizeof(pThis->aFallbackBuffer)
```

### 練習 B：測量 heap 佈局

**【未實測，以公開 writeup 及原始碼為據】**

```bash
# 在 gdb 內查 DevE1000State 結尾和下一個 heap chunk 的距離
(gdb) p sizeof(E1KSTATE)
(gdb) x/32gx (void*)pThis + sizeof(E1KSTATE)
# 觀察 aFallbackBuffer 之後的記憶體，找 vtable pointer 的特徵（指向 VBoxDD.so 的位址）
```

### 練習 C：跑 Zelenyuk PoC（LKM）

**【未實測，以公開 writeup 及原始碼為據】**

```bash
# 在 guest（root）裡
git clone https://github.com/MorteNoir1/virtualbox_e1000_0day
cd virtualbox_e1000_0day
make  # 編譯 kernel module
insmod exploit.ko  # 載入，觀察 host 上發生什麼事
dmesg | tail -20   # 看 module 的輸出
```

觀察重點：
- host gdb 有沒有在 `e1kFallbackAddToFrame` 斷下？
- 斷下時 `pThis->u16TxPktLen` 是多少？OOB 觸發了嗎？
- vtable pointer 有沒有被改掉？

### 練習 D：找 ROP gadget

```bash
# 在 host 上
ROPgadget --binary ./out/linux.amd64/debug/bin/VBoxDD.so \
          --rop --depth 3 | grep "pop rdi"

# 找 system() 位址
objdump -d ./out/linux.amd64/debug/bin/VBoxDD.so | grep -A3 "<system@plt>"
```

---

## 本章重點整理

- **漏洞**：`e1kFallbackAddToFrame()` 的 `uint16_t` 計數器下溢，讓 `memcpy` 往 `aFallbackBuffer` 結尾之後 OOB write guest 控制的資料
- **利用鏈五步**：整數下溢 OOB write → OOB read leak PIE base → 覆蓋 vtable pointer → 構造 fake vtable + ROP → 觸發 virtual method call
- **loopback 模式**：讓 TX 封包回環 RX，不需真實網路，可持續觸發，繞某些狀態檢查
- **IPRT heap**：VirtualBox 自有 heap pool，heap feng shui 技術需重新實測，不能套 glibc 技巧
- **C++ vtable vs function pointer**：VirtualBox 用 vtable 劫持，QEMU 用 C struct function pointer 劫持；原理相同，操作不同
- **無 seccomp**：拿到 ring-3 code exec 後可直接呼叫任意 syscall，`system()` 直接可用
- **ring-0 升級**：host ring-3 shell 後，可透過 `/dev/vboxdrv` ioctl 再提權到 host kernel

---

## 自我檢核

- [ ] 我能說明 `e1kFallbackAddToFrame()` 的整數下溢是如何發生的，包括觸發條件（NAT 模式、累計長度超過 MTU）
- [ ] 我能解釋為什麼 loopback 模式有助於 exploit 的穩定性
- [ ] 我能描述 IPRT heap 和 glibc malloc 的關鍵差異，以及這對 heap feng shui 的影響
- [ ] 我能解釋 C++ vtable 劫持的原理，以及為什麼覆蓋 vtable pointer 就能控制控制流
- [ ] 我能說明 leak primitive 的目的（繞 ASLR / PIE），以及在 VirtualBox 場景裡怎麼取得 leak
- [ ] 我能對照說出 VirtualBox 和 QEMU 利用鏈的至少三個關鍵差異
- [ ] 我知道如何在 debug build 環境裡設 gdb 斷點驗證 OOB 觸發和 vtable 覆蓋
- [ ] 我理解為什麼 `/dev/vboxdrv` ioctl 可以把 host ring-3 shell 升級到 ring-0

---

## 延伸閱讀

1. **Zelenyuk 原始公開說明**（2018）：`https://github.com/MorteNoir1/virtualbox_e1000_0day` — exploit source code + README，一手資料，所有 offset 都在這裡
2. **VirtualBox 5.2.20 原始碼**：`src/VBox/Devices/Network/DevE1000.cpp` — 直接找 `e1kFallbackAddToFrame`，對照 bug 的實際程式碼；可從 Oracle VirtualBox 下載頁取得
3. **Qrious Security Pwn2Own 2023 writeup**：搜尋 "CVE-2023-21987 CVE-2023-21991 VirtualBox Pwn2Own 2023 Qrious"，示範現代版本的兩洞組合逃逸
4. **IPRT 記憶體管理原始碼**：`src/VBox/Runtime/common/alloc/` — 理解 RTMemAlloc 的 pool 機制，對 heap feng shui 不可少
5. **"Pwn2Own: Exploiting Virtual Machines" — various DEF CON/Black Hat talks**：每年都有新的 VM 逃逸研究，搜尋 DEF CON / Black Hat YouTube 頻道，關鍵字 "hypervisor escape" 或 "VirtualBox exploit"

---

Ch 31 到這裡結束。我們把一個整數下溢走成了完整的利用鏈，把 C++ vtable 劫持和 QEMU 的 function pointer 劫持放在同一張表裡對照，也確認了「無 seccomp = 逃到 ring-3 就幾乎贏了」這個 VirtualBox 特有的設計選擇。

下一章進入 Part 6，對象換成 VMware。VMware 的架構和 VirtualBox 不同，特別是它的 SVGA 和 VMCI 子系統——都是有趣的攻擊面。

→ [Ch 32](./32-vmware-architecture.md)
