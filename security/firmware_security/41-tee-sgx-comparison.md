# Ch 41 — TEE / SGX / TrustZone 對照

> **目標**：把 trust anchor 家族擺在同一張桌上比較——TPM（被動 co-processor）、Intel SGX（enclave）、ARM TrustZone（secure world）、AMD SEV（confidential computing）、Microsoft Pluton——搞清楚各自保護什麼、威脅模型是什麼、attestation 怎麼做、已知攻擊面在哪裡、以及什麼場景該選哪個。

---

## 為什麼要對照比較？

資安研究者常遇到「這個系統用了 TPM/SGX/TrustZone 所以很安全」的說法。這句話的問題是：**每種 TEE 保護的威脅模型不同，把它們混為一談是很危險的認知錯誤。**

```
TPM：        保護「金鑰不被 OS/ring-0 偷走」
SGX：        保護「代碼和資料不被 OS/Hypervisor 偷看」
TrustZone：  保護「安全 world 不被 normal world 存取」
AMD SEV：    保護「VM 裡的記憶體不被 hypervisor 偷看」
Pluton：     把 TPM 搬進 CPU，保護「金鑰匯流排不被竊聽」
```

這五個技術解決的不是同一個問題。用 TPM 不代表你有了 SGX 的保護，用 TrustZone 也不代表你有了 TPM 的 measured boot。

---

## TPM：被動的密碼學 Co-Processor

### 設計哲學

TPM 是一個**被動**的晶片（dTPM）或韌體模組（fTPM），它不主動做任何事情——只有當主機系統發 command 給它，它才回應。

```
TPM 的核心能力：
  ┌─────────────────────────────────────────────┐
  │  PCR（Platform Configuration Registers）    │
  │    → 累積開機量測值，只可 extend 不可寫      │
  │                                             │
  │  Sealed Storage                             │
  │    → 金鑰綁 PCR policy，policy 不符不 unseal│
  │                                             │
  │  Attestation（Quote）                       │
  │    → 用 EK/AK 對 PCR 值簽章，給驗證者      │
  │                                             │
  │  Random Number Generator                   │
  │    → 硬體亂數，供密碼學用                   │
  └─────────────────────────────────────────────┘
```

### 保護邊界

- **保護的**：在 TPM 晶片/韌體內部的 key material，OS（ring-0）無法直接讀取
- **不保護的**：量測邏輯的正確性（誰 extend 什麼由 BIOS 決定）、匯流排通訊（dTPM 有竊聽問題）
- **信任根**：在硬體上是 TPM 晶片的 EK（Endorsement Key），出廠時由 TPM 廠商簽章

### Attestation 模型

```
Local Attestation：  在同一台機器上做，TPM quote 給本機軟體用
Remote Attestation： 把 TPM quote 傳給遠端驗證服務（Attestation Service）
                      驗證者確認：EK 是合法的 TPM（廠商 CA 簽的）
                               + PCR 值代表「正常」的開機狀態
```

---

## Intel SGX：Enclave（使用者態隔離）

### 設計哲學

SGX（Software Guard Extensions）是 Intel CPU 指令集擴充，讓使用者態應用程式可以建立一個**受保護的記憶體區域（enclave）**，即使 OS kernel 或 hypervisor 也無法讀取 enclave 內的資料。

```
SGX 的隔離模型：

  ┌────────────────────────────────────────┐
  │  正常 OS 環境                           │
  │  ┌──────────────────────┐              │
  │  │  SGX Enclave          │              │
  │  │  （受保護記憶體）      │              │
  │  │  ← OS kernel 無法讀  │              │
  │  │  ← Hypervisor 無法讀 │              │
  │  │  ← 其他 process 無法讀│             │
  │  └──────────────────────┘              │
  │  Intel CPU 硬體強制執行上述隔離          │
  └────────────────────────────────────────┘
```

SGX 保護的不是「金鑰」而是「代碼和資料的執行環境」。適合的場景是：在不可信的環境（雲端 VM、受攻擊的 OS）上安全執行敏感計算。

### SGX Attestation

SGX 的 attestation 分兩層：
1. **Local Attestation**：兩個 enclave 互相驗證（同一台機器上）
2. **Remote Attestation**：enclave 向遠端驗證服務證明「我確實是合法的 SGX enclave」

```
Remote Attestation 流程（EPID 模式，舊）：
  1. Enclave 產生 report（用 REPORT key 簽）
  2. Quoting Enclave 把 report 轉成 quote（用 Intel EPID key 簽）
  3. 遠端送去 Intel Attestation Service（IAS）驗證
  4. IAS 回傳驗證結果

Remote Attestation 流程（DCAP 模式，現代）：
  1. 使用 ECDSA key（不依賴 Intel IAS）
  2. 企業可以自行部署 Quoting Verification Service（QVS）
  3. 離線驗證，不需要連回 Intel
```

### SGX 的攻擊面

SGX 是著名的側信道攻擊受害者：

```
Foreshadow（CVE-2018-3615）：
  L1 Terminal Fault（L1TF）
  攻擊者（hypervisor level）可以讀取 SGX enclave 的 L1 快取內容
  → 打破了 SGX「hypervisor 無法讀」的核心保證

Plundervolt（CVE-2019-11157）：
  電壓頻率調整（Intel RAPL/MSR 介面）
  讓 SGX enclave 的密碼學計算在電壓不穩時產生錯誤的輸出
  攻擊者可從錯誤輸出提取 RSA 私鑰等 secret

SGX-Step / Cache Timing 系列：
  利用 CPU 快取的時序差異，在 enclave 外部推斷 enclave 的執行路徑
  適用於各種 cache timing side-channel（見 microarch_attacks 課）
```

SGX 在 Foreshadow 之後，Intel 意識到「用 CPU 保護 CPU 上的程式碼不被 CPU 的 hypervisor 讀」這個威脅模型本身有根本困難。Alder Lake 後的 SGX 在 Client 平台已不再支援，主要留在 Server（Xeon）平台。

---

## ARM TrustZone：Secure World 隔離

### 設計哲學

TrustZone 是 ARM 的硬體安全擴充（本課 Ch 15-16 已深入），把 CPU 分成兩個「世界」：

```
Normal World（EL0/EL1/EL2）← 一般 OS 和 App
Secure World（S-EL0/S-EL1）← TEE OS 和 TA（Trusted Application）

NS（Non-Secure）位元控制所有存取：
  DRAM 的 Secure 分區：Secure World 可讀，Normal World 讀到 0 或錯誤
  外設（Secure 外設）：只有 Secure World 能控制
```

TrustZone 跟 SGX 不同：它是 **SoC-wide 的隔離**，不只保護一個 enclave，而是整個 Secure World。典型部署：
- Normal World：Android/Linux
- Secure World：OP-TEE（或廠商 TEE），跑 Keymaster（密碼學）、Keystore（Android 金鑰管理）、DRM 等

### TrustZone 與 TPM 的關係

ARM 平台沒有獨立的 TPM 晶片，fTPM 的功能通常由 TrustZone 裡的 TEE 應用（TA）實作：

```
ARM 平台的 fTPM 架構：
  Normal World
    OS → tpm2-tools → /dev/tpm0（driver）
                              ↓ SMC 呼叫
  Secure World
    TEE OS（OP-TEE） → fTPM TA
                              ↓
                    TPM 2.0 command 執行
                    PCR、sealed key 等都在 Secure World 的記憶體裡
```

這讓 ARM 的 fTPM 比 x86 dTPM 更安全（沒有外部匯流排），同時也意味著攻擊 TEE 就等於攻擊 TPM。

### TrustZone 已知攻擊

- **TrustZone Normal→Secure 的 SMC 介面**：每次 Normal World 呼叫 Secure World，都是一個潛在的攻擊介面。SMC handler 裡的任何 bug 都可能讓 Normal World 攻擊者取得 Secure World 的執行權限
- **側信道（時序攻擊）**：TrustZone 的 cache 和 Normal World 共享，Cache timing 攻擊可以讓 Normal World 推斷 Secure World 的執行流程（見 microarch_attacks 課）
- **廠商 TA 漏洞**：很多廠商在 TrustZone 裡實作自己的 TA，程式碼品質參差不齊。三星、高通、聯發科的 TEE TA 都有公開 CVE

---

## AMD SEV：Confidential Computing（VM 加密）

### 設計哲學

SEV（Secure Encrypted Virtualization）是 AMD 針對雲端場景的方案，讓 VM 的記憶體被加密，即使 hypervisor（VMM）也無法讀取 VM 的記憶體。

```
傳統雲端 VM：
  雲端廠商 Hypervisor ←→ 可以直接讀任何 VM 的記憶體（記憶體明文）

AMD SEV：
  AMD PSP 管理每個 VM 的加密金鑰
  VM 記憶體在 DRAM 中是加密的（AES-128）
  Hypervisor 即使有實體存取，也只能看到密文

SEV 家族：
  SEV：VM 記憶體加密（最基本）
  SEV-ES（Encrypted State）：VM 暫存器狀態也加密
  SEV-SNP（Secure Nested Paging）：記憶體完整性保護，防止 replay
```

### SEV Attestation

SEV 的 attestation 讓 VM 裡的程式碼向外部驗證「我確實跑在 AMD SEV 保護下，hypervisor 沒有竄改我的啟動映像」：

```
SEV Attestation Report：
  - 由 AMD PSP 產生
  - 包含 VM 的量測值（啟動時的記憶體 hash）
  - 用 AMD 廠商 key（VCEK/VLEK）簽章
  - 遠端驗證者可以向 AMD KDS（Key Distribution Service）取得 cert chain
```

### SEV 的攻擊面

```
CacheWarp（2023，AMD CVE-2023-20592）：
  利用 CPU cache 的一個特性，讓 SEV-SNP 保護的 VM 受到記憶體完整性攻擊
  攻擊者（有 hypervisor 控制權）可以讓 VM 看到「過期的」記憶體版本
  → 打破了 SEV-SNP 的完整性保護保證

SEV-ES 的 #VC exception 攻擊：
  VM Exit 時，暫存器需要透過 VMSA（VM Save Area）傳給 hypervisor
  設計上 hypervisor 不應看到加密後的暫存器值
  但 #VC（VMM Communication Exception）handler 的一些設計缺陷可能洩漏資訊
```

---

## Microsoft Pluton：把 TPM 搬進 CPU

### 設計哲學

Pluton 是 Microsoft 與 AMD/Intel/Qualcomm 合作的 SoC 整合安全處理器，目標是解決 dTPM 的匯流排竊聽問題：

```
dTPM 架構：  CPU ←──LPC/SPI（明文匯流排）──→ TPM 晶片
                                ↑
                           可被攔截

Pluton 架構：  CPU ←──晶片內部匯流排──→ Pluton 安全處理器
                                             （在 CPU die 內部）
                        攔截不到（沒有外部接點）
```

Pluton 實作了 TPM 2.0 介面，對上層軟體（BitLocker、Windows Hello）透明，但底層完全在 CPU die 內部，消除了硬體攔截的可能。

Pluton 還有一個額外功能：**安全韌體更新**。Pluton 韌體可以透過 Windows Update 更新，而不需要 BIOS 更新。這讓 Microsoft 可以快速修補 Pluton 漏洞。

### Pluton 的爭議

Pluton 引入了一個新問題：**誰控制 Pluton？** 目前是 Microsoft 控制韌體更新。批評者認為這讓 OEM 和使用者失去對安全晶片的控制權。與 TPM Spec 的開放性不同，Pluton 是 Microsoft 的私有設計。

---

## 大對照表

| 維度 | TPM（dTPM/fTPM） | Intel SGX | ARM TrustZone | AMD SEV | Microsoft Pluton |
|------|----------------|-----------|----------------|---------|-----------------|
| **隔離機制** | 獨立晶片/韌體，PCR + sealed storage | CPU 指令集，enclave 記憶體隔離 | CPU 模式位元，Secure World | CPU 記憶體加密，VM 隔離 | CPU die 內部安全處理器 |
| **信任根** | TPM EK（廠商 CA 簽） | Intel SGX signing key + CPUSVN | ARM TrustZone 硬體 + 廠商 TEE key | AMD PSP + VCEK | Microsoft Pluton key |
| **保護目標** | 金鑰儲存 + 平台量測 | 使用者態程式碼和資料的機密性 | Secure World 與 Normal World 隔離 | VM 記憶體對 hypervisor 保密 | 金鑰儲存（匯流排安全） |
| **attestation** | PCR quote（遠端）或 local | EPID/DCAP（遠端，可離線） | TEE TA 各自設計（無統一標準） | AMD attestation report（遠端） | Windows Attestation（Microsoft 中心化） |
| **OS 可讀** | 否（ring-0 只能發 command） | 否（OS kernel 無法讀 enclave） | Secure World：否；Normal World：是 | VM 記憶體：否（hypervisor 層） | 否（比 dTPM 更安全，無匯流排） |
| **Hypervisor 可讀** | 是（TPM 不隔離 hypervisor 層） | 否（SGX 設計目標，但 Foreshadow 破洞） | TrustZone 不保護，hypervisor（EL2）可能繞過 | 否（SEV 的設計目標） | 否 |
| **物理攻擊** | dTPM 匯流排可竊聽；fTPM 需 glitch | 電壓 glitch（Plundervolt）；需修正 | SoC-level 攻擊；JTAG | DRAM 攻擊，CacheWarp | 晶片 die 攻擊（極難） |
| **側信道** | 弱（簡單操作） | 強攻擊面（Foreshadow, Cache timing） | Cache timing（Normal 探 Secure） | CacheWarp | 未知（Pluton 太新） |
| **已知攻擊** | 匯流排竊聽；faulTPM；CVE-2023-1017/1018 | Foreshadow；Plundervolt；SGX-Step | TrustZone 廠商 TA CVE；時序攻擊 | CacheWarp；SEV-ES #VC | 尚無公開重大漏洞 |
| **適用場景** | 平台完整性量測；金鑰保護；BitLocker/LUKS | 雲端機密計算；DRM；密碼學 | 行動裝置 DRM；金鑰管理；支付 | 雲端 VM 機密性（租戶 vs. 雲廠商） | Windows 平台金鑰保護（次世代 PC） |
| **開放性** | TCG 開放規格 | Intel 私有，部分開源 SDK | ARM 私有硬體，OP-TEE 開源 | AMD 私有，部分開源 | Microsoft 私有 |

---

## 威脅模型的抉擇：什麼場景選哪個

### 場景一：「我要保護伺服器上的密碼學金鑰，防止 OS 被 hack 後金鑰洩漏」

最適合：**TPM**（sealed key 綁 PCR）或 **SGX**

- TPM sealed key：OS 被 hack 了，攻擊者有 ring-0，但只要沒有 unseal 的 policy session，金鑰不洩漏
- SGX enclave：把密碼學操作放進 enclave，OS 即使全 root 也讀不到 enclave 記憶體
- 差異：TPM 保護靜態金鑰，SGX 保護動態計算

### 場景二：「我是雲端服務，想確認 VM 裡的租戶不能被我（hypervisor）偷看資料」

最適合：**AMD SEV-SNP** 或 **Intel TDX**

TPM 在這個場景沒有用：TPM 不隔離 hypervisor 層，hypervisor 可以讀 VM 的所有記憶體。SGX 不適合保護整個 VM（SGX 是 enclave 模型，不是 VM 模型）。

### 場景三：「我是 Android 手機，想保護使用者的指紋和支付資料」

最適合：**ARM TrustZone**（配合廠商 TEE）

TrustZone 是 ARM 的標準做法，Android Keystore 和 StrongBox 都依賴 TrustZone。沒有 TrustZone 替代品（除非是 RISC-V 平台的 PMP/ePMP 方案）。

### 場景四：「我想確認一台機器在我發出 remote attestation 挑戰時，確實跑著正常的 OS 而不是惡意軟體」

最適合：**TPM + Remote Attestation**

這是 TPM 的本業。SGX attestation 只能證明 enclave 是合法的，不能證明整個平台是正常的。TrustZone 沒有標準的 remote attestation 介面。

### 場景五：「我的 Windows 筆電，想確保 BitLocker 金鑰不被硬體竊聽」

最適合：**Pluton**（下一代 PC）或 **BitLocker TPM + PIN**（現有 PC）

Pluton 從根本消除匯流排竊聽。現有機器退而求其次：TPM + PIN 模式，即使 attacker 能竊聽匯流排也無法取得 VMK（因為 PIN 未輸入不觸發 unseal）。

---

## SGX 攻擊面預告：Foreshadow 與 Plundervolt

這兩個攻擊是 microarch_attacks 課的深入主題，這裡給快速索引：

```
Foreshadow（CVE-2018-3615）：
  L1TF（L1 Terminal Fault）的 SGX 版本
  透過特製的 page table entry（設 P=0 但 PFN 指向 SGX enclave page），
  讓 CPU 在 cache miss 時 speculative load enclave 資料
  L1 hit 時資料進 CPU pipeline，可被 Spectre-like side channel 讀出
  → 打破了 SGX「hypervisor 無法讀 enclave」的核心保證

Plundervolt（CVE-2019-11157）：
  Intel CPU 有 MSR 介面可以動態調整電壓/頻率（RAPL、DVFS）
  OS ring-0 可以呼叫這個介面
  攻擊者在 SGX enclave 執行 AES/RSA 計算的特定時刻降低電壓，
  讓計算結果出現 bit flip
  從錯誤輸出用 DFA（Differential Fault Analysis）恢復出 AES key

關聯：
  Foreshadow → 見 microarch_attacks Ch 25-26（L1TF 側信道）
  Plundervolt → 見 Ch 34 故障注入（軟體控制的電壓 glitch）
```

---

## 踩雷

1. **「TEE 不被 OS 存取」和「TEE 完全安全」是兩回事**：SGX enclave 不被 OS kernel 讀，但 Foreshadow 讓 hypervisor 繞過了這個保護。TrustZone Secure World 不被 Normal World 讀，但 SMC 介面 bug 讓 Normal World 可以利用 Secure World 的漏洞。隔離機制只是防線，不是萬能牆。

2. **Attestation 的可信度取決於信任根**：TPM attestation 信任 EK 的廠商 CA 鏈；SGX attestation 信任 Intel 的 IAS/DCAP；AMD SEV attestation 信任 AMD 的 VCEK。如果你不信任 Intel 或 AMD，這些 attestation 就沒有意義。Pluton 信任 Microsoft，這讓一些研究者不舒服。

3. **TrustZone 不等於 TPM**：ARM 設備上常見的誤解是「有 TrustZone = 有 TPM」。不對：TrustZone 是隔離機制，fTPM 是在 TrustZone 上跑的應用。很多嵌入式設備有 TrustZone 但沒有 fTPM，或者 fTPM 的 PCR 支援很有限。

4. **AMD SEV-SNP 的完整性保護不是萬無一失**：CacheWarp（2023）繞過了 SEV-SNP 的完整性保護，代表「VM 記憶體加密 + 完整性保護」的假設在硬體層面被打破過。機密計算是個持續攻防的領域。

5. **Pluton 的鎖定效應**：Pluton 的安全性由 Microsoft 的韌體控制，OEM 選擇了使用 Pluton 就選擇了信任 Microsoft 的更新生態。對於某些組織（非 Microsoft 生態的企業、特定政府要求自主性）這是一個部署顧慮。

---

## 進階延伸

- **Intel TDX（Trust Domain Extensions）**：Intel 的 confidential computing 方案，對應 AMD SEV。讓整個 VM 成為一個「Trust Domain」，hypervisor 無法讀取。TDX 是 SGX 的 VM 粒度版本，適合雲端場景。

- **RISC-V 的信任機制**：RISC-V 沒有硬體 TEE 標準，但有 PMP（Physical Memory Protection）可以做基本隔離。RISC-V 的 TEE 研究（Keystone、Penglai）是學術界的熱點，工業部署還不成熟。

- **Confidential Computing Consortium（CCC）**：Linux Foundation 旗下的行業組織，推動 SGX、TrustZone、SEV、TDX 的互操作性。CCC 的白皮書是理解各家 confidential computing 技術差異的好起點。

- **TPM Profile for Embedded（ePTP）**：TCG 為嵌入式設備設計的輕量級 TPM Profile，比完整 TPM 2.0 更精簡，適合 IoT 裝置。很多工業嵌入式系統的 TPM 選型會在完整 TPM 和 ePTP 之間取捨。

---

## 本章重點

- TPM 是被動 co-processor：保護金鑰儲存和量測狀態，不隔離 OS/hypervisor 的記憶體存取
- SGX 保護 enclave 記憶體不被 OS/Hypervisor 讀，但 Foreshadow/Plundervolt 展示了微架構攻擊的有效性；Client SGX 已逐步退場
- TrustZone 是 SoC-wide 的 Normal/Secure World 隔離，是 ARM 行動裝置的 TEE 骨幹；SMC 介面是攻擊入口
- AMD SEV 讓 VM 記憶體對 hypervisor 保密，適合雲端機密計算；CacheWarp 破過 SEV-SNP 完整性保護
- Pluton 把 TPM 整合進 CPU die，消除匯流排竊聽，但引入對 Microsoft 的信任依賴
- 選擇 TEE：要保護靜態金鑰 → TPM；要保護計算過程 → SGX/TDX；要保護 VM → SEV；要保護行動裝置 → TrustZone；要防匯流排竊聽 → Pluton

---

## 自我檢核

- [ ] 能一句話說出 TPM、SGX、TrustZone、SEV、Pluton 各自解決什麼問題
- [ ] 能解釋為什麼「有 TPM」不代表「有 SGX 的保護」
- [ ] 知道 Foreshadow 打破了 SGX 的哪個保證，攻擊的技術原理是什麼（L1TF + speculative load）
- [ ] 能說出 ARM TrustZone 上 fTPM 的架構，以及它如何消除了 dTPM 的匯流排竊聽問題
- [ ] 能解釋 AMD SEV-SNP 的設計目標，以及 CacheWarp 如何繞過它
- [ ] 知道什麼場景選 TPM vs SGX vs SEV（各自適合的威脅模型）

---

## 延伸閱讀

1. **"Foreshadow: Breaking the Virtual Memory Abstraction with Transient Out-of-Order Execution" — Van Bulck et al.（USENIX Security 2018）**  
   讀哪裡：foreshadowattack.eu，完整論文 PDF  
   學什麼：L1TF 在 SGX enclave 上的完整攻擊鏈，包括如何用 page table 操控讓 CPU speculative load enclave 資料  
   關聯：本章 SGX 攻擊面的核心案例，也直接銜接 microarch_attacks 課的 L1TF 章節

2. **Confidential Computing Consortium White Paper — Linux Foundation（2021）**  
   讀哪裡：confidentialcomputing.io/white-papers/  
   學什麼：SGX、TrustZone、SEV 的統一威脅模型框架，各技術的 attestation 機制對比  
   關聯：本章大對照表的補充閱讀，提供業界共識的威脅模型定義和術語標準化

3. **"CacheWarp: Software-based Fault Attacks on AMD SEV-SNP" — Buhren et al.（USENIX Security 2024）**  
   讀哪裡：cachewarpmachine.github.io，論文 PDF  
   學什麼：AMD SEV-SNP 完整性保護的設計，CacheWarp 如何透過 cache invalidation 繞過它，AMD 的修補方案  
   關聯：本章 AMD SEV 攻擊段落的一手來源，也說明了「機密計算 ≠ 萬無一失」的實際案例

→ [下一章](./practice-f-swtpm-measured-boot.md)
