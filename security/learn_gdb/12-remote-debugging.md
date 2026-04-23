# Ch 12 — Remote debugging 與 gdbserver

> 目標：熟悉 `gdbserver` 的兩種啟動方式（launch / attach）、`target remote` 連線、跨架構 debug 的 gdb-multiarch，以及 remote debugging 為什麼值得。

## 用在哪

- **生產環境的機器沒有編譯器、沒有 source**，但 bug 只在那跑出來
- **嵌入式 / ARM 板子** — 資源不夠，或沒螢幕，不能本機跑 gdb
- **Container / VM 裡的 process** — 主機 gdb 進不去
- **Debug 一個在別人電腦上出事的程式** — 他跑 gdbserver、你遠端接

本質上 remote debugging 把 GDB 拆成兩半：

```
┌──────────────────┐            ┌──────────────────┐
│  Your machine    │            │  Remote machine  │
│                  │  TCP/SSH   │                  │
│   GDB frontend   │ ◄────────► │   gdbserver      │
│   (有 source、    │            │   (只有 binary、  │
│    有 UI)         │            │    沒 source)     │
└──────────────────┘            └──────────────────┘
```

GDB 做 UI、讀 DWARF、管 source。gdbserver 做 ptrace。兩者用 **GDB Remote Serial Protocol（RSP）** 溝通，協定細節非常規整（純文字、字元導向、checksum）— 你用 `strace` 就能看到 `$g#00` 這種請求。

## 第一次遠端：兩台機器

這節假設你在本機（可以是同一台也可以是 SSH 過去）有 gdb，另一台（或同一台）跑 gdbserver。示範用 loopback。

### 遠端：啟動 gdbserver 並 launch 程式

```bash
# 在「target」機器上
gdbserver localhost:1234 ./sample
```

意思：

- 監聽 TCP port 1234
- 啟動 `./sample`，但**不馬上執行** — 等你 connect

看到輸出：

```
Process ./sample created; pid = 12345
Listening on port 1234
```

### 本機：啟動 gdb 並連進去

**把** `sample` 的 binary 拷一份到你本機，因為 gdb 需要讀 DWARF（gdbserver 不會幫你傳 binary）。

```bash
# 本機
gdb -q ./sample
(gdb) target remote localhost:1234
Remote debugging using localhost:1234
```

現在你就像平常一樣 debug：

```
(gdb) b main
(gdb) c
Continuing.

Breakpoint 1, main () at sample.c:17
17          int n = 5;
```

所有指令（`bt`、`p`、`step`）都會通過 RSP 發到 gdbserver 執行。

### 斷線

本機：

```
(gdb) disconnect               ; gdb 這邊斷開，gdbserver 還在
(gdb) detach                   ; 斷開並讓 inferior 繼續跑（遠端 gdbserver 會結束）
(gdb) kill                     ; 殺掉遠端 inferior
(gdb) quit
```

## 另一種用法：attach 到已經在跑的 process

遠端：

```bash
gdbserver --attach localhost:1234 <PID>
```

本機：

```
gdb -q ./sample
(gdb) target remote localhost:1234
```

跟 launch 模式一樣 debug。結束後：

```
(gdb) detach              ; 保險，讓 inferior 繼續運行（不殺）
```

## 如果本機沒 binary：只有 host 有

最乾淨的做法：**把 binary 從遠端 copy 到本機**，讓本機 gdb 能讀 DWARF。

```bash
scp remote:/opt/app/myprog ./myprog
gdb -q ./myprog
(gdb) target remote remote-host:1234
```

如果遠端 binary 有 debug info（`-g`），本機 gdb 能完整工作。

### 如果遠端 binary 已被 strip 呢？

遠端 binary strip 掉 debug info 是生產環境常態。你需要**一份 unstripped 版本**留在本機開發端：

```bash
# build 時
gcc -g -o myprog myprog.c
cp myprog myprog.unstripped
strip myprog                    # 這份部署到遠端

# debug 時本機用 .unstripped 版本
gdb -q ./myprog.unstripped
(gdb) target remote remote-host:1234
```

binary 的 `.text` 等實質 segment 是一樣的（strip 只拿掉 symbol/debug 段），所以遠端的位址跟本機的位址能對上。

## gdbserver 的啟動方式：多 session 用 `--multi`

預設 gdbserver 只服務一次連線，inferior 結束 / detach 後 gdbserver 也退出。想要一個長期服務：

```bash
gdbserver --multi localhost:1234
```

然後在本機每次：

```
(gdb) target extended-remote localhost:1234
(gdb) set remote exec-file /path/to/prog/on/remote
(gdb) run
```

`target extended-remote`（不是 `target remote`）才能在 session 內重啟 inferior（`run` 多次）或切 executable。

## 跨架構 debug：gdb-multiarch

場景：本機是 x86_64，遠端 target 是 ARM（例如樹莓派）或 RISC-V。

本機裝 `gdb-multiarch`：

```bash
sudo apt install gdb-multiarch
```

遠端裝對應架構的 gdbserver：

```bash
# 樹莓派上
sudo apt install gdbserver
```

本機啟動：

```bash
gdb-multiarch -q ./arm_binary
(gdb) set architecture arm            # 或讓它自動偵測
(gdb) target remote pi:1234
```

之後指令跟本地一樣。**這是 embedded 工作的日常**：在 workstation debug 板子上的程式。

### sysroot — 跨架構的 library 對照

跨架構時，遠端 binary 用的 libc、libpthread 等在本機可能沒有。你看 bt 進到 libc 函式時會出現 `<optimized out>` 或 `??`。

解法：把 target 的 `/lib` 與 `/usr/lib` 拷回本機某個目錄，然後：

```
(gdb) set sysroot /path/to/pi/libs
```

gdb 會從那裡找 shared library 的 symbol。對複雜 debug 很關鍵。

## OpenOCD + JTAG：真的嵌入式

板子連電腦只有 JTAG，沒網路 / USB 串列。`OpenOCD` 扮演 gdbserver，透過 JTAG 控制 CPU：

```bash
# 在本機（連著 JTAG debugger 的那台）
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg
```

OpenOCD 會在本機 port 3333 開一個 RSP server。然後：

```
gdb-multiarch -q firmware.elf
(gdb) target remote localhost:3333
(gdb) monitor reset halt           ; OpenOCD 特殊指令
(gdb) load                         ; 把 firmware flash 進板子
(gdb) continue
```

這就是嵌入式 firmware 開發的標準工作流。`monitor` 命令會被 RSP forward 到 OpenOCD 執行。

## SSH 包裝：比 TCP port 更安全

不想開 port？ssh stdio：

```
gdb -q ./sample
(gdb) target remote | ssh user@remote-host gdbserver --stdio ./sample
```

gdb 把 ssh 的 stdin/stdout 當成 gdbserver 的 RSP channel。全程走 SSH encrypted tunnel。

## 看 RSP 協定：`set debug remote 1`

想知道 gdb 實際發什麼指令給 gdbserver：

```
(gdb) set debug remote 1
(gdb) c
Sending packet: $vCont;c#a8...Ack
Packet received: T05thread:p...;core:0;...
...
```

每個 `$...#XX` 是一個 RSP packet。`#XX` 是 checksum。協定細節在 GDB manual，可以 google「GDB Remote Serial Protocol」。

這對寫自己的 debugger 後端（例如讓 VS Code 連上自己的 debugger）會用到。

## 效能：remote debugging 很慢

原因：

- 每個 `step` / `print` 都是一次 round-trip
- `step` 一次要 get regs、set regs、step、get regs — 4 次 round-trip
- 網路 latency 10ms 就變成 40ms per step

優化：

- 能在本機做的不要遠端（例如 disas 只要位址對就行）
- 用 `set remotetimeout 20` 等長一點避免 timeout
- gdbserver 7.0+ 支援 `--once` 減少 setup overhead
- 如果反覆使用，考慮 `gdbserver --multi` 減少 setup

## 常見坑

1. **版本不匹配**：gdb 跟 gdbserver 的 major version 最好一致。差太多會有協定失配。
2. **Ack / No-ack mode**：新版預設 no-ack（少一半 round-trip），老版要手動 `set remote noack-packet on`。
3. **binary 不一致**：本機的 binary 跟遠端不同，DWARF 對不上位址。確保兩邊是同一個 build。
4. **找不到 source**：遠端 binary 的 DWARF 存的是編譯機器上的路徑。本機找不到。用 `set substitute-path /build/path /your/local/path`。
5. **libc 版本差**：bt 進 libc 出亂碼。設 sysroot 或安裝對應的 `libc6-dbg`。
6. **`Connection refused`**：gdbserver 沒監聽、防火牆擋、port 打錯。先 `nc remote-host 1234` 測。
7. **ASLR 不同步**：遠端開了 ASLR、本機沒關。`set disable-randomization on/off` 根據兩邊狀況調整。

## 動手練習

### 練習一：本機 loopback

一個 terminal：

```bash
gdbserver localhost:1234 ./sample
```

另一個：

```bash
gdb -q ./sample
(gdb) target remote localhost:1234
(gdb) b main
(gdb) c
(gdb) bt
(gdb) n
(gdb) p n
```

### 練習二：attach mode

```bash
./sample &                    # 讓它背景跑
sleep 1
gdbserver --attach localhost:1234 $!
```

另一個 terminal：

```bash
gdb -q ./sample
(gdb) target remote localhost:1234
(gdb) bt
```

### 練習三：strip + 本機 debuginfo

```bash
cp sample sample.full
strip sample                  # 移除 debug info

gdbserver localhost:1234 ./sample
```

本機用 `sample.full`：

```bash
gdb -q ./sample.full
(gdb) target remote localhost:1234
(gdb) b main                  # 仍然能下斷點因為本機有 symbol
```

### 練習四：SSH stdio（若有第二台機器）

```bash
# 本機
gdb -q ./sample
(gdb) target remote | ssh user@remote-machine gdbserver --stdio ./sample
```

### 練習五：看 RSP

```
(gdb) set debug remote 1
(gdb) si
```

觀察 packet 流。

## 自我檢核

- [ ] 我能用 gdbserver 啟動 launch 或 attach 模式
- [ ] 我能從本機用 `target remote` 連進去 debug
- [ ] 我知道 binary 要在本機有一份（含 debug info）
- [ ] 我會用 `set sysroot` 指向遠端 library 的本機副本
- [ ] 我知道 gdb-multiarch + 遠端 gdbserver 可以跨架構 debug
- [ ] 我知道 OpenOCD + JTAG 是嵌入式 firmware debug 的方式
- [ ] 我能看 RSP 協定的實際 packet

下一章看 post-mortem — 程式已經死了，只留下 core dump，我們怎麼從一具「屍體」還原現場？

→ [Ch 13 Core dump 與 post-mortem](./13-core-dumps.md)
