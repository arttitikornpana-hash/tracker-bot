```python
"""
LINE Bot - Price Tracker v6 (FINAL CLEAN)
- category flow
- optional image
- optional amount
- optional protein
- history compare
- google sheet logging
"""

import os
import re
from datetime import datetime

from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageMessage, QuickReply, QuickReplyButton, MessageAction
)

import gspread
from google.oauth2.service_account import Credentials


# ─────────────────────────────
# APP CONFIG
# ─────────────────────────────

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_NAME = "prices"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)


# ─────────────────────────────
# CATEGORY (IMPORTANT: นม/โปรตีน ต้องอยู่บนสุด)
# ─────────────────────────────

CATEGORIES = [
    "นม/โปรตีน",
    "ของสด",
    "ของแห้ง",
    "ของใช้",
    "เครื่องดื่ม",
    "ขนม",
    "สุขภาพ",
    "อื่นๆ"
]


# ─────────────────────────────
# STATE
# ─────────────────────────────

state = {}


# ─────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_file(
        "credentials.json",
        scopes=scopes
    )

    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)


def ensure_header(sheet):
    headers = [
        "วันที่-เวลา",
        "ชื่อสินค้า",
        "category",
        "ราคา (฿)",
        "amount",
        "unit",
        "โปรตีน (g)",
        "ราคา/unit (฿)",
        "ราคา/โปรตีนg (฿)",
        "ซื้อครั้งที่",
        "image_id"
    ]

    if sheet.row_values(1) != headers:
        sheet.insert_row(headers, 1)


# ─────────────────────────────
# NORMALIZE NAME
# ─────────────────────────────

def normalize_name(name):
    return (
        str(name)
        .lower()
        .replace("โปรตีน", "")
        .replace("protein", "")
        .strip()
    )


# ─────────────────────────────
# PARSER (FULL OPTIONAL)
# ─────────────────────────────

def parse_product(text: str):

    text = re.sub(r'กรัม', 'g', text.strip())

    # ── protein optional ──
    protein = 0.0
    m_prot = re.search(r'(?:โปรตีน|protein)\s*([\d.]+)\s*g', text, re.IGNORECASE)

    if m_prot:
        protein = float(m_prot.group(1))
        text = text.replace(m_prot.group(0), "").strip()

    # ── price ──
    m_price = re.search(r'([\d.]+)\s*บาท', text)
    if not m_price:
        return None

    price = float(m_price.group(1))
    text = text.replace(m_price.group(0), "").strip()

    # ── optional amount ──
    m_amount = re.search(r'([\d.]+)\s*(g|ml|ฟอง|ชิ้น)', text)

    amount = 0
    unit = ""
    price_per_unit = None

    if m_amount:
        amount = float(m_amount.group(1))
        unit = m_amount.group(2)

        if amount > 0:
            price_per_unit = round(price / amount, 4)

        name = text.replace(m_amount.group(0), "").strip()
    else:
        name = text.strip()

    price_per_prot = round(price / protein, 4) if protein > 0 else None

    return {
        "name": name,
        "price": price,
        "amount": amount,
        "unit": unit,
        "protein": protein,
        "price_per_unit": price_per_unit,
        "price_per_prot": price_per_prot
    }


# ─────────────────────────────
# HISTORY
# ─────────────────────────────

def get_history(sheet, name: str):
    records = sheet.get_all_records()
    n = normalize_name(name)

    return [
        r for r in records
        if normalize_name(r.get("ชื่อสินค้า", "")) == n
    ]


# ─────────────────────────────
# SAVE
# ─────────────────────────────

def save_row(sheet, product, category, image_id, buy_count):

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    sheet.append_row([
        now,
        product["name"],
        category,
        product["price"],
        product["amount"],
        product["protein"],
        product["price_per_unit"] or "",
        product["price_per_prot"] or "",
        buy_count,
        image_id or ""
    ])


# ─────────────────────────────
# RESULT FORMAT
# ─────────────────────────────

def format_result(product, category, history):

    name = product["name"]
    ppu = product["price_per_unit"]
    ppp = product["price_per_prot"]
    unit = product["unit"]

    lines = [f"✅ บันทึกแล้ว: {name} [{category}]", ""]

    if ppu:
        lines.append(f"💸 ราคา/{unit or 'หน่วย'}: {ppu:.2f} ฿")

    if ppp:
        lines.append(f"💪 โปรตีนคุ้มค่า: {ppp:.2f} ฿/g")

    if len(history) > 0:
        lines.append(f"📦 ซื้อซ้ำครั้งที่ {len(history)+1}")

    return "\n".join(lines)


# ─────────────────────────────
# QUICK REPLY CATEGORY
# ─────────────────────────────

def send_category(reply_token):

    buttons = [
        QuickReplyButton(
            action=MessageAction(
                label=c,
                text=f"__cat__{c}"
            )
        )
        for c in CATEGORIES
    ]

    line_bot_api.reply_message(
        reply_token,
        TextSendMessage(
            text="เลือก Category 👇",
            quick_reply=QuickReply(items=buttons)
        )
    )


# ─────────────────────────────
# WEBHOOK
# ─────────────────────────────

@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


# ─────────────────────────────
# IMAGE HANDLER
# ─────────────────────────────

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    user_id = event.source.user_id
    image_id = event.message.id

    s = state.get(user_id, {})

    state[user_id] = {
        **s,
        "image_id": image_id
    }


# ─────────────────────────────
# TEXT HANDLER
# ─────────────────────────────

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    user_id = event.source.user_id
    text = event.message.text.strip()
    s = state.get(user_id, {})

    sheet = get_sheet()
    ensure_header(sheet)

    # start
    if text == "บันทึก":
        send_category(event.reply_token)
        state[user_id] = {}
        return

    # category
    if text.startswith("__cat__"):
        cat = text.replace("__cat__", "")
        state[user_id] = {"category": cat}
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"เลือก {cat} แล้ว ส่งข้อมูลสินค้าได้เลย")
        )
        return

    # parse product
    product = parse_product(text)

    if not product:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="รูปแบบไม่ถูกต้อง")
        )
        return

    category = s.get("category", "อื่นๆ")
    image_id = s.get("image_id", "")

    history = get_history(sheet, product["name"])

    save_row(sheet, product, category, image_id, len(history)+1)

    reply = format_result(product, category, history)

    state.pop(user_id, None)

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
```
