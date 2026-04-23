# 練習 B — 用 KLEE 找一個故意放漏洞的小程式

> 目標：實戰跑一次 KLEE 找 OOB、UAF、assertion bug。看 test case 生成、用 klee-replay 重現、跑 `klee-stats` 看 coverage。

## 設計一個有料的 target

我們做一個 `task manager`：接受 command line input，管理固定數量的 task。故意塞三個 bug。

```c
// tm.c - task manager with planted bugs
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <klee/klee.h>

#define MAX_TASKS 10

struct task {
    int id;
    char name[16];
    int active;
};

static struct task tasks[MAX_TASKS];
static int task_count = 0;

int add_task(int id, const char* name) {
    if (task_count >= MAX_TASKS) return -1;
    tasks[task_count].id = id;
    strncpy(tasks[task_count].name, name, sizeof(tasks[task_count].name));
    // BUG 1: strncpy 不保證 null terminate
    tasks[task_count].active = 1;
    task_count++;
    return 0;
}

int remove_task(int idx) {
    // BUG 2: 沒檢查 idx < 0
    if (idx >= task_count) return -1;
    tasks[idx].active = 0;
    return 0;
}

int find_task(int id) {
    for (int i = 0; i < task_count; i++) {
        if (tasks[i].id == id && tasks[i].active) {
            return i;
        }
    }
    return -1;
}

int get_name_length(int idx) {
    // BUG 3: 沒檢查 idx < 0 或 ≥ task_count
    return strlen(tasks[idx].name);
}

int main() {
    int cmd;
    klee_make_symbolic(&cmd, sizeof(cmd), "cmd");
    klee_assume(cmd >= 0 && cmd < 4);

    int arg_a;
    char arg_b[16];
    klee_make_symbolic(&arg_a, sizeof(arg_a), "arg_a");
    klee_make_symbolic(arg_b, sizeof(arg_b), "arg_b");

    switch (cmd) {
        case 0:
            return add_task(arg_a, arg_b);
        case 1:
            return remove_task(arg_a);
        case 2:
            return find_task(arg_a);
        case 3:
            return get_name_length(arg_a);
    }
    return 0;
}
```

三個 bug：

- **Bug 1 (add_task)**：strncpy 在 name 長度 == 16 時不會 null-terminate，後續 strlen 讀 OOB
- **Bug 2 (remove_task)**：idx < 0 時 tasks[idx] OOB
- **Bug 3 (get_name_length)**：idx 沒檢 lower bound 跟 upper bound 的任一

## Step 1 - build

```bash
mkdir -p ~/symex/ex-b && cd ~/symex/ex-b
# 把上面 code 存成 tm.c

docker run --rm -v $(pwd):/work -w /work klee/klee:3.0 /bin/bash -c "
    clang -I /usr/local/include -emit-llvm -c -g -O0 \
        -Xclang -disable-O0-optnone \
        tm.c -o tm.bc
"
```

## Step 2 - 跑 KLEE

```bash
docker run --rm -v $(pwd):/work -w /work klee/klee:3.0 /bin/bash -c "
    klee --libc=uclibc --posix-runtime --optimize \
         --search=random-path \
         --max-memory=2000 \
         --max-time=120 \
         tm.bc
"
```

預期輸出：

```
KLEE: WARNING: undefined reference to function: printf
...
KLEE: ERROR: tm.c:37: memory error: out of bound pointer  ← Bug 2 (remove_task, idx < 0)
KLEE: ERROR: tm.c:57: memory error: out of bound pointer  ← Bug 3 (get_name_length, idx < 0)
KLEE: done: total instructions = XXXXX
KLEE: done: completed paths = N
KLEE: done: generated tests = M
```

## Step 3 - 看產出 test case

```bash
ls klee-out-0
# test000001.ktest  test000002.ktest  ...
# info  messages.txt  run.stats

docker run --rm -v $(pwd):/work -w /work klee/klee:3.0 \
    ktest-tool klee-out-0/test000001.ktest
# 會看到 cmd, arg_a, arg_b 的具體 byte 內容
```

看哪個 test 觸發了 error：

```bash
docker run --rm -v $(pwd):/work -w /work klee/klee:3.0 \
    ls klee-out-0/*.err
# 如 test000003.ptr.err （memory error）
```

對應的 test 就是能觸發那個 bug 的 input。

## Step 4 - 用 klee-replay 或 concrete 重現

### 方法 A：KLEE replay

```bash
# 重新編譯成 native binary
clang -I /usr/local/include tm.c -o tm_native

KTEST_FILE=klee-out-0/test000003.ktest ./tm_native
```

（因為我們的 target 用 klee_make_symbolic，原生跑會是 `undefined reference`。解法是加 stub）

### 方法 B：手寫 reproducer

看 ktest 給出的 cmd / arg_a / arg_b 具體值，手寫一個 regular C main（不用 klee_make_symbolic）來 reproduce：

```c
int main() {
    // 從 ktest 看出的值
    int cmd = 1;
    int arg_a = -1;  // ← Bug 2 trigger
    
    return remove_task(arg_a);
}
```

跑：segfault 或 undefined behavior（記憶體被踩）。

## Step 5 - 看 coverage

```bash
docker run --rm -v $(pwd):/work -w /work klee/klee:3.0 \
    klee-stats klee-out-0
```

應該看到：
- ICov：應該接近 100%（target 小）
- Generated tests：幾十個
- Solver time：幾秒

如果 ICov 不是 100%，用 --write-cov 產生 gcov report 看哪幾行沒跑到。

## Step 6 - 調 KLEE flags 看差別

### 關 --optimize 跑一次

```bash
klee --libc=uclibc --posix-runtime tm.bc
# 觀察：path 數跟跑的時間都上升
```

### 換 searcher

```bash
klee --libc=uclibc --posix-runtime --optimize \
     --search=bfs tm.bc
```

看 tests 產生順序有沒有變、total path 數有沒有變。

### klee_assume 的效果

把 `klee_assume(cmd >= 0 && cmd < 4)` 註解掉、重跑。path 數應該爆多，因為 switch 的 default case 加上所有 `cmd` 的可能值都探索。

## Step 7 - fix bug 後驗證

patch tm.c：

```c
int remove_task(int idx) {
    if (idx < 0 || idx >= task_count) return -1;
    tasks[idx].active = 0;
    return 0;
}

int get_name_length(int idx) {
    if (idx < 0 || idx >= task_count) return -1;
    return strlen(tasks[idx].name);
}

int add_task(int id, const char* name) {
    if (task_count >= MAX_TASKS) return -1;
    tasks[task_count].id = id;
    strncpy(tasks[task_count].name, name, sizeof(tasks[task_count].name) - 1);
    tasks[task_count].name[sizeof(tasks[task_count].name) - 1] = 0;   // 確保 null term
    tasks[task_count].active = 1;
    task_count++;
    return 0;
}
```

重新跑 KLEE：

```
KLEE: done: total instructions = ...
KLEE: done: completed paths = N
KLEE: done: generated tests = M
（沒有 ERROR 行）
```

**patch 有效 iff KLEE 不再報 bug**。這就是 **patch verification** workflow。

## Step 8 - 測試 strncpy 的 null-terminate 問題

Bug 1 是 `strncpy` 不 null-terminate。KLEE 報了嗎？

很可能 **沒報** — KLEE 對 strncpy 的 SimProcedure 會寫 16 byte，不 null terminate，但這本身不是 OOB。要觸發 bug 要**之後**對 name 做 `strlen`。

改 main 讓 bug 浮現：

```c
case 0:
    int r = add_task(arg_a, arg_b);
    // strncpy 可能沒 null-term，接下來 strlen 就 OOB
    return get_name_length(task_count - 1);
```

再跑 KLEE：

```
KLEE: ERROR: OOB read at tm.c:XX  ← strlen 讀出 tasks[] 邊界
```

這個練習教你：**bug 不一定在 source buggy 的地方觸發**。bug 發生時 location 可能是下游。看 KLEE backtrace 是 RE 的必備能力。

## Step 9 - 測 POSIX runtime

改 target 讓它從 file 讀 input，用 `--sym-files`：

```c
int main() {
    FILE* f = fopen("A", "r");
    if (!f) return 1;
    int cmd;
    fread(&cmd, sizeof(cmd), 1, f);
    // ...
}
```

跑：

```bash
klee --libc=uclibc --posix-runtime --optimize --sym-files 1 100 tm.bc
```

`--sym-files 1 100` 創 1 個 symbolic file，大小 100 byte。

## 你應該學到什麼

做完這練習你對 KLEE 有**具體的觸感**：

- KLEE 自動抓 memory error 很好用
- strncpy / memcpy / library function 的 edge case 不一定被 flagged
- KLEE 不會讀你的 mind — 你要把 symbolic 跟 assume 正確標記
- test case replay 是生產使用 KLEE 的關鍵 UX
- patch 後重跑驗證 bug 消失，是 KLEE 的**正面應用**

## 延伸

### 挑戰 1：用 KLEE 驗證 patch

寫兩個版本的 `remove_task`（buggy + fixed），用 KLEE 分別跑，確認 fixed 版沒 error。這是**真實 SDL 流程**的微縮。

### 挑戰 2：增加複雜度

加一個 `reschedule(int from, int to)` function，讓 idx 跨範圍 move task。KLEE 能抓出 integer overflow、alias 問題嗎？

### 挑戰 3：把 KLEE 加進 CI

寫一個 bash script 跑 KLEE、grep 出 ERROR 數、非零則 fail。這就是 KLEE-in-CI 的基本做法。

### 挑戰 4：對比 AFL

同個 target 也給 AFL 跑（把 klee_make_symbolic 換 stdin 讀）。比：
- AFL 跑多久找到同個 bug
- input corpus 是否合理

你會發現：AFL 秒找 OOB，但 strncpy 的 null-terminate 問題可能完全漏掉（因為沒 ASan）。兩個工具有不同 sensitivity。

## 提交

我建議你把結果寫成一個 short report：

```
ex-b-report.md
- Target description
- Bugs planted (3)
- KLEE tests generated: X
- KLEE coverage: Y%
- Bugs found: which ones, by which test
- Bug not found (and why)
- After patch: KLEE re-run results
```

這種 report 在 bug-bounty / vuln research 是重要 communication skill。練習一次。

## 自我檢核

- [ ] 三個 bug 中，KLEE 報了哪些
- [ ] 知道為什麼某些 bug 沒報（Bug 1 可能要下游才觸發）
- [ ] 會用 ktest-tool 看具體 input
- [ ] 會用 klee-stats 看 coverage
- [ ] Patch 後重跑、確認乾淨
- [ ] 比過 KLEE 跟 AFL 的表現

→ [Ch 14 — angr 架構：VEX IR、SimState、SimProcedure](./14-angr-architecture.md)
