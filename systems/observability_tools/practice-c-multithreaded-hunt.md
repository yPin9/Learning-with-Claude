# 練習 C — 多執行緒 bug 獵殺

> **目標**：整合 Part 6 的記憶體與正確性工具（valgrind memcheck/helgrind、sanitizers ASan/TSan/UBSan），獵殺一個多執行緒程式裡藏的多種 bug——data race、記憶體 leak、use-after-free、未定義行為。每個 bug 用對的工具抓出。完成後你具備「用工具抓記憶體和並發 bug」的能力，這是 C/C++ 開發最關鍵的 debug 技能，也是這類 bug（最難 debug 的那種）的正確處理方法。

## 背景與動機

多執行緒 + 記憶體管理是 C/C++ 最容易出 bug 的地方，而且這些 bug 最難 debug（不確定、Heisenbug、看起來正常卻有定時炸彈）。Part 6 你學了專門的工具——valgrind（memcheck/helgrind）和 sanitizers（ASan/TSan/UBSan）。現在實戰：用它們獵殺一個藏了多種 bug 的多執行緒程式。

這正是真實的 C/C++ debug——一個多執行緒程式偶爾崩潰、記憶體漲、結果不對，你要用工具系統地找出所有 bug。重點是**用對工具**（race 用 TSan/helgrind、記憶體用 ASan/memcheck、UB 用 UBSan）而非瞎試（這些 bug 加 printf 沒用，會改變時序）。完成這個練習，你建立了「多執行緒/記憶體 bug 用工具系統獵殺」的能力——這是 C/C++ 工程師的核心技能。

## 任務規格

對一個藏了多種 bug 的多執行緒程式，你要：
1. **用對的工具**抓出每個 bug（不是讀碼猜）
2. **說出每個 bug 是什麼**（race/leak/UAF/UB）
3. **修復**並用工具驗證

| Bug 類型 | 用什麼工具 |
|---|---|
| data race | TSan / helgrind |
| 記憶體 leak | ASan / memcheck |
| use-after-free | ASan / memcheck |
| 未定義行為 | UBSan |
| deadlock（加分）| helgrind |

## 目標程式（藏多個 bug）

```c
// buggy_threaded.c —— 藏了多種 bug 的多執行緒程式
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>

int total = 0;                          // 共享計數器
char *shared_buf = NULL;

void* worker(void *arg) {
    int id = *(int*)arg;
    // Bug 1: data race（total 沒鎖保護）
    for (int i = 0; i < 100000; i++) {
        total++;                        // race！
    }
    // Bug 2: 記憶體 leak（local_data 沒 free）
    char *local_data = malloc(100);
    sprintf(local_data, "worker %d", id);
    // 忘了 free(local_data)！
    return NULL;
}

int main() {
    pthread_t threads[4];
    int ids[4];

    shared_buf = malloc(50);
    strcpy(shared_buf, "shared");

    for (int i = 0; i < 4; i++) {
        ids[i] = i;
        pthread_create(&threads[i], NULL, worker, &ids[i]);
    }

    // Bug 3: use-after-free（在 thread 還在用 shared_buf 時 free）
    // free(shared_buf);   // 如果這裡 free，thread 還在用 → UAF（這版先註解）

    for (int i = 0; i < 4; i++) {
        pthread_join(threads[i], NULL);
    }

    // Bug 4: 未定義行為（signed overflow）
    int big = total * 100000;           // 可能 overflow（UB）

    printf("total = %d (expected 400000), big = %d\n", total, big);

    // Bug 5: use-after-free
    free(shared_buf);
    printf("buf was: %s\n", shared_buf);  // 用了已 free 的！UAF

    return 0;
}
```

## 如果你卡住了

1. 不要用 printf debug 並發 bug（會改變時序，Heisenbug，Ch 16）——用工具
2. race 用 TSan（`gcc -fsanitize=thread`）或 helgrind（`valgrind --tool=helgrind`）
3. 記憶體（leak/UAF）用 ASan（`gcc -fsanitize=address`）或 memcheck
4. UB 用 UBSan（`gcc -fsanitize=undefined`）
5. TSan 和 ASan 不能同時——分開編譯跑（一個抓 race、一個抓記憶體）
6. 每個工具報告精確到行——照著行號去看
7. 修一個 bug 後用工具驗證（報告變乾淨）

## 實作步驟建議

### Step 1：編譯 TSan 版抓 data race
### Step 2：編譯 ASan 版抓記憶體 bug（leak/UAF）
### Step 3：編譯 UBSan 版抓未定義行為
### Step 4：逐一修復每個 bug
### Step 5：用工具驗證所有 bug 都修好

## 完整參考解答

**自己用工具獵殺再看！** 親手用對的工具抓才學得到。

<details>
<summary>獵殺過程與修復</summary>

```bash
cd ~/obslab

# === Step 1：TSan 抓 data race ===
gcc -g -fsanitize=thread -O1 buggy_threaded.c -o bt_tsan -pthread
./bt_tsan 2>&1 | grep -A4 'data race' | head
# WARNING: ThreadSanitizer: data race
#   Write of size 4 ... worker buggy_threaded.c:13 (total++)
# → Bug 1: total++ 是 data race（4 個 thread 同時 ++）

# === Step 2：ASan 抓記憶體 bug ===
# 註：先把 UAF 那行（free 後又用）的影響隔離測試
gcc -g -fsanitize=address -O1 buggy_threaded.c -o bt_asan -pthread
./bt_asan 2>&1 | grep -E 'use-after-free|leak' | head
# heap-use-after-free ... buggy_threaded.c:48 (printf shared_buf 在 free 後)
# → Bug 5: use-after-free（free(shared_buf) 後又 printf 它）
# LeakSanitizer: detected memory leaks
#   Direct leak of 400 bytes ... worker buggy_threaded.c:16 (local_data)
# → Bug 2: local_data leak（4 個 worker 各 malloc 100 沒 free）

# === Step 3：UBSan 抓未定義行為 ===
gcc -g -fsanitize=undefined buggy_threaded.c -o bt_ubsan -pthread
./bt_ubsan 2>&1 | grep 'runtime error' | head
# buggy_threaded.c:43: runtime error: signed integer overflow: 400000 * 100000 ...
# → Bug 4: total * 100000 signed overflow（UB）

# === Step 4-5：修復所有 bug ===
cat > fixed_threaded.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>

int total = 0;
pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;   // 修 Bug 1: 加鎖
char *shared_buf = NULL;

void* worker(void *arg) {
    int id = *(int*)arg;
    for (int i = 0; i < 100000; i++) {
        pthread_mutex_lock(&lock);      // 修 Bug 1: 鎖保護
        total++;
        pthread_mutex_unlock(&lock);
    }
    char *local_data = malloc(100);
    sprintf(local_data, "worker %d", id);
    free(local_data);                   // 修 Bug 2: free
    return NULL;
}

int main() {
    pthread_t threads[4];
    int ids[4];
    shared_buf = malloc(50);
    strcpy(shared_buf, "shared");
    for (int i = 0; i < 4; i++) {
        ids[i] = i;
        pthread_create(&threads[i], NULL, worker, &ids[i]);
    }
    for (int i = 0; i < 4; i++) pthread_join(threads[i], NULL);

    long big = (long)total * 100000;    // 修 Bug 4: 用 long 避免 overflow
    printf("total = %d, big = %ld\n", total, big);

    printf("buf was: %s\n", shared_buf); // 修 Bug 5: 先用再 free
    free(shared_buf);                    // free 移到最後
    return 0;
}
EOF
gcc -g -pthread fixed_threaded.c -o ft

# 驗證：所有工具都乾淨
gcc -g -fsanitize=thread -O1 fixed_threaded.c -o ft_tsan -pthread && ./ft_tsan 2>&1 | grep -c 'data race'
# 0 ← 沒 race
gcc -g -fsanitize=address -O1 fixed_threaded.c -o ft_asan -pthread && ./ft_asan 2>&1 | grep -cE 'leak|use-after'
# 0 ← 沒記憶體 bug
gcc -g -fsanitize=undefined fixed_threaded.c -o ft_ub -pthread && ./ft_ub 2>&1 | grep -c 'runtime error'
# 0 ← 沒 UB
# → 全部修好！每個工具驗證乾淨
```

**解答說明**：

- **用對工具**：data race 用 TSan、記憶體 bug 用 ASan、UB 用 UBSan——每個 bug 對應正確的工具，而非瞎試
- **TSan 和 ASan 分開編譯**：它們不能同時（記憶體佈局衝突），所以編譯三個版本（TSan/ASan/UBSan）分別跑
- **Bug 1 (race)**：total++ 沒鎖 → 用 mutex 保護
- **Bug 2 (leak)**：local_data 沒 free → 加 free
- **Bug 4 (UB)**：int overflow → 用 long
- **Bug 5 (UAF)**：free 後又用 → 把 free 移到最後（先用再 free）
- **修復→驗證閉環**：修完用每個工具驗證報告乾淨（0 race、0 leak、0 UB）
- **核心教訓**：並發/記憶體 bug 不能用 printf（Heisenbug，Ch 16），要用對的工具系統獵殺

</details>

## 測試用案例

| Bug | 工具 | 報告 |
|---|---|---|
| data race | TSan | data race at total++ |
| leak | ASan | leak ... local_data |
| use-after-free | ASan | heap-use-after-free |
| signed overflow | UBSan | runtime error: overflow |
| 修復後 | 全部 | 0 報告（乾淨）|

## 延伸挑戰（加分）

- **挑戰一**：加一個 deadlock bug（兩個鎖，順序不一致），用 helgrind 偵測 lock order violation，修復（統一順序）

- **挑戰二**：對比 TSan vs helgrind——同個 race 程式用兩個工具，看報告差異和速度差異

- **挑戰三**：對比 ASan vs valgrind memcheck——同個記憶體 bug，看報告和速度，理解編譯時 vs 動態插樁

- **挑戰四**：CI 腳本——寫一個腳本，用 ASan+UBSan 和 TSan 編譯跑測試，任何報錯就 exit 1（模擬 CI 的持續正確性檢查）

- **挑戰五**：抓一個「ABA problem」或更微妙的並發 bug（lock-free 資料結構的問題），體會 TSan 的威力

## 自我檢核

- [ ] 知道每類 bug 用哪個工具（race→TSan/helgrind、記憶體→ASan/memcheck、UB→UBSan）
- [ ] 知道 TSan 和 ASan 不能同時用，要分開編譯
- [ ] 能用工具精確定位每個 bug（到行）並理解是什麼
- [ ] 會修復並用工具驗證（修復→驗證閉環）
- [ ] 理解為什麼並發/記憶體 bug 要用工具（不能用 printf，Heisenbug）

這個練習訓練了「用工具系統獵殺記憶體和並發 bug」的能力。接下來 Part 7 進入進階自製工具——ptrace 注入（Ch 19）、LD_PRELOAD 攔截（Ch 20）、core dump（Ch 21），把「理解工具底層」推到能自己造工具。

→ [Ch 19 ptrace 進階：process 注入](./19-ptrace-advanced-injection.md)
