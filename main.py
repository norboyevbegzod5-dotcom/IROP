import asyncio
import logging
from datetime import date

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from zoneinfo import ZoneInfo

from bot import db, handlers, scheduler, webapp
from bot.config import ADMIN_CHAT_ID, BOT_TOKEN, TIMEZONE, WEBAPP_PORT, WEBAPP_URL

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("tasks", handlers.tasks_today))
    application.add_handler(CommandHandler("status", handlers.status_admin))
    application.add_handler(CommandHandler("team", handlers.team_admin))
    application.add_handler(CommandHandler("style", handlers.style_admin))
    application.add_handler(CommandHandler("learn", handlers.learn_admin))
    application.add_handler(CommandHandler("knowledge", handlers.knowledge_admin))
    application.add_handler(CommandHandler("forget", handlers.forget_admin))
    application.add_handler(CommandHandler("cancel", handlers.cancel_admin))
    application.add_handler(CallbackQueryHandler(handlers.on_register_callback, pattern=r"^reg:"))
    application.add_handler(CallbackQueryHandler(handlers.on_done_callback, pattern=r"^done:"))
    application.add_handler(CallbackQueryHandler(handlers.on_cancel_callback, pattern=r"^cancel:"))
    application.add_handler(CallbackQueryHandler(handlers.on_accept_callback, pattern=r"^accept:"))
    application.add_handler(CallbackQueryHandler(handlers.on_style_callback, pattern=r"^style:"))
    application.add_handler(CallbackQueryHandler(handlers.on_reopen_callback, pattern=r"^reopen:"))
    application.add_handler(CallbackQueryHandler(handlers.on_sendfix_callback, pattern=r"^sendfix:"))

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

    application = build_application()

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    web_runner = await webapp.start_web_server(application.bot, WEBAPP_PORT)
    logger.info("Mini App web server listening on port %s", WEBAPP_PORT)
    if WEBAPP_URL:
        logger.info("WEBAPP_URL=%s — бот шлёт кнопки открытия мини-аппа.", WEBAPP_URL)
    else:
        logger.info("WEBAPP_URL не задан — бот пока работает в текстовом режиме.")

    try:
        await asyncio.Event().wait()
    finally:
        if web_runner is not None:
            await web_runner.cleanup()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
