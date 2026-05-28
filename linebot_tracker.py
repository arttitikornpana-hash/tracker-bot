```python
import re
import time
from datetime import datetime

import gspread

from oauth2client.service_account import (
    ServiceAccountCredentials
)

from flask import Flask, request

from linebot import (
    LineBotApi,
    WebhookHandler
)

from linebot.exceptions import (
    InvalidSignatureError
)

from linebot.models import *

# ─────────────────────────────
# CONFIG
# ─────────────────────────────

LINE_CHANNEL_ACCESS_TOKEN = "YOUR_TOKEN"
LINE_CHANNEL_SECRET = "YOUR_SECRET"

GOOGLE_SHEET_NAME = "shopping_tracker"

STATE_TIMEOUT = 60 * 30

CATEGORIES = [

    "ของสด",
    "ของแห้ง",
    "นม/โปรตีน",
    "ของใช้",
    "เครื่องดื่ม",
    "ขนม",
    "สุขภาพ",
    "อื่นๆ"
]

# ─────────────────────────────
# LINE
# ─────────────────────────────

app = Flask(__name__)

line_bot_api = LineBotApi(
    LINE_CHANNEL_ACCESS_TOKEN
)

handler = WebhookHandler(
    LINE_CHANNEL_SECRET
)

# ─────────────────────────────
# STATE
# ─────────────────────────────

pending_category = {}
pending_image = {}
pending_timestamp = {}

# ─────────────────────────────
# GOOGLE SHEET
# ─────────────────────────────

def get_sheet():

    scope = [

        "https://spreadsheets.google.com/feeds",

        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "credentials.json",
        scope
    )

    client = gspread.authorize(creds)

    return client.open(
        GOOGLE_SHEET_NAME
    ).sheet1

def ensure_header(sheet):

    rows = sheet.get_all_values()

    if rows:
        return

    sheet.append_row([

        "datetime",
        "product",
        "category",
        "price",
        "amount",
        "unit",
        "price_per_unit",
        "protein_g",
        "protein_price",
        "image_id"
    ])

# ─────────────────────────────
# HELPERS
# ─────────────────────────────

def clear_expired_state():

    now = time.time()

    expired_users = []

    for user_id, ts in pending_timestamp.items():

        if now - ts > STATE_TIMEOUT:

            expired_users.append(user_id)

    for user_id in expired_users:

        pending_timestamp.pop(user_id, None)
        pending_category.pop(user_id, None)
        pending_image.pop(user_id, None)

def normalize_name(name):

    return (
        name.lower()
        .replace("โปรตีน", "")
        .replace("protein", "")
        .strip()
    )

# ─────────────────────────────
# PARSE PRODUCT
# ─────────────────────────────

def parse_price(text):

    text = text.strip()

    price_match = re.search(
        r'(\d+(?:\.\d+)?)\s*บาท',
        text
    )

    amount_match = re.search(
        r'(\d+(?:\.\d+)?)\s*(g|ml|ฟอง|ชิ้น)',
        text
    )

    protein_match = re.search(
        r'โปรตีน\s*(\d+(?:\.\d+)?)g',
        text
    )

    if not price_match or not amount_match:
        return None

    price = float(
        price_match.group(1)
    )

    amount = float(
        amount_match.group(1)
    )

    unit = amount_match.group(2)

    name = text.split(
        str(int(price))
    )[0].strip()

    price_per_unit = round(
        price / amount,
        2
    )

    protein_amount = 0
    protein_price = 0

    if protein_match:

        protein_amount = float(
            protein_match.group(1)
        )

        if protein_amount > 0:

            protein_price = round(
                price / protein_amount,
                2
            )

    return {

        "name": name,

        "normalized_name": normalize_name(
            name
        ),

        "price": price,

        "amount": amount,

        "unit": unit,

        "price_per_unit": price_per_unit,

        "protein_amount": protein_amount,

        "protein_price": protein_price
    }

# ─────────────────────────────
# HISTORY
# ─────────────────────────────

def get_history(sheet, product_name):

    rows = sheet.get_all_records()

    normalized = normalize_name(
        product_name
    )

    prices = []

    for row in rows:

        old_name = normalize_name(
            row["product"]
        )

        if old_name == normalized:

            prices.append(
                float(row["price"])
            )

    if not prices:

        return None

    avg_price = sum(prices) / len(prices)

    return {

        "count": len(prices),

        "avg_price": avg_price,

        "min_price": min(prices)
    }

# ─────────────────────────────
# SAVE
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
# FORMAT REPLY
# ─────────────────────────────

def format_reply(
    product,
    category,
    history
):

    lines = []

    lines.append(
        f"✅ บันทึกแล้ว: {product['name']} [{category}]"
    )

    lines.append("")

    lines.append(
        f"💸 ราคา/{product['unit']}: "
        f"{product['price_per_unit']}฿"
    )

    if product["protein_amount"] > 0:

        lines.append(
            f"💪 ราคาโปรตีนจริง: "
            f"{product['protein_price']}฿/โปรตีนg"
        )

    if history:

        lines.append("")

        lines.append(
            f"📦 ซื้อซ้ำครั้งที่ "
            f"{history['count'] + 1}"
        )

        diff = (
            (
                product["price"]
                - history["avg_price"]
            )
            / history["avg_price"]
        ) * 100

        if diff < 0:

            lines.append(
                f"🟢 ถูกกว่าค่าเฉลี่ย "
                f"{abs(round(diff))}%"
            )

        else:

            lines.append(
                f"🔴 แพงกว่าค่าเฉลี่ย "
                f"{round(diff)}%"
            )

        if product["price"] <= history["min_price"]:

            lines.append(
                "🏆 นี่คือราคาถูกที่สุด!"
            )

    return "\n".join(lines)

# ─────────────────────────────
# QUICK REPLY
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
# IMAGE
# ─────────────────────────────

@handler.add(
    MessageEvent,
    message=ImageMessage
)

def handle_image(event):

    clear_expired_state()

    user_id = event.source.user_id

    if user_id not in pending_category:

        line_bot_api.reply_message(

            event.reply_token,

            TextSendMessage(
                text="พิมพ์ 'เพิ่มสินค้า' ก่อน 👇"
            )
        )

        return

    image_id = event.message.id

    pending_image[user_id] = image_id

    pending_timestamp[user_id] = time.time()

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
# TEXT
# ─────────────────────────────

@handler.add(
    MessageEvent,
    message=TextMessage
)

def handle_text(event):

    clear_expired_state()

    user_id = event.source.user_id

    text = event.message.text.strip()

    try:

        sheet = get_sheet()

        ensure_header(sheet)

        # start
        if text in [

            "เพิ่ม",
            "เพิ่มสินค้า"
        ]:

            pending_timestamp[
                user_id
            ] = time.time()

            line_bot_api.reply_message(

                event.reply_token,

                make_category_selector()
            )

            return

        # category
        if text.startswith("__cat__"):

            category = text.replace(
                "__cat__",
                ""
            ).strip()

            pending_category[
                user_id
            ] = category

            pending_timestamp[
                user_id
            ] = time.time()

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

        # parse
        product = parse_price(text)

        if not product:

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(

                    text=(
                        "พิมพ์ไม่ถูกต้อง\n\n"
                        "เช่น:\n"
                        "ไข่ไก่ 79 บาท 10 ฟอง"
                    )
                )
            )

            return

        if user_id not in pending_category:

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(
                    text="เลือก category ก่อน 👇"
                )
            )

            return

        if user_id not in pending_image:

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(
                    text="ส่งรูปสินค้าก่อน 📸"
                )
            )

            return

        category = pending_category[
            user_id
        ]

        image_id = pending_image[
            user_id
        ]

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

        reply = format_reply(
            product,
            category,
            history
        )

        pending_image.pop(
            user_id,
            None
        )

        line_bot_api.reply_message(

            event.reply_token,

            TextSendMessage(
                text=reply
            )
        )

    except Exception as e:

        line_bot_api.reply_message(

            event.reply_token,

            TextSendMessage(
                text=f"ERROR: {str(e)}"
            )
        )

# ─────────────────────────────
# WEBHOOK
# ─────────────────────────────

@app.route(
    "/callback",
    methods=["POST"]
)

def callback():

    signature = request.headers[
        "X-Line-Signature"
    ]

    body = request.get_data(
        as_text=True
    )

    try:

        handler.handle(
            body,
            signature
        )

    except InvalidSignatureError:

        return "Invalid signature", 400

    return "OK"

# ─────────────────────────────
# RUN
# ─────────────────────────────

if __name__ == "__main__":

    app.run(port=5000)
```
