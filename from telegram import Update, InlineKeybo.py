from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import logging
import os

# Bot tokeni va guruh ID
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8433320719:AAHv2Cq1lT7GoBs8Hrx6PLOfWjrgFZZoqAg")
# make sure GROUP_CHAT_ID is an int
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "1379834022"))

# In-memory store for active orders: {client_id: {message_id, chat_id, order_data, driver_id, driver_name}}
orders = {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- NEW helper functions ---
def format_order_message(order_data: dict, client_display: str) -> str:
    return (
        f"🔔 Yangi so‘rov!\n"
        f"📍 Qayerdan: {order_data.get('from')}\n"
        f"📍 Qayerga: {order_data.get('to')}\n"
        f"⏰ Vaqt: {order_data.get('time')}\n"
        f"👥 Yo‘lovchilar: {order_data.get('passengers')}\n"
        f"📞 Mijoz: {client_display}"
    )

def get_client_display(user) -> str:
    # user can be Update.message.from_user or an id
    if not user:
        return "unknown"
    try:
        username = getattr(user, "username", None)
        user_id = getattr(user, "id", None)
        return f"@{username}" if username else str(user_id)
    except Exception:
        return str(user)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Salom! Taxi so‘rovini yuborish uchun /order buyrug‘ini ishlat.")

async def order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Qaysi manzildasiz?")
    context.user_data['order_step'] = 1
    context.user_data['order_data'] = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Guard: make sure we have a message and text
    if not update.message:
        logger.info("handle_message: update has no message, ignoring")
        return

    text = update.message.text
    user_id = update.message.from_user.id if update.message.from_user else None
    chat_type = update.effective_chat.type if update.effective_chat else "unknown"

    # Log for debugging so you can see why steps may not proceed
    logger.info("handle_message: user=%s chat_type=%s text=%r", user_id, chat_type, text)

    # If user sent non-text (sticker/photo/etc.), remind them to send text
    if text is None:
        await update.message.reply_text("Iltimos, matn kiriting (manzil, vaqt yoki yo‘lovchilar soni).")
        return

    # Ensure user_data structure exists (in case it was cleared)
    if 'order_step' not in context.user_data or 'order_data' not in context.user_data:
        # Not in an active order flow — ignore or prompt user to start
        await update.message.reply_text("Agar yangi buyurtma bermoqchi bo‘lsangiz, /order buyrug‘ini bosing.")
        return

    step = context.user_data['order_step']

    if step == 1:
        context.user_data['order_data']['from'] = text
        await update.message.reply_text("Qayerga bormoqchisiz?")
        context.user_data['order_step'] = 2
    elif step == 2:
        context.user_data['order_data']['to'] = text
        await update.message.reply_text("Qachon ketmoqchisiz? (masalan, 18:00)")
        context.user_data['order_step'] = 3
    elif step == 3:
        context.user_data['order_data']['time'] = text
        await update.message.reply_text("Yo‘lovchilar soni?")
        context.user_data['order_step'] = 4
    elif step == 4:
        context.user_data['order_data']['passengers'] = text
        order_data = context.user_data['order_data']

        # Guruhga xabar yuborish (wrapped in try/except to avoid crashing)
        client_display = get_client_display(update.message.from_user)
        message = format_order_message(order_data, client_display)

        keyboard = [[InlineKeyboardButton("Men bog'lanaman", callback_data=f"contact_{update.message.from_user.id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            sent_message = await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=message, reply_markup=reply_markup)
        except Exception as exc:
            logger.exception("Guruhga yuborishda xato:")
            # inform the client and clear the in-progress order
            await update.message.reply_text("Xato: so‘rovingiz guruhga yuborilmadi. Iltimos, keyinroq qayta urinib ko‘ring.")
            context.user_data.pop('order_step', None)
            context.user_data.pop('order_data', None)
            return

        client_id = int(update.message.from_user.id)
        orders[client_id] = {
            'message_id': sent_message.message_id,
            'chat_id': sent_message.chat_id,
            'order_data': order_data.copy(),
            'driver_id': None,
            'driver_name': None,
        }

        cancel_keyboard = [[InlineKeyboardButton("So‘rovni bekor qilish", callback_data=f"cancel_{update.message.from_user.id}")]]
        cancel_markup = InlineKeyboardMarkup(cancel_keyboard)
        await update.message.reply_text("So‘rovingiz taksichilar guruhiga yuborildi! Iltimos, kuting.", reply_markup=cancel_markup)

        # mark as waiting; keep order_step to allow cancellation, but prevent further inputs being treated as new order steps
        context.user_data['order_step'] = 5  # Kutilmoqda holati
    else:
        # unknown step — clear state and inform user to restart
        logger.warning("handle_message: unknown order_step=%s for user=%s — clearing state", step, user_id)
        context.user_data.pop('order_step', None)
        context.user_data.pop('order_data', None)
        await update.message.reply_text("Xato holat topildi. Yangi buyurtma uchun /order buyrug'ini bosing.")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    # parse actions
    if data.startswith("contact_"):
        # driver clicked 'contact' to accept the request
        driver_id = query.from_user.id
        driver_name = f"@{query.from_user.username}" if query.from_user.username else str(driver_id)
        try:
            client_id = int(data.split("_")[1])
        except Exception:
            await query.message.reply_text("Xato: noto'g'ri client id")
            return

        order = orders.get(client_id)
        if not order:
            await query.message.reply_text("So‘rov topilmadi yoki allaqachon bekor qilingan.")
            return

        # update order
        order['driver_id'] = driver_id
        order['driver_name'] = driver_name

        # Build a safe updated message using stored order_data (fallback if query.message.text missing)
        try:
            original_text = query.message.text if query.message and query.message.text else None
        except Exception:
            original_text = None

        if original_text:
            updated_message = original_text + f"\n\n🚖 {driver_name} so‘rovni qabul qildi, kutilmoqda..."
        else:
            updated_message = format_order_message(order['order_data'], get_client_display(client_id)) + f"\n\n🚖 {driver_name} so‘rovni qabul qildi, kutilmoqda..."

        keyboard = [[InlineKeyboardButton("Men oldim", callback_data=f"accept_{client_id}_{driver_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await context.bot.edit_message_text(
                chat_id=order['chat_id'],
                message_id=order['message_id'],
                text=updated_message,
                reply_markup=reply_markup
            )
        except Exception:
            logger.exception("Guruhdagi xabarni yangilashda xato:")

        # Mijozga xabar yuborish
        try:
            await context.bot.send_message(
                chat_id=client_id,
                text=f"🚖 Taksichi {driver_name} siz bilan bog‘lanmoqda. Iltimos, kuting."
            )
        except Exception:
            logger.exception("Mijozga xabar yuborishda xato:")

    elif data.startswith("accept_"):
        # accept_{client_id}_{driver_id}
        parts = data.split("_")
        if len(parts) < 3:
            await query.message.reply_text("Xato: noto'g'ri malumot")
            return
        client_id = int(parts[1])
        driver_id = int(parts[2])
        order = orders.get(client_id)
        if not order:
            await query.message.reply_text("So‘rov topilmadi.")
            return

        driver_name = order.get('driver_name')
        if not driver_name:
            if query.from_user and query.from_user.username:
                driver_name = f"@{query.from_user.username}"
            else:
                driver_name = str(driver_id)

        # safe updated message
        try:
            original_text = query.message.text if query.message and query.message.text else None
        except Exception:
            original_text = None

        if original_text:
            updated_message = original_text + f"\n\n✅ So‘rov {driver_name} tomonidan qabul qilindi!"
        else:
            updated_message = format_order_message(order['order_data'], get_client_display(client_id)) + f"\n\n✅ So‘rov {driver_name} tomonidan qabul qilindi!"

        try:
            await context.bot.edit_message_text(
                chat_id=order['chat_id'],
                message_id=order['message_id'],
                text=updated_message
            )
        except Exception:
            logger.exception("Guruhdagi xabarni yangilashda xato (accept):")

        # Mijozga taksichi ma'lumotlarini yuborish va lokatsiya so'rash
        try:
            await context.bot.send_message(
                chat_id=client_id,
                text=f"✅ Sizning so‘rovingiz {driver_name} tomonidan qabul qilindi!\nIltimos, hozirgi joylashuvingizni taxisga yuboring."
            )
        except Exception:
            logger.exception("Mijozga xabar yuborishda xato (accept):")

    elif data.startswith("cancel_"):
        try:
            client_id = int(data.split("_")[1])
        except Exception:
            await query.message.reply_text("Xato: noto'g'ri client id")
            return

        # only client can cancel
        if query.from_user.id != int(client_id):
            await query.message.reply_text("Faqat so‘rov egasi uni bekor qilishi mumkin!")
            return

        order = orders.pop(client_id, None)
        if not order:
            await query.message.reply_text("So‘rov topilmadi.")
            return

        # Guruhdagi xabarni yangilash: "So‘rov bekor qilindi"
        try:
            await context.bot.edit_message_text(
                chat_id=order['chat_id'],
                message_id=order['message_id'],
                text=(query.message.text if query.message and query.message.text else format_order_message(order['order_data'], get_client_display(client_id)))
                + f"\n\n❌ So‘rov @{query.from_user.username or query.from_user.id} tomonidan bekor qilindi."
            )
        except Exception:
            logger.exception("Guruhdagi xabarni yangilashda xato (cancel):")

        # Mijozga tasdiqlash va qayta boshlash taklifi
        await query.message.reply_text("So‘rovingiz bekor qilindi.\nYangi buyurtma boshlash uchun /order ni bosing.")

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Expecting client to send location after driver accepted
    try:
        location = update.message.location
    except Exception:
        await update.message.reply_text("Lokatsiya topilmadi.")
        return

    if not location:
        await update.message.reply_text("Lokatsiya topilmadi.")
        return

    client_id = update.message.from_user.id
    order = orders.get(client_id)
    if not order:
        await update.message.reply_text("Faol so‘rov topilmadi. Iltimos /order ni bosing.")
        return

    # send location to group as reply to order message
    try:
        await context.bot.send_location(
            chat_id=order['chat_id'],
            latitude=location.latitude,
            longitude=location.longitude,
            reply_to_message_id=order['message_id']
        )
        await context.bot.send_message(
            chat_id=order['chat_id'],
            text=f"📍 Mijozning joylashuvi: @{update.message.from_user.username or client_id}"
        )
        await update.message.reply_text("Joylashuvingiz taksichiga yuborildi. Tez orada siz bilan bog‘lanadi!")
    except Exception:
        logger.exception("Lokatsiyani yuborishda xato:")
        await update.message.reply_text("Lokatsiyani yuborishda xato yuz berdi.")

    # clean up
    orders.pop(client_id, None)
    # Also clear user's order state
    context.user_data.pop('order_step', None)
    context.user_data.pop('order_data', None)

# --- NEW: global error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error in update handler:")
    # optional: notify developer chat or just continue

# Add simple /cancel command so user can abort manually
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id if update.message and update.message.from_user else None
    context.user_data.pop('order_step', None)
    context.user_data.pop('order_data', None)
    await update.message.reply_text("So‘rov bekor qilindi. Yana buyurtma uchun /order buyrug'ini bosing.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("order", order))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(CallbackQueryHandler(button))
    # register error handler
    app.add_error_handler(error_handler)
    app.run_polling()

if __name__ == '__main__':
    main()