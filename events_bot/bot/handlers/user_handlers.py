from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from events_bot.database.services import UserService, CategoryService, PostService, LikeService
from events_bot.bot.states import UserStates
from events_bot.bot.keyboards import get_main_keyboard, get_category_selection_keyboard, get_city_keyboard
from events_bot.utils import get_clean_category_string
from events_bot.bot.keyboards.notification_keyboard import get_post_notification_keyboard
from events_bot.bot.handlers.feed_handlers import show_liked_page_from_animation, format_liked_list
from events_bot.bot.keyboards.feed_keyboard import get_liked_list_keyboard
from events_bot.bot.handlers.start_handler import show_main_menu, MAIN_MENU_GIF_IDS  # ✅ Импортируем гифки
import logfire
import os
import random  # ✅ Импортируем random

# Гифки
LIKED_GIF_ID = os.getenv("LIKED_GIF_ID")

# Количество постов на странице
POSTS_PER_PAGE = 5  # Можно изменить

router = Router()


@router.callback_query(F.data.startswith("notify_heart_"))
async def handle_notify_heart(callback: CallbackQuery, db):
    """Обработка нажатия на 'В избранное' в уведомлении"""
    try:
        post_id = int(callback.data.split("notify_heart_")[1])
        user_id = callback.from_user.id

        # Переключаем лайк
        result = await LikeService.toggle_like(db, user_id, post_id)
        is_liked = result["action"] == "added"

        # Получаем URL поста
        post = await PostService.get_post_by_id(db, post_id)
        post_url = getattr(post, "url", None)

        # Обновляем клавиатуру
        new_keyboard = get_post_notification_keyboard(
            post_id=post_id,
            is_liked=is_liked,
            url=post_url
        )
        await callback.message.edit_reply_markup(reply_markup=new_keyboard)

        # Ответ пользователю
        action_text = "добавлено" if is_liked else "удалено"
        await callback.answer(f"Избранное {action_text}", show_alert=True)

    except Exception as e:
        await callback.answer("❌ Ошибка при изменении избранного", show_alert=True)


@router.message(F.text == "/delete_user")
async def cmd_delete_user(message: Message, db):
    """Удаление пользователя и всех его данных"""
    user_id = message.from_user.id
    logfire.info(f"Пользователь {user_id} запросил удаление аккаунта")

    # Проверяем, существует ли пользователь
    user = await UserService.register_user(
        db=db,
        telegram_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    if not user:
        await message.answer("❌ Ваш аккаунт уже удалён или не существует.")
        return

    # Удаляем пользователя
    success = await UserService.delete_user(db, user_id)
    if success:
        await message.answer(
            "✅ Ваш аккаунт и все связанные данные (посты, лайки) успешно удалены.\n\n"
            "Если захотите вернуться — просто начните сначала командой /start",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer("❌ Ошибка при удалении аккаунта. Попробуйте позже.")


@router.message(F.text == "/liked_posts")
async def cmd_liked_posts(message: Message, db):
    """Обработчик команды /liked_posts — открытие избранного"""
    logfire.info(f"Пользователь {message.from_user.id} открывает избранное через команду")
    
    # Удаляем команду
    try:
        await message.delete()
    except Exception:
        pass

    # Показываем гифку "загрузка"
    if LIKED_GIF_ID:
        try:
            sent = await message.answer_animation(
                animation=LIKED_GIF_ID,
                caption="❤️ Загружаю избранное...",
                parse_mode="HTML"
            )
            await show_liked_page_from_animation(sent, 0, db, user_id=message.from_user.id)
            return
        except Exception as e:
            logfire.warning(f"Ошибка отправки гифки избранного: {e}")

    # Резервный вариант — без гифки
    await show_liked_page_cmd(message, 0, db, user_id=message.from_user.id)


async def show_liked_page_cmd(message: Message, page: int, db, user_id: int):
    """Показать страницу избранного (через Message)"""
    posts = await PostService.get_liked_posts(db, user_id, POSTS_PER_PAGE, page * POSTS_PER_PAGE)
    if not posts:
        await message.answer(
            "У вас пока нет избранных мероприятий\n\n"
            "Чтобы добавить:\n"
            "• Выберите событие в подборке\n"
            "• Перейдите в подробнее события\n"
            "• Нажмите «В избранное» под постом",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        return

    total_posts = await PostService.get_liked_posts_count(db, user_id)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    start_index = page * POSTS_PER_PAGE + 1
    text = format_liked_list(posts, start_index, total_posts)

    await message.answer(
        text,
        reply_markup=get_liked_list_keyboard(posts, page, total_pages, start_index=start_index),
        parse_mode="HTML"
    )


@router.callback_query(UserStates.waiting_for_categories, F.data == "confirm_categories")
async def confirm_categories(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    selected_ids = data.get("selected_categories", [])

    if not selected_ids:
        await callback.answer("Выберите хотя бы одну категорию!", show_alert=True)
        return

    # Сохраняем категории
    await UserService.select_categories(db, callback.from_user.id, selected_ids)

    try:
        # Удаляем текущее сообщение
        await callback.message.delete()

        # ✅ Шаг 1: Отправляем гифку как photo
        if MAIN_MENU_GIF_IDS:
            selected_gif = random.choice(MAIN_MENU_GIF_IDS)
            sent = await callback.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=selected_gif,
                caption="",  # Пустой caption
                reply_markup=None  # Без клавиатуры
            )
            # ✅ Шаг 2: Через 0.5 сек — редактируем с клавиатурой
            await asyncio.sleep(0.5)
            await sent.edit_reply_markup(reply_markup=get_main_keyboard())
        else:
            # Резерв: текстовое меню
            await callback.message.answer(
                "Выберите действие:",
                reply_markup=get_main_keyboard(),
                input_field_placeholder=""
            )
    except Exception as e:
        logfire.warning(f"Ошибка отправки гифки: {e}")
        await callback.message.answer(
            "Выберите действие:",
            reply_markup=get_main_keyboard(),
            input_field_placeholder=""
        )

    await state.clear()
    await callback.answer()


def register_user_handlers(dp: Router):
    """Регистрация обработчиков пользователя"""
    dp.include_router(router)


@router.message(F.text == "/my_posts")
async def cmd_my_posts(message: Message, db):
    """Обработчик команды /my_posts"""
    posts = await PostService.get_user_posts(db, message.from_user.id)

    if not posts:
        await message.answer(
            "📭 У вас пока нет постов.", reply_markup=get_main_keyboard()
        )
        return

    response = "📊 Ваши посты:\n\n"
    for post in posts:
        await db.refresh(post, attribute_names=["categories"])
        status = "✅ Одобрен" if post.is_approved else "⏳ На модерации"
        category_str = get_clean_category_string(post.categories)
        post_city = getattr(post, "city", "Не указан")
        response += f"📝 {post.title}\n"
        response += f"🏙️ {post_city}\n"
        response += f"📂 {category_str}\n"
        response += f"📅 {post.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        response += f"📊 {status}\n\n"

    await message.answer(response, reply_markup=get_main_keyboard())


@router.message(F.text == "/change_university")
async def cmd_change_city(message: Message, state: FSMContext):
    """Обработчик команды /change_university"""
    await message.answer("Выберите город для получения уведомлений и подборки:", reply_markup=get_city_keyboard())
    await state.set_state(UserStates.waiting_for_city)


@router.message(F.text == "/change_category")
async def cmd_change_category(message: Message, state: FSMContext, db):
    """Обработчик команды /change_category"""
    categories = await CategoryService.get_all_categories(db)
    user_categories = await UserService.get_user_categories(db, message.from_user.id)
    selected_ids = [cat.id for cat in user_categories]

    await message.answer(
        "Выберите категории интересов для получения уведомлений и подборки:",
        reply_markup=get_category_selection_keyboard(categories, selected_ids),
    )
    await state.set_state(UserStates.waiting_for_categories)


@router.message(F.text == "/help")
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = """Справка по Сердцу. Основные функции:

💌 Главное меню - /menu

📮 Смотреть подборку - список актуальных мероприятий по заданным интересам и городу

❤️ Мое избранное - список избранных мероприятий

✏️ Создать мероприятие - публикация собственного мероприятия в Сердце

⭐️ Изменить категории - смена категорий для получения уведомлений и подборки

📍 Изменить город - смена города для получения уведомлений и подборки

Как использовать:

1. Выберите город проживания
2. Выберите категорию для получения уведомлений и подборки
3. Создавайте и продвигайте собственные мероприятия
4. Получайте уведомления о новых мероприятиях по вашему городу и интересам

Создание поста:

• Заголовок: до 100 символов
• Содержание: до 2000 символов
• Мероприятия проходят модерацию перед публикацией

По любым вопросам обращайтесь в поддержку @serdce_help
"""

    await message.answer(help_text, reply_markup=get_main_keyboard())


@router.callback_query(F.data.startswith("city_"))
async def process_city_selection_callback(callback: CallbackQuery, state: FSMContext, db):
    city = callback.data[5:]
    user = await UserService.register_user(
        db=db,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name,
    )
    user.city = city
    await db.commit()

    categories = await CategoryService.get_all_categories(db)

    try:
        # ✅ РЕДАКТИРУЕМ, а не отправляем новое сообщение
        await callback.message.edit_text(
            text=f"📍 Город {city} выбран!\n\nТеперь выберите категории интересов:",
            reply_markup=get_category_selection_keyboard(categories),
            parse_mode="HTML"
        )
    except Exception as e:
        logfire.error(f"Ошибка редактирования: {e}")
        await callback.answer("❌ Ошибка", show_alert=True)
        return

    await state.set_state(UserStates.waiting_for_categories)
    await callback.answer()


@router.callback_query(F.data == "change_city")
async def change_city_callback(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.edit_text(
            "Выберите город:",
            reply_markup=get_city_keyboard()
        )
    except Exception as e:
        logfire.error(f"Ошибка: {e}")
    await state.set_state(UserStates.waiting_for_city)
    await callback.answer()


@router.callback_query(F.data == "change_category")
async def change_category_callback(callback: CallbackQuery, state: FSMContext, db):
    """Изменение категории через инлайн-кнопку"""
    categories = await CategoryService.get_all_categories(db)
    user_categories = await UserService.get_user_categories(db, callback.from_user.id)
    selected_ids = [cat.id for cat in user_categories]

    try:
        await callback.message.delete()
        await callback.message.answer(
            "Выберите категории интересов для кастомизации уведомлений и подборки:",
            reply_markup=get_category_selection_keyboard(categories, selected_ids),
        )
    except Exception as e:
        if "message is not modified" not in str(e):
            raise
    await state.set_state(UserStates.waiting_for_categories)
    await callback.answer()


@router.callback_query(F.data == "my_posts")
async def show_my_posts_callback(callback: CallbackQuery, db):
    """Показать посты пользователя через инлайн-кнопку"""
    posts = await PostService.get_user_posts(db, callback.from_user.id)

    if not posts:
        try:
            await callback.message.delete()
            await callback.message.answer(
                "📭 У вас пока нет постов.", reply_markup=get_main_keyboard()
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                raise
        return

    response = "📊 Ваши посты:\n\n"
    for post in posts:
        await db.refresh(post, attribute_names=["categories"])
        status = "✅ Одобрен" if post.is_approved else "⏳ На модерации"
        category_str = get_clean_category_string(post.categories)
        post_city = getattr(post, "city", "Не указан")
        response += f"📝 {post.title}\n"
        response += f"🏙️ {post_city}\n"
        response += f"📂 {category_str}\n"
        response += f"📅 {post.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        response += f"📊 {status}\n\n"

    try:
        await callback.message.delete()
        await callback.message.answer(response, reply_markup=get_main_keyboard())
    except Exception as e:
        if "message is not modified" not in str(e):
            raise
    await callback.answer()


@router.callback_query(F.data == "help")
async def show_help_callback(callback: CallbackQuery):
    """Показать справку через инлайн-кнопку"""
    help_text = """Справка по Сердцу. Основные функции:

💌 Главное меню - /menu

📮 Смотреть подборку - список актуальных мероприятий по заданным интересам и городу

❤️ Мое избранное - список избранных мероприятий

✏️ Создать мероприятие - публикация собственного мероприятия в Сердце

⭐️ Изменить категории - смена категорий для получения уведомлений и подборки

📍 Изменить город - смена города для получения уведомлений и подборки

Как использовать:

1. Выберите город проживания
2. Выберите категорию для получения уведомлений и подборки
3. Создавайте и продвигайте собственные мероприятия
4. Получайте уведомления о новых мероприятиях по вашему городу и интересам

Создание поста:

• Заголовок: до 100 символов
• Содержание: до 2000 символов
• Мероприятия проходят модерацию перед публикацией

По любым вопросам обращайтесь в поддержку @serdce_help
"""

    try:
        await callback.message.delete()
        await callback.message.answer(help_text, reply_markup=get_main_keyboard())
    except Exception as e:
        if "message is not modified" not in str(e):
            raise
    await callback.answer()
