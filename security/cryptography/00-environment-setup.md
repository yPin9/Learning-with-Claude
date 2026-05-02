# Ch 0 — 環境搭建

> 目標：把這門課用到的工具一次裝齊。Python `cryptography` / `pycryptodome` 用來教概念，C 配 OpenSSL / mbedTLS 用來看真實實作，SageMath 給數學驗算，pwntools 給攻擊章節用。確認每個工具都能跑最小範例。

## 工具總覽

```
┌─────────────────┬─────────────────────────────────────────────┐
│ 工具            │ 在這門課的角色                              │
├─────────────────┼─────────────────────────────────────────────┤
│ Python 3.11+    │ 主要教學語言（清晰、易讀）                  │
│ cryptography    │ 高階 Python lib，比對自寫實作               │
│ pycryptodome    │ 中階 Python lib，部分 hash / RSA 細節       │
│ OpenSSL CLI     │ command line 驗算 / 互通                     │
│ libssl-dev      │ C 寫 production-style 範例                   │
│ mbedTLS         │ 嵌入式風格 C library，code 比 OpenSSL 易讀  │
│ SageMath        │ 數論 / 有限體 / lattice 驗算                │
│ pwntools        │ Part 5 / 11 攻擊範例的 socket / 互動 helper │
│ gmp / mpz       │ 大數運算（C 端）                            │
│ pycryptodome.SAGE  │ 跨用                                     │
└─────────────────┴─────────────────────────────────────────────┘
```

## Linux（Ubuntu / Debian）

```bash
# 系統套件
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-venv \
    build-essential libssl-dev libgmp-dev \
    mbedtls-dev openssl gdb

# Python 環境（建議用 venv 隔離）
python3 -m venv ~/.venv/crypto
source ~/.venv/crypto/bin/activate
pip install cryptography pycryptodome pwntools sympy

# SageMath（重，但數學章節需要）
sudo apt install -y sagemath
```

`cryptography` 與 `pycryptodome` 兩個都裝，因為：
- `cryptography` API 漂亮，多數章節用它對照
- `pycryptodome` 暴露更多底層細節（如 `Crypto.Hash.SHA1.new().copy()`），length extension attack 章需要
- 兩者 namespace 不衝突

## macOS

```bash
brew install python@3.11 openssl mbedtls gmp sagemath
python3 -m venv ~/.venv/crypto
source ~/.venv/crypto/bin/activate
pip install cryptography pycryptodome pwntools sympy
```

注意 macOS 系統內建的 `openssl` 是 LibreSSL（Apple 換的），與 OpenSSL CLI 行為略有差。我們需要 GNU 版：

```bash
brew install openssl@3
echo 'export PATH="/opt/homebrew/opt/openssl@3/bin:$PATH"' >> ~/.zshrc
```

## Windows

兩條路：

### 路線 A：WSL2（推薦）

```powershell
winget install -e --id Microsoft.WSL
wsl --install -d Ubuntu
```

進 WSL 後照 Linux 那一節。

### 路線 B：原生 Windows

OpenSSL：<https://slproweb.com/products/Win32OpenSSL.html>
Python：python.org 官方 installer
SageMath：<https://www.sagemath.org/download-windows.html>（裝起來大）

之後章節範例用 bash 語法，原生路線你要自己換 PowerShell 或 cmd。

## 驗證安裝

每個都跑一次：

```bash
# Python crypto
python3 -c "from cryptography.hazmat.primitives.ciphers import Cipher; print('cryptography OK')"
python3 -c "from Crypto.Cipher import AES; print('pycryptodome OK')"

# OpenSSL
openssl version    # 期望 OpenSSL 3.x

# C 編譯（簡單測試）
cat > /tmp/sslhello.c << 'EOF'
#include <openssl/sha.h>
#include <stdio.h>
int main(void) {
    unsigned char d[32];
    SHA256((unsigned char *)"hi", 2, d);
    for (int i = 0; i < 32; i++) printf("%02x", d[i]);
    putchar('\n');
    return 0;
}
EOF
gcc /tmp/sslhello.c -lssl -lcrypto -o /tmp/sslhello && /tmp/sslhello
# 期望輸出：8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4

# SageMath
sage -c "print(factor(2024))"   # 期望：2^3 * 11 * 23

# pwntools
python3 -c "import pwn; print(pwn.cyclic(20))"
```

每個都看到預期輸出再進下一章。

## CryptoHack 帳號

註冊一個（免費）：<https://cryptohack.org/>

CryptoHack 是這個課的 **partner platform**。每章末尾我會提幾道對應 CryptoHack 題目，做完後你會更紮實。**現在就開帳號**，後面要用。

## 一個常見誤解

「自己寫 crypto 不是危險嗎？為什麼還要學？」

不矛盾。**寫 production crypto 確實要用 audited library**（libsodium、ring、Tink）— 自己 roll 一個 AES 上 production 等同把使用者推下水。但**學習用自己刻**完全沒問題，且是吃透演算法的唯一方式。

這門課的 deliverable 不是「拿去 production」，是「**看到 OpenSSL 的 SHA256 實作能秒懂**」「**Heartbleed 出現時知道 bug 在哪一行為什麼會洩漏**」。Ch 1 會展開為什麼 don't roll your own crypto。

## 自我檢核

- [ ] Python venv 與 `cryptography` / `pycryptodome` / `pwntools` / `sympy` 都裝好
- [ ] OpenSSL CLI 跑得動，版本 3.x
- [ ] 能編譯 C + link `-lssl -lcrypto` 並跑 SHA-256
- [ ] SageMath 能跑簡單 `factor()`
- [ ] CryptoHack 已開帳號

下一章看密碼學作為「學科」與「工程」的分野，以及為什麼 self-roll 是壞主意。

→ [Ch 1 密碼學全貌](./01-cryptography-overview.md)
