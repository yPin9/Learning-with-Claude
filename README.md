# Project 200

我跟 Claude 一起亂學東西的地方


## 教材索引

### algorithms/
- [leetcode_patterns](./algorithms/leetcode_patterns/README.md)
  - 演算法面試完整課程：C++，從遞迴直覺到 DP/Graph/Greedy，41 章 + 6 練習 + 30 題衝刺 final

### ai/
- [ai_applications](./ai/ai_applications/README.md)
  - Claude 深度應用 + 用 Claude 生態做產品 + 通用 LLM 工程（RAG / eval / observability）
- [harness_engineering](./ai/harness_engineering/README.md)
  - 打造 AI agent 執行框架：agent loop + context 管理 + 工具系統 + subagent + permission + eval + 導入落地，44 章 + 6 練習 + 自刻 mini harness final
- [spec_driven_development](./ai/spec_driven_development/README.md)
  - 用 SDD + DDD 和 AI 協作：軟工地基 + 需求工程(EARS/BDD) + 領域驅動設計 + Spec Kit/Kiro 實戰 + 自建 pipeline + 實測批判，45 章 + 6 練習 + 完整 SDD final

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
- [ssa_optimizations](./compilers/ssa_optimizations/README.md)
  - SSA 理論到 LLVM Pass 實作：Dominator Tree / SCCP / GVN / SCEV / 過程間分析，32 章 + 3 練習 + mini optimizer + CSmith 驗證 final

### dev_tools/
- [git](./dev_tools/git/README.md)
  - 給已經會 add/commit/push、想真正熟練的人，hooks + worktree + 真實踩坑
- [open_source](./dev_tools/open_source/README.md)
  - 從會 commit 到能跟全世界一起寫程式：中階 git（rebase/衝突/reflog）+ fork/PR/review/CI + 貢獻開源（含真實 PR）+ 團隊協作 + 維護者視角，38 章 + 6 練習 + 真實貢獻 final
- [cicd](./dev_tools/cicd/README.md)
  - Docker + GitHub Actions，把 FastAPI + Postgres 服務做成可交付 pipeline
- [docker](./dev_tools/docker/README.md)
  - 從 `docker run` 到生產部署：namespace/cgroup/OverlayFS 底層 + Dockerfile 進階 + Compose + 資安 hardening + Swarm，28 章 + 3 練習 + CI Pipeline final

### programming/
- [algorithms](./programming/algorithms/README.md)
  - 面試導向，從 pattern 記憶轉到原理理解，Python
- [modern_cpp](./programming/modern_cpp/README.md)
  - 給有 C 基礎的人速成 C++20，目標讀 + 寫現代風格
- [sat_smt](./programming/sat_smt/README.md)
  - 從命題邏輯到自刻 mini-SMT solver，C++20，全程不靠 Z3 當黑盒
- [c_interview](./programming/c_interview/README.md)
  - C 語言面試深度準備：記憶體/UB/ABI/嵌入式/效能/lock-free，30 章 + 3 練習 + mini libc final

### security/
- [gdb](./security/gdb/README.md)
  - 從會用到能改：精通 GDB 全功能 + ptrace/DWARF 底層 + Python API，自寫 mini debugger 與 gef 風格插件，43 章 + 7 練習 + 插件套件 final
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
- [ai_security](./security/ai_security/README.md)
  - AI 資安工程師面試衝刺：LLM 攻擊面（Prompt Injection/Jailbreak/RAG 投毒/Agent 劫持）+ 防護工具（NeMo Guardrails/Lakera Guard）+ NIST AI RMF/ISO 42001，27 章 + 3 練習 + Red Team 評測 final
- [oscp_prep](./security/oscp_prep/README.md)
  - OSCP 備考全攻略：從完全新手到拿證，含 Buffer Overflow + AD 三機鏈 + HTB/THM 機器推薦，42 章 + 4 練習 + 24hr PG 模擬 final
- [windows_kernel_driver](./security/windows_kernel_driver/README.md)
  - Windows 核心驅動開發 + 核心安全研究雙線：WDM/KMDF/Minifilter/WFP + BYOVD/Token 竊取/Pool 利用/Anti-EDR/VBS-HVCI，40 章 + 3 練習 + NanoEDR final
- [binary_exploitation](./security/binary_exploitation/README.md)
  - Userland pwn 地基（接 kernel_pwn）：stack smashing → ROP 全譜 → format string → glibc heap 深挖（tcache/House of X）→ 現代無 hook 世界（FSOP/exit handler）+ seccomp 沙箱逃逸/反調試，全程 WSL glibc 2.39 實測，41 章 + 7 練習 + 復刻 pwnable.tw 題鏈 final

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

### automation/
- [n8n](./automation/n8n/README.md)
  - 從零建立個人自動化中樞：視覺化 workflow + Code Node + REST/Webhook + self-host + AI Agent，25 章 + 3 練習 + 個人自動化中樞 final

### embedded/
- [protocols](./embedded/protocols/README.md)
  - ESP32 嵌入式通訊協議：SPI/I2C/UART/RS-485/Modbus/CAN/BLE/LoRa/Zigbee/USB/Ethernet，全程 register-level，不靠 HAL，26 章 + 3 練習 + 工業閘道器 final

### interview/
- [mtk_firmware](./interview/mtk_firmware/README.md)
  - MTK（聯發科）韌體工程師面試衝刺：C/嵌入式/OS/計組/資料結構考古題詳解 + 概念複習，44 章 + 5 練習 + 模擬面試 final

### scripting/
- [powershell](./scripting/powershell/README.md)
  - 從零到系統維運自動化：語法核心 + 系統管理 + AD + PSRemoting + 自訂模組，final = SysOpsToolkit

### ml/
- [local_llm](./ml/local_llm/README.md)
  - 地端 LLM 全端工程：pre-training 原理 + QLoRA fine-tuning + Ollama 部署，CPU 跑得動，34 章 + 4 練習 + 地端繁中小模型 final

### passive_income_with_ai/
- [threads_shopee_affiliate](./passive_income_with_ai/threads_shopee_affiliate/README.md)
  - ⭐主力：Threads × 蝦皮分潤可操作 playbook + 腳本，含「拆解別人爆文」判讀框架（post_teardown）與自產貼文 pipeline（鉤子產生器/留言連結/QA 閘/追蹤）
- [seo_shopee_affiliate](./passive_income_with_ai/seo_shopee_affiliate/README.md)
  - 長線輔助：SEO 內容站 × 蝦皮分潤，關鍵字選題打分 + AI 產文 + 上線品質閘 + 成效追蹤
- 註：此分類是「可操作 playbook + 腳本」，不是教學課程；誠實面對「不是真被動」與平台合規

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
