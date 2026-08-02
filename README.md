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
- [cpu_design](./architecture/cpu_design/README.md)
  - 用 SystemVerilog 從零打造 pipelined RISC-V core（補 riscv ISA 層與 compiler_backend 之間的 RTL/微架構斷層）：數位邏輯地基 → 單週期 RV32I → 五級 pipeline/hazard/forwarding → 分支預測 → cache/Sv32 VM/AXI → CSR/trap/中斷，verilator + spike 逐指令對拍，40 章 + 5 練習 + pipelined core final

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
- [rust](./programming/rust/README.md)
  - 給懂 C/C++20 的系統/資安工程師的 Rust：ownership/borrow/lifetime 底層 → 記憶體佈局/unsafe/FFI/Miri → async 手刻 executor → 資安研究向（逆 Rust binary/audit unsafe/RUSTSEC/fuzzing）→ Rust-for-Linux，全程 C/C++ 對照、WSL rustc 1.97+nightly+Miri 真跑，43 章 + 5 練習 + Rust-for-Linux 字元裝置 kernel module final

### security/
- [gdb](./security/gdb/README.md)
  - 從會用到能改：精通 GDB 全功能 + ptrace/DWARF 底層 + Python API，自寫 mini debugger 與 gef 風格插件，43 章 + 7 練習 + 插件套件 final
- [ida_pro](./security/ida_pro/README.md)
  - IDA 9.x，從只敢按 F5 到寫 IDAPython 自動化
- [afl_plus_plus](./security/afl_plus_plus/README.md)
  - AFL++ 內部機制，從 bitmap 到 CmpLog
- [advanced_fuzzing](./security/advanced_fuzzing/README.md)
  - 接 afl_plus_plus 之後的進階 fuzzing 大課，目標導向工具不拘：LibAFL 造 fuzzer + 文法/stateful + kernel(syzkaller) + snapshot(Nyx) + 韌體 rehosting + JS 引擎(Fuzzilli) + hybrid(SymCC/AFLGo) + OSS-Fuzz/評測科學，CTF+CVE hunting 導向，48 章 + 6 練習 + 真實開源 campaign final
- [symex_taint](./security/symex_taint/README.md)
  - symbolic execution + dynamic taint analysis，自寫 concolic executor
- [pentest](./security/pentest/README.md)
  - 滲透測試的工具、心法、白帽思維
- [kernel_pwn](./security/kernel_pwn/README.md)
  - Linux kernel pwn，目標 Google kernelCTF（含現代 heap / 隨機 kmalloc cache）
- [owasp](./security/owasp/README.md)
  - OWASP Top 10 2025 + Web 安全完整課程（含 2021→2025 對照），含 API Top 10、CVE 案例、WAF/RASP、紅藍隊演習
- [web_exploitation](./security/web_exploitation/README.md)
  - 現代 Web 攻擊深課（接 owasp 廣度、與 browser_pwn 平行的 pwn 天梯 Web 支線）：primitive→gadget chain 思維，SSRF chain/多語言反序列化(Java/PHP/Python/.NET)/request smuggling+cache poisoning/JWT-OAuth-OIDC-SAML/prototype pollution/GraphQL/CSPT/SSTI，CTF+bug bounty 導向、PortSwigger Academy+Docker 靶場真跑，39 章 + 6 練習 + full-chain 報告 final
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
- [browser_pwn](./security/browser_pwn/README.md)
  - V8 一條到底的瀏覽器 pwn（pwn 天梯頂端，接 binary_exploitation/kernel_pwn）：物件模型（Map/elements kind/pointer compression）→ Ignition/TurboFan/Maglev 管線 → addrof/fakeobj/任意讀寫原語 → TurboFan type confusion 五大家族（CVE-2018-17463/Math.expm1/JSON hole）→ 找洞（patch-diff + Fuzzilli）→ 任意 R/W 繞 V8 Sandbox/CET（data-only）→ CTF+full chain 全景，自編 V8 15.3 真跑驗證，41 章 + 6 練習 + 真實 CVE 到 exploit final
- [windows_pwn](./security/windows_pwn/README.md)
  - Windows 使用者態漏洞利用（補 Linux pwn 天梯缺的 Windows 大陸，接 binary_exploitation）：internals 與利用五五不妥協——PE/PEB-TEB/loader/Native API/syscall/handle 內部 → NT Heap/LFH/Segment Heap → SEH overwrite/SEHOP/DEP-ROP/ASLR/shellcode → heap UAF/vtable 劫持/info leak → 現代緩解重頭戲（CFG/XFG/CET/ACG/CIG/data-only）→ WinAFL/patch diffing 找洞 → token/UAC/EoP 提權銜接 windows_kernel_driver，CTF 導向、全程 Win11 x64（mingw/Python 真跑、MSVC/CET 段落標未實測），47 章 + 5 練習 + 全緩解 exploit chain final
- [android_reversing](./security/android_reversing/README.md)
  - 安卓逆向從 APK 到 ART 底層：App 層還原（DEX/smali/Java）→ Frida 動態插樁 → Native 逆向（.so/ARM64/JNI）→ 加固對抗（脫殼/反調試/混淆）→ ART 系統底層，安全研究/App 破解導向，AVD 實測，42 章 + 5 練習 + 綜合防護 App 拆解 final
- [android_app_vuln](./security/android_app_vuln/README.md)
  - 安卓 App 漏洞分析（接 android_reversing）：以 OWASP MASVS/MASTG 為骨架、bug bounty 導向，元件/IPC 濫用 → Intent redirection/PendingIntent 劫持 → Provider SQLi/path traversal → deeplink/WebView RCE/task hijacking → 儲存/crypto 誤用/secret 洩漏 → 權限/zip slip → 自動化(MobSF/semgrep)+報告，drozer/靶場實戰，16 章 + 3 練習 + MASTG 評估報告 final
- [android_exploitation](./security/android_exploitation/README.md)
  - 安卓系統漏洞利用（接 binary_exploitation/kernel_pwn）：把 glibc/x86 pwn 技能移植到 Android — bionic + scudo/jemalloc heap 破壞 → ARM64 PAC/BTI/MTE 緩解對抗 → Binder/Parcel LPE → SELinux/Zygote 沙箱 → Android kernel 驅動利用 → patch diff 到穩定 exploit，32 章 + 4 練習 + CVE 到 exploit final
- [ios_macos_exploitation](./security/ios_macos_exploitation/README.md)
  - Apple 生態全棧逆向 + exploitation（行動安全的另一半，接 android_reversing/android_exploitation）：iOS/macOS 五五、一門到底 — Mach-O/dyld/dyld shared cache/Obj-C runtime/Swift 逆向 → lldb/Frida/dtrace 工具鏈 → App 沙箱/Keychain/SSL pinning/越獄偵測繞過 → Mach port/XPC/launchd/TCC 的 IPC 與 macOS LPE → libmalloc 內部/heap 利用/PAC 深挖/沙箱逃逸 → XNU kernel/IOKit/zone allocator/PPL → 越獄內部（checkm8/KTRR/SEP/KFD-PUAF）/full chain → fuzzing/patch diffing/防禦演進，使用者有 Mac+iPhone 動手（作者 Windows 環境故工具輸出標未實測+附驗證步驟），43 章 + 5 練習 + CVE 鏈研究報告 final
- [cloud_container_security](./security/cloud_container_security/README.md)
  - 把 pwn 攻擊直覺搬進雲端，紅隊視角、AWS 主線 + Azure/GCP 對照：IAM 提權 → 服務攻擊面（S3/metadata SSRF/Lambda）→ 容器逃逸 → Kubernetes 淪陷（從零教 K8s）→ 供應鏈/CI-CD → 補回防禦偵測（CSPM/Falco/CloudTrail），39 章 + 4 練習 + 紅隊 engagement final
- [vm_escape](./security/vm_escape/README.md)
  - VM escape pwn，pwn 天梯上 browser_pwn 旁的另一座山：從 guest 內部打穿 hypervisor 拿 host code exec，QEMU/KVM 主線 + VirtualBox + VMware，含 VT-x/EPT/KVM 原理補強 → device emulation/MMIO/DMA 當原語 → heap overflow/UAF/劫 callback/ROP → VENOM/virtio CVE 復刻 → seccomp 繞過/Firecracker/full-chain，41 章 + 5 練習 + CVE 到完整逃逸 final
- [microarch_attacks](./security/microarch_attacks/README.md)
  - 微架構攻擊：把 CPU 效能優化反過來當洩密通道，攻擊為主 + 完整防禦 Part。cache 側信道原語（Flush+Reload/Prime+Probe/eviction set）→ 瞬態執行（Spectre v1/v2/RSB、Meltdown/MDS/L1TF、Downfall/Zenbleed 分類學）→ Rowhammer → Hertzbleed/port contention/TLB/KASLR break → 防禦（KPTI/retpoline/constant-time+dudect/HPC 偵測）→ 找新洞方法論，x86-64 主線 i7-10700 真跑（F+R/Spectre-v1 親手做、HW 已修的誠實標），37 章 + 4 練習 + 微架構洩漏實驗室 final
- [firmware_security](./security/firmware_security/README.md)
  - UEFI / Secure Boot 漏洞研究（把 pwn 從 Ring 0 往下打到 Ring -2/-3，接 linux_boot/arm/mtk_firmware）：以「信任鏈 / secure boot」貫穿全課、x86 UEFI 與 ARM 嵌入式雙線，攻擊為主 + 四支柱（韌體逆向 / 防守偵測 / 硬體故障注入 / TPM 密鑰）。UEFI PI 攻擊面/DXE/NVRAM variable → SMM 聖杯 + ME/BootGuard/PSP → ARM TF-A/U-Boot/AVB/MTK BootROM → 韌體 RE（UEFITool/Ghidra/efiXplorer）→ Secure Boot 繞過鏈（BootHole/BlackLotus/LogoFAIL）→ 硬體 glitch/SPI/JTAG → TPM 2.0/measured boot/bus sniffing → 防守偵測（CHIPSEC/attestation/廠商緩解），WSL qemu+OVMF(secboot)+aarch64+AAVMF+swtpm+tpm2-tools+uefi_firmware 真跑（chipsec/真硬體/glitch 誠實標未實測），46 章 + 6 練習 + 端到端攻防研究報告 final
- [blue_team_dfir](./security/blue_team_dfir/README.md)
  - 藍隊與 DFIR（補全 repo 唯一缺的防守視角，purple team 框架把既有 22 門攻擊課全變成靶）：ATT&CK/PICERL/證據可信度地基 → Detection Engineering（Sysmon/ETW、Sigma/YARA、涵蓋度、SIEM、Detection-as-Code）→ Windows endpoint DFIR 重頭戲（Volatility3 記憶體/注入、$MFT/$UsnJrnl、Prefetch/AMCache/ShimCache/SRUM、Registry、Event Log、持久化、憑證橫移）→ Linux/網路/雲/K8s IR（auditd/eBPF、Zeek/Suricata beaconing+JA3、CloudTrail/GuardDuty、Falco）→ Threat Hunting（hypothesis-driven、stacking、LOLBins、偵測 AMSI/ETW/unhooking 規避）→ 惡意程式鑑識/fileless/反鑑識對抗/偵測盲點 → 營運（alert triage/SOAR、TI/MISP、報告+MTTD/MTTR、purple team 演練閉環），跨 Windows/Linux/雲/網路四平台、概念為主關鍵處示範，39 章 + 4 練習 + 完整入侵事件調查 final

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
- [reading_code](./soft_skills/reading_code/README.md)
  - 讀碼即逆向：把 binary RE 攻堅直覺移植到讀陌生大型 source，40 ch + 4 練習 + 冷啟動攻堅 final，工具與方法並重（rg/ctags/cscope/clangd/tree-sitter/gdb/trace），全程 WSL redis 真跑

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
