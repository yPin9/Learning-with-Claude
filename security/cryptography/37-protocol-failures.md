# Ch 37 — Protocol 出錯精選：Heartbleed、ROBOT、Logjam、Triple Handshake

> 目標：四個經典 protocol 等級災難，看 protocol 怎麼錯。Heartbleed（OpenSSL 2014，缺 length check）、ROBOT（Bleichenbacher 復活在 TLS 1.2，2017）、Logjam（DH downgrade，2015）、TLS Triple Handshake（cross-protocol）。每個都是密碼學課。

## Heartbleed (CVE-2014-0160)

2014 年 4 月公開。**OpenSSL 1.0.1 的 TLS heartbeat 擴展**有 bug。

### 機制

TLS heartbeat extension（RFC 6520）讓 client 送 keepalive：

```
struct {
    HeartbeatMessageType type;
    uint16 payload_length;
    opaque payload[HeartbeatMessage.payload_length];
    opaque padding[padding_length];
} HeartbeatMessage;
```

server 看到 → 把 payload 回送（echo）。

OpenSSL 實作：

```c
// 偽碼
unsigned char *p = received_packet;
unsigned short payload_length = ntohs(*(unsigned short *)p);
p += 2;

unsigned char *response = malloc(1 + 2 + payload_length + padding);
memcpy(response + 3, p, payload_length);  // ← 沒檢查實際 packet 大小！
```

**bug**：`payload_length` 是 attacker 可控。送 packet 含 `payload_length = 65535` 但實際 payload 只 1 byte → server `memcpy` 從 packet buffer 讀 65535 byte → **讀到 packet 之後的 memory** → 包含其他 user 的 data、private key、cookie 等。

### 影響

```
65535 byte = 64 KB 一次讀
全球 17% HTTPS server 受影響
影響 server private key（重新發證書 + revoke 舊的）
影響 user session、密碼、信用卡
```

修復：

```c
// patched
if (1 + 2 + payload_length + 16 > received_length)
    return error;
```

**一行 length check 修好** — 但這個 bug 在 OpenSSL 裡躺 2 年。

### 教訓

1. **length validation 永遠是 protocol 殺手**：buffer overflow 在 C 領域 30 年沒解決
2. **記憶體安全語言**（Rust、Go）對 protocol 實作有真實價值
3. **fuzz testing**：libfuzzer / AFL 對 OpenSSL 後續找出大量類似 bug
4. **OpenSSL 後來重大改寫 + 開支援基金（OpenSSL Foundation）**

Heartbleed 是密碼學社群的 wake-up call。**很多 modern security investment（OSS-Fuzz、Linux Foundation、Core Infrastructure Initiative）都從這裡來**。

## Logjam (2015)

CVE-2015-4000。**DH downgrade attack**。

### 機制

回憶 Ch 21：

```
- 美國早期出口管制要求弱 DH（512-bit）
- 即使 2015 仍有 server support EXPORT_DHE 為 backwards compat
- 多 server 共用某幾個 1024-bit DH primes
```

attacker MITM：

```
1. client 送 cipher suite list（含 ECDHE_RSA、DHE_RSA 等強選項）
2. attacker 改成只 EXPORT_DHE
3. server 用 512-bit DH
4. attacker 預計算 GNFS first stage 對共用 prime
   (一次幾百萬美元 nation-state 等級投資)
5. 實時破譯該 session 的 DH
6. MITM 成功
```

### 影響

```
8.4% HTTPS server 受影響
26% SSH server
56% IPSec/IKE server
```

特別恐怖的是 IPSec：許多 VPN 用受影響參數。**nation-state 級 mass surveillance 變得可能**。

### 教訓

1. **永遠不留 export-grade cipher**
2. **共用 prime 在 large attack budget 下危險**
3. **client 該 reject 弱參數**（就算 server 給）
4. **TLS 1.3 砍掉 export、自選 prime → 根本解**

Logjam 的影響推進 TLS 1.3 設計。

## ROBOT (2017)

CVE-2017-13099 等。**Bleichenbacher 1998 復活**。

### 機制

回憶 Ch 20：Bleichenbacher 攻擊 RSA PKCS#1 v1.5 padding oracle。1998 已知，TLS 1.0+ 加 mitigation。

但 mitigation **沒做對**：

```
TLS RSA key exchange：
  client 送 RSA(server_pubkey, pre_master_secret)
  server 解密、驗 padding
  
  本來 spec 要求：padding 錯時也要繼續處理（用 random pre_master_secret）
                 不洩漏「padding 錯」的資訊

  但很多 server 實作偷工：
    timing 不一致（padding 錯時較快）
    回不同 alert（padding 錯回 BadRecordMac，padding 對回 DecryptError）
    其他 oracle channel
```

Hanno Böck 等 2017 系統地測試 100 大 HTTPS site，**1/3 易受 Bleichenbacher attack**：

```
受影響：Facebook、Cisco ACE、Citrix、F5、IBM、Erlang、PAN-OS、Cavium...
攻擊步驟：百萬次 query，幾小時破 session
```

### Why 19 年後仍有效

- 1998 後加 mitigation 但**沒驗證**（多 vendor 實作各自 mitigate，結果各異）
- 程式碼複雜：怎麼確定每個 error path 都 const-time / 同 alert？
- 自動化測試少
- 規範「應該不洩漏 oracle」沒給具體實作 guideline

### 修復

- 大型 vendor patch 各自 server
- IETF 推 **TLS 1.3 砍 RSA key exchange**
- 推 **OAEP padding 取代 PKCS#1 v1.5**（但 TLS 1.2 沒簡單支援 OAEP）

### 教訓

1. **舊 attack 永遠不死**：mitigation 不等於修復
2. **規範要給「**怎麼**」mitigate，而非「**什麼**」mitigate**
3. **自動化 protocol 測試**（test suite）很重要
4. **TLS 1.3 設計者吸取教訓 → 直接砍掉問題 cipher suite**

## TLS Triple Handshake (2014)

Bhargavan / Delignat-Lavaud 等。**Cross-protocol attack**。

### 機制

```
1. attacker 與 server 各跑 TLS 連線
2. 用某 trick 讓 master_secret 在不同連線中重複出現
3. 把一個連線的「客戶證明」轉到另一連線
4. 偽造身分
```

具體：

- session resumption 把舊 master_secret 帶進新連線
- session ID 衝突
- 不同 cipher 的 master_secret derive 不互相獨立

attacker 設計三步握手：a) attacker ↔ legit_server, b) attacker ↔ victim_client (替身), c) 把 step a 的 master 重用到 step b。最終 attacker 與 victim 共享 master，但 victim 以為跟 legit_server 直連。

### 修復

**Extended Master Secret extension** (RFC 7627)：

```
master_secret = PRF(pre_master_secret, "extended master secret",
                    session_hash)   ← 包含 transcript hash
```

把 transcript 綁進 master_secret → 不同 connection 的 transcript 不同 → master 不能跨連線重用。

TLS 1.3 進一步根本解：master 從 handshake 整段 transcript derive，**不可能跨 session 共享**。

### 教訓

1. **Protocol composition 很危險**：不同 protocol / mode 互動產生未預期 attack
2. **state binding 要顯式**：別假設「同 server」就 OK
3. **形式化驗證有用**：Bhargavan 等的 ProVerif / miTLS 模型化發現這個

## 共同教訓

```
1. 規範要嚴格，且實作必符
2. 舊 attack 永遠不死，要定期回測
3. Protocol composition 要小心：不同 layer 互動易出錯
4. 形式化驗證對複雜 protocol 是必要的
5. Memory safety language（Rust）能消滅一整類 bug
6. fuzz testing + Adversarial test 應 standard
7. 部署 Defense-in-depth：一個 layer fail 仍 ok
```

## 對應的工程實踐

```
寫 Protocol 程式：
  ✓ 用 Rust / Go / 記憶體安全語言
  ✓ 用 audited library (BoringSSL, rustls)，不用 OpenSSL legacy 部分
  ✓ 走 IETF 標準，避開自家 protocol
  ✓ 自動化 fuzz testing
  ✓ 形式化驗證（適合大 protocol，如 TLS 1.3 的 miTLS）
  ✓ 預設啟用最強 cipher，舊 cipher 全 disable
```

## 一個常見誤解

「OpenSSL 那麼大用戶基數，bug 少」

**反過來**。OpenSSL 因**復雜（30+ 年累積）+ 缺資源**有大量 bug。Heartbleed、CCS injection、SSLv2 export decrypt、SSL 2.0 Drown、**FREAK**、無數 timing channel。

modern 推薦：

- **BoringSSL**（Google fork，less legacy）
- **rustls**（Rust，記憶體安全）
- **mbedTLS**（嵌入式，相對簡單）
- **wolfSSL**（嵌入式 / FIPS）

OpenSSL 仍是大宗（無人敢全換），但**寫 server 從零的話用 rustls + Tokio**比 OpenSSL 好得多。

## 自我檢核

- [ ] 我能解釋 Heartbleed 的 length check missing
- [ ] 我能描述 Logjam 的 downgrade attack 流程
- [ ] 我能說出 ROBOT 為什麼 19 年後仍有效
- [ ] 我能解釋 Triple Handshake 的 cross-protocol idea
- [ ] 我能列出寫 protocol 程式的 5 條紀律
- [ ] 我能說出 BoringSSL、rustls 等 OpenSSL alternatives

下一個 Part 進攻擊與密碼分析 — Side-channel、const-time、隨機數、cryptanalysis。

→ [Ch 38 Side-channel](./38-side-channel.md)
