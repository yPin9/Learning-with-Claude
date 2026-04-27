# Ch 24 — kernelCTF 賽制與流程：LTS / COS / Mitigation 賽道、穩定性要求

> 目標：認識 Google kernelCTF — 三個賽道各自在打什麼 kernel、flag 環境長怎樣、提交需要哪些 artifact、穩定性與時間限制。參賽前要知道自己要打什麼。

## kernelCTF 是什麼

Google kernelCTF（前身為 kCTF）是 Google 的持續性 kernel 漏洞賞金計畫，不是定期舉辦的比賽，而是**全年開放提交**。你發現 Linux kernel 漏洞、寫出穩定的 exploit、提交到 kernelCTF flag server，就能拿到獎金。

官方 repo：`github.com/google/security-research/tree/master/pocs/linux/kernelctf`

每個 submission 包含：
- CVE 號碼
- exploit source code
- writeup（說明 root cause + 利用路徑）
- flag（從 flag server 拿到的 proof）

---

## 三個賽道

### 賽道 1：LTS（Long-Term Support kernel）

- **目標 kernel**：upstream LTS kernel（例如 5.15.x、6.1.x），每次 LTS 版本都有對應的 flag server
- **mitigation 設定**：標準 distro kernel 的 mitigation，沒有 Mitigation 賽道的額外保護
- **獎金**：$21,337 per exploit（2024 數字，隨時更新）
- **最容易入門**：bug 最多，因為 LTS kernel 用戶最廣，bug 修的壓力也最大

### 賽道 2：COS（Container-Optimized OS）

- **目標 kernel**：Google 的 COS kernel（用在 Kubernetes / GKE 節點上），基於 LTS 但有額外 hardening patch
- **mitigation 設定**：比 LTS 更嚴格（COS 特有的 patch），但不如 Mitigation 賽道
- **獎金**：$21,337-$40,337（依嚴重程度）
- **難度**：中等，COS patch 有時候已修了你發現的 bug

### 賽道 3：Mitigation 賽道

- **目標 kernel**：COS kernel + 實驗性的額外 mitigation
- **mitigation 設定**：`RANDOM_KMALLOC_CACHES` + `SLAB_VIRTUAL`（部分）+ `KCFI` + 更多
- **獎金**：最高（$40,000+），因為難度最大
- **現狀**：每個 mitigation 功能在這個賽道當「防禦測試」，你的 exploit 能通過就代表那個 mitigation 有洞

---

## flag server 環境

每個賽道有自己的 flag server。環境大致：

```
你的 exploit binary（static-compiled C）
    ↓ 上傳到 flag server
flag server 在 QEMU VM 裡跑你的 binary
    VM：
    ├── target kernel（LTS / COS / Mitigation）
    ├── 無 root shell（以普通 user 身份跑）
    ├── seccomp 沙盒（可能限制某些 syscall）
    └── flag 在 /root/flag（或 /dev/flag）
你的 exploit 拿到 root → 讀 flag → 印出來 → 提交
```

**exploit 必須是 static-compiled C**：flag server 環境不一定有 libc、不一定有 Python/pwntools。kernelCTF submission 全部是 `gcc -static -o exploit exploit.c` 這種。

---

## 穩定性要求

kernelCTF 要求 exploit 的成功率要**夠高**。沒有硬性數字，但實際上：
- 成功率 < 50%：通常不接受
- 成功率 70-90%：可接受，但要說明
- 成功率 > 90%：理想

**穩定性技巧**：
- Pin exploit 到 CPU 0（`sched_setaffinity`）
- 用 userfaultfd 擴大時間窗口
- 大量 spray（寧多勿少）
- 開頭加 `mlockall(MCL_CURRENT|MCL_FUTURE)`，避免 swap 擾亂時序
- 加重試機制：exploit 失敗時自動重試 N 次（要小心別讓 kernel crash）

---

## 提交所需的 Artifact

一個完整提交需要：

```
submission/
├── exploit.c        # exploit source code
├── Makefile         # 編譯方式（gcc -static 等）
├── README.md        # writeup
│    ├── CVE 號碼
│    ├── root cause 說明
│    ├── 利用路徑
│    ├── primitive 取得步驟
│    └── 穩定性說明
└── flag.txt         # 從 flag server 拿到的 flag
```

writeup 是評分重點之一。Google 團隊會審核你的技術說明是否正確、完整。

---

## 整個賽制流程

```
1. 找 bug（讀 kernel commit log、fuzz、讀 CVE 資料庫、auditing）
2. 確認 bug 在 LTS / COS 的哪個版本有
3. 寫 PoC：觸發 bug（crash / dmesg 有 KASAN / BUG 輸出）
4. 分析利用路徑：
   - 什麼 primitive？（UAF、OOB write、type confusion）
   - 在哪個 kmalloc size？
   - spray 物件選哪個？
   - 要走 cross-cache？RIP control？data-only？
5. 開發 stable exploit
6. 在本地 QEMU 測試穩定性
7. 上傳到 flag server 拿 flag
8. 寫 writeup
9. 提交到 Google（GitHub PR 到 kernelCTF repo）
10. 等 review、拿獎金
```

---

## 從哪裡找 bug

1. **kernel commit log**：`git log --grep="use-after-free" v6.1..v6.1.99`。找「Fix UAF」或「Fix refcount」類型的 commit，往前找它修的 bug。
2. **syzbot**：`syzkaller.appspot.com`。Google 的 continuous fuzzer，找到的 bug 列在這裡，很多都有 reproducer。
3. **CVE database**：NVD、kernel.org/docs/CVEs，找 kernel 相關且 score 高的。
4. **upstream security fix**：`git log --grep="CVE" refs/tags/v6.1` 找已公開的修補，回去看 vulnerable version。
5. **kernelCTF 已公開的 submission**：看別人怎麼找 bug，學 bug 類型的 pattern。

---

## 新人 checklist

- [ ] 看完 kernelCTF 官方 README（github.com/google/security-research 的 kernelctf 目錄）
- [ ] 讀 3 個已公開的 submission writeup（從最近的開始）
- [ ] 能在本地 QEMU 跑 target LTS kernel + 你的 exploit
- [ ] 能 static compile exploit（`gcc -static`）並確認在 target kernel 跑
- [ ] 了解 flag server 的環境限制（看官方說明）

---

## 自我檢核

- [ ] 能說出三個賽道的目標 kernel 和獎金差異
- [ ] 知道 exploit 必須是 static-compiled C 的原因
- [ ] 知道穩定性 < 50% 通常不被接受
- [ ] 能列出一個 submission 需要哪些文件
- [ ] 知道 syzbot 是什麼、怎麼用它找 bug
- [ ] 能說出從「commit diff」到「flag」的 10 步流程

→ [Ch 25 — 從 patch 到 exploit：N-day 完整 walkthrough](./25-patch-to-exploit.md)
