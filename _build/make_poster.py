from PIL import Image, ImageDraw, ImageFont

logo = Image.open(r'Assets\Square310x310Logo.png').convert('RGBA')

W, H = 720, 1080
poster = Image.new('RGBA', (W, H), (10, 25, 50, 255))
draw = ImageDraw.Draw(poster)

# Background stripes (topo feel)
for y in range(0, H, 40):
    alpha = int(15 + 10 * (y / H))
    draw.rectangle([(0, y), (W, y + 20)], fill=(0, 80, 120, alpha))

# Logo centered upper area
logo_size = 480
logo_resized = logo.resize((logo_size, logo_size), Image.LANCZOS)
lx = (W - logo_size) // 2
ly = 160
poster.paste(logo_resized, (lx, ly), logo_resized)

# Fonts
try:
    font_big = ImageFont.truetype('C:/Windows/Fonts/segoeuib.ttf', 58)
    font_sub = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 28)
    font_tag = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 22)
except Exception:
    font_big = font_sub = font_tag = ImageFont.load_default()

# App name
title = 'CV Toposheet'
bb = draw.textbbox((0, 0), title, font=font_big)
draw.text(((W - (bb[2] - bb[0])) // 2, 720), title, font=font_big, fill=(255, 255, 255, 255))

# Tagline
tag = 'AI-Powered Map Digitization'
bb2 = draw.textbbox((0, 0), tag, font=font_sub)
draw.text(((W - (bb2[2] - bb2[0])) // 2, 795), tag, font=font_sub, fill=(0, 200, 220, 255))

# Sub
sub = 'Extract  \u2022  Search  \u2022  Export'
bb3 = draw.textbbox((0, 0), sub, font=font_tag)
draw.text(((W - (bb3[2] - bb3[0])) // 2, 840), sub, font=font_tag, fill=(150, 200, 220, 255))

# Bottom bar
draw.rectangle([(0, H - 80), (W, H)], fill=(0, 150, 180, 200))
foot = 'Survey of India  \u2022  USGS  \u2022  UK OS  \u2022  and more'
bb4 = draw.textbbox((0, 0), foot, font=font_tag)
draw.text(((W - (bb4[2] - bb4[0])) // 2, H - 50), foot, font=font_tag, fill=(255, 255, 255, 200))

poster.convert('RGB').save('poster_720x1080.png')
print('Done: _build/poster_720x1080.png')
