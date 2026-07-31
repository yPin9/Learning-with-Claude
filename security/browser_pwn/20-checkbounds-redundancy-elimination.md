# Ch 20 — CheckBounds / redundancy-elimination bug

> **目標**：搞懂 TurboFan 怎麼用 **CheckBounds** 節點保護每一次陣列存取，以及當「冗餘消除（redundancy elimination）」或「範圍推理」過度激進、把本該保留的 CheckBounds 消掉時，怎麼直接變成越界（OOB）讀寫。這是所有 type confusion 家族裡**最短路徑通往 OOB** 的一種。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`。本章的 bounds-check deopt 是真跑的；被拿來當例子的歷史 typer/BCE bug 在 15.3 已修，觸發碼為理論分析。

## 為什麼需要這個？

上一章的副作用模型 bug 要繞好幾步才拿到原語。CheckBounds 消除 bug 不一樣——它**直接**給你「用一個超出範圍的 index 讀寫陣列」。一旦你能 `arr[out_of_range]` 而不 crash、不 deopt，你就已經站在 OOB 原語的門口了（接 [Ch 14](./14-first-oob.md) 那套）。所以這類 bug 在 CTF 和真實世界都是最搶手的。

## 先建立直覺：被拆掉的護欄

每一次 JS 陣列存取 `a[i]`，TurboFan 都會在機器碼裡放一道護欄：**「先確認 `i` 在 `[0, length)` 裡，不在就 deopt」**。這道護欄在 IR 上叫 **CheckBounds** 節點。護欄還在時，越界存取會安全退場：

```
$ d8 --allow-natives-syntax --trace-opt --trace-deopt bce.js
[completed compiling 0x..<JSFunction get> (target TURBOFAN_JS) - took ...]
--- in-bounds ok, now access out of declared range ---
[bailout (kind: deopt-eager, reason: out of bounds): begin. deoptimizing 0x..<JSFunction get>, ...]
```

**`reason: out of bounds`**——這就是 CheckBounds 護欄在說話。`get(arr, 100)` 越界，護欄偵測到、deopt、安全。type confusion 不會發生。

那 bug 從哪來？**當 TurboFan「證明」了 `i` 一定在範圍內、於是把這道護欄當成冗餘拆掉——但它的證明是錯的。** 護欄沒了，`arr[100]` 就真的去讀第 100 格的記憶體，越界成立。

## 底層機制：CheckBounds 何時「可以」被消

TurboFan 消 CheckBounds 不是亂消，它有依據——問題出在依據被騙。消除的兩條主要路徑：

### 路徑一：typer 證明 index 在範圍內

TurboFan 的 **typer**（[Ch 11](./11-optimization-pipeline.md)、深入見 [Ch 22](./22-typer-range-analysis-bug.md)）會替每個值推導一個**型別 + 數值範圍**。如果它推出「`i` 的範圍是 `[0, 3]`」而陣列 length 是 4，那 `i < length` 恆成立——CheckBounds 是冗餘的，消掉。

這在 `i` 真的被限制住時完全正確，例如：

```js
for (let i = 0; i < a.length; i++) a[i];  // i 明顯 < length，CheckBounds 可消
```

**bug 在於：如果 typer 對某個運算的範圍推理錯了**（例如漏掉 `-0`、漏掉溢位、漏掉某個邊界），它會給出一個**比實際更窄**的範圍，讓 TurboFan 誤以為 index 一定在界內，消掉護欄。攻擊者就用那個「typer 以為不可能、實際做得到」的 index 值去越界。這正是著名的 **Math.expm1 typer bug** 的機制（完整拆在 [Ch 22](./22-typer-range-analysis-bug.md)）。

### 路徑二：redundancy elimination（前面查過就不再查）

如果同一個 index 已經被一道 CheckBounds 檢查過，而中間沒有任何操作能改變 index 或 length，那後面對同一 index 的存取就不用再檢查——這是 **redundancy elimination（冗餘消除）**。

bug 在於：**如果中間其實有操作偷偷改了 length（例如把陣列縮短），而 TurboFan 沒把這件事算進去**（副作用沒建模對，呼應 [Ch 19](./19-turbofan-type-confusion.md)），那「前面查過所以安全」的推論就崩了——第一次查的時候 index 合法，改短之後再用同一 index 就越界，但護欄已經被當冗餘拿掉。[Ch 21](./21-array-prototype-side-effect.md) 的 side-effect bug 常和這條路徑合流。

## CheckBounds 被消之後：NumberLessThan 也躲不掉

有個微妙但重要的細節（真實 Math.expm1 exploit 的關鍵）：就算 TurboFan 因為偵測到「這裡曾發生過越界」而**不敢直接消 CheckBounds**，它可能改用一個較弱的 **`NumberLessThan`** 節點來做範圍比較——而 `NumberLessThan` 一樣會被 typer 的範圍資訊給消掉。也就是說，**typer 的錯誤範圍會連累好幾種形式的邊界檢查**，不只 CheckBounds 一種。這是為什麼 typer bug（Ch 22）如此致命：它汙染的是「範圍」這個上游事實，下游所有依賴範圍的優化都跟著錯。

## 從「消掉的護欄」到 OOB 原語

一旦你有「`arr[evil_index]` 不 deopt、真的去存取越界記憶體」，接下來就是熟悉的套路：

1. 佈局：讓 `arr` 後面緊接著你關心的目標（另一個陣列的 length 欄位、一個物件的 elements 指標…）。GC 佈局引導見 [Ch 13](./13-garbage-collection.md)。
2. 用越界**讀**洩漏相鄰 metadata（例如讀出相鄰陣列的 map、length）。
3. 用越界**寫**竄改相鄰陣列的 length，做出一個「length 超大」的陣列，得到穩定、範圍更廣的 OOB → 接 [Ch 15](./15-addrof-fakeobj.md) 的 addrof/fakeobj。

CheckBounds bug 的甜蜜處在於：它給的 OOB 通常在**同一個 elements 型別**內（例如都是 double 陣列），乾淨、好控。

## 對比：CheckBounds 被消的幾種成因

| 成因 | TurboFan 為什麼消 | 真實例子 |
|---|---|---|
| **typer 範圍推錯** | 以為 index 恆在界內 | Math.expm1 `-0` typer bug（[Ch 22](./22-typer-range-analysis-bug.md)） |
| **length 被偷改** | 以為前面查過、中間沒變 | side-effect / 縮短陣列（[Ch 21](./21-array-prototype-side-effect.md)） |
| **induction variable 分析錯** | 迴圈變數範圍推導錯誤 | 各種 loop-based BCE bug |
| **整數運算溢位未建模** | 以為某加法不會 wrap | typer 對大數的邊界 bug |

四種成因，同一個結果：**護欄沒了，OOB 成立**。抓住這個共通點，你看任何 BCE writeup 都能一眼定位「它是哪種成因騙過了 typer/RE」。

## 踩雷集錦

1. **以為 OOB 讀回 `undefined` 就是安全**：在**沒被優化**的直譯器層，`arr[100]`（超過 length）確實回 `undefined`，這是 JS 語意、不是漏洞。危險的是**優化後 CheckBounds 被消**，此時 `arr[100]` 直接去讀第 100 格的**原始記憶體**，不再回 `undefined`。務必分清「JS 語意越界」和「JIT 護欄消失的越界」。
2. **以為看到 `out of bounds` deopt 代表有 bug**：相反，那代表護欄**正常**。bug 是「越界卻**沒**出現這個 deopt」。
3. **把 typer bug 當成獨立於 BCE 的東西**：它們是因果。typer 給錯範圍是**因**，CheckBounds/NumberLessThan 被消是**果**。Ch 22 講因、本章講果，要合起來看。
4. **忽略 `NumberLessThan` 這種替代檢查**：以為「TurboFan 不敢消 CheckBounds 就安全了」。錯。它可能換成較弱的比較節點，一樣被 typer 範圍消掉。
5. **假設消除只發生在直接的 `a[i]`**：`.length` 讀取、`copyWithin`、`fill`、TypedArray 存取等內建也依賴範圍推理，一樣可能中招。

## 進階：再往深一層

- **看 CheckBounds 在哪**：`--trace-turbo` 產生的 IR（用 turbolizer 看，[Ch 10](./10-turbofan-overview.md)）裡能看到 `CheckBounds` 節點，以及它在 typer 給出範圍後是否被 `simplified-lowering` 消掉。想自己研究這類 bug，追 `src/compiler/simplified-lowering.cc` 與 `typer.cc`。
- **hardening：即使 typer 錯了也想擋住**。V8 後來加了緩解，例如對某些 OOB store 做額外的 runtime check、或讓 CheckBounds 在 abort 而非放行。這是「防禦把攻擊逼進更窄角落」的又一例——但 data-only、跨越到別的原語的路仍在（[Ch 36](./36-cfi-cet-data-only.md)）。
- **TypedArray 的 detach 競態**：TypedArray 存取的邊界依賴 backing store 還在（沒被 detach）。優化期間若 buffer 被 detach，長度歸零，是另一種「length 被偷改」的變體。
- **和 Spectre 的交界**：CheckBounds 也和推測執行的側通道防禦有關（`--no-untrusted-code-mitigations` 等旗標的歷史），但那是另一條線。

## 動手練習

1. 用 `--trace-deopt` 確認：一個被優化的函式對陣列越界存取，會不會吐 `reason: out of bounds`？改成迴圈 `for (i<len)` 的形式，觀察 TurboFan 是否因為能證明範圍而**不再** deopt（因為它合法地消了 CheckBounds）。體會「合法消除」長什麼樣。
2. 讀 abiondo 的 Math.expm1 writeup（延伸閱讀），找出：typer 對 `Math.expm1(-0)` 到底推出什麼錯誤範圍，以及那個錯誤怎麼讓 CheckBounds/NumberLessThan 被消。
3. 思考題（面試常問）：為什麼「typer 給出比實際**更窄**的範圍」是危險的，而「更寬」的範圍反而安全？（提示：更窄 → 優化器過度自信 → 消掉該留的檢查。）

## 本章重點整理

- 每次陣列存取，TurboFan 放一道 **CheckBounds** 護欄，越界則 deopt（真跑：`reason: out of bounds`）。
- 護欄可被合法消除（typer 證明 index 在界內、或前面查過且中間沒變）——**問題是依據被騙**。
- **typer 範圍推錯**是最常見成因，且會連累 `NumberLessThan` 等替代檢查——汙染的是「範圍」這個上游事實。
- 護欄一消，`arr[evil]` 直接越界存取原始記憶體（不再回 `undefined`），接 addrof/fakeobj。
- 「JS 語意越界回 `undefined`」≠「JIT 護欄消失的越界」，務必分清。

## 自我檢核

- [ ] 能解釋 CheckBounds 節點的作用，以及它被 deopt 觸發時代表什麼
- [ ] 能說出 CheckBounds 被「合法消除」的兩條路徑，以及各自怎麼被騙
- [ ] 能分辨「直譯器越界回 undefined」和「優化後護欄消失的真越界」
- [ ] 知道為什麼 typer 給「更窄」的範圍是危險的
- [ ] 面試被問「BCE bug 怎麼變成 OOB」，能完整講因（typer/RE 錯）到果（護欄消失）到利用（改相鄰 length）

## 延伸閱讀

- **[“Exploiting the Math.expm1 typing bug in V8” — abiondo](https://abiondo.me/2019/01/02/exploiting-math-expm1-v8/)**
  - **這篇說什麼**：把「typer 漏掉 `-0` → CheckBounds/NumberLessThan 被消 → OOB」講到位元級，是本章因果鏈的完整實證。
  - **讀哪裡**：從 typer 分析到 exploit 整篇；本章的「NumberLessThan 也躲不掉」就出自這裡。
  - **和本章的關聯**：本章講機制骨架，這篇是骨架上的血肉。搭 [Ch 22](./22-typer-range-analysis-bug.md) 一起讀。

- **[“Exploiting TurboFan Through Bounds Check Elimination” — GTS3 / Georgia Tech](https://gts3.org/2019/turbofan-BCE-exploit.html)**
  - **這篇說什麼**：直接以 BCE 為題，示範從消掉的護欄做到任意讀寫。
  - **為什麼值得讀**：標題就是本章主題，方法論高度重疊，適合對照學。

- **[V8 `src/compiler/typer.cc` 與 `simplified-lowering.cc`（原始碼）](https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/compiler/)**
  - **讀哪裡**：`typer.cc` 裡各運算的範圍推導（尤其 `Math` 家族）、`simplified-lowering` 裡 CheckBounds 的處理。
  - **和本章的關聯**：這是「typer 怎麼推範圍、CheckBounds 怎麼被消」在原始碼的樣子，也是找同類 bug 的起點。

護欄被消的「果」看完了，下一章往上游走一步：Array.prototype 上的方法在優化期間觸發 callback，趁機改變世界——一種讓假設在優化器眼皮底下鬆動的經典手法。

→ [Ch 21 — Array.prototype side-effect / species](./21-array-prototype-side-effect.md)
