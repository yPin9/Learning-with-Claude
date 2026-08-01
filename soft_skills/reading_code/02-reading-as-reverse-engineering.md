# Ch 2 — 讀碼是一種逆向工程

> **目標**：把你在 binary reverse engineering 上練出來的攻堅直覺，明確搬到讀 source 上。這章是全課的定調章：讀懂之後你會意識到「你在逆 binary 時會的，一半可以直接拿來用」——找 entry、跟 flow、猜 invariant、從命名/字串恢復語意、由外而內，這些心法在 source 上照樣成立，而且你手上的線索還更多。

## 先建立直覺：source 和 binary 只是同一個檔案在不同壓縮等級

上一章講寫碼是有損壓縮。把這條光譜畫完整：

```
   意圖（作者腦中）
      │  compile step 1：意圖 → source   （丟：為什麼、試錯史、context）
      ▼
   source code                    ← 你以為的「原始」，其實已經是壓縮產物
      │  compile step 2：source → binary（丟：命名、型別、註解、結構）
      ▼
   machine code                   ← 逆向工程師面對的最壓縮形態
```

逆向工程師每天做的事，是從最右邊那格往左還原意圖。而**你讀陌生 source，做的是同一個方向的動作**——只是起點在中間那格。你不是在做一件全新的事，你是在做逆向工程師做的事的**簡單版**：從壓縮產物反推意圖，只是你的產物少壓了一層。

這一句話值得停下來體會，因為它給你兩個東西：**信心**（你會的攻堅心法能直接用）和**清醒**（source 一樣是壓縮產物，別天真以為「有原始碼就都懂了」）。做過 RE 的人讀 source 應該有種熟悉的鬆一口氣——「喔，這關我打過，只是這次怪物血少一半」。

## 相同的心法：RE 的攻堅套路，一條條搬過來

逆向一個陌生 binary，你不會從 `_start` 一條一條反組譯到底。你有一套攻堅套路。每一條在 source 上都成立：

### 1. 找 entry point，不從頭讀

**RE**：你先定位 `main`（或 `WinMain`、或 `.init_array`）。IDA/Ghidra 幫你標出來，因為那是控制流的源頭。

**Source**：完全一樣。redis 十萬行，你要找的第一個錨點是真正的伺服器入口。上一章的 Ch 0 已經示範過——八個 `main` 裡真正的伺服器入口在 `src/server.c`，往下走到：

```c
initServer();          // server.c:7189
...
aeMain(server.el);     // server.c:7251  ← 事件迴圈，整個 server 的心臟
```

`aeMain` 就是 redis 的主迴圈，等價於你在 binary 裡找到的那個 `while(1)` dispatch loop。**找到它，你就有了往下追一切的起點**（Ch 6「找 entry point 與主迴圈」整章在講怎麼快速定位）。

### 2. 跟 data flow / control flow，不平行掃

**RE**：你選一個關鍵資料（某個 buffer、某個 struct、使用者輸入），用 IDA 的 xref 往前往後追它怎麼流動、被誰改。或者跟一條控制流：這個 `jz` 往哪跳、那個 `call` 進哪。

**Source**：一模一樣，而且工具更爽。「這個 struct 欄位在哪被改」用 cscope/clangd 一秒查到（Ch 8「data flow 追蹤」）；「這個函式誰呼叫、它呼叫誰」用 cscope 反查（Ch 9「控制流與 call graph」）。你在 IDA 按 `X` 看 xref 的那個動作，在 source 上就是 cscope 的 `-3`（找呼叫者）和 clangd 的 find-references。**同一個腦內動作，不同的鍵。**

### 3. 猜 invariant，用來剪枝

**RE**：你看到一段檢查 `cmp eax, 0x10 / jl fail`，猜「這裡有個長度上限 16」。這個猜測（invariant，程式在某點恆為真的條件）幫你理解後面所有邏輯，也幫你**跳過**不相關的分支。

**Source**：讀 code 時你同樣不斷猜 invariant。redis 的 `dictAddRaw`（`src/dict.c`）：

```c
dictEntry *dictAddRaw(dict *d, void *key, dictEntry **existing)
{
    void *position = dictFindPositionForInsert(d, key, existing);
    if (!position) return NULL;          // 猜：position==NULL 代表 key 已存在
    if (d->type->keyDup) key = d->type->keyDup(d, key);
    return dictInsertAtPosition(d, key, position);
}
```

你不用讀 `dictFindPositionForInsert` 就能猜出 invariant：「`position` 為 NULL ⟺ key 已存在（於是不插入、回 NULL）」。這個猜測讓你**不必**跳進 `dictFindPositionForInsert` 就理解 `dictAddRaw` 的契約。而且 source 這裡還佛心給了註解印證你的猜測（`/* Get the position for the new key or NULL if the key already exists. */`）——binary 裡你只能靠 cmp/jz 猜，source 常常直接告訴你。猜 invariant 是收斂的核心武器（Ch 10「假設驅動讀碼」把它系統化）。

### 4. 從殘存線索恢復語意

**RE**：strings、匯入表、常數（magic number、CRC 多項式）、格式化字串——這些是 stripped binary 裡少數沒被抹掉的語意線索。看到 `"Invalid license key"` 你就知道附近有授權檢查。

**Source**：命名、型別、字串、常數同樣是你的線索——而且沒被 strip，全都在。想知道「server 什麼時候算啟動完成」？搜那句人類看得到的字串：

```
$ rg -n "Ready to accept connections" src/*.c
src/server.c:7224:  serverLog(LL_NOTICE,"Ready to accept connections %s", ...);
```

一句 grep 就把你帶到「啟動流程的終點」那一行——這正是 RE 裡「搜 string 定位功能」的手法，在 source 上更好用（Ch 12「grep/ripgrep 的藝術」）。命名更是 source 獨有的富礦：`expireIfNeeded`、`lookupKeyReadOrReply` 這些名字**直接洩漏意圖**，是 binary 被 strip 掉的東西。

### 5. 動靜態結合

**RE**：靜態卡住就上 debugger——下斷點、看實際走哪條路、dump 真實的記憶體。靜態看不出的（間接跳轉目標、實際資料值），動態一跑就現形。

**Source**：一樣。靜態讀不確定「這條路徑到底走不走到」「這個 function pointer 實際指向誰」，就 gdb 斷點跑一次（Ch 18「debugger-driven reading」）、strace/ltrace 看它真正做了什麼 syscall（Ch 19「tracing 讀執行」）。你在 IDA 靜態看半天不如 F5 動態跑一次的經驗，在 source 上完全複製。

### 6. 由外而內、先粗後細

**RE**：先看 section、匯入匯出、函式列表建立全局印象，再挑關鍵函式深入，最後才逐指令啃。你不會一開始就逐指令。

**Source**：先 `cloc` 看規模、看目錄結構、看主要 header 和型別建立架構印象（Ch 5「60 分鐘偵察」、Ch 7「建立架構地圖」），再挑關鍵路徑精讀，最後才逐行啃硬核那 200 行。**先掃、再精讀、再追蹤**——這正是下一章 Ch 4 三種閱讀模式的由來。

## indirection：RE 的「間接跳轉」= source 的「函式指標表」

有一個對照特別值得單獨拉出來，因為它是 RE 和 source reading 都最頭痛的東西：**間接控制流**。

**RE**：你最恨的是 `call [rax]` / `jmp [rdx*8+table]`——間接跳轉，靜態看不出目標。C++ 的 vtable、jump table、callback，反組譯出來都是這種你得動態才能確定目標的東西。

**Source**：同一個惡夢，換個皮。redis 派發指令的核心是（`src/server.c`）：

```c
c->cmd->proc(c);       // server.c:3575  ← 透過函式指標呼叫，靜態看不出實際進哪
```

`proc` 是 `struct redisCommand` 的一個欄位（`redisCommandProc *proc;`，`src/server.h:2356`）。你看著 `c->cmd->proc(c)` 這行，**靜態上完全不知道它會進 `getCommand` 還是 `setCommand` 還是別的**——這就是 binary 裡 `call [rax]` 的 source 版！它由執行期的 `c->cmd` 決定，而 `c->cmd` 又是 `lookupCommand` 根據使用者送來的指令名，去一張命令表裡查出來的。

這種「靠一張表 + 函式指標派發」的 pattern，是 RE 裡 vtable/jump table 的直接對應。攻堅方法也對應：靜態上你去讀那張表（redis 的命令表），把「指令名 → proc 函式」的對應關係抄下來；動態上你 gdb 斷在 `c->cmd->proc(c)` 印 `c->cmd->name`，直接看它這次進哪。**你逆 C++ vtable 的手法，一比一搬過來讀 C 的函式指標派發。** Ch 23「讀懂 indirection」整章就在講這件事。

## 不同：source 比 binary 幸運，但也有它獨有的噪音

搬心法之前，先把兩邊的差異講清楚，免得你把 RE 的悲觀或樂觀錯用。

### source 幸運在哪（線索更多）

- **命名還在**：`expireIfNeeded` 直接說了它幹嘛。binary strip 後這是 `sub_401A20`。
- **型別還在**：`struct redisCommand *cmd` 告訴你這是什麼、有哪些欄位。binary 裡你得自己重建 struct layout。
- **註解可能在**：像 `dictAddRaw` 上面那段契約說明。binary 完全沒有。
- **結構還在**：檔案/模組/函式邊界清楚。binary 裡函式邊界都可能要自己認。

這些就是「你在 RE 上會的，讀 source 一半直接能用，而且更省力」的底氣。

### source 獨有的噪音（RE 反而沒有）

但別以為 source 全是恩惠。它有 binary 沒有的麻煩：

- **人為抽象噪音**：層層 wrapper、interface、依賴注入、template/泛型、巨集展開。binary 把這些全部**編譯掉了**——你逆的是攤平後的機器碼，沒有 `getCommand → getGenericCommand → lookupKeyReadOrReply` 這種三層 delegation 要你手動跳（上一章那個例子）。source 的抽象層是**讀者要自己在腦中攤平**的，這是 binary reverser 不會遇到的稅。
- **註解會騙人**：binary 沒註解，所以不會被註解騙。source 的註解可能是三年前的、跟現在的 code 已經不符——**過時註解比沒註解更危險**，因為它給你錯誤的信心。（信條：註解是線索不是真相，衝突時信 code。）
- **命名會騙人**：`tmp`、`data`、`process()`、被 rename 過但沒改乾淨的舊名——命名是線索但不是保證。binary 沒命名，反而逼你只信行為。

所以正確的心態是：**善用 source 多出來的線索（命名/型別/註解/結構）當快速假設的來源，但用 RE 那套「只信行為、動態驗證」的紀律去核對它們。** 線索多不等於可以偷懶信，只是讓你的假設起點更準。

## 對比與取捨

| 攻堅動作 | binary RE | source reading | 誰更省力 |
|---|---|---|---|
| 找入口 | 定位 `main`/`_start`（IDA 標） | 找真正的 `main`/主迴圈（rg + cscope） | 差不多 |
| 跟資料流 | xref 追 buffer/struct | cscope/clangd 追欄位讀寫 | source（工具更準） |
| 跟控制流 | 追 jmp/call、畫 CFG | 追呼叫關係、call graph | source |
| 猜 invariant | 靠 cmp/jz 推 | 靠 code + 命名 + 註解推 | source（線索多） |
| 恢復語意 | strings / 常數 / 匯入表 | 命名 / 字串 / 型別 / 註解 | source（沒被 strip） |
| 間接控制流 | `call [rax]`、vtable、jump table | 函式指標、`c->cmd->proc(c)`、callback | 一樣痛 |
| 動態驗證 | debugger 下斷 | gdb / strace / ltrace | 一樣 |
| 抽象層 | 已被編譯攤平（無稅） | 要讀者自己攤平（有稅） | binary |
| 被線索誤導 | 幾乎不會（沒命名沒註解） | 會（過時註解、爛命名） | binary |

一句話取捨：**source 給你更多線索（命名/型別/註解），代價是更多噪音（抽象層/過時註解/爛命名）。RE 給你更少線索，但少的都是不會騙你的。** 讀 source 的高手，是用 RE 的懷疑紀律去駕馭 source 的豐富線索。

## 踩雷集錦

1. **錯誤直覺**：「有原始碼就好懂了，逆向那套用不上。」
   **正確認識**：source 只是**少壓了一層**的壓縮產物，意圖/試錯史/為什麼一樣被壓掉了（上一章）。有 source 讓你少啃機器碼，但「反推意圖」這個核心難點原封不動。RE 的攻堅心法不是用不上，是**正好用得上**——它本來就是為「從壓縮產物反推意圖」設計的。

2. **錯誤直覺**：「註解/命名寫著這樣，那就是這樣。」
   **正確認識**：註解會過時、命名會騙人（rename 沒改乾淨、`tmp` 其實很重要、`process()` 到底 process 什麼）。它們是**線索不是真相**。用 RE 那套紀律：把它當假設，然後看行為驗證。衝突時**信 code、信動態行為**，不信註解。

3. **錯誤直覺**：「靜態把 code 讀完就懂了，不用跑。」
   **正確認識**：跟 RE 一樣，間接控制流（函式指標 `c->cmd->proc(c)`、callback、多型）靜態根本看不出實際目標；某條路徑到底走不走到也常常要跑才知道。該上 debugger 就上，別跟 IDA 靜態看半天的錯誤一樣在 source 上重犯（Ch 18、19）。

4. **錯誤直覺**：「我沒做過 binary RE，所以這門課的比喻對我沒用。」
   **正確認識**：反過來也成立——這門課把 RE 的攻堅方法**明講**出來，就算你沒逆過 binary，你學到的是同一套可遷移的攻堅框架（找 entry、跟 flow、猜 invariant、由外而內）。有 RE 底子是加分不是門檻。

## 進階：再往深一層

- **decompiler 就是「binary → 近似 source」**：Ghidra/Hex-Rays 把 binary 反編譯成類 C，本質是**把壓縮產物往回還原一層**——正好走完前面那條光譜的逆向一段。讀 decompiler 輸出（沒命名、型別靠猜、控制流可能還原不完美）和讀爛 source（Ch 30「讀爛 code」）的技巧高度重疊。兩邊互相練，讀碼功力一起漲。

- **source ↔ disassembly 對照是終極驗證**：當 source 用了你吃不準的巨集、UB、編譯器行為時，最硬的核對方式是**編出來看組語**（`objdump -d`），拿 source 和 disassembly 並排。Ch 28 整章在做這件事——這是 RE 技能反哺 source reading 的最直接場景，尤其對 kernel/系統程式的慣例（Ch 27）。

- **「找漏洞式讀碼」是 RE 直覺最值錢的地方**：安全研究者讀 source 找洞（Ch 32），用的正是 RE 的攻堅眼——追使用者可控的 data flow 到危險 sink、猜哪個 invariant 沒被守住、由外而內鎖定 attack surface。這是整門課裡 RE 背景讀者最能發揮、也最能拉開差距的一章。

## 動手練習

1. **把 RE 套路對到 redis**：拿本章「相同的心法」六條，各在 redis 上做一次最小驗證。例如第 1 條——用 `rg -n "aeMain" src/server.c` 找主迴圈；第 4 條——挑一句 `serverLog` 的字串，`rg` 它，看它把你帶到哪個功能點。把六條各做一次，你就完成了一次微型攻堅。

2. **猜 invariant 再驗證**：讀 `src/dict.c` 的 `dictAddRaw`（先**別**讀 `dictFindPositionForInsert`），寫下你猜的 invariant（`position` 什麼時候是 NULL？）。然後才跳進 `dictFindPositionForInsert` 印證。體會「靠猜 invariant 剪枝、不必讀完所有 callee」的省力。

3. **逆一次函式指標派發**：找到 `c->cmd->proc(c)`（`server.c:3575`）。靜態上：`c->cmd` 從哪來？（往上找 `lookupCommand`。）如果你有 gdb（Ch 18 會正式教），試著斷在這行、印 `c->cmd->name`，看你送一個 `GET` 進去時它實際指向誰。這就是逆 vtable 的 source 版。

## 本章重點整理

- source 和 binary 只是同一份意圖在**不同壓縮等級**：source 少壓一層（命名/型別/註解還在），但「反推意圖」的核心難點兩邊相同。讀 source = 逆向工程的簡單版。
- RE 的六條攻堅心法直接可搬：**找 entry、跟 data/control flow、猜 invariant、從命名/字串恢復語意、動靜態結合、由外而內先粗後細**。
- 間接控制流是兩邊共同的惡夢：binary 的 `call [rax]`/vtable = source 的函式指標派發（`c->cmd->proc(c)`）。攻堅法也對應：讀表 + 動態斷點。
- source 幸運在線索多（命名/型別/註解/結構沒被 strip），但獨有噪音（人為抽象層要自己攤平、註解命名會騙人）。
- 正確心態：用 source 多出來的線索當**快速假設來源**，用 RE 的**懷疑紀律**（只信行為、動態驗證）去核對。

## 自我檢核

- [ ] 不看筆記，能不能把「意圖 → source → binary」這條壓縮光譜畫出來，並說明「讀 source 是逆向工程的簡單版」到底簡單在哪？
- [ ] RE 的六條攻堅心法，你能一條條對到 source reading 的對應動作與工具嗎？
- [ ] 為什麼說 `c->cmd->proc(c)` 是 binary 裡 `call [rax]` 的 source 版？靜態和動態各怎麼攻堅它？
- [ ] source 比 binary 多了哪些線索、又多了哪些 binary 沒有的噪音？各舉一例。
- [ ] 註解和命名跟 code 衝突時你信誰？為什麼「過時註解比沒註解更危險」？

## 延伸閱讀

- **Dennis Yurichev,《Reverse Engineering for Beginners》(免費 PDF, beginners.re)，Part I 前幾章**
  - **讀哪裡**：不必全讀。看「怎麼從 disassembly 認出 if/loop/switch/函式呼叫」那幾章。
  - **學到什麼**：把控制流結構在最壓縮形態下的樣子看清楚，回頭讀 source 的控制流會覺得「這也太清楚了」。反向鞏固本章的壓縮光譜觀。
  - **和本章關聯**：直接對應「相同心法」與「source ↔ disassembly」，是 RE 直覺的系統來源。

- **Diomidis Spinellis,《Code Reading: The Open Source Perspective》(Addison-Wesley, 2003), Ch 1–2**
  - **讀哪裡**：第 1、2 章，看他怎麼定義「讀碼」並示範「由外而內、先建全局印象」的策略。
  - **學到什麼**：少數專門講讀 source 方法論的經典。它的「先看大結構再鑽細節」正是本章第 6 條攻堅心法的展開。
  - **和本章關聯**：source reading 這一側的方法論主幹，和 RE 側互為表裡。

- **redis 原始碼 `src/server.c` 的 `main` → `initServer` → `aeMain` 這條線（redis 7.4.0）**
  - **讀哪裡**：不必全懂。順著 `main`（6917 行附近）往下掃到 `aeMain`（7251），只求看出「入口 → 初始化 → 主迴圈」這個骨架。
  - **學到什麼**：把「找 entry、定位主迴圈」從抽象變成手感。這是你之後每個陌生 C 專案第一步要做的事。
  - **和本章關聯**：本章第 1 條攻堅心法的活體標本，也是 Ch 6 的預習。

定調完成——讀碼就是逆向工程，你的攻堅直覺能用。下一章往內轉，看看**你的大腦到底怎麼理解程式**：工作記憶為什麼一下就爆、專家和新手的差距在哪、top-down 和 bottom-up 兩種理解策略何時各自生效。搞懂大腦的機制，前面所有攻堅心法才有了「為什麼有效」的底層解釋。

→ [Ch 3 程式設計師怎麼理解程式](./03-how-programmers-understand-code.md)
