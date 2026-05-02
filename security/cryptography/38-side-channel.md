# Ch 38 — Side-channel：timing、power、EM、cache

> 目標：搞懂「演算法數學上對，但實作會洩漏資訊」的攻擊面。Kocher 1996 timing attack、power analysis（DPA）、EM 輻射、cache timing（FLUSH+RELOAD、PRIME+PROBE）、Spectre / Meltdown 的密碼學變種。

## Side-channel 是什麼

```
Theoretical attack:
  attacker 看 input/output → 推 secret
  防禦：演算法數學強

Side-channel attack:
  attacker 觀察「計算過程的副作用」
  - 時間 (timing)
  - 電力 (power consumption)
  - 電磁輻射 (EM)
  - 聲音 (acoustic)
  - cache 行為
  - 錯誤訊息
  → 推 secret
  防禦：const-time, blinding, masking
```

**演算法對 + 實作有洩漏 = 整個系統破**。Side-channel 是密碼工程的最大踩雷區。

## Kocher 1996 Timing Attack

Paul Kocher 的 paper 「Timing attacks on implementations of Diffie-Hellman, RSA, DSS, and other systems」。

**Square-and-multiply 的洩漏**：

```python
def modpow(base, exp, mod):
    result = 1
    while exp:
        if exp & 1:           # ← 看 bit
            result = (result * base) % mod  # ← 多算一次
        base = (base * base) % mod
        exp >>= 1
    return result
```

每個 `exp & 1` 是 1 → 多做一次 multiplication → 多用 microseconds。

attacker 觀察解密時間（多次平均）→ 推算 exp 的每個 bit → 拿到 RSA 私鑰。

實際 Kocher 1996 demo：對 OpenSSL 早期 RSA 實作，幾百萬次 query 約 1 小時還原 RSA key。

修復：

```python
def modpow_const_time(base, exp, mod):
    result = 1
    base_squared = base
    for i in range(exp.bit_length()):
        # 永遠做 multiplication，但結果用 mask 選
        bit = (exp >> i) & 1
        candidate = (result * base_squared) % mod
        result = candidate if bit else result   # ← const-time select 必用 bit op
        base_squared = (base_squared * base_squared) % mod
    return result
```

實際還要避免「`if bit` 編譯成 branch」 — 用 const-time conditional move（`cmov` / bit mask）。

## 實際 attack 在 Network 上

Brumley & Boneh 2003 「Remote Timing Attacks are Practical」。從 network 端 attack OpenSSL RSA：

- 同 LAN：幾小時破譯 1024-bit RSA key
- 同 WAN：較慢但仍可行

關鍵：**統計上累積 timing 差異**。即使 jitter 大，n 次平均仍能推。

修復：OpenSSL 後來加 RSA blinding（randomize input → timing 與 secret 不相關）。

## RSA Blinding

```python
def rsa_decrypt_blinded(c, d, n):
    r = random()
    r_inv = modinv(r, n)
    blinded = (c * pow(r, e, n)) % n   # 先 blind
    decrypted_blinded = pow(blinded, d, n)
    return (decrypted_blinded * r_inv) % n
```

每次解密用不同 r → modpow 的 input 與 attacker 看的 c 無關 → timing 不洩漏 d。

OpenSSL、libsodium 都用 blinding。

## Cache Timing Attack

CPU cache 是 timing channel：

- cache hit：快 (ns)
- cache miss：慢 (10-100 ns)

**攻擊者觀察 cache state** → 推 secret。

### FLUSH+RELOAD (Yarom-Falkner 2014)

```
Attacker:
  1. flush 某個 memory location (CLFLUSH)
  2. wait for victim to use it
  3. reload (read) → 看時間
     hit (快) → victim 用過
     miss (慢) → victim 沒用
```

對 AES T-table 實作：每個 round 從 T-table 讀某 byte。**T-table index 取決於 secret key**。FLUSH+RELOAD 推 cache 命中 pattern → 推 key。

### PRIME+PROBE

attacker 不需 shared memory：

```
1. PRIME: 把 cache filled with attacker data（佔據整個 cache set）
2. wait for victim to use cache
3. PROBE: 重 access attacker data，看時間
   慢 → victim 把 attacker data evict 了 → victim 用了那個 cache set
```

對 cross-VM / cloud 攻擊有效（attacker 與 victim 跑在同 physical CPU，shared L1/L2）。

## 修復：Const-time AES

避開 cache timing 兩種方式：

```
1. AES-NI (硬體):
   完全沒 lookup table，CPU 內部 const-time
   現代 CPU (Intel >= Westmere、ARMv8 + Crypto)
   
2. Bitsliced AES (軟體):
   把 8 個 block 並行算
   用 SIMD bit operation 取代 SBOX lookup
   const-time 但 throughput 較低

3. T-table + cache prefetch:
   把整 T-table preload 到 cache
   一旦 cache，後續 access 都 hit
   不完美（preempt 後 cache 可能被 evict）
```

**OpenSSL `vpaes` 是 SSSE3 const-time bitsliced AES**（vector permutation） — 沒 AES-NI 時的 fallback。

## Power Analysis

物理攻擊：**測 chip 的功耗**。

### Simple Power Analysis (SPA)

直接從 oscilloscope 看 power trace 識別操作：

```
RSA modpow 一條 trace：
  ___|‾|_|‾|_|‾‾‾|_|‾|_|‾‾‾|...
     0  1  0  1  1  0  1  0  1  1
     ↑                ← 每個 bit 的 multiplication 圖案不同
```

特別簡單 implementation 的 RSA / ECC 一條 trace 就破。

### Differential Power Analysis (DPA, Kocher 1999)

更狠：統計 thousands of traces，攻擊者用 **correlation analysis** 推 secret。即使 trace 看不出 pattern，統計上仍能推。

### EM (Electromagnetic) emanations

晶片的電流變化產生 EM 場。**遠端可測**（10 cm 內 simple antenna）。同 DPA 可分析。

**TEMPEST**（NSA 1960s 研究）：CRT 顯示器、鍵盤、HD 都漏 EM。

### 防禦：Masking、shuffling

```
Masking:
  把 secret s 拆成 s = s_1 XOR s_2 (random split)
  每個操作分開做兩個 share
  attacker 只看一個 share → 沒資訊
  
Shuffling:
  操作順序 random（如每 round 順序打亂）
  attacker 對齊 trace 困難
```

**hardware 級**：HW security module、TPM、smart card 大量用 masking。

## Spectre / Meltdown

2018 公布。**CPU speculative execution** 造成 cache state 洩漏。

```
if (x < array_size) {       // CPU speculatively 執行 branch
    y = array[x];           // even if branch will not actually be taken
    z = array2[y * 256];    // → array2[secret * 256] 進 cache
}
// ...
// CPU 後悔了，rollback architectural state
// 但 cache 已被影響！
// attacker 用 FLUSH+RELOAD 看 array2 哪個 element 在 cache
// → 推 secret y
```

對密碼學：

- 攻擊 user-space 密碼 library 的 in-memory secret
- 跨 VM / 跨 process 攻擊
- KASLR 繞過

修復（不完美）：

- KAISER / KPTI（kernel page table isolation）
- MSR LFENCE、SSBD、IBRS 等 microcode update
- Retpoline 取代 indirect branch
- 重要 secret 用「**masked**」表示（multiple share）

性能損失 5-30%，看 workload。

## 修復 Side-channel 的工程紀律

```
Const-time:
  ✓ 沒 secret-dependent branch
  ✓ 沒 secret-dependent memory access
  ✓ 沒 lookup table indexed by secret
  ✓ 用 bit operation 做 conditional select
  ✓ 用 CRYPTO_memcmp 比較 MAC

Blinding:
  ✓ RSA / ECC 用 blinding
  ✓ 簽章每次新 nonce

Masking (HW level):
  ✓ Smart card / HSM 內部用 masked S-box
  
Memory zeroization:
  ✓ Secret 用完 explicit_bzero（防 compiler 優化掉）
  ✓ Stack 上的 secret 也清

Compiler:
  ✓ 用 const-time 寫法時驗證 compiler 沒優化掉
  ✓ 用 dudect / ctgrind 工具測 timing
```

## 一個常見誤解

「我的 server 不在物理可控環境，side-channel 沒事」

**Cloud / virtualized 環境是 side-channel 的天堂**：

- co-located VM → cache attack
- shared CPU → speculative execution attack
- network timing 仍能用（Brumley 2003）
- 容器之間 cgroup 隔離不阻 side-channel

**Cloud crypto 必 const-time**。AWS、GCP 的 KMS 都跑在 HSM 而非普通 VM 就是這個理由。

## 自我檢核

- [ ] 我能解釋 Kocher 1996 timing attack 對 RSA modpow
- [ ] 我能寫 RSA blinding 防 timing
- [ ] 我能解釋 FLUSH+RELOAD 對 AES T-table 的攻擊
- [ ] 我能說出 SPA、DPA 的差別
- [ ] 我能列出 const-time 程式設計的至少 5 條紀律
- [ ] 我能解釋 Spectre 怎麼利用 speculative execution

下一章專門講 const-time programming 的工程細節。

→ [Ch 39 Constant-time programming](./39-constant-time.md)
