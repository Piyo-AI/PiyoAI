# Troubleshooting

Find the message you see, or the thing that is not working.

## Piyo will not start or says it cannot reach the core

The window shows **Can't reach the Piyo core** with a reason and a **Restart** button.

- Press **Restart**. If it works, nothing more is needed.
- If the reason mentions a missing file, the installation is damaged: reinstall Piyo from the releases page.
- Security software that blocks programs from opening local network connections can stop the core. Piyo only
  listens on `127.0.0.1`, the computer itself. Allow it in that software.
- A new version that will not start twice in a row offers to go back to the old one on the third try. Accept it,
  then report the problem on the issues page.

## "Rejected the API key", "doesn't know that model", "rate limiting"

- **Rejected the API key:** the key is wrong, expired or for a different provider. Paste it again in
  Settings > Providers.
- **Doesn't know that model:** the model name is misspelled or your account cannot use it. Pick one from the list.
- **Rate limiting:** you are sending requests faster than your plan allows. Wait a minute. Free models on
  OpenRouter have very low limits.
- **Couldn't connect to Ollama:** Ollama is not running. Start it, or choose another provider.

## Piyo answers but does not use its tools, or does it badly

Small local models are often weak at tools. Try a larger one. In the model settings (the button next to the model
name) you can set **Tool calling** to the text mode, which suits some models better. If a skill says the model
cannot run it (for example it needs images), choose a model that can.

## "Local-only mode is on"

Settings > Limits > **Only use models that run on this computer** is on and you picked an online provider. Pick a
local model, or turn the setting off.

## A request stopped part-way

Messages like "reached the step limit", "used up its token budget" or a timeout mean the limits in
Settings > Limits were reached. Raise them if the task is long. "The reply was cut off by the reply-length limit"
means raise **Max reply** in the model settings.

## Piyo keeps asking for approval

That is by design for anything that changes or sends something. For files, Settings > Folders has a per-folder
**no prompts for changes** switch that lets Piyo create folders and add new files in that folder without asking.
Deleting and replacing files always ask.

## Files: "not an approved folder"

Add the folder under Settings > Folders. Piyo refuses a whole drive, your home folder and system folders; add a
specific folder inside. Keys, `.env` files and browser profiles are off limits even inside approved folders.

## The browser

- **"The browser is not installed yet":** press **Install browser** in the banner. It is a one-time download of
  about 150 MB and needs internet access. If it fails, the banner says why; press **Try again**.
- **A page will not open:** Settings > Browser may block it (an allowed-sites list that does not include it, or a
  blocked site). Some sites refuse automated browsers; Piyo does not try to get around that.
- Piyo does not sign in, solve CAPTCHAs or enter passwords. If a page needs one, it tells you.

## Web search says there is no API key

Add a Brave Search key under Settings > Web search. The web-research skill's Setup guide has the steps.

## Google (Gmail, Calendar)

- Follow Gmail triage > Setup; it tests the connection at the end.
- **Access not granted:** you connected without ticking the access the action needs. Disconnect and connect again
  with the right boxes ticked.
- **Google says the app is not verified, or blocks the account:** you are using your own Google project, so this is
  expected. The Gmail triage setup guide explains the two ways round it (publish the app for yourself, or stay in
  testing and add every account as a test user).

## Skills

- **A skill does not appear or "did not load":** Settings > Skills lists skills that failed with the reason, for
  example a mistake in its `SKILL.md`.
- **"Needs Google" or "needs a key":** open the skill's **Setup**.
- **The catalog will not load, or says its signature did not verify:** the catalog is being updated, or the file was
  changed after it was signed. Try again later. Piyo refuses an unsigned catalog on purpose.
- **A skill was switched off by Piyo:** the catalog withdrew that version. The skill's row shows why. You can
  switch it back on or uninstall it.
- **A script fails:** Python scripts need `uv` and TypeScript ones need Deno; both come with the installed app. If
  you run Piyo from source, install them.

## Updates

- **The update will not install:** check your internet connection, then Settings > Updates > check again. Every
  update is verified before it is installed; one that fails verification is refused.
- **Piyo went back to an older version:** the newer one did not start properly. Settings > Updates warns before
  offering that version again.

## Still stuck

Open an issue at <https://github.com/Piyo-AI/PiyoAI/issues>. Say your system, Piyo's version (Settings >
Updates), what you did and the message you saw. Do not paste API keys. Tasks shows the run's steps with secrets
masked, which is safe to copy from. For a security problem, read [SECURITY.md](../../SECURITY.md) first.
