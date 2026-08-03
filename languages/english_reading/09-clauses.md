# Ch 9 — 子句：關係子句、that 子句、分詞構句，以及「子句裡還有子句」

> **目標**：把英文長句裡最會讓你迷路的東西——**嵌入子句（embedded clause）**——系統性拆給你看。讀完這章，你能認出關係子句、that 補語子句、分詞構句、名詞子句四大類，並且在「子句裡還套著子句」的三四層結構裡，追蹤層次而不迷路。這是原理深挖章，我們會拆大量真實風格的長句，寧長勿短。

---

## 為什麼需要這個？

Ch 7 教你找主幹（主詞—動詞—受詞），Ch 8 教你拆名詞片語的堆疊。但這兩章的修飾都還是「片語」等級——一串詞，沒有自己的動詞。

真正把 BBC 新聞句子撐到三四行、把技術文件句子撐到「一句話講完整段邏輯」的，是**子句（clause）**。子句和片語的關鍵差別只有一個：**子句自己有一組主詞和動詞。** 一旦一個句子裡塞進好幾個各自帶動詞的子句，你的工作記憶就開始吃緊——你得同時記住「主句的主詞在等它的動詞」「這個 which 開的子句在講前面哪個名詞」「那個 that 後面又開了一整句」。

這正是 Ch 1 講的「文法沒自動化」最典型的現場：**一句話的字你全認識，卻讀不懂，因為子句的層次把工作記憶塞爆了。** 這一章就是要把「拆子句層次」這件事，從「模糊地感覺句子很長」變成「明確地看見每一層在幹嘛」。看得見層次，你才追得住；追得住，才可能練到自動化。

---

## 先建立直覺

先給你一個貫穿全章的心智模型：**句子是一棵樹，不是一條線。**

你讀句子時眼睛是「從左到右一條線」在掃，但句子的真實結構是**有層次的樹**——主幹是樹幹，每個子句是掛在某根枝上的分枝，分枝上還能再長分枝。你讀不懂長句，往往是因為你**用「讀一條線」的方式去讀「一棵樹」**：把所有詞平鋪成一串，於是分不清哪個詞屬於哪一層。

我們這一章的核心動作，就是把「線」還原成「樹」。用 ASCII 縮排來畫：

```
主幹（樹幹）
├── 修飾 / 子句（第 1 層分枝）
│   └── 子句裡的子句（第 2 層分枝）
│       └── 再一層（第 3 層分枝）
└── 另一個修飾 / 子句（第 1 層分枝）
```

**縮排越深＝嵌入越深＝離主幹越遠。** 讀長句時，你心裡要一直問一個問題：「我現在讀的這個詞，掛在哪一層？」只要答得出來，你就沒迷路。

嵌入（embedding）為什麼會這麼深？因為英文的子句可以**遞迴（recursion）**——一個子句裡可以放另一個子句，那個子句裡又可以再放一個，理論上沒有上限。這是人類語言的核心特性，也是長句難讀的根源。我們先分別認四大類子句，再專門攻「遞迴嵌套怎麼追」。

---

## 核心一：關係子句（relative clause）——最常見、也最會纏住你

**關係子句**是用來修飾名詞的子句——它掛在一個名詞後面，補充說明那個名詞。它由**關係代名詞（relative pronoun）**開頭：`who / whom / which / that / whose`，有時是**關係副詞（relative adverb）**：`where / when / why`。

被修飾的那個名詞，叫**先行詞（antecedent）**。這是拆關係子句的第一要務：**這個 which/who/that，到底在講前面哪個名詞？**

先看一句技術風格的短例，把結構畫出來：

```
The buffer that stores the incoming packets is too small.

主幹：The buffer ......................... is too small.
        └─ 關係子句：that stores the incoming packets
              先行詞 = The buffer
              （在講「這個 buffer 會儲存進來的封包」）
```

注意主幹被關係子句**從中間切開**了：`The buffer` 和它的動詞 `is` 中間，硬塞了一整個關係子句。這是關係子句最會坑你的地方——**它把主詞和動詞拉開**，你讀到 `packets` 時可能已經忘了主詞是 `buffer`、還在等一個主要動詞。拆句時的對策：先跳過關係子句，把主幹接起來（`The buffer ... is too small`），再回頭讀被跳過的部分。

### 限定 vs 非限定：那個逗號決定意思

關係子句分兩種，差別在**有沒有逗號**，而這個差別會改變句子的意思，讀者常忽略。

**限定關係子句（restrictive relative clause）**：沒有逗號。它**限定、篩選**先行詞——把先行詞縮小到「符合這個條件的那些」。拿掉它，句子意思會不完整或改變。

**非限定關係子句（non-restrictive relative clause）**：有逗號隔開。它只是**額外補充**先行詞的資訊，順帶一提。拿掉它，主句意思照樣成立。

看兩句幾乎一樣、意思卻不同的句子：

```
(限定)   The engineers who had signed the agreement were paid.
         → 只有「簽了協議的那些工程師」拿到錢。（暗示有人沒簽，沒拿到）

(非限定) The engineers, who had signed the agreement, were paid.
         → 工程師（全部）都拿到錢；順帶一提他們都簽了協議。
```

一個逗號，一個「只有簽的人拿到」、一個「全部都拿到」。**讀 BBC 新聞時這個區別很要命**：非限定子句常被記者用來塞入「已知的背景事實」，你要能一眼認出「這段逗號夾住的東西是補充，不是主線」，才不會被它岔開。

兩個實用規則幫你辨認：
- **`that` 只能開限定子句**，非限定子句不能用 `that`（要用 `which` / `who`）。所以看到 `, that` 幾乎一定是誤用或別的結構。
- 非限定子句前面那個逗號，是給你的**明確信號**：「接下來是補充，可暫時跳過，先把主句讀完。」

### reduced relative clause：關代被省略，句子看起來「缺了東西」

英文常把關係子句**縮短（reduce）**，省掉關係代名詞、甚至連 be 動詞一起省掉。這叫 **reduced relative clause（縮減關係子句）**。它是讓長句「看起來缺主詞或動詞、其實沒缺」的頭號元兇。

有兩種常見的縮減：

**縮減一：省掉「關代 + be」，留下 -ing 或 -ed 分詞。**

```
完整：  The report which was released yesterday warned of a slowdown.
縮減：  The report released yesterday warned of a slowdown.
                    └────────────┘
                    這是縮減關係子句（省了 which was）
                    = 「昨天發布的那份報告」

主幹：The report ................. warned of a slowdown.
        └─ (which was) released yesterday   ← 修飾 The report
```

讀者常在這裡出錯：看到 `The report released...`，誤以為 `released` 是主要動詞（「報告釋放了……？」），於是整句解讀歪掉。**正解是：`released yesterday` 是縮減關係子句在修飾 `report`，真正的主要動詞是後面的 `warned`。** 判斷訣竅：如果一個 -ed 詞後面又冒出另一個動詞（這裡是 `warned`），前面那個 -ed 很可能是縮減關係子句（過去分詞當修飾），不是主要動詞。

**縮減二：受詞關係子句直接省掉關代（zero relative）。**

```
完整：  The vulnerability that the team discovered was critical.
縮減：  The vulnerability the team discovered was critical.
                          └──────────────┘
                          關代 that 被省掉了，直接接一個新主詞 the team

主幹：The vulnerability .................... was critical.
        └─ (that) the team discovered   ← 修飾 The vulnerability
```

這種句子讀起來像「兩個名詞撞在一起」（`The vulnerability the team`），很多人在這裡卡住。**只要看到「名詞 + 名詞（或名詞 + 代名詞）直接相鄰、中間沒有連接詞」，就要警覺：可能有個被省掉的 that，這是一個 zero relative。** 心裡把 that 補回去，結構就清楚了。

---

## 核心二：that 補語子句（that-complement clause）——「一整句」當一個東西用

關係子句是**修飾名詞**；that 補語子句完全不同——它是**把一整個句子，當成一個名詞來用**，通常當某個動詞的受詞。

```
The researchers found that the encryption had been broken.

主幹：The researchers found [受詞]
                                └─ that the encryption had been broken
                                   （整個 that 子句 = found 的受詞）
```

`found` 的受詞不是一個名詞，而是**「the encryption had been broken」這整件事**。that 在這裡不是關係代名詞（它不指代任何先行詞），而是**補語連接詞（complementizer）**——它的唯一作用是「宣告：後面接一整個子句當受詞」。

怎麼跟關係子句的 that 區分？看 that 後面：
- **that 後面的子句「不缺東西」**（主詞、動詞、受詞都齊全）→ 這是 **that 補語子句**。（`the encryption had been broken` 是完整一句）
- **that 後面的子句「缺一個成分」**（缺主詞或缺受詞，因為那個成分就是先行詞）→ 這是 **關係子句**。（`that stores the packets` 缺主詞，主詞是先行詞 buffer）

這個「缺不缺成分」的判準非常實用，記起來。

that 補語子句也常被省略：

```
The team confirmed the patch was effective.
                   └─ (that) the patch was effective
```

`confirmed` 後面直接接 `the patch was effective`，中間的 that 被省了。這又是一個「兩個東西撞在一起」的現場——`confirmed the patch`，你可能誤以為 `the patch` 是 confirmed 的受詞（「確認了這個 patch」），讀到 `was effective` 才發現不對。**對策同前：動詞後面接了一個看起來完整的子句，多半有個省略的 that，這是補語子句。**

that 補語子句也能掛在名詞或形容詞後面：

```
名詞後： the claim that the system is secure   （the claim = 這個主張，內容是「系統安全」）
形容詞後：aware that the risk was real          （aware of 什麼？「風險是真的」這件事）
```

---

## 核心三：分詞構句（participle clause）——沒有主詞、以 -ing 或 -ed 開頭的子句

**分詞構句**是一種**非限定形式子句（non-finite clause）**——它沒有自己明講的主詞、動詞也不隨時態變化，靠一個**分詞（participle）**開頭：現在分詞 `-ing`，或過去分詞 `-ed`（不規則動詞則是其過去分詞形）。它常放在句首或句尾，用逗號和主句隔開，用來表達**時間、原因、條件、伴隨動作**等關係。

分詞構句的「隱藏主詞」，預設是**主句的主詞**。這是拆它的關鍵。

先看 -ing 開頭的（現在分詞，通常表主動、進行、或原因）：

```
Facing mounting criticism, the minister resigned on Tuesday.
└──────────────────────┘
分詞構句（-ing）              主句：the minister resigned on Tuesday

隱藏主詞 = the minister（誰 facing criticism？部長）
意思 ≈ Because he was facing mounting criticism, the minister resigned.
      （由於面臨越來越多的批評，部長辭職了）
```

再看 -ed 開頭的（過去分詞，通常表被動）：

```
Released in 2021, the tool quickly became an industry standard.
└──────────────┘
分詞構句（-ed，被動）           主句：the tool quickly became an industry standard

隱藏主詞 = the tool（什麼被 released？這個工具）
意思 ≈ The tool, which was released in 2021, quickly became an industry standard.
      （這工具 2021 年發布，很快成為業界標準）
```

**新聞英文極度愛用句首分詞構句**，因為它能把一整個背景／原因壓縮成短短一截、掛在句首，讓主句直接切入重點。學會一看到句首的 `-ing,` 或 `-ed,` 就知道「這是背景，主句在逗號後面」，你讀新聞會順很多。

分詞構句也常出現在句尾，表伴隨或結果：

```
The company laid off 200 staff, citing falling revenue.
                              └──────────────────┘
                              -ing 分詞構句（伴隨／說明）
                              = 「並援引營收下滑（作為理由）」
```

技術文件裡的分詞構句常表條件或方法：

```
Given the constraints above, the algorithm runs in linear time.
└──────────────────────┘
分詞構句（Given = 過去分詞，表條件）= 「在上述限制下」
```

`Given ...` 是技術與學術英文的固定句首套路，意思是「在……的情況下 / 考慮到……」，看到就當它是條件狀語。

---

## 核心四：名詞子句（wh- 名詞子句）——how / what / whether 開頭當名詞用

還有一類子句，由 `what / how / why / where / when / whether / if` 開頭，整個當**名詞**用（當主詞、受詞、或介系詞的受詞）。這叫**名詞子句（nominal clause）**或 **wh- 子句**。

```
How the malware spreads remains unclear.
└──────────────────────┘
名詞子句當「主詞」          主要動詞：remains
= 「這惡意程式如何散播」這件事，remains unclear（仍不清楚）
```

```
Investigators are trying to determine whether the breach was internal.
                                       └──────────────────────────┘
                                       名詞子句當 determine 的受詞
                                       whether = 「是否」
= 調查人員正試圖判定「這次入侵是否來自內部」
```

**`whether` 和 `if` 都表「是否」**，讀時要小心 `if` 的兩義：它可能是「是否」（名詞子句），也可能是「如果」（條件狀語子句）。靠位置判斷——當受詞的是「是否」，當狀語（可搬到句首、表條件）的是「如果」。

這類子句與 that 補語子句是近親，差別在開頭詞攜帶的意思：that 只宣告「後面是一整句」，而 what/how/whether 本身還帶「什麼／如何／是否」的疑問內容。

---

## 機制小節：嵌入與遞迴——「子句裡還有子句」怎麼追

前面四類是「零件」。真正的難句，是把這些零件**層層嵌套**起來。這一節專門講怎麼在多層嵌套裡不迷路。這是全章的重心。

### 為什麼會嵌套：遞迴

**遞迴（recursion）**指「同一種結構，可以放進它自己裡面」。子句可以放進子句，是英文（所有人類語言）的核心能力。舉個逐步長大的例子，感受它怎麼堆疊：

```
第 0 層： The system failed.
第 1 層： The system that we deployed failed.
                     └─ 關係子句
第 2 層： The system that we deployed last week failed.
第 2 層： The system that the team [which owns the pipeline] deployed failed.
                                  └─ 關係子句裡，又嵌了一個關係子句
```

最後一句，`the team` 後面的 `which owns the pipeline` 是**嵌在關係子句裡的關係子句**——這就是遞迴。理論上你可以無限套下去，實務上人腦大概三四層就開始吃力（又是 Ch 1 的工作記憶限制）。

### 追蹤法：一次只解決一層，用「壓棧／出棧」的方式讀

拆多層嵌套句，最有效的心法是把它當**堆疊（stack）**來處理（工程師應該對這個資料結構很熟）：

1. 沿著句子往下讀，每遇到一個「開子句的信號」（who/which/that/-ing/-ed/what…），就**壓一層（push）**：記住「主句還沒講完，我先進到一個子句裡」。
2. 一個子句講完（它的動詞、受詞都齊了），就**出一層（pop）**：回到上一層繼續。
3. 心裡永遠知道「我現在在第幾層」。

我們用一個技術風格的三層句實地走一遍：

```
原句：
The tool that the researcher who first reported the bug had written
crashed on startup.
```

一步步壓棧：

```
讀到 "The tool ..."          → 主句主詞 = The tool，等一個主要動詞【第 0 層】
讀到 "that ..."              → push：進入關係子句（修飾 The tool）【第 1 層】
        主詞 = the researcher，等這層的動詞
讀到 "who ..."              → push：又進一個關係子句（修飾 the researcher）【第 2 層】
        who first reported the bug   ← 這層完整了（動詞 reported、受詞 the bug）
                             → pop：回到第 1 層
第 1 層繼續：the researcher ... had written  ← 第 1 層動詞 had written，完整
                             → pop：回到第 0 層
第 0 層繼續：The tool ... crashed on startup ← 主要動詞 crashed，全句完整
```

畫成樹：

```
The tool ...................................... crashed on startup.   ← 主幹
   └─ that the researcher ......... had written                       ← 第 1 層（修飾 tool）
          └─ who first reported the bug                               ← 第 2 層（修飾 researcher）
```

讀懂了嗎？拆完就清楚了：「那個工具當機了；哪個工具？就是『那位研究員寫的』工具；哪位研究員？就是『最早回報這個 bug 的』那位。」

**注意一個殺傷力極大的現象：動詞的堆疊。** 這句話中間出現 `... had written crashed ...`——兩個動詞連著，`had written` 是第 1 層的、`crashed` 是主句的。當多個子句同時把「動詞」推遲到後面，你會讀到**一串動詞連珠炮**（英文語言學稱這種主詞和動詞被中間嵌入拉得很開的現象為 center-embedding，中間嵌入），這是長句最難的一種。對策就是上面的壓棧法：認出每個動詞屬於哪一層，配對回它自己的主詞。

### 一個標點與連接詞的路標整理

嵌套句裡，這些詞是你的「進出層」路標，讀時盯著它們：

- **who / whom / whose / which / that / where / when** → 通常在開一個關係子句（往下一層）
- **that（動詞後、且後面子句完整）** → that 補語子句（往下一層）
- **what / how / why / whether / if** → 名詞子句（往下一層）
- **句首或句尾的 `-ing,` / `-ed,`（有逗號）** → 分詞構句（一層背景／伴隨）
- **逗號** → 常標記「一層結束」或「非限定補充的邊界」，是你 pop 的好時機

---

## 對比與取捨

把四大類子句 + 縮減形式整理成一張辨識表，這是本章最該記熟的東西：

| 結構 | 開頭信號 | 作用 | 關鍵辨識法 | 例 |
|---|---|---|---|---|
| **限定關係子句** | who/which/that（無逗號） | 修飾並篩選名詞 | that 後子句**缺**一個成分 | the bug **that we found** |
| **非限定關係子句** | , who / , which（有逗號） | 額外補充名詞 | 逗號夾住，拿掉主句仍成立 | the CEO**, who resigned,** ... |
| **that 補語子句** | that（動詞/名詞後） | 一整句當名詞（受詞等） | that 後子句**不缺**成分 | found **that it failed** |
| **分詞構句** | 句首/尾 -ing 或 -ed | 背景／原因／伴隨 | 沒明講主詞，主詞=主句主詞 | **Facing criticism,** he quit |
| **wh- 名詞子句** | what/how/whether/if | 一整句當名詞 | 開頭詞帶「什麼/如何/是否」 | **whether it was safe** |
| **縮減關係子句** | 名詞後直接接 -ing/-ed 或名詞 | 修飾名詞（省了關代） | -ed 後還有另一個主要動詞 | the report **released** yesterday |

取捨與提醒：

- **不要急著在讀第一遍就分類到位。** 熟練前，先做最省力的一件事：**跳過所有子句，把主幹（主詞—主要動詞—受詞）接起來讀懂**，抓到句子骨架後，再一層一層回頭補子句。骨架對了，理解就不會整個歪掉。
- **逗號是你的朋友。** 有逗號的子句（非限定關係子句、分詞構句）幾乎都可以「先跳過、讀完主句再回來」，因為它們是補充，不是主線。優先利用逗號來降負荷。
- **省略是你的敵人。** 最難的不是有標記的子句，是被省略掉關代／that 的**縮減與 zero 形式**——它們讓句子「看起來缺零件」。看到「兩個名詞硬相鄰」「動詞後直接接一個完整子句」「-ed 後又冒出另一個動詞」，就要立刻警覺有省略。

---

## 踩雷集錦

1. **把縮減關係子句的 -ed 誤當主要動詞。**
   句子 `The data collected during the trial showed a clear trend.` 很多人讀成「資料收集了（during the trial）……然後 showed？」整個亂掉。
   → 正解：`collected during the trial` 是縮減關係子句（= which was collected...），修飾 `The data`；主要動詞是後面的 `showed`。**判準：一個 -ed 詞後面又出現另一個動詞（showed），前面那個 -ed 極可能是修飾（過去分詞），不是主要動詞。** 主幹是 `The data ... showed a clear trend`。

2. **分不清 that 是關係子句還是補語子句，於是誤判先行詞。**
   `The evidence that the system had been compromised was overwhelming.` 有人把 that 當關係代名詞，去找它修飾 evidence 的哪個缺口——找不到，因為根本沒缺。
   → 正解：that 後面的 `the system had been compromised` 是完整一句，所以這是 **that 補語子句**，內容是「系統已被入侵」這件事，用來說明 evidence 的內容。**判準：that 後子句不缺成分 → 補語子句；缺成分 → 關係子句。**

3. **忽略逗號，把非限定子句當成限定子句，讀出相反的意思。**
   `The employees, who were warned in advance, evacuated safely.`（有逗號）意思是「員工（全體）都安全撤離；順帶一提他們都事先被警告了」。若誤讀成無逗號的限定子句，會變成「只有事先被警告的那些員工才安全撤離」——暗示還有沒被警告、沒撤離的人，意思完全不同。
   → 對策：**讀到逗號夾住的 who/which，先在心裡標記「這是補充、非篩選」**，別把它當條件。

4. **在多層嵌套裡「弄丟主句的主詞」。**
   長句 `The framework that the vendor we had partnered with recommended turned out to be obsolete.` 中間 `the vendor we had partnered with` 又是個 zero relative，一堆名詞和動詞連珠炮，讀到最後忘了主句主詞是 `The framework`。
   → 對策：用壓棧法，遇子句就 push、記住「主句還在等它的主要動詞」。這句主幹是 `The framework ... turned out to be obsolete`，中間全是修飾 framework 的兩層嵌套關係子句。**先把主幹拎出來，別讓中間的嵌套沖走主詞。**

5. **把 `if` 一律讀成「如果」。**
   `The auditors will check if the logs were tampered with.` 有人讀成「如果日誌被竄改，稽核員就會檢查」——邏輯很怪。
   → 正解：這裡 `if` 是 whether 的口語替代，開的是**名詞子句**當 check 的受詞，意思是「稽核員會查明『日誌是否被竄改』」。**判準：if 子句若當某動詞的受詞（無法搬到句首），是「是否」；若當條件狀語（可搬到句首、可加 then），才是「如果」。**

---

## 進階：再往深一層

- **限定 vs 非限定的深層本質是「篩選 vs 補充」，不只是逗號。** *The Cambridge Grammar of the English Language*（Huddleston & Pullum）把這組區別稱為 **integrated（整合型）vs supplementary（補充型）**relative。integrated（= 限定）的子句和先行詞「合成一個完整概念」，你不能拿掉；supplementary（= 非限定）則是「插入的、附帶的評論」，語調上也會停頓。理解到這個層次，你甚至能處理逗號被記者漏標、或標點模稜兩可的真實句子——靠「這子句是在幫我鎖定是哪一個，還是只是順帶補一句」來判斷，而不是死盯逗號。

- **which 的先行詞可以是「一整句」，不只是一個名詞。** `The server crashed during peak hours, which cost the company millions.` 這裡的 which 不指代某個名詞，而是指代**前面整件事**（伺服器在尖峰當機這件事）。這種「句子先行詞的非限定 which」在新聞和技術寫作裡很常見，用來對前一個陳述追加評論或後果。看到 `, which` 而前面找不到單一名詞先行詞時，就往「它指整句」想。

- **center-embedding（中間嵌入）為什麼特別難，是有認知科學根據的。** 語言學經典例子 `The rat the cat the dog chased killed ate the malt.`（三層中間嵌入）幾乎沒有母語者能即時讀懂——不是文法錯，是**人腦的堆疊深度撐不住**。這印證 Ch 1：難句難不在字，在工作記憶。好消息是：真實的好新聞與技術寫作，作者通常會**刻意避免**深度 center-embedding（改用非限定子句、分句、或分詞構句往句尾展開），所以你遇到的多半是「往右邊長」（right-branching）的句子，比中間嵌入好追得多。認得出哪種嵌入，你就知道該不該多花力氣。

- **finite vs non-finite 的區分能幫你預測子句行為。** 關係子句、that 補語、wh- 名詞子句通常是 **finite（限定形式）**——有隨時態變化的動詞、常有明講的主詞。分詞構句、不定詞子句（to-infinitive）是 **non-finite（非限定形式）**——動詞不變時態、主詞常省略。non-finite 子句更精簡、更常用來壓縮資訊（新聞與技術文都愛），代價是「主詞是誰」要你自己補（預設是主句主詞）。認出一個子句是 non-finite，你就知道要主動去找它的隱藏主詞。

---

## 動手練習

以下句子刻意做成真實新聞／技術風格。請對每一句：(a) 畫出 ASCII 樹，標出主幹與各層子句；(b) 說出每個子句是哪一類；(c) 寫出中文大意。做完再看參考。

1. `The algorithm that the paper describes runs in constant time.`

2. `The minister, who had denied the allegations for weeks, resigned on Friday.`（注意逗號）

3. `Investigators confirmed that the fire had started in the basement.`

4. `Facing a deadline, the team shipped the release without full testing.`（句首分詞構句）

5. `The library the developers had chosen turned out to have a critical flaw that no one had noticed.`（兩層＋一個 zero relative）

<details>
<summary>參考拆解</summary>

**1.**
```
The algorithm ..................... runs in constant time.   ← 主幹
   └─ that the paper describes                               ← 限定關係子句（修飾 algorithm）
      （that 後缺受詞：describes 什麼？= algorithm，所以是關係子句）
```
大意：這篇論文所描述的演算法，以常數時間執行。

**2.**
```
The minister ......................................... resigned on Friday.   ← 主幹
   └─ , who had denied the allegations for weeks,                             ← 非限定關係子句
      （逗號夾住 = 補充，拿掉主句仍成立）
```
大意：這位部長週五辭職了；順帶一提，他數週來一直否認那些指控。

**3.**
```
Investigators confirmed [受詞]                       ← 主幹
                  └─ that the fire had started in the basement   ← that 補語子句
                     （that 後子句完整，不缺成分 → 補語子句）
```
大意：調查人員證實，火是從地下室燒起來的。

**4.**
```
Facing a deadline,  the team shipped the release without full testing.
└──────────────┘   └──────────── 主幹 ────────────────────────────┘
分詞構句（-ing，表原因／背景，隱藏主詞 = the team）
```
大意：面臨截止期限，這個團隊沒做完整測試就把版本推出去了。

**5.**（最難，兩層＋zero relative）
```
The library ................................. turned out to have a critical flaw ...   ← 主幹
   └─ (that) the developers had chosen                                                 ← zero relative（修飾 library；省了 that）
      主要動詞：turned out
                                          └─ a critical flaw that no one had noticed   ← 限定關係子句（修飾 flaw）
```
壓棧走法：`The library`（主句主詞，等主要動詞）→ 撞到 `the developers`（名詞相鄰！有 zero relative，push 進第 1 層修飾 library）→ 第 1 層 `(that) the developers had chosen` 完整，pop → 回主句，主要動詞 `turned out to have a critical flaw` → 又撞到 `that no one had noticed`（push，修飾 flaw）。
大意：開發者選用的那個函式庫，結果有一個誰都沒注意到的嚴重缺陷。

</details>

---

## 本章重點整理

- **子句和片語的差別**：子句自己有一組主詞＋動詞。長句難讀，主因是**多個帶動詞的子句嵌套**把工作記憶塞爆。
- **四大類子句**：關係子句（修飾名詞）、that 補語子句（一整句當名詞用、當受詞）、分詞構句（-ing/-ed 開頭、隱藏主詞=主句主詞、表背景/原因/伴隨）、wh- 名詞子句（what/how/whether… 當名詞用）。
- **限定 vs 非限定關係子句**由逗號區分，意思不同：限定是**篩選**（只有符合條件的），非限定是**補充**（順帶一提）。that 只能開限定子句。
- **縮減關係子句是頭號陷阱**：省了關代（甚至 be），留下 -ing/-ed 或直接名詞相鄰（zero relative），讓句子「看起來缺零件」。**-ed 後又出現另一個動詞 → 前面 -ed 多半是修飾，不是主要動詞。**
- **分辨 that 兩用**：that 後子句**缺成分**→關係子句；**不缺成分**→補語子句。
- **嵌套追蹤用壓棧法**：遇開子句信號就 push、記住主句還在等主要動詞；子句講完就 pop。永遠知道「現在在第幾層」。
- **通用心法**：先跳過子句把主幹接起來讀懂，再一層層回補；善用逗號（補充可先跳過）；警惕省略（讓句子看似缺件）。

---

## 自我檢核

不看上文，主動回想：

- [ ] 我能說出子句和片語的關鍵差別嗎？
- [ ] 我能用「that 後子句缺不缺成分」來區分關係子句和補語子句嗎？
- [ ] 我能講出限定 vs 非限定關係子句的意思差別，以及靠什麼標點辨認嗎？
- [ ] 看到 `The report released yesterday warned...` 我知道 released 不是主要動詞、warned 才是嗎？為什麼？
- [ ] 遇到句首 `Facing ...,` 或 `Released ...,` 我知道那是分詞構句、隱藏主詞是主句主詞嗎？
- [ ] 拆三層嵌套句時，我能用壓棧法一層層 push/pop、不弄丟主句主詞嗎？

---

## 延伸閱讀

- **[Cambridge Dictionary — Grammar: Relative clauses](https://dictionary.cambridge.org/grammar/british-grammar/relative-clauses)** — 關係子句（含限定/非限定、reduced relative）最清楚好查的免費權威解說，每個規則都配例句。讀不動長篇時，先讀這個。
- **Michael Swan, *Practical English Usage*（4th ed., Oxford）** — 查「relative clauses」「participle clauses」「that-clauses」三個條目。Swan 專治「規則的例外與真實用法」，你讀真實新聞碰到的怪句子，答案常在這本。
- **[Purdue OWL — Relative Pronouns / Participles](https://owl.purdue.edu/owl/general_writing/mechanics/index.html)** — 從寫作角度講子句與分詞，反過來幫你理解「作者為什麼這樣組句」，讀時就更能預測結構。
- **Huddleston & Pullum, *The Cambridge Grammar of the English Language*（2002）** — 想深挖 integrated vs supplementary relative、finite vs non-finite 的權威（學術等級，選讀相關章節即可）。上面「進階」小節的說法都出自這裡。

你現在會拆四大類子句、也會在嵌套裡壓棧追層次了。但真實的難句還有一批「非結構性」的絆腳石——被動語態把施事者藏起來、倒裝打亂語序、cleft 句改變重點、長距離依賴把相關的兩個詞拉到句子兩端。下一章我們把這些最會絆倒讀者的結構一次解決，並且對比新聞英文與技術英文各自的「句構指紋」。

→ [下一章：難句拆解——被動、倒裝、cleft、新聞 vs 技術句構](./10-hard-sentence-dissection.md)
