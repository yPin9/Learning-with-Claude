# Ch 2 — 信任鏈解剖：全課地圖

> **目標**：理解「信任鏈（chain of trust）」的本質，區分 verified boot 與 measured boot 的設計哲學差異，掌握 x86 和 ARM 兩條信任鏈的完整結構，並把後續 Part 1–8 的每個攻擊面對應到信任鏈的具體環節。這是全課最重要的概念章，後面所有章節都建立在這裡的語彙上。

---

## 為什麼需要信任鏈？

電腦開機是一個信任傳遞問題，不是技術問題。

問題的根源：**CPU 重置後執行第一條指令時，它不知道自己跑的是合法韌體還是被竄改的韌體**。韌體不知道它載入的 OS loader 是否合法。OS loader 不知道它啟動的 kernel 是否合法。每一個環節都是一個需要回答的問題：「我能相信這段程式碼嗎？」

信任鏈（chain of trust）的做法是：**由一個所有人都同意相信的起點，逐階驗證下一個環節**。每一環說「我已驗過下一環是合法的，可以把控制權交給它」，這樣信任就從固定起點一路傳遞到 OS。

如果任何一環被竄改，信任就在那裡斷掉——這正是 bootkit 攻擊的本質：找到信任鏈最弱的一環並在那裡插入惡意程式碼。

---

## 最核心的心智模型

> **信任鏈每一環都是攻擊點，鏈的最弱環決定整體安全。**

一條十環的信任鏈，即使九環都用 hardware root of trust 保護，只要有一環沒有被驗證（或者驗證邏輯本身有漏洞），攻擊者就從那裡進去。這個道理貫穿本課所有 45 個章節。

---

## 兩個根本不同的設計哲學

在研究信任鏈之前，必須先搞清楚兩個常被混淆的概念：

### Verified Boot（驗證式開機）

**邏輯：先驗章，再執行。驗失敗就停。**

```
階段 A
  │
  ├─── 計算階段 B 的雜湊值
  ├─── 用信任金鑰驗簽章
  │       │
  │       ├─── 驗過 ──→ 執行階段 B
  │       │
  │       └─── 驗失敗 ──→ 停機 / 顯示錯誤 / 進入 recovery
  │
（階段 B 永遠不會在未通過驗證時執行）
```

這是「**主動阻擋**」模型。代表實作：UEFI Secure Boot、Android Verified Boot（AVB）、ARM TF-A 的 verified boot 模式。

優點：惡意程式碼在被驗章失敗時就被攔截，永遠不會執行。
缺點：需要管理金鑰（誰有權簽、誰有權撤銷），以及決定「驗失敗時怎麼辦」（硬停機 vs recovery 模式 vs 僅警告）。撤銷舊版 bootloader 需要維護一個黑名單（dbx / SBAT），這個黑名單本身又是攻擊面。

### Measured Boot（量測式開機）

**邏輯：不攔截，只記錄。每個階段把下一個階段的量測值（雜湊）延伸(extend)進 TPM 的 PCR。**

```
階段 A
  │
  ├─── 計算階段 B 的雜湊值
  ├─── PCR_extend(PCR[n], hash(B))  ← 寫進 TPM，無法抹除
  │
  └─── 執行階段 B（無論 B 是否合法）
```

```
PCR extend 的累積邏輯：
PCR_new = SHA256( PCR_old || new_measurement )
```

這是「**被動記錄**」模型。代表實作：TPM + SRTM、TCG Trusted Computing、Windows BitLocker 綁 PCR。

優點：不攔截正常開機流程，即使某個元件被修改，系統還是能開機，但 PCR 值會反映改動，後續可用於**遠端證明（remote attestation）**。
缺點：本身不阻止惡意程式碼執行，需要搭配額外的機制（PCR 策略、sealed key、遠端證明服務）才能把量測轉換成安全保護。

### 最重要的對比

| 維度 | Verified Boot | Measured Boot |
|---|---|---|
| 核心問題 | 「我能相信這段程式碼嗎？」 | 「這台機器的開機路徑是什麼？」 |
| 攔截惡意程式碼？ | 是（驗失敗就停） | 否（只記錄） |
| 需要 TPM？ | 否 | 是 |
| 需要預設金鑰？ | 是（信任根公鑰） | 否 |
| 抵禦 rollback？ | 需要 dbx/SBAT | 依賴 PCR 值變化被偵測 |
| 提供的保障 | 執行前完整性 | 開機路徑的可稽核性 |
| 典型用途 | 防止未簽名 OS 執行 | BitLocker 密鑰綁定、遠端證明 |

**這組對立在本課反覆出現**：Part 5 的 Secure Boot 繞過是攻擊 verified boot；Part 7 的 TPM sealed key 和遠端證明是 measured boot 的防禦應用。

---

## 信任起點：Root of Trust

「信任鏈從哪裡開始？」這個問題的答案是**信任根（Root of Trust, RoT）**。

RoT 的特性：**它本身不需要被驗證**，它是大家同意相信的起點。這個「同意」通常來自硬體：晶片製造時燒入（不可更改）的公鑰 hash 或程式碼。

### 硬體信任根（Hardware Root of Trust）

硬體信任根把信任錨（trust anchor）放在矽裡面，軟體無法修改：

**Intel Boot Guard（x86）**：
- 在 CPU 旁邊的 PCH（Platform Controller Hub）中有一個 ACM（Authenticated Code Module），在 CPU 重置後、主韌體執行之前由硬體載入。
- OEM 在出廠時把信任的公鑰 hash 燒進 CPU 的 fuse（不可逆）。
- ACM 用這個 fuse hash 驗證 UEFI 韌體的初始化程式碼（IBB, Initial Boot Block）。
- 如果驗章失敗，Boot Guard 可以設定為停機（Verified Boot 模式）或繼續但發送錯誤訊號（Measured Boot 模式）。

**ARM ROTPK（Root of Trust Public Key）**：
- 類似 Boot Guard，ARM 平台通常在 SoC 的 OTP（One-Time Programmable fuse）裡燒入信任的公鑰 hash（ROTPK hash）。
- 這個 ROTPK 是 BL1 驗 BL2 時所用公鑰的 anchor，OTP 燒入後不可更改。

### 靜態信任根 vs 動態信任根

**SRTM（Static Root of Trust for Measurement）**：量測從 CPU 重置開始，第一段執行的不可變程式碼（BIOS ROM 的最開始）是量測起點。這是傳統的 measured boot 架構，問題是：量測鏈從最開頭就開始，任何對 BIOS 早期程式碼的修改都會改變所有後續 PCR。

**DRTM（Dynamic Root of Trust for Measurement）**：利用 CPU 的特殊指令（Intel TXT 的 `SENTER`、AMD SVM 的 `SKINIT`）在 OS 已啟動後的任意時間點重新建立一個乾淨的量測起點，不依賴 BIOS 的 SRTM 鏈。
- DRTM 讓系統可以在「韌體可能不可信」的情況下仍然建立一個可信的執行環境（如 Intel TXT 建立的 Measured Launch Environment）。
- DRTM 技術上要求 CPU 支援（TXT/SVM），且配置複雜，部署率遠低於 SRTM。
- Part 2 的 SMM 章節會碰到 DRTM 的邊界。

---

## x86 信任鏈全圖

```
  ┌─────────────────────────────────────────────────────────────┐
  │                    ★ TRUST ANCHOR ★                        │
  │              Intel BootGuard fuse (PCH OTP)                 │
  │           公鑰 hash 燒入，出廠後不可更改                    │
  └────────────────────────┬────────────────────────────────────┘
                           │ 驗 IBB 簽章
                           ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  ACM（Authenticated Code Module）                          │
  │  由 PCH 硬體載入，在 CPU 進 SEC 之前執行                    │
  │  → 驗 UEFI ROM 的 IBB（Initial Boot Block）                │
  └────────────────────────┬────────────────────────────────────┘
                           │ 若驗過，交棒
                           ▼
  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
  │ SEC      │ →  │ PEI      │ →  │ DXE      │ →  │ BDS      │
  │ Security │    │ Pre-EFI  │    │ Driver   │    │ Boot     │
  │ (RESET)  │    │ Init     │    │ Exec Env │    │ Device   │
  │          │    │          │    │          │    │ Select   │
  └──────────┘    └──────────┘    └──────────┘    └────┬─────┘
  CPU 重置後         初始化 RAM     載入所有 DXE         │ 選開機裝置
  第一段程式碼        PEIM 執行      driver（DXE driver   │
  (ROM 最末端)                      是主要攻擊面)         ▼
                                                  ┌──────────┐
                                                  │ OS Loader│
                                                  │ (GRUB /  │
                                                  │ bootmgfw)│
                                                  └────┬─────┘
                                                       │ Secure Boot
                                                       │ 驗 OS loader 簽章
                                                       ▼
                                                  ┌──────────┐
                                                  │ OS       │
                                                  │ Kernel   │
                                                  └──────────┘
```

每個階段的職責和對應攻擊：

| 階段 | 職責 | 主要攻擊面 | 本課對應章節 |
|---|---|---|---|
| Boot Guard ACM | 驗 IBB，硬體 RoT | fuse 未燒（OEM 失誤）、ACM 本身 | Ch 14 |
| SEC | 初始化 CPU 狀態、切換到 PEI | ROM 竄改（SPI 直接寫）| Ch 35 |
| PEI | DRAM 初始化、早期 SoC 設定 | PEIM 驗章缺失 | Ch 3 |
| DXE | 載入大量 driver，建立 UEFI 服務表 | 惡意 DXE driver、NVRAM 攻擊、pointer 竄改 | Ch 4–8 |
| BDS | 列舉可開機裝置，執行 Secure Boot 驗章 | db/dbx/KEK/PK 竄改、Secure Boot bypass | Ch 5、28–32 |
| OS Loader | 載入 OS kernel | bootloader 漏洞（BootHole）、ESPecter 型攻擊 | Ch 30–31 |
| SMM（跨階段） | 硬體中斷處理（Ring -2） | SMI handler 漏洞、SMRAM overlap、callout | Ch 10–13 |
| NVRAM | 儲存 UEFI variable | variable 竄改、runtime write access | Ch 5、9 |

---

## ARM 信任鏈全圖

ARM 的信任鏈由 Trusted Firmware-A（TF-A）的 Boot Loader 分層架構描述，通常稱為 BL1/BL2/BL3x：

```
  ┌─────────────────────────────────────────────────────────────┐
  │                    ★ TRUST ANCHOR ★                        │
  │              ROTPK（Root of Trust Public Key）              │
  │          OTP fuse hash，SoC 出廠燒入，不可更改              │
  └────────────────────────┬────────────────────────────────────┘
                           │ ROTPK hash 驗 BL2 公鑰
                           ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  BL1 — AP Trusted ROM（BootROM）                           │
  │  固化在 SoC ROM 裡，不可更改                                │
  │  在 EL3（最高異常層級）執行                                  │
  │  功能：初始化最基本的 CPU、載入並驗 BL2                      │
  └────────────────────────┬────────────────────────────────────┘
                           │ 用 ROTPK 驗 BL2 簽章
                           ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  BL2 — Trusted Boot Firmware                               │
  │  在 EL1（Secure World）執行                                  │
  │  功能：初始化記憶體控制器、載入並驗 BL31/BL32/BL33          │
  └──────────┬────────────────┬────────────────────────────────┘
             │                │
             ▼                ▼
  ┌──────────────────┐  ┌─────────────────────────────────────┐
  │ BL32（可選）     │  │  BL31 — EL3 Runtime Firmware        │
  │ Trusted OS /     │  │  （Secure Monitor / TF-A runtime）  │
  │ OP-TEE / TrustZone│ │  永久常駐 EL3，處理 SMC 呼叫        │
  │ Secure Payload   │  │  功能：管理 EL3/EL1-S 切換          │
  └──────────────────┘  └────────────────┬────────────────────┘
                                          │
                                          ▼
                         ┌────────────────────────────────────┐
                         │  BL33 — Non-Trusted Firmware       │
                         │  （UEFI / U-Boot / GRUB）          │
                         │  在 EL2 或 EL1（Non-Secure）執行   │
                         └───────────────┬────────────────────┘
                                         │
                                         ▼
                         ┌────────────────────────────────────┐
                         │  OS（Linux / Android / RTOS）      │
                         │  在 EL1 Non-Secure 執行            │
                         └────────────────────────────────────┘
```

ARM 信任鏈的每個階段攻擊面：

| 階段 | 職責 | 攻擊面 | 本課對應章節 |
|---|---|---|---|
| BootROM（BL1）| 第一段不可更改程式碼 | BootROM 漏洞（MTK 等廠商有案例）| Ch 20、25 |
| BL2 | 驗章並載入後續階段 | BL2 二進位竄改（若 fuse 未燒）| Ch 15、16 |
| BL31（TF-A）| EL3 monitor，SMC 分派 | SMC handler 漏洞 | Ch 16 |
| BL33（U-Boot/UEFI）| Non-Secure 開機 | U-Boot 環境變數、command injection | Ch 17 |
| Secure Boot（ARM）| BL2 驗 BL3x 的簽章 | bypass 模式類似 x86 | Ch 21 |

---

## x86 vs ARM 並排對照

```
x86 (Intel/AMD)                         ARM (AArch64 / TF-A)
═══════════════════════════════════════════════════════════════════
Trust Anchor                            Trust Anchor
  Intel BootGuard fuse (PCH OTP)   ←→    ROTPK hash (SoC OTP fuse)
  │ 硬體驗 IBB                            │ 硬體驗 BL2
  ▼                                       ▼
ACM (hw-verified code module)       ←→  BL1 (BootROM, immutable)
  │ 驗 SEC/IBB                            │ 驗 BL2
  ▼                                       ▼
SEC (CPU reset vector, ROM tail)    ←→  BL2 (Trusted Boot Firmware)
  │ 切換至 PEI                            │ 驗 BL31/BL33
  ▼                                       ▼
PEI (RAM init, PEIM)                ←→  BL31 (EL3 runtime monitor)
  │ 建立 HOB list                         │ 常駐 EL3
  ▼                                       ▼
DXE (driver exec environment)       ←→  BL33 (UEFI / U-Boot)
  │ 大量 driver，最肥最容易打              │ Non-Secure 開機 firmware
  ▼                                       ▼
BDS (Secure Boot verification)      ←→  AVB / U-Boot verified boot
  │ 驗 OS loader                          │ 驗 boot.img
  ▼                                       ▼
OS Loader / Kernel                  ←→  Linux / Android Kernel
═══════════════════════════════════════════════════════════════════
特有                                    特有
  SMM (Ring -2, SMRAM)             ←→    TrustZone (EL1-S, Secure World)
  ME/AMT (Ring -3, minix OS)       ←→    TEE (OP-TEE, TOS)
  NVRAM / UEFI variable            ←→    Secure Storage (TEE)
```

兩條鏈的設計哲學雖然不同，但面臨的問題是一樣的：**如何讓信任從一個不可更改的 anchor 一路傳遞到 OS，同時每一個傳遞環節都必須有驗證**。缺了任何一個驗證，那一環就是攻擊點。

---

## 全課攻擊面地圖

下表把本課所有 Part 對應到信任鏈的攻擊位置：

```
信任鏈位置                     本課對應 Part / 章
────────────────────────────────────────────────────
BootGuard / ROTPK anchor       Part 2（Ch 14），Part 6（Ch 33–36）
SEC / BL1 (ROM)                Part 6（SPI 竄改，cold boot）
PEI / BL2                      Part 1 Ch 3（PI 攻擊面）
                               Part 3 Ch 15–16（ARM BL1–BL31）
DXE drivers（最大攻擊面）      Part 1 Ch 4–8（DXE 全章），Practice A
NVRAM / UEFI variable          Part 1 Ch 5
Capsule Update                 Part 1 Ch 6
SMM（SMRAM，Ring -2）          Part 2 Ch 10–13，Practice B
ME / PSP（Ring -3）            Part 2 Ch 14
ARM TF-A / U-Boot              Part 3 Ch 16–17
Vendor SoC / MTK               Part 3 Ch 20
Secure Boot db/dbx/KEK/PK      Part 5 Ch 28–29
Known bypass chains            Part 5 Ch 30–31（BootHole/BlackLotus）
Hardware（SPI/JTAG/FI）        Part 6 Ch 33–36
TPM PCR / Sealed key           Part 7 Ch 37–41，Practice F
Detection / Attestation        Part 8 Ch 42–45
```

每一個「方框」被打穿，後面所有依賴它的環節都跟著失效——這就是攻擊韌體最吸引人的地方，也是防守最難的地方。

---

## Secure Boot 的 db/dbx/KEK/PK 層級（快速預覽）

Part 5 會詳細拆，這裡先建立語彙：

```
PK（Platform Key）
  └── 擁有者：OEM 或企業 IT
  └── 唯一、用來簽 KEK

KEK（Key Exchange Key）
  └── 擁有者：OS 廠商（Microsoft）或管理員
  └── 用來簽更新 db/dbx 的 authenticated variable

db（Signature Database）
  └── 「信任的」：已知合法 bootloader/OS loader 的公鑰 / hash
  └── 開機時 UEFI 用 db 驗 EFI binary

dbx（Forbidden Signature Database，黑名單）
  └── 「不信任的」：已撤銷的 binary hash 或公鑰
  └── CVE-2022-21894（BlackLotus）的繞過點就在這裡
```

信任鏈在 Secure Boot 層的邏輯：簽名鏈必須從 PK 可達。如果 PK 被替換、或 dbx 沒有及時包含撤銷項目、或 db 中有過於寬鬆的金鑰（可簽任何程式碼的「uber key」），信任鏈就斷了。

---

## 踩雷集錦

**錯誤直覺：「Secure Boot 和 Measured Boot 是同一件事」**
→ 正確認識：兩者是正交的機制。Secure Boot 是 verified boot 的實例，它在執行前驗章，失敗就停；Measured Boot 是另一套機制，它把每個階段的雜湊延伸進 TPM PCR，不攔截，只記錄。一台機器可以同時啟用 Secure Boot（驗章攔截）和 Measured Boot（量測記錄），也可以只有其中一個，或兩個都沒有。

**錯誤直覺：「fuse 燒了就鐵定安全」**
→ 正確認識：fuse 確保了 trust anchor 不可被軟體修改，但信任鏈的每一個後續環節的安全不由 fuse 保障。DXE driver 的驗章邏輯如果有 bug、Secure Boot db 如果包含過期的寬鬆金鑰、NVRAM 如果對 OS 可寫——這些都是 fuse 之後的攻擊面，完全由軟體決定。Intel Boot Guard 有沒有啟用、有沒有設定為 Verified Boot 模式，也是 OEM 出廠時的決定，並非所有機器都燒了 fuse。

**錯誤直覺：「ARM 的 TrustZone 就是 x86 的 SMM」**
→ 正確認識：兩者都是「在 OS 之外有一個特權隔離區」，但設計目的和機制截然不同。TrustZone 是一個永久的雙世界（Secure World / Normal World）分隔架構，OS 完全在 Normal World 運行，Secure World 有自己的 OS（OP-TEE）和儲存。SMM 是一個「中斷驅動的臨時切換」，CPU 進 SMM 時 OS 被凍結，SMI handler 執行完再恢復。兩者的隔離邊界、生命週期、攻擊手法都不同。

**錯誤直覺：「只要 DXE driver 有數位簽章驗證就安全」**
→ 正確認識：問題在於「誰驗、驗誰的金鑰、金鑰是否被適當管理」。OVMF 的預設設定（非 secboot 版）完全不驗 DXE driver 的簽章。即使有簽章驗證，如果攻擊者能替換 db 裡的信任金鑰（需要 KEK 權限，而 KEK 需要 PK）、或找到能繞過驗章邏輯的 parsing 漏洞，簽章驗證就失效。

**錯誤直覺：「SRTM 和 DRTM 是可替換的技術選項，選一個就好」**
→ 正確認識：SRTM 從開機最初就量測，整條鏈長，任何韌體改動都影響 PCR；DRTM 在 OS 運行後動態建立新的量測起點（用 SENTER/SKINIT），可以在韌體「可能被污染」的情況下仍建立一個可信環境。兩者解決的問題不同，Intel TXT 建立的 MLE 用 DRTM 正是因為它不想依賴 BIOS/UEFI 的可信度。部分高安全場景兩者並用。

---

## 進階延伸

這裡的概念在以下場景延伸出有趣的研究問題：

**「信任鏈的最薄弱環在哪裡？」** 在現實中這個問題的答案往往是「DXE 階段」（因為 driver 數量龐大、來自不同廠商）或「Secure Boot 的 db/dbx 管理」（因為許多廠商從未更新 dbx）。Binarly 的研究（LogoFAIL、PKfail）一再指向相同的結論：供應鏈中的「實作質量」而非「協議設計」是最弱點。

**「verified boot 如果驗失敗，應該怎麼辦？」** 這是一個安全性 vs 可用性的真實工程問題：硬停機（最安全、最難 debug）、進 recovery 模式（實用、但 recovery 本身要如何信任？）、警告後繼續（最差，但有些工業設備因可用性需求而採用）。Android 採用的是「warning + 繼續」（解鎖 bootloader 時顯示橙色警告），企業 PC 通常是硬停機。

**「DRTM 還能信任什麼？」** Intel TXT 用 DRTM 建立 MLE（Measured Launch Environment），理論上讓 Hypervisor 能在不信任 BIOS 的前提下運行。但 TXT 本身的實作（ACM、SINIT）也是攻擊面，歷史上有 TXT bypass 的研究（如 Invisible Things Lab 的論文）。DRTM 把信任問題推向了「我能信任 CPU 和 ACM 嗎」這個更基本的層次。

---

## 本章重點

- 信任鏈是「由固定起點逐階驗證下一環節」的信任傳遞機制；最弱環決定整體安全。
- Verified boot：驗章失敗就停（主動阻擋）；Measured boot：只記錄不攔截（被動量測）。兩者是正交機制，可以並用。
- PCR extend 是累積雜湊（`PCR_new = SHA256(PCR_old || measurement)`），任何環節改動都會改變 PCR 值，且無法抹除。
- SRTM 從開機最初量測；DRTM 在 OS 運行後動態重建量測起點（用 SENTER/SKINIT），不依賴韌體的可信度。
- x86 trust anchor = Intel BootGuard fuse（PCH OTP）；ARM trust anchor = ROTPK（SoC OTP fuse）；兩者都燒入後不可更改。
- x86 鏈：ACM → SEC → PEI → DXE → BDS → OS Loader → OS；最肥攻擊面在 DXE。
- ARM 鏈：BL1（BootROM）→ BL2 → BL31（TF-A）→ BL33（UEFI/U-Boot）→ OS；對應 EL3 → EL2/EL1。
- Secure Boot 的 db/dbx/KEK/PK 是 verified boot 的金鑰管理層，dbx 管理（撤銷）是最常被利用的弱點。
- 整門課的所有攻擊都對應到這條鏈的某一環：Ch 4–8（DXE）、Ch 10–13（SMM）、Ch 28–32（Secure Boot bypass）、Ch 37–41（TPM）。

## 自我檢核

- [ ] 我能在不看圖的情況下畫出 x86 信任鏈（ACM 到 OS Loader）的每個階段
- [ ] 我能說出 verified boot 和 measured boot 的根本差異，以及各自「驗失敗」和「量測到惡意改動」的行為
- [ ] 我能解釋 PCR extend 的數學公式（`PCR_new = SHA256(PCR_old || measurement)`）以及為什麼這讓量測記錄不可篡改
- [ ] 我能說出 SRTM 和 DRTM 的差異，以及 DRTM 為什麼需要 CPU 的特殊指令（SENTER/SKINIT）
- [ ] 我能對應「信任鏈的哪一環」到本課的哪個 Part/章節
- [ ] 我能解釋為什麼 TrustZone 和 SMM 不是同一件事，儘管兩者都是「比 OS 更高特權的隔離區」

## 延伸閱讀

1. **[TCG Architecture Overview Specification](https://trustedcomputinggroup.org/resource/tcg-architecture-overview-specification/)** — Trusted Computing Group
   - 讀 Section 3（Roots of Trust）和 Section 4（Integrity Measurement）；這是 SRTM/DRTM、PCR extend、trust anchor 概念的規範來源，本章的語彙直接來自這份文件，讀過之後本課所有 TPM 相關章節的術語都會更清楚。

2. **[Trusted Firmware-A 文件：Boot Requirements](https://trustedfirmware-a.readthedocs.io/en/latest/design/auth-framework.html)** — TrustedFirmware.org
   - 讀 "Authentication Framework" 和 "Chain of Trust" 兩節；這是 ARM BL1/BL2/BL31 verified boot 的一手設計文件，說明每個 BL 階段如何驗下一階段，以及 ROTPK 如何作為 trust anchor。Ch 15–16 的基礎。

3. **[Intel Boot Guard overview（Intel Platform Security Brief）](https://www.intel.com/content/www/us/en/support/articles/000025873/processors.html)** 以及 Trammell Hudson 的研究 "Intel ME Secrets"
   - Intel 官方說明 Boot Guard 的 OEM Configuration 選項（Disabled / Measured / Verified / Verified and Measured）；Hudson 的研究則揭示了 ME/BootGuard 在現實中的配置問題。讀這兩份材料能理解 Ch 14（Intel ME/BootGuard）為什麼「fuse 有沒有燒」是廠商出廠設定的問題而非技術問題。

---

→ [Ch 3 PI 規範各階段的攻擊面](./03-uefi-pi-attack-surface.md)
