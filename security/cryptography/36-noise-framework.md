# Ch 36 — Noise Framework：可組合的 Handshake 積木

> 目標：能解釋 Noise 的 handshake pattern 系統，用 token 語言描述握手流程，區分 IK/XX/NK pattern 的適用場景，理解 WireGuard 為什麼選 Noise_IKpsk2。

---

## 為什麼需要 Noise Framework

回顧前兩章：

- **TLS 1.3**：一個完整的 transport protocol，內建 certificate、cipher negotiation、session resumption、0-RTT。功能齊全但也龐大——RFC 8446 有 160 頁
- **Signal Protocol**：專為即時通訊設計的 E2EE 方案，X3DH + Double Ratchet 組合。精巧但不通用——你不會用 Signal Protocol 來保護 VPN tunnel

每次設計新的加密 protocol 都要回答同一堆問題：誰先說話？哪些 key 是預先知道的？握手幾輪？安全屬性有哪些？然後找密碼學家做安全分析。

**Noise Framework**（Trevor Perrin, 2018）把這個過程系統化：

1. 定義一套 **token 語言**，用 `e`、`s`、`ee`、`es`、`se`、`ss` 描述握手中的每一步
2. 提供一組 **pre-defined handshake patterns**（IK、XX、NK 等），每個 pattern 的安全屬性都已經被分析過
3. 你選一個 pattern + 一組密碼學原語（DH function、cipher、hash），就得到一個完整的 handshake protocol——不需要從頭設計，不需要重新做安全分析

WireGuard VPN 用 `Noise_IKpsk2`，Lightning Network 用 `Noise_XK`，I2P 用 Noise-based protocol。

---

## 先建立直覺

把 Noise 想成一門「握手描述語言」。你用幾個 token 就能寫出一個完整的握手：

```
Noise_IK:
  <- s                     ← Responder 的 static key 預先已知
  ...
  -> e, es, s, ss          ← Initiator 的第一輪
  <- e, ee, se             ← Responder 的回應
```

每個 token 代表一個操作：

| Token | 操作 |
|-------|------|
| `e` | 生成 ephemeral key pair，把公鑰傳給對方 |
| `s` | 把 static key 的公鑰傳給對方（可能加密） |
| `ee` | DH(initiator_ephemeral, responder_ephemeral) |
| `es` | DH(initiator_ephemeral, responder_static) |
| `se` | DH(initiator_static, responder_ephemeral) |
| `ss` | DH(initiator_static, responder_static) |

`->` 表示 initiator 傳給 responder，`<-` 表示反向。`...` 上面的行是「pre-message pattern」——表示握手開始前雙方已經知道的 key。

---

## 核心概念：三種常見 Pattern

### Pattern 1：NK（Anonymous Initiator, Known Responder）

```
Noise_NK:
  <- s                      ← Responder 的 static key 預先已知
  ...
  -> e, es                  ← Initiator 發 ephemeral key，做 DH(e_I, s_R)
  <- e, ee                  ← Responder 發 ephemeral key，做 DH(e_I, e_R)
```

**場景**：Client 知道 server 的 public key（hard-coded 或透過其他管道取得），但 server 不需要知道 client 是誰。類似「匿名瀏覽已知網站」。

安全屬性：
- Client → Server：加密 ✓，server 驗證 ✗（server 不知道 client 是誰）
- Server → Client：加密 ✓，client 驗證 ✓（client 知道 server 的 static key）
- Forward secrecy：有（ephemeral key 用完即丟）

### Pattern 2：IK（Immediate, Known）

```
Noise_IK:
  <- s                      ← Responder 的 static key 預先已知
  ...
  -> e, es, s, ss           ← Initiator 發 e + s（加密），做 DH(e_I, s_R) + DH(s_I, s_R)
  <- e, ee, se              ← Responder 發 e，做 DH(e_I, e_R) + DH(s_I, e_R)
```

**場景**：雙方預先知道對方的 static key。Initiator 在第一輪就附上自己的 static key（加密過的），**一輪就完成身份驗證**。WireGuard 用的就是 IK（加上 PSK）。

安全屬性：
- 雙方都能在握手完成後驗證對方身份
- Forward secrecy：有
- 但 initiator 的 static key 在第一輪就傳了——如果 responder 的 static key 被 compromise，攻擊者能得知 initiator 的身份

### Pattern 3：XX（Mutual Authentication, Dynamic）

```
Noise_XX:
  -> e                      ← Initiator 發 ephemeral key
  <- e, ee, s, es           ← Responder 發 e + s（加密），做 DH(e_I, e_R) + DH(e_I, s_R)
  -> s, se                  ← Initiator 發 s（加密），做 DH(s_I, e_R)
```

**場景**：雙方在握手前不知道對方的 static key。三輪握手，互相交換 static key（都在加密後傳）。最通用的 pattern——**不需要任何預先知識**。

安全屬性：
- 雙方都驗證對方身份
- Forward secrecy：有
- Static key 都在加密後傳送（第一輪之後），比 IK 更好地保護身份隱私

### 範例一：用 Python noiseprotocol 建 XX Handshake

```python
"""
noise_xx_demo.py — Noise XX pattern handshake
pip install noiseprotocol
"""
from noise.connection import NoiseConnection, Keypair
import os

# ═══════════════════════════════════════════
# 雙方生成 static key pair
# ═══════════════════════════════════════════
initiator_static = os.urandom(32)  # X25519 private key
responder_static = os.urandom(32)

# ═══════════════════════════════════════════
# Initiator 設定
# ═══════════════════════════════════════════
initiator = NoiseConnection.from_name(b'Noise_XX_25519_ChaChaPoly_SHA256')
initiator.set_as_initiator()
initiator.set_keypair_from_private_bytes(Keypair.STATIC, initiator_static)
initiator.start_handshake()

# ═══════════════════════════════════════════
# Responder 設定
# ═══════════════════════════════════════════
responder = NoiseConnection.from_name(b'Noise_XX_25519_ChaChaPoly_SHA256')
responder.set_as_responder()
responder.set_keypair_from_private_bytes(Keypair.STATIC, responder_static)
responder.start_handshake()

# ═══════════════════════════════════════════
# 三輪握手
# ═══════════════════════════════════════════
print("=== XX Handshake ===")

# Round 1: Initiator -> Responder (-> e)
msg1 = initiator.write_message()
print(f"Round 1 (I->R): {len(msg1)} bytes")
responder.read_message(msg1)

# Round 2: Responder -> Initiator (<- e, ee, s, es)
msg2 = responder.write_message()
print(f"Round 2 (R->I): {len(msg2)} bytes")
initiator.read_message(msg2)

# Round 3: Initiator -> Responder (-> s, se)
msg3 = initiator.write_message()
print(f"Round 3 (I->R): {len(msg3)} bytes")
responder.read_message(msg3)

print(f"\nHandshake complete: {initiator.handshake_finished}")

# ═══════════════════════════════════════════
# 握手完成，用 transport keys 加解密
# ═══════════════════════════════════════════
plaintext = b"Hello from initiator via Noise XX!"
ciphertext = initiator.encrypt(plaintext)
decrypted = responder.decrypt(ciphertext)
print(f"\nPlaintext:  {plaintext}")
print(f"Ciphertext: {ciphertext.hex()[:48]}... ({len(ciphertext)} bytes)")
print(f"Decrypted:  {decrypted}")
assert decrypted == plaintext
print("\n✓ 加密通訊成功")

# 反方向
reply = b"Hello from responder!"
ciphertext2 = responder.encrypt(reply)
decrypted2 = initiator.decrypt(ciphertext2)
assert decrypted2 == reply
print("✓ 雙向通訊成功")
```

---

## 底層機制：三層 State 結構

Noise 的內部有三層 state object，由內而外：

```
┌─────────────────────────────────────────────────┐
│                HandshakeState                    │
│  ├── 自己的 static key pair (s)                  │
│  ├── 自己的 ephemeral key pair (e)               │
│  ├── 對方的 static public key (rs)               │
│  ├── 對方的 ephemeral public key (re)            │
│  ├── message_patterns: 剩餘的 pattern tokens     │
│  │                                               │
│  │  ┌───────────────────────────────────────┐    │
│  │  │          SymmetricState               │    │
│  │  │  ├── ck: chaining key (32 bytes)      │    │
│  │  │  ├── h:  handshake hash (32 bytes)    │    │
│  │  │  │                                    │    │
│  │  │  │  ┌──────────────────────────┐      │    │
│  │  │  │  │     CipherState          │      │    │
│  │  │  │  │  ├── k: encryption key   │      │    │
│  │  │  │  │  ├── n: nonce counter    │      │    │
│  │  │  │  │  └── has_key: bool       │      │    │
│  │  │  │  └──────────────────────────┘      │    │
│  │  │  └────────────────────────────────────┘    │
│  │  └────────────────────────────────────────────┘
│  └───────────────────────────────────────────────┘
```

### CipherState（最內層）

負責加密/解密。有 key 時用 AEAD（ChaCha20-Poly1305 或 AES-GCM），沒 key 時 passthrough（明文）。

### SymmetricState（中間層）

維護兩個值：

- **h（handshake hash）**：到目前為止所有 handshake data 的 hash。每次處理一個 token，h 都會更新。握手結束時 h 就是整個 handshake transcript 的 digest
- **ck（chaining key）**：用來衍生新 key 的 KDF chain。每次做 DH，DH output 會透過 HKDF 和 ck 結合，產生新的 ck 和 encryption key

```
MixKey(DH_output):
    ck, temp_k = HKDF(ck, DH_output, 2)
    CipherState.InitializeKey(temp_k)

MixHash(data):
    h = HASH(h || data)
```

### HandshakeState（最外層）

管理 key pair 和 pattern token 的執行：

```
WriteMessage(payload):
    for token in current_message_pattern:
        if token == 'e':
            生成 ephemeral key pair
            MixHash(e.public)
            把 e.public 寫入 output
        elif token == 's':
            把 s.public 用 CipherState 加密後寫入 output
            MixHash(encrypted_s)
        elif token in ('ee', 'es', 'se', 'ss'):
            dh_output = DH(local_key, remote_key)
            MixKey(dh_output)
    
    加密 payload（用 CipherState）
    MixHash(ciphertext)
    return output
```

握手結束時，SymmetricState.Split() 產生兩個 CipherState——一個用於 initiator → responder，一個用於 responder → initiator。

---

## 進一步用法：WireGuard 的 Noise_IKpsk2

WireGuard 選了 `Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s`：

- **IK**：雙方預先知道對方的 static public key（WireGuard 的 config 檔裡寫了 peer 的 public key）
- **psk2**：在第二輪（responder 的回應）加入 Pre-Shared Key（PSK）
- **25519**：X25519 做 DH
- **ChaChaPoly**：ChaCha20-Poly1305 做 AEAD
- **BLAKE2s**：BLAKE2s 做 hash

### WireGuard 握手流程

```
Initiator                                    Responder
─────────                                    ─────────
[已知: responder 的 static public key]        [已知: initiator 的 static public key]

生成 ephemeral key pair (e_I)
DH1 = DH(e_I, s_R)          ← es
加密 initiator 的 static key
DH2 = DH(s_I, s_R)          ← ss
─── Handshake Initiation ──→

                                        生成 ephemeral key pair (e_R)
                                        DH3 = DH(e_I, e_R)    ← ee
                                        DH4 = DH(s_I, e_R)    ← se
                                        MixKey(PSK)            ← psk2
                            ←── Handshake Response ───

兩邊都有 transport keys
═══ Encrypted Data ═══════════════ Encrypted Data ═══
```

為什麼 WireGuard 選 IK 而不是 XX？

1. VPN 場景：**你一定知道你要連哪個 server**。peer 的 public key 寫在 config 檔裡，不需要動態交換
2. IK 只需要 **1-RTT**（XX 需要 1.5-RTT）。VPN 對 latency 敏感
3. PSK 提供額外的 **post-quantum defense**：即使 X25519 被量子電腦破，PSK 仍然保護你（但前提是 PSK 沒被洩漏）

---

## 對比與取捨

### IK vs XX vs NK

| 面向 | NK | IK | XX |
|------|----|----|-----|
| Initiator 身份 | 匿名 | 已知（第一輪傳） | 已知（第三輪傳） |
| Responder 身份 | 已知（預先知道） | 已知（預先知道） | 動態交換 |
| 需要預先知道對方 key？ | 只需知道 responder | 雙方都需要 | 都不需要 |
| 握手輪數 | 1-RTT | 1-RTT | 1.5-RTT |
| Initiator 身份保護 | 完全（匿名） | 弱（若 responder key 洩漏，身份暴露） | 強（在加密後才傳） |
| 典型場景 | 匿名 client 連已知 server | VPN（WireGuard） | 通用 P2P |
| Forward secrecy | 有 | 有 | 有 |

### Noise vs TLS 1.3

| 面向 | Noise Framework | TLS 1.3 |
|------|----------------|---------|
| 設計目標 | 可組合的 handshake building blocks | 完整的 transport protocol |
| Certificate 驗證 | 不處理（你自己管 key distribution） | 內建 X.509 PKI |
| Cipher negotiation | 沒有（選 pattern 時就固定了） | 有（ClientHello 協商） |
| Session resumption | 沒有（需自己實作） | 內建 PSK-based resumption |
| Spec 長度 | ~35 頁 | ~160 頁 |
| 適用場景 | 嵌入式、VPN、自定義 protocol | Web、通用 Internet |
| Handshake pattern | 15+ 預定義 pattern，安全屬性已分析 | 固定的一種 handshake |
| 實作複雜度 | 低（幾百行 code） | 高（幾萬行 code） |

---

## 踩雷集錦

### 踩雷 1：「Noise 可以取代 TLS」

Noise 不處理以下問題：

- **Certificate verification**：Noise 假設你已經知道對方的 public key。「怎麼知道這個 public key 是對方的、不是攻擊者的」——Noise 不管。TLS 用 X.509 PKI 和 Certificate Authority 解決這個問題
- **Cipher negotiation**：Noise 在選 pattern 時就固定了 DH function、cipher、hash。沒有「client 和 server 協商用哪個 cipher」的機制
- **Version negotiation 和 backward compatibility**：Noise 沒有版本協商。如果你想換 cipher，需要定義新的 protocol name

**Noise 的定位是 building block，不是 drop-in replacement for TLS。**

### 踩雷 2：PSK mode 和 non-PSK mode 容易搞混

Noise 的 PSK modifier（`psk0`, `psk1`, `psk2` 等）表示在第幾輪的哪個位置 MixKey(PSK)。

- `Noise_IK`：沒有 PSK，只靠 DH
- `Noise_IKpsk2`：WireGuard 用的，在第二輪加入 PSK

PSK 和 non-PSK 版本的安全屬性不同：PSK 版本在 DH 被破的情況下（例如量子電腦）仍然有一定保護。但 PSK 版本需要預先共享一個 secret——增加了 key management 的負擔。

不要混用同一個 static key 在 PSK 和 non-PSK pattern 之間——可能有 subtle 的安全問題。

### 踩雷 3：Pattern 選錯導致安全屬性不符預期

常見錯誤：

- 用 **NK** 但預期 server 能驗證 client 身份 → NK 的 initiator 是匿名的，server 不知道 client 是誰
- 用 **IK** 但 initiator 的身份需要保密 → IK 在第一輪就傳 initiator 的 static key，如果 responder 的 static key 被 compromise，攻擊者能得知 initiator 的身份。用 **XX** 或 **XK** 更安全
- 用 **KK** 但其中一方的 static key 可能過期 → KK 假設雙方預先知道對方的 static key，如果一方換了 key 就需要重新 provision

---

## 進階

### One-way Patterns：N, K, X

除了互動式 pattern（NK, IK, XX 等），Noise 也定義了 one-way pattern——initiator 送一則加密訊息，不需要 responder 回應：

| Pattern | Pre-knowledge | 用途 |
|---------|---------------|------|
| N | Responder 的 s 已知 | 匿名加密訊息（像 sealed box） |
| K | 雙方的 s 都已知 | 已知身份的單向訊息 |
| X | Responder 的 s 已知 | Initiator 在訊息中附上自己的 s |

One-way pattern 沒有 forward secrecy（因為 responder 不做 DH），但適用於「fire-and-forget」的場景。

### Deferred Patterns

標準 pattern 的名字用兩個字母：第一個字母是 initiator 的 static key 何時驗證，第二個字母是 responder 的。

| 字母 | 含義 |
|------|------|
| N | 不驗證（anonymous） |
| K | 預先知道（pre-message） |
| X | 在握手中傳送 |
| I | 在第一輪就傳送（immediate） |

所以 `IK` = initiator 在第一輪就傳 static key（I），responder 的 static key 預先知道（K）。

Deferred pattern（名字帶數字，例如 `NK1`、`IX1`）把某些 DH 延遲到下一輪，改變了安全屬性的達成時機。通常用於特殊的安全需求。

### Noise Pipes：結合 XX 和 IK

一個常見的 deployment pattern 叫 **Noise Pipes**：

1. 第一次連線：用 **XX**（雙方不知道對方的 key）
2. 記住對方的 static key
3. 後續連線：用 **IK**（已知對方的 key，更快）
4. 如果 IK 失敗（對方換了 key）：fallback 回 XX

這結合了 XX 的通用性和 IK 的效率。

### Handshake Hash 的用途

握手結束後，SymmetricState 的 `h`（handshake hash）可以用作 **channel binding**。雙方可以把 `h` 顯示給使用者比對（類似 Signal 的 Safety Number），確認沒有 MITM。

`h` 也可以用在 application layer 的 authentication——例如在加密通道建立後再做一次 password 驗證，password 和 `h` 綁定，確保 password 不能被 relay 到另一個 session。

---

## 動手練習

1. **Pattern 閱讀練習**：讀 Noise spec 裡 `Noise_XK` 的 pattern，畫出 message flow 圖（類似本章的 ASCII 圖），標注每個 DH 操作和 key 傳送的時機

2. **IK vs XX 對比實驗**：用 `noiseprotocol` 分別建一個 IK 和一個 XX handshake，比較握手的 message 數量和每則 message 的大小

3. **安全屬性分析**：對 NK pattern，列出每一輪握手完成後的安全屬性。具體問題：Round 1 之後 initiator → responder 的訊息是否有 forward secrecy？是否有 responder 身份驗證？

4. **（挑戰）自己寫 SymmetricState**：用 Python 的 `hashlib` 和 `cryptography` 套件實作 Noise 的 `MixHash`、`MixKey`、`EncryptAndHash` 三個操作。對一組手動計算的 test vector 驗證你的實作

---

## 重點整理

1. **Noise 用 token 語言描述握手**：`e`（ephemeral key）、`s`（static key）、`ee/es/se/ss`（DH combination），每個 pattern 的安全屬性都已經被分析過
2. **三種常見 pattern**：NK（匿名 client + 已知 server）、IK（雙方已知，1-RTT）、XX（雙方未知，最通用）
3. **WireGuard 用 Noise_IKpsk2**：IK pattern + PSK 在第二輪，適合 VPN 的「雙方預先配置」場景
4. **Noise 不是 TLS 的替代品**：Noise 不處理 certificate verification、cipher negotiation、version negotiation。它是 building block，不是完整的 transport protocol
5. **三層 state 結構**：CipherState（加密）⊂ SymmetricState（KDF chain + hash）⊂ HandshakeState（key management + pattern execution）

---

## 自我檢核

1. Noise 的 `e`、`s`、`ee`、`es`、`se`、`ss` 各代表什麼操作？
2. IK 和 XX pattern 的主要差異是什麼？各自適合什麼場景？
3. WireGuard 為什麼選 IK 而不是 XX？PSK 的作用是什麼？
4. Noise 和 TLS 1.3 的定位差異是什麼？Noise 不處理哪些事情？
5. SymmetricState 的 `h` 和 `ck` 各自的功能是什麼？
6. 為什麼不該在 PSK 和 non-PSK pattern 之間共用同一個 static key？

---

## 延伸閱讀

- **Noise Protocol Framework spec（https://noiseprotocol.org/noise.html）**
  - 整份 spec 約 35 頁，值得全讀。Section 7（Handshake Patterns）和 Section 9（DH Functions, Cipher Functions, Hash Functions）是核心
- **WireGuard paper（Jason Donenfeld, NDSS 2017）**
  - "WireGuard: Next Generation Kernel Network Tunnel"——Section 5 完整描述了 Noise_IKpsk2 在 WireGuard 中的具體用法
- **Trevor Perrin, "Noise: A Crypto Framework"（Real World Crypto 2018 talk）**
  - 作者本人的演講，解釋 Noise 的設計動機和 pattern 系統
- **Girol et al., "Noise Explorer: Fully Automated Modeling and Verification for Arbitrary Noise Protocols"（IEEE EuroS&P 2020）**
  - Noise Explorer（https://noiseexplorer.com/）的論文——一個自動化工具，輸入 Noise pattern 就能產生 ProVerif model 和安全屬性報告

---

## 下一章預告

[Ch 37 — Protocol 出錯精選](./37-protocol-failures.md)：前三章講了 TLS 1.3、Signal、Noise 的「正確做法」。下一章反過來——用四個真實案例（Heartbleed、ROBOT、Logjam、Triple Handshake）展示 protocol-level 的漏洞有多致命。密碼學原語設計得再好，protocol 或 implementation 出錯就全盤皆輸。
