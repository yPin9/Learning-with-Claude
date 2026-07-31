# Ch 21 — Array.prototype side-effect / species

> **目標**：理解 type confusion 的第三個家族——**優化期間跑了攻擊者的 callback，趁機把世界改掉**。看清 `Array.prototype` 上的方法（`map`/`concat`/`fill`/`sort`…）與 `Symbol.species`、`valueOf`、getter 這些「會回呼使用者程式碼」的縫隙，怎麼讓一個本該不變的假設在優化器眼皮底下鬆動。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`。本章用到的歷史 side-effect bug 在 15.3 已修，觸發碼為理論分析；「callback 會不會在內建方法中途跑」這件事本身可在現行 d8 觀察。

## 為什麼需要這個？

前兩章的假設是「靜態」被騙的——typer 推錯範圍、副作用模型標錯。這一章的假設是**動態**被騙：優化碼跑到一半，呼叫了一個看似無害的內建方法或運算，而那個方法會**回呼你寫的 JS**。你的 callback 就是那個「趁廚神轉身把牛肉換成豆腐」的時機。

這類 bug 特別陰險，因為觸發點藏在「JS 語言規範要求呼叫使用者 callback」的地方——它們是合法語意，不是 V8 亂寫。攻擊者只是把 callback 塞滿惡意副作用。

## 先建立直覺：中途插隊的電話

想像 TurboFan 優化了一段碼，邏輯是：「檢查這個陣列是 PACKED_DOUBLE、拿到它的 elements 指標、然後對每個元素做某事」。它在「檢查」和「使用」之間，做了一個賭注：**這中間 elements 不會變**。

但如果「做某事」這一步其實會**打一通電話給你**（呼叫你提供的 callback / species constructor / valueOf），而你在電話裡把陣列**改短、換型別、或搬走 backing store**——等電話掛掉、優化碼回來繼續用它剛才拿到的舊 elements 指標時，那個指標已經指向過時或錯誤的東西了。

```
   優化碼：  [檢查 map/length] ──► [拿 elements 指標] ──► 呼叫 callback(...) ──► [用剛才的指標存取]
                                        ↑ 賭注：這中間不變          │
                                        └──────── 但 callback 是你的 ┘
                                          你在這裡：arr.length = 0
                                          或 arr = 換成別的 elements kind
                                          或 detach backing store
                                        ──► 回來用舊指標 = 越界/型別混淆
```

## 底層機制：JS 規範要求的「回呼縫隙」

哪些地方會在內建方法中途跑你的 JS？這不是 bug，是 ECMAScript 規範明訂的行為——正因為合法，才容易被 V8 的優化忽略：

### 縫隙一：`Symbol.species`

`Array.prototype.map`、`filter`、`slice`、`concat` 這些會**產生新陣列**的方法，規範要求它們透過 `constructor[Symbol.species]` 決定用什麼建構子造結果陣列。攻擊者覆寫 `Symbol.species` 成一個惡意建構子，就能在方法執行中途插入自己的碼：

```js
class EvilArray extends Array {
  static get [Symbol.species]() {
    return function(...args) {
      // 這裡是你的碼，在 map/filter 內部被呼叫
      // 趁機改原陣列的 length / elements kind / detach
      return new Array(...args);
    };
  }
}
```

### 縫隙二：`valueOf` / `toString` / `@@toPrimitive`

任何需要把物件轉成數字/字串的地方（例如 `arr.fill(evilObj)`、`arr[evilObj]`、算術運算），都會呼叫該物件的 `valueOf`。攻擊者用一個帶惡意 `valueOf` 的物件，就能在轉換那一刻執行任意碼：

```js
let evil = { valueOf() { arr.length = 0; return 0; } };
arr.fill(evil);  // fill 內部呼叫 evil.valueOf() 時，arr 被改短
```

### 縫隙三：getter / Proxy

物件屬性的 getter、`Proxy` 的 trap，同樣是規範允許的回呼點。存取 `obj.x` 若 `x` 是 getter，就跑你的碼。

### 為什麼優化碼會中招

TurboFan 在優化一個內建方法（或內聯它）時，如果**沒有正確地在 callback 之後重新載入/重新檢查**它先前快取的狀態（elements 指標、length、map），就會用過時的值。修補這類 bug 的方式通常是：在 callback 後強制重新檢查（重新放一道 map check / bounds check），或乾脆不對「可能回呼」的路徑做該優化。這又回到 [Ch 19](./19-turbofan-type-confusion.md) 的副作用建模——callback 就是最強的「副作用」。

## 一個具體災難鏈（理論，對照歷史 bug）

> 觸發碼針對當年 vulnerable 版本；在 15.3 已修。

```
1. 攻擊者造一個 PACKED_DOUBLE 陣列 arr（元素都是 double）
2. 呼叫某個優化過、會回呼的方法，例如 arr.map(callback) 或帶惡意 valueOf 的操作
3. 優化碼已經：確認 arr 是 PACKED_DOUBLE、快取了 elements 指標
4. 在 callback 裡：arr[0] = {} 之類，把 arr 轉成 PACKED_ELEMENTS（存物件指標）
   → elements backing store 換了型別、甚至換了位置
5. callback 返回，優化碼用「舊的、以為是 double 的」方式寫入
   → 把一個攻擊者控制的 double 寫進「本該是物件指標」的欄位
   → 或反過來，把物件指標當 double 讀出 = addrof
```

第 4 步的 elements kind 轉換（[Ch 7](./07-jsarray-elements-kind.md)）是關鍵副作用。這條鏈常常一步就給你 addrof/fakeobj，因為它本質就是「double 和物件指標的混淆」。

## 對比：三大回呼縫隙

| 縫隙 | 在哪觸發 | 攻擊者塞什麼 |
|---|---|---|
| `Symbol.species` | `map`/`filter`/`slice`/`concat` 造結果陣列時 | 惡意 species 建構子 |
| `valueOf`/`@@toPrimitive` | 物件被轉數字/字串（`fill`、index、算術） | 惡意 `valueOf` |
| getter / Proxy trap | 存取屬性、`Reflect`/`Proxy` 操作 | 惡意 getter / trap |

共通點：**都是規範允許的「執行你的碼」的時機**。找這類 bug 的思路就是：列出一個內建方法規範上會回呼使用者碼的每一個點，然後問「TurboFan 在這個點之後，有沒有重新檢查它先前的假設？」

## 踩雷集錦

1. **以為內建方法是原子的**：`Array.prototype.map` 看起來像一個不可分割的操作，但規範要求它中途呼叫 callback、species、valueOf。這些縫隙是**語言特性**，不是實作疏忽——V8 的疏忽在於優化時沒防它們。
2. **以為只有明顯的 callback（如 map 的第一參數）才危險**：`Symbol.species`、`valueOf`、getter 這些**隱式**回呼更陰險，因為容易被忽略。`arr.fill(obj)` 看起來沒有 callback，其實 `obj.valueOf` 會被叫。
3. **把這類 bug 和 typer bug 搞混**：typer bug（Ch 22）是靜態範圍推錯；side-effect bug 是動態地在 callback 裡改變狀態。成因不同，但都導致「優化碼用了過時假設」。
4. **以為 detach 之後 length 是安全的**：TypedArray 的 backing store 被 detach 後，若優化碼還握著舊的 backing store 指標，就是 use-after-free 式的存取。detach 是 side-effect 的一種強形式。
5. **忽略「重新載入」才是修補重點**：這類 bug 的 fix 不是「禁止 callback」（不可能，規範要求），而是「callback 之後重新檢查/重新載入」。理解這點才看得懂 patch。

## 進階：再往深一層

- **怎麼系統性找**：對每個用 Torque/CSA 寫的 Array 內建（`src/builtins/*.tq`），標出所有 `Call`（呼叫使用者可見函式）的點，檢查之後有沒有重新 `LoadElements` / 重新 check length。V8 內部有 `EnsureArrayLengthWritable` 之類的防護，看哪裡漏了。
- **`Array.prototype.sort` 的比較函式**：`sort(cmpFn)` 的 `cmpFn` 是最肥的 callback 之一，歷史上多個 bug 在此（sort 中途改陣列）。
- **與 BCE 合流**：side-effect 改短 length 後，若前面的 CheckBounds 已被當冗餘消掉（[Ch 20](./20-checkbounds-redundancy-elimination.md)），越界就成立。這兩個家族經常合作。
- **Proxy 是回呼的放大器**：`Proxy` 讓幾乎任何操作都能觸發 trap，是研究這類 bug 時的萬用觸發器。想深挖可專門研究「optimized code + Proxy」的交互。

## 動手練習

1. 在現行 d8 驗證回呼縫隙**存在**（不是 bug，是語意）：寫 `let a=[1,2,3]; a.fill({valueOf(){ print("callback ran!"); a.length=1; return 0; }});` 然後 `print(a.length)`，確認 `valueOf` 真的在 `fill` 中途跑了、且改了 length。你剛示範了「內建方法中途執行你的碼」。
2. 用 `Symbol.species` 做同樣的事：`class E extends Array { static get [Symbol.species](){ print("species!"); return Array; } }`，`new E(1,2,3).map(x=>x)`，確認 species getter 被呼叫。
3. 讀一篇 `Array.prototype.sort` 或 species 相關的 V8 bug（延伸閱讀），找出「優化碼在 callback 後**沒有**重新載入哪個狀態」。

## 本章重點整理

- 第三個 type confusion 家族：**優化期間跑了攻擊者的 callback**，趁機改變陣列的 length / elements kind / backing store。
- 回呼縫隙都是 **ECMAScript 規範要求**的合法時機：`Symbol.species`、`valueOf`/`@@toPrimitive`、getter/Proxy trap。
- 優化碼中招的原因是 **callback 之後沒重新檢查/重新載入**先前快取的狀態（指標、length、map）。
- 這條鏈常一步給出 addrof/fakeobj（本質是 double↔物件指標混淆）；且常與 [Ch 20](./20-checkbounds-redundancy-elimination.md) 的 BCE 合流。
- 修補重點是「重新載入」，不是「禁止 callback」。

## 自我檢核

- [ ] 能列出三種規範允許的回呼縫隙，各舉一個觸發的 JS 寫法
- [ ] 能解釋為什麼 `arr.fill(obj)` 這種「看起來沒 callback」的呼叫其實會跑你的碼
- [ ] 能複述一條「callback 改 elements kind → 優化碼用舊型別存取」的災難鏈
- [ ] 知道這類 bug 的 fix 為什麼是「重新載入」而非「禁止回呼」
- [ ] 面試被問「Symbol.species 為什麼是安全問題」，能答出「它在內建方法中途注入使用者碼」

## 延伸閱讀

- **[“Exploiting Logic Bugs in JavaScript JIT Engines” — saelo, Phrack 70:9](https://phrack.org/issues/70/9)**
  - **這篇說什麼**：涵蓋 side-effect / callback 類邏輯 bug 的方法論，與 Ch 19 同源。
  - **讀哪裡**：callback / side-effect 相關段落。
  - **和本章的關聯**：把「哪裡會回呼、優化器怎麼漏防」講成一套可複製的找 bug 流程。

- **[Google Project Zero — “The Great DOM Fuzz-off” 之外的 V8 species/sort writeup（googleprojectzero.blogspot.com）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：真實的 `Array.prototype` side-effect / species bug 分析。
  - **讀哪裡**：挑一篇標題含 `Array` / `species` / `sort` 的，看它怎麼用 callback 改變狀態。
  - **前提**：先懂本章三種縫隙。

- **[V8 `src/builtins/` 的 Array 內建（Torque `.tq`）原始碼](https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/builtins/)**
  - **讀哪裡**：`array-map.tq`、`array-filter.tq`、`array-fill.tq`，找 `Call` 之後有沒有重新 load elements。
  - **和本章的關聯**：這是「回呼縫隙」在原始碼裡的樣子，也是自己審這類 bug 的現場。

三種假設鬆動的方式（副作用建模、範圍推理、動態 callback）看了兩種半。下一章正面拆最經典的靜態成因：typer 對數值範圍的推理錯誤——以 Math.expm1 的 `-0` bug 為標本。

→ [Ch 22 — Typer / range-analysis bug](./22-typer-range-analysis-bug.md)
