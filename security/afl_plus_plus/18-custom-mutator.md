# Ch 18 — Custom Mutator：寫自己的 mutation 邏輯

> **目標**：能實作一個 AFL++ custom mutator（Python 或 C），理解 mutator API 的各個 callback，以及什麼時候需要它。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要這個？

AFL++ 的內建 mutation 是 format-agnostic（不知道輸入格式）。它把 input 當成不透明的 bytes 串，隨機翻轉位元、插入或刪除區塊。這個策略對 C 字串解析器或 PNG decoder 效果很好，因為合法的 input 和 mutated input 在結構上距離不遠。

但對高度結構化的格式就失效了：

- **PDF**：每個物件都有固定的 `<< ... >>` 語法，隨機 bit flip 幾乎必然產生被 parser 在第一行就拒絕的廢料
- **HTTP request**：`GET /path HTTP/1.1\r\nHost: ...` 的格式非常嚴格
- **Protobuf**：二進位格式，隨機修改一個 byte 就破壞 varint 編碼，parser 直接報錯退出

結果是：fuzzer 把 99% 的時間花在修正語法錯誤的入口路徑，根本沒機會接觸到深層邏輯。

2019 年以後，AFL、libFuzzer 等工具陸續加入 custom mutator 支援。AFL++ 的 custom mutator API 在 AFL++ 2.x 出現，到 4.x 已經相當成熟，讓使用者可以攔截 mutation 過程，替換成知道格式語意的邏輯。

## 先建立直覺

把 AFL++ 的 fuzzing 主迴圈想成一條流水線：

```
選一個 seed（from queue）
        ↓
  AFL++ 的 trimming（精簡 seed）
        ↓
  mutation 階段
   ├─ deterministic（按序 bit flip, byte flip...）
   └─ havoc（亂數 mutation）
        ↓
  執行 target，收集 coverage
        ↓
  有新 coverage？→ 存進 queue
```

Custom mutator 的切入點就在「mutation 階段」：你可以完全替換 `afl_custom_fuzz()`，也可以在 AFL 做完之後再後處理（`afl_custom_post_process()`）。

**心智圖像**：你是一個坐在 AFL++ 旁邊的「翻譯員」。AFL++ 遞給你一個 seed，你按照你對格式的知識做出有意義的變體，再還給 AFL++。AFL++ 只管執行和收集 coverage，完全不需要懂格式。

## 核心概念：六個 Callback

AFL++ Custom Mutator API 有六個主要 callback，你不需要全部實作，只需要實作你用得到的：

### 1. `afl_custom_init()` — 初始化

```c
void *afl_custom_init(afl_state_t *afl, unsigned int seed);
```

- AFL++ 啟動時呼叫一次
- 返回一個 `void *`，這個指標會傳給後續所有 callback（你的 mutator 狀態）
- `seed`：AFL++ 給你的隨機種子，讓你的隨機邏輯可以重現

### 2. `afl_custom_fuzz()` — 主要 mutation callback

```c
size_t afl_custom_fuzz(void *data,
                       uint8_t *buf, size_t buf_size,
                       uint8_t **out_buf,
                       uint8_t *add_buf, size_t add_buf_size,
                       size_t max_size);
```

- `buf` / `buf_size`：當前選中的 seed
- `add_buf` / `add_buf_size`：另一個隨機選出的 seed（用於 splice 操作）
- `out_buf`：你寫入 mutated output 的緩衝區指標（填入 `*out_buf`）
- `max_size`：輸出上限，**必須遵守**
- 返回值：mutated output 的 byte 數；返回 0 表示「本次不做 mutation，讓 AFL++ fallback 到 havoc」

### 3. `afl_custom_describe()` — 描述這次 mutation

```c
const char *afl_custom_describe(void *data, size_t max_description_len);
```

- Debug 用，顯示在 AFL++ 的 UI 和 log 裡
- 讓你知道某次 crash 是「HTTP method fuzz」還是「header value overflow」

### 4. `afl_custom_queue_get()` — 控制 seed 選取

```c
uint8_t afl_custom_queue_get(void *data, const uint8_t *filename);
```

- AFL++ 每次從 queue 選 seed 時呼叫
- 返回 1：允許選這個 seed；返回 0：跳過
- 用途：只讓某些類型的 seed 進你的 mutator（例如只處理 GET 請求）

### 5. `afl_custom_queue_new_entry()` — 新 seed 進 queue 時的 callback

```c
uint8_t afl_custom_queue_new_entry(void *data,
                                    const uint8_t *filename_new_queue,
                                    const uint8_t *filename_orig_queue);
```

- 有新 seed 被加入 queue 時呼叫
- 用途：對新 seed 做後處理（例如補全 checksum）

### 6. `afl_custom_post_process()` — AFL++ mutation 之後再處理

```c
size_t afl_custom_post_process(void *data,
                                uint8_t *buf, size_t buf_size,
                                uint8_t **out_buf);
```

- AFL++ 的 havoc 做完之後，你再加工一次
- 典型用途：AFL 做完 mutation 後，你重新計算 checksum 或補上 magic bytes

---

## 底層機制：它是怎麼運作的？

```
AFL++ main loop
│
├─ seed = queue_pick()
│       ↑ afl_custom_queue_get() ← 你可以 veto
│
├─ trim(seed)
│
├─ [deterministic stages — bit/byte flip]
│
└─ havoc stage
        │
        ├─ if (custom_mutator exists)
        │       call afl_custom_fuzz(seed, add_buf, ...)
        │            ↓
        │       你的邏輯：parse → mutate → serialize
        │            ↓
        │       返回 mutated_buf
        │
        ├─ afl_custom_post_process(mutated_buf)  ← 選配
        │
        └─ fork() → target(mutated_buf)
                ↓
            coverage bitmap diff
                ↓
            有新 coverage？
                ├─ YES → add to queue
                │         afl_custom_queue_new_entry()  ← 選配
                └─ NO  → discard
```

AFL++ 透過動態載入（`dlopen()`）載入你的 `.so`，或透過 Python C Extension 呼叫你的 `.py`。兩者的 API 語意相同，差在 overhead。

---

## 範例一：Python Custom Mutator（最快上手）

以下是一個針對 HTTP request 的 Python mutator，知道 header 的格式，只做有意義的變體：

```python
# http_mutator.py
import random

# AFL++ Python mutator API：不需要 class，直接用 module-level function

def init(seed):
    """AFL++ 啟動時呼叫一次"""
    random.seed(seed)

def fuzz(buf, add_buf, max_size):
    """
    buf:      原始 input（bytes）
    add_buf:  另一個 seed（bytes），可用於 splice
    max_size: 輸出最大 bytes
    回傳:     mutated bytes
    """
    try:
        text = buf.decode('utf-8', errors='replace')
        lines = text.split('\r\n')
    except Exception:
        return buf  # 無法解析就原樣返回，AFL 會 fallback

    if not lines:
        return buf

    # 策略 1：隨機替換一個 header 的值
    header_indices = [i for i, l in enumerate(lines) if ':' in l]
    if header_indices and random.random() < 0.5:
        idx = random.choice(header_indices)
        key, _, val = lines[idx].partition(': ')
        # 用 add_buf 的片段替換 value（cross-seed splice）
        if add_buf and random.random() < 0.3:
            fragment = add_buf[random.randint(0, max(0, len(add_buf)-8)):][:16]
            lines[idx] = key + ': ' + fragment.decode('utf-8', errors='replace')
        else:
            # 常見 header injection payload
            payloads = [
                'A' * random.randint(100, 1000),   # 過長值
                '\r\nX-Injected: evil',              # header injection
                '../../../etc/passwd',               # path traversal in header
                '0' * 0,                             # 空值
            ]
            lines[idx] = key + ': ' + random.choice(payloads)

    # 策略 2：隨機插入一個新 header
    if random.random() < 0.2 and len(lines) > 1:
        evil_headers = [
            'Transfer-Encoding: chunked',
            'Content-Length: -1',
            'X-Forwarded-For: 127.0.0.1',
        ]
        lines.insert(1, random.choice(evil_headers))

    result = '\r\n'.join(lines).encode('utf-8', errors='replace')

    # 嚴格遵守 max_size
    if len(result) > max_size:
        result = result[:max_size]

    return result


def describe(max_description_len):
    """顯示在 AFL++ 的 log 裡（選配）"""
    return "http_header_mutator"[:max_description_len]
```

啟動方式：

```bash
# 設定使用 Python mutator
export AFL_PYTHON_MODULE=http_mutator

# 把 mutator 放在當前目錄或 PYTHONPATH 能找到的地方
afl-fuzz -i seeds/ -o out/ -- ./http_server @@
```

---

## 進一步用法：C Custom Mutator（效能版）

Python mutator 每次呼叫都有 Python interpreter overhead，比 C 慢約 5-10x。對需要高速 fuzzing 的場景，用 C 實作：

```c
/* my_mutator.c */
#include "afl-fuzz.h"   /* AFL++ 提供，包含 afl_state_t 定義 */
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* Mutator 的私有狀態 */
typedef struct {
    afl_state_t *afl;    /* AFL++ 狀態（不要修改） */
    uint8_t     *buf;    /* 內部工作緩衝區 */
    uint32_t     buf_size;
    unsigned int rng;    /* 私有隨機數種子 */
} my_mutator_t;

/* ── 初始化 ── */
void *afl_custom_init(afl_state_t *afl, unsigned int seed) {
    my_mutator_t *m = calloc(1, sizeof(my_mutator_t));
    if (!m) return NULL;
    m->afl      = afl;
    m->rng      = seed;
    m->buf_size = 4096;
    m->buf      = malloc(m->buf_size);
    return m;
}

/* 內部：簡單 LCG */
static unsigned int lcg_rand(unsigned int *state) {
    *state = *state * 1664525u + 1013904223u;
    return *state;
}

/* ── 主要 mutation ── */
size_t afl_custom_fuzz(void    *data,
                       uint8_t *buf,      size_t buf_size,
                       uint8_t **out_buf,
                       uint8_t *add_buf,  size_t add_buf_size,
                       size_t   max_size) {

    my_mutator_t *m = (my_mutator_t *)data;

    /* 確保工作緩衝區夠大 */
    if (max_size > m->buf_size) {
        free(m->buf);
        m->buf      = malloc(max_size);
        m->buf_size = max_size;
        if (!m->buf) return 0;   /* OOM → fallback to havoc */
    }

    /* 複製原始 input 到工作緩衝區 */
    size_t out_size = buf_size < max_size ? buf_size : max_size;
    memcpy(m->buf, buf, out_size);

    /* 找到第一個 '\n'，假設這是 HTTP request line */
    uint8_t *nl = memchr(m->buf, '\n', out_size);
    if (!nl) {
        *out_buf = buf;  /* 找不到換行，不做 mutation */
        return 0;        /* 0 → AFL fallback to havoc */
    }

    /* 在 request line 末尾插入一個隨機長度的 padding */
    uint32_t pad_len = (lcg_rand(&m->rng) % 64) + 1;
    size_t   insert_pos = (size_t)(nl - m->buf);

    if (insert_pos + pad_len + (out_size - insert_pos) <= max_size) {
        /* 後移後段 */
        memmove(m->buf + insert_pos + pad_len,
                m->buf + insert_pos,
                out_size - insert_pos);
        /* 填入 'A' */
        memset(m->buf + insert_pos, 'A', pad_len);
        out_size += pad_len;
    }

    *out_buf = m->buf;
    return out_size;
}

/* ── 描述 ── */
const char *afl_custom_describe(void *data, size_t max_description_len) {
    (void)data; (void)max_description_len;
    return "http_request_line_pad";
}

/* ── 清理 ── */
void afl_custom_deinit(void *data) {
    my_mutator_t *m = (my_mutator_t *)data;
    if (m) {
        free(m->buf);
        free(m);
    }
}
```

編譯與啟動：

```bash
# 編譯成共享函式庫（shared library）
gcc -O2 -shared -fPIC -o my_mutator.so my_mutator.c \
    -I /path/to/AFLplusplus/include

# 啟動（.so 路徑可以是絕對或相對）
AFL_CUSTOM_MUTATOR_LIBRARY=./my_mutator.so \
    afl-fuzz -i seeds/ -o out/ -- ./http_server @@

# 也可以只用 custom mutator，完全關閉 AFL++ 的內建 havoc
AFL_CUSTOM_MUTATOR_LIBRARY=./my_mutator.so \
AFL_CUSTOM_MUTATOR_ONLY=1 \
    afl-fuzz -i seeds/ -o out/ -- ./http_server @@
```

---

## 官方 Custom Mutator 範例

AFL++ 的 `custom_mutators/` 目錄提供幾個可以直接用的 mutator：

| 目錄 | 功能 | 適用場景 |
|------|------|----------|
| `grammar_mutator/` | libprotobuf-mutator 整合，用 protobuf schema 定義 grammar | Protobuf-based protocols, 可自定義 grammar |
| `radamsa/` | 整合 Radamsa（著名的 black-box fuzzer），提供額外 mutation 策略 | 已有 Radamsa 的環境，快速增加多樣性 |
| `symcc_simple/` | 整合 SymCC（symbolic execution compiler）| 想用 concolic execution 突破 magic byte 比較 |
| `libfuzzer/` | 讓 libFuzzer 的 corpus 和 mutation 被 AFL++ 使用 | 混合 libFuzzer + AFL++ |

---

## 對比與取捨

| 策略 | 開發成本 | 效能 | 效果（結構化 input） | 適用場景 |
|------|----------|------|----------------------|----------|
| 純 havoc（預設） | 零 | 最快 | 差（到不了深層邏輯） | 簡單格式、C 字串解析 |
| Python custom mutator | 低（1-2 天） | 慢 5-10x | 好 | 快速驗証想法，格式不複雜 |
| C custom mutator | 中（3-5 天） | 接近 havoc | 好 | 生產環境，需要跑長時間 |
| libprotobuf-mutator | 高（需要寫 .proto schema） | 中 | 最好（完全 grammar-aware） | Protobuf 格式，有明確 schema |
| 純 grammar fuzzer（如 Nautilus） | 高（需要定義完整 grammar） | 中 | 最好 | 已知完整語法的格式 |

---

## 踩雷集錦

1. **`afl_custom_fuzz()` 返回 0 的語意**：返回 0 不是錯誤，是「這次我選擇不做 mutation」。AFL++ 會 fallback 到自己的 havoc。如果你永遠返回 0，等於 mutator 沒作用。如果你設了 `AFL_CUSTOM_MUTATOR_ONLY=1` 卻返回 0，AFL++ 會直接用原始 seed 執行（沒有 mutation）。

2. **Python mutator 的速度懲罰是真實的**：Python mutator 在呼叫密集的情況下，每秒執行次數可能從 10,000+ 降到 1,000-2,000。如果你的 target 本身就很慢（< 500 exec/sec），Python 可能還好；如果 target 很快（> 5,000 exec/sec），就要考慮改用 C。

3. **`max_size` 必須遵守**：如果你的 `*out_buf` 指向的資料超過 `max_size`，AFL++ 不會幫你截斷——它會直接讀超出邊界，導致 AFL++ 本身崩潰或行為異常。永遠在返回前確認 `out_size <= max_size`。

4. **`*out_buf` 的生命週期**：如果你讓 `*out_buf` 指向你 mutator 內部的緩衝區（如上面 C 範例），這個緩衝區必須在下次 `afl_custom_fuzz()` 呼叫之前保持有效。不要返回 stack 上的指標。

5. **AFL_CUSTOM_MUTATOR_ONLY 的副作用**：設了這個環境變數後，AFL++ 的 deterministic stage（bit flip 等）也全部跳過。如果你的 custom mutator 沒有覆蓋到某些 mutation 類型，整體效果可能比不加差。先不設，測試有效後再決定是否加。

---

## 進階：再往深一層

**同時載入多個 mutator**：AFL++ 支援用冒號分隔多個 `.so`：
```bash
AFL_CUSTOM_MUTATOR_LIBRARY=./mutator_a.so:./mutator_b.so afl-fuzz ...
```
AFL++ 會依序呼叫每個 mutator 的 `afl_custom_fuzz()`，每次從不同 mutator 選一個（隨機）。

**`afl_custom_post_process()` 的正確用法**：這個 callback 在 AFL++ 完成它自己的 mutation 之後才呼叫。用途是「修正 AFL++ 破壞的東西」，例如：
- 重新計算 CRC32 checksum
- 修正 length field（AFL++ 可能改了內容但沒更新長度）
- 補上 magic number（AFL++ 可能翻轉掉了）

這樣你可以讓 AFL++ 的亂數 mutation 提供多樣性，再由你的 post_process 讓格式保持合法。

**Grammar Mutator 的選擇**：如果你需要完整 grammar 支援，考慮：
- [Nautilus](https://github.com/nautilus-fuzz/nautilus)：用 ANTLR4 語法定義 input grammar
- [libprotobuf-mutator](https://github.com/google/libprotobuf-mutator)：Protobuf schema 直接當 grammar
- [FormatFuzzer](https://github.com/uds-se/FormatFuzzer)：從 Binary Template（010 Editor 格式）生成 mutator

---

## 動手練習

1. **實作並測試 Python HTTP mutator**：
   - 準備一個簡單的 HTTP parsing target（可以用 `curl -s --data-binary @$1 http://localhost:8080` 包裝）
   - 實作 `http_mutator.py`，至少處理 3 種 header mutation 策略
   - 跑 30 分鐘，對比加 mutator 前後的 coverage（用 `afl-plot`）

2. **實作 checksum-fixing post_process**：
   - 找一個有 CRC32 checksum 的 binary format（PNG、ZIP 都有）
   - 先不加 mutator，測試 AFL++ 能跑多少 paths
   - 加一個只做 `afl_custom_post_process()` 的 mutator，重算 CRC32
   - 對比 crash 數量和 unique paths

3. **閱讀官方範例**：
   - 讀 `custom_mutators/example.py`（AFL++ 官方 Python 範例）
   - 讀 `custom_mutators/example.c`（C 版範例）
   - 找出哪些 callback 是必要的，哪些是選配的

---

## 本章重點整理

- AFL++ 的 custom mutator 讓你攔截 mutation 流程，對結構化 input（PDF、HTTP、Protobuf）做 format-aware 的變體，解決 format-agnostic mutation 對深層邏輯覆蓋率差的問題
- 六個主要 callback：`init`（初始化）、`fuzz`（主 mutation）、`post_process`（事後修正）、`queue_get`（seed 過濾）、`queue_new_entry`（新 seed hook）、`describe`（debug 標記）；不需要全部實作
- Python mutator 開發快但慢 5-10x；C mutator 效能接近原生；`AFL_CUSTOM_MUTATOR_ONLY=1` 可完全關閉 AFL++ 內建 mutation，但要確保你的 mutator 覆蓋夠廣

## 自我檢核

1. `afl_custom_fuzz()` 返回 0 會發生什麼事？如果同時設了 `AFL_CUSTOM_MUTATOR_ONLY=1` 呢？
2. Python mutator 和 C mutator 的效能差距主要來自哪裡？
3. `afl_custom_post_process()` 和 `afl_custom_fuzz()` 的呼叫順序是什麼？適合什麼用途？
4. 如果你有一個 PNG fuzzing 目標，你會選擇哪種 custom mutator 策略？為什麼？
5. `max_size` 如果被違反，AFL++ 會有什麼行為？

## 延伸閱讀

- **AFL++ `custom_mutators/README.md`**（https://github.com/AFLplusplus/AFLplusplus/blob/stable/custom_mutators/README.md）：核心貢獻：完整 API 規格和所有 callback 的語意說明；讀 callback 的 return value 那節；和本章的 API 介紹互補。

- **"Grammar-based Whitebox Fuzzing"（Godefroid et al., PLDI 2008）**：核心貢獻：最早提出把 input grammar 和 symbolic execution 結合，讓 fuzzer 能生成語法正確的 input；讀第 3 節（grammar model）；和本章的「為什麼需要 custom mutator」直接對應。

- **LibProtobuf-Mutator**（https://github.com/google/libprotobuf-mutator）：核心貢獻：把 Protobuf schema 當作 input grammar，自動生成 structure-aware mutator，已在 Chrome 和 OSS-Fuzz 廣泛使用；讀 `README.md` 的 AFL++ integration 那節；和本章的 C custom mutator 範例是同一層概念，但更完整。

→ [下一章：Ch 19 — Sanitizers：ASan、UBSan、MSan 與 AFL++ 整合](19-sanitizers.md)
