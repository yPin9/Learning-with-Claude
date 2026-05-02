# Ch 35 — Signal Protocol：X3DH + Double Ratchet

> 目標：搞懂端對端加密通訊的工業標準。X3DH（Extended Triple Diffie-Hellman）asymmetric 起手、Double Ratchet（symmetric ratchet + DH ratchet）怎麼提供 forward secrecy + post-compromise security。WhatsApp / Signal / Messenger 全用這套。

## Signal 協定的兩個目標

```
1. Forward Secrecy:
   今天的 key 洩漏 → 不影響昨天的 message
   
2. Post-Compromise Security (Future Secrecy):
   今天的 key 洩漏 → 一段時間後新 message 又安全
   (假設 attacker 不能持續控制 device)
```

第二個性質**特別難** — 大多數加密協定（TLS、PGP）做不到。Signal 的 Double Ratchet 是創新解法。

## 整體架構

```
1. X3DH (Extended Triple DH)
   First time A 連 B → 共建初始 shared key
   Asymmetric, asynchronous (B 不在線也可)

2. Double Ratchet
   後續每個 message 推進 key
   Symmetric ratchet + DH ratchet 雙進化
   
最終效果：每個 message 用獨立 key
        forward secrecy + post-compromise
```

## X3DH 細節

每個用戶有 3 種 key：

```
IK (Identity Key):       長期 key，公開於 server
SPK (Signed Pre-Key):    中期 key，每幾天換，server 簽（用 IK）
OPK (One-Time Pre-Key):  一次性 key，server pool 一堆，用一次扔
```

Alice 想連 Bob：

```
1. Alice 從 server 取 Bob 的 (IK_B, SPK_B, OPK_B)
   server 同時驗證 SPK_B 是 Bob 用 IK_B 簽的

2. Alice 自己有 (IK_A, EK_A)  
   IK_A: 長期
   EK_A: ephemeral (每次連線新的)

3. 算 4 個 DH:
   DH1 = DH(IK_A, SPK_B)
   DH2 = DH(EK_A, IK_B)
   DH3 = DH(EK_A, SPK_B)
   DH4 = DH(EK_A, OPK_B)  ← optional 但 Signal 用

4. SK = HKDF(DH1 || DH2 || DH3 || DH4)
   ← shared secret，後續 ratchet 用

5. Alice 送 (IK_A, EK_A, OPK_B 的 ID, encrypted_msg) 給 server
```

Bob 上線後：

```
1. Bob 取 (IK_A, EK_A, OPK_B 的 ID) 從 server
2. 算同樣 4 個 DH
3. 算同 SK
4. 解密 message
5. 把 OPK_B 從 pool 拿掉（一次性）
```

## 為什麼是「4 個 DH」

不同 key 提供不同性質：

```
DH1 = DH(IK_A, SPK_B):   驗證 Alice 與 Bob 的長期身分
DH2 = DH(EK_A, IK_B):    驗證 Bob 的長期身分（讓 Alice 知道是 Bob）
DH3 = DH(EK_A, SPK_B):   forward secrecy（EK 是 ephemeral）
DH4 = DH(EK_A, OPK_B):   每 session 隨機 OPK → 即使 SPK 被破不影響
```

**4 個 DH 一起 mix → 抗多種 attack**。漏掉任一性質會出洞。

## Double Ratchet

X3DH 給雙方初始 SK。後續 message：**每個 message 一個新 key**。

### Symmetric Ratchet

```
Chain Key (CK_0) -- KDF --> CK_1 -- KDF --> CK_2 ...
                |           |             |
                v           v             v
         Message Key 0  Message Key 1  Message Key 2
```

每個 message 一個 message key（用完丟）。chain key 進化（**單向 hash chain**）。

```python
def kdf_ratchet(chain_key):
    new_ck = HMAC(chain_key, b"\x02")  # 進化用
    msg_key = HMAC(chain_key, b"\x01")  # 解密用
    return new_ck, msg_key
```

**前向安全**：CK_n 洩漏 → CK_{n+1}, CK_{n+2}, ... 仍導 forward derive，但**不能回推 CK_{n-1}**（hash 不可逆）。

但 attacker 拿到 CK_n 仍能解所有未來 messages（直到 DH ratchet 進化）。**這就是為什麼還要 DH ratchet**。

### DH Ratchet

每次方向切換（A→B 換 B→A），雙方 generate 新的 ephemeral DH key 並交換：

```
Alice                            Bob
  │  ratchet pub: dh_A_0         │
  │── encrypted msg + dh_A_0 ───►│
  │                              │
  │  Bob: 算 DH(dh_B_-1, dh_A_0) │
  │       → 新 root key           │
  │       → 衍生新 chain key       │
  │                              │
  │  Bob 回信時 generate dh_B_0   │
  │◄── encrypted msg + dh_B_0 ──│
  │                              │
  │  Alice: 算 DH(dh_A_0, dh_B_0)│
  │         → 新 root key         │
  │         → 衍生新 chain key     │
```

每次 DH 交換 → root key 進化 → 新 chain key 開始。**舊 chain 全部失效**。

**Post-Compromise Security**：如果 attacker 偷 dh_A_0 但 dh_A_1 之後她沒控制 → 等 Alice 下次發 message + 新 ratchet → attacker 拿不到新 root key。

## 整體 ratchet 進化

```
DH ratchet（粗粒度）
  Root_0 → Root_1 → Root_2 → ...
              │       │
              v       v
         Chain_A  Chain_B   (per direction)
              │       │
              v       v
       Symmetric ratchet（細粒度）
       MK_0 → MK_1 → MK_2 → ... (per message)
```

兩層：

- **Symmetric ratchet**：每 message 進化（forward secrecy 細粒度）
- **DH ratchet**：每方向切換進化（post-compromise security）

## Out-of-order Message

Messaging 不保證順序到。Signal 處理：

```
Bob 收到 message_5（message_3, 4 還沒到）：
  從 chain_key_2 推進兩次得 MK_3, MK_4，**儲存** 等待
  推進到 MK_5 解密 message_5
  之後 message_3, 4 到時用 stored MK_3, MK_4 解密
```

stored key 一段時間後自動丟（防止 attacker 拿到 device 後解開幾十條過去 message）。Signal 預設 stored key 1000 個或 30 天。

## 最終的 message format

```python
class SignalMessage:
    dh_pub: bytes          # ratchet ephemeral pub key
    prev_chain_length: int # 前一個 chain 長度（給 out-of-order 用）
    chain_index: int       # 此 message 在當前 chain 的 index
    ciphertext: bytes      # 用 message key 加密的 payload
    mac: bytes             # 防篡改
```

ciphertext 用 AES-256-CBC + HMAC-SHA256（Signal 自家 protocol，不是 AEAD）。Modern fork 也有用 AEAD 的。

## libsignal-protocol 程式範例

```python
# Signal 的 Python binding（簡化偽碼）
from signal_protocol import (
    InMemorySignalProtocolStore,
    SessionBuilder,
    SessionCipher,
)

# 建立 Alice 與 Bob 的 store
alice_store = InMemorySignalProtocolStore(alice_identity_keypair, ...)
bob_store = InMemorySignalProtocolStore(bob_identity_keypair, ...)

# Bob 上傳 prekey bundle
bob_bundle = bob_store.get_prekey_bundle(...)

# Alice 用 bundle 建立 session
session_builder = SessionBuilder(alice_store, bob_address)
session_builder.process_prekey_bundle(bob_bundle)

# Alice 加密
cipher = SessionCipher(alice_store, bob_address)
ciphertext = cipher.encrypt(b"Hello Bob")

# Bob 解密
bob_cipher = SessionCipher(bob_store, alice_address)
plaintext = bob_cipher.decrypt(ciphertext)
```

實際 API 比這複雜，但骨架類似。

## PQXDH：post-quantum 升級

Signal 2024 推 **PQXDH (Post-Quantum X3DH)**：

```
原 X3DH：4 個 ECDH
PQXDH：4 個 ECDH + 1 個 ML-KEM encapsulation

shared = HKDF(DH1 || DH2 || DH3 || DH4 || ML-KEM_secret)
```

混合古典 + PQC。**攻擊者要破古典 ECDH 與 ML-KEM 兩者**才能拿 shared secret。

WhatsApp、Signal client 已部署。

## Group Chat：sender keys

1-on-1 用 X3DH + Double Ratchet。Group chat 用不同 protocol：

```
Sender Keys:
  每個 group member 有自己的 sender chain key
  消息用 sender chain key 派生 message key
  其他 member 也維護 sender chain（同步進化）
```

不同 protocol 因為 **N 人 group 用 N² Double Ratchet 太貴**。Sender Keys 是 O(N)。

代價：**post-compromise security 弱**（一個 member 被駭可能洩多個 message 直到下次 key rotation）。

WhatsApp、Signal、Messenger、Element 多用變體 sender keys。MLS (Messaging Layer Security) 是 IETF 標準化的 group protocol，更強，2023 標準化。

## Signal 完整應用 stack

```
- Cryptography: X25519 + Ed25519 + AES-256-CBC + HMAC-SHA256 + ML-KEM
- Protocol: X3DH / PQXDH + Double Ratchet
- Transport: TLS 1.3 to Signal server
- Server: 不存 message（24h 暫存後刪）、minimal metadata
- Open source: 全 client + server 公開審查
```

## 一個常見誤解

「WhatsApp 加密所以 Meta 看不到我的訊息」

**訊息加密是 end-to-end**（Signal protocol），Meta 看不到內容。但：

- **metadata** 看得到（誰跟誰聊、何時、多久）
- **backup**（如 iCloud / Google Drive 備份）通常**未加密**或用較弱加密
- **key 仍在 client 端**，被 root 後可拿
- **server 可能漏發 prekey** 引導 client 與假 key 通訊（雖然 Signal 有 verification 但用戶不常做）

E2EE 是強保證但**不是萬能**。需配合 verification、device 信任、metadata 防護。

## 自我檢核

- [ ] 我能畫 X3DH 的 4 個 DH
- [ ] 我能解釋每個 DH 提供的安全性質
- [ ] 我能描述 symmetric ratchet 與 DH ratchet 的進化
- [ ] 我能說出 forward secrecy 與 post-compromise security 的差別
- [ ] 我能解釋 out-of-order message 怎麼處理
- [ ] 我知道 PQXDH 是什麼以及 hybrid 構造

下一章看 Noise Framework — handshake pattern 系統化。

→ [Ch 36 Noise Framework](./36-noise-framework.md)
