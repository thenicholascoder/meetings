FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# LibreOffice converts uploaded PowerPoint files to PDF. Speaker notes are
# read separately and are not drawn onto those pages. Slim installs omit the
# /usr/bin/soffice launcher, so link the program binary onto PATH.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libreoffice-impress \
    libreoffice-uiconfig-impress \
    && ln -sf /usr/lib/libreoffice/program/soffice /usr/local/bin/soffice \
    && test -x /usr/local/bin/soffice \
    && rm -rf /var/lib/apt/lists/*

# Microsoft core fonts, Calibri/Cambria substitutes, and Google Slides families.
# Build context is the repository root so this script can be shared with the converter.
COPY infra/fonts/install-slide-fonts.sh /tmp/install-slide-fonts.sh
RUN sed -i 's/\r$//' /tmp/install-slide-fonts.sh \
    && chmod +x /tmp/install-slide-fonts.sh \
    && /tmp/install-slide-fonts.sh \
    && rm /tmp/install-slide-fonts.sh

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120"]
