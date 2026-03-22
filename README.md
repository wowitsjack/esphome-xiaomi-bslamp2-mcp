# Xiaomi Mi Bedside Lamp 2 - ESPHome + MCP

Custom ESPHome firmware and Claude Code MCP server for the Xiaomi Mi Bedside Lamp 2 (MJCTD02YL). No Home Assistant required. Control your lamp from the command line, a browser, or directly from Claude Code.

## What This Does

- **ESPHome firmware** with 16 buttery smooth 60fps effects running directly on the ESP32 via raw PWM output (no light state machine overhead)
- **MCP server** that lets Claude Code control the lamp, trigger effects, set colors, and run integrations
- **Web UI** at `http://bedside-lamp.local` for browser control
- **REST API** on port 80 for scripting
- **Native ESPHome API** on port 6053 for direct programmatic control
- **Captive Portal** for WiFi reconfiguration without reflashing
- **BLE Improv** for WiFi setup from your phone
- **OTA updates** so you never need to open the lamp again

## On-Chip Effects (60fps, direct PWM)

All effects run natively on the ESP32 at 60fps by writing directly to the LEDC PWM outputs, bypassing ESPHome's light state machine entirely. Every effect respects the brightness slider.

| Effect | Description |
|--------|-------------|
| Rainbow | Smooth hue rotation |
| Rainbow Slow | Dreamy slow cycle |
| Breathe | Sine-wave brightness pulse |
| Candle | Realistic warm flicker |
| Strobe | Hard on/off flash |
| Party | Fast saturated random colors |
| Police | Red/blue alternation |
| Lightning | Random white flashes on dark blue |
| Romantic | Slow pinks and purples |
| Sunrise | 5 min warm-up from dim red to bright white |
| Sunset | 5 min cool-down to off |
| Alert | Red pulse |
| Ocean | Calming blue-green waves |
| Lava | Slow red/orange morph |
| Northern Lights | Aurora borealis greens and purples |

All continuous effects support a global **Effect Speed** control (0.1x to 5.0x).

## MCP Server Tools

The MCP server exposes tools for Claude Code to control the lamp directly:

**Basic control:** `connect`, `disconnect`, `get_status`, `turn_on`, `turn_off`, `set_color`, `set_white`, `set_brightness`, `night_light`

**Effects (network-driven, for when you want parameters):** `effect_rainbow`, `effect_breathe`, `effect_strobe`, `effect_candle`, `effect_color_fade`, `effect_sunrise`, `effect_sunset`, `effect_lightning`, `effect_alert`, `effect_party`, `effect_romantic`, `effect_police`, `effect_sleep_timer`, `effect_focus`, `effect_relax`, `stop_effect`, `list_effects`

**Advanced:** `list_entities`, `call_service`

## Setup

### 1. Flash the firmware

Create `secrets.yaml` from the example:

```bash
cp secrets.yaml.example secrets.yaml
# Edit with your WiFi credentials
```

First flash (requires USB serial adapter connected to UART pads):

```bash
esphome run lamp.yaml
```

All subsequent updates are OTA:

```bash
esphome run lamp.yaml --device bedside-lamp.local
```

### 2. Install the MCP server

```bash
pip install aioesphomeapi mcp
```

Register with Claude Code:

```bash
claude mcp add esphome-lamp \
  --transport stdio \
  -e ESPHOME_HOST=bedside-lamp.local \
  -e ESPHOME_PORT=6053 \
  -- python3 /path/to/server.py
```

### 3. Use it

From Claude Code, just say things like:
- "turn the lamp purple"
- "candle mode"
- "police lights"
- "set brightness to 30%"
- "slow rainbow"
- "Silent Hill vibes"

## Hardware

Based on the [esphome-xiaomi_bslamp2](https://github.com/mmakaay/esphome-xiaomi_bslamp2) component by @mmakaay.

The Xiaomi Mi Bedside Lamp 2 contains:
- ESP32-WROOM-32D (single core, 4MB flash)
- RGBWW LED system (5 PWM channels)
- Capacitive touch front panel (power, color, 10-segment slider)
- 12 individually controllable front panel LEDs

### UART Pinout (for initial flash)

| Pad | Function |
|-----|----------|
| GPIO1 | TX |
| GPIO3 | RX |
| GND | Ground |
| GPIO0 | Hold LOW for flash mode |

Connect GPIO0 to GND before powering on to enter bootloader mode.

## License

MIT
