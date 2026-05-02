# Ch 20 — RSA 攻擊：Wiener、common modulus、Hastad、Bleichenbacher

> 目標：把 RSA 的四大經典攻擊講清楚：Wiener（小私鑰的 continued fraction 攻擊）、common modulus（不同 e 同 n）、Hastad broadcast（小 e 同訊息給多人）、Bleichenbacher 1998（PKCS#1 v1.5 padding oracle，實戰殺 SSL）。

## 攻擊地圖

```
RSA 攻擊面：
├── 因式分解 n（理論最強，但 2048-bit 做不到）
│   ├── GNFS（一般情況）
│   ├── ECM（小因子）
│   └── Pollard p-1, Pollard rho（特殊 p）
├── 私鑰 d 太小：Wiener、Boneh-Durfee
├── e 太小（如 e=3）+ 訊息特殊：Hastad、Coppersmith
├── Common modulus：同 n 給多人 → 兩個 e 互質就破
├── Padding 攻擊：Bleichenbacher、Manger
├── Implementation 攻擊：timing、power、fault
└── 隨機數錯：ROCA、Mersenne 等
```

整節挑四個經典展開。

## 1. Wiener Attack：小 d 災難

```
場景：d 太小（d < n^0.25 / 3）
攻擊：用 continued fraction 找 d
成本：< 1 秒（純數學）
```

**為什麼有人會選小 d**：希望解密快（小 d → `pow(c, d, n)` 快）。但 1990 Wiener 證明：

```
若 d < (1/3) × n^(1/4)，則 d 能從 (n, e) 公開資訊算出
```

**continued fraction expansion**：把 e/n 展開成 continued fraction，d/k 必出現在某個 convergent 裡（k 是 e×d - 1 = k×φ(n) 的 k）。試每個 convergent 檢查能否解密。

```python
def continued_fraction(num, den):
    """e/n 的 continued fraction expansion"""
    cf = []
    while den:
        cf.append(num // den)
        num, den = den, num % den
    return cf

def convergents(cf):
    """從 cf 計算 convergents (h_i / k_i)"""
    h_prev2, h_prev1 = 1, cf[0]
    k_prev2, k_prev1 = 0, 1
    yield (h_prev1, k_prev1)
    for ai in cf[1:]:
        h = ai * h_prev1 + h_prev2
        k = ai * k_prev1 + k_prev2
        yield (h, k)
        h_prev2, h_prev1 = h_prev1, h
        k_prev2, k_prev1 = k_prev1, k

def wiener_attack(n, e):
    cf = continued_fraction(e, n)
    for k, d_candidate in convergents(cf):
        if k == 0: continue
        # 檢查 d_candidate 是否正確
        # k × φ(n) = e × d_candidate - 1
        phi_candidate = (e * d_candidate - 1) // k
        if phi_candidate <= 0: continue
        # n - φ(n) + 1 = p + q
        # x² - (p+q)x + n = 0
        s = n - phi_candidate + 1
        disc = s*s - 4*n
        if disc >= 0:
            sqrt_disc = isqrt(disc)
            if sqrt_disc * sqrt_disc == disc:
                # 找到了
                return d_candidate
    return None
```

**修補**：選 d > n^(1/4)（實務 e = 65537 + d 無人為小化 = 沒問題）。

## 2. Common Modulus Attack

```
場景：兩個用戶 Alice 與 Bob 用同個 n，不同 (e_a, e_b)
       gcd(e_a, e_b) = 1
       同訊息 m 給兩人加密
攻擊：可以還原 m
成本：ext-Euclid + 兩次 pow
```

對 attacker：

```
c_a = m^(e_a) mod n
c_b = m^(e_b) mod n

由於 gcd(e_a, e_b) = 1，存在 (u, v) 使
  u × e_a + v × e_b = 1

則 c_a^u × c_b^v = m^(u×e_a + v×e_b) = m^1 = m
```

```python
def common_modulus_attack(c_a, e_a, c_b, e_b, n):
    g, u, v = ext_gcd(e_a, e_b)
    assert g == 1
    # 處理 u 或 v 為負（用 modular inverse）
    if u < 0:
        c_a = pow(c_a, -1, n)
        u = -u
    if v < 0:
        c_b = pow(c_b, -1, n)
        v = -v
    return (pow(c_a, u, n) * pow(c_b, v, n)) % n
```

**修補**：每用戶用獨立 n。**「分享 n 省記憶體」是經典踩雷**。

## 3. Hastad Broadcast Attack

```
場景：e 很小（如 e = 3）
       同訊息 m 給多個用戶（不同 n_i）
       e 個（或更多）密文：c_1, c_2, c_3
攻擊：用 CRT 算出 m^e，再開 e 次方根
成本：CRT + 整數開根號
```

對 e = 3，3 個 (n_i, c_i)：

```
c_1 = m^3 mod n_1
c_2 = m^3 mod n_2
c_3 = m^3 mod n_3

CRT 合併 → x ≡ m^3 (mod n_1 × n_2 × n_3)
若 m < n_i 對所有 i，則 m^3 < n_1 × n_2 × n_3
故 x = m^3（在 ℤ 中，無 mod）
m = cbrt(x)
```

```python
def hastad_attack(ct_pairs):
    """ct_pairs = [(c1, n1), (c2, n2), (c3, n3)]"""
    cs = [c for c, _ in ct_pairs]
    ns = [n for _, n in ct_pairs]
    me_e = crt(cs, ns)
    from sympy import integer_nthroot
    m, exact = integer_nthroot(me_e, 3)
    if exact:
        return m
    return None
```

**修補**：用 OAEP padding（每次加密 randomness 不同 → CRT 後不是 m^3）。或用 e = 65537（4096-bit + e=65537 仍安全）。

## 4. Bleichenbacher 1998：PKCS#1 v1.5 Padding Oracle

**最有名的 RSA attack**。Daniel Bleichenbacher 1998 paper "Chosen Ciphertext Attacks Against Protocols Based on the RSA Encryption Standard PKCS #1"。

### 場景

server 收到 RSA encrypt 的訊息，**回不同錯誤給「padding 對 / 不對」**：

```
SSL/TLS 1.2 RSA key exchange:
  client 用 server cert public key 加密 pre-master secret（PKCS#1 v1.5 padded）
  server 解密、檢查 padding
    padding 對  → 繼續
    padding 錯  → close connection / 回 alert
```

attacker MITM 看 connection close 或 timing → 知道 padding 對 / 錯。

### 攻擊原理

回憶 PKCS#1 v1.5 padded 的開頭必為 `0x00 0x02`：

```
m_padded = 0x00 0x02 <PS> 0x00 <plaintext>
```

attacker 構造 `c' = c × s^e mod n`（用 RSA homomorphic 性質）：

```
c' 解密後 = m × s mod n
```

如果 `m × s mod n` 仍以 `0x00 0x02` 開頭（PKCS conformant），server 接受。

**對「m × s 是否 PKCS conformant」這個 yes/no oracle，Bleichenbacher 證明**：用約 **百萬次 query** 能完整還原 m。

具體 algorithm 比較複雜（有四個 step：blinding、interval narrowing、合 interval、final），但核心 idea 是「用 oracle 二分搜尋 m 在哪個區間」。

### 真實影響

- **1998**：影響 SSL/TLS 1.0
- **2014 POODLE**：CBC-mode 變種
- **2016 DROWN**：跨 protocol 攻擊（SSLv2 ↔ TLS）
- **2017 ROBOT (Return Of Bleichenbacher's Oracle Threat)**：Hanno Böck 等
  - 影響 Facebook、Cisco、IBM、Erlang、F5 等
  - PKCS#1 v1.5 RSA 在 TLS 1.2 仍存在
  - 19 年後仍可破

### 修補

**完全的解**：**棄用 PKCS#1 v1.5 RSA encryption**：

- TLS 1.3 砍掉 RSA key exchange（只 ECDHE / DHE）
- TLS 1.2 with **CCS_INJECTION mitigation** 或單純不啟用 RSA cipher suite
- 改用 OAEP padding

實務 mitigation：**寫 server 不對 padding 對 / 錯回不同訊息**。所有錯誤回同樣 generic error，**且 timing 統一**。

但這非常難正確實作（timing leak 隱蔽）— **唯一可靠就是換 OAEP / 用 ECDHE**。

## ROCA（2017）：Infineon RSA key 弱

特殊 RSA 攻擊：**Infineon 晶片產生的 RSA key 有結構**，2017 Nemec / Sys / Svenda paper 揭露。

問題：Infineon 用某 prime generation 算法導致 p, q 有特殊結構（小 prime power）。Coppersmith-style attack 能在合理時間因式分解：

- 1024-bit Infineon RSA：99 美元 GPU 1 小時
- 2048-bit Infineon RSA：40000 美元 ~30 天

影響：Estonia、Slovak ID 卡、TPM、PGP key — 大規模 revocation。

**教訓**：**RSA key 必用 standard random prime generation**，不要為了 performance 走 shortcut。

## Coppersmith Method：低指數 + 部分已知

Don Coppersmith 1996 一系列 paper。**LLL lattice reduction 的應用**：

```
若 e = 3，attacker 知道 m 的部分 bits（如高 256 bit），
  Coppersmith 能在多項式時間找出剩下的 m
```

具體：對多項式 `f(x) = (m_known + x)^3 - c mod n`，找 small root。LLL 算法處理。

**修補**：用 OAEP（每次 random padding，attacker 拿不到 m 的部分）。

## 總結：寫 RSA 安全程式的紀律

```
1. 用 e = 65537（不要 e = 3）
2. 用 OAEP（加密）/ PSS（簽章）padding，不用 PKCS#1 v1.5
3. 用 CRT 加速但簽後 verify（防 fault attack）
4. 永遠用 trusted library（OpenSSL / cryptography）
5. RSA key ≥ 2048-bit（最低）/ 3072-bit（推薦）
6. 不分享 n 給多人
7. Const-time 實作
8. 對 randomness 來源嚴格（CSPRNG）
```

**現代建議：新系統用 ECC / EdDSA，不新建 RSA**。RSA 留給維護 legacy。

## 一個常見誤解

「RSA 算法本身被破了嗎？」

**沒有**。**演算法層面 RSA 仍安全**，假設因式分解 2048-bit 不可解（目前正確）。所有 attack 是**特定錯誤用法**：

- 太小 d、太小 e
- 共用 n
- 沒 OAEP padding
- 弱隨機數
- 實作有 side channel

**正確用 RSA-OAEP-2048 仍很安全**。但「正確用」門檻高 — 比 ECC 容易出錯。**這就是 ECC 取代 RSA 的根本原因**：算法 + library 都更難寫錯。

## 自我檢核

- [ ] 我能寫 Wiener attack 並破小 d 的 RSA
- [ ] 我能寫 common modulus attack
- [ ] 我能寫 Hastad broadcast attack
- [ ] 我能解釋 Bleichenbacher 1998 與 ROBOT 2017
- [ ] 我能說出 RSA 安全用法的至少 5 條紀律
- [ ] 我知道 ROCA 是什麼以及為什麼影響那麼大

下一章補回 DH 細節：safe prime、small subgroup、Logjam。

→ [Ch 21 Diffie-Hellman 細節與 Logjam](./21-dh-details-logjam.md)
