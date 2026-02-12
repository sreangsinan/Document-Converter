import logging
import os
import sys
import asyncio
import zipfile
import shutil
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)
from PIL import Image
import pytesseract
from pdf2image import convert_from_path
from PyPDF2 import PdfReader, PdfWriter, PdfMerger
import ffmpeg
from flask import Flask # បន្ថែមសម្រាប់ Render

# --- Flask Server សម្រាប់ Render ---
app = Flask(__name__)
@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- ការកំណត់ Tesseract Path សម្រាប់ Docker ---
pytesseract.pytesseract.tesseract_cmd = r'/usr/bin/tesseract'

# បើកការកត់ត្រា (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# ទាញយក Token ពី Environment Variable
BOT_TOKEN = os.getenv("BOT_TOKEN")

# កំណត់ States នៃ Conversation
(SELECT_ACTION, WAITING_FOR_FILE_TO_PDF, WAITING_FOR_PDF_TO_IMG,
 WAITING_FOR_FILES_TO_MERGE, WAITING_FOR_SPLIT_FILE, WAITING_FOR_SPLIT_RANGE,
 WAITING_FOR_COMPRESS, WAITING_FOR_IMG_TO_PDF, WAITING_FOR_IMG_TO_TEXT_FILE,
 WAITING_FOR_AUDIO_FILE, WAITING_FOR_VIDEO_FILE, WAITING_FOR_FILES_TO_ZIP,
 WAITING_FOR_ARCHIVE_TO_EXTRACT) = range(13)

# --- មុខងារ Menu ដើមរបស់អ្នក (រក្សាទុកដូចមុន) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    text = "សួស្តី! ខ្ញុំគឺជា Bot បំប្លែងឯកសារ។ សូមជ្រើសរើសមុខងារខាងក្រោម៖"
    keyboard = [
        [InlineKeyboardButton("📄 ឯកសារទៅជា PDF", callback_query_data='to_pdf'),
         InlineKeyboardButton("🖼️ PDF ទៅជារូបភាព", callback_query_data='pdf_to_img')],
        [InlineKeyboardButton("➕ បញ្ចូល PDF", callback_query_data='merge_pdf'),
         InlineKeyboardButton("✂️ កាត់ PDF", callback_query_data='split_pdf')],
        [InlineKeyboardButton("🗜️ បង្រួម PDF", callback_query_data='compress_pdf'),
         InlineKeyboardButton("🖼️ រូបភាពទៅជា PDF", callback_query_data='img_to_pdf')],
        [InlineKeyboardButton("🔍 រូបភាពទៅជាអក្សរ (OCR)", callback_query_data='img_to_text')],
        [InlineKeyboardButton("🎵 បំប្លែងសំឡេង", callback_query_data='audio_conv'),
         InlineKeyboardButton("🎥 បំប្លែងវីដេអូ", callback_query_data='video_conv')],
        [InlineKeyboardButton("📦 បង្កើត ZIP", callback_query_data='make_zip'),
         InlineKeyboardButton("📂 ពន្លាឯកសារ (Unzip)", callback_query_data='extract_zip')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SELECT_ACTION

# --- មុខងារ OCR ដែលបានកែសម្រួលឱ្យដើរលើ Docker ---
async def receive_img_for_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("សូមផ្ញើរូបភាពដែលមានអក្សរ។")
        return WAITING_FOR_IMG_TO_TEXT_FILE
    
    status_msg = await update.message.reply_text("កំពុងអានអក្សរ... សូមរង់ចាំ។")
    photo_file = await update.message.photo[-1].get_file()
    file_path = f"ocr_{update.message.from_user.id}.jpg"
    await photo_file.download_to_drive(file_path)

    try:
        # បន្ថែមភាសាខ្មែរ និងអង់គ្លេស
        text = pytesseract.image_to_string(Image.open(file_path), lang='khm+eng')
        
        if text.strip():
            # ប្រសិនបើអក្សរវែងពេក ផ្ញើជាឯកសារ
            if len(text) > 4000:
                txt_file = f"result_{update.message.from_user.id}.txt"
                with open(txt_file, "w", encoding="utf-8") as f:
                    f.write(text)
                await update.message.reply_document(open(txt_file, 'rb'))
                os.remove(txt_file)
            else:
                await update.message.reply_text(f"លទ្ធផល OCR:\n\n`{text}`", parse_mode='Markdown')
        else:
            await update.message.reply_text("រកមិនឃើញអក្សរនៅក្នុងរូបភាពនេះទេ។")
    except Exception as e:
        await update.message.reply_text(f"កំហុស OCR: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        await status_msg.delete()
    return await start(update, context)

# --- មុខងារបំប្លែងវីដេអូ (FFmpeg) ---
async def receive_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video or update.message.document
    if not video:
        await update.message.reply_text("សូមផ្ញើឯកសារវីដេអូ។")
        return WAITING_FOR_VIDEO_FILE
    
    status_msg = await update.message.reply_text("កំពុងបំប្លែងវីដេអូ... នេះអាចប្រើពេលបន្តិច។")
    file = await video.get_file()
    input_path = f"in_{update.message.from_user.id}.mp4"
    output_path = f"out_{update.message.from_user.id}.mp4"
    await file.download_to_drive(input_path)

    try:
        # ប្រើ FFmpeg បំប្លែង (ឧទាហរណ៍៖ បង្រួម ឬប្តូរ format)
        ffmpeg.input(input_path).output(output_path, vcodec='libx264', crf=28).run(overwrite_output=True)
        await update.message.reply_video(video=open(output_path, 'rb'))
    except Exception as e:
        await update.message.reply_text(f"កំហុស FFmpeg: {e}")
    finally:
        for p in [input_path, output_path]:
            if os.path.exists(p): os.remove(p)
        await status_msg.delete()
    return await start(update, context)

# (សូមបញ្ចូល Logic ផ្សេងទៀតរបស់អ្នកដូចជា PDF Merge, Split, etc. ចូលមកវិញតាមធម្មតា)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ប្រតិបត្តិការត្រូវបានបោះបង់។")
    return ConversationHandler.END

def main():
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN variable is not set!")
        return

    # រត់ Web Server ក្នុង Thread ផ្សេង
    threading.Thread(target=run_flask, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [
                CallbackQueryHandler(start, pattern='^main_menu$'),
                CallbackQueryHandler(lambda u, c: WAITING_FOR_IMG_TO_TEXT_FILE, pattern='^img_to_text$'),
                CallbackQueryHandler(lambda u, c: WAITING_FOR_VIDEO_FILE, pattern='^video_conv$'),
                # បន្ថែម Callback ផ្សេងៗទៀតរបស់អ្នកនៅទីនេះ...
            ],
            WAITING_FOR_IMG_TO_TEXT_FILE: [MessageHandler(filters.PHOTO, receive_img_for_text)],
            WAITING_FOR_VIDEO_FILE: [MessageHandler(filters.VIDEO | filters.Document.VIDEO, receive_video_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))

    print("Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()