import { Component, ReactNode } from "react";

import { ViewEnvelope } from "./api";
import {
  KeyValueView,
  ListView,
  MessageListView,
  parseKeyValue,
  parseList,
  parseMessageList,
  parseTable,
  parseTimeline,
  TableView,
  TimelineView,
} from "./GenericViews";
import { parseWeather, WeatherView } from "./WeatherView";

/**
 * Response views (core/piyo/views): a tool's result drawn as a widget. The core has already checked the data; this
 * is the closed list of things the app can draw, and anything else (an unknown type, a newer version than this
 * build knows, data of an older shape, a crash while drawing) shows the plain text instead. A view is display only:
 * no controls, and nothing here may look like an approval card (`ApprovalCard` stays in Tools.tsx).
 */

/** type -> newest version drawn here, and how to draw it (null when the data does not have the expected shape). */
function drawing<T>(parse: (data: unknown) => T | null, View: (props: { data: T }) => ReactNode) {
  return {
    v: 1,
    draw: (data: unknown) => {
      const parsed = parse(data);
      return parsed ? <View data={parsed} /> : null;
    },
  };
}

const KNOWN: Record<string, { v: number; draw: (data: unknown) => ReactNode | null }> = {
  weather: drawing(parseWeather, WeatherView),
  table: drawing(parseTable, TableView),
  "key-value": drawing(parseKeyValue, KeyValueView),
  timeline: drawing(parseTimeline, TimelineView),
  list: drawing(parseList, ListView),
  "message-list": drawing(parseMessageList, MessageListView),
};

/** What would be drawn for this view, or null when only the text can be shown. */
export function drawView(view: ViewEnvelope): ReactNode | null {
  const known = KNOWN[view.type];
  if (!known || !Number.isInteger(view.v) || view.v < 1 || view.v > known.v) return null;
  return known.draw(view.data);
}

function Text({ text }: { text: string }) {
  return (
    <section className="view view-text">
      <pre>{text}</pre>
    </section>
  );
}

class Guard extends Component<{ text: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? <Text text={this.props.text} /> : this.props.children;
  }
}

/** `text` is the tool's own output, shown when the view cannot be drawn. */
export function ViewRenderer({ view, text }: { view: ViewEnvelope; text: string }) {
  const shown = drawView(view);
  const fallback = view.fallback || text;
  if (shown === null) return fallback ? <Text text={fallback} /> : null;
  return <Guard text={fallback}>{shown}</Guard>;
}
