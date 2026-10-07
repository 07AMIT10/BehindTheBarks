import { describe, expect, it } from "vitest";
import { backendBase } from "./config";

describe("backendBase", () => {
  it("defaults to page host on port 8000", () => {
    expect(backendBase({ search: "", env: "", protocol: "http:", hostname: "laptop.local" })).toEqual({
      http: "http://laptop.local:8000", ws: "ws://laptop.local:8000",
    });
  });
  it("env var wins over default, https maps to wss", () => {
    expect(backendBase({ search: "", env: "https://abc.trycloudflare.com/", protocol: "http:", hostname: "x" })).toEqual({
      http: "https://abc.trycloudflare.com", ws: "wss://abc.trycloudflare.com",
    });
  });
  it("?backend= wins over everything and accepts ws urls", () => {
    expect(backendBase({ search: "?backend=wss%3A%2F%2Fb.example", env: "http://e", protocol: "http:", hostname: "x" })).toEqual({
      http: "https://b.example", ws: "wss://b.example",
    });
  });
  it("https protocol uses standard origin without port 8000", () => {
    expect(backendBase({ search: "", env: "", protocol: "https:", hostname: "behind-the-barks.onrender.com" })).toEqual({
      http: "https://behind-the-barks.onrender.com", ws: "wss://behind-the-barks.onrender.com",
    });
  });
});
