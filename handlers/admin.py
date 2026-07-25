from datetime import date

from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile

from config import OWNER_ID
import database as db
from services import excel

router = Router()
router.message.filter(F.chat.type == "private")


@router.message(Command("tabel"))
async def send_current_month_tabel(message: types.Message):
    """Joriy oy uchun tabelni generatsiya qilib, FAQAT egasiga (OWNER_ID) yuboradi."""
    if message.from_user.id != OWNER_ID:
        return  # boshqa hech kimga hatto rad javobi ham bermaydi

    today = date.today()

    await message.answer("⏳ Tabel tayyorlanmoqda...")

    file_path = await excel.generate_monthly_tabel(today.year, today.month)

    await message.answer_document(
        FSInputFile(file_path),
        caption=f"📊 {today.month:02d}.{today.year} oyi uchun tabel",
    )


@router.message(Command("bolim_ozgartir"))
async def reset_department(message: types.Message, command: CommandObject):
    """
    /bolim_ozgartir            — o'zingizning bo'limingizni tozalaydi
    /bolim_ozgartir 123456789  — ko'rsatilgan xodimning bo'limini tozalaydi
    Ikkalasi ham FAQAT OWNER_ID uchun ishlaydi. Tozalangach, o'sha odam
    /start bosganda bo'lim tanlash savoli yana beriladi.
    """
    if message.from_user.id != OWNER_ID:
        return

    target_id = message.from_user.id

    if command.args:
        try:
            target_id = int(command.args.strip())
        except ValueError:
            await message.answer("⚠️ Foydalanuvchi ID raqam bo'lishi kerak.\nMisol: /bolim_ozgartir 123456789")
            return

    await db.reset_employee_department(target_id)

    if target_id == message.from_user.id:
        await message.answer("✅ Bo'limingiz tozalandi. /start bosib qaytadan tanlang.")
    else:
        await message.answer(f"✅ {target_id} uchun bo'lim tozalandi. U /start bosib qaytadan tanlashi mumkin.")