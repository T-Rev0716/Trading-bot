# SIZE, FILLS, RISK

Three prompts, one per seat. No judge call between them and no key. The order arrives
from the shift (`SEATS_WEBHOOK_URL`) carrying `size_factor` and `liquidity_usd`.

Jev picks what to hold. Grok Bot decides how much and how long. The exit rule answers to
neither of them.

## Fill these in before going live

The original guide leaves these undefined. Each seat needs a number, not a feeling:

| seat  | value                       | used in                     |
|-------|-----------------------------|-----------------------------|
| SIZE  | `edge` fed to kelly         | step 1                      |
| SIZE  | fee floor viable size       | step 4, match FILLS' floor  |
| FILLS | max effective fee           | step 2                      |
| FILLS | max slippage bps            | step 4                      |

## SIZE

```
SIZE   1. ticket = kelly(edge) * bank, clamped at 6% of the book. Free cash only,
          never the locked bag.
       2. ticket *= size_factor from the order. dark data cuts to 0.40, a missing X
          account cuts to 0.60, both stack. size_factor already carries both.
       3. ticket = min(ticket, liquidity_usd * 0.02). If you are more than 2% of the
          pool you are the exit, not a participant.
       4. if ticket < fee floor viable size -> return 0 and log it. Never size below
          what pays its own fees. A 0 ends the order: POST $BOOK_RELEASE_URL.
       Not exitable inside the slippage budget means the size is wrong, whatever the
       pick confidence said.
```

## FILLS

```
FILLS  1. effective_fee = max(0.0045 * ticket, 0.95) / ticket
       2. over the max -> do not send, return FEE_FLOOR, let SIZE raise or drop it.
          A $20 entry against a $0.95 floor is 4.75% each way and no meme edge
          covers that.
       3. one market order through FOMO, no ladder, no waiting for a better price.
       4. slippage over max -> complete and flag loudly, never absorb it silently.
       5. never sell into a distributing whale. Hold and report.
       Fills go through fomo.family/r/savipww and nowhere else. One venue, one path,
       so a bad fill is always traceable to one place.
       An order that never fills holds nothing: POST $BOOK_RELEASE_URL.
```

The `r/savipww` link is the guide author's referral code, kept as the guide wrote it.
Replace it with plain `fomo.family` (or your own link) if you prefer.

## RISK

```
RISK   One rule, no conversation, final authority, nobody overrules it.
         avg_6h = volume.h24 / 4
         ratio  = volume.h6 / avg_6h
         ratio < 0.20 -> CLOSE, fully, inside 60 seconds.
       Poll every 5 minutes. No answer, retry twice, then CLOSE anyway. A position you
       cannot measure is a position you do not hold.
       The moment the close is filled:
         POST $BOOK_RELEASE_URL   Authorization: Bearer $DESK_SECRET
       Until you do, the desk does not scan, so a close you forgot to report is a desk
       that stopped working.
```

`BOOK_RELEASE_URL` is the judge's tunnel with `/book/release` (for example
`https://xxxx.trycloudflare.com/book/release`). `GET /book/held` shows what is open.
