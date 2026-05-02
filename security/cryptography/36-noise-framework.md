# Ch 36 — Noise Framework：handshake pattern 系統化

> 目標：搞懂 Noise Framework 怎麼把 handshake 設計工程化。pattern 標記法（IK / XX / NK / KK 等）、token（e、s、ee、es、se、ss）的組合語意、WireGuard 為什麼用 Noise IK pattern。

## 為什麼需要 Noise

過去每個 protocol 自己設計 handshake：

```
TLS 1.2 handshake:    特殊 design，patch 多年
SSH handshake:        另一套
IPSec IKE:            又另一套
Signal X3DH:          特殊 design
WireGuard:            最終選 Noise
```

每個都要**自己證明安全**。每個都可能踩雷（IPSec、SSH 都有過 design flaw）。

**Noise Framework**（Trevor Perrin，Signal 共同 author）2018 提：把 handshake 設計**系統化、組合化**。

```
Noise = small set of primitives + grammar
       → systematic generation of handshake patterns
       → 每個 pattern 自帶安全證明
```

## Noise 的元件

```
DH:    X25519 / X448 (主流選 X25519)
Cipher: AES-GCM / ChaCha20-Poly1305
Hash:  SHA-256 / SHA-512 / BLAKE2

Pattern: 描述 handshake 的 token 序列
```

每個 Noise protocol 用 `Noise_<pattern>_<DH>_<cipher>_<hash>` 命名：

```
Noise_IK_25519_ChaChaPoly_BLAKE2s   ← WireGuard 用
Noise_NN_25519_AESGCM_SHA256
Noise_XX_448_ChaChaPoly_SHA512
```

清楚說明用哪些 primitive。

## Token 系統

handshake 是**一系列 token 的交換**：

```
Single tokens:
  e   "send my ephemeral DH public key"
  s   "send my static DH public key"

DH tokens (兩個元素 mix):
  ee  "DH(my_ephemeral, their_ephemeral)"
  es  "DH(my_ephemeral, their_static)"
  se  "DH(my_static, their_ephemeral)"
  ss  "DH(my_static, their_static)"
  
特殊 token (PSK):
  psk  "mix in pre-shared key"
```

DH token 的結果累積進 chaining key（類似 Signal 的 root key）。

## Pattern 命名法

兩個字母：

```
First letter (initiator's key knowledge):
  N - No static key from initiator
  K - Known static key by responder upfront
  X - X transmitted to responder during handshake
  I - Immediately transmitted (in first message)

Second letter (responder's key knowledge):
  N - No static key
  K - Known to initiator upfront
  X - Transmitted during handshake

例:
  NN: 都沒 static key (anonymous)
  IK: initiator immediately sends, responder K (initiator already knows)
  XX: 雙方都在 handshake 中 X-mit static key
```

## 經典 pattern 詳解

### NN (Anonymous DH)

```
-> e
<- e, ee
```

最簡單。雙方各送 ephemeral，做一個 DH。

**問題**：沒 authentication，MITM 風險。

### XX (Mutual Auth, no foreknowledge)

```
-> e
<- e, ee, s, es
-> s, se
```

3 個 message 雙向 authenticate。雙方都不知對方 static key 之前。**TLS-like flexibility**。

### IK (initiator known + responder static)

```
-> e, es, s, ss
<- e, ee, se
```

initiator 一開始就知道 responder static key，**第一個 message 就能加密 + 認證**。

兩個 message 完成。**WireGuard 用這個** — server 公開 static key（在 config）、client 一開始就用。少 round trip = 快 + simple。

### KK (mutual known static)

```
-> e, es, ss
<- e, ee, se
```

雙方早就知對方 static。**極簡 + 高度認證**。但前提是 key distribution 已解。

## WireGuard 用 Noise_IK

```
WireGuard config:
  [Peer]
  PublicKey = <responder's static key>     ← 一開始就有
  Endpoint = ...
  AllowedIPs = ...
```

initiator (client) 連時：

```
1. 知道 responder 的 static pub key
2. 第一個 message 包 client ephemeral + 加密的 client static + ...
3. 一個 round trip 完成 handshake
```

**僅 1.5 RTT**（quasi-1RTT）。比 TLS 1.3 還快。

WireGuard 的 simplicity 大部分來自 Noise — 把複雜的 handshake 邏輯外包給 Noise 規範。

## Handshake state 細節

每個 endpoint 維護：

```
HandshakeState:
  cipher_state   # 當前加密 state
  hash           # h: 累積所有 transcript 的 hash
  ck             # chain key (root key 等)
  
  static_priv    # local static
  ephemeral_priv # local ephemeral
  remote_static  # 對方 static (若已知)
  remote_ephemeral # 對方 ephemeral (handshake 後)
```

每個 token 處理：

```python
def process_token(state, token):
    if token == 'e':
        state.ephemeral = generate_ephemeral()
        state.send(state.ephemeral.public_key())
        state.h = hash(state.h || state.ephemeral.public_key())
    elif token == 'ee':
        dh = DH(state.ephemeral, state.remote_ephemeral)
        state.ck, state.cipher_state = HKDF(state.ck, dh)
    elif token == 'es':
        dh = DH(state.ephemeral, state.remote_static) if initiator \
             else DH(state.static, state.remote_ephemeral)
        state.ck, state.cipher_state = HKDF(state.ck, dh)
    # ...
```

## 安全性質：每個 pattern 自帶

Noise 文件對每個 pattern 給出形式化安全 properties：

```
Noise_IK 提供:
  Sender authentication: 有 (responder static 已 verified before handshake)
  Receiver authentication: 有
  Forward secrecy: 有 (ephemeral key 摧毀後過去通訊安全)
  Identity hiding: 部分
```

每個性質有正式 game-based 定義。Noise 規範用 5 個 grade（None / Weak / Strong / Perfect 等）。

寫應用 protocol 時可以**精準說「我需要這些 properties」 → 找對應 pattern**。

## 程式範例：Noise via dissononce

```python
# pip install dissononce
from dissononce.processing.handshakepatterns.handshakepatternfactory import HandshakePatternFactory
from dissononce.processing.impl.handshakestate import HandshakeState
from dissononce.processing.impl.symmetricstate import SymmetricState
from dissononce.processing.impl.cipherstate import CipherState
from dissononce.cipher.chachapoly import ChaChaPolyCipher
from dissononce.dh.x25519.x25519 import X25519DH
from dissononce.hash.blake2s import Blake2sHash

# 建立 NN pattern handshake
pattern = HandshakePatternFactory.get_pattern('NN')

initiator = HandshakeState(
    SymmetricState(CipherState(ChaChaPolyCipher()), Blake2sHash()),
    X25519DH()
)
initiator.initialize(pattern, True, b'')

# 第一個 message: -> e
message_buffer = bytearray()
initiator.write_message(b'', message_buffer)

# ... 之後類似
```

## Noise 與 PQC

Noise extension 有 PQ 變體（draft-aviram-noise-pqc 等）。基本想法：在 token 添加 ML-KEM encapsulation。標準化還在進行。

## 一個常見誤解

「Noise 是新的、未審查的協定」

**反過來**。Noise 由 Trevor Perrin（Signal 共同設計者）+ 學術社群 5+ 年迭代。**形式化驗證做過多次**（Bhargavan / Kobeissi 等）。

**WireGuard、Lightning Network、Signal、Tox 等都用** — 廣泛 production deployment。比自己 roll handshake 安全得多。

## 自我檢核

- [ ] 我能列舉 Noise 的 token（e, s, ee, es, se, ss）的意義
- [ ] 我能解釋 NN / IK / XX 三個 pattern 的差別
- [ ] 我能說出 WireGuard 為什麼選 IK pattern
- [ ] 我能解析 `Noise_IK_25519_ChaChaPoly_BLAKE2s` 名字的每個部分
- [ ] 我能說出 Noise 提供的安全 properties grade
- [ ] 我知道用 Noise 寫 protocol 比自己 roll 安全得多

下一章看 protocol 出錯精選 — Heartbleed、Logjam、ROBOT、Triple Handshake。

→ [Ch 37 Protocol 出錯精選](./37-protocol-failures.md)
