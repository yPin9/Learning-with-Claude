# Ch 30 — 真實利用鏈剖析：BootHole / BlackLotus / LogoFAIL

> **目標**：把三條公開的 Secure Boot 繞過鏈逐一拆解到技術原語層級——入口是什麼、漏洞在哪裡、如何劫持控制流、如何持久化。每條鏈給 ASCII 流程圖與類型學歸類（T1-T6，Ch 21/Ch 29），讓你看到「公開報告的漏洞」和「研究者工具箱的類型」之間的精確對應。

本章內容完全基於已公開的學術報告、安全研究白皮書與 CVE 說明。目的是理解已知攻擊的技術原理，以改善防禦設計。

---

## 為什麼要拆真實利用鏈？

類型學（Ch 21/Ch 29）是骨架，真實鏈才是肌肉。光知道「T3b 是 length confusion」沒用，你需要知道：

- 攻擊者在哪個二進位找到漏洞
- 漏洞觸發需要什麼前提條件（物理存取？OS root？只要能改檔案？）
- 控制流從使用者可控的資料到達什麼程度的執行上下文
- 廠商怎麼修（以及為什麼修了還是不夠）

三條鏈的公開程度差距很大：BootHole 有完整 PoC（CVE-2020-10713）、BlackLotus 有 ESET 的完整反向分析報告、LogoFAIL 有 Binarly 的技術論文。三條鏈合起來涵蓋了 T3b、T4、T5 三種主要類型，以及「資料檔繞簽章」這個 Secure Boot 最核心的設計矛盾。

---

## 鏈一：BootHole（CVE-2020-10713）

### 背景

2020 年 7 月，Eclypsium 公開 CVE-2020-10713，影響幾乎所有使用 GRUB2 的 Linux 發行版。在 Secure Boot 啟用的系統上，GRUB2 的 bootloader shim 已被 Microsoft/Canonical 等 CA 簽章——這意味著 db 數據庫認可它。問題是：GRUB2 的 **grub.cfg 設定檔**本身沒有被簽章，卻被已簽章的 GRUB2 解析執行。

這是 T3b（length confusion）和 T5（替代路徑）的組合，搭配 T3（資料檔污染）的精神——合法的執行者解析攻擊者可控的資料。

### 漏洞機制

GRUB2 在解析 `grub.cfg` 時，從設定檔讀取 menuentry 等命令，並用 yacc/flex 風格的 parser 處理字串。問題出在 `grub_parser_split_cmdline()` 函數：

```c
/* GRUB2 grub/lib/cmdline.c（示意，非完整原始碼）*/
static grub_err_t
grub_parser_split_cmdline(const char *cmdline, ...)
{
    /* 計算所需 buffer 大小：遍歷命令列計算 token 數量 */
    int count = 0;
    /* ... 第一次遍歷計算 count 和 buffer size ... */

    /* 分配 buffer 並第二次遍歷填入 token */
    char *buffer = grub_malloc(size);

    /* 漏洞：兩次遍歷的計算邏輯不一致，
       某些 escape sequence 在計算時算 2 bytes，
       在複製時只佔 1 byte，造成 buffer 比實際需要的大，
       BUT：另一個路徑的計算反而 undercount，
       最終寫入超過分配大小 → heap overflow */
    while (*p) {
        if (is_escape(*p)) {
            /* 計算階段：+2；複製階段邏輯差異造成 off-by-one */
        }
    }
}
```

類型學歸類：**T3b（Length Confusion）**——負責計算大小的程式碼路徑和負責複製的程式碼路徑對 escape sequence 的計算不一致，造成 heap overflow。

攻擊者不需要修改 GRUB2 二進位（那有簽章），只需要替換 `grub.cfg`——這是一個**未簽章的設定檔**，存放在 ESP（EFI System Partition）上。

### 利用鏈流程圖

```
攻擊者控制 grub.cfg（ESP 上，未簽章，OS 層可寫）
│
│  Secure Boot 的問題：
│  UEFI db 信任 shim.efi（已簽章）
│  shim 信任 grubx64.efi（已由 CA 簽章）
│  grubx64.efi 解析 grub.cfg → 沒有簽章驗證
│
▼
grub.cfg 中的惡意 menuentry，觸發
grub_parser_split_cmdline() 的 heap overflow
│
▼
在 GRUB2 的 heap 上覆蓋關鍵資料
（函數指標、metadata、grub_module 結構）
│
▼
控制流劫持：GRUB2 在 UEFI boot services 仍活著的階段執行任意程式碼
（此時 SecureBoot 驗簽已完成，但 ExitBootServices 尚未呼叫）
│
▼
攻擊者程式碼執行於 UEFI 環境：
  ├── 載入未簽章的 OS kernel（繞過 Secure Boot 的根本目的）
  ├── 操控 UEFI variable（安裝惡意 BootEntry）
  └── 竄改記憶體中的 Secure Boot 狀態（將 SecureBoot variable 改為 0）
│
▼
持久化：修改 BootOrder 或安裝惡意 EFI，之後開機不依賴 exploit
```

### 為什麼這是設計矛盾

Secure Boot 的信任模型是：
1. 只執行有 db 中受信任 CA 簽章的 EFI binary
2. GRUB2 是合法簽章的 EFI binary

但 GRUB2 設計上允許讀取 ESP 上的 `grub.cfg` 來決定開機行為——而 `grub.cfg` 沒有被簽章。這不是 GRUB2 的 bug，這是 Secure Boot 的**信任邊界定義問題**：「已驗證的 binary 解析未驗證的資料」。

換句話說：即使沒有 heap overflow，攻擊者也可以用 grub.cfg 的合法命令（`linux`、`initrd`、`insmod`）載入任意 kernel。Heap overflow 只是讓攻擊在更廣泛的情境下成立（包括有限的 grub.cfg 白名單）。

類型學：**T3b（Length Confusion）+ T5（替代路徑：grub.cfg 不在 Secure Boot 信任鏈內）**

### 緩解與 SBAT 的誕生

修補 CVE-2020-10713 的 buffer overflow 只是第一步。真正的問題是：如何讓所有**已有洞的舊版 GRUB2**失效？

用 dbx 加 hash 不現實——每個 distro、每個版本都是不同的 binary，數百個 hash。這直接促成了 **SBAT（Secure Boot Advanced Targeting）** 機制的設計，細節在 Ch 32。

| 元素 | 細節 |
|------|------|
| CVE | CVE-2020-10713 |
| 影響範圍 | 幾乎所有啟用 Secure Boot 的 Linux |
| 類型學 | T3b + T5 |
| 入口 | ESP 上的 grub.cfg（OS 層可寫） |
| 漏洞位置 | GRUB2 `grub_parser_split_cmdline()` heap overflow |
| 執行上下文 | UEFI Boot Services 階段（Ring 0 equivalent，Secure Boot 已繞過）|
| 撤銷機制催生 | SBAT generation-based revocation |

---

## 鏈二：BlackLotus（CVE-2022-21894）

### 背景

2023 年 3 月，ESET 公開了 BlackLotus 的完整分析——第一個被研究者捕獲的、**在 Windows 上實際部署**的 UEFI bootkit，能在 Secure Boot 啟用的機器上執行。它利用的核心是 **CVE-2022-21894**，俗稱 **"baton drop"**。

BlackLotus 不是漏洞利用的故事，是**設計缺陷加上撤銷失敗**的故事：T4（Rollback 未防護）。

### 漏洞機制：baton drop

Windows 的 Secure Boot 信任鏈：
1. UEFI firmware 驗 Windows Boot Manager（`bootmgfw.efi`）→ 在 db 中
2. `bootmgfw.efi` 驗 `winload.efi`（OS loader）
3. `winload.efi` 驗 kernel

問題出在第 1 → 2 步的交接（baton pass / baton drop）。Windows Boot Manager 有一個功能：支援 **legacy MBR boot path** 的相容性 shim，在特定條件下可以「放棄 Secure Boot 的強制驗章」而繼續開機。

CVE-2022-21894 的精確機制（依 ESET 分析）：

```
攻擊者在 Windows Recovery 路徑上放置一個特製的 BCD（Boot Configuration Data）
↓
bootmgfw.efi 解析 BCD，選擇進入 Recovery Environment 的分支
↓
在 Recovery 路徑的某個轉換點，bootmgfw.efi 呼叫
EFI_BOOT_SERVICES.ExitBootServices() 之後才驗證 winload.efi
↓
「Baton drop」：在 ExitBootServices 後，Secure Boot 的強制驗章
（MokManager / shim 的機制）已經失效
↓
此時可以載入未簽章的 winload.efi
```

更直接的解讀：**攻擊者使用的是合法版本的 `bootmgfw.efi`**（在 db 中，有效簽章），但這個版本包含 CVE-2022-21894。Microsoft 後來把有洞版本加入 dbx，但...

### 核心問題：為什麼 dbx 撤銷沒用？

BlackLotus 的精髓在於**降級攻擊（Downgrade / Rollback）**：

```
Microsoft 發現 CVE-2022-21894
↓
2022 年 1 月 patch：新版 bootmgfw.efi 修了漏洞
↓
Microsoft 沒有立刻把舊版加入 dbx（因為會 brick 雙開機機器）
↓
攻擊者下載有漏洞的舊版 bootmgfw.efi（公開可得，任何 Windows ISO）
↓
替換目標機器 ESP 上的 bootmgfw.efi 為舊版
↓
UEFI 驗章：舊版有合法 Microsoft 簽章，db 中存在 → 通過
↓
BlackLotus 利用 CVE-2022-21894 繞過 Secure Boot
```

類型學：**T4（Rollback 未防護）**——撤銷機制（dbx）沒有即時跟上，且回滾到有漏洞的版本技術上完全合法。

### 利用鏈流程圖

```
攻擊者已有 OS 層讀寫 ESP 的能力
（Windows admin 或 local privilege escalation）
│
▼
步驟 1：從公開 Windows ISO 取得有 CVE-2022-21894 的舊版 bootmgfw.efi
（有 Microsoft 合法簽章，在 db 受信任）
│
▼
步驟 2：替換 ESP:\EFI\Microsoft\Boot\bootmgfw.efi
（不需要繞過任何驗章，因為舊版本身就在 db 裡）
│
▼
步驟 3：重開機，UEFI 載入舊版 bootmgfw.efi → db 驗章通過
│
▼
步驟 4：攻擊者利用 CVE-2022-21894 的 baton drop，
在 Recovery 路徑上觸發，放棄後續的驗章強制
│
▼
步驟 5：載入惡意的 winload.efi（BlackLotus 本體的一部份）
│
▼
步驟 6：惡意 winload.efi 在 Secure Boot 繞過後的環境裡：
  ├── 關閉 Windows Defender 的 early-launch anti-malware
  ├── 停用 Kernel Patch Protection（KPP/PatchGuard）
  ├── 安裝 kernel driver（因為 DSE/Driver Signing 已繞過）
  └── 安裝用於 C2 通訊的 HTTP kernel bootkit
│
▼
持久化：
  ├── 在 ESP 寫入自訂的 EFI binary（BlackLotus loader）
  ├── 修改 BootOrder，確保每次開機都載入 BlackLotus
  └── BlackLotus loader 每次開機前執行 Stage 1 → 再載入真實 OS
```

### 軍備競賽：撤銷的代價

BlackLotus 曝光後，Microsoft 面臨兩難：

- 把所有有 CVE-2022-21894 的 `bootmgfw.efi` 加入 dbx → 會讓使用舊 Windows ISO 開機的機器（例如 WinPE rescue disk）失效，可能 brick 雙開機系統
- 不加 → BlackLotus 繼續有效

最終 Microsoft 在 2023 年 5 月和 7 月分批更新 dbx，同時警告使用者確保 Windows 已更新。這場「revoke 舊版 vs. 不 brick 使用者」的軍備競賽直接推動了 Ch 32 要深入的議題。

| 元素 | 細節 |
|------|------|
| CVE | CVE-2022-21894（baton drop） |
| 影響範圍 | 啟用 Secure Boot 的 Windows 系統 |
| 類型學 | T4（Rollback 未防護）+ T5（Recovery path 的信任繞過） |
| 入口 | 需要 OS 層 admin 權限或能改 ESP |
| 攻擊核心 | 舊版合法簽章的 bootmgfw.efi 仍被 db 信任 |
| 執行上下文 | UEFI 到 OS 轉換階段（Secure Boot 強制驗章已繞） |
| 撤銷困難 | dbx 批次更新風險高，分批推出時存在時間窗 |

### BlackLotus 的構造補充

BlackLotus 的持久化層（Ch 31 會深挖）分為三個 Stage：

```
Stage 1：ESP bootloader（每次開機執行）
    → 驗 Stage 2 完整性
    → 觸發 CVE-2022-21894
Stage 2：Windows bootloader patch（記憶體中）
    → 載入 Stage 3
Stage 3：Kernel-mode driver（.sys）
    → C2 通訊
    → 關閉防禦機制
```

注意：BlackLotus 在初次安裝時，Stage 1 也**會把自己加入 MOK（Machine Owner Key）資料庫**（在某些設定下），確保重開機後 shim 仍然信任它的 EFI。這是 T6（公開金鑰 / 自签金鑰濫用）的變形。

---

## 鏈三：LogoFAIL

### 背景

2023 年 12 月，Binarly 在 BlackHat Europe 公開了 LogoFAIL——一類**UEFI image parser 漏洞**，影響範圍橫跨 AMI、Insyde、Phoenix 三大 UEFI firmware 廠商，幾乎涵蓋所有 x86 廠牌（Intel、AMD、ARM）的消費級與企業級設備。

LogoFAIL 的類型學歸屬是 **T3（驗簽邏輯漏洞）加資料檔污染**，但它的攻擊面更特別：入口是**開機 Logo 圖片**。

### 攻擊面：為什麼 Logo 是問題？

UEFI 在 DXE 階段要顯示 OEM logo（廠商標誌），於是 firmware 內建了 BMP、PNG、JPEG 等格式的 image parser。這些 parser 讀取的圖片檔案通常存放在：

1. **UEFI firmware volume 裡**（FV 中的 Logo.bmp，ROM 的一部份）
2. **ESP 上**（某些 UEFI 允許從 ESP 讀取自訂 logo）
3. **NVRAM variable 裡**（部分廠商把 logo 存在 NVRAM）

問題在於：這些 parser 是攻擊者可以觸及的，且它們在 **DXE 階段執行**——此時 Secure Boot 還沒到驗章環節，ExitBootServices 也還沒呼叫。

### 漏洞機制

Binarly 找到了多個不同類型的記憶體破壞漏洞，分佈在不同廠商的不同 parser：

```c
/* 示意：BMP parser 的典型漏洞模式（非特定廠商原始碼）*/

typedef struct {
    uint16_t  Signature;     // "BM"
    uint32_t  FileSize;
    uint32_t  Reserved;
    uint32_t  DataOffset;    // ← 攻擊者可控
    uint32_t  HeaderSize;
    int32_t   Width;
    int32_t   Height;
    uint16_t  ColorPlanes;
    uint16_t  BitsPerPixel;
    /* ... */
} BMP_HEADER;

EFI_STATUS ParseBmpLogo(UINT8 *RawData, UINTN DataSize)
{
    BMP_HEADER *Header = (BMP_HEADER *)RawData;

    /* 漏洞 A：DataOffset 無上界驗證 */
    UINT8 *PixelData = RawData + Header->DataOffset;
    /* DataOffset 若超過 DataSize，PixelData 指向 UEFI 記憶體其他區域 */

    /* 漏洞 B：Width * Height * BytesPerPixel 整數溢位 */
    UINTN BufferSize = Header->Width * Header->Height * (Header->BitsPerPixel / 8);
    UINT8 *Buffer = AllocatePool(BufferSize);
    /* 若 Width=65536, Height=65536, BPP=1 → BufferSize = 0（整數溢位）*/
    /* 然後 CopyMem(Buffer, PixelData, 實際大小) → heap overflow */

    CopyMem(Buffer, PixelData, /* 實際像素大小 */);
}
```

### 利用鏈流程圖

```
攻擊者目標：在 UEFI DXE 階段（Secure Boot 驗章前）執行任意程式碼
│
▼
步驟 1：找到目標 logo 的存放位置
  ├── ESP 上（最易存取，OS admin 即可改）
  ├── NVRAM variable（需要 UEFI variable 寫入能力）
  └── ROM 中的 Logo FV（需要 SPI 寫入能力，或 capsule update）
│
▼
步驟 2：製作惡意 BMP/PNG/JPEG
  - 設定觸發 heap overflow 的 header 欄位
  - pixel data 部分包含 shellcode 或 ROP gadget chain
│
▼
步驟 3：替換目標 logo 檔案
  （不需要簽章：logo 不在 Secure Boot 的驗章路徑上）
│
▼
步驟 4：重開機
  UEFI DXE 階段：LogoDxe driver 或 BmpSupportLib 解析 logo
  └── 觸發 heap overflow / 越界讀寫
      └── 覆蓋 DXE 記憶體中的函數指標或 protocol handler
│
▼
步驟 5：控制流劫持
  在 DXE 階段（Ring 0 equivalent）執行任意 UEFI 程式碼
  此時：
  ├── Secure Boot 驗章尚未開始（DXE 比 Secure Boot 更早）
  ├── SMRAM 可能尚未鎖定（取決於 DXE 執行順序）
  └── 可修改 UEFI 記憶體中的 Secure Boot 狀態
│
▼
步驟 6：持久化
  ├── 在 NVRAM 寫入惡意 Boot 項目（BootOrder）
  ├── 透過 SPI 寫入（如果 SPI 保護未啟用）
  └── 修改 db/dbx（如果 Secure Boot variable 尚未鎖定）
│
▼
結果：之後每次開機，Secure Boot 的驗章被繞過，
惡意程式碼在 OS 啟動前就已執行
```

### 「資料檔繞簽章」的本質

LogoFAIL 揭示了 Secure Boot 的第二個設計矛盾（第一個是 BootHole 的 grub.cfg）：

**Secure Boot 驗的是 EFI binary，但 EFI binary 解析的資料檔沒有被驗章。**

```
信任邊界示意：

  [UEFI db] ─驗簽─▶ [LogoDxe.efi] ─解析─▶ [Logo.bmp]
                         ✓ 已驗章              ✗ 未驗章
                                               ↑
                                         攻擊者可以替換這個
```

攻擊者不需要破解 LogoDxe.efi 的簽章，只需要讓 LogoDxe.efi 解析一個惡意的 Logo.bmp。這個攻擊向量的通用性非常高：任何「已簽章的 binary 解析未簽章的資料」都是潛在的 LogoFAIL 型攻擊面。

類型學：**T3（驗簽邏輯問題：信任邊界止於 binary，未覆蓋其解析的資料）+ T5（替代路徑：logo 解析在 Secure Boot 驗章流程之外）**

### 影響範圍與嚴重性

| 元素 | 細節 |
|------|------|
| 發現者 | Binarly（2023 年 12 月，BlackHat Europe）|
| 影響廠商 | AMI、Insyde、Phoenix 的 firmware；覆蓋大多數 x86 裝置 |
| 類型學 | T3（資料檔 parser 漏洞）+ T5（logo 不在 Secure Boot 驗章路徑）|
| 入口 | ESP 上的 logo 檔（OS admin），或 NVRAM variable |
| 漏洞多樣性 | BMP/PNG/JPEG 的多個 parser 各有不同的記憶體破壞類型 |
| 執行上下文 | UEFI DXE 階段，Secure Boot 驗章之前 |
| 防禦困難 | 需要 firmware 更新；logo parser 攻擊面廣、parser 程式碼歷史悠久 |

---

## 三條鏈的並列比較

```
                  BootHole           BlackLotus          LogoFAIL
─────────────────────────────────────────────────────────────────
類型學            T3b + T5           T4 + T5              T3 + T5
CVE               CVE-2020-10713    CVE-2022-21894        多個
入口              grub.cfg（ESP）    ESP bootmgfw.efi      Logo 圖片
需要 OS 層能力？  是（改 ESP）       是（admin）           是（改 ESP/NVRAM）
需要破解簽章？    否                 否                    否
漏洞位置          GRUB2 heap        bootmgfw.efi baton    UEFI image parser
執行時機          UEFI boot svc     UEFI→OS 轉換          UEFI DXE
持久化強度        NVRAM/ESP         ESP + kernel driver   NVRAM/SPI
撤銷手段          SBAT + dbx        dbx（有 brick 風險）  firmware update
─────────────────────────────────────────────────────────────────
共同點：三條鏈都不需要破解任何簽章，利用的都是信任邊界的不完整性。
```

### 信任邊界的三種裂縫

```
裂縫 1（BootHole/LogoFAIL）：
  「已驗章的 binary 解析未驗章的資料」
  → 驗章邊界止於 binary 本身，binary 讀取的資料不在驗章覆蓋範圍

裂縫 2（BlackLotus）：
  「已撤銷的 binary 在 dbx 更新前仍然有效」
  → 信任的實時性問題：舊版本的信任應該隨時被撤銷，但撤銷有成本

裂縫 3（三條鏈共有）：
  「信任鏈的強度等於最弱的環節」
  → 只要有一個未驗章的入口（設定檔/logo/舊版binary），
    整條 Secure Boot 信任鏈就可以被繞過
```

---

## 防禦角度的教訓

| 教訓 | 技術含義 | 對應措施 |
|------|---------|---------|
| 驗章要覆蓋資料，不只是 binary | grub.cfg、Logo.bmp 都要加入信任鏈 | GRUB2 signed configs；Logo 要有 hash 或放進 FV |
| 撤銷機制要即時且低成本 | dbx 更新的 brick 風險讓廠商不敢快速撤銷 | SBAT generation-based revocation |
| 信任模型要考慮降級攻擊 | 舊版本的信任應該有 time-limited 性質 | Anti-rollback + 定期 dbx 更新 |
| parser 是巨大的攻擊面 | 韌體的 BMP/PNG/JPEG parser 有幾十年技術債 | 最小 parser（只支援簡單 BMP）、fuzzing、記憶體安全語言重寫 |
| 縱深防禦：假設 UEFI 被攻破 | Secure Boot 只是一層 | BootGuard（保護 BIOS ROM）+ measured boot（TPM 記錄） |

---

## 踩雷

1. **把 Secure Boot 等同於完整安全**：BootHole/BlackLotus/LogoFAIL 三條鏈全部在 Secure Boot 啟用的機器上成立。Secure Boot 防的是「完全未簽章的 binary 被執行」，不能防「已簽章 binary 的漏洞」或「信任邊界定義不完整」。

2. **以為 ESP 有存取限制**：在 Windows 和 Linux 上，admin/root 都可以掛載 ESP 並寫入。Secure Boot 不保護 ESP 上的資料，只驗章 EFI binary 在被執行之前。

3. **混淆「驗章通過」和「程式碼正確」**：BlackLotus 使用的 bootmgfw.efi 確實驗章通過，但它有 CVE-2022-21894。驗章只保證 origin（這個 binary 確實由 Microsoft 簽發），不保證它沒有漏洞。

4. **低估 DXE 的攻擊面**：LogoFAIL 的執行時機是 DXE 階段，比 Secure Boot 的 bootloader 驗章還早。UEFI 的 DXE 執行了大量功能性程式碼（顯示、網路、硬碟），每一個都是潛在的攻擊面。

5. **認為更新 BIOS 就夠了**：LogoFAIL 的某些變體，如果 logo 存在 NVRAM，韌體更新後 NVRAM 的惡意 logo 可能仍然存在（NVRAM 不被 firmware update 清除）。需要同時清除 NVRAM 或重設到出廠設定。

6. **忽略 rollback 的時間窗口**：BlackLotus 從 CVE 公開（2022 年 1 月 patch）到 dbx 更新推送（2023 年 5 月）有超過一年的時間窗。在 dbx 更新前，所有機器都可能被降級攻擊。

---

## 進階延伸

- **LogoFAIL 的 fuzzing 方法論**：Binarly 用 custom fuzzer 對 UEFI image parser 做 coverage-guided fuzzing，找出多個廠商的多個 parser 漏洞。他們的方法論（emulation-based UEFI fuzzing）直接對應 advanced_fuzzing 課程的 firmware rehosting 章節。

- **SBAT 的 generation model**：SBAT 如何解決「N 個有洞版本需要 N 個 dbx hash」的問題，Ch 32 深挖。簡短版本：SBAT 在 binary 中加入 generation 元數據，revocation 只需要更新「最低可接受 generation」，而非列出所有有洞版本。

- **BlackLotus 的 VHD 部署手法**：BlackLotus 的初始安裝利用了 Windows 的 VHD/VHDX 開機功能——把惡意 ESP 放在 VHD 中，讓 Windows 開機時掛載。這個手法避開了直接寫 ESP 需要的部分防護。完整分析在 ESET 的報告《BlackLotus UEFI Bootkit: Myth Confirmed》。

---

## 動手練習

### 練習 1：BootHole 的 grub.cfg 信任邊界驗證

在 OVMF + secboot 環境確認 grub.cfg 的驗章地位：

```bash
# 設定 OVMF secboot 環境（見 Ch 00 環境設定）
# 啟動 QEMU 進入 UEFI Shell

# 在 ESP 上放一個合法簽章的 grub（或 shim），以及一個正常的 grub.cfg
# 然後把 grub.cfg 替換成一個有語法錯誤但不會觸發 overflow 的版本
# 觀察：GRUB2 是否有任何錯誤訊息，還是直接崩潰？

# 目的：親眼確認 grub.cfg 完全沒有簽章驗證
# 替換 grub.cfg 不會觸發 Secure Boot 警告
```

### 練習 2：模擬 Rollback 攻擊的時間窗

```bash
# 在 OVMF secboot 環境：
# 1. 建立一個「舊版」EFI binary（用 sbsign 簽章，但故意不加 SBAT）
# 2. 建立一個「新版」EFI binary（加入 SBAT metadata）
# 3. 確認 Secure Boot 對兩個版本都通過（因為 SBAT 還沒 revoke 舊版）
# 4. 設定 SBAT revocation（寫入 SbatLevel variable）
# 5. 再次確認：新版通過，舊版被擋
#
# 這就是 SBAT 解決 rollback 問題的操作示範
# 詳細 SBAT 操作在 Ch 32
```

### 練習 3：追蹤 LogoFAIL 類型的攻擊面

```python
# 用 uefi_firmware（Python 套件）解析一個 UEFI ROM，
# 找出所有與 image/logo 相關的模組：

import uefi_firmware

with open("firmware.bin", "rb") as f:
    data = f.read()

parser = uefi_firmware.AutoParser(data)
firmware = parser.parse()

# 遞迴找所有 PE/TE module 的名稱，過濾含 "Logo", "Bmp", "Image" 的
def find_logo_modules(obj, depth=0):
    if hasattr(obj, 'name') and obj.name:
        name = obj.name.lower()
        if any(k in name for k in ['logo', 'bmp', 'image', 'jpeg', 'png']):
            print("  " * depth + f"[!] {obj.name} ({type(obj).__name__})")
    if hasattr(obj, 'objects'):
        for child in obj.objects:
            find_logo_modules(child, depth + 1)

find_logo_modules(firmware)
# 輸出：目標 UEFI 中所有 image parser 相關的模組
# 這是 LogoFAIL 研究的第一步：確認攻擊面存在
```

---

## 本章重點

- **BootHole（T3b+T5）**：grub.cfg 不在 Secure Boot 驗章路徑上，GRUB2 解析它時的 heap overflow 讓攻擊者在 UEFI boot services 階段執行任意程式碼。修了 overflow 不夠，還需要 SBAT 解決撤銷問題。
- **BlackLotus（T4+T5）**：不需要 0-day，只需要把合法但有洞的舊版 bootmgfw.efi 放回 ESP——因為 dbx 撤銷更新有 brick 風險而延遲推送，造成超過一年的攻擊窗口。
- **LogoFAIL（T3+T5）**：UEFI image parser 的記憶體破壞漏洞，入口是開機 logo 圖片，執行在 Secure Boot 驗章之前的 DXE 階段。三大 UEFI 廠商的 parser 全部受影響。
- **三條鏈的共同點**：都不需要破解簽章，都利用「信任邊界定義不完整」——驗章止於 binary，binary 解析的資料或替換的舊版本都在信任邊界之外。
- **Secure Boot 的三種裂縫**：資料檔未驗章、撤銷不即時、信任最弱環節決定整鏈強度。

---

## 自我檢核

- [ ] 能說出 BootHole 的漏洞位置（哪個函數、哪種記憶體破壞類型）及其類型學歸類
- [ ] 能解釋為什麼替換 grub.cfg 不需要繞過任何簽章驗證
- [ ] 能說出 BlackLotus 利用的核心是「T4 rollback」而不是「找新洞」
- [ ] 能解釋「baton drop」發生在開機流程的哪個時間點
- [ ] 能說出 LogoFAIL 的執行時機（DXE，比 Secure Boot 驗章更早）及其類型學歸類
- [ ] 能畫出三條鏈「攻擊者可控資料 → 信任邊界 → 執行上下文」的示意圖
- [ ] 理解為什麼修了 binary 的漏洞還需要 SBAT 才能真正解決 BootHole
- [ ] 能說出 Secure Boot 的三種信任邊界裂縫及對應的防禦措施

---

## 延伸閱讀

1. **"There's a Hole in the Boot" — Eclypsium（2020）**
   讀哪裡：`eclypsium.com/research/theres-a-hole-in-the-boot/`，完整技術報告的「Technical Details」章節
   學什麼：`grub_parser_split_cmdline()` 的精確 off-by-one 機制，以及 Eclypsium 如何設計 PoC 觸發可控的 heap corruption
   關聯：本章 T3b 的 x86 具體實現；對照 Ch 21 的類型學 T3b 定義，看真實 CVE 如何映射

2. **"BlackLotus UEFI Bootkit: Myth Confirmed" — ESET（2023）**
   讀哪裡：`welivesecurity.com/2023/03/01/blacklotus-uefi-bootkit-myth-confirmed/`，尤其是「Bootkit persistence mechanism」和「Secure Boot bypass」章節
   學什麼：BlackLotus 的完整三 Stage 架構、VHD 初始部署手法、dbx 撤銷的時序問題
   關聯：本章 BlackLotus 分析的底層資料來源；Ch 31 bootkit 構造的最佳真實案例

3. **"LogoFAIL: Security Implications of Image Parsing During System Boot" — Binarly（2023）**
   讀哪裡：Binarly Research Blog `binarly.io/blog` 搜尋 LogoFAIL，或 BlackHat Europe 2023 白皮書
   學什麼：Binarly 的 emulation-based UEFI fuzzing 方法論（如何在不需要真機的情況下 fuzz DXE driver）；三家廠商的 parser 漏洞分佈
   關聯：本章 LogoFAIL 分析；Ch 27 韌體 emulation；advanced_fuzzing 課程的 firmware rehosting 章節

→ [下一章](./31-bootkit-construction.md)
