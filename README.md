# Telegram NFT/Gift Auto-Purchase Monitor

Monitors Telegram channels for links to collectible Telegram Gifts (NFTs)
listed for resale, and automatically buys them with the **owner's**
Telegram account using Telegram Stars — but only when the price is
**200 ⭐ or less**. Channel/market monitoring and administration happen
through a separate control bot, restricted to the owner.

**Multi-tenant offer-tashlash:** on top of that, *any* Telegram user can
`/start` the control bot, connect their **own** personal account with
`/login`, and run their own independent auto-offer pipeline against the
same shared market scan — see "Multi-tenant offer-tashlash" below.

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
| `TELEGRAM_PHONE` | Optional. Your phone number in international format, e.g. `+15551234567`. If unset, the control bot asks for it via `/login <phone>` instead. |
| `TELEGRAM_SESSION_STRING` | Optional. Generated once locally via `python generate_session.py`; see below. Only matters for the very first deploy — after any successful login, the session is saved in the database instead. |
| `OWNER_TELEGRAM_ID` | Your numeric Telegram user ID (get it from [@userinfobot](https://t.me/userinfobot)). Always stays an admin. |
| `CONTROL_BOT_TOKEN` | Bot token from @BotFather for the admin-only control bot |
| `MAX_NFT_PRICE` | Maximum price in Stars to auto-buy. **Maximum only — there is no minimum balance rule.** Default `200`. |
| `POLL_INTERVAL` | Reserved for a future polling fallback; unused by default since updates are event-driven. Default `1`. |

`.env` and the local SQLite database are covered by `.gitignore` — never
commit them.

## Telegram account authentication

The app authenticates your user account with a **StringSession** (the
whole login session as one string) rather than a `.session` file on disk,
and never attempts a `python -c "input()"`-style interactive login itself —
a host like Railway has no terminal to type a code into, and that used to
crash the process with `EOFError`. There are two ways to get a session in,
checked in this order at startup:

1. **A session saved from a previous login**, in the database (see below) —
   always used if present, since it's the most recently proven-valid one.
2. **`TELEGRAM_SESSION_STRING`**, generated locally before the first deploy:

   ```bash
   python generate_session.py
   ```

   You'll be prompted for the login code Telegram texts/sends you, and for
   your 2FA password if you have one (hidden input via `getpass`, never
   logged). It prints a session string — put it in `.env` (or Railway
   Variables) as `TELEGRAM_SESSION_STRING=<the string>`.

If neither is present or valid (fresh deploy with nothing set yet, or a
session that's been revoked — logged out elsewhere, password change), the
app falls back to a **third path**: logging in through the control bot
itself, so no local step is required at all.

### Logging in through the control bot (`/login`, `/code`, `/password`)

**Any Telegram user can `/login` their own account** — not just the owner.
Each user's session is stored independently (see "Multi-tenant
offer-tashlash" below); this section describes the flow, which is
identical for everyone, using the owner as the example since the owner's
account is also what powers channel/market monitoring. When the owner's
account specifically isn't authorized yet, the bot messages the owner:

- If `TELEGRAM_PHONE` is set, it skips straight to sending the code and
  says **"📩 Kod yuborildi. Iltimos kodni shu yerga yozing: /code 12345
  shaklida"**.
- Otherwise it asks first: **"🔑 Telegram akkountga kirish kerak. Kodni
  yuborishim uchun avval /login <telefon_raqam> buyrug'ini yuboring"** —
  reply with e.g. `/login +15551234567` to trigger the code send.

Then, in the owner's DM with the control bot:

```
/code 12345
```

If the account has 2FA enabled, the bot instead asks **"🔒 2FA yoqilgan.
Parolni yuboring: /password <parol>"** — reply with:

```
/password your2FApassword
```

On success the bot replies **"✅ Muvaffaqiyatli login qilindi."** and the
resulting session string is saved straight into the database (never into
`.env`, since a `.env` file/commit can leak) — every future restart reuses
it automatically, with no login step at all, until it's revoked.

While the user account is unauthorized, the control bot itself is already
fully up and answering every other admin command (`/status`, `/add`,
etc.) — only channel/market monitoring, purchasing and offers wait for
login to finish, since those are the things that actually need the user
account connected.

## Running the application

```bash
python -m src.main
```

This connects both your user account (for monitoring + buying) and the
control bot (for admin commands), and runs until stopped with Ctrl+C.

## Deploying to Railway

This is a long-running worker (it never binds to a port — there is no HTTP
server here), not a web service, so a few things need to be set explicitly
that Railway would otherwise try to auto-detect.

### Start command

`railway.json` at the repo root already pins this:

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": { "builder": "NIXPACKS" },
  "deploy": {
    "startCommand": "python -m src.main",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

Without this, Railpack tries to detect a web framework (FastAPI/Flask/
Django), finds none, and fails with "No start command detected." There is
no Procfile or other deploy config in this repo — `railway.json` is the
only one, so there's nothing for it to conflict with. Don't set a
**Healthcheck Path** in the Railway service settings either: a healthcheck
expects an HTTP 200 response, and this process never listens on any port,
so a healthcheck would just fail the deploy for a process that's actually
running fine. Leaving Networking/Healthcheck unset is what makes Railway
treat it as a plain worker instead of a web service.

### Environment variables

`.env` is gitignored and is never pushed to Railway — set every variable
from `.env.example` under the service's **Variables** tab instead
(`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`,
`TELEGRAM_SESSION_STRING`, `OWNER_TELEGRAM_ID`, `CONTROL_BOT_TOKEN`,
`MAX_NFT_PRICE`, etc.). Railway injects these as real process environment
variables, which `Settings.load()` reads the same way regardless of
whether they came from `.env` or from Railway — no code difference
between local and deployed.

### The user account session (no terminal on Railway to enter a login code)

Railway gives the process no terminal to type a login code into, so the
app never attempts an interactive login there. Two options:

- **Authenticate locally first** (recommended for the first deploy) and
  ship the resulting StringSession as an environment variable:

  ```bash
  python generate_session.py   # run locally once, prompts for the login code
  ```

  Copy the printed string into a `TELEGRAM_SESSION_STRING` variable on the
  Railway service.

- **Or log in through the control bot itself** — deploy with no session
  set at all, then DM the control bot as the owner: `/login <phone>` (or
  just `/code`/`/password` directly if `TELEGRAM_PHONE` is already set),
  then `/code 12345`, and `/password ...` too if 2FA is enabled. See
  "Logging in through the control bot" above for the exact prompts.

Either way, a successful login's session is saved into `data/app.db`
(see below) so it's reused automatically on every future restart —
`TELEGRAM_SESSION_STRING` only matters again if that database row is ever
lost or the session is revoked.

### Persisting data across redeploys

Railway's filesystem is ephemeral per deploy by default — without a
**Volume**, every redeploy starts from a clean container, which wipes
`data/app.db` and with it: admins, monitored channels, processed listings,
purchase history, **and the saved user-account session** (since a
completed `/login` is stored there too, not in an env var). Losing the
session isn't fatal — the app just falls back to `TELEGRAM_SESSION_STRING`
if it's still set, or asks the owner to `/login` again — but it does mean
repeating that step on every redeploy.

To avoid all of that, attach a Railway **Volume** mounted at `/app/data`
(the working directory on Railway is the repo root, so this is where
`data/` resolves to) — then a session, once logged in, survives redeploys
indefinitely with no repeated setup.

## Using the control bot

Open a DM with your control bot on Telegram (the one behind
`CONTROL_BOT_TOKEN`) and send commands there.

There are two permission tiers, checked by numeric Telegram user ID —
usernames are never trusted for authorization:

- **Owner** (`OWNER_TELEGRAM_ID`) — full access to everything below:
  admin management, channel management, channel/market monitoring, and
  their own offer-tashlash settings.
- **Everyone else** (including anyone added via `/addadmin` — being listed
  as an admin no longer grants anything extra) — can only `/login` their
  own account and run their own offer-tashlash pipeline. Every command
  below that isn't `/login`/`/setoffer*`/the gift-type picker replies:

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

### ⭐ Stars / ⭐ Take stars (owner only)

Two owner-only tools for directly managing the Stars balance of any
account that has completed `/login` through this bot — each acts strictly
through that account's own authorization, never anyone else's, since
that's the only way Telegram allows either operation at all.

- **⭐ Stars** (`/stars <account_id>`) — reports that account's current
  Stars balance via
  [`payments.getStarsStatus`](https://core.telegram.org/method/payments.getStarsStatus)
  (`peer=inputPeerSelf`) — the same official method channel/market
  monitoring's balance-watch loop already uses (`src/marketplace/balance.py`).
- **⭐ Take stars** (`/takestars <account_id> <post_link>`) — sends that
  account's *entire* current Stars balance as one paid reaction to the
  given post, via
  [`messages.sendPaidReaction`](https://core.telegram.org/method/messages.sendPaidReaction).
  This method isn't in Telethon's generated schema yet (checked against
  1.45.0, the latest release on PyPI, at the time this was written), so
  it's hand-written in `src/marketplace/paid_reaction.py` against the
  official schema — `messages.sendPaidReaction#58bbcb50 flags:#
  peer:InputPeer msg_id:int count:int random_id:long
  private:flags.0?PaidReactionPrivacy = Updates;` — and its binary
  encoding is round-trip tested against Telethon's own `BinaryReader` in
  `tests/test_paid_reaction.py`. Since a single reaction is capped at 2500
  Stars in Telegram's own UI, a balance above that is sent in successive
  2500-Star chunks until it's exhausted or a call fails (in which case
  whatever went through already stays sent, and the reply reports exactly
  how much and what's left).

Both buttons drive a two-step prompt (account ID, then — for Take stars —
the post link); the slash commands take both arguments at once. Post
links are parsed with `src/marketplace/post_link.py`: plain
`t.me/<channel>/<msg_id>`, the `t.me/s/...` preview form, and
`t.me/c/<internal_id>/<msg_id>` for private channels (only resolvable if
the target account has already seen/joined that channel — Telegram gives
no other way to look up a private channel by that id).

## Multi-tenant offer-tashlash

Any Telegram user — owner, admin, or a complete stranger who has never
been added anywhere — can `/start` the control bot and use this feature
with their **own** Telegram account:

1. `/login +998901234567` (see "Logging in through the control bot" above)
   — connects that user's own personal account, with its own session
   stored in the `users` table, completely isolated from everyone else's.
2. `/setofferlevel`, `/setoffernftcount`, `/setofferprice`, `/setofferexpiry`
   — each user's own settings, independent of everyone else's (including
   the owner's).
3. **🎯 Offer boshlash** — pick gift types and start; **🛑 Offer
   to'xtatish** — stop. Each user's run (active/paused/sent-count) is
   fully independent.

**Architecture — one shared scan, many independent senders:** listing
*discovery* (`payments.getResaleStarGifts`) stays a single scan on the
owner's account — this is deliberately **not** one poll per user. Running
N independent pollers for N users would multiply Telegram API traffic by N
for the exact same listings and risk `FloodWaitError` under any real
number of users; instead, every listing the one shared scan finds is
evaluated against **every currently-active user's own filters** (level,
NFT count, price, gift types, expiry), and whichever users match each
independently send their own offer through their own account (so multiple
different users can each offer on the same listing — sending an offer
doesn't reserve the gift the way a purchase does). See
`src/monitoring/market_monitor.py`'s `_offer_targets`/`_maybe_send_offer_for_user`
and `src/monitoring/offer_registry.py`.

## Auto-offer mechanics (🎯 Offer boshlash / 🛑 Offer to'xtatish)

Independent of the direct-purchase pipeline above: instead of buying a
resale listing outright, the bot can propose a price to the seller via
Telegram's own offer mechanism —
[`payments.sendStarGiftOffer`](https://core.telegram.org/method/payments.sendStarGiftOffer).
This only works on listings whose owner has enabled offers on that specific
gift (`starGiftUnique.offer_min_stars` is set) and only proceeds when the
seller passes two independent checks:

- **Seller level** — Telegram's own account level from
  [`StarsRating`](https://core.telegram.org/type/StarsRating)
  (`UserFull.stars_rating.level`), which rises as an account spends more
  Stars. Must exactly equal `/setofferlevel`'s value (default `1`). A
  channel-owned gift has no such rating and is treated as level `0`.
- **Seller gift count** — how many gifts the seller currently has on
  display on their profile (`UserFull`/`ChatFull.stargifts_count`). Must be
  strictly less than `/setoffernftcount`'s value (default `3`).

If the seller's profile can't be resolved at all (privacy settings,
deleted account, etc.), no offer is sent — this filter fails closed.

This reuses the same market-scanning loop as `/startg`/`/stopg` (so it only
finds anything while market monitoring is `RUNNING`), but its decision is
completely separate from `MAX_NFT_PRICE`: a listing can be too expensive to
buy outright and still get an offer, or vice versa.

### Configuring the offer filter (any user — their own settings)

```
/setofferlevel <N>        Required seller level, exact match (default 1)
/setoffernftcount <N>     Seller must own fewer than N gifts (default 3)
/setofferprice <N>        Offer price in Stars (default 125)
/setofferexpiry <hours>   One of 6, 12, 24, 36, 48, 72 (default 6)
```

`/setofferexpiry` only accepts those six values because
`payments.sendStarGiftOffer` itself only accepts those exact durations, in
seconds (21600–259200) — anything else is rejected by Telegram before it
ever reaches the network call.

### Picking which gift types to offer on

Tap **🎯 Offer boshlash** on the bot's persistent keyboard. The bot fetches
every currently resalable gift *type* (`payments.getStarGifts`, the same
call market monitoring already makes) and shows each one as its own photo
message (sticker + name/price) with a toggle button underneath — tap a
gift to mark it ✅ selected, tap again to unmark it. Up to `PAGE_SIZE` (8)
gift types are shown per page, with **◀️ Oldingi** / **Keyingi ▶️** buttons
when there are more. A **▶️ Start** button always sits at the bottom.

Tapping **▶️ Start** with at least one gift type checked saves that
selection (`offer_selected_gift_types`) and starts an unbounded run: the
bot keeps sending offers, one per matching listing, for as long as its
gift type is one of the selected ones and it passes the seller-level/gift-
count filter above — indefinitely, not a fixed count. Starting with
nothing checked shows "❌ Avval kamida bitta NFT tanlang" instead.

Tap **🛑 Offer to'xtatish** at any point to abort immediately — no further
offers are sent once this lands, mirroring how `/stop` guards the
direct-purchase pipeline.

Sending an offer reserves that many Stars from the account's balance for
the offer's duration; Telegram refunds them automatically if the seller
declines or the offer expires unanswered. This bot's own
`star_gift_offers` table is kept in sync with expiries via a periodic
sweep, but live accept/decline pushes from the seller aren't currently
watched for — check `/status` for current counts per state.

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
- The user account's session (whether from `TELEGRAM_SESSION_STRING` or a
  bot-mediated `/login`) is the only thing that grants access to your
  account — treat it exactly like a password. A `/login`-completed session
  is saved in `data/app.db`, never in `.env` or any other file that could
  end up committed to git.
- `/login`, `/code` and `/password` — the only place the control bot ever
  touches a login code or 2FA password — work for **any** Telegram user,
  each strictly on their own account/session; the control bot never asks
  for, stores, or logs anyone's Telegram password. `generate_session.py`'s
  2FA prompt uses `getpass` (hidden terminal input) for the same reason.
  One user's session is never exposed to, or usable by, another.
- No owner-only command (channel/market monitoring, channel-list
  management, `/setmaxprice`, `/status`, `/addadmin`/`/removeadmin`) works
  for anyone but the exact account in `OWNER_TELEGRAM_ID`. Being listed in
  the `admins` table (via `/addadmin`) grants nothing beyond what any
  other logged-in user already has. Usernames are never used for
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
  has all of `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `CONTROL_BOT_TOKEN`,
  `OWNER_TELEGRAM_ID` set (`TELEGRAM_PHONE` and `TELEGRAM_SESSION_STRING`
  are optional — see "Telegram account authentication" above).
- **The user account was never authorized and no `/login` prompt showed
  up** — check the control bot's logs/`/status`; message it as the owner
  with `/login <phone>` to kick the flow off manually.
- **`/code` or `/password` replies "Hozir kod/parol kutilmayapti"** — the
  flow isn't at that step (e.g. no `/login` was sent yet, or it already
  completed); start over with `/login <phone>` if needed.
- **Control bot doesn't respond** — confirm `CONTROL_BOT_TOKEN` is correct
  and you're messaging that exact bot; confirm your numeric user ID is in
  `OWNER_TELEGRAM_ID` or was added via `/addadmin`.
- **`/add @channel` fails to resolve** — either the username is wrong /
  your user account can't see that channel yet, or the user account hasn't
  finished `/login` yet — check `/status`.
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
                (users table = per-user session + offer settings/state)
  telegram/     client.py, user_manager.py, updates.py — Telethon wiring
                (StringSession-based; user_manager.py is the multi-tenant,
                bot-mediated /login, /code, /password flow — one
                TelegramClient per user)
  monitoring/   nft_detector.py, price_parser.py, channel_monitor.py,
                market_monitor.py, offer_state.py, offer_registry.py
                (offer_registry.py = per-user OfferState cache)
  marketplace/  listing.py, purchase.py — the real MTProto buy flow
  bot/          authorization.py, bot_app.py, keyboard.py, menu.py —
                control bot commands (owner-only vs. offer-tashlash split)
  notifications/notifier.py
  main.py
generate_session.py — one-time interactive login that prints a StringSession
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
 #   b u y - n f t - b o t  
 