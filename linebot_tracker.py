```python
# ─────────────────────────────
# STATE
# ─────────────────────────────

# user_id -> category
pending_category = {}

# user_id -> image_id
pending_image = {}

# user_id -> waiting state
pending_waiting_product = {}

# ─────────────────────────────
# SAVE TO SHEET
# ─────────────────────────────

def save_to_sheet(
    sheet,
    product,
    category,
    image_id=""
):

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M"
    )

    sheet.append_row([

        now,

        product["name"],

        category,

        product["price"],

        product["amount"],

        product["unit"],

        product["price_per_unit"],

        product["protein_amount"],

        product["protein_price"],

        image_id
    ])

# ─────────────────────────────
# CATEGORY QUICK REPLY
# ─────────────────────────────

def make_category_selector():

    buttons = [

        QuickReplyButton(

            action=MessageAction(
                label=cat,
                text=f"__cat__{cat}"
            )

        )

        for cat in CATEGORIES
    ]

    return TextSendMessage(

        text="เลือกหมวดสินค้าก่อน 👇",

        quick_reply=QuickReply(
            items=buttons
        )
    )

# ─────────────────────────────
# IMAGE HANDLER
# ─────────────────────────────

@handler.add(
    MessageEvent,
    message=ImageMessage
)

def handle_image(event):

    user_id = event.source.user_id

    image_id = event.message.id

    # ยังไม่ได้เลือก category
    if user_id not in pending_category:

        line_bot_api.reply_message(

            event.reply_token,

            TextSendMessage(
                text=(
                    "พิมพ์ 'เพิ่มสินค้า' ก่อน 👇"
                )
            )
        )

        return

    # เก็บรูป
    pending_image[user_id] = image_id

    pending_waiting_product[user_id] = True

    category = pending_category[user_id]

    line_bot_api.reply_message(

        event.reply_token,

        TextSendMessage(

            text=(
                f"📸 รับรูปแล้ว [{category}] ✅\n\n"
                "พิมพ์ข้อมูลสินค้าได้เลย\n\n"
                "เช่น:\n"
                "ไข่ไก่ 79 บาท 10 ฟอง\n\n"
                "หรือ:\n"
                "Ally clear protein 47 บาท 30g โปรตีน25g"
            )
        )
    )

# ─────────────────────────────
# TEXT HANDLER
# ─────────────────────────────

@handler.add(
    MessageEvent,
    message=TextMessage
)

def handle_text(event):

    user_id = event.source.user_id

    text = event.message.text.strip()

    try:

        sheet = get_sheet()

        ensure_header(sheet)

        # เริ่มเพิ่มสินค้า
        if text in [
            "เพิ่ม",
            "เพิ่มสินค้า"
        ]:

            line_bot_api.reply_message(

                event.reply_token,

                make_category_selector()
            )

            return

        # เลือก category
        if text.startswith("__cat__"):

            category = text.replace(
                "__cat__",
                ""
            ).strip()

            pending_category[
                user_id
            ] = category

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(
                    text=(
                        f"เลือก [{category}] แล้ว ✅\n\n"
                        "ส่งรูปสินค้าได้เลย 📸"
                    )
                )
            )

            return

        # summary
        if text.startswith("สรุป"):

            arg = text.replace(
                "สรุป",
                ""
            ).strip()

            if arg:

                reply = cmd_summary_by_cat(
                    sheet,
                    arg
                )

            else:

                reply = cmd_summary(sheet)

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(
                    text=reply
                )
            )

            return

        # help
        if text in [
            "ช่วย",
            "help",
            "?"
        ]:

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(
                    text=HELP_TEXT
                )
            )

            return

        # parse product
        product = parse_price(text)

        if product:

            # ยังไม่ได้เลือก category
            if user_id not in pending_category:

                reply_msg = TextSendMessage(
                    text="พิมพ์ 'เพิ่มสินค้า' ก่อน 👇"
                )

            # ยังไม่ได้ส่งรูป
            elif user_id not in pending_image:

                reply_msg = TextSendMessage(
                    text="ส่งรูปสินค้าก่อน 📸"
                )

            else:

                category = pending_category[
                    user_id
                ]

                image_id = pending_image.get(
                    user_id,
                    ""
                )

                history = get_history(
                    sheet,
                    product["name"]
                )

                save_to_sheet(
                    sheet,
                    product,
                    category,
                    image_id
                )

                # clear state
                pending_image.pop(
                    user_id,
                    None
                )

                pending_waiting_product.pop(
                    user_id,
                    None
                )

                reply_msg = TextSendMessage(

                    text=format_reply(
                        product,
                        category,
                        history
                    )
                )

        else:

            reply_msg = TextSendMessage(
                text=(
                    "พิมพ์ไม่ถูกต้อง\n\n"
                    "เช่น:\n"
                    "ไข่ไก่ 79 บาท 10 ฟอง"
                )
            )

        line_bot_api.reply_message(

            event.reply_token,

            reply_msg
        )

    except Exception as e:

        line_bot_api.reply_message(

            event.reply_token,

            TextSendMessage(
                text=f"ERROR: {str(e)}"
            )
        )
```
