# 練習 A — 抓一隻經典 segfault

> 目標：把 Ch 2–6 學到的東西串起來，用 GDB 從零定位一個 segfault，不改 source 的情況下搞清楚 root cause。

## 故事背景

你接手了一份舊 code：一個簡陋的「員工管理系統」。跑起來 crash。沒留原作者聯絡方式。

程式在下面。先**直接編譯、跑、看 crash**，不要作弊讀完全文才開 gdb。

## 題目：`crashy.c`

把下面這份 code 存成 `crashy.c`，編譯跑：

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct Employee {
    int id;
    char *name;
    int salary;
    struct Employee *manager;
} Employee;

Employee *create_employee(int id, const char *name, int salary) {
    Employee *e = malloc(sizeof(Employee));
    e->id = id;
    e->name = malloc(strlen(name) + 1);
    strcpy(e->name, name);
    e->salary = salary;
    e->manager = NULL;
    return e;
}

void print_employee(Employee *e) {
    printf("[#%d] %s (salary: $%d)", e->id, e->name, e->salary);
    if (e->manager) {
        printf(", manager: %s", e->manager->name);
    }
    printf("\n");
}

void raise_salary(Employee *e, int amount) {
    e->salary += amount;
}

void fire(Employee *e) {
    free(e->name);
    free(e);
}

Employee *find_highest_paid(Employee **employees, int n) {
    Employee *best = employees[0];
    for (int i = 1; i <= n; i++) {
        if (employees[i]->salary > best->salary) {
            best = employees[i];
        }
    }
    return best;
}

int main(void) {
    Employee *alice = create_employee(1, "Alice",  80000);
    Employee *bob   = create_employee(2, "Bob",    60000);
    Employee *carol = create_employee(3, "Carol",  90000);
    Employee *dave  = create_employee(4, "David",  55000);

    alice->manager = carol;
    bob->manager   = carol;
    dave->manager  = alice;

    Employee *team[4] = { alice, bob, carol, dave };

    printf("=== Team ===\n");
    for (int i = 0; i < 4; i++) {
        print_employee(team[i]);
    }

    Employee *top = find_highest_paid(team, 4);
    printf("\nHighest paid: %s\n", top->name);

    raise_salary(top, 10000);
    printf("After raise:\n");
    print_employee(top);

    fire(bob);
    fire(bob);   // ← 業務邏輯「有時候」會連續觸發兩次

    printf("Done.\n");
    return 0;
}
```

編譯：

```bash
gcc -g -O0 -fno-omit-frame-pointer crashy.c -o crashy
./crashy
```

你會看到它印了一些東西然後 crash。**你的任務**：用 gdb 找出所有 bug（不只一個），並且**不改 source**的狀況下描述：

1. crash 發生在哪個函式、哪一行？
2. 為什麼會 crash？（root cause，不是「指標無效」這種表面答案）
3. 這份 code 還有哪些潛在問題，即使今天沒 crash 改天也會？

## 實戰步驟建議

### Step 1：讓 crash 自己講話

```bash
gdb -q ./crashy
(gdb) run
```

看 crash 時的 signal、位址、函式、行號。這是最便宜的資訊。

### Step 2：bt 看呼叫鏈

`bt`、`bt full` 看清楚是從哪條路徑走到 crash 的。把每一層 frame 的 args 記下來。

### Step 3：`info locals` / `p`

在 crash 的 frame 逐一印 local、印參數。哪個指標是奇怪的值（0x0、0xbebebebebebebebe、0x…非法位址）？

### Step 4：往上推

如果 crash 的 frame 裡資料看起來是「被動的」（收到壞的參數才 crash），往上一層用 `up` 看它是怎麼傳來的。一路往上直到找到「它本來是好的，到某個地方就壞了」那一層。

### Step 5：重跑 + watchpoint

現在你懷疑哪個變數在某處被改壞，`watch` 它。重跑，看它在哪一行被改。

### Step 6：發現第一個 bug 後，繼續找

這份 code 至少有 **三個** bug。一次修一個在腦中，找出全部。

## 提示（寫完自己的分析再看）

<details>
<summary>提示 1：crash 的那一刻</summary>

`find_highest_paid` 迴圈條件寫 `i <= n`，應該是 `i < n`。這個 off-by-one 會讓它讀 `employees[4]`，但 `team[]` 只有 4 個 element（index 0–3）。讀到 stack 上 `team[]` 後面的亂七八糟記憶體（通常是其他 local variable 或 saved registers），強轉成 `Employee *` 再 deref `->salary` 就炸。

這是**第一個**明顯 bug。

</details>

<details>
<summary>提示 2：double free</summary>

main 最後連續 `fire(bob)` 兩次。第一次 free 了 `bob->name` 和 `bob`，第二次又 free 一次 — 這是 **double free**。在近代 glibc 上會被 abort 抓到（`double free or corruption`），但如果你先 fix 了 find_highest_paid 讓程式跑到這裡，才會看到這個 crash。

</details>

<details>
<summary>提示 3：dangling pointer</summary>

`alice->manager = carol`、`dave->manager = alice`、`bob->manager = carol`。如果在某個變種情境裡，有人 `fire(carol)`，然後再 `print_employee(alice)`，就會讀 freed memory。這份 code 目前沒觸發，但是個**未爆彈**。

</details>

<details>
<summary>提示 4：如果你找不到第三個</summary>

`create_employee` 沒檢查 `malloc` 是否回傳 NULL。雖然現代系統記憶體很多、這個小程式不會真的 malloc 失敗，但這是嚴重的錯誤處理漏洞，生產環境會爆。

另外，`raise_salary` 如果 `amount` 是負數可以 overflow（雖然不是 memory bug，是業務邏輯 bug）。

</details>

## 完整分析（寫完再看）

<details>
<summary>全解</summary>

### Bug #1：find_highest_paid 的 off-by-one

```c
for (int i = 1; i <= n; i++) {   // ← 應該是 i < n
```

當 `n == 4`，迴圈會跑 `i = 1, 2, 3, 4`。`employees[4]` 越界。`employees` 是 stack 上的 `Employee *[4]`，`employees[4]` 讀到的是 array 後面的 stack 資料 — 通常是 saved rbp、return address、或其他 local variable 的位元組，強轉成 `Employee *` 幾乎一定不是有效指標。

**在 gdb 裡驗證：**

```
(gdb) r
Program received signal SIGSEGV, Segmentation fault.
find_highest_paid (employees=..., n=4) at crashy.c:46
46              if (employees[i]->salary > best->salary) {

(gdb) p i
$1 = 4

(gdb) p employees[i]
$2 = (Employee *) 0x7fffffffXXXXXX    ← 一個奇怪的位址

(gdb) x/gx &employees[4]
0x7fffffffYYYY: 0x00007fffffffZZZZ    ← 這是 employees 陣列後面的記憶體
```

### Bug #2：double free（在 main 最後）

```c
fire(bob);
fire(bob);  // ← 釋放已經釋放的記憶體
```

`fire` 第一次 free 了 `bob->name` 跟 `bob`，但沒把 `bob` 設成 NULL。第二次 `fire(bob)` 傳進去還是那個已經失效的指標，`free(e->name)` 讀 freed memory 算 UB，`free(e)` 算 double free。

**在 gdb 裡驗證（需先修 bug #1）：**

```
(gdb) r
...
free(): double free detected in tcache 2
Program received signal SIGABRT, Aborted.
```

glibc 的 tcache 會幫你抓，印出類似的訊息。

### Bug #3：missing NULL check on malloc

`create_employee` 假設 `malloc` 永遠成功。不做 NULL check 是程式員的壞習慣。在 OOM 情境或記憶體有限的嵌入式環境會馬上炸。

### 還可以挑的

- `print_employee` 用 `printf` 沒檢查 e 是不是 NULL。`print_employee(NULL)` 會爆。
- `fire(e)` 應該收 `Employee **e` 並把 caller 的指標設成 NULL，避免 dangling。（或在 caller 那邊做。）

</details>

## 測試用例（驗證你修完後的版本）

如果你真的改了這份 code，修完應該能通過：

```c
/* 修正後至少要能跑完 */
int main(void) {
    Employee *alice = create_employee(1, "Alice", 80000);
    Employee *bob   = create_employee(2, "Bob",   60000);
    Employee *team[2] = { alice, bob };
    Employee *top = find_highest_paid(team, 2);
    assert(top == alice);
    fire(alice);
    fire(bob);
    return 0;
}
```

`fire(bob); fire(bob);` 當然不該連續兩次，修時改成一次即可，或讓 fire 接受 `Employee **` 把它設 NULL。

## 自我檢核

- [ ] 我能從 crash 的 bt 推到 root cause
- [ ] 我能用 `p` 跟 `x` 確認一個指標是否合法
- [ ] 我知道 glibc 會幫忙抓 double free
- [ ] 我能用 watchpoint 追蹤一個值被改壞的時間點
- [ ] 我不會看到 crash 就只修那一行，而會繼續找其他未爆彈

做完這個練習，你已經具備「用 GDB 抓一般 bug」的能力。Part 3 我們進階到 TUI、反組譯、signal handling、reverse debugging — 讓你的 GDB 火力再升一級。

→ [Ch 7 TUI 模式與 layout](./07-tui-mode.md)
