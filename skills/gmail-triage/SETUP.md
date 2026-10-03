# Connect Google (Gmail and Calendar)

Piyo reads your mail and calendar through Google's official API. You create a small "OAuth client" in your own
Google account, so no one else sits between you and Google, and the sign-in token stays in your computer's
keychain. This takes about ten minutes, once. The same connection is used by Gmail, Calendar and the morning
brief.

## What you are agreeing to

- Piyo starts with **read-only** access to Gmail and Calendar.
- You can add more later (saving drafts, sending, archiving, changing events) on the Skills page. Sending,
  archiving, labelling and every calendar change still ask you first, each time.
- Mail and calendar text is treated as data: instructions hidden in an email are ignored.
- Disconnecting removes the token from your computer and revokes it at Google.

- [ ] I understand and want to continue

## Create a Google Cloud project

1. Open the [Google Cloud console](https://console.cloud.google.com/projectcreate) and sign in with the Google
   account whose mail and calendar you want to use.
2. Name the project **Piyo AI** and choose **Create**. Make sure the new project is selected at the top of the
   page.

- [ ] The project exists and is selected

## Turn on the two APIs

1. Open [the Gmail API page](https://console.cloud.google.com/apis/library/gmail.googleapis.com) and choose
   **Enable**.
2. Open [the Google Calendar API page](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com)
   and choose **Enable**.

If you only want one of them, enable just that one. Piyo will tell you if a tool needs an API you left off.

- [ ] Gmail API is enabled
- [ ] Google Calendar API is enabled

## Set up the consent screen

Google calls this the "OAuth consent screen" (newer consoles call it **Google Auth Platform**).

1. Open [the consent screen settings](https://console.cloud.google.com/apis/credentials/consent).
2. If asked, choose **Get started**. App name: **Piyo AI**. User support email: your address.
3. Audience: choose **External**. Contact email: your address. Accept the policy and choose **Create**.
4. Open **Audience** and choose **Publish app**, then confirm. The status changes from **Testing** to
   **In production**.

Publishing sounds like a big step, but for an app only you use it just means Google stops expiring your sign-in
every 7 days. It does not make the app public or searchable, and Google does not review it. Other people can
only use it if you give them the client ID. Do this **before** you connect, so the first sign-in is already a
long-lived one.

**If you would rather not publish,** leave the app in **Testing** and add your address (and every other account
you want to connect) under **Test users**. It works the same, but Google expires the sign-in after about a week
and you will need to choose **Connect Google** again now and then. That is a Google rule for test apps, not a
Piyo problem. (Google's rules can change; if sign-in stops working, check the Audience page first.)

- [ ] Consent screen is created
- [ ] The app is **In production** (or my address is listed as a test user)

## Create the OAuth client

1. Open [the credentials page](https://console.cloud.google.com/apis/credentials).
2. Choose **Create credentials**, then **OAuth client ID**.
3. Application type: **Desktop app**. Name it **Piyo AI** and choose **Create**.
4. Copy the **Client ID**. If Google also shows a **Client secret**, copy that too.

Keep these private, like a password. Do not put them in a file or a message.

- [ ] I have the client ID (and secret, if shown)

## Connect Piyo

Paste the client ID below and choose **Save client**, then **Connect Google**. Your browser opens Google's
sign-in. Pick your account.

You can connect more than one Google account: once the first is connected, choose **Add another account**.
Each account has its own access, and Piyo asks which one to use when it matters.

Google will say **"Google hasn't verified this app"**. That is expected, because the app is your own and
Google has not reviewed it. Choose **Advanced**, then **Go to Piyo AI (unsafe)**, check the boxes for the access
you want, and continue. The page then says you are connected and you can close it. You will see this warning
each time you add an account or more access.

With the app in production you can add any Google account this way. If you stayed in Testing, an account that is
not on your Test users list is refused.

:::google:::

- [ ] Piyo shows my address as connected

## Test it

Choose **Test connection**. Piyo makes one small read-only request to Gmail and one to Calendar and tells you
what worked.

:::google-test:::

If a line says an API is not turned on, go back to "Turn on the two APIs". If it says access was not granted,
press **Connect Google** again and tick the boxes.

## Try it

Close this guide and ask Piyo:

- "What's in my inbox and what do I have today?"
- "Give me my morning brief." (it will ask which city you are in for the weather)

If you added draft, send or calendar access, Piyo shows you a preview and waits for your approval before
anything is sent or changed.
