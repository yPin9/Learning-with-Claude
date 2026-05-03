# Project 200

我跟 Claude 一起亂學東西的地方。

新人建議從 [`soft_skills/how_to_learn`](./soft_skills/how_to_learn/README.md) 起手 —— 它是後面每一門課的前置。

## 教材索引

### algorithms/
- [leetcode_patterns](./algorithms/leetcode_patterns/README.md)
  - 演算法面試完整課程：C++，從遞迴直覺到 DP/Graph/Greedy，41 章 + 6 練習 + 30 題衝刺 final

### ai/
- [ai_applications](./ai/ai_applications/README.md)
  - Claude 深度應用 + 用 Claude 生態做產品 + 通用 LLM 工程（RAG / eval / observability）

### architecture/
- [riscv](./architecture/riscv/README.md)
  - 從 RV32I 到 V/B 擴充、custom extension，自寫 RV32I emulator
- [arm](./architecture/arm/README.md)
  - Cortex-A/M 雙線，從 ISA 到 JTAG，自刻 STM32 韌體與 Cortex-M3 mini RTOS-lite

### compilers/
- [compiler_frontend](./compilers/compiler_frontend/README.md)
  - flex + bison 寫 MiniC frontend
- [compiler_backend](./compilers/compiler_backend/README.md)
  - LLVM RISC-V backend：SelectionDAG / TableGen / Scheduler / MC
- [elf_linking](./compilers/elf_linking/README.md)
  - relocation / linker script / RISC-V relaxation

### dev_tools/
- [git](./dev_tools/git/README.md)
  - 給已經會 add/commit/push、想真正熟練的人，hooks + worktree + 真實踩坑
- [cicd](./dev_tools/cicd/README.md)
  - Docker + GitHub Actions，把 FastAPI + Postgres 服務做成可交付 pipeline

### programming/
- [algorithms](./programming/algorithms/README.md)
  - 面試導向，從 pattern 記憶轉到原理理解，Python
- [modern_cpp](./programming/modern_cpp/README.md)
  - 給有 C 基礎的人速成 C++20，目標讀 + 寫現代風格
- [sat_smt](./programming/sat_smt/README.md)
  - 從命題邏輯到自刻 mini-SMT solver，C++20，全程不靠 Z3 當黑盒

### security/
- [gdb](./security/gdb/README.md)
  - 從基本 break/run 到自寫 ptrace + DWARF mini debugger
- [ida_pro](./security/ida_pro/README.md)
  - IDA 9.x，從只敢按 F5 到寫 IDAPython 自動化
- [afl_plus_plus](./security/afl_plus_plus/README.md)
  - AFL++ 內部機制，從 bitmap 到 CmpLog
- [symex_taint](./security/symex_taint/README.md)
  - symbolic execution + dynamic taint analysis，自寫 concolic executor
- [pentest](./security/pentest/README.md)
  - 滲透測試的工具、心法、白帽思維
- [kernel_pwn](./security/kernel_pwn/README.md)
  - Linux kernel pwn，目標 Google kernelCTF（含現代 heap / 隨機 kmalloc cache）
- [owasp](./security/owasp/README.md)
  - OWASP Top 10 2025 + Web 安全完整課程（含 2021→2025 對照），含 API Top 10、CVE 案例、WAF/RASP、紅藍隊演習
- [malware_analysis](./security/malware_analysis/README.md)
  - 讀懂並分析惡意程式碼：PE/ELF → anti-analysis 對抗 → injection/C2/ransomware → Linux rootkit → .NET/CS beacon → Volatility memory forensics → Yara/Sigma 偵測規則
- [cryptography](./security/cryptography/README.md)
  - 從 GF(2⁸) 到 mini-TLS：對稱/公鑰/AEAD/post-quantum 全套+攻擊（Bleichenbacher、Logjam、Heartbleed），手刻 AES/RSA/Kyber/TLS 1.3

### soft_skills/
- [how_to_learn](./soft_skills/how_to_learn/README.md)
  - meta-learning，所有系列的前置課程
- [chinese_writing](./soft_skills/chinese_writing/README.md)
  - 敘事散文與小說
- [industry_analysis](./soft_skills/industry_analysis/README.md)
  - 從零拆解陌生產業到投資判斷
- [chess](./soft_skills/chess/README.md)
  - 戰術 + 殘局 + 中局，最後 5 盤 Rapid 自我復盤
- [go](./soft_skills/go/README.md)
  - 圍棋從零到業餘高段，死活/手筋/形 重，AI 復盤(KataGo) 整 Part，50 盤升段 final

### scripting/
- [powershell](./scripting/powershell/README.md)
  - 從零到系統維運自動化：語法核心 + 系統管理 + AD + PSRemoting + 自訂模組，final = SysOpsToolkit

### systems/
- [bpf](./systems/bpf/README.md)
  - classic BPF 到 eBPF（verifier / CO-RE / libbpf），最後寫一個 agent
- [linux_boot](./systems/linux_boot/README.md)
  - x86_64 開機流程，BIOS + UEFI 雙線，自製 bootloader / initramfs / minimal Linux
- [observability_tools](./systems/observability_tools/README.md)
  - strace/perf/valgrind 全套，自寫 mini-strace + LD_PRELOAD interceptor，5-bug 偵探破案 final
- [perf_bench](./systems/perf_bench/README.md)
  - perf + benchmark + compiler optimization 連動
- [yocto](./systems/yocto/README.md)
  - toolchain 工程師速通：把 patched GCC 進 BSP
- [networking](./systems/networking/README.md)
  - TCP/IP + VPN + Proxy + VPS 全套，含 GFW 對抗演進史 + 完整 production 部署 final
- [linux_commands](./systems/linux_commands/README.md)
  - 從 VFS/inode/fd 底層到 shell scripting，31 章 + 4 練習 + SysOps 腳本工具包 final
