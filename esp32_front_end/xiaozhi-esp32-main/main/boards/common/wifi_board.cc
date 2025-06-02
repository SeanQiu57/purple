#include "wifi_board.h"

#include "display.h"
#include "application.h"
#include "system_info.h"
#include "font_awesome_symbols.h"
#include "settings.h"
#include "assets/lang_config.h"

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_http.h>
#include <esp_mqtt.h>
#include <esp_udp.h>
#include <tcp_transport.h>
#include <tls_transport.h>
#include <web_socket.h>
#include <esp_log.h>
#include <regex>

#include <wifi_station.h>
#include <wifi_configuration_ap.h>
#include <ssid_manager.h>

static const char *TAG = "WifiBoard";

WifiBoard::WifiBoard()
{
    Settings settings("wifi", true);
    wifi_config_mode_ = settings.GetInt("force_ap") == 1;
    if (wifi_config_mode_)
    {
        ESP_LOGI(TAG, "force_ap is set to 1, reset to 0");
        settings.SetInt("force_ap", 0);
    }
}

std::string WifiBoard::GetBoardType()
{
    return "wifi";
}

void WifiBoard::EnterWifiConfigMode()
{
    auto &application = Application::GetInstance();
    application.SetDeviceState(kDeviceStateWifiConfiguring);

    auto &wifi_ap = WifiConfigurationAp::GetInstance();
    wifi_ap.SetLanguage(Lang::CODE);
    wifi_ap.SetSsidPrefix("Xiaozhi");
    wifi_ap.Start();

    // 显示 WiFi 配置 AP 的 SSID 和 Web 服务器 URL
    std::string hint = Lang::Strings::CONNECT_TO_HOTSPOT;
    hint += wifi_ap.GetSsid();
    hint += Lang::Strings::ACCESS_VIA_BROWSER;
    hint += wifi_ap.GetWebServerUrl();
    hint += "\n\n";

    // 播报配置 WiFi 的提示
    application.Alert(Lang::Strings::WIFI_CONFIG_MODE, hint.c_str(), "", Lang::Sounds::P3_WIFICONFIG);

    // Wait forever until reset after configuration
    while (true)
    {
        int free_sram = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
        int min_free_sram = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
        ESP_LOGI(TAG, "Free internal: %u minimal internal: %u", free_sram, min_free_sram);
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}
#include <regex>

bool WifiBoard::IsCaptivePortalDetected(std::string *redirect_url)
{
    esp_http_client_config_t config = {
        .url = "http://www.baidu.com",
        .timeout_ms = 3000,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_err_t err = esp_http_client_perform(client);

    if (err != ESP_OK)
    {
        ESP_LOGW(TAG, "Captive check failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return false;
    }

    int status = esp_http_client_get_status_code(client);

    // ✅ 手动读取响应 body（你当前框架支持的方法）
    std::string body;
    char buffer[256];
    int total_len = 0;
    int content_length = esp_http_client_get_content_length(client);

    while (true)
    {
        int read_len = esp_http_client_read(client, buffer, sizeof(buffer) - 1);
        if (read_len <= 0)
            break;
        buffer[read_len] = '\0'; // 添加字符串结束符
        body += buffer;
        total_len += read_len;
        if (total_len >= content_length)
            break;
    }

    esp_http_client_cleanup(client);

    ESP_LOGW(TAG, "HTTP status: %d", status);
    if (!body.empty())
    {
        std::string preview = body.substr(0, 512);
        ESP_LOGW(TAG, "HTTP response body (preview):\n%s", preview.c_str());
    }
    else
    {
        ESP_LOGW(TAG, "HTTP response body is empty");
    }

    if (status == 204)
    {
        return false; // 网络畅通无拦截
    }

    std::smatch match;
    std::regex regex_url(R"(https?://[^\s\"']+)");
    if (std::regex_search(body, match, regex_url))
    {
        *redirect_url = match.str(0);
        ESP_LOGW(TAG, "⚠️ Captive Portal login page found in body: %s", redirect_url->c_str());
    }
    else
    {
        *redirect_url = config.url;
        ESP_LOGW(TAG, "⚠️ Captive Portal suspected (status=%d), but no URL found", status);
    }

    return true;
}

void WifiBoard::StartNetwork()
{
    // 若 NVS 中设置了强制进入配网模式
    if (wifi_config_mode_)
    {
        EnterWifiConfigMode();
        return;
    }

    // 如果没有保存的 WiFi，进入配网模式
    auto &ssid_manager = SsidManager::GetInstance();
    auto ssid_list = ssid_manager.GetSsidList();
    if (ssid_list.empty())
    {
        wifi_config_mode_ = true;
        EnterWifiConfigMode();
        return;
    }

    auto &wifi_station = WifiStation::GetInstance();
    wifi_station.OnScanBegin([this]()
                             {
        auto display = Board::GetInstance().GetDisplay();
        display->ShowNotification(Lang::Strings::SCANNING_WIFI, 30000); });
    wifi_station.OnConnect([this](const std::string &ssid)
                           {
        auto display = Board::GetInstance().GetDisplay();
        std::string notification = Lang::Strings::CONNECT_TO;
        notification += ssid;
        notification += "...";
        display->ShowNotification(notification.c_str(), 30000); });
    wifi_station.OnConnected([this](const std::string &ssid)
                             {
        auto display = Board::GetInstance().GetDisplay();
        std::string notification = Lang::Strings::CONNECTED_TO;
        notification += ssid;
        display->ShowNotification(notification.c_str(), 30000); });

    wifi_station.Start();

    // Step 1: 等待连接结果
    if (!wifi_station.WaitForConnected(60 * 1000))
    {
        ESP_LOGW(TAG, "❌ WiFi 连接失败，进入配网模式");
        wifi_station.Stop();
        wifi_config_mode_ = true;
        EnterWifiConfigMode();
        return;
    }

    // Step 2: 已连接 WiFi，检测是否被 Captive Portal 拦截
    std::string redirect_url;
    if (IsCaptivePortalDetected(&redirect_url))
    {
        ESP_LOGW(TAG, "⚠️ 检测到 Captive Portal，认证地址: %s", redirect_url.c_str());

        auto display = Board::GetInstance().GetDisplay();
        display->SetStatus("需要认证");
        display->SetChatMessage("system", redirect_url.c_str());

        // TODO: 后续可以弹出二维码或输入用户名密码界面
        // 可选：进入 WiFi 配网模式
        // wifi_station.Stop();
        // wifi_config_mode_ = true;
        // EnterWifiConfigMode();
        // return;
    }
    else
    {
        ESP_LOGI(TAG, "✅ 未被 Captive Portal 拦截，网络可用");
    }
}

Http *WifiBoard::CreateHttp()
{
    return new EspHttp();
}

WebSocket *WifiBoard::CreateWebSocket()
{
    Settings settings("websocket", false);
    std::string url = settings.GetString("url");
    if (url.find("wss://") == 0)
    {
        return new WebSocket(new TlsTransport());
    }
    else
    {
        return new WebSocket(new TcpTransport());
    }
    return nullptr;
}

Mqtt *WifiBoard::CreateMqtt()
{
    return new EspMqtt();
}

Udp *WifiBoard::CreateUdp()
{
    return new EspUdp();
}

const char *WifiBoard::GetNetworkStateIcon()
{
    if (wifi_config_mode_)
    {
        return FONT_AWESOME_WIFI;
    }
    auto &wifi_station = WifiStation::GetInstance();
    if (!wifi_station.IsConnected())
    {
        return FONT_AWESOME_WIFI_OFF;
    }
    int8_t rssi = wifi_station.GetRssi();
    if (rssi >= -60)
    {
        return FONT_AWESOME_WIFI;
    }
    else if (rssi >= -70)
    {
        return FONT_AWESOME_WIFI_FAIR;
    }
    else
    {
        return FONT_AWESOME_WIFI_WEAK;
    }
}

std::string WifiBoard::GetBoardJson()
{
    // Set the board type for OTA
    auto &wifi_station = WifiStation::GetInstance();
    std::string board_json = std::string("{\"type\":\"" BOARD_TYPE "\",");
    board_json += "\"name\":\"" BOARD_NAME "\",";
    if (!wifi_config_mode_)
    {
        board_json += "\"ssid\":\"" + wifi_station.GetSsid() + "\",";
        board_json += "\"rssi\":" + std::to_string(wifi_station.GetRssi()) + ",";
        board_json += "\"channel\":" + std::to_string(wifi_station.GetChannel()) + ",";
        board_json += "\"ip\":\"" + wifi_station.GetIpAddress() + "\",";
    }
    board_json += "\"mac\":\"" + SystemInfo::GetMacAddress() + "\"}";
    return board_json;
}

void WifiBoard::SetPowerSaveMode(bool enabled)
{
    auto &wifi_station = WifiStation::GetInstance();
    wifi_station.SetPowerSaveMode(enabled);
}

void WifiBoard::ResetWifiConfiguration()
{
    // Set a flag and reboot the device to enter the network configuration mode
    {
        Settings settings("wifi", true);
        settings.SetInt("force_ap", 1);
    }
    GetDisplay()->ShowNotification(Lang::Strings::ENTERING_WIFI_CONFIG_MODE);
    vTaskDelay(pdMS_TO_TICKS(1000));
    // Reboot the device
    esp_restart();
}
