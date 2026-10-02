import asyncio
import logging
from datetime import date

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from zoneinfo import ZoneInfo

from bot import db, handlers, scheduler, webapp
from bot.config import (
    ADMIN_CHAT_ID,
    BOT_TOKEN,
    TELEGRAM_MODE,
    TIMEZONE,
    WEBAPP_PORT,
    WEBAPP_URL,
    WEBHOOK_SECRET,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO
)
# httpx на INFO пишет полный адрес каждого запроса к Telegram, а в нём токен бота.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _use_webhook() -> bool:
    if TELEGRAM_MODE != "webhook":
        return False
    if not WEBAPP_URL.startswith("https://"):
        # Telegram шлёт вебхуки только на HTTPS — без публичного адреса работаем опросом.
        logger.warning("TELEGRAM_MODE=webhook, но WEBAPP_URL не https — перехожу на polling.")
        return False
    return True


async def _set_webhook(bot):
    await bot.set_webhook(
        url=f"{WEBAPP_URL}{webapp.WEBHOOK_PATH}",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
    )


async def _ensure_webhook(context):
    info = await context.bot.get_webhook_info()
    if info.url != f"{WEBAPP_URL}{webapp.WEBHOOK_PATH}":
        logger.warning("Вебхук был снят (url=%r) — ставлю снова.", info.url)
        await _set_webhook(context.bot)


def build_application(webhook: bool = False) -> Application:
    builder = Application.builder().token(BOT_TOKEN)
    if webhook:
        builder = builder.updater(None)  # обновления приходят в наш веб-сервер, опрос не нужен
    application = builder.build()

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("tasks", handlers.tasks_today))
    application.add_handler(CommandHandler("status", handlers.status_admin))
    application.add_handler(CommandHandler("team", handlers.team_admin))
    application.add_handler(CommandHandler("style", handlers.style_admin))
    application.add_handler(CommandHandler("admin", handlers.admin_panel))
    application.add_handler(CommandHandler("copies", handlers.copies_admin))
    application.add_handler(CommandHandler("learn", handlers.learn_admin))
    application.add_handler(CommandHandler("knowledge", handlers.knowledge_admin))
    application.add_handler(CommandHandler("forget", handlers.forget_admin))
    application.add_handler(CommandHandler("cancel", handlers.cancel_admin))
    application.add_handler(CallbackQueryHandler(handlers.on_register_callback, pattern=r"^reg:"))
    application.add_handler(CallbackQueryHandler(handlers.on_done_callback, pattern=r"^done:"))
    application.add_handler(CallbackQueryHandler(handlers.on_cancel_callback, pattern=r"^cancel:"))
    application.add_handler(CallbackQueryHandler(handlers.on_accept_callback, pattern=r"^accept:"))
    application.add_handler(CallbackQueryHandler(handlers.on_reports_callback, pattern=r"^reports:"))
    application.add_handler(CallbackQueryHandler(handlers.on_style_callback, pattern=r"^style:"))
    application.add_handler(CallbackQueryHandler(handlers.on_reopen_callback, pattern=r"^reopen:"))
    application.add_handler(CallbackQueryHandler(handlers.on_sendfix_callback, pattern=r"^sendfix:"))
    application.add_handler(CallbackQueryHandler(handlers.on_copies_callback, pattern=r"^copies:"))

    if ADMIN_CHAT_ID is not None:
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Chat(chat_id=ADMIN_CHAT_ID),
                handlers.admin_free_text,
            )
        )

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.employee_free_text)
    )
    application.add_handler(MessageHandler(filters.VOICE, handlers.employee_voice))

    tzinfo = ZoneInfo(TIMEZONE)
    scheduler.register_jobs(application.job_queue, tzinfo)
    return application


async def run():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен от @BotFather."
        )

    db.init_db()
    db.generate_instances_for_date(date.today())

    webhook = _use_webhook()
    application = build_application(webhook)

    await application.initialize()
    await application.start()

    web_runner = await webapp.start_web_server(
        application.bot, WEBAPP_PORT, application.update_queue if webhook else None
    )
    logger.info("Mini App web server listening on port %s", WEBAPP_PORT)

    if webhook:
        # Новая версия при деплое сама перенастраивает вебхук на себя. При остановке
        # вебхук НЕ удаляем: старая версия гасится уже после старта новой и иначе
        # отключила бы ей приём сообщений.
        await _set_webhook(application.bot)
        # Локальный запуск с боевым токеном (polling) снимает вебхук — возвращаем его.
        application.job_queue.run_repeating(_ensure_webhook, interval=600, first=600,
                                            name="ensure_webhook")
        logger.info("Telegram: webhook (обновления приходят на WEBAPP_URL).")
    else:
        await application.updater.start_polling()
        logger.info("Telegram: polling.")
    if WEBAPP_URL:
        logger.info("WEBAPP_URL=%s — бот шлёт кнопки открытия мини-аппа.", WEBAPP_URL)
    else:
        logger.info("WEBAPP_URL не задан — бот пока работает в текстовом режиме.")

    try:
        await asyncio.Event().wait()
    finally:
        if web_runner is not None:
            await web_runner.cleanup()
        if application.updater is not None:
            await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
