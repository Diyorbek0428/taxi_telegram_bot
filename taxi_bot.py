from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import logging
import os

# Bot tokeni va guruh username yoki ID (default: @Toshkent_Rapqon_taxi)
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8433320719:AAHv2Cq1lT7GoBs8Hrx6PLOfWjrgFZZoqAg")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID", "@Toshkent_Rapqon_taxi")  # username yoki numeric id sifatida sozlanadi

# In-memory store for active orders: {client_id: {message_id, chat_id, order_data, driver_id, driver_name}}
orders = {}
# NEW: pending orders (waiting client confirmation): {client_id: {'order_data': ..., 'preview_message_id': int}}
pending_orders = {}

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
        + (f"☎️ Kontakt: {order_data.get('contact')}\n" if order_data.get('contact') else "")
        + f"📞 Mijoz: {client_display}"
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
    await update.message.reply_text("Salom! Taxislarga so‘rov yuborish uchun /order buyrug‘ini ishlating.")

async def order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Qaysi manzildasiz?")
    context.user_data['order_step'] = 1
    context.user_data['order_data'] = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'order_step' not in context.user_data:
        return

    step = context.user_data['order_step']
    text = update.message.text

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
        # Oldingi versiyada shu erda guruhga yuborilardi.
        # Yangi oqim: avvalo mijozdan kontaktni so‘raymiz.
        context.user_data['order_data']['passengers'] = text
        await update.message.reply_text("Iltimos, telefon raqamingizni yuboring ")
        context.user_data['order_step'] = 5  # kutish: kontakt uchun
    elif step == 5:
        # Ba'zan foydalanuvchi kontaktni matn sifatida yuborishi mumkin — qabul qilamiz
        contact_text = text.strip() if text else None
        if not contact_text:
            await update.message.reply_text("Iltimos, telefon raqamingizni yuboring.")
            return

        # save contact in user_data
        context.user_data['order_data']['contact'] = contact_text
        order_data = context.user_data['order_data']

        client_display = get_client_display(update.message.from_user)
        preview_text = format_order_message(order_data, client_display)

        # Inline preview buttons for client: Edit or Confirm
        client_id = int(update.message.from_user.id)
        keyboard = [
            [
                InlineKeyboardButton("Tahrirlash", callback_data=f"preview_edit_{client_id}"),
                InlineKeyboardButton("Tasdiqlash va yuborish", callback_data=f"preview_confirm_{client_id}"),
            ]
        ]
        preview_markup = InlineKeyboardMarkup(keyboard)

        try:
            # send preview to client (not to group)
            sent_preview = await context.bot.send_message(chat_id=client_id, text="Iltimos, so'rovingizni tekshiring:\n\n" + preview_text, reply_markup=preview_markup)
        except Exception:
            logger.exception("Preview yuborishda xato:")
            await update.message.reply_text("Xatolik: Preview yuborilmadi. Iltimos, keyinroq qayta urinib ko‘ring.")
            return

        # store pending order until client confirms
        pending_orders[client_id] = {
            'order_data': order_data.copy(),
            'preview_message_id': sent_preview.message_id,
            'preview_chat_id': sent_preview.chat_id,
        }

        # keep user's state so they can cancel or edit; set to waiting-for-confirm
        context.user_data['order_step'] = 6
        await update.message.reply_text("So‘rovingiz preview qilingan. Agar hammasi to‘g‘ri bo‘lsa «Tasdiqlash va yuborish» tugmasini bosing yoki «Tahrirlash» orqali o‘zgartiring.")
        return

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    # parse actions

    # --- NEW: handle preview confirm (send to group) ---
    if data.startswith("preview_confirm_"):
        # data format: preview_confirm_{client_id}
        try:
            client_id = int(data.split("_")[2])
        except Exception:
            await query.message.reply_text("Xato: noto'g'ri client id")
            return

        # only owner can confirm
        if query.from_user.id != client_id:
            await query.message.reply_text("Faqat so‘rov egasi tasdiqlashi mumkin.")
            return

        pending = pending_orders.pop(client_id, None)
        if not pending:
            await query.message.reply_text("Preview topilmadi yoki allaqachon yuborilgan.")
            return

        order_data = pending['order_data']
        client_display = get_client_display(query.from_user)
        group_message = format_order_message(order_data, client_display)

        # send to group with driver button
        keyboard = [[InlineKeyboardButton("Men bog'lanaman", callback_data=f"contact_{client_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            sent_message = await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=group_message, reply_markup=reply_markup)
        except Exception as exc:
            logger.exception("Guruhga yuborishda xato (confirm): %s", exc)
            # restore pending so client can retry
            pending_orders[client_id] = pending
            # notify client with a simple actionable message
            try:
                await context.bot.send_message(
                    chat_id=client_id,
                    text="Guruhga yuborishda muammo yuz berdi. Iltimos, guruh ID va botning guruhga yozish huquqlarini tekshiring yoki keyinroq qayta urinib ko‘ring."
                )
            except Exception:
                logger.exception("Clientga xato haqida xabar yuborishda ham xato yuz berdi:")
            return

        # Save as active order (awaiting driver)
        orders[client_id] = {
            'message_id': sent_message.message_id,
            'chat_id': sent_message.chat_id,
            'order_data': order_data.copy(),
            'driver_id': None,
            'driver_name': None,
        }

        # Edit client's preview message to show sent status and remove buttons
        try:
            await context.bot.edit_message_text(
                chat_id=pending.get('preview_chat_id'),
                message_id=pending.get('preview_message_id'),
                text="✅ So‘rovingiz guruhga yuborildi:\n\n" + group_message
            )
        except Exception:
            logger.exception("Preview messageni yangilashda xato (after confirm).")

        # clear user's order state
        try:
            context.user_data.pop('order_step', None)
            context.user_data.pop('order_data', None)
        except Exception:
            pass

        # Inform client of success
        try:
            await context.bot.send_message(chat_id=client_id, text="✅ So‘rovingiz muvaffaqiyatli guruhga yuborildi. Taksichi bog‘languncha kuting.")
        except Exception:
            logger.exception("Mijozga muvaffaqiyat haqida xabar yuborishda xato:")
        return

    # --- NEW: handle preview edit (restore and prompt user) ---
    if data.startswith("preview_edit_"):
        # data format: preview_edit_{client_id}
        try:
            client_id = int(data.split("_")[2])
        except Exception:
            await query.message.reply_text("Xato: noto'g'ri client id")
            return

        # only owner can edit
        if query.from_user.id != client_id:
            await query.message.reply_text("Faqat so‘rov egasi tahrirlashi mumkin.")
            return

        pending = pending_orders.pop(client_id, None)
        if pending:
            # restore order_data to user_data for editing
            # NOTE: in callback context, context.user_data refers to the bot's memory for the invoking user,
            # so this will restore data for the user who pressed the button.
            context.user_data['order_data'] = pending['order_data'].copy()
        else:
            context.user_data.setdefault('order_data', {})

        # set step to 1 to restart flow
        context.user_data['order_step'] = 1

        # remove buttons from preview message if exists
        try:
            if pending:
                await context.bot.edit_message_text(
                    chat_id=pending.get('preview_chat_id'),
                    message_id=pending.get('preview_message_id'),
                    text="So‘rov tahrirlanmoqda. Iltimos, yangi ma'lumotni kiriting."
                )
        except Exception:
            logger.exception("Preview edit: message edit failed.")

        await query.message.reply_text("Tahrirlash boshlandi. Qaysi manzildasiz?")
        return

    # ...existing code for contact_, accept_, cancel_ ...
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
            await query.message.reply_text("So‘rov topilmadi yoki allaqchon bekor qilingan.")
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
                text=f"✅ Sizning so‘rovingiz {driver_name} tomonidan qabul qilindi!\nIltimos, hozirgi joylashuvingizni yuboring."
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

# NEW: handler for Telegram Contact objects
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'order_step' not in context.user_data or context.user_data.get('order_step') != 5:
        return

    contact = update.message.contact
    if not contact or not contact.phone_number:
        await update.message.reply_text("Kontakt topilmadi. Iltimos, telefon raqamingizni matn yoki Kontakt orqali yuboring.")
        return

    phone = contact.phone_number
    # optional: combine name
    name = (contact.first_name or "") + (" " + contact.last_name if contact.last_name else "")
    contact_display = f"{name.strip()} ({phone})" if name.strip() else phone

    context.user_data['order_data']['contact'] = contact_display
    order_data = context.user_data['order_data']

    client_display = get_client_display(update.message.from_user)
    preview_text = format_order_message(order_data, client_display)

    client_id = int(update.message.from_user.id)
    keyboard = [
        [
            InlineKeyboardButton("Tahrirlash", callback_data=f"preview_edit_{client_id}"),
            InlineKeyboardButton("Tasdiqlash va yuborish", callback_data=f"preview_confirm_{client_id}"),
        ]
    ]
    preview_markup = InlineKeyboardMarkup(keyboard)

    try:
        sent_preview = await context.bot.send_message(chat_id=client_id, text="Iltimos, so'rovingizni tekshiring:\n\n" + preview_text, reply_markup=preview_markup)
    except Exception:
        logger.exception("Guruhga yuborishda xato (kontakt matn):")
        # Do not notify the user about internal send errors here.
        context.user_data.pop('order_step', None)
        context.user_data.pop('order_data', None)
        return

    pending_orders[client_id] = {
        'order_data': order_data.copy(),
        'preview_message_id': sent_preview.message_id,
        'preview_chat_id': sent_preview.chat_id,
    }

    context.user_data['order_step'] = 6
    await update.message.reply_text("So‘rovingiz preview qilingan. Tasdiqlash yoki tahrirlash tugmasini bosing.")

# --- NEW: global error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error in update handler:")
    # optional: notify developer chat or just continue

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("order", order))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # register contact handler to accept shared contacts
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    app.add_handler(CallbackQueryHandler(button))
    # register error handler
    app.add_error_handler(error_handler)
    app.run_polling()

if __name__ == '__main__':
    main()
if __name__ == '__main__':
    main()

