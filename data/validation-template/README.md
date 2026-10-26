# Labeling guide and import format

These files are for the people who prepare, label and adjudicate conversations for the workload validation of the adopted extraction profile. The study is displayed as **M7**; its internal key is `m8`. The protocol is [`experiments/M8-workload-validation.md`](../../experiments/M8-workload-validation.md), and the operator workflow is [`docs/workload-validation.md`](../../docs/workload-validation.md).

| File | Purpose |
|---|---|
| `labeling-template.csv` | One row per conversation. Each labeler fills in their own copy. |
| `adjudication-template.csv` | One row per conversation the adjudicator decides. |
| `label-record.schema.json` | The same row as JSON, for JSONL exports from a labeling tool. |

Rows whose `conversation_id` starts with `EXAMPLE-` are illustrations. Delete them before labeling; the importer refuses them. They were written by the study author to show the format, and they are not data.

## Roles and independence

| Role | Does | Never |
|---|---|---|
| Coordinator | Samples and de-identifies conversations, fills in `conversation_id`, `split` and `text`, and gives each labeler a copy. Puts the returned files in the private input directory: `<input>/<dataset>/labels/` for label files, `<input>/<dataset>/adjudication/` for the adjudicator's. | Labels conversations they coordinate. Saves conversations or labels inside a git repository, or uploads them to the public RouteWise dashboard. |
| Labelers (two per conversation) | Label every column independently, using only this guide and the conversation. | See any model output, RouteWise trace or dashboard result for these conversations. Discuss a conversation with the other labeler before both have submitted. Use an AI system to suggest, fill in or check a label. |
| Adjudicator (a third person) | Decides each conversation in the adjudication queue, still without model outputs. | Adjudicate a conversation they labeled. |

Model-generated labels are not accepted in any role. The importer cannot verify this, so the people involved are responsible for it. The protocol records who they are.

Exchange files only through channels your organization approves for customer data. Each labeler's copy of the conversations is as sensitive as the source system.

## Conversation text

The coordinator prepares the text before labeling, and nobody edits it afterwards.

- **One line per message:** `[YYYY-MM-DD HH:MM] Speaker: message`. Join wrapped lines with spaces. Keep messages in time order.
- **Speaker** is exactly `Customer`, `Agent` or `System`. Use `Agent` for any store employee and `System` for automated store messages. Agent and System messages count as store statements.
- **Timestamps** are in the store's local time, where its agents meant "today" and "tomorrow". Relative dates resolve from them, so do not drop or round them.
- **De-identify** names, email addresses, phone numbers, postal addresses and payment details. Replace them with placeholders such as `[NAME]` or `[EMAIL]`.
  - Keep order IDs, amounts, currencies and dates exactly as written, because they are the labels.
  - If an order ID must be masked, replace it consistently everywhere with a surrogate of the same shape (`ORD-48213` → `ORD-90517`).
  - If dates must be shifted, shift every timestamp and every written date in the conversation by the same whole number of weeks, so that weekdays still match.
- **Length:** at most 20,000 characters.
- **Currencies outside USD, EUR, GBP, CAD, AUD, JPY and CHF** cannot be represented by the output schema. Leave such conversations out when sampling, and count them in the sampling-frame description.

The conversation text is sent to Nebius Token Factory when the profiles run. The importer warns about likely email addresses, phone numbers and card numbers, but it is not a de-identification tool.

## Columns

Blank means null. Booleans are `true` or `false`. In CSV files, separate evidence quotes with `|` and tags with `;`. In a spreadsheet, format the date and `order_id` columns as text, so the spreadsheet keeps `YYYY-MM-DD` dates and leading zeros.

| Column | Filled by | Content |
|---|---|---|
| `conversation_id` | Coordinator | A stable, unique ID that is the same in both labelers' copies. It must not start with `EXAMPLE-` or `SYNTH-`. Prefer an opaque code over a ticket number from your support system. Give it at least five characters, mixing letters and digits (such as `PV-3K7Q`), so that `leak-scan` can recognize it. |
| `split` | Coordinator | `development` for the pilot, `heldout` for the confirmation. |
| `text` | Coordinator | The conversation, formatted as above. |
| `refund_requested` … `refund_date` | Labeler | The six fields, defined in the field contract below. |
| `review_required` | Labeler | `true` when a careful person cannot determine a single correct value for some field under the contract. |
| `review_reason` | Labeler | Required exactly when review is required. Use a code, optionally followed by `: explanation`. |
| `evidence` | Labeler | Verbatim quotes that support the label. |
| `scenario_tags` | Labeler | One or more tags. The first is the primary scenario. |
| `labeler_id` | Labeler | A stable pseudonym, such as `L1`. |
| `notes` | Labeler | Optional. It is never scored. |

## Field contract

This is the contract both deployment profiles are scored against. If your business defines a field differently, for example using the date money reaches the customer as the refund date, the profiles were not designed for that definition. Settle it before labeling, because changing the contract changes the study.

- **`refund_requested`.** `true` if the customer asks for money back anywhere in the conversation, including in an earlier message and including when they later accept something else. A request only for a replacement, repair, exchange, store credit or discount is `false`.
- **`refund_amount`.** The numeric refund amount stated in the conversation, as a plain number such as `1234.50`.
  - If an amount is agreed, use the agreed amount, not the purchase price or a rejected offer.
  - "Full refund" with no number means blank.
  - Leave it blank if no numeric refund amount is stated.
- **`currency`.** The ISO code of `refund_amount`, and blank when the amount is blank.
  - `$` means USD unless another dollar is named (`CA$` is CAD, `A$` is AUD).
  - `€` is EUR, `£` is GBP and `¥` is JPY.
- **`order_id`.** The complete order identifier, copied exactly with its prefix and punctuation, without a leading `#`.
  - If several orders appear, use the one the request concerns.
  - Product names, SKUs, tracking numbers, invoice numbers and dates are not order IDs.
  - Leave it blank if no order ID appears.
- **`contact_date`.** The date of the first `Customer` message, taken from its timestamp. The importer checks it.
- **`refund_date`.** The calendar date on which the store issued, or will issue, the refund, as stated by the store.
  1. Only Agent and System statements count. A customer restating a date never establishes it.
  2. A relative expression resolves from the date of the store message that contains it.
  3. A month and day with no year takes that message's year.
  4. A date on which money should arrive, appear, clear, post or reach the customer is not an issue date. If only such a date is given, leave `refund_date` blank.
  5. When the store explicitly corrects or updates the issue date, the latest corrected statement wins.
  6. If the store says the refund was or will be issued with no timing at all, leave it blank (no review).
  7. If the store's timing words do not fix one calendar date, review is required (next section).

| Store says (message dated Mon 2026-03-02) | `refund_date` | Review |
|---|---|---|
| "issued today" / "issued in three days" / "the day after tomorrow" | 2026-03-02 / 2026-03-05 / 2026-03-04 | no |
| "was issued yesterday" / "issued two days ago" | 2026-03-01 / 2026-02-28 | no |
| "issued this Friday" / "on Friday" (same week) | 2026-03-06 | no |
| "issued in a week" / "a week from today" | 2026-03-09 | no |
| "issued on March 10" / "issued 2026-03-10" | 2026-03-10 | no |
| "your refund has been processed" (no timing) | blank | no |
| "issued tomorrow; it should reach your bank in 5 days" | 2026-03-03 (the arrival is ignored) | no |
| "should arrive in your account by Friday" (arrival only) | blank | no |
| "within 3-5 business days" / "in 3 working days" | blank | **yes**: `issue_date_ambiguous` |
| "in 3-5 days" / "within 3 days" / "by Friday" / "no later than the 10th" | blank | **yes**: `issue_date_ambiguous` |
| "next Friday" / "next week" / "by the end of the week" / "shortly" / "soon" | blank | **yes**: `issue_date_ambiguous` |
| "issued March 5" … later "issued March 7" with no correction | blank | **yes**: `conflicting_statements` |
| "issued March 5" … later "correction: issued March 7" | 2026-03-07 | no |

## When review is required

Mark `review_required` true when a careful person cannot determine one correct value for some field under the contract. Difficulty alone is not a reason: if you can determine the value, label it. Fill in every field you can determine, and leave the undeterminable field blank.

| Code | Use when |
|---|---|
| `issue_date_ambiguous` | The store's issue timing does not fix one date: business or working days, ranges, bounds, "next <weekday>", vague periods. |
| `conflicting_statements` | The store gives different values for a field and never corrects one. |
| `amount_ambiguous` | Several refund amounts are discussed and none is agreed. |
| `order_ambiguous` | Several orders appear and you cannot tell which one the request concerns. |
| `request_ambiguous` | You cannot tell whether the customer asks for money back. |
| `other` | Anything else. Explain it: `other: …`. |

A released answer to a conversation that requires review counts as a wrong release, whatever its field values.

## Evidence

Quote the conversation text exactly, and separate quotes with `|`. Quote at least the words that contain each non-blank value (except `contact_date`), the customer's refund request when `refund_requested` is true, and the ambiguous or conflicting statement when review is required. The importer checks that every quote occurs in the text, ignoring case and spacing.

## Scenario tags

Use lowercase tags made of letters, digits, `-` or `_`, and keep a shared list across labelers. The first tag is the primary scenario, used for per-scenario tables and for the cluster sensitivity interval. Tags describe the conversation, not how hard it seems. Some useful tags:

`relative_issue_date`, `absolute_issue_date`, `past_issue_date`, `no_issue_date`, `arrival_date_only`, `business_days`, `range_or_bound`, `vague_timing`, `corrected_date`, `conflicting_statements`, `multiple_orders`, `partial_refund`, `agreed_amount`, `full_refund_no_amount`, `no_refund_request`, `replacement_or_repair`, `cancellation`, `damaged_item`, `late_delivery`, `return_received`.

## Adjudication

The importer pairs the two labels for each conversation. It queues a conversation when the labels differ in a way that changes a metric:
- the review-required status differs; or
- both labels say the conversation is answerable, and they differ on any field.

When both labels require review, their field values are never scored, so they are not queued. The queue file, `adjudication_queue.csv`, is the adjudication template with both labels shown in `ref_` columns for reference.

For every queued row the adjudicator fills in `decision`, `adjudicator_id` and `reason`:
- **`label`.** Also fill in the label columns with the correct label under the contract.
- **`exclude`.** Use this only when the conversation cannot be used: it is not a support conversation, it cannot be de-identified, or its text is broken. Never exclude a conversation because it is hard or ambiguous; that is what `review_required` is for. Exclusions are counted and reported.

The adjudicator may also decide a conversation that was not queued, for example to exclude it. Rows left blank decide nothing.
