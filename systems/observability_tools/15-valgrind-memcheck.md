# Ch 15 — valgrind memcheck

> **目標**：掌握 valgrind memcheck——debug 記憶體錯誤的核心工具，能偵測 leak（記憶體洩漏）、use-after-free（用了已釋放的記憶體）、越界讀寫、未初始化的值、double-free。理解它**怎麼做到**（用「動態二進位插樁」模擬執行每條指令、追蹤每個記憶體）、它的代價（慢 10-50 倍）、以及怎麼讀它的報告。記憶體錯誤是 C/C++ 最常見也最危險的 bug，valgrind 是抓它們的利器。

> **環境**：Linux，valgrind（Ch 0），C 程式用 `gcc -g -O0` 編譯（debug symbols）。

## 為什麼記憶體錯誤需要專門工具？

C/C++ 手動管理記憶體（malloc/free），這帶來最常見也最危險的 bug：**leak**（malloc 沒 free，記憶體一直漲）、**use-after-free**（free 後還用那塊記憶體）、**越界**（讀寫超出配置的範圍）、**未初始化**（用了沒初始化的值）、**double-free**（free 兩次）。這些 bug 詭異——可能不立刻崩潰（記憶體還沒被覆蓋），而是「有時崩潰、有時不崩潰、在別處崩潰」，極難 debug。

strace/perf 看不到這些（它們不是 syscall 或熱點，是記憶體存取的錯誤）。**valgrind memcheck** 是專門抓它們的——它追蹤程式的每個記憶體配置和存取，發現錯誤就報告（精確指出在哪行、什麼錯）。理解 valgrind 和它怎麼運作，你能抓出 C/C++ 最隱蔽的 bug。這是記憶體安全的核心工具，也是 C/C++ 開發的必備。

## 先建立直覺:記憶體的會計師

```
valgrind memcheck = 記憶體的「會計師」

  程式每次 malloc/free/讀/寫記憶體，valgrind 都記帳：
        │
  追蹤每塊記憶體的狀態：
    - 這塊是 malloc 的嗎？多大？（合法範圍）
    - 已經 free 了嗎？（free 後不該再用）
    - 初始化了嗎？（未初始化的值不該被用於決策）
        │
  每次記憶體存取，valgrind 檢查：
    讀寫的位址在合法範圍嗎？（否則：越界）
    這塊 free 了嗎？（是的話：use-after-free）
    讀的值初始化了嗎？（否則：未初始化的使用）
        │
  程式結束時，檢查：
    所有 malloc 的都 free 了嗎？（否則：leak）
        │
  → valgrind 像會計師，記錄每塊記憶體的「帳」
    發現「帳對不上」（越界/UAF/leak）就報告
    精確到「哪行、什麼錯、哪塊記憶體」
```

關鍵心智：valgrind memcheck 像「記憶體的會計師」——追蹤每塊記憶體的狀態（是否 malloc、多大、是否 free、是否初始化），每次存取檢查「合法嗎」（越界/use-after-free/未初始化），程式結束檢查「都 free 了嗎」（leak）。發現「帳對不上」就精確報告（哪行、什麼錯）。

> valgrind 抓的記憶體錯誤是 strace/perf 看不到的（它們不是 syscall 或熱點）。如果對 malloc/free、記憶體不熟，回看 Ch 2 或 C 基礎。valgrind 是記憶體 debug 的核心。

## valgrind 怎麼運作（動態二進位插樁）

```
valgrind 的原理：動態二進位插樁（DBI）

  valgrind 不是「在旁邊觀察」程式（像 strace 的 ptrace）
  而是「模擬執行」程式的每一條指令：
        │
  1. valgrind 把程式的機器碼「翻譯」成中間表示
  2. 在每個記憶體存取「插入檢查碼」（插樁）
  3. 在它的「模擬 CPU」上執行翻譯後的碼
  4. 每次記憶體存取都跑檢查（合法嗎）
        │
  → 程式實際在 valgrind 的「模擬環境」裡跑
    每條指令都被檢查 → 抓得到任何記憶體錯誤
    代價：慢 10-50 倍（模擬執行 + 每次檢查）
        │
  對比：
    strace：ptrace 在 syscall 邊界攔截（不模擬執行，快）
    valgrind：模擬執行每條指令（慢，但能檢查每個記憶體存取）
    sanitizers（Ch 18）：編譯時插樁（快，但要重新編譯）
```

> **valgrind 用「動態二進位插樁」模擬執行每條指令並檢查每個記憶體存取——這讓它能抓任何記憶體錯誤，代價是慢 10-50 倍**。valgrind 的原理和 strace（ptrace 攔截 syscall）不同——它**模擬執行程式的每一條指令**（動態二進位插樁，DBI）：把機器碼翻譯成中間表示、在每個記憶體存取插入檢查碼、在它的「模擬 CPU」上執行。所以程式實際在 valgrind 的模擬環境裡跑，**每條指令、每個記憶體存取都被檢查**——這讓它能抓到**任何**記憶體錯誤（越界、UAF、未初始化的每個位元組），非常徹底。代價是**慢 10-50 倍**（模擬執行 + 每次檢查的開銷）——所以 valgrind 用於開發/測試環境（不是生產，太慢）。對比三種記憶體檢查工具的取捨：**valgrind**（模擬執行，最徹底，不用重編譯，但慢）；**sanitizers**（Ch 18，編譯時插樁，快很多但要重新編譯）；理解這個取捨——valgrind 適合「不能重編譯」或「要最徹底檢查」的場景，sanitizers 適合「開發時的快速檢查」（能重編譯）。valgrind 的「模擬執行」也是為什麼它不需要原始碼或重新編譯（直接對 binary 動作，雖然有 `-g` debug symbols 能顯示行號更有用）。理解它怎麼運作，你知道它的能力（抓任何記憶體錯誤）和限制（慢、改變時序可能影響並發 bug，Ch 3 的 Heisenbug）。

## 用 memcheck 抓記憶體錯誤

```bash
cd ~/obslab
# 一個有各種記憶體錯誤的程式
cat > membugs.c <<'EOF'
#include <stdlib.h>
#include <string.h>
int main() {
    // 錯誤 1：leak（malloc 沒 free）
    char *leak = malloc(100);

    // 錯誤 2：use-after-free
    char *uaf = malloc(50);
    free(uaf);
    uaf[0] = 'x';                 // 用了已 free 的記憶體！

    // 錯誤 3：越界寫
    char *oob = malloc(10);
    oob[10] = 'y';                // 寫超出範圍（只配置 10，索引 10 越界）
    free(oob);

    // 錯誤 4：未初始化的使用
    int *uninit = malloc(sizeof(int));
    if (*uninit == 42) { }        // 用了未初始化的值
    free(uninit);

    return 0;
}
EOF
gcc -g -O0 membugs.c -o membugs

# 用 valgrind 抓
valgrind --leak-check=full --track-origins=yes ./membugs 2>&1 | head -40
# ==12345== Invalid write of size 1                      ← 越界寫
# ==12345==    at 0x...: main (membugs.c:17)             ← 精確到行！
# ==12345== Address 0x... is 0 bytes after a block of size 10 alloc'd
#
# ==12345== Invalid write of size 1                      ← use-after-free
# ==12345==    Address 0x... is 0 bytes inside a block of size 50 free'd
#
# ==12345== Use of uninitialised value                   ← 未初始化
#
# ==12345== LEAK SUMMARY:
# ==12345==    definitely lost: 100 bytes in 1 blocks    ← leak！
# ==12345==    at malloc ... main (membugs.c:5)          ← leak 在哪分配的
```

> **valgrind 精確報告每個記憶體錯誤——什麼錯、哪一行、哪塊記憶體，這是它的核心價值**。valgrind 對 membugs.c 直接報告所有錯誤，且**精確**：**越界寫**（`Invalid write of size 1 at membugs.c:17`，還說「在 size 10 的塊之後 0 bytes」——明確指出越界多少）；**use-after-free**（`Invalid write ... inside a block ... free'd`——指出用了已 free 的記憶體）；**未初始化**（`Use of uninitialised value`，配 `--track-origins=yes` 還告訴你「這個未初始化的值來自哪裡」）；**leak**（`LEAK SUMMARY: definitely lost: 100 bytes`，配 `--leak-check=full` 告訴你「leak 的記憶體在哪行分配的」）。這個「精確到行、說明什麼錯」是 valgrind 的核心價值——記憶體 bug 通常難 debug（不立刻崩潰、在別處崩潰），valgrind 直接指出根因（在 membugs.c:17 越界、在 membugs.c:5 分配的記憶體沒 free）。關鍵選項：**`--leak-check=full`**（詳細的 leak 報告，含分配位置）、**`--track-origins=yes`**（追蹤未初始化值的來源——「這個未初始化的值是哪個變數」，對 debug 未初始化超有用）。**`-g` 編譯**（debug symbols）讓 valgrind 顯示行號（否則只有位址）。leak 的分類：**definitely lost**（確定洩漏，沒有指標指向它了——真 bug）、**indirectly lost**（被洩漏的塊引用的）、**possibly lost**（可能，指標指向塊中間）、**still reachable**（還有指標指向，程式結束才沒釋放——通常不算嚴重）。記住：**用 valgrind 跑你的 C/C++ 程式，看到 definitely lost 和各種 Invalid 就是 bug**。

## 讀 valgrind 報告

```
valgrind 報告的關鍵錯誤類型：

  Invalid read/write of size N     越界或 UAF（讀寫不該碰的記憶體）
    + "X bytes after a block"      → 越界（超出配置範圍）
    + "inside a block ... free'd"  → use-after-free
        │
  Use of uninitialised value       用了未初始化的值
        │
  Invalid free                     free 不該 free 的（double-free、free 非 malloc 的）
        │
  LEAK SUMMARY:
    definitely lost                確定洩漏（真 bug）★
    indirectly lost                間接洩漏
    possibly lost                  可能洩漏
    still reachable                程式結束還沒 free（通常 OK）
        │
  每個錯誤都有：
    at 0x...: 函式 (檔案:行)        ← 精確位置（要 -g）
    Address 0x... 的說明            ← 哪塊記憶體、什麼狀態
        │
  → 看 Invalid（記憶體存取錯）和 definitely lost（leak）
    照著行號去修
```

> **看 valgrind 報告聚焦「Invalid（存取錯誤）」和「definitely lost（leak）」——這兩類是要修的真 bug**。valgrind 報告各種錯誤，優先修：**Invalid read/write**（越界或 use-after-free——讀寫了不該碰的記憶體，這些可能造成崩潰或資料損壞，是嚴重 bug）；**Invalid free**（double-free 或 free 非 malloc 的——也會崩潰）；**Use of uninitialised value**（用了未初始化的值——行為不確定）；**definitely lost**（確定的記憶體洩漏——真 bug，要 free）。每個錯誤都有精確位置（`at 函式 (檔案:行)`，要 `-g`）和記憶體說明（哪塊、什麼狀態），照著去修。**leak 分類要分辨**：`definitely lost`（真洩漏，修）vs `still reachable`（程式結束時還有指標指向，通常不算嚴重——如全域的快取，程式結束 OS 會回收，但乾淨的程式會 free）。實務上：**把 valgrind 整合進測試**——每次跑測試也跑 valgrind，確保沒有記憶體錯誤（CI 裡常這樣做）。`--error-exitcode=1`（有錯誤時 exit 非 0）讓 CI 能偵測。記憶體錯誤是 C/C++ 最危險的 bug（崩潰、安全漏洞、難 debug），valgrind 讓它們無所遁形——養成「C/C++ 程式用 valgrind 跑一遍」的習慣，能抓出大量隱藏的記憶體 bug。

## 故意弄壞:用 valgrind 抓「詭異」的記憶體 bug

```bash
cd ~/obslab
# 一個「有時崩潰有時不崩潰」的記憶體 bug（最難 debug 的那種）
cat > heisenbug.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
char* make_greeting(const char *name) {
    char buf[20];                 // 棧上的緩衝（太小！）
    sprintf(buf, "Hello, %s!", name);  // 如果 name 長 → 越界（棧溢位）
    return strdup(buf);
}
int main() {
    char *g1 = make_greeting("Bob");           // 短名字 → 剛好沒事
    char *g2 = make_greeting("Alexander");     // 長名字 → 越界！（但可能不立刻崩潰）
    printf("%s / %s\n", g1, g2);
    free(g1);
    // 忘了 free(g2)！→ leak
    return 0;
}
EOF
gcc -g -O0 heisenbug.c -o heisenbug

# 直接跑：可能「看起來正常」（越界沒立刻崩潰）
./heisenbug
# Hello, Bob! / Hello, Alexander!    ← 看起來沒問題！（但有 bug）

# valgrind 揪出隱藏的 bug
valgrind --leak-check=full ./heisenbug 2>&1 | grep -E 'Invalid|lost|overflow' | head
# Invalid write of size ...           ← 越界寫（buf[20] 裝不下 "Hello, Alexander!"）
# definitely lost: ... (g2 沒 free)   ← leak
# → valgrind 揪出「直接跑看不出」的 bug：
#   1. 棧緩衝越界（buf 太小，長名字溢位 —— 可能在生產環境隨機崩潰！）
#   2. g2 leak
#   這些「看起來正常但有 bug」的程式是定時炸彈，valgrind 提前抓出
```

> **valgrind 揪出「直接跑看起來正常但有 bug」的記憶體錯誤——這些是最危險的定時炸彈**。這個例子展示記憶體 bug 最危險的特性——**「看起來正常但有 bug」**。`make_greeting` 的 `buf[20]` 裝不下 "Hello, Alexander!"（越界），但**直接跑可能看起來沒問題**（越界寫到了棧上相鄰的記憶體，沒立刻崩潰，輸出看起來對）。這是定時炸彈——在某些情況（不同的編譯器、不同的記憶體佈局、更長的輸入）會**隨機崩潰**或**資料損壞**，而且**極難 debug**（崩潰可能發生在別處、有時崩潰有時不崩潰）。valgrind **直接揪出**——`Invalid write`（越界）+ `definitely lost`（g2 leak）。這就是 valgrind 的價值——它在「bug 還沒造成明顯災難前」就抓出來。記憶體 bug 不像邏輯 bug（行為錯）那麼明顯，它們潛伏著，在最糟的時候爆發（生產環境、客戶現場）。**養成用 valgrind 跑 C/C++ 程式的習慣**，能提前抓出這些定時炸彈。這呼應本課的核心——觀察「實際發生什麼」（valgrind 看到實際的越界寫），而非「看起來怎樣」（輸出看起來正常）。對 C/C++ 開發者，valgrind（和 Ch 18 的 sanitizers）是記憶體安全的守護者——用它們，你的程式少很多隱藏的記憶體 bug。練習 C 會用 valgrind 系列抓更多記憶體和並發 bug。

## 動手練習

1. 抓各種錯誤：對 membugs.c 用 valgrind，看它報告 leak/UAF/越界/未初始化，理解每種報告

2. 讀報告：理解 Invalid write（越界 vs UAF）、definitely lost（真 leak）vs still reachable

3. track-origins：用 `--track-origins=yes` 抓未初始化的值，看它追蹤來源

4. 整合測試：寫一個小程式 + valgrind，用 `--error-exitcode=1` 讓有錯誤時 exit 非 0（CI 用）

5. 跑「故意弄壞」：用 valgrind 抓 heisenbug.c 的「看起來正常但有 bug」，理解定時炸彈

## 本章重點整理

- valgrind memcheck 是記憶體錯誤的核心工具：偵測 leak、use-after-free、越界、未初始化、double-free
- 原理：動態二進位插樁（模擬執行每條指令、檢查每個記憶體存取）——徹底但慢 10-50 倍（開發環境用）
- 報告精確到行（要 -g）+ 說明什麼錯/哪塊記憶體；`--leak-check=full`（詳細 leak）、`--track-origins=yes`（未初始化來源）
- 聚焦修 Invalid（越界/UAF）和 definitely lost（真 leak）；still reachable 通常不嚴重
- valgrind 揪出「看起來正常但有 bug」的定時炸彈——C/C++ 程式養成用 valgrind 跑的習慣

## 自我檢核

- [ ] 知道 valgrind memcheck 能抓哪些記憶體錯誤（leak/UAF/越界/未初始化）
- [ ] 理解它怎麼運作（動態插樁，模擬執行），為什麼慢
- [ ] 會讀 valgrind 報告（Invalid 的類型、leak 的分類）
- [ ] 知道關鍵選項（--leak-check=full、--track-origins、-g 編譯）
- [ ] 理解 valgrind 揪出「看起來正常但有 bug」的價值

## 延伸閱讀

### 官方文件

- **[Valgrind memcheck manual](https://valgrind.org/docs/manual/mc-manual.html)** — Valgrind
  - **讀哪裡**：memcheck 的錯誤類型、選項、leak 分類
  - **為什麼值得讀**：valgrind 的權威，每種錯誤和 leak 分類的完整說明

### 文章

- **[Valgrind 教學](https://www.cprogramming.com/debugging/valgrind.html)** — Cprogramming
  - **這篇說什麼**：valgrind 的常用法和報告解讀
  - **為什麼值得讀**：本章用法的入門擴充

- **[How Valgrind works](https://valgrind.org/docs/valgrind2007.pdf)** — Valgrind 論文
  - **這篇說什麼**：valgrind 的動態插樁原理
  - **為什麼值得讀**：本章「怎麼運作」的權威深入版

### 書籍

- **《The Art of Debugging》— 記憶體章 / 各 C 偵錯書**
  - **為什麼值得讀**：把 valgrind 放進 C/C++ debug 的脈絡

下一章看 valgrind 的並發工具——helgrind/drd，偵測 data race 和 deadlock（多執行緒的記憶體/同步 bug）。從單執行緒記憶體錯誤進到並發 bug。

→ [Ch 16 valgrind helgrind/drd（並發）](./16-valgrind-helgrind-drd.md)
