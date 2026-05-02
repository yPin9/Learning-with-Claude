# Ch 41 — 密碼分析方法：differential、linear、algebraic、MITM

> 目標：搞懂密碼學者怎麼攻擊密碼。differential cryptanalysis（Biham-Shamir 1990，DES 設計者其實預先防了）、linear cryptanalysis（Matsui 1993）、algebraic attack、meet-in-the-middle（為什麼 2DES 沒比 DES 強）。

## Cryptanalysis 概覽

```
Cryptanalysis = 分析密碼演算法找弱點

技術光譜：
  Brute force → 純試所有 key
  Algebraic → 用代數方程式
  Statistical → 用 distribution 偏差
  Differential → 看 input/output 差分 pattern
  Linear → 找 linear approximation
  Meet-in-the-middle → 兩端各算一半
  Side-channel → 看實作洩漏
  Quantum → 用量子算法
```

學術 cryptanalyst 的目標不一定是 "production break"，而是 **找比 brute force 快的攻擊**。即使「2¹²⁰ 比 brute force 2¹²⁸ 快 256 倍」也是 paper 級成就。

## Differential Cryptanalysis

Biham-Shamir 1990 公開（雖然 NSA / IBM 1974 已內部知）。

### 概念

對 round function F：

```
觀察 input difference (XOR) 與 output difference 的關聯：
  ΔX = X_1 XOR X_2
  ΔY = F(X_1) XOR F(X_2)

對「good cipher」每個 ΔY 應 uniformly 分布
對「bad cipher」某些 ΔX → ΔY 機率高（characteristic）
```

如果找到一條高機率的 differential `ΔX → ΔY`（如機率 2⁻¹⁰），就有：

```
1. 收集 ~2¹⁰ pair (X_1, X_2) with X_1 XOR X_2 = ΔX
2. 加密看 ciphertext
3. 統計上會看到 ΔY pattern
4. 用這個推 last round key
```

### DES 對 differential 的抵抗

Biham-Shamir 證明：

```
完整 DES (16 round):
  最佳 differential 機率 ~ 2^-47.2
  需要 ~2^47 已知 plaintext 才能 attack
  仍比 brute force (2^55) 快但不實用
  
8-round DES 變體可被 attack
```

回憶 Ch 8：**NSA 改 S-box 的真實理由**就是 differential resistance。IBM 1974 內部已知，被 NSA 要求保密，1990 才公開。

### AES 對 differential

AES 設計者（Daemen / Rijmen）公開預先考慮 differential：

```
AES 4 round 後，differential trail 必跨足 25 個 active S-box
每個 S-box 最大 differential probability = 4/256 = 2^-6
4 round 整體 ≤ 2^(-6 × 25) = 2^-150

10 round AES = 兩個 4-round trail concat
即使 differential 機率最大 = 2^-150
攻擊 plaintext 需求超過 2^128 → impossible
```

**AES 對 differential immune**。Ch 9 的 MixColumns branch number 就是這個證明的核心。

## Linear Cryptanalysis

Matsui 1993 公開。針對 DES。

### 概念

```
找 plaintext bit、ciphertext bit、key bit 的 linear approximation：
  P[i_1] XOR P[i_2] XOR ... XOR C[j_1] XOR ... XOR K[k_1] = 0  with bias ε
  (bias = Pr - 1/2)

對 random bias = 0
對 cipher bad 的 bias > 0
```

如果找到 bias = 2⁻⁸ 的 approximation，N = (1 / bias²) ≈ 2¹⁶ 個 plaintext 能 distinguish from random + 推 key bit。

### DES Matsui 1993

完整 16-round DES：bias = 2⁻²⁴ → 需 2⁴² known plaintext。

實戰：1994 Matsui 用 50 工作站 50 天破完整 DES。**理論到實戰的精彩案例**。

### AES 對 linear

類似 differential 分析：AES 4 round 後 25 個 active S-box。每 S-box 最大 linear bias 2⁻⁴。AES 對 linear 也 immune。

## Algebraic Attacks

把 cipher 寫成 polynomial system，用代數技術解。

```
AES 可寫成 GF(2) 上 ~ 8000 個 quadratic equation 的系統
求解這個系統 = break AES
```

主要技術：

- **Gröbner basis**：F4, F5 algorithm
- **XL/XSL attack** (Courtois 2002)：claimed AES 弱於 brute force，但實際 不 work
- **interpolation attack**：對某些 cipher 有效

對 AES：**沒人成功用 algebraic attack 接近 break**。XSL 的 claimed complexity 後來被反駁。

對其他 cipher：HFE（hidden field equation）類 cryptosystem 多被 algebraic attack 殺。

## Meet-in-the-Middle (MITM)

對 multi-step encryption 的標準攻擊。最有名 example：**2DES**。

```
2DES: C = AES_K2(AES_K1(P))
      key 等效 56+56 = 112 bit
      brute force 應 2^112

但 MITM:
  for K1 in 0..2^56:
    A = AES_K1(P)        ← 加密
    store (A, K1) in table
  
  for K2 in 0..2^56:
    B = AES_K2^-1(C)      ← 解密
    if B in table:
      return (K1, K2)
  
  total operations: 2^57 + 2^57 = 2^58
  total memory: 2^56 × (16+8) byte = 1 EB
```

時間 2⁵⁸ 比 2¹¹² 快超多。**2DES 等於 single-DES 的 2² 倍工作**，不是 2⁵⁶ 倍。**所以才設計 3DES**。

對 3DES：MITM 攻擊複雜度 2¹¹² 工作 + 2⁵⁶ memory。仍可行 but expensive。

## Algebraic Attacks on Block Ciphers (XSL Saga)

2002 Courtois-Pieprzyk paper claim：用 XSL（eXtended Sparse Linearization）能 break AES with complexity 2¹⁰⁰。

整個密碼學社群**興奮 + 懷疑**。後續研究：

- 估計可能被低估（complexity 算法錯）
- 後續 paper 反駁 XSL 對 AES 不 work
- 最終共識：**AES 沒被 algebraic attack 破**

但 XSL 故事提醒：**algebraic attack 是可能威脅**。設計新 cipher 必考慮。

## Side-Channel Already Covered

Ch 38 已詳述。**Side-channel 是 production 系統的最大威脅**。比理論 cryptanalysis 更實用。

## Quantum Cryptanalysis

Ch 29 已介紹 Shor / Grover。

```
Shor:    factoring + discrete log → polynomial
Grover:  brute force → square-root
```

對 AES-256：Grover 把搜尋空間從 2²⁵⁶ 降到 2¹²⁸。**仍極昂貴**。

對 RSA-2048：Shor 多項式時間破。

## Side-Channel 與 Cryptanalysis 對比

```
                Cryptanalysis          Side-channel
Target          演算法數學             具體實作
Phase           設計時抗 cryptanalysis  實作時抗 side-channel
影響            演算法設計重做           換一種 implementation
時間            幾年研究找一個 attack    幾天 write 一個 attack
受眾            學術                   工程
```

**現代密碼學需要兩者都做**：

- 設計時假設「最壞 attack 不會比 X 強」（cryptanalysis 安全）
- 實作時保證「沒 timing/cache/power leak」（side-channel 安全）

## 一個常見誤解

「AES 已經被 break / 即將被 break」

**沒有**。每隔幾年有 paper claim「最佳 attack on AES」，但都是 marginal improvement：

- Bogdanov-Khovratovich-Rechberger 2011: AES-128 with 2^126.0 (slight better than 2^128)
- Various biclique attacks: 2^126.x

**所有都比 brute force 快不到 2 倍**。實戰意義：零。

AES 的安全 margin 仍極大。**未來幾十年 unlikely break**（除 quantum，但 AES-256 對 quantum 仍夠）。

## 自我檢核

- [ ] 我能解釋 differential cryptanalysis 的核心 idea
- [ ] 我能說出 NSA 改 DES S-box 的真實理由
- [ ] 我能解釋 AES branch number 為什麼 immune to differential / linear
- [ ] 我能寫 MITM attack against 2DES 的步驟
- [ ] 我能說出 XSL attack 的 saga
- [ ] 我能比較 cryptanalysis 與 side-channel 的差別

下一章為這門課收尾：寫 crypto code 的 do/don't、職涯方向。

→ [Ch 42 收尾](./42-reflections.md)
