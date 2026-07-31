# Ch 40 — 讀 Project Zero / 廠商 writeup 的地圖與下一步

> **目標**：交給你一個貫穿整個漏洞研究生涯的元技能——**怎麼有系統地讀一篇真實 V8 0-day writeup**，而不是讀三行就迷路。你會拿到一套「讀 writeup 的固定拆解框架」（把任何一篇對應到本課章節）、一張「追蹤 V8 安全生態」的雷達（bug tracker、v8-dev、Pwn2Own、CTF），以及一份誠實的「學完本課的下一步」路線圖。這是 Part 7 的最後一課，也是把你從「學生」推向「能自主追蹤前沿的研究者」的那一腳。

> **本章為方法論章**：不釘 V8 版本、不逐行實作。示範用的 CVE 皆為**公開、有 patch、有 writeup** 的真實案例；具體 commit / 版本請以 chromium.googlesource.com 上的公開紀錄為準（本章給查法，不捏造本地 `git log` 輸出）。

## 為什麼需要這個？

你學完了前 39 章。V8 的物件模型、TurboFan type confusion、addrof/fakeobj、任意讀寫、sandbox、CTF 套路、full chain 全景——地基打完了。但漏洞研究是一個**永遠在移動的靶**：V8 每兩週一個版本，mitigation 每年翻新，新的 bug 類別不斷冒出。你不可能靠「上完一門課」就永遠跟得上。

真正讓你**持續**跟得上的，是一個元技能：**讀懂前沿研究者寫的 writeup**。Project Zero、Pwn2Own 團隊、doar-e、saelo 每發一篇 V8 0-day 分析，就是把「當前最前沿的一個 bug 從發現到利用」攤開給你看。這門課從 [Ch 1](./01-why-renderer-attack-surface.md) 就說了：「這門課的終極讀者能力，就是讀懂 P0 的文章。」現在你有能力了，但**讀 writeup 本身是一門有方法的手藝**——沒方法，你會被術語和細節淹死；有方法，你能把任何一篇拆成你熟悉的積木。這章就教這套方法，並告訴你畢業後往哪走。

## 先建立直覺：每篇 V8 writeup 都是同一副骨架

再前沿、再嚇人的 V8 0-day writeup，骨架都一樣——因為 V8 exploit 的**流程是固定的**（這正是本課 Part 3 那條流水線）。你要做的不是「從頭理解」，而是**把文章的每一段對號入座到這副骨架**：

```
   一篇 V8 0-day writeup 的固定骨架          ←→   本課對應章節
   ┌────────────────────────────────┐
   │ 1. Root cause（bug 出在哪）       │  ←→  Part 2（管線）+ Part 4（漏洞類別）
   │    哪個 optimization / builtin    │
   │    做了什麼錯誤假設               │
   ├────────────────────────────────┤
   │ 2. Trigger / PoC（怎麼觸發）      │  ←→  Ch 12 speculation/deopt、Part 4
   │    一段讓 bug 發生的 JS          │
   ├────────────────────────────────┤
   │ 3. Primitive（拿到什麼初始能力）  │  ←→  Ch 14 OOB / Ch 30 triage
   │    OOB read/write? type confusion?│
   ├────────────────────────────────┤
   │ 4. addrof/fakeobj（兩把鑰匙）     │  ←→  Ch 15
   ├────────────────────────────────┤
   │ 5. 任意讀寫（read64/write64）     │  ←→  Ch 16–18
   ├────────────────────────────────┤
   │ 6. Code exec（含繞 mitigation）  │  ←→  Ch 32–36（sandbox / CFI）
   ├────────────────────────────────┤
   │ 7.（真實）sandbox escape / LPE    │  ←→  Ch 39 + kernel_pwn
   └────────────────────────────────┘
```

**這副骨架就是你的閱讀地圖**。任何一篇 writeup，你先問「這段在講骨架的第幾格」，再用對應章節的知識去讀那一段。你會驚訝地發現：一篇看起來高不可攀的 P0 文章，90% 是你已經學過的積木，真正「新」的往往只有第 1 格（一個你沒見過的 root cause）和第 6/7 格（一個新的 mitigation 繞法）。

## 讀 writeup 的固定拆解框架（七步）

拿到一篇 V8 0-day writeup，照這七步拆，不要從頭讀到尾：

1. **先定版本與 mitigation 背景**。這篇打的是哪個 V8 版本？當時 sandbox 上了沒、pointer compression 上了沒？這決定它的收尾（第 6 格）用的是老招還是現代招（[Ch 38](./38-d8-vs-real-chrome.md) 的時間軸）。版本錯位，你會困惑「為什麼它能直接控 backing store 指標」——因為那是 sandbox 前的世界。
2. **抓 root cause（第 1 格）**。找文章裡「哪個 optimization phase / builtin 做了什麼錯誤假設」。這是全文最有價值、最該慢讀的一段。用 Part 4 的分類問：這是 CheckBounds 消除（[Ch 20](./20-checkbounds-redundancy-elimination.md)）？typer/range bug（[Ch 22](./22-typer-range-analysis-bug.md)）？side-effect（[Ch 21](./21-array-prototype-side-effect.md)/[Ch 24](./24-jit-side-effect.md)）？element-kind confusion（[Ch 23](./23-element-kind-map-confusion.md)）？把它歸類，你就抓到了這篇的靈魂。
3. **看 trigger PoC（第 2 格）**。那段觸發 JS 通常很短。對照 root cause 理解「為什麼這幾行剛好打破那個假設」。注意它怎麼控制優化時機（真實的用熱迴圈，CTF 的用 `%OptimizeFunctionOnNextCall`）。
4. **認 primitive（第 3 格）**。bug 給的初始能力是什麼？相對 OOB？full type confusion？這決定接下來難度（[Ch 30](./30-exploitability-triage.md) 的 triage 直覺）。
5. **快速掃 4–5 格（addrof/fakeobj/RW）**。這幾格幾乎每篇都一樣（就是本課 Part 3 的 template），**掃過去確認它用的是標準路子即可，不用慢讀**。這是你能省時間的地方——你已經會這段了。
6. **慢讀 code exec 與繞 mitigation（第 6 格）**。這是除了 root cause 外第二有價值的段落。它怎麼繞 sandbox（[Ch 34](./34-v8-sandbox.md)/[Ch 35](./35-bypassing-v8-sandbox.md)）？怎麼在 CET/CFI 下拿控制流（[Ch 36](./36-cfi-cet-data-only.md)）？現代 writeup 的「新東西」大半在這格。
7. **（真實 chain）掃 sandbox escape / LPE（第 7 格）**。用 [Ch 39](./39-renderer-mojo-sandbox-escape.md) 的地圖認「它打哪個 Mojo interface、經不經過 GPU」，不用懂 C++ 細節。

**這套框架的威力在於「分配注意力」**：它告訴你哪兩格（1 和 6）值得花 80% 時間慢讀、哪幾格（4-5）掃過即可。新手的問題永遠是「每一段都用同樣力氣讀」，結果在你早就會的 addrof 段耗掉精力，到真正新的 root cause 已經累了。

## 示範：把 CVE-2021-38003 對號入座

拿一個公開、有 patch、有 writeup 的真實案例走一遍框架。**CVE-2021-38003** 是 2021 年一個被 in-the-wild 利用的 V8 0-day（Google 緊急修補、Project Zero 有分析），root cause 大意是 V8 在某條路徑上**把內部 sentinel「the hole」洩漏到了 JS 可見層**，導致後續可構造出 type confusion。用七步框架拆：

- **版本/背景**：Chrome 94/95 時代，pointer compression 已上、V8 sandbox 尚未全面（決定它收尾的招式屬於「sandbox 前後過渡期」）。
- **root cause（第 1 格）**：內部 `the_hole` sentinel 洩漏——這是「內部值不該被 JS 看到卻看到了」這一類（本課 [Ch 3](./03-value-representation.md) 講過 the hole 是 V8 的內部 sentinel，[Ch 23](./23-element-kind-map-confusion.md) 的 element-kind/內部值混淆家族）。這是最該慢讀的一段。
- **trigger（第 2 格）**：一段觸發洩漏的短 JS。
- **primitive → addrof/fakeobj → RW（第 3-5 格）**：從 the-hole 洩漏構造出 type confusion，接標準 Part 3 template。這幾格掃過確認是標準路。
- **code exec（第 6 格）**：當時的收尾（對照本課 [Ch 32](./32-arbitrary-rw-to-code-exec.md)，注意它的招在今天可能已被 mitigation 擋）。

**為什麼選它示範**：它 root cause 乾淨（一句話能講清：內部 sentinel 洩漏）、公開資料完整、且**正是 Final Project 建議的範本之一**。你在這章學會把它對號入座，Final Project 就是叫你把這套「讀懂」升級成「動手復現」。

> **公開紀錄的查法（不捏造本地輸出）**：CVE-2021-38003 的修補 commit 與受影響版本，去 chromium.googlesource.com 的 V8 repo 搜 commit message、或看 Chrome release blog 的 security fix 條目、或 Google Project Zero 的 in-the-wild 0-day tracking spreadsheet。**本課的 d8 是 depth-1 淺 clone，本地 `git log` 看不到這個歷史 commit**——這正是「認識論誠實」：能查證的給你查法，不假裝我在本地跑過。

## 追蹤 V8 安全生態：你的前沿雷達

課上完，靶還在動。裝好這幾個雷達，你就能持續跟上前沿：

- **V8 / Chromium bug tracker（issues.chromium.org）**：安全 bug 修補後會逐步解禁（公開）。訂閱 Security component，看真實 bug 長怎樣、patch 怎麼寫。這是 [Ch 27](./27-patch-diffing.md) patch-diff 的活水源頭。
- **v8-dev mailing list / V8 commit log**：V8 團隊的日常。看 TurboFan/Maglev 在改什麼、新 optimization 進來（新 optimization = 新攻擊面）。commit message 常直接寫「fix OOB in X」——那就是線索。
- **Chrome release / security blog**：每個 stable release 的 security fix 列表，含 CVE 編號和「reported by」。看哪些研究者在產出、哪類 bug 還在被修。
- **Pwn2Own / TyphoonPWN 等賽事 writeup**：每年的 Chrome full chain 實戰報告，是「當前最高水準 exploit 長怎樣」的年度快照。用 [Ch 39](./39-renderer-mojo-sandbox-escape.md) + 本章框架讀。
- **CTF（\*CTF、Google CTF、hxp…）**：[Ch 37](./37-ctf-v8-challenges.md) 講的 n-day 型題，本質是「把最近的真實 CVE 做成練習」。持續打 CTF = 持續被餵最新的可練 bug。
- **研究者個人 blog**：doar-e（Jeremy Fetiveau）、saelo（Samuel Groß）、以及各安全團隊（theori、Ret2、dataflow）。追蹤幾個人，你的前沿密度自然跟上。

**建雷達的訣竅**：不要想「全部讀完」，那會累死。設定「每週讀懂一篇」的節奏，用七步框架快速拆，把新學到的 root cause 記進你的「bug 類別筆記」。一年下來你會累積出對 V8 安全史的肌肉記憶——這正是 [Ch 37](./37-ctf-v8-challenges.md) 說的「看到 M89 就能聯想那區間著名 bug」的底氣。

## 學完本課的下一步：一張誠實的路線圖

你現在站在哪、往哪走：

1. **鞏固第一節（V8 RCE）**：把 Final Project 做完（挑一個真 CVE 走完 patch-diff → PoC → exploit）。這是把「讀懂」變「做到」的關鍵一步。再刷幾題 CTF V8（[Ch 37](./37-ctf-v8-challenges.md)），把 template 打到閉眼能默寫。
2. **自主找洞**：回頭把 Part 5 的 Fuzzilli（[Ch 28](./28-fuzzilli-internals.md)/[Ch 29](./29-running-fuzzilli.md)）真的長跑一次，triage 一個真 crash。這是從「打現成洞」升級到「自己挖洞」的分水嶺，也是 CTF 之外唯一的變現路徑（bug bounty / 研究員工作）。
3. **往第二節走（sandbox escape）**：如果你要做 full chain，[Ch 39](./39-renderer-mojo-sandbox-escape.md) 的入口往下：build content_shell、讀 Mojo 文件、讀真實 escape writeup。這是另一棵技能樹，要專門投入。
4. **接第三節（kernel LPE）**：你已有 `security/kernel_pwn` 地基。把 sandbox escape 拿到的 Browser process 當作 kernel_pwn 的「使用者態進程」起點，三門課串成完整 chain。
5. **拓寬引擎（可選）**：本課專打 V8。若要打 Safari（JSC）/ Firefox（SpiderMonkey），addrof/fakeobj 的**思路完全平移**，只是內部結構不同——你有 V8 的深度後，換引擎主要是重新熟悉物件模型，不是從零。

**最重要的一步是第 1 步**：很多人學到這裡就停在「讀懂」，從不動手復現一個真 CVE。Final Project 存在就是逼你跨過這道坎——**讀一百篇 writeup 不如親手復現一個**。

## 對比：新手讀 writeup vs 有框架地讀

| 面向 | 新手（無框架） | 有本章框架 |
|---|---|---|
| 讀法 | 從頭讀到尾，每段同樣力氣 | 先對號入座七格，火力集中在第 1、6 格 |
| addrof/RW 段 | 慢讀、耗精力（其實早會了） | 掃過確認標準路，省時間 |
| 遇到沒見過的 root cause | 卡住、放棄 | 用 Part 4 分類歸類，抓住靈魂 |
| 遇到版本錯位 | 困惑「為何能控 raw pointer」 | 先定版本/mitigation 背景，不困惑 |
| 讀完的產出 | 「看過了但講不出來」 | 一句話 root cause + 記進 bug 筆記 |
| 一年後 | 還是跟不上前沿 | 累積出 V8 安全史肌肉記憶 |

## 踩雷集錦

1. **從頭讀到尾、每段同力氣**：writeup 的價值高度集中在 root cause（第 1 格）和繞 mitigation（第 6 格）。在你早會的 addrof 段慢讀是浪費精力。先對號入座，再分配注意力。
2. **不先定版本就讀**：不知道這篇是 sandbox 前還後、pointer compression 上沒上，你會對它的收尾一頭霧水。第一步永遠是定版本與 mitigation 背景（[Ch 38](./38-d8-vs-real-chrome.md) 時間軸）。
3. **只讀不記、不歸類**：讀懂一篇卻不把 root cause 記進你的 bug 類別筆記，一週後就忘光。前沿追蹤的複利來自「每篇都歸類進 Part 4 的分類、累積肌肉記憶」。
4. **想一次跟上所有來源**：bug tracker + mailing list + 所有 blog + 所有賽事，想全讀會累死然後放棄。設「每週一篇」的可持續節奏，比三分鐘熱度地訂閱十個來源有用。
5. **停在「讀懂」不動手**：讀一百篇 writeup 給你的是「認得」，不是「會做」。真正的能力來自親手復現一個真 CVE（Final Project）。跳過動手，你的技能是紙上的。
6. **以為換引擎要從零學**：JSC/SpiderMonkey 的 addrof/fakeobj 思路和 V8 完全同源。你有 V8 深度後換引擎主要是重新熟悉物件模型，別被「另一個引擎」嚇到而不敢碰。

## 進階：再往深一層

- **建自己的「bug 類別 × CVE」對照表**：每讀懂一篇，記一列：CVE 編號、root cause 一句話、屬 Part 4 哪類、收尾用什麼 mitigation 繞法、版本。累積三十列，你就有了一張私人的 V8 安全史地圖——這比任何教材都貼近前沿，且是你面試/研究的獨門資產。
- **從 patch 反推 writeup**：進階研究者常常在 writeup 出現**之前**就從 patch（bug tracker 解禁的 commit）自己推出 root cause 和 PoC（[Ch 27](./27-patch-diffing.md) 的技能）。這是 1-day 開發，也是把「讀懂別人的」升級成「自己重建」的能力。Final Project 就是這個能力的第一次實戰。
- **關注新 optimization 就是關注新攻擊面**：Maglev 是 2023 才成熟的中間層，它一出現就帶來一批新 type confusion（和 TurboFan 同源的病，[Ch 2](./02-v8-architecture.md)）。每當 V8 加一個新 optimization phase / 新 builtin，那就是下一批 bug 的產地。追 v8-dev 的動機就在這。
- **變現路徑的現實**：V8 pwn 技能的真實出路——bug bounty（Chrome VRP 對 V8 RCE 出價很高）、Pwn2Own、資安研究員職位、以及 [Ch 1](./01-why-renderer-attack-surface.md) 提的 Node/Electron/Workers 這些「沒沙盒、V8 RCE 破壞力更大」的宿主。你的技能不只用在 CTF。

## 動手練習

1. **七步拆一篇**：找一篇 Project Zero 的 V8 exploit 分析，嚴格按本章七步拆解，寫下每一格對應本課哪一章、哪一格是「新東西」。目標：用一句話講出這篇的 root cause。
2. **建雷達**：訂閱 issues.chromium.org 的 Security component、把 doar-e/saelo/一個安全團隊 blog 加進 RSS、把 Chrome security release note 加書籤。設定「每週讀懂一篇、記一列對照表」的節奏。
3. **CVE-2021-38003 溯源**：去 chromium.googlesource.com 和 P0 的 in-the-wild spreadsheet 查 CVE-2021-38003 的修補 commit、受影響版本、原始 writeup。把它按七步框架拆一遍——這正是 Final Project 的前置閱讀。
4. **畫你的下一步**：不看本章，寫下你學完這門課後接下來三個月的具體計畫（Final Project → 刷幾題 CTF → 長跑 Fuzzilli → 要不要往 sandbox escape）。把它和本章「下一步路線圖」對照，補上你漏掉的環節。

## 本章重點整理

- **每篇 V8 writeup 都是同一副七格骨架**（root cause → trigger → primitive → addrof/fakeobj → RW → code exec →（真實）escape/LPE），每格對應本課明確章節。讀 = 對號入座，不是從頭理解。
- **七步拆解框架的核心是分配注意力**：火力集中在 root cause（第 1 格）和繞 mitigation（第 6 格），addrof/RW 段掃過即可（你早會了）。第一步永遠先定版本與 mitigation 背景。
- **前沿雷達**：bug tracker、v8-dev、Chrome security release、Pwn2Own、CTF、研究者 blog。訣竅是「每週一篇 + 歸類進 bug 筆記」的可持續節奏，累積 V8 安全史肌肉記憶。
- **下一步路線圖**：鞏固第一節（Final Project + CTF）→ 自主找洞（Fuzzilli）→（可選）往 sandbox escape → 接 kernel LPE 完成 chain →（可選）拓寬引擎。**最關鍵是動手復現一個真 CVE**，別停在「讀懂」。

## 自我檢核

- [ ] 能說出 V8 writeup 的七格骨架，並把每格對應到本課章節
- [ ] 拿到一篇陌生 writeup，能先定版本/mitigation 背景，再把注意力集中到 root cause 與繞 mitigation 兩格
- [ ] 能用本課 Part 4 的分類，把一個沒見過的 root cause 歸到某個 bug 家族
- [ ] 列得出至少四個追蹤 V8 安全前沿的來源，並有可持續的閱讀節奏
- [ ] 能講清楚自己學完本課後的下一步路線，以及為什麼「動手復現真 CVE」比「多讀 writeup」重要
- [ ] 面試被問「你怎麼跟上瀏覽器安全前沿」，能講出雷達 + 七步框架 + bug 筆記這套方法

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

- **[Project Zero blog（V8 exploit / in-the-wild 分析）— googleprojectzero.blogspot.com](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：最高品質的真實 V8 0-day 分析，含 root cause + 完整 exploit。
  - **讀哪裡**：任挑一篇 V8 的，用本章七步框架拆（動手練習 1）。這是你這門課培養的終極閱讀能力的主要用武之地。

- **[Google Project Zero「0day In the Wild」追蹤表 — googleprojectzero.github.io/0days-in-the-wild](https://googleprojectzero.github.io/0days-in-the-wild/)**
  - **這篇說什麼**：被真實利用的 0-day 清單，含 CVE、廠商、root cause 分類、有無公開 exploit。
  - **和本章的關聯**：CVE-2021-38003 溯源（動手練習 3）的一手來源，也是你「bug 類別對照表」的起點資料庫。

- **[Chromium issue tracker（Security component）— issues.chromium.org](https://issues.chromium.org/)**
  - **這篇說什麼**：真實安全 bug 的 report + patch，解禁後可見。
  - **為什麼值得讀**：patch-diff（[Ch 27](./27-patch-diffing.md)）的活水源頭，也是「從 patch 反推 writeup」進階技能的練兵場。

- **[Zero Day Initiative blog（Pwn2Own writeup）— zerodayinitiative.com/blog](https://www.zerodayinitiative.com/blog)** 與 **[doar-e / saelo 個人 blog](https://doar-e.github.io/)**
  - **這篇說什麼**：年度最高水準的 Chrome full chain 實戰，以及研究者第一手的 V8 利用細節。
  - **和本章的關聯**：建前沿雷達（動手練習 2）的核心追蹤對象；用七步框架 + [Ch 39](./39-renderer-mojo-sandbox-escape.md) 讀。

你手上現在有：V8 RCE 的完整功力、full chain 的全景地圖、讀懂前沿 writeup 的框架、持續追蹤的雷達。最後只剩一件事——**動手把一個真實 CVE 從 patch 走到完整 exploit**。Final Project 就是這一步，它會逼你把這門課 70% 以上的核心概念一次串起來，證明你不只是「讀懂了」，而是「做得到」。

→ [Final Project — 從真實 V8 CVE 到完整 exploit](./final-project-cve-to-exploit.md)
