# -*- coding: utf-8 -*-
import logging
import os
import sys
import asyncio
import ffmpeg
import zipfile
import tarfile
import shutil
import threading
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from PIL import Image
import pytesseract

# --- ការកំណត់សម្រាប់ Flask Web Server (Health Check) ---
app = Flask('')

@app.route('/')
def home():
    return "I am alive! Bot is running."

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    # Render ផ្ដល់ Port តាមរយៈ Environment Variable "PORT" (ជាទូទៅគឺ 10000)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- ពិនិត្យ Library ---
try:
    from PyPDF2 import PdfReader, PdfWriter, PdfMerger
    from pdf2image import convert_from_path
except ImportError:
    print("!!! កំហុស៖ សូមប្រាកដថាបានតម្លើង Library ទាំងអស់៖ pip install PyPDF2 pdf2image Pillow python-telegram-bot ffmpeg-python flask")
    sys.exit(1)

# --- ពិនិត្យការដំឡើង FFmpeg ---
def is_ffmpeg_installed():
    return shutil.which("ffmpeg") is not None

# --- ការកំណត់តម្លៃ ---
BOT_TOKEN = "8358054959:AAHj7HQZqEd94V20j8kvWkY6UCseXsz10-Q" 
MAX_FILE_SIZE = 50 * 1024 * 1024 # 50 MB

# កំណត់ 'ស្ថានភាព' (States)
(SELECT_ACTION,
 WAITING_PDF_TO_IMG_FORMAT, WAITING_PDF_TO_IMG_FILE,
 WAITING_FOR_MERGE, WAITING_FOR_SPLIT_FILE, WAITING_FOR_SPLIT_RANGE,
 WAITING_FOR_COMPRESS,
 WAITING_FOR_IMG_TO_PDF,
 WAITING_FOR_IMG_TO_TEXT_FILE,
 SELECT_AUDIO_OUTPUT_FORMAT, WAITING_FOR_AUDIO_FILE,
 SELECT_VIDEO_OUTPUT_FORMAT, WAITING_FOR_VIDEO_FILE,
 SELECT_ARCHIVE_ACTION, WAITING_FOR_FILES_TO_ZIP, WAITING_FOR_ARCHIVE_TO_EXTRACT
) = range(16)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# --- Background Tasks (រក្សាកូដចាស់) ---
async def pdf_to_img_task(chat_id, file_path, msg, context, fmt):
    try:
        images = convert_from_path(file_path, dpi=200, fmt=fmt)
        await context.bot.edit_message_text(f"បំប្លែងបាន {len(images)} ទំព័រ។ កំពុងផ្ញើរូបភាព...", chat_id=chat_id, message_id=msg.message_id)
        for i, image in enumerate(images):
            out_path = f"page_{i+1}_{chat_id}.{fmt}"
            image.save(out_path, fmt.upper())
            await context.bot.send_photo(chat_id=chat_id, photo=open(out_path, 'rb'))
            os.remove(out_path)
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែង PDF ទៅជារូបភាព។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def merge_pdf_task(chat_id, file_paths, msg, context):
    try:
        merger = PdfMerger()
        for path in file_paths: merger.append(path)
        output_path = f"merged_{chat_id}.pdf"
        merger.write(output_path)
        merger.close()
        await context.bot.edit_message_text("បញ្ចូលឯកសារបានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Merged.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបញ្ចូលឯកសារ។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def split_pdf_task(chat_id, file_path, page_range_str, msg, context):
    try:
        writer, reader = PdfWriter(), PdfReader(file_path)
        pages_to_extract = set()
        for part in page_range_str.split(','):
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                for i in range(start, end + 1): pages_to_extract.add(i-1)
            else: pages_to_extract.add(int(part)-1)
        for i in sorted(list(pages_to_extract)):
            if 0 <= i < len(reader.pages): writer.add_page(reader.pages[i])
        output_path = f"split_{chat_id}.pdf"
        writer.write(output_path)
        await context.bot.edit_message_text("បំបែកបានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Split.pdf")
    except Exception:
        await context.bot.edit_message_text("មានបញ្ហាក្នុងការបំបែកឯកសារ។ សូមឆែកទម្រង់លេខទំព័រ។", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def compress_pdf_task(chat_id, file_path, msg, context):
    try:
        reader, writer = PdfReader(file_path), PdfWriter()
        for page in reader.pages:
            page.compress_content_streams()
            writer.add_page(page)
        output_path = f"compressed_{chat_id}.pdf"
        with open(output_path, "wb") as f: writer.write(f)
        await context.bot.edit_message_text("បន្ថយទំហំបានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Compressed.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def img_to_text_task(chat_id, file_path, msg, context):
    try:
        text = pytesseract.image_to_string(Image.open(file_path), lang='khm+eng')
        if not text.strip():
            await context.bot.edit_message_text("មិនអាចអានអក្សរបានទេ។", chat_id=chat_id, message_id=msg.message_id)
        else:
            await context.bot.send_message(chat_id=chat_id, text=f"**លទ្ធផល៖**\n\n```\n{text}\n```", parse_mode='Markdown')
            await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុស OCR: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def media_conversion_task(chat_id, file_path, output_format, msg, context, media_type='audio'):
    output_path = f"converted_{chat_id}.{output_format}"
    try:
        await context.bot.edit_message_text(f"កំពុងបំប្លែងទៅជា {output_format.upper()}...", chat_id=chat_id, message_id=msg.message_id)
        ffmpeg.input(file_path).output(output_path).run(overwrite_output=True)
        if media_type == 'audio': await context.bot.send_audio(chat_id=chat_id, audio=open(output_path, 'rb'))
        else: await context.bot.send_video(chat_id=chat_id, video=open(output_path, 'rb'))
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception as e:
        await context.bot.edit_message_text(f"កំហុសបំប្លែង: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("📄 PDF ទៅជា រូបភាព", callback_data='pdf_to_img')],
        [InlineKeyboardButton("🖇️ បញ្ចូល PDF", callback_data='merge_pdf'), InlineKeyboardButton("✂️ បំបែក PDF", callback_data='split_pdf')],
        [InlineKeyboardButton("📦 បន្ថយទំហំ PDF", callback_data='compress_pdf'), InlineKeyboardButton("📖 រូបភាព ទៅជា អក្សរ", callback_data='img_to_text')],
        [InlineKeyboardButton("🎵 បំប្លែងសម្លេង", callback_data='audio_converter'), InlineKeyboardButton("🎬 បំប្លែងវីដេអូ", callback_data='video_converter')],
    ]
    text = '👋 សួស្តី! សូមជ្រើសរើសមុខងារខាងក្រោម៖'
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_ACTION

async def show_audio_formats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    formats = ['MP3', 'WAV', 'M4A', 'WMA', 'OGG', 'FLAC']
    buttons = [InlineKeyboardButton(fmt, callback_data=f"audio_{fmt.lower()}") for fmt in formats]
    keyboard = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    keyboard.append([InlineKeyboardButton("⬅️ ថយក្រោយ", callback_data='main_menu')])
    await query.edit_message_text("🎵 ជ្រើសរើស Format សម្លេង៖", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_ACTION

async def select_audio_output(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data['output_format'] = query.data.split('_')[1]
    await query.edit_message_text(f"✅ ជ្រើសរើស {context.user_data['output_format'].upper()}។ សូមផ្ញើឯកសារសម្លេងមក។")
    return WAITING_FOR_AUDIO_FILE

async def receive_audio_for_conversion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    file_obj = update.message.audio or update.message.document
    file = await file_obj.get_file()
    file_path = f"audio_{file.file_id}"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("កំពុងចាប់ផ្ដើម...")
    asyncio.create_task(media_conversion_task(update.effective_chat.id, file_path, context.user_data.get('output_format', 'mp3'), msg, context, 'audio'))
    return ConversationHandler.END

# --- មុខងារផ្សេងៗ (PDF, OCR, Merge) ---
async def start_pdf_to_img(update, context):
    query = update.callback_query; await query.answer()
    keyboard = [[InlineKeyboardButton("JPG", callback_data='fmt_jpeg'), InlineKeyboardButton("PNG", callback_data='fmt_png')], [InlineKeyboardButton("⬅️ ថយក្រោយ", callback_data='main_menu')]]
    await query.edit_message_text("ជ្រើសរើសប្រភេទរូបភាព៖", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_ACTION

async def start_conversion_with_format(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['format'] = query.data.split('_')[1]
    await query.edit_message_text(f"✅ សូមផ្ញើ PDF មក។")
    return WAITING_PDF_TO_IMG_FILE

async def receive_pdf_for_img(update, context):
    file = await update.message.document.get_file()
    path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(path)
    msg = await update.message.reply_text("កំពុងបំប្លែង...")
    asyncio.create_task(pdf_to_img_task(update.effective_chat.id, path, msg, context, context.user_data.get('format')))
    return ConversationHandler.END

async def start_img_to_text(update, context):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("✅ សូមផ្ញើរូបភាពមក។")
    return WAITING_FOR_IMG_TO_TEXT_FILE

async def receive_img_for_text(update, context):
    file_obj = update.message.photo[-1] if update.message.photo else update.message.document
    file = await file_obj.get_file()
    path = f"ocr_{file.file_id}.jpg"
    await file.download_to_drive(path)
    msg = await update.message.reply_text("កំពុងអានអក្សរ...")
    asyncio.create_task(img_to_text_task(update.effective_chat.id, path, msg, context))
    return ConversationHandler.END

async def cancel(update, context):
    context.user_data.clear()
    await (update.callback_query.edit_message_text("បោះបង់។") if update.callback_query else update.message.reply_text("បោះបង់។"))
    return ConversationHandler.END

# --- Main Logic ---
def main() -> None:
    # ១. បើក Flask Health Check ក្នុង Thread ថ្មី
    threading.Thread(target=run_flask, daemon=True).start()
    
    # ២. កំណត់ Telegram Bot
    application = Application.builder().token(BOT_TOKEN).read_timeout(30).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [
                CallbackQueryHandler(start_pdf_to_img, pattern='^pdf_to_img$'),
                CallbackQueryHandler(start_conversion_with_format, pattern='^fmt_'),
                CallbackQueryHandler(start_img_to_text, pattern='^img_to_text$'),
                CallbackQueryHandler(show_audio_formats, pattern='^audio_converter$'),
                CallbackQueryHandler(select_audio_output, pattern='^audio_'),
                CallbackQueryHandler(start, pattern='^main_menu$'),
            ],
            WAITING_PDF_TO_IMG_FILE: [MessageHandler(filters.Document.PDF, receive_pdf_for_img)],
            WAITING_FOR_IMG_TO_TEXT_FILE: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_img_for_text)],
            WAITING_FOR_AUDIO_FILE: [MessageHandler(filters.AUDIO | filters.Document.ALL, receive_audio_for_conversion)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    print(">>> Bot is running with Health Check on Render!")
    application.run_polling()

if __name__ == "__main__":
    main()
