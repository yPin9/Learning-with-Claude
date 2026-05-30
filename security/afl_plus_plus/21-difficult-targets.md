# Ch 21 — 困難 Target：Network Service、Closed-Source Binary、Kernel Module

> **目標**：對 network service、closed-source binary、kernel module 三種難 target 知道各自的 fuzzing 策略和工具選擇。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

AFL++ 的設計假設是：target 從 stdin 或檔案讀 input，跑完就結束，每次執行獨立。這個假設在 `file(1)`、`libpng`、`binutils` 上完全成立。

但現實世界有大量的 target 不符合這個模型：

- **Network service**：等待 TCP 連線，有 session state，跑完不結束。
- **Closed-source binary**：沒有 source code，無法用 `afl-clang-lto` 插樁。
- **Kernel module**：跑在 ring 0，crash 等於機器死機，沒有進程隔離。

這三類 target 佔了現實世界大量高價值的攻擊面——Web server、PDF reader、驅動程式。
跳過這些等於把最有趣的部分留給對手。

---

## 先建立直覺

把 AFL++ 想成一個工廠流水線：

```
[input file] → [fork target] → [target 讀 stdin/file] → [coverage bitmap] → [next mutation]
```

三種難 target 各自打破了這條流水線的一個環節：

- **Network service** 打破「target 讀 stdin/file」—— 它要的是 TCP socket，不是 stdin。
- **Closed-source binary** 打破「coverage bitmap」—— 沒有插樁就沒有 bitmap。
- **Kernel module** 打破「fork target」—— 你沒辦法 `fork()` 一個核心。

解法就是各別修復這個破掉的環節，其他部分盡量維持 AFL++ 的原有機制。

---

## 橫向連結

- **Ch 8（QEMU / Frida mode）**：這是 closed-source binary 的基礎工具，本章是進階應用。
- **Ch 15（CmpLog / REDQUEEN）**：checksum bypass 在難 target 上同樣需要。
- **Ch 13（Dictionary）**：network protocol 的 message format 可以直接做成 dictionary。

---

## Network Service Fuzzing

### 為什麼標準流程不能用

一個典型的 echo server：

```c
int sock = socket(AF_INET, SOCK_STREAM, 0);
bind(sock, &addr, sizeof(addr));
listen(sock, 5);
int client = accept(sock, NULL, NULL);  // 等待連線
recv(client, buf, sizeof(buf), 0);      // 從 socket 讀
// 處理 buf...
```

AFL++ 把 input 寫進 `/tmp/afl_input`，然後執行 `./target @@`。
Target 完全不管這個檔案——它在等 `accept()` 傳回的 socket fd。
AFL++ 和 target 之間沒有 input 管道，fuzzing 永遠無法開始。

除此之外，network service 還有 **session state** 的問題：
每次 iteration 不 reset state，fuzzer 看到的 coverage 是 session 累積的，不是單次 input 的。
這會讓 AFL++ 的 coverage bitmap 出現假陽性（以為探索了新路徑，其實是上次跑的殘留 state）。

### 方案 A：Desocket（patch source）

最乾淨的方案。把 `accept()`/`recv()` 替換成 `read(STDIN_FILENO, ...)`。

```c
// 原本的程式碼
int client = accept(sock, NULL, NULL);
ssize_t n = recv(client, buf, sizeof(buf), 0);

// patch 後
// int client = accept(sock, NULL, NULL);  // 刪掉
ssize_t n = read(STDIN_FILENO, buf, sizeof(buf));
```

Patch 完用 `afl-clang-lto` 重新編譯，就能直接用 AFL++ 的標準流程。

優點：改動最少，coverage 最精準，可以用所有 AFL++ 的 instrumentation 選項。
缺點：需要 source code，patch 工作量依 target 複雜度而定，每次 upstream update 要重新 patch。

**State reset**：每次 iteration 程式要從乾淨狀態開始。對大多數 single-connection server，`fork()` 本身就夠了（fork server 模式）。
對有全域狀態的 server（TLS session cache、連線池），要在 `__AFL_INIT()` 之前顯式清空。

### 方案 B：AFL-Net（stateful protocol fuzzing）

AFL-Net（Pham et al., ICSE 2020）是專門設計給 stateful network protocol 的 AFL 衍生工具。

```
                    ┌─────────────┐
 AFL-Net mutation   │  message M1 │──→ server response R1
                    │  message M2 │──→ server response R2 (state 已改變)
                    │  message M3 │──→ crash!
                    └─────────────┘
         ↑
    AFL-Net 把一個 session 作為一個 unit 進行變異
```

AFL-Net 的關鍵設計：
- **Region-based mutation**：把整個 TCP session 切成多個 message，對每個 message 獨立變異。
- **Response code-based state inference**：根據 server 的回應 code（HTTP 200/400 等）推斷 state machine。
- **Selective region mutation**：focus 在最近讓 state 轉移的 message。

```bash
# 安裝 AFL-Net
git clone https://github.com/aflnet/aflnet
cd aflnet && make

# 用 AFL-Net fuzz FTP server
afl-net -i seed_dir/ -o out/ -N tcp://127.0.0.1/21 \
        -P FTP -D 10000 -q 3 -s 3 -E -K \
        ./proftpd -n -c /etc/proftpd.conf
```

`-P FTP`：指定 protocol（AFL-Net 內建 FTP、HTTP、RTSP、DNS 等 parser）
`-N tcp://127.0.0.1/21`：target 的 network endpoint
`-D 10000`：等待 server 啟動的 delay（microseconds）

### 方案 C：Preeny（LD_PRELOAD hook）

Preeny 是一組 LD_PRELOAD 函式庫，最核心的是 `desock.so`：把 `socket()`/`accept()`/`connect()` 替換成操作 stdin/stdout 的版本。

```bash
git clone https://github.com/zardus/preeny
cd preeny && make

# 用 preeny 讓 echo server 從 stdin 讀
LD_PRELOAD=/path/to/preeny/x86_64-linux-gnu/desock.so \
    afl-fuzz -i seeds/ -o out/ -- ./echo_server
```

不需要 source code，不需要重新編譯。
適合快速驗證一個 binary 能不能被 fuzz，或是不能修改 source 的情況。

限制：只適合「一問一答」的 single-connection protocol。multi-connection 或有複雜 socket management 的 server 需要更複雜的 hook。

---

## 底層機制：Desocket 的 LD_PRELOAD 工作原理

```
正常執行路徑：
  target → glibc accept() → kernel → TCP socket fd

LD_PRELOAD 路徑：
  target → preeny desock.so → 攔截！
                │
                ▼
         fd 0 (stdin) 當作 "client socket" 回傳
                │
  target 以為拿到了 client fd，
  對著 stdin 呼叫 recv()/read()
                │
  AFL++ 把 input 寫進 stdin ──→ target 讀到
```

LD_PRELOAD 的載入順序：
```
1. kernel execve() 載入 dynamic linker (ld.so)
2. ld.so 解析 LD_PRELOAD 環境變數
3. 優先載入 preeny/desock.so
4. desock.so 的 accept() 符號覆蓋 glibc 的 accept()
5. target 呼叫 accept() → 拿到 desock.so 的版本
```

這個機制對 statically linked binary 完全無效——沒有 dynamic linker，LD_PRELOAD 插不進去。

---

## Closed-Source Binary Fuzzing

### QEMU Mode（實際應用）

Ch 8 介紹了 QEMU mode 的原理。這裡補充實戰細節。

```bash
# 編譯 AFL++ 的 QEMU mode（需要先下載 QEMU）
cd AFLplusplus && make distrib

# 對 closed-source binary 使用 QEMU mode
afl-fuzz -Q -i seeds/ -o out/ -- ./closed_binary @@
```

**QEMU mode 的 coverage granularity**：以 basic block 為單位，不是 edge。
AFL++ 用 `prev_loc XOR cur_loc` 模擬 edge coverage，但 prev_loc 是 block 的 PC，不是真正的 edge。
對 highly redundant code（很多相同 block 的不同 transition），coverage accuracy 比 instrumented mode 差。

**QEMU persistent mode**：同樣可以用，但需要手動指定 entry function 的位址：

```bash
# 找到目標函式的位址（用 objdump 或 radare2）
objdump -d ./closed_binary | grep '<parse_input>:'
# 假設位址是 0x401234

AFL_QEMU_PERSISTENT_ADDR=0x401234 \
AFL_QEMU_PERSISTENT_HOOK=/path/to/hook.so \
    afl-fuzz -Q -i seeds/ -o out/ -- ./closed_binary
```

### Binary Patching：RetroWrite

RetroWrite 把 binary reassemble 成 augmented assembly，然後插入 AFL++ instrumentation。

```bash
# RetroWrite 要求 binary 有符號表（non-stripped）
# 對 PIE binary 效果最好
pip3 install retrowrite

retrowrite ./target ./target.rw.s          # 反編譯成 assembly
retrowrite -a -b ./target ./target_afl     # 直接產生 AFL-instrumented binary
```

限制：
- 只支援 x86_64 PIE binary。
- 非 PIE 或 stripped binary 效果不穩定。
- 不支援 C++ exception（LSDA table 太複雜）。

### AFL-Dyninst

Dyninst 是 binary instrumentation 框架，AFL-dyninst 用它插入 coverage 追蹤。

```bash
# 需要先安裝 Dyninst
sudo apt-get install libdyninst-dev

git clone https://github.com/vanhauser-thc/afl-dyninst
cd afl-dyninst && make

# 插樁
./afl-dyninst -i ./target -o ./target_dyninst -v

# 執行
afl-fuzz -i seeds/ -o out/ -- ./target_dyninst @@
```

AFL-Dyninst 比 QEMU mode 快（~20-30% overhead vs ~2-5x overhead），但穩定性比 QEMU 差，對某些 binary 會產生錯誤的 instrumentation。

### Frida Mode 的 Stalker API

Frida 的 Stalker 是 instruction-level tracer，AFL++ frida mode 用它做 inline hook。

```bash
# AFL++ frida mode 不需要額外安裝，build 時已包含
afl-fuzz -O -i seeds/ -o out/ -- ./closed_binary @@
# -O = frida mode
```

**精準 hook**：只對特定 module 追蹤 coverage，排除不相關的 library：

```js
// frida_hook.js
Afl.setInstrumentLibraries(["libpng.so.16"]);
// 只追蹤 libpng 的 coverage，忽略 libc、ld.so 等
```

```bash
AFL_FRIDA_INST_RANGES="0x7f000000-0x7f100000" \
    afl-fuzz -O -i seeds/ -o out/ -- ./target @@
```

Frida mode 的優勢：支援 iOS/Android binary（QEMU 不支援 ARM64 iOS binary）、支援 just-in-time instrumentation（可以在 runtime 決定要追蹤哪些 function）。

---

## Kernel Module Fuzzing

### 正確答案：用 Syzkaller

直接說結論：對 kernel module 或 syscall interface，**用 syzkaller，不要用 AFL++**。

Syzkaller 是 Google 開發、專門針對 Linux kernel syscall interface 的 fuzzer：

```
┌──────────────────────────────────────────────────────┐
│                  syzkaller 架構                       │
│                                                      │
│  syz-manager（host）                                  │
│      ↕  SSH + SCP                                    │
│  syz-executor（guest VM）                             │
│      ↕  共享記憶體                                    │
│  syscall 序列（自動生成）                              │
│      ↓                                               │
│  kernel（ring 0）                                     │
└──────────────────────────────────────────────────────┘
```

Syzkaller 的關鍵特性：
- **Syscall grammar**：用 syzlang 描述 syscall 的語義（參數型別、有效範圍、struct layout），產生語義正確的 syscall 序列。
- **KCOV（Kernel Coverage）**：Linux kernel 內建的 coverage 追蹤機制，不需要重新插樁。
- **VM 隔離**：每次 crash 後自動重啟 VM，不會搞壞 host。

```bash
# syzkaller 的基本設定檔（config.json）
{
    "target": "linux/amd64",
    "http": "127.0.0.1:56741",
    "workdir": "/path/to/syzkaller/workdir",
    "kernel_obj": "/path/to/linux/build",
    "image": "/path/to/vm_image.img",
    "sshkey": "/path/to/ssh_key",
    "syzkaller": "/path/to/syzkaller",
    "procs": 8,
    "type": "qemu",
    "vm": {
        "count": 4,
        "kernel": "/path/to/bzImage",
        "cpu": 2,
        "mem": 2048
    }
}
```

### 非要用 AFL++：KCOV + kAFL / NYX

如果特殊原因必須用 AFL++（比如研究比較、或是有特定的 AFL++ mutation 需求），需要以下組合：

**KCOV** 是 Linux 4.6+ 的 kernel coverage 機制：

```bash
# 編譯 kernel 時開啟
CONFIG_KCOV=y
CONFIG_KCOV_ENABLE_COMPARISONS=y  # 支援 CmpLog
CONFIG_DEBUG_INFO=y
CONFIG_KASAN=y  # 開啟 AddressSanitizer
```

**kAFL / NYX**（USENIX Security 2021）是真正能在這個場景下運作的工具：

```
┌────────────────────────────────────────────────┐
│               NYX 架構                          │
│                                                 │
│  QEMU/KVM hypervisor（包含 NYX patch）           │
│  ├── 快速 snapshot/restore（比 fork() 快 10x）   │
│  ├── hypercall-based coverage 傳遞              │
│  └── 直接連接 AFL++ 的 feedback loop            │
│                                                 │
│  Guest kernel（要 fuzz 的目標）                  │
└────────────────────────────────────────────────┘
```

NYX 的核心創新：用 KVM 的硬體虛擬化做 snapshot/restore，比傳統的 process fork 快，比每次重啟 VM 快得多。

**絕對不要在 bare metal 上 fuzz kernel module**。一個 wild pointer 寫入，機器直接掛掉，甚至可能損壞硬碟。永遠在 VM 裡做 kernel fuzzing。

---

## Checksum / Magic Bytes 問題

Network protocol 和 file format 常有 checksum 欄位，fuzzer 改了 payload 但沒更新 checksum，target 在 checksum verification 就直接丟棄，連解析都沒開始。

### 解法一：CmpLog（Ch 15 的延伸）

AFL++ 的 CmpLog 可以偵測到 checksum 比較：

```
fuzzer 發送：payload=AA BB CC DD, checksum=0x1234
target 計算：checksum_of(AA BB CC DD) = 0x5678
target 比較：0x1234 == 0x5678  → CmpLog 記錄這個比較
fuzzer 收到：「有個比較，一側是 0x5678，對應 input 的 offset 4-5」
fuzzer 動作：把 input 的 offset 4-5 改成 0x5678
```

這對簡單的 CRC16/CRC32 有效，對 cryptographic checksum（SHA256、HMAC）無效——後者的計算是不可逆的。

### 解法二：Patch Checksum 函式

用 LD_PRELOAD 讓 checksum 函式永遠回傳「正確」：

```c
// checksum_bypass.c
#include <stdint.h>
#include <string.h>

// 讓 png_crc_finish() 永遠以為 checksum 正確
void png_crc_finish(void *png_ptr, uint32_t skip) {
    // 什麼都不做，不 abort
    return;
}
```

```bash
gcc -shared -fPIC -o checksum_bypass.so checksum_bypass.c
LD_PRELOAD=./checksum_bypass.so afl-fuzz -i seeds/ -o out/ -- ./target @@
```

### 解法三：Grammar-Aware Mutator

對有完整格式規範的 protocol（HTTP、DNS），用 grammar-based mutator 產生結構正確的 input，checksum 由 mutator 計算好再送進去。

AFL++ 支援 custom mutator（Ch 20 或見文件），可以掛載第三方 mutator：

```bash
AFL_CUSTOM_MUTATOR_LIBRARY=/path/to/grammar_mutator.so \
    afl-fuzz -i seeds/ -o out/ -- ./target @@
```

---

## 對比與取捨

### Closed-Source Binary 技術比較

| 技術 | Overhead | 需要 source | ASAN 支援 | 穩定性 | 適合場景 |
|------|----------|------------|-----------|--------|---------|
| QEMU mode | 2–5x 慢 | 不需要 | 不支援 | 高 | 一般 binary，快速上手 |
| Frida mode | 1.5–3x 慢 | 不需要 | 不支援 | 中 | iOS/Android，需要動態 hook |
| AFL-Dyninst | 1.2–1.5x 慢 | 不需要 | 不支援 | 中低 | 想要比 QEMU 快 |
| RetroWrite | 接近原生 | 不需要（需符號） | 可以另外加 | 中 | x86_64 PIE，有符號表 |
| Desocket + instrumented | 原生速度 | 需要 | 完整支援 | 最高 | Network service，有 source |

### Network Service Fuzzing 方案比較

| 方案 | 適合 | 不適合 | 主要限制 |
|------|------|--------|---------|
| Desocket（patch source） | 有 source，single-connection | 複雜 multi-conn server | 要維護 patch |
| AFL-Net | Stateful protocol（FTP/SMTP/RTSP） | Binary-only target | 速度慢（每次建立真實連線） |
| Preeny（LD_PRELOAD） | 快速原型，無 source | Multi-connection，static binary | 不支援複雜 socket 管理 |

---

## 踩雷集錦

1. **Network service 不 reset state，coverage 是假的**：每次 iteration 後 server 的 state 累積，AFL++ 的 bitmap 看到「新 coverage」，但其實是因為 state 不同，不是 input 不同。解法：用 fork server 確保每次 iteration 從同一個 state 開始；或在 harness 裡顯式 reset 所有全域狀態。

2. **Kernel fuzzing 在 bare metal 上跑直接讓機器掛**：kernel panic 後機器完全無法回應，需要物理重開機。一定要在 VM（QEMU + KVM）裡做 kernel fuzzing，VM crash 後 hypervisor 自動重啟。

3. **QEMU mode 比預期慢得多，卻沒有開 persistent mode**：QEMU mode 每次 fork + exec 加上 emulation overhead，execs/sec 可能只有 100-200。如果 target 有明確的 parse 函式，一定要開 `AFL_QEMU_PERSISTENT_ADDR` 的 persistent mode，可以提速 10-20x。

4. **RetroWrite 在 stripped binary 上輸出垃圾**：RetroWrite 需要符號表來正確 reassemble。用 `file ./target` 確認是否 stripped；stripped binary 要用 QEMU 或 Frida mode。

5. **LD_PRELOAD desock.so 對 statically linked binary 無效**：`ldd ./target` 如果輸出 `not a dynamic executable`，Preeny 完全沒用。這時候只能用 QEMU mode 或對 binary 做 binary patching。

---

## 進階：再往深一層

### TriforceAFL：純 kernel fuzzing

TriforceAFL 是一個把 AFL++ 和 QEMU 緊密整合的 kernel fuzzer，通過 QEMU hypercall 直接對 kernel syscall 層做 fuzzing，coverage 來自 KCOV。適合對整個 syscall 層做系統性 fuzz，而不是只針對特定 module。

### Nyx-Net：network service 的 snapshot fuzzing

NYX 的衍生版本，專門對 network server 做 hypervisor-level snapshot fuzzing。每次 iteration 不是 fork 一個 process，而是 restore 整個 VM 到一個乾淨的 snapshot。對有複雜 state 的 server（資料庫、Web server）效果比 AFL-Net 好，因為 state reset 是 100% 乾淨的。

### Grammar-based fuzzing 和 AFL++ 的整合

對有明確協定格式的 target（TLS、DNS、HTTP/2），grammar fuzzer（如 BooFuzz、Peach、Boofuzz）可以產生結構正確的 seed，AFL++ 再對這些 seed 做 coverage-guided mutation。兩者互補：grammar fuzzer 覆蓋協定的廣度，AFL++ 深挖每個 state 的深度。

---

## 動手練習

### 練習 1：Desocket 一個 TCP echo server

```c
// echo_server.c（原始版本）
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>

int main(void) {
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {AF_INET, htons(9999), {INADDR_ANY}};
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    bind(srv, (struct sockaddr*)&addr, sizeof(addr));
    listen(srv, 1);

    int cli = accept(srv, NULL, NULL);
    char buf[1024];
    ssize_t n = recv(cli, buf, sizeof(buf) - 1, 0);
    buf[n] = '\0';

    // 這裡是真正要 fuzz 的邏輯
    if (n > 3 && buf[0] == 'G' && buf[1] == 'E' && buf[2] == 'T') {
        if (n > 10 && buf[3] == ' ' && buf[4] == '/') {
            // 模擬 path traversal
            if (strstr(buf, "../../../etc/passwd") != NULL) {
                volatile char *p = (char*)0xdeadbeef;
                *p = 1;  // crash
            }
        }
    }
    close(cli);
    return 0;
}
```

任務：
1. Patch 成 desocket 版本（把 `accept()`/`recv()` 改成讀 stdin）
2. 用 `afl-clang-fast` 編譯（帶 ASAN）
3. 用 `echo -n "GET "` 作為 seed，跑 AFL++ 10 分鐘
4. 確認能觸發 crash

### 練習 2：對 closed-source binary 使用 QEMU mode

```bash
# 用不帶 debug info 的編譯模擬 closed-source
gcc -O2 -s -o target_stripped echo_server_desocket.c

# 確認已 stripped
file target_stripped  # 應看到 "stripped"

# 用 QEMU mode fuzz
AFL_SKIP_CPUFREQ=1 \
    afl-fuzz -Q -i seeds/ -o out_qemu/ -- ./target_stripped @@

# 對比：原生 instrumented 的 execs/sec
# 觀察 QEMU mode 的速度差異
```

---

## 本章重點整理

- **Network service** 三種策略：desocket（最乾淨，需 source）、AFL-Net（stateful protocol）、Preeny（快速原型）；每次 iteration 必須 reset state，否則 coverage 是假的。
- **Closed-source binary** 工具選型依 overhead 和需求：QEMU（穩定）→ Frida（動態/行動平台）→ Dyninst（速度要求高）→ RetroWrite（有符號的 PIE binary）。
- **Kernel module** 正確答案是 syzkaller；非要用 AFL++ 要用 kAFL / NYX 的 hypervisor-level snapshot，絕對不能在 bare metal 上直接跑。

---

## 自我檢核

1. 一個 HTTPS server（TLS 握手 + HTTP 請求），你選哪個 fuzzing 方案？說明理由。
2. 你拿到一個 stripped x86_64 PIE binary，沒有 source code，想要 fuzz 它。依序列出你會嘗試的三個工具，各自的優缺點是什麼？
3. AFL++ 跑 network service，`execs/sec` 只有 5，但 CPU 使用率 100%。最可能的原因是什麼？
4. `LD_PRELOAD=desock.so afl-fuzz` 啟動後 target 直接 segfault。`strace` 能看到哪些資訊幫助診斷？
5. Kernel fuzzing 為什麼不能在 bare metal 上跑？VM 隔離解決了什麼問題？

---

## 延伸閱讀

- **AFL-Net（Pham et al., ICSE 2020）**
  核心貢獻：定義 region-based mutation 和 response code-guided state inference，是第一個系統性解決 stateful protocol fuzzing 的工具。
  讀哪裡：論文 Section 3（設計）和 Section 4（實驗）；GitHub README 裡的 protocol 設定範例。
  和本章關聯：方案 B 的理論基礎；理解「為什麼每條 message 要獨立變異」。

- **NYX: Greybox Hypervisor Fuzzing Using Fast Snapshots（USENIX Security 2021）**
  核心貢獻：用 KVM snapshot/restore 實現比 fork() 快的 state reset，解決 kernel fuzzing 的兩大問題：速度和 state 乾淨度。
  讀哪裡：Section 4（snapshot 機制）和 Section 6（與 AFL++ 的整合）。
  和本章關聯：kernel fuzzing 的現代標準做法；也適用於 network service 的 state reset。

- **Syzkaller 文件（https://github.com/google/syzkaller/docs）**
  核心貢獻：完整的 syscall-oriented kernel fuzzer，syzlang grammar 是 kernel fuzzing 的業界標準。
  讀哪裡：`docs/linux/setup.md`（快速上手）；`docs/syscall_descriptions.md`（理解 syzlang）。
  和本章關聯：為什麼 kernel module fuzzing 要用 syzkaller 而不是 AFL++。

---

→ 下一章：[Ch 22 — Crash Triage](22-crash-triage.md)
