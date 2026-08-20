# SBOM 學習筆記：從「你的軟體裡有什麼」到端到端供應鏈安全

> 給想搞懂軟體物料清單（SBOM）、並把它接進真實供應鏈安全流程的工程師與資安人。

這系列用 SBOM 當主軸，往外延伸到它所在的整個軟體供應鏈安全生態：格式（SPDX / CycloneDX）、生成、品質、漏洞比對、VEX、簽章與來源證明（sigstore / in-toto / SLSA）、法規治理。全程在 WSL 用 syft / grype / trivy / cosign / dependency-track 真跑驗證。學完你能自己建一條「產 SBOM → 簽章 + provenance → 掃描降噪 → 持續監控」的供應鏈安全管線，並在新 CVE 爆發時回答「我到底中了沒」。

## 為什麼學這個？

- **實用角度**：Log4Shell 爆發那晚，全世界工程師在問同一句「我的系統裡到底有沒有 log4j？」。有 SBOM 的人幾分鐘知道答案，沒有的人翻了三天。SBOM 是把這個問題從「考古」變成「查表」的東西。
- **底層理解**：SBOM 不是「跑個工具吐一份清單」就完事——元件識別（naming）、生成盲點、漏洞比對的誤報、VEX 降噪、簽章信任鏈，每一環都有真實的難題。懂機制才知道一份 SBOM 什麼時候能信、什麼時候在騙你。
- **職涯 / 合規角度**：US EO 14028、EU Cyber Resilience Act、FDA 醫材規範都開始強制要 SBOM。這從「加分技能」正在變成「賣軟體給政府 / 上市的門票」。供應鏈安全是目前資安最熱、最缺人的方向之一。

## 先修知識

- **命令列與容器基礎**（程度：能用 docker build、跑 CLI、讀 JSON/YAML）
- **軟體依賴的概念**（程度：知道 npm/pip/go mod/maven 是在做什麼，知道「傳遞依賴」是什麼）
- **基本資安直覺**（程度：知道 CVE 是什麼、hash 與簽章大概在幹嘛即可）
- 沒有也沒關係的：密碼學細節（簽章章會從頭講）、特定雲平台、任何 SBOM 先備知識

## 課程地圖

### Part 0 — 心智模型與環境（Ch 0–1）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼需要 SBOM](./01-why-sbom.md)

### Part 1 — SBOM 是什麼（Ch 2–4）
- [Ch 2 SBOM 的本質：從製造業 BOM 到 dependency graph](./02-what-is-sbom.md)
- [Ch 3 最小要素與生命週期：六型 SBOM 各看到什麼](./03-minimum-elements-lifecycle.md)
- [Ch 4 元件識別的難題：naming / PURL / CPE / SWID](./04-component-identity.md)

### Part 2 — 格式深挖（Ch 5–8）
- [Ch 5 SPDX 深挖](./05-spdx-deep-dive.md)
- [Ch 6 CycloneDX 深挖](./06-cyclonedx-deep-dive.md)
- [Ch 7 SPDX vs CycloneDX 對比與選型](./07-spdx-vs-cyclonedx.md)
- [Ch 8 授權資訊與 license compliance](./08-license-info.md)

### Part 3 — 生成（Ch 9–12）
- [Ch 9 生成策略：source vs build vs binary 分析](./09-generation-strategies.md)
- [Ch 10 syft 生成與內部：catalogers 怎麼認 package](./10-syft-internals.md)
- [Ch 11 build-time 生成：各語言生態](./11-build-time-generation.md)
- [Ch 12 SBOM 品質與完整度](./12-sbom-quality.md)
- [練習 A：三種來源 SBOM 比對](./practice-a-three-source-sbom.md)

### Part 4 — 消費與漏洞管理（Ch 13–17）
- [Ch 13 SBOM 怎麼變成價值：component → vulnerability](./13-sbom-to-value.md)
- [Ch 14 漏洞資料庫：NVD/CVE/CPE 的痛與 OSV](./14-vulnerability-databases.md)
- [Ch 15 掃描實戰：grype / trivy / osv-scanner](./15-scanning-in-practice.md)
- [Ch 16 VEX：有漏洞不等於可被利用](./16-vex.md)
- [Ch 17 Dependency-Track 營運](./17-dependency-track.md)
- [練習 B：VEX 降噪 + Dependency-Track 監控](./practice-b-vex-and-monitoring.md)

### Part 5 — 信任：簽章、來源證明、attestation（Ch 18–23）
- [Ch 18 供應鏈攻擊面全景](./18-supply-chain-attack-surface.md)
- [Ch 19 完整性與來源證明：hashing / in-toto](./19-integrity-provenance.md)
- [Ch 20 sigstore 原理：cosign / fulcio / rekor](./20-sigstore.md)
- [Ch 21 簽 SBOM 與 attestation](./21-signing-sbom-attestation.md)
- [Ch 22 SLSA framework](./22-slsa-framework.md)
- [Ch 23 生出 SLSA provenance](./23-generating-slsa-provenance.md)
- [練習 C：signed SBOM + SLSA provenance](./practice-c-signed-sbom-provenance.md)

### Part 6 — 治理、法規、落地（Ch 24–29）
- [Ch 24 法規版圖：EO 14028 / EU CRA / FDA](./24-regulations.md)
- [Ch 25 企業導入 SBOM 計畫](./25-enterprise-sbom-program.md)
- [Ch 26 SBOM 分發與交換](./26-sbom-distribution.md)
- [Ch 27 SBOM 之外的 xBOM：SaaSBOM / AI-BOM / HBOM](./27-xbom.md)
- [Ch 28 SBOM 與 DFIR / 藍隊](./28-sbom-dfir.md)
- [Ch 29 局限、批評與現實](./29-limitations-critiques.md)

### Part 7 — Capstone
- [Final Project：端到端供應鏈安全 pipeline](./final-project-supply-chain-pipeline.md)

### Part 8 — SBOM 系統設計與研究方法（設計者 / 研究者視角）

> 前面在教你**操作** SBOM；這個 Part 教你**設計**一套 SBOM 系統、以及學界怎麼想這件事。以精讀論文為骨架、論理導向（不是跑工具），可獨立於 Part 7 閱讀。

- [Ch 30 從操作者到設計者：SBOM 系統的架構空間](./30-sbom-system-design-space.md)
- [Ch 31 命名與版本求解的理論地基](./31-naming-version-resolution.md)
- [Ch 32 生成引擎的架構：從 manifest 到二進位識別](./32-generation-engine-architecture.md)
- [Ch 33 消費平台的架構：漏洞關聯與可達性](./33-consumption-platform-correlation.md)
- [Ch 34 可達性分析與 VEX 自動化](./34-reachability-vex-automation.md)
- [Ch 35 威脅模型與防禦設計](./35-threat-model-defense-design.md)
- [Ch 36 信任與完整性的系統設計](./36-trust-integrity-system-design.md)
- [Ch 37 SBOM 的實證現況與研究地圖](./37-empirical-state-research-map.md)
- [設計 Capstone：你自己的 SBOM 系統架構設計文件](./design-capstone-sbom-architecture.md)

## 學習方式建議

1. **讀完一章就動手**：這門課大半章節都有可跑的工具範例，讀完馬上在你自己的 WSL 跑一遍，別只看輸出。
2. **故意把它弄壞**：刪掉 SBOM 裡一個 component 再去掃，看漏報怎麼發生；亂改一個 PURL，看比對怎麼失準。SBOM 的坑幾乎都在「它以為它看到了全部，但其實沒有」。
3. **拿真專案開刀**：範例會用真實 container image 與開源專案，不是玩具。final 要求你對一個真實專案端到端跑完整條管線。

## 精選資料庫

這裡列整門課最值得反覆參照的權威來源，每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[CISA SBOM 官方頁面](https://www.cisa.gov/sbom)**
  - 美國政府推 SBOM 的權威入口，NTIA minimum elements、SBOM types、VEX 文件都從這裡連出去；遇到「官方定義到底是什麼」時的最終仲裁
- **[SPDX 規範](https://spdx.github.io/spdx-spec/)** 與 **[CycloneDX 規範](https://cyclonedx.org/specification/overview/)**
  - 兩大格式的權威文件；Part 2 的實作全部對照這兩份 spec

### 推薦工具文件

- **[syft](https://github.com/anchore/syft)** / **[grype](https://github.com/anchore/grype)**（Anchore）
  - 本課生成與掃描的主力工具，README 與 wiki 是最新行為的第一手來源
- **[OWASP Dependency-Track](https://docs.dependencytrack.org/)**
  - 把 SBOM 當持續監控資產庫的參考實作，Part 4 營運章的主角

### 標準與框架

- **[SLSA](https://slsa.dev/)**、**[in-toto](https://in-toto.io/)**、**[sigstore](https://docs.sigstore.dev/)**
  - Part 5 信任鏈三大支柱的官方文件；provenance / attestation / keyless signing 的權威來源

### 讀完本課之後

- **[cloud_container_security](../cloud_container_security/README.md)**（本 repo）— 把供應鏈安全放進更大的雲端 / K8s / CI-CD 紅隊攻防脈絡
- **[blue_team_dfir](../blue_team_dfir/README.md)**（本 repo）— SBOM 在事件應變裡的角色，出事時怎麼用它加速調查
