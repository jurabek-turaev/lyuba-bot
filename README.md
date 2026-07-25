# Attendance Bot (Davomat boti)

Telegram bot orqali xodimlarning ishga kelish-ketishini joylashuv (GPS) va dumaloq
video tasdig'i bilan qayd qiluvchi tizim. Oy oxirida `/tabel` orqali Excel hisobot beradi.

## Imkoniyatlari

- Bo'lim/filial tanlash (Savdo, Ishlab chiqarish, Haydovchi, I.T.R)
- Dumaloq video (video_note) tasdig'i — guruhga avtomatik yuboriladi
- GPS masofa tekshiruvi (belgilangan `MAX_DISTANCE` metr ichida bo'lishi shart)
- "Keldim" / "Ketdim" qayd qilish, ish soatini avtomatik hisoblash
- Tungi smenani to'g'ri hisoblash (kun chegarasidan o'tsa ham)
- "Ketdim" bosilmasa — smenani avtomatik yopish (auto-close)
- Oylik Excel tabel (`/tabel` — faqat bot egasiga)

---

## 1. Sozlash (.env)

`.env.example` ni nusxalab, `.env` yarating va qiymatlarni to'ldiring:

```bash
cp .env.example .env
```

| O'zgaruvchi | Izoh |
|-------------|------|
| `BOT_TOKEN` | BotFather'dan olingan token |
| `OWNER_ID` | Bot egasining Telegram ID'si (`/tabel` faqat shunga yuboriladi) |
| `GROUP_ID` | Savdo filiallari uchun umumiy guruh ID'si |
| `PRODUCTION_LAT` / `PRODUCTION_LON` | Zavod hududi koordinatalari |
| `PRODUCTION_GROUP_ID` | Ishlab chiqarish guruhi ID'si |
| `MAX_DISTANCE` | Ruxsat etilgan masofa (metr), masalan `200` |
| `MAX_SHIFT_HOURS` | Maksimal smena (soat), masalan `12` |

> ⚠️ `.env` faylni hech kimga bermang va git'ga yuklamang — u ichida bot tokeni bor.

---

## 2. Docker bilan ishga tushirish (tavsiya etiladi)

Serverda Docker va Docker Compose o'rnatilgan bo'lishi kerak.

```bash
docker compose up -d --build
```

Loglarni ko'rish:

```bash
docker compose logs -f
```

To'xtatish:

```bash
docker compose down
```

Ma'lumotlar bazasi (`data/attendance.db`) va tabellar (`data/tables/`) host mashinada
`./data` papkasida saqlanadi — konteyner o'chirilsa ham yo'qolmaydi.

---

## 3. Docker'siz ishga tushirish (lokal test uchun)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

---

## Vaqt zonasi haqida

Bot davomat vaqtlarini serverning mahalliy vaqtiga qarab yozadi. Docker'da vaqt zonasi
`Asia/Tashkent` qilib o'rnatilgan (`Dockerfile` va `docker-compose.yml` da). Docker'siz
ishlatsangiz, server vaqti to'g'ri sozlanganiga ishonch hosil qiling.

---

## Xavfsizlik eslatmalari

- Bot GPS asosida ishlaydi. Telegram joylashuvni soxtalashtirish (fake GPS) texnik
  jihatdan mumkin — bu Telegram'ning tabiiy cheklovi. Ishonchli xodimlar uchun mo'ljallangan.
- Barcha maxfiy sozlamalar `.env` da; kodda ochiq token yo'q.
