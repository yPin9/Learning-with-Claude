# Ch 5 — classic BPF：為什麼會發明一個 in-kernel VM

> 目標：回到 1992 年的 BSD packet filter，徹底拆解 cBPF 的 VM 架構、指令集、與「為什麼這樣設計」。理解 cBPF 的精神，後面 eBPF 的所有改動都只是這個故事的續集。

## 1992 年的問題

當時要做網路封包擷取（`tcpdump` 那種事），標準做法是：

```
NIC ──→ kernel buffer ──→ copy 到 user space ──→ user 程式 if/else 過濾
                                ▲
                                │
                          99% 是要丟掉的
```

你只想要 `port 80` 的 packet，但 kernel 把每個 packet 都複製給你 — 因為 kernel 不知道你要什麼。複製的成本（user/kernel 跨界 + memcpy）遠遠大於後續的判斷。在 10 Mbps 的時代就已經很痛，1992 年正在往 100 Mbps 走，這條路撐不下去。

**「能不能讓 user 把過濾邏輯下放到 kernel」** — 這就是 McCanne 和 Jacobson 那篇論文的核心問題。

## 兩個顯然但都不行的方案

**方案 A：讓 user 直接送 native code 進 kernel 跑**。
不行 — buggy 的 code 會 crash kernel、惡意 code 會 root 整台機器。

**方案 B：給 user 一個固定的 if/else table，讓他填欄位**。
不行 — 過濾規則千變萬化（IP、port、TCP flags、payload pattern...），固定 table 表達不出來。

**方案 C（他們的選擇）：設計一個極小的 VM，user 送這個 VM 的 bytecode 進來，kernel 解釋執行**。VM 的指令集**刻意設計成「不可能寫出危險程式」**：

- 沒有任意記憶體存取（只能讀 packet 與固定 scratch 區）
- 沒有 backward jump（保證會結束）
- 程式長度有上限

這是 BPF 的第一性原理：**安全性建在語言層，而不是建在權限管理上**。

## cBPF VM 的硬體模型

整個 VM 簡單到一頁紙能畫完：

```
┌─────────────────────────────────────────┐
│            cBPF Virtual Machine         │
│                                         │
│   ┌────────────┐    ┌────────────┐      │
│   │ Register A │    │ Register X │      │
│   │ (32-bit)   │    │ (32-bit)   │      │
│   └────────────┘    └────────────┘      │
│                                         │
│   ┌──────────────────────────────────┐  │
│   │   Scratch memory (16 words)      │  │
│   └──────────────────────────────────┘  │
│                                         │
│   ┌──────────────────────────────────┐  │
│   │   Packet (read-only)             │  │
│   └──────────────────────────────────┘  │
│                                         │
│   PC ── 指向當前指令                     │
│                                         │
└─────────────────────────────────────────┘
```

就這樣。**2 個 32-bit register、16 word scratch、唯讀的 packet buffer、一個 PC**。沒有 stack、沒有 heap、沒有 syscall、沒有函式呼叫。

## 指令格式

每條 cBPF 指令固定 64 bit：

```
┌──────────────┬────────┬────────┬──────────────────┐
│  opcode (16) │ jt (8) │ jf (8) │       k (32)     │
└──────────────┴────────┴────────┴──────────────────┘
       │           │        │              │
       │           │        │              └─ immediate value（常數）
       │           │        └─ jump if false 偏移
       │           └─ jump if true 偏移
       └─ 操作類型
```

`jt` / `jf` 只有 jump 類指令會用。對非 jump 指令這兩欄忽略。

## 指令集（不到 30 條）

分六大類：

| 類型 | 例子 | 做什麼 |
|---|---|---|
| **LD** | `ld [12]` | 把 packet 第 12 byte 載入 A |
| **LDX** | `ldx 4` | 把常數 4 載入 X |
| **ST** | `st M[3]` | A 存到 scratch[3] |
| **ALU** | `add x` | A = A + X |
| **JMP** | `jeq #0x800, jt, jf` | A == 0x800 嗎？跳 jt or jf |
| **RET** | `ret #65535` | 結束，回傳 65535（=「整個 packet 都收」） |

**關鍵限制**：所有 jump 的 `jt` / `jf` 都是 **正數偏移** — 只能往前跳。這就保證了**沒有 loop、程式一定會結束**。

## 來看一個真的 cBPF 程式

`tcpdump -d` 會把 expression 編譯成 cBPF 並 disassemble 出來：

```bash
sudo tcpdump -d 'tcp and port 80'
```

輸出（簡化版，行號是 `(NNN)`）：

```
(000) ldh      [12]                     ; A = packet[12..13] (EtherType)
(001) jeq      #0x800           jt 2  jf 12   ; 是 IPv4 嗎？
(002) ldb      [23]                     ; A = packet[23] (IP protocol)
(003) jeq      #0x6             jt 4  jf 12   ; 是 TCP 嗎？(0x6)
(004) ldh      [20]                     ; A = packet[20..21] (IP frag offset)
(005) jset     #0x1fff          jt 12 jf 6    ; 是 fragment 嗎？跳掉
(006) ldxb     4*([14]&0xf)             ; X = IP header length
(007) ldh      [x + 14]                 ; A = src port
(008) jeq      #0x50            jt 11 jf 9    ; src port == 80?
(009) ldh      [x + 16]                 ; A = dst port
(010) jeq      #0x50            jt 11 jf 12   ; dst port == 80?
(011) ret      #65535                   ; YES — 整個 packet 都收
(012) ret      #0                       ; NO  — 丟掉
```

讀法：

1. 從 (000) 開始：把 packet offset 12-13 的 2 byte 讀到 A — 那是 Ethernet frame 的 EtherType 欄位
2. (001) 比較 A 是不是 0x800（IPv4）— 是的話跳到 (002)，否則跳到 (012) 直接丟掉
3. (002) 讀 IP protocol 欄位
4. (003) 是 TCP（0x6）嗎
5. ...
6. 最後不是 (011) `ret #65535`（保留），就是 (012) `ret #0`（丟）

**整個過程沒有 backward jump、沒有 loop、最大 13 步就結束**。kernel 對每個進來的 packet 跑這個 program，不過關的早早 ret #0 拋掉，省下複製到 user space 的成本。

## Verifier（古早版）

cBPF 載入時 kernel 也會檢查：

- 所有 jump target 都在程式範圍內
- 所有 jump 都是 forward
- 程式有 ret 收尾
- 不會讀 packet 越界（runtime 檢查 + 部分 static 檢查）

但相比現代 eBPF verifier，這個檢查是**表面**的 — 因為 cBPF 指令集本身就限制得夠死，verifier 不用做太複雜的分析。

## cBPF 今天還在哪用？

很多人以為 cBPF 已死 — 不是。它今天還在這幾個地方用得很活：

### 1. socket filter

`tcpdump` 仍然送 cBPF 進 kernel：

```bash
sudo tcpdump -d 'host 1.2.3.4'   # 看編譯結果
sudo tcpdump 'host 1.2.3.4'      # 實際跑就是這個
```

### 2. seccomp-bpf

容器、Chrome sandbox、Docker default profile，全部用 cBPF 做 syscall 過濾：

```c
// 簡化版 seccomp filter（過濾 write syscall）
struct sock_filter filter[] = {
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_write, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL),
    BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
};
```

每行對應一條 cBPF 指令。Ch 22 會詳細展開。

### 3. 在 kernel 內部「翻譯成 eBPF」

實作上，現代 Linux kernel 收到 cBPF 程式後，會**自動把它轉成 eBPF** 再跑（`bpf_convert_filter()`）。所以即使你寫 cBPF，跑的時候也是 eBPF — 但 ABI 與工具仍然以 cBPF 介面對外。

這個「向上相容 + 內部統一」的設計很漂亮 — 老程式不用改，新引擎吃得下。

## cBPF 的限制（為什麼要 eBPF）

cBPF 對 packet filter 來說剛剛好，但放到別的場景就立刻穿幫：

| cBPF 不行的事 | 為什麼需要 |
|---|---|
| 跨呼叫保留狀態 | observability 要做「平均延遲」「總數」需要累加 |
| 呼叫 kernel 函式 | 想拿 process info、改 packet field 都要 helper |
| 寫 kernel memory | 改 packet field、給 sockmap 加 entry |
| > 4096 bytes scratch | 複雜邏輯放不下 |
| 任意 data structure | 沒 hash table，沒 list，做不了複雜 lookup |
| Backward jump / loop | 處理 string、解析 protocol header chain |

每一條都是 eBPF 改進的方向。下一章我們進到現代 eBPF 的 ISA、看 Starovoitov 怎麼把這個 1992 的 VM 重造成一台「能寫真程式」的 VM。

## 動手練習

1. **編譯幾個 tcpdump expression 看看**：
   ```bash
   sudo tcpdump -d 'icmp'
   sudo tcpdump -d 'src host 8.8.8.8'
   sudo tcpdump -d 'tcp and (dst port 80 or dst port 443)'
   ```
   逐行讀懂，特別注意「複合條件」是怎麼用 `jt` / `jf` 串接的。
2. **看 RAW bytecode**：`sudo tcpdump -dd 'tcp port 80'` — 這次輸出的是 C struct array，那才是真正塞進 kernel 的東西。
3. **故意寫一個會被 verifier 拒絕的 cBPF**：跑 `sudo tcpdump -d 'foo bar baz'` — 看 tcpdump 怎麼罵你（這是 expression 編譯失敗，但概念類似）。
4. **找 seccomp 範例**：`grep -r BPF_STMT /usr/src/linux*` 或讀 [systemd 的 seccomp setup](https://github.com/systemd/systemd/blob/main/src/shared/seccomp-util.c)。

## 自我檢核

- [ ] 我能畫出 cBPF VM 的硬體模型（2 register + scratch + packet）
- [ ] 我能解釋為什麼「只能 forward jump」就保證了 termination
- [ ] 我能讀懂 `tcpdump -d` 的輸出
- [ ] 我能說出 cBPF 今天還活在哪三個地方
- [ ] 我能列出 cBPF 至少三個對 modern observability 不夠的限制

下一章我們進 eBPF 的 ISA — 看 11 個 64-bit register、512 byte stack、helper function 機制怎麼把 cBPF 升級成一台能跑真程式的 VM。

→ [Ch 6 eBPF instruction set、register、JIT 與 sandboxing](./06-ebpf-isa-and-jit.md)
