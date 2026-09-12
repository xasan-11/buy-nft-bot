# Telegram NFT/Gift Auto-Purchase Monitor

Monitors Telegram channels for links to collectible Telegram Gifts (NFTs)
listed for resale, and automatically buys them with your own Telegram
account using Telegram Stars — but only when the price is **200 ⭐ or
less**. Administration happens through a separate, admin-only control bot.

## How buying actually works (read this first)

Telegram does not expose a generic "buy anything in a channel post" API.
What it does expose, and what this project uses, is the real MTProto flow
for buying a **unique collectible gift that is listed for resale**:

1. A collectible gift has a permanent link: `t.me/nft/<slug>` (or
   `tg://nft?slug=<slug>`). See <https://core.telegram.org/api/gifts> and
   <https://core.telegram.org/api/links>.
2. `payments.getPaymentForm` is called with an `inputInvoiceStarGiftResale`
   built from that slug. Telegram only returns a valid form if the listing
   still exists and is still purchasable — this doubles as the "verify
   listing is still available" step, and its `invoice.prices` give the
   live, authoritative price (not whatever the channel post said).
3. `payments.sendStarsForm` finalizes the payment with the form's `form_id`.

These three calls were verified against the installed Telethon 1.45.0's
generated TL schema (which tracks Telegram's current MTProto layer) before
being used here — see `src/marketplace/listing.py` and
`src/marketplace/purchase.py`.

**Consequence for detection:** only channel posts that contain an actual
`t.me/nft/...` link can be identified reliably and therefore acted on. A
post that only *describes* a gift in prose, with no link, is skipped —
per spec, an unreliable identification must never lead to a purchase.

## Requirements

- Python 3.10+ (tested with 3.14)
- A Telegram account to do the monitoring/buying (with enough Stars balance
  — Stars must be topped up manually in the Telegram app; nothing here
  automates buying Stars themselves)
- A Telegram API ID/hash from <https://my.telegram.org> ("API development tools")
- A bot token from [@BotFather](https://t.me/BotFather) for the control bot

## Installation

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

| Variable | Meaning |
|---|---|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | From my.telegram.org, for your user account |
| `TELEGRAM_PHONE` | Your phone number in international format, e.g. `+15551234567` |
| `OWNER_TELEGRAM_ID` | Your numeric Telegram user ID (get it from [@userinfobot](https://t.me/userinfobot)). Always stays an admin. |
| `CONTROL_BOT_TOKEN` | Bot token from @BotFather for the admin-only control bot |
| `MAX_NFT_PRICE` | Maximum price in Stars to auto-buy. **Maximum only — there is no minimum balance rule.** Default `200`. |
| `POLL_INTERVAL` | Reserved for a future polling fallback; unused by default since updates are event-driven. Default `1`. |

`.env`, `data/*.session`, and the local SQLite database are all covered by
`.gitignore` — never commit them.

## Telegram account authentication

Run once, before starting the app:

```bash
python login.py
```

You'll be prompted for the login code Telegram texts/sends you, and for
your 2FA password if you have one (hidden input via `getpass`, never
logged, never sent to the control bot). This creates
`data/user_session.session`, which is reused on every future run — you
won't be prompted again unless you delete it or log out elsewhere.

## Running the application

```bash
python -m src.main
```

This connects both your user account (for monitoring + buying) and the
control bot (for admin commands), and runs until stopped with Ctrl+C.

## Using the control bot

Open a DM with your control bot on Telegram (the one behind
`CONTROL_BOT_TOKEN`) and send commands there. Every command checks the
sender's numeric Telegram user ID against the `admins` table — usernames
are never trusted for authorization. Unauthorized users get:

> ⛔ You are not authorized to use this bot.

### Adding administrators (owner only)

```
/addadmin 123456789
```

### Removing administrators (owner only)

```
/removeadmin 123456789
```

The owner (`OWNER_TELEGRAM_ID`) can never be removed this way — the bot
replies "⛔ The owner cannot be removed." if you try.

### Viewing administrators (any admin)

```
/admins
```

### Adding channels (any admin)

```
/add @examplechannel
```

Your user account must already be able to see the channel (a member, or
it must be public) to resolve it.

### Removing channels (any admin)

```
/remove @examplechannel
```

### Listing monitored channels (any admin)

```
/list
```

### /stop

Immediately flips monitoring to `STOPPED`, persisted in the database. No
new purchase is started after this — the purchase pipeline re-checks this
flag right before the money-moving call, so even a purchase already in
flight for a given listing will abort before paying if `/stop` lands in
the window between price verification and payment.

### /sstart

Flips monitoring back to `RUNNING` (persisted) and resumes processing new
channel posts. Named `/sstart` deliberately, not `/start` — `/start` is
kept as the bot's generic greeting/help command.

### /status

```
📊 Status
Monitoring: RUNNING
Maximum NFT price: 200 ⭐
Monitored channels: 3
Total detected NFTs: 42
Eligible NFTs: 30
Purchase attempts: 25
Successful purchases: 20
Failed purchases: 5
```

## NFT price limit

`MAX_NFT_PRICE` (default `200`) is a **maximum**, never a minimum. A 50 ⭐
gift and a 200 ⭐ gift are both bought; a 201 ⭐ gift is ignored. There is
no separate "minimum Stars balance" check anywhere in this codebase.

## How automatic purchasing works

For every new/edited post in a monitored channel:

1. **Detect** — look for a `t.me/nft/<slug>` (or `tg://nft?slug=`) link. No
   link → skip, nothing is recorded.
2. **Claim** — atomically insert the slug into `processed_listings`
   (`PRIMARY KEY` on `slug`). If the insert fails, this exact listing was
   already handled (duplicate post, edit re-processing, race between two
   handlers) → skip.
3. **Extract price** — parse Stars amount from the post text. Ambiguous or
   missing → marked `IGNORED_UNKNOWN_PRICE`, never purchased.
4. **Filter** — price > `MAX_NFT_PRICE` → marked `IGNORED_PRICE`, never
   purchased.
5. **Check monitoring state** — if `/stop` was issued, marked
   `IGNORED_STOPPED`, never purchased.
6. **Verify + re-price** — call `payments.getPaymentForm`. Failure (already
   sold, invalid, delisted) → recorded as a failed attempt, not retried
   automatically. The live price from the form is compared against
   `MAX_NFT_PRICE` again, since the channel post's price can be stale.
7. **Final monitoring check** — re-checked immediately before payment.
8. **Pay** — `payments.sendStarsForm`. Success/failure is recorded in
   `purchase_attempts` and an admin notification is sent either way.
9. Monitoring continues regardless of outcome.

## Concurrency / duplicate-purchase safety

The `processed_listings.slug` primary key is the actual lock: whichever
caller's `INSERT` succeeds owns that listing; every other caller's insert
fails immediately and it backs off. This is safe across concurrent event
handlers in the same process (new-message and edited-message events firing
for the same post) without needing a separate in-memory lock.

## Notifications

Sent by the control bot to the owner and every current administrator:

- `✅ NFT purchased` — price, channel, NFT ID (slug)
- `⏭ NFT ignored` — price, limit, reason (over limit / unknown price / monitoring stopped)
- `❌ NFT purchase failed` — price, channel, reason (Telegram's error message)
- `🛑 Monitoring stopped.` / `🟢 Monitoring started.` — broadcast on `/stop` / `/sstart`

## Security

- All credentials live in `.env` (gitignored). `.env.example` has no secrets.
- Session files (`data/*.session`) are gitignored and are the only thing
  that grants access to your account — treat them like a password.
- The control bot never asks for, stores, or logs your Telegram password.
  2FA password entry only ever happens interactively in `login.py` via
  `getpass` (hidden terminal input).
- No admin command works without the sender's numeric Telegram user ID
  being present in the `admins` table. Usernames are never used for
  authorization decisions.
- Nothing here bypasses Telegram's rate limits: `FloodWaitError` is caught
  and surfaced as a failed attempt rather than retried in a tight loop.

## Telegram rate limits

The monitor is purely event-driven (Telethon's `NewMessage` /
`MessageEdited` / `MessageDeleted` updates) — there is no polling loop
hitting Telegram on an interval, so there's nothing to rate-limit under
normal operation. The only calls made per listing are one
`getPaymentForm` and, if eligible, one `sendStarsForm` — both wrapped so a
`FloodWaitError` is recorded as a failed purchase attempt rather than
retried immediately.

## Troubleshooting

- **`RuntimeError: Missing required environment variables`** — check `.env`
  has all of `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`,
  `CONTROL_BOT_TOKEN`, `OWNER_TELEGRAM_ID` set.
- **Control bot doesn't respond** — confirm `CONTROL_BOT_TOKEN` is correct
  and you're messaging that exact bot; confirm your numeric user ID is in
  `OWNER_TELEGRAM_ID` or was added via `/addadmin`.
- **`/add @channel` fails to resolve** — your user account (not the bot)
  needs to already be able to see that channel; join it first if it's
  public, or make sure the username is correct.
- **A listing is always "unavailable"** — the gift was likely already
  sold/delisted between the post and your bot processing it; this is
  expected under contention for popular listings and is not a bug.
- **Purchases never happen even though channels are added** — check
  `/status`: monitoring must show `RUNNING` (`/sstart`).
- **Stars balance too low** — Stars can only be topped up manually inside
  the official Telegram app; this project only spends existing balance.

## Project structure

```
src/
  config/       settings.py          — env loading
  database/     models.py, repository.py — SQLite schema + async repository
  telegram/     client.py, authentication.py, updates.py — Telethon wiring
  monitoring/   nft_detector.py, price_parser.py, channel_monitor.py
  marketplace/  listing.py, purchase.py — the real MTProto buy flow
  bot/          authorization.py, bot_app.py — control bot commands
  notifications/notifier.py
  main.py
login.py        — one-time interactive account login
tests/          — pytest suite (no real purchases are ever made in tests)
```

## Running the tests

```bash
pytest
```

Tests never talk to real Telegram servers — the purchase pipeline and
marketplace calls are exercised against fakes/mocks (see
`tests/test_marketplace_purchase.py` and `tests/test_purchase_pipeline.py`).
#   b u y - n f t - b o t  
 