# Ch 20 — 協定 fuzzing 實戰

> **目標**: 把 Ch 16–19 的工具和技巧整合成一條可操作的端對端工作流。用一個有真實漏洞的 toy DNS server 走完「選工具 → 建 seed → 插樁 → 跑 fuzzer → triage → 確認 bug」六個步驟，讓你下次面對陌生協定時有可以直接套用的決策框架。

---

## 為什麼需要這章

Ch 16 說明了 stateful protocol fuzzing 為什麼困難，Ch 17 教了 AFLNet，Ch 18 教了 StateAFL，Ch 19 教了用 desock + persistent mode 把速度拉起來。每一章都是點，這章把點連成線。

真實的 CVE 發現過程不是「套上 fuzzer 等崩潰」，而是一系列決策：這個協定有沒有狀態？要不要用 desock？seed 怎麼弄？跑出三千個 crash 接下來怎麼辦？每個問題都有陷阱，踩過才知道。

---

## 先建立直覺

面對一個新的協定目標，第一步不是開 fuzzer，而是走這棵決策樹：

```
協定 fuzzing 目標
├── 有源碼？
│   ├── YES → 插樁 (afl-clang-fast) + ASAN + UBSan
│   │   ├── 狀態複雜？ (多 phase、伺服器記憶客戶端歷史)
│   │   │   └── YES → AFLNet (text) 或 StateAFL (binary)
│   │   └── 可以抽出 message handler 函數？
│   │       └── YES → in-memory harness (Ch 19，最快)
│   └── NO  → binary-only (snapshot fuzzing / QEMU mode)
│                (Ch 29 / Ch 34 韌體場景)
│
├── 協定格式
│   ├── Binary → StateAFL (記憶體狀態擷取) 或 自訂 M2S
│   └── Text   → AFLNet (HTTP 200、FTP 220 直接當 state label)
│
└── 速度需求
    ├── 高 (> 5000 exec/s 目標)
    │   └── desock + persistent mode (Ch 19)
    └── 可接受 ~500 exec/s
        └── AFLNet / afl-fuzz + preeny 直接跑
```

**判斷案例：DNS**
- 有源碼（toy server）→ 插樁
- DNS 是 stateless（單一 request / response，無 session 狀態）→ 不需要 AFLNet，用 afl++ + preeny 即可
- Binary 格式 → 手工或 scapy 造 seed
- 速度夠 → 直接 afl-fuzz

強行對 stateless 協定用 AFLNet，唯一的效果是讓你的 campaign 更慢、更難除錯。

---

## 核心概念

### 目標：一個有真實漏洞的 toy DNS server

下面這個 C 程式刻意埋了一個 RFC 違規導致的 stack buffer overflow：當 QDCOUNT > 0 且第一個 query name 的 label 長度超過 63 bytes（RFC 1035 規定上限），`parse_label()` 會把它 `memcpy` 進 64-byte 的棧緩衝區而不檢查長度。

```c
/* dns_server.c — toy DNS server，刻意含 stack BOF */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* DNS header：12 bytes fixed */
typedef struct {
    uint16_t txid;
    uint16_t flags;
    uint16_t qdcount;
    uint16_t ancount;
    uint16_t nscount;
    uint16_t arcount;
} __attribute__((packed)) dns_hdr_t;

/* 漏洞函數：把第一個 label 拷進固定大小的棧緩衝區 */
static int parse_label(const uint8_t *buf, size_t len, size_t off)
{
    char label[64];           /* 最多 63 chars + NUL，RFC 1035 */
    uint8_t label_len = buf[off];
    if (off + 1 + label_len > len) return -1;

    /* BUG: 未驗證 label_len <= 63，直接拷貝 */
    memcpy(label, buf + off + 1, label_len);
    label[label_len] = '\0';
    fprintf(stderr, "[dns] label: %s\n", label);
    return 0;
}

int main(void)
{
    uint8_t buf[512];
    ssize_t n = read(STDIN_FILENO, buf, sizeof(buf));
    if (n < (ssize_t)sizeof(dns_hdr_t)) {
        fprintf(stderr, "too short\n");
        return 1;
    }

    dns_hdr_t *hdr = (dns_hdr_t *)buf;
    uint16_t qdcount = ntohs(hdr->qdcount);
    fprintf(stderr, "[dns] txid=%04x flags=%04x qdcount=%u\n",
            ntohs(hdr->txid), ntohs(hdr->flags), qdcount);

    if (qdcount > 0) {
        size_t off = sizeof(dns_hdr_t);
        if (parse_label(buf, (size_t)n, off) != 0) {
            fprintf(stderr, "parse error\n");
            return 1;
        }
    }
    return 0;
}
```

從 `stdin` 讀輸入是刻意設計的，這樣 preeny 不需要；`afl-fuzz` 預設就把 mutation 餵給 stdin。

### Step 1：理解協定

DNS message 格式（RFC 1035）：

```
Bytes  意義
0-1    Transaction ID（隨機，client 選）
2-3    Flags（QR | Opcode | AA | TC | RD | RA | Z | RCODE）
4-5    QDCOUNT（question 數量）
6-7    ANCOUNT
8-9    NSCOUNT
10-11  ARCOUNT
12+    Question section（variable）
```

Question section 的第一個欄位是 domain name，以 label 序列編碼：

```
0x07  e x a m p l e      ← length byte + 7 chars
0x03  c o m              ← length byte + 3 chars
0x00                     ← 終止符（root label）
0x00 0x01                ← QTYPE = A
0x00 0x01                ← QCLASS = IN
```

RFC 1035 Section 2.3.4 規定 label 最大 63 bytes，label_len byte 的高 2 bits 若為 11 表示 compression pointer（不是長度）。Toy server 沒有實作這個檢查。

### Step 2：建立 seed corpus

**方法 A：捕獲真實流量**

```bash
# 在 lo 上跑一個 real DNS query 並捕捉
sudo tcpdump -w /tmp/dns_cap.pcap -i lo udp port 5353 &
dig @127.0.0.1 -p 5353 example.com
sudo kill %1

# 從 pcap 提取 DNS payload（去掉 UDP/IP/Ethernet 頭）
python3 << 'EOF'
from scapy.all import rdpcap, DNS, UDP
pkts = rdpcap('/tmp/dns_cap.pcap')
i = 0
for p in pkts:
    if p.haslayer(DNS) and p.haslayer(UDP) and p[UDP].dport == 5353:
        payload = bytes(p[DNS])
        fname = f'seeds/dns_{i:02d}.bin'
        open(fname, 'wb').write(payload)
        print(f'wrote {fname}: {len(payload)} bytes')
        i += 1
EOF
```

**方法 B：手工造最小有效 query**

29 bytes，每個 byte 都有意義：

```
AB CD   txid
01 00   flags: QR=0, RD=1
00 01   qdcount=1
00 00   ancount=0
00 00   nscount=0
00 00   arcount=0
07      label_len=7
65 78 61 6d 70 6c 65   "example"
03      label_len=3
63 6f 6d               "com"
00      end of name
00 01   QTYPE A
00 01   QCLASS IN
```

```bash
mkdir -p seeds
python3 -c "
import struct
hdr = struct.pack('!HHHHHH', 0xABCD, 0x0100, 1, 0, 0, 0)
qname = b'\\x07example\\x03com\\x00'
q = struct.pack('!HH', 1, 1)   # QTYPE A, QCLASS IN
open('seeds/min_query.bin', 'wb').write(hdr + qname + q)
"
hexdump -C seeds/min_query.bin
```

預期輸出：
```
00000000  ab cd 01 00 00 01 00 00  00 00 00 00 07 65 78 61  |.............exa|
00000010  6d 70 6c 65 03 63 6f 6d  00 00 01 00 01           |mple.com.....|
```

### Step 3：插樁編譯

```bash
# 安裝 AFL++（假設已在 PATH）
CC=afl-clang-fast \
  CFLAGS="-fsanitize=address,undefined -g -O1" \
  gcc dns_server.c -o dns_server_asan

# 驗證 seed 能跑通（不崩潰）
cat seeds/min_query.bin | ./dns_server_asan
# 應該看到: [dns] txid=abcd flags=0100 qdcount=1
#            [dns] label: example
```

### Step 4：跑 fuzzer

```bash
mkdir -p out

afl-fuzz \
  -i seeds/ \
  -o out/ \
  -m 256 \
  -- ./dns_server_asan

# 想要快一點：用 persistent mode 包住 main 邏輯
# （參考 Ch 19 的 __AFL_LOOP 範例，把 read() 和 parse 放進去）
```

一個 ASAN + afl-clang-fast 的 toy server，在普通筆電上可以跑到 8000–15000 exec/s。DNS 格式很緊湊，fuzzer 通常幾分鐘內就能找到 crash。

### Step 5：triage crashes

```bash
# 先去重 crash
afl-cmin -i out/crashes/ -o crashes_dedup/ -- ./dns_server_asan

# 看每個 crash 的覆蓋差異
for f in crashes_dedup/*; do
  echo "=== $f ==="
  afl-showmap -q -o /tmp/cov.map -- sh -c "cat '$f' | ./dns_server_asan 2>&1" | head -5
done

# 跑 ASAN 取得完整 stack trace
for f in crashes_dedup/*; do
  echo "=== $(basename $f) ===" >> triage.txt
  cat "$f" | ./dns_server_asan 2>> triage.txt
  echo "---" >> triage.txt
done
grep -A 20 "ERROR: AddressSanitizer" triage.txt | head -60
```

預期看到的 ASAN 輸出（stack-buffer-overflow）：

```
=================================================================
==12345==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x...
WRITE of size 128 at 0x... thread T0
    #0 0x... in parse_label dns_server.c:21
    #1 0x... in main dns_server.c:41
...
SUMMARY: AddressSanitizer: stack-buffer-overflow dns_server.c:21 in parse_label
```

第 21 行正是 `memcpy(label, buf + off + 1, label_len);`，WRITE of size 128 表示 fuzzer 送了 label_len=128 的封包，overflow 了 64-byte 的 `label` 緩衝區。

### Step 6：重現並確認

```bash
# 找到觸發 crash 的最小輸入
afl-tmin -i out/crashes/id:000000,* -o crash_min.bin -- ./dns_server_asan

# 確認重現
cat crash_min.bin | ./dns_server_asan
# → ASAN: stack-buffer-overflow

# 用 gdb 確認控制流
cat crash_min.bin | gdb -q -ex run -ex bt --args ./dns_server_asan
```

到這裡你拿到了：crash input + ASAN stack trace + gdb backtrace。這三樣就是 CVE 報告的核心技術部分。

---

## 底層機制

整條 campaign pipeline 的資料流：

```
RFC 1035 / source code
        │ 理解格式與狀態
        ▼
  Seed creation
  ┌─────────────────────────────┐
  │ tcpdump capture → scapy    │
  │ 或 手工造 29-byte binary   │
  └──────────────┬──────────────┘
                 │ seeds/
                 ▼
  Build (afl-clang-fast + ASAN + UBSan)
        │ dns_server_asan
        ▼
  Harness 選擇
  ┌──────────────────────────────────────┐
  │ stdin (toy server，本章)             │
  │ preeny desock (真實 UDP server)      │
  │ in-memory harness (只測 parser)      │
  └───────────────┬──────────────────────┘
                  │
                  ▼
    afl-fuzz / AFLNet / StateAFL
    （依協定狀態複雜度選工具）
                  │ out/crashes/
                  ▼
        Triage pipeline
        ┌──────────────────────────────┐
        │ afl-cmin 去重                │
        │ afl-showmap 差異化           │
        │ ASAN stack trace 分類        │
        │ afl-tmin 最小化              │
        └───────────────┬──────────────┘
                        │ unique crashes
                        ▼
        Root cause analysis
        ┌──────────────────────────────┐
        │ gdb + ASAN 定位漏洞行       │
        │ 判斷嚴重度（W/R, heap/stack)│
        │ 確認可控性 (offset, size)    │
        └───────────────┬──────────────┘
                        │
                        ▼
        PoC 最小化 → CVE 報告 / CTF flag
        （完整流程見 Ch 47）
```

**速度瓶頸分析**：對 stdin 型 harness，exec 速度受限於 fork() 開銷（每次 fork 約 1–2ms）。如果目標是一個真實的 UDP server（需要 preeny desock），速度會掉到 500–2000 exec/s，這時就要考慮 Ch 19 的 persistent mode 技巧。

---

## 進階用法

### 多個 parser 並行 fuzzing

大型 DNS 實作（如 dnsmasq、BIND、PowerDNS）有多個 message type 的 handler。可以針對每種 record type（A, AAAA, MX, CNAME, TXT）分別維護一個 seed subdirectory，然後用 `-M` / `-S` 跑多個 afl-fuzz 實例：

```bash
afl-fuzz -i seeds/a_records/   -o out/ -M master -- ./dns_server_asan
afl-fuzz -i seeds/txt_records/ -o out/ -S slave1 -- ./dns_server_asan
afl-fuzz -i seeds/mx_records/  -o out/ -S slave2 -- ./dns_server_asan
```

Master 做 deterministic mutation，Slave 做 random mutation，三個 instance 共享 out/queue/。

### 轉換成 in-memory harness

對 DNS 這類 stateless 協定，把 parser 抽成函數再用 `__AFL_LOOP` 包起來，速度可以從 10000 exec/s 拉到 50000+ exec/s：

```c
/* harness_inmem.c */
#include <stdint.h>
#include <string.h>

extern int parse_dns(const uint8_t *buf, size_t len);  /* 從 dns_server.c 抽出 */

int main(void) {
    uint8_t buf[512];
    while (__AFL_LOOP(10000)) {
        ssize_t n = read(0, buf, sizeof(buf));
        if (n > 0) parse_dns(buf, (size_t)n);
    }
}
```

### 對真實 dnsmasq 的 desock 流程

```bash
# 用 preeny desock 把 UDP socket 重定向到 stdin/stdout
LD_PRELOAD=/path/to/preeny/lib/desock.so \
  afl-fuzz -i seeds/ -o out/ -- \
  /usr/sbin/dnsmasq --no-daemon --port=0
```

注意：dnsmasq 有自己的 config parsing 和 daemon 初始化，需要提供最小化的 config 讓它不試圖 fork 或 drop privileges。通常還需要配合 `-Q` 讓它只跑一次就退出。

---

## 對比取捨

| 協定        | 狀態複雜度 | 格式     | 推薦工具                     | Seed 建立難度       |
|-------------|------------|----------|------------------------------|---------------------|
| DNS         | 無狀態     | Binary   | afl++ + stdin/preeny         | 低（29 bytes 足夠） |
| HTTP/1.1    | 弱狀態     | Text     | AFLNet（靠 200/4xx 做 M2S）  | 低（curl 捕捉）     |
| FTP         | 強狀態     | Text     | AFLNet（PASV/STOR 序列）     | 中（需多 round trip）|
| MQTT        | Pub/sub    | Binary   | StateAFL 或自訂 M2S          | 高（需 broker 配合）|
| SMB3        | 複雜協商   | Binary   | StateAFL + impacket seed     | 高（多 round trip） |

DNS 是這張表裡最友善的：格式緊湊、無狀態、可以手工造 seed。用它練習 triage workflow 比用 FTP 或 MQTT 更有效率。

---

## 踩雷

- **錯誤直覺：「DNS 沒有狀態，AFLNet 沒用，但 StateAFL 的記憶體 snapshot 可以補這個缺點。」**
  正確認知：DNS 無狀態意味著 stateful fuzzer 對它毫無優勢，反而帶來額外的 overhead 和配置複雜度。選 afl++ + preeny，配置最少、速度最快、debug 最簡單。把 stateful fuzzer 留給真正需要它的目標（FTP、MQTT、Kerberos）。

- **錯誤直覺：「crash 越多代表 fuzzer 效果越好。」**
  正確認知：未去重的幾千個 crash 可能只對應 2–3 個 unique bug，差別只是觸發偏移量或 payload 長度不同。評估 campaign 效果要看 `afl-cmin` 後的 unique crash 數量，以及 unique coverage map（`afl-showmap` diff）。Crash 數量本身沒有意義。

- **錯誤直覺：「seed 越接近真實流量越好，所以直接 dump 整個 pcap 當 seed。」**
  正確認知：一個完整的 pcap session 可能包含幾千個封包，把它整體當 seed 會讓 fuzzer 的 mutation 空間過大，大多數 mutation 只能改到 session 尾段而進不了深層邏輯。正確做法是提取單一 message 的 payload，然後選「剛好通過格式驗證、能到達目標 handler」的最小化版本。一個 29-byte 的 DNS query seed 比一個 10KB pcap dump 更有用。

- **錯誤直覺：「ASAN 說 WRITE of size X 就代表可以任意寫 X bytes。」**
  正確認知：ASAN 報告的是「這次觸發 overflow 的大小」，不是「攻擊者可控制的最大大小」。要判斷可利用性，需要看 label_len byte 是否完全由輸入控制（是）、overflow 是否蓋到 return address（需要計算棧佈局）、目標平台是否有 stack canary（GCC 預設開 `-fstack-protector-strong`）。ASAN 的 stack trace 是起點，不是終點。

---

## 進階延伸

**1. 自動化 triage pipeline**

用 `clusterfuzz-tools` 或自己寫 Python 腳本，把 ASAN output 的 stack hash（取前三幀做 sha256）當作 unique crash 的 key，自動去重並按嚴重度分類。這是大規模 fuzzing infrastructure 的標準配備。

**2. Corpus distillation 跨 campaign 複用**

跑完 DNS server A 的 campaign 之後，用 `afl-cmin` 精煉出的 corpus 可以作為 DNS server B 的初始 seed，因為兩者使用相同的格式。這在 fuzzing 同一個協定的不同實作時特別有效（例如 dnsmasq vs BIND vs Unbound）。

**3. 結構感知 mutation**

AFL++ 有 custom mutator API，可以掛一個知道 DNS 格式的 mutator（例如用 `radamsa` 或自己寫一個 AFL++ custom mutator，保持 QDCOUNT 和實際 question 數量一致）。對格式驗證嚴格的協定（如 QUIC、TLS），結構感知 mutation 可以把有效 exec 比例從 1% 拉到 30%+。

**4. 從 crash 到 CVE**

確認 crash 之後的流程（severity scoring、CVSS 計算、vendor disclosure、embargo 期間）在 Ch 47 詳述。這裡只強調一件事：在 disclosure 之前要能提供可靠的 PoC，而 `afl-tmin` 最小化的 crash input 就是最好的 PoC 起點。

---

## 動手練習

1. 編譯 `dns_server.c`（不加 ASAN），用 `seeds/min_query.bin` 確認正常執行。再加上 `-fsanitize=address` 重新編譯，確認 seed 不觸發 ASAN。

2. 手工造一個觸發 stack overflow 的 payload：修改 `min_query.bin`，把第 12 個 byte（第一個 label_len）改成 0x80（128），然後補 128 個 'A' bytes，再補 NUL terminator 和 QTYPE/QCLASS。確認 ASAN 報 stack-buffer-overflow。

3. 用 `afl-fuzz` 跑一個 5 分鐘的完整 campaign，用 `afl-cmin` 去重 crashes，用 `afl-tmin` 最小化第一個 crash，最後用 gdb 看 backtrace。把 ASAN 的 crash summary 那行截圖或複製下來。

4. （選做）修 `parse_label()` 的 bug（加一行 `if (label_len > 63) return -1;`），重新編譯，確認同樣的 crash input 不再觸發，然後再跑一次 fuzzer 看 crash 數量是否歸零。

---

## 本章重點

- 面對新協定的第一步是走決策樹，確認是否需要 stateful fuzzer、使用哪種 harness。
- DNS 是 stateless 的代表性協定，最適合用 afl++ + stdin/preeny，不需要 AFLNet。
- Seed 建立優先手工造最小有效 message，而不是直接用大 pcap；最小 seed 讓 mutation 更集中。
- Crash triage 的核心是去重（stack hash）+ 嚴重度分類（WRITE vs READ, heap vs stack）+ 最小化（afl-tmin）。
- ASAN 的 stack trace 定位漏洞行，gdb 確認控制流，兩者缺一不可。
- 從 fuzzing 到 CVE 報告還有一段路（Ch 47），但乾淨可重現的 PoC 是最關鍵的起點。

---

## 自我檢核

- [ ] 我能說出為什麼 DNS 不需要 AFLNet，並給出兩個反例協定。
- [ ] 我能手工造一個合法的 DNS query binary（不查文件）。
- [ ] 我知道 `afl-cmin`、`afl-showmap`、`afl-tmin` 各自的用途有什麼不同。
- [ ] 我能從 ASAN 的 stack-buffer-overflow 輸出中，找出漏洞所在的函數和行號。
- [ ] 我能解釋為什麼 "crash 數量" 不是衡量 fuzzing 效果的好指標。
- [ ] 我知道 persistent mode 在什麼情況下值得加，以及大概可以帶來多少速度提升。
- [ ] 我能描述「seed → fuzzer → crash → triage → PoC」這條流水線的每個步驟。

---

## 延伸閱讀

1. **AFLNet: A Greybox Fuzzer for Network Protocols（ICST 2021，Van-Thuan Pham 等）**
   ICST 2021 論文原文（arXiv:2004.13897）。第 3–4 節詳述 M2S 函數的設計原則，第 5 節的評估數據可以幫你判斷 AFLNet 在不同協定上的實際效益，避免在不適合的場景強行使用。

2. **"Evaluating Fuzz Testing"（NDSS 2018，Klees 等）**
   評估方法論論文。重點在第 4 節（什麼是正確的 unique crash 計數方式）和第 5 節（coverage 指標的選擇）。讀完之後你會改變對「crash 數量」的看法，也會更嚴謹地設計 campaign 的評估方式。

3. **AddressSanitizer: A Fast Address Sanity Checker（USENIX ATC 2012，Serebryany 等）**
   ASAN 原始論文。第 2 節解釋 shadow memory 機制（理解 ASAN 為什麼對 stack 和 heap 分別用不同的 poison pattern），第 3 節解釋 use-after-free 和 heap overflow 的偵測原理。讀完你能解讀任何 ASAN 輸出，而不是只看 summary 行。

4. **Wireshark 官方 Capture File Format Documentation（wireshark.org/docs）**
   若要從 pcap 手工提取 DNS payload（不用 scapy），這份文件說明了 pcap 的 global header 和 packet record 結構（magic number、linktype、incl_len/orig_len 欄位）。對 binary-only 場景下手工解析 capture 特別有用。

---

協定 fuzzing 是一條需要不斷重複走的流程，每個新目標都會在決策樹的某個節點給你一個新的問題。這章的框架不是答案，而是讓你問出正確問題的地圖。

→ [練習 C](./practice-c-stateful-daemon.md)
