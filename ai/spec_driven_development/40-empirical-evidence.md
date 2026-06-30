# Ch 40 — 實測數據與復現報告

> **目標**：誠實讀懂四組核心量化結果——Eberhardt 的 ~10x 慢、Böckeler 的 scope inflation、METR 的 19% 變慢、Peng et al. 的 55.8% 加速——理解每組數據的條件邊界與適用範圍，再把它們拼成一幅沒有誤導性的全局圖。

---

## 先建立一個心智圖像

在任何一篇 SDD 文章的留言區，你都會看到兩種引用方式：

```
A 說：「有人測過，SDD 比普通提示慢 10 倍。」
B 說：「研究顯示 AI coding 讓工程師快 55.8%。」
C 說：「METR 的 RCT 證明 AI 讓有經驗的開發者慢了 19%。」
```

這三句話都引自真實的報告，但它們**不是在測同一件事**。把它們擺在一起好像是三個矛盾的結論，其實是三個不同情境的三個不同答案。

理解它們的方法，是先畫一個座標系：

```
                   成熟 codebase（有歷史包袱）
                            │
              ◄─────────────┼─────────────►
         自由創作（greenfield）              受管治（有規格）
                            │
                   新功能（greenfield）
```

每一個研究都落在這個座標系的不同象限。把它們混同是最常見、也最危險的解讀錯誤。

---

## 一、Eberhardt 的 ~10x 慢：Spec Kit 端到端再現（2025 年）

### 背景與來源

Colin Eberhardt 是 Scott Logic 的 CTO。2025 年 11 月，他寫了一篇完整的 Spec Kit 再現報告——把一個真實 go-kart PWA 的電路管理功能（約 1,000 行 code）用 Spec Kit 完整跑過一遍，記錄了每個階段的時間和產物行數。

**這是目前公開最完整、最誠實的 SDD 端到端計時**，有明確的 per-phase breakdown，不是印象式描述。

### 具體數字

| 階段 | 產物 | 行數 |
|------|------|------|
| `/speckit.constitution` | constitution.md | 161 行 |
| `/speckit.specify` | feature spec | 230 行 |
| `/speckit.plan` | 技術設計（5 份文件）| 共 2,067 行 |
| `/speckit.tasks` | 任務清單 | 66 步 |
| `/speckit.implement` | 最終 code | ~700 行 |

總計：
- **AI agent 執行時間**：33 分 30 秒
- **人工審核時間**：~3.5 小時
- **合計**：約 4 小時

對比：同一功能用一般 iterative prompting：
- AI agent 時間：~8 分鐘
- 人工審核時間：~15 分鐘
- **合計：約 23 分鐘**

比率：4 小時 ÷ 23 分鐘 ≈ **~10x 更慢**

### 最後還是出現了一個 bug

即便經歷了這套「工業化規格流程」，agent 最終仍然生出了一個 trivial bug：`circuitsData` 沒有正確被填充。

Eberhardt 的結論直接：「目前最快的路徑仍然是 iterative prompting 加 review，不是工業化的規格流水線。」

> 如果你對 Spec Kit 的工作流還不熟，先回看 [Ch 28 GitHub Spec Kit（二）：/speckit.* 工作流端到端](./28-spec-kit-workflow.md)。

### 邊界條件：這組數字告訴你什麼，不告訴你什麼

**告訴你**：
- 在 2025 年底版本的 Spec Kit、一個特定 go-kart PWA 功能上，SDD 比 iterative prompting 慢了約 10 倍
- 人工審核是時間的主要消耗（3.5h vs 33min agent 時間），不是 AI 計算速度
- 產物量遠大於最終 code 量（~2,500 行 Markdown → ~700 行 code）

**不告訴你**：
- 這套 overhead 在「第 2 個功能」、「第 10 個功能」是否縮小（spec 複用效益）
- 在需要多人協作、跨功能對齊的情境是否值得
- 在不同工具（Kiro / Tessl）上是否有同樣的倍數
- 這個 bug 是否在 iterative prompting 中也會出現

這是一個工程師、一個功能、一個工具版本的**單次再現，不是受控實驗**。它是非常有說服力的個案，但不能直接外推。

---

## 二、Böckeler 的 scope inflation：三工具橫向測試（2025 年）

### 背景與來源

Birgitta Böckeler 是 Thoughtworks 的 Distinguished Engineer，她的文章發表在 Martin Fowler 的網站上（martinfowler.com）。她不只用一個工具，而是親自測試了 Kiro、Spec Kit、Tessl 三個主要 SDD 工具，時間是 2025 年 10 月。

這篇文章建立了 SDD 工具分析的**標準詞彙**——她提出的三層分類法（spec-first / spec-anchored / spec-as-source）被後來幾乎所有評論者採用。

> 這三層分類在 [Ch 22 兩種「規格驅動」](./22-two-meanings-of-spec-driven.md) 有詳細說明。

### 最重要的量化觀察：Scope Inflation（範疇膨脹）

Böckeler 的核心量化發現之一：

> Kiro 把一個小 bug fix 自動展開成「4 個 user story、共 16 條接受條件」。

這不是邊界案例，這反映了工具的**設計傾向**——SDD 工具被設計來「完整規格化」，而「完整」在工具眼中幾乎總是意味著「更多文件」。

她的其他具體觀察：

| 工具 | 主要問題 | 機制 |
|------|---------|------|
| Kiro | Scope inflation（範疇膨脹）| 小需求被系統性放大成多個 user story |
| Spec Kit | 冗余 Markdown + 同時過度遵從與違反規格 | 文件重複；agent 忽略部分約束 |
| Tessl | 非確定性 | 同一份 spec 兩次執行生出不同 code |

### 「虛假的確定感」（False Sense of Control）

Böckeler 最尖銳的論點之一：SDD 工具讓使用者感覺「我寫了規格，AI 會照著做」，但實際上：

- 工具可能「太過積極地遵從」（過度執行，加入沒要求的東西）
- 工具可能「同時忽略」規格的部分指令
- Tessl 的同一份規格在兩次執行間產出不同 code

這讓 SDD 工具面臨一個類似 Model-Driven Development（MDD，模型驅動開發，2000 年代的嘗試，最終因工具難以掌控而沒有廣泛採用）的風險：把「inflexibility and non-determinism」這兩個最壞的性質組合在一起。

### Zaninotto 的補充：Markdown Madness

François Zaninotto（Marmelab CEO）在 2025 年 11 月補充了一個具體案例：用 Spec Kit 為一個時間追蹤應用程式的「顯示當前日期」功能做規格，最終產出 8 個檔案、超過 1,300 行 Markdown。

注意：Zaninotto 是在引用和分析他人的再現，他沒有親自執行工具——這一點需要標注（查證日期 2026-06-30）。但這個案例展示的模式和 Eberhardt 的計時數字相互印證：SDD 工具在小任務上的文件 overhead 尤其可觀。

---

## 三、METR 的 19% 變慢：AI 輔助對有經驗開發者的 RCT（2025 年）

### 背景與來源

METR 是一個模型評估非營利組織。2025 年 7 月，他們發表了目前**AI coding 研究中方法論最嚴謹的一篇**：arXiv:2507.09089「Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity」。

這是一個**真實的 RCT（隨機對照試驗，Randomized Controlled Trial）**——在 AI coding 研究中極其罕見。

### 研究設計

```
受試者：16 位有經驗的 open-source 開發者
         （平均維護目標 repo 約 5 年）
任務數：246 個任務
工具：Cursor Pro + Claude 3.5/3.7
Repo 特性：成熟的 open-source 專案（有歷史包袱）
```

### 結果

- **開發者預測**：AI 工具會讓他們快 24%
- **實際測量**：AI access 讓完成時間**增加了 19%**
- **事後感知**：即便任務完成後，開發者仍然錯誤地相信 AI 讓他們快了約 20%

這組數字揭露了兩個獨立的驚人結果：
1. AI 工具在這個情境下**讓有經驗的開發者變慢**，不是變快
2. 開發者對 AI 加速的**感知與現實是反的**——相信快了，實際上慢了

### 為什麼 SDD 文章引用這個數字，但不是直接證據

METR 的研究測的是 **AI 輔助（Cursor + Claude）對有經驗開發者在成熟 repo 上的影響**，不是「有沒有寫規格」對開發速度的影響。

SDD 討論引用這個數字的正確方式：作為「AI coding 工具的生產力效益並非理所當然」的背景證據，特別是在成熟 codebase 的情境下。

**如果有人說「METR 的研究顯示 SDD 讓開發者慢了 19%」——那是錯的。** 這項研究根本沒有測 SDD，它測的是 AI 輔助一般。

### 「感知 vs 測量」的 gap 有什麼含義？

這個 gap 在 SDD 的情境下格外重要。Farrag（University of East London）在 arXiv:2605.01160 的欄位研究中，其中一個指標是工程師的「自信心」從 3.1 → 3.9/5。

如果工程師對 AI 工具的速度感知本來就不準確（METR 的受試者在 AI 讓他們慢 19% 時仍相信快了 20%），那麼「自信心提升」這個主觀指標需要格外謹慎看待。

---

## 四、Peng et al. 的 55.8% 加速：GitHub Copilot 的 RCT（2023 年）

### 背景與來源

Sida Peng、Eirini Kalliamvakou、Peter Cihon、Mert Demirer 在 2023 年發表了 arXiv:2302.06590，研究 GitHub Copilot 對一個受控程式設計任務的影響。

**這是另一個 RCT**，也是目前引用率最高的 AI coding 正向結果研究之一。

### 研究設計與結果

```
任務：用 JavaScript 實作一個 HTTP server
受試者：95 位專業開發者（N=95）
對照：有 Copilot vs 無 Copilot
結果：有 Copilot 的開發者完成任務快了 55.8%
```

### 關鍵的條件邊界

這個 55.8% 數字背後有幾個重要的情境條件：

**任務類型**：單一、定義明確的功能實作（實作 HTTP server）。這是最適合 AI coding 輔助的情境——需求清晰、可從零開始、沒有歷史包袱。

**這不是成熟 codebase 的複雜維護任務**。和 METR 研究的任務類型幾乎是兩個極端。

| 維度 | Peng et al.（55.8% 加速）| METR（19% 變慢）|
|------|--------------------------|-----------------|
| 任務性質 | 全新、定義明確 | 成熟 repo 的雜項任務 |
| 受試者 | 95 人（較大樣本）| 16 人（較小樣本）|
| 工具 | GitHub Copilot | Cursor Pro + Claude 3.5/3.7 |
| codebase | 空白 | 有歷史包袱的 OSS repo |
| 設計 | RCT | RCT |
| 結果 | +55.8% 速度 | -19% 速度（慢了）|

**兩個都是 RCT，得出了方向相反的結果。** 這恰好說明「情境」是決定 AI coding 工具效益的關鍵變數，不是工具本身。

### 這個數字怎麼被引用（常見誤用）

Farrag 的 SDD 欄位研究（arXiv:2605.01160）的 Table 1-2 彙整了多個研究，其中包括 Peng et al. 的 55.8%。有些 SDD 支持者進而用這個數字「支持 SDD 的生產力主張」——這是**兩步錯誤**：

1. Peng et al. 研究的是 Copilot，不是 SDD 工具（Spec Kit / Kiro / Tessl）
2. Peng et al. 的任務情境（greenfield、明確需求）與 SDD 最常應用的情境（複雜功能規格化）不完全相同

引用這個數字時，條件必須標清楚。

---

## 五、Farrag 的 N=14 欄位研究：唯一測 SDD 的量化研究（2026 年）

### 背景

Sabry E. Farrag（University of East London）在 2026 年 5 月發表 arXiv:2605.01160「The Productivity-Reliability Paradox: Specification-Driven Governance for AI-Augmented Software Development」。

這是**目前唯一一個量化測量 SDD 治理（Spec Kit）效果**的研究，但它的限制非常明確。

### 研究設計

```
N：14 位工程師
時間：4 個月，before/after 設計（前後對比）
組織：單一機構
專案：3 個 web 專案
規格工具：Spec Kit
對照組：無（沒有控制組）
```

### 報告數字

| 指標 | 導入前 | 導入後 |
|------|--------|--------|
| median lead time | 8-12 天 | 6-9 天 |
| 晚期 hotfix | 3-5 次/sprint | 1-2 次/sprint |
| rollback | 2-4 次/月 | 0-1 次/月 |
| code churn | 12-18% | 6-10% |
| 工程師自信心 | 3.1/5 | 3.9/5 |

同時報告了**規格 overhead**：每個功能額外花 45-90 分鐘寫規格。

### 作者自己的免責聲明

論文第 10 節（limitations）明確說：

- 單一評分者（single rater）——主觀偏差風險高
- 無控制組——無法排除其他因素的影響
- 排除了 junior 工程師——可能低估了某些效益
- 指標是回溯性收集——不是前瞻性的工具測量
- 「**cannot support claims of statistical generalisation**」（無法支持統計上的普遍化宣稱）

作者自己把這些數字定性為「**indicative, not statistically controlled**（指示性的，非統計控制）」。

### 正確的引用方式

這份研究是「SDD 治理在某個小型、單一機構的短期觀察中似乎與一些指標改善相關」，不能被當成「SDD 讓生產力提升 X%」的證據。它是一個起點，說明有哪些指標值得在後續更嚴謹的研究中追蹤。

---

## 六、「AI 程式碼有 10 萬個存活技術債問題」：與 SDD 的關係

### 來源

「Debt Behind the AI Boom」（Liu, Widyasari, Irsan, Chen, Lo @ 新加坡管理大學；Zhao @ 華中科技大學），arXiv:2603.28592v2，2026 年 4 月。

研究規模：6,299 個 GitHub repo、302,579 個 AI authored commits，透過 Git metadata（如 `noreply@anthropic.com`、`copilot-swe-agent[bot]`、`Cursor Agent` 等）識別 AI 生成的 code。

核心數字：
- 識別出 484,366 個技術債問題
- 89.3% 是 code smells（程式碼異味）
- 6.0% 是正確性問題
- 4.7% 是安全問題
- 105,364 個（22.7%）在 HEAD 仍然存活

### 這個數字告訴我們什麼，以及不告訴我們什麼

**告訴我們**：AI 生成的 code 在大規模樣本中，確實有相當比例的技術債在存活——這是關於 AI code 品質的背景事實。

**不告訴我們**：SDD 是否改善或惡化了這個情況。這項研究研究的是「AI 生成的 code」，完全沒有涉及「有沒有用規格驅動」這個變數。

一些 SDD 文章把「AI code 有大量技術債」作為「我們需要 SDD 來管控品質」的論據——這個邏輯鏈在直覺上有一定說服力（更好的規格 → 更好的 AI 輸出 → 更少的技術債），但目前沒有任何研究直接測量這條鏈條。這是一個**未被驗證的因果路徑**，需要誠實標注。

---

## 七、把四組數字拼起來

下面是一個整合的視圖，按「研究對象」和「方法論嚴謹度」排列：

```
嚴謹度（RCT > 欄位研究 > 個案再現）

        高
        │
        │  Peng et al. 2023 (RCT, N=95)
        │  → Copilot 在 greenfield 任務：+55.8%
        │
        │  METR 2025 (RCT, N=16)
        │  → AI 輔助在成熟 repo：-19%
        │
        │  Farrag 2026 (欄位, N=14, 無控制組)
        │  → Spec Kit 治理 before/after：指標改善
        │
        │  Eberhardt 2025 (個案, N=1 功能)
        │  → Spec Kit vs iterative：~10x 慢
        │
        低
        └─────────────────────────────────────
          只測 AI coding 工具    測 SDD 工具
```

一個清醒的結論：**我們對 SDD 工具本身的量化效益，目前幾乎沒有嚴謹的研究**。有的是一個個案再現（Eberhardt，測了慢了多少）和一個小型欄位觀察（Farrag，作者自稱不能普遍化）。關於 AI coding 輔助工具的 RCT（Peng, METR）是有的，但它們測的是工具，不是規格驅動的方法論。

這不是說 SDD 無效——這是說「效益主張目前的證據基礎非常薄弱」，任何宣稱有大型 RCT 支持 SDD 的說法都需要仔細追溯原始來源。

---

## 對比取捨表

| 數據來源 | 測的是什麼 | 方法論 | 主要限制 | 能支持的宣稱 |
|---------|-----------|--------|---------|------------|
| Peng et al. 2023 | Copilot，greenfield，N=95 | RCT | 任務太窄、工具是 Copilot | AI 在特定情境可顯著加速 |
| METR 2025 | AI 輔助，成熟 repo，N=16 | RCT | 樣本小，特定工具組合 | AI 在成熟 codebase 可能使有經驗者變慢 |
| Eberhardt 2025 | Spec Kit，一個功能，N=1 | 個案再現 | 單一工程師、工具版本 | Spec Kit 在此情境比 iterative 慢 ~10x |
| Farrag 2026 | Spec Kit 治理，N=14 | 欄位研究（無控制組）| 作者自稱無法普遍化 | 小樣本中某些指標改善（需謹慎） |
| Debt Behind AI Boom 2026 | AI code 技術債，N=6299 repos | 大規模爬梳 | 與 SDD 方法無關 | AI code 有技術債存活問題 |

---

## 踩雷集錦

**錯誤直覺 1**：「METR 的研究說 AI 讓開發者慢 19%，所以 SDD 也一樣。」

**正確認識**：METR 的研究測的是 Cursor Pro + Claude 3.5/3.7 在成熟 OSS repo 上的效果，沒有任何 SDD 相關的設定或規格工具。把 METR 數字引用為「SDD 讓人變慢的證據」是研究對象的移花接木。METR 的正確用法：作為「AI 輔助不保證加速」的背景證據。

---

**錯誤直覺 2**：「Peng et al. 55.8% 說明 AI coding 很有效，SDD 加上 AI 應該更有效。」

**正確認識**：Peng et al. 測的是 Copilot 在一個 greenfield、定義明確的任務（實作 HTTP server）上的加速。SDD 最常應用的情境——複雜功能、多人協作、需要對齊理解——與這個任務情境差距很大。兩個 RCT（Peng 和 METR）在不同情境得出了方向相反的結果，這本身就說明情境比工具更重要。

---

**錯誤直覺 3**：「Farrag 的欄位研究顯示 lead time 從 8-12 天降到 6-9 天，SDD 有效。」

**正確認識**：Farrag 的作者自己說這些數字是「indicative, not statistically controlled」。N=14、單一機構、無控制組、回溯性指標，這是最低標準的量化設計。它說明「這些指標可能值得追蹤」，不能說明「SDD 導致了改善」。把這份研究當成 SDD 有效性的統計證據，是過度詮釋。

---

**錯誤直覺 4**：「Eberhardt 一個人的測試，代表不了什麼。」

**正確認識**：雖然 Eberhardt 的測試確實是單一工程師、單一功能，但它的價值在於**完整的透明性**——每個階段的時間和產物行數都有記錄，任何人可以重現或挑戰。相比那些沒有方法論說明的「我用 SDD 速度提升了 3 倍」的部落格文章，一個有透明記錄的個案再現品質更高。它的局限是不能外推，而不是沒有價值。

---

**錯誤直覺 5**：「AI 生了 10 萬個存活技術債問題，所以我們需要 SDD 來管控。」

**正確認識**：「AI code 有技術債」和「SDD 能減少技術債」是兩個獨立的宣稱。Debt Behind the AI Boom 只證明了前者；SDD 是否能改善 AI 生成 code 的品質，目前沒有直接測量。這個因果鏈目前是一個合理的假說，不是已驗證的事實。

---

**錯誤直覺 6**：「SDD 研究還少，所以先不要用 SDD。」

**正確認識**：「研究少」不等於「無效」，它只說明「我們目前無法量化宣稱其效益」。SDD 的採用決策應該基於你自己的情境測量和接受的不確定性，而不是等待一個尚不存在的大型 RCT。Brooker 的觀點（查證日期 2026-06-30）是：SDD 是一個仍在快速演進的範式，目前的工具測量數字都帶有版本綁定，實踐者需要持續重測。

---

## 進階延伸

### 「感知 vs 測量」的系統性 bias

METR 的「預測快 24%，實際慢 19%」不是特例。Kahneman 在《Thinking, Fast and Slow》裡描述的計畫謬誤（planning fallacy）在 AI 輔助情境下有一個新的變種：工具讓工作**感覺**更容易，即使測量結果更慢，主觀感受仍然是正面的。

這對 SDD 有一個直接含義：工程師在用了規格驅動工具之後的主觀滿意度（Farrag 的自信心指標 3.1→3.9/5）可能和客觀生產力效益脫鉤。公司在採用 SDD 時，如果只收集工程師滿意度調查，可能會得到一個偏向正面但不反映客觀現實的圖像。

### Birgitta Böckeler 的三層分類法和測量的關係

Böckeler 的 spec-first / spec-anchored / spec-as-source 三層分類的一個重要含義是：**不同層級的 SDD 在「什麼東西是 source of truth」這個問題上有根本的差異**，因此它們的測量指標也應該不同。

- spec-first：量 code 品質（spec 只是輔助，code 是結果）
- spec-anchored：量 spec-code 同步率（spec drift 的頻率）
- spec-as-source：量 spec 的表達力（能否完整表達預期行為）

現有研究幾乎都沒有區分這三層——Eberhardt 用的是 Spec Kit，Spec Kit 在 Böckeler 的分類中大約是 spec-first/spec-anchored 之間。把這個結果類推到 spec-as-source 工具（如 Tessl）是不當的。

> 這三層分類詳見 [Ch 22 兩種「規格驅動」：可執行規格 vs 規格再生成](./22-two-meanings-of-spec-driven.md)。

### 我們真正需要什麼樣的研究

如果要對 SDD 的效益做出嚴謹的宣稱，研究需要：

1. 明確的 SDD 工具和版本（工具版本綁定）
2. 隨機分配（控制混淆因素）
3. 足夠的樣本量（N=14 無法計算統計效力）
4. 多元任務（不只是 greenfield）
5. 客觀測量（不只是主觀自評）
6. 追蹤長期指標（技術債是否在 3 個月後才顯現）

這樣的研究目前不存在。這不是說不可能——這是說研究社群還沒跟上工具的爆發速度。

---

## 動手練習

以下練習不需要跑程式碼，需要的是批判性閱讀。

1. **閱讀 Eberhardt 的原文**（連結在延伸閱讀），找出他的 per-phase breakdown，畫一個時間甘特圖，把 agent 時間和人工時間分開標記。思考：哪個階段的人工時間最長？為什麼？

2. **閱讀 METR 的 blog summary**（連結在延伸閱讀），找出「感知速度 vs 實測速度」的那段。思考：如果你的團隊導入 SDD 後做了一個滿意度調查，這個感知 vs 測量的 gap 對你的調查設計有什麼含義？

3. **找一篇宣稱「SDD 讓生產力提升 X%」的部落格文章（可用搜尋引擎）**，追溯它引用的原始研究。問這三個問題：
   - 原始研究測的是 SDD 工具，還是 AI coding 工具一般？
   - 原始研究的任務情境和你的情境有多相似？
   - 原始研究有沒有控制組？

---

## 本章重點整理

- **Eberhardt 的 ~10x 慢**是目前最完整的 SDD 個案再現：Spec Kit 在一個 go-kart PWA 功能上花了約 4 小時（33min agent + 3.5h review），對比 iterative prompting 的約 23 分鐘。條件：2025 年底 Spec Kit 版本、一個功能、一位工程師。不能外推，但有透明的 per-phase 記錄。

- **Böckeler 的 scope inflation**：Kiro 把小 bug fix 放大成 4 user stories / 16 acceptance criteria；Spec Kit 產出冗余 Markdown；Tessl 同一 spec 兩次執行出不同 code。三工具都有「虛假的確定感」風險。

- **METR 的 19% 變慢**是最嚴謹的 AI coding RCT（N=16），但它測的是 Cursor + Claude 在成熟 OSS repo 的影響，**不是 SDD**。最重要的副產品：工程師在慢了 19% 的情況下仍相信自己快了 20%——這個感知 gap 對主觀調查的設計有重要含義。

- **Peng et al. 的 55.8% 加速**是另一個 RCT（N=95），測的是 Copilot 在 greenfield 任務的影響，**不是 SDD**。它和 METR 的方向相反，說明情境是主要的調節變數。

- **Farrag 的 N=14 欄位研究**是唯一直接測 Spec Kit 的量化研究，作者自稱結果「indicative, not statistically controlled」。不能作為 SDD 效益的統計證據。

- 目前對 SDD 工具本身的嚴謹量化研究幾乎是零。採用 SDD 的理性基礎是「合理的工程判斷 + 情境適用性」，不是「有 RCT 支持」。

---

## 自我檢核

- [ ] 我能說出 Eberhardt 測試的四個階段（Constitution/Specify/Plan/Tasks）各自產出的文件行數，以及為什麼人工審核時間遠超過 agent 執行時間。
- [ ] 面試被問「有沒有研究支持 SDD 的效益」，我能說出哪些研究測的是 SDD、哪些測的是 AI 輔助一般，並解釋為什麼這個區分很重要。
- [ ] 我能用自己的話解釋 Peng et al. 的 55.8% 和 METR 的 -19% 為什麼可以同時為真，兩者的情境邊界差在哪裡。
- [ ] 我知道 Farrag 的 N=14 研究有什麼限制，以及作者自己怎麼定性這些數字。
- [ ] 我能解釋「感知 vs 測量的 gap」（METR 的副產品）對 SDD 採用後的主觀滿意度調查有什麼含義。
- [ ] 我能說出 Debt Behind the AI Boom 這個研究的核心數字，以及為什麼不能把它直接用作「SDD 必要性」的證據。

---

## 延伸閱讀

- **[Putting Spec Kit Through Its Paces: Radical Idea or Reinvented Waterfall?](https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html)** — Colin Eberhardt（Scott Logic CTO），2025 年 11 月。本章 ~10x 數字的一手來源。先讀 "Plan" 和 "Implementation" 兩節取得 per-phase 數字，再讀最後的 "Reinvented Waterfall?" 段落。是目前公開的 SDD 個案再現中方法論最透明的一篇。

- **[Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)** — Birgitta Böckeler（Thoughtworks），發表於 martinfowler.com，2025 年 10 月。scope inflation 和 false sense of control 的一手來源；spec-first / spec-anchored / spec-as-source 三層分類的出處。讀各工具的具體測試段落，再讀 "False Sense of Control" 和 "MDD" 類比那節。

- **[Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)** — METR，2025 年 7 月；arXiv:2507.09089。目前最嚴謹的 AI coding RCT；-19% 和感知 gap 數字的一手來源。先讀 blog summary，再看 arXiv 裡關於任務設計和 forecast methodology 的章節。

- **[The Impact of AI on Developer Productivity: Evidence from GitHub Copilot](https://arxiv.org/abs/2302.06590)** — Peng, Kalliamvakou, Cihon, Demirer，arXiv:2302.06590，2023 年。55.8% 加速的一手來源。讀 Abstract 和 Section 3（Experimental Design），特別注意任務的限縮範圍——這是理解「為什麼它和 METR 方向相反」的關鍵。

- **[The Productivity-Reliability Paradox (arXiv:2605.01160)](https://arxiv.org/html/2605.01160)** — Sabry E. Farrag（University of East London），2026 年 5 月。唯一直接測 Spec Kit 治理效益的量化研究。先讀 Tables 1-2（彙整其他研究）和 Table 4（本研究數字），再讀 Section 10（limitations）——作者的誠實免責聲明是使用這份研究的先決條件。

- **[Debt Behind the AI Boom (arXiv:2603.28592v2)](https://arxiv.org/html/2603.28592v2)** — Liu, Widyasari, Irsan, Chen, Lo, Zhao，2026 年 4 月。105,364 個存活技術債問題的一手來源。讀 Tables in Sections 4-5 取得分類數字。使用前請記：這個研究測的是 AI 生成 code 一般，**不是** SDD 工具的輸出。

- **[Spec-Driven Development: The Waterfall Strikes Back](https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html)** — François Zaninotto（Marmelab CEO），2025 年 11 月。Markdown Madness（8 檔案/1,300 行）的來源。注意作者沒有親自執行工具，是在分析他人的再現——這本身就是本章「引用條件要標清楚」原則的一個好教材。

---

下一章我們轉換視角，從「SDD 有效不有效」轉向「SDD 在安全上帶來了哪些風險」——包括 prompt injection、規格被污染、以及 Simon Willison 真正定義的 lethal trifecta（不是 Osmani 借用的那個版本）。

→ [Ch 41 SDD 的安全面：prompt injection 與 lethal trifecta](./41-sdd-security.md)
