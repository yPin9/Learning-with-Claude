# Ch 25 — RegExp / JSON / 內建物件的洞

> **目標**：離開 JIT，看另一塊獨立的攻擊面——**Torque/CSA 寫的內建函式**（`JSON`、`RegExp`、`Array` 內建…）本身的實作 bug。以 **CVE-2021-38003**（`JSON.stringify` 洩漏內部值 `the_hole`，真實 in-the-wild、列入 CISA KEV）為主線，理解「內部值洩漏到 JS 可見層」為什麼是嚴重的記憶體破壞。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`。CVE-2021-38003 在 15.3 已修，觸發碼為理論分析；`the_hole` 不該被 JS 觀察到這件事可在現行 d8 側面驗證。

## 為什麼需要這個？

Part 4 到這裡都在打 JIT（TurboFan/Maglev 的優化錯誤）。但 [Ch 2](./02-v8-architecture.md) 的攻擊面礦脈圖裡還有一條中密度礦脈：**Builtins**——那些用 Torque（`.tq`）或 CodeStubAssembler（CSA）手寫的內建函式。它們不經過優化器的「押賭注」，bug 的性質不同：不是「優化假設錯」，而是**手寫低階碼的直接錯誤**（漏檢查、洩漏內部值、狀態機錯）。

這條線重要，因為：(1) 它繞過所有「JIT hardening」——你根本沒碰 JIT；(2) CVE-2021-38003 這種「洩漏 the-hole」的 bug 威力極強，是近年被實際用於攻擊的 0-day。

## 先建立直覺：不該流出廚房的半成品

V8 內部有一些**特殊值**，是引擎自己用的「半成品」，**規範保證 JS 程式永遠看不到它們**。最重要的是 **`the_hole`**（[Ch 13](./13-garbage-collection.md) 提過）——它標記「陣列的洞」「還沒初始化的槽」「字典裡被刪的位置」等。

想像餐廳廚房裡有一種「半成品醬料」，只在後廚流程中用，**絕不能上桌**。整個廚房的流程都預設「客人桌上不會有這罐醬料」，所以沒人在桌邊防它。

**如果某個 bug 讓 `the_hole` 洩漏到 JS 可見層**（你的 JS 變數真的拿到了 `the_hole`），災難就來了：因為 V8 到處都是「這個值不可能是 the_hole」的假設。你把這個「不可能出現的值」餵回各種內建，就能觸發它們從沒設想過的狀態——型別混淆、越界、記憶體破壞。半成品醬料上了桌，整個餐廳的安全假設崩塌。

## 底層機制：CVE-2021-38003 — JSON.stringify 洩漏 the_hole

> 觸發碼針對 Chrome <95.0.4638.69 的 vulnerable 版本；15.3 已修。這是機制分析。

CVE-2021-38003 是 `JSON.stringify` 實作裡的一個 bug（"inappropriate implementation in V8"）。在特定構造下（涉及 stringify 對物件屬性列舉與某些內部快取的處理），**內部的 `the_hole` 值會被當成正常的 JS 值回傳/暴露到腳本層**。

一旦攻擊者的 JS 拿到 `the_hole`，利用鏈大致是：

```
1. 用 JSON.stringify 的 bug，把 the_hole 洩漏到一個 JS 變數 h
2. 把 h 放進一個陣列 / 物件，觸發某些內建對「不可能是 hole」的假設
   例如：陣列的某些操作看到 hole 會走「快速路徑」，跳過初始化/檢查
3. 製造出「長度與實際容量不符」的陣列，或讓某個 map/elements kind 錯亂
   → OOB / type confusion → 接 Part 3 的 addrof/fakeobj → 任意讀寫
```

核心不是「JSON 本身多危險」，而是 **the_hole 這個內部值一旦逃逸，會撬動 V8 各處「hole 不可能出現」的隱含假設**。這也是為什麼這類「內部值洩漏」bug 評級都很高——它的破壞半徑遠超出洩漏點本身。

CVE-2021-38003 被實際用於在野攻擊（列入 CISA 的 Known Exploited Vulnerabilities），是「內建 bug 一樣能造成完整 RCE」的鐵證。

## 其他內建攻擊面

### RegExp：`lastIndex` 與狀態

`RegExp` 是另一塊肥肉，因為它有**可變的內部狀態**（`lastIndex`）和**會回呼使用者碼的縫隙**：

- `regex.exec(str)` 使用並更新 `lastIndex`；如果 `lastIndex` 是一個帶惡意 `valueOf` 的物件（[Ch 21](./21-array-prototype-side-effect.md) 的縫隙再現），能在 match 中途執行你的碼。
- `String.prototype.replace(regex, fn)` 的 `fn` 是 callback；`Symbol.replace`/`Symbol.match` 等讓你能覆寫 RegExp 的行為。
- RegExp 的編譯器（把 pattern 編成 bytecode/機器碼的 irregexp 引擎）本身也是攻擊面，出過記憶體 bug。

### JSON：parse/stringify 的狀態機

`JSON.parse` 的 reviver callback、`JSON.stringify` 的 replacer callback 與 `toJSON` 方法，都是回呼縫隙。加上 stringify 對物件形狀的快取假設，就是 CVE-2021-38003 的溫床。

### 其他

`Array.prototype` 的 Torque 實作（[Ch 21](./21-array-prototype-side-effect.md) 從 JIT 角度看過，這裡是內建實作本身的 bug）、`Promise`、`Proxy`、`WeakRef`、國際化 `Intl`（背後是 ICU，C++ 大庫）都出過內建 bug。

## 對比：JIT bug vs Builtin bug

| 面向 | JIT bug（Ch 19–24） | Builtin bug（本章） |
|---|---|---|
| 出錯的東西 | 優化器對 JS 語意的錯誤推理 | 手寫 Torque/CSA 內建的直接錯誤 |
| 觸發條件 | 要先讓函式變熱、被優化 | 通常直接呼叫內建就觸發，不用等優化 |
| 繞過的防禦 | 需要優化發生 | **繞過所有 JIT hardening** |
| 典型形態 | type confusion via 錯誤假設 | 洩漏內部值 / 漏檢查 / 狀態機錯 |
| 找法 | 讀 compiler、differential fuzz | 讀 `.tq`/CSA、fuzz 內建輸入 |

兩條線的 bug 性質不同、找法不同，但**終點一樣**：都要導向 Part 3 的 addrof/fakeobj → 任意讀寫。

## 踩雷集錦

1. **以為內建函式很安全、bug 只在 JIT**：CVE-2021-38003 是在野 0-day，純內建、沒碰 JIT。內建是獨立且高價值的攻擊面。
2. **低估「內部值洩漏」的嚴重性**：`the_hole` 洩漏聽起來只是「一個奇怪的值跑出來」，但它撬動的是 V8 全域「hole 不可能出現」的假設，破壞半徑巨大。同理還有其他 internal oddball、`uninitialized` 值。
3. **以為 RegExp 就是字串比對**：它有可變狀態（`lastIndex`）、回呼縫隙（`Symbol.replace`、`valueOf`）、和一整個 irregexp 編譯引擎——攻擊面比想像大得多。
4. **把 JSON/RegExp 的 callback 忘掉**：reviver、replacer、`toJSON`、`Symbol.match` 都是使用者碼注入點，和 [Ch 21](./21-array-prototype-side-effect.md) 的縫隙同源。
5. **以為 Torque 是高階語言就不會有記憶體 bug**：Torque 會編成低階的 CSA/機器碼，一樣能直接操作記憶體、一樣能漏檢查。它比手寫 CSA 安全，但不是免疫。

## 進階：再往深一層

- **哪裡找 internal-value 洩漏**：搜 `.tq`/CSA 裡回傳值的路徑，找「有沒有某條路徑可能回傳 `TheHole`/`Uninitialized`/其他 internal oddball 而沒轉成 `undefined`」。這正是 CVE-2021-38003 類 bug 的模式。
- **irregexp 引擎**：RegExp 的 pattern 被編成專用 bytecode（甚至 JIT），`src/regexp/` 是個獨立的小型編譯器，有自己的記憶體 bug 史。想專攻可深入。
- **CSA/Torque 的 `CSA_DCHECK`**：debug build 會檢查很多內部不變式（例如「這裡不該是 hole」）。用帶 dcheck 的 build 跑可疑輸入，能在洩漏發生的第一時間 abort——是研究這類 bug 的利器（呼應 [Ch 0](./00-environment-setup.md) 的 debug build 建議）。
- **`Intl` → ICU**：國際化內建背後是龐大的 C++ 函式庫 ICU，是另一條「內建但其實是大 C++ 庫」的攻擊面。

## 動手練習

1. 在現行 d8 側面理解 the_hole 的「不可見」：`let a=[,];`（稀疏陣列，有個洞）`a[0]` 回 `undefined` 而**不是** the_hole——V8 在讀出時把 hole 轉成了 `undefined`。想一想：如果某個內建**忘了**做這個轉換，會發生什麼（就是 CVE-2021-38003 的本質）。
2. 玩 RegExp 的回呼縫隙：`"abc".replace(/b/, () => { print("cb ran"); return "X"; })`，確認 callback 執行。再試 `let r=/a/; r.lastIndex = { valueOf(){ print("lastIndex valueOf!"); return 0; } }; r.exec("aaa")`——觀察 `valueOf` 是否被呼叫。
3. 讀一篇 CVE-2021-38003 的分析（延伸閱讀），畫出「the_hole 從 JSON.stringify 洩漏 → 撬動哪個假設 → 變成 OOB」的鏈。

## 本章重點整理

- **Builtins（Torque/CSA 手寫內建）**是獨立於 JIT 的攻擊面，bug 是「直接的實作錯誤」而非「優化假設錯」，且**繞過 JIT hardening**。
- **CVE-2021-38003**：`JSON.stringify` 把內部值 **the_hole** 洩漏到 JS 層；因為 V8 各處假設「hole 不可能出現」，破壞半徑巨大，被實際用於在野攻擊。
- **RegExp**（`lastIndex` valueOf、`Symbol.replace`、irregexp 引擎）、**JSON**（reviver/replacer/toJSON callback）都是富礦。
- 找法：讀 `.tq`/CSA 找「internal value 可能洩漏 / 漏檢查」的路徑；用帶 dcheck 的 build 逼出違反不變式的時機。
- 終點和 JIT bug 一樣：導向 addrof/fakeobj → 任意讀寫。

## 自我檢核

- [ ] 能解釋為什麼 builtin bug 繞過 JIT hardening
- [ ] 能說出 the_hole 是什麼、為什麼它洩漏到 JS 層這麼嚴重
- [ ] 能複述 CVE-2021-38003 的鏈：JSON.stringify 洩 hole → 撬動假設 → 記憶體破壞
- [ ] 知道 RegExp 有哪些回呼縫隙和可變狀態
- [ ] 面試被問「非 JIT 的 V8 攻擊面有哪些」，能舉出 JSON/RegExp/內建 + 內部值洩漏的例子

## 延伸閱讀

- **[CVE-2021-38003 分析與 PoC 討論（Chromium bug tracker issue 1263462 / 各家 writeup）](https://nvd.nist.gov/vuln/detail/cve-2021-38003)**
  - **這篇說什麼**：the_hole 洩漏的根因與利用概述。
  - **讀哪裡**：root cause 與「hole 撬動哪個假設」的部分。
  - **和本章的關聯**：本章主線的一手資料；讀它把鏈補完整。

- **[“The Hat Trick: Exploit Chrome Twice from Runtime to JIT” — Black Hat US 2023（含 the-hole 類 runtime bug）](https://i.blackhat.com/BH-US-23/Presentations/US-23-Wang-The-Hat-Trick-Exploit-Chrome-Twice-from-Runtime-to-JIT-wp.pdf)**
  - **這篇說什麼**：runtime/內建層（非 JIT）的 Chrome 利用，含內部值/物件的濫用。
  - **為什麼值得讀**：示範「內建/runtime bug」如何做到完整 RCE，補足本章的利用面。

- **[V8 `src/builtins/*.tq`（Torque 內建原始碼）與 `src/regexp/`](https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/builtins/)**
  - **讀哪裡**：`json-stringify.tq`（對照 CVE 修補）、`array-*.tq` 找 hole 處理；`src/regexp/` 看 irregexp。
  - **和本章的關聯**：這是「內建 bug」的現場，也是自己審這類 bug 的起點。

- **[Project Zero — in-the-wild 0-day 分析（含 CVE-2021-38003）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：被實際用於攻擊的 V8 0-day 分析，理解這類 bug 在真實攻擊中的角色。
  - **為什麼值得讀**：印證「內建 bug 不是玩具，是真 0-day」。

Part 4 的漏洞類別（JIT 五章 + 內建一章）到此完整。你現在有了一整套「V8 bug 長在哪、為什麼、怎麼變成原語」的地圖。下一個練習把這些綜合起來：親手解一題 TurboFan type confusion CTF。

→ [練習 C — TurboFan type confusion CTF 完整解](./practice-c-type-confusion-ctf.md)
