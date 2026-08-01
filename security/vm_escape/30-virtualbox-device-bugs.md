# Ch 30 — 經典 device bug：E1000 / AHCI / audio

> **目標**：剖析 VirtualBox E1000、AHCI、HDA 三類裝置模擬的漏洞成因，建立「從 guest 可控欄位追到 OOB write」的完整分析直覺。

> **環境**：VirtualBox 7.0 / x86-64 / Linux 或 Windows host（漏洞分析對象為 5.2.x 原始洞，7.0 用於現代對照）

---

## 為什麼需要這個？

Ch 29 解釋了 PDM、VBoxDD、PDMDEVREG 如何讓 MMIO 寫入轉進裝置模擬程式碼。  
那層架構沒有問題。問題出在裝置模擬本身：複雜的狀態機、giant 的 C 結構、guest 完全控制的 DMA 參數。

每一塊 guest 可見的裝置都有一套「descriptor ring 或 buffer list」，要告訴 host 要 DMA 到哪裡、搬多少位元組。  
這些欄位由 guest 寫入，host 讀取後拿去做 memcpy。  
只要長度欄位沒有妥善驗證，就是 OOB write 的前置條件。

E1000 是這個模式的最壞示範——2018 年 Zelenyuk 做到了 0day 公開、無需 Oracle 授權的完整逃逸。  
AHCI 和 HDA 用同一套思路，只是狀態機的形狀不同。

---

## 先建立直覺

### 裝置模擬的共同結構

任何 VirtualBox 裝置模擬都重複同一個流程：

```
guest 寫 MMIO 暫存器
   ↓
IOM 派給 PDMDEVREG.pfnMMIOWrite
   ↓
裝置狀態機改變（例如「TDT 暫存器更新 = 有新 TX descriptor」）
   ↓
裝置讀取 guest 物理記憶體中的 descriptor（PDMDevHlpPhysRead）
   ↓
按 descriptor 欄位做 DMA 複製或封包處理
```

攻擊者的目標：在第四步讓 descriptor 裡的 addr 和 len 欄位導致複製越界。

### guest 控制哪些欄位？

以三個裝置為例，guest 可完全控制的欄位：

| 裝置 | 可控欄位 | 後果 |
|------|----------|------|
| E1000 | TX descriptor 的 `length`、`addr`、`MSS` | OOB write 到 transmit buffer 之後 |
| AHCI | PRDT entry 的 `DBA`、`DBC` | DMA 寫入邊界問題 |
| HDA | BDLE 的 `u64BufAdr`、`u32BufSize` | BDL 遍歷中的驗證缺失 |

### 整數下溢的抽象模型

```c
// 危險模式：size 由 guest 控制的欄位計算而來
uint32_t remaining = MSS - already_copied;  // MSS 由 context descriptor 設定
// 若 already_copied > MSS，remaining 下溢成 huge positive number
memcpy(dst, src, remaining);  // OOB write
```

這是 CVE-2018-3295 的骨幹。MSS 來自 context descriptor，`already_copied` 來自 data descriptor 長度累加，兩者都由 guest 控制，讓下溢成為可能。

---

## 底層機制：E1000 TX descriptor 處理

### 硬體背景

VirtualBox E1000 模擬 Intel 82540EM NIC，原始碼在：
`src/VBox/Devices/Network/DevE1000.cpp`

82540EM 在 NAT 和 橋接（bridged）模式都是預設網卡。NAT 模式下封包由 VirtualBox 內部 TCP/IP stack 處理，loopback 路徑可繞過部分驗證。

### TX Descriptor Ring 機制

```
guest 記憶體
┌─────────────────────────────────────┐
│  TX Descriptor Ring                  │
│  [desc 0][desc 1][desc 2]...[desc N] │
└─────────────────────────────────────┘
         ↑ 位址由 TDBAL/TDBAH 指定
         ↑ 大小由 TDLEN 指定

MMIO 暫存器（guest 可讀寫）：
  TDBAL = Transmit Descriptor Base Address Low
  TDBAH = Transmit Descriptor Base Address High
  TDLEN = Transmit Descriptor Length（ring 大小，bytes）
  TDH   = Transmit Descriptor Head（host 推進，guest 讀）
  TDT   = Transmit Descriptor Tail（guest 推進，通知 host）
```

guest 想發封包的流程：
1. 在物理記憶體寫好一個或多個 descriptor
2. 將 TDT 寫成新的 tail index（MMIO write）
3. E1000 收到 TDT 更新事件，從 TDH 到 TDT 掃描 descriptor

### 兩種 Descriptor 格式（偽 C，對應 82540EM datasheet）

```c
/* Context Descriptor：設定 offload 參數，必須在 data descriptor 之前 */
struct e1000_context_desc {
    uint8_t   ipcss;     /* IP checksum start */
    uint8_t   ipcso;     /* IP checksum offset */
    uint16_t  ipcse;     /* IP checksum ending */
    uint8_t   tucss;     /* TCP/UDP checksum start */
    uint8_t   tucso;     /* TCP/UDP checksum offset */
    uint16_t  tucse;     /* TCP/UDP checksum ending */
    uint32_t  cmd_and_length; /* 包含 DTYP=0x0（context） */
    uint8_t   status;
    uint8_t   hdr_len;   /* header length，TSO 用 */
    uint16_t  mss;       /* Maximum Segment Size，TSO 用 ← guest 完全控制 */
};

/* Data Descriptor：實際 payload */
struct e1000_data_desc {
    uint64_t  buffer_addr; /* guest 物理位址 ← guest 完全控制 */
    uint16_t  length;      /* ← guest 完全控制，這是漏洞入口 */
    uint8_t   cso;
    uint8_t   cmd;         /* DTYP=0x1（data），TSE bit 開啟 TSO */
    uint8_t   status;
    uint8_t   css;
    uint16_t  special;
};
```

### e1kFallbackAddToFrame() 漏洞

當 `cmd` 欄位的 TSE（TCP Segmentation Enable）位設定，E1000 進入 TSO fallback 路徑。  
原始碼路徑（VirtualBox 5.2.x）：

```
e1kTransmitPending()
  → e1kXmitDesc()
      → e1kXmitAllocBuf()
      → e1kFallbackAddToFrame()   ← 漏洞所在
```

`e1kFallbackAddToFrame()` 虛擬碼（標示漏洞位置）：

```c
// src/VBox/Devices/Network/DevE1000.cpp（5.2.x 版本，簡化）
static int e1kFallbackAddToFrame(PE1KSTATE pThis,
                                  E1KTXDESC *pDesc,
                                  bool fOnWorkerThread)
{
    // pThis->u16TxPktLen：目前 transmit buffer 已累積的長度
    // pDesc->data.cmd.u16Length：這個 data descriptor 宣告的長度 ← guest 控制

    uint16_t cb = pDesc->data.cmd.u16Length;

    // 當 TSO 分段時，需要計算「這一段還剩多少空間」
    // pThis->u32MaxPayloadSize 從 context descriptor 的 MSS 欄位來 ← guest 控制
    // pThis->u16TxPktLen 是已填入的累積長度

    uint16_t cbMax = pThis->u32MaxPayloadSize;

    // *** 整數下溢（integer underflow）位置 ***
    // 若 pThis->u16TxPktLen > cbMax（guest 刻意讓兩者差為負）
    // 下溢後 cbCopy 變成巨大的 uint16_t 值
    uint16_t cbCopy = cbMax - pThis->u16TxPktLen;  // ← UNDERFLOW HERE

    // transmit buffer 大小固定（例如 16 KB），cbCopy 超過剩餘空間
    // 以下 memcpy 寫到 buffer 末端之後的記憶體
    PDMDevHlpPhysRead(pThis->CTX_SUFF(pDevIns),
                      pDesc->data.u64BufAddr,      /* src：guest 物理位址 */
                      pThis->aTxPacketFallback + pThis->u16TxPktLen, /* dst */
                      cbCopy);                     /* size：下溢後的巨大值 */
    // aTxPacketFallback 是 E1KSTATE 結構裡的固定大小 buffer
    // 這個 memcpy 會覆寫 E1KSTATE 結構後方的其他欄位
}
```

**漏洞根因**：`cbMax - u16TxPktLen` 在 `u16TxPktLen > cbMax` 時下溢，而兩個操作數均來自 guest 可控的 descriptor 欄位。  
**效果**：以 guest 提供的 `buffer_addr` 為來源，在 host ring-3 VBoxDD 行程堆積/記憶體中 OOB write，最終達成任意程式碼執行。

### 漏洞時間線

- **CVE-2017-10235**（fundacion-sadosky）：早期 E1000 buffer overflow，驗證不足
- **CVE-2018-3295**（Sergey Zelenyuk，2018-11 0day 公開）：e1kFallbackAddToFrame() integer underflow，VirtualBox 5.2.20；程式碼在 `github.com/MorteNoir1/virtualbox_e1000_0day`
- **CVE-2019-2722**（STAR Labs，Pwn2Own 2019）：同樣 e1kFallbackAddToFrame()，整數下溢變體
- **CVE-2023-21987 + CVE-2023-21991**（Qrious Security，Pwn2Own 2023）：TPM MMIO read handler stack OOB write + VGA OOB read，與 E1000 無關，說明 MMIO handler 的驗證問題是跨裝置通病

### 逃逸路徑

```
guest root（ring-3）
   ↓ 寫惡意 TX descriptor + 更新 TDT
VBoxDD（host ring-3）執行 e1kFallbackAddToFrame()
   ↓ OOB write 覆寫 VBoxDD 行程記憶體
任意程式碼執行（host ring-3）
   ↓ 開啟 /dev/vboxdrv，ioctl 提權
host ring-0
```

---

## 底層機制：AHCI SATA 控制器

### 原始碼位置

`src/VBox/Devices/Storage/DevAHCI.cpp`  
模擬 AHCI（Advanced Host Controller Interface），SATA 裝置的標準介面。

### Command Table 與 PRDT

AHCI 每個 port 有一個 Command List，每個 command 對應一個 Command Table。  
Command Table 末端附加 Physical Region Descriptor Table（PRDT）：

```c
/* PRDT entry，對應 AHCI spec */
struct ahci_prdt_entry {
    uint32_t dba;      /* Data Base Address（低 32 bit）← guest 控制 */
    uint32_t dbau;     /* Data Base Address Upper（高 32 bit）← guest 控制 */
    uint32_t reserved;
    uint32_t dbc;      /* Data Byte Count（低 21 bit）← guest 控制；bit 31 = IRQ on completion */
    /* DBC 最大值依 spec 應為 4MB-2，但驗證不足時可超過 */
};
```

guest 設定好 PRDT 後，更新 Command List 的 Command Header，再寫入 PxCI（Port x Command Issue）暫存器觸發 host 執行。

### AHCI 潛在攻擊面 **【未實測，理論預期】**

驗證方向：在 `DevAHCI.cpp` 搜尋 `PRDT`、`dbc`、`PDMDevHlpPhysWrite` 的呼叫路徑，追蹤 `dbc` 是否有上界檢查。

```
ahciR3PdmQueueConsume()
  → ahciProcessCommand()
      → ahciR3ReadWriteGuest()     ← 按 PRDT entry 做 DMA
          → PDMDevHlpPhysWrite(..., dbc)  ← dbc 若未驗證即 OOB
```

讀者可用以下方法確認：

```bash
# 在 VirtualBox 源碼中追 PRDT DBC 驗證
grep -n "dbc\|DBC\|cbTransfer" \
  src/VBox/Devices/Storage/DevAHCI.cpp | head -60
```

---

## 底層機制：HDA 音效卡

### 原始碼位置

`src/VBox/Devices/Audio/DevHDA.cpp`  
模擬 Intel HDA（High Definition Audio，82801AA），大多數 VirtualBox VM 預設啟用。

### Buffer Descriptor List（BDL）機制

HDA 的 DMA 引擎用 BDL 告訴 host 要從哪些 buffer 取資料（播放）或寫到哪裡（錄音）。

```c
/* HDA Buffer Descriptor List Entry（BDLE）*/
struct hda_bdle {
    uint64_t u64BufAdr;   /* DMA 位址（guest 物理記憶體）← guest 完全控制 */
    uint32_t u32BufSize;  /* buffer 大小（bytes）← guest 完全控制 */
    uint32_t fFlags;      /* bit 0：IOC（Interrupt On Completion）*/
};
```

Stream 初始化時，guest 設定：
- `CBL`（Cyclic Buffer Length）：整個 BDL 的總長度
- `LVI`（Last Valid Index）：BDL 最後一個有效 entry 的 index
- `BDPL`/`BDPU`：BDL 在 guest 記憶體中的基址

HDA 控制器遍歷 BDL，對每個 BDLE 執行 DMA：

```
hdaR3StreamUpdate()
  → hdaR3StreamDoDmaOutput()
      → 讀取 BDLE[i].u64BufAdr 和 u32BufSize
      → PDMDevHlpPhysWrite(pDevIns, u64BufAdr, src, u32BufSize)
```

### HDA 潛在攻擊面 **【未實測，理論預期】**

若 `u32BufSize` 沒有對照實際 host buffer 大小做上界驗證，即可能 OOB。  
`LVI` 越界也可能讀取 BDL 之外的記憶體作為 BDLE 並觸發任意 DMA。

驗證方向：

```bash
grep -n "u32BufSize\|LVI\|BDLE\|BDL" \
  src/VBox/Devices/Audio/DevHDA.cpp | grep -i "check\|assert\|ASSERT\|>="
```

---

## 對比與取捨

| 維度 | E1000（CVE-2018-3295）| AHCI | HDA |
|------|----------------------|------|-----|
| 觸發路徑 | TX descriptor + TDT 寫入 | PxCI 寫入 | SDCTL stream run |
| 漏洞類型 | integer underflow → OOB write | DBC 未驗證（潛在）| BDL entry 越界（潛在）|
| guest 需要 | NAT/bridge 模式網卡 | SATA 控制器（幾乎必有）| 啟用音效（預設開） |
| 攻擊難度 | 中（需控制 context + data descriptor 時序）| 中 | 高（需要精確 stream 時序）|
| 已知 CVE | CVE-2018-3295, CVE-2019-2722 | 公開資訊少 | 公開資訊少 |
| 現代修補方式 | 長度欄位強制上界 | 同左 | 同左 |
| 研究價值 | 高（有公開 exploit 可對照）| 中（攻擊面清楚，缺乏公開 PoC）| 中 |

---

## 踩雷集錦

### 1. 以為 loopback 模式會關掉 offload 路徑

E1000 在 loopback 模式下，TX 的封包繞回 RX 時仍然走 TSO fallback 路徑。  
Zelenyuk 正是利用這一點讓 `e1kFallbackAddToFrame()` 在 loopback 條件下被觸發，同時減少對外部網路的依賴。

### 2. 誤以為需要 root 才能存取 MMIO 暫存器

在 Linux guest，`/dev/vboxguest` 和 `ioctl` 可以讓 ring-3 guest 程式讀寫 guest 的物理記憶體和送出 hypercall，但 MMIO 對裝置的操作通常需要存取 PCI BAR 對應的 I/O 空間。  
E1000 case 中 Zelenyuk 的 PoC 從 guest root 執行，後續透過 `/dev/vboxdrv` 提權才到 host ring-0。  
沒有 guest root 的情境（例如只有 guest user）需要更多步驟。

### 3. context descriptor 必須先送，才能啟動 TSO fallback

若只送 data descriptor 而沒有先送 context descriptor 設定 MSS，`e1kFallbackAddToFrame()` 不會被呼叫——device 狀態機需要先看到 `DTYP=0x0`。  
exploit 必須嚴格控制 descriptor 送出順序。

### 4. OOB write 的目標不是 stack，是 E1KSTATE 結構後方的堆積空間

`aTxPacketFallback` 是 E1KSTATE 結構的成員，因此 OOB write 打到的是 heap 上 E1KSTATE 之後的相鄰配置物件。  
exploit 需要先做 heap feng shui，讓函式指標或重要資料結構排在 E1KSTATE 之後。

### 5. VirtualBox 7.x 已對長度欄位做限制，5.2.20 的 PoC 無法直接在 7.x 重現

現代版本在 `e1kFallbackAddToFrame()` 入口加了邊界檢查。  
閱讀修補 commit（`3ecfd45` 附近，依版本而異）可以確認修補位置。**【未實測，理論預期】**

---

## 進階：再往深一層

### 系統性找 device bug 的方法論

在 `src/VBox/Devices/` 下面的任何裝置都可以用以下流程分析：

**Step 1：找 guest 可控的 addr/len 欄位**
```bash
# 搜尋所有從 guest descriptor 讀取 addr/len 的地方
grep -rn "PhysRead\|PhysWrite\|PDMDevHlpPhysRead" \
  src/VBox/Devices/ --include="*.cpp" | grep -v "Test\|test"
```

**Step 2：追 MMIO write callback → 狀態機轉換**

每個裝置在 `PDMDEVREG` 裡登記 `pfnConstruct`，在 `pfnConstruct` 裡呼叫 `PDMDevHlpMMIORegister` 指定 MMIO write handler。  
從 write handler 開始，追 guest 寫入特定暫存器後的狀態機變化，找到最終執行 DMA 的路徑。

**Step 3：盯 memcpy/PDMDevHlpPhysRead 的 size 參數來源**

```bash
# 找 size 參數直接來自 descriptor 欄位的位置
grep -n "PDMDevHlpPhysRead\|memcpy" DevE1000.cpp | head -40
```

**Step 4：整數運算分析**

在 size 計算路徑上找所有 `uint16_t` 或 `uint32_t` 的減法，問自己：「兩個操作數都能由 guest 控制嗎？可以讓減數大於被減數嗎？」

**Step 5：狀態機邊界**

找 offload、loopback、省電模式等非標準路徑——這些路徑通常測試較少，容易遺漏驗證。

### 修補前後 diff 讀法

```bash
# clone VirtualBox 源碼後，比較 5.2.20 → 5.2.22 的 DevE1000.cpp
git log --oneline -- src/VBox/Devices/Network/DevE1000.cpp
git diff <before-hash> <after-hash> -- src/VBox/Devices/Network/DevE1000.cpp
```

找新增的 `AssertReturn`、`RT_MIN`、`if (cb > MAX_SOMETHING) return` 行，那就是 patch 點。

---

## 動手練習

### 練習 1：讀 E1000 TX descriptor 處理流程

在 VirtualBox 源碼（可從 [https://www.virtualbox.org/svn/vbox/trunk/](https://www.virtualbox.org/svn/vbox/trunk/) 取得）找到 `DevE1000.cpp`。

1. 搜尋 `e1kFallbackAddToFrame` 函式，找出它在 VirtualBox 7.0 版本裡多了哪些邊界檢查（對比 5.2.20 無 patch 版本）。
2. 繪製從 `e1kTransmitPending()` 到 `e1kFallbackAddToFrame()` 的完整 call stack。
3. 找出 `E1KSTATE` 結構中 `aTxPacketFallback` 的大小（bytes）。

### 練習 2：AHCI PRDT 追蹤

在 `DevAHCI.cpp` 裡：

1. 找到處理 PRDT entry 的函式，確認 `dbc` 欄位是否有 `Assert` 或 range check。
2. 找出 `PDMDevHlpPhysWrite` 呼叫中 size 參數的計算路徑。
3. 寫下：若要觸發 AHCI DMA OOB，guest 需要設定哪些暫存器，順序為何？

### 練習 3：HDA BDLE 遍歷

在 `DevHDA.cpp` 裡搜尋 `hdaR3StreamDoDmaOutput`（或對應函式名）：

1. 確認 `u32BufSize` 讀取後到 DMA 呼叫之間有沒有上界驗證。
2. 找出 `LVI` 欄位讀取後是否有 `>= BDLE count` 的越界檢查。

### 練習 4：建立自己的 checklist

整合前三個練習，為 VirtualBox 裝置模擬漏洞分析建立一份五行以內的 checklist，涵蓋：  
「從 MMIO handler 到 memcpy/PhysRead，我需要確認哪五件事才能判定有無 OOB？」

---

## 本章重點整理

- E1000（`DevE1000.cpp`）模擬 Intel 82540EM，TX descriptor ring 讓 guest 完全控制 `length`、`addr`、`MSS`。
- `e1kFallbackAddToFrame()` 在 TSO fallback 路徑中因 `cbMax - u16TxPktLen` 整數下溢，導致 OOB write 到 `aTxPacketFallback` 之後的 heap。
- CVE-2018-3295 是 Zelenyuk 2018 年 11 月 0day 公開的成果，逃逸路徑：guest root → host ring-3 → `/dev/vboxdrv` → host ring-0。
- AHCI 的攻擊面在 PRDT entry 的 `dbc` 欄位；HDA 的攻擊面在 BDLE 的 `u32BufSize` 和 `LVI` 越界。
- 系統性方法：找 guest 可控的 addr/len → 追 MMIO callback 到 DMA 呼叫 → 分析 size 計算路徑上的整數運算 → 找非標準狀態機路徑（loopback/offload）。
- 現代 VirtualBox（7.0）在入口加了邊界檢查，5.2.20 的 PoC 無法直接複用。

---

## 自我檢核

主動回憶，不要回頭翻——

- [ ] 說出 TX descriptor ring 的四個 MMIO 暫存器（TDBAL/TDBAH/TDH/TDT）各自的角色
- [ ] context descriptor 和 data descriptor 的差異是什麼？各自由哪個欄位區分 DTYP？
- [ ] e1kFallbackAddToFrame() 的整數下溢發生在哪兩個值的計算之間？兩個值分別從哪裡來？
- [ ] CVE-2018-3295 的 exploit 逃逸後為什麼還需要 /dev/vboxdrv？
- [ ] AHCI PRDT entry 有哪三個 guest 可控欄位？
- [ ] HDA BDLE 的兩個關鍵欄位名稱是什麼？
- [ ] 找 device OOB 的五步驟系統方法，能從頭背出來嗎？

---

## 延伸閱讀

1. **Zelenyuk 原始文章與 PoC**  
   `https://github.com/MorteNoir1/virtualbox_e1000_0day`  
   第一手資料；含完整漏洞說明、trigger 條件、guest escape 步驟。

2. **ndureiss/e1000_vulnerability_exploit**（GitHub）  
   CVE-2018-3295 的另一份 exploit 研究，直接引用 CVE 編號，可對照 Zelenyuk 原始 PoC 理解差異。

3. **Intel 82540EM GbE Controller Datasheet**  
   `https://www.intel.com/content/dam/doc/datasheet/82540em-gbe-controller-datasheet.pdf`  
   TX descriptor 格式（第 3.3 節）、TSO 機制（第 3.5 節）的硬體規格，分析 emulation 漏洞前必讀。

4. **VirtualBox 源碼：DevE1000.cpp**  
   `https://www.virtualbox.org/svn/vbox/trunk/src/VBox/Devices/Network/DevE1000.cpp`  
   比對 5.2.20 和當前版本的差異，patch 點一目瞭然。

5. **Qrious Security 2023 Pwn2Own writeup**（CVE-2023-21987）  
   搜尋「VirtualBox Pwn2Own 2023 Qrious Security writeup」；TPM MMIO handler OOB，不同裝置但同樣 MMIO handler 未驗證 cb 的問題，驗證本章方法論的普遍性。

---

Ch 30 確立了三個方向：E1000 有完整 CVE 和公開 exploit 可解剖，AHCI 和 HDA 是留給讀者用同一套方法自己挖的攻擊面。  
下一章直接拆解 CVE-2018-3295 的 exploit 復刻——把 descriptor 操作序列、heap feng shui、shellcode 階段一個個跑過。

→ [Ch 31](./31-virtualbox-escape.md)
