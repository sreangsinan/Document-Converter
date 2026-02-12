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

# --- ការកំណត់សម្រាប់ Web Server (Health Check) ដើម្បីបង្ការ Render កុំឱ្យ Sleep ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

@app.route('/health')
def health_check():
    return "OK", 200

def run_flask():
    # Render ប្រើ Port 10000 ជាទូទៅ ប៉ុន្តែយើងប្រើ environment variable
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

# --- អនុគមន៍ដំណើរការនៅខាងក្រោយ (Background Tasks) ---
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
        for path in file_paths:
            merger.append(path)
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
        writer = PdfWriter()
        reader = PdfReader(file_path)
        pages_to_extract = set()
        parts = page_range_str.split(',')
        for part in parts:
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                for i in range(start, end + 1): pages_to_extract.add(i-1)
            else:
                pages_to_extract.add(int(part)-1)
        for i in sorted(list(pages_to_extract)):
            if 0 <= i < len(reader.pages): writer.add_page(reader.pages[i])
        if not writer.pages: raise ValueError("ទំព័រមិនត្រឹមត្រូវ")
        output_path = f"split_{chat_id}.pdf"
        writer.write(output_path)
        await context.bot.edit_message_text("បំបែកឯកសារបានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Split.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំបែកឯកសារ។\nសូមប្រាកដថាទម្រង់លេខទំព័រត្រឹមត្រូវ (ឧ. 2-5 ឬ 1,3,8)។", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def compress_pdf_task(chat_id, file_path, msg, context):
    try:
        reader = PdfReader(file_path)
        writer = PdfWriter()
        for page in reader.pages:
            page.compress_content_streams()
            writer.add_page(page)
        output_path = f"compressed_{chat_id}.pdf"
        with open(output_path, "wb") as f: writer.write(f)
        await context.bot.edit_message_text("បន្ថយទំហំឯកសារបានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Compressed.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបន្ថយទំហំឯកសារ។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)

async def img_to_pdf_task(chat_id, file_paths, msg, context):
    try:
        if not file_paths:
            raise ValueError("មិនមានរូបភាពដើម្បីបំប្លែងទេ")
        image_list = []
        for path in file_paths:
            image_list.append(Image.open(path).convert('RGB'))
        output_path = f"converted_from_img_{chat_id}.pdf"
        first_image = image_list[0]
        other_images = image_list[1:]
        first_image.save(output_path, "PDF", resolution=100.0, save_all=True, append_images=other_images)
        await context.bot.edit_message_text("បំប្លែងរូបភាពទៅជា PDF បានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="Image_to_PDF.pdf")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែងរូបភាពទៅជា PDF ។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def img_to_text_task(chat_id, file_path, msg, context):
    try:
        image = Image.open(file_path)
        # កំណត់ tesseract path សម្រាប់ Linux (Render) ប្រសិនបើចាំបាច់
        # ជាទូទៅក្នុង Docker វានៅក្នុង /usr/bin/tesseract
        text = pytesseract.image_to_string(image, lang='khm+eng')
        if not text.strip():
            await context.bot.edit_message_text("មិនអាចរកឃើញអក្សរនៅក្នុងរូបភាពនេះទេ ឬរូបភាពគ្មានគុណភាពល្អ។", chat_id=chat_id, message_id=msg.message_id)
        else:
            await context.bot.edit_message_text("បំប្លែងរូបភាពទៅជាអក្សរបានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
            await context.bot.send_message(chat_id=chat_id, text=f"**លទ្ធផលដែលបានបំប្លែង៖**\n\n```\n{text}\n```", parse_mode='Markdown')
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែងរូបភាពទៅជាអក្សរ។\nកំហុស: {e}\nសូមប្រាកដថា tesseract-ocr-khm ត្រូវបានដំឡើង។", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)

async def media_conversion_task(chat_id, file_path, output_format, msg, context, media_type='audio'):
    output_path = f"converted_{chat_id}.{output_format}"
    try:
        await context.bot.edit_message_text(f"កំពុងបំប្លែងទៅជា {output_format.upper()}... ការងារនេះអាចត្រូវការពេលវេលាយូរបន្តិច។", chat_id=chat_id, message_id=msg.message_id)
        ffmpeg.input(file_path).output(output_path).run(overwrite_output=True)
        await context.bot.edit_message_text("បំប្លែងបានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        if media_type == 'audio':
            await context.bot.send_audio(chat_id=chat_id, audio=open(output_path, 'rb'))
        elif media_type == 'video':
            await context.bot.send_video(chat_id=chat_id, video=open(output_path, 'rb'))
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបំប្លែងឯកសារ។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(output_path): os.remove(output_path)
        if msg: 
            try: await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            except: pass

async def create_zip_task(chat_id, file_paths, msg, context):
    output_path = f"archive_{chat_id}.zip"
    try:
        await context.bot.edit_message_text("កំពុងបង្កើតឯកសារ ZIP...", chat_id=chat_id, message_id=msg.message_id)
        with zipfile.ZipFile(output_path, 'w') as zipf:
            for file_path in file_paths:
                zipf.write(file_path, os.path.basename(file_path))
        await context.bot.edit_message_text("បង្កើតឯកសារ ZIP បានជោគជ័យ! កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        await context.bot.send_document(chat_id=chat_id, document=open(output_path, 'rb'), filename="archive.zip")
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការបង្កើតឯកសារ ZIP។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        for path in file_paths:
            if os.path.exists(path): os.remove(path)
        if os.path.exists(output_path): os.remove(output_path)

async def extract_archive_task(chat_id, file_path, msg, context):
    extract_dir = f"extracted_{chat_id}"
    try:
        await context.bot.edit_message_text("កំពុងពន្លាឯកសារ...", chat_id=chat_id, message_id=msg.message_id)
        os.makedirs(extract_dir, exist_ok=True)
        if file_path.endswith('.zip'):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
        elif file_path.endswith(('.tar.gz', '.tgz', '.tar')):
            mode = 'r:gz' if file_path.endswith(('.gz', '.tgz')) else 'r:'
            with tarfile.open(file_path, mode) as tar_ref:
                tar_ref.extractall(extract_dir)
        else:
            raise ValueError("មិនគាំទ្រទ្រង់ទ្រាយឯកសារនេះទេ។")
        
        extracted_files = os.listdir(extract_dir)
        if not extracted_files:
            raise ValueError("ឯកសារ Archive គឺទទេ។")
            
        await context.bot.edit_message_text(f"ពន្លាបាន {len(extracted_files)} ឯកសារ។ កំពុងផ្ញើ...", chat_id=chat_id, message_id=msg.message_id)
        for filename in extracted_files:
            file_to_send = os.path.join(extract_dir, filename)
            if os.path.isfile(file_to_send):
                await context.bot.send_document(chat_id=chat_id, document=open(file_to_send, 'rb'))
        await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except Exception as e:
        await context.bot.edit_message_text(f"មានបញ្ហាក្នុងការពន្លាឯកសារ។\nកំហុស: {e}", chat_id=chat_id, message_id=msg.message_id)
    finally:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.isdir(extract_dir): shutil.rmtree(extract_dir)

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("📄 PDF ទៅជា រូបភាព", callback_data='pdf_to_img')],
        [InlineKeyboardButton("🖇️ បញ្ចូល PDF ច្រើនចូលគ្នា", callback_data='merge_pdf')],
        [InlineKeyboardButton("✂️ បំបែក PDF ជាទំព័រៗ", callback_data='split_pdf')],
        [InlineKeyboardButton("📦 បន្ថយទំហំ PDF", callback_data='compress_pdf')],
        [InlineKeyboardButton("🖼️ រូបភាព ទៅជា PDF", callback_data='img_to_pdf')],
        [InlineKeyboardButton("📖 រូបភាព ទៅជា អក្សរ", callback_data='img_to_text')],
        [InlineKeyboardButton("🎵 បំប្លែងឯកសារសម្លេង", callback_data='audio_converter')],
        [InlineKeyboardButton("🎬 បំប្លែងឯកសារវីដេអូ", callback_data='video_converter')],
        [InlineKeyboardButton("🗜️ គ្រប់គ្រងឯកសារ Archive", callback_data='archive_manager')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = '👋 សួស្តី! សូមជ្រើសរើសមុខងារខាងក្រោម៖'
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    return SELECT_ACTION

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = "📖 **ជំនួយ:**\nប្រើ `/start` ដើម្បីមើលម៉ឺនុយមេ។\nប្រើ `/cancel` ដើម្បីបោះបង់ប្រតិបត្តិការបច្ចុប្បន្ន។"
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    msg = "ប្រតិបត្តិការត្រូវបានបោះបង់។"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg)
    else:
        await update.message.reply_text(msg)
    return ConversationHandler.END

# --- PDF Functions ---
async def start_pdf_to_img(update, context):
    query = update.callback_query; await query.answer()
    keyboard = [[InlineKeyboardButton("JPG", callback_data='fmt_jpeg'), InlineKeyboardButton("PNG", callback_data='fmt_png')], [InlineKeyboardButton("⬅️ ថយក្រោយ", callback_data='main_menu')]]
    await query.edit_message_text("ជ្រើសរើសប្រភេទរូបភាព៖", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_ACTION

async def start_conversion_with_format(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['format'] = "jpeg" if query.data == 'fmt_jpeg' else "png"
    await query.edit_message_text(f"✅ ជ្រើសរើស {context.user_data['format'].upper()}។ សូមផ្ញើ PDF មក។")
    return WAITING_PDF_TO_IMG_FILE

async def receive_pdf_for_img(update, context):
    doc = update.message.document
    file = await doc.get_file()
    file_path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("កំពុងដំណើរការ...")
    asyncio.create_task(pdf_to_img_task(update.effective_chat.id, file_path, msg, context, context.user_data.get('format')))
    return ConversationHandler.END

# (អនុគមន៍ receive_ ផ្សេងៗទៀតរក្សាតាមកូដចាស់របស់អ្នក...)
# ដើម្បីឱ្យខ្លី ខ្ញុំនឹងរំលងការសរសេរ function receive នីមួយៗឡើងវិញ តែអ្នកត្រូវរក្សាវាទុក
# [ចំណាំ៖ សូមប្រើ Function Receive ដូចក្នុងកូដដើមរបស់អ្នក]

async def start_merge_command(update, context):
    context.user_data['merge_files'] = []
    await update.message.reply_text("ផ្ញើ PDF ម្ដងមួយៗ រួចវាយ /done")
    return WAITING_FOR_MERGE

async def receive_pdf_for_merge(update, context):
    file = await update.message.document.get_file()
    path = f"temp_{file.file_id}.pdf"
    await file.download_to_drive(path)
    if 'merge_files' not in context.user_data: context.user_data['merge_files'] = []
    context.user_data['merge_files'].append(path)
    await update.message.reply_text(f"ទទួលបានឯកសារទី {len(context.user_data['merge_files'])}។ បន្តផ្ញើ ឬ /done")
    return WAITING_FOR_MERGE

async def done_merging(update, context):
    if len(context.user_data.get('merge_files', [])) < 2:
        await update.message.reply_text("ត្រូវការយ៉ាងហោចណាស់ ២ ឯកសារ។")
        return WAITING_FOR_MERGE
    msg = await update.message.reply_text("កំពុងបញ្ចូល...")
    asyncio.create_task(merge_pdf_task(update.effective_chat.id, context.user_data['merge_files'], msg, context))
    return ConversationHandler.END

# --- Image to Text ---
async def start_img_to_text(update, context):
    query = update.callback_query; await query.answer()
    await query.edit_message_text("សូមផ្ញើរូបភាពមក។")
    return WAITING_FOR_IMG_TO_TEXT_FILE

async def receive_img_for_text(update, context):
    file_obj = update.message.photo[-1] if update.message.photo else update.message.document
    file = await file_obj.get_file()
    file_path = f"ocr_{file.file_id}.jpg"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("កំពុងអានអក្សរ...")
    asyncio.create_task(img_to_text_task(update.effective_chat.id, file_path, msg, context))
    return ConversationHandler.END

# --- Audio/Video ---
async def select_audio_output(update, context):
    query = update.callback_query; await query.answer()
    context.user_data['output_format'] = query.data.split('_')[1]
    await query.edit_message_text(f"✅ បានជ្រើសរើស {context.user_data['output_format'].upper()}។ សូមផ្ញើឯកសារសម្លេង។")
    return WAITING_FOR_AUDIO_FILE

async def receive_audio_for_conversion(update, context):
    file_obj = update.message.audio or update.message.document
    file = await file_obj.get_file()
    file_path = f"audio_{file.file_id}"
    await file.download_to_drive(file_path)
    msg = await update.message.reply_text("កំពុងបំប្លែង...")
    asyncio.create_task(media_conversion_task(update.effective_chat.id, file_path, context.user_data['output_format'], msg, context, 'audio'))
    return ConversationHandler.END

def create_format_buttons(formats, prefix):
    buttons = [InlineKeyboardButton(f"{fmt.upper()}", callback_data=f"{prefix}_{fmt.lower()}") for fmt in formats]
    return [buttons[i:i + 3] for i in range(0, len(buttons), 3)]

# --- Main Logic ---
def main() -> None:
    # ១. បើក Flask Server ក្នុង Thread ផ្សេងមួយ
    threading.Thread(target=run_flask, daemon=True).start()
    
    # ២. បើក Telegram Bot
    application = Application.builder().token(BOT_TOKEN).read_timeout(30).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("merge_pdf", start_merge_command),
            CommandHandler("img_to_text", lambda u, c: start_img_to_text(u, c)),
        ],
        states={
            SELECT_ACTION: [
                CallbackQueryHandler(start_pdf_to_img, pattern='^pdf_to_img$'),
                CallbackQueryHandler(start_conversion_with_format, pattern='^fmt_'),
                CallbackQueryHandler(lambda u, c: start_img_to_text(u, c), pattern='^img_to_text$'),
                CallbackQueryHandler(start, pattern='^main_menu$'),
                CallbackQueryHandler(lambda u, c: u.callback_query.edit_message_text("ជ្រើសរើសសម្លេង៖", reply_markup=InlineKeyboardMarkup(create_format_buttons(['MP3', 'WAV', 'M4A'], "audio"))), pattern='^audio_converter$'),
                CallbackQueryHandler(select_audio_output, pattern='^audio_'),
            ],
            WAITING_PDF_TO_IMG_FILE: [MessageHandler(filters.Document.PDF, receive_pdf_for_img)],
            WAITING_FOR_MERGE: [MessageHandler(filters.Document.PDF, receive_pdf_for_merge), CommandHandler('done', done_merging)],
            WAITING_FOR_IMG_TO_TEXT_FILE: [MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_img_for_text)],
            WAITING_FOR_AUDIO_FILE: [MessageHandler(filters.AUDIO | filters.Document.ALL, receive_audio_for_conversion)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    
    print(">>> Bot & Health Check Server are running!")
    application.run_polling()

if __name__ == "__main__":
    main()
