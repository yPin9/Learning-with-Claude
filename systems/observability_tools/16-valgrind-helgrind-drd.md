# Ch 16 — valgrind helgrind / drd（並發）

> **目標**：用 helgrind/drd 偵測並發 bug——data race（兩個執行緒同時存取同一記憶體，至少一個是寫）、deadlock（鎖的循環等待）、鎖的誤用。理解為什麼並發 bug 是「最難 debug 的 bug」（不確定性、Heisenbug）、為什麼需要專門工具（它們追蹤鎖和記憶體存取的順序）。並發 bug 在多執行緒程式無所不在且極危險，這章教你抓它們。

> **環境**：Linux，valgrind（helgrind/drd），pthread 程式（`gcc -g -pthread`）。

## 為什麼並發 bug 最難 debug？

多執行緒程式的 bug 是 debug 界的惡夢——**data race**（兩個執行緒沒同步地存取同一記憶體）造成「有時對、有時錯」的不確定結果、**deadlock**（互相等對方的鎖）造成程式凍結。這些 bug 的可怕在於**不確定性**——它們依賴執行緒的執行順序（時序），而時序每次都不同，所以「有時重現、有時不重現」，極難 debug。

更糟的是 **Heisenbug**（Ch 3）——你加 printf 或用 strace 想 debug，改變了時序，bug 就「消失」了（但沒修好，換個環境又出現）。所以並發 bug 不能用「加 printf」或「單步 debug」（那會改變時序）。需要**專門工具**——helgrind/drd 追蹤鎖和記憶體存取的順序，**偵測潛在的 race/deadlock**（即使這次執行沒觸發，它也能發現「這裡有 race 的可能」）。這是抓並發 bug 的關鍵——偵測「可能性」而非「等它發生」。

## 先建立直覺:data race 是什麼

```
data race（資料競爭）：並發 bug 的核心

  兩個執行緒「同時」存取同一個記憶體，且至少一個是「寫」，
  且沒有「同步」（鎖）保護
        │
  例：兩個執行緒都做 counter++
    counter++ 不是「原子」的，它是三步：
      1. 讀 counter（如讀到 5）
      2. 加 1（5+1=6）
      3. 寫回 counter（寫 6）
        │
  如果兩個執行緒交錯：
    執行緒 A 讀 5 → 執行緒 B 讀 5 → A 寫 6 → B 寫 6
    → 兩次 ++ 但結果只 +1（應該 +2）！= race
        │
  data race 的後果：
    結果不確定（依賴時序，每次可能不同）
    「有時對、有時錯」、難重現、難 debug
        │
  → 解法：用鎖（mutex）保護共享記憶體
    helgrind 偵測「沒被鎖保護的共享存取」= race
```

關鍵心智：**data race** 是「兩個執行緒同時存取同一記憶體、至少一個寫、沒有鎖保護」。因為操作不是原子的（如 `counter++` 是讀-加-寫三步），執行緒交錯會造成錯誤結果（兩次 ++ 結果只 +1）。後果是「結果不確定、有時對有時錯、難重現」。解法是用鎖保護。helgrind 偵測「沒被鎖保護的共享存取」。

> 並發 bug 的不確定性和 Heisenbug（Ch 3）緊密相關——觀察改變時序，bug 消失。如果對 Heisenbug 不熟，回看 [Ch 3](./03-ptrace-syscall-deep-dive.md)。

## helgrind:偵測 data race

```bash
cd ~/obslab
# 一個有 data race 的多執行緒程式
cat > race.c <<'EOF'
#include <stdio.h>
#include <pthread.h>
int counter = 0;                 // 共享變數（沒有鎖保護！）
void* worker(void *arg) {
    for (int i = 0; i < 100000; i++) {
        counter++;               // race！兩個執行緒同時 ++
    }
    return NULL;
}
int main() {
    pthread_t t1, t2;
    pthread_create(&t1, NULL, worker, NULL);
    pthread_create(&t2, NULL, worker, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    printf("counter = %d (expected 200000)\n", counter);
    return 0;
}
EOF
gcc -g -pthread race.c -o race

# 直接跑：結果「有時對有時錯」（race 的不確定性）
./race    # counter = 187234 (expected 200000)  ← 少了！（race 吃掉了一些 ++）
./race    # counter = 200000                      ← 有時剛好對（更難 debug）
./race    # counter = 156789                      ← 每次不同

# 用 helgrind 偵測 race
valgrind --tool=helgrind ./race 2>&1 | grep -A5 'Possible data race' | head -15
# Possible data race during read of size 4 at 0x... by thread #3
#    at 0x...: worker (race.c:7)               ← 精確指出 race 在 counter++ 那行
# This conflicts with a previous write of size 4 by thread #2
#    at 0x...: worker (race.c:7)
# → helgrind 偵測到「counter 被兩個執行緒沒同步地存取」= data race
#   即使這次執行剛好結果對（200000），helgrind 也能偵測到「有 race 的可能」！
```

> **helgrind 偵測 data race「即使這次執行剛好結果對」——它偵測「可能性」而非「等它發生」，這是抓並發 bug 的關鍵**。data race 的可怕在於**不確定性**——`./race` 有時給 187234、有時剛好 200000、每次不同（兩個執行緒的 `counter++` 交錯，吃掉一些遞增）。這種「有時對有時錯」極難 debug——你可能跑十次都對，以為沒問題，但生產環境某次就錯了。**helgrind 的價值**是它偵測「**有 race 的可能**」而非「等 race 發生」——即使這次執行剛好結果對（200000），helgrind 也報告 `Possible data race`（因為它看到「counter 被兩個執行緒沒同步地存取」這個**結構性問題**，不管這次有沒有觸發錯誤結果）。這是抓並發 bug 的關鍵——你不能靠「跑很多次看有沒有出錯」（race 可能很少觸發），要靠工具偵測「結構上有 race」。helgrind 精確指出 race 在哪行（`race.c:7` 的 `counter++`）、哪兩個執行緒衝突。它的原理是**追蹤每個記憶體存取和鎖的狀態**——如果兩個執行緒存取同一記憶體（至少一個寫）而中間沒有共同的鎖/同步，就是 race。**修法**：用 mutex 保護 `counter++`（`pthread_mutex_lock` / `unlock` 包住），或用原子操作（`atomic`）。helgrind 是多執行緒程式的守護者——它在 race 造成生產災難前抓出來。

## drd 與 deadlock 偵測

```bash
# drd 是另一個並發偵測工具（和 helgrind 類似，演算法不同）
valgrind --tool=drd ./race 2>&1 | grep -A3 'Conflicting' | head

# === deadlock 偵測 ===
cat > deadlock.c <<'EOF'
#include <pthread.h>
#include <unistd.h>
pthread_mutex_t lock_a = PTHREAD_MUTEX_INITIALIZER;
pthread_mutex_t lock_b = PTHREAD_MUTEX_INITIALIZER;
void* thread1(void *arg) {
    pthread_mutex_lock(&lock_a);   // 先鎖 A
    usleep(100000);
    pthread_mutex_lock(&lock_b);   // 再鎖 B
    pthread_mutex_unlock(&lock_b);
    pthread_mutex_unlock(&lock_a);
    return NULL;
}
void* thread2(void *arg) {
    pthread_mutex_lock(&lock_b);   // 先鎖 B（順序相反！）
    usleep(100000);
    pthread_mutex_lock(&lock_a);   // 再鎖 A → deadlock！
    pthread_mutex_unlock(&lock_a);
    pthread_mutex_unlock(&lock_b);
    return NULL;
}
int main() {
    pthread_t t1, t2;
    pthread_create(&t1, NULL, thread1, NULL);
    pthread_create(&t2, NULL, thread2, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    return 0;
}
EOF
gcc -g -pthread deadlock.c -o deadlock

# 直接跑：程式凍結（deadlock）
# ./deadlock    # 卡住不動（t1 等 B、t2 等 A，互相等）

# helgrind 偵測 lock order 問題（deadlock 的根源）
valgrind --tool=helgrind ./deadlock 2>&1 | grep -A5 'lock order' | head
# Thread #x: lock order "lock_a before lock_b" violated
# → helgrind 偵測到「兩個執行緒以相反順序鎖 A/B」= 潛在 deadlock
#   即使這次沒卡住，helgrind 也能偵測到「lock order 不一致」
```

> **deadlock 的根源是「鎖順序不一致」，helgrind 偵測 lock order violation——即使這次沒卡住也能發現**。**deadlock**（死鎖）是另一類並發 bug——兩個執行緒互相等對方持有的鎖，永遠卡住。經典模式是「**鎖順序不一致**」：thread1 先鎖 A 再鎖 B，thread2 先鎖 B 再鎖 A——如果 thread1 拿到 A、thread2 拿到 B，然後 thread1 等 B（被 thread2 持有）、thread2 等 A（被 thread1 持有），互相等，**deadlock**（程式凍結）。helgrind 偵測 **lock order violation**——它記錄「每個執行緒的鎖順序」，發現「兩個執行緒以相反順序鎖同樣的鎖」就報告（即使這次沒真的卡住——因為時序剛好沒觸發，但結構上有 deadlock 的可能）。這又是「偵測可能性而非等它發生」——deadlock 可能很少觸發（要剛好的時序），但 helgrind 從「鎖順序不一致」這個結構問題偵測它。**修法**：**統一鎖順序**（所有執行緒都先鎖 A 再鎖 B，就不會死鎖——這是避免 deadlock 的黃金規則）。**drd** 是另一個並發偵測工具（和 helgrind 類似，演算法略不同，有時一個抓到另一個沒抓到，可以兩個都試）。deadlock 在生產環境是嚴重問題（服務凍結、要重啟），helgrind 能在開發時抓出潛在的 deadlock。理解「鎖順序一致」是避免 deadlock 的關鍵，helgrind 是驗證它的工具。

## 為什麼並發 bug 不能用一般工具

```
為什麼並發 bug 需要 helgrind（不能用 strace/printf/gdb）：

  1. Heisenbug（Ch 3）：觀察改變時序
     加 printf → 改變執行緒時序 → race 可能消失（但沒修好）
     用 strace/gdb 單步 → 嚴重改變時序 → bug 不重現
        │
  2. 不確定性：依賴時序，難重現
     跑十次可能都對，第十一次錯（生產環境）
        │
  3. 「沒出錯」不代表「沒 bug」：
     這次時序剛好沒觸發 race，不代表沒有 race
        │
  → helgrind 的解法：偵測「結構上的可能性」
     不靠「等 bug 發生」，而是分析「鎖和記憶體存取的順序」
     發現「沒同步的共享存取」「鎖順序不一致」= 潛在 bug
     即使這次沒觸發，也能偵測到
        │
  代價：慢（valgrind 的插樁）+ 可能有 false positive
```

> **並發 bug 不能用 printf/gdb（會改變時序造成 Heisenbug）——helgrind 偵測「結構上的可能性」，這是它不可取代的價值**。並發 bug 為什麼需要 helgrind 這種專門工具？因為一般 debug 方法**會改變時序**：**加 printf** → 改變執行緒的執行時序 → race 可能「消失」（但沒修好，換個環境又出現——這是 Heisenbug，Ch 3）；**用 gdb 單步** → 嚴重改變時序 → bug 完全不重現。所以「加 printf debug」「單步追蹤」對並發 bug **無效甚至誤導**（讓你以為修好了）。而且並發 bug**不確定**——跑十次可能都對，第十一次錯（生產環境），「沒出錯」不代表「沒 bug」（只是這次時序剛好沒觸發）。**helgrind 的解法**是偵測「**結構上的可能性**」——它不靠「等 bug 發生」，而是分析「鎖和記憶體存取的順序」，發現「沒同步的共享存取」（race）或「鎖順序不一致」（deadlock）這些**結構問題**，即使這次執行沒觸發錯誤。這是抓並發 bug 的正確方法——偵測「有沒有 race/deadlock 的可能」，而非「這次有沒有出錯」。代價是慢（valgrind 的插樁開銷）+ 可能有 false positive（報告一些其實安全的，要判斷）。Ch 18 的 **TSan**（ThreadSanitizer）是另一個並發偵測工具（編譯時插樁，比 helgrind 快很多），是現代的選擇。理解並發 bug 的特性（不確定、Heisenbug、結構性），你才知道為什麼要用專門工具，以及怎麼正確地 debug 它們——用 helgrind/TSan 偵測結構，而非 printf 瞎試。

## 故意弄壞:修復 race

```bash
cd ~/obslab
# 修復 race.c 的 race（用 mutex）
cat > race_fixed.c <<'EOF'
#include <stdio.h>
#include <pthread.h>
int counter = 0;
pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;   // 加鎖
void* worker(void *arg) {
    for (int i = 0; i < 100000; i++) {
        pthread_mutex_lock(&lock);     // 鎖保護
        counter++;                     // 現在是安全的（同步存取）
        pthread_mutex_unlock(&lock);
    }
    return NULL;
}
int main() {
    pthread_t t1, t2;
    pthread_create(&t1, NULL, worker, NULL);
    pthread_create(&t2, NULL, worker, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    printf("counter = %d (expected 200000)\n", counter);
    return 0;
}
EOF
gcc -g -pthread race_fixed.c -o race_fixed

# 跑：現在每次都對（鎖保證同步）
./race_fixed    # counter = 200000   ← 每次都對！
./race_fixed    # counter = 200000

# helgrind 驗證：沒有 race 了
valgrind --tool=helgrind ./race_fixed 2>&1 | grep -c 'data race'
# 0    ← 沒有 data race（修好了）

# → 修法：用 mutex 保護共享記憶體的存取
#   helgrind 驗證「race 沒了」（從報告 race 到 0 個 race）
```

> **修 race 的方法是「用鎖保護共享記憶體」，helgrind 驗證「race 沒了」——這完成了「偵測→修復→驗證」的閉環**。修復 data race 的方法是**用 mutex 保護共享記憶體的存取**——`pthread_mutex_lock` / `unlock` 包住 `counter++`，保證同一時間只有一個執行緒能存取 counter（同步），消除 race。修復後 `./race_fixed` **每次都給正確結果**（200000），不再不確定。`helgrind` 驗證——從原本報告 race 到現在 0 個 race，確認修好了。這完成了並發 bug 的完整流程：**helgrind 偵測 race → 用鎖修復 → helgrind 驗證 race 消失**。這個「偵測→修復→驗證」閉環是 debug 並發 bug 的正確方法（不是「加 printf 看有沒有變好」那種瞎試）。注意修復的代價——鎖有開銷（每次 lock/unlock），且鎖用多了可能造成 deadlock（鎖順序問題）或效能問題（鎖競爭）。所以並發程式設計是個平衡——要正確（用鎖避免 race）也要避免 deadlock（鎖順序一致）和效能問題（鎖粒度）。現代也有 lock-free 的技術（原子操作 atomic）避免鎖的開銷。但核心原則不變：**共享的可變狀態必須同步保護**，helgrind/TSan 是驗證你做對了的工具。練習 C 會用 helgrind/TSan 抓更複雜的並發 bug。掌握並發 bug 的偵測和修復，你能寫出正確的多執行緒程式——這是現代（多核）程式設計的核心能力。

## 動手練習

1. 偵測 race：對 race.c 用 helgrind，看它報告 data race 在 counter++，理解 race 的偵測

2. race 的不確定性：跑 race 多次，看結果每次不同（有時剛好對），理解為什麼難 debug

3. deadlock：用 helgrind 偵測 deadlock.c 的 lock order violation

4. 修復驗證：用 mutex 修 race，helgrind 驗證 race 消失（偵測→修復→驗證閉環）

5. 對比 drd：對同個程式用 helgrind 和 drd，看兩個工具的報告

## 本章重點整理

- data race：兩執行緒同時存取同一記憶體、至少一個寫、沒鎖保護 → 結果不確定（有時對有時錯）
- helgrind 偵測 race「即使這次剛好結果對」——偵測結構上的可能性（沒同步的共享存取），而非等它發生
- deadlock：鎖順序不一致（互相等對方的鎖）→ 凍結；helgrind 偵測 lock order violation
- 並發 bug 不能用 printf/gdb（改變時序造成 Heisenbug，Ch 3）——要用 helgrind/TSan 偵測結構
- 修法：race 用鎖保護共享記憶體、deadlock 統一鎖順序；helgrind 驗證（偵測→修復→驗證閉環）

## 自我檢核

- [ ] 能解釋 data race 是什麼，為什麼造成不確定的結果
- [ ] 理解 helgrind 偵測「結構上的 race 可能性」，為什麼這比「等 bug 發生」好
- [ ] 知道 deadlock 的根源（鎖順序不一致）和 helgrind 怎麼偵測
- [ ] 理解為什麼並發 bug 不能用 printf/gdb（Heisenbug）
- [ ] 會用鎖修復 race，用 helgrind 驗證

## 延伸閱讀

### 官方文件

- **[Helgrind manual](https://valgrind.org/docs/manual/hg-manual.html)** + **[DRD manual](https://valgrind.org/docs/manual/drd-manual.html)** — Valgrind
  - **讀哪裡**：data race 和 lock order 的偵測說明
  - **為什麼值得讀**：helgrind/drd 的權威

### 文章

- **[Data races 詳解](https://blog.regehr.org/archives/490)** — John Regehr
  - **這篇說什麼**：data race 為什麼是 undefined behavior、為什麼危險
  - **為什麼值得讀**：理解 race 的本質和危險性

### 書籍

- **《The Art of Multiprocessor Programming》— Herlihy & Shavit**
  - **讀哪幾章**：同步、鎖、race 那幾章
  - **這本書的定位**：並發程式設計的權威，理解 race/deadlock 的理論

下一章看 valgrind 的 profiling 工具——callgrind（呼叫圖+指令計數）和 cachegrind（快取模擬）。從「正確性」（memcheck/helgrind）轉到「效能分析」的另一個視角（精確的指令/快取分析，vs perf 的取樣）。

→ [Ch 17 valgrind profiling（callgrind/cachegrind）](./17-valgrind-profiling.md)
