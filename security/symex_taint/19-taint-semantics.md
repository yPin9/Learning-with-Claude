# Ch 19 — Taint 語意：source / sink / propagation rule

> 目標：把 dynamic taint analysis (DTA) 的概念從俗化「追誰來自哪裡」升級到形式化定義。講完你要能用 source / sink / propagation 三件事 specify 一個安全問題。

## Taint 是什麼：一個 label 的故事

DTA 的核心想法單純到不可思議：

> 「給某些資料加一個 label（我們叫它 **taint**），每次這些資料參與運算，label 傳到結果上。你看到 label 在哪裡出現，就知道資料流到哪裡去了。」

這跟你在廚房貼紅色標籤「這東西碰過生肉」一樣直覺。吃具碰了紅標、那吃具變紅標；洗過了就可以撤掉紅標。

把它形式化：

```
tainted : Value → {true, false}
```

對每個 runtime value，taint 是一個 boolean（或一個 label set，後面會講）。定義三件事就完成 DTA 規格：

1. **Sources**：什麼值被初始標記 tainted
2. **Propagation rule**：運算怎麼傳 taint
3. **Sinks**：什麼位置要檢查 taint（如果 tainted 就 alert）

## Source：什麼要標 tainted

典型選擇：

| Source | 為什麼 tainted | 要找什麼 |
|--------|----------------|----------|
| `read()` / `recv()` 的 buffer | 外部輸入 | injection、buffer overflow |
| `getenv()` 的 return | 環境變數 | privilege escalation |
| `fgets()` 從 stdin | 使用者輸入 | SQLi、command injection |
| HTTP request body | 網路輸入 | SSRF、deserialization |
| 密碼、金鑰 buffer | 敏感資料 | information leak |
| system call 回傳 | kernel 來源 | TOCTOU |

你決定 source 集合 = 你定義了「我要追蹤什麼」。

## Sink：什麼要檢查

典型選擇：

| Sink | 為什麼 check | 找什麼 |
|------|-------------|--------|
| `system()`、`exec()` 的 arg | 命令執行 | command injection |
| SQL query string | DB query | SQL injection |
| `memcpy(dst, src, n)` 的 n | size parameter | buffer overflow |
| function pointer 的值 | control flow hijack | ROP / exploit |
| `open()` 的 path | file access | path traversal |
| `send()`、`sendto()` 的 buffer | 網路 output | info leak |
| printf format string | 格式字串攻擊 | fmt string exploit |

source + sink 成對 define 一個 security policy：
- `read → system`：command injection
- `recv → sql_query`：SQL injection  
- `secret → send`：info leak

## Propagation rule：核心

最有意思的部分。**什麼運算會把 taint 從輸入傳到輸出？**

### 直接賦值

```c
int a = tainted_val;   // a 也 tainted
int b = a;             // b 也 tainted
```

顯然 taint 要傳。

### 算術運算

```c
int c = tainted_a + tainted_b;  // c tainted
int d = tainted_a + 5;          // d tainted
int e = 3 + 5;                   // e 不 tainted
```

一般 rule：**output tainted 當且僅當至少一個 operand tainted**。

### Memory load

```c
int *p = &arr[tainted_idx];
int v = *p;   // v tainted？
```

這是關鍵 design choice。兩派：

- **Value-tainted**：只有**值本身**來自 source 才 tainted。`arr` 裡是乾淨 data 所以 `v` 不 tainted
- **Address-tainted**：**address 如果 tainted**，load 的 value 也 tainted（因為 attacker 可以控 address）

下一章 Ch 20 細拆。這叫 **pointer taint policy**。

### Memory store

```c
mem[tainted_idx] = clean_val;
```

address tainted 但 value 不 tainted。寫完，`mem[idx]` 的那個位置 tainted 嗎？

一樣兩派。多數 dynamic 工具預設**不**對 store 做 address-taint 傳播（因為 store 只「定位」，沒產生新 value）。

### Bit 運算

```c
int x = tainted_a & 1;   // x tainted（attacker 知 taint_a）
int y = tainted_a & 0;   // y 必為 0 — 還 tainted 嗎？
```

這出名地煩。y 的值恆為 0、沒 attacker-controlled。但 **naive propagation** 會說 tainted — 造成 **false positive**（over-tainting）。

精確 rule 要檢查「output 是否 truly depends on input」— 昂貴，實作上多數工具就 over-taint。

### Control flow

```c
int x;
if (tainted_cond) x = 1;
else              x = 2;
// x 是什麼 taint？
```

x 的值不是 tainted 值算出來的，但 **x 的值依賴於 tainted cond**。這叫 **implicit flow**。

- **Explicit flow**：data 直接流過去（assignment、算術）
- **Implicit flow**：control 流依賴 taint，讓 data 的選擇依賴 taint

Implicit flow 是 DTA 的 achilles heel — 追蹤它的 cost 很高，不追它漏很多。Ch 20 詳講。

### Library call

```c
strcpy(dst, tainted_src);   // dst tainted？
memcpy(dst, tainted_src, n);
atoi(tainted_s);
```

library 內部也要 instrument，或寫 **taint summary**：「strcpy 的 taint = src 的 taint 傳給 dst 的 [0, len] byte」。

每個 libc function 都要 summary。實際工具有個 table。

### Syscall

```c
int fd = open(tainted_path, ...);
```

kernel 不 instrument（你沒辦法），所以要在 userspace 在 syscall boundary **停 taint 或手動處理**。

## Forms of taint

最簡單：**boolean** — 每個 byte tainted or not。

進階：**label sets** — 每個 byte 可以有多個 label，每個 label 代表一個 source：

```
byte = { "user_input", "network", "arg_1" }
```

好處：
- 追多個 source 同時
- label 有 type 可以做 capability model
- 知道某個 sink 被哪幾個 source 染到

工具支援：
- libdft：單 bit（fast）
- Triton：可選單 bit 或多 label
- PANDA：多 label

單 bit 比多 label 快 10+ 倍 — 每個 byte 的 shadow 只要 1 bit vs 幾 byte。決定 **granularity trade-off**。

## Taint 是 approximate 的，永遠

跟 symex 的 "精確 path" 不一樣，DTA 的核心性質：

- **over-taint**：回報 tainted 的 value 實際上可能不 taint（false positive）
- **under-taint**：有些 tainted 該回報但沒回報（false negative）

大多數 DTA 工具**同時**有兩者。不同工具在 FP/FN tradeoff 的位置不同。

**所以 DTA 的設計永遠是 policy 問題**：你要多少 FP、多少 FN，據此調整 propagation rule、implicit flow handling、library summary 的粗細。

## Forward vs backward taint

剛才討論的都是 **forward taint**：source → 往後傳、看有沒有到 sink。

**Backward taint**：從 sink 往前推，看什麼 source 可能到達這個 sink。實作上通常用 **static analysis + data flow graph**，不是 dynamic。

DTA 預設是 forward。這門課的 DTA 部分講 forward。backward 比較偏 static analysis，不在範圍。

## DTA vs static taint

| 面向 | DTA（dynamic） | Static taint |
|------|----------------|--------------|
| 覆蓋率 | 只看實際執行的 path | 全 path |
| 精度 | 真實 value，精確 | alias、dispatch 會 over-approximate |
| 速度 | 執行慢 10–100× | offline 可批量 |
| implicit flow | 手動 instrument | 理論上可全自動 |
| 工具 | Triton、libdft、Pin-based | CodeQL、Pysa、Infer |

互補不替代。大型 codebase 通常 static taint 當 screening、DTA 做 reproducer。

## 一個具體例子

```c
// 簡單 command injection
int main() {
    char cmd[256];
    fgets(cmd, 256, stdin);      // source
    char full[300] = "ls ";
    strcat(full, cmd);
    system(full);                // sink
    return 0;
}
```

DTA 跟蹤：

```
read stdin → cmd[0..N] tainted
strcat(full, cmd) → full[4..4+N] tainted
system(full) → sink 處檢查 arg tainted == true
→ ALERT: command injection possible
```

整個過程不需要 SMT、不需要 fork。**極簡單、極快**。

## 為什麼 DTA 跟 symex 是親戚

注意到沒？DTA 在每個 instruction 把 "taint" 沿著 data flow 傳，symex 在每個 instruction 把 "formula" 沿著 data flow 傳。工程核心幾乎一樣：

- Shadow state（放 meta data）
- Per-instruction propagation rule
- Source/sink 介面
- Pointer / memory semantics 的取捨

只是 symex 的 meta data 是 SMT formula、DTA 的 meta data 是 taint label。

很多現代工具把兩者合體 — **Triton 就是**（Ch 23）。同一套 instrumentation、跑兩種 analysis。

## 心法

DTA 的核心不是實作，是 **policy**：

- source 定哪 → 決定 FN 下界
- sink 定哪 → 決定要抓什麼問題
- propagation rule → 決定 FP/FN 平衡
- implicit flow 處不處理 → 決定精度

寫 DTA 工具前，先**文字列出這四件事**。跳過這步直接 code，寫出來的一定不對。

## 自我檢核

- [ ] 用 source/sink/propagation 三件事 define 一個 SQL injection detector
- [ ] 解釋 memory load/store 兩種 taint policy（value vs address）
- [ ] 定義 explicit flow 跟 implicit flow，各舉一例
- [ ] 知道 over-taint 與 under-taint 的意義
- [ ] 能對照 DTA 跟 symex 的工程結構

下一章深入 **policy design**：implicit flow、over/under-tainting 的細節、每個設計決定的取捨。

→ [Ch 20 — Taint policy 設計：explicit vs implicit flow、over/under-tainting](./20-taint-policy.md)
