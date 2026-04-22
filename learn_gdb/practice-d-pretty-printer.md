# 練習 D — pretty printer + 自動化 workflow

> 目標：為一個具備多種資料結構的 C 專案，寫完整的 pretty printer 模組 + 一個自動化 debug command，把「每次 crash 我都要重打 10 行」變成 `(gdb) crash-report`。

## 背景

有一份小型「職員管理」程式 `emp.c`，裡面有三種 data structure：linked list、hash map、tag set。你的工作：

1. 為這三種結構寫 pretty printer
2. 寫一個 `emp-dump` 命令一次 dump 全部狀態
3. 寫一個 `crash-report` 命令：自動印 bt、locals、所有 thread 狀態、所有 employee data 到一個 log 檔
4. 打包成 python module，配 `~/.gdbinit` 自動載入

## 題目：`emp.c`

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HASH_CAP 16
#define MAX_TAGS 8

typedef struct TagSet {
    char *tags[MAX_TAGS];
    int count;
} TagSet;

typedef struct Employee {
    int id;
    char name[32];
    int salary;
    TagSet *tags;
    struct Employee *next;        // linked list of all employees
} Employee;

typedef struct HashEntry {
    int key;
    Employee *value;
    struct HashEntry *next;
} HashEntry;

typedef struct HashMap {
    HashEntry *buckets[HASH_CAP];
    int size;
} HashMap;

static Employee *all_employees = NULL;     // head of linked list
static HashMap emp_by_id = {{0}, 0};        // hash map for fast lookup

static unsigned hash(int key) {
    return (unsigned)key % HASH_CAP;
}

TagSet *new_tagset(void) {
    TagSet *t = calloc(1, sizeof(TagSet));
    return t;
}

void tagset_add(TagSet *t, const char *tag) {
    if (t->count >= MAX_TAGS) return;
    t->tags[t->count++] = strdup(tag);
}

Employee *new_employee(int id, const char *name, int salary) {
    Employee *e = calloc(1, sizeof(Employee));
    e->id = id;
    strncpy(e->name, name, sizeof(e->name) - 1);
    e->salary = salary;
    e->tags = new_tagset();

    // insert into linked list
    e->next = all_employees;
    all_employees = e;

    // insert into hashmap
    unsigned h = hash(id);
    HashEntry *he = calloc(1, sizeof(HashEntry));
    he->key = id;
    he->value = e;
    he->next = emp_by_id.buckets[h];
    emp_by_id.buckets[h] = he;
    emp_by_id.size++;

    return e;
}

Employee *find_employee(int id) {
    unsigned h = hash(id);
    for (HashEntry *he = emp_by_id.buckets[h]; he != NULL; he = he->next) {
        if (he->key == id) return he->value;
    }
    return NULL;
}

void crash_me(void) {
    // 故意的 NULL deref
    Employee *ghost = find_employee(9999);
    printf("salary: %d\n", ghost->salary);
}

int main(void) {
    Employee *alice = new_employee(1, "Alice", 80000);
    tagset_add(alice->tags, "engineer");
    tagset_add(alice->tags, "senior");

    Employee *bob = new_employee(2, "Bob", 60000);
    tagset_add(bob->tags, "junior");

    Employee *carol = new_employee(3, "Carol", 95000);
    tagset_add(carol->tags, "manager");
    tagset_add(carol->tags, "engineer");

    crash_me();         // NULL deref
    return 0;
}
```

編譯：

```bash
gcc -g -O0 emp.c -o emp
```

跑：

```bash
./emp
Segmentation fault (core dumped)
```

## 任務 1：寫三個 pretty printer

目標輸出：

```
(gdb) p *alice
$1 = Employee(id=1, name="Alice", salary=80000, tags=TagSet({"engineer", "senior"}))

(gdb) p all_employees
$2 = EmployeeList of length 3 = {
  [0] = Employee(id=3, name="Carol", ...),
  [1] = Employee(id=2, name="Bob", ...),
  [2] = Employee(id=1, name="Alice", ...)
}

(gdb) p emp_by_id
$3 = HashMap(size=3) = {
  [1] = <Employee 0x... (Alice)>,
  [2] = <Employee 0x... (Bob)>,
  [3] = <Employee 0x... (Carol)>
}
```

### 骨架：`emp_printers.py`

```python
import gdb
import gdb.printing


class TagSetPrinter:
    def __init__(self, val):
        self.val = val

    def to_string(self):
        count = int(self.val['count'])
        tags = []
        for i in range(count):
            t = self.val['tags'][i]
            if int(t) == 0:
                tags.append('<NULL>')
            else:
                tags.append(t.string())
        return f"TagSet({{{', '.join(repr(t) for t in tags)}}})"


class EmployeePrinter:
    def __init__(self, val):
        self.val = val

    def to_string(self):
        id_ = int(self.val['id'])
        name = self.val['name'].string()
        salary = int(self.val['salary'])
        tags = self.val['tags']
        if int(tags) == 0:
            tags_str = "<no tags>"
        else:
            tags_str = TagSetPrinter(tags.dereference()).to_string()
        return f"Employee(id={id_}, name={name!r}, salary={salary}, tags={tags_str})"


class EmployeeListPrinter:
    """Handles an Employee* interpreted as a linked list via `next`."""
    def __init__(self, val):
        self.val = val

    def _count(self):
        n = 0
        node = self.val
        seen = set()
        while int(node) != 0:
            addr = int(node)
            if addr in seen:
                return -1            # cycle
            seen.add(addr)
            n += 1
            node = node['next']
            if n > 100000:
                return -1            # sanity
        return n

    def to_string(self):
        n = self._count()
        if n < 0:
            return "EmployeeList(<cycle or too long>)"
        return f"EmployeeList of length {n}"

    def children(self):
        node = self.val
        i = 0
        while int(node) != 0 and i < 1000:
            yield (f"[{i}]", node.dereference())
            node = node['next']
            i += 1

    def display_hint(self):
        return "array"


class HashMapPrinter:
    def __init__(self, val):
        self.val = val

    def to_string(self):
        n = int(self.val['size'])
        return f"HashMap(size={n})"

    def children(self):
        buckets = self.val['buckets']
        cap = buckets.type.range()[1] + 1
        for i in range(cap):
            entry = buckets[i]
            while int(entry) != 0:
                key = int(entry['key'])
                emp = entry['value']
                yield (f"[{key}]", emp)
                entry = entry['next']

    def display_hint(self):
        return "map"


def build_pp():
    pp = gdb.printing.RegexpCollectionPrettyPrinter("emp")
    pp.add_printer("TagSet",    r"^TagSet$",     TagSetPrinter)
    pp.add_printer("Employee",  r"^Employee$",   EmployeePrinter)
    pp.add_printer("EmpList",   r"^Employee \*$", EmployeeListPrinter)
    pp.add_printer("HashMap",   r"^HashMap$",    HashMapPrinter)
    return pp

gdb.printing.register_pretty_printer(None, build_pp())
```

**注意**：`r"^Employee \*$"` 裡 regex 要 match `Employee *`（指標型別）— 你印的是 `Employee *` 當 linked list head，不是 struct 本身。

## 任務 2：`emp-dump` command

一次 dump 所有 state：

```python
class EmpDump(gdb.Command):
    """Dump all employee state."""

    def __init__(self):
        super().__init__("emp-dump", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        try:
            list_val = gdb.parse_and_eval("all_employees")
            map_val = gdb.parse_and_eval("emp_by_id")
        except gdb.error as e:
            gdb.write(f"error: {e}\n")
            return

        gdb.write("=== Employee linked list ===\n")
        gdb.write(str(list_val) + "\n\n")
        gdb.write("=== HashMap ===\n")
        gdb.write(str(map_val) + "\n")


EmpDump()
```

使用：

```
(gdb) emp-dump
=== Employee linked list ===
EmployeeList of length 3 = {
  [0] = Employee(id=3, name='Carol', salary=95000, tags=TagSet({'manager', 'engineer'})),
  ...
}

=== HashMap ===
HashMap(size=3) = {
  [1] = 0x... "Alice",
  [2] = 0x... "Bob",
  [3] = 0x... "Carol"
}
```

## 任務 3：`crash-report` command

一個「當我 attach 到 crash，打一個指令就把所有現場資訊存到檔案」的工具：

```python
import datetime
import os

class CrashReport(gdb.Command):
    """Generate a crash report to a file. Usage: crash-report [filename]"""

    def __init__(self):
        super().__init__("crash-report", gdb.COMMAND_USER)

    def invoke(self, arg, from_tty):
        filename = arg.strip()
        if not filename:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"/tmp/crash-report-{ts}.txt"

        with open(filename, "w") as f:
            def w(s=""):
                f.write(s + "\n")

            w("=" * 60)
            w(f"Crash report generated at {datetime.datetime.now().isoformat()}")
            w("=" * 60)

            # basic info
            w()
            w("--- Program info ---")
            w(gdb.execute("info program", to_string=True))

            # signal
            try:
                sig = gdb.parse_and_eval("$_siginfo")
                w(f"Signal info: {sig}")
            except gdb.error:
                pass

            # bt of current thread
            w()
            w("--- Backtrace (current thread) ---")
            w(gdb.execute("bt full", to_string=True))

            # all threads
            w()
            w("--- All threads ---")
            w(gdb.execute("thread apply all bt", to_string=True))

            # locals
            w()
            w("--- Locals (current frame) ---")
            try:
                w(gdb.execute("info locals", to_string=True))
            except gdb.error:
                w("(no frame / optimized out)")

            # registers
            w()
            w("--- Registers ---")
            w(gdb.execute("info registers", to_string=True))

            # custom dumps
            w()
            w("--- Employee state ---")
            try:
                w(gdb.execute("emp-dump", to_string=True))
            except gdb.error as e:
                w(f"emp-dump failed: {e}")

        gdb.write(f"crash report saved to {filename}\n")


CrashReport()
```

使用：

```
(gdb) run
...crash...
(gdb) crash-report
crash report saved to /tmp/crash-report-20260423-030000.txt

(gdb) shell cat /tmp/crash-report-20260423-030000.txt
```

傳這份檔案給同事，他不用 attach 也看得到完整現場。

## 任務 4：打包 + auto-load

**檔案結構：**

```
emp/
  emp.c
  gdb/
    __init__.py        ← 空檔或模組入口
    printers.py        ← 上面的 pretty printer code
    commands.py        ← emp-dump, crash-report
```

**`~/.gdbinit`** 或 project `.gdbinit`：

```gdb
python
import sys
sys.path.insert(0, "/path/to/emp/gdb")
import printers
import commands
end
```

`source` 那串可以放到專案 `.gdbinit`，git 管理，所有人共享。

## 驗收

跑完整個流程：

```bash
cd emp/
gdb -q ./emp

(gdb) run
... crash in crash_me ...

(gdb) p *alice
$1 = Employee(id=1, name='Alice', salary=80000, tags=TagSet({'engineer', 'senior'}))

(gdb) p all_employees
$2 = EmployeeList of length 3 = { ... }

(gdb) emp-dump
=== Employee linked list ===
...

(gdb) crash-report
crash report saved to /tmp/crash-report-...

(gdb) shell head -40 /tmp/crash-report-*.txt
```

每個動作都要順利。

## 挑戰（選做）

1. 為 `HashEntry` 的 bucket collision chain 畫出視覺化（例如用 ascii art 印）。
2. 加一個 frame filter，把 `new_tagset` / `new_employee` / `calloc` 這類 initialization frame 從 bt 隱藏（只在主要 business logic frame）。
3. 為 `emp-dump` 加 JSON 輸出格式，方便丟到其他 tool。
4. 處理 edge case：list 有 cycle、tags 指標壞掉、name 字串沒 null-terminator — 你的 printer 不能 crash。
5. 用 `gdb.events.stop.connect(...)` 做一個 event hook：程式一 crash 就自動 `crash-report`，user 完全不用打指令。

## 自我檢核

- [ ] 我能為多種 data structure 寫 pretty printer（struct / linked list / hashmap / set）
- [ ] 我的 printer 會處理 NULL、cycle 等 edge case 不 crash
- [ ] 我能寫一個 custom command，整合多種現有 command 輸出
- [ ] 我能把這些工具打包成 Python module，透過 `.gdbinit` 自動載入
- [ ] 我理解「把日常 debug 動作腳本化」是 senior 工程師的 debug workflow

Part 5 結束。接下來 Part 6 是這整個課程的思想核心 — GDB 的**內部原理**。我們要下到 ptrace、DWARF、breakpoint patch、ASLR、unwinding 這些層次，理解之前所有章節背後的機制。

→ [Ch 17 ptrace 系統呼叫](./17-ptrace-internals.md)
