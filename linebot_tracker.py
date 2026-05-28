```python
import os
import re

from datetime import datetime

from flask import Flask, request, abort

from linebot import (
    LineBotApi,
    WebhookHandler
)

from linebot.exceptions import (
    InvalidSignatureError
)

from linebot.models import (
    MessageEvent,
    TextMessage,
    TextSendMessage,
    QuickReply,
    QuickReplyButton,
    MessageAction
)

import gspread

from google.oauth2.service_account import (
    Credentials
)

# ─────────────────────────────
# APP
# ─────────────────────────────

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ.get(
    "LINE_CHANNEL_SECRET",
    ""
)

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    "LINE_CHANNEL_ACCESS_TOKEN",
    ""
)

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID",
    ""
)

SHEET_NAME = "prices"

line_bot_api = LineBotApi(
    LINE_CHANNEL_ACCESS_TOKEN
)

handler = WebhookHandler(
    LINE_CHANNEL_SECRET
)

# ─────────────────────────────
# CATEGORY
# ─────────────────────────────

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

CATEGORY_ALIAS = {
    "โปรตีน": "นม/โปรตีน",
    "นม": "นม/โปรตีน",
    "สด": "ของสด",
    "แห้ง": "ของแห้ง",
}

# user_id -> category
pending_category = {}

# ─────────────────────────────
# GOOGLE SHEET
# ─────────────────────────────

def get_sheet():

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_file(
        "credentials.json",
        scopes=scopes
    )

    client = gspread.authorize(
        creds
    )

    spreadsheet = client.open_by_key(
        SPREADSHEET_ID
    )

    try:

        sheet = spreadsheet.worksheet(
            SHEET_NAME
        )

    except:

        sheet = spreadsheet.add_worksheet(
            title=SHEET_NAME,
            rows=1000,
            cols=20
        )

    return sheet


def ensure_header(sheet):

    headers = [
        "วันที่-เวลา",
        "ชื่อสินค้า",
        "category",
        "ราคา (฿)",
        "ปริมาณ",
        "หน่วย",
        "ราคา/หน่วย (฿)",
        "โปรตีน (g)",
        "ราคา/โปรตีน (฿)"
    ]

    current = sheet.row_values(1)

    if current != headers:

        sheet.clear()

        sheet.insert_row(
            headers,
            1
        )

# ─────────────────────────────
# PARSE PRODUCT
# ─────────────────────────────

def parse_price(text: str):

    text = text.strip()

    text = re.sub(
        r'กรัม',
        'g',
        text
    )

    text = re.sub(
        r'มล\.?|มิลลิลิตร',
        'ml',
        text
    )

    text = re.sub(
        r'บาท',
        '',
        text
    )

    protein_amount = 0

    protein_match = re.search(
        r'โปรตีน\s*([\d.]+)\s*g',
        text
    )

    if protein_match:

        protein_amount = float(
            protein_match.group(1)
        )

        text = re.sub(
            r'โปรตีน\s*[\d.]+\s*g',
            '',
            text
        )

    pattern = r'^(.+?)\s+([\d.]+)\s+([\d.]+)\s*([a-zA-Zก-๙]+)$'

    match = re.match(
        pattern,
        text.strip()
    )

    if not match:
        return None

    name = match.group(1).strip()

    price = float(match.group(2))

    amount = float(match.group(3))

    unit = match.group(4).strip()

    price_per_unit = round(
        price / amount,
        4
    )

    protein_price = 0

    if protein_amount > 0:

        protein_price = round(
            price / protein_amount,
            4
        )

    return {

        "name": name,

        "price": price,

        "amount": amount,

        "unit": unit,

        "price_per_unit": price_per_unit,

        "protein_amount": protein_amount,

        "protein_price": protein_price,
    }

# ─────────────────────────────
# SAVE
# ─────────────────────────────

def save_to_sheet(
    sheet,
    product,
    category
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
    ])

# ─────────────────────────────
# HISTORY
# ─────────────────────────────

def get_history(
    sheet,
    name
):

    records = sheet.get_all_records()

    return [

        r for r in records

        if r["ชื่อสินค้า"].strip()
        == name.strip()
    ]

# ─────────────────────────────
# REPLY
# ─────────────────────────────

def format_reply(
    product,
    category,
    history
):

    name = product["name"]

    price = product["price"]

    amount = product["amount"]

    unit = product["unit"]

    ppu = product["price_per_unit"]

    protein_amount = product[
        "protein_amount"
    ]

    protein_price = product[
        "protein_price"
    ]

    lines = [

        f"✅ {name}",

        f"[{category}]",

        "",

        f"💰 {price:.0f}฿",

        f"⚖️ {amount:.0f}{unit}",

        f"📊 {ppu:.2f}฿/{unit}",
    ]

    if protein_amount > 0:

        lines.append(
            f"💪 โปรตีน {protein_amount:.0f}g"
        )

        lines.append(
            f"💸 {protein_price:.2f}฿/โปรตีนg"
        )

    if history:

        all_ppu = [

            float(
                r["ราคา/หน่วย (฿)"]
            )

            for r in history
        ]

        avg_ppu = (
            sum(all_ppu)
            / len(all_ppu)
        )

        diff = (
            (ppu - avg_ppu)
            / avg_ppu
            * 100
        ) if avg_ppu > 0 else 0

        lines.append("")

        lines.append(
            f"📦 ซื้อซ้ำครั้งที่ {len(history)+1}"
        )

        if diff > 25:

            lines.append(
                f"🚨 แพงกว่าปกติ {diff:.1f}%"
            )

        elif diff > 0:

            lines.append(
                f"🔴 แพงขึ้น {diff:.1f}%"
            )

        elif diff < 0:

            lines.append(
                f"🟢 ถูกลง {abs(diff):.1f}%"
            )

    else:

        lines.append("")
        lines.append("🆕 ซื้อครั้งแรก")

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
# SUMMARY
# ─────────────────────────────

def cmd_summary(sheet):

    records = sheet.get_all_records()

    if not records:
        return "ยังไม่มีข้อมูล"

    latest = {}

    for r in records:

        latest[
            r["ชื่อสินค้า"]
        ] = r

    lines = [
        f"📦 {len(latest)} รายการ\n"
    ]

    for name, r in latest.items():

        lines.append(
            f"• {name} "
            f"[{r['category']}]"
        )

    return "\n".join(lines)


def cmd_summary_by_cat(
    sheet,
    cat
):

    cat = CATEGORY_ALIAS.get(
        cat.strip(),
        cat.strip()
    )

    records = sheet.get_all_records()

    filtered = []

    for r in records:

        row_cat = str(
            r.get("category", "")
        ).strip()

        if row_cat == cat:

            filtered.append(r)

    if not filtered:

        return (
            f"ไม่มีสินค้าใน category: {cat}"
        )

    lines = [
        f"[{cat}] {len(filtered)} รายการ\n"
    ]

    for r in filtered:

        lines.append(
            f"• {r['ชื่อสินค้า']}"
        )

    return "\n".join(lines)

# ─────────────────────────────
# HELP
# ─────────────────────────────

HELP_TEXT = """
📌 วิธีใช้

1.พิมพ์:
เพิ่มสินค้า

2.เลือก category

3.พิมพ์สินค้า เช่น:

ไข่ไก่ 79 บาท 10 ฟอง

Ally clear protein
47 บาท 30g โปรตีน25g

📋 คำสั่ง

สรุป
สรุป โปรตีน
ช่วย
"""

# ─────────────────────────────
# CALLBACK
# ─────────────────────────────

@app.route(
    "/callback",
    methods=["POST"]
)

def callback():

    signature = request.headers.get(
        "X-Line-Signature",
        ""
    )

    body = request.get_data(
        as_text=True
    )

    try:

        handler.handle(
            body,
            signature
        )

    except InvalidSignatureError:

        abort(400)

    return "OK"

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
                        "พิมพ์สินค้าได้เลย"
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

            if user_id not in pending_category:

                reply_msg = TextSendMessage(
                    text=(
                        "พิมพ์ 'เพิ่มสินค้า' ก่อน 👇"
                    )
                )

            else:

                category = pending_category[
                    user_id
                ]

                history = get_history(
                    sheet,
                    product["name"]
                )

                save_to_sheet(
                    sheet,
                    product,
                    category
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

# ─────────────────────────────

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
```
