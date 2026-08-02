# Ch 1 — afl++ 的四道牆

> **目標**：精確說出 afl++ 在哪四類目標上失效、為什麼失效，以及這四道牆分別指向本課的哪個 Part。讀完之後你能對任何目標說「它卡在哪道牆、我需要什麼工具」。

---

## 為什麼要系統性地問「afl++ 打不了什麼」

很多人學完 afl++ 之後的困惑是：「我知道 coverage-guided fuzzing，我知道 mutation，我知道 forkserver——但我想打 Linux kernel 的一個驅動、想打 nginx 的 HTTP 狀態機、想打一個沒有原始碼的路由器韌體，怎麼辦？」

答案不是「換個更好的工具」，而是「先搞清楚你的目標把 afl++ 卡在哪裡」。每道牆背後都有一個結構性的原因，對應一類解法。把這個地圖建起來，後面每章就是地圖上的一個標記點。

---

## 先建立直覺：afl++ 的假設模型

afl++ 背後的運作模型非常具體——它針對一個特定形態的目標最佳化，而你必須先知道這個模型長什麼樣，才能看出它在哪裡裂縫。

```
afl++ 的理想目標模型

輸入來源         執行方式         狀態           可見性
  │                  │              │               │
  ▼                  ▼              ▼               ▼
檔案（stdin）    fork() clone      無狀態          有原始碼
      或          + exec           ─────────        或
stdin pipe        或               每次執行         插樁 binary
                  forkserver       從零開始
                  loop

具體例子：
  pdfinfo input.pdf        ← 完美目標
  xmllint --noout -         ← 完美目標
  objdump -d binary.elf     ← 完美目標
  cat input | md5sum        ← 完美目標
```

這個模型有四個隱含的假設，每個假設一旦不成立，就出現一道牆。

---

## 第一道牆：非檔案輸入介面

### 問題所在

afl++ 透過檔案或 stdin 把 mutation 送進目標。它的執行鏈是：

```
afl-fuzz
  │
  ├─ 把 mutated 輸入寫進 /tmp/.afl_xxxx
  │
  ├─ fork() → exec("target", "--input", "/tmp/.afl_xxxx")
  │            或
  │            exec("target") 然後 forkserver loop 讀 stdin
  │
  └─ 觀察 exit status + 讀 coverage bitmap
```

如果目標根本不是「吃一個檔案、做完事、退出」的形態，這條路就死了。

### 具體目標例子

**Linux kernel syscall 介面**

```
你想 fuzz 的東西：
  open("/proc/net/xt_recent", O_RDONLY)
  ioctl(fd, SIOCSIFADDR, &ifr)
  sendto(sock, buf, len, 0, &addr, sizeof(addr))

問題：
  syscall 不是「讀一個檔案」，是「進入 kernel space 執行一段邏輯」
  afl++ 不知道怎麼把 mutation 轉換成一序列的 syscall 參數
```

**網路協定伺服器（nginx、OpenSSL、mosquitto）**

```
你想 fuzz 的東西：
  client → TLS handshake → HTTP request → response

問題：
  nginx 監聽 :80/:443，輸入透過 TCP socket 進來
  不是從 stdin/file 讀
  afl++ 要先建立 TCP 連線才能把 mutation 送進去
  forkserver 模型 + TCP 有 address reuse/TIME_WAIT 問題
```

**in-process API（library 函式）**

```
你想 fuzz 的東西：
  libpng_read_image(buf, len)  ← 直接叫 API，不跑 command-line tool
  OpenSSL_EVP_Decrypt(ctx, ...)

問題：
  這些 library 不是獨立執行的 binary
  afl++ 需要有一個 binary 入口點
  你必須自己寫 harness，才能讓 afl++ 的 forkserver 插得進去
```

### 為什麼 forkserver 模型解不了

forkserver 的設計假設是：程式在 `__AFL_INIT()` 點之後進入 loop，每次接收一個輸入執行一次，然後等待下一個。這個設計讓 afl++ 不需要每次都重新 exec，省去 loader 開銷。

但 syscall/socket/in-memory API 的「輸入」本質上不是一個 blob，而是一個**事件序列**或**函式呼叫序列**。把這個序列壓縮成「一個 blob 的 bit-flip」幾乎不可能保持語意有效性。

**本牆在本課的出口**：Part 1（LibAFL 的 in-process executor）、Part 4（syzkaller 的 syscall 描述語言）

---

## 第二道牆：不可重置的狀態

### 問題所在

afl++ 假設每次執行都從相同的初始狀態開始——這是 fork() 的語意保證：子程序繼承父程序的記憶體映像，執行完退出，什麼都不留。這讓 afl++ 可以無限重跑同一個輸入，得到確定性的結果。

但很多真實目標不能被 fork/reset：

```
無法重置的狀態來源：

┌─────────────────────────────────────────────────────┐
│ kernel 全域狀態                                      │
│   fs inode cache、TCP connection table、             │
│   device 驅動 state machine                         │
│   → fork 不會 reset kernel                          │
├─────────────────────────────────────────────────────┤
│ 長生命週期 daemon                                    │
│   資料庫（PostgreSQL、Redis）、                      │
│   message broker（RabbitMQ）                        │
│   → 在 connection n 時的狀態依賴 connection n-1     │
├─────────────────────────────────────────────────────┤
│ 硬體/嵌入式設備                                      │
│   MCU 的 peripheral state、                         │
│   FPGA 的 register state                            │
│   → 根本沒有 fork() 這種操作                        │
└─────────────────────────────────────────────────────┘
```

### 具體目標例子

**OpenSSH sshd**

```
情境：你想 fuzz sshd 的 pre-auth 路徑（已知有 CVE 在這裡）

sshd 的執行模型：
  1. 主 process 監聽 :22
  2. 收到連線 → fork() 一個子 process 處理這個 session
  3. 子 process 有自己的 user state（身份、session key）

問題：
  fork 子 process 的時間點在連線建立之後，不是 afl++ 控制的
  每個 fuzzing 迭代都需要一個新的 TCP 連線，有 OS-level 的 TIME_WAIT 延遲
  session 的密鑰協商狀態無法被 afl++ 的 mutation 直接注入
```

**stateful 協定（MQTT broker）**

```
MQTT 狀態機：
  CONNECT → CONNACK → SUBSCRIBE → SUBACK → PUBLISH → ...

問題：
  broker 的 SUBSCRIBE 邏輯只有在前面的 CONNECT/CONNACK 都成功之後才能到達
  afl++ 的 mutation 是針對「單一 blob」，不知道「這個 blob 要扮演哪一步」
  直接丟 bit-flipped SUBSCRIBE 給 broker 會被拒絕在 state machine 入口
```

### 為什麼 forkserver 的 reset 語意解不了

forkserver 讓你 reset **user-space 記憶體**，但不 reset：
- kernel 的網路連線狀態（socket、TIME_WAIT）
- kernel 的 IPC 資源（semaphore、shared memory）
- daemon 的磁碟狀態（database file、log）
- 遠端設備的 hardware register

所以有些目標你根本無法產生「確定性的重複執行」，也就無法把一個 crash 的輸入重現。

**本牆在本課的出口**：Part 3（AFLNet：stateful 協定 fuzzing）、Part 5（Nyx：snapshot 整個系統狀態）

---

## 第三道牆：非本機或無源目標

### 問題所在

afl++ 需要：
1. 在本機執行目標 binary
2. 在 binary 裡插 coverage instrumentation（要嘛有源碼編譯時插，要嘛 QEMU/Frida 動態插）

當目標不在本機、或者 binary 的執行環境根本不是 x86_64 Linux，這個前提就垮掉。

### 具體目標例子

**閉源 binary（Windows DLL、路由器韌體）**

```
Netgear 路由器 httpd（ARM ELF on MIPS）：
  - 沒有源碼
  - CPU 架構是 MIPS32，不能直接跑在 x86_64
  - 依賴硬體 UART、GPIO、flash memory driver
  - afl++ 的 QEMU 模式可以模擬 MIPS 使用者空間
    但 GPIO/flash 的 MMIO 沒有模擬，程式在 init 就會死
```

**Hypervisor / VMM**

```
KVM + QEMU 虛擬機：
  你想 fuzz 的目標是 QEMU 的 device emulation 程式碼
  具體：virtio-net 的 MMIO handler

問題：
  QEMU 是一個很大的 binary，大部分 code path 跟目標無關
  你想要從 Guest VM 送 crafted I/O → 觸發 Host QEMU 的 handler
  afl++ 不知道「在 Guest 裡執行一個 I/O write」這個動作的語意
```

**kernel 本身**

```
你想 fuzz 的是 Linux kernel 的 netfilter 子系統
問題：
  kernel 執行在 Ring 0，afl++ 執行在 Ring 3
  afl++ 無法直接 exec() 一個 kernel 函式
  需要一個媒介（syscall / ioctl / /proc write）把輸入送進 kernel
  還要一個方法在 kernel 裡收集 coverage（KCOV）
```

### 為什麼 QEMU 使用者模式（afl-qemu-mode）只解了一半

afl++ 的 QEMU 模式確實可以跑沒有源碼的 ARM/MIPS binary。但它解的是「沒有插樁的 binary」問題，不解：

- 目標依賴的 peripheral / MMIO 沒有模擬
- 目標不是一個 standalone binary（是 kernel module、Hypervisor 元件）
- 目標需要特定的 runtime 環境（特定 libc 版本、特定 device driver 介面）

**本牆在本課的出口**：Part 5（Nyx：全系統 snapshot）、Part 6（Fuzzware：韌體 MMIO 建模）

---

## 第四道牆：語意有效性障礙

### 問題所在

afl++ 的 mutation 是 **bit/byte 層級**的操作：flip bits、insert bytes、crossover、dictionary substitution。這些操作對「格式自由」的輸入（如原始記憶體 blob）有效，但對「格式嚴格」的輸入幾乎無效。

```
afl++ 的 mutation 遇到結構化格式的問題：

JSON parser 的輸入：{"name":"Alice","age":30}
                                │
                flip 1 bit      │
                                ▼
                    {"name":"Alice","age":3\x7f}
                                │
                JSON parser     │
                                ▼
                    立刻噴 parse error，不跑任何商業邏輯
                    coverage 沒有增加，這個輸入被丟掉
```

這個問題在語法嚴格的格式上特別致命：

### 具體目標例子

**JavaScript 引擎（V8、SpiderMonkey）**

```
JS 的 grammar 有嚴格的 token 規則、AST 結構、語意限制
「觸發 JIT 的 type confusion」這個 bug 通常在：
  1. 一段能被 JIT 編譯的合法 JS（不能有 SyntaxError）
  2. 觸發 speculation 的特定操作序列
  3. 讓引擎做出錯誤的型別假設

afl++ 的 byte-flip 幾乎無法產生「能被 parse 的 JS + 觸發 JIT」
```

**網路協定（TLS ClientHello、DNS query）**

```
TLS ClientHello 有嚴格的 binary 格式：
  - 2 bytes version
  - 32 bytes random
  - 1 byte session_id_len
  - ... (TLV 格式)

flip 任何長度欄位 → parser 拒絕整個 handshake record
afl++ 找到的大多是 "malformed record" 路徑，不是 handshake 邏輯
```

**checksum 保護的格式（PNG、PDF、ZIP）**

```
PNG 的 chunk 格式：
  length (4B) + type (4B) + data (...) + CRC32 (4B)

flip data 裡任何 byte → CRC32 check fail → 不進 image decode 邏輯
afl++ 找的是「CRC 錯誤路徑」而不是「image decode 漏洞路徑」
```

### 為什麼 afl++ 的 dictionary 只解了一小部分

afl++ 的 `-x dict.txt` 可以讓 mutator 知道一些關鍵字（如 `{"`, `true`, `null`），但 dictionary 只告訴 mutator「哪些 token 常見」，不告訴它「token 之間的組合規則」。語法規則（grammar）才是結構化格式的核心，dictionary 解決不了。

**本牆在本課的出口**：Part 2（libprotobuf-mutator/Nautilus：結構感知 mutation）、Part 7（Fuzzilli：JS grammar-aware fuzzing）

---

## 四道牆的全景地圖

```
目標形態               卡的牆                   本課出口
────────────────────────────────────────────────────────────
syscall / ioctl        牆 1：非檔案輸入介面     Part 4 syzkaller
kernel module          牆 1 + 牆 3              Part 4 + Part 5
network server         牆 1 + 牆 2              Part 3 AFLNet
stateful daemon        牆 2：不可重置狀態       Part 3 + Part 5
closed binary          牆 3：非本機/無源         Part 6 unicorn/Fuzzware
firmware blob          牆 3：無執行環境          Part 6 Fuzzware
hypervisor / VMM       牆 3：Ring 0 + snapshot  Part 5 Nyx
JS engine              牆 4：語意有效性          Part 7 Fuzzilli
TLS/JSON parser        牆 4：格式結構            Part 2 LPM/Nautilus
checksum 格式          牆 4：格式結構            Part 2 + 客製 mutator
```

每道牆都有多個工具作為出口，但工具的選擇取決於具體目標的細節——這是整門課要教的，不是一張表能說清楚的。

---

## 底層機制：為什麼 afl++ 的 forkserver 讓這些牆更高

forkserver 是 afl++ 效能的關鍵優化：它讓目標在 `__AFL_INIT()` 點暫停，等待 afl-fuzz 的指令，收到一個新輸入就 fork 出子程序執行、拿到 exit status、再回來等待下一個。這個設計讓每次迭代省去了 exec() 的 loader 開銷，每秒可以跑幾萬次迭代。

但 forkserver 帶來了一個根本假設：**目標的全部輸入在 `fork()` 之前就能確定，執行是無副作用的（除了 exit status 和 coverage bitmap）。**

```
afl++ forkserver 生命週期

Parent process (forkserver loop)
  │
  │  fork()
  ├──────────────────────────────────►  Child process
  │                                         │
  │  寫 mutation 到 /tmp/.afl_xxx           │ 讀 /tmp/.afl_xxx
  │                                         │ 執行目標邏輯
  │                                         │ exit()
  │◄─────────────────────────────────────── │
  │  讀 coverage bitmap shm                 │ (coverage bitmap 在 shm)
  │  分析 new coverage?
  │  corpus 更新
  │  下一個 mutation
  │  fork()
  └── repeat

問題：
  牆 1 — 輸入不是 /tmp/.afl_xxx，無法 inject
  牆 2 — child exit 之後 kernel 狀態沒有 reset，下次 fork 繼承錯誤狀態
  牆 3 — 沒有可以 exec 的本機 binary
  牆 4 — mutation 不知道結構，byte-flip 破壞語法，子程序早早退出
```

---

## 對比取捨表

| 問題維度 | afl++ 的策略 | 策略限制 |
|---------|------------|---------|
| 輸入介面 | 檔案/stdin | 只適用檔案型輸入 |
| 狀態 reset | fork() | 不 reset kernel/disk/net 狀態 |
| Coverage 收集 | 編譯期插樁 | 無源碼的 binary 需要 QEMU 模式，效能差 3–5x |
| Mutation 策略 | byte-level | 無語法結構認知，高 rejection rate |
| 目標執行環境 | 本機 x86_64 | 跨架構/VM/kernel 目標需要額外媒介 |

---

## 踩雷集錦

**踩雷 1：把 harness 包起來就以為解了牆 1**

對 in-process library 的 API fuzzing，確實可以寫一個 harness binary，然後讓 afl++ 的 forkserver 插在 harness 裡——這解了 library 的輸入介面問題。但如果 library 有全域狀態（如 OpenSSL 的 EVP context、SQLite 的 database handle），forkserver 的 fork 點如果在全域初始化之後，fork 出的子程序會繼承已初始化的狀態，跟真實呼叫行為不一樣，可能導致 false positive 或者 missing bugs。

**踩雷 2：afl++ 的 QEMU 模式是「使用者空間模擬」，不是「系統模擬」**

afl-qemu-mode 跑的是 QEMU user-space emulation（`qemu-arm -L /usr/arm-linux-gnueabi`），模擬的是 CPU 指令，系統呼叫透傳到 host kernel。但韌體的 MMIO 存取（`*(uint32_t*)0x40021000 = 0x01`）在 user-space 模擬裡會直接 SIGSEGV——這是牆 3 的核心。

**踩雷 3：dictionary 解的是「token」而不是「語法」**

afl++ 的 `-x json.dict` 讓 mutator 知道 `{`、`}`、`"key"`，但這不等於 mutator 知道 `{"key": value, ...}` 的巢狀結構。有 dictionary 的 afl++ 對 JSON fuzzing 的改進是「減少 parser 的 early rejection」，不是「產生語法合法的輸入」。Coverage 增長曲線會比沒有 dictionary 好，但到達真正業務邏輯的機率還是很低。

**踩雷 4：看到 afl++ 的 corpus 在長就以為有效**

corpus 在長 = 有新 edge 被發現，但不代表那些 edge 在你關心的目標邏輯裡。對 JSON parser 的 afl++，大量 corpus 種子可能全都在「不同的 error handling 路徑」（malformed input 的各種錯誤訊息）。真正的業務邏輯（語意正確的 JSON 被 parse 之後做什麼）的 coverage 可能是零。

---

## 進階延伸

這四道牆在研究文獻裡也有對應的正式討論。三篇值得讀：

- **IJON（USENIX Security 2020）**：系統性分析了 coverage-guided fuzzing 的「annotation 問題」——用來跑遊戲 AI 的 IJON 框架揭示了「coverage bitmap 有盲點」這個更深的問題，是牆 4 的延伸思考。

- **StateAFL（RAID 2022）**：直接針對牆 2，提出在 network server 裡用 protocol state 作為 feedback 的方法。Part 3 Ch 18 會細讀。

- **Sok: The Progress, Challenges, and Perils of Firmware Security（SP 2020）**：把韌體 fuzzing 的挑戰正式分類，和牆 3 的討論對應。

---

## 動手練習

1. 選一個你熟悉的開源目標（OpenSSH、nginx、ffmpeg 都可以），分析它卡在哪幾道牆。寫下：(a) 它的輸入介面是什麼？(b) 它有沒有不可重置的狀態？(c) 有源碼嗎？(d) 輸入格式有語法嗎？

2. 對一個 JSON 程式庫（比如 `cJSON`，apt 可裝），寫一個 afl++ harness，不加任何 dictionary，跑 5 分鐘。觀察 coverage 增長曲線和 queue size。再加上 `json.dict`（從 AFL++ 的 `dictionaries/` 目錄），跑 5 分鐘，比較差異。這個差異說明了什麼？dictionary 沒解決什麼？

3. 找一個帶 CRC32 保護的檔案格式（PNG 或 ZIP），用 afl++ 跑一個對應的 parser。觀察：crash 主要在什麼路徑？跑到格式驗證成功之後的邏輯了嗎？這說明了什麼？

---

## 本章重點

- afl++ 的四道牆：非檔案輸入介面、不可重置狀態、非本機/無源目標、語意有效性障礙。
- 每道牆背後都有 forkserver 模型的結構性假設在支撐，不是「工具不夠強」的問題。
- 四道牆對應本課四個主要 Part：Part 3（狀態）、Part 4（kernel/syscall）、Part 5/6（無源/全系統）、Part 2/7（結構化格式）。
- 同一個目標可能同時碰到多道牆；分析清楚再選工具，不要看到「afl++ 跑不動」就隨機換工具。

---

## 自我檢核

不翻書回答：

- [ ] afl++ 的 forkserver 為什麼不能 reset kernel 狀態？reset 的邊界在哪裡？
- [ ] 一個 MQTT broker，它卡在哪幾道牆？每道牆的具體症狀是什麼？
- [ ] dictionary 在 afl++ 裡解決的問題和「文法 fuzzing」（Nautilus）解決的問題有什麼本質差異？
- [ ] afl++ 的 QEMU 模式（`-Q`）解決了哪道牆的哪一半？沒解決哪一半？
- [ ] 一個 x86_64 binary、沒有源碼、輸入是標準 stdin、無狀態——這個目標卡在幾道牆？

---

## 延伸閱讀

1. **[AFL++ 論文](https://www.usenix.org/conference/woot20/presentation/fioraldi)**（WOOT 2020）的 §3 和 §4——回顧 afl 的設計空間（instrumentation/mutator/scheduler），對照本章四道牆，哪些設計選擇製造了哪些限制；Section 4 的 CmpLog 和 REDQUEEN 直接對應牆 4。

2. **[StateAFL 論文](https://arxiv.org/abs/2110.06253)**（RAID 2022）的 §1 Introduction——這篇論文的 introduction 對 stateful target 的挑戰有精確的分類，可以對照本章牆 2 的討論；它提出的 in-memory protocol state inference 是 Part 3 Ch 18 的主線。

3. **[Fuzzing: Challenges and Reflections](https://ieeexplore.ieee.org/document/9130354)**（IEEE S&P Magazine 2020，Böhme et al.）——以偏哲學的角度回顧 fuzzing 的挑戰，包括 semantic validity（對應牆 4）和 oracle 問題；短文，30 分鐘讀完，適合對照本章建立全局觀。

---

知道 afl++ 的極限之後，下一步是在全局上看清楚這些工具之間的關係——它們從哪裡演化而來、按什麼維度分類、分別能解本課哪個問題。

→ [下一章：Ch 2 現代 fuzzer 全景](./02-modern-fuzzer-landscape.md)
