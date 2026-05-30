# Ch 26 — init 系統：從 SysV 到 systemd

> **目標**：理解 init 系統（PID 1）的角色與演進——SysV init 的循序啟動、systemd 的並行依賴模型、unit/target 概念、開機目標的達成，以及為什麼 systemd 取代了 SysV（含爭議）。這是開機接力的最後一棒。

> **環境**：systemd（現代主流發行版）。承接 Ch 23（PID 1 的誕生）、Ch 25（switch_root 後執行真正的 init）。

## 為什麼 init 系統這麼重要又這麼有爭議？

switch_root 後（Ch 24-25），真正的 root 上的 `/sbin/init` 接管——這就是 init 系統（PID 1）。它是開機的最後一棒：把系統從「只有一個 kernel + 空 userspace」帶到「所有服務跑起來、你能登入」。

init 系統決定了系統怎麼啟動服務、怎麼管理它們的生命週期。現代幾乎都用 **systemd**——但它取代 SysV init 的過程充滿爭議（systemd 是 Linux 社群史上最有爭議的軟體之一）。理解 init 系統的演進，你會懂現代開機的最後階段，以及這場爭議的技術根源。

## 先建立直覺：init 是系統服務的「啟動總管」

```
init（PID 1）的職責：

  開機時：
    按某種順序/邏輯啟動所有系統服務
    （網路、log、cron、各種 daemon、登入介面...）
        │
  運行時：
    - 管理服務生命週期（啟動/停止/重啟）
    - 收養孤兒 process（parent 死了的 process 過繼給 PID 1）
    - 處理 shutdown/reboot
        │
  PID 1 永遠存在（Ch 25 的鐵律），是所有 userspace process 的祖先
```

init 是「系統服務的啟動總管 + 生命週期管理者」。它怎麼「按某種順序啟動服務」是 SysV 和 systemd 的核心差異。

## SysV init：循序啟動

傳統的 SysV init（System V，源自 Unix）用 **runlevel** 和 **循序腳本**：

```
SysV init 的模型：

  /etc/inittab 定義 default runlevel（如 3=多用戶+網路，5=圖形）
        │
  runlevel 對應 /etc/rc<N>.d/ 的腳本目錄
    /etc/rc3.d/
      S10networking    ← S = start，數字 = 順序
      S20ssh
      S30apache
      ...              ← 按數字「循序」執行
        │
  每個 S 腳本是 /etc/init.d/ 的服務腳本（start/stop/restart）
        │
  init 按數字順序「一個接一個」啟動服務
```

```bash
# SysV 的服務腳本（/etc/init.d/ssh，概念）
#!/bin/sh
case "$1" in
  start) /usr/sbin/sshd ;;
  stop)  kill $(cat /var/run/sshd.pid) ;;
  restart) $0 stop; $0 start ;;
esac
```

SysV 的問題：
- **循序啟動慢**：服務一個接一個啟動，即使它們互不依賴（不能並行）
- **依賴用數字編號表達**：S10、S20 的順序是人工排的，脆弱、難維護
- **shell 腳本**：每個服務一個 shell 腳本，重複、易出錯

## systemd：並行依賴模型

systemd 用**宣告式的依賴 + 並行啟動**取代 SysV 的循序腳本：

```
systemd 的模型：

  每個服務是一個 unit（宣告式設定，不是 shell 腳本）
    sshd.service:
      [Unit]
      After=network.target       ← 宣告依賴（在 network 之後）
      [Service]
      ExecStart=/usr/sbin/sshd
        │
  systemd 讀所有 unit，建立依賴圖
        │
  並行啟動：沒有依賴關係的服務同時啟動
    （network 和 log 可以並行；sshd 等 network 好了再啟動）
        │
  → 比 SysV 快（並行）、依賴明確（宣告式）、無 shell 腳本
```

systemd 的核心創新：
- **宣告式依賴**：unit 宣告 `After=`/`Requires=` 等，systemd 自動算啟動順序
- **並行啟動**：無依賴的服務同時啟動，大幅加快開機
- **socket activation**：服務按需啟動（有連線才啟動，省資源）
- **統一管理**：`systemctl` 一個工具管所有服務

## Unit 與 Target

systemd 的核心抽象是 **unit**（各種可管理的東西）：

```
systemd 的 unit 類型：
  .service   服務（daemon，如 sshd.service）
  .target    一組 unit 的集合（取代 SysV runlevel）
  .socket    socket activation
  .mount     掛載點
  .timer     定時任務（取代 cron）
  .device    裝置
  ...

target（取代 runlevel）：
  multi-user.target    多用戶+網路（≈ SysV runlevel 3）
  graphical.target     圖形介面（≈ runlevel 5）
  rescue.target        救援模式（≈ runlevel 1）
  default.target       預設開機目標（symlink 到上面之一）
```

```bash
# 看當前 default target（開機目標）
systemctl get-default
# graphical.target

# 看一個 service unit
systemctl cat sshd.service
# [Unit]
# Description=OpenSSH server daemon
# After=network.target
# [Service]
# ExecStart=/usr/sbin/sshd -D
# [Install]
# WantedBy=multi-user.target

# 管理服務
systemctl start/stop/restart/status sshd
systemctl enable/disable sshd     # 開機自動啟動與否
```

## systemd 的開機流程

```
systemd 開機（switch_root 後 PID 1 = systemd）：

  systemd（PID 1）啟動
        │
  讀 default.target（如 graphical.target）
        │
  解析 target 的依賴鏈：
    graphical.target
      需要 multi-user.target
        需要 basic.target
          需要 sysinit.target
            需要 local-fs.target（掛檔案系統）、swap...
        │
  從底層往上，並行啟動每層的 unit
        │
  最終達成 graphical.target → 顯示登入畫面
```

```bash
# 分析開機（systemd 的強大工具）
systemd-analyze              # 總開機時間
systemd-analyze blame        # 各服務耗時排名（找慢的）
systemd-analyze critical-chain  # 關鍵路徑（依賴鏈上最耗時的）
systemd-analyze plot > boot.svg  # 視覺化開機時序圖
```

> `systemd-analyze` 是理解和優化開機的利器。`blame` 找出哪個服務拖慢開機，`critical-chain` 顯示依賴鏈上的瓶頸。這些工具是 systemd 並行模型的副產品——因為 systemd 有完整的依賴圖和時序資料，才能做這種分析。SysV 做不到（它只是循序跑腳本，沒有依賴圖）。

## SysV 到 systemd 的爭議

systemd 取代 SysV 是 Linux 史上最大的爭議之一：

```
反對 systemd 的論點：
  1. 違反 Unix 哲學「做一件事做好」
     → systemd 管服務、log（journald）、網路（networkd）、
       DNS（resolved）、開機、登入（logind）... 包山包海
  2. 複雜度爆炸，PID 1 變得龐大（PID 1 崩潰 = 系統崩潰）
  3. 二進位 log（journald）取代純文字 log
  4. 中心化：很多東西綁死 systemd，難換掉

支持 systemd 的論點：
  1. 並行啟動快很多
  2. 宣告式依賴比 shell 腳本可靠
  3. 統一管理（systemctl）比一堆 init.d 腳本好用
  4. 現代功能（socket activation、cgroup 整合、timer）
```

> **認識論誠實**：systemd 的爭議是真實且未解的。它確實快、功能多、好管理，但也確實龐大、違反 Unix 哲學、中心化。多數主流發行版（Ubuntu、Debian、Fedora、Arch）採用了 systemd，但有發行版（Devuan、Alpine、Gentoo 的選項）堅持不用 systemd（用 SysV、OpenRC、runit 等）。「systemd 好不好」沒有客觀答案——它是工程取捨和哲學立場的衝突。本課教 systemd 因為它是主流，但你要知道這場辯論的存在和雙方論點。

## 其他 init 系統

```
非 systemd 的 init 系統：
  SysV init     傳統，循序腳本（漸被淘汰）
  OpenRC        Gentoo/Alpine，依賴管理但較輕量
  runit         極簡，每個服務一個 run 腳本
  s6            極簡、高可靠，process supervision
  busybox init  嵌入式（你的練習 C 可以用）
        │
  這些多強調「簡單、符合 Unix 哲學、不中心化」
  是對 systemd 複雜度的回應
```

## 故意對照：SysV vs systemd 啟動同樣的服務

```
SysV 啟動 sshd：
  /etc/init.d/ssh（shell 腳本）
  /etc/rc3.d/S20ssh → symlink，數字 20 決定順序
  循序：等前面的 S10 跑完才跑 S20
        │
systemd 啟動 sshd：
  sshd.service（宣告式）
  After=network.target → 依賴明確
  並行：和其他無關服務同時啟動，只等 network.target
        │
  systemd 快在「並行」，可靠在「宣告式依賴」
```

## 踩雷集錦

1. **以為 systemd 只是 init**：systemd 是一整套系統管理（init + journald + networkd + logind...）。PID 1 只是其中一部分

2. **混淆 enable 和 start**：`systemctl start` 立刻啟動（這次）；`systemctl enable` 設定開機自動啟動（每次）。兩個不同

3. **target 不是 runlevel 的精確對應**：multi-user.target ≈ runlevel 3，但不是嚴格映射。systemd 的 target 是依賴集合，比 runlevel 靈活

4. **改了 unit 沒 daemon-reload**：改 `.service` 檔後要 `systemctl daemon-reload` 讓 systemd 重讀，否則改動不生效

5. **以為不用 systemd 就「落後」**：非 systemd 的 init（runit、OpenRC）有其價值（簡單、嵌入式、哲學）。不是「落後」，是不同取捨

## 進階：systemd 在開機早期的整合

systemd 不只在 switch_root 後——它也能在 initramfs 階段運作：

```
systemd 的全程整合：
  initramfs 階段：systemd（如果 initramfs 用 systemd）
    管理 initramfs 的服務（解密、組 LVM...）
        │
  switch_root → systemd（真正 root 的 PID 1）
    無縫接管（同一個 systemd 概念）
        │
  現代 dracut 的 initramfs 可以用 systemd 當 initramfs 的 init
  → 開機全程統一用 systemd 的依賴模型
```

這個全程整合是 systemd 的野心——從 initramfs 到完整系統，用統一的 unit/依賴模型。配合 UKI（Ch 17 的 unified kernel image），systemd 在推動一個「從韌體到登入，全程 systemd 管理」的願景。理解這個，你會懂為什麼 systemd 不只是 init，而是想統一整個開機和系統管理。這也是爭議的核心——它的整合野心。

## 動手練習

1. 分析你的開機：`systemd-analyze`（總時間）、`systemd-analyze blame | head`（最慢的服務）、`systemd-analyze critical-chain`（關鍵路徑）。找出拖慢開機的服務

2. 探索 unit：`systemctl list-units --type=service`（跑著的服務）、`systemctl cat sshd.service`（看 unit 內容）、`systemctl get-default`（開機目標）

3. 看依賴：`systemctl list-dependencies graphical.target`，看開機目標的依賴樹，理解並行啟動的結構

4. 對比：如果你有非 systemd 系統（Alpine、Devuan），看它的 init（OpenRC/SysV），對比 systemd 的差異。或讀 runit 的設計，體會「極簡 init」的哲學

## 本章重點整理

- init 系統（PID 1）是開機最後一棒：啟動所有服務、管理生命週期、收養孤兒 process
- SysV init：runlevel + 循序腳本（S10/S20 數字排序），慢、脆弱、shell 腳本
- systemd：宣告式 unit + 並行依賴啟動，快、明確、統一管理（systemctl）
- unit 類型（service/target/socket/timer...）；target 取代 runlevel；systemd-analyze 分析開機
- systemd 取代 SysV 充滿爭議（快/功能多 vs 龐大/違反 Unix 哲學/中心化），非 systemd init 仍有價值

## 自我檢核

- [ ] 能解釋 SysV 循序啟動和 systemd 並行依賴啟動的差異
- [ ] 知道 systemd 的 unit 和 target 概念，target 怎麼取代 runlevel
- [ ] 能用 systemd-analyze 分析開機、找出慢的服務
- [ ] 能說出 systemd 爭議的雙方論點（不只一面）
- [ ] 知道 enable 和 start 的差別，以及 daemon-reload 何時需要

## 延伸閱讀

### 官方文件

- **[systemd man pages: systemd(1), systemd.unit(5), bootup(7)](https://www.freedesktop.org/software/systemd/man/)**
  - **讀哪裡**：bootup(7)（開機流程）、systemd.unit(5)（unit 格式）、systemd.target(5)
  - **學什麼**：systemd 開機流程和 unit 的權威定義
  - **前提**：本章

### 部落格 / 文章

- **[systemd for Administrators](http://0pointer.de/blog/projects/systemd-for-admins-1.html)** — Lennart Poettering（systemd 作者）
  - **這篇說什麼**：systemd 作者的系列文，從設計理念到實用技巧
  - **讀哪裡**：前幾篇（unit、依賴、socket activation）
  - **為什麼值得讀**：來自創造者，理解 systemd 的設計意圖（雖然要帶批判眼光看）

- **[The systemd controversy](https://www.linuxjournal.com/content/init-freedom-and-systemd-controversy)** 或類似的爭議討論
  - **這篇說什麼**：systemd 爭議的雙方論點
  - **讀哪裡**：論點對比那部分
  - **為什麼值得讀**：平衡地理解這場辯論，不只聽一面之詞

### 書籍

- **《How Linux Works, 3rd ed.》— Ch 6 (systemd)** — Brian Ward
  - **讀哪幾章**：Ch 6 的 systemd 部分
  - **這本書的定位**：平易地講 systemd，含和 SysV 的對比
  - **前提**：本章

→ [Ch 27 Secure Boot：簽署鏈](./27-secure-boot.md)
