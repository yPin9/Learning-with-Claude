# Ch 13 — Dictionary：讓 Fuzzer 說「行話」

> **目標**：理解 dictionary 如何讓 AFL++ 更快繞過 magic bytes 比較，以及 auto-dictionary（`AFL_LLVM_DICT2FILE`）怎麼從程式碼中自動提取 token，讓你不用手動維護 dictionary。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

2014 年，Michał Zalewski 在設計 AFL 時碰到一個無法迴避的問題：coverage-guided fuzzing 對格式嚴格的輸入幾乎束手無策。

假設目標程式的第一行驗證碼是：

```c
if (memcmp(buf, "\x89PNG\r\n\x1a\n", 8) != 0) return ERROR;
```

AFL 的 mutation 以 bit flip、byte flip、arithmetic 為主。要隨機產生出完整的 8-byte magic sequence，機率是 1/256⁸ ≈ 10⁻¹⁹。就算一秒跑 100,000 次，宇宙年齡都不夠。

Coverage-guided fuzzing 的根本弱點是：**它對程式的輸入語意一無所知**，只靠 coverage 信號來引導——而通過 magic bytes 校驗的 coverage 根本到不了。

早期的解法是手工餵 seed：給幾個合法的 PNG、ELF 讓 AFL 去變異。但這帶出另一個問題：seed 的覆蓋範圍有限，AFL 只能在 seed 附近做局部探索，無法系統性地嘗試所有有意義的 keyword 組合。

Dictionary 在 2014 年晚期被加進 AFL，解決方式很直接：**把有意義的 token 明確告訴 fuzzer**，讓 havoc 階段可以插入這些 token，而不是瞎猜。

---

## 先建立直覺

想像你在破解一扇密碼鎖。純 brute-force 是從 0000 試到 9999。Coverage-guided fuzzing 的改良是：每次試一個號碼後，鎖會告訴你「這次嘗試讓幾個卡榫轉動了」，你根據這個信號聚焦在更有希望的範圍。

但問題是鎖的第一道關卡要求你先輸入正確的「廠牌代碼」——四個英文字，不是數字。沒有代碼，卡榫完全不動，coverage 信號全是零。

Dictionary 的作用是：「我告訴你這個牌子叫 YALE、MASTER、ABLOY，你先把這些試一遍。」一旦通過第一道關卡，後面的 coverage-guided 策略就能正常發揮。

換成程式語言的術語：

```
沒有 dictionary：mutation space = 所有 byte 組合  → 太大，有意義的極少
有了 dictionary：mutation 可以插入 ["PNG", "IDAT", "\x89PNG\r\n\x1a\n"] → 直接命中 parser 分支
```

---

## 橫向連結

- **Ch 11 — Havoc**：dictionary token 在 havoc 的插入操作（`insert_dict_entry`）中被使用，理解 havoc 的 mutation 選擇機制能幫助你預測 dictionary 效果。
- **Ch 12 — CmpLog**：CmpLog 和 auto-dictionary 解決相同的問題（讓 fuzzer 知道比較值），但機制不同——dictionary 是靜態注入 token；CmpLog 是執行期攔截比較，並即時回饋給 mutation engine。兩者可以同時啟用。
- **Ch 14 — Crash Semantics**：dictionary 增加到達深層程式碼的機率，也因此可能觸發更多 crash。

---

## Dictionary 格式

AFL++ 的 dictionary 格式是一個純文字檔案，每行一條 entry。格式有兩種：

**命名 entry（推薦）**

```
keyword="PNG"
keyword="\x89PNG\r\n\x1a\n"
keyword="IDAT"
keyword="IEND"
keyword="IHDR"
```

`keyword` 只是標籤，AFL++ 不用它——你可以隨便命名，也可以全部用相同名稱。

**匿名 entry**

```
"PNG"
"\x89PNG\r\n\x1a\n"
"IDAT"
```

兩種格式可以混用。

**跳脫序列（escape sequences）**

| 寫法 | 意義 |
|------|------|
| `\x41` | 十六進位 byte 0x41（'A'） |
| `\n` `\r` `\t` | 換行、回車、tab |
| `\\` | 反斜線本身 |
| `\"` | 雙引號本身 |
| `\x00` | null byte（AFL++ dict 支援 null bytes，不是 C string） |

**重要**：AFL++ dictionary 中的 `\x00` 是有效 byte，不是字串終止符。這與 C 字串語意不同——後面踩雷一節會展開。

---

## 核心用法：手寫 Dictionary

**範例一：對 ELF parser 建 dictionary**

```bash
# 建立 ELF dictionary
cat > elf.dict << 'EOF'
# ELF magic
magic="\x7fELF"

# e_type: ET_NONE ET_REL ET_EXEC ET_DYN ET_CORE
type_exec="\x02\x00"
type_dyn="\x03\x00"
type_rel="\x01\x00"

# e_machine: EM_386 EM_X86_64 EM_ARM EM_AARCH64
arch_x86="\x03\x00"
arch_x86_64="\x3e\x00"
arch_arm="\x28\x00"
arch_aarch64="\xb7\x00"

# Section names
sh_text=".text"
sh_data=".data"
sh_bss=".bss"
sh_dynamic=".dynamic"
sh_got=".got"
sh_plt=".plt"
sh_symtab=".symtab"
sh_strtab=".strtab"

# GNU-specific
gnu_note="GNU"
gnu_property="\x05\x00\x00\x00"

# Alignment padding
pad_null="\x00\x00\x00\x00"
EOF

# 對 readelf 做 fuzzing，使用這個 dictionary
afl-fuzz -x elf.dict -i seeds/ -o out/ -- ./readelf -a @@
```

`-x` 後面接 dictionary 檔案路徑，或者一個目錄（AFL++ 會讀目錄下所有 `.dict` 檔案）。

**AFL++ 官方 dictionary 倉庫**

AFL++ 在 `dictionaries/` 目錄下預裝了大量現成 dictionary：

```bash
ls $(afl-fuzz --help 2>&1 | grep -oP '(?<=located in ).*' | head -1)/../dictionaries/
# 或直接找
find /usr/local/lib/afl/ -name "*.dict" 2>/dev/null
ls /usr/local/share/afl/dictionaries/
```

常見的有：`html.dict`、`js.dict`、`http.dict`、`pdf.dict`、`png.dict`、`xml.dict`、`elf.dict`、`sqlite.dict`……

對這些格式做 fuzzing 時，直接拿來用：

```bash
afl-fuzz -x /usr/local/share/afl/dictionaries/png.dict \
  -i seeds/ -o out/ -- ./png_parser @@
```

---

## 底層機制：它是怎麼運作的？

Dictionary token 在 **havoc 階段**被使用。Havoc 的 mutation 選項是一個加權輪盤，每次隨機選擇一種操作：

```
Havoc mutation 輪盤（簡化）：

  ┌─────────────────────────────────────────────────────┐
  │  bit flip       │  byte flip      │  arithmetic     │
  │  (7%)           │  (7%)           │  (10%)          │
  ├─────────────────┼─────────────────┼─────────────────┤
  │  interesting    │  insert random  │  overwrite byte │
  │  values (5%)    │  bytes (8%)     │  (8%)           │
  ├─────────────────┼─────────────────┼─────────────────┤
  │  delete block   │  clone block    │  splice         │
  │  (6%)           │  (8%)           │  (8%)           │
  ├─────────────────┼─────────────────┴─────────────────┤
  │  insert dict    │  overwrite with dict entry        │
  │  entry (X%)     │  (X%)                             │
  └─────────────────┴───────────────────────────────────┘
                     ↑
              只有載入 dictionary 後這兩格才啟用
```

兩個 dictionary 相關操作：

1. **insert dict entry**：在測試輸入的隨機位置插入一個隨機選取的 token。
2. **overwrite with dict entry**：把測試輸入的某段 bytes 直接覆寫為一個 token。

AFL++ 原始碼中對應的函式在 `src/afl-fuzz-mutators.c`，搜尋 `STAGE_HAVOC` 和 `extras`（extras 是 AFL++ 對 dictionary 的內部名稱）。

**Token 選取機制**

```
載入 dictionary → extras[] 陣列
                      │
              havoc 每次 iteration
                      │
         random_below(extras_cnt)  ← 均勻隨機選一個 index
                      │
            取出 extras[i].data / extras[i].len
                      │
            插入或覆寫到 testcase 的隨機位置
```

這裡有個重要含義：**extras_cnt 越大，每個特定 token 被選到的機率越低**。如果 dictionary 有 1000 個 token，而輪盤本身只有 ~15% 的機率落在 dict 操作，那麼每個特定 token 在單次 havoc iteration 中被選到的機率約為 0.15 / 1000 = 0.00015。Token 太多會稀釋效果——踩雷一節會詳細說。

---

## Auto-Dictionary：從程式碼自動提取 Token

手寫 dictionary 的問題：
- 對不熟悉的格式，你不知道該放什麼 token
- 程式碼內部有大量比較常數，但你看不到

`AFL_LLVM_DICT2FILE` 解決了這個問題：在**編譯期**攔截 LLVM IR 中的比較操作，把比較的常數 operand 提取出來。

**範例二：auto-dictionary 完整流程**

```bash
# 步驟 1：帶 AFL_LLVM_DICT2FILE 重新編譯 target
AFL_LLVM_DICT2FILE=/tmp/auto.dict \
  afl-clang-fast -o target_afl target.c

# 步驟 2：看提取到了什麼
cat /tmp/auto.dict
# 可能的輸出：
# "MAGIC"
# "\x89PNG\r\n\x1a\n"
# "application/json"
# "Content-Type"
# "\x00\x00\x00\x01"
# ...

# 步驟 3：用 auto.dict 啟動 fuzzing
afl-fuzz -x /tmp/auto.dict -i seeds/ -o out/ -- ./target_afl @@
```

**`AFL_LLVM_DICT2FILE` 能攔截什麼比較**

| 函式 | 說明 |
|------|------|
| `strcmp(a, "literal")` | 字串相等比較 |
| `strncmp(a, "literal", n)` | 前 n 字元比較 |
| `memcmp(a, "\xde\xad\xbe\xef", 4)` | binary 比較 |
| `strcasecmp(a, "HTTP")` | 大小寫不敏感比較 |
| `strstr(a, "keyword")` | 子字串搜尋 |
| 整數比較（switch case 等） | LLVM IR 層的 `icmp` |

**`AFL_LLVM_LTO_AUTODICTIONARY`：LTO 版本**

```bash
# LTO 模式提取更完整
AFL_LLVM_LTO_AUTODICTIONARY=1 \
  afl-clang-lto -o target_lto target.c

# LTO auto-dict 自動合并到 target_lto.dict，不需要額外 -x
afl-fuzz -i seeds/ -o out/ -- ./target_lto @@
```

LTO（Link Time Optimization）模式的優勢：
- 可以看到跨 translation unit 的比較（inter-procedural）
- 對 library function 的 inline 之後，能攔截更多隱藏比較
- Auto-dictionary 直接 embed 進 binary，不用額外傳 `-x`

---

## 對比與取捨

| 方式 | 精確度 | 工作量 | 效果 | 適用場景 |
|------|--------|--------|------|----------|
| **手寫 dictionary** | 高（你知道格式語意） | 高（需要閱讀規格或原始碼） | 最好（token 精準） | 知名格式（PNG/ELF/HTTP）；AFL++ 官方已有現成 dict |
| **Auto-dictionary（PCGUARD）** | 中（只看同一 TU 的比較） | 極低（重新編譯即可） | 中（可能漏掉 library 內部比較） | 快速上手；target 原始碼複雜時 |
| **Auto-dictionary（LTO）** | 高（全程式可見） | 低（換 compiler flag） | 高（接近手寫效果） | 有 LTO 支援的環境；想要最好的自動化效果 |
| **CmpLog（Ch 12）** | 最高（執行期攔截） | 低（加 `-c` flag） | 最好（動態比較值） | 比較值是執行期決定的（如從 config 讀取的 magic）；與 dictionary 互補 |

**結論**：實際使用時，推薦**手寫 dictionary + auto-dictionary 合並**：

```bash
# 合并手寫和 auto-dictionary
cat elf.dict /tmp/auto.dict | sort -u > combined.dict
afl-fuzz -x combined.dict -i seeds/ -o out/ -- ./target @@
```

---

## 踩雷集錦

**踩雷 1：dictionary token 太多反而降速**

把所有你能想到的 token 全塞進 dictionary 是個常見錯誤。Havoc 的 dict 操作以均勻分布選 token，1000 個 token 的 dictionary 讓每個 token 的期望命中頻率比 50 個 token 的版本低 20 倍。

診斷方式：

```bash
# 看 dictionary entry 數量
wc -l my.dict

# AFL++ 載入時會顯示
# [*] Loaded 1247 extra tokens from '/tmp/my.dict'.
# 超過 200 個就要考慮精簡
```

原則：每個 token 都要有存在的理由。刪掉重複的、低頻的、可以被其他 token 組合出來的。

**踩雷 2：PCGUARD vs LTO 的 auto-dictionary 效果差距**

```bash
# 這樣編譯，auto-dict 的效果較差（PCGUARD mode）
AFL_LLVM_DICT2FILE=/tmp/auto.dict afl-clang-fast -o target target.c

# 這樣才能看到 libfoo.c 裡的比較（LTO mode）
AFL_LLVM_LTO_AUTODICTIONARY=1 afl-clang-lto -o target target.c -lfoo
```

如果 target 依賴外部 library，且 magic bytes 比較在 library 內部，PCGUARD 模式根本看不到——只有 LTO 模式可以做 whole-program analysis。

**踩雷 3：`\x00` 在 dictionary 裡是有效 byte**

```
# 這是合法的 dictionary entry，包含 null byte
magic="\x00\x00\x00\x01"
```

AFL++ 的 dictionary parser 知道 token 的長度，不靠 null terminator 判斷邊界。但如果你用 shell 工具（如 `echo`）來生成 dictionary，`\x00` 可能被截斷：

```bash
# 錯誤：echo 會截斷 \x00 後的內容
echo 'keyword="\x00\x00\x00\x01"' >> my.dict

# 正確：直接用文字編輯器或 printf 配合 xxd 驗證
printf 'keyword="\\x00\\x00\\x00\\x01"\n' >> my.dict
# 然後確認
cat -v my.dict   # 確認沒有被截斷
```

**踩雷 4：dictionary 路徑帶空格**

```bash
# 錯誤
afl-fuzz -x /path/with spaces/dict.txt ...

# 正確
afl-fuzz -x "/path/with spaces/dict.txt" ...
# 或
afl-fuzz -x /path/without_spaces/dict.txt ...
```

AFL++ 的路徑解析在某些版本對空格處理有問題，避免路徑帶空格最保險。

**踩雷 5：忘記重新編譯就用 `AFL_LLVM_DICT2FILE`**

```bash
# 如果 target 沒有改，make 可能不重編
AFL_LLVM_DICT2FILE=/tmp/auto.dict make target   # 可能 no-op

# 強制重編
AFL_LLVM_DICT2FILE=/tmp/auto.dict make -B target
# 或
touch target.c && AFL_LLVM_DICT2FILE=/tmp/auto.dict make target
```

---

## 進階：再往深一層

**Token 長度限制**

AFL++ 對 dictionary token 有長度上限（預設 `MAX_DICT_FILE` = 128 bytes）。超過上限的 token 會在載入時被截斷，並顯示警告：

```
[!] Token in '/tmp/my.dict' is too long (256 > 128), stripping.
```

如果你需要長 token（例如一個完整的 JSON 結構作為 seed），直接放進 seed corpus 更合適，不要放 dictionary。

**`-x` 指向目錄**

```bash
mkdir dicts/
cp /usr/local/share/afl/dictionaries/png.dict dicts/
cp /tmp/auto.dict dicts/
afl-fuzz -x dicts/ -i seeds/ -o out/ -- ./target @@
```

AFL++ 載入目錄時，合并所有 `.dict` 文件，自動去重。

**SHM-based dictionary（進階）**

AFL++ 支援透過 shared memory 把 dictionary 傳給 fork server，避免每次 fork 後重新解析。這對 token 極多的 dictionary 有效能意義，但一般使用不需要手動配置。

**`AFL_LLVM_DICT2FILE_NO_MAIN`**

```bash
# 不提取 main() 裡的比較（有時 main 的比較是 CLI 參數解析，不是輸入格式）
AFL_LLVM_DICT2FILE=/tmp/auto.dict \
AFL_LLVM_DICT2FILE_NO_MAIN=1 \
  afl-clang-fast -o target target.c
```

---

## 動手練習

**環境準備**

```bash
# 建立一個需要 magic bytes 的 target
cat > magic_target.c << 'EOF'
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

int parse_header(const char *buf, size_t len) {
    if (len < 4) return -1;
    if (memcmp(buf, "FUZZ", 4) != 0) return -1;   // magic check
    if (len < 8) return -1;
    if (memcmp(buf + 4, "\xde\xad", 2) != 0) return -1;  // version check
    // 深層邏輯
    int type = (unsigned char)buf[6];
    if (type == 0x42) {
        // type B processing
        if (len > 16 && strcmp(buf + 8, "PAYLOAD") == 0) {
            // 觸發有趣行為
            printf("Found PAYLOAD in type B\n");
        }
    }
    return 0;
}

int main(int argc, char *argv[]) {
    if (argc < 2) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    char buf[256];
    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    parse_header(buf, n);
    return 0;
}
EOF

# 編譯兩個版本：一個用 auto-dict，一個不用
AFL_LLVM_DICT2FILE=/tmp/magic_auto.dict \
  afl-clang-fast -o magic_target magic_target.c

echo "=== Auto-extracted tokens ==="
cat /tmp/magic_auto.dict
```

**練習 A：比較有無 dictionary 的 coverage**

```bash
mkdir -p seeds/ out_no_dict/ out_with_dict/
echo "AAAA" > seeds/seed1

# 不用 dictionary，跑 60 秒
timeout 60 afl-fuzz -i seeds/ -o out_no_dict/ \
  -- ./magic_target @@ 2>/dev/null || true

# 用 auto-dictionary，跑 60 秒
timeout 60 afl-fuzz -x /tmp/magic_auto.dict \
  -i seeds/ -o out_with_dict/ \
  -- ./magic_target @@ 2>/dev/null || true

# 比較 coverage
echo "=== Without dictionary ==="
cat out_no_dict/default/fuzzer_stats | grep -E "corpus_found|edges_found"

echo "=== With dictionary ==="
cat out_with_dict/default/fuzzer_stats | grep -E "corpus_found|edges_found"
```

預期：有 dictionary 的版本應該更快找到通過 magic check 的路徑，`corpus_found` 和 `edges_found` 都更高。

**練習 B：手寫 dictionary 並驗證**

```bash
cat > manual.dict << 'EOF'
magic="FUZZ"
version="\xde\xad"
type_b="\x42"
payload="PAYLOAD"
EOF

# 用手寫 dictionary 跑
timeout 60 afl-fuzz -x manual.dict \
  -i seeds/ -o out_manual/ \
  -- ./magic_target @@ 2>/dev/null || true

cat out_manual/default/fuzzer_stats | grep -E "corpus_found|edges_found"
```

**練習 C：觀察 AFL++ 如何使用 dictionary**

```bash
# 啟用 AFL_DEBUG 觀察 dictionary 載入
AFL_DEBUG=1 afl-fuzz -x manual.dict -i seeds/ -o out_debug/ \
  -- ./magic_target @@ 2>&1 | head -50
```

觀察輸出中的 `Loaded N extra tokens` 行，以及 havoc 階段的 dict 操作統計。

---

## 本章重點整理

- Coverage-guided fuzzing 對 magic bytes 比較的天然弱點，dictionary 透過預先提供候選 token 來解決；dictionary token 在 havoc 的 insert/overwrite 操作中被均勻隨機選用，token 太多會稀釋命中頻率。
- `AFL_LLVM_DICT2FILE` 在 compile time 從 LLVM IR 中提取 `strcmp`/`memcmp` 等比較的常數 operand，免去手寫 dictionary 的工作；LTO 版本（`AFL_LLVM_LTO_AUTODICTIONARY`）能看到跨模組的比較，效果更完整。
- 手寫 dictionary 精確度最高但工作量大；auto-dictionary 快速但可能遺漏 library 內部比較；CmpLog 能捕捉執行期動態決定的比較值——三者互補，可以同時啟用。

---

## 自我檢核

1. 為什麼純 coverage-guided fuzzing 對 `memcmp(buf, "\x89PNG\r\n\x1a\n", 8)` 這類比較幾乎無能為力？Dictionary 用什麼機制繞過這個問題？
2. AFL++ dictionary 格式中，`keyword="\x00\x00\x00\x01"` 和 C 字串 `"\x00\x00\x00\x01"` 的語意差異是什麼？
3. `AFL_LLVM_DICT2FILE`（PCGUARD 模式）和 `AFL_LLVM_LTO_AUTODICTIONARY`（LTO 模式）在 dictionary 品質上有什麼差別？什麼情況下差別最大？
4. 一個 dictionary 從 20 個 token 增加到 2000 個 token，對 fuzzing 速度和效果各有什麼影響？
5. 為什麼 CmpLog 和 dictionary 可以互補？哪種比較是 CmpLog 能捕捉但 auto-dictionary 不能的？

---

## 延伸閱讀

**AFL++ `docs/fuzzing_in_depth.md` — Dictionary 章節**
核心貢獻：官方文件對 `-x` 旗標、dictionary 格式、auto-dictionary 環境變數的完整說明，以及「何時用 dictionary vs CmpLog」的建議。
讀哪裡：`AFL++ 原始碼目錄/docs/fuzzing_in_depth.md`，搜尋 `Dictionaries` 段落。
和本章關聯：直接對應本章所有實作細節的權威來源。

**AFL++ `dictionaries/` 目錄**
核心貢獻：官方維護的 30+ 種格式 dictionary（HTTP、HTML、JS、PNG、ELF、PDF、SQLite 等），每個 dictionary 都有格式說明注釋。
讀哪裡：`AFL++ 原始碼目錄/dictionaries/`，或 GitHub `aflplusplus/AFLplusplus/tree/stable/dictionaries`。
和本章關聯：範例一的 ELF dictionary 就是從這裡參考而來；實際 fuzzing 前先查這裡有沒有現成的。

**"Coverage-based Greybox Fuzzing as Markov Chain"（Böhme et al., CCS 2016）**
核心貢獻：AFLFast 的理論基礎，用 Markov chain 分析 AFL 的路徑探索效率，解釋了為什麼某些 seed 應該被優先選擇——理解這個之後，dictionary 對 path discovery 的加速效果有更深的理論基礎。
讀哪裡：ACM DL 或 arxiv preprint。
和本章關聯：dictionary 本質上是給特定路徑（magic bytes 後面的 code）的 bias，和 AFLFast 的 seed scheduling 互補。

**LLVM `SanitizerCoverage` 文件**
核心貢獻：PCGUARD instrumentation 的底層原理，理解 instrumentation 在哪個 LLVM pass 發生，有助於理解為什麼 `AFL_LLVM_DICT2FILE` 在 PCGUARD 模式只能看到 TU 內的比較。
讀哪裡：https://clang.llvm.org/docs/SanitizerCoverage.html
和本章關聯：解釋了 auto-dictionary PCGUARD vs LTO 效果差距的根本原因。

---

→ [下一章：Ch 14 — Crash Semantics：讀懂每一個 Signal](14-crash-semantics.md)
