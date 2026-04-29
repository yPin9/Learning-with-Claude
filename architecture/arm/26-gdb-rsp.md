# Ch 26 — GDB Remote Serial Protocol

> 目標：搞懂 GDB 與 remote target（OpenOCD、qemu、自己刻 stub）之間的 wire protocol。能讀 packet log、debug 「GDB 看不見 register」「load 時 timeout」這類 wire-level 問題。能自己寫個迷你 GDB stub。

## RSP 是什麼

GDB Remote Serial Protocol：GDB 與 remote 之間的 ASCII 協定。1990 年代設計，**簡單到能在 9600 baud serial line 上跑**。

```
Host (GDB) ←──── TCP / serial / pipe ────→ Remote (gdbserver / OpenOCD / stub)
        $packet#checksum
        +/-              acknowledge
```

每個 packet 格式：

```
$<data>#<2-digit-hex-checksum>
```

checksum = sum of data bytes mod 256，2 位 hex。

接收方回 `+` (ack) 或 `-` (resend)。

## 看一段 wire log

啟 OpenOCD `-d3`，能看到所有 RSP packet：

```
< $g#67                  # GDB: 給我所有 register
> $0000000000000000 ... #ab
< $m20000000,4#27        # GDB: 讀 0x20000000 起 4 bytes
> $deadbeef#XX
< $Z0,8000000,2#YY       # GDB: 設斷點在 0x8000000，size 2
> $OK#9a
< $c#63                  # GDB: continue
> $T05swbreak;...#XX     # remote: stopped due to swbreak
```

兩個方向交錯。每個 packet 後 `+` ack。

## 常用 packet 一覽

| Packet | 意義 |
|---|---|
| `?` | 為什麼停？ |
| `g` | 讀全部 register |
| `G XX..` | 寫全部 register |
| `p N` | 讀 register N |
| `P N=V` | 寫 register N |
| `m ADDR,LEN` | 讀記憶體 |
| `M ADDR,LEN:DATA` | 寫記憶體 |
| `c` | continue |
| `s` | step |
| `Z TYPE,ADDR,KIND` | 設斷點 |
| `z TYPE,ADDR,KIND` | 清斷點 |
| `vCont;c` | 多執行緒 continue |
| `qSupported:...` | feature 協商 |
| `qXfer:features:read:target.xml` | 讀 target description |
| `T XX...` | stop reply |

完整列表在 GDB 文件 `gdb/doc/gdb.texinfo` Remote Protocol 章。

## Z packet：斷點種類

```
Z0 = software breakpoint
Z1 = hardware breakpoint
Z2 = write watchpoint
Z3 = read watchpoint
Z4 = access watchpoint
```

GDB 預設 `Z0`（software），對 ARM 是把目標位址換成 `BKPT` (Cortex-M) 或 `BRK #1` (AArch64)。**flash 不能寫**，所以對 flash 區的斷點 GDB 改用 `Z1`（hardware breakpoint，吃 FPB / Cortex-A 的 break-comparator）。

OpenOCD 對 flash 自動偵測切到 `Z1`，但 hardware breakpoint 數量限（FPB 通常 6 個，Cortex-A 6-16 個）。**用完就掛**「Cannot insert breakpoint」。

## qSupported：開場協商

GDB 連線後第一件事：

```
< $qSupported:multiprocess+;swbreak+;hwbreak+;...#XX
> $PacketSize=4000;qXfer:features:read+;...#XX
```

雙方互報「我支援這些 feature」，挑共同子集。`PacketSize` 限制單包大小。

## qXfer:features：target description

GDB 不知道 ARM 暫存器數量、命名 — 怎麼解 `g` packet 的 binary？

答：**target description (XML)**。GDB 啟動時讀：

```
< $qXfer:features:read:target.xml:0,fff#XX
> $l<?xml version="1.0"?>
   <target version="1.0">
     <architecture>aarch64</architecture>
     <feature name="org.gnu.gdb.aarch64.core">
       <reg name="x0" bitsize="64"/>
       ...
     </feature>
   </target>#XX
```

target.xml 列出 register、bitsize、架構，GDB 拿來 parse `g` packet 內容。**這是 GDB 跨架構的關鍵**：同一個 GDB binary debug ARM、x86、RISC-V，靠 target.xml 區分。

寫自己 stub 也要提供 target.xml — 不然 GDB 看到 register dump 解不開。

## ACK / non-ACK mode

每個 packet 都 `+` ack 是慢（多 round-trip）。**GDB 8+ 預設 non-ACK mode**：

```
< $QStartNoAckMode#XX
> $OK#YY
< +
< $g#67
> $...#XX
                              # 從這裡起不需 ack
```

OpenOCD、qemu、modern gdbserver 全支援。對 USB / network 來回延遲大的場景**速度提升 2-3×**。

## 多核 / 多執行緒：vCont

```
< $vCont;c#XX                  # 全部 continue
< $vCont;c:p1.-1#XX             # process 1 全部 thread continue
< $vCont;c:1;s:2#XX             # thread 1 continue, thread 2 step
```

OpenOCD SMP 配置會把每個 core 報為一個 thread，GDB 用 `vCont` 控制每個 core。

## 寫一個迷你 stub

最小 stub 要支援：

- `?`：回傳 stop reason
- `g` / `G`：register 全 dump 與 set
- `m` / `M`：memory 讀寫
- `c` / `s`：continue / step
- `Z0` / `z0`：software breakpoint

虛擬碼：

```c
char buf[1024];

void gdb_loop(void) {
    while (1) {
        recv_packet(buf);
        switch (buf[0]) {
            case '?':  send("S05"); break;            // SIGTRAP
            case 'g':  send_registers(); break;
            case 'G':  parse_set_registers(buf+1); send("OK"); break;
            case 'm':  send_memory(buf+1); break;
            case 'M':  set_memory(buf+1); send("OK"); break;
            case 'c':  resume(); single_step = 0; return;
            case 's':  resume(); single_step = 1; return;
            case 'Z':  insert_bp(buf+1); send("OK"); break;
            case 'z':  remove_bp(buf+1); send("OK"); break;
            default:   send("");                       // unknown
        }
    }
}
```

`send_registers()` 把 R0-R15 + PSR 編成 hex 字串：

```c
void send_registers(void) {
    char *p = buf;
    for (int i = 0; i < 16; i++) {
        uint32_t v = regs[i];
        // 注意：little-endian！每 byte 反向 hex
        for (int b = 0; b < 4; b++) {
            sprintf(p, "%02x", (v >> (b*8)) & 0xff);
            p += 2;
        }
    }
    send(buf);
}
```

完整 stub 要做 packet framing、checksum、ACK / NACK，但骨架就這樣。實作放 final project 的 trace 模組可以參考。

## 對 Linux gdbserver 對比

Linux user space `gdbserver` 跑在 target，透過 ptrace 控制 process。OpenOCD / bare-metal stub 直接控 CPU。但 wire 上**RSP 完全一樣**：GDB 不知道（也不關心）對面是 OS 內 process 還是 bare-metal CPU。

## qemu 也有 GDB server

QEMU 啟動加 `-s -S`：

```
-s     開 GDB server on :1234
-S     啟動時暫停（不要直接跑）
```

```bash
qemu-system-arm -M mps2-an385 -kernel firmware.elf -s -S
# 另一終端
gdb-multiarch firmware.elf
(gdb) target remote :1234
```

QEMU 的 GDB server 用全 RSP，跟 OpenOCD 對 GDB 看起來一樣。**寫 firmware 用 QEMU debug 是最便宜方式**。

## debug RSP 本身：dprintf

GDB 可以 dump RSP 流量：

```
(gdb) set debug remote 1
(gdb) target remote :3333
Sending packet: $qSupported:multiprocess+...#XX
Received: $PacketSize=4000;...#XX
...
```

**遇到「GDB 顯示 register 全 0xfffffff」「load timeout」這種狀況開這個看 wire**。多數時候會看到 packet 沒回、checksum 不對、target.xml 有缺。

## 一個常見誤解

「RSP 太老了，現代 debugger 是不是有更好協定？」

**新協定有，但 RSP 還是事實標準**。LLDB 也用 RSP（加自家擴展），CodeLLDB / VSCode debug adapter 底下還是 RSP。

替代協定（DAP - Debug Adapter Protocol，VSCode 用）是 **GUI 與 debugger 之間** 的協定，不取代 GDB↔target wire 協定。RSP 仍在那一層。

## 自我檢核

- [ ] 我能解碼一個 `$packet#xx` 的 checksum
- [ ] 我能列出 g / m / Z0 / c / s 五個常用 packet
- [ ] 我能解釋 ACK 與 non-ACK mode 的差別
- [ ] 我能說明 target.xml 在哪一步傳遞
- [ ] 我能用 `set debug remote 1` 看 wire log
- [ ] 我能寫一個最小 stub 的骨架

下一章看斷點 — 軟體 breakpoint vs 硬體 breakpoint、watchpoint、Cortex-M 的 FPB / DWT、Cortex-A 的 break/watch comparator。

→ [Ch 27 硬體斷點 vs 軟體斷點、watchpoint](./27-breakpoints-watchpoints.md)
