# Ch 12 — SSH 與其他應用層速覽

> 目標：認識 SSH 的核心機制（Ch 34 詳細）+ 速覽 SMTP / FTP / WebSocket 等應用層協定。

## SSH（Secure Shell）

**SSH = Telnet + 加密 + 驗證**。port 22。

3 主要功能：

1. **遠端命令執行**：`ssh user@host command`
2. **互動 shell**：`ssh user@host`
3. **檔案傳輸**：`scp` / `sftp`

加 **port forwarding / tunneling** 變超強工具（Ch 34 詳細）。

### SSH 連線握手

簡化版：

```
 client                              server
   │                                   │
   ├── TCP 三次握手 ──────────────────►│
   │                                   │
   │◄── SSH 版本字串 ───────────────────┤   "SSH-2.0-OpenSSH_8.9"
   │                                   │
   ├── SSH 版本字串 ──────────────────►│
   │                                   │
   │ === Key Exchange (Diffie-Hellman) ───
   │                                   │
   │ === Server Authentication =========
   │     (server 公鑰，跟 ~/.ssh/known_hosts 對照)
   │                                   │
   │ === User Authentication ===========
   │     (password / public key / ...)
   │                                   │
   │  = 加密 channel 建立 =            │
```

### Server 公鑰（known_hosts）

第一次連 server：

```
$ ssh user@host
The authenticity of host 'host (1.2.3.4)' can't be established.
ED25519 key fingerprint is SHA256:abc123...
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```

說「yes」→ server 公鑰存到 `~/.ssh/known_hosts`。**之後驗證 server 用這個**。

如果 server 公鑰變了：

```
$ ssh user@host
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
```

可能：

- server 重灌
- 攻擊者 MITM

不確定 → 不要繼續。

### User Authentication

兩種主要方式：

**1. Password**

```
$ ssh user@host
user@host's password:
```

簡單但弱（暴力破解、洩漏）。

**2. Public key（推）**

你生 key pair：

```bash
ssh-keygen -t ed25519
# ~/.ssh/id_ed25519       (private)
# ~/.ssh/id_ed25519.pub   (public)
```

把 public key 放 server 的 `~/.ssh/authorized_keys`：

```bash
ssh-copy-id user@host
```

之後 SSH 連線**不需密碼**：

```bash
ssh user@host    # 直接進
```

**Password 認證在 production 應該關掉**（`/etc/ssh/sshd_config` 設 `PasswordAuthentication no`）。Ch 35 詳細。

### SCP / SFTP

複製檔案：

```bash
# 本機 → 遠端
scp file.txt user@host:/path/

# 遠端 → 本機
scp user@host:/path/file.txt .

# 整個目錄
scp -r dir/ user@host:/path/
```

**現代 SCP 已 deprecated**，改用 `rsync` 或 SFTP：

```bash
rsync -av file.txt user@host:/path/

# SFTP 互動
sftp user@host
sftp> ls
sftp> get file.txt
sftp> put local.txt
```

## SMTP（Simple Mail Transfer Protocol）

**Email 送出去**用的協定。port 25 / 587 / 465。

```
 client                              server
   │                                   │
   ├── TCP 連線 ──────────────────────►│
   │                                   │
   │◄── 220 mail.example.com ESMTP ────┤
   │                                   │
   ├── EHLO myhost.example.com ───────►│
   │                                   │
   │◄── 250-Hello ──────────────────────┤
   │                                   │
   ├── MAIL FROM:<sender@host.com> ────►│
   │                                   │
   │◄── 250 OK ──────────────────────────┤
   │                                   │
   ├── RCPT TO:<recipient@example.com>►│
   │                                   │
   │◄── 250 OK ──────────────────────────┤
   │                                   │
   ├── DATA ──────────────────────────►│
   │   ...email body...                 │
   │   .                                │
   │                                   │
   │◄── 250 OK ──────────────────────────┤
```

**文字協定**，能 telnet 進去手動發 email（debug 用）。

現代 SMTP 加：

- **STARTTLS**：升級到 TLS 加密
- **SPF / DKIM / DMARC**：防偽造寄件人
- **SASL**：認證寄件者

自架 SMTP 超痛 — IP 容易被列黑名單。**99% 場景用 SendGrid / Mailgun / Amazon SES**。

## POP3 / IMAP

收信協定：

| 協定 | 模式 | port |
|---|---|---|
| POP3 | 下載到本機，server 刪掉 | 110 / 995 |
| IMAP | 留 server，本機 sync | 143 / 993 |

現在幾乎全用 IMAP（多裝置 sync）。

## FTP（File Transfer Protocol）

老檔案傳輸協定。port 20 / 21。

**問題**：

- 明文（密碼可被偷）
- 雙連線（control + data）對 firewall 不友善
- NAT 問題

**現代別用 FTP**，改用：

- SFTP（SSH 上的）
- HTTPS file upload
- rsync over SSH
- S3 / 雲端 storage

## WebSocket

讓 HTTP 變雙向長連線。常用於 chat / 即時更新。

握手是個特殊的 HTTP request：

```http
GET /chat HTTP/1.1
Host: example.com
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==
Sec-WebSocket-Version: 13
```

server 回：

```http
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=
```

之後同 TCP 連線變 binary frame 雙向通訊。

替代：

- **HTTP/2 SSE**（Server-Sent Events）
- **Long polling**（老式）
- **gRPC streaming**

## gRPC

Google 的 RPC framework。基於 HTTP/2 + Protobuf。

特性：

- binary protocol（小、快）
- streaming（client / server / 雙向）
- 多語言 SDK
- 內建驗證 / TLS

API 多用 REST + JSON，gRPC 多用於微服務之間。

## QUIC / HTTP/3 應用層協定

QUIC 不只跑 HTTP/3。可以跑：

- DoQ（DNS over QUIC）
- 自定 protocol

QUIC 預期會慢慢取代 TCP 上的 application protocols。

## 一個常見誤解：「SSH 跟 SSL/TLS 同一個東西」

**錯**。SSH 跟 TLS 都做加密 + 驗證，但**用不同協定**：

- SSH：自家加密協定，主要用於 shell / file transfer
- TLS：通用加密層，HTTPS / SMTPS / IMAPS / 自定 protocol 都能用

SSH 不是「TCP 上的 TLS」，是獨立 protocol。

## 一個常見誤解：「FTP 跟 HTTP 一樣現代」

**錯**。FTP 是 1970 年的 protocol，老到沒邊。**所有新東西都該用 SFTP / HTTPS / S3**。

如果你公司還在用 FTP，**那是技術債，趕快遷**。

## 一個常見誤解：「SMTP 自架很簡單」

**錯**。SMTP 協定簡單，但**自架 mail server** 是地獄：

- IP 一定被列「VPS IP」黑名單
- 反向 DNS / SPF / DKIM / DMARC 全要設
- Gmail / Microsoft 的 spam filter 嚴
- 一個 misconfig 你的信全進對方垃圾桶

學習 OK，production 用第三方。

## 動手練習

**1. SSH 你的 VPS**

```bash
ssh root@<VPS-IP>
```

第一次會問 fingerprint，存到 known_hosts。

**2. 設 SSH key**

```bash
ssh-keygen -t ed25519
ssh-copy-id root@<VPS-IP>
ssh root@<VPS-IP>    # 不需密碼
```

**3. 用 telnet 發 SMTP（如果還有 SMTP server 開放 25）**

```bash
telnet smtp.gmail.com 25
# Connected to smtp.gmail.com.
EHLO myclient
QUIT
```

看 protocol 對話。

**4. SCP / rsync 比較**

```bash
# 建個檔
echo "test" > test.txt

# scp
scp test.txt root@<VPS>:/tmp/

# rsync
rsync -av test.txt root@<VPS>:/tmp/
```

rsync 提供詳細進度 + 增量同步。

**5. 用 wscat 玩 WebSocket**

```bash
npm install -g wscat
wscat -c wss://echo.websocket.org
> hello
< hello
```

## 自我檢核

- [ ] SSH 連線握手 + key authentication 機制清楚
- [ ] 知道 known_hosts 怎麼運作
- [ ] SCP / SFTP / rsync 各用過
- [ ] SMTP / POP3 / IMAP 用途分清
- [ ] 知道 WebSocket vs HTTP polling
- [ ] 知道為什麼 FTP 過時、SMTP 自架難

Part 3 結束。練習 A 用 Wireshark 看完整 HTTPS 請求 — 把 DNS / TCP / TLS / HTTP 串一起。

→ [練習 A：用 Wireshark 看完整 HTTPS 請求](./practice-a-https-wireshark.md)
