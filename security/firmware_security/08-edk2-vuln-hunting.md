# Ch 8 — edk2 漏洞挖掘

> **目標**：建立一套可複製的 edk2 漏洞挖掘方法論——從認識哪裡是高價值程式碼、到鎖定 attacker-controlled input、到靜態分析模式、到真跑一個 fuzzer harness 看 crash。

> **環境**：WSL，gcc with `-fsanitize=address`。clang 14 已裝（Ubuntu 22.04）。

---

## 為什麼需要系統性方法論？

edk2 repository 有超過 3000 個 .c 檔。隨機翻程式碼是浪費時間。有效的挖洞流程必須回答三個問題：

1. **哪裡是高價值目標**（哪個 package/module 影響最廣、最多廠商用）？
2. **攻擊者的輸入如何流入**（input source 在哪）？
3. **哪些程式碼模式最容易出問題**（sink pattern）？

有了這三個答案，才能把精力集中在值得分析的地方。

---

## 先建立直覺：edk2 的可信邊界

```
外部世界（攻擊者可控）
         │
         ▼
┌────────────────────────────────────────────────────────┐
│                  DXE / BDS / UEFI Runtime              │
│                                                        │
│  ┌───────────┐   ┌──────────────┐   ┌──────────────┐  │
│  │  Variable │   │   Capsule    │   │  Network     │  │
│  │  Service  │   │   (UpdateC.) │   │  (PXE/DHCP)  │  │
│  └─────┬─────┘   └──────┬───────┘   └──────┬───────┘  │
│        │                │                  │          │
│        ▼                ▼                  ▼          │
│  ┌──────────────────────────────────────────────────┐ │
│  │          信任邊界（Boundary）                      │ │
│  │  Q: 有沒有驗長度、驗格式、驗範圍？                  │ │
│  └──────────────────────────────────────────────────┘ │
│        │                                              │
│        ▼                                              │
│  DXE driver 內部邏輯（heap alloc, memcpy, parse...）   │
└────────────────────────────────────────────────────────┘
```

漏洞永遠在「邊界內側」——攻擊者的資料跨過邊界後，第一個用它的地方。

---

## 高價值目標 Packages

### MdeModulePkg

edk2 核心功能 package，幾乎所有廠商都用：

| Module | 功能 | 攻擊面 |
|--------|------|-------|
| `Core/Dxe/DxeMain` | DXE core | Protocol DB、image loader |
| `Universal/Variable/RuntimeDxe` | Variable service | SetVariable/GetVariable 邊界 |
| `Application/UiApp` | BDS UI | 使用者輸入、Option ROM |
| `Library/BaseBmpSupportLib` | BMP 解析 | 已知 LogoFAIL 類型 |
| `Universal/CapsulePei` | capsule 早期解析 | 整數溢位 |

### NetworkPkg

PXE boot、IPv6、DHCP、DNS、TLS：PixieFail 就出在這裡。直接 parse 網路封包，輸入完全攻擊者控制。

### SecurityPkg

Secure Boot、measured boot、TCG TPM。理論上「安全相關」，實際上程式碼也有問題（CVE 持續出現）。

### FatPkg / PartitionDxe

FAT32 parser、GPT parser。攻擊者可以帶一個特製 USB，裡面有惡意格式的 FAT/GPT。

---

## Attacker-Controlled Input 清單

挖洞時先把這份清單過一遍，找到每個 input source 對應的「第一個用到它的函數」：

| Input Source | 進入點 | edk2 函數 / Protocol |
|-------------|-------|---------------------|
| UEFI variable | `GetVariable()` 返回值的 data buffer | `VariableRuntimeDxe` |
| Capsule image header | `UpdateCapsule()` 傳入的 `CapsuleHeaderArray` | `CapsulePei`/`CapsuleRuntime` |
| SMM comm buffer | SMM handler 的 `CommBuffer` 參數 | 各 SMM driver 的 `SmiHandlerFn` |
| BMP/logo | `GetSectionFromFv()` 讀出 BMP section | `BaseBmpSupportLib` |
| PXE/DHCP 封包 | 網路中斷回呼 | `NetworkPkg/DhcpDxe` |
| FAT32 目錄項 | 讀取 ESP 時 | `FatPkg/EnhancedFatDxe` |
| GPT 分割表 | 讀取磁碟時 | `MbrPartitionDxe`/`PartitionDxe` |
| Option ROM | 掃 PCIe bus 時 | `PciBusDxe` |

---

## 靜態分析：Sink Patterns

找到 input source 之後，追蹤資料流向下面這些 sink pattern：

### 模式 A：AllocatePool 後立刻 CopyMem，長度來自攻擊者

```c
/* 危險模式 */
Status = gBS->AllocatePool(EfiBootServicesData, UserLen, &Buf);
CopyMem(Buf, UserData, UserLen);  // 如果 alloc 用的長度經過截斷或溢位

/* 更危險：alloc 一個值，copy 另一個值 */
Status = gBS->AllocatePool(EfiBootServicesData, ComputedLen, &Buf);
CopyMem(Buf, UserData, ActualLen);  // ComputedLen < ActualLen 時 overflow
```

用 `grep -n "AllocatePool" *.c` 列出所有 alloc，再往下看 CopyMem 的 size 參數和 alloc 的 size 參數是否同源。

### 模式 B：`offset + size` 沒有 overflow 檢查

```c
/* 危險模式：若 Offset + Size > UINT32_MAX → 溢位成小數，跑到錯誤區域 */
if (Offset + Size > TotalSize) {
    return EFI_INVALID_PARAMETER;
}
```

正確寫法要先用 SafeIntLib 確認 `Offset + Size` 本身不溢位，才能拿去比較。

### 模式 C：`StrSize()` / `AsciiStrSize()` 後沒驗邊界

```c
/* 如果 Str 沒有 null terminator（攻擊者控制），StrSize 會一直掃 */
UINTN NameSize = StrSize(VarName);  /* 可能越界 */
CopyMem(Dst, VarName, NameSize);
```

### 模式 D：Protocol 指標解引用前沒驗 NULL

```c
Status = gBS->LocateProtocol(&gEfiFooProtocolGuid, NULL, (VOID **)&FooProto);
/* 忘記檢查 Status 和 FooProto != NULL */
FooProto->DoSomething(Data);  /* crash 或指標竄改 */
```

### 快速 grep 腳本

```bash
# 找沒有緊跟 SafeIntLib 的 width*height 乘法
grep -n "Width.*Height\|height.*width" MdeModulePkg/ -r --include="*.c" | \
  grep -v "SafeUint\|SafeMult"

# 找 AllocatePool 附近的 CopyMem（上下 10 行）
grep -n -A 10 "AllocatePool" SecurityPkg/ -r --include="*.c" | \
  grep -B 5 "CopyMem"

# 找所有 SMM comm buffer handler 入口
grep -rn "EFI_SMM_SW_DISPATCH2_PROTOCOL\|SmiHandlerRegister" \
  MdeModulePkg/ SecurityPkg/ --include="*.c"
```

---

## CHIPSEC：自動化平台稽核

CHIPSEC 是 Intel 開源的平台安全稽核工具，本質上是一套「已知 bug checker」，每個模組對應一個已知的攻擊面或漏洞類型。

**本環境未裝 CHIPSEC（需要 kernel module）**，以下為理論說明與指令參考。

### 安裝（真實 Linux 主機）

```bash
# 需要 Python 3.x，root 權限
git clone https://github.com/chipsec/chipsec
cd chipsec
pip install -e .
# 或用 pip: pip install chipsec
```

### 掃描 S3 boot script 保護

```bash
# 偵測 S3 boot script 是否在 SMRAM 之外且未鎖定
sudo python3 -m chipsec.main -m common.uefi.s3bootscript

# 預期輸出（有問題時）：
# [!] FAILED: S3 Boot Script is not in SMRAM
# [!] S3 Boot Script region at 0x... is writable from OS
```

### 掃描 BIOS write protect

```bash
# 偵測 SPI flash 是否允許 OS 寫入（BIOS_CNTL.BIOSWE / PRx）
sudo python3 -m chipsec.main -m common.bios_wp

# 掃描所有常見模組
sudo python3 -m chipsec.main
```

### 重要模組清單

| 模組 | 對應攻擊面 |
|------|---------|
| `common.bios_wp` | SPI 寫保護 |
| `common.smrr` | SMRR 是否正確設定 |
| `common.uefi.s3bootscript` | S3 boot script 記憶體保護 |
| `common.smm_code_chk` | SMM_CODE_CHK_EN |
| `tools.uefi.scan_image` | 掃 firmware image 已知 bug |

**本段未實測，為理論預期行為。** 驗證方法：在實體 Linux 主機（不是 WSL）上安裝 CHIPSEC 後執行上述指令；WSL 沒有 `/dev/mem`。

---

## edk2 Host-Based Fuzzing

### 為什麼要 host-based？

在 QEMU 跑完整 UEFI boot 做 fuzzing：每次 iteration 需要重新開機，大約 2–5 秒。host-based 把 UEFI library 直接編成 Linux 上的 shared library，每次 iteration 是函數呼叫，速度快 100–1000 倍。

### HBFA（Host-Based Firmware Analyzer）

Intel 的 `edk2-libtool` / HBFA 專案：把 edk2 的 `BaseBmpSupportLib`、`FatPkg` 等直接 build 成 Linux native binary，配合 libFuzzer 做 fuzzing。

GitHub: `https://github.com/intel/hbfa-fl`

基本流程（**本段未實測，為理論預期行為**）：
```bash
# 建立 fuzzing harness（以 BMP 為例）
# hbfa-fl/HBFA/UefiHostFuzzTestPkg/TestCase/MdeModulePkg/
#   Library/BaseBmpSupportLib/TestBmpSupportLib.c

# 用 clang 建置
CXX=clang CC=clang cmake ...
make TestBmpSupportLib_fuzzer

# 執行 libFuzzer
./TestBmpSupportLib_fuzzer -max_len=65536 corpus/
```

### TSFFS（Tianocore Secure Firmware Fuzzing Suite）

較新的官方方案，基於 Simics 模擬器，可在系統層做覆蓋率引導 fuzzing。需要 Simics 授權。

---

## 真跑：手寫 Fuzzer Harness + ASan 抓 Crash

既然 HBFA/clang libFuzzer 在這個環境設置複雜，用一個「手寫迴圈 fuzzer」搭配 gcc+ASan 示範完整流程。這是真實可執行的模型，與 libFuzzer 的差別只在「沒有覆蓋率回饋」（用隨機變異代替），在 WSL 上可以直接跑。

模擬對象：一個有兩個 bug 的 UEFI capsule header parser。

```c
/* fuzz_capsule.c
 * 模擬有 bug 的 capsule header parser（edk2 CapsulePei 風格）
 *
 * Bug 1: total_size - header_size 下溢（unsigned）→ 超大 payload_region
 * Bug 2: 用 payload_size（攻擊者控制）alloc，但 CopyMem 用 payload_region
 *
 * gcc -fsanitize=address -g -o fuzz_capsule fuzz_capsule.c && ./fuzz_capsule
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

typedef struct {
    uint32_t total_size;
    uint32_t header_size;
    uint32_t payload_size;   /* 攻擊者填的 "期望" 大小 */
    uint8_t  flags;
} CapsuleHdr;

static int parse_capsule(const uint8_t *data, size_t len)
{
    if (len < sizeof(CapsuleHdr)) return -1;
    const CapsuleHdr *hdr = (const CapsuleHdr *)data;

    /* Bug 1: 未驗 header_size <= total_size，下溢 */
    uint32_t payload_region = hdr->total_size - hdr->header_size;

    /* 基本防護：payload_size 合理範圍 */
    if (hdr->payload_size == 0 || hdr->payload_size > 0x10000) return -1;

    uint8_t *buf = malloc(hdr->payload_size);
    if (!buf) return -1;

    /* Bug 2: 若 payload_region > payload_size → heap overflow */
    size_t avail = len - sizeof(CapsuleHdr);
    if (payload_region <= avail) {
        memcpy(buf, data + sizeof(CapsuleHdr), payload_region); /* 危險 */
    }
    free(buf);
    return 0;
}

int main(void)
{
    srand(0x1337);
    uint8_t buf[256];

    for (int i = 0; i < 100000; i++) {
        CapsuleHdr *hdr = (CapsuleHdr *)buf;
        /* 隨機化 header 欄位 */
        hdr->total_size   = (uint32_t)(rand() % 512) + 16;
        hdr->header_size  = (uint32_t)(rand() % 32)  + 8;
        hdr->payload_size = (uint32_t)(rand() % 0x200) + 1;
        hdr->flags        = (uint8_t)(rand() & 0xFF);

        size_t plen = (size_t)(rand() % 200);
        if (plen + sizeof(CapsuleHdr) < sizeof(buf))
            memset(buf + sizeof(CapsuleHdr), 0x41, plen);

        parse_capsule(buf, sizeof(CapsuleHdr) + plen);
    }
    printf("[fuzzer] done 100000 iterations\n");
    return 0;
}
```

**真實 ASan 輸出**（WSL，gcc 11，iteration 第一次觸發即 abort）：
```
=================================================================
==303387==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x50200000001e
    at pc ... in __interceptor_memcpy ...
WRITE of size 87 at 0x50200000001e thread T0
    #0 ... in __interceptor_memcpy ...
    #1 ... in parse_capsule /tmp/fuzz_capsule.c:32
    #2 ... in main /tmp/fuzz_capsule.c:57

0x50200000001e is located 0 bytes to the right of 14-byte region
[0x502000000010,0x50200000001e)
allocated by thread T0 here:
    #0 ... in __interceptor_malloc ...
    #1 ... in parse_capsule /tmp/fuzz_capsule.c:27

SUMMARY: AddressSanitizer: heap-buffer-overflow ... in __interceptor_memcpy
```

讀法：
- `WRITE of size 87` — 嘗試寫 87 bytes
- `14-byte region` — 只分配了 14 bytes（`payload_size` 的一個隨機值）
- `0 bytes to the right` — 緊接 redzone，第一個 OOB byte 立刻被捕

---

## libFuzzer 概念（若有 clang）

WSL 已裝 clang 14，libFuzzer 理論上可用，但 edk2 library 本身需要 host build 才能連結。這裡給 libFuzzer harness 的寫法模板：

```c
/* 若已有 host-build 的 parse_capsule 函數 */
#include <stddef.h>
#include <stdint.h>

int parse_capsule(const uint8_t *data, size_t len);  /* 已有的 parser */

/* libFuzzer 入口，名稱固定 */
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    parse_capsule(data, size);
    return 0;  /* 非 0 表示告訴 fuzzer 這個 input 要丟棄 */
}
```

編譯：
```bash
clang -fsanitize=address,fuzzer -g \
      -o fuzzer_capsule fuzzer_harness.c parser_impl.c
./fuzzer_capsule -max_len=4096 corpus/
```

libFuzzer 與手寫迴圈 fuzzer 的核心差別：

| 面向 | libFuzzer | 手寫迴圈 |
|------|----------|---------|
| 變異策略 | 覆蓋率引導（edge coverage）| 完全隨機 |
| 速度 | 每秒 10K–1M exec | 每秒 10K–100K exec |
| Corpus 學習 | 自動保留探索新路徑的輸入 | 無 |
| 設置複雜度 | 需要 sanitizer + clang | 只需 gcc |

---

## 方法論總結：挖洞五步驟

```
Step 1 → 選目標 Package
         高價值: MdeModulePkg, NetworkPkg, SecurityPkg, FatPkg

Step 2 → 找 Input Source
         Variable / Capsule / SMM comm / 圖形 / 網路封包 / FAT

Step 3 → 追資料流到第一個 Sink
         grep AllocatePool, CopyMem, StrSize, ZeroMem 周圍

Step 4 → 靜態分析 Sink Pattern
         長度計算是否用 SafeIntLib?
         有沒有 offset + size 不溢位驗證?
         array bound 有無?

Step 5 → 動態驗證
         HBFA / 手寫 fuzzer / CHIPSEC / 真機 QEMU 觀察
```

---

## 對比取捨：靜態 vs 動態分析

| 面向 | 靜態（grep / CodeQL） | 動態（fuzzer / CHIPSEC） |
|------|---------------------|------------------------|
| 涵蓋率 | 廣（可掃整個 repo） | 深（但覆蓋路徑有限） |
| 誤報率 | 高（很多只是 pattern） | 低（真跑 = 真 bug） |
| 適合找 | 整數溢位、字串函數誤用 | TOCTOU、runtime 條件競爭 |
| 速度 | 快 | 慢（需要環境搭建） |
| 最佳組合 | 靜態找候選，動態驗證 | ← 這樣 |

---

## 踩雷

1. **只看 edk2 upstream，不看廠商 fork**：CVE-2023-40238 在 edk2 upstream 早有 fix，但 Insyde 的 fork 沒有同步，漏洞繼續存在。真正的攻擊面在廠商 BIOS binary，upstream 只是起點。

2. **認為 AllocatePool 不會回傳 NULL**：edk2 文件說「memory shortage 時」返回 `EFI_OUT_OF_RESOURCES`，但早期 DXE 記憶體很大，測試時幾乎不觸發。Fuzzer 要故意製造小 alloc（payload_size 給 0 或 1）。

3. **搞錯 UINT32 vs UINTN**：edk2 在 64-bit 系統上 `UINTN` = 64-bit，`UINT32` = 32-bit。長度計算如果混用（`UINTN len = UINT32_a * UINT32_b`），乘法在 32-bit 溢位後才轉型，結果仍是溢位後的值。

4. **以為 host-build fuzzing 不需要任何修改**：edk2 library 裡有大量 `gST`、`gBS`、`gRT` 全域指標呼叫，host build 時這些都是 NULL。HBFA 有一套 stub 實作，需要連結對應的 stub library，不是直接拿 .c 檔編。

5. **只跑 `-fsanitize=address` 忘了 `-fsanitize=undefined`**：整數溢位在 ASan 下不一定 abort（要看溢位後的值有沒有觸發 OOB），加上 `-fsanitize=undefined` 才會直接在溢位點報錯。

---

## 進階延伸

- **CodeQL for edk2**：GitHub 官方 CodeQL 有 edk2 query pack，可以直接在 Actions 上跑，找到 CopyMem/AllocatePool 的長度計算問題。適合大範圍掃描，靜態分析的現代做法。
- **Fuzz Everything With Structure-Aware Fuzzing**：libFuzzer 加上 `LLVMFuzzerMutate` custom mutator，可以生成符合 EFI_LOAD_OPTION 或 Capsule header 格式的結構化輸入，比純隨機 mutation 快找到深層 parser bug。
- **Binarly VulnCheck**：Binarly 的商業版工具，可以掃 firmware binary（不需要原始碼），用 binary similarity 比對已知漏洞 pattern。可以掃廠商 BIOS image 找 1-day。

---

## 動手練習

1. 把 `fuzz_capsule.c` 的 Bug 1 修好（加 `if (hdr->header_size > hdr->total_size) return -1`），重新編譯跑 fuzzer，確認 Bug 2 仍然被 ASan 抓到。
2. 加 `-fsanitize=undefined` 到編譯指令，看看 ASan 輸出有何不同——是否有 `signed integer overflow` 或 `unsigned integer overflow` 報告（UBSan 預設不報 unsigned，加 `-fsanitize=unsigned-integer-overflow` 試試）。
3. 在 edk2 repo（clone 本地）執行 `grep -rn "Width \* Height\|PixelHeight \* PixelWidth" --include="*.c"`，看看 `BaseBmpSupportLib` 有幾個乘法，現在有沒有用 SafeIntLib。
4. 把手寫 fuzzer 的 `srand(0x1337)` 換成 `srand(time(NULL))`，跑幾次，觀察每次觸發的 iteration 號碼是否相同（重現性測試）。

---

## 本章重點

- edk2 高價值目標：MdeModulePkg、NetworkPkg、FatPkg、SecurityPkg。
- Attacker-controlled input 的主要來源：variable、capsule、SMM comm buffer、圖形資源、網路封包。
- 靜態 sink pattern：`AllocatePool + CopyMem`（長度不同源）、`offset + size`（無溢位檢查）、`StrSize` 後無邊界。
- CHIPSEC 是「已知 bug checker」，可自動稽核 S3 script / SMRR / SPI write protect。
- Host-based fuzzing（HBFA）比 QEMU fuzzing 快 100 倍以上；手寫迴圈 fuzzer 是最快能跑的 baseline。
- 靜態找候選 + 動態驗證 = 完整方法論。

---

## 自我檢核

- [ ] 說得出 edk2 三個高價值 package 的名稱與攻擊面？
- [ ] 能用 grep 在 edk2 repo 找 AllocatePool + CopyMem 的可疑模式？
- [ ] 解釋 host-based fuzzing 為什麼比 QEMU-based 快？
- [ ] 寫得出一個 libFuzzer harness 的最小骨架？
- [ ] 知道 `UINT32 * UINT32 → UINTN` 在 64-bit 上是否安全？

---

## 延伸閱讀

1. **HBFA（Host-Based Firmware Analyzer）**（Intel, GitHub）— edk2 library 的 host-build + libFuzzer 整合框架，設置步驟在 README；理解它如何 stub 掉 `gBS`/`gST` 是讀懂 edk2 host build 的最快路徑。[https://github.com/intel/hbfa-fl](https://github.com/intel/hbfa-fl)

2. **PixieFail Analysis**（Quarkslab, 2024）— 詳細分析九個 NetworkPkg 漏洞的靜態分析過程和成因；是「用靜態分析找 edk2 bug」最好的案例研究，與本章方法論直接對應。[https://blog.quarkslab.com/pixiefail.html](https://blog.quarkslab.com/pixiefail.html)

3. **CodeQL for C/C++ UEFI**（GitHub Security Lab）— 用 CodeQL 寫 query 找 edk2 裡的 CopyMem 長度問題；學會寫 CodeQL query 可以把本章的「手動 grep」升級成「自動語義分析」。[https://securitylab.github.com/research/codeql-uefi/](https://securitylab.github.com/research/codeql-uefi/)

4. **CHIPSEC Framework**（GitHub, Intel）— 官方文件，每個模組的原始碼就是一份攻擊技術說明文件；特別是 `chipsec/modules/common/` 底下，跟著模組名稱找對應的 CVE，學習「稽核工具的作者怎麼想攻擊面」。[https://github.com/chipsec/chipsec](https://github.com/chipsec/chipsec)

---

→ [下一章：從 OS 打回韌體：runtime 信任邊界](./09-os-to-firmware-runtime.md)
