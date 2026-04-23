# Ch 20 — Taint policy 設計：explicit vs implicit flow、over/under-tainting

> 目標：把 taint policy 的 design space 拆到位。每個決定都影響 FP/FN，搞清楚你才能選對策略、看報表時知道 alert 可信度。

## 三個 P：Precision、Performance、Policy

DTA 工程的鐵三角：

```
       Precision
           ┃
     ┌─────┴─────┐
     │   pick    │
     │   two     │
     └─────┬─────┘
           ┃
    Performance ─── Policy complexity
```

你不能三個都要。典型取捨：
- libdft：極快、基本 precision、policy 簡單
- Triton：中速、高 precision、policy 豐富
- TEMU / PANDA：慢、極高 precision、policy 極細

## Explicit flow：基礎情況

「資料直接流」的 case，毫無爭議：

```c
int x = tainted;
int y = x;              // taint(y) = taint(x)
int z = x + 5;          // taint(z) = taint(x)
int w = x * tainted2;   // taint(w) = taint(x) ∪ taint(tainted2)
mem[0] = x;             // taint(mem[0]) = taint(x)
int v = mem[0];          // taint(v) = taint(mem[0])
```

幾乎所有工具都同意這個。你寫 DTA 工具從這層起手。

### 運算的 taint rule

對 binary op `c = f(a, b)`：

```
taint(c) = taint(a) | taint(b)       # union (single-bit)
```

或對 label set：

```
taint(c) = taint(a) ∪ taint(b)
```

例外：**值不依賴於某 operand** 時不該傳 taint：

```c
int c = a & 0;          // c = 0, 不依賴 a
int c = a ^ a;          // c = 0, 不依賴 a
```

精確的 rule 要做 **value analysis**；成本高。實務多數工具**不**做這層優化，over-taint 了事。

## Pointer taint policy（memory）

最難的部分。

### Value-only policy

```c
mem[tainted_idx] = clean_val;
int v = mem[tainted_idx];
```

policy：**taint 只看 value，不看 address**。

- store：只看 value tainted，不看 idx
- load：只看 `mem[idx]` 的 value，不看 idx

特性：
- 少 false positive
- 漏抓「address 是 attacker-controlled」的漏洞（OOB、type confusion）

libdft 預設就是這個。dumb 但快。

### Address-taint load policy

```c
int v = mem[tainted_idx];
// 如果 tainted_idx 可控，那 v 也是 attacker-controlled
// → v 應該 tainted
```

policy：load 時如果 **address tainted**，load 的 value 也 tainted（即使 memory content 乾淨）。

特性：
- 抓 **OOB read**（attacker 控 idx → read 敏感 memory）
- FP 上升：很多 lookup table 的 index 來自輸入，但實際沒危害

部分工具（TEMU、IntelliDroid）採用。

### Address-taint store policy

```c
mem[tainted_idx] = clean_val;
// store 到哪了？不確定
// 所有可能的 mem[...] 都 tainted 嗎？
```

policy：store 時如果 **address tainted**，把所有（可能的）target 位置都標 tainted。

特性：
- 抓 **OOB write**（最危險的 exploit primitive）
- FP 極高：address 稍微 tainted 就炸一大片
- 計算成本高

極少工具預設開。要開必然配合 bound analysis（例如，只 taint 該 allocation 內的 byte）。

## Implicit flow：深坑

```c
int x = 0;
if (tainted_cond) {
    x = 1;
}
// x 的值實際上依賴於 tainted_cond
// 但 x 本身不是 tainted 的 computation 結果
```

這就是 **implicit flow**。不追的話：

- attacker 可以用 `tainted_cond` 的值把 tainted 資訊**編碼**進 x
- 明明是洩漏，DTA 說沒事

### 傳統做法：control scope

追蹤方式：

```
instrument if (tainted_cond):
    mark "we are in a tainted branch"
    all assignments in this scope get tainted
```

實作上：維護一個 **context taint stack**，進入 tainted branch push、出來 pop。branch 內的寫入都被 taint。

問題：
- 編譯後的 binary 沒有 "scope" 概念，需要靜態分析找 post-dominator 才能知道「什麼時候這個 scope 結束」
- branch 裡什麼都沒寫也會被影響（implicit 的 else branch 做的 "什麼都沒寫" 本身是 taint 資訊）
- 在迴圈、function call、exception 交錯時，scope 邊界變得模糊

### 實務選擇

- **多數工具（libdft、Triton default）**：不追 implicit flow — 承認 FN、換性能
- **安全敏感場景（crypto side-channel 分析）**：追 implicit flow — 接受 performance penalty、更多 FP
- **混合**：預設不追，針對特定 branch 手動指示「這個 branch 要追 implicit」

### 實務測試你懂不懂 implicit flow

```c
int copy_with_implicit(int tainted_bit) {
    int result = 0;
    for (int i = 0; i < 32; i++) {
        if ((tainted_bit >> i) & 1) {
            result |= (1 << i);
        }
        // else: 什麼都沒做
    }
    return result;
}
```

這個 function 實際上是 identity（把 `tainted_bit` 複製到 `result`）。

**沒有 explicit flow 從 `tainted_bit` 到 `result`**！`tainted_bit` 只進入 `if` 的 cond、沒直接寫 `result`。

- 追 explicit：`result` 不 tainted，DTA 漏報
- 追 implicit：`result` tainted，正確

Malware 用這種 pattern 繞過 naive DTA。

## Over-tainting 的主要來源

### 1. 常用 sink 暴力傳

```c
memcpy(dst, tainted_src, 100);
```

假設 dst 一開始乾淨。memcpy 後 `dst[0..99]` tainted。如果 target 只用 dst[0]，你把 `dst[1..99]` 的 taint 傳出去可能無意義。

### 2. libc function 的粗略 summary

`strcpy(dst, src)` — 傳到 null terminator 為止。如果 summary 寫「整個 dst 都 tainted」就 over。

### 3. Pointer 結果

```c
void* p = malloc(100);
// malloc 回傳的 pointer 是否 tainted？
```

多數工具說否（malloc 是內部 allocation，不來自外部）。但有些場景想 taint — 代表 "這塊 memory 的 allocation 尺寸來自 tainted"。

## Under-tainting 的主要來源

### 1. 不 instrument 的 function

target 呼叫 syscall、第三方 library（沒 instrument）、JIT-compiled code、kernel。taint 進去不出來。

### 2. Implicit flow 漏追

前面已講。

### 3. SIMD / floating-point 的邊緣

某些 SIMD instruction 在 DBI 中 instrument 不完整，taint 漏失。

### 4. 時序 / side channel

```c
if (tainted_secret) { delay(100); }
else                { delay(200); }
```

timing 本身洩漏資訊。DTA 不追 timing，必漏。

## Policy 的典型配置

不同 use case 的主流 policy：

### 漏洞研究（找 OOB / exploit）

- Source：external input (read / recv / argv)
- Sink：function pointer、return address、syscall args
- Policy：explicit + pointer-taint load + address-taint store（aggressive）
- Implicit flow：通常不追
- 目標：少 FN，FP 可容忍

### Privacy leak analysis

- Source：敏感資料（credential buffer、密鑰）
- Sink：socket send、file write、log output
- Policy：explicit + implicit（crypto 側會編碼隱藏）
- 目標：少 FN，FP 可接受

### Malware 動態分析

- Source：network receive、 dropped file 內容
- Sink：system API（shell exec、registry write、file write、inject）
- Policy：explicit + 控流追蹤（malware 愛用 implicit）
- 目標：完整 behavior profile

### Web runtime（PHP / Python）

- Source：HTTP request fields
- Sink：SQL executor、HTML output、eval()
- Policy：explicit only（implicit 跨 function 難）
- 目標：FP 低 ─ 給開發者看
- 實作：語言 runtime 整合（PHP Suhosin、Python Pysa）

## Sanitizer：削掉 taint

有些 operation 明確 "清洗" 了 taint 來源：

```c
char* escaped = htmlspecialchars(tainted_s);  // 清洗
char* hashed = sha256(tainted_s);              // 變了，已經不是原資料
int len = strlen(tainted_s);                    // 只是長度，不洩漏內容？
```

policy：指定 **sanitizer function 集合**，它們的 output 不繼承 input 的 taint。

注意：sanitizer 必須證明過正確。寫錯的 sanitizer（漏處理 edge case）反而讓你以為乾淨了。

## 工具的 policy 組合選擇

| 工具 | Explicit | Pointer taint (load) | Pointer taint (store) | Implicit |
|------|---------|--------------------|---------------------|----------|
| libdft | ✓ | ✗ | ✗ | ✗ |
| Triton (default) | ✓ | ✗ | ✗ | ✗ |
| Triton (可開) | ✓ | ✓ | ✓ | ✓ |
| TEMU / Argos | ✓ | ✓ | ✓ | ✓ (partial) |
| DECAF | ✓ | optional | optional | optional |
| Panda/PIRATE | ✓ | ✓ | ✓ | ✓ |

實務最常跑 **explicit only**、加必要時的 pointer load。implicit 幾乎沒人 default 開。

## 設定 policy 的 checklist

你要寫 DTA 工具時，問自己這幾題：

1. **Source 定義**：具體列出所有 source type（不只 "user input"，要精確到 API）
2. **Sink 定義**：具體列出所有要檢查的 API + 參數 position
3. **Pointer taint**：load tainted？store tainted？各自理由
4. **Implicit flow**：追嗎？如果追，範圍是整個 binary 還是關鍵區域
5. **Sanitizer list**：什麼 function 清 taint
6. **Library summary**：對哪些 lib 寫 summary
7. **精度 / 速度 tradeoff**：容忍多少 FP、接受多少 slowdown

把答案寫下來當 spec，再寫 code。這是 DTA 工具設計的正確順序。

## 心法

Taint policy 是一個**規格問題**，不是實作問題。

多數人的誤區：搶著用 tool（libdft / Triton），policy 抄 default、跑一堆 alert、然後抱怨「noise 太多」。

正確：
1. 先明文寫你的 policy
2. 看 tool 的 default 跟你的 policy 差多少
3. 填補差距（寫 hook、寫 sanitizer list、關 implicit 追蹤）
4. 跑、看 report、根據 FP/FN 反饋調 policy

好 DTA 工程師的技能是 **policy 設計的 judgment**，不是工具熟練。

## 自我檢核

- [ ] 能定義 explicit flow 與 implicit flow
- [ ] 列出 over-taint 與 under-taint 的 3+ 個來源
- [ ] 理解 pointer taint 的 value-only / load / store 三種 policy
- [ ] 能對一個具體 use case（漏洞研究、privacy、malware）寫 policy
- [ ] 知道 sanitizer 的角色跟設計風險

下一章進到實作 — taint granularity（byte/bit）怎麼選、shadow memory 怎麼放。

→ [Ch 21 — Granularity 與 shadow memory 實作](./21-shadow-memory.md)
