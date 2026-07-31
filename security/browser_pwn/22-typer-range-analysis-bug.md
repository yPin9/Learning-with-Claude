# Ch 22 — Typer / range-analysis bug

> **目標**：正面拆解 CheckBounds 被消的**上游成因**——TurboFan 的 typer 對數值範圍推理錯誤。以史上最有名的 **Math.expm1 `-0` typer bug**（V8 issue 8056，2018）為標本，看清「typer 漏掉一個邊界值」怎麼一路變成任意讀寫。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`。Math.expm1 bug 在 15.3 早已修，觸發碼為理論分析（對照 abiondo 的 writeup）；typer 「會替值推導範圍」這件事本身可用 `--trace-turbo` 觀察。

## 為什麼需要這個？

[Ch 20](./20-checkbounds-redundancy-elimination.md) 講了「護欄被消」的果，但只點到「typer 推錯範圍」是因。這一章把那個因徹底攤開。理由是：**typer bug 是 V8 exploit 裡 CP 值最高的一類**——它汙染的是「範圍」這個上游事實，下游一切依賴範圍的優化（bounds check、型別窄化、常數摺疊）全跟著錯。一個小小的「漏掉 `-0`」，能滾成完整的任意讀寫。看懂它，你就懂了為什麼 V8 團隊對 typer 的每一行都如履薄冰。

## 先建立直覺：報錯身高範圍的門禁

想像一個遊樂設施門禁，規則是「身高 100–140 cm 才能玩」。系統為了快，事先算好「今天排隊這群人的身高範圍是 110–130」，於是門口的量身高機**被拆了**——反正都在範圍內嘛。

問題來了：如果「今天這群人身高 110–130」這個**預估本身是錯的**（其實有個人 200 cm，系統算漏了），而量身高機已經拆了——那個 200 cm 的人就大搖大擺進去了。

typer 就是那個「預估身高範圍」的系統。它替每個值算一個範圍，優化器據此拆掉檢查。**只要 typer 算出的範圍比實際窄（漏算了某個極端值），拆掉的檢查就攔不住那個極端值。**

## 底層機制：typer 怎麼推範圍，怎麼推錯

TurboFan 的 typer（`src/compiler/typer.cc`）替 IR 裡每個 value node 推導一個 **Type**，其中對數字包含一個**範圍** `Range(min, max)`。它靠一組「typing rule」——每個運算一條規則，說「輸入是這個範圍，輸出就是那個範圍」。例如：

- `x` 的型別是 `Range(0, 10)`，那 `x + 1` 的型別是 `Range(1, 11)`。
- `Math.abs(x)`（`x` 是 `Range(-5, 3)`）→ `Range(0, 5)`。

這些規則**必須絕對正確**——因為下游會**完全信任**它們去拆檢查。而「絕對正確」對浮點數特別難，因為 IEEE 754 有一堆惡魔邊界：`NaN`、`+0`/`-0`、`Infinity`、次正規數。

### Math.expm1 的 `-0`

`Math.expm1(x)` 算的是 `e^x - 1`。它的 typer 規則當年寫的輸出範圍**漏掉了 `-0`**：`Math.expm1(-0)` 實際回傳 `-0`，但 typer 以為輸出範圍是 `Range(-1, +Infinity)` 之類、**不含 `-0` 這個特殊值**（typer 用 `PlainNumber` 而非包含 `MinusZero` 的型別）。

`-0` 為什麼要命？因為 `-0` 在多數運算裡等於 `0`，但在某些地方會暴露它的「負」的一面。攻擊者利用「typer 以為不可能是 `-0`、實際是 `-0`」這個認知落差，配合一連串運算，最終**做出一個 typer 以為在界內、實際越界的 index**：

```
1. i = Math.expm1(-0)            // 實際 = -0；typer 以為 = 普通非負數
2. 用一連串運算（如 Object.is(i,-0) 之類的分支、或 -0 特性的算術）
   把「i 是 -0」這個實際事實，轉成一個實際很大、但 typer 以為很小的 index
3. arr[i]                         // typer：i 在界內，消掉 CheckBounds/NumberLessThan
                                  // 實際：i 越界 → OOB！
```

（精確的第 2 步在不同 writeup 有不同構造；核心都是「放大 typer 的範圍誤差」。abiondo 的 writeup 有完整位元級推導。）

### 為什麼連 NumberLessThan 都躲不掉

呼應 [Ch 20](./20-checkbounds-redundancy-elimination.md)：就算 TurboFan 因為偵測到過越界而不敢直接消 `CheckBounds`、改用較弱的 `NumberLessThan` 比較，`NumberLessThan` 一樣吃 typer 的範圍資訊——typer 說 `i < length` 恆真，這個比較節點也被摺掉。**typer 錯一次，好幾種檢查同時失守。** 這是 typer bug 威力遠大於單點 bug 的原因。

## 從錯誤範圍到任意讀寫

typer bug 給的 OOB 通常非常乾淨——同型別、可控 index、穩定。標準後續：

1. OOB 讀寫相鄰陣列的 length 欄位，做出一個「length 巨大」的 double 陣列（`oob_arr`）。
2. `oob_arr` 現在能覆蓋大片堆記憶體 → 接 [Ch 15](./15-addrof-fakeobj.md) 的 addrof/fakeobj → [Ch 17](./17-typedarray-attack.md) 的 TypedArray 任意讀寫。

Krautflare（35C3 CTF 2018）就是 Math.expm1 bug 的 CTF 化，是練這條鏈的最佳題目（延伸閱讀）。

## 對比：typer 容易出錯的地雷區

| 地雷 | 為什麼難 | 歷史案例 |
|---|---|---|
| **`-0`** | 多數運算 `-0==0`，但型別上是獨立值 | Math.expm1（本章） |
| **`NaN`** | `NaN` 破壞所有序關係、傳染性強 | 各種浮點 typer bug |
| **整數溢位 / 大數** | 超過 `Number.MAX_SAFE_INTEGER` 後精度/邊界異常 | typer 加法/乘法邊界 bug |
| **`Infinity`** | 參與運算後範圍變無窮 | 範圍推導失準 |
| **String.length / typed array length** | 有明確上界，typer 過度樂觀 | 索引範圍推導 bug |

想找 typer bug，就盯著這些地雷區，問「這條 typing rule 有沒有把這個極端值算進去？」很多 typer 修補的 diff 就是「在某規則的輸出範圍聯集 `MinusZero` / `NaN`」。

## 踩雷集錦

1. **以為 `-0` 只是無聊的浮點細節**：它是 V8 史上最著名 typer bug 的核心。`-0`、`NaN` 這些邊界值在 exploit 世界是黃金，因為它們最容易被 typing rule 漏掉。
2. **以為 typer 推「更寬」的範圍也危險**：不。更寬的範圍只會讓優化器**更保守**（少消檢查），是安全側。危險的永遠是**更窄**——過度自信、消掉該留的檢查。
3. **把 typer bug 當成獨立漏洞**：typer bug 本身不直接讀寫記憶體。它是「上游汙染源」，要靠下游的 CheckBounds/NumberLessThan 消除才變成 OOB。因（Ch 22）與果（Ch 20）要連著看。
4. **以為修了 Math.expm1 就沒事**：typer 有上百條規則，每條都可能漏邊界。Math.expm1 只是被抓到的一個；這是一整類、會持續出現的 bug。
5. **忽略 typer 規則必須「可靠地悲觀」**：typer 寧可給過寬的範圍（安全但少優化），也不能給過窄（危險）。任何「剛好貼緊」的範圍推導都是危險信號。

## 進階：再往深一層

- **看 typer 推出什麼**：`--trace-turbo` 產生 IR JSON，用 turbolizer（[Ch 10](./10-turbofan-overview.md)）能看到每個 node 被 typer 標的 `Type`（含範圍）。想研究 typer bug，就對照「typer 標的範圍」和「你知道的實際可能值」，找落差。
- **`MinusZero` 與 `NaN` 在型別格（type lattice）裡**：V8 的 Type 系統把 `-0`、`NaN` 當成獨立的型別位元，`PlainNumber` 不含它們。讀 `src/compiler/turbofan-types.h` 看這個格怎麼設計，你會理解為什麼「漏掉 `-0`」在型別上是可能的。
- **範圍在 `Word32` 化之後的溢位**：typer 的數字範圍在 lowering 成 32-bit 整數運算時，若邊界處理不當會 wrap，是另一類 typer/lowering 交界的 bug。
- **不只 CheckBounds**：typer 的錯誤範圍也能讓「型別窄化」出錯（把一個其實可能是物件的值當成純數字），這就跨到 [Ch 23](./23-element-kind-map-confusion.md) 的型別/map 混淆。

## 動手練習

1. 在現行 d8 觀察 typer**存在**：`d8 --allow-natives-syntax --trace-turbo` 跑一個熱迴圈函式，產生 `turbo-*.json`，確認有輸出（真正看圖需 turbolizer）。或用 `--trace-representation` 看型別/表示推導的痕跡。
2. 玩 `-0`：在 d8 跑 `Object.is(-0, 0)`（`false`）、`-0 === 0`（`true`）、`1/-0`（`-Infinity`）、`Math.expm1(-0)`（`-0`）。體會 `-0` 為什麼「多數時候是 0，關鍵時刻不是」。
3. 讀 abiondo 的 Math.expm1 writeup（延伸閱讀），把第 2 步「怎麼把 -0 放大成越界 index」的構造抄下來、逐步推一遍。這是理解 typer bug 從「小誤差」到「大 OOB」的關鍵。

## 本章重點整理

- typer 替每個數值推導一個**範圍**，下游**完全信任**它去消檢查——所以 typer 的範圍**必須絕對正確、寧寬勿窄**。
- **Math.expm1 `-0` bug**：typer 漏掉輸出可能是 `-0`，攻擊者把這個認知落差放大成「typer 以為在界內、實際越界」的 index。
- typer 錯一次，**CheckBounds、NumberLessThan、型別窄化**同時失守——這是它威力巨大的原因。
- typer 的地雷區：`-0`、`NaN`、`Infinity`、整數溢位、length 上界。找 bug 就盯這些。
- typer bug 是**上游汙染源**，要靠下游檢查消除（Ch 20）才變 OOB；因果連著看。

## 自我檢核

- [ ] 能解釋 typer 的「範圍」為什麼必須寧寬勿窄
- [ ] 能複述 Math.expm1 bug：typer 漏掉什麼、攻擊者怎麼放大成越界 index
- [ ] 能說出為什麼一個 typer bug 會讓好幾種邊界檢查同時失守
- [ ] 知道 `-0`、`NaN` 為什麼是 typer bug 的高發區
- [ ] 面試被問「Math.expm1 漏洞的根因」，能答「typer 對輸出範圍漏了 `-0`，導致下游 bounds check 被錯誤消除」

## 延伸閱讀

- **[“Exploiting the Math.expm1 typing bug in V8” — abiondo](https://abiondo.me/2019/01/02/exploiting-math-expm1-v8/)**
  - **這篇說什麼**：本章標本的權威 writeup，從 typer 的 `-0` 漏洞一路推到任意讀寫，位元級完整。
  - **讀哪裡**：整篇。「怎麼把 -0 放大成越界 index」的構造是精華。
  - **和本章的關聯**：本章講骨架，這篇是完整血肉。搭 [Ch 20](./20-checkbounds-redundancy-elimination.md) 一起。

- **[“Exploiting Chrome V8: Krautflare (35C3 CTF 2018)” — Jay Bosamiya](https://www.jaybosamiya.com/blog/2019/01/02/krautflare/)**
  - **這篇說什麼**：把 Math.expm1 bug 做成 CTF 題的完整解，適合當你的第一個 typer-bug 練習題。
  - **為什麼值得讀**：CTF 化、有題目可下載真的打，比純理論好上手。

- **[“Problems about Math.Expm1 Bug in V8” — mem2019](https://mem2019.github.io/jekyll/update/2019/09/05/Problems-About-Expm1.html)**
  - **這篇說什麼**：對這個 bug 幾個容易卡住的細節的補充討論。
  - **和本章的關聯**：當你照 abiondo 走卡住時，這篇補洞。

- **[V8 `src/compiler/typer.cc` 與 `turbofan-types.h`（原始碼）](https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/compiler/)**
  - **讀哪裡**：`typer.cc` 裡 `Math` 家族的 typing rule、`turbofan-types.h` 的型別格（`MinusZero`/`NaN` 怎麼表示）。
  - **和本章的關聯**：看「漏掉 `-0`」在原始碼裡長什麼樣，也是找同類 bug 的現場。

typer 這種靜態成因看完了。下一章換一個角度：不是範圍推錯，而是優化器對物件的 **elements kind / Map** 本身產生錯誤認知——更直接的型別混淆。

→ [Ch 23 — Element-kind confusion / Map transition bug](./23-element-kind-map-confusion.md)
