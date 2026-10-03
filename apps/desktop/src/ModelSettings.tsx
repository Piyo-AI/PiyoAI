import { useCallback, useEffect, useRef, useState } from "react";

import { api, ContextLimit, ModelInfo, OutputLimit, Price, ToolMode, Vision } from "./api";

interface Props {
  providerId: string;
  model: string;
  /** The loaded model list: when it changes the core may have learned new limits. */
  models: ModelInfo[];
  /** A finished run can teach the core that a model has no tool calling. */
  busy: boolean;
}

/** Fetches one per-model setting for the selected model and refreshes it when `deps` change. */
function useModelSetting<T>(load: (provider: string, model: string) => Promise<T>, providerId: string, model: string, deps: unknown[]) {
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    if (!providerId || !model.trim()) {
      setValue(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      load(providerId, model.trim())
        .then((v) => !cancelled && setValue(v))
        .catch(() => !cancelled && setValue(null));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId, model, ...deps]);
  return [value, setValue] as const;
}

/** A number setting with an automatic value the user can override and reset. */
function NumberField({
  label,
  help,
  value,
  custom,
  min,
  step,
  error,
  onSave,
  onReset,
}: {
  label: string;
  help: string;
  value: number;
  custom: boolean;
  min: number;
  step: number;
  error: string;
  onSave: (n: number) => void;
  onReset: () => void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const n = Number(text);
    if (!Number.isInteger(n) || n <= 0) return setText(String(value));
    if (n !== value) onSave(n);
  };
  return (
    <div className="field">
      <label>
        <span className="field-label">{label}</span>
        <input
          type="number"
          min={min}
          step={step}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
          aria-invalid={!!error}
        />
        {custom && (
          <button type="button" className="ghost" onClick={onReset} title="Back to automatic">
            ↺ Automatic
          </button>
        )}
      </label>
      <p className="hint">{help}</p>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/** Dollars per million tokens, used for the cost shown on each run. Local models are always free. */
function PriceField({ price, error, onSave }: { price: Price; error: string; onSave: (i: number | null, o: number | null) => void }) {
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  useEffect(() => {
    setInput(price.input == null ? "" : String(price.input));
    setOutput(price.output == null ? "" : String(price.output));
  }, [price.input, price.output]);
  const changed = input !== (price.input == null ? "" : String(price.input)) || output !== (price.output == null ? "" : String(price.output));
  const valid = input.trim() !== "" && output.trim() !== "" && Number(input) >= 0 && Number(output) >= 0;
  const source = {
    custom: "Set by you.",
    reported: "Reported by the provider.",
    local: "",
    unknown: "Unknown, so runs show no cost. Enter the prices from your provider's price list.",
  }[price.source];
  return (
    <div className="field">
      <span className="field-label">Price</span>
      {price.source === "local" ? (
        <p className="hint">Runs on this computer, so it costs nothing.</p>
      ) : (
        <>
          <div className="inline">
            <input type="number" min={0} step="any" placeholder="Input $/1M" value={input} onChange={(e) => setInput(e.target.value)} aria-label="Input price per million tokens" />
            <input type="number" min={0} step="any" placeholder="Output $/1M" value={output} onChange={(e) => setOutput(e.target.value)} aria-label="Output price per million tokens" />
            <button type="button" disabled={!changed || !valid} onClick={() => onSave(Number(input), Number(output))}>
              Save
            </button>
            {price.source === "custom" && (
              <button type="button" className="ghost" onClick={() => onSave(null, null)} title="Back to automatic">
                ↺ Automatic
              </button>
            )}
          </div>
          <p className="hint">US dollars per million tokens. {source}</p>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

/** One button in the top bar; the per-model settings live in the panel it opens. */
export function ModelSettings({ providerId, model, models, busy }: Props) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const name = model.trim();

  const [limit, setLimit] = useModelSetting<OutputLimit>(api.outputLimit, providerId, model, [models]);
  const [ctx, setCtx] = useModelSetting<ContextLimit>(api.contextLimit, providerId, model, [models]);
  const [tools, setTools] = useModelSetting<ToolMode>(api.toolMode, providerId, model, [models, busy]);
  const [price, setPrice] = useModelSetting<Price>(api.price, providerId, model, [models]);
  const [vision, setVision] = useModelSetting<Vision>(api.vision, providerId, model, [models]);
  const [errors, setErrors] = useState({ limit: "", ctx: "", tools: "", price: "", vision: "" });
  const fail = (key: keyof typeof errors, e: unknown) => setErrors((p) => ({ ...p, [key]: (e as Error).message }));
  const clear = (key: keyof typeof errors) => setErrors((p) => ({ ...p, [key]: "" }));

  useEffect(() => setErrors({ limit: "", ctx: "", tools: "", price: "", vision: "" }), [providerId, model]);

  // Close on a click outside or Esc.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const saveLimit = useCallback(
    async (tokens: number | null) => {
      try {
        setLimit(await api.setOutputLimit(providerId, name, tokens));
        clear("limit");
      } catch (e) {
        fail("limit", e);
      }
    },
    [providerId, name, setLimit],
  );
  const saveCtx = useCallback(
    async (tokens: number | null) => {
      try {
        setCtx(await api.setContextLimit(providerId, name, tokens));
        clear("ctx");
      } catch (e) {
        fail("ctx", e);
      }
    },
    [providerId, name, setCtx],
  );
  const savePrice = async (input: number | null, output: number | null) => {
    try {
      setPrice(await api.setPrice(providerId, name, input, output));
      clear("price");
    } catch (e) {
      fail("price", e);
    }
  };
  const saveVision = async (mode: Vision["mode"]) => {
    try {
      setVision(await api.setVision(providerId, name, mode));
      clear("vision");
    } catch (e) {
      fail("vision", e);
    }
  };
  const saveTools = async (mode: ToolMode["mode"]) => {
    try {
      setTools(await api.setToolMode(providerId, name, mode));
      clear("tools");
    } catch (e) {
      fail("tools", e);
    }
  };

  const available = !!name && (limit || ctx || tools);

  return (
    <div className="model-settings" ref={root}>
      <button
        type="button"
        className="ghost"
        onClick={() => setOpen((o) => !o)}
        disabled={!available}
        aria-expanded={open}
        title={available ? "Reply length, context size and tool calling for this model" : "Pick a model first"}
      >
        Model settings
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="Model settings">
          <h3>{name}</h3>
          <p className="hint">These apply to this model only.</p>
          {limit && (
            <NumberField
              label="Max reply"
              help={`Longest reply Piyo will ask for, in tokens. ${
                limit.reported_max ? `This model's maximum is ${limit.reported_max}.` : "The model's maximum is unknown."
              } ${limit.custom ? "Set by you." : "Automatic."}`}
              value={limit.output_limit}
              custom={limit.custom}
              min={256}
              step={256}
              error={errors.limit}
              onSave={(n) => void saveLimit(n)}
              onReset={() => void saveLimit(null)}
            />
          )}
          {ctx && (
            <NumberField
              label="Context"
              help={`How much conversation Piyo sends the model, in tokens; older tool output and turns are dropped to fit. ${
                ctx.reported
                  ? `This model reports ${ctx.reported}.`
                  : "The model's window is unknown, so 32000 is assumed. Lower it for a small local model."
              } ${ctx.custom ? "Set by you." : "Automatic."}`}
              value={ctx.context_limit}
              custom={ctx.custom}
              min={1024}
              step={1024}
              error={errors.ctx}
              onSave={(n) => void saveCtx(n)}
              onReset={() => void saveCtx(null)}
            />
          )}
          {vision && (
            <div className="field">
              <label>
                <span className="field-label">Images</span>
                <select value={vision.mode} onChange={(e) => void saveVision(e.target.value as Vision["mode"])}>
                  <option value="auto">Automatic</option>
                  <option value="yes">Can read images</option>
                  <option value="no">Can't read images</option>
                </select>
              </label>
              <p className="hint">
                Skills that need to see images only run on a model that can. Automatic uses what the provider
                reports ({vision.reported == null ? "nothing for this model" : vision.reported ? "it can" : "it can't"}),
                and when that is unknown no skill is blocked. Choose "Can read images" for a vision model the
                provider doesn't describe.
              </p>
              {errors.vision && <p className="error">{errors.vision}</p>}
            </div>
          )}
          {price && <PriceField price={price} error={errors.price} onSave={(i, o) => void savePrice(i, o)} />}
          {tools && (
            <div className="field">
              <label>
                <span className="field-label">Tools</span>
                <select value={tools.mode} onChange={(e) => void saveTools(e.target.value as ToolMode["mode"])}>
                  <option value="auto">Auto</option>
                  <option value="native">Native tool calling</option>
                  <option value="prompt">Text instructions</option>
                </select>
              </label>
              <p className="hint">
                How Piyo asks the model to use tools. Auto uses the model's own tool calling and switches to text
                instructions if the model has none. Now using{" "}
                {tools.effective === "prompt" ? "text instructions" : "the model's own tool calling"}. {tools.reason}
              </p>
              {errors.tools && <p className="error">{errors.tools}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
