# 練習 G — 寫一個 ops table callback 自動定位工具

> **目標**：把 Ch 28 的靜態方法、Ch 36 的 Kconfig 推論、Ch 47 的搜尋策略、Ch 50 的 build 產物，拼成一條指令：給一個 ops struct 型別和欄位名（例如 `file_operations` `read_iter`），列出 v6.18 tree 裡所有實作、定義位置、所屬 ops 變數、控制它的 Kconfig 選項，以及在我們的 build 裡有沒有真的被編進去。完成後你驗證了：你能把「找間接呼叫的候選」從一門手藝變成一個可重複、可檢驗的工具。
> **鴻溝**：本練習主要練**間接鴻溝**（自動化找賦值點）與**脈絡鴻溝**的一部分（哪些候選在這個 config 下根本不存在）。

> **環境**：Python 3.10（Ubuntu 22.04 內建），只用標準函式庫；Linux v6.18 tree 在 `~/code_reading/linux`，build 在 `~/code_reading/build`。參考解答在這個環境實際跑過，輸出照貼。

## 背景與動機

[Ch 28](./28-ops-tables-indirect-calls.md) 教過：追到 `f->f_op->read_iter(...)` 斷掉時，第一招是找 designated initializer。手動做是這樣：

```console
$ rg -n --type c '\.read_iter\s*=' . | wc -l
99
```

99 行。接下來你要逐行打開檔案確認：這是不是 `struct file_operations`（`.read_iter` 這個欄位名別的 struct 也可能有）？賦的值是函式還是巨集？函式定義在哪？這個檔案在我們的 config 下會被編譯嗎？99 行做一遍要一個下午，換個欄位又要再做一遍。

而且 `rg` 有它看不到的東西：

- `DEFINE_SHOW_ATTRIBUTE(foo)` 這類巨集展開出來的 `file_operations`，原始碼裡只有一行巨集呼叫，`.read = seq_read` 寫在 `include/linux/seq_file.h` 的巨集定義裡。
- 註解裡的 `.read_iter = xxx` 會被 `rg` 當成真的。
- 老程式碼的 positional 初始化（`{ open_fn, read_fn, NULL }`）或 GNU 的 `read: read_fn` 寫法，根本沒有 `.read_iter =` 這個字串。

真實世界裡，kernel 開發者用 cscope、clangd、elixir 做這件事（[Ch 48](./48-elixir-cscope-gtags.md)、[Ch 49](./49-clangd-lsp.md)），但這些工具回答的是「誰引用了 `anon_pipe_write`」，方向和我們相反。我們要的是「`file_operations.read_iter` 這一格，全 tree 填了哪些值」——這個查詢沒有現成指令。

## 任務規格

寫一個 Python 3 程式 `opsfind.py`：

**輸入（命令列）**

| 參數 | 說明 |
|---|---|
| `STRUCT` | ops struct 型別名，不含 `struct`，例如 `file_operations`、`seq_operations` |
| `FIELD` | 欄位名，例如 `read_iter` |
| `--tree DIR` | kernel 原始碼根目錄，預設 `~/code_reading/linux` |
| `--build DIR` | 選用。`O=` build 目錄，用來判斷 `.o` 是否存在 |
| `--path SUB` | 選用、可重複。只掃這些子目錄或檔案（開發時加速用） |
| `--json` | 選用。輸出 JSON 而不是表格 |

**輸出**：對每一個「`struct STRUCT` 變數的初始化裡、`FIELD` 被指派」的位置，回報：

1. 指派的值（實作函式名，或原樣的運算式）
2. 實作函式的定義位置 `file:line`（找不到要明說原因）
3. 所屬 ops 變數名與指派所在的 `file:line`
4. 該檔案的 Kconfig 條件鏈：從同目錄 Makefile 的 `obj-$(CONFIG_X) += file.o`（含複合物件 `foo-y := file.o`、`obj-$(CONFIG_Y) += foo.o`）一路往上推到父目錄的 `obj-$(CONFIG_Z) += dir/`
5. （有 `--build` 時）該 `.c` 對應的 `.o` 在 build 目錄裡是否存在

最後在 stderr 印出摘要：指派總數、不同實作數、被多個 ops 共用的實作。

**必須處理的情況**

| 情況 | 要求 |
|---|---|
| 一般 designated initializer | `.read_iter = foo,` |
| 有 `const`、`static`、`__ro_after_init` 等修飾 | 正確辨識變數名 |
| ops 陣列 `struct file_operations x[] = { {...}, {...} };` | 每個元素都要找 |
| 指派在 `#ifdef` 裡 | 回報條件 |
| 註解裡的假指派 | 不能算 |
| 巨集定義裡的 ops（`#define DEFINE_X(n) static const struct file_operations n##_fops = {...}`） | 標出巨集名；值是巨集參數時要明說 |
| 值是 `NULL` 或運算式（`IS_ENABLED(...) ? a : b`） | 原樣回報，不要假裝找到定義 |
| positional 初始化 `{ a, b, c }` | 從 struct 定義算出欄位順序，對應到正確的值 |
| GNU 舊式 `field: value` | 辨識 |

**限制**

- 只用 Python 3 標準函式庫（`re`、`os`、`argparse`、`json`……）。這個環境沒有 pip；tree-sitter、libclang 留給延伸挑戰。
- 不需要是完整的 C parser。允許的誤差要在「已知限制」裡寫清楚。
- 全 tree 掃一次 `file_operations read_iter`，在這台機器上要能在一分鐘內完成。

**驗收標準**

- `file_operations read_iter` 的指派集合要和 `rg -n --type c '\.read_iter\s*=' .` 完全一致（v6.18 上沒有註解、positional、巨集裡的 `.read_iter`，所以兩者應該相等）。
- 下方的 fixture 測試全部正確。

## 期望輸出範例

```
輸入：opsfind.py file_operations write_iter --path fs/pipe.c --build ~/code_reading/build
輸出：
fifo_pipe_write                  -> fs/pipe.c:604                             ops=pipefifo_fops @ fs/pipe.c:1249
                                    kconfig: obj-y (fs/pipe.o)   build: built
anon_pipe_write                  -> fs/pipe.c:431                             ops=pipeanon_fops @ fs/pipe.c:1260
                                    kconfig: obj-y (fs/pipe.o)   build: built

== 2 個指派，2 個不同的實作，0 個實作被多個 ops 共用
== 其中 2 個指派所在的 .o 在 /home/ypp/code_reading/build 裡真的有編出來
```

```
邊界輸入：opsfind.py file_operations no_such_field --path fs
輸出：
== 0 個指派，0 個不同的實作，0 個實作被多個 ops 共用
（exit code 0：找不到不是錯誤）
```

```
邊界輸入：opsfind.py（缺參數）
輸出：
opsfind.py: error: the following arguments are required: struct, field
```

## 測試用例

### 測試 fixture：一棵迷你 tree

真實 kernel 裡有些邊界情況已經絕跡（positional、GNU 冒號寫法），所以我們造一棵迷你 tree 把所有情況集中起來。建在 `~/code_reading/scratch/practice-g/fixture/`：

`include/linux/demo.h`：

```c
struct demo_ops {
	int (*open)(int);
	int (*read)(int, char *);   /* 第二個成員 */
	void (*release)(int);
};
#define DEFINE_DEMO_OPS(__name, __rd)			\
static const struct demo_ops __name ## _ops = {		\
	.open	= demo_generic_open,			\
	.read	= __rd,					\
}
#define DEFINE_DEMO_RO(__name) DEFINE_DEMO_OPS(__name, demo_ro_read)
```

`drivers/demo/Makefile`：

```make
obj-$(CONFIG_DEMO) += demo.o
demo-y := core.o legacy.o
obj-$(CONFIG_DEMO_EXTRA) += extra.o
```

`drivers/demo/core.c`：

```c
#include <linux/demo.h>

static int core_read(int fd, char *buf)
{
	return 0;
}

/* 一般 designated initializer，含 #ifdef */
static const struct demo_ops core_ops = {
	.open = demo_generic_open,
#ifdef CONFIG_DEMO_READ
	.read = core_read,
#endif
};

/* 同一個函式被第二個 ops 用 */
const struct demo_ops core_alt_ops __ro_after_init = {
	.read	= core_read,	/* .read = fake_in_comment, */
};

/* 陣列形式 */
static struct demo_ops many_ops[] = {
	{ .read = core_read },
	{ .read = NULL },
};

DEFINE_DEMO_RO(foo);
DEFINE_DEMO_RO(bar);
```

`drivers/demo/legacy.c`：

```c
#include <linux/demo.h>

int legacy_read(int fd, char *buf) { return 1; }
static int legacy_open(int fd) { return 0; }

/* 舊式 positional：依成員順序 open, read, release */
static struct demo_ops legacy_ops = {
	legacy_open, legacy_read, NULL
};

/* 更古老的 GNU 寫法 field: value */
static struct demo_ops gnu_ops = {
	open: legacy_open,
	read: legacy_read,
};
```

`drivers/demo/extra.c`：

```c
#include <linux/demo.h>
static const struct demo_ops extra_ops = {
	.read = IS_ENABLED(CONFIG_X) ? legacy_read : NULL,
};
```

### 測試表

| # | 輸入 | 預期輸出 | 說明 |
|---|---|---|---|
| 1 | fixture，`demo_ops read` | `core_ops` 的指派帶 `#ifdef CONFIG_DEMO_READ` | `#ifdef` 內的指派 |
| 2 | 同上 | `core_alt_ops` 被找到，變數名正確；`fake_in_comment` **不**出現 | `__ro_after_init` 修飾、註解裡的假指派 |
| 3 | 同上 | `many_ops[]` 兩筆：`core_read` 與 `NULL`，`NULL` 標成「明確設成 NULL」 | 陣列、NULL |
| 4 | 同上 | 摘要裡 `core_read x3` | 同一函式被多個 ops 共用 |
| 5 | 同上 | `legacy_ops` 的值是 `legacy_read`，標 `positional[1]` | 從 struct 定義算出 `read` 是第 1 個成員（0-based） |
| 6 | 同上 | `gnu_ops` 的值是 `legacy_read`，標 `gnu-colon` | 舊式寫法 |
| 7 | 同上 | `extra_ops` 的值原樣保留，標「運算式」；kconfig 是 `CONFIG_DEMO_EXTRA` | 運算式、不同的 obj 條件 |
| 8 | 同上 | `include/linux/demo.h` 那筆標 `macro DEFINE_DEMO_OPS`，值 `__rd` 標「巨集參數」 | 巨集定義裡的 ops |
| 9 | 同上 | `core.c`、`legacy.c` 的 kconfig 是 `CONFIG_DEMO (drivers/demo/demo.o)` | 複合物件 `demo-y` |
| 10 | v6.18，`file_operations read_iter` | 99 個指派，與 `rg` 的 99 行逐一相同 | 正確性基準 |
| 11 | v6.18，`file_operations write_iter --build` | 88 個指派；`fs/pipe.c` 兩筆都是 built | build 判斷 |
| 12 | v6.18，`file_operations read --macro-uses` | `DEFINE_SHOW_ATTRIBUTE` 被辨識為巨集，值 `seq_read` 找到定義 `fs/seq_file.c:151` | 真實的巨集包裹 |
| 13 | v6.18，`file_operations no_such_field` | 0 個指派，正常結束 | 空結果 |
| 14 | 缺參數 | argparse 錯誤訊息 | 非法輸入 |

## 如果你卡住了

1. **regex 找不到 struct 變數名**：先把 `struct file_operations` 後面可能出現的東西列出來：`const`、`__maybe_unused`、變數名、`[]`、`__ro_after_init`、`__section(...)`，最後是 `= {`。巨集裡的變數名長得像 `__name ## _fops`，也要能吃。一條 regex 寫不出來時，先用 `rg -o 'struct file_operations[^=;]*=\s*\{' . | sort | uniq -c | sort -rn | head -50` 看看真實世界的樣子。
2. **大括號配對被註解或字串搞亂**：先把註解和字串換成**等長的空白**（保留換行），再做所有的配對和搜尋。等長很重要，這樣 offset 和行號都不用重算。
3. **陣列初始化抓到太多或太少**：關鍵是「深度」。一般變數的欄位在 body 的第 0 層，陣列元素的欄位在第 1 層。巢狀的 `{}` 和 `()` 都要算進深度，否則 `.read_iter = foo(bar)` 之類的值會讓你的計數錯位。
4. **Kconfig 推論卡在複合物件**：`fs/ext4/Makefile` 是 `ext4-y := ... file.o ...` 加 `obj-$(CONFIG_EXT4_FS) += ext4.o`。找 `file.o` 時要發現它屬於 `ext4-y`，再遞迴去找 `ext4.o`。小心 `foo-y += foo.o` 這種自我參照，它會讓你無限遞迴（參考解答的第一版真的在 v6.18 上撞到 `RecursionError`）。
5. **找函式定義很慢**：別對每個名字掃一次全 tree。先在同一個檔案找（大部分 callback 是 `static`，就在同檔案）；剩下的名字合成一條 alternation regex，整棵 tree 只掃一遍。

## 實作步驟建議

### Step 1：前處理與檔案走訪

寫 `strip_comments()`（註解、字串換成等長空白）、`LineIndex`（offset → 行號）、`iter_sources()`（走訪 `.c`/`.h`，跳過 `.git`、`tools/`、`Documentation/`、`scripts/`）。

子目標：對 `fs/pipe.c` 跑 `strip_comments`，結果長度和原文相同，`text.count('\n')` 也相同。

### Step 2：找出所有 `struct STRUCT var = { ... }` 並抽出欄位

用 regex 找初始化的開頭，用 `match_brace()` 找結尾，在 body 裡找 `.FIELD =`，用 `depth_at()` 過濾巢狀層，用 `value_end()` 抓到同層的逗號為止當作值。

子目標：`--path fs/pipe.c` 對 `read_iter` 找到 `fifo_pipe_read`、`anon_pipe_read` 兩筆，行號是 1248、1259。

### Step 3：邊界情況

依序加上：陣列（深度 1）、`#ifdef` 堆疊、`#define` 偵測（往上找反斜線接續行）、GNU 冒號寫法、positional（從 `include/` 找 struct 定義、算成員順序）。每加一個就跑一次 fixture。

子目標：fixture 測試 1–8 通過。

### Step 4：找定義

`find_defs()`：在一個檔案裡找「行首不是空白、名字後面接 `(`、參數列結束後是 `{`」的位置。先同檔案，再全 tree。`.h` 裡的 `static inline` 空殼（`#ifndef CONFIG_X` 時的 stub）要讓位給 `.c` 的真定義。

子目標：`read_iter` 全 tree 跑，`generic_file_read_iter` 指向 `mm/filemap.c`、`seq_read_iter` 指向 `fs/seq_file.c`。

### Step 5：Kconfig 與 build

`parse_makefile()`（處理反斜線接續、`:=`/`+=`/`=`）、`obj_chain()`（obj 與複合物件）、`dir_chain()`（父目錄的 `dir/`，多行帶入同一目錄時是 OR）。`--build` 時檢查 `build/<path>.o`。

子目標：`fs/ext4/file.c` 得到 `CONFIG_EXT4_FS (fs/ext4/ext4.o) <- CONFIG_EXT4_FS (fs/ext4/)`。

### Step 6：整合與驗收

輸出格式、摘要、`--json`。最後做驗收：用 `--json` 輸出和 `rg` 的結果逐筆比對（見參考解答說明裡的比對腳本）。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作（opsfind.py，623 行）</summary>

```python
#!/usr/bin/env python3
"""opsfind.py -- 在 Linux kernel tree 裡找出 ops struct 某個欄位的所有實作。

用法：
    opsfind.py STRUCT FIELD [--tree DIR] [--build DIR] [--path SUBDIR ...]
               [--json] [--macro-uses]

例：
    opsfind.py file_operations read_iter --build ~/code_reading/build

只用 Python 3 標準函式庫；靠 regex + 括號配對，不是真正的 C parser。
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

SKIP_DIRS = {'.git', 'tools', 'Documentation', 'scripts'}

# ---------------------------------------------------------------- 前處理


_STRIP_RE = re.compile(
    r'//[^\n]*'                    # 行註解
    r'|/\*.*?\*/'                  # 區塊註解
    r'|"(?:\\.|[^"\\\n])*"'        # 字串
    r"|'(?:\\.|[^'\\\n])*'",       # 字元常數
    re.S)


def strip_comments(src):
    """把註解與字串內容換成空白，保留換行與長度，讓 offset 和行號不變。"""
    def repl(m):
        s = m.group(0)
        if s[0] in '"\'':
            return s[0] + ' ' * (len(s) - 2) + s[-1]
        return re.sub(r'[^\n]', ' ', s)
    return _STRIP_RE.sub(repl, src)


class LineIndex:
    """offset -> 行號（1-based），用二分搜尋。"""

    def __init__(self, text):
        self.starts = [0]
        for m in re.finditer(r'\n', text):
            self.starts.append(m.end())

    def line(self, off):
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1


def match_brace(text, open_pos):
    """text[open_pos] == '{'，回傳對應 '}' 的位置（找不到回 -1）。"""
    depth = 0
    for i in range(open_pos, len(text)):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
    return -1


def depth_at(body, pos):
    """body 內 pos 處的 {}/() 巢狀深度（不含最外層的 {）。"""
    d = 0
    for c in body[:pos]:
        if c in '{(':
            d += 1
        elif c in '})':
            d -= 1
    return d


def value_end(body, start):
    """從 start 開始，找到同層的 ',' 或結尾，回傳結束位置。"""
    d = 0
    for i in range(start, len(body)):
        c = body[i]
        if c in '{([':
            d += 1
        elif c in '})]':
            if d == 0:
                return i
            d -= 1
        elif c == ',' and d == 0:
            return i
    return len(body)


def in_macro_define(raw_lines, lineno):
    """lineno 這行是不是某個 #define 的一部分？是的話回傳巨集名稱。"""
    i = lineno - 1
    # 往上找：只要上一行以反斜線結尾，就還在同一個邏輯行
    while i > 0 and raw_lines[i - 1].rstrip().endswith('\\'):
        i -= 1
    m = re.match(r'\s*#\s*define\s+(\w+)', raw_lines[i])
    if m and (i == lineno - 1 or raw_lines[i].rstrip().endswith('\\')):
        return m.group(1)
    return None


def ifdef_context(body, pos):
    """回傳 body[:pos] 中尚未關閉的 #if/#ifdef 條件（由外而內）。"""
    stack = []
    for m in re.finditer(r'^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b([^\n]*)',
                         body[:pos], re.M):
        kw, rest = m.group(1), m.group(2).strip()
        if kw in ('if', 'ifdef', 'ifndef'):
            stack.append('%s %s' % (kw, rest))
        elif kw in ('elif', 'else'):
            if stack:
                stack[-1] = stack[-1] + ' / ' + kw + (' ' + rest if rest else '')
        elif kw == 'endif' and stack:
            stack.pop()
    return stack


# ---------------------------------------------------------------- struct 定義（給 positional 用）


def struct_members(tree, stype, cache={}):
    """在 include/ 底下找 struct STYPE { ... }，回傳成員名稱的順序串列。"""
    if stype in cache:
        return cache[stype]
    pat = re.compile(r'\bstruct\s+%s\s*\{' % re.escape(stype))
    for d, dirs, files in os.walk(os.path.join(tree, 'include')):
        for f in files:
            if not f.endswith('.h'):
                continue
            p = os.path.join(d, f)
            try:
                s = strip_comments(open(p, errors='replace').read())
            except OSError:
                continue
            m = pat.search(s)
            if not m:
                continue
            end = match_brace(s, m.end() - 1)
            body = s[m.end():end]
            names = []
            for decl in split_top(body, ';'):
                decl = re.sub(r'^\s*#.*$', '', decl, flags=re.M).strip()
                if not decl:
                    continue
                fp = re.search(r'\(\s*\*\s*(\w+)\s*\)', decl)   # 函式指標
                if fp:
                    names.append(fp.group(1))
                    continue
                ids = re.findall(r'(\w+)\s*(?:\[[^\]]*\])?\s*(?::\s*\d+)?\s*$', decl)
                if ids:
                    names.append(ids[-1])
            cache[stype] = (names, os.path.relpath(p, tree))
            return cache[stype]
    cache[stype] = (None, None)
    return cache[stype]


def split_top(body, sep):
    out, d, cur = [], 0, []
    for c in body:
        if c in '{(':
            d += 1
        elif c in '})':
            d -= 1
        if c == sep and d == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    if ''.join(cur).strip():
        out.append(''.join(cur))
    return out


# ---------------------------------------------------------------- 掃描初始化


def scan_file(tree, relpath, stype, field):
    path = os.path.join(tree, relpath)
    try:
        raw = open(path, errors='replace').read()
    except OSError:
        return []
    if field not in raw or stype not in raw:
        return []                          # 快速過濾：大部分檔案在這裡就跳過
    text = strip_comments(raw)
    raw_lines = raw.split('\n')
    li = LineIndex(text)
    hdr = re.compile(
        r'\bstruct\s+%s\s+'                         # struct file_operations
        r'(?:(?:const|__\w+)\s+)*'                  # const / __maybe_unused ...
        r'(\w+(?:\s*##\s*\w+)*)\s*'                 # 變數名，或巨集裡的 __name ## _fops
        r'(\[[^\]=;]*\])?\s*'                       # 陣列 []
        r'(?:__\w+(?:\([^)]*\))?\s*)*'              # __ro_after_init、__section(...)
        r'=\s*\{' % re.escape(stype))
    desig = re.compile(r'(?<![\w.>])\.\s*%s\s*=(?!=)' % re.escape(field))
    gnu_old = re.compile(r'(?<![\w.>?])\b%s\s*:(?!:)' % re.escape(field))
    hits = []
    for m in hdr.finditer(text):
        var, is_array = m.group(1), bool(m.group(2))
        open_pos = m.end() - 1
        close = match_brace(text, open_pos)
        if close < 0:
            continue
        body = text[open_pos + 1:close]
        want = 1 if is_array else 0
        var_line = li.line(m.start())
        macro = in_macro_define(raw_lines, var_line)
        found = False
        for style, rx in (('designated', desig), ('gnu-colon', gnu_old)):
            for a in rx.finditer(body):
                if depth_at(body, a.start()) != want:
                    continue
                vstart = a.end()
                vend = value_end(body, vstart)
                value = ' '.join(body[vstart:vend].split())
                value = value.rstrip('\\').strip()
                hits.append(dict(
                    value=value, style=style, var=' '.join(var.split()),
                    array=is_array,
                    var_line=var_line,
                    assign_line=li.line(open_pos + 1 + a.start()),
                    macro=macro,
                    ifdef=ifdef_context(body, a.start()),
                    file=relpath))
                found = True
        if found:
            continue
        # 沒有任何 designator：可能是 positional（舊式依序初始化）
        if re.search(r'(?<![\w.>])\.\s*\w+\s*=', body):
            continue                                  # 有別的 designator，只是沒設這個欄位
        inner = body
        if is_array:                                  # 只看第一個元素
            b = inner.find('{')
            if b < 0:
                continue
            inner = inner[b + 1:match_brace(inner, b)]
        items = [' '.join(x.split()) for x in split_top(inner, ',')]
        items = [x for x in items if x]
        if not items:
            continue
        members, defsite = struct_members(tree, stype)
        if members and field in members:
            idx = members.index(field)
            value = items[idx] if idx < len(items) else None
            if value:
                hits.append(dict(
                    value=value, style='positional[%d]' % idx, var=var,
                    array=is_array, var_line=var_line, assign_line=var_line,
                    macro=macro, ifdef=[], file=relpath))
    return hits


def iter_sources(tree, subpaths):
    for sub in subpaths:
        base = os.path.join(tree, sub)
        if os.path.isfile(base):
            yield os.path.relpath(base, tree)
            continue
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs
                             if x not in SKIP_DIRS and not x.startswith('.'))
            for f in sorted(files):
                if f.endswith(('.c', '.h')):
                    yield os.path.relpath(os.path.join(d, f), tree)


# ---------------------------------------------------------------- 找函式定義


IDENT = re.compile(r'^&?\s*([A-Za-z_]\w*)$')


def func_name(value):
    """'anon_pipe_write' -> 'anon_pipe_write'；'&foo' -> 'foo'；
    運算式（三元、巨集呼叫）回傳 None。"""
    m = IDENT.match(value)
    if not m or m.group(1) in ('NULL',):
        return None
    return m.group(1)


def find_defs(tree, rel, names):
    """在一個檔案裡找 col 0 開頭的函式定義。回傳 {name: line}。"""
    if not names:
        return {}
    try:
        raw = open(os.path.join(tree, rel), errors='replace').read()
    except OSError:
        return {}
    if not any(n in raw for n in names):
        return {}
    text = strip_comments(raw)
    li = LineIndex(text)
    alt = '|'.join(sorted(map(re.escape, names), key=len, reverse=True))
    # 定義：該行不以空白或 # 開頭；名字後面是 (...)，接著（可能跨行）是 {
    rx = re.compile(r'^(?![ \t#])[^;{}()\n]*?\b(%s)\s*\(' % alt, re.M)
    out = {}
    for m in rx.finditer(text):
        name = m.group(1)
        if name in out:
            continue
        p = m.end() - 1
        d = 0
        for i in range(p, min(len(text), p + 2000)):     # 走過參數列
            if text[i] == '(':
                d += 1
            elif text[i] == ')':
                d -= 1
                if d == 0:
                    tail = text[i + 1:i + 300]
                    t = re.match(r'\s*(?:__\w+(?:\([^)]*\))?\s*)*\{', tail)
                    if t:
                        out[name] = li.line(m.start(1))
                    break
    return out


def locate_definitions(tree, hits, all_files):
    want = defaultdict(set)              # file -> names
    names = set()
    for h in hits:
        n = func_name(h['value'])
        h['func'] = n
        if n:
            want[h['file']].add(n)
            names.add(n)
    loc = {}
    # 1. 同一個檔案
    for f, ns in want.items():
        if not f.endswith('.c'):
            continue    # .h 裡找到的可能是 static inline 空殼，交給全域搜尋決定
        for n, line in find_defs(tree, f, ns).items():
            loc.setdefault((f, n), (f, line))
    # 2. 全域：其餘名字掃一遍整個 tree
    missing = {n for f, ns in want.items() for n in ns if (f, n) not in loc}
    glob = {}
    if missing:
        # .h 裡常有 #ifndef CONFIG_X 時的 static inline 空殼，所以 .c 的定義優先
        for rel in all_files:
            pending = {n for n in missing
                       if n not in glob or glob[n][0].endswith('.h')}
            if not pending:
                break
            for n, line in find_defs(tree, rel, pending).items():
                if n not in glob or (glob[n][0].endswith('.h') and rel.endswith('.c')):
                    glob[n] = (rel, line)
    for h in hits:
        n = h['func']
        if not n:
            h['def'] = None
        else:
            h['def'] = loc.get((h['file'], n)) or glob.get(n)


# ---------------------------------------------------------------- Kconfig 推論


_mk_cache = {}


def parse_makefile(tree, d):
    """回傳 [(lhs, [objs])]，lhs 像 'obj-$(CONFIG_EXT4_FS)'、'ext4-y'。"""
    if d in _mk_cache:
        return _mk_cache[d]
    rules = []
    for name in ('Makefile', 'Kbuild'):
        p = os.path.join(tree, d, name)
        if not os.path.exists(p):
            continue
        txt = open(p, errors='replace').read()
        txt = re.sub(r'\\\n', ' ', txt)               # 接續行
        for line in txt.split('\n'):
            line = line.split('#', 1)[0]
            m = re.match(r'\s*([\w\-$()]+)\s*[:+]?=\s*(.*)', line)
            if m:
                rules.append((m.group(1), m.group(2).split()))
    _mk_cache[d] = rules
    return rules


def cond_of(lhs):
    m = re.search(r'\$\((CONFIG_\w+)\)', lhs)
    if m:
        return m.group(1)
    if lhs.endswith('-y') or lhs.endswith('-objs'):
        return 'y'
    return None


def kconfig_chain(tree, relfile, depth=0):
    """回傳條件串列，例如 ['CONFIG_EXT4_FS (fs/ext4/ext4.o)', 'CONFIG_EXT4_FS (fs/ext4/)']。"""
    if not relfile.endswith('.c') or depth > 8:
        return []
    d, base = os.path.split(relfile)
    return obj_chain(tree, d, base[:-2] + '.o', depth)


NOT_OBJ = re.compile(r'flags|subdir|targets|always|hostprogs|userprogs|extra|clean')


def obj_chain(tree, d, obj, depth):
    if depth > 8:
        return ['?（巢狀太深）']
    chain = []
    for lhs, objs in parse_makefile(tree, d):
        if obj not in objs or NOT_OBJ.search(lhs):
            continue
        c = cond_of(lhs)
        if lhs.startswith('obj-') or lhs.startswith('lib-'):
            chain.append('%s (%s)' % (c if c and c != 'y' else 'obj-y',
                                      os.path.join(d, obj)))
            chain += dir_chain(tree, d, depth + 1)
            return chain
        # 複合物件：ext4-y / ext4-$(CONFIG_X) / ext4-objs
        m = re.match(r'([\w\-]+?)-(?:y|objs|\$\((CONFIG_\w+)\))$', lhs)
        if m:
            composite = m.group(1) + '.o'
            if composite == obj:          # 像 foo-y += foo.o 這種自我參照
                continue
            if m.group(2):
                chain.append('%s (%s 的一部分)' % (m.group(2), composite))
            return chain + obj_chain(tree, d, composite, depth + 1)
    return chain + ['?（%s 找不到 %s）' % (os.path.join(d, 'Makefile'), obj)]


def dir_chain(tree, d, depth):
    if not d or depth > 8:
        return []
    parent, name = os.path.split(d)
    conds = []
    for lhs, objs in parse_makefile(tree, parent):
        if name + '/' in objs and not NOT_OBJ.search(lhs):
            conds.append(cond_of(lhs) or 'y')
    if not conds:
        return []  # 頂層目錄由根 Makefile/Kbuild 帶入，視為無條件
    # 同一個子目錄可能被好幾行帶入（obj-$(CONFIG_USB) += usb/、obj-$(CONFIG_USB_PHY) += usb/），是 OR 關係
    if 'y' in conds:
        here = []
    else:
        here = ['%s (%s/)' % (' || '.join(dict.fromkeys(conds)), d)]
    return here + dir_chain(tree, parent, depth + 1)


# ---------------------------------------------------------------- build 驗證


def load_sysmap(build):
    syms = set()
    p = os.path.join(build, 'System.map')
    if os.path.exists(p):
        for line in open(p):
            parts = line.split()
            if len(parts) == 3:
                syms.add(parts[2])
    return syms


def build_status(build, relfile, func, syms):
    if not relfile.endswith('.c'):
        return '-'
    o = os.path.join(build, relfile[:-2] + '.o')
    if not os.path.exists(o):
        return 'not built'
    if func and syms and func not in syms:
        return 'built (symbol 不在 System.map：inline 或在 module)'
    return 'built'


class LazyFiles:
    """第一次被迭代時才走訪整棵 tree。"""

    def __init__(self, tree):
        self.tree, self.cache = tree, None

    def __iter__(self):
        if self.cache is None:
            self.cache = list(iter_sources(self.tree, ['.']))
        return iter(self.cache)


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('struct', help='ops struct 型別，例如 file_operations')
    ap.add_argument('field', help='欄位名，例如 read_iter')
    ap.add_argument('--tree', default=os.path.expanduser('~/code_reading/linux'))
    ap.add_argument('--build', help='O= build 目錄，用來判斷 .o 是否真的被編進去')
    ap.add_argument('--path', action='append', default=None,
                    help='只掃這些子目錄（可重複），預設整棵 tree')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--macro-uses', action='store_true',
                    help='對巨集模板，數出它在 tree 中被展開幾次')
    args = ap.parse_args()

    tree = os.path.abspath(args.tree)
    files = list(iter_sources(tree, args.path or ['.']))
    hits = []
    for rel in files:
        hits += scan_file(tree, rel, args.struct, args.field)

    # 全 tree 的檔案清單只在需要時才走一遍（--path 掃小範圍時省時間）
    all_files = files if args.path is None else LazyFiles(tree)
    locate_definitions(tree, hits, all_files)
    syms = load_sysmap(args.build) if args.build else set()
    for h in hits:
        h['kconfig'] = kconfig_chain(tree, h['file'])
        if args.build:
            h['build'] = build_status(args.build, h['file'], h['func'], syms)

    if args.macro_uses:
        macros = {h['macro'] for h in hits if h['macro']}
        uses = defaultdict(int)
        # 先找「包裝巨集」：#define DEFINE_SIMPLE_ATTRIBUTE(...) DEFINE_SIMPLE_ATTRIBUTE_XSIGNED(...)
        wrappers = defaultdict(set)          # 內層巨集 -> {外層巨集}
        if macros:
            # 一個 #define 的完整邏輯行（含反斜線接續）
            drx = re.compile(r'^[ \t]*#[ \t]*define[ \t]+(\w+)\((?:\\\n|[^\n])*', re.M)
            for rel in all_files:
                if not rel.endswith('.h'):
                    continue
                try:
                    s = open(os.path.join(tree, rel), errors='replace').read()
                except OSError:
                    continue
                if not any(mc in s for mc in macros):
                    continue
                for dm in drx.finditer(s):
                    for mc in macros:
                        if dm.group(1) != mc and re.search(r'\b%s\s*\(' % mc, dm.group(0)):
                            wrappers[mc].add(dm.group(1))
            macros = macros | {w for ws in wrappers.values() for w in ws}
        if macros:
            drx_any = re.compile(r'^[ \t]*#[ \t]*define(?:\\\n|[^\n])*', re.M)
            rx = re.compile(r'\b(%s)\s*\(' % '|'.join(map(re.escape, macros)))
            for rel in all_files:
                try:
                    s = open(os.path.join(tree, rel), errors='replace').read()
                except OSError:
                    continue
                if not rx.search(s):
                    continue
                spans = [(a.start(), a.end()) for a in drx_any.finditer(s)]
                for m in rx.finditer(s):
                    # 出現在 #define 本體（含接續行）裡的是「定義」不是「使用」
                    if not any(a <= m.start() < b for a, b in spans):
                        uses[m.group(1)] += 1
        for h in hits:
            if h['macro']:
                h['macro_uses'] = uses[h['macro']] + sum(
                    uses[w] for w in wrappers[h['macro']])
                h['macro_wrappers'] = sorted(wrappers[h['macro']])

    if args.json:
        json.dump(hits, sys.stdout, ensure_ascii=False, indent=1)
        print()
        return

    for h in sorted(hits, key=lambda h: (h['file'], h['assign_line'])):
        d = h['def']
        if d:
            defs = '%s:%d' % d
        elif h['value'] == 'NULL':
            defs = '(明確設成 NULL)'
        elif h['macro']:
            defs = '(巨集參數或 ## 拼接)'
        elif not h['func']:
            defs = '(運算式)'
        else:
            defs = '(定義沒找到)'
        tag = []
        if h['style'] != 'designated':
            tag.append(h['style'])
        if h['macro']:
            t = 'macro %s' % h['macro']
            if h.get('macro_wrappers'):
                t += ' (via %s)' % ', '.join(h['macro_wrappers'])
            if 'macro_uses' in h:
                t += ' x%d' % h['macro_uses']
            tag.append(t)
        if h['ifdef']:
            tag.append('#' + ' && '.join(h['ifdef']))
        print('%-32s -> %-40s  ops=%s%s @ %s:%d%s' % (
            h['value'][:32], defs, h['var'], '[]' if h['array'] else '',
            h['file'], h['assign_line'],
            ('  [' + '; '.join(tag) + ']') if tag else ''))
        print('%32s    kconfig: %s%s' % (
            '', ' <- '.join(h['kconfig']) or '-',
            ('   build: ' + h['build']) if 'build' in h else ''))

    funcs = defaultdict(set)
    for h in hits:
        if h['value'] != 'NULL':            # 明確設成 NULL 不算一個「實作」
            funcs[h['value']].add('%s:%s' % (h['file'], h['var']))
    shared = {k: v for k, v in funcs.items() if len(v) > 1}
    print('\n== %d 個指派，%d 個不同的實作，%d 個實作被多個 ops 共用' % (
        len(hits), len(funcs), len(shared)), file=sys.stderr)
    for k, v in sorted(shared.items(), key=lambda kv: -len(kv[1]))[:10]:
        print('   %-30s x%d' % (k, len(v)), file=sys.stderr)
    if args.build:
        nb = sum(1 for h in hits if h.get('build', '').startswith('built'))
        print('== 其中 %d 個指派所在的 .o 在 %s 裡真的有編出來' % (nb, args.build),
              file=sys.stderr)


if __name__ == '__main__':
    main()
```

**解答說明**

設計決策：

- **先「抹掉」再搜尋**：`strip_comments()` 把註解和字串換成等長空白。之後所有 regex、括號配對、深度計算都在抹過的文字上做，所以註解裡的 `.read = fake_in_comment` 不會被當真，字串裡的 `{` 也不會打亂配對；行號則用同一份文字算，和原檔一致。
- **深度而不是語法**：不做完整的 C 解析，只追蹤 `{}`、`()` 的深度。ops 初始化的欄位一定在 body 的第 0 層（陣列是第 1 層），這個不變式足以排除巢狀結構裡的同名欄位。
- **兩段式找定義**：kernel 的 callback 絕大多數是同檔案的 `static` 函式，先在同檔案找可以解決大部分；剩下的（`generic_file_read_iter`、`seq_read_iter` 這類共用實作）合成一條 regex 全 tree 掃一次。`.h` 只當備案，因為 `include/linux/debugfs.h:341` 就有一個 `#ifndef CONFIG_DEBUG_FS` 時的 `debugfs_attr_read` 空殼，真正的定義在 `fs/debugfs/file.c:514`。第一版的解答就在這裡錯了，回報了空殼。
- **Kconfig 推論只讀 Makefile**：不解析 Kconfig 檔本身，因為問題是「這個 `.o` 被什麼條件帶進來」，答案就在 Kbuild 的 `obj-$(CONFIG_X)` 語法裡（[Ch 36](./36-kconfig-kbuild-ifdef.md)）。
- **`--build` 是地面真相**：Makefile 推論會失敗（見下方 ramfs），但 `.o` 存不存在不會騙人。兩者並列，讓你看出推論的盲點。

**實際執行結果（v6.18）**

驗收基準，全 tree 掃 `read_iter`（第二次執行，檔案已在 page cache）：

```console
$ time python3 opsfind.py file_operations read_iter --build ~/code_reading/build > ri.txt 2> ri.err
real	0m5.983s
user	0m4.641s
sys	0m1.342s
$ cat ri.err
== 99 個指派，62 個不同的實作，14 個實作被多個 ops 共用
   generic_file_read_iter         x23
   seq_read_iter                  x4
   read_iter_zero                 x2
   tty_read                       x2
   v9fs_file_read_iter            x2
   gfs2_file_read_iter            x2
   nfs_file_read                  x2
   ntfs_file_read_iter            x2
   ocfs2_file_read_iter           x2
   proc_reg_read_iter             x2
== 其中 34 個指派所在的 .o 在 /home/ypp/code_reading/build 裡真的有編出來
```

（第一次執行要從磁碟讀整棵 tree，約 13 秒。）99 個指派、62 個不同的實作，但在我們的 defconfig build 裡只有 34 個指派所在的檔案真的被編譯。換句話說，追 `f_op->read_iter` 時，**有 65 個候選在這顆 kernel 裡根本不存在**，可以直接劃掉。

和 `rg` 的逐筆比對：

```python
import json, subprocess, os
h = json.load(open('ri.json'))          # opsfind.py ... --json > ri.json
mine = {(x['file'], x['assign_line']) for x in h}
out = subprocess.run(['rg', '-n', '--type', 'c', r'\.read_iter\s*=', '.'],
                     cwd=os.path.expanduser('~/code_reading/linux'),
                     capture_output=True, text=True).stdout
rg = set()
for l in out.splitlines():
    f, n, _ = l.split(':', 2)
    rg.add((f[2:], int(n)))
print('rg', len(rg), 'mine', len(mine))
print('rg-only', sorted(rg - mine)); print('mine-only', sorted(mine - rg))
```

```
rg 99 mine 99
rg-only []
mine-only []
```

輸出節錄，可以看到幾種典型的 Kconfig 鏈：

```
blkdev_read_iter                 -> block/fops.c:815                          ops=def_blk_fops @ block/fops.c:960
                                    kconfig: obj-y (block/fops.o) <- CONFIG_BLOCK (block/)   build: built
tty_read                         -> drivers/tty/tty_io.c:901                  ops=tty_fops @ drivers/tty/tty_io.c:460
                                    kconfig: CONFIG_TTY (drivers/tty/tty_io.o)   build: built
tty_read                         -> drivers/tty/tty_io.c:901                  ops=console_fops @ drivers/tty/tty_io.c:474
                                    kconfig: CONFIG_TTY (drivers/tty/tty_io.o)   build: built
ext4_file_read_iter              -> fs/ext4/file.c:130                        ops=ext4_file_operations @ fs/ext4/file.c:965
                                    kconfig: CONFIG_EXT4_FS (fs/ext4/ext4.o) <- CONFIG_EXT4_FS (fs/ext4/)   build: built
proc_sys_read                    -> fs/proc/proc_sysctl.c:619                 ops=proc_sys_file_operations @ fs/proc/proc_sysctl.c:860
                                    kconfig: CONFIG_PROC_SYSCTL (proc.o 的一部分) <- obj-y (fs/proc/proc.o) <- CONFIG_PROC_FS (fs/proc/)   build: built
shmem_file_read_iter             -> mm/shmem.c:3330                           ops=shmem_file_operations @ mm/shmem.c:5213  [#ifdef CONFIG_TMPFS]
                                    kconfig: obj-y (mm/shmem.o)   build: built
snd_pcm_readv                    -> sound/core/pcm_native.c:3568              ops=snd_pcm_f_ops[] @ sound/core/pcm_native.c:4203
                                    kconfig: CONFIG_SND_PCM (sound/core/snd-pcm.o) <- CONFIG_SND (sound/core/)   build: built
ffs_epfile_read_iter             -> drivers/usb/gadget/function/f_fs.c:1255   ops=ffs_epfile_operations @ drivers/usb/gadget/function/f_fs.c:1825
                                    kconfig: CONFIG_USB_F_FS (drivers/usb/gadget/function/usb_f_fs.o) <- CONFIG_USB_GADGET (drivers/usb/gadget/function/) <- CONFIG_USB_GADGET (drivers/usb/gadget/) <- CONFIG_USB_PHY || CONFIG_USB || CONFIG_USB_SUPPORT || CONFIG_PCI || CONFIG_USB_GADGET || CONFIG_OF (drivers/usb/)   build: not built
```

幾個值得注意的點：`tty_read` 被兩個 ops 共用（`tty_fops` 與 `console_fops`），這正是 Ch 51 實戰一看到的 `console_fops`；`shmem_file_operations` 的 `.read_iter` 在 `#ifdef CONFIG_TMPFS` 裡，真實 tree 裡的 `#ifdef` 測試；`snd_pcm_f_ops[]` 是真實的 ops 陣列；`drivers/usb/` 被 `drivers/Makefile` 的六行不同條件帶入，所以是 OR。

`write_iter`：

```
== 88 個指派，61 個不同的實作，10 個實作被多個 ops 共用
   generic_file_write_iter        x18
   netfs_file_write_iter          x3
   ...
== 其中 23 個指派所在的 .o 在 /home/ypp/code_reading/build 裡真的有編出來
```

巨集包裹的真實案例（`read` 欄位，`--macro-uses`，全 tree 約 60 秒，因為要找九百多個不同實作的定義）：

```
== 1468 個指派，940 個不同的實作，52 個實作被多個 ops 共用
   seq_read                       x333
   generic_read_dir               x72
   ...
seq_read -> fs/seq_file.c:151 ops=__name ## _fops @ include/linux/seq_file.h:216 [macro DEFINE_SHOW_ATTRIBUTE x711]
simple_attr_read -> fs/libfs.c:1326 ops=__fops @ include/linux/fs.h:3923 [macro DEFINE_SIMPLE_ATTRIBUTE_XSIGNED (via DEFINE_SIMPLE_ATTRIBUTE, DEFINE_SIMPLE_ATTRIBUTE_SIGNED) x71]
debugfs_attr_read -> fs/debugfs/file.c:514 ops=__fops @ include/linux/debugfs.h:58 [macro DEFINE_DEBUGFS_ATTRIBUTE_XSIGNED (via DEFINE_DEBUGFS_ATTRIBUTE, DEFINE_DEBUGFS_ATTRIBUTE_SIGNED) x341]
```

`DEFINE_SHOW_ATTRIBUTE` 在 tree 裡被展開了 711 次，每一次都產生一個 `.read = seq_read` 的 `file_operations`——這些在 `rg '\.read\s*='` 的結果裡**完全看不到**，因為原始碼裡只有 `DEFINE_SHOW_ATTRIBUTE(foo);` 一行。`DEFINE_SIMPLE_ATTRIBUTE` 則是兩層巨集，工具往上追了一層包裝巨集才數對。（`seq_read x333` 的 333 只算直接寫出來的指派；加上巨集展開，實際使用 `seq_read` 的 ops 超過一千個。）

fixture：

```
== 8 個指派，4 個不同的實作，2 個實作被多個 ops 共用
   core_read                      x3
   legacy_read                    x2
core_read                        -> drivers/demo/core.c:3                     ops=core_ops @ drivers/demo/core.c:12  [#ifdef CONFIG_DEMO_READ]
                                    kconfig: CONFIG_DEMO (drivers/demo/demo.o)
core_read                        -> drivers/demo/core.c:3                     ops=core_alt_ops @ drivers/demo/core.c:18
                                    kconfig: CONFIG_DEMO (drivers/demo/demo.o)
core_read                        -> drivers/demo/core.c:3                     ops=many_ops[] @ drivers/demo/core.c:23
                                    kconfig: CONFIG_DEMO (drivers/demo/demo.o)
NULL                             -> (明確設成 NULL)                               ops=many_ops[] @ drivers/demo/core.c:24
                                    kconfig: CONFIG_DEMO (drivers/demo/demo.o)
IS_ENABLED(CONFIG_X) ? legacy_re -> (運算式)                                     ops=extra_ops @ drivers/demo/extra.c:3
                                    kconfig: CONFIG_DEMO_EXTRA (drivers/demo/extra.o)
legacy_read                      -> drivers/demo/legacy.c:3                   ops=legacy_ops @ drivers/demo/legacy.c:7  [positional[1]]
                                    kconfig: CONFIG_DEMO (drivers/demo/demo.o)
legacy_read                      -> drivers/demo/legacy.c:3                   ops=gnu_ops @ drivers/demo/legacy.c:14  [gnu-colon]
                                    kconfig: CONFIG_DEMO (drivers/demo/demo.o)
__rd                             -> (巨集參數或 ## 拼接)                             ops=__name ## _ops @ include/linux/demo.h:9  [macro DEFINE_DEMO_OPS (via DEFINE_DEMO_RO) x2]
                                    kconfig: -
```

（這是加了 `--macro-uses` 的輸出；`x2` 是 `DEFINE_DEMO_RO(foo)`、`DEFINE_DEMO_RO(bar)` 兩次使用，透過包裝巨集算到 `DEFINE_DEMO_OPS` 頭上。摘要不把明確設成 `NULL` 算成一個實作，所以是 4 個不同的實作。）

**已知限制（誠實清單）**

1. **Makefile 變數間接**：`fs/ramfs/Makefile` 是 `file-mmu-$(CONFIG_MMU) := file-mmu.o` 加 `ramfs-objs += inode.o $(file-mmu-y)`。工具不展開 make 變數，所以：

   ```
   generic_file_read_iter           -> mm/filemap.c:2899                         ops=ramfs_file_operations @ fs/ramfs/file-mmu.c:42
                                       kconfig: ?（fs/ramfs/Makefile 找不到 file-mmu.o）   build: built
   generic_file_read_iter           -> mm/filemap.c:2899                         ops=ramfs_file_operations @ fs/ramfs/file-nommu.c:43
                                       kconfig: ?（fs/ramfs/Makefile 找不到 file-mmu.o）   build: not built
   ```

   Kconfig 推論失敗了，但 `--build` 欄正確分辨出 MMU 版本有編、nommu 版本沒編。這是「推論 + 地面真相」並列的價值。
2. **同名巨集**：iwlwifi 好幾個目錄各自定義了同名的 `DEBUGFS_READ_FILE_OPS`，`--macro-uses` 的計數會混在一起。
3. **只往上追一層包裝巨集**：三層以上的巨集鏈會少算。
4. **`.o` 存在 ≠ 在 vmlinux 裡**：`=m` 的 module 也會產生 `.o`。我們的 build 只有 9 個 module（`build/modules.order` 共 9 行），影響不大；嚴格判斷要再查 `modules.order` 或 `System.map`（工具對函式名查了 `System.map`，但只用來標示可能被內聯的情況）。
5. **不認得的寫法**：compound literal（`&(struct file_operations){ ... }`）、ops 嵌在外層 struct 的初始化裡（`.fops = { .read = x }`）、用 `memcpy`/逐欄位賦值在執行期填 ops 的程式碼（`fops->read_iter = x;`），都不會被找到。最後一種是**指派而非初始化**，要另外用 `rg '->read_iter\s*='` 補。
6. **定義搜尋靠排版慣例**：假設函式定義的名字所在行從第 0 欄開始（kernel coding style 如此），巨集產生的函式（`SHOW_FUNCTION(name)`）會回報「定義沒找到」。

</details>

## 延伸挑戰（加分）

- **換成真正的 parser**：用 tree-sitter 的 C grammar 或 libclang（`clang.cindex`）做同一件事。libclang 可以直接吃 `~/code_reading/build/compile_commands.json`，看到的是**前處理後**的 AST，巨集展開、`#ifdef` 都已經依 `.config` 處理掉，限制 1、2、3、5 一次解決；代價是只看得到這個 config 編得到的檔案，而且要先處理安裝（這個環境沒有 pip，要從原始碼或 wheel 手動裝到 `~/.local`）。比較兩種結果的差異，本身就是一份很好的「regex 方法漏了什麼」報告。
- **補上執行期指派**：加一個模式掃 `->FIELD\s*=` 與 `\.FIELD\s*=` 出現在函式本體裡（不是初始化）的情況，例如某些 driver 在 probe 時才填 ops。
- **反向查詢**：給一個函式名，列出它被放進哪些 ops 的哪些欄位（`anon_pipe_write` → `pipeanon_fops.write_iter`，以及 `fifo_pipe_write` 裡的直接呼叫）。這是 elixir/cscope 做得到、但輸出格式不適合腳本的那一半。
- **和動態證據接起來**：讀 [Ch 51](./51-dynamic-tracing.md) 的 function_graph 輸出，自動把 trace 裡出現的函式在 `opsfind.py` 的結果上標「這次執行有被呼叫」。靜態候選與動態命中的交集，就是你這次該讀的程式碼。
- **效能**：`read` 欄位要 60 秒，大部分花在全 tree 找九百多個定義。改用 `ctags`／`global` 的資料庫（[Ch 48](./48-elixir-cscope-gtags.md)）查定義，或把每個檔案的定義索引快取起來。

## 自我檢核

- [ ] 不看解答，說出「先抹掉註解和字串、再用深度判斷欄位層級」這個設計為什麼能同時解決三個邊界情況。
- [ ] 解釋為什麼 `rg '\.read\s*='` 找到的數量遠少於實際用到 `seq_read` 的 ops 數量，以及你的工具怎麼補上。
- [ ] 說出 Makefile 推論與 `--build` 檢查各自會在什麼情況出錯，為什麼兩者並列比只用一個好。
- [ ] 給你一個新的 ops 型別（例如 `struct inode_operations` 的 `lookup`），你能預測哪些限制會變得更明顯嗎？跑一次驗證你的預測。
- [ ] 能說出自己的實作和參考解答的差異，並解釋各自取捨。

Part 8 的工具都備齊了：搜尋、索引、編譯器、動態追蹤、debugger、LLM，以及你自己寫的工具。Part 9 把它們全部拿去冷讀真實的 kernel 程式碼。

→ [Ch 54 冷讀實戰一：隨機抓一個 kernel 函式](./54-cold-read-random-function.md)
