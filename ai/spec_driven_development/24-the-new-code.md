# Ch 24 — Sean Grove《The New Code》：規格作為單一真相來源

> **目標**：理解 Grove 在 AI Engineer World's Fair 2025 提出的核心論點——規格（spec）才是應該被版本控制的真相，而非程式碼——並拆解「shred the source, version-control the binary」這個類比的力道與界限，以及 80-90% 價值在「結構化溝通」的主張。

## 心智圖像：你現在在做什麼，等價於哪件事？

先想一個程式設計師的日常：

```
你腦中的意圖
   │
   ▼
[你打的 prompt]  ← 其實這才是「原始碼」
   │
   ▼
[LLM 生成的 code]  ← 這是「二進位」
   │
   ▼
你把 code 推進 git，把 prompt 丟掉
```

Sean Grove 在 2025 年 AI Engineer World's Fair 的演講《The New Code》說的，
就是上面這張圖的最後一行有多荒謬。

他的原話（引自社群謄本，非 OpenAI 官方逐字稿，查證日期 2026-06-30）：

> "this feels like a little bit like you shred the source and then you very carefully version control the binary."

這個類比能刺到人，是因為它翻轉了所有受過訓練的工程師的本能：
我們永遠版本控制原始碼，讓 build 系統自動產出 binary。
但如果 prompt 是「原始碼」、code 是 LLM compile 出來的 binary，
那我們現在的工程習慣正好做了相反的事。

## 歷史脈絡：這個問題之前人們怎麼做？

在 AI coding agent 普及之前，「什麼是真相來源」這個問題幾乎沒有歧義：
程式碼就是真相，文件（文字規格）不過是解釋程式碼的附屬品。

傳統流程：

```
PRD (Word/Confluence)  ──  設計文件  ──  人寫 code  ──  code 進 git
     ↑                                         ↑
  「軟」規格（慢慢過時）           「硬」真相（一切以 code 為準）
```

這個模型有個隱含假設：**code 是昂貴的，所以要保護它**。
人力是貴重資源，一旦 code 寫好就要版本控制。
PRD 沒有版本控制也沒關係，它只是讓人讀的過渡文件，
遲早會被程式碼「本身」取代（你只要讀 code 就知道系統做什麼）。

### AI 把這個等式打破了

2023-2025 年，AI coding agent 的能力快速提升：
給定足夠好的指引，agent 可以在分鐘內產出原本需要數天的程式碼。

這讓「code 是昂貴資源」這個假設失效。
如果 code 可以被 regenerate，那什麼是真正應該保護的？

Karpathy 在 2017 年已經預見了類似的方向：

> "The process of training the neural network compiles the dataset into the binary — the final neural network."

（Software 2.0 essay, Nov 11, 2017）

他的框架裡，神經網路權重是 compile 出來的，dataset 才是應該被管理的「原始碼」。
Grove 在 2025 年把這個邏輯搬到軟體規格上：
spec 是意圖，code 是 compile 結果。

## 核心主張一：規格是單一真相來源

Grove 的論點有三層：

### 層一：Prompt ≠ Spec

很多人把 SDD 想成「把 prompt 存下來」。
這是錯的。

Prompt 是揮發性的、上下文相關的、不可組合的。
Spec 是持久的、可被團隊共享的、可版本控制的。

| 維度 | Prompt | Spec |
|------|--------|------|
| 壽命 | 對話結束即消失 | 與專案共存 |
| 受眾 | LLM | 人類 + LLM |
| 粒度 | 一次任務 | 整個功能或系統 |
| 版本控制 | 難（無結構） | 天然適合 Markdown + git |
| 可重現性 | 低（上下文不同輸出不同） | 較高（相同 spec 驅動相同實作） |

### 層二：80-90% 的價值在結構化溝通

Grove 的說法（同上，社群謄本）：

> "code is 10% to 20% of the value... the other 80% to 90% is in structured communication"

「最有價值的程式設計師」不是手速最快的人，
而是「communicates most effectively」的人。

這個主張在 2025 年有其脈絡：如果 code 可以被 agent 產出，
那決策品質（什麼要做、為什麼做、做到什麼程度）才是稀缺資源。

要誠實標注：這個 80/90% 數字來自社群謄本，不是受控實驗的結果。
它是一個論點框架，不是量化研究（這類實驗難以設計且未見可靠一次研究）。

### 層三：OpenAI Model Spec 作為活規格（living spec）範例

Grove 用 OpenAI 自家的 Model Spec（openai/model_spec，Markdown 在 GitHub 上公開）
作為「規格作為真相來源」的具體示範：

- Model Spec 用條文描述模型應有的行為，包含反討好（anti-sycophancy）條款
- 條文的措辭（「sycophancy might feel good in the short term, it's bad for everyone」）
  代表 OpenAI 對模型行為的正式意圖
- 若模型行為偏離 spec，那就是 **一個 bug，而不是 spec 應該配合模型行為改變**

這個例子的力道在於：Model Spec 並不是「有了 code 之後寫的說明文件」，
而是**在訓練和評估之前存在的規格**，code（weights）是 spec 的實現。

```
Model Spec (Markdown, git versioned)
          │
          ▼  deliberative alignment
   Training data / RLHF
          │
          ▼
   Model weights (= the "binary")
```

這個架構和 Karpathy Software 2.0 裡「dataset → compiled weights」的比喻直接呼應。

> 如果你對 Karpathy 的 Software 2.0/3.0 架構還不熟，先回看 [Ch 23 從 Software 2.0 到 Software 3.0：Karpathy 的弧線](./23-software-2-to-3.md)

## 核心主張二：「shred the source」類比的力道在哪裡

再拆解一次這個類比，抓住它打中的東西和它的界限。

### 類比成立的部分

在傳統編譯模型裡，這兩件事是等價的：

```
A：把 source.c 刪掉，保留 a.out   → 所有人都覺得荒謬
B：把 spec.md 丟掉，保留 impl.py  → 2025 年 99% 的專案正在做的事
```

A 和 B 都丟失了同一個東西：**可讀性、可修改性、可再生性**。

你改不了 a.out，除非你反組譯又猜意圖。
你改不了 impl.py 的深層邏輯，除非你逆向工程又問「這段在幹嘛」。

版本控制 spec 讓你能做到：
- 回溯決策（這個欄位為什麼存在）
- 協作審查（這個行為期望是否正確）
- 重新生成（換個 LLM、換個 framework、升版）

### 類比的界限（不要過度解讀）

**類比不成立的地方**：

傳統 compiler 是確定性的（deterministic）。
給同一份 source，每次 compile 出相同的 binary。

LLM 不是確定性的（temperature > 0）。
給同一份 spec，不同的 LLM、不同的 context window、不同的日期，
產出的 code 可能截然不同。

這表示 spec → code 不是單向不可逆的關係。
你無法假設「有 spec 就夠了，code 隨時可以重生成且完全等價」。

**正確的使用姿勢**：
spec 是意圖的真相來源，code 是在特定時間點、特定 LLM、特定需求下的實現。
兩者都要版本控制，但 spec 是「主」，code 是「從」。

另一個限制：spec 的「精確度」是個難題。
自然語言有不可消除的歧義。
一份說「handle high traffic gracefully」的 spec，
對 PM、backend engineer、SRE 各有不同的意思。

> 這就是為什麼有 EARS notation 這類限制性句型。
> 詳見 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)

## 動態類比：spec 是 DNA，code 是蛋白質

有另一個補充類比有助於理解：

```
DNA（spec）──轉錄──▶ mRNA（LLM context）──轉譯──▶ 蛋白質（code）
  ↑                                                      ↑
持久、版本控制、                               揮發、環境依賴、
複製後代也用它                                   根據需求不斷重新合成
```

細胞不保留每個蛋白質的精確排列記錄；它保留 DNA，需要時重新表達。
但細胞也不「丟掉」蛋白質——蛋白質仍然存在並且作用，只是 DNA 是真相的來源。

這個類比同樣不完美（生物不是軟體），但它強調了一個重點：
**spec 和 code 同時存在；spec 是上游，不是替代品。**

## 對比：為什麼不選其他方案？

| 方案 | 直覺 | 為什麼不夠 |
|------|------|-----------|
| 保留 prompt，丟掉 spec | 「有 prompt 就夠了」 | Prompt 無法被人類直接審閱、難以跨工具重用、無法建立共識 |
| 保留 code，丟掉 spec | 傳統做法 | AI 時代 code 可再生，真正昂貴的是意圖；code 也會腐化 |
| Code + 行內注釋（Knuth 文學編程）| 「prose 解釋 code」 | 注釋是解釋，不是規格；機器無法用注釋重新生成不同的實現 |
| 保留測試（TDD） | 「測試就是可執行的規格」 | 測試驗證行為，但無法描述為什麼要這個行為、如何取捨 |
| 保留 spec + code（兩者並存）| Grove 真正的主張 | **這才是正解**，但 spec 是主，code 是從 |

## 踩雷集錦

### 雷一：把「版本控制 spec」當成「不用版本控制 code」

**錯誤直覺**：既然 code 是 binary，那 code 不用進 git。
**正確認識**：code 仍然要版本控制。Grove 的主張是 spec 是**主要**真相來源，不是說 code 可以丟棄。在 spec 不夠精確的現實下，code 是實際行為的最終記錄。

### 雷二：把 Grove 的論點當成「SDD 是 Grove 發明的」

**錯誤直覺**：Grove 的演講太有說服力，覺得 SDD 是他的概念。
**正確認識**：「spec-driven development」這個術語在 2025 年的 AI coding 社群中有機出現，沒有單一發明人。Guy Podjarny（Tessl）在 2024 年 11 月已提出「spec-centric, as opposed to code-centric」；GitHub Spec Kit 和 AWS Kiro 也是各自獨立發展的實踐工具。Grove 的演講是這個方向的有力聲明，不是起源。

> 關於術語混用的問題，詳見 [Ch 22 兩種「規格驅動」：可執行規格 vs 規格再生成](./22-two-meanings-of-spec-driven.md)

### 雷三：把 OpenAI Model Spec 類比無限延伸

**錯誤直覺**：既然 Model Spec 成功，那所有軟體只要有一份 Markdown spec 就能驅動 AI 做出正確的東西。
**正確認識**：Model Spec 之所以有效，是因為 OpenAI 有完整的 RLHF 訓練管線、評估基礎建設、和確保 spec 與訓練對齊的人工審核流程。一般軟體專案直接拿 Markdown spec 餵給 LLM，沒有這些配套，效果截然不同。

### 雷四：80% 是實驗數據

**錯誤直覺**：「80-90% 的價值在結構化溝通」是 Grove 做了實驗測量的。
**正確認識**：這是演講中的論點框架，出自社群謄本（非官方逐字稿，查證日期 2026-06-30）。它是經驗判斷，不是量化研究。把它當直覺輸入和論述工具，不要當實驗數據引用。

### 雷五：把「spec 作為真相來源」當成「spec 一定要先寫完才能開始」

**錯誤直覺**：既然 spec 是主，那要有完整 spec 才能開始 coding。
**正確認識**：Grove 沒有說 spec 要一次寫完。Model Spec 本身就是活的、持續更新的文件。實踐上 spec 是迭代的——先有足夠的 spec，驅動第一個功能，然後根據實作反饋修改 spec。spec 先行不等於 waterfall。

> 關於 waterfall 與迭代的討論，詳見 [Ch 5 迭代與敏捷：用快速回饋換掉大份前期規格](./05-iterative-agile.md)

## 工具落地：誰在實踐這個論點？

Grove 的演講是 2025 年 6 月，同年就有兩個主要工具上線，
把「spec 作為真相來源」從論點變成可操作的工程流程：

**GitHub Spec Kit**（2025-09-02 開源，~116k stars，查證日期 2026-06-30）

spec-driven.md 裡的「Power Inversion」宣言直接呼應 Grove：

> "Specifications don't serve code—code serves specifications... The specification becomes the primary artifact. Code becomes its expression in a particular language and framework."

指令集現在已命名空間化為 `/speckit.*` 系列（查證日期 2026-06-30）。

**AWS Kiro**（2025-07-14 推出）

三份 spec 產物：`requirements.md`（EARS 驗收條件）、`design.md`（介面與架構）、
`tasks.md`（可追蹤任務清單）。

這三份文件合在一起，就是 Grove 說的那份「應該被版本控制」的規格。

> Kiro 的詳細工作流見 [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)
> Spec Kit 的工作流見 [Ch 28 GitHub Spec Kit（二）：/speckit.* 工作流端到端](./28-spec-kit-workflow.md)

## 一個可以跑的思想實驗

下面是一個具體的邊界例子，幫助你感受「spec 作為真相來源」在哪裡成立、哪裡失效。

**情境**：你在 2025 年 10 月寫了這份 spec：

```markdown
# 功能：使用者登入

## 行為
WHEN 使用者提交正確的帳號密碼，
  系統 SHALL 在 200ms 內回傳 JWT token，
  有效期為 24 小時。

WHEN 使用者連續失敗 5 次，
  系統 SHALL 鎖定帳號 15 分鐘，
  並發送告警 email 給 security@yourco.com。
```

2026 年 3 月，你要把 JWT 改成 session cookie。

**spec 作為真相來源的優勢**：
你只要改 spec 的「行為」欄，然後重新跑 agent 生成新實作。
不需要在 code 裡搜尋所有 JWT 相關的位置（可能散落在 10 個檔案）。

**spec 的界限**：
如果 2025 年的 agent 和 2026 年的 agent 對同一份 spec 的解讀不同，
你可能發現新的實作行為與舊的有細微差異，
不是因為 spec 變了，而是因為 LLM 的解讀漂移。

這就是為什麼**測試套件是 spec 的執行層**——
有驗收測試，你才能知道「重新生成的 code 是否符合 spec 的意圖」。

**輸出範例（正常情況）**：

```
spec.md 版本：v1.2（2026-03-01，修改：jwt → session cookie）
agent 生成：auth.py（重新生成）
測試結果：23 passed, 0 failed
spec 與實作：同步
```

**輸出範例（失敗情況）**：

```
spec.md 版本：v1.2
agent 生成：auth.py（重新生成）
測試結果：17 passed, 6 failed
失敗項目：session cookie 有效期未正確實作
原因：spec 說「24 小時」，但沒指定 sliding window 還是 absolute expiry
→ spec 有歧義，測試幫你發現了
```

這個失敗情況不是 agent 的問題，是 spec 不夠精確。
正確回應是**更新 spec，不是直接改 code**。

## 進階延伸

### Model Spec 的「deliberative alignment」

Grove 舉的 OpenAI Model Spec 例子背後有一個更深的機制：
spec 不只是人讀的文件，它進入訓練管線，變成對齊的約束。

這叫做 deliberative alignment（審慎對齊）：
模型在生成時「思考」spec 的條文，而不是依賴事後的 RLHF 修正。

對一般軟體開發的含義：
如果 spec 能進入 agent 的 system prompt 或上下文，
它就不只是文件——它是 agent 行為的運行時約束。

### Spec Registry（Tessl）

Guy Podjarny 的 Tessl 把 spec 做到更進一步：
Spec Registry 是「NPM for knowledge」，
讓你 import 別人寫好的 spec，而不是每次從零開始描述標準函式庫的使用方式
（查證日期 2026-06-30，版本依賴，以官方最新為準）。

這個概念成立的前提，就是 spec 是可組合、可版本控制的一等公民——
和 Grove 的論點直接相連。

## 動手練習

挑你最近一個月寫過的功能（或者一個你熟悉的功能），
完成以下三個步驟：

**步驟一**：用 EARS 句型寫出 spec（3-5 條）。
- `WHEN <trigger>，系統 SHALL <response>`
- `WHILE <state>，系統 SHALL <behavior>`
- `IF <condition> THEN 系統 SHALL <response>`

**步驟二**：對著你的 spec，回答這個問題：
「如果把現有的 code 全部刪掉，給一個沒看過這個功能的 agent 這份 spec，
它能生成出行為等價的 code 嗎？如果不行，spec 缺了什麼？」

**步驟三**：把你認為缺失的補進 spec，然後比較 spec 修改前後的版本差異。
這個 diff 就是你原本「只在 code 裡」的隱性知識。

不需要真的跑 agent——這個練習的目的是讓你感受 spec 的完整性邊界。

## 本章重點整理

1. Grove 的核心類比：保留 code 丟掉 spec，等於「shred the source, version-control the binary」——這翻轉了工程師的直覺，因為 code 現在是 AI 可以 compile 出來的 output。

2. 80-90% 的價值在結構化溝通：這是論點框架（出自社群謄本，查證日期 2026-06-30），不是量化實驗，但它指向一個真實的稀缺性轉移——AI 時代稀缺的不是 code 本身，而是精確的意圖表達。

3. OpenAI Model Spec 是活規格的範例：Markdown 在 GitHub，模型行為偏離即為 bug，規格驅動訓練與評估，不是 code 寫完才補的說明。

4. spec 與 code 並存，但 spec 是「主」：類比不完美（LLM 不是確定性 compiler），正確的姿勢是兩者都版本控制，spec 作為意圖的上游。

5. 這不是一個人的發明：「spec-driven development」是 2025 年在 Grove、Podjarny、GitHub Spec Kit、AWS Kiro 等多個獨立力量匯聚下浮現的術語，沒有單一起源。

## 自我檢核

- [ ] 我能用自己的話解釋「shred the source, version-control the binary」這個類比的核心邏輯，以及它的界限在哪裡
- [ ] 如果面試被問「為什麼 spec 比 prompt 更適合版本控制？」，我能答出至少三個維度的差異
- [ ] 我能說明 OpenAI Model Spec 作為「活規格」的具體機制（不是「它只是一份文件」）
- [ ] 我知道「80-90%」這個數字的來源是什麼，以及為什麼不能把它當實驗數據引用
- [ ] 我能解釋 spec 作為真相來源和「spec 要先全部寫完才能開始」的差別
- [ ] 我能舉一個真實場景，說明「重新生成 code 但保留 spec」有實際意義的時機

## 延伸閱讀

1. **Sean Grove 演講影片《The New Code》**（AI Engineer World's Fair 2025）
   - URL：https://www.youtube.com/watch?v=8rABwKRsec4
   - 讀哪裡：從頭看，特別注意「shred the source」類比出現的段落（約 8-12 分鐘處）和 Model Spec 的舉例。社群謄本：lawwu.github.io/transcripts/8rABwKRsec4.html（非官方）。
   - 和本章的關聯：本章所有核心論點的一次來源。

2. **GitHub Spec Kit — spec-driven.md（Power Inversion 宣言）**
   - URL：https://github.com/github/spec-kit/blob/main/spec-driven.md
   - 讀哪裡：「Power Inversion」一節，看「Specifications don't serve code—code serves specifications」的完整論述。
   - 和本章的關聯：把 Grove 的論點落地為可操作工程實踐的最具體文件。

3. **Software 2.0** — Andrej Karpathy（2017）
   - URL：https://karpathy.medium.com/software-2-0-a64152b37c35
   - 讀哪裡：開頭的 1.0 vs 2.0 定義，以及「The process of training the neural network compiles the dataset into the binary」段落。
   - 和本章的關聯：Grove「spec = source, code = binary」類比的智識根基。

4. **Andrej Karpathy: Software in the Age of AI（Software 3.0 授權文字整理）** — Latent Space
   - URL：https://www.latent.space/p/s3
   - 讀哪裡：「3.0 is eating 1.0/2.0」段落，以及 autonomy slider 的討論。
   - 和本章的關聯：理解為何 spec 驅動 LLM 是比 prompt 更高一層的抽象。

5. **Spec Driven Development: When Architecture Becomes Executable** — InfoQ
   - URL：https://www.infoq.com/articles/spec-driven-development/
   - 讀哪裡：為什麼 SDD 是架構模式而不是 TDD-like 方法論，以及供應鏈風險和技術債的討論。
   - 和本章的關聯：提供對 Grove 論點的實務批判與補充。

6. **Announcing Our Series A for AI Native Software Development** — Guy Podjarny，Tessl（Nov 14, 2024）
   - URL：https://tessl.io/blog/announcing-our-series-a-for-ai-native-software-development/
   - 讀哪裡：AI-Native 定義段落，「make specs the primary artifact, and let code follow」論述。
   - 和本章的關聯：Grove 演講之前最早的公開「spec-centric」框架，幫你理解這個觀念不是某一場演講突然冒出來的。

7. **OpenAI Model Spec**（GitHub，公開）
   - URL：https://github.com/openai/model_spec
   - 讀哪裡：anti-sycophancy 條款，以及文件開頭說明這份 spec 如何被用於訓練和評估的段落。
   - 和本章的關聯：Grove 在演講中引用的活規格範例，值得第一手看一次。

---

Ch 24 介紹了 Grove 的論點與 OpenAI Model Spec 作為具體範例。
下一章我們拉長視野，看看這套「規格先行」的想法在歷史上已有哪些前輩——
TDD、BDD、MDA、文學編程，它們各自走到了哪裡、又在哪裡停住了。

→ [Ch 25 祖先與對照：TDD / BDD / MDA / 文學編程](./25-tdd-bdd-mda-lineage.md)
