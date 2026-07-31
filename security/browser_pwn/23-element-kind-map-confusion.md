# Ch 23 — Element-kind confusion / Map transition bug

> **目標**：拆解 type confusion 家族裡**最直接通往 addrof/fakeobj** 的一種——優化器對物件的 **elements kind** 或 **Map** 產生錯誤認知，把 double 陣列當物件陣列（或反過來）。這一章把 [Ch 7](./07-jsarray-elements-kind.md) 的 elements kind 和 [Ch 15](./15-addrof-fakeobj.md) 的兩把鑰匙接起來：你會看到「為什麼 double/object 陣列的混淆幾乎等於直接拿到 addrof」。

> **環境**：V8 15.3.0（candidate）、commit `ab2cad06`、`~/v8build/v8/out/x64.release/d8`。本章的 elements kind / map 佈局用現行 d8 的 `%DebugPrint` 驗證；具體歷史 CVE 的觸發碼為理論分析。

## 為什麼需要這個？

前幾章的 bug（typer、BCE、side-effect）都要繞幾步才拿到原語。elements-kind 混淆最短——因為 V8 用**同一塊記憶體**存兩種語意完全不同的東西：`PACKED_DOUBLE_ELEMENTS` 存的是原始 8-byte 浮點數，`PACKED_ELEMENTS` 存的是壓縮的物件指標。**只要能讓優化器搞混這兩者，你立刻同時擁有 addrof 和 fakeobj**（[Ch 15](./15-addrof-fakeobj.md)）。這是為什麼很多 V8 exploit 的最後一步都是「想辦法做出一個 elements-kind 混淆」。

## 先建立直覺：同一個抽屜，兩種標籤

想像一個抽屜，裡面放著一排 8-byte 的格子。抽屜外貼一張標籤說明「裡面裝的是什麼」：

- 標籤寫 **DOUBLE**：每個格子是一個浮點數，你讀出來就是 `1.1`、`2.2`。
- 標籤寫 **OBJECT**：每個格子是一個**指向物件的指標**，V8 會照指標去找物件。

抽屜（記憶體）一模一樣，差別只在**標籤**（elements kind，記在 Map 裡）。現在如果你能:

- 把一個裝著**物件指標**的抽屜，貼上 **DOUBLE** 標籤去讀 → 你把指標當浮點數讀出來 = **`addrof`**（洩漏物件位址）。
- 把一個裝著**浮點數**的抽屜，貼上 **OBJECT** 標籤去讀 → V8 把你控制的浮點數當指標去解引用 = **`fakeobj`**（憑空造物件）。

```
   記憶體（同一塊）：  [ 0x00002c71010042 f9 ]   ← 這 8 bytes 是什麼？
                              ↑
        標籤 DOUBLE  ─────────┼─────►  讀成 double：一個奇怪的浮點數 → addrof
        標籤 OBJECT  ─────────┴─────►  當指標解引用：跳到那個位址找物件 → fakeobj
```

**elements-kind confusion = 讓 V8 對抽屜貼錯標籤。**

## 底層機制：Map 記著標籤，優化器快取標籤

elements kind 存在物件的 **Map** 裡（[Ch 5](./05-map-hidden-class.md)）。用現行 d8 看兩種抽屜的差別：

double 陣列——elements 是 `FixedDoubleArray`，值是攤平的原始位元：

```
$ d8 --allow-natives-syntax -e 'let a=[1.1,2.2]; %DebugPrint(a);'
 - map: 0x..<Map[16](PACKED_DOUBLE_ELEMENTS)>
 - elements: 0x..<FixedDoubleArray[2]> {
       0: 1.1 (0x3ff199999999999a)   ← 原始 8-byte double
       1: 2.2 (0x400199999999999a)
 }
```

物件陣列——elements 是 `FixedArray`，值是壓縮的 tagged 指標：

```
$ d8 --allow-natives-syntax -e 'let a=[{},{}]; %DebugPrint(a);'
 - map: 0x..<Map[16](PACKED_ELEMENTS)>
 - elements: 0x..<FixedArray[2]> { 0: 0x..<Object>, 1: 0x..<Object> }
```

注意兩者的 `map` 不同（`PACKED_DOUBLE_ELEMENTS` vs `PACKED_ELEMENTS`）——**標籤就記在 map 裡**。

TurboFan 優化一段存取陣列的碼時，會用 `CheckMaps` 節點確認「這個陣列的 map 是我優化時看到的那個」，然後基於那個 map 的 elements kind 決定「用 double 方式還是 object 方式存取」。**bug 出現在：物件的 map 在優化碼不知情時發生了 transition（換標籤），而 `CheckMaps` 被消掉或放錯位置。** 這時優化碼用「舊標籤」的方式，去存取「新標籤」的抽屜——混淆成立。

## Map transition：標籤怎麼被偷換

elements kind 不是固定的，它會**轉換（transition）**（[Ch 7](./07-jsarray-elements-kind.md) 講過「只升不降」）：

```
PACKED_SMI → PACKED_DOUBLE → PACKED_ELEMENTS   (放進 double、放進物件，逐步「泛化」)
```

每次 transition，物件換一個新的 Map。攻擊面在於：

- 一個看似無害的操作（`arr[0] = {}` 把 double 陣列升級成 object 陣列）會**改 map**。
- 如果優化碼已經快取了「這是 DOUBLE 陣列」的假設，而這個 transition 在它背後發生（例如透過 [Ch 21](./21-array-prototype-side-effect.md) 的 callback），優化碼就會用 DOUBLE 方式寫入一個已經是 OBJECT 的抽屜——把攻擊者控制的 double 寫成物件指標欄位 = fakeobj 的原料。

反過來，`CheckMaps` 消除 bug 讓「該重新確認 map 卻沒確認」，效果一樣。

## 為什麼這種混淆「一步到位」

其他 bug 給你的可能是「越界讀一段 double」，你還得自己想辦法把它變成 addrof。elements-kind 混淆**直接就是** double↔指標的互轉，正是 addrof/fakeobj 的定義：

| 你有的混淆 | 直接得到 |
|---|---|
| 把 object 陣列當 double 陣列**讀** | `addrof(obj)`：`arr[i]` 回傳 obj 的位址（當 double） |
| 把 double 陣列當 object 陣列**用** | `fakeobj(addr)`：把你寫的 double 當指標解引用 |
| 兩者都有 | addrof + fakeobj → [Ch 16](./16-fake-object-rw.md) 任意讀寫 |

所以在 CTF 裡，一旦題目給的 primitive 能導向 elements-kind 混淆，剩下就是 [Ch 15](./15-addrof-fakeobj.md)~[Ch 18](./18-oob-to-arbitrary-rw.md) 的公式套用。這也是為什麼本課把 elements kind（Ch 7）放在 Part 1 就講透——它是所有路的匯流點。

## 對比：elements-kind 混淆的幾種來源

| 來源 | 機制 | 相關章 |
|---|---|---|
| **CheckMaps 被消** | 優化器以為 map 不變，省掉重新確認 | Ch 23（本章）、[Ch 20](./20-checkbounds-redundancy-elimination.md) |
| **transition 在 callback 中發生** | `arr[0]={}` 之類在優化碼背後升級 elements kind | [Ch 21](./21-array-prototype-side-effect.md) |
| **副作用模型錯** | 改 map 的操作被當無副作用 | [Ch 19](./19-turbofan-type-confusion.md) |
| **typer 型別窄化錯** | 把可能是物件的值窄化成純數字 | [Ch 22](./22-typer-range-analysis-bug.md) |

看出來了嗎——前面幾章的家族**最後常常都匯流到 elements-kind / map 混淆**這個出海口，因為它是最好用的原語形態。它們是「怎麼騙過優化器」的不同手法，本章是「騙成功之後最甜的那種結果」。

## 踩雷集錦

1. **以為 double 陣列和物件陣列在記憶體裡不同**：它們的 elements backing store 佈局**一樣**（都是一排 8-byte 格子），差別只在 map 記的 elements kind（標籤）。正因為記憶體一樣，混淆才這麼致命。
2. **忘記 elements kind 只升不降**：`arr[0]={}` 把 DOUBLE 升成 OBJECT 後，就算你再 `arr[0]=1.1`，它也回不去 DOUBLE（變成能裝任何東西的 OBJECT）。這個不可逆性影響你構造混淆的順序。
3. **把 map confusion 和 property confusion 混為一談**：本章講的是 **elements**（indexed，`arr[0]`）的 kind 混淆；[Ch 19](./19-turbofan-type-confusion.md) 的 PropertyArray↔NameDictionary 是 **properties**（named，`obj.x`）的混淆。兩條軸（[Ch 6](./06-properties-elements.md)）別搞混。
4. **以為 pointer compression 下 addrof 拿到的是完整位址**：混淆讀出的「指標」是 32-bit 壓縮值（[Ch 4](./04-pointer-compression.md)）。當 double 讀出來時要注意高低位怎麼擺，別誤讀成 64-bit。
5. **以為 fakeobj 憑空造的物件立刻能用**：你得先在某個你控制的記憶體區（通常是一個 double 陣列的 elements）擺好一個**假的 Map + 假的欄位**，fakeobj 指過去才有意義——見 [Ch 16](./16-fake-object-rw.md)。

## 進階：再往深一層

- **`CheckMaps` vs `CheckMapsWithMigration`**：V8 對「可能已 deprecated 的 map」有特殊處理（migration）。這條路徑歷史上出過 bug，值得研究 `src/compiler/` 裡 map check 的各種變體。
- **stable map 假設**：TurboFan 對「這個 map 是 stable（不會再 transition）」有優化。攻擊者讓一個以為 stable 的 map 發生 transition，是一類手法。
- **Map 的 `back pointer` 與 transition tree**：[Ch 5](./05-map-hidden-class.md) 提過 map 用 transition tree 連起來。理解這棵樹能幫你預測「一個操作會把物件帶到哪個 map」，這是精確構造混淆的基礎。
- **Maglev 的 map 處理**：中階 JIT Maglev 也做 map check 與 elements kind 假設，同源 bug 會以不同形式出現。

## 動手練習

1. 用 `%DebugPrint` 完整對照三種陣列：`[1,2]`（SMI）、`[1.1]`（DOUBLE）、`[{}]`（OBJECT）。記下各自的 map 位址和 elements kind 字串。然後 `let a=[1.1]; a[0]={}; %DebugPrint(a)`——觀察 map 怎麼從 DOUBLE transition 到 OBJECT（map 位址變了）。
2. 手算一次 addrof 的位元：用 [Ch 15](./15-addrof-fakeobj.md) 的 `ftoi`，把一個物件的壓縮位址（從 `%DebugPrint` 抄）拼成一個 double，驗證「當 double 讀出來會是什麼樣的浮點數」。體會 addrof 讀出的值為什麼是個「醜浮點數」。
3. 思考題：為什麼攻擊者偏好「把 object 陣列當 double 讀」來做 addrof，而不是直接讀物件？（提示：double 陣列的值不會被 GC 當成指標追蹤，能安全地「攜帶」任意 64-bit 值。）

## 本章重點整理

- V8 用**同一塊記憶體**存 double（原始位元）和物件指標，差別只在 Map 記的 **elements kind（標籤）**。
- 讓優化器對標籤產生錯誤認知 = **elements-kind confusion**，**一步同時給你 addrof（object 當 double 讀）和 fakeobj（double 當 object 用）**。
- 來源是 `CheckMaps` 被消 / transition 在背後發生 / 副作用建模錯——前幾章的家族常**匯流**到這個最好用的原語形態。
- elements kind **只升不降**、混淆讀出的是 **32-bit 壓縮指標**、fakeobj 需先擺好假 Map——三個實作細節別忽略。
- 這是 Part 1（Ch 7 elements kind）與 Part 3（Ch 15 addrof/fakeobj）的匯流點。

## 自我檢核

- [ ] 能解釋為什麼 double 陣列和物件陣列「記憶體一樣、只差標籤」
- [ ] 能說出 elements-kind 混淆怎麼一步給出 addrof 和 fakeobj
- [ ] 能分辨 elements（indexed）kind 混淆和 properties（named）型別混淆
- [ ] 知道混淆讀出的「位址」是壓縮的 32-bit，不是完整 64-bit
- [ ] 面試被問「為什麼 double/object array confusion 這麼好用」，能答「同記憶體佈局 + 直接是指標↔數字互轉 = addrof/fakeobj」

## 延伸閱讀

- **[“Attacking JavaScript Engines” — saelo, Phrack 70:2](https://phrack.org/issues/70/2)**
  - **這篇說什麼**：addrof/fakeobj 原語的原始出處，用 elements-kind 混淆建立這兩把鑰匙的經典文本（雖以 JSC 為例，概念與 V8 完全相通）。
  - **讀哪裡**：addrof/fakeobj 的建構段落。
  - **和本章的關聯**：本章講「混淆從哪來」，這篇講「混淆之後怎麼變原語」，互補。

- **[“Exploiting the Wild” 系列 / doar-e — Jeremy Fetiveau 的 V8 elements kind 相關文章](https://doar-e.github.io/)**
  - **這篇說什麼**：V8 elements kind / map 混淆的實戰分析，多篇以真實 bug 示範。
  - **為什麼值得讀**：Fetiveau 的 V8 系列是 elements-kind 混淆寫得最清楚的來源之一。

- **[V8 `src/objects/elements-kind.h` 與 `src/compiler/` 的 CheckMaps 處理（原始碼）](https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/objects/elements-kind.h)**
  - **讀哪裡**：`elements-kind.h` 看六種 kind 與 transition 規則；compiler 裡 `CheckMaps` 的 lowering。
  - **和本章的關聯**：標籤（elements kind）與標籤檢查（CheckMaps）在原始碼裡的樣子。

四個 type confusion 家族（副作用建模、BCE、callback side-effect、typer 範圍）看完，加上本章的匯流點。下一章拉高一層，談這整類「優化期間世界被改變」的 bug 為什麼**永遠殺不完**——以及 hardening 與攻擊的軍備競賽。

→ [Ch 24 — JIT side-effect 系列](./24-jit-side-effect.md)
