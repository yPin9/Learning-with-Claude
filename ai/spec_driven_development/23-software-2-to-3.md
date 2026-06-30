# Ch 23 — 從 Software 2.0 到 Software 3.0：Karpathy 的弧線

> **目標**：追蹤 Andrej Karpathy 從 2017 年「用神經網路取代手寫邏輯」到 2023 年「英文是最熱的程式語言」再到 2025 年「LLM 是一台可程式化的電腦（OS）」這條思想弧線，理解為何它是整個 AI 原生開發（AI-native software development）浪潮的底層邏輯，以及規格（spec）如何在這個框架下取得它應有的位置。

---

## 心智圖像：三代「程式」

在進入細節之前，先用一張圖把整個演化放進同一個畫面：

```
時間軸 ──────────────────────────────────────────────────────────────────▶
  1950s~2010s         2012~2020s              2017~2025              2025+
       │                   │                      │                    │
  Software 1.0        深度學習爆發          Software 2.0          Software 3.0
  ──────────         ──────────────         ──────────────        ──────────────
  手寫規則           ImageNet、ResNet        Karpathy 2017         Karpathy 2025
  if/else/for        AlexNet 2012            神經網路的「程式」       LLM 作為 OS
  Python/C++         自動特徵學習            dataset→weights         英文→行為
  程式設計師          (不再手設特徵)          training=編譯           prompt=程式
  寫的是「指令」       Data-centric           weights=binary          spec=意圖
```

這不是斷裂，是連續的位移：**每一代都把「意圖」的描述層次往上提了一格。**

---

## Software 1.0：我們熟悉的那個世界

Software 1.0 就是你我從小學到的那套：程式設計師把對世界的理解翻譯成 Python、C++、Java 的指令，電腦逐行執行。

問題在哪？

有些任務天生就很難「翻譯」。你能用 if/else 寫一個「識別照片裡的貓」的程式嗎？理論上能——你可以窮舉所有貓的形狀、顏色、光線——但沒有人真的這樣做。規則爆炸，維護成本指數上升，準確度還是爛的。

2012 年 AlexNet 在 ImageNet 擊敗所有手工特徵的方法，揭示了一個不安的事實：**有些知識不是用規則存的，而是從資料裡「壓縮」出來的。**

---

## Software 2.0（2017）：資料集就是你的程式，training 就是編譯

2017 年 11 月 11 日，Karpathy 在 Medium 發表 [Software 2.0](https://karpathy.medium.com/software-2-0-a64152b37c35)，把這個現象給了一個名字和框架。

原文四個核心論斷（均已對照原文驗證）：

> **Software 1.0** is "explicit instructions to the computer written by a programmer."

> **Software 2.0** is "written in much more abstract, human unfriendly language, such as the weights of a neural network."

> "Neural networks are not just another classifier, they represent the beginning of a fundamental shift in how we develop software."

> "The process of training the neural network compiles the dataset into the binary — the final neural network."

這四句話的衝擊力在於：它不是在說神經網路「好用」，而是在說「神經網路**改變了軟體的本質**」。

用更直白的語言翻譯：

| 概念 | Software 1.0 | Software 2.0 |
|---|---|---|
| 你寫的是 | 指令（code） | 目標+資料（dataset） |
| 「程式」是什麼 | `.py` / `.cpp` 檔 | 模型權重（weights） |
| 「編譯」是什麼 | `gcc` / `javac` | training loop |
| 執行平台 | CPU / 作業系統 | GPU / inference engine |
| 怎麼 debug | 讀邏輯 | 分析失敗案例，加資料 |

### 具體例子：圖像分類

Software 1.0 想識別「車牌數字 8」：你得寫規則——圓形、兩個洞、某個比例範圍……寫一千行，準確度 70%。

Software 2.0：你蒐集十萬張帶標籤的車牌圖片，定義 loss function（「分類錯誤就懲罰」），跑 training，得到一個 50MB 的 `.pt` 檔。那個 `.pt` 檔就是你的「程式」，準確度 99%。

**你沒有寫「如何識別 8」的指令，你只定義了「什麼叫做識別對了」。**

這個位移非常根本：你的工作從「描述解法」變成「描述目標」。

---

## 銜接段：缺的那一層

2017 年論文裡的 Software 2.0 世界，人類仍然需要做幾件繁瑣的事：

- 蒐集並標注大量訓練資料
- 設計模型架構
- 調整 hyperparameter
- 評估模型輸出

最重要的是：要對**每個任務**做一遍。你訓練了一個圖像分類器，它只會圖像分類；你要做翻譯，要從頭再來。

Large Language Model（大型語言模型，LLM）的出現打破了這個限制。GPT-2（2019）、GPT-3（2020）、ChatGPT（2022）的突破是：一個模型，用自然語言描述就能執行各種任務，無需重新訓練。

這讓 Karpathy 在 2023 年說出那句話。

---

## 那條推文（2023）

2023 年 1 月 24 日下午 3:14（依 Quote Investigator 的查證，日期與措辭可信；原推文 URL `x.com/karpathy/status/1617979122625712128` 因平台限制無法直接抓取驗證），Karpathy 在 X 上寫道：

> "The hottest new programming language is English."

七個英文字，把 Software 2.0 的邏輯往前推了一步：

- Software 2.0 說「dataset 是你的程式，weights 是 binary」
- 這句推文說「LLM 已經夠通用，所以你描述任務的語言就是你的程式語言」
- 那個語言，就是英文（或任何自然語言）

注意這不是在說「程式設計師要失業了」，而是在說**「意圖描述」已經成為軟體開發的核心技能**，而意圖描述的媒介是自然語言。

這一句話在 AI 編碼社群裡廣泛流傳，成為整個「英文即程式碼」（English-as-code）論述的標誌性表述。

---

## Software 3.0（2025）：LLM 作為作業系統

2025 年 6 月 17 日，Karpathy 在 Y Combinator AI Startup School 發表演講，把三代架構正式整合成一個框架。以下依 Latent Space 的授權整理文（`latent.space/p/s3`）——這場演講無官方逐字稿，Latent Space 是目前最可信的文字參照：

### 三種「程式設計」並行

```
Software 1.0   ─────── Python/C++ ─────────── 執行在 CPU/OS
Software 2.0   ─────── 資料集+目標 ──────────── 訓練成 weights，執行在 GPU
Software 3.0   ─────── 英文 prompt/spec ──────── 驅動 LLM，LLM 就是 OS
```

Karpathy 的論點：LLM 不只是另一種工具，它**更接近一台電腦（computer/OS）**：

- 它有「記憶體」（context window）
- 它可以「執行程式」（執行你給它的指令）
- 它有「I/O」（讀取輸入、產生輸出）
- 它可以被「程式化」——這個程式語言叫做英文

但這個「作業系統」有個特性和 1.0/2.0 都不一樣：**它的介面是意圖，不是指令序列。**

### Karpathy 的「自主性滑桿」

演講中 Karpathy 提出「autonomy slider」（自主性滑桿）的概念：

```
低自主                                               高自主
   │────────────────────────────────────────────────│
   │  人類主導        │   Iron Man        │   全自動  │
   │  AI 輔助         │   AI+人類協作     │   AI agent│
   │  (autocomplete)  │   (copilot)       │   (agent) │
   └──────────────────┴───────────────────┴───────────┘
```

他偏好「Iron Man suit」型的應用——AI 大幅增強人類的能力，但人類仍保持控制權和判斷力，而非讓 AI 完全自主運行（因為現階段 agent 的可靠度還不夠高）。

這個觀點和 SDD 的哲學高度吻合：規格（spec）就是那個「人類保持控制的點」。AI 根據 spec 生成程式碼，但 spec 本身是人類寫的、版本控制的、可以審查的。

---

## 三代對比表

| 維度 | Software 1.0 | Software 2.0 | Software 3.0 |
|---|---|---|---|
| 人類描述的是 | 解法（how） | 目標+資料（what+examples） | 意圖（intent/spec） |
| 機器做的是 | 執行指令 | 從資料中學習 | 理解意圖並生成行為 |
| 核心產物 | `.py`/`.cpp` | 模型 weights | prompt/spec |
| 知識存在哪裡 | 程式碼 | 模型參數 | prompt + 模型 |
| Debug 方式 | 讀程式邏輯 | 改資料/架構 | 修 prompt/spec |
| 可移植性 | 換語言要重寫 | 換任務要重訓 | 換模型可能直接用 |
| 版本控制對象 | 程式碼（code） | 資料集+checkpoints | spec（本課重點） |

---

## 這條弧線和 SDD 的關係

為什麼我們在「規格驅動開發」的課程裡要花一整章談 Karpathy？

因為他的三個主張合在一起，為 SDD 提供了最清晰的理論基礎：

1. **Software 2.0**：「描述目標比描述解法更有效」——這解釋了為什麼 AI 能從高層級的描述生成程式碼
2. **英文推文**：「意圖表達語言就是程式語言」——這解釋了為什麼自然語言的規格（spec）可以驅動 AI 寫程式
3. **Software 3.0**：「LLM 是 OS，prompt/spec 是程式」——這解釋了為什麼規格應該取代隨手打的 prompt，成為正式的版本控制產物

Sean Grove 在 AI Engineer World's Fair 2025 說的那句話（見 Ch 24）——「把 prompt 扔掉、把 spec 版本控制起來，感覺像是你把原始碼銷毀、然後很仔細地把 binary 版本控制起來」——其實就是在把 Karpathy 的這條弧線推到它的邏輯終點。

> 如果你對「兩種 SDD 意涵」還不清楚，先回看 [Ch 22 兩種「規格驅動」：可執行規格 vs 規格再生成](./22-two-meanings-of-spec-driven.md)。

---

## 「Vibe Coding」：Software 3.0 的反面教材

Karpathy 在 2025 年初也是「vibe coding」這個詞的創造者：描述一種完全沉浸在 AI 輔助中、不審查輸出、碰到錯誤就貼給 AI 再生成的開發方式。

這個詞在 SDD 社群裡被當作負面案例：

```
Vibe Coding                       SDD
──────────────                    ──────────
prompt → code → (run) → (bug)    spec → code → verify → (bug)
→ paste bug back to AI           → update spec → regenerate
→ pray                           → 有根可查
意圖存在對話記錄裡                意圖存在 spec 裡
對話結束就消失                    版本控制永久保存
下次 AI 不記得你說過什麼           下次 AI 讀 spec 就知道脈絡
```

AWS Kiro 的產品定位文（2025 年 7 月 14 日，kiro.dev/blog/introducing-kiro/）直接把 Kiro 描述為「vibe coding 的紀律化版本」——把 vibe coding 的流暢感加上 spec 的清晰度。

---

## 歷史脈絡：在 Karpathy 之前

這條「高層意圖驅動低層實作」的思路並非 Karpathy 首創，他是把它的推力說清楚了：

### 更早的表親

- **文學編程（Literate Programming，Knuth 1984）**：把程式碼嵌入自然語言說明中，讓程式「像文學一樣可讀」。Knuth 的目標是讓人類讀懂，程式碼仍由人類寫。SDD 的方向相反：讓機器從自然語言生成程式碼。
- **BDD/Given-When-Then（Dan North, 2006）**：用人類可讀的句子描述行為，工具把句子對映到測試。這是「可執行規格」的早期型態。
- **MDA（Model-Driven Architecture，OMG 2001）**：從 UML 模型生成程式碼。理念上和 SDD 最近，但失敗了，因為 UML 和 code generator 都太僵硬，模型比程式碼更難寫。

Karpathy 的貢獻是指出 **LLM 解決了 MDA 的「生成器太脆」問題**：語言模型足夠通用，能從高度抽象的意圖描述生成品質可用的程式碼。

> 如果你對 BDD/TDD/MDA 的血緣還不熟，先看 [Ch 25 祖先與對照：TDD / BDD / MDA / 文學編程](./25-tdd-bdd-mda-lineage.md)。

---

## 踩雷集錦

### 踩雷 1：把 Software 3.0 理解成「AI 取代一切，人類退出」

**錯誤直覺**：Karpathy 說「英文是程式語言」→ 程式設計師以後不需要懂技術，說說話就好。

**正確認識**：Karpathy 的 autonomy slider 論點恰恰相反——他強調「partial autonomy」（部分自主）的重要性。LLM 是很強的「執行工具」，但意圖的精確表達（spec 的品質）決定了輸出的品質。能寫出清楚、完整、無歧義的 spec，是一種新的核心技能，要求對技術、領域、邊界條件都有深度理解。說「英文是程式語言」，不代表「用說的就夠了」——你對英文本身的要求提高了。

### 踩雷 2：以為 Software 2.0 的「weights as program」和 Software 3.0 的「prompt as program」是同一件事

**錯誤直覺**：2.0 和 3.0 都說「不是傳統程式碼了」，所以其實一樣吧？

**正確認識**：兩者有本質差別。Software 2.0 的「程式」（weights）是從大量資料 training 出來的，是非語言的、不可讀的、不可編輯的。Software 3.0 的「程式」（prompt/spec）是人類用自然語言寫的，是可讀的、可版本控制的、可 review 的。SDD 關注的是 3.0 這一層，不是 2.0。

### 踩雷 3：把 Karpathy 的框架視為「規格驅動開發的理論來源」

**錯誤直覺**：SDD 的理論根基就是 Karpathy → 所以 Karpathy 是 SDD 的提倡者。

**正確認識**：Karpathy 提供了語言和框架，但他本人並沒有提倡某種具體的軟體開發流程。SDD 的工具化（Spec Kit、Kiro、Tessl）和思想結晶（Grove 的 The New Code）是不同的人在不同場合獨立發展的。「spec-driven development」這個詞本身也不是任何一個人發明的，它在 2025 年 AI 編碼社群中有機地匯聚成形。不要把 Karpathy 框架和 SDD 的具體方法論混為一談。

### 踩雷 4：認為「LLM as OS」意味著現有的作業系統知識沒用了

**錯誤直覺**：LLM 是新的 OS，所以 Linux/Windows 的那套知識過時了。

**正確認識**：「LLM as OS」是一個**類比**（analogy），強調 LLM 的通用性和可程式化性，不是字面上的技術替代。LLM 仍然運行在真實的 OS 上。這個類比的用處是幫你理解「prompt 對 LLM 的關係，就像程式對 OS 的關係」，讓你把對 prompt/spec 的嚴謹度提升到「寫程式」的層次，而不是「隨口說說」的層次。

### 踩雷 5：把「訓練資料」當成 Software 3.0 的「程式」

**錯誤直覺**：Software 3.0 的 LLM 是從更大的資料訓練出來的，所以 3.0 還是 2.0 的延伸。

**正確認識**：從工程師的使用者視角，Software 3.0 的重點是「LLM 已經存在，你不需要自己訓練它；你需要做的是寫 prompt/spec」。訓練那一層對大多數工程師是黑盒子，就像你用 OS 不需要自己實作 CPU 排程器一樣。3.0 的「程式」是你寫的 spec，不是底層的訓練資料。

---

## 進階延伸

### 「LLM 作為 OS」的技術含義

如果你想深入這個類比的技術維度：現代 LLM 應用的 context window 管理、memory 架構、tool calling、multi-agent 協調，這些確實在演化出類似 OS 的概念——process scheduling、IPC、memory management 的 LLM 對應物。本課程 [Ch 35 Bounded Context = Agent Scope](./35-bounded-context-agent-scope.md) 會從 DDD 角度討論 agent 的邊界問題。

### Software 3.0 的「程式設計語言」問題

自然語言作為程式語言有一個根本限制：歧義性（ambiguity）。「處理使用者認證」這句話對工程師、PM、資安人員的意思完全不同。這也是 EARS（Easy Approach to Requirements Syntax）這類受限語言（constrained language）的存在理由——在自然語言和形式語言之間找一個平衡點。

> 如果你對 EARS 還不熟，先回看 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)。

### 「意圖表達」的歷史哲學

「高層意圖驅動低層實作」這個方向是計算機科學的一條長線：從組合語言→高階語言→領域特定語言（DSL）→自然語言。每一次往上一層，都付出了一些精確性，換來了表達效率。SDD 是這條線的當前最前沿，同樣的代價-效益問題仍然存在。

---

## 動手練習

這個練習不需要寫程式碼。

**情境**：你在一家電商公司工作，Product Manager 說：「我要一個推薦系統，讓使用者看到他們會喜歡的商品。」

用 Software 1.0、2.0、3.0 的框架，各寫一段「工程師怎麼把 PM 的意圖轉化成工作」的描述：

1. **Software 1.0 視角**（你要寫什麼「指令」？規則是什麼？）
2. **Software 2.0 視角**（你要蒐集什麼「資料」？訓練目標是什麼？weights 存著什麼「知識」？）
3. **Software 3.0 / SDD 視角**（你要寫什麼「spec」？spec 裡要包含哪些邊界條件、使用者故事、驗收標準，讓 AI 能從 spec 生成可信的實作？）

對比三個版本，找出「可以被機器處理的意圖邊界」在哪裡移動了。

---

## 本章重點整理

- Karpathy 2017 年的 Software 2.0 論文提出：訓練過程就是「把 dataset 編譯成 binary（weights）」，神經網路 weights 是一種新型態的「程式」。
- 2023 年 1 月 24 日的推文「The hottest new programming language is English」，把 Software 2.0 的邏輯延伸到 LLM 的通用性上。（查證日期 2026-06-30；原推文因平台限制無法直接抓取，日期/措辭依 Quote Investigator 查證）
- 2025 年 6 月 17 日的 YC AI Startup School 演講，正式提出 Software 3.0：LLM 是新型可程式化電腦/OS，English prompt/spec 是其程式語言。（無官方逐字稿；依 Latent Space 授權整理文）
- 三代的根本差異在「人類描述的層次」：1.0 描述解法（how），2.0 描述目標+例子（what + examples），3.0 描述意圖（intent）。
- SDD 的「spec 取代 prompt 成為版本控制核心」，是 Software 3.0 框架的邏輯結論：意圖既然是程式，就應該被嚴謹地版本控制。
- Karpathy 強調 partial autonomy（部分自主），偏好「Iron Man suit」型應用，而非全自動 agent——這和 SDD 的「人類保持 spec 控制權」一致。
- 「英文是程式語言」不代表不需要精確性，恰恰相反：對意圖描述的精確度要求比過去更高。

---

## 自我檢核

- [ ] 我能不看書，用自己的話解釋「Software 2.0 為什麼說 training 就是編譯」——如果面試官問我，我會怎麼答？
- [ ] 我能說清楚 Software 2.0 的「weights 是程式」和 Software 3.0 的「prompt/spec 是程式」，這兩件事哪裡相同、哪裡不同。
- [ ] 我能用 Software 3.0 的框架解釋「為什麼隨手打 prompt 不夠、需要寫 spec」。
- [ ] 我能說出 Karpathy 的「自主性滑桿」論點，以及它為什麼支持「人類保留 spec 控制權」的 SDD 哲學。
- [ ] 我知道「英文是最熱的程式語言」這句話的 context，不會錯誤地理解成「不需要技術深度了」。
- [ ] 我能說出 MDA 失敗的原因，以及 Software 3.0 / LLM 如何解決（或聲稱解決）那個問題。

---

## 延伸閱讀

1. **Software 2.0 — Andrej Karpathy**
   - URL：https://karpathy.medium.com/software-2-0-a64152b37c35
   - 讀哪裡：開頭的 1.0/2.0 定義，以及「compiles the dataset into the binary」段落。
   - 和本章的關聯：本章所有關於 Software 2.0 的論斷的第一手來源。作者是 OpenAI 共同創辦人、前 Tesla AI 總監，可信度高。發表於 2017 年 11 月 11 日。

2. **Andrej Karpathy: Software in the Age of AI（Software 3.0 授權整理文）— Latent Space**
   - URL：https://www.latent.space/p/s3
   - 讀哪裡：「3.0 is eating 1.0/2.0」段落，以及 autonomy slider / Iron Man suit 的論述。
   - 和本章的關聯：Karpathy 2025 年 YC 演講的最可信文字參照（無官方逐字稿）。演講日期 2025 年 6 月 17 日。

3. **Quote Origin: The Hottest New Programming Language Is English — Quote Investigator**
   - URL：https://quoteinvestigator.com/2024/10/20/hottest-program/
   - 讀哪裡：開頭的日期與措辭確認段落（2023 年 1 月 24 日）。
   - 和本章的關聯：確認 Karpathy 推文的精確日期與措辭；因原推文對自動抓取工具返回 402，這是查證日期 2026-06-30 最可信的二手來源。

4. **Spec-Driven Development with AI（GitHub Blog） — Den Delimarsky**
   - URL：https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/
   - 讀哪裡：SDD 的工作定義（「a contract... the source of truth your tools and AI agents use」）與 Spec Kit 的 anti-vibe-coding 定位。
   - 和本章的關聯：展示 Karpathy 框架如何在工具層落地；Spec Kit 的官方首發公告（2025 年 9 月 2 日）。

5. **Introducing Kiro — Kiro team / AWS**
   - URL：https://kiro.dev/blog/introducing-kiro/
   - 讀哪裡：「the flow of vibe coding + the clarity of specs」的產品定位，以及 requirements/design/tasks 三文件工作流。
   - 和本章的關聯：展示 Software 3.0 框架如何在 IDE 層具體化；與 vibe coding vs SDD 的對比直接相關。發表於 2025 年 7 月 14 日。

6. **Announcing Our Series A for AI Native Software Development — Guy Podjarny（Tessl）**
   - URL：https://tessl.io/blog/announcing-our-series-a-for-ai-native-software-development/
   - 讀哪裡：「spec-centric, as opposed to code-centric」的業界視角，以及「make specs the primary artifact, and let code follow」。
   - 和本章的關聯：展示 Software 3.0 哲學如何在創業生態中轉化為商業押注；Podjarny 是 Snyk 創辦人，2024 年 11 月 14 日的 Series A 公告早於 Grove 和 Spec Kit。

7. **Literate Programming — Wikipedia（摘要 Knuth 1984）**
   - URL：https://en.wikipedia.org/wiki/Literate_programming
   - 讀哪裡：與 SDD 的差異比較——Knuth 的目標是讓人類讀懂，SDD 的目標是讓機器生成。
   - 和本章的關聯：建立歷史脈絡：「prose first，code second」的思路在 SDD 之前已有深厚積累；理解差異有助於說清楚 SDD 的真正新穎性。

---

下一章我們直接看 Sean Grove 在 AI Engineer World's Fair 2025 的演講《The New Code》，他把 Karpathy 這條弧線的邏輯推到終點：spec 不只是輔助工具，它應該取代程式碼成為版本控制的核心。

→ [Ch 24 Sean Grove《The New Code》：規格作為單一真相來源](./24-the-new-code.md)
