# Ch 35 — VMware 逃逸案例：Pwn2Own writeup 導讀

> **目標**：學會從一篇閉源 hypervisor 逃逸的 writeup 還原攻擊者的完整思考路徑——包括入口選擇、漏洞推導、原語構造、host 利用，以及從 VMSA patch diff 反推洞的通用 1-day 方法。

---

> **認識論聲明**：本章所有技術細節均來自**公開 writeup 與 VMSA 公告**，未在 VMware Workstation 上實測任何 PoC。
> - Synacktiv 案例：引用自 https://www.synacktiv.com/en/publications/on-the-clock-escaping-vmware-workstation-at-pwn2own-berlin-2025
> - Keen Lab 案例：引用自 https://keenlab.tencent.com/en/2018/04/23/A-bunch-of-Red-Pills-VMware-Escapes/
>
> 凡 writeup 沒有明確敘述的推測，本章一律標「**推測**」。凡已有公開依據的技術描述，標「**writeup 所述**」或「**據公開資料**」。

---

## 為什麼需要這個？

VMware Workstation 是閉源商業軟體。它的二進位你可以買到，原始碼你拿不到。

這個事實從根本上改變了攻擊者的工作方式。

對 QEMU 你能做的事：拿原始碼、搜 `memcpy`、找邊界缺失、從 commit history 追每一個修補背後的 bug。整條「從程式碼找洞」的工作流是開放的。對 VMware，這條路封死了。你剩下兩條路：

1. **讀 writeup**：前人打出來、公開了的，你把攻擊路徑還原並吸收進自己的方法論。
2. **讀 patch diff（VMSA）**：VMware 發安全公告時有時附 patch，有時你能 diff 兩個版本的二進位——從「修了什麼」反推「原本壞在哪」。

這兩條路的價值不對等。writeup 告訴你這個洞怎麼打，VMSA diff 告訴你下一個洞可能在哪。兩者合在一起，才構成在閉源 hypervisor 上做有效研究的基礎能力。

本章的目標不是讓你「知道 CVE-2025-41238 的細節」——細節會過時。目標是讓你讀任何一篇 hypervisor 逃逸 writeup 時都有辦法問出正確的問題，並且能把答案轉換成可複用的攻擊方法論。

---

## 先建立直覺

### 閉源逃逸的工作流長這樣

```
┌──────────────────────────────────────────────────────────────┐
│  閉源 hypervisor 逃逸工作流                                   │
│                                                              │
│  1. 選攻擊面（不靠程式碼，靠 device list + 逆向入口）         │
│     ↓                                                        │
│  2. 靜態逆向 → 找「guest 能控制的輸入進到的 handler」         │
│     ↓                                                        │
│  3. 動態驗證（attach debugger / fuzzing）                    │
│     ↓                                                        │
│  4. 確認 bug：類型？觸發條件？可重現？                        │
│     ↓                                                        │
│  5. 構造原語：infoleak → 任意讀/寫 → 控制流劫持              │
│     ↓                                                        │
│  6. Host code exec：在 host 端落地（Windows / Linux）        │
│     ↓                                                        │
│  7. 繞 host mitigation：ASLR / LFH 隨機化 / heap guard      │
└──────────────────────────────────────────────────────────────┘
```

這七步不是 VMware 專屬的——你在 VirtualBox、Hyper-V、甚至部分 QEMU 閉源外掛上會遇到完全一樣的流程。差異在細節：host 是 Windows 還是 Linux、用哪個 allocator、哪個 device 是主攻面。

### 為什麼 VMware 逃逸幾乎都是 bug chain

單洞落地（single-bug to code exec）的逃逸越來越罕見，原因有三：

1. **VMware 的記憶體操作大量有界**（writeup 裡攻擊者常常抱怨「能 overflow 但範圍被限制」）。一個 overflow 往往只能覆蓋特定偏移，不夠直接控 RIP。
2. **Host 端 ASLR + heap 隨機化**：Windows LFH（Low Fragmentation Heap）的 bucket 隨機化讓攻擊者無法預測目標 chunk 的位置，需要 infoleak 或 side-channel 先定位。
3. **NX / DEP 加上 CFG（Control Flow Guard）**：Windows 上 vmware-vmx.exe 開 CFG，間接 call 的目標必須是合法程式碼，不能直接跳 shellcode 或任意 ROP gadget。

這三點加在一起，典型的現代 VMware 逃逸是：
- **Bug 1**：infoleak（洩漏 heap 位址或 vmx.exe base）
- **Bug 2**（或同一 bug 的第二次觸發）：受控寫覆蓋目標
- **host 端**：LFH 排布 → 精確覆蓋 → 劫持控制流

---

## 主案例：Synacktiv @ Pwn2Own Berlin 2025

### 背景

- **writeup 所述**：作者 Thomas Bouzerar 與 Etienne Helluy-Lafont（Synacktiv），公開於 2025 年 Pwn2Own Berlin 後。
- **CVE**：CVE-2025-41238
- **獎金**：$80,000（据公開資料）
- **目標環境**：VMware Workstation on Windows 11（host），guest OS 未指定
- **攻擊面**：PVSCSI（Paravirtual SCSI）控制器 emulation

這個案例有幾件事值得注意，在讀 writeup 前先建立期待：

PVSCSI 是 VMware 自家的 paravirtual SCSI 控制器，用來加速 I/O。它不是「傳統的硬體模擬」——PVSCSI 的整個設計假設 guest driver 知道自己在 VMware 裡，並且主動配合使用特定的 ring buffer 協定。這表示它的介面更複雜、更依賴 guest 提供的結構，攻擊面寬度和傳統模擬 SCSI adapter 不一樣。

大部分 VMware 逃逸長期走 SVGA / mks 路徑（Ch 34 詳述），PVSCSI 入口是相對少見的。Synacktiv 選這裡，說明他們在攻擊面篩選上走了非主流路徑——這本身就是一個方法論的選擇。

### 入口選擇：為什麼是 PVSCSI

**writeup 所述**：Synacktiv 的初始攻擊面調查從「列出所有 guest 可控的 I/O 路徑」開始。PVSCSI 引起他們注意，是因為它的 ring buffer 結構允許 guest 傳入大量欄位，而這些欄位在 host 端 emulation 程式碼裡有複雜的解析邏輯。

從逆向的角度（**推測**，writeup 未詳述選擇過程的全部考量），PVSCSI 的 emulation 程式碼涉及：
- 多個 ring buffer（request ring、completion ring、message ring）
- 每個 ring entry 都帶長度、偏移、SG（scatter-gather）欄位
- Guest 能夠控制 ring entry 的數量和內容

這是典型的「複雜 parser + guest 控制輸入 = 值得深挖的攻擊面」組合。

### PVSCSI 協定結構（據公開資料）

```
Guest 記憶體佈局（PVSCSI 用到的部分）
┌─────────────────────────────────────────┐
│  Rings State Page                        │
│  ┌─────────────────────────────────┐    │
│  │ req_num_entries_log2            │    │  ← request ring 大小（2 的冪次）
│  │ cmp_num_entries_log2            │    │  ← completion ring 大小
│  │ msg_num_entries_log2            │    │  ← message ring 大小
│  └─────────────────────────────────┘    │
│                                          │
│  Request Ring（guest → host）           │
│  ┌─────────────────────────────────┐    │
│  │ [ PVSCSI Request Descriptor 0 ] │    │
│  │   context, dataLen, dataAddr    │    │
│  │   senseLen, senseAddr           │    │
│  │   vcpuHint, cdbLen, cdb[16]     │    │
│  │   ...                           │    │
│  │ [ PVSCSI Request Descriptor 1 ] │    │
│  │   ...                           │    │
│  └─────────────────────────────────┘    │
│                                          │
│  SG Array（scatter-gather 分散表）      │
│  ┌─────────────────────────────────┐    │
│  │ [ addr, length ] × N            │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

Guest 送出一個 SCSI request 的流程：填好 request descriptor → 放進 request ring → kick host（寫 PVSCSI doorbell）→ host 的 emulation 程式碼從 ring 讀出 descriptor → 處理 SG array → 回填 completion ring。

問題在於：**host 怎麼相信 descriptor 裡的長度欄位？**

### 漏洞：heap overflow

**writeup 所述**：漏洞存在於 PVSCSI emulation 處理 SG（scatter-gather）list 的程式碼。Host 端在讀取 SG array 時，使用了 guest 提供的長度欄位來決定複製多少資料進一個堆積上的 buffer，但對長度的驗證不充分——允許攻擊者觸發超出 buffer 邊界的寫入（heap overflow）。

具體地說（writeup 所述的技術細節）：
- SG array 的 entry 數量或長度欄位可以被 guest 設成超過 host 端分配的 buffer 容量的值
- Host 在沒有充分邊界檢查的情況下，迭代 SG entries 並複製資料
- 結果是 heap overflow：可以覆蓋堆積上相鄰的物件

這個 overflow 的「直接控制」程度（**推測**，writeup 未給全部細節）：攻擊者可以選擇寫入的總量，但不一定能精確控制每個位元組的值（SG entry 的 addr 欄位可能指向 guest 記憶體，所以內容有一定控制性）。

### Host 端挑戰：Windows LFH 隨機化

**writeup 所述**：vmware-vmx.exe 在 Windows 11 上跑，host heap 用 Windows 的 LFH（Low Fragmentation Heap）。LFH 的設計包含 bucket 內的隨機化：同一個 size class 的 chunk 分配順序不是線性的，而是打亂過的。

這對攻擊者的意義：你沒辦法假設「我先分配 A，再分配 B，B 就在 A 後面」——這個假設在 LFH 裡不成立。

**writeup 所述**：Synacktiv 使用了 side-channel 方法來繞過 LFH 隨機化。具體方式是**觀察分配行為**來推斷 heap layout，而不是直接假設固定的佈局。

writeup 描述了一個技術（**writeup 所述**）：利用 PVSCSI 的正常操作介面，反覆分配和釋放特定大小的物件，觀察 host 端的行為（例如透過 timing 或錯誤回應），從而推斷哪些 LFH slot 目前被佔用、哪些是空的。這讓攻擊者能「感知」 heap 的目前狀態，再做精確佈局。

這個技術的分類（**推測**）：類似 Heap Feng Shui，但加入了 side-channel 觀測，讓佈局操作不再是「盲打」。在沒有 heap spray 保底的情況下，這是讓 LFH 環境下的 heap overflow 可靠化的必要手段。

### 原語構造路徑

根據 writeup 描述的攻擊流程，我們可以還原出大致的原語構造路徑（**writeup 所述的概念，部分細節為推測**）：

```
步驟 1：heap 探測（side-channel）
  └─ 反覆觸發 PVSCSI 操作，觀察 host 反應
  └─ 推斷目前 LFH 佈局狀態

步驟 2：heap groom（佈局準備）
  └─ 在目標物件前後分配已知大小的填充物件
  └─ 目標：讓 overflow 的寫入落在「我們想覆蓋的物件」上

步驟 3：觸發 overflow
  └─ 送出精心構造的 PVSCSI request（超大 SG list）
  └─ Host heap 上的相鄰物件被覆蓋

步驟 4：infoleak（推測 writeup 包含此步驟，未詳述）
  └─ 覆蓋後的物件被 vmx 使用時，回傳洩漏 host 地址
  └─ 取得：vmx.exe base 或 heap 位址

步驟 5：精確覆蓋
  └─ 有了地址基礎，再次 groom + overflow
  └─ 覆蓋目標：function pointer 或 vtable 指標

步驟 6：觸發劫持
  └─ 讓 vmx.exe 呼叫被覆蓋的指標
  └─ 控制 RIP → host code exec
```

**重要注意**：writeup 沒有一行一行描述每個步驟的細節（這是 Pwn2Own 後常見的適度披露慣例）。上面的步驟 4 是推測——幾乎所有現代 hypervisor 逃逸都需要 infoleak 步驟，但 writeup 可能省略了它，或者他們找到了不需要 infoleak 就能精確覆蓋的方法（例如已知偏移 + 確定性 heap 佈局）。

### Host Code Exec 後的落地（據公開資料）

**writeup 所述**：成功在 Windows 11 host 上取得任意代碼執行。Pwn2Own 的評分標準是「彈出 calc（計算機）」——目的是展示任意代碼執行能力，不是真實攻擊鏈的終點。

落地過程中需要繞的障礙（**據公開資料，Windows 11 VMware Workstation 的標準設定**）：
- **ASLR**：vmx.exe 和系統 DLL 每次啟動位址都變——需要事先 infoleak
- **DEP/NX**：堆積上的 shellcode 不可執行——需要 ROP 或 JOP
- **CFG（Control Flow Guard）**：間接呼叫的目標必須是合法 CFG target——需要選擇 CFG-compatible 的 pivot 目標

**推測**：Synacktiv 使用了 ROP chain 配合 CFG-friendly 的 gadget，而不是直接跳 shellcode。這是 Windows 上的標準做法。

---

## 次案例：Keen Lab 2018 多洞鏈

### 背景

- **據公開資料**：Tencent Keen Security Lab 的 Pwn2Own 2017 VMware Workstation 逃逸。
- **公開 writeup**：https://keenlab.tencent.com/en/2018/04/23/A-bunch-of-Red-Pills-VMware-Escapes/
- **描述多個漏洞**，涉及 SVGA GPU emulation 和 mks（Multi-Client Server）通訊通道

這個案例和 Synacktiv 2025 形成對比的關鍵在於：**攻擊面選擇不同**（SVGA vs PVSCSI）、**時代不同**（2017 vs 2025 的 mitigation 差異）、**洞的性質不同**（多個不同類型的 bug 組成 chain）。

### SVGA 路徑（writeup 所述）

**writeup 所述**：Keen Lab 攻擊了 SVGA（Super VGA）emulation。SVGA 是 VMware 自家的虛擬 GPU，它有一個非常大的攻擊面：

- SVGA FIFO：guest 寫入命令，host 解析執行
- SVGA register I/O：guest 透過 I/O port 控制
- Guest Framebuffer：共享記憶體，guest/host 都能讀寫

**writeup 所述**：洞存在於 SVGA 命令解析的不同階段。具體的 CVE 編號（據公開資料）：
- **CVE-2017-4905**：SVGA emulation 中的 uninitialized memory read，洩漏 host 記憶體內容
- **CVE-2017-4904**：SVGA FIFO 處理中的 type confusion 或無效 pointer 解引
- **CVE-2017-4902**：涉及 SVGA 命令執行路徑中的另一個記憶體安全問題

（**據公開資料，CVE 號與 bug 之間的精確對應關係以 VMware VMSA 為準**）

### Keen Lab 鏈的結構（writeup 所述）

```
Bug 1：infoleak（uninitialized memory / OOB read）
  └─ 透過 SVGA register 操作觸發
  └─ 讀出 host heap 或 vmx.exe 段地址
  ↓
Bug 2：controlled write（SVGA FIFO 命令 OOB 或 type confusion）
  └─ 有了 infoleak 提供的地址基礎
  └─ 精確覆蓋 target object（function pointer 或 vtable）
  ↓
Host code exec
  └─ 觸發覆蓋後的 function pointer 呼叫
  └─ Host shell
```

**writeup 所述的重要觀察**：Keen Lab 在 2017/2018 年的環境（較舊的 Windows / VMware 版本）操作，當時的 heap mitigation 比 2025 輕得多——LFH 隨機化存在，但 side-channel 繞過的複雜度較低，CFG 的覆蓋率也較低。這讓他們的 chain 在利用複雜度上低於 Synacktiv 2025。

### 兩個案例的對比

| 維度 | Synacktiv 2025 | Keen Lab 2018 |
|------|----------------|---------------|
| 攻擊面 | PVSCSI（paravirtual SCSI 控制器） | SVGA（虛擬 GPU emulation） |
| Host OS | Windows 11 | Windows（較舊版本） |
| Bug 類型 | Heap overflow（單一主洞） | 多洞（infoleak + OOB write） |
| LFH 繞過 | 明確使用 side-channel 方法 | 較少說明，可能不需要 |
| CFG 繞過 | 推測需要 CFG-aware ROP | 較舊 VMware，CFG 覆蓋較低 |
| Chain 長度 | 1 個主洞 + heap layout 技術 | 3 個 CVE 組成的鏈 |
| 教育重點 | LFH side-channel、heap groom | Multi-bug chain 的組合思路 |
| 獎金 | $80,000 | Pwn2Own 2017 獎項 |

---

## 底層機制：VMware 逃逸的技術結構

```
VMware Workstation 逃逸路徑圖（閉源視角）

┌─────────────────────────────────────────────────────────┐
│  Guest（VM 內部）                                        │
│  ┌─────────────────────────────────────────────────┐   │
│  │  攻擊者控制的 guest 程式碼                        │   │
│  │  - PVSCSI：寫 SG descriptor + kick doorbell      │   │
│  │  - SVGA：寫 FIFO 命令 + 操作 register            │   │
│  │  - Backdoor：IN/OUT 指令（Ch 33 詳述）           │   │
│  └─────────────────────┬───────────────────────────┘   │
│                         │ 每一次 I/O 觸發 VM-exit        │
└─────────────────────────┼───────────────────────────────┘
                           │ KVM/VMware hypervisor 接手
┌─────────────────────────▼───────────────────────────────┐
│  Host（vmware-vmx.exe userspace process）                │
│  ┌─────────────────────────────────────────────────┐   │
│  │  Device emulation handler（閉源，需逆向）         │   │
│  │  ┌─────────────────────────────────────────┐    │   │
│  │  │  PVSCSI handler                          │    │   │
│  │  │  ├─ 讀 ring buffer（guest 提供）          │    │   │
│  │  │  ├─ 驗證長度？（這裡有洞）               │    │   │  ← CVE-2025-41238
│  │  │  └─ 複製 SG data 進 heap buffer          │    │   │
│  │  └─────────────────────────────────────────┘    │   │
│  │                                                   │   │
│  │  Host Heap（Windows LFH）                         │   │
│  │  ┌────┐ ┌────┐ ┌──────────┐ ┌────┐              │   │
│  │  │ A  │ │ B  │ │  PVSCSI  │ │ C  │  ← overflow  │   │
│  │  │    │ │    │ │  buffer  │→│    │  覆蓋 C      │   │
│  │  └────┘ └────┘ └──────────┘ └────┘              │   │
│  │                                                   │   │
│  │  Target（被覆蓋的 C）                             │   │
│  │  - vtable pointer？                              │   │
│  │  - function pointer？                            │   │
│  │  - struct 內的 callback？                        │   │
│  └─────────────────────────────────────────────────┘   │
│                                                          │
│  Host OS（Windows 11）mitigation                         │
│  - ASLR：需要 infoleak                                   │
│  - LFH 隨機化：需要 side-channel 或大量 spray           │
│  - CFG：需要 CFG-compatible 的 gadget                   │
└─────────────────────────────────────────────────────────┘
```

---

## 讀 writeup 的方法論：五個問題

讀任何一篇 hypervisor 逃逸 writeup，不管目標是 VMware、VirtualBox 還是 Hyper-V，問這五個問題。你能答得出來，你就把 writeup 讀進去了。

### 問題一：攻擊面入口在哪裡，為什麼選它？

不要只記「PVSCSI」這個詞。問的是：**攻擊者如何決定選這個 device，而不是別的？**

答案的維度：
- 逆向工程量（這個 device 的 handler 有多大）
- Guest 輸入的控制性（guest 能控制幾個欄位、每個欄位的值域有多大）
- 歷史漏洞密度（這個 device 或相鄰 device 之前是否出過洞）
- 攻擊者的先備知識（是否有前人的逆向工作可參考）

Synacktiv 選 PVSCSI 而不是 SVGA，可能的原因（**推測**）：2025 年的 SVGA 路徑已經被充分研究和修補；PVSCSI 的研究較少，可能存在未挖掘的洞。這是典型的「非主流攻擊面」策略。

### 問題二：bug 的根因是什麼，在程式碼層次怎麼描述？

「heap overflow in PVSCSI handler」不夠。要能說：

- **缺少哪個邊界檢查**（例如：「SG entry count 沒有和分配的 buffer size 做比對」）
- **輸入路徑是什麼**（guest 從哪個寄存器或哪個 ring buffer field 送入那個值）
- **bug 觸發的前提條件**（需要先做什麼初始化？需要特定的 PVSCSI 操作序列嗎？）

Synacktiv 案例（**writeup 所述的抽象描述**）：SG list 的長度沒有被正確驗證，導致複製操作超出 host 端 heap buffer 邊界。能說到這個粒度，你才算搞懂了 bug。

### 問題三：原語是什麼，怎麼從 bug 構造出來？

Bug 是漏洞，primitive（原語）是攻擊者手上的武器。它們不一樣。

例如：
- Bug：heap overflow（可以在 buffer 之後寫入）
- Primitive：OOB write（能控制偏移和資料的受控越界寫入）

從 bug 到 primitive 的距離，有時很近（bug 本身就是很乾淨的任意寫），有時很遠（overflow 只能在有限範圍內、只能寫有限值）。

讀 writeup 時，問：**攻擊者做了什麼操作，把 bug 轉換成了什麼 primitive？** 中間有沒有借助其他機制（例如 heap groom、先分配特定大小的物件）？

### 問題四：如何繞過 host 端 mitigation？

這是最容易被跳過、卻最有通用價值的部分。

mitigation 的種類：
- **ASLR**：如何取得 vmx.exe base？如何取得 heap 指標？通常需要 infoleak。
- **Heap 隨機化（LFH）**：如何讓目標物件落在已知位置？spray、side-channel、或確定性佈局？
- **NX/DEP**：如何不執行 heap 上的資料？通常需要 ROP chain。
- **CFG**：如何找到合法的間接 call target？

Synacktiv 2025 明確花了篇幅描述 LFH side-channel，這表示這是他們花最多時間的環節之一。**能繞 LFH + CFG 的攻擊者，才是真正有 Windows heap exploit 能力的人。**

### 問題五：整個 chain 的可靠性如何，為什麼？

Pwn2Own 要求現場一次成功（或有限次嘗試），這意味著 chain 的可靠性必須很高。

影響可靠性的因素：
- Heap spray 的覆蓋率夠不夠（spray 越大越可靠，但觸發越多問題）
- Side-channel 繞過的準確度（多少情況下能成功識別 heap 狀態）
- Race condition（有沒有 timing-sensitive 的步驟？race window 多大？）

讀 writeup 時，特別注意作者有沒有描述「第一次 Pwn2Own 現場的失敗」或「可靠性調試的過程」。Synacktiv 的標題「On the Clock」（writeup 標題）暗示了時間壓力——Pwn2Own 有時間限制，他們可能在最後才做成。

---

## 從 VMSA Patch Diff 反推洞

這是比讀 writeup 更通用的 1-day 技能。VMware 每次發安全公告（VMSA），就是告訴你他們改了什麼。你 diff 前後兩個版本的 vmx binary，就能看到哪個函式被修改了。

### 方法流程

```
步驟 1：下載兩個版本的 VMware Workstation
  └─ 舊版本（有洞的）和新版本（已修補的）
  └─ 注意：VMware 會保留舊版本下載一段時間，但不保證永遠

步驟 2：提取 vmware-vmx.exe（或 Linux 上的 vmware-vmx）
  └─ Windows：Program Files\VMware\VMware Workstation\vmware-vmx.exe
  └─ Linux：/usr/lib/vmware/bin/vmware-vmx

步驟 3：Binary diff 工具
  └─ diaphora（IDA Pro plugin，最強大的二進位 diff）
  └─ BinDiff（Google，同樣強大）
  └─ 免費替代：radiff2（radare2 的子工具）

步驟 4：識別修改的函式
  └─ Binary diff 會列出「similarity score 下降」或「新增/刪除的 basic block」的函式
  └─ 篩選標準：涉及邊界檢查的函式、新增了 length validation 的函式、新增了早期 return 的函式

步驟 5：逆向修改的函式，理解「修了什麼」
  └─ 找到新增的 if-check：它在驗證什麼值？
  └─ 找到新增的 assert 或 error return：原本的執行路徑到哪裡了？
  └─ 找到修改的 length 計算：原本用哪個值，現在改用什麼？

步驟 6：反推「修之前的漏洞」
  └─ 如果新增了 `if (len > MAX_SG_ENTRIES) return error;`
  └─ 原本就是：len 沒有被驗證，直接被用來做某個計算
  └─ 問題是：那個計算做了什麼？overflow 到了哪裡？
```

### 典型的 diff 特徵（據公開資料的一般觀察）

修補 heap overflow 的 diff 通常長這樣（**此為通用模式，非 CVE-2025-41238 的實際 patch**）：

```c
// 修補前（重建的邏輯，非原始碼）
void pvscsi_process_sg(PVSCSIState *s, SGEntry *entries, uint32_t count) {
    uint8_t *buf = malloc(FIXED_BUF_SIZE);
    for (uint32_t i = 0; i < count; i++) {  // count 來自 guest，未驗證
        memcpy(buf + offset, entries[i].data, entries[i].len);
        offset += entries[i].len;  // offset 可能超過 FIXED_BUF_SIZE
    }
}

// 修補後（新增邊界檢查）
void pvscsi_process_sg(PVSCSIState *s, SGEntry *entries, uint32_t count) {
    if (count > MAX_SG_COUNT) return ERROR;  // 新增的驗證
    uint8_t *buf = malloc(FIXED_BUF_SIZE);
    for (uint32_t i = 0; i < count; i++) {
        if (offset + entries[i].len > FIXED_BUF_SIZE) return ERROR;  // 新增的驗證
        memcpy(buf + offset, entries[i].data, entries[i].len);
        offset += entries[i].len;
    }
}
```

在 binary diff 裡，你看到的是：原本的函式少了幾個 basic block（新增的 early return），多了幾個 compare-and-branch 指令。這讓你定位到「驗證邏輯之前的執行路徑」，也就是原本沒有驗證的路徑——那就是洞。

### VMSA 公告的資訊量

VMware 的 VMSA（例如 VMSA-2025-0018，CVE-2025-41238 的公告）通常只告訴你：
- CVE 號
- CVSS score
- 漏洞類型（「heap overflow」）
- 哪個產品版本受影響、哪個版本修好了

這些資訊對 patch diff 的價值是：**給你版本號，讓你確定要 diff 哪兩個版本**。真正的洞的位置，要自己從 diff 裡找。

---

## 對比與取捨

| 攻擊場景 | 你有什麼 | 主要挑戰 | 推薦工具 |
|---------|----------|---------|---------|
| 讀 writeup 還原方法論 | writeup + CVE 資訊 | 補全作者省略的細節 | 紙筆 + 問五個問題 |
| VMSA patch diff 1-day | 兩個版本的 binary | 從 diff 找到修改點 + 還原 bug | BinDiff / diaphora |
| 純逆向，無 writeup 無 diff | 目標 binary | 海量逆向 + 模糊測試 | IDA + fuzzer + 時間 |
| CTF（有源碼的 QEMU escape） | 完整源碼 | 讀程式碼、找邊界缺失 | grep + gdb |
| 真實研究（新洞） | binary + 研究時間 | 攻擊面選擇 + 模糊測試 + 逆向 | 全套 + 方法論 |

---

## 踩雷集錦

### 1. 「讀懂 writeup 等於能複現」

錯誤直覺：作者寫得這麼清楚，照著步驟做就能打出來。

正確認識：writeup 是事後整理的成功路徑，刻意省略了：失敗的嘗試、調試了幾週的細節、依賴作者逆向出的內部結構（你沒有）、以及版本特定的 heap layout（你的版本可能不同）。「能讀懂 writeup」和「能寫出自己的 exploit」之間是幾十小時的逆向工作。

### 2. 「有 heap overflow 就能打 host code exec」

錯誤直覺：確認了 overflow 之後，後面就是套 ROP chain 的工序。

正確認識：在 Windows LFH 環境下，從「有一個 heap overflow」到「可靠落地 code exec」是最難的環節。Pwn2Own 的參賽者花最多時間不是在找 bug，而是在讓利用可靠。LFH 隨機化意味著你的 overflow 大概率覆蓋到錯誤的物件；需要側信道、精確 spray、或確定性佈局才能讓覆蓋打在正確的地方。

### 3. 「不用 infoleak 也能打 ASLR」

錯誤直覺：heap spray 的範圍夠大，可以覆蓋所有可能的位址，就不需要 infoleak。

正確認識：Windows heap spray 在 vmware-vmx.exe 這個程式上會遇到現實限制——你能分配的記憶體量、能觸發的 PVSCSI 操作次數都有邊界。更根本的問題是：在 CFG 開啟的情況下，你的 spray 目標（例如 vtable）必須指向合法的程式碼位址，而你不知道那個位址在哪，除非先 infoleak。「不需要 infoleak 就繞 ASLR」在 2025 年的 Windows 11 VMware 環境是幾乎不成立的假設。

### 4. 「SVGA 路徑是最好的 VMware 攻擊面」

錯誤直覺：SVGA 是 Pwn2Own 的傳統路徑，出過最多洞，所以研究它最有價值。

正確認識：正因為 SVGA 被打得最徹底，它也被修得最仔細。2025 年 Synacktiv 選擇 PVSCSI 而不是 SVGA，說明的不是「SVGA 更好」，而是「研究得越徹底的攻擊面越難找到新洞」。最好的攻擊面是「複雜度夠高、但被研究得相對少」的。盲目選主流攻擊面不是好策略。

### 5. 「binary diff 會直接告訴你 bug 在哪」

錯誤直覺：我 diff 出來了修改的函式，bug 就是那個函式的某個比較語句。

正確認識：diff 告訴你修改的函式，不告訴你漏洞的完整利用路徑。一個修補可能影響多個函式（例如一個 check 加在 caller，真正越界的在 callee）；有時修補是間接的（改變了一個全域變數的初始值，影響了另一個函式的行為）。從 diff 到「理解洞在哪裡可以做什麼」，你還需要完整逆向那個函式及其 callchain。

---

## 進階：再往深一層

### VMware 的閉源逆向現實

VMware Workstation 的 vmware-vmx.exe 是一個非常大的 binary（Windows 上超過 20MB）。它包含了虛擬機的所有設備模擬邏輯。逆向這個 binary 的挑戰不是「沒有符號」——IDA 的 FLIRT 可以識別出部分標準函式庫函式，有時甚至能識別出 VMware 自己的一部分（透過與以前版本的 diff）——而是「有太多函式，你不知道從哪裡開始」。

實際的逆向工作流（**據公開資料，資安研究者常用方法**）：
1. 從 guest 端入手：找「vmx 一定會處理的 I/O 操作」對應的 host 端 handler
2. 用 `IN`/`OUT` 指令（對 VMware backdoor port 0x5658）或 PVSCSI doorbell 觸發，然後在 vmx 上追 PC 落在哪裡
3. 從那個點出發向外讀，逐漸建立對 device 狀態機的理解
4. 尋找「guest 提供的值直接被用來做大小計算的地方」

### LFH 的深層機制

Windows LFH 的隨機化（據公開資料的 Windows 記憶體管理文件）：

LFH 把同一個 size class 的 chunk 分成 UserBlocks（一個連續的大記憶體塊），UserBlocks 裡的 slot 在分配時被打亂。具體地說：每次分配，LFH 從一個 `FreeEntryOffset` bitmap 裡隨機選一個未使用的 slot，而這個 bitmap 的初始化是有部分隨機性的。

Side-channel 繞過 LFH 的一種思路（**推測，基於公開的 Windows heap 研究**）：反覆分配和釋放特定大小的物件，觀察分配成功/失敗的時間，推斷目前哪些 slot 是空閒的。如果你能觸發足夠多的 PVSCSI 操作（每個操作導致不同大小的 heap 分配），你能建立一個關於 heap 狀態的粗略模型。

這不是精確的——它給你的是「heap 大概是什麼狀態」而不是「第 N 個 slot 是空的」。但在 Pwn2Own 實操中，「大概」加上多次重試的容錯設計，足夠讓攻擊者在有限嘗試次數內成功。

### CFG 繞過的現代技術

Windows CFG 在 vmware-vmx.exe 上的效果（**據公開資料**）：CFG 保護間接 call（`call [rax]`、`call [rbp+offset]`），要求目的地址必須是「CFG valid target」——即編譯器在 build time 記錄的合法 call 目標。

繞 CFG 的幾個路徑（**據公開安全研究**）：
1. **用 CFG-valid gadget 做 stack pivot**：vmx.exe 本身的函式入口都是 CFG-valid 的，某些可以被用來做控制流重新導向
2. **覆蓋 non-CFG-protected 指標**：並非所有間接呼叫都受 CFG 保護，特別是跨模組呼叫或某些條件下的 callback
3. **JOP（Jump-Oriented Programming）**：用 `jmp [rax]` 代替 `call [rax]`，部分版本的 CFG 對 indirect jump 的保護弱於 indirect call

Synacktiv 在 writeup 裡沒有詳細描述他們的 CFG 繞過方法（適度披露），這是合理的——CFG bypass 技術是最有商業價值的部分，不會在 Pwn2Own 後完全公開。

---

## 動手練習

**前提說明**：本節的練習不涉及在真實 VMware Workstation 上執行 exploit——那需要閉源逆向和可能的 ToS 問題。練習目標是訓練「讀 writeup + patch diff」的方法論，在你已有源碼的 QEMU 環境上做對應的思維遷移。

### 練習 1：用五個問題框架讀 Synacktiv writeup

閱讀 https://www.synacktiv.com/en/publications/on-the-clock-escaping-vmware-workstation-at-pwn2own-berlin-2025

對每個問題，寫出你能從 writeup 裡找到的答案，以及 writeup 沒有說清楚的部分：

1. 攻擊面入口在哪裡？攻擊者為什麼選它？
2. Bug 的根因是什麼？哪個欄位沒有被驗證？
3. 原語是什麼？heap overflow 的「可控性」如何？
4. 如何繞 LFH 隨機化？side-channel 的機制是什麼？
5. Chain 的可靠性？現場成功了幾次嘗試？

### 練習 2：模擬 patch diff 流程（用 QEMU CVE）

選擇 QEMU 的一個有公開 patch 的 CVE（建議：CVE-2015-3456 VENOM，因為有完整的修補 commit）。

用 `git diff <before_commit> <after_commit> -- hw/block/fdc.c` 看修補內容。

問：
- 修補在哪個函式裡？新增了什麼 check？
- 如果你不看 CVE 描述，只看這個 diff，你能推斷出「沒修之前的執行路徑是什麼」嗎？
- 對應到 VMware VMSA 的場景：如果你只有二進位 diff，你還能做出同樣的推斷嗎？差距在哪裡？

### 練習 3：PVSCSI 攻擊面研究（概念層次）

在 QEMU 的 `hw/scsi/` 目錄下找到 `pvscsi.c`（QEMU 有 PVSCSI 的開源實作）。

閱讀 `pvscsi_ring_pop_req_descr()`、`pvscsi_process_io()` 等函式。

問：
- SG（scatter-gather）list 的處理在哪裡？有哪些邊界相關的計算？
- QEMU 的開源 PVSCSI 實作和 VMware 閉源實作可能有哪些共同的邊界問題模式？
- 如果這個 QEMU 實作也存在類似 CVE-2025-41238 的問題，patch 大概會長什麼樣？

（注意：這是用開源代碼類比理解閉源 bug 模式，不是要你找 QEMU 0-day）

### 練習 4：LFH vs glibc ptmalloc 對比

研究 Windows LFH 和 glibc ptmalloc 的隨機化機制。

LFH 的隨機化：slot 選擇有部分隨機性，但 heap spray 到一定數量可以強制對齊。

ptmalloc 的「隨機化」：實際上沒有 slot 隨機化，但 ASLR 讓 heap 的起始位址隨機。

問：
- 為什麼 Ch 14 介紹的 QEMU heap groom 技術（基於 ptmalloc）不能直接移植到 VMware on Windows（LFH）？
- Synacktiv 使用 side-channel 的必要性，在 Linux QEMU 上有對應場景嗎？

---

## 本章重點整理

1. **閉源 hypervisor 的兩條研究路徑**：writeup 給你前人的方法論；VMSA patch diff 給你新洞的線索。兩者配合，才能形成持續的研究能力。

2. **Synacktiv 2025 的技術核心**（writeup 所述）：PVSCSI SG list 的長度驗證不足 → heap overflow → Windows LFH 佈局 + side-channel 繞過隨機化 → host code exec。攻擊面選擇（PVSCSI 而非 SVGA）本身就是非顯然的決策。

3. **Keen Lab 2018 的對比價值**：展示了多洞鏈的組合方式（infoleak + OOB write），以及在較早年代（LFH mitigation 較輕）的攻擊複雜度差異。

4. **讀 writeup 的五個問題**：入口選擇、bug 根因、原語構造、mitigation 繞過、chain 可靠性。能答出這五個，你才算把 writeup 真正讀進去。

5. **VMSA patch diff 流程**：下兩個版本 → binary diff（diaphora/BinDiff）→ 找修改函式 → 逆向「新增的 check 驗證了什麼」→ 反推「沒有 check 時的執行路徑」→ 理解漏洞。

6. **VMware 逃逸幾乎都需要多 bug 鏈**：因為 Windows LFH 隨機化、ASLR、CFG 的存在，單洞落地幾乎不可能。infoleak + OOB write + host layout 是標準組合，LFH side-channel 是 2020 年代後的必備技能。

7. **誠實認識「未實測」的邊界**：本章所有 VMware 技術細節都是二手知識（writeup + 公開研究）。真正在 vmware-vmx.exe 上動手，你需要的不是讀課程，而是拿起 IDA 和 WinDbg。

---

## 自我檢核

- [ ] 我能說出 Synacktiv 2025 逃逸的三個主要技術環節（攻擊面、bug 類型、host 端挑戰）
- [ ] 我能用五個問題框架，把 writeup 的關鍵資訊結構化提取出來
- [ ] 我理解為什麼 LFH 隨機化讓 heap overflow 的可靠利用比 ptmalloc 環境更難
- [ ] 我能描述 VMSA patch diff 的六步工作流
- [ ] 我能說出 Synacktiv 2025 和 Keen Lab 2018 在攻擊面和鏈結構上的主要差異
- [ ] 我知道 CFG 保護什麼、不保護什麼，以及繞過它的三個方向
- [ ] 我清楚本章哪些技術細節是 writeup 明確所述的，哪些是推測

---

## 延伸閱讀

1. **Synacktiv「On the Clock」writeup（2025）**
   - 連結：https://www.synacktiv.com/en/publications/on-the-clock-escaping-vmware-workstation-at-pwn2own-berlin-2025
   - 讀哪裡：完整讀，重點是 LFH side-channel 段落
   - 學什麼：PVSCSI 攻擊面的完整利用路徑；Windows LFH 環境下的 heap 佈局技術
   - 關聯：本章的主案例依據；讀完本章後再讀，應該能補全 writeup 省略的背景知識

2. **Keen Security Lab 多洞鏈 writeup（2018）**
   - 連結：https://keenlab.tencent.com/en/2018/04/23/A-bunch-of-Red-Pills-VMware-Escapes/
   - 讀哪裡：SVGA 攻擊面段落，以及 infoleak bug 的描述
   - 學什麼：multi-bug chain 的組合方式；2017 年 VMware 環境下的利用複雜度
   - 關聯：與 Synacktiv 案例對比，理解攻擊技術在 7 年間的演進

3. **「Windows 10 Segment Heap Internals」（BlackHat 2016，Mark Vincent Yason）**
   - 連結：https://github.com/nicowillis/papers/blob/master/windows-10-segment-heap-internals-en.pdf（或 BlackHat 官方議程搜索）
   - 讀哪裡：LFH bucket 隨機化一節
   - 學什麼：LFH 的記憶體佈局與隨機化機制的底層細節，是理解「為什麼 LFH 讓 heap exploit 更難」的一手資料
   - 關聯：本章踩雷集錦第 2 條和第 3 條的技術基礎

4. **VMware VMSA 公告列表**
   - 連結：https://www.vmware.com/security/advisories/
   - 讀哪裡：選一個近期的 Workstation CVE，閱讀受影響版本和修補版本的資訊
   - 學什麼：VMSA 給你什麼資訊、不給你什麼資訊；如何利用 VMSA 定位 patch diff 的兩個版本
   - 關聯：本章「從 VMSA patch diff 反推洞」段落的實際起點

5. **「A Survey of Research on VM-Escape Vulnerabilities」類型的學術論文**
   - 建議搜索：`site:usenix.org vmware escape` 或 `site:ieee.org hypervisor escape survey`
   - 讀哪裡：VMware 相關的 CVE 統計段落，以及「攻擊面分類」一節
   - 學什麼：從統計角度看 VMware Workstation 歷史上哪些 device 的 bug 密度最高——這是攻擊面選擇的系統性依據
   - 關聯：把本章的個案研究放進 VMware 漏洞歷史的全景中

---

本章是 Part 6 VMware 主線的收尾。我們從架構（Ch 32）、通訊通道（Ch 33）、SVGA 攻擊面（Ch 34），走到了實際的 Pwn2Own 逃逸案例導讀。

接下來 Part 7 轉向「逃逸之後你面對什麼」——不是空曠的 host，而是 seccomp、sVirt、namespace 等多層防禦。逃逸只是入場券；在有縱深防禦的真實環境裡穿透每一層，才是完整攻擊能力的全貌。

→ [Ch 36](./36-host-mitigations.md)
