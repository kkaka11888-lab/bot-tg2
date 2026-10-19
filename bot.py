import asyncio
import json
import re
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Твой токен
BOT_TOKEN = "8564988748:AAHdpWtAkkq_pyWsKZHS55j1aUzlyfPt9D4"
MAIN_URL = "https://sibpsa.ru/ISKO/ScheduleView/index?id_Shed=1355&type_shed=4"
API_URL = "https://sibpsa.ru/ISKO/ScheduleView/Calendar"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
CURRENT_SCHEDULE = {}

# --- ЛОГИКА API ЗАПРОСА (БЕЗ БРАУЗЕРА) ---
def get_current_monday():
    """Вычисляет дату понедельника текущей недели в нужном формате"""
    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%dT00:00:00")

async def fetch_schedule_api():
    """Отправляет POST-запрос к скрытому API сайта"""
    async with aiohttp.ClientSession() as session:
        # 1. Сначала заходим на страницу, чтобы сервер выдал нам cookie сессии
        await session.get(MAIN_URL)
        
        # 2. Формируем Payload на основе твоего перехвата
        start_date = get_current_monday()
        payload = {
            "action": "Init",
            "type": "CallBack",
            "header": {
                "startDate": start_date,
                "viewType": "Week",
                "days": 1,
                "cellDuration": 10,
                "cssOnly": True,
                "heightSpec": "BusinessHours",
                "businessBeginsHour": 8,
                "businessEndsHour": 19,
                "timeFormat": "Clock24Hours",
                "locale": "ru-ru"
            }
        }
        
        # DayPilot требует приставку JSON перед телом запроса
        data = "JSON" + json.dumps(payload)
        headers = {
            "Content-Type": "text/plain",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/119.0.0.0"
        }
        
        # 3. Запрашиваем расписание
        async with session.post(API_URL, data=data, headers=headers) as resp:
            text = await resp.text()
            
            # Сервер может отдавать ответ с лишним символом ! в начале
            if text.startswith("!"):
                text = text[1:]
                
            try:
                json_data = json.loads(text)
                return json_data.get("Events", [])
            except Exception as e:
                print("Ошибка парсинга:", e)
                return []

def format_schedule(events_data):
    """Преобразует JSON-события в удобный словарь по дням"""
    schedule = {}
    
    for item in events_data:
        # DayPilot может отдавать ключи с маленькой или большой буквы
        start_raw = item.get("start") or item.get("Start")
        text_raw = item.get("text") or item.get("Text", "Нет данных")
        
        if not start_raw:
            continue
            
        try:
            start_dt = datetime.strptime(start_raw, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
            
        date_str = start_dt.strftime("%d.%m.%Y")
        time_str = start_dt.strftime("%H:%M")
        
        # Очищаем текст от HTML-тегов вроде <span>
        clean_text = BeautifulSoup(text_raw, "html.parser").get_text(separator=" ").strip()
        clean_text = re.sub(r'\s+', ' ', clean_text)
        
        if date_str not in schedule:
            schedule[date_str] = []
            
        schedule[date_str].append({"time": time_str, "text": clean_text})
        
    for date_str in schedule:
        schedule[date_str] = sorted(schedule[date_str], key=lambda x: x["time"])
        
    return schedule

# --- ЛОГИКА БОТА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("⚡ Запрашиваю расписание через серверный API...")
    
    events_data = await fetch_schedule_api()
    global CURRENT_SCHEDULE
    CURRENT_SCHEDULE = format_schedule(events_data)
    
    if not CURRENT_SCHEDULE:
        await message.answer("❌ Расписание не найдено или пар на этой неделе нет.")
        return

    builder = InlineKeyboardBuilder()
    for date, events in CURRENT_SCHEDULE.items():
        btn_text = f"📅 {date}"
        builder.button(text=btn_text, callback_data=f"day_{date}")
        
    builder.button(text="🔍 Посчитать оставшиеся пары", callback_data="count_pairs")
    builder.adjust(1) 
    
    await message.answer("Готово! Выбери день:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("day_"))
async def show_day(callback: types.CallbackQuery):
    date = callback.data.replace("day_", "")
    events = CURRENT_SCHEDULE.get(date, [])
    
    text = f"🎓 **Расписание на {date}:**\n\n"
    for pair in events:
        text += f"⏰ **{pair['time']}**\n📚 {pair['text']}\n\n"
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "count_pairs")
async def ask_discipline(callback: types.CallbackQuery):
    await callback.message.answer("Напиши название дисциплины, чтобы посчитать пары на текущей неделе:")
    await callback.answer()

@dp.message(F.text)
async def handle_text(message: types.Message):
    if not CURRENT_SCHEDULE:
        await message.answer("Сначала загрузи расписание командой /start")
        return
        
    search_query = message.text.lower()
    count = 0
    found_events = []
    
    for date, events in CURRENT_SCHEDULE.items():
        for pair in events:
            if search_query in pair['text'].lower():
                count += 1
                found_events.append(f"• {date} в {pair['time']}")
                
    if count == 0:
        await message.answer(f"По запросу «{message.text}» пар не найдено.")
    else:
        text = f"✅ Найдено пар: **{count}**\n\n" + "\n".join(found_events)
        await message.answer(text, parse_mode="Markdown")

# --- ВЕБ-СЕРВЕР ДЛЯ ОБХОДА «СНА» ОБЛАЧНЫХ ПЛАТФОРМ ---
async def ping_handler(request):
    return web.Response(text="API-версия бота работает безупречно!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', ping_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000) # Render использует порт 10000 по умолчанию
    await site.start()

async def main():
    await start_web_server()
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())