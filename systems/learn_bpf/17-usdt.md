# Ch 17 — USDT：觀察 user space 應用

> 目標：認識 USDT (User Statically Defined Tracing) 的歷史與機制、如何列出與使用 user 應用的 USDT、為什麼它比 uprobe 更好用、PostgreSQL / Python / JVM 的實戰範例。

## USDT 是什麼

User Statically Defined Tracing — **應用程式作者預先在程式裡標的 trace point**。源自 Solaris dtrace 時代的設計。

語意上跟 kernel 的 tracepoint 完全對應：

| | Tracepoint | USDT |
|---|---|---|
| 在哪 | kernel | user space binary |
| 誰標 | kernel 開發者 | 應用作者 |
| 穩定性 | 跨 kernel 版本承諾 | 跨應用版本承諾 |
| 開銷 | 低（沒事是 nop） | 低（沒事是 nop） |
| 比對對象 | kprobe | uprobe |

**有 USDT 就用 USDT，跟 tracepoint 同樣道理**。Postgres、Python、JVM、Node.js、Erlang、MySQL 都內建大量 USDT。

## USDT 怎麼運作（巧妙的設計）

應用作者在 source 寫：

```c
DTRACE_PROBE2(myapp, query__start, query_text, query_len);
```

編譯時這個 macro 展開成：

1. 一條 `nop` 指令（5 byte）— 對 application 來說是免費
2. 一個 ELF note section 描述「這裡是 query__start probe，參數在哪些 register」

執行時如果沒人 trace，就跑那 5 byte nop — **完全免費**。

當 BPF 想 trace 這個 probe，BPF runtime 把那 5 byte nop 改成 `int 3` (breakpoint)，跟 uprobe 一樣的機制。

差別在：

- **位置由應用作者決定**（最有意義的點）
- **參數有 schema**（解析 ELF note 就知道）
- **應用發 release 不會改 probe 名稱**（穩定 ABI 承諾）

## 列出某 binary 有哪些 USDT

```bash
# Postgres
sudo bpftrace -l 'usdt:/usr/lib/postgresql/15/bin/postgres:*' | head
# usdt:postgres:transaction__start
# usdt:postgres:transaction__commit
# usdt:postgres:query__start
# usdt:postgres:query__done
# usdt:postgres:lwlock__acquire
# ...

# Python (3.6+ with --enable-trace-refs)
sudo bpftrace -l 'usdt:/usr/bin/python3:*'

# OpenJDK with --enable-dtrace
sudo bpftrace -l 'usdt:/path/to/libjvm.so:*'
```

或用 `readelf`：

```bash
readelf -n /usr/lib/postgresql/15/bin/postgres | grep -A 5 stapsdt
```

`stapsdt` 是 SystemTap 給 USDT 取的 ELF note name，沿用至今。

## 用 bpftrace 抓 USDT

最簡單的範例 — 追 PostgreSQL 每個 query：

```bash
sudo bpftrace -e '
usdt:/usr/lib/postgresql/15/bin/postgres:query__start
{
    @start[pid] = nsecs;
    printf("[%d] query: %s\n", pid, str(arg0));
}

usdt:/usr/lib/postgresql/15/bin/postgres:query__done
/@start[pid]/
{
    printf("[%d] done in %d us\n", pid, (nsecs - @start[pid]) / 1000);
    delete(@start[pid]);
}'
```

`arg0` / `arg1` / ... 是 USDT probe 的參數。**參數型別與順序由 application docs 定義**。

對 Postgres 來說 `query__start(arg0=query_text)` — 從 Postgres source `pg_trace_user_stuff.h`（或 `probes.d`）查得。

## libbpf 寫 USDT

從 C 端用 libbpf attach USDT：

```c
SEC("usdt//usr/lib/postgresql/15/bin/postgres:postgres:query__start")
int BPF_USDT(query_start, char *query)
{
    char buf[256];
    bpf_probe_read_user_str(buf, sizeof(buf), query);
    bpf_printk("query: %s\n", buf);
    return 0;
}
```

`BPF_USDT` macro 處理參數解析。SEC 字串格式：`usdt/<binary>:<provider>:<probe>`，通常 provider 跟 binary 名一樣。

## Python USDT

Python 3.6+ 編譯時加 `--enable-trace-refs` 與 `--with-dtrace` 才會有 USDT。Ubuntu 預設沒開，要自己編或裝 `python3-dtrace` 之類套件。

probe 列表（節錄）：

```
usdt:python3:function__entry(filename, funcname, lineno)
usdt:python3:function__return(filename, funcname, lineno)
usdt:python3:line(filename, funcname, lineno)
usdt:python3:gc__start(generation)
usdt:python3:gc__done(collected)
```

範例：誰呼叫了 `time.sleep`：

```bash
sudo bpftrace -e '
usdt:/usr/bin/python3:python3:function__entry
/str(arg1) == "sleep"/
{
    printf("[%d] %s called sleep at %s:%d\n", pid, comm, str(arg0), arg2);
}'
```

## JVM USDT

OpenJDK 用 `--enable-dtrace` 編出來才有。**probe 數量極多**（GC、JIT、class loading、monitor、method 進出 ...）。

```bash
sudo bpftrace -l 'usdt:/path/to/libjvm.so:*' | wc -l
# 可能 60+
```

範例：追 GC 開始/結束：

```bash
sudo bpftrace -e '
usdt:/path/to/libjvm.so:hotspot:gc__begin { printf("GC begin: full=%d\n", arg0); }
usdt:/path/to/libjvm.so:hotspot:gc__end   { printf("GC end\n"); }'
```

## USDT vs uprobe — 該選哪個

| | USDT | uprobe |
|---|---|---|
| 位置 | 由應用作者選 | 任意 function |
| 參數穩定 | 是 | 否（取決於 binary） |
| 應用 rebuild 後是否壞 | 否 | 可能 |
| 開銷（沒人 trace） | 0 | 0 |
| 開銷（trace 時） | 同 uprobe | 同 uprobe |
| 應用沒 USDT 時 | N/A | 還是能用 |

**規則**：應用有 USDT 就用 USDT，沒有再用 uprobe。

## 沒 USDT 的應用怎麼辦

很多應用沒 USDT。三條路：

1. **uprobe**：直接掛 binary symbol（Ch 4），痛但能用
2. **加 USDT**：如果是你維護的 application，用 [`libstapsdt`](https://github.com/sthima/libstapsdt) 動態加 USDT 到 runtime
3. **改 source**：對 open source 加 `DTRACE_PROBE2` macro，patch 上游

實務上 #1 最常見。

## 動手練習

1. **找你機器上有 USDT 的 binary**：
   ```bash
   for f in /usr/bin/* /usr/lib/postgresql/*/bin/postgres; do
       readelf -n "$f" 2>/dev/null | grep -q stapsdt && echo "$f"
   done
   ```
2. **抓一個 USDT**：選一個 USDT binary，用 bpftrace `-l` 看 probe，挑一個 attach 看看會收到什麼。
3. **如果有 PostgreSQL**：跑上面的 query tracer，連 psql 跑幾個 query，看延遲分布。
4. **沒 PostgreSQL 也有 Postgres docker**：
   ```bash
   docker run --rm -d --name pg postgres:15
   docker exec pg find / -name 'postgres' -executable | head
   # 然後 bpftrace USDT 上去（要把 binary 路徑挪出來或在 container 內跑）
   ```

## 自我檢核

- [ ] 我能解釋 USDT 跟 tracepoint 的概念對應關係
- [ ] 我能解釋 USDT 沒 trace 時為什麼是免費的（nop）
- [ ] 我能用 bpftrace 列出與 attach 一個 USDT
- [ ] 我能說出何時用 USDT、何時退回 uprobe
- [ ] 我知道哪些主流應用內建 USDT

下一章我們把 BPF 拿來做最經典的效能分析任務 — sampling profiler 與 flamegraph。

→ [Ch 18 Profiling 與 flamegraph 製作](./18-profiling-flamegraph.md)
