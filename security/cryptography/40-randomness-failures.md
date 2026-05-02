# Ch 40 — 隨機數失敗史

> 目標：複習並深入幾個經典 RNG 災難 — Debian OpenSSL CVE-2008-0166（兩年期、影響全球）、Sony PS3 ECDSA 用常數 nonce 被破、Dual_EC_DRBG NIST 後門故事（NSA 滲透標準）、Bitcoin 早期錢包重複 k 值。

Ch 28 提過這些故事的概念，本章深入細節與時間線。

## #1 Debian OpenSSL CVE-2008-0166

### 時間線

```
2006-05  Debian maintainer 收到 Valgrind 警告：OpenSSL 用 uninitialized memory
2006-05  Maintainer 提交 patch：註解掉看似有問題的 memcpy
         (那個 memcpy 是把 random pool 餵 entropy，刻意用 uninit memory)
2006-09  Debian etch 發布，含此 patch
2007     Ubuntu 7.04 / 7.10 也帶此 patch
2008-05  Luciano Bello 揭發
2008-05  CVE 公開，Debian / Ubuntu 全更新
```

### 技術細節

OpenSSL `RAND_add()` 接受 entropy input：

```c
// 簡化版 OpenSSL RNG
void RAND_add(const void *buf, int num, double entropy) {
    static unsigned char state[1024];
    // mix buf into state
    MD_Update(md, buf, num);   // ← Debian 註解掉的這行
    MD_Update(md, state, 1024);
    // ...
}
```

第一個 `MD_Update(md, buf, num)` 是把 buf（用戶提供的 entropy）加入 mix。Debian maintainer 看到 valgrind 警告（buf 可能是 uninit memory）→ 註解掉。

結果：**state 只 mix 自己（1024 byte 已知 init value）+ process ID（從別處進）**。

OpenSSL 又 seed PID + time 進 state，但 PID 範圍只 0-32767 → **整個 RNG 只 32768 種可能初始 state**。

### 影響

```
2006-2008 兩年內所有 Debian / Ubuntu 系統產生的：
  - SSH host key
  - SSH user key
  - SSL cert (server & client)
  - DSA simulation key
  - GPG key
  - OpenVPN key
全部只 32768 種可能。

attacker 預生表（每 PID 一個 SSH host key）→ 對任意 SSH server 試 32768 把 key
→ 直接登入

DSA 簽章影響更深：
  k nonce 也被同 RNG 影響 → nonce 重複 → 私鑰外洩
```

修補：

- 立刻 update OpenSSL package
- **撤銷 + 重新產**所有受影響 key（cert、SSH key、OpenVPN key 等）
- 整個 internet 的 SSH host key 大規模 rotation

教訓：

1. **不要 patch 你不懂的密碼學程式碼**
2. **OpenSSL 應該在 source 加大警告**「**這段看似 uninit 是故意**」
3. **distro maintainer 與 upstream 應 close coordination**
4. **獨立審查任何 modification 到 cryptographic library**

OpenSSL 後來 source 大改：對 RNG 加大量註解、加 #ifdef 防止錯改。**這是一個 distro 級錯誤而非 OpenSSL upstream 錯**。

## #2 Sony PS3 ECDSA (2010)

### 細節

Sony 對 PS3 firmware 用 ECDSA 簽章，每個 firmware 用 NDA 保護的私鑰簽。理論安全 — attacker 沒私鑰簽不了 firmware → 無法 mod / 跑 homebrew。

但 Sony 程式設計師偷懶 / 抄錯 / 不懂：**把 ECDSA nonce 寫成常數**：

```c
// ❌ Sony 的 PS3 ECDSA code（簡化）
const uint8_t k[32] = { 0x12, 0x34, 0x56, ... };  // 同樣 k 每次簽都用！

void sign(uint8_t *m, uint8_t *sig) {
    // 用 const k 算 r, s
    ...
}
```

回憶 Ch 23：

```
sig_1 = (r, s_1)  for m_1
sig_2 = (r, s_2)  for m_2

s_1 - s_2 = k^-1 (H(m_1) - H(m_2))
k = (H(m_1) - H(m_2)) / (s_1 - s_2)
d = (s_1 × k - H(m_1)) / r
```

只要兩個簽章 → 直接算出 d。

### 公開揭露

2010 年 12 月 27 日，27c3 conference (Berlin)。fail0verflow 團隊 demo：

- 從兩個 firmware 簽章算出 Sony 的私鑰
- 任意簽自己的 firmware
- PS3 完全越獄

Sony 反應：

- 起訴 GeoHot (George Hotz)，2011 年 1 月
- 起訴 fail0verflow
- 4 月 2011 庭外和解（Sony 撤訴 + GeoHot 同意不再 mod PS3）
- 但**私鑰已外洩**，整代 PS3 firmware 永遠無法收回

Anonymous 抗議 Sony 法律行動 → 2011 年 4 月 PSN 大規模被駭，7700 萬用戶資料外洩。**最終 Sony 被告與賠償 損失 estimated $171M**。

### 教訓

1. **ECDSA nonce 必須 unique**
2. **避免 randomness 失敗**，用 RFC 6979 deterministic nonce
3. **Code review 對密碼學 code**：const k 任何 reviewer 應發現
4. **法律行動不能修復技術洩漏**

## #3 Dual_EC_DRBG NSA 後門

### 背景

NIST 2007 SP 800-90A 標準化 4 個 CSPRNG：

```
Hash_DRBG    基於 hash function
HMAC_DRBG    基於 HMAC
CTR_DRBG     基於 block cipher CTR mode
Dual_EC_DRBG  基於 elliptic curve (Dual Elliptic Curve)
```

前三個直觀，**Dual_EC_DRBG 用 EC point** — 為什麼？沒人解釋。

```
state ∈ EC group
generate output:
  state' = state × P
  output = (state' × Q).x_coordinate truncated to k bytes
  state = state'

Q 是 NIST 給的特殊 EC point
```

### 後門揭露

Shumow / Ferguson 2007 (CRYPTO conference) 學術指出：

> **若 Q = e × P 對某 e（後門）**，知 e 的人能：
> 從 32 byte output → 推出當前 state → 預測未來所有 output

**用 32 byte output（一個 SSL session 的 ephemeral random）就能 fully predict 後續 RNG**。

NIST 沒解釋為什麼用這個 (P, Q)。學術社群懷疑 NSA 留 backdoor。

### 確認

2013 Snowden leak。NSA "Bullrun" project 文件：

- **NSA 主動 push Dual_EC_DRBG 進 NIST 標準**
- **NSA 給 NIST 那組 (P, Q)** — 顯然知道後門 e
- **付 RSA Security 1000 萬美元**讓 BSAFE library 預設用 Dual_EC_DRBG

RSA Security 否認知後門，但事實是 BSAFE 從 2004 預設這個 → 影響 enterprise customer 多年。

### NIST 反應

```
2013-09  NIST 重新審查
2014-04  NIST 移除 Dual_EC_DRBG from SP 800-90A
2015-01  Reuters 揭 RSA Security 收 1000 萬故事
```

### 教訓

1. **NIST 標準不是絕對信任** — NSA 滲透記錄
2. **公開、可驗證的 design rationale 必要**：用 nothing-up-my-sleeve number（如 sin(0), sin(1) 等顯然 random source 的常數）
3. **學術警告應認真對待** — 2007 paper 早就警告
4. **post-Snowden 改革**：IETF 對 NSA 影響更警惕，學術社群推 transparent design

現在 NIST 標準（如 ML-KEM、ML-DSA）design rationale 公開、社群審查。**Dual_EC_DRBG 故事永遠是密碼學界的傷疤**。

## #4 Bitcoin 早期錢包

```
2013      Android SecureRandom 在某些 device 有 bug
          多個 Bitcoin 錢包用 ECDSA + 重複 k
          ~ 55 BTC 被盜（當時 $5000，今 > $5M）
          
2013-08    Bitcoin Improvement Proposal: deterministic ECDSA
          libsecp256k1 改用 RFC 6979
```

修補後 Bitcoin 簽章 deterministic，nonce reuse 不可能再發生。**Bitcoin 用戶多到任何錢包 bug 都是大事** — 推動 deterministic ECDSA 主流化的最大力量之一。

## 共同模式

所有 RNG 災難共同點：

1. **「看起來沒問題」的小 patch / 偷懶 / 後門**
2. **影響範圍極廣**（Debian: 全 distro；NSA: 全 BSAFE）
3. **發現後修補的時間遠超出洩漏期**（Debian 2 年；Dual_EC 6 年）
4. **損害不可逆**（key 已洩漏）

## 防禦

寫 production 系統：

```
1. 用 OS 提供 CSPRNG（getrandom() / BCryptGenRandom）
2. **不要** 自己 implement RNG
3. 不要在 RNG 內部加未經 review 的 patch
4. 用 deterministic 簽章（RFC 6979 / EdDSA）避開 nonce 風險
5. 監控你的 systems：定期測 RNG 輸出 entropy（如 NIST SP 800-90B）
6. 對不同 OS 行為熟（embedded / VM 早期 boot 可能 entropy 不夠）
```

## 一個常見誤解

「現代 OS 的 /dev/urandom 已經夠好，不會有 RNG 失敗了」

**錯**。**OS 對了，但用法可能錯**：

- fork 後沒 reset state（之前 RNG state 雙親共用）
- VM clone：snapshot 包含 RNG state，多 VM 同 state
- early boot：embedded device 開機時 entropy 不夠
- 容器環境：某些容器運行時無法 access /dev/urandom 直接，預設 fallback

實務上 RNG 災難仍偶爾發生（雖比 2008 年少）。**永遠假設你的 RNG 可能出包**，並測試。

## 自我檢核

- [ ] 我能描述 Debian OpenSSL CVE 的具體 patch 與後果
- [ ] 我能解釋 Sony PS3 ECDSA 用 const nonce 怎麼破
- [ ] 我能說出 Dual_EC_DRBG 後門的數學原理
- [ ] 我能列出 RNG 災難的共同 pattern
- [ ] 我能寫安全的 RNG 用法 cheat sheet
- [ ] 我知道 fork / VM clone / early boot 的 RNG 注意事項

下一章看密碼分析方法。

→ [Ch 41 密碼分析方法](./41-cryptanalysis.md)
