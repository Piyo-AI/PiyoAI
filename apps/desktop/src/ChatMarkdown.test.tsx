import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChatMarkdown, parseChat } from "./ChatMarkdown";

const html = (text: string) => renderToStaticMarkup(<ChatMarkdown text={text} />);

describe("ChatMarkdown formatting", () => {
  it("renders headings, emphasis, code and links", () => {
    const out = html("# Title\n\nSome **bold**, *italic*, ~~gone~~ and `code` with [a link](https://example.com/x).");
    expect(out).toContain("<h3>Title</h3>");
    expect(out).toContain("<strong>bold</strong>");
    expect(out).toContain("<em>italic</em>");
    expect(out).toContain("<del>gone</del>");
    expect(out).toContain("<code>code</code>");
    expect(out).toContain('<a href="https://example.com/x" target="_blank" rel="noreferrer">a link</a>');
  });

  it("keeps single line breaks inside a paragraph", () => {
    expect(html("one\ntwo")).toContain("<p>one<br/>two</p>");
  });

  it("renders nested and loose lists", () => {
    const out = html("1. first\n\n2. second\n   - inner a\n   - inner b\n3. third");
    expect(out).toContain("<ol>");
    expect(out).toContain("<li>second<ul><li>inner a</li><li>inner b</li></ul></li>");
    expect(out.match(/<li>/g)).toHaveLength(5);
  });

  it("renders tables with alignment and escaped bars", () => {
    const out = html("| Day | Temp |\n|:---|---:|\n| Mon | 20 \\| 22 |\n| Tue | 18 |");
    expect(out).toContain('<th style="text-align:left">Day</th>');
    expect(out).toContain('<td style="text-align:right">20 | 22</td>');
    expect(out.match(/<tr>/g)).toHaveLength(3);
  });

  it("renders block quotes and rules", () => {
    const out = html("> quoted *text*\n\n---\n\nafter");
    expect(out).toContain("<blockquote><p>quoted <em>text</em></p></blockquote>");
    expect(out).toContain("<hr/>");
  });

  it("links bare URLs without their trailing punctuation", () => {
    const out = html("See https://example.com/page.");
    expect(out).toContain('href="https://example.com/page"');
    expect(out).toContain("</a>.</p>");
  });

  it("does not mangle snake_case or math with asterisks", () => {
    const out = html("use my_var_name and 2 * 3 * 4");
    expect(out).toContain("my_var_name");
    expect(out).not.toContain("<em>");
  });
});

describe("ChatMarkdown streaming", () => {
  it("treats an unclosed fence as code", () => {
    const out = html("Here:\n```python\nprint('hi')\nx = 1");
    expect(out).toContain("<pre");
    expect(out).toContain("print(&#x27;hi&#x27;)\nx = 1");
  });

  it("shows a half-written table as text, not a broken table", () => {
    const out = html("| a | b |");
    expect(out).not.toContain("<table");
    expect(out).toContain("| a | b |");
  });

  it("copes with unbalanced markers and empty input", () => {
    expect(() => html("**oops *and `more [x](https://")).not.toThrow();
    expect(html("")).toBe('<div class="md"></div>');
  });
});

describe("ChatMarkdown safety", () => {
  it("never produces markup from raw HTML", () => {
    const out = html('<script>alert(1)</script> <img src="https://evil.test/p.gif" onerror="x()"> <b>hi</b>');
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<b>");
    expect(out).toContain("&lt;script&gt;");
  });

  it("makes links only for http(s) targets", () => {
    for (const bad of ["javascript:alert(1)", "data:text/html,<b>x</b>", "file:///etc/passwd", "//evil.test", "vbscript:x"]) {
      const out = html(`[click](${bad})`);
      expect(out).not.toContain("<a ");
      expect(out).not.toContain("href=");
    }
  });

  it("does not render images, so nothing is fetched", () => {
    const out = html("![pixel](https://evil.test/p.gif?data=secret)");
    expect(out).not.toContain("<img");
    expect(out).toContain('href="https://evil.test/p.gif?data=secret"'); // shown as an ordinary, visible link
  });

  it("escapes text inside every element", () => {
    const evil = '"><svg onload=alert(1)>';
    const out = html(`# ${evil}\n\n- ${evil}\n\n> ${evil}\n\n| ${evil} |\n|---|\n| ${evil} |\n\n[${evil}](https://a.test/?q=${evil})`);
    expect(out).not.toContain("<svg");
    expect(out).toContain("&lt;svg onload=alert(1)&gt;"); // inert text, not an attribute
  });

  it("survives pathological nesting without hanging", () => {
    const text = "> ".repeat(500) + "deep\n" + "*a ".repeat(2000) + "\n" + "[".repeat(2000);
    const start = Date.now();
    expect(() => html(text)).not.toThrow();
    expect(Date.now() - start).toBeLessThan(2000);
  });

  it("parses to plain data only", () => {
    const blocks = parseChat("- a\n- b");
    expect(blocks).toEqual([
      {
        type: "list",
        ordered: false,
        start: 1,
        items: [
          { lines: ["a"], children: [] },
          { lines: ["b"], children: [] },
        ],
      },
    ]);
  });
});
