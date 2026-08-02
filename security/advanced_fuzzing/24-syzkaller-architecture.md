# Ch 24 — syzkaller 架構

> **目標**：理解 syzkaller 的元件如何協作，能在腦中畫出從「生成一個 syscall 序列」到「把 crash 存進 DB」的完整執行流。理解 syz-manager / syz-executor 的分工，以及 program 如何表示 syscall 序列。

## 為什麼需要理解架構？

很多人用 syzkaller 的方式是：照著文件把環境架起來，然後等 crash。這樣的問題是：一旦 syzkaller 跑起來沒找到 bug，或者找到的 crash 無法重現，你完全不知道從哪裡開始 debug。

更重要的是：如果你要 fuzz 一個自訂的 kernel module，你需要寫 syzlang description（Ch 25 的主題）；要正確地寫 description，你需要理解 syzkaller 怎麼用這份 description 生成 program；要調試 description 的效果，你需要看 dashboard 上的 coverage 數字怎麼解讀。這一切都建立在理解架構的基礎上。

## 整體架構一覽

先看最高層的元件組成：

```
┌─────────────────────────────────────────────────────────┐
│  Host machine（你的 workstation / server）               │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  syz-manager                                     │   │
│  │                                                  │   │
│  │  ┌──────────────┐   ┌────────────┐               │   │
│  │  │ Corpus DB    │   │ Crash DB   │               │   │
│  │  │ (programs)   │   │ (splats +  │               │   │
│  │  └──────────────┘   │  C repro)  │               │   │
│  │                     └────────────┘               │   │
│  │  ┌──────────────┐   ┌────────────┐               │   │
│  │  │ VM Pool      │   │ Web        │               │   │
│  │  │ Manager      │   │ Dashboard  │               │   │
│  │  └──────────────┘   └────────────┘               │   │
│  └───────────────┬──────────────────────────────────┘   │
│                  │ gRPC / SSH + serial                   │
└──────────────────┼──────────────────────────────────────┘
                   │
     ┌─────────────┼─────────────────────┐
     │             │                     │
┌────▼────┐  ┌────▼────┐          ┌────▼────┐
│  VM 0   │  │  VM 1   │   ...    │  VM N   │
│         │  │         │          │         │
│ syz-    │  │ syz-    │          │ syz-    │
│ fuzzer  │  │ fuzzer  │          │ fuzzer  │
│    │    │  │    │    │          │    │    │
│ syz-    │  │ syz-    │          │ syz-    │
│ executor│  │ executor│          │ executor│
└─────────┘  └─────────┘          └─────────┘
  （跑有 KASAN+KCOV 的 kernel）
```

三個執行層：
1. **syz-manager**：在 host 上跑，管理一切（VM 池、corpus、crash DB、web UI）
2. **syz-fuzzer**：在每個 VM 裡跑（較新版本已合併進 syz-manager 的邏輯，但概念上仍然存在），負責生成 program 和突變
3. **syz-executor**：真正執行 syscall 序列、收集 KCOV、偵測 crash 的元件，在 VM 內跑

## syz-manager 的職責

syz-manager 是 syzkaller 的大腦，它做的事分成四塊：

### VM 池管理

```
syz-manager
│
├── 啟動 N 個 VM（QEMU / gVisor / 物理機）
│   每個 VM 用 SSH 登入，傳入 syz-fuzzer binary
│
├── 監控每個 VM 的健康狀態
│   ├── 透過 VM 的序列埠（serial console）讀 kernel console log
│   └── 如果偵測到 "BUG:" / "KASAN:" / "kernel panic" → crash
│
├── VM crash 後：
│   ├── 收集完整 crash log（從序列埠）
│   ├── 重啟 VM（從乾淨 snapshot 恢復）
│   └── 繼續 fuzzing
│
└── 動態調整 VM 數量（根據資源使用率）
```

VM 的重啟是從 **snapshot** 恢復的，而不是完整重開機——這讓重啟時間從幾十秒降到幾秒（取決於 VM 技術）。

### Corpus 管理

Corpus 是一組「有覆蓋價值的 program」。syz-manager 維護這個集合：

```
corpus 資料夾（持久化到磁碟）
│
├── program_0000.prog   ← 一個 syzkaller program
├── program_0001.prog
├── ...
│
每個 .prog 檔案的格式（純文字）：
─────────────────────────────
r0 = socket$inet(0x2, 0x1, 0x0)
setsockopt$SO_REUSEADDR(r0, 0x1, 0x2, &AUTO, 0x4)
bind$inet(r0, &AUTO={0x2, 0x4321, @empty}, 0x10)
listen(r0, 0x5)
─────────────────────────────
```

Coverage 回報有新 edge 的 program 加進 corpus，沒有的丟棄。Corpus 定期做 minimization（移除不再提供 unique coverage 的 program）。

### Crash DB 與去重

找到 crash 之後，syz-manager 做：

1. **去重（dedup）**：把 crash 的 call stack 最上面幾個函式名稱取 hash，相同 hash 的 crash 當成同一個 bug
2. **存儲 crash report**：完整的 KASAN splat / panic log
3. **嘗試生成 C reproducer**：把觸發 crash 的 program 轉換成等效的 C 程式，讓開發者能在沒有 syzkaller 的環境重現

C reproducer 的生成是 syzkaller 的殺手特性。它做的是「syscall 序列 → 等效 C code」的翻譯：

```
syzkaller program:                    生成的 C reproducer:
──────────────────                    ──────────────────────
r0 = socket$inet(0x2, 0x1, 0x0)  →   int r0 = socket(AF_INET, SOCK_STREAM, 0);
bind$inet(r0, &(0x7f0000000000)=  →   struct sockaddr_in addr = {
  {0x2, 0x4321, @empty}, 0x10)           .sin_family = AF_INET,
                                         .sin_port = htons(0x4321), };
                                    bind(r0, (struct sockaddr*)&addr, sizeof(addr));
```

### Web Dashboard

syz-manager 在預設的 `:56741` port 開一個 HTTP server，顯示：
- 總 crash 數量、unique crash 數量
- 每個 VM 的執行速度（programs/sec）
- Corpus 大小和增長曲線
- Coverage 統計（哪些 kernel subsystem 被覆蓋了）

**本段可直接觀察**：線上的 syzbot dashboard（https://syzkaller.appspot.com/upstream）就是 Google 的 syz-manager web UI 的公開版，架構和本地跑的完全相同。

## syz-fuzzer（舊版獨立、新版已整合）

較舊版本的 syzkaller（~2022 年以前）在每個 VM 裡分別跑一個 syz-fuzzer process，它負責：

- 接收 syz-manager 傳來的 corpus
- 生成新的 program（從 corpus 突變，或從 scratch 生成）
- 把 program 傳給 syz-executor 執行
- 收 syz-executor 回傳的 coverage signal
- 判斷是否有新 coverage → 回傳給 syz-manager

較新版本（~2023 以後）把這個邏輯重構，fuzzer 的功能更多地在 manager side 處理，executor 在 VM 裡跑得更獨立。架構細節隨版本變化，但**概念上的分工**（調度層 vs 執行層）是一致的，所以這裡按概念講，不綁定具體的 binary 名稱。

## syz-executor：最底層的執行引擎

syz-executor 是 syzkaller 裡唯一真正在 VM 內**執行 syscall**的元件，也是最性能敏感的部分。

它是一個靜態鏈接的 C++ binary（不依賴任何 .so），設計原則：

- **沒有 heap allocation**（用靜態 buffer）——避免記憶體分配導致的 KASAN 誤報
- **沒有 libc**（用 syscall 直接呼叫）——避免 glibc 觸發 sanitizer 的雜訊
- **Process sandbox**：每次執行一個 program，在 fork 出來的子 process 裡跑，跑完 exit

### executor 的執行循環

```
syz-executor 主迴圈（偽代碼）
──────────────────────────────────
while true:
    // 從標準輸入或 shared memory 讀取下一個 program
    prog = read_next_program()
    
    // fork 一個子 process 執行（避免 crash 影響 executor 自己）
    pid = fork()
    if pid == 0:  // child
        setup_sandbox()         // namespace、seccomp
        cover = kcov_open()     // 開啟 KCOV（見 Ch 22）
        
        for each call in prog:
            kcov_reset(cover)   // 清空 coverage
            
            // 執行實際的 syscall
            ret = execute_syscall(call.nr, call.args)
            
            // 收集 coverage
            n_edges = kcov_read(cover)
            
            // 把 call 結果（return value）存起來
            // 讓後續的 syscall 用來當 resource
            resources[call.out_var] = ret
        
        kcov_close(cover)
        _exit(0)
    
    else:  // parent（executor 本身）
        wait(pid)              // 等子 process 跑完
        collect_coverage()     // 從 shared memory 讀 coverage
        report_to_fuzzer()     // 回傳 coverage signal
```

### 為什麼 executor 不能 crash？

Executor 是 fuzzing 的基礎設施，如果它 crash 了，那個 VM 就失去功能。所以：

- 每個 program 在 **fork 出來的子 process** 裡跑，子 process crash 不影響 executor
- Executor 用 timeout watchdog，如果子 process 超時（通常 5 秒），殺掉它繼續
- Executor 自己盡量不分配記憶體，不走 kernel 的複雜路徑

## Program 表示：syscall 序列 + resource

syzkaller 的 program 是一個**有型別的 syscall 序列**。以下是一個真實 program 的例子：

```
# syzkaller program 格式（.prog 檔）
r0 = socket$inet(0x2, 0x1, 0x0)
r1 = accept$inet(r0, &(0x7f0000000000)=AUTO, &(0x7f0000000010)=0x10)
getsockopt$SO_ERROR(r1, 0x1, 0x4, &(0x7f0000000020)=0x0, &(0x7f0000000030)=0x4)
close(r1)
```

**格式解讀**：
- `r0`, `r1`：resource 變數，代表 syscall 的回傳值（這裡是 fd）
- `socket$inet`：`socket` syscall，加上 `$inet` 的變體限定（告訉 syzkaller 這是 AF_INET socket，所以 args 有額外的型別推斷）
- `0x2, 0x1, 0x0`：具體的參數值（`AF_INET=2`, `SOCK_STREAM=1`, `protocol=0`）
- `&(0x7f0000000000)=AUTO`：指向地址 `0x7f0000000000` 的指標，值是 `AUTO`（由 syzkaller 推斷填入）
- `&AUTO={0x2, 0x4321, @empty}`：一個 struct 的 inline 表示

### Resource 型別系統：生產者 / 消費者

這是 syzkaller 和 triniry 最大的差異。syzkaller 理解「`accept()` 的回傳值（`r1`）是一個 socket fd，可以傳給 `getsockopt()`」。

在 syzlang 裡（Ch 25 詳述），每個 syscall 的輸入和輸出都有型別標注：

```
# syzlang 描述（來自 sys/linux/socket_inet.txt）
socket$inet(domain const[AF_INET], type flags[socket_type], proto int32) sock
#                                                                          ^^^^
#                          輸出型別：sock（一個 resource）

accept$inet(fd sock, addr ptr[out, sockaddr_in], addrlen ptr[inout, len[addr, int32]]) sock
#           ^^^^^^^^
#           輸入：需要一個 sock type resource
```

Syzkaller 在生成 program 時：
1. 看到某個 syscall 需要 `sock` type 的 resource
2. 查找哪些 syscall 能**生產** `sock`（例如 `socket$inet`、`accept$inet`）
3. 在序列前面插入一個生產者 syscall
4. 把生產者的回傳值（`r0`）當成消費者的參數

這讓生成的 program 語義上更合理——不是隨機的 fd 數字，而是「先建立，再使用」的正確順序。

## Coverage 回傳到 Corpus 的流程

這是整個 feedback loop 的核心：

```
┌─────────────────────────────────────────────────────────┐
│  一次 program 執行的 coverage feedback 流程              │
│                                                          │
│  1. syz-fuzzer 從 corpus 抽一個 program（或生成新的）    │
│                                                          │
│  2. 傳給 syz-executor                                   │
│                                                          │
│  3. executor 跑完，把 KCOV 收到的 PC 序列               │
│     轉換成 edge set：                                    │
│     edges = {hash(PC[i], PC[i+1]) for i in 0..n-1}     │
│                                                          │
│  4. 回傳 edges 給 syz-fuzzer                            │
│                                                          │
│  5. syz-fuzzer 比對現有 corpus 的 edge union：           │
│     if edges - corpus_edges != ∅:                       │
│         加入 corpus（這個 program 有 new coverage）      │
│         把 new edges 加入 corpus_edges                   │
│     else:                                               │
│         丟棄（不提供新 coverage）                        │
│                                                          │
│  6. 定期同步 corpus 給 syz-manager（持久化）             │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

**Edge vs PC**：為什麼用 edge（相鄰 PC pair）而不是單獨的 PC？因為 PC 只知道「這個 basic block 跑過了」，edge 知道「從 A 跳到 B 這條路徑跑過了」。同樣的兩個 BB，不同的執行路徑代表不同的程式狀態——edge coverage 比 PC coverage 更有區分度，能引導 fuzzer 探索不同的分支組合。這和 afl++ 的 edge bitmap 概念相同。

## VM Pool：支援多種後端

Syzkaller 的 VM 池支援多種執行環境：

| 後端 | 說明 | 適用場景 |
|------|------|---------|
| **qemu** | QEMU/KVM 虛擬機 | 最常用，需要 KVM，支援快照 |
| **gvisor** | Google gVisor（userspace kernel） | 隔離強，不需要 KVM，但覆蓋不到真 kernel |
| **vmm** | VMware Fusion / ESX | macOS 開發者偶爾用 |
| **isolated** | 物理機（沒有 VM）| 需要特殊韌體或無法虛擬化的目標 |
| **adb** | Android 裝置（ADB 連線）| Fuzz Android kernel |

**QEMU 是預設和最常用的**。syz-manager 的 config 裡指定 VM 數量和 QEMU 的啟動參數，manager 負責啟動 / 重啟每個 QEMU 實例。

為了讓 VM 重啟夠快，syzkaller 用 **QEMU snapshot**（`-snapshot` 或 `savevm`/`loadvm`）——第一次啟動後 snapshot，之後重啟都是 `loadvm` 恢復，通常 3–10 秒就回來了。

## 整體執行流（從啟動到找 crash）

把前面所有元件整合成一個執行流：

```
[啟動 syzkaller]
      │
      ▼
syz-manager 讀 config.cfg
      │
      ├── 編譯 syz-executor（如果需要）
      ├── 啟動 N 個 QEMU VM
      │     每個 VM 啟動後 SSH 登入
      │     上傳 syz-executor binary 到 VM
      └── 開啟 web dashboard（http://localhost:56741）

[正常 fuzzing 迴圈]
      │
      ▼
syz-manager 選擇一個 VM，傳送 corpus 子集和 config
      │
      ▼
VM 內：syz-fuzzer 開始生成 program
      │
      ├── Strategy A：從 corpus 選一個 program，做突變
      │     - 增刪 syscall
      │     - 改 resource 的型別
      │     - 隨機化參數值
      │     - 插入一個新的相關 syscall（依 syzlang resource 型別）
      │
      └── Strategy B：從 scratch 生成
            - 隨機選一個 syscall
            - 推斷它需要哪些 resource（producer chain）
            - 生成最短的能提供這些 resource 的前綴序列

      │
      ▼
syz-executor 執行 program（fork + run）
      ├── KCOV 追蹤每個 syscall 的 kernel coverage
      └── 如果子 process crash → 記錄但繼續

      │
      ▼
coverage signal 回傳
      ├── 有 new edge？→ 加進 corpus
      └── 沒有？→ 丟棄

      │
      ▼
syz-manager 監控 VM 序列埠
      ├── 正常輸出 → 繼續
      └── 偵測到 "BUG:" / "KASAN:" / "panic" →
            ├── 收集完整 crash log
            ├── 重啟 VM
            ├── 嘗試最小化觸發 program
            ├── 嘗試生成 C reproducer
            └── 存入 crash DB
```

## syzbot：syzkaller at scale

Google 在公開的 syzbot 基礎設施上跑著幾百個 VM，24/7 fuzz Linux kernel 的 git master 和各個穩定分支。

**syzbot 的運作**（架構和本地 syzkaller 完全相同，只是規模更大）：
- 追蹤 Linux kernel 的 git 樹，每次 commit 都重新 build kernel 並 fuzz
- 找到的 crash 自動寄 email 給 kernel maintainer
- 提供線上 dashboard：https://syzkaller.appspot.com/

對初學者而言，syzbot dashboard 是理解 syzkaller 能力的最好示範：
- 點開任何一個 crash，看「Crash report」（就是 KASAN splat，Ch 23 的格式）
- 看「Reproducer」欄位（syz program + C reproducer）
- 看「Patch」欄位（commit hash 修了這個 bug）

這讓你能追蹤「fuzzer 找到 bug → 開發者修 bug → patch 進 mainline」的完整生命週期。

## syzkaller 的設計取捨

| 設計決策 | 優點 | 缺點 |
|---------|------|------|
| syzlang 型別描述 | 生成語義合理的 program | 每個新介面需要手寫 description |
| per-fork 執行 | crash isolation，executor 不死 | fork 開銷，比 in-process 慢 |
| edge coverage（KCOV PC pairs）| 方向性強，能區分路徑 | 比 branch coverage 資訊少 |
| VM pool（不是 snapshot fuzzing）| 隔離性好，能觸發 persistent state | 重啟慢，狀態難完全重置 |
| C reproducer 自動生成 | 大幅降低 patch 門檻 | 生成不一定成功（複雜 race）|
| 單一 corpus 在 manager 集中 | 多 VM 共享覆蓋知識 | manager 成為單點 |

snapshot fuzzing（Part 5 的 Nyx/kAFL）是另一個方向：不用 VM 重啟，在 hypervisor 層做毫秒級的 state restore，速度快 10–100×，但設定更複雜，需要 Intel PT 支援。

## 踩雷

**錯誤直覺 1**：「syz-executor 直接在 host 上跑就好，不一定需要 VM。」

Executor 執行的是任意 syscall，包含格式錯誤的參數。在 host 上跑會汙染 host 的 kernel 狀態，更糟糕的是，一旦觸發 kernel panic，整台 host 就死了——你的 fuzzing 基礎設施也跟著掛掉。VM 隔離是不可省略的。（syzkaller 有一個 `isolated` backend 可以直接在 bare metal 跑，但那是針對無法虛擬化的目標，而且你需要另一台機器監控 serial console。）

**錯誤直覺 2**：「corpus 裡的 program 越多越好，不用 minimize。」

Corpus 過大會讓 fuzzer 花太多時間重放舊的 program，而不是探索新的路徑。syzkaller 的 `minimize` 機制會定期移除不再提供 unique coverage 的 program（因為它的 edge 已經被其他 program 覆蓋）。更小的 corpus = 更快的迭代速度。這是 afl++ 的 corpus minimization 在 kernel fuzzer 裡的等價操作。

**錯誤直覺 3**：「syzkaller 找不到 crash，代表目標沒有 bug。」

更可能的原因是：syzlang description 不完整（沒有覆蓋到 bug 藏的 code path）、syscall 序列不夠深（需要更長的 setup 序列才能到達漏洞）、或者 VM 數量太少時間太短。看 dashboard 的 coverage 統計——如果你的目標 subsystem 的 coverage 是個位數百分比，那是 description 的問題，不是「沒有 bug」。

**錯誤直覺 4**：「每個 crash 都是不同的 bug。」

syzkaller 用 call stack hash 去重，但去重不完美——同一個 bug 可能因為不同的呼叫路徑產生不同的 stack trace，被計成兩個 crash。也有相反的情況：兩個不同的 bug 在 crash 時 call stack 很像，被計成一個。看 crash 要讀 KASAN splat 的內容，不能只看去重後的 crash 數量。

## 進階延伸

- **Coverage-guided 突變策略**：syzkaller 有幾個突變算子——`splice`（把兩個 program 的片段拼接）、`insertCall`（插入新 syscall）、`removeCall`（刪掉不必要的 syscall）、`mutateArg`（改某個參數的值）。理解這些突變算子的實作（`prog/mutation.go`），能幫助你在寫 syzlang description 時知道 fuzzer 會怎麼用它。
- **Crash reproducer 的限制**：如果 crash 需要精確的 race condition 時序，自動生成的 C reproducer 重現率可能很低（<10%）。syzkaller 有一個 `threaded` 執行模式，讓多個 syscall 在多執行緒裡跑，提高 race 觸發率；C reproducer 也會對應地加上 `pthread_create`。
- **與 syzbot 互動**：如果你在 syzbot 上找到一個 open bug 想研究，可以：下載它的 `.prog` reproducer，用本地的 syzkaller 複現（需要正確的 kernel 版本）；或者用 C reproducer 在自 build 的 kernel 上測試。這是學習 kernel exploit 的好入口，接上 kernel_pwn 課。

## 動手練習

1. 瀏覽 syzbot dashboard（https://syzkaller.appspot.com/upstream）的 open bugs 清單，找到最近一週新出現的 crash，記錄：bug 類型（KASAN / KMSAN / KCSAN / panic）、subsystem、有沒有 reproducer、有沒有 patch。
2. 下載 syzkaller 原始碼（`git clone https://github.com/google/syzkaller`），閱讀 `prog/prog.go` 的 `Program` 結構，理解一個 program 在 Go 資料結構裡長什麼樣。
3. 閱讀 syzkaller 的一個 description 檔案：`sys/linux/socket_inet.txt`，找到三個有 `resource` 型別的 syscall，說明它們的生產者-消費者關係。
4. 在 syzbot 的一個有 C reproducer 的 crash 頁面，下載 C reproducer，閱讀它的結構——找到對應本章提到的「syscall 序列翻譯」的部分。

## 本章重點

- syzkaller 分三層：syz-manager（host，調度一切）、syz-fuzzer（VM 內，生成和突變 program）、syz-executor（VM 內，真正執行 syscall 並收 KCOV coverage）。
- Program 是有型別的 syscall 序列，resource 型別系統讓 fuzzer 能生成語義合理的「先建立後使用」序列，而不是隨機打 fd 數字。
- Coverage feedback：KCOV PC 序列 → edge hash set → 和 corpus edge union 比較 → 有新 edge 就加入 corpus。
- VM pool 用 snapshot 加速重啟（3–10 秒）；crash 自動最小化並嘗試生成 C reproducer。
- syzbot 是 syzkaller at scale 的公開示範——理解本章後看 syzbot dashboard 上的任何 crash 都應該能讀懂。

## 自我檢核

- [ ] 我能說出 syz-manager / syz-fuzzer / syz-executor 各自的職責
- [ ] 我能解釋為什麼 syz-executor 用 fork 而不是在同一個 process 裡執行 syscall
- [ ] 我能說出 resource 型別系統如何幫助 fuzzer 生成語義合理的 syscall 序列
- [ ] 我能描述 coverage feedback loop：從 KCOV PC 到 corpus 更新的完整流程
- [ ] 我能在 syzbot dashboard 上找到一個 crash 並解讀它的基本資訊

## 延伸閱讀

1. **[syzkaller GitHub: docs/internals.md](https://github.com/google/syzkaller/blob/master/docs/internals.md)**
   - 讀哪段：整份文件，特別是「Program representation」和「Fuzzing process」兩節。
   - 學什麼：syzkaller 原始開發者撰寫的架構文件，包含本章沒有覆蓋的 fuzzer 內部細節（例如 prio table、hint seeds）。這是追 syzkaller source code 的最佳起點。
   - 關聯：本章的所有段落。

2. **[Dmitry Vyukov — Coverage-guided kernel fuzzing with syzkaller（論文 draft）](https://storage.googleapis.com/syzkaller/syzkaller_fuzzbench_oss-fuzz.pdf)**
   - 讀哪段：前 15 頁，架構部分。
   - 學什麼：syzkaller 在 FuzzBench 上的評估，包含和其他 kernel fuzzer 的比較，以及 resource-aware 突變對 coverage 的實際提升數據。
   - 關聯：本章的 VM pool 和 corpus 管理設計取捨。

3. **[syzbot — https://syzkaller.appspot.com/upstream](https://syzkaller.appspot.com/upstream)**
   - 讀哪段：點開任何一個 open bug（選有 C reproducer 的），仔細讀「Crash report」、「Reproducer」、「Kernel config」三個欄位。
   - 學什麼：看 syzkaller at scale 產出的真實 crash，驗證本章對 KASAN splat 格式（Ch 23）和 program 表示的理解。
   - 關聯：Ch 23 的 KASAN splat 解讀 + 本章的 crash DB / reproducer 段落。

4. **[syzkaller source: executor/executor.cc](https://github.com/google/syzkaller/blob/master/executor/executor.cc)**
   - 讀哪段：`main()`、`execute_one()`、`cover_enable()`、`cover_collect()` 四個函式。
   - 學什麼：syz-executor 的真實實作，包含 fork/sandbox 設定、KCOV 整合（對照 Ch 22）、syscall 執行和 coverage 收集的完整細節。用 Go 讀 Fuzzer 邏輯，用 C++ 讀 Executor 邏輯。
   - 關聯：本章的 syz-executor 段落 + Ch 22 的 KCOV mmap+ioctl 介面。

→ [下一章：syzlang——描述 syscall 介面](./25-syzlang.md)
