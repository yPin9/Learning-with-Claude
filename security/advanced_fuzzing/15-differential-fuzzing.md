# Ch 15 差分 Fuzzing

> **目標**: 理解差分 fuzzing 的 oracle 設計，掌握如何用多實作比對找到非 crash 的邏輯 bug，用真實 json vs ujson 差分找到實際行為差異，並能獨立撰寫差分 harness 應用於 CVE 獵捕。

---

## 為什麼需要差分 fuzzing

傳統 fuzzing 的 oracle 是 crash：程式丟出 SIGSEGV、SIGABRT，或 ASAN 拍桌，就知道找到 bug 了。這條路走得通，是因為「crash」這個訊號天然存在、不需要設計。

但邏輯 bug 不 crash。

JSON parser 接受了 RFC 明確禁止的 control character 在字串裡——不 crash，照樣回傳結果。壓縮庫在某個特殊 byte pattern 下 round-trip 之後默默丟失兩個 byte——不 crash，只是資料錯了。TLS 實作對某個格式異常的 certificate 選擇接受，而另一個實作拒絕——兩邊都不 crash，但至少有一個是錯的。

這類 bug 的 security implication 往往比 crash 更嚴重：

- JSON parser 接受不合規範的輸入 → 攻擊者在上游繞過用嚴格 parser 做的 input validation，輸入餵到用寬鬆 parser 做 processing 的下游，行為分歧造成 SSRF / injection
- 壓縮庫 round-trip 損失資料 → 備份靜默腐化，或攻擊者構造「壓縮後還原不回來」的輸入讓系統狀態不一致
- TLS 實作對 alert 反應不一致 → 協定降級攻擊的前提

差分 fuzzing 的核心思路只有一句話：**兩個宣稱實作同一規範的程式，對同一輸入的輸出應該一致；不一致的地方就是 bug，至少其中一個是錯的。**

---

## 先建立直覺

```
普通 fuzzing:

  input → target → crash? → bug signal
              ↓
          sanitizer
       (ASAN/UBSAN)


差分 fuzzing:

  input → impl_A → output_A ─┐
       ↘                      ├→ output_A ≠ output_B ? → 差異！→ 分析 spec
  input → impl_B → output_B ─┤
       ↘                      │
  input → impl_C → output_C ─┘


Oracle 設計的三個關鍵問題:

  1. 誰是 ground truth？
     - 有明確 spec（RFC、POSIX）→ 對照 spec 判斷誰對
     - 沒有 spec → 多數決（3 個 impl 中 2 個一致的算對）
     - 老版本當 oracle → 找 regression

  2. 什麼算「差異」？
     - Exact match（輸出字串一模一樣）→ 太嚴，浮點/key 排序合法差
     - Accept vs Reject（接受 vs 拒絕輸入）→ 最弱但最乾淨
     - 語義等價（JSON parse 結果 key-value 集合相同）→ 需自訂比較器

  3. 差異是 bug 還是 undefined behavior？
     - Spec 沒規定的情況 → 兩個 impl 都合法 → 不是 bug
     - Spec 明確禁止的情況 → 接受那個是 bug
```

---

## Section A: Oracle 設計四層次

差分 fuzzing 的難點不在 fuzzing 本身，在 oracle。oracle 設計錯，找到的全是雜訊；oracle 設計對，每個差異都值得看。

```
Oracle 強度層次:

  weak ←──────────────────────────────────────────────────→ strong

  accept/reject     value_equal       round-trip         diff-crash
  ─────────────     ───────────       ──────────         ──────────
  A rejects,        A.parse(x) ==     decomp(comp        A crashes,
  B accepts         B.parse(x)        (x)) == x          B doesn't

  找規範邊界        找語義 bug         找序列化 bug        最明確 bug
  解釋差異          需自訂比較器       單個 impl 可測       自動記錄
```

**策略一：Accept/Reject 一致性**（最弱但最常用）

兩個 impl 對同一輸入，一個接受、一個拒絕。適合找「規範邊界」的解釋差異。實作最容易：只看 return code 或 exception，不用比較輸出值。

**策略二：輸出語義等價**（中）

兩個 impl 的輸出語義上相等。JSON 的 key 順序可以不同，但 key-value 集合必須相同。浮點數在兩個 impl 可能表示為 `1.0` 和 `1`，語義相同但字串不同。需要自訂比較器，這是這個策略的主要工作量。

**策略三：Round-trip 一致性**（適合壓縮/序列化，強）

`decompress(compress(x)) == x`。不需要第二個 impl，一個實作自己前後一致就夠了。這是最容易自動化的 oracle 之一，因為比較對象就是原始輸入，不用設計比較器。

**策略四：差分崩潰**（最強）

只有一個 impl crash，另一個不 crash。這是最明確的 bug 訊號——同樣的輸入，一個實作能處理，另一個爆炸，crash 的那個明確是 bug。

---

## Section B: 真實案例——json vs ujson

Python 標準庫 `json` 和高效能第三方庫 `ujson` 都宣稱解析 JSON，但對規範邊界的處理有明顯差異。以下是在 WSL2 上真實執行的差分測試：

```python
import json, ujson

cases = [
    ('"\x01"',  'U+0001 control char'),
    ('"\x02"',  'U+0002 control char'),
    ('"\x1f"',  'U+001F control char'),
    ('"\t"',    'TAB in string (unescaped)'),
    ('1.',      'trailing dot number'),
]

for val, label in cases:
    try:    r1 = json.loads(val);  ok1, s1 = True,  repr(r1)
    except: ok1, s1 = False, 'ERROR'
    try:    r2 = ujson.loads(val); ok2, s2 = True,  repr(r2)
    except: ok2, s2 = False, 'ERROR'
    if ok1 != ok2:
        print(f'DIFF [{label}]:')
        print(f'  stdlib json: {"accepts →" if ok1 else "rejects"} {s1[:40]}')
        print(f'  ujson:       {"accepts →" if ok2 else "rejects"} {s2[:40]}')
```

真實輸出：

```
DIFF [U+0001 control char]:
  stdlib json: rejects ERROR
  ujson:       accepts → '\x01'

DIFF [U+0002 control char]:
  stdlib json: rejects ERROR
  ujson:       accepts → '\x02'

DIFF [U+001F control char]:
  stdlib json: rejects ERROR
  ujson:       accepts → '\x1f'

DIFF [TAB in string (unescaped)]:
  stdlib json: rejects ERROR
  ujson:       accepts → '\t'

DIFF [trailing dot number]:
  stdlib json: rejects ERROR
  ujson:       accepts → 1.0
```

**這些差異的 security implication**：

RFC 7159 §7 明確規定，JSON string 內的 control character（U+0000 到 U+001F）必須用 `\uXXXX` 轉義，不能裸放。ujson 違反這條規定，接受裸 control character。

這在實際系統中造成的問題：

- **Log injection**：攻擊者在 JSON 值裡塞 `\x0d\x0a`（CRLF），ujson 接受後輸出給 log 系統，可以偽造 log 記錄
- **ANSI escape injection**：`\x1b[31m` 等 ANSI 序列透過 ujson 進入終端輸出，可以改變顏色或清空螢幕
- **Validation bypass**：前端用 stdlib json 做輸入驗證（拒絕含 control char 的 JSON），後端用 ujson 做解析（接受）——攻擊者送含 `\x01` 的 JSON，繞過前端驗證，後端照樣處理

`trailing dot`（`1.`）的差異是另一個典型案例：如果 A 系統用 stdlib json 做 type validation（確認值是整數），B 系統用 ujson 做實際解析，攻擊者送 `1.` 可以讓 A 拒絕（所以攻擊者改送 `1.0` 過了 A），或者讓 B 接受 A 認為不合法的輸入。

---

## Section C: 差分 Harness 實作

### Python 黑盒差分（直接可跑）

上面的 json vs ujson 測試是手動挑選 case。搭配 fuzzer 的版本：

```python
import sys, json, atheris

try:
    import ujson
    HAS_UJSON = True
except ImportError:
    HAS_UJSON = False

def TestOneInput(data):
    if not HAS_UJSON:
        return
    try:
        text = data.decode('utf-8', errors='replace')
    except Exception:
        return

    ok_stdlib = True
    ok_ujson  = True

    try:
        r_stdlib = json.loads(text)
    except Exception:
        ok_stdlib = False

    try:
        r_ujson = ujson.loads(text)
    except Exception:
        ok_ujson = False

    # Accept/reject mismatch → 記錄
    if ok_stdlib != ok_ujson:
        with open('diffs.log', 'a') as f:
            f.write(f'DIFF accept: stdlib={ok_stdlib} ujson={ok_ujson} '
                    f'input={repr(text[:80])}\n')

atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
```

注意這裡用 `diffs.log` 而不是直接 `abort()`。差分 fuzzing 的輸出通常很多——每個 edge case 都可能觸發差異，你要收集夠多樣本再去分析哪些是真 bug。直接 abort 的話 fuzzer 第一個差異就停了。

### C 語言差分 Harness（結構性說明）

```c
/* differential_harness.c
 * 比較兩個 JSON parser 的 accept/reject 行為
 * 編譯: clang -fsanitize=fuzzer,address -o fuzz_diff \
 *        differential_harness.c libparser_a.a libparser_b.a
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "parser_a.h"   /* impl A: returns 1=ok, 0=reject */
#include "parser_b.h"   /* impl B: returns 1=ok, 0=reject */

/* 全域 fd，避免每次 open/close 的開銷 */
static FILE *g_log = NULL;

__attribute__((constructor))
static void init_log(void) {
    g_log = fopen("diffs.log", "a");
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size == 0 || size > 65536) return 0;

    /* null-terminated copy */
    char *buf = malloc(size + 1);
    if (!buf) return 0;
    memcpy(buf, data, size);
    buf[size] = '\0';

    int result_a = parse_json_a(buf, size);
    int result_b = parse_json_b(buf, size);

    if (result_a != result_b) {
        if (g_log) {
            fprintf(g_log, "DIFF: a=%d b=%d size=%zu input=",
                    result_a, result_b, size);
            /* 只印可見字元，binary 用 hex */
            for (size_t i = 0; i < size && i < 128; i++) {
                unsigned char c = (unsigned char)buf[i];
                if (c >= 0x20 && c < 0x7f)
                    fputc(c, g_log);
                else
                    fprintf(g_log, "\\x%02x", c);
            }
            fputc('\n', g_log);
            fflush(g_log);
        }

        /*
         * 選擇性 abort:
         * 如果想讓 libFuzzer 把這個 input 存成 crash sample，
         * 取消下面這行的注釋。
         * 但注意：這樣 fuzzer 每找到一個差異就停，
         * 通常只在確認某類差異是真 bug 後才這樣做。
         */
        /* abort(); */
    }

    free(buf);
    return 0;
}
```

---

## Section D: Coverage 與差分 Fuzzing 的關係

差分 fuzzing 遇到的工程問題：兩個 impl 的 coverage 是不同的 binary，怎麼引導？

```
差分 fuzzing 的 coverage 困境:

  impl_A 的 code coverage ≠ impl_B 的 code coverage
  （兩個不同的 binary，不同的 code path）

  策略一: 只追蹤 impl_A 的 coverage（以 A 為主線）
  ────────────────────────────────────────────────
  libFuzzer 掛在 impl_A 上，正常跑 coverage-guided fuzzing

  優點: 直接用，不改 fuzzer
  缺點: 可能錯過 impl_B 的 deep paths（B 的某些 bug
        只在 B 的深層 code path 才觸發）


  策略二: Union coverage（追蹤 A ∪ B 的 coverage）
  ──────────────────────────────────────────────────
  兩個 binary 同時 instrument，feedback = 兩者 bitmap 的 OR

  優點: 兩個 impl 的 deep paths 都能探到
  缺點: 需要修改 fuzzer 或用 custom mutator，
        binary 要重編，工程量大

  策略三: 黑盒（只靠 grammar/random 生成，不用 coverage）
  ──────────────────────────────────────────────────────────
  適合情境:
  - 有大量 seed（JSON: 網路上 JSON 樣本數億）
  - 有好的 grammar（JSON grammar 完整定義在 RFC）
  - 目標是規範邊界（deep path 不重要，邊界條件才重要）

  搭配 Ch 14 的 grammar fuzzer，生成結構合法的輸入，
  確保輸入能穿過 early rejection，打到語義層的差異
```

實務上最常見的選擇是**策略一 + grammar seed**：用 libFuzzer 的 coverage 引導探 impl_A，同時用大量來自真實世界的 seed corpus 覆蓋常見 edge case。純黑盒只在有超大 seed corpus 或 grammar 非常完整時才夠用。

---

## Section E: 真實案例庫

### 案例一：JSON parser 差異（本章主案例）

如上文所示。RFC 7159 有明確規定，差異直接比對 spec 就能判斷誰對。

### 案例二：壓縮庫 round-trip

不需要第二個 impl，直接測一個：

```python
import zlib, sys, atheris

def TestOneInput(data):
    if len(data) == 0:
        return
    for level in [1, 6, 9]:
        try:
            compressed   = zlib.compress(data, level)
            decompressed = zlib.decompress(compressed)
            if decompressed != data:
                with open('roundtrip_bugs.log', 'a') as f:
                    f.write(f'level={level} original={data.hex()} '
                            f'got={decompressed.hex()}\n')
        except Exception:
            pass  # compress/decompress 本身 exception 不算這類 bug

atheris.Setup(sys.argv, TestOneInput)
atheris.Fuzz()
```

注意：zlib 本身已經很成熟，這個測試對 zlib 可能找不到東西，但對新的壓縮庫實作非常有用。

### 案例三：Frankencerts（X.509 / TLS 差分）

2014 年 Brubaker et al. 的工作。思路：隨機組合 X.509 certificate 的各個欄位（subject, issuer, extension, signature 等），生成「語法上合法但語義上奇怪」的 cert chain，餵給 OpenSSL、NSS、GnuTLS、CyaSSL、PolarSSL，比對接受/拒絕行為。

結果：找到 7 個 CVE。典型差異：某個 extension 的解析方式，兩個 lib 的行為完全相反。

關鍵設計：cert 的合法性邊界很複雜（RFC 5280 有大量 MUST/SHOULD），隨機生成的 cert 大部分都是「兩邊都應該拒絕」的垃圾，有效信號很稀疏。解法：先用 grammar 確保基本結構正確，再隨機組合欄位值。

### 案例四：JS 引擎差分

Fuzzilli（用於 JS 引擎的 coverage-guided fuzzer）有差分模式：同一段 JS 跑在 V8、SpiderMonkey、JavaScriptCore，比對輸出。

差分 JS fuzzing 找到的 bug 通常是 JIT 最佳化錯誤：同一段 JS，解釋執行和 JIT 後的結果不同（V8 的 TurboFan 對某個推論做了錯誤最佳化）。這類 bug 通常會導致 type confusion → exploit。

### 案例五：多版本差分（找 regression）

同一軟體不同版本當做「兩個 impl」：

```
libfoo 1.2.3 接受某輸入，libfoo 1.3.0 拒絕
  → 向後相容性 bug，可能是 security fix 過頭了
  → 也可能是 1.2.3 的 bug 被暴露了，1.3.0 才是對的
```

建 build matrix，對同一 seed corpus 跑兩個版本，比對每個 seed 的接受/拒絕結果。版本之間行為分歧的 input 列出來逐一分析。

---

## 對比取捨

| 方法 | 能找的 bug 類型 | 需要 oracle | 適合場景 | 主要難點 |
|------|----------------|-------------|---------|---------|
| 普通 crash fuzzing | 記憶體漏洞、crash | 自動（crash/sanitizer） | 任何 C/C++ target | 提高 coverage |
| 差分（accept mismatch） | 規範解釋不一致、validation bypass | 兩個 impl | parser、codec、TLS | 找到品質夠好的第二個 impl |
| 差分（值比較） | 邏輯 bug、計算錯誤 | 語義等價函數 | 浮點計算、加密函數 | 撰寫準確的比較器 |
| Round-trip | 序列化/壓縮 bug | 自動（恆等式） | 壓縮庫、序列化格式 | 幾乎沒有，最易上手 |
| 多版本差分 | Regression bug | 舊版當 oracle | 版本升級前後對比 | 要維護多個版本的 build |
| 差分崩潰 | 記憶體漏洞 + 規範差異 | 自動（crash） | 兩個 impl 都能 fuzz | 需要兩個都能 fuzz |

---

## 踩雷

**踩雷 1：「找到差異就是 bug」**

最常見的誤判。差異可能是 undefined behavior——spec 沒有規定的情況，兩個 impl 的選擇都合法。

例子：JSON spec 沒有規定 object 的 key 排序。如果你把兩個 parser 的輸出序列化回 JSON string 再做字串比對，key 順序不同就會報差異——但這不是 bug，RFC 明確沒有規定順序。

另一個例子：某個輸入在 spec 的 undefined 區域，兩個 impl 各自有合理的選擇，都不算錯。

正確做法：找到差異後，**對照 spec 判斷哪個是正確行為**。沒有規定的情況，標記為 `UNSPECIFIED_BEHAVIOR`，不算 bug，但記錄下來——因為這種「兩個 impl 行為不同」的 undefined area 在實際系統中仍然可能被利用（attacker 控制走哪個 impl）。

**踩雷 2：「差分 fuzzing 不需要 coverage，隨機生成就夠」**

隨機生成的 input 大部分在 early rejection path 就被拒掉了，根本到不了語義層的差異。

實際測試：對 JSON parser 純隨機生成的 input，超過 99% 在第一個字元就被拒（不是 `{`、`[`、`"`、數字之一）。剩下的 1% 裡，大部分在第二個 token 就失敗了。真正能觸發語義差異的 input 需要先是「結構合法的 JSON」，然後在邊界值上做變異。

解法：用 Ch 14 的 grammar fuzzer 生成結構合法的 JSON，再在值層做 mutation（替換數字、插入 control char、改 string 內容）。搭配 coverage 引導到不常被測試的 code path。效果比純隨機差一個數量級以上。

**踩雷 3：「差分 log 輸出太多，分析不過來」**

差分 fuzzing 跑幾個小時可以找到數萬個差異，但其中 95% 是同一類問題的重複樣本（例如所有 control char 都觸發同一個 ujson 問題）。不做 deduplication，log 就是雜訊堆。

解法：對差異做分類。最簡單的是只記錄「新類型」的差異：

```python
seen_patterns = set()

def classify_diff(text, ok_a, ok_b):
    # 分類鍵：接受/拒絕模式 + 輸入的前 4 字元類型
    first_chars = repr(text[:4])
    return (ok_a, ok_b, first_chars)

if ok_stdlib != ok_ujson:
    key = classify_diff(text, ok_stdlib, ok_ujson)
    if key not in seen_patterns:
        seen_patterns.add(key)
        log_diff(text, ok_stdlib, ok_ujson)  # 只記第一個新類型
```

更好的方法是 post-processing 階段用 triage script 對 diff log 做聚類，對每個 unique 差異類型各取 1-3 個最小 reproducer。

**踩雷 4：「差分 fuzzing 要找到第二個 impl 才能做」**

很多人以為差分 fuzzing 一定需要兩個獨立實作。round-trip oracle 完全不需要：`decompress(compress(x)) == x` 就是一個完整的差分 oracle，只有一個 impl。另一個技巧是用慢但確定正確的 reference implementation 當 oracle——例如用 Python 的 `json` 標準庫當 ground truth，測各種 C 語言的高效能 JSON parser。標準庫通常更嚴格遵守 spec（效能壓力小，可以多做檢查），適合當 oracle 使用。

---

## 進階延伸

**多版本差分找 regression**

把同一軟體的 v1.2 和 v1.3 都 build 出來，作為「兩個 impl」做差分。v1.2 接受、v1.3 拒絕的輸入，可能是正確的 security fix，也可能是不必要的限制。這對 library maintainer 追蹤向後相容性問題特別有用，也讓安全研究員能快速找到「哪個 commit 引入了限制」。

**差分 fuzzing + Sanitizer 疊加**

impl_A 用 ASAN + UBSAN 編譯，impl_B 用 release build。差分 fuzzing 找到語義差異的同時，ASAN 可能同時抓到記憶體問題。兩個訊號疊加，bug 密度更高：

```bash
# impl_A: ASAN build（掛 sanitizer）
clang -fsanitize=address,undefined -o parser_a_asan parser_a.c

# impl_B: release build（不加 sanitizer，避免雜訊干擾差分邏輯）
clang -O2 -o parser_b_release parser_b.c
```

**跨語言差分**

同一協定的不同語言實作之間的差分：

- gRPC protobuf：Go / Python / Rust 實作解析同一個 .proto message
- JWT 驗證：`python-jose` vs `jsonwebtoken`（Node.js）vs `golang-jwt`
- HTTP/2 frame 解析：`h2`（Python）vs `nghttp2`（C）vs `hyper`（Rust）

跨語言差分特別容易找到 security bug，因為各語言生態的實作者通常不會互相看對方的 code，邊界行為更容易分歧。跨語言的 differential test 需要一個「橋接層」（例如把兩個 impl 都包成 subprocess，用 stdin/stdout 通訊），工程量比單語言高，但投報率也高。

**Stateful 差分**

協定 fuzzing（如 TLS handshake、HTTP/2 stream）需要比較 stateful 行為：同一序列的訊息，兩個實作的狀態機轉換是否一致。這是 Part 3 stateful fuzzing 的主題，比單一輸入的差分更複雜，但 CVE 密度也更高——Frankencerts 就屬於這類的輕量版。

---

## 動手練習

1. **基礎差分**：安裝 `ujson`（`pip install ujson`），把本章的差分測試跑起來，再手動增加 5 個你認為可能有差異的測試 case（提示：嘗試超大數字、巢狀過深的結構、Unicode surrogate pair、空字串 key、重複 key）。

2. **Round-trip oracle**：用 Python `zlib` 或 `lzma` 模組寫一個 round-trip fuzzer，搭配 `atheris` 讓它真正跑起來。確認它能正確偵測 round-trip 失敗，並記錄觸發的 input。

3. **Grammar + 差分整合**：把 Ch 14 的 JSON grammar fuzzer 產生的 corpus 當成 seed，餵給本章的 json vs ujson 差分測試腳本。統計跑 10 秒能找到幾類差異，對比純隨機輸入的效率。

4. **撰寫語義比較器**：如果兩個 JSON parser 都接受同一輸入但輸出不同（例如一個把 `1e100` 解析成 float，另一個溢位成 `inf`），純 accept/reject 比較抓不到這個 bug。寫一個語義比較函數，處理以下情況：key 排序不同、浮點精度表示不同、`null` vs Python `None`。

---

## 本章重點

- 差分 fuzzing 解決了傳統 fuzzing 無法偵測非 crash 邏輯 bug 的問題，透過多實作比對產生 oracle
- Oracle 強度從弱到強：accept/reject 一致性 → 語義值比較 → round-trip 恆等 → 差分崩潰
- ujson 對 RFC 7159 禁止的 control character 和 trailing dot 的接受行為，是真實存在的規範差異，有具體 security implication（log injection、validation bypass）
- 找到差異不等於找到 bug：undefined behavior 的情況兩個 impl 都可能合法；必須對照 spec 判斷
- 差分 fuzzing 不一定需要兩個 impl：round-trip oracle 單個 impl 即可使用
- Coverage guidance 對差分 fuzzing 仍然重要：純隨機生成的 input 大部分在 early rejection path 就被過濾，到不了語義層差異；grammar seed + coverage 的組合效果遠優於純隨機

---

## 自我檢核

- 你能說出差分 fuzzing 和普通 crash fuzzing 的根本差異，以及各自能找到哪類 bug 嗎？
- 你知道 accept/reject oracle、語義等價 oracle、round-trip oracle 各自的適用場景嗎？
- 看到差異就說「這是 bug」之前，你會先做什麼確認步驟？
- 如果找到 100,000 個差異 log 記錄，你的 triage 流程是什麼？
- 不借助第二個 impl，哪種 oracle 仍然可以做差分 fuzzing？
- ujson 接受裸 control character 在 JSON string 裡，這個在實際系統中怎麼被利用？

---

## 延伸閱讀

1. **"Finding and Understanding Bugs in C Compilers"** — Yang et al., PLDI 2011（CSmith 論文）
   優先讀 §1 Introduction 和 §2 Differential Testing。差分 fuzzing 的開山之作。用多個 C compiler（GCC、LLVM、ICC 等）編譯同一份隨機生成的 C 程式，比對輸出。找到數百個 compiler bug，其中不少是 miscompilation（編譯出來的程式行為錯誤但沒有 crash）。這篇論文確立了「多實作比對」作為 bug 找尋方法的地位。

2. **"Frankencerts: Synthesized X.509 Certificate Chains for Testing TLS Libraries"** — Brubaker et al., IEEE S&P 2014
   優先讀 §3 Approach。直接導致 7 個 CVE。方法：把真實 cert 的各個欄位拆開，重新隨機組合，生成語法合法但語義奇特的 cert chain，餵給 OpenSSL、NSS、GnuTLS 等 5 個 TLS lib。展示了差分 fuzzing 在 security-critical protocol 上的高效性，是差分 fuzzing 實戰效果最有說服力的案例。

3. **"Coverage-guided, Property-based Testing"** — Lampropoulos et al., POPL 2019
   如果你想深入理解如何把 property-based testing（包含差分 oracle 設計）和 coverage guidance 結合，這篇是理論基礎。比前兩篇更學術，但對理解「oracle 是什麼、怎麼設計好的 oracle」有幫助。

4. **ujson GitHub Issues**（搜尋 "RFC 7159" 或 "control character"）
   本章發現的差異已有人在 upstream 回報過部分。直接看 issue tracker 可以了解維護者的 trade-off 考量（效能 vs 嚴格合規），以及哪些「差異」被認為是 feature，哪些被接受為 bug 修復。這是把差分 fuzzing 結果轉化為實際貢獻的完整路徑示範。

---

上一章用 grammar fuzzer 確保輸入結構合法，這一章把合法輸入導向多個實作做比對——兩者自然疊加：grammar 生成 → 差分比對，是 parser/codec 類 target 的標準攻擊路線。

Part 3 進入 stateful fuzzing，處理協定和 API 序列（TLS handshake、HTTP/2 stream、系統呼叫序列）。差分的概念在 stateful 場景中還是適用，但 oracle 設計要同時考慮狀態機的正確性，複雜度跳一個台階。

→ [練習 B](./practice-b-grammar-parser-fuzzer.md)
