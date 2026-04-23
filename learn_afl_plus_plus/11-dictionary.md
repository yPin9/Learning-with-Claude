# Ch 11 — Dictionary 與 auto-dictionary

> 目標：說明手動 `.dict` 檔的格式與在 mutator 裡被怎麼取用；解釋 LTO mode 的 auto-dictionary 如何從 `cmp` / `strcmp` 靜態抽出 magic bytes；對比 auto-dictionary 和 CmpLog 的職責邊界。

## 先解釋需求

假設 target 是個 PNG parser：

```c
if (memcmp(input, "\x89PNG\r\n\x1a\n", 8) != 0) return -1;
// 後面才是真正的 parser 邏輯
```

純 coverage-guided fuzzer 要**隨機猜出這 8 bytes**才能越過第一關。8 byte = $256^8 \approx 10^{19}$ 組合，靠 bitflip 幾乎不可能。但你讀了 PNG 規格就知道這是 magic header，直接餵這 8 bytes 就搞定。

Dictionary 就是這個 insight：**把 input 中已知重要的 byte sequence 列出來**，讓 mutator 有機會把它們插進 input、替換 input 片段。

## Manual dictionary：`.dict` 檔

`.dict` 格式是 key-value 文字檔：

```
# 註解
keyword_1="GET "
keyword_2="POST "
keyword_3="HTTP/1.1"
keyword_4="Content-Length:"
keyword_5="\x89PNG\r\n\x1a\n"
keyword_6="\x00\x00\x00\x00"
```

- 左邊是 token name（無意義，只給你自己看）。
- 右邊是 token 內容，支援 `\xNN` hex escape。
- `#` 開頭是註解。

用法：

```bash
afl-fuzz -x ./dict.txt -i seeds/ -o out/ -- ./target @@
```

`afl-fuzz` 啟動時把 dict load 進記憶體，放進一個叫 `extras[]` 的 array（`src/afl-fuzz-extras.c`）。

## 怎麼在 mutator 裡被用

dict token 在兩個階段進 mutator：

### Deterministic 階段的 dict ops

如果 deterministic 沒被關，會跑：

- **`dict-insert`**：在 input 的每個 offset 嘗試插入每個 dict token。
- **`dict-override`**：在 input 的每個 offset 嘗試用每個 dict token 覆蓋一段。

這是窮舉，成本 = `input_len * len(extras)`。dict 太大會爆。

### Havoc 階段

havoc 有兩個 op 涉及 dict：

- **`ADD_EXTRA`**：隨機抽一個 token 插入隨機 offset。
- **`OVERWRITE_EXTRA`**：隨機抽一個 token 覆蓋隨機 offset。

havoc 每次選 op 時這兩個是選項之一，機率和其他 op 類似。

## 內建 dictionary

AFL++ 在 `dictionaries/` 目錄下附了一堆現成的：

```
dictionaries/
├── png.dict
├── jpeg.dict
├── gif.dict
├── http_request.dict
├── http_response.dict
├── xml.dict
├── json.dict
├── tiff.dict
├── zip.dict
├── sql.dict
└── ...
```

如果你 fuzz 一個常見格式的 parser，先找這裡面有沒有對應的 dict，**幾乎永遠該加**。成本幾乎零，收益可以很大。

## Auto-dictionary：LTO 的魔法

Manual dict 的問題：**你要自己知道 token**。對閉源格式或你不熟的 target，從零寫 dict 很痛。

LTO mode (`afl-clang-lto`) 有個殺手鐧 — **auto-dictionary**：在編譯期靜態分析 target 的所有 compare 指令，把常數 operand 自動抽出來當 dict。

### 怎麼抽

LTO pass 遍歷所有 LLVM IR 指令，尋找：

- `icmp` 指令，其中一個 operand 是 `ConstantInt`：抽出常數值當 dict entry。
- `memcmp(x, constant_bytes, n)`：抽出 constant_bytes。
- `strcmp(x, "literal")` / `strncmp` / `strstr` / ...：抽出 literal。
- `switch` 指令：所有 case 值都抽出來。

簡化 pass 程式碼：

```cpp
for (auto &F : M) {
    for (auto &BB : F) {
        for (auto &I : BB) {
            if (auto *CI = dyn_cast<ICmpInst>(&I)) {
                // 找 constant operand
                for (auto &Op : CI->operands()) {
                    if (auto *C = dyn_cast<ConstantInt>(Op)) {
                        u64 val = C->getZExtValue();
                        add_to_dict(val);
                    }
                }
            }
            if (auto *Call = dyn_cast<CallInst>(&I)) {
                StringRef Name = Call->getCalledFunction()->getName();
                if (Name == "strcmp" || Name == "strncmp" ...) {
                    if (auto *Str = extract_string_constant(Call)) {
                        add_to_dict(Str);
                    }
                }
            }
        }
    }
}
```

收集完的 dict 寫進 binary 的一個特殊 ELF section 或透過 `AFL_LLVM_DICT2FILE` 寫到檔。fuzzer 在 load target 時一併讀回。

### 實例

假設 target 有：

```c
if (memcmp(buf, "\x89PNG\r\n\x1a\n", 8)) error();
if (val == 0xDEADBEEF) ...
char *pos = strstr(input, "MAGIC_KEYWORD");
```

LTO pass 會自動抽出：

```
"\x89PNG\r\n\x1a\n"    ← memcmp constant
0xDEADBEEF             ← icmp constant
"MAGIC_KEYWORD"        ← strstr literal
```

不用你動手。

### 啟用方法

```bash
CC=afl-clang-lto \
AFL_LLVM_DICT2FILE=/tmp/autodict.txt \
make

afl-fuzz -x /tmp/autodict.txt -i seeds/ -o out/ -- ./target @@
```

或者更直接 — 新版 AFL++ 會把 autodict 直接 embed 到 binary，fuzzer 自動抓。

## Auto-dict vs CmpLog：分工

兩者都解「magic bytes」問題，但機制不同：

| 維度 | Auto-dictionary | CmpLog |
|---|---|---|
| 作用時機 | 編譯期靜態分析 | 執行期動態收集 |
| 抽出什麼 | 所有 constant compare operand | 動態看到的 operand pair |
| 覆蓋什麼 | `x == 0xDEADBEEF` 中的 `0xDEADBEEF` | 同樣，外加 `x == y` 中 runtime 的 y 值 |
| 成本 | 零（編譯期一次） | 每 iteration 跑 CMPLOG target，慢 2x |
| 缺點 | 只能找 constant；若 compare 用變數做不到 | 需要 runtime 執行到 compare 才能抓 |

**互補**：auto-dict 抓「寫死的 magic」，CmpLog 抓「動態算出的比對值」。兩者同開才 robust。Ch 12 詳講 CmpLog。

## 一個對照

考慮兩段 code：

```c
// 情境 A：寫死的 magic
if (input[0] == 0xCA && input[1] == 0xFE && input[2] == 0xBA && input[3] == 0xBE)
    parse_class_file(input);
```

auto-dict 能抽：`0xCA`、`0xFE`、`0xBA`、`0xBE`（或某些情境下的 `0xCAFEBABE`）。

```c
// 情境 B：動態 checksum
u32 expected = compute_crc32(input + 8, input_len - 8);
if (*(u32*)input == expected) parse(input);
```

auto-dict **做不到** — 這裡沒有 constant operand。但 CmpLog 能在 runtime 看到 `expected` 的值，把它 replace 回 `input[0..4]`。

兩者能力邊界不同，合起來才強。

## Dict 的限制

就算 dict 完美，它也只能解「**input 中有明確可識別 token**」的 target。對以下情境無效：

- **Checksum 需要你算**：例如 PNG 要計算每個 chunk 的 CRC32，dict 給不了 runtime 值。
- **State machine 依賴狀態**：例如 TCP handshake，要按順序送封包，不是靜態 token 問題。
- **Grammar-level**：SQL injection 依賴語義結構，token 夠不夠全是另外一個問題。

這些場景需要 CmpLog（checksum）、custom mutator（state machine）、grammar mutator（SQL）等更重的機制。

## 常見誤解

- **「dict 越大越好」**：不。dict 太大，deterministic 階段會爆；havoc 階段抽到你想要的 token 機率反而被稀釋。500–2000 entries 是甜蜜點。
- **「auto-dict 可以取代 manual dict」**：大多數時候可以，但 manual dict 能放「語義 token」（例如 HTTP 的 `Content-Type`），auto-dict 只會抽出現過的字串 literal。互補關係。
- **「有 dict 就不需要 CmpLog」**：對靜態 magic 可能，但動態 checksum 沒 CmpLog 解不了。

## 自我檢核

- [ ] 能寫出 `.dict` 檔的語法（`keyword="..."`、hex escape）
- [ ] 知道 dict 在 deterministic 和 havoc 哪些 op 被用
- [ ] 能解釋 LTO auto-dictionary 怎麼從 `memcmp`/`icmp` 抽 constant
- [ ] 知道 auto-dict 和 CmpLog 的分工邊界
- [ ] 看到常見 format 的 target 會想先找 `dictionaries/*.dict`

下一章講 CmpLog / REDQUEEN — 這是破解動態 magic、checksum 的關鍵武器。

→ [Ch 12 CmpLog / RedQueen：破 magic bytes 的關鍵](./12-cmplog-redqueen.md)
