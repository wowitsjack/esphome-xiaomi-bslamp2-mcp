#!/usr/bin/env python3
"""MCP server for controlling Xiaomi Mi Bedside Lamp 2 via ESPHome native API."""

import asyncio
import colorsys
import json
import math
import os
import logging
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import FastMCP

import aioesphomeapi
from aioesphomeapi import (
    APIClient,
    LightState,
    LightColorCapability,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("esphome-lamp-mcp")

ESPHOME_HOST = os.environ.get("ESPHOME_HOST", "192.168.1.100")
ESPHOME_PORT = int(os.environ.get("ESPHOME_PORT", "6053"))
ESPHOME_PASSWORD = os.environ.get("ESPHOME_PASSWORD", "")
ESPHOME_NOISE_KEY = os.environ.get("ESPHOME_NOISE_KEY", "")


@dataclass
class LampState:
    """Tracks the current state of the lamp and its entities."""

    client: APIClient | None = None
    connected: bool = False
    device_info: Any = None
    entities: list = field(default_factory=list)
    services: list = field(default_factory=list)
    light_key: int | None = None
    light_state: dict = field(default_factory=dict)
    entity_states: dict = field(default_factory=dict)
    _state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _reconnect_task: asyncio.Task | None = None
    _effect_task: asyncio.Task | None = None
    _effect_name: str | None = None


lamp = LampState()


def _build_client() -> APIClient:
    kwargs = {
        "address": ESPHOME_HOST,
        "port": ESPHOME_PORT,
        "password": ESPHOME_PASSWORD,
    }
    if ESPHOME_NOISE_KEY:
        kwargs["noise_psk"] = ESPHOME_NOISE_KEY
    return APIClient(**kwargs)


def _state_callback(state: Any) -> None:
    """Handle state updates from ESPHome."""
    lamp.entity_states[state.key] = state
    if isinstance(state, LightState) and state.key == lamp.light_key:
        lamp.light_state = {
            "on": state.state,
            "brightness": round(state.brightness * 100, 1) if state.brightness else 0,
            "color_mode": state.color_mode,
            "red": round(state.red * 255) if state.red else 0,
            "green": round(state.green * 255) if state.green else 0,
            "blue": round(state.blue * 255) if state.blue else 0,
            "color_temperature": round(state.color_temperature, 0) if state.color_temperature else None,
            "effect": state.effect if state.effect else None,
        }


async def _connect() -> str:
    """Connect to the ESPHome device."""
    if lamp.connected and lamp.client:
        return "Already connected."

    lamp.client = _build_client()
    try:
        await asyncio.wait_for(lamp.client.connect(login=True), timeout=10)
    except Exception as e:
        lamp.connected = False
        return f"Connection failed: {e}"

    lamp.connected = True
    lamp.device_info = await lamp.client.device_info()

    entities, services = await lamp.client.list_entities_services()
    lamp.entities = entities
    lamp.services = services

    # Find the main light entity
    for entity in entities:
        if hasattr(entity, "color_modes") or "light" in type(entity).__name__.lower():
            lamp.light_key = entity.key
            break

    # Subscribe to state changes
    cb = lamp.client.subscribe_states(_state_callback)
    if cb is not None:
        await cb

    name = lamp.device_info.name if lamp.device_info else "unknown"
    model = lamp.device_info.model if lamp.device_info else "unknown"
    return f"Connected to {name} ({model}). Found {len(entities)} entities."


async def _disconnect() -> str:
    """Disconnect from the ESPHome device."""
    if lamp.client:
        await lamp.client.disconnect()
    lamp.connected = False
    lamp.client = None
    lamp.light_key = None
    lamp.light_state = {}
    lamp.entity_states = {}
    return "Disconnected."


async def _ensure_connected() -> str | None:
    """Ensure we're connected, return error string if not."""
    if not lamp.connected or not lamp.client:
        result = await _connect()
        if not lamp.connected:
            return result
    return None


def _stop_effect():
    """Cancel any running effect."""
    if lamp._effect_task and not lamp._effect_task.done():
        lamp._effect_task.cancel()
    lamp._effect_task = None
    lamp._effect_name = None


def _cmd(brightness: float = 1.0, r: float = 1.0, g: float = 1.0, b: float = 1.0,
         transition: float = 0.0, color_temp: float | None = None):
    """Send a light command (fire-and-forget)."""
    kwargs: dict[str, Any] = {
        "key": lamp.light_key,
        "state": True,
        "brightness": brightness,
        "transition_length": transition,
    }
    if color_temp is not None:
        kwargs["color_temperature"] = color_temp
        kwargs["color_mode"] = LightColorCapability.COLOR_TEMPERATURE
    else:
        kwargs["rgb"] = (r, g, b)
        kwargs["color_mode"] = LightColorCapability.RGB
    lamp.client.light_command(**kwargs)


def _hue_to_rgb(hue: float) -> tuple[float, float, float]:
    """Convert hue (0-1) to RGB floats (0-1)."""
    return colorsys.hsv_to_rgb(hue, 1.0, 1.0)


async def _run_effect(name: str, coro):
    """Wrapper to run an effect coroutine with cleanup."""
    lamp._effect_name = name
    try:
        await coro
    except asyncio.CancelledError:
        pass
    finally:
        lamp._effect_name = None


# --- MCP Server ---

mcp = FastMCP(
    "esphome-lamp",
    instructions="Control Xiaomi Mi Bedside Lamp 2 via ESPHome native API",
)


@mcp.tool()
async def connect() -> str:
    """Connect to the ESPHome lamp. Call this first before using other tools."""
    return await _connect()


@mcp.tool()
async def disconnect() -> str:
    """Disconnect from the ESPHome lamp."""
    return await _disconnect()


@mcp.tool()
async def get_status() -> str:
    """Get the current status of the lamp including connection state and light state."""
    if err := await _ensure_connected():
        return err

    info_parts = [f"Connected: {lamp.connected}"]
    if lamp.device_info:
        info_parts.append(f"Device: {lamp.device_info.name}")
        info_parts.append(f"Model: {lamp.device_info.model}")
        info_parts.append(f"ESPHome: {lamp.device_info.esphome_version}")

    if lamp.light_state:
        info_parts.append(f"Light: {'ON' if lamp.light_state.get('on') else 'OFF'}")
        info_parts.append(f"Brightness: {lamp.light_state.get('brightness', 0)}%")
        r = lamp.light_state.get("red", 0)
        g = lamp.light_state.get("green", 0)
        b = lamp.light_state.get("blue", 0)
        info_parts.append(f"RGB: ({r}, {g}, {b})")
        ct = lamp.light_state.get("color_temperature")
        if ct:
            info_parts.append(f"Color temp: {ct} mireds")
        effect = lamp.light_state.get("effect")
        if effect:
            info_parts.append(f"Effect: {effect}")

    return "\n".join(info_parts)


@mcp.tool()
async def turn_on(
    brightness: float | None = None,
    red: float | None = None,
    green: float | None = None,
    blue: float | None = None,
    color_temp: float | None = None,
    transition: float = 1.0,
) -> str:
    """Turn on the lamp.

    Args:
        brightness: Brightness percentage (0-100).
        red: Red value (0-255). Set with green and blue for RGB mode.
        green: Green value (0-255).
        blue: Blue value (0-255).
        color_temp: Color temperature in mireds (153=cool, 588=warm). Overrides RGB.
        transition: Transition duration in seconds (default 1.0).
    """
    if err := await _ensure_connected():
        return err

    if lamp.light_key is None:
        return "No light entity found on device."

    kwargs: dict[str, Any] = {
        "key": lamp.light_key,
        "state": True,
        "transition_length": transition,
    }

    if brightness is not None:
        kwargs["brightness"] = max(0, min(100, brightness)) / 100.0

    if color_temp is not None:
        kwargs["color_temperature"] = color_temp
        kwargs["color_mode"] = LightColorCapability.COLOR_TEMPERATURE
    elif red is not None and green is not None and blue is not None:
        kwargs["rgb"] = (
            max(0, min(255, red)) / 255.0,
            max(0, min(255, green)) / 255.0,
            max(0, min(255, blue)) / 255.0,
        )
        kwargs["color_mode"] = LightColorCapability.RGB

    await lamp.client.light_command(**kwargs)

    parts = ["Lamp turned ON."]
    if brightness is not None:
        parts.append(f"Brightness: {brightness}%")
    if color_temp is not None:
        parts.append(f"Color temp: {color_temp} mireds")
    elif red is not None:
        parts.append(f"RGB: ({red}, {green}, {blue})")
    return " ".join(parts)


@mcp.tool()
async def turn_off(transition: float = 1.0) -> str:
    """Turn off the lamp.

    Args:
        transition: Transition duration in seconds (default 1.0).
    """
    if err := await _ensure_connected():
        return err

    if lamp.light_key is None:
        return "No light entity found on device."

    await lamp.client.light_command(
        key=lamp.light_key,
        state=False,
        transition_length=transition,
    )
    return "Lamp turned OFF."


@mcp.tool()
async def set_color(red: float, green: float, blue: float, brightness: float | None = None, transition: float = 1.0) -> str:
    """Set the lamp to a specific RGB color.

    Args:
        red: Red value (0-255).
        green: Green value (0-255).
        blue: Blue value (0-255).
        brightness: Optional brightness percentage (0-100).
        transition: Transition duration in seconds (default 1.0).
    """
    return await turn_on(brightness=brightness, red=red, green=green, blue=blue, transition=transition)


@mcp.tool()
async def set_white(color_temp: float = 370, brightness: float = 100, transition: float = 1.0) -> str:
    """Set the lamp to white light with a specific color temperature.

    Args:
        color_temp: Color temperature in mireds. 153=cool daylight, 370=neutral, 588=warm candlelight.
        brightness: Brightness percentage (0-100).
        transition: Transition duration in seconds (default 1.0).
    """
    return await turn_on(brightness=brightness, color_temp=color_temp, transition=transition)


@mcp.tool()
async def night_light(transition: float = 1.0) -> str:
    """Turn on the lamp in night light mode (very dim, 1% brightness).

    Args:
        transition: Transition duration in seconds (default 1.0).
    """
    return await turn_on(brightness=1, color_temp=588, transition=transition)


@mcp.tool()
async def set_brightness(brightness: float, transition: float = 1.0) -> str:
    """Set the lamp brightness without changing color.

    Args:
        brightness: Brightness percentage (0-100).
        transition: Transition duration in seconds (default 1.0).
    """
    if err := await _ensure_connected():
        return err

    if lamp.light_key is None:
        return "No light entity found on device."

    await lamp.client.light_command(
        key=lamp.light_key,
        state=True,
        brightness=max(0, min(100, brightness)) / 100.0,
        transition_length=transition,
    )
    return f"Brightness set to {brightness}%."


@mcp.tool()
async def list_entities() -> str:
    """List all entities available on the ESPHome device."""
    if err := await _ensure_connected():
        return err

    lines = []
    for entity in lamp.entities:
        etype = type(entity).__name__.replace("Info", "")
        name = getattr(entity, "name", "unknown")
        key = getattr(entity, "key", "?")
        obj_id = getattr(entity, "object_id", "")
        lines.append(f"[{etype}] {name} (key={key}, id={obj_id})")
    return "\n".join(lines) if lines else "No entities found."


@mcp.tool()
async def call_service(name: str, data: str = "{}") -> str:
    """Call a custom ESPHome service (e.g. activate_preset).

    Args:
        name: Service name as defined in the ESPHome config.
        data: JSON string of service data/arguments.
    """
    if err := await _ensure_connected():
        return err

    try:
        service_data = json.loads(data)
    except json.JSONDecodeError:
        return f"Invalid JSON for service data: {data}"

    target_service = None
    for svc in lamp.services:
        if svc.name == name:
            target_service = svc
            break

    if not target_service:
        available = [s.name for s in lamp.services]
        return f"Service '{name}' not found. Available: {available}"

    await lamp.client.execute_service(target_service, service_data)
    return f"Service '{name}' called with data: {service_data}"


# --- Effects ---


@mcp.tool()
async def stop_effect() -> str:
    """Stop any currently running effect and leave the lamp in its current state."""
    _stop_effect()
    return "Effect stopped."


@mcp.tool()
async def effect_rainbow(speed: float = 2.0, brightness: float = 100) -> str:
    """Smoothly cycle through all rainbow colors continuously.

    Args:
        speed: Seconds per full color cycle (default 2.0, lower = faster).
        brightness: Brightness percentage (0-100).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _rainbow():
        step = 0.0
        while True:
            r, g, b = _hue_to_rgb(step % 1.0)
            _cmd(brightness=brightness / 100, r=r, g=g, b=b, transition=speed / 20)
            step += 0.05
            await asyncio.sleep(speed / 20)

    lamp._effect_task = asyncio.create_task(_run_effect("rainbow", _rainbow()))
    return f"Rainbow cycling at {speed}s per cycle, {brightness}% brightness."


@mcp.tool()
async def effect_breathe(red: float = 255, green: float = 255, blue: float = 255,
                         min_brightness: float = 5, max_brightness: float = 100,
                         period: float = 4.0) -> str:
    """Pulse brightness up and down like breathing.

    Args:
        red: Red value (0-255).
        green: Green value (0-255).
        blue: Blue value (0-255).
        min_brightness: Minimum brightness percentage (default 5).
        max_brightness: Maximum brightness percentage (default 100).
        period: Seconds for one full breathe cycle (default 4.0).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _breathe():
        t = 0.0
        step = period / 40
        while True:
            phase = (math.sin(2 * math.pi * t / period) + 1) / 2
            bri = (min_brightness + phase * (max_brightness - min_brightness)) / 100
            _cmd(brightness=bri, r=red / 255, g=green / 255, b=blue / 255, transition=step)
            t += step
            await asyncio.sleep(step)

    lamp._effect_task = asyncio.create_task(_run_effect("breathe", _breathe()))
    return f"Breathing RGB({red},{green},{blue}), {min_brightness}-{max_brightness}%, {period}s cycle."


@mcp.tool()
async def effect_strobe(red: float = 255, green: float = 255, blue: float = 255,
                        brightness: float = 100, rate: float = 4.0) -> str:
    """Flash the lamp on and off rapidly.

    Args:
        red: Red value (0-255).
        green: Green value (0-255).
        blue: Blue value (0-255).
        brightness: Brightness percentage (0-100).
        rate: Flashes per second (default 4.0).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _strobe():
        on = True
        interval = 1.0 / (rate * 2)
        while True:
            if on:
                _cmd(brightness=brightness / 100, r=red / 255, g=green / 255, b=blue / 255)
            else:
                lamp.client.light_command(key=lamp.light_key, state=False, transition_length=0)
            on = not on
            await asyncio.sleep(interval)

    lamp._effect_task = asyncio.create_task(_run_effect("strobe", _strobe()))
    return f"Strobing RGB({red},{green},{blue}) at {rate} Hz."


@mcp.tool()
async def effect_candle(brightness: float = 60) -> str:
    """Simulate a flickering candle with warm, randomized light.

    Args:
        brightness: Base brightness percentage (default 60).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _candle():
        while True:
            flicker = random.uniform(0.6, 1.0)
            bri = (brightness / 100) * flicker
            r = random.uniform(0.9, 1.0)
            g = random.uniform(0.3, 0.55)
            b = random.uniform(0.0, 0.08)
            _cmd(brightness=bri, r=r, g=g, b=b, transition=random.uniform(0.05, 0.3))
            await asyncio.sleep(random.uniform(0.05, 0.25))

    lamp._effect_task = asyncio.create_task(_run_effect("candle", _candle()))
    return f"Candle flickering at ~{brightness}% brightness."


@mcp.tool()
async def effect_color_fade(colors: str = "[[255,0,0],[0,255,0],[0,0,255]]",
                            step_time: float = 3.0, brightness: float = 100) -> str:
    """Fade smoothly between a list of colors.

    Args:
        colors: JSON array of [R,G,B] arrays, e.g. [[255,0,0],[0,0,255]].
        step_time: Seconds to transition between each color (default 3.0).
        brightness: Brightness percentage (0-100).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    try:
        color_list = json.loads(colors)
    except json.JSONDecodeError:
        return f"Invalid JSON for colors: {colors}"

    if len(color_list) < 2:
        return "Need at least 2 colors."

    async def _fade():
        i = 0
        while True:
            r, g, b = color_list[i % len(color_list)]
            _cmd(brightness=brightness / 100, r=r / 255, g=g / 255, b=b / 255,
                 transition=step_time)
            i += 1
            await asyncio.sleep(step_time)

    lamp._effect_task = asyncio.create_task(_run_effect("color_fade", _fade()))
    return f"Fading through {len(color_list)} colors, {step_time}s each."


@mcp.tool()
async def effect_sunrise(duration: float = 300) -> str:
    """Simulate a sunrise from dim warm red to bright cool white over a duration.

    Args:
        duration: Total sunrise duration in seconds (default 300 = 5 minutes).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _sunrise():
        steps = 60
        step_time = duration / steps
        for i in range(steps + 1):
            progress = i / steps
            if progress < 0.3:
                p = progress / 0.3
                r, g, b = 1.0, p * 0.2, 0.0
                bri = 0.01 + p * 0.15
            elif progress < 0.6:
                p = (progress - 0.3) / 0.3
                r, g, b = 1.0, 0.2 + p * 0.4, p * 0.1
                bri = 0.16 + p * 0.34
            else:
                p = (progress - 0.6) / 0.4
                ct = 588 - p * (588 - 250)
                bri = 0.5 + p * 0.5
                _cmd(brightness=bri, color_temp=ct, transition=step_time)
                await asyncio.sleep(step_time)
                continue
            _cmd(brightness=bri, r=r, g=g, b=b, transition=step_time)
            await asyncio.sleep(step_time)

    lamp._effect_task = asyncio.create_task(_run_effect("sunrise", _sunrise()))
    return f"Sunrise starting over {duration}s."


@mcp.tool()
async def effect_sunset(duration: float = 300) -> str:
    """Simulate a sunset from bright white to dim warm red, then off.

    Args:
        duration: Total sunset duration in seconds (default 300 = 5 minutes).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _sunset():
        steps = 60
        step_time = duration / steps
        for i in range(steps + 1):
            progress = i / steps
            if progress < 0.4:
                p = progress / 0.4
                ct = 250 + p * (588 - 250)
                bri = 1.0 - p * 0.5
                _cmd(brightness=bri, color_temp=ct, transition=step_time)
            elif progress < 0.7:
                p = (progress - 0.4) / 0.3
                r, g, b = 1.0, 0.6 - p * 0.4, 0.1 - p * 0.1
                bri = 0.5 - p * 0.34
                _cmd(brightness=bri, r=r, g=g, b=b, transition=step_time)
            else:
                p = (progress - 0.7) / 0.3
                r, g, b = 1.0, 0.2 - p * 0.2, 0.0
                bri = max(0.01, 0.16 - p * 0.15)
                _cmd(brightness=bri, r=r, g=g, b=b, transition=step_time)
            await asyncio.sleep(step_time)
        lamp.client.light_command(key=lamp.light_key, state=False, transition_length=2.0)

    lamp._effect_task = asyncio.create_task(_run_effect("sunset", _sunset()))
    return f"Sunset starting over {duration}s."


@mcp.tool()
async def effect_lightning(brightness: float = 100, interval: float = 5.0) -> str:
    """Random bright white lightning flashes on a dim blue background.

    Args:
        brightness: Flash brightness percentage (default 100).
        interval: Average seconds between strikes (default 5.0).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _lightning():
        while True:
            _cmd(brightness=0.05, r=0.1, g=0.1, b=0.3, transition=0.5)
            await asyncio.sleep(random.uniform(interval * 0.3, interval * 1.7))
            for _ in range(random.randint(1, 3)):
                _cmd(brightness=brightness / 100, r=1.0, g=1.0, b=1.0)
                await asyncio.sleep(random.uniform(0.03, 0.1))
                _cmd(brightness=0.05, r=0.1, g=0.1, b=0.3)
                await asyncio.sleep(random.uniform(0.05, 0.2))

    lamp._effect_task = asyncio.create_task(_run_effect("lightning", _lightning()))
    return f"Lightning storm with ~{interval}s between strikes."


@mcp.tool()
async def effect_alert(red: float = 255, green: float = 0, blue: float = 0,
                       flashes: int = 10, rate: float = 3.0) -> str:
    """Flash an alert color a set number of times, then stop.

    Args:
        red: Red value (0-255, default 255).
        green: Green value (0-255, default 0).
        blue: Blue value (0-255, default 0).
        flashes: Number of flashes (default 10).
        rate: Flashes per second (default 3.0).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _alert():
        interval = 1.0 / (rate * 2)
        for _ in range(flashes):
            _cmd(brightness=1.0, r=red / 255, g=green / 255, b=blue / 255)
            await asyncio.sleep(interval)
            lamp.client.light_command(key=lamp.light_key, state=False, transition_length=0)
            await asyncio.sleep(interval)

    lamp._effect_task = asyncio.create_task(_run_effect("alert", _alert()))
    return f"Alert: {flashes} flashes of RGB({red},{green},{blue}) at {rate} Hz."


@mcp.tool()
async def effect_party(speed: float = 0.5, brightness: float = 100) -> str:
    """Fast random color changes for party mode.

    Args:
        speed: Seconds between color changes (default 0.5).
        brightness: Brightness percentage (0-100).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _party():
        while True:
            r, g, b = _hue_to_rgb(random.random())
            _cmd(brightness=brightness / 100, r=r, g=g, b=b, transition=speed * 0.3)
            await asyncio.sleep(speed)

    lamp._effect_task = asyncio.create_task(_run_effect("party", _party()))
    return f"Party mode at {speed}s per color, {brightness}% brightness."


@mcp.tool()
async def effect_romantic(brightness: float = 50, speed: float = 5.0) -> str:
    """Slowly cycle through deep reds, pinks, and purples.

    Args:
        brightness: Brightness percentage (default 50).
        speed: Seconds per color transition (default 5.0).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    colors = [
        (1.0, 0.1, 0.2),   # deep red
        (0.9, 0.0, 0.4),   # crimson
        (0.7, 0.0, 0.7),   # purple
        (0.9, 0.2, 0.5),   # pink
        (1.0, 0.05, 0.1),  # scarlet
        (0.6, 0.0, 0.8),   # violet
    ]

    async def _romantic():
        i = 0
        while True:
            r, g, b = colors[i % len(colors)]
            _cmd(brightness=brightness / 100, r=r, g=g, b=b, transition=speed)
            i += 1
            await asyncio.sleep(speed)

    lamp._effect_task = asyncio.create_task(_run_effect("romantic", _romantic()))
    return f"Romantic mode at {brightness}%, {speed}s transitions."


@mcp.tool()
async def effect_focus() -> str:
    """Set cool white light at 100% for focused work."""
    if err := await _ensure_connected():
        return err
    _stop_effect()
    _cmd(brightness=1.0, color_temp=180, transition=1.0)
    return "Focus mode: cool white at 100%."


@mcp.tool()
async def effect_relax() -> str:
    """Set warm dim amber light for relaxation."""
    if err := await _ensure_connected():
        return err
    _stop_effect()
    _cmd(brightness=0.35, color_temp=588, transition=2.0)
    return "Relax mode: warm amber at 35%."


@mcp.tool()
async def effect_sleep_timer(duration: float = 1800) -> str:
    """Gradually dim to off over a duration. Good for falling asleep.

    Args:
        duration: Seconds until lamp turns off (default 1800 = 30 minutes).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _sleep():
        steps = 30
        step_time = duration / steps
        for i in range(steps + 1):
            progress = i / steps
            bri = max(0.01, 1.0 - progress)
            ct = 400 + progress * (588 - 400)
            _cmd(brightness=bri * 0.3, color_temp=ct, transition=step_time)
            await asyncio.sleep(step_time)
        lamp.client.light_command(key=lamp.light_key, state=False, transition_length=3.0)

    lamp._effect_task = asyncio.create_task(_run_effect("sleep_timer", _sleep()))
    mins = duration / 60
    return f"Sleep timer: dimming to off over {mins:.0f} minutes."


@mcp.tool()
async def effect_police(speed: float = 0.15) -> str:
    """Alternate red and blue like police lights.

    Args:
        speed: Seconds per flash (default 0.15).
    """
    if err := await _ensure_connected():
        return err
    _stop_effect()

    async def _police():
        while True:
            _cmd(brightness=1.0, r=1.0, g=0.0, b=0.0)
            await asyncio.sleep(speed)
            _cmd(brightness=1.0, r=0.0, g=0.0, b=1.0)
            await asyncio.sleep(speed)

    lamp._effect_task = asyncio.create_task(_run_effect("police", _police()))
    return f"Police lights at {speed}s per flash."


@mcp.tool()
async def list_effects() -> str:
    """List all available effects and the currently running effect."""
    effects = [
        "rainbow - smooth hue cycling",
        "breathe - pulse brightness up and down",
        "strobe - rapid on/off flashing",
        "candle - warm flickering candlelight",
        "color_fade - fade between custom colors",
        "sunrise - dim warm to bright cool over time",
        "sunset - bright cool to dim warm, then off",
        "lightning - random white flashes on dark blue",
        "alert - flash a color N times then stop",
        "party - fast random colors",
        "romantic - slow reds, pinks, purples",
        "police - alternating red and blue",
        "focus - cool white at 100%",
        "relax - warm amber at 35%",
        "sleep_timer - gradually dim to off",
    ]
    current = lamp._effect_name or "none"
    lines = [f"Current effect: {current}", "", "Available effects:"]
    lines.extend(f"  {e}" for e in effects)
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
