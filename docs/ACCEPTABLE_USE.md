# Acceptable Use Policy — **DRAFT**

> **This is an engineering draft, not legal advice, and it has not been
> reviewed by a lawyer.** It exists so the policy questions are answered
> before they are answered badly under time pressure, and so counsel has
> something concrete to review rather than a blank page. Do not publish it,
> link it from a pricing page, or rely on it in a dispute until a qualified
> lawyer in the operating jurisdiction has reviewed it. Terms of Service, a
> privacy notice and a refund policy are separate documents that do not exist
> yet.

---

## Why this document exists at all

Multiprofile makes it easy to run many browser identities that do not link to
each other. That is a legitimate and widely-sold capability, and it is also
precisely what someone committing fraud would want. Every comparable product
publishes a policy on this, and none of them can operate without one: payment
processors, app stores and business customers all ask for it during
onboarding, and the absence of one is a reason to decline.

It also has an internal purpose. Writing down where the line is — **before**
there is revenue attached to a particular customer — is what makes it possible
to enforce later.

---

## 1. What this product is for

Multiprofile is built for operators who legitimately need separate, unlinked
browser identities:

- **Agencies** managing advertising or marketplace accounts for multiple clients, where mixing client sessions is a contractual and practical failure.
- **E-commerce sellers** operating multiple storefronts that platforms permit to be held separately.
- **Ad operations** teams managing several advertiser accounts.
- **Affiliate marketers** operating within the terms of the programmes they belong to.
- **QA and web developers** testing how a site behaves for different devices, locales and user states.
- **Web scraping and market research** conducted within the target's terms and applicable law.
- **Privacy-conscious users** separating personal, professional and financial identities.

---

## 2. Prohibited uses

Do not use Multiprofile to:

**Commit fraud or theft**
- Payment fraud, carding, or testing stolen payment instruments.
- Creating or operating accounts using another person's identity documents or personal data.
- Account takeover, credential stuffing, or accessing an account you are not authorised to access.
- Fake reviews, fake engagement, vote manipulation, or any misrepresentation of independent endorsement.

**Evade enforcement of law**
- Circumventing a ban, suspension or legal order imposed for illegal conduct.
- Sanctions evasion, money laundering, or operating on behalf of a sanctioned entity.
- Evading age verification or other legally mandated identity checks.

**Harm people**
- Harassment, stalking, or coordinated abuse of an individual.
- Creating accounts to impersonate a real person or organisation.
- Distributing malware, phishing pages, or content sexualising minors.

**Attack infrastructure**
- Denial-of-service, credential brute-forcing, or unauthorised penetration of systems you do not own or have written permission to test.
- Scraping at a volume or rate designed to degrade a service.

---

## 3. The genuinely grey area, stated honestly

Most platforms' terms of service restrict multiple accounts in ways that are
broader than the law requires, inconsistently enforced, and in some
jurisdictions unenforceable. Operating several accounts on a platform whose
terms discourage it is **a contract matter between the operator and that
platform**, not in itself illegal, and this policy does not pretend otherwise.

Where that line sits:

- **Permitted here:** operating accounts a platform allows you to hold, including ones it permits under a business or agency arrangement; separating identities the platform does not require you to link.
- **Prohibited here:** using multiple identities to do something that would be fraud, theft or harassment with one identity. The count of identities is not what makes conduct wrong; it is what makes it scale.

A customer asking "will this let me evade a ban?" should be told: if the ban
was for illegal conduct, no, and the account will be terminated for asking. If
it was a platform policy disagreement, that is between them and the platform,
and this product takes no position and offers no guarantee.

---

## 4. Enforcement

**Today, this is unenforceable and that should be said plainly.** The product
is a local Linux application. It makes no network requests of its own, reports
no telemetry, and has no account system — there is nothing to suspend and no
way to detect a violation. Publishing this policy alongside the current build
states an intention, not a capability.

**If a hosted tier is built** (see
[`08_COMMERCIAL_ARCHITECTURE.md`](08_COMMERCIAL_ARCHITECTURE.md)), enforcement
becomes both possible and obligatory:

1. Reports from affected platforms or individuals, with a named contact to receive them.
2. Volume and pattern signals that arrive with a hosted account system.
3. A documented process: investigate, suspend, allow appeal, terminate.
4. Refund policy on termination — determined per case; fraud is not refunded.

Acquiring that capability also acquires the duty to use it. A hosted product
that can see abuse and does not act on it is in a worse position, legally and
commercially, than a local one that cannot see anything.

---

## 5. Open questions for legal review

These are the points an engineering draft cannot settle:

1. **Operating jurisdiction and governing law.** Affects everything below.
2. **Whether §3's framing is defensible** in the jurisdictions where customers will be. "Contract matter, not illegal" is the author's understanding and is not legal advice.
3. **KYC thresholds.** The brief mentions KYC for high-volume accounts. At what volume, collecting what, retained for how long, and under whose obligation?
4. **Liability for downstream use**, and whether a disclaimer meaningfully limits it.
5. **Data-protection role** — controller or processor — once cloud sync exists. It determines whether a DPA is needed and who signs it.
6. **Export control**, if any applies to privacy tooling in the operating jurisdiction.
7. **Payment-processor category acceptance**, which is commercial rather than legal but is decided by the same conversation and should happen first.

---

*Related:* [`07_SECURITY_AND_COMPLIANCE.md`](07_SECURITY_AND_COMPLIANCE.md) §7
for where this sits in the compliance picture,
[`08_COMMERCIAL_ARCHITECTURE.md`](08_COMMERCIAL_ARCHITECTURE.md) §6 for why
processor acceptance is the first thing to validate.
