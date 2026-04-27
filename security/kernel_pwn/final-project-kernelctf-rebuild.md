# Final Project — 重建一個已公開 kernelCTF submission

> 目標：挑一個已公開的 kernelCTF submission，從 0 到 root shell 自己寫一次 exploit。不能只是跑原作者的 code。這是整門課的「你會了沒」期末考。

## 這個 Project 在測什麼

前面 25 章 + 4 個練習都是「照著做」。Final Project 是**反過來**：

1. 你看 writeup 和 patch diff（允許）
2. 你自己讀 kernel source，理解 root cause
3. 你自己選利用路徑（可以和原作者不同）
4. 你自己寫 exploit（不能複製原作者的 code）
5. 你的 exploit 能在 QEMU 裡穩定拿到 root

通過的標準：**自己的 exploit，自己的 root shell，成功率 > 50%**。

---

## 推薦選題

以下三個都是公開 writeup + kernelCTF submission，難度遞增：

### 選項 1（入門）：CVE-2023-32233

你在 Ch 25 已經讀過它的 root cause 和框架。這是最好的起點 — 你有 walkthrough 參考，但要自己實作。

- **知識點**：nf_tables UAF、msg_msg spray、cross-cache → cred_jar 或 ops hijack
- **難點**：netlink batch message 的正確格式（要讀 nf_tables.h 和 kernel source）
- **公開 submission**：搜 `CVE-2023-32233 kernelCTF github`，Notselwyn 的版本有詳細 writeup

### 選項 2（中等）：CVE-2022-32250

- **root cause**：nf_tables expr 在 abort path double free（比 2023-32233 稍簡單一點，但 kmalloc size 和 object 不一樣）
- **知識點**：nft_set UAF、ops hijack（2022 年 kernel 沒有 KCFI）、pivot + ROP
- **公開 submission**：搜 `CVE-2022-32250 LPE writeup`

### 選項 3（進階）：CVE-2024-1086

- **root cause**：nft_chain double free via verdict refcount error
- **知識點**：nft_chain（kmalloc-512）UAF、cross-cache 到更大 size 的 victim、data-only
- **難點**：kmalloc-512 的 spray 物件選擇比較少，cross-cache 較難布局
- **公開 submission**：notselwyn 的 github，有 writeup + exploit

---

## 工作流程

### 1. 環境建立（半天）

```bash
# 下載 vulnerable kernel（以 CVE-2023-32233 為例：6.1.27）
wget https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.27.tar.xz
tar xf linux-6.1.27.tar.xz
cd linux-6.1.27

# 配置：開 KASAN + netfilter + user_namespace
make defconfig
./scripts/config \
    -e CONFIG_KASAN -e CONFIG_KASAN_GENERIC \
    -e CONFIG_NETFILTER -e CONFIG_NF_TABLES \
    -e CONFIG_USER_NS -e CONFIG_NET_NS \
    -e CONFIG_DEBUG_INFO -e CONFIG_KALLSYMS_ALL
make -j$(nproc)

# 更新 Ch 0 的 QEMU 啟動腳本，換成這個 bzImage
```

### 2. 理解 root cause（2-4 小時）

```bash
# 找 fix commit
git log --oneline -20 net/netfilter/nf_tables_api.c  # 在 upstream git
git show <commit-hash>

# 讀 diff，找被改掉的函式
# 逆向：如果沒有 fix，原來的 code 是什麼
```

回答以下問題後才開始寫 exploit：
- 漏洞在哪個函式？
- 哪個 code path 觸發 double free / UAF？
- UAF 物件是哪個 struct？size 多少？在哪個 cache？
- 需要什麼權限（user namespace？CAP？）？

### 3. 寫 PoC — 觸發 crash（2-4 小時）

目標：`dmesg` 裡看到 KASAN 報告，確認 bug 被觸發。

不要跳過這步。很多人看完 writeup 就想直接寫 exploit，結果連 crash 都觸發不了。

```bash
# 確認 KASAN 報告含目標 struct 的 alloc/free trace
dmesg | grep -A 30 "KASAN"
# 看 "Allocated by" 有 nft_set_create / nft_chain_create 等
```

### 4. 分析利用路徑（1-2 小時）

**你的選擇，不能抄原作者的路**。考慮：

- UAF 物件 size → 哪個 kmalloc cache？
- spray 物件用哪個？（msg_msg / user_key_payload / pipe_buffer）
- info leak 怎麼做？（讀 dangling chunk 的哪個 field？）
- 提權路線？（ops hijack / cross-cache cred / Dirty Pagetable / modprobe_path）

寫在紙上，再寫 code。

### 5. 開發 exploit（1-3 天）

分段開發，每個 milestone 獨立驗證：

```
milestone 1：user namespace 進去，netlink socket 能正常 send batch
milestone 2：UAF 觸發，dangling pointer 確認（GDB + /proc/kallsyms 驗地址）
milestone 3：spray object 命中 dangling chunk（讀到 spray object 的已知 pattern）
milestone 4：info leak 成功（算出的 kernel_base 後 12 bit = 0）
milestone 5：提權完成（uid = 0）
milestone 6：成功率 > 50%（跑 20 次）
```

每個 milestone 失敗時不要往前走，先 debug 這一層。

### 6. 寫 writeup（2-4 小時）

格式參考 kernelCTF submission：

```markdown
# CVE-XXXX-XXXXX Exploit Writeup

## Root Cause

[一段話說明 bug 在哪個函式、哪個 code path 觸發]

## Exploitation Primitive

UAF on `nft_xxx` (kmalloc-N)
Triggered by: [batch sequence]
Controlled: [read / write / size]

## Exploitation Steps

1. Enter user namespace (CAP_NET_ADMIN)
2. Spray [object] × N to kmalloc-N
3. Trigger UAF: [batch sequence]
4. Info leak via [mechanism]: kernel_base = 0x...
5. [選擇的提權路線]

## Stability

Success rate: N/20 (N%)
Improvement: [你加了什麼讓它變穩定]
```

---

## 評分標準（自我評估）

| 條件 | 說明 |
|---|---|
| **必要** | exploit 是自己寫的（不是原作者 code 的 copypaste） |
| **必要** | 能在 QEMU vulnerable kernel 拿到 root shell |
| **必要** | 寫出 writeup 解釋 root cause + 利用路徑 |
| **加分** | 成功率 > 70% |
| **加分** | 利用路徑和原作者不同 |
| **加分** | 解釋為什麼你選這個路徑而不選別的 |
| **進階** | 在 Mitigation 賽道 kernel 上嘗試（會遇到 random kmalloc caches） |

---

## 卡關時的 debug 流程

**卡在 UAF 觸發**：
- 讀 kernel source 的 transaction/abort 邏輯，用 GDB 在 abort path 加 breakpoint，逐行確認你的 batch sequence 走了哪條路

**卡在 spray 命中**：
- 用 KASAN heap 分配 debugger（`CONFIG_KASAN_GENERIC` + `slub_debug=FZPU`），在 alloc/free 時印 stack trace，確認你的 spray object 和 dangling chunk 的物理 page 是否相同

**卡在 info leak**：
- 在 GDB 裡先手動查 dangling chunk 的鄰居 object，找哪個 field 是 kernel pointer，再在 exploit 裡讀那個 offset

**卡在提權**：
- 先確認 info leak 是對的（kernel_base 和 `/proc/kallsyms` 一致），再確認 primitive（任意寫 / ops hijack）的 target 寫到了正確地址

---

## 完成條件

1. 自己寫的 exploit，在 QEMU vulnerable kernel 能穩定拿到 root shell
2. 完成 writeup（root cause + 利用路徑 + 穩定性說明）
3. Exploit 成功率 > 50%（跑 20 次中 10 次以上成功）

達到這三個條件，這門課的目標就達成了：你有能力讀真實的 kernel CVE、理解 root cause、開發 stable exploit、說清楚你做了什麼。kernelCTF 的賽道就在你面前。

---

## 最後的話

kernelCTF 的頂尖選手不是一開始就會寫 nf_tables exploit 的。他們是做了十幾個 CTF kernel pwn、讀了幾十個 writeup、自己踩過 KASAN / page fault / double fault 每一個坑之後，才能穩定地拿 kernelCTF 的 submission。

這門課給你的是路線圖。走完 Final Project，你有了第一個「自己從頭打到底的 kernel exploit」。接下來的事是繼續讀 writeup、繼續看 syzbot 的 crash、繼續打 CTF kernel 題。每一次都比上一次快一點。
