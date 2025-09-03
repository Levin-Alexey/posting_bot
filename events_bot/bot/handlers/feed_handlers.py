from aiogram import Router, F
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from events_bot.database.services import PostService, LikeService
from events_bot.bot.keyboards.main_keyboard import get_main_keyboard
from events_bot.bot.keyboards.feed_keyboard import (
    get_feed_list_keyboard,
    get_feed_post_keyboard,
    get_liked_list_keyboard,
    get_liked_post_keyboard,
)
from events_bot.storage import file_storage
import logfire
from datetime import timezone
from events_bot.utils import get_clean_category_string

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

router = Router()

POSTS_PER_PAGE = 5  # Показываем по 4-5 постов в списке


def register_feed_handlers(dp: Router):
    """Регистрация обработчиков ленты"""
    dp.include_router(router)


@router.message(F.text == "/feed")
async def cmd_feed(message: Message, db):
    """Обработчик команды /feed"""
    logfire.info(f"Пользователь {message.from_user.id} открывает ленту через команду")
    await show_feed_page_cmd(message, 0, db)


@router.callback_query(F.data == "feed")
async def show_feed_callback(callback: CallbackQuery, db):
    """Показать ленту постов"""
    logfire.info(f"Пользователь {callback.from_user.id} открывает ленту")
    await show_feed_page(callback, 0, db)


@router.callback_query(F.data.startswith("feed_"))
async def handle_feed_navigation(callback: CallbackQuery, db):
    """Обработка навигации по ленте"""
    data = callback.data.split("_")
    action = data[1]
    logfire.info(f"Пользователь {callback.from_user.id} навигация по ленте: {action}")
    try:
        if action in ["prev", "next"]:
            current_page = int(data[2])
            total_pages = int(data[3])
            new_page = (
                max(0, current_page - 1) if action == "prev" else current_page + 1
            )
            await show_feed_page(callback, new_page, db)
        elif action == "open":
            post_id = int(data[2])
            current_page = int(data[3])
            total_pages = int(data[4])
            await show_post_details(callback, post_id, current_page, total_pages, db)
        elif action == "back":
            current_page = int(data[2])
            await show_feed_page(callback, current_page, db)
        elif action == "heart":
            post_id = int(data[2])
            current_page = int(data[3])
            total_pages = int(data[4])
            await handle_post_heart(callback, post_id, db, data)
    except Exception as e:
        logfire.exception("Ошибка навигации по ленте {e}", e=e)
    await callback.answer()


@router.callback_query(F.data == "main_menu")
async def return_to_main_menu(callback: CallbackQuery):
    """Возврат в главное меню"""
    await callback.message.edit_text(
        "Выберите действие:", reply_markup=get_main_keyboard()
    )
    await callback.answer()


async def show_feed_page_cmd(message: Message, page: int, db):
    """Показать страницу ленты через сообщение"""
    logfire.info(f"Пользователь {message.from_user.id} загружает страницу {page} ленты")
    # Получаем посты для ленты
    posts = await PostService.get_feed_posts(
        db, message.from_user.id, POSTS_PER_PAGE, page * POSTS_PER_PAGE
    )
    if not posts:
        logfire.info(f"Пользователь {message.from_user.id} — в ленте нет постов")
        await message.answer(
            "📮 <b>Смотреть подборку</b>\n\n"
                "В подборке пока нет мероприятий по вашим категориям.\n\n"
                "Что можно сделать:\n"
                "• Выбрать другие категории\n"
                "• Создать своё мероприятие\n"
                "• Дождаться появления в подборке новых мероприятий",
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
        )
        return
    # Получаем общее количество постов для пагинации
    total_posts = await PostService.get_feed_posts_count(db, message.from_user.id)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    # Список кратких карточек (предварительно загрузим категории)
    for post in posts:
        await db.refresh(post, attribute_names=["categories"])
    preview_text = format_feed_list(posts, page * POSTS_PER_PAGE + 1, total_posts)
    await message.answer(
        preview_text,
        reply_markup=get_feed_list_keyboard(posts, page, total_pages),
        parse_mode="HTML",
    )


async def show_feed_page(callback: CallbackQuery, page: int, db):
    """Показать страницу ленты"""
    logfire.info(
        f"Пользователь {callback.from_user.id} загружает страницу {page} ленты"
    )
    # Получаем посты для ленты
    posts = await PostService.get_feed_posts(
        db, callback.from_user.id, POSTS_PER_PAGE, page * POSTS_PER_PAGE
    )
    if not posts:
        logfire.info(f"Пользователь {callback.from_user.id} — в ленте нет постов")
        try:
            await callback.message.edit_text(
                "📮 <b>Смотреть подборку</b>\n\n"
                "В подборке пока нет мероприятий по вашим категориям.\n\n"
                "Что можно сделать:\n"
                "• Выбрать другие категории\n"
                "• Создать своё мероприятие\n"
                "• Дождаться появления в подборке новых мероприятий",
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                pass
            else:
                raise
        return
    # Получаем общее количество постов для пагинации
    total_posts = await PostService.get_feed_posts_count(db, callback.from_user.id)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    for post in posts:
        await db.refresh(post, attribute_names=["categories"])
    preview_text = format_feed_list(posts, page * POSTS_PER_PAGE + 1, total_posts)
    try:
        await callback.message.edit_text(
            preview_text,
            reply_markup=get_feed_list_keyboard(posts, page, total_pages),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            # Игнорируем попытку редактировать без изменений
            pass
        else:
            raise


def _msk_str(dt) -> str:
    if not dt:
        return ""
    # event_at теперь хранится в МСК, показываем как есть
    return dt.strftime("%d.%m.%Y %H:%M")


def format_post_for_feed(
    post, current_position: int, total_posts: int, likes_count: int = 0
) -> str:
    """Формат карточки поста (детально)"""
    author_name = "Аноним"
    if hasattr(post, "author") and post.author is not None:
        author_name = (
            getattr(post.author, "first_name", None)
            or getattr(post.author, "username", None)
            or "Аноним"
        )
    # Получаем чистые названия категорий без эмодзи для текста
    category_str = get_clean_category_string(
        post.categories if hasattr(post, "categories") else None
    )
    post_city = getattr(post, "city", "Не указан")
    event_at = getattr(post, "event_at", None)
    event_str = _msk_str(event_at)
    return (
        f"📰 Пост\n\n"
        f"📝 <b>{post.title}</b>\n\n"
        f"{post.content}\n\n"
        f"👤 Автор: {author_name}\n"
        f"🏙️ Город: {post_city}\n"
        f"📂 Категории: {category_str}\n"
        f"📅 Событие: {event_str} (МСК)\n"
        if event_str
        else ""
        f"💖 Сердечек: {likes_count}\n\n"
        f"📊 {current_position} из {total_posts} постов"
    )


def format_feed_list(posts, current_position_start: int, total_posts: int) -> str:
    """Формат списка кратких карточек 4-5 постов (лента)"""
    lines = ["Актуальные мероприятия", ""]
    for idx, post in enumerate(posts, start=current_position_start):
        # Получаем чистые названия категорий без эмодзи
        category_str = get_clean_category_string(post.categories)
        event_at = getattr(post, "event_at", None)
        event_str = _msk_str(event_at)
        lines.append(f"{idx}. <b>{post.title}</b>")
        lines.append(f"   📂 {category_str}")
        lines.append(f"   📅 {event_str}")
        lines.append("")
    lines.append("Нажмите 'Подробнее' под списком")
    return "\n".join(lines)


def format_liked_list(posts, current_position_start: int, total_posts: int) -> str:
    """Формат списка кратких карточек 4-5 постов (избранное)"""
    lines = ["❤️ Ваши избранные мероприятия", ""]
    for idx, post in enumerate(posts, start=current_position_start):
        # Получаем чистые названия категорий без эмодзи
        category_str = get_clean_category_string(post.categories)
        event_at = getattr(post, "event_at", None)
        event_str = _msk_str(event_at)
        lines.append(f"{idx}. <b>{post.title}</b>")
        lines.append(f"   📂 {category_str}")
        lines.append(f"   📅 {event_str}")
        lines.append("")
    lines.append(f"Всего: {total_posts} в избранном")
    lines.append("Нажмите 'Подробнее' под списком")
    return "\n".join(lines)


async def handle_post_heart(callback: CallbackQuery, post_id: int, db, data):
    """Обработка нажатия на сердечко"""
    logfire.info(
        f"Пользователь {callback.from_user.id} нажал на сердечко посту {post_id}"
    )

    try:
        # Переключаем лайк в БД
        result = await LikeService.toggle_like(db, callback.from_user.id, post_id)

        # Формируем сообщение для пользователя
        action_text = "добавлено" if result["action"] == "added" else "удалено"
        likes_count = result["likes_count"]

        response_text = f"Сердечко {action_text}!\n\n"
        response_text += f"💖 Всего сердечек: {likes_count}"

        await callback.answer(response_text, show_alert=True)

        # Обновляем клавиатуру с новым количеством лайков
        is_liked = await LikeService.is_post_liked_by_user(
            db, callback.from_user.id, post_id
        )

        current_page = int(data[3])
        total_pages = int(data[4])
        # Выбираем правильную клавиатуру для текущего раздела (лента или избранное)
        section = data[0]
        if section == "liked":
            new_keyboard = get_liked_post_keyboard(
                current_page=current_page,
                total_pages=total_pages,
                post_id=post_id,
                is_liked=is_liked,
                likes_count=likes_count,
            )
        else:
            new_keyboard = get_feed_post_keyboard(
                current_page=current_page,
                total_pages=total_pages,
                post_id=post_id,
                is_liked=is_liked,
                likes_count=likes_count,
            )
        await callback.message.edit_reply_markup(reply_markup=new_keyboard)

        logfire.info(f"Сердечко посту {post_id} успешно {action_text}")

    except Exception as e:
        logfire.error(f"Ошибка при сохранении сердечка посту {post_id}: {e}")
        await callback.answer("❌ Ошибка при сохранении сердечка", show_alert=True)


async def show_post_details(
    callback: CallbackQuery, post_id: int, current_page: int, total_pages: int, db
):
    post = await PostService.get_post_by_id(db, post_id)
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return
    await db.refresh(post, attribute_names=["author", "categories"])
    is_liked = await LikeService.is_post_liked_by_user(
        db, callback.from_user.id, post.id
    )
    likes_count = await LikeService.get_post_likes_count(db, post.id)
    text = format_post_for_feed(
        post,
        current_page + 1,
        await PostService.get_feed_posts_count(db, callback.from_user.id),
        likes_count,
    )
    post_url = getattr(post, "url", None)
    if post.image_id:
        media_photo = await file_storage.get_media_photo(post.image_id)
        if media_photo:
            try:
                await callback.message.edit_media(
                    media=InputMediaPhoto(
                        media=media_photo.media, caption=text, parse_mode="HTML"
                    ),
                    reply_markup=get_feed_post_keyboard(
                        current_page,
                        total_pages,
                        post.id,
                        is_liked,
                        likes_count,
                        url=post_url,
                    ),
                )
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    raise
            return
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_feed_post_keyboard(
                current_page, total_pages, post.id, is_liked, likes_count, url=post_url
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise


@router.callback_query(F.data == "liked_posts")
async def show_liked(callback: CallbackQuery, db):
    await show_liked_page(callback, 0, db)


@router.callback_query(F.data.startswith("liked_"))
async def handle_liked_navigation(callback: CallbackQuery, db):
    data = callback.data.split("_")
    action = data[1]
    try:
        if action in ["prev", "next"]:
            current_page = int(data[2])
            total_pages = int(data[3])
            new_page = (
                max(0, current_page - 1) if action == "prev" else current_page + 1
            )
            await show_liked_page(callback, new_page, db)
        elif action == "open":
            post_id = int(data[2])
            current_page = int(data[3])
            total_pages = int(data[4])
            await show_liked_post_details(
                callback, post_id, current_page, total_pages, db
            )
        elif action == "back":
            current_page = int(data[2])
            await show_liked_page(callback, current_page, db)
        elif action == "heart":
            post_id = int(data[2])
            current_page = int(data[3])
            total_pages = int(data[4])
            await handle_post_heart(callback, post_id, db, data)
    except Exception as e:
        logfire.exception("Ошибка навигации по избранному {e}", e=e)
    await callback.answer()


async def show_liked_page(callback: CallbackQuery, page: int, db):
    posts = await PostService.get_liked_posts(
        db, callback.from_user.id, POSTS_PER_PAGE, page * POSTS_PER_PAGE
    )
    if not posts:
        await callback.message.edit_text(
           "❤️ <b>Мое избранное</b>\n\n"
            "У вас пока нет избранных мероприятий\n\n"
            "Чтобы добавить:\n"
            "• Выберите событие в подборке\n"
            "• Нажмите на сердце под постом",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        return
    total_posts = await PostService.get_liked_posts_count(db, callback.from_user.id)
    total_pages = (total_posts + POSTS_PER_PAGE - 1) // POSTS_PER_PAGE
    text = format_liked_list(posts, page + 1, total_posts)
    try:
        await callback.message.edit_text(
            text, reply_markup=get_liked_list_keyboard(posts, page, total_pages)
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise


async def show_liked_post_details(
    callback: CallbackQuery, post_id: int, current_page: int, total_pages: int, db
):
    post = await PostService.get_post_by_id(db, post_id)
    if not post:
        await callback.answer("Мероприятие не найдено", show_alert=True)
        return
    await db.refresh(post, attribute_names=["author", "categories"])
    is_liked = await LikeService.is_post_liked_by_user(
        db, callback.from_user.id, post.id
    )
    likes_count = await LikeService.get_post_likes_count(db, post.id)
    text = format_post_for_feed(
        post,
        current_page + 1,
        await PostService.get_liked_posts_count(db, callback.from_user.id),
        likes_count,
    )
    if post.image_id:
        media_photo = await file_storage.get_media_photo(post.image_id)
        if media_photo:
            try:
                await callback.message.edit_media(
                    media=InputMediaPhoto(
                        media=media_photo.media, caption=text, parse_mode="HTML"
                    ),
                    reply_markup=get_liked_post_keyboard(
                        current_page, total_pages, post.id, is_liked, likes_count
                    ),
                )
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    pass
                else:
                    raise
            return
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_liked_post_keyboard(
                current_page, total_pages, post.id, is_liked, likes_count
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise
