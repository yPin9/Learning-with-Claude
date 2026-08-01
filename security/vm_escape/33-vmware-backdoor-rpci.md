# Ch 33 — Backdoor / RPCI：guest→host 通訊通道

> **目標**：搞清楚 VMware 的 backdoor I/O port 與 RPCI 訊息通道的運作方式，理解 vmware-vmx 如何解析 guest 送來的字串，並把這條攻擊面放進對的框架裡。

---

## 為什麼需要這個？

Part 5（VirtualBox）的攻擊面——E1000、AHCI——都是標準 PCI device，你能找到原始碼、能設斷點、能追 `.write` callback。VMware Workstation 是閉源商業軟體，沒有對應的 `hw/` 目錄可以瀏覽；但它有一套公開文件化的 guest↔host 通訊機制：**backdoor I/O port**。

這個機制存在於 1990 年代末就出現的 VMware Workstation 1.x，最初目的是讓 guest 能偵測「我有沒有跑在 VMware 裡面」，之後逐步擴展成 VMware Tools 和 host 通訊的主要通道。因為 open-vm-tools（VMware 官方開源的 guest 端工具集）包含完整的 guest 端 C 程式碼，我們對這條通道的 **guest 端介面** 有精確認識；host 端（vmware-vmx 行程）的實作則只能透過逆向、或閱讀公開的 vulnerability writeup 來推斷。

為什麼攻擊者在意這條通道？**因為 guest 送進去的資料最終由 vmware-vmx 裡的 string parser 處理。** 只要 parser 有邊界錯誤，就是一條從 guest 打穿到 vmware-vmx 行程（跑在 host userspace）的路。Pwn2Own 歷年多個 VMware 逃逸都走過這條路的某個子系統（DnD、clipboard、shared folder）。

---

## 先建立直覺

### 直覺 1：這不是真的硬體 I/O

一般的 I/O port（`in`/`out` 指令）會觸發 Intel VT-x 的 **I/O VMEXIT**，KVM 把這個 exit 丟給 userspace VMM 處理（Ch 7 走過這條路）。VMware 的 backdoor port **0x5658**（ASCII 'VX'）用的是相同機制——VMM 攔截這個 exit，但不是把它模擬成真實硬體，而是識別 magic value 並走進 backdoor dispatch。

Guest 程式碼觀點：執行一次 `IN` 指令，暫存器就變了。從 CPU 的角度，這和讀一個真實 ISA 設備的 port 沒有差別；從 VMM 的角度，這是一個完全由軟體模擬的介面，magic 讓 VMM 和真實硬體 I/O 區分開來。

### 直覺 2：高頻寬通道用另一個 port

backdoor 通道的基本操作每次只搬四個暫存器的資料（EAX/EBX/ECX/EDX/ESI/EDI），對傳輸大量資料（比如複製貼上幾 KB 的文字）效率很差。VMware 為此設了 **高頻寬通道（High-Bandwidth Backdoor），port 0x5659**（BDOOR_PORT_HB），用不同的暫存器約定讓 guest 每次可以搬更多位元組。

### 直覺 3：RPCI 是建在 backdoor 之上的 RPC 層

Backdoor 本身只是「帶命令碼的暫存器呼叫」。在這之上，VMware 建了一個文字型的 RPC 子系統，稱為 RPCI（Remote Procedure Call Interface），命令碼 `BDOOR_CMD_MESSAGE`（0x1e）。Guest 透過七步協定把一個字串傳進 vmware-vmx，vmware-vmx 解析這個字串並回傳結果。

整條鏈：`guest 程式碼 → backdoor I/O port → VMM VMEXIT handler → vmware-vmx RPCI dispatcher → string parser`。最末端的 string parser 就是攻擊者想到達的地方。

---

## 底層機制：Backdoor 呼叫約定

### 暫存器約定（公開已知，open-vm-tools backdoor_def.h 可查）

```
EAX = BDOOR_MAGIC    (0x564D5868，即 'VMXh' 小端序)
EBX = 任意參數       (命令相關)
ECX = 命令碼         (高 16 位元為子命令)
EDX = BDOOR_PORT     (0x5658，低 16 位元為 port，高 16 位元為通道 ID)
ESI = 選用參數
EDI = 選用參數
```

執行 `IN EAX, DX`（guest 讀 port）之後，VMM 把結果寫回暫存器：
```
EAX = 回傳值 / 狀態
EBX = 額外資料
ECX / EDX / ESI / EDI = 依命令而定
```

以「版本協商」命令為例（BDOOR_CMD_GETVERSION，命令碼 0x0a）：

```
輸入：
  EAX = 0x564D5868   ; magic 'VMXh'
  EBX = 0            ; 不使用
  ECX = 0x0a         ; BDOOR_CMD_GETVERSION
  EDX = 0x5658       ; port

執行：IN EAX, DX

輸出（若在 VMware 內）：
  EAX = 建構版本號碼
  EBX = BDOOR_MAGIC  ; 若不在 VMware，EBX 不會被改成 magic
  ECX / EDX 依命令
```

若 EBX 回傳值不等於 `BDOOR_MAGIC`，代表不在 VMware 環境內（裸機或其他 hypervisor）。

### open-vm-tools 的 C 介面（公開原始碼）

open-vm-tools 的 `lib/include/backdoor_def.h` 與 `lib/backdoor/backdoorGcc64.c` 包含完整的 guest 端實作。典型的 inline assembly 呼叫（x86-64，AT&T 語法）：

```c
/* 來自 open-vm-tools backdoor 實作，已簡化說明用 */
static INLINE void
Backdoor_InOut(Backdoor_proto *myBp)
{
   uint64 dummy;
   __asm__ __volatile__(
#  ifdef __x86_64__
      /* 保存 rbx；x86-64 ABI 要求 callee-saved */
      "xchgq %%rbx, %0"  "\n\t"
      "inl %%dx, %%eax"  "\n\t"   /* 關鍵：IN 指令觸發 VMEXIT */
      "xchgq %%rbx, %0"  "\n\t"
#  endif
      : "=&rm" (dummy),
        "=a" (myBp->out.words.ax),
        "=c" (myBp->out.words.cx),
        "=d" (myBp->out.words.dx),
        "=S" (myBp->out.words.si),
        "=D" (myBp->out.words.di)
      : "0" (myBp->in.words.bx),
        "1" (myBp->in.words.ax),
        "2" (myBp->in.words.cx),
        "3" (myBp->in.words.dx),
        "4" (myBp->in.words.si),
        "5" (myBp->in.words.di)
      : "memory"
   );
}
```

這段程式碼是「guest 端的事實」——任何 VMware guest 上跑的程式都可以用這個介面和 host 通訊。攻擊者在 guest 獲得 code execution 後，可以直接呼叫這個介面構造惡意 RPCI 訊息。

### VMM 端的 VMEXIT 攔截（理論預期 / 逆向推測）

當 guest 執行 `IN EAX, DX` 且 DX = 0x5658（或 0x5659），VT-x 因為 I/O bitmap 的設定觸發 VMEXIT，控制權轉移到 VMware 的 VMM 層。VMM 檢查 EAX 是否等於 `BDOOR_MAGIC`，若是則進入 backdoor dispatch，根據 ECX 低 16 位元的命令碼路由到對應 handler。

> **逆向推測**：vmware-vmx 內部 backdoor dispatcher 的具體函式名稱與結構，此處為逆向分析的推斷，非公開已知事實。公開 writeup（如 Abdul-Aziz Hariri 等人在 Ruxcon 2017 的分析）確認了這個 dispatch table 的存在，但未公開完整符號表。

```
VMEXIT（I/O port 0x5658）
       │
       ▼
   VMM I/O exit handler
       │
       ├── EAX == BDOOR_MAGIC?  ──No──→  Pass through / inject #GP
       │
       Yes
       │
       ▼
   backdoor_dispatch(ECX & 0xffff)
       │
       ├── 0x0a  GETVERSION   → 回傳版本號
       ├── 0x04  GETTIME      → 回傳 host 時間
       ├── 0x0f  GET_BIOS_UUID→ 回傳 UUID
       ├── 0x1e  MESSAGE      → 進入 RPCI 通道 ←── 攻擊面主線
       ├── 0x1c  CLIPBOARD    → 複製貼上通道
       └── ... (數十個命令碼)
```

---

## 底層機制：RPCI 七步協定

RPCI 訊息通道的建立、傳輸、關閉是一個七步序列，全部透過 backdoor 命令碼 `0x1e`（BDOOR_CMD_MESSAGE）的不同子命令完成。

### 步驟拆解

```
Step 1: Open channel
  ECX = 0x0000001e    ; BDOOR_CMD_MESSAGE
  EBX = 0x00000000    ; 開啟請求 magic
  →  EAX = 成功/失敗
     ECX.high16 = 通道 ID（後續步驟用這個 ID）

Step 2: Send message length
  ECX.high16 = 通道 ID
  EBX = 訊息長度（bytes）
  →  通知 host 即將傳入多少資料

Step 3: Send message data (4 bytes / 次)
  重複直到整個訊息傳完
  EBX = 4 bytes 的訊息內容
  （高頻寬通道可一次傳更多）

Step 4: Receive response status
  →  EAX = 狀態碼
     EBX = 回應長度（bytes）

Step 5: Receive response data (4 bytes / 次)
  重複直到整個回應接完

Step 6: Close channel
  ECX.high16 = 通道 ID
  →  釋放通道資源

（Step 7 在某些實作中是確認 close 的 ACK 步驟）
```

### 訊息格式：純文字字串

RPCI 訊息是 **ASCII 字串**，格式為：

```
"命令名稱 參數1 參數2 ..."
```

幾個真實命令（來自 open-vm-tools 與公開文件）：

| 命令字串                          | 作用                          | 方向        |
|----------------------------------|-------------------------------|------------|
| `info-get guestinfo.ip`          | 取得 guest IP 供 host 查詢     | guest→host |
| `tools.set.version VERSION`      | 回報 VMware Tools 版本         | guest→host |
| `info-set guestinfo.KEY VALUE`   | 在 guestinfo 命名空間寫 KV     | guest→host |
| `unity.not.maximized`            | Unity 模式視窗事件通知          | guest→host |
| `DnD_Transport ...`              | 拖放資料傳輸（DnD 子系統）      | guest→host |

Host 端回傳格式一般是 `"1 成功回應內容"` 或 `"0 錯誤訊息"`（首字元是成功/失敗旗標）。

### ASCII 圖：完整呼叫路徑

```
 Guest userspace
 ┌─────────────────────────────────────┐
 │  RpcChannel_Send("info-get ...")    │
 │    └─ Message_Send()                │
 │         └─ Backdoor(0x1e, ...)      │
 │              └─ IN EAX, DX (0x5658)│
 └──────────────┬──────────────────────┘
                │  VMEXIT (I/O port)
 VMM / vmware-vmx
 ┌──────────────▼──────────────────────┐
 │  I/O Exit Handler                   │
 │    └─ backdoor_dispatch(0x1e)       │
 │         └─ message_channel_handler  │
 │              ├─ open_channel()      │
 │              ├─ recv_data()    ←────── guest 傳進來的字串
 │              ├─ rpci_dispatch()     │
 │              │    └─ string match → │
 │              │         handler()    │  ← 攻擊者想到達的 parser
 │              └─ send_response()     │
 └─────────────────────────────────────┘
 Host userspace (vmware-vmx 行程)
```

---

## 對比與取捨

| 通道              | 協定層次      | 資料格式        | 攻擊面          | 公開資訊量       |
|-----------------|-------------|--------------|----------------|----------------|
| Backdoor (基本)  | 暫存器級      | 32/64-bit 值  | 命令碼 handler  | 高（open-vm-tools）|
| RPCI (0x1e)     | 文字 RPC 層   | ASCII 字串    | String parser  | 中（公開 writeup）|
| 高頻寬通道 0x5659 | Backdoor 延伸 | 批次位元組流   | 長度/緩衝區處理  | 中             |
| VMCI             | 類 socket     | 任意二進位     | VMCI protocol  | 低（驅動逆向）   |
| HGFS (shared folder) | 檔案系統協定 | 結構化 packet | FS parser      | 低（協定逆向）   |
| Clipboard        | RPCI 子系統   | 任意 bytes    | 大緩衝區複製    | 中（歷史 CVE）   |

**VMCI（Virtual Machine Communication Interface）**：VMware 提供的高效能 socket-like 通道，guest 和 host（或 guest 之間）透過 `vmci.sys`（Windows）/`vmci.ko`（Linux）driver 通訊。主要用於 VMware 工具內部；攻擊面相對獨立，需要逆向 VMCI host daemon。

**HGFS（Host-Guest Filesystem）**：shared folder 功能的底層協定，透過 RPCI 傳送結構化封包。因為涉及路徑字串解析與檔案操作，歷史上是 OOB 的豐產區，Pwn2Own 有案可查的 VMware 逃逸中有數個走過 HGFS 路徑。

**RPCI vs VMCI 對比**：RPCI 走 backdoor port，不需要 guest 安裝驅動（純 `in`/`out` 指令）；VMCI 需要 guest 安裝對應 kernel module。從攻擊者角度，RPCI 在 guest 獲得 userspace RCE 後**立刻可用**，不依賴 Tools 安裝狀態，是更直接的管道。

---

## 踩雷集錦

**「backdoor port 0x5658 是真實硬體 I/O」**

錯。這個 port 在真實 x86 上屬於 unused range，沒有任何真實硬體映射到這裡。VMware 在 I/O bitmap 把這個 port 標為「trap」，所以 guest 執行 `IN EAX, DX`（DX = 0x5658）時 100% 觸發 VMEXIT，完全由 VMM 軟體處理。在裸機執行同樣的指令，行為是 undefined（可能產生隨機值，也可能 hang）。

**「EBX 的 magic check 是 guest 做的，host 不用驗」**

反過來的。Guest 送 `EAX = BDOOR_MAGIC` 給 host，host（VMM）回傳 `EBX = BDOOR_MAGIC` 作為確認。**Guest 做的是「偵測自己是不是在 VMware 裡跑」**；host 對收到的 magic 進行驗證，但完全不代表後續 RPCI 字串有被嚴格驗證——那才是 bug 出現的地方。

**「RPCI 只有 VMware Tools 安裝後才能用」**

不正確。Backdoor 介面只需要 `IN` 指令，不依賴任何 guest driver。Open-vm-tools 是 guest 端的高階封裝；但攻擊者可以直接呼叫 `IN EAX, DX`，自己實作訊息傳送，完全繞過 Tools 層。CTF writeup 和真實利用都直接使用 raw backdoor 呼叫。

**「RPCI 命令只有白名單裡的幾個，parser 沒有攻擊面」**

歷史說明恰好相反。vmware-vmx 的 RPCI dispatcher 需要 string match 幾十個命令子串，DnD、clipboard、Unity、HGFS 每個子系統都有自己的 handler；每個 handler 都要進一步解析剩餘的字串參數。CVE-2017-4901 就是 DnD handler 的 OOB，起點是 guest 送進去的一個惡意 RPCI 訊息。parser 攻擊面不小。

**「從 open-vm-tools 原始碼能看到完整的 host 端 handler 邏輯」**

open-vm-tools 只包含 **guest 端** 的程式碼（`lib/backdoor/`、`lib/rpcChannel/`、`lib/rpcIn/` 等）。這些檔案告訴我們 guest 如何 **發送** 請求，以及 guest 如何 **處理** host 主動送來的請求（RpcIn）；但 vmware-vmx 對每個命令的 **回應邏輯** 是閉源的。你能從 open-vm-tools 推斷「host 應該會理解這些命令字串」，但 host 的解析細節需要逆向 vmware-vmx 才能確認。

---

## 進階：再往深一層

### RpcIn vs RpcOut

RPCI 有兩個方向：

- **RpcOut（guest→host）**：guest 主動發訊息給 host，問 IP、推 Tools 狀態等。攻擊者利用這個方向把惡意字串送進 host parser。
- **RpcIn（host→guest）**：host 主動通知 guest（例如「你被掛起了，請做 snapshot 準備」）。Guest 端 open-vm-tools 有 callback 機制監聽這些通知。攻擊方向反過來：如果 guest 端的 RpcIn callback 有 bug，惡意 host 可以反向打 guest——這是 hypervisor→tenant 的攻擊向量，和我們的主題（VM escape）相反，但值得認識。

### 通道多路複用

單一 guest 可以開多個獨立的 RPCI 通道（每個通道有不同的 ID），host 端維護一個通道狀態表。CVE 分析中有人提到通道 ID 的邊界處理可能存在問題——這屬於**逆向推測領域**，需要對 vmware-vmx 做靜態分析或 fuzzing 才能確認，本課不做進一步陳述。

### DnD 子系統：CVE-2017-4901 的背景

CVE-2017-4901（公開 advisory：VMware 的 drag-and-drop 功能 OOB）走的路徑是 guest 透過 RPCI 的 `DnD_Transport` 系列命令把資料送給 host，host 端的 DnD 實作在處理某個長度或緩衝區邊界時出現 OOB write，導致 vmware-vmx 行程記憶體損壞。

> **已知事實**：CVE-2017-4901 存在，VMware 官方確認並修補，CVSS 8.8（guest→host）。漏洞位於 drag-and-drop 功能，涉及 RPCI 層。
>
> **逆向推測**：具體的 vulnerable function 名稱、OOB 位移量、精確的 heap 佈局——這些在 Ruxcon 2017 「For the Greater Good」演講中有部分描述，但完整的技術細節並非所有細節都公開。此處不補充推斷的內部細節。

### Fuzzing RPCI 的方法論

因為 RPCI 是文字協定，guest 端又完全可控，RPCI fuzzing 的入口門檻極低：

1. 在 guest 內寫一個迴圈，透過 backdoor 送各種變體的 RPCI 字串
2. 在 host 用 `strace`/`rr` 監控 vmware-vmx 行程是否崩潰
3. 對 DnD、clipboard、HGFS 各子系統的參數欄位做 mutation

真實的 Pwn2Own 研究者（如 ZDI 的研究員）使用這個框架的變體對 VMware 的 RPCI 子系統做系統性 fuzzing，並在歷屆找到多個漏洞。

### RPCI 與 HGFS 的邊界

部分 HGFS（shared folder）請求也透過 RPCI 通道傳送，命令前綴為 `HGFS_PACKET_MAGIC` 之類的識別子（實際格式為逆向推測，非公開文件）。這代表 HGFS parser 是 RPCI attack surface 的一個子集，guest 不需要掛載 shared folder 就能把 HGFS 格式的 bytes 送進 host parser——前提是 handler 的路由邏輯確實允許這條路。

---

## 動手練習

**前置說明**：VMware Workstation 閉源，以下練習的「觀察 host 端行為」部分有環境限制。若有合法授權的 VMware Workstation 環境，可跑 guest 端；host 端分析需要逆向 vmware-vmx（需法律確認），本練習聚焦在 guest 端可驗證的部分。

### 練習 1：讀懂 open-vm-tools 的 guest 端呼叫鏈

1. 從 GitHub clone open-vm-tools（`https://github.com/vmware/open-vm-tools`）
2. 找到並閱讀以下檔案：
   - `lib/include/backdoor_def.h`：所有命令碼常數定義
   - `lib/backdoor/backdoorGcc64.c`：x86-64 的 inline asm 實作
   - `lib/rpcChannel/rpcChannel.c`：RpcChannel 高階封裝
   - `lib/rpcIn/rpcin.c`：RpcIn（host→guest）callback 架構
3. 追蹤 `RpcChannel_Send()` 的呼叫鏈，畫出從這個函式到 `IN EAX, DX` 的每一層函式呼叫。
4. 找到 `BDOOR_CMD_MESSAGE`（0x1e）在哪裡被用到，以及它的子命令常數是什麼。

### 練習 2：寫 guest 端 backdoor 偵測器

在 VMware guest（Linux）內，寫一個 C 程式直接呼叫 backdoor port：

```c
#include <stdint.h>
#include <stdio.h>

#define BDOOR_MAGIC     0x564D5868UL
#define BDOOR_PORT      0x5658
#define BDOOR_CMD_GETVERSION 0x0a

typedef struct {
    uint32_t ax, bx, cx, dx, si, di;
} BdoorRegs;

static void backdoor_call(BdoorRegs *r) {
    __asm__ __volatile__(
        "xchgl %%ebx, %1\n\t"
        "inl %%dx, %%eax\n\t"
        "xchgl %%ebx, %1\n\t"
        : "=a"(r->ax), "=rm"(r->bx), "=c"(r->cx),
          "=d"(r->dx), "=S"(r->si), "=D"(r->di)
        : "0"(r->ax), "1"(r->bx), "2"(r->cx),
          "3"(r->dx), "4"(r->si), "5"(r->di)
        : "memory"
    );
}

int main(void) {
    BdoorRegs r = {
        .ax = BDOOR_MAGIC,
        .bx = 0,
        .cx = BDOOR_CMD_GETVERSION,
        .dx = BDOOR_PORT
    };
    backdoor_call(&r);
    if (r.bx == BDOOR_MAGIC)
        printf("Running in VMware, version: %u\n", r.ax);
    else
        printf("Not in VMware (EBX=0x%x)\n", r.bx);
    return 0;
}
```

在 VMware guest 和裸機（或 KVM/QEMU guest）各跑一次，比較輸出差異。注意裸機上這個 `IN` 指令可能行為不一（多數 x86 機器此 port 為 unused，有的會直接 freeze，請在 VM 內測試）。

### 練習 3：閱讀 CVE-2017-4901 advisory 並畫攻擊路徑圖

1. 查閱 VMware 官方 advisory（VMSA-2017-0006）和 NVD 的 CVE-2017-4901 描述
2. 確認：漏洞在哪個 VMware 版本？哪個功能（DnD）？影響等級？
3. 根據本章的 RPCI 知識，畫出「guest 惡意程式 → RPCI DnD_Transport → vmware-vmx OOB」的路徑圖
4. 思考：host 端 OOB write 之後，要達到 code execution 還需要什麼步驟？（heap layout control、infoleak、function pointer hijack——和 QEMU 的流程有何異同？）

### 練習 4：對照 QEMU 與 VMware 的 guest→host 通道

製作一張比較表，對照：
- QEMU/KVM 的 virtio notify（`MMIO write → eventfd → vhost thread`）
- VMware backdoor（`IN 指令 → VMEXIT → vmware-vmx handler`）
- VMware RPCI（`backdoor 0x1e → string parser`）

每欄填：觸發機制、資料格式、host 端接收函式層級、已知 CVE 舉例（各填 1 個）。

---

## 本章重點整理

- Backdoor I/O port **0x5658**（magic `0x564D5868`）是 VMware guest↔host 通訊的基礎介面，透過 VT-x I/O VMEXIT 實作，不涉及真實硬體。
- 暫存器約定：`EAX = magic`、`ECX = 命令碼`、`EDX = port`；回傳時 `EBX = magic` 作確認。高頻寬通道用 port **0x5659**。
- **RPCI** 建立在 backdoor 命令碼 `0x1e`（BDOOR_CMD_MESSAGE）之上，七步協定傳輸 ASCII 字串，由 vmware-vmx 內部的 string parser 處理。
- Guest 端實作完整公開（open-vm-tools），host 端（vmware-vmx）為閉源，需逆向或依賴公開 writeup。
- 攻擊面核心是 vmware-vmx 的 RPCI string parser：DnD、clipboard、HGFS、Unity 各子系統的 handler 都是潛在目標。
- CVE-2017-4901 確認了 RPCI DnD 子系統存在可利用的 OOB；完整技術細節部分公開於 Ruxcon 2017 演講。
- Guest 端只需 `IN` 指令就能呼叫 backdoor，不依賴 VMware Tools 安裝——這讓 RPCI 成為 guest userspace RCE 之後立刻可用的 host 攻擊管道。

---

## 自我檢核

- [ ] 我能說出 backdoor 為什麼不是真實硬體 I/O，而是 VMEXIT 處理
- [ ] 我知道 EAX/EBX/ECX/EDX 在 backdoor 呼叫前後分別代表什麼
- [ ] 我能區分 backdoor（暫存器級）與 RPCI（文字 RPC）的層次關係
- [ ] 我能說出 RPCI 七步協定的每個步驟及其目的
- [ ] 我知道 open-vm-tools 涵蓋哪一端的程式碼，不涵蓋哪一端
- [ ] 我理解為什麼 DnD / clipboard / HGFS 是 RPCI 攻擊面的子集
- [ ] 我能解釋 CVE-2017-4901 的觸發路徑（在公開 advisory 層級）
- [ ] 我清楚「已知事實」與「逆向推測」在本章哪些陳述中各自適用

---

## 延伸閱讀

1. **open-vm-tools 原始碼（GitHub）**
   `https://github.com/vmware/open-vm-tools`
   直接看 `lib/include/backdoor_def.h`（命令碼常數）、`lib/backdoor/backdoorGcc64.c`（x86-64 asm）、`lib/rpcChannel/`（RPC 高階層）。這是 guest 端的一手文件，沒有比這更權威。學什麼：RPCI 協定的 guest 端完整實作、暫存器約定的精確定義。

2. **「For the Greater Good」— Abdul-Aziz Hariri, Jasiel Spelman, Brian Gorenc（ZDI）**
   Ruxcon 2017 演講；投影片可在 ZDI 或 Ruxcon 存檔找到。
   學什麼：系統性的 RPCI 攻擊面分析，包含如何對 vmware-vmx 的 RPCI dispatcher 做 fuzzing、CVE-2017-4901 相關背景，以及 DnD 子系統的漏洞模式。本課關於 RPCI 攻擊面的敘述以這場演講為主要背景依據。

3. **VMware VMSA-2017-0006（官方 advisory）**
   `https://www.vmware.com/security/advisories/VMSA-2017-0006.html`
   CVE-2017-4901 的官方確認，包含影響版本、CVSS 評分、修補資訊。學什麼：如何閱讀 VMware 官方 advisory，以及從 advisory 反推攻擊面（「drag-and-drop 功能」→「走 RPCI DnD 子系統」）。

4. **Kostya Kortchinsky「Cloudburst」（Black Hat USA 2009）**
   VMware Workstation 舊版 SVGA OOB 的早期公開研究，雖然年代久遠，但展示了「從 guest 送構造好的封包打穿 vmware-vmx」的完整思路，且部分細節涉及 backdoor 通道的使用。學什麼：閉源 hypervisor 攻擊面分析的方法論雛形，以及 VMware 攻擊的歷史脈絡（Ch 34/35 的前置理解）。

5. **Félix Cloutier「x86 IN instruction」（felixcloutier.com/x86）**
   `https://www.felixcloutier.com/x86/in`
   `IN` 指令在 VMX non-root 模式下的行為（當 I/O bitmap bit 被設定時觸發 VM exit）的 Intel SDM 節錄與整理。學什麼：確認 backdoor 的硬體機制——為什麼 guest 執行 `IN EAX, DX` 會陷入 VMM，而不是讀到真實硬體值。與 Intel SDM Vol. 3C § 25.1.3 對照。

---

Backdoor / RPCI 是 VMware 攻擊面的入口地圖；你現在知道 guest 的字串是怎麼被送進 vmware-vmx parser 的，也知道歷史上哪些子系統（DnD、clipboard、HGFS）在這條路上留過洞。下一章轉向另一條更常見於現代 Pwn2Own 的攻擊面——SVGA GPU 模擬，那是一個有完整 DMA 介面、ring buffer、shader 管線的複雜子系統，在獎項紀錄上的命中率比 RPCI 高。

→ [Ch 34 — SVGA / mks GPU 攻擊面（Pwn2Own 常客）](./34-vmware-svga-gpu.md)
