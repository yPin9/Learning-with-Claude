# Ch 8 名詞片語的堆疊：英文的名詞化風格

> **目標**：搞懂為什麼技術文與新聞的名詞片語（noun phrase）會腫得又長又密，難到一個名詞佔掉半行。學會**先定位中心詞（head noun）**——這串東西真正在講的那個名詞——再分兩頭拆：往前拆前置修飾（形容詞堆疊、名詞當形容詞的複合詞），往後拆後置修飾（介系詞片語、關係子句、分詞）。讀完你會有一套解剖名詞片語的固定手法，把 `government cyber security incident response team` 這種怪獸一刀切開。

## 為什麼需要這個？

上一章你學會找句子的骨架：主詞、主要動詞、受詞。但你很快會發現一件事——**骨架的那三根骨頭本身，每一根都可能是一坨腫脹的名詞片語。** 主詞是一長串、受詞又是一長串，你就算知道「主詞在動詞左邊」，那個主詞裡塞了十個字，你還是不知道它到底在講什麼。

英文（尤其是技術文、官方文件、新聞）有一種很鮮明的風格，叫**名詞化（nominalization）**：它偏好把動作、概念、關係都**包裝成名詞**，然後把一大堆資訊全塞進**一個名詞片語**裡，而不是拆成好幾個短句。中文的習慣相反——中文愛用短句、愛用動詞、愛把事情一件一件說。所以你讀英文名詞片語時的痛苦，有一半是**語言習慣的落差**：英文把「本來該是一整句話的資訊」壓縮成了一坨名詞。

這一章就是教你把這坨壓縮檔**解壓縮**。名詞片語是英文長句裡密度最高、最容易讓你當機的結構。攻下它，長句就攻下一大半。

## 先建立直覺

先感受一下名詞化的威力。同一件事，兩種寫法：

- **動詞風（中文人習慣的）**：*The system failed. This caused the service to go down. The company had to investigate why.*（系統壞了，這害服務掛掉，公司得去查為什麼。）
- **名詞化風（英文技術文習慣的）**：*The company launched an investigation into the cause of the service outage.*（公司對這次服務中斷的原因展開了調查。）

看到差別了嗎？第二種寫法把「壞掉、掛掉、調查」這些**動作**全變成了**名詞**（`investigation`、`cause`、`outage`），然後用介系詞（`into`、`of`）把它們串成**一整坨名詞片語**：`an investigation into the cause of the service outage`。一整句話的資訊，被壓進了一個名詞結構。

這就是為什麼技術文「每個字都認得卻讀不懂」——它的資訊不是攤在句子的時間軸上讓你一件件接收，而是**層層疊疊塞進名詞片語裡**，等你自己去解開層次。

核心直覺是：**每個名詞片語都有一個中心詞（head noun），就是這串東西「歸根究底在講的那個名詞」。** 其他所有東西——前面的形容詞、名詞，後面的片語、子句——全都是在**修飾這個中心詞**。你的任務永遠是兩步：

1. **先找中心詞**：這一大串，最核心是在講「什麼」？
2. **分兩頭拆修飾**：中心詞**前面**掛了什麼，**後面**拖了什麼。

用比喻：名詞片語像一個洋蔥。中心詞是**蔥心**，前置修飾和後置修飾是**一層層蔥皮**。你要先摸到蔥心，再一層層剝皮，才知道這是一顆什麼蔥。**先找蔥心，再剝皮。**

## 第一步：定位中心詞

拆名詞片語的第一動作，永遠是**找中心詞**。找到它，你就抓住了這串東西的根，前後的修飾語才有地方掛。

有兩條可靠的規則幫你定位中心詞：

**規則一（前置修飾的情況）：一連串名詞疊在一起時，中心詞通常是「最後一個名詞」。** 英文的前置修飾是**由左往右、越靠右越核心**。看這個經典怪獸：

> **government cyber security incident response team**

六個字全是名詞，眼睛看得發昏。但套規則一：中心詞是最後一個——**`team`（團隊）**。這一整串，歸根究底在講一個 `team`。前面五個字全是在告訴你「哪一種 team」。定位到蔥心 `team`，怪獸就馴服了一半。

**規則二（後置修飾的情況）：中心詞通常在後置修飾語「開始之前」。** 後置修飾多半以介系詞（`of, in, for, on…`）、關係代名詞（`who, which, that`）、或分詞（`-ing / -ed`）開頭。中心詞就在這些訊號**出現之前**的那個名詞。看：

> **a report on the safety of nuclear reactors**

`on` 是介系詞，後置修飾從這裡開始。所以中心詞在 `on` 之前——**`report`**。這一串在講一份 `report`，`on the safety of nuclear reactors` 是在說這份報告是關於什麼的。

**找中心詞的萬用測試**：問自己「這一整串，最簡化成一個字，是講什麼？」——`government cyber security incident response team` 講的是一個「團隊」，`a report on the safety of nuclear reactors` 講的是一份「報告」。那個字就是中心詞。抓到它，剩下的都是外掛。

## 第二步：往前拆——前置修飾

定位好中心詞，先往它**左邊**看。掛在中心詞前面的修飾語叫**前置修飾（premodifier）**，主要有兩種，而技術文最愛用第二種。

**（一）形容詞堆疊（stacked adjectives）**：一串形容詞排在名詞前面，像 `a large red brick building`（一棟紅磚大樓）。這種對你不難，形容詞你多半認得，一個個往中心詞 `building` 貼上去即可。

**（二）名詞當形容詞（noun-noun compound，名詞複合）**：這才是技術文與新聞的重災區。英文允許**用名詞去修飾名詞**——名詞放在另一個名詞前面，就當形容詞用。`a data breach` 裡，`data` 是名詞，卻在修飾 `breach`（一次資料的外洩）。`a security team` 裡 `security` 修飾 `team`。**問題是這種堆疊可以無限接龍**，一個名詞前面串三四五個名詞，全部沒有任何連接詞、沒有標點，你得自己判斷誰修飾誰。

回到那頭六字怪獸，我們正式拆它：

> **government cyber security incident response team**

中心詞是 `team`（規則一：最後一個名詞）。現在的關鍵是**理解修飾的層次**——這串名詞不是平的，它是**一層套一層**的。拆法是**從中心詞往左，一層層往外包**：

```
                                          team          ← 蔥心：一個團隊
                                 response team          ← 什麼團隊？回應（response）的團隊
                        incident response team          ← 回應什麼？事件（incident）
              security incident response team          ← 什麼事件？資安（security）事件
        cyber security incident response team          ← 哪種資安？網路（cyber）資安
government cyber security incident response team        ← 誰的？政府（government）的
```

一層層讀出來：**這是一個「政府的、網路資安事件、回應團隊」**——政府用來回應網路資安事件的團隊。原本一坨無法解析的名詞泥，拆成層次後意思清清楚楚。

**拆名詞複合的原則：找到最右邊的中心詞，然後由右往左，把每一個名詞當成「限縮範圍的形容詞」，一層層問『哪一種』。** 越靠右越接近核心概念，越靠左越是外圈的限定。這個「由右往左剝」的方向感，是拆名詞複合的鑰匙。

再練一個新聞裡常見的：

> **the UK government data protection reform proposals**

中心詞（最右名詞）：`proposals`（提案）。由右往左：`reform proposals`（改革提案）→ `data protection reform proposals`（資料保護改革的提案）→ `government data protection reform proposals`（政府的資料保護改革提案）→ `UK government ...`（英國政府的……）。讀出來：**英國政府的資料保護改革提案。** 六個字，一層層限縮，指向一種很具體的東西。

## 第三步：往後拆——後置修飾

拆完前面，往中心詞**右邊**看。掛在中心詞後面的叫**後置修飾（postmodifier）**，這是把名詞片語撐長的另一半力量，有三大類，你上一章已經見過它們：

**（一）介系詞片語（prepositional phrase）**：以 `of / in / for / on / with / between…` 開頭。這是名詞化風格最愛的工具，常常一個接一個串成鏈。看：

> **the impact of climate change on coastal communities**

中心詞 `impact`（衝擊）。後面拖了兩個介系詞片語：`of climate change`（氣候變遷的）、`on coastal communities`（對沿海社區的）。讀出來：**氣候變遷對沿海社區的衝擊。** 注意介系詞片語會**接龍**——`the risk of failure of the cooling system`（冷卻系統故障的風險）裡，`of failure` 修飾 `risk`，`of the cooling system` 又修飾 `failure`。一層套一層，得順著介系詞一節節解。

**（二）關係子句（relative clause）**：以 `who / which / that / whose` 開頭，自帶一個動詞，整段修飾中心詞。看：

> **a vulnerability that allows attackers to bypass authentication**

中心詞 `vulnerability`（漏洞）。`that allows attackers to bypass authentication` 是關係子句，在說這是一個「怎麼樣的」漏洞——一個讓攻擊者得以繞過驗證的漏洞。**關係子句是後置修飾裡資訊量最大的一種**，第 9 章會專門深挖，這裡先認得它的長相：`中心詞 + who/which/that + 一個動詞`。

**（三）分詞（participle）**：`-ing` 現在分詞或 `-ed` 過去分詞，掛在名詞後面當修飾，等於一個「精簡版的關係子句」。看：

> **a document detailing the security flaws** ＝ a document that details the security flaws（一份詳述這些資安缺陷的文件）
>
> **the data collected from users** ＝ the data that was collected from users（從使用者身上蒐集到的資料）

`detailing`（現在分詞，主動：文件「詳述」）、`collected`（過去分詞，被動：資料「被蒐集」）。**這兩個分詞是後置修飾裡最容易被漏看的**，因為它們短、又長得像動詞——別忘了上一章的教訓：`-ing / -ed` 是非限定動詞，當不了主要動詞，它們在這裡是**修飾語**。

## 綜合實戰：一坨腫到極致的名詞片語

把前置和後置修飾同時堆上去，就是你在 BBC 和技術文裡真正會遇到的怪物。我們拆一個：

> **a leaked internal government report on the growing security risks facing critical national infrastructure**

一眼看去是一團亂麻。用兩步法。

**第一步，找中心詞。** 由左掃到右，找「後置修飾開始之前的最後一個名詞」。`on` 是介系詞（後置修飾起點），所以中心詞在 `on` 之前——最後一個名詞是 **`report`**。整串在講一份 `report`。

**第二步，分兩頭拆。**

往前（前置修飾）：`a leaked internal government report`
```
                         report          ← 蔥心
                government report          ← 政府的報告
       internal government report          ← 內部的政府報告
leaked internal government report          ← 外洩的、內部的政府報告
```
（`leaked` 是過去分詞當前置形容詞、`internal` 是形容詞、`government` 是名詞當形容詞——三種前置修飾同時上。）

往後（後置修飾）：`on the growing security risks facing critical national infrastructure`
```
report
  └─ on the growing security risks              ← 介系詞片語：關於「不斷升高的資安風險」
       └─ (內部又是一坨名詞複合：growing[分詞] + security[名詞] + risks[中心])
       └─ facing critical national infrastructure   ← 現在分詞，修飾 risks：正衝著「關鍵國家基礎設施」而來的
            └─ (又一坨名詞複合：critical[形容詞] + national[形容詞] + infrastructure[中心])
```

全部組回來：**一份外洩的政府內部報告，內容是關於關鍵國家基礎設施所面臨、不斷升高的資安風險。** 一坨十六字的名詞泥，拆成有層次的結構後，意思乾淨俐落。

**注意這個怪獸的巢狀（nested）本質**：大名詞片語 `risks` 的後置修飾（分詞 `facing…`）裡面，又包著另一坨名詞複合 `critical national infrastructure`。名詞片語會**自我套疊**——這正是它能無限膨脹的原因，也是為什麼你得**一層層拆、別想一次吞完**。

## 機制：為什麼英文這麼愛名詞化

理解「為什麼」能讓你讀得更甘願。名詞化不是英文作者存心刁難你，它有實用的驅動力：

**（一）資訊密度（information density）。** 把一整句話壓成一個名詞片語，能在更少的字裡塞更多資訊——這對追求精簡的技術寫作與講究版面的新聞標題是剛需。新聞標題尤其極端，`Government cyber attack response failure probe`（政府網路攻擊應對失敗調查）這種全名詞串，就是密度壓到極限的產物。

**（二）客觀與抽象。** 把動詞（`we investigated`）換成名詞（`an investigation`），主詞（誰做的）就可以省略，語氣變得客觀、非人稱——這正是學術與官方文件想要的腔調。「調查已展開」比「我們展開了調查」聽起來更中立、更權威。**代價是：讀者要自己還原『是誰、對什麼、做了什麼』。** 你的解壓縮工作，本質上就是在替作者省略掉的東西補回主詞和動作。

**（三）當主題往下傳遞。** 把前一句提到的動作名詞化，就能當成下一句的主詞接著講，讓行文連貫。這也是為什麼名詞片語常出現在句子開頭當主詞——它往往濃縮了前文。**遇到句首一坨長名詞片語，別慌，它多半是在替前面的內容打包。**

記住這三個驅動力，你對名詞片語的態度會從「作者找我麻煩」變成「這是一種可預期的壓縮格式，我有解碼器」。

## 對比與取捨

| 面向 | 動詞風（中文習慣） | 名詞化風（英文技術/新聞） |
|---|---|---|
| 資訊怎麼擺 | 攤在多個短句、沿時間軸展開 | 壓進單一名詞片語、層層疊套 |
| 動作怎麼呈現 | 用動詞（failed, caused, investigate） | 包裝成名詞（failure, cause, investigation） |
| 誰做的（主詞） | 通常講明 | 常被省略，語氣客觀抽象 |
| 讀者的負擔 | 低，一件件接收 | 高，得自己定位中心詞、解開層次 |
| 密度 | 低，字多但好懂 | 極高，字少但每字都咬 |
| 典型出沒地 | 口語、故事、簡單說明 | 學術論文、官方文件、新聞標題、技術規格 |

取捨的本質：**名詞化是「作者省事、讀者費事」的交易。** 作者用它換到密度與客觀，成本轉嫁給讀者——你得做解壓縮。對你這個讀者來說，沒有選擇餘地：BBC 和技術文就是這麼寫的。**唯一的出路是把解壓縮練成本能**，讓「找中心詞、剝兩頭」快到你意識不到自己在做。

## 踩雷集錦

**雷 1：把名詞複合裡的中心詞找錯，導致理解整個歪掉。** `a security software update`（一次資安軟體更新）的中心詞是 `update`（更新），不是 `security`，也不是 `software`。整串在講一次「更新」，前面全是限定它是哪種更新。若你誤把 `security` 當核心，會讀成「關於軟體更新的資安（措施）」，意思完全跑掉。**中心詞在最右邊，這條規則對名詞複合幾乎鐵準，先套它。**

**雷 2：漏看名詞後面的分詞（`-ing` / `-ed`），把它當成主要動詞或整段漏讀。** `the data collected last year showed a trend`——新手容易把 `collected` 當主要動詞，讀成「資料蒐集了去年」而卡住。其實 `collected last year` 是過去分詞後置修飾 `data`（去年蒐集的資料），這句真正的主要動詞是 `showed`。**名詞後面緊跟的 `-ing`/`-ed`，先假設它是修飾語（精簡版關係子句），不是主要動詞。**

**雷 3：介系詞片語接龍時，搞錯誰修飾誰。** `the report of the head of the committee`——是「委員會主席的報告」。`of the head` 修飾 `report`，`of the committee` 修飾 `head`（不是修飾 report）。介系詞片語接龍時，**後一個片語通常修飾前一個片語的名詞，不是一路修飾到最前面的中心詞。** 一節一節順著解，別跳。

**雷 4：以為所有前置名詞都平等地修飾中心詞。** `student loan interest rate`（助學貸款利率）不是「學生、貸款、利息、利率」四個平等修飾。它是層層限縮：`rate`（利率）← `interest rate`（利息的利率）← `loan interest rate`（貸款利息的利率）← `student loan interest rate`（助學貸款利息的利率）。**名詞複合是有內部層次的，不是一坨平的形容詞。** 用「由右往左、每層問哪一種」的方法拆。

**雷 5：被名詞化騙過，讀懂了字面卻漏掉「動作被藏起來了」。** `the rejection of the proposal by the board`——字面是名詞串，但它其實藏著一整個動作：`the board rejected the proposal`（董事會否決了提案）。讀名詞化時要有意識地**把關鍵名詞還原回動詞**，問「誰對誰做了這個動作」，你才真的懂它在講一件事，而不只是認得幾個名詞。

## 進階：再往深一層

**名詞片語就是上一章「骨架三根骨頭」的填充物。** 這兩章要合起來看：Ch 7 教你找出句子骨架（S-V-O），這一章教你拆開骨架裡的每一根——因為主詞和受詞本身往往就是腫脹的名詞片語。完整的拆句流程其實是**兩層巢狀**：先用 Ch 7 的方法抓出主幹的 S、V、O，再對 S 和 O 各自用這一章的方法拆開它們內部的名詞片語。**先拆句子層，再拆片語層**，兩層工具疊起來用，才能對付真正的硬句子。

**名詞化的還原是頂尖讀者的隱藏技能。** 前面雷 5 提到把 `the rejection of the proposal` 還原成 `the board rejected the proposal`。這個「把名詞化拆回動詞句」的動作，語言學上對應的正是名詞化的逆操作。**練到能在腦中自動還原**，你讀官方文件、學術論文的速度會質變——因為你不再是在解讀一串抽象名詞，而是在還原一件件具體的事。想深入的話，Cambridge 的 grammar 有 nouns 與 noun phrases 的專頁（延伸閱讀），Swan 的 *Practical English Usage* 也有名詞複合與名詞化的條目。

**新聞標題是名詞化的極限運動。** BBC 標題常把整句壓成純名詞串，甚至省掉冠詞和動詞：`Cyber attack fears grow`、`Data breach probe launched`。這種標題其實就是「名詞片語堆疊」開到最大，且刻意留白讓你補。等你這一章的解壓縮練熟，回頭看新聞標題會發現它們不再是謎語，而是壓縮到極致、但規則一致的名詞片語——第 16 章專講新聞英文（journalese）時會再回來收這條線。

## 動手練習

1. **找中心詞**：下面每一串名詞片語，只圈出**中心詞**那一個字。
   - `a wireless network security protocol`
   - `the risk of a global economic recession`
   - `emergency medical response procedures`
   - `an algorithm designed to detect fraud`

2. **拆名詞複合**：把下面兩串名詞複合，用「由右往左、每層問哪一種」的方法，一層層寫出來（像本章拆 `government cyber security incident response team` 那樣）。
   - `climate change adaptation strategy`
   - `open source software supply chain attack`

3. **辨認後置修飾的三種類型**：下面每句的中心名詞後面各拖了一種後置修飾——判斷它是**介系詞片語、關係子句、還是分詞**。
   - `a policy that protects consumer data`
   - `the effects of long-term exposure`
   - `a company based in Taiwan`
   - `the number of users affected by the outage`

4. **綜合解剖**：把這一坨完整拆開——先找中心詞，再分兩頭（前置、後置）拆，最後用一句中文講出它在說什麼。
   > `a newly discovered high-severity remote code execution vulnerability affecting millions of devices`

5. **名詞化還原**：把下面三個名詞化片語，還原成「主詞＋動詞」的句子（補回被藏起來的動作與行為者，可自行合理推測主詞）。
   - `the government's approval of the new law`
   - `a sharp decline in sales`
   - `the investigation into the cause of the crash`

6. **實戰**：打開一篇技術文件或 BBC 文章，找出**最長的一個名詞片語**（不是整句，是其中一坨名詞片語），對它完整套用「找中心詞、分兩頭拆」。你會發現越長的名詞片語，拆開後層次越清楚。

## 本章重點整理

- 英文技術文與新聞偏好**名詞化（nominalization）**：把動作、概念壓成名詞，再塞進**單一名詞片語**。這是「每個字都認得卻讀不懂」的主因。
- 拆名詞片語永遠兩步：**先找中心詞（head noun）**，再**分兩頭拆修飾**（前置、後置）。
- **找中心詞的規則**：一串名詞疊在一起時，中心詞是**最右邊**那個名詞；有後置修飾時，中心詞在**介系詞／關係代名詞／分詞出現之前**。
- **前置修飾**兩類：形容詞堆疊、**名詞當形容詞（noun-noun compound）**。拆名詞複合要**由右往左、每層問「哪一種」**，因為它有內部層次，不是一坨平的。
- **後置修飾**三類：**介系詞片語**（of/in/for…，會接龍）、**關係子句**（who/which/that＋動詞）、**分詞**（-ing/-ed，＝精簡版關係子句）。
- 名詞片語會**巢狀自我套疊**，能無限膨脹——所以要**一層層拆，別想一次吞完**。
- 進階：把關鍵名詞**還原回動詞句**（誰對誰做了什麼），是讀名詞化文本的隱藏技能。

## 自我檢核

- [ ] 我能解釋什麼是名詞化，並說出它為什麼讓技術文/新聞這麼難讀。
- [ ] 給我一坨名詞片語，我能一眼指出它的中心詞。
- [ ] 我能拆一串名詞複合（如 `student loan interest rate`），講出它的內部層次，而不是當成一坨平的。
- [ ] 我能分辨後置修飾的三種類型：介系詞片語、關係子句、分詞。
- [ ] 我知道名詞後面緊跟的 `-ing`/`-ed` 是修飾語（分詞），不是主要動詞。
- [ ] 我能把一個名詞化片語（如 `the rejection of the proposal`）還原成「主詞＋動詞」的句子。

## 延伸閱讀

- **[Cambridge Dictionary — Nouns and noun phrases](https://dictionary.cambridge.org/grammar/british-grammar/nouns)** — 權威且免費。看它怎麼定義名詞片語與中心詞（head），並說明前置與後置修飾如何掛在中心詞上。本章的「中心詞＋兩頭修飾」框架就對應它的講法，讀它確認你的拆法沒歪。
- **[Cambridge Dictionary — Relative clauses](https://dictionary.cambridge.org/grammar/british-grammar/relative-clauses)** — 本章把關係子句列為後置修飾三大類之一但只點到為止。這頁把關係子句講全（defining vs non-defining、關係代名詞何時可省略），是你進第 9 章前最好的暖身。
- **Michael Swan, *Practical English Usage*（4th ed., Oxford）** — 查閱型用法聖經。想確認名詞複合（noun + noun）該怎麼組、名詞化的慣用法時，查它的相關條目。它把「英文為何愛用名詞修飾名詞」講得比多數教科書實在。
- **Huddleston & Pullum, *The Cambridge Grammar of the English Language*** — 最高權威參考書。若你對「中心詞到底怎麼界定」「名詞片語的內部結構」較真，最終答案在這裡。這門課不要求你讀，知道它存在、需要時能查即可。

你現在能拆句子的骨架（Ch 7），也能拆開骨架裡腫脹的名詞片語（本章）。但還有一種結構，資訊量最大、也最會把句子撐長——**子句**：關係子句、that 子句、分詞構句。它們前面兩章都露過臉，下一章我們正面把它們一次講透。

→ [下一章：Ch 9 子句：關係子句、that 子句、分詞構句](./09-clauses.md)
