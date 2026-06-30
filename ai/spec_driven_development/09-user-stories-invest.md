# Ch 9 — User Story 與 INVEST

> **目標**：理解 Connextra 的 As a/I want/so that 模板、Ron Jeffries 的 3C（Card/Conversation/Confirmation）、以及 Bill Wake 2003 年提出的 INVEST 六點品質檢核，知道這些工具在哪裡有效、在哪裡不夠用，以及為什麼 AI 代理時代讓它們重新被重視。

---

## 在 User Story 之前，人們怎麼做

1990 年代的需求工程標準做法是寫「系統需求規格書」（Software Requirements Specification，SRS）。一份 SRS 動輒幾十到幾百頁，用「系統應……」（the system shall）的格式羅列每條功能，發行時間點通常在設計開始之前，不容更動。

問題在哪裡？在上一章 [Ch 8 為什麼需求這麼難：自然語言的八種病](./08-why-requirements-hard.md) 我們看到，自然語言天生模糊；而 SRS 的頁數越長，模糊累積得越快，誰也不保證每條「應該」的解讀一致。更致命的是：到系統交付時，客戶早就改變了想法。

1996–1999 年，Kent Beck 在 Chrysler 做 Extreme Programming（XP）時提出了一個激進的主張：**不要在開始之前試圖寫清楚所有需求。改為寫一張「能引發對話的卡片」，在對話裡把細節說清楚，用測試確認理解一致。** 這就是 User Story 的胚胎。

---

## 心智模型：故事是需求的佔位符，不是需求本身

```
傳統 SRS 思維：
  需求文件 ──────────────────────────→ 實作
  (要求詳盡、凍結在某個時間點)

User Story 思維：
  卡片 (佔位符) → 對話 (詳細說明) → 確認 (測試)
       ↑                                    ↓
       └──────── 需求在這個循環裡成形 ────────┘
```

故事本身不完整是刻意的。那張 3x5 英寸的索引卡根本寫不了多少字——這是設計上的限制，強迫你去對話，而不是以為一次寫清楚就夠了。

---

## Connextra 模板：As a / I want / so that

大約 2001 年，倫敦的 Connextra 團隊（Rachel Davies 等人）在實踐 XP 時，發展出這個寫法：

```
As a <role>,
I want <feature>,
so that <benefit>.
```

中文版：

```
身為一個 <角色>，
我想要 <功能>，
以便 <業務價值/動機>。
```

**具體例子：電商結帳流程**

```
身為一位已登入的顧客，
我想要在結帳頁面看到我的訂單摘要，
以便在付款前確認品項與金額正確。
```

三個欄位各有其用意：

| 欄位 | 問的問題 | 常見失誤 |
|------|----------|----------|
| role | **誰在乎？** | 寫成「使用者」——太廣，讓任何人都以為自己是受眾 |
| feature | **要做什麼？** | 混入實作細節（例：「使用 React 渲染...」） |
| benefit | **為什麼要做？** | 省略——沒有 benefit 就無法判斷這個故事是否值得做 |

`so that` 那一行是 PM 最常省略的。省略之後，開發者看到的是一張沒有脈絡的工作單，無從決定邊界與取捨。

---

## Ron Jeffries 的 3C：Card / Conversation / Confirmation

Ron Jeffries（XP 三創始人之一）把 User Story 的本質壓縮成三個 C：

**Card（卡片）**
物理上的索引卡（或數位工具裡的票券），代表「這個故事存在」。它的用途是在計畫會議裡可以被排序、丟棄、合併、拆分。卡片的內容是**意圖**的佔位符，不是完整規格。

**Conversation（對話）**
卡片無法表達的細節，靠人與人的對話補足。這場對話的參與者是業務方（Product Owner）、開發者、測試員——三角驗證，確保同一張卡對每個人的意義相同。對話發生在迭代計畫、釐清會議、站會，甚至在白板前五分鐘的問答。

**Confirmation（確認）**
驗收條件（acceptance criteria）是「對話結論」的書面化。它把口頭協議轉成可測試的陳述：「在什麼情境下，什麼行為算是正確的？」沒有 Confirmation，故事沒有邊界，「完成」沒有定義。

> 如果你對驗收條件的結構化寫法還不熟，下一章 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md) 會深入處理。

3C 的核心洞見是：**需求知識不住在文件裡，住在人的大腦裡；文件只是讓對話發生的觸發器。** 這與 SRS 的假設截然相反。

---

## Bill Wake 的 INVEST（2003）

知道怎麼寫 User Story 之後，下一個問題是：**怎麼判斷寫出來的故事是好故事？**

Bill Wake 在 2003 年 8 月 17 日的文章〈INVEST in Good Stories, and SMART Tasks〉裡提出了六個標準，縮寫為 INVEST：

### Independent（獨立）

故事與故事之間不應有強耦合，每張卡片要能獨立排序與實作。若故事 A 的完成必須先等故事 B，你就無法靈活調整迭代優先序。

**反例：**
```
故事 1：身為顧客，我想要「用信用卡付款」
故事 2：身為顧客，我想要「看到付款結果」  ← 依賴故事 1
```

**修法：** 把兩者合併成一個端對端故事：「身為顧客，我想要完成信用卡結帳並看到確認頁」，或者確認故事 2 可以先用假資料實作。

### Negotiable（可協商）

卡片是**邀請對話**，不是合約條款。在開發開始之前，細節都應該可以修改——不同的實作方式可以達成同樣的業務價值。固執地堅守卡片原文是 User Story 最大的反模式之一。

**例：** 「身為顧客，我想要搜尋商品」這張卡，搜尋是用全文索引還是前綴匹配、要不要模糊搜尋、要不要搜尋歷史，這些在對話前都是可協商的。

### Valuable（有價值）

故事的價值必須對**業務方或使用者**可見，不能是純技術性的。

**反例（不好的故事）：**
```
身為開發者，我想要把資料庫從 MySQL 遷移到 PostgreSQL
so that 程式碼比較好維護。
```

這是一個技術任務，不是 User Story。如果你真的要做這件事，把它寫成業務可見的後果：「系統在高並發時保持 99.9% 可用」，然後把資料庫遷移列為實作任務。

**垂直切片（vertical slice）** 是讓故事有價值的關鍵手法——每個故事都穿透所有層次（UI → 後端 → 資料庫），讓使用者感覺到完整的功能，而不只是某一層的地基。

```
水平切片（危險）：      垂直切片（正確）：
  UI                     故事 A: 登入
  ──────── ← 第一個故事  ├─ UI 部分
  API                    ├─ API 部分
  ──────── ← 第二個故事  └─ DB 部分
  DB
```

### Estimable（可估算）

團隊必須能夠合理估算這個故事的大小，才能做迭代計畫。無法估算通常出於三個原因：

1. **知識不足**：需要做一個 spike（技術探索任務）先弄清楚
2. **太大**：需要拆分
3. **太模糊**：需要更多 Conversation

估算不要求精確，但要能給出相對尺度（例如：故事點數 1/2/3/5）。

### Small（夠小）

一個故事應該小到可以在一個迭代（通常 1–2 週）內完成，Bill Wake 的原文說「at most a few person-weeks」。太大的故事無法在迭代內交付，也無法得到快速回饋。

常見的拆分手法：

```
大故事：「身為顧客，我想要完整的商品管理功能」

拆成→

故事 1：新增商品（帶必填欄位）
故事 2：編輯商品基本資訊
故事 3：上傳商品圖片
故事 4：下架（軟刪除）商品
```

拆分原則：**沿業務功能拆，不沿技術層拆。**

### Testable（可測試）

每個故事都必須帶著隱含承諾：「我理解我要的東西清楚到可以為它寫一個測試。」沒有測試條件的故事，表示需求還不清楚。

Wake 的原話很直接：「If you can't write a test, you don't know what you want.」

這一點直接銜接到 Jeffries 3C 的 Confirmation——可測試性是確認對話有沒有真正完成的關鍵指標。

---

## INVEST 六個字母速查表

| 字母 | 含義 | 違反時的症狀 | 修法 |
|------|------|------------|------|
| I | Independent | 「必須先做 A 才能做 B」 | 重新切片或合併 |
| N | Negotiable | 「客戶說要照這個字面做」 | 提醒卡片是對話起點 |
| V | Valuable | 「這是技術重構/換 DB」 | 重新表達業務後果，或列為 spike/task |
| E | Estimable | 「我不知道這有多大」 | 先做 spike，或繼續拆 |
| S | Small | 迭代結束故事還沒完成 | 拆分，沿功能邊界切 |
| T | Testable | 「這要怎麼測？」 | 補寫驗收條件，繼續 Conversation |

---

## 從概念到實作：一個完整的電商例子

**初始故事（常見的不良寫法）：**

```
身為使用者，我想要購物車功能。
```

這張卡 INVEST 全數不合格：角色不明（哪種使用者？）、功能太大（購物車包含多少行為？）、benefit 全無、無法估算、無法測試。

**第一輪改寫——拆成子故事：**

```
S1: 身為已登入的顧客，
    我想要把商品加入購物車，
    以便稍後一次結帳。

S2: 身為已登入的顧客，
    我想要查看購物車內的所有品項與小計，
    以便確認我要購買的東西。

S3: 身為已登入的顧客，
    我想要從購物車移除特定品項，
    以便更正誤選的商品。
```

**S1 的驗收條件（Confirmation 範例）：**

```
情境 1（正常流程）：
  前提：顧客已登入，並且商品庫存 > 0
  操作：點擊「加入購物車」
  預期：購物車圖示數字 +1，並顯示成功訊息

情境 2（邊界情況）：
  前提：顧客已登入，商品庫存 = 0
  操作：嘗試加入購物車
  預期：按鈕呈現停用狀態，顯示「目前無庫存」

情境 3（未登入）：
  前提：訪客未登入
  操作：點擊「加入購物車」
  預期：導向登入頁，登入後回到原商品頁
```

每個情境都是可以自動化測試的——這就是 Testable 的具體體現。

---

## 為什麼 INVEST 在 AI 代理時代重新重要

> 如果你還沒看 [Ch 1 為什麼「規格」突然重要了](./01-why-specs-matter-now.md)，先回去看一下背景。

傳統上，User Story 的模糊性靠**人的對話**來消解——開發者可以轉身問 PM，PM 可以打電話給客戶。AI 代理做不到這一點：它不會主動停下來問問題，而是靜默地填補空白，填錯了你也不知道。

這意味著：

- **Testable** 不再只是品質訊號，它是 AI 代理的執行前提。驗收條件越具體，代理偏離的空間越小。
- **Small** 變得更急迫。一個故事越大，代理一次猜錯的範圍就越大，修正成本也越高。
- **Negotiable** 的那一面暫時讓位給「對 AI 的精確描述」——卡片的文字必須足夠清楚，不能只靠後續對話補充（因為代理不會發起那個對話）。

這不代表 User Story 過時了，而是說：在 AI 協作流程中，你必須更認真對待 Confirmation，把它寫得接近下一章會講的 Given-When-Then 格式，才能交給代理執行。

---

## 歷史比較：User Story 與 Use Case

有人問：既然 Ivar Jacobson 1986 年就提出了 Use Case（使用案例），User Story 還有什麼必要？

| 維度 | User Story | Use Case（Cockburn 完整版） |
|------|------------|----------------------------|
| 長度 | 1–3 行 | 數頁，含主成功情節、擴展情節、前置條件 |
| 用途 | 引發對話的佔位符 | 完整的互動契約 |
| 細節 | 刻意不完整 | 力求完整 |
| 更新頻率 | 隨迭代持續演化 | 較不適合頻繁異動 |
| 可執行性 | 靠驗收條件（外部） | 主成功情節可直接驅動測試 |
| AI 代理適配 | 需補充 Given-When-Then | 結構較完整，但格式較繁瑣 |

兩者不是競爭關係。User Story 適合做需求池管理與迭代排序；Use Case 適合記錄關鍵業務流程的完整細節。大型系統常見的做法是：用 Story 管理工作流，用 Use Case 記錄複雜業務規則。Ch 12 會深入 Use Case 的格式。

---

## 踩雷集錦

**雷 1：把「as a user」當預設角色**

錯誤直覺：「使用者」已經夠具體了，大家都知道是誰在用。

正確認識：「使用者」是最沒有資訊含量的角色描述。不同角色對同一功能的期待截然不同——「顧客」想看折扣價，「店長」想看成本價，「倉管」想看庫存量。角色錯了，benefit 就寫不清楚，故事就失去焦點。

---

**雷 2：以為故事寫完就等於需求釐清完**

錯誤直覺：卡片進了 backlog，需求就「固化」了，可以直接丟給工程師做。

正確認識：卡片只是 Card，沒有 Conversation 就沒有 Confirmation。把卡片當成規格書的替代品，只會讓誤解更晚被發現（通常在 review 時）。3C 必須一起運作。

---

**雷 3：拆太細或拆太粗都算失敗**

錯誤直覺：把所有故事拆成一個 sprint 能完成的大小就好，方向對就行。

正確認識：拆太粗（史詩級故事）讓迭代無法交付可測試的增量；拆太細（比如「修改資料庫 schema」「新增 API endpoint」）讓故事失去業務可見的價值，變成任務清單。正確的粒度是「使用者能感受到的最小完整功能」——垂直切片，而非水平切片。

---

**雷 4：忽略 Negotiable，堅持字面執行**

錯誤直覺：客戶說要做 A，我們就做 A，白紙黑字不能改。

正確認識：INVEST 的 N 明確說故事不是合約。在 Conversation 期間，開發者可以提出不同的實作方式——只要達成相同的業務價值，所有選項都值得討論。這不是對客戶不尊重，而是讓對話產生最大價值的設計。

---

**雷 5：以為可以省略 so that**

錯誤直覺：benefit 是廢話，大家都知道「為什麼要做這個」，寫了也沒人看。

正確認識：`so that` 是最重要的那行。它決定了故事的業務價值判斷標準，也是 PM 在優先排序時的依據。拿掉它，你無從判斷這個功能是否值得做、值得做多細。更關鍵的是：AI 代理在生成實作決策時，沒有 benefit 就無從判斷「邊界在哪裡」。

---

## 進階延伸

### 故事映射（Story Mapping）

Jeff Patton 在《User Story Mapping》（2014）提出故事地圖：把所有故事排列在一個二維空間，X 軸是使用者活動的時間序列，Y 軸是優先序。這讓團隊可以看到「哪些故事構成一個可交付的薄片（walking skeleton）」，避免把所有低優先故事推到第一個 release。

### DEEP Backlog

Mike Cohn 提出的 Backlog 品質標準：Detailed appropriately（越近的故事越細）、Estimated（估過算）、Emergent（隨時可更新）、Prioritized（有優先序）。INVEST 是評估單一故事，DEEP 是評估整個 backlog 健康度。

### 從 INVEST 到形式化

當故事遷移到 AI 代理執行時，Testable 那一條催生了一條路徑：

```
User Story (Connextra)
  → 驗收條件 (bullet list)
  → Given-When-Then (Dan North BDD)
  → Gherkin .feature 文件（可執行）
  → EARS 句型（更正式）
  → 形式化規格（TLA+ / Alloy）
```

這個梯子就是 Part 2 接下來幾章要爬的路。

---

## 動手練習

以下是一個故事，對照 INVEST 六個維度分析它的問題，然後重寫成至少兩個符合 INVEST 的故事，每個故事附上至少兩條驗收條件：

**原始故事：**
```
身為管理員，我想要一個完整的用戶管理系統，
以便控制系統存取。
```

**提示：**
- 「完整的用戶管理系統」是一個 Epic，不是 Story
- 「控制系統存取」能拆成哪些具體可見的行為？
- 想想：新增用戶、停用用戶、修改角色、重設密碼……每個都是獨立的故事
- 每個故事的驗收條件要包含至少一個邊界情況（例：嘗試新增已存在的 email）

寫完之後，把自己的故事對照 INVEST 打分：I / N / V / E / S / T 各幾分（0–2）？哪幾個字母最難達到？

---

## 本章重點整理

- **User Story 的起源**：Kent Beck XP（1996-1999）→ Connextra 模板（約 2001）→ Jeffries 3C → Mike Cohn 《User Stories Applied》（2004）推廣。
- **Connextra 模板**：`As a <role>, I want <feature>, so that <benefit>`——三個欄位缺一不可，`so that` 最常被省略且最重要。
- **3C**：Card 是佔位符，Conversation 是細節來源，Confirmation 是可測試的驗收條件。需求知識住在人的對腦裡，文件是觸發對話的工具。
- **INVEST**：Bill Wake 2003 年的六點品質標準。Independent（可獨立排序）、Negotiable（非合約）、Valuable（業務可見）、Estimable（可估算）、Small（一個迭代內完成）、Testable（有驗收條件）。
- **垂直切片原則**：沿功能邊界拆，不沿技術層拆，確保每個故事都能獨立交付使用者可感受的價值。
- **AI 代理時代的影響**：代理無法主動發起對話，所以 Testable 和 Small 的重要性放大；Confirmation 必須寫得足夠具體，接近結構化的 Given-When-Then。

---

## 自我檢核

- [ ] 不看筆記，用你自己的話解釋 3C 裡的「Card」為什麼刻意設計成容量很小的索引卡。
- [ ] 你能說出 `so that` 那一行省略了會發生什麼問題嗎？對人和對 AI 代理，影響有何不同？
- [ ] 面試被問「什麼是 INVEST」，你能不照順序、用自己的話把六個維度和它們的違反症狀說出來嗎？
- [ ] 「垂直切片」和「水平切片」有什麼差？哪個讓故事保持 Valuable？
- [ ] INVEST 的 N（Negotiable）和直覺中「需求不能隨便改」有衝突嗎？怎麼解釋這個張力？
- [ ] Connextra 模板和 Use Case 各適合在什麼場合用？能給一個你自己領域的例子嗎？

---

## 延伸閱讀

**Bill Wake，《INVEST in Good Stories, and SMART Tasks》（2003）**
https://xp123.com/invest-in-good-stories-and-smart-tasks/
這是 INVEST 的第一手來源，Wake 本人在 xp123.com 上的原文。整篇不長，每個字母都有說明，以及配套的 SMART（適用於任務而非故事）。和本章的關聯：這就是 INVEST 的定義，任何其他解釋都是二手資料。

**Cucumber 官方文件，《History of BDD》**
https://cucumber.io/docs/bdd/history/
說明了從 Kent Beck XP → Connextra 使用者故事格式 → Dan North BDD → Gherkin 的完整傳承。和本章的關聯：了解 User Story 如何演化成 Given-When-Then，是連接 Ch 9 和 Ch 10 最清楚的一條線。

**Dan North，《Introducing BDD》（2006，Better Software magazine）**
https://dannorth.net/blog/introducing-bdd/
BDD 的起點。North 解釋了為什麼他把「測試」改成「行為」、以及「As a / I want / so that」的接收條件模板怎麼來。和本章的關聯：直接示範了 Confirmation 如何從 bullet list 轉成結構化的 Given-When-Then。

**Liu Shangqi（Thoughtworks），《Spec-driven development: Unpacking one of 2025's key new AI-assisted engineering practices》（2025 年 12 月）**
https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices
討論了結構化輸入如何降低 LLM 幻覺，以及為什麼 2025 年的 AI 代理工具（Kiro、GitHub Spec Kit）重新強調需求格式的重要性。和本章的關聯：回答了「INVEST 跟 AI 有什麼關係」這個問題，是從傳統敏捷到 SDD 的橋梁。

**Mike Cohn，《User Stories Applied》（2004，Addison-Wesley）**
這本書讓 Connextra 模板和 INVEST 從 XP 社群傳播到主流敏捷。第 2 章（What is a User Story?）和第 6 章（Splitting User Stories）特別實用。和本章的關聯：如果你覺得本章的內容太濃縮，這是最好的展開讀物，有大量真實案例。

**Jeff Patton，《User Story Mapping》（2014，O'Reilly）**
Story Mapping 是管理大量故事、決定 release 範圍的關鍵技術。和本章的關聯：INVEST 教你評估單一故事，Story Mapping 教你把所有故事組織成有意義的交付計畫——一體兩面。

---

寫好 User Story 只是第一步：它告訴我們「要做什麼」，但「怎麼確認做對了」需要更精確的語言。下一章我們把 Confirmation 從 bullet list 升級到結構化的 Given-When-Then，看看 Dan North 的 BDD 如何讓驗收條件變成可執行的規格。

→ [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)
