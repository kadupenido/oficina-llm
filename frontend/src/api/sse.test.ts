import { describe, expect, it } from "vitest";
import { pushSseChunk } from "./sse";

describe("pushSseChunk", () => {
  it("junta um frame partido entre chunks e ignora o ping", () => {
    const first = pushSseChunk("", 'event: token\ndata: {"del');
    expect(first.events).toEqual([]);
    const second = pushSseChunk(first.buffer, 'ta":"Oi"}\n\n: ping\n\n');
    expect(second.events).toEqual([{ event: "token", data: { delta: "Oi" } }]);
    expect(second.buffer).toBe("");
  });

  it("parseia evento think", () => {
    const parsed = pushSseChunk("", 'event: think\ndata: {"delta":"hm"}\n\n');
    expect(parsed.events).toEqual([{ event: "think", data: { delta: "hm" } }]);
  });

  it("parseia eventos de busca na web", () => {
    const start = pushSseChunk(
      "",
      'event: tool_start\ndata: {"name":"web_search","query":"clima"}\n\n',
    );
    expect(start.events).toEqual([
      { event: "tool_start", data: { name: "web_search", query: "clima" } },
    ]);
    const end = pushSseChunk(
      "",
      'event: tool_end\ndata: {"sources":[{"title":"Ex","url":"https://exemplo.test"}]}\n\n',
    );
    expect(end.events[0]?.event).toBe("tool_end");
  });
});
