# Ch 36 — gdbserver 與 remote protocol

> **目標**：掌握遠端除錯——`gdbserver` 在目標機跑、本機 GDB 連過去；理解 GDB Remote Serial Protocol (RSP) 的運作；`target remote` vs `extended-remote`；以及這套機制怎麼成為嵌入式、容器、跨架構、QEMU 除錯的共同基礎。

> **環境**：GDB 13/14，gdbserver，Linux x86_64（概念適用所有 target）。

## 為什麼需要遠端除錯

很多時候，你要 debug 的程式**不在你的開發機上**：

- **嵌入式裝置**：目標是個 ARM 板子，跑不動完整 GDB，但能跑輕量的 gdbserver
- **容器 / VM**：程式在容器裡，你想從 host debug
- **跨架構**：你在 x86 開發機，目標是 ARM/RISC-V binary（配 QEMU）
- **生產機**：不想在生產機裝完整 toolchain，只放一個 gdbserver
- **kernel / 裸機**：透過 JTAG/QEMU 的 gdb stub

這些全靠同一套機制：**GDB（前端，在你的機器）+ stub（在目標，回應 GDB 的請求）**，中間用 RSP 協定溝通。這是 Ch 1 講的 target 抽象的極致體現——同樣的 GDB 指令，operating 一個網路另一頭的程式。

## 先建立直覺：前端與後端分離

```
   你的開發機                          目標機 / 裝置
   ┌─────────────────┐                ┌──────────────────────┐
   │   GDB (前端)    │  RSP over      │  gdbserver (後端)     │
   │  - 符號/DWARF   │  TCP/serial    │  - 用 ptrace 控制     │
   │  - 你的指令     │ ◄────────────► │    目標 process       │
   │  - 顯示         │  "$g#..." 等   │  - 讀寫記憶體/暫存器  │
   └─────────────────┘                └──────────────────────┘
        重量級                              輕量級
        （符號都在這）                       （只做 ptrace 動作）
```

關鍵分工：

- **GDB 前端**：擁有符號、DWARF、你的指令解析、顯示邏輯——重量級。
- **gdbserver 後端**：只負責「用 ptrace 對目標做動作」（讀記憶體、設斷點、讀暫存器）——輕量，不需要符號。

所以嵌入式板子只要跑得動小小的 gdbserver，符號全在你開發機的 GDB 裡。這個分離是遠端除錯的精髓。

## 最簡單的遠端除錯

同一台機器先練（之後換成真遠端只是改 IP）：

```bash
# 目標端：用 gdbserver 啟動程式，監聽 port
gdbserver :1234 ./myprog arg1
# Process myprog created; pid = 5678
# Listening on port 1234
```

```bash
# 開發端：GDB 連過去
gdb ./myprog                      # 載入符號（本機要有同一份 binary）
(gdb) target remote :1234         # 連到 gdbserver（同機）
# 或 target remote 192.168.1.50:1234（真遠端）
Remote debugging using :1234
(gdb) break main
(gdb) continue                    # 一切像本機 debug！
(gdb) bt
(gdb) print x
```

連上之後，**所有 GDB 指令照常用**——break、step、print、bt 全部。底層每個操作都變成 RSP 封包送到 gdbserver 執行。這就是 target 抽象的威力。

## attach 遠端的活 process

```bash
# 目標端：attach 一個已在跑的 process
gdbserver :1234 --attach 5678
```

```bash
# 開發端
(gdb) target remote :1234
```

## `target remote` vs `extended-remote`

```
(gdb) target remote :1234         # 一次性：程式結束 / detach 後連線就斷
(gdb) target extended-remote :1234 # 持久：可以 run 多次、重啟程式
```

差別：

| | `target remote` | `target extended-remote` |
|---|---|---|
| 連線 | 程式結束就斷 | 持久，可重複用 |
| `run` | 不能重啟 | 可以 `run` 重啟程式 |
| 多 process | 有限 | 支援（配 follow-fork）|
| gdbserver 啟動 | `gdbserver :port prog` | `gdbserver --multi :port`（不指定程式）|

`extended-remote` + `gdbserver --multi` 讓你連上後在 GDB 裡決定跑什麼、可重啟、可 debug 多個 process——較靈活，嵌入式/反覆 debug 時用。

```bash
# extended 模式：gdbserver 不綁特定程式
gdbserver --multi :1234
```
```
(gdb) target extended-remote :1234
(gdb) set remote exec-file /path/on/target/myprog
(gdb) run                         # 在目標上啟動
(gdb) run                         # 可以再 run！
```

## RSP：底層協定

GDB 和 gdbserver 用 **Remote Serial Protocol** 溝通——一個簡單的文字協定。每個封包是 `$<data>#<checksum>`：

```
   GDB → server:  $g#67              （g = 讀所有暫存器）
   server → GDB:  $<暫存器值的 hex>#xx
   GDB → server:  $m1000,4#..        （m = 讀位址 0x1000 起 4 bytes）
   server → GDB:  $<4 bytes hex>#xx
   GDB → server:  $Z0,1149,1#..      （Z0 = 設軟體斷點 at 0x1149）
   GDB → server:  $c#63              （c = continue）
   server → GDB:  $T05...#xx         （停了，signal 05 = SIGTRAP）
```

常見封包：

| 封包 | 意思 |
|---|---|
| `g` / `G` | 讀 / 寫所有暫存器 |
| `m` / `M` | 讀 / 寫記憶體 |
| `c` / `s` | continue / step |
| `Z` / `z` | 設 / 清斷點 |
| `?` | 查詢停止原因 |
| `T` / `S` | 停止回報（含 signal） |

理解 RSP 的價值：

- debug 連線問題（`set debug remote 1` 看封包往來）
- 知道為什麼遠端比本機慢（每個操作一次往返）
- 寫自己的 gdb stub（嵌入式 bootloader、模擬器、自製 OS——呼應 linux_boot / arm 課程）

## 觀察 RSP 封包

```
(gdb) set debug remote 1          # 印出所有 RSP 封包往來
(gdb) target remote :1234
Sending packet: $qSupported:...#xx
Received: PacketSize=...;...
(gdb) continue
Sending packet: $vCont;c#xx
...
```

debug 「遠端連不上」「某操作在遠端壞掉」時，`set debug remote 1` 看封包是診斷利器。也是學習 RSP 的最好方式——直接看 GDB 和 server 在說什麼。

## 符號與檔案：遠端的關鍵細節

遠端除錯最常見的坑是**符號**：

- gdbserver 不需要符號，但**你的 GDB 需要**——本機要有「和目標上跑的完全相同」的 binary（含 debug info）。
- library 也一樣：目標的 `.so` 版本要和你 GDB 找到的一致。

```
(gdb) set sysroot /path/to/target/rootfs   # 告訴 GDB 去哪找目標的 library
(gdb) set solib-search-path /path/to/libs
(gdb) file ./myprog-with-symbols           # 本機的符號版 binary
```

`set sysroot` 指向目標檔案系統的副本，讓 GDB 從那裡讀 library 符號——嵌入式/跨架構除錯必設（Ch 37 細講）。

## 一個完整的遠端流程

```bash
# 目標機（如樹莓派）
$ gdbserver :2345 ./sensor_daemon
```
```
# 開發機（有同一份帶符號的 binary）
(gdb) file ./sensor_daemon          # 本機符號版
(gdb) set sysroot ./pi-rootfs       # 目標的 rootfs 副本（找 libc 等）
(gdb) target remote raspberrypi.local:2345
(gdb) break sensor_read
(gdb) continue
(gdb) bt                            # 在開發機看樹莓派上的 backtrace
(gdb) print reading
```

你坐在大螢幕的開發機前，debug 一個跑在樹莓派上的程式，享受完整的符號、TUI、Python 插件——這就是遠端除錯的體驗。

## 踩雷集錦

1. **本機 binary 和目標不一致**：符號全錯、斷點下錯位址。本機的 binary 必須和目標跑的**完全相同**（同次編譯）。
2. **忘了 `set sysroot`**：找不到目標的 library 符號，`bt` 進 libc 是 `??`。指向目標 rootfs。
3. **遠端慢**：每個操作一次網路往返。大量 step / 讀大塊記憶體會明顯慢。減少來回（一次讀一塊而非逐 byte）。
4. **防火牆 / port**：gdbserver 的 port 要通。雲/容器注意防火牆、port forward。
5. **`target remote` 想重啟程式失敗**：用 `extended-remote` + `--multi` 才能 `run` 多次。
6. **架構不匹配**：x86 的 GDB 連 ARM 的 gdbserver 要用 `gdb-multiarch` 或 cross-gdb（Ch 37）。
7. **gdbserver 版本太舊**：和新 GDB 的 RSP 協商可能有問題。盡量版本接近。

## 進階：再往深一層

- **RSP over serial**：嵌入式常用串列埠而非 TCP：`target remote /dev/ttyUSB0`。裸機/bootloader debug 經典方式。
- **`qSupported` 協商**：連線時 GDB 和 server 協商支援哪些功能（封包大小、非停模式、硬體斷點數）。`set debug remote` 看得到。
- **自己寫 gdb stub**：實作 RSP 的核心封包（g/m/c/s/Z），你的模擬器/OS/bootloader 就能被 GDB debug。QEMU、自製 emulator（呼應 riscv 課程的 emulator）都這樣做。
- **non-stop remote**（Ch 15）：遠端也能 non-stop，只停一個 thread。
- **`gdbserver --once`**：服務一個連線就退出（自動化）。
- **gdbserver 的安全性**：RSP 沒有加密/認證！任何能連到 port 的人都能完全控制目標 process。生產環境要透過 SSH tunnel（`ssh -L 1234:localhost:1234`）或限制網路。
- **`target` 的其他形態**：`target sim`（內建模擬器）、`target tfile`（trace 檔）——target 抽象的更多分支。

## 動手練習

1. 同機練習：`gdbserver :1234 ./myprog`，另一個 terminal `gdb ./myprog` + `target remote :1234`，跑完整 debug 流程。
2. `set debug remote 1` 後連線並 `continue`，觀察 RSP 封包（`$g`、`$m`、`$Z`、`$vCont`），對照本章的封包表。
3. 試 `target extended-remote` + `gdbserver --multi`，連上後 `run` 兩次（`target remote` 做不到）。
4. `gdbserver :1234 --attach <pid>` attach 一個活的 process，從 GDB 端控制它。
5. 跨機器（或容器）：在另一台/容器跑 gdbserver，從本機連過去，設 `sysroot` 解決 library 符號。
6. （進階）讀 QEMU 的 `-s -S` gdb stub 文件，理解它怎麼實作 RSP（為 Ch 37 鋪路）。

## 本章重點整理

- 遠端除錯 = GDB 前端（符號/顯示，重）+ gdbserver 後端（ptrace 動作，輕），用 RSP 協定溝通。
- `gdbserver :port prog`（目標）+ `target remote host:port`（本機）；所有 GDB 指令照常用。
- `target remote`（一次性）vs `extended-remote` + `--multi`（持久、可重啟、多 process）。
- RSP 是 `$data#checksum` 文字協定（g/m/c/s/Z…）；`set debug remote 1` 觀察、診斷、學習。
- 本機 binary 必須和目標一致；`set sysroot` 指向目標 rootfs 找 library 符號。
- RSP 無加密——生產環境走 SSH tunnel。

## 自我檢核

- [ ] 遠端除錯時，符號在哪一端、ptrace 動作在哪一端？為什麼這樣分？
- [ ] `target remote` 和 `extended-remote` 差在哪？想在連上後 `run` 多次用哪個？
- [ ] RSP 大概長什麼樣？怎麼觀察封包往來？
- [ ] 遠端 debug 找不到 library 符號，要設什麼？
- [ ] 為什麼說 gdbserver 有資安風險？怎麼緩解？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Remote Debugging](https://sourceware.org/gdb/current/onlinedocs/gdb/Remote-Debugging.html)** 與 **[Remote Protocol](https://sourceware.org/gdb/current/onlinedocs/gdb/Remote-Protocol.html)**
  - **讀哪裡**：Connecting、gdbserver、`set sysroot`；Remote Protocol 的封包定義（g/m/c/Z…）。
  - **和本章的關聯**：本章核心的權威；想寫 stub 必讀 Remote Protocol。

### 部落格 / 文章

- **[Implementing a GDB stub](https://medium.com/@tristan_19022/implementing-a-gdb-stub-in-rust-for-bare-metal-debugging)** 類文章 / **[gdbstub crate 文件](https://docs.rs/gdbstub/)**
  - **這篇說什麼**：怎麼為自己的模擬器/OS 實作 RSP stub。
  - **和本章的關聯**：理解 RSP 的最佳方式是實作一個；呼應 riscv/linux_boot 課程。

### 規格

- **[GDB RSP 完整封包列表](https://sourceware.org/gdb/current/onlinedocs/gdb/Packets.html)**
  - **讀哪裡**：當 reference 查特定封包。
  - **和本章的關聯**：寫 stub 或 debug 協定問題時的權威。

下一章把遠端機制用到跨架構與嵌入式——cross-gdb、QEMU、OpenOCD/JTAG、裸機與 kernel 除錯。

→ [Ch 37 跨架構與嵌入式](./37-cross-arch-and-embedded.md)
