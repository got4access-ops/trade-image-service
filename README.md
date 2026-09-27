# trade-image-service

Tiny Flask + Pillow app that renders a pink trade-box PNG for the ADM Discord
trade log. WebhookLogger builds a URL, Discord fetches it, this composes and
returns the image.

## What's here

- `app.py`         Flask app; the only endpoint that matters is `/trade`
- `template.png`   1014x634 pink trade-box background (slots are empty)
- `requirements.txt`, `Procfile`, `render.yaml`   deployment glue

## Endpoint

    GET /trade?left=<name>&right=<name>&lp=<ids>&rp=<ids>&page=<n>&of=<m>

- `left`, `right` — usernames rendered on top of each grid (32 char cap)
- `lp`, `rp`      — comma-separated Roblox asset IDs, up to 9 each
                    (any beyond 9 are cropped; WebhookLogger paginates)
- `page`, `of`    — page counter shown in the top-right when `of > 1`

Returns `image/png`, cache-able for a day. Bad or missing icons leave that
slot empty rather than aborting the render.

## Deploy to Render.com (free, easiest)

1. Push these files to a GitHub repo (any name; keep `template.png` in the
   same folder as `app.py`).
2. Sign up at https://render.com — free tier, no credit card.
3. New -> Web Service -> connect the repo.
4. Render reads `render.yaml` automatically; just click Create.
   Runtime = Python, build = `pip install -r requirements.txt`,
   start = the gunicorn line in `Procfile` / `render.yaml`.
5. Wait for the first deploy (~2 min). You get a URL like
   `https://trade-image-service.onrender.com`. Test it:
   `https://<your>.onrender.com/trade?left=Alice&right=Bob&lp=15698960105&rp=15698960105`.
6. In Roblox Studio, open `ServerScriptService.Bootstrap.Ext.WebhookLogger`,
   find `M.TRADE_IMAGE_URL = ""`, and set it to your URL + `/trade`:

       M.TRADE_IMAGE_URL = "https://trade-image-service.onrender.com/trade"

   Save, publish, done.

### Cold-start heads-up

Render's free tier spins a service down after 15 minutes of no traffic; the
first request after that takes ~30s to warm up, which Discord will time out.
Fix in one of two ways:

- **UptimeRobot** (free, easiest): monitor `https://<your>.onrender.com/`
  every 5 minutes. Keeps the service warm 24/7.
- **Upgrade Render's plan** ($7/mo starter): always-on, no cold starts.

If you want zero cold starts and no credit card, Fly.io's free tier is
always-on but requires a card on file (nothing is charged unless you go
over the free quota).

## Deploy to Vercel (alternative)

Works but Pillow on Vercel's serverless Python runtime is fragile and the
250MB deployment cap gets close. Only bother if Render is off the table.

## Run it locally to test

    py -3 -m pip install -r requirements.txt
    py -3 app.py

Then hit `http://localhost:8080/trade?...` — a browser tab is fine.

`test_out.png` in this folder was rendered by that flow with 5 pets left,
3 pets right, page 1/2.
