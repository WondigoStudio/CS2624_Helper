"""/weather — current weather in a nicely formatted message.

Data: Open-Meteo (free, no API key). `/weather` shows the default city
(Astana); `/weather <город>` looks any city up by name.
"""

import asyncio
import html
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import logger, requests
from ..db import register_chat

DEFAULT_CITY = {"name": "Астана", "latitude": 51.1801, "longitude": 71.446, "country_code": "KZ"}

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_CACHE_SECONDS = 900
_STALE_SECONDS = 6 * 3600  # many people asking at once shouldn't hammer the API

_geo_cache: dict = {}
_weather_cache: dict = {}

# WMO weather codes -> (emoji, Russian description)
_WMO = {
    0: ("☀️", "Ясно"), 1: ("🌤", "Преимущественно ясно"), 2: ("⛅", "Переменная облачность"),
    3: ("☁️", "Пасмурно"), 45: ("🌫", "Туман"), 48: ("🌫", "Изморозь"),
    51: ("🌦", "Лёгкая морось"), 53: ("🌦", "Морось"), 55: ("🌦", "Сильная морось"),
    56: ("🌧", "Ледяная морось"), 57: ("🌧", "Сильная ледяная морось"),
    61: ("🌧", "Небольшой дождь"), 63: ("🌧", "Дождь"), 65: ("🌧", "Сильный дождь"),
    66: ("🌧", "Ледяной дождь"), 67: ("🌧", "Сильный ледяной дождь"),
    71: ("🌨", "Небольшой снег"), 73: ("🌨", "Снег"), 75: ("❄️", "Сильный снег"),
    77: ("🌨", "Снежная крупа"), 80: ("🌦", "Небольшие ливни"), 81: ("🌧", "Ливни"),
    82: ("⛈", "Сильные ливни"), 85: ("🌨", "Снегопад"), 86: ("❄️", "Сильный снегопад"),
    95: ("⛈", "Гроза"), 96: ("⛈", "Гроза с градом"), 99: ("⛈", "Сильная гроза с градом"),
}
# where the wind comes FROM, by 45° sector, and the arrow showing where it blows TO
_WIND_FROM = ["Северный", "Северо-Восточный", "Восточный", "Юго-Восточный",
              "Южный", "Юго-Западный", "Западный", "Северо-Западный"]
_WIND_ARROW = ["⬇️", "↙️", "⬅️", "↖️", "⬆️", "↗️", "➡️", "↘️"]


def _flag(country_code: str) -> str:
    code = (country_code or "").upper()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code)


def _temp(value) -> str:
    n = round(value)
    return f"{n:+d}°C" if n else "0°C"


def _geocode(city: str):
    """Blocking. {'name', 'latitude', 'longitude', 'country_code'} or None."""
    key = city.strip().lower()
    if key in _geo_cache:
        return _geo_cache[key]
    resp = requests.get(
        _GEOCODE_URL, params={"name": city, "count": 1, "language": "ru", "format": "json"}, timeout=15
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    found = None
    if results:
        r = results[0]
        found = {"name": r.get("name", city), "latitude": r["latitude"], "longitude": r["longitude"],
                 "country_code": r.get("country_code", "")}
    _geo_cache[key] = found
    return found


def _fetch(place: dict) -> dict:
    """Blocking. Raw Open-Meteo response (cached for a few minutes)."""
    key = (round(place["latitude"], 2), round(place["longitude"], 2))
    hit = _weather_cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    params = {
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,"
                   "cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,visibility",
        "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset",
        "wind_speed_unit": "ms", "timezone": "auto", "forecast_days": 1,
    }
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(_FORECAST_URL, params=params, timeout=15)
            if resp.status_code in (429, 502, 503, 504):
                raise RuntimeError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()
            _weather_cache[key] = (time.time(), data)
            return data
        except Exception as e:  # shared hosting IPs often hit the free rate limit
            last_err = e
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    if hit and time.time() - hit[0] < _STALE_SECONDS:
        return hit[1]  # better slightly old weather than none
    raise last_err


def format_weather(place: dict, data: dict) -> str:
    cur, daily = data["current"], data.get("daily", {})
    emoji, desc = _WMO.get(cur.get("weather_code"), ("🌡", "—"))
    lines = [
        f"{emoji} <b>Погода: {html.escape(place['name'])}</b> {_flag(place.get('country_code'))}".rstrip(),
        f"<i>{desc}</i>",
        "──────────────────",
        f"🌡 <b>Температура:</b> {_temp(cur['temperature_2m'])} "
        f"<i>(ощущается как {_temp(cur['apparent_temperature'])})</i>",
    ]
    try:
        lines.append(
            f"📊 <b>Мин / Макс:</b> {_temp(daily['temperature_2m_min'][0])} / {_temp(daily['temperature_2m_max'][0])}"
        )
    except (KeyError, IndexError, TypeError):
        pass
    if cur.get("relative_humidity_2m") is not None:
        lines.append(f"💧 <b>Влажность:</b> {round(cur['relative_humidity_2m'])}%")
    if cur.get("wind_speed_10m") is not None:
        deg = cur.get("wind_direction_10m") or 0
        sector = round(deg / 45) % 8
        lines.append(
            f"💨 <b>Ветер:</b> {cur['wind_speed_10m']:.1f} м/с, {_WIND_FROM[sector]} {_WIND_ARROW[sector]}"
        )
    if cur.get("pressure_msl") is not None:
        lines.append(
            f"🧭 <b>Давление:</b> {round(cur['pressure_msl'] * 0.750062)} мм рт. ст. "
            f"<i>({round(cur['pressure_msl'])} гПа)</i>"
        )
    if cur.get("cloud_cover") is not None:
        lines.append(f"☁️ <b>Облачность:</b> {round(cur['cloud_cover'])}%")
    if cur.get("visibility") is not None:
        lines.append(f"👁 <b>Видимость:</b> {cur['visibility'] / 1000:.1f} км")
    try:
        sunrise, sunset = daily["sunrise"][0][-5:], daily["sunset"][0][-5:]
        lines += ["", f"🌅 <b>Восход:</b> {sunrise}   🌇 <b>Закат:</b> {sunset}"]
    except (KeyError, IndexError, TypeError):
        pass
    lines += ["──────────────────", f"🕐 <i>Местное время: {cur['time'][-5:]}</i>"]
    return "\n".join(lines)


async def weather_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    query = " ".join(context.args).strip() if context.args else ""
    status = await update.message.reply_text("🌤 Смотрю погоду…")
    try:
        place = DEFAULT_CITY
        if query:
            place = await asyncio.to_thread(_geocode, query)
            if place is None:
                await status.edit_text(f"Не нашёл город «{query}». Попробуй написать иначе: /weather Алматы")
                return
        data = await asyncio.to_thread(_fetch, place)
        await status.edit_text(format_weather(place, data), parse_mode=ParseMode.HTML)
    except requests.exceptions.RequestException as e:
        logger.warning("Weather request failed: %s", e)
        await status.edit_text("Сервис погоды сейчас недоступен — попробуй чуть позже.")
    except Exception as e:
        logger.warning("Weather failed: %s", e)
        await status.edit_text("Не получилось получить погоду.")


async def morning_weather_text():
    """Weather for the default city as HTML for the morning message, or None
    if the service is unavailable (the morning message just goes without it)."""
    try:
        data = await asyncio.to_thread(_fetch, DEFAULT_CITY)
        return format_weather(DEFAULT_CITY, data)
    except Exception as e:
        logger.warning("Morning weather failed: %s", e)
        return None
