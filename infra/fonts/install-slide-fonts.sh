#!/bin/sh
# Install the typefaces LibreOffice needs so a PowerPoint export matches the
# deck. Google Slides and PowerPoint name fonts directly (Arial, Calibri,
# Roboto, ...). Missing faces are substituted with DejaVu, which changes
# wrapping and spacing in the PDF.
set -eu

export DEBIAN_FRONTEND=noninteractive
UA="Mozilla/5.0 (Linux; U; Android 4.4.4; en-us; SM-G900V Build/KTU84P) AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Mobile Safari/534.30"
FONT_DIR=/usr/local/share/fonts/google-slides
SELAWIK_DIR=/usr/local/share/fonts/selawik

enable_contrib() {
  if grep -RqsE '(^|[[:space:]])contrib([[:space:]]|$)' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
    return 0
  fi
  codename=$(. /etc/os-release && printf '%s' "$VERSION_CODENAME")
  printf 'deb http://deb.debian.org/debian %s contrib\n' "$codename" > /etc/apt/sources.list.d/contrib.list
}

install_packages() {
  enable_contrib
  apt-get update
  apt-get install -y --no-install-recommends \
    fontconfig \
    curl \
    ca-certificates \
    cabextract \
    unzip \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-liberation \
    fonts-liberation2 \
    fonts-dejavu-core \
    fonts-noto-core \
    fonts-noto-cjk \
    fonts-noto-color-emoji
  # Core web fonts (Arial, Georgia, Verdana, Trebuchet MS, Comic Sans MS,
  # Impact, Times New Roman, Courier New, Arial Black). The installer downloads
  # them during build; the Microsoft EULA is accepted here.
  echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | debconf-set-selections
  attempt=0
  while [ "$attempt" -lt 3 ]; do
    if apt-get install -y --no-install-recommends ttf-mscorefonts-installer; then
      return 0
    fi
    attempt=$((attempt + 1))
    echo "retrying Microsoft core fonts ($attempt)" >&2
    sleep 3
  done
  echo "Microsoft core fonts failed to install" >&2
  return 1
}

download_family() {
  family=$1
  encoded=$(printf '%s' "$family" | sed 's/ /+/g')
  css=""
  for spec in \
    "ital,wght@0,400;0,500;0,700;1,400;1,700" \
    "ital,wght@0,400;0,600;0,700;1,400;1,700" \
    "ital,wght@0,400;0,700;1,400;1,700" \
    "ital,wght@0,400;0,700;1,400" \
    "wght@400;600;700" \
    "wght@400;700" \
    "wght@400"
  do
    if css=$(curl --connect-timeout 20 --max-time 60 --retry 5 --retry-delay 2 --retry-all-errors -fsSL -A "$UA" \
      "https://fonts.googleapis.com/css2?family=${encoded}:${spec}&display=swap"); then
      break
    fi
    css=""
  done
  if [ -z "$css" ]; then
    echo "warning: could not download ${family}" >&2
    return 0
  fi
  printf '%s\n' "$css" | grep -oE 'https://fonts.gstatic.com/[^ )]+' | while IFS= read -r font_url; do
    case "$font_url" in
      *.ttf) ;;
      *) continue ;;
    esac
    base=$(printf '%s' "$font_url" | sed 's|.*/||; s|\?.*||')
    curl --connect-timeout 20 --max-time 120 --retry 5 --retry-delay 2 --retry-all-errors -fsSL -A "$UA" \
      -o "${FONT_DIR}/${base}" "$font_url"
  done
  echo "installed ${family}"
}

install_google_slides_fonts() {
  mkdir -p "$FONT_DIR"
  families="
Roboto
Roboto Condensed
Roboto Mono
Roboto Slab
Open Sans
Lato
Montserrat
Oswald
Source Sans 3
Poppins
Nunito
Nunito Sans
Raleway
Inter
Outfit
Manrope
DM Sans
Lexend
Work Sans
Barlow
Karla
Cabin
Libre Franklin
Ubuntu
Fira Sans
Quicksand
Josefin Sans
Mulish
Rubik
Hind
Archivo
Archivo Narrow
Titillium Web
Exo 2
Signika
Noto Sans
Noto Serif
PT Sans
PT Serif
Merriweather
Playfair Display
Libre Baskerville
Lora
EB Garamond
Crimson Text
Source Serif 4
Bitter
Domine
Spectral
Newsreader
Fraunces
Literata
Figtree
Plus Jakarta Sans
Albert Sans
Be Vietnam Pro
Sora
Gelasio
Comic Neue
Pacifico
Dancing Script
Caveat
Permanent Marker
Indie Flower
Shadows Into Light
Amatic SC
Patrick Hand
Kalam
Satisfy
Great Vibes
Lobster
Bebas Neue
Anton
Libre Bodoni
Abril Fatface
Yanone Kaffeesatz
Comfortaa
Inconsolata
"
  # Read line by line so names such as "Open Sans" stay intact.
  printf '%s\n' "$families" | while IFS= read -r family; do
    [ -n "$family" ] || continue
    download_family "$family"
  done
}

install_selawik() {
  # Selawik is Microsoft's metric-compatible stand-in for Segoe UI (OFL).
  tmp=$(mktemp -d)
  mkdir -p "$SELAWIK_DIR"
  curl --connect-timeout 20 --max-time 120 --retry 3 --retry-delay 1 -fsSL \
    -o "$tmp/selawik.zip" \
    "https://github.com/microsoft/Selawik/releases/download/1.01/Selawik_Release.zip"
  unzip -q "$tmp/selawik.zip" -d "$tmp/out"
  find "$tmp/out" -iname '*.ttf' -exec cp {} "$SELAWIK_DIR/" \;
  rm -rf "$tmp"
}

write_fontconfig() {
  # accept = fallback only. An installed face with the requested name still wins,
  # so Arial from the core fonts is used as Arial. Calibri and Cambria are not
  # redistributable; Carlito and Caladea match their metrics.
  cat > /etc/fonts/conf.d/99-slide-fonts.conf <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <alias binding="strong">
    <family>Calibri</family>
    <accept><family>Carlito</family></accept>
  </alias>
  <alias binding="strong">
    <family>Calibri Light</family>
    <accept><family>Carlito</family></accept>
  </alias>
  <alias binding="strong">
    <family>Cambria</family>
    <accept><family>Caladea</family></accept>
  </alias>
  <alias binding="strong">
    <family>Cambria Math</family>
    <accept><family>Caladea</family></accept>
  </alias>
  <alias binding="strong">
    <family>Segoe UI</family>
    <accept><family>Selawik</family></accept>
  </alias>
  <alias binding="strong">
    <family>Arial Narrow</family>
    <accept><family>Liberation Sans Narrow</family></accept>
  </alias>
  <alias binding="strong">
    <family>Source Sans Pro</family>
    <accept><family>Source Sans 3</family></accept>
  </alias>
  <alias binding="strong">
    <family>Garamond</family>
    <accept><family>EB Garamond</family></accept>
  </alias>
  <alias binding="strong">
    <family>Droid Sans</family>
    <accept><family>Roboto</family></accept>
  </alias>
  <alias binding="strong">
    <family>Droid Serif</family>
    <accept><family>Noto Serif</family></accept>
  </alias>
  <alias binding="strong">
    <family>Georgia</family>
    <accept><family>Gelasio</family></accept>
  </alias>
  <alias binding="strong">
    <family>Comic Sans MS</family>
    <accept><family>Comic Neue</family></accept>
  </alias>
  <alias binding="strong">
    <family>Google Sans</family>
    <accept><family>Inter</family></accept>
  </alias>
  <alias binding="strong">
    <family>Google Sans Text</family>
    <accept><family>Inter</family></accept>
  </alias>
  <alias binding="strong">
    <family>Google Sans Display</family>
    <accept><family>Inter</family></accept>
  </alias>
  <alias binding="strong">
    <family>Product Sans</family>
    <accept><family>Inter</family></accept>
  </alias>
  <alias binding="strong">
    <family>Bodoni</family>
    <accept><family>Libre Bodoni</family></accept>
  </alias>
</fontconfig>
EOF
}

expect_font() {
  requested=$1
  expected=$2
  got=$(fc-match -f '%{family[0]}' -- "$requested" || true)
  echo "font ${requested} -> ${got}"
  case "$got" in
    "$expected"*) return 0 ;;
  esac
  echo "expected ${requested} to resolve to a ${expected} face, got ${got}" >&2
  return 1
}

install_packages
install_google_slides_fonts
install_selawik
write_fontconfig
fc-cache -f
if id gotenberg >/dev/null 2>&1; then
  su -s /bin/sh gotenberg -c "fc-cache -f" || true
fi

failed=0
expect_font "Arial" "Arial" || failed=1
expect_font "Times New Roman" "Times New Roman" || failed=1
expect_font "Georgia" "Georgia" || failed=1
expect_font "Verdana" "Verdana" || failed=1
expect_font "Trebuchet MS" "Trebuchet MS" || failed=1
expect_font "Courier New" "Courier New" || failed=1
expect_font "Comic Sans MS" "Comic Sans MS" || failed=1
expect_font "Impact" "Impact" || failed=1
expect_font "Calibri" "Carlito" || failed=1
expect_font "Cambria" "Caladea" || failed=1
expect_font "Segoe UI" "Selawik" || failed=1
expect_font "Roboto" "Roboto" || failed=1
expect_font "Open Sans" "Open Sans" || failed=1
expect_font "Montserrat" "Montserrat" || failed=1
expect_font "Lato" "Lato" || failed=1
expect_font "Poppins" "Poppins" || failed=1
expect_font "Inter" "Inter" || failed=1
expect_font "Source Sans Pro" "Source Sans 3" || failed=1
expect_font "Noto Sans" "Noto Sans" || failed=1
expect_font "Merriweather" "Merriweather" || failed=1
expect_font "Google Sans" "Inter" || failed=1
expect_font "Anton" "Anton" || failed=1
expect_font "Bodoni" "Libre Bodoni" || failed=1
exit "$failed"
