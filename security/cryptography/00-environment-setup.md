# Ch 0 — 環境搭建

> **目標**：裝好 Python `cryptography`、OpenSSL、SageMath、pwntools，驗證每個工具能正常執行，理解每個工具在本課扮演什麼角色。

> **環境**：Python 3.11+, Ubuntu 22.04 LTS。macOS 可用但 pwntools 部分功能受限；Windows 請用 WSL2。

## 四個工具，四個角色

本課用到四種不同定位的工具，各有分工：

```
┌────────────────────────────────────────────────────────────┐
│  Python cryptography              正規加解密               │
│  「生產級」的密碼學操作                                      │
│  AES-GCM / RSA / ECDSA / X25519 / HKDF ...               │
├────────────────────────────────────────────────────────────┤
│  SageMath                         數學實驗                  │
│  有限體運算、橢圓曲線、大整數分解                              │
│  GF(2^8) 的 AES S-box、ECC point addition ...             │
├────────────────────────────────────────────────────────────┤
│  pwntools                         攻擊腳本                  │
│  Padding Oracle、RSA 低指數攻擊、protocol exploit           │
│  remote()、xor()、long_to_bytes() ...                      │
├────────────────────────────────────────────────────────────┤
│  OpenSSL / mbedTLS CLI            TLS 實驗                  │
│  建 CA、簽憑證、TLS handshake 抓封包                         │
│  s_client / s_server / x509 / req ...                     │
└────────────────────────────────────────────────────────────┘
```

為什麼不用一個工具打天下？因為定位不同：

- `cryptography` 是 high-level API，它刻意不讓你碰底層細節（例如你不能自己選 AES 的 padding），這在生產環境是正確的——但學習密碼學需要看到底層
- SageMath 是數學計算環境，能跑 `GF(2^8)` 的有限體運算、橢圓曲線的 point addition、大數分解，Python 原生做不到
- pwntools 是攻擊工具，提供 `xor()`、`long_to_bytes()`、remote socket——寫 Padding Oracle exploit 時你不想自己處理 socket
- OpenSSL 是 TLS 的事實標準實作，Part 8 做 TLS 實驗時直接用 CLI 操作最直覺

## Step 1：Python 環境

用 venv 隔離，不要汙染系統 Python。

```bash
# 確認 Python 版本
python3 --version
# Python 3.11.x 或更高

# 建立虛擬環境
python3 -m venv ~/crypto-lab
source ~/crypto-lab/bin/activate

# 之後每次進 lab 都要 activate
echo 'alias crypto-lab="source ~/crypto-lab/bin/activate"' >> ~/.bashrc
source ~/.bashrc
```

## Step 2：安裝 Python cryptography

```bash
pip install cryptography
```

驗證：

```python
python3 -c "
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

# 產生 256-bit key 和 96-bit nonce
key = AESGCM.generate_key(bit_length=256)
nonce = os.urandom(12)

# 加密
aesgcm = AESGCM(key)
ct = aesgcm.encrypt(nonce, b'hello cryptography', b'')

# 解密
pt = aesgcm.decrypt(nonce, ct, b'')
assert pt == b'hello cryptography'
print(f'AES-256-GCM OK | key={key.hex()[:16]}... | ct={ct.hex()[:16]}...')
"
```

你應該看到類似：
```
AES-256-GCM OK | key=a3b1c4d5e6f7... | ct=8f2e3a4b5c6d...
```

### 為什麼選 `cryptography` 而不是 `PyCryptodome`？

兩者都能做密碼學操作，但設計哲學不同：

本課選 `cryptography` 的三個原因：

1. **API 更安全**：它會阻止你犯常見錯誤（重複 nonce、不驗 MAC），學習時遇到這些阻擋反而是好事
2. **`hazmat` 層夠底層**：需要手動操作 block cipher 時，`hazmat.primitives.ciphers` 可以逐 block 加密
3. **生態系更廣**：`requests`、`paramiko`、`pyOpenSSL` 都用它當後端

`PyCryptodome` 的差異：API 扁平（全部直接暴露）、預設 ECB、純 C 後端。它不是不好，但 `cryptography` 的安全預設更適合學習——它逼你做對的事。

但有些章節（例如 DES、RC4）`cryptography` 已經移除支援（因為不安全），那些我們會用 `PyCryptodome` 補充。所以也裝起來：

```bash
pip install pycryptodome
```

驗證：

```python
python3 -c "
from Crypto.Cipher import DES
# DES key 必須 8 bytes
key = b'8byteky!'
cipher = DES.new(key, DES.MODE_ECB)
ct = cipher.encrypt(b'testtest')  # DES block = 8 bytes
print(f'DES ECB OK | ct={ct.hex()}')
"
```

## Step 3：安裝 OpenSSL

Ubuntu 22.04 預裝 OpenSSL 3.x，確認版本：

```bash
openssl version
# OpenSSL 3.0.x ...
```

驗證基本操作：

```bash
# 產生 RSA-2048 私鑰
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /tmp/test.key 2>/dev/null

# 從私鑰導出公鑰
openssl pkey -in /tmp/test.key -pubout -out /tmp/test.pub

# 用公鑰加密
echo "hello openssl" | openssl pkeyutl -encrypt -pubin -inkey /tmp/test.pub -out /tmp/test.enc

# 用私鑰解密
openssl pkeyutl -decrypt -inkey /tmp/test.key -in /tmp/test.enc
# hello openssl

# 清理
rm /tmp/test.key /tmp/test.pub /tmp/test.enc

echo "OpenSSL RSA encrypt/decrypt OK"
```

### OpenSSL 版本差異：1.1.1 vs 3.x

主要差異：OpenSSL 3.x 引入 provider 架構，預設不載入 legacy 演算法（DES、RC4 等），需要加 `-provider legacy -provider default`。另外 3.x 預設只允許 TLS 1.2+，FIPS mode 改為 runtime provider。Part 3 的 DES 章節會詳細處理。

## Step 4：安裝 SageMath

SageMath 是本課最麻煩的安裝。它是一個完整的數學計算系統（底層包含 Python、GAP、Singular、PARI/GP、NumPy、SciPy），裝起來占 3–5 GB。

### 方案 A：Docker（推薦——最省事）

```bash
# 拉 SageMath 官方映像
docker pull sagemath/sagemath:latest

# 測試
docker run --rm sagemath/sagemath sage -c "
R = GF(2^8, 'x', modulus=x^8 + x^4 + x^3 + x + 1)
a = R.fetch_int(0x57)
b = R.fetch_int(0x83)
c = a * b
print(f'GF(2^8): 0x57 * 0x83 = {hex(c.integer_representation())}')
# 這是 AES MixColumns 裡的乘法
"
```

你應該看到：
```
GF(2^8): 0x57 * 0x83 = 0xc1
```

這個結果可以對照 FIPS 197（AES 規格書）的範例。

設定 alias 方便日後使用：

```bash
echo 'alias sage="docker run --rm -it -v \$(pwd):/work -w /work sagemath/sagemath sage"' >> ~/.bashrc
source ~/.bashrc

# 之後在任何目錄下直接打 sage 就能用
sage
```

### 方案 B：系統套件安裝

```bash
# Ubuntu 22.04
sudo apt install -y sagemath

# 這會拉進幾百個依賴，下載 + 安裝需要 15–30 分鐘
# 安裝完後驗證
sage --version
# SageMath version 9.5 (Ubuntu 22.04 repo 的版本)
```

> 方案 B 的版本通常比 Docker 舊一到兩個大版本。對本課來說差異不大——我們用到的有限體和橢圓曲線 API 很穩定。但如果你需要最新的 lattice 相關功能（Part 7），Docker 是比較安全的選擇。

### 驗證 SageMath 的關鍵功能

不論用哪個方案，跑這段驗證：

```python
# 在 sage 裡跑（不是普通 Python）

# 1. 有限體 GF(p)
F = GF(17)
print(f"GF(17): 3^(-1) = {F(3)^(-1)}")  # 模反元素
# GF(17): 3^(-1) = 6  (因為 3*6 = 18 ≡ 1 mod 17)

# 2. 橢圓曲線
E = EllipticCurve(GF(23), [1, 1])  # y^2 = x^3 + x + 1 over GF(23)
print(f"E(GF(23)) order = {E.order()}")
P = E.random_point()
print(f"Random point: {P}")

# 3. 整數分解
n = 1000000007 * 998244353  # 兩個大質數的乘積
print(f"factor({n}) = {factor(n)}")

# 4. 離散對數
g = F(3)       # 生成元
h = F(3)^11    # 目標
print(f"discrete_log in GF(17): log_3({h}) = {discrete_log(h, g)}")
# 應該是 11
```

如果四個都跑過，SageMath 環境沒問題。

## Step 5：安裝 pwntools

pwntools 是 CTF 攻擊工具，本課用它寫 exploit 腳本。

```bash
pip install pwntools
```

**重要限制**：pwntools 完整功能只在 Linux 上運作（它依賴 `/proc`、`ptrace` 等 Linux-specific 機制）。macOS 上 `process()` 和 `gdb` 功能不可用，但 `remote()`、`xor()`、`long_to_bytes()` 等我們最常用的功能可以。Windows 上完全不支援——用 WSL2。

驗證：

```python
python3 -c "
from pwn import *

# 工具函式
print(f'xor: {xor(b\"hello\", b\"world\").hex()}')
print(f'long_to_bytes(0x48656c6c6f) = {long_to_bytes(0x48656c6c6f)}')
print(f'bytes_to_long(b\"Hi\") = {bytes_to_long(b\"Hi\")}')

# 確認 context 能設定
context.log_level = 'error'
context.arch = 'amd64'
print('pwntools OK')
"
```

### 額外安裝：pwntools 的 crypto 工具

pwntools 附帶的 `pwnlib.util.fiddling` 有一些密碼學常用函式，但更完整的數學攻擊工具在 `sympy` 裡：

```bash
pip install sympy gmpy2
```

```python
python3 -c "
import gmpy2
# gmpy2 提供高效大數運算——比 Python 原生 int 快 10-100 倍
n = gmpy2.mpz(2)**1024 - 1
print(f'gmpy2: is_prime(2^1024 - 1) = {gmpy2.is_prime(n)}')

from sympy import factorint
# sympy 的整數分解（小數字用）
print(f'sympy: factor(1000000007 * 13) = {factorint(1000000007 * 13)}')
print('gmpy2 + sympy OK')
"
```

`gmpy2` 在 RSA 攻擊章節（Ch 20）會大量使用——Coppersmith 方法、Wiener attack 都需要高效的大數運算。

## Step 6：一次驗證全部

逐個確認：

```bash
# Python 套件
python3 -c "import cryptography; print(f'cryptography {cryptography.__version__}')"
python3 -c "import Crypto; print(f'PyCryptodome {Crypto.__version__}')"
python3 -c "from pwn import *; print('pwntools OK')"
python3 -c "import gmpy2; print(f'gmpy2 {gmpy2.version}')"
python3 -c "import sympy; print(f'sympy {sympy.__version__}')"

# AES-GCM 加解密 round-trip
python3 -c "
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os
key = AESGCM.generate_key(bit_length=256)
nonce = os.urandom(12)
aesgcm = AESGCM(key)
ct = aesgcm.encrypt(nonce, b'test', b'')
pt = aesgcm.decrypt(nonce, ct, b'')
assert pt == b'test'
print('AES-GCM round-trip OK')
"

# OpenSSL
openssl version

# SageMath (Docker 方案)
docker run --rm sagemath/sagemath sage -c "print('SageMath', version())"
# 或 native 方案: sage -c "print('SageMath', version())"
```

全部通過就可以開始。

## 踩雷集錦

### 1. SageMath 裝很久

系統套件安裝（`apt install sagemath`）下載量 1–2 GB，安裝需要 15–30 分鐘。Docker 方案首次 `pull` 也要下載 2–3 GB 的 image。這是正常的——SageMath 打包了整個數學計算生態系。

如果你趕時間：先跳過 SageMath，Ch 2（數論）和 Ch 9（GF(2^8)）才會大量用到。Ch 0–1 不需要它。

### 2. pwntools 只支援 Linux

pwntools 的 `process()`、`gdb.attach()` 依賴 Linux 的 `ptrace` 和 `/proc`。macOS 上 `pip install` 能成功但 `process()` 會炸。解法：WSL2 或 Linux VM。本課的攻擊腳本大多用 `remote()` 或純數學計算，macOS 上也能跑。

### 3. `cryptography` 安裝失敗——缺 Rust compiler

`cryptography` 從 v3.4 開始部分用 Rust 寫。pip 找不到預編譯 wheel 時需要 Rust compiler：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
pip install cryptography
```

Ubuntu 22.04 x86_64 通常有預編譯 wheel，不會遇到。

### 4. OpenSSL 3.x 拒絕 legacy 演算法

跑 DES 或 RC4 指令會得到 `Error setting cipher DES-ECB`。加上 `-provider legacy -provider default` 即可。Part 3 的 DES 章節會詳細處理。

### 5. gmpy2 安裝失敗——缺 libgmp

```bash
sudo apt install -y libgmp-dev libmpfr-dev libmpc-dev
pip install gmpy2
```

### 6. Docker 裡的 SageMath 無法存取 host 檔案

掛載目錄：`docker run --rm -it -v $(pwd):/work -w /work sagemath/sagemath sage`

## 本章重點整理

- 四個工具各有分工：`cryptography`（正規操作）、SageMath（數學實驗）、pwntools（攻擊腳本）、OpenSSL（TLS 實驗）
- `cryptography` 選擇的原因：API 安全預設、`hazmat` 層夠底層、生態系最廣
- SageMath 用 Docker 方案最省事；native 裝法佔 3–5 GB 且耗時
- pwntools 完整功能限 Linux；macOS/Windows 用 WSL2
- OpenSSL 3.x 預設不載入 legacy 演算法——Part 3 會處理

## 自我檢核

- [ ] `python3 verify_env.py` 全部 OK
- [ ] `openssl version` 顯示 3.x
- [ ] SageMath 能算 `GF(17)(3)^(-1)` 得到 6
- [ ] 能解釋為什麼本課同時需要 `cryptography` 和 `PyCryptodome`
- [ ] 知道 pwntools 在 macOS 上的限制是什麼

## 延伸閱讀

### 官方文件

- **[Python cryptography 文件](https://cryptography.io/en/latest/)**
  - **讀哪裡**：Fernet（高層 API）和 Hazmat / Primitives（底層 API）兩個區塊的首頁
  - **學什麼**：理解 `cryptography` 的分層設計——什麼時候用 Fernet、什麼時候下到 hazmat
  - **前提**：Python 基礎

- **[SageMath 教學](https://doc.sagemath.org/html/en/tutorial/)**
  - **讀哪裡**：Ch 1–3（基礎語法、有限體、橢圓曲線）
  - **學什麼**：SageMath 的互動式使用方法；Ch 2 和 Ch 22 會大量使用
  - **前提**：Python 基礎

### 工具對比

- **[PyCryptodome vs cryptography — 官方 FAQ](https://cryptography.io/en/latest/faq/#how-does-cryptography-compare-to-pycryptodome)**
  - 兩者的設計哲學差異；本章已摘要，官方版本更完整

→ [Ch 1 密碼學全貌](./01-cryptography-overview.md)
