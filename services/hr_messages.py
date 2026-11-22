"""Messages the candidate receives (HTML). The admin sees exactly these in the previews."""
from datetime import datetime
from html import escape

from services import hr_format


def map_link(lat: float, lon: float) -> str:
    return f'<a href="https://yandex.uz/maps/?pt={lon},{lat}&amp;z=17&amp;l=map">xaritada ochish</a>'


def _interview_lines(interview_at: str, place: str, lat=None, lon=None, note: str | None = None) -> list:
    moment = datetime.strptime(interview_at, "%Y-%m-%d %H:%M")
    day = hr_format.interview_time(interview_at).rsplit(", ", 1)[0]  # "25-noyabr, seshanba"
    lines = [
        f"📅 Sana: <b>{day}</b>",
        f"🕙 Vaqt: <b>{moment:%H:%M}</b>",
        f"📍 Manzil: <b>{escape(place)}</b>",
    ]
    if lat is not None and lon is not None:
        lines.append(f"🗺 Xaritada: {map_link(lat, lon)}")
    if note:
        lines.append(f"📝 {escape(note)}")
    return lines


def invitation(full_name: str, interview_at: str, place: str, lat=None, lon=None,
               note: str | None = None, moved: bool = False) -> str:
    if moved:
        head = [f"🔁 Hurmatli {escape(full_name)}!", "", "Suhbat vaqti o'zgartirildi. Yangi vaqt:"]
    else:
        head = [f"🎉 Hurmatli {escape(full_name)}!", "",
                "Arizangiz ko'rib chiqildi va Sizni suhbatga taklif qilamiz."]
    return "\n".join(head + [""] + _interview_lines(interview_at, place, lat, lon, note)
                     + ["", "Lyuba jamoasi"])


def reminder(full_name: str, interview_at: str, place: str, lat=None, lon=None, note: str | None = None,
             today: bool = False) -> str:
    when = "bugun" if today else "ertaga"
    return "\n".join([f"⏰ Hurmatli {escape(full_name)}, {when} suhbatingiz bor!", ""]
                     + _interview_lines(interview_at, place, lat, lon, note)
                     + ["", "Sizni kutamiz! Lyuba jamoasi"])


def rejection(full_name: str, position: str | None) -> str:
    applied = (f"Lyuba kompaniyasidagi {escape(position)} lavozimiga" if position
               else "Lyuba kompaniyasiga")
    return (
        f"Hurmatli {escape(full_name)},\n\n"
        f"{applied} ariza topshirganingiz va jamoamizga qo‘shilishga qiziqish bildirganingiz uchun tashakkur.\n"
        "Arizangizni diqqat bilan ko‘rib chiqqach, ushbu lavozim bo‘yicha hozirgi talablarimizga tajribasi "
        "ko‘proq mos keladigan boshqa nomzodlar bilan jarayonni davom ettirishga qaror qildik.\n"
        "Ariza topshirishga vaqt ajratganingiz uchun minnatdorchilik bildiramiz hamda ish qidirish "
        "jarayonida sizga omad tilaymiz.\n\n"
        "Hurmat bilan,\n"
        "Lyuba jamoasi"
    )


def hired(full_name: str, position: str | None) -> str:
    where = f" <b>{escape(position)}</b> lavozimiga" if position else ""
    return (f"🎉 Tabriklaymiz, {escape(full_name)}!\n\n"
            f"Siz{where} ishga qabul qilindingiz. Jamoamizga xush kelibsiz!\n\n"
            "Ishni boshlash uchun /start bosing.")


def no_show(full_name: str) -> str:
    return (f"Hurmatli {escape(full_name)},\n\n"
            "Belgilangan vaqtda suhbatga kelmaganingiz sababli arizangiz yopildi. "
            "Xohlasangiz, 7 kundan keyin qayta ariza topshirishingiz mumkin.\n\n"
            "Lyuba jamoasi")
