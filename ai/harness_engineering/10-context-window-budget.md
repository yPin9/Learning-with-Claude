# Ch 10 — Context window 是稀缺資源

> **目標**：建立 context engineering 的經濟學基礎。讀完你能精確說出：context window 是什麼、token 怎麼數、一次請求的 token 花在哪、為什麼「塞越多越好」是錯的、以及怎麼開始用「預算」的角度思考每一輪該放什麼進 context。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版）。本章有實際數 token 的範例。

## 為什麼 Part 2 從「稀缺」講起

Ch 6 你已經看到問題的影子：`messages` 只增不減，每輪都更大。但「更大」到底有什麼壞處？很多人模糊地知道「context 有上限」，卻沒有把它當成一個**要主動編列的預算**來經營。結果就是兩種極端：要嘛塞得太省、模型缺資訊亂答；要嘛塞得太貪、又貴又慢、模型還抓不到重點。

這一章不教任何壓縮技巧（那是 Ch 13 之後）。它做一件更基礎的事：**讓你對「token = 有限資源 = 錢 = 時間 = 注意力」這組等式建立量化的體感**。沒有這個體感，後面所有壓縮策略你都只是照抄，不知道為什麼。context engineering 的本質是資源分配，而資源分配的前提是你得先會算這個資源。

## 先建立直覺：context window 是模型的「工作桌面」

想像模型在一張固定大小的桌子上工作。每次它要處理你的請求，得把所有相關東西攤在這張桌上：你的問題、system prompt、整段對話歷史、工具清單、工具吐出來的資料……全部。桌子就這麼大（這就是 **context window**），東西攤太多就放不下、得擠掉一些。

```
   ┌─────────────────── context window（桌面，固定大小）───────────────────┐
   │                                                                       │
   │  system prompt   對話歷史   工具定義   工具結果   ←── 全部要攤在這張桌上   │
   │  ▓▓▓▓▓▓▓▓▓▓     ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓     [ 還剩這麼多空間 ]      │
   │                                                                       │
   │  ◀──────────── input（你放上去的）────────────▶  ◀── output（模型寫的）──▶ │
   └───────────────────────────────────────────────────────────────────────┘
```

兩個關鍵事實從這張圖直接讀出來：

1. **input 和 output 共用同一張桌子**。模型生成回應（output）也要佔桌面空間。所以「你放進去的 input」+「模型要寫的 output」加起來不能超過 window 大小。這就是 Ch 7 那個 `model_context_window_exceeded` 的由來——桌子滿了。
2. **桌子的大小是固定的**。不同模型桌子大小不同（context window 大小不同），但對單一模型，它是個硬上限。你不能把桌子變大，只能決定「桌上擺什麼」。

context engineering 整門學問，一句話講完，就是：**在這張固定大小的桌子上，每一輪都擺出『剛好夠模型做對這一步』的東西。** 多了浪費，少了出錯。

## 一、token 是什麼？怎麼數？

模型不是一個字一個字讀，是一個 **token** 一個 token 讀。token 是模型的基本單位——大致是「常見的字詞片段」。英文裡一個 token 大約對應 4 個字元 / 0.75 個單字；中文因為資訊密度高，**一個漢字常常就是 1 個甚至更多 token**，比英文「吃 token」。

別用猜的，實際數。SDK 提供 `count_tokens` 來精確計算一段 messages 會用掉多少 input token：

```python
from anthropic import Anthropic

client = Anthropic()

result = client.messages.count_tokens(
    model="claude-opus-4-8",
    system="你是一個有用的助理。",
    messages=[{"role": "user", "content": "解釋什麼是 context window，用兩句話。"}],
)
print(result.input_tokens)   # → 印出這份請求的 input token 數
```

`count_tokens` 不會真的去生成回應（不花生成的錢、也很快），純粹算「這份 input 多大」。它把 system、messages、甚至工具定義都算進去——也就是你**真正送進桌子的全部 input**。

> **為什麼不要用「字元數 / 4」就好？** Ch 6 我們用過那個髒估計建立直覺，但它對中文、對含工具定義的請求都不準。要做預算決策（例如「該不該壓縮了」），用 `count_tokens` 的精確值；要在迴圈裡每輪快速估個量級，髒估計可以。分清楚「精確 vs 估計」各自的場合。

## 二、一次請求的 token 花在哪：讀 `usage`

請求跑完後，回應的 `usage` 物件告訴你這次實際的 token 帳單。Ch 0 你見過它，現在認真讀它的欄位：

```python
resp = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=500,
    system="你是一個有用的助理。",
    messages=[{"role": "user", "content": "用三句話解釋 TCP。"}],
)
print(resp.usage)
# Usage(input_tokens=23, output_tokens=78,
#       cache_creation_input_tokens=0, cache_read_input_tokens=0)
```

| 欄位 | 意思 | 為什麼重要 |
|---|---|---|
| `input_tokens` | 這次送進去的 input 有多大（不含被快取命中的部分） | 隨對話歷史增長而變大——Ch 6 那條成長曲線就是它 |
| `output_tokens` | 模型這次生成了多少 | 受 `max_tokens` 上限約束 |
| `cache_read_input_tokens` | 命中 prompt cache、用較便宜算的部分 | Ch 17 的主題；命中越多越省 |
| `cache_creation_input_tokens` | 寫入快取的部分 | 同上 |

**input 和 output 的計費單價通常不同**（output 一般比 input 貴）。所以「成本」不是看總 token，要分開算。最簡單的（沒用快取時）：`input_tokens × input單價 + output_tokens × output單價`。一旦用了 prompt caching（Ch 17），就還要把 `cache_creation_input_tokens` 和 `cache_read_input_tokens` 算進去——它們各有自己的單價（寫入快取通常略貴、讀取快取則便宜很多），而「這次的總 input」其實是 `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` 三者之和。本章先用沒快取的簡單公式建立直覺，快取的省錢效果留到 Ch 17。

這對 agent 特別關鍵——因為 agent 每多跑一輪，就要把**整段越來越長的歷史當 input 重送一次**，input token 是會隨輪數累積暴漲的那一塊。

## 三、agent 的 token 成本為什麼會「平方級」成長

這是 agent 跟單次問答最不一樣、也最容易讓帳單失控的地方。看一個具體推演。假設一個任務跑了 N 輪，每輪歷史增加固定一塊：

```
   第 1 輪 input：[問題]                          ← 小
   第 2 輪 input：[問題 + 回應1 + 工具結果1]        ← 變大
   第 3 輪 input：[問題 + 回應1 + 結果1 + 回應2 + 結果2]  ← 更大
   ...
   第 N 輪 input：[前面全部]                       ← 最大
```

因為**每一輪都把前面累積的全部歷史當 input 重送**，在「不做任何壓縮、也先不算 prompt cache 折扣」的前提下，總 input token 不是「N × 單輪大小」，而是接近「1 + 2 + 3 + … + N」的等差級數和，量級是 **O(N²)**。也就是說，一個跑 20 輪的任務，它的 input 成本遠不止「20 倍的單輪」，而是接近「200 倍的單輪基數」。

（兩個但書：prompt caching 能讓「重送的穩定前綴」用便宜很多的快取價計費，**大幅壓低實際帳單**，但它不會改變這條曲線的形狀、也不解決 window 上限與訊噪比問題——它是折扣，不是解藥。真正打斷這條 O(N²) 曲線的是壓縮與裁剪。）

```
   單輪成本若是固定的 → 總成本只是線性疊加
   但 agent 的單輪 input 自己在長大 → 總成本是平方級堆疊

   token
    ▲
    │                                    ░░░  ← 實際（平方級）
    │                              ░░░░░░
    │                        ░░░░░░
    │                  ░░░░░░          ___  ← 你以為的（線性）
    │            ░░░░░▒▒▒▒▒▒▒▒▒▒▒▒▒
    │      ░░░▒▒▒▒▒▒▒▒▒▒▒
    └──────────────────────────────────────▶ 輪數
```

**這就是為什麼 context 管理對 agent 是生死問題、不是優化問題。** 對單次聊天，context 大頂多貴一點點；對會跑很多輪的 agent，不管 context，成本和延遲都會平方級爆炸，而且遲早撞上 window 上限。Part 2 後面的壓縮、裁剪、memory，全都是在打斷這條平方級曲線。

## 四、為什麼「塞越多越好」是錯的

就算桌子夠大、錢也夠，把所有東西都塞進 context 仍然是壞主意。三個理由：

### 理由一：成本與延遲

如上，input 越大越貴、越慢。每一個你「順手塞進去以防萬一」的 token，乘以剩餘的輪數，都是真金白銀和等待時間。

### 理由二：注意力會被稀釋（context rot / lost in the middle）

模型的注意力不是無限均勻的。當 context 塞滿大量內容，模型**更容易忽略或弄混其中的細節**，尤其是放在**中間**位置的資訊（業界稱為 "lost in the middle" 現象）——開頭和結尾的東西模型抓得比較住，中段的容易被「淹沒」。

也就是說，**塞太多反而會降低模型在你真正在意的那件事上的表現**。一份精煉的 5000-token context，常常比一份塞滿無關內容的 50000-token context 讓模型答得更準。這違反「資料越多越好」的直覺，但對 LLM 是真的。

> 釐清兩個常被混用的詞：**lost in the middle** 是上面那個有論文實證的「位置效應」——同一份資訊放中間，比放開頭或結尾更容易被模型漏掉。**context rot** 則是業界更廣義的講法，泛指「context 越長越雜，模型整體行為越退化」。兩者相關但不等同：前者講位置，後者講長度與雜訊。它們都是經驗性傾向、程度隨模型而異，不是精確定律——但共同指向同一個結論：**訊噪比（signal-to-noise）比絕對資訊量更重要**。

### 理由三：可預測性與除錯

context 越乾淨，agent 的行為越可預測、出錯越好查。一個塞滿歷史垃圾的 context，當模型突然行為怪異時，你很難判斷是哪段內容把它帶歪的。精簡的 context 是可維護性的一部分。

**結論**：context engineering 的目標不是「塞好塞滿」，而是**最大化「相關訊號」、最小化「無關雜訊」**。「給模型剛好它需要的，不多不少」——這句話會貫穿整個 Part 2。

## 五、開始用「預算」思考

把上面的認識落地成一個可操作的心態：**每一輪請求，你都在花一筆 token 預算，你應該知道它花在哪、值不值得**。一個粗略的預算框架：

```
   一輪 input 的 token 預算大致分給：
   ┌──────────────────────────────────────────────┐
   │ system prompt + 工具定義   （固定成本，每輪都付）  │ ← Ch 11, Ch 17(快取它)
   │ 對話歷史                   （隨輪數成長，要管理）  │ ← Ch 13(壓縮)
   │ 工具結果                   （常是最肥，要裁剪）    │ ← Ch 16
   │ 撈進來的參考資料            （RAG，要精選）        │ ← Ch 15
   │ ──────────────────────────────────────────    │
   │ 預留給 output 的空間        （別把桌子塞到沒地方寫） │ ← Ch 7
   └──────────────────────────────────────────────┘
```

注意最後一項：**你必須替 output 預留空間**。如果你把 input 塞到逼近 window 上限，模型就沒地方生成回應了（或只能生成很短）。`max_tokens` 設多少，要跟「input 佔了多少」一起算，兩者加起來不能超過 window。

這個框架現在還很抽象，但它是 Part 2 的地圖：後面每一章都是在優化這張預算表裡的某一格。你現在要帶走的是這個**心態轉變**——從「把能放的都放進去」變成「我有一筆有限預算，每個 token 都要值得」。

## 失敗示範：不數就送，撞牆才知道

看一個沒有預算意識的寫法會怎麼出事。一個 agent 跑了很多輪、讀了好幾個大檔，歷史無管理地累積：

```python
# 沒有任何 context 管理的 agent，跑了 30 輪、讀了 3 個大檔
# 第 31 輪呼叫時：
resp = client.messages.create(model="claude-opus-4-8", max_tokens=4096,
                              messages=huge_messages)   # huge_messages 已逼近 window
```

你可能會撞到（依模型而定）：

```
anthropic.BadRequestError: prompt is too long: 213000 tokens > 200000 maximum
```

或者更陰險的——還沒到硬上限，但 context 已經塞滿幾個大檔的內容，模型開始抓不準重點、被中間某段舊內容帶著走（context rot：長而雜的 context 拉低整體表現）。注意這跟「lost in the middle」的乾淨位置效應不完全一樣——你最新的指令放在結尾，理論上模型反而抓得住；這裡更像是大量雜訊與彼此衝突的舊指令一起干擾了它。總之你以為是模型笨了，其實是你把它的桌子堆爆了。

**先量化、先預算，不要等撞牆。** 在迴圈裡定期用 `count_tokens` 或累加 `usage` 監看用量，逼近某個門檻就觸發壓縮（Ch 13）——這是負責任 agent 的標準做法。

## 踩雷集錦

1. **以為 context 大就一定好**：錯。成本、延遲、context rot 三個理由都指向「精簡優於塞滿」。目標是高訊噪比，不是高資訊量。
2. **用「字元數 / 4」當精確值做決策**：那是估量級用的。要決定「該不該壓縮」「會不會超 window」這種決策，用 `count_tokens` 的精確值。中文尤其不能用 /4 估。
3. **忘了 input 和 output 共用 window**：把 input 塞到逼近上限，模型就沒空間生成回應。永遠替 output 預留空間。
4. **以為 token 成本是線性的**：agent 因為每輪重送全部歷史，input 成本是 **O(N²)** 的。這是帳單失控的頭號元兇，也是 context 管理對 agent 是生死問題的根本原因。
5. **input 和 output 用同一單價估成本**：兩者單價通常不同（output 較貴）。要分開算。
6. **撞牆才處理**：等 `model_context_window_exceeded` 或 `prompt is too long` 才反應就太晚了。要主動監看用量、提前觸發管理。

## 進階：再往深一層

- **不同模型、不同 window 大小**：context window 是模型規格的一部分，不同模型差很多。選模型時，window 大小、input/output 單價、速度都要一起考慮——一個 window 大但貴的模型，未必比「window 小但你好好管理 context」划算。Ch 37（成本優化）、Ch 40（框架/模型對比）會回到這個取捨。
- **`count_tokens` 也有使用考量**：它**不產生 inference / output 成本**（不會真的生成回應），所以比 `create` 便宜快很多——但它仍是一個 API 請求、有自己獨立的速率限制與請求開銷，不是「想呼叫幾次都沒差」。別在迴圈裡每輪都無腦呼叫它做精算；通常的做法是用便宜的本地估計監看、逼近門檻時才用 `count_tokens` 精算確認。把「便宜估計」和「精確計算」分層使用。
- **token 化對「非英文」與「程式碼」的影響**：中文、日文、以及 JSON/程式碼這類符號密集的內容，token 效率跟純英文差很多。一段你覺得「不長」的中文或一塊 JSON 工具結果，token 數可能比你預期高不少。這是為什麼 Ch 16（裁剪工具結果）對「回傳大量結構化資料」的工具特別重要。

## 動手練習

1. 用 `count_tokens` 數三段內容的 token：(a) 一句英文、(b) 一句等長的中文、(c) 一小塊 JSON。比較它們的 token 數，體會「中文和符號比英文吃 token」。
2. 拿你練習 A 的 mini-agent，在 `chat()` 每輪後印出 `resp.usage.input_tokens`，跑一個會用好幾次工具的任務，把每輪的 input_tokens 記下來、加總。對照「如果是線性會是多少」，親眼看到那條 O(N²) 曲線。
3. 估算一筆成本：查你用的模型的 input/output 單價，拿上一題的總 input_tokens 和總 output_tokens，分開算出這個任務大概花了多少錢。對「多跑幾輪 = 多少錢」建立體感。
4. 故意餵 mini-agent 讀一個很大的檔案，然後連續問幾個跟那個檔無關的問題，觀察模型會不會開始「忘記」你最新問的、或抓著舊檔內容打轉——體會 context rot。

## 本章重點整理

- context window 是模型的固定大小「工作桌面」，input 和 output 共用它，是硬上限。
- token 是模型的基本單位；用 `count_tokens` 精確數、用 `usage` 看實際帳單；中文與符號比英文吃 token。
- agent 的 input 成本隨輪數呈 **O(N²)** 成長（每輪重送全部歷史），這使 context 管理對 agent 是生死問題而非優化。
- 「塞越多越好」是錯的：成本、延遲、context rot（lost in the middle）三個理由都指向「精簡優於塞滿」，目標是高訊噪比。
- 用「預算」思考：每輪 token 分給 system/歷史/工具結果/參考資料，並替 output 預留空間——Part 2 後面每章都在優化這張表的一格。

## 自我檢核

- [ ] 我能解釋為什麼 input 和 output 共用 context window，以及這對設 `max_tokens` 的影響
- [ ] 我會用 `count_tokens` 精確數一份請求的 token，也知道什麼時候用估計就好
- [ ] 我能推導為什麼 agent 的 input 成本是 O(N²)，並說出這為什麼是 context 管理的根本動機
- [ ] 我能說出「塞越多越好」錯在哪的三個理由，以及 context rot 是什麼
- [ ] 我能用預算框架說出一輪 context 的 token 分給了哪幾類，各對應 Part 2 哪一章

## 延伸閱讀

### 官方文件

- **[Anthropic — Token counting](https://docs.anthropic.com/en/docs/build-with-claude/token-counting)**
  - **讀哪裡**：`count_tokens` 的用法與它涵蓋的範圍（system、messages、tools 都算）。
  - **能學到什麼**：本章精算 token 的權威依據，以及它的限制與速率考量。
  - **前提知識**：本章看完即可。

- **[Anthropic — Models overview / Pricing](https://docs.anthropic.com/en/docs/about-claude/models)**
  - **讀哪裡**：各模型的 context window 大小、input/output 單價。
  - **能學到什麼**：把本章的「成本」算式填上真實數字；選模型時的 window/價格取捨。
  - **前提知識**：本章看完即可。

### 部落格 / 技術文章

- **[Effective context engineering for AI agents（Anthropic Engineering）](https://www.anthropic.com/engineering)** — Anthropic Engineering
  - **這篇說什麼**：把「context 是稀缺資源、要當預算經營」講成一套方法論；本章的「高訊噪比優於塞滿」正是它的核心主張。
  - **讀哪裡**：在 Engineering 索引頁找 context engineering 那篇；重點讀它論述「為什麼更多 context 不等於更好」的段落。
  - **為什麼值得讀**：它是整個 Part 2 的思想總綱，由模型的開發方寫的，現在讀能讓你後面每一章都有定位。

- **[Lost in the Middle: How Language Models Use Long Contexts](https://arxiv.org/abs/2307.03172)** — Liu et al.（TACL, 2023）
  - **核心貢獻**：用實驗證明模型對「放在 context 中間」的資訊掌握度，明顯低於開頭與結尾——本章 context rot 的學術依據。
  - **讀哪裡**：Abstract 與 Section 1、4 的圖；數學細節可略過。
  - **和本章的關聯**：它把「為什麼塞太多反而變差」從直覺變成可量測的現象；讀完你對「資訊擺放位置也是 context engineering」會更有感。

下一章我們從 context 的固定成本那一格切入：system prompt 怎麼寫，才能用最少的 token 把 agent 的角色、能力、邊界、行為準則交代清楚——它每一輪都送、又深刻影響行為，是高槓桿的一塊。

→ [Ch 11 System prompt 設計](./11-system-prompt-design.md)
