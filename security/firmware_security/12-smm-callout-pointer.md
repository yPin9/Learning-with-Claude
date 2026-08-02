# Ch 12 — SMM callout 與指標竄改

> **目標**：剖析四類經典 SMM 漏洞的機制，理解每一類的觸發條件、利用原理、以及防禦方法；能在閱讀真實韌體程式碼時識別這些模式。

## 為什麼 SMM 漏洞格外危險？

在講具體漏洞類型前，先確認一件事：SMM 漏洞的**後果不同於普通 kernel 漏洞**。

一個普通 kernel privilege escalation，讓攻擊者從 Ring 3 打到 Ring 0。很痛，但還有上層的防線（SMEP、SMAP、KASLR、CFG）；重開機後狀態清除。

一個 SMM 漏洞，讓攻擊者在 Ring -2 執行任意程式碼，結果是：

- 繞過 hypervisor（VMX root 在 Ring -1，SMM 在它下面）
- 讀寫 hypervisor 的記憶體（EPT 對 SMM 無效）
- 竄改 kernel 記憶體（以 Ring -2 做 DMA-like 任意寫）
- 在 SPI flash 寫入 bootkit（持久化；OS 重灌無法清除）
- 禁用 Secure Boot（改 SPI flash 中的 PK/db 變數）

這不是誇大。LoJax（2018）、CosmicStrand（2022）、MoonBounce（2022）這些真實 bootkit 的持久化手段，最終都需要一個 Ring -2 等級的寫入原語。SMM callout / 指標竄改就是取得這個原語的路徑。

## 四類經典 SMM 漏洞

### 類型一：SMM Callout

**定義**：SMI handler 在 SMM 環境中呼叫了一個**位於 SMRAM 外**的函式指標。

**為什麼這會發生？**

UEFI DXE 環境下有兩組全域指標，幾乎所有 DXE driver 都在用：

- `gBS`（Global Boot Services Table Pointer）：指向 Boot Services table
- `gRT`（Global Runtime Services Table Pointer）：指向 Runtime Services table

這些指標在 DXE 階段存在於正常 DRAM（SMRAM 外）。問題是：有些 SMM driver 在被轉換到 SMM 執行時，**直接沿用了 DXE phase 的 `gBS`/`gRT` 指標**，沒有切換到 SMM 版本（`gSmst->SmmInstallProtocolInterface` 等 SMM-specific API）。

**漏洞模式（概念性 pseudo-code）**：

```c
// ====== 有漏洞的 SMM driver（概念示意，非真實程式碼）======

// 在 DXE 初始化期間：
EFI_STATUS EFIAPI MyDriverEntryPoint(...) {
    // gBS 是 DXE Boot Services，指向普通 DRAM
    // 這個函式指標在 SMRAM 外
    gBS->LocateProtocol(&gSomeProtocolGuid, NULL, &SomeInterface);
    // SomeInterface 的記憶體也在 SMRAM 外
    
    // 登記 SMI handler（這是合法的）
    SwDispatch->Register(SwDispatch, MySwSmiHandler, &Context, &Handle);
}

// 在 SMM 執行期間（已進入 Ring -2）：
EFI_STATUS EFIAPI MySwSmiHandler(
    EFI_HANDLE DispatchHandle, VOID *Context, VOID *CommBuffer, UINTN *CommSize
) {
    // 問題：這裡呼叫了 gBS，但 gBS 指向 SMRAM 外的記憶體
    // 如果攻擊者在 OS 層竄改了 gBS 指向的 table，
    // 或竄改了 SomeInterface 的函式指標，
    // 下面這行就是以 Ring -2 執行攻擊者的程式碼
    SomeInterface->DoSomething(param);  // ← callout！
}
```

**利用原理**：

```
攻擊者在 OS 層（Ring 0）執行：

1. 找到 gBS 或 SomeInterface 在 DRAM 中的位址
   （可從 EFI System Table Pointer 或 UEFI runtime memory map 取得）

2. 把 SomeInterface->DoSomething 函式指標
   替換成指向攻擊者 shellcode 的位址：
   *(UINT64 *)(some_interface_addr + offset) = shellcode_addr;

3. 觸發 SW SMI（outb(smi_code, 0xB2)）

4. SMM handler 以 Ring -2 執行攻擊者的 shellcode
   Shellcode 可以：修改 SPI flash、patch kernel text、竄改 SMRAM

注意：步驟 1–2 需要 Ring 0；步驟 3 觸發 SMM；步驟 4 的執行在 SMM 中
```

**本段未實測，為理論預期行為**。驗證環境需求：有漏洞的真實韌體（未修補的舊韌體 image）、真機（CHIPSEC + SMM_Code_Chk_En 未啟用的平台）、kernel 層程式碼執行能力。

**現實中的 Callout CVE**：

- **CVE-2021-33625 / CVE-2021-33626 / CVE-2021-33627**（Insyde H2O SMM callout 系列，2021）
  - Binarly 在 Insyde 的 InsydeH2O UEFI 固件中發現多個 SMM callout，涵蓋 HP、Fujitsu、Siemens 等品牌
  - 漏洞在多個 SW SMI handler 中對 gBS/gSmst 的混用
  - CVSS 評分 8.2

- **CVE-2021-42554**（Insyde SMM callout，Lenovo IdeaPad 等）
  - 同一批研究的延伸，handler 呼叫了未受保護的 DXE 協議指標

- **PixieFail / LogoFAIL（2023）** 中也有 SMM 範圍的 callout 路徑，但主要利用鏈透過 DXE。

**緩解**：

1. SMM driver 嚴格區分 DXE phase 和 SMM phase 的 API；不在 SMM context 呼叫 `gBS`
2. 啟用 `SMM_Code_Chk_En`（Intel）：若 RIP 跑出 SMRR 保護範圍，CPU 觸發 Machine Check
3. CHIPSEC `smm_code_chk` 模組驗證這個 bit 是否已設

---

### 類型二：Confused Deputy

**定義**：SMM handler 作為一個高權限的「代理人」（deputy），接受來自低權限呼叫者（OS）的請求，但沒有正確驗證呼叫者的請求是否合法，替攻擊者做了它自己不應該做的事。

SMM 天生就是 confused deputy 的溫床：OS 觸發 SMM，SMM 代替 OS 執行 Ring -2 操作。若 SMM 沒有充分限制「我替誰做什麼事」，OS 就能用 SMM 當跳板。

**漏洞模式**：

```c
// ====== Confused Deputy SMM handler（概念示意）======

EFI_STATUS EFIAPI PrivilegedSmiHandler(..., VOID *CommBuffer, ...) {
    MY_SMM_COMM_BUFFER *Req = (MY_SMM_COMM_BUFFER *)CommBuffer;
    
    // CommBuffer 來自 OS，攻擊者完全控制
    UINT64 TargetAddr = Req->Address;   // 攻擊者控制的目標位址
    UINT32 Value      = Req->Value;     // 攻擊者控制的寫入值
    
    // SMM handler 以 Ring -2 寫入任意位址
    // 本來設計用來寫平台硬體 register（MMIO range）
    // 但沒有限制 TargetAddr 的合法範圍
    *(UINT32 *)TargetAddr = Value;      // ← Ring -2 任意寫
}
```

這個 handler 的**設計意圖**可能是讓 OS 的 ACPI / EC driver 透過 SMM 寫特定硬體暫存器，但沒有白名單檢查 TargetAddr 是否是合法的 MMIO 位址。攻擊者把 TargetAddr 指向 kernel text，就完成了一次有 SMM 背書的 kernel patch。

**利用原理**：攻擊者構造 CommBuffer，讓 SMM handler 替自己執行任意寫（或讀），達到 kernel text patch、SPI flash 寫入、或繞過 Secure Boot variable 保護。

---

### 類型三：CommBuffer 指標未驗證（SMRAM 任意讀寫）

這是最廣泛、最容易理解的 SMM 漏洞類型，在 2017–2022 年間出現在幾乎所有主要韌體廠商。

**核心問題**：CommBuffer 裡藏了一個指標，SMM handler 直接用這個指標做記憶體操作，沒有呼叫 `SmmIsBufferOutsideSmmValid()` 驗證。

**Sub-type A：指標指回 SMRAM，讀出 SMRAM 內容**

```c
// ====== 未驗證指標 → 任意讀（概念示意）======

typedef struct {
    UINT64  ReadAddr;    // 攻擊者可控
    UINT64  ReadSize;    // 攻擊者可控
    UINT8   OutputBuffer[256];  // SMM 回寫結果到這裡（在 CommBuffer 內）
} MY_READ_REQUEST;

EFI_STATUS EFIAPI MyReadSmiHandler(..., VOID *CommBuffer, ...) {
    MY_READ_REQUEST *Req = (MY_READ_REQUEST *)CommBuffer;
    
    // 漏洞：沒有驗證 Req->ReadAddr 不在 SMRAM 內
    CopyMem(Req->OutputBuffer, (VOID *)Req->ReadAddr, Req->ReadSize);
    // 若 ReadAddr 指向 SMRAM，就把 SMRAM 內容複製到 CommBuffer
    // CommBuffer 在 SMRAM 外，OS 可以讀到複製結果
}
```

攻擊者設定 `ReadAddr = SMRAM_BASE + offset`，觸發 SMI，讀取 SMRAM 中的 SMM handler 程式碼，洩漏指標、函式位址、加密金鑰（某些 BitLocker 相關 SMM driver 可能在 SMRAM 中暫存敏感資料）。

**Sub-type B：指標指回 SMRAM，攻擊者讓 SMM 幫自己寫 SMRAM**

```c
// ====== 未驗證指標 → 任意寫 SMRAM（概念示意）======

typedef struct {
    UINT64  WriteAddr;   // 攻擊者可控
    UINT64  WriteSize;   // 攻擊者可控
    UINT8   Payload[512]; // 攻擊者控制的寫入資料
} MY_WRITE_REQUEST;

EFI_STATUS EFIAPI MyWriteSmiHandler(..., VOID *CommBuffer, ...) {
    MY_WRITE_REQUEST *Req = (MY_WRITE_REQUEST *)CommBuffer;
    
    // 漏洞：沒有驗證 WriteAddr 不在 SMRAM 內
    CopyMem((VOID *)Req->WriteAddr, Req->Payload, Req->WriteSize);
    // 若 WriteAddr 指向 SMRAM，攻擊者就能修改 SMRAM 的任意內容
    // 包括 SMM handler 程式碼本身
}
```

這個漏洞讓攻擊者在不能直接讀寫 SMRAM 的情況下，透過 SMM 自身來覆蓋 SMM handler 程式碼——等同繞過 D_LCK 保護，因為是 SMM 自己在做寫入。

**`SmmIsBufferOutsideSmmValid()` 的作用**：

```c
// EDK2 提供的正確用法：
EFI_STATUS EFIAPI MyFixedSmiHandler(..., VOID *CommBuffer, ...) {
    MY_READ_REQUEST *Req = (MY_READ_REQUEST *)CommBuffer;
    
    // 先驗證 CommBuffer 本身不在 SMRAM
    if (!SmmIsBufferOutsideSmmValid((UINTN)CommBuffer, sizeof(MY_READ_REQUEST))) {
        return EFI_ACCESS_DENIED;
    }
    
    // 再驗證 ReadAddr 指向的目標不在 SMRAM
    if (!SmmIsBufferOutsideSmmValid(Req->ReadAddr, Req->ReadSize)) {
        return EFI_ACCESS_DENIED;  // 攻擊者試圖讀 SMRAM，拒絕
    }
    
    CopyMem(Req->OutputBuffer, (VOID *)Req->ReadAddr, Req->ReadSize);
    return EFI_SUCCESS;
}
```

**真實 CVE 案例**：

- **CVE-2020-3714 / CVE-2020-3715 / CVE-2020-3716 / CVE-2020-3717**（Qualcomm 韌體，2020）
  - SMM handler 直接使用 CommBuffer 內的指標進行記憶體操作，無邊界檢查

- **CVE-2021-3971 / CVE-2021-3972**（Lenovo Notebook BIOS，2021）
  - 多個 SW SMI handler 未驗證 CommBuffer 指標，可導致 SMRAM 任意讀寫
  - 影響 100+ Lenovo 型號

- **CVE-2021-28216**（AMI Aptio V，2021）
  - CommBuffer handler 允許攻擊者傳入指向 SMRAM 的指標

- **BRLY-2021-003 至 BRLY-2021-014**（Binarly，2021）
  - 針對 AMI、Insyde、Phoenix 等主要韌體廠商的 SMM handler 指標驗證漏洞批次披露

---

### 類型四：Double-Fetch / TOCTOU

**定義**：SMM handler 對同一個 CommBuffer 欄位做了兩次讀取（Time-of-Check / Time-of-Use），在兩次讀取之間，OS 修改了該欄位，導致驗證用的值和實際使用的值不一致。

**為什麼 SMM 特別容易 TOCTOU？**

CommBuffer 在普通 DRAM 中，OS 可以隨時讀寫它，包括在 SMI 執行期間（SMI 不是原子操作的：雖然 SMI 進入時 CPU 暫停 normal 模式，但**其他 CPU 的 DMA 和記憶體寫入並未停止**）。

在多核系統中：
- CPU 0 進入 SMM（由 SW SMI 觸發）
- CPU 1 仍在 Normal mode 執行 OS 程式碼（直到 SMM rendezvouz 完成）
- 在 CPU 0 的 SMM handler 兩次讀取 CommBuffer 之間，CPU 1 修改了 CommBuffer

**漏洞模式**：

```c
// ====== TOCTOU 漏洞（概念示意）======

EFI_STATUS EFIAPI MyToctoSmiHandler(..., VOID *CommBuffer, ...) {
    MY_COMM_BUFFER *Req = (MY_COMM_BUFFER *)CommBuffer;
    
    // 第一次讀取：驗證大小
    UINTN Size = Req->DataSize;   // ← 讀取 1（Time of Check）
    if (Size > MAX_ALLOWED_SIZE) {
        return EFI_INVALID_PARAMETER;  // 驗證通過
    }
    
    // ... 一些計算 ...（這段期間 OS 可以修改 Req->DataSize）
    
    // 第二次讀取：使用大小做 copy
    CopyMem(InternalBuffer, Req->Data, Req->DataSize);  // ← 讀取 2（Time of Use）
    // 若 OS 在兩次讀取之間把 DataSize 改成很大的值
    // → InternalBuffer overflow in SMRAM
}
```

**利用原理**：

```
攻擊執行緒（OS kernel thread A）：
  CommBuffer->DataSize = 32;  ← 設合法值
  outb(smi_code, 0xB2);      ← 觸發 SMI

SMM（CPU 0）：
  讀取 DataSize = 32；驗證通過

OS kernel thread B（在 CPU 1 執行）：
  CommBuffer->DataSize = 0x10000;  ← 在 SMI 執行中競態修改

SMM（CPU 0，繼續）：
  讀取 DataSize = 0x10000；做 CopyMem → overflow
```

在單核 QEMU 環境下這種 race 幾乎無法觸發（SMI 進入期間 QEMU 不模擬真正的多核）；在真機多核環境中可能需要大量嘗試，但現代工具（如 BIOS fuzzer 配合硬體計時器）可以提高成功率。

**修補方式**：把 CommBuffer 的關鍵欄位在第一次讀取後複製到 SMRAM 內的臨時緩衝區，後續操作只使用 SMRAM 內的副本：

```c
// 正確做法：複製到 SMRAM 內
MY_COMM_BUFFER LocalReq;
CopyMem(&LocalReq, CommBuffer, sizeof(MY_COMM_BUFFER));  // 一次複製
// 之後所有操作使用 LocalReq，不再碰 CommBuffer
```

## 各漏洞類型總覽

```
攻擊者（OS kernel，Ring 0）
│
├──[CallOut]──────────────────────────────────────────────────
│  OS 竄改 gBS 或協議介面的函式指標（DRAM，OS 可寫）
│  → 觸發 SMI → SMM handler 呼叫被竄改的指標
│  → 以 Ring -2 執行攻擊者的 shellcode
│
├──[ConfusedDeputy]───────────────────────────────────────────
│  OS 構造 CommBuffer，把 TargetAddr 設成 kernel text 或 SPI
│  → SMM handler 以 Ring -2 執行攻擊者想做的寫入操作
│
├──[Pointer Corruption]───────────────────────────────────────
│  OS 在 CommBuffer 裡放一個指向 SMRAM 的指標
│  → SMM 對該指標做讀/寫操作
│  ├─ 讀：洩漏 SMRAM 內容（handler code、金鑰）
│  └─ 寫：覆蓋 SMRAM 中的 handler code → 控制 SMM 執行流
│
└──[TOCTOU]────────────────────────────────────────────────────
   OS 在 SMI 執行中（另一顆 CPU）修改 CommBuffer 的 Size/Index
   → SMM 驗證通過但用到被竄改的值
   → SMRAM 中的 overflow 或 OOB 存取
```

## 真實漏洞利用鏈的邏輯

**本段為理論預期行為，未實測。驗證環境要求：有已知漏洞韌體映像（未修補版）的真機、kernel 程式碼執行能力（Ring 0）、CHIPSEC 安裝。**

一條完整的利用鏈通常不會只用一種漏洞類型，而是組合：

```
Step 1：從 OS Ring 0 觸發 SW SMI（0xB2）
         只需要 iopl(3) 或 /dev/port 存取

Step 2：用 CommBuffer 指標未驗證漏洞
         做任意讀，洩漏 SMRAM 中的 handler 地址
         → 破解 SMRAM 的 ASLR（如果有的話）

Step 3：用 CommBuffer 寫漏洞或 TOCTOU
         在 SMRAM 的 handler 進入點植入 shellcode
         （覆蓋函式指標或 code 本身）

Step 4：再次觸發 SMI
         SMM 以 Ring -2 執行攻擊者的 shellcode

Step 5：Shellcode 在 SMM 中執行
         選擇：直接呼叫 SPI flash 寫入 API（持久化）
              或修改 UEFI Variable（Secure Boot 繞過）
              或 patch OS kernel（ringkit）
```

這套流程只在以下條件全部滿足時可行：
- 攻擊者有 Ring 0 執行能力
- 目標平台的 SMM handler 有指標驗證漏洞
- `SMM_Code_Chk_En` 未啟用（否則 Step 4 會觸發 MC）
- SPI flash 寫保護未鎖定（否則 Step 5 的持久化失敗）

每一個條件都是一道防線。現代已修補的平台通常至少有 2–3 道，讓完整鏈變得困難但不是不可能。

## 防禦方向總整理

| 攻擊類型 | 程式碼層防禦 | 平台層防禦 |
|---------|------------|-----------|
| Callout | 不在 SMM context 使用 gBS/gRT；使用 gSmst | SMM_Code_Chk_En 啟用 |
| Confused Deputy | 白名單驗證目標位址範圍 | — |
| 指標未驗證 | 呼叫 SmmIsBufferOutsideSmmValid；複製到 local | — |
| TOCTOU | 一次複製 CommBuffer 到 SMRAM local copy | — |
| 通用 | SMM handler 最小化；每個輸入欄位的合法性驗證 | D_LCK、SMRR 正確設定 |

## 踩雷紀錄

**坑 1：以為修了指標驗證就沒事**
`SmmIsBufferOutsideSmmValid` 只驗證位址範圍不在 SMRAM，但它不驗證「位址是否在合法的 OS 記憶體映射內」。攻擊者可以把指標指向 MMIO 位址（硬體暫存器），讓 SMM handler 讀取敏感硬體狀態或觸發硬體副作用。完整的防禦需要白名單，不只黑名單。

**坑 2：Callout 在 VMware/Hyper-V guest 也有影響**
SMM callout 讓攻擊者從 VM guest 的 Ring 0 最終執行 Ring -2 程式碼，而 Ring -2 在 hypervisor 之下——意味著攻擊者從 VM 內部打穿了 hypervisor 的隔離。部分廠商的修補只考慮了裸機場景，沒有考慮虛擬化環境。

**坑 3：TOCTOU 在單核 QEMU 幾乎不可觸發**
QEMU 的 TCG 模式下，SMI 執行期間其他「核心」不會真正並行。所以在 QEMU 中測不出 TOCTOU 不代表真機沒有這個漏洞。評估時需要在真機多核環境下壓測。

**坑 4：混淆 SMM callout 和 DXE callout**
DXE phase 也有 callout 問題（DXE driver 呼叫 PEI 遺留的資料結構），但 DXE callout 的影響在 Ring 0，沒有 SMM callout 的 Ring -2 影響。閱讀 CVE 描述時要確認是哪個階段的 callout。

**坑 5：以為 SMRAM ASLR 能抵擋利用**
部分平台對 SMRAM 的 base 做了隨機化（TSEG 的位置在每次開機時不同），讓攻擊者無法預先知道 handler 位址。但若有任意讀漏洞（指標洩漏），ASLR 就失效了。SMRAM ASLR 是提高難度，不是根本防禦。

## 進階延伸

- **SMM Privilege Separation（研究方向）**：學術界和 Intel 都在研究把 SMM handler 進一步拆成 user/supervisor 層，讓大部分 handler 在受限的沙盒中執行，只有核心操作可以呼叫 privileged SMM API。這類似 OS 的 syscall 設計，但在 Ring -2 層實作。

- **Firmware fuzzing 的 CommBuffer 向量**：UEFI 韌體 fuzzer（如 Riscure 的 UEFI fuzzer、Binarly 的 FwAnalyzer）會系統性地對每個已知 GUID 的 SMM handler 發送畸形 CommBuffer，自動尋找指標驗證缺失。這是目前發現 SMM 漏洞效率最高的方法。

- **Intel TDX（Trust Domain Extensions）對 SMM 的改動**：TDX VM 中的 SMM 行為被進一步隔離，SMM 不能存取 TDX VM 的加密記憶體（TDRAM）。這不是修好了 SMM 漏洞，而是限制了 SMM 的爆炸半徑。

- **AMD SMM 的類似問題**：AMD 平台的 SMM 攻擊面與 Intel 大體相同（CommBuffer、SWSMI、SMRR 等），但具體暫存器名稱和 chipset 介面不同。Binarly 在 2022 年也披露了 AMD 韌體的 SMM 指標問題。

## 動手練習

**練習 1：在 EDK2 原始碼中識別 Callout 模式（閱讀練習）**

```bash
wsl -e bash -lc '
git clone --depth=1 https://github.com/tianocore/edk2 /tmp/edk2 2>/dev/null || true
# 搜尋 SMM driver 中對 gBS 的使用（這些可能是 callout 候選）
grep -rn "\bgBS\b" /tmp/edk2/MdeModulePkg/ \
  --include="*Smm*.c" --include="*smm*.c" | \
  grep -v "//\|^\s*/\*" | head -20
echo "---"
# 對照正確的 SMM API 用法
grep -rn "gSmst\|SmmInstallProtocolInterface" \
  /tmp/edk2/MdeModulePkg/Core/PiSmmCore/ | head -10
'
```

目標：找到在 SMM context 中仍然使用 `gBS` 的地方（若存在），和使用正確 `gSmst` 的地方做對比，理解什麼是 callout 的來源。

**練習 2：追蹤 SmmIsBufferOutsideSmmValid 的呼叫路徑**

```bash
wsl -e bash -lc '
# 找所有有使用 CommBuffer 但沒有呼叫驗證函式的 handler（概念性搜尋）
cd /tmp/edk2
FILES=$(grep -rln "CommBuffer" MdeModulePkg/ --include="*Smm*.c")
for f in $FILES; do
    HAS_COMM=$(grep -c "CommBuffer" "$f")
    HAS_VALID=$(grep -c "SmmIsBufferOutsideSmmValid\|SmmIsBufferToSmmCommunicateHeader" "$f" || true)
    if [ "$HAS_COMM" -gt 0 ] && [ "$HAS_VALID" -eq 0 ]; then
        echo "可能缺少驗證: $f (CommBuffer uses: $HAS_COMM)"
    fi
done
'
```

這個搜尋不是精確的漏洞掃描（有的 handler 用其他方式驗證），但能讓你看到哪些檔案值得深入審計。

**練習 3：閱讀 Binarly 的 CVE 披露報告**

不需要實際環境。閱讀以下任一份 Binarly 的公開 SMM 研究報告：

- https://binarly.io/posts/Finding_SMM_Privilege_Escalation_Vulnerabilities_in_UEFI_Firmware/ （指標驗證問題的方法論）
- CVE-2021-3971/3972 的 Lenovo 公告

目標：理解研究者如何系統性識別這些漏洞，以及 CVSS 評分如何計算（SMM 漏洞通常要求 local + Ring 0 前置條件，所以 attack vector 是 Local，但 scope 是 Changed）。

## 本章重點

- SMM Callout：handler 在 Ring -2 呼叫 SMRAM 外的函式指標，攻擊者預先竄改該指標即可控制執行流
- Confused Deputy：SMM handler 沒有驗證操作目標的合法性，被攻擊者借刀殺人做任意讀寫
- 指標未驗證：CommBuffer 中的指標若指向 SMRAM 內部，handler 會替攻擊者讀寫 SMRAM，繞過 D_LCK
- TOCTOU：多核環境下 OS 在 SMI 執行中修改 CommBuffer 欄位，讓驗證與使用的值不一致
- 正確修補：一次 copy CommBuffer 到 local；呼叫 SmmIsBufferOutsideSmmValid；啟用 SMM_Code_Chk_En
- 這些漏洞不是理論，CVE-2021-33625/33626、CVE-2021-3971/3972 等已在真實韌體中確認

## 自我檢核

- [ ] 我能解釋 SMM callout 是如何讓攻擊者從 Ring 0 取得 Ring -2 執行能力
- [ ] 我能說出 `gBS` 在 SMM context 使用時為什麼是危險的
- [ ] 我能畫出 CommBuffer 指標漏洞（Sub-type A 任意讀、Sub-type B 任意寫）的觸發流程
- [ ] 我能解釋 TOCTOU 在多核環境的觸發條件，以及正確的修補方式
- [ ] 我能說出 `SMM_Code_Chk_En` 緩解了哪個漏洞類型、對哪些無效

## 延伸閱讀

1. **「Vulnerability in Firmware：An Attacker's and a Defender's Perspective」— Alex Matrosov, Binarly（2021 Black Hat）**
   - 讀哪裡：slides 的 SMM 章節（約 p.25–55），關注 SMM callout 和指標竄改的案例
   - 學什麼：Binarly 如何用靜態分析工具系統性找 SMM handler 的指標驗證問題；CVSS 的計算方式；廠商如何在修補時仍留下 variant
   - 關聯：本章四個漏洞類型的業界實戰版本，提供真實韌體的對應例子

2. **CVE-2021-33625 系列（Insyde H2O SMM Callout）—— Binarly Security Advisories**
   - 讀哪裡：https://binarly.io/advisories/ 找 BRLY-2021-xxx 系列
   - 學什麼：SMM callout 的具體觸發路徑、受影響品牌清單（HP/Lenovo/Fujitsu/Dell）、修補 commit 的 diff 分析
   - 關聯：「類型一：SMM Callout」小節的真實案例對應

3. **EDK2 SecurityPkg 的 SMM 安全 README**
   - 讀哪裡：`edk2/SecurityPkg/Readme.md` + `MdeModulePkg/Core/PiSmmCore/PiSmmCore.c` 裡的 `SmmIsBufferOutsideSmmValid` 函式
   - 學什麼：EDK2 維護者對 SMM 安全的設計原則；`SmmIsBufferOutsideSmmValid` 的實作邏輯（它掃描 SMRAM 範圍列表，若位址在任何 SMRAM 段內則返回 FALSE）
   - 關聯：「類型三：指標未驗證」的防禦方向，也是 Ch 11 攻擊面章節的延伸

→ [下一章](./13-smm-exploitation.md)
