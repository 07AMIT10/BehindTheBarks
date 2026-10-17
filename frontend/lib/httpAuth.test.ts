import { describe, expect, it } from "vitest";
import { authedHeaders, authedUrl, revokeStationRequest, revokeViewerRequest } from "./httpAuth";

describe("authedUrl", () => {
  it("appends the token as a query parameter", () => {
    expect(authedUrl("http://be", "/diary/daily", "tok")).toBe("http://be/diary/daily?token=tok");
  });

  it("joins with & when the path already has a query", () => {
    expect(authedUrl("http://be", "/diary/daily?station=living", "tok two")).toBe(
      "http://be/diary/daily?station=living&token=tok%20two"
    );
  });

  it("leaves the URL alone for cookie-only (households) sessions", () => {
    expect(authedUrl("http://be", "/snippets/s1", null)).toBe("http://be/snippets/s1");
  });
});

describe("authedHeaders", () => {
  it("carries the Bearer token", () => {
    expect(authedHeaders("tok")).toEqual({ Authorization: "Bearer tok" });
  });

  it("omits Authorization without a token but keeps extras", () => {
    expect(authedHeaders(null, { "content-type": "application/json" })).toEqual({
      "content-type": "application/json",
    });
  });
});

describe("revoke requests", () => {
  it("revokeStation sends the same auth shape as revokeViewer", () => {
    const viewer = revokeViewerRequest("http://be", "v-1", "tok");
    const station = revokeStationRequest("http://be", "st-9", "tok");
    expect(station.init.method).toBe("DELETE");
    expect(station.init.credentials).toBe("include");
    expect(station.init.headers).toEqual(viewer.init.headers);
    expect(station.init.headers).toEqual({ Authorization: "Bearer tok" });
    expect(station.url).toBe("http://be/stations/st-9?token=tok");
    expect(viewer.url).toBe("http://be/viewers/v-1?token=tok");
  });

  it("builds cookie-only requests without a token", () => {
    const station = revokeStationRequest("http://be", "st-9", null);
    expect(station.init.headers).toEqual({});
    expect(station.url).toBe("http://be/stations/st-9");
  });
});
