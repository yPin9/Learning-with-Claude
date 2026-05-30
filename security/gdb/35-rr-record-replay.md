# Ch 35 — rr：record-replay 時間旅行

> **目標**：掌握 rr——生產級的 record-replay 除錯器。理解它和 GDB 內建 record 的根本差異（記錄非確定性輸入而非每條指令）、為什麼它快又確定、`rr record`/`rr replay` 工作流、以及它怎麼把「不可重現的 bug」（race、Heisenbug、間歇崩潰）變成「完全可重現、可反覆 reverse」。

> **環境**：Linux x86-64（rr 需要特定 CPU 功能與 `perf_event` 權限），rr 5.x。`sudo apt install rr` 或從 source。

## 為什麼 rr 是 debug 的遊戲規則改變者

Ch 34 的 GDB 內建 record 概念對但太慢。**rr** 用完全不同的機制解決同樣問題，且快到能用於真實程式。它做到一件近乎魔法的事：

> **把程式的一次執行錄下來，之後可以無限次、完全一模一樣地重播——而且能往回走。**

這對最難的 bug 是降維打擊：

- **間歇性 bug**：錄到一次失敗的執行，就能反覆重播研究——不用再「跑一百次祈禱它再壞一次」。
- **race condition**（練習 C 的痛）：錄下來的執行包含確切的 thread 交錯，重播完全一致——race 從「不可重現」變「完全可重現」。
- **Heisenbug**：你加斷點研究時，重播的執行**不受影響**（斷點不改變已錄好的執行流）。

這是練習 C 結尾承諾的「終極武器」。

## rr 怎麼做到（和內建 record 的根本差異）

關鍵洞察：**程式的執行是確定的，除了少數「非確定性輸入」。**

```
   一個程式的執行 = 確定性計算 + 少數非確定性輸入
                                    ├─ syscall 的回傳（read 讀到什麼）
                                    ├─ signal 何時到達
                                    ├─ thread 排程（誰先跑）
                                    ├─ 隨機數、時間、PID
                                    └─ 共享記憶體的非同步存取

   GDB 內建 record：記錄「每條指令」改了什麼 → 巨量、超慢
   rr：           只記錄「非確定性輸入」     → 少量、夠快
                  重播時，確定性部分自己重算，
                  非確定性部分餵回錄好的值 → 完美重現
```

rr 只記錄那些「無法重算」的東西（syscall 結果、signal timing、排程決定），其他靠重新執行重算。記錄量小得多，所以**只比正常執行慢約 1.2–2 倍**（vs 內建 record 的幾十上百倍）。

為了讓多執行緒可重現，rr 還做了一件聰明事：**把所有 thread 排到單一核心上序列化執行**，記錄排程決定。這犧牲了真正的平行（錄製時），換來確定性重播。

## 基本工作流

```bash
# 1. 錄製一次執行
rr record ./myprog arg1 arg2
# 程式正常跑（稍慢），rr 把這次執行存進 trace（~/.local/share/rr/）

# 2. 重播（可重複無數次，每次一模一樣）
rr replay
# 這會啟動一個 GDB，連到 rr 的 replay engine
(rr) continue          # 像平常 debug，但這是「重播」錄好的執行
(rr) reverse-continue  # 往回走！rr 的 reverse 又快又準
(rr) break foo
(rr) reverse-next
```

`rr replay` 進去就是一個 GDB（你所有 GDB 技能都能用），差別是：

- 執行是**重播**錄好的，每次完全一致
- 所有 `reverse-*`（Ch 34）都能用，而且**快**
- 斷點、watchpoint、print 都不改變被重播的執行

## 殺手級用法一：抓 race condition（練習 C 的答案）

```bash
# 一直錄，直到錄到一次「結果錯誤」的執行
rr record ./race
# 檢查結果，如果這次 counter 正確，再錄一次，直到錄到壞的
rr record ./race    # counter = 743821（錯的！就是這次）

# 重播這次壞的執行
rr replay
(rr) break main
(rr) continue
(rr) print counter    # = 743821，和錄製時一模一樣
(rr) watch counter
(rr) reverse-continue # 往回找到「某次 +1 被覆蓋」的確切時刻
```

錄到的 race 包含確切的 thread 交錯——重播時 race **每次都以同樣方式發生**。你可以反覆 reverse、慢慢研究那個丟失更新的瞬間。練習 C 用 scheduler-locking「人為導演」race，rr 是「錄下真實發生的 race 然後反覆看」——這才是真正解 race 的方式。

## 殺手級用法二：從崩潰往回追

```bash
rr record ./crasher      # 錄到崩潰
rr replay
(rr) continue
... 崩在 process_node, node=NULL ...
(rr) break process_node
(rr) reverse-continue    # 回到上次進入 process_node
(rr) up
(rr) watch node          # 監視 node 怎麼變成 NULL 的
(rr) reverse-continue    # 往回找 node 被設成 NULL 的地方
```

崩潰只是症狀，rr 讓你從症狀**往回走到病因**——這是 core dump（Ch 33，只有快照）做不到的。rr 的 trace 像一個「可以前後走的 core dump」。

## rr 的設定與限制

```bash
# 需要的權限（rr 用 perf counter）
sudo sysctl kernel.perf_event_paranoid=1   # 或更低
# rr 會檢查並提示需要的設定

rr record -n ./prog        # 不錄 syscall buffering（除錯 rr 自己時）
rr replay -p <pid>         # 多 process trace 選特定 process
rr ps                      # 看 trace 裡的 process
rr record --chaos ./prog   # chaos mode：故意擾動排程，更容易錄到 race！
```

`rr record --chaos` 特別有用——它故意製造不同的排程，提高「錄到 race」的機率。debug 間歇性並行 bug 時，`--chaos` 模式反覆錄，直到中獎。

限制（認識論誠實）：

1. **CPU 需求**：rr 需要特定的硬體效能計數器，且對 CPU 型號敏感（某些虛擬機、某些 CPU 不支援）。雲端 VM 常不支援。
2. **單核序列化**：錄製時所有 thread 序列化到一核，所以**錄製時的 timing 和真實多核不同**——有些只在真正平行下出現的 race，rr 反而錄不到（要 `--chaos` 碰運氣）。
3. **效能開銷**：~1.2–2x，可接受但非零。
4. **不支援某些東西**：特定 CPU 指令、GPU、某些 syscall、io_uring 的部分功能。
5. **trace 體積**：長時間執行的 trace 不小（但遠小於內建 record）。

> rr 是目前 Linux 上最實用的 reverse debugging 工具，Mozilla 開發、用於 debug Firefox。但它對環境（CPU、權限）挑剔——能跑的話是神器，跑不了（如某些雲 VM）就只能靠別的。

## rr 配合 GDB Python（Part 5）

`rr replay` 裡是完整 GDB，你 Part 5 寫的所有插件、自訂指令、pretty-printer 都能用。甚至可以寫「自動往回找污染源」的 Python 腳本——結合 reverse 與自動化。pwndbg/gef 也能在 rr replay 裡用。

## 踩雷集錦

1. **rr record 失敗說 perf 權限**：`kernel.perf_event_paranoid` 太高。調低（rr 會告訴你需要的值）。
2. **rr 在雲 VM 不能跑**：很多 VM 沒有 rr 需要的 PMU/CPU 功能。檢查 `rr record echo hi` 能不能跑。
3. **錄不到 race**：錄製時單核序列化，timing 和真實不同。用 `rr record --chaos` 反覆錄。
4. **以為 rr replay 能改變執行**：replay 是重播錄好的，你 `set var` 改了值也不會改變後續（它照錄好的走）。要改變得重新 record。
5. **trace 佔空間**：長 trace 大。`rr` 的 trace 在 `~/.local/share/rr/`，定期清。
6. **多 process 搞混**：`rr ps` 看 trace 裡有哪些 process，`rr replay -p` 選對的。
7. **把 rr 當萬能**：它解決「重現 + reverse」，但 CPU/環境限制讓它不是處處能用。知道它的適用邊界。

## 進階：再往深一層

- **rr pack / 分享 trace**：`rr pack` 把 trace 連同 binary 打包，可傳給同事——「我這邊重現了，trace 給你，你也能完全重現」。團隊 debug 的革命。
- **`when` / `when-ticks`**：rr 的時間用 tick 計量，`when` 看當前在 trace 的哪個時間點，可精確跳轉。
- **chaos mode 的原理**：故意隨機化排程優先序、記憶體佈局，最大化暴露並行 bug 的機率。
- **rr 的論文**：USENIX ATC 2017 的論文（延伸閱讀）詳述 record-replay 怎麼做到確定性——很值得讀的系統設計。
- **與 GDB 內建 record 對比**：理解「記錄非確定性輸入」vs「記錄每條指令狀態」是兩者效能天差地遠的根本。
- **Pernosco**：rr 作者做的雲端 reverse debugging 服務，把 rr trace 變成可在瀏覽器探索的時間軸——reverse debugging 的未來形態。
- **與其他工具**：Undo 的 UDB 是商業版類似工具；WinDbg 的 TTD 是 Windows 上的對應物。

## 動手練習

1. 確認環境能跑 rr：`rr record echo hi` + `rr replay`（進去 `continue` 看到 hi）。不行就調 perf_event_paranoid。
2. 對練習 C 的 `race`，反覆 `rr record ./race` 直到錄到一次 counter 錯誤的執行；`rr replay` 重播，確認 counter 值和錄製時一致。
3. 在 replay 裡 `watch counter` + `reverse-continue`，往回找丟失更新的瞬間——完成練習 C 的「rr 重現 race」延伸挑戰。
4. 對一個間歇崩潰的程式，`rr record --chaos` 反覆錄到崩潰，replay 裡從崩潰 `reverse-continue` 追原因。
5. 在 rr replay 裡用你 Part 5 寫的插件指令（如 telescope），確認 Python API 在 rr 下可用。
6. `rr pack` 一個 trace，看它包了什麼（理解「可分享的重現」）。

## 本章重點整理

- rr 是生產級 record-replay 除錯器：錄一次執行，之後無限次完全一致地重播，且能 reverse。
- 機制差異：rr 只記錄「非確定性輸入」（syscall 結果、signal/排程 timing），確定性部分重算——所以快（~1.2–2x）、確定。
- 把不可重現的 bug 變可重現：race（練習 C 的真正解法）、Heisenbug、間歇崩潰。
- `rr record` 錄、`rr replay` 進 GDB 重播；`--chaos` 提高錄到 race 的機率；`rr pack` 分享 trace。
- 限制：CPU/PMU 需求（雲 VM 常不支援）、錄製單核序列化、特定功能不支援。
- replay 裡是完整 GDB，Part 5 的插件都能用。

## 自我檢核

- [ ] rr 和 GDB 內建 record 的根本機制差異是什麼？為什麼 rr 快這麼多？
- [ ] rr 怎麼把「不可重現的 race」變成「完全可重現」？
- [ ] 為什麼說 rr trace 像「可以前後走的 core dump」？
- [ ] `rr record --chaos` 解決什麼問題？
- [ ] rr 的主要環境限制是什麼？什麼情況可能跑不了？

## 延伸閱讀

### 論文 / 官方

- **[rr: Lightweight Recording & Deterministic Debugging](https://arxiv.org/abs/1705.05937)** — O'Callahan, Jones, Froyd, Huey, Noll, Partush（USENIX ATC 2017）
  - **核心貢獻**：記錄非確定性輸入而非全狀態，達到低開銷的確定性 record-replay。
  - **讀哪裡**：§2 設計概覽、§3 怎麼處理非確定性（syscall/signal/排程）。
  - **和本章的關聯**：本章機制解釋的權威來源；想真懂 rr 必讀。

- **[rr 官網與文件](https://rr-project.org/)** 與 **[rr GitHub wiki](https://github.com/rr-debugger/rr/wiki)**
  - **讀哪裡**：Usage、Building And Installing、Chaos mode。
  - **和本章的關聯**：環境設定、指令、限制的權威。

### 部落格 / 文章

- **[Pernosco](https://pernos.co/)** — rr 作者的雲端 reverse debugging
  - **為什麼值得讀**：看 reverse debugging 的下一步形態（瀏覽器裡探索時間軸）。

- **[Debugging Firefox with rr](https://firefox-source-docs.mozilla.org/contributing/debugging/debugging_firefox_with_rr.html)**
  - **為什麼值得讀**：rr 在超大型真實專案的實戰用法。

下一章把 debug 的範圍擴展到「不在本機的」程式：gdbserver 與 remote protocol——遠端、嵌入式、模擬器除錯的基礎。

→ [Ch 36 gdbserver 與 remote protocol](./36-gdbserver-and-remote-protocol.md)
