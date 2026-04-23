# Ch 27 — Debug 技巧：verifier log、bpftool、bpf_printk

> 目標：把 BPF 開發中所有 debug 工具整合成一套 workflow — 從「verifier 拒絕」「程式 attach 失敗」「event 沒出」「結果不對」每一種症狀到對應的 debug 武器。

## BPF debug 跟一般程式不一樣

寫一般 C 程式 debug：gdb、printf、core dump、strace。

寫 BPF：
- **沒 gdb**：BPF 跑在 kernel 沙盒，沒 debugger
- **沒 stdout**：要走 trace_pipe 或 ringbuf
- **沒 core dump**：crash → verifier 應該已經擋下來
- **strace 沒幫助**：BPF 不是用 syscall

要靠另一套工具集。

## Symptom → 工具 對照表

| 症狀 | 第一個用 |
|---|---|
| `bpf_object__load_skeleton` 失敗 | verifier log（最後 5 行） |
| Attach 失敗 | `dmesg | tail` + 看 attach 點存不存在 |
| Load 成功但沒 event | `bpf_printk` + `cat trace_pipe` |
| Event 內容不對 | `bpftool map dump` |
| 跨 kernel 跑掛 | 看 BTF + CO-RE relocation log |
| 高 CPU | `bpftool prog profile`（5.7+） |
| Map 內容不對 | `bpftool map dump` + 在 BPF 加 bpf_printk |

下面逐個工具拆。

## 武器 1：verifier log

最關鍵也最難讀的一個。預設只在失敗時印幾行，要看完整 trace 要把 log level 開到 verbose：

```c
LIBBPF_OPTS(bpf_object_open_opts, opts);
opts.kernel_log_level = 2;     // 1 = 失敗才印；2 = 全印
struct skel *skel = skel__open_opts(&opts);
```

或環境變數：

```bash
export LIBBPF_LOG_LEVEL=2
sudo ./your-tool
```

讀 verifier log 的方法（Ch 9 講過，這裡再強化）：

1. **倒著讀**。失敗訊息通常在最後 5–10 行
2. **找 `R0`–`R10` 的型別變化**。verifier 對每個 PC 印 register state — 看你的 register 在哪個 PC 變成 verifier 不接受的 type
3. **counter-example trace**。verifier 拒絕時通常會印一條「導致拒絕的 path」 — 跟著它倒推

## 武器 2：bpf_printk + trace_pipe

最廉價的「printf debugging」：

```c
bpf_printk("got pid=%d filename=%s\n", pid, filename);
```

讀輸出：

```bash
sudo cat /sys/kernel/tracing/trace_pipe
# 或：
sudo bpftool prog tracelog
```

注意：

- **trace_pipe 是全域共享**，多個 BPF 程式同時 printk 會混在一起
- bpf_printk 有 size 上限（PRINTK_MAX）
- **不要 leave in production** — 開銷不低且洗版

## 武器 3：bpftool prog dump

兩種：

```bash
# verifier-translated bytecode（你看到的「正確」BPF）
sudo bpftool prog dump xlated id <id>

# JIT 後的 native code（最終跑的）
sudo bpftool prog dump jited id <id>
```

用途：

- 確認 BPF 真的有 attach（dump 出來不空）
- 比對 helper 呼叫順序與你寫的 C 一致
- 看 verifier 把你的 code 優化成什麼樣

## 武器 4：bpftool map dump

```bash
sudo bpftool map list
sudo bpftool map dump id <map_id>
```

用途：

- 確認 BPF 真的有寫 map（dump 不空）
- 確認 user space 看到的內容跟 kernel 預期一致
- ringbuf 不能 dump（是 stream），但 hash / array / lru 都可以

對 percpu map 加 `--cpu N`：

```bash
sudo bpftool map dump id <id> --cpu 0
```

## 武器 5：bpftool prog profile（5.7+）

量你的 BPF 程式自己跑多久：

```bash
sudo bpftool prog profile id <id> duration 10 cycles instructions
# 結果：
#  prog_id 872:
#      cycles: 12,345
#      instructions: 23,456
```

看單次觸發平均花多少 cycle / instruction — production tuning 必備。

## 武器 6：dmesg

attach 失敗、kernel BPF 子系統內部錯誤通常在 dmesg：

```bash
sudo dmesg | tail -20
```

例如沒開 BPF LSM 卻試 attach LSM 程式，dmesg 會印錯誤詳情。

## 武器 7：bpf_obj_get_info_by_fd / 結構檢查

從 user space 拿 program / map 的 metadata：

```c
struct bpf_prog_info info = {};
__u32 len = sizeof(info);
bpf_obj_get_info_by_fd(prog_fd, &info, &len);
printf("prog id=%d, type=%d, jited_len=%d\n",
       info.id, info.type, info.jited_prog_len);
```

debug 「這個 fd 是誰、什麼狀態」用。

## Ringbuf 沒事件的常見原因

「我寫了 ringbuf 但 user 端收不到」debug 順序：

1. **BPF 真的有跑嗎**：在 BPF 開頭加 `bpf_printk("entered\n")`，看 trace_pipe
2. **reserve 有沒有失敗**：BPF 加：
   ```c
   if (!e) { bpf_printk("ringbuf full\n"); return 0; }
   ```
3. **submit 有沒有跑**：`bpftool map dump` ringbuf 不行，但你可以從 user 端用 `bpf_map_get_info_by_fd` 看 ringbuf 的 producer position 有沒有動
4. **user 端 polling 真的有跑嗎**：在 callback 加 printf
5. **ringbuf max_entries 太小 batch 觸發 wakeup 條件沒到**：把 max_entries 開大 4 倍試

## CO-RE relocation 失敗

```
libbpf: failed to load object 'xxx'
libbpf: failed to relocate calls in section ...
```

成因通常：

1. **vmlinux.h 跟目標 kernel BTF 不一致導致欄位名對不到** — 重新生成 vmlinux.h
2. **目標 kernel 沒這個 struct / field** — 加 `bpf_core_field_exists` fallback
3. **target kernel 沒 BTF**：`/sys/kernel/btf/vmlinux` 不存在 — 用 BTFHub external BTF

## 開發循環建議

寫 BPF 工具的標準 workflow：

```
1. 寫 BPF C
2. clang -O2 -g 編 → 看編譯 error
3. bpftool prog load → 看 verifier log（失敗回 1）
4. 加 bpf_printk → 看 trace_pipe → 確認邏輯對
5. 開 ringbuf 上報 → 寫 user side
6. 跑通後拿掉 bpf_printk
7. 量 overhead（bpftool prog profile）
```

## 一個常見誤解

「BPF 沒法 debug」 — **錯**。

BPF debug 工具是充足的，只是跟 user space 不一樣。verifier log + bpftool dump + bpf_printk 三件套覆蓋 90% 的場景。剩下 10% 透過 systemtap 風格分析（觀察 BPF 內部行為）也能搞定。

## 動手練習

1. **故意造 verifier 失敗 5 次**：每次寫不同類型的 bug（NULL deref、bound、type 錯），讀 log 找原因。
2. **看一支大型 BPF 的 dump**：找 cilium / Falco 載的 program，dump xlated 看複雜程式長啥樣。
3. **用 prog profile 量你前面寫的 tracer**：看每次觸發吃多少 cycle。
4. **製造 ringbuf loss**：寫一個慢消費者，觀察 reserve 失敗計數。

## 自我檢核

- [ ] 我能讀 verifier log 並找出失敗原因
- [ ] 我能用 bpf_printk + trace_pipe debug
- [ ] 我能用 bpftool dump xlated/jited/map
- [ ] 我能用 bpftool prog profile 量 overhead
- [ ] 我能列出 ringbuf 沒收到 event 的 5 個檢查點

最後一章我們處理「BPF 上 production」的所有 concern — overhead 模型、攻擊面、CI 跨版本測試、kernel 升級策略。

→ [Ch 28 效能、安全、生產部署考量](./28-production-considerations.md)
