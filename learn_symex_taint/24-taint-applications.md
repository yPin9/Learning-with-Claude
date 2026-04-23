# Ch 24 — Taint 的攻擊面應用：exploit 可達性、漏洞發現

> 目標：把 DTA 放進實戰脈絡。看完你要能對一個具體安全問題 — exploit 分析、info leak、CVE reproducer — 設計 taint spec 並用工具跑。

## Use case 1：Exploit reachability

問題：某個 CVE 在某些 input 下觸發。換個 version 的軟體 / 不同 configuration，這個 input path 還到得到 vulnerable code 嗎？

這就是 **exploit reachability** — 是否從 attacker-controlled source 存在 path 到 vulnerable sink。

### 設計 taint spec

- **Source**：所有 attacker-input（command line arg、stdin、file content、network）
- **Sink**：vulnerable instruction 的 operand（overflow-prone memcpy 的 size、format string、function pointer、...）
- **Propagation**：explicit（簡單情況）或 explicit + pointer load（complex）

### 實作 flow

```
1. 拿 target binary
2. 選 DBI 工具 + libdft 類的 DTA framework
3. 標 source（在 syscall / argv parsing 處）
4. 跑 program with representative input
5. 看 sink 被 tainted：是 → reachable，否 → 不 reachable
```

例：CVE-2017-11176 (Linux kernel mq_notify UAF)。reachability 分析要追 attacker-controlled file descriptor 到 fork() 的 netlink code。DTA 可以 trace 這條 path。

### 限制

這個方法的 **sound-ness 只在動態看到的 path 上**。DTA 沒看到的 path 不知道。如果 attacker 用了其他 input pattern 激活另一條 path，這套 DTA 跑不到。

補強：搭配 fuzzing 或 symex 去 explore 更多 path。

## Use case 2：Information leak detection

問題：敏感資料（password、private key、memory layout）會不會洩漏到網路、檔案、log？

### Taint spec

- **Source**：敏感 buffer。經典方式：
  - `bpf_probe_read` 從 /etc/shadow
  - mmap private key file 的 page
  - 用 heuristic（name contains "password"、specific address range）
- **Sink**：`send()`、`sendto()`、`write()`、`fwrite()`、`syslog()` — 任何外流 API
- **Propagation**：explicit + implicit（如果關心 side channel）

### 實戰常見：Heartbleed reproducer

Heartbleed (CVE-2014-0160) 洩漏 server memory 的一段。

- source：私鑰在 memory 裡的位置
- sink：TLS heartbeat response 的 buffer write
- 跑 PoC exploit：看 taint 是不是從 key buffer 流到 heartbeat output

用 libdft / Triton 對 OpenSSL 跑一次，taint trace 直接視覺化了 Heartbleed 的 leak path。

### Side channel 擴展

純 DTA 不追 timing side channel（Spectre、Meltdown）。但有研究擴展 DTA 追 cache access pattern：

- source：secret value
- sink：cache line address
- propagation：包含 implicit flow

叫 **microarchitectural taint analysis**。非常前沿，工具還不成熟（Hardware-assisted Ctrl-Alt-Flow 2020+）。

## Use case 3：Input sanitization 驗證

Server 接受 input → 經過 sanitizer → 傳進危險 API。問：sanitizer 有涵蓋所有 path 嗎？

### Taint spec

- **Source**：external input
- **Sanitizer**：你宣告的 clean function（例如 `escape_html`）
- **Sink**：dangerous API（例如 `render_html`）
- **Policy**：走過 sanitizer 的 taint 被清除；沒走過的維持 tainted

### 實作

libdft 的 taint map 在 sanitizer 出口處清掉 output 的 taint：

```c
void sanitizer_exit(uintptr_t ret_buf, size_t len) {
    for (size_t i = 0; i < len; i++) {
        TAINT_CLEAR(ret_buf + i);
    }
}
```

DTA 跑完看哪些 path 到 sink 時 taint > 0 — 就是 sanitize 沒蓋到的漏洞。

這種用法常見在 **web security** — PHP/Python 的 DTA 主要做這個（Pysa、Suhosin）。

## Use case 4：Fuzzer feedback enhancement

AFL 的 coverage 是 edge-based。但 **如果兩個 edge 看起來一樣、但一個處理 tainted data、另一個處理 clean data**，它們對 bug 的重要性不同。

**Taint-guided fuzzing**：
- 用 DTA 標記「input 的哪些 byte 影響 code path」
- fuzzer mutation 優先改這些 byte，不浪費時間改無關 byte

代表工具：
- **TaintScope**（USENIX Sec 2010）
- **VUzzer**（NDSS 2017）
- **Angora**（S&P 2018）
- **TIFF**（NDSS 2020）

典型 speedup：2–10× 對複雜 target。

### 簡化的 spec

- Source：fuzzer input buffer（AFL 的 `input` file）
- Sink：沒固定 sink；關注 **哪些 byte 影響哪些 branch**
- 產出：每個 branch 的 "byte dependency set"
- Mutation 優先改 branch-affecting byte

要在 DTA pipeline 跟 fuzzer 之間串一個 bidirectional channel。工程大，但效果明顯。

## Use case 5：Malware analysis

malware 動態分析：觀察 **dropped file**、**C2 communication**、**injected process** 的 data flow。

### Taint spec

- **Source**：網路接收（`recv`）、dropped file 的 content
- **Sink**：execution API（`CreateProcess`、`LoadLibrary`、`shellcode jmp`）、sensitive info exfil（`send`、`keylogger buffer`）
- **Policy**：追 implicit flow（malware 常用 anti-taint technique）、追 pointer-taint load

### 經典 platform

- **PANDA**（QEMU-based）：全系統 DTA + record/replay
- **DECAF**：類似 PANDA
- **Argos**：kernel + userspace

這類工具用在 CTI（threat intel）、研究 APT。普通公司不用，因為成本大。

## Use case 6：Compliance / data governance

企業法規：某些資料（PII、PHI）必須在特定邊界內。DTA 驗證：

- Source：DB 裡的 PII 欄位
- Sink：外部 API call、log output、message queue 發送
- 目標：這些 data 不洩漏到 non-compliant 目的

這是 **enterprise taint analysis** 的應用。實務工具：
- Pysa（Meta's Python DTA）
- CodeQL（static，但同 flavor）
- 各家 cloud SAST

這些通常**靜態** taint，不是 dynamic。但思路跟 DTA 完全一致。

## 一個 hands-on 例子：寫個最小 DTA 工具抓 command injection

設計：
- Source：`read()` 到 stdin
- Sink：`system()` 的 arg
- 用 Pin + libdft 實作（或 Frida 簡化版）

Frida JS 版（最簡單起手）：

```javascript
// cmdinjection.js
const tainted_buf = new Set();

Interceptor.attach(Module.getExportByName(null, 'read'), {
    onEnter(args) {
        this.buf = args[1];
        this.count = args[2].toInt32();
    },
    onLeave(retval) {
        const n = retval.toInt32();
        for (let i = 0; i < n; i++) {
            tainted_buf.add(this.buf.add(i).toString());
        }
    }
});

Interceptor.attach(Module.getExportByName(null, 'system'), {
    onEnter(args) {
        const cmd = args[0];
        const s = cmd.readCString();
        // 簡易 check：cmd 的每個 byte 是否 tainted
        for (let i = 0; i < s.length; i++) {
            if (tainted_buf.has(cmd.add(i).toString())) {
                console.log('[!] COMMAND INJECTION ALERT at', 
                            Thread.backtrace(this.context, Backtracer.ACCURATE)[0]);
                return;
            }
        }
    }
});
```

**注意**：這個 naive 版本只追 **explicit memory taint**，沒追 register、沒追 `strcat`、沒追 memory copy。所以如果 attacker-input 經過 `sprintf(cmd, "ls %s", input)`，這個版本會漏。

要做完整：Ch 22-23 的 Pin + libdft 路子。這個 Frida 版當玩具學概念用。

## Taint 的現實角色

真實 security team 的 DTA 使用：

1. **Reproducer minimizer**：已知 exploit input 大 → 用 DTA 找最小必要 byte → 給 PoC
2. **Fuzzer enhancer**：integrate 進 AFL / libFuzzer，加速
3. **Incident response**：malware sample 的 behavior profile
4. **Patch verification**：patch 後，原 exploit 的 taint path 還在不在
5. **Red team**：exploit 可達性評估

**很少用 DTA 單打獨鬥找新 bug**。DTA 是 "augment existing tool" 的 amplifier，不是 "push-button vuln finder"。

## 常見 pitfall

- **Over-alert**：DTA 報 500 個 potential injection，實際 1 個真 bug。對策：tighten policy、增加 sanitizer list、human filter
- **Under-detect**：DTA 說 clean，實際 vulnerable。原因通常是 implicit flow、library 沒 instrument、或 syscall 穿越
- **Performance崩**：10× slowdown 對 CI 勉強、100× 就廢了。要權衡 granularity 跟 scope
- **Library function 沒 summary**：target 用了少見 library，DTA 不追，taint 消失

所有 pitfall 都回到 Ch 20 的 policy 設計 — **先花時間在 spec，再花時間寫 code**。

## 整合 fuzzer + DTA 的架構

一個現代 taint-aware fuzzer 的架構：

```
    AFL / libFuzzer
         │
         │ input
         ▼
    target (instrumented)
         │
         │ coverage
         │
         │ + DTA info
         │   (byte → affected branches)
         ▼
    mutator
         │
         │ 優先改 branch-affecting byte
         ▼
    new input → AFL
```

關鍵是 feedback loop — DTA 告訴 mutator 哪些 byte 重要。這個 architectural pattern 在 Angora、QSYM、RedQueen 都看得到。

## 心法

DTA 的應用策略：

- 不是一個 standalone tool，是個 amplifier
- 實戰少用 implicit flow，多用 explicit + selected sanitizer
- source / sink / sanitizer 是 spec，不是隨手填
- 跟 fuzzer、symex 串起來才強

寫 DTA 的 spec 時問自己：「這個 spec 如果完美實作，能抓到我今天要找的 bug 嗎？」答不出 yes，別寫 code。

## 自我檢核

- [ ] 能對三個 use case（exploit reachability、info leak、sanitization 驗證）各寫一份 taint spec
- [ ] 理解 taint-guided fuzzing 的工程 flow
- [ ] 知道為什麼 DTA 很少獨立找新 bug
- [ ] 講得出 DTA 與 static taint / fuzzer / symex 的分工
- [ ] 能對一個具體 CVE 設計 taint 追蹤策略

Part 5 結束。下個是 **練習 D**，用 Triton 實作一個真實的 taint 追蹤小工具。

→ [練習 D：用 Triton 寫 taint 追蹤小工具](./practice-d-triton-taint.md)
