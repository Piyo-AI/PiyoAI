# Models and providers

Piyo does not include a model. It uses one you choose: a service you pay for or try for free, or a model running on
your own computer. You can switch at any time from the picker at the top of the chat.

## Choosing

| | Online provider | On your computer (Ollama) |
|---|---|---|
| Quality | Best models available | Smaller models; depends on your hardware |
| Privacy | Your messages and the files and pages Piyo reads for a task are sent to the provider | Stays on your computer |
| Cost | Pay per use (some have free tiers) | Free after the download |
| Needs | An API key and internet | A reasonably capable computer; about 2 GB for the suggested model |

Piyo needs a model that can use tools. Larger and newer models do this well; small local ones can be slower and
less reliable.

## An online provider

Piyo comes with Anthropic, OpenAI, OpenRouter, DeepSeek, Kimi (Moonshot AI) and Google Gemini.

1. Create an API key on the provider's website. Settings > Providers links to the right page for each.
2. Open **Settings > Providers**, paste the key next to the provider and save. The key goes into your operating
   system's keychain. Piyo never shows it again or writes it to a file.
3. Back in the chat, choose the provider and a model.

**OpenRouter** is a good way to try models for free: choose it, tick **Free only**, and pick a model labelled
**tools**. Free models have strict request limits; a "rate limiting" message usually means you reached them.

A key you paste is checked by loading the provider's model list. If that fails, the message says why (wrong key,
no connection).

## A model on your computer

1. Install [Ollama](https://ollama.com/download) and start it. Piyo does not install it for you.
2. In the setup (or Settings > Run setup again) choose **On this computer**. Piyo detects Ollama and, with your
   permission, downloads the suggested model (`llama3.2:3b`, about 2 GB) through Ollama itself, showing progress.
   You can type any other model name instead.
3. Choose **Ollama (local)** and the model in the chat.

LM Studio is also listed; start its local server and choose it. Any other local server that speaks the OpenAI or
Anthropic API works through **Add a provider**.

## Any other service

Settings > Providers > **Add provider**: give it a name, the base URL (for example `https://api.together.xyz/v1`),
and which API style it speaks (OpenAI-compatible or Anthropic-compatible), plus a key if it needs one.

## Per-model settings

The button next to the model name opens the model's settings:

- **Reply length**: the most Piyo asks the model to write in one turn.
- **Context size**: how much conversation fits. Piyo trims old tool output first when a chat gets long.
- **Tool calling**: Automatic, or force native tool calling, or ask the model for tools as text. If a model does
  not support tools natively, Piyo falls back to text by itself. Automatic is right for most models.

## Use local models only

Settings > Limits > **Only use models that run on this computer** refuses every online provider, so nothing you
type and nothing Piyo reads can leave the machine through a model.

## Limits and cost

Settings > Limits sets how many steps, tokens and minutes one request may use. A request stops cleanly at any of
them, and time spent waiting for your approval does not count. Tasks shows what each run cost when the price is
known (online providers report it for some models; local models are free).
