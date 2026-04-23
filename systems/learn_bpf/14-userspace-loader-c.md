# Ch 14 — User space loader：用 C 寫 loader

> 目標：學會用 libbpf 的 skeleton API 從 user space 載入 .bpf.o、attach、polling ringbuf、操作 map、cleanup。寫出第一支「kernel + user 完整」的 BPF 工具。

## 為什麼要 user space loader

Ch 13 用 `bpftool prog load ... autoattach` 就讓 BPF 跑起來了 — 為什麼還需要 user space code？

幾個 bpftool 不夠用的場景：

- 想**即時消費 ringbuf event**（bpftool 沒有 polling）
- 想用 program 的結果**做後處理**（轉 JSON、推 Kafka、寫 DB）
- 想動態更新 map 內容（pin 之後 bpftool 也行，但程式化更乾淨）
- 想包成一個自帶 daemon 的單檔 binary（給 ops 部署）
- 想加 graceful shutdown、健康檢查、metrics

實務上 90% 的生產 BPF 工具都有 user space loader。

## libbpf skeleton — 自動生成的 user-side API

寫 BPF user space 最痛的部分是「對 .bpf.o 各個 program 與 map 的引用很囉嗦」。bpftool 提供 **skeleton generator** 解決這個：

```bash
# 從 .bpf.o 生成 .skel.h
bpftool gen skeleton minimal.bpf.o > minimal.skel.h
```

`minimal.skel.h` 是個 header file，定義了一個對應你 BPF object 的 struct，把所有 program、map 變成 C 欄位：

```c
struct minimal_bpf {
    struct bpf_object_skeleton *skeleton;
    struct bpf_object *obj;
    struct {
        struct bpf_map *open_counts;
        struct bpf_map *events;
    } maps;
    struct {
        struct bpf_program *do_openat2;
    } progs;
    struct {
        struct bpf_link *do_openat2;
    } links;
};
```

然後 user space loader 寫起來像：

```c
#include "minimal.skel.h"

int main() {
    struct minimal_bpf *skel;

    skel = minimal_bpf__open();      // 1. open .bpf.o
    minimal_bpf__load(skel);         // 2. load 進 kernel + verifier
    minimal_bpf__attach(skel);       // 3. auto-attach（按 SEC 字串）

    /* ... 主迴圈 ... */

    minimal_bpf__destroy(skel);      // 4. cleanup
    return 0;
}
```

**乾淨 4 步**。所有「載入失敗就 cleanup 之前的東西」的 boilerplate 都省了。

## 完整 minimal user 端

把 Ch 13 的 minimal（kprobe + bpf_printk 版）配上 user space loader：

```c
// minimal.c
#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <bpf/libbpf.h>
#include "minimal.skel.h"

static volatile int running = 1;
static void sig_handler(int) { running = 0; }

static int libbpf_print(enum libbpf_print_level lvl, const char *fmt, va_list args) {
    return vfprintf(stderr, fmt, args);
}

int main() {
    struct minimal_bpf *skel;
    int err;

    libbpf_set_print(libbpf_print);
    signal(SIGINT, sig_handler);

    skel = minimal_bpf__open();
    if (!skel) { fprintf(stderr, "open failed\n"); return 1; }

    err = minimal_bpf__load(skel);
    if (err) { fprintf(stderr, "load failed: %d\n", err); goto cleanup; }

    err = minimal_bpf__attach(skel);
    if (err) { fprintf(stderr, "attach failed: %d\n", err); goto cleanup; }

    printf("Tracing... output via /sys/kernel/tracing/trace_pipe\n");
    printf("Try: sudo cat /sys/kernel/tracing/trace_pipe\n");

    while (running) sleep(1);

cleanup:
    minimal_bpf__destroy(skel);
    return err;
}
```

Build：

```bash
clang -Wall -O2 minimal.c -lbpf -lelf -lz -o minimal
```

跑：

```bash
sudo ./minimal
# 另一個 terminal：
sudo cat /sys/kernel/tracing/trace_pipe
```

## Ringbuf polling — 真正的 event-driven 工具

把 Ch 13 的 ringbuf 版 BPF 配上 user space。BPF 那邊 submit event 到 ringbuf，user 這邊 poll：

```c
// loader.c
#include <stdio.h>
#include <unistd.h>
#include <signal.h>
#include <bpf/libbpf.h>
#include "tracer.skel.h"

struct event {
    int  pid;
    char comm[16];
    char filename[128];
};

static volatile int running = 1;
static void sig_handler(int) { running = 0; }

static int handle_event(void *ctx, void *data, size_t sz) {
    struct event *e = data;
    printf("%-16s %-7d %s\n", e->comm, e->pid, e->filename);
    return 0;
}

int main() {
    struct tracer_bpf *skel;
    struct ring_buffer *rb;
    int err;

    signal(SIGINT, sig_handler);

    skel = tracer_bpf__open_and_load();
    tracer_bpf__attach(skel);

    rb = ring_buffer__new(bpf_map__fd(skel->maps.events), handle_event, NULL, NULL);
    if (!rb) { fprintf(stderr, "ringbuf init failed\n"); goto cleanup; }

    printf("%-16s %-7s %s\n", "COMM", "PID", "FILENAME");

    while (running) {
        err = ring_buffer__poll(rb, 100 /* ms */);
        if (err == -EINTR) { err = 0; break; }
        if (err < 0) { fprintf(stderr, "poll failed: %d\n", err); break; }
    }

cleanup:
    ring_buffer__free(rb);
    tracer_bpf__destroy(skel);
    return err;
}
```

`ring_buffer__poll(rb, 100)` 是阻塞最多 100ms — 有資料就跑 callback、沒就 timeout 回來檢查 `running`。是「Ctrl+C 能即時停」的標準做法。

跑起來會看到：

```
COMM             PID     FILENAME
cat              23456   /etc/hostname
bash             12345   /usr/bin/ls
ls               23457   /etc/ld.so.cache
...
```

**這是一個生產級 BPF 工具的最小完整 form**。

## 操作 map

Map 操作從 user 端：

```c
// 讀單一 entry
__u32 pid = 1234;
__u64 count;
err = bpf_map__lookup_elem(skel->maps.open_counts,
                           &pid, sizeof(pid),
                           &count, sizeof(count), 0);

// 寫
__u64 new_value = 0;
err = bpf_map__update_elem(skel->maps.open_counts,
                           &pid, sizeof(pid),
                           &new_value, sizeof(new_value), BPF_ANY);

// 遍歷
__u32 prev = 0, cur;
while (bpf_map__get_next_key(skel->maps.open_counts,
                             &prev, &cur, sizeof(cur)) == 0) {
    bpf_map__lookup_elem(skel->maps.open_counts,
                         &cur, sizeof(cur),
                         &count, sizeof(count), 0);
    printf("pid=%u count=%llu\n", cur, count);
    prev = cur;
}
```

## Makefile 整合 BPF 編譯 + skeleton 生成 + user code

完整 build pipeline：

```makefile
CLANG ?= clang
ARCH ?= x86

BPF_CFLAGS = -O2 -g -Wall -target bpf \
             -D__TARGET_ARCH_$(ARCH) -I.

USER_CFLAGS = -O2 -Wall -I.
USER_LIBS   = -lbpf -lelf -lz

all: tracer

vmlinux.h:
	sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > $@

%.bpf.o: %.bpf.c vmlinux.h
	$(CLANG) $(BPF_CFLAGS) -c $< -o $@

%.skel.h: %.bpf.o
	bpftool gen skeleton $< > $@

tracer: tracer.c tracer.skel.h
	$(CC) $(USER_CFLAGS) $< $(USER_LIBS) -o $@

clean:
	rm -f *.bpf.o *.skel.h tracer vmlinux.h

.PHONY: all clean
.PRECIOUS: %.bpf.o %.skel.h
```

`make tracer` → 一條指令把 vmlinux.h、.bpf.o、.skel.h、tracer binary 全部 build 好。

## 為什麼跨機器部署只搬一個 binary

Build 出的 `tracer` binary 已經把 .bpf.o 嵌在裡面（透過 skeleton header 的 byte array）。**搬到別台 Linux（kernel ≥ build host 的 BTF feature）就能跑** — CO-RE 在那台機器的載入時 relocation。

```bash
scp tracer prod-server:/tmp/
ssh prod-server "sudo /tmp/tracer"
```

不用裝 clang、不用裝 kernel headers、不用裝 Python、不用裝 libbpf-runtime（只要 kernel 有就好）— 這就是 CO-RE 的承諾。

## 一個常見誤解

「我要 release，所以要 static link 整個 libbpf」 — **不必要**。

libbpf 是個 ABI-stable 的小 library（< 200KB）。**動態 link 沒問題**。但要小心 distro 內建的 libbpf 版本可能太舊。實務上很多 BPF 工具會把 libbpf 拉成 git submodule 自己 build，或用 [libbpf-bootstrap](https://github.com/libbpf/libbpf-bootstrap) 範本管理。

## 動手練習

1. **跑 libbpf-bootstrap minimal**：
   ```bash
   cd ~/libbpf-bootstrap/examples/c
   make minimal
   sudo ./minimal
   ```
2. **改 minimal**：把 bpf_printk 換成 ringbuf event。
3. **加 user 端 map dump**：寫 user code 每 5 秒印一次 `open_counts` map 內容，找出開檔最頻繁的 PID。
4. **跨機器部署**：把 build 出的 binary 複製到另一台 Linux 跑（distro 不同也行 — 試試看 CO-RE）。

## 自我檢核

- [ ] 我能說出為什麼需要 user space loader（vs 純 bpftool）
- [ ] 我能用 skeleton API 完成 open/load/attach/destroy 流程
- [ ] 我能用 `ring_buffer__poll` 實作 event-driven loop
- [ ] 我能從 user 端 lookup/update/iterate 一個 map
- [ ] 我能寫一份完整的 Makefile build .bpf.o + .skel.h + binary

下一章我們把 user space 換成 Go — cilium/ebpf 套件是 cloud-native 世界的主流。

→ [Ch 15 cilium/ebpf：Go 寫 user space](./15-cilium-ebpf-go.md)
