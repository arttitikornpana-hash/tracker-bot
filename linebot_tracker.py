```python
"""
LINE Bot - Price Tracker v3
format: "ไข่ไก่ 79 บาท 10 ฟอง"
"""

import os
import re
from datetime import datetime
from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent,
    TextMessage,
    TextSendMessage,
    ImageMessage,
    QuickReply,
    QuickReplyButton,
    MessageAction
)

import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

SHEET_NAME = "prices"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ──────────────────────────────────────────────────────────────
# Categories
# ──────────────────────────────────────────────────────────────

CATEGORIES = [
    "ของสด",
    "ของแห้ง",
    "นม/โปรตีน",
    "ของใช้",
    "อาหารสำเร็จรูป",
    "อื่นๆ"
]

CATEGORY_ALIAS = {
    "โปรตีน": "นม/โปรตีน",
    "นม": "นม/โปรตีน",
    "สด": "ของสด",
    "แห้ง": "ของแห้ง",
}

# user_id -> product
pending = {}

# ──────────────────────────────────────────────────────────────
# Google Sheets
# ──────────────────────────────────────────────────────────────

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_file(
        "credentials.json",
        scopes=scopes
    )

    client = gspread.authorize(creds)

    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    try:
        sheet = spreadsheet.worksheet(SHEET_NAME)
    except:
        sheet = spreadsheet.add_worksheet(
            title=SHEET_NAME,
            rows=1000,
            cols=10
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
        "ราคา/หน่วย (฿)"
    ]

    current = sheet.row_values(1)

    if not current:
        sheet.insert_row(headers, 1)
        return

    if "category" not in current:
        current.insert(2, "category")
        sheet.delete_rows(1)
        sheet.insert_row(current, 1)

# ──────────────────────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────────────────────

def parse_price(text: str):
    text = text.strip()

    text = re.sub(r'กรัม', 'g', text)
    text = re.sub(r'มล\.?|มิลลิลิตร', 'ml', text)
    text = re.sub(r'กก\.?|กิโลกรัม', 'kg', text)
    text = re.sub(r'ลิตร', 'l', text)
    text = re.sub(r'บาท', '', text)

    pattern = r'^(.+?)\s+([\d.]+)\s+([\d.]+)\s*([a-zA-Zก-๙]+)$'

    match = re.match(pattern, text.strip())

    if not match:
        return None

    name = match.group(1).strip()

    try:
        price = float(match.group(2))
        amount = float(match.group(3))
        unit = match.group(4).strip()
    except ValueError:
        return None

    if amount <= 0:
        return None

    return {
        "name": name,
        "price": price,
        "amount": amount,
        "unit": unit,
        "price_per_unit": round(price / amount, 4),
    }

# ──────────────────────────────────────────────────────────────
# History
# ──────────────────────────────────────────────────────────────

def get_history(sheet, name: str):
    records = sheet.get_all_records()

    return [
        r for r in records
        if r["ชื่อสินค้า"].strip() == name.strip()
    ]


def save_to_sheet(sheet, product: dict, category: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    sheet.append_row([
        now,
        product["name"],
        category,
        product["price"],
        product["amount"],
        product["unit"],
        product["price_per_unit"],
    ])

# ──────────────────────────────────────────────────────────────
# Reply Formatter
# ──────────────────────────────────────────────────────────────

def format_reply(product: dict, category: str, history: list) -> str:
    name = product["name"]
    price = product["price"]
    amount = product["amount"]
    unit = product["unit"]
    ppu = product["price_per_unit"]

    lines = [
        f"✅ บันทึกแล้ว: {name} [{category}]",
        f"ราคา: {price:.0f} ฿ | {amount:.0f} {unit}",
        f"ราคา/{unit}: {ppu:.2f} ฿",
    ]

    if history:
        all_ppu = [
            float(r["ราคา/หน่วย (฿)"])
            for r in history
        ]

        min_ppu = min(all_ppu)
        max_ppu = max(all_ppu)
        avg_ppu = sum(all_ppu) / len(all_ppu)

        last = history[-1]

        last_date = last["วันที่-เวลา"][:10]

        diff_avg = (
            (ppu - avg_ppu) / avg_ppu * 100
        ) if avg_ppu > 0 else 0

        lines.append("──────────────")
        lines.append(f"📦 ซื้อทั้งหมด {len(history)} ครั้ง")
        lines.append(f"🕒 ล่าสุด: {last_date}")

        lines.append(
            f"📊 ช่วงราคา: {min_ppu:.2f} – {max_ppu:.2f} ฿/{unit}"
        )

        lines.append(
            f"📈 ราคาเฉลี่ย: {avg_ppu:.2f} ฿/{unit}"
        )

        if ppu < avg_ppu:
            lines.append(
                f"🟢 รอบนี้ถูกกว่าค่าเฉลี่ย {abs(diff_avg):.1f}%"
            )

        elif ppu > avg_ppu:
            lines.append(
                f"🔴 รอบนี้แพงกว่าค่าเฉลี่ย +{diff_avg:.1f}%"
            )

        else:
            lines.append("⚪ ราคาใกล้เคียงค่าเฉลี่ย")

        if ppu == min_ppu:
            lines.append(
                "🏆 นี่คือราคาที่ถูกที่สุดที่เคยซื้อ!"
            )

    else:
        lines.append("──────────────")
        lines.append(
            "บันทึกครั้งแรก จะเปรียบเทียบได้ในครั้งถัดไป"
        )

    return "\n".join(lines)

# ──────────────────────────────────────────────────────────────
# Quick Reply
# ──────────────────────────────────────────────────────────────

def make_category_quick_reply(product_name: str):

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
        text=f'"{product_name}" อยู่ใน category ไหนคะ?',
        quick_reply=QuickReply(items=buttons)
    )

# ──────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────

def cmd_history(sheet, name: str):

    history = get_history(sheet, name)

    if not history:
        return f"ไม่พบประวัติสินค้า: {name}"

    lines = [
        f"ประวัติ: {name} ({len(history)} ครั้ง)\n"
    ]

    for r in history[-5:]:

        date = r["วันที่-เวลา"][:10]

        price = float(r["ราคา (฿)"])

        ppu = float(r["ราคา/หน่วย (฿)"])

        unit = r["หน่วย"]

        lines.append(
            f"{date}  {price:.0f}฿  →  {ppu:.2f}฿/{unit}"
        )

    return "\n".join(lines)


def cmd_summary(sheet):

    records = sheet.get_all_records()

    if not records:
        return "ยังไม่มีสินค้าที่บันทึก"

    latest = {}

    for r in records:
        latest[r["ชื่อสินค้า"]] = r

    lines = [
        f"สินค้าทั้งหมด {len(latest)} ชนิด\n"
    ]

    for name, r in list(latest.items())[-10:]:

        cat = r.get("category", "")

        ppu = float(r["ราคา/หน่วย (฿)"])

        unit = r["หน่วย"]

        price = float(r["ราคา (฿)"])

        lines.append(
            f"• {name} [{cat}] {price:.0f}฿ ({ppu:.2f}฿/{unit})"
        )

    return "\n".join(lines)


def cmd_summary_by_cat(sheet, cat: str):

    records = sheet.get_all_records()

    filtered = [
        r for r in records
        if r.get("category", "") == cat
    ]

    if not filtered:
        return f"ไม่มีสินค้าใน category: {cat}"

    latest = {}

    for r in filtered:
        latest[r["ชื่อสินค้า"]] = r

    lines = [
        f"[{cat}] {len(latest)} ชนิด\n"
    ]

    for name, r in latest.items():

        ppu = float(r["ราคา/หน่วย (฿)"])

        unit = r["หน่วย"]

        price = float(r["ราคา (฿)"])

        lines.append(
            f"• {name} {price:.0f}฿ ({ppu:.2f}฿/{unit})"
        )

    return "\n".join(lines)

# ──────────────────────────────────────────────────────────────
# Help
# ──────────────────────────────────────────────────────────────

HELP_TEXT = """คำสั่งที่ใช้ได้:

📌 บันทึกราคา:
ไข่ไก่ 79 บาท 10 ฟอง
เนย 215 บาท 250g
นม 45 200ml

📋 ดูข้อมูล:
สรุป
สรุป ของสด
สรุป โปรตีน
ประวัติ ไข่ไก่
ช่วย

📦 Categories:
ของสด / ของแห้ง / นม/โปรตีน
ของใช้ / อาหารสำเร็จรูป / อื่นๆ
"""

# ──────────────────────────────────────────────────────────────
# Webhook
# ──────────────────────────────────────────────────────────────

@app.route("/callback", methods=["POST"])
def callback():

    signature = request.headers.get(
        "X-Line-Signature",
        ""
    )

    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)

    except InvalidSignatureError:
        abort(400)

    return "OK"

# ──────────────────────────────────────────────────────────────
# Text Handler
# ──────────────────────────────────────────────────────────────

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    user_id = event.source.user_id

    text = event.message.text.strip()

    try:

        sheet = get_sheet()

        ensure_header(sheet)

        # category reply
        if text.startswith("__cat__"):

            category = text.replace(
                "__cat__",
                ""
            ).strip()

            if user_id in pending:

                product = pending.pop(user_id)

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
                    text="ไม่พบข้อมูลสินค้า ลองใหม่อีกครั้ง"
                )

            line_bot_api.reply_message(
                event.reply_token,
                reply_msg
            )

            return

        # history
        if text.startswith("ประวัติ"):

            name = text.replace(
                "ประวัติ",
                ""
            ).strip()

            reply_msg = TextSendMessage(
                text=cmd_history(sheet, name)
            )

        # summary
        elif text.startswith("สรุป"):

            arg = text.replace(
                "สรุป",
                ""
            ).strip()

            ```python
def cmd_summary_by_cat(sheet, cat: str):

    # map alias
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
        return f"ไม่มีสินค้าใน category: {cat}"

    latest = {}

    for r in filtered:
        latest[r["ชื่อสินค้า"]] = r

    lines = [
        f"[{cat}] {len(latest)} ชนิด\n"
    ]

    for name, r in latest.items():

        ppu = float(r["ราคา/หน่วย (฿)"])

        unit = r["หน่วย"]

        price = float(r["ราคา (฿)"])

        lines.append(
            f"• {name} {price:.0f}฿ ({ppu:.2f}฿/{unit})"
        )

    return "\n".join(lines)
```


            if arg:

                reply_msg = TextSendMessage(
                    text=cmd_summary_by_cat(sheet, arg)
                )

            else:

                reply_msg = TextSendMessage(
                    text=cmd_summary(sheet)
                )

        # help
        elif text in ("ช่วย", "help", "?"):

            reply_msg = TextSendMessage(
                text=HELP_TEXT
            )

        # parse product
        else:

            product = parse_price(text)

            if product:

                pending[user_id] = product

                reply_msg = make_category_quick_reply(
                    product["name"]
                )

            else:

                reply_msg = TextSendMessage(
                    text=(
                        "พิมพ์ไม่ถูกต้องค่ะ\n\n"
                        "ตัวอย่าง:\n"
                        "ไข่ไก่ 79 บาท 10 ฟอง\n"
                        "เนย 215 บาท 250g\n\n"
                        'พิมพ์ "ช่วย" เพื่อดูคำสั่ง'
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
                text=f"เกิดข้อผิดพลาด: {str(e)}"
            )
        )

# ──────────────────────────────────────────────────────────────
# Image Handler
# ──────────────────────────────────────────────────────────────

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text=(
                "รับรูปแล้วค่ะ!\n"
                "พิมพ์ราคาสินค้าได้เลย\n\n"
                "เช่น:\n"
                "เนย 215 บาท 250g"
            )
        )
    )

# ──────────────────────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
```

```python id="mxq25p"
if text.startswith("__cat__"):

    category = text.replace(
        "__cat__",
        ""
    ).strip()

    if user_id in pending:

        product = pending.pop(user_id)

        history = get_history(
            sheet,
            product["name"]
        )

        # save ก่อน
        save_to_sheet(
            sheet,
            product,
            category
        )

        # แล้วค่อย reply
        reply_msg = TextSendMessage(
            text=format_reply(
                product,
                category,
                history
            )
        )

    else:

        reply_msg = TextSendMessage(
            text="ไม่พบข้อมูลสินค้า ลองใหม่อีกครั้ง"
        )

    line_bot_api.reply_message(
        event.reply_token,
        reply_msg
    )

    return
```

