# Ch 10 — TurboFan 概論：sea-of-nodes IR

> **目標**：把 TurboFan 從 [Ch 2](./02-v8-architecture.md) 的「最激進的優化器」升級成「你能看懂它內部長什麼樣」。這章要你理解 **sea-of-nodes** 這種 IR 為什麼長那樣（control/effect/value 三種邊）、node 有哪些大類、怎麼用 `--trace-turbo` 產出 IR 的 JSON、怎麼用 turbolizer 把它畫出來,以及優化 pipeline 有哪些階段。你不需要記住每個 node,但你要建立「TurboFan 的世界是一張圖,漏洞是圖上某個節點的型別推理錯了」這個心智模型——因為 Part 4 講 type confusion 時,會直接指著這張圖上的某個節點說「這裡壞了」。

> **環境**：V8 15.3.0（candidate、commit `ab2cad06`）、`~/v8build/v8/out/x64.release/d8`（`disassembler`/`sandbox`/`pointer_compression` 全開）。本章的 pipeline phase 清單、node opcode、`turbo-*.json` 結構都是在這顆 d8 上用 `--trace-turbo` 真跑出來的。turbolizer 是需要瀏覽器的 GUI 工具,本章描述其用法並明確標註**未實測(GUI 工具)**,但 `--trace-turbo` 有沒有產出 JSON 是可驗證的,已驗。

## 為什麼需要這個？

到 [Ch 9](./09-parser-ignition-bytecode.md) 為止,你手上有「bytecode + feedback」——優化器的**輸入**。從這章開始進優化器內部。

為什麼要看得懂 TurboFan 的 IR?因為 **V8 pwn 的主礦脈(type confusion)幾乎都是「TurboFan 對某個節點的型別推理犯錯」**。要看懂一個 CVE 的 root cause,你得能讀懂那份 writeup 貼的 sea-of-nodes 圖、能自己 `--trace-turbo` 重現、能指出「這個 `CheckBounds` 節點本該存在卻被消掉了」或「這個 `LoadElement` 的型別被推成錯的了」。不會讀 IR,你只能背 exploit,不能理解 bug。

這章不深入任何一個具體優化(那是 [Ch 11](./11-optimization-pipeline.md) 的事),只做三件事:**把 sea-of-nodes 的三種邊講清楚、把 pipeline 的階段列出來、把看 IR 的工具鏈架起來**。

## 先建立直覺：把程式碼打散成一鍋「節點的海」

你在 `compilers` 課看過的 IR(三位址碼、CFG + basic block)是**線性的**:指令一條接一條,分支用 basic block 串。TurboFan 不這樣。它把程式**打散成一堆節點,節點之間只靠「依賴關係」連接**,沒有「這條在那條之前」的固有順序——只有「這個節點的輸入需要那個節點的輸出」這種資料依賴,和少數必要的順序約束。這就是 **sea-of-nodes**:節點漂在一片海裡,靠邊(edge)彼此牽連。

為什麼要這樣?**因為「不強加不必要的順序」給了優化器最大的自由**。傳統 IR 裡 `a = x+y; b = p+q;` 這兩條有先後,即使它們毫無關係;優化器要重排得先證明重排安全。sea-of-nodes 裡它們就是兩個獨立的加法節點,誰先誰後由排程(scheduling)階段最後才決定。優化器可以自由地把節點搬來搬去、合併、刪除,只要不破壞邊表達的依賴。

```
   傳統 IR(線性):          sea-of-nodes(圖):
   ┌────────────┐              [Start]
   │ 1: t = x+y │                │ (control)
   │ 2: b = p+q │          ┌─────┴─────┐
   │ 3: r = t*b │       [x]  [y]     [p]  [q]
   └────────────┘         └─┬─┘        └─┬─┘
   順序寫死                 [Add]       [Add]      ← 兩個加法無先後
                             └────┬──────┘
                               [Mul]              ← 只有資料依賴牽著它
                                 │
                              [Return]
```

代價是這種圖對人腦不直覺——一堆節點加一堆箭頭。所以才需要 turbolizer 把它畫出來。**你要習慣的心智轉換:別再想「第幾行」,改想「哪個節點的輸出餵給哪個節點」**。

## sea-of-nodes 的三種邊

這是本章最重要的一節。sea-of-nodes 的節點之間有**三種不同顏色的邊**,搞懂它們才看得懂 IR,也才看得懂漏洞為什麼發生。

### 1. value edge(值邊)——「我需要你算出來的值」

最直覺的一種。`[Mul]` 需要兩個 `[Add]` 的結果當輸入,就有兩條 value edge 指向它們。這表達**資料流(data flow)**:哪個值是哪個計算的輸入。turbolizer 裡通常畫成細的黑線。

### 2. control edge(控制邊)——「執行流從我這裡經過」

表達**控制流(control flow)**:哪些節點在「執行路徑」上、分支怎麼走、迴圈怎麼繞。`[Start]`、`[Branch]`、`[Merge]`、`[Loop]`、`[Return]`、`[End]` 這些是控制節點,control edge 把它們串成一張控制流圖。這相當於傳統 IR 的 CFG,但融進了同一張圖。turbolizer 裡通常畫成紅色粗線。

### 3. effect edge(效果邊)——「副作用的先後順序」

**這是 sea-of-nodes 最巧妙、也最和安全相關的一種邊**。問題是這樣:純計算(`x+y`)沒有副作用,誰先誰後無所謂;但**讀寫記憶體有副作用**——「讀 `o.x`」和「寫 `o.x = 5`」的先後**不能亂調**,否則讀到錯的值。

sea-of-nodes 用 **effect edge** 串起所有「有副作用的節點」成一條(或多條)鏈,強制它們維持正確的先後。一個 `LoadField`(讀屬性)、`StoreField`(寫屬性)、`Call`(呼叫可能改變世界的函式)都掛在 effect chain 上。turbolizer 裡通常畫成藍色虛線。

**為什麼 effect edge 和漏洞高度相關?** 因為 type confusion 的一大來源就是「優化器誤判某個操作**沒有副作用**、於是把它從 effect chain 上鬆綁、允許重排或消除,但它其實會改變世界」。舉例:優化器以為某個 callback 不會改變某個陣列的 Map(所以把 Map check 提前或消掉),但那個 callback 偷偷改了——effect 依賴被漏掉,就是 bug。[Ch 12](./12-speculation-deopt.md) 的 side-effect 類漏洞,根源就在 effect edge 上的錯誤。

```
   effect chain 示意(藍色虛線):

   [Start:effect] ┈┈► [CheckMaps] ┈┈► [LoadField o.x] ┈┈► [Call cb()] ┈┈► [LoadField o.x] ┈┈► ...
                        (賭 Map)         (讀屬性)          (可能改世界)      (再讀,依賴前面)

   漏洞:如果優化器誤判 [Call cb()] 無副作用,把它從鏈上摘掉,
        或把 [CheckMaps] 移到 [Call] 之後,cb() 改了 Map 卻沒被重新檢查 → type confusion
```

**一句話記住三種邊**:value = 「誰是誰的輸入」、control = 「執行流怎麼走」、effect = 「副作用的先後不能亂」。看 IR 時每條箭頭先問自己是哪一種。

## node 的大類

TurboFan 的節點有好幾百種 opcode,但可以分成幾個層次。這個分層對應優化 pipeline「逐步 lowering(降階)」的過程——高階節點慢慢被換成低階節點,越往後越接近機器碼。

| 層次 | 代表節點 | 意義 |
|---|---|---|
| **通用/控制** | `Start`、`End`、`Branch`、`Merge`、`Loop`、`IfTrue`、`IfFalse`、`Return`、`Phi`、`EffectPhi` | 控制流骨架。`Phi` 合併不同分支來的值,`EffectPhi` 合併 effect chain |
| **JS 層(最高階)** | `JSCall`、`JSLoadProperty`、`JSAdd`、`JSLoadNamed` | 直接對應 JS 語意的操作,還沒展開成細節。帶 `JS` 前綴 |
| **Simplified(中階)** | `LoadField`、`StoreField`、`LoadElement`、**`CheckBounds`**、**`CheckMaps`**、`SpeculativeNumberAdd`、`NumberAdd`、`Checkpoint` | JS 操作被 lowering 成「型別化但還不是機器指令」的操作。**這一層是 type confusion 的主戰場**——`CheckBounds`/`CheckMaps` 這些「守衛」節點就活在這 |
| **Machine(最低階)** | `Load`、`Store`、`Int64Add`、`Word32And`、`Word64Shl` | 幾乎一對一對應機器指令,排程後就變組合語言 |

你要特別盯住 **Simplified 層的「Check 系列」節點**:

- **`CheckMaps`**:守衛「這個物件的 Map 是我賭的那個」。賭錯 → deopt。這是 [Ch 12](./12-speculation-deopt.md) 的 Map check。
- **`CheckBounds`**:守衛「這個索引在陣列長度範圍內」。這是 [Ch 11](./11-optimization-pipeline.md) 的 **bounds-check elimination** 的主角——優化器如果**證明**索引一定在範圍內,就把這個 `CheckBounds` 刪掉。而「該證明失敗卻誤判成功、把 `CheckBounds` 錯誤消除」,就是 OOB 讀寫漏洞的直接根源。

**記住這句貫穿 Part 4 的話:漏洞通常不是「多了一個壞節點」,而是「少了一個 Check 節點」——一個本該守衛的 `CheckBounds`/`CheckMaps` 被優化器判定為多餘而消掉了。**

## 用 `--trace-turbo` 把 IR dump 出來

理論夠了,實際看。`--trace-turbo` 會把 TurboFan 優化某函式的**每個 pipeline 階段的 IR**,寫成一個 `turbo-<函式名>-<id>.json`。拿 [Ch 11](./11-optimization-pipeline.md) 會細講的這個函式來跑:

```
$ cat bce.js
function load(arr, i) { return arr[i] + arr[i + 1]; }
let a = [1.1, 2.2, 3.3, 4.4, 5.5];
%PrepareFunctionForOptimization(load);
load(a, 0); load(a, 1);
%OptimizeFunctionOnNextCall(load);
load(a, 0);

$ d8 --allow-natives-syntax --trace-turbo bce.js
$ ls -la turbo-*.json
-rw-r--r-- 1 ypp ypp 692618 ... turbo-load-0.json     ← 產出了,約 692 KB
```

（真跑:確實產出 `turbo-load-0.json`。）這個 JSON 裡有兩塊:`function`(原始碼資訊)和 `phases`(每個優化階段一份 IR 快照)。看它的頂層結構(真跑節錄):

```json
{"function":{ "functionName":"load", "sourceText":"(arr, i) {\n  return arr[i] + arr[i + 1];\n}", ...},
 "phases":[
   {"name":"V8.TFBytecodeGraphBuilder","type":"graph","data":{
     "nodes":[
       {"id":31,"label":"End","opcode":"End","control":true,
        "opinfo":"0 v 0 eff 1 ctrl in, 0 v 0 eff 0 ctrl out", ...},
       {"id":30,"label":"Return","opcode":"Return","control":true, ...},
       ...
```

逐格讀一個節點:

- **`"opcode":"End"`**:節點種類(對應上面的大類表)。
- **`"control":true`**:這是個控制節點(在 control edge 上)。
- **`"opinfo":"0 v 0 eff 1 ctrl in, 0 v 0 eff 0 ctrl out"`**:**這一欄直接告訴你三種邊的度數**——`v`=value、`eff`=effect、`ctrl`=control。`End` 有 0 個 value、0 個 effect、1 個 control 輸入。這正是三種邊的實體證據:**每個節點都明確記著它有幾條 value/effect/control 邊進出**。

用 grep 統計這個函式圖裡有哪些關鍵節點(真跑):

```
$ grep -oE '"opcode":"(CheckBounds|CheckMaps|LoadElement|LoadField|SpeculativeNumberAdd|Checkpoint)"' turbo-load-0.json | sort | uniq -c
     14 "opcode":"CheckBounds"
     15 "opcode":"CheckMaps"
     13 "opcode":"Checkpoint"
     20 "opcode":"LoadElement"
     30 "opcode":"LoadField"
      4 "opcode":"SpeculativeNumberAdd"
```

（這些數字是「所有階段加總」的出現次數,不是單一階段的節點數——JSON 每個 phase 都存一份圖,所以同一個 `CheckBounds` 在多個階段各算一次。）重點是你看到了 `CheckBounds`、`CheckMaps`——那個 `arr[i]` 觸發了 bounds check 和 map check 節點,`arr[i] + arr[i+1]` 裡的 `+` 因為兩邊是浮點而變成 `SpeculativeNumberAdd`。**這就是 [Ch 11](./11-optimization-pipeline.md) 要追的東西:這些 `CheckBounds` 在後面的階段被消掉了幾個。**

## pipeline 有哪些階段:真實的 phase 清單

`turbo-*.json` 的 `phases` 陣列,每個 element 是一個優化階段。把這個函式的**頂層階段名**全抓出來(真跑,只列 `V8.TF*` 的主要 phase):

```
$ grep -oE '"name":"V8\.TF[^"]*"' turbo-load-0.json | uniq
V8.TFBytecodeGraphBuilder            ← 從 bytecode+feedback 建初始圖
V8.TFInlining                        ← 內聯:把被呼叫的小函式的圖併進來
V8.TFEarlyGraphTrimming              ← 修剪不可達節點
V8.TFTyper                           ← 型別推導:給每個節點算一個型別
V8.TFTypedLowering                   ← 依型別把高階節點換成低階(JSAdd→NumberAdd)
V8.TFLoopPeeling                     ← 迴圈處理
V8.TFLoadElimination                 ← 消除多餘的記憶體讀取
V8.TFEscapeAnalysis                  ← 逃逸分析:沒逃出去的物件可不配置
V8.TFSimplifiedLowering              ← 把 Simplified 節點降到接近機器層,決定表示法
V8.TFGenericLowering                 ← 進一步 lowering
V8.TFEarlyOptimization
V8.TFTurboshaftBuildGraph            ← 交棒給 Turboshaft(新後端,見下)
V8.TFTurboshaftMachineLowering
V8.TFTurboshaftLoopUnrolling
V8.TFTurboshaftLoadElimination
V8.TFTurboshaftMemoryOptimization
V8.TFTurboshaftCodeEliminationAndSimplification
V8.TFTurboshaftDecompressionOptimization
V8.TFTurboshaftSpecialRPOScheduling
```

這張清單是你在 V8 15.3 上**親眼看到的真實 pipeline**,幾個要點:

- **`TFTyper` → `TFTypedLowering` → `TFSimplifiedLowering`** 是型別推理與降階的核心三步。[Ch 11](./11-optimization-pipeline.md) 專門拆這幾步——bounds-check elimination 就發生在 typing + SimplifiedLowering 一帶。
- **前半是 `TF*`(舊 TurboFan pipeline),後半冒出一整排 `TFTurboshaft*`**。這是 V8 這幾年的大工程:**Turboshaft 是 TurboFan 的新後端**,把原本 sea-of-nodes 後段的 machine-level 優化換成一套更線性、更好維護的 IR。V8 15.3 的 pipeline 是「前段還是 sea-of-nodes(Typer/TypedLowering…),後段轉進 Turboshaft」的混合體。

> **踩雷(版本漂移)**:2019–2021 年的 V8 writeup 完全沒有 `Turboshaft` 這串,pipeline 全程 sea-of-nodes。你在自己的 d8 上看到 `TFTurboshaft*` 不要以為裝錯——那是這個版本的正常樣貌。反過來,舊 writeup 講的某個「發生在 machine lowering 的 bug」,在新版可能搬到了 Turboshaft 那半邊。**看 writeup 先看它的 V8 commit**(這是 [Ch 0](./00-environment-setup.md) 反覆講的紀律),pipeline 結構會隨版本移動。

## 用 turbolizer 把圖畫出來(GUI,未實測)

`turbo-*.json` 是給 **turbolizer** 這個網頁工具吃的,不是給人肉眼讀的。turbolizer 把每個 phase 的圖畫成互動式的節點圖,你能:

- 選不同 phase,看 IR 在該階段的樣子,**逐階段比對某個 `CheckBounds` 節點什麼時候消失**(這對追 BCE bug 是神器)。
- 點一個節點,高亮它的 value/control/effect 邊(三種顏色),看它依賴誰、被誰依賴。
- 對照原始碼行號(節點的 `sourcePosition`)。

架設方式(官方在 V8 原始碼樹 `tools/turbolizer`):

```bash
cd ~/v8build/v8/tools/turbolizer
npm install
npm run dev        # 起一個本機 http server
# 瀏覽器開它給的 localhost URL,把 turbo-load-0.json 拖進去
```

> **標註:未實測(GUI 工具)**。turbolizer 需要瀏覽器,本環境(WSL + 命令列)無法截圖驗證其畫面,故此段的操作與畫面描述**未經本機實測**。但**上游的 `--trace-turbo` 有沒有吐出合法 JSON 是可驗證的,已驗**(見前面 692 KB 的 `turbo-load-0.json`,且結構正確)。你在自己有桌面環境的機器上照上面步驟就能開起來。這是 V8 pwn 圈的標準工具,幾乎每篇 TurboFan writeup 的圖都出自它。

**看不了 GUI 時的替代**:直接 grep JSON(像上面統計 `CheckBounds` 那樣),或看反組譯的機器碼(`--print-opt-code`,[Ch 11](./11-optimization-pipeline.md) 大量用)。GUI 直覺,但最終真相在機器碼——bounds check 有沒有被消掉,反組譯裡有沒有那條 `cmp`/`jnc` 一翻兩瞪眼。

## 對比:TurboFan sea-of-nodes vs 傳統 CFG-based IR vs LLVM IR

| 面向 | 傳統 CFG IR | LLVM IR(SSA + CFG) | TurboFan sea-of-nodes |
|---|---|---|---|
| 順序 | basic block 內線性 | basic block 內線性 | **只有依賴,順序最後才排程** |
| 副作用建模 | 隱含在指令順序 | memory SSA / 隱含 | **顯式的 effect edge** |
| 控制流 | 獨立的 CFG | 獨立的 CFG | **control edge 融進同一張圖** |
| 優化自由度 | 中 | 高 | 高(重排自由,但圖難讀) |
| 對利用的意義 | — | — | **漏洞 = 某節點型別推理錯 / Check 被誤消** |

sea-of-nodes 不是 V8 發明的(Cliff Click 1995 的博論),但 V8 是它最有名的現代使用者。你在 `ssa_optimizations` 課學的 SSA/CFG 優化直覺這裡都用得上,只是**副作用被顯式建模成 effect edge** 這點是最大差異,也是最和安全相關的差異。

## 踩雷集錦

1. **錯誤直覺:「sea-of-nodes 就是把 CFG 畫成圖」**。正確:關鍵差異是**它刻意不強加不必要的執行順序**——兩個無依賴的操作在圖裡就是平行的,順序由最後的排程階段決定。這個「自由」正是優化威力的來源,也是重排類 bug 的溫床。

2. **錯誤直覺:「effect edge 只是實作細節」**。正確:effect edge 是**安全的命脈**。type confusion 的一大類就是「優化器誤判某操作無副作用,把它從 effect chain 鬆綁」導致 Map/bounds check 和實際修改之間的依賴斷掉。[Ch 12](./12-speculation-deopt.md) 的 side-effect 漏洞根源在此。

3. **錯誤直覺:「V8 15 的 TurboFan pipeline 和 2020 的 writeup 一樣」**。正確:V8 這幾年把後段換成 **Turboshaft**,你的 `--trace-turbo` 會看到一整排 `TFTurboshaft*` phase。舊 writeup 的 phase 名對不上是正常的,pipeline 隨版本移動,看 writeup 先看 commit。

4. **錯誤直覺:「turbolizer 是必須的,沒 GUI 就沒法分析」**。正確:turbolizer 直覺但非必須。反組譯的機器碼(`--print-opt-code`)才是最終真相,bounds check 有沒有被消,機器碼裡看得一清二楚(下一章示範)。GUI 沒開起來不代表你分析不了。

5. **錯誤直覺:「漏洞是圖上多了一個壞節點」**。正確:type confusion 通常是**少了一個守衛節點**——一個 `CheckBounds`/`CheckMaps` 被優化器判定多餘而消除。找 bug 要找「本該有 Check 卻沒有」,不是找「多了什麼」。

## 進階:再往深一層

- **`--trace-turbo-graph`**:不想架 turbolizer 時,這個 flag 把每個 phase 的圖用**純文字**印到 stdout(節點 + 邊的文字表示)。醜但能看,適合快速 grep 某個節點在哪個 phase 出現/消失。
- **node 的 `opinfo` 完整格式**:`"N v M eff K ctrl in, ..."` 精確給出該節點的 value/effect/control 邊度數。想確認某個 `Call` 節點掛在 effect chain 上,看它的 `eff` 輸入是不是 ≥1。
- **Turboshaft 的設計動機**:sea-of-nodes 後段對「排程 + 機器層優化」其實不好用(圖太自由反而難做暫存器配置)。Turboshaft 用一套更線性、block-based 的 IR 取代後段,更好維護、編譯更快。想深入讀 V8 blog 的 “Land ahoy: leaving the Sea of Nodes”。
- **`sourcePosition` / `inliningId`**:每個節點記著它來自原始碼哪個位置、被內聯進來時的 inlining id。追一個「內聯後才出現的 bug」時,靠這兩個欄位把節點對回原始函式。

## 動手練習

1. 拿本章的 `load` 函式跑 `--trace-turbo`,確認產出 `turbo-load-0.json`。用 `grep -oE '"name":"V8\.TF[^"]*"' turbo-load-0.json | uniq` 列出所有 phase,對照本章那張清單,找出你的版本多了/少了哪些 phase。
2. 用 `grep -c '"opcode":"CheckBounds"'` 數這個 JSON 裡 `CheckBounds` 總共出現幾次。再寫一個**索引一定合法**的版本(例如 `arr[0]+arr[1]` 寫死),重跑,比較 `CheckBounds` 出現次數變化。你在用最粗糙的方式觀察 bounds-check elimination(細節下一章)。
3. 用 `--trace-turbo-graph` 印純文字圖,找一個 `Return` 節點,讀它的 `opinfo`,說出它有幾條 value/effect/control 輸入邊。把「三種邊」從概念變成你在輸出裡指得出來的東西。
4. (有桌面環境的話)照本章步驟架 turbolizer,把 `turbo-load-0.json` 拖進去,切到不同 phase,肉眼找那個 `arr[i]` 的 `CheckBounds` 節點在哪個 phase 消失。這是 V8 pwn 分析的日常動作。

## 本章重點整理

- **sea-of-nodes** 把程式打散成節點的海,**只保留依賴、不強加不必要的順序**,給優化器最大重排自由;代價是難以肉眼閱讀,故需 turbolizer。
- **三種邊**:value(誰是誰的輸入)、control(執行流怎麼走)、effect(副作用先後不能亂)。**effect edge 是安全命脈**,誤判副作用 = type confusion 的一大來源。
- node 分層:通用/控制 → JS 層 → **Simplified 層(`CheckBounds`/`CheckMaps` 守衛節點住這,type confusion 主戰場)** → Machine 層,pipeline 逐步 lowering。
- **漏洞通常是「少了一個 Check 節點」**——守衛被優化器誤判為多餘而消除,不是「多了壞節點」。
- `--trace-turbo` 產出 `turbo-*.json`(可驗證,已驗),餵給 **turbolizer**(GUI,未實測)看每個 phase 的圖;V8 15.3 的 pipeline 是「前段 sea-of-nodes + 後段 Turboshaft」的混合體。

## 自我檢核

- [ ] 能解釋 sea-of-nodes 和傳統 CFG IR 最根本的差異(不強加順序)
- [ ] 能說出 value/control/effect 三種邊各表達什麼,並解釋為什麼 effect edge 和安全高度相關
- [ ] 看到 `CheckBounds`/`CheckMaps` 節點知道它們是「守衛」,且知道 type confusion 常是它們被誤消除
- [ ] 能用 `--trace-turbo` 產出 JSON、用 grep 統計某類節點出現次數
- [ ] 知道 V8 15.3 的 pipeline 後段已是 Turboshaft,舊 writeup 的 phase 名對不上是版本漂移
- [ ] **面試題**:為什麼說 sea-of-nodes 的「重排自由」既是效能來源、也是漏洞溫床?(答:不強加順序讓優化器能大膽重排/消除節點;一旦它對某節點的型別或副作用推理錯誤,錯誤的重排/消除就直接產生 type confusion 或 OOB。)

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

### 官方文件 / 部落格

- **[“TurboFan JIT” 與 sea-of-nodes 相關 — v8.dev/blog](https://v8.dev/blog)**
  - **這篇說什麼**:TurboFan 的設計哲學、sea-of-nodes 為什麼被選中。
  - **讀哪裡**:先讀概述,別鑽演算法。本章的三種邊是它的實戰化。
  - **和本章的關聯**:官方對「為什麼不強加順序」的說法比本章的比喻更權威。

- **[“Land ahoy: leaving the Sea of Nodes” — v8.dev/blog/leaving-the-sea-of-nodes](https://v8.dev/blog/leaving-the-sea-of-nodes)**
  - **這篇說什麼**:V8 團隊解釋為什麼後段要離開 sea-of-nodes、改用 Turboshaft,以及 sea-of-nodes 在實務上的痛點。
  - **為什麼值得讀**:直接解答你在 `--trace-turbo` 看到一整排 `TFTurboshaft*` 的困惑,是理解本章「混合 pipeline」的第一手來源。
  - **關聯**:讀完就懂為什麼舊 writeup 的 pipeline 圖和你的不一樣。

### 深入資料

- **[Benedikt Meurer 的 TurboFan 演講與 blog — benediktmeurer.de](https://benediktmeurer.de/)**
  - **這篇說什麼**:前 TurboFan 負責人對 IR、typing、speculation 的第一手講解,常附 turbolizer 截圖。
  - **和本章的關聯**:他的圖就是 turbolizer 產的,看他怎麼讀圖,就是本章「看 IR」的示範。是通往 [Ch 11](./11-optimization-pipeline.md)/[Ch 12](./12-speculation-deopt.md) 的最佳橋樑。

- **[Cliff Click, “A Simple Graph-Based Intermediate Representation”(1995 原始論文)](https://www.oilshell.org/archive/Simple-Graph-Based-IR.pdf)**
  - **這篇說什麼**:sea-of-nodes 的原始出處,講清楚為什麼要把 control/data/effect 融進一張圖。
  - **讀哪裡**:前半的動機與三種邊那段。後半演算法可略。
  - **關聯**:本章三種邊的理論根源,想徹底理解「為什麼是這三種」讀它。

### 原始碼 / 工具

- **V8 `tools/turbolizer/`(README + 原始碼)**
  - **讀哪裡**:README 的架設步驟(本章那三行 npm 指令)。想改工具再看原始碼。
  - **關聯**:本章「未實測」的那段,你在有桌面的機器上照 README 就能實測。

- **V8 `src/compiler/`——`node.h`、`opcodes.h`、`simplified-operator.h`**
  - **讀哪裡**:`opcodes.h` 掃一遍所有節點 opcode(對照本章的大類表);`simplified-operator.h` 找 `CheckBounds`/`CheckMaps` 的定義。
  - **關聯**:你在 JSON 看到的每個 `opcode`,定義都在這;下一章追 BCE 時會回到 `simplified-lowering.cc`。

sea-of-nodes 的骨架有了,三種邊也認得了。下一章我們深挖 pipeline 中間那幾個優化階段——typing、range analysis、redundancy elimination,尤其是把 `CheckBounds` 消掉的 **bounds-check elimination**。那正是陣列 OOB 型 type confusion 的機制根源。

→ [Ch 11 — 優化 pipeline:typing、redundancy / bounds-check elimination](./11-optimization-pipeline.md)
