# Ch 18 — 供應鏈攻擊面全景

> **目標**：從防禦者視角，系統性地理解軟體供應鏈能被打的每個環節——源碼、依賴、build 系統、散布、部署——並且搞清楚「光有 SBOM」能擋什麼、不能擋什麼。這章是後面信任鏈四章（Ch 19–22）的動機章：看完你會知道為什麼我們需要 hashing、in-toto、sigstore、SLSA，而不只是一份清單。

## 為什麼需要這個？

前四個 Part 教你把 SBOM 生出來、掃漏洞、用 VEX 降噪、用 Dependency-Track 長期監控。這些技術都假設一件事：**你手上的 artifact 是你以為的那個 artifact**。

SolarWinds 那次，客戶拿到的是一份有效簽章的 Orion 更新包，SBOM 上的依賴清單也是正確的。只是在 build 時，攻擊者悄悄把 SUNBURST 後門注入了編譯流程。任何「分析這份 binary 有什麼元件」的工具，都找不到問題——因為問題不在「有什麼元件」，而在「這個元件是怎麼被產出來的、產出過程有沒有被竄改」。

**這就是為什麼 SBOM 不夠，還要信任鏈（trust chain）**。信任鏈解決的問題是：

- 這份 artifact 是不是真的由聲稱的人產出的？（identity）
- 這份 artifact 在傳輸途中有沒有被動過？（integrity）
- 產出這份 artifact 的流程，有沒有符合我們定義的規格？（provenance）

這三個問題，對應 Ch 19（integrity + provenance）→ Ch 20（sigstore identity）→ Ch 22（SLSA 流程規格）。這章先把攻擊面的地圖畫出來，讓後面幾章的每個工具都有對應的「它在擋這裡」。

## 先建立直覺：供應鏈是一條管線

把軟體從寫好到跑在生產環境這段路，想成一條實體工廠的供應鏈：

```
  原料採購       加工          品管            包裝出貨         倉庫配送          上架
  (依賴)        (build)       (測試/簽章)      (registry)       (mirror/CDN)     (部署)
```

每一個環節都可以被動手腳。不同的是，軟體供應鏈的每個節點都在網路上，攻擊者不用進工廠——他可以在遠端偽造上游材料、在網路上騙你的 build 系統抓錯包、在 registry 上傳同名惡意版本。

MITRE ATT&CK 有一個專門的 Tactic：`TA0001 Initial Access`，其中技術 `T1195 Supply Chain Compromise` 把攻擊面分成三個子類：

- `T1195.001`：軟體依賴 (Software Dependencies)
- `T1195.002`：Build 工具與流程 (Software Development Lifecycle)
- `T1195.003`：硬體（本課不涉及）

SLSA framework 的 threat model 則從「誰在哪一步能做什麼」出發，把威脅分成 insider threat、infrastructure compromise、source compromise、build compromise 等類型。我們接下來按照供應鏈的五個環節，逐一拆解。

## 環節一：源碼與開發者帳號

### 攻擊向量

**帳號盜用（Account Takeover）**：攻擊者取得 GitHub/GitLab 帳號，直接 push 惡意 commit。方式包含釣魚、洩漏的 PAT（Personal Access Token）、OAuth token 盜竊。

**惡意 commit / PR 注入**：攻擊者對開源專案送 PR，混入惡意程式碼，等待維護者 review 不仔細而 merge。常見手法是讓惡意程式碼在 diff 裡不顯眼（放在大型重構裡、利用 Unicode 雙向文字控制字元隱藏惡意邏輯）。

**長期社會工程滲透（Long-term Social Engineering）**：這是 xz-utils 事件的核心手法，也是迄今最精密的已知案例。

### 案例：xz-utils CVE-2024-3094（2024 年 3 月，CVSS 10.0）

這個案例值得深入，因為它展示了耐心程度遠超一般認知的供應鏈攻擊。

**時間線：**

- **2021 年底**：GitHub 帳號 `JiaT75`（自稱 Jia Tan）建立，開始對多個開源專案貢獻程式碼，建立看似真實的貢獻歷史。
- **2022–2023 年**：Jia Tan 開始向 xz-utils 的維護者 Lasse Collin 貢獻 patch。在這段期間，有疑似協調的壓力——其他帳號向 Lasse 施壓，抱怨他回應太慢、要求他讓 Jia Tan 取得 co-maintainer 權限。
- **2023 年初**：Jia Tan 成功取得 co-maintainer 權限，開始直接 merge 自己的 patch，甚至把 oss-fuzz 專案的聯絡 email 改成自己的。
- **2024 年 2 月**：xz-utils 5.6.0 和 5.6.1 發布，release tarball 中包含了精心構造的惡意 build script（`m4/build-to-host.m4`）。這段惡意程式碼**不在 git repository 裡**——它藏在 release tarball 的額外二進位測試檔案中，靠 autoconf 流程在 build 時自動提取並注入。
- **2024 年 3 月 29 日**：Microsoft 工程師 Andres Freund 在 PostgreSQL 的效能分析中，注意到 `sshd` 消耗 CPU 異常，追查下去發現了這個後門。他在 Openwall 郵件列表上公開披露。

**後門目標**：注入 `liblzma`，而許多 Linux 發行版的 `systemd` 和 `sshd` 都連結到這個函式庫。後門攔截 OpenSSH 用到的 `RSA_public_decrypt`，拿收到的認證資料去比對攻擊者預置的 **Ed448** 公鑰；只有握有對應 Ed448 私鑰的攻擊者能通過驗證，在受影響的系統上實現未認證的遠端程式執行（RCE）。

**SBOM 能擋到什麼？** 幾乎什麼都擋不到。後門藏在 release tarball 的 build 流程裡，不是一個普通的惡意依賴；SBOM 看到的 xz-utils 版本號是正確的，看不到 build 過程有什麼不對。

**信任鏈能擋到什麼？** 如果有完整的 in-toto layout 定義「允許哪些人執行哪些 build 步驟、用哪些輸入材料」，且簽章要求用受信任的 CI 基礎設施（SLSA Level 3+），攻擊者在 build 時注入惡意程式碼就會留下可被驗證的異常痕跡。Ch 22 的 SLSA 正是針對這類攻擊的回應。

---

## 環節二：依賴（第三方套件）

依賴鏈攻擊是目前出現頻率最高、進入門檻最低的供應鏈攻擊類型。你的程式碼可能很安全，但你的 `node_modules` 有幾千個套件，每個都是潛在進入點。

### 攻擊手法一：Typosquatting（打錯字攻擊）

攻擊者在 npm/PyPI/RubyGems 上傳一個名字和熱門套件只差一個字元的惡意套件。常見模式：

- `requesst`（vs `requests`）
- `cross-env` vs `crossenv`
- 多餘的 `-`、`_`，或前後綴（`python-urllib3` vs `urllib3`）

使用者手殘打錯名字裝到惡意版本，或攻擊者直接把名字注進 AI 助理的建議裡（「AI 幻覺套件」型變體）。

**SBOM 能擋到什麼？** SBOM 忠實記錄你裝了什麼，但不知道你「本來應該裝什麼」。不過，SBOM + 套件名稱白名單審查可以發現拼寫異常——這是一種消費端防禦。

### 攻擊手法二：Dependency Confusion（依賴混淆）

2021 年 2 月，安全研究員 Alex Birsan 公開了這個技術，並成功在 Apple、Microsoft、PayPal、Shopify、Netflix、Tesla 等超過 35 家公司的內部系統執行程式碼，合計獲得超過 13 萬美元的 bug bounty。

**原理：**

```
公司內部 registry 有個私有套件：  @mycompany/auth-service  v1.2.0
攻擊者在 npm 公開 registry 上傳：  @mycompany/auth-service  v999.0.0

npm 在解析依賴時，看到公開 registry 有更高版本
→ 選擇攻擊者的 v999.0.0，而不是內部的 v1.2.0
```

問題的根源是套件管理器預設「公開 registry 版本號高者優先」，而許多組織的 CI 環境同時設定了內部 registry 與公開 registry，沒有嚴格隔離。

**SBOM 能擋到什麼？** 事後，SBOM 會記錄你用的版本是 v999.0.0，可以拿來比對「你的合法套件在自己的 registry 上應該是什麼版本」。但攻擊發生時，SBOM 本身不阻止它。正確的防禦是 registry 優先順序設定、scope scoping，以及把內部套件名稱預先在公開 registry 佔位（防禦性搶注）。

### 攻擊手法三：維護者劫持（Maintainer Takeover）

最經典的案例是 2018 年的 `event-stream` npm 事件。

**時間線（2018）：**

- `event-stream` 是 npm 下載量極高的工具套件（每週數百萬次下載）
- 原作者 Dominic Tarr 長期無暇維護，有人主動表示願意接手
- 2018 年 9 月，維護權轉移給 `right9ctrl` 帳號
- 新維護者加入了一個新依賴：`flatmap-stream`，其中包含加密的惡意 payload
- 惡意 payload 只在特定條件下觸發：偵測到宿主套件的 `npm_package_description` 環境變數等於 "A Secure Bitcoin Wallet"（即 BitPay 的 Copay 錢包）
- 目標是竊取錢包餘額超過 100 BTC 的帳號的私鑰

**Copay 官方錢包 v5.0.2 到 v5.1.0 帶著這個後門出了貨。** 2018 年 11 月 20 日被發現並公開。

**SBOM 能擋到什麼？** 如果你在 event-stream 更新前已有 SBOM，更新後做差異比對，可以看到新增了 `flatmap-stream` 這個從沒有過的依賴——這是個異常訊號。但大多數組織當時沒有這層監控。這正是 Dependency-Track（Ch 17）的價值：持續比對 SBOM 快照，新依賴出現時發出警告。

### 攻擊手法四：惡意套件直接發布

攻擊者直接在 PyPI/npm/crates.io 發布新的惡意套件，等待受害者裝到它。手法包含：

- 製造看似有用的套件（機器學習工具包、CLI 輔助工具），在安裝時執行惡意程式碼（`setup.py` / `install` script）
- 在現有套件的高版本號中植入後門（beta 或 rc 版本）
- 利用套件 readme 的 SEO 讓搜尋排名靠前

**SBOM 能擋到什麼？** SBOM + 比對 OSV/NVD 資料庫可以發現已知惡意套件（如果漏洞庫已更新）。但在新惡意套件被 registry 安全團隊發現並報告之前，這個視窗期是盲點。

---

## 環節三：Build 系統

### 案例：SolarWinds SUNBURST（2020 年 12 月揭露）

這是迄今最嚴重的已知供應鏈攻擊之一，受害者包含美國財政部、國防部、DHS，以及數十家頂級企業。

**攻擊手法：**

攻擊者（後來歸因於俄羅斯 SVR 情報機構，稱為 APT29/Cozy Bear）**不修改 source code repository**。他們入侵的是 SolarWinds 的 build 環境，部署了一個專門的惡意工具 **SUNSPOT**，在 Orion 軟體的 build 流程中自動注入後門程式碼 **SUNBURST**。

```
正常 build 流程：
  source code  ─────────────────────→  Orion.exe（乾淨）

被入侵後的流程：
  source code ──→ [SUNSPOT 注入後門] ──→  Orion.exe（含 SUNBURST）
```

**關鍵點：**

- 從 2019 年 10 月，攻擊者就已進入 SolarWinds 系統測試注入
- 2020 年 3 月開始，含後門的 Orion 更新包（`2019.4.5250.9374` 等版本）帶著有效的 SolarWinds 數位簽章推送給約 17,000 名客戶
- SolarWinds 官方的簽章是真的——問題在簽章前的 binary 就已被動了手腳
- 2020 年 12 月，FireEye 在調查自身入侵事件時發現，並協同 CISA 公開披露

**SBOM 能擋到什麼？** 什麼都擋不到。SBOM 描述的是「這個 artifact 包含哪些元件」，而那份 Orion.exe 的元件清單是正確的——只是多了一個不存在於 source code 的後門。這正是 **provenance（來源證明）** 要解決的問題：不只證明 artifact 完整，還要證明「這個 artifact 是在受控的 build 環境裡、由合法的 build 流程產出的」。SLSA Level 3 要求 build 在隔離的環境（hardened build service）執行，且環境本身經過獨立稽核，使得這類 build 環境入侵更難實現並更容易偵測。

### 其他 Build 系統攻擊向量

**CI/CD pipeline 設定被改**：GitHub Actions workflow YAML 被改，加入一個惡意 step，在 build 完成後把產物上傳到攻擊者的伺服器，或把後門寫進去再繼續正常流程。

**惡意 GitHub Actions action**：在你的 workflow 裡引用第三方 action（`uses: some-user/some-action@v2`），如果那個 action 維護者帳號被盜，tag `v2` 被指向惡意 commit，你下次 build 就執行了惡意程式碼。這正是為什麼建議用 commit hash 而非 tag 釘定版本（`uses: some-user/some-action@abc1234`）。

**build 腳本中的動態下載**：Makefile / Dockerfile / CI script 裡有 `curl ... | sh` 或 `wget ... && ./install.sh`，這些動態下載在 build 時可能被 MITM 或 DNS 劫持，抓到惡意版本。

---

## 環節四：散布（Registry 與 Mirror）

### Registry 投毒

公開 registry（npm、PyPI、Docker Hub、Maven Central）被直接植入惡意套件，或維護者帳號被盜後上傳惡意版本。

**緩解**：registry 端的安全審查、多因素認證強制、簽章驗證（npm provenance、PyPI Trusted Publishers）。但 registry 是中心化的，對其本身的 compromise 沒有完美防禦。

### Mirror 與 CDN 劫持

許多組織用內部 mirror 或 CDN 快取 artifact，這些節點成為攻擊點。攻擊者入侵 mirror 後，修改快取的 artifact，讓所有從這個 mirror 下載的客戶都拿到被動了手腳的版本。

**Hash 驗證**的作用在這裡最直接：如果下載後驗 SHA-256，然後和 registry 上公告的 hash 對比，即使 mirror 被投毒也能在安裝前發現。但大多數工具預設不做這一步，或驗 hash 用的 metadata 也從同一個被入侵的 mirror 拿。

### MITM（中間人攻擊）

在網路層面，攻擊者攔截 `http://` 的 artifact 下載，替換成惡意版本。這在 2015 年之前更常見（`pip install` 曾預設用 HTTP）。現代 registry 幾乎都強制 HTTPS，大幅減少此類攻擊；但在企業內網或 VPN 環境中，TLS inspection proxy 可能成為一個人為引入的 MITM 點。

---

## 環節五：部署（Container Image 替換）

**Image Tag 不可信**：`docker pull myapp:latest` 中的 `latest` 是可變的 tag，指向什麼隨時可以改。如果你的 CD pipeline 用 tag 而非 image digest 部署，registry 上的 `latest` 被推了一個惡意版本，你下次部署就跑了惡意 image。

**Digest 釘定的正確做法：**

```bash
# 不安全：tag 可變
docker pull myapp:latest

# 安全：digest 是 SHA256，不可變
docker pull myapp@sha256:a1b2c3d4...
```

**倉庫設定被改**：Kubernetes `imagePullPolicy: Always` 加上 tag 部署，每次 pod 重啟都重新拉 image，給了攻擊者持續的投毒機會。改成 digest 部署，加上 admission webhook 驗證簽章（如 Cosign + Kyverno），才算真正閉環。

---

## 攻擊面彙整：SBOM + 信任鏈各擋什麼

```
攻擊環節                攻擊類型                  SBOM能偵測？  信任鏈能防禦？
─────────────────────────────────────────────────────────────────────────────
源碼 / 開發者帳號       帳號盜用 + 惡意 commit    部分(*1)       in-toto (*2)
                       社工滲透 (xz-utils)        否             SLSA L3+ (*3)
                       Unicode 隱藏惡意程式碼     否             code review

依賴套件               typosquatting              部分(*4)       否
                       dependency confusion        事後記錄       否(*5)
                       維護者劫持 (event-stream)  差異比對(*6)   sigstore provenance
                       惡意新套件                  已知CVE       否(零時差)

build 系統             build env 被入侵 (SolarWinds) 否          SLSA L3 硬化環境
                       CI script / Action 被改     否            commit hash 釘定
                       動態下載被攔截              否            deterministic build

散布 / registry        registry 投毒              已知CVE        簽章驗證 (*7)
                       mirror MITM                否             hash 驗證
                       CDN 被換                   否             hash + 簽章

部署                   tag 被替換                  否            cosign + digest
                       image 掉包                  否            admission webhook
```

*1: SBOM 可以記錄 artifact 的 hash，但不能證明 source code 是否被惡意修改。
*2: in-toto 可以定義「source code 由哪些合法 key 簽章的人 commit」，在 layout 裡強制執行。
*3: SLSA L3 要求在隔離、稽核過的 build service 執行，使 build env 入侵更難且可被偵測。
*4: SBOM 記錄了套件名稱，可以對比拼寫白名單。
*5: 正確的 registry 優先順序設定可以防止 dependency confusion；Artifact Hub / OCI 的 scope 設定也是。
*6: 新增依賴會在 SBOM diff 中出現，Dependency-Track 可以設警報。
*7: sigstore 的 cosign attach 讓每個 registry artifact 都有可驗證的簽章，讓投毒後的版本缺乏合法簽章而被驗章步驟擋下。

---

## 底層機制：SLSA Threat Model 的框架語言

SLSA（Supply chain Levels for Software Artifacts，Ch 22 深挖）把威脅分成四類：

```
  ┌─────────────────────────────────────────────────────────┐
  │  D: 直接修改 artifact（在散布 / 部署階段替換）           │
  │      → 防禦：artifact 簽章 + digest 驗證                │
  ├─────────────────────────────────────────────────────────┤
  │  C: 竄改 build 流程（build env 被入侵、CI 被改）         │
  │      → 防禦：hardened build service + build provenance  │
  ├─────────────────────────────────────────────────────────┤
  │  B: 修改 source code（帳號盜用、惡意 PR）                │
  │      → 防禦：branch protection + two-party review       │
  ├─────────────────────────────────────────────────────────┤
  │  A: 修改依賴（任何上游輸入）                             │
  │      → 防禦：version pinning + hash 驗證 + 依賴審查      │
  └─────────────────────────────────────────────────────────┘
           （D 最容易防，A 是最難系統性解決的）
```

ATT&CK T1195 的分類和這個大致對應，不過 SLSA 的框架更適合用來設計防禦，因為它對每個級別列出了具體的要求。

---

## 對比與取捨

| 防禦手段 | 能擋什麼 | 擋不了什麼 | 主要成本 |
|---|---|---|---|
| SBOM（清單） | 已知 CVE 比對、依賴清點 | build 流程被竄改、社工滲透 | 生成與維護管線 |
| Hash 驗證 | artifact 下載途中被換 | 原始 build 就已被動手腳 | 幾乎免費 |
| 簽章（cosign） | 驗證 artifact 來源身分 | 簽章者本身帳號被盜 | key 管理（keyless 減輕） |
| in-toto provenance | 整條 build pipeline 完整性 | layout 定義錯誤、功能者 key 被盜 | 在每個 step 加 instrumentation |
| SLSA Level 3 | hardened build + 可驗證 provenance | 攻擊者已控制 layout 定義者 | build service 架構重建 |
| code review 政策 | 明顯惡意程式碼 | 精心隱藏的社工手法（xz-utils） | 人力 |

## 踩雷集錦

1. **「我有 SBOM，應該沒問題了」**：這是這章最要打破的誤解。SBOM 是「清點」，信任鏈是「驗真」，兩者解決的不是同一個問題。SBOM 告訴你「你的 artifact 裡有哪些元件」，不告訴你「這個 artifact 是不是你以為的那個人以你以為的方式做出來的」。

2. **「簽了章就安全」**：SolarWinds 的 Orion 更新有 SolarWinds 官方的有效數位簽章。問題是簽章的 binary 在 build 時就已被動了手腳。簽章只保證「這個東西是簽章者發出來的」，不保證「簽章者的 build 環境沒有被入侵」。這就是 build provenance（SLSA）的意義。

3. **「開源就透明、透明就安全」**：xz-utils 是完全開源的，source code 在 GitHub 上公開，任何人都可以看。但那兩年間幾乎沒有人注意到惡意的社工滲透。開源的透明性是潛力，不是保證；需要主動的 code review、安全掃描、和貢獻者信任機制才能實現。

4. **「用 hash 就能防 dependency confusion」**：Dependency confusion 成功的條件之一是 `package-lock.json` 或 `go.sum` 還沒有鎖定那個套件的 hash（或者 lock file 不受信任地被重新生成）。正確的防禦是 registry 配置和 scope 隔離，而不只是 hash 驗證。

5. **「只有大型企業才是目標」**：event-stream 的最終目標是 Copay 錢包，攻擊者的選擇標準是「誰引入了這個套件、這個 payload 能帶來什麼收益」。中小型 crypto 服務、熱門 CLI 工具的使用者都是潛在目標，攻擊者的觸達成本接近零。

## 進階：再往深一層

**理解攻擊者的經濟學**：供應鏈攻擊的吸引力在於「一次入侵，觸達 N 個下游」——入侵一個 build 系統，比分別入侵 17,000 個客戶容易得多。這個乘數效應讓供應鏈攻擊的投資回報比直接攻擊終端目標高出幾個數量級。防禦者要理解這個邏輯，才能知道應該把資源集中在保護哪個節點。

**SLSA 的現實困境**：SLSA Level 3 要求 build 在「可稽核的、隔離的 hardened build service」上執行，並且 build 過程本身必須是可重現的（reproducible build）。Debian 的 [reproducible-builds.org](https://reproducible-builds.org) 已在推動這件事很多年，但要讓整個生態達到 SLSA L3 還很遠。更現實的路徑是：先達到 SLSA L1（有 provenance，哪怕是 unsigned 的），再往 L2（signed）、L3 推進。

**內部威脅（Insider Threat）**：以上大多數場景都假設外部攻擊者。SLSA 的 threat model 也考慮「你的 CI 管理員本身變成攻擊者」的情境。這時，除了技術控制，還需要加入職責分離（separation of duties）、稽核日誌（audit log）、和定期的安全審查。

## 動手練習

1. 搜尋你目前任意一個專案的 `package.json` 或 `go.mod`，把所有直接依賴貼進 [socket.dev](https://socket.dev) 或 [deps.dev](https://deps.dev) 掃一遍，看看有沒有近期剛換維護者的套件——這是 event-stream 型攻擊的早期訊號。

2. 在 GitHub 上搜尋 `path: .github/workflows "curl" "pipe" "sh"`，看看有多少公開 repo 的 CI workflow 裡有 `curl | sh` 這種動態下載。感受一下「有多少 build pipeline 對這個向量毫無防禦」。

3. 在你的 WSL 環境試試 `pip install requets`（故意打錯字），看看 PyPI 回傳什麼。然後試 `pip install -i https://pypi.org/simple/ requets`，比較搜尋結果。這給你直覺「typosquatting 在技術上有多容易踩到」。

## 本章重點整理

- 軟體供應鏈有**五個主要攻擊面**：源碼（帳號盜用 / 社工）、依賴（typosquatting / dependency confusion / 維護者劫持）、build 系統（SolarWinds 模式）、散布（registry 投毒 / MITM）、部署（image tag 被換）。
- **SBOM 是清點工具，不是信任工具**。它告訴你有什麼，不告訴你這個「有什麼」是不是你以為的那個「有什麼」。
- **簽章只保護最後一哩**（傳輸完整性），不保護 build 流程。SolarWinds 的教訓是「有效簽章 ≠ 安全 artifact」。
- 信任鏈需要三個層次：完整性（hash）＋ 身分（簽章）＋ 流程合規性（provenance / SLSA）。
- ATT&CK T1195 和 SLSA threat model 是描述和討論供應鏈威脅的標準語言，值得熟悉。

## 自我檢核

- [ ] 我能說出 xz-utils 攻擊的「為什麼難被發現」：後門藏在 release tarball 而非 git repo，且花了兩年建立信任
- [ ] 我能解釋 dependency confusion 和 typosquatting 的根本差異：前者利用 registry 解析邏輯、後者利用人的打字錯誤
- [ ] 我能說出 SolarWinds SUNBURST 為什麼讓簽章驗證完全失效，以及需要什麼機制才能防
- [ ] 我能把「SBOM 能擋什麼」和「信任鏈能擋什麼」分開說清楚，不混用

## 延伸閱讀

- **[MITRE ATT&CK T1195 Supply Chain Compromise](https://attack.mitre.org/techniques/T1195/)**
  TTP 語言的供應鏈攻擊分類，面試時說出 T1195.001/002 讓你立刻顯得專業；裡面的「Procedure Examples」列了真實案例，比任何博客文章都更有參考價值。

- **[SLSA Threat Model](https://slsa.dev/spec/v1.0/threats)**
  SLSA 官方的威脅模型文件，把這章的五個環節用更正式的框架再說一遍，並對每個威脅列出對應的 SLSA Level 要求；Ch 22 的前置閱讀。

- **[XZ Utils Backdoor — Wikipedia](https://en.wikipedia.org/wiki/XZ_Utils_backdoor)**
  目前最完整的 xz-utils 事件整理，有詳細時間線和技術分析；Andres Freund 的原始 Openwall 帖子連結也在裡面，那封信的技術分析深度非常值得讀。

- **[Dependency Hijacking — Sonatype Blog](https://www.sonatype.com/blog/dependency-hijacking-software-supply-chain-attack-hits-more-than-35-organizations)**
  Alex Birsan dependency confusion 研究的第一手技術說明，包含他用來收集內部套件名稱、上傳惡意套件的具體方法。

- **[A Post-Mortem of the Malicious event-stream Backdoor — Snyk](https://snyk.io/blog/a-post-mortem-of-the-malicious-event-stream-backdoor/)**
  event-stream 事件的技術事後分析，包含惡意 payload 如何解密、如何靶向特定 app 的細節；是「依賴鏈攻擊可以有多精準」的最佳教材。

---

五個環節、三個真實案例、一張「誰能擋什麼」的地圖——現在你知道攻擊面在哪了。接下來兩章要把防禦工具一個一個建起來。第一步：**完整性（integrity）和來源證明（provenance）**，這是信任鏈的技術地基。

→ [Ch 19 完整性與來源證明：hashing / in-toto](./19-integrity-provenance.md)
