# Ch 7 — 區塊密碼基礎：Feistel vs SPN、IND-CPA

> 目標：搞懂區塊密碼兩大架構（Feistel network 與 Substitution-Permutation Network）的差異與取捨，並把 IND-CPA 從上一章的直覺升級到嚴格定義。為 Ch 8 DES 與 Ch 9-10 AES 鋪底。

## 區塊密碼是什麼

```
n-bit plaintext block ──┐
                        ├──► Block Cipher (key k) ──► n-bit ciphertext block
n-bit key ──────────────┘
```

把固定長度（block size）的 plaintext 變成同長 ciphertext，**用 key 控制行為**。常見 block size：

- **DES**：64-bit
- **AES**：128-bit（256-bit 是 key size，block 還是 128）
- **ChaCha20**：嚴格說不算 block cipher，是 stream

block cipher 是**抽象建構積木**。實際傳訊息用 mode（CBC / CTR / GCM ...）把多個 block 串起來，Ch 11 / Ch 25-27 詳述。

## 兩個關鍵性質：confusion 與 diffusion

Shannon 1949 提的設計原則，後來成所有 block cipher 通則：

```
Confusion（混淆）
  Ciphertext 與 key 的關係極複雜，無法簡單推回 key
  → 用非線性元件達成（S-box、modular addition）

Diffusion（擴散）
  Plaintext 一個 bit 變動 → ciphertext 多個 bit 變
  → 用線性混合達成（permutation、矩陣乘）
```

**所有現代 block cipher 都是 confusion + diffusion 的組合**。差別在用哪些元件、迭代多少輪。

## Feistel Network

1973 年 IBM Horst Feistel 提出。**核心 idea**：把 block 切兩半，用一個 round function 反覆混：

```
              plaintext block (2n bit)
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
       L₀ (n bit)           R₀ (n bit)
          │                     │
          │  ┌──── F(R₀, k₁) ◄──┤   F = round function
          │  │                  │
          ▼  ▼                  │
         XOR                    │
          │                     │
          ▼                     ▼
       L₁ = R₀                R₁ = L₀ ⊕ F(R₀, k₁)
          │                     │
                ... 多輪 ...
          │                     │
       L_r                    R_r
          │                     │
          └──────────┬──────────┘
                     ▼
              ciphertext block
```

每一輪：

```
L_{i+1} = R_i
R_{i+1} = L_i XOR F(R_i, k_{i+1})
```

**奇妙之處**：解密用 same algorithm，只要倒著用 round key（k_r, k_{r-1}, ..., k_1）。從輸出反推：

```
L_{i+1} = R_i           → R_i = L_{i+1}
R_{i+1} = L_i XOR F(R_i, k) → L_i = R_{i+1} XOR F(L_{i+1}, k)
```

- enc 與 dec 共用同一電路（**省硬體成本**）
- F 不需要可逆 — 只要是函數就行（**設計自由度大**）

代表 cipher：**DES、3DES、Blowfish、CAST5、Camellia**。

## Substitution-Permutation Network (SPN)

另一條路：**整個 block 同時做 confusion + diffusion**：

```
plaintext block (n bit)
        │
        ▼
   AddRoundKey (XOR key)
        │
        ▼
   ┌───────────────────┐
   │ S-box S-box ... │  ← Substitution（confusion）
   └───────────────────┘
        │
        ▼
   ┌───────────────────┐
   │ Permutation       │  ← Diffusion
   └───────────────────┘
        │
        ▼
   AddRoundKey
        │
        ... 多輪 ...
        │
        ▼
   ciphertext block
```

代表 cipher：**AES、Serpent、PRESENT**。

每輪：

```
state = state XOR round_key
state = SubBytes(state)        # 每個 byte 經 S-box
state = Permute(state)         # 全 block 重排或矩陣混合
```

**特性**：

- enc 與 dec **不同**（解密要用 inverse S-box / inverse permutation）
- F（這裡是整個輪）必須**可逆**
- 每個 byte / 位元都被處理 — 比 Feistel **更強的 diffusion per round**
- **AES 4 輪後達 full diffusion**；Feistel 通常 8+ 輪

## Feistel vs SPN 對照

| | Feistel | SPN |
|---|---|---|
| 輪數需求 | 多（DES 16、3DES 48） | 少（AES 10/12/14） |
| 硬體 | enc/dec 共用電路 | 兩套電路 |
| 軟體 | 簡單，64-bit lane | 高效，128-bit SIMD |
| 設計自由 | F 不需可逆 | S-box / permutation 必須可逆 |
| Diffusion 速度 | 慢（一半 bit 一輪） | 快（整 block 一輪） |
| 代表 | DES, Blowfish | AES, Serpent |

**現代主流是 SPN**（AES 贏了 1997-2001 NIST 競賽）。Feistel 仍存在於遺留系統與某些 key schedule。

## 輪數與 key schedule

```
master key (128/192/256 bit)
        │
        ▼
   Key Schedule (KS algorithm)
        │
        ▼
round_key_1, round_key_2, ..., round_key_r
```

key schedule 把 master key 擴展成多個 round key。要求：

1. **每個 round key 看起來無關**（防 related-key attack）
2. **快**（不能比 cipher 本身慢）
3. **可逆**（解密要算 round key）

AES 的 key schedule 是核心設計之一，下章 Ch 9-10 細看。

## IND-CPA 嚴格定義（升級版）

Ch 3 給了直覺，這裡給數學形式：

```
IND-CPA 安全 game：

1. challenger 隨機產生 key k ← KeyGen()
2. attacker A 給定 oracle access：A 提任意 m，得到 Enc(k, m)
3. A 選兩個等長 m₀, m₁ 給 challenger
4. challenger 擲銅板 b ∈ {0,1}，回 c* = Enc(k, m_b)
5. A 繼續用 oracle（除了不能查 c* 對應的 m₀ 或 m₁）
6. A 輸出猜測 b'
7. A 贏 ↔ b' = b

Encryption scheme is IND-CPA-secure iff
  for all polynomial-time A:
    | Pr[A wins] - 1/2 | ≤ negligible
```

直覺：**就算 A 能任意加密 plaintext 看密文（oracle），也無法區分**兩個她選的 plaintext 的密文。

## 確定性 vs 隨機性加密

block cipher 本身是 **確定性 PRP**：相同 key + 相同 plaintext → 永遠相同 ciphertext。

問題：**確定性加密絕對不滿足 IND-CPA**：

```
A 選 m₀ = "00...0", m₁ = "11...1"
challenger 回 c*
A 用 oracle 查 Enc(k, m₀) 與 Enc(k, m₁)
看 c* 等於哪個 → 100% 知道 b
```

**所以 ECB mode（直接用 block cipher 加密每個 block）不滿足 IND-CPA**。Ch 11 會展開 — 經典 ECB penguin 圖就是這個 attack 的視覺化。

要 IND-CPA 必須：

1. 用 **隨機 IV / nonce**（CBC、CTR）
2. 同 plaintext 加密兩次得不同 ciphertext

## IND-CPA → IND-CCA 升級

實際攻擊更狠：**A 可解密**（除了 challenge ciphertext 本身）。

對應現實：

- **padding oracle**：server 回不同 error 給「padding 對 / 不對」 — 等於部分解密 oracle
- **Bleichenbacher 1998**：RSA PKCS#1 v1.5 對應的 oracle attack
- **CCA2**：A 看到 c* 後仍能繼續查解密 oracle（adaptive）— 最強模型

**現代密碼學要求 IND-CCA2**。純 block cipher + random IV 仍只是 IND-CPA — 必須加 MAC（authenticated encryption）才達到 IND-CCA。Ch 25 詳述。

## 一個對照：CTR 與 CBC 的 IND-CPA 差別

```python
# CTR mode 的「加密」概念
def ctr_encrypt(key, nonce, plaintext):
    out = b""
    for i, block in enumerate(blocks(plaintext)):
        keystream = AES_enc(key, nonce || i)
        out += xor(block, keystream)
    return nonce || out
```

CTR 的安全性歸結到：**AES_enc(k, nonce || i) 是 PRF** → 多 block 的 keystream 是獨立隨機 → IND-CPA。

**CTR 非常清晰**。CBC 也 IND-CPA 但證明複雜（需要 IV 真隨機 + 不能 chosen-IV），這就是為什麼現代偏 CTR / GCM。

## 安全歸約：「破我等同破 PRP」

block cipher 安全證明的標準形式：

```
假設 AES 是 secure PRP（沒人破得了）
則 AES-CTR 是 IND-CPA-secure
證明：對任何 IND-CPA attacker A，可構造 PRP distinguisher D
      D 模擬 A 的環境，A 贏 → D 區分 PRP / random function 成功
      若 A 贏機率 > 1/2 + ε → AES 不是 PRP，矛盾
```

整個現代密碼學都是這樣 reduce：**底層原語安全（AES、SHA、橢圓曲線假設）→ 上層協定安全**。看 paper 看到 "we reduce to..."、"under the assumption that..." 就是這個。

## 一個常見誤解

「block cipher 比 stream cipher 安全」

**沒這回事**。安全性看設計，不看類別。

- **AES（block）**：強
- **3DES（block）**：弱（64-bit block 太短，2³² 個 block 後 birthday attack）
- **ChaCha20（stream）**：強
- **RC4（stream）**：已死

**但 block cipher 通常更通用**：可以用於 MAC、KDF、PRF 各種構造（CMAC、HKDF、AES-GCM）。stream cipher 構造範圍小（多用於 stream encryption）。

## 自我檢核

- [ ] 我能畫出 Feistel 一輪的訊號流
- [ ] 我能畫出 SPN 一輪的訊號流
- [ ] 我能比較 Feistel 與 SPN 的優缺
- [ ] 我能說出 Shannon 的 confusion / diffusion
- [ ] 我能寫出 IND-CPA game 的 6 個步驟
- [ ] 我能解釋為什麼 ECB 不滿足 IND-CPA

下一章看第一個現代 block cipher — DES。它的設計、NSA 改 S-box 的故事、為什麼要被 AES 取代。

→ [Ch 8 DES / 3DES](./08-des-3des.md)
