# 練習 D — 用純命令語言寫自動化指令

> **目標**：綜合 Part 4（TUI、.gdbinit、命令語言、自訂指令模式），不寫一行 Python，純用 GDB 命令語言做出一個實用的 debug 工具集：一個資料結構檢查器 + 一個自動 context 顯示。完成後你會具備「把重複 debug 動作自動化」的能力，並親身體會命令語言的天花板——為 Part 5 的 Python 鋪路。

## 背景與動機

真正讓你 debug 變快的，不是記得更多指令，而是把常做的事**自動化**。這個練習要你做兩個真實會用的工具：(1) 一個能檢查 linked list 健康狀態的指令（長度、有無環、值是否合法）；(2) 一個每次停下來自動顯示「暫存器 + 程式碼 + stack」的 context。做完你會有一個迷你版 gef，而且全程不碰 Python——這讓你清楚知道命令語言能到哪、不能到哪。

## 任務規格

### 目標程式

```c
// listcheck.c — gcc -g -O0 listcheck.c -o listcheck
#include <stdio.h>
#include <stdlib.h>

typedef struct Node { int val; struct Node *next; } Node;

Node *push(Node *head, int v) {
    Node *n = malloc(sizeof *n);
    n->val = v; n->next = head;
    return n;
}

int main(void) {
    Node *head = NULL;
    for (int i = 1; i <= 5; i++) head = push(head, i * 10);
    // 故意製造一個環（debug 用）：把最後一個節點指回 head
    Node *last = head;
    while (last->next) last = last->next;
    // last->next = head;   // ← 取消註解就變成有環的 list
    printf("built list\n");      // ← break 在這
    return 0;
}
```

### 你要做的事

實作兩個工具（純命令語言，放進一個 `.gdb` 檔）：

**工具 1 — `listcheck <head>`**：對一個 linked list 回報：
- 節點總數
- 是否有環（用 Floyd 龜兔演算法）
- 所有節點的值（若有環，要能停止不無窮印）
- 是否有「看起來不合法」的 next 指標（非 NULL 但不像 heap 位址）

**工具 2 — `ctx`（context）+ `hook-stop`**：每次程式停下來時自動顯示：
- 主要暫存器（rax/rbx/rsp/rbp/rip）
- 當前 `$pc` 起 3 條反組譯指令
- stack 頂 4 個 slot（telescope）
- 當前原始碼行（若有）

### 驗收標準

- [ ] `listcheck head` 對正常 list 正確回報節點數與值
- [ ] 把 `last->next = head` 取消註解（變成環），`listcheck` 能偵測到環、不無窮迴圈
- [ ] `ctx` 能手動呼叫顯示完整 context
- [ ] `hook-stop` 讓每次 step/停下自動顯示 context
- [ ] 工具放進可 `source` 的檔案，並在 `.gdbinit` 載入
- [ ] 你能指出這兩個工具「如果用 Python 會好在哪」（命令語言的侷限）

## 期望輸出範例

```
(gdb) listcheck head
node[0] 0x5555...5e0: val=50
node[1] 0x5555...5c0: val=40
node[2] 0x5555...5a0: val=30
node[3] 0x5555...580: val=20
node[4] 0x5555...560: val=10
total: 5 nodes, cycle: NO

(有環時)
(gdb) listcheck head
... 印出節點 ...
CYCLE DETECTED after 5 nodes!
total: 5 nodes, cycle: YES
```

## 如果你卡住了

1. **無窮迴圈印不停？** 有環的 list 用普通 while 走會永遠跑。先用 Floyd 偵測環、或設一個最大節點數上限（`if $i > 10000 loop_break`）。
2. **怎麼判斷指標「不像 heap 位址」？** 簡化判斷：合法的 heap 指標通常 `> 0x1000` 且 8-byte 對齊（`addr & 7 == 0`）。命令語言裡 `if ($p->next != 0) && (((long)$p->next & 7) != 0)` 算近似。
3. **hook-stop 報錯？** hook 裡用到的指令（如 `ctx`）要先定義好。把 `define ctx` 放在 `define hook-stop` 之前。
4. **stack telescope 怎麼寫？** `x/gx $sp + $i*8` 在迴圈裡跑，`$i` 從 0 到 3。

## 實作步驟建議

### Step 1：基本走訪 + 計數

先寫最簡單的 `listcheck`：走訪 + 印值 + 計數，假設無環。

```gdb
define listcheck
  set $p = $arg0
  set $i = 0
  while $p != 0
    printf "node[%d] %p: val=%d\n", $i, $p, $p->val
    set $p = $p->next
    set $i = $i + 1
  end
  printf "total: %d nodes\n", $i
end
```

子目標：對正常 list 跑出 5 個節點。

### Step 2：加環偵測（Floyd）

在走訪外，先跑一次龜兔判斷有沒有環，避免無窮迴圈。

子目標：取消 `last->next = head` 註解重編，`listcheck` 能報 cycle: YES 而不卡死。

### Step 3：加非法指標檢查

走訪時檢查每個 `next`：非 NULL 但未對齊（`& 7 != 0`）或太小（`< 0x1000`）就標警告。

子目標：（可手動把某節點 next 設成怪值 `set var node->next = 0x1` 測試）能標出可疑指標。

### Step 4：寫 ctx + hook-stop

```gdb
define ctx
  printf "── regs ──\n"
  printf "rax=%#lx rbx=%#lx rsp=%#lx rbp=%#lx\n", $rax, $rbx, $rsp, $rbp
  printf "rip=%#lx\n", $rip
  printf "── code ──\n"
  x/3i $pc
  printf "── stack ──\n"
  set $i = 0
  while $i < 4
    printf "%#lx: ", $sp + $i*8
    x/gx $sp + $i*8
    set $i = $i + 1
  end
end

define hook-stop
  ctx
end
```

子目標：step 幾步，每步自動看到 context。

### Step 5：模組化

把全部放進 `~/scripts/gdb/listtools.gdb`，`.gdbinit` 加 `source ~/scripts/gdb/listtools.gdb`。

## 完整參考解答

**自己做到 Step 3 再看。**

<details>
<summary>點開完整工具實作</summary>

```gdb
# ~/scripts/gdb/listtools.gdb
# 用法：source 這個檔，然後 listcheck <head>

# ---- 工具 1：list 健康檢查 ----
define listcheck
  # 先用 Floyd 判斷有沒有環
  set $slow = $arg0
  set $fast = $arg0
  set $has_cycle = 0
  while $fast != 0 && $fast->next != 0
    set $slow = $slow->next
    set $fast = $fast->next->next
    if $slow == $fast
      set $has_cycle = 1
      loop_break
    end
  end

  # 走訪並印（有環時用 Floyd 已知的安全節點數上限保護）
  set $p = $arg0
  set $i = 0
  set $limit = 100000
  while $p != 0 && $i < $limit
    # 非法指標檢查
    set $bad = 0
    if $p->next != 0
      if (((long)$p->next) & 7) != 0
        set $bad = 1
      end
      if ((long)$p->next) < 0x1000
        set $bad = 1
      end
    end
    if $bad == 1
      printf "node[%d] %p: val=%d  next=%p  <-- SUSPICIOUS POINTER!\n", \
             $i, $p, $p->val, $p->next
    else
      printf "node[%d] %p: val=%d\n", $i, $p, $p->val
    end
    set $p = $p->next
    set $i = $i + 1
    # 有環時，走過一圈就停（避免無窮印）
    if $has_cycle == 1 && $i > 1000
      printf "CYCLE DETECTED, stopping after %d nodes!\n", $i
      loop_break
    end
  end
  printf "total: %d nodes, cycle: %s\n", $i, $has_cycle ? "YES" : "NO"
end
document listcheck
  Check a singly-linked list: count, cycle detection, suspicious pointers.
  Usage: listcheck <head_pointer>
end

# ---- 工具 2：context 顯示 ----
define ctx
  printf "─────────────── registers ───────────────\n"
  printf "rax=%#-18lx rbx=%#-18lx\n", $rax, $rbx
  printf "rsi=%#-18lx rdi=%#-18lx\n", $rsi, $rdi
  printf "rbp=%#-18lx rsp=%#-18lx\n", $rbp, $rsp
  printf "rip=%#lx  ", $rip
  output/a $rip
  echo \n
  printf "─────────────── code ───────────────\n"
  x/3i $pc
  printf "─────────────── stack ───────────────\n"
  set $i = 0
  while $i < 4
    printf "%#lx|+%02x: ", $sp + $i*8, $i*8
    x/gx $sp + $i*8
    set $i = $i + 1
  end
  printf "─────────────────────────────────────\n"
end
document ctx
  Show a gef-style context: registers, code, stack.
end

# 每次停下自動顯示（取消註解啟用）
# define hook-stop
#   ctx
# end
```

`.gdbinit` 裡：

```gdb
source ~/scripts/gdb/listtools.gdb
```

**解答說明**：

- **環偵測用 Floyd**：先跑龜兔，知道有沒有環，再決定走訪策略——避免「邊走邊判斷」的複雜度。
- **多重保護**：`$i < $limit` 上限 + 有環時走一圈就停，雙保險防無窮迴圈。
- **非法指標啟發式**：對齊（`& 7`）+ 範圍（`< 0x1000`）是「看起來不像合法 heap 指標」的快速判斷，呼應練習 B 的「指標值像 ASCII」直覺。
- **context 用 printf 對齊**：`%#-18lx` 做欄寬對齊，盡量讓輸出整齊——但你會發現命令語言的格式化能力到此為止（不能上色、不能根據值動態決定格式）。

**這就是命令語言的天花板**：能走訪、能判斷、能 printf，但——
- 不能上色（gef 的紅綠藍 context 做不到）
- 不能「自動跟隨指標」telescope（stack 上的值若是指標，無法遞迴解讀並顯示它指向什麼）
- 不能解讀型別（無法自動判斷 stack slot 是 int/指標/字串）
- 非法指標檢查很笨拙（無法真的查 `info proc mappings` 確認位址落在哪個段）
- 程式碼難維護（一長就難讀）

這五點全部是 Part 5 Python 能優雅解決的——Final Project 的 context 視窗會把上面每一點做到位。

</details>

## 測試用例

| 情境 | 預期 |
|---|---|
| 正常 5 節點 list | 印 5 個節點，cycle: NO |
| 取消註解製造環 | 偵測到環，cycle: YES，不無窮迴圈 |
| 手動 `set var node->next = 0x1` | 標出 SUSPICIOUS POINTER |
| `ctx` 手動呼叫 | 顯示 regs/code/stack |
| 啟用 hook-stop 後 step | 每步自動顯示 context |

## 延伸挑戰（加分）

1. **doubly linked list 檢查**：擴充 `listcheck` 驗證 `node->next->prev == node`（雙向一致性）。
2. **hash table 走訪**：對一個 bucket 陣列 + chaining 的 hash table 寫走訪指令。
3. **把 `ctx` 做成條件式**：只在組語模式或特定函式裡顯示完整 context，其他時候精簡。
4. **對照 gef**：裝 gef，用它的 `context` 和你的 `ctx` 並排比較——列出 gef 多做了哪些你做不到的（上色、自動 telescope、heap 分析）。這份清單就是你 Final Project 的功能規格。
5. **預告 Python 改寫**：挑你這個工具裡「最痛」的一部分（多半是非法指標檢查或 telescope），想想 Python 會怎麼寫——記下來，Part 5 學完回來實作。

## 自我檢核

- [ ] 我能用純命令語言寫出一個會偵測環、不無窮迴圈的 list 檢查器
- [ ] 我能用 hook-stop 做出「每次停自動顯示 context」
- [ ] 我能把工具模組化成可 source 的檔案、納入 .gdbinit
- [ ] 我能具體列出命令語言相對 Python 的五個侷限
- [ ] 我知道哪些功能必須等 Python 才做得到（上色、telescope、型別解讀、heap 分析）

Part 4 完成——你已經能讓 GDB 為你工作。Part 5 是這門課的重頭戲：Python API。我們會把這個練習做的所有東西，升級成真正的、可發布的插件，最終長成你自己的 gef。

→ [Ch 22 Python API 入門](./22-python-api-intro.md)
