# 練習 B — 嵌入式情境題

> **目標**：把 Part 2（Ch 13–19）的嵌入式考點綜合——memory-mapped I/O、ISR、reentrancy、endian、volatile 放進真實的韌體情境題。這些是 MTK 韌體技術面的差異化考點，先遮答案自己想。

> **環境**：C，嵌入式。前置：Part 2 全部（尤其 Ch 13-16）。

## 怎麼用這份練習

嵌入式情境題不像 C 上機考有標準答案——面試官想看你的**思路與安全意識**（有沒有想到 volatile、原子性、reentrancy、endian）。每題先自己分析，再對照——重點是「有沒有想到那些坑」。

---

## 第一部分：找 bug / 評論（韌體最重視）

### Q1（Ch 13, 3）這段控制 GPIO 的 code 有什麼問題？

```c
#define GPIO_OUT (*(unsigned int *)0x40000000)

void set_pin_high(int pin) {
    GPIO_OUT = (1 << pin);
}
```

<details>
<summary>分析</summary>

**兩個問題**：

1. **缺 volatile**（Ch 3/13）：`(unsigned int *)` 沒有 volatile——編譯器可能最佳化掉對暫存器的寫入（認為「寫了沒人讀」），或快取——硬體控制失效。應該 `(volatile unsigned int *)`。

2. **直接賦值清掉其他 pin**（Ch 7/13）：`GPIO_OUT = (1 << pin)` 把整個暫存器設成「只有 pin 那位是 1」——其他所有 pin 都被清成 0！應該用 `GPIO_OUT |= (1u << pin)`（set bit，保留其他位）。

修正：
```c
#define GPIO_OUT (*(volatile unsigned int *)0x40000000)
void set_pin_high(int pin) { GPIO_OUT |= (1u << pin); }
```

**考點**：memory-mapped I/O 的 volatile + bit 操作（set 而非覆蓋），Ch 13/7。
</details>

### Q2（Ch 14, 3）這個 ISR 與主程式的 code 有什麼問題？

```c
int sensor_value = 0;          // ISR 會更新

void adc_isr(void) {           // ADC 轉換完成中斷
    sensor_value = ADC_DATA_REG;
}

int main(void) {
    init();
    while (sensor_value < THRESHOLD) {
        // 等感測值超過閾值
    }
    do_action();
}
```

<details>
<summary>分析</summary>

**問題：`sensor_value` 沒宣告 volatile**（Ch 3/14）。

主迴圈 `while(sensor_value < THRESHOLD)` 裡，編譯器看不到「ISR 會改 sensor_value」——可能把它讀進暫存器一次後不再讀記憶體，導致 ISR 更新了 sensor_value、主迴圈卻看不到 → 永遠卡在迴圈（即使感測值已超閾值）。

修正：`volatile int sensor_value = 0;`

延伸（若 sensor_value 是多 byte 且在 8/16-bit MCU 上）：讀它時可能不是原子的（主迴圈讀一半被 ISR 改），嚴格要關中斷讀（Ch 14/15）。但基本問題是缺 volatile。

**考點**：ISR 共享變數 + volatile，超高頻（Ch 14）。
</details>

### Q3（Ch 14）評論這個 ISR

```c
int process_packet(char *data, int len) {
    char buffer[256];
    memcpy(buffer, data, len);
    log_to_file(buffer);          // 寫檔
    return checksum(buffer, len);
}

void uart_rx_isr(void) {
    char *pkt = get_packet();
    int result = process_packet(pkt, get_len());
    printf("Processed: %d\n", result);
}
```

<details>
<summary>分析</summary>

**多個問題**（Ch 14 ISR 準則）：

1. **ISR 做太多耗時的事**：memcpy 256 bytes、寫檔（log_to_file）、計算 checksum、printf——ISR 應該「快進快出」，這些耗時操作會卡住其他中斷和主程式。
2. **ISR 呼叫 printf**：printf 慢、不可重入、可能阻塞（Ch 14/15）。
3. **ISR 呼叫 log_to_file（寫檔/I/O）**：阻塞、慢、可能不可重入。
4. **大的區域變數 `char buffer[256]`**：ISR 的 stack 通常有限，256 bytes 可能爆 ISR stack。
5. **沒檢查 len**：`memcpy(buffer, data, len)` 若 len > 256 → buffer overflow（Ch 12）。

正確做法：ISR 只快速「讀走封包、放進 queue/ring buffer、設旗標」，把 process_packet/log/checksum 留給主程式或一個 task（Ch 14/18）做。

**考點**：ISR 設計準則綜合（快進快出、不 printf/不 I/O、不耗時、stack、邊界），Ch 14。
</details>

---

## 第二部分：手寫實作

### Q4（Ch 13, 7）寫一個函式：設定位址 0x50000000 的暫存器的第 3、第 5 位為 1，清除第 7 位，不影響其他位

<details>
<summary>參考解答</summary>

```c
#define REG (*(volatile unsigned int *)0x50000000)

void config_reg(void) {
    REG |=  (1u << 3) | (1u << 5);   // set bit 3 and 5
    REG &= ~(1u << 7);               // clear bit 7
}
```

要點：volatile（Ch 13）；set 用 `|=`、clear 用 `&= ~`（Ch 7）；用 `1u`；可以一次設多位 `(1<<3)|(1<<5)`。

注意：這是 read-modify-write，若該暫存器也被中斷改，有 race（Ch 14/15）——某些硬體有 SET/CLR 暫存器避免 RMW。

**考點**：memory-mapped I/O + bit 操作，Ch 13/7。
</details>

### Q5（Ch 16）寫一個函式判斷系統 endian，並寫一個 32-bit byte swap

<details>
<summary>參考解答</summary>

```c
int is_little_endian(void) {
    int x = 1;
    return *((char *)&x) == 1;     // little: 最低位址 byte 是 1
}

unsigned int swap32(unsigned int x) {
    return ((x >> 24) & 0x000000FF)
         | ((x >> 8)  & 0x0000FF00)
         | ((x << 8)  & 0x00FF0000)
         | ((x << 24) & 0xFF000000);
}
```

endian 偵測看「int=1 的最低位址 byte」；swap32 把 4 byte 順序顛倒（移位+mask）。Ch 16。

**考點**：endian 偵測 + byte swap，Ch 16。
</details>

### Q6（Ch 15）把這個不可重入的函式改成可重入

```c
char *int_to_str(int n) {
    static char buf[16];
    sprintf(buf, "%d", n);
    return buf;
}
```

<details>
<summary>參考解答</summary>

問題：用了 `static char buf`（共享狀態）+ 回傳指向它的指標 → 不可重入（多執行流/ISR 呼叫會互相覆蓋 buf）。

改成可重入（呼叫者提供 buffer）：
```c
char *int_to_str(int n, char *buf, size_t size) {
    snprintf(buf, size, "%d", n);    // 用呼叫者的 buffer，不共享
    return buf;
}
```

要點：把狀態（buffer）從 static 改成「呼叫者傳入」——每次呼叫用自己的 buffer，不共享 → 可重入（Ch 15）。用 snprintf 限大小（防 overflow）。

**考點**：reentrant 改寫（消除共享 static），Ch 15。
</details>

---

## 第三部分：綜合情境題

### Q7（Ch 14, 15, 18）一個感測器透過中斷送資料，主程式（或 task）處理。設計 ISR 與主程式的協作，並說明同步問題

<details>
<summary>參考解答</summary>

設計（Ch 14 的「ISR 快進快出 + 主程式處理」模式）：

```c
#define BUF_SIZE 64
volatile unsigned char ring[BUF_SIZE];   // 環形緩衝
volatile int head = 0, tail = 0;          // ISR 寫 head、主程式讀 tail

// ISR：快速把資料放進 ring buffer、不做耗時處理
void sensor_isr(void) {
    unsigned char data = SENSOR_DATA_REG;     // 快速讀走（不讀會遺失）
    int next = (head + 1) % BUF_SIZE;
    if (next != tail) {                        // buffer 沒滿
        ring[head] = data;
        head = next;
    }
    SENSOR_CLEAR_IRQ();                         // 清中斷旗標（Ch 14）
}

// 主程式：處理 buffer 裡的資料（耗時的留這做）
void main_loop(void) {
    while (1) {
        if (head != tail) {                    // 有新資料
            unsigned char data = ring[tail];
            tail = (tail + 1) % BUF_SIZE;
            process(data);                     // 耗時處理在這（不在 ISR）
        } else {
            enter_sleep();                     // 沒事就睡（省電，Ch 19）
        }
    }
}
```

同步問題討論：
- **volatile**：ring、head、tail 都要 volatile（ISR 和主程式共享，Ch 3/14）。
- **ring buffer 的妙處**：ISR 只動 head、主程式只動 tail——單一生產者單一消費者下，多數情況不用鎖（各改各的指標）。這是 ISR↔主程式溝通的經典 lock-free 模式。
- **原子性**：head/tail 若是多 byte 且在窄匯流排 MCU 上，讀寫要注意原子性（Ch 15）。
- **buffer 滿/空判斷**：`head != tail` 判空、`(head+1)%SIZE != tail` 判滿（留一格區分滿/空）。
- **省電**：沒資料時 sleep（Ch 19）。

**考點**：ISR + ring buffer + volatile + 同步 + 省電的綜合設計，Part 2 集大成。
</details>

### Q8（Ch 13, 3, 7）一個狀態暫存器在 0x40001000，bit 0 是「資料就緒」、bit 1 是「錯誤」。寫一個函式：等資料就緒（且沒錯誤）就讀資料暫存器（0x40001004），有錯誤就回傳 -1

<details>
<summary>參考解答</summary>

```c
#define STATUS (*(volatile unsigned int *)0x40001000)
#define DATA   (*(volatile unsigned int *)0x40001004)
#define READY_BIT (1u << 0)
#define ERROR_BIT (1u << 1)

int read_data(void) {
    while (1) {
        unsigned int s = STATUS;            // 讀狀態（volatile→每次重讀）
        if (s & ERROR_BIT) return -1;       // 有錯誤
        if (s & READY_BIT) return (int)DATA;// 資料就緒，讀資料
        // 否則繼續等（實務上可加 sleep/timeout）
    }
}
```

要點：
- **volatile**：STATUS 必須 volatile——硬體會更新它，沒 volatile 編譯器會把 `while(1)` 裡的 STATUS 讀一次就快取 → 永遠看不到硬體更新 → 卡死（Ch 3/13，這正是 volatile 經典場景）。
- **bit test**：`s & READY_BIT`、`s & ERROR_BIT`（Ch 7）。
- **先讀進本地變數 `s`**：避免一個 if 裡多次讀 volatile STATUS（讀到不一致，Ch 3 square 陷阱）。
- 實務改進：加 timeout（別無限等）、沒就緒時 sleep（省電）。

**考點**：memory-mapped I/O + volatile + bit test + volatile 多次讀陷阱，Ch 13/3/7 綜合。
</details>

## 自評與弱點

| 題 | 章 | 考點 |
|---|---|---|
| Q1 | Ch 13,7,3 | GPIO：volatile + set bit（非覆蓋）|
| Q2 | Ch 3,14 | ISR 共享變數缺 volatile |
| Q3 | Ch 14 | ISR 設計準則（快進快出/不 printf/邊界）|
| Q4 | Ch 13,7 | 暫存器 bit 操作 |
| Q5 | Ch 16 | endian 偵測 + byte swap |
| Q6 | Ch 15 | reentrant 改寫 |
| Q7 | Ch 14,15,18,19 | ISR+ring buffer 綜合設計 |
| Q8 | Ch 13,3,7 | 暫存器輪詢 + volatile |

- **找 bug 題（Q1-3）漏掉 volatile** → Ch 3/13/14 重看，volatile 是韌體面試最高頻。
- **ISR 設計題答不全** → Ch 14 的「ISR 準則」要能列出 4+ 點。
- **綜合題（Q7-8）寫不出** → 這些最接近真實韌體技術面，把 ISR↔主程式溝通模式練熟。

## 如果你卡住了

1. **找 bug 先問三件事**：有沒有 volatile（共享/暫存器）？bit 操作是 set 還是覆蓋？ISR 有沒有做不該做的事（printf/耗時/阻塞）？
2. **ISR 題**：記住「快進快出」——ISR 只讀資料+設旗標/放 queue+清中斷，耗時的丟主程式。
3. **同步題**：共享變數 → volatile；多 byte/RMW → 原子性（關中斷/atomic）；ISR↔主程式 → ring buffer 常見。
4. **暫存器題**：volatile + 正確型別寬度 + bit 操作（set/clear/test）+ 先讀進本地變數。

## 自我檢核

- [ ] 看到操作硬體暫存器/ISR 共享變數的 code，我會反射性檢查有沒有 volatile
- [ ] 我能列出 ISR 不該做的事（printf/浮點/耗時/阻塞/不可重入函式）並說明理由
- [ ] 我能設計 ISR↔主程式的安全溝通（ring buffer + volatile + 快進快出）
- [ ] 我能寫暫存器的 bit 操作（set/clear/test）且記得 volatile 和型別寬度
- [ ] 我能判斷一個函式可不可重入、並改成可重入

Part 2（嵌入式/韌體）綜合驗收完成。Part 3 進入作業系統——process/thread、deadlock、同步、scheduling、virtual memory，技術面的另一大塊。

→ [Ch 20 process vs thread](./20-process-vs-thread.md)
