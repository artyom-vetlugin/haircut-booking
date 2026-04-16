from __future__ import annotations

import logging

from telegram.ext import Application, ApplicationBuilder

from app.core.config import settings

logger = logging.getLogger(__name__)


class TelegramBotClient:
    def __init__(self) -> None:
        self._application: Application | None = None  # type: ignore[type-arg]

    @property
    def application(self) -> Application:  # type: ignore[type-arg]
        if self._application is None:
            raise RuntimeError("TelegramBotClient is not initialized. Call initialize() first.")
        return self._application

    async def initialize(self) -> None:
        app = ApplicationBuilder().token(settings.telegram_bot_token).build()
        self._register_handlers(app)
        await app.initialize()
        self._application = app
        logger.info("Telegram bot client initialized.")

    async def shutdown(self) -> None:
        if self._application is not None:
            await self._application.shutdown()
            self._application = None
            logger.info("Telegram bot client shut down.")

    def _register_handlers(self, app: Application) -> None:  # type: ignore[type-arg]
        # Lazy import to avoid any module-level circular dependency risk.
        from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

        from app.core.constants import (
            BTN_BOOK,
            BTN_CANCEL,
            BTN_CONTACT_MASTER,
            BTN_MASTER_ALL_APPOINTMENTS,
            BTN_MASTER_BOOK_CLIENT,
            BTN_MASTER_CANCEL,
            BTN_MASTER_FREE_SLOTS,
            BTN_MASTER_RESCHEDULE,
            BTN_MY_APPOINTMENT,
            BTN_RESCHEDULE,
            BTN_SKIP_PHONE,
        )
        from app.integrations.telegram import handlers

        app.add_handler(CommandHandler("start", handlers.handle_start))
        # Also handle Russian "старт" typed as plain text (e.g. from share button)
        app.add_handler(MessageHandler(filters.Text(["старт", "Старт"]), handlers.handle_start))

        # Client menu handlers
        app.add_handler(MessageHandler(filters.Text([BTN_BOOK]), handlers.handle_book))
        app.add_handler(
            MessageHandler(filters.Text([BTN_MY_APPOINTMENT]), handlers.handle_my_appointment)
        )
        app.add_handler(
            MessageHandler(filters.Text([BTN_RESCHEDULE]), handlers.handle_reschedule)
        )
        app.add_handler(
            MessageHandler(filters.Text([BTN_CANCEL]), handlers.handle_cancel_appointment)
        )
        app.add_handler(
            MessageHandler(filters.Text([BTN_CONTACT_MASTER]), handlers.handle_contact_master)
        )

        # Master menu handlers
        app.add_handler(
            MessageHandler(filters.Text([BTN_MASTER_BOOK_CLIENT]), handlers.handle_master_book_client)
        )
        app.add_handler(
            MessageHandler(
                filters.Text([BTN_MASTER_ALL_APPOINTMENTS]), handlers.handle_master_all_appointments
            )
        )
        app.add_handler(
            MessageHandler(filters.Text([BTN_MASTER_RESCHEDULE]), handlers.handle_master_reschedule)
        )
        app.add_handler(
            MessageHandler(filters.Text([BTN_MASTER_CANCEL]), handlers.handle_master_cancel)
        )
        app.add_handler(
            MessageHandler(filters.Text([BTN_MASTER_FREE_SLOTS]), handlers.handle_master_free_slots)
        )

        # Phone request step handlers (must be before catch-all)
        app.add_handler(MessageHandler(filters.CONTACT, handlers.handle_share_phone))
        app.add_handler(
            MessageHandler(filters.Text([BTN_SKIP_PHONE]), handlers.handle_skip_phone)
        )

        # Inline keyboard callbacks (booking state machine)
        app.add_handler(CallbackQueryHandler(handlers.handle_callback))
        # Catch-all for unrecognised text (must be registered last)
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_unknown)
        )

    async def register_webhook(self, base_url: str, secret: str = "") -> None:
        """Call Telegram's setWebhook so updates are pushed to this server."""
        url = f"{base_url.rstrip('/')}/webhook/telegram"
        kwargs: dict = {"url": url}
        if secret:
            kwargs["secret_token"] = secret
        await self.application.bot.set_webhook(**kwargs)
        logger.info("Telegram webhook registered at %s", url)

    async def process_update(self, data: dict) -> None:  # type: ignore[type-arg]
        from telegram import Update

        update = Update.de_json(data, self.application.bot)
        await self.application.process_update(update)

    async def send_message(self, chat_id: int | str, text: str, **kwargs: object) -> None:
        await self.application.bot.send_message(chat_id=chat_id, text=text, **kwargs)


bot_client = TelegramBotClient()
