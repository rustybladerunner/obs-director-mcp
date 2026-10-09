# Audience alerts

The audience module converts a small caller-supplied object into `obs.event.v1`.
It also builds fixed alert cues for existing OBS items. It does not authenticate
with Twitch or YouTube, receive provider events, poll an API, or verify event
authenticity. Current examples and tests use synthetic events only.

## Local contract

```python
from obs_director.audience import normalize_audience_event, build_audience_registry

event = normalize_audience_event({
    "platform": "twitch",
    "kind": "subscription",
    "event_id": "synthetic-twitch-001",
    "occurred_at": "2026-01-01T12:00:00Z",
    "display_name": "Synthetic Viewer",
})
routes = build_audience_registry("Program", "Alert Text", 20, 21)
```

Pass `routes` to `EventService`, or put them in the `routes` field of the existing
`obs.event-routes.v1` local configuration. Pass the normalized event to
`obs_preview_event` or `obs_dispatch_event`. Preview remains the default. Execution
during recording or streaming also requires `allow_live=True`.

The five common fields in the example are mandatory. Unknown fields are rejected.
The object is a projection, not a raw EventSub or YouTube response. The provider
event ID must contain 1 to 256 ASCII identifier characters. The adapter derives a
stable platform-prefixed hash for deduplication. It does not hash the event kind,
so reuse of one provider ID for changed content remains a conflict.

The timestamp must be valid UTC, end in `Z`, and have at most six fractional
digits. The future ingestion layer must convert provider timestamps to this form
consistently. This pure adapter does not check event age or compare timestamps
with a clock. Display names contain 1 to 60 characters. Controls, bidirectional
formatting controls, markup delimiters and explicit URLs are rejected. Text is
sent only to the bound OBS text source; it is never HTML or executable code.

| Platform | Local kind | Additional required fields |
| --- | --- | --- |
| Twitch | `subscription`, `follow` | None |
| Twitch | `subscription_gift` | `amount`: number of gift subscriptions |
| Twitch | `cheer` | `amount`: Bits count |
| Twitch | `raid` | `amount`: viewer count |
| YouTube | `membership` | None |
| YouTube | `super_chat`, `super_sticker` | `amount`, `currency` |

Count amounts must be integers from 1 to 1,000,000. Monetary amounts must be
finite positive JSON numbers, at most 1,000,000, with at most six decimal places.
They use major currency units. `currency` must have three uppercase ASCII letters.
This checks code syntax only; it does not validate a currency registry, convert
currencies, or calculate channel revenue. These bounds are local application
limits. Unused amount and currency fields are rejected.

## Provider mapping and limitations

The following mapping was checked against official documentation on 2026-10-08.
It describes the ingestion work still needed, not a connected integration.

| Local kind | Official Twitch EventSub type | Version |
| --- | --- | --- |
| `subscription` | `channel.subscribe` | 1 |
| `subscription_gift` | `channel.subscription.gift` | 1 |
| `cheer` | `channel.cheer` | 1 |
| `raid` | `channel.raid` | 1 |
| `follow` | `channel.follow` | 2 |

Twitch subscriptions exclude resubscriptions. Subscription and gift events need
`channel:read:subscriptions`; cheers need `bits:read`; follows need
`moderator:read:followers`. Raid conditions must choose the incoming or outgoing
broadcaster. A connector must select incoming raids for this overlay. Gift and
cheer identities can be anonymous: use an explicit anonymous label, never infer
a viewer. Gift totals are counts; Bits are not currency amounts. Handle recipient
subscription notifications separately from gift batches to avoid double alerts.
See the [official EventSub types](https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/).

YouTube `newSponsorEvent` maps to `membership`; it can include a membership
upgrade. `superChatEvent` and `superStickerEvent` map to the two support kinds.
Convert their `amountMicros` to major units by dividing by 1,000,000 and preserve
the supplied currency. Do not infer money from membership level names. Milestone
messages, gifted memberships, ordinary chat, comments and sticker images are not
handled by this adapter. See the
[official liveChatMessages resource](https://developers.google.com/youtube/v3/live/docs/liveChatMessages?hl=en).

A future YouTube poller must preserve `nextPageToken` and honor
`pollingIntervalMillis`. List pages accept 200 to 2,000 results, with 500 as the
default. Initial retrieval does not provide unlimited older history. These
limits do not establish complete event coverage. See
[liveChatMessages.list](https://developers.google.com/youtube/v3/live/docs/liveChatMessages/list?hl=en).

Ordinary YouTube subscriptions are different from paid memberships. The documented
channel push feed covers video uploads and video title or description changes;
it is not an ordinary subscriber-notification feed.
[YouTube push documentation](https://developers.google.com/youtube/v3/guides/push_notifications?hl=en).
Private subscriptions do not expose subscriber identities in the recent-subscriber
list, and subscriptions are private by default. This adapter therefore rejects a
YouTube `subscription` kind; it does not promise alerts for every new subscriber.
[YouTube subscriber visibility](https://support.google.com/youtube/answer/7280745?hl=en).

## Execution and ownership

Create dedicated panel and text items in the expected scene. Start both hidden.
The builder requires distinct scene item IDs and an explicit text source name.
It returns eight routes, within the existing 16-route limit. Each cue updates the
text, shows the panel and text, waits, then hides the text and panel. The default
wait is four seconds; the accepted range is 0.1 to 15 seconds. OBS requests can
extend the total elapsed time. No route selects a scene or controls an output.

`EventService` serializes execution and deduplicates IDs in the current process.
Previews do not consume IDs. An execution attempt consumes its ID even if a later
step fails. A failure can leave an overlay visible; inspect OBS before recovery.
The default capacity is 1,024 IDs, configurable up to 4,096. A full store refuses
new executions; it does not discard old IDs. Restarting the process loses the store.
There is no rollback, durable delivery queue, restart-safe ledger, or provider
ingestion in this module. A future connector must authenticate and authorize the
channel, validate provider delivery, preserve stable IDs and timestamps, and keep
its own bounded durable delivery record. Twitch uses at-least-once delivery and
reuses the message ID on a resend. See [Twitch delivery semantics](https://dev.twitch.tv/docs/eventsub/).

Audience support amounts are display data. They are separate from trading P&L
and must never be added to a trading result. Synthetic alert demonstrations prove
local cue behavior only; they do not prove provider ingestion or real donations.
