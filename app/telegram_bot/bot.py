def run_bot() -> None:
    """
    Start the Telegram bot (polling).

    Demo commands:
      - /top <k>        -> shows top matched jobs
      - /save <job_id> -> saves a job to `saved_jobs` (Notion optional/simulated)
      - free text       -> agent interprets your message
    """
    import logging

    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

    from app.config import settings
    from app.agent.agent_core import handle_user_query

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("telegram-bot")

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Set it in your `.env`.")

    # Simple in-memory de-dupe for updates (prevents repeated replies if Telegram
    # re-delivers an update or if polling restarts quickly).
    processed_update_ids: set[int] = set()

    # Per-chat short conversation history for better continuity.
    # Key: chat_id, Value: list of {"role": "...", "content": "..."} dicts.
    conversation_history: dict[int, list[dict[str, str]]] = {}

    async def _reply(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str) -> None:
        """
        Single place that calls the agent and sends back the reply.
        """
        if update.update_id in processed_update_ids:
            return
        processed_update_ids.add(update.update_id)
        # Keep the set small (avoid unbounded growth).
        if len(processed_update_ids) > 5000:
            processed_update_ids.clear()

        chat_id = update.effective_chat.id if update.effective_chat else 0
        history = conversation_history.get(chat_id, [])

        result = handle_user_query(user_text, conversation_history=history)
        reply_text = result.get("reply") or "Done."
        await update.message.reply_text(reply_text)

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply_text})
        history = history[-10:]
        conversation_history[chat_id] = history

    async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        k = context.args[0] if context.args else ""
        user_text = f"/top {k}".strip()
        await _reply(update, context, user_text)

    async def save_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.message.reply_text("Usage: /save <job_id>")
            return
        job_id = context.args[0]
        user_text = f"/save {job_id}"
        await _reply(update, context, user_text)

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, context, "/help")

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        await _reply(update, context, update.message.text)

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Prevent silent crashes during demo; show a short message to user.
        logger.exception("Unhandled bot error", exc_info=context.error)
        if isinstance(update, Update) and update.message:
            await update.message.reply_text(
                "Something went wrong while processing that message. Try again, or use /top 5."
            )

    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("save", save_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    logger.info("Telegram bot started. Listening for messages...")
    app.run_polling()


if __name__ == "__main__":
    run_bot()
