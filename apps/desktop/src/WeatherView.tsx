/**
 * The `weather` response view (core/piyo/views/schema.py). It only ever draws numbers and short text the core
 * already checked; the icon comes from a closed list and is drawn here, so no image is fetched.
 */

export type Icon =
  | "clear"
  | "mostly-clear"
  | "partly-cloudy"
  | "cloudy"
  | "fog"
  | "drizzle"
  | "rain"
  | "heavy-rain"
  | "snow"
  | "thunder"
  | "unknown";

export interface WeatherData {
  place: string;
  timezone: string;
  units: { temp: string; wind: string; rain: string };
  now: { temp: number | null; feels_like: number | null; wind: number | null; sky: string; icon: Icon } | null;
  days: {
    date: string;
    sky: string;
    icon: Icon;
    high: number | null;
    low: number | null;
    rain: number | null;
    rain_chance: number | null;
    wind: number | null;
    sunrise: string | null;
    sunset: string | null;
  }[];
}

const ICONS: Icon[] = ["clear", "mostly-clear", "partly-cloudy", "cloudy", "fog", "drizzle", "rain", "heavy-rain", "snow", "thunder", "unknown"];

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const isNum = (v: unknown) => v === null || (typeof v === "number" && Number.isFinite(v));
const isText = (v: unknown): v is string => typeof v === "string";
const isIcon = (v: unknown): v is Icon => typeof v === "string" && (ICONS as string[]).includes(v);

/** The data if it has the shape the widget draws (a stored chat can hold an older shape), else null. */
export function parseWeather(raw: unknown): WeatherData | null {
  if (!isObj(raw) || !isText(raw.place) || !isObj(raw.units) || !Array.isArray(raw.days)) return null;
  const u = raw.units;
  if (!isText(u.temp) || !isText(u.wind) || !isText(u.rain)) return null;
  const now = raw.now;
  if (now !== null && now !== undefined) {
    if (!isObj(now) || !isIcon(now.icon) || !isText(now.sky) || !isNum(now.temp) || !isNum(now.feels_like) || !isNum(now.wind)) return null;
  }
  for (const d of raw.days) {
    if (!isObj(d) || !isText(d.date) || !isText(d.sky) || !isIcon(d.icon)) return null;
    if (![d.high, d.low, d.rain, d.rain_chance, d.wind].every(isNum)) return null;
  }
  return raw as unknown as WeatherData;
}

const num = (n: number | null | undefined) => (n === null || n === undefined ? "–" : String(Math.round(n)));

/** "Mon" for an ISO date, in the viewer's language; UTC so the day never shifts with the viewer's time zone. */
export function weekday(date: string, index: number): string {
  if (index === 0) return "Today";
  const t = Date.parse(`${date}T00:00:00Z`);
  return Number.isNaN(t) ? date : new Date(t).toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" });
}

/** Where a day's low..high sits inside the whole forecast's range, as percentages for the bar. */
export function rangeBar(low: number | null, high: number | null, min: number, max: number): { left: number; width: number } | null {
  if (low === null || high === null || max <= min) return null;
  const left = ((low - min) / (max - min)) * 100;
  const width = Math.max(((high - low) / (max - min)) * 100, 6);
  return { left: Math.min(left, 100 - width), width };
}

export function WeatherIcon({ icon, size = 28 }: { icon: Icon; size?: number }) {
  const cloud = <path className="wi-cloud" d="M8 21a5 5 0 0 1-.6-9.96A7 7 0 0 1 21 10.5a4.25 4.25 0 0 1-.5 8.5H8z" />;
  const sun = (cx: number, cy: number, r: number) => (
    <g className="wi-sun">
      <circle cx={cx} cy={cy} r={r} />
      {[0, 45, 90, 135, 180, 225, 270, 315].map((a) => (
        <line key={a} x1={cx} y1={cy - r - 2} x2={cx} y2={cy - r - 5} transform={`rotate(${a} ${cx} ${cy})`} />
      ))}
    </g>
  );
  let art;
  switch (icon) {
    case "clear":
      art = sun(16, 16, 6);
      break;
    case "mostly-clear":
      art = (
        <>
          {sun(13, 13, 5)}
          <g transform="translate(6 7) scale(.7)">{cloud}</g>
        </>
      );
      break;
    case "partly-cloudy":
      art = (
        <>
          {sun(12, 11, 4.5)}
          <g transform="translate(4 6) scale(.85)">{cloud}</g>
        </>
      );
      break;
    case "fog":
      art = (
        <g className="wi-line">
          <line x1="6" y1="12" x2="26" y2="12" />
          <line x1="4" y1="17" x2="24" y2="17" />
          <line x1="8" y1="22" x2="28" y2="22" />
        </g>
      );
      break;
    case "drizzle":
    case "rain":
    case "heavy-rain": {
      const drops = icon === "drizzle" ? [11, 19] : icon === "rain" ? [9, 16, 23] : [8, 13, 18, 23];
      art = (
        <>
          <g transform="translate(0 -4) scale(1)">{cloud}</g>
          <g className="wi-rain">
            {drops.map((x, i) => (
              <line key={x} x1={x} y1={22 + (i % 2) * 2} x2={x - 2} y2={26 + (i % 2) * 2} />
            ))}
          </g>
        </>
      );
      break;
    }
    case "snow":
      art = (
        <>
          <g transform="translate(0 -4)">{cloud}</g>
          <g className="wi-snow">
            {[10, 16, 22].map((x, i) => (
              <circle key={x} cx={x} cy={24 + (i % 2) * 2.5} r="1.4" />
            ))}
          </g>
        </>
      );
      break;
    case "thunder":
      art = (
        <>
          <g transform="translate(0 -4)">{cloud}</g>
          <path className="wi-bolt" d="M17 18l-4 6h3l-1.5 5 5.5-7.5h-3.2L19 18z" />
        </>
      );
      break;
    case "cloudy":
    default:
      art = <g transform="translate(0 2)">{cloud}</g>;
  }
  return (
    <svg className={`wi wi-${icon}`} width={size} height={size} viewBox="0 0 32 32" role="img" aria-label={icon.replace("-", " ")}>
      {art}
    </svg>
  );
}

export function WeatherView({ data }: { data: WeatherData }) {
  const { units, now, days } = data;
  const deg = "°";
  const lows = days.map((d) => d.low).filter((n): n is number => n !== null);
  const highs = days.map((d) => d.high).filter((n): n is number => n !== null);
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const today = days[0];
  const stats = today
    ? [
        today.rain !== null && `Rain ${today.rain} ${units.rain}${today.rain_chance !== null ? ` (${today.rain_chance}%)` : ""}`,
        today.wind !== null && `Wind up to ${num(today.wind)} ${units.wind}`,
        today.sunrise && `Sunrise ${today.sunrise}`,
        today.sunset && `Sunset ${today.sunset}`,
      ].filter(Boolean)
    : [];

  return (
    <section className="view weather" aria-label={`Weather for ${data.place}`}>
      <div className="w-place">{data.place}</div>
      {now && (
        <div className="w-now">
          <WeatherIcon icon={now.icon} size={56} />
          <div>
            <div className="w-temp">
              {num(now.temp)}
              <span>{units.temp}</span>
            </div>
            <div className="w-sky">{now.sky}</div>
          </div>
          <div className="w-meta">
            {now.feels_like !== null && <div>Feels like {num(now.feels_like)}{deg}</div>}
            {now.wind !== null && (
              <div>
                Wind {num(now.wind)} {units.wind}
              </div>
            )}
          </div>
        </div>
      )}
      {days.length > 1 && (
        <ul className="w-days">
          {days.map((d, i) => {
            const bar = rangeBar(d.low, d.high, min, max);
            return (
              <li key={d.date}>
                <span className="w-day">{weekday(d.date, i)}</span>
                <WeatherIcon icon={d.icon} size={24} />
                <span className="w-chance" title="Chance of rain">
                  {d.rain_chance !== null && d.rain_chance >= 20 ? `${d.rain_chance}%` : ""}
                </span>
                <span className="w-lo">{num(d.low)}{deg}</span>
                <span className="w-bar" aria-hidden="true">{bar && <i style={{ left: `${bar.left}%`, width: `${bar.width}%` }} />}</span>
                <span className="w-hi">{num(d.high)}{deg}</span>
              </li>
            );
          })}
        </ul>
      )}
      {stats.length > 0 && <div className="w-stats">{stats.join(" · ")}</div>}
    </section>
  );
}
