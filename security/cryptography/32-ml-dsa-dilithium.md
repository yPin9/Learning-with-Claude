# Ch 32 — ML-DSA (Dilithium)：lattice 簽章

> 目標：把 NIST FIPS 204 的 ML-DSA（前身 Dilithium）拆開：基於 Module-LWE 的簽章、Fiat-Shamir with aborts 變形（如何避免 lattice 簽章的 leak issues）、簽章大小與 key 大小取捨。

## ML-DSA 是什麼

NIST FIPS 204（2024-08）。Dilithium → ML-DSA 改名。

基於 **Module-LWE + SIS**。Lyubashevsky 2009 提的 "Fiat-Shamir with aborts" 框架。

## 三個變體

```
                  Level   PK size   SK size   Sig size
ML-DSA-44         2       1312      2528      2420
ML-DSA-65         3       1952      4000      3293
ML-DSA-87         5       2592      4864      4595
```

**簽章 2-4.5 KB**（vs Ed25519 的 64 byte）— **大 30-70 倍**。這是 lattice 簽章的代價。

實務多選 **ML-DSA-65**（Level 3 = 192-bit security）。

## 為什麼 lattice 簽章難設計

Schnorr / ECDSA 簽章很簡單：

```
Schnorr: s = k - e × d
```

但 lattice 上類似公式會 **leak** 私鑰：每個簽章 reveal 部分 d 的資訊。多個簽章累積 → lattice 解 d。

NTRU-Sign（早期 lattice 簽章）2001 推、2006 被 Nguyen-Regev 攻破：**400 個簽章夠還原私鑰**。

修補：**Rejection sampling**（"with aborts"）。

## Fiat-Shamir with Aborts 框架

```
Sign(sk, m):
  loop:
    1. y ← R_q^k 隨機 small (mask)
    2. w = A @ y
    3. c = H(w, m)        ← challenge
    4. z = y + c × s       ← response
    5. if z 在 "safe range" 內 → return (z, c)
       else continue (reject, try new y)
```

**關鍵 trick**：第 5 step 的 reject — 如果 z 超出某個 「safe range」，重來。**只接受不洩漏資訊的簽章**。

數學上：rejection 確保 z 的 distribution **獨立於 secret s**。即使簽章很多次，attacker 也學不到 s。

統計上每幾次 sign 約 reject 一次（固定機率）。**簽章本身不洩漏**，但整個 process 多輪。

## 詳細 algorithm（簡化）

```
KeyGen():
  ρ ← random seed (公開)
  ζ ← random seed (秘密)
  Expand A from ρ          ← matrix A ∈ R_q^(k×ℓ)
  s_1, s_2 ← short vectors (秘密)
  t = A @ s_1 + s_2
  pk = (ρ, t)
  sk = (ρ, ζ, t, s_1, s_2)
  return (pk, sk)

Sign(sk, m):
  loop:
    y ← short random vector
    w = A @ y
    w_high = HighBits(w)     ← 取高位
    c̃ = H(m, w_high)
    c = sample challenge from c̃
    z = y + c @ s_1
    if z ∉ safe_range: continue
    if reveal_too_much: continue
    return (c̃, z, hint)

Verify(pk, m, sig):
  c̃, z, hint = sig
  c = sample from c̃
  w' = A @ z - c @ t
  w'_high = UseHint(hint, w')
  return c̃ == H(m, w'_high)
```

`HighBits` / `LowBits` / `hint` 是技術細節，把 polynomial coefficient 拆成 high / low 部分，hint 補正以節省簽章 size。

## 為什麼 reject 不洩漏

詳細數學要 paper 看。直覺：

```
y 在某 distribution
y + c × s 在 (shift by c × s) 的 distribution

兩者重疊區域 = Pr[accept]
拒絕 fall outside → 接受的簽章 distribution 等於 y 的 distribution
不依賴 s
```

技術上是「**rejection sampling**」做 indistinguishable distribution。

## 與 Schnorr 的比較

```
Schnorr (基於 DLP):
  s = k - e × d
  
ML-DSA (基於 Module-LWE):
  z = y + c × s_1   (with rejection)
  
Schnorr 簽章 64 byte (256-bit r + 256-bit s)
ML-DSA 簽章 ~3 KB
```

**Schnorr 因為 modular arithmetic 簡單，small output**。ML-DSA 處理大 polynomial vector，self-contained but 大。

## SLH-DSA、FALCON 對比

NIST 同時標準化三個簽章：

```
ML-DSA (Dilithium):    lattice，速度快，sig 大
SLH-DSA (SPHINCS+):     hash-based，sig 極大 (8-50KB) 但 long-term 信心
FN-DSA (FALCON):        lattice (NTRU)，sig 小但實作難
```

**FALCON** 簽章只 ~700 byte（比 Dilithium 小 4×），但內部用 floating-point Gaussian 採樣 — const-time 實作極難（floating-point timing 易洩漏）。**多數場景仍選 Dilithium**。

## 性能

```
Operation         Time (modern CPU)
KeyGen            ~ 80 µs
Sign              ~ 200 µs (含 rejection retry)
Verify            ~ 50 µs
```

**比 Ed25519 慢 5-10 倍**。Verify 比 Sign 快（不需 rejection）。

對 web TLS handshake：每 connection 1 次 sign + 1 次 verify，多 200-300 µs。可接受。

## 實作 source

```
github.com/pq-crystals/dilithium
PQClean/dilithium
liboqs

Python:
  dilithium-py (academic)
  pqcrypto package
```

## ML-DSA 範例（pseudocode）

```python
from dilithium_py.ml_dsa import ML_DSA_65

# KeyGen
sk, pk = ML_DSA_65.keygen()
print(f"pk: {len(pk)} bytes")    # 1952
print(f"sk: {len(sk)} bytes")    # 4000

# Sign
sig = ML_DSA_65.sign(sk, b"important message")
print(f"sig: {len(sig)} bytes")  # 3293

# Verify
ok = ML_DSA_65.verify(pk, b"important message", sig)
assert ok
```

## hybrid 簽章

對長期 trust（CA 證書、code signing），用 hybrid signature：

```
sig = Ed25519.sign(sk_ed, m) || ML-DSA.sign(sk_dsa, m)
verify: 兩者都 valid
```

任一未來被破不影響整體。code signing 與 PKI 已開始 hybrid 試 enrollment。

## 一個常見誤解

「lattice 簽章需要 quantum 才會用上」

**錯**。雖然 quantum 是主因，但**現在 ML-DSA 已可用**：

- 任何長期重要簽章（CA root、code signing）應 hybrid
- DNSSEC 等系統開始試
- 未來 5 年標準 PKI 會逐步遷移

**Quantum 還沒來，但 PQC 已是 production reality**。

## 自我檢核

- [ ] 我能解釋 Fiat-Shamir with aborts 為什麼避免簽章洩漏
- [ ] 我能說出 ML-DSA 三個變體的 size
- [ ] 我能比較 ML-DSA 與 Ed25519 在 sig size、speed 上的差異
- [ ] 我能說出 NIST 三個簽章標準（ML-DSA、SLH-DSA、FALCON）的取捨
- [ ] 我能寫一個 hybrid 簽章流程

下一章看 SLH-DSA (SPHINCS+) — hash-based 簽章。

→ [Ch 33 SLH-DSA / SPHINCS+](./33-slh-dsa-sphincs.md)
