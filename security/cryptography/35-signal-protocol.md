# Ch 35 — Signal Protocol：端到端加密的工業標準

> 目標：能解釋 X3DH 初始握手和 Double Ratchet 的持續金鑰更新機制，理解為什麼 Signal Protocol 能同時提供 forward secrecy（洩漏長期金鑰不影響過去訊息）和 post-compromise security（被入侵後能自癒）。

---

## 為什麼需要 Signal Protocol

TLS 1.3（上一章）保護的是 client 和 server 之間的管道。但在即時通訊的場景裡，TLS 有三個根本性限制：

1. **Server 看得到明文**：TLS 在 client-to-server 和 server-to-client 兩段分別加密。訊息在 server 端是明文——server 被入侵、被傳票、或員工手賤就全洩漏
2. **Synchronous（同步）要求**：TLS 握手需要雙方同時在線。聊天 app 的使用者不會同時在線——Alice 發訊息時 Bob 可能手機關機
3. **沒有 post-compromise security**：TLS 1.3 有 forward secrecy（洩漏長期金鑰不影響過去的 session key），但一旦某一方的裝置被完全入侵（攻擊者拿到當前的 session key + long-term key），TLS 無法自癒——攻擊者可以永遠解密後續流量

Signal Protocol（前身叫 Axolotl Ratchet，由 Moxie Marlinspike 和 Trevor Perrin 設計）同時解決了這三個問題：

- **端到端加密（End-to-End Encryption, E2EE）**：訊息在 Alice 的裝置加密，在 Bob 的裝置解密，server 只看到密文
- **非同步握手**：Alice 可以在 Bob 離線時建立加密 session——靠的是 pre-key（預存在 server 上的一次性公鑰）
- **Post-compromise security**：即使攻擊者拿到某一刻的所有 session state，只要後續 Alice 和 Bob 再互傳一輪訊息，新的 DH ratchet 會產生攻擊者算不出的新 key

---

## 先建立直覺

把 Signal Protocol 想成兩層機制：

**第一層：X3DH 握手**——Alice 和 Bob「交換電話號碼」的過程。即使 Bob 不在線，Alice 也能從 server 拿到 Bob 預存的公鑰，算出一個共享秘密。

**第二層：Double Ratchet**——交換完電話號碼後的「每通電話都換一次密碼」的機制。每條訊息都用新 key 加密（forward secrecy），每次對話方向切換都做一次新 DH（post-compromise security）。

```
X3DH 建立初始 shared secret
        │
        v
Double Ratchet 持續更新 key
  ├── Symmetric ratchet（每條訊息更新一次）→ forward secrecy
  └── DH ratchet（每次 reply 更新一次）→ post-compromise security
```

---

## 核心概念：X3DH 初始握手

### 四把 Key

| Key 名稱 | 全稱 | 誰持有 | 壽命 | 用途 |
|----------|------|--------|------|------|
| IK | Identity Key | 每個使用者 | 永久 | 長期身份識別 |
| SPK | Signed Pre-Key | 每個使用者 | 中期（定期更換） | 允許非同步握手 |
| OPK | One-Time Pre-Key | 每個使用者 | 一次性（用完即棄） | 加強 forward secrecy |
| EK | Ephemeral Key | 發起者 | 一次性 | 握手用完即棄 |

Bob 預先把 IK_B、SPK_B（附 IK_B 的簽章）、一批 OPK_B 上傳到 server。Alice 想發訊息給 Bob 時，從 server 拿到這些公鑰。

### 範例一：用 Python 模擬 X3DH 握手

```python
"""
x3dh_demo.py — X3DH key agreement 模擬
用 X25519 做 DH，HKDF 做 key derivation
"""
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
import os

def generate_keypair():
    """生成 X25519 key pair"""
    sk = X25519PrivateKey.generate()
    pk = sk.public_key()
    return sk, pk

def x25519_dh(sk, pk):
    """X25519 DH exchange → 32 bytes shared secret"""
    return sk.exchange(pk)

def hkdf_derive(input_key_material: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256"""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=b'\x00' * 32,
        info=info,
    ).derive(input_key_material)

# ═══════════════════════════════════════════
# Bob 的 key bundle（預先上傳到 server）
# ═══════════════════════════════════════════
print("=== Bob 產生 key bundle ===")
IK_B_sk, IK_B_pk = generate_keypair()  # Identity Key
SPK_B_sk, SPK_B_pk = generate_keypair()  # Signed Pre-Key
OPK_B_sk, OPK_B_pk = generate_keypair()  # One-Time Pre-Key

# Bob 用 IK_B 簽署 SPK_B（實際用 Ed25519，這裡略）
print(f"  IK_B  public: {IK_B_pk.public_bytes_raw().hex()[:32]}...")
print(f"  SPK_B public: {SPK_B_pk.public_bytes_raw().hex()[:32]}...")
print(f"  OPK_B public: {OPK_B_pk.public_bytes_raw().hex()[:32]}...")

# ═══════════════════════════════════════════
# Alice 發起 X3DH
# ═══════════════════════════════════════════
print("\n=== Alice 發起 X3DH ===")
IK_A_sk, IK_A_pk = generate_keypair()  # Alice 的 Identity Key
EK_A_sk, EK_A_pk = generate_keypair()  # Alice 的 Ephemeral Key

# Alice 從 server 取得 Bob 的 key bundle
# 驗證 SPK_B 的簽章（略）

# 三個（或四個）DH 交換
DH1 = x25519_dh(IK_A_sk, SPK_B_pk)   # DH(IK_A, SPK_B) — 身份互認
DH2 = x25519_dh(EK_A_sk, IK_B_pk)    # DH(EK_A, IK_B)  — 綁定 Bob 身份
DH3 = x25519_dh(EK_A_sk, SPK_B_pk)   # DH(EK_A, SPK_B) — ephemeral × pre-key
DH4 = x25519_dh(EK_A_sk, OPK_B_pk)   # DH(EK_A, OPK_B) — one-time（可選）

print(f"  DH1: {DH1.hex()[:32]}...")
print(f"  DH2: {DH2.hex()[:32]}...")
print(f"  DH3: {DH3.hex()[:32]}...")
print(f"  DH4: {DH4.hex()[:32]}...")

# 合併所有 DH output，衍生 shared key
combined = DH1 + DH2 + DH3 + DH4
SK_alice = hkdf_derive(combined, info=b"X3DH_SharedKey")
print(f"\n  Alice 的 shared key: {SK_alice.hex()}")

# ═══════════════════════════════════════════
# Bob 收到 Alice 的初始訊息後，做同樣的 DH
# ═══════════════════════════════════════════
print("\n=== Bob 計算 shared key ===")
DH1_bob = x25519_dh(SPK_B_sk, IK_A_pk)   # DH(IK_A, SPK_B) 的另一邊
DH2_bob = x25519_dh(IK_B_sk, EK_A_pk)    # DH(EK_A, IK_B) 的另一邊
DH3_bob = x25519_dh(SPK_B_sk, EK_A_pk)   # DH(EK_A, SPK_B) 的另一邊
DH4_bob = x25519_dh(OPK_B_sk, EK_A_pk)   # DH(EK_A, OPK_B) 的另一邊

combined_bob = DH1_bob + DH2_bob + DH3_bob + DH4_bob
SK_bob = hkdf_derive(combined_bob, info=b"X3DH_SharedKey")
print(f"  Bob 的 shared key:   {SK_bob.hex()}")

# 驗證雙方得到同一個 key
assert SK_alice == SK_bob, "Keys don't match!"
print("\n✓ 雙方的 shared key 完全一致")
```

### X3DH 的三個 DH 各自的角色

```
DH1 = DH(IK_A, SPK_B)   ── Alice 的長期身份 × Bob 的中期 pre-key
                             → 證明「是 Alice 在跟擁有 SPK_B 的人通訊」
                             → 即使 EK_A 被洩漏，這個 DH 仍然安全

DH2 = DH(EK_A, IK_B)    ── Alice 的 ephemeral × Bob 的長期身份
                             → 綁定 Bob 的身份
                             → 即使 IK_A 被洩漏，這個 DH 仍然安全

DH3 = DH(EK_A, SPK_B)   ── Alice 的 ephemeral × Bob 的中期 pre-key
                             → 雙方都是「非長期」key，提供最強的 forward secrecy

DH4 = DH(EK_A, OPK_B)   ── Alice 的 ephemeral × Bob 的一次性 pre-key（可選）
                             → OPK 用完就刪，加強 forward secrecy
                             → 如果 server 上沒有剩餘的 OPK，退回三個 DH
```

為什麼要三個 DH 而不是一個？因為任何單一 key pair 都可能被 compromise。三個 DH 的組合確保：即使其中任何一對 key 被洩漏，共享秘密仍然安全。

---

## 底層機制：Double Ratchet

X3DH 建立了初始 shared secret `SK`。接下來的每條訊息都由 Double Ratchet 管理金鑰。

### 三條鏈

Double Ratchet 維護三條 KDF chain：

```
                      DH ratchet
                     (每次 reply)
                          │
                          v
┌──────────────────────────────────────────────────────┐
│                    Root Chain                         │
│  RK₀ ──[KDF]──→ RK₁ ──[KDF]──→ RK₂ ──[KDF]──→ ... │
│           │              │              │             │
│           v              v              v             │
│       CK_send₀      CK_recv₀      CK_send₁          │
└──────────────────────────────────────────────────────┘
              │              │              │
              v              v              v
         ┌────────┐    ┌────────┐    ┌────────┐
         │Sending │    │Receiving│   │Sending │
         │ Chain  │    │ Chain   │   │ Chain  │
         └────────┘    └────────┘   └────────┘
              │              │              │
         MK₀ MK₁ ...   MK₀ MK₁ ...  MK₀ MK₁ ...
         (每條訊息     (每條訊息     (每條訊息
          一把 key)     一把 key)     一把 key)
```

| Chain | 更新時機 | 提供的安全屬性 |
|-------|---------|---------------|
| Root Chain | 每次 DH ratchet step | 連結 DH ratchet 和 symmetric ratchet |
| Sending Chain | 每送出一條訊息 | Forward secrecy（用完即衍生下一個） |
| Receiving Chain | 每收到一條訊息 | Forward secrecy |

### Symmetric Ratchet：每條訊息更新 key

```
CK_n ──[KDF]──→ (CK_n+1, MK_n)
                    │       │
                    │       └── Message Key（加密第 n 條訊息）
                    └────────── 下一個 Chain Key
```

每次送訊息：
1. 用當前 Chain Key `CK_n` 衍生 Message Key `MK_n` 和下一個 Chain Key `CK_n+1`
2. 用 `MK_n` 加密訊息
3. 刪除 `MK_n` 和 `CK_n`

因為 KDF 不可逆，即使攻擊者拿到 `CK_n+1`，也算不出 `CK_n` 和 `MK_n`——**forward secrecy**。

### DH Ratchet：每次 reply 更新 key

```
Alice 送訊息時：
  Alice 的 DH ratchet key pair = (a, gᵃ)
  Alice 在 header 裡附上 gᵃ

Bob 回覆時：
  Bob 生成新的 DH ratchet key pair (b, gᵇ)
  Bob 計算 DH(b, gᵃ) → 新的 DH output
  Root Chain: RK_old ──[KDF(DH output)]──→ RK_new + new CK
  Bob 在 header 裡附上 gᵇ

Alice 收到 Bob 的回覆後：
  Alice 計算 DH(a, gᵇ) → 同一個 DH output
  Alice 也能推進 Root Chain
  Alice 生成新的 DH key pair (a', gᵃ') 準備下一次
```

這個 DH ratchet 的關鍵：即使攻擊者在某一刻拿到了 Alice 的整個 state（包括當前的 DH private key `a`），只要 Bob 回覆一次（帶上新的 `gᵇ`），Alice 收到後生成新的 `(a', gᵃ')`，攻擊者就算不出新的 DH output——**post-compromise security**。

### 範例二：用 Python 模擬 Double Ratchet 的一次 key rotation

```python
"""
double_ratchet_demo.py — 簡化的 Double Ratchet 演示
展示 symmetric ratchet + DH ratchet 的 key 更新
"""
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import hmac
import hashlib

def kdf_chain(chain_key: bytes) -> tuple[bytes, bytes]:
    """
    Symmetric ratchet: CK → (new_CK, message_key)
    用 HMAC 做 KDF（Signal spec 的做法）
    """
    new_ck = hmac.new(chain_key, b'\x02', hashlib.sha256).digest()
    mk     = hmac.new(chain_key, b'\x01', hashlib.sha256).digest()
    return new_ck, mk

def kdf_root(root_key: bytes, dh_output: bytes) -> tuple[bytes, bytes]:
    """
    DH ratchet: (RK, DH_output) → (new_RK, new_CK)
    """
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=64,
        salt=root_key,
        info=b"DoubleRatchet",
    ).derive(dh_output)
    new_rk = derived[:32]
    new_ck = derived[32:]
    return new_rk, new_ck

def dh(sk, pk):
    return sk.exchange(pk)

# ═══════════════════════════════════════
# 初始化：X3DH 之後，雙方共享 root_key
# ═══════════════════════════════════════
root_key = bytes.fromhex(
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
    "a7b8c9d0e1f2a3b4c5d6a7b8c9d0e1f2"
)

# Alice 的初始 DH ratchet key pair
alice_dh_sk = X25519PrivateKey.generate()
alice_dh_pk = alice_dh_sk.public_key()

# Bob 知道 alice_dh_pk（來自 X3DH 或第一條訊息的 header）

# ═══════════════════════════════════════
# Step 1: Alice 送三條訊息給 Bob（symmetric ratchet）
# ═══════════════════════════════════════
print("=== Alice 送三條訊息（同一個 sending chain）===")
# 先做一次 DH ratchet 初始化 sending chain
# （實際上第一次的 CK 來自 X3DH 後的初始推導，這裡模擬）
alice_send_ck = hmac.new(root_key, b'init_send', hashlib.sha256).digest()

for i in range(3):
    alice_send_ck, mk = kdf_chain(alice_send_ck)
    print(f"  Message {i}: MK = {mk.hex()[:24]}...")
    # Alice 用 mk 加密訊息，附上 alice_dh_pk 在 header
    # 刪除 mk

# ═══════════════════════════════════════
# Step 2: Bob 回覆（觸發 DH ratchet）
# ═══════════════════════════════════════
print("\n=== Bob 回覆（DH ratchet step）===")
bob_dh_sk = X25519PrivateKey.generate()
bob_dh_pk = bob_dh_sk.public_key()

# Bob 計算新的 DH output
dh_output_bob = dh(bob_dh_sk, alice_dh_pk)
print(f"  DH output: {dh_output_bob.hex()[:24]}...")

# Bob 推進 root chain → 得到新的 receiving CK 和 sending CK
root_key_new, bob_recv_ck = kdf_root(root_key, dh_output_bob)
# Bob 再做一次 root chain step 得到 sending CK
# （實際 protocol 會在 Bob 發訊息時再做）
root_key_new2, bob_send_ck = kdf_root(root_key_new, dh_output_bob)

bob_send_ck, mk_bob = kdf_chain(bob_send_ck)
print(f"  Bob 的第一條回覆 MK: {mk_bob.hex()[:24]}...")
# Bob 在 header 裡附上 bob_dh_pk

# ═══════════════════════════════════════
# Step 3: Alice 收到 Bob 的回覆（DH ratchet step）
# ═══════════════════════════════════════
print("\n=== Alice 收到回覆（DH ratchet step）===")
# Alice 看到 header 裡的 bob_dh_pk（新的！）
dh_output_alice = dh(alice_dh_sk, bob_dh_pk)
assert dh_output_alice == dh_output_bob  # 同一個 DH output
print(f"  DH output 一致: ✓")

# Alice 推進 root chain
root_key_alice_new, alice_recv_ck = kdf_root(root_key, dh_output_alice)

# Alice 生成新的 DH ratchet key pair → post-compromise security
alice_dh_sk_new = X25519PrivateKey.generate()
alice_dh_pk_new = alice_dh_sk_new.public_key()
print(f"  Alice 的新 DH 公鑰: {alice_dh_pk_new.public_bytes_raw().hex()[:24]}...")
print("  → 即使攻擊者之前拿到了 alice_dh_sk，")
print("    現在 alice_dh_sk_new 是全新的，攻擊者算不出來")
```

---

## 為什麼 Double Ratchet 這麼有名

| 產品 | 使用 Signal Protocol / Double Ratchet |
|------|--------------------------------------|
| Signal | 原生（由 Signal 團隊設計） |
| WhatsApp | 2016 年起全面啟用 |
| Facebook Messenger | Secret Conversations 模式 |
| Google Messages | RCS E2EE |
| Matrix (Element) | Olm / Megolm（基於 Double Ratchet 的變體） |
| Wire | 基於 Proteus（Double Ratchet 變體） |

幾乎所有主流端到端加密通訊 app 都用某種形式的 Double Ratchet。原因：它是目前唯一同時提供 forward secrecy + post-compromise security + 非同步的已知方案。

---

## 對比與取捨

### TLS 1.3 vs Signal Protocol

| 面向 | TLS 1.3 | Signal Protocol |
|------|---------|----------------|
| 保護範圍 | Client ↔ Server | Endpoint ↔ Endpoint（E2EE） |
| Server 看得到明文？ | 看得到 | 看不到 |
| 同步 / 非同步 | Synchronous（雙方需在線） | Asynchronous（用 pre-key） |
| Forward secrecy | 有（(EC)DHE） | 有（symmetric ratchet） |
| Post-compromise security | 無 | 有（DH ratchet） |
| Key 更新頻率 | 每個 session 一次 | 每條訊息（symmetric）/ 每次 reply（DH） |
| 身份驗證 | X.509 certificate + PKI | Trust on first use + safety number |
| 標準化 | IETF RFC 8446 | Signal 自己的 spec（非 IETF） |
| 適用場景 | 瀏覽器 ↔ web server | 即時通訊 app |

### Forward Secrecy vs Post-Compromise Security

| 屬性 | 定義 | 保護對象 | 誰提供 |
|------|------|---------|--------|
| Forward Secrecy | 洩漏長期金鑰不影響過去的 session key | 過去的訊息 | TLS 1.3, Signal |
| Post-Compromise Security | 被入侵後，經過一輪互動就能恢復安全 | 未來的訊息 | Signal（TLS 沒有） |

---

## 踩雷集錦

### 踩雷 1：「Signal 用 E2EE 所以完全安全」

E2EE 保護的是**傳輸中的訊息內容**。但：

- **Metadata（元資料）仍然可見**：server 知道「Alice 在 3:47 AM 發了訊息給 Bob」——誰和誰通訊、頻率、時間，這些 metadata 本身就有極高的情報價值。Signal 用 Sealed Sender 機制嘗試隱藏發送者，但這不是完美的
- **Endpoint 被 compromise 就完蛋**：如果攻擊者拿到你的手機（解鎖狀態），Double Ratchet 保護不了已經解密存在裝置上的歷史訊息。Signal 的 Disappearing Messages 嘗試緩解這個問題
- **Key verification 是 trust-on-first-use（TOFU）**：除非你手動掃 Safety Number QR code，否則 server 可以做 MITM（插入自己的 public key）。大多數使用者從不驗證 Safety Number

### 踩雷 2：X3DH 的 One-Time Pre-Key 用完就沒了

OPK 是一次性的——每個 OPK 只能被一個發起者使用。如果 Bob 很受歡迎，他的 OPK 很快就會被用完。用完後，X3DH fallback 到只做三個 DH（沒有 DH4）。

沒有 OPK 的安全性稍弱：如果 Bob 的 SPK 在某個時間點被 compromise，攻擊者可以用它來完成 X3DH——而有 OPK 時，即使 SPK 被 compromise，攻擊者還需要 OPK（已經被用掉且刪除了）。

**教訓**：Client 要定期上傳新的 OPK batch 到 server。

### 踩雷 3：Double Ratchet 的 State 很大

每個 conversation 需要存：
- Root key
- Sending chain key
- Receiving chain key
- 當前 DH ratchet key pair
- 對方的 DH ratchet public key
- Message number counters
- Skipped message keys（處理亂序訊息）

對一對一聊天還好，但 group chat 的 state 是 O(n) 的（n = 群組人數）。這就是為什麼 Matrix 用 Megolm（Sender Key 方案）做 group chat——犧牲一些 post-compromise security 換取 O(1) 的 state。

### 踩雷 4：Group Chat 的 E2EE 比想像中難

Signal Protocol 原本為一對一設計。Group chat 的做法是每個成員和其他每個成員各維護一個 Signal session——N 人群組有 N(N-1)/2 個 session。發訊息時，sender 對每個 recipient 分別加密。

這意味著：
- 發送一條群組訊息的計算量是 O(N)
- 加入/退出群組需要更新所有 session
- 群組越大，overhead 越高

WhatsApp 對大群組（> 256 人）使用不同的 E2EE scheme（Sender Key）。

---

## 進階

### Sealed Sender：隱藏發送者

Signal 的 Sealed Sender 機制：Alice 用 Bob 的 Identity Key 加密 sender certificate，server 只看到「某個人發了訊息給 Bob」而不知道發送者是 Alice。

流程：
1. Alice 從 Signal server 取得一個 Sender Certificate（由 Signal 簽發，證明 Alice 的身份）
2. Alice 用 Bob 的 Identity Key 加密整個訊息（包含 Sender Certificate）
3. Server 只能把密文轉給 Bob——不知道是誰寄的
4. Bob 解密後才看到 Sender Certificate，知道是 Alice 寄的

限制：server 仍然知道 Bob 收到了一條訊息、IP 地址等 metadata。

### PQXDH：Post-Quantum X3DH

Signal 在 2023 年推出 PQXDH——在 X3DH 的基礎上加入 ML-KEM（Kyber）：

```
原始 X3DH:  DH1 || DH2 || DH3 [|| DH4]
PQXDH:      DH1 || DH2 || DH3 [|| DH4] || KEM_output
```

KEM_output 來自 Alice 對 Bob 的 post-quantum pre-key 做 ML-KEM encapsulation。即使未來量子電腦能破 X25519 DH，ML-KEM 的 output 仍然安全。

這是 **hybrid approach**：同時用 classical DH 和 post-quantum KEM，確保即使其中一個被破，另一個仍然保護你。

### Session Management 的複雜性

實際的 Signal 實作要處理很多 edge case：

- **亂序訊息**：UDP 或推播通知可能導致訊息亂序到達。Double Ratchet 需要快取 skipped message keys
- **多裝置**：一個使用者可能有手機 + 電腦 + 平板。每個裝置有自己的 Identity Key，其他人要對每個裝置分別建立 session
- **Key rotation**：SPK 和 OPK 需要定期更換。舊 SPK 要保留一段時間（因為可能有在途的訊息用了舊 SPK）

---

## 動手練習

1. **X3DH 完整實作**：在範例一的基礎上，加入 Ed25519 對 SPK 的簽章 + 驗章。Alice 在用 Bob 的 SPK 之前應該先驗證 Bob 的 IK 對 SPK 的簽章

2. **Symmetric ratchet chain**：實作一條 10-step 的 symmetric ratchet chain，驗證 forward secrecy——從 `CK₅` 開始，試著反推 `MK₃`（應該做不到）

3. **DH ratchet 模擬**：模擬 Alice 和 Bob 來回對話五輪，每輪都做 DH ratchet。印出每一輪的 root key 和 chain key，觀察它們如何隨著每次 DH 而徹底改變

4. **（挑戰）Post-compromise security 驗證**：在第 3 題的基礎上，假設攻擊者在第 3 輪拿到了 Alice 的完整 state（root key + DH private key + chain key），驗證攻擊者在第 4 輪之後無法衍生正確的 key（因為 Bob 帶了新的 DH 公鑰）

---

## 重點整理

1. **X3DH 解決非同步握手**：靠 pre-key（SPK + OPK）讓 Alice 在 Bob 離線時也能建立共享秘密
2. **Double Ratchet 同時提供 forward secrecy 和 post-compromise security**：symmetric ratchet 負責前者，DH ratchet 負責後者
3. **三條 chain**：Root Chain 連結 DH ratchet 和 symmetric ratchet；Sending Chain 和 Receiving Chain 各自為每條訊息衍生 unique message key
4. **Post-compromise security 是 Signal 獨有的（相對於 TLS）**：一旦雙方互傳一輪訊息，新的 DH ratchet 就把攻擊者踢出去
5. **E2EE 不是萬靈丹**：metadata 洩漏、endpoint compromise、缺乏 key verification 都是實際威脅

---

## 自我檢核

1. X3DH 的三個 DH 交換分別用了哪些 key？各自的功能是什麼？
2. Double Ratchet 的 symmetric ratchet 和 DH ratchet 分別在什麼時候觸發？
3. Forward secrecy 和 post-compromise security 的差別是什麼？Signal Protocol 用什麼機制提供各自？
4. 為什麼 X3DH 需要 One-Time Pre-Key？沒有 OPK 時安全性差在哪？
5. Group chat 的 E2EE 為什麼比 one-on-one 更難？Signal 和 Matrix 各自怎麼處理？
6. PQXDH 在 X3DH 的基礎上加了什麼？為什麼要用 hybrid approach？

---

## 延伸閱讀

- **Signal Protocol 官方 spec**
  - X3DH：https://signal.org/docs/specifications/x3dh/ — 完整的協議定義，不長，值得全讀
  - Double Ratchet：https://signal.org/docs/specifications/doubleratchet/ — 包含完整的 state machine 和 pseudo-code
- **"The Signal Protocol for Dummies"（Trail of Bits blog）**
  - 把 X3DH 和 Double Ratchet 用圖解拆解，可讀性極高
- **Cohn-Gordon et al., "A Formal Security Analysis of the Signal Messaging Protocol"（IEEE EuroS&P 2017）**
  - Signal Protocol 的第一個完整形式化安全證明——證明 Double Ratchet 確實提供 forward secrecy 和 post-compromise security
- **"More Instant Messages"（Marlinspike & Perrin, 2016）**
  - X3DH 的設計 rationale，解釋每一個 DH 的目的
- **Signal 的 PQXDH spec**
  - https://signal.org/docs/specifications/pqxdh/ — 後量子擴展的完整定義

---

## 下一章預告

[Ch 36 — Noise Framework](./36-noise-framework.md)：X3DH 和 TLS 1.3 都是「特定用途的 handshake protocol」——每次設計新 protocol 都要重新做安全分析。Noise Framework 提供了一套可組合的 handshake building blocks，讓你用 token 語言（e, s, ee, es, se, ss）描述 handshake pattern，自動得到安全屬性分析。WireGuard VPN 用的就是 Noise。
