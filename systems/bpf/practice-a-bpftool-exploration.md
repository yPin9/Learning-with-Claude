# 練習 A — bpftool 全面探索

> **目標**：把 Ch 0–5 學到的東西——BPF program types、maps、verifier、BTF——用 bpftool 對正在執行的 kernel 一一驗證，建立「kernel 裡面目前有什麼 BPF 程式在跑」的直覺。

## 背景與動機

你已經能用 clang 編譯 BPF 程式、能讀 verifier log、知道 ISA 長什麼樣。但在你動手寫複雜的 BPF 程式之前，先把你的 kernel 當作一個黑盒子打開來看——系統裡已經有哪些 BPF 程式在跑？它們是什麼型別？有什麼 maps？這個練習讓你建立「BPF 生態在我的機器上的全圖」。

bpftool 是這個練習的主角。你不需要在這個練習裡寫 BPF 程式；你只需要用 bpftool 查詢和觀察。

## 任務規格

這個練習沒有「輸入/輸出」——你在探索，目標是回答後面的問題。每個問題都有對應的 bpftool 指令提示（但先試著自己找）。

**系統需求**：
- Ubuntu 22.04 或 kernel 5.15+
- 已安裝 bpftool（`sudo apt install linux-tools-$(uname -r)`）
- sudo 權限（bpftool 大部分功能需要 root）

## 如果你卡住了

1. `sudo bpftool help` 和 `sudo bpftool prog help` 是你的第一個求助對象
2. 如果某個功能說 "not supported"，先查 `sudo bpftool feature probe` 確認 kernel 版本是否支援
3. 每個 bpftool subcommand 都有 `--json` 和 `--pretty` 選項，輸出更容易 parse
4. `sudo bpftool prog dump xlated` 和 `sudo bpftool prog dump jited` 需要 `CONFIG_IKHEADERS=y` 或已載入 kheaders module

## 任務一：查看所有已載入的 BPF Programs

```bash
sudo bpftool prog list
```

對每一個 loaded program，找出：
- **id**：program 的 kernel id
- **type**：program 類型（kprobe / tracepoint / xdp / cgroup_skb / ...）
- **name**：程式名稱（通常是 C 函式名）
- **tag**：程式的 hash fingerprint（即使重新載入，相同的 code tag 相同）
- **gpl_compatible**：是否宣告 GPL（影響能用的 helper）
- **loaded_at**：何時載入
- **run_cnt**：執行了多少次

**問題 1-1**：你的系統上有幾個 loaded BPF programs？哪個 program 的 `run_cnt` 最高？這個 program 是做什麼的？

**問題 1-2**：找出所有 `type = cgroup_skb` 的 programs，說出它們的用途（提示：`run_cnt` 每次有封包就會增加）

**問題 1-3**：執行 `systemctl status --no-pager`，然後再執行 `sudo bpftool prog list`，新出現了什麼 program？

## 任務二：Dump 一個 Program 的指令

選一個 loaded program（用任意一個你看到的 id），執行：

```bash
# 用 id 找程式
sudo bpftool prog dump xlated id <id>

# 如果想要帶 source 標注（需要 BTF）
sudo bpftool prog dump xlated id <id> linum
```

**問題 2-1**：dump 出來的是 BPF bytecode 還是 x86-64 native code？這是什麼格式？

**問題 2-2**：找出 `call` 指令（opcode 結尾是 `call`），說出它呼叫的 helper 名稱

**問題 2-3**（進階）：執行 `sudo bpftool prog dump jited id <id>`，對比 xlated 和 jited 的輸出——哪些 BPF 指令對應到哪些 x86 指令？（提示：看 comment 標注的 BPF 指令號）

## 任務三：查看所有 BPF Maps

```bash
sudo bpftool map list
```

**問題 3-1**：你的系統上有哪幾種 map type（`BPF_MAP_TYPE_*`）？各有幾個？

**問題 3-2**：找一個 `type = ringbuf` 的 map（如果有的話）和 `type = hash` 的 map，說出它們的 `key_size` 和 `value_size` 是多少

**問題 3-3**：執行以下指令，看一個 map 的內容：

```bash
# 列出 map 的所有 key-value pair
sudo bpftool map dump id <map-id>

# 如果 map 太大，只看一個 key
sudo bpftool map lookup id <map-id> key <key-bytes-in-hex>
# 例如：sudo bpftool map lookup id 5 key 00 00 00 00
```

找到一個有內容的 map，說出它存的是什麼資料（根據 map type 和 key/value size 猜測）。

## 任務四：BTF 型別資訊

```bash
# 查看 kernel 的 BTF（來自 /sys/kernel/btf/vmlinux）
sudo bpftool btf show

# Dump 特定 BTF 的型別資訊
sudo bpftool btf dump id <btf-id>

# 查看 kernel BTF 裡的 task_struct 定義
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format raw | grep -A 5 'task_struct'
```

**問題 4-1**：執行 `sudo bpftool btf show`，找出 `vmlinux` 的 BTF id 是多少

**問題 4-2**：執行 `sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c | grep -c "typedef"`，你的 kernel BTF 有多少個 typedef？

**問題 4-3**：執行 `sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c | grep "struct task_struct" | head -3`，找到 `struct task_struct` 的定義開頭；說出你的 kernel 的 `task_struct` 有哪幾個前面的欄位

## 任務五：feature probe

```bash
sudo bpftool feature probe
```

**問題 5-1**：`BPF_MAP_TYPE_RINGBUF` 有沒有支援？你的 kernel 版本是多少？

**問題 5-2**：`bpf_ringbuf_output` helper 有沒有支援？這個 helper 和 `bpf_perf_event_output` 的差別是什麼？（提示：查 Ch 25）

**問題 5-3**：找出你的 kernel **不支援**的最新功能（按 feature probe 輸出，找到第一個 "is NOT available" 的項目），猜測它需要的最低 kernel 版本

## 任務六：pin 和 unpin

BPF 的 pin 機制讓 BPF object 在沒有 userspace 持有 fd 的情況下繼續存在：

```bash
# 建立一個簡單的 BPF program 並 pin 它
# 先用 Ch 0 的 hello.bpf.c（或任何你已有的 .bpf.o）

sudo bpftool prog load hello.bpf.o /sys/fs/bpf/my_test_prog
# 查看 pin 在 BPF filesystem 的物件
sudo ls /sys/fs/bpf/
sudo bpftool prog show pinned /sys/fs/bpf/my_test_prog

# 取消 pin（刪除檔案）
sudo rm /sys/fs/bpf/my_test_prog

# 確認 program 消失了（如果沒有其他 fd 持有它）
sudo bpftool prog list | grep my_test_prog
```

**問題 6-1**：pin 之後，程式的 id 會改變嗎？同一個 loaded program 可以有幾個 pin 路徑？

**問題 6-2**：刪除 pin 之後，program 立刻消失了嗎？如果沒有，為什麼？

## 實作步驟建議

### Step 1：安裝確認
確認 `sudo bpftool version` 成功，記下版本號

### Step 2：系統 BPF 全覽
跑 `sudo bpftool prog list` 和 `sudo bpftool map list`，把輸出存到文字檔，方便後面查閱

### Step 3：深挖一個 Program
選一個 run_cnt 最高的 program，dump xlated 和 jited，嘗試理解它在做什麼

### Step 4：BTF 探索
用 `bpftool btf dump` 找到 `struct sk_buff` 的定義，數數看它有多少個欄位

### Step 5：回答所有問題
把每個問題的答案寫下來（文字或 markdown），確認自己能解釋每個輸出的意義

## 完整參考解答

**先做完再看！**

<details>
<summary>點開參考解答</summary>

**任務一解說**：
在 Ubuntu 22.04 desktop 上，通常會看到 5–15 個 loaded programs，包括 systemd 的 cgroup_skb filter 和 networkd 的 socket filter。run_cnt 最高的通常是 cgroup_skb 的 ingress/egress filter（每個封包都觸發）。

```bash
# 用 json 輸出更容易 parse
sudo bpftool prog list --json | python3 -m json.tool | head -50
```

**任務二解說**：
`dump xlated` 輸出的是 BPF 指令（不是 x86），以人類可讀格式顯示。`call` 指令後面跟的數字是 helper id，可以在 `include/uapi/linux/bpf.h` 裡查 `enum bpf_func_id`。

`dump jited` 的輸出是 x86-64 native code（已經過 JIT 翻譯）。

**任務三解說**：
```bash
# 查看特定 array map 的第一個 entry
sudo bpftool map dump id <id>
# 輸出格式：
# key: 00 00 00 00    value: 00 00 00 00 00 00 00 00
```

**任務四解說**：
vmlinux BTF 通常是 id 1（是第一個被載入的 BTF 物件）。typedef 數量通常在 2000–5000 之間，取決於 kernel 版本。

**任務五解說**：
RINGBUF 在 kernel 5.8+ 支援。`bpf_ringbuf_output` 相比 `bpf_perf_event_output` 的優點是不需要 per-CPU buffer，效能更好，也不會 drop event（如果 consumer 跟上）。

**任務六解說**：
Pin 之後 id 不變。同一個 program 可以有多個 pin 路徑（多個 symlink 指向同一個 program）。刪除 pin 之後，只要還有其他持有者（fd），program 不會立刻消失；當最後一個引用消失時才被清理（BPF object 的生命週期是 reference counted）。

</details>

## 測試用案例

這個練習沒有固定的 input/output，但這些是驗收標準：

| 驗收項目 | 如何確認 |
|---|---|
| 能列出系統所有 loaded BPF programs | `bpftool prog list` 有輸出，理解每個欄位 |
| 能 dump 一個 program 的 BPF 指令 | `bpftool prog dump xlated` 有輸出，能解讀 |
| 能找到 maps 的 key/value 內容 | `bpftool map dump` 有資料 |
| 能查 BTF 型別資訊 | 找到 `struct task_struct` 的欄位 |
| 能用 `feature probe` 找出 kernel 支援的功能 | 至少說出 3 個「supported/not supported」的功能 |

## 延伸挑戰（加分）

- **挑戰一**：找出哪個 BPF program 佔用最多 JIT 後的 native code bytes（提示：`bpftool prog dump jited id <id> | wc -l`）

- **挑戰二**：找出系統上所有 pinned BPF objects：`sudo find /sys/fs/bpf -type f`，說出每個 pin 對應的 program 或 map 的 id

- **挑戰三**：用 bpftool 生成一個 BPF skeleton（`bpftool gen skeleton hello.bpf.o`），看看生成的 `.skel.h` 長什麼樣

- **挑戰四**：嘗試修改一個 map 的內容（`bpftool map update`），觀察 BPF program 的行為是否改變

## 自我檢核

- [ ] 能解釋 `bpftool prog list` 輸出的每個欄位的意義
- [ ] 能讀懂 `bpftool prog dump xlated` 的輸出，識別 load/store/call 指令
- [ ] 知道 BPF program 的生命週期（reference counted），以及 pin 的作用
- [ ] 能用 `bpftool feature probe` 判斷 kernel 是否支援某個功能

→ [Ch 6 Program Types 完整解析](./06-program-types.md)
