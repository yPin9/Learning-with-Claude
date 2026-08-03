# 原始碼審計學習筆記：從手讀找洞到規模化變體獵殺

> 給已經能讀懂陌生大型 source、想把「找漏洞式讀碼」工業化的安全研究者。

這系列接續 [`soft_skills/reading_code`](../../soft_skills/reading_code/README.md)：那門課教你用眼睛和腦袋讀一條路徑，這門課教你用 **query engine 一次掃幾百萬行**，把一個 bug 抽成 pattern，找出整個 repo 甚至整個生態的所有變體（variant analysis）。四把刀並用——**CodeQL、Semgrep、Joern、weggli**——涵蓋 native（C/C++/kernel 記憶體安全）與 web（Java/JS/Python 反序列化、injection、SSRF），最後把靜態命中收斂到 PoC 驗證與報告。

## 為什麼學這個？

- **產能**：手讀一天追幾條路徑；一條寫好的 query 掃完一個生態、找出上百個變體。現代 vuln research 真正的槓桿在這裡。
- **接得上你既有的底子**：你已經有 dataflow/SSA（`ssa_optimizations`）、taint/symbolic（`symex_taint`）、SMT（`sat_smt`）。這門課把這些理論落到「怎麼在真實 codebase 上規模化找洞」。
- **雙向能力**：攻方用它掃 target 找 0/1-day；守方用它寫 CI gate、擋整類 CWE。同一套技能兩邊都吃。

## 先修知識

- C/C++ 讀寫（程度：能讀 redis/curl 等級的真實 source；有 UAF/OOB 概念）
- 基本 dataflow / taint 直覺（程度：知道 source→sink 是什麼；沒有也行，Part 1 會重建）
- 命令列與 git（程度：能 clone、checkout patch、跑 build）
- 沒有也沒關係的：Scala（Joern 用，會邊學）、QL（CodeQL 用，Part 4 從零教）

## 課程地圖

### Part 0 — 為什麼要原始碼審計（Ch 0–2）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 讀碼即逆向 → 審計即規模化](./01-reading-to-auditing.md)
- [Ch 2 靜態分析全景：sound、complete 與四工具地圖](./02-static-analysis-landscape.md)

### Part 1 — 靜態分析理論地基（Ch 3–8）
- [Ch 3 程式表示：從 AST/CFG/SSA/PDG 到 CPG](./03-program-representations-cpg.md)
- [Ch 4 資料流分析：lattice、transfer function、fixpoint](./04-dataflow-analysis.md)
- [Ch 5 IFDS/IDE：把 taint 化成圖可達性](./05-ifds-ide.md)
- [Ch 6 指標分析：Andersen、Steensgaard 與精度](./06-points-to-analysis.md)
- [Ch 7 Taint 分析原理：source/sink/sanitizer](./07-taint-analysis-theory.md)
- [Ch 8 理論怎麼落到工具：近似與取捨](./08-theory-to-tools.md)
- [練習 A：手刻 mini taint tracker](./practice-a-mini-taint-tracker.md)

### Part 2 — 從讀碼到審計：攻擊面建模（Ch 9–12）
- [Ch 9 source/sink/sanitizer 思維](./09-source-sink-sanitizer.md)
- [Ch 10 攻擊面建模與 target 選擇](./10-attack-surface-modeling.md)
- [Ch 11 跨語言 sink 目錄](./11-cross-language-sink-catalog.md)
- [Ch 12 誤報三角與可信度分級](./12-false-positive-triage.md)
- [練習 B：攻擊面地圖 + sink 清單](./practice-b-attack-surface-map.md)

### Part 3 — Semgrep：快篩與規則工程（Ch 13–17）
- [Ch 13 Semgrep 語法模式](./13-semgrep-syntactic-patterns.md)
- [Ch 14 Semgrep taint mode](./14-semgrep-taint-mode.md)
- [Ch 15 Semgrep 規則工程](./15-semgrep-rule-engineering.md)
- [Ch 16 跨語言 Semgrep](./16-semgrep-cross-language.md)
- [Ch 17 Semgrep 進 CI](./17-semgrep-ci.md)
- [練習 C：Semgrep taint 規則抓 CVE](./practice-c-semgrep-taint-rules.md)

### Part 4 — CodeQL：變體獵殺主力（Ch 18–28）
- [Ch 18 CodeQL 模型：extractor/database/QL](./18-codeql-model.md)
- [Ch 19 QL 語言核心](./19-ql-language-core.md)
- [Ch 20 建 database：多語言抽取](./20-codeql-databases.md)
- [Ch 21 CodeQL local dataflow](./21-codeql-local-dataflow.md)
- [Ch 22 CodeQL global taint tracking](./22-codeql-global-taint.md)
- [Ch 23 flow state 與 models-as-data](./23-codeql-flow-state-models.md)
- [Ch 24 C/C++ 記憶體安全 query](./24-codeql-cpp-memory-safety.md)
- [Ch 25 Java/JS/Python query](./25-codeql-web-languages.md)
- [Ch 26 從 CVE 到 query](./26-codeql-cve-to-query.md)
- [Ch 27 MRVA 多倉庫變體分析](./27-codeql-mrva.md)
- [Ch 28 query 效能與除錯](./28-codeql-query-performance.md)
- [練習 D：CodeQL variant analysis](./practice-d-codeql-variant-analysis.md)

### Part 5 — Joern / CPG：語意程式搜尋（Ch 29–32）
- [Ch 29 Joern 上手](./29-joern-getting-started.md)
- [Ch 30 Joern 語意查詢](./30-joern-semantic-queries.md)
- [Ch 31 Joern 自訂 pass](./31-joern-custom-passes.md)
- [Ch 32 Joern vs CodeQL](./32-joern-vs-codeql.md)
- [練習 E：Joern 無 build dataflow 查詢](./practice-e-joern-no-build.md)

### Part 6 — 輕量武器：weggli 與結構搜尋（Ch 33–35）
- [Ch 33 weggli：C/C++ 半結構 pattern](./33-weggli.md)
- [Ch 34 結構搜尋家族：comby/ast-grep](./34-structural-search-family.md)
- [Ch 35 組合拳漏斗](./35-funnel-combining-tools.md)

### Part 7 — 規模化與整合（Ch 36–40）
- [Ch 36 誤報治理](./36-false-positive-governance.md)
- [Ch 37 靜態 + 動態驗證](./37-static-plus-dynamic.md)
- [Ch 38 diff-based 審計](./38-diff-based-auditing.md)
- [Ch 39 SARIF 與生態整合](./39-sarif-ecosystem.md)
- [Ch 40 AI 輔助審計](./40-ai-assisted-auditing.md)
- [練習 F：diff-gate pipeline](./practice-f-diff-gate-pipeline.md)

### Part 8 — 整合與方法論（Ch 41–43）
- [Ch 41 審計反模式](./41-auditing-antipatterns.md)
- [Ch 42 打造你的 audit SOP](./42-your-audit-sop.md)
- [Ch 43 案例實況：完整 variant hunt](./43-case-study-variant-hunt.md)

### Final Project
- [Variant Analysis Campaign](./final-project-variant-analysis-campaign.md)

## 學習方式建議

1. **每章真的跑 query**：CodeQL/Semgrep/weggli 都在 WSL 裝好，看一個 pattern 命中真實 code 才算學會。
2. **從 patch 反推**：挑一個 CVE，先看它的 fix commit，再想「怎麼寫一條 query 抓出所有沒被 fix 的變體」——這是全課的核心動作。
3. **誤報是常態**：工具會給你一堆命中，學會 triage 和 ranking 比學會寫 query 更難，也更值錢。
4. **對照理論**：Part 1 的 dataflow/IFDS/points-to 不是裝飾——當 query 漏報或爆炸時，你要能回到理論說出為什麼。

## 精選資料庫

這裡列整門課最值得反覆參照的資源，每章的「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[CodeQL 官方文件](https://codeql.github.com/docs/)**
  - 全課 CodeQL 部分的權威來源；「QL language reference」與「CodeQL library for C/C++/Java」是主要參照
- **[Semgrep 官方文件](https://semgrep.dev/docs/)**
  - 規則語法與 taint mode 的權威來源；配 [Semgrep Playground](https://semgrep.dev/playground/) 即時試

### 推薦論文

- **[Modeling and Discovering Vulnerabilities with Code Property Graphs](https://ieeexplore.ieee.org/document/6956589)** — Yamaguchi et al., IEEE S&P 2014
  - CPG 的原始論文，Joern 的理論基礎；解釋為什麼把 AST+CFG+PDG 合一能一次表達語法與語意漏洞

### 推薦部落格 / 文章

- **[GitHub Security Lab research](https://securitylab.github.com/research/)**
  - CodeQL 團隊親自示範 variant analysis 找真實 CVE，全課最好的實戰範本
- **[Trail of Bits blog](https://blog.trailofbits.com/)**（搜 CodeQL / Semgrep / weggli tag）
  - 頂級 audit 團隊的實戰經驗，很多自訂 query 與方法論

### 讀完本課之後

- **[The Art of Software Security Assessment](https://www.amazon.com/dp/0321444426)** — Dowd, McDonald, Schuh
  - 手動 code audit 的聖經；本課教你把它的直覺自動化，這本補回工具抓不到的深度
- **接續**：把命中丟給 [`security/advanced_fuzzing`](../advanced_fuzzing/README.md) 與 [`security/symex_taint`](../symex_taint/README.md) 做動態驗證，形成靜態+動態閉環
