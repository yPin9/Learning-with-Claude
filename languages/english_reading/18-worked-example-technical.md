# Ch 18 — 精讀示範：逐段拆一篇技術原文

> **目標**：把同一套精讀 SOP（Ch 13）套到**完全不同的文體**上——一段技術原文（man page／RFC／官方文件風格）。技術文的難點和新聞完全相反：新聞難在言外之意，技術文難在**濃縮的資訊密度與精確的規範語氣**。這一章我們拆解技術文特有的五大難點（名詞化、被動、條件句、被嚴格定義的術語、RFC 的 MUST/SHOULD/MAY），並誠實示範一件事：**你的領域知識在哪裡幫你飛、又在哪裡完全幫不上（純語言的敘事段落）**。這是實戰精讀第二場示範，寧長勿短。

---

## 為什麼需要這個？

你是技術背景的讀者。你可能已經發現一個現象：**同樣英文程度，你讀技術文比讀新聞順得多。** 這不是錯覺，也不是你英文變好了——是**領域知識在替你補洞**。當你讀 `The function returns a null pointer when allocation fails`，就算你漏看幾個字，你的工程直覺也知道「配置失敗回傳 null」，因為這是你熟悉的概念。領域知識像一張安全網，接住你漏掉的語言資訊。

但這張網有破洞，而且破洞的位置很關鍵。**技術文裡總有一些段落，講的不是你熟的機制，而是純粹的敘事、動機、限制條件、歷史脈絡——那些段落，你的領域知識完全接不住，你得靠純語言能力硬讀。** 很多工程師的閱讀能力就卡在這裡：熟悉的技術描述讀得飛快，一遇到「為什麼要這樣設計」「這個限制在什麼情況下成立」的敘事段落就當機，還誤以為是「這段太難」，其實是**你一直靠領域知識代償、從沒真的練純語言閱讀**。

這一章要做兩件事：一是把精讀 SOP 套到技術文上、拆解技術文特有的難點；二是**明確標出「哪裡是領域知識幫你、哪裡是你必須靠語言硬讀」**，讓你看清自己閱讀能力的真實邊界。看清邊界，才知道該練哪裡。

---

## 先建立直覺

技術文和新聞，難點幾乎是鏡像的：

```
新聞：句子相對短，難在「言外之意」（attribution 態度、被動的政治意涵）
技術：字面就是全部（沒有言外之意），難在「資訊密度」——
      一句話塞進大量精確條件，且每個字都算數，不能猜、不能略過
```

**技術文最關鍵的直覺：每個字都是規範，不能靠猜。** 新聞裡你猜錯一個形容詞的褒貶，大意還在；技術文裡你把 `MUST` 讀成 `SHOULD`、把 `unless` 讀成 `if`、把 `the buffer`（特定那個）讀成 `a buffer`（任意一個），意思就**根本性地錯了**，寫出來的程式會壞。所以技術文的精讀，慢是應該的、精確是必須的。

技術文的五大難點，我們一個一個在真實風格的段落上拆：

```
1. 名詞化（nominalization）——把動作壓成名詞，句子變抽象
2. 被動語態——聚焦系統行為，隱藏執行者
3. 條件句——if / when / unless / provided that，規範「什麼情況下成立」
4. 被嚴格定義的術語——文件內部給某個字下了精確定義，不能用日常意思讀
5. RFC 的規範動詞——MUST / SHOULD / MAY，有嚴格定義的要求等級（RFC 2119）
```

下面用兩段代表性技術原文，把這五點與「領域知識的邊界」一起演一遍。

---

## 我們要精讀的段落

> **誠實聲明**：下面兩段是我們**自己撰寫的、貼近真實技術文件風格的代表性示範段落**（一段 man page 風、一段 RFC 風），不是任何一份真實文件的原文。理由同 Ch 17：避免大段複製受版權／授權限制的文件，並刻意把技術文的典型難點集中在一處示範。**英文本身完全自然、地道、文法與技術描述皆正確**，句型與用詞你在真實 man page / RFC 裡會讀到幾乎一樣的。RFC 的規範動詞 MUST/SHOULD/MAY 定義出自真實的 **RFC 2119**（延伸閱讀有連結），關鍵詞是真實的。

### 段落 A（man page 風，假想主題：某個 read-like 系統呼叫）

```
On success, the number of bytes read is returned. It is not an
error if this number is smaller than the number of bytes requested;
this may happen, for example, because fewer bytes are actually
available right now, or because the call was interrupted by a
signal. On error, -1 is returned and errno is set to indicate the
error. The behaviour is undefined if the buffer overlaps with the
region being written to.
```

### 段落 B（RFC 風，假想主題：某個協定的欄位驗證規範）

```
A conforming implementation MUST validate the length field before
allocating the buffer. If the declared length exceeds the maximum
permitted value, the implementation MUST reject the message and
SHOULD log the event. Implementations MAY impose a stricter limit,
provided that the limit is documented. Failure to validate the
length field before allocation has been the root cause of numerous
memory-corruption vulnerabilities.
```

段落 A 全是你熟的系統呼叫語意，你的領域知識會飛。段落 B 前三句也是你熟的規範描述，但**最後一句是純敘事**——我們會在那句停下來，示範「領域知識在這裡幫不上」。

---

## 段落 A：man page 風——領域知識飛起來的地方

### 第 1 遍：抓大意

技術文的第一遍和新聞一樣：快掃、抓結構。man page 的 `RETURN VALUE` 段有固定套路——**成功回傳什麼、失敗回傳什麼、有什麼特殊情況**。掃一眼：

```
成功 → 回傳讀到的位元組數
特殊 → 讀到的比要求的少，「不是錯誤」（並給兩個原因）
失敗 → 回傳 -1，設 errno
警告 → buffer 和寫入區重疊時，行為未定義
```

**這一遍你的領域知識全開**：只要你寫過 C、用過 `read()`，這四點你幾乎是「認出」而非「讀出」的——你的大腦拿英文當提示，用既有知識填滿細節。這正是技術背景讀者的最大優勢。

### 第 2 遍：逐句拆——技術難點在哪

#### 句 1：`On success, the number of bytes read is returned.`

- **被動**：`is returned`（被回傳）。誰回傳的？系統呼叫。技術文用被動聚焦「回傳這個行為」，不點名執行者——這是規格書標準語體，**不像新聞那樣有迴避責任的意涵**，純粹是文體慣例。
- **`the number of bytes read`**：注意 `read` 是過去分詞後置修飾 `bytes`（＝ the number of bytes that were read，被讀取的位元組數），不是主要動詞。這是 Ch 8/9 的縮減關係子句。**技術背景讓你一眼知道意思，但語言結構上這是個容易誤讀的點**——若不熟結構，可能把 `read` 當成句子的動詞。

#### 句 2：`It is not an error if this number is smaller than the number of bytes requested; this may happen, for example, because ... or because ...`

- **這是條件句**：`It is not an error if ...`（若……則不算錯誤）。技術文的 `if` 是**規範性**的——它精確界定「在什麼條件下，某個判斷成立」。這裡界定的是：**讀到的比要求的少，不算錯誤**。這是關鍵規範（很多 bug 來自誤以為「沒讀滿 = 出錯」）。
- **`the number of bytes requested`**：又一個過去分詞後置（requested 修飾 bytes）。和句 1 的 `bytes read` 對稱。
- **`this may happen because A or because B`**：兩個並列的原因子句。`may` 在這裡是**可能性**（可能發生），不是 RFC 的規範動詞——別和段落 B 的 `MAY` 混淆（見下方踩雷）。

**中文大意**：成功時回傳讀到的位元組數；這個數字比要求的少並不算錯誤——可能因為當下可用的位元組較少，或呼叫被訊號中斷。

#### 句 3：`On error, -1 is returned and errno is set to indicate the error.`

- 兩個並列被動：`-1 is returned` and `errno is set`。標準 man page 錯誤回傳套路。領域知識讓你秒懂。

#### 句 4：`The behaviour is undefined if the buffer overlaps with the region being written to.`

- **`undefined behaviour`（未定義行為，UB）是被文件/標準嚴格定義的術語**——它不是「行為不清楚」的日常意思，而是 C 標準裡的專有名詞：**編譯器可以做任何事，程式沒有任何保證**。若你不知道 UB 是專有名詞，會嚴重低估這句的嚴重性。這是「被嚴格定義的術語」難點的典型。
- **條件句 + `the buffer`（定冠詞）**：`the buffer` 指「（此呼叫的）那個緩衝區」，特定的，不是任意 buffer。定冠詞在技術文裡**指涉精確**，不能當可有可無的冠詞略過。
- **`the region being written to`**：`being written to` 是進行式被動的分詞（正在被寫入的區域）。`write to` 的介系詞 `to` 留在句尾——這是 Ch 9 的「介系詞擱淺」，別漏掉 `to`。

**中文大意**：若緩衝區與正被寫入的區域重疊，行為未定義（＝毫無保證，可能任意壞掉）。

**段落 A 小結**：這段幾乎每一句你的領域知識都幫得上，讀起來飛快。**但注意兩個純語言的陷阱藏在裡面**——`bytes read`/`bytes requested` 的過去分詞後置、`undefined behaviour` 是專有名詞。領域知識讓你「懂意思」，但**若要精確到寫規格、寫實作，你得靠語言能力確認每個結構**。

---

## 段落 B：RFC 風——規範語氣，與領域知識的邊界

### RFC 規範動詞：MUST / SHOULD / MAY（來自 RFC 2119）

先把這組動詞講清楚，因為它是讀 RFC/標準文件的鑰匙。**RFC 2119** 給這幾個關鍵詞下了嚴格定義：

| 關鍵詞 | 定義 | 白話 | 違反的後果 |
|---|---|---|---|
| `MUST` / `REQUIRED` / `SHALL` | 絕對要求 | **必須**，沒有例外 | 不符合規範，實作是錯的 |
| `MUST NOT` / `SHALL NOT` | 絕對禁止 | **絕對不可** | 不符合規範 |
| `SHOULD` / `RECOMMENDED` | 強烈建議 | **應該**（除非有充分理由且理解後果，否則要照做） | 通常算次等實作，但不算違規 |
| `SHOULD NOT` | 強烈不建議 | **不應該**（除非有充分理由） | 同上 |
| `MAY` / `OPTIONAL` | 可選 | **可以**做也可以不做，都符合規範 | 無 |

**這是嚴格的分級，不是同義字。** 把 `MUST` 讀成 `SHOULD`（把「必須」當「建議」）、或把 `MAY` 讀成 `MUST`（把「可選」當「必須」），你的實作就會不符規範或過度限制。讀 RFC 時，這幾個大寫詞是**全文最該精確對待的字**。

### 第 1 遍：抓大意

```
規則 1 → 實作 MUST 在配置 buffer 前驗證 length 欄位
規則 2 → 若宣告的 length 超過上限 → MUST 拒絕訊息、SHOULD 記錄
規則 3 → 實作 MAY 設更嚴格的上限，前提是要有文件記載
最後一句 → 「沒在配置前驗證 length」是眾多記憶體損毀漏洞的根源（← 純敘事！）
```

### 第 2 遍：逐句拆

#### 句 1：`A conforming implementation MUST validate the length field before allocating the buffer.`

- **`MUST`（絕對要求）**：這是硬性規範——不驗證就不符合協定。
- **`a conforming implementation`**：`conforming`（符合規範的）是 RFC 常用限定詞——這條規則只約束「想符合規範」的實作。
- **`before allocating the buffer`**：時間條件（**配置之前**）。順序在這裡是規範的一部分——先驗證、再配置，順序反了就是那個著名的漏洞。

#### 句 2：`If the declared length exceeds the maximum permitted value, the implementation MUST reject the message and SHOULD log the event.`

- **條件句** `If ... exceeds ...`：界定觸發條件（宣告長度超過上限時）。
- **`MUST reject` 但 `SHOULD log`**：**一句話裡兩個不同等級**——拒絕是必須的（不拒就違規），記錄是強烈建議的（不記不算違規，但不是好實作）。**這正是為什麼你不能把 MUST/SHOULD 混為一談**：同一句話刻意用了兩個不同等級，作者是在精確區分「哪個沒商量、哪個可斟酌」。
- **`the declared length`**：`declared`（過去分詞修飾 length）＝訊息裡宣稱的長度——注意是「對方宣稱的」，不是「實際的」，這個區別正是漏洞的核心（攻擊者謊報長度）。

#### 句 3：`Implementations MAY impose a stricter limit, provided that the limit is documented.`

- **`MAY`（可選）**：設更嚴格的上限是**可做可不做**，兩種都符合規範。
- **`provided that ...`（＝ only if，條件是……）**：這是條件連接詞，界定「行使這個 MAY 的前提」——**前提是那個上限要有文件記載**。`provided that` 在技術文裡等於「以……為條件」，不能略過。

#### 句 4：`Failure to validate the length field before allocation has been the root cause of numerous memory-corruption vulnerabilities.`

**停在這句。這句是純敘事——領域知識幫得上，但語言結構才是真正的門檻。**

- **名詞化重災區**：主詞 `Failure to validate the length field before allocation` 是一整坨名詞化片語——把一個動作（「沒有在配置前驗證長度欄位」）壓縮成一個名詞 `Failure`。**這是技術文最典型的難點**：動作被包成名詞當主詞，句子瞬間變抽象。還原成動作句就好懂了：「（實作）沒有在配置前驗證長度欄位」這件事……
- **主要動詞**：`has been`（現在完成式）。
- **述語**：`the root cause of numerous memory-corruption vulnerabilities`（眾多記憶體損毀漏洞的根本原因）。又一坨名詞堆疊（Ch 8）：`memory-corruption`（複合形容詞）修飾 `vulnerabilities`。

還原後的中文大意：**「沒有在配置前驗證長度欄位」一直是眾多記憶體損毀漏洞的根本原因。**

**這句就是「領域知識的邊界」示範**：
- 你的資安/系統知識**幫得上一半**——你知道「配置前不驗長度」會導致 heap overflow 這類漏洞，所以你猜得到這句大概在講「這很危險」。
- 但**要精確讀懂這句，你得靠純語言能力**：認出主詞是個名詞化片語、把它還原成動作、追 `has been ... the root cause of ...` 的結構。如果你只靠領域知識「猜大概」，你會漏掉「這是**根本原因**（root cause，強因果宣稱）」「是**眾多**（numerous）漏洞」這些精確資訊。**純敘事、講動機/歷史/因果的句子，領域知識只能給你方向，讀懂細節必須靠語言。**

---

## 第 3、4、5 遍：技術文的收尾

**第 3 遍（生字）**：技術文的生字分兩類。**術語**（buffer / allocation / errno / conforming）——這些你多半已認得，且是領域詞彙，該熟。**非術語的語言字**（`impose` 施加、`declared` 宣告的、`numerous` 眾多、`provided that` 以……為條件、`root cause` 根本原因）——**這些才是技術背景讀者最常忽略、卻最該補的字**。它們不是專業術語，是「技術英文的敘事詞彙」，跨所有技術領域高頻出現。挑這類做 Anki，投資報酬率最高。

**第 4 遍（文體手法）**：技術文的「手法」不是言外之意，而是**規範等級的精確辨識**——把 MUST/SHOULD/MAY 標出來、把每個 if/when/unless/provided that 的條件範圍圈清楚、把被嚴格定義的術語（undefined behaviour）標記為「這是專有名詞，不能用日常意思讀」。

**第 5 遍（重讀）**：重讀確認——你有沒有把 MUST 和 SHOULD 分清？有沒有把 `Failure to validate...` 那句的名詞化還原成動作？順了，就完成。

---

## 對比與取捨：新聞 vs 技術文，讀法差在哪

| 面向 | 新聞（Ch 17） | 技術文（本章） |
|---|---|---|
| 主要難點 | 言外之意（attribution 態度、被動的政治意涵） | 資訊密度、規範精確度 |
| 能不能猜 | 大意可猜，猜錯形容詞褒貶影響小 | **不能猜**，MUST/SHOULD、the/a、if/unless 錯一個就全錯 |
| 領域知識 | 幫助有限（除非你熟該領域議題） | **大幅代償**，但純敘事段落幫不上 |
| 第一遍策略 | 抓 5W、畫倒金字塔地圖 | 抓固定套路（RETURN VALUE / 規範清單） |
| 最該精確的字 | attribution 動詞 | MUST/SHOULD/MAY、條件連接詞、定冠詞 |
| 最容易被忽略 | 被動的施事者 | 名詞化片語、「敘事詞彙」（非術語的高頻語言字） |

**核心取捨對技術背景讀者尤其重要**：你的優勢（領域知識）也是你的陷阱。它讓你在熟悉的技術描述上讀得飛快，卻讓你**誤以為自己「英文閱讀」很好**——直到遇到純敘事段落當機。**要真正把英文閱讀練起來，你得刻意在「領域知識幫不上的段落」上慢下來、逐句拆**，那才是你純語言能力的真實水位，也是最該練的地方。

---

## 踩雷集錦

**雷 1：把 RFC 的 `MAY` 和普通 `may` 搞混。** 段落 A 的 `this may happen`（可能發生，普通助動詞，表可能性）和段落 B 的 `Implementations MAY impose`（RFC 規範動詞，表「可選」）**是兩回事**。判斷靠兩點：一是**大寫**（RFC 規範詞全大寫），二是**語境**（規範文件在列要求時）。讀 RFC 時，大寫的 MUST/SHOULD/MAY 一律當規範等級處理。

**雷 2：靠領域知識「猜大概」就跳過純敘事句。** 講動機、歷史、因果、限制的段落，領域知識只給方向不給細節。`Failure to validate ... has been the root cause of numerous vulnerabilities`——猜「大概在講很危險」會漏掉「根本原因」「眾多」這些精確宣稱。**純敘事句要逐字讀，這是你語言能力的真實考場。**

**雷 3：把名詞化片語當單一名詞囫圇吞。** `Failure to validate the length field before allocation` 是一整個動作被壓成名詞。不還原成「（沒有）在配置前驗證長度欄位」這個動作句，你就抓不到句子在講什麼。**看到 -tion/-ment/-ure/Failure to... 開頭的長主詞，先還原成動詞再讀。**（這是 Ch 8 的名詞化，技術文重災區。）

**雷 4：忽略定冠詞的指涉精確度。** `the buffer`（那個特定的）vs `a buffer`（任意一個），在技術文裡是**不同的規範**。日常閱讀你可以略過冠詞，讀規格書不行——`the buffer overlaps` 指的是「此呼叫的那個 buffer」，指涉錯了，你對規範的理解就錯了。

**雷 5：把「被嚴格定義的術語」用日常意思讀。** `undefined behaviour` 不是「行為不明確」，是 C 標準的專有名詞（＝無任何保證，編譯器可任意處理）。`conforming implementation`、`declared length` 也都是精確界定過的。**讀技術文遇到看似普通、卻被文件正式定義過的詞，要用文件的定義讀，不是字面意思。** 遇到 "is defined as" / "For the purposes of this document" 這類句子，那就是在給你下定義，要記住。

---

## 進階：再往深一層——技術文的「規範 vs 說明」二元結構

熟練的技術文讀者，會在讀的時候**自動把每一句歸類**：這句是**規範（normative）**還是**說明（informative）**？

- **規範句**：定義實作「必須／應該／可以」怎麼做——含 MUST/SHOULD/MAY、含條件句、含定冠詞精確指涉。**這些句子每個字都算數，錯一個字實作就壞**，要用最高精度讀。
- **說明句**：解釋動機、舉例、講背景、談歷史（段落 A 的「for example, because fewer bytes are available」、段落 B 的最後一句都是）。**這些幫你理解「為什麼」，但不是硬性要求。**

RFC 甚至會明確標註哪些章節是 normative、哪些是 informative（附錄常是 informative）。**會分這兩類，你讀技術文的力氣就用對地方**：規範句慢讀求精確，說明句正常速度求理解。

再往深一層——**這也重新定位了「領域知識」的角色**：領域知識在「說明句」上幫你最多（因為你熟那些概念），在「規範句」上反而可能害你（你憑經驗「以為」規範是某樣，跳過細讀，結果 MUST/SHOULD 看反）。**越是你熟的主題，越要小心規範句的細節**——因為熟悉會誘使你關掉逐字讀的謹慎。這是資深工程師讀規格書時最容易犯的錯：憑經驗腦補，漏掉規範的精確措辭。

---

## 動手練習

1. **還原名詞化**：把段落 B 最後一句 `Failure to validate the length field before allocation has been the root cause...` 改寫成一個以「動作」為主詞的普通句子（例如用 `If an implementation fails to...` 開頭）。改寫後對照，感受名詞化壓縮了多少資訊。
2. **標規範等級**：找一份真的短 RFC（例如 RFC 2119 本身，只有兩頁），圈出所有 MUST / SHOULD / MAY，並用一句中文寫出「違反各自的後果」。
3. **辨規範 vs 說明**：把段落 A 和 B 的每一句標成 [規範] 或 [說明]，再檢查你有沒有把兩段最後的敘事句誤標成規範。
4. **讀一段真 man page**：`man 2 read`（或線上版），只讀 RETURN VALUE 和 ERRORS 兩段，走五遍 SOP。特別留意哪些句子你「領域知識秒懂」、哪些句子你「必須靠語言硬讀」——把後者標出來，那就是你該練的。
5. **建「技術敘事詞彙」Anki 組**：impose / declared / numerous / provided that / root cause / regardless / arbitrary / subsequent / respectively / accordingly——這 10 個都是非術語的技術英文高頻字，各配例句做卡。

---

## 本章重點整理

- **技術文的難點是資訊密度與精確度**，不是言外之意。每個字都算數：MUST≠SHOULD、the≠a、if≠unless，錯一個字實作就壞。
- **五大難點**：名詞化、被動、條件句、被嚴格定義的術語、RFC 規範動詞。逐一辨識、逐一處理。
- **RFC 2119 的 MUST/SHOULD/MAY 是嚴格分級**，不是同義字。大寫 + 規範語境 → 當要求等級處理，與普通 may/should 區分開。
- **領域知識大幅代償技術文閱讀**，但**在純敘事段落（動機/歷史/因果）幫不上**——那些段落是你純語言能力的真實考場，要逐字讀。
- **名詞化片語**（`Failure to validate...`）要先還原成動作句再讀；**定冠詞**在技術文裡指涉精確，不能略過。
- **把每句歸類為「規範」或「說明」**：規範句慢讀求精確，說明句正常讀求理解。越熟的主題，越要警惕規範句的細節（別憑經驗腦補）。

---

## 自我檢核

不看上文，主動回想：

- [ ] 我能說出技術文的五大難點，並各舉一例嗎？
- [ ] RFC 的 MUST / SHOULD / MAY 各是什麼要求等級？違反各自的後果是什麼？我能區分大寫規範詞和普通 may/should 嗎？
- [ ] 我知道「領域知識」在技術文的哪類句子幫我最多、哪類幫不上（甚至害我）嗎？
- [ ] 看到 `Failure to validate ... has been the root cause of ...` 這種名詞化長主詞，我會先還原成動作句再讀嗎？
- [ ] 我知道 `the buffer` 和 `a buffer` 在技術文裡是不同規範嗎？我讀規格書時會不會略過冠詞？
- [ ] 遇到 `undefined behaviour`、`conforming implementation` 這類被文件正式定義的詞，我會用文件的定義而非日常意思讀嗎？
- [ ] 我能把一段技術文的每句標成「規範」或「說明」，並知道各該用什麼精度讀嗎？

---

## 延伸閱讀

- **[RFC 2119 — Key words for use in RFCs to Indicate Requirement Levels](https://www.rfc-editor.org/rfc/rfc2119)** — 只有兩頁，MUST/SHOULD/MAY 的定義原文。**讀任何 RFC/W3C/標準文件之前必看**，這是解讀規範語氣的鑰匙。本章表格出自此。
- **[RFC 8174 — Ambiguity of Uppercase vs Lowercase in RFC 2119 Key Words](https://www.rfc-editor.org/rfc/rfc8174)** — 補充 RFC 2119，明確規定「只有大寫的 MUST/SHOULD/MAY 才是規範詞」。這就是本章「大寫才算規範」判斷的官方依據。
- **[Linux man-pages project](https://man7.org/linux/man-pages/)** — Michael Kerrisk 維護的權威 man page 線上版。挑 `read(2)`、`malloc(3)`、`open(2)` 的 RETURN VALUE / ERRORS 段精讀，是技術文精讀最好的真實材料。本章段落 A 模擬其風格。
- **[The C Standard — Undefined Behaviour（cppreference）](https://en.cppreference.com/w/c/language/behavior)** — 想確認 `undefined behaviour` 為何是「被嚴格定義的術語、且後果嚴重」的權威說明，理解「被定義過的術語」這個難點的最佳範例。

這兩章（Ch 17 新聞、Ch 18 技術）我們把完整的精讀 SOP 在兩種對立文體上各演了一遍，也點出了「精讀很昂貴、要挑值得的材料」。但你不可能每篇都精讀——大量的技術文件（尤其 man page、API 文件）根本不該從頭讀到尾。下一章我們轉向精讀的對面：**略讀（skimming）與掃讀（scanning）**——什麼時候該快、怎麼快、以及一個誠實的殘酷真相：**你略讀不了你根本讀不動的東西**。

→ [下一章：略讀與掃讀——以及它們誠實的極限](./19-skimming-and-scanning.md)
