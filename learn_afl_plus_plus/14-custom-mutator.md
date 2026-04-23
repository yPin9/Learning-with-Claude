# Ch 14 — Custom mutator API：grammar-aware fuzzing 怎麼接

> 目標：走過 `afl_custom_init` / `afl_custom_fuzz` / `afl_custom_post_process` 等 hook 點；以一個假想的 JSON grammar mutator 為例說明 flow；解釋 custom mutator 和內建 havoc 的權重競合。

## 為什麼內建 mutator 不夠

Ch 9 的 havoc/splice 是 byte-level mutator — 它不知道輸入有結構。對結構化 target 這是雙面刃：

- **好處**：完全通用，任何 target 都能跑。
- **壞處**：對 JSON / SQL / JS 這種高度結構化輸入，隨機 byte mutation 破壞語法的機率極高，fuzzer 大部分時間卡在 parser 的 syntax error 層。

例子：

```json
{"user": "alice", "age": 30}
```

byte-level mutation 一個 `}` 變 `{` → JSON parser 直接 return syntax error。真正的 bug 可能藏在「合法 JSON 但結構異常」的情境 — 例如深度 1000 的嵌套、重複 key、超長 string。byte mutator 很難產生這種。

**Custom mutator API** 讓你用自己的 mutator 替換或補強內建的。典型用途：

- grammar-aware mutator（JSON / SQL / JS / protobuf）
- format-specific mutator（PNG chunk、ELF section）
- 帶 semantics 的 fuzzer（e.g. Gramatron、Nautilus 都是靠這 API 接入）

## Hook 點一覽

AFL++ 的 API 定義在 `include/afl-fuzz.h`。主要 hook：

| Hook | 時機 | 必要？ |
|---|---|---|
| `afl_custom_init` | fuzzer 啟動時，mutator 初始化 | 必 |
| `afl_custom_deinit` | fuzzer 關閉時，清資源 | 必 |
| `afl_custom_fuzz_count` | 告訴 fuzzer 這個 entry 你想要變異幾次 | 選 |
| `afl_custom_fuzz` | 實際產生變異後的 input | 必（核心） |
| `afl_custom_post_process` | 變異後交給 target 前的 fix up（例如重算 checksum） | 選 |
| `afl_custom_havoc_mutation` | 加入 havoc 階段作為一個 op | 選 |
| `afl_custom_havoc_mutation_probability` | havoc 抽到 custom op 的機率 | 選 |
| `afl_custom_queue_get` | fuzzer 每次選 queue entry 問你要不要 fuzz 這個 | 選 |
| `afl_custom_queue_new_entry` | 有新 entry 加入 queue 時通知 | 選 |
| `afl_custom_introspection` | debug / logging hook | 選 |

實務上最常寫的是 `init / deinit / fuzz`，其他按需。

## 最小可執行 mutator 骨架

用 C 寫的 mutator 編成 shared object，fuzzer 透過 `dlopen` 載入。最小骨架：

```c
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct my_mutator {
    uint8_t *buf;
    size_t  buf_size;
} my_mutator_t;

// Init
void *afl_custom_init(void *afl, unsigned int seed) {
    my_mutator_t *data = calloc(1, sizeof(my_mutator_t));
    data->buf = malloc(1024 * 1024);
    data->buf_size = 1024 * 1024;
    srand(seed);
    return data;
}

// Fuzz — 核心
size_t afl_custom_fuzz(my_mutator_t *data,
                       uint8_t *buf, size_t buf_size,
                       uint8_t **out_buf,
                       uint8_t *add_buf, size_t add_buf_size,
                       size_t max_size) {
    // 簡單例子：把 input 反過來
    for (size_t i = 0; i < buf_size; i++) {
        data->buf[i] = buf[buf_size - 1 - i];
    }
    *out_buf = data->buf;
    return buf_size;
}

// Deinit
void afl_custom_deinit(my_mutator_t *data) {
    free(data->buf);
    free(data);
}
```

編譯：

```bash
gcc -O2 -shared -fPIC -o my_mutator.so my_mutator.c
```

執行：

```bash
AFL_CUSTOM_MUTATOR_LIBRARY=./my_mutator.so \
  afl-fuzz -i seeds/ -o out/ -- ./target @@
```

這樣 fuzzer 每次 mutate 時會呼叫你的 `afl_custom_fuzz` 而不是（或除了）內建 havoc。

## 一個 grammar-aware 例子（偽 code）

假設要 fuzz JSON parser，內建 grammar：

```c
typedef struct json_mutator {
    json_tree_t *tree;     // 把 input parse 成 AST
    uint8_t     *buf;
} json_mutator_t;

size_t afl_custom_fuzz(json_mutator_t *m,
                       uint8_t *buf, size_t buf_size,
                       uint8_t **out_buf, ...,
                       size_t max_size) {
    // 1. Parse input 成 AST（容忍失敗）
    json_tree_t *tree = json_parse(buf, buf_size);
    if (!tree) tree = random_tree();   // 解析失敗就亂生一棵

    // 2. 隨機在 AST 某節點做 mutation
    switch (rand() % 4) {
        case 0: duplicate_random_node(tree); break;
        case 1: swap_key_value(tree); break;
        case 2: deep_nest(tree, rand() % 1000); break;
        case 3: insert_edge_case_value(tree); break;
    }

    // 3. Serialize 回 byte stream
    size_t out_len = json_serialize(tree, m->buf, max_size);
    json_free(tree);

    *out_buf = m->buf;
    return out_len;
}
```

每個 mutation 都保持 **grammar 合法性**（或選擇性違反），fuzzer 不再把時間花在 syntax error。

## `post_process`：checksum / wrapper 修補

有些 format 要求每次 input 改了就要重算 checksum：

```c
size_t afl_custom_post_process(my_mutator_t *data,
                               uint8_t *buf, size_t buf_size,
                               uint8_t **out_buf) {
    // buf 是 mutator 剛產生的
    // 重算 header CRC32
    u32 crc = crc32(buf + 16, buf_size - 16);
    memcpy(buf, &crc, 4);
    *out_buf = buf;
    return buf_size;
}
```

post_process 在 mutator 產出、target 執行之前呼叫。這裡可以做：

- 重算 checksum / HMAC
- 套 encoding（base64、zlib）
- Pad 到固定大小

## `fuzz_count`：告訴 fuzzer 期待多少變異

```c
uint32_t afl_custom_fuzz_count(my_mutator_t *data,
                               const uint8_t *buf, size_t buf_size) {
    // 對這個 input 我只想產生 10 個變異就夠了
    return 10;
}
```

預設 fuzzer 依 power schedule 決定。如果你的 mutator 對某類 input 知道「做 N 次就會收斂」，可以告知 fuzzer 省時間。

## `havoc_mutation`：融入 havoc 階段

與其完全替換 havoc，可以讓 custom mutator 成為 havoc 的一個 op：

```c
size_t afl_custom_havoc_mutation(my_mutator_t *data,
                                 uint8_t *buf, size_t buf_size,
                                 uint8_t **out_buf, size_t max_size) {
    // 這會被 havoc stack 的其中一輪呼叫
    return apply_one_grammar_mutation(data, buf, buf_size, out_buf, max_size);
}

uint8_t afl_custom_havoc_mutation_probability(my_mutator_t *data) {
    return 50;   // 50% 機率 havoc 抽到 custom op
}
```

這樣 custom mutator 和 byte-level mutation 混合 — 有時走 grammar 變異、有時走 bit flip。對 grammar 不完美的 target 這是穩妥選擇。

## Python mutator API

如果 C 太重，AFL++ 也支援 Python：

```python
def init(seed):
    global rng
    rng = random.Random(seed)

def fuzz(buf, add_buf, max_size):
    # 對 buf 做 mutation，return bytes
    return buf[::-1]   # 簡單例子：反轉

def post_process(buf):
    return buf   # 或做 fixup
```

執行：

```bash
AFL_PYTHON_MODULE=my_mutator \
  afl-fuzz -i seeds/ -o out/ -- ./target @@
```

Python 版適合 prototype 和 grammar 描述（用 `random` + 資料結構較好寫）。生產 fuzz 還是 C 快。

## 內建 custom mutator 範例

AFL++ 自帶幾個可以參考：

- `custom_mutators/grammar_mutator/`：通用 grammar mutator（JSON、XML、SQL 等）。
- `custom_mutators/gramatron/`：paper 實作，用 PDA 表達 grammar。
- `custom_mutators/honggfuzz/`：把 Honggfuzz 的 mutation 移植過來。
- `custom_mutators/libfuzzer/`：跑 libFuzzer 的 mutator。
- `custom_mutators/radamsa/`：radamsa（blackbox 代表）當 mutator。

讀這些是最快掌握 API 的方法。

## 多 mutator 共存

你可以同時 load 多個 custom mutator：

```bash
AFL_CUSTOM_MUTATOR_LIBRARY=./grammar_mutator.so:./honggfuzz_mutator.so \
  afl-fuzz -i seeds/ -o out/ -- ./target @@
```

fuzzer 會輪流呼叫每個。各司其職（e.g. 一個專做 grammar-level、一個專做 byte-level）通常效果最好。

## 常見誤解

- **「custom mutator 會取代 havoc」**：預設不會。如果 `AFL_CUSTOM_MUTATOR_ONLY=1` 才完全取代；否則 custom 和 havoc 並存。
- **「grammar mutator 一定比 havoc 好」**：對強 grammar target 是；對弱結構 target（binary format、C struct）經常不如 havoc。
- **「API 有 post_process 就可以算 checksum」**：能但要注意 — post_process 跑在每個 mutation 後，checksum 計算成本會放大。長 input 的 CRC 算很慢時需要 optimize。

## 自我檢核

- [ ] 能列出 custom mutator 的必要 hook（init / fuzz / deinit）
- [ ] 能說出 `post_process` 的典型用途（checksum fixup）
- [ ] 知道 custom mutator 可以和 havoc 共存、也可以獨佔
- [ ] 理解 grammar-aware mutator 為什麼對 JSON / SQL / JS target 特別有效
- [ ] 知道 `custom_mutators/` 目錄下有現成範例可抄

下一章看 AFL 和 sanitizer 的合作 — 為什麼 ASan 不和 bitmap 打架。

→ [Ch 15 Sanitizer 整合：AFL + ASan 為什麼不衝突](./15-sanitizers.md)
