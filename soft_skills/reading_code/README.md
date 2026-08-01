# 讀碼即逆向：陌生大型 codebase 的系統化攻堅

> 給已經會寫程式、但面對幾十萬行陌生 source 會發怵的工程師與安全研究者。

這門課把 binary reverse engineering 的攻堅直覺（找 entry、抓 data flow、猜 invariant、定位關鍵函式）移植到讀 source。學完你有一套可複製的 SOP：拿到任何專案，能在時限內建出架構地圖、追出關鍵路徑、定位「你要改的那 200 行」，並解釋任何一段硬核機制。工具與方法並重，全程拿真實開源專案開刀。

## 為什麼學這個？

- **讀碼是工程師花最多時間、卻最少被系統教的技能**：你寫的每一行 code 之前，都得先讀懂周圍幾千行。讀得快，你就比別人快。
- **這是一種逆向工程**：沒有文件、註解騙人、抽象層層疊疊的陌生 codebase，本質上就是待逆向的目標。攻堅它需要方法，不是硬讀。
- **職涯與研究角度**：onboarding 新專案、貢獻開源、找漏洞、接手 legacy——全都卡在「能不能快速讀懂別人的 code」這一關。

## 先修知識

- 至少一門語言能讀能寫（程度：能獨立寫出中型程式）
- C/C++ 基礎（程度：看得懂 pointer、struct、函式指標；本課實戰主戰場是 C/C++/kernel）
- 命令列基本操作（程度：會用 shell、git clone、跑 build）
- 沒有也沒關係的：binary reverse engineering 經驗（有的話 Part 1–2 會特別有共鳴，沒有也能學）

## 課程地圖

### Part 1 — 心法：讀碼為什麼難、大腦怎麼理解程式（Ch 0–4）
- [Ch 0 環境與工具鏈搭建](./00-environment-setup.md)
- [Ch 1 讀碼 vs 寫碼的不對稱](./01-reading-vs-writing.md)
- [Ch 2 讀碼是一種逆向工程](./02-reading-as-reverse-engineering.md)
- [Ch 3 程式設計師怎麼理解程式](./03-how-programmers-understand-code.md)
- [Ch 4 三種閱讀模式：掃讀 / 精讀 / 追蹤](./04-three-reading-modes.md)

### Part 2 — 攻堅 SOP：系統化拆解陌生 codebase（Ch 5–11）
- [Ch 5 第一次接觸：60 分鐘偵察](./05-first-contact-recon.md)
- [Ch 6 找 entry point 與主迴圈](./06-finding-entry-points.md)
- [Ch 7 建立架構地圖](./07-building-architecture-map.md)
- [Ch 8 順藤摸瓜：data flow 追蹤](./08-data-flow-tracing.md)
- [Ch 9 控制流與 call graph](./09-control-flow-call-graph.md)
- [Ch 10 假設驅動讀碼](./10-hypothesis-driven-reading.md)
- [Ch 11 從 50 萬行收斂到你要改的 200 行](./11-narrowing-to-change-site.md)
- [練習 A：偵察與架構地圖](./practice-a-recon-and-map.md)

### Part 3 — 工具鏈：把讀碼工程化（Ch 12–20）
- [Ch 12 grep/ripgrep 的藝術](./12-grep-ripgrep-art.md)
- [Ch 13 LSP 與語意導航](./13-lsp-semantic-navigation.md)
- [Ch 14 ctags / cscope / GNU global](./14-ctags-cscope-global.md)
- [Ch 15 tree-sitter 與結構化查詢](./15-tree-sitter-structural-query.md)
- [Ch 16 靜態分析輔助讀碼](./16-static-analysis-reading.md)
- [Ch 17 git 當考古工具](./17-git-as-archaeology.md)
- [Ch 18 debugger-driven reading](./18-debugger-driven-reading.md)
- [Ch 19 tracing 讀執行](./19-tracing-execution.md)
- [Ch 20 AI 輔助讀碼](./20-ai-assisted-reading.md)
- [練習 B：追一個功能的完整路徑](./practice-b-trace-a-feature.md)

### Part 4 — 讀懂特定結構（Ch 21–28）
- [Ch 21 讀懂 build system](./21-reading-build-systems.md)
- [Ch 22 讀懂巨集與 metaprogramming](./22-reading-macros-metaprogramming.md)
- [Ch 23 讀懂 indirection](./23-reading-indirection.md)
- [Ch 24 讀懂狀態機與事件驅動](./24-reading-state-machines-events.md)
- [Ch 25 讀懂並發程式](./25-reading-concurrency.md)
- [Ch 26 讀懂 C++ 的複雜性](./26-reading-cpp-complexity.md)
- [Ch 27 讀懂 kernel/系統程式慣例](./27-reading-kernel-idioms.md)
- [Ch 28 source ↔ disassembly 對照](./28-source-vs-disassembly.md)
- [練習 C：讀懂一段硬核 code](./practice-c-read-hardcore-code.md)

### Part 5 — 高階策略（Ch 29–36）
- [Ch 29 讀你不會的語言](./29-reading-unknown-languages.md)
- [Ch 30 讀爛 code / 義大利麵](./30-reading-bad-code.md)
- [Ch 31 大型專案的分而治之](./31-divide-and-conquer-large-codebases.md)
- [Ch 32 找漏洞式讀碼](./32-vulnerability-hunting-reading.md)
- [Ch 33 code review 式讀碼](./33-code-review-reading.md)
- [Ch 34 為了移植/重寫而讀](./34-reading-to-port-rewrite.md)
- [Ch 35 外化理解：筆記、圖、心智模型](./35-externalizing-understanding.md)
- [Ch 36 費曼測試](./36-feynman-test.md)
- [練習 D：找漏洞式讀碼（CVE hunt）](./practice-d-cve-hunt.md)

### Part 6 — 整合（Ch 37–39）
- [Ch 37 常見誤區與反模式](./37-anti-patterns.md)
- [Ch 38 打造你自己的讀碼 SOP](./38-your-reading-sop.md)
- [Ch 39 案例研究：完整攻堅實況](./39-case-study-full-attack.md)
- [Final Project：冷啟動攻堅一個真實 codebase](./final-project-cold-codebase-attack.md)

## 學習方式建議

1. **讀完一章就開一個真實 repo 動手**：這門課的技巧不 clone 專案來練等於沒學。每章都有指定或建議的實戰目標。
2. **計時**：讀碼是速度技能。偵察給自己 60 分鐘、追一條路徑給自己 30 分鐘，逼出策略。
3. **外化**：邊讀邊畫圖、記假設、寫下「我猜這裡是……」然後驗證。腦中讀不算讀。
4. **故意讀難的**：挑你完全沒背景的語言和領域練，痛苦的地方才是技巧生效的地方。

## 精選資料庫

這裡列整門課最值得反覆參照的資源；每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《The Programmer's Brain》** — Felienne Hermans（Manning, 2021）
  - 認知科學角度講「大腦怎麼讀 code」，chunking / working memory / beacon 的實證基礎；Part 1 的主要參考。
- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - 少數專門講「讀碼」的書，拿真實開源 code 示範讀法；年代久但方法論不過時。

### 推薦部落格 / 文章

- **[John Ousterhout, "A Philosophy of Software Design"](https://web.stanford.edu/~ouster/cgi-bin/aposd.php)**
  - 反向理解「好 code 長怎樣」能幫你更快判斷「這段 code 想幹嘛」。

### 讀完本課之後

- 把方法論套到你自己領域最硬的 codebase（Linux kernel 某子系統、V8、LLVM），限時攻堅一次就出師。
