# Ch 29 — ITM / SWO trace 與 printf debugging

> 目標：吃透 ITM trace 機制 — port 寫法、stimulus packet、time stamp、DWT 事件、TPIU 出口（SWO 或 ETB）。能寫一個比 UART printf 快百倍、不阻塞 CPU 的 log 系統。

## ITM 是什麼

**ITM = Instrumentation Trace Macrocell**。簡單說：CPU 寫 register，**硬體把那個寫入打包成 trace packet 出 SWO pin**。

```
CPU:  STR  R0, [ITM->PORT[0]]    ; 寫 ITM port
                  │
                  ▼
ITM 硬體：把寫入打包：[port=0, len=N, data=R0]
                  │
                  ▼
TPIU: 加 timestamp、合併其他 trace source
                  │
                  ▼
SWO 序列輸出 → host 抓
```

對 CPU 端：**就是一條 STR 指令，幾 ns**。對比 UART printf 的數百 µs，**快 1000+ 倍**。

## ITM 啟用步驟

```c
void ITM_Init(uint32_t cpu_hz, uint32_t swo_hz) {
    /* 開 trace */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;

    /* TPIU 配置 */
    TPI->SPPR = 2;                  /* protocol = SWO async (NRZ) */
    TPI->ACPR = cpu_hz / swo_hz - 1;
    TPI->FFCR = 0x00000100;         /* enable formatter on stimulus only */

    /* ITM 啟用 */
    ITM->LAR = 0xC5ACCE55;          /* unlock */
    ITM->TCR = 0x00010001;          /* ITMENA, TraceBusID=1 */
    ITM->TER = 0xFFFFFFFF;          /* enable all 32 ports */
    ITM->TPR = 0;                   /* allow unprivileged access (optional) */
}
```

寫了一堆 magic constant，但對應 CMSIS macro 是 `ITM_SendChar`（CMSIS 提供）。手刻時要 **`LAR = 0xC5ACCE55`** 解鎖（防 random write），這個 magic value 寫錯就 silent 失敗。

## 寫一個字元

```c
void ITM_PutChar(uint32_t port, char c) {
    if (!(ITM->TCR & ITM_TCR_ITMENA_Msk)) return;
    if (!(ITM->TER & (1 << port))) return;
    while (ITM->PORT[port].u32 == 0) ;        /* wait for port ready */
    ITM->PORT[port].u8 = c;
}
```

Port ready 檢查：ITM port 有 FIFO，滿了 register 讀回 0。loop 等空。**也可不等**，丟掉的字元算了 — 取決於你要可靠 vs 不阻塞。

## printf via ITM

newlib `_write` 重定向：

```c
int _write(int fd, const char *buf, int len) {
    for (int i = 0; i < len; i++) ITM_PutChar(0, buf[i]);
    return len;
}
```

之後 `printf` 自動走 ITM。不用 UART driver、不用 baud config。

CMSIS 也提供 `ITM_SendChar(c)` 可直接用。

## 多 port 的用法

ITM 32 個 port，慣例分流：

```
Port 0:     printf 輸出
Port 1:     critical event timestamp（state transition）
Port 2:     IRQ entry/exit
Port 3:     application metric A
Port 4:     application metric B
...
```

host 端 OpenOCD 可分別 dump 各 port 到不同 file：

```
itm port 0 on
itm port 1 on
tpiu config internal /tmp/swo.bin uart off 168000000 2000000
```

之後寫 script parse `/tmp/swo.bin`，依 port 分流到不同檔。

## DWT exception trace

ITM 不只是寫 char，**DWT 能自動 emit exception entry/exit packet**：

```c
DWT->CTRL |= (1 << 16);    /* EXCTRCENA: exception trace enable */
```

開了之後，每次 IRQ entry / exit / return → ITM 自動發一個 packet 含 exception number 與 timestamp。host 重建出整個 IRQ timeline，**對 IRQ 風暴 / 優先權翻轉**極有幫助。

## Sleep cycle / fold inst counter

DWT 也能用 ITM 出 sleep 與其他 counter event：

```c
DWT->CTRL |= (1 << 21);   /* CYCEVTENA */
DWT->CTRL |= (1 << 17);   /* PCSAMPLEENA — periodic PC sample */
```

可以重建 PC over time，做 statistical profiling。Linux ARM 上 perf coresight 用 ETM 走更精細的 trace；嵌入式直接用 DWT + ITM 打 light-weight profiling。

## SWO speed limit

SWO 物理層是 async UART，速度限制：

- ARM 規格：max 60 MHz（NRZ）
- 實際多數 probe：4–12 MHz 穩定，更高會丟 packet
- bandwidth at 4 MHz：約 400 KB/s

**printf 本身用不到這頻寬**，但開 exception trace + PC sample 可能爆掉。SWO drop 後 OpenOCD 顯示「sync packet」warning。**遇到大量 trace 損失就降頻或換 ETB**。

## ETB：on-chip buffer 替代 SWO

如果不想拉 SWO 線、或 SWO 頻寬不夠，可改用 **ETB (Embedded Trace Buffer)**：

```
ITM ──→ TPIU ──→ ETB (32-64 KB on-chip SRAM)
                   │
                   └── host 透過 DAP 讀
```

trace 暫存晶片內，host 用 SWD 讀。**頻寬不靠 SWO 物理速率**，但容量小（最多幾百 ms 的 trace）。

對「**抓 crash 前最後一段**」是黃金組合：crash 時 CPU halt，ETB 內容凍結，host 透過 DAP dump 出來。

## 解析 SWO 串流

SWO 出來是 byte stream，先要 demux 到 port：

```
ITM packet 格式（簡化）：
  Header byte: bit[0:1] = type
                bit[2:4] = size
                bit[3:7] = port number (對 stimulus packet)
  Payload: 1/2/4 bytes
```

OpenOCD `tpiu config internal file ...` 把 raw stream dump 到 file，使用 `swo_console` 之類工具或自己寫 parser 解開。

工具：

- `pyOCD` 自帶 `swv-console`
- **Orbuculum / Orb tools**：好用的 SWO console
- SEGGER J-Link 軟體有自家 viewer

## RTT：替代 SWO 的軟體方案

SEGGER 發明的 **RTT (Real-Time Transfer)**：

- target 在 SRAM 開一塊 ring buffer
- target 寫 buffer
- host 透過 SWD 周期性 polling buffer

**完全不需要 SWO pin、不需要 TPIU 配置**，但需要 J-Link（SEGGER 工具）或 probe-rs 端的 RTT 支援。

優點：

- 比 SWO 簡單（不用 baud / TPIU）
- bidirectional（host 可寫進 buffer 給 target 讀）
- bandwidth 隨 SWD speed scale

對沒拉 SWO pin 的 dev board / 想用 J-Link 的 user，RTT 是好選擇。

## 一個常見誤解

「ITM 是不是只 Cortex-M 有？」

**Cortex-A 也有**！但叫法稍不同 — Cortex-A 上 ITM-like 的單元是 CoreSight ITM module（位於 SoC 的 debug bus）。實作概念一樣（write register → trace packet），但配置介面從 system register 變成 memory-mapped APB register。

實務上 Cortex-A debug 大多用 ftrace、perf、ETM、kgdb 等工具，ITM 用得少。

## printf 三種選擇對照

| 方案 | 速度 | 線數 | 阻塞 | 適合 |
|---|---|---|---|---|
| **UART printf** | 慢（µs/char） | UART pins | 是（waitTXE） | bring-up 早期、量產 firmware |
| **Semihost** | 慢（ms/call，OpenOCD 走 SWD） | 已有 SWD | 是 | bring-up、debug、CI 測試 |
| **ITM** | 快（ns/char） | SWO 1 線 | 否（FIFO 滿丟） | development debug、profile |
| **RTT** | 快 | 無（SWD 走訊） | 否 | J-Link 用戶、嵌入式 IDE |

實務組合：開發時 ITM/RTT，量產韌體留 UART log 給 field debug，CI 用 QEMU + semihost。

## 自我檢核

- [ ] 我能寫一個 ITM init function（解鎖 LAR、配 TPIU、開 ITM）
- [ ] 我能用 ITM_PutChar 替換 newlib `_write`
- [ ] 我能列出 ITM 32 port 的常見分配
- [ ] 我能解釋 DWT exception trace 怎麼自動進 ITM
- [ ] 我能比較 SWO 與 ETB 各自的 bandwidth / 容量
- [ ] 我能比較 UART printf / semihost / ITM / RTT 四種方案

下一章看 GDB Python — 寫 pretty-printer、stack walk hook、frame filter，把 GDB 變成你的 debug 助手。

→ [Ch 30 GDB Python 進階用法](./30-gdb-python.md)
