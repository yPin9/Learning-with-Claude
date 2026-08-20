# Ch 22 — SLSA framework

> **目標**：理解 SLSA（Supply-chain Levels for Software Artifacts，讀 "salsa"）v1.0 的設計邏輯與 Build track 四個 level 的具體要求，並說清楚 SLSA 與 SBOM 如何互補，以及它對應到哪些真實攻擊。

## 為什麼需要這個？

Ch 21 解決了「這份 SBOM 是誰發的、沒被改過」——但這只驗了 SBOM 本身的完整性。還有一個更深的問題沒有被回答：

**「SBOM 列出的元件，是從哪個 source、用哪個 builder、怎麼 build 出來的？」**

這個問題的背景是 2020 年的 SolarWinds 事件：攻擊者入侵了 build 環境，在 build 過程中植入惡意程式碼，產出的 binary 跟 source code 不一致——SBOM 誠實地說「有這些元件」，但沒有記錄「build 過程被動過」。就算你有完整的 SBOM，你也不知道產出它的 build 有沒有被篡改。

SLSA 要解決的就是這件事：**build 完整性（build integrity）**。它的核心概念是 **provenance**（來源證明）：一份機器可讀的文件，記錄「這個 artifact 是由哪個 source、哪個 builder、用什麼 build 參數、在什麼時間點產出的」。

SLSA 是 OpenSSF（Open Source Security Foundation）維護的框架，2023 年發布 v1.0，是目前業界的參考標準。

## 先建立直覺

在進 level 定義之前，先把 SLSA 解決的問題畫出來。軟體從 source 到用戶手上，有幾個可以被攻擊的環節：

```
開發者電腦                  CI/CD                       用戶
┌────────────┐          ┌───────────────┐         ┌──────────────┐
│            │          │  build 環境   │         │              │
│  源碼      │──push──▶│  compiler     │──發布──▶│  安裝軟體    │
│  (git)     │          │  script       │         │              │
└────────────┘          │  dependencies │         └──────────────┘
                        └───────────────┘
     ↑攻擊點 A               ↑攻擊點 B                ↑攻擊點 C
  源碼本身被改            build 過程被植入            發布物件被替換
```

SBOM 主要說明攻擊點 A 的結果（「有哪些元件進去了」），但不能防止攻擊點 B——build 過程中植入的後門不會改變 source，SBOM 不會察覺。

SLSA 的設計問題是：**你有多大把握 artifact 的確是從那份 source 用那個 build 過程來的，沒有在中間被動手腳？**

Provenance 就是回答這個問題的文件。

## SLSA v1.0 的框架結構

SLSA v1.0 定義了多個 track（軌道），目前主要的是 **Build track**：衡量「artifact 的 build 過程有多可信」。

> **注意**：這裡說的是 **SLSA v1.0**，不是 2021 年的舊版（舊版有 SLSA 1/2/3/4 四個 level，v1.0 改成 Build L0/L1/L2/L3，概念相似但定義有所調整。本章全部以 v1.0 為準，slsa.dev/spec/v1.0 是規範的權威來源）。

Build track 有四個 level：

### Build L0：沒有保證

L0 是「沒有 SLSA」的狀態，代表不符合任何 SLSA 要求。開發機上直接 `go build` 再傳給朋友的 binary，就是 L0。這不代表「不安全」，只代表「沒有任何機器可驗的 build 完整性保證」。

### Build L1：有 provenance

**核心要求**：
- Build 過程是一致的（有定義好的 build 流程，不是每次靠記憶手動跑）
- 產出 provenance 文件，描述 build platform、build 流程、top-level 輸入
- Provenance 按生態系的慣例分發給消費者

**解決的問題**：記錄 build 過程，讓錯誤可以追溯（「我從哪個 commit build 的？用了哪些參數？」）。防止「不小心從錯誤的 commit build」這類意外。

**沒有解決的問題**：Provenance 可以是手寫的，沒有機制阻止你謊稱「這是用乾淨 build 環境產的」。L1 的 provenance 是「有就好」，不需要防竄改。

### Build L2：有簽章的 provenance + 託管 build

**核心要求**（在 L1 之上）：
- Build 在**專屬的託管 build 平台**上執行（不是開發者自己的機器）
- Provenance 有**數位簽章**，由 build platform 簽發
- 消費者驗章時確認 provenance 的真實性

**解決的問題**：對抗 post-build 的竄改。如果有人替換了 artifact，簽章的 provenance 和 artifact 的 digest 就會對不上。外部攻擊者要偽造 provenance 需要竊取 build platform 的簽章金鑰，難度大幅提高。

**沒有解決的問題**：如果 build platform 本身被入侵（insider 或 compromised credential），攻擊者仍然能在 build 過程中植入後門並產出「合法」的 provenance。

### Build L3：強隔離 build

**核心要求**（在 L2 之上）：
- Build platform 防止「不同 build runs 互相影響」（例如前一個 build 留下的檔案不能影響下一個）
- 防止用戶自定義的 build steps 存取 signing key（你寫的 `Makefile` 不能拿到簽 provenance 的金鑰）

**解決的問題**：對抗 build 內部的攻擊。即使 build script 被植入惡意程式碼，它也無法：(a) 污染其他 build（隔離），(b) 自己簽一份假 provenance（因為拿不到 signing key）。

**用一句話理解**：L3 讓「就算你的 build script 被攻陷，攻擊者也無法假造一份可信的 provenance」。

## SLSA Levels 總表（v1.0 Build Track）

| Level | 主要要求 | 對應威脅 | 典型實現 |
|---|---|---|---|
| L0 | 無 | 無 | 本機手動 build |
| L1 | 有 provenance（可不防竄改） | 意外錯誤、記錄缺失 | CI 跑 build 並記錄 |
| L2 | 有簽章的 provenance + 託管 build | Post-build 竄改、deterrence | GitHub Actions + 簽章 |
| L3 | 強隔離 + signing key 與 user build 隔離 | Build-time 植入、cross-build 污染 | slsa-github-generator reusable workflow |

## 威脅模型：SLSA 對應 Ch 18 的攻擊面

回到 Ch 18 的供應鏈攻擊分類，SLSA 各 level 防禦的位置：

```
攻擊面                              SLSA 防禦層
────────────────────────────────────────────────────────
A. 源碼被改（PR / commit 植入）    → SLSA 不直接防（這是 code review 的問題）
                                      但 L3 的 resolvedDependencies 讓你知道
                                      確切的 commit hash，可比對
B. dependency 被投毒               → resolvedDependencies 留記錄，出事能追溯
C. Build 過程被植入（SolarWinds）  → L3：build steps 拿不到 signing key，
                                         即使植入也無法偽造合法 provenance
D. Build 產出被替換（post-build）  → L2：artifact digest 寫進 provenance，
                                         替換後 digest 對不上
E. Provenance 偽造                 → L2+：provenance 需要 builder 的 signing key
                                         外部攻擊者沒有 key 就偽造不了
```

SolarWinds 的攻擊模式（攻擊點 C）需要 L3 才能防——入侵 build 環境植入後門後，攻擊者如果沒有 signing key，產出的 artifact 就沒有合法的 SLSA L3 provenance，消費者驗章時會發現。

## SBOM 與 SLSA Provenance 的關係

這是很多人搞混的地方。兩個文件互補，不重疊：

| 維度 | SBOM | SLSA Provenance |
|---|---|---|
| 核心問題 | 這個 artifact 裡**有什麼**？ | 這個 artifact 是**怎麼來的**？ |
| 列出的東西 | 元件清單（名稱、版本、license、PURL）| Build 過程（source、builder、參數、時間）|
| 主要消費者 | 漏洞掃描器、license checker、CISA 法規 | 供應鏈驗證工具、SLSA verifier |
| 格式 | SPDX / CycloneDX | in-toto Statement，predicate 是 SLSA provenance |
| 簽章標準 | cosign sign-blob / attest | cosign attest，predicateType = slsa.dev/provenance/v1 |

一個完整的供應鏈安全流程，兩者都需要：

```
source (git commit)
     ↓ build
artifact (binary / image)
     ↓
  ┌─────────────────┐    ┌──────────────────────┐
  │  SBOM           │    │  SLSA Provenance      │
  │  「有 libssl 3.0.2,  │    │  「從 commit abc123,  │
  │   libz 1.3.1,   │    │   在 GitHub Actions,  │
  │   ...」          │    │   build 參數 ...」     │
  └─────────────────┘    └──────────────────────┘
         ↑ 哪些元件               ↑ 怎麼來的
   漏洞掃描用                  完整性驗證用
```

## 底層機制：Provenance 的結構

SLSA v1.0 的 provenance 是一個 in-toto Statement，`predicateType` = `https://slsa.dev/provenance/v1`，`predicate` 的結構如下：

```json
{
  "buildDefinition": {
    "buildType": "https://...",        // 必填：描述 build 流程的 URI
    "externalParameters": {            // 必填：用戶可控的 build 輸入
      "workflow": { "ref": "...", "repository": "..." }
    },
    "internalParameters": {            // 選填：platform 控制的內部參數
      "GITHUB_EVENT_NAME": "push"
    },
    "resolvedDependencies": [          // 選填：build 過程中抓的所有依賴
      {
        "uri": "git+https://github.com/example/myapp@refs/tags/v1.0.0",
        "digest": { "gitCommit": "abc123def456" }
      }
    ]
  },
  "runDetails": {
    "builder": {
      "id": "https://github.com/slsa-framework/slsa-github-generator/..."
    },
    "metadata": {
      "invocationId": "https://github.com/.../actions/runs/123456789",
      "startedOn": "2026-08-17T00:00:00Z",
      "finishedOn": "2026-08-17T00:05:00Z"
    }
  }
}
```

關鍵欄位的設計意圖：

- **`buildType`**：一個 URI，指向「這個 build 系統的 provenance schema 怎麼解讀」的文件。不同 build 系統（GitHub Actions、Tekton、Google Cloud Build）有不同的 buildType。
- **`externalParameters`**：用戶可以控制的輸入，是驗證時要特別注意的——攻擊者可能從這裡下手（例如改 workflow ref 指向惡意 fork）。
- **`builder.id`**：指向 build platform 的 URI，同時也是「決定 SLSA level 的關鍵」：`slsa-verifier` 用這個 URI 查詢對應的 SLSA level。
- **`resolvedDependencies`**：build 過程中真正用到的 dependency 的 digest，讓出事後能追溯「那個版本的 dependency 有問題嗎」。

## 實際驗 Level：slsa-verifier 怎麼判斷

`slsa-verifier verify-artifact` 讀到一份 provenance 時，它做的判斷邏輯可以用 pseudocode 描述：

```
輸入：artifact 檔案, .intoto.jsonl provenance

1. 解 DSSE envelope，驗簽章（用已知 trusted builder 的公鑰）
   → 失敗：「invalid signature」
   → 成功：繼續

2. 算 artifact 的 sha256，比對 Statement 裡的 subject digest
   → 不符：「subject digest mismatch」
   → 符合：繼續

3. 查 builder.id 是否在 trusted builder 清單裡
   → 不在清單：回報 L1，停止 level 評定
   → 在清單：繼續

4. 依 builder.id 對應的 trust model 評定 level：
   - GitHub Actions + slsa-github-generator@v2.1.0 → L3
   - 其他 hosted CI（視支援程度）→ L2 或 L3

5. 比對 --source-uri 是否與 provenance 的 externalParameters.repository 一致
   → 不符：「source mismatch」
   → 符合：PASSED，印出 level
```

這個流程說明了幾件事：

- **Builder 的公鑰是怎麼來的**：slsa-verifier 內建或從 Rekor 撈 keyless 簽章的憑證，再比對 Fulcio 的 CA 鏈，確認簽章的確是 GitHub Actions 的 OIDC token 換來的短期憑證簽的，不是攻擊者的私鑰。

- **`--source-uri` 是信任的錨點**：用戶呼叫 `slsa-verifier` 時明確告訴工具「我相信這個 artifact 應該來自 github.com/example/myapp」，如果 provenance 宣稱的 source 不符，即使簽章合法也拒絕。這防止「用 A repo 的 CI 產出一份看起來合法的 provenance 卻貼到 B repo 的 artifact 上」。

## 對比與取捨

| 考量 | 低 SLSA Level（L0/L1） | 高 SLSA Level（L2/L3） |
|---|---|---|
| 實作成本 | 低，現有 CI 稍微改一下 | 需要特定 CI 平台（GitHub Actions 等）和 reusable workflow |
| 防禦強度 | 主要是記錄，沒有強驗證 | 防 build 植入、防 provenance 偽造 |
| 驗證門檻 | 消費者端驗章即可 | 需要 slsa-verifier 或等效工具 |
| Key 管理 | 需要管理 signing key | L3 可以 keyless（OIDC），不需要長期 key |
| 法規要求 | EO 14028 提到 provenance，未強制 level | US DoD 某些合約開始要求 L2+ |
| 開源生態 | Go、Python 已有部分專案達 L3 | npm 生態支援仍在建設中（2026） |

## 踩雷集錦

1. **把舊版 SLSA（2021 初版，4 level）和 v1.0（Build L0–L3）混著用**：舊版的 SLSA 3 和 v1.0 的 Build L3 要求不完全相同，工具支援也不同。slsa-github-generator 產出的 provenance 是 v1.0 格式，slsa-verifier 1.x 只驗舊格式，需要 2.x 才驗 v1.0。在查工具版本時一定要確認「v1.0 還是舊版」。

2. **以為 SLSA = SBOM**：SLSA provenance 說「怎麼來的」，SBOM 說「有什麼」。兩個都要，缺一不可。只有 SBOM 你不知道 build 有沒有被植入；只有 SLSA provenance 你不知道有哪些元件需要追蹤漏洞。

3. **以為 L3 防止源碼被改**：L3 只保證「artifact 是從指定的 source 和 builder 產出的，build 過程符合特定隔離要求」，它不審查 source code 的內容。如果有人把惡意程式碼透過 PR merge 進 source，L3 的 provenance 會誠實地說「這個惡意 source 被 build 了」，不會攔截。

4. **`builder.id` 的 URI 不是隨便填的**：slsa-verifier 用 `builder.id` 查詢對應的 SLSA level。如果你的 CI 不是已知的受信 builder（例如 `slsa-framework/slsa-github-generator`），slsa-verifier 會回報 `unknown builder`，無法驗 level。要達到可驗的 L3，builder 必須是 slsa-verifier 能識別的已知 builder。

5. **本機手工產的 provenance 只是 L1**：用 `cosign attest-blob --type slsaprovenance1` 手動附一份 provenance 是可以的，也可以過 cosign 的驗章，但 slsa-verifier 會評定它為 L1（沒有 trusted builder），因為沒有受信 build platform 在背後保證 build 流程。

## 進階：再往深一層

**SLSA Source Track（未來方向）**

v1.0 聚焦 Build track；Source track（衡量「source code 的完整性有多可信」）在規範中有提及但尚未完整定義。未來預計會涵蓋「PR 需要 code review」、「強制 MFA」之類的 source 完整性要求。

**多個 Artifact 的 Provenance**

一個 build 可能產出多個 artifact（x86_64 binary + arm64 binary + checksums）。SLSA v1.0 的 subject 欄位是一個陣列，可以同時聲明多個 artifact，一份 provenance 覆蓋整個 build 的所有產出。

**Provenance 的傳遞性**

如果你的 artifact 依賴另一個 artifact，而那個 artifact 有 SLSA provenance，消費者理論上可以遞迴驗「每個依賴的來源都可信」。這就是「supply chain」一詞的核心——整條鏈都有保證，才是完整的供應鏈安全。實務上這還是研究議題，工具還沒有完整支援端到端的遞迴驗證。

## 動手練習

1. 去 [slsa.dev/spec/v1.0/levels](https://slsa.dev/spec/v1.0/levels) 讀 Build L2 和 L3 的完整要求，找出「哪一個要求讓 build 內部的惡意程式碼沒辦法自行簽發 provenance」，用自己的話寫出來。
2. 找一個你知道已經達到 SLSA 某 level 的開源專案（例如 [slsa.dev/blog](https://slsa.dev/blog) 有案例），看它的 release assets 裡有沒有 `.intoto.jsonl` 後綴的 provenance 檔案，用 `jq` 或 Python 解析看 `builder.id` 和 `buildType`。
3. 把 SBOM 和 SLSA provenance 的關係用一張自己畫的表格整理：哪些欄位各自有、各自回答的問題是什麼。

## 本章重點整理

- SLSA（讀 "salsa"，OpenSSF）是衡量 **build 完整性**的分級框架，核心概念是 **provenance**（來源證明）。
- v1.0 的 Build track 有四個 level：L0（無）、L1（有 provenance）、L2（有簽章的 provenance + 託管 build）、L3（強隔離 + signing key 與 user build 隔離）。
- 只有 L3 才能防「build 過程中植入後門」這類 SolarWinds 式攻擊。
- SBOM 說「有什麼」，SLSA provenance 說「怎麼來的」，兩者互補，完整的供應鏈安全都需要。
- Provenance 是一個 in-toto Statement，`predicateType` = `https://slsa.dev/provenance/v1`，`predicate` 包含 `buildDefinition`（輸入）和 `runDetails`（builder + metadata）。

## 自我檢核

- [ ] 我能說出 SLSA Build L1 / L2 / L3 各自的核心要求（不靠查表）
- [ ] 我能解釋 L3 為什麼能讓 build script 植入的惡意程式碼無法偽造合法 provenance
- [ ] 我能說出 SBOM 和 SLSA provenance 各回答什麼問題、格式上的差異
- [ ] 我知道 SLSA v1.0 和舊版（4-level）的差異，不會混用

## 延伸閱讀

- **[SLSA v1.0 Build track levels](https://slsa.dev/spec/v1.0/levels)**（slsa.dev 官方）
  - **讀哪裡**：L0–L3 的「Requirements」和「Threats addressed」欄位，本章的 level 定義全部來自這裡
  - **為什麼值得讀**：規範比任何二手資料都準確，且有很多部落格把 v1.0 和舊版混著用，回到原始規範才能辨別

- **[SLSA v1.0 Provenance schema](https://slsa.dev/spec/v1.0/provenance)**（slsa.dev 官方）
  - **讀哪裡**：`buildDefinition`、`runDetails`、`builder.id` 的欄位定義和「Required for SLSA Build L1/L2/L3」的標注
  - **和本章的關聯**：Ch 23 手寫 provenance 時會嚴格按這份 schema

- **[OpenSSF SLSA GitHub repository](https://github.com/slsa-framework/slsa)**
  - **讀哪裡**：`docs/` 裡的 threat model 文件，尤其是「Threats」那節
  - **為什麼值得讀**：比網站更詳細的威脅模型說明，包含每個 level 防禦的具體攻擊者能力假設

- **[SLSA in Practice（Sigstore blog）](https://blog.sigstore.dev/)**
  - **讀哪裡**：搜「SLSA」，有幾篇從 slsa-github-generator 實際使用角度介紹 L3 實現
  - **和本章的關聯**：Ch 23 會直接用 slsa-github-generator，這些文章是背景

- **[Google SLSA 實踐報告](https://security.googleblog.com/2022/04/how-google-is-using-slsa-to.html)**（Google Security Blog）
  - **讀哪裡**：整篇，約 10 分鐘，Google 描述他們如何在內部推 SLSA 以及遇到的現實挑戰
  - **為什麼值得讀**：最大規模的 SLSA 採用案例，讓你知道「理論上 L3 很好，實務上推起來哪裡難」

下一章進入實務：怎麼真的產出一份 SLSA provenance，本機手工版和 GitHub Actions CI 版各怎麼做，以及用 `slsa-verifier` 驗它。

→ [Ch 23 生出 SLSA provenance](./23-generating-slsa-provenance.md)
