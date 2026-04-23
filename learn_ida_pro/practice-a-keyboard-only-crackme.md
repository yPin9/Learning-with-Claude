# 練習 A — 純鍵盤解一個 crackme

> 目標：把 Ch 2–5 的快捷鍵全用上，不碰滑鼠、不寫任何 IDAPython，解開一個小型 crackme。逼肌肉記憶長出來。

## 為什麼是這個練習

你現在已經學了 **50+ 個快捷鍵**。問題是：不實戰根本記不住。看著 cheat sheet 按不叫內化，內化是「手指先動，腦袋才反應過來」。

這個練習的規則很極端：
- **禁用滑鼠**（真的。拔掉也可以）
- **禁用 IDAPython**（Part 2 才開始）
- **不准開搜尋引擎查快捷鍵**（用 `F1` / `Options → Shortcuts` 在 IDA 內查）

規則違反 = 重來。這個難度設計是刻意的。

## 任務規格

寫一支 C 程式當題目（你自己的 crackme）：

```c
// crackme.c — 編譯：gcc -O0 -o crackme crackme.c
#include <stdio.h>
#include <string.h>

struct User {
    int  level;           // 0x00
    int  credit;          // 0x04
    char name[16];        // 0x08
    int  checksum;        // 0x18
};

static int compute_checksum(struct User *u) {
    int s = u->level * 7 + u->credit;
    for (int i = 0; u->name[i]; i++) s ^= u->name[i] * (i + 1);
    return s;
}

static int check(struct User *u) {
    if (u->level < 10) return 0;
    if (u->credit != 0xDEADBEEF) return 0;
    if (strcmp(u->name, "admin") != 0) return 0;
    return u->checksum == compute_checksum(u);
}

int main(void) {
    struct User u;
    printf("level: ");    if (scanf("%d", &u.level) != 1) return 1;
    printf("credit: ");   if (scanf("%x", &u.credit) != 1) return 1;
    printf("name: ");     scanf("%15s", u.name);
    printf("checksum: "); if (scanf("%d", &u.checksum) != 1) return 1;

    if (check(&u)) puts("correct");
    else           puts("wrong");
    return 0;
}
```

編譯：

```bash
gcc -O0 -o crackme crackme.c         # Linux
x86_64-w64-mingw32-gcc crackme.c -o crackme.exe    # Cross-compile to Windows
```

**不要看原始碼**。刪掉 `crackme.c`，只保留 binary。丟進 IDA 當題目。

## 期望產出

解完之後：

1. **找出 4 個條件**：level / credit / name / checksum 要什麼值。
2. **實際跑 binary 用找到的輸入**，看到 `correct`。
3. **還原的 struct** 在 Local Types 裡看得到（`Shift+F1`）。
4. **function 都有意義的名字**：`check` / `compute_checksum` / `main`（不是 `sub_xxx`）。

## 解題步驟（只給鍵序，不告訴你答案）

### Phase 1：環境 onboarding
```
IDA 開 crackme
 → 等 AU: idle
 → Ctrl+S 存 IDB
 → Shift+F12  (看 strings — 心裡抓 "wrong" / "correct" / "level:" 位置)
```

### Phase 2：找 check function
```
從 strings window 雙擊 "correct" 或 "wrong"
 → X (xref)
 → 雙擊 caller，進入一個 function
 → F5  看偽代碼
 → 判斷這個 function 是什麼角色
```

猜想：這應該就是 `main`（它印 `correct`/`wrong`），或是 `check` 的 caller。

### Phase 3：還原 struct User
```
F5 中游標停在可疑 struct pointer（`a1` / `v3`）
 → 看存取 pattern: [reg+0x0], [reg+0x4], [reg+0x8], [reg+0x18]
 → Shift+F1 開 Local Types
 → Insert  寫入 struct：
     struct User { int a; int b; char c[16]; int d; };
 → 回 pseudocode
 → 游標在可疑變數上按 Y，填 User *
 → 看偽代碼立刻還原
```

### Phase 4：還原每個 field 的意義
```
從 main 的 scanf 呼叫順序：
 → 第一個 scanf %d 存到 [rbp + ??]  →  level
 → 第二個 scanf %x 存到 [rbp + ??]  →  credit
 → 第三個 scanf %15s 存到 [rbp + ??] →  name
 → 第四個 scanf %d 存到 [rbp + ??]  →  checksum
 → 在 Local Types 編輯 struct，把欄位名從 a/b/c/d 改為 level/credit/name/checksum
```

### Phase 5：找 check 與 compute_checksum
```
check 的特徵：對四個 field 做比較，回傳 0/1
 → N  改名為 check
 → Y  改 prototype 為 int check(User *u)
 → X  看誰呼叫，確認是 main

compute_checksum 的特徵：有迴圈，XOR，return int
 → N  改名 compute_checksum
 → Y  int compute_checksum(User *u)
```

### Phase 6：抽 4 個條件
```
F5 看 check
 → level < 10  代表什麼？      →  level 要 >= 10
 → credit != 0xDEADBEEF        →  credit 要 == 0xDEADBEEF
 → strcmp(name, "admin")       →  name 要 == "admin"
 → checksum == compute_checksum(u)
     →  需要逆算 compute_checksum
     →  設 level=10, name="admin", 算 s = 10*7 + 0xDEADBEEF
        然後 for i in range(5): s ^= "admin"[i] * (i+1)
```

### Phase 7：手算 checksum（或用計算機，但不能寫 IDAPython）

```
s = 70 + 0xDEADBEEF       = 0xDEADBF35  (assuming int wraparound)

i=0: s ^= 'a' * 1 = 0x61
i=1: s ^= 'd' * 2 = 0xC8
i=2: s ^= 'm' * 3 = 0x147  → 截成 int
i=3: s ^= 'i' * 4 = 0x1A4
i=4: s ^= 'n' * 5 = 0x226
```

算出最終值。

### Phase 8：跑 binary 驗證

```bash
./crackme
level: 10
credit: DEADBEEF
name: admin
checksum: <你算的值>
correct
```

## 實際解答（寫完再看）

**不要偷看**。

<details>
<summary>點開參考解答</summary>

條件：

- `level = 10`（或任何 `>= 10`）
- `credit = 0xDEADBEEF`
- `name = "admin"`
- `checksum` 用以下 Python 算（在 IDA 外，這不違反規則 — 規則是不寫 IDAPython）：

```python
s = 10 * 7 + 0xDEADBEEF
s &= 0xFFFFFFFF
for i, c in enumerate("admin"):
    s ^= ord(c) * (i + 1)
    s &= 0xFFFFFFFF
# 注意 C int 可能 signed，要轉回 signed
if s >= 0x80000000:
    s -= 0x100000000
print(s)
```

實際 checksum 數字每次編譯基本一致（跟 optimization 無關，純數學運算）。

打磨後的 pseudocode 應該接近：

```c
int check(User *u) {
  if ( u->level < 10 ) return 0;
  if ( u->credit != 0xDEADBEEF ) return 0;
  if ( strcmp(u->name, "admin") ) return 0;
  return u->checksum == compute_checksum(u);
}

int compute_checksum(User *u) {
  int s = 7 * u->level + u->credit;
  for (int i = 0; u->name[i]; ++i)
    s ^= (i + 1) * u->name[i];
  return s;
}
```

Local Types 裡的 struct：

```c
struct User {
  int level;
  int credit;
  char name[16];
  int checksum;
};
```

</details>

## 自我評估

解完後誠實回答：

- [ ] 全程沒碰滑鼠嗎？
- [ ] 用了 `X` 幾次？（應該至少 3 次）
- [ ] 用了 `N` 幾次？（應該至少 5 次，每個 function / field）
- [ ] 用了 `Y` 幾次？（至少 3 次）
- [ ] 按了 `Esc` / `Ctrl+Enter` 幾次？（應該很多 — 到處跳）
- [ ] 還原的 struct 欄位對齊和大小都對嗎？
- [ ] 實際跑 binary 看到 `correct` 了嗎？

如果任何一項是 no，**重做一次**。這個練習就是要打實。

## 延伸

解完覺得太簡單？加難度：

1. 編譯時加 `-O2`，看看 Hex-Rays 推斷的偽代碼複雜多少。
2. 用 `strip --strip-all` 去掉 symbol，重解一次。
3. 自己改 crackme：加一層 XOR 加密字串、把 `0xDEADBEEF` 用幾道運算藏起來、加反 debug（`ptrace(PTRACE_TRACEME)`）。

## 銜接

完成這個練習後，你對「手動分析的痛點」應該有切身感覺：

- 改 5 個 function 名字 — 手動做 1 分鐘。
- 改 50 個 — 手動做會想死。
- 改 500 個 stripped 函式 — 手動等同不可能。

Part 2 從下一章開始，我們把這些重複動作全丟給 IDAPython。

→ [Ch 7 IDAPython 入門](./07-idapython-intro.md)
