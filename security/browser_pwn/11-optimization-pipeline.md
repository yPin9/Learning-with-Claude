# Ch 11 — 優化 pipeline：typing、redundancy / bounds-check elimination

> **目標**：這是 Part 2 的兩個深挖章之一,也是整門課「機制 → 漏洞」轉折的關鍵一章。你要看透優化器怎麼一步步「推理」你的 JS:先給每個節點算一個**型別(typing)**、再用**range analysis(範圍分析)**縮小整數的可能值域、再用這些資訊做 **redundancy elimination(冗餘消除)**——其中最要命的是 **bounds-check elimination(BCE,把 `CheckBounds` 消掉)**。你要能親手用 `--print-opt-code` 看到一個 bounds check 存在、再看到它在另一個函式裡被消掉,並理解:**「優化器證明索引一定在範圍內、於是刪掉那個 check」這件事,一旦它的證明基於錯誤的型別假設,就是陣列 OOB 讀寫的直接根源。這正是 Part 4([Ch 19](./19-turbofan-type-confusion.md) 起)最經典的一類 type confusion。**

> **環境**：V8 15.3.0(candidate、commit `ab2cad06`)、`~/v8build/v8/out/x64.release/d8`(`disassembler`/`sandbox`/`pointer_compression` 全開)。本章所有反組譯(`--print-opt-code`)、deopt reason、`CheckBounds` 出現/消失的統計都是在這顆 d8 上真跑出來的。反組譯的機器碼會因 V8 版本不同而變,這裡貼的是本版真實輸出。

## 為什麼需要這個？

[Ch 10](./10-turbofan-overview.md) 給了你 sea-of-nodes 的骨架,並反覆強調一句話:**漏洞通常是「少了一個 Check 節點」**。這章就是要讓你親眼看到「Check 節點怎麼被合法地消掉」,因為只有先懂**合法的消除**,你才懂 Part 4 的**非法的消除**(漏洞)長什麼樣、差在哪。

具體來說:陣列存取 `arr[i]` 在優化前一定會生一個 `CheckBounds` 節點,守衛「`i` 沒有越界」。優化器有一整套推理機器,目的是**在能證明安全的前提下,把這個 check 刪掉以加速**。這套推理機器就是本章的 typing + range analysis + redundancy elimination。

- 它**正確運作**時:刪掉的都是真的多餘的 check,程式又快又安全。
- 它**推理錯誤**時(基於一個被污染的 feedback、一個錯誤的型別推導、一個沒算進去的副作用):它會刪掉一個**其實不多餘**的 `CheckBounds`,於是優化後的機器碼帶著「我保證 `i` 在範圍內」的自信,對一個實際越界的 `i` 做記憶體存取——**OOB 讀寫**。

所以這章不是「優化器的美好」,是「優化器最鋒利、也最容易割到自己的那把刀」。看懂它,Part 4 的陣列型 type confusion 對你就從「魔法」變成「機制的必然」。

## 先建立直覺:一個過度自信的保鏢

想像陣列存取 `arr[i]` 前站著一個保鏢(`CheckBounds`),每次有人要進門(存取索引)都攔下來量身高(檢查 `i < length`),不合格就轟出去(deopt)。這個保鏢盡責但慢——每次都量。

優化器是保鏢的主管,它想省掉不必要的盤查。它會**推理**:

> 「這個 `i` 是 `someOtherIndex & 3` 算來的,`& 3` 保證結果在 0~3。而這個陣列我看它長度一直是 4。**0~3 一定 < 4,所以這道門的保鏢是多餘的,撤掉。**」

推理沒錯的話,撤掉保鏢純賺速度。**但主管的推理建立在兩個前提上**:「`i` 一定是 `x & 3`(所以 ∈ [0,3])」和「陣列長度一定 ≥ 4」。如果攻擊者能讓其中一個前提在優化器背後悄悄失效——比方讓陣列長度變成 2,而主管還記得「長度 ≥ 4 所以不用查」——那道沒有保鏢的門就讓一個越界的存取大搖大擺走進去。

**這章教你看主管怎麼推理、怎麼撤保鏢。Part 4 教你怎麼騙主管撤錯保鏢。**

## pipeline 中段的三步推理

[Ch 10](./10-turbofan-overview.md) 列過真實的 phase 清單。這章聚焦中段那三個和「消除 Check」直接相關的:

```
   ...BytecodeGraphBuilder → Inlining →
   ┌─────────────────────────────────────────────────────────┐
   │ (1) Typer          給每個節點算一個「型別」               │
   │ (2) TypedLowering  依型別把高階節點換低階,順手消一些檢查  │
   │ (3) SimplifiedLowering  決定表示法(int32/float64...),    │
   │                    range analysis 在這一帶收斂,           │
   │                    多餘的 CheckBounds 在這裡被判死         │
   └─────────────────────────────────────────────────────────┘
   → ...Turboshaft 後段(進一步消除、排程、生機器碼)
```

### 步驟 1:Typer——給每個節點貼型別標籤

`Typer` 這個 phase 走訪圖上每個節點,替它算一個**型別(type)**。這裡的「型別」不是 JS 的 `typeof`,是 TurboFan 內部一套**格(lattice)**上的抽象值,比 JS 型別精細得多。例如:

- 一個 `LdaSmi [3]` 常數節點的型別是 `Range(3, 3)`——精確到「就是 3」。
- 一個 `x & 3` 的型別是 `Range(0, 3)`——位元 AND 保證結果落在 0~3。
- 一個 `arr.length`(對 `PACKED_DOUBLE_ELEMENTS` 陣列)的型別可能是 `Range(0, 2^32-1)` 之類的無號整數範圍。
- 一個從 feedback 得知「總是 SMI」的加法,型別會被推成某個整數 Range;若 feedback 說「有時是浮點」,型別放寬到含 Float64。

**型別來自哪裡?兩個源頭:操作本身的語意(`& 3` 天生 ∈[0,3])+ feedback(執行期收集的型別紀錄)。** 這就是為什麼 [Ch 9](./09-parser-ignition-bytecode.md) 一直強調 feedback:**feedback 直接決定 Typer 給節點貼什麼型別標籤,而型別標籤決定後面哪些 check 被判為多餘**。污染 feedback → 讓 Typer 貼錯標籤 → 讓錯誤的 check 消除發生,這是一條完整的攻擊鏈,Part 4 反覆走。

### 步驟 2:range analysis——把整數的可能值域夾緊

typing 對整數節點做的事,本質是 **range analysis(範圍分析)**:追蹤每個整數值「最小可能是多少、最大可能是多少」。這是 dataflow 分析,沿著 value edge 傳播:

```
   i = x & 3           →  i ∈ [0, 3]
   j = i + 1           →  j ∈ [1, 4]
   k = i * 2           →  k ∈ [0, 6]
   if (i < len) {...}  →  進入 then 分支後,i ∈ [0, min(3, len-1)]  ← 分支細化
```

注意最後一行:**進入 `if (i < len)` 的 then 分支後,優化器知道「這裡的 `i` 一定 < len」**。這個「分支條件細化型別」的能力,是 BCE 最主要的武器之一——迴圈 `for(i=0;i<arr.length;i++)` 的 body 裡,`i` 已經被條件保證 `< arr.length`,所以 body 裡的 `arr[i]` 的 `CheckBounds` **就是多餘的**,可以合法消掉。下面會親眼看到。

### 步驟 3:redundancy elimination——把可證明多餘的檢查刪掉

有了每個節點的型別/範圍,優化器做各種**冗餘消除**:

- **redundant `CheckMaps` elimination**:如果一個物件的 Map 已經在前面被 `CheckMaps` 確認過,且中間**沒有可能改變它 Map 的副作用**(這裡又碰到 [Ch 10](./10-turbofan-overview.md) 的 effect edge!),後面對同一物件的 `CheckMaps` 就是多餘的,刪掉。
- **`CheckBounds` elimination(BCE)**:如果 range analysis 證明「這個索引的範圍**一定**落在 `[0, length)` 內」,那個 `CheckBounds` 就是多餘的,刪掉。
- **load elimination**:同一個屬性讀兩次、中間沒改,第二次的讀可以復用第一次的結果。

**這三個都依賴「中間沒有搗亂的副作用」這個前提,而這個前提由 effect edge 表達。** 所以:**BCE / CheckMaps elimination 的正確性,綁死在「effect chain 是否完整正確」上。** 一旦優化器漏算了某個會改變陣列長度/物件 Map 的副作用(例如一個看似無害、實則會 resize 陣列的 callback),它就會消掉一個**其實不該消**的 check——這是 [Ch 12](./12-speculation-deopt.md) side-effect 漏洞和 Part 4 的共同根源。

## 親眼看 bounds check:它在這裡

理論講完,上機。看 [Ch 10](./10-turbofan-overview.md) 用過的 `load(arr, i)`——索引 `i` 是**任意參數**,優化器**無法**證明它在範圍內,所以 bounds check **必須保留**。反組譯(真跑,`--print-opt-code` 節錄關鍵段):

```
$ cat load.js
function load(arr, i) { return arr[i] + arr[i + 1]; }
let a = [1.1, 2.2, 3.3, 4.4, 5.5];
%PrepareFunctionForOptimization(load);
load(a, 0); load(a, 1);
%OptimizeFunctionOnNextCall(load);
load(a, 0);

$ d8 --allow-natives-syntax --print-opt-code load.js
--- Optimized code ---
kind = TURBOFAN_JS
name = load
...
Instructions (size = 628)
   ; --- 檢查 arr 的 Map 是不是 PACKED_DOUBLE_ELEMENTS(CheckMaps 的機器碼) ---
   26  movl r8,0x100cfc9    ;; (compressed) object: <Map[16](PACKED_DOUBLE_ELEMENTS)>
   2c  cmpl [rcx-0x1],r8
   30  jnz  0x...0288       ;; 不合就跳 deopt

   36  movl r8,[rcx+0x7]    ; r8 = arr 的 elements 指標
   3d  movl r9,[rcx+0xb]    ; r9 = arr.length(原始)
   41  sarl r9,1            ; r9 = length(從 tagged 還原)
   ...
   ; --- 第一個 arr[i] 的 bounds check ---
   5c  cmpq r12,r9          ; 比較 index(r12) 和 length(r9)
   5f  jnc  0x...028c       ;; index >= length 就跳 → deopt reason 'out of bounds'
   65  vmovsd xmm0,[r8+r12*8+0x7]   ; 真正讀 arr[i](通過 check 才讀)
   ...
   ; --- 第二個 arr[i+1] 的 bounds check ---
   86  cmpl r12,r9
   89  jnc  0x...0298       ;; → deopt reason 'out of bounds'
   8f  vaddsd xmm0,xmm0,[r8+r12*8+0x7]  ; 讀 arr[i+1] 並加上去
   ...

; deopt 出口(真跑輸出的註解):
   24c  call [r13-0x28]   ;; debug: deopt reason 'out of bounds'
   258  call [r13-0x28]   ;; debug: deopt reason 'out of bounds'

RelocInfo:
   ... deopt reason  (out of bounds)     ← 真的有兩個 out-of-bounds deopt 點
   ... deopt reason  (out of bounds)
```

逐格解讀,這是你要練的核心技能:

- **`cmpl [rcx-0x1],r8` + `jnz → deopt`**:這是 `CheckMaps` 的機器碼實現。`[rcx-0x1]` 是物件的 Map 指標(pointer compression 下 Map 在物件頭 offset -1 的壓縮欄位),和 `r8`(賭定的 `PACKED_DOUBLE_ELEMENTS` Map)比;不合就 deopt。**優化器賭「`arr` 永遠是這個 Map」,並用一條 cmp 守衛這個賭注。**
- **`cmpq r12,r9` + `jnc → deopt reason 'out of bounds'`**:這是 `CheckBounds` 的機器碼實現。`r12` 是索引 `i`,`r9` 是 `length`。`jnc`(jump if not carry,即 `i >= length`)跳去 deopt。**這就是保鏢:每次存取都比一次、越界就 deopt。**
- 統計:這個函式的反組譯裡有 **2 個 `out of bounds` deopt reason**——對應 `arr[i]` 和 `arr[i+1]` 兩次存取,各一個 bounds check。**優化器保留了它們,因為 `i` 是任意參數,無法證明在範圍內。**

**這是「保鏢還在」的樣子。記住 `cmpq index,length` + `jnc → out of bounds` 這個機器碼指紋。**

## 親眼看 bounds check 被消掉:BCE 發生

現在換一個**索引可證明在範圍內**的函式——經典的 `for(i=0;i<arr.length;i++)` 迴圈。迴圈條件 `i < arr.length` 保證 body 裡的 `i` 一定 `< length`,所以 `arr[i]` 的 bounds check **是多餘的,會被消掉**。用最直接的方式驗證:數這個函式的反組譯裡有幾個 `out of bounds` deopt reason(真跑):

```
$ cat sum.js
function sum(arr) {
  let s = 0.0;
  for (let i = 0; i < arr.length; i++) { s += arr[i]; }
  return s;
}
let a = [1.1,2.2,3.3,4.4,5.5,6.6,7.7,8.8];
%PrepareFunctionForOptimization(sum);
sum(a); sum(a);
%OptimizeFunctionOnNextCall(sum);
sum(a);

$ d8 --allow-natives-syntax --print-opt-code sum.js | grep -c "out of bounds"
0
```

**對比一翻兩瞪眼**:

| 函式 | 索引來源 | 優化器能否證明 `i` 在範圍內 | 反組譯裡 `out of bounds` deopt 數 |
|---|---|---|---|
| `load(arr, i)` | 任意參數 `i` | ❌ 不能 | **2**(check 保留) |
| `sum(arr)` | `for(i=0;i<arr.length;i++)` | ✅ 能(迴圈條件保證) | **0**(check 被 BCE 消掉) |

`sum` 裡那個 `arr[i]` 完全沒有 bounds check——優化器用 range analysis + 分支細化證明了「迴圈 body 裡 `i` 一定 ∈ [0, arr.length)」,於是把 `CheckBounds` 判死。這**就是 bounds-check elimination**。程式更快(少了每圈一次的 cmp/jnc),而且**在 `arr.length` 不變的前提下完全安全**。

**關鍵在那個「前提」**:BCE 的安全性,建立在「優化器對 `arr.length` 和 `i` 範圍的推理是對的」之上。這推理的輸入是 feedback + 型別 + effect chain。**Part 4 做的事,就是讓其中一個輸入在優化器背後失真,誘導它消掉一個不該消的 `CheckBounds`。**

## 這個機制怎麼在 Part 4 被濫用(伏筆,不實作)

把上面兩節接起來,你已經能看懂陣列 OOB 型 type confusion 的**骨架**。Part 4([Ch 19](./19-turbofan-type-confusion.md) 起)會實作,這裡只點出機制根源:

**攻擊模板(僅示意骨架,細節在 Part 4)**:

1. 讓優化器基於某個假設消掉某個 `arr[i]` 的 `CheckBounds`(例如透過 range analysis 相信 `i` 有上界,或相信 `arr.length` 有下界)。
2. 在優化完成後,想辦法**打破那個假設而不觸發 deopt**——例如:
   - 讓一個「優化器以為無副作用」的操作(某個 callback、某個型別轉換、某個 valueOf)偷偷把 `arr` 縮短或換 Map(這需要 effect chain 有漏,是 [Ch 12](./12-speculation-deopt.md) 的 side-effect 主題)。
   - 或利用某個型別推導的 off-by-one / 邊界錯誤,讓優化器算出的 Range 比實際寬鬆一格(著名的 `Array.prototype.fill`/`slice` 長度推導 bug 就是這類)。
3. 現在那份優化過的機器碼,對一個實際越界的 `i` 執行 `vmovsd xmm0,[r8+r12*8+0x7]`——**沒有保鏢的門,OOB 讀寫成立**。

**看出對稱性沒有?** 合法 BCE 和漏洞的差別,不在「消不消 check」,在「消 check 的推理對不對」。優化器消 check 是它的本職;**攻擊者的工作是餵它一個會導致錯誤推理的世界**。這就是為什麼 [Ch 9](./09-parser-ignition-bytecode.md) 的 feedback、[Ch 10](./10-turbofan-overview.md) 的 effect edge、本章的 typing/range,是同一條攻擊鏈上的環節。

> **一句話貫穿 Part 2 → Part 4**:**bounds-check elimination 不是漏洞,「基於錯誤前提的 bounds-check elimination」才是。** 你這章學的是機制,Part 4 學的是怎麼製造那個錯誤前提。

## 其他值得認得的優化

BCE 是主角,但 pipeline 還做很多事,認得名字有助讀 writeup:

| 優化 | 做什麼 | 和安全的關係 |
|---|---|---|
| **redundant CheckMaps elimination** | 消掉對同一物件的重複 Map 檢查 | 和 BCE 對稱:漏算副作用 → 消掉不該消的 Map check → Map type confusion |
| **load/store elimination** | 消掉多餘的記憶體讀寫、復用結果 | 若誤判「中間沒改過」,復用到 stale 值 → 邏輯 bug/資訊洩漏 |
| **escape analysis** | 沒逃出函式的物件不真的配置(scalar replacement) | 逃逸分析出錯 → 物件狀態不一致,歷史上出過 bug |
| **constant folding / strength reduction** | 常數摺疊、把貴的運算換便宜的 | 較少直接安全影響 |
| **dead code elimination** | 刪不可達/無用節點 | 較少直接安全影響 |
| **typed lowering** | 依型別把 `JSAdd` 降成 `NumberAdd`/`SpeculativeNumberAdd` | 型別推導錯 → 降成錯的低階操作 |

其中 **`SpeculativeNumberAdd`**(你在 [Ch 10](./10-turbofan-overview.md) 的 JSON 裡看過)值得一提:它是「賭這個加法的兩個運算元都是數字」的加法節點。賭對就用快的數值加法;賭錯(某運算元其實是物件/字串)就 deopt。這個「speculative」前綴是 [Ch 12](./12-speculation-deopt.md) 的主題——**優化器充滿了 speculative 節點,每個都是一個賭注 + 一個守衛**。

## 對比:AOT 編譯器的 BCE vs TurboFan 的 BCE

| 面向 | C/C++ 編譯器(如 GCC/LLVM) | TurboFan |
|---|---|---|
| 有沒有 bounds check | C 語言根本沒有(所以有 OOB UB) | JS 有,存取一定生 `CheckBounds` |
| BCE 依據 | 靜態範圍分析(編譯期全知) | **範圍分析 + 執行期 feedback + speculation** |
| 消錯的後果 | (C 本來就沒 check,不談) | **消掉不該消的 check = OOB 漏洞** |
| 賭錯的退路 | 無(編譯期定型) | deopt(正常)或 type confusion(漏洞) |

關鍵差異:**C 編譯器的 BCE 是純靜態的,錯了頂多優化失效;TurboFan 的 BCE 建立在「執行期 feedback + speculation」上,這些輸入可被攻擊者操弄,錯了就是記憶體破壞。** JIT 的 BCE 比 AOT 的危險一個數量級,因為它的推理前提是動態的、可被污染的。

## 踩雷集錦

1. **錯誤直覺:「bounds check 被消掉 = 有漏洞」**。正確:消 check 是優化器的**正當本職**,絕大多數 BCE 完全安全(如本章的 `sum`)。漏洞是「基於**錯誤前提**的 BCE」。看到 check 被消別興奮,要問「它的消除前提能不能被打破」。

2. **錯誤直覺:「typing 用的『型別』就是 JS 的 `typeof`」**。正確:TurboFan 的型別是內部 lattice 上的抽象值(`Range(0,3)`、`Float64`、`OrderedNumber`…),比 JS 型別精細太多,還帶整數範圍。BCE 靠的正是這種精細的 `Range` 型別。

3. **錯誤直覺:「range analysis 只看單一運算式」**。正確:它是 dataflow 分析,**沿 value edge 跨運算式傳播,還會被分支條件細化**(`if(i<len)` 的 then 分支裡 `i` 被夾到 `<len`)。迴圈 BCE 全靠這個分支細化。

4. **錯誤直覺:「redundancy elimination 只看資料流」**。正確:CheckMaps/CheckBounds 的消除**還依賴 effect chain**——「中間有沒有可能改變 Map/length 的副作用」。漏算副作用是這類漏洞的核心,effect edge([Ch 10](./10-turbofan-overview.md))在這裡是安全命脈。

5. **錯誤直覺:「反組譯太難,看 turbolizer 就好」**。正確:turbolizer 直覺,但**最終真相在機器碼**。「這個 bounds check 到底消了沒」,`--print-opt-code` 裡 `cmpq index,length` + `jnc out of bounds` 在不在,一翻兩瞪眼,不會騙你。學會讀這段機器碼指紋是硬功夫。

6. **錯誤直覺:「反組譯的位址/offset 我可以照抄」**。正確:機器碼會因 V8 版本而變(本章貼的是 commit `ab2cad06`)。看的是**模式**(有沒有 map check、有沒有 bounds check、deopt reason 是什麼),不是具體位址。

## 進階:再往深一層

- **`--trace-turbo` 逐 phase 追 `CheckBounds` 消失點**:把 [Ch 10](./10-turbofan-overview.md) 的 turbolizer 用起來,切換 phase,肉眼定位那個 `CheckBounds` 節點在**哪個 phase** 消失(通常在 SimplifiedLowering 一帶)。追一個真實 BCE bug 時,「它本該在哪個 phase 被保留卻被消了」是 root cause 分析的核心問題。
- **`Range` 型別的邊界 bug**:很多真實 CVE 是「某個 builtin(如 `Array.prototype.indexOf`、`String.fromCharCode`、`Math.max`)的回傳值型別被 Typer 推導得比實際寬鬆一格」,導致下游 range 算錯、BCE 消錯 check。想深入讀 saelo 的 “Exploiting Logic Bugs in JavaScript JIT Engines” 和相關 Project Zero issue。
- **`CheckBounds` 的 abort/deopt 語意**:`CheckBounds` 越界時是 deopt(退回直譯器)還是 abort(當掉)?一般是 deopt。理解「它到底怎麼失敗」影響你判斷「消掉它之後,越界存取是靜靜地 OOB 還是會被別的機制擋下」。
- **Turboshaft 後段的再消除**:pipeline 後段(Turboshaft)還會做一輪 machine-level 的優化,可能再消掉一些冗餘。追 bug 時別忘了看後段——一個 check 可能前段留著、後段才被消。
- **`--no-turbo-loop-peeling` 之類的 flag**:V8 有一堆 `--no-turbo-*` 開關可以**關掉單一優化 pass**,用來二分定位「是哪個優化 pass 消掉了這個 check」。triage 漏洞時的利器。

## 動手練習

1. 把本章的 `load`(任意索引)和 `sum`(迴圈索引)都跑 `--print-opt-code`,分別 `grep -c "out of bounds"`。確認 `load` 是 2、`sum` 是 0。這是你親手驗證 BCE 的第一步。
2. 寫一個 `masked(arr, i){ return arr[i & 3]; }`,陣列長度分別設 4 和 2,各跑 `--print-opt-code | grep "out of bounds"`。觀察:當陣列長度 ≥ 4 時,`i & 3` 保證 ∈[0,3] < 4,check 可能被消(0 個);當長度可能 < 4 時,優化器不敢消(保留)。(提示:實測結果可能因 V8 版本的 range 精度而異,重點是理解「消不消取決於能否證明 `i & 3 < length`」。)
3. 拿 `sum` 版本,把迴圈條件從 `i < arr.length` 改成 `i <= arr.length`(故意 off-by-one),重跑 `--print-opt-code`。這下 `i` 可能等於 `length`,優化器**還能不能**消掉 bounds check?觀察 `out of bounds` deopt 數的變化,體會「證明前提差一格,BCE 就不敢做」。
4. 用 `--trace-turbo` 對 `sum` 產出 JSON,`grep -c '"opcode":"CheckBounds"'`,再對 `load` 做同樣的事,對比兩者 `CheckBounds` 節點的相對數量。(注意:JSON 是所有 phase 加總,粗略但能看出趨勢。)

## 本章重點整理

- 優化器中段三步推理:**Typer(給節點貼型別/Range)→ TypedLowering → SimplifiedLowering(range analysis 收斂、多餘 check 判死)**。
- **型別來自「操作語意 + feedback」**;feedback 污染 → Typer 貼錯型別 → 錯誤的 check 消除,這是一條完整攻擊鏈。
- **range analysis 沿 value edge 傳播、被分支條件細化**;迴圈 `for(i=0;i<len;i++)` 的 body 裡 `i` 被證明 `<len`,是 BCE 的主要武器。
- **BCE / CheckMaps elimination 的正確性綁死在 effect chain 完整性上**——漏算副作用 = 消掉不該消的 check = 漏洞。
- 親眼驗證:`load`(任意索引)反組譯有 **2 個 `out of bounds` deopt**(check 保留),`sum`(迴圈索引)有 **0 個**(BCE 消掉)。機器碼指紋:`cmpq index,length` + `jnc out of bounds`。
- **一句話**:bounds-check elimination 不是漏洞,**基於錯誤前提的 BCE** 才是。Part 4 的陣列 OOB type confusion 直接源於此。

## 自我檢核

- [ ] 能解釋 Typer 給節點的「型別」和 JS `typeof` 的差異,以及 `Range` 型別怎麼來
- [ ] 能說明 range analysis 怎麼被分支條件細化,為什麼迴圈 body 裡的 `arr[i]` 可以消 check
- [ ] 能在 `--print-opt-code` 輸出裡指出哪段是 `CheckMaps`、哪段是 `CheckBounds`(map cmp / index-length cmp + jnc)
- [ ] 能解釋為什麼 `load` 保留 bounds check 而 `sum` 消掉了,並用 `grep -c "out of bounds"` 驗證
- [ ] 能說出「合法 BCE」和「漏洞」的唯一差別在哪(消 check 的推理前提對不對)
- [ ] 知道 BCE 的安全性為什麼綁在 effect chain 上
- [ ] **面試題**:給定 `for(i=0;i<arr.length;i++) arr[i]`,TurboFan 為什麼能安全消掉 `arr[i]` 的 bounds check?攻擊者要讓這個消除變成 OOB,需要打破哪個前提?(答:迴圈條件證明 `i<arr.length`,故 body 裡 check 多餘;攻擊者需在優化後、不觸發 deopt 的前提下讓 `arr.length` 縮小或讓 `i` 超出——通常靠一個優化器誤判無副作用的操作偷改 length。)

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。

### 官方文件 / 部落格

- **[Benedikt Meurer, “An Introduction to Speculative Optimization in V8” — benediktmeurer.de](https://benediktmeurer.de/2017/12/13/an-introduction-to-speculative-optimization-in-v8/)**
  - **這篇說什麼**:speculative optimization、typing、check 的存在意義,作者是前 TurboFan 負責人。
  - **讀哪裡**:typing 與 speculative node 那幾段,和本章的 Typer/range 對讀。
  - **關聯**:本章的「型別來自 feedback」在這裡有第一手權威說法,也接 [Ch 12](./12-speculation-deopt.md)。

### 攻擊視角(直接看機制怎麼被濫用)

- **[saelo, “Exploiting Logic Bugs in JavaScript JIT Engines”(Phrack / saelo.github.io)](https://saelo.github.io/papers/)**
  - **這篇說什麼**:JIT 邏輯 bug(含 bounds-check elimination 被誤導)的系統化分類與利用範式,V8 pwn 的奠基讀物之一。
  - **讀哪裡**:整篇都值得,但先看「redundancy elimination / bounds check」相關段落,正是本章 Part 4 伏筆的完整版。
  - **關聯**:本章講「合法 BCE」,這篇講「怎麼騙它做非法 BCE」,是 Part 2 → Part 4 的直通橋。
  - **前提**:讀完本章對 typing/BCE 的機制理解後看,會非常有共鳴。

- **[Project Zero 關於 TurboFan typer / range bug 的 issue 與 writeup(如 CVE-2020-16040 一類 typer 邊界 bug)](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**:真實案例——某個 builtin 的回傳型別被 Typer 推寬一格,導致 range 算錯、BCE 消錯 check。
  - **讀哪裡**:root cause 分析那段,對照本章的 range analysis。
  - **關聯**:把本章「基於錯誤前提的 BCE」從抽象變成一個有 CVE 編號的真實案例。

### 原始碼

- **V8 `src/compiler/`——`typer.cc`、`simplified-lowering.cc`、`redundancy-elimination.cc`**
  - **讀哪裡**:`typer.cc` 找某個運算子的 typing rule(例如 `Word32And` 怎麼算出 `Range(0,mask)`);`redundancy-elimination.cc` 看 CheckBounds/CheckMaps 消除的條件。
  - **關聯**:本章的三步推理,原始碼就在這幾個檔。想確認某個 typing rule 到底寬鬆到哪一格,讀 `typer.cc` 比猜快。

typing 和 BCE 讓你看懂了「優化器基於假設省掉檢查」。但假設會賭錯——賭錯時的機制是 deopt。下一章我們深挖 speculation 與 deoptimization:優化器怎麼下賭注、怎麼埋守衛、賭錯怎麼安全退回,以及最關鍵的——**「該 deopt 卻沒 deopt」為什麼就是漏洞**,還有那對 `%PrepareFunctionForOptimization`/`%OptimizeFunctionOnNextCall` 儀式到底在幹嘛。

→ [Ch 12 — Speculation 與 Deoptimization](./12-speculation-deopt.md)
