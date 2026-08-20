# Ch 27 — SBOM 之外的 xBOM：SaaSBOM / AI-BOM / HBOM / CBOM

> **目標**：搞清楚「xBOM」這個統稱底下有哪幾種格式、各自解決什麼問題、工具成熟度到哪、哪些還只是 spec 上的概念。重點是不被行銷話術帶走——SBOM 系列課到這裡，我們已經知道「清單 ≠ 安全」，這個清醒度在評估任何 xBOM 時同樣適用。

## 為什麼需要這個？

SBOM 最初的假設是：威脅來自你的 **軟體元件**（library、framework、executable）。但現代系統的風險並不僅止於此：

- 你的應用依賴 Stripe 付款 API——Stripe 掛掉，你也掛掉。這不是一個 npm 套件，SBOM 描述不了它。
- 你部署了一個 ML 模型——這個模型用什麼資料集訓練？訓練過程有沒有被資料投毒？誰能回答這個問題？
- 你的嵌入式設備用了一顆 MediaTek 晶片——這顆晶片的韌體版本、供應商是誰、有沒有已知漏洞？SBOM 的 purl 格式沒有為硬體設計。
- 你的後端用了 RSA-2048 做了幾百個地方的加密——後量子密碼遷移要計畫了，你能快速盤點出來嗎？

這四個問題，分別是 **SaaSBOM、ML-BOM（AI-BOM）、HBOM、CBOM** 嘗試回答的。這些格式統稱 xBOM，x 是任意特定領域的 Bill of Materials。

**旗手格式是 CycloneDX**。OWASP CycloneDX 從 v1.4 開始陸續把這些類型納入規格，到 2025 年 10 月發布的 **v1.7** 持續擴充。CycloneDX 已在 2023 年成為 **ECMA-424 2nd Edition** 國際標準，是目前 xBOM 最完整的格式規格。SPDX 在這塊遠落後於 CycloneDX。

---

## 先建立直覺

把這些 xBOM 想成同一件事的不同切面：

```
你的生產系統
  ├── 軟體元件（libraries、frameworks、OS 套件）  ← SBOM
  ├── 雲服務相依（APIs、SaaS、微服務）            ← SaaSBOM
  ├── AI/ML 模型（架構、訓練資料、推論設定）       ← ML-BOM (AI-BOM)
  ├── 硬體元件（晶片、PCB、韌體）                 ← HBOM
  └── 密碼學用法（演算法、金鑰、憑證、協定）       ← CBOM
```

這幾個維度彼此不重疊，但都是「你的系統用了什麼，清點出來」的同一邏輯。差別在工具成熟度差很多——SBOM 有 Syft / cdxgen / trivy 可以自動生成；其他幾個，有的 spec 已經成熟但工具還在早期，有的連 spec 都還在草稿。

---

## SaaSBOM：描述雲服務相依

### 它解決什麼問題

傳統 SBOM 只看你 **build** 進去的東西。但現代應用大量依賴外部 SaaS：你的電商後端可能是這樣：

```
你的 API → AWS Lambda
         → Stripe 付款 API
         → Twilio 簡訊 API
         → SendGrid 郵件 API
         → Auth0 身分驗證
```

Stripe 出事，你的結帳流程就掛。Twilio 被封鎖，你的 OTP 就送不出去。這些相依關係沒有在任何 SBOM 裡面——因為它們不是 library，是網路上的服務。

SaaSBOM 要描述的是這張 **服務拓樸圖**：哪個服務依賴哪個外部 API、對應的端點、版本（如果 API 有版本控制）、SLA 承諾、資料流向（哪些個資流進了第三方）。

### CycloneDX 怎麼表達

CycloneDX 從 **v1.4**（2022 年初）起，在 JSON schema 裡加入 `services` 頂層欄位，每個 service 可以描述：

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "version": 1,
  "services": [
    {
      "bom-ref": "stripe-api",
      "provider": {
        "name": "Stripe, Inc."
      },
      "group": "com.stripe",
      "name": "Stripe Payment API",
      "version": "v1",
      "description": "Payment processing and subscription management",
      "endpoints": [
        "https://api.stripe.com/v1/charges",
        "https://api.stripe.com/v1/subscriptions"
      ],
      "authenticated": true,
      "x-trust-boundary": false,
      "data": [
        {
          "flow": "outbound",
          "classification": "PII",
          "name": "Customer payment details"
        }
      ]
    }
  ]
}
```

`x-trust-boundary: false` 表示這個服務跨越了信任邊界（外部服務），`data` 欄位記錄資料流向和分類。這對 GDPR / PCI-DSS 稽核很有用。

### 實際用途

- **供應鏈風險分析**：Stripe 去年有幾次 incident？這個 API 有 SLA 保障嗎？依賴關係圖拉出來，可以評估「如果 X 掛掉，我哪些功能受影響」。
- **雲服務授權稽核**：你付費的 SaaS 服務有哪些？有沒有 shadow IT（工程師私下接進去但沒在 security review 的外部服務）？
- **資料流向追蹤（隱私合規）**：個資流進了哪些第三方——這對 GDPR 資料處理協議（DPA）盤點很重要。

### 誠實評估：概念成熟，工具不成熟

CycloneDX spec 的 `services` 欄位設計得很合理，但**能自動生成 SaaSBOM 的工具幾乎不存在**。原因很現實：自動偵測一個應用呼叫了哪些外部 API，需要做動態分析（抓網路流量）或靜態分析（找程式碼裡的 HTTP 呼叫），這兩個方法覆蓋率都不高，而且兩個方法對應的工具生態都還很稀薄。

實務上，SaaSBOM 目前基本上是**手寫**，或是從 API gateway 的 log 半手動整理。如果你的組織已經有 service mesh（Istio、Linkerd），流量拓樸資料是現成的，可以寫腳本轉成 CycloneDX `services` 格式——但這也是自己搭，不是有現成工具。

---

## ML-BOM / AI-BOM：描述機器學習模型供應鏈

### 它解決什麼問題

你部署了一個 LLM 或影像分類模型：

- 這個模型是什麼架構（transformer？CNN？）
- 用什麼 framework 訓練的（PyTorch 2.1？TensorFlow 2.14？）
- 訓練資料集是什麼？版本？授權？有沒有包含個資？
- 訓練時的 epochs、hyperparameter？
- 部署時的推論設定（量化方式？ONNX？）
- 有沒有做 bias 評估？bias 評估結果是什麼？

這些問題，沒有一個可以從 SBOM 裡找到答案。SBOM 最多告訴你「這個服務用了 `torch==2.1.0`」，不告訴你模型本身的任何事情。

CycloneDX 從 **v1.5**（2023 年 6 月）起引入 ML-BOM 支援，v1.6 / v1.7 持續擴充，現在可以描述：

- 模型架構家族（`modelCard.modelParameters.architectureFamily`：如 `transformer`、`CNN`）
- 訓練資料集（名稱、版本、授權、資料集大小、URL）
- 訓練配置（框架版本、epochs、batch size、optimizer）
- 推論配置（量化、runtime、硬體需求）
- Performance metrics（accuracy、F1、AUC——在 BOM 裡）
- Bias 評估結果（使用了哪個 bias evaluation framework，結果如何）

```json
{
  "type": "machine-learning-model",
  "name": "my-classifier",
  "version": "1.2.0",
  "modelCard": {
    "modelParameters": {
      "task": "image-classification",
      "architectureFamily": "CNN",
      "modelArchitecture": "ResNet-50",
      "datasets": [
        {
          "type": "training",
          "name": "ImageNet ILSVRC 2012",
          "version": "2012",
          "url": "https://image-net.org",
          "classification": "public"
        }
      ],
      "inputs": [
        { "format": "image" }
      ],
      "outputs": [
        { "format": "label" }
      ]
    },
    "quantitativeAnalysis": {
      "performanceMetrics": [
        {
          "type": "accuracy",
          "value": "0.938"
        }
      ]
    },
    "considerations": {
      "trainingData": [
        "ImageNet has known dataset biases in geographic and demographic representation"
      ]
    }
  }
}
```

### 和 Model Card 的關係

這裡很容易混淆。釐清：

- **Model Card**（Google 2019 年提出）：給人讀的 markdown / PDF 文件，描述模型的預期用途、限制、效能指標、倫理考量。格式自由，給模型使用者看。
- **ML-BOM（CycloneDX）**：機器可讀的結構化格式（JSON / XML），讓工具能解析、搜尋、差異比對、和漏洞資料庫整合。

兩個不互斥。好的 AI 系統兩個都應該有：Model Card 讓人讀懂，ML-BOM 讓工具能掃。實務上，大型模型（Llama、Mistral）有 Model Card 沒有 ML-BOM；CycloneDX ML-BOM 是更新的格式，生態還在追趕。

### EU AI Act 的驅動力

**EU AI Act** 2024 年通過、2025 年開始分階段生效，對「高風險 AI 系統」要求技術文件（Technical Documentation），內容包含模型的訓練方式、資料集來源、效能指標、測試結果。ML-BOM 是滿足這類要求的機器可讀方式——至少比「寫一份 Word 文件」更容易和內控系統整合。

### 誠實評估：規格有了，生態還很稀薄

CycloneDX Python library 可以手動構建 ML-BOM，CycloneDX JavaScript 和 Java library 也有。但**自動生成工具幾乎不存在**——你很難讓工具自動掃一個 `.pt` 檔案，把訓練資料集、framework 版本、hyperparameter 全部抽出來寫成 BOM，因為這些資訊根本沒有統一的地方記。

要清醒面對的另一件事：**ML-BOM 列出訓練資料集，但你怎麼驗資料集沒有被投毒？** 清單說「用了 ImageNet 2012」，但有沒有人在 fine-tuning 時偷偷混進惡意資料？怎麼驗模型沒有 backdoor trigger？BOM 只是清單，不是驗證機制。「AI-BOM 解決 AI 安全問題」是一句過度承諾的行銷話術。

---

## HBOM：描述硬體元件

### 它解決什麼問題

IoT 設備、嵌入式系統、工控設備的「供應鏈」延伸到硬體層面：

- 你的路由器用了哪顆 SoC？韌體版本是什麼？
- PCB 上用的是哪家供應商的 flash 晶片？有沒有已知晶片漏洞？
- 這個工業控制器的零件，有多少來自中國供應商？（這在美國聯邦採購是合規問題）

SolarWinds 是軟體供應鏈攻擊，但類似的攻擊可以發生在硬體層面：晶片製造商在製程中植入後門（理論上），或更現實地，在晶片韌體（firmware）裡藏漏洞。美國 NIST 的 SBOM 指引文件和 CISA 的供應鏈指引都提到，完整的供應鏈透明度需要把 HBOM 納入。

### CycloneDX 的支援方式

CycloneDX 用元件的 `type` 欄位支援硬體，`type` 可以設成 `hardware`，並搭配 `externalReferences`、`evidence` 欄位描述：

```json
{
  "type": "hardware",
  "manufacturer": {
    "name": "MediaTek"
  },
  "name": "MT7621A",
  "version": "A1",
  "description": "MIPS-based dual-core SoC for home gateway applications",
  "properties": [
    {
      "name": "category",
      "value": "SoC"
    },
    {
      "name": "cpu-architecture",
      "value": "MIPS"
    }
  ],
  "externalReferences": [
    {
      "type": "advisories",
      "url": "https://www.mediatek.com/product-security"
    }
  ]
}
```

但這裡有一個根本問題：**硬體元件沒有像 purl 那樣成熟的統一識別符**。軟體有 `pkg:npm/lodash@4.17.21`，硬體沒有對應的標準格式。CycloneDX 有 `cpe` 欄位可以填 CPE（Common Platform Enumeration），但 CPE 的 hardware 資料庫覆蓋率很差，很多晶片根本找不到 CPE。

### 誠實評估：很需要但很難做，工具最稀薄

HBOM 是幾個 xBOM 中工具最不成熟的一個。沒有能自動掃 PCB 設計檔（Gerber、KiCad 格式）或嵌入式 binary 的工具直接輸出 HBOM。目前的實踐是：

- 嵌入式/IoT 廠商手動整理 BOM（本來就有 PCB BOM，轉成 CycloneDX 格式）
- 韌體安全分析工具（Binwalk、firmwalker）可以找出韌體裡用的軟體元件，這邊和 SBOM 重疊
- 真正的「硬體元件識別」幾乎沒有工具能做

**在合規用途（如美國國防採購、聯邦供應商）**，HBOM 的需求很明確，但技術生態還沒跟上法規的期待。

---

## CBOM：密碼學元件清單

### 它解決什麼問題

這是目前幾個 xBOM 裡 **最有實際現實緊迫性** 的一個。原因只有一個：**後量子密碼（Post-Quantum Cryptography，PQC）遷移**。

NIST 在 2022–2024 年陸續標準化了 PQC 演算法（ML-KEM / CRYSTALS-Kyber、ML-DSA / CRYSTALS-Dilithium、SLH-DSA / SPHINCS+），NIST FIPS 203/204/205 已在 2024 年 8 月正式發布。量子電腦在幾年到幾十年的時間窗口內，有能力破解 RSA、ECDSA、ECDH 這些目前主流的非對稱密碼。

問題是：**你的系統裡有多少地方用了 RSA-2048 / ECDSA / ECDH？** 如果你說不出確切數字，你就沒辦法規劃遷移。

CBOM 的答案是：把你系統裡所有密碼學用法清點出來。包含：

- 用了哪些演算法（RSA-2048、AES-256-GCM、ECDSA-P256、TLS 1.2/1.3）
- 金鑰長度
- 密碼學元件來自哪裡（OpenSSL 3.x？BoringSSL？系統 CNG？）
- 憑證（有效期、簽章演算法）
- 協定設定（TLS cipher suite、選用了哪些 cipher）
- 隨機數來源（`/dev/urandom`？RDRAND？）

### CycloneDX v1.6 的 CBOM 支援

CycloneDX **v1.6**（2024 年初）是第一個正式引入 CBOM 的版本。定義了專門的 `cryptoProperties` 欄位和 `type: cryptographic-asset` 的元件類型：

```json
{
  "components": [
    {
      "type": "cryptographic-asset",
      "bom-ref": "tls-config-api",
      "name": "TLS 1.3 for API endpoint",
      "cryptoProperties": {
        "assetType": "protocol",
        "protocolProperties": {
          "type": "tls",
          "version": "1.3",
          "cipherSuites": [
            {
              "name": "TLS_AES_256_GCM_SHA384",
              "algorithms": [
                { "bom-ref": "alg-aes256" },
                { "bom-ref": "alg-sha384" }
              ]
            },
            {
              "name": "TLS_CHACHA20_POLY1305_SHA256"
            }
          ],
          "ikm": [
            { "bom-ref": "alg-ecdhe" }
          ]
        }
      }
    },
    {
      "type": "cryptographic-asset",
      "bom-ref": "alg-aes256",
      "name": "AES-256-GCM",
      "cryptoProperties": {
        "assetType": "algorithm",
        "algorithmProperties": {
          "primitive": "ae",
          "parameterSetIdentifier": "256",
          "mode": "gcm",
          "nistQuantumSecurityLevel": 1,
          "cryptoFunctions": ["encrypt", "decrypt"]
        }
      }
    },
    {
      "type": "cryptographic-asset",
      "bom-ref": "alg-rsa2048",
      "name": "RSA-2048",
      "cryptoProperties": {
        "assetType": "algorithm",
        "algorithmProperties": {
          "primitive": "pke",
          "parameterSetIdentifier": "2048",
          "nistQuantumSecurityLevel": 0,
          "cryptoFunctions": ["encapsulate", "sign"]
        }
      }
    }
  ]
}
```

`nistQuantumSecurityLevel: 0` 直接在 BOM 裡標出這個演算法量子不安全。這讓工具可以自動掃 CBOM，找出所有 `nistQuantumSecurityLevel: 0` 的條目，列出遷移清單。

### CBOM 和 SBOM 的差異

這個很重要，容易混淆：

```
SBOM 說：「這個應用用了 openssl 3.0.9」
CBOM 說：「這個應用在 /api/login endpoint 用了 RSA-2048 做 session key exchange，
         在 /api/data 的 TLS 用了 ECDHE-RSA 做 key exchange + AES-256-GCM 做加密」
```

從 SBOM 你知道 OpenSSL 的版本，可以查 CVE。但你不知道你的應用 **具體調用了哪些 cipher**。一個應用可以 link 進 OpenSSL 但只用了 AES 對稱加密，不用任何量子不安全的演算法；另一個應用可能用了一堆 RSA。SBOM 分不出這兩種情況，CBOM 可以。

**你沒辦法從 SBOM 推導出 CBOM**，這是兩個獨立的清點維度。

### 工具支援：IBM cbomkit

IBM 的 **cbomkit**（開源，GitHub 上有）是目前最完整的 CBOM 工具，支援：

- 靜態分析：掃 Java / Python 原始碼，識別密碼學 API 呼叫
- 輸出 CycloneDX CBOM 格式
- 和 sbomqs 類似的 CBOM 品質評估

限制：靜態分析的覆蓋率取決於你用了多少動態 dispatch / reflection 在密碼學呼叫上；如果密碼學函式庫是透過 JNI 或 CGo 橋接調用的，靜態分析看不到。部分商業 SAST 工具（Veracode 等）也開始支援密碼學 API 識別，但 CBOM 輸出格式的支援還不統一。

### 誠實評估：最有現實緊迫性，但覆蓋率有限

CBOM 是這幾個 xBOM 中最值得實際操作的一個。PQC 遷移的時間壓力是真實的，盤點現有密碼學用法是遷移的第一步，沒有比 CBOM 更好的方式。

但靜態分析的覆蓋率有限：動態決定用哪個 cipher（例如從 config 讀取）、跨語言邊界調用、native 程式碼橋接，這些場景靜態分析都看不到。完整的 CBOM 需要靜態分析加上動態觀察（network packet capture 看實際跑起來的 TLS 設定、審查 config 文件），不能只靠工具自動生成就交差。

---

## OBOM：操作環境清單（簡介）

OBOM（Operations Bill of Materials）是這幾個 xBOM 裡定義最模糊的一個。概念上它描述「跑起來的環境」：

- 哪個 OS 版本（Ubuntu 22.04.3 LTS）
- 哪個 container runtime（containerd 1.7.2）
- 哪個 kernel 版本
- 實際的 mount、network interface、開放 port
- 環境變數（排除機密）、config 設定

這和 SBOM 的差別在時間點：**SBOM 是 build 時的清單，OBOM 是 runtime 的清單**。同一個 Docker image 在不同主機跑起來，OBOM 可能不同（kernel 版本不同、runtime 不同）。

CycloneDX spec 有提到 OBOM 的概念，但沒有獨立的 schema 支援，實務上用 `components` 加上 `type: operating-system` / `type: container` 的元件類型來表達。**工具幾乎不存在，定義仍然模糊**，有沒有 OBOM 這個需求也取決於使用情境。這裡點到為止，不要花太多時間在最不成熟的部分。

---

## 對比與取捨

| xBOM 類型 | CycloneDX 版本支援 | 工具成熟度 | 主要用途 | 最大挑戰 |
|-----------|-------------------|-----------|---------|---------|
| SBOM      | v1.0+（主線）      | 成熟（Syft / cdxgen / trivy） | 軟體元件漏洞管理 | 已知洞才有用 |
| SaaSBOM   | v1.4+ `services`  | 早期（幾乎手寫） | 雲服務相依圖、隱私稽核 | 自動生成工具極少 |
| ML-BOM    | v1.5+ `modelCard` | 早期（手動構建） | AI 供應鏈透明度、EU AI Act | 訓練資訊沒有統一存放位置 |
| HBOM      | v1.x `type: hardware` | 很稀薄（幾乎手寫） | 嵌入式/IoT 溯源、聯邦採購合規 | 硬體識別符未統一 |
| CBOM      | v1.6+ `cryptoProperties` | 初期可用（cbomkit） | PQC 遷移盤點、密碼學稽核 | 靜態分析覆蓋率有限 |
| OBOM      | 概念（無獨立 schema） | 幾乎沒有 | 執行時環境快照 | 定義仍模糊 |

---

## 踩雷集錦

1. **「AI-BOM 解決 AI 安全問題」**

   這句話在行銷材料裡出現頻率很高，要警惕。ML-BOM 是一張清單，告訴你「這個模型用了哪些東西」。但「這些東西有沒有問題」是另一回事。ML-BOM 列出訓練資料集名稱，但你怎麼驗那個資料集沒有被資料投毒（data poisoning）？你怎麼驗模型沒有植入 backdoor trigger？怎麼驗 fine-tuning 過程沒有被竄改？這些問題 ML-BOM 一個都回答不了——就像 SBOM 列出你用了哪個 library，但回答不了「這個 library 有沒有 0-day」一樣。清單是第一步，不是終點。

2. **把 ML-BOM 和 Model Card 搞混**

   兩個是不同用途的東西。Model Card 是人讀的自然語言描述，讓使用者了解模型的預期用途和限制，格式自由，通常是 markdown。ML-BOM（CycloneDX 格式）是機器可讀的結構化 JSON / XML，讓工具能解析、整合、比對、生成報告。很多人說「我們有 Model Card」就以為完成了 ML-BOM——不，Model Card 不能被 Dependency-Track 解析，不能自動觸發 CI pipeline 告警，不能和漏洞資料庫整合。兩個各有用途，不能互相取代。

3. **「從 SBOM 可以推導出 CBOM」**

   這個誤解很直覺但完全錯的。SBOM 告訴你用了 `openssl 3.0.9`，CBOM 告訴你這個應用在哪個端點、用哪個 cipher suite、做什麼密碼學操作。前者你可以從套件管理器自動生成；後者你需要分析應用程式碼、設定、runtime 行為。知道你用了 OpenSSL 不代表你知道你用了 RSA 還是 ECDH，更不代表你知道你的 TLS 有沒有開 TLS 1.0（量子安全性先不談，連 TLS 1.0 都是合規問題）。PQC 遷移的第一步就是 CBOM 盤點，不是 SBOM 盤點——即使你有完美的 SBOM，PQC 遷移計畫也沒辦法從它推導出來。

4. **「SaaSBOM 是 SBOM 的子集」**

   不是。SBOM 和 SaaSBOM 描述的是完全不同類型的相依：前者是你 build 進 artifact 的軟體元件，後者是你在 runtime 透過網路呼叫的外部服務。一個 `node_modules` 裡完全沒有 Stripe 的 npm 套件的應用，卻可能 HTTP call 到 Stripe API；一個直接 bundle 了 stripe JS SDK 的前端，在 SaaSBOM 和 SBOM 裡都要出現。兩個清單不相交，也不互相包含。

---

## 進階：再往深一層

### CBOM 和 PQC 遷移的現實時間線

NIST FIPS 203（ML-KEM）、FIPS 204（ML-DSA）、FIPS 205（SLH-DSA）在 2024 年 8 月正式發布後，美國 CISA 和 NSA 陸續發布指引，要求聯邦機構和 Critical Infrastructure 在 2030 年代完成 PQC 遷移。這個時間窗口比多數工程師想像的緊：

- RSA / ECDSA / DH 的金鑰可以今天被「先存後破」（harvest now, decrypt later）——攻擊者現在抓取加密流量，等量子電腦成熟了再解密。對長期保密的資料（國家機密、醫療記錄）這已經是現在的威脅。
- TLS 替換不是最難的部分；憑證基礎設施（PKI）的遷移、硬體安全模組（HSM）、舊嵌入式設備的韌體更新，才是遷移計畫裡最棘手的環節。
- 沒有 CBOM 就不知道從哪裡下手——這是 CBOM 目前最有說服力的 business case。

### CycloneDX ECMA-424 國際標準化的影響

CycloneDX 在 2023 年成為 ECMA-424 後，到 2024 年發布 2nd Edition，這不只是加分項，而是合規要求引用時的重要性：美國 CISA、UK NCSC、歐盟的 CRA（Cyber Resilience Act）在提到 BOM 格式時，都可以引用這個國際標準。對需要向政府機關提交 SBOM / xBOM 的廠商，有國際標準背書的格式遠比自訂 JSON 格式有說服力。

### xBOM 和 SBOM 整合：同一個 BOM 描述所有維度

CycloneDX spec 的設計允許在同一份 BOM 文件裡同時包含 `components`（SBOM）、`services`（SaaSBOM）、`cryptographic-asset` 類型元件（CBOM）、`machine-learning-model` 類型元件（ML-BOM）、`hardware` 類型元件（HBOM）。也就是說，CycloneDX 的願景是**一份文件描述你系統的所有物料清單維度**，不同的消費者（漏洞掃描器、密碼學稽核工具、AI 合規平台）各自讀自己關心的部分。

目前大多數工具還只讀得懂 `components` 部分，但 spec 的設計是向前相容的。

---

## 動手練習

### 練習一：手寫迷你 CBOM

用 CycloneDX JSON（v1.6+）手寫一個 CBOM，描述一個 Web API 的密碼學設定：

- TLS 1.3 端點，cipher suite：`TLS_AES_256_GCM_SHA384`、`TLS_CHACHA20_POLY1305_SHA256`
- Key exchange 用 ECDHE（P-256）
- JWT 簽章用 RS256（RSA-2048）

參考上面的 JSON 範例結構，填入 `cryptoProperties.algorithmProperties` 的 `nistQuantumSecurityLevel`：
- AES-256-GCM：量子安全（`nistQuantumSecurityLevel: 5`，對稱密碼 256 bit 在 Grover's algorithm 後等效 128 bit，仍安全）
- ECDHE-P256：量子不安全（`nistQuantumSecurityLevel: 0`，Shor's algorithm 可破）
- RSA-2048：量子不安全（`nistQuantumSecurityLevel: 0`）

寫完後，回答：如果你要做 PQC 遷移，從這份 CBOM 你能識別出哪些需要替換的條目？JWT 的 RS256 可以換成什麼 PQC 替代品？

### 練習二：評估一個 AI 工具的 ML-BOM

找一個你最近用過的開源 AI 工具（Stable Diffusion、Whisper、LLaMA.cpp，或任何你知道的開源模型），或用假設情境。嘗試列出這個模型的 ML-BOM 條目：

1. 模型架構（`architectureFamily`）：transformer？diffusion？
2. 訓練框架（`framework`）：PyTorch？JAX？版本？
3. 訓練資料集：名稱、版本、授權？（如果公開的話）
4. 有沒有 Model Card？Model Card 和你試著填的 ML-BOM 條目之間，哪些資訊重疊？哪些 Model Card 有但 ML-BOM 裡沒有？哪些是 Model Card 沒有但你希望 ML-BOM 能記錄的？

這個練習不需要工具，手動思考就好。目的是體會 ML-BOM 在哪裡有價值、在哪裡碰到「這資訊根本沒有地方記」的困難。

---

## 本章重點整理

- **xBOM 是 SBOM 邏輯的延伸**，把「清點物料」的概念應用到軟體元件以外的領域——雲服務、AI 模型、硬體元件、密碼學用法。
- **CycloneDX 是主要格式旗手**：v1.4 有 `services`（SaaSBOM）、v1.5 有 ML-BOM、v1.6 有 CBOM，已成為 ECMA-424 國際標準；SPDX 在 xBOM 這塊大幅落後。
- **成熟度差異極大**：SBOM 工具生態成熟；CBOM 有初步可用工具（cbomkit）；SaaSBOM / ML-BOM 是早期；HBOM 最稀薄。
- **CBOM 是目前最有現實緊迫性的 xBOM**，驅動力是 PQC 遷移——你無法規劃你不知道的遷移工作，CBOM 就是那份盤點。
- **SBOM 推不出 CBOM**：知道你用了 OpenSSL 3.x 不告訴你你用了哪些 cipher、在哪裡、量子安全性如何。
- **ML-BOM 不等於 AI 安全**，清單解決透明度問題，不解決模型行為驗證問題；不要被「AI-BOM 解決 AI 安全」這類說法帶走。
- **SaaSBOM 的工具缺口是真實的**：spec 設計合理，但沒有工具能自動生成，今天要做只能手寫或從 service mesh log 半自動整理。

---

## 自我檢核

- [ ] 我能說出 SaaSBOM 和 SBOM 的根本差異：前者描述 runtime 的網路服務相依，後者描述 build 時打包的軟體元件
- [ ] 我能解釋為什麼 ML-BOM 和 Model Card 不能互相取代：機器可讀 vs. 人類可讀，整合工具能力不同
- [ ] 我能說出 CBOM 和 SBOM 的差異：「用了 OpenSSL」vs.「在哪個端點用了 ECDHE + AES-256-GCM」
- [ ] 我能說出為什麼 CBOM 現在最有緊迫性：PQC 遷移需要先知道哪裡用了量子不安全演算法
- [ ] 我知道 CycloneDX v1.5 / v1.6 各自引入了什麼：v1.5 ML-BOM，v1.6 CBOM
- [ ] 我能對「AI-BOM 解決 AI 安全」這句話給出批判性回應

---

## 延伸閱讀

- **[CycloneDX Specification v1.7](https://cyclonedx.org/specification/overview/)**
  官方 spec 文件，SaaSBOM / ML-BOM / CBOM 的 schema 在這裡。直接看 JSON schema 比看說明文件清楚得多——實際的欄位名稱、必選 / 選填、允許值，全在這裡。

- **[ECMA-424 2nd Edition — CycloneDX Standard](https://ecma-international.org/publications-and-standards/standards/ecma-424/)**
  國際標準文本，合規用途需要引用標準時看這裡。

- **[NIST IR 8547 (Draft) — Transition to Post-Quantum Cryptography Standards](https://csrc.nist.gov/pubs/ir/8547/ipd)**
  NIST 的 PQC 遷移指引草稿，把 CBOM 放在整個遷移計畫的脈絡裡理解，比單看 CycloneDX spec 更有 business case 感。

- **[IBM cbomkit — GitHub](https://github.com/IBM/cbomkit)**
  目前最完整的 CBOM 工具，支援 Java / Python 靜態分析輸出 CycloneDX CBOM。README 裡有用法範例，直接試跑比看文件有感。

- **[CycloneDX ML-BOM use case](https://cyclonedx.org/capabilities/mlbom/)**
  CycloneDX 官方的 ML-BOM 使用案例說明，包含 JSON 範例，比 spec 更容易入門。

- **[EU AI Act — Technical Documentation Requirements (Article 11 + Annex IV)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32024R1689)**
  高風險 AI 系統的技術文件要求出處，把 ML-BOM 放在 AI 合規的法律框架裡理解的一手資料。

---

這章的五種 xBOM，除了 CBOM 有相對具體的工具可以動手，其他幾種今天的主要用途還是作為「思考框架」——幫你把供應鏈風險的思考延伸到軟體元件以外。工具生態的成熟是幾年的事，但現在就有必要知道這幾個維度存在、知道它們解決的問題和 SBOM 的差異，才不會把「做了 SBOM」等同於「供應鏈透明度已經搞定了」。

→ [Ch 28 SBOM 與 DFIR / 藍隊](./28-sbom-dfir.md)
