import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ViewEnvelope } from "./api";
import { toUiMessages } from "./useChat";
import { drawView, ViewRenderer } from "./Views";
import { parseWeather, rangeBar, weekday } from "./WeatherView";

const day = (over = {}) => ({
  date: "2026-10-05", sky: "rain", icon: "rain", high: 15.2, low: 9.1, rain: 4.3, rain_chance: 80, wind: 22.4,
  sunrise: "07:36", sunset: "18:55", ...over,
}); // prettier-ignore
const data = (over = {}) => ({
  place: "Paris, France",
  timezone: "Europe/Paris",
  units: { temp: "°C", wind: "km/h", rain: "mm" },
  now: { temp: 13.6, feels_like: 11.9, wind: 14.2, sky: "light rain", icon: "rain" },
  days: [day(), day({ date: "2026-10-06", sky: "overcast", icon: "cloudy", high: 17, low: 8, rain_chance: 10 })],
  ...over,
});
const view = (over: Partial<ViewEnvelope> = {}): ViewEnvelope => ({ type: "weather", v: 1, data: data(), ...over });
const html = (v: ViewEnvelope, text = "Weather for Paris: rain.") => renderToStaticMarkup(<ViewRenderer view={v} text={text} />);

describe("weather view", () => {
  it("draws the place, now, the days and the stats", () => {
    const out = html(view());
    expect(out).toContain("Paris, France");
    expect(out).toContain("14"); // 13.6 rounded
    expect(out).toContain("light rain");
    expect(out).toContain("Today");
    expect(out).toContain("Rain 4.3 mm (80%)");
    expect(out).toContain("Sunrise 07:36");
    expect(out.match(/<li>/g)).toHaveLength(2);
  });

  it("shows no day list for a single day, and copes with missing numbers", () => {
    const one = data({ days: [day({ high: null, low: null, rain: null, rain_chance: null, wind: null })], now: null });
    const out = html(view({ data: one }));
    expect(out).not.toContain("<li>");
    expect(out).not.toContain("NaN");
  });

  it("draws only a bundled icon: no image elements and no URLs", () => {
    const out = html(view());
    expect(out).toContain("<svg");
    expect(out).not.toContain("<img");
    expect(out).not.toMatch(/https?:/);
  });

  it("renders hostile text as inert text", () => {
    const evil = '<img src=x onerror=alert(1)>';
    const out = html(view({ data: data({ place: evil, now: { temp: 1, feels_like: 1, wind: 1, sky: evil, icon: "rain" } }) }));
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img src=x onerror=alert(1)&gt;");
  });
});

describe("falling back to text", () => {
  it("uses the tool's text for an unknown type", () => {
    const out = html(view({ type: "carousel" }), "plain answer");
    expect(out).toContain("plain answer");
    expect(out).not.toContain("<svg");
  });

  it("prefers the view's own fallback", () => {
    expect(html(view({ type: "carousel", fallback: "from the view" }), "tool text")).toContain("from the view");
  });

  it("does not draw a version newer than the app knows", () => {
    expect(drawView(view({ v: 2 }))).toBeNull();
    expect(drawView(view({ v: 0 }))).toBeNull();
    expect(html(view({ v: 2 }), "text instead")).toContain("text instead");
  });

  it("does not draw data of the wrong shape, including an older stored shape", () => {
    for (const bad of [null, "x", {}, data({ days: "x" }), data({ units: null }), data({ days: [day({ icon: "https://x/y.gif" })] }), data({ now: { sky: 1 } })]) {
      expect(parseWeather(bad)).toBeNull();
      expect(html(view({ data: bad }), "fallback text")).toContain("fallback text");
    }
  });

  it("draws nothing when there is neither a drawing nor any text", () => {
    expect(html(view({ type: "carousel" }), "")).toBe("");
  });
});

describe("helpers", () => {
  it("names days without shifting them by the viewer's time zone", () => {
    expect(weekday("2026-10-05", 0)).toBe("Today");
    expect(weekday("2026-10-05", 1)).toBe(new Date(Date.UTC(2026, 9, 5)).toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" }));
    expect(weekday("garbage", 1)).toBe("garbage");
  });

  it("places a day's range inside the whole forecast", () => {
    expect(rangeBar(0, 10, 0, 20)).toEqual({ left: 0, width: 50 });
    expect(rangeBar(20, 20, 0, 20)).toEqual({ left: 94, width: 6 }); // never thinner than a dot, never past the end
    expect(rangeBar(null, 10, 0, 20)).toBeNull();
    expect(rangeBar(5, 5, 5, 5)).toBeNull();
  });
});

describe("reloading a chat", () => {
  it("puts a stored tool message's view back on its call", () => {
    const stored = [
      { role: "user", content: "weather?", tool_calls: [], tool_call_id: null, is_error: false },
      { role: "assistant", content: "", tool_calls: [{ id: "c1", name: "weather__forecast", arguments: {} }], tool_call_id: null, is_error: false },
      { role: "tool", content: "Weather for Paris", tool_calls: [], tool_call_id: "c1", is_error: false, view: view() },
    ] as Parameters<typeof toUiMessages>[0];
    const reply = toUiMessages(stored)[1];
    expect(reply.tools?.[0].view?.type).toBe("weather");
    expect(reply.tools?.[0].output).toBe("Weather for Paris");
  });
});

describe("generic views", () => {
  const link = { text: "Track", url: "https://carrier.example/t/1" };
  const show = (type: string, data: unknown) => html({ type, v: 1, data }, "text fallback");

  it("draws a table with numbers aligned and links showing their host", () => {
    const out = show("table", { title: "Prices", columns: ["Shop", "Price"], rows: [["A", 10], ["B", link], ["C", null]] });
    expect(out).toContain("Prices");
    expect(out).toContain('<td class="num">10</td>');
    expect(out).toContain('<a href="https://carrier.example/t/1" target="_blank" rel="noreferrer">Track</a>');
    expect(out).toContain("carrier.example</span>");
    expect(out.match(/<tr>/g)).toHaveLength(4);
  });

  it("draws key-value, timeline, list and message-list", () => {
    expect(show("key-value", { items: [{ label: "Status", value: "In transit" }] })).toContain("<dt>Status</dt><dd>In transit</dd>");
    const timeline = show("timeline", { items: [{ when: "09:00", title: "Standup", detail: "Room 2", tone: "warn" }, { title: "Lunch" }] });
    expect(timeline).toContain("tone-warn");
    expect(timeline).toContain("Room 2");
    expect(timeline).toContain("tone-neutral");
    const list = show("list", { ordered: true, items: [{ text: "Milk", checked: true }, { text: "Eggs", checked: false }] });
    expect(list).toContain("<ol");
    expect(list).toContain("☑");
    expect(list).toContain("☐");
    const mail = show("message-list", { items: [{ sender: "Ana", subject: "Lunch?", account: "me@x.com", action: "reply" }] });
    expect(mail).toContain("Ana");
    expect(mail).toContain("Needs a reply");
    expect(mail).toContain("me@x.com");
  });

  it("is display only: no inputs, buttons or images in any generic view", () => {
    const all = [
      show("table", { columns: ["A"], rows: [["x"]] }),
      show("list", { items: [{ text: "x", checked: true }] }),
      show("message-list", { items: [{ sender: "a", action: "reply" }] }),
      show("timeline", { items: [{ title: "x", link }] }),
    ].join("");
    expect(all).not.toMatch(/<(input|button|img|form|textarea|select)/);
  });

  it("draws a link only for http(s); anything else is plain text", () => {
    for (const url of ["javascript:alert(1)", "data:text/html,<b>x</b>", "file:///etc/passwd", "ftp://x/y", "not a url"]) {
      const out = show("key-value", { items: [{ label: "L", value: { text: "click me", url } }] });
      expect(out).toContain("click me");
      expect(out).not.toContain("<a ");
      expect(out).not.toContain("href=");
    }
  });

  it("draws hostile text as text in every view", () => {
    const evil = '<img src=x onerror="alert(1)"><script>x()</script>';
    const out = [
      show("table", { title: evil, columns: [evil], rows: [[evil]] }),
      show("key-value", { items: [{ label: evil, value: evil }] }),
      show("timeline", { items: [{ when: evil, title: evil, detail: evil }] }),
      show("list", { items: [{ text: evil }] }),
      show("message-list", { items: [{ sender: evil, subject: evil, snippet: evil, account: evil }] }),
    ].join("");
    expect(out).not.toMatch(/<(img|script)/);
    expect(out).toContain("&lt;img src=x");
  });

  it("falls back to the text for data of the wrong shape", () => {
    const wrong: [string, unknown][] = [
      ["table", { columns: ["A", "B"], rows: [["only one"]] }],
      ["table", { columns: ["A"], rows: "x" }],
      ["key-value", { items: [{ label: "L", value: { text: 1 } }] }],
      ["timeline", { items: [{ title: 5 }] }],
      ["list", { items: [{ text: "x", checked: "yes" }] }],
      ["message-list", { items: [{ subject: "no sender" }] }],
      ["list", null],
    ];
    for (const [type, data] of wrong) expect(show(type, data)).toContain("text fallback");
  });
});
