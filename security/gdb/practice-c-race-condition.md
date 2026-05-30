# 練習 C — 多執行緒 race condition 圍捕

> **目標**：綜合 Part 3（條件斷點、watchpoint、signal、多執行緒、scheduler-locking），在一個有 race condition 的多執行緒程式裡，用 GDB 重現、定位、並證明 race 的存在。完成後你會理解 race 為什麼難抓，以及 GDB 的能與不能（伏筆 Ch 35 rr）。

## 背景與動機

race condition 是工程師職涯最痛的 bug 類型：時有時無、加 log 就消失、在你的機器跑十萬次都對、上線就掛。這個練習用一個經典的「丟失更新」與「未初始化讀取」race，訓練你用 GDB 圍捕它——同時也讓你親身體會 GDB 在 race 面前的侷限，為什麼業界要發明 rr 與 ThreadSanitizer。

## 任務規格

### 產生有 race 的程式

```c
// race.c — gcc -g -O0 -pthread race.c -o race
#include <stdio.h>
#include <pthread.h>
#include <unistd.h>

#define NTHREAD 8
#define NITER   100000

long       counter = 0;          // race 1：無鎖累加
int        ready = 0;            // race 2：未同步的旗標
long       shared_result = 0;

void *worker(void *arg) {
    long id = (long)arg;
    while (!ready) { }           // busy-wait 等 main 說開始（race 2）
    for (int i = 0; i < NITER; i++) {
        counter++;               // race 1：load-add-store 非原子
    }
    if (id == 0) shared_result = counter;   // race 3：在別人還沒做完就讀
    return NULL;
}

int main(void) {
    pthread_t t[NTHREAD];
    for (long i = 0; i < NTHREAD; i++)
        pthread_create(&t[i], NULL, worker, (void*)i);
    ready = 1;                    // 放行所有 worker
    for (int i = 0; i < NTHREAD; i++)
        pthread_join(t[i], NULL);
    printf("counter = %ld (expected %d)\n", counter, NTHREAD * NITER);
    printf("shared_result = %ld\n", shared_result);
    return 0;
}
```

三個 race：
1. **counter++** 無鎖累加，最終值幾乎一定 < 800000（丟失更新）。
2. **ready** 旗標未用 memory barrier/atomic，理論上可能可見性問題（實務 x86 強記憶體模型較少出包，但仍是 UB）。
3. **shared_result** 在其他 thread 可能還沒做完時就被 thread 0 讀。

### 你要做的事

1. 跑很多次 `./race`，記錄 `counter` 的值——觀察它每次不同且小於期望值。
2. 用 GDB 證明 `counter++` 是非原子的 load-add-store。
3. 用 scheduler-locking 製造一個**確定的丟失更新**：手動讓兩個 thread 都 load 同一個舊值，只 store 一次。
4. 用 watchpoint 觀察 `counter` 的變化軌跡，看到「兩次寫入卻只 +1」的丟失。
5. 反思：為什麼純 GDB 很難「自然重現」這個 race？

### 驗收標準

- [ ] 你能展示 `counter` 多次執行的不同結果，並解釋為什麼 < 期望值
- [ ] 你能用 `disassemble` 指出 `counter++` 的 load/add/store 三條指令
- [ ] 你能用 scheduler-locking 手動編排一次「確定的」丟失更新
- [ ] 你能用 watchpoint 觀察並證明丟失
- [ ] 你能說出 GDB 在 race debug 的侷限，以及 rr / TSan 怎麼補

## 期望輸出範例

```
$ for i in 1 2 3; do ./race; done
counter = 743821 (expected 800000)
counter = 689332 (expected 800000)
counter = 800000 (expected 800000)     # 偶爾剛好對！這就是 race 的可怕
```

## 如果你卡住了

1. **每次 counter 都剛好 800000？** 你的 CPU 核心數少、或迴圈太短，race window 小。增加 NTHREAD、NITER，或在 `counter++` 前後加點別的運算拉長 window。
2. **怎麼手動製造確定丟失？** 用 `scheduler-locking on` 鎖住一個 thread，讓它 load counter 到暫存器後**先別 store**（停在 load 和 store 之間），切到另一個 thread 讓它完整做一次 +1，再切回第一個讓它 store——它 store 的是舊值，覆蓋掉第二個 thread 的成果。
3. **watchpoint 太多次觸發？** counter 變化八十萬次，watchpoint 會停到天荒地老。用 `watch counter if counter > 799990`（只在接近尾聲時看），或把 NITER 改到很小（如 5）。
4. **scheduler-locking 卡死？** 你鎖了一個 thread 去 continue，它在等別人。設回 `step`（Ch 16 踩雷）。

## 實作步驟建議

### Step 1：觀察 race 的不確定性

```
$ for i in $(seq 10); do ./race; done | sort | uniq -c
```

子目標：看到 counter 值散佈、幾乎都 < 800000，偶爾命中。建立「這是 race」的認知。

### Step 2：證明 counter++ 非原子

```
(gdb) break worker
(gdb) run
(gdb) disassemble worker
   ... mov counter,%rax ; add $1,%rax ; mov %rax,counter ...
```

子目標：指出三條指令，理解「兩個 thread 在 load 和 store 之間交錯」就會丟更新。

### Step 3：手動編排確定丟失（核心）

把 NITER 改成很小（例如 3），重編。然後：

```
(gdb) set scheduler-locking on
(gdb) break worker
(gdb) run                        # 停在某 thread 的 worker
(gdb) thread 2                   # focus thread 2
(gdb) 走到 counter++ 的 load 之後、store 之前（stepi 到 add 完）
(gdb) print $rax                 # thread 2 手上的 counter 值，假設是 5
(gdb) thread 3                   # 切到 thread 3
(gdb) 讓 thread 3 完整做一次 counter++（counter 變 6）
(gdb) thread 2                   # 切回 thread 2
(gdb) stepi                      # thread 2 執行 store，把它手上的 5+1=6 寫回
(gdb) print counter              # 還是 6！thread 3 的貢獻被覆蓋 → 丟失！
```

子目標：親手讓「兩次 +1 變成一次」，確定性地重現丟失更新。

### Step 4：用 watchpoint 看丟失軌跡

```
(gdb) watch counter
(gdb) continue                   # 每次 counter 變化都停，看 Old/New
```

子目標：在小 NITER 下，看到 counter 的每次寫入；對照「寫入次數 > 最終值的增量」。

### Step 5：反思 GDB 的侷限

子目標：寫下「為什麼 GDB 難自然重現 race」——斷點/單步嚴重改變 timing（Heisenbug），scheduler-locking 是人為編排不是自然發生。引出 rr（記錄一次真實執行再重播）與 TSan（編譯期插樁主動偵測）。

## 完整參考解答

**自己做到 Step 3 再看。**

<details>
<summary>點開 race 圍捕全流程</summary>

### 非原子證明

```
(gdb) disassemble worker
   0x...11a0 <+...>:  mov    0x2e8a(%rip),%rax    # load counter
   0x...11a7 <+...>:  add    $0x1,%rax            # +1
   0x...11ab <+...>:  mov    %rax,0x2e83(%rip)    # store counter
```

三條指令。race 視窗 = load 到 store 之間。若 thread A load 了 counter=100，還沒 store，thread B 也 load 100、+1、store 101，然後 A store 101（它手上的 100+1）——B 的更新被 A 覆蓋，兩次 +1 只得 101。

### 手動編排丟失（NITER=3 版）

```
(gdb) set scheduler-locking on
(gdb) break worker
(gdb) run
[停在 thread 2]
(gdb) info threads               # 確認有多個 worker
(gdb) thread 2
# 單步到 load 之後 add 之後、store 之前
(gdb) si  ... 直到 $pc 在 store 那條 ...
(gdb) p $rax                     # = 假設 counter 此時 0，load 後 rax=0，add 後 rax=1
$1 = 1
(gdb) p counter
$2 = 0                           # 記憶體裡還是 0
(gdb) thread 3
(gdb) set scheduler-locking on
# 讓 thread 3 完整做一輪 counter++
(gdb) si ... 走完 thread 3 的一次 load-add-store ...
(gdb) p counter
$3 = 1                           # thread 3 把 counter 寫成 1
(gdb) thread 2
(gdb) si                         # thread 2 執行它的 store，把手上的 1 寫回
(gdb) p counter
$4 = 1                           # 還是 1！thread 3 的 +1 被 thread 2 覆蓋 = 丟失一次更新
```

兩個 thread 各做了一次 +1，counter 卻只從 0 變 1。確定性地重現了丟失更新。

### watchpoint 軌跡

```
(gdb) set scheduler-locking off
(gdb) watch counter
(gdb) continue
Old value = 0, New value = 1      # thread X store
Old value = 1, New value = 2
... 觀察寫入次數 ...
```

在 NITER 很小時，數 watchpoint 觸發次數，對照最終值，能看到「寫入發生但增量丟失」。

### GDB 的侷限與補救

- **Heisenbug**：斷點/單步改變 timing，自然 race 在 GDB 下常常消失。我們是用 scheduler-locking **人為編排**才確定重現——這不是「抓到自然發生的 race」。
- **rr（Ch 35）**：record 一次真實執行（含確切的 thread 交錯），之後可重複、可 reverse 地重播**同一個** race。把「不可重現」變「完全可重現」。
- **ThreadSanitizer**：`gcc -fsanitize=thread`，編譯期插樁，在 race 真實發生時自動報告衝突的兩個存取與其 stack——主動偵測，不需重現。

**解答說明**：這題的三層教學——(1) race 的本質是非原子操作的交錯，用 disassemble 看穿；(2) scheduler-locking 讓你「導演」一場確定的 race，理解機制；(3) 最重要的——體會 GDB 對 race 是「事後檢視 + 人為編排」的工具，真正的 race 偵測/重現要靠 rr 與 TSan。知道工具的邊界，比會用工具更重要。

</details>

## 測試用例

| 設定 | 預期 | 說明 |
|---|---|---|
| 正常跑多次 | counter 散佈、多 < 800000 | race 的不確定性 |
| NITER=3 + 手動編排 | counter 增量 < 操作次數 | 確定丟失更新 |
| `-fsanitize=thread` 重編 | TSan 報 data race on counter | 主動偵測對照 |
| 加 `pthread_mutex` 保護 counter | counter 永遠 = 800000 | 正確修法驗證 |

## 延伸挑戰（加分）

1. **修好它**：用 `pthread_mutex_t` 或 C11 `atomic_long` 保護 counter，重跑驗證永遠 = 800000。比較 mutex 與 atomic 的效能差異。
2. **ThreadSanitizer 對照**：`gcc -fsanitize=thread -g race.c`，看 TSan 一次報出 race 的兩個衝突存取與 stack，對比你用 GDB 的辛苦。寫下心得。
3. **rr 重現**（學完 Ch 35 回來）：用 `rr record ./race` 錄一次「結果錯誤」的執行，`rr replay` 重播，用 reverse-continue 回到丟失發生的瞬間——體驗確定性 race debug。
4. **non-stop 觀察**（Ch 15）：用 non-stop mode 只停一個 worker、其他續跑，觀察 counter 在你眼前被別的 thread 改動。
5. **ready 旗標的 race**：把 `ready` 改成正確的 `atomic_int` 或加 barrier，理解 race 2 的記憶體可見性問題（在弱記憶體模型架構如 ARM 上更明顯——呼應 architecture/arm 課程）。

## 自我檢核

- [ ] 我能解釋為什麼 `counter++` 在多 thread 下會丟失更新（非原子的 load-add-store）
- [ ] 我能用 scheduler-locking「導演」一場確定的丟失更新
- [ ] 我能用 watchpoint 觀察 counter 的寫入軌跡
- [ ] 我理解為什麼斷點會讓 race「消失」（Heisenbug）
- [ ] 我能說出 GDB、rr、TSan 在 race debug 各自的角色與邊界

Part 3 完成。Part 4 轉向「讓 GDB 為你工作」——TUI 介面、.gdbinit 設定、命令語言腳本、自訂指令，把重複的 debug 動作自動化，為 Part 5 的 Python 插件鋪路。

→ [Ch 18 TUI 與 layout](./18-tui-and-layouts.md)
