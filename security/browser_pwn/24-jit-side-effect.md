# Ch 24 — JIT side-effect 系列（"the return of the JIT"）

> **目標**：把前五章的 type confusion 家族拉高一層統合——理解它們共通的根：**優化器對「effect（副作用）」的建模與現實有落差**。看清為什麼這一整類 bug **殺不完**、hardening 與攻擊怎麼軍備競賽、以及新的 JIT 層（Maglev、Turboshaft）為什麼是「同一種病、新的宿主」。這章讓你從「認得幾個 CVE」升級到「看到一個新 JIT 就知道去哪找 bug」。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`。本章偏機制統合與方法論，具體歷史 bug 為理論分析；effect chain 可用 `--trace-turbo` 觀察。

## 為什麼需要這個？

如果你只把 Ch 19–23 當成「五個獨立的漏洞技巧」背下來，那你學到的是**過去**——那些 bug 都修了。真正值錢的是看穿它們的**共同結構**，這樣當 V8 明年推出新的優化、新的 JIT 層時，你知道**同一種病會在哪裡復發**。這一章給你那副眼鏡。這也是為什麼這類研究常被戲稱 "the return of the JIT"——同樣的病，換個地方，一次又一次回來。

## 先建立直覺：一條所有人都要遵守的時間線

TurboFan 的 IR 裡有一條 **effect chain（副作用鏈）**——把所有「會改變世界狀態」的操作串成一條時間線，強制它們照順序發生。value edge 說「誰用誰的值」，effect edge 說「誰在誰之後改變了世界」。

```
   effect chain（時間線）：
   [載入 elements 指標] ──eff──► [呼叫某函式] ──eff──► [用 elements 指標寫入]
        ↑ 讀了狀態                 ↑ 可能改變狀態          ↑ 用先前讀的狀態
        └─────────── 這中間狀態變了嗎？由 effect chain 決定 ───────────┘
```

**所有 type confusion 的根，都是這條時間線建錯了**：某個「其實會改變世界」的操作，在 effect chain 上被標成「不改變世界」（或被移到錯的位置），於是優化器以為「我先前讀的狀態還有效」，用了過時的東西。

- [Ch 19](./19-turbofan-type-confusion.md)（副作用模型錯）：`JSCreateObject` 被標成不改變世界，其實它改了 property 表示。
- [Ch 21](./21-array-prototype-side-effect.md)（callback）：callback 是最強的「改變世界」，但被漏防。
- [Ch 20](./20-checkbounds-redundancy-elimination.md)（BCE）/[Ch 22](./22-typer-range-analysis-bug.md)（typer）：不是 effect 建錯，而是**type/range 這個「靜態事實」**被算錯——但效果一樣：優化器信任了一個錯的前提。

**一句話收攏**：type confusion = **優化器信任了一個錯誤的前提**。前提有兩種：「這個狀態不會變」（effect）和「這個值的型別/範圍是這樣」（type）。這五章就是這兩種前提被騙的各種方式。

## 底層機制：effect 建模為什麼這麼難

為什麼 V8 團隊這麼多聰明人還是一直漏？因為 effect 建模是個**組合爆炸**問題：

1. **每個 IR 操作都要正確宣告它的副作用**（`kNoWrite`、`kNoThrow`、`kEliminatable`…）。V8 有數百個操作，漏標一個就是一個 bug。
2. **副作用是路徑相依的**：一個操作在多數情況無害，但某條罕見路徑（deprecated map、特殊 elements kind、Proxy）會有副作用。要涵蓋所有路徑極難。
3. **優化 pass 之間會交互**：typer、redundancy elimination、escape analysis、load elimination 各自對 effect/type 做假設，一個 pass 的小失誤被另一個 pass 放大。
4. **語言規範充滿隱式回呼**（[Ch 21](./21-array-prototype-side-effect.md)）：`valueOf`、species、getter——每個都是 effect chain 上必須正確建模的「使用者碼注入點」。

這是一場注定有漏洞的仗——**只要 JIT 為了快而基於前提省略檢查，前提就有被騙的空間**。這是 JIT 的原罪，不是 V8 特別爛。

## 軍備競賽：hardening 怎麼把攻擊逼進角落

V8 團隊當然不是坐以待斃。防禦演進（呼應 [Ch 1](./01-why-renderer-attack-surface.md) 的「防禦把攻擊逼進下一個角落」）：

- **更嚴的 effect 建模與 fuzzing**：Fuzzilli（[Ch 28](./28-fuzzilli-internals.md)）大量針對 JIT 一致性做差分測試，早期就抓掉很多。
- **Bounds check hardening**：即使 CheckBounds 被錯消，某些 OOB store 仍被額外 runtime check 擋。
- **V8 Sandbox（[Ch 34](./34-v8-sandbox.md)）**：這是關鍵轉折。它**不試圖阻止 type confusion**（承認擋不完），而是限制「拿到堆內任意讀寫之後能造成的傷害」——把 backing store 指標等關進 cage、用 external pointer table 間接化。於是 Ch 15–18 那套「拿到 RW 就直接改指標打穿」的經典路被斬斷，攻擊者被逼去做 data-only、或攻擊 sandbox 本身。

結果：**一個 type confusion 不再等於贏**。現代 V8 exploit 常常需要「type confusion + sandbox 內的進一步利用 + code exec 手法」好幾層。這是 [Ch 32](./32-arbitrary-rw-to-code-exec.md)、[Ch 36](./36-cfi-cet-data-only.md) 的主題。

## 同一種病，新的宿主：Maglev 與 Turboshaft

V8 為了效能不斷加新的編譯層，而**每一層新的優化器都重演一次 effect/type 建模的挑戰**：

- **Maglev**（[Ch 2](./02-v8-architecture.md)）：中階 JIT，2023 年上線。它有自己的 IR、自己的 effect/type 假設——於是也有自己的 type confusion bug，且因為較新、被審視得較少，是近年的新礦。
- **Turboshaft**：TurboFan 的新後端 IR，V8 正逐步遷移。IR 換了，effect 建模的實作也換了，舊 bug 可能以新形式重現，或新引入。你在現行 15.3 的 regression test 就能看到 `Turboshaft` 相關的修補（例如 LoopUnrollingReducer 的 miscompile）。

**方法論結論**：看到 V8 推出任何新的優化層/新 IR，第一件事就是問「它怎麼建模 effect？怎麼推 type？哪些隱式回呼它可能漏防？」——bug 就在那裡等著。這副眼鏡比背 CVE 值錢一百倍。

## 對比：兩種「錯誤前提」

| 錯誤前提 | 被騙的東西 | 家族 | 修補方式 |
|---|---|---|---|
| 「這個狀態不會變」 | effect chain / 副作用建模 | Ch 19、21、23 | 補建模 / callback 後重載入 |
| 「這個值的型別/範圍是這樣」 | typer / range analysis | Ch 20、22 | 修 typing rule / 保守化範圍 |

所有 JIT type confusion 都落在這兩格之一（或兩者交互）。下次讀任何 V8 exploit writeup，先把它歸到這張表——你會發現再花俏的 bug 都是這兩種前提被騙。

## 踩雷集錦

1. **把每個 CVE 當獨立技巧背**：那是最沒效率的學法，且學到的都過期了。要抽出「哪個前提被騙」，才有遷移力。
2. **以為 hardening 讓 JIT bug 絕跡**：沒有。hardening 讓「一個 bug 直接贏」變難，但 bug 本身照樣被 fuzzing 挖出。改變的是「bug 之後要多做幾步」，不是「沒有 bug」。
3. **忽略新 JIT 層**：只盯 TurboFan 的人會錯過 Maglev/Turboshaft 這些新礦。新優化器 = 新的 effect/type 建模 = 新 bug。
4. **以為 effect 建模錯是「粗心」**：它是**組合爆炸**下的必然。理解這點你才會去對的地方找（罕見路徑、隱式回呼、pass 交互），而不是期待「找到那個手滑的人」。
5. **把 V8 Sandbox 當成「修了 type confusion」**：sandbox 不修 bug，它**限制傷害**。type confusion 照樣能觸發，只是拿到的 RW 被關在 cage 裡。搞混這點會誤判現代 exploit 的難度來源。

## 進階：再往深一層

- **差分測試找 JIT bug**：讓同一段 JS 在「有優化」和「無優化」下跑，比對結果是否一致——不一致就是 miscompile（可能是 type confusion）。Fuzzilli 的核心思想之一，[Ch 29](./29-running-fuzzilli.md) 實作。
- **escape analysis / load elimination 的交互**：這些進階 pass 對「物件有沒有逃逸、載入能不能省」做假設，是 effect 建模最容易出錯的交界。想深挖 JIT bug，這裡是富礦。
- **讀 regression test 反推歷史 bug**：`test/mjsunit/regress/` 裡每個 `regress-*.js` 背後都是一個修過的 bug（[Ch 31](./31-oss-fuzz-regression.md)）。挑 compiler 相關的讀，是理解「effect/type 建模實際怎麼出錯」的最快路。
- **Turboshaft 遷移的風險窗**：大型 IR 遷移期間，新舊語意的縫隙是 bug 高發區。追 V8 的 Turboshaft 相關 commit 能看到第一手。

## 動手練習

1. 把 Ch 19–23 的五個 bug，各用一句話填進「哪個前提被騙（狀態不變 / 型別範圍）+ 怎麼被騙」。做完這張表，你就從「記憶」升級到「理解」。
2. 在現行 d8 找一個 `test/mjsunit/regress/` 裡 compiler 相關的 regression test（`ls ~/v8build/v8/test/mjsunit/regress/ | grep -i turbo` 之類），讀它、跑它（已修，會 pass），試著從測試碼反推「當年錯的前提是什麼」。
3. 思考題（面試）：為什麼「新增一個更快的 JIT 層」在安全上是一把雙刃劍？用 effect/type 建模的角度回答。

## 本章重點整理

- 所有 JIT type confusion 的共同根：**優化器信任了一個錯誤的前提**——「狀態不會變」（effect 建模）或「型別/範圍是這樣」（typer）。
- effect 建模是**組合爆炸**問題（數百操作 × 路徑相依 × pass 交互 × 隱式回呼），注定有漏——這是 JIT 的原罪，不是 V8 特別爛。
- hardening（fuzzing、bounds hardening、**V8 Sandbox**）不消滅 bug，而是**限制傷害**：一個 type confusion 不再等於贏。
- **Maglev、Turboshaft** 是「同一種病、新宿主」——新優化層重演 effect/type 建模挑戰，是新礦。
- 方法論：看到新 JIT/新 IR，問「它怎麼建 effect、怎麼推 type、漏防哪些回呼」——比背 CVE 值錢。

## 自我檢核

- [ ] 能把 Ch 19–23 五個家族歸進「兩種錯誤前提」的框架
- [ ] 能解釋為什麼 effect 建模「注定」會有漏（組合爆炸，非粗心）
- [ ] 能說出 V8 Sandbox 對「type confusion 等於贏」這件事改變了什麼
- [ ] 知道為什麼 Maglev/Turboshaft 是新 bug 的高發區
- [ ] 面試被問「JIT 漏洞為什麼層出不窮」，能用「優化=基於前提省檢查，前提就有被騙空間」回答

## 延伸閱讀

- **[“Exploiting Logic Bugs in JavaScript JIT Engines” — saelo, Phrack 70:9](https://phrack.org/issues/70/9)**
  - **這篇說什麼**：JIT 邏輯 bug 的方法論總綱，本章的「兩種錯誤前提」框架與它一脈相承。
  - **讀哪裡**：分類與 effect 建模的討論。
  - **和本章的關聯**：本章是它的統合視角 + 現代（sandbox、Maglev）更新。

- **[“Maglev — V8's Fastest Optimizing JIT” — v8.dev/blog/maglev](https://v8.dev/blog/maglev)**
  - **這篇說什麼**：Maglev 的設計動機與架構，理解這個「新宿主」長什麼樣。
  - **和本章的關聯**：本章說「新 JIT 層 = 新礦」，這是那個新 JIT 層的第一手介紹。

- **[V8 Turboshaft 設計文件 / 相關 v8.dev 貼文](https://v8.dev/blog)**
  - **這篇說什麼**：TurboFan 新後端 IR 的動機與遷移。
  - **讀哪裡**：架構概述；理解「IR 遷移期的風險窗」。
  - **前提**：先懂 [Ch 10](./10-turbofan-overview.md) 的 sea-of-nodes。

- **[Project Zero — 各年度 V8 JIT bug 回顧文（googleprojectzero.blogspot.com）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實 in-the-wild JIT 0-day 的長期趨勢與 hardening 效果評估。
  - **為什麼值得讀**：印證本章「軍備競賽」的實況——防禦怎麼把攻擊逼往 data-only / sandbox escape。

JIT 這條線（Ch 19–24）走完。下一章離開 JIT，看另一塊攻擊面：不經優化器、直接在 **Torque/CSA 內建**（RegExp、JSON…）裡的 bug——以 CVE-2021-38003（`JSON.stringify` 洩漏 the-hole，真實 in-the-wild）為例。

→ [Ch 25 — RegExp / JSON / 內建物件的洞](./25-regexp-json-builtins.md)
