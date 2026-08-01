# Ch 3 — 程式設計師怎麼理解程式

> **目標**：搞懂你的大腦讀 code 時到底發生什麼——工作記憶為什麼一下就爆、專家和新手差在哪、top-down 和 bottom-up 兩種理解策略何時生效。前兩章講「讀碼難、讀碼是逆向」是現象；這章講**機制**，給那些攻堅心法一個「為什麼有效」的底層解釋。認知科學不是雞湯，它直接推導出你該怎麼降負荷、怎麼練。

> **關於引用**：這章大量引用認知科學與程式理解研究（Miller、Cowan、Brooks、Pennington、Letovsky、Hermans）。我盡量給出可查證的出處與年份；有些是被廣泛接受的模型而非單一定論，我會標明。細節數字（如工作記憶容量）學界仍有爭論，我採主流估計並說明區間。

## 先建立直覺：三種記憶體，讀 code 時同時在跑

先給你一個心智模型（mental model）。把大腦當成一台有三層儲存的機器，讀 code 時三層同時運作：

```
 ┌───────────────────────────────────────────────────────────┐
 │ 長期記憶 LTM (long-term memory)                             │
 │  容量近乎無限、持久。存你會的一切：語法、pattern、          │
 │  演算法、你讀過的 codebase……「hash table 怎麼運作」在這   │
 └───────────────────────────────────────────────────────────┘
              ▲ 提取(retrieval)        │ 儲存(把新 pattern 練成長期)
              │                        ▼
 ┌───────────────────────────────────────────────────────────┐
 │ 短期記憶 STM (short-term memory)                            │
 │  容量小、幾秒就衰退。暫存你「剛剛看到」的字面：             │
 │  變數名、剛讀的那行                                         │
 └───────────────────────────────────────────────────────────┘
              │                        ▲
              ▼  送去加工               │ 結果
 ┌───────────────────────────────────────────────────────────┐
 │ 工作記憶 WM (working memory)  ← 讀碼的瓶頸就在這            │
 │  「處理器」。把 STM 的字面 + LTM 提取的知識拿來組合、       │
 │  推理。容量約 4 個 chunk。爆了就是「我剛追到哪忘了」        │
 └───────────────────────────────────────────────────────────┘
```

這三者的分工，Felienne Hermans 在《The Programmer's Brain》(Manning, 2021) 講得最清楚，也是這章的主要參考。**核心洞見一句話：讀 code 卡住，幾乎都是工作記憶（WM）過載，而不是你不夠聰明。** 上一章「補 context 把工作記憶擠爆」就是這裡。接下來把每一層拆開，導出實務啟示。

## 工作記憶的容量：約 4 個 chunk，不是 7

最有名的數字是 Miller 1956 的〈The Magical Number Seven, Plus or Minus Two〉——短期記憶約能記 7±2 個項目。這個數字被引用了七十年，但**要小心**：

- Miller 那篇某種程度是**修辭與綜述**，「7」他自己都說帶點戲謔（"magical"），不是嚴格的容量常數。
- 更近的 **Cowan 2001〈The Magical Number 4 in Short-Term Memory〉**主張，在**排除複誦（rehearsal）與 chunking 幫忙**的純粹注意力焦點下，容量其實只有**約 4 個 chunk**。也有研究給出 3–5 的區間。

學界對確切數字仍有爭論（會隨任務、材料浮動），但對讀碼，**採保守的「約 4 個 chunk」最有用**——它逼你認清工作記憶有多小。Hermans 在書中就採「同時處理 2–6 個元素就會吃力」這種保守估計。

關鍵在 **chunk 是什麼**。一個 chunk 不是一個字元、一個 token，而是**一個對你有意義的單位**。看這串：

```
C H U N K I N G
```

8 個字母 = 8 個 chunk？對完全不識英文的人是。對你是**1 個** chunk（"CHUNKING" 一個字）。同理，這段 C：

```c
for (int i = 0; i < n; i++) { sum += a[i]; }
```

對新手是十幾個要分別追蹤的元素（宣告 `i`、比較、遞增、索引、累加……工作記憶直接爆）；對你是**1 個 chunk**——「陣列求和」。你一眼打包，工作記憶只佔 1 格，剩下的容量拿去想別的。**這就是專家和新手的根本差距，下一節展開。**

## chunking：專家把一大段 pattern 打包成 1 格

這是整個認知科學裡對讀碼最關鍵的機制。經典來源是 De Groot 與 Chase & Simon 對西洋棋大師的研究：給大師看**真實對局**的棋盤幾秒，他能幾乎完整重建；給**隨機亂擺**的棋盤，他重建能力跟新手一樣爛。

結論震撼：大師的記憶力沒有比較好，他是把「一個常見的開局陣型」打包成**一個 chunk**存進 LTM。真實棋局是熟悉 pattern 的組合，他用少數幾個 chunk 就記住；亂擺的沒有 pattern，無從打包，只能一顆一顆記，於是跟新手一樣受工作記憶那 4 格限制。

**讀 code 一模一樣。** 給你這段 redis（`src/dict.c`）：

```c
dictEntry *dictAddRaw(dict *d, void *key, dictEntry **existing)
{
    void *position = dictFindPositionForInsert(d, key, existing);
    if (!position) return NULL;
    if (d->type->keyDup) key = d->type->keyDup(d, key);
    return dictInsertAtPosition(d, key, position);
}
```

一個資深 C 工程師讀這段，工作記憶裡大概只放**兩三個 chunk**：「找插入位置 → 沒位置(key已存在)就 return → dup key → 插入」。他把 `if (!position) return NULL` 打包成「找不到就早退」這個他看過幾千遍的 pattern（early-return guard），`if (d->type->keyDup) key = d->type->keyDup(...)` 打包成「可選的 hook 呼叫」。**每個 pattern 佔 1 格**，整段 5 行只吃掉他 3 格工作記憶，游刃有餘。

一個 C 新手讀同一段，每一行都是生的：`dictEntry **existing` 這個雙重指標是什麼？`d->type->keyDup` 是函式指標嗎？為什麼 `return NULL` ？——他沒有現成 chunk 可打包，只能一個 token 一個 token 追，**5 行就塞爆了 4 格工作記憶**，讀到第 4 行忘了第 1 行的 `position` 是幹嘛的。

同一段 code，同樣的眼睛，理解成本差十倍——差別**不在智力，在 LTM 裡 chunk 庫的大小**。這給你一個殘酷但可操作的結論：

> 讀碼變快的唯一長期途徑，是**擴充你 LTM 裡的 chunk 庫**——見過越多 pattern、演算法、慣用法、codebase，你能一眼打包的越多，工作記憶越省，讀得越快。這是為什麼「多讀 code」真的會讓你變強，而且是複利。

## beacon：洩漏功能的關鍵標記

chunk 是你腦中打包好的知識；**beacon（信標）** 是 code 裡觸發你提取正確 chunk 的線索。Brooks（1983）提出這概念：beacon 是「一眼就暗示某段 code 在幹嘛」的刻板標記，讓你快速形成或驗證假設。

beacon 有兩類：

- **語意 beacon**：好的命名、關鍵註解。redis 的 `lookupKeyRead` / `lookupKeyWrite` / `lookupKeyReadOrReply`（`src/db.c`）——這三個名字是強 beacon：`lookup` 說是查詢、`Read`/`Write` 說讀寫意圖不同、`OrReply` 說它會順便回覆 client。你根本沒讀 body 就已經對它們的職責形成準確假設。這正是上一章「命名恢復語意」的認知學解釋——**命名之所以是線索，是因為它是 beacon，觸發你 LTM 裡的 chunk。**

- **語法/結構 beacon**：`swap` 三行、`for` 迴圈搭累加器、`if (!ptr) return`、`while ((n = read(...)) > 0)`——這些刻板結構本身就是 beacon，你看到形狀就認出 pattern。

beacon 的實務意義：**讀陌生 code 先掃 beacon**（函式名、關鍵字串、明顯的結構），用它們快速搭出「這裡大概在幹嘛」的假設骨架，再挑要緊的地方驗證。這是 Ch 4「掃讀」和 Ch 10「假設驅動」的認知基礎。反過來——**當你為別人（或未來的自己）寫 code，好命名就是在幫讀者埋 beacon**，這也是為什麼爛命名（Ch 30）讀起來特別痛：beacon 壞了，你被迫回退到逐 token 的新手模式。

## mental model 與 plan knowledge：你腦中那張「它怎麼運作」的圖

讀懂一段 code，最終產物是腦中一個 **mental model（心智模型）**——關於「這個系統/這段 code 怎麼運作」的內在表徵。它不是 code 的複本，是抽象化的「它做什麼、資料怎麼流、狀態怎麼變」。你能不能跟人講清楚一段 code，取決於你有沒有建起準確的 mental model（Ch 36「費曼測試」就是拿它來檢驗）。

**plan knowledge（計畫知識）** 是另一個關鍵概念（Soloway & Ehrlich 等人的研究）：程式設計有一堆刻板的「解法模板」——「用一個 flag 追蹤是否找到」「用哨兵值標記結尾」「先蒐集再排序」。這些 plan 存在你 LTM 裡。讀 code 時你不斷拿眼前的 code 去比對已知的 plan：「喔這是 accumulator plan」「這是 sentinel search」。認出 plan = 一次高效 chunking。

這解釋了一個現象：**讀你熟悉領域的 code 快得多**——不是語法比較簡單，是你有那個領域的 plan 庫。一個寫過 event loop 的人讀 redis 的 `aeMain` 秒懂，因為他 LTM 裡有「event loop plan」；沒寫過的人得從 `epoll_wait` 一行行重建。這也預告了 Ch 3 的實務啟示：**刻意累積特定領域的 plan/chunk，是專精某類 codebase 的捷徑。**

## 兩種理解策略：top-down vs bottom-up（務必記住這組）

程式理解研究最核心的成果，是發現人讀 code 用兩種相反方向的策略，何時用哪種取決於**你有多少相關先備知識**。這組區分是整門課讀碼策略的認知地基，務必吃透。

### top-down（Brooks 1983）：有領域知識時，假設驅動

當你**對這類程式已經熟悉**（讀過類似的、懂這個 domain），你不從細節開始。你**先在腦中生成一個高層假設**（「這是個網路伺服器，那它應該有：監聽 socket、accept loop、命令解析、派發」），然後把假設**逐層細分**成子假設，最後才下到 code 去找 beacon **確認或推翻**。

Brooks 的模型：理解是「生成假設 → 找 beacon 驗證 → 假設對就往下細分，錯就換假設」的過程。**你不是在讀 code，你是在拿 code 驗證你預先想好的結構。** 這正是 Ch 2 講的 RE 攻堅——逆向工程師逆一個他猜是「授權檢查」的程式，就是 top-down：先假設有授權邏輯，再去 code 裡找證據。

top-down 極快，因為你大部分 code 根本不讀——假設對了就跳過。前提是**你得有那個先備知識來生成靠譜的假設**。

### bottom-up（Pennington 1987）：陌生時，逐塊往上建

當你**對這類程式很陌生**（沒背景、看不出這是什麼），你沒有假設可生成，只能反過來：**逐行讀 code，把相鄰的行 chunk 成小單位（microstructure），再把小單位組成大結構（macrostructure）**，慢慢往上建出理解。Pennington 區分了兩層模型：先建「程式怎麼運作」的 program model（控制流層面），再建「它在真實世界解決什麼」的 situation model（領域層面）。

bottom-up 慢、費工作記憶（你在做大量現場 chunking），但當你**沒有先備知識時，這是唯一的路**。這對應 Ch 2 那句「陌生時逐行建 chunk」，也是新手讀 `dictAddRaw` 的處境。

### Letovsky（1986）：真實情況是兩者交織

別把 top-down / bottom-up 當成互斥的兩派。**Letovsky 的整合模型（integrated / systematic model）** 更貼近真實：熟練的讀者**在兩者間流暢切換**。他描述三個成分——一個編碼背景知識的 knowledge base、一個當前理解的 mental model、一個用 knowledge base 去豐富 mental model 的 assimilation 過程。實務長這樣：

```
 你讀一個陌生 codebase 的真實過程（Letovsky：機會主義地切換）
 
 top-down: 「這是 KV store，應該有 GET/SET 派發」  ← 用先備知識生成假設
     │  去找 beacon 驗證……
     ▼
 撞到 dictAddRaw 完全看不懂（沒有相關 chunk）
     │  切成 bottom-up
     ▼
 bottom-up: 逐行讀 dict.c，把 rehash / 開放定址 chunk 起來  ← 現場建 chunk
     │  建起「dict 怎麼運作」的局部 model
     ▼
 有了新 chunk，跳回 top-down: 「原來 SET 就是往這個 dict 塞」 ← 回到假設驅動
```

**高手不是只會其中一種，是知道何時切換**：有背景就 top-down 猛跳、假設驅動、大膽跳過；一撞到知識盲區（沒 chunk 可用）就切 bottom-up 老實逐行建，建完再跳回 top-down。這個「機會主義切換」的能力，是這門課 Part 2 攻堅 SOP 要練成肌肉記憶的東西。

## 實務啟示：認知科學直接推導出的三件事

理論不是拿來背的。上面每個機制都推出一個具體動作：

### 1. 主動降低工作記憶負荷 → 外化

既然工作記憶只有約 4 格、一 lookup 就爆，那就**別把東西全塞腦裡**。把它外化（externalize）到腦外：

- **記筆記**：把「重建出來的 context」（`flags` 的每個 bit 意義、三態差別）寫在旁邊，讀到就查，不佔工作記憶。
- **畫圖**：call graph、state machine、data flow 畫成圖，圖是外部工作記憶，你的腦只需在圖上移動注意力。
- **重命名/加註解**：讀懂一個爛名 `x` 就地改成 `retry_count`（在你自己的 branch），把你的理解固化進 code，下次不用重新推。

這是 Ch 35「外化理解」整章的認知學依據。**「腦中讀不算讀」不是雞湯，是工作記憶容量的物理限制。**

### 2. 刻意累積 chunk 庫 → 讀得多、讀得雜

既然讀碼速度 = LTM 裡 chunk 庫大小，那**變快沒有捷徑，只有累積**：多讀不同 codebase、多看不同 pattern、把常見演算法/慣用法/資料結構練到一眼認出。這是複利——你認識的 pattern 越多，讀新東西時能打包的越多，累積越快。這也是為什麼這門課逼你「每章 clone 真實 repo 動手」：不動手就不長 chunk。

### 3. 判斷該用 top-down 還是 bottom-up → 誠實評估先備知識

拿到陌生 code，先問自己：**「我對這類程式有多少背景？」**

- 有背景（讀過類似的）→ 走 top-down：先生成結構假設，拿 code 找 beacon 驗證，大膽跳過。
- 沒背景 → 走 bottom-up：老實挑一小塊逐行建 chunk，先別急著看全貌。
- 過程中隨時切換：撞到知識盲區就 bottom-up 補，補完跳回 top-down。

不做這個判斷，最常見的錯是**明明沒背景卻硬 top-down**（假設全錯，越讀越歪），或**明明有背景卻苦哈哈 bottom-up**（逐行讀你其實一眼能打包的東西，慢死）。

## 對比與取捨

| 維度 | top-down（Brooks） | bottom-up（Pennington） |
|---|---|---|
| 前提 | 有相關先備知識/領域經驗 | 陌生、無背景 |
| 方向 | 高層假設 → 細分 → 找 beacon 驗證 | 逐行 → chunk 成小結構 → 組成大結構 |
| 速度 | 快（大量跳過） | 慢（現場 chunking，費工作記憶） |
| 風險 | 假設錯會整個讀歪 | 見樹不見林，久久拼不出全貌 |
| 工作記憶負擔 | 低（用假設剪枝） | 高（每塊都要現場打包） |
| 對應 RE | 「我猜這是授權檢查」直奔目標 | stripped 函式從頭一條條啃 |
| 何時用 | 熟悉的 domain、標準架構 | 完全陌生的領域/演算法/爛 code |

整合（Letovsky）：**兩者不是二選一，是隨知識邊界機會主義切換**——這才是高手的實況。

## 踩雷集錦

1. **錯誤直覺**：「我讀 code 老是讀到後面忘了前面，是我記性差/不夠專心。」
   **正確認識**：那是工作記憶（約 4 chunk）被塞爆的正常生理現象，不是缺陷。任何人塞超過容量都會這樣。解法不是「更專心」（沒用），是**外化**——把東西寫下來畫出來，卸載到腦外，騰出工作記憶。

2. **錯誤直覺**：「大師/資深工程師是記憶力/智力比我強，所以讀得快。」
   **正確認識**：Chase & Simon 的西洋棋研究打臉這個——大師記亂擺棋盤跟新手一樣爛。他快是因為 **LTM 裡 chunk 庫大**，能把大段 pattern 打包成 1 格，不是工作記憶比較大。好消息：chunk 庫是**可以練的**，這是後天技能不是天賦。

3. **錯誤直覺**：「讀陌生 code 就該老老實實從頭逐行讀（bottom-up）才踏實。」
   **正確認識**：只有在你**真的沒背景**時 bottom-up 才是對的。如果你對這類程式有經驗，硬逐行讀是浪費——你該 top-down 生成假設、大膽跳過。反過來也錯：沒背景硬 top-down 會假設全錯讀歪。**先誠實評估先備知識，再選策略。**

4. **錯誤直覺**：「命名/註解只是 code 的裝飾，不影響理解難度。」
   **正確認識**：命名是 **beacon**——它觸發你 LTM 裡的正確 chunk。好命名讓你 top-down 秒認、壞命名逼你退回 bottom-up 逐 token 啃。這是爛 code（Ch 30）讀起來慢十倍的認知機制，不是「風格問題」。

## 進階：再往深一層

- **認知負荷的三種類型**：教育心理學（Sweller 的 cognitive load theory）把負荷分成 intrinsic（問題本身的固有難度）、extraneous（呈現方式造成的多餘負荷，如爛命名、糟排版）、germane（用於建立 schema 的有效負荷）。讀碼策略的很大一部分，是**砍掉 extraneous 負荷**（外化、重命名、畫圖）好把工作記憶留給 intrinsic。Hermans 書中對此有展開，值得深挖。

- **spacing effect 與 chunk 的固化**：你今天讀懂一個 pattern 不代表它進了 LTM——沒有間隔複習，chunk 會流失。真正把讀碼經驗轉成長期能力，需要間隔重複地再遇到同類 pattern（這也是為什麼「讀過一次的 codebase 隔月再讀」收穫巨大）。這連到 Ch 35/36 的外化與費曼測試——外化的筆記正是你日後複習、固化 chunk 的材料。

- **專家的「去技能化」風險**：chunk 是雙刃劍。太熟的 pattern 會讓你**自動打包而不細看**，於是漏掉「這次 pattern 裡藏了個 bug」——安全研究上這是找洞的機會（作者的自動化思維有盲點），也是你 review 時的陷阱（Ch 33）。高手會在關鍵處**刻意退出自動 chunking**，切回慢速逐行，正是為了不被自己的 chunk 騙過去。

## 動手練習

1. **測自己的 chunk 邊界**：在 redis 挑三段陌生 code——一段你**秒懂**（如 `dictAddRaw` 若你熟 C）、一段**要想一下**、一段**完全生**（如 `src/ziplist.c` 或 `src/t_stream.c` 的某段）。對每段誠實記錄：你把它打包成幾個 chunk？哪裡工作記憶開始吃力？這讓你摸清自己 chunk 庫的邊界在哪、下一步該補什麼。

2. **強迫 top-down**：打開一個你**有背景**的 redis 子系統（如果你懂網路，就 networking.c）。**先不讀 code**，寫下你假設它「應該有哪些函式、怎麼分工」。然後才去 code 找 beacon 驗證。統計你猜中幾成——猜得越準，代表你這領域的 plan 庫越厚，top-down 越省力。

3. **強迫 bottom-up 並外化**：挑一段你**完全沒背景**的（如 `src/rax.c` 基數樹）。老實逐行 bottom-up，但**全程外化**：邊讀邊在紙上畫資料結構、記下每個變數的意義。讀完對照「有畫 vs 純腦中讀」的差別——你會親身體會外化如何救回被塞爆的工作記憶。

4. **抓 beacon**：用 `rg -n "^robj \*lookupKey" src/db.c` 列出 `lookupKey` 家族。光看名字（不讀 body），寫下每個的職責假設，再開 body 驗證。體會「命名作為 beacon」讓你不讀實作就形成準確假設。

## 本章重點整理

- 三層記憶體：**LTM**（近乎無限，存知識/chunk）、**STM**（暫存剛看到的字面）、**WM 工作記憶**（處理器，約 **4 個 chunk**，讀碼瓶頸在此）。讀碼卡住幾乎都是 WM 過載，不是智力問題。
- **chunk** 是「對你有意義的單位」；**chunking** 是專家把大段 pattern 打包成 1 格的能力。專家 vs 新手的差距在 **LTM 的 chunk 庫大小**，不在記憶力（西洋棋研究：大師記亂擺棋盤跟新手一樣爛）。
- **beacon**（Brooks）是 code 裡觸發正確 chunk 的線索：好命名、關鍵註解、刻板結構。命名之所以是線索，因為它是 beacon。
- 兩種策略：**top-down**（Brooks，有背景時假設驅動、快、會讀歪）vs **bottom-up**（Pennington，陌生時逐行建 chunk、慢、費 WM）；**Letovsky** 整合模型——高手在兩者間**機會主義切換**。
- 三個實務啟示：**外化**（降 WM 負荷，腦中讀不算讀）、**累積 chunk 庫**（讀碼變快的唯一長期途徑，複利）、**先評估先備知識再選 top-down/bottom-up**。

## 自我檢核

- [ ] 不看筆記，畫出 LTM/STM/WM 三層及其分工，並說明「讀到後面忘了前面」發生在哪一層、為什麼。
- [ ] 工作記憶容量的「7」和「4」分別出自誰、差在哪？為什麼讀碼採「約 4」比較有用？
- [ ] 用西洋棋大師的研究，解釋「專家讀 code 快」的真正原因——並說明這對你（想變快）意味著要做什麼。
- [ ] 面試情境：「你拿到一個陌生子系統，怎麼決定要逐行讀還是先猜結構？」——用 top-down/bottom-up + 先備知識判斷來答。
- [ ] beacon 是什麼？舉一個 redis 的例子，說明好命名如何讓你不讀 body 就形成假設。
- [ ] 認知科學推出的三個實務動作是什麼？各對應哪個機制？

## 延伸閱讀

- **Felienne Hermans,《The Programmer's Brain: What Every Programmer Needs to Know about Cognition》(Manning, 2021), Part 1（Ch 1–4）**
  - **讀哪裡**：Part 1 講 LTM/STM/WM、chunking、beacon、confusion 的四種類型。這章的骨架就來自這裡。
  - **學到什麼**：把本章所有概念用大量 code 例子講透，還給你「如何刻意練 chunk」的具體方法。
  - **和本章關聯**：本章的主要來源與最佳延伸，讀完這章直接接它。

- **Nelson Cowan,〈The Magical Number 4 in Short-Term Memory: A Reconsideration of Mental Storage Capacity〉(Behavioral and Brain Sciences, 2001)**
  - **讀哪裡**：不必啃全文（很長且技術）。看 Abstract 與結論，理解「為什麼是 4 不是 7」的論證方向。
  - **學到什麼**：工作記憶容量的當代修正，以及「chunk 才是計數單位」這個對讀碼最關鍵的點。
  - **和本章關聯**：本章「約 4 個 chunk」數字的出處；也讓你有底氣不盲信流傳的「7±2」。

- **Ruven Brooks,〈Towards a Theory of the Comprehension of Computer Programs〉(International Journal of Man-Machine Studies, 1983)**
  - **讀哪裡**：top-down 假設驅動理解與 beacon 概念的原始論述，看它怎麼定義「假設 → beacon 驗證」的循環。
  - **學到什麼**：top-down 模型的第一手來源；配合 Pennington（1987, bottom-up）與 Letovsky（1986, 整合）一起讀，三篇構成程式理解理論的經典三角。
  - **和本章關聯**：本章「兩種策略」那節的理論根，也是 Ch 10 假設驅動讀碼的學術地基。

大腦怎麼理解 code 的機制清楚了。下一章把這些機制落地成**三種可操作的閱讀模式**——掃讀、精讀、追蹤——並教你在它們之間怎麼切換。你會發現這三種模式正好對應本章的三件事：掃讀是快速鋪 beacon 搭 top-down 假設、精讀是對一小塊做 bottom-up chunking、追蹤是沿 data/control flow 建 program model。

→ [Ch 4 三種閱讀模式：掃讀 / 精讀 / 追蹤](./04-three-reading-modes.md)
