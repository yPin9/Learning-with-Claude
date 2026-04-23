# Ch 10 — BTF 與 CO-RE：跨 kernel 版本部署

> 目標：搞懂為什麼跨 kernel 版本是 BPF 部署的最大痛點、BTF 是什麼、CO-RE 的 relocation 怎麼讓一份 BPF binary 在不同 kernel 上跑得起來。讀完這章，Part 3 寫真正的 BPF 程式才有底氣。

## 殘酷的事實：kernel struct 每版都會動

你寫的 BPF 程式很可能要存取 kernel struct 欄位：

```c
struct task_struct *task = (struct task_struct *)bpf_get_current_task();
u32 pid = BPF_CORE_READ(task, pid);
```

問題是 — `task_struct` 這個 struct 在不同 kernel 版本**長得不一樣**：

```
kernel 5.4:                kernel 5.15:
struct task_struct {        struct task_struct {
    int             state;       struct thread_info  thread_info;
    void           *stack;       unsigned int        __state;
    refcount_t      usage;       void               *stack;
    unsigned int    flags;       refcount_t          usage;
    ...                          ...
    pid_t           pid;         pid_t               pid;        ← offset 不同！
    ...                          ...
};                          };
```

如果你在 5.4 上編譯出 BPF，硬編碼 `pid` 在 offset 0x478，搬到 5.15 跑 — 那個 offset 上是別的欄位。讀出來是垃圾值，更糟的是越界讀別的東西。

**這就是 BPF 部署最大的歷史痛點**：你不能「編一次到處跑」。

## 史前時代怎麼解 —— BCC 的方案

BCC（Ch 12 會詳細看）的解法是 **「在目標機器上編譯」**：

```python
from bcc import BPF
b = BPF(text="""
    int trace(struct pt_regs *ctx) {
        struct task_struct *t = (void *)bpf_get_current_task();
        bpf_trace_printk("pid=%d\\n", t->pid);
        return 0;
    }
""")
```

執行時 BCC 會：
1. 在目標機器上找 `linux-headers-$(uname -r)`
2. 把 C 程式碼丟給 clang
3. 編譯成 BPF object
4. 載入

**痛點**：

- **每台機器都要裝 clang + kernel headers**（幾百 MB）
- **啟動慢**（每次跑都重編，幾秒起跳）
- **編譯失敗看你機器運氣**（kernel headers 對不上、套件版本問題）
- **記憶體吃凶**（clang 在 kernel 緊張時很要命）

容器化時代這個方案痛苦到不行。**Cilium、Falco 都不能接受**。

## CO-RE 的核心想法

「Compile Once - Run Everywhere」的目標：**一份 BPF object 在所有 kernel 都能跑**。

關鍵 insight：**程式存取的不是「字面 offset」，而是「邏輯欄位」**。`task->pid` 這個語意不變 — 變的是它在記憶體裡的 offset。如果 BPF 在載入時能**自動把 offset 修正成當前 kernel 的值**，問題就解了。

兩件事讓這變成可能：

1. **BTF**（BPF Type Format） — kernel 在 runtime 提供「自己 struct 長什麼樣」的 metadata
2. **CO-RE relocation** — BPF object 帶著「我想讀某個 struct 的某個 field」的描述，載入時 libbpf 用 BTF 查當前 offset、把 BPF bytecode 裡的 offset patch 成正確的

```
編譯時（你的機器）                       運行時（目標機器）
                                  
my.bpf.c                              my.bpf.o + 當前 kernel 的 BTF
   │                                       │
   │ clang -g                              │ libbpf load
   ▼                                       ▼
my.bpf.o                              查 BTF：task_struct.pid
   │                                       的 offset = 0x4F0
   │ + CO-RE relocation                    │
   │   metadata                            │ 改寫 BPF instruction
   │                                       │   load offset = 0x4F0
   │                                       ▼
   ▼                                  載入 verifier → JIT → 跑
```

## BTF 是什麼？

BTF 是個**緊湊的 type metadata 格式**。可以想成「kernel struct/enum/func 的 dwarf 精簡版」：

- 每個 type 編號（type ID）
- 每個 struct 欄位的 name、type、offset、size
- 每個 function 的參數型別

kernel 編譯時開 `CONFIG_DEBUG_INFO_BTF=y`，就會把全 kernel struct 的 BTF 嵌進 kernel image，runtime 透過 `/sys/kernel/btf/vmlinux` 暴露：

```bash
ls -la /sys/kernel/btf/vmlinux
# -r--r--r-- 1 root root 5234567 ...

sudo bpftool btf dump file /sys/kernel/btf/vmlinux | head -20
```

每個 kernel module 也可以有自己的 BTF：

```bash
ls /sys/kernel/btf/
# vmlinux  i915  amdgpu  nf_conntrack  ...
```

BTF 跟 DWARF 很像但**為 BPF 場景優化**：去掉了 DWARF 80% 的功能、檔案小很多（5MB 等級而非幾百 MB）、解析快。

## vmlinux.h 怎麼來

寫 BPF C 時你 `#include "vmlinux.h"` 就能用 kernel 的所有 type。這個 header 不是手寫的 — 是從 BTF dump 出來的：

```bash
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h
wc -l vmlinux.h
# 大概 100,000+ 行
```

含有所有 struct / enum / typedef 定義。**這個 header 是「從你機器當前 BTF 生成」的** — 在你機器上 build 出的 .bpf.o 帶的 type 結構，反映的是你機器當下的 kernel。

但這不代表 .bpf.o 只能在你機器跑 — 因為 CO-RE 會在載入時做 relocation。

## CO-RE relocation —— 魔法的細節

當你寫：

```c
u32 pid = BPF_CORE_READ(task, pid);
```

`BPF_CORE_READ` 這個 macro **不會把 offset 寫死進 bytecode**。它會：

1. 編譯時：產生一條「讀 task_struct.pid 欄位」的**符號描述**，存進 .bpf.o 的 `.BTF.ext` section
2. 編譯時：在 bytecode 對應位置先放一個 placeholder offset
3. 載入時：libbpf 讀 `.BTF.ext` 拿到「我想讀 task_struct.pid」、查目標 kernel 的 BTF、找到當前 offset、**把 bytecode 那個 placeholder 改成真值**

整個過程對你透明。你寫 C，libbpf + verifier + kernel 一起把跨版本問題吞了。

```bash
# 看 .bpf.o 裡的 CO-RE relocation
sudo bpftool btf dump file my.bpf.o | grep -A 1 "00000.*FieldRel"
```

## CO-RE 的核心 macro

幾乎所有現代 libbpf BPF 都會用到：

| Macro | 做什麼 |
|---|---|
| `BPF_CORE_READ(ptr, a, b, c)` | 安全讀 `ptr->a.b.c`，自動處理 relocation + safe read |
| `BPF_CORE_READ_INTO(&dst, ptr, a, b)` | 同上但寫到 dst |
| `BPF_CORE_READ_STR_INTO(buf, ptr, name)` | 讀 string 欄位 |
| `bpf_core_field_exists(t->f)` | 編譯時檢查欄位存不存在（boolean） |
| `bpf_core_type_exists(struct_name)` | 同上但檢查整個 struct |
| `bpf_core_enum_value_exists(...)` | 檢查 enum 值 |

範例：跨版本 task state 欄位的 fallback

```c
// kernel 5.14 之前叫 ->state，5.14 後改名 ->__state
unsigned int task_state(struct task_struct *t) {
    if (bpf_core_field_exists(t->__state)) {
        return BPF_CORE_READ(t, __state);
    } else {
        return BPF_CORE_READ(t, state);
    }
}
```

這份 code 編一次，5.4 跟 5.15 都跑得起來 — `bpf_core_field_exists` 在 relocation 時被替換成 0 或 1，verifier 看到一條 dead branch 直接砍掉。

## 為什麼一定要用 BPF_CORE_READ 而不是直接 `->`？

```c
// 不行：直接解參考
u32 pid = task->pid;     // ← verifier 拒絕：task 是 kernel ptr，不能直接讀

// 不行：手動 bpf_probe_read 但寫死 offset
u32 pid;
bpf_probe_read_kernel(&pid, sizeof(pid), (void *)task + 0x4F0);   // ← offset 是 hardcode

// OK：CO-RE
u32 pid = BPF_CORE_READ(task, pid);
```

`BPF_CORE_READ` 內部展開成 `bpf_probe_read_kernel` + 自動 relocation 的 offset。**這就是現代 BPF 寫 kernel 存取的標準方式**。

## CO-RE 的限制

不是萬能。下面這些情況 CO-RE 救不了你：

1. **欄位語意改變**：name 跟 type 都一樣，但**含意**變了。例如 `task->mm` 在 kernel A 指 user mm、在 kernel B 改成 kthread 用的 mm — relocation 處理 offset，不處理語意。
2. **struct 整個被刪掉**：CO-RE 只能 relocate 存在的欄位。整個 struct 不在了就 GG，要寫 `bpf_core_type_exists` 加 fallback。
3. **取代成完全不同的 mechanism**：例如 `bpf_get_socket_cookie` 內部換實作 — relocation 沒辦法救你，要換 helper。
4. **目標 kernel 沒 BTF**：4.x 老 kernel 沒有 `/sys/kernel/btf/vmlinux`，CO-RE 失靈。可以用 BTFHub 的 [external BTF](https://github.com/aquasecurity/btfhub) 補救。

## CO-RE 工具鏈

寫 CO-RE BPF 程式的標準環境：

```
clang ≥ 12      ← 必須支援 -target bpf 與 BTF
libbpf ≥ 0.5    ← CO-RE 客戶端
bpftool ≥ 5.10  ← 生成 vmlinux.h、查 BTF
target kernel ≥ 5.5 with CONFIG_DEBUG_INFO_BTF=y
```

編譯典型指令：

```bash
clang -O2 -g -target bpf \
    -D__TARGET_ARCH_x86 \
    -I. \
    -c my.bpf.c -o my.bpf.o
```

`-g` **必加** — CO-RE relocation metadata 是放在 debug info section 裡的。少了 `-g` 編出來的 BPF object 不能 CO-RE。

## libbpf-bootstrap：學 CO-RE 最快的方式

[`libbpf/libbpf-bootstrap`](https://github.com/libbpf/libbpf-bootstrap) 是官方的 CO-RE BPF 範本集：

```bash
git clone --recurse-submodules https://github.com/libbpf/libbpf-bootstrap
cd libbpf-bootstrap/examples/c
make minimal     # 最小範例
sudo ./minimal
```

每個範例都是「kernel side .bpf.c + user side .c + skeleton 自動生成」的標準三件套。Ch 13–14 會直接拿這個當教材。

## 一個常見誤解

「我有 vmlinux.h 就能 CO-RE」 — **不全然**。

vmlinux.h 只給你**型別定義**，讓 BPF C 編得過。CO-RE 真正魔法在：

1. clang 編譯時加 CO-RE relocation metadata（要 `-g`）
2. libbpf 載入時讀目標 kernel BTF
3. libbpf 修正 bytecode 的 offset

少了任何一環都不算 CO-RE。最常見的 fail 是「忘記加 `-g`」 — 編出來的 .bpf.o 只能在編譯這台機器跑。

## 動手練習

1. **看你機器的 BTF 多大**：
   ```bash
   ls -lh /sys/kernel/btf/vmlinux
   sudo bpftool btf dump file /sys/kernel/btf/vmlinux | wc -l
   ```
2. **生成你的 vmlinux.h**：
   ```bash
   sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h
   wc -l vmlinux.h
   grep "struct task_struct {" vmlinux.h
   ```
3. **看一個 CO-RE relocation**：clone libbpf-bootstrap、make examples/c/minimal、跑：
   ```bash
   sudo bpftool btf dump file minimal.bpf.o | grep -i reloc
   ```
4. **故意忘記 `-g` 看會怎樣**：編個 BPF object 不加 `-g`，再 `bpftool btf dump file` — 看 relocation section 是不是空的。

## 自我檢核

- [ ] 我能解釋為什麼跨 kernel 版本是 BPF 部署的最大痛點
- [ ] 我能說出 BCC 跟 CO-RE 在這件事上的根本差別
- [ ] 我能描述 CO-RE relocation 在編譯期與載入期各做什麼
- [ ] 我能用 `BPF_CORE_READ` 跟 `bpf_core_field_exists` 寫出跨版本 fallback
- [ ] 我知道為什麼 `-g` 是必加的

**Part 2 完工**。你現在知道 BPF VM 長怎樣、能掛在哪、verifier 為什麼難搞、跨 kernel 怎麼解。下一個 Part 開始**真的寫 BPF**：先用最高階的 bpftrace、再下降到 bcc、最後到 libbpf + CO-RE 的生產級寫法。

→ [練習 A：用 bpftool 探索系統上的 BPF](./practice-a-bpftool-exploration.md)
