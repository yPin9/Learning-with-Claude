# Ch 17 — 工作控制（Job Control）

> 目標：掌握前景/背景、暫停/繼續的操作，以及 nohup/disown/tmux 讓行程在終端關掉後繼續跑。

## 前景與背景

Shell 同時只能有一個前景工作（foreground job）——它佔著你的輸入。其他的工作在背景（background）跑，不占終端。

```bash
sleep 100        # 前景跑，終端被卡住
sleep 100 &      # 加 & = 背景跑，立刻回到提示符
```

## 常用快捷鍵

| 快捷鍵 | 效果 |
|--------|------|
| `Ctrl+C` | 送 SIGINT，終止前景工作 |
| `Ctrl+Z` | 送 SIGTSTP，**暫停**前景工作（不是終止）|
| `Ctrl+\` | 送 SIGQUIT，終止並 core dump |

`Ctrl+Z` 是很多人誤解的地方——它**不是**終止，是暫停，行程還在，只是停止執行。

## jobs / fg / bg

```bash
jobs            # 列出當前 shell 的所有工作
jobs -l         # 也顯示 PID

fg              # 把最近一個暫停/背景工作拉到前景
fg %2           # 把 job 2 拉到前景
bg              # 把最近一個暫停工作放到背景繼續跑
bg %2           # 把 job 2 放到背景
```

典型流程：

```bash
$ vim file.txt         # 正在編輯
^Z                     # Ctrl+Z 暫停
[1]+  Stopped  vim file.txt
$ ls                   # 做點別的事
$ fg                   # 回到 vim
```

## nohup：斷線後繼續跑

`nohup` 讓行程忽略 SIGHUP——終端關掉時，shell 會送 SIGHUP 給它的所有子行程，用了 nohup 就不會被殺。

```bash
nohup ./long-script.sh &          # 背景跑，斷線不死
nohup ./long-script.sh > out.log 2>&1 &   # 指定輸出
```

輸出預設寫到 `nohup.out`。

## disown：事後脫鉤

已經用 `&` 跑了，忘了加 nohup？用 `disown` 事後把它從 shell 的 job table 移除：

```bash
./long-script.sh &
disown             # 移除最後一個背景工作
disown %2          # 移除 job 2
disown -h %2       # -h = 只標記忽略 SIGHUP，不從 job table 移除
```

`disown` 之後 `jobs` 看不到它，但 `ps` 還看得到。

## tmux：最正確的解法

`nohup`/`disown` 是補救措施。**真正的解法**是 tmux：建立一個不依賴終端的工作階段。

```bash
tmux new -s work      # 建立名為 work 的 session
# ... 在裡面跑任何東西 ...
Ctrl+b d              # detach（離開 session，工作繼續跑）

tmux ls               # 列出所有 session
tmux attach -t work   # 重新進入
tmux kill-session -t work  # 終止 session
```

常用 tmux 快捷鍵（都要先按 `Ctrl+b`）：

| 組合 | 效果 |
|------|------|
| `Ctrl+b d` | Detach |
| `Ctrl+b c` | 建立新視窗 |
| `Ctrl+b n/p` | 下一個/上一個視窗 |
| `Ctrl+b %` | 垂直分割 pane |
| `Ctrl+b "` | 水平分割 pane |
| `Ctrl+b 方向鍵` | 在 pane 之間移動 |
| `Ctrl+b [` | 進入 copy mode（可以捲動）|
| `Ctrl+b z` | 最大化/還原當前 pane |

## 什麼時候用哪個

| 情境 | 工具 |
|------|------|
| 短暫切換做別的事 | `Ctrl+Z` + `fg` |
| 跑一個背景任務，不需要看輸出 | `command &` |
| 怕斷線，任務已經在跑了 | `disown` |
| 事先規劃，長時間工作 | `tmux` |
| 遠端 server 跑批次任務 | `tmux`（絕對優先）|
| 系統服務 | `systemd`（不是這幾個）|

## 動手練習

```bash
# 1. 前景 / 背景切換練習
sleep 30 &           # job 1
sleep 40 &           # job 2
jobs -l              # 看兩個工作
fg %1                # 拉 job 1 到前景
# 按 Ctrl+Z 暫停
bg %1                # 讓它在背景繼續
jobs -l              # 確認都在背景跑

# 2. 讓工作繼續到結束
wait %1              # 等 job 1 結束
wait                 # 等所有背景工作結束

# 3. 練習 nohup
nohup bash -c 'for i in $(seq 1 10); do echo "$i"; sleep 2; done' &
cat nohup.out        # 看輸出

# 4. tmux 基本操作
tmux new -s test
# Ctrl+b % 分割
# Ctrl+b d detach
tmux ls
tmux attach -t test
tmux kill-session -t test

# 5. 抓住背景工作的輸出
./script.sh > /tmp/script.log 2>&1 &
tail -f /tmp/script.log   # 即時看輸出
```

## 自我檢核

- [ ] 知道 `Ctrl+Z` 是暫停不是終止
- [ ] 能用 `jobs` / `fg` / `bg` 管理多個工作
- [ ] 知道 `disown` 用來事後解除 SIGHUP 關聯
- [ ] 能建立 tmux session，detach 後 attach 回來

→ [Ch 18 檔案描述符深入](./18-file-descriptors.md)
