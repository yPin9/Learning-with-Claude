# 練習 E — 重現「資料檔繞簽章」最小 PoC

> **目標**：把 LogoFAIL 和 BootHole 的核心精神——「已簽章 binary 解析未簽章資料」——做成一個可以在 WSL gcc 上真跑、ASAN 能抓、OVMF secboot 能對照驗證的教學版最小 PoC。
>
> **前提**：Part 5 的前四章（Ch 28-32）已讀完，了解 Secure Boot 信任模型、T3b、SBAT 機制。
>
> **環境**：WSL Ubuntu 22.04，gcc + ASAN（主要動手環境）；OVMF secboot + snakeoil（Secure Boot 信任邊界對照）。
>
> **誠實標注**：這是**原理教學版**，不是真實 UEFI parser 的 exploit。PoC 示範「驗簽通過但解析被污染」的語意，而非重現 LogoFAIL 的真實 BMP parser bug。

---

## 攻擊精神與教學目標

### 問題本質

Secure Boot 驗章的對象是 **EFI binary**（.efi 檔案）。但 binary 可以開口讀取**資料檔**——設定、logo、字型——這些資料檔沒有被 Secure Boot 驗章。

當這個 binary 的資料解析邏輯有 bug，而攻擊者能控制資料檔的內容時：

```
信任鏈的裂縫：

  Secure Boot ─驗章─▶ loader.efi ─解析─▶ data.bin
                           ✓                ✗
                      已簽章確認          未驗章，攻擊者可替換

  攻擊者目標：不破解 loader.efi 的簽章，
              而是讓 loader.efi 解析一個惡意的 data.bin，
              透過 parser bug 污染 loader.efi 的執行流。
```

### 本練習的三個部分

```
Part A：C 程式模擬「signed loader 解析 data.bin」
        ├── 實作一個帶 T3b（length confusion）bug 的 parser
        ├── 示範「驗簽通過但資料可控」的語意
        └── gcc ASAN 真跑，heap overflow 被抓

Part B：OVMF secboot 環境示範 Secure Boot 信任邊界
        ├── 未簽章 EFI 被擋（Secure Boot 作用）
        ├── 簽章 EFI 通過（驗章有效）
        └── 簽章 EFI 解析的資料不受驗章保護（裂縫存在）

Part C：完整原理報告模板（填空後作為驗收）
```

---

## Part A：C 模擬程式

### 設計說明

我們模擬一個「signed loader」的行為：
1. 它有一個「簽章驗證」步驟（模擬 Secure Boot 的 db 驗章）
2. 驗章通過後，它解析一個外部資料檔（模擬 grub.cfg 或 logo.bmp）
3. 資料檔的 header 有一個「length」欄位，loader 用它決定讀多少 bytes
4. 但「計算 buffer 大小」和「實際複製」使用不同的 length 欄位 → T3b

### 資料檔格式設計

```c
/* 模擬的資料檔格式（data.bin 的結構）*/

typedef struct {
    uint32_t  magic;         // "DATA" = 0x44415441
    uint32_t  header_size;   // header 本身的大小（用於驗算）
    uint32_t  payload_size;  // 聲稱的 payload 大小（用於分配 buffer）
    uint32_t  actual_size;   // 實際的 payload 大小（用於複製）
                             // ← T3b 漏洞點：payload_size 和 actual_size 不一致
    uint8_t   checksum;      // 模擬「資料完整性驗算」（但不是簽章！）
    uint8_t   reserved[3];
    /* payload data 緊接在 header 後面 */
} DataFileHeader;
```

### 主程式碼

把以下內容儲存為 `/tmp/sbat_poc/loader_poc.c`：

```c
/**
 * loader_poc.c — 示範「資料檔繞簽章」原理的教學版 PoC
 *
 * 模擬場景：
 *   - loader（本程式）有「簽章」（模擬 Secure Boot 驗章通過）
 *   - loader 解析一個外部資料檔 data.bin
 *   - data.bin 沒有被驗章（攻擊者可控）
 *   - data.bin 的 header 有 T3b（length confusion）漏洞
 *   - ASAN 示範 heap overflow 被觸發
 *
 * 教育用途：說明「驗章通過 ≠ 執行安全」的原理
 *
 * 編譯：gcc -fsanitize=address -g -o loader_poc loader_poc.c
 * 執行：./loader_poc data_normal.bin   (正常資料，應該成功)
 *       ./loader_poc data_malicious.bin (惡意資料，ASAN 應該報告 heap overflow)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>

/* ──────────────────────────────────────────────────
   資料檔格式定義
   ────────────────────────────────────────────────── */

#define DATA_MAGIC    0x44415441U  /* "DATA" */
#define HEADER_SIZE   16           /* 固定 header 大小 */

typedef struct {
    uint32_t  magic;
    uint32_t  header_size;
    uint32_t  payload_size;   /* 用於分配 buffer 的大小 */
    uint32_t  actual_size;    /* 用於複製的大小 */
    /* payload 緊接在此 header 之後 */
} DataFileHeader;

/* ──────────────────────────────────────────────────
   模擬「簽章驗證」（不是真正的密碼學，只示範語意）
   ────────────────────────────────────────────────── */

/**
 * simulate_signature_check - 模擬 Secure Boot 驗章
 *
 * 在真實場景中，這裡是 UEFI 的 db 驗章邏輯：
 *   1. 計算 binary 的 PE hash
 *   2. 與 db 中的已知 hash 或 cert 比對
 *   3. 通過則信任此 binary 可以執行
 *
 * 這裡我們用一個固定的「簽章字串」模擬，
 * 重點是：簽章驗的是 loader 本身，不是它讀取的 data.bin
 */
bool simulate_signature_check(void)
{
    /* 模擬：這個 loader 的 hash 在 db 中 → 驗章通過 */
    printf("[SECURE BOOT] Verifying loader signature...\n");
    printf("[SECURE BOOT] Hash matches entry in signature database (db)\n");
    printf("[SECURE BOOT] Loader is TRUSTED. Proceeding.\n");
    printf("[SECURE BOOT] NOTE: data.bin is NOT in the trust chain.\n\n");
    return true;
}

/* ──────────────────────────────────────────────────
   有漏洞的 parser（T3b: length confusion）
   ────────────────────────────────────────────────── */

/**
 * vulnerable_parse_data - 有 T3b 漏洞的資料解析函式
 *
 * 漏洞原理（T3b: Length Confusion）：
 *   - 使用 header->payload_size 決定分配多少 buffer
 *   - 使用 header->actual_size 決定複製多少 bytes
 *   - 如果 actual_size > payload_size → heap overflow
 *
 * 在真實的 LogoFAIL 中，類似的不一致存在於：
 *   - BMP header 的 DataOffset 和實際 pixel data 大小
 *   - Width × Height × BytesPerPixel 的整數溢位（計算出小值，但複製真實大小）
 */
int vulnerable_parse_data(const uint8_t *file_data, size_t file_size)
{
    if (file_size < HEADER_SIZE) {
        fprintf(stderr, "[PARSER] Error: file too small (%zu bytes)\n", file_size);
        return -1;
    }

    const DataFileHeader *header = (const DataFileHeader *)file_data;

    /* 驗 magic */
    if (header->magic != DATA_MAGIC) {
        fprintf(stderr, "[PARSER] Error: invalid magic 0x%08X\n", header->magic);
        return -1;
    }

    /* 驗 header_size */
    if (header->header_size != HEADER_SIZE) {
        fprintf(stderr, "[PARSER] Error: unexpected header size %u\n", header->header_size);
        return -1;
    }

    printf("[PARSER] Magic: OK (0x%08X = 'DATA')\n", header->magic);
    printf("[PARSER] payload_size (for malloc): %u bytes\n", header->payload_size);
    printf("[PARSER] actual_size  (for memcpy): %u bytes\n", header->actual_size);

    /* ── T3b 漏洞：用 payload_size 分配 buffer ── */
    /* 如果 payload_size 很小，分配的 buffer 就很小 */
    char *buffer = (char *)malloc(header->payload_size);
    if (!buffer) {
        fprintf(stderr, "[PARSER] Error: malloc(%u) failed\n", header->payload_size);
        return -1;
    }

    printf("[PARSER] malloc(%u) → buffer @ %p\n", header->payload_size, (void *)buffer);

    /* ── T3b 漏洞：用 actual_size 複製資料 ── */
    /* 如果 actual_size > payload_size → 超過分配的 buffer → heap overflow */
    const uint8_t *payload = file_data + HEADER_SIZE;

    if ((size_t)(HEADER_SIZE + header->actual_size) > file_size) {
        fprintf(stderr, "[PARSER] Error: actual_size exceeds file size\n");
        free(buffer);
        return -1;
    }

    printf("[PARSER] memcpy(buffer, payload, %u)... ", header->actual_size);
    fflush(stdout);

    /*
     * ★ 這一行是 T3b 漏洞的觸發點 ★
     *
     * 當 actual_size > payload_size 時：
     *   buffer 只有 payload_size 大小
     *   但我們複製 actual_size bytes
     *   → 超出 buffer 邊界 → heap overflow
     *
     * ASAN 會在這裡中止並報告 heap-buffer-overflow
     */
    memcpy(buffer, payload, header->actual_size);  /* ← VULNERABLE */

    printf("done.\n");
    printf("[PARSER] Successfully parsed %u bytes of payload data.\n\n",
           header->actual_size);

    /* 模擬「使用」解析結果 */
    printf("[PARSER] Payload content: \"%.64s\"\n", buffer);

    free(buffer);
    return 0;
}

/* ──────────────────────────────────────────────────
   安全版 parser（修補後的對照版本）
   ────────────────────────────────────────────────── */

/**
 * safe_parse_data - 修補後的版本
 *
 * 修補原則：
 *   確保 actual_size <= payload_size（或用同一個 size 決策）
 *   這消除了 length confusion
 */
int safe_parse_data(const uint8_t *file_data, size_t file_size)
{
    if (file_size < HEADER_SIZE) return -1;

    const DataFileHeader *header = (const DataFileHeader *)file_data;
    if (header->magic != DATA_MAGIC) return -1;
    if (header->header_size != HEADER_SIZE) return -1;

    /* ── 修補：驗證兩個 size 欄位一致 ── */
    if (header->actual_size > header->payload_size) {
        fprintf(stderr, "[SAFE PARSER] REJECTED: actual_size (%u) > payload_size (%u)\n",
                header->actual_size, header->payload_size);
        fprintf(stderr, "[SAFE PARSER] This is a sign of malicious or corrupt data.\n");
        return -1;
    }

    /* 使用 payload_size（分配）和 actual_size（複製），已驗證後者 <= 前者 */
    char *buffer = (char *)malloc(header->payload_size);
    if (!buffer) return -1;

    const uint8_t *payload = file_data + HEADER_SIZE;
    if ((size_t)(HEADER_SIZE + header->actual_size) > file_size) {
        free(buffer);
        return -1;
    }

    memcpy(buffer, payload, header->actual_size);  /* 安全：actual_size <= payload_size */

    printf("[SAFE PARSER] Successfully parsed %u bytes (size-checked).\n",
           header->actual_size);
    printf("[SAFE PARSER] Payload content: \"%.64s\"\n", buffer);

    free(buffer);
    return 0;
}

/* ──────────────────────────────────────────────────
   主程式：讀 data.bin，驗章，解析
   ────────────────────────────────────────────────── */

int main(int argc, char *argv[])
{
    printf("============================================================\n");
    printf("  Loader PoC: Data File Bypass Demo (LogoFAIL / BootHole 精神)\n");
    printf("============================================================\n\n");

    if (argc < 2) {
        fprintf(stderr, "Usage: %s <data_file> [--safe]\n", argv[0]);
        fprintf(stderr, "  --safe: use patched parser (no vulnerability)\n\n");
        fprintf(stderr, "Generate test files:\n");
        fprintf(stderr, "  python3 gen_data.py normal   → data_normal.bin\n");
        fprintf(stderr, "  python3 gen_data.py malicious → data_malicious.bin\n");
        return 1;
    }

    bool use_safe_parser = (argc >= 3 && strcmp(argv[2], "--safe") == 0);

    /* Step 1: 模擬簽章驗證（loader 本身通過驗章）*/
    if (!simulate_signature_check()) {
        fprintf(stderr, "[LOADER] Signature check FAILED. Aborting.\n");
        return 1;
    }

    /* Step 2: 讀取資料檔（這個資料檔沒有被驗章！）*/
    printf("[LOADER] Loading data file: %s\n", argv[1]);
    printf("[LOADER] WARNING: data file is NOT covered by Secure Boot trust chain.\n\n");

    FILE *fp = fopen(argv[1], "rb");
    if (!fp) {
        perror("[LOADER] fopen");
        return 1;
    }

    fseek(fp, 0, SEEK_END);
    long file_size = ftell(fp);
    fseek(fp, 0, SEEK_SET);

    if (file_size <= 0 || file_size > 1024 * 1024) {
        fprintf(stderr, "[LOADER] Invalid file size: %ld\n", file_size);
        fclose(fp);
        return 1;
    }

    uint8_t *file_data = (uint8_t *)malloc((size_t)file_size);
    if (!file_data) {
        perror("[LOADER] malloc for file");
        fclose(fp);
        return 1;
    }

    if (fread(file_data, 1, (size_t)file_size, fp) != (size_t)file_size) {
        perror("[LOADER] fread");
        free(file_data);
        fclose(fp);
        return 1;
    }
    fclose(fp);

    printf("[LOADER] Read %ld bytes from data file.\n\n", file_size);

    /* Step 3: 解析資料檔 */
    int result;
    if (use_safe_parser) {
        printf("[LOADER] Using SAFE (patched) parser.\n\n");
        result = safe_parse_data(file_data, (size_t)file_size);
    } else {
        printf("[LOADER] Using VULNERABLE parser (T3b: length confusion).\n\n");
        result = vulnerable_parse_data(file_data, (size_t)file_size);
    }

    free(file_data);

    if (result == 0) {
        printf("\n[LOADER] Data processing complete. Continuing boot...\n");
    } else {
        printf("\n[LOADER] Data processing FAILED.\n");
    }

    return result;
}
```

### 資料檔產生器

儲存為 `/tmp/sbat_poc/gen_data.py`：

```python
#!/usr/bin/env python3
"""
gen_data.py — 產生測試用的資料檔

Usage:
    python3 gen_data.py normal     → data_normal.bin（正常，不觸發漏洞）
    python3 gen_data.py malicious  → data_malicious.bin（觸發 T3b heap overflow）
"""

import struct
import sys

MAGIC = 0x44415441          # "DATA"
HEADER_SIZE = 16

def make_data_file(payload: bytes, payload_size_override: int | None = None) -> bytes:
    """
    建立資料檔。

    payload_size_override：如果設定，用這個值作為 payload_size（分配 buffer 用），
                           但 actual_size 仍然等於 len(payload)（複製用）。
                           當 payload_size_override < len(payload) 時觸發 T3b。
    """
    actual_size = len(payload)
    payload_size = payload_size_override if payload_size_override is not None else actual_size

    header = struct.pack("<IIII",
        MAGIC,
        HEADER_SIZE,
        payload_size,   # 分配 buffer 用
        actual_size,    # 複製用
    )
    return header + payload


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 gen_data.py [normal|malicious]")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "normal":
        # 正常資料：payload_size == actual_size，不觸發漏洞
        payload = b"Hello from data.bin! This is a normal, safe payload." + b"\x00" * 10
        data = make_data_file(payload)
        with open("data_normal.bin", "wb") as f:
            f.write(data)
        print(f"[+] Created data_normal.bin")
        print(f"    payload_size = actual_size = {len(payload)} bytes")
        print(f"    → Should parse successfully without heap overflow.")

    elif mode == "malicious":
        # 惡意資料：
        #   payload_size = 16（分配一個小 buffer）
        #   actual_size = 256（複製 256 bytes → overflow）
        #
        # 這模擬攻擊者把 header 中的 payload_size 改小，
        # 讓 parser 分配一個小 buffer，然後複製大量資料進去。
        #
        # 在真實的 LogoFAIL 中，這個不一致來自 BMP header 的解析邏輯缺陷，
        # 不是攻擊者直接填兩個不同的值——但「計算出小值 X，複製真實大小 Y > X」是相同的原理。

        small_alloc_size = 16       # malloc(16) → 小 buffer
        large_copy_size = 256       # memcpy(..., 256) → 超出 buffer 邊界

        # payload 必須至少有 large_copy_size bytes
        # 填入辨識標記方便 ASAN 報告時確認 overflow 的內容
        payload = b"A" * 16 + b"OVERFLOW_MARKER_" * 15   # 16 + 240 = 256 bytes

        assert len(payload) == large_copy_size

        data = make_data_file(payload, payload_size_override=small_alloc_size)
        with open("data_malicious.bin", "wb") as f:
            f.write(data)

        print(f"[+] Created data_malicious.bin")
        print(f"    payload_size (malloc) = {small_alloc_size} bytes  ← 小 buffer")
        print(f"    actual_size  (memcpy) = {large_copy_size} bytes  ← 大複製")
        print(f"    Difference = {large_copy_size - small_alloc_size} bytes overflow")
        print(f"    → Should trigger ASAN heap-buffer-overflow.")

    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

---

## Part A 操作步驟

```bash
# 在 WSL Ubuntu 22.04 中：
mkdir -p /tmp/sbat_poc && cd /tmp/sbat_poc

# 把上面的兩個檔案存好，然後：

# 1. 產生測試資料
python3 gen_data.py normal
python3 gen_data.py malicious

# 驗證檔案存在：
ls -la data_normal.bin data_malicious.bin

# 2. 編譯（啟用 ASAN）
gcc -fsanitize=address -g -o loader_poc loader_poc.c
# 預期輸出：無錯誤

# 3. 測試正常資料（不應觸發 ASAN）
./loader_poc data_normal.bin
```

**預期輸出（正常資料）**：

```
============================================================
  Loader PoC: Data File Bypass Demo (LogoFAIL / BootHole 精神)
============================================================

[SECURE BOOT] Verifying loader signature...
[SECURE BOOT] Hash matches entry in signature database (db)
[SECURE BOOT] Loader is TRUSTED. Proceeding.
[SECURE BOOT] NOTE: data.bin is NOT in the trust chain.

[LOADER] Loading data file: data_normal.bin
[LOADER] WARNING: data file is NOT covered by Secure Boot trust chain.

[LOADER] Read 80 bytes from data file.

[LOADER] Using VULNERABLE parser (T3b: length confusion).

[PARSER] Magic: OK (0x44415441 = 'DATA')
[PARSER] payload_size (for malloc): 66 bytes
[PARSER] actual_size  (for memcpy): 66 bytes
[PARSER] malloc(66) → buffer @ 0x...
[PARSER] memcpy(buffer, payload, 66)... done.
[PARSER] Successfully parsed 66 bytes of payload data.

[PARSER] Payload content: "Hello from data.bin! This is a normal, safe payload."

[LOADER] Data processing complete. Continuing boot...
```

```bash
# 4. 測試惡意資料（應觸發 ASAN heap-buffer-overflow）
./loader_poc data_malicious.bin
```

**預期輸出（惡意資料，ASAN 介入）**：

```
============================================================
  Loader PoC: Data File Bypass Demo (LogoFAIL / BootHole 精神)
============================================================

[SECURE BOOT] Verifying loader signature...
[SECURE BOOT] Hash matches entry in signature database (db)
[SECURE BOOT] Loader is TRUSTED. Proceeding.
[SECURE BOOT] NOTE: data.bin is NOT in the trust chain.

[LOADER] Loading data file: data_malicious.bin
[LOADER] WARNING: data file is NOT covered by Secure Boot trust chain.

[LOADER] Read 272 bytes from data file.

[LOADER] Using VULNERABLE parser (T3b: length confusion).

[PARSER] Magic: OK (0x44415441 = 'DATA')
[PARSER] payload_size (for malloc): 16 bytes
[PARSER] actual_size  (for memcpy): 256 bytes
[PARSER] malloc(16) → buffer @ 0x...
[PARSER] memcpy(buffer, payload, 256)...
=================================================================
==XXXXX==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x...
WRITE of size 256 at 0x... thread T0
    #0 0x... in memcpy ...
    #1 0x... in vulnerable_parse_data loader_poc.c:134
    #2 0x... in main loader_poc.c:218
...
Shadow bytes around the buggy address:
  ...
  ...0x...: 00 00 fa fa  ← 00=accessible, fa=heap metadata（紅區）
SUMMARY: AddressSanitizer: heap-buffer-overflow loader_poc.c:134 in vulnerable_parse_data
```

**ASAN 的關鍵資訊**：
- `heap-buffer-overflow`：確認是 heap 上的越界寫入
- `WRITE of size 256 at 0x...`：試圖寫 256 bytes
- `malloc(16)`：但只分配了 16 bytes
- `loader_poc.c:134`：在 `memcpy` 那一行觸發

```bash
# 5. 確認安全版 parser 能擋住惡意資料
./loader_poc data_malicious.bin --safe
```

**預期輸出（安全 parser 擋住）**：

```
[LOADER] Using SAFE (patched) parser.

[SAFE PARSER] REJECTED: actual_size (256) > payload_size (16)
[SAFE PARSER] This is a sign of malicious or corrupt data.

[LOADER] Data processing FAILED.
```

---

## Part B：OVMF secboot 信任邊界示範

本段在 WSL QEMU + OVMF.secboot 環境執行，示範：
1. 未簽章 EFI 被 Secure Boot 擋住
2. 簽章 EFI 通過
3. 簽章 EFI 讀取的資料檔不受 Secure Boot 保護

### 環境前提

```bash
# 確認 Ch 00 的 OVMF secboot 環境可用：
ls /usr/share/OVMF/OVMF_CODE.secboot.fd   # 主 OVMF ROM（secboot 版）
ls /usr/share/OVMF/OVMF_VARS.fd            # 可寫 VARS（含 secboot keys）

# 確認 snakeoil key 存在（Ch 00 安裝）：
ls /etc/ssl/certs/ssl-cert-snakeoil.pem   # public cert（DER 格式需轉換）
ls /etc/ssl/private/ssl-cert-snakeoil.key # private key
```

### 步驟 1：建立「未簽章」和「簽章」版本的測試 EFI

我們需要一個簡單的 UEFI 應用程式，顯示 "Data Bypass PoC" 字樣。

由於在 WSL 中直接 build edk2 較複雜，這裡用 **gnu-efi** 建一個最小的 EFI：

```bash
# 安裝 gnu-efi
sudo apt-get install -y gnu-efi

# 建立最小 UEFI app
cat > /tmp/sbat_poc/hello_uefi.c << 'EOF'
#include <efi.h>
#include <efilib.h>

EFI_STATUS
EFIAPI
efi_main(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable)
{
    InitializeLib(ImageHandle, SystemTable);
    Print(L"[DataBypassPoC] Loader is RUNNING (Secure Boot passed)\n");
    Print(L"[DataBypassPoC] But I could read data.bin without any signature check!\n");
    /* 真實 exploit 在這裡解析惡意 logo/config 並觸發 parser bug */

    /* 等待使用者按鍵 */
    WaitForSingleEvent(ST->ConIn->WaitForKey, 0);
    return EFI_SUCCESS;
}
EOF

# 編譯成 EFI binary（x86_64）
gcc -I/usr/include/efi \
    -I/usr/include/efi/x86_64 \
    -I/usr/include/efi/protocol \
    -fpic -ffreestanding -fno-stack-protector \
    -fno-stack-check -fshort-wchar -mno-red-zone \
    -Wall \
    -DEFI_FUNCTION_WRAPPER \
    -c -o /tmp/sbat_poc/hello_uefi.o /tmp/sbat_poc/hello_uefi.c

ld -nostdlib -znocombreloc \
   -T /usr/lib/elf_x86_64_efi.lds \
   -shared -Bsymbolic \
   /usr/lib/crt0-efi-x86_64.o \
   /tmp/sbat_poc/hello_uefi.o \
   -o /tmp/sbat_poc/hello_uefi.so \
   /usr/lib/libefi.a /usr/lib/libgnuefi.a

objcopy -j .text -j .sdata -j .data -j .rodata \
        -j .dynamic -j .dynsym -j .rel \
        -j .rela -j .reloc \
        --target=efi-app-x86_64 \
        /tmp/sbat_poc/hello_uefi.so \
        /tmp/sbat_poc/hello_uefi.efi

echo "[+] Built hello_uefi.efi (unsigned)"
```

### 步驟 2：簽章

```bash
# 轉換 snakeoil cert 格式
openssl x509 -in /etc/ssl/certs/ssl-cert-snakeoil.pem \
    -outform DER -out /tmp/sbat_poc/snakeoil.crt.der

# 用 sbsign 簽章
sbsign \
    --key /etc/ssl/private/ssl-cert-snakeoil.key \
    --cert /etc/ssl/certs/ssl-cert-snakeoil.pem \
    --output /tmp/sbat_poc/hello_uefi_signed.efi \
    /tmp/sbat_poc/hello_uefi.efi

echo "[+] Signed: hello_uefi_signed.efi"

# 確認簽章：
sbverify --cert /etc/ssl/certs/ssl-cert-snakeoil.pem \
         /tmp/sbat_poc/hello_uefi_signed.efi \
    && echo "[+] Signature VALID" \
    || echo "[-] Signature INVALID"
```

### 步驟 3：準備 ESP 並啟動 QEMU

```bash
# 準備 OVMF VARS（含 snakeoil 的 secboot 設定）
# 這需要先把 snakeoil cert 加入 db
# （詳細步驟見 Ch 28；這裡用預設 OVMF secboot 設定）

cp /usr/share/OVMF/OVMF_VARS.fd /tmp/sbat_poc/OVMF_VARS_test.fd

# 建立 ESP image，包含兩個 EFI：未簽和已簽
mkdir -p /tmp/sbat_poc/esp/EFI/BOOT

# 只放未簽章版本
cp /tmp/sbat_poc/hello_uefi.efi \
   /tmp/sbat_poc/esp/EFI/BOOT/BOOTX64.EFI

# 建立 ESP image
dd if=/dev/zero of=/tmp/sbat_poc/esp.img bs=1M count=32 2>/dev/null
mkfs.vfat -F 32 /tmp/sbat_poc/esp.img
mcopy -i /tmp/sbat_poc/esp.img -s /tmp/sbat_poc/esp/::

# 啟動 QEMU（不啟用 secboot，確認未簽章能開機）
echo "[TEST 1] Booting unsigned EFI without Secure Boot enforcement..."
qemu-system-x86_64 \
    -machine q35 \
    -cpu qemu64 \
    -m 256M \
    -drive if=pflash,format=raw,file=/usr/share/OVMF/OVMF_CODE.fd,readonly=on \
    -drive if=pflash,format=raw,file=/tmp/sbat_poc/OVMF_VARS_test.fd \
    -drive format=raw,file=/tmp/sbat_poc/esp.img \
    -nographic \
    -serial stdio \
    -no-reboot \
    2>/dev/null
# 按 Ctrl+A X 結束 QEMU
```

### 步驟 4：啟用 Secure Boot 測試

```bash
# 使用 OVMF.secboot（預載 Microsoft 測試 key，或 snakeoil 設定）
# 注意：secboot VARS 需要 db 中有 snakeoil cert 才能讓簽章版本通過
#
# 這裡的關鍵觀察不需要成功執行 EFI，
# 而是觀察 OVMF 的輸出訊息，確認：
#   "Security Violation" 當未簽章 EFI 被載入（Secure Boot 作用）
#   EFI 正常執行當簽章版本被載入（驗章通過）

echo "[TEST 2] Expected: unsigned EFI triggers Security Violation"
# 在 secboot 環境中，BOOTX64.EFI（未簽章）應該觸發：
# "Security Violation" 或
# "Image was not loaded: security policy violation"
```

### 預期觀察總結

```
場景 1：無 Secure Boot 強制
  未簽章 EFI → 執行（直接顯示訊息）
  觀察：驗章不強制，任何 EFI 都能跑

場景 2：有 Secure Boot 強制 + 未簽章 EFI
  未簽章 EFI → OVMF 報告 "Security Violation"，拒絕執行
  觀察：Secure Boot 有作用，未授權 binary 被擋

場景 3：有 Secure Boot 強制 + 已簽章 EFI
  已簽章 EFI → 通過驗章，正常執行
  觀察：信任的 binary 能執行

場景 4（核心：信任邊界）：
  已簽章 EFI（hello_uefi_signed.efi）如果去解析 data_malicious.bin
  → Secure Boot 不管這件事，只管 EFI binary 本身的簽章
  → data_malicious.bin 可以觸發 parser bug（如 Part A 所示）
  → 這就是 LogoFAIL / BootHole 的信任邊界問題
```

---

## Part C：報告模板與驗收

### 原理說明報告（填空完成）

```
標題：「資料檔繞簽章」原理報告

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
一、信任模型的裂縫

Secure Boot 驗章的對象是：____________________（EFI binary）

驗章未覆蓋的對象：________________________（資料檔、設定檔、logo 圖片）

信任邊界的比喻：
  海關（Secure Boot）只查「旅行者」（binary）的護照，
  不查旅行者「行李」（data）的內容。
  旅行者是合法的，但行李裡裝著炸彈（惡意 payload）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
二、PoC 的漏洞類型

類型學分類：T____（從 Ch 21 的六大類型中選）

漏洞名稱：Length Confusion / Integer Overflow（T3b 的一種形態）

漏洞位置（程式碼行號）：loader_poc.c 第 ____ 行

觸發條件：
  分配 buffer 使用：______________ 欄位（值：____）
  複製大小使用：_________________ 欄位（值：____）
  overflow 大小：____ bytes

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三、驗證結果

ASAN 報告類型：________________________
觸發位置（函數名）：____________________
Buffer 分配大小：____ bytes
嘗試寫入大小：____ bytes
差值（overflow 量）：____ bytes

安全版 parser 的修補策略：
  在 ____________________ 操作前，先驗證 actual_size ≤ payload_size
  若不成立，回傳 ___________________（錯誤碼）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
四、Secure Boot 信任邊界觀察

OVMF secboot 環境觀察：
  未簽章 EFI 的行為：____________________________
  已簽章 EFI 的行為：____________________________
  已簽章 EFI 解析惡意 data 的後果：_____________

Secure Boot 能防止此攻擊嗎？（是/否）：____
理由：________________________________________

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
五、對應到真實攻擊

本 PoC 對應的真實案例：
  LogoFAIL 中的類比：__________________________
    （binary 是什麼？data 是什麼？漏洞類型？）

  BootHole 中的類比：__________________________
    （binary 是什麼？data 是什麼？漏洞類型？）

防禦措施（至少兩條）：
  1. ___________________________________________
  2. ___________________________________________
  （提示：Measured Boot / parser fuzzing / 最小 parser / data 也要驗章）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 驗收標準

| 項目 | 驗證方式 | 通過條件 |
|------|---------|---------|
| ASAN 觸發 | `./loader_poc data_malicious.bin` | 輸出包含 `heap-buffer-overflow` |
| 安全 parser 擋住 | `./loader_poc data_malicious.bin --safe` | 輸出 `REJECTED`，程式正常退出（非 ASAN crash）|
| 正常資料通過 | `./loader_poc data_normal.bin` | 無 ASAN 報告，輸出 `Data processing complete` |
| UEFI secboot 觀察 | QEMU + OVMF | 能說明未簽 vs 簽章的差異，以及 data 不受保護 |
| 報告完整 | 填空模板 | 六個答案正確（見答案說明） |

### 答案對照（只看本題答案，不要先看）

<details>
<summary>報告填空參考答案（先自己試，卡住再看）</summary>

```
一、
驗章對象：EFI binary（.efi 檔案的 PE hash 或 X.509 cert chain）
未覆蓋：資料檔（grub.cfg、Logo.bmp、字型檔、任何由 binary 讀取的外部資料）

二、
類型學：T3b（Length Confusion）
行號：約第 134 行（memcpy 那行）
分配欄位：payload_size（16）
複製欄位：actual_size（256）
overflow：240 bytes

三、
ASAN 報告：heap-buffer-overflow
函數名：vulnerable_parse_data
Buffer 大小：16 bytes
寫入大小：256 bytes
overflow：240 bytes
修補：在 memcpy 之前驗證 actual_size <= payload_size；
     不成立則 free(buffer); return -1;

四、
未簽章：Security Violation，被拒絕
已簽章：正常執行
惡意 data 後果：parser bug 觸發（Secure Boot 無感知）
能防止？否
理由：Secure Boot 只驗 binary 的簽章，不驗 binary 解析的資料內容

五、
LogoFAIL 類比：
  binary = LogoDxe.efi（UEFI image loader driver，有廠商簽章）
  data = Logo.bmp/PNG（ESP 或 NVRAM 中的 logo 圖片，無簽章）
  漏洞類型：BMP/PNG parser 的 heap overflow（T3b 或整數溢位）

BootHole 類比：
  binary = grubx64.efi（Canonical/廠商 CA 簽章的 GRUB2）
  data = grub.cfg（ESP 上的文字設定檔，無簽章）
  漏洞類型：grub_parser_split_cmdline() 的 heap overflow（T3b）

防禦措施：
  1. Measured Boot + TPM：即使 binary 執行被污染，PCR 值改變可被遠端證明偵測
  2. Parser fuzzing：對 UEFI 中的 image/config parser 做 coverage-guided fuzzing（LogoFAIL 的發現方法）
  3. 最小化 parser：只支援最簡單的 BMP 格式（無 PNG/JPEG），減少攻擊面
  4. 把 data 也放進 Measured Boot 的範圍（grub.cfg signed configs）
```

</details>

---

## 這份練習在課程地圖的位置

```
Ch 21（嵌入式繞過類型學：T3b 定義）
    │
    ▼
Ch 28-29（Secure Boot 內部 / 繞過類型學）
    │
    ▼
Ch 30（三條真實鏈：BootHole / BlackLotus / LogoFAIL）
    │
    ▼
Ch 31（bootkit 構造）
    │
    ▼
Ch 32（dbx / SBAT 撤銷）
    │
    ▼
練習 E（本章：動手做「資料檔繞簽章」的最小 PoC）
    │
    ▼
Ch 33（軟體攻擊面關閉後，物理是下一步）
```

Part A（C 程式 + ASAN）讓你**親手觸發 T3b**，不再是抽象概念。Part B（OVMF secboot）讓你**親眼看到 Secure Boot 的信任邊界**——它擋得住什麼、擋不住什麼。兩者合起來就是 LogoFAIL/BootHole 的教學復現。

---

→ [下一章](./33-when-software-fails.md)
