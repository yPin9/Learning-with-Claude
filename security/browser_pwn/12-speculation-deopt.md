# Ch 12 — Speculation 與 Deoptimization

> **目標**：這是 Part 2 的第二個深挖章,也是把前三章(feedback、sea-of-nodes、typing/BCE)收束成一句攻擊格言的地方。你要徹底搞懂:優化器怎麼**下賭注(speculation)**、怎麼用 **guard/assumption** 守衛賭注、賭錯了怎麼透過 **deoptimization(deopt)** 安全退回直譯器,以及那對你已經打過無數次的 `%PrepareFunctionForOptimization` / `%OptimizeFunctionOnNextCall` 儀式**到底在幹嘛、為什麼非它不可**。最後把整個 Part 2 濃縮成一句:**「該 deopt 卻沒 deopt」= type confusion。** 這句話是 Part 4([Ch 19](./19-turbofan-type-confusion.md) 起)每一個漏洞的共同 DNA。

> **環境**：V8 15.3.0(candidate、commit `ab2cad06`)、`~/v8build/v8/out/x64.release/d8`(`disassembler`/`sandbox`/`pointer_compression` 全開)。本章的 `--trace-deopt` deopt reason、`%GetOptimizationStatus` 位元、機器碼裡的 deopt trampoline 都是在這顆 d8 上真跑出來的。

## 為什麼需要這個？

前三章你看到優化器一直在「省檢查」:[Ch 9](./09-parser-ignition-bytecode.md) 的 feedback 是它的情報、[Ch 11](./11-optimization-pipeline.md) 的 BCE 是它省 bounds check、[Ch 10](./10-turbofan-overview.md) 的 `SpeculativeNumberAdd` 是它賭運算元是數字。**但它憑什麼敢省?憑它相信「執行期的世界會維持它優化時假設的樣子」。** 這個「相信 + 押注 + 準備好賭錯的退路」的完整機制,就是本章的 speculation + deopt。

為什麼這是 Part 4 之前的**最後一塊、也最關鍵的一塊拼圖**?因為:

- **deopt 是優化器的安全網**——它敢激進省檢查,正因為它埋了 guard,一旦假設破了就 deopt 退回慢而安全的直譯器。
- **漏洞不是「發生了 deopt」,而是「該 deopt 卻沒 deopt」**——優化器以為某個狀態「不可能發生」,所以**根本沒埋那個 guard**;攻擊者製造出那個「不可能」的狀態,優化過的機器碼就帶著錯誤假設一路執行下去,不 deopt,直接 type confusion。

搞懂 deopt 怎麼運作,你才能精準指出「這個漏洞裡,本該有的那個 guard 為什麼不在」。這是 root cause 分析的終點,也是 Part 4 的起點。

## 先建立直覺:一個押注的賭徒和他的保險

回到 [Ch 2](./02-v8-architecture.md) 的廚神比喻,但這次講清楚保險機制。廚神(優化器)為了快,賭「食材永遠是牛肉」,省掉「檢查是不是牛肉」這步。但他不是莽夫——他在門口放了**一個小小的檢查哨(guard)**:每次出餐前,哨兵瞄一眼食材標籤,只要**還是**牛肉就放行(超快);**萬一標籤變了**(食材被換成豆腐),哨兵拉警報,整個「一鍵出餐」作廢,退回讓服務生(直譯器)重新照食譜慢慢做。這個「作廢 + 退回」就是 **deopt**。

```
   優化過的機器碼(廚神的一鍵出餐):
   ┌──────────────────────────────────────────────┐
   │ guard: 食材標籤還是「牛肉」嗎?                │
   │   是 ──► 照牛肉做法一鍵完成(省掉所有檢查)     │  ← 快
   │   否 ──► DEOPT:作廢,把狀態翻譯回 bytecode 層, │
   │          退回直譯器重跑                        │  ← 安全網
   └──────────────────────────────────────────────┘
```

**deopt 是廚神敢押注的底氣,不是失敗。** 沒有 deopt,優化器就不敢省任何檢查,也就沒有速度。

那漏洞在哪?**漏洞在「廚神以為某種食材不可能出現,於是那道門根本沒放哨兵」。** 比方廚神推理「這道菜的食材來源被我控制死了,絕無可能變成豆腐」,於是**省掉了那個 guard**。攻擊者的全部工作,就是找到一條廚神沒想到的路,把食材偷換成豆腐——現在沒有哨兵、沒有 deopt,豆腐被當牛肉切,**廚房爆炸(type confusion → 記憶體破壞)**。

## speculation:優化器賭的到底是什麼

「speculation(投機/推測優化)」是個大詞,拆開來看優化器具體賭哪些東西。每一種賭注對應一種 guard 節點(你在 [Ch 10](./10-turbofan-overview.md) 看過的 Check 系列):

| 賭注(assumption) | guard 節點 | 賭錯的觸發條件 |
|---|---|---|
| 這個物件的 Map 永遠是 X | `CheckMaps` | 物件換了 hidden class |
| 這個索引永遠在陣列範圍內 | `CheckBounds`(或被 BCE 消掉) | 索引越界 / 陣列被縮短 |
| 這個值永遠是 SMI(小整數) | `CheckSmi` | 值變成 HeapNumber / 物件 |
| 這個加法兩邊永遠是數字 | `SpeculativeNumberAdd` 內含檢查 | 運算元變成物件/字串 |
| 這個屬性存取的 shape 不變 | `CheckMaps` + 依賴 | shape 改變 |
| 這個 prototype chain 沒被動過 | **依賴一個 protector cell** | 有人改了 `Array.prototype` 等 |

前幾種是**局部 guard**(在機器碼裡放一條 cmp + 條件跳 deopt),後面「protector」那種不太一樣,值得單獨講。

### 兩種守衛賭注的方式

**方式一:局部 guard(check node)**。最常見。優化器在會用到假設的地方,**就地放一條檢查**:比一次、不合就 deopt。[Ch 11](./11-optimization-pipeline.md) 反組譯裡的 `cmpl [rcx-0x1],map` + `jnz deopt`(CheckMaps)、`cmpq index,length` + `jnc deopt`(CheckBounds)都是。**成本:每次執行都比一次。** 好處:哪怕假設只在執行時偶爾破,也能即時抓到。

**方式二:code dependency(全域假設 + 遠端引爆)**。對某些「全域性、幾乎不會變」的假設(例如「沒人改過 `Array.prototype`」、「沒人給 Array 加過 index getter」),每次存取都 check 太貴。V8 改用另一招:優化這份機器碼時,**在對應的『全域假設物件』上登記一筆依賴(dependency)**。這份機器碼裡**根本不放 check**(所以更快);但如果哪天真的有人改了那個全域假設,V8 會**主動把所有依賴它的優化機器碼作廢**(這叫 deoptimize dependent code)。

這兩種的**安全語意完全不同,也各有各的攻擊面**:

- 局部 guard 漏掉 → 某個 check 該放沒放(BCE 消錯、Map check 消錯),Part 4 主線。
- code dependency 漏掉 → **某個全域假設沒被正確登記依賴**,於是攻擊者改了那個全域假設卻沒觸發作廢,優化機器碼帶著過時的全域假設繼續跑。著名的 `Array.prototype` protector / `no_elements_protector` 相關 bug 就是這類。

**protector cell** 是 code dependency 的具體實現:V8 有一堆 cell(如 `ArraySpeciesProtector`、`NoElementsProtector`),平時是「valid」狀態,優化器信任它;一旦有人做了會使假設失效的事(如給 `Array.prototype` 加屬性),對應 cell 被設為「invalid」,連鎖作廢所有依賴的優化碼。**「protector 該失效卻沒失效」是另一種『該 deopt 卻沒 deopt』**,和局部 guard 那種對稱。

## deoptimization:賭錯了,怎麼安全退回

假設 `CheckMaps` 抓到 Map 變了,觸發 deopt。deopt 要做的事比你想的難:優化過的機器碼**用的暫存器佈局、堆疊佈局和直譯器完全不同**(浮點在 xmm、整數拆成 int32、物件可能因 escape analysis 根本沒配置)。deopt 必須把「優化世界的執行狀態」**翻譯**回「直譯器世界能接手的狀態」(bytecode offset、accumulator、register file),然後跳進直譯器繼續跑。這個翻譯靠優化時一併產生的 **deopt metadata**(每個 deopt 點記著「當時每個直譯器 register 對應優化世界的哪個位置」)。

親眼看一次 deopt。故意讓優化器賭「`a` 的 Map 是只有 `x` 的那種」,優化後餵它一個多一個 `y` 的物件(不同 hidden class),打破 Map 賭注(真跑):

```
$ cat deopt.js
function foo(a) { return a.x + 1; }
let o1 = {x: 1};
%PrepareFunctionForOptimization(foo);
foo(o1); foo(o1);
%OptimizeFunctionOnNextCall(foo);
foo(o1);
let o2 = {x: 2, y: 3};   // 不同 hidden class!
foo(o2);                 // 打破 Map 賭注 → 應 deopt
foo(o1);

$ d8 --allow-natives-syntax --trace-deopt deopt.js
[bailout (kind: deopt-eager, reason: wrong map): begin. deoptimizing
 0x...<JSFunction foo>, <Code TURBOFAN_JS>, opt id 0, bytecode offset 0,
 deopt exit 1, FP to SP delta 32, ...]
```

逐格讀這行 trace(這是你 triage 漏洞時天天看的東西):

- **`kind: deopt-eager`**:eager deopt——**主動式**,由機器碼裡的 guard(`CheckMaps`)當場觸發。另一種是 `lazy deopt`,見下。
- **`reason: wrong map`**:deopt 的原因——Map 賭錯了。這正是我們 `o2` 的不同 hidden class 觸發的。其他常見 reason:`out of bounds`([Ch 11](./11-optimization-pipeline.md) 見過)、`not a smi`、`wrong instance type`、`insufficient type feedback`。
- **`deoptimizing <JSFunction foo>, <Code TURBOFAN_JS>`**:哪個函式的哪份優化碼被作廢。
- **`bytecode offset 0`**:退回直譯器後從 bytecode 的哪個位置接手。
- **`FP to SP delta 32`**:堆疊 frame 的調整量——deopt 要重建直譯器的 frame。

deopt 完,`foo` 退回用直譯器跑,feedback 也會更新(現在知道 `foo` 會收到兩種 shape,下次優化會賭 polymorphic 或更保守)。**整個過程對 JS 程式完全透明,結果一模一樣,只是慢了一下下。這就是安全網正常運作的樣子。**

### eager deopt vs lazy deopt

- **eager deopt**:guard 當場發現假設破了,立刻 deopt(上面 `wrong map` 就是)。「執行到 guard 那條指令時」觸發。
- **lazy deopt**:一份優化碼因為**別處**發生的事而需要作廢,但它此刻**正在執行(在 stack 上)**,不能立刻抽掉。V8 把它標記為「作廢」,等它這次執行返回到某個安全點時才真的替換。常見於 code dependency 觸發(某人改了全域假設,所有依賴的碼要作廢,但其中有一份正在跑)。

**lazy deopt 的時間窗是一類 side-effect 漏洞的溫床**:「機器碼已被標記作廢、但還沒真的被替換」的那一小段,如果攻擊者能在裡面做點手腳,可能造成不一致。這連到下面的併發/side-effect 主題。

## 那對儀式:`%PrepareFunctionForOptimization` / `%OptimizeFunctionOnNextCall`

你從 [Ch 0](./00-environment-setup.md) 就在敲這兩行,現在講清楚它們為什麼存在、為什麼順序不能錯。

正常情況下,函式要被呼叫**幾千次**變「熱」才會自動觸發優化——這在寫 PoC/exploit 時太慢、也太不可控。V8 提供 intrinsic 讓你**手動**強制優化。但這裡有個微妙的相依:

```
%PrepareFunctionForOptimization(f);   // 儀式第一步
f(1); f(2);                           // 跑幾次,餵 feedback
%OptimizeFunctionOnNextCall(f);       // 儀式第二步:下次呼叫時優化
f(3);                                 // ← 這次呼叫觸發優化
```

- **`%PrepareFunctionForOptimization(f)`**:**強制配置 feedback vector 並開始收集 feedback**。回想 [Ch 9](./09-parser-ignition-bytecode.md):函式剛定義時只有空的 closure feedback cell array,沒有真正的 FV。沒有 FV,優化器就沒有情報可賭,優化沒意義甚至報錯。`Prepare` 就是把 FV 準備好、打開收集開關。
- **中間的 `f(1); f(2);`**:**實際餵 feedback**。這幾次呼叫用什麼型別的參數,直接決定優化器將來賭什麼。**你在這裡餵什麼,就是在替優化器佈置賭注**——這正是 Part 4 型別污染的操作點:先用「乾淨」的型別餵飽 feedback 讓優化器樂觀地賭,優化後再偷換。
- **`%OptimizeFunctionOnNextCall(f)`**:標記「下次呼叫 `f` 時,同步編譯優化版」。注意 trace 裡是 `ConcurrencyMode::kSynchronous`(手動優化通常同步,方便你控制時序)。

**為什麼順序不能顛倒、為什麼少了 `Prepare` 會壞?** 因為 `%OptimizeFunctionOnNextCall` 假設 feedback vector 已存在且有內容。少了 `Prepare`,新版 V8 會報錯或不生效——這是新手照 2019 舊 writeup(那時不需要 `Prepare`)最常撞的牆。**這對儀式是「手動、可控地把一個函式推上 TurboFan」的標準流程,是每個 V8 PoC 的開場白。**

### 用 `%GetOptimizationStatus` 確認賭注狀態

怎麼確認函式「現在到底優化了沒、有沒有 deopt 掉」?用 `%GetOptimizationStatus(f)`,它回傳一個 bitmask。跑一輪完整生命週期(真跑):

```
$ cat status.js
function f(x){ return x + 1; }
function pr(){ print("status = 0x" + (%GetOptimizationStatus(f)).toString(16)); }
%PrepareFunctionForOptimization(f);
f(1); f(2);
pr();                              // 優化前
%OptimizeFunctionOnNextCall(f);
f(3);
pr();                              // 優化後
f("str");                          // 字串參數 → 打破 SMI 賭注 → deopt
pr();                              // deopt 後

$ d8 --allow-natives-syntax status.js
status = 0x41
status = 0x29
status = 0x41
```

解讀這三個值(bitmask,每個 bit 一個狀態,常見 bit:`kIsFunction`、`kMaybeDeopted`、`kOptimized`、`kTurboFanned`、`kInterpreted`…):

- **`0x41`(優化前)**:函式存在 + 目前在直譯器跑(還沒優化)。
- **`0x29`(優化後)**:**bit 變了**——`0x29` 帶著「已被 TurboFan 優化」的位。這確認 `f` 成功升上 TurboFan。
- **`0x41`(deopt 後)**:**變回和優化前一樣**——`f("str")` 用字串打破了「`x` 是 SMI」的賭注,觸發 deopt,`f` 退回直譯器。狀態位回到「未優化」。

**這三個數字 `0x41 → 0x29 → 0x41` 就是一次完整的「優化 → 賭錯 → deopt 退回」的生命週期指紋。** 你在寫 exploit 時會反覆用 `%GetOptimizationStatus` 確認「我的函式現在到底在哪個狀態」——尤其要確認「我做完壞事後,它**還在**優化狀態(沒 deopt)」,因為你的攻擊往往依賴「優化過的機器碼帶著錯誤假設繼續執行」。**如果你做壞事後它 deopt 了,通常代表你的 type confusion 沒成功(guard 抓到了)。**

> **踩雷**:`%GetOptimizationStatus` 的 bit 定義會隨 V8 版本變,別背 `0x29` 這個具體數字。要看的是**「這個 bit 有沒有翻」**——優化前後某個 bit 亮起、deopt 後熄滅。要精確解讀某個 bit,查你這版 V8 的 `src/runtime/runtime-test.cc` 裡 `OptimizationStatus` 的 enum。

## 核心:「該 deopt 卻沒 deopt」= type confusion

現在把整個 Part 2 收束成一句話。優化器的安全,建立在一個**閉環**上:

```
   優化器賭一個假設  →  埋一個 guard(局部 check 或 code dependency)
        ▲                              │
        │                              ▼
   假設維持:快速執行  ◄─── guard 每次確認假設還成立 ───► 假設破:deopt 退回(安全)
```

這個閉環**正常時無懈可擊**。漏洞永遠出在**閉環破了一個口**——具體有三種破法,全都是「該 deopt 卻沒 deopt」的變體:

1. **guard 根本沒埋(missing check)**:優化器基於錯誤推理(污染的 feedback、typer 的 range off-by-one、被誤消的 CheckBounds/CheckMaps)認定「這個假設不可能破」,於是**沒放 guard**。攻擊者製造出那個「不可能」的狀態,沒有 guard → 不 deopt → type confusion。([Ch 11](./11-optimization-pipeline.md) 的 BCE 誤消是這類。)

2. **guard 埋錯位置(side-effect 漏過)**:guard 埋了,但攻擊者能在「guard 檢查完」和「實際使用」之間,透過一個**優化器以為無副作用**的操作(callback、`valueOf`、getter、型別轉換)偷改世界。guard 過了、世界變了、使用時已錯——這是 [Ch 10](./10-turbofan-overview.md) 的 **effect edge 漏洞**,根源是 effect chain 沒把那個副作用串進去。

3. **code dependency 沒登記(protector 沒失效)**:某個全域假設該登記依賴卻沒登記,攻擊者改了全域假設但沒觸發作廢,優化碼帶著過時的全域假設繼續跑。(protector 相關 bug。)

**三種都是同一句話:優化器該撤銷這份機器碼(deopt/作廢)卻沒撤銷,於是機器碼帶著已經失真的假設,對記憶體做出基於錯誤型別的存取。** 這就是 type confusion 的完整定義,也是 Part 4 每一章的共同骨架。

### 併發優化:額外的時間維度

[Ch 2](./02-v8-architecture.md) 提過優化是 `ConcurrencyMode::kConcurrent`——**背景執行緒編譯,主執行緒繼續跑 JS**。這給「該 deopt 卻沒 deopt」加了一個時間維度:背景執行緒**優化時看到的世界**,和它**編譯完裝上去時的世界**,中間可能被主執行緒改過。如果背景執行緒基於「優化開始那一刻的 feedback/Map」下了賭注,而主執行緒在編譯期間偷改了那個 Map,又沒有機制讓背景執行緒察覺——裝上去的機器碼一出生就帶著過時假設。這類「concurrent compilation race」是較進階、較新的攻擊面,Part 4 後段會碰。手動 `%OptimizeFunctionOnNextCall` 是同步的(`kSynchronous`),避開了這個窗,但真實 Chrome 是併發的。

## 對比:deopt vs 例外 vs C 的 UB

| 面向 | JS 例外(throw) | V8 deopt | C 的 UB(如 OOB) |
|---|---|---|---|
| 誰觸發 | 程式邏輯 | guard 發現假設破 | 沒人——直接壞 |
| 對程式可見嗎 | 可見(catch) | **完全透明** | 不可見(靜靜地錯) |
| 是不是錯誤 | 是(程式層) | **不是,是正常機制** | 是(記憶體層) |
| 對利用的意義 | 邏輯 | **「該 deopt 卻沒 deopt」才是漏洞** | 記憶體破壞本身 |

最容易搞混的是把 deopt 當「錯誤」。**deopt 是優化器的正常呼吸,發生一億次都不是 bug。** 危險的是它**該發生而沒發生**——那一刻,V8 的安全模型從「JIT 的動態假設」退化成「C 的 UB」:一個帶著錯誤型別的記憶體存取,靜靜地讀寫了不該碰的地方。V8 pwn 的本質,就是把「JIT 的錯誤假設」轉化成「C 級別的記憶體破壞」。

## 踩雷集錦

1. **錯誤直覺:「deopt 是錯誤/失敗,exploit 要避免 deopt」**。正確:deopt 是正常安全網。你要避免的不是「deopt 這件事」,而是「**在做完壞事後**觸發 deopt」——那通常代表 guard 抓到你了、type confusion 沒成。做壞事**前**的 deopt 無所謂。

2. **錯誤直覺:「漏洞是優化器產生了 deopt」**。正確:漏洞是**該 deopt 卻沒 deopt**——guard 沒埋、埋錯位置、或全域依賴沒登記。找 bug 要找「本該存在的那個 guard 為什麼不在」。

3. **錯誤直覺:「`%OptimizeFunctionOnNextCall` 自己就能優化」**。正確:新版 V8 **必須先 `%PrepareFunctionForOptimization`**(配置 feedback vector),否則報錯/不生效。少了 `Prepare` 那行是照舊 writeup 最常見的坑。

4. **錯誤直覺:「中間的 `f(1); f(2)` 只是湊次數」**。正確:那幾次呼叫是**實際餵 feedback**——用什麼型別餵,決定優化器賭什麼。這正是型別污染的操作點,不是可有可無的湊數。

5. **錯誤直覺:「所有賭注都靠就地 check 守衛」**。正確:有兩種——局部 guard(就地 cmp+deopt)和 code dependency(全域假設 + 遠端作廢/protector)。後者的機器碼裡**根本沒 check**,靠「改了全域假設就作廢依賴碼」守衛。兩種各有各的漏法。

6. **錯誤直覺:「`%GetOptimizationStatus` 的 `0x29` 之類數字可以照抄判斷」**。正確:bit 定義隨版本變,別背數字。看的是「某個 bit 優化後亮、deopt 後熄」的**變化**,精確解讀查你這版的 `runtime-test.cc`。

## 進階:再往深一層

- **`--trace-deopt-verbose` / `--print-deopt-stress`**:更詳細的 deopt 資訊、以及「隨機強制 deopt」的壓力測試模式。後者能幫你發現「某段碼在被隨機 deopt 時狀態不一致」的 bug,是 fuzzing/triage 的輔助。
- **deopt metadata 的結構**:每個 deopt 點的「優化世界 → 直譯器世界」對應表,存在優化碼的 `DeoptimizationData` 裡([Ch 11](./11-optimization-pipeline.md) 反組譯末尾的 `Deoptimization Input Data (deopt points = 12)` 就是它)。理解它有助於分析「deopt 後狀態被重建成什麼」,某些漏洞就藏在重建的錯誤裡。
- **protector cell 全清單**:`src/objects/` 裡 `Protectors` 定義了 `ArraySpeciesProtector`、`NoElementsProtector`、`ArrayIteratorProtector` 等。每個 protector 保護一個「幾乎不變的全域假設」,對應一類「改了 prototype/加了 index getter 卻沒觸發 protector 失效」的歷史 bug。Part 4 的某些 exploit 直接玩 protector。
- **soft deopt vs hard deopt / deopt loop**:反覆優化又反覆 deopt(deopt loop)是效能病也是分析線索。V8 有機制在一個函式 deopt 太多次後「放棄優化它」。理解這個對「為什麼我的函式優化不起來/一優化就 deopt」的除錯很有用。
- **`%DeoptimizeFunction` / `%NeverOptimizeFunction`**:手動 deopt 或禁止優化某函式,寫 PoC 控制時序時偶爾用到。

## 動手練習

1. 重跑本章的 `deopt.js`(Map 賭注)和一個 SMI 版本(`function g(x){return x+1}` 優化後餵字串),都開 `--trace-deopt`,對比兩者的 `reason`(一個 `wrong map`、一個應是 `not a smi` 之類)。把「不同賭注破掉 → 不同 deopt reason」看清楚。
2. 跑本章的 `status.js`,確認你的 V8 上 `%GetOptimizationStatus` 的三個值(優化前/後/deopt 後)。**別在意具體數字,標出哪個 bit 翻了**。查你這版 `runtime-test.cc` 的 `OptimizationStatus` enum,把翻掉的那個 bit 對到名字(應該是 TurboFan/Optimized 相關)。
3. 故意寫錯儀式:只 `%OptimizeFunctionOnNextCall(f)` **不加** `%PrepareFunctionForOptimization`,看 V8 報什麼錯/是否不生效。親手撞一次這個坑,以後看到別人 PoC 少那行你就知道會壞。
4. 寫一個函式,優化後做一件「你以為不會 deopt」的事(例如給參數物件加一個新屬性改變它 Map),用 `%GetOptimizationStatus` 確認它**確實 deopt** 了。這是在體會「guard 正常運作 = 你的假設破壞被抓到」;Part 4 你要做的是反過來——讓破壞**不被**抓到。

## 本章重點整理

- **speculation** = 優化器基於 feedback 下賭注(Map/bounds/SMI/型別),每個賭注配一個守衛。
- 守衛有兩種:**局部 guard**(就地 cmp+deopt,如 `CheckMaps`/`CheckBounds`)和 **code dependency / protector**(機器碼裡不放 check,靠「改了全域假設就作廢依賴碼」)。
- **deopt** 是賭錯時把「優化世界的狀態」翻譯回「直譯器世界」的安全退回,對 JS 完全透明,**是正常機制不是錯誤**。`--trace-deopt` 看得到 `reason`(`wrong map`/`out of bounds`/`not a smi`…)。
- 儀式 **`%PrepareFunctionForOptimization`(配置 FV + 收集)→ 餵 feedback → `%OptimizeFunctionOnNextCall`(下次呼叫優化)**,順序不能錯;`%GetOptimizationStatus` 用 bit 確認狀態(優化前 → 後某 bit 翻 → deopt 後翻回)。
- **核心格言:「該 deopt 卻沒 deopt」= type confusion。** 三種破法:guard 沒埋(missing check)、guard 埋錯位置(side-effect 漏過 effect chain)、code dependency 沒登記(protector 沒失效)——全是 Part 4 的共同骨架。

## 自我檢核

- [ ] 能解釋 speculation 和 deopt 是「押注 + 安全網」的一體兩面,deopt 為什麼是優化器敢激進的底氣
- [ ] 能區分「局部 guard」和「code dependency / protector」兩種守衛方式,並說出各自的漏法
- [ ] 能讀懂 `--trace-deopt` 的一行,指出 `kind`(eager/lazy)和 `reason`
- [ ] 能解釋那對儀式每一步在幹嘛,以及為什麼少了 `Prepare` 會壞、中間的呼叫為什麼是餵 feedback
- [ ] 能用 `%GetOptimizationStatus` 判斷函式現在優化了沒、有沒有 deopt(看 bit 變化)
- [ ] 能把「該 deopt 卻沒 deopt」的三種破法各舉一例,並對應回 Part 2 前幾章的機制
- [ ] **面試題**:為什麼說「deopt 發生一億次都不是 bug,不發生一次才可能是」?(答:deopt 是 guard 正常抓到假設破壞的表現;漏洞是 guard 該存在卻不存在/該觸發卻沒觸發,使優化碼帶著失真假設對記憶體做基於錯誤型別的存取,即 type confusion。)

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

### 官方文件 / 部落格

- **[Benedikt Meurer, “An Introduction to Speculative Optimization in V8”](https://benediktmeurer.de/2017/12/13/an-introduction-to-speculative-optimization-in-v8/)**
  - **這篇說什麼**:speculation + deopt 的第一手權威解釋,作者是前 TurboFan 負責人。本章「賭徒 + 保險」比喻的正式版。
  - **讀哪裡**:整篇。特別是 guard 與 deopt 那幾段,和本章的 `--trace-deopt` 實例對讀。
  - **關聯**:這是 Part 2 → Part 4 心智模型的官方基石,讀完本章後看會全部串起來。

### 攻擊視角

- **[saelo, “Exploiting Logic Bugs in JavaScript JIT Engines”](https://saelo.github.io/papers/)**
  - **這篇說什麼**:把「該 deopt 卻沒 deopt」系統化成可利用的 bug 類別,含 missing check 與 side-effect 兩大類的實例。
  - **讀哪裡**:speculation guard / redundancy 相關段落,正是本章三種破法的完整版。
  - **關聯**:本章的格言在這裡變成一整套方法論,是 Part 4 的直接前導。

- **[Project Zero, “The Great DOM Fuzz-off” 之外——搜尋其 V8 TurboFan side-effect / protector bug writeup](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**:真實的 side-effect(callback 偷改世界)與 protector 未失效的漏洞案例。
  - **讀哪裡**:root cause 段落,對照本章「guard 埋錯位置」和「code dependency 沒登記」兩種破法。
  - **關聯**:把本章抽象的三種破法各配一個 CVE 級別的真實案例。

### 原始碼

- **V8 `src/deoptimizer/deoptimizer.cc`、`src/compiler/` 的 checkpoint/dependency 相關、`src/objects/` 的 `Protectors`**
  - **讀哪裡**:先看 `Protectors` 有哪些 protector cell(對照本章的 code dependency 那節);`deoptimizer.cc` 看 deopt 怎麼重建直譯器 frame(對照 deopt metadata 那節)。
  - **關聯**:本章的機制在原始碼裡的落點。想確認某個 protector 保護什麼假設,讀這裡最快。

- **V8 `src/runtime/runtime-test.cc`——`OptimizationStatus`**
  - **讀哪裡**:`OptimizationStatus` 的 bit 定義,把本章 `%GetOptimizationStatus` 回傳值的每個 bit 對到名字。
  - **關聯**:解讀你自己 d8 上那三個十六進位數字的權威依據(別背數字,查這裡)。

到這裡,Part 2 的「執行管線」已經把 Parser、Ignition、TurboFan 的優化與 deopt 全部深挖完了——你已經有能力指著一份 IR 或反組譯,說出「這個 guard 該在哪、如果它不在會怎樣」。Part 2 最後一章換個角度:所有這些物件活在 V8 的堆上,由 GC 管生死。GC 怎麼搬物件、怎麼分代、怎麼影響你 spray 的佈局和位址穩定性——這是把「機制知識」落地成「exploit 佈局」的最後一塊。

→ [Ch 13 — GC(Orinoco)與對利用的影響](./13-garbage-collection.md)
