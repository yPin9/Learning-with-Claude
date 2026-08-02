# Ch 11 — SMM 攻擊面

> **目標**：搞清楚 SMM handler 的軟體架構，找出攻擊者能控制的每一個輸入點，理解 SMRAM 保護機制的設計意圖與邊界，為後面的具體漏洞類型（Ch 12）打好基礎。

## 為什麼要研究攻擊面？

攻擊面分析是一切漏洞挖掘的起點。SMM 的程式碼在 SMRAM 中，攻擊者無法直接讀寫它——但攻擊者能做到的是：

1. **觸發 SMI**（只需 kernel 層權限）
2. **控制 SMI 帶入 SMM 的輸入資料**（CommBuffer 在 SMRAM 外）
3. **觀察 SMM 的副作用**（它寫回 DRAM 的內容、I/O 操作的結果）

整個 SMM 的攻擊面本質上就是：「SMI handler 讀取了哪些來自 SMRAM 外的資料，對這些資料做了哪些假設？」

## SMM 軟體架構：三層 Dispatch

EDK2 的 SMM 框架把 SMI 的分發做成三層結構：

```
SMI 觸發（例：outb(0x88, 0xB2)）
│
▼
SmmCore（SMM Foundation）
  ├── 管理所有 SMM driver 的生命週期
  ├── 維護 handler 的 registered list
  └── 呼叫 top-level dispatcher
        │
        ▼
        SMM SW Dispatch Driver（SmmSwDispatch2）
          ├── 讀取 I/O port 0xB2 的值（SMI code）
          ├── 在 registered handler 表中尋找對應 code
          └── 呼叫對應 child handler
                │
                ▼
                Child SMI Handler（各 SMM driver 自己的函式）
                  ├── 接收 DispatchContext（含 SwSmiInputValue）
                  ├── 讀取 CommBuffer（OS 傳入的資料）
                  └── 執行具體邏輯
```

每一層都有自己的攻擊面，但最豐富的輸入在 **child handler 對 CommBuffer 的處理**。

### SMM Driver 的種類

| 類型 | 描述 | 攻擊面 |
|------|------|--------|
| **SMM Core Driver** | `PiSmmCore`，唯一由 SMM foundation 直接管理的 | 幾乎不可攻，在韌體啟動期載入 |
| **SW SMI Dispatch Handler** | 登記特定 SW SMI code 的 handler | 可透過 0xB2 觸發，CommBuffer 完全由 OS 控制 |
| **Child Dispatcher** | 基於 GUID 做次級 dispatch | 需要知道正確 GUID 才能觸發特定功能 |
| **非 SW SMI Handler** | 響應 GPI/USB legacy/periodic 等 | 觸發條件受限，但某些 GPI 可用軟體控制 |

## EFI_SMM_COMMUNICATION_PROTOCOL：OS 到 SMM 的橋梁

UEFI 規範定義了一個正式協議讓 OS 呼叫 SMM 服務：**EFI_SMM_COMMUNICATION_PROTOCOL**（簡稱 SmmCommunication protocol）。

它的工作流程：

```c
// OS（EFI Runtime 環境）呼叫這個 protocol 的 Communicate()：
EFI_SMM_COMMUNICATE_HEADER *Header;
Header = (EFI_SMM_COMMUNICATE_HEADER *)CommBuffer;
Header->HeaderGuid = TARGET_SMM_HANDLER_GUID;  // 指定要呼叫哪個 handler
Header->MessageLength = sizeof(MY_DATA);
CopyMem(Header->Data, &my_payload, sizeof(MY_DATA));

SmmCommunication->Communicate(SmmCommunication, CommBuffer, &CommSize);
// 這個呼叫最終觸發一個 SW SMI（code 由實作決定，常是 0x00）
```

**CommBuffer 的位置是關鍵**：它必須在 **SMRAM 之外**，因為 OS runtime 要能寫入，且 SMRAM 對 OS 不可見。這意味著 CommBuffer 的內容完全由 OS（攻擊者）控制。

## CommBuffer 的資料流

這是整個攻擊面最重要的一張圖：

```
OS Memory（SMRAM 外，攻擊者可控）
┌────────────────────────────────────────┐
│  CommBuffer                            │
│  ┌─────────────────────────────────┐   │
│  │ EFI_SMM_COMMUNICATE_HEADER      │   │
│  │   HeaderGuid: {選擇 handler}    │   │  ← 攻擊者填寫
│  │   MessageLength: N              │   │  ← 攻擊者填寫
│  │   Data[N]:                      │   │
│  │     struct MY_INPUT {           │   │
│  │       UINT64 Ptr;    ← 指標？   │   │  ← 攻擊者填寫
│  │       UINT32 Size;              │   │  ← 攻擊者填寫
│  │       UINT8  Payload[64];       │   │  ← 攻擊者填寫
│  │     }                           │   │
│  └─────────────────────────────────┘   │
└────────────────────────────────────────┘
         │
         │  SW SMI 觸發後
         │  SMM handler 讀取此 buffer
         ▼
SMRAM（攻擊者不可直接讀寫）
┌──────────────────────────────────────────┐
│  SMM Handler                             │
│                                          │
│  CommBuffer = 從 save state 取得位址     │
│  Data = CommBuffer->Data;                │
│                                          │
│  if (Data->Ptr != NULL) {                │
│      // 若沒驗證 Ptr 是否在 SMRAM 內...  │
│      memcpy(dest, (void*)Data->Ptr, ...); │  ← 危險！
│  }                                       │
└──────────────────────────────────────────┘
```

攻擊者能控制 CommBuffer 中的每一個位元組，包括任何指標欄位、長度欄位、索引欄位。

## SMRAM 保護機制詳解

### SMRR（SMM Range Registers）

SMRR 是 Intel 在 Nehalem（Xeon 5500/Core i7 第一代）引入的 MSR 組：

- `IA32_SMRR_PHYSBASE`（MSR 0x1F2）：SMRAM 基底位址 + 記憶體類型（WB/UC）
- `IA32_SMRR_PHYSMASK`（MSR 0x1F3）：範圍遮罩 + valid bit

在**非 SMM 模式**下，CPU 對命中 SMRR 範圍的記憶體存取會強制為 UC（Uncacheable）。這阻止了以下攻擊：

```
攻擊情境（SMRR 阻止的）：
OS 用 WBINVD 讓 CPU cache 失效
→ 在 SMRAM 對應位址做 speculative/cache 存取
→ 試圖透過 cache timing 側信道讀出 SMRAM 內容

SMRR 的效果：SMRAM 位址永遠不會進入 cache（非 SMM 模式下）
             → 這條攻擊路徑無效
```

SMRR 只有 BSP（Bootstrap Processor）的 SMRR 才有強制效果（在某些平台設計下）；AP 的 SMRR 也需要設定。韌體必須對每個邏輯 CPU 都設好 SMRR，否則 MP 系統上的非 BSP 核心成為漏洞點。

### D_LCK 與 D_OPEN（Chipset SMRAM Control Register）

Intel PCH/MCH 上有一個 `GEN_PMCON_3` 或類似暫存器（PCH Gen 不同名稱不同，但功能一致）控制 SMRAM 的可見性：

| 位元 | 名稱 | 功能 |
|------|------|------|
| D[3] | D_OPEN | = 1 時允許非 SMM 存取 SMRAM（初始化期間用） |
| D[2] | D_CLS | = 1 時 SMRAM 完全關閉（A/B 段用） |
| D[1] | D_LCK | = 1 時鎖定 D_OPEN，無法再改；直到 HRESET |
| D[0] | G_SMRAME | Global SMRAM Enable |

正確的開機流程：
```
SEC/PEI：D_OPEN=1 → 寫 SMM handler 到 TSEG
DXE 結束前：D_OPEN=0 → D_LCK=1（鎖定，硬體重設前不可解）
```

**漏洞點**：若平台沒有在 DXE 結束前正確設 D_LCK，攻擊者可以在 OS 層透過 PCI config space 存取（需要 root + /dev/mem 或 CHIPSEC）操作 D_OPEN=1，重新打開 SMRAM。這是 CHIPSEC `smm` 模組要稽核的核心項目之一。

### TSEG Base/Size 保護

TSEG 的 base 和 size 也需要鎖定。若 chipset 允許 OS 修改 TSEG 範圍暫存器，攻擊者可以把 TSEG 範圍移到別處，讓原本的保護失效。EDK2 的 `SmmAccessDxe` 負責鎖定這些暫存器。

### SMM_Code_Chk_En（SMM Code Access Check）

Intel 在 Haswell 後引入 SMM_Code_Chk_En（IA32_FEATURE_CONTROL MSR 的 bit 2）：

- 設為 1 後，SMM 執行的程式碼**位址必須在 SMRR 保護範圍內**
- 若 SMM handler 的 RIP 跑出 SMRAM 範圍（如被 callout 到 OS 記憶體），CPU 產生 machine check
- 這是 Intel 2015 年後針對 SMM callout 攻擊的硬體緩解

**未設的情況**：很多老平台、老韌體沒有啟用 SMM_Code_Chk_En，這直接讓 callout 攻擊（Ch 12）可行。CHIPSEC 的 `smm_code_chk` 模組稽核這個 bit。

## 攻擊面清單

把上面的分析整理成一份攻擊者的角度清單：

### 輸入向量 1：CommBuffer 內容

這是**主要攻擊面**。CommBuffer 完全在 OS 記憶體中，SMM handler 必須從那裡讀取輸入。

可攻擊的欄位：
- **GUID**：若 handler 沒做嚴格 GUID 比對，可能被錯誤觸發
- **MessageLength**：整數溢位、buffer 大小計算錯誤 → heap overflow in SMRAM
- **內嵌指標**：若 handler 把 CommBuffer 裡的指標當作讀寫目標，未驗證其範圍 → arbitrary read/write
- **索引值**：陣列索引若來自 CommBuffer，可 OOB 存取 SMRAM

### 輸入向量 2：NVRAM / UEFI Variable

部分 SMM handler 在執行期讀取 UEFI Variable（如設定值）。若這些 Variable 在 DXE 階段由 OS 可寫（runtime variable），攻擊者可以在呼叫 SMM 前竄改它。

### 輸入向量 3：共享記憶體區域（非 CommBuffer）

部分廠商實作用了自定義的共享記憶體區域（不透過正式 SmmCommunication protocol），這些區域同樣在 SMRAM 外、OS 可改。

### 輸入向量 4：硬體狀態（GPE、EC）

某些 SMI handler 從嵌入式控制器（EC）或其他 I/O 埠讀取狀態。若攻擊者能操控這些 I/O 埠（需要 IOPL 或 /dev/port 存取），可以注入假資料。

## CHIPSEC 稽核工具

**注意：以下假設 CHIPSEC 已安裝於真機或支援 CHIPSEC 的環境。QEMU 模擬環境的 MSR/PCI 存取值不等於真機行為。本段為說明模組功能，未在此機器上實測。驗證環境要求：裸機 x86（Intel 第 6 代以上），CHIPSEC 1.9+ 安裝於 Linux root 或 Windows system。**

```bash
# 以 root 執行 CHIPSEC 對 SMM 保護的完整稽核
sudo python3 chipsec_main.py -m smm

# 模組輸出會涵蓋：
# 1. SMRR 是否已設定（每個 CPU core）
# 2. D_LCK 是否已設（chipset SMRAM control register）
# 3. TSEG range 是否合理

# 個別模組：只查 SMRR
sudo python3 chipsec_main.py -m common.smrr

# 個別模組：SMRAM 鎖定狀態
sudo python3 chipsec_main.py -m common.smram

# 個別模組：SMM Code Access Check
sudo python3 chipsec_main.py -m common.smm_code_chk
```

CHIPSEC 的每個模組都在它的 `.py` 原始碼裡清楚地寫出它讀的是哪個 MSR、哪個 PCI config space 暫存器，以及期望的值是什麼。即使沒有硬體，閱讀這些模組原始碼本身就是學習 SMM 保護機制的好方法：

```
chipsec/modules/common/smrr.py      → 讀 IA32_SMRR_PHYSBASE/MASK MSR
chipsec/modules/common/smram.py     → 讀 GEN_SMRAMC / TSEG_MB
chipsec/modules/common/smm_code_chk.py → 讀 IA32_FEATURE_CONTROL
chipsec/modules/smm.py              → 綜合稽核
chipsec/modules/smm_ptr.py          → 掃描 SMM 指標驗證問題
```

## 保護機制的邊界

即使所有保護機制都正確設定，SMM 的安全模型仍有一個根本假設：

**SMM 程式碼自身沒有漏洞。**

D_LCK、SMRR、SMM_Code_Chk_En 這些都是「外部」保護，防的是從 SMRAM 外部直接攻擊。但如果 SMM 程式碼**主動讀取攻擊者控制的資料（CommBuffer）**，然後對這些資料做了不安全的操作，那所有外部保護都救不了你。

這就是為什麼 Ch 12 要講 callout 和指標竄改：真正的 SMM 漏洞利用鏈幾乎都是透過 CommBuffer 進入。

## 對比取捨

| 保護機制 | 防禦目標 | 繞過條件 | 有無硬體強制 |
|---------|---------|---------|------------|
| D_LCK | 非 SMM 直接讀寫 SMRAM | D_LCK 未設；chipset bypass | 是（chipset） |
| SMRR | Cache 側信道探測 SMRAM | SMRR 未設；APs 未設 | 是（CPU MSR） |
| SMM_Code_Chk_En | Callout 到 SMRAM 外執行 | 未啟用（老平台常見） | 是（CPU） |
| TSEG 鎖定 | 移動 SMRAM 範圍 | TSEG 暫存器未鎖 | 依廠商實作 |
| SmmIsBufferOutsideSmmValid | CommBuffer 指標驗證 | 程式碼沒呼叫這個函式 | 否（純軟體） |

最後一行是最常出問題的：`SmmIsBufferOutsideSmmValid` 是 EDK2 提供的輔助函式，用來確認某個位址範圍不在 SMRAM 內（CommBuffer 應該完全在 SMRAM 外）。但這是**軟體 API**，要靠 SMM driver 開發者主動呼叫——忘記呼叫就是一個洞。

## 踩雷紀錄

**坑 1：以為 SmmCommunication 是唯一的輸入路徑**
部分韌體廠商用了私有的 software SMI 介面（直接寫 0xB2 + 自定義 I/O port 序列），不走 EFI_SMM_COMMUNICATION_PROTOCOL。逆向時要掃所有 SW SMI code 對應的 handler，不只找 SmmCommunication 的 GUID dispatch。

**坑 2：混淆 SMRR 保護範圍與 TSEG 大小**
SMRR 的 PHYSMASK 是 MTRRphysMask 的語法（2 的冪次方對齊），設定上容易出錯。一個 8MB 的 TSEG 需要 SMRR_PHYSMASK = 0xFF800001（舉例），如果計算錯誤，SMRR 保護的範圍可能比實際 TSEG 大或小，留下側信道漏洞。

**坑 3：MP 系統只設 BSP 的 SMRR**
在多核系統中，SMRR 需要在每個邏輯 CPU 上都設定。EDK2 的 `SmmCpuFeaturesInstallSmiHandler` 負責這件事，但第三方韌體偶爾只設 BSP 的 SMRR，AP 的 SMRR 沒設 → AP 的 cache 不受 SMRR 保護。

**坑 4：SMM handler 信任 CommBuffer 的 MessageLength 做陣列邊界**
這是最常見的 SMM buffer overflow 類型。MessageLength 在 CommBuffer 中，攻擊者可控。若 handler 直接用這個值計算 copy 大小，而沒有對照實際的 SMRAM 目標 buffer 大小做上限檢查，就是 overflow into SMRAM。

**坑 5：以為 OVMF 上的 CHIPSEC 結果代表真機**
在 QEMU 裡跑 CHIPSEC，D_LCK 可能回報 1（OVMF 有設），但實際的 SMRAM 隔離是 QEMU 軟體模擬的，不等於 chipset 硬體路徑。做安全評估，CHIPSEC 一定要在真機上跑。

## 進階延伸

- **Measured SMM**（Intel Boot Guard + ACM）：Intel 的 TXT/Boot Guard 架構可以把 SMM 的度量值（hash）納入 PCR，讓攻擊者無法靜默地替換 SMM 程式碼而不被 TPM attestation 察覺。但這是防 offline 攻擊（物理竄改 SPI flash）的手段，對 runtime CommBuffer 攻擊沒有直接防護。

- **SMM Supervisor（AMD）**：AMD 在 EPYC 3 代引入 SMM Supervisor，把 SMM handler 分成 user/supervisor 兩層，限制 handler 能執行的操作。Intel 也有類似的 SMM isolation 研究方向（參見 2023 年 MITRE 的 SMM isolation paper）。

- **SMRAM fingerprinting**：即使 SMRAM 在非 SMM 模式下不可讀，其大小和位置可以透過 TSEG_MB 暫存器讀取（需要 PCI config space 存取）。逆向者和攻擊者可以用這個資訊推測 SMRAM 的佈局，規劃後續攻擊的記憶體策略。

## 動手練習

**練習 1：閱讀 EDK2 的 SmmIsBufferOutsideSmmValid**

```bash
wsl -e bash -lc '
git clone --depth=1 https://github.com/tianocore/edk2 /tmp/edk2 2>/dev/null || true
grep -rn "SmmIsBufferOutsideSmmValid\|InternalIsBufferToSmmCommBuffer" \
  /tmp/edk2/MdeModulePkg/Core/PiSmmCore/ | head -20
cat /tmp/edk2/MdeModulePkg/Core/PiSmmCore/PiSmmCore.h | grep -A5 "SmmIsBuffer"
'
```

找到這個函式的實作，理解它在做什麼樣的範圍檢查，以及它的侷限（它只驗證傳入的 buffer 不在 SMRAM，不驗證 buffer 內部的指標欄位）。

**練習 2：找一個真實 SMM driver 的 CommBuffer 處理**

```bash
wsl -e bash -lc '
# 找 SmmCommunication 的 Communicate handler
grep -rn "EFI_SMM_COMMUNICATE_HEADER\|CommBuffer\|MessageLength" \
  /tmp/edk2/MdeModulePkg/ -l | head -10
# 選一個檔案，看它怎麼處理 CommBuffer 的輸入
grep -n "CommBuffer\|MessageLength" \
  /tmp/edk2/MdeModulePkg/Universal/Variable/RuntimeDxe/VariableSmm.c | head -30
'
```

Variable SMM driver 是一個非常豐富的研究對象——它處理 UEFI Variable 的 SMM 端，CommBuffer 攜帶 Variable 名稱、大小、資料，歷史上出現過多個漏洞。

**練習 3：理解 CHIPSEC smm_ptr 模組的邏輯（閱讀，無需執行）**

```bash
wsl -e bash -lc '
git clone --depth=1 https://github.com/chipsec/chipsec /tmp/chipsec 2>/dev/null || true
cat /tmp/chipsec/chipsec/modules/smm_ptr.py | head -100
'
```

這個模組掃描 SMRAM 中的指標，尋找指向 SMRAM 外部的函式指標（可能是 callout 的跡象）。閱讀它的邏輯，理解攻擊者和防禦者各自在尋找什麼。

## 本章重點

- SMM 的軟體架構是三層：SmmCore → SW Dispatch Driver → Child Handler
- CommBuffer 是最主要的攻擊面：它在 SMRAM 外，完全由 OS（攻擊者）控制
- SMRAM 保護有四個層次：D_LCK（chipset）、SMRR（CPU cache）、SMM_Code_Chk_En（CPU 執行）、軟體驗證（SmmIsBufferOutsideSmmValid）
- 每個層次針對不同的攻擊向量；任一缺失都是可利用的漏洞
- CHIPSEC 模組逐一稽核這些保護機制，在真機上跑才有意義
- 最根本的問題：SMM 程式碼若對 CommBuffer 輸入做了不安全的假設，外部硬體保護全部無效

## 自我檢核

- [ ] 我能畫出 SmmCore → SW Dispatch → Child Handler 的 dispatch 鏈
- [ ] 我能說出 CommBuffer 為什麼必須在 SMRAM 外，以及這帶來什麼安全含義
- [ ] 我能解釋 SMRR、D_LCK、SMM_Code_Chk_En 各自防的是什麼攻擊
- [ ] 我能說出 `SmmIsBufferOutsideSmmValid` 的作用與侷限
- [ ] 我能列出至少 3 個 CommBuffer 中可被攻擊者利用的欄位類型

## 延伸閱讀

1. **UEFI Platform Initialization Specification, Volume 4「SMM Management Mode Core Interface」**
   - 讀哪裡：Vol.4 第 4 章（SMM Services Table）、第 5 章（Protocols for SMM）
   - 學什麼：EFI_SMM_COMMUNICATION_PROTOCOL 的正式定義、CommBuffer 格式規範、各 Dispatch protocol（Sw/Gpi/Periodic/Usb）的接口
   - 關聯：Ch 12 的 handler 分析與 CommBuffer 漏洞的法律依據

2. **「A Tour Beyond BIOS with the UEFI SMM」— Jiewen Yao, Vincent Zimmer（Intel, 2014）**
   - 讀哪裡：全篇，重點是 Figure 1（SMM architecture）和 Section 3（SMM communication）
   - 學什麼：EDK2 設計者視角的 SMM 架構說明，澄清 SmmCore 和 PiSmm driver 的分工
   - 關聯：本章架構圖的一手資料；也是 Ch 12 開始理解「什麼是正確實作」的對照基準

3. **CHIPSEC 原始碼：`chipsec/modules/common/smrr.py` 與 `chipsec/modules/smm_ptr.py`**
   - 讀哪裡：直接讀 `.py` 原始碼，尤其是 `check()` 函式
   - 學什麼：每個保護 bit 的 MSR 位址、期望值、判斷邏輯；smm_ptr 的掃描方法論
   - 關聯：本章「CHIPSEC 稽核工具」小節的實作對應；練習 B（practice-b-chipsec-audit.md）的預習

→ [下一章](./12-smm-callout-pointer.md)
