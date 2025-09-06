from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.fsm.context import FSMContext
from typing import Union
import logfire
from sqlalchemy import select
from events_bot.database.services import PostService, UserService, CategoryService
from events_bot.database.models import Category, Post
from events_bot.database.models import Category
from events_bot.bot.states import PostStates
from events_bot.bot.keyboards import (
    get_main_keyboard,
    get_category_selection_keyboard,
    get_city_keyboard,
)
from events_bot.storage import file_storage
from loguru import logger
from datetime import timezone
import re

router = Router()

# Список запрещённых слов
BANNED_WORDS = [
    "спам", "реклама", "казино", "букмекер", "наркотики", "проститутки",
    "заработок", "деньги", "дешево", "бесплатно", "выигрыш", "лотерея",
    "инвестиции", "криптовалюта", "биткоин", "кредит", "ссуда", "раскрутка",
    "прайс", "цена", "скидка", "акция", "распродажа", "оптовая продажа",
    "мат", "хуй", "блять", "ебать", "сука", "пидор", "гондон", "шлюха"
]

# Подозрительные ссылки
SPAM_LINK_PATTERNS = [
    r"@[\w]+",           # Telegram юзернеймы
    r"t\.me/[\w]+",      # t.me ссылки
    r"telegram\.me/[\w]+", 
    r"wa\.me/[\d]+",     # WhatsApp
    r"whatsapp", "viber", "vk\.com", "vkontakte"
]

def contains_banned_words(text: str) -> bool:
    """Проверяет, есть ли запрещённые слова"""
    text_lower = text.lower()
    return any(word in text_lower for word in BANNED_WORDS)

def contains_spam_links(text: str) -> bool:
    """Проверяет, есть ли подозрительные ссылки"""
    text_lower = text.lower()
    return any(re.search(pattern, text_lower) for pattern in SPAM_LINK_PATTERNS)

def is_suspicious_post(title: str, content: str, url: str | None) -> tuple[bool, str]:
    """Проверяет, подозрителен ли пост"""
    reasons = []
    full_text = f"{title} {content} {url or ''}"
    
    if contains_banned_words(full_text):
        reasons.append("запрещённые слова")
    
    if contains_spam_links(full_text):
        reasons.append("подозрительные ссылки")
    
    return len(reasons) > 0, ", ".join(reasons) if reasons else ""

async def send_post_to_moderation_with_check(bot, post: Post, db):
    """Отправить пост на модерацию с пометкой, если подозрительный"""
    is_suspicious, reason = is_suspicious_post(
        post.title, post.content, getattr(post, "url", None)
    )
    
    moderation_group_id = os.getenv("MODERATION_GROUP_ID")
    if not moderation_group_id:
        logfire.error("MODERATION_GROUP_ID не установлен")
        return

    # Форматируем текст
    moderation_text = ModerationService.format_post_for_moderation(post)
    
    # Добавляем пометку, если подозрительный
    if is_suspicious:
        warning = f"⚠️ <b>ПОДОЗРИТЕЛЬНЫЙ ПОСТ</b>\n"
        warning += f"🔍 Причина: {reason}\n\n"
        moderation_text = warning + moderation_text

    moderation_keyboard = get_moderation_keyboard(post.id)
    
    try:
        if post.image_id:
            media_photo = await file_storage.get_media_photo(post.image_id)
            if media_photo:
                await bot.send_photo(
                    chat_id=moderation_group_id,
                    photo=media_photo.media,
                    caption=moderation_text,
                    reply_markup=moderation_keyboard,
                    parse_mode="HTML",
                )
                return
        
        await bot.send_message(
            chat_id=moderation_group_id,
            text=moderation_text,
            reply_markup=moderation_keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        logfire.error(f"Ошибка отправки поста на модерацию: {e}")
        import traceback
        logfire.error(f"Стек ошибки: {traceback.format_exc()}")

def register_post_handlers(dp: Router):
    """Регистрация обработчиков постов"""
    dp.include_router(router)

@router.message(F.text == "/create_post")
async def cmd_create_post(message: Message, state: FSMContext, db):
    """Обработчик команды /create_post"""
    # Устанавливаем начальное состояние создания поста
    await state.set_state(PostStates.creating_post)

    # Сначала предлагаем выбрать города
    await message.answer(
        "📍 Выберите город для поста:", reply_markup=get_city_keyboard(for_post=True)
    )
    await state.set_state(PostStates.waiting_for_city_selection)

@router.message(F.text == "/cancel")
async def cmd_cancel_post(message: Message, state: FSMContext, db):
    """Отмена создания поста на любом этапе"""
    logfire.info(f"Пользователь {message.from_user.id} отменил создание поста")
    await state.clear()
    await message.answer(
        "Создание мероприятия отменено ✖️", reply_markup=get_main_keyboard()
    )

@router.callback_query(F.data == "create_post")
async def start_create_post(callback: CallbackQuery, state: FSMContext, db):
    """Начать создание поста через инлайн-кнопку"""
    # Устанавливаем начальное состояние создания поста
    await state.set_state(PostStates.creating_post)

    # Сначала предлагаем выбрать города
    await callback.message.edit_text(
        "Выберите город для публикации мероприятия:", reply_markup=get_city_keyboard(for_post=True)
    )
    await state.set_state(PostStates.waiting_for_city_selection)
    await callback.answer()

@router.callback_query(F.data == "cancel_post")
async def cancel_post_creation(callback: CallbackQuery, state: FSMContext, db):
    """Отмена создания поста"""
    await state.clear()
    try:
        if callback.message.text:
            await callback.message.edit_text(
                "Создание мероприятия отменено ✖️", reply_markup=get_main_keyboard()
            )
        elif callback.message.caption:
            await callback.message.edit_caption(
                caption="Создание мероприятия отменено ✖️", reply_markup=get_main_keyboard()
            )
    except Exception as e:
        if "message is not modified" not in str(e):
            raise
    await callback.answer()

@router.callback_query(
    PostStates.waiting_for_city_selection, F.data == "post_city_select_all"
)
async def select_all_cities(callback: CallbackQuery, state: FSMContext, db):
    """Выбрать все города для поста"""
    logfire.info(f"Получен callback post_city_select_all от пользователя {callback.from_user.id}")
    all_cities = [
        "Москва", "Санкт-Петербург"
    ]
    
    # Сохраняем все города
    await state.update_data(selected_cities=all_cities)
    
    # Обновляем клавиатуру
    city_text = ", ".join(all_cities)
    try:
        await callback.message.edit_text(
            f"📍 Выбранные города: {city_text}",
            reply_markup=get_city_keyboard(for_post=True, selected_cities=all_cities)
        )
    except Exception as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise
    await callback.answer("Все города выбраны!")

@router.callback_query(
    PostStates.waiting_for_city_selection, F.data == "post_city_confirm"
)
async def confirm_city_selection(callback: CallbackQuery, state: FSMContext, db):
    """Подтверждение выбора городов для поста"""
    logfire.info(f"Получен callback post_city_confirm от пользователя {callback.from_user.id}")
    data = await state.get_data()
    selected_cities = data.get('selected_cities', [])
    logfire.info(f"Выбранные города: {selected_cities}")
    
    if not selected_cities:
        await callback.answer("Выберите хотя бы один город!")
        return
    
    # Сохраняем выбранные города в формате строки для совместимости
    await state.update_data(post_city=", ".join(selected_cities))
    
    # Получаем все категории для выбора
    all_categories = await CategoryService.get_all_categories(db)
    
    city_text = ", ".join(selected_cities)
    try:
        await callback.message.edit_text(
            f"📍 Города выбраны: {city_text}\n\n⭐️ Теперь выберите категории мероприятия:",
            reply_markup=get_category_selection_keyboard(all_categories, for_post=True),
        )
    except Exception as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise
    await state.set_state(PostStates.waiting_for_category_selection)
    await callback.answer()

@router.callback_query(
    PostStates.waiting_for_city_selection, F.data.startswith("post_city_")
)
async def process_post_city_selection(callback: CallbackQuery, state: FSMContext, db):
    """Обработка выбора города для поста"""
    logfire.info(f"Получен callback {callback.data} от пользователя {callback.from_user.id}")
    city = callback.data[10:]  # Убираем префикс "post_city_"
    
    # Получаем текущие выбранные города из состояния
    data = await state.get_data()
    selected_cities = data.get('selected_cities', [])
    
    # Переключаем состояние города
    if city in selected_cities:
        selected_cities.remove(city)
    else:
        selected_cities.append(city)
    
    # Сохраняем обновленный список городов
    await state.update_data(selected_cities=selected_cities)
    
    # Обновляем клавиатуру
    city_text = ", ".join(selected_cities) if selected_cities else "не выбраны"
    try:
        await callback.message.edit_text(
            f"📍 Выбранные города: {city_text}",
            reply_markup=get_city_keyboard(for_post=True, selected_cities=selected_cities)
        )
    except Exception as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise
    await callback.answer()

@router.callback_query(
    PostStates.waiting_for_category_selection, F.data.startswith("post_category_")
)
async def process_post_category_selection(
    callback: CallbackQuery, state: FSMContext, db
):
    """Мультивыбор категорий для поста"""
    category_id = int(callback.data.split("_")[2])  # post_category_123 -> 123
    data = await state.get_data()
    category_ids = data.get("category_ids", [])

    if category_id in category_ids:
        category_ids.remove(category_id)
    else:
        category_ids.append(category_id)
    await state.update_data(category_ids=category_ids)

    # Получаем все категории для выбора
    all_categories = await CategoryService.get_all_categories(db)
    try:
        await callback.message.edit_text(
            "⭐️ Выберите одну или несколько категорий для мероприятия:",
            reply_markup=get_category_selection_keyboard(
                all_categories, category_ids, for_post=True
            ),
        )
    except Exception as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise
    await callback.answer()

@router.callback_query(
    PostStates.waiting_for_category_selection, F.data == "confirm_post_categories"
)
@logger.catch
async def confirm_post_categories(callback: CallbackQuery, state: FSMContext, db):
    """Подтверждение выбора категорий для поста"""
    data = await state.get_data()
    category_ids = data.get("category_ids", [])
    if not category_ids:
        await callback.answer("Выберите хотя бы одну категорию", show_alert=True)
        return

    # Получаем объекты категорий из базы данных
    stmt = select(Category).where(Category.id.in_(category_ids))
    result = await db.execute(stmt)
    categories = result.scalars().all()

    # Формируем список названий категорий
    if categories:
        category_names = [cat.name for cat in categories]
        category_list = ", ".join(category_names)
    else:
        category_list = "Неизвестные категории"

    # Сохраняем выбранные ID в состояние
    await state.update_data(category_ids=category_ids)
    logfire.info(
        f"Категории подтверждены для пользователя {callback.from_user.id}: {category_names}"
    )

    # Отправляем сообщение с названиями категорий
    try:
        await callback.message.edit_text(
            f"✏️ Создание мероприятия в категориях: {category_list}\n\nВведите заголовок:"
        )
    except Exception as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise

    # Переходим к следующему шагу
    await state.set_state(PostStates.waiting_for_title)
    logfire.info(
        f"Состояние изменено на waiting_for_title для пользователя {callback.from_user.id}"
    )
    await callback.answer()

@router.message(PostStates.waiting_for_title)
@logger.catch
async def process_post_title(message: Message, state: FSMContext, db):
    """Обработка заголовка поста"""
    logfire.info(
        f"Получен заголовок поста от пользователя {message.from_user.id}: {message.text}"
    )

    if len(message.text) > 100:
        await message.answer("✖️ Заголовок слишком длинный. Максимум 100 символов.")
        return

    await state.update_data(title=message.text)
    logfire.info(f"Заголовок сохранен в состоянии: {message.text}")
    await message.answer("Введите описание мероприятия:")
    await state.set_state(PostStates.waiting_for_content)
    logfire.info(
        f"Состояние изменено на waiting_for_content для пользователя {message.from_user.id}"
    )

@router.message(PostStates.waiting_for_content)
async def process_post_content(message: Message, state: FSMContext, db):
    """Обработка содержания поста"""
    if len(message.text) > 2000:
        await message.answer("✖️ Описание слишком длинное. Максимум 2000 символов.")
        return

    await state.update_data(content=message.text)
    await message.answer(
        "🔗 Введите ссылку на сайт / канал / сообщество мероприятия (или отправьте контакты организатора в формате https://).\n\nЭта ссылка будет прикреплена к вашему анонсу:"
    )
    await state.set_state(PostStates.waiting_for_url)

@router.message(PostStates.waiting_for_url)
async def process_post_url(message: Message, state: FSMContext, db):
    """Обработка ссылки для поста"""
    url = None if message.text == "/skip" else message.text.strip()
    if url and not (url.startswith("http://") or url.startswith("https://")):
        await message.answer(
            "❌ Ссылка на должна начинаться с http:// или https://. Попробуйте снова или отправьте /skip."
        )
        return
    await state.update_data(url=url)
    await message.answer(
        "🗓 Введите дату и время события в формате ДД.ММ.ГГГГ ЧЧ:ММ (например, 25.12.2025 18:30)\n\n"
    )
    await state.set_state(PostStates.waiting_for_event_datetime)

@router.message(PostStates.waiting_for_event_datetime)
async def process_event_datetime(message: Message, state: FSMContext, db):
    """Обработка даты/времени события"""
    from datetime import datetime, timezone, timedelta

    try:
        from zoneinfo import ZoneInfo
    except Exception:
        ZoneInfo = None
    text = message.text.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H.%M"):
        try:
            event_dt = datetime.strptime(text, fmt)

            # Проверяем что время не в прошлом (сравниваем в МСК)
            if ZoneInfo is not None:
                msk = ZoneInfo("Europe/Moscow")
                current_msk = datetime.now(msk).replace(tzinfo=None)
            else:
                # Fallback: получаем московское время через UTC
                current_utc = datetime.now(timezone.utc)
                current_msk = current_utc.replace(tzinfo=None) + timedelta(hours=3)

            # Добавляем запас времени (минимум 30 минут в будущем)
            min_future_time = current_msk + timedelta(minutes=30)

            if event_dt <= min_future_time:
                await message.answer(
                    "✖️ Время события должно быть не ранее, чем через 30 минут!\n"
                    f"Сейчас: {current_msk.strftime('%d.%m.%Y %H:%M')} (МСК)\n"
                    f"Минимальное время: {min_future_time.strftime('%d.%m.%Y %H:%M')} (МСК)\n"
                    "Выберите время в будущем."
                )
                return

            # Время остается в МСК (как ввёл пользователь)
            # Сохраняем в ISO (с таймзоной +00:00)
            await state.update_data(event_at=event_dt.isoformat())
            await message.answer(
                "📍 Введите адрес мероприятия:"
            )
            await state.set_state(PostStates.waiting_for_address)
            return
        except ValueError:
            continue
    await message.answer(
        "✖️ Неверный формат. Пример: 25.12.2025 18:30. Попробуйте снова."
    )

@router.message(PostStates.waiting_for_address)
async def process_post_address(message: Message, state: FSMContext, db):
    """Обработка адреса мероприятия"""
    address = message.text.strip()
    if len(address) > 200:
        await message.answer("✖️ Адрес слишком длинный. Максимум 200 символов.")
        return

    await state.update_data(address=address)
    await message.answer(
        "🎆 Отправьте изображение для мероприятия (или нажмите /skip для пропуска):"
    )
    await state.set_state(PostStates.waiting_for_image)

@router.message(F.text.startswith("/delete_post "))
async def delete_post_handler(message: Message, db):
    try:
        post_id = int(message.text.split()[1])
        success = await PostService.delete_post(db, post_id)
        
        if success:
            await message.answer(f"✅ Пост {post_id} успешно удалён")
        else:
            await message.answer(f"❌ Пост {post_id} не найден")
    except (IndexError, ValueError):
        await message.answer("❌ Укажите ID поста. Пример: /delete_post 45")
    except Exception as e:
        logfire.error(f"Ошибка при удалении поста: {e}")
        await message.answer("❌ Ошибка при удалении поста")

@router.message(PostStates.waiting_for_image)
async def process_post_image(message: Message, state: FSMContext, db):
    """Обработка изображения поста"""
    if message.text == "/skip":
        await continue_post_creation(message, state, db)
        return

    if not message.photo:
        await message.answer("✖️ Пожалуйста, отправьте изображение или нажмите /skip")
        return

    # Получаем самое большое изображение
    photo = message.photo[-1]

    # Скачиваем файл
    file_info = await message.bot.get_file(photo.file_id)
    file_data = await message.bot.download_file(file_info.file_path)

    # Сохраняем файл
    file_id = await file_storage.save_file(file_data.read(), "jpg")

    await state.update_data(image_id=file_id)
    await continue_post_creation(message, state, db)

@router.callback_query(PostStates.waiting_for_image, F.data == "skip_image")
async def skip_image_callback(callback: CallbackQuery, state: FSMContext, db):
    await continue_post_creation(callback, state, db)

async def continue_post_creation(
    callback_or_message: Union[Message, CallbackQuery], state: FSMContext, db
):
    """Продолжение создания поста после загрузки изображения"""
    user_id = callback_or_message.from_user.id
    message = (
        callback_or_message
        if isinstance(callback_or_message, Message)
        else callback_or_message.message
    )
    data = await state.get_data()
    title = data.get("title")
    content = data.get("content")
    category_ids = data.get("category_ids", [])
    post_city = data.get("post_city")
    image_id = data.get("image_id")
    event_at_iso = data.get("event_at")
    url = data.get("url")
    address = data.get("address")  # ✅ Новый параметр

    if not all([title, content, category_ids, post_city]):
        await message.answer(
            "✖️ Ошибка: не все данные поста заполнены. Попробуйте создать пост заново.",
            reply_markup=get_main_keyboard(),
        )
        await state.clear()
        return

    # Создаем один пост с несколькими категориями
    parsed_event_at = None
    if event_at_iso:
        try:
            from zoneinfo import ZoneInfo
            parsed_event_at = datetime.fromisoformat(event_at_iso)
            if parsed_event_at.tzinfo is not None:
                parsed_event_at = parsed_event_at.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass

    post = await PostRepository.create_post(
        db, title, content, user_id, category_ids, post_city, image_id, parsed_event_at, url, address
    )

    if post and message.bot:
        await send_post_to_moderation_with_check(message.bot, post, db)
        await message.answer(
            f"Мероприятие отправлено в Сердце и будет автоматически опубликовано после модерации 👏🥳",
            reply_markup=get_main_keyboard(),
        )
    else:
        await message.answer(
            "✖️ Ошибка при создании поста. Попробуйте еще раз.",
            reply_markup=get_main_keyboard(),
        )

    await state.clear()
