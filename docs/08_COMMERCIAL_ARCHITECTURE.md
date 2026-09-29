# 08 — Commercial Architecture

**What this document is.** A specification for the hosted layer that turns
this desktop application into a product someone can buy. None of it is built.
It is written as a design rather than as code because the parts it describes —
payment processing, subscription state, an admin console — cannot be built or
verified on a development machine without a payment-processor account, a
hosted backend and a legal entity, and writing plausible-looking versions of
them would produce exactly the failure this audit spent its length fixing: an
interface that reports success while nothing behind it works.

**What was built instead.** The *substrate*: the local measurements and
controls a commercial layer has to draw on, all of them real and tested.

| Built (this repository) | Not built (this document) |
|---|---|
| Usage metering — runtime per profile / workspace (F37) | Rating and invoicing |
| Plans as named, enforced limits (F38) | Plan entitlement issued by a server |
| Named, scoped, revocable API tokens (F39) | User accounts, OAuth, 2FA |
| Data export and hard deletion (F40) | Cloud sync, cross-device state |
| Workspaces (`clients`) with per-profile attribution | Team roles and sharing |
| Local audit log | Tamper-evident, centralised audit |

---

## 1. The decision this document does not make

Whether to sell this at all, and to whom, is a business decision. What follows
assumes the answer is "later, to multi-account operators", which is what the
substrate was built for. If the answer changes to "never — it is my own tool",
**nothing here is needed** and the substrate remains useful on its own: the
metering answers "which profiles do I actually use", the plans make the limits
findable, the scoped tokens contain the damage of a leaked automation
credential, and the export is how a laptop gets replaced.

That is the honest framing: none of §2–§7 is a prerequisite for the product
being good. They are prerequisites for it being a business.

---

## 2. What has to exist server-side

Five services, in dependency order. Effort figures assume one experienced
full-stack engineer and include tests, not marketing.

### 2.1 Identity (**L**, 3–5 weeks)

Email + password with TOTP, plus OAuth (Google, GitHub). Sessions, device
list, revocation.

The desktop app currently has **no concept of a user**. Adding one is not a
login screen; it is a new axis through every existing object. The honest cost
is mostly in deciding what a profile belongs to when the answer stops being
"this machine".

*Depends on:* nothing. *Blocks:* everything else.

### 2.2 Entitlement (**M**, 2–3 weeks)

The server issues a signed, short-lived entitlement: plan id, limits, expiry.
The desktop app already knows how to apply a plan ([`plans.py`](../src/multiprofile/plans.py));
what it does not have is a trustworthy source for one.

**Design constraint that matters:** the app must work **offline**. An
entitlement carries an expiry measured in weeks, and the failure mode when it
lapses must be a warning and a degraded tier, never a refusal to open a
browser the operator already paid for. An anti-detect tool that stops working
because a licence server is down destroys exactly the accounts it exists to
protect.

*Depends on:* 2.1. *Blocks:* 2.3.

### 2.3 Billing (**L**, 4–6 weeks)

Stripe for international, Razorpay for India (UPI is not optional in that
market). Subscriptions, trials, coupons, dunning, invoices, tax handling
(GST, EU VAT/OSS).

**Do not hand-roll any of this.** The list above is roughly a year of
edge-case discovery that both providers have already done.

*Metering feeds this.* The `usage_sessions` table already records what a
runtime-priced or fair-use plan would need: profile, workspace, start, end,
seconds, and how the session ended. Two properties were built deliberately
with a bill in mind — usage is attributed to the workspace that owned the
profile **at the time**, and an abandoned session is closed at the last
heartbeat rather than at the next process start, so the meter cannot be shown
to round in the vendor's favour. That is the difference between a number a
customer will accept and one they will dispute.

*What is still missing between metering and a bill:* aggregation into billing
periods, a rating table, proration on plan change, and an idempotent
usage-report upload. Call it **M**, 2 weeks, on top of the billing work.

*Depends on:* 2.1, 2.2. *Blocks:* revenue.

### 2.4 Sync (**XL**, 6–10 weeks)

Profiles, cookies and session state across devices. This is the hardest item
and the most requested one.

**It must be end-to-end encrypted.** The payload is live session credentials
for the customer's accounts; a server that can read them is a target worth
attacking and a liability worth avoiding. E2E means the server cannot help
with conflict resolution, which makes last-writer-wins with vector clocks the
realistic design and makes "profile locking" (§2.5) load-bearing rather than a
nicety.

*Depends on:* 2.1. *Blocks:* teams being genuinely useful.

### 2.5 Teams (**L**, 3–4 weeks)

Roles (owner / admin / manager / member), profile sharing with view / launch /
edit levels, per-user audit, and **profile locking** so two people cannot open
one profile simultaneously — which, on an anti-detect product, is not a
convenience feature but a correctness one: two concurrent sessions on one
identity from two IPs is the signature the product exists to avoid.

*Depends on:* 2.1, and 2.4 to be worth much.

---

## 3. Admin console (**M**, 2–3 weeks)

Users, subscriptions, revenue, abuse reports, refunds, impersonation for
support.

**One rule worth writing down now:** support impersonation must be logged,
time-boxed, and must never expose cookie jars in plaintext. The moment
support can read a customer's live sessions, the company is one disgruntled
employee away from its worst possible incident.

---

## 4. What this costs

| Item | Effort | Depends on |
|---|---|---|
| Identity | L (3–5 wk) | — |
| Entitlement | M (2–3 wk) | Identity |
| Billing | L (4–6 wk) | Identity, Entitlement |
| Usage → invoice | M (2 wk) | Billing, **metering (done)** |
| Sync (E2E) | XL (6–10 wk) | Identity |
| Teams | L (3–4 wk) | Identity, Sync |
| Admin | M (2–3 wk) | Identity, Billing |
| Signed builds + auto-update (F12) | M (2–4 wk) | — |
| Windows + macOS builds | XL (8–12 wk) | — |

**Minimum viable commercial tier** — identity, entitlement, billing,
usage→invoice, admin, signed builds — is roughly **16–23 engineer-weeks**,
call it **4–6 months for one engineer** or **2–3 months for two**, excluding
sync, teams and cross-platform.

Two items outside engineering gate the same launch: a legal entity with Terms
and an Acceptable Use Policy (see [`07`](07_SECURITY_AND_COMPLIANCE.md) §7),
and payment-processor onboarding, which for this product category is not
guaranteed and should be validated **before** the billing work starts, not
after. Both processors underwrite anti-detect browsers case by case.

---

## 5. Sequencing

The dependency graph says identity first, but the *risk* graph says otherwise.

**Validate payment-processor onboarding before writing any billing code.** If
Stripe and Razorpay both decline the category, the entire §2.3 estimate is
wasted and the product needs a different distribution model — a one-time
licence sold through a reseller, for instance, which needs almost none of
this. It is a two-week conversation that can invalidate three months of work,
so it goes first.

After that, the order that produces something sellable soonest:

1. **Signed builds and auto-update (F12).** Independent of everything, and no
   commercial buyer should be asked to install an unsigned binary with no
   update path. It is also the only item on this list that improves the
   product for its current single user.
2. **Identity + entitlement.** The axis everything else hangs on.
3. **Billing + usage→invoice.** Revenue.
4. **Admin.** Becomes urgent the day there is a second customer.
5. **Sync**, then **teams**.

Cross-platform builds are deliberately last. They are the largest single item
and they multiply the maintenance cost of everything above them; doing them
before there is evidence of demand is how a small team stops shipping.

---

## 6. What would make this not worth doing

Stated plainly, because a commercial architecture document that cannot
articulate its own failure case is a sales deck:

- **Payment processors decline the category.** Likely enough to check first.
- **F3 is never closed.** Competitors ship patched engines; a stock-Chromium
  product has a detectability ceiling it cannot raise. A buyer comparing on
  detection rate will find the difference. This does not preclude selling on
  workflow, price or Linux-first support — but it does preclude selling on
  "undetectable", and that is what this market advertises on.
- **Single-maintainer risk.** Sixteen to twenty-three engineer-weeks of
  hosted infrastructure creates an on-call obligation that does not pause.
  A hosted tier is a commitment to be available, not a feature to ship.

---

## 7. The recommendation

**Do not build §2 yet.** Two things should happen first, in this order.

**Close F3, or decide not to.** It is the product's competitive ceiling, it
is the first decision in [`06_ROADMAP`](06_ROADMAP.md), which is written as
a fork because of it, and it is the one open question on this list that
nobody else can answer. Every estimate above assumes a product worth
selling; that assumption rests on F3.

**Then validate demand at a price.** The substrate built in this phase makes
that measurable rather than hypothetical: the metering already answers "how
much would this customer's usage actually cost to serve", and the plan limits
already express what a tier would contain. Those are the two numbers a pricing
conversation needs, and until recently neither existed.

---

*Companion documents:* [`02_AUDIT_REPORT.md`](02_AUDIT_REPORT.md) (F37–F40
built the substrate), [`07_SECURITY_AND_COMPLIANCE.md`](07_SECURITY_AND_COMPLIANCE.md)
(what a hosted tier changes about every security answer).
