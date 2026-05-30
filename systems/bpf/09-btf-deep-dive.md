# Ch 9 — BTF：BPF Type Format 深入

> **目標**：理解 BTF（BPF Type Format）是什麼、它在 ELF 物件和 kernel 裡的 encoding 方式、為什麼它是 CO-RE 的基礎、以及如何用 bpftool 查看和解讀 BTF 資訊。

## 為什麼需要這個？

在 BTF 出現之前（kernel 4.18 之前），eBPF 有一個根本問題：你的 BPF 程式裡的型別資訊（"這個 pointer 指向 `struct task_struct`"）在編譯成 bytecode 之後就消失了。

這導致兩個連鎖問題：

**問題一：Verifier 無法驗證 struct 存取**。Verifier 只知道「這是個 pointer」，不知道它指向哪種 struct，因此無法驗證 `task->comm` 的 offset 是否在有效範圍內。

**問題二：跨 kernel 版本不相容**。Kernel 升級後，某個 struct 的 field offset 可能改變（例如 `task_struct.comm` 從 offset 592 變成 600）。你的 BPF 程式 hardcode 了 offset，升級 kernel 後讀到錯誤的值。

BTF 解決了這兩個問題：**把型別資訊帶進 kernel，讓 kernel 在 load-time 知道你的 BPF 程式在操作哪些 type**。

## 先建立直覺：BTF 是什麼？

BTF 是一個 compact 的 type description format，類似 DWARF（debug info format）但更精簡：

```
DWARF（ELF debug info，完整）
  - 幾 MB 大小
  - 包含行號、變數名、所有 debug info
  - 為 debugger（GDB）設計

BTF（BPF Type Format，精簡）
  - 幾 KB 到幾 MB（vmlinux BTF 約 5 MB）
  - 只包含型別資訊（struct/union/enum/typedef/func/var）
  - 為 BPF verifier 和 CO-RE 設計
  - 可以在 kernel runtime 查詢
```

BTF 有兩個用途：

1. **程式 BTF（`.BTF` ELF section）**：你的 `.bpf.o` 裡的型別資訊，讓 verifier 知道你操作了哪些 struct field
2. **Kernel BTF（`/sys/kernel/btf/vmlinux`）**：kernel 在 build time 生成，包含 kernel 所有 struct 的型別和 field offset；CO-RE 用它做 runtime relocation

## BTF 的資料格式

BTF 由三個部分組成：

```
BTF blob（位於 .BTF ELF section 或 /sys/kernel/btf/vmlinux）

┌──────────────────┐
│   BTF header     │  magic, version, hdr_len, type_off, type_len, str_off, str_len
├──────────────────┤
│   Type section   │  一系列 btf_type 結構（每個描述一個型別）
├──────────────────┤
│   String section │  所有型別名稱的 string pool
└──────────────────┘
```

每個 `struct btf_type` 的 encoding：

```c
/* include/uapi/linux/btf.h */
struct btf_type {
    __u32 name_off;    /* type 名稱在 string section 的 offset */
    __u32 info;        /* kind（16-18 bits）+ vlen（0-15 bits）+ flags */
    union {
        __u32 size;    /* 對 int/struct/union/enum：size in bytes */
        __u32 type;    /* 對 pointer/typedef/array/...：target type id */
    };
    /* 緊跟著 kind-specific 的額外資料 */
};
```

**Kind 值（型別的種類）**：

| Kind | 值 | 意義 |
|---|---|---|
| `BTF_KIND_INT` | 1 | 整數（u8/u16/u32/u64/bool） |
| `BTF_KIND_PTR` | 2 | Pointer |
| `BTF_KIND_ARRAY` | 3 | Array |
| `BTF_KIND_STRUCT` | 4 | Struct |
| `BTF_KIND_UNION` | 5 | Union |
| `BTF_KIND_ENUM` | 6 | Enum |
| `BTF_KIND_FWD` | 7 | Forward declaration |
| `BTF_KIND_TYPEDEF` | 8 | Typedef |
| `BTF_KIND_VOLATILE` | 9 | Volatile qualifier |
| `BTF_KIND_CONST` | 10 | Const qualifier |
| `BTF_KIND_RESTRICT` | 11 | Restrict qualifier |
| `BTF_KIND_FUNC` | 12 | Function |
| `BTF_KIND_FUNC_PROTO` | 13 | Function prototype |
| `BTF_KIND_VAR` | 14 | Variable |
| `BTF_KIND_DATASEC` | 15 | Data section（maps、vars）|
| `BTF_KIND_FLOAT` | 16 | Float（kernel 5.13+）|
| `BTF_KIND_DECL_TAG` | 17 | Declaration tag |
| `BTF_KIND_TYPE_TAG` | 18 | Type tag |
| `BTF_KIND_ENUM64` | 19 | 64-bit enum（kernel 6.0+）|

## 用 bpftool 查看 BTF

```bash
# 查看你的 .bpf.o 裡的 BTF
bpftool btf dump file simple_count.bpf.o

# 輸出（human-readable format）：
# [1] INT __u8 size=1 bits_offset=0 nr_bits=8 encoding=UNSIGNED
# [2] INT __u16 size=2 bits_offset=0 nr_bits=16 encoding=UNSIGNED
# ...
# [10] STRUCT event size=24 vlen=3
#   pid type_id=4 bits_offset=0
#   comm type_id=8 bits_offset=32
#   timestamp type_id=3 bits_offset=160

# 用 C 格式輸出（生成 header file）
bpftool btf dump file simple_count.bpf.o format c
```

```bash
# 查看 kernel 的 BTF
sudo bpftool btf dump id 1  # id 1 通常是 vmlinux

# 搜尋特定型別
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format raw | \
    grep -A 20 "struct task_struct"

# 更好的方式：dump 成 C format 再搜尋
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c | \
    grep -A 5 "struct task_struct {"
```

## Kernel BTF（vmlinux）

Kernel 在 build time 用 `pahole` 工具從 DWARF 生成 BTF，存放在 kernel image 裡，runtime 暴露在 `/sys/kernel/btf/vmlinux`。

```bash
ls -la /sys/kernel/btf/vmlinux
# -r--r--r-- 1 root root 5132341  /sys/kernel/btf/vmlinux
# 約 5 MB

# 查看包含多少個型別
sudo bpftool btf dump file /sys/kernel/btf/vmlinux | wc -l
# 通常幾萬行
```

kernel BTF 包含 kernel 的所有 public struct、union、enum、typedef，包括：
- `struct task_struct`、`struct file`、`struct inode`
- `struct sk_buff`、`struct net_device`、`struct sock`
- `struct pt_regs`、`struct bpf_context`
- 所有 syscall 的 tracepoint 型別

## Module BTF（kernel 5.11+）

Kernel module 有自己的 BTF，放在 `/sys/kernel/btf/<module_name>`：

```bash
ls /sys/kernel/btf/
# vmlinux
# btrfs
# nf_tables
# ...（根據 loaded modules 不同）
```

Module BTF 和 vmlinux BTF 是分開的，但可以用 split BTF 機制互相引用。

## 程式 BTF：`.BTF` 和 `.BTF.ext` ELF section

當你用 `clang -g -target bpf` 編譯 BPF 程式時，clang 會生成兩個 BTF 相關的 ELF section：

**`.BTF`**：型別資訊（struct、func、var 的定義）

**`.BTF.ext`**：擴充資訊：
- **line info**：每條 BPF 指令對應到 source 的哪一行
- **func info**：每個 BPF function 的 BTF type id
- **core_relos**：CO-RE relocation entries（告訴 kernel 哪些 field access 需要 relocation）

```bash
# 查看 .bpf.o 的 ELF section
llvm-objdump -h simple_count.bpf.o
# 輸出包含：
# .BTF         0x00003FD0  ...
# .BTF.ext     0x00000...  ...
```

`.BTF.ext` 裡的 CO-RE relocation 是 CO-RE 的核心，在 [Ch 10](./10-co-re.md) 詳細說明。

## BTF 型別 ID 和 引用關係

BTF 的 type IDs 從 1 開始遞增（0 保留為 void）。Pointer、typedef、array 透過 type ID 引用它們指向的型別：

```
假設一個 BPF 程式裡有：

struct event {
    u32 pid;      // type_id = 4（BTF_KIND_INT, u32）
    char comm[16]; // type_id = 8（BTF_KIND_ARRAY）
    u64 ts;       // type_id = 3（BTF_KIND_INT, u64）
};

BTF type 鏈：
  STRUCT "event"（type_id = 10）
    ├── member "pid" → type_id = 4（INT, u32）
    ├── member "comm" → type_id = 8（ARRAY）
    │         ├── nelems = 16
    │         └── element type → type_id = 1（INT, char）
    └── member "ts" → type_id = 3（INT, u64）
```

## BTF 和 Verifier 的關係

當你 load 一個 BPF 程式時，kernel 用程式的 BTF 做兩件事：

1. **Struct access validation**：`task->comm` 的 offset 是多少？是否在 struct 大小內？
2. **Type compatibility check（CO-RE）**：程式編譯時的 `task_struct` 和目前 kernel 的 `task_struct` 是否相容？如果 field offset 不同，需要 relocate。

沒有 BTF 的 BPF 程式仍然可以工作（退化成直接 offset 存取），但不支援 CO-RE，也讓 verifier 的型別追蹤更弱。

## 踩雷集錦

1. **沒有 `-g` flag 就沒有 BTF**：忘記加 `-g` 的話，`.bpf.o` 裡沒有 `.BTF` section，CO-RE relocation 無法工作；verifier log 也不會有 source line 對應

2. **vmlinux.h 的型別和 kernel 的 BTF 不一致**：vmlinux.h 是在某個 kernel 上生成的；如果你在不同 kernel 版本上執行，CO-RE 負責修正 field offset，但如果 struct 被完全移除了，CO-RE 也無法幫你

3. **bpftool btf dump 的 raw format 和 c format 不同**：`format raw` 輸出的是 BTF 的低層編碼（type ID、kind）；`format c` 生成的是 C header 格式。查 field offset 用 raw，要生成 vmlinux.h 用 c

4. **Module BTF 需要對應 module 被載入**：如果你要用某個 kernel module 的 struct，那個 module 必須已被載入，否則 `/sys/kernel/btf/<module>` 不存在

5. **BTF ID 在 kernel 重開後改變**：BTF object 的 id（例如 vmlinux 的 id = 1）不保證在 reboot 後相同；永遠用 path（`/sys/kernel/btf/vmlinux`）而不是 id 來引用

## 進階：生成自訂 BTF

在某些特殊情況下（例如為 cross-compiled 的 kernel 生成 BTF），你需要手動生成 BTF：

```bash
# 用 pahole 從 vmlinux ELF（帶 DWARF）生成 BTF
pahole --btf_encode vmlinux
# 生成的 BTF 塞進 vmlinux 的 .BTF section

# 用 bpftool 從 BTF 生成 C header
bpftool btf dump file vmlinux format c > vmlinux.h
```

## 動手練習

1. 編譯任意 BPF 程式（帶 `-g`），執行 `bpftool btf dump file prog.bpf.o`，找到你定義的 struct 的 BTF 表示，確認每個 member 的 `bits_offset` 和你預期的一致

2. 執行 `sudo bpftool btf dump file /sys/kernel/btf/vmlinux format raw | grep "STRUCT" | wc -l`，你的 kernel BTF 裡有多少個 struct？

3. 在 vmlinux.h 裡搜尋 `struct sk_buff`，找到 `data`、`head`、`tail` 三個 field，說出它們的 offset（位元組）

4. 故意編譯一個沒有 `-g` flag 的 BPF 程式，確認 `bpftool btf dump` 顯示 "No BTF found"，然後嘗試 CO-RE relocation（`BPF_CORE_READ`），確認編譯失敗

## 本章重點整理

- BTF 是精簡的型別描述格式，讓 BPF verifier 在 runtime 知道你的程式在操作哪些 struct field
- Kernel BTF（`/sys/kernel/btf/vmlinux`）包含 kernel 所有 struct 的型別和 field offset；是 CO-RE 的基礎
- `.BTF` ELF section 包含程式的型別；`.BTF.ext` 包含行號 info 和 CO-RE relocation entries
- 沒有 BTF 就沒有 CO-RE；編譯時必須加 `-g` flag

## 自我檢核

- [ ] 能解釋 BTF 和 DWARF 的相同點和不同點
- [ ] 知道 `.BTF` 和 `.BTF.ext` 分別包含什麼，以及 CO-RE 用的是哪個
- [ ] 能用 bpftool 找出 `struct task_struct` 的 `comm` field 在 kernel BTF 裡的 offset
- [ ] 知道為什麼 `-g` flag 在 BPF 編譯裡是必要的（不只是 debug info）

## 延伸閱讀

### 官方文件

- **[Linux kernel: BPF Type Format](https://www.kernel.org/doc/html/latest/bpf/btf.html)**
  - **讀哪裡**：整份；特別是 BTF type encoding 那一節
  - **學什麼**：BTF 的完整 spec；每個 kind 的 encoding 格式

### 部落格

- **[BPF CO-RE reference guide](https://nakryiko.com/posts/bpf-core-reference-guide/)** — Andrii Nakryiko
  - **這篇說什麼**：CO-RE 的完整指南，BTF 是其基礎；Section 1-3 解釋了 BTF 在 CO-RE 裡的角色
  - **讀哪裡**：前三節（BTF、CO-RE overview、BTF relocation）
  - **為什麼值得讀**：作者是 libbpf 和 CO-RE 的設計者；這是最準確的技術描述

→ [Ch 10 CO-RE：Compile Once Run Everywhere](./10-co-re.md)
