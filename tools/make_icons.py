from PIL import Image, ImageDraw, ImageFont

OUT = "/home/pi/projects/K4-Echo-Control/skill/skill-package/assets"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

M = 2160                      # master canvas, divisible by both 108 and 512-ish
PAD = 16 / 108                # Amazon's recommended padding fraction
BG = (13, 27, 42)             # deep navy, opaque -- no transparency anywhere
GRAD_TOP = (44, 129, 214)     # card gradient
GRAD_BOT = (17, 74, 141)
EDGE = (108, 178, 240)        # subtle top highlight on the card
TEXT = (255, 255, 255)

def make(size_hint):
    img = Image.new("RGB", (M, M), BG)

    pad = int(M * PAD)
    box = (pad, pad, M - pad, M - pad)
    card_w = box[2] - box[0]
    radius = int(card_w * 0.22)

    # vertical gradient, masked to a rounded rectangle
    grad = Image.new("RGB", (1, card_w))
    for y in range(card_w):
        t = y / max(card_w - 1, 1)
        grad.putpixel((0, y), tuple(
            int(GRAD_TOP[i] + (GRAD_BOT[i] - GRAD_TOP[i]) * t) for i in range(3)))
    grad = grad.resize((card_w, card_w), Image.BILINEAR)

    mask = Image.new("L", (card_w, card_w), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, card_w - 1, card_w - 1), radius=radius, fill=255)
    img.paste(grad, (box[0], box[1]), mask)

    d = ImageDraw.Draw(img)
    # hairline highlight so the card reads as a raised tile
    d.rounded_rectangle(box, radius=radius, outline=EDGE, width=max(2, card_w // 220))

    # "K4" scaled to fit the card, then optically centred on its ink box
    target = int(card_w * 0.60)
    fs = target
    while fs > 8:
        f = ImageFont.truetype(FONT, fs)
        l, t, r, b = d.textbbox((0, 0), "K4", font=f)
        if (r - l) <= card_w * 0.66 and (b - t) <= card_w * 0.60:
            break
        fs -= 4
    l, t, r, b = d.textbbox((0, 0), "K4", font=f)
    cx = box[0] + (card_w - (r - l)) / 2 - l
    cy = box[1] + (card_w - (b - t)) / 2 - t
    d.text((cx, cy), "K4", font=f, fill=TEXT)
    return img

master = make(M)
for size, name in ((108, "en-US_smallIcon.png"), (512, "en-US_largeIcon.png")):
    master.resize((size, size), Image.LANCZOS).save("%s/%s" % (OUT, name), "PNG", optimize=True)
    print("wrote %s/%s  (%dx%d)" % (OUT, name, size, size))
