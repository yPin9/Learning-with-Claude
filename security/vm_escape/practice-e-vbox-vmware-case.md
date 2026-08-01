# 練習 E — VirtualBox E1000 vs VMware Cloudburst：逃逸案例深挖與對比分析

> **目標**：選一個案例深挖（還原 bug root cause、畫出完整利用鏈），另一個做對比；最後能說清楚開源 vs 閉源目標對研究者造成的本質差異。

---

## 背景與動機

Part 5 和 Part 6 各給了一顆真實的目標：VirtualBox（開源，C++，可讀源碼）和 VMware Workstation（閉源，vmx binary，只能逆向）。兩者都出過嚴重的逃逸漏洞，但研究者打它們的體驗截然不同。

這個練習不要求你從零寫出能在 Pwn2Own 上台的 exploit——閉源環境 + 複雜的內部狀態讓這件事在教材情境裡不現實。我們要做的是：**把一個公開的 writeup 讀到你能在不看筆記的情況下，把整條利用鏈畫出來，並且解釋每一步如果少掉會在哪裡斷**。這才是「理解一個 VM escape」真正的驗收標準。

兩個案例：

- **案例 A：VirtualBox E1000 — CVE-2019-2525（OOB Read/Write）**
  - VirtualBox 開源，`src/VBox/Devices/Network/DevE1000.cpp` 可直接讀。
  - Corentin Bayet 和 Bruno Pujos（Synacktiv）在 Pwn2Own Vancouver 2019 用這個洞（加上 CVE-2019-2526）成功逃逸，之後有公開 writeup 和 commit diff。
  - 攻擊面：E1000（Intel 8254x）虛擬網卡的描述符（descriptor）處理。

- **案例 B：VMware SVGA Cloudburst — CVE-2009-1244**
  - Kostya Kortchinsky（Immunity Security）在 Black Hat USA 2009 發表的完整論文。
  - 這是史上最早被公開文件化的完整 VMware guest→host 逃逸，方法論的敘述比後來許多 writeup 都完整。
  - 攻擊面：VMware 的 SVGA II 虛擬顯卡，透過 FIFO command 機制觸發 heap corruption。

**選哪個深挖？** 如果你對網路裝置模擬 + C++ 物件佈局有把握，選 A，開源讓你能直接驗證每一行推論。如果你更想練「只靠論文/binary 還原一條利用鏈」這種研究手感，選 B，它是方法論課，不是「有原始碼就能查」的那種。

---

## 前置知識

- VirtualBox E1000 descriptor 的概念：E1000 網卡的 TX/RX descriptor ring（Ch 30 介紹過）。你不需要背完整的 8254x 規格，但要知道「descriptor 是 guest 在 ring buffer 裡放的、描述一個封包傳送/接收任務的結構」。
- heap 原語：OOB read 洩漏位址 + OOB write 覆蓋 vtable/function pointer 的思路（Ch 16–21 主線）。
- VMware SVGA II FIFO：guest 透過 MMIO 映射的 FIFO ring 把繪圖命令丟給 host 端的 vmx 行程處理（Ch 34 介紹過）。
- 開源 vs 逆向的基本工具差距：有原始碼 = diff 可以直接定位洞；閉源 = 只能靠論文描述 + 二進位分析。

---

## 任務規格

### 案例資源清單

**案例 A（VirtualBox E1000）：**
- CVE advisory：[CVE-2019-2525](https://cve.mitre.org/cgi-bin/cvename.cgi?name=CVE-2019-2525)、[CVE-2019-2526](https://cve.mitre.org/cgi-bin/cvename.cgi?name=CVE-2019-2526)
- Synacktiv writeup：「VirtualBox E1000 guest-to-host escape」（Corentin Bayet & Bruno Pujos，2019 年 Synacktiv 部落格）
- VirtualBox 修補 commit：在 VirtualBox 官方 svn/git（`DevE1000.cpp` 的變更 diff，對照 5.2.28 → 6.0.6 之間的修補）
- 直接讀原始碼：`src/VBox/Devices/Network/DevE1000.cpp`（VirtualBox 5.2.x 或從官方 Oracle 軟體庫取得舊版）

**案例 B（VMware Cloudburst）：**
- 論文 PDF：`https://blackhat.com/presentations/bh-usa-09/KORTCHINSKY/BHUSA09-Kortchinsky-Cloudburst-PAPER.pdf`
- 補充：Immunity CANVAS 2009 release notes（提及 Cloudburst 模組）
- VMware 補丁 advisory：VMware Security Advisory VMSA-2009-0006
- 注意：VMware 閉源，你只能從論文描述推，無法直接讀 vmx binary 的 C code

### 分析報告要求（五個維度，每個維度都要回答）

**1. 攻擊面入口**
- 哪個虛擬 device？哪個命令 / 操作 / code path？
- guest 怎麼觸達這個入口？（透過 PIO？MMIO？DMA？描述符？FIFO command？）
- 觸發需要什麼前提條件（root？網卡驅動載入？特定 device 型號）？

**2. Bug root cause**
- 漏洞類別（OOB read / OOB write / integer overflow / type confusion / UAF / …）
- 發生在哪個函式 / 哪個結構欄位 / 哪一行邏輯？
- 根本原因：是哪個前提假設錯了？（「guest 不會給超出範圍的長度」？「這個欄位在使用前一定被初始化」？）

**3. 利用鏈（全程 ASCII 圖）**
- 從 bug 觸發到 host 上執行 shellcode，每一步是什麼原語，怎麼從上一步的結果拿到下一步需要的東西。
- 每一步要說：這一步給了我什麼（位址 / 任意讀 / 任意寫 / RIP control）？

**4. 每步的 mitigation 繞過**
- 當時（2009 或 2019）的目標環境有哪些 mitigation（ASLR / NX / stack canary / PIE / heap randomization）？
- 這條鏈怎麼應對？哪一步 infoleak 繞 ASLR？哪一步用了 ROP 繞 NX？

**5. 開源 vs 閉源對研究難度的影響**
- 在「定位洞」這個環節，有原始碼 vs 沒有原始碼的差距體現在哪裡？
- 在「建構利用鏈」這個環節，差距又在哪裡？
- 用一段話具體說明，不是泛泛而論。

---

## 實作步驟

### Step 1：帶著「五問框架」讀 writeup（30–60 分鐘）

拿到 writeup（案例 A 讀 Synacktiv 部落格文章；案例 B 讀 Kortchinsky 論文）之前，先把這五個問題寫在紙上，讀的時候帶著它們找答案：

1. **入口在哪**：guest 的觸發動作是什麼？是寫一個暫存器？送一個特定格式的資料包？下一個 FIFO command？
2. **bug 類型**：OOB、UAF、整數問題，還是類型混淆？用一個詞定性。
3. **觸發條件**：要在 guest 裡有什麼東西（驅動、root 權限、device 配置）才能走到洞？
4. **利用原語**：這個 bug 直接給的是什麼——任意讀？任意寫？任意 free？還是需要配合 heap groom 才能轉換成有用的原語？
5. **mitigation**：2009/2019 的 VMware/VirtualBox host 環境有什麼防禦，writeup 怎麼繞的？

**不要只讀一遍**。先快速掃一遍確認整體結構，第二遍帶著五問仔細讀，在每個關鍵轉折點停下來問：「如果這一步失敗，接下來能繼續嗎？」

### Step 2：定位攻擊面（15–30 分鐘，方法依案例不同）

**案例 A（E1000）：**
去讀 `DevE1000.cpp`，找 `e1kHandleRxPacket` 或 `e1kLocateTxPacket` 附近的描述符處理邏輯。具體任務：
- 找到處理 TX descriptor 的主迴圈，確認哪個欄位是 guest 完全控制的
- 找到長度計算的那幾行，確認溢位怎麼發生的
- 找到修補 commit 的 diff，確認修補前後差在哪裡（一兩行的 `if` 增刪）

```
# 下載 VirtualBox 5.2.x 源碼（Oracle 軟體庫或 GitHub 鏡像）
# 搜尋漏洞函式
grep -n "e1kHandleTxDescriptor\|e1kLocateTxPacket\|cbPacket\|cbFragment" \
  src/VBox/Devices/Network/DevE1000.cpp | head -60
```

**案例 B（Cloudburst）：**
VMware 閉源，你做不到「讀原始碼」。你能做的：
- 從論文第 3 節找到 SVGA FIFO 的 command 格式描述
- 確認攻擊用的是哪個 FIFO command（論文有給 command ID / name）
- 在 VMware Workstation binary（vmx）上用 `strings` 找相關字串，確認 vmx 行程確實在處理這個命令（可選，需要 Linux 版 VMware）
- 這個定位過程就是你在「閉源目標」上能做到的最大程度

### Step 3：畫利用鏈 ASCII 圖（這是核心產出，不能省）

格式要求：
- 每個箭頭對應一個步驟
- 箭頭上面標「這一步的動作」，箭頭下面標「拿到的結果」
- 最後一個框是「host 上執行任意程式碼」

E1000 鏈的大框架（你要填細節）：

```
guest 送出精心構造的 TX descriptor ring
         │
         │ 觸發 e1kHandleTxDescriptor 裡的 ??? 計算錯誤
         ▼
    [OOB read/write in VMM heap]
         │
         │ 洩漏 ???（VirtualBox 哪個 struct 的哪個欄位）
         ▼
    [infoleak: VirtualBox .text base / heap layout]
         │
         │ 利用 CVE-2019-2526 的 ??? 做任意寫
         ▼
    [覆蓋 ??? function pointer / vtable entry]
         │
         │ 觸發 ???（什麼操作讓 host 呼叫這個 pointer）
         ▼
    [RIP 控制 → ROP chain → system() / shellcode]
         │
         ▼
    host shell（逃逸成功）
```

Cloudburst 鏈的大框架：

```
guest 送出構造的 SVGA FIFO command（command ID: ???）
         │
         │ vmx 行程的 FIFO handler 信任了 guest 給的 ???
         ▼
    [heap overflow in vmx process heap]
         │
         │ 覆蓋緊鄰 heap chunk 的 ??? 欄位
         ▼
    [取得任意寫原語 / 控制 ???]
         │
         │ 2009 年：ASLR 狀況如何？vmx 是 PIE 嗎？
         ▼
    [RIP 控制 → shellcode 在 heap 上執行]
         │
         ▼
    host 上執行（逃逸成功）
```

**把你的圖裡所有 `???` 都填上正確答案**。這是這個練習最重要的一步。

### Step 4：斷鏈測試——驗證你真的理解了

把整條鏈印出來（或畫在紙上），然後逐步問：

> 「如果 Step N 不能做到，接下來能繼續嗎？」

例：
- 如果沒有 infoleak，後面的任意寫還有用嗎？（答案：通常沒有，因為不知道寫到哪裡才能覆蓋 function pointer）
- 如果 heap spray 失敗，能繼續嗎？（取決於 bug 的穩定性）
- 如果 vmx 是 PIE 且 ASLR 開著，Cloudburst 的利用路徑還 work 嗎？為什麼 2009 年的 exploit 可以不管這個？

**每個「如果少了這一步」都要能回答**，這是驗收標準，不是可選練習。

### Step 5：對比分析（500 字以內，但要具體）

寫下這段文字，你的分析報告的最後一節：

> **開源（VirtualBox）vs 閉源（VMware）：對研究者的實際影響**

要包含：
- 「定位洞」：有 diff 可以看 vs 只有 advisory 描述，差距有多大？
- 「理解 root cause」：有 `.cpp` 可以讀 vs 只能靠論文推測 C 行為，差距在哪裡？
- 「建構 exploit」：可以精確知道 struct 佈局 vs 要靠 binary 逆向確認，什麼工具幫你彌補這個差距？
- 「穩定性與可重現性」：開源環境能在相同版本重現；閉源你的環境可能和目標環境微妙不同，這對 exploit reliability 有什麼影響？

---

## 如果卡住了

1. **找不到 E1000 的漏洞函式**：從修補 commit 反找。去 VirtualBox 官方 bug tracker 搜 CVE-2019-2525，或在 VirtualBox git 歷史裡搜 `e1000` + `2019`。Diff 裡改動的那幾行，就是洞的位置。你不用先讀完整個 `DevE1000.cpp`，從 diff 的改動點往外擴展讀即可。

2. **Cloudburst 論文的利用鏈描述不夠具體**：論文的重點是方法論，不是 step-by-step exploit 說明書。遇到模糊的地方，試著用 Ch 14（QEMU heap）學過的 heap chunk 操作概念去填補——VMware 的 vmx heap 行為模型和 glibc malloc 類似。2009 年的 VMware 環境也沒有現代的 heap metadata integrity check，heap overflow 覆蓋下一個 chunk 的 header 這條路比現在容易走得多。

3. **ASCII 鏈圖不知道怎麼填**：先把你確定的填上，不確定的用 `[?]` 標記，然後帶著 `[?]` 回去讀 writeup 的對應段落。如果讀完還是不確定，這本身就是你的分析結論：「writeup 在這個環節的描述不夠完整，我能推斷到 X，但 X 之後到 Y 的跳躍我無法從公開資料重建」——這種誠實比硬掰一個你不確定的答案更有價值。

---

<details>
<summary>參考分析（展開前先自己做）</summary>

### 案例 A：VirtualBox E1000 — CVE-2019-2525 + CVE-2019-2526

#### 攻擊面入口

E1000 是 VirtualBox 預設模擬的 Intel 8254x 千兆網卡（也是 VirtualBox 最常用的虛擬網卡型號之一）。guest 的網路驅動（Linux 的 `e1000.ko`，或 Windows 的 `e1000.sys`）透過 MMIO 和 PIO 操控這張虛擬網卡。

傳送封包的流程：
1. guest 驅動把「TX descriptor」寫入 descriptor ring（一塊 guest 物理記憶體）
2. 透過 PIO 寫 tail pointer 暫存器（`TDT`，Transmit Descriptor Tail），通知 host 端「有新的 descriptor 可以處理了」
3. VirtualBox 的 `DevE1000.cpp` 讀這些 descriptor，按照裡面記錄的 GPA（guest 物理位址）和長度，把 guest 記憶體裡的封包資料拉到 host 端 buffer 裡準備傳送

觸發前提：
- guest 裡要有 E1000 網卡（VirtualBox 預設配置就有）
- guest 要有 E1000 驅動載入（Linux/Windows 都有內建，不需要特殊安裝）
- guest 不需要 root 就能構造 raw TX descriptor——但在 Linux 上要送原始 socket 封包通常需要 `CAP_NET_RAW`（root 或特定 capability）

#### Bug Root Cause

**CVE-2019-2525**：`e1kHandleTxDescriptor` 在處理 TCP Segmentation Offload（TSO）描述符時，累加的 `cbPayload`（payload 長度）沒有做上限檢查。guest 可以送一個 context descriptor，裡面宣告一個非常大的 MSS（Maximum Segment Size），導致後續計算 buffer 大小時整數計算出的值遠小於實際需要的空間。

根本假設錯誤：「guest 驅動給的 MSS / payload 長度不會讓累計長度超過 MAX_MTU」。這個假設在真實驅動裡成立，在惡意 guest 裡當然不成立。

**CVE-2019-2526**：TX ring 描述符的「next pointer」欄位的處理，讓攻擊者能控制 VirtualBox 從哪個 GPA 讀資料，組合起來製造 heap OOB read。

兩個 CVE 配合使用：2525 給任意 OOB write 的路徑，2526 給 infoleak（OOB read）。

#### 利用鏈

```
Step 0：環境準備
guest 驅動載入 e1000，VirtualBox E1000 device 初始化完成
heap 上有 E1000TxRing struct + 相鄰的 VirtualBox 物件

Step 1：OOB Read（CVE-2019-2526）
構造特殊的 TX descriptor chain，讓 DevE1000.cpp 的
e1kHandleTxDescriptor 在讀取 data 時越界讀到相鄰 heap chunk
         │
         │ 洩漏相鄰物件的欄位（包含 VirtualBox .text/heap 指標）
         ▼
取得：VirtualBox PIE base + heap layout

Step 2：OOB Write（CVE-2019-2525）
利用 TSO context descriptor 觸發長度計算錯誤，
使實際 copy 長度超過分配的 buffer
         │
         │ 覆蓋相鄰 heap chunk（目標：某個帶 function pointer 的物件）
         ▼
取得：可控的 function pointer 覆蓋

Step 3：觸發覆蓋後的 function pointer
送出下一個操作，讓 VirtualBox 呼叫剛才被覆蓋的 callback
（例如：timer callback、device MMIO handler、vtable 方法）
         │
         ▼
RIP 指向攻擊者控制的位址

Step 4：ROP → code exec
VirtualBox 行程有 NX，shellcode 不能直接跳。
用 Step 1 洩漏的 .text base 計算 ROP gadget 位址。
ROP chain 呼叫 system("/bin/bash") 或 execve
         │
         ▼
host 上的 VirtualBox 行程執行任意命令 → 逃逸成功
```

**Synacktiv 實際做法的補充說明**（根據公開 writeup）：Bayet 和 Pujos 在 Pwn2Own 上展示的是兩個洞組合，但他們公開的技術細節集中在漏洞分析，完整 exploit 沒有公開釋出。上面的鏈是根據 writeup 描述的合理重建，某些細節（具體哪個 callback 被覆蓋、ROP gadget 怎麼選）需要自己從源碼裡確認。

#### Mitigation 狀況（2019 年 VirtualBox host）

- ASLR：開著。OOB read（infoleak）是必要的，不 leak 就無法計算目標位址。
- NX：開著。ROP 是必要的，不能直接跳 shellcode。
- PIE：VirtualBox 主 binary 是 PIE。需要 leak `.text` base。
- Stack canary：棧保護開著。這條鏈走的是 heap，不是 stack overflow，canary 不相關。
- Heap ASLR：glibc 的 heap base 有隨機化。需要 leak heap 位址確定目標物件位置。

總結：這條鏈需要兩個 CVE 互補才能繞過所有 mitigation——這是現代 exploit 的常態。

---

### 案例 B：VMware SVGA Cloudburst — CVE-2009-1244

#### 攻擊面入口

VMware Workstation 的虛擬顯卡是「VMware SVGA II」，它是 VMware 自己設計的協定，不是模擬某個真實 GPU。guest 透過兩個機制與它通訊：

1. **MMIO 暫存器**（`SVGA_REG_*`）：透過 PIO 讀寫，設定解析度、啟用 framebuffer 等
2. **FIFO ring buffer**：一塊 guest 可寫的記憶體，裡面放著繪圖命令（`SVGA_CMD_*`）。guest 的顯示驅動把命令塞進 FIFO，更新 `SVGA_REG_FIFO_NEXT_CMD`，host 端 vmx 行程輪詢這個 pointer，取出命令執行。

觸發前提：
- guest 要安裝 VMware Tools（內含 SVGA 驅動）—— 但 Kortchinsky 的論文指出，即使沒有 VMware Tools，guest 也能用 `outb`/`outl` 自行構造 FIFO 命令
- guest 不需要 root（論文明確提到這是一個非特權 guest 行程可以觸發的洞）

#### Bug Root Cause

`SVGA_CMD_RECT_COPY`（矩形複製命令，用於 2D 加速）的 handler 在計算複製區域時，對來源矩形和目標矩形的邊界做了不充分的驗證。

具體問題（根據 Kortchinsky 論文的描述）：
- handler 信任 guest 提供的 `x`, `y`, `width`, `height` 欄位
- 沒有正確驗證 `srcX + width` 和 `srcY + height` 不超過 framebuffer 邊界
- 導致 vmx 行程在 host 端的 framebuffer buffer 上做了越界的 `memcpy`，把 framebuffer 之外的 heap 資料複製到了攻擊者選定的位置（heap overflow）

根本假設錯誤：「SVGA 驅動送來的矩形座標在 framebuffer 範圍內」。

#### 利用鏈

```
Step 0：環境準備（2009 年 VMware Workstation on Windows XP/Vista）
- guest 透過 PIO 初始化 SVGA FIFO
- vmx 行程在 host 上以普通使用者身分執行
- 2009 年 Windows 的 heap：無現代 metadata integrity check
- ASLR：Windows Vista 有，XP 沒有
  Kortchinsky 的 exploit 針對 XP（沒有 ASLR）做示範

Step 1：Heap Spray 準備
在 vmx 行程的 heap 上預先噴射大量包含 shellcode 指標的物件，
讓 heap 的特定區域充滿已知內容
（在 XP 上：heap 位址是可預測的，不需要 infoleak）
         │
         ▼
vmx heap 上的目標區域充滿偽造的 heap chunk header

Step 2：觸發 SVGA_CMD_RECT_COPY OOB
guest 送出精心構造的 RECT_COPY 命令，
srcX/srcY/width/height 讓 memcpy 的目標超出 framebuffer buffer
         │
         │ 把 framebuffer 外的 heap chunk header 覆蓋成攻擊者控制的值
         ▼
heap 上某個 free chunk 的 fwd/bck pointer 被覆蓋（unlink 攻擊）

Step 3：Heap Unlink → 任意寫
下一次 heap 釋放操作觸發 unlink，
把攻擊者控制的值寫到攻擊者選定的位址
（Windows XP heap unlink 在 2009 年仍然可以利用）
         │
         │ 覆蓋某個 function pointer（例如 vmx 的 timer handler、
         │   或 SEH handler）
         ▼
取得 RIP 控制（或 SEH → RIP）

Step 4：Shellcode 執行
2009 年 Windows XP 沒有強制 NX（DEP 要手動開），
heap 上的 shellcode 可以直接執行
         │
         ▼
以 vmx 行程的權限在 host 上執行任意程式碼 → 逃逸成功
```

#### Mitigation 狀況（2009 年）

- ASLR：XP 沒有，Vista 有但未強制全覆蓋。論文 exploit 針對 XP，heap 位址可靠預測。
- NX/DEP：XP 預設不開 DEP，heap 上的 shellcode 可執行。
- Heap Integrity：XP 的 heap 沒有現代 glibc 的 unlink check，`unlink` 攻擊直接可用。
- Stack canary：棧保護未普及。

這解釋了為什麼 2009 年的逃逸比 2019 年的簡單得多——不需要 infoleak（heap 位址已知），不需要 ROP（DEP 沒開），不需要兩個 CVE（一個 OOB write 就夠）。

---

### 開源 vs 閉源的本質差距

**定位洞：**

VirtualBox：修補 commit 公開後，「洞在哪」是一個有確定答案的問題。你去看 diff，找到 `if (cbPayload > MAX_PAYLOAD)` 這行新增的 check，往上看三行就是洞。整個定位過程可以在一小時內完成。

VMware：advisory 說「SVGA FIFO 的矩形命令有邊界問題」，但沒有 source code 讓你核實。你只知道「某個命令的某個欄位沒被驗證」——但到底是哪個計算路徑、邊界條件的確切形式是什麼，都需要靠逆向工程（IDA/Ghidra 反編譯 vmx binary）才能確認。Kortchinsky 的論文省略了這部分（他當時直接有目標 binary 可以分析），後來的研究者要重現這個分析要花多出數倍的時間。

**建構利用鏈：**

VirtualBox：你可以直接讀 `src/VBox/Devices/Network/DevE1000.cpp` 裡的 struct 定義，知道 `E1KSTATE` 的佈局，算出哪個欄位在 overflow 的範圍內、偏移多少。用 `pahole` 確認，再下 gdb 斷點驗證。每個推論都能在 30 分鐘內確認對錯。

VMware：你必須先用 IDA 把 vmx 的 FIFO handler 反編譯，辨認出哪塊記憶體是 framebuffer buffer、哪個 struct 在它後面。struct 欄位沒有名字，你看到的是 `v12 + 0x18`，要自己給它命名。做一個假設，用動態分析（在 Linux 版 VMware 上 gdb attach vmx）驗證——一個來回花的時間是 VirtualBox 的五到十倍。

**穩定性：**

VirtualBox 的 exploit 能直接在相同版本的 VirtualBox 上重現，因為 struct 佈局完全可從原始碼確認。VMware exploit 對 vmx binary 的版本非常敏感：vmx 一小更新，struct 佈局可能偏移幾個 byte，整個 exploit 就壞掉，要重新做逆向分析。

</details>

---

## 延伸挑戰

**在自編 debug VirtualBox（Ch 29/31 環境）上觸發 E1000 的 crash**

不要求完整 exploit，目標是讓 VirtualBox 行程因為 E1000 的邊界錯誤而 segfault 或觸發 VirtualBox 的 `AssertionFailed`。

**環境確認：**

```bash
# 確認是 Ch 29 建好的 debug VirtualBox（帶 symbol）
# 確認 guest 網卡型號是 Intel PRO/1000 MT Desktop（E1000）
VBoxManage showvminfo <vm-name> | grep "NIC 1"
# 應該看到 Intel PRO/1000 MT Desktop

# 啟動 VirtualBox，attach gdb 到 VBoxHeadless 行程
ps aux | grep VBox
gdb -p <pid>
# 在 e1kHandleTxDescriptor 或 e1kLocateTxPacket 下斷點
b e1kHandleTxDescriptor
```

**觸發步驟：**

1. 在 guest 裡寫一個 C 程式，使用 raw socket 送出畸形 TX descriptor：
   - 建立一個 `AF_PACKET`/`SOCK_RAW` socket
   - 構造一個 TSO context descriptor，把 MSS 設成 0xFFFF
   - 接著送一個 data descriptor，長度也設成接近最大值
   - 發送

2. 觀察 host 端 VirtualBox：
   - 如果你的 VirtualBox 是 5.2.x（漏洞版本），`e1kHandleTxDescriptor` 裡的長度計算會錯
   - 你應該看到 VirtualBox assert 失敗或 segfault
   - gdb 應該在 `e1kHandleTxDescriptor` 內的某個 memcpy / buffer access 附近停下來

3. 記錄 crash 時的：
   - 觸發時 `$rip` 在哪裡（哪個函式 + 偏移）
   - 崩潰原因（SIGSEGV？address? access type?）
   - backtrace（`bt` 10 層）

**為什麼只要 crash 就夠了：** 完整 exploit 需要精確的 heap groom、兩個 CVE 配合、正確的 ROP gadget，在教材環境裡重現完整鏈需要非常多的環境特定調整。但「能觸發 crash」已經足夠驗證你對 bug 觸發路徑的理解——你知道怎麼讓 VirtualBox 走到有問題的那行程式碼。

---

## 驗收表

**分析品質的最低標準**（這些不是選項，是必要條件）：

- [ ] 你選的主案例，能說清楚「guest 做了什麼動作」觸發了這個 bug（不能只說「送了一個惡意封包」，要說哪個 descriptor type、哪個欄位）
- [ ] Bug root cause：能用一句話說清楚是哪個函式裡的哪個假設錯了，以及「假設錯在哪裡」（不是 CVE 編號，是邏輯）
- [ ] 利用鏈：ASCII 圖裡沒有空白的 `???`，每一步都有具體內容
- [ ] 每一步的「這步給了我什麼」都填了（不是「洩漏了資訊」，是「洩漏了 VirtualBox .text base，讓我能計算 ROP gadget 位址」這種具體度）
- [ ] 斷鏈測試：至少回答了「如果沒有 infoleak 這一步，後面能繼續嗎？為什麼？」
- [ ] 對比分析：說清楚了開源 vs 閉源在「定位洞」和「建構利用鏈」兩個環節各造成什麼具體的研究難度差異（不是泛泛說「閉源比較難」）
- [ ] 你的分析報告誠實標明了哪些是從 writeup 直接讀到的、哪些是你自己推論的（兩者都允許，但要區分）

---

## 自我檢核

閉上報告，純憑記憶回答：

- [ ] E1000 bug 在哪個函式？bug 的類型？（一句話）
- [ ] Cloudburst 用的是哪個 SVGA 命令？為什麼這個命令有漏洞？
- [ ] 2019 年的 E1000 逃逸需要幾個 CVE？各自的角色？
- [ ] 2009 年的 Cloudburst 不需要 infoleak 的原因是什麼？
- [ ] 一個研究者拿到 VMware advisory，他要確認 bug 的確切位置，第一步要做什麼？
- [ ] 「開源讓研究者在 exploit 開發上快五到十倍」這個說法，你能具體舉出兩個環節嗎？

---

→ [Ch 36 — host 端 mitigation：QEMU seccomp、sVirt、namespace](./36-host-mitigations.md)
