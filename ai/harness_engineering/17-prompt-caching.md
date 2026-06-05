# Ch 17 — Prompt caching

> **目標**：學會用 prompt caching 把 agent 的成本與延遲打一個大折扣。讀完你能說出快取的運作原理（前綴快取）、怎麼用 `cache_control` 設斷點、tools/system/messages 的快取順序、怎麼從 `usage` 讀命中率、TTL 與最小可快取大小的限制、以及為什麼前面幾章的「穩定前綴」「壓縮會破壞快取」都在這裡收斂。

> **環境**：Python 3.11、`anthropic` Python SDK（最新版）。本章把 Ch 10、Ch 11、Ch 12、Ch 13 散落的快取線索整合起來。

## 為什麼 prompt caching 是 agent 的剛需

回顧 Ch 10 那條讓人心驚的曲線：agent 每多跑一輪，就要把**整段越來越長的歷史當 input 重送**，input 成本是 O(N²)。而這些重送的內容裡，有一大塊是**每輪幾乎一字不差**的——system prompt、工具定義、以及前面已經定案的歷史。

prompt caching 正是為這個場景而生：**既然這些前綴每輪都一樣，為什麼每輪都要從頭重算一次？** 把它們在伺服器端快取起來，後續請求命中快取的部分，用**便宜很多**的價格計算（讀取快取的單價遠低於正常 input）。對一個跑很多輪、且前綴龐大（長 system prompt + 一堆工具）的 agent，這往往能把成本砍掉一大截、同時降低延遲。

這不是「錦上添花」的優化，對 production agent 它是**剛需**——因為 agent 的工作模式（反覆重送大前綴）剛好是 caching 效益最大的場景。

## 先建立直覺：快取是「劃重點的講義」

想像你每天上同一門課，老師每堂課都從第一頁開始重念整本講義。太浪費了。比較聰明的做法：老師把「每堂都一樣的前半本」影印好放著，每堂課直接從「今天新的部分」開始講，前半本要參照時翻印好的就行。

```
   沒有快取：每輪都從頭「重算」整個前綴
   輪1: [算 system][算 tools][算 歷史][算 新訊息]
   輪2: [算 system][算 tools][算 歷史][算 新訊息]   ← system/tools 又算一遍，浪費
   輪3: [算 system][算 tools][算 歷史][算 新訊息]   ← 又一遍...

   有快取：穩定前綴只「算一次」，後續「讀快取」
   輪1: [算並快取 system+tools+歷史][算 新訊息]
   輪2: [讀快取 ▓▓▓▓▓▓▓▓▓▓▓▓▓▓][算 新訊息]        ← 前綴用讀的，便宜又快
   輪3: [讀快取 ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓][算 新訊息]
```

關鍵字是 **前綴（prefix）**：快取是**從頭開始、連續相同的那一段**。只要某個位置之後的內容變了，那個位置之後就不能用快取（要重算）。這個「前綴」特性決定了快取的所有設計原則——**把不變的放前面，變的放後面**（Ch 11 已經劇透過）。

## 一、怎麼開：`cache_control` 斷點

要啟用快取，你得用 `cache_control` 在某個內容區塊（如某段 system block、某個 tool、某則 message）上掛一個**斷點（breakpoint）**，告訴 API「快取到這裡」。下面是最常見的做法——在 system block 上加 `cache_control`：

```python
resp = client.messages.create(
    model="claude-opus-4-8",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": LONG_STABLE_SYSTEM_PROMPT,        # 長、每輪都一樣的 system prompt
            "cache_control": {"type": "ephemeral"},    # ← 標記：快取到這裡
        },
    ],
    tools=TOOL_SCHEMAS,        # 工具定義也會被涵蓋進快取前綴（見下一節順序）
    messages=messages,
)
```

這個 `cache_control: {"type": "ephemeral"}` 就是一個**斷點（breakpoint）**：它告訴 API「從前綴開頭到這個區塊為止，快取起來」。下次請求只要這段前綴一字不差，就會命中快取。

> **斷點同時是「寫入點」也是「命中查找的起點」，但兩件事要分清楚。** (1) **cache write 只發生在斷點**：API 把「從前綴開頭到這個斷點」這段寫成一個 cache entry。(2) **命中時做 automatic prefix checking**：API 從你的斷點往前回看（約 20 個 block 邊界），找出「先前請求已經在某個斷點寫過、且與本次逐字相同」的最長前綴來命中。換句話說，lookback 只能命中**之前已被寫過的** cache entry，不會憑空快取斷點「後面」的內容。所以你還是要把斷點設在「想被快取的那段尾端」——只是不必每個前綴點都精算著標。

注意 `system` 這裡寫成 **block 陣列**而不是字串——這正是 Ch 11 進階提過的「system 可以結構化」。**要在 system 上掛 `cache_control` 斷點，就必須用 block 形式**（純字串的 system 沒有地方掛斷點）。

## 二、快取的順序：tools → system → messages

這是最多人搞錯的地方。Anthropic 計算「前綴」是固定順序的：

```
   前綴的組成順序（從頭到尾）：
   ┌─────────────┬─────────────┬──────────────────────┐
   │   tools     │   system    │      messages         │
   │ （工具定義） │ （系統提示） │   （對話歷史）         │
   └─────────────┴─────────────┴──────────────────────┘
   最穩定 ────────────────────────────────▶ 最常變
```

含義很重要：

- **工具定義在最前面**。所以如果你改了工具（加一個、改一個 description），由於 tools 是前綴的最開頭，它一變，**它之後的整段前綴**（system + messages）都無法命中舊快取、要全部重算。這是「別頻繁改工具 schema」的一個隱藏成本。
- **system 在中間**。Ch 11 說「別把動態值塞進 system」就是這個道理——system 在前綴的前段，一動它，它之後（messages）的快取全失效。
- **messages 在最後、最常變**。新的對話輪不斷往後加，前面定案的歷史是穩定前綴。

所以「把不變的放前面」在 API 層是**強制的順序**，不是建議。你能控制的是：把最穩定的東西（system、工具）寫穩定，並在適當位置設斷點。

## 三、讀 usage：到底有沒有命中

Ch 10 介紹過 `usage` 的快取欄位，現在它們派上用場。每次請求後看這兩個：

```python
print(resp.usage)
# Usage(input_tokens=120, output_tokens=85,
#       cache_creation_input_tokens=2400,   ← 這次「寫入」快取的 token（第一次，較貴）
#       cache_read_input_tokens=0)
```

- **`cache_creation_input_tokens`**：這次**寫入**快取的 token 數。第一次請求（快取還沒建立）會看到這個是大的——你在「建快取」，這次略貴（寫入單價通常比正常 input 略高一點）。
- **`cache_read_input_tokens`**：這次**命中快取、用便宜價算**的 token 數。後續請求應該看到這個變大——代表前綴成功命中。
- **`input_tokens`**：沒走快取、正常計價的部分（通常就是這輪新增的內容）。

判斷快取有沒有在工作，就看**第二次以後的請求 `cache_read_input_tokens` 是不是大的**。如果它一直是 0，代表你的前綴每次都在變（最常見原因：system 裡塞了動態值、或工具一直在改、或你壓縮了歷史——見第五節），快取根本沒命中。

```
   健康的快取（第二輪起）：
   cache_read_input_tokens = 2400  ← 大！前綴命中了
   input_tokens = 30               ← 只有新內容是全價

   壞掉的快取：
   cache_read_input_tokens = 0     ← 永遠 0，前綴每次都變，白設了
```

## 四、TTL 與最小大小：兩個現實限制

快取不是無限期、也不是什麼都能快取，有兩個限制要知道：

- **TTL（存活時間）**：快取有效期有限。預設的 ephemeral 快取存活時間是**約 5 分鐘**（每次命中會刷新這個計時）。也有較長（**約 1 小時**）的選項可用——在 `cache_control` 裡指定 `{"type": "ephemeral", "ttl": "1h"}`，但寫入成本較高（見下面成本那段）。含義：如果你的請求間隔超過 TTL，快取就過期了、下次要重建。對「使用者連續互動」的 agent，5 分鐘通常夠；對「偶爾跑一次」的場景，可考慮 1 小時 TTL，或接受每次重建。想知道某次寫入是 5 分鐘還是 1 小時的份額，可讀 `usage.cache_creation` 這個物件，裡面有 `ephemeral_5m_input_tokens` 與 `ephemeral_1h_input_tokens` 的拆分。
- **最小可快取大小**：太短的內容不值得快取，API 有一個**最小 token 門檻，而且依模型而異**：
  - Claude Opus 4.5 / 4.6 / 4.7、Haiku 4.5：**4096 token**
  - Haiku 3.5：**2048 token**
  - 其餘支援模型：**1024 token**

  你的 system prompt 如果低於對應門檻，設了 `cache_control` 也不會真的快取。所以快取對「大前綴」才有意義——這也呼應為什麼 agent（長 system + 一堆工具）是快取的理想受益者。**確切數字以官方文件為準、會隨模型更新調整。**

> 這兩個限制解釋了一個常見困惑：「我設了 cache_control 怎麼沒省到？」——可能是內容太短（沒達門檻）、或請求間隔太久（TTL 過期）、或前綴一直在變（沒命中）。三個都要排除。

## 五、收斂：前面幾章的快取線索都在這裡會合

prompt caching 不是孤立的一章，它是前面好幾章的「為什麼」的答案。現在把它們串起來：

- **Ch 11「system prompt 寫穩定」**：因為 system 在快取前綴的前段，一動它（尤其塞動態值如時間）就讓後面的快取全失效。穩定的 system = 可快取的 system = 省錢。
- **Ch 11「動態值別放 system 本體，要放就分段設斷點」**：現在你懂機制了——把斷點設在「動態值之前的穩定段」，那段穩定前綴照樣命中，只有動態值之後重算。
- **Ch 12 / Ch 13「壓縮會破壞快取」**：壓縮改寫了歷史的某一段，那段**之後**的前綴就變了、命中不了。所以壓縮省下未來 token 的同時，當下要付「重建被改動部分之後的快取」的代價——這是「別太頻繁壓縮」的隱藏成本。（注意：失效的只是「被改動點之後」的部分；如果壓縮只動 messages 後段，前面 tools/system 的較早斷點仍可命中，不是整份快取歸零。）
- **Ch 16「工具結果裁剪」**：如果你在歷史中段二次裁剪一個舊工具結果，同樣會破壞那之後的快取前綴。所以二次裁剪也要權衡：省的 token vs 破壞的快取。

一句話收斂：**快取獎勵「穩定的前綴」，懲罰「對前段的任何改動」。** Part 2 教的所有 context 操作，本質上都在這個張力裡權衡——縮小 context（省未來）vs 保住快取前綴（省當下）。理解這個張力，你就真正懂了 context engineering 的經濟學。

## 六、實務最佳實踐

把上面整合成可操作的準則：

1. **快取最穩定的大前綴**：在 system（或工具之後的第一個穩定點）設斷點。工具 + 長 system 是首要快取對象。
2. **不變的放前面、變的放後面**：API 的順序已經幫你排好（tools→system→messages），你要做的是**別在前段放會變的東西**。
3. **斷點可以設多個（最多 4 個）**：你可以在「穩定前綴」和「半穩定的歷史」之間各設斷點——API 允許**最多 4 個 cache breakpoint**，讓不同穩定程度的層級各自快取。進階用法，先掌握「一個斷點快取 system+tools」即可。
4. **權衡壓縮頻率**：壓縮破壞快取，所以別一有點長就壓（Ch 12）。讓快取在穩定期充分發揮，逼近門檻才壓。
5. **用 `usage` 監控命中率**：把 `cache_read_input_tokens` 記進 log（Ch 35 observability），定期確認快取真的在工作。命中率掉了，通常代表你不小心讓前綴變動了。
6. **別為快取犧牲正確性**：快取是優化，不是目的。如果為了「保住快取」而不敢更新該更新的 system 或工具，那是本末倒置。先做對，再省錢。

## 失敗示範：以為設了 cache_control 就會省，但前綴一直在變

最常見的「我設了快取怎麼沒效果」翻車。某人很開心地在 system 設了 `cache_control`，但他的 system prompt 長這樣（Ch 11 罵過的）：

```python
system=[{
    "type": "text",
    "text": f"你是助理。現在時間是 {datetime.now()}。",   # 💥 時間每輪都變
    "cache_control": {"type": "ephemeral"},
}]
```

他跑了五輪，每輪看 `usage`：

```
cache_creation_input_tokens = 5000   ← 每輪都在「建快取」（已超過門檻，確實有寫入）
cache_read_input_tokens = 0          ← 永遠 0，從來沒命中
```

每一輪都在重新建快取（還付了建快取的略高成本），卻從來沒命中過——因為那個 `datetime.now()` 讓前綴**每輪都不一樣**，快取系統認為「這是全新的前綴」。**他不但沒省到，還倒貼了建快取的成本。**

修法（Ch 11 講過）：把動態的時間移出 system 本體——放進 messages，或設計成一個工具讓模型需要時查。system 本體保持一字不差，`cache_read` 才會變大。**快取的第一鐵律：你想快取的那段，必須每次完全相同。**

## 踩雷集錦

1. **前綴裡藏動態值**：system 或工具定義裡有時間、隨機 id、使用者名這種每次變的東西，快取永遠 miss。想快取的段必須逐字不變。
2. **以為「零設定」就會快取**：不會，快取是 opt-in 的。你得主動打開——要嘛像本章這樣明確設 `cache_control` 斷點（要掛在 system 上就得寫成 block 陣列），要嘛用 top-level automatic caching（在請求頂層設 `cache_control`，由 API 自動把斷點放到最後一個可快取 block；不是所有平台都支援）。本章用 explicit block-level，因為它最能看清「快取邊界在哪」。重點是：什麼都不設，就什麼都不快取。
3. **快取太短的內容**：低於最小門檻（依模型而異，1024～4096 token）的內容設了也不快取。快取對大前綴才有意義。
4. **忽略 TTL**：請求間隔超過存活時間（預設約 5 分鐘）快取就過期。偶爾跑一次的場景可能每次都重建、沒省到。
5. **頻繁壓縮/改工具殺死快取**：壓縮歷史、改工具 schema 都會讓那之後的前綴失效。這些操作要跟「快取效益」一起權衡，別無腦頻繁做。
6. **不監控命中率**：設了快取卻不看 `cache_read_input_tokens`，根本不知道有沒有在工作。一定要監控。
7. **為快取犧牲正確性**：不敢更新該更新的東西只為保快取，本末倒置。快取是優化手段，正確性優先。

## 進階：再往深一層

- **多斷點與分層快取**：你可以設多個 `cache_control` 斷點，把 context 分成「永遠不變（tools+system）」「半穩定（早期已定案的歷史）」等層級，各自快取。當半穩定層偶爾變動時，只有它之後失效，最前面的核心前綴仍命中。這對長 agent 對話能榨出更多快取效益，但管理較複雜——先用單斷點，需要時再分層。
- **快取與 batch / 並發**：當你跑多個共享同一個大前綴的請求（例如 Part 4 的 multi-agent，多個 subagent 用同一份 system + 工具），快取效益會被放大。但有個時序陷阱：**cache entry 要等第一個請求的 response 開始後才可命中**——所以「真正同時」發出的平行請求，後面那些不會等到快取建好，往往各自又寫一次。實務做法是**先用一個請求 prewarm（或等第一個 response 開始）建好快取，再送其餘共享前綴的請求**去讀。設計 multi-agent 時，讓 subagent 共享可快取的前綴、並注意這個「先暖後並發」的時序，是個省錢技巧。
- **快取的成本模型（含具體倍率）**：以正常 input 單價為基準（1x），**讀取快取（`cache_read`）約 0.1x**（便宜很多）。**寫入快取（`cache_creation`）的倍率取決於 TTL**：5 分鐘 TTL 寫入約 **1.25x**（比正常 input 略貴一點），1 小時 TTL 寫入約 **2x**（明顯貴）。所以「寫略貴」只在 5 分鐘 TTL 成立；用 1 小時 TTL 時寫入是雙倍，要更確定這個前綴會被讀夠多次才划算。整體規律不變：快取在「寫一次、讀很多次」時才賺——這正好是 agent 多輪重送大前綴的模式。但如果一個前綴只用一次（建了就沒再命中），你反而虧了建快取的差價。判斷「值不值得快取」就看「這個前綴會被讀幾次」。（倍率為近似值，以官方 Pricing 為準。）
- **快取讓「長 system / 多工具」的成本顧慮降低**：沒有快取時，長 system prompt 和大量工具定義每輪都全價重送，會讓你不敢寫太長。有了快取，這些穩定的大前綴只在第一次付 **cache write 價**（5m 1.25x / 1h 2x）、之後每輪都用便宜的 cache read（0.1x）——這某種程度上「解放」了你對 system/工具長度的成本焦慮（但 Ch 10 的 context rot、注意力稀釋問題仍在，所以還是別亂塞——快取省的是錢，救不了訊噪比）。

## 動手練習

1. 拿一個夠長（上千 token）的 system prompt，跑兩次相同的請求，第一次和第二次都印 `usage`。確認第一次 `cache_creation_input_tokens` 大、第二次 `cache_read_input_tokens` 大——親眼看到快取從「建」到「命中」。
2. 重現失敗示範：在 system 裡塞 `datetime.now()`，跑五次，確認 `cache_read_input_tokens` 一直是 0。再把時間移出 system，確認它變大。
3. 把你 mini-agent 的 system + 工具加上 `cache_control`，跑一個多輪任務，每輪記錄 `cache_read_input_tokens`，估算快取幫你省了多少（用 Ch 10 的成本算式，把命中的部分用便宜的快取讀取價算）。
4. 觸發一次 `compact`（Ch 13）後，觀察下一輪的 `cache_read_input_tokens` 是不是掉下來了——親眼看到「壓縮破壞快取」這個 Part 2 反覆提的張力。
5. （思考）你的 agent 如果使用者常常隔很久才回一句（超過 5 分鐘 TTL），快取還幫得上忙嗎？這種場景該怎麼權衡？

## 本章重點整理

- prompt caching 把每輪重送的穩定前綴快取起來、用便宜很多的價格算——對「多輪重送大前綴」的 agent 是剛需，不是錦上添花。
- 快取的是**前綴**（從頭連續相同的那段）：一旦某位置之後變了，那之後就不能用快取。所以「不變的放前面」是鐵律。
- 用 `cache_control: {"type":"ephemeral"}` 設斷點；system 要寫成 block 陣列；前綴順序是 **tools → system → messages**。
- 看 `usage`：`cache_creation_input_tokens`（建快取，第一次）、`cache_read_input_tokens`（命中，便宜）——第二輪起 `cache_read_input_tokens` 該變大，一直是 0 代表前綴在變；想看 5m/1h 拆分讀 `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`。
- 限制：TTL 約 5 分鐘（可選約 1 小時，寫入較貴）、有最小可快取門檻（依模型 1024～4096 token）——太短或間隔太久都快取不到。
- 收斂：Ch 11（system 寫穩定）、Ch 12/13（壓縮破壞快取）、Ch 16（裁剪破壞快取）全在這裡會合——快取獎勵穩定前綴、懲罰對前段的改動，這是 context engineering 的核心張力。

## 自我檢核

- [ ] 我能用「劃重點講義」比喻解釋快取為什麼省錢，以及「前綴」是什麼意思
- [ ] 我能正確設一個 `cache_control` 斷點（含為什麼 system 要寫成 block 陣列）
- [ ] 我知道前綴順序是 tools→system→messages，以及改工具為什麼會讓整個快取失效
- [ ] 我能從 `usage` 的 `cache_creation_input_tokens` / `cache_read_input_tokens` 判斷快取有沒有在工作
- [ ] 我能說出快取的兩個限制（TTL、最小大小）以及「設了沒省到」的三個可能原因
- [ ] 我能解釋「壓縮/裁剪破壞快取」這個張力，以及它如何串起整個 Part 2

## 延伸閱讀

### 官方文件

- **[Anthropic — Prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)**
  - **讀哪裡**：`cache_control` 用法、automatic prefix checking、tools/system/messages 的前綴順序、最小可快取 token 數、TTL（5 分鐘 / 1 小時）、`cache_creation_input_tokens` / `cache_read_input_tokens` 計費，以及 `usage.cache_creation` 的 5m/1h 拆分。
  - **能學到什麼**：本章每個數字與行為的權威來源——最小大小、TTL、計價的精確值都以它為準（這些會隨模型/版本調整，務必查當前文件）。
  - **前提知識**：本章看完即可。

- **[Anthropic — Pricing](https://docs.anthropic.com/en/docs/about-claude/pricing)**
  - **讀哪裡**：cache write / cache read 相對於正常 input 的單價倍率。
  - **能學到什麼**：把本章「寫略貴、讀很便宜」的定性說法填上真實數字，算出你的 agent 用快取能省多少。
  - **前提知識**：本章成本那節 + Ch 10。

### 部落格 / 技術文章

- **[Prompt caching with Claude（Anthropic News）](https://www.anthropic.com/news/prompt-caching)** — Anthropic
  - **這篇說什麼**：prompt caching 的官方介紹，含適用場景（長 system、多輪對話、工具定義）與省下的成本/延遲量級。
  - **讀哪裡**：適用場景與 benchmark 那幾段。
  - **為什麼值得讀**：讓你對「快取到底能省多少、什麼場景最划算」有量化的感覺，呼應本章「agent 是理想受益者」的主張。

練習 B 把 Part 2 整合起來：你會給 agent 加上 context 壓縮（Ch 13）+ memory（Ch 14），並用快取（Ch 17）與量測（Ch 10、Ch 12）讓它在長對話下既不撐爆、又省錢。

→ [練習 B：給 agent 加上 context 壓縮 + memory](./practice-b-compaction-memory.md)
