# Ch 42 — 生態與未來

> **目標**：跳出 Rust-for-Linux 這一棵樹，看整片森林——kernel 之外，Rust 已經在系統與資安的哪些地方落地（Android Binder、Windows kernel、firmware/bootloader、hypervisor），embedded Rust（`embedded-hal`/`cortex-m`/RTIC/Embassy）長什麼樣，以及 Rust 在系統領域接下來往哪走。最後把整門課六個 Part 的能力收成一張地圖，告訴你「學完你站在哪、下一步往哪走」，並指回本 repo 的相鄰課。這是全課的收尾章，不是新技術章——目的是讓你**定位自己**，而不是再塞一個 API。

> **環境考據**：本章大量涉及「某專案某時間點的狀態」——這類事實會過時。所有版本、合併時間、行數等**可查證的斷言，均標 2026-08 查證來源**（見延伸閱讀）。生態變動快，讀到時請以官方現況為準；本章給的是**地形圖**，不是即時快照。本章**不含需要真跑的 code**（純生態與定位），故無「未實測」標記——但每個事實斷言都附了來源。

## 為什麼需要這一章？

前面五章（[Ch 37](./37-rust-for-linux-overview.md)–[Ch 41](./41-kernel-unsafe-safety.md)）把你帶進 Rust-for-Linux 一條很深的隧道：kernel crate、pin-init、misc device、kernel unsafe。隧道走完，容易產生一種錯覺——「Rust 在系統領域 = Rust-for-Linux」。**不是。** RfL 是最受矚目的一塊，但只是整片版圖的一角。

這一章要做兩件事。第一，**拉高視角**：讓你看到 Rust 已經爬進 Android 最核心的 IPC、Windows 的 kernel、firmware、hypervisor、MCU——每一塊都對應本 repo 的某一門課，你不是在學一個孤立技能。第二，**幫你定位**：一門 40 章的課讀完，最怕的是「學了一堆但不知道自己現在能幹嘛」。這章把六個 Part 收成能力地圖，明說「你現在會什麼、缺什麼、下一步該補哪門課」。

沒有這一章，你會帶著「Rust = RfL」的窄視角離開；有了它，你知道自己手上這套 Rust 系統能力，能往至少五個方向展開。

## 先建立直覺：Rust 正在「從外圈往內核滲透」

把系統軟體想成一個同心圓，越往中心越「特權、越難改、bug 越致命」：

```
        ┌─────────────────────────────────────────────┐
        │  應用層工具（ripgrep, uv, Zed, CNCF 專案）    │  ← Rust 早就占領
        │  ┌───────────────────────────────────────┐  │
        │  │  使用者空間系統服務 / hypervisor        │  │  ← Firecracker/crosvm（純 Rust VMM）
        │  │  ┌─────────────────────────────────┐  │  │
        │  │  │  OS kernel（Linux / Windows）     │  │  │  ← RfL、Windows GDI/DWrite
        │  │  │  ┌───────────────────────────┐  │  │  │
        │  │  │  │  firmware / bootloader     │  │  │  │  ← oreboot、tianocore Rust
        │  │  │  │  ┌─────────────────────┐  │  │  │  │
        │  │  │  │  │  MCU 裸機 / RTOS     │  │  │  │  │  ← embedded-hal/Embassy/RTIC
        │  │  │  │  └─────────────────────┘  │  │  │  │
        │  │  │  └───────────────────────────┘  │  │  │
        │  │  └─────────────────────────────────┘  │  │
        │  └───────────────────────────────────────┘  │
        └─────────────────────────────────────────────┘

   時間軸：Rust 從最外圈（~2015 應用工具）一路往內滲透，
           2020 後同時攻進 kernel、firmware、MCU 三個內圈。
```

這個「由外往內」的滲透順序不是巧合。越外圈，換語言的**風險越低、收益兌現越快**（一個 CLI 工具用 Rust 重寫，壞了頂多這個工具掛）；越內圈，memory safety bug 的**代價越高**（一個 kernel UAF = 整台機器 + 提權漏洞），所以「用 memory-safe 語言」的**動機越強**，但**改動阻力也越大**（既有 C code 海量、ABI 綁死、審查嚴）。Rust 這幾年的故事，就是它終於強到能同時應付內圈的「阻力」和滿足內圈的「動機」。

你做過 kernel_pwn，對這個「越內圈越致命」有第一手體感——你打的正是內圈那些 UAF、cross-cache、dirty pagetable。從攻擊者視角看，這片版圖的意義是：**Rust 化的每一圈，都在關掉你熟悉的那些原語**。一個用 Rust 寫的 driver，沒有 C 那種「忘了配對 `kref_put` 導致 UAF」的洞給你打（[Ch 41](./41-kernel-unsafe-safety.md)）——攻擊面從記憶體破壞，被迫移向邏輯漏洞和 `unsafe`/FFI 邊界。這是為什麼本課 Part 5（[Ch 30](./30-security-boundary.md)–[Ch 36](./36-fuzzing-rust.md)）花那麼多力氣講「Rust 的破口在哪」：當 memory bug 這條路被堵，找洞的人要重新學看哪裡。

你這門課的 Part 6 深挖的是「OS kernel」那一圈（Linux）。這一章帶你巡一遍其他所有圈——你會發現每一圈用的都是你**已經學過的同一套 Rust 核心**（ownership、`Result`、trait、`unsafe` 邊界、`no_std`），只是換了個宿主。

### 歷史脈絡：Rust 花了十年才爬到內圈

這個滲透不是一夕發生的，值得知道時間軸，才能理解「為什麼是現在」。Rust 1.0 在 2015 年才發布——在那之前它連穩定的語言都不是。早期（2015–2018）Rust 主要在**最外圈**證明自己：ripgrep（2016）證明它能寫出比 C 工具更快又更安全的 CLI，Firefox 的 Servo/Stylo（2017 把 Rust 的並行 CSS 引擎塞進 Firefox）證明它能進生產級瀏覽器。這階段的訊息是「Rust 能寫真東西，不是玩具」。

中期（2018–2021）往中圈走：AWS Firecracker（2018 開源）證明 Rust 能寫生產級 VMM，`embedded-hal`/`cortex-m` 生態成形證明它能上 MCU。這階段建立了「Rust 適合系統程式」的信心。

近期（2021–至今）攻進最內圈：RfL 的 RFC（2020）與正式合併（Linux 6.1，2022 年底把 Rust 支援併入主線）是分水嶺——**世界上最重要的 C 專案，第一次接受了第二個語言**。微軟同期開始在 Windows kernel 塞 Rust。到 2025，RfL「實驗」被 kernel 維護者判定成功、Binder 進主線（6.18）——內圈的門正式打開。

這條十年的路解釋了一件事：**Rust 進內圈不是炒作，是它花了十年逐圈證明自己「夠快、夠底層、能和 C 共存」之後的自然結果**。你現在學它、用它進 kernel，是站在這條累積的信任上。

## kernel 之外的系統 Rust

### Android：Binder 的 Rust 重寫（已進主線）

Android 的 **Binder** 是它的核心 IPC 機制——幾乎每個跨行程呼叫（app 呼叫 system service、service 之間通訊）都走 Binder。它同時是**效能關鍵**（每次 IPC 都經過它）和**安全關鍵**（它在 kernel 裡，處理來自不受信任 app 的資料）。歷史上 C 版 Binder 出過多個 UAF/race 類的提權漏洞。

為什麼 Binder 是 memory-safe 語言的完美目標？因為它踩中所有「C 最容易出事」的點：(1) 它管理一堆有生命週期的物件（binder node、reference、transaction、buffer），這些物件被多方持有、跨行程傳遞——正是 refcount 管理最容易出錯的場景（漏 `put` 泄漏、多 `put` 變 UAF，[Ch 41](./41-kernel-unsafe-safety.md) 講過）。(2) 它直接處理來自不受信任 userspace 的資料（transaction payload），每一個 `copy_from_user` 都是攻擊面。(3) 它有複雜的鎖與並發（多個行程同時發 transaction）。C 版靠開發者紀律管這三件事，出過的漏洞就是紀律失守的證據。Rust 版把 refcount 交給 `Arc`（[Ch 41](./41-kernel-unsafe-safety.md)）、把 user 資料交給 `UserSlice`（[Ch 40](./40-rust-driver.md)）、把鎖交給 `Mutex` guard——把三類最危險的手動管理，交給型別。

Google 用 RfL 把 Binder driver 用 Rust 重寫。狀態（**2026-08 查證**，見延伸閱讀 LWN/RfL）：

- 這個 Rust Binder driver 於 **Linux 6.18** 合併進主線（`v6.18-rc1`），原作者 Wedson Almeida Filho，現由 Alice Ryhl 維護。
- Android 16（跑 6.12 kernel）已出貨用 Rust 寫的 **ashmem**（匿名共享記憶體）模組——Rust kernel code 已經在數十億台裝置上跑。
- **誠實標注**：Rust 不是萬靈藥。Linux kernel 的**第一個 Rust code CVE** 就出在這個 Rust Binder——**CVE-2025-68260**，一個 race condition，影響 6.18+。這印證了 [Ch 30](./30-security-boundary.md)/[Ch 41](./41-kernel-unsafe-safety.md) 的訊息：**Rust 消滅的是記憶體安全 bug（UAF/OOB），不是邏輯 bug 和並發設計錯誤**。race condition 是設計層的問題，Rust 的 `Send`/`Sync` 能擋掉一部分 data race，但擋不掉「鎖的粒度設計錯了」這種邏輯 race。

Binder 是目前 RfL 「不是玩具、是最嚴苛的生產 driver」的最強證據——它讓「Rust 能寫真正 critical 的 kernel 元件」從口號變成主線裡跑的 code。

> 這連回 [Ch 40](./40-rust-driver.md) 你寫的 misc device：Binder 本質也是一個字元裝置（`/dev/binder`），只是 `ioctl` 的命令複雜得多、狀態機龐大。你手上那套 `MiscDevice` + `UserSlice` + `Mutex` 的技能，就是 Binder driver 用的同一套 kernel crate 抽象，只是規模大幾個數量級。

### Windows kernel：微軟自己在塞 Rust

不只 Linux。微軟也在把 Rust 塞進 Windows，而且是**核心元件**。狀態（**2026-08 查證**，Mark Russinovich 於 RustConf 2025 確認 + Check Point 研究，見延伸閱讀）：

- Windows 已出貨的 Rust code 包括 **DirectWrite Core**（文字排版）和 **Win32k/GDI 的 region 引擎**——GDI region code 已經「officially shipping in your Windows systems」。
- 已遷移的規模：核心 kernel base 約 36,000 行、DirectWrite 子系統約 152,000 行 Rust，都在生產環境。
- 為什麼挑這些？GDI 和 windowing 子系統是 Windows **提權漏洞的重災區**（EoP 大宗），是典型的「high-blast-radius surface」——正是「用 memory-safe 語言收益最大」的地方。
- **同樣誠實**：2025 年 1 月 Check Point 在**新的 Rust 版 GDI 元件**裡找到一個漏洞（微軟於 KB5058499 更新修復）。再次印證：把 C 換成 Rust 收掉一大類 bug，但 `unsafe` 邊界、FFI 邊界、邏輯錯誤仍是攻擊面。這正是本課 [Ch 30](./30-security-boundary.md)–[Ch 32](./32-audit-unsafe.md)（資安向）要你盯的地方。

為什麼是「現在」？這背後有一個業界共識在推動：微軟的 Matt Miller 在 **2019 BlueHat IL** 公開統計，微軟過去 12 年修的安全漏洞約**七成是記憶體安全問題**（memory safety bugs，源自 C/C++ 的記憶體破壞，**2026-08 查證**，見延伸閱讀 MSRC blog）；Google 對 Android、Chrome 也給過同量級的數字。這個「~70%」不是某個人的意見，是各大廠對自家 CVE 分類統計出來的——它是「主流 OS 集體轉向 memory-safe 語言」最硬的一個數字動機。當你知道七成漏洞是同一類、而有個語言能在編譯期消滅那一類，投資就變得理性。這也是本課 [Ch 1](./01-why-rust.md) 開篇「系統語言世代交替」那句話的數據支撐。

Linux 走「社群 + RfL 框架」，Windows 走「廠商內部逐塊重寫」——兩條不同的路徑，但**同一個結論**：主流 OS 都認定「kernel 該引入 memory-safe 語言」，而目前唯一夠格的是 Rust。為什麼是 Rust 而不是別的 memory-safe 語言（Go、Java、Swift…）？因為只有 Rust 同時滿足系統程式的三個硬需求：(1) **無 GC**（kernel/firmware 不能忍受 GC 的不可預測停頓，[Ch 1](./01-why-rust.md) 的「為什麼不用 GC」）；(2) **無 runtime**（能 `no_std`，不需要一個 runtime 撐著）；(3) **C ABI 互操作**（能和海量既有 C code 逐塊共存，不用一次全重寫）。這三個是別的 memory-safe 語言過不了的關，也是 Rust 能爬進最內圈的根本原因。

### firmware / bootloader：更靠近硬體的那一圈

再往內一圈是開機前的世界——firmware 和 bootloader，在 OS kernel 起來之前跑，出 bug 通常是「機器變磚」或「信任根被攻破」。這裡也有 Rust：

- **oreboot**：coreboot 的「去 C」分支，`oreboot` = "coreboot without the C"。用 Rust 寫的 open-source firmware，目標 RISC-V/ARM/x86。這一圈的挑戰是極端 `no_std`（[Ch 22](./22-no-std.md)）——沒有 OS、沒有 heap（一開始連 RAM 都還沒初始化，這叫「romstage」，你只有 CPU cache 當暫時 RAM 用）、直接對 MMIO 暫存器操作。這是 `no_std` 的最硬核場景：連 [Ch 16](./16-smart-pointers.md) 的 `Box` 都不能用（沒 allocator），全靠 stack 和靜態記憶體。
- **tianocore / UEFI 的 Rust 實驗**：UEFI（現代 x86/ARM 開機標準）的參考實作 EDK II（tianocore）社群有把 Rust 引入的實驗；也有純 Rust 的 UEFI 開發生態（`uefi-rs` crate 讓你用 Rust 寫 UEFI application，跑在 UEFI 韌體提供的執行環境上）。

為什麼 firmware 用 Rust 特別有意義？因為 firmware 是**信任鏈的根**——secure boot 從 firmware 開始一路驗證到 OS。firmware 被攻破 = 整個信任鏈崩塌，而且 firmware 漏洞極難修（要刷 BIOS，很多使用者永遠不刷）。同時 firmware 大量解析不受信任的輸入（UEFI 解析磁碟上的 boot loader、韌體解析各種硬體回報的資料結構），是 memory bug 的溫床。歷史上 UEFI 的 C code 出過大量可被利用的解析漏洞（BootHole 那類）。用 Rust 寫解析器，把這類 bug 在編譯期擋掉，收益直接。這一圈直接連回本 repo 的 **`systems/linux_boot`** 課（BIOS/UEFI 雙線、自製 boot sector/UEFI app）——那門課教你開機流程的 C/組語那半邊，Rust firmware 是「用 memory-safe 語言重做同一件事」的前沿。

### hypervisor / VMM：純 Rust 的虛擬機監控器

往外一圈（回到使用者空間，但仍是最底層的系統軟體）是 **hypervisor / VMM**（Virtual Machine Monitor）——管理 VM、模擬裝置、對接 KVM 的東西。這一塊 Rust 幾乎是主流選擇（**2026-08 查證**，見延伸閱讀）：

- **rust-vmm**：一個共享的開源專案，提供一堆可重用的 VMM 元件（KVM wrapper、virtio 裝置、VMM libraries），讓各專案不用重寫共通部分。crosvm、Firecracker、Kata Containers、Cloud Hypervisor 團隊共同維護。
- **Firecracker**（AWS）：純 Rust 的 microVM，為 serverless（Lambda）和容器設計，刻意只給 VM 最小裝置模型——「QEMU 的替代品，但只做必要的事」。
- **crosvm**（Google/ChromeOS）：ChromeOS 的 VMM，Firecracker 就是從它 fork 出來的。

為什麼 VMM 幾乎清一色 Rust？因為 VMM 是**攻擊面極大**的東西——它要**解析來自 guest（可能是惡意的）的裝置操作**（MMIO、DMA、virtio queue），一個 memory bug 就是 **VM escape**（從 guest 逃到 host）。這正是 memory safety 收益最大的場景之一。

> 這一圈**直接對應本 repo 的 `security/vm_escape` 課**——那門課教你 QEMU/KVM 的 device emulation 怎麼被當成 exploit 原語（heap overflow/UAF/劫 callback）打出 VM escape。Firecracker/crosvm 用 Rust 寫，正是為了在**寫 VMM 這一側**把那類漏洞在編譯期擋掉。攻擊視角看 `vm_escape`、防守/實作視角看 rust-vmm——兩門課是同一個戰場的正反兩面。你的 Rust `unsafe`/FFI 邊界審計技能（[Ch 19](./19-ffi.md)/[Ch 32](./32-audit-unsafe.md)）在這裡就是核心武器。

## embedded Rust：MCU 裸機的另一片天

前面都是「大機器」（server、PC、手機）。往同心圓最內圈走是 **MCU（微控制器）** 的世界——Cortex-M、RISC-V microcontroller、幾十 KB RAM、沒有 OS、直接對暫存器。這是 Rust 系統應用**最成熟、生態最完整**的一塊之一，而且和 kernel 世界共用同一個地基：`no_std`（[Ch 22](./22-no-std.md)）。

### 分層地圖

embedded Rust 生態分幾層，由下往上：

```
  應用 / RTOS 風格框架
  ┌────────────────────────────────────────────────┐
  │  RTIC（中斷驅動的即時並發）  Embassy（async 執行）│  ← 框架層
  ├────────────────────────────────────────────────┤
  │  各晶片 HAL：embassy-stm32 / stm32f4xx-hal / ...  │  ← 晶片專屬（實作 embedded-hal traits）
  ├────────────────────────────────────────────────┤
  │  embedded-hal（1.0）：GPIO/SPI/I2C/UART 的抽象 trait│  ← 硬體無關的 trait 標準
  ├────────────────────────────────────────────────┤
  │  cortex-m / riscv：核心存取（暫存器、中斷、臨界區）│  ← 架構層
  ├────────────────────────────────────────────────┤
  │  PAC（Peripheral Access Crate，svd2rust 生成）     │  ← 暫存器級 type-safe 存取
  └────────────────────────────────────────────────┘
```

### 各層在幹嘛

- **`cortex-m` / `riscv`**：對應核心的存取——`cortex-m` crate 給你 Cortex-M 的暫存器、中斷遮罩、臨界區（`critical_section`）。這直接連回本 repo **`architecture/arm`** 課的 Cortex-M 線（final = Cortex-M3 mini RTOS）。
- **`embedded-hal`（1.0）**：這是 embedded Rust 生態的關鍵——它定義一組**硬體無關的 trait**（`SpiBus`、`I2c`、`OutputPin`…）。狀態（**2026-08 查證**）：`embedded-hal` **1.0 已發布**，提供穩定 API + 語意化版本，是整個生態的穩定地基。它的意義跟你 [Ch 9](./09-traits.md)/[Ch 11](./11-trait-objects-dispatch.md) 學的 trait 完全一致——一個 driver crate（例如某個溫度感測器）只依賴 `embedded-hal` 的 trait，就能在**任何**實作了那些 trait 的晶片上跑。這是 trait 作為「硬體抽象層」的教科書級應用。
- **晶片 HAL**：各家晶片（STM32、nRF、ESP32…）的 HAL crate 實作 `embedded-hal` 的 trait，把抽象接到真實暫存器。
- **RTIC**：Real-Time Interrupt-driven Concurrency——一個以**硬體中斷**為並發原語的框架，用 Rust 型別系統在編譯期保證「共享資源存取無 data race」（靠優先權天花板協定 Priority Ceiling Protocol，不用 RTOS，也不用 heap）。RTIC 的核心洞見很漂亮：MCU 上的並發本來就是「中斷打斷主程式或彼此打斷」，RTIC 把「哪個 task 能碰哪個共享資源、在什麼優先權下」在編譯期算清楚，讓你**不需要鎖**（優先權天花板保證不會有兩個 task 同時碰同一資源），也就沒有死鎖。這是 [Ch 24](./24-shared-state.md)/[Ch 25](./25-atomics-lockfree.md) 的並發知識在「硬體中斷即排程」場景的一種特化。RTIC 和 Embassy 不互斥——RTIC 可以當 Embassy 的 executor 用，兩者處理的是不同層次（RTIC 管中斷優先權與資源，Embassy 管 async task 的 poll）。
- **Embassy**：把 [Part 4](./26-async-futures.md)（[Ch 26](./26-async-futures.md)–[Ch 29](./29-async-pitfalls.md)）你學的 **async/await 直接搬到 MCU**。狀態（**2026-08 查證**）：Embassy 從 **Rust 1.75 起可用 stable 編譯器編**（不再需要 nightly），HAL/USB/網路/藍牙/bootloader crate 都已發布到 crates.io。它的執行器（executor）不需要 OS、不需要 heap——你 [Ch 27](./27-async-executor-pin.md) 手刻的那個 mini executor，Embassy 就是它的生產、`no_std`、跑在 MCU 上的版本。

> **這是全課最漂亮的橫向連結之一**：你在 [Part 4](./26-async-futures.md) 學 async 時可能覺得「這是 server 的東西（Tokio、epoll）」。Embassy 證明**同一套 `Future`/`poll`/`Waker` 機制**，換掉底層的 reactor（從 epoll 換成 MCU 的硬體中斷/timer），就能在一個沒有 OS、幾十 KB RAM 的晶片上做 async 多工。async 不是「網路專用」，是一套**通用的協作式多工抽象**——這是你走完 [Ch 26](./26-async-futures.md)–[Ch 29](./29-async-pitfalls.md) + 這一章才拼得起來的認識。

為了讓「poll 迴圈不依賴 OS」這句話具體，用純 Rust 手動 poll 一個 `Future`（**本機 `rustc 1.97.1` 真跑**，不用任何 async runtime）——這正是 [Ch 27](./27-async-executor-pin.md) executor 的最小骨架，也是 Embassy 在 MCU 上做的事的核心：

```rust
use std::future::Future;
use std::pin::Pin;
use std::task::{Context, Poll, RawWaker, RawWakerVTable, Waker};

// 「等某事件 N 次才完成」的 Future，模擬等 timer/中斷 N 次
struct CountDown { remaining: u32 }
impl Future for CountDown {
    type Output = u32;
    fn poll(mut self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<u32> {
        if self.remaining == 0 { Poll::Ready(0) }
        else { self.remaining -= 1; Poll::Pending }   // 真實 Embassy：這裡登記 waker 給硬體中斷
    }
}
fn noop_waker() -> Waker {                              // 真實環境的 waker 由 reactor 提供
    fn no_op(_: *const ()) {}
    fn clone(_: *const ()) -> RawWaker { RawWaker::new(std::ptr::null(), &VT) }
    static VT: RawWakerVTable = RawWakerVTable::new(clone, no_op, no_op, no_op);
    unsafe { Waker::from_raw(RawWaker::new(std::ptr::null(), &VT)) }
}
// 最小 executor：反覆 poll 直到 Ready。這骨架不依賴 OS——換 waker 就換宿主。
fn block_on<F: Future>(mut fut: F) -> F::Output {
    let waker = noop_waker();
    let mut cx = Context::from_waker(&waker);
    let mut fut = unsafe { Pin::new_unchecked(&mut fut) };
    let mut polls = 0u32;
    loop {
        polls += 1;
        if let Poll::Ready(v) = fut.as_mut().poll(&mut cx) {
            println!("ready after {} polls", polls); return v;
        }
    }
}
fn main() {
    let r = block_on(CountDown { remaining: 3 });
    println!("result = {}", r);
}
```

本機真跑輸出：

```
ready after 4 polls
result = 0
```

`4 polls` = 3 次 `Pending`（`remaining` 從 3 數到 0）+ 1 次 `Ready`。這個 `block_on` 迴圈**沒有一行依賴 OS**——它只是「反覆問 Future 好了沒」。把這個手刻迴圈換成 Embassy 的 executor、把 `noop_waker` 換成「由硬體中斷觸發的 waker」，同一套機制就在 MCU 上跑起來了。這就是「async 是通用多工模型、不是網路專利」的技術本質：`Future`/`poll`/`Waker` 是純語言機制，reactor（誰來 poll、何時喚醒）才是換宿主時要換的部分。

### `embedded-hal` 為什麼是關鍵：trait 就是那條硬體抽象邊界

`embedded-hal` 值得多說一段，因為它是「trait 作為抽象核心」（[Ch 9](./09-traits.md)）在真實生態最成功的落地，而且它的設計解決了 embedded C 世界一個長年的痛。

C 的 embedded 世界沒有統一的硬體抽象——每家晶片廠給你一套自己的 SDK（STM32 的 HAL、Nordic 的 SDK、Microchip 的…），一個溫度感測器的 driver 綁死在某家 SDK 的 API 上。換晶片就要重寫 driver，或用一堆 `#ifdef` 硬湊。生態碎片化，driver 不可攜。

`embedded-hal` 的解法是定義一組**硬體無關的 trait**，讓 driver 只依賴 trait、不依賴具體晶片。一個感測器 driver 泛型於「任何實作了 `I2c` trait 的東西」，就能在任何晶片上跑。用純 Rust（userland）示範這個形狀（**本機 `rustc 1.97.1` 真跑**，模擬真實 `embedded-hal` 的 `I2c` trait）：

```rust
// embedded-hal 風格的抽象 trait（真實 I2c trait 的概念簡化版）
trait I2c {
    type Error;
    fn write_read(&mut self, addr: u8, wr: &[u8], rd: &mut [u8]) -> Result<(), Self::Error>;
}

// 感測器 driver：泛型於任何 I2c，完全不知道底下是哪顆晶片
struct TmpSensor<B: I2c> { bus: B, addr: u8 }
impl<B: I2c> TmpSensor<B> {
    fn new(bus: B, addr: u8) -> Self { Self { bus, addr } }
    fn read_celsius(&mut self) -> Result<i32, B::Error> {
        let mut buf = [0u8; 2];
        self.bus.write_read(self.addr, &[0x00], &mut buf)?;   // 讀溫度暫存器 0x00
        let raw = ((buf[0] as i32) << 8) | (buf[1] as i32);
        Ok(raw >> 8)   // 高 byte = 整數攝氏（Q8.8 定點簡化）
    }
}

// 晶片 A、晶片 B 各自的 HAL 實作同一個 I2c trait（底層不同、介面相同）
struct ChipA;  impl I2c for ChipA { type Error = ();
    fn write_read(&mut self, _:u8, _:&[u8], rd:&mut [u8]) -> Result<(),()> {
        rd[0]=25; rd[1]=0x80; Ok(()) } }               // 模擬回 25.5 度
struct ChipB;  impl I2c for ChipB { type Error = ();
    fn write_read(&mut self, _:u8, _:&[u8], rd:&mut [u8]) -> Result<(),()> {
        rd[0]=30; rd[1]=0x00; Ok(()) } }               // 模擬回 30.0 度

fn main() {
    let mut a = TmpSensor::new(ChipA, 0x48);
    let mut b = TmpSensor::new(ChipB, 0x48);
    println!("chip A temp = {} C", a.read_celsius().unwrap());
    println!("chip B temp = {} C", b.read_celsius().unwrap());
    // 同一個 TmpSensor code 跑在兩顆晶片上——trait 就是那條硬體抽象邊界
}
```

本機真跑輸出：

```
chip A temp = 25 C
chip B temp = 30 C
```

`TmpSensor` 的 code 一個字都沒為「哪顆晶片」而改——它只認 `I2c` trait。換晶片 = 換那個實作 `I2c` 的型別，driver 不動。這就是 `embedded-hal` 讓「一份 driver 跑遍所有晶片」的機制，本質是你 [Ch 10](./10-generics-monomorphization.md) 的泛型 + 單型化：編譯期為 `ChipA`/`ChipB` 各單型化一份 `TmpSensor`，零執行期開銷（不像 C 用 function pointer 表達抽象要付間接呼叫的代價）。真實 `embedded-hal` 還有 async 版 trait（`embedded-hal-async`），配 Embassy 用。

embedded Rust 直接對應本 repo 兩門課：**`architecture/arm`**（Cortex-M 那半邊的硬體與 ISA）和 **`embedded/protocols`**（ESP32 register-level、SPI/I2C/UART/CAN/BLE…）。那兩門課教你 C/暫存器那半邊；embedded Rust 是「用 Rust + `embedded-hal` + Embassy 重做同一件事」的現代路線。

## Rust 在系統領域的未來方向（誠實、簡短）

不誇大，只講有實質進展的幾條。**這節是趨勢判斷，不是既成事實，讀時請對照官方現況。**

1. **kernel 內的 async**：RfL 正在探索把 async 帶進 kernel（例如 Binder 的某些路徑）。這比聽起來難——kernel 沒有 Tokio，需要 kernel 自己的 executor 與 waker，而且 kernel 的執行 context 限制很多（哪些地方能睡、哪些不能，[Ch 41](./41-kernel-unsafe-safety.md) 的 `GFP_KERNEL` vs `GFP_ATOMIC` 就是這個問題的一面）。你 [Ch 27](./27-async-executor-pin.md) 的 `Pin`/executor 知識就是理解這個的基礎——kernel async 面對的正是「Future 不能 move、要 pin 在原地」（[Ch 38](./38-kernel-abstractions.md) 的 pin-init）與「怎麼在沒有 std 的環境驅動 poll」這兩個你已經學過的問題。這還在早期，但方向明確。

2. **更多子系統的 abstraction 穩定化**：目前 RfL 的 kernel crate API **未穩定**（[Ch 38](./38-kernel-abstractions.md)/[Ch 41](./41-kernel-unsafe-safety.md) 反覆強調）。演進的動力來自新 driver 落地——每寫一個新 driver，就會發現需要哪些新抽象、既有抽象哪裡不夠好。目前活躍的方向包括網路 driver、區塊裝置、以及 GPU（Rust 的 Nova driver、以及先前 Asahi Linux 的 Apple GPU Rust driver 是這方向的先行者）。用得多的抽象會逐步穩定，方向是「從實驗性 API 走向可依賴的 API」。但誠實說：**別期待像 C ABI 那樣的長期穩定承諾**。Linus 對 kernel 內部 API 一向的態度是「內部沒有穩定 ABI，需要就改」，Rust 抽象同理。短期內對著你那棵 kernel 樹的原始碼寫、不背 API，仍是常態。

3. **工具鏈與形式化驗證**：這是最能提升 `unsafe` 可信度的方向。`Miri`（[Ch 20](./20-memory-model-ub.md)）已經能在直譯層抓一大類 UB；更進一步是形式化驗證工具（`kani` 用 model checking、`verus` 用 SMT 證明），目標是「機器證明某個 `unsafe` 區塊真的 sound」，而不是靠人讀 `// SAFETY:` 註解判斷。kernel 的 `// SAFETY:` 契約（[Ch 41](./41-kernel-unsafe-safety.md)）目前完全靠人審——這是 RfL 安全模型的最後一道人力關卡。未來若 kernel 的 unsafe 抽象能部分被機器驗證，「這層 API 到底 sound 不 sound」就從「相信維護者審過了」變成「有證明」。這對資安工程師（你）尤其重要：audit unsafe（[Ch 32](./32-audit-unsafe.md)）從純人力往「工具輔助」走。

4. **Rust 語言本身為系統場景演進**：Rust 也在為這些內圈場景加語言特性——例如更完整的 `const` 求值（firmware/embedded 常需要編譯期算好一切）、更好的 `no_std` 生態、以及讓 async trait、GAT 等已穩定的特性在 embedded/kernel 更好用。語言、生態、應用三者互相推動。

**學習資源地圖**（接下來去哪學）：kernel 方向看 `Documentation/rust/` + `samples/rust/` + rust-for-linux.com；embedded 方向看 The Embedded Rust Book + Embassy 官網 + `embedded-hal` docs；VMM 方向看 rust-vmm 各 crate 的文件 + Firecracker 原始碼；生態動態追 LWN（kernel）、This Week in Rust（週報）。這些都在延伸閱讀有連結與說明。**重點是**：這一章給的是地形圖，會過時；你要建立的是「持續追蹤」的習慣，不是「一次讀完」的心態。

## 貫穿全部的一件事：同一套 Rust 核心，換不同宿主

巡完五個圈層，值得停下來看一個模式：**每一圈用的都是你這門課已經學過的同一套 Rust 核心機制**，只是換了執行環境和底層 API。這不是巧合，是 Rust 設計的直接結果——它的核心保證（ownership、`Send`/`Sync`、`Result`、trait、`unsafe` 邊界）不依賴 OS、不依賴 heap、不依賴 runtime，所以能整片搬過去。

| 你學的核心（章節） | 在 kernel（RfL） | 在 VMM | 在 MCU（embedded） | 在 firmware |
|---|---|---|---|---|
| ownership/`Drop`（[Ch 2](./02-ownership-move.md)/[Ch 12](./12-core-traits.md)） | driver 資源自動釋放 | VM 資源/fd 自動釋放 | 週邊 handle 自動釋放 | 資源自動釋放 |
| `Result`/`?`（[Ch 13](./13-error-handling.md)） | 回 errno 給 C | VMM 錯誤處理 | HAL 操作回 `Result` | 開機失敗優雅處理 |
| trait（[Ch 9](./09-traits.md)/[Ch 11](./11-trait-objects-dispatch.md)） | `MiscDevice`/`Module` | virtio 裝置抽象 | **`embedded-hal`** | 韌體驅動抽象 |
| `Send`/`Sync`（[Ch 23](./23-threads-send-sync.md)） | kernel 並發安全 | 多 vCPU 執行緒 | RTIC 資源共享 | （多半單執行緒） |
| async（[Ch 26](./26-async-futures.md)–[Ch 27](./27-async-executor-pin.md)） | kernel async（早期） | tokio-based VMM | **Embassy** | 少用 |
| `unsafe`/FFI 邊界（[Ch 17](./17-unsafe-basics.md)/[Ch 19](./19-ffi.md)） | 呼叫 C kernel API | 呼叫 KVM ioctl | 存取 MMIO 暫存器 | 存取硬體暫存器 |
| `no_std`（[Ch 22](./22-no-std.md)） | kernel 無 std | （多半有 std） | **核心地基** | **核心地基** |

看這張表最右邊三欄——你學 `no_std`、`unsafe` 邊界、trait 時可能覺得抽象，這裡它們是 MCU 和 firmware 的**日常**。你不是為了考試學這些，是為了能在任何一圈工作。這也是為什麼這門課花整整一章（[Ch 22](./22-no-std.md)）講 `no_std`：它是「離開舒適的 std 環境、進入系統最底層」的通行證，kernel、MCU、firmware 三圈共用。

## 對比：Rust 在各系統圈的落地程度

| 圈層 | 代表專案 | 落地程度（2026-08） | 對應本 repo 課 | 你缺的那半邊 |
|---|---|---|---|---|
| 應用工具 | ripgrep, uv, Zed | 成熟、主流 | （本課即是） | — |
| VMM/hypervisor | Firecracker, crosvm, rust-vmm | 成熟、生產 | `security/vm_escape` | KVM/device emu 的攻擊面 |
| OS kernel (Linux) | RfL, Binder (6.18) | 主線、擴張中 | `systems/kernel_internals` | C 那半邊的子系統 |
| OS kernel (Windows) | GDI/DWrite Rust | 廠商生產、逐塊 | `security/windows_kernel_driver` | WDM/KMDF 的 C |
| firmware/boot | oreboot, uefi-rs | 前沿、小眾 | `systems/linux_boot` | 開機流程的組語/C |
| MCU 裸機 | embedded-hal, Embassy, RTIC | 成熟、生態完整 | `architecture/arm`, `embedded/protocols` | 暫存器/ISA 那半邊 |

這張表是本章的核心產出：**你手上這套 Rust 系統能力，能接到六個不同的圈層**，而每個圈層都有本 repo 的一門課教你「另外那半邊」（通常是 C/組語/硬體）。你不是學了一個孤立技能。

## 踩雷集錦

1. **以為「Rust 進 kernel = 記憶體安全 = 沒漏洞了」**：錯得離譜。Linux 第一個 Rust CVE（CVE-2025-68260，Binder 的 race）和 Windows Rust GDI 的漏洞都證明：Rust 收掉的是**記憶體安全 bug**（UAF/OOB/double-free），**收不掉**邏輯 bug、設計層的 race、`unsafe`/FFI 邊界的錯誤。把 Rust 當「安全銀彈」是誤解它的價值——它是「消滅一大類最常見漏洞」，不是「消滅所有漏洞」。

2. **以為 embedded Rust = 把 std Rust 搬上 MCU**：不是。MCU 是 `no_std`（[Ch 22](./22-no-std.md)）——沒有 heap（或要自己配）、沒有 OS、沒有 `std`。你用的是 `core` + `embedded-hal` + 晶片 HAL。以為能直接 `use std::collections::HashMap` 會撞牆。這是為什麼 [Ch 22](./22-no-std.md) 的 `no_std` 是 embedded 和 kernel **共同**的地基。

3. **以為 async 只能配 Tokio / 只用在網路**：Embassy 打臉這個。同一套 `Future`/`poll`/`Waker`（[Ch 26](./26-async-futures.md)/[Ch 27](./27-async-executor-pin.md)），換掉 reactor 就能在無 OS 的 MCU 上做 async。async 是**通用的協作式多工模型**，不是網路專利。把 async 和 Tokio/epoll 綁死，會看不懂 Embassy 在幹嘛。

4. **以為 kernel crate API 現在穩定了、可以背**：[Ch 38](./38-kernel-abstractions.md)/[Ch 41](./41-kernel-unsafe-safety.md) 講過，這章再強調一次——**未穩定，版本間會變**。即使 Binder 進了主線，它依賴的抽象仍在演進。永遠對著你那棵 kernel 樹的 `rust/kernel/` 原始碼寫，不要背 API 簽章。

5. **以為 Windows 走 RfL 那條路 / 以為兩者做法一樣**：不一樣。Linux 是社群主導 + RfL 這個統一框架 + 開源 sample；Windows 是**微軟內部**逐塊重寫 high-blast-radius 元件（GDI、DirectWrite），不是一個對外的框架。結論相同（kernel 該用 memory-safe 語言），路徑完全不同。

6. **以為 embedded Rust 就是「Embassy 一套打天下」**：不是。這一圈有多個層次、多個框架並存：底層是 `cortex-m`/`riscv` + PAC + `embedded-hal`（這幾乎是所有人的共同地基），上面 RTIC 和 Embassy 是**兩種不同的並發模型**（RTIC=中斷優先權、Embassy=async），適用場景不同（硬即時多用 RTIC，I/O 密集多用 Embassy），也能混用。以為「學 Embassy = 學會 embedded Rust」會漏掉整個 `embedded-hal`/HAL/PAC 的地基，那才是真正硬體無關可攜的關鍵。

7. **以為 VMM 用 Rust 就沒有 VM escape 風險**：降低不等於消除。VMM 的攻擊面在**解析 guest 給的資料**——virtio queue 的描述符、MMIO 的存取、DMA 的位址。Rust 擋掉「解析時 buffer overflow」這類記憶體 bug，但**邏輯漏洞仍在**：例如 virtio 描述符的邊界檢查邏輯寫錯（讓 guest 讀到不該讀的 host 記憶體），這是邏輯 bug，Rust 的型別系統擋不了。這也是為什麼 `security/vm_escape` 那門課仍然值得學——攻擊者找的正是這種 Rust 擋不到的縫。

## 進階：再往深一層

- **讀 Binder Rust driver 的真實 code**：主線 6.18+ 的 Rust 版 Binder（`drivers/android/binder/`）是目前最好的「大型生產 RfL driver」教材。帶著你 [Ch 40](./40-rust-driver.md) 的 misc device 骨架去讀，看它怎麼把同一套抽象（`MiscDevice` 概念、`UserSlice`、`Mutex`、`ForeignOwnable`）撐到一個完整 IPC 子系統。這是「從 hello driver 到生產 driver」的最佳跳板。
- **追一個 RUSTSEC / kernel Rust CVE 的完整生命週期**：拿 CVE-2025-68260（Binder race）當案例，讀它的根因分析（延伸閱讀有連結），問自己：「Rust 為什麼沒擋掉這個？」答案會加深你 [Ch 30](./30-security-boundary.md) 對「Rust 的安全邊界到底在哪」的理解——這是資安工程師比一般 Rust 開發者更該想清楚的問題。
- **在 QEMU 上跑一個 Embassy 範例**：Embassy 有能在 QEMU（模擬 Cortex-M）上跑的範例。這是把 [Part 4](./26-async-futures.md) 的 async 從「server 概念」變成「你親眼看到它在模擬 MCU 上多工」的最省事路徑，不用買開發板。
- **面試/定位角度**：能講清楚「Rust 在系統領域的落地版圖（至少四個圈層）」「Rust 消滅哪類 bug、消滅不了哪類（舉真實 CVE）」「async 為什麼不是網路專利（Embassy）」「kernel crate 為什麼還不穩定」，你就不只是「會寫 Rust」，而是**理解 Rust 在系統世界的位置與邊界**——這是資深系統/資安工程師和初學者的分水嶺。

## 動手練習

這章沒有 code 要跑，但有幾件「動手定位自己」的事——比讀更重要，因為這章的目的是讓你**找到方向**，不是背事實。

1. **跑一次本章的兩段驗證 demo**：把「同一套 Rust 核心」那節的 `embedded-hal` trait demo（`TmpSensor` + `ChipA`/`ChipB`）自己 `rustc` 跑一遍，然後**加第三顆晶片 `ChipC`**（回不同溫度），確認 `TmpSensor` 的 code 一個字都不用改。這讓你親手體會「trait 就是硬體抽象邊界」——這正是 `embedded-hal` 讓 driver 可攜的機制。

2. **讀一段真實的大型 RfL code**：打開主線 kernel 樹 6.18+ 的 Rust Binder（`drivers/android/binder/`），找到它的 `ioctl` 處理或 transaction 路徑，對照你 [Ch 40](./40-rust-driver.md) 的 misc device——找出「一樣的抽象」（`Mutex`、`UserSlice`、`Arc`/`ForeignOwnable`）出現在哪。你不需要看懂全部，目標是確認「我學的那套，真的就是生產 driver 用的那套」。

3. **拿 CVE-2025-68260 做一次「Rust 為什麼沒擋掉」的分析**：讀它的根因（延伸閱讀有連結），在紙上寫下：這是哪一類 bug（記憶體安全 / 邏輯 / race）？Rust 的哪個機制**本該**擋這類但沒有（`Send`/`Sync` 擋 data race，但擋不了什麼樣的 race）？做完這題，你對 [Ch 30](./30-security-boundary.md) 的「安全邊界」會有實感，而不是口號。

4. **畫你自己的能力地圖**：不看下面那張表，憑記憶把六個 Part 各寫一句「我現在會什麼」，再對照下表補漏。這是主動回憶，比讀表有效十倍——也順便檢查全課哪個 Part 你其實心虛，回去補。

## 全課收尾：你現在站在哪，下一步往哪走

走完 42 章，把你的能力收成六個 Part 的地圖：

| Part | 你現在會的 | 這對應的「C 隱性知識」 |
|---|---|---|
| **Part 1（所有權）** | ownership/borrow/lifetime 底層在管什麼、borrow checker（NLL/Polonius）怎麼運作 | 你在 C 靠紀律避免的 UAF、iterator invalidation、懸空指標 |
| **Part 2（型別系統）** | trait/泛型/單型化/trait object、`Result`/`Option`/`?`、閉包 | C 的 vtable、手動 dispatch、errno 傳播、function pointer |
| **Part 3（佈局與 unsafe）** | `repr`/niche、smart pointer 底層、`unsafe` 五 superpower、FFI、Stacked/Tree Borrows、`no_std` | C 的記憶體佈局、ABI、UB、與 C 互操作 |
| **Part 4（並發/async）** | `Send`/`Sync`、`Mutex`/atomics/Ordering、`Future`/executor/`Pin`、Tokio | C 的 pthread、memory_order、epoll 事件迴圈、手刻狀態機 |
| **Part 5（資安）** | Rust 威脅模型、`unsafe` 漏洞類、audit（cargo-geiger/audit）、逆向 Rust binary、Fuzzing | 你逆向 C binary、audit C code 的直覺，移植到 Rust |
| **Part 6（kernel）** | RfL 架構、kernel crate 抽象、pin-init、寫 misc device driver、kernel unsafe | 你寫 C kernel module 的一切，加上型別強制的契約 |

**你現在能做的**：讀懂並貢獻 Rust 系統專案的 code、audit 別人的 `unsafe`、逆向 Rust binary、用 RfL 寫一個真的 kernel driver（Final Project 就是）、把 C 系統知識和 Rust 對接。

**下一步往哪走**（指回本 repo 相鄰課，按你的興趣挑）：

- **想深挖 kernel 的 C 那半邊** → **`systems/kernel_internals`**（Linux 6.12 源碼導讀，排程/mm/RCU/VFS/驅動——RfL driver 背後的子系統）。這是把你 Part 6 的「Rust 那半邊」補齊成「完整 kernel 理解」的正路。
- **想把 Fuzzing 推深** → **`security/afl_plus_plus`**（AFL++ 引擎那半邊；本課 [Ch 36](./36-fuzzing-rust.md) 教 cargo-fuzz/AFL++ 的 Rust 前端）。
- **想打 VM escape / 理解 hypervisor 攻擊面** → **`security/vm_escape`**（QEMU/KVM device emulation 當 exploit 原語；本章的 rust-vmm/Firecracker 是同一戰場的防守側）。
- **想做 embedded / MCU** → **`architecture/arm`**（Cortex-M 硬體與 ISA、mini RTOS）+ **`embedded/protocols`**（ESP32 register-level 通訊協定）。這兩門補齊 embedded Rust 的硬體那半邊。
- **想補系統 C 底子 / 面試** → **`programming/c_interview`**（UB/記憶體/ABI/lock-free，mini libc）。你學 Rust 時反覆對照的 C 概念，這門課從 C 側系統化。

這門課的核心信條，從 [Ch 1](./01-why-rust.md) 到這裡沒變過：**Rust 不是要取代你的 C/C++ 知識，是把你那些靠紀律維持的隱性知識，變成編譯器強制的顯性規則。** 你走完全課，得到的不是「又一個語言」，是**一套讓你的系統與資安能力延伸到 memory-safe 世界的橋**——而那個世界，正如這一章所示，已經從應用工具一路滲透到 kernel、firmware、MCU 的每一圈。

## 本章重點整理

- Rust 在系統領域「由外往內滲透」：應用工具（成熟）→ VMM/hypervisor（Firecracker/crosvm/rust-vmm，生產）→ OS kernel（Linux RfL + Binder 6.18；Windows GDI/DWrite）→ firmware（oreboot/uefi-rs）→ MCU（embedded-hal/Embassy/RTIC）。每一圈都用你學過的同一套 Rust 核心。
- **Rust 不是安全銀彈**：Linux 首個 Rust CVE（CVE-2025-68260，Binder race）和 Windows Rust GDI 漏洞都證明——Rust 消滅記憶體安全 bug，消滅不了邏輯 bug、設計層 race、`unsafe`/FFI 邊界錯誤。這是資安工程師必須內化的邊界。
- **embedded Rust 生態成熟**：`embedded-hal` 1.0（穩定 trait 地基）+ 晶片 HAL + RTIC/Embassy。Embassy 把 [Part 4](./26-async-futures.md) 的 async（`Future`/`poll`/`Waker`）搬上無 OS 的 MCU——async 是通用多工模型，不是網路專利。
- 每個系統圈層都對應本 repo 的一門課（教你「另外那半邊」的 C/組語/硬體）：kernel→`kernel_internals`、VMM→`vm_escape`、fuzzing→`afl_plus_plus`、MCU→`arm`/`embedded/protocols`、C 底子→`c_interview`。
- 全課六個 Part 的能力，本質是把 C 的隱性知識（靠紀律避免的 bug、手動 dispatch、errno 傳播、記憶體佈局、pthread、kernel module 慣例）變成 Rust 型別系統強制的顯性規則。你得到的是延伸到 memory-safe 世界的橋。

## 自我檢核

- [ ] 不看筆記，能說出 Rust 在系統領域至少**四個**落地圈層，各舉一個代表專案。
- [ ] 能用一個真實 CVE（Binder race 或 Windows GDI）解釋「Rust 消滅哪類 bug、消滅不了哪類」——如果面試官問「Rust 進 kernel 是不是就沒漏洞了」，你會怎麼答？
- [ ] 能解釋為什麼 async（Embassy）能跑在無 OS 的 MCU 上，這和你 [Ch 27](./27-async-executor-pin.md) 手刻的 executor 是什麼關係。
- [ ] 能說出 Linux（RfL 社群框架）和 Windows（微軟內部逐塊重寫）引入 Rust 的路徑差異，以及它們的共同結論。
- [ ] 想一個你現在工作/興趣中的系統或資安場景，說出「這門課的哪些能力用得上、下一步該補本 repo 的哪門課」。

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、和本章的關聯。生態類資源會過時，讀時對照發表日期。

### 官方 / 一手來源

- **[Android Binder Driver — Rust for Linux](https://rust-for-linux.com/android-binder-driver)** 與 **[A Rust implementation of Android's Binder（LWN）](https://lwn.net/Articles/953116/)**
  - **讀哪裡**：RfL 官網的 Binder 頁（目標與現況）；LWN 那篇的設計動機段（為什麼用 Rust 重寫 Binder、遇到哪些抽象挑戰）。
  - **學到什麼**：本章「Android Binder」那節的一手依據；Binder 怎麼用你 [Ch 40](./40-rust-driver.md) 的同一套抽象撐起一個完整 IPC 子系統。
  - **前提**：讀完 [Ch 40](./40-rust-driver.md)/[Ch 41](./41-kernel-unsafe-safety.md)；帶著「這和 misc device 差在規模與狀態機」的問題去讀。

- **[The state of the kernel Rust experiment（LWN, 2025）](https://lwn.net/Articles/1050174/)**
  - **讀哪裡**：2025 Maintainers Summit 那節——RfL「實驗」被判定成功的結論、Binder 進主線、Android 出貨 Rust ashmem 的現況。
  - **學到什麼**：本章 RfL 狀態斷言（6.18 Binder、Android 16/6.12 ashmem）的權威來源；kernel 社群對 Rust 的真實態度。
  - **前提**：無；這是了解「RfL 現在到哪了」的最佳單篇。

- **[embedded-hal / Embassy crates released and stable support（Embassy blog）](https://embassy.dev/blog/embassy-hals-released/)** 與 **[Embassy 官網](https://embassy.dev/)**
  - **讀哪裡**：blog 講 `embedded-hal` 1.0 + Embassy 可用 stable（1.75+）編的那段；官網首頁的架構概觀。
  - **學到什麼**：本章 embedded 分層地圖與 Embassy/`embedded-hal` 狀態斷言的來源；async on MCU 怎麼組起來。
  - **前提**：[Part 4](./26-async-futures.md)（尤其 [Ch 27](./27-async-executor-pin.md) 的 executor/`Pin`）+ [Ch 22](./22-no-std.md) 的 `no_std`。

### 資安 / CVE 分析

- **[CVE-2025-68260: The Linux Kernel's First Rust-Code CVE in rust_binder](https://www.penligent.ai/hackinglabs/cve-2025-68260-the-linux-kernels-first-rust-code-cve-in-rust_binder-root-cause-exposure-checks-and-fix-strategy/)**
  - **讀哪裡**：root cause 那節——這個 race condition 怎麼發生、Rust 為什麼沒擋掉。
  - **學到什麼**：本章「Rust 不是銀彈」最具體的一手案例；把 [Ch 30](./30-security-boundary.md) 的「安全邊界在哪」變成一個真實漏洞來讀。
  - **前提**：[Ch 30](./30-security-boundary.md) 對 Rust 威脅模型的理解 + [Ch 23](./23-threads-send-sync.md) 的 `Send`/`Sync`（理解為什麼 Rust 擋 data race 但擋不了這種 race）。

- **[Denial of Fuzzing: Rust in the Windows kernel（Check Point Research, 2025）](https://research.checkpoint.com/2025/denial-of-fuzzing-rust-in-the-windows-kernel/)**
  - **讀哪裡**：他們在新的 Rust 版 GDI 元件裡找漏洞的過程與那個 bug 的性質。
  - **學到什麼**：本章「Windows Rust GDI 也出過漏洞」的來源；資安研究者視角看「Rust kernel code 的攻擊面在哪」——`unsafe`/FFI 邊界仍是重點。
  - **前提**：[Ch 32](./32-audit-unsafe.md)（audit unsafe）+ [Ch 33](./33-reversing-rust-binary.md)（逆向 Rust binary 的直覺）。

- **[A proactive approach to more secure code（MSRC blog, 2019）](https://msrc.microsoft.com/blog/2019/07/a-proactive-approach-to-more-secure-code/)** — 微軟 Matt Miller
  - **讀哪裡**：開頭那個「~70% 的 CVE 是記憶體安全問題」的統計圖與說明。
  - **學到什麼**：本章「為什麼主流 OS 現在集體轉 memory-safe 語言」那個 ~70% 數字的一手來源；理解 Rust 進 kernel 的商業/安全理性動機，而不只是「Rust 潮」。
  - **前提**：無；這是整個「memory safety 為什麼重要」論述的原始數據。

### 生態 / 專案

- **[rust-vmm community](https://github.com/rust-vmm/community)** 與 **[Firecracker 官網](https://firecracker-microvm.github.io/)**
  - **讀哪裡**：rust-vmm 的 README（它提供哪些可重用 VMM 元件、哪些專案共用）；Firecracker 的 design/FAQ（為什麼是純 Rust、最小裝置模型）。
  - **學到什麼**：本章 hypervisor 那節的來源；VMM 為什麼幾乎清一色 Rust（VM escape 攻擊面 → memory safety 收益最大）。
  - **前提**：[Ch 19](./19-ffi.md)（FFI 邊界）；想連攻擊視角就配本 repo `security/vm_escape` 課。

- **[This Week in Rust](https://this-week-in-rust.org/)**（週報）與 **[LWN Kernel index](https://lwn.net/Kernel/)**
  - **讀哪裡**：This Week in Rust 追整個 Rust 生態動態（含 embedded/kernel）；LWN 追 kernel（含 RfL）的深度報導。
  - **學到什麼**：本章刻意標「會過時」——這兩個是你**持續更新**這張生態地圖的正路，不是一次讀完。
  - **前提**：無；當長期訂閱來源。

### 書籍

- **《The Embedded Rust Book》** — Rust Embedded WG（線上免費，doc.rust-lang.org/embedded-book）
  - **這本書的定位**：embedded Rust 的官方入門，本章 embedded 那節的下一步深入。涵蓋 `no_std`、PAC/HAL、中斷、`embedded-hal`。
  - **讀哪幾章**：前幾章（`no_std` 環境、記憶體映射 I/O、中斷）與本章分層地圖直接對應；配 [Ch 22](./22-no-std.md) 讀。

---

全課到此。你從 [Ch 0](./00-environment-setup.md) 的「C/C++ 對照心智」出發，走過所有權、型別系統、unsafe 與佈局、並發與 async、資安研究，最後在 Rust-for-Linux 寫真 kernel driver——現在你站在一片正在擴張的版圖上，手裡有橋通往它的每一圈。

接下來把整條 Part 6 收束成一件作品：**用 Rust-for-Linux 寫一個有實際功能的字元裝置 kernel module（XOR cipher device）**，整合全課至少七成的核心概念——ownership、`Result` 錯誤處理、trait（`MiscDevice`）、`Mutex`/同步、`Pin`/`KBox`、`no_std`、`unsafe` 邊界（`UserSlice`）、FFI 概念、`module!`。這是把「讀懂 RfL」變成「寫出 RfL」的最後一哩。

→ [Final Project：用 Rust-for-Linux 寫字元裝置 kernel module](./final-project-kernel-module.md)
