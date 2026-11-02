import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AssistantMarkdown } from "./AssistantMarkdown";

describe("AssistantMarkdown", () => {
  it("interpreta br em celula de tabela markdown", () => {
    const markdown = `| a | b |
| --- | --- |
| x | linha1<br>linha2 |`;

    const { container } = render(<AssistantMarkdown content={markdown} />);

    expect(container.querySelector("table")).toBeTruthy();
    expect(container.querySelector("br")).toBeTruthy();
    expect(container.textContent).toContain("linha1");
    expect(container.textContent).toContain("linha2");
    expect(container.innerHTML).not.toContain("&lt;br");
  });

  it("renderiza bloco table html", () => {
    const markdown = `<table><tr><td>celula</td></tr></table>`;

    const { container } = render(<AssistantMarkdown content={markdown} />);

    const cell = container.querySelector("table td");
    expect(cell).toBeTruthy();
    expect(cell?.textContent).toBe("celula");
  });

  it("remove script e atributos perigosos", () => {
    const markdown = `<script>alert(1)</script><p>ok</p><img src="x" onerror="alert(1)" alt="">`;

    const { container } = render(<AssistantMarkdown content={markdown} />);

    expect(container.querySelector("script")).toBeNull();
    const img = container.querySelector("img");
    expect(img?.getAttribute("onerror")).toBeNull();
    expect(container.textContent).toContain("ok");
  });
});
