# Ch 18 — sanitizers（ASan/TSan/UBSan/MSan）

> **目標**：掌握 sanitizers——編譯時插樁的執行期檢查工具：ASan（AddressSanitizer，記憶體錯誤）、TSan（ThreadSanitizer，data race）、UBSan（UndefinedBehaviorSanitizer，未定義行為）、MSan（MemorySanitizer，未初始化）。理解它們和 valgrind 的根本差別（編譯時插樁 vs 動態插樁）——sanitizers 快很多（2-3 倍 vs valgrind 的 10-50 倍）但要重新編譯。這是現代 C/C++ 開發的標準工具，CI 必備。

> **環境**：Linux，gcc/clang（內建 sanitizers）。`gcc -fsanitize=...`。

## 為什麼有了 valgrind 還要 sanitizers？

valgrind（Ch 15-17）很強大，但**慢**（10-50 倍）——慢到不適合「每次跑測試都用」。sanitizers 解決這個——它們在**編譯時插樁**（編譯器在程式碼裡插入檢查），所以執行期開銷小很多（2-3 倍），快到能「每次測試都開著」。

這個速度差別改變了使用方式——valgrind 是「偶爾跑一次抓 bug」，sanitizers 是「開發時一直開著，CI 每次都跑」。sanitizers 是現代 C/C++ 開發的標準——很多專案的 CI 用 ASan/TSan/UBSan 跑測試，在 bug 進入主分支前抓出。理解 sanitizers 和它們的取捨（快但要重編譯 vs valgrind 慢但不用重編譯），你能在開發流程中正確使用它們。它們和 valgrind 各有定位。

## 先建立直覺:編譯時插樁 vs 動態插樁

```
valgrind（動態插樁）vs sanitizers（編譯時插樁）：

  valgrind（Ch 15）：執行時插樁
    對「已編譯的 binary」模擬執行，每條指令插檢查
    優點：不用重編譯（對任何 binary 都能用）
    缺點：慢（10-50倍，模擬執行整個程式）
        │
  sanitizers：編譯時插樁
    編譯時，編譯器在程式碼「插入檢查碼」
    gcc -fsanitize=address → 編譯出「帶檢查」的程式
    優點：快（2-3倍，只在需要處插檢查，且編譯器優化）
    缺點：要重新編譯（要有原始碼）
        │
  → sanitizers 快但要重編譯（開發時用，CI 用）
    valgrind 慢但不用重編譯（沒原始碼/不能重編譯時用）
        │
  類比：
    valgrind = 事後請人逐一檢查（慢，但不用改原件）
    sanitizers = 製造時就裝感測器（快，但要改製造流程）
```

關鍵心智：valgrind（動態插樁）對已編譯的 binary 模擬執行（不用重編譯但慢 10-50 倍）；sanitizers（編譯時插樁）讓編譯器在程式碼裡插檢查（要重編譯但快 2-3 倍）。sanitizers 快到能「開發時一直開著、CI 每次跑」，valgrind 適合「沒原始碼/不能重編譯」時。

> sanitizers 抓的 bug 和 valgrind 重疊（記憶體錯誤、race）但更快。如果對 valgrind 的動態插樁不熟，回看 [Ch 15](./15-valgrind-memcheck.md)。它們的取捨是「編譯時 vs 執行時插樁」。

## ASan:記憶體錯誤

```bash
cd ~/obslab
# 用之前的 membugs.c（各種記憶體錯誤）
gcc -g -fsanitize=address -O1 membugs.c -o membugs_asan
# -fsanitize=address：開啟 ASan
# -O1：sanitizers 建議帶點優化（行為更接近 release）

./membugs_asan
# ==12345==ERROR: AddressSanitizer: heap-buffer-overflow ...   ← 越界
#     #0 ... in main membugs.c:17                              ← 精確到行
#     ...
# 0x... is located 0 bytes to the right of 10-byte region      ← 越界多少
# allocated by thread T0 here:
#     #0 ... in malloc
#     #1 ... in main membugs.c:16                              ← 在哪分配的
#
# ==12345==ERROR: AddressSanitizer: heap-use-after-free ...    ← UAF
#
# 程式結束時（leak 偵測）：
# ==12345==ERROR: LeakSanitizer: detected memory leaks
# Direct leak of 100 byte(s) ... in main membugs.c:5           ← leak

# → ASan 抓和 valgrind memcheck 一樣的記憶體錯誤，但快很多
#   報告也精確（什麼錯、哪行、哪塊記憶體）
```

> **ASan（AddressSanitizer）抓記憶體錯誤（和 valgrind memcheck 一樣），但快 10 倍——這讓它能「開發時一直開著」**。ASan 抓的記憶體錯誤和 valgrind memcheck（Ch 15）一樣——**heap/stack buffer overflow**（越界）、**use-after-free**、**use-after-return**、**double-free**、**leak**（LeakSanitizer）。報告一樣精確（什麼錯、哪行、哪塊記憶體在哪分配的）。但 ASan **快很多**——valgrind memcheck 慢 10-50 倍，ASan 只慢約 2 倍。這個速度差別讓使用方式不同：valgrind 是「偶爾跑」，ASan 快到能**「開發時一直開著」**（你編譯 debug 版時就開 ASan，每次跑都檢查）。用法：`gcc -fsanitize=address`（編譯時開啟）。建議配 `-g`（行號）和 `-O1`（帶點優化，行為接近 release，也讓 stack trace 更準）。ASan 還能抓 valgrind 抓不到的——**stack buffer overflow**（棧上的越界，valgrind 主要看 heap）、**use-after-return**（用了已返回函式的棧記憶體）。所以 ASan 在某些方面比 valgrind 更全面（棧的錯誤）。代價是要**重新編譯**（要原始碼）和記憶體開銷（ASan 用額外記憶體做 shadow memory 追蹤）。現代 C/C++ 開發的標準做法：**開發和測試時用 ASan**（快、抓 heap+stack 錯誤），沒原始碼或要最徹底時用 valgrind。ASan 是 Google 開發的，現在是 gcc/clang 內建的標準工具。

## TSan / UBSan / MSan

```bash
# === TSan：data race（並發，對應 helgrind Ch 16）===
gcc -g -fsanitize=thread -O1 race.c -o race_tsan
./race_tsan
# ==12345== WARNING: ThreadSanitizer: data race ...
#   Write of size 4 at 0x... by thread T2:
#     #0 worker race.c:7                          ← race 在 counter++
#   Previous read ... by thread T1:
#     #0 worker race.c:7
# → TSan 抓 data race，比 helgrind 快很多

# === UBSan：未定義行為（C/C++ 的 UB，很隱蔽）===
cat > ubdemo.c <<'EOF'
#include <stdio.h>
int main() {
    int x = 2147483647;   // INT_MAX
    int y = x + 1;        // signed overflow → UB！
    int arr[5];
    int z = arr[10];      // 越界（也是 UB）
    int s = 1 << 35;      // 移位超過寬度 → UB
    printf("%d %d %d\n", y, z, s);
    return 0;
}
EOF
gcc -g -fsanitize=undefined ubdemo.c -o ubdemo
./ubdemo
# ubdemo.c:4: runtime error: signed integer overflow: 2147483647 + 1 ...
# ubdemo.c:7: runtime error: shift exponent 35 is too large ...
# → UBSan 抓「未定義行為」—— 這些是 C/C++ 最隱蔽的 bug（編譯器可能假設不發生）

# === MSan：未初始化的記憶體（只有 clang）===
# clang -fsanitize=memory -g uninit.c -o uninit_msan
# → 抓「用了未初始化的值」（比 valgrind 的精確）
```

> **TSan（data race）、UBSan（未定義行為）、MSan（未初始化）各專精一類 bug——UBSan 特別有價值，抓 C/C++ 最隱蔽的「未定義行為」**。sanitizers 家族各專精一類：**TSan**（ThreadSanitizer）抓 **data race**（對應 helgrind，Ch 16，但快很多）——並發 bug 的偵測；**UBSan**（UndefinedBehaviorSanitizer）抓 **未定義行為（UB）**——這是 C/C++ 最隱蔽危險的 bug：signed integer overflow（INT_MAX + 1）、移位超過寬度（`1 << 35`）、越界、null 指標解參考、對齊錯誤等。UB 危險在於**編譯器假設它不發生**——所以編譯器可能基於「這不會 UB」做優化，導致 UB 的程式在優化後行為詭異（甚至「刪掉」你以為會執行的程式碼）。UBSan 在執行時抓到 UB 並報告（精確到行），這對寫正確的 C/C++ 極有價值（UB 是無數詭異 bug 的根源）；**MSan**（MemorySanitizer，只有 clang）抓**未初始化的記憶體使用**（比 valgrind 更精確）。**注意 TSan 和 ASan 不能同時用**（它們的記憶體佈局衝突，要分開編譯跑）。**現代 C/C++ 開發的標準**：CI 裡用多個 sanitizer 跑測試——ASan（記憶體）、UBSan（UB，常和 ASan 一起 `-fsanitize=address,undefined`）、TSan（並發，分開跑）——在 bug 進主分支前抓出。這是「shift left」的測試理念（越早抓 bug 越好）。sanitizers 讓 C/C++ 的「記憶體不安全、有 UB」這些根本問題，能在開發時被系統地偵測——大幅提升程式的正確性和安全性。

## sanitizers vs valgrind:何時用哪個

| 面向 | sanitizers | valgrind |
|---|---|---|
| 插樁時機 | 編譯時 | 執行時（動態）|
| 速度 | 快（2-3 倍）| 慢（10-50 倍）|
| 要重編譯 | 是（要原始碼）| 否（任何 binary）|
| 記憶體錯誤 | ASan | memcheck |
| data race | TSan | helgrind/drd |
| 未定義行為 | UBSan | （不專門）|
| stack 越界 | ASan ✓ | memcheck △ |
| 用於 | 開發/CI（一直開）| 沒原始碼/最徹底 |

```
選擇框架：
  有原始碼 + 開發/測試 → sanitizers（快，CI 一直跑）
  沒原始碼（只有 binary）→ valgrind（不用重編譯）
  要最徹底的記憶體檢查 → valgrind memcheck（模擬每條指令）
  CI 自動測試 → sanitizers（快到能每次跑）
        │
  → 現代開發：sanitizers 為主（CI 標配）
    valgrind 補充（沒原始碼、最徹底、特定場景）
```

> **現代開發以 sanitizers 為主（CI 標配，快到能每次跑），valgrind 補充（沒原始碼或要最徹底時）**。選擇框架：**有原始碼 + 開發/測試** → **sanitizers**（快，能在 CI 每次測試都跑，bug 早抓出）；**沒原始碼（只有 binary）** → **valgrind**（不用重編譯，對任何 binary 都能用）；**要最徹底的記憶體檢查** → valgrind memcheck（模擬每條指令，連未初始化的每個位元組都追蹤，比 ASan 在某些細節更徹底）。現代 C/C++ 專案的標準做法：**CI 裡用 sanitizers**——編譯一個 ASan 版、一個 TSan 版、一個 UBSan 版，跑測試套件，任何 sanitizer 報錯就 fail（bug 在進主分支前被抓）。這是「持續正確性檢查」——不是「偶爾跑一次 valgrind」，而是「每次 commit 都自動檢查記憶體/並發/UB」。sanitizers 的速度（快到能 CI 每次跑）讓這成為可能。valgrind 仍有價值——分析第三方 binary（沒原始碼）、要最徹底的檢查、或開發環境沒設好 sanitizers 時。但日常開發，sanitizers 是更好的選擇（快、整合進編譯流程、CI 友善）。理解這個取捨，你在專案裡設定正確的工具——開發/CI 用 sanitizers，特殊場景用 valgrind。這完成了 Part 6 的「記憶體與正確性」——valgrind 家族（memcheck/helgrind/profiling）+ sanitizers（ASan/TSan/UBSan/MSan）。你有了完整的「正確性檢查」工具，能抓記憶體錯誤、並發 bug、未定義行為——C/C++ 最危險的問題。

## 故意弄壞:CI 風格的 sanitizer 測試

```bash
cd ~/obslab
# 模擬 CI：用多個 sanitizer 跑測試，任何錯誤就 fail
cat > buggy_lib.c <<'EOF'
#include <stdlib.h>
#include <string.h>
char* process(const char *input) {
    char *result = malloc(strlen(input));   // bug: 少了 +1 給 \0！
    strcpy(result, input);                   // 越界寫（\0 沒空間）
    return result;
}
int main() {
    char *r = process("hello");
    free(r);
    return 0;
}
EOF

# CI 風格：用 ASan + UBSan 編譯測試
echo "=== ASan + UBSan ==="
gcc -g -fsanitize=address,undefined -O1 buggy_lib.c -o test_asan
./test_asan
# AddressSanitizer: heap-buffer-overflow ...   ← 抓到 malloc 少 +1 的越界！
echo "exit: $?"    # 非 0（測試 fail）

# 在 CI 裡，這個非 0 退出會讓 build fail → bug 不會進主分支
# CI 設定範例（概念）：
#   - gcc -fsanitize=address,undefined ... && ./test || exit 1
#   - gcc -fsanitize=thread ... && ./test_threaded || exit 1
#   任何 sanitizer 報錯 → CI fail → 開發者必須修

# → sanitizers 整合進 CI = 自動的持續正確性檢查
#   每次 commit 都檢查記憶體/並發/UB，bug 早抓出
```

> **把 sanitizers 整合進 CI = 自動的持續正確性檢查，每次 commit 都抓記憶體/並發/UB bug**。這個例子展示 sanitizers 的現代用法——**CI 整合**。`process` 函式有經典 bug：`malloc(strlen(input))` 少了 `+1`（沒給 `\0` 的空間），`strcpy` 越界寫。這個 bug **直接跑可能看不出**（越界一個 byte，可能沒立刻崩潰）——但 **ASan 一跑就抓到**（heap-buffer-overflow），且讓程式**非 0 退出**。在 CI 裡，這個非 0 退出讓 **build fail**——bug **不會進主分支**（開發者必須先修）。這是 sanitizers 的核心價值——**自動的持續正確性檢查**：CI 設定「用 ASan/UBSan/TSan 編譯跑測試，任何報錯就 fail」，每次 commit 都自動檢查記憶體錯誤、未定義行為、data race。這讓 C/C++ 的「容易有記憶體/並發/UB bug」這個根本弱點，被系統化地防守——bug 在開發階段（不是生產）被抓出。對比傳統「偶爾手動跑一次 valgrind」，sanitizers 的速度讓「每次自動檢查」成為可能。這是現代 C/C++ 專案品質的關鍵實踐（很多大型 C/C++ 專案如 Chromium、LLVM 都重度使用 sanitizers）。`-fsanitize=address,undefined`（ASan+UBSan 一起，常見組合）、`-fsanitize=thread`（TSan，分開）。設定好 CI 的 sanitizer 測試，你的 C/C++ 程式品質會大幅提升——那些隱藏的記憶體/並發/UB bug 會在進主分支前被抓出。這呼應本課的核心——觀察「實際的錯誤行為」（sanitizer 看到實際的越界），在它造成災難前。

## 動手練習

1. ASan：用 `-fsanitize=address` 編譯 membugs.c，看它抓記憶體錯誤（對比 valgrind 的速度）

2. TSan：用 `-fsanitize=thread` 編譯 race.c，看它抓 data race（對比 helgrind）

3. UBSan：用 `-fsanitize=undefined` 編譯有 UB 的程式（overflow/越界），看它抓未定義行為

4. 速度對比：同個程式用 ASan 和 valgrind memcheck，比較執行時間（ASan 快很多）

5. 跑「故意弄壞」：用 ASan+UBSan 抓 buggy_lib.c 的 malloc 少 +1，理解 CI 整合

## 本章重點整理

- sanitizers 編譯時插樁（要重編譯但快 2-3 倍），vs valgrind 動態插樁（不用重編譯但慢 10-50 倍）
- ASan（記憶體錯誤，含 stack 越界）、TSan（data race）、UBSan（未定義行為）、MSan（未初始化，clang）
- UBSan 特別有價值——抓 C/C++ 最隱蔽的 UB（signed overflow/越界/移位），編譯器假設不發生的危險 bug
- TSan 和 ASan 不能同時用（記憶體佈局衝突）；UBSan 常和 ASan 一起（-fsanitize=address,undefined）
- 現代開發以 sanitizers 為主（CI 標配，每次 commit 自動檢查），valgrind 補充（沒原始碼/最徹底）

## 自我檢核

- [ ] 理解 sanitizers（編譯時插樁）和 valgrind（動態插樁）的取捨
- [ ] 知道四個 sanitizer 各抓什麼（ASan/TSan/UBSan/MSan）
- [ ] 知道 UBSan 的價值（抓未定義行為），UB 為什麼危險
- [ ] 知道 ASan 和 TSan 不能同時用
- [ ] 理解怎麼把 sanitizers 整合進 CI（持續正確性檢查）

## 延伸閱讀

### 官方文件

- **[AddressSanitizer](https://github.com/google/sanitizers/wiki/AddressSanitizer)** + **[ThreadSanitizer](https://github.com/google/sanitizers/wiki/ThreadSanitizerCppManual)** — Google sanitizers
  - **讀哪裡**：ASan/TSan 的用法和原理
  - **為什麼值得讀**：sanitizers 的權威（Google 開發的）

- **[Clang sanitizers 文件](https://clang.llvm.org/docs/index.html)** — LLVM
  - **讀哪裡**：各 sanitizer 的 -fsanitize 選項
  - **為什麼值得讀**：所有 sanitizer 選項的權威

### 文章

- **[Undefined behavior 系列](https://blog.regehr.org/archives/213)** — John Regehr
  - **這篇說什麼**：C/C++ 的未定義行為為什麼危險（編譯器怎麼利用 UB）
  - **為什麼值得讀**：理解 UBSan 抓的 UB 為什麼重要

### 論文

- **[AddressSanitizer paper](https://www.usenix.org/conference/atc12/technical-sessions/presentation/serebryany)** — USENIX ATC 2012
  - **核心貢獻**：ASan 的設計（shadow memory、編譯時插樁）
  - **為什麼值得讀**：ASan 怎麼運作的權威

下一個是練習 C——多執行緒 bug 獵殺，綜合 valgrind/sanitizers 抓記憶體和並發 bug。

→ [練習 C：多執行緒 bug 獵殺](./practice-c-multithreaded-hunt.md)
