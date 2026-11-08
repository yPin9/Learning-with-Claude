<p align="center">
  <img src="assets/logo.svg" width="112" alt="Project 200 logo">
</p>

<h1 align="center">Project 200</h1>

<p align="center">我跟 Claude 一起亂學東西的地方。</p>

<p align="center">
  <img src="https://img.shields.io/badge/courses-82-2ea44f?style=flat-square" alt="82 courses">
  <img src="https://img.shields.io/badge/lang-繁體中文-d73a49?style=flat-square" alt="繁體中文">
  <img src="https://img.shields.io/badge/written_with-Claude_Code-d97757?style=flat-square" alt="Written with Claude Code">
</p>

---

以 Claude 為協作夥伴撰寫的繁體中文技術教材，涵蓋系統、資安、編譯器、計算機架構與 AI 等領域。每門課程包含循序漸進的章節、階段練習與一個總結專案，內容盡可能經過實際執行驗證。

## 索引

### algorithms/

- [leetcode_patterns](./algorithms/leetcode_patterns/README.md)
  - 演算法面試，用 C++ 寫
  - 從遞迴的直覺開始，一路到 DP、圖、貪婪
  - 最後 30 題限時衝刺

### math/

- [graph_theory](./math/graph_theory/README.md)
  - 圖論加一點離散數學，證明跟 Python 實作一起來
  - 自己刻演算法，再拿 NetworkX 對答案
  - 後面接到 register allocation、攻擊圖、binary 相似度，最後做一套圖分析工具 graphlab

### ai/

- [ai_applications](./ai/ai_applications/README.md)
  - 用 Claude 做東西：prompting、MCP server、agent
  - RAG、eval、observability 這些 LLM 工程
  - 最後把一個產品真的做出來上線
- [ai_bdd](./ai/ai_bdd/README.md)
  - 照「AI x BDD」課綱重新整理 agent 和規格相關的東西
  - agent 迴圈、skill、Spec Kit、TDD、Playwright、pytest-bdd
  - 最後做一條會議室預約的流水線
- [harness_engineering](./ai/harness_engineering/README.md)
  - AI agent 的執行框架是怎麼組起來的
  - agent loop、context 管理、工具、subagent、權限、eval
  - 最後自己刻一個 mini harness
- [pstack](./ai/pstack/README.md)
  - 拆 Cursor 的 pstack 工作流，搬到 Claude Code 上實作
  - playbook、多模型交叉檢查、驗證跟決策紀錄
  - 最後替這個 repo 做一個 course-mode
- [spec_driven_development](./ai/spec_driven_development/README.md)
  - 規格驅動開發加 DDD，跟 AI 協作時規格怎麼寫
  - 需求工程（EARS、BDD）、Spec Kit 和 Kiro 實際用過一輪
  - 最後走完一次完整的 SDD 流程

### architecture/

- [riscv](./architecture/riscv/README.md)
  - RV32I 到 V/B 擴充，還有 custom extension
  - 手動解碼、在 spike 上跑 bare-metal、Sv48 分頁
  - 最後自己寫 RV32I 和 RV64I 模擬器
- [arm](./architecture/arm/README.md)
  - Cortex-A 和 Cortex-M 兩條線，從 ISA 看到 JTAG
  - 最後寫 STM32 韌體和一個 Cortex-M3 的小 RTOS
- [cpu_design](./architecture/cpu_design/README.md)
  - 用 SystemVerilog 從單週期寫到五級 pipeline
  - 分支預測、cache、虛擬記憶體、中斷都做
  - verilator 跑模擬，跟 spike 逐指令對答案

### parallel/

- [gpu_cuda](./parallel/gpu_cuda/README.md)
  - 先看 CPU 的 SIMD 和 OpenMP，再進 GPU 架構和 CUDA
  - 優化是重點：coalescing、bank conflict、reduction，往下看到 PTX/SASS
  - 最後手刻 GEMM 和 mini FlashAttention，用 Colab T4 跑

### compilers/

- [compiler_frontend](./compilers/compiler_frontend/README.md)
  - flex + bison 寫 MiniC 的前端
  - tokenizer、parser、AST、符號表、型別檢查
  - 這門課是其他課的格式範本
- [compiler_backend](./compilers/compiler_backend/README.md)
  - LLVM 的 RISC-V backend：SelectionDAG、TableGen、scheduler、MC
  - 練習讀 pass、加 pseudo instruction
  - 最後幫 backend 加一個擴充指令
- [elf_linking](./compilers/elf_linking/README.md)
  - relocation、linker script、RISC-V relaxation
  - 最後寫一個 mini linker
- [ssa_optimizations](./compilers/ssa_optimizations/README.md)
  - SSA 理論：dominator tree、SCCP、GVN、SCEV
  - 在 LLVM 上寫 pass，包含過程間分析
  - 最後寫一個小 optimizer，用 CSmith 驗證

### dev_tools/

- [git](./dev_tools/git/README.md)
  - 給已經會 add/commit/push、想真的用熟的人
  - hooks、worktree、救災、整理 commit
  - 最後用 bisect 抓一個 regression
- [open_source](./dev_tools/open_source/README.md)
  - rebase、衝突、reflog 這些中階 git
  - fork、PR、review、CI，還有維護者的角度
  - 最後真的去開源專案送 PR
- [cicd](./dev_tools/cicd/README.md)
  - Docker + GitHub Actions
  - 練習救爛 Dockerfile、PR 上跑 CI、加安全檢查
  - 最後把一個 FastAPI + Postgres 服務做到可以上線
- [docker](./dev_tools/docker/README.md)
  - 從 `docker run` 到上線
  - 底層的 namespace、cgroup、OverlayFS，加上 Compose、hardening、Swarm
  - 最後做一條 CI pipeline
- [neovim](./dev_tools/neovim/README.md)
  - 把 Neovim 調成讀大型 C 專案的工具，config 自己寫
  - motion → telescope → treesitter → clangd → gtags 一路串起來
  - 全部在 WSL 的 nvim 0.12 上實際跑過

### databases/

- [database_internals](./databases/database_internals/README.md)
  - 用 Rust 從零寫一個關聯式資料庫
  - B+tree 和 LSM 兩種引擎都做，加上 WAL、crash recovery、MVCC
  - SQL parser、執行器、優化器也自己寫，可以跑真的 SQL

### programming/

- [algorithms](./programming/algorithms/README.md)
  - 面試用，從背 pattern 改成懂原理，Python
  - 線性結構、樹和圖、DP 分段練
  - 最後整理一張自己的 pattern 地圖
- [modern_cpp](./programming/modern_cpp/README.md)
  - 會 C 的人速成 C++20，看得懂也寫得出現代寫法
  - RAII、自己實作 unique_ptr、ranges
  - 最後用 coroutine 寫一個 TCP echo server
- [sat_smt](./programming/sat_smt/README.md)
  - 從命題邏輯開始，用 C++20 寫
  - Tseitin 轉換、DPLL、CDCL、EUF 一步步做
  - 最後寫一個 mini SMT solver，不把 Z3 當黑盒子
- [c_interview](./programming/c_interview/README.md)
  - C 面試：記憶體、UB、ABI、嵌入式、效能、lock-free
  - 最後寫一個 mini libc
- [rust](./programming/rust/README.md)
  - 給會 C/C++ 的人學 Rust，全程跟 C/C++ 對照
  - ownership 底層、unsafe、FFI、自己寫 async executor，也看怎麼逆 Rust binary
  - 最後寫一個 Rust-for-Linux 的字元裝置 kernel module

### security/

- [gdb](./security/gdb/README.md)
  - GDB 全部功能用到熟
  - 往下看 ptrace 和 DWARF，再學 Python API
  - 最後自己寫 mini debugger 和 gef 風格的插件
- [ida_pro](./security/ida_pro/README.md)
  - IDA 9.x，從只會按 F5 到寫 IDAPython
  - 練習只用鍵盤解 crackme、自動加註解
  - 最後寫一個脫殼輔助工具
- [reverse_engineering](./security/reverse_engineering/README.md)
  - 沒有 source 的時候怎麼讀 binary，主力 x86-64 ELF
  - 重點是認出編譯器產生的固定寫法，靜態和動態都練
  - 自己寫、自己編、strip 掉再逆回來對答案
- [afl_plus_plus](./security/afl_plus_plus/README.md)
  - AFL++ 內部怎麼運作，從 bitmap 看到 CmpLog
  - 比較不同插樁方式、整理 corpus 和 crash
  - 最後對一個真的目標跑一輪
- [advanced_fuzzing](./security/advanced_fuzzing/README.md)
  - AFL++ 之後的 fuzzing，工具看目標選
  - LibAFL、文法和有狀態 fuzzing、syzkaller、Nyx、Fuzzilli、hybrid
  - 最後對真的開源專案跑一個 campaign
- [symex_taint](./security/symex_taint/README.md)
  - 符號執行和 dynamic taint analysis
  - 用 KLEE 找 bug、angr 解 CTF、Triton 做 taint
  - 最後自己寫一個 concolic executor
- [code_auditing](./security/code_auditing/README.md)
  - 原始碼審計，CodeQL、Semgrep、Joern、weggli 一起用
  - C/C++ 的記憶體問題，加上 Java、JS、Python 的 web sink
  - 最後從一個 CVE 抽出 pattern，去找同類型的洞
- [pentest](./security/pentest/README.md)
  - 滲透測試的工具、思路，跟白帽該有的觀念
  - 從偵察走到後滲透，最後寫報告、複測結案
- [kernel_pwn](./security/kernel_pwn/README.md)
  - Linux kernel pwn，目標是 Google kernelCTF
  - stack overflow、UAF、cross-cache、nftables 一路練上去
  - 最後重現一題 kernelCTF
- [owasp](./security/owasp/README.md)
  - OWASP Top 10 2025，附 2021 的對照
  - API Top 10、CVE 案例、WAF 和 RASP
  - 紅藍隊演練
- [web_exploitation](./security/web_exploitation/README.md)
  - 比 owasp 深，重點是把小漏洞串成完整攻擊鏈
  - SSRF、反序列化、request smuggling、OAuth/SAML、prototype pollution、SSTI
  - 在 PortSwigger 和 Docker 靶場練，最後寫一份完整報告
- [malware_analysis](./security/malware_analysis/README.md)
  - PE/ELF 開始，學會對付反分析手法
  - 注入、C2、勒索軟體、Linux rootkit、.NET 和 Cobalt Strike beacon
  - Volatility 記憶體鑑識，寫 YARA 和 Sigma 規則
- [cryptography](./security/cryptography/README.md)
  - 從 GF(2⁸) 開始，對稱、公鑰、AEAD、後量子
  - 手刻 AES、RSA、Kyber，順便打 Bleichenbacher、Logjam、Heartbleed
  - 最後做一個 mini TLS 1.3
- [ai_security](./security/ai_security/README.md)
  - AI 資安工程師面試準備
  - prompt injection、jailbreak、RAG 投毒、agent 劫持，還有 NeMo Guardrails 這類防護
  - 最後做一次紅隊評測
- [oscp_prep](./security/oscp_prep/README.md)
  - OSCP 備考，從新手到拿證
  - buffer overflow、AD 三台機器的攻擊鏈、HTB/THM 機器推薦
  - 最後 24 小時模擬考
- [windows_kernel_driver](./security/windows_kernel_driver/README.md)
  - Windows 驅動開發：WDM、KMDF、Minifilter、WFP
  - 核心安全：BYOVD、token 竊取、反 EDR、VBS/HVCI
  - 最後做一個小 EDR
- [binary_exploitation](./security/binary_exploitation/README.md)
  - userland pwn，kernel_pwn 的前一站
  - stack、ROP、format string、glibc heap、沒有 hook 之後的 FSOP
  - 全部在 glibc 2.39 上實測，最後重現 pwnable.tw 的題目
- [browser_pwn](./security/browser_pwn/README.md)
  - V8 從物件模型和 JIT 管線看起
  - addrof/fakeobj、TurboFan 型別混淆、繞過 V8 sandbox
  - 自己編 V8 15.3 實際跑，最後把一個真的 CVE 寫成 exploit
- [windows_pwn](./security/windows_pwn/README.md)
  - Windows 使用者態的漏洞利用，內部機制和攻擊各佔一半
  - PEB/TEB、NT heap、SEH、ROP，重點是 CFG、CET 這些現代緩解怎麼繞
  - 最後寫一條繞過所有緩解的 exploit chain
- [android_reversing](./security/android_reversing/README.md)
  - APK、DEX、smali 還原 App 邏輯
  - Frida、native .so、ARM64、脫殼和反調試
  - 往下看到 ART，最後拆一個有完整防護的 App
- [android_app_vuln](./security/android_app_vuln/README.md)
  - Android App 漏洞，照 OWASP MASVS/MASTG 走，bug bounty 導向
  - 元件和 IPC 濫用、Intent 重導、WebView、儲存和加密誤用
  - 最後寫一份 MASTG 評估報告
- [android_exploitation](./security/android_exploitation/README.md)
  - 把 Linux pwn 的技能搬到 Android
  - scudo heap、PAC/BTI/MTE、Binder、SELinux、kernel 驅動
  - 最後從 patch diff 寫出穩定的 exploit
- [ios_macos_exploitation](./security/ios_macos_exploitation/README.md)
  - Mach-O、Obj-C、Swift 逆向，lldb 和 Frida
  - App 沙箱、XPC、TCC，往下到 XNU、IOKit、越獄原理
  - 教材是在 Windows 上寫的，工具輸出沒在 Mac 上實測過的都有標，最後寫一份 CVE 鏈的研究報告
- [cloud_container_security](./security/cloud_container_security/README.md)
  - 雲端紅隊，以 AWS 為主，對照 Azure 和 GCP
  - IAM 提權、metadata SSRF、容器逃逸、K8s（從零教）、CI/CD 供應鏈
  - 最後補防守端的偵測，做一次完整的紅隊演練
- [vm_escape](./security/vm_escape/README.md)
  - 從 VM 裡面打穿 hypervisor，QEMU/KVM 為主，也看 VirtualBox 和 VMware
  - VT-x 和 EPT 原理，把模擬裝置當成攻擊入口
  - 復刻 VENOM 和 virtio 的 CVE，最後做一次完整逃逸
- [microarch_attacks](./security/microarch_attacks/README.md)
  - 把 CPU 的效能優化反過來當洩密管道
  - Flush+Reload、Spectre、Meltdown、Rowhammer，再補一個防禦 Part
  - 能跑的都在 i7-10700 上跑過，硬體已經修掉的會標出來
- [firmware_security](./security/firmware_security/README.md)
  - UEFI 和 Secure Boot 的漏洞研究，x86 和 ARM 都看
  - SMM、BootGuard、BlackLotus 這類繞過鏈、TPM 2.0
  - 用 QEMU + OVMF 練，最後寫一份攻防研究報告
- [blue_team_dfir](./security/blue_team_dfir/README.md)
  - 換到防守方，拿前面的攻擊課當靶
  - 偵測規則（Sysmon、Sigma）、Windows 鑑識、Linux 和雲端事件處理、威脅獵捕
  - 最後調查一整起入侵事件
- [sbom](./security/sbom/README.md)
  - SBOM 是什麼、為什麼需要（Log4Shell、SolarWinds）
  - SPDX、CycloneDX、syft、grype、VEX，再到 sigstore 和 SLSA
  - 最後做一條端到端的供應鏈 pipeline

### soft_skills/

- [how_to_learn](./soft_skills/how_to_learn/README.md)
  - 怎麼學東西，其他課之前可以先看
  - 刻意練習、卡關怎麼辦、怎麼知道自己真的學會
- [chinese_writing](./soft_skills/chinese_writing/README.md)
  - 敘事散文和小說
  - 該讀誰、從模仿到原創、怎麼修改
- [industry_analysis](./soft_skills/industry_analysis/README.md)
  - 怎麼拆解一個不熟的產業，到能做投資判斷
  - 拿半導體、SaaS、消費品當案例
- [chess](./soft_skills/chess/README.md)
  - 戰術、殘局、中局判斷
  - 練習各 50 題
  - 最後下 5 盤 rapid 自己復盤
- [go](./soft_skills/go/README.md)
  - 圍棋從零到業餘高段
  - 死活、手筋、棋形是重點
  - 用 KataGo 復盤，最後 50 盤升段
- [reading_code](./soft_skills/reading_code/README.md)
  - 怎麼讀大型、陌生的 codebase，借用逆向的思路
  - rg、ctags、clangd、tree-sitter、gdb 一起用
  - 全程拿 redis 練
- [codebase_case_studies](./soft_skills/codebase_case_studies/README.md)
  - reading_code 的實戰篇，限時讀六個專案
  - Lua、SQLite、nginx、git、CPython、PostgreSQL
  - 每個專案整理出可以帶走的設計 pattern

### debate/

- [chinese_debate](./debate/chinese_debate/README.md)
  - 華語辯論，從零開始，目標 2027 新國辯
  - 立論、攻防、自由辯、結辯、怎麼研究辯題
  - 最後整理一份完整的備賽手冊

### automation/

- [n8n](./automation/n8n/README.md)
  - 視覺化 workflow、Code Node、REST 和 Webhook、self-host
  - 接 AI Agent
  - 最後架一個自己的自動化中樞

### embedded/

- [protocols](./embedded/protocols/README.md)
  - ESP32 上的 SPI、I2C、UART、RS-485、Modbus、CAN、BLE、LoRa、Zigbee、USB、Ethernet
  - 直接操作暫存器，不用 HAL
  - 最後做一個工業閘道器

### interview/

- [mtk_firmware](./interview/mtk_firmware/README.md)
  - 聯發科韌體工程師面試準備
  - C、嵌入式、OS、計組、資料結構的考古題詳解
  - 最後模擬面試

### scripting/

- [powershell](./scripting/powershell/README.md)
  - PowerShell 從零開始
  - 系統管理、AD、PSRemoting、自己寫 module
  - 最後做一套維運工具 SysOpsToolkit

### ml/

- [local_llm](./ml/local_llm/README.md)
  - 預訓練的原理
  - QLoRA 微調、Ollama 部署，CPU 也跑得動
  - 最後做一個地端的繁中小模型

### passive_income_with_ai/

這兩個不是課，是實際在用的做法和腳本。老實說也沒有真的很被動。

- [threads_shopee_affiliate](./passive_income_with_ai/threads_shopee_affiliate/README.md)
  - Threads 加蝦皮分潤，目前的主力
  - 拆解別人的爆文，判斷為什麼會紅
  - 自己發文的流程：產鉤子、留言放連結、發前檢查、追蹤成效
- [seo_shopee_affiliate](./passive_income_with_ai/seo_shopee_affiliate/README.md)
  - SEO 內容站加蝦皮分潤，長期慢慢經營
  - 關鍵字選題打分、AI 產文、上線前的品質檢查、成效追蹤

### languages/

- [english_reading](./languages/english_reading/README.md)
  - 目標是能輕鬆讀 BBC 和技術原文
  - 單字靠高頻詞表，文法只學拆句需要的
  - 手冊刻意寫得薄，重點是每天固定練，最後排一份 8–12 週的閱讀計畫

### systems/

- [bpf](./systems/bpf/README.md)
  - classic BPF 到 eBPF：verifier、CO-RE、libbpf
  - 練習 execve tracer、PostgreSQL 慢查詢、XDP 防火牆
  - 最後寫一個可以上線的 eBPF agent
- [linux_boot](./systems/linux_boot/README.md)
  - x86_64 開機流程，BIOS 和 UEFI 都看
  - 自己做 bootloader、initramfs、最小 Linux
- [observability_tools](./systems/observability_tools/README.md)
  - strace、perf、valgrind 全套
  - 自己寫 mini strace 和 LD_PRELOAD 攔截器
  - 最後當偵探抓 5 個 bug
- [perf_bench](./systems/perf_bench/README.md)
  - perf、benchmark 和編譯器最佳化怎麼互相影響
  - 練習寫 CoreMark 報告、用 llvm-mca 分析
  - 最後做一份效能案例分析
- [yocto](./systems/yocto/README.md)
  - toolchain 工程師速通 Yocto
  - 練習幫套件上 CVE patch
  - 最後把改過的 GCC 放進 BSP
- [networking](./systems/networking/README.md)
  - TCP/IP、VPN、proxy、VPS
  - 順便看 GFW 和翻牆工具一路怎麼演變
  - 最後自己部署一整套
- [linux_commands](./systems/linux_commands/README.md)
  - 先看 VFS、inode、fd 這些底層
  - 再學 shell scripting
  - 最後寫一包維運腳本
- [distributed_systems](./systems/distributed_systems/README.md)
  - 用 Go 和自己寫的確定性模擬器學分散式
  - 時鐘、複製、一致性、手刻 Raft、PBFT，再拆 Spanner、Kafka、etcd
  - 最後做一個容錯的分片 KV，用 Jepsen 的方式驗證
- [kernel_internals](./systems/kernel_internals/README.md)
  - Linux kernel 各子系統怎麼設計：排程、記憶體、同步、VFS、網路
  - 對照 6.12 原始碼，用 QEMU + gdb 跟
  - 最後寫一套 kernel module
- [debian_packaging](./systems/debian_packaging/README.md)
  - dpkg 和 apt 的底層，source package 和 debian/ 目錄
  - debhelper、sbuild、lintian，簽章和 repository
  - 最後自己架一套私有 APT 基礎設施

### web/

- [firebase](./web/firebase/README.md)
  - Auth、Firestore、Storage、Hosting，用 v9 的 modular SDK
  - 重點是 Security Rules，它是前端直連時唯一的防線
  - 全部在 emulator 上跑，最後做一個即時筆記板

## 健檢

[`tools/check_repo.py`](tools/check_repo.py) 會檢查索引有沒有漏列課程、Markdown 連結有沒有壞掉，CI 每次 push 都會跑。想在 commit 前就擋下來：

```sh
git config core.hooksPath .githooks
```
