# Ch 21 — DH 細節 + Logjam

> 目標：深入 DH 的安全參數選擇，理解 safe prime 為什麼重要，知道 Logjam 2015 利用的是什麼。

---

## 為什麼需要這章

Ch 18 教了 DH 的基本流程：選 p, g，各自生成 a/b，交換 g^a/g^b，算出共享 secret g^ab。但「選 p, g」這四個字背後有大量的安全陷阱：

- p 不是 safe prime → small subgroup attack
- p 的 bit 數太小 → Logjam
- 大量 server 用同一組 (p, g) → precomputation attack
- 不驗證對方公鑰 → trivial key recovery

這些不是理論問題——2015 年的 Logjam 攻擊影響了當時 8.4% 的 HTTPS server。

---

## 先建立直覺

DH 的安全性取決於 DLP 的難度。但 DLP 的難度不只取決於 p 的大小——還取決於 p 的**結構**。

```
差的 p：p - 1 的因數都很小（smooth number）
  → Pohlig-Hellman 演算法可以分治 DLP
  → 在每個小因數的子群裡解小 DLP，再用 CRT 合併

好的 p：p = 2q + 1（q 也是質數）
  → p - 1 = 2q，只有兩個因數：2 和 q
  → Pohlig-Hellman 沒用（q 是大質數，子群的 DLP 跟原問題一樣難）
```

---

## 核心概念：Safe Prime 與群結構

### Z_p* 的群結構

Z_p* = {1, 2, ..., p-1}，在 mod p 下的乘法群。這個群的 order 是 p-1。

由 Lagrange 定理：群中每個元素的 order 都整除群的 order（p-1）。

```
p = 23 → p - 1 = 22 = 2 × 11

Z_23* 的子群結構：
  order 1:  {1}
  order 2:  {1, 22}          （22 = -1 mod 23）
  order 11: {1, 2, 3, 4, 6, 8, 9, 12, 13, 16, 18}
  order 22: {所有 22 個元素}   ← 整個群

如果 g 的 order 是 22，g 是 generator
如果 g 的 order 是 11，g 的冪次只跑 11 個元素
如果 g 的 order 是 2，g 的冪次只有 {1, g}
```

### Safe Prime 的定義

**Safe prime（安全質數）**：p = 2q + 1，其中 q 也是質數。q 稱為 **Sophie Germain prime**。

```
p = 2q + 1 → p - 1 = 2q

p - 1 的因數只有：1, 2, q, 2q
Z_p* 的子群 order 只有：1, 2, q, 2q

子群結構非常單純：
  order 1:  {1}           ← trivial
  order 2:  {1, p-1}      ← 只有兩個元素
  order q:  大小 q 的子群  ← 幾乎跟整個群一樣大
  order 2q: 整個群         ← 整個群
```

使用 safe prime 時，generator g 的 order 要麼是 2（危險），要麼是 q（好），要麼是 2q（好）。只要排除 order 2 的情形（g = p-1），DH 的安全性就有保障。

### 範例：SageMath 驗算

```python
# SageMath
p = 23  # safe prime: 23 = 2×11 + 1, q = 11
F = GF(p)

# 檢查每個元素的 order
for g in range(1, p):
    order = F(g).multiplicative_order()
    print(f"g={g:2d}, order={order:2d}")
    # order 只會是 1, 2, 11, 或 22
```

---

## 底層機制：Small Subgroup Attack

### 攻擊原理

如果 p-1 有小因數 r，Z_p* 就有一個 order r 的子群 H。攻擊者可以：

```
攻擊者 Mallory 送給 Alice 的「公鑰」：
  B' = h （h 是 order r 的子群的元素）

Alice 計算 shared secret：
  s = B'^a mod p = h^a mod p

因為 h 的 order 是 r：
  s = h^(a mod r) mod p

s 只有 r 種可能值！
如果 r 很小（例如 r = 3），Mallory 嘗試 3 次就能猜到 s
→ 也就知道了 a mod r
```

重複這個攻擊（用不同的小因數 r1, r2, r3, ...），用 CRT 合併：

```
知道 a mod r1, a mod r2, ..., a mod rk
用 CRT：a mod (r1 × r2 × ... × rk)

如果 r1 × r2 × ... × rk 足夠大，就恢復了完整的 a
```

這就是 **Pohlig-Hellman 攻擊**在 DH 中的應用。

### Python PoC：Small Subgroup Attack

```python
"""
Small Subgroup Attack on DH
當 p-1 有小因數時，攻擊者可以恢復私鑰
"""

def pohlig_hellman_factor(g, h, p, r):
    """
    在 order r 的子群中解 DLP：
    找 x 使得 g^x ≡ h (mod p)，x ∈ [0, r-1]
    r 很小時暴力搜索即可
    """
    # 把 g, h 映射到 order r 的子群
    order = p - 1
    g_sub = pow(g, order // r, p)  # order r 的 generator
    h_sub = pow(h, order // r, p)  # h 在子群中的像

    # 暴力搜索
    val = 1
    for x in range(r):
        if val == h_sub:
            return x
        val = (val * g_sub) % p
    return None


def small_subgroup_attack(g, A, p, factors):
    """
    用 Pohlig-Hellman 恢復 a mod (各因數的乘積)
    factors: p-1 的小因數列表
    """
    remainders = []
    moduli = []

    for r in factors:
        x_mod_r = pohlig_hellman_factor(g, A, p, r)
        if x_mod_r is not None:
            remainders.append(x_mod_r)
            moduli.append(r)
            print(f"  a ≡ {x_mod_r} (mod {r})")

    # CRT 合併
    from functools import reduce
    def crt_pair(r1, m1, r2, m2):
        """合併兩個同餘方程"""
        def ext_gcd(a, b):
            if a == 0: return b, 0, 1
            g, x, y = ext_gcd(b % a, a)
            return g, y - (b // a) * x, x

        _, s, t = ext_gcd(m1, m2)
        M = m1 * m2
        return (r1 * m2 * t + r2 * m1 * s) % M, M

    r, m = remainders[0], moduli[0]
    for i in range(1, len(remainders)):
        r, m = crt_pair(r, m, remainders[i], moduli[i])

    return r, m


# 演示：用 smooth p（p-1 只有小因數）
# p - 1 = 2 × 3 × 5 × 7 × 11 × 13 = 30030 → p = 30031
p = 30031
assert all(pow(2, p-1, p) == 1 for _ in [1])  # 確認 p 是質數

# p - 1 = 30030 = 2 × 3 × 5 × 7 × 11 × 13 × ... 
# 讓我們找一個 p 使得 p-1 smooth
# 用 p = 15013 → p-1 = 15012 = 4 × 3 × 1251 = 4 × 3753 
# 用更好的例子
p = 31 * 41 + 1  # = 1272? 不是質數
# 手動選一個好的例子
p = 1009  # 質數, p-1 = 1008 = 2^4 × 3^2 × 7

g = 11  # generator of Z_1009*
a = 587  # Alice 的私鑰
A = pow(g, a, p)

print("=== Small Subgroup Attack ===")
print(f"p = {p}, p-1 = {p-1} = 2^4 × 3^2 × 7")
print(f"g = {g}, A = g^a mod p = {A}")
print(f"真正的 a = {a}")
print()

# 攻擊：利用 p-1 的小因數
small_factors = [16, 9, 7]  # 2^4, 3^2, 7
print("用 Pohlig-Hellman 恢復 a mod (小因數)：")
a_partial, mod = small_subgroup_attack(g, A, p, small_factors)
print(f"\n恢復 a ≡ {a_partial} (mod {mod})")
print(f"真正的 a mod {mod} = {a % mod}")
print(f"攻擊成功？{a_partial == a % mod}")
if mod >= p - 1:
    print(f"完全恢復 a = {a_partial}")
```

### 防禦：safe prime + 公鑰驗證

```python
def validate_dh_public_key(their_public, p):
    """驗證 DH 公鑰（safe prime p = 2q + 1 的情形）"""
    # 基本範圍檢查
    if their_public <= 1 or their_public >= p - 1:
        raise ValueError("Public key out of range")

    # safe prime 時，確認在 order q 的子群中
    q = (p - 1) // 2
    if pow(their_public, q, p) != 1:
        raise ValueError("Public key not in safe subgroup")

    return True
```

---

## 進一步用法：Logjam 攻擊（2015）

### 攻擊背景

2015 年，研究者發現：

1. **大量 HTTPS server 用同一組 DH 參數**：Apache 的預設 DH group 只有 1024 bit
2. **TLS 支援降級到 512-bit DH**：export-grade crypto 遺毒（1990 年代美國加密出口管制）
3. **Number Field Sieve 的 precomputation 可以分攤到很多連線**

### Logjam 的兩步攻擊

```
Step 1: Precomputation（offline，花很多錢但只做一次）
  針對一個特定的 512-bit prime p
  用 Number Field Sieve 做 precomputation
  花費：一台高端 server 跑數週

Step 2: Online attack（每次連線只需幾分鐘）
  利用 precomputation 的結果
  解每個 DLP 只需 ~70 秒

攻擊流程：
  1. Client 和 Server 開始 TLS handshake
  2. 攻擊者（MITM）把 DH group 降級到 512-bit（export_dh）
  3. Server 用 512-bit DH group（如果支援的話）
  4. 攻擊者用 precomputed 資料在 ~70 秒內解出 DLP
  5. 攻擊者取得 session key → 解密通訊
```

### 攻擊規模

```
512-bit DH：
  precomputation 成本：~$1,000 in cloud compute
  online attack：~70 秒
  影響：8.4% 的 Alexa Top 1M HTTPS domains

768-bit DH：
  precomputation 成本：~$10M
  state-level attacker 可行

1024-bit DH：
  precomputation 成本：~$100M-$1B
  論文推測 NSA 可能已經做了
  （2015 年當時約 18% 的 Top 1M 用 1024-bit DH）

2048-bit DH：
  目前不可行
```

### Logjam 跟 FREAK 的區別

| 攻擊 | 年份 | 降級到 | 打什麼 |
|---|---|---|---|
| FREAK | 2015/03 | RSA_EXPORT (512-bit RSA) | RSA key transport |
| Logjam | 2015/05 | DHE_EXPORT (512-bit DH) | DH key exchange |

兩者都是 **export-grade crypto 遺毒**——1990 年代美國政府限制出口 > 512-bit 的加密，強制軟體支援弱密碼。2015 年這些弱密碼仍然在很多 server 上啟用。

---

## 對比與取捨

### DH 參數選擇指南

| 參數 | 建議 | 理由 |
|---|---|---|
| p 的 bit 數 | ≥ 2048 | 1024 可能被 state-level 打；512 已被學術打 |
| p 的類型 | Safe prime (p=2q+1) | 防 small subgroup attack |
| g | 2 或 5 | 搭配 safe prime 就安全；小 g 加速計算 |
| 參數來源 | RFC 7919 標準 group | 不要自己生成（可能有 backdoor） |
| key reuse | 每次 session 新生成 | Forward secrecy |

### RFC 7919 標準 DH Groups（ffdhe groups）

| Group | Size | 用途 |
|---|---|---|
| ffdhe2048 | 2048 bit | 最小建議 |
| ffdhe3072 | 3072 bit | NIST 128-bit 安全等級 |
| ffdhe4096 | 4096 bit | 長期安全 |
| ffdhe6144 | 6144 bit | 偏執級別 |
| ffdhe8192 | 8192 bit | 很慢，少用 |

### DH vs ECDH 性能

| 操作 | DH-2048 | ECDH-P256 | X25519 |
|---|---|---|---|
| Key generation | ~1.5 ms | ~0.1 ms | ~0.05 ms |
| Key agreement | ~1.5 ms | ~0.3 ms | ~0.15 ms |
| Key size | 2048 bit | 256 bit | 256 bit |
| 等效對稱安全 | ~112 bit | ~128 bit | ~128 bit |

結論：**ECDH 在每個面向都優於 finite-field DH**。TLS 1.3 優先使用 X25519 和 P-256，finite-field DH 只是為了相容性。

---

## 踩雷集錦

### 雷 1：自己生成 DH 參數

```python
# 錯誤：自己隨便選 p
p = random_prime(2^2048)  # 不是 safe prime！

# 正確：用標準 group
# RFC 7919 ffdhe2048 的 p
p = 0xFFFFFFFF...  # (用 RFC 7919 的完整值)
```

自己生成 DH 參數有兩個風險：(1) 可能不是 safe prime；(2) 可能被植入 backdoor（generator 的 order 特別選過）。用標準化的 group 可以避免這些問題。

### 雷 2：支援 export-grade ciphersuite

```
# 錯誤：TLS 設定
ssl_ciphers = "ALL"  # 包含 EXPORT ciphersuite

# 正確：
ssl_ciphers = "ECDHE+AESGCM:DHE+AESGCM"
# 或直接用 TLS 1.3
```

2024 年了，還在用 TLS 1.0/1.1 的 server 應該立刻升級。

### 雷 3：不做 DH 公鑰驗證

即使用了 safe prime，不驗證公鑰仍然危險。公鑰如果是 0、1、或 p-1，shared secret 就變成可預測的值。

### 雷 4：1024-bit DH 還在用

截至 2024 年，仍有不少舊設備（VPN gateway、legacy server）使用 1024-bit DH。國家級攻擊者可能已經做了 precomputation。

### 雷 5：混淆 DH 和 static DH

- **Ephemeral DH (DHE)**：每次 session 新生成 a, b → 有 forward secrecy
- **Static DH**：公鑰固定、長期使用 → 沒有 forward secrecy

TLS 1.3 只允許 ephemeral。

---

## 進階

### Number Field Sieve 的 Precomputation

NFS 解 DLP 分兩步：

```
Step 1: Precomputation（只取決於 p，跟 a 無關）
  - 收集 relations（smooth numbers 的因式分解）
  - 線性代數（高斯消去法在大矩陣上）
  - 花費：NFS 總運算量的 ~95%

Step 2: Individual DLP（每個 a 各自做）
  - 花費：NFS 總運算量的 ~5%
```

關鍵洞見：如果很多 server 用同一個 p，precomputation 只做一次，之後每個 DLP 都很快。

這就是為什麼 Logjam 論文說「如果 NSA 花了幾億美金做 1024-bit 的 precomputation，他們可以被動解密大量 VPN 流量」。

### Trapdoor DH Groups

理論上，有人可以生成一個看起來正常的 DH group，但背後藏了 trapdoor（例如 p-1 有特殊的因式分解結構）。

這就是為什麼建議用 **標準化的 group**（RFC 7919）——這些 group 的 p 是「nothing-up-my-sleeve numbers」，用公開的方法生成，任何人都可以驗證。

### 跟 Dual_EC_DRBG 的類比

NSA 在 NIST 標準 Dual_EC_DRBG 中植入 backdoor，用的也是類似的手法：選了一組看起來隨機但其實有 trapdoor 的參數。2013 年 Snowden 洩露後被確認。

教訓：**永遠不要信任不透明的參數選擇過程**。

---

## 動手練習

1. **驗證 safe prime**：用 SageMath 檢查 RFC 7919 ffdhe2048 的 p 是否滿足 p = 2q + 1。
   ```python
   p = 0xFFFFFFFF...  # ffdhe2048
   q = (p - 1) // 2
   print(f"p is prime: {is_prime(p)}")
   print(f"q is prime: {is_prime(q)}")
   ```

2. **Small Subgroup Attack**：選一個 p-1 smooth 的質數（例如 p = 1009），實作 Pohlig-Hellman 攻擊。

3. **測量 precomputation 效果**：用 SageMath 在一個 64-bit 質數上比較：(a) 從頭解 DLP，(b) 先做 precomputation 再解多個 DLP。觀察時間差。

4. **TLS 掃描**：用 `testssl.sh` 掃描你自己的 server（或測試站），檢查是否支援弱 DH group。
   ```bash
   ./testssl.sh --dh-size example.com
   ```

---

## 重點整理

```
Safe Prime (p = 2q + 1)：
  p-1 = 2q 只有兩個因數：2 和 q
  防止 Pohlig-Hellman / small subgroup attack
  Z_p* 的子群只有 order 1, 2, q, 2q

Small Subgroup Attack：
  攻擊者送 order r 的元素當公鑰
  受害者的 shared secret 只有 r 種可能
  多次攻擊 + CRT → 恢復完整私鑰

Logjam (2015)：
  NFS precomputation 分攤到多個連線
  512-bit DH：$1000 + 70 秒/連線
  1024-bit DH：可能已被國家級攻擊者做了 precomputation
  根因：export-grade crypto 遺毒

防禦：
  p ≥ 2048 bit（safe prime）
  用 RFC 7919 標準 group
  每次 session 新生成 ephemeral key
  驗證對方公鑰
  優先用 ECDH（X25519）→ 更快、更安全
```

---

## 自我檢核

- [ ] 我能解釋 safe prime 的定義和為什麼重要
- [ ] 我能描述 small subgroup attack 的流程
- [ ] 我能解釋 Logjam 攻擊的兩步（precomputation + online）
- [ ] 我知道為什麼大量 server 用同一組 DH 參數會讓 precomputation 攻擊更致命
- [ ] 我能解釋 ephemeral DH vs static DH 的 forward secrecy 差異
- [ ] 我知道 ECDH 在性能和安全性上都優於 finite-field DH

---

## 延伸閱讀

- **"Imperfect Forward Secrecy: How Diffie-Hellman Fails in Practice"**（Adrian et al., 2015）：Logjam 論文
- **RFC 7919**："Negotiated Finite Field Diffie-Hellman Ephemeral Parameters for TLS"
- **"Measuring the Security Harm of TLS Crypto Shortcuts"**（Springall et al., 2016）：量化 DH 參數 reuse 的危害
- **"A Kilobit Hidden SNFS Discrete Logarithm Computation"**（Fried et al., 2017）：trapdoor DH group 的具體構造

---

## 下一章連結

[Ch 22 — 橢圓曲線數學](./22-elliptic-curves-math.md)：finite-field DH 太慢、key 太長。橢圓曲線把同等安全性壓進 256 bit——來看它的數學。
