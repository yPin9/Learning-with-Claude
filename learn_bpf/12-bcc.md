# Ch 12 — bcc：Python 包 C 的混合方式

> 目標：認識 bcc 框架、它的 BPF C + Python 混合架構、為什麼曾經是主流、為什麼現在不推薦新專案用、但你仍然必須讀得懂。

## bcc 是什麼

bcc（BPF Compiler Collection）是 IO Visor 在 2015 推的 BPF 開發框架。核心做兩件事：

1. **把寫 BPF 包成「Python script + 嵌入的 C」**：BPF 程式寫成 Python 字串，bcc 在 runtime 把它丟給 clang 編譯
2. **包成 user-friendly 的 Python API**：建 map、attach probe、讀 ringbuf、印結果

範例（最小版 hello-world）：

```python
#!/usr/bin/env python3
from bcc import BPF

prog = """
int hello(void *ctx) {
    bpf_trace_printk("Hello from BPF!\\n");
    return 0;
}
"""

b = BPF(text=prog)
b.attach_kprobe(event="do_sys_openat2", fn_name="hello")
print("Tracing... Ctrl-C to stop.")
b.trace_print()
```

跑：

```bash
sudo python3 hello.py
```

任何 process 開檔，trace_print 就會印 "Hello from BPF!"。

**這是 BPF 出來頭五年最主流的寫法**。Brendan Gregg 的 [bcc 工具集](https://github.com/iovisor/bcc/tree/master/tools)（execsnoop、opensnoop、tcpconnect 等等）全都用這套寫成。

## bcc 的架構

```
┌─────────────────────────────────────┐
│        Python script                │
│   - 嵌入 BPF C 字串                  │
│   - 建 BPF object                    │
│   - attach probe                     │
│   - 讀 maps / ringbuf                │
│   - 印 / 處理 / 上報                  │
└──────────────┬──────────────────────┘
               │ bcc Python lib
               ▼
┌─────────────────────────────────────┐
│  bcc 運行時                          │
│   - 呼叫 clang 把 C 編成 BPF object  │
│   - 透過 bpf() syscall 載入          │
│   - 建 maps、attach probe            │
└──────────────┬──────────────────────┘
               │
               ▼
            Kernel BPF
```

**關鍵痛點**：clang 在**目標機器** runtime 跑。意思是：

- 每台部署的機器都要裝 clang + llvm + kernel headers（幾百 MB）
- 每次 script 啟動都要編譯（**幾秒** 起跳）
- 編譯時吃記憶體（在 OOM 邊緣的機器是地雷）
- 跨 distro / kernel 版本相容性靠 kernel headers 對得上

這些痛點是 CO-RE 出現的直接動機。

## bcc 與 BPF map 的整合

bcc 提供 macro 簡化 map 宣告：

```python
prog = """
BPF_HASH(counts, u32, u64);     // 自動展開成 map 宣告

int trace_open(struct pt_regs *ctx) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    u64 zero = 0, *val;
    val = counts.lookup_or_try_init(&pid, &zero);
    if (val) (*val)++;
    return 0;
}
"""

b = BPF(text=prog)
b.attach_kprobe(event="do_sys_openat2", fn_name="trace_open")

# user 端讀 map
import time
time.sleep(10)
for k, v in b["counts"].items():
    print(f"PID {k.value}: {v.value} opens")
```

`BPF_HASH`、`lookup_or_try_init`、`b["counts"]` 都是 bcc 的語法糖。底層還是普通 BPF map。

## bcc 與 ringbuf / perfbuf

bcc 經典做法是 perfbuf（早於 ringbuf）：

```python
prog = """
BPF_PERF_OUTPUT(events);

struct event_t {
    u32 pid;
    char comm[16];
    char filename[256];
};

int trace_open(struct pt_regs *ctx, int dfd, const char __user *filename) {
    struct event_t event = {};
    event.pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&event.comm, sizeof(event.comm));
    bpf_probe_read_user_str(&event.filename, sizeof(event.filename), filename);
    events.perf_submit(ctx, &event, sizeof(event));
    return 0;
}
"""

b = BPF(text=prog)
b.attach_kprobe(event="do_sys_openat2", fn_name="trace_open")

def print_event(cpu, data, size):
    e = b["events"].event(data)
    print(f"{e.pid} {e.comm.decode()} {e.filename.decode()}")

b["events"].open_perf_buffer(print_event)
while True:
    b.perf_buffer_poll()
```

新版 bcc 也支援 ringbuf（`BPF_RINGBUF_OUTPUT` macro）— 5.8+ kernel 推薦用 ringbuf。

## bcc tools — 寶藏級工具集

bcc 真正的價值是它附帶的 ~150 個 production-ready 工具：

```bash
ls /usr/share/bcc/tools/ | head -20
# argdist     biolatency  biotop      bitesize    btrfsdist
# btrfsslower cachestat   cachetop    capable     cobjnew
# cpudist     cpuunclaimed  criticalstat  dbslower   dbstat
# ...
```

每個工具都對應一個典型 observability 問題。讀這些 source 是學 bcc 最佳路徑：

```bash
sudo less /usr/share/bcc/tools/execsnoop
sudo less /usr/share/bcc/tools/biolatency
```

## 為什麼新專案不推薦 bcc

幾個結構性問題：

| 問題 | bcc | libbpf+CO-RE 的解法 |
|---|---|---|
| 部署需 clang/headers | 是 | 否，預編譯 binary |
| 啟動慢 | 是（編譯數秒） | 否（毫秒） |
| 跨 kernel 相容性 | 靠 headers，脆弱 | CO-RE relocation |
| 記憶體佔用 | 高（clang runtime） | 低 |
| 二進位大小 | 小（只有 .py） | 較大（嵌 BPF + libbpf） |
| Production 部署 | 痛 | 順 |

**結果**：bcc 社群在 2020 年後逐漸把工具用 libbpf+CO-RE 重寫，叫 `libbpf-tools`，放在 [bcc/libbpf-tools](https://github.com/iovisor/bcc/tree/master/libbpf-tools)。

實際對比：

```bash
# bcc 版（Python + C）
time sudo /usr/share/bcc/tools/execsnoop  # 啟動 ~3-5 秒

# libbpf-tools 版（純 C binary）
time sudo /usr/sbin/execsnoop-bpfcc       # 啟動 < 100ms
```

## 該不該學 bcc？

**該學讀，不必學寫**：

- **讀**：你會在 production server 上看到大量 bcc 工具。會讀才能改、才能做 troubleshooting。
- **不必寫**：新專案用 libbpf+CO-RE（Ch 13）。bpftrace 就夠 ad-hoc。

把 bcc 當「BPF 的 Python wrapper 史」看，比當「現役工具」看健康。

## 一個常見誤解

「BCC 已經死了」 — **不全然**。

bcc 的 Python 框架確實淡出，但 **bcc 這個 GitHub repo 仍是 BPF 工具最大寶庫**。`bcc/libbpf-tools/` 是 CO-RE 工具的標竿、`bcc/tools/` 還是很多人讀的範本。**bcc 變的是底層實作策略，不是社群活躍度**。

## 動手練習

1. **跑一個 bcc 工具**：`sudo /usr/share/bcc/tools/execsnoop`，開個新 terminal 隨便跑指令，看它怎麼即時印。
2. **讀 source**：打開 `/usr/share/bcc/tools/opensnoop`，逐段讀懂 — 你會看到上面講的 map / perfbuf pattern 真實出現。
3. **改一個 bcc 工具**：把 execsnoop 改成只印 `comm == "bash"` 觸發的 execve。
4. **比較 bcc vs libbpf-tools**：如果你 distro 有 `bpfcc-tools`（bcc）跟 `bpftrace`，跑：
   ```bash
   time sudo /usr/share/bcc/tools/execsnoop > /dev/null &
   sleep 2
   kill %1
   ```
   然後對 libbpf-tools 版（如果裝了）做同樣測試，比啟動時間。

## 自我檢核

- [ ] 我能寫一支簡單的 bcc Python script（嵌 BPF C、attach、讀 map）
- [ ] 我能說出 bcc 為什麼啟動慢、為什麼吃記憶體
- [ ] 我能解釋 bcc/tools 與 bcc/libbpf-tools 的關係
- [ ] 我能讀一個 bcc 工具的 source 並指出它用了哪些 bcc macro
- [ ] 我知道為什麼新專案不該用 bcc

下一章開始**真正的現代 BPF 開發** — libbpf + CO-RE 的 kernel side。寫的是純 C，編出 .bpf.o，讓 vmlinux.h、relocation、verifier 全部就位。

→ [Ch 13 libbpf + CO-RE 入門（kernel side C）](./13-libbpf-core-kernel-side.md)
