# Ch 33 — SLH-DSA / SPHINCS+：hash-based 簽章

> 目標：搞懂 hash-based 簽章為什麼適合 long-term assurance — 唯一安全假設是「hash function 抗碰撞」，沒 lattice 的數論假設。Merkle tree、WOTS+、HORST、為什麼 SLH-DSA 簽章那麼大但仍被 NIST 列為標準。

## SLH-DSA 是什麼

**SLH = StateLess Hash-based**。前身 SPHINCS+。

NIST FIPS 205（2024-08）。**唯一非 lattice 的標準 PQC 簽章**。

## 為什麼 NIST 要 hash-based

```
Lattice 假設：
  Module-LWE 難
  ← 30+ 年 cryptanalysis 沒突破
  ← 但仍是「相對年輕」的數學
  
Hash 假設：
  hash function 抗碰撞 / 原像
  ← 60+ 年 hash function 研究
  ← SHA-2, SHA-3 經過大量分析
  ← 「最保守」的安全假設
```

**hash 安全等於 hash 安全**。不需要其他 number theoretic 假設。**lattice 萬一突破，hash-based 仍安全**。

代價：**簽章極大**（7-50 KB），sign / verify 極慢。

## Lamport one-time signature：基礎

1979 Leslie Lamport 提的最簡單 hash-based 簽章。**簽一個 bit 用兩把 256-bit secret + 公開 hash**：

```
KeyGen:
  for i in 0..n-1:    (n = message bit length)
    x_{i,0} ← random 256-bit
    x_{i,1} ← random 256-bit
    y_{i,0} = H(x_{i,0})
    y_{i,1} = H(x_{i,1})
  sk = {x_{i,b}}
  pk = {y_{i,b}}

Sign(m):
  for i in 0..n-1:
    σ_i = x_{i, m_i}      ← reveal x for bit m_i
  return σ

Verify(pk, m, σ):
  for i in 0..n-1:
    if H(σ_i) != y_{i, m_i}: return False
  return True
```

**security**：對 m_i = 0，attacker 看到 x_{i,0}，但 y_{i,1} 對應的 x_{i,1} 仍 secret（hash preimage 困難）。**簽章後 attacker 不能 forge 不同 m**。

**致命限制**：**one-time** — 同 sk 簽兩個不同 message → reveal 兩個 message 對應的 secret，attacker 能組合偽造任意 message。

## Winternitz OTS (W-OTS) 改良

Lamport 簽章對 256-bit message 要 16 KB sk 與 pk。Winternitz：**用 hash chain 縮短**。

```
參數 w（如 16）

KeyGen:
  for i in 0..len-1:
    x_i ← random
    y_i = H^w(x_i)   ← hash w 次
  
Sign(m):
  for i in 0..len-1:
    digit_i = m's i-th base-w digit
    σ_i = H^(w - digit_i - 1)(x_i)   ← hash 一定次數
  + checksum digits

Verify:
  for i:
    H^(digit_i + 1)(σ_i) ?= y_i
```

space-time trade-off：增大 w，sig 變短但 verify 慢。WOTS+ (改良版) 是 SPHINCS+ 內建。

## Merkle Tree：scaling

OTS 只能簽一次。要多次必須**累積很多 OTS keys**，每個 message 用一把。pk 變很大。

**Merkle tree** 解：

```
y_0  y_1  y_2  y_3  y_4  y_5  y_6  y_7    ← 8 個 OTS pk
 │   │    │   │    │   │    │   │
 └─h─┘    └─h─┘    └─h─┘    └─h─┘         ← 兩兩 hash
   │        │        │        │
   └───h────┘        └───h────┘
        │                 │
        └────────h────────┘
              │
            root            ← 只 publish root 當 master pk
```

簽章 = OTS_signature + Merkle path（兄弟 hash 序列） + leaf index。

verify：用 path 重建 root，比對。**N 個 message 的 sig size 從 O(N) 縮到 O(log N)**。

但仍 stateful：要記哪個 leaf 用過。

## HORST：解 stateful 問題

SPHINCS 用 **HORST (HORS Tree)** 替 OTS：randomly 從 large pool 選 secret reveal，避開「同 leaf 用兩次」的需求。

具體：

- pool 有 t 個 secret
- sign 一個 m → 從 m hash 出 k 個 indices，reveal 對應 secret
- 不同 m 的 indices 大概率不重複 → **stateless OK**

代價：sig 大（多個 secret reveal）。

## SPHINCS+ 完整結構：hypertree

把 Merkle tree 串接 — **forest of trees**：

```
            top tree (FORS)
                │
        ┌───────┼───────┐
        │       │       │
       sub-tree sub-tree sub-tree
        │
   ┌────┼────┐
   │    │    │
  ...  ...  ...
   │
  HORS leaves (final)
```

每層 tree 簽下一層 tree 的 root。**12-22 層深**（看參數）。

簽章 = HORS sig + 路徑上每一層的 OTS sig + Merkle paths。**累積很大**：7 KB（小）到 50 KB（大）。

## SLH-DSA 變體

```
                         Sig size    Sign time  Verify time
SLH-DSA-128s             7 KB        慢          快  (small/slow sign)
SLH-DSA-128f             17 KB       快          慢  (fast sign)
SLH-DSA-192s/f           16/35 KB    
SLH-DSA-256s/f           29/50 KB    
```

`s` = small signature, slow sign
`f` = fast sign, larger signature

**選擇**：sign 多用 s，verify 多用 f？ 反過來：Web TLS server 一次 sign N 次 verify → 用 s（sig size 重要）。

實務上多選 SLH-DSA-128s（7 KB sig，可接受）。

## 性能

```
Operation         SLH-DSA-128s    SLH-DSA-128f    對比 Ed25519
KeyGen            ~3 ms           ~3 ms           ~50 µs
Sign              ~600 ms (慢!)   ~30 ms          ~50 µs
Verify            ~700 µs         ~2 ms           ~150 µs
```

Sign 對 SLH-DSA-128s **慢 1000+ 倍**。**只用於不頻繁簽的場景**：

- CA root key 簽下級
- Code signing（一個 release 簽一次）
- Long-term archive

不適合 TLS handshake（每 connection 一次）— 太慢。

## 為什麼 NIST 仍標準化

雖然慢且大，**hash-based 是最保守的 PQC**。NIST 的 portfolio：

```
ML-DSA (lattice):  最快，預設選擇
SLH-DSA (hash):     最保守，long-term assurance
FN-DSA (FALCON):    平衡（小 sig），但實作難
```

不同場景不同選擇。**SPHINCS+ 抗 lattice break 風險** — 如果某天 lattice 被 sub-exponential 算法破，hash-based 仍站得住。

## 真實採用

```
2022  AWS KMS 開始試
2024  NIST 正式標準後普及加速
2024  Linux kernel 部分子系統用於 firmware 簽
2025+ CA 與 code signing 漸入
```

不會取代 ML-DSA 為主流 — 速度差太多。**作為 backup / hybrid 補強**。

## 一個常見誤解

「SPHINCS+ 簽章那麼大，沒用吧」

**對 long-term, infrequent signing 它是 sweet spot**。

例：
- 公司簽的 software 5 年用：sign 一次 50 ms，verify 每客戶 1 ms — 全可接受
- CA root 簽下級：3 個月一次，sign 1 秒沒人在意
- 政府機密文件存檔：10 GB 文件配 50 KB 簽章，比例極小

對 high-volume（TLS handshake）才不適合。**選對工具**。

## 一個常見誤解 #2

「SLH-DSA 比 ML-DSA 安全」

**安全度等同**。差別在**安全假設不同**：

- ML-DSA：依賴 Module-LWE 難
- SLH-DSA：依賴 hash 抗碰撞 / 原像

**雙方都過 NIST PQC 審查**。SLH-DSA 是「**保險 backup**」，**信心更高 in absolute terms**（hash 研究更久），但**不代表 ML-DSA 不安全**。

## 自我檢核

- [ ] 我能寫 Lamport one-time signature
- [ ] 我能解釋 Winternitz 怎麼用 hash chain 縮短 sig
- [ ] 我能畫 Merkle tree 的 N → log N 簽章節省
- [ ] 我能解釋 SLH-DSA 為什麼 stateless（HORST trick）
- [ ] 我能說出 SLH-DSA-128s vs 128f 的 trade-off
- [ ] 我能說出 SLH-DSA 適合的場景

到這裡 Part 7 章節結束。下一個是練習 D — 實作簡化 Kyber-512 KEM。

→ [練習 D：簡化版 Kyber-512](./practice-d-kyber-512.md)
