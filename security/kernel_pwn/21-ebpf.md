# Ch 21 — eBPF：verifier bypass 與 map 型漏洞

> 目標：eBPF 讓 unprivileged 用戶在 kernel 跑 code（有 verifier 守著），但 verifier 本身有過錯 — 讓你繞過邊界檢查、讓 JIT 生出錯誤指令。這章講 verifier 的心智模型與歷年經典 bypass 模式。

## eBPF 的攻擊面在哪

eBPF 的設計目標：讓 user-space 程式安全地在 kernel 裡執行 bytecode（不需要寫 kernel module）。保障安全性的是 **verifier**：在 JIT 之前靜態分析整個 program，確保它不會越界讀寫記憶體、不會死循環、不會用不安全的指令。

**攻擊面**：verifier 的靜態分析本身有 bug → 讓不安全的程式通過 verification → JIT 把它編譯執行 → kernel 以 ring 0 執行你的「惡意」指令。

---

## verifier 的心智模型：register tracking

verifier 用**抽象解釋**（abstract interpretation）追蹤每個暫存器（`r0`-`r10`）的**可能值範圍**。每個暫存器在任何時刻有一個 `bpf_reg_state`：

```c
struct bpf_reg_state {
    enum bpf_reg_type type;  /* PTR_TO_MAP_VALUE, SCALAR_VALUE, PTR_TO_STACK, ... */
    struct tnum var_off;     /* 值的 known bits（tnum = trivium，64-bit known/uncertain bitvector） */
    s64 smin_value;          /* signed min */
    s64 smax_value;          /* signed max */
    u64 umin_value;          /* unsigned min */
    u64 umax_value;          /* unsigned max */
    /* ... */
};
```

key insight：**verifier 只追蹤型別和值範圍，不追蹤確切值**。

```
r1 = map_lookup_elem(...)       → PTR_TO_MAP_VALUE or NULL
if (r1 == NULL) goto out        → 過了這行，verifier 知道 r1 != NULL → PTR_TO_MAP_VALUE
r2 = r1 + 4                    → PTR_TO_MAP_VALUE + offset 4
r3 = *(u32 *)(r2 + 0)          → SCALAR_VALUE（可能是任何值）
if (r3 > 7) goto out            → 過了這行，verifier 知道 r3 ∈ [0,7]
r4 = r1 + r3                   → PTR_TO_MAP_VALUE + [0,7]，在 map value 範圍內 → OK
*(u64 *)(r4 + 0) = 0            → verifier 批准
```

如果 verifier 在某一步對 r3 的範圍估計有誤（例如它認為 r3 ∈ [0,7] 但實際上可以更大），你就能寫到 map value 邊界外。

---

## 三類經典 verifier bug

### Bug 類型 A：ALU32 / 64-bit 範圍不一致（CVE-2021-3490）

**背景**：eBPF 暫存器是 64-bit，但有些指令只操作低 32-bit（ALU32 instructions，`dst32 op src32`）。verifier 要同時追蹤 64-bit 和 32-bit 的範圍。

**Bug**：某些 ALU32 操作後，verifier 更新了 32-bit 範圍，但 **64-bit 範圍沒有同步更新**（或反過來）。如果 verifier 之後拿 64-bit 範圍做邊界決策，但你實際上透過 ALU32 操作構造了一個超出 64-bit 範圍的值，驗證就失效了。

**利用**：
```bpf
; r1 = map_value pointer
; r2 = user-controlled value (verifier thinks [0, 0x7fffffff])
r2 = (u32)r2       ; ALU32 zero-extend → verifier 64-bit range: [0, 0x7fffffff]
r2 += 0x80000000   ; verifier: [0x80000000, 0xffffffff] → still < 0x100000000
; 但如果 ALU32/64 sync bug 讓 64-bit range 維持錯誤值
; 後面的 bounds check 被欺騙
r3 = *(u64 *)(r1 + r2)  ; OOB read，verifier 認為 in-bounds
```

### Bug 類型 B：scalar pointer 混淆（CVE-2022-23222）

**背景**：verifier 區分 `SCALAR_VALUE`（普通整數）和各種 pointer type。pointer 不能被洩漏到 user，也不能做任意算術。

**Bug**：某些操作後 verifier 把 pointer 當成 scalar，或把 scalar 當成 pointer，導致：
- **pointer leak**：scalar 實際上是 kernel address，被複製到 user-readable map → KASLR bypass
- **pointer arithmetic bypass**：scalar 被當 pointer 用，可以做 unchecked 的 kernel address 算術

### Bug 類型 C：map value 越界（最常見）

BPF map 是 kernel 中的一塊記憶體，user 用 `bpf_map_lookup_elem()` 拿到 pointer，可以讀寫這塊記憶體。verifier 要確保讀寫不超出 `map->value_size`。

範圍 check 的 off-by-one 或 signed/unsigned 混用就會讓你 OOB read/write map 之後的記憶體。

---

## 利用 eBPF 的 OOB 做提權

一旦有 OOB write in BPF map，標準路徑：

```
OOB write beyond map value
  → 覆寫相鄰的 map value 的 metadata（map pointer / ops）
  → 或直接覆寫到 kernel 物件（如果 map 附近有 slab 物件）
  → 任意讀寫 primitive（透過 map 操作）
  → 改 modprobe_path 或 cred（data-only）
```

更直接的路：用 OOB write 構造一個「fake map」，讓 kernel 以為這個 map 的 value_size 是 0xffffffff，之後對這個 fake map 的任何 read/write 都是任意地址讀寫。

```c
/* 概念：構造 fake map 拿到任意 R/W */
/* 1. 建兩個 map A 和 B，讓它們在 kernel 中相鄰 */
int map_a = bpf_create_map(BPF_MAP_TYPE_ARRAY, 4, 256, 1, 0);
int map_b = bpf_create_map(BPF_MAP_TYPE_ARRAY, 4, 256, 1, 0);

/* 2. 透過 OOB write（verifier bypass），從 map_a 的末端寫到 map_b 的 map->data.value_size */
/* 3. 修改 map_b 的 value_size → 0xffffffff */
/* 4. 之後 bpf_map_update_elem(map_b, 0, arbitrary_addr, ...) 就能寫任意地址 */
```

---

## unprivileged BPF：歷史變化

| 版本 | unprivileged BPF 狀態 |
|---|---|
| < 5.9 | 允許（`sysctl kernel.unprivileged_bpf_disabled=0`） |
| 5.9+ | 預設關閉 unprivileged BPF |
| kernelCTF LTS | 通常關閉，需要 user namespace 的 CAP_BPF |
| kernelCTF COS | 關閉 |

**現狀**：kernelCTF 的 BPF 題多數需要 user namespace（`CLONE_NEWUSER`），在 ns 內有 `CAP_BPF`（或 `CAP_SYS_ADMIN`）。或者題目本身有 `setuid` binary 允許你拿到 BPF 能力。

---

## 快速寫一個 BPF program（minimal skeleton）

```c
#include <linux/bpf.h>
#include <sys/syscall.h>
#include <stdint.h>
#include <stdio.h>

static int bpf_prog_load(const struct bpf_insn *insns, int insn_cnt) {
    union bpf_attr attr = {
        .prog_type = BPF_PROG_TYPE_SOCKET_FILTER,
        .insns     = (uintptr_t)insns,
        .insn_cnt  = insn_cnt,
        .license   = (uintptr_t)"GPL",
        .log_level = 1,
        /* log_buf / log_size 設好可以看 verifier 輸出 */
    };
    return syscall(__NR_bpf, BPF_PROG_LOAD, &attr, sizeof(attr));
}

/* 最小的合法 program：return 0 */
static struct bpf_insn prog[] = {
    { .code = BPF_ALU64 | BPF_MOV | BPF_K, .dst_reg = BPF_REG_0, .imm = 0 },
    { .code = BPF_JMP | BPF_EXIT },
};

int main(void) {
    int fd = bpf_prog_load(prog, 2);
    printf("prog fd = %d\n", fd);
    return 0;
}
```

加 `log_buf` / `log_size` 讓 verifier 輸出 register state，是理解 verifier 邏輯的最快方法。

---

## 動手練習

1. **看 verifier log**：寫一個簡單的 map lookup + value read BPF program，設 `log_level=2`，把 verifier 輸出存下來。找每條指令後的 register state，確認 `smin_value`/`smax_value` 如何更新。
2. **觸發 verifier reject**：寫一個沒有 NULL check 就 dereference map pointer 的 program，確認 verifier 拒絕並說明原因。
3. **探索 tnum**：讀 `kernel/bpf/tnum.c`，找 `tnum_range`、`tnum_add`、`tnum_and`，理解 known bits 和 unknown bits 的運算方式。
4. **讀 CVE-2021-3490 PoC**（公開）：找 writeup（e.g., Marek Majkowski），追蹤哪條 ALU32 指令讓 64-bit 和 32-bit 的 range 不同步。
5. **造一個 fake OOB 場景**：建兩個 adjacent array map，用 GDB 確認它們在 kernel 中的位置，計算從 map_a 的 value 末端到 map_b 的 metadata 的 offset（這是 OOB exploit 的第一步）。

## 自我檢核

- [ ] 能解釋 verifier 的抽象解釋機制（register state: type, tnum, smin/smax, umin/umax）
- [ ] 知道 ALU32/64 範圍不一致 bug 的根本原因（32-bit op 後 64-bit range 沒更新）
- [ ] 知道 scalar/pointer 混淆讓 verifier 失效的兩個效果（pointer leak、unchecked arithmetic）
- [ ] 能描述 OOB map write → fake map → 任意 R/W 的三步路徑
- [ ] 知道 kernelCTF 環境下 unprivileged BPF 的狀態（通常關閉，需要 CAP_BPF via ns）
- [ ] 能手寫最小 BPF prog skeleton 並取得 verifier log

→ [Ch 22 — User namespace + ksmbd + 其他子系統速覽](./22-other-subsystems.md)
