#include "wifi_board.h"
#include "audio_codecs/no_audio_codec.h"
#include "zhengchen_lcd_display.h"
#include "system_reset.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "power_save_timer.h"
#include "iot/thing_manager.h"
#include "led/single_led.h"
#include "assets/lang_config.h"
#include "power_manager.h"

#include <esp_log.h>
#include <esp_lcd_panel_vendor.h>
#include <wifi_station.h>

#include <driver/rtc_io.h>
#include <esp_sleep.h>

#define TAG "ZHENGCHEN_1_54TFT_WIFI"

LV_FONT_DECLARE(font_puhui_20_4);
LV_FONT_DECLARE(font_awesome_20_4);
#ifndef LV_SYMBOL_MICROPHONE
#define LV_SYMBOL_MICROPHONE "\xEF\x84\xB0" // fa-microphone
#endif
#ifndef LV_SYMBOL_AUDIO
#define LV_SYMBOL_AUDIO "\xEF\x80\xA1" // fa-volume-up
#endif
#ifndef LV_SYMBOL_BELL
#define LV_SYMBOL_BELL "\xEF\x83\xB3" // fa-bell
#endif

class ZHENGCHEN_1_54TFT_WIFI : public WifiBoard
{
private:
    Button boot_button_;
    Button volume_up_button_;
    Button volume_down_button_;
    ZHENGCHEN_LcdDisplay *display_;
    PowerSaveTimer *power_save_timer_;
    PowerManager *power_manager_;
    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;

    void InitializePowerManager()
    {
        power_manager_ = new PowerManager(GPIO_NUM_9);
        power_manager_->OnTemperatureChanged([this](float chip_temp)
                                             { display_->UpdateHighTempWarning(chip_temp); });

        power_manager_->OnChargingStatusChanged([this](bool is_charging)
                                                {
            if (is_charging) {
                power_save_timer_->SetEnabled(false);
                ESP_LOGI("PowerManager", "Charging started");
            } else {
                power_save_timer_->SetEnabled(true);
                ESP_LOGI("PowerManager", "Charging stopped");
            } });
    }

    void InitializePowerSaveTimer()
    {
        rtc_gpio_init(GPIO_NUM_2);
        rtc_gpio_set_direction(GPIO_NUM_2, RTC_GPIO_MODE_OUTPUT_ONLY);
        rtc_gpio_set_level(GPIO_NUM_2, 1);

        power_save_timer_ = new PowerSaveTimer(-1, 60, 300);
        power_save_timer_->OnEnterSleepMode([this]()
                                            {
            ESP_LOGI(TAG, "Enabling sleep mode");
            display_->SetChatMessage("system", "");
            display_->SetEmotion("sleepy");
            GetBacklight()->SetBrightness(1); });
        power_save_timer_->OnExitSleepMode([this]()
                                           {
            display_->SetChatMessage("system", "");
            display_->SetEmotion("neutral");
            GetBacklight()->RestoreBrightness(); });
        power_save_timer_->SetEnabled(true);
    }

    void InitializeSpi()
    {
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = DISPLAY_SDA;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = DISPLAY_SCL;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }

    void InitializeButtons()
    {
        // --- BOOT 按钮保持不变 -----------------------------------
        boot_button_.OnClick([this]()
                             {
        power_save_timer_->WakeUp();
        auto &app = Application::GetInstance();
        if (app.GetDeviceState() == kDeviceStateStarting && !WifiStation::GetInstance().IsConnected()) {
            ResetWifiConfiguration();
        }
        app.ToggleChatState(); });

        boot_button_.OnDoubleClick([this]()
                                   {
        power_save_timer_->WakeUp();
        auto &app = Application::GetInstance();
        app.SetDeviceState(kDeviceStateWifiConfiguring);
        ResetWifiConfiguration(); });

        // ==========================================================
        // 音量 ↑ : ①【短按】切换自动 / 手动聆听模式 ②【长按】音量 +10
        // ----------------------------------------------------------
        volume_up_button_.OnClick([this]()
                                  {
            power_save_timer_->WakeUp();
            auto &app = Application::GetInstance();

            if (app.GetDeviceState() == kDeviceStateSpeaking)
            {
                ESP_LOGW(TAG, "Ignore mode switch during speaking");
                return;
            }
            ListeningMode new_mode = (app.GetListeningMode() == kListeningModeManualStop)
                                         ? kListeningModeAutoStop
                                         : kListeningModeManualStop;
            /* 若之前是自动且正在监听，先停止再切换 */
            if (app.GetListeningMode() == kListeningModeAutoStop &&
                app.GetDeviceState() == kDeviceStateListening)
            {
                app.StopListening(); // 立即向服务器发送 stop
            }
            app.SetListeningMode(new_mode);
            // --- refresh mode icon -----------------------------------
            static_cast<ZHENGCHEN_LcdDisplay *>(GetDisplay())
                ->UpdateModeText(new_mode); });

        volume_down_button_.OnClick([this]()
                                    {
            power_save_timer_->WakeUp();
            auto& app = Application::GetInstance();
            app.SetDeviceState(kDeviceStateWifiConfiguring);
            ResetWifiConfiguration(); });

        // Volume‑Up  (长按) → +10 volume (逻辑略，保持原实现)
        volume_up_button_.OnLongPress([this]()
                                      {
            power_save_timer_->WakeUp();
            auto codec  = GetAudioCodec();
            int  volume = codec->output_volume() + 10;
            if (volume > 100) volume = 100;
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume / 10)); });

        // =========================
        // Volume‑Down  → 按下说话 / 松开发送
        // =========================
        boot_button_.OnPressDown([this]()
                                 {
            power_save_timer_->WakeUp();
            auto &app = Application::GetInstance();
            if (app.GetListeningMode() == kListeningModeManualStop) {
                app.StartListening(); 
                app.SetDeviceState(kDeviceStateListening);
            } });

        boot_button_.OnPressUp([this]()
                               {
            power_save_timer_->WakeUp();
            auto &app = Application::GetInstance();
            if (app.GetListeningMode() == kListeningModeManualStop) {
                app.StopListening();
            } });

        // Volume‑Down (长按) → –10 volume (逻辑略，保持原实现)
        volume_down_button_.OnLongPress([this]()
                                        {
            power_save_timer_->WakeUp();
            auto codec  = GetAudioCodec();
            int  volume = codec->output_volume() - 10;
            if (volume < 0) volume = 0;
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume / 10)); });
    }

    void InitializeSt7789Display()
    {
        ESP_LOGD(TAG, "Install panel IO");
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = DISPLAY_CS;
        io_config.dc_gpio_num = DISPLAY_DC;
        io_config.spi_mode = 3;
        io_config.pclk_hz = 80 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io_));

        ESP_LOGD(TAG, "Install LCD driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = DISPLAY_RES;
        panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
        panel_config.bits_per_pixel = 16;
        ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io_, &panel_config, &panel_));
        ESP_ERROR_CHECK(esp_lcd_panel_reset(panel_));
        ESP_ERROR_CHECK(esp_lcd_panel_init(panel_));
        ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel_, DISPLAY_SWAP_XY));
        ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel_, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y));
        ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel_, true));

        display_ = new ZHENGCHEN_LcdDisplay(panel_io_, panel_, DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y,
                                            DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY,
                                            {
                                                .text_font = &font_puhui_20_4,
                                                .icon_font = &font_awesome_20_4,
                                                .emoji_font = font_emoji_64_init(),
                                            });
        display_->InitCustomUI();
    }

    void InitializeIot()
    {
        auto &thing_manager = iot::ThingManager::GetInstance();
        thing_manager.AddThing(iot::CreateThing("Speaker"));
        thing_manager.AddThing(iot::CreateThing("Screen"));
        thing_manager.AddThing(iot::CreateThing("Battery"));
    }

public:
    ZHENGCHEN_1_54TFT_WIFI() : boot_button_(BOOT_BUTTON_GPIO),
                               volume_up_button_(VOLUME_UP_BUTTON_GPIO),
                               volume_down_button_(VOLUME_DOWN_BUTTON_GPIO)
    {
        InitializePowerManager();
        InitializePowerSaveTimer();
        InitializeSpi();
        InitializeButtons();
        InitializeSt7789Display();
        InitializeIot();
        GetBacklight()->RestoreBrightness();
    }

    virtual AudioCodec *GetAudioCodec() override
    {
        static NoAudioCodecSimplex audio_codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
                                               AUDIO_I2S_SPK_GPIO_BCLK, AUDIO_I2S_SPK_GPIO_LRCK, AUDIO_I2S_SPK_GPIO_DOUT, AUDIO_I2S_MIC_GPIO_SCK, AUDIO_I2S_MIC_GPIO_WS, AUDIO_I2S_MIC_GPIO_DIN);
        return &audio_codec;
    }

    virtual Display *GetDisplay() override
    {
        return display_;
    }

    virtual Backlight *GetBacklight() override
    {
        static PwmBacklight backlight(DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
        return &backlight;
    }

    virtual bool GetBatteryLevel(int &level, bool &charging, bool &discharging) override
    {
        static bool last_discharging = false;
        charging = power_manager_->IsCharging();
        discharging = power_manager_->IsDischarging();
        if (discharging != last_discharging)
        {
            power_save_timer_->SetEnabled(discharging);
            last_discharging = discharging;
        }
        level = power_manager_->GetBatteryLevel();
        return true;
    }

    virtual bool GetTemperature(float &esp32temp) override
    {
        esp32temp = power_manager_->GetTemperature();
        return true;
    }

    virtual void SetPowerSaveMode(bool enabled) override
    {
        if (!enabled)
        {
            power_save_timer_->WakeUp();
        }
        WifiBoard::SetPowerSaveMode(enabled);
    }
};

DECLARE_BOARD(ZHENGCHEN_1_54TFT_WIFI);
