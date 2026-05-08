# Ch 21 — Zigbee 原理與實作

> 目標：理解 Zigbee 的協議堆疊架構、IEEE 802.15.4 物理層和 MAC 層機制、設備角色、Mesh 路由、ZCL Cluster 模型，並用 ESP-Zigbee-SDK 實作 End Device 加入網路與 OnOff cluster 控制。

---

## 硬體需求：先看這裡

Classic ESP32 沒有 IEEE 802.15.4 無線電，無法跑 Zigbee。你需要：

| 晶片 | 802.15.4 | 是否支援 Zigbee | 備註 |
|------|---------|----------------|------|
| ESP32（classic） | 無 | 不支援 | 只有 WiFi + BLE |
| ESP32-S3 | 無 | 不支援 | 只有 WiFi + BLE |
| ESP32-H2 | 原生 | 支援 | 只有 802.15.4，無 WiFi |
| ESP32-C6 | 原生 | 支援 | WiFi 6 + BLE + 802.15.4 |

建議買 ESP32-H2 DevKitC，只跑 Zigbee 最單純。兩塊一個當 Coordinator，一個當 End Device。

---

## Zigbee 協議堆疊架構

```
+---------------------------+
|       Application         |  <-- 使用者應用程式
+---------------------------+
|   ZDO（Zigbee Device     |  <-- 設備物件：網路加入、服務發現
|   Object）                |
+---------------------------+
|   ZCL（Zigbee Cluster    |  <-- Cluster Library：標準化功能定義
|   Library）               |
+---------------------------+
|   APS（Application       |  <-- 應用支援子層：端對端尋址、分段、安全
|   Support Sub-layer）     |
+---------------------------+
|   NWK（Network Layer）    |  <-- 網路層：Mesh 路由、網路形成
+---------------------------+
|   IEEE 802.15.4 MAC       |  <-- MAC：CSMA/CA、ACK、關聯
+---------------------------+
|   IEEE 802.15.4 PHY       |  <-- 物理層：2.4GHz、OQPSK、250kbps
+---------------------------+
```

IEEE 802.15.4 只定義了最下面兩層，上面的 NWK 到 ZCL 都是 Zigbee 規範。

---

## IEEE 802.15.4 物理層與 MAC 層

### PHY 基本規格

| 參數 | 規格 |
|------|------|
| 頻段 | 2.4 GHz ISM |
| 通道 | 11~26，共 16 個，每個間隔 5 MHz |
| 調變 | O-QPSK（Offset Quadrature Phase Shift Keying） |
| 速率 | 250 kbps |
| 典型範圍 | 室內 10~20m，室外 100m |

### MAC 層機制

CSMA/CA（Carrier Sense Multiple Access with Collision Avoidance，載波偵測多重存取/碰撞避免）：傳送前先聽信道，信道忙則退讓一段隨機時間再重試。和 WiFi 的 CSMA/CA 原理相同，但更省電。

ACK 機制：每個 Data frame 傳出後，接收方回 ACK frame（3 bytes），沒收到 ACK 在設定的重試次數內重傳。

---

## 設備角色

Zigbee 網路有三種角色，一個網路只有一個 Coordinator：

| 角色 | 縮寫 | 功能 | 是否常開 | 典型硬體 |
|------|------|------|---------|---------|
| Coordinator（協調器） | ZC | 建立網路、分配 PAN ID、管理路由表 | 必須 | 插電裝置 |
| Router（路由器） | ZR | 轉發封包、擴大覆蓋範圍 | 必須 | 插電裝置 |
| End Device（終端設備） | ZED | 只傳送/接收自己的資料，不轉發 | 可休眠 | 電池裝置 |

End Device 的父節點（Parent）負責在 End Device 休眠時替它緩存訊息，End Device 醒來後 poll 父節點取資料。這是 Zigbee 低功耗的核心機制。

---

## Mesh 路由：AODV

Zigbee NWK 層用 AODV（Ad hoc On-demand Distance Vector）協議做路由：

```
Route Discovery 流程：

Source Node
  |
  v  廣播 RREQ（Route Request）
  |
  +-----> Intermediate Router A ----+
  |                                  |
  +-----> Intermediate Router B      v  RREQ 傳播
                                    |
                                    v
                               Destination Node
                                    |
                                    v  回送 RREP（Route Reply）沿原路返回
                                    |
                               Source Node 收到 RREP，建立路由表項目
```

路由表項目記錄「要到達 X，下一跳送給 Y」。後續封包直接查表轉發，不需要再 discover。路由失效時觸發 RERR（Route Error）重新 discover。

---

## ZCL（Zigbee Cluster Library）

ZCL 是 Zigbee 的標準功能定義庫，用 Cluster 為單位組織功能：

```
Endpoint（端點）
  |
  +-- Cluster（功能集合）
        |
        +-- Attribute（屬性，有 ID、資料型別、讀/寫/報告權限）
        +-- Command（命令，如 On/Off/Toggle）
```

一個設備可以有多個 Endpoint，每個 Endpoint 可以包含多個 Cluster。標準 Cluster 有 ID，例如：

| Cluster | ID | 功能 |
|---------|----|------|
| Basic | 0x0000 | 裝置描述資訊（廠商、型號等） |
| OnOff | 0x0006 | 開關控制（On/Off/Toggle） |
| Level Control | 0x0008 | 亮度調節 |
| Temperature Measurement | 0x0402 | 溫度感測器 |
| Occupancy Sensing | 0x0406 | 人體偵測 |

自訂 Cluster ID 範圍 0xFC00~0xFFFF（廠商自定義）。

---

## ESP-Zigbee-SDK 實作

IEEE 802.15.4 MAC 層非常複雜，包含 beacon 管理、superframe 結構、GTS 分配等，手刻的效益遠低於成本。業界和 Espressif 官方都直接用 ESP-Zigbee-SDK（基於 ZBOSS stack），這是正確選擇。

### 專案設定

```
idf.py set-target esp32h2
```

`idf.py menuconfig` 確認 Zigbee 相關：

```
Component config → ESP Zigbee
  [*] Enable Zigbee stack
```

### End Device 完整範例

```c
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_zigbee_core.h"
#include "ha/esp_zigbee_ha_standard.h"

#define TAG "ZB_ED"

/* OnOff attribute 初始值 */
static uint8_t onoff_attr = 0;

/* ZCL attribute 讀寫回調 */
static esp_err_t zb_attribute_handler(const esp_zb_zcl_set_attr_value_message_t *msg)
{
    if (msg->info.cluster == ESP_ZB_ZCL_CLUSTER_ID_ON_OFF) {
        onoff_attr = *(uint8_t *)msg->attribute.data.value;
        ESP_LOGI(TAG, "OnOff attribute: %s", onoff_attr ? "ON" : "OFF");
        /* 這裡控制 GPIO 等實際硬體 */
    }
    return ESP_OK;
}

/* Zigbee 事件回調 */
static void esp_zb_app_signal_handler(uint8_t bufid)
{
    uint32_t *p_sg_p = esp_zb_app_signal_get(bufid, NULL);
    esp_zb_app_signal_type_t sig_type = *p_sg_p;

    switch (sig_type) {
    case ESP_ZB_ZDO_SIGNAL_SKIP_STARTUP:
        /* Stack 準備好，開始入網流程 */
        ESP_LOGI(TAG, "Stack ready, starting steering...");
        esp_zb_bdb_start_top_level_commissioning(ESP_ZB_BDB_MODE_NETWORK_STEERING);
        break;

    case ESP_ZB_BDB_SIGNAL_STEERING:
        /* 網路加入結果 */
        if (esp_zb_bdb_signal_get_params(sig_type) == ESP_OK) {
            esp_zb_ieee_addr_t ex_pan_id;
            esp_zb_get_extended_pan_id(ex_pan_id);
            ESP_LOGI(TAG, "Joined network! PAN ID: 0x%04x, Channel: %d",
                     esp_zb_get_pan_id(), esp_zb_get_current_channel());
        } else {
            /* 找不到網路，60 秒後重試 */
            ESP_LOGW(TAG, "Steering failed, retry in 60s...");
            esp_zb_scheduler_alarm(
                (esp_zb_callback_t)esp_zb_bdb_start_top_level_commissioning,
                ESP_ZB_BDB_MODE_NETWORK_STEERING, 60000);
        }
        break;

    default:
        break;
    }
    esp_zb_free_buf(bufid);
}

/* Zigbee 任務：在這裡初始化 stack 並啟動 */
static void esp_zb_task(void *pvParameters)
{
    /* 1. 設定 Zigbee End Device 角色 */
    esp_zb_cfg_t zb_cfg = {
        .esp_zb_role = ESP_ZB_DEVICE_TYPE_ED,          /* End Device */
        .install_code_policy = INSTALLCODE_POLICY_DISABLE,
        .nwk_cfg.zed_cfg = {
            .ed_timeout = ESP_ZB_ED_AGING_TIMEOUT_64MIN,
            .keep_alive = 3000,   /* ms，每 3 秒 poll 父節點 */
        },
    };
    esp_zb_init(&zb_cfg);

    /* 2. 建立 Endpoint 和 Cluster 列表 */
    esp_zb_ep_list_t *ep_list = esp_zb_ep_list_create();

    /* 建立 HA On/Off Light endpoint（Endpoint ID = 10） */
    esp_zb_on_off_light_cfg_t light_cfg = ESP_ZB_DEFAULT_ON_OFF_LIGHT_CONFIG();
    esp_zb_endpoint_config_t ep_cfg = {
        .endpoint    = 10,
        .app_profile_id = ESP_ZB_AF_HA_PROFILE_ID,
        .app_device_id  = ESP_ZB_HA_ON_OFF_LIGHT_DEVICE_ID,
        .app_device_version = 0,
    };
    esp_zb_ep_list_add_ep(ep_list,
        esp_zb_on_off_light_clusters_create(&light_cfg),
        ep_cfg);

    /* 3. 登錄設備 */
    esp_zb_device_register(ep_list);

    /* 4. 設定 attribute 更新回調 */
    esp_zb_core_action_handler_register(zb_attribute_handler);

    /* 5. 啟動 stack，連接到 Coordinator */
    ESP_ERROR_CHECK(esp_zb_start(false));

    /* 6. 進入主事件迴圈（阻塞） */
    esp_zb_stack_main_loop();
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /* Zigbee 任務需要較大 stack（ZBOSS stack 本身需要） */
    xTaskCreate(esp_zb_task, "Zigbee_main", 4096, NULL, 5, NULL);
}
```

### Coordinator 端（另一塊板子）

Coordinator 的初始化結構類似，角色換成 `ESP_ZB_DEVICE_TYPE_COORDINATOR`，啟動時先建立網路：

```c
/* Coordinator 設定 */
esp_zb_cfg_t zb_cfg = {
    .esp_zb_role = ESP_ZB_DEVICE_TYPE_COORDINATOR,
    .install_code_policy = INSTALLCODE_POLICY_DISABLE,
    .nwk_cfg.zczr_cfg = {
        .max_children = 10,
    },
};
esp_zb_init(&zb_cfg);

/* 在 SKIP_STARTUP 信號中：建立網路 */
/* esp_zb_bdb_start_top_level_commissioning(ESP_ZB_BDB_MODE_NETWORK_FORMATION) */
/* 成功後開啟允許加入視窗（STEERING） */
```

---

## 兩節點測試流程

1. 先燒錄 Coordinator 並上電，等待 `Network steering started` log。
2. 燒錄 End Device 並上電，等待 `Joined network!` log。
3. 用 Coordinator 端的應用（或 Zigbee2MQTT 搭配 USB adapter）發送 OnOff cluster 的 Toggle command 給 End Device Endpoint 10。
4. End Device 序列埠應顯示 `OnOff attribute: ON` / `OFF` 交替。

官方範例的最快路徑：用 ESP-IDF 的 `examples/zigbee/light_bulb` 和 `light_switch` 直接改，架構和上面的 code 一致。

---

## 為什麼不 Register-Level

IEEE 802.15.4 MAC 層的複雜度遠超 SPI 或 UART。光是 beacon 管理、superframe 結構、GTS（Guaranteed Time Slots）分配、非信標模式的 CSMA/CA 退讓算法，就需要數千行 state machine。再加上 Zigbee NWK 的路由協議、APS 的安全層（AES-CCM* 加密），自己從暫存器打不是「學底層」，是「重複造輪子且容易出 bug」。這一層的正確作法就是用 ZBOSS stack，和用 lwIP 寫 TCP/IP 而不是自己實作 TCP 的道理一樣。

---

## 自我檢核

- [ ] 知道 Classic ESP32 不支援 Zigbee，需要 ESP32-H2 或 C6
- [ ] 能說明 Zigbee 協議堆疊各層的職責
- [ ] 知道 IEEE 802.15.4 的頻段、速率、CSMA/CA 機制
- [ ] 能區分 Coordinator、Router、End Device 三個角色
- [ ] 知道 ZCL Cluster 是什麼，OnOff Cluster 有哪些 Command
- [ ] 能說明 AODV 路由發現的基本流程
- [ ] 能解釋 End Device 的 keep-alive 和 poll 機制

LoRa 和 Zigbee 都是低功耗無線協議，但都不是 USB。下一章進入完全不同的領域：USB 的主從架構和 enumeration 流程。

→ [練習 C：BLE + LoRa 橋接](./practice-c-ble-lora-bridge.md)
