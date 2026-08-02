# Ch 12 — libprotobuf-mutator 與 FuzzTest

> **目標**: 理解 structure-aware fuzzing 的核心工具鏈——用 libprotobuf-mutator (LPM) 把 libFuzzer 的 mutation 層從 bytes 提升到 protobuf message 層，再看 Google FuzzTest 如何用 Domain API 做更高層的結構約束。

> **環境**:
> - WSL2, Ubuntu 22.04
> - `sudo apt install clang-14 protobuf-compiler libprotobuf-dev`
> - LPM: `sudo apt install libprotobuf-mutator-dev`（若 apt 有則直接用；Ubuntu 22.04 repo 通常有 0.x 版，若沒有則需從源碼 build）
> - 從源碼 build LPM（若 apt 無）:
>   ```bash
>   git clone https://github.com/google/libprotobuf-mutator
>   cd libprotobuf-mutator
>   mkdir build && cd build
>   cmake .. -GNinja -DCMAKE_C_COMPILER=clang-14 -DCMAKE_CXX_COMPILER=clang++-14 \
>     -DLIB_PROTO_MUTATOR_DOWNLOAD_PROTOBUF=ON
>   ninja
>   sudo ninja install
>   ```
> - FuzzTest: 目前主要透過 Bazel 或 CMake 引入，無 apt 套件；參考 https://github.com/google/fuzztest 的 CMake 整合說明
> - 本章 LPM harness 範例為**理論預期行為**（若環境無 libprotobuf-mutator-dev），但 protobuf 基礎操作可在有 libprotobuf-dev 的環境真跑

---

## 為什麼需要 structure-aware fuzzing

Ch 11 結尾留下了一個痛點：checksum 與 magic bytes 把 fuzzer 擋在解析邏輯門外。更普遍的問題是**結構化輸入**。

考慮一個接受 HTTP/2 frame 的 parser，或一個讀 protobuf 序列化配置的服務。dumb mutation 每次隨機翻位元，產生的「輸入」絕大多數在 parsing 第一行就被拒絕——連業務邏輯都碰不到。即便你加 corpus，libFuzzer 的 bytes-level mutation 也很難從一個有效的 protobuf binary 突變出另一個有效的 protobuf binary：一個 varint 的長度欄位改錯，整個 message 就廢了。

coverage 停滯的根本原因：**mutation 在 bytes 層操作，但合法性約束在 schema 層**。兩個層次不匹配，fuzzer 大部分算力在試不合法的輸入。

LPM 的答案直接：把 mutation 也搬到 schema 層。

---

## 先建立直覺

```
Dumb mutation（bytes 層）
────────────────────────
  harness(const uint8_t* data, size_t size)
        ↑
  random byte flips / splices
        ↑
  [0x0a 0x03 0x75 0x72 0x6c ...]  ← 隨機翻了第 3 byte
        → 多半變成 protobuf 解析失敗，harness 直接 return
        → coverage 原地踏步

LPM（message 層）
─────────────────
  DEFINE_PROTO_FUZZER(const MyMessage& msg)
        ↑
  ProtobufMutator::Mutate(msg)
    ├─ flip int32 field 值
    ├─ add repeated field element
    ├─ delete repeated field element
    └─ 修改 string field 長度或內容
        ↑
  永遠產生合法的 MyMessage encoding
        → 每次 mutation 都直接進業務邏輯
        → coverage 持續增長

MyMessage {
  required string url  = 1;   ← mutation: 改 url 路徑、加特殊字元
  optional int32  port = 2;   ← mutation: 試 0, -1, 65535, 65536
  repeated Header hdr  = 3;   ← mutation: add/remove/modify header
}
```

重點：LPM 不保證輸入在「應用層」合法（port 可以是 -1），它只保證輸入是**合法的 protobuf encoding**——讓 fuzzer 能跳過 parsing 層，直接打到應用邏輯的各種邊界。

---

## Section A: libprotobuf-mutator

### 定義 schema

LPM 的第一步是寫 `.proto` 檔，描述 fuzzer 輸入的結構。Schema 設計直接影響 fuzzing 效果，這一點後面踩雷會細說。

```proto
// calc_input.proto
syntax = "proto2";

message CalcInput {
  required sint32 a      = 1;
  required sint32 b      = 2;
  required bool   do_div = 3;
}
```

`sint32` 用 zigzag encoding，負數 mutation 代價低，比 `int32` 更容易讓 fuzzer 試到負值邊界。

### 寫 harness

```cpp
// calc_fuzz.cc
#include "libprotobuf-mutator/src/libfuzzer/libfuzzer_macro.h"
#include "calc_input.pb.h"
#include <cstdint>

// 目標：一個有整數除以零 bug 的計算器
static int32_t calc(int32_t a, int32_t b, bool do_div) {
    if (do_div) {
        return a / b;  // BUG: b == 0 時觸發 SIGFPE
    }
    return a + b;
}

DEFINE_PROTO_FUZZER(const CalcInput& input) {
    calc(input.a(), input.b(), input.do_div());
}
```

`DEFINE_PROTO_FUZZER` 是 LPM 提供的 macro，它展開成 `LLVMFuzzerTestOneInput`，在內部做 protobuf 解序列化再呼叫你的 lambda。你不需要自己處理 bytes。

### Build 與執行

```bash
# 產生 protobuf C++ 程式碼
protoc --cpp_out=. calc_input.proto

# Build（假設 LPM 已安裝到系統路徑）
clang++-14 -std=c++17 -fsanitize=fuzzer,address \
  calc_fuzz.cc calc_input.pb.cc \
  -I. \
  -lprotobuf \
  -lprotobuf-mutator-libfuzzer \
  -o calc_fuzz

./calc_fuzz
```

> **注意**: 若 LPM 從源碼 build，`-I` 需指向 LPM 源碼目錄，`-L` 需指向 build 輸出目錄。以下輸出為**理論預期行為**。

預期輸出（fuzzer 很快找到 divide-by-zero）:

```
INFO: Running with entropic power schedule (0xFF, 100).
INFO: Seed: 2847563190
INFO: Loaded 0 modules   (... edges)
INFO: -max_len is not provided; libFuzzer will not generate inputs larger than 4096 bytes
#2      INITED cov: 4 ft: 5 corp: 1/1b exec/s: 0 rss: 35Mb
#7      NEW    cov: 7 ft: 10 corp: 2/4b ...
...
SUMMARY: AddressSanitizer: FPE on unknown address
  in calc(int, int, bool) calc_fuzz.cc:9
  in LLVMFuzzerTestOneInput
```

為什麼這麼快？因為 LPM mutation 每次都產生合法的 `CalcInput`，`do_div=true` 且 `b=0` 的組合幾輪就會被覆蓋到。如果用 dumb mutation，fuzzer 必須碰巧在正確的 byte 位置對齊 `do_div` 的 bool encoding 與 `b` 的 varint 0——這個機率要低得多。

### Corpus seed 的正確姿勢

雖然 LPM 能從空 corpus 跑，但給 seed 效果差很多。用 protobuf text format 寫 seed，再轉 binary：

```bash
# 準備 seed corpus
mkdir -p corpus
echo 'a: 100 b: 7 do_div: true'  | protoc --encode=CalcInput calc_input.proto > corpus/s1
echo 'a: -1  b: 1 do_div: false' | protoc --encode=CalcInput calc_input.proto > corpus/s2
echo 'a: 0   b: 0 do_div: true'  | protoc --encode=CalcInput calc_input.proto > corpus/s3
./calc_fuzz corpus/
```

上面這三行 `protoc --encode` 在有 `protobuf-compiler` 與 `libprotobuf-dev` 的環境**可以真跑**，不依賴 LPM。

---

## 底層機制

```
LPM mutation 在 libFuzzer 框架內的位置
────────────────────────────────────────

  libFuzzer 主循環
       │
       ▼
  選取 corpus 中的 input (bytes)
       │
       ▼
  呼叫 LLVMFuzzerMutate(data, size, max_size)
       │                ↑ 這個 hook 被 LPM 攔截
       ▼
  LPM: 嘗試 ParseFromString(data) → Message M
       │  若解析失敗 → 用空 Message 繼續
       ▼
  ProtobufMutator::Mutate(&M, seed)
    ├─ MutateField(M, field_index, seed)
    │    ├─ int32/sint32: 隨機算術 mutation (±1, ×2, random)
    │    ├─ string: flip bytes, change length, insert/delete
    │    └─ bool: flip
    ├─ AddField(M, repeated_field)    → 在 repeated 加一個 element
    ├─ DeleteField(M, repeated_field) → 刪一個 element
    └─ (客製化 mutation 可繼承 ProtobufMutator 覆寫這些方法)
       │
       ▼
  M.SerializeToString(&mutated_bytes)
       │
       ▼
  libFuzzer 拿 mutated_bytes 跑 harness
       │
       ▼
  DEFINE_PROTO_FUZZER macro 展開的 LLVMFuzzerTestOneInput:
    ParseFromString(data) → CalcInput input
    → 呼叫你的 fuzzing body
       │
       ▼
  coverage feedback → 有新 edge → 加入 corpus
```

關鍵：mutation 與 serialization 都在 LPM 內部完成，libFuzzer 只看到 bytes，不知道這些 bytes 是 protobuf。這讓 LPM 能套在任何支援 `LLVMFuzzerMutate` hook 的 coverage-guided fuzzer 上。

---

## 客製化 mutation（進階）

預設的 ProtobufMutator 對所有 field 機率相等。對某些目標，你希望某個 field 被 mutation 更頻繁，或加入 domain-specific 的突變邏輯：

```cpp
// 繼承 libprotobuf_mutator::ProtobufMutator
#include "libprotobuf-mutator/src/mutator.h"

class SqlMutator : public libprotobuf_mutator::ProtobufMutator {
public:
    // 覆寫 string field 的 mutation：加入 SQL 注入 payload
    void MutateString(std::string* value, int size_increase_hint) override {
        static const char* kSqlPayloads[] = {
            "' OR '1'='1",
            "'; DROP TABLE users; --",
            "1 UNION SELECT NULL--",
        };
        if (random() % 3 == 0) {
            *value = kSqlPayloads[random() % 3];
            return;
        }
        // fallback 到預設 mutation
        ProtobufMutator::MutateString(value, size_increase_hint);
    }
};
```

這種做法在 SQL parser fuzzing、URL parser fuzzing 等場景效果顯著：你把 domain knowledge 注入 mutation 層，不需要靠 coverage feedback 從零學習這些 pattern。

---

## Section B: Google FuzzTest

### 定位

FuzzTest 是比 LPM 更高層的框架。它不依賴 protobuf——你用 **Domain API** 直接在 C++ 裡描述輸入的結構，然後 FuzzTest 在底層負責 mutation 與 coverage guidance。

更重要的是：FuzzTest 讓 fuzzing 與 unit test 住在同一個 binary。同一個 test case 可以在 CI 跑固定 seed（`--test` mode），也可以在 fuzzing session 跑持續 mutation（`--fuzz` mode）。

### Domain API 基礎

```cpp
#include "fuzztest/fuzztest.h"
#include "gtest/gtest.h"

// 最簡單：對任意 string 做 fuzzing
void ParseUrl(const std::string& url) {
    MyUrlParser::Parse(url);  // 不能 crash
}
FUZZ_TEST(ParserTests, ParseUrl)
    .WithDomains(fuzztest::Arbitrary<std::string>());
```

`Arbitrary<std::string>()` 告訴 FuzzTest 這個參數可以是任意字串，它會做 coverage-guided mutation。

### 結構化 Domain

```cpp
struct Config {
    int port;
    std::string host;
    bool tls;
};

void ParseConfig(const Config& cfg) {
    MyServer::Connect(cfg.host, cfg.port, cfg.tls);
}

FUZZ_TEST(ServerTests, ParseConfig)
    .WithDomains(
        fuzztest::StructOf<Config>(
            fuzztest::InRange(1, 65535),          // port: 只試合法範圍
            fuzztest::PrintableAsciiString(),      // host: 只試可列印字元
            fuzztest::Arbitrary<bool>()            // tls: 任意 bool
        )
    );
```

`StructOf` 把多個 Domain 組合成一個 struct 的 Domain。FuzzTest 的 mutation 會在 Domain 定義的約束內進行。

### 常用 Domain 組合器

```cpp
// 從固定集合選一個值
fuzztest::ElementOf({200, 400, 404, 500})

// 整數範圍
fuzztest::InRange(0, 255)

// 只含 printable ASCII 的字串
fuzztest::PrintableAsciiString()

// 長度受限的字串（只含 a-z）
fuzztest::StringOf(fuzztest::InRange('a', 'z')).WithMaxSize(64)

// vector，元素個數受限
fuzztest::VectorOf(fuzztest::Arbitrary<uint8_t>()).WithMaxSize(16)

// optional 欄位
fuzztest::OptionalOf(fuzztest::InRange(1, 100))
```

### 執行模式

```bash
# unit test 模式（CI 用，跑固定 seed，秒級結束）
./my_fuzz_test --test

# fuzzing 模式（持續跑到找到 crash 或手動中止）
./my_fuzz_test --fuzz=ServerTests.ParseConfig

# 指定 fuzzing 時間後自動停止
./my_fuzz_test --fuzz=ServerTests.ParseConfig --fuzz_for=60s
```

---

## 對比取捨表

| 特性 | raw libFuzzer | LPM | FuzzTest |
|------|---------------|-----|----------|
| input 描述方式 | 無（純 bytes） | .proto schema | Domain API (C++) |
| 語言 | C/C++ | C++ | C++ |
| 結構保證 | 無 | protobuf 合法 encoding | domain 約束 |
| 與 unit test 整合 | 無 | 無 | 有（同 binary，兩種 mode）|
| 適合已有 schema 的目標 | 差 | 優（直接用 .proto）| 可（需重寫成 Domain）|
| Build 複雜度 | 低 | 中（需 LPM + protobuf）| 中-高（Bazel/CMake 設定）|
| 客製化 mutation | 自己實作 hook | 繼承 ProtobufMutator | 組合 Domain 組合器 |
| stateful protocol | 需自己管狀態 | `repeated Action` pattern | Domain 可描述 action sequence |
| 適合場景 | 任意 bytes input | 有 .proto 定義的協定 | 有 C++ unit test 的 codebase |

選法：
- 目標已經有 `.proto` schema → LPM，直接重用 schema
- 目標是有 unit test 的 C++ codebase，想把 fuzz test 和 unit test 統一管理 → FuzzTest
- 目標是 binary protocol 但沒有 schema，或你要最大彈性 → raw libFuzzer + 自己寫 structure-aware mutator

---

## 踩雷

### 踩雷 1：空 corpus 照跑，但效率很差

LPM 從空 corpus 啟動時，第一個 mutation 的出發點是**空 Message**——所有 required field 都是預設值（0, "", false）。這個出發點非常差：fuzzer 需要更多步驟才能走到有趣的欄位組合。

做法：用 protobuf text format 準備幾個代表性的有效輸入作 seed，轉成 binary 放 corpus 目錄。5 個好 seed 能讓初始 coverage 提升數倍，後續 mutation 的出發點也更多樣。

空 corpus 能跑是事實；但「能跑」和「應該這樣跑」是兩件事。在有 seed 的情況下，fuzzer 平均要少花 3-10 倍的 exec 才能達到同樣的 coverage 深度。

### 踩雷 2：schema 設計過度限制，把 bug 鎖在門外

直覺告訴你：把 schema 限制得越嚴，fuzzer 就越快找到業務 bug。實際情況相反。

```proto
// 過度限制的例子：看似嚴謹，實際上自廢武功
message HttpRequest {
  enum Method { GET = 0; POST = 1; PUT = 2; DELETE = 3; }
  required Method method = 1;   // 只試 4 個合法動詞
  required string path   = 2;   // 若再用 regex 限制，更慘
  required int32  port   = 3;   // InRange(80, 443) 只試兩個值
}
```

這樣的 schema 讓 fuzzer 永遠不會試 `method = "INVALID"`、`path = "../etc/passwd"`、`port = 0`。而這些邊界恰好是 parser 最可能有 bug 的地方。

原則：**schema 只做 encoding 層的結構保證，不做應用層的值域限制**。讓 fuzzer 自由試所有合法的 protobuf encoding 值，即便那些值在應用層「不合理」。對確認完全不影響覆蓋率的欄位，才考慮限制其值域。適度保留 `bytes` 欄位讓 fuzzer 仍能測試 encoding 邊界。

### 踩雷 3：以為 FuzzTest 的 `--test` mode 是假 fuzzing

有人看到 FuzzTest 可以跑 unit test，以為 `--test` mode 才是 fuzzing，`--fuzz` mode 是什麼額外功能。

實際上：
- `--test` mode：只跑 corpus 裡已有的 seed（包括 regression seed），固定輸入，用於 CI 確保已知 bug 不復發
- `--fuzz` mode：coverage-guided 持續 mutation，才是真正的 fuzzing

這個設計的好處是 CI pipeline 跑 `--test`，幾秒就結束；定期跑 `--fuzz` session 發現新 crash 後，把觸發 crash 的輸入存入 corpus，下次 `--test` 就會 regression check 這個 case。

別因為「它有 unit test mode」就誤認為 FuzzTest 不是真的 fuzzer。在 `--fuzz` mode 下，它做的事和 libFuzzer 沒有本質差異——只是輸入描述更高層。

---

## 進階延伸

### LPM + stateful protocol

LPM 最強的進階用法是把「一次 fuzzing input」變成「一個 API call sequence」：

```proto
// stateful_input.proto
syntax = "proto2";

message OpenFile  { required string path  = 1; }
message ReadChunk { required int32  size  = 1; }
message SeekPos   { required int64  offset = 1; }
message CloseFile {}

message Action {
  oneof action {
    OpenFile  open  = 1;
    ReadChunk read  = 2;
    SeekPos   seek  = 3;
    CloseFile close = 4;
  }
}

message FuzzSession {
  repeated Action actions = 1;
}
```

Harness 依序執行 `actions`，模擬真實的 API 使用順序。這讓 fuzzer 能探索**狀態機**的邊界，而不只是單一 call 的邊界。Ch 16 stateful fuzzing 會深入這個方向。

### FuzzTest + property-based testing

FuzzTest 的另一面：你可以同時指定「輸入約束」和「輸出不變量」，讓它跑 property-based testing：

```cpp
void SortIsIdempotent(std::vector<int> v) {
    auto sorted_once  = Sort(v);
    auto sorted_twice = Sort(sorted_once);
    // 排序兩次結果應該和排序一次相同
    EXPECT_EQ(sorted_once, sorted_twice);
}
FUZZ_TEST(SortTests, SortIsIdempotent)
    .WithDomains(fuzztest::VectorOf(fuzztest::Arbitrary<int>())
                 .WithMaxSize(1000));
```

這比「找 crash」更強：你在告訴 fuzzer「這個性質任何輸入都要成立」，任何違反的 case 都是 bug，不需要 crash 就能觸發回報。

---

## 動手練習

1. **練習 A（protobuf 基礎，可真跑）**: 安裝 `libprotobuf-dev` 與 `protobuf-compiler`，寫一個 `.proto` 定義一個 `LoginRequest`（含 username string、password string、remember_me bool），用 `protoc` 產生 C++ code，手動寫一個小程式序列化再解序列化，確認欄位值一致。目標：熟悉 protobuf 工作流程，為 LPM harness 打底。

2. **練習 B（LPM schema 設計，理論分析）**: 給你一個 DNS query parser，它接受 `name`（string）、`qtype`（uint32）、`qclass`（uint32）。設計兩版 `.proto` schema：版本 A 對 qtype/qclass 用 enum 嚴格限制到標準值（A=1, NS=2, MX=15...）；版本 B 用 `uint32` 不加限制。分析兩個版本各自會覆蓋到哪些 code path，哪個版本更可能找到 parser bug，寫出你的推論（不需要實際跑）。

3. **練習 C（FuzzTest Domain 設計）**: 假設你要 fuzz 一個函式 `ParseIpv4(const std::string&) -> uint32_t`。用 FuzzTest 的 Domain API 設計兩個版本：版本 A 用 `Arbitrary<std::string>()`；版本 B 用 `StructOf` 組合四個 `InRange(0, 255)` 的 int，再在 harness 內格式化成 `"a.b.c.d"` 字串。推論：哪個版本會更快找到 IP parser 的 edge case？哪個版本會找到 format string 層的 bug？什麼情況下你應該兩個都跑？

4. **挑戰題（若環境有 LPM）**: 把 Ch 11 的 checksum 問題用 LPM 解決。定義一個 proto schema，把 checksum 欄位移出 fuzzer 控制——在 harness 內部計算正確的 checksum 並填入，讓 LPM 只 mutate payload 欄位。比較這個做法與 Ch 11 magic bytes patch 做法的異同：各自需要了解多少目標格式的細節？哪個更容易維護？

---

## 本章重點

- dumb mutation 在 bytes 層，LPM mutation 在 protobuf message 層——後者每次都產生合法 encoding，直接打到業務邏輯
- LPM harness 核心是 `DEFINE_PROTO_FUZZER(const MyProto& input)`，protobuf 解序列化由 LPM macro 自動處理
- Schema 設計原則：只做 encoding 結構保證，不做應用層值域限制；過度限制會把 bug 鎖在門外
- 空 corpus 能跑但效率差，給代表性 seed 能讓初始 coverage 提升數倍
- FuzzTest 用 Domain API 在 C++ 層描述結構，與 unit test 同 binary；`--test` 跑固定 seed，`--fuzz` 才是持續 mutation
- LPM + `repeated Action` 是處理 stateful protocol 的標準模式（Ch 16 深入）
- 底層機制：LPM 攔截 `LLVMFuzzerMutate` hook，做 deserialize → field mutation → reserialize，libFuzzer 只看到 bytes

---

## 自我檢核

- [ ] 說得出 LPM mutation 比 bytes-level mutation 在 protobuf 目標上快找 bug 的具體原因（不是「結構化」這個詞，而是 mutation 每次都合法 encoding 這件事）
- [ ] 如果有人把 proto schema 的 port 欄位用 enum 限制到 80/443，能說出這樣做的代價是什麼
- [ ] 解釋 FuzzTest 的 `--test` 與 `--fuzz` mode 各自的用途，以及它們在 CI pipeline 裡如何配合
- [ ] 能畫出 LPM mutation 流程的三個步驟：deserialize → mutate fields → reserialize，並說明 libFuzzer 在哪個層次與 LPM 互動
- [ ] 知道「LPM 從空 corpus 能跑」和「應該給 seed」這兩件事都是真的，且不矛盾

---

## 延伸閱讀

1. Kostya Serebryany, "Structure-Aware Fuzzing with libFuzzer" (Google Security Blog, 2017)
   — LPM 的源頭文章。解釋為何對 protobuf 目標做 bytes-level mutation 幾乎是死路，以及 structure-aware mutation 的設計動機。讀全文，特別是「Why Naive Fuzzing Fails on Protobuf」那一節。這篇文章也解釋了為何 LPM 選擇攔截 `LLVMFuzzerMutate` 而不是 `LLVMFuzzerTestOneInput`。

2. libprotobuf-mutator GitHub: https://github.com/google/libprotobuf-mutator
   — 讀 `README.md` 的 "Fuzzing other data formats" 章節與 `examples/` 目錄（特別是 `examples/libfuzzer/` 下的結構）。這裡有 LPM 套在 XML parser 上的完整範例，看完就懂如何把 LPM 應用到非 protobuf 目標——關鍵是 LPM 本質是個 message-level mutator，只要你能把輸入序列化成 protobuf，任何格式都能套。

3. FuzzTest GitHub: https://github.com/google/fuzztest — 讀 `doc/domains-reference.md`
   — Domain API 的完整參考。`ElementOf`、`StructOf`、`Arbitrary`、`OneOf`、`VectorOf` 的組合方式都在這裡。搭配 `doc/fuzz-test-macro.md` 看 `FUZZ_TEST` macro 的完整語法，以及如何在 CMake 與 Bazel 中引入 FuzzTest 依賴。

4. "libFuzzer – A Library for Coverage-Guided Fuzz Testing", LLVM 文件
   — 特別讀 "Custom Mutators" 一節。理解 `LLVMFuzzerCustomMutator` hook 的介面，才能看懂 LPM 是如何插入這個 hook 的，以及為何 LPM 能透明地疊在 libFuzzer 上而不需要修改 libFuzzer 本身。

---

Ch 11 用 patch magic bytes 和 checksum 解了「能進 parser」的問題；本章把 mutation 層直接搬到 message 層，解了「mutation 大多產生垃圾」的問題。這兩個工具針對的都是**結構化協定**——輸入有明確 schema 的目標。

下一章轉換場景：**語言類目標**（script interpreter、template engine、compiler）的輸入是語法樹而不是 message——protobuf schema 無法描述它，你需要的是 **grammar fuzzing**。

→ [下一章](./13-grammar-fuzzing.md)
