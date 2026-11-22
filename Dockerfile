FROM python:3.12-slim

# Vaqt zonasi — davomat vaqtlari to'g'ri yozilishi uchun juda muhim.
# datetime.now() shu zonaga qarab ishlaydi.
ENV TZ=Asia/Tashkent
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Python sozlamalari: buffersiz chiqish (loglar darhol ko'rinadi), .pyc yozmaslik
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# RenderCV (rezyume PDF'i) — ALOHIDA virtualenv'da: aiogram 3.13 pydantic<2.10 ni,
# RenderCV esa pydantic>=2.12 ni talab qiladi. Bot uni /opt/rendercv/bin/python
# orqali alohida jarayon qilib chaqiradi (services/resume_pdf.py). Kam o'zgargani
# uchun birinchi qatlam — keshdan olinadi.
COPY requirements-rendercv.txt .
RUN python -m venv /opt/rendercv \
    && /opt/rendercv/bin/pip install --no-cache-dir -r requirements-rendercv.txt

# Avval faqat requirements — Docker layer keshidan foydalanish uchun
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Qolgan kodni ko'chirish
COPY . .

# Ma'lumotlar bazasi va tabellar uchun papka (volume shu yerga ulanadi)
RUN mkdir -p data/tables

CMD ["python", "bot.py"]
