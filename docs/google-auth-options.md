# Google Authentication & Audience Options for Piyo AI

**Author:** Piyo AI Team  
**Date:** 2026-10-03  
**Status:** Proposal / Decision Reference  
**Context:** Piyo AI Phase 2 (Gmail & Calendar Skill Integration)

---

## 1. Problem Statement

Piyo AI integrates with Google services (Gmail and Google Calendar) to power daily assistance tasks such as email summaries, inbox triage, draft responses, and scheduling.

Connecting to Google introduces a critical architectural dilemma:
1. **Developer / Distribution Burden:** Gmail scopes (`gmail.readonly`, `gmail.send`, `gmail.modify`) are classified as **Restricted Scopes** by Google. Providing a shared OAuth client for public consumer use requires an annual **Cloud Application Security Assessment (CASA Tier 2)** audit, which carries significant cost, complexity, and compliance overhead.
2. **User Onboarding Friction:** A pure "Bring Your Own Key" (BYOK) model delegates Google Cloud Console project creation to end users. Non-technical users struggle with project creation, API enablement, OAuth consent screens, and credentials generation.
3. **Audience Constraints:** Google Cloud restricts audience configurations based on organization type, directly affecting who can authenticate and token persistence.

---

## 2. Google OAuth Audience Types Explained

When configuring an OAuth consent screen in Google Cloud Console, Google presents two User Types:

### Internal
* **Availability:** **Strictly limited to Google Workspace organizations** (e.g., corporate accounts like `@mycompany.com`). Personal `@gmail.com` accounts cannot select this option.
* **Scope:** Only users inside that specific Workspace tenant can authenticate.
* **Verification:** Does not require Google verification or CASA audits.
* **Verdict for Piyo AI:** **Not viable for general consumer users or standard `@gmail.com` accounts.** Only applicable if Piyo AI is deployed as an internal enterprise tool.

### External
* **Availability:** Any Google user (both `@gmail.com` and Workspace accounts).
* **Two Operating States:**
  1. **Testing Status:**
     * Limited to up to 100 manually specified "Test Users" (by email address).
     * Does not require Google verification.
     * **Major Drawback:** Google automatically expires refresh tokens after **7 days**, forcing users to re-authenticate every week.
  2. **In Production (Published) Status:**
     * Available to all Google users. Refresh tokens do not expire after 7 days.
     * If restricted scopes (Gmail) or sensitive scopes (Calendar) are used:
       * Requires Google OAuth App Verification.
       * Requires an annual CASA Tier 2 third-party security assessment.
       * An unverified app will display a scary warning (*"Google hasn't verified this app"*) and cap total sign-ins to 100 before blocking new users.

---

## 3. Evaluated Options for Piyo AI

### Option 1: Bring-Your-Own-Client (BYOK) via Google Cloud Console (Recommended for Phase 2 / Beta)

In this approach, each user creates their own Google Cloud project and provides their own OAuth Client ID and Secret to Piyo AI.

* **Configuration:**
  * **User Type:** `External`
  * **Application Type:** `Desktop App`
  * **Publishing Status Strategy:**
    * *Standard Testing Mode:* Safe, but tokens expire every 7 days.
    * *Personal "Publish to Production" (Unverified):* The user clicks "Publish App" in their personal project. Since the user is the sole user of their own project, Google does not enforce the 100-user block. The user clicks *"Advanced → Go to Piyo AI (unsafe)"* once during setup. **Tokens remain valid indefinitely without the 7-day expiration.**
* **Pros:**
  * Zero security assessment costs or liability for Piyo AI maintainers.
  * Complete privacy: API credentials and tokens remain on the user's machine (stored in system keychain).
  * No centralized API quotas or risk of domain-wide bans.
* **Cons:**
  * High friction: ~8 manual steps in Google Cloud Console. High drop-off rate for non-technical users.
* **Mitigation:** Build an in-app setup wizard (Workstream E) featuring direct deep links and step-by-step screenshots.

---

### Option 2: Pre-Configured / Official Shared OAuth Client (Recommended for Phase 5 / Public Release)

Piyo AI ships with official, embedded Google OAuth client credentials.

* **Configuration:**
  * **User Type:** `External`
  * **Publishing Status:** `In Production` (Fully Verified by Google)
  * **Security Assessment:** CASA Tier 2 passed by Piyo AI team.
* **User Flow:**
  * User clicks "Connect Google".
  * System browser opens loopback OAuth flow (`http://127.0.0.1:<random-port>`).
  * User approves scopes in standard Google dialogue. Done.
* **Pros:**
  * Flawless consumer experience (one-click connect).
  * No technical knowledge required.
* **Cons:**
  * High upfront and recurring costs (CASA security assessment fees).
  * Centralized quota management and legal compliance obligations.
  * Google policy scrutiny over prompt injection and automated email sending.

---

### Option 3: Google App Passwords via IMAP / SMTP (Low-Barrier Alternative)

Allow users who cannot navigate Google Cloud Console to connect their Gmail using standard mail protocols.

* **User Flow:**
  1. User enables 2-Step Verification on their Google Account.
  2. User generates a 16-character App Password at `myaccount.google.com/apppasswords`.
  3. User enters their email and App Password into Piyo AI settings.
* **Pros:**
  * Fast and accessible for non-technical users (takes under 2 minutes).
  * Bypasses Google Cloud Console, OAuth verification, CASA audits, and 7-day token expiration.
  * Uses standard protocols (`imaplib`, `smtplib` in Python core).
* **Cons:**
  * Only covers Gmail; Google Calendar is not supported via IMAP/SMTP (would require CalDAV or separate OAuth).
  * Less granular permissions: App Password provides full mailbox access rather than scoped OAuth grants (e.g. read-only vs. send).

---

## 4. Comparison Matrix

| Factor | Option 1: BYOK (GCP External) | Option 2: Verified Shared Client | Option 3: App Passwords (IMAP) |
|---|---|---|---|
| **Audience** | External (Self-hosted) | External (Public consumer) | Any Google Account with 2FA |
| **Setup Effort** | High (8–10 GCP steps) | Zero (1 click) | Low (Generate 1 app password) |
| **Developer Cost** | $0 | High (CASA audit fees) | $0 |
| **Maintenance** | Low | High (Annual audits, quota monitoring) | Low |
| **Token Expiry** | None if published; 7 days if in Testing | None (until user revokes) | None (until user revokes) |
| **Calendar Support** | Yes (Native Google Calendar API) | Yes (Native Google Calendar API) | No (Requires CalDAV) |
| **Scope Granularity** | High (Incremental scope grants) | High (Incremental scope grants) | Low (Full mailbox access) |
| **Target User Base** | Developers, power users, privacy-conscious | General public, everyday consumers | Non-dev testers needing quick setup |

---

## 5. Phased Recommendation for Piyo AI

1. **Phase 2 (Current Focus):**
   * Default to **Option 1 (BYOK - External)** as decided in `PLAN.md`.
   * Instruct users to select **External** in Google Cloud Console.
   * Document the "Publish to Production (Unverified)" technique in `SETUP.md` so testers do not suffer from the 7-day token expiration.
   * Provide an interactive setup wizard in the Tauri UI (Phase 2 Workstream E).

2. **Phase 3–4 (Bridge Phase):**
   * (Optional) Implement **Option 3 (App Passwords)** as a quick fallback connector in `piyo/integrations/google/` if non-technical beta testers need immediate email access without setting up GCP.

3. **Phase 5 (Public Launch):**
   * Transition to **Option 2 (Verified Shared Client)** for mainstream distribution once CASA assessment requirements are addressed.
   * Retain Option 1 as an "Advanced: Use custom Google Cloud credentials" setting for enterprise and privacy-oriented users.
