# 練習 A — 用 bpftool 探索系統上的 BPF

> 目標：把 Ch 0–10 的所有概念對到一台真實 Linux 機器上。**不寫一行 BPF**，純用 bpftool 觀察系統當下跑了什麼 BPF、它們是什麼 type、有哪些 map、bytecode 長怎樣。學完這個練習，BPF 不再是抽象概念。

## 任務規格

完成下面 7 道小任務，每題寫下你看到的 + 你的解讀。**重點不是答案，而是「親眼看見」**。

| 任務 | 主要工具 | 對應章節 |
|---|---|---|
| 1. 列出系統上所有 BPF program | `bpftool prog list` | Ch 7 |
| 2. 觀察一支 program 的 metadata | `bpftool prog show id <id>` | Ch 7 |
| 3. dump program 的 xlated bytecode | `bpftool prog dump xlated` | Ch 6 |
| 4. dump program 的 JIT 後 native code | `bpftool prog dump jited` | Ch 6 |
| 5. 列出所有 map、檢查內容 | `bpftool map list` / `dump` | Ch 8 |
| 6. 檢查當前 kernel 的 BTF | `bpftool btf dump file ...` | Ch 10 |
| 7. 觀察 program 與 map 的關聯 | `bpftool prog show --pretty` | Ch 7+8 |

## 開工前置

確認 bpftool 能跑、且系統上有 BPF 在跑：

```bash
sudo bpftool version
sudo bpftool prog list | wc -l
```

如果第二行回傳 0，你的系統沒 BPF 可看。**最簡單的觸發**是裝 `bpftrace` 然後跑個 one-liner：

```bash
sudo bpftrace -e 'kprobe:vfs_read { @reads[comm] = count(); }' &
sleep 2
sudo bpftool prog list | wc -l
# 應該 >= 1
```

把這個 bpftrace 留著背景跑，下面的觀察才有東西看。

## Task 1 — 列出系統 BPF 全景

```bash
sudo bpftool prog list
```

預期看到一堆 entry 像：

```
3: cgroup_skb  name sd_devices  tag 6deef7357e7b4530  gpl
        loaded_at 2026-04-23T08:15:42+0800  uid 0
        xlated 64B  jited 54B  memlock 4096B
4: cgroup_skb  name sd_devices  tag 6deef7357e7b4530  gpl
        loaded_at 2026-04-23T08:15:42+0800  uid 0
        xlated 64B  jited 54B  memlock 4096B
...
872: kprobe  name BEGIN_trigger  tag 4a72b8a9c8e9d2c0  gpl
        loaded_at 2026-04-23T10:23:12+0800  uid 0
        xlated 256B  jited 234B  memlock 4096B
        btf_id 1234
```

**回答**：

- 總共幾支 program？（`sudo bpftool prog list | grep -c ^[0-9]`）
- 出現幾種 program type？（`sudo bpftool prog list | awk '/^[0-9]+:/ {print $2}' | sort -u`）
- 哪幾支是你自己跑的 bpftrace 載入的？（提示：name 可能含 `BEGIN_` 或 `kprobe_`）
- `xlated` 與 `jited` 大小通常哪個比較大？（提示：x86_64 native code 通常比 BPF bytecode 略大或相當）

## Task 2 — 一支 program 的詳細 metadata

挑你 bpftrace 載的那支：

```bash
sudo bpftool prog show id <id> --pretty
```

JSON 輸出，會看到：

```json
{
    "id": 872,
    "type": "kprobe",
    "name": "BEGIN_trigger",
    "tag": "4a72b8a9c8e9d2c0",
    "gpl_compatible": true,
    "loaded_at": 1714452192,
    "uid": 0,
    "bytes_xlated": 256,
    "jited": true,
    "bytes_jited": 234,
    "bytes_memlock": 4096,
    "map_ids": [123, 124],
    ...
}
```

**回答**：

- 它的 program type 是什麼？對應到 Ch 7 哪一格？
- 用了哪些 map？記下 `map_ids` 清單（Task 5 會用到）
- `gpl_compatible` 是什麼？為什麼大多 BPF 程式宣告 GPL？

## Task 3 — 看 BPF bytecode

```bash
sudo bpftool prog dump xlated id <id>
```

輸出像：

```
   0: (b7) r1 = 0
   1: (7b) *(u64 *)(r10 -8) = r1
   2: (bf) r2 = r10
   3: (07) r2 += -8
   4: (18) r1 = map[id:123]
   6: (85) call bpf_map_lookup_elem#1
   7: (15) if r0 == 0x0 goto pc+5
   8: (61) r1 = *(u32 *)(r0 +0)
   ...
```

**回答**：

- 認得 `r0`–`r10` 這些 register 嗎？對應 Ch 6 哪一段？
- 找一個 `call bpf_map_xxx#NN` — 那個 NN 是 helper id。對照 `man 7 bpf-helpers` 找到對應 helper 名字。
- `*(u64 *)(r10 -8)` 是什麼意思？（提示：Ch 6 frame pointer + 負偏移）

## Task 4 — JIT 後的 native code

```bash
sudo bpftool prog dump jited id <id>
```

x86_64 機器會看到 AT&T 語法的 asm：

```
   0:   nop
   5:   xchg ax,ax
   7:   push   %rbp
   8:   mov    %rsp,%rbp
   b:   sub    $0x10,%rsp
  ...
  19:   callq  bpf_map_lookup_elem
  1e:   test   %rax,%rax
  21:   je     0x32
  ...
```

**回答**：

- 比對 Task 3 的 xlated 與這份 jited，找一條對應的（例如 `r1 = 0` ↔ `xor edi, edi`）。**親眼看到 1:1 翻譯**。
- function 開頭的 `push %rbp; mov %rsp,%rbp; sub $X,%rsp` — 這是什麼 prologue？

## Task 5 — 看 maps

```bash
sudo bpftool map list
```

挑 Task 2 看到的 map id 之一：

```bash
sudo bpftool map show id <map_id> --pretty
sudo bpftool map dump id <map_id>
```

**回答**：

- 這個 map 是哪種 type（HASH / ARRAY / RINGBUF / ...）？對應到 Ch 8 哪一格？
- key/value size 各多少？max_entries 多少？
- dump 出來幾個 entry？bpftrace 還在跑的話應該每隔幾秒就會變多。

## Task 6 — 看 kernel BTF

```bash
ls -la /sys/kernel/btf/vmlinux
sudo bpftool btf dump file /sys/kernel/btf/vmlinux | head -30
sudo bpftool btf dump file /sys/kernel/btf/vmlinux | wc -l
```

**回答**：

- 你的 vmlinux BTF 多大？多少行 dump 出來？
- 找一個 struct（例如 `task_struct`）：
  ```bash
  sudo bpftool btf dump file /sys/kernel/btf/vmlinux | grep -A 40 "STRUCT 'task_struct'" | head -40
  ```
  數一下這個 struct 有幾個欄位。
- 列出有幾個 module 有自己的 BTF：
  ```bash
  ls /sys/kernel/btf/ | wc -l
  ```

## Task 7 — program 與 map 的關聯

最後一張總圖：用 graph 模式看 program 與 map 的關係：

```bash
sudo bpftool prog list --pretty | head -50
```

挑一支 program，記下它的 `map_ids: [X, Y, Z]`。對每個 map id 跑：

```bash
sudo bpftool map show id <X> --pretty
```

**回答**：

- 把這支 program + 它用到的 map 在腦中畫一張圖。
- 為什麼有的 program 沒 map？（提示：bpftrace 簡單 one-liner 可能不需要狀態）
- 為什麼有的 program 用同一個 map？（提示：兩支可能是 entry / exit pair）

## 收尾

```bash
# 把背景跑的 bpftrace 結束
kill %1
```

## 進階探索（選做）

8. **觀察 attach 點**：`sudo bpftool perf list` 列出當前所有 BPF perf event attachment。
9. **看 link**（5.7+ 的 attach API）：`sudo bpftool link list`。
10. **跨 program 比較大小**：找一支 cilium 或 systemd 的大型 program（`xlated` 數 KB），dump 出來感受「真實生產 BPF 多複雜」。

## 自我檢核

- [ ] 我能用 bpftool 列出系統上所有 BPF program、知道每支是什麼 type
- [ ] 我能 dump xlated bytecode 並認出 register、helper call、frame pointer 操作
- [ ] 我能 dump jited code 並對應回 xlated
- [ ] 我能找到一支 program 用了哪些 map、map 內容是啥
- [ ] 我能解釋為什麼 vmlinux 的 BTF 是 CO-RE 的根

完成這個練習，你已經把前 10 章的概念全部「對到實物」了。下一章開始正式寫 BPF — 從最高階、最像 awk 的 bpftrace 入手。

→ [Ch 11 bpftrace：一行解決問題的高階語言](./11-bpftrace.md)
