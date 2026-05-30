# Ch 17 — Harness Design：為任意 Target 量身打造進入點

> **目標**：能根據 target 的特性設計正確的 fuzz harness，涵蓋 library API、CLI tool、stateful protocol 三種典型場景。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

AFL++ 不能直接 fuzz 任何程式，它需要一個**進入點**：一段膠水 code，負責把 fuzzer 產生的 bytes 餵給 target 的邏輯。
這段 code 叫做 **harness（測試線束）**。

Harness 的品質直接決定 fuzzing 的效果：

- 差的 harness：每次 iteration 只走到 target 的第一個 if 就 return，永遠找不到深層 bug
- 好的 harness：把 input bytes 解讀成 target 期待的格式，讓 fuzzer 能探索最多的 code path

寫 harness 沒有通用公式，但有三種典型場景——library API、CLI tool、network service——各自有固定的設計模式。

---

## 先建立直覺

把 fuzzer 想成一個會說「我給你 bytes」的機器人，target 是一個「只接受特定格式輸入」的工廠。

Harness 就是工廠門口的翻譯員：
- 接過機器人給的 bytes
- 把它翻譯成工廠能理解的格式（struct、file handle、command args 等）
- 把工廠的處理結果報告給機器人（crash 就是報警）

好的翻譯員：讓機器人的每種輸入都能進到工廠最深處。
壞的翻譯員：大部分輸入在門口就被丟掉了，機器人找不到工廠的 bug。

---

## 橫向連結

- **Ch 16（Persistent Mode）**：library harness 的標準形式就是 persistent mode harness。
- **Ch 15（CmpLog）**：好的 harness + CmpLog 組合，是應對 magic bytes 的標準解法。
- **Ch 11（Forkserver）**：CLI tool harness 通常搭配 deferred forkserver。

---

## 三種 Target 類型

### A. Library API（最理想）

Library 是最容易 fuzz 的 target：你有 source code，能直接呼叫內部函式，不需要 spawn process。

設計原則：
- 找到最底層的 parsing / processing 函式，直接呼叫（跳過 CLI 解析、錯誤訊息輸出等）
- 用 persistent mode
- 用 SHM input（`__AFL_FUZZ_TESTCASE_BUF`）

**範例：fuzz libxml2 的 XML 解析**

```c
#include <stdint.h>
#include <stddef.h>
#include <libxml/parser.h>
#include <libxml/tree.h>

// 告訴 libxml2 不要輸出錯誤訊息到 stderr（否則 AFL 的 output 很難看）
static void xml_error_handler(void *ctx, const char *msg, ...) {
    (void)ctx; (void)msg;
}

__AFL_FUZZ_INIT();

int main(void) {
    // 一次性初始化
    xmlInitParser();
    xmlSetGenericErrorFunc(NULL, xml_error_handler);

    while (__AFL_LOOP(1000)) {
        const uint8_t *buf = __AFL_FUZZ_TESTCASE_BUF;
        size_t         len = __AFL_FUZZ_TESTCASE_LEN;

        // 直接呼叫 parser，不開 file
        xmlDocPtr doc = xmlReadMemory(
            (const char *)buf, (int)len,
            "noname.xml",   // 假的 filename，libxml2 需要這個
            NULL,           // encoding: auto-detect
            0               // options
        );

        // 不管 parse 成不成功，都要 free（避免 memory leak 累積）
        if (doc) {
            xmlFreeDoc(doc);
        }
    }

    xmlCleanupParser();
    return 0;
}
```

```bash
# 編譯
afl-clang-fast -o fuzz_xml fuzz_xml.c $(xml2-config --cflags --libs)

# 啟動（搭配 CmpLog，因為 XML 有大量 magic bytes）
AFL_LLVM_CMPLOG=1 afl-clang-fast -o fuzz_xml_cmplog fuzz_xml.c $(xml2-config --cflags --libs)
afl-fuzz -c ./fuzz_xml_cmplog -i seeds/ -o out/ -- ./fuzz_xml
```

### B. CLI Tool（一般）

CLI tool 是最常見的 target。你通常有 source code（或至少能 patch），可以加 forkserver 插樁。

設計原則：
- 用 `@@` 讓 AFL++ 把 input 寫成 temp file 傳給 target
- 如果有重型初始化（如讀設定檔、初始化 runtime），用 deferred forkserver 推遲 fork 到初始化完成後
- 避免讓 target 做網路連線、spawn subprocess

**範例：fuzz binutils readelf**

```bash
# 直接使用（不修改 source）
# 用 afl-clang-fast 重新編譯 readelf
CC=afl-clang-fast ./configure
make

# 啟動 fuzzing
echo "ELF" > seeds/seed1  # 簡單的 ELF magic bytes seed
afl-fuzz -i seeds/ -o out/ -- ./readelf -a @@
```

如果想加 deferred forkserver（減少每次 fork 的初始化成本）：

```c
// 在 readelf 的 main() 裡，找到初始化完成的位置，加入：
__AFL_INIT();  // 在這個點 fork，跳過之前的初始化開銷
```

### C. Network Service（困難）

Network service 是最難 fuzz 的 target 類型。AFL++ 本身設計來傳遞 file 或 stdin，不直接支援 socket。

**選項 1：改 source，讓它從 stdin 讀**

```c
// 原始 code
int sock = accept(server_fd, NULL, NULL);
handle_connection(sock);

// 修改後：把 stdin 當成 socket
// 在 debug/testing 模式下，直接處理 stdin
if (getenv("AFL_FUZZ_MODE")) {
    handle_connection(STDIN_FILENO);  // stdin 當作已連線的 socket
} else {
    int sock = accept(server_fd, NULL, NULL);
    handle_connection(sock);
}
```

**選項 2：用 `preeny` desocketing**

`preeny` 是一個 LD_PRELOAD 函式庫，攔截 `accept()` 呼叫，把它替換成從 stdin 讀：

```bash
# 安裝 preeny
git clone https://github.com/zardus/preeny && cd preeny && make

# 使用
AFL_PRELOAD=./preeny/x86_64-linux-gnu/desock.so \
afl-fuzz -i seeds/ -o out/ -- ./target_server
```

**選項 3：AFL-net（專為 network protocol 設計）**

適合有完整 stateful protocol 的 target（如 HTTP server），但設定複雜，throughput 很低。
通常只在「target 完全無法改 source」時才考慮。

---

## 好的 Harness 的特徵

### 1. 不要在 invalid input 上 crash（要 return，不要 exit(-1)）

```c
// 錯誤的 harness
int main(int argc, char **argv) {
    if (argc < 2) exit(1);      // AFL 認為這是 crash，但其實不是 bug
    FILE *f = fopen(argv[1], "rb");
    if (!f) exit(1);            // 同上
    process_file(f);
    fclose(f);
    return 0;
}
```

AFL++ 用 exit code 和 signal 判斷是否 crash：
- `exit(0)` 或 `return 0`：正常執行
- 非零 exit code：通常不算 crash（取決於 AFL++ 的設定）
- SIGSEGV、SIGABRT、SIGFPE 等 signal：crash，記錄下來

如果你的 harness 在 invalid input 時 `exit(1)`，AFL++ 可能把每個 input 都記錄為「crash」，或忽略所有這樣的 exit。
正確做法：遇到無效 input 就 `return 0`（優雅退出），讓 AFL++ 繼續嘗試其他 mutation。

### 2. 把所有 code path 暴露出來

```c
// 差的 harness：過度防守，讓 fuzzer 看不到 target 的邏輯
int fuzz_target(const uint8_t *data, size_t size) {
    if (size < 100) return 0;     // 過濾掉太短的 input
    if (size > 1000) return 0;    // 過濾掉太長的 input
    if (data[0] != 0x42) return 0; // 只接受特定 magic byte

    // fuzzer 能碰到的 code 只有這一小塊
    process(data, size);
    return 0;
}

// 好的 harness：讓 AFL++ 自己學到「有效的 input 長什麼樣」
int fuzz_target(const uint8_t *data, size_t size) {
    // 只過濾絕對必要的 case（如 NULL pointer dereference）
    if (size == 0) return 0;
    process(data, size);  // 讓 target 自己判斷 input 是否有效
    return 0;
}
```

### 3. 避免 I/O 操作

- 不要在 loop 裡開網路連線
- 不要在 loop 裡寫 log 到 disk
- 不要讓 target print 大量 output 到 stdout/stderr（會拖慢速度）

```bash
# 讓 target 的 stderr 輸出消失
afl-fuzz -i seeds/ -o out/ -- ./target @@ 2>/dev/null
```

或在 harness 裡 redirect：

```c
// 在 __AFL_FUZZ_INIT() 之前
int devnull = open("/dev/null", O_WRONLY);
dup2(devnull, STDERR_FILENO);
close(devnull);
```

---

## 常見 Harness 錯誤

**對比：壞的 harness vs 好的 harness**

```c
// ========================================
// 壞的 harness（用 file I/O + exit）
// ========================================
int main(int argc, char **argv) {
    if (argc < 2) exit(1);          // 錯：exit code 1 不是 crash signal
    FILE *f = fopen(argv[1], "rb");
    if (!f) exit(1);                // 錯：同上
    process(f);
    fclose(f);
    return 0;
    // 問題：每次 iteration 都有 file open/close overhead
    // 問題：exit(1) 讓 AFL++ 混亂
    // 問題：沒有 persistent mode，throughput 低
}

// ========================================
// 好的 harness（LibFuzzer 介面，AFL++ 也支援）
// ========================================
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    // 直接用 data/size，不開 file
    parse_internal(data, size);
    return 0;  // 永遠 return 0；crash（SIGSEGV 等）才是 bug signal
}

// ========================================
// 更好的 harness（AFL++ persistent mode）
// ========================================
__AFL_FUZZ_INIT();

int main(void) {
    while (__AFL_LOOP(1000)) {
        const uint8_t *data = __AFL_FUZZ_TESTCASE_BUF;
        size_t          size = __AFL_FUZZ_TESTCASE_LEN;
        parse_internal(data, size);
        // 不 exit，繼續 loop
    }
    return 0;
}
```

---

## 底層機制：AFL++ 如何判斷 Crash

```
target process 執行結束
         │
         ▼
    afl-fuzz 收到 waitpid() 結果
         │
    ┌────▼────────────────────────────────────┐
    │  WIFEXITED(status)?                     │
    │  ├─ YES: exit code 是什麼？              │
    │  │    ├─ 0: 正常退出 → 不是 crash       │
    │  │    └─ 非 0: 根據 AFL_CRASH_EXITCODE  │
    │  │         ├─ 有設定且符合: 記為 crash   │
    │  │         └─ 未設定: 不是 crash        │
    │  └─ NO（被 signal 殺死）                │
    │       ├─ SIGSEGV, SIGABRT, SIGFPE, ...  │
    │       │  → 記錄為 crash                 │
    │       └─ SIGKILL（timeout）             │
    │          → 記錄為 timeout               │
    └─────────────────────────────────────────┘
```

這解釋了為什麼 harness 裡應該 `return 0` 而不是 `exit(1)`：
非零 exit code 預設不被視為 crash，你的 bug 被靜默忽略。

---

## OSS-Fuzz 風格的 Harness

Google 的 OSS-Fuzz 使用 `LLVMFuzzerTestOneInput()` 作為標準介面：

```c
// fuzz_target.c
#include <stdint.h>
#include <stddef.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    // 你的 fuzz 邏輯
    target_parse(data, size);
    return 0;
}
```

AFL++ 支援這個介面，透過 `utils/aflpp_driver/`：

```bash
# 用 AFL++ driver 編譯（使 LLVMFuzzerTestOneInput 兼容 AFL++）
afl-clang-fast fuzz_target.c /path/to/aflpp_driver/libAFLDriver.a -o fuzz_target_afl -ltarget_lib

# 正常啟動
afl-fuzz -i seeds/ -o out/ -- ./fuzz_target_afl
```

優點：一份 harness，同時支援 AFL++ 和 LibFuzzer（甚至 Honggfuzz）。
缺點：`LLVMFuzzerTestOneInput()` 本身不支援 persistent mode 的 state reset，需要手動管理。

---

## 對比與取捨

| 方式 | 開發複雜度 | Throughput | 適用場景 | State leak 風險 |
|------|-----------|-----------|---------|---------------|
| `@@` file harness | 低（直接重新編譯） | 低（有 disk I/O） | CLI tool，無法改 source 太多 | 無（每次 fork） |
| Deferred forkserver | 低（加一行） | 中（省掉初始化 fork 成本） | 有重型初始化的 binary | 無 |
| Persistent mode (`__AFL_LOOP`) | 中（改寫 main） | 高（省掉每次 fork） | Library，可改 source | 有，需手動管理 |
| Persistent + SHM input | 中（同上 + `__AFL_FUZZ_INIT`） | 最高 | Library，追求最高速度 | 有，需手動管理 |
| Network desocketing | 高（preeny 或改 source） | 低（協定 overhead） | Network service，無法改 source 架構 | 視 target 而定 |

---

## 踩雷集錦

**1. Harness 裡 call `exit()` 而不是 `return`**

```c
// 錯誤：exit() 繞過正常的 return path，AFL++ 的 exit code 追蹤混亂
void fuzz_one(const uint8_t *data, size_t size) {
    if (size < 4) exit(0);  // 這等於讓 process 直接結束
}

// 正確：用 return 提前結束這次 iteration
void fuzz_one(const uint8_t *data, size_t size) {
    if (size < 4) return;   // 只結束這個函式
}
```

**2. Target 在 invalid input 時 call `abort()` 但不是真正的 bug**

一些 library 在遇到「不合法」的輸入時，會主動 `abort()`（如 `assert()` 失敗）。
這會被 AFL++ 記錄為 crash，但可能是正常的防禦性檢查，不是你在找的 bug。

解法：
```bash
# 方法 1：過濾掉 SIGABRT（只在你確定 abort() 不是 bug signal 時）
# AFL_CRASH_ABORT=0 是 AFL++ 的 env var（查文件確認當前版本）

# 方法 2：在 harness 裡 wrap
#include <setjmp.h>
#include <signal.h>
# 攔截 SIGABRT，視為非 crash（前提：你確定它不是真正的 bug）
```

**3. 忘記處理 size 邊界**

```c
// 常見 bug：直接用 size 做索引，不檢查範圍
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    int type = data[0];           // 如果 size == 0，這是 undefined behavior
    int len  = data[1] | (data[2] << 8);  // 如果 size < 3，越界讀

    // 正確做法：
    if (size < 3) return 0;
    int type2 = data[0];
    int len2  = data[1] | (data[2] << 8);
    ...
}
```

**4. 把 fuzzer 產生的 noise 誤認為 target bug**

```c
// 錯誤：harness 本身的 bug 製造 false crash
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    char buf[100];
    memcpy(buf, data, size);  // 如果 size > 100，這是 harness 的 stack overflow
    process(buf);
    return 0;
}

// 正確：先截斷再處理
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    size_t actual_size = size > 100 ? 100 : size;
    char buf[100];
    memcpy(buf, data, actual_size);
    process(buf, actual_size);
    return 0;
}
```

**5. 在 harness 裡列印 target 的 warning/error 訊息**

Target library 通常有 error callback 機制。如果你沒有設置 quiet handler，所有的「parse error at line 42」都會輸出到 stderr，大幅拖慢 fuzzing 速度（I/O 很慢）。

```c
// 設置 quiet 的 error handler
xmlSetGenericErrorFunc(NULL, silent_error_handler);  // libxml2 範例
```

---

## 進階：再往深一層

### 結構感知 Harness（Structure-Aware Fuzzing）

當 target 的 input 有嚴格格式（如 protobuf、zip、ELF），純 byte-level mutation 效率很低。
你可以用 **custom mutator** 或 **structured fuzzing** 讓 AFL++ 產生更有效的 input：

```c
// 用 protobuf-mutator：讓 AFL++ 在 protobuf 結構層面做 mutation
#include "src/libfuzzer/libfuzzer_macro.h"
#include "my_proto.pb.h"

DEFINE_PROTO_FUZZER(const MyProto& input) {
    // input 是一個合法的 protobuf 物件
    process_proto(input);
}
```

AFL++ 的 custom mutator API（`AFL_CUSTOM_MUTATOR_LIBRARY`）讓你完全控制 mutation 邏輯，是 structure-aware fuzzing 的基礎。

### 多 Input Harness（Multi-Input Fuzzing）

某些 target 需要多個 input 協作（如 crypto API 需要 key + message）：

```c
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 16) return 0;

    // 用 input 的前 16 bytes 當 key，其餘當 message
    const uint8_t *key = data;
    const uint8_t *msg = data + 16;
    size_t msg_len = size - 16;

    crypto_process(key, 16, msg, msg_len);
    return 0;
}
```

這個設計讓 AFL++ 在同一個 input 裡同時變異 key 和 message。

### Harness 的 Code Coverage 分析

在開始 fuzzing 之前，用 `afl-showmap` 確認你的 harness 確實觸發了 target 的 code：

```bash
# 用 seed input 跑一次，看觸發了多少 edge
afl-showmap -o /dev/null -i seeds/ -- ./fuzz_target @@

# 輸出類似：
# Trace bitmap says 847 edges, 0 timeouts
# 如果只有幾十個 edge，harness 可能沒走到 target 的核心邏輯
```

也可以用 gcov 或 llvm-cov 做完整的 line coverage 分析，找出 harness 沒有覆蓋到的 code path。

---

## 動手練習

### 練習 1：為 libjpeg-turbo 寫 Harness

```bash
# 安裝
sudo apt install libjpeg-turbo8-dev

# 寫 harness
cat > fuzz_jpeg.c << 'EOF'
#include <stdint.h>
#include <stddef.h>
#include <jpeglib.h>
#include <setjmp.h>

struct my_error_mgr {
    struct jpeg_error_mgr pub;
    jmp_buf setjmp_buffer;
};

static void my_error_exit(j_common_ptr cinfo) {
    struct my_error_mgr *myerr = (struct my_error_mgr*)cinfo->err;
    longjmp(myerr->setjmp_buffer, 1);
}

__AFL_FUZZ_INIT();

int main(void) {
    while (__AFL_LOOP(1000)) {
        const uint8_t *data = __AFL_FUZZ_TESTCASE_BUF;
        size_t          size = __AFL_FUZZ_TESTCASE_LEN;

        struct jpeg_decompress_struct cinfo;
        struct my_error_mgr jerr;

        cinfo.err = jpeg_std_error(&jerr.pub);
        jerr.pub.error_exit = my_error_exit;

        if (setjmp(jerr.setjmp_buffer)) {
            jpeg_destroy_decompress(&cinfo);
            continue;  // libjpeg 拋出錯誤，不是 crash，繼續下一次 iteration
        }

        jpeg_create_decompress(&cinfo);
        jpeg_mem_src(&cinfo, data, size);

        if (jpeg_read_header(&cinfo, TRUE) == JPEG_HEADER_OK) {
            jpeg_start_decompress(&cinfo);
            // 讀取並丟棄 output（我們只關心 crash，不關心 output 內容）
            int row_stride = cinfo.output_width * cinfo.output_components;
            JSAMPARRAY buffer = (*cinfo.mem->alloc_sarray)(
                (j_common_ptr)&cinfo, JPOOL_IMAGE, row_stride, 1);
            while (cinfo.output_scanline < cinfo.output_height) {
                jpeg_read_scanlines(&cinfo, buffer, 1);
            }
            jpeg_finish_decompress(&cinfo);
        }

        jpeg_destroy_decompress(&cinfo);
    }
    return 0;
}
EOF

afl-clang-fast -o fuzz_jpeg fuzz_jpeg.c -ljpeg
```

### 練習 2：分析一個壞的 Harness

找到以下這個 harness 的所有問題：

```c
#include <stdio.h>
#include <stdlib.h>

extern int parse(const char *data, int len, int strict_mode);

int main(int argc, char **argv) {
    if (argc != 2) {
        printf("Usage: %s <file>\n", argv[0]);
        return 1;
    }

    FILE *f = fopen(argv[1], "r");
    if (!f) {
        perror("fopen");
        exit(-1);
    }

    char buf[65536];
    int n = fread(buf, 1, sizeof(buf), f);
    fclose(f);

    int result = parse(buf, n, 1);  // strict_mode=1 會 reject 大多數 fuzzer 輸入
    if (result < 0) {
        fprintf(stderr, "Parse error: %d\n", result);
        exit(1);
    }

    printf("Parse OK: result=%d\n", result);
    return 0;
}
```

問題清單（找到幾個算幾個，答案不只一個）：
1. 使用 `exit()` 而不是 `return`
2. `strict_mode=1` 過濾掉了大多數 fuzzer 輸入
3. 有 `printf`/`fprintf` 輸出（I/O 拖慢速度）
4. 沒有 persistent mode（每次 fork）
5. `fread` 用 text mode（`"r"`），binary data 可能被截斷

---

## 本章重點整理

- Harness 是把 fuzzer bytes 轉換成 target 能處理的格式的膠水 code；harness 的品質決定 fuzzing 能探索多深的 code path，壞的 harness 讓 fuzzer 永遠在門口打轉。
- 三種典型場景：library（直接呼叫 API + persistent mode，最理想）、CLI tool（`@@` + deferred forkserver，簡單直接）、network service（改 source 或 preeny desocket，最困難）。
- 好的 harness 三個原則：invalid input 用 `return 0` 不用 `exit()`、把所有 code path 暴露出來不過度防守、避免不必要的 I/O 操作。

---

## 自我檢核

1. 為什麼 harness 裡 `exit(1)` 是錯誤的？AFL++ 用什麼機制判斷「這次執行是否找到 bug」？
2. Library target 和 CLI tool target 在 harness 設計上的主要差異是什麼？
3. 為什麼「過度防守的 harness」（對 invalid input 直接 return）反而讓 fuzzing 效果更差？
4. Network service 無法直接用 AFL++ fuzz 的根本原因是什麼？三種解法各有什麼代價？
5. `LLVMFuzzerTestOneInput()` 介面的優點是什麼？和 `__AFL_LOOP()` 相比差在哪裡？
6. 在 harness 裡，如何用 `setjmp`/`longjmp` 處理 target library 的 `longjmp`-style error handling？

---

## 延伸閱讀

**Google OSS-Fuzz 的 harness 指南（https://google.github.io/oss-fuzz/getting-started/new-project-guide/）**
- 核心貢獻：Google 大規模 fuzz 開源 library 的實戰標準，包含 Dockerfile、build system 整合、harness 樣板
- 讀哪裡："Writing Fuzz Targets" 節，以及任何一個你熟悉的 library 的 `fuzz/` 目錄（如 curl、libpng）
- 和本章關聯：本章的設計原則在 OSS-Fuzz 的文件裡有大量實際案例驗證

**AFL++ `utils/aflpp_driver/`**
- 核心貢獻：官方的 LibFuzzer-compatible driver；讓 `LLVMFuzzerTestOneInput()` harness 在 AFL++ 下跑，並自動啟用 persistent mode
- 讀哪裡：`aflpp_driver.c` 的完整 source（約 200 行），看它如何把 LibFuzzer 介面橋接到 AFL++ 的 forkserver 協定
- 和本章關聯：了解「一份 harness 同時支援多個 fuzzer」的工程實作

**"FuzzBench: An Open Fuzzer Benchmarking Platform and Service"（Metzman et al., FSE 2021）**
- 核心貢獻：標準化的 fuzzer 對比方法論；分析不同 fuzzer 在不同 target 類型上的效果，揭示「harness 品質對 fuzzer 效果的影響遠大於 fuzzer 演算法本身」
- 讀哪裡：Section 3（benchmarking methodology）和 Section 5（結果分析）
- 和本章關聯：給你一個數字感——好的 harness 能讓 AFL++ 的效果超過演算法更複雜的 fuzzer

**libFuzzer 的 structure-aware fuzzing 文件（https://llvm.org/docs/LibFuzzer.html#structure-aware-fuzzing）**
- 核心貢獻：protobuf-mutator 的使用方式；說明什麼時候 byte-level mutation 不夠用、需要 structure-aware 方法
- 讀哪裡："Structure-Aware Fuzzing" 節
- 和本章關聯：本章的「進階」節提到 custom mutator，這份文件給你更完整的 structure-aware fuzzing 背景

→ [下一章：Ch 18 — Sanitizers：讓 Bug 更容易被發現](18-sanitizers.md)
