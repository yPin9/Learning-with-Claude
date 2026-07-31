# Ch 19 — TurboFan type confusion：CVE-2018-17463

> **目標**：吃下第一個真實的 TurboFan 型別混淆（type confusion）。理解優化器怎麼用「守衛（guard）」保護它押的型別賭注，以及當它**錯誤地認為某個操作沒有副作用、於是省掉守衛**時，型別混淆是怎麼發生的。以 **CVE-2018-17463**（`Object.create` 的 `JSCreateObject` side-effect bug，saelo 回報，Chrome ≤69）為主線。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`。本章的 deopt trace 是真跑的；CVE-2018-17463 的漏洞**在 15.3 早已修補、無法在現行 d8 觸發**，其觸發碼為理論分析（對照當年 vulnerable 版本 / saelo 的 writeup）。

## 為什麼需要這個？

Part 4 之前你學的漏洞（Part 3 的 OOB）都是「有人把一個越界原語塞進來」。但真實世界的 V8 漏洞不會有人幫你植入——它們自己長在優化編譯器對 JavaScript 語意的**錯誤推理**裡。這一章是你第一次看到：一段完全合法的 JS，怎麼因為 TurboFan 的一個錯誤假設，變成記憶體破壞。

理解這一類 bug，你才看得懂 2016 年後絕大多數瀏覽器 0-day 的骨架。它們幾乎都是同一個劇本的變奏：**優化器相信某件事不變，攻擊者讓它變。**

## 先建立直覺：省掉的安檢門

回到 [Ch 2](./02-v8-architecture.md) 那個「賭食材是牛肉的廚神」。TurboFan 的優化，本質是**基於假設省略檢查**。但它不是盲目省——它會在關鍵處放一道「安檢門」（guard），萬一假設被打破就觸發 deopt（[Ch 12](./12-speculation-deopt.md)），安全退回直譯器。

親眼看這道安檢門。一個讀陣列元素的函式，優化後 TurboFan 會假設「傳進來的一直是某個特定 map 的陣列」，並放一道 **map check**。當你餵一個不同 shape 的東西進去，安檢門觸發 deopt：

```
$ d8 --allow-natives-syntax --trace-opt --trace-deopt p4.js
[completed compiling 0x..<JSFunction load> (target TURBOFAN_JS) - took ...]
--- now feed a different shape to force deopt ---
[bailout (kind: deopt-eager, reason: wrong map): begin. deoptimizing 0x..<JSFunction load>,
   ... bytecode offset 2, deopt exit 1, ...]
```

**`reason: wrong map`**——這就是安檢門在說話。TurboFan 押注「這個參數的 map 是我優化時看到的那個」，餵進不同 map 時它偵測到、安全退場。這個機制**正常運作時，type confusion 不會發生**。

那 bug 從哪來？**當某個操作偷偷改變了物件的形狀，而 TurboFan 沒把這道安檢門放對地方（或錯誤地把它消掉）**——安檢門形同虛設，優化碼帶著過時的假設繼續執行。這就是 type confusion。

## 底層機制：TurboFan 怎麼追蹤副作用

TurboFan 的 IR（sea-of-nodes，[Ch 10](./10-turbofan-overview.md)）裡，每個操作除了 value edge，還有 **effect edge**——用來表達「這個操作會不會改變世界的狀態」。優化器靠 effect edge 判斷：

- 一個 map check 之後，如果中間**沒有任何有副作用的操作**能改變這個物件的 map，那麼後面再用到這個物件時，**map check 可以省掉**（redundancy elimination，[Ch 11](./11-optimization-pipeline.md)）——反正 map 不可能變。
- 但如果 TurboFan **錯誤地把一個「其實會改變 map / 物件表示」的操作標記成「無副作用」**，它就會誤消掉本該保留的 check。

一句話：**副作用模型錯了 → 安檢門被誤消 → type confusion。** CVE-2018-17463 正是這個機制的教科書案例。

## CVE-2018-17463：`Object.create` 的謊言

> 以下觸發碼針對 Chrome ≤69 的 vulnerable V8；在本課 15.3 已修，無法觸發。這是機制分析，對照 saelo 的公開 writeup。

漏洞出在 TurboFan 對 **`JSCreateObject`**（`Object.create(proto)` 對應的 IR 操作）的副作用模型。TurboFan **假設 `JSCreateObject` 完全無副作用**。

問題是這個假設是**謊言**。`Object.create(proto)` 在某些情況下會**改變 `proto` 這個物件的內部表示**：如果 `proto` 原本用 **fast property storage**（PropertyArray，見 [Ch 6](./06-properties-elements.md)），`Object.create` 會把它轉成 **dictionary mode**（NameDictionary）。這是一個實打實的副作用——它改變了 `proto` 的 properties backing store 的**型別**。

於是災難鏈長這樣：

```
1. TurboFan 優化一段碼，其中讀取 proto 的某個 fast property
   → 它基於「proto 是 fast property storage」放/消了相關的存取碼
2. 中間呼叫 Object.create(proto)
   → TurboFan 以為無副作用，什麼都不做
   → 但實際上 proto 被轉成了 dictionary mode（NameDictionary）
3. 優化碼繼續用「fast property（PropertyArray）」的方式去存取
   現在其實是 NameDictionary 的東西
   → PropertyArray 與 NameDictionary 的型別混淆！
```

`PropertyArray` 和 `NameDictionary` 在記憶體裡佈局完全不同（一個是線性陣列、一個是雜湊表）。優化碼拿「陣列的方式」去解讀「雜湊表的位元組」，就是把一段記憶體**當成錯誤的型別**——經典 type confusion。攻擊者接著用它讀寫到不該碰的記憶體，一路做出 [Ch 15](./15-addrof-fakeobj.md) 的 `addrof`/`fakeobj` 原語。

## 從 type confusion 到利用原語

拿到「把 A 型別當 B 型別」的混淆後，怎麼接到你 Part 3 的 template？關鍵是找到一組「兩種解讀方式的欄位錯位」，讓你能：

- **越界或錯位讀**：把某個欄位（例如一個長度、一個指標）用錯誤型別讀出來，洩漏資訊。
- **可控寫**：讓混淆後你能寫到一個你控制內容的位置，最終偽造一個物件。

不同的 type confusion 給的原語強弱不同。好的 confusion（例如能直接偽造一個 `length` 很大的陣列）幾乎立刻給你 OOB；差的可能要繞好幾步。CVE-2018-17463 的 PropertyArray↔NameDictionary 混淆，saelo 把它一路做到穩定的任意讀寫——細節見延伸閱讀的原始 writeup。

## 對比：type confusion 的幾種「守衛失效」模式

| 守衛失效方式 | 機制 | 本課章節 |
|---|---|---|
| **副作用模型錯**（本章） | 某操作偷偷改型別/map，TurboFan 以為無副作用 | Ch 19（CVE-2018-17463） |
| **範圍推理錯**（typer bug） | typer 給出錯誤的值範圍，導致 bounds check 被消 | [Ch 22](./22-typer-range-analysis-bug.md) |
| **check 被冗餘消除誤消** | redundancy/BCE 過度激進消掉 CheckBounds | [Ch 20](./20-checkbounds-redundancy-elimination.md) |
| **優化期間 callback 改變世界** | side-effect 發生在優化器沒防的時間點 | [Ch 21](./21-array-prototype-side-effect.md)、[Ch 24](./24-jit-side-effect.md) |

這四種是 TurboFan type confusion 的四大家族，Part 4 一章一個。它們共通的骨架都是本章講的：**優化器對「什麼會變、什麼不變」判斷錯了。**

## 踩雷集錦

1. **以為 type confusion 是「記憶體壞掉」**：不是。它是「同一段記憶體被兩段碼用不同型別解讀」。記憶體本身沒壞，是**解讀方式**錯了。抓住這個定義，後面所有 JIT bug 都好懂。
2. **以為 deopt 是漏洞**：相反。deopt（`reason: wrong map`）是安檢門**正常運作**的證據。漏洞是「該 deopt 卻沒 deopt」——安檢門被誤消或放錯位置。
3. **把 CVE-2018-17463 的觸發碼拿到現代 d8 跑**：跑不出來，它 2018 年就被修了。你要學的是**機制**；要重現得 checkout 當年的 commit 自己編。
4. **以為 `Object.create` 本身是 bug**：`Object.create` 完全正常。bug 是 **TurboFan 對它的副作用模型錯了**。同一段 JS 在直譯器下永遠安全，只有被 TurboFan 優化後才出事——這是所有 JIT bug 的共同特徵。
5. **忽略「fast → dictionary」轉換這個副作用**：這個轉換（[Ch 6](./06-properties-elements.md) 講過）在正常開發中只是效能細節，但它是這個 CVE 的核心。很多 V8 bug 的「副作用」就藏在這種平常沒人注意的內部狀態轉換裡。

## 進階：再往深一層

- **怎麼自己找這類 bug**：讀 `src/compiler/` 裡各 IR 操作的 `Operator` 定義，看它宣告的 `Operator::Properties`（`kNoWrite`、`kNoThrow`、`kEliminatable`…）。凡是被標成「無副作用/可消除」的操作，都要問一句：「它真的無副作用嗎？有沒有哪條路徑會改變某個物件的表示？」這正是 Part 5（[Ch 27](./27-patch-diffing.md)）patch diffing 的著眼點——很多修補就是「把某個操作的 properties 標對」。
- **Maglev 也有同源病**：[Ch 2](./02-v8-architecture.md) 提過的中階 JIT Maglev 同樣基於 feedback 押賭注，也會有副作用模型錯的 bug，只是 IR 不同。
- **NoElementsProtector 等「保護器」**：V8 有一組全域 protector cell，用來讓優化器對「沒人動過 Array.prototype」這類假設能安全省檢查。攻擊者讓 protector 失效（invalidate）也是一種讓假設鬆動的手法，[Ch 21](./21-array-prototype-side-effect.md) 會碰到。

## 動手練習

1. 用 `--trace-deopt` 製造各種 deopt reason：餵不同 map、餵不同 elements kind、讓一個原本是 int 的變數變成 double。收集你能觸發的 deopt reason 清單。這些 reason 就是「安檢門的種類」，理解它們才知道哪種假設有安檢門保護、哪種沒有。
2. 讀 saelo 的 CVE-2018-17463 writeup（延伸閱讀），對照本章的災難鏈，找出他實際是用哪個 fast property 存取觸發混淆的。
3. 在現行 d8 用 `%DebugPrint` 觀察：一個有 fast property 的物件，被 `Object.create(它)` 當 prototype 後，它的 properties 是不是真的變成 dictionary/NameDictionary？（這個副作用本身在 15.3 仍在，只是 TurboFan 現在正確建模了它。）

## 本章重點整理

- TurboFan 用 **effect edge** 追蹤副作用，據此決定哪些 **map check / 型別 check 可以省**。
- **副作用模型錯**（把會改型別的操作當成無副作用）→ 安檢門被誤消 → **type confusion**：同一段記憶體被兩段碼用不同型別解讀。
- CVE-2018-17463：`JSCreateObject`（`Object.create`）被誤認無副作用，但它會把 prototype 從 fast property 轉成 dictionary mode → **PropertyArray↔NameDictionary 混淆**。
- deopt（`wrong map`）是安檢門**正常**的證據；漏洞是「該 deopt 卻沒 deopt」。
- 這是 TurboFan bug 四大家族的第一個；共通骨架是「優化器對什麼會變判斷錯」。

## 自我檢核

- [ ] 能用一句話定義 type confusion，且不含「記憶體壞掉」這種模糊說法
- [ ] 能解釋 effect edge / 副作用模型和「map check 能不能省」的關係
- [ ] 能複述 CVE-2018-17463 的災難鏈：優化假設 → Object.create 副作用 → 型別混淆
- [ ] 知道為什麼同一段 JS 在直譯器安全、被 TurboFan 優化後才出事
- [ ] 面試被問「什麼是 JIT type confusion」，能用「優化器省掉了本該保留的型別守衛」回答

## 延伸閱讀

- **[“Exploiting Logic Bugs in JavaScript JIT Engines” — Samuel Groß (saelo), Phrack 70:9](https://phrack.org/issues/70/9)**
  - **這篇說什麼**：JIT 邏輯漏洞的方法論總綱，CVE-2018-17463 是其中的招牌案例。整個 Part 4 的思想源頭。
  - **讀哪裡**：先讀「side-effect modeling」與 CVE-2018-17463 的段落。
  - **和本章的關聯**：本章的災難鏈就是它的濃縮；讀它補齊從混淆到任意讀寫的完整利用。

- **[SSD Advisory – Chrome Type Confusion in JSCreateObject Operation to RCE](https://ssd-disclosure.com/ssd-advisory-chrome-type-confusion-in-jscreateobject-operation-to-rce/)**
  - **這篇說什麼**：CVE-2018-17463 的官方揭露，含 PoC 與根因說明。
  - **讀哪裡**：root cause 段落，對照本章「fast → dictionary」的副作用。
  - **前提**：先懂 [Ch 6](./06-properties-elements.md) 的 fast/dictionary property。

- **[“Chrome Browser Exploitation, Part 3: Analyzing and Exploiting CVE-2018-17463” — jhalon](https://jhalon.github.io/chrome-browser-exploitation-3/)**
  - **這篇說什麼**：一步步從漏洞到 exploit 的教學向 writeup，附可運作 PoC repo。
  - **為什麼值得讀**：比 Phrack 更手把手，適合你想真的 checkout 舊版重現時照著做。

- **[V8 `src/compiler/` 的 `Operator::Properties`（原始碼）](https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/compiler/)**
  - **讀哪裡**：搜 `kNoWrite` / `kEliminatable` 的用法，看各操作怎麼宣告副作用。
  - **和本章的關聯**：這是「副作用模型」在原始碼裡的樣子，也是找同類 bug 的起點。

守衛失效的第一種模式（副作用模型錯）看完了。下一章換第二種：優化器把保護陣列存取的 **CheckBounds** 給消掉——這是最直接通往 OOB 的一條路。

→ [Ch 20 — CheckBounds / redundancy-elimination bug](./20-checkbounds-redundancy-elimination.md)
