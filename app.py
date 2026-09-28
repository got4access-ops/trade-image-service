"""
Trade-box image renderer.

Roblox WebhookLogger builds a URL like

    https://<host>/trade?left=<name>&right=<name>&lp=<tokens>&rp=<tokens>&page=<n>&of=<m>

and hands that URL to Discord as an embed image. Discord fetches it, this
service composes the trade card, returns a PNG.

Each token in `lp`/`rp` is `<asset_id>[:<flags>]` -- the flags letters are
`n` (neon), `m` (mega neon), `f` (fly), `r` (ride), in any order. Plain
`<asset_id>` still works, so old links keep rendering.

Slot positions were measured off the actual template.png (1014x634, 3x3 grid
per side, each slot is 113x113 with a small inner padding). Pet icons come
from Roblox's public asset thumbnail endpoint.
"""
import os
from functools import lru_cache
from io import BytesIO

import requests
from flask import Flask, abort, request, send_file
from PIL import Image, ImageDraw, ImageFilter, ImageFont

app = Flask(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = Image.open(os.path.join(HERE, "template.png")).convert("RGBA")

# Slot centres, measured from the template. 3 columns per side, 3 rows.
LEFT_COL_X  = [113, 236, 359]
RIGHT_COL_X = [650, 773, 896]
ROW_Y       = [173, 296, 419]
SLOT_INNER  = 100   # icon size inside the 113 slot (leaves a 6px border)

# Card colours picked out of the template.
CARD_BG     = (255, 229, 248, 255)
LABEL_PINK  = (255, 112, 216, 255)

# Where "REPLACE WITH USER" sits above each grid.
LEFT_LABEL_RECT  = (45,  65,  435, 105)
RIGHT_LABEL_RECT = (582, 65,  972, 105)

# Page indicator in the top-right corner of the card, only drawn when
# `of > 1`.
PAGE_RECT = (855, 12, 1005, 52)

# Pet-property badges. Icons live next to app.py as prop_<key>.png. They are
# 150x150 alpha PNGs; we scale each one down once at startup so the render
# loop just pastes. `n`/`m` are mutually exclusive (`m` wins if both are
# sent), so at most three badges appear per slot.
BADGE_SIZE = 30
BADGE_GAP  = 2


def _load_badge(filename: str) -> Image.Image:
    path = os.path.join(HERE, filename)
    return Image.open(path).convert("RGBA").resize(
        (BADGE_SIZE, BADGE_SIZE), Image.LANCZOS
    )


BADGES = {
    "n": _load_badge("prop_neon.png"),
    "m": _load_badge("prop_mega_neon.png"),
    "f": _load_badge("prop_fly.png"),
    "r": _load_badge("prop_ride.png"),
}


def _load_font(size: int) -> ImageFont.ImageFont:
    """Best-effort bold sans-serif that works on Render, Vercel, and local."""
    for name in (
        "DejaVuSans-Bold.ttf",
        "Arial Bold.ttf",
        "arialbd.ttf",
        "LiberationSans-Bold.ttf",
        "Helvetica-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


LABEL_FONT = _load_font(32)
PAGE_FONT  = _load_font(24)


@lru_cache(maxsize=512)
def _fetch_icon(asset_id: str) -> bytes:
    """
    Fetch an asset thumbnail using Roblox's current thumbnail API.
    """
    api_url = (
        "https://thumbnails.roblox.com/v1/assets"
        f"?assetIds={asset_id}&returnPolicy=PlaceHolder"
        "&size=150x150&format=Png&isCircular=false"
    )

    r = requests.get(api_url, timeout=8)
    r.raise_for_status()

    data = r.json()

    if not data.get("data"):
        raise ValueError("No thumbnail returned")

    image_url = data["data"][0].get("imageUrl")

    if not image_url:
        raise ValueError("Roblox returned no image URL")

    image = requests.get(image_url, timeout=8)
    image.raise_for_status()

    return image.content


def _place_icon(img: Image.Image, asset_id: str, flags: str, cx: int, cy: int) -> None:
    try:
        raw = _fetch_icon(asset_id)
        icon = Image.open(BytesIO(raw)).convert("RGBA")
    except Exception:
        icon = None   # a bad id / broken thumbnail just leaves the pet blank

    if icon is not None:
        icon = icon.resize((SLOT_INNER, SLOT_INNER), Image.LANCZOS)
        img.paste(icon, (cx - SLOT_INNER // 2, cy - SLOT_INNER // 2), icon)

    _place_badges(img, flags, cx, cy)


def _place_badges(img: Image.Image, flags: str, cx: int, cy: int) -> None:
    """Draw the property badges along the bottom of the slot, centred.
    `m` (mega neon) supersedes `n` (neon); order is neon/mega, fly, ride."""
    if not flags:
        return

    keys = []
    if "m" in flags:
        keys.append("m")
    elif "n" in flags:
        keys.append("n")
    if "f" in flags:
        keys.append("f")
    if "r" in flags:
        keys.append("r")
    if not keys:
        return

    total_w = len(keys) * BADGE_SIZE + (len(keys) - 1) * BADGE_GAP
    start_x = cx - total_w // 2
    # The slot cell is ~113px tall and the pet icon is 100px; nudging the
    # badges below the pet's midline keeps them clear of the pet's face but
    # inside the visible slot border.
    y = cy + SLOT_INNER // 2 - BADGE_SIZE + 4

    for i, key in enumerate(keys):
        badge = BADGES[key]
        x = start_x + i * (BADGE_SIZE + BADGE_GAP)
        img.paste(badge, (x, y), badge)


def _draw_label(
    draw: ImageDraw.ImageDraw,
    rect,
    text: str,
    align: str = "left",
    font=LABEL_FONT
):
    """Paint over the placeholder label and draw the name flush to `align`.
    The trade UI puts the sender's name at the LEFT edge of their grid and
    the recipient's at the RIGHT edge of theirs.
    """
    x0, y0, x1, y1 = rect
    draw.rectangle(rect, fill=CARD_BG)

    if not text:
        return

    # Fit-to-width: shrink if the name is too long.
    f = font
    for size in range(32, 12, -2):
        f = _load_font(size)
        w = draw.textlength(text, font=f)
        if w <= (x1 - x0) - 8:
            break

    w = draw.textlength(text, font=f)
    ascent, descent = f.getmetrics()

    if align == "right":
        tx = x1 - w
    else:
        tx = x0

    ty = y0 + ((y1 - y0) - (ascent + descent)) / 2 - 2
    draw.text((tx, ty), text, fill=LABEL_PINK, font=f)


def _parse_tokens(raw: str) -> list[tuple[str, str]]:
    """Parse `id[:flags],id[:flags],...` into a list of (asset_id, flags).
    Unknown flag chars are dropped; missing colon means no flags."""
    if not raw:
        return []

    out = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            asset_id, flags = token.split(":", 1)
            asset_id = asset_id.strip()
            flags = "".join(c for c in flags.lower() if c in "nmfr")
        else:
            asset_id, flags = token, ""
        if asset_id:
            out.append((asset_id, flags))
    return out


@app.route("/trade")
def trade():
    left_name  = (request.args.get("left")  or "?").strip()[:32]
    right_name = (request.args.get("right") or "?").strip()[:32]

    lp = _parse_tokens(request.args.get("lp", ""))[:9]
    rp = _parse_tokens(request.args.get("rp", ""))[:9]

    try:
        page  = max(1, int(request.args.get("page", "1")))
        total = max(1, int(request.args.get("of", "1")))
    except ValueError:
        page, total = 1, 1

    img = TEMPLATE.copy()
    draw = ImageDraw.Draw(img)

    _draw_label(draw, LEFT_LABEL_RECT, left_name, align="left")
    _draw_label(draw, RIGHT_LABEL_RECT, right_name, align="right")

    for i, (asset_id, flags) in enumerate(lp):
        _place_icon(
            img,
            asset_id,
            flags,
            LEFT_COL_X[i % 3],
            ROW_Y[i // 3]
        )

    for i, (asset_id, flags) in enumerate(rp):
        _place_icon(
            img,
            asset_id,
            flags,
            RIGHT_COL_X[i % 3],
            ROW_Y[i // 3]
        )

    if total > 1:
        draw.rectangle(PAGE_RECT, fill=CARD_BG)

        pt = f"{page}/{total}"
        w = draw.textlength(pt, font=PAGE_FONT)

        px = PAGE_RECT[0] + (
            (PAGE_RECT[2] - PAGE_RECT[0]) - w
        ) / 2

        draw.text(
            (px, PAGE_RECT[1] + 4),
            pt,
            fill=LABEL_PINK,
            font=PAGE_FONT
        )

    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    buf.seek(0)

    resp = send_file(buf, mimetype="image/png")

    # Discord caches by URL; help it out by letting proxies cache too.
    resp.headers["Cache-Control"] = "public, max-age=86400"

    return resp


@app.route("/")
def health():
    return "trade-image-service ok", 200


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        debug=False
    )
