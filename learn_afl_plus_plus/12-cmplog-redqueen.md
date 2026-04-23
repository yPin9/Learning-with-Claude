# Ch 12 — CmpLog / RedQueen：破 magic bytes 的關鍵

> 目標：先解釋為什麼 `if (x == 0xDEADBEEF)` 讓純 bitflip 幾乎無望；引入 REDQUEEN 論文的 input-to-state correspondence 觀點；拆 AFL++ 的 CmpLog 如何 instrument 比較指令、在 runtime 收集 operand、回饋給 mutator 做直接替換。

## 再次面對 magic bytes 問題

Ch 1 提過，Ch 11 也碰過：

```c
if (input_val == 0xDEADBEEF) crash();
```

pure bitflip 要命中這個 branch 的機率是 $2^{-32}$。auto-dictionary 能解這個（常數在編譯期可見），但：

```c
u32 expected = compute_checksum(input_body);
if (input_header == expected) ...
```

auto-dict 完全無解 — `expected` 是 runtime 算出來的，沒有編譯期 constant 可抽。

這類「runtime dependent magic」是 coverage-guided fuzzer 的**結構性弱點**，2019 年 REDQUEEN paper 提出了一個關鍵 insight 來解它。

## REDQUEEN 的 insight：input-to-state correspondence

Aschermann et al., **REDQUEEN: Fuzzing with Input-to-State Correspondence** (NDSS 2019)。中心論點一句話：

> **很多時候，input 的某幾個 byte 會原封不動（或近乎原封）出現在 compare 指令的 operand 裡。**

例如這種常見 pattern：

```c
void parse(char *input) {
    u32 magic = *(u32*)input;         // 直接讀 input 4 byte
    if (magic == 0xDEADBEEF) ...      // compare 的 operand1 就是 input[0..4]
}
```

Runtime 第一次跑到 `if` 時，CPU 執行的比較是 `cmp input[0..4], 0xDEADBEEF`。這時 operand1 = 隨機 bytes（fuzzer 生的），operand2 = 0xDEADBEEF。

**如果你觀察到 operand1 就是 input 裡的 bytes，那 operand2 就是「你想要 input 裡變成什麼」**。直接把 input[0..4] 替換成 0xDEADBEEF 就能過關。

這個想法同樣適用於 runtime 動態值：

```c
u32 expected = compute_crc(input + 8, len - 8);
if (*(u32*)input == expected) ...
```

跑一次後你會看到 `cmp input[0..4], <CRC value>`。把 input[0..4] 換成那個 CRC value —  **即使你不知道 CRC 怎麼算**。

這個把 operand2 替換回 input 的動作，paper 叫 **I2S replacement**。

## AFL++ 的實作：CmpLog

CmpLog 是 AFL++ 對 REDQUEEN idea 的實作。流程分兩步：

### Step 1：編譯第二份 target

CmpLog 不在主 target binary 裡插，而是**另外編一份「CmpLog binary」**：

```bash
# 先編主 target（正常 coverage instrumentation）
AFL_LLVM_INSTRUMENT=PCGUARD afl-clang-fast -o target target.c

# 再編 CmpLog 版
AFL_LLVM_CMPLOG=1 AFL_LLVM_INSTRUMENT=PCGUARD \
  afl-clang-fast -o target.cmplog target.c
```

跑 fuzzer 時給兩個 binary：

```bash
afl-fuzz -i seeds/ -o out/ -c ./target.cmplog -- ./target @@
#                              ^^^ -c 指向 CmpLog binary
```

為什麼要兩份？CmpLog 的插樁很重（每個 compare、每個 strcmp 都要 hook），對主 fuzzing loop 的 throughput 有拖累。讓它只在少數 iteration 跑、平時用輕量版 — 是個 throughput 考量。

### Step 2：CmpLog 的插樁

CmpLog binary 的 LLVM pass（`instrumentation/cmplog-instructions-pass.cc` 和 `cmplog-routines-pass.cc`）做這些：

**Instruction 級**：每個 `icmp` 指令前後加 hook：

```c
// 原始：
if (a == b) { ... }

// 插樁後（概念）：
__cmplog_ins_hook(sizeof(a), (u64)a, (u64)b);
if (a == b) { ... }
```

hook 把 (operand1, operand2) 寫進 shared memory 的 `cmplog_map`：

```c
struct cmpfn_operands {
    u8 v0[32];   // operand1 最多 32 byte
    u8 v1[32];   // operand2 最多 32 byte
};

struct cmp_map {
    u16 headers[65536];            // 每個 compare 位置的 log 計數
    struct cmpfn_operands log[65536][32];   // 環型 buffer
};
```

**Function 級**：`strcmp`、`memcmp`、`strncmp` 等函式的呼叫也被 hook，記下兩個 operand 的 byte 內容：

```c
// 原始：
if (strcmp(input, "MAGIC") == 0) ...

// 插樁後：
__cmplog_rtn_hook(input, "MAGIC");
if (strcmp(input, "MAGIC") == 0) ...
```

跑完 CmpLog binary 後，fuzzer 手上有一張表：「在這次執行中，target 比較過 (A, B), (C, D), (E, F) ...」。

## Step 3：I2S 替換 mutator

`src/afl-fuzz-redqueen.c` 的核心邏輯：

```
for each entry in cmplog_map:
    (op1, op2) = entry

    # 如果 op1 在 input 裡出現 → 把 input 那段換成 op2
    for each offset in input where input[offset..offset+len] == op1:
        mutated = input with input[offset..] replaced by op2
        run(mutated)
        if new_coverage: save

    # 反過來也試 —— op2 出現在 input 就換成 op1
    for each offset in input where input[offset..offset+len] == op2:
        mutated = input with input[offset..] replaced by op1
        run(mutated)
        if new_coverage: save
```

這招對前述 CRC 例子就能成功：

- input = `[XXXX][body...]`（XXXX 是 fuzzer 瞎猜的 4 byte）
- CmpLog 跑完記下：`cmp XXXX, <CRC value>`
- redqueen 看到 `XXXX` 在 input 開頭 → 替換為 CRC value
- 新 input = `[<CRC value>][body...]` → 正確過關

**不需要理解 CRC，不需要知道 checksum 算法，只靠觀察運行時的 compare operand**。這是這個 idea 漂亮的地方。

## 延伸情境

CmpLog + I2S replace 還能解幾種進階 pattern：

### 多 byte 逐漸命中

```c
if (input[0] == 'A')
 if (input[1] == 'B')
  if (input[2] == 'C')
   if (input[3] == 'D')
    ...
```

每層 compare 被 CmpLog hook 到，I2S 逐步把每個 byte 換對 — 比 pure bitflip 快數個數量級。

### Non-magic 常數比較

```c
if (len > 65535) return -1;
```

CmpLog 記下 `cmp len, 65535`。之後即使 input 沒有 65535 這個 bytes，redqueen 也會嘗試插入 / 替換 `65535` — 某種意義上等於動態擴充 dictionary。

### 算出來的 checksum

前面舉過。CmpLog 是目前處理動態 checksum 最通用的手段。

## 侷限

CmpLog 解決很多問題，但不是萬能：

- **Complex transformation**：例如 `if ((input[0] ^ key) == magic)` — operand1 是 `input[0] ^ key`，已經不是 input 原樣了。I2S replace 找不到 input 裡的 match。
- **Non-obvious correspondence**：例如 `sha256(input)` 和 constant 比 — operand1 是 32 byte hash，和 input 毫無 byte-level 關係，I2S 束手無策。
- **Side-effecting compare**：compare 會改變後續行為（但一般 target 不會這樣寫）。

對這些極端情況，需要 symbolic execution（SymCC）或 taint analysis（Angora）等更重機制。CmpLog 是「80% 成本的 20% 情境覆蓋不到」— 其餘 80% 已經很好了。

## 和 compare-transform-pass（laf-intel）的關係

Ch 2 提過 laf-intel 的 compare-transform-pass。它和 CmpLog 解同一個問題但手段不同：

| 機制 | Compare-transform (laf-intel) | CmpLog |
|---|---|---|
| 解法 | 編譯期把 `a == 0xDEADBEEF` 拆成 4 個 byte compare | 執行期收集 operand，mutator 做替換 |
| 效果 | 讓 coverage bitmap 逐 byte 點亮，指引 bitflip | 跳過 bitflip，直接把 input 的 byte 換成正確值 |
| 成本 | binary 變大（compare 數爆增），runtime 些微慢 | 額外一份 binary、redqueen 階段慢 |
| 互補 | 是 | 是 |

兩者通常一起開：**compare-transform 在輕量情境優先**（CmpLog 沒跑時就能漸進），**CmpLog 做最後一擊**（要直接寫對 magic bytes）。

## 開 CmpLog 的實務細節

```bash
# 1. 編 main binary
AFL_LLVM_INSTRUMENT=PCGUARD AFL_LLVM_LAF_ALL=1 \
  afl-clang-fast -o target src.c

# 2. 編 CmpLog binary（同 source，額外 env）
AFL_LLVM_INSTRUMENT=PCGUARD AFL_LLVM_CMPLOG=1 \
  afl-clang-fast -o target.cmplog src.c

# 3. 跑
afl-fuzz -i seeds/ -o out/ -c ./target.cmplog -- ./target @@
```

幾個觀察：

- CmpLog 不是每 iteration 都跑。它在 `fuzz_one()` 的特定條件下觸發（例如 entry 還沒被 CmpLog 處理過、或有明顯 stuck）。
- CmpLog 收集的資料有 TTL，redqueen 用完後會清。
- `AFL_CMPLOG_ONLY_NEW=1` 會讓 CmpLog 只處理新加入的 queue entry，省成本。

## 常見誤解

- **「CmpLog 能自動解 checksum」**：只對 input → compare operand 有直接對應的情境。SHA / HMAC 這類不行。
- **「開 CmpLog 一定變快」**：不。增加 throughput 取決於 target 是不是被 magic bytes 卡住。如果 target 本來就沒這問題，開 CmpLog 是白增加 overhead。
- **「CmpLog 和 laf-intel 選一個就好」**：兩者互補，一起開最穩。

## 自我檢核

- [ ] 能用自己的話解釋 input-to-state correspondence
- [ ] 知道 CmpLog 需要**額外編一份 binary**，用 `-c` 指定
- [ ] 能說出 `__cmplog_ins_hook` 和 `__cmplog_rtn_hook` 分別 hook 什麼
- [ ] 能描述 I2S 替換的 mutator 流程
- [ ] 知道 CmpLog 解不了 SHA / 複雜 transform 這種情境

下一章進 persistent mode — 讓 throughput 再飛一個數量級的技巧。

→ [Ch 13 Persistent mode：同一個 process 跑一萬次](./13-persistent-mode.md)
