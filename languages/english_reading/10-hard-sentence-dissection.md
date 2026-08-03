# Ch 10 — 難句拆解：被動、倒裝、cleft、長距離依賴，與新聞 vs 技術句構指紋

> **目標**：把「字全認識、結構也拆了，卻還是卡住」的最後一批難句攻下來——被動語態、倒裝、分裂句（cleft）、長距離依賴、花園小徑句。然後，把新聞英文與技術英文各自的**句構指紋**攤開對照，讓你一看句型就知道「這是哪種文體、作者接下來要幹嘛」。這是 Part 2 的收官原理章，寧長勿短，例句管夠。

---

## 為什麼需要這個？

到這章為止，你會找主幹（Ch 7）、會拆名詞堆疊（Ch 8）、會追子句嵌套（Ch 9）。但真實文本裡還有一類難句，難的不是「層次多」，而是**語序被動過手腳、或相關的字被拉得很遠**——它們打破你「從左到右順順讀」的預設，讓大腦在半路上做出錯誤預測，然後卡住。

這些結構有個共同點：**它們都是為了某種修辭目的而扭曲了正常語序**——把重點提前、把已知資訊藏起來、把新資訊推到句尾。新聞記者和技術作者不是為了刁難你才這樣寫，他們是為了**控制資訊的呈現順序**。所以這一章有兩個任務：一是認出這些扭曲、把語序還原；二是搞懂「哪種文體慣用哪種扭曲」——因為新聞和技術文的句構指紋差異極大，認得指紋，你讀之前就有預期，讀起來就順。

這是 Part 2 的最後一塊拼圖。補完，你面對任何長句都有一套系統化的拆解流程，接下來 Part 3 就能進到「怎麼選材料、怎麼精讀」。

---

## 先建立直覺

正常的英文句子，資訊流動有個默認方向：**主詞（通常是已知的、舊資訊）在前，新資訊往句尾放。** 這叫**尾焦點（end-focus）原則**——英文傾向把「最想強調、最新」的東西擺在句子末端。

這章講的每一種難句，本質上都是在**動用某種手段，重新安排「什麼放前面、什麼放後面」**：

```
被動語態  → 把「受事者」提到主詞位，把「施事者」藏到句尾（甚至刪掉）
倒裝      → 把某個成分提到句首，動詞跟著跳到主詞前
cleft     → 把某個成分「劈出來」單獨強調，其餘塞回一個子句
長距離依賴 → 相關的兩個詞（如疑問詞和它的空位）被拉到句子兩端
```

所以拆這些句子的通用心法只有一句：**先把語序還原成「正常語序」，意思就浮現了。** 下面每一種，我們都示範怎麼還原。

---

## 難句一：被動語態（passive voice）——施事者藏哪去了？

**被動語態**把正常「主動句」的受詞提到主詞位置，動詞變成 `be + 過去分詞（past participle）`，原本的主詞（動作的發出者，叫**施事者 agent**）被丟到句尾的 `by ...`，而且**經常整個省略**。

```
主動：The committee approved the proposal.
       （施事者）  動詞      （受事者）

被動：The proposal was approved by the committee.
       （受事者→主詞）  be+p.p.    （施事者→by 片語）

被動（省略施事者）：The proposal was approved.
                    ← 誰批准的？沒說。這正是被動最關鍵的效果。
```

### 為什麼新聞和技術文這麼愛用被動

被動不是壞文筆，它在這兩種文體裡是**刻意的工具**，理由不同：

**新聞用被動，常是為了「施事者不明、不重要、或不願指名」。**
```
Three protesters were arrested near the square.
（被誰逮捕的？多半是警方，但記者選擇不明講，或聚焦在「被捕的人」上）

The claims have been widely disputed.
（被誰質疑的？含糊帶過——這常是新聞客觀語氣的手法，有時也是模糊責任的手法）
```
**這裡藏著一個閱讀警訊**：被動 + 省略施事者，有時是在**淡化或迴避「誰做的」**。讀新聞時，看到重要動作用被動且沒講施事者，要主動問一句：「這件事到底是誰做的？作者為什麼不講？」這是讀出言外之意的關鍵技能。

**技術文用被動，常是為了「聚焦在過程／物件，而非執行者」。**
```
The connection is closed after the timeout expires.
（誰關的不重要，重點是「連線會被關閉」這個行為和時機）

The header field MUST be validated before the payload is parsed.
（RFC 風格：聚焦在「該欄位必須被驗證」，執行者是誰由實作者自行決定）
```
技術寫作裡，被動讓句子聚焦在**系統行為本身**，這是規格書、論文的標準語體。

### 拆被動的方法：把它還原成主動

```
被動句：A critical vulnerability was discovered in the parser last month.

還原步驟：
  1. 找 be + 過去分詞：was discovered
  2. 現在的主詞 A critical vulnerability，其實是「被做動作的東西」（受事者）
  3. 施事者？句中沒有 by ...，被省略了 → 心裡補「（某人）」
  → 主動語意：「（某人）上個月在 parser 裡發現了一個嚴重漏洞」
```

**判斷被動的信號：`be 動詞（is/was/were/been/being…）＋ 過去分詞`。** 但小心：這個組合有時是「be + 形容詞化的分詞」（狀態），不是真被動，例如 `The door was closed`（可能指「門是關著的狀態」也可能指「門被關上」）。靠上下文判斷，但對閱讀理解通常不影響大意。

還要小心 Ch 9 講過的陷阱：**過去分詞除了組被動，也用來組縮減關係子句**。`The report published last week...`（published 是修飾 report 的分詞，不是被動主要動詞）vs `The report was published last week.`（was published 才是被動主要動詞）。差別在有沒有 be 動詞。

---

## 難句二：倒裝（inversion）——動詞怎麼跑到主詞前面了？

**倒裝**指主詞和（助）動詞的正常順序被顛倒，通常是因為某個成分被提到了句首。英文除了問句（天生倒裝），還有幾種陳述句會倒裝，這些最會絆倒讀者。

### 否定詞開頭的倒裝（新聞、正式文體常見）

當句首放了**否定或半否定的副詞片語**（`Never / Rarely / Not only / No sooner / Little / Under no circumstances / Not until...`），主詞和助動詞要倒裝：

```
正常：The government has never faced such pressure.
倒裝：Never has the government faced such pressure.
      └─否定詞  └助動詞 └──主詞──┘
      （提前 Never 強調，助動詞 has 跳到主詞前）
```

```
Not only did the company miss its target, but it also lost market share.
└─否定片語  └助 └─主詞─┘ ...
還原：The company not only missed its target, but ...
```

讀到句首是 `Never / Rarely / Not only / Not until / No sooner / Little...` 後面緊接一個助動詞（has/did/could…），立刻反應：**這是倒裝，把主詞找出來、在心裡還原成正常語序。** 這種倒裝在正式新聞和評論裡不少，用來製造強調與氣勢。

### 地方副詞開頭的倒裝（描述、新聞現場常見）

句首放地方或方向副詞片語時，動詞（常是 be 或表移動的動詞）也可能跑到主詞前：

```
正常：A row of armoured vehicles stood at the entrance.
倒裝：At the entrance stood a row of armoured vehicles.
      └──地方副詞──┘ └動詞┘ └─────真正的主詞─────┘
```

這種倒裝的真正主詞在**動詞後面**，而且常是個長名詞片語。**對策：看到句首是地方／方向片語、後面接了個動詞卻找不到主詞，主詞就在動詞後面。**

### 條件句省略 if 的倒裝（正式、技術文常見）

正式英文常把 `if` 省掉、改用倒裝來表條件：

```
正常：If the system had failed, data would have been lost.
倒裝：Had the system failed, data would have been lost.
      └助動詞 └─主詞──┘ └分詞┘
      （省 if，把 had 提到主詞前，就是條件句）
```

看到句首是 `Had / Were / Should` 直接接主詞（而非問句），這是**省略 if 的條件句**，心裡補回 if 就懂：`Had the system failed = If the system had failed`。技術文件和正式文件很常見。

---

## 難句三：分裂句（cleft）——把重點劈出來單獨強調

**分裂句（cleft sentence）**把一個簡單句「劈成兩半」，目的是把某個成分**單獨拎出來強調**。英文有兩種主要的 cleft，讀者都得認得。

### it-cleft：`It is/was X that/who ...`

```
原句（無強調）：The firmware caused the crash.

it-cleft 強調主詞：
  It was the firmware that caused the crash.
  └─ It was [被強調的東西] that [其餘的話]
  = 「是那個韌體造成了當機」（強調：是韌體，不是別的）

it-cleft 也能強調別的成分：
  It was last Tuesday that the outage began.
  = 「就是上週二，那次斷線開始的」（強調時間）
```

**拆 it-cleft 的關鍵：句首的 `It` 不指任何東西**（不是「它」），它只是撐起強調結構的空殼。把 `It is/was ... that/who ...` 的框架拿掉，被夾在中間的就是被強調的重點，其餘還原成一句普通話。

**警告：別把 it-cleft 的 that 當成關係子句或補語子句去硬拆。** `It was the firmware that caused the crash` 裡的 that 是 cleft 結構的一部分，不是修飾 firmware 的關係子句。判斷法：句子開頭是 `It is/was`，就先往 cleft 想。

### wh-cleft（pseudo-cleft）：`What ... is/was X`

```
原句：The team needs more time.

wh-cleft：
  What the team needs is more time.
  └─ What [一件事] is [被強調的答案]
  = 「這團隊需要的，是更多時間」（把「更多時間」放句尾強調）
```

wh-cleft 用 `What ...` 開頭當主詞（一個名詞子句，Ch 9 學過），然後用 is/was 把「答案」隆重推到句尾。這是尾焦點原則的極致運用——**把最重要的資訊放在整句最後一個位置**。技術寫作和演講很愛用，因為它製造「先鋪陳、再揭曉」的節奏。

**拆 wh-cleft：`What ... is/was X` → 把 What 子句還原成正常句，X 是它強調的重點。** `What the team needs is more time` → `The team needs more time`，重點在 `more time`。

---

## 難句四：長距離依賴（long-distance dependency）——相關的兩個詞被拉到天涯海角

**長距離依賴**指句子裡兩個在文法上相關、必須配對理解的成分，被中間插入的一大段東西**拉開了很遠的距離**。你讀到後面那個，得回頭把它接回前面那個——如果中間隔太遠，工作記憶跟不上，就斷線。

最典型的兩種：

### 主詞和它的動詞被拉開

```
The proposal [that the committee, after weeks of heated debate, had finally
approved] was rejected by the board.

主詞：The proposal ................................................. was rejected
       └─ 中間插了一整個關係子句（還帶一個逗號夾住的插入語）──┘
```

你讀到 `was rejected` 時，主詞 `The proposal` 已經在四行前——中間隔了關係子句、又隔了 `after weeks of heated debate` 這個插入語。**對策（Ch 9 的壓棧法在這裡再次救命）：讀到主詞就在心裡「掛住它、等它的動詞」，中間的插入全部先當背景略讀，直到撞上那個對得起主詞的主要動詞。**

### 疑問詞／關代和它的「空位（gap）」被拉開

在關係子句或問句裡，開頭的 wh- 詞對應句子後面某處的一個**空位（gap，該有成分卻空著的位置）**。空位越靠後，依賴越長：

```
This is the exploit [which] researchers believe attackers had been using ___
                     └──┘                                             └gap┘
                      關代 which 對應到最後 using 後面的空位
                      （using 什麼？= the exploit）
```

`which` 在句首、它的空位（using 的受詞）在句尾，中間隔了 `researchers believe attackers had been`。你得把最後那個空位接回開頭的 which（= the exploit）才讀懂：「研究人員認為攻擊者一直在用的那個 exploit」。**對策：看到 wh- 詞開頭的子句，讀的時候留意「後面哪裡缺了一個成分」，那個缺口就是 wh- 詞的歸宿。**

---

## 難句五：花園小徑句（garden-path sentence）——大腦被騙進岔路

**花園小徑句**指一種句子：你順著讀，大腦在半路做了一個**看似合理、實則錯誤**的結構預測，走進岔路（「被引進花園小徑」），讀到後面發現不通，得倒回去重新解析。它不是文法錯，是**故意或無意地利用了歧義**。

經典教科書例句：

```
The old man the boats.

第一次讀：The old man（那個老人）... 然後呢？句子沒動詞了，卡住。
重新解析：man 在這裡是「動詞」（to man = 操作、駕駛）！
          The old（老人們，the + 形容詞 = 那類人）man（駕駛）the boats.
          = 「老人們駕駛那些船。」
```

```
The horse raced past the barn fell.

第一次讀：The horse raced past the barn（馬跑過穀倉）... fell？多一個動詞，卡住。
重新解析：raced past the barn 是縮減關係子句（= that was raced past the barn）！
          The horse [raced past the barn] fell. 主要動詞是 fell。
          = 「那匹被騎過穀倉的馬，跌倒了。」
```

真實新聞和技術文較少出現這麼極端的花園小徑句（好作者會避免），但**縮減關係子句造成的短暫誤讀**（Ch 9 那個 `The report released yesterday...`）本質上就是輕量版的花園小徑。認得這個現象，你在「讀到一半發現不通」時，第一反應不是「我看錯字了」，而是**「我剛才的結構預測錯了，倒回去換一種解析」**——這個 debug 心態，是老練讀者和卡住的讀者最大的差別。

---

## 新聞英文 vs 技術英文：兩種句構指紋

拆句技巧之外，這章還要給你一個**宏觀武器**：認出文體的句構指紋。不同文體有各自偏愛的句型，你一旦認出「這是新聞句法」或「這是技術句法」，讀之前就有預期，大腦的預測命中率大增，讀速和理解都跳一階。

### 新聞英文的指紋

- **前置重點（前重心）**：新聞的第一句（導言 lead）通常把「誰、做了什麼、何時何地」塞在最前面，因為讀者可能只讀第一句。所以新聞句常是「主幹先給，細節往後掛」。
- **同位語堆疊（apposition）**：用逗號把人物的頭銜／身分補在名字旁。`Jane Smith, the company's chief security officer, warned that...`——`the company's chief security officer` 是同位語，補充 Jane Smith 是誰，讀時當「附帶說明」略過即可。
- **消息來源歸屬（attribution）**：新聞必須交代「這是誰說的」，於是充斥 `officials said / according to the report / the minister claimed / sources told the BBC`。這些 attribution 常掛在句尾或用逗號插在中間，**它們是「來源標籤」不是主要內容**，你可以先抓「說了什麼」，再看「誰說的、可信度如何」。注意動詞選字帶立場：`said`（中性）vs `claimed / alleged`（暗示存疑）vs `admitted`（暗示不利）——**attribution 動詞是新聞語氣的密碼。**
- **headlinese（標題體）**：標題為求短，省略冠詞和 be 動詞、用現在式表過去、用不定詞表未來。`Minister to resign amid probe`（= A minister is going to resign amid a probe）。標題自成一套語法，讀正文前先破譯標題，能預告全文重點。
- **非限定子句與分詞構句塞背景**：`, who had led the department since 2019,` / `Speaking at a press conference, ...`——記者用這些把已知背景壓縮塞進句子，讓主句能直奔新資訊。

一句典型新聞句，指紋全開：

```
Speaking at a press conference on Tuesday, the health minister, who has faced
mounting criticism, said the new measures would be reviewed within weeks.

├─ Speaking at a press conference on Tuesday,     ← 分詞構句（背景：何時何地在說）
├─ the health minister,                            ← 主詞
│    └─ who has faced mounting criticism,          ← 非限定關係子句（補充背景）
├─ said                                            ← attribution 動詞（中性）
└─ (that) the new measures would be reviewed within weeks   ← that 補語子句（他說的內容 = 真正新資訊）

讀法：先抓主幹「the health minister said [新措施幾週內會被檢討]」，
     句首分詞構句和逗號夾的關係子句都是背景，最後留意 said 是中性歸屬。
```

### 技術英文的指紋

- **名詞化（nominalization）**：把動詞、形容詞變成名詞來用（Ch 8 的主題）。`The system fails → the failure of the system`；`allocate → allocation`。技術文靠名詞化把「動作」打包成「概念」再堆疊，句子密度極高、動詞很少很弱（常是 is/occurs/results in）。**讀技術文要練「把名詞化還原成動作」**：讀到 `the allocation of the buffer` 想成「配置這個 buffer（這個動作）」。
- **被動語態**：如前所述，聚焦系統行為、隱去執行者。技術文的被動密度遠高於一般寫作。
- **條件句與規格語氣**：`If X, then Y` / `When the flag is set, ...` / `Unless otherwise specified, ...`。技術文大量用條件句描述行為分支，讀時要抓清楚「條件是什麼、結果是什麼」的配對。
- **RFC 的 MUST / SHOULD / MAY**（規格文件的特有指紋）：這些大寫詞在 RFC/標準文件裡是**有嚴格定義的規範等級**（出自 RFC 2119）——`MUST`（絕對要求）、`MUST NOT`（絕對禁止）、`SHOULD`（強烈建議，但有正當理由可不做）、`MAY`（可選）。讀規格時，這些字決定了「這條是硬規定還是建議」，是規格語意的核心，不能當普通字略過。
- **大量「已定義過的術語」**：技術文會先定義一個術語，之後整篇反覆用它，且用得很精確。所以技術文的「生字」很多是**該文件內部定義的專名**，回頭查定義即可，不是要你事先都懂。

一句典型技術句，指紋全開：

```
If the validation of the incoming token fails, the request MUST be rejected
and an error response is returned to the client.

├─ If the validation of the incoming token fails,      ← 條件句（含名詞化 validation of...）
│     （名詞化還原：如果「驗證進來的 token」這個動作失敗）
├─ the request MUST be rejected                         ← 被動 + MUST（硬規定：請求必須被拒絕）
└─ and an error response is returned to the client.     ← 被動（回傳錯誤回應給 client）

讀法：抓條件「若 token 驗證失敗」→ 結果「請求必須被拒（MUST=強制）、並回錯誤給 client」。
     MUST 是規範等級不可忽略，兩個被動聚焦在「請求」「回應」而非執行者。
```

---

## 對比與取捨

把兩種文體的指紋並排，這是本章最該內化的一張表：

| 指紋面向 | 新聞英文 | 技術英文 |
|---|---|---|
| 重點擺放 | 前置（lead 先給誰做了什麼） | 條件在前、規範／結果在後 |
| 被動語態 | 有，常為隱去/淡化施事者 | 大量，為聚焦系統行為 |
| 名詞化 | 中等 | 極高（動作打包成名詞概念） |
| 招牌結構 | 同位語、非限定子句、分詞構句塞背景 | 條件句、規格語氣、定義過的術語 |
| 來源／規範標記 | attribution（said/claimed/according to） | RFC 的 MUST/SHOULD/MAY |
| 語氣密碼 | attribution 動詞選字（said vs claimed vs admitted） | 規範等級詞（MUST vs SHOULD vs MAY） |
| 生字性質 | 專有名詞多（人名地名機構名，可跳過） | 內部定義術語多（回查定義即可） |
| 讀者該練的動作 | 分離「誰說的」與「說了什麼」、讀 attribution 語氣 | 把名詞化還原成動作、抓條件-結果配對 |

取捨與心法：

- **讀之前先判文體，套對應的讀法。** 同樣是被動句，新聞裡你要問「施事者為什麼被藏？」，技術文裡你通常不必——被動只是慣例。判錯文體會白費力氣或漏掉言外之意。
- **難句拆解有固定優先順序**：(1) 先認文體指紋、建立預期 →(2) 找主幹（跳過所有子句、插入語、attribution、同位語）→(3) 還原被扭曲的語序（被動轉主動、倒裝復位、cleft 拆框架）→(4) 回頭補子句與修飾 →(5) 對新聞讀 attribution 語氣、對技術文讀規範等級。**照這個順序走，再難的句子都有把手。**
- **不是每個扭曲都要完全還原。** 熟練後，很多被動、cleft 你會「直接懂」，不必真的在腦中翻回主動。還原是給卡住時用的除錯工具，不是每句必做的儀式。目標是自動化（Ch 1），不是儀式化。

---

## 踩雷集錦

1. **把 it-cleft 的開頭 `It` 當成真正的代名詞去找它指誰。**
   `It was the misconfigured firewall that let the traffic through.` 有人苦苦找「It 指前面哪個東西」——找不到，因為它什麼都不指。
   → 正解：`It was ... that ...` 是 cleft 強調框架，It 是空殼。被強調的是 `the misconfigured firewall`，還原成 `The misconfigured firewall let the traffic through`。**看到句首 `It is/was` 先往 cleft 想。**

2. **被動 + 省略施事者，卻沒警覺「誰做的被藏起來了」。**
   新聞句 `Mistakes were made and lessons will be learned.` 讀成中性的客觀陳述就漏了重點。
   → 正解：這是被動＋無施事者的經典「迴避責任」句式（誰犯的錯？誰該學教訓？全沒講）。**讀新聞遇到重要動作用被動且無施事者，主動追問「誰做的、為何不講」，這常是言外之意所在。**

3. **看到否定詞開頭的倒裝，誤以為是問句或句子壞掉。**
   `Rarely has a single bug caused so much damage.` 有人卡在「怎麼 has 在主詞前面、這是問句嗎？」
   → 正解：句首否定/半否定副詞（Rarely/Never/Not only…）觸發倒裝，這是強調用的陳述句。還原：`A single bug has rarely caused so much damage.` **句首 Never/Rarely/Not only + 助動詞 = 倒裝，不是問句。**

4. **在技術文裡把名詞化「當靜態的東西」讀，讀不出動作與因果。**
   `The corruption of the heap metadata leads to the failure of the allocator.` 讀成一串抽象名詞，糊成一團。
   → 正解：把名詞化還原成動作再讀——「heap metadata 被破壞（動作）→ 導致 allocator 失效（動作）」，因果就清楚了。**技術文讀不動時，先把每個名詞化 `the X of Y` 翻回 `X 這個動作/狀態，作用在 Y 上`。**

5. **忽略 RFC 的 MUST/SHOULD/MAY 是規範等級，當普通字讀過。**
   `The client SHOULD retry the request, but it MUST NOT retry more than three times.` 讀成「客戶端應該重試，但不能重試超過三次」——把 SHOULD 和 MUST NOT 讀成同一種強度。
   → 正解：SHOULD = 強烈建議（有正當理由可不做），MUST NOT = 絕對禁止（沒有例外）。**在規格文件裡，這兩者強度天差地別，是實作對錯的分界線，絕不能當同義字。**（定義出自 RFC 2119）

6. **把縮減關係子句造成的短暫卡住當成「自己看錯」，硬用原解析讀下去。**
   `The files copied to the server were corrupted.` 讀成「檔案複製到伺服器……were？」然後懷疑自己漏字。
   → 正解：這是輕量版花園小徑——`copied to the server` 是縮減關係子句（修飾 files），主要動詞是 `were corrupted`。**讀到一半不通時，正確反應是「我的結構預測錯了，換一種解析」，而不是「我一定看錯字了」。**

---

## 進階：再往深一層

- **尾焦點與尾重量：英文為什麼這樣安排語序，有統一原理。** 本章的被動、cleft、倒裝，很多都可以用兩條原則統一解釋：**end-focus（尾焦點）**——新／重要資訊放句尾；**end-weight（尾重量）**——結構越長越重的成分放句尾（避免頭重腳輕）。被動把長的施事者推到句尾（`... was designed by a team of researchers from three universities`）、wh-cleft 把答案推到句尾，都是在服從這兩條。理解這層，你不只認得結構，還能**預測**作者為什麼選它、句尾多半是重點——這對抓「作者到底想強調什麼」極有用。

- **attribution 的隱藏立場，是新聞批判性閱讀的核心。** 進一步說，attribution 不只選動詞（said/claimed/admitted），還選**要不要具名**（`officials said` vs `a senior official who declined to be named said`）、**放在句首還是句尾**（放句首會讓「誰說的」更突出）。老練的讀者從 attribution 的這些選擇，讀出記者對消息可信度的暗示、以及責任歸屬的操作。這是從「讀懂字面」升到「讀懂新聞如何被建構」的一步，Part 5 精讀 BBC 時會實地練。

- **花園小徑句揭示的是「人腦是即時、增量地解析句子」。** 心理語言學（psycholinguistics）研究顯示，人讀句子不是讀完整句才分析，而是**每讀一個字就即時更新一個結構預測**（incremental parsing）。花園小徑句之所以卡人，就是它讓這個即時預測在半路走錯。這解釋了為什麼「回頭重讀」（regression）是正常且必要的——連母語者都會。你該追求的不是「一次讀對」，而是「走錯時能快速偵測並修正」。這也是為什麼練到自動化後，你的「修正」會快到自己沒察覺。

- **被動的「真假」與訊息結構。** 語言學區分 `be + p.p.` 是動態被動（`The window was broken by a rock` — 一個事件）還是狀態被動／形容詞（`The window was broken` — 一個狀態）。對閱讀而言，多數時候不影響大意，但在推敲「是描述一次動作，還是描述一個既成狀態」時（例如追究時序、因果）會有差。需要精確時，看有沒有 `by 施事者`、有沒有時間點、動詞本身是否表變化，來判斷。

---

## 動手練習

對每句：(a) 指出它用了哪種難句結構；(b) 還原成正常語序（或還原被強調/被藏的成分）；(c) 寫中文大意；(d) 若是新聞或技術句，指出一個文體指紋。做完再看參考。

1. `It was the third-party library that introduced the vulnerability.`（哪種 cleft？被強調的是什麼？）

2. `Never before had the agency issued such a stark warning.`（哪種結構？還原語序）

3. `The data collected over six months was analysed by an independent lab.`（找主要動詞；施事者是誰；有幾個過去分詞、各是什麼角色？）

4. `What the standard requires is that all connections be encrypted.`（哪種 cleft？重點在哪？）

5. `Had the backup completed, the outage would have been trivial.`（哪種結構？補回省略的字）

6. `The suspect, who police say fled the country last week, has been charged in absentia.`（找主幹；指出兩個新聞指紋；attribution 藏在哪、語氣如何？）

<details>
<summary>參考拆解</summary>

**1.** it-cleft，強調主詞。
- 框架 `It was ... that ...` 拿掉 → `The third-party library introduced the vulnerability.`
- 被強調的：`the third-party library`（是那個第三方函式庫，不是別的）。
- 大意：就是那個第三方函式庫引入了這個漏洞。
- 指紋（技術）：聚焦「哪個元件是禍首」，cleft 用來鎖定責任元件。

**2.** 否定詞開頭的倒裝（Never before + 助動詞 had 提前）。
- 還原：`The agency had never before issued such a stark warning.`
- 大意：該機構前所未有地發出如此嚴厲的警告。
- 指紋（新聞）：倒裝製造強調與氣勢，正式評論常見。

**3.**
- 主要動詞：`was analysed`（被動）。
- 施事者：`by an independent lab`（一家獨立實驗室）。
- 兩個過去分詞：`collected`（縮減關係子句，修飾 The data = 蒐集了六個月的那些資料，**不是**主要動詞）；`analysed`（與 was 組成被動主要動詞）。
- 主幹：`The data ... was analysed by an independent lab.`
- 大意：那些蒐集了六個月的資料，由一家獨立實驗室分析。
- 指紋（技術/新聞皆可）：被動聚焦資料與流程；縮減關係子句壓縮背景。

**4.** wh-cleft（pseudo-cleft），重點推到句尾。
- 還原：`The standard requires that all connections be encrypted.`
- 重點：`that all connections be encrypted`（所有連線都必須加密——注意 `be encrypted` 是虛擬語氣，規範用法）。
- 大意：這份標準所要求的，是所有連線都必須加密。
- 指紋（技術）：wh-cleft 把「規範內容」隆重放句尾；that 子句是規格內容。

**5.** 省略 if 的倒裝條件句。
- 補回：`If the backup had completed, the outage would have been trivial.`
- 大意：假如備份當時有完成，這次斷線本來會是小事一樁。
- 指紋（技術/正式）：Had + 主詞 = 省 if 的條件句。

**6.**
- 主幹：`The suspect ... has been charged in absentia.`（被動：嫌犯已被缺席起訴）
- 指紋一：非限定關係子句 `who ... fled the country last week` 塞背景。
- 指紋二：attribution —— `police say` 就藏在關係子句裡（`who police say fled...` = 警方說他上週潛逃；這是 zero-that 的補語，插進關係子句）。語氣：用 `police say` 而非直接斷言「他潛逃了」，是記者對「未經證實資訊」的標準免責歸屬，暗示這是警方說法、非本報認定。
- 大意：這名嫌犯——警方稱其上週已潛逃出境——已遭缺席起訴。

</details>

---

## 本章重點整理

- 這章的難句共同點：**為修辭目的扭曲了正常語序**。拆解通用心法是**還原成正常語序**。
- **被動語態**（be + 過去分詞）把受事者提為主詞、施事者藏到句尾或省略。新聞用它隱去/淡化施事者（讀時要追問「誰做的、為何不講」），技術文用它聚焦系統行為。
- **倒裝**三型：否定詞開頭（Never/Rarely/Not only + 助動詞）、地方副詞開頭（主詞在動詞後）、省 if 的條件句（Had/Were/Should + 主詞）。都不是問句，還原成正常語序即懂。
- **分裂句**：it-cleft（`It is/was X that...`，It 是空殼、X 是強調重點）、wh-cleft（`What... is X`，把答案推句尾）。別把 cleft 的 that 當關係子句。
- **長距離依賴**：主詞與動詞、wh- 詞與其空位被拉遠。用壓棧法「掛住主詞等動詞」「找 wh- 詞的後方空位」。
- **花園小徑句**：大腦即時解析走進岔路，讀到不通要**換解析**而非疑心自己看錯字。縮減關係子句是其輕量版。
- **兩種文體指紋**：新聞（前置重點、同位語、非限定子句/分詞塞背景、attribution 語氣、headlinese）；技術（名詞化、被動、條件句、RFC 的 MUST/SHOULD/MAY、內部定義術語）。**先判文體、套對讀法。**
- **難句拆解優先順序**：認文體指紋 → 找主幹 → 還原扭曲語序 → 回補子句修飾 → 讀 attribution 語氣／規範等級。

---

## 自我檢核

不看上文，主動回想：

- [ ] 我能把一個被動句還原成主動、並找出被省略的施事者嗎？我知道新聞和技術文用被動的不同動機嗎？
- [ ] 看到 `Never has... / Had the system... / At the entrance stood...`，我能認出是倒裝並還原語序嗎？
- [ ] 我能區分 it-cleft 和 wh-cleft，並知道 cleft 的 `It`／`that` 不是普通代名詞／關係子句嗎？
- [ ] 讀到一半發現句子不通，我的第一反應是「換一種結構解析」而不是「我看錯字」嗎？
- [ ] 我能列出新聞英文和技術英文各至少三個句構指紋，並說出各自對應的讀法嗎？
- [ ] 我知道 RFC 的 MUST / SHOULD / MAY 是有嚴格定義的規範等級、不能當同義字嗎？
- [ ] 我能講出難句拆解的固定優先順序嗎？

---

## 延伸閱讀

- **[Cambridge Dictionary — Grammar: The passive / Inversion / Cleft sentences](https://dictionary.cambridge.org/grammar/british-grammar/)** — 被動、倒裝、cleft 三個條目都在這裡，免費、每條配例句，是本章結構的第一查詢站。
- **Michael Swan, *Practical English Usage*（4th ed., Oxford）** — 查「passive」「inversion」「cleft sentences」「emphasis」。Swan 對「什麼時候用、真實文本怎麼出現」講得最實用，補足字典的規則式說明。
- **[RFC 2119 — Key words for use in RFCs to Indicate Requirement Levels](https://www.rfc-editor.org/rfc/rfc2119)** — 技術規格 MUST/SHOULD/MAY 的定義原文，只有兩頁，讀技術標準文件前必看。你以後讀任何 RFC/W3C/標準文件，這是解讀規範語氣的鑰匙。
- **[BBC News Style Guide](https://www.bbc.co.uk/newsstyleguide)** — 想深挖新聞英文指紋（attribution、headlinese、如何寫 lead）的權威來源，直接看記者被要求怎麼寫，反推你該怎麼讀。
- **Huddleston & Pullum, *The Cambridge Grammar of the English Language*（2002）** — 想深挖被動、cleft、information packaging（訊息結構、end-focus/end-weight）背後語言學原理的權威（選讀 information packaging 相關章節）。上面「進階」小節的原理出自這裡。

Part 2 到此收官：你已經有一整套系統化的拆句流程——找主幹（Ch 7）、拆名詞堆疊（Ch 8）、追子句嵌套（Ch 9）、還原難句與辨文體指紋（Ch 10）。接下來別急著往下讀理論，先去**練習 B** 用 20 個真實風格長句把這套流程跑到手感出來——那才是把知識變成能力的關鍵一步。練完，Part 3 我們轉向「閱讀方法論」：extensive vs intensive、怎麼選對材料、怎麼把一篇文章榨乾。

→ [下一章：練習 B——拆解 20 個真實長句](./practice-b-dissect-20-sentences.md)
