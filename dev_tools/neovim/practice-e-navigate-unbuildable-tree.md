# 練習 E — 對編不起來的樹用 gtags 導航

> **目標**：把 Ch 23–25 學到的後備索引用在最硬的靶上——一棵**編不起來的 Linux kernel 子系統**。你不會去 build kernel（那要 config、toolchain、幾分鐘編譯，而且 clangd 只認得你編的那種 config）。你會**不 build**，用 gtags 建索引，在 Neovim 裡找一個網路收包 handler 的定義、反查它所有 caller、**追出一條真實的 call chain**。這就是 clangd 徹底幫不上時，你唯一的導航手段。

> **環境**：Neovim v0.12.4 + Part 5 config（gutentags + `<leader>g*` gtags quickfix），WSL2 / Ubuntu，GNU Global 6.6.7、cscope 15.9。靶樹是 Linux kernel 的 `net/ipv4` 子系統（sparse checkout，不含完整 kernel、無法 build）。本練習所有 `gtags` / `global` / `cscope` 輸出與 Neovim quickfix 都是隔離 XDG 環境真跑照抄。

## 為什麼是這個練習？

kernel 是「clangd 會跪」的教科書案例（Ch 23）：Kbuild 難生 `compile_commands.json`、生了也只涵蓋一種 arch/config、`#ifdef` 地獄。而你常常只是想在某個子系統裡快速回答「這個函式誰呼叫、call chain 長怎樣」——為此去成功編一次 kernel 是荒謬的成本。

這正是 gtags 的主場：**不 build、指到目錄就索引、秒查 caller**。做完這個練習，你會對「編不起來的樹照樣導航」有肌肉記憶——這是讀 kernel、讀 legacy C、讀只拿到部分 source 的專案時，最值錢的一項能力。

## 準備靶樹（不 build 的 kernel 子系統）

我們只抓 `net/ipv4` 這個子系統加它依賴的 header，用 `git` 的 blobless + sparse checkout，**不下載整棵 kernel**（整棵有 GB 級）：

```bash
cd /tmp && mkdir -p klab && cd klab
# blobless + sparse：只抓需要的檔
git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/torvalds/linux.git linux
cd linux
git sparse-checkout set net/ipv4 include/net include/linux
```

驗證抓到什麼（真跑）：

```
$ ls net/ipv4/*.c | wc -l
100                    # 100 個 C 檔
$ du -sh .
116M                  # 只有 116M，不是整棵 kernel
```

**關鍵：這棵樹 build 不起來**——它缺其他子系統、缺 arch code、缺 build config。`bear -- make` 生不出 `compile_commands.json`，clangd 對它是瞎的。這就是本練習要的處境。

> 若你網路抓 kernel 有困難：拿任何一個**中型 C 專案**（clone nginx、redis、或 Ch 24 用的 Lua）也能練同一套流程，只是規模小、call chain 淺。**kernel 同理，只是更大、更深、更能體現「gtags 是唯一手段」**。本練習的參考解答用的是 kernel `net/ipv4`，你用替代樹時符號名換掉即可。

## 任務

限時參考：**15 分鐘**（熟練後 5 分鐘內）。

在 `net/ipv4` 子系統裡，圍繞 TCP 收包路徑，完成：

1. **建索引**：對整棵 `net/ipv4` 樹建 GTAGS，計時。
2. **找定義**：找 `tcp_v4_rcv`（IPv4 TCP 收包入口）的定義在哪個檔哪一行。
3. **反查 caller（第一層）**：`tcp_v4_rcv` 被誰引用？你會發現它**沒有直接的 C 呼叫者**——它是怎麼被叫到的？（提示：函式指標）
4. **追 call chain（往下）**：從 `tcp_v4_rcv` 出發，追出 `tcp_v4_rcv` → `tcp_v4_do_rcv` → `tcp_rcv_established` 這條收包處理鏈，每一跳都用 gtags/cscope 查證「誰呼叫誰」。
5. **在 Neovim 裡實際跳**：全程在 nvim 裡用 `<leader>gd`（定義）、`<leader>gr`（引用/caller）灌 quickfix，`<CR>` 跳過去，`Ctrl-o` 跳回，體會「編不起來的樹一鍵導航」。

**成功標準**：你能畫出這條 call chain，並對每一跳說出「我是用哪條指令查到 X 呼叫 Y 的」，且全程沒有 build、沒有 clangd。

## 如果你卡住了

1. **建不出 GTAGS**：確認你在 `net/ipv4` 的**上層**（`/tmp/klab/linux`）跑 `gtags`，且 `global --version` 是 GNU Global。索引的是整棵 checkout 出來的樹。

2. **`tcp_v4_rcv` 查不到 caller / caller 是空的**：這**不是** bug。`cscope -d -L -3 tcp_v4_rcv` 回空，是因為它**不是被直接呼叫的**——它是註冊進一張函式指標表、透過 indirect call 被叫到。改用 `global -rx tcp_v4_rcv`（引用）看它在哪被**註冊**（找 `.handler =` 那一行）。這是 kernel 導航的經典坑，也是 clangd/cscope 對 indirect call 的共同盲點。

3. **不知道下一跳追誰**：找定義後（`<leader>gd`）**讀那個函式的函式體**，看它呼叫了哪些 `tcp_*` 函式，挑收包相關的那個當下一跳。或用 `cscope -d -L -2 <函式>`（callee，它呼叫誰）列出來挑。

4. **Neovim 裡 `<leader>gr` 沒反應 / 報 no result**：確認你已在 `/tmp/klab/linux` 根目錄建過 GTAGS（`global` 要在有 GTAGS 的樹裡才查得到），且 nvim 的 cwd 在那（`:cd /tmp/klab/linux`）。

5. **想用 `:cscope`**：它在 Neovim **不存在**（Ch 25）。caller 反查在 nvim 裡用 `<leader>gr`（gtags）；要 cscope 的精準 `-3`，跳 terminal 跑 `cscope -d -L -3`。

## 分段步驟

### 階段 1：gtags 建索引

```bash
cd /tmp/klab/linux
time gtags          # 對整棵 checkout 建 GTAGS/GRTAGS/GPATH
```

記下時間。這棵樹 100 個 C 檔，看它幾秒建完——對照「編一次 kernel 要幾分鐘」，體會後備索引的成本優勢。

### 階段 2：global CLI 查定義與引用

先在 CLI 摸清楚 call chain（比在編輯器裡試錯快）：

```bash
global -x  tcp_v4_rcv        # 定義在哪
global -rx tcp_v4_rcv        # 誰引用它（注意：沒有直接 caller，找 .handler 註冊點）
global -x  tcp_v4_do_rcv     # 下一跳定義
cscope -d -L -3 tcp_v4_do_rcv   # 誰呼叫 tcp_v4_do_rcv（cscope 帶「所在函式」欄）
```

（cscope 要先建：`ls net/ipv4/*.c include/net/*.h include/linux/*.h > cscope.files && cscope -b -q -k`）

### 階段 3：Neovim 整合，實際跳

CLI 摸清方向後，回 Neovim 用鍵位跑一遍（這是你日後讀碼的實際姿勢）：

```
:cd /tmp/klab/linux
:edit net/ipv4/tcp_ipv4.c
/tcp_v4_do_rcv<CR>          用搜尋把游標停到符號上
<leader>gd                 查定義 → quickfix → <CR> 跳過去讀函式體
<leader>gr                 查所有 caller → quickfix 列出 → <CR> 逐個跳
Ctrl-o                     跳回（jumplist）
```

## 參考解答

**自己先追完再看。** 追 call chain 的樂趣就在自己撞到「咦這函式沒 caller」然後想通「喔是函式指標」的那一刻。

<details>
<summary>點開參考解答（含真跑輸出）</summary>

### 階段 1：建索引（真跑）

```
$ cd /tmp/klab/linux
$ time gtags
real	0m1.770s
$ ls net/ipv4/*.c | wc -l
100
```

**1.77 秒**建完整個 `net/ipv4`（含依賴 header）的交叉引用索引。對照 build 一次 kernel 動輒數分鐘——而且 build 完 clangd 還只認得那一種 config。gtags 把整棵樹（所有 `#ifdef` 分支的定義）都掃進去了。

### 階段 2：找定義

```
$ global -x tcp_v4_rcv
tcp_v4_rcv       2070 net/ipv4/tcp_ipv4.c int tcp_v4_rcv(struct sk_buff *skb)
```

`tcp_v4_rcv` 定義在 `net/ipv4/tcp_ipv4.c:2070`。這是 IPv4 收到一個 TCP 封包時的入口。

### 階段 3：反查 caller —— 撞到「沒有直接呼叫者」

```
$ cscope -d -L -3 tcp_v4_rcv
（空的！）
```

cscope `-3`（誰呼叫）**回空**。第一次看到會以為索引壞了。但改查引用就真相大白：

```
$ global -rx tcp_v4_rcv
tcp_v4_rcv        364 include/net/tcp.h    int tcp_v4_rcv(struct sk_buff *skb);
tcp_v4_rcv       1934 net/ipv4/af_inet.c   	.handler	=	tcp_v4_rcv,
tcp_v4_rcv        188 net/ipv4/ip_input.c  INDIRECT_CALLABLE_DECLARE(int tcp_v4_rcv(struct sk_buff *));
tcp_v4_rcv        207 net/ipv4/ip_input.c  	ret = INDIRECT_CALL_2(ipprot->handler, tcp_v4_rcv, udp_rcv,
```

**關鍵發現**：`tcp_v4_rcv` 沒有 `foo(...)` 形式的直接呼叫，它是：

- 在 `af_inet.c:1934` 被**註冊**成 `.handler = tcp_v4_rcv`（塞進一個 protocol handler 的 struct）。
- 在 `ip_input.c:207` 透過 `INDIRECT_CALL_2(ipprot->handler, tcp_v4_rcv, ...)` 被**間接呼叫**（走函式指標 `ipprot->handler`）。

這是 kernel 導航的經典坑，也是 **indirect call 的盲點**：cscope/clangd 都追不到「透過函式指標的呼叫」，因為呼叫點寫的是 `ipprot->handler(...)` 不是 `tcp_v4_rcv(...)`。`global -rx`（引用）能撈到註冊點，讓你手動接上這一段。**這就是為什麼讀 kernel 要同時會 `-3`（直接 caller）和 `-rx`（所有引用，含註冊）。**

### 階段 4：往下追 call chain（cscope `-3` 帶所在函式）

`tcp_v4_rcv` 的函式體裡呼叫了 `tcp_v4_do_rcv`。查它的直接 caller：

```
$ cscope -d -L -3 tcp_v4_do_rcv
include/net/tcp.h unknown 568 int tcp_v4_do_rcv(struct sock *sk, struct sk_buff *skb);
net/ipv4/tcp_ipv4.c tcp_v4_rcv 2238 ret = tcp_v4_do_rcv(sk, skb);
net/ipv4/tcp_ipv4.c tcp_v4_rcv 2248 ret = tcp_v4_do_rcv(sk, skb);
```

cscope `-3` 的「所在函式」欄（第二欄）是金礦：`tcp_v4_do_rcv` 在 **`tcp_v4_rcv`** 這個函式裡被呼叫（2238、2248 兩處）。**這就接上了第一跳**：`tcp_v4_rcv` → `tcp_v4_do_rcv`。

再下一跳。`tcp_v4_do_rcv` 定義：

```
$ global -x tcp_v4_do_rcv
tcp_v4_do_rcv    1830 net/ipv4/tcp_ipv4.c int tcp_v4_do_rcv(struct sock *sk, struct sk_buff *skb)
```

它呼叫了 `tcp_rcv_established`（連線已建立時的快路徑）。查證：

```
$ cscope -d -L -3 tcp_rcv_established
net/ipv4/tcp_ipv4.c tcp_v4_do_rcv 1854 tcp_rcv_established(sk, skb);
```

`tcp_rcv_established` 在 **`tcp_v4_do_rcv`** 裡被呼叫（1854 行）。**第二跳接上**：`tcp_v4_do_rcv` → `tcp_rcv_established`。

### 完整 call chain

```
    [IP 層] ip_input.c:207
       │  INDIRECT_CALL_2(ipprot->handler, ...)   ← 函式指標，cscope -3 追不到
       │  註冊點: af_inet.c:1934  .handler = tcp_v4_rcv   ← global -rx 撈到
       ▼
    tcp_v4_rcv          net/ipv4/tcp_ipv4.c:2070   收包入口
       │  (tcp_ipv4.c:2238 / 2248)
       ▼
    tcp_v4_do_rcv       net/ipv4/tcp_ipv4.c:1830   分派
       │  (tcp_ipv4.c:1854)
       ▼
    tcp_rcv_established  連線已建立的快路徑處理
```

每一跳都用「哪條指令查到」標註了——第一段用 `global -rx`（間接呼叫的註冊點），後兩段用 `cscope -3`（直接呼叫 + 所在函式）。**全程沒 build、沒 clangd。**

### 階段 5：Neovim 裡實際跑（真跑驗證）

用 Part 5 config 的 `<leader>gd` / `<leader>gr`，headless 驗證底層可執行（互動 UI 無法貼截圖，以下為底層查詢 + quickfix 灌入的真跑輸出）：

```
$ nvim --headless -u init.lua -l check_practice.lua
   # 腳本: cd kernel → edit tcp_ipv4.c → global -d/-r tcp_v4_do_rcv → setqflist
def of tcp_v4_do_rcv: net/ipv4/tcp_ipv4.c:1830:int tcp_v4_do_rcv(struct sock *sk, struct sk_buff *skb)
tcp_v4_do_rcv callers in quickfix: 6
  sock.h:1182
  sock.h:1192
  tcp.h:568
  tcp_ipv4.c:2238
  tcp_ipv4.c:2248
  tcp_ipv4.c:3362
```

`<leader>gd` 找到定義（tcp_ipv4.c:1830），`<leader>gr` 把 6 個引用灌進 quickfix。實際互動：quickfix 視窗跳出，`j/k` 選、`<CR>` 跳到 `tcp_ipv4.c:2238` 那個 caller、`Ctrl-o` 跳回。你在一棵**編不起來的 kernel 子系統**裡，一鍵反查 caller 並得到可導航清單——這就是這個練習要練成的反射。

（`tcp_v4_do_rcv` 的引用比 cscope `-3` 多，因為 `global -r` 含 header 宣告 `tcp.h:568`、`sock.h` 的 `INDIRECT_CALLABLE_DECLARE`、以及 `tcp_ipv4.c:3362` 的 `.backlog_rcv = tcp_v4_do_rcv` 註冊——又一個函式指標註冊點。gtags 撈得比 cscope 全，代價是混入宣告與註冊，判斷靠你。）

</details>

## 驗證你的成果

- [ ] 你的 `gtags` 在 `net/ipv4` 樹上建出了 GTAGS/GRTAGS/GPATH，且計時是秒級。
- [ ] `global -x tcp_v4_rcv` 給出 `tcp_ipv4.c:2070`。
- [ ] 你發現 `tcp_v4_rcv` 沒有直接 caller，並用 `global -rx` 找到 `af_inet.c` 的 `.handler = tcp_v4_rcv` 註冊點。
- [ ] 你用 `cscope -3` 的「所在函式」欄接出 `tcp_v4_rcv` → `tcp_v4_do_rcv` → `tcp_rcv_established`。
- [ ] 你在 Neovim 裡用 `<leader>gr` 把某個函式的 caller 灌進 quickfix 並跳過去了。
- [ ] 全程你**沒有 build kernel、沒有用到 clangd**。

## 延伸挑戰

1. **往上追到 syscall**：從 `tcp_v4_rcv` 收包端，換方向追**送包 / 系統呼叫端**——找 `tcp_sendmsg` 的定義與 caller，追到 `sys_sendto` / `__sys_sendto`（用 `global -rx`、`cscope -3`）。體會一條 syscall 到底層的路徑要跳幾層。

2. **indirect call 的全貌**：把所有 `.handler = ` / `.backlog_rcv = ` 這類函式指標**註冊點**用 `global -g "\.handler\s*="`（grep pattern）撈出來，理解 kernel 有多依賴函式指標分派——這解釋了為什麼純 caller 反查在 kernel 常常「斷線」。

3. **cscope callee 往下鑽**：對 `tcp_rcv_established` 用 `cscope -d -L -2`（它呼叫誰），往下再追兩層，畫出一棵更深的 call tree。

4. **對照 clangd 的無力**：試著在這棵樹上開 clangd（`nvim net/ipv4/tcp_ipv4.c`，等它 attach），按 `gd` 跳 `tcp_v4_do_rcv`——看它因為沒有 `compile_commands.json` 跳不動或跳錯。這就是 Ch 23 說的「clangd 在 kernel 會跪」的親身現場，也是你為什麼要會 gtags。

5. **裝 git hook 自動更新**：在 `.git/hooks/post-checkout` / `post-merge` 放 `gtags`（或 `global -u` 增量），切 branch / pull 後 GTAGS 自動重建（`reading_code` Ch 14 有腳本）。

## 自我檢核

- [ ] 我知道為什麼在 kernel 子系統裡 clangd 幫不上，而 gtags 是唯一手段
- [ ] 我能解釋「`tcp_v4_rcv` 沒有直接 caller」是怎麼回事，以及怎麼用 `global -rx` 補上
- [ ] 我知道 cscope `-3` 的「所在函式」欄為什麼是追 call chain 的金礦
- [ ] 我能說出 `global -rx` 比 cscope `-3` 多撈到什麼（宣告、函式指標註冊點），以及那是好是壞
- [ ] 我能在 Neovim 裡不靠 clangd、用 `<leader>gd`/`<leader>gr` 在編不起來的樹裡導航
- [ ] 我理解 indirect call（函式指標）是 caller 反查在 kernel 常「斷線」的根本原因

做完這個練習，你手上的後備索引不再是「知道有這工具」，而是「clangd 一跪我立刻能接手」。這是讀 kernel、legacy C、部分 source dump 的底氣。Part 5 到此——你有了 clangd 之外的完整後備。下一個 Part 把整套讀碼流水線串起來：怎麼用 marks / harpoon 把攻堅過程中的關鍵位置**外化**下來，不再靠腦袋記。

→ [Ch 26 外化：marks / harpoon 標攻堅點](./26-externalizing-marks-harpoon.md)
