# 練習 B — 拆解 20 個真實風格長句

> **目標**：把 Part 2（Ch 7–10）的整套拆句流程，用 20 個真實風格的長句跑到手感出來。10 句 BBC 新聞風、10 句技術文風，難度由淺到深。做完你要能對任何一個陌生長句：秒抓主幹、認出結構類型、追住子句層次、還原被扭曲的語序、寫出正確中文大意。**這是 Part 2 從「知道」變「會做」的關鍵一步——不做這個練習，前四章等於白讀。**

---

## 背景

Ch 7–10 給了你四樣工具：

- **Ch 7 找主幹**：剝掉所有修飾，抓出主詞—主要動詞—受詞的骨架。
- **Ch 8 拆名詞堆疊**：拆開層層修飾的長名詞片語、把名詞化還原成動作。
- **Ch 9 追子句嵌套**：認四大類子句（關係／that 補語／分詞構句／wh- 名詞子句），用壓棧法追多層嵌套。
- **Ch 10 還原難句 + 辨文體指紋**：被動、倒裝、cleft、長距離依賴、花園小徑；新聞 vs 技術的句構指紋。

工具都拿到了，但工具擺在工具箱裡不會讓你變強——**你得真的拿它們去修一堆句子，修到動作變成反射。** 這就是這份練習的用途。閱讀是肌肉不是知識（README 講過），拆句尤其是。

---

## 任務規格

下面有 20 個長句，第 1–10 句是 BBC 新聞風格，第 11–20 句是技術文風格，每組內大致由易到難。對**每一句**，你要產出四樣東西：

1. **主幹**：寫出剝除所有修飾後的主詞—主要動詞（—受詞）。
2. **ASCII 拆解樹**：用縮排把各層子句／修飾／插入語畫出來，標明哪個掛在哪一層。
3. **結構類型標籤**：列出這句用到的結構（例如：非限定關係子句、被動、it-cleft、分詞構句、名詞化、attribution…）。
4. **中文大意**：一句通順的繁體中文翻譯／大意。

**做法要求**：先自己拆完全部 20 句、寫下答案，**再**展開下方的參考拆解對照。不要邊看邊做——那樣練不出反射。

---

## 期望輸出範例

先示範一句（不在正式 20 題內），讓你知道「一份合格的拆解」長什麼樣。

**例句**：`The report, which was commissioned by the government, warned that the risks had been underestimated.`

**主幹**：`The report ... warned [that 子句]`（那份報告警告……）

**ASCII 拆解樹**：
```
The report ....................................... warned [受詞子句]     ← 主幹
   └─ , which was commissioned by the government,                        ← 非限定關係子句（補充：受政府委託）
                                                          └─ that the risks had been underestimated   ← that 補語子句（warned 的受詞＝警告的內容）
                                                                （內含被動 had been underestimated）
```

**結構類型**：非限定關係子句、被動語態（was commissioned / had been underestimated）、that 補語子句。

**中文大意**：這份受政府委託的報告警告，那些風險先前被低估了。

看到那份「合格答案」的顆粒度了嗎？——主幹拎得乾淨、每層子句掛對位置、結構標籤齊全、大意通順。下面 20 句就照這個標準做。

---

## 20 個待拆句

### Part A — BBC 新聞風格（第 1–10 句）

1. The minister announced that the scheme would be scrapped.

2. The company, which employs over 5,000 people, has denied any wrongdoing.

3. Speaking outside the court, the lawyer said her client would appeal.

4. It was the leaked memo that finally forced the chief executive to resign.

5. Three people were injured in the blast, which struck a crowded market on Sunday morning.

6. Rarely has a government faced such widespread public anger over a single policy.

7. The documents, seen by the BBC, suggest that officials were warned months in advance.

8. The president, who has ruled the country for over two decades, dismissed the allegations as politically motivated.

9. What critics have long feared is that the new law will be used to silence dissent.

10. Analysts say the measures, though welcomed by industry, are unlikely to reverse the downturn that has gripped the sector since the pandemic.

### Part B — 技術文風格（第 11–20 句）

11. The function returns a null pointer when allocation fails.

12. The buffer that stores incoming packets must be flushed before the connection is closed.

13. If the checksum does not match, the packet is silently discarded.

14. The vulnerability, first reported in 2021, allows an attacker to execute arbitrary code.

15. Given the constraints described above, the scheduler cannot guarantee real-time performance.

16. What the specification requires is that every request be authenticated before it is processed.

17. The corruption of the heap metadata, which occurs when the allocator writes past the end of a chunk, can be leveraged to hijack control flow.

18. The library the team had integrated turned out to contain a backdoor that had gone unnoticed for years.

19. Had the input been properly sanitised, the injection attack would have failed.

20. The paper demonstrates that the technique, which had been dismissed as impractical, can be applied to real-world binaries whose source code is unavailable.

---

## 如果你卡住了

- **完全不知道從哪下手** → 永遠先做 Ch 7 那一步：**把所有逗號夾住的東西、所有 who/which/that 開頭的子句、所有 -ing/-ed 開頭的片語，先用括號整團括起來當「暫時看不見」**，剩下沒被括的骨頭就是主幹。先把主幹讀懂，再回頭一團一團拆。
- **主詞和動詞離太遠、讀到後面忘了主詞** → 用 Ch 9/10 的壓棧法：讀到主詞就在紙上把它圈起來、寫個「等動詞」，中間所有插入的東西都先略過，直到撞上對得起這個主詞的主要動詞。
- **that 不知道是關係子句還是補語子句** → 用 Ch 9 判準：**看 that 後面的子句缺不缺成分**。缺（少主詞或受詞）→ 關係子句；不缺（完整一句）→ 補語子句。
- **-ed 結尾的詞不知道是主要動詞還是修飾** → 用 Ch 9/10 判準：**如果這個 -ed 後面還冒出另一個動詞**，前面那個 -ed 多半是縮減關係子句或分詞（修飾），後面那個才是主要動詞。
- **看到 `It was ... that ...` 硬要找 It 指誰** → 停。用 Ch 10：這是 it-cleft，It 是空殼，被 `It was` 和 `that` 夾住的才是強調重點，把框架拿掉還原成普通句。
- **句子讀起來就是不通** → 用 Ch 10 的花園小徑心態：**很可能是你半路的結構預測錯了，倒回去換一種解析**（最常見的兇手是把「縮減關係子句的 -ed」誤當主要動詞）。不是你看錯字。

---

## 實作步驟

1. **準備**：拿紙筆或開一個文字檔。**一句一句來，不要跳。** 每句先抄下來（抄一遍本身就在幫你放慢、看清結構）。
2. **第一輪——只抓主幹**：對每句，先只做「括號法」把修飾括掉、寫出主幹。20 句全部做完這一輪。這一輪練的是「一眼看穿骨架」。
3. **第二輪——畫樹＋貼標籤**：回到每一句，畫 ASCII 拆解樹、標出每個結構類型。這一輪練的是「追層次、認結構」。
4. **第三輪——寫中文大意**：每句寫一句通順的中文。翻不順，代表結構其實還沒真懂，回頭重看第二輪的樹。
5. **對答案**：展開下方 `<details>`，逐句比對。**重點不是「我翻得對不對」，是「我的主幹抓對了嗎、結構判對了嗎」**——這兩個對了，大意自然對；這兩個錯了，就算矇對大意也是運氣。
6. **重做錯題**：把主幹或結構判錯的句子挑出來，隔一天再拆一次（間隔重複，Ch 5 的原理），直到不看答案也能拆對。

---

## 完整參考解答

<details>
<summary>Part A：BBC 新聞風格（第 1–10 句）參考拆解</summary>

**1.** The minister announced that the scheme would be scrapped.
```
The minister announced [受詞子句]           ← 主幹
                  └─ that the scheme would be scrapped   ← that 補語子句（宣布的內容；內含被動 be scrapped）
```
結構：that 補語子句、被動語態。
大意：部長宣布，該方案將被廢除。

**2.** The company, which employs over 5,000 people, has denied any wrongdoing.
```
The company ................................. has denied any wrongdoing.   ← 主幹
   └─ , which employs over 5,000 people,                                    ← 非限定關係子句（補充：雇用逾五千人）
```
結構：非限定關係子句。
大意：這家雇用逾五千人的公司否認有任何不當行為。

**3.** Speaking outside the court, the lawyer said her client would appeal.
```
Speaking outside the court,  the lawyer said [受詞子句]                     ← 主幹在逗號後
└──────────────────────┘        └─ (that) her client would appeal          ← that 補語子句（省 that；她說的內容）
分詞構句（-ing，背景：在法院外發言；隱藏主詞＝the lawyer）
```
結構：分詞構句、that 補語子句（省 that）、attribution（said）。
大意：這位律師在法院外表示，她的當事人將提出上訴。

**4.** It was the leaked memo that finally forced the chief executive to resign.
```
It was [被強調成分] that [其餘]              ← it-cleft 框架（It 是空殼）
        └─ the leaked memo                    ← 被強調的重點
                          └─ that finally forced the chief executive to resign
還原：The leaked memo finally forced the chief executive to resign.
```
結構：it-cleft（強調主詞）。
大意：正是那份外洩的備忘錄，最終逼得執行長辭職。

**5.** Three people were injured in the blast, which struck a crowded market on Sunday morning.
```
Three people were injured in the blast ...                     ← 主幹（被動）
                                        └─ , which struck a crowded market on Sunday morning   ← 非限定關係子句（先行詞＝the blast）
```
結構：被動語態（were injured，施事者省略）、非限定關係子句。
大意：這起爆炸造成三人受傷；爆炸於週日早晨襲擊一處人潮擁擠的市場。

**6.** Rarely has a government faced such widespread public anger over a single policy.
```
Rarely has a government faced ...            ← 否定副詞開頭的倒裝
還原：A government has rarely faced such widespread public anger over a single policy.
主幹：a government has faced ... anger
```
結構：否定詞開頭倒裝（Rarely + has）。
大意：一個政府很少因單一政策而面臨如此廣泛的民怨。

**7.** The documents, seen by the BBC, suggest that officials were warned months in advance.
```
The documents ................. suggest [受詞子句]                    ← 主幹
   └─ , seen by the BBC,                                              ← 縮減關係子句（＝which were seen by the BBC；被動）
                              └─ that officials were warned months in advance   ← that 補語子句（內含被動 were warned）
```
結構：縮減關係子句（被動分詞）、that 補語子句、被動語態、attribution 意味（suggest）。
大意：這些經 BBC 過目的文件顯示，官員早在數月前就已被警告。

**8.** The president, who has ruled the country for over two decades, dismissed the allegations as politically motivated.
```
The president ....................................... dismissed the allegations as politically motivated.   ← 主幹
   └─ , who has ruled the country for over two decades,                                                     ← 非限定關係子句（補充背景）
```
結構：非限定關係子句、`dismiss ... as ...` 句式（帶立場：斥為）。
大意：這位執政逾二十年的總統，將這些指控斥為出於政治動機。

**9.** What critics have long feared is that the new law will be used to silence dissent.
```
What critics have long feared  is  that the new law will be used to silence dissent.
└────────────────────────┘         └──────────────────────────────────────────┘
wh-cleft 主詞（What 名詞子句）        被強調的答案（that 補語子句，推到句尾；內含被動 be used）
還原：Critics have long feared that the new law will be used to silence dissent.
```
結構：wh-cleft（pseudo-cleft）、that 補語子句、被動語態。
大意：批評者長久以來所擔憂的，是這部新法將被用來壓制異議。

**10.**（最難）Analysts say the measures, though welcomed by industry, are unlikely to reverse the downturn that has gripped the sector since the pandemic.
```
Analysts say [受詞子句]                                                              ← attribution 主幹
       └─ the measures ............................. are unlikely to reverse the downturn ...   ← 被說的內容（內層主幹）
             └─ , though welcomed by industry,                                        ← 讓步的縮減狀語子句（＝though they are welcomed by industry；被動）
                                                          └─ that has gripped the sector since the pandemic   ← 限定關係子句（修飾 the downturn）
```
結構：attribution（say）＋省 that 的補語子句、縮減讓步狀語子句（though + 被動分詞）、被動語態、限定關係子句。
大意：分析師表示，這些措施雖受業界歡迎，卻不太可能扭轉自疫情以來緊掐該產業的這波衰退。

</details>

<details>
<summary>Part B：技術文風格（第 11–20 句）參考拆解</summary>

**11.** The function returns a null pointer when allocation fails.
```
The function returns a null pointer ...      ← 主幹
                                    └─ when allocation fails   ← 時間/條件狀語子句（內含名詞化 allocation）
```
結構：狀語子句（when）、名詞化（allocation ＝ allocate 的名詞）。
大意：當配置失敗時，這個函式回傳一個空指標。

**12.** The buffer that stores incoming packets must be flushed before the connection is closed.
```
The buffer ......................... must be flushed ...                    ← 主幹（被動 + must）
   └─ that stores incoming packets                                          ← 限定關係子句（修飾 buffer）
                                     └─ before the connection is closed     ← 時間狀語子句（內含被動 is closed）
```
結構：限定關係子句、被動語態（must be flushed / is closed）、狀語子句（before）。
大意：儲存進入封包的那個緩衝區，必須在連線關閉前被清空。

**13.** If the checksum does not match, the packet is silently discarded.
```
If the checksum does not match,  the packet is silently discarded.
└──────────────────────────┘    └──────────── 主幹（被動）──────────┘
條件狀語子句
```
結構：條件句（If）、被動語態（is discarded，施事者省略）。
大意：若校驗和不相符，該封包會被靜默丟棄。

**14.** The vulnerability, first reported in 2021, allows an attacker to execute arbitrary code.
```
The vulnerability ................. allows an attacker to execute arbitrary code.   ← 主幹
   └─ , first reported in 2021,                                                     ← 縮減關係子句（＝which was first reported in 2021；被動）
```
結構：縮減關係子句（被動分詞）、`allow ... to ...` 句式。
大意：這個最早於 2021 年被回報的漏洞，讓攻擊者得以執行任意程式碼。

**15.** Given the constraints described above, the scheduler cannot guarantee real-time performance.
```
Given the constraints described above,  the scheduler cannot guarantee real-time performance.
└─────────────────────────────────┘    └──────────────── 主幹 ────────────────────────────┘
分詞構句（Given ＝過去分詞，表條件：在上述限制下）
   └─ described above   ← 縮減關係子句（修飾 constraints；被動分詞）
```
結構：分詞構句（Given，表條件）、縮減關係子句（described）。
大意：在上述限制下，這個排程器無法保證即時效能。

**16.** What the specification requires is that every request be authenticated before it is processed.
```
What the specification requires  is  that every request be authenticated before it is processed.
└──────────────────────────┘        └──────────────────────────────────────────────────────┘
wh-cleft 主詞                          被強調答案（that 補語子句，含虛擬語氣 be authenticated）
                                        └─ before it is processed   ← 時間狀語子句（被動）
還原：The specification requires that every request be authenticated before it is processed.
```
結構：wh-cleft、that 補語子句（虛擬語氣 be authenticated，規範用法）、被動語態、狀語子句（before）。
大意：這份規格所要求的，是每一個請求都必須在被處理前先通過身分驗證。

**17.**（難）The corruption of the heap metadata, which occurs when the allocator writes past the end of a chunk, can be leveraged to hijack control flow.
```
The corruption of the heap metadata ............... can be leveraged to hijack control flow.   ← 主幹（被動）
   （名詞化 corruption of ... ＝「heap metadata 被破壞」這個動作）
   └─ , which occurs when the allocator writes past the end of a chunk,                          ← 非限定關係子句
                            └─ when the allocator writes past the end of a chunk               ← 內嵌的時間狀語子句
```
結構：名詞化（the corruption of...）、非限定關係子句、內嵌狀語子句（when）、被動語態（can be leveraged）。
名詞化還原讀法：「heap metadata 被破壞（當 allocator 寫超過一個 chunk 的結尾時發生），這件事可被利用來劫持控制流。」
大意：heap 中繼資料的損毀——當配置器寫入超出一個 chunk 的結尾時就會發生——可被利用來劫持控制流程。

**18.**（難）The library the team had integrated turned out to contain a backdoor that had gone unnoticed for years.
```
The library ............................. turned out to contain a backdoor ...        ← 主幹
   └─ (that) the team had integrated                                                   ← zero relative（省 that，修飾 library）
                                          主要動詞：turned out
                                                              └─ a backdoor that had gone unnoticed for years   ← 限定關係子句（修飾 backdoor）
```
壓棧走法：`The library`（主句主詞，等主要動詞）→ 撞到 `the team`（名詞相鄰！zero relative，push 修飾 library）→ 第 1 層 `(that) the team had integrated` 完整，pop → 回主句主要動詞 `turned out to contain a backdoor` → 又撞 `that had gone unnoticed`（push，修飾 backdoor）。
結構：zero relative（省關代的關係子句）、限定關係子句、長距離依賴（主詞 library 與主要動詞 turned out 被拉開）。
大意：這個團隊整合進來的那個函式庫，結果內含一個多年來一直沒被發現的後門。

**19.** Had the input been properly sanitised, the injection attack would have failed.
```
Had the input been properly sanitised,  the injection attack would have failed.
└───────────────────────────────────┘   └──────────────── 主幹 ─────────────────┘
省 if 的倒裝條件句（＝If the input had been properly sanitised；被動 been sanitised）
```
結構：省 if 的倒裝條件句、被動語態（been sanitised）。
大意：假如當初輸入有被妥善清理，這場注入攻擊本來會失敗。

**20.**（最難）The paper demonstrates that the technique, which had been dismissed as impractical, can be applied to real-world binaries whose source code is unavailable.
```
The paper demonstrates [受詞子句]                                                              ← 主幹
       └─ that the technique ................. can be applied to real-world binaries ...        ← that 補語子句（內層主幹，被動 can be applied）
             └─ , which had been dismissed as impractical,                                     ← 非限定關係子句（補充；被動 had been dismissed）
                                                          └─ whose source code is unavailable  ← 限定關係子句（whose，修飾 binaries）
```
壓棧走法：`The paper demonstrates`（主幹＋補語 that）→ push 進 that 子句，主詞 `the technique`（等這層動詞）→ 撞 `, which...` push 進非限定關係子句 → 該層 `which had been dismissed as impractical` 完整，pop → 回 that 子句，動詞 `can be applied to real-world binaries` → 撞 `whose source code...` push（修飾 binaries）。
結構：that 補語子句、非限定關係子句、限定關係子句（whose）、被動語態（had been dismissed / can be applied）、長距離依賴（the technique 與 can be applied 被中間關係子句拉開）。
大意：這篇論文證明，這項曾被斥為不切實際的技術，其實可以應用在原始碼無法取得的真實世界二進位檔上。

</details>

---

## 測試 / 檢核表

逐句對完答案後，用這張表打分。**每句四項各算一分，滿分 80。**

- [ ] **主幹抓對**（20 句）：主詞和主要動詞找對了嗎？（最關鍵——這項錯，整句必歪）
- [ ] **層次掛對**（20 句）：每個子句／修飾掛在正確的那一層、修飾對的東西了嗎？
- [ ] **結構標對**（20 句）：結構類型標籤判對了嗎？（特別是 that 兩用、-ed 是動詞還是修飾、cleft 的辨認）
- [ ] **大意通順且正確**（20 句）：中文讀起來通順、且沒把意思弄反（尤其被動施事者、限定 vs 非限定）？

**判讀你的分數**：

- **70–80**：Part 2 的拆句能力已成形。可以進 Part 3 了。
- **55–69**：主幹大致抓得到，但層次或結構還會判錯。把錯的那幾句標記起來，隔天重拆一次，再往下走。
- **55 以下**：先別急著往下。回頭重讀你最常錯的那一章（多半是 Ch 9 子句或 Ch 10 難句），然後重做這 20 句。**在這裡多花兩天，勝過帶著破洞硬闖 Part 3。**

**自我對照的重點提醒**：不要用「中文翻得像不像參考答案」來評分——翻譯可以有很多種說法。要用**「主幹和結構判斷」**來評分，那才是這份練習真正在測的能力。

---

## 延伸挑戰

1. **實彈演練**：打開一篇真正的 BBC News 文章（挑一則你不熟主題的），找出**最長的三句**，用同樣四步驟拆解。真實句子比這裡的練習句更亂（有破折號、有你不認識的專有名詞），正好練「在雜訊中抓主幹」。
2. **技術實彈**：找一份 RFC（例如 RFC 2616 或任何你工作會碰到的）或一篇 CVE 描述，挑三句含 MUST/SHOULD 或被動的句子拆解，並標出規範等級。
3. **計時挑戰**：把這 20 句（或新找的 20 句）重做一輪，這次計時。目標不是快，是感受「哪些結構你已經一眼看穿（自動化了）、哪些還要停下來想（還沒自動化）」——還要停的那些，就是你接下來要重點練的。
4. **反向造句**：挑三個你覺得最難的結構（例如 wh-cleft、省 if 倒裝、深層嵌套），自己各造一個英文長句。**能造得出來，代表你真的懂了結構，而不只是會拆。**

---

## 自我檢核

不看答案，主動回想：

- [ ] 拿到一個陌生長句，我的第一個動作是「括掉修飾、抓主幹」嗎？
- [ ] 我能穩定分辨 that 是關係子句還是補語子句（靠「後面缺不缺成分」）嗎？
- [ ] 看到 -ed 結尾的詞，我能判斷它是主要動詞還是縮減關係子句/分詞（靠「後面有沒有另一個動詞」）嗎？
- [ ] 看到 `It was ... that ...`、`What ... is ...`、`Had ... , ...`、`Rarely has ...`，我能立刻認出 cleft／倒裝並還原語序嗎？
- [ ] 拆多層嵌套時我能用壓棧法追層次、不弄丟主句主詞嗎？
- [ ] 我能區分新聞句（attribution、非限定子句塞背景）和技術句（名詞化、被動、MUST/SHOULD）的指紋嗎？
- [ ] 這 20 句裡我最常錯的是哪一類結構？我知道接下來要重點練它嗎？

拆句流程練到這裡有手感了，你面對長句就不再是「一片模糊的字海」，而是「一棵能看見枝幹的樹」。這是 Part 2 的終點，也是質變的一步——但別忘了 Ch 1 的話：**真正讓你變強的是「每天讀對的東西」，不是拆句技巧本身。** 拆句是為了讓你讀得動更難的材料、進而讀更多。接下來 Part 3 就要回答那個最實際的問題：該讀什麼、怎麼讀，才讓閱讀量真正累積成能力。

→ [下一章：兩種閱讀——extensive vs intensive](./11-extensive-vs-intensive.md)
