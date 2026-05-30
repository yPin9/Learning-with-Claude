# Ch 1 — LLM 運作原理

> **目標**：能解釋 tokenizer → embedding → attention → sampling 的完整流程；理解 temperature / top-p 對輸出的影響；知道為什麼 LLM 會「幻覺」；看出這些機制和安全攻擊面的關聯。

---

## 先建立直覺

LLM（Large Language Model，大型語言模型）的本質是一個**超大的條件機率預測器**。

給定一段文字 `"The capital of France is"`，LLM 做的事情是：計算詞彙表中每個 token 出現在下一個位置的機率，然後從中挑一個。`"Paris"` 的機率最高，所以它輸出 `"Paris"`。

注意：它不是在「查詢事實」，也不是在「理解語意」。它是在做**統計預測**——根據訓練資料中看過的大量文本，學到了 `"The capital of France is"` 後面最常接 `"Paris"` 這個 pattern。這個區別貫穿整門課，因為 LLM 的大部分安全問題都源自人們把「統計預測」誤解為「理解」。

整個流程可以拆成四個階段：

```
輸入文字
   │
   ▼
┌──────────┐
│ Tokenizer │  文字 → token ID 序列
└──────────┘
   │
   ▼
┌───────────┐
│ Embedding  │  token ID → 高維向量
└───────────┘
   │
   ▼
┌───────────────────────┐
│ Transformer (Attention)│  向量序列 → 上下文感知的向量序列
│ × N layers             │
└───────────────────────┘
   │
   ▼
┌──────────┐
│ Sampling  │  最後一個位置的向量 → 下一個 token 的機率分佈 → 抽樣
└──────────┘
   │
   ▼
輸出 token（重複整個流程直到停止條件）
```

以下逐一拆解。

---

## Tokenizer：文字怎麼變成數字

### 為什麼不用 word-level？

最直覺的做法是把每個英文單字對應一個數字。問題：

1. **詞彙表爆炸**：英文光常用詞就有幾十萬個，加上專有名詞、拼字錯誤、各國語言，詞彙表會大到不可用
2. **未知詞（OOV）**：訓練時沒見過的詞只能標成 `<UNK>`，丟失所有資訊
3. **形態變化浪費**：`run`、`running`、`runs`、`runner` 被當作四個完全不同的 token，但它們共享語意

### Subword Tokenization

現代 LLM 用的是**次詞切割**（subword tokenization）：把文字拆成介於字元和單字之間的片段。

主流演算法有兩個：

- **BPE（Byte Pair Encoding）**：從字元開始，反覆合併最常出現的相鄰 pair，直到達到目標詞彙量。GPT 系列、Llama 用這個。
- **SentencePiece**：Google 開發，把整個輸入當成 byte 序列處理（語言無關），不需要先做空格切割。支援 BPE 和 Unigram 兩種模式。

### BPE 的運作直覺

假設訓練語料裡 `"lo"` 和 `"w"` 經常相鄰出現，BPE 會把它們合併成一個 token `"low"`。下次看到 `"lower"` 就會切成 `["low", "er"]` 而不是 `["l", "o", "w", "e", "r"]`。

結果是：

- 常見詞（`"the"`、`"is"`）會成為完整的單一 token
- 罕見詞（`"defenestration"`）會被拆成幾個 subword（`["def", "en", "est", "ration"]`）
- 任何文字都能被表示——沒有 OOV 問題

### Token 數不等於字數

```
英文: "Hello world"     → ["Hello", " world"]           = 2 tokens
中文: "你好世界"         → ["你", "好", "世", "界"]       ≈ 4 tokens
中文: "機器學習"         → ["機", "器", "學", "習"]       ≈ 4 tokens
```

中文在 BPE 詞彙表中的覆蓋率比英文低，所以同樣語意的中文文字會消耗更多 token。經驗法則：中文平均 1 個字 ≈ 1.5–2 個 tokens。這直接影響 API 費用和 context window 的可用長度。

### 動手看：用 Ollama 觀察 tokenization

Ollama 沒有直接暴露 tokenizer API，但你可以用 Python 的 `tiktoken`（OpenAI 的 tokenizer）來建立直覺：

```python
import tiktoken

enc = tiktoken.get_encoding("cl100k_base")  # GPT-4 用的 tokenizer

text = "Prompt injection is a security vulnerability."
tokens = enc.encode(text)
print(f"Text: {text}")
print(f"Token IDs: {tokens}")
print(f"Token count: {len(tokens)}")
print(f"Decoded tokens: {[enc.decode([t]) for t in tokens]}")
```

不同模型的 tokenizer 不同（Llama 3 用自己的 BPE），但概念相通。

### 安全關聯：Tokenizer Bypass

因為 content filter 通常在 token 層或文字層做匹配，攻擊者可以利用 tokenizer 的行為來繞過：

- **Unicode 替換**：用視覺上相同但 token 不同的字元（例如全形字母替換半形）
- **插入零寬字元**：在敏感詞中間插入 zero-width space（U+200B），人眼看不到，但 tokenizer 會把詞切開
- **語言切換**：用其他語言表達敏感概念，因為多語言 tokenizer 對非英語語言的 token 覆蓋不同

這些在 Ch 7（Prompt Injection）和 Ch 8（Jailbreak）會深入實作。

---

## Embedding：Token 怎麼變成向量

Tokenizer 把文字轉成 token ID 序列（整數），但數字本身沒有語意資訊。`token_id=1234` 和 `token_id=1235` 之間沒有任何語意距離的概念。

Embedding layer 把每個 token ID 映射到一個高維向量（通常 768 到 4096 維）。這個映射是一張查詢表：token ID 是 index，對應的 row 就是那個 token 的向量。

```
token "king"  → [0.21, -0.43, 0.87, 0.12, ...]    （4096 維）
token "queen" → [0.19, -0.41, 0.85, 0.15, ...]
token "man"   → [0.52,  0.33, 0.11, 0.78, ...]
token "woman" → [0.50,  0.35, 0.09, 0.80, ...]
```

### 為什麼「king - man + woman ≈ queen」？

在訓練過程中，模型學會把語意相近的 token 放在向量空間中相近的位置。因為 `"king"` 和 `"queen"` 的關係類似 `"man"` 和 `"woman"` 的關係，所以：

```
vector("king") - vector("man") + vector("woman") ≈ vector("queen")
```

這不是人為設計的——是模型從大量文本中自動學到的。向量空間中的方向代表語意關係：「性別」是一個方向、「時態」是另一個方向、「國家-首都」又是另一個方向。

### Positional Encoding

Embedding 只知道每個 token 是什麼，但不知道它在句子中的位置。`"dog bites man"` 和 `"man bites dog"` 的 token embedding 一樣，但語意完全不同。

Positional encoding（位置編碼）在 embedding 上加入位置資訊。原始 Transformer 用正弦/餘弦函數：

```
PE(pos, 2i)   = sin(pos / 10000^(2i/d))
PE(pos, 2i+1) = cos(pos / 10000^(2i/d))
```

其中 `pos` 是位置、`i` 是維度 index、`d` 是向量維度。

現代 LLM（包括 Llama 3）改用 **RoPE（Rotary Position Embedding）**：用旋轉矩陣把位置資訊編碼進 attention 的 QK 內積中，讓模型能泛化到比訓練時更長的序列。

你不需要記住公式——重點是理解：**每個 token 最終的向量表示 = token embedding + 位置資訊**。

### 安全關聯：Embedding 空間的對抗式擾動

攻擊者可以在 embedding 空間中做微小的擾動（adversarial perturbation），讓模型的輸出劇烈改變。這是 Ch 13（對抗式機器學習）的核心主題。概念上類似影像領域的 adversarial examples：人眼看不出差別，但模型的判斷完全不同。

---

## Attention 機制：上下文怎麼影響理解

Embedding 後，每個 token 有自己的向量，但這些向量是孤立的——`"bank"` 在 `"river bank"` 和 `"bank account"` 裡的 embedding 一樣。模型需要一個機制讓每個 token「看到」其他 token，根據上下文調整自己的表示。

這就是 **Self-Attention**。

### Query-Key-Value 的直覺

把 attention 想成一個搜尋引擎：

- **Query（Q）**：「我在找什麼？」——每個 token 發出的搜尋查詢
- **Key（K）**：「我是什麼？」——每個 token 的標籤
- **Value（V）**：「我帶什麼資訊？」——每個 token 的實際內容

attention 計算的核心是：**每個 token 的 Query 和所有 token 的 Key 做比對，得到 attention weights（權重），然後用這些權重加權求和所有 token 的 Value**。

```
                              所有 token 的 Key
                              ┌─────────────────┐
每個 token 的 Query ──比對──→ │ attention weights │ ──加權──→ 新的向量表示
                              └─────────────────┘
                                     ↑
                              所有 token 的 Value
```

數學上：

```
Attention(Q, K, V) = softmax(QK^T / √d_k) · V
```

- `QK^T`：Query 和 Key 的點積，數值越大代表兩個 token 越「相關」
- `√d_k`：除以維度的平方根做縮放，防止點積值太大導致 softmax 飽和
- `softmax`：把分數轉成機率分佈（加起來 = 1）
- `· V`：用機率分佈加權求和 Value

### 完整計算流程

以 `"The cat sat"` 三個 token 為例：

```
輸入向量: [x_the, x_cat, x_sat]     （各 d 維）

            ┌─── × W_Q ──→ Q = [q_the, q_cat, q_sat]
            │
x_i  ──────┼─── × W_K ──→ K = [k_the, k_cat, k_sat]
            │
            └─── × W_V ──→ V = [v_the, v_cat, v_sat]

Attention matrix（3×3）:

         k_the  k_cat  k_sat
q_the  [  0.1    0.7    0.2  ]   ← "The" 最關注 "cat"
q_cat  [  0.3    0.1    0.6  ]   ← "cat" 最關注 "sat"
q_sat  [  0.2    0.5    0.3  ]   ← "sat" 最關注 "cat"

（以上數值是示意，真實值由學習得到）

Output:
out_the = 0.1 × v_the + 0.7 × v_cat + 0.2 × v_sat
out_cat = 0.3 × v_the + 0.1 × v_cat + 0.6 × v_sat
out_sat = 0.2 × v_the + 0.5 × v_cat + 0.3 × v_sat
```

每個 token 的輸出現在融合了它關注的其他 token 的資訊。`"bank"` 在 `"river bank"` 裡會因為關注 `"river"` 而偏向「河岸」的語意。

### Multi-Head Attention

一個 attention head 只能學一種「關注模式」。但語言中有多種關係需要同時關注——語法關係、語意關係、長距離指代、位置鄰近等。

Multi-head attention 的做法是：把向量空間拆成 `h` 個子空間（head），每個 head 獨立做 attention，最後把結果拼接起來。

```
head_1 = Attention(Q·W_Q1, K·W_K1, V·W_V1)   ← 可能學到語法關係
head_2 = Attention(Q·W_Q2, K·W_K2, V·W_V2)   ← 可能學到語意關係
...
head_h = Attention(Q·W_Qh, K·W_Kh, V·W_Vh)

MultiHead(Q,K,V) = Concat(head_1, ..., head_h) · W_O
```

Llama 3 的 3B 版本用 24 個 attention head，每個 head 的維度是 128，合起來 24 × 128 = 3072 維。

### Causal Masking

在文字生成中，LLM 在預測第 `i` 個 token 時，不能看到第 `i+1` 及之後的 token（因為那些還沒生成）。causal mask 把 attention matrix 的右上三角設成 `-∞`，softmax 後就是 0：

```
         k_1    k_2    k_3
q_1    [ 1.0   -inf   -inf ]   ← token 1 只看自己
q_2    [ 0.4    0.6   -inf ]   ← token 2 看 1 和自己
q_3    [ 0.2    0.5    0.3 ]   ← token 3 看所有
```

這就是為什麼 GPT 系列叫 **decoder-only** 模型——它只做 causal（因果）attention，每個 token 只看到左邊的歷史。

### 安全關聯：Attention 的可攻擊性

攻擊者可以利用 attention 機制的特性：

- **Attention hijacking**：在 prompt 中放入高度吸引 attention 的特殊 pattern（例如重複的指令、特殊符號），讓模型忽略系統 prompt 而關注攻擊 payload
- **Context window 搶佔**：在 RAG 的 retrieved documents 中塞入大量垃圾文字，搶佔 attention 資源，讓模型無法關注到正確的知識

---

## Transformer Block 全貌

一個 Transformer block 由以下元件組成：

```
輸入 x
   │
   ├──→ Layer Norm ──→ Multi-Head Attention ──→ + ←── x  (residual)
   │                                              │
   │                                              ▼
   │                              Layer Norm ──→ FFN ──→ + ←── (residual)
   │                                                      │
   └──────────────────────────────────────────────────────▼
                                                        輸出
```

- **Layer Norm**：正規化向量，穩定訓練
- **Residual Connection**：把輸入直接加到輸出上，讓梯度能直接流過深層網路
- **FFN（Feed-Forward Network）**：兩層全連接，中間用 SiLU 激活函數，是模型儲存「知識」的主要地方

Llama 3 的 3B 版本疊了 28 層這個 block。每一層的 attention 學不同層次的語言 pattern——低層偏語法，高層偏語意。

---

## Sampling：怎麼從機率分佈中挑 token

Transformer 的最後一層輸出一個向量，這個向量乘以 embedding matrix 的轉置，得到詞彙表中每個 token 的分數（logits）。softmax 後就是機率分佈。

接下來要從這個分佈中「挑」一個 token。挑法不同，生成的文字風格就不同。

### Temperature

Temperature（溫度）控制機率分佈的「尖銳程度」：

```
adjusted_logits = logits / temperature
probs = softmax(adjusted_logits)
```

| Temperature | 效果 | 使用場景 |
|-------------|------|---------|
| 0.0（實際接近 0） | 幾乎確定性——永遠選機率最高的 token | 事實查詢、程式碼生成 |
| 0.7 | 適度隨機，多數時候選高機率 token，偶爾有驚喜 | 一般對話 |
| 1.0 | 原始分佈，不做調整 | 基準對比 |
| 1.5+ | 高度隨機，低機率 token 也有機會被選到 | 創意寫作、brainstorming |

溫度越低，分佈越尖（最高機率的 token 佔比更大）；溫度越高，分佈越平（各 token 的機率差距縮小）。

### Top-p（Nucleus Sampling）

Top-p 從另一個角度控制隨機性：只從累積機率達到 `p` 的最小 token 集合中抽樣。

```
排序後的 token 機率: [0.35, 0.25, 0.15, 0.10, 0.05, 0.03, ...]

top_p = 0.9:
  0.35 + 0.25 + 0.15 + 0.10 + 0.05 = 0.90 ✓
  → 只從前 5 個 token 中抽樣，剩下的機率歸零
```

Top-p = 0.1：極端保守，幾乎只用機率最高的 1-2 個 token。
Top-p = 1.0：不過濾，等同不使用 top-p。

### Top-k

更粗暴的做法：只保留機率最高的 `k` 個 token，其餘歸零。

Top-k = 1 等同 greedy decoding（永遠選最高的）。
Top-k = 50 是常見設定。

### Temperature = 0 真的完全確定嗎？

一個常見誤解：設了 `temperature=0` 輸出就完全一致。實際上有幾個原因可能導致不同結果：

1. **浮點精度**：GPU 的浮點運算在不同 batch size 下可能有 rounding 差異
2. **Quantization noise**：量化後的模型（INT4/INT8）在不同推論框架裡的 rounding 策略不同
3. **並行計算的不確定性**：某些 GPU kernel 的 reduce 操作順序不保證一致

對安全測試的影響：如果你要做可重現的實驗，`temperature=0` 是必要條件但不充分——你還需要固定推論框架版本和硬體。

---

## 幻覺（Hallucination）

### 根本原因

LLM 在做 **next-token prediction**，不是在做**事實查詢**。

當模型遇到 `"The CEO of Apple in 2024 is"` 時，它根據訓練資料中的統計 pattern 預測下一個 token。如果訓練資料裡有足夠多的文本把 `"Tim Cook"` 和 `"CEO of Apple"` 關聯在一起，它就會輸出 `"Tim Cook"`。

但如果你問 `"The CEO of Anthropic in 2030 is"`，訓練資料裡沒有這個事實。模型不會說「我不知道」——它會根據 pattern 生成一個看起來合理的名字。這就是幻覺。

### 幻覺的三種類型

| 類型 | 描述 | 範例 |
|------|------|------|
| **事實幻覺** | 生成不存在的事實 | 「Anthropic 成立於 2015 年」（實際是 2021 年） |
| **忠實度幻覺** | 回答和提供的 context 矛盾 | RAG 提供的文件說 A，模型回答 B |
| **引用幻覺** | 捏造引用來源 | 「根據 Smith et al., 2023 的研究...」（論文不存在） |

### 為什麼這是安全問題？

1. **信任誤導**：使用者把幻覺當成事實，做出錯誤決策
2. **法律風險**：AI 生成的法律建議引用了不存在的判例
3. **攻擊向量**：攻擊者可以利用幻覺——例如在 RAG 中投毒，讓模型「忠實地」引用攻擊者捏造的虛假知識

---

## 完整流程：從輸入到輸出

把所有元件串起來，一次完整的 LLM 推論流程：

```
使用者輸入: "What is prompt injection?"

1. Tokenizer
   "What is prompt injection?" → [1024, 374, 10137, 28817, 30]
   （5 個 token IDs）

2. Embedding + Positional Encoding
   [1024, 374, 10137, 28817, 30] → 5 個 d 維向量，各自帶位置資訊

3. Transformer Blocks × 28 層
   每一層:
     - Multi-Head Attention: 每個 token 看到左邊的所有 token
     - FFN: 存取和組合知識
   經過 28 層後，每個向量融合了完整的上下文資訊

4. Output Layer
   取最後一個位置的向量 × embedding matrix 轉置
   → 詞彙表 128,256 個 token 各一個分數（logits）

5. Sampling
   logits / temperature → softmax → 機率分佈
   top-p/top-k 過濾 → 抽樣 → 選出下一個 token

6. 自回歸（Autoregressive）
   把新 token 接到輸入後面，重複步驟 1-5
   直到生成 <eos> 或達到 max tokens

最終輸出: "Prompt injection is a type of attack where..."
```

每生成一個 token 都要跑一次完整的 Transformer forward pass。這就是為什麼 LLM 的推論速度和序列長度正相關——越長的輸入+輸出，要跑越多次。

---

## 這些原理和安全的關聯

| 機制 | 攻擊面 | 對應章節 |
|------|--------|---------|
| Tokenizer（subword 切割） | Unicode bypass、零寬字元繞過 content filter | Ch 7, Ch 8 |
| Embedding（向量空間） | Adversarial perturbation、embedding space attack | Ch 13 |
| Attention（上下文關注） | Attention hijacking、context window 搶佔 | Ch 7, Ch 10 |
| Sampling（溫度/top-p） | 高 temperature 增加 jailbreak 成功率 | Ch 8 |
| 自回歸生成 | 逐 token 累積 bias，chain-of-thought 注入 | Ch 11 |
| 幻覺 | 捏造事實、引用洗白、RAG 投毒利用 | Ch 9, Ch 10 |

這張表是後面所有攻擊章節的預覽。每一種攻擊技術都建立在本章講的某個機制上——不是 LLM 設計有 bug，而是這些機制本身的數學性質被利用了。

---

## 踩雷集

### 「LLM 理解語言」

它不理解。它在做統計預測。一個 LLM 可以流暢地描述「火是熱的」，但它從未感受過溫度。這個區別在安全研究中至關重要：你不能靠「讓 LLM 理解攻擊是壞的」來防禦攻擊——你需要的是工程上的 guardrails。

### 「Temperature = 0 就完全確定」

前面講過了，不一定。quantization、浮點精度、GPU kernel 的並行計算都會引入 noise。做安全測試時，同一個 prompt 多跑幾次看結果的穩定性。

### 「Token 數 = 字數」

中文平均 1 字 ≈ 1.5–2 tokens。一個 4096 token 的 context window，塞中文大約只能放 2000–2700 個字。這在設計 RAG 的 chunk size 和 prompt template 時很重要。

### 「更大的模型一定更好」

不一定。對於安全測試（當靶子用），3B 模型和 70B 模型的攻擊面結構是一樣的——都有 tokenizer、都有 attention、都會幻覺。差別在於大模型可能對某些攻擊更 robust（因為 alignment 訓練更多），但這反而讓攻擊測試失去泛化性——你在 70B 上打不通的攻擊，可能在 3B 上打得通，而 3B 才是很多中小企業實際部署的大小。

---

## 動手實驗

### 實驗 1：觀察 temperature 的影響

```python
from langchain_ollama import OllamaLLM

prompt = "Write a one-sentence definition of artificial intelligence."

for temp in [0.0, 0.5, 1.0, 1.5]:
    llm = OllamaLLM(model="llama3.2:3b", temperature=temp)
    print(f"\n--- temperature={temp} ---")
    for i in range(3):
        response = llm.invoke(prompt)
        print(f"  Run {i+1}: {response[:100]}...")
```

觀察：`temperature=0` 的三次結果是否完全一致？`temperature=1.5` 的結果有多發散？

### 實驗 2：觸發幻覺

```python
from langchain_ollama import OllamaLLM

llm = OllamaLLM(model="llama3.2:3b", temperature=0)

prompts = [
    "Who is the author of the paper 'Adversarial Attacks on Neural Networks for Graph Data' published in KDD 2018?",
    "What is the population of the city of Zephyria?",
    "Summarize the key findings of the Smith et al. 2024 study on quantum computing security.",
]

for p in prompts:
    print(f"\nQ: {p}")
    print(f"A: {llm.invoke(p)[:200]}...")
    print("---")
```

第一個問題是真實論文（Zügner et al.），看模型能否答對。第二個是虛構城市。第三個是虛構論文。觀察模型在每種情況下的行為。

---

## 延伸閱讀

- **[Attention Is All You Need](https://arxiv.org/abs/1706.03762)** — Vaswani et al., NeurIPS 2017 — Transformer 的原始論文，一切的起點
- **[The Illustrated Transformer](https://jalammar.github.io/illustrated-transformer/)** — Jay Alammar — 視覺化解說 attention 機制，是最好的入門圖解
- **[Let's build GPT from scratch](https://www.youtube.com/watch?v=kCc8FmEb1nY)** — Andrej Karpathy, YouTube — 兩小時從零實作一個 GPT，看完你對每個元件的理解會從「知道」變成「會寫」
- **[A Survey of Large Language Models](https://arxiv.org/abs/2303.18223)** — Zhao et al., 2023 — LLM 的全景綜述，適合想深挖的人

---

→ 下一章：[Ch 2 — LangChain 核心](./02-langchain-core.md)
