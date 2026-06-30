# Ch 1 — 為什麼「規格」突然重要了：AI 把瓶頸推到意圖上

> **目標**：理解為什麼 LLM 讓實作變便宜之後，規格（specification）從過去被壓縮掉的成本中心，變成決定一切的槓桿點——並且用具體的成本結構、歷史教訓與反例來支撐這個判斷。

---

## 先給一個心智圖像：瓶頸在哪裡？

想像你雇了一個動作極快的工人，可以在一秒鐘砌好一塊磚。

問題來了：如果藍圖錯了，他砌得越快，你就輸得越快。

LLM 就是那個工人。它可以在幾分鐘內產出幾千行可以跑的程式碼。但它只能忠實地把你告訴它的事情實作出來——而如果你告訴它的事情含糊、矛盾、或根本錯誤，它只會把那個錯誤具象化成一個能跑的 bug。

```
傳統世界的瓶頸                AI 世界的瓶頸
─────────────────────        ─────────────────────
[意圖/規格] ← 便宜            [意圖/規格] ← 瓶頸
    │                              │
[設計]                        [設計]
    │                              │
[實作]      ← 貴                [實作]      ← 便宜（LLM）
    │                              │
[測試/驗證] ← 貴                [測試/驗證] ← 貴（仍然）
```

成本結構改變了，槓桿點也隨之移動。這不是哲學主張，是工程經濟學。

---

## 在這之前：人們怎麼對待規格？

### 瀑布的誤會（1970—2001）

Winston Royce 在 1970 年那篇被後人引用為「瀑布模型源頭」的論文，其實明確寫道：他描述的那種從需求直線走到完工的流程是「risky and invites failure（有風險且招致失敗）」，他建議至少跑兩遍才保險。但業界讀到的是那張可以向下流的圖，沒讀到那句警告。「瀑布」這個詞本身也不是 Royce 發明的，而是 Bell 和 Thayer 在 1976 年的論文裡才出現的。

> **更正注意**：坊間常說 Royce 發明了瀑布模型並奉行之。這是史實錯誤，詳見 [Ch 4 瀑布的真相：Royce 1970 與一個誤會](./04-waterfall-myth.md)。

結果：企業確實砸了大量資源在前期規格上，但那些規格通常拖很久、格式繁重、而且在實作結束時已經和現實脫鉤。規格是成本，不是資產。

### 敏捷的反動（2001—2022）

2001 年，十七位工程師在猶他州簽署了《敏捷宣言（Agile Manifesto）》。其中一個價值觀直接回應了「規格文件地獄」：

> 「Working software over comprehensive documentation。」（可運行的軟體勝過詳盡的文件。）

宣言的作者們非常小心地加了一句：「While there is value in the items on the right, we value the items on the left more.」（右邊的項目仍有價值，但我們更重視左邊的。）

但業界再度只讀了一半。很多團隊把「敏捷」解讀成「不用寫文件了」、「需求會在迭代中浮現」。規格從重要到變成「壞味道」。

對工程師來說，這在當時是合理的反應。寫了一百頁 Word 文件，客戶讀了三頁，剩下的在第一個 sprint 就過時——那這一百頁是在幫誰？

問題是，那個判斷是在「實作很貴」的前提下做出的。把規格砍掉換成迭代，是因為：快速交付可跑的程式碼，然後讓客戶的回饋指引下一步，比試圖在前期說清楚所有需求便宜得多。

---

## 成本結構改變了什麼？

Brooks 在《No Silver Bullet》（1986）裡把軟體的難度拆成兩種：

- **本質難度（Essence）**：軟體固有的難——規格、設計、概念結構的複雜性。
- **偶然難度（Accident）**：當前生產工具帶來的難——語言、IDE、部署環境。

Brooks 的洞見是：過去幾十年的生產力工具（高階語言、IDE、版本控制）全部攻克的是偶然難度。本質難度——決定要做什麼——從來沒有被任何工具消除過。

> 「The hardest single part of building a software system is deciding precisely what to build. No other part of the work so cripples the resulting system if done wrong. No other part is more difficult to rectify later.」
> — Fred Brooks，〈No Silver Bullet〉，1986

現在 LLM 來了。它攻克的恰恰是偶然難度的主體：**寫程式碼**。一個能力尚可的工程師配上 GitHub Copilot 或 Claude Code，每天產出的程式碼量已經和過去一人一週的產出不相上下。在某些場景，從零到能跑的原型，幾個小時就能做出來。

當實作成本趨近於零，公式的其他項目就放大了：

```
總交付成本 ≈ 規格成本 + 設計成本 + 實作成本 + 驗證成本 + 維護成本

以前：規格+設計 佔 30%，實作 佔 50%，其餘 20%
現在：規格+設計 佔 60%+，實作 趨近於 0%，驗證+維護 放大
```

（上面的比例是概念性的，不是引用自任何特定研究。具體數字因專案型態差異極大。）

這個重新分配不只是比例問題，而是**槓桿點移動**：現在你花一小時把規格寫得更清晰，省下的不是一個工程師一小時，而是可能防止一個 LLM 兩天後在錯誤方向上生成出幾千行程式碼。

---

## Sean Grove 的「The New Code」（2025）

2025 年 6 月，OpenAI 的 Sean Grove 在 AI Engineer World's Fair 上發表了一場演講，是對這個成本結構轉變最清晰的論述。

他提出一個類比，出自社群整理的逐字稿（非官方 OpenAI 文字稿）：

> 「This feels like you shred the source and then you very carefully version control the binary.」

他說的是目前大多數人的工作流程：先有一個想法或 prompt，讓 LLM 生成程式碼，然後把 prompt 刪掉、把程式碼存進 git。這等同於把原始碼銷毀，只留二進位。

然後他給出了數字：

> 「Code is sort of 10 to 20% of the value... The other 80 to 90% is in structured communication.」

而且點出了槓桿在哪裡：

> 「The person who communicates most effectively is the most valuable programmer.」

他引用 OpenAI 的 Model Spec——一份存在 GitHub 上、任何人都可以提 PR 的 Markdown 文件——作為「規格作為單一真相來源（single source of truth）」的具體案例。當模型行為偏離 Model Spec，OpenAI 把它視為 bug，而不是「模型就是這樣」。

他用的另一個核心說法：

> 「Code is a lossy projection from the specification.」（程式碼是規格的有損投影。）

你沒辦法從二進位還原出設計意圖。你沒辦法從程式碼裡讀出「我們當初為什麼選擇這個結構」。意圖在實作過程中被壓縮、被遺失。如果你把意圖當做一等公民版本控制，就能保留這層語意。

> **引用說明**：Grove 演講的逐字稿來自社群整理（lawwu.github.io），而非 OpenAI 官方文字稿，引言措辭應以官方影片（YouTube id: 8rABwKRsec4）為準，查證日期 2026-06-30。

---

## 「lossy projection」是什麼意思？一個具體例子

考慮以下這段 Python 函式：

```python
def calculate_price(base_price, user_tier):
    if user_tier == "premium":
        return base_price * 0.8
    elif user_tier == "corporate":
        return base_price * 0.7
    return base_price
```

從這段程式碼你能回答以下問題嗎？

- 為什麼 premium 折扣是 20%，而不是 15% 或 25%？
- 如果同一個使用者同時是 premium 又有企業合約，應該怎麼算？
- 這個折扣邏輯是否適用於所有產品，或者只有特定類別？
- 未來計劃中還有哪些 tier？

答案：**不能**。這些決策曾經存在於某個會議室裡、某份 email 往來裡，或者某個 PM 的腦袋裡，但它們在變成程式碼的過程中消失了。

現在假設你有一份規格：

```markdown
## 定價折扣規則

### 背景
折扣率由商業策略部門在 2024-Q3 定案，每年 Q4 重新審查。

### 優先級
當多個條件同時成立時，取最高折扣，不疊加。

### 折扣表
| Tier       | 折扣率 | 生效條件                          |
|------------|--------|-----------------------------------|
| premium    | 20%    | 用戶已訂閱 Premium 方案           |
| corporate  | 30%    | 公司帳號且年度合約金額 > $10,000  |

### 排除
訂閱期間的一次性加購項目不適用折扣。
```

這份規格是可以修改、可以審查、可以追溯的。程式碼是它的有損投影——是表達，不是真相。

---

## 歷史上的嘗試：為什麼沒有更早成功？

「把規格作為主要產物」不是 2025 年才有的想法。1980 年代 Knuth 的**文學編程（Literate Programming）**就讓程式碼與說明共存——只不過，意圖解釋的仍是人類手寫的程式碼，而不是生成的。

2000 年代 OMG（Object Management Group）的**模型驅動架構（Model-Driven Architecture，MDA）**則更激進：用 UML 圖生成程式碼。它為什麼失敗？原因有幾個：

1. **模型比程式碼更難寫**：正確的 UML 類圖要求的形式化程度，比寫 Java 還費力。
2. **產生器脆弱**：輸入模型稍有變化，產生的程式碼就面目全非，難以維護。
3. **工具不成熟**：沒有一個成熟的生態系統可以「生成」任意複雜的邏輯。

SDD 的賭注是：LLM 解決了 MDA 的第三個問題，也大幅緩解了第二個。Markdown 規格比 UML 圖人性化，生成品質好得多。第一個問題部分殘存——寫一份好規格仍然需要功夫——但不再是進入障礙。

> **注意**：SDD 和 MDA 是否「真的不同」，在學術和實踐社群都有爭議。Birgitta Böckeler（Thoughtworks）在 2025 年 10 月於 martinfowler.com 上提出，SDD 可能結合了「MDD 的不靈活性和 LLM 的非確定性」兩個缺點。我們會在 [Ch 26 懷疑論者的最強論證](./26-skeptics-case.md) 詳細處理這個問題。

---

## 工具的出現：結晶點

「規格驅動開發（Spec-Driven Development，SDD）」這個詞不是任何一個人發明的。它在 2025 年從工具和社群中有機浮現。

幾個標誌性事件：

| 時間 | 事件 |
|------|------|
| 2024-11-14 | Tessl（Guy Podjarny，Snyk 創辦人）Series A 發佈，提出「spec-centric, as opposed to code-centric」 |
| 2025-06-03 | Sean Grove 在 AI Engineer World's Fair 發表〈The New Code〉 |
| 2025-07-14 | AWS 推出 Kiro，三份規格檔（requirements.md / design.md / tasks.md）驅動整個開發 |
| 2025-09-02 | GitHub 開源 Spec Kit（約 116,000 星，截至 2026-06-30，version-dependent），以 `/speckit.*` 系列指令定義工作流 |

（星數、定價等工具細節為 version-dependent，以各工具官方最新資訊為準，查證日期 2026-06-30。）

這些工具的共同命題：**規格是主產物，程式碼是派生物**。

GitHub Spec Kit 的 `spec-driven.md` 寫得很直白（原文）：

> 「Specifications don't serve code—code serves specifications... The specification becomes the primary artifact. Code becomes its expression in a particular language and framework.」

Kiro 官方部落格把自己定位成「vibe coding」的對立面——它不是讓你隨意描述然後祈禱，而是讓你先把需求寫清楚，再讓 AI 實作。

---

## 比較：兩個時代的成本思維

| 面向 | AI 之前 | AI 之後 |
|------|---------|---------|
| 實作成本 | 高（工程師時間） | 低（LLM 生成） |
| 規格品質的報酬率 | 中等（人類工程師可以問問題） | 極高（LLM 只能靠規格） |
| 規格遺漏的代價 | 中等（人類工程師可以猜） | 高（LLM 會自信地猜錯） |
| 迭代的成本 | 高（重寫很貴） | 低（重生成便宜） |
| 主要瓶頸 | 實作 | 意圖/規格 |

這張表也解釋了為什麼「vibe coding」（Karpathy 2025 年初提出的詞）在探索和原型階段是合理的——當你真的不知道要做什麼，先跑起來看看確實是有效的學習策略。問題是，當你試圖在一個團隊、一個有持續性的產品裡用同樣的方式工作，規格的缺失就會開始累積成難以追溯的債務。

---

## 踩雷集錦

### 雷 1：「我用 prompt 說清楚了，不需要規格」

**錯誤直覺**：只要 prompt 夠詳細，LLM 就會做對。

**正確認識**：Prompt 是揮發性的一次性輸入。你下完 prompt、拿到程式碼之後，prompt 就消失了。三週後當你（或另一個工程師）需要修改這段程式碼，沒有人知道當初的決策脈絡是什麼。規格是持久性的文件，可以被版本控制、審查、引用。

---

### 雷 2：「寫規格等於回到瀑布，我們要敏捷」

**錯誤直覺**：寫前期規格就是老方法，和敏捷原則衝突。

**正確認識**：敏捷宣言反對的是那種「鎖死六個月不能改」的重量級規格，不是反對把意圖寫清楚。現代 SDD 工具產生的規格是 Markdown 檔案，可以 PR、可以改版，迭代速度不亞於程式碼。真正的對立不是「有規格 vs 沒規格」，而是「僵化規格 vs 活文件（living documentation）」。

---

### 雷 3：「AI 很聰明，它會自己判斷」

**錯誤直覺**：LLM 理解力很強，應該能從模糊描述推出正確實作。

**正確認識**：LLM 在規格模糊時，會用最高機率的預測填補空白——而那個預測來自訓練資料的分佈，不是你的業務脈絡。它的「猜」往往是技術上可行但業務上錯誤的。Addy Osmani（Google）在 2026 年 1 月的文章中指出，隨著你堆疊的規格越多，模型遵守每一條的能力會下降。這表示規格品質比規格數量更重要。

> **細節注意**：Osmani 在同一篇文章中使用了 Simon Willison 的「lethal trifecta」一詞，但語境有所轉移。Willison 原本的意思是 prompt injection 的安全風險（私有資料存取 + 不可信內容暴露 + 外洩能力三者共存），不是 Osmani 用來描述的速度/非確定性/成本問題。引用 Willison 的「lethal trifecta」時要注意語境。查證日期 2026-06-30。

---

### 雷 4：「規格一寫好就不用動了」

**錯誤直覺**：規格是靜態文件，寫完就可以移交給 AI 去跑。

**正確認識**：「規格漂移（spec drift）」和「規格腐化（spec rot）」是 SDD 最真實的失敗模式。當程式碼和規格各自進化而不同步，你就有兩份不一致的真相——這比沒有規格更危險，因為你可能不知道哪一份才是對的。我們會在 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md) 深入處理這個問題。

---

### 雷 5：「SDD = BDD，或 SDD = TDD」

**錯誤直覺**：規格驅動開發只是測試驅動開發（Test-Driven Development，TDD）或行為驅動開發（Behaviour-Driven Development，BDD）換個名字。

**正確認識**：TDD 的規格是測試案例，BDD 的規格是 Given-When-Then 情境——兩者都是在「測試層」操作，目的是驅動程式碼設計。AI 時代的 SDD 是在更上層操作：用自然語言描述意圖和需求，讓 AI 同時生成實作和測試。兩者並不互斥，但層次不同。[Ch 2 先把三個詞分清楚：SDD vs DDD vs BDD/TDD](./02-sdd-ddd-bdd-tdd-map.md) 會詳細畫出這張地圖。

---

## 進階延伸

**Karpathy 的弧線**：如果你想了解「English 是程式語言」的說法從何而來，可以先看 Karpathy 2017 年的 Software 2.0 文章，再看他 2023 年 1 月 24 日的那條推文（「The hottest new programming language is English」，Quote Investigator 於 2024-10-20 確認，x.com/karpathy/status/1617979122625712128），以及他 2025 年 6 月在 Y Combinator AI Startup School 的 Software 3.0 演講（Latent Space 授權整理：latent.space/p/s3）。這條弧線在 [Ch 23 從 Software 2.0 到 Software 3.0：Karpathy 的弧線](./23-software-2-to-3.md) 會完整展開。

**Brooks 的本質/偶然難度**：〈No Silver Bullet〉（1986）是理解「為什麼 AI 讓規格更重要」的理論基礎。讀它的「Essence」一節，那段關於「決定要做什麼是最難的部分」的論述，是本章所有論證的地基。

---

## 動手練習

拿一個你最近做過的功能（或想像一個電商的「加入購物車」功能），用以下兩種方式各寫一遍，然後比較：

**版本 A（prompt 風格）**：
```
幫我實作加入購物車的功能，需要檢查庫存，並且在庫存不足時給用戶看錯誤訊息。
```

**版本 B（規格風格，嘗試回答以下問題）**：
- 「庫存不足」的判定是 0 件，還是比某個閾值少？
- 這個判定是在前端做、還是後端做、還是兩層都做？為什麼？
- 如果同一個用戶在不同分頁同時按下「加入購物車」，系統應該怎麼處理？
- 「加入購物車」是即時扣減庫存，還是在結帳時才扣？業務上的理由是什麼？

你能回答第二組問題嗎？無法回答的部分，就是你的「意圖空洞（intent gap）」——也是 LLM 最容易猜錯的地方。

---

## 本章重點整理

1. **成本結構改變了**：LLM 把實作成本壓低，槓桿點移到了規格和意圖。
2. **Brooks 的本質難度**：決定要做什麼一直是最難的部分，只是現在它佔了更大的比例。
3. **code is a lossy projection**：程式碼是規格的有損投影，保留規格才能保留意圖（Grove，The New Code，2025；引自社群逐字稿）。
4. **SDD 不是瀑布的復活**：它用的是活文件、可版本控制的規格，不是靜態鎖定的前期文件。
5. **SDD 也不是 MDA 的復活**：LLM 的生成能力解決了 MDA 失敗的主要原因，但 SDD 的工具和批評者都還在演化中（version-dependent）。
6. **「vibe coding」和 SDD 各有適用場景**：探索期適合前者，需要持久性和可維護性的產品適合後者。

---

## 自我檢核

- [ ] 用自己的話解釋：為什麼 LLM 讓規格的重要性提高，而不是降低？（想像你要向一個對 AI 半信半疑的同事解釋。）
- [ ] 「code is a lossy projection」是什麼意思？用一個具體例子說明（不要用本章的例子）。
- [ ] 如果面試官問「你們團隊怎麼處理 AI 生成程式碼的意圖保留問題」，你會怎麼回答？
- [ ] SDD 和 BDD 的差異是什麼？為什麼說它們「層次不同」？
- [ ] Boehm 的變更成本曲線告訴我們「越早修越便宜」，但曲線背後的 1:100 數字被質疑是捏造的——這對你使用這個論點有什麼影響？

---

## 延伸閱讀

**The New Code — Sean Grove，OpenAI（AI Engineer World's Fair 2025）**
- 官方影片：https://www.youtube.com/watch?v=8rABwKRsec4
- 社群逐字稿：https://lawwu.github.io/transcripts/8rABwKRsec4.html
- 先看什麼：「shred the source」類比與 OpenAI Model Spec 的案例。本章所有 Grove 引言的原始出處。約 22 分鐘。

**Software 2.0 — Andrej Karpathy（Medium，2017-11-11）**
- https://karpathy.medium.com/software-2-0-a64152b37c35
- 先看什麼：開頭的 Software 1.0 vs 2.0 定義，以及「compiles the dataset into the binary」那段。理解「新型程式」概念的基礎。

**Software in the Age of AI（Software 3.0）— Latent Space（swyx / Alessio），涵蓋 Karpathy 2025-06-17 演講**
- https://www.latent.space/p/s3
- 先看什麼：「3.0 is eating 1.0/2.0」段落，以及「autonomy slider」的討論。這是目前最完整的授權文字整理，因為官方無逐字稿。

**No Silver Bullet — Fred Brooks（1986/1987）**
- https://www.cin.ufpe.br/~phmb/ip/MaterialDeEnsino/BrooksNoSilverBullet.html
- 先看什麼：「Essence」一節，特別是「hardest single part」段落。本章 Brooks 引言的原始出處；理解本質/偶然難度二分法的必讀。

**spec-kit/spec-driven.md（GitHub Spec Kit）**
- https://github.com/github/spec-kit/blob/main/spec-driven.md
- 先看什麼：「Power Inversion」段落與 lingua franca 那段。最清晰的書面 SDD 宣言。指令名稱有 version-dependent 的問題（`/speckit.*` 前綴，查證日期 2026-06-30），閱讀時確認最新版本。

**Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl — Birgitta Böckeler（Thoughtworks，martinfowler.com，2025-10-15）**
- https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html
- 先看什麼：spec-first / spec-anchored / spec-as-source 的三類分法，以及「illusion of control」的批評。是本課程最重要的平衡觀點來源之一。

**Everyone cites that 'bugs are 100x more expensive to fix in production' research — Tim Anderson，The Register，2021-07-22**
- https://www.theregister.com/2021/07/22/bugs_expense_bs/
- 先看什麼：Hillel Wayne 的引言。幫你建立對「變更成本曲線」這個常見說法的正確態度——方向對，數字要存疑。

---

下一章我們要先把課程中反覆出現的三個術語拆開：SDD 到底和 DDD（Domain-Driven Design，領域驅動設計）有什麼關係？和 BDD/TDD 又是什麼關係？用一張地圖把這些詞釘清楚，後面的章節才不會混。

→ [Ch 2 先把三個詞分清楚：SDD vs DDD vs BDD/TDD](./02-sdd-ddd-bdd-tdd-map.md)
