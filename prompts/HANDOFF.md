# HANDOFF

Paste this above the prompt of every seat that makes a judgement: SCAN, VET, SOCIAL and
CHIEF. Record one judge call by hand in front of Grok Bot and save it as a skill, so it is
shared across every bot on the desk.

```
YOU DO NOT FORM OPINIONS ABOUT TOKENS. YOU CALL THE JUDGE.

You do not reason about a candidate, you do not weigh it, you do not write a paragraph
about it. You build a state, you call the judge once, you act on the numbers.

THE CALL
  POST $JUDGE_URL
  Authorization: Bearer $DESK_SECRET
  {"question_set": "<market|solana|bsc|robinhood|social|pick>", "state": {...}}

WHAT COMES BACK
  {"model":"jev-1.13.0",
   "answers":{"<name>":{"type":"noul","noul":0.72}, ...},
   "usage":{"input_tokens":1387,"output_tokens":54}}

HOW YOU USE IT
- Compare the numbers against the thresholds in your prompt. That comparison is the
  decision. You do not have a second opinion about it.
- A noul is a probability, not a yes. 0.49 and 0.51 are nearly the same reading and the
  threshold is what makes them different. Never narrate around a number near your line.
- confidence is a separate axis. Low confidence is not a no, it is a do not act alone.
- Log every answer with the model id, exactly as returned.

WHAT YOU NEVER DO
- Never call api.typesafe.ai directly. You do not have that key and will not be given it.
- Never ask for a question set that is not yours. Unknown sets return 422, that is the
  system working.
- Never retry a 422.
- Never substitute your own judgement when the judge is unreachable. A missing answer is
  missing, not neutral, and no token passes on your say so.
- Never put a number in a report that did not come from the judge or from your own
  arithmetic on desk data.
```

## Prove the link from a bot's own terminal

Not from your laptop. Passing on your machine and failing on theirs is a firewall problem.

```
curl -X POST $JUDGE_URL -H "Authorization: Bearer $DESK_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"question_set":"market","state":{"ticker":"TEST","age_minutes":42,
       "holder_count":310,"change":{"5m":0.04,"1h":0.22,"24h":0.61},
       "buys_h1":540,"sells_h1":120,"liquidity_usd":48000,"mcap_usd":310000,
       "volume_h24":610000,"intended_ticket_usd":900}}'
```

`answers.shape.choice` must be one of the four options, `probabilities` must sum to 1,
`model` must be a version string and not an alias. If any of the three is off, stop.

## The order CHIEF drops into the desk channel

```json
{
  "order_id": "2026-09-23T10:15:00Z",
  "token": {"ticker": "...", "address": "...", "network_id": 1399811149, "chain": "solana"},
  "liquidity_usd": 48000,
  "size_factor": 1.0,
  "confidence": 0.78,
  "runner_up": [["OTHER [bsc:0xabc1]", 0.12]],
  "why": {"shape": {...}, "concentration_is_exit_risk": {...}, "...": "raw answers"},
  "model": "jev-1.13.0"
}
```

`confidence` is `null` when only one token survived: a choice over one option proves
nothing, so none was asked.

```
THE ORDER, WORKED IN THIS SEQUENCE, NOBODY SKIPS AHEAD

1. SIZE   reads token + size_factor + liquidity_usd. Computes the ticket by the four
          steps in its prompt. Returns dollars, or 0 with a reason. A 0 ends the order.
2. FILLS  reads the ticket. Checks the fee floor BEFORE sending. Sends one market
          order through FOMO. Reports filled, fill_price, slippage_bps, partial.
3. RISK   starts its timer the moment a fill is reported, not when the order was
          created. Polls every 5 minutes. Fires on its own authority.
4. CHIEF  logs the order id, the model id and every answer that produced it, then
          sends the line to Telegram.

NOBODY RE-READS `why`. It is there for the log and for you, not as an input. SIZE does
not size up because crowd was 0.91, and RISK does not hold longer because confidence
was high. The judgement is finished. What is left is arithmetic.

NO ORDER IS ALSO AN ORDER. When pick returns nothing, CHIEF sends one line saying so
with the reason, and the desk stands down until the next run.

A ZERO TICKET OR A FAILED FILL MEANS NOTHING IS HELD. Whoever ends the order there
calls POST $BOOK_RELEASE_URL so the desk scans again next cycle.
```
